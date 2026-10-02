from django import forms

from .models import MAX_LENGTH, Testimonial


class TestimonialForm(forms.ModelForm):
    class Meta:
        model = Testimonial
        fields = ["body", "is_anonymous"]
        labels = {
            "body": "Your testimonial",
            "is_anonymous": "Post anonymously",
        }
        help_texts = {
            "body": f"A few sentences, up to {MAX_LENGTH} characters. Plain "
                    "text: what MSS has meant to you, or a moment you'd "
                    "recommend to someone thinking of joining.",
            "is_anonymous": "Shown as “An MSS member”. The committee can still "
                            "see it's from you.",
        }
        error_messages = {
            "body": {"required": "Write a few words first."},
        }
        widgets = {
            "body": forms.Textarea(attrs={
                "rows": 5, "maxlength": MAX_LENGTH,
                "placeholder": "I joined MSS in my first term and…",
            }),
        }

    def clean_body(self):
        body = (self.cleaned_data.get("body") or "").strip()
        if not body:
            raise forms.ValidationError("Write a few words first.")
        return body
