"""Date and college filters for the private committee report."""

from datetime import date

from django import forms
from django.utils import timezone

from .models import HOST_COLLEGES


class StatisticsFilterForm(forms.Form):
    date_from = forms.DateField(
        required=False, label="From", widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False, label="To", widget=forms.DateInput(attrs={"type": "date"}),
    )
    college = forms.ChoiceField(
        required=False, choices=[("", "All colleges"), *HOST_COLLEGES], label="Host college",
    )
    ledger = forms.ChoiceField(
        required=False, label="Guest list", choices=[
            ("attended", "Confirmed visits"),
            ("pending", "Awaiting attendance confirmation"),
            ("all", "All requests and reservations"),
        ],
    )

    def clean_ledger(self):
        return self.cleaned_data.get("ledger") or "attended"

    def clean(self):
        data = super().clean()
        start, end = data.get("date_from"), data.get("date_to")
        if start and end and start > end:
            self.add_error("date_to", "Choose an end date on or after the start date.")
        if end == date.max:
            self.add_error("date_to", "Choose an end date before 31 December 9999.")
        return data


def statistics_filter(query):
    """Keep defaults stable through pagination; explicit blank dates mean all time."""
    if any(name in query for name in ("date_from", "date_to", "college")):
        return StatisticsFilterForm(query)
    today = timezone.localdate()
    year = today.year if today.month >= 10 else today.year - 1
    return StatisticsFilterForm({
        "date_from": date(year, 10, 1).isoformat(),
        "date_to": date(year + 1, 9, 30).isoformat(),
        "college": "",
        "ledger": query.get("ledger", "attended"),
    })
