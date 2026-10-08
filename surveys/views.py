"""Surveys: the list, each survey's page with its answer sheet, the results,
and the builder for the people who run it."""

import csv
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from accounts.decorators import portal_admin_required
from accounts.models import User
from notifications.models import Notification
from notifications.services import notify
from panel.models import AuditLog

from .forms import AlreadyAnswered, QuestionForm, ResponseForm, SurveyForm
from .models import Participation, Question, Survey

REMINDER_GAP = datetime.timedelta(hours=20)


def _survey(request, slug):
    """A survey a member may look at: drafts only for the people running them."""
    survey = get_object_or_404(Survey.objects.prefetch_related("admins"), slug=slug)
    survey.resolve_if_due()
    if survey.is_draft and not survey.can_manage(request.user):
        raise Http404("No survey here.")
    return survey


def _manageable(request, slug):
    survey = get_object_or_404(Survey.objects.prefetch_related("admins"), slug=slug)
    if not survey.can_manage(request.user):
        if survey.is_draft:
            raise Http404("No survey here.")  # a draft's existence is the runners' business
        raise PermissionDenied("Only the people running this survey can do that.")
    survey.resolve_if_due()
    return survey


def _members():
    return User.objects.filter(is_active=True, is_banned=False)


@login_required
def index(request):
    """Every survey a member may see: live ones to answer, ones opening
    soon, closed ones with their results, and drafts for those who run them."""
    for survey in Survey.objects.due():
        survey.close()
    surveys = (
        Survey.objects.visible_to(request.user)
        .prefetch_related("admins")
        .annotate(answer_count=Count("participations", distinct=True))
    )
    answered = set(Participation.objects.filter(user=request.user).values_list("survey_id", flat=True))
    live, soon, closed, drafts = [], [], [], []
    for survey in surveys:
        survey.answered_by_me = survey.pk in answered
        survey.results_ok = survey.can_see_results(request.user)
        if survey.is_draft:
            drafts.append(survey)
        elif survey.is_scheduled:
            soon.append(survey)
        elif survey.is_live:
            live.append(survey)
        else:
            closed.append(survey)
    far = timezone.now() + datetime.timedelta(days=3650)
    live.sort(key=lambda s: s.closes_at or far)
    soon.sort(key=lambda s: s.opens_at)
    closed.sort(key=lambda s: s.ended_at or s.updated_at, reverse=True)
    return render(request, "surveys/index.html", {
        "nav_active": "surveys",
        "live": live, "soon": soon, "closed": closed, "drafts": drafts,
    })


@login_required
def detail(request, slug):
    """The survey page: its introduction, the answer sheet while it is live,
    and for the people running it the questions and the controls."""
    survey = _survey(request, slug)
    user = request.user
    named = survey.named_response_for(user)
    answered = survey.has_answered(user)
    form = None
    if survey.is_live and (named is not None or not answered):
        form = ResponseForm(survey, request.POST or None, user=user, existing=named)
        if request.method == "POST":
            if not form.questions:
                messages.error(request, "This survey has no questions yet.")
                return redirect(survey)
            if form.is_valid():
                try:
                    form.save()
                except AlreadyAnswered:
                    messages.info(request, "You had already answered this survey.")
                    return redirect(survey)
                messages.success(
                    request,
                    "Your answers have been updated." if named else "Thank you, your answers have been sent.",
                )
                return redirect(survey)
            messages.error(request, "Please check the answers marked below.")
    elif request.method == "POST":
        if survey.is_live:
            messages.error(request, "You answered this survey anonymously, so the answers can't be changed.")
        else:
            messages.error(request, "This survey isn't taking answers at the moment.")
        return redirect(survey)
    can_manage = survey.can_manage(user)
    return render(request, "surveys/detail.html", {
        "nav_active": "surveys",
        "survey": survey,
        "form": form,
        "named": named,
        "answered": answered,
        "can_manage": can_manage,
        "can_see_results": survey.can_see_results(user),
        "questions": list(survey.questions.prefetch_related("choices")) if can_manage else [],
        "locked": survey.is_locked if can_manage else False,
        "answer_count": survey.participations.count(),
        "runners": survey.runners(),
    })


@login_required
def results(request, slug):
    survey = _survey(request, slug)
    can_manage = survey.can_manage(request.user)
    if not survey.can_see_results(request.user):
        raise PermissionDenied("The results of this survey aren't shared with you.")
    if request.GET.get("format") == "csv":
        if not can_manage:
            raise PermissionDenied("Only the people running this survey can download the answers.")
        if not survey.is_closed:  # row by row, as answers arrive, would say too much
            messages.info(request, "The download is available once the survey has closed.")
            return redirect("surveys:results", slug=slug)
        return _csv(survey)
    total = survey.responses.count()
    named = survey.responses.filter(is_anonymous=False).count()
    answered = survey.participations.count()
    return render(request, "surveys/results.html", {
        "nav_active": "surveys",
        "survey": survey,
        "can_manage": can_manage,
        "results": survey.results(),
        "total": total, "named": named, "anonymous": total - named,
        "answered": answered,
        "not_answered": max(0, _members().count() - answered) if can_manage else None,
    })


