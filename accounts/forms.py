"""Forms for login, profile completion, the associate waitlist and the
WhatsApp re-access request."""

import re

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.utils import timezone

from .models import User, WaitlistRequest, WhatsAppAccessRequest

# After stripping spaces/hyphens/dots/brackets: optional +, then 9-15 digits.
# Accepts 07700 900123, +44 7700 900123, 00447700900123 etc.
MOBILE_RE = re.compile(r"^\+?\d{9,15}$")

CRSID_RE = re.compile(r"^[a-z][a-z0-9]{1,15}$")


def clean_mobile_number(raw):
    """Normalise a UK-friendly mobile number: strip spaces and punctuation,
    convert a 00 prefix to +. Raises ValidationError if it doesn't look like
    a phone number afterwards."""
    cleaned = re.sub(r"[\s\-().]", "", raw or "")
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not MOBILE_RE.match(cleaned):
        raise forms.ValidationError(
            "Enter a mobile number like 07700 900123 or +44 7700 900123."
        )
    return cleaned


class AssociateLoginForm(AuthenticationForm):
    """Email + password login for associate accounts (username IS the email)."""

    error_messages = {
        "invalid_login": "That email and password didn't match. Passwords are "
                         "case-sensitive.",
        "inactive": "This account is currently inactive. Contact the committee "
                    "if you think this is a mistake.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        username = self.fields["username"]
        username.label = "Email"
        username.widget.attrs.update({"autocomplete": "email"})
        username.widget.attrs.pop("autofocus", None)
        self.fields["password"].widget.attrs.update(
            {"autocomplete": "current-password"}
        )


class ProfileForm(forms.ModelForm):
    """Profile completion / editing: the details members need to recognise
    each other. All fields except the photo are required (profile_complete
    checks name, college and mobile)."""

    class Meta:
        model = User
        fields = [
            "first_name", "last_name", "college", "course", "bio",
            "talk_to_me_about", "work", "interests",
            "mobile", "email", "wants_mailer", "photo",
        ]
        labels = {
            "course": "What you're studying",
            "bio": "About me",
            "talk_to_me_about": "Talk to me about…",
            "work": "Work",
            "interests": "Interests",
            "mobile": "Mobile number",
            "photo": "Profile photo (optional)",
        }
        help_texts = {
            "course": "e.g. 'MPhil History of Science' — shown on your profile.",
            "bio": "A few sentences for your member profile (optional).",
            "talk_to_me_about": "The easiest icebreaker on your profile — "
                                "give people an opening (optional).",
            "work": "What you do or did before Cambridge (optional).",
            "interests": "Comma-separated is fine (optional).",
            "mobile": "Only visible to society admins and to the organisers of "
                      "events you RSVP to — never to other members.",
            "photo": f"JPEG/PNG, up to {settings.MAX_UPLOAD_SIZE_MB} MB.",
        }
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name", "college", "mobile", "email"):
            self.fields[name].required = True
        self.fields["mobile"].widget = forms.TextInput(
            attrs={"type": "tel", "autocomplete": "tel"}
        )

    def clean_mobile(self):
        return clean_mobile_number(self.cleaned_data.get("mobile"))

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip()
        others = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise forms.ValidationError(
                "Another member already uses that email address."
            )
        return email

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        # Only vet fresh uploads; an existing FieldFile has no content_type.
        if photo and hasattr(photo, "content_type"):
            if not photo.content_type.startswith("image/"):
                raise forms.ValidationError("Please upload an image file.")
            max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
            if photo.size > max_bytes:
                raise forms.ValidationError(
                    f"Photos must be {settings.MAX_UPLOAD_SIZE_MB} MB or "
                    "smaller — this one is too big."
                )
            from core.images import PROFILE_MAX_PX, shrink_image
            photo = shrink_image(photo, PROFILE_MAX_PX)
        return photo


class WaitlistForm(forms.ModelForm):
    """Public request-an-account form for partners/family without a CRSid.

    ``website`` is a honeypot: hidden from humans by CSS, so anything that
    fills it in is a bot and the view quietly pretends to succeed.

    ``accept_terms`` is only added when terms are actually published; the
    version ticked is stamped onto the request so the reviewing admin can see
    what the applicant agreed to, and when.
    """

    website = forms.CharField(required=False, label="Website")

    class Meta:
        model = WaitlistRequest
        fields = ["first_name", "last_name", "email", "mobile", "connection"]
        labels = {
            "mobile": "Mobile number (optional)",
            "connection": "Your connection to the society",
        }
        help_texts = {
            "mobile": "Optional — used for WhatsApp group adds if you'd like "
                      "to join the Open Forum.",
            "connection": "e.g. “Partner of Jane Smith (Wolfson)”.",
        }
        widgets = {
            "connection": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import TermsVersion

        self.terms = TermsVersion.current()
        if self.terms is not None:
            self.fields["accept_terms"] = forms.BooleanField(
                required=True,
                label=f"I have read and agree to the {self.terms.title}",
                error_messages={
                    "required": "Please read and accept the terms and "
                                "conditions to request an account.",
                },
            )

    def clean_mobile(self):
        raw = self.cleaned_data.get("mobile", "")
        if not raw.strip():
            return ""
        return clean_mobile_number(raw)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        # Associates are, by definition, people WITHOUT a CRSid. A cam.ac.uk
        # address means they should log in with Raven instead — and it avoids
        # an associate account colliding with a future Raven account whose
        # email defaults to <crsid>@cam.ac.uk.
        if email.endswith("@cam.ac.uk"):
            raise forms.ValidationError(
                "That's a Cambridge address — you can log in directly with "
                "Raven, no waitlist needed. Head to the members' login."
            )
        return email

    def save(self, commit=True):
        """Record which version of the terms was ticked, and when."""
        instance = super().save(commit=False)
        if self.terms is not None and self.cleaned_data.get("accept_terms"):
            instance.terms_version = self.terms.number
            instance.terms_accepted_at = timezone.now()
        if commit:
            instance.save()
        return instance

    def validate_unique(self):
        """Skip the unique check on ``email``: the view greets repeat
        applicants with a friendly message instead of a form error."""
        exclude = self._get_validation_exclusions()
        exclude.add("email")
        try:
            self.instance.validate_unique(exclude=exclude)
        except forms.ValidationError as e:
            self._update_errors(e)


class WhatsAppRequestForm(forms.ModelForm):
    """Ask an admin to re-admit you after the one-time link has been used."""

    class Meta:
        model = WhatsAppAccessRequest
        fields = ["message"]
        labels = {"message": "Message for the admins (optional)"}
        widgets = {
            "message": forms.Textarea(
                attrs={"rows": 3,
                       "placeholder": "e.g. I got a new phone and lost the group."}
            ),
        }
