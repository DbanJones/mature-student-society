"""Society admin panel views.

Every view is gated by ``portal_admin_required`` (society admins via
``User.is_portal_admin`` — NOT Django's ``is_staff``) and every mutating
action writes an ``AuditLog`` row via ``AuditLog.record``.
"""

from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMessage
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.decorators import portal_admin_required
from accounts.models import User, WaitlistRequest, WhatsAppAccessRequest
from core.models import SiteConfig

from . import services
from .forms import MailerForm, MemberEditForm
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

@portal_admin_required
def home(request):
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
    """Approve or reject one pending waitlist request (button name=decision)."""
    wreq = get_object_or_404(
        WaitlistRequest, pk=pk, status=WaitlistRequest.Status.PENDING
    )
    decision = request.POST.get("decision")
    note = request.POST.get("review_note", "").strip()[:200]

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
            request, f"Rejected the request from {wreq.first_name} {wreq.last_name}."
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


@portal_admin_required
@require_POST
def member_toggle_admin(request, pk):
    member = get_object_or_404(User, pk=pk)
    if member == request.user and member.is_portal_admin:
        messages.error(
            request,
            "You cannot remove your own admin access — ask another admin to do it.",
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
def member_ban(request, pk):
    member = get_object_or_404(User, pk=pk)
    if member == request.user:
        messages.error(request, "You cannot ban yourself.")
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

    cascades = {
        "events": member.events_created.count(),
        "rsvps": member.rsvps.count(),
        "ratings": member.restaurant_ratings.count(),
        "guide_pages": member.guide_pages_created.count(),
        "guide_revisions": member.guide_revisions.count(),
    }
    if request.method == "POST":
        target = f"{_display_name(member)} <{member.email or member.username}>"
        detail = (
            f"deleted {cascades['events']} events, {cascades['rsvps']} RSVPs, "
            f"{cascades['ratings']} ratings"
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
    })


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
        if form.is_valid():
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
