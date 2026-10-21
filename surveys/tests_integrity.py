"""Regression coverage for forms that become stale while a survey changes."""

import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib import admin
from django.db import close_old_connections
from django.test import RequestFactory, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from .admin import QuestionAdmin, SurveyAdmin
from .forms import QuestionForm, ResponseForm, SurveyChanged
from .models import Answer, Participation, Question, Response, Survey
from .tests import SurveyTestCase


class SurveyIntegrityTests(SurveyTestCase):
    def response_form(self, **kwargs):
        form = ResponseForm(self.survey, self.answers(identity="anonymous"), user=self.member, **kwargs)
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def unlocked_question_form(self, instance=None):
        form = QuestionForm(
            {"prompt": "New choices", "kind": "multi", "choices_text": "Lunch\nDinner"},
            survey=self.survey, instance=instance,
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def test_validated_submission_cannot_save_after_closure(self):
        form = self.response_form()
        Survey.objects.get(pk=self.survey.pk).close()
        with self.assertRaisesRegex(SurveyChanged, "isn't taking answers"):
            form.save()
        self.assertFalse(Response.objects.exists())
        self.assertFalse(Participation.objects.exists())

    def test_validated_named_edit_cannot_replace_answers_after_closure(self):
        initial = ResponseForm(self.survey, self.answers(identity="named"), user=self.member)
        self.assertTrue(initial.is_valid(), initial.errors)
        response = initial.save()
        edit = ResponseForm(
            self.survey, self.answers(**{f"q{self.mood.pk}": "1"}),
            user=self.member, existing=response,
        )
        self.assertTrue(edit.is_valid(), edit.errors)
        self.survey.close()
        with self.assertRaises(SurveyChanged):
            edit.save()
        response.refresh_from_db()
        self.assertIsNone(response.updated_at)
        self.assertEqual(response.answers.get(question=self.mood).value, "4")

    def test_expired_submission_does_not_create_a_receipt(self):
        form = self.response_form()
        Survey.objects.filter(pk=self.survey.pk).update(closes_at=timezone.now() - datetime.timedelta(seconds=1))
        with self.assertRaises(SurveyChanged):
            form.save()
        self.assertFalse(Participation.objects.exists())

    def test_first_response_protects_choices_from_an_already_valid_editor(self):
        edit = self.unlocked_question_form(self.kinds)
        response = self.response_form().save()
        with self.assertRaisesRegex(SurveyChanged, "Someone has answered"):
            edit.save()
        self.kinds.refresh_from_db()
        self.assertEqual(self.kinds.prompt, "Which events?")
        self.assertEqual(list(response.answers.get(question=self.kinds).choices.all()), [self.pub])
        self.assertEqual(list(self.kinds.choices.all()), [self.pub, self.walk])

    def test_first_response_protects_against_an_already_valid_new_question(self):
        edit = self.unlocked_question_form()
        self.response_form().save()
        with self.assertRaises(SurveyChanged):
            edit.save()
        self.assertEqual(self.survey.questions.count(), 3)

    def test_changed_questions_reject_a_stale_submission_without_a_receipt(self):
        form = self.response_form()
        self.unlocked_question_form(self.kinds).save()
        with self.assertRaisesRegex(SurveyChanged, "questions have changed"):
            form.save()
        self.assertFalse(Response.objects.exists())
        self.assertFalse(Participation.objects.exists())

    def test_new_required_question_rejects_a_previously_valid_submission(self):
        form = self.response_form()
        Question.objects.create(survey=self.survey, prompt="Required now", kind="yesno")
        with self.assertRaises(SurveyChanged):
            form.save()
        self.assertFalse(Response.objects.exists())

    def test_anonymous_choice_is_never_silently_changed_to_named(self):
        form = self.response_form()
        Survey.objects.filter(pk=self.survey.pk).update(allow_anonymous=False)
        with self.assertRaisesRegex(SurveyChanged, "no longer accepts anonymous"):
            form.save()
        self.survey.refresh_from_db()
        # Also protect a form rendered before the setting changed but posted later.
        repost = self.response_form()
        with self.assertRaises(SurveyChanged):
            repost.save()
        self.assertFalse(Response.objects.exists())
        self.assertFalse(Participation.objects.exists())

    def test_existing_anonymous_response_cannot_be_edited_or_timestamped(self):
        response = self.response_form().save()
        edit = self.response_form(existing=response)
        with self.assertRaises(SurveyChanged):
            edit.save()
        response.refresh_from_db()
        self.assertIsNone(response.respondent_id)
        self.assertIsNone(response.submitted_at)
        self.assertIsNone(response.updated_at)
        self.assertEqual(response.answers.get(question=self.mood).value, "4")

    def test_response_failure_rolls_back_receipt_and_all_answers(self):
        form = self.response_form()
        with patch.object(Answer, "save", side_effect=RuntimeError("failed write")):
            with self.assertRaises(RuntimeError):
                form.save()
        self.assertFalse(Response.objects.exists())
        self.assertFalse(Participation.objects.exists())

    def test_stale_due_instance_cannot_close_a_rescheduled_survey(self):
        self.survey.closes_at = timezone.now() - datetime.timedelta(hours=1)
        self.survey.save()
        stale = Survey.objects.get(pk=self.survey.pk)
        future = timezone.now() + datetime.timedelta(days=1)
        Survey.objects.filter(pk=self.survey.pk).update(closes_at=future)
        self.assertFalse(stale.resolve_if_due())
        self.survey.refresh_from_db()
        self.assertEqual(self.survey.status, Survey.Status.OPEN)
        self.assertEqual(self.survey.closes_at, future)

    def test_repeat_close_preserves_original_closed_time(self):
        self.survey.close()
        closed_at = self.survey.closed_at
        self.assertFalse(self.survey.close())
        self.assertEqual(self.survey.closed_at, closed_at)

    def test_dashboard_rechecks_a_due_survey_rescheduled_after_the_due_query(self):
        self.client.force_login(self.member)
        Survey.objects.filter(pk=self.survey.pk).update(
            closes_at=timezone.now() - datetime.timedelta(hours=1),
        )
        future = timezone.now() + datetime.timedelta(days=1)

        def reschedule_after_due_query():
            due = list(Survey.objects.filter(pk=self.survey.pk))
            Survey.objects.filter(pk=self.survey.pk).update(closes_at=future)
            return due

        with patch.object(Survey.objects, "due", side_effect=reschedule_after_due_query):
            response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, self.survey.title)
        self.survey.refresh_from_db()
        self.assertEqual(self.survey.status, Survey.Status.OPEN)
        self.assertEqual(self.survey.closes_at, future)
        self.assertIsNone(self.survey.closed_at)

    def test_stale_reopen_preserves_current_schedule(self):
        self.survey.close()
        stale = Survey.objects.get(pk=self.survey.pk)
        future = timezone.now() + datetime.timedelta(days=1)
        Survey.objects.filter(pk=self.survey.pk).update(closes_at=future)
        stale.reopen()
        self.survey.refresh_from_db()
        self.assertEqual(self.survey.closes_at, future)
        self.assertTrue(self.survey.is_live)

    def test_submission_race_gets_a_clear_page_instead_of_an_error(self):
        self.client.force_login(self.member)
        save = ResponseForm.save
        def close_then_save(form):
            Survey.objects.get(pk=form.survey.pk).close()
            return save(form)
        with patch.object(ResponseForm, "save", close_then_save):
            response = self.client.post(self.url, self.answers(identity="anonymous"), follow=True)
        self.assertContains(response, "Your answers were not saved")
        self.assertContains(response, "This survey has closed")
        self.assertFalse(Participation.objects.exists())

    def test_low_level_admin_cannot_bypass_question_and_state_guards(self):
        request = RequestFactory().post("/dj-admin/")
        for model, admin_class, obj in ((Survey, SurveyAdmin, self.survey), (Question, QuestionAdmin, self.kinds)):
            site = admin_class(model, admin.site)
            self.assertFalse(site.has_add_permission(request))
            self.assertFalse(site.has_change_permission(request, obj))
            self.assertFalse(site.has_delete_permission(request, obj))
        self.assertIn(reverse("surveys:edit", args=[self.survey.slug]), SurveyAdmin(Survey, admin.site).manage_survey(self.survey))


