from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def index(request):
    rows = list(request.user.notifications.all()[:100])
    return render(request, "notifications/index.html", {
        "nav_active": "notifications",
        "rows": rows,
        "unread": sum(1 for r in rows if not r.is_read),
    })


@login_required
def go(request, pk):
    """Follow a notification: mark it read, then redirect to what it's about."""
    row = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if row.read_at is None:
        row.read_at = timezone.now()
        row.save(update_fields=["read_at"])
    if row.url and url_has_allowed_host_and_scheme(
        row.url, allowed_hosts={request.get_host()}
    ):
        return redirect(row.url)
    return redirect("notifications:index")


@login_required
@require_POST
def mark_all_read(request):
    request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    return redirect("notifications:index")
