"""Society admin panel views.

Every view is gated by ``portal_admin_required`` (society admins via
``User.is_portal_admin`` — NOT Django's ``is_staff``) and every mutating
action writes an ``AuditLog`` row via ``AuditLog.record``.
"""

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMessage
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.decorators import (
    portal_admin_required,
    super_admin_required,
    tag_owner_or_admin_required,
)
from accounts.models import User, WaitlistRequest, WhatsAppAccessRequest
from core.models import (
    SiteConfig,
    SitePage,
    TermsAcceptance,
    TermsRevision,
    TermsVersion,
)
from events.models import RSVP, Category, Event
from faq.models import ContactNode, DepartmentContact
from inbox.models import DirectMessage

from . import ai, services
from .forms import (
    ContactNodeForm,
    DepartmentContactForm,
    EmailSettingsForm,
    MailerForm,
    MemberEditForm,
    SitePageForm,
    TabVisibilityForm,
    TermsVersionForm,
    TagAdminForm,
    WhatsAppSettingsForm,
)
from .models import AuditLog, MailLog


def _display_name(member):
    return member.get_full_name() or member.username


def _redirect_back(request, fallback="panel:members"):
    """Redirect to the (local) page the action form was submitted from, so
    search/filter/pagination state survives row actions."""
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}
    ):
        return redirect(next_url)
    return redirect(fallback)


# --- dashboard -----------------------------------------------------------------

@tag_owner_or_admin_required
def home(request):
    # Tag owners who aren't society admins get exactly one page of the
    # panel — their tagged-events workspace — so /admin/ takes them there.
    if not request.user.is_portal_admin:
        return redirect("panel:tagged_events")
    return render(request, "panel/home.html", {
        "nav_active": "panel",
        "panel_tab": "home",
        "counts": services.home_counts(),
        "recent_audit": AuditLog.objects.select_related("actor")[:10],
    })


# --- waitlist approval queue ------------------------------------------------------

@portal_admin_required
def waitlist(request):
    status = request.GET.get("status", WaitlistRequest.Status.PENDING)
    if status not in WaitlistRequest.Status.values:
        status = WaitlistRequest.Status.PENDING
    queue = WaitlistRequest.objects.filter(status=status).select_related(
        "reviewed_by", "created_user"
    )
    if status != WaitlistRequest.Status.PENDING:
        queue = queue.order_by("-reviewed_at")
    tab_counts = {
        row["status"]: row["count"]
        for row in WaitlistRequest.objects.values("status").annotate(count=Count("id"))
    }
    return render(request, "panel/waitlist.html", {
        "nav_active": "panel",
        "panel_tab": "waitlist",
        "status": status,
        "requests": queue,
        "tab_counts": tab_counts,
    })


@portal_admin_required
@require_POST
def waitlist_review(request, pk):
    """Approve or reject one pending waitlist request (button name=decision).

    The request row is locked for the duration and its PENDING status is
    re-checked inside the lock, so two admins clicking Approve at the same
    time can't both create an account (which would raise a duplicate-username
    IntegrityError): the second one finds the request already handled.
    """
    decision = request.POST.get("decision")
    note = request.POST.get("review_note", "").strip()[:200]

    with transaction.atomic():
        wreq = get_object_or_404(
            WaitlistRequest.objects.select_for_update(), pk=pk
        )
        if wreq.status != WaitlistRequest.Status.PENDING:
            messages.info(
                request,
                f"That request was already {wreq.get_status_display().lower()} "
                "by another admin — nothing changed.",
            )
            return redirect("panel:waitlist")

        if decision == "approve":
            _approve_waitlist_request(request, wreq, note)
        elif decision == "reject":
            wreq.status = WaitlistRequest.Status.REJECTED
            wreq.reviewed_by = request.user
            wreq.reviewed_at = timezone.now()
            wreq.review_note = note
            wreq.save()
            AuditLog.record(
                request.user, "reject_waitlist", target=wreq.email, detail=note
            )
            messages.info(
                request,
                f"Rejected the request from {wreq.first_name} {wreq.last_name}.",
            )
        else:
            messages.error(request, "Unknown decision — nothing was changed.")
    return redirect("panel:waitlist")


def _approve_waitlist_request(request, wreq, note):
    """Create (or link) the associate account and email a set-password invite."""
    # The accounts app owns the invite email; imported lazily because the two
    # apps are developed in parallel and this keeps startup order-independent.
    from accounts.services import send_associate_invite

    email = wreq.email.strip().lower()
    existing = User.objects.filter(Q(username=email) | Q(email__iexact=email)).first()

    if existing is not None:
        # created_user is one-to-one; only link if no other request claims them.
        if not WaitlistRequest.objects.filter(created_user=existing).exclude(pk=wreq.pk).exists():
            wreq.created_user = existing
        messages.warning(
            request,
            f"An account for {email} already exists ({_display_name(existing)}). "
            "The request has been marked approved and linked to it; no invite "
            "email was sent.",
        )
        AuditLog.record(
            request.user, "approve_waitlist", target=email,
            detail=f"linked existing account #{existing.pk}. {note}".strip(),
        )
    else:
        user = User(
            username=email,
            email=email,
            first_name=wreq.first_name,
            last_name=wreq.last_name,
            mobile=wreq.mobile,
            account_type=User.AccountType.ASSOCIATE,
        )
        user.set_unusable_password()
        user.save()
        wreq.created_user = user
        _carry_over_terms_acceptance(wreq, user)
        try:
            send_associate_invite(user, request)
            messages.success(
                request,
                f"Approved {_display_name(user)} — a set-password invite is on "
                f"its way to {email}.",
            )
        except Exception as exc:  # account exists either way; surface the failure
            messages.warning(
                request,
                f"Approved and created the account for {email}, but the invite "
                f"email failed ({exc}). Send them a password reset manually.",
            )
        AuditLog.record(
            request.user, "approve_waitlist", target=email,
            detail=note or "associate account created",
        )

    wreq.status = WaitlistRequest.Status.APPROVED
    wreq.reviewed_by = request.user
    wreq.reviewed_at = timezone.now()
    wreq.review_note = note
    wreq.save()


