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


def super_admin_required(view_func):
    """Allow only the super admin(s) (User.is_super_admin) through."""

    @login_required
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_super_admin:
            raise PermissionDenied("Super admin access required.")
        return view_func(request, *args, **kwargs)

    return wrapper


def tag_owner_or_admin_required(view_func):
    """Allow society admins, plus members who own at least one event tag.

    Used for the panel's tagged-events workspace: tag owners are ordinary
    members with responsibility for one club, so they get this page (and only
    this page) of the admin panel.
    """

    @login_required
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not (
            request.user.is_portal_admin or request.user.tags_owned.exists()
        ):
            raise PermissionDenied("Tag owner or society admin access required.")
        return view_func(request, *args, **kwargs)

    return wrapper