def _cell(text):
    """Text safe to open in a spreadsheet: a leading =, +, -, @, tab or
    return would otherwise be run as a formula."""
    text = str(text or "")
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv(survey):
    """Every response, one row each: named ones by name with the time they
    were sent, anonymous ones in the order of their random ids with no
    time at all."""
    questions = list(survey.questions.all())
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{survey.slug}-answers.csv"'
    writer = csv.writer(response)
    writer.writerow(["Sent", "Member"] + [_cell(q.prompt) for q in questions])
    rows = list(
        survey.responses.select_related("respondent")
        .prefetch_related("answers__choices", "answers__question")
    )
    by_name = sorted(
        (r for r in rows if not r.is_anonymous),
        key=lambda r: (r.respondent.last_name, r.respondent.first_name) if r.respondent else ("", ""),
    )
    by_id = sorted((r for r in rows if r.is_anonymous), key=lambda r: str(r.pk))
    for row in by_name + by_id:
        answers = {a.question_id: a for a in row.answers.all()}
        if row.is_anonymous:
            who, sent = "Anonymous", ""
        else:
            who = (row.respondent.get_full_name() or row.respondent.username) if row.respondent else "a former member"
            sent = timezone.localtime(row.submitted_at).strftime("%Y-%m-%d %H:%M") if row.submitted_at else ""
        writer.writerow(
            [sent, _cell(who)] + [_cell(answers[q.pk].display()) if q.pk in answers else "" for q in questions]
        )
    return response


# --- running a survey ---------------------------------------------------------


@portal_admin_required
def create(request):
    form = SurveyForm(request.POST or None, can_set_admins=True)
    if request.method == "POST" and form.is_valid():
        survey = form.save(commit=False)
        survey.created_by = request.user
        survey.save()
        form.save_m2m()
        AuditLog.record(request.user, "create_survey", target=survey.title)
        messages.success(request, "Survey created. Add its questions, then open it.")
        return redirect(survey)
    return render(request, "surveys/survey_form.html", {"nav_active": "surveys", "form": form, "survey": None})


@login_required
def edit(request, slug):
    survey = _manageable(request, slug)
    form = SurveyForm(request.POST or None, instance=survey, can_set_admins=request.user.is_portal_admin)
    if request.method == "POST" and form.is_valid():
        form.save()
        AuditLog.record(request.user, "edit_survey", target=survey.title)
        messages.success(request, "Survey settings saved.")
        return redirect(survey)
    return render(request, "surveys/survey_form.html", {"nav_active": "surveys", "form": form, "survey": survey})


@login_required
def question_add(request, slug):
    survey = _manageable(request, slug)
    if survey.is_locked:
        messages.error(request, "This survey already has answers, so questions can't be added. Start a new survey for new questions.")
        return redirect(survey)
    form = QuestionForm(request.POST or None, survey=survey)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Question added.")
        return redirect(survey.get_absolute_url() + "#questions")
    return render(request, "surveys/question_form.html", {"nav_active": "surveys", "form": form, "survey": survey, "question": None})


def _wording(question):
    """A question's wording in one line, for the audit log."""
    parts = [question.prompt]
    if question.has_choices:
        parts.append(" / ".join(c.label for c in question.choices.all()))
    if question.kind == Question.Kind.SCALE and (question.scale_low or question.scale_high):
        parts.append(f"1={question.scale_low} 5={question.scale_high}")
    return " | ".join(parts)


@login_required
def question_edit(request, slug, pk):
    survey = _manageable(request, slug)
    question = get_object_or_404(Question, survey=survey, pk=pk)
    locked = survey.is_locked
    before = _wording(question) if locked else ""  # rewording a question people have answered is on the record
    form = QuestionForm(request.POST or None, instance=question, survey=survey, locked=locked)
    if request.method == "POST" and form.is_valid():
        form.save()
        if locked and _wording(question) != before:
            AuditLog.record(
                request.user, "reword_survey_question", target=survey.title,
                detail=f"{before} -> {_wording(question)}"[:300],
            )
        messages.success(request, "Question saved.")
        return redirect(survey.get_absolute_url() + "#questions")
    return render(request, "surveys/question_form.html", {
        "nav_active": "surveys", "form": form, "survey": survey, "question": question, "locked": survey.is_locked,
    })