def _carry_over_terms_acceptance(wreq, user):
    """Copy the applicant's waitlist acceptance onto their new account.

    They ticked the box on the public form, so asking them to accept the same
    version again at first login would be noise. The original timestamp is
    preserved — that is the moment they actually agreed. If the terms have
    moved on since they applied, nothing is carried over and the middleware
    asks them to accept the current version instead.
    """
    if wreq.terms_version is None:
        return
    terms = TermsVersion.objects.filter(number=wreq.terms_version).first()
    if terms is None:
        return
    user.record_terms_acceptance(
        terms,
        source="waitlist",
        accepted_at=wreq.terms_accepted_at,
    )


# --- member management --------------------------------------------------------------

@portal_admin_required
def members(request):
    q = request.GET.get("q", "").strip()
    type_filter = request.GET.get("type", "")
    only_admins = request.GET.get("admin") == "1"
    only_banned = request.GET.get("banned") == "1"

    members_qs = User.objects.all()
    if q:
        members_qs = members_qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(crsid__icontains=q)
            | Q(email__icontains=q)
            | Q(username__icontains=q)
            | Q(college__icontains=q)
        )
    if type_filter in User.AccountType.values:
        members_qs = members_qs.filter(account_type=type_filter)
    else:
        type_filter = ""
    if only_admins:
        members_qs = members_qs.filter(is_portal_admin=True)
    if only_banned:
        members_qs = members_qs.filter(is_banned=True)

    page = Paginator(members_qs, 50).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)

    return render(request, "panel/members.html", {
        "nav_active": "panel",
        "panel_tab": "members",
        "page": page,
        "q": q,
        "type_filter": type_filter,
        "only_admins": only_admins,
        "only_banned": only_banned,
        "qs": params.urlencode(),
    })


@portal_admin_required
def member_edit(request, pk):
    member = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        old_email = member.email
        form = MemberEditForm(request.POST, instance=member)
        if form.is_valid():
            updated = form.save(commit=False)
            # Associates log in with their email, and username mirrors it.
            if (
                updated.account_type == User.AccountType.ASSOCIATE
                and updated.username.lower() == old_email.lower()
                and updated.email
            ):
                new_username = updated.email.lower()
                clash = User.objects.filter(username=new_username).exclude(pk=member.pk)
                if clash.exists():
                    form.add_error(
                        "email", "Another account already uses this email as its login."
                    )
                else:
                    updated.username = new_username
            if form.errors:
                return render(request, "panel/member_edit.html", {
                    "nav_active": "panel", "panel_tab": "members",
                    "member": member, "form": form,
                })
            updated.save()
            AuditLog.record(
                request.user, "edit_member", target=member,
                detail="details updated from the panel",
            )
            messages.success(request, f"Saved changes to {_display_name(member)}.")
            return redirect("panel:members")
    else:
        form = MemberEditForm(instance=member)
    return render(request, "panel/member_edit.html", {
        "nav_active": "panel",
        "panel_tab": "members",
        "member": member,
        "form": form,
    })


@super_admin_required
@require_POST
def member_toggle_admin(request, pk):
    """Appointing and removing admins is reserved for the super admin."""
    member = get_object_or_404(User, pk=pk)
    if member == request.user and member.is_portal_admin:
        messages.error(
            request,
            "You cannot remove your own admin access.",
        )
        return _redirect_back(request)
    member.is_portal_admin = not member.is_portal_admin
    member.save(update_fields=["is_portal_admin"])
    action = "promote_admin" if member.is_portal_admin else "demote_admin"
    AuditLog.record(request.user, action, target=member)
    if member.is_portal_admin:
        messages.success(request, f"{_display_name(member)} is now a society admin.")
    else:
        messages.success(
            request, f"{_display_name(member)} is no longer a society admin."
        )
    return _redirect_back(request)


@portal_admin_required
@require_POST
def member_shadow_ban(request, pk):
    member = get_object_or_404(User, pk=pk)
    if member == request.user:
        messages.error(request, "You cannot shadow-ban yourself.")
    elif member.is_portal_admin:
        messages.error(request, "Admins can't be shadow-banned — demote them first.")
    elif member.is_shadow_banned:
        messages.info(request, f"{_display_name(member)} is already shadow-banned.")
    else:
        member.shadow_ban()
        AuditLog.record(request.user, "shadow_ban", target=member)
        messages.success(
            request,
            f"Shadow-banned {_display_name(member)}. They can keep using the "
            "site, but their events and messages are now invisible to everyone "
            "else.",
        )
    return _redirect_back(request)


@portal_admin_required
@require_POST
def member_shadow_unban(request, pk):
    member = get_object_or_404(User, pk=pk)
    if not member.is_shadow_banned:
        messages.info(request, f"{_display_name(member)} is not shadow-banned.")
    else:
        member.shadow_unban()
        AuditLog.record(request.user, "shadow_unban", target=member)
        messages.success(request, f"Removed the shadow ban on {_display_name(member)}.")
    return _redirect_back(request)


@portal_admin_required
@require_POST
def member_toggle_mute(request, pk):
    member = get_object_or_404(User, pk=pk)
    if member == request.user:
        messages.error(request, "You cannot mute yourself.")
        return _redirect_back(request, fallback="panel:messages")
    if member.is_portal_admin or member.is_super_admin:
        messages.error(request, "Admins can't be muted — demote them first.")
        return _redirect_back(request, fallback="panel:messages")
    member.can_send_messages = not member.can_send_messages
    member.save(update_fields=["can_send_messages"])
    action = "unmute_messages" if member.can_send_messages else "mute_messages"
    AuditLog.record(request.user, action, target=member)
    if member.can_send_messages:
        messages.success(request, f"{_display_name(member)} can send messages again.")
    else:
        messages.success(
            request,
            f"Muted {_display_name(member)} — they can read but not send messages.",
        )
    return _redirect_back(request, fallback="panel:messages")


