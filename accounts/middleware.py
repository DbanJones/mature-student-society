from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect

# Paths a logged-in-but-incomplete profile may still visit (avoids redirect loops
# and lets people log out, finish their profile, or load assets).
PROFILE_EXEMPT_PREFIXES = (
    "/accounts/",
    "/static/",
    "/media/",
    "/dj-admin/",
)

# Paths reachable before the terms have been accepted. Deliberately NARROWER
# than the profile exemptions: only the auth flow itself (log in, log out,
# accept, the public copy of the terms) and assets. Everything else in the
# members' area — the profile, the one-time WhatsApp reveal, password
# changes — sits behind the gate.
TERMS_EXEMPT_PREFIXES = (
    "/accounts/terms/",
    "/accounts/login/",         # includes the dev impersonation card
    "/accounts/logout/",
    "/accounts/raven/",
    "/accounts/waitlist/",      # public request form + thanks page
    "/accounts/set-password/",  # invite links must work pre-acceptance
    "/static/",
    "/media/",
    "/dj-admin/",
    "/terms/",
)


class BannedUserMiddleware:
    """Kill the session of anyone banned while logged in.

    Password logins are already blocked by ``is_active=False``; this catches
    live sessions and any future auth backend that ignores is_active.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.user.is_banned:
            logout(request)
            messages.error(
                request,
                "Your account has been suspended. Contact the committee if you "
                "think this is a mistake.",
            )
            return redirect("core:home")
        return self.get_response(request)


class ProfileCompletionMiddleware:
    """New members must give their basic details (name, college, mobile) before
    using the members' area."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (
            user.is_authenticated
            and not user.profile_complete
            and not request.path.startswith(PROFILE_EXEMPT_PREFIXES)
        ):
            return redirect("accounts:profile_setup")
        return self.get_response(request)


class TermsAcceptanceMiddleware:
    """Members must accept the current terms and conditions before using the
    members' area.

    Runs ahead of ProfileCompletionMiddleware: agreeing to the terms is what
    licenses us to collect the profile details, so it has to come first.
    Everyone is gated, not just new sign-ups — publishing a new version of the
    terms therefore re-prompts the whole membership on their next page load.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not request.path.startswith(
            TERMS_EXEMPT_PREFIXES
        ):
            # Deferred so the query is skipped entirely for static assets and
            # for the accept page itself.
            from core.models import TermsVersion

            if not user.has_accepted_terms(TermsVersion.current_number()):
                return redirect("accounts:terms")
        return self.get_response(request)
