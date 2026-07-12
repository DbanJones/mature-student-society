import functools

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def portal_admin_required(view_func):
    """Allow only society admins (User.is_portal_admin) through."""

    @login_required
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_portal_admin:
            raise PermissionDenied("Society admin access required.")
        return view_func(request, *args, **kwargs)

    return wrapper
