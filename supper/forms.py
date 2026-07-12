from django import forms

from .models import RATING_DIMENSIONS, Rating

# 1-5 star choices shared by all four dimensions. Labels are read out by
# screen readers; sighted users see a row of ★ buttons (see rate.html).
STAR_CHOICES = [(i, f"{i} star" if i == 1 else f"{i} stars") for i in range(1, 6)]

_LABELS = dict(RATING_DIMENSIONS)


def _star_field(label):
    """A 1-5 radio group rendered as a CSS-only row of star buttons."""
    return forms.TypedChoiceField(
        label=label,
        coerce=int,
        choices=STAR_CHOICES,
        widget=forms.RadioSelect,
        error_messages={"required": "Pick a star rating."},
    )


class RatingForm(forms.ModelForm):
    """One member's scores for one Supper Club visit.

    The view attaches ``event`` and ``user``; passing the member's existing
    Rating as ``instance`` turns a resubmission into an update rather than a
    duplicate (the model enforces one rating per event+user).
    """

    food = _star_field(_LABELS["food"])
    service = _star_field(_LABELS["service"])
    atmosphere = _star_field(_LABELS["atmosphere"])
    value = _star_field(_LABELS["value"])

    class Meta:
        model = Rating
        fields = ["food", "service", "atmosphere", "value", "comment"]
        widgets = {
            "comment": forms.Textarea(attrs={
                "rows": 3,
                "maxlength": 300,
                "placeholder": "What should the next table know? (optional)",
            }),
        }

    def star_fields(self):
        """Bound fields for the four dimensions, in scoring order."""
        return [self[key] for key, _ in RATING_DIMENSIONS]
