"""The poll form: the question, when it closes, and up to MAX_OPTIONS
options whose shape depends on the kind (labels, proposed dates, or slots
with a capacity)."""

import datetime

from django import forms
from django.utils import timezone
from django.utils.formats import date_format

from .models import MAX_OPTIONS, Poll, PollOption

DATETIME_LOCAL = "%Y-%m-%dT%H:%M"


class PollForm(forms.ModelForm):
    class Meta:
        model = Poll
        fields = ["question", "description", "closes_at", "allow_multiple", "show_results"]
        labels = {"closes_at": "Voting closes", "show_results": "Show results"}
        widgets = {
            "closes_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format=DATETIME_LOCAL
            ),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, kind, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind, self.event = kind, event
        self.options = []
        if self.kind == Poll.Kind.VOLUNTEERS:
            self.fields["allow_multiple"].initial = True
            self.fields["allow_multiple"].label = "People may take more than one slot"
        if not self.initial.get("closes_at"):
            default = timezone.now() + datetime.timedelta(days=3)
            if event is not None and event.start > timezone.now():
                default = min(default, event.start - datetime.timedelta(days=1))
            self.fields["closes_at"].initial = timezone.localtime(default).replace(
                second=0, microsecond=0
            )
        for i in range(MAX_OPTIONS):
            self.fields[f"opt_label_{i}"] = forms.CharField(
                required=False, max_length=140,
                widget=forms.TextInput(attrs={"placeholder": self._placeholder(i)}),
            )
            if self.kind == Poll.Kind.DATE:
                self.fields[f"opt_start_{i}"] = forms.DateTimeField(
                    required=False,
                    widget=forms.DateTimeInput(
                        attrs={"type": "datetime-local"}, format=DATETIME_LOCAL
                    ),
                )
            if self.kind == Poll.Kind.VOLUNTEERS:
                self.fields[f"opt_capacity_{i}"] = forms.IntegerField(
                    required=False, min_value=1,
                    widget=forms.NumberInput(attrs={"placeholder": "people"}),
                )
        if self.kind == Poll.Kind.DATE:
            self.fields["add_opt_out"] = forms.BooleanField(
                required=False, initial=True,
                label="Add an “I can't make any of these” option",
            )

    def _placeholder(self, i):
        return {
            Poll.Kind.VENUE: ["The Granta, Newnham Road", "The Free Press, Prospect Row"],
            Poll.Kind.DATE: ["Optional label, e.g. “after the lecture”"] * 2,
            Poll.Kind.VOLUNTEERS: ["Stall, 10:00 to 12:00", "Stall, 12:00 to 14:00"],
            Poll.Kind.GENERAL: ["Option", "Another option"],
        }[self.kind][min(i, 1)]

    def option_rows(self):
        """Bound fields per row, for the template."""
        for i in range(MAX_OPTIONS):
            yield {
                "label": self[f"opt_label_{i}"],
                "start": self[f"opt_start_{i}"] if self.kind == Poll.Kind.DATE else None,
                "capacity": (
                    self[f"opt_capacity_{i}"] if self.kind == Poll.Kind.VOLUNTEERS else None
                ),
            }

    def clean_closes_at(self):
        closes_at = self.cleaned_data["closes_at"]
        if closes_at <= timezone.now():
            raise forms.ValidationError("Pick a closing time in the future.")
        return closes_at

    def clean(self):
        cleaned = super().clean()
        options = []
        for i in range(MAX_OPTIONS):
            label = (cleaned.get(f"opt_label_{i}") or "").strip()
            start = cleaned.get(f"opt_start_{i}")
            capacity = cleaned.get(f"opt_capacity_{i}")
            if self.kind == Poll.Kind.DATE:
                if start is None:
                    continue
                label = label or date_format(timezone.localtime(start), "D j M, H:i")
            elif not label:
                continue
            options.append({"label": label, "start": start, "capacity": capacity})
        minimum = 1 if self.kind == Poll.Kind.VOLUNTEERS else 2
        if len(options) < minimum:
            raise forms.ValidationError(
                f"Give people at least {minimum} option{'s' if minimum > 1 else ''}."
            )
        self.options = options
        return cleaned

    def save(self, commit=True):
        poll = super().save(commit=False)
        poll.kind = self.kind
        poll.event = self.event
        poll.save()
        for order, data in enumerate(self.options):
            PollOption.objects.create(poll=poll, sort_order=order, **data)
        if self.kind == Poll.Kind.DATE and self.cleaned_data.get("add_opt_out"):
            PollOption.objects.create(
                poll=poll, label="I can't make any of these",
                is_opt_out=True, sort_order=len(self.options),
            )
        return poll