@skipUnlessDBFeature("has_select_for_update")
class SurveyConcurrencyTests(TransactionTestCase):
    setUp = SurveyTestCase.setUp
    answers = SurveyTestCase.answers

    def test_first_response_and_choice_edit_cannot_destroy_recorded_choices(self):
        response_form = ResponseForm(self.survey, self.answers(identity="anonymous"), user=self.member)
        edit = QuestionForm(
            {"prompt": "New choices", "kind": "multi", "choices_text": "Lunch\nDinner"},
            survey=self.survey, instance=self.kinds,
        )
        self.assertTrue(response_form.is_valid(), response_form.errors)
        self.assertTrue(edit.is_valid(), edit.errors)
        barrier = Barrier(2, timeout=15)

        def run(form):
            close_old_connections()
            try:
                barrier.wait()
                try:
                    form.save()
                    return True
                except SurveyChanged:
                    return False
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, form) for form in (response_form, edit)]
            answered, edited = [future.result(timeout=30) for future in futures]
        self.assertNotEqual(answered, edited)
        self.assertEqual(Response.objects.count(), int(answered))
        self.assertEqual(Participation.objects.count(), int(answered))
        if answered:
            self.assertEqual(list(Answer.objects.get(question=self.kinds).choices.all()), [self.pub])
        else:
            self.assertEqual(list(self.kinds.choices.values_list("label", flat=True)), ["Lunch", "Dinner"])
