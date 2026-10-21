"""Member-owned notification preferences for newly published formals."""
from django import forms
from django.utils import timezone

from .models import HOST_COLLEGES


class FormalAlertForm(forms.Form):
    college = forms.ChoiceField(choices=[("", "Any college")] + HOST_COLLEGES, required=False)
    invitation_type = forms.ChoiceField(choices=[("", "Any invitation"), ("open", "No obligation"), ("reciprocal", "Swap")], required=False)
    booking_mode = forms.ChoiceField(choices=[("", "Any booking option"), ("approval", "Host chooses"), ("instant", "Reserve immediately")], required=False)
    after = forms.DateField(required=False, label="From date", widget=forms.DateInput(attrs={"type": "date"}))
    before = forms.DateField(required=False, label="Until date", widget=forms.DateInput(attrs={"type": "date"}))
    min_places = forms.TypedChoiceField(choices=[(1, "1 or more places"), (2, "2 or more places"), (3, "3 places")], coerce=int, empty_value=1, required=False, initial=1, label="Places available")

    def clean(self):
        data = super().clean()
        if data.get("after") and data.get("before") and data["after"] > data["before"]:
            self.add_error("before", "Choose an end date on or after the start date.")
        if data.get("before") and data["before"] < timezone.localdate():
            self.add_error("before", "Choose today or a future date for new formal alerts.")
        return data