@portal_admin_required
@require_POST
def member_ban(request, pk):
    member = get_object_or_404(User, pk=pk)
    if member == request.user:
        messages.error(request, "You cannot ban yourself.")
    elif member.is_portal_admin or member.is_super_admin:
        messages.error(request, "Admins can't be banned — demote them first.")
    elif member.is_banned:
        messages.info(request, f"{_display_name(member)} is already banned.")
    else:
        member.ban(request.user)
        AuditLog.record(request.user, "ban", target=member)
        messages.success(
            request,
            f"Banned {_display_name(member)}. Their session is closed and they "
            "can no longer log in.",
        )
    return _redirect_back(request)


@portal_admin_required
@require_POST
def member_unban(request, pk):
    member = get_object_or_404(User, pk=pk)
    if not member.is_banned:
        messages.info(request, f"{_display_name(member)} is not banned.")
    else:
        member.unban()
        AuditLog.record(request.user, "unban", target=member)
        messages.success(request, f"Unbanned {_display_name(member)}.")
    return _redirect_back(request)


@portal_admin_required
def member_delete(request, pk):
    """GET: confirmation page listing what cascades. POST: delete for real."""
    member = get_object_or_404(User, pk=pk)
    if member == request.user:
        messages.error(request, "You cannot delete your own account from the panel.")
        return redirect("panel:members")
    if member.is_portal_admin or member.is_super_admin:
        messages.error(request, "Admins can't be deleted — demote them first.")
        return redirect("panel:members")

    # Deleting a member cascades through the events they created: every RSVP
    # and restaurant rating that OTHER members left on those events is deleted
    # too. Surface that wider blast radius, not just the member's own rows.
    from events.models import RSVP
    from supper.models import Rating

    others_rsvps = RSVP.objects.filter(event__created_by=member).exclude(
        user=member
    ).count()
    others_ratings = Rating.objects.filter(event__created_by=member).exclude(
        user=member
    ).count()
    cascades = {
        "events": member.events_created.count(),
        "rsvps": member.rsvps.count(),
        "ratings": member.restaurant_ratings.count(),
        "guide_pages": member.guide_pages_created.count(),
        "guide_revisions": member.guide_revisions.count(),
        "others_rsvps_on_their_events": others_rsvps,
        "others_ratings_on_their_events": others_ratings,
    }
    if request.method == "POST":
        target = f"{_display_name(member)} <{member.email or member.username}>"
        detail = (
            f"deleted {cascades['events']} events, {cascades['rsvps']} own RSVPs, "
            f"{cascades['ratings']} own ratings, plus {others_rsvps} RSVPs and "
            f"{others_ratings} ratings by others on their events"
        )
        member.delete()
        AuditLog.record(request.user, "delete_user", target=target, detail=detail)
        messages.success(request, f"Deleted {target}.")
        return redirect("panel:members")

    return render(request, "panel/member_delete_confirm.html", {
        "nav_active": "panel",
        "panel_tab": "members",
        "member": member,
        "cascades": cascades,
    })


@portal_admin_required
@require_POST
def member_reset_whatsapp(request, pk):
    member = get_object_or_404(User, pk=pk)
    member.whatsapp_link_viewed_at = None
    member.save(update_fields=["whatsapp_link_viewed_at"])
    AuditLog.record(request.user, "reset_whatsapp", target=member)
    messages.success(
        request,
        f"Reset {_display_name(member)}'s one-time WhatsApp invite. "
        "They can view the invite link once more.",
    )
    return _redirect_back(request)


# --- WhatsApp access queue -----------------------------------------------------------

@portal_admin_required
def whatsapp_requests(request):
    config = SiteConfig.get()
    open_requests = (
        WhatsAppAccessRequest.objects.filter(status=WhatsAppAccessRequest.Status.OPEN)
        .select_related("user")
        .order_by("created_at")
    )
    history = (
        WhatsAppAccessRequest.objects.exclude(status=WhatsAppAccessRequest.Status.OPEN)
        .select_related("user", "handled_by")[:25]
    )
    return render(request, "panel/whatsapp.html", {
        "nav_active": "panel",
        "panel_tab": "whatsapp",
        "open_requests": open_requests,
        "history": history,
        "settings_form": WhatsAppSettingsForm(
            initial={"whatsapp_group_link": config.whatsapp_group_link}
        ),
        "used_count": User.objects.filter(
            whatsapp_link_viewed_at__isnull=False
        ).count(),
    })


@portal_admin_required
@require_POST
def whatsapp_settings(request):
    """Update the group invite link and/or reset everyone's one-time view
    (e.g. after WhatsApp rotates the invite)."""
    config = SiteConfig.get()
    form = WhatsAppSettingsForm(request.POST)
    if not form.is_valid():
        messages.error(request, "That doesn't look like a valid invite link.")
        return redirect("panel:whatsapp_requests")

    new_link = form.cleaned_data["whatsapp_group_link"]
    if new_link != config.whatsapp_group_link:
        config.whatsapp_group_link = new_link
        config.save()
        AuditLog.record(request.user, "update_whatsapp_link")
        messages.success(request, "Group invite link updated.")

    if request.POST.get("reset_all"):
        reset = User.objects.filter(
            whatsapp_link_viewed_at__isnull=False
        ).update(whatsapp_link_viewed_at=None)
        AuditLog.record(
            request.user, "reset_whatsapp_all", detail=f"{reset} member(s)"
        )
        messages.success(
            request,
            f"Reset the one-time invite for {reset} member(s) — everyone can "
            "view the link once more.",
        )
    return redirect("panel:whatsapp_requests")


