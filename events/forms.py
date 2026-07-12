from django import forms
from django.utils import timezone

from supper.models import Restaurant

from .models import Category, Event

DATETIME_LOCAL = "%Y-%m-%dT%H:%M"


class EventForm(forms.ModelForm):
    """Shared create/edit form.

    Permission-sensitive behaviour is enforced server-side here:
    - ``is_official`` is only present for portal admins (removed for everyone
      else, so a forged POST value is ignored by the ModelForm).
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
            "title", "category", "description", "location",
            "start", "end", "capacity", "members_only", "is_official",
            "restaurant",
        ]
        labels = {
            "members_only": "Hide from the public calendar",
            "is_official": "Official society event",
            "restaurant": "Restaurant",
        }
        help_texts = {
            "description": "Markdown supported — links, lists, **bold**, *italics*.",
            "end": "Optional.",
            "members_only": "Only logged-in members will see it.",
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
        }

    def __init__(self, *args, user=None, is_create=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.is_create = is_create
        self.fields["category"].queryset = Category.objects.all()
        self.fields["restaurant"].queryset = Restaurant.objects.order_by("name")
        self.fields["restaurant"].required = False
        if user is None or not user.is_portal_admin:
            del self.fields["is_official"]
        if not is_create:
            del self.fields["allow_past"]

    def clean_capacity(self):
        capacity = self.cleaned_data.get("capacity")
        if capacity is not None and capacity < 1:
            raise forms.ValidationError("Capacity must be at least 1 (or blank for unlimited).")
        return capacity

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
