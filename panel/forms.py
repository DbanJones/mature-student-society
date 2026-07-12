"""Forms for the society admin panel."""

from django import forms

from accounts.models import User


class MemberEditForm(forms.ModelForm):
    """Small fix-up form for correcting a member's details on request."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "college", "mobile"]


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