@portal_admin_required
@require_POST
def whatsapp_handle(request, pk):
    wa = get_object_or_404(
        WhatsAppAccessRequest.objects.select_related("user"),
        pk=pk,
        status=WhatsAppAccessRequest.Status.OPEN,
    )
    action = request.POST.get("action")
    name = _display_name(wa.user)

    if action == "handled":
        wa.status = WhatsAppAccessRequest.Status.HANDLED
        detail = "marked handled"
        if request.POST.get("reset_link"):
            wa.user.whatsapp_link_viewed_at = None
            wa.user.save(update_fields=["whatsapp_link_viewed_at"])
            detail = "marked handled; one-time link reset"
            messages.success(
                request,
                f"Marked {name}'s request handled and reset their one-time link — "
                "they can view the invite link once more.",
            )
        else:
            messages.success(request, f"Marked {name}'s request handled.")
        AuditLog.record(request.user, "handle_whatsapp", target=wa.user, detail=detail)
    elif action == "declined":
        wa.status = WhatsAppAccessRequest.Status.DECLINED
        AuditLog.record(request.user, "decline_whatsapp", target=wa.user)
        messages.info(request, f"Declined {name}'s WhatsApp access request.")
    else:
        messages.error(request, "Unknown action — nothing was changed.")
        return redirect("panel:whatsapp_requests")

    wa.handled_by = request.user
    wa.handled_at = timezone.now()
    wa.save()
    return redirect("panel:whatsapp_requests")


# --- event management -----------------------------------------------------------------

@portal_admin_required
def events_admin(request):
    """Add, remove or ban (cancel/hide) events; toggle official status."""
    q = request.GET.get("q", "").strip()
    show = request.GET.get("show", "upcoming")
    events_qs = Event.objects.select_related("category", "created_by").annotate(
        going_count_agg=Count("rsvps", filter=Q(rsvps__status=RSVP.Status.GOING))
    )
    if q:
        events_qs = events_qs.search(q)
    if show == "past":
        events_qs = events_qs.filter(start__lt=timezone.now()).order_by("-start")
    elif show == "cancelled":
        events_qs = events_qs.filter(is_cancelled=True).order_by("-start")
    elif show == "all":
        events_qs = events_qs.order_by("-start")
    else:
        show = "upcoming"
        events_qs = events_qs.filter(
            start__gte=timezone.now(), is_cancelled=False
        ).order_by("start")

    page = Paginator(events_qs, 40).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "panel/events.html", {
        "nav_active": "panel",
        "panel_tab": "events",
        "page": page,
        "q": q,
        "show": show,
        "qs": params.urlencode(),
    })


@portal_admin_required
@require_POST
def event_action(request, pk):
    event = get_object_or_404(Event, pk=pk)
    action = request.POST.get("action")

    if action == "cancel":
        event.is_cancelled = True
        event.save(update_fields=["is_cancelled", "updated_at"])
        AuditLog.record(request.user, "cancel_event", target=event.title)
        messages.success(request, f"Cancelled “{event.title}” — it's off the calendar.")
    elif action == "restore":
        event.is_cancelled = False
        event.save(update_fields=["is_cancelled", "updated_at"])
        AuditLog.record(request.user, "restore_event", target=event.title)
        messages.success(request, f"Restored “{event.title}” to the calendar.")
    elif action == "toggle_official":
        event.is_official = not event.is_official
        event.save(update_fields=["is_official", "updated_at"])
        AuditLog.record(
            request.user,
            "mark_official" if event.is_official else "unmark_official",
            target=event.title,
        )
        messages.success(
            request,
            f"“{event.title}” is {'now' if event.is_official else 'no longer'} "
            f"an official {event.category.name} event.",
        )
    elif action == "toggle_super":
        if not request.user.is_super_admin:
            raise PermissionDenied("Only super admins can promote super events.")
        event.is_super = not event.is_super
        event.save(update_fields=["is_super", "updated_at"])
        AuditLog.record(
            request.user,
            "mark_super" if event.is_super else "unmark_super",
            target=event.title,
        )
        messages.success(
            request,
            f"“{event.title}” is {'now' if event.is_super else 'no longer'} "
            "a super event.",
        )
    elif action == "delete":
        title = event.title
        rsvp_count = event.rsvps.count()
        event.delete()
        AuditLog.record(
            request.user, "delete_event", target=title,
            detail=f"removed with {rsvp_count} RSVPs",
        )
        messages.success(request, f"Deleted “{title}” and its {rsvp_count} RSVPs.")
    else:
        messages.error(request, "Unknown action — nothing was changed.")
    return _redirect_back(request, fallback="panel:events")


# --- message moderation ------------------------------------------------------------------

@portal_admin_required
def messages_admin(request):
    """Review recent direct messages, remove abusive ones, mute senders.

    Members are told (on the messages page) that admins can review message
    traffic — this is the enforcement side of that.
    """
    q = request.GET.get("q", "").strip()
    dms = DirectMessage.objects.select_related("sender", "recipient", "removed_by")
    if q:
        dms = dms.filter(
            Q(sender__username__icontains=q)
            | Q(sender__first_name__icontains=q)
            | Q(sender__last_name__icontains=q)
            | Q(recipient__username__icontains=q)
            | Q(recipient__first_name__icontains=q)
            | Q(recipient__last_name__icontains=q)
            | Q(body__icontains=q)
        )
    page = Paginator(dms.order_by("-created_at"), 50).get_page(
        request.GET.get("page")
    )
    params = request.GET.copy()
    params.pop("page", None)

    muted = User.objects.filter(can_send_messages=False).order_by("first_name")
    return render(request, "panel/messages.html", {
        "nav_active": "panel",
        "panel_tab": "messages",
        "page": page,
        "q": q,
        "qs": params.urlencode(),
        "muted": muted,
    })


