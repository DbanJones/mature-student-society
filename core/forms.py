"""Forms for the public site: the editors' view of an admin-managed page."""

from django import forms

from .models import SitePage


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
            "content": forms.Textarea(attrs={"rows": 18}),
        }
