"""Forms for the public site: the editors' view of an admin-managed page."""

from django import forms
from django.urls import reverse_lazy

from .models import SitePage
from .richtext import richtext_problem


class SitePageContentForm(forms.ModelForm):
    """What a page's named editors may change: the title and the body.

    Everything else (publishing, audience, navigation, editors) stays on the
    admin panel's form, so an editor can never widen a page's audience or
    take it off the site.
    """

    class Meta:
        model = SitePage
        fields = ["title", "content"]
        help_texts = {
            "content": "Markdown and basic HTML supported: headings, links, "
                       "lists, images, tables.",
        }
        widgets = {
            "content": forms.Textarea(attrs={"rows": 18, "data-editor": reverse_lazy("core:preview")}),
        }

    def clean_content(self):
        content = self.cleaned_data["content"]
        problem = richtext_problem(content)
        if problem:
            raise forms.ValidationError(f"Not saved: {problem}.")
        return content