@portal_admin_required
@require_POST
def message_remove(request, pk):
    dm = get_object_or_404(
        DirectMessage.objects.select_related("sender", "recipient"), pk=pk
    )
    if dm.is_removed:
        messages.info(request, "That message was already removed.")
    else:
        dm.remove(request.user)
        AuditLog.record(
            request.user, "remove_message",
            target=f"{dm.sender} → {dm.recipient}",
            detail=dm.body[:120],
        )
        messages.success(
            request,
            "Message removed — the thread shows it was removed by an admin.",
        )
    return _redirect_back(request, fallback="panel:messages")


# --- content: CMS pages, tab visibility, contact map --------------------------------------

@portal_admin_required
def content(request):
    """One tab for everything editorial: site pages, who sees which nav
    tabs, and the who-to-contact map."""
    config = SiteConfig.get()
    if request.method == "POST" and "save_tabs" in request.POST:
        tabs_form = TabVisibilityForm(request.POST, config=config)
        if tabs_form.is_valid():
            tabs_form.apply(config)
            AuditLog.record(
                request.user, "update_tab_visibility",
                detail=str(config.tab_visibility)[:250],
            )
            messages.success(request, "Navigation visibility saved.")
            return redirect("panel:content")
    else:
        tabs_form = TabVisibilityForm(config=config)

    return render(request, "panel/content.html", {
        "nav_active": "panel",
        "panel_tab": "content",
        "pages": SitePage.objects.all(),
        "tabs_form": tabs_form,
        "contact_node_count": ContactNode.objects.count(),
        "dept_contacts": DepartmentContact.objects.all(),
    })


# --- recorded department contacts ------------------------------------------------------

@portal_admin_required
def dept_contact_add(request):
    form = DepartmentContactForm(
        request.POST or None, initial={"school": request.GET.get("school", "")}
    )
    if request.method == "POST" and form.is_valid():
        contact = form.save(commit=False)
        contact.added_by = request.user
        contact.save()
        AuditLog.record(
            request.user, "add_dept_contact",
            target=f"{contact.department} <{contact.email}>",
        )
        messages.success(request, f"Recorded {contact.department}.")
        return redirect("faq:department", slug=contact.school)
    return render(request, "panel/dept_contact_form.html", {
        "nav_active": "panel", "panel_tab": "content",
        "form": form, "contact": None,
    })


@portal_admin_required
def dept_contact_edit(request, pk):
    contact = get_object_or_404(DepartmentContact, pk=pk)
    form = DepartmentContactForm(request.POST or None, instance=contact)
    if request.method == "POST" and form.is_valid():
        form.save()
        AuditLog.record(
            request.user, "edit_dept_contact",
            target=f"{contact.department} <{contact.email}>",
        )
        messages.success(request, f"Saved {contact.department}.")
        return redirect("panel:content")
    return render(request, "panel/dept_contact_form.html", {
        "nav_active": "panel", "panel_tab": "content",
        "form": form, "contact": contact,
    })


@portal_admin_required
@require_POST
def dept_contact_delete(request, pk):
    contact = get_object_or_404(DepartmentContact, pk=pk)
    target = f"{contact.department} <{contact.email}>"
    contact.delete()
    AuditLog.record(request.user, "delete_dept_contact", target=target)
    messages.success(request, f"Removed {contact.department}.")
    return redirect("panel:content")


@portal_admin_required
def page_create(request):
    form = SitePageForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        page = form.save(commit=False)
        page.updated_by = request.user
        page.save()
        AuditLog.record(request.user, "create_page", target=page.title)
        messages.success(
            request, f"Created “{page.title}” at /pages/{page.slug}/."
        )
        return redirect("panel:content")
    return render(request, "panel/page_form.html", {
        "nav_active": "panel", "panel_tab": "content",
        "form": form, "page": None,
    })


@portal_admin_required
def page_edit(request, pk):
    page = get_object_or_404(SitePage, pk=pk)
    form = SitePageForm(request.POST or None, instance=page)
    if request.method == "POST" and form.is_valid():
        page = form.save(commit=False)
        page.updated_by = request.user
        page.save()
        AuditLog.record(request.user, "edit_page", target=page.title)
        messages.success(request, f"Saved “{page.title}”.")
        return redirect("panel:content")
    return render(request, "panel/page_form.html", {
        "nav_active": "panel", "panel_tab": "content",
        "form": form, "page": page,
    })


@portal_admin_required
@require_POST
def page_delete(request, pk):
    page = get_object_or_404(SitePage, pk=pk)
    title = page.title
    page.delete()
    AuditLog.record(request.user, "delete_page", target=title)
    messages.success(request, f"Deleted “{title}”.")
    return redirect("panel:content")


# --- who-to-contact map editor --------------------------------------------------------------

def _map_rows(node, depth=0):
    rows = [(node, depth)]
    for child in node.children.all():
        rows += _map_rows(child, depth + 1)
    return rows


@portal_admin_required
def contact_map(request):
    root = ContactNode.get_root()
    rows = _map_rows(root) if root else []
    return render(request, "panel/contact_map.html", {
        "nav_active": "panel",
        "panel_tab": "content",
        "rows": rows,
        "root": root,
    })


@portal_admin_required
def contact_node_add(request):
    parent = get_object_or_404(ContactNode, pk=request.GET.get("parent")
                               or request.POST.get("parent"))
    form = ContactNodeForm(request.POST or None,
                           initial={"kind": ContactNode.Kind.RESULT})
    if request.method == "POST" and form.is_valid():
        node = form.save(commit=False)
        node.parent = parent
        node.save()
        AuditLog.record(
            request.user, "add_contact_node", target=node.option_label,
        )
        messages.success(request, f"Added “{node.option_label}” to the map.")
        return redirect("panel:contact_map")
    return render(request, "panel/contact_node_form.html", {
        "nav_active": "panel", "panel_tab": "content",
        "form": form, "node": None, "parent": parent,
    })


