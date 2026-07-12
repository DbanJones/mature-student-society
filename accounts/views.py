"""Authentication, profiles, the associate waitlist and the one-time
WhatsApp invite."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from core.models import SiteConfig

from .forms import (
    CRSID_RE,
    AssociateLoginForm,
    ProfileForm,
    WaitlistForm,
    WhatsAppRequestForm,
)
from .models import User, WaitlistRequest, WhatsAppAccessRequest


def _dev_login_enabled():
    """Development impersonation is available ONLY locally: both DEBUG and
    RAVEN_MODE=dev must hold. Never true on the SRCF."""
    return settings.DEBUG and settings.RAVEN_MODE == "dev"


def _safe_next(request):
    """The validated ?next= target, or None."""
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return None


# --- Login / logout -----------------------------------------------------------


def login_view(request):
    """The society's front door: Raven for Cambridge members, email+password
    for associates, and (in local development only) an impersonation card."""
    if request.user.is_authenticated:
        return redirect("dashboard:home")

    if request.method == "POST":
        form = AssociateLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            auth_login(request, user)
            messages.success(
                request, f"Welcome back, {user.first_name or user.username}."
            )
            return redirect(_safe_next(request) or "dashboard:home")
    else:
        form = AssociateLoginForm(request)

    context = {
        "form": form,
        "next": request.GET.get("next", ""),
        "dev_login": _dev_login_enabled(),
    }
    if context["dev_login"]:
        context["dev_users"] = User.objects.filter(is_banned=False).order_by(
            "username"
        )
    return render(request, "accounts/login.html", context)


def dev_login(request):
    """One-click impersonation for local development.

    404s unless BOTH settings.DEBUG and RAVEN_MODE == "dev" — this view must
    be unreachable in any production configuration.
    """
    if not _dev_login_enabled() or request.method != "POST":
        raise Http404

    user = None
    user_id = request.POST.get("user_id")
    crsid = (request.POST.get("crsid") or "").strip().lower()

    if user_id:
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            raise Http404
    elif crsid:
        if not CRSID_RE.match(crsid):
            messages.error(request, "That doesn't look like a CRSid (e.g. dbj25).")
            return redirect("accounts:login")
        # Mirror accounts.auth.CrsidBackend.configure_user.
        user, created = User.objects.get_or_create(
            username=crsid,
            defaults={
                "crsid": crsid,
                "account_type": User.AccountType.RAVEN,
                "email": f"{crsid}@cam.ac.uk",
            },
        )
        if not created and not user.crsid:
            user.crsid = user.username
            user.account_type = User.AccountType.RAVEN
            if not user.email:
                user.email = f"{user.username}@cam.ac.uk"
            user.save(update_fields=["crsid", "account_type", "email"])
    else:
        messages.error(request, "Pick a user or enter a CRSid.")
        return redirect("accounts:login")

    if user.is_banned:
        messages.error(request, "That account is banned — impersonation refused.")
        return redirect("accounts:login")

    auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    messages.warning(
        request,
        f"Development impersonation: you are now logged in as "
        f"{user.get_full_name() or user.username}.",
    )
    return redirect("dashboard:home")


def raven(request):
    """Raven entry point.

    In ``header`` mode Apache's mod_ucam_webauth protects this path and
    NevarRemoteUserMiddleware has already logged the user in before this view
    runs — so an unauthenticated request here means the Apache config is
    broken. In ``dev`` mode there is no Raven; point people at the dev login.
    """
    if settings.RAVEN_MODE == "dev":
        messages.info(
            request,
            "Raven login is simulated in development — use the impersonation "
            "card below.",
        )
        return redirect("accounts:login")
    if request.user.is_authenticated:
        # ProfileCompletionMiddleware diverts incomplete profiles to setup.
        return redirect("dashboard:home")
    return render(request, "accounts/raven_error.html", status=500)


class LogoutView(auth_views.LogoutView):
    """POST-only logout, back to the public homepage."""

    next_page = reverse_lazy("core:home")


# --- Profile ------------------------------------------------------------------


@login_required
def profile_setup(request):
    """The 'complete your details' form every new login is sent to by
    ProfileCompletionMiddleware."""
    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            user = form.save(commit=False)
            if user.account_type == User.AccountType.ASSOCIATE:
                user.username = user.email  # associates' username IS their email
            user.save()
            messages.success(request, "Thanks — your profile is complete.")
            if user.can_view_whatsapp_link:
                return redirect("accounts:whatsapp")
            return redirect("dashboard:home")
    else:
        form = ProfileForm(instance=request.user)
    return render(request, "accounts/profile_setup.html", {"form": form})


@login_required
def profile(request):
    """View and edit your own profile."""
    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            user = form.save(commit=False)
            if user.account_type == User.AccountType.ASSOCIATE:
                user.username = user.email
            user.save()
            messages.success(request, "Profile updated.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=request.user)
    return render(
        request,
        "accounts/profile.html",
        {"form": form, "nav_active": "dashboard"},
    )


class PasswordChangeView(auth_views.PasswordChangeView):
    """Password change for associate accounts (Raven users have no portal
    password to change)."""

    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def dispatch(self, request, *args, **kwargs):
        if (
            request.user.is_authenticated
            and request.user.account_type != get_user_model().AccountType.ASSOCIATE
        ):
            messages.info(
                request,
                "You log in with Raven, so there's no portal password to change.",
            )
            return redirect("accounts:profile")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        messages.success(self.request, "Password changed.")
        return super().form_valid(form)


# --- Member directory & profiles -------------------------------------------------


def _members_visible_to(viewer):
    """Members shown in the directory: not banned, profile complete enough to
    be recognisable. Shadow-banned members appear only to admins (and to
    themselves, so nothing looks amiss)."""
    qs = User.objects.filter(is_banned=False).exclude(first_name="")
    if not viewer.is_portal_admin:
        qs = qs.filter(Q(is_shadow_banned=False) | Q(pk=viewer.pk))
    return qs


@login_required
def member_directory(request):
    q = request.GET.get("q", "").strip()
    members_qs = _members_visible_to(request.user)
    if q:
        members_qs = members_qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(course__icontains=q)
            | Q(college__icontains=q)
            | Q(work__icontains=q)
            | Q(interests__icontains=q)
            | Q(talk_to_me_about__icontains=q)
        )
    return render(request, "members/directory.html", {
        "nav_active": "members",
        "members": members_qs.order_by("first_name", "last_name"),
        "q": q,
    })


@login_required
def member_profile(request, username):
    member = get_object_or_404(
        _members_visible_to(request.user) | User.objects.filter(
            pk=request.user.pk
        ),
        username=username,
    )
    from events.models import RSVP, Event

    now = timezone.now()
    hosting = (
        Event.objects.visible_to(request.user)
        .filter(Q(host=member) | Q(created_by=member, host__isnull=True),
                start__gte=now)
        .select_related("category")
        .order_by("start")[:6]
    )
    attended_count = RSVP.objects.filter(
        user=member, status=RSVP.Status.GOING,
        event__start__lt=now, event__is_cancelled=False,
    ).count()

    return render(request, "members/profile.html", {
        "nav_active": "members",
        "member": member,
        "hosting": hosting,
        "attended_count": attended_count,
        "hosted_count": member.events_created.filter(is_cancelled=False).count(),
        "tags_owned": member.tags_owned.all(),
        "is_self": member == request.user,
    })


# --- Associate waitlist ---------------------------------------------------------


def waitlist(request):
    """PUBLIC: partners/family without a CRSid ask for an associate account."""
    if request.method == "POST":
        form = WaitlistForm(request.POST)
        if form.is_valid():
            if form.cleaned_data.get("website"):
                # Honeypot tripped: a bot. Pretend it worked; save nothing.
                return redirect("accounts:waitlist_done")
            email = form.cleaned_data["email"]
            # Uniform outcome regardless of whether an account or an earlier
            # request already exists — otherwise the distinct messages would
            # let an anonymous visitor probe who is/isn't an MSS member. Only
            # create a genuinely new request; existing ones are left untouched.
            already_known = (
                User.objects.filter(email__iexact=email).exists()
                or User.objects.filter(username__iexact=email).exists()
                or WaitlistRequest.objects.filter(email__iexact=email).exists()
            )
            if not already_known:
                form.save()
            return redirect("accounts:waitlist_done")
    else:
        form = WaitlistForm()
    return render(request, "accounts/waitlist.html", {"form": form})


def waitlist_done(request):
    """Thanks page after a waitlist submission."""
    return render(request, "accounts/waitlist_done.html")


class SetPasswordView(auth_views.PasswordResetConfirmView):
    """Approved associates land here from their invite email to choose a
    password (uidb64 + token, single-use)."""

    template_name = "accounts/set_password.html"
    success_url = reverse_lazy("accounts:login")

    def form_valid(self, form):
        messages.success(
            self.request,
            "Password set — you can now log in with your email address.",
        )
        return super().form_valid(form)


# --- One-time WhatsApp invite ---------------------------------------------------


@login_required
def whatsapp(request):
    """The one-time WhatsApp Open Forum invite.

    Security model: the group link is revealed exactly once per member
    (mark_whatsapp_link_viewed). Afterwards they must ask an admin, via a
    WhatsAppAccessRequest, to be re-admitted.
    """
    config = SiteConfig.get()
    link = config.whatsapp_group_link
    user = request.user

    if not link:
        return render(request, "accounts/whatsapp.html", {"state": "unconfigured"})

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "reveal":
            # Atomic claim: only the request that actually flips the flag from
            # NULL gets to reveal the link. Two concurrent reveals therefore
            # can't both succeed — the loser falls through to the redirect and
            # sees the "already used" state.
            claimed = User.objects.filter(
                pk=user.pk, whatsapp_link_viewed_at__isnull=True
            ).update(whatsapp_link_viewed_at=timezone.now())
            if claimed:
                # Deliberately rendered on the POST response: the link is shown
                # once, so there is no page it could be re-fetched from.
                return render(
                    request,
                    "accounts/whatsapp.html",
                    {"state": "revealed", "link": link},
                )
            return redirect("accounts:whatsapp")
        if action == "request" and not user.can_view_whatsapp_link:
            has_open = user.whatsapp_requests.filter(
                status=WhatsAppAccessRequest.Status.OPEN
            ).exists()
            if has_open:
                messages.info(
                    request,
                    "You already have an open request — an admin will get to "
                    "it soon.",
                )
            else:
                req_form = WhatsAppRequestForm(request.POST)
                if req_form.is_valid():
                    req = req_form.save(commit=False)
                    req.user = user
                    req.save()
                    messages.success(
                        request,
                        "Request sent — an admin will add you back to the "
                        "group and mark it handled.",
                    )
                else:
                    messages.error(request, "That message was too long — try a "
                                            "shorter one.")
        return redirect("accounts:whatsapp")

    if user.can_view_whatsapp_link:
        return render(request, "accounts/whatsapp.html", {"state": "interstitial"})

    open_request = user.whatsapp_requests.filter(
        status=WhatsAppAccessRequest.Status.OPEN
    ).first()
    last_handled = user.whatsapp_requests.exclude(
        status=WhatsAppAccessRequest.Status.OPEN
    ).first()
    return render(
        request,
        "accounts/whatsapp.html",
        {
            "state": "used",
            "open_request": open_request,
            "last_handled": last_handled,
            "request_form": WhatsAppRequestForm(),
        },
    )
