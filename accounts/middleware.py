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
