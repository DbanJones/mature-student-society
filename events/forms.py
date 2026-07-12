from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from supper.models import Restaurant

from .models import Category, Event

DATETIME_LOCAL = "%Y-%m-%dT%H:%M"


class EventForm(forms.ModelForm):
    """Shared create/edit form.

    Permission-sensitive behaviour is enforced server-side here:
    - ``is_official`` is admins-only: the field is removed for everyone else,
      so a forged POST value is ignored by the ModelForm. Official events are
      prioritised on the calendar and in the mailer.
    - ``allow_past`` (the "this event already happened" override) only exists
      on create; edits never re-validate the start against the clock.
    """

    new_restaurant = forms.CharField(
        required=False, max_length=120, label="…or add a new restaurant",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Noodles Plus, Mill Road"}),
        help_text="Used if it isn't in the list above.",
    )
    allow_past = forms.BooleanField(
        required=False, label="This event already happened",
        help_text="Tick to add a past event (e.g. back-filling a Supper Club visit).",
    )

    class Meta:
        model = Event
        fields = [
            "title", "category", "description", "image", "location",
            "start", "end", "host", "capacity", "members_only", "is_official",
            "group_chat_link", "attendee_info", "restaurant",
        ]
        labels = {
            "category": "Tag",
            "image": "Event picture (optional)",
            "host": "Who's running it",
            "members_only": "Hide from the public calendar",
            "is_official": "Official society event",
            "group_chat_link": "WhatsApp group link (optional)",
            "attendee_info": "Details for attendees (optional)",
            "restaurant": "Restaurant",
        }
        help_texts = {
            "category": "e.g. Supper Club, History Club, Coffee Club.",
            "description": "Markdown supported — links, lists, **bold**, *italics*.",
            "image": f"JPEG/PNG, up to {settings.MAX_UPLOAD_SIZE_MB} MB.",
            "host": "Defaults to you.",
            "end": "Optional.",
            "members_only": "Only logged-in members will see it.",
            "group_chat_link": "Shown only to people who have RSVP'd.",
            "attendee_info": "Meeting point, what to bring… shown only after RSVP.",
        }
        widgets = {
            "start": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format=DATETIME_LOCAL
            ),
            "end": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format=DATETIME_LOCAL
            ),
            "capacity": forms.NumberInput(attrs={"min": 1}),
            "description": forms.Textarea(attrs={"rows": 8}),
            "attendee_info": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, user=None, is_create=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.is_create = is_create
        self.fields["category"].queryset = Category.objects.all()
        self.fields["restaurant"].queryset = Restaurant.objects.order_by("name")
        self.fields["restaurant"].required = False
        self.fields["host"].queryset = (
            get_user_model().objects.filter(is_banned=False)
            .order_by("first_name", "last_name")
        )
        self.fields["host"].required = False
        if is_create and user is not None:
            self.fields["host"].initial = user.pk

        if user is None or not user.is_portal_admin:
            del self.fields["is_official"]
        else:
            self.fields["is_official"].help_text = (
                "Admins only. Official events lead the calendar day, the "
                "'coming up' list and the What's On mailer."
            )
        if not is_create:
            del self.fields["allow_past"]

    def clean_capacity(self):
        capacity = self.cleaned_data.get("capacity")
        if capacity is not None and capacity < 1:
            raise forms.ValidationError("Capacity must be at least 1 (or blank for unlimited).")
        return capacity

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image and hasattr(image, "content_type"):
            if not image.content_type.startswith("image/"):
                raise forms.ValidationError("Please upload an image file.")
            max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
            if image.size > max_bytes:
                raise forms.ValidationError(
                    f"Pictures must be {settings.MAX_UPLOAD_SIZE_MB} MB or smaller."
                )
        return image

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end <= start:
            self.add_error("end", "The end must be after the start.")
        if self.is_create and start and start < timezone.now() and not cleaned.get("allow_past"):
            self.add_error(
                "start",
                "That start time is in the past — tick “this event already "
                "happened” below if that's deliberate.",
            )
        category = cleaned.get("category")
        if category and not category.has_restaurant_ratings:
            cleaned["restaurant"] = None
            cleaned["new_restaurant"] = ""
        return cleaned

    def save(self, commit=True):
        event = super().save(commit=False)
        category = self.cleaned_data.get("category")
        if category and category.has_restaurant_ratings:
            name = (self.cleaned_data.get("new_restaurant") or "").strip()
            if name:
                existing = Restaurant.objects.filter(name__iexact=name).first()
                event.restaurant = existing or Restaurant.objects.create(
                    name=name, added_by=self.user
                )
        else:
            event.restaurant = None
        if commit:
            event.save()
        return event


class TagPageForm(forms.ModelForm):
    """What a tag's owners may edit: the blurb and the page itself.

    Name, colour, emoji and ownership are managed by the super admin.
    """

    class Meta:
        model = Category
        fields = ["description", "page_content"]
        labels = {
            "description": "Short blurb",
            "page_content": "Page content",
        }
        help_texts = {
            "description": "One line, shown under the tag name and on hover.",
            "page_content": "Markdown and basic HTML supported (headings, "
                            "links, lists, images, tables).",
        }
        widgets = {
            "description": forms.TextInput(),
            "page_content": forms.Textarea(attrs={"rows": 14}),
        }
