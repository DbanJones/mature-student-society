from django import forms

from .models import EventPoster


class PosterSettingsForm(forms.ModelForm):
    """Every poster choice. Used both to save an organiser's settings and to
    validate a member's one-off overrides from the query string."""

    class Meta:
        model = EventPoster
        fields = [
            "template", "size", "accent", "use_event_picture", "focal_x", "focal_y",
            "headline", "extra_line", "show_map", "show_qr", "show_description",
            "show_host", "print_marks",
        ]
        widgets = {
            "template": forms.RadioSelect, "size": forms.RadioSelect, "accent": forms.RadioSelect,
            "focal_x": forms.HiddenInput, "focal_y": forms.HiddenInput,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("use_event_picture", "show_map", "show_qr", "show_description",
                     "show_host", "print_marks", "focal_x", "focal_y"):
            self.fields[name].required = False

    def clean_focal_x(self):
        value = self.cleaned_data.get("focal_x")
        return min(1.0, max(0.0, 0.5 if value is None else value))

    def clean_focal_y(self):
        value = self.cleaned_data.get("focal_y")
        return min(1.0, max(0.0, 0.5 if value is None else value))
