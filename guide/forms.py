"""Forms for the Mature Students Guide wiki."""

from django import forms

from .models import GuidePage


class GuidePageForm(forms.ModelForm):
    """Create or edit a guide page.

    The slug is deliberately absent: it is generated from the title once, on
    creation (see views._unique_slug), and is immutable afterwards so links
    to guide pages never break.
    """

    class Meta:
        model = GuidePage
        fields = ["title", "section", "content"]
        help_texts = {
            "content": "Markdown and basic HTML supported — headings, links, "
                       "lists, images, tables.",
        }
        widgets = {
            "title": forms.TextInput(
                attrs={"placeholder": "e.g. Choosing a mature college"}
            ),
            "content": forms.Textarea(
                attrs={
                    "rows": 18,
                    "placeholder": "Write in Markdown — the cheatsheet alongside "
                                   "covers everything you need.",
                }
            ),
        }
