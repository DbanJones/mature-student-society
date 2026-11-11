import datetime

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse_lazy
from django.utils import timezone

from core.richtext import richtext_problem
from supper.models import Restaurant

from . import lunch
from .models import Category, Event

DATETIME_LOCAL = "%Y-%m-%dT%H:%M"


class EventForm(forms.ModelForm):
    """Shared create/edit form.

    Permission-sensitive behaviour is enforced server-side here:
    - ``is_super`` exists only for super admins; the field is removed for
      everyone else, so a forged POST value is ignored by the ModelForm.
    - ``is_official`` (a tagged event) exists for society admins and for tag
      owners; owners are additionally validated in clean() so they can only
      promote events carrying a tag they own.
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
            "start", "end", "host", "capacity", "members_only", "show_attendees", "is_official",
            "is_super", "group_chat_link", "attendee_info", "restaurant",
        ]
        labels = {
            "category": "Tag",
            "image": "Event picture (optional)",
            "host": "Who's running it",
            "members_only": "Hide from the public calendar",
            "show_attendees": "Show who's going",
            "is_official": "Tagged event (official for its tag)",
            "is_super": "Super event",
            "group_chat_link": "WhatsApp group link (optional)",
            "attendee_info": "Details for attendees (optional)",
            "restaurant": "Restaurant",
        }
        help_texts = {
            "category": "e.g. Supper Club, History Club, Coffee Club.",
            "description": "Markdown supported — links, lists, **bold**, *italics*.",
            "image": f"JPEG/PNG, up to {settings.MAX_UPLOAD_SIZE_MB} MB.",
            "host": "Defaults to you.",
            "end": "Two hours after the start unless you change it. Clear it later to make the event open-ended.",
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
        self.fields["category"].queryset = Category.objects.exclude(slug="formal-exchange")
        self.fields["restaurant"].queryset = Restaurant.objects.order_by("name")
        self.fields["restaurant"].required = False
        self.fields["host"].queryset = (
            get_user_model().objects.filter(is_banned=False)
            .order_by("first_name", "last_name")
        )
        self.fields["host"].required = False
        if is_create and user is not None:
            self.fields["host"].initial = user.pk

        is_admin = user is not None and user.is_portal_admin
        owns_tags = (
            user is not None
            and user.is_authenticated
            and user.tags_owned.exists()
        )
        if not (is_admin or owns_tags):
            del self.fields["is_official"]
        else:
            self.fields["is_official"].help_text = (
                "Promotes this event to its tag's official listing — it "
                "ranks above ordinary member events. Admins may promote any "
                "event; tag owners only events carrying their tag."
            )
        if user is None or not user.is_super_admin:
            del self.fields["is_super"]
        else:
            self.fields["is_super"].help_text = (
                "Super admins only. Super events lead every listing, appear "
                "larger on the calendar, and are pinned to every member's "
                "dashboard."
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
            from core.images import EVENT_MAX_PX, shrink_image
            image = shrink_image(image, EVENT_MAX_PX)
        return image

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if self.is_create and start and not end:
            import datetime

            cleaned["end"] = end = start + datetime.timedelta(hours=2)  # the usual length of an evening
        if start and end and end <= start:
            self.add_error("end", "The end must be after the start.")
        if self.is_create and start and start < timezone.now() and not cleaned.get("allow_past"):
            self.add_error(
                "start",
                "That start time is in the past — tick “this event already "
                "happened” below if that's deliberate.",
            )
        category = cleaned.get("category")
        already_official_here = bool(
            self.instance.pk
            and self.instance.is_official
            and category == self.instance.category
        )
        if (
            cleaned.get("is_official")
            and category is not None
            and not already_official_here
            and self.user is not None
            and not self.user.is_portal_admin
            and not category.is_owned_by(self.user)
        ):
            self.add_error(
                "is_official",
                f"You can only promote events carrying a tag you own — "
                f"you don't own “{category.name}”.",
            )
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


class LunchSwapForm(forms.Form):
    """The short "Host a lunch swap" form. It is turned into an ordinary
    event by events.lunch.create_lunch; after that the full form edits it."""

    FURTHEST = datetime.timedelta(days=366)  # no lunch is planned more than a year ahead

    college = forms.ChoiceField(
        choices=[("", "Choose a college")] + lunch.HOST_COLLEGES, label="College",
        help_text="Where you can bring guests in to eat.",
        error_messages={"required": "Choose the college where you will host."},
    )
    date = forms.DateField(
        label="Date", input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        error_messages={"required": "Pick a date."},
    )
    time = forms.ChoiceField(
        choices=[(t, t) for t in lunch.TIMES], initial=lunch.TIME_DEFAULT, label="Time",
    )
    places = forms.TypedChoiceField(
        coerce=int, choices=[(n, str(n)) for n in range(1, lunch.PLACES_MAX + 1)],
        initial=lunch.PLACES_DEFAULT, label="How many guests can you take?",
        help_text="Not counting you.",
    )
    meet = forms.CharField(
        max_length=80, initial="Porters' Lodge", label="Where to meet",
        help_text="Somewhere guests can reach without a college card.",
        error_messages={"required": "Say where guests should meet you."},
    )
    paying = forms.ChoiceField(
        choices=lunch.PAYING, widget=forms.RadioSelect, initial="own", label="Who pays?",
    )
    note = forms.CharField(
        required=False, max_length=600, widget=forms.Textarea(attrs={"rows": 3}),
        label="Anything guests should know?",
        help_text="For example the rough cost, card only, or when you need to be back.",
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        # Set per request: a class-level value would freeze on the day the server started.
        today = timezone.localdate()
        self.fields["date"].widget.attrs["min"] = today.isoformat()
        self.fields["date"].widget.attrs["max"] = (today + self.FURTHEST).isoformat()
        if user.college in dict(lunch.HOST_COLLEGES):
            self.fields["college"].initial = user.college

    def clean(self):
        cleaned = super().clean()
        day, at = cleaned.get("date"), cleaned.get("time")
        if day and at:
            start = timezone.make_aware(
                datetime.datetime.combine(day, datetime.time.fromisoformat(at))
            )
            if start <= timezone.now():
                self.add_error("date", "That time has already passed. Pick a later date or time.")
            elif day > timezone.localdate() + self.FURTHEST:
                # A slip of the year (2072 for 2027) would post a lunch nobody finds.
                self.add_error("date", "That is more than a year away. Check the year.")
            else:
                cleaned["start"] = start
        return cleaned


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
            "page_content": forms.Textarea(attrs={"rows": 14, "data-editor": reverse_lazy("core:preview")}),
        }

    def clean_page_content(self):
        content = self.cleaned_data["page_content"]
        problem = richtext_problem(content)
        if problem:
            raise forms.ValidationError(f"Not saved: {problem}.")
        return content