@portal_admin_required
def contact_node_edit(request, pk):
    node = get_object_or_404(ContactNode, pk=pk)
    is_root = node.parent_id is None
    form = ContactNodeForm(request.POST or None, instance=node, is_root=is_root)
    if request.method == "POST" and form.is_valid():
        form.save()
        AuditLog.record(
            request.user, "edit_contact_node",
            target=node.option_label or node.question,
        )
        messages.success(request, "Contact map updated.")
        return redirect("panel:contact_map")
    return render(request, "panel/contact_node_form.html", {
        "nav_active": "panel", "panel_tab": "content",
        "form": form, "node": node, "parent": node.parent,
    })


@portal_admin_required
@require_POST
def contact_node_delete(request, pk):
    node = get_object_or_404(ContactNode, pk=pk)
    if node.parent_id is None:
        messages.error(request, "The root question can't be deleted — edit it instead.")
        return redirect("panel:contact_map")
    label = node.option_label
    descendant_count = len(_map_rows(node)) - 1
    node.delete()
    AuditLog.record(
        request.user, "delete_contact_node", target=label,
        detail=f"with {descendant_count} descendant node(s)",
    )
    messages.success(
        request,
        f"Deleted “{label}”"
        + (f" and its {descendant_count} descendant node(s)." if descendant_count else "."),
    )
    return redirect("panel:contact_map")


# --- super admin -------------------------------------------------------------------------

@super_admin_required
def superadmin(request):
    """The webmaster's tab: appoint admins, manage tags & their owners, and
    hold the email API key + tone-of-voice brief."""
    config = SiteConfig.get()
    admins = User.objects.filter(is_portal_admin=True).order_by("first_name")
    non_admins = User.objects.filter(
        is_portal_admin=False, is_banned=False
    ).order_by("first_name")
    tags = Category.objects.prefetch_related("owners").annotate(
        event_count=Count("events")
    )
    return render(request, "panel/superadmin.html", {
        "nav_active": "panel",
        "panel_tab": "superadmin",
        "admins": admins,
        "non_admins": non_admins,
        "tags": tags,
        "email_form": EmailSettingsForm(initial={
            "email_tone": config.email_tone,
            "email_ai_engine": config.email_ai_engine,
        }),
        "config": config,
    })


@super_admin_required
@require_POST
def superadmin_email(request):
    config = SiteConfig.get()
    form = EmailSettingsForm(request.POST)
    if form.is_valid():
        changed = []
        new_key = form.cleaned_data["email_api_key"].strip()
        if new_key:
            config.email_api_key = new_key
            changed.append("API key")
        if form.cleaned_data["email_ai_engine"] != config.email_ai_engine:
            config.email_ai_engine = form.cleaned_data["email_ai_engine"]
            changed.append(
                f"AI engine → {form.cleaned_data['email_ai_engine']}"
            )
        if form.cleaned_data["email_tone"] != config.email_tone:
            config.email_tone = form.cleaned_data["email_tone"]
            changed.append("tone of voice")
        if changed:
            config.save()
            AuditLog.record(
                request.user, "update_email_settings", detail=", ".join(changed)
            )
            messages.success(request, f"Saved: {', '.join(changed)}.")
        else:
            messages.info(request, "Nothing changed.")
    else:
        messages.error(request, "Couldn't save the email settings.")
    return redirect("panel:superadmin")


@super_admin_required
def tag_create(request):
    form = TagAdminForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        tag = form.save()
        AuditLog.record(request.user, "create_tag", target=tag.name)
        messages.success(request, f"Created the “{tag.name}” tag.")
        return redirect("panel:superadmin")
    return render(request, "panel/tag_form.html", {
        "nav_active": "panel",
        "panel_tab": "superadmin",
        "form": form,
        "tag": None,
    })


@super_admin_required
def tag_admin_edit(request, pk):
    tag = get_object_or_404(Category, pk=pk)
    form = TagAdminForm(request.POST or None, instance=tag)
    if request.method == "POST" and form.is_valid():
        form.save()
        AuditLog.record(
            request.user, "edit_tag", target=tag.name,
            detail="owners: " + ", ".join(
                str(u) for u in form.cleaned_data["owners"]
            )[:250],
        )
        messages.success(request, f"Saved the “{tag.name}” tag.")
        return redirect("panel:superadmin")
    return render(request, "panel/tag_form.html", {
        "nav_active": "panel",
        "panel_tab": "superadmin",
        "form": form,
        "tag": tag,
    })


@super_admin_required
@require_POST
def tag_delete(request, pk):
    tag = get_object_or_404(Category.objects.annotate(event_count=Count("events")), pk=pk)
    if tag.event_count:
        messages.error(
            request,
            f"“{tag.name}” still has {tag.event_count} event(s) — move or "
            "delete them first.",
        )
    else:
        name = tag.name
        tag.delete()
        AuditLog.record(request.user, "delete_tag", target=name)
        messages.success(request, f"Deleted the “{name}” tag.")
    return redirect("panel:superadmin")


# --- tagged events: the tag owners' workspace -------------------------------------------

def _managed_tags(user):
    """The tags this user runs. Society admins manage every tag."""
    qs = Category.objects.prefetch_related("owners")
    if user.is_portal_admin:
        return qs
    return qs.filter(owners=user)


