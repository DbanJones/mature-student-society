"""The public testimonials page, and members' own submissions."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import TestimonialForm
from .models import MAX_PENDING_PER_MEMBER, Testimonial


def _can_submit(user):
    return user.testimonials.pending().count() < MAX_PENDING_PER_MEMBER


def _render_index(request, form=None):
    context = {
        "nav_active": "testimonials",
        "testimonials": Testimonial.objects.approved().featured_first(),
    }
    if request.user.is_authenticated:
        context.update({
            "mine": request.user.testimonials.all(),
            "can_submit": _can_submit(request.user),
            "form": form or TestimonialForm(),
        })
    return render(request, "testimonials/index.html", context)


def index(request):
    """PUBLIC. Approved testimonials, featured first. Logged-in members also
    see the submission form and the state of their own."""
    return _render_index(request)


@login_required
@require_POST
def submit(request):
    if not _can_submit(request.user):
        messages.info(
            request,
            "You already have a testimonial waiting for review. You can send "
            "another once the committee has looked at it.",
        )
        return redirect("testimonials:index")
    form = TestimonialForm(request.POST)
    if not form.is_valid():
        return _render_index(request, form=form)
    user = request.user
    testimonial = form.save(commit=False)
    testimonial.author = user
    testimonial.author_name = user.get_full_name() or user.username
    testimonial.author_college = user.get_college_display() if user.college else ""
    testimonial.save()
    messages.success(
        request,
        "Thank you. The committee will read it before it appears on the page.",
    )
    return redirect("testimonials:index")


@login_required
@require_POST
def withdraw(request, pk):
    """Members can take their own words down at any time, whatever the state."""
    testimonial = get_object_or_404(Testimonial, pk=pk, author=request.user)
    testimonial.delete()
    messages.success(request, "Removed your testimonial.")
    return redirect("testimonials:index")
