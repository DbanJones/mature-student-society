"""Private alert management. Only publication queues notifications."""
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts.models import User

from .alert_forms import FormalAlertForm
from .models import FormalAlert
from .views import member_required

MAX_ACTIVE_ALERTS = 20


def _context(request, form=None):
    return {"nav_active": "formals", "formal_tab": "alerts", "alerts": FormalAlert.objects.filter(member=request.user), "form": form or FormalAlertForm()}


@member_required
@require_GET
def alerts(request):
    return render(request, "formals/alerts.html", _context(request))


@member_required
@require_POST
def save_alert(request):
    form = FormalAlertForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            existing = FormalAlert.objects.filter(member=request.user, active=True, **form.cleaned_data).first()
            if existing:
                messages.info(request, "This alert is already saved.")
            elif FormalAlert.objects.filter(member=request.user, active=True).count() >= MAX_ACTIVE_ALERTS:
                form.add_error(None, "You can have up to 20 active alerts. Remove or pause an existing alert first.")
            else:
                FormalAlert.objects.create(member=request.user, **form.cleaned_data)
                messages.success(request, "Alert saved. We will email you and notify you here when a new formal matches.")
        if not form.errors:
            return redirect("formals:index" if request.POST.get("next") == "browse" else "formals:alerts")
    return render(request, "formals/alerts.html", _context(request, form), status=400)


@member_required
@require_POST
def delete_alert(request, pk):
    with transaction.atomic():
        User.objects.select_for_update().get(pk=request.user.pk)
        alert = get_object_or_404(FormalAlert, pk=pk, member=request.user)
        action = request.POST.get("action", "delete")
        if action == "pause":
            alert.active = False
            alert.save(update_fields=["active"])
            messages.success(request, "Alert paused.")
        elif action == "resume":
            if not alert.active and FormalAlert.objects.filter(member=request.user, active=True).count() >= MAX_ACTIVE_ALERTS:
                messages.error(request, "Pause another alert before resuming this one. The limit is 20 active alerts.")
            else:
                alert.active = True
                alert.save(update_fields=["active"])
                messages.success(request, "Alert resumed for newly published formals.")
        elif action == "delete":
            alert.delete()
            messages.success(request, "Alert removed.")
        else:
            messages.error(request, "Choose pause, resume or remove.")
    return redirect("formals:alerts")