@login_required
@require_POST
def question_delete(request, slug, pk):
    survey = _manageable(request, slug)
    question = get_object_or_404(Question, survey=survey, pk=pk)
    if survey.is_locked:
        messages.error(request, "This survey already has answers, so questions can't be removed.")
    elif not survey.is_draft and survey.questions.count() == 1:
        messages.error(request, "A published survey keeps at least one question. Add another before removing this one.")
    else:
        question.delete()
        messages.success(request, "Question removed.")
    return redirect(survey.get_absolute_url() + "#questions")


@login_required
@require_POST
def question_move(request, slug, pk):
    survey = _manageable(request, slug)
    questions = list(survey.questions.all())
    index = next((i for i, q in enumerate(questions) if q.pk == pk), None)
    if index is None:
        raise Http404("No such question.")
    other = index - 1 if request.POST.get("direction") == "up" else index + 1
    if 0 <= other < len(questions):
        questions[index], questions[other] = questions[other], questions[index]
        for position, question in enumerate(questions, start=1):
            if question.sort_order != position:
                question.sort_order = position
                question.save(update_fields=["sort_order"])
    return redirect(survey.get_absolute_url() + "#questions")


@login_required
@require_POST
def open_survey(request, slug):
    """Open a draft, or reopen a closed survey. Members are told once: all
    of them when it first opens, those who haven't answered when it reopens."""
    survey = _manageable(request, slug)
    if survey.status == Survey.Status.OPEN:
        messages.info(request, "The survey is already open.")
        return redirect(survey)
    if not survey.questions.exists():
        messages.error(request, "Add at least one question before opening the survey.")
        return redirect(survey)
    if survey.closes_at and survey.closes_at <= timezone.now() and survey.is_draft:
        messages.error(request, "The closing time has already passed. Change it in Settings first.")
        return redirect(survey)
    reopening = survey.status == Survey.Status.CLOSED
    if reopening:
        survey.reopen()
        recipients = _members().exclude(pk__in=survey.participations.values("user_id"))
    else:
        survey.status = Survey.Status.OPEN
        survey.save(update_fields=["status", "updated_at"])
        recipients = _members()
    if survey.is_scheduled:
        opens = date_format(timezone.localtime(survey.opens_at), "D j M, H:i")
        text = f"Have your say: {survey.title} (opens {opens})"
    elif reopening:
        text = f"Reopened for more answers: {survey.title}"
    else:
        text = f"Have your say: {survey.title}"
    told = notify(recipients, Notification.Kind.SURVEY, text, survey.get_absolute_url(), exclude=[request.user])
    AuditLog.record(
        request.user, "reopen_survey" if reopening else "open_survey",
        target=survey.title, detail=f"{len(told)} members told",
    )
    if survey.is_scheduled:
        opens = date_format(timezone.localtime(survey.opens_at), "l j F, H:i")
        messages.success(request, f"The survey will open on {opens}. Members have been told.")
    else:
        messages.success(request, f"The survey is open. {len(told)} members have been told.")
    return redirect(survey)


@login_required
@require_POST
def close_survey(request, slug):
    survey = _manageable(request, slug)
    if survey.status == Survey.Status.OPEN:
        survey.close()
        AuditLog.record(request.user, "close_survey", target=survey.title)
        messages.success(request, "The survey is closed. Its results stay on the Surveys page.")
    return redirect(survey)


@login_required
@require_POST
def delete_survey(request, slug):
    survey = _manageable(request, slug)
    title = survey.title
    survey.delete()
    AuditLog.record(request.user, "delete_survey", target=title)
    messages.success(request, f"“{title}” and its answers have been deleted.")
    return redirect("surveys:index")


@login_required
@require_POST
def remind(request, slug):
    survey = _manageable(request, slug)
    if not survey.is_live:
        messages.error(request, "The survey isn't open, so there's nobody to remind.")
        return redirect(survey)
    now = timezone.now()
    if survey.last_reminded_at and now - survey.last_reminded_at < REMINDER_GAP:
        messages.error(request, "A reminder went out less than a day ago. Give people a little longer.")
        return redirect(survey)
    answered = survey.participations.values("user_id")
    told = notify(
        _members().exclude(pk__in=answered), Notification.Kind.SURVEY,
        f"Reminder: have your say in “{survey.title}”", survey.get_absolute_url(), exclude=[request.user],
    )
    survey.last_reminded_at = now
    survey.save(update_fields=["last_reminded_at", "updated_at"])
    AuditLog.record(request.user, "remind_survey", target=survey.title, detail=f"{len(told)} members reminded")
    messages.success(request, f"Reminded {len(told)} member{'s' if len(told) != 1 else ''} who haven't answered yet.")
    return redirect(survey)