@tag_owner_or_admin_required
def tagged_events(request):
    """The tag owners' workspace: every event carrying one of their tags,
    with promote/demote controls, plus a picker to adopt an existing event
    into the tag (e.g. turning a member's dinner into a Supper Club event).
    """
    tags = list(_managed_tags(request.user))
    now = timezone.now()

    going = Count("rsvps", filter=Q(rsvps__status=RSVP.Status.GOING))
    for tag in tags:
        events = (
            tag.events.filter(is_cancelled=False)
            .select_related("created_by", "host")
            .annotate(going_count_agg=going)
        )
        tag.upcoming_events = list(
            events.filter(start__gte=now).by_promotion()
        )
        tag.past_events = list(events.filter(start__lt=now).order_by("-start")[:8])

    # The adopt picker: search upcoming events that don't yet carry the
    # chosen tag. Only run when a tag owner has actually asked.
    adopt_q = request.GET.get("adopt_q", "").strip()
    adopt_results = []
    if adopt_q and tags:
        adopt_results = list(
            Event.objects.filter(is_cancelled=False, start__gte=now)
            .exclude(category__in=tags, is_official=True)
            .search(adopt_q)
            .select_related("category", "created_by")
            .order_by("start")[:12]
        )

    return render(request, "panel/tagged_events.html", {
        "nav_active": "panel",
        "panel_tab": "tagged",
        "tags": tags,
        "adopt_q": adopt_q,
        "adopt_results": adopt_results,
    })


@tag_owner_or_admin_required
@require_POST
def tagged_event_action(request, pk):
    """Promote/demote an event within a managed tag, or adopt an event into
    one. Every path re-checks ownership server-side; society admins may act
    on any tag."""
    event = get_object_or_404(Event.objects.select_related("category"), pk=pk)
    action = request.POST.get("action")

    def owns(tag):
        return request.user.is_portal_admin or tag.is_owned_by(request.user)

    if action in ("promote", "demote"):
        if not owns(event.category):
            raise PermissionDenied(
                f"You don't own the “{event.category.name}” tag."
            )
        event.is_official = action == "promote"
        event.save(update_fields=["is_official", "updated_at"])
        AuditLog.record(
            request.user,
            "mark_official" if event.is_official else "unmark_official",
            target=event.title,
            detail=f"tag: {event.category.name}",
        )
        if event.is_official:
            messages.success(
                request,
                f"“{event.title}” is now an official {event.category.name} "
                "event — it ranks above ordinary member events.",
            )
        else:
            messages.success(
                request,
                f"“{event.title}” is back to an ordinary member event.",
            )
    elif action == "adopt":
        tag = get_object_or_404(Category, pk=request.POST.get("tag"))
        if not owns(tag):
            raise PermissionDenied(f"You don't own the “{tag.name}” tag.")
        old_tag = event.category
        event.category = tag
        event.is_official = True
        if not tag.has_restaurant_ratings:
            event.restaurant = None
        event.save(
            update_fields=["category", "is_official", "restaurant", "updated_at"]
        )
        AuditLog.record(
            request.user, "adopt_event", target=event.title,
            detail=f"{old_tag.name} → {tag.name}, promoted",
        )
        messages.success(
            request,
            f"Adopted “{event.title}” into {tag.name} and promoted it — "
            f"its creator ({event.created_by.get_full_name() or event.created_by.username}) "
            "can still edit it.",
        )
    else:
        messages.error(request, "Unknown action — nothing was changed.")
    return redirect("panel:tagged_events")


# --- terms and conditions --------------------------------------------------------------

@portal_admin_required
def terms(request):
    """List every version of the terms, with who last touched each one.

    Any society admin may add, change and delete terms; every one of those
    actions writes both a TermsRevision (the full text as it then stood) and
    an AuditLog entry, so "who changed what, when" is answerable afterwards.
    """
    versions = (
        TermsVersion.objects.select_related("created_by", "updated_by")
        .annotate(acceptance_count=Count("acceptances"))
    )
    current = TermsVersion.current()
    members = User.objects.filter(is_banned=False).count()
    accepted_current = (
        TermsAcceptance.objects.filter(version_number=current.number).count()
        if current else 0
    )
    return render(request, "panel/terms.html", {
        "nav_active": "panel",
        "panel_tab": "terms",
        "versions": versions,
        "current": current,
        "member_count": members,
        "accepted_current": accepted_current,
        "outstanding": max(0, members - accepted_current) if current else 0,
        "recent_changes": TermsRevision.objects.select_related("editor")[:15],
    })


@portal_admin_required
def terms_create(request):
    form = TermsVersionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        version = form.save(commit=False)
        version.created_by = request.user
        version.updated_by = request.user
        if version.is_published:
            version.published_at = timezone.now()
        version.save()
        version.save_revision(request.user, TermsRevision.Action.CREATED)
        AuditLog.record(
            request.user, "create_terms", target=f"v{version.number}",
            detail=version.change_note[:300],
        )
        if version.is_published:
            version.save_revision(request.user, TermsRevision.Action.PUBLISHED)
            AuditLog.record(
                request.user, "publish_terms", target=f"v{version.number}"
            )
            messages.success(
                request,
                f"Published version {version.number}. Every member will be "
                "asked to accept it the next time they load a page.",
            )
        else:
            messages.success(
                request, f"Saved version {version.number} as a draft."
            )
        return redirect("panel:terms")
    return render(request, "panel/terms_form.html", {
        "nav_active": "panel", "panel_tab": "terms",
        "form": form, "version": None,
    })


