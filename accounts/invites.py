"""Invite a member: a member gives an email address, the site sends that
person an email with a link of their own, and when they join the inviting
member is credited. The admin panel (People > Invitations) counts the
credits to show who brings in the most new members.

Joining happens two ways, and both are covered:

* a University member logs in with Raven for the first time
  (``accounts.auth.CrsidBackend.configure_user``);
* anyone else fills in the waitlist form, and an admin approves it
  (``panel.views._approve_waitlist_request``).

The link's token is kept in the visitor's session between opening the link
and joining. If the session is lost (another device, another browser) the
new member's email address is matched against open invitations instead.
"""

import datetime
import logging
import secrets

from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from core import emailbody
from core.net import absolute_url

from .models import Invitation, User

logger = logging.getLogger(__name__)

SESSION_KEY = "invite_token"
# A member may send this many invitations in any 24 hours...
DAILY_LIMIT = 10
# ...and one address is written to by at most this many different members,
# so nobody outside the society can be pestered through the site.
PER_ADDRESS_LIMIT = 3


def _is_member(email):
    """Is there already an account for this address?"""
    query = Q(email__iexact=email) | Q(username__iexact=email)
    local, _, domain = email.partition("@")
    if domain == "cam.ac.uk":
        query |= Q(crsid__iexact=local)
    return User.objects.filter(query).exists()


def invite_text(inviter, link, society_name):
    name = inviter.get_full_name() or inviter.username
    return (
        "Hello,\n\n"
        f"{name} would like you to join the {society_name}: events, college "
        "lunches and formal dinners, and a friendly crowd of mature students "
        "at Cambridge.\n\n"
        "Use this link to join:\n\n"
        f"{link}\n\n"
        f"The link is yours: it tells us that {name} invited you.\n\n"
        f"You have been sent this once, because {name} gave us your address. "
        "If you would rather not join, there is nothing to do and we will "
        "not write again.\n"
    )


def send_invitation(inviter, email, request):
    """Invite ``email`` on behalf of ``inviter``.

    Returns ``(ok, message)``; the message is ready to show to the member.
    """
    from core.models import SiteConfig

    email = (email or "").strip().lower()
    if inviter.is_shadow_banned or inviter.is_banned:
        # Looks the same as a real send; nothing is stored or sent.
        return True, f"Invitation sent to {email}."
    if _is_member(email):
        return False, "Good news: that person is already a member."
    if Invitation.objects.filter(inviter=inviter, email__iexact=email).exists():
        return False, "You have already invited that address."
    since = timezone.now() - datetime.timedelta(hours=24)
    if Invitation.objects.filter(inviter=inviter, by_link=False, created_at__gte=since).count() >= DAILY_LIMIT:
        return False, f"That is {DAILY_LIMIT} invitations in a day, which is the limit. Try again tomorrow."
    if Invitation.objects.filter(email__iexact=email).count() >= PER_ADDRESS_LIMIT:
        return False, "Other members have already invited that person, so we won't email them again."

    try:
        with transaction.atomic():
            invitation = Invitation.objects.create(
                inviter=inviter, email=email, token=secrets.token_urlsafe(24),
            )
    except IntegrityError:  # a double press
        return False, "You have already invited that address."

    config = SiteConfig.get()
    link = absolute_url(request, reverse("accounts:invite_accept", args=[invitation.token]))
    name = inviter.get_full_name() or inviter.username
    subject = f"{name} has invited you to join {config.short_name}"
    body = invite_text(inviter, link, config.society_name)
    try:
        send_mail(
            subject=subject, message=body, from_email=None, recipient_list=[email],
            html_message=emailbody.plain_html(body, title=subject),
        )
    except Exception:
        logger.exception("Could not send an invitation email")
        invitation.delete()
        return False, "The invitation could not be sent just now. Please try again later."
    return True, f"Invitation sent to {email}."


