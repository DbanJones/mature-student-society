"""Forms for the society admin panel."""

from django import forms
from django.utils.text import slugify

from accounts.models import User
from core.models import BUILTIN_TABS, VISIBILITY_CHOICES, SitePage
from events.models import Category
from faq.models import ContactNode


class MemberEditForm(forms.ModelForm):
    """Small fix-up form for correcting a member's details on request."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "college", "course", "mobile"]


class MailerForm(forms.Form):
    """The editable What's On draft: recipient, subject and plain-text body."""

    recipient = forms.EmailField(
        label="To",
        help_text="The SRCF mailing list address — delivers to every newsletter subscriber.",
    )
    subject = forms.CharField(max_length=200)
    body = forms.CharField(
        strip=False,
        widget=forms.Textarea(
            attrs={"class": "mailer-body", "rows": 24, "spellcheck": "false"}
        ),
    )


class TagAdminForm(forms.ModelForm):
    """Super admin management of a tag: identity, ordering and owners.

    Owners are the members who run that club — they can mark their events
    official and edit the tag's public page.
    """

    class Meta:
        model = Category
        fields = [
            "name", "emoji", "color", "description", "owners",
            "has_restaurant_ratings", "sort_order",
        ]
        labels = {
            "color": "Colour",
            "owners": "Tag owners",
            "has_restaurant_ratings": "Supper Club-style restaurant ratings",
        }
        widgets = {
            "color": forms.TextInput(attrs={"type": "color"}),
            "owners": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owners"].queryset = User.objects.filter(
            is_banned=False
        ).order_by("first_name", "last_name")
        self.fields["owners"].required = False

    def save(self, commit=True):
        tag = super().save(commit=False)
        if not tag.slug:
            base = slugify(tag.name)[:50] or "tag"
            slug, n = base, 2
            while Category.objects.filter(slug=slug).exclude(pk=tag.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            tag.slug = slug
        if commit:
            tag.save()
            self.save_m2m()
        return tag


class EmailSettingsForm(forms.Form):
    """Super admin: the AI drafting engine, its API key and the society's
    tone-of-voice brief for anyone (or anything) drafting emails."""

    email_ai_engine = forms.ChoiceField(
        label="AI drafting engine",
        choices=[
            ("deepseek", "DeepSeek (default)"),
            ("anthropic", "Anthropic (Claude)"),
            ("openai", "OpenAI (GPT)"),
            ("gemini", "Google (Gemini)"),
            ("mistral", "Mistral"),
        ],
        initial="deepseek",
        help_text="Which engine drafts society emails, using the API key below.",
    )
    email_api_key = forms.CharField(
        required=False, label="API key",
        widget=forms.PasswordInput(render_value=False,
                                   attrs={"autocomplete": "new-password"}),
        help_text="Leave blank to keep the current key.",
    )
    email_tone = forms.CharField(
        required=False, label="Tone of voice for emails",
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text="Guidance shown next to the mailer, e.g. “Warm, plain "
                  "English, no exclamation marks, sign off as ‘The MSS "
                  "committee’.”",
    )


class WhatsAppSettingsForm(forms.Form):
    """Admins: rotate the group invite link (SiteConfig)."""

    whatsapp_group_link = forms.URLField(
        required=False, label="Group invite link",
        help_text="The chat.whatsapp.com invite revealed once to each member. "
                  "If WhatsApp made you reset it, paste the new one here.",
    )


class SitePageForm(forms.ModelForm):
    """Admin-managed content pages (everything that isn't the calendar or
    the Guide)."""

    class Meta:
        model = SitePage
        fields = [
            "title", "content", "is_published",
            "nav_label", "nav_visibility", "sort_order",
        ]
        help_texts = {
            "sort_order": "Lower numbers appear first in the navigation.",
        }
        widgets = {
            "content": forms.Textarea(attrs={"rows": 16}),
        }

    def save(self, commit=True):
        page = super().save(commit=False)
        if not page.slug:
            base = slugify(page.title)[:130] or "page"
            slug, n = base, 2
            while SitePage.objects.filter(slug=slug).exclude(pk=page.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            page.slug = slug
        if commit:
            page.save()
        return page


class TabVisibilityForm(forms.Form):
    """One select per built-in tab; stored on SiteConfig.tab_visibility."""

    def __init__(self, *args, config=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tabs = BUILTIN_TABS
        for key, label, _default in BUILTIN_TABS:
            self.fields[f"tab_{key}"] = forms.ChoiceField(
                label=label, choices=VISIBILITY_CHOICES,
                initial=config.tab_visibility_for(key) if config else "public",
            )

    def apply(self, config):
        config.tab_visibility = {
            key: self.cleaned_data[f"tab_{key}"]
            for key, _label, _default in BUILTIN_TABS
        }
        config.save()


class ContactNodeForm(forms.ModelForm):
    """One node of the who-to-contact map."""

    class Meta:
        model = ContactNode
        fields = [
            "kind", "option_label", "sort_order",
            "question",
            "who", "action", "escalate", "keep", "email_template",
        ]
        widgets = {
            "action": forms.Textarea(attrs={"rows": 3}),
            "escalate": forms.Textarea(attrs={"rows": 2}),
            "keep": forms.Textarea(attrs={"rows": 2}),
            "email_template": forms.Textarea(attrs={"rows": 8}),
        }

    def __init__(self, *args, is_root=False, **kwargs):
        super().__init__(*args, **kwargs)
        if is_root:
            # The root is always a question and has no branch label.
            self.fields["kind"].disabled = True
            self.fields["option_label"].widget = forms.HiddenInput()
            self.fields["option_label"].required = False
        else:
            self.fields["option_label"].required = True

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        if kind == ContactNode.Kind.QUESTION and not cleaned.get("question"):
            self.add_error("question", "A question node needs its question.")
        if kind == ContactNode.Kind.RESULT:
            for field in ("who", "action"):
                if not cleaned.get(field):
                    self.add_error(field, "A result node needs this.")
        return cleaned