@portal_admin_required
def terms_edit(request, pk):
    version = get_object_or_404(TermsVersion, pk=pk)
    was_published = version.is_published
    form = TermsVersionForm(request.POST or None, instance=version)
    if request.method == "POST" and form.is_valid():
        version = form.save(commit=False)
        version.updated_by = request.user
        newly_published = version.is_published and not was_published
        if newly_published:
            version.published_at = timezone.now()
        version.save()

        if newly_published:
            action = TermsRevision.Action.PUBLISHED
        elif was_published and not version.is_published:
            action = TermsRevision.Action.UNPUBLISHED
        else:
            action = TermsRevision.Action.EDITED
        version.save_revision(request.user, action)
        AuditLog.record(
            request.user, f"{action}_terms", target=f"v{version.number}",
            detail=version.change_note[:300],
        )

        if newly_published:
            messages.success(
                request,
                f"Published version {version.number}. Every member will be "
                "asked to accept it the next time they load a page.",
            )
        elif action == TermsRevision.Action.UNPUBLISHED:
            messages.warning(
                request,
                f"Unpublished version {version.number}. Members are no longer "
                "asked to accept it.",
            )
        else:
            messages.success(request, f"Saved version {version.number}.")
        return redirect("panel:terms")
    return render(request, "panel/terms_form.html", {
        "nav_active": "panel", "panel_tab": "terms",
        "form": form, "version": version,
        "acceptance_count": version.acceptances.count(),
    })


@portal_admin_required
@require_POST
def terms_delete(request, pk):
    """Delete a version.

    The acceptance rows are deliberately NOT deleted with it: they carry their
    own copy of the version number and title, so the record of who agreed to
    what survives. The same goes for the revision history.
    """
    version = get_object_or_404(TermsVersion, pk=pk)
    number, accepted = version.number, version.acceptances.count()
    version.save_revision(request.user, TermsRevision.Action.DELETED)
    version.delete()
    AuditLog.record(
        request.user, "delete_terms", target=f"v{number}",
        detail=f"{accepted} acceptance(s) kept in the log",
    )
    messages.success(
        request,
        f"Deleted version {number}. The {accepted} recorded acceptance(s) "
        "have been kept.",
    )
    return redirect("panel:terms")


@portal_admin_required
def terms_acceptances(request, pk):
    """Who has accepted one version of the terms, and when."""
    version = get_object_or_404(TermsVersion, pk=pk)
    return render(request, "panel/terms_acceptances.html", {
        "nav_active": "panel", "panel_tab": "terms",
        "version": version,
        "acceptances": (
            TermsAcceptance.objects.filter(version_number=version.number)
            .select_related("user")
        ),
        "outstanding": (
            User.objects.filter(is_banned=False)
            .exclude(terms_accepted_version__gte=version.number)
            .order_by("first_name", "last_name")
        ),
    })


# --- statistics ----------------------------------------------------------------------

@portal_admin_required
def stats(request):
    return render(request, "panel/stats.html", {
        "nav_active": "panel",
        "panel_tab": "stats",
        "summary": services.stats_summary(),
        "by_college": services.members_by_college(),
        "by_category": services.events_by_category(),
        "rsvps_by_month": services.rsvps_per_month(),
        "top_hosts": services.top_hosts(),
        "top_restaurants": services.top_restaurants(),
    })


# --- What's On mailer ------------------------------------------------------------------

@portal_admin_required
def mailer(request):
    config = SiteConfig.get()

    if request.method == "POST":
        form = MailerForm(request.POST)
        if form.is_valid() and request.POST.get("action") == "ai_draft":
            # Rewrite the current draft with AI and re-render for review — do
            # NOT send. Keeps whatever the admin has edited into recipient/
            # subject, and only replaces the body.
            if not config.email_api_key:
                messages.error(
                    request, "Add an AI API key on the Super admin tab first."
                )
            else:
                try:
                    new_body = services.ai_draft_mailer(
                        config, form.cleaned_data["subject"], form.cleaned_data["body"]
                    )
                except ai.AIDraftError as exc:
                    messages.error(request, f"AI drafting failed: {exc}")
                else:
                    AuditLog.record(
                        request.user, "ai_draft_mailer",
                        detail=f"engine={config.email_ai_engine}",
                    )
                    messages.success(
                        request, "Draft rewritten by AI — review it before sending."
                    )
                    form = MailerForm(initial={
                        "recipient": form.cleaned_data["recipient"],
                        "subject": form.cleaned_data["subject"],
                        "body": new_body,
                    })
            # fall through to render with the (re-drafted or unchanged) form
        elif form.is_valid():
            recipient = form.cleaned_data["recipient"]
            subject = form.cleaned_data["subject"][:200]
            body = form.cleaned_data["body"]
            ok, error = True, ""
            try:
                EmailMessage(
                    subject=subject,
                    body=body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[recipient],
                ).send()
            except Exception as exc:
                ok, error = False, str(exc)[:300]
            MailLog.objects.create(
                subject=subject,
                body=body,
                recipients=recipient,
                sent_by=request.user,
                ok=ok,
                error=error,
            )
            AuditLog.record(
                request.user, "send_mailer", target=recipient,
                detail=(f"{'sent' if ok else 'FAILED'}: {subject}")[:300],
            )
            if ok:
                messages.success(request, f"What's On sent to {recipient}.")
            else:
                messages.error(request, f"Sending failed: {error}")
            return redirect("panel:mailer")
    else:
        subject, body = services.build_whats_on_email(request)
        form = MailerForm(initial={
            "recipient": config.mailing_list_address,
            "subject": subject,
            "body": body,
        })

    return render(request, "panel/mailer.html", {
        "nav_active": "panel",
        "panel_tab": "mailer",
        "form": form,
        "event_count": services.whats_on_events().count(),
        "recent_logs": MailLog.objects.select_related("sent_by")[:5],
    })


# --- audit log ---------------------------------------------------------------------------

@portal_admin_required
def audit(request):
    action = request.GET.get("action", "")
    entries = AuditLog.objects.select_related("actor")
    if action:
        entries = entries.filter(action=action)
    actions = (
        AuditLog.objects.order_by("action").values_list("action", flat=True).distinct()
    )
    page = Paginator(entries, 50).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "panel/audit.html", {
        "nav_active": "panel",
        "panel_tab": "audit",
        "page": page,
        "actions": actions,
        "action_filter": action,
        "qs": params.urlencode(),
    })
