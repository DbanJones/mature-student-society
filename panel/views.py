"""Society admin panel views.

Every view is gated by ``portal_admin_required`` (society admins via
``User.is_portal_admin`` — NOT Django's ``is_staff``) and every mutating
action writes an ``AuditLog`` row via ``AuditLog.record``.
"""

import datetime

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMessage
from django.http import Http404, HttpResponse
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
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
    VISIBILITY_CHOICES,
    Activity,
    CommitteeMember,
    Picture,
    SiteConfig,
    SitePage,
    SitePageRevision,
    TermsAcceptance,
    TermsRevision,
    TermsVersion,
)
from core import mailinglist
from core.richtext import richtext_problem
from events.models import RSVP, Category, Event
from faq.models import ContactNode, DepartmentContact
from inbox.models import DirectMessage

from . import ai, listsync, services, sheets
from .forms import (
    ActivityForm,
    BannerForm,
    TermDatesForm,
    CommitteeMemberForm,
    ContactNodeForm,
    DepartmentContactForm,
    EmailSettingsForm,
    MailerForm,
    MapSettingsForm,
    MemberEditForm,
    MessagingSettingsForm,
    ListPasteForm,
    OldListUploadForm,
    PictureForm,
    SitePageForm,
    TabVisibilityForm,
    TermsVersionForm,
    TagAdminForm,
    WhatsAppSettingsForm,
)
from .models import AuditLog, ErrorEvent, ListMember, ListSync, MailLog, OldListFile, OldSubscriber


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
    from .healthchecks import latest_summary

    return render(request, "panel/home.html", {
        "nav_active": "panel",
        "panel_tab": "home",
        "health": latest_summary(),
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
    only_incomplete = request.GET.get("incomplete") == "1"
    only_unlisted = request.GET.get("unlisted") == "1"
    current_terms = TermsVersion.current_number()
    # The society's mailing list, as the site last knew it (People > Mailing list).
    list_known = ListSync.objects.filter(kind=ListSync.Kind.UPDATE).exists() or ListMember.objects.exists()
    list_keys = listsync.list_keys() if list_known else set()
    list_stop = listsync.stopped_keys() if list_known else set()

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
    if only_incomplete:
        members_qs = members_qs.filter(User.incomplete_q(current_terms))
    if only_unlisted:
        unlisted = [user.pk for user in members_qs.exclude(email="").only(
                        "id", "email", "crsid", "is_active", "is_banned", "wants_mailer")
                    if listsync.gets_mailer(user) and not listsync.on_list(user.email, list_keys)]
        members_qs = members_qs.filter(pk__in=unlisted)

    page = Paginator(members_qs, 50).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    # Where each account is in sign-up: shown as a badge on the row, so an
    # admin can see at a glance why a member has no college or mobile.
    for member in page.object_list:
        member.onboarding = member.onboarding_status(current_terms)
        member.on_list = listsync.on_list(member.email, list_keys) if list_known else None
        member.gets_mailer = listsync.gets_mailer(member)
        member.list_stopped = bool(member.on_list) and listsync.mailbox_key(member.email) in list_stop

    return render(request, "panel/members.html", {
        "nav_active": "panel",
        "panel_tab": "members",
        "page": page,
        "q": q,
        "type_filter": type_filter,
        "only_admins": only_admins,
        "only_banned": only_banned,
        "only_incomplete": only_incomplete,
        "only_unlisted": only_unlisted,
        "list_known": list_known,
        "incomplete_count": User.objects.filter(is_banned=False)
        .filter(User.incomplete_q(current_terms))
        .count(),
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
            if old_email and listsync.mailbox_key(old_email) != listsync.mailbox_key(updated.email):
                listsync.queue_removal([], request.user, "a member's email address changed", addresses=[old_email])
            AuditLog.record(
                request.user, "edit_member", target=member,
                detail="details updated from the panel",
            )
            messages.success(request, f"Saved changes to {_display_name(member)}.")
            missing = [
                label for label, value in (
                    ("college", updated.college), ("mobile number", updated.mobile)
                ) if not value
            ]
            if missing:
                messages.warning(
                    request,
                    f"Saved, but {_display_name(member)} still has no "
                    f"{' or '.join(missing)}, so they count as an unfinished "
                    "sign-up.",
                )
            return redirect("panel:members")
    else:
        form = MemberEditForm(instance=member)
    return render(request, "panel/member_edit.html", {
        "nav_active": "panel",
        "panel_tab": "members",
        "member": member,
        "form": form,
        "timeline": services.member_timeline(member),
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
def member_messaging(request, pk):
    """Set who a member may message: the committee only (the default),
    anyone, or nobody (muted). Admins can always message anyone, so the
    setting is only meaningful for ordinary members."""
    member = get_object_or_404(User, pk=pk)
    level = request.POST.get("level")
    name = _display_name(member)
    if level not in User.Messaging.values:
        messages.error(request, "Unknown messaging setting — nothing was changed.")
    elif member == request.user:
        messages.error(request, "You cannot change your own messaging setting.")
    elif (
        (member.is_portal_admin or member.is_super_admin)
        and level == User.Messaging.MUTED
    ):
        messages.error(request, "Admins can't be muted — demote them first.")
    elif member.messaging == level:
        messages.info(
            request,
            f"{name} is already set to “{User.Messaging(level).label.lower()}”.",
        )
    else:
        member.messaging = level
        member.save(update_fields=["messaging"])
        action = {
            User.Messaging.ENABLED: "enable_messaging",
            User.Messaging.MUTED: "mute_messages",
            User.Messaging.DEFAULT: "reset_messaging",
        }[level]
        AuditLog.record(request.user, action, target=member)
        if level == User.Messaging.ENABLED:
            messages.success(request, f"{name} can now message any member.")
        elif level == User.Messaging.MUTED:
            messages.success(
                request, f"Muted {name} — they can read messages but not send any."
            )
        else:
            messages.success(request, f"{name} can now message the committee only.")
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
        try:
            with listsync.deleting(request.user, "an account was deleted") as queued:
                member.delete()
        except ProtectedError:
            messages.error(
                request,
                "This member has Formal Exchange history, so their account cannot be "
                "deleted here. Suspend the account and resolve any outstanding exchanges.",
            )
            return redirect("panel:members")
        AuditLog.record(request.user, "delete_user", target=target, detail=detail)
        messages.success(request, f"Deleted {target}.")
        if queued:
            messages.warning(request, "If they're on the society's mailing list, take them off there too: their "
                                      "address is waiting on People → Mailing list.")
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


@portal_admin_required
@require_POST
def member_remind(request, pk):
    """Nudge an account that never finished signing up.

    Associates who have never set a password get their invite again; anyone
    else gets a short reminder with the login link.
    """
    from accounts.services import send_associate_invite, send_profile_reminder

    member = get_object_or_404(User, pk=pk)
    name = _display_name(member)
    status = member.onboarding_status(TermsVersion.current_number())
    if status == User.Onboarding.COMPLETE:
        messages.info(request, f"{name} has already finished signing up.")
        return _redirect_back(request)
    if not member.email:
        messages.error(request, f"{name} has no email address to write to.")
        return _redirect_back(request)
    try:
        if (
            member.account_type == User.AccountType.ASSOCIATE
            and not member.has_usable_password()
        ):
            send_associate_invite(member, request)
            detail = "re-sent the set-password invite"
        else:
            send_profile_reminder(member, request)
            detail = f"reminder sent ({status.label.lower()})"
    except Exception as exc:
        messages.error(request, f"Couldn't email {name}: {exc}")
        return _redirect_back(request)
    AuditLog.record(request.user, "remind_member", target=member, detail=detail)
    messages.success(request, f"Reminder sent to {member.email}.")
    return _redirect_back(request)


# How old an unfinished account must be before the cleanup will offer to
# remove it. Long enough that a slow starter isn't deleted mid-term.
CLEANUP_AFTER_DAYS = 90


def _cleanup_candidates():
    """Accounts that never completed their profile, are older than
    CLEANUP_AFTER_DAYS, aren't admins, and have left nothing behind (no
    events, no RSVPs) — so deleting them loses nothing."""
    cutoff = timezone.now() - datetime.timedelta(days=CLEANUP_AFTER_DAYS)
    return (
        User.objects.filter(
            created_at__lt=cutoff,
            is_portal_admin=False, is_super_admin=False, is_superuser=False,
        )
        .filter(Q(first_name="") | Q(last_name="") | Q(college="") | Q(mobile=""))
        .exclude(events_created__isnull=False)
        .exclude(rsvps__isnull=False)
        .exclude(formal_bookings__isnull=False)
        .order_by("created_at")
    )


@portal_admin_required
def members_cleanup(request):
    """GET: list the unfinished accounts that would go. POST: delete them."""
    candidates = list(_cleanup_candidates())
    if request.method == "POST":
        count = len(candidates)
        names = ", ".join(u.email or u.username for u in candidates[:20])
        with listsync.deleting(request.user, "unfinished accounts were deleted") as queued:
            for user in candidates:
                user.delete()
        if queued:
            messages.warning(request, "If any of them are on the society's mailing list, take them off there too: "
                                      "their addresses are waiting on People → Mailing list.")
        AuditLog.record(
            request.user, "cleanup_incomplete",
            detail=f"deleted {count} account(s): {names}"[:300],
        )
        messages.success(
            request,
            f"Removed {count} account{'s' if count != 1 else ''} that never "
            "finished signing up.",
        )
        return redirect("panel:members")
    return render(request, "panel/members_cleanup_confirm.html", {
        "nav_active": "panel",
        "panel_tab": "members",
        "candidates": candidates,
        "days": CLEANUP_AFTER_DAYS,
    })


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
    events_qs = Event.objects.select_related("category", "created_by", "formal_listing").annotate(
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
    if event.is_formal:
        messages.info(request, "Manage this formal and its guest places in Formal Exchange.")
        return redirect(event)
    action = request.POST.get("action")

    if action == "cancel":
        from events.services import cancel_event

        event = cancel_event(request.user, event.pk)
        AuditLog.record(request.user, "cancel_event", target=event.title)
        messages.success(request, f"Cancelled “{event.title}” — it's off the calendar.")
    elif action == "restore":
        with transaction.atomic():
            event = Event.objects.select_for_update().get(pk=event.pk)
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

    config = SiteConfig.get()
    enabled = User.objects.filter(
        messaging=User.Messaging.ENABLED, is_portal_admin=False
    ).order_by("first_name", "last_name")
    muted = User.objects.filter(messaging=User.Messaging.MUTED).order_by(
        "first_name", "last_name"
    )
    return render(request, "panel/messages.html", {
        "nav_active": "panel",
        "panel_tab": "messages",
        "page": page,
        "q": q,
        "qs": params.urlencode(),
        "config": config,
        "settings_form": MessagingSettingsForm(
            initial={"messaging_mode": config.messaging_mode}
        ),
        "enabled": enabled,
        "muted": muted,
    })


@portal_admin_required
@require_POST
def messaging_settings(request):
    """Switch member-to-member messaging between open and restricted."""
    config = SiteConfig.get()
    form = MessagingSettingsForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Couldn't save the messaging setting.")
        return redirect("panel:messages")
    mode = form.cleaned_data["messaging_mode"]
    if mode == config.messaging_mode:
        messages.info(request, "Nothing changed.")
        return redirect("panel:messages")
    config.messaging_mode = mode
    config.save()
    AuditLog.record(request.user, "update_messaging_mode", detail=mode)
    if mode == SiteConfig.MessagingMode.OPEN:
        messages.success(
            request, "Messaging is now open: any member can message any other member."
        )
    else:
        messages.success(
            request,
            "Messaging is now restricted: members can message the committee, "
            "and only members you enable can message each other.",
        )
    return redirect("panel:messages")


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
def pages(request):
    """Every page on the site as a tree: each menu section, the fixed pages
    in it with the text blocks an admin can edit, and the custom pages with
    their controls (edit, rename, move, publish, delete)."""
    from core.blocks import BLOCKS, PAGES, SECTIONS, blocks_for
    from core.models import SECTION_CHOICES, TextBlock
    from core.views import CANONICAL_PAGE_SLUGS

    edited = set(TextBlock.objects.values_list("key", flat=True))
    custom = list(SitePage.objects.prefetch_related("editors").select_related("updated_by"))
    bodies = {page.slug: page for page in custom if page.slug in CANONICAL_PAGE_SLUGS}
    tree = []
    for section_key, section_title in SECTIONS:
        rows = []
        for page in PAGES:
            if page["section"] != section_key:
                continue
            rows.append({
                "kind": "fixed", "key": page["key"], "title": page["title"], "url": reverse(page["url"]),
                "note": page.get("note", ""),
                "links": [(label, reverse(name)) for label, name in page.get("links", [])],
                "blocks": [{"key": k, "label": BLOCKS[k]["label"], "edited": k in edited} for k in blocks_for(page["key"])],
                "body": bodies.get(page["key"]),
            })
        own = [p for p in custom if p.section == section_key and p.slug not in CANONICAL_PAGE_SLUGS]
        for i, page in enumerate(own):
            rows.append({"kind": "custom", "page": page, "first": i == 0, "last": i == len(own) - 1})
        tree.append({
            "key": section_key, "title": section_title, "rows": rows,
            "can_add": section_key in dict(SECTION_CHOICES),
        })
    return render(request, "panel/pages.html", {
        "nav_active": "panel",
        "panel_tab": "pages",
        "tree": tree,
        "sections": SECTION_CHOICES,
    })


@portal_admin_required
def pictures(request):
    """Pictures for the pages: upload one, copy its line into any page."""
    form = PictureForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        picture = form.save(commit=False)
        picture.uploaded_by = request.user
        picture.save()
        AuditLog.record(request.user, "upload_picture", target=picture.alt[:200])
        messages.success(request, "Picture added. Copy its line into a page to show it.")
        return redirect("panel:pictures")
    return render(request, "panel/pictures.html", {
        "nav_active": "panel",
        "panel_tab": "pictures",
        "form": form,
        "pictures": Picture.objects.select_related("uploaded_by"),
    })


@portal_admin_required
@require_POST
def picture_delete(request, pk):
    picture = get_object_or_404(Picture, pk=pk)
    alt = picture.alt
    picture.image.delete(save=False)
    picture.delete()
    AuditLog.record(request.user, "delete_picture", target=alt[:200])
    messages.success(request, f"Deleted the picture “{alt}”. Any page that used it shows a broken picture until its line is removed.")
    return redirect("panel:pictures")


@portal_admin_required
@require_POST
def page_move(request, pk):
    """Swap a page with its neighbour in the same menu section."""
    from core.views import CANONICAL_PAGE_SLUGS

    page = get_object_or_404(SitePage, pk=pk)
    siblings = list(
        SitePage.objects.filter(section=page.section).exclude(slug__in=CANONICAL_PAGE_SLUGS)
        .order_by("sort_order", "title")
    )
    i = next(n for n, p in enumerate(siblings) if p.pk == page.pk)
    j = i - 1 if request.POST.get("direction") == "up" else i + 1
    if 0 <= j < len(siblings):
        siblings[i], siblings[j] = siblings[j], siblings[i]
        for position, sibling in enumerate(siblings, start=1):
            if sibling.sort_order != position * 10:
                sibling.sort_order = position * 10
                sibling.save(update_fields=["sort_order"])
        AuditLog.record(request.user, "move_page", target=page.title, detail=request.POST.get("direction", ""))
    return redirect(reverse("panel:pages") + f"#section-{page.section}")


@portal_admin_required
@require_POST
def page_section(request, pk):
    """Move a page to another menu."""
    from core.models import SECTION_CHOICES

    page = get_object_or_404(SitePage, pk=pk)
    section = request.POST.get("section")
    if section in dict(SECTION_CHOICES) and section != page.section:
        page.section = section
        page.save(update_fields=["section", "updated_at"])
        AuditLog.record(request.user, "move_page", target=page.title, detail=f"to {section}")
        messages.success(request, f"“{page.title}” now sits under {dict(SECTION_CHOICES)[section]}.")
    return redirect(reverse("panel:pages") + f"#section-{page.section}")


@portal_admin_required
def text_edit(request, key):
    """Edit one text block on a fixed page (see core/blocks.py)."""
    from core.blocks import BLOCKS, PAGES_BY_KEY, forget_texts, render_value, value_of
    from core.models import TextBlock

    block = BLOCKS.get(key)
    if block is None:
        raise Http404("No such text.")
    page = PAGES_BY_KEY[block["page"]]
    stored = TextBlock.objects.filter(key=key).first()
    text = stored.text if stored else block["default"]
    preview = None
    if request.method == "POST":
        text = request.POST.get("text", "")
        if request.POST.get("reset"):
            TextBlock.objects.filter(key=key).delete()
            forget_texts()
            AuditLog.record(request.user, "reset_text", target=f"{page['title']}: {block['label']}", detail=key)
            messages.success(request, "The original wording is back.")
            return redirect(reverse("panel:pages") + f"#page-{page['key']}")
        if block["format"] == "plain":
            text = " ".join(text.split())
        problem = richtext_problem(text) if block["format"] == "markdown" else None
        if problem:
            messages.error(request, f"Not saved: {problem}.")
        elif request.POST.get("preview"):
            preview = render_value(block, text)
        else:
            TextBlock.objects.update_or_create(key=key, defaults={"text": text, "updated_by": request.user})
            AuditLog.record(request.user, "edit_text", target=f"{page['title']}: {block['label']}", detail=key)
            messages.success(request, "Saved. The new wording is live.")
            return redirect(reverse("panel:pages") + f"#page-{page['key']}")
    return render(request, "panel/text_form.html", {
        "nav_active": "panel", "panel_tab": "pages",
        "text_block": block, "key": key, "page": page, "page_url": reverse(page["url"]),
        "text": text, "is_default": stored is None, "preview": preview,
    })


@portal_admin_required
def navigation(request):
    """Who sees each built-in tab, and which managed pages sit in the nav."""
    config = SiteConfig.get()
    if request.method == "POST":
        tabs_form = TabVisibilityForm(request.POST, config=config)
        if tabs_form.is_valid():
            tabs_form.apply(config)
            AuditLog.record(
                request.user, "update_tab_visibility",
                detail=str(config.tab_visibility)[:250],
            )
            messages.success(request, "Navigation visibility saved.")
            return redirect("panel:navigation")
    else:
        tabs_form = TabVisibilityForm(config=config)
    return render(request, "panel/navigation.html", {
        "nav_active": "panel",
        "panel_tab": "navigation",
        "tabs_form": tabs_form,
        "nav_pages": SitePage.objects.exclude(nav_label=""),
        "banner_form": BannerForm(initial={
            "banner_text": config.banner_text,
            "banner_until": timezone.localtime(config.banner_until) if config.banner_until else None,
        }),
        "banner_live": bool(config.banner_text) and (
            config.banner_until is None or config.banner_until > timezone.now()
        ),
        "term_form": TermDatesForm(initial={
            "michaelmas_start": config.michaelmas_start,
            "lent_start": config.lent_start,
            "easter_start": config.easter_start,
        }),
    })


@portal_admin_required
def content(request):
    """Contacts: recorded department addresses and the who-to-contact map."""
    return render(request, "panel/content.html", {
        "nav_active": "panel",
        "panel_tab": "content",
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
    from core.models import SECTION_CHOICES

    initial = {}
    if request.GET.get("section") in dict(SECTION_CHOICES):
        initial["section"] = request.GET["section"]
    form = SitePageForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        page = form.save(commit=False)
        page.updated_by = request.user
        page.save()
        form.save_m2m()
        page.save_revision(request.user, SitePageRevision.Action.CREATED)
        AuditLog.record(request.user, "create_page", target=page.title)
        messages.success(
            request, f"Created “{page.title}” at /pages/{page.slug}/."
        )
        return redirect("panel:pages")
    return render(request, "panel/page_form.html", {
        "nav_active": "panel", "panel_tab": "pages",
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
        form.save_m2m()
        page.save_revision(request.user, SitePageRevision.Action.EDITED)
        from core.views import tell_page_editors
        tell_page_editors(page, request.user)
        AuditLog.record(
            request.user, "edit_page", target=page.title,
            detail="editors: " + (
                ", ".join(str(u) for u in form.cleaned_data["editors"]) or "none"
            )[:250],
        )
        messages.success(request, f"Saved “{page.title}”.")
        return redirect("panel:pages")
    return render(request, "panel/page_form.html", {
        "nav_active": "panel", "panel_tab": "pages",
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
    return redirect("panel:pages")


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
        "map_form": MapSettingsForm(),
        "config": config,
    })


@super_admin_required
@require_POST
def superadmin_maps(request):
    config = SiteConfig.get()
    form = MapSettingsForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Couldn't save the map key.")
        return redirect("panel:superadmin")
    key = form.cleaned_data["geoapify_api_key"].strip()
    if key == "CLEAR":
        config.geoapify_api_key = ""
        config.save()
        AuditLog.record(request.user, "update_map_key", detail="cleared")
        messages.success(request, "Map key removed. Posters show a directions code instead of a map.")
    elif key:
        config.geoapify_api_key = key
        config.save()
        AuditLog.record(request.user, "update_map_key", detail="set")
        messages.success(request, "Map key saved. Posters can now show a map.")
    else:
        messages.info(request, "Nothing changed.")
    return redirect("panel:superadmin")


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
            tag.events.filter(is_cancelled=False, formal_listing__isnull=True)
            .select_related("created_by", "host", "formal_listing")
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
            Event.objects.filter(is_cancelled=False, start__gte=now, formal_listing__isnull=True)
            .exclude(category__in=tags, is_official=True)
            .search(adopt_q)
            .select_related("category", "created_by", "formal_listing")
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
    if event.is_formal:
        if not event.can_edit(request.user):
            raise PermissionDenied("Only the formal host or committee may manage it.")
        return redirect(event)
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
        if tag.slug == "formal-exchange":
            raise PermissionDenied("Create formal invitations through Formal Exchange.")
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
    from . import charts

    heat_rows, heat_cols = services.events_heatmap()
    rsvps = services.rsvps_per_month(months=12)
    return render(request, "panel/stats.html", {
        "nav_active": "panel",
        "panel_tab": "stats",
        "growth_chart": charts.line_chart(services.members_growth()),
        "rsvp_chart": charts.line_chart(
            [(r["label"], r["count"]) for r in rsvps], colour="var(--brick)"
        ),
        "heatmap": charts.heatmap(heat_rows, heat_cols),
        "tag_rows": services.tag_performance(),
        "poll_rows": services.poll_turnout(),
        "scan_rows": services.poster_scans(),
        "summary": services.stats_summary(),
        "by_college": services.members_by_college(),
        "by_category": services.events_by_category(),
        "rsvps_by_month": services.rsvps_per_month(),
        "top_hosts": services.top_hosts(),
        "top_restaurants": services.top_restaurants(),
    })


# --- What's On mailer ------------------------------------------------------------------

RESEND_GUARD = datetime.timedelta(minutes=10)
STARTED = "Started; if this stays, the send was cut off part-way."
HANDLED = "marked handled by"  # an admin has dealt with a cut-off or failed send (Site health)
# Audiences that share people: a send to one blocks a send to the others for a while.
OVERLAPS = {
    "members": ("members", "old", "both"),
    "old": ("old", "old_new", "both", "members"),
    "old_new": ("old_new", "old", "both"),
    "both": ("both", "members", "old", "old_new"),
}


def _earlier_send(audience, subject, to=""):
    """A send of this mailer in the last ten minutes that may have reached
    some of the same people: finished with at least one delivery, or still
    going (or cut off). A send that reached nobody doesn't count. The
    society's mailing list holds the same people as every website audience,
    so a send to it and a send to any of them overlap."""
    listed = (SiteConfig.get().mailing_list_address or "").strip().lower()
    to_list = audience == "address" and bool(listed) and to.strip().lower() == listed
    if audience == "address" and not to_list:
        logs = MailLog.objects.filter(audience="address", recipients__iexact=to[:200])
    else:
        overlapping = Q(audience__in=OVERLAPS["both"] if to_list else OVERLAPS[audience])
        if listed:
            overlapping |= Q(audience="address", recipients__iexact=listed)
        logs = MailLog.objects.filter(overlapping)
    reached = Q(ok=True) | Q(error=STARTED) | (~Q(audience="address") & ~Q(recipients="nobody"))
    return (logs.filter(reached, subject=subject, sent_at__gte=timezone.now() - RESEND_GUARD)
            .order_by("-sent_at").first())


def _send_mailer(request, config, form):
    """Carry out "Send" or "Send a test to me". Returns a redirect once a
    real send has gone, or None to show the draft again (after a test, or
    when the send was refused)."""
    is_test = request.POST.get("action") == "test"
    audience = form.cleaned_data["audience"]
    subject = form.cleaned_data["subject"][:200]
    body = form.cleaned_data["body"]
    if is_test:
        to = request.user.email
        if not to:
            messages.error(request, "Your account has no email address to send a test to.")
            return None
        subject = f"[Test] {subject}"[:200]
        ok, error = True, ""
        try:
            services.send_test(request, config, subject, body, audience, to)
        except Exception as exc:
            ok, error = False, str(exc)[:300]
        MailLog.objects.create(subject=subject, body=body, recipients=to[:200], sent_by=request.user,
                               ok=ok, error=error, audience="test")
        AuditLog.record(request.user, "test_mailer", target=to[:200], detail=f"{'sent' if ok else 'FAILED'}: {subject}"[:300])
        if ok:
            messages.success(
                request,
                f"Test sent to {to}, as “{services.AUDIENCE_LABELS[audience]}” would get it. "
                "Check it arrived (and the spam folder) before sending for real.",
            )
        else:
            messages.error(request, f"Sending failed: {error}")
        return None

    if audience != "address":
        members, old = services.mailer_recipients(audience)
        if not members and not old:
            messages.error(request, "Nobody to send to: that audience has no addresses.")
            return None
    # Claim a send before contacting SMTP. The singleton settings row serialises
    # concurrent committee submissions, including overlapping audiences, without
    # holding a database lock while the email server is contacted.
    with transaction.atomic():
        SiteConfig.objects.select_for_update().get(pk=config.pk)
        earlier = _earlier_send(audience, subject, form.cleaned_data.get("recipient", ""))
        if not earlier:
            target = form.cleaned_data["recipient"] if audience == "address" else f"{len(members) + len(old)} addresses (sending)"
            log = MailLog.objects.create(
                subject=subject, body=body, recipients=target[:200], sent_by=request.user,
                ok=False, error=STARTED, audience=audience,
            )
    if earlier:
        when = f"{timezone.localtime(earlier.sent_at):%H:%M}"
        label = (earlier.recipients if earlier.audience == "address"
                 else services.AUDIENCE_LABELS.get(earlier.audience, earlier.audience))
        if earlier.error == STARTED:
            said = f"A send of this mailer to “{label}” started at {when} and hasn't finished, or was cut off part-way."
        elif earlier.audience == audience:
            said = f"This mailer went to that audience at {when}."
        else:
            said = f"This mailer went to “{label}” at {when}, and that would reach some of the same people again."
        if earlier.audience == "members" and audience in ("old", "both"):
            said += " To reach the old list without repeating anyone, choose “only those not yet on the website”."
        messages.error(request, f"{said} To send it anyway, wait ten minutes; Recent sends shows how it went.")
        return None
    if audience == "address":
        to = form.cleaned_data["recipient"]
        ok, error = True, ""
        try:
            services.send_one(request, config, subject, body, to)
        except Exception as exc:
            ok, error = False, str(exc)[:300]
        recipient = to
    else:
        sent, failed, error = services.send_mailer(request, config, subject, body, request.user.email, members, old)
        ok = failed == 0
        if failed:
            error = f"{failed} address{'es' if failed != 1 else ''} not sent: {error}"[:300]
        recipient = services.describe_sent(sent)
    log.recipients, log.ok, log.error = recipient[:200], ok, error
    log.save(update_fields=["recipients", "ok", "error"])
    AuditLog.record(request.user, "send_mailer", target=recipient[:200],
                    detail=f"{'sent' if ok else 'FAILED'}: {subject}"[:300])
    if ok:
        messages.success(request, f"What's On handed to the mail server for {recipient}.")
    elif audience == "address":
        messages.error(request, f"Sending failed: {error}")
    else:
        messages.error(request, f"What's On went to {recipient}, but {error}")
    return redirect("panel:mailer")


def _mailer_form(*args, **kwargs):
    """A MailerForm whose audience labels carry today's counts, and whose
    address field names the society's mailing list."""
    form = MailerForm(*args, **kwargs)
    counts = services.audience_counts()
    form.fields["audience"].choices = [
        (key, f"{label} ({counts[key]})" if key in counts else label) for key, label in services.AUDIENCES
    ]
    listed = SiteConfig.get().mailing_list_address
    if listed:
        form.fields["recipient"].help_text = f"Only for “One address”. The society's mailing list is {listed}."
    return form


@portal_admin_required
def mailer(request):
    config = SiteConfig.get()

    if request.method == "POST":
        form = _mailer_form(request.POST)
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
                        config, form.cleaned_data["subject"], form.cleaned_data["body"],
                        form.cleaned_data.get("instructions", ""),
                    )
                except ai.AIDraftError as exc:
                    messages.error(request, f"AI drafting failed: {exc}")
                else:
                    AuditLog.record(
                        request.user, "ai_draft_mailer",
                        detail=f"engine={config.email_ai_engine}"
                        + ("; with instructions" if form.cleaned_data.get("instructions") else ""),
                    )
                    messages.success(
                        request, "Draft rewritten by AI — review it before sending."
                    )
                    form = _mailer_form(initial={
                        "audience": form.cleaned_data["audience"],
                        "recipient": form.cleaned_data.get("recipient", ""),
                        "subject": form.cleaned_data["subject"],
                        "body": new_body,
                        "instructions": form.cleaned_data.get("instructions", ""),
                    })
            # fall through to render with the (re-drafted or unchanged) form
        elif form.is_valid():
            response = _send_mailer(request, config, form)
            if response:
                return response
            # nothing sent for real (a test, or a refusal): keep the draft on screen
            form = _mailer_form(initial={
                key: form.cleaned_data.get(key, "")
                for key in ("audience", "recipient", "subject", "body", "instructions")
            })
    else:
        subject, body = services.build_whats_on_email(request)
        form = _mailer_form(initial={
            "audience": "members",
            "recipient": config.mailing_list_address,
            "subject": subject,
            "body": body,
        })

    return render(request, "panel/mailer.html", {
        "nav_active": "panel",
        "panel_tab": "mailer",
        "form": form,
        "download_audiences": [(key, label) for key, label in form.fields["audience"].choices
                               if key in services.LIST_AUDIENCES]
                              + [(services.STOPPED, f"{services.STOPPED_LABEL} ({len(services.stopped_rows())})")],
        "list_pages": {
            "members": mailinglist.list_url(config.mailing_list_address, "admin", "members/list"),
            "held": mailinglist.list_url(config.mailing_list_address, "admindb"),
        },
        "email_live": "smtp" in settings.EMAIL_BACKEND,
        "email_from": settings.DEFAULT_FROM_EMAIL,
        "email_from_ok": settings.DEFAULT_FROM_EMAIL.rstrip("> ").lower().endswith("@srcf.net"),
        "audience_count": services.mailer_audience().count(),
        "batch_size": services.MAILER_BATCH,
        "event_count": services.whats_on_events().count(),
        "recent_logs": MailLog.objects.select_related("sent_by")[:5],
    })


@portal_admin_required
@require_POST
def mailer_download(request):
    """Everyone on the mailing list, for a spreadsheet or for copying into
    the society's Mailman list: the addresses the mailer reaches for the
    chosen audience, each inbox once, with opted-out, banned and
    unsubscribed people left out. Every download is in the audit log."""
    import csv

    from core.csvsafe import cell

    audience = request.POST.get("audience", "")
    if audience not in services.DOWNLOADS:
        messages.error(request, "Choose who to download first.")
        return redirect(reverse("panel:mailer") + "#download")
    as_text = request.POST.get("format") == "text"
    if audience == services.STOPPED:
        rows, label = services.stopped_rows(), services.STOPPED_LABEL
    else:
        rows, label = services.mailing_list_rows(audience), services.AUDIENCE_LABELS[audience]
    name = f"{SiteConfig.get().short_name or 'society'}-mailing-list-{audience}-{timezone.localdate():%Y-%m-%d}"
    name = "".join(char if char.isalnum() or char in "-_" else "-" for char in name).lower()
    if as_text:
        # One address per line: what Mailman's mass subscription box takes.
        response = HttpResponse("".join(row["email"] + "\n" for row in rows),
                                content_type="text/plain; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{name}.txt"'
    else:
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{name}.csv"'
        response.write("\ufeff")  # so Excel reads accented names as UTF-8
        writer = csv.writer(response)
        writer.writerow(["email", "first name", "last name",
                         "why" if audience == services.STOPPED else "on the list as"])
        for row in rows:
            writer.writerow([cell(row["email"]), cell(row["first_name"]), cell(row["last_name"]), row["source"]])
    response["Cache-Control"] = "no-store, private"
    AuditLog.record(request.user, "download_mailing_list", target=label,
                    detail=f"{len(rows)} address{'es' if len(rows) != 1 else ''}, {'text' if as_text else 'CSV'}")
    return response


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


# --- testimonials -------------------------------------------------------------------

@portal_admin_required
def testimonials(request):
    """Review members' testimonials before they appear on the public page.

    Anonymous ones are anonymous to the public, not to the committee: the
    queue always shows who wrote what.
    """
    from testimonials.models import Testimonial

    status = request.GET.get("status", Testimonial.Status.PENDING)
    if status not in Testimonial.Status.values:
        status = Testimonial.Status.PENDING
    queue = Testimonial.objects.filter(status=status).select_related(
        "author", "reviewed_by"
    )
    if status == Testimonial.Status.PENDING:
        queue = queue.order_by("submitted_at")
    else:
        queue = queue.order_by("-is_featured", "-reviewed_at")
    counts = {
        row["status"]: row["count"]
        for row in Testimonial.objects.values("status").annotate(count=Count("id"))
    }
    return render(request, "panel/testimonials.html", {
        "nav_active": "panel",
        "panel_tab": "testimonials",
        "status": status,
        "statuses": Testimonial.Status.choices,
        "testimonials": queue,
        "counts": counts,
    })


@portal_admin_required
@require_POST
def testimonial_action(request, pk):
    from testimonials.models import Testimonial

    testimonial = get_object_or_404(Testimonial, pk=pk)
    action = request.POST.get("action")
    note = request.POST.get("review_note", "").strip()[:200]
    who = testimonial.author_name or "a former member"
    excerpt = testimonial.body[:80]

    from notifications.models import Notification
    from notifications.services import notify

    if action == "approve":
        testimonial.review(request.user, Testimonial.Status.APPROVED, note)
        AuditLog.record(request.user, "approve_testimonial", target=who, detail=excerpt)
        notify(
            [testimonial.author], Notification.Kind.TESTIMONIAL,
            "Your testimonial has been published. Thank you!",
            reverse("testimonials:index"),
        )
        messages.success(request, f"Published {who}'s testimonial.")
    elif action == "reject":
        testimonial.review(request.user, Testimonial.Status.REJECTED, note)
        AuditLog.record(
            request.user, "reject_testimonial", target=who, detail=note or excerpt
        )
        notify(
            [testimonial.author], Notification.Kind.TESTIMONIAL,
            "Your testimonial wasn't published." + (f" Note: {note}" if note else ""),
            reverse("testimonials:index"),
        )
        messages.info(request, f"{who}'s testimonial is not published.")
    elif action in ("feature", "unfeature"):
        if testimonial.status != Testimonial.Status.APPROVED:
            messages.error(request, "Only published testimonials can be featured.")
        else:
            testimonial.is_featured = action == "feature"
            testimonial.save(update_fields=["is_featured"])
            AuditLog.record(request.user, f"{action}_testimonial", target=who)
            messages.success(
                request,
                f"{who}'s testimonial is {'now' if testimonial.is_featured else 'no longer'} featured.",
            )
    elif action == "delete":
        testimonial.delete()
        AuditLog.record(request.user, "delete_testimonial", target=who, detail=excerpt)
        messages.success(request, f"Deleted {who}'s testimonial.")
    else:
        messages.error(request, "Unknown action — nothing was changed.")
    return _redirect_back(request, fallback="panel:testimonials")


# --- polls ------------------------------------------------------------------------

@portal_admin_required
def polls(request):
    """Every poll on the site, open ones first, with where each one stands."""
    from polls.models import Poll

    all_polls = list(
        Poll.objects.select_related("event", "created_by", "outcome_option")
        .annotate(vote_count=Count("votes"))
        .order_by("status", "closes_at")[:200]
    )
    for poll in all_polls:
        poll.resolve_if_due()
    return render(request, "panel/polls.html", {
        "nav_active": "panel",
        "panel_tab": "polls",
        "polls": all_polls,
    })


@portal_admin_required
def surveys(request):
    """Every survey on the site, who runs it and how many have answered."""
    from surveys.models import Survey

    rows = list(
        Survey.objects.prefetch_related("admins")
        .annotate(answer_count=Count("participations", distinct=True))
        .order_by("status", "-created_at")[:200]
    )
    for survey in rows:
        survey.resolve_if_due()
    return render(request, "panel/surveys.html", {
        "nav_active": "panel",
        "panel_tab": "surveys",
        "surveys": rows,
    })


# --- announcement banner, committee and "what we do" --------------------------------

@portal_admin_required
@require_POST
def banner(request):
    config = SiteConfig.get()
    form = BannerForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Couldn't save the banner — check the date.")
        return redirect("panel:navigation")
    config.banner_text = form.cleaned_data["banner_text"].strip()
    config.banner_until = form.cleaned_data["banner_until"]
    config.save()
    AuditLog.record(
        request.user, "update_banner",
        detail=(config.banner_text[:120] or "cleared"),
    )
    messages.success(
        request, "Banner saved." if config.banner_text else "Banner cleared."
    )
    return redirect("panel:navigation")


@portal_admin_required
def about_content(request):
    return render(request, "panel/about.html", {
        "nav_active": "panel",
        "panel_tab": "about",
        "committee": CommitteeMember.objects.all(),
        "activities": Activity.objects.all(),
    })


def _simple_edit(request, model, form_class, pk, label, audit):
    obj = get_object_or_404(model, pk=pk) if pk else None
    form = form_class(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        AuditLog.record(request.user, audit, target=str(obj))
        messages.success(request, f"Saved {obj}.")
        return redirect("panel:about")
    return render(request, "panel/simple_form.html", {
        "nav_active": "panel", "panel_tab": "about",
        "form": form, "obj": obj, "label": label,
        "back": reverse("panel:about"),
    })


@portal_admin_required
def committee_edit(request, pk=None):
    return _simple_edit(
        request, CommitteeMember, CommitteeMemberForm, pk, "committee member", "edit_committee"
    )


@portal_admin_required
@require_POST
def committee_delete(request, pk):
    member = get_object_or_404(CommitteeMember, pk=pk)
    AuditLog.record(request.user, "delete_committee", target=str(member))
    member.delete()
    messages.success(request, "Removed from the committee list.")
    return redirect("panel:about")


@portal_admin_required
def activity_edit(request, pk=None):
    return _simple_edit(request, Activity, ActivityForm, pk, "activity", "edit_activity")


@portal_admin_required
@require_POST
def activity_delete(request, pk):
    activity = get_object_or_404(Activity, pk=pk)
    AuditLog.record(request.user, "delete_activity", target=str(activity))
    activity.delete()
    messages.success(request, "Removed the activity.")
    return redirect("panel:about")


# --- bulk member actions --------------------------------------------------------------

def _remind_member(request, member):
    """Email a nudge to an unfinished account. Returns what was sent, or
    None if there was nothing to do."""
    from accounts.services import send_associate_invite, send_profile_reminder

    status = member.onboarding_status(TermsVersion.current_number())
    if status == User.Onboarding.COMPLETE or not member.email:
        return None
    if (
        member.account_type == User.AccountType.ASSOCIATE
        and not member.has_usable_password()
    ):
        send_associate_invite(member, request)
        return "re-sent the set-password invite"
    send_profile_reminder(member, request)
    return f"reminder sent ({status.label.lower()})"


@portal_admin_required
@require_POST
def members_bulk(request):
    """Apply one action to the ticked members: remind, export, or ban (with
    a confirmation step)."""
    ids = [i for i in request.POST.getlist("ids") if i.isdigit()]
    action = request.POST.get("action")
    members = list(User.objects.filter(pk__in=ids).order_by("first_name", "last_name"))
    if not members:
        messages.error(request, "Tick at least one member first.")
        return _redirect_back(request)

    if action == "export":
        import csv

        from core.csvsafe import cell

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="members.csv"'
        writer = csv.writer(response)
        writer.writerow(["Name", "Email", "College", "Mobile", "Type", "Joined", "Sign-up"])
        current = TermsVersion.current_number()
        for m in members:
            writer.writerow([
                cell(m.get_full_name() or m.username), cell(m.email),
                m.get_college_display() if m.college else "", cell(m.mobile),
                m.get_account_type_display(), m.created_at.strftime("%Y-%m-%d"),
                m.onboarding_status(current).label,
            ])
        AuditLog.record(request.user, "export_members", detail=f"{len(members)} member(s)")
        return response

    if action == "remind":
        sent = 0
        for m in members:
            detail = _remind_member(request, m)
            if detail:
                sent += 1
                AuditLog.record(request.user, "remind_member", target=m, detail=detail)
        messages.success(request, f"Sent {sent} reminder{'s' if sent != 1 else ''}.")
        return _redirect_back(request)

    if action == "ban":
        eligible = [
            m for m in members
            if m != request.user and not (m.is_portal_admin or m.is_super_admin)
            and not m.is_banned
        ]
        if request.POST.get("confirm") == "1":
            for m in eligible:
                m.ban(request.user)
                AuditLog.record(request.user, "ban", target=m, detail="bulk")
            messages.success(request, f"Banned {len(eligible)} member{'s' if len(eligible) != 1 else ''}.")
            return redirect("panel:members")
        return render(request, "panel/members_bulk_confirm.html", {
            "nav_active": "panel", "panel_tab": "members",
            "members": eligible, "skipped": len(members) - len(eligible),
        })

    messages.error(request, "Pick an action first.")
    return _redirect_back(request)


@portal_admin_required
@require_POST
def page_toggle(request, pk):
    """Quick changes from the Pages table: publish/unpublish, or audience."""
    page = get_object_or_404(SitePage, pk=pk)
    if "nav_visibility" in request.POST:
        value = request.POST["nav_visibility"]
        if value not in dict(VISIBILITY_CHOICES):
            messages.error(request, "Unknown audience.")
            return redirect("panel:pages")
        page.nav_visibility = value
        page.save(update_fields=["nav_visibility", "updated_at"])
        AuditLog.record(request.user, "edit_page", target=page.title, detail=f"audience: {value}")
        messages.success(request, f"“{page.title}” is now {page.get_nav_visibility_display().lower()}.")
    else:
        page.is_published = not page.is_published
        page.save(update_fields=["is_published", "updated_at"])
        AuditLog.record(
            request.user, "publish_page" if page.is_published else "unpublish_page",
            target=page.title,
        )
        messages.success(
            request, f"“{page.title}” is {'published' if page.is_published else 'unpublished'}."
        )
    return redirect("panel:pages")


@portal_admin_required
def terms_diff(request, pk):
    """What one save of the terms changed, against the save before it."""
    from core.diff import line_diff, summary

    newer = get_object_or_404(TermsRevision.objects.select_related("editor"), pk=pk)
    older = (
        TermsRevision.objects.filter(
            version_number=newer.version_number, created_at__lt=newer.created_at
        ).order_by("-created_at").first()
    )
    rows = line_diff(older.content if older else "", newer.content)
    return render(request, "panel/terms_diff.html", {
        "nav_active": "panel", "panel_tab": "terms",
        "older": older or newer, "newer": newer,
        "rows": rows, "diff_summary": summary(rows),
    })


@portal_admin_required
@require_POST
def term_dates(request):
    config = SiteConfig.get()
    form = TermDatesForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Couldn't save the term dates — check the dates.")
        return redirect("panel:navigation")
    for field in ("michaelmas_start", "lent_start", "easter_start"):
        setattr(config, field, form.cleaned_data[field])
    config.save()
    AuditLog.record(request.user, "update_term_dates")
    messages.success(request, "Term dates saved. The calendar now labels term weeks.")
    return redirect("panel:navigation")


# --- the old mailing list -----------------------------------------------------------------

@portal_admin_required
def old_list(request):
    """The mailing list from before the website: import a spreadsheet, see
    who has moved over, download the rest."""
    export = request.method == "POST" and request.POST.get("export") == "csv"
    form = OldListUploadForm(None if export else request.POST or None, None if export else request.FILES or None)
    if request.method == "POST" and not export and form.is_valid():
        upload = form.cleaned_data["sheet"]
        try:
            entries, stats = sheets.parse_old_list(sheets.read_rows(upload))
        except sheets.SheetError as exc:
            messages.error(request, str(exc))
            return redirect("panel:old_list")
        if not entries:
            messages.error(request, "No email addresses found in that file, so nothing was imported.")
            return redirect("panel:old_list")
        added = filled = 0
        for entry in entries:
            sub, created = OldSubscriber.objects.get_or_create(
                email=entry["email"],
                defaults={**{k: v for k, v in entry.items() if k != "email"}, "added_by": request.user},
            )
            if created:
                added += 1
                continue
            changed = False
            for field in ("first_name", "last_name", "college", "notes"):
                if entry[field] and not getattr(sub, field):
                    setattr(sub, field, entry[field])
                    changed = True
            if changed:
                sub.save()
                filled += 1
        _archive_old_list(upload, len(entries), request.user)
        AuditLog.record(
            request.user, "import_old_list", target=upload.name[:200],
            detail=(f"added {added}, filled in {filled}, skipped {stats['no_address']} + {stats['invalid']} invalid, "
                    f"merged {stats['duplicates']}")[:300],
        )
        invalid = (f", {stats['invalid']} with an address that isn't valid" if stats["invalid"] else "")
        messages.success(
            request,
            f"Added {added}, filled in {filled}, skipped {stats['no_address']} "
            f"row{'s' if stats['no_address'] != 1 else ''} without an address{invalid}; "
            f"{stats['duplicates']} address{'es' if stats['duplicates'] != 1 else ''} repeated in the file merged.",
        )
        return redirect("panel:old_list")

    filters = request.POST if export else request.GET
    status = filters.get("status", "")
    if status not in ("unsubscribed", "moved", "waiting"):
        status = ""  # an unknown filter shows (and exports) everything, so say so
    q = filters.get("q", "").strip()[:200]
    subs = services.old_list_status(list(OldSubscriber.objects.all()))
    counts = {
        "all": len(subs),
        "unsubscribed": sum(1 for s in subs if s.unsubscribed_at),
        "moved": sum(1 for s in subs if s.member and not s.unsubscribed_at),
    }
    counts["waiting"] = counts["all"] - counts["unsubscribed"] - counts["moved"]
    if status == "unsubscribed":
        subs = [s for s in subs if s.unsubscribed_at]
    elif status == "moved":
        subs = [s for s in subs if s.member and not s.unsubscribed_at]
    elif status == "waiting":
        subs = [s for s in subs if not s.member and not s.unsubscribed_at]
    if q:
        needle = q.lower()
        subs = [s for s in subs if needle in f"{s.name} {s.email} {s.college} {s.notes}".lower()]
    if export:
        import csv

        from core.csvsafe import cell

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="old-mailing-list.csv"'
        response["Cache-Control"] = "no-store, private"
        writer = csv.writer(response)
        writer.writerow(["email", "first name", "last name", "college", "status"])
        for s in subs:
            if s.unsubscribed_at:
                state = "unsubscribed"
            elif s.member and s.member.is_banned:
                state = "banned member"
            elif s.member and not s.member.wants_mailer:
                state = "on the website, opted out"
            else:
                state = "on the website" if s.member else "not yet"
            writer.writerow([cell(s.email), cell(s.first_name), cell(s.last_name), cell(s.college), state])
        AuditLog.record(request.user, "export_old_list", detail=f"{len(subs)} row{'s' if len(subs) != 1 else ''}"
                        + (f", {status}" if status else "") + (", searched" if q else ""))
        return response
    page = Paginator(subs, 100).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "panel/old_list.html", {
        "nav_active": "panel", "panel_tab": "old_list",
        "form": form, "page": page, "counts": counts, "status": status, "q": q,
        "qs": params.urlencode(),
        "archive": OldListFile.objects.select_related("uploaded_by"),
    })


def _archive_old_list(upload, addresses, user):
    """Keep the spreadsheet as it was uploaded. Never in the way of the import."""
    import logging
    import os

    from .models import OldListFile

    try:
        upload.seek(0)
        record = OldListFile(name=os.path.basename(upload.name)[:255], size=upload.size or 0, addresses=addresses,
                             uploaded_by=user)
        record.file.save(upload.name, upload, save=False)
        record.save()
    except Exception:
        logging.getLogger(__name__).warning("Could not keep a copy of the old list's spreadsheet", exc_info=True)


@portal_admin_required
@require_POST
def old_list_file(request, pk):
    """Download an archived spreadsheet of the old list, or delete it."""
    from django.http import FileResponse

    from .models import OldListFile

    record = get_object_or_404(OldListFile, pk=pk)
    if request.POST.get("action") == "delete":
        record.file.delete(save=False)
        record.delete()
        AuditLog.record(request.user, "delete_old_list_file", target=record.name[:200],
                        detail=f"uploaded {timezone.localtime(record.uploaded_at):%d %b %Y}")
        messages.success(request, f"Deleted {record.name} from the archive.")
        return redirect(reverse("panel:old_list") + "#archive")
    try:
        handle = record.file.open("rb")
    except (FileNotFoundError, OSError):
        messages.error(request, f"{record.name} is no longer on the server.")
        return redirect(reverse("panel:old_list") + "#archive")
    AuditLog.record(request.user, "download_old_list_file", target=record.name[:200],
                    detail=f"uploaded {timezone.localtime(record.uploaded_at):%d %b %Y}, {record.addresses} addresses")
    response = FileResponse(handle, as_attachment=True, filename=record.name)
    response["Cache-Control"] = "no-store, private"
    return response


@portal_admin_required
@require_POST
def old_list_action(request, pk):
    sub = get_object_or_404(OldSubscriber, pk=pk)
    action = request.POST.get("action")
    # a Cambridge address and its @hermes twin are one mailbox: change both
    twins = OldSubscriber.objects.filter(
        email__in=[f"{sub.crsid}@{domain}" for domain in OldSubscriber.CRSID_DOMAINS] if sub.crsid else [sub.email])
    if action == "unsubscribe":
        twins.filter(unsubscribed_at__isnull=True).update(unsubscribed_at=timezone.now())
    elif action == "resubscribe":
        twins.update(unsubscribed_at=None)
    elif action == "remove":
        if sub.unsubscribed_at:
            messages.error(request, "Unsubscribed addresses are kept, so a re-import can't put them back on the list.")
            return _redirect_back(request, "panel:old_list")
        sub.delete()
    else:
        raise Http404
    AuditLog.record(request.user, f"old_list_{action}", target=sub.email[:200])
    return _redirect_back(request, "panel:old_list")


# --- site health ---------------------------------------------------------------------------

@portal_admin_required
def health(request):
    """Is the website working? Quick checks run now; the network and disk
    checks show their latest run (press "Run all checks" to repeat them)."""
    from .healthchecks import page_context

    return render(request, "panel/health.html", {
        "nav_active": "panel",
        "panel_tab": "health",
        **page_context(request),
    })


@portal_admin_required
@require_POST
def health_run(request):
    from .healthchecks import LABELS, run_locked

    snapshot = run_locked(request)
    if snapshot is None:
        messages.info(request, "The checks ran less than a minute ago, so these are their results.")
        return redirect("panel:health")
    AuditLog.record(request.user, "run_health_check", detail=f"{snapshot.overall}, {snapshot.duration_ms} ms")
    text = f"Checked in {snapshot.duration_ms / 1000:.1f} s: {LABELS.get(snapshot.overall, snapshot.overall).lower()}."
    if snapshot.overall == "fail":
        messages.error(request, text)
    elif snapshot.overall == "warn":
        messages.warning(request, text)
    else:
        messages.success(request, text)
    return redirect("panel:health")


@super_admin_required
@require_POST
def health_error_resolve(request, pk):
    error = get_object_or_404(ErrorEvent, pk=pk)
    error.resolved_at, error.resolved_by, error.reopened = timezone.now(), request.user, False
    error.save(update_fields=["resolved_at", "resolved_by", "reopened"])
    AuditLog.record(request.user, "resolve_error", target=(error.view_name or error.route)[:200],
                    detail=f"{error.exc_type} ×{error.count}"[:300])
    _quick_recheck(request)
    messages.success(request, "Marked fixed. If it happens again it comes back, marked “came back”.")
    return redirect(reverse("panel:health") + "#errors")


@portal_admin_required
@require_POST
def health_mail_handled(request, pk):
    log = get_object_or_404(MailLog.objects.exclude(error__contains=HANDLED), pk=pk, ok=False)
    note = f"{HANDLED} {_display_name(request.user)} on {timezone.localtime():%d %b %Y}"
    if log.error == STARTED:
        log.error = f"Cut off part-way; {note}."[:300]
    else:
        log.error = f"{log.error[:300 - len(note) - 3]} ({note})"
    log.save(update_fields=["error"])
    AuditLog.record(request.user, "mailer_cut_off_handled", target=log.subject[:200])
    _quick_recheck(request)
    messages.success(request, "Marked as handled.")
    return redirect("panel:health")


def _quick_recheck(request):
    """Re-run the quick checks after an admin deals with something, so the
    Overview tab and the dashboard follow at once. Never in the way."""
    from .healthchecks import run

    try:
        run(request, full=False, trigger="button", user=request.user)
    except Exception:
        pass


# --- the society's mailing list: which accounts are on it -----------------------------------

LIST_FILTERS = (
    ("missing", "Want the mailer, not on the list"),
    ("stopped", "Stopped the mailer, still on the list"),
    ("on", "On the list"),
    ("all", "Everyone with an address"),
)
LIST_STALE = datetime.timedelta(days=31)
LIST_SHRINK = 0.8  # a paste this much smaller than the copy is probably one page of several


def _sender_address():
    from email.utils import parseaddr

    return parseaddr(settings.DEFAULT_FROM_EMAIL)[1].lower()


def _how_to_copy(address):
    from django.utils.html import format_html

    roster = mailinglist.list_url(address, "roster")
    if not roster:
        return "open the list's page of members, select the whole page and copy it."
    return format_html(
        "sign in to the list's <a href=\"{}\" rel=\"noopener\">admin pages</a>, then open its "
        "<a href=\"{}\" rel=\"noopener\">subscriber list</a>, which has everyone on one page (members who chose "
        "to hide as well), select the whole page and copy it. The Membership List works too, once “Maximum number "
        "of members to show on one page” on the list's <a href=\"{}\" rel=\"noopener\">General options</a> is "
        "more than the list has; until then it shows a page at a time.",
        mailinglist.list_url(address, "admin"), roster, mailinglist.list_url(address, "admin", "general"))


def _mailing_list_page(request, form=None, shrink=None):
    config = SiteConfig.get()
    keys = listsync.list_keys()
    reached = listsync.reached_keys()
    stop = listsync.stopped_keys(reached)
    rows = listsync.account_rows(keys, stop)
    owed_batches, owed_addresses = listsync.owed(reached)
    removing = sorted(set(listsync.to_remove(stop)) | set(owed_addresses))
    show = request.GET.get("show", "missing")
    if show not in dict(LIST_FILTERS):
        show = "missing"
    q = request.GET.get("q", "").strip()[:200]
    shown = {
        "missing": [row for row in rows if row["wants"] and not row["on_list"]],
        "stopped": [row for row in rows if row["stopped"]],
        "on": [row for row in rows if row["on_list"]],
        "all": rows,
    }[show]
    if q:
        needle = q.lower()
        shown = [row for row in shown
                 if needle in f"{row['user'].get_full_name()} {row['user'].username} {row['user'].email}".lower()]
    page = Paginator(shown, 100).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    # the last paste of the whole list; a part pasted since only adds to it
    updates = ListSync.objects.filter(kind=ListSync.Kind.UPDATE).select_related("created_by")
    last_update = updates.filter(note="").first()
    last_part = updates.exclude(note="").first()
    if last_part and last_update and last_part.created_at < last_update.created_at:
        last_part = None
    since_update = ""
    if last_update:
        # what the copy has gained and lost since, from this page
        added = ListMember.objects.filter(added_at__gt=last_update.created_at).count()
        removed = max(0, last_update.count + added - ListMember.objects.count())
        since_update = " and ".join(part for part in (
            f"{added} added" if added else "", f"{removed} taken off" if removed else "") if part)
    # Addresses that must come off because accounts went or moved wait until a
    # take-off file with them in is done; a download waits a fortnight.
    owed = None
    if owed_addresses:
        owed = {"addresses": owed_addresses, "batches": owed_batches[:8], "more": max(0, len(owed_batches) - 8)}
    # the download each card waits on to be marked done (a new one replaces it)
    waiting = {kind: ListSync.objects.filter(kind=kind, note="", done_at__isnull=True)
               .select_related("created_by").first() for kind in (ListSync.Kind.ADD, ListSync.Kind.REMOVE)}
    return render(request, "panel/mailing_list.html", {
        "nav_active": "panel",
        "panel_tab": "mailing_list",
        "list_address": config.mailing_list_address,
        "pages": {
            name: mailinglist.list_url(config.mailing_list_address, view, page)
            for name, view, page in (
                ("subscribe", "admin", "members/add"), ("remove", "admin", "members/remove"),
                ("members", "admin", "members/list"), ("held", "admindb", ""),
                ("roster_privacy", "admin", "privacy/subscribing"), ("senders", "admin", "privacy/sender"),
                ("archive", "admin", "archive"), ("general", "admin", "general"),
                ("footer", "admin", "nondigest"),
            )
        },
        "public_archive": (f"https://lists.srcf.net/pipermail/{config.mailing_list_address.split('@')[0]}/"
                           if mailinglist.list_url(config.mailing_list_address) else ""),
        "leave_address": mailinglist.leave_address(config.mailing_list_address),
        "contact_email": config.contact_email,
        "sender_address": _sender_address(),
        "own_on_list": [address for address in {config.contact_email, _sender_address()} if address
                        and listsync.mailbox_key(address) in keys],
        "how_to_copy": _how_to_copy(config.mailing_list_address),
        "last_update": last_update,
        "last_part": last_part,
        "update_old": bool(last_update) and timezone.now() - last_update.created_at > LIST_STALE,
        "since_update": since_update,
        "owed": owed,
        "waiting": {"add": waiting[ListSync.Kind.ADD], "remove": waiting[ListSync.Kind.REMOVE]},
        "old_list_url": reverse("panel:old_list"),
        "counts": listsync.summary(rows, keys, removing),
        "filters": LIST_FILTERS,
        "show": show,
        "q": q,
        "page": page,
        "qs": params.urlencode(),
        "form": form or ListPasteForm(),
        "shrink": shrink,
    })


@portal_admin_required
def mailing_list(request):
    """Which website accounts are on the society's mailing list (as the
    site last knew it), filtered, with the downloads to add the rest or take
    off those who stopped the mailer."""
    return _mailing_list_page(request)


@portal_admin_required
@require_POST
def mailing_list_update(request):
    """Replace (or add to) the site's copy of the list with addresses pasted
    from Mailman's pages."""
    from django.db import transaction

    config = SiteConfig.get()
    form = ListPasteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Paste the list's members into the box first.")
        return redirect(reverse("panel:mailing_list") + "#update")
    addresses = listsync.addresses_in(form.cleaned_data["members"], config.mailing_list_address)
    if not addresses:
        messages.error(request, "No email addresses found in what you pasted, so nothing changed.")
        return redirect(reverse("panel:mailing_list") + "#update")
    partial = form.cleaned_data["partial"]
    counted = listsync.mailman_counts(form.cleaned_data["members"])
    if counted.get("shown") and not partial:
        messages.error(
            request,
            f"That's one page of the Membership List ({counted['shown']} of its {counted['total']} members), so "
            "nothing changed. Copy the subscriber list instead, which has everyone on one page, or tick “This is "
            "only part of the list” to add just these.",
        )
        return redirect(reverse("panel:mailing_list") + "#update")
    current = ListMember.objects.count()
    digest = listsync.paste_digest(addresses)
    if not partial and current and len(addresses) < current * LIST_SHRINK and form.cleaned_data["confirm"] != digest:
        form = ListPasteForm(initial={"members": form.cleaned_data["members"], "confirm": digest})
        messages.warning(
            request,
            f"That paste has {len(addresses)} address{'es' if len(addresses) != 1 else ''}, but the site's copy has "
            f"{current}. If you copied only one page of the membership list, tick “This is only part of the list” "
            "and paste again. If the list really has shrunk, press Update again to replace the copy.",
        )
        return _mailing_list_page(request, form=form, shrink=len(addresses))
    now = timezone.now()
    pasted = {address: listsync.mailbox_key(address) for address in addresses}
    with transaction.atomic():
        existing = set(ListMember.objects.values_list("email", flat=True))
        new = [address for address in pasted if address not in existing]
        ListMember.objects.bulk_create([
            ListMember(email=address, key=pasted[address], source=ListMember.Source.LIST, added_at=now)
            for address in new
        ], ignore_conflicts=True)  # another request may have just added one
        ListMember.objects.filter(email__in=list(pasted)).update(source=ListMember.Source.LIST)
        gone = 0
        if not partial:
            gone_ids = [pk for pk, email in ListMember.objects.values_list("pk", "email") if email not in pasted]
            gone = len(gone_ids)
            for start in range(0, len(gone_ids), 500):
                ListMember.objects.filter(pk__in=gone_ids[start:start + 500]).delete()
        total = ListMember.objects.count()
        if not partial:
            # the whole list, just pasted, says who is on it: unmarked downloads are past
            ListSync.objects.filter(kind__in=[ListSync.Kind.ADD, ListSync.Kind.REMOVE], note="",
                                    done_at__isnull=True).delete()
        ListSync.objects.create(kind=ListSync.Kind.UPDATE, created_by=request.user, created_at=now, count=total,
                                note="part of the list" if partial else "")
    AuditLog.record(request.user, "update_mailing_list", target=config.mailing_list_address,
                    detail=f"{len(addresses)} pasted{' (part of the list)' if partial else ''}: {len(new)} new, "
                           f"{gone} no longer on it, {total} in all")
    messages.success(request, f"The site's copy of the list has {total} address{'es' if total != 1 else ''}: "
                              f"{len(new)} new{'' if partial else f', {gone} no longer on it'}.")
    expected = counted.get("shown") or counted.get("total")
    if counted.get("hidden"):
        hidden = counted["hidden"]
        messages.warning(
            request,
            f"Mailman's subscriber list left out {hidden} member{'s' if hidden != 1 else ''} who chose to hide, "
            "because it took you for a member, not the list's admin (you may be signed in to your own subscription "
            "in this browser). They show here as not on the list. Copy it again from a private window, signed in "
            "only to the list's admin pages.",
        )
    elif expected and len(addresses) < expected:
        messages.warning(
            request,
            f"Mailman's page counted {expected} members, but what you pasted had {len(addresses)} "
            f"address{'es' if len(addresses) != 1 else ''}. If you didn't copy the whole page, copy it again.",
        )
    return redirect("panel:mailing_list")


@portal_admin_required
@require_POST
def mailing_list_download(request):
    """The addresses to add to the list, or to take off it, one per line for
    Mailman's Mass Subscription or Mass Removal box. Each download is kept,
    so an admin can say afterwards that it was done."""
    kind = request.POST.get("kind", "")
    if kind not in (ListSync.Kind.ADD, ListSync.Kind.REMOVE):
        messages.error(request, "Choose what to download.")
        return redirect("panel:mailing_list")
    keys = listsync.list_keys()
    if kind == ListSync.Kind.ADD:
        addresses = listsync.to_add(listsync.account_rows(keys, stop=set()), keys,
                                    old_list=request.POST.get("old_list") == "1")
    else:
        reached = listsync.reached_keys()
        addresses = sorted(set(listsync.to_remove(listsync.stopped_keys(reached))) | set(listsync.owed(reached)[1]))
    if not addresses:
        messages.info(request, "There's nobody to " + ("add." if kind == ListSync.Kind.ADD else "take off."))
        return redirect("panel:mailing_list")
    config = SiteConfig.get()
    # it replaces an unmarked older download it covers (one that also had the
    # old list's people, say, stays until marked or forgotten)
    covering = set(addresses)
    for older in ListSync.objects.filter(kind=kind, note="", done_at__isnull=True):
        if set(older.addresses) <= covering:
            older.delete()
    ListSync.objects.create(kind=kind, created_by=request.user, count=len(addresses), addresses=addresses)
    AuditLog.record(request.user, f"download_list_{kind}", target=config.mailing_list_address,
                    detail=f"{len(addresses)} address{'es' if len(addresses) != 1 else ''}"
                           + (", old list included" if request.POST.get("old_list") == "1" else ""))
    return _address_file(addresses, "to-add" if kind == ListSync.Kind.ADD else "to-take-off", config)


def _address_file(addresses, what, config):
    """Addresses one per line, as a file for Mailman's Mass Subscription or
    Mass Removal page, which no browser or proxy keeps."""
    name = f"{config.short_name or 'society'}-mailing-list-{what}-{timezone.localdate():%Y-%m-%d}"
    name = "".join(char if char.isascii() and char.isalnum() or char in "-_" else "-" for char in name).lower()
    response = HttpResponse("".join(address + "\n" for address in addresses),
                            content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}.txt"'
    response["Cache-Control"] = "no-store, private"
    return response


@portal_admin_required
@require_POST
def mailing_list_forget(request, pk):
    """A download that was never used: forget it, so it stops waiting to be
    marked done. (Addresses owed because accounts went can't be forgotten.)"""
    batch = get_object_or_404(ListSync, pk=pk, kind__in=[ListSync.Kind.ADD, ListSync.Kind.REMOVE], note="",
                              done_at__isnull=True)
    batch.delete()
    AuditLog.record(request.user, "forget_list_download", target=SiteConfig.get().mailing_list_address,
                    detail=f"{batch.count} address{'es' if batch.count != 1 else ''} to "
                           + ("add" if batch.kind == ListSync.Kind.ADD else "take off"))
    messages.success(request, "Forgotten: that download no longer waits to be marked done.")
    return redirect("panel:mailing_list")


@portal_admin_required
@require_POST
def mailing_list_done(request, pk):
    """An admin added (or took off) a downloaded batch on Mailman: record it
    in the site's copy, so the page shows it before the next update."""
    batch = get_object_or_404(ListSync, pk=pk, kind__in=[ListSync.Kind.ADD, ListSync.Kind.REMOVE])
    now = timezone.now()
    addresses = list(dict.fromkeys(address.strip().lower() for address in batch.addresses if address.strip()))
    with transaction.atomic():
        # claimed once, however many times the button is pressed
        if not ListSync.objects.filter(pk=batch.pk, done_at__isnull=True).update(done_at=now, done_by=request.user):
            messages.info(request, "That was already recorded.")
            return redirect("panel:mailing_list")
        if batch.kind == ListSync.Kind.ADD:
            ListMember.objects.bulk_create([
                ListMember(email=address, key=listsync.mailbox_key(address), source=ListMember.Source.SITE,
                           added_at=now)
                for address in addresses
            ], ignore_conflicts=True)  # some may be in the copy already
        else:
            ListMember.objects.filter(email__in=addresses).delete()
            listsync.close_owed(addresses, listsync.reached_keys(), request.user, now)
    AuditLog.record(request.user, f"list_{batch.kind}_done", target=SiteConfig.get().mailing_list_address,
                    detail=f"{batch.count} address{'es' if batch.count != 1 else ''}")
    messages.success(request, f"Recorded: the {batch.count} address{'es' if batch.count != 1 else ''} in that file "
                              + ("are on the list now." if batch.kind == ListSync.Kind.ADD else "are off the list now."))
    return redirect("panel:mailing_list")


@portal_admin_required
def space_saver(request):
    """Free the space taken by pictures of events more than a week behind
    us, and by pictures no event uses. The events themselves stay."""
    from django.template.defaultfilters import filesizeformat

    from events import space

    if request.method == "POST":
        pictures, files, freed = space.free_up()
        if pictures or files:
            AuditLog.record(request.user, "space_saver",
                            detail=f"{pictures} event picture{'s' if pictures != 1 else ''} and {files} unused "
                                   f"file{'s' if files != 1 else ''}, {filesizeformat(freed)}")
            messages.success(request, f"Freed {filesizeformat(freed)}: {pictures} picture{'s' if pictures != 1 else ''} "
                                      f"of past events and {files} unused file{'s' if files != 1 else ''}.")
        else:
            messages.info(request, "Nothing to clear.")
        return redirect("panel:space_saver")
    found = space.survey()
    return render(request, "panel/space_saver.html", {
        "nav_active": "panel",
        "panel_tab": "space_saver",
        **found,
        "shown": found["events"][:50],
        "more": max(0, len(found["events"]) - 50),
        "free_bytes": found["event_bytes"] + found["unused_bytes"],
    })