def personal_link(user, request):
    """The member's own invitation link: the same one every time, for
    sharing in a message or showing as a QR code."""
    return absolute_url(request, reverse("accounts:invite_accept", args=[user.get_invite_code()]))


def qr_svg(link):
    """The link as a QR code, as inline SVG that scales to its box."""
    import segno

    return segno.make(link, error="m").svg_inline(
        scale=1, border=3, dark="#14352a", light="#ffffff", omitsize=True,
    )


def tools(user, request):
    """Everything the "Invite a member" box needs for ``user``."""
    from urllib.parse import quote

    from core.models import SiteConfig

    link = personal_link(user, request)
    society = SiteConfig.get().society_name
    words = f"Come and join the {society}. This link is my invitation to you:"
    return {
        "link": link,
        "qr": qr_svg(link),
        "words": words,
        "whatsapp": "https://wa.me/?text=" + quote(f"{words} {link}", safe=""),
        "counts": counts_for(user),
    }


def remember(request, token):
    """Keep the link's code until the visitor joins. Returns the member
    who invited them, or None when the link is not (or no longer) real.
    The code is either one emailed invitation's or a member's own."""
    invitation = (
        Invitation.objects.filter(token=token, joined_user__isnull=True)
        .select_related("inviter").first()
    )
    inviter = invitation.inviter if invitation else None
    if inviter is None and token:
        inviter = User.objects.filter(invite_code=token, is_banned=False).first()
    if inviter is not None:
        request.session[SESSION_KEY] = token
    return inviter


def from_session(request, email=""):
    """The open invitation this visitor arrived through, if any. For a
    member's own link the invitation is made here, for ``email`` (the
    address the visitor is joining with)."""
    token = getattr(request, "session", {}).get(SESSION_KEY) if request is not None else None
    if not token:
        return None
    invitation = Invitation.objects.filter(token=token, joined_user__isnull=True).first()
    if invitation is not None:
        return invitation
    email = (email or "").strip().lower()
    owner = User.objects.filter(invite_code=token, is_banned=False).first()
    if owner is None or not email:
        return None
    try:
        with transaction.atomic():
            invitation, _ = Invitation.objects.get_or_create(
                inviter=owner, email=email,
                defaults={"token": secrets.token_urlsafe(24), "by_link": True},
            )
    except IntegrityError:
        return None
    return invitation if invitation.joined_user_id is None else None


def credit(user, invitation=None):
    """Record that ``user``, who has just joined, came through an
    invitation: the one given, or else the earliest open invitation sent to
    their email address. Does nothing if there is none. Never raises: a
    problem here must not stop someone logging in or being approved."""
    try:
        if Invitation.objects.filter(joined_user=user).exists():
            return None
        open_invitations = Invitation.objects.filter(joined_user__isnull=True).exclude(inviter=user)
        chosen = None
        if invitation is not None:
            chosen = open_invitations.filter(pk=invitation.pk).first()
        if chosen is None and user.email:
            chosen = open_invitations.filter(email__iexact=user.email).order_by("created_at", "pk").first()
        if chosen is None:
            return None
        with transaction.atomic():
            chosen.joined_user = user
            chosen.joined_at = timezone.now()
            chosen.save(update_fields=["joined_user", "joined_at"])
        return chosen
    except Exception:
        logger.exception("Could not credit an invitation")
        return None


def counts_for(user):
    """What a member sees about their own invitations."""
    totals = Invitation.objects.filter(inviter=user).aggregate(
        sent=Count("pk"), joined=Count("joined_user"),
    )
    return {"sent": totals["sent"] or 0, "joined": totals["joined"] or 0}


def leaderboard():
    """Members who have invited anyone: most new members first."""
    return list(
        User.objects.annotate(
            invites_sent=Count("invitations", distinct=True),
            invites_joined=Count("invitations__joined_user", distinct=True),
        )
        .filter(invites_sent__gt=0)
        .order_by("-invites_joined", "-invites_sent", "first_name", "last_name")
    )
