"""Forms for the society admin panel."""

from django import forms
from django.conf import settings
from django.urls import reverse_lazy
from django.utils.text import slugify

from accounts.forms import clean_mobile_number
from accounts.models import User
from core.models import (
    BUILTIN_TABS,
    VISIBILITY_CHOICES,
    Activity,
    CommitteeMember,
    Picture,
    SiteConfig,
    SitePage,
    TermsVersion,
)
from events.models import Category
from faq.models import ContactNode, DepartmentContact


class MemberEditForm(forms.ModelForm):
    """Small fix-up form for correcting a member's details on request.

    College and mobile stay optional here — an admin sometimes needs to clear
    a wrong value — but the view warns when either is left blank, because
    that is exactly what makes a member show as incomplete.
    """

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "college", "course", "mobile"]

    def clean_mobile(self):
        raw = self.cleaned_data.get("mobile", "")
        if not raw.strip():
            return ""
        return clean_mobile_number(raw)


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
    instructions = forms.CharField(
        required=False, label="Instructions for the AI", max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional, for “Draft with AI”: how to shape this issue, e.g. “short and punchy, "
                  "lead with the Winter Ball, warm sign-off from the committee”. The facts and "
                  "links stay as they are whatever you ask.",
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
        # Every tag needs someone responsible for its events.
        self.fields["owners"].required = True
        self.fields["owners"].error_messages["required"] = (
            "Pick at least one owner — every tag needs a member responsible "
            "for its events."
        )

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


class MessagingSettingsForm(forms.Form):
    """Admins: the site-wide member-to-member messaging mode (SiteConfig)."""

    messaging_mode = forms.ChoiceField(
        label="Member-to-member messaging",
        choices=SiteConfig.MessagingMode.choices,
        widget=forms.RadioSelect,
    )


class MapSettingsForm(forms.Form):
    """Super admin: the Geoapify key behind poster maps and venue look-ups."""

    geoapify_api_key = forms.CharField(
        required=False, label="Geoapify API key",
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Free at geoapify.com (3,000 map and geocoding requests a day). "
                  "Leave blank to keep the current key; type CLEAR to remove it.",
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
    the Guide): the full set of controls, including who else may edit."""

    class Meta:
        model = SitePage
        fields = [
            "title", "content", "is_published",
            "nav_label", "nav_visibility", "section", "sort_order", "editors",
        ]
        labels = {
            "nav_visibility": "Audience",
            "section": "Menu",
            "editors": "Editors",
        }
        help_texts = {
            "sort_order": "Lower numbers appear first in the navigation.",
            "nav_visibility": "Who can read the page and see its navigation "
                              "link. “Hidden” keeps it out of the navigation "
                              "but readable by anyone with the address. "
                              "Admins always see everything.",
            "editors": "Members who may change the title and content "
                       "without being admins. Everything else on this form "
                       "stays with admins.",
        }
        widgets = {
            "content": forms.Textarea(attrs={
                "rows": 16, "data-editor": reverse_lazy("core:preview"),
                "data-pictures": reverse_lazy("panel:pictures"),
            }),
            "editors": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["editors"].queryset = User.objects.filter(
            is_banned=False, is_portal_admin=False
        ).order_by("first_name", "last_name")
        self.fields["section"].required = False  # About unless told otherwise

    def clean_section(self):
        return self.cleaned_data.get("section") or "about"

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
            self.save_m2m()
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


class DepartmentContactForm(forms.ModelForm):
    """A recorded departmental email address."""

    class Meta:
        model = DepartmentContact
        fields = ["school", "department", "email", "notes", "responds_well"]


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


class TermsVersionForm(forms.ModelForm):
    """Create or edit a version of the terms and conditions.

    The version number is assigned automatically on creation and is read-only
    afterwards: acceptances are recorded against the number, so renumbering an
    existing version would quietly rewrite what members agreed to.
    """

    class Meta:
        model = TermsVersion
        fields = ["title", "number", "content", "change_note", "is_published"]
        labels = {
            "number": "Version number",
            "change_note": "What changed",
            "is_published": "Published",
        }
        widgets = {
            "content": forms.Textarea(attrs={"rows": 22}),
            "change_note": forms.TextInput(
                attrs={"placeholder": "e.g. Added photography consent clause"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["number"].disabled = True
            self.fields["number"].help_text = (
                "Fixed once created — members' acceptances are recorded "
                "against this number. Publish a new version to change the "
                "terms people must agree to."
            )
        else:
            self.fields["number"].initial = TermsVersion.next_number()

    def clean_content(self):
        content = (self.cleaned_data.get("content") or "").strip()
        if not content:
            raise forms.ValidationError("The terms can't be empty.")
        return content


class BannerForm(forms.Form):
    """The site-wide announcement banner (SiteConfig)."""

    banner_text = forms.CharField(
        required=False, label="Announcement",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "e.g. Freshers Fair stall: volunteers needed, see the poll."}),
        help_text="Markdown. Blank means no banner. Visitors can dismiss it for their session.",
    )
    banner_until = forms.DateTimeField(
        required=False, label="Show until",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        help_text="Leave blank to show it until you clear the text.",
    )


class TermDatesForm(forms.Form):
    """When each Full Term begins, so the calendar can label term weeks."""

    michaelmas_start = forms.DateField(required=False, label="Michaelmas Full Term begins",
                                       widget=forms.DateInput(attrs={"type": "date"}))
    lent_start = forms.DateField(required=False, label="Lent Full Term begins",
                                 widget=forms.DateInput(attrs={"type": "date"}))
    easter_start = forms.DateField(required=False, label="Easter Full Term begins",
                                   widget=forms.DateInput(attrs={"type": "date"}))


class CommitteeMemberForm(forms.ModelForm):
    class Meta:
        model = CommitteeMember
        fields = ["name", "role", "sort_order", "is_active"]


class ActivityForm(forms.ModelForm):
    class Meta:
        model = Activity
        fields = ["emoji", "name", "blurb", "sort_order"]


class PictureForm(forms.ModelForm):
    """A picture for the pages: checked, shrunk and stored under media/public."""

    class Meta:
        model = Picture
        fields = ["image", "alt", "caption"]
        labels = {"image": "Picture", "alt": "Describe it", "caption": "Caption (optional)"}

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image and hasattr(image, "content_type"):
            if not image.content_type.startswith("image/"):
                raise forms.ValidationError("Please upload an image file.")
            max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
            if image.size > max_bytes:
                raise forms.ValidationError(f"Pictures must be {settings.MAX_UPLOAD_SIZE_MB} MB or smaller.")
            from core.images import EVENT_MAX_PX, shrink_image

            image = shrink_image(image, EVENT_MAX_PX)
        return image
