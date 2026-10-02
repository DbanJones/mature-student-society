"""Direct-message views: an inbox of conversations and per-member threads.

Sending is guarded in one place (``inbox.policy`` plus the daily cap in
``_send_denied_reason``) so every rule applies to every send path.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User
from core.models import SiteConfig

from .models import MAX_MESSAGE_LENGTH, DirectMessage, MessageBlock
from .policy import messaging_denied_reason


def _get_member(request, username):
    """The other participant. Shadow-banned members stay reachable only to
    admins and to people they already have a thread with — otherwise they
    'don't exist', which is the point of a shadow ban."""
    member = get_object_or_404(User, username=username, is_banned=False)
    if member == request.user:
        raise Http404("That's you.")
    if (
        member.is_shadow_banned
        and not request.user.is_portal_admin
        and not DirectMessage.objects.between(request.user, member).exists()
    ):
        raise Http404("No member found.")
    return member


def _send_denied_reason(sender, recipient):
    denied = messaging_denied_reason(sender, recipient)
    if denied:
        return denied
    if DirectMessage.sender_is_over_daily_cap(sender):
        return "You've hit the daily message limit — try again tomorrow."
    return None


@login_required
def inbox(request):
    """Conversations, most recent first, with unread counts."""
    visible = DirectMessage.objects.visible_to(request.user)
    mine = visible.filter(Q(sender=request.user) | Q(recipient=request.user))

    # Latest message per counterpart.
    partners = {}
    for msg in mine.select_related("sender", "recipient").order_by("-created_at"):
        other = msg.recipient if msg.sender == request.user else msg.sender
        if other.pk not in partners:
            partners[other.pk] = {"member": other, "last": msg, "unread": 0}
    unread = (
        visible.filter(recipient=request.user, read_at__isnull=True,
                       removed_at__isnull=True)
        .values_list("sender", flat=True)
    )
    for sender_id in unread:
        if sender_id in partners:
            partners[sender_id]["unread"] += 1

    # In restricted mode an ordinary member can only start a conversation
    # with the committee, so list the admins they may write to.
    restricted = (
        SiteConfig.get().messaging_mode == SiteConfig.MessagingMode.RESTRICTED
        and not request.user.messaging_enabled
    )
    committee = []
    if restricted and not request.user.is_muted:
        committee = list(
            User.objects.filter(is_portal_admin=True, is_banned=False)
            .exclude(pk=request.user.pk)
            .order_by("first_name", "last_name")
        )

    return render(request, "inbox/inbox.html", {
        "nav_active": "messages",
        "conversations": list(partners.values()),
        "restricted": restricted,
        "muted": request.user.is_muted,
        "committee": committee,
    })


@login_required
def thread(request, username):
    member = _get_member(request, username)

    if request.method == "POST":
        body = (request.POST.get("body") or "").strip()[:MAX_MESSAGE_LENGTH]
        denied = _send_denied_reason(request.user, member)
        if denied:
            messages.error(request, denied)
        elif not body:
            messages.error(request, "Write something first.")
        else:
            DirectMessage.objects.create(
                sender=request.user, recipient=member, body=body
            )
        return redirect("inbox:thread", username=member.username)

    thread_messages = list(
        DirectMessage.objects.between(request.user, member)
        .visible_to(request.user)
        .select_related("sender")
    )
    DirectMessage.objects.filter(
        sender=member, recipient=request.user, read_at__isnull=True
    ).update(read_at=timezone.now())

    return render(request, "inbox/thread.html", {
        "nav_active": "messages",
        "member": member,
        "thread_messages": thread_messages,
        "is_blocked": MessageBlock.objects.filter(
            user=request.user, blocked=member
        ).exists(),
        "send_denied": _send_denied_reason(request.user, member),
        "max_length": MAX_MESSAGE_LENGTH,
    })


@login_required
@require_POST
def block_toggle(request, username):
    member = _get_member(request, username)
    existing = MessageBlock.objects.filter(user=request.user, blocked=member)
    if existing.exists():
        existing.delete()
        messages.success(
            request, f"Unblocked {member.get_full_name() or member.username}."
        )
    else:
        MessageBlock.objects.create(user=request.user, blocked=member)
        messages.success(
            request,
            f"Blocked {member.get_full_name() or member.username} — you will "
            "no longer receive messages from each other.",
        )
    return redirect("inbox:thread", username=member.username)
