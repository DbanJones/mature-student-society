"""Account-related services shared with other apps (the admin panel imports
``send_associate_invite`` when approving waitlist requests)."""

from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


def build_set_password_url(user, request):
    """Absolute URL for the one-time set-password page for ``user``."""
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("accounts:set_password", kwargs={"uidb64": uidb64, "token": token})
    return request.build_absolute_uri(path)


def send_associate_invite(user, request):
    """Email a newly-approved associate their set-password link.

    Called by the admin panel after a waitlist request is approved. Works with
    the console email backend in development (the link is printed to stdout).
    Returns the set-password URL so callers can log or display it.
    """
    url = build_set_password_url(user, request)
    first_name = user.first_name or "there"
    body = (
        f"Hi {first_name},\n\n"
        "Good news — your request to join the University of Cambridge Mature "
        "Student Society members' portal has been approved.\n\n"
        "Set your password using this one-time link:\n\n"
        f"    {url}\n\n"
        f"You'll then be able to log in at any time with your email address "
        f"({user.email}) and your new password.\n\n"
        "We're looking forward to seeing you at an event soon.\n\n"
        "— The MSS committee\n"
    )
    send_mail(
        subject="Your MSS account is approved",
        message=body,
        from_email=None,  # DEFAULT_FROM_EMAIL
        recipient_list=[user.email],
    )
    return url


def send_profile_reminder(user, request):
    """Email a member who logged in but never finished the terms/profile
    step. Sent from the panel's "Remind" action."""
    login_url = request.build_absolute_uri(reverse("accounts:login"))
    first_name = user.first_name or "there"
    body = f"""Hi {first_name},

You started setting up your account on the University of Cambridge Mature
Student Society members' portal but didn't quite finish. Completing your
details takes about a minute, and until then you won't appear in the members
directory or be able to RSVP to events.

Pick up where you left off:

    {login_url}

If you'd rather not have an account, you can ignore this email.

— The MSS committee
"""
    send_mail(
        subject="Finish setting up your MSS account",
        message=body,
        from_email=None,
        recipient_list=[user.email],
    )
