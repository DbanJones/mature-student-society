"""Surveys: who runs them, who sees them, answering with a name or
anonymously, and what the results give away."""

import datetime

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from notifications.models import Notification
from panel.models import AuditLog

from .forms import AlreadyAnswered, ResponseForm
from .models import Answer, Participation, Question, Response, Survey


def make_user(username, **extra):
    defaults = dict(
        first_name="Test", last_name=username.title(), college="wolfson",
        mobile="+447700900000", email=f"{username}@cam.ac.uk", crsid=username,
    )
    defaults.update(extra)
    return User.objects.create_user(username=username, **defaults)


class SurveyTestCase(TestCase):
    def setUp(self):
        self.admin = make_user("adm1", is_portal_admin=True)
        self.runner = make_user("run1")     # runs the survey, not a society admin
        self.member = make_user("mem1")
        self.other = make_user("oth1")
        self.survey = Survey.objects.create(
            title="Term check", intro="How is it going?", created_by=self.admin,
            status=Survey.Status.OPEN, results_visibility=Survey.Results.ADMINS,
        )
        self.survey.admins.add(self.runner)
        self.mood = Question.objects.create(
            survey=self.survey, prompt="How is term?", kind="scale", sort_order=1,
        )
        self.kinds = Question.objects.create(
            survey=self.survey, prompt="Which events?", kind="multi", sort_order=2, required=False,
        )
        self.pub, self.walk = (self.kinds.choices.create(label=label, sort_order=i) for i, label in enumerate(["Pub", "Walk"]))
        self.more = Question.objects.create(
            survey=self.survey, prompt="Anything else?", kind="long", sort_order=3, required=False,
        )
        self.url = self.survey.get_absolute_url()

    def answers(self, **extra):
        data = {f"q{self.mood.pk}": "4", f"q{self.kinds.pk}": [str(self.pub.pk)], f"q{self.more.pk}": "More walks please"}
        data.update(extra)
        return data


class RunningTests(SurveyTestCase):
    def test_only_society_admins_create_and_each_survey_has_its_own_admins(self):
        self.client.force_login(self.runner)
        self.assertEqual(self.client.get(reverse("surveys:create")).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("surveys:create"), {
            "title": "Lent plans", "intro": "", "admins": [self.runner.pk, self.member.pk],
            "results_visibility": "everyone", "allow_anonymous": "on",
        })
        survey = Survey.objects.get(title="Lent plans")
        self.assertRedirects(response, survey.get_absolute_url())
        self.assertEqual(survey.status, Survey.Status.DRAFT)
        self.assertEqual(set(survey.admins.all()), {self.runner, self.member})
        self.assertTrue(AuditLog.objects.filter(action="create_survey", target="Lent plans").exists())
        # The people named run it; other members don't, and can't tell the draft exists.
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("surveys:edit", args=[survey.slug])).status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("surveys:edit", args=[survey.slug])).status_code, 404)
        self.assertEqual(self.client.get(reverse("surveys:edit", args=[self.survey.slug])).status_code, 403)
        # Only society admins choose who runs a survey.
        self.client.force_login(self.runner)
        self.client.post(reverse("surveys:edit", args=[self.survey.slug]), {
            "title": "Term check (renamed)", "intro": "", "admins": [self.other.pk],
            "results_visibility": "admins", "allow_anonymous": "on",
        })
        self.survey.refresh_from_db()
        self.assertEqual(self.survey.title, "Term check (renamed)")
        self.assertEqual(list(self.survey.admins.all()), [self.runner])

    def test_a_survey_called_new_does_not_collide_with_the_create_page(self):
        survey = Survey.objects.create(title="New", created_by=self.admin)
        self.assertEqual(survey.slug, "new-2")

    def test_drafts_are_hidden_until_opened_and_opening_tells_everyone_once(self):
        draft = Survey.objects.create(title="Secret draft", created_by=self.admin)
        draft.admins.add(self.runner)
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(reverse("surveys:index")), "Secret draft")
        self.assertEqual(self.client.get(draft.get_absolute_url()).status_code, 404)
        self.client.force_login(self.runner)
        self.assertContains(self.client.get(reverse("surveys:index")), "Secret draft")
        open_url = reverse("surveys:open", args=[draft.slug])
        self.assertContains(self.client.post(open_url, follow=True), "Add at least one question")
        Question.objects.create(survey=draft, prompt="Well?", kind="yesno")
        draft.closes_at = timezone.now() - datetime.timedelta(hours=1)
        draft.save()
        self.assertContains(self.client.post(open_url, follow=True), "closing time has already passed")
        draft.closes_at = None
        draft.save()
        self.client.post(open_url)
        draft.refresh_from_db()
        self.assertTrue(draft.is_live)
        told = Notification.objects.filter(kind="survey", url=draft.get_absolute_url())
        self.assertEqual({n.recipient for n in told}, {self.admin, self.member, self.other})  # not the opener
        self.client.post(open_url)  # pressing Open again tells nobody twice
        self.assertEqual(Notification.objects.filter(kind="survey", url=draft.get_absolute_url()).count(), 3)
        self.client.force_login(self.member)
        page = self.client.get(reverse("surveys:index"))
        self.assertContains(page, "Secret draft")
        self.assertContains(page, "Open now")

    def test_questions_can_be_built_reordered_and_lock_once_answered(self):
        self.client.force_login(self.runner)
        add = reverse("surveys:question_add", args=[self.survey.slug])
        response = self.client.post(add, {"prompt": "Favourite pub?", "kind": "single", "choices_text": "Eagle\n\nEagle \nPickerel", "required": "on"})
        self.assertRedirects(response, self.url + "#questions", fetch_redirect_response=False)
        question = self.survey.questions.get(prompt="Favourite pub?")
        eagle, pickerel = question.choices.all()
        self.assertEqual([eagle.label, pickerel.label], ["Eagle", "Pickerel"])  # blank and duplicate lines dropped
        self.assertEqual(question.sort_order, 4)
        self.assertContains(self.client.post(add, {"prompt": "One choice", "kind": "single", "choices_text": "Only"}), "at least two choices")
        move = reverse("surveys:question_move", args=[self.survey.slug, question.pk])
        for _ in range(3):
            self.client.post(move, {"direction": "up"})
        self.assertEqual(list(self.survey.questions.values_list("prompt", flat=True))[0], "Favourite pub?")
        # A member answers: the structure locks, wording stays editable per choice.
        self.client.force_login(self.member)
        self.client.post(self.url, self.answers(**{f"q{question.pk}": str(eagle.pk), "identity": "named"}))
        self.assertEqual(self.survey.responses.count(), 1)
        self.client.force_login(self.runner)
        self.assertContains(self.client.post(add, {"prompt": "Late", "kind": "short"}, follow=True), "can&#x27;t be added")
        self.client.post(reverse("surveys:question_delete", args=[self.survey.slug, question.pk]))
        self.assertTrue(Question.objects.filter(pk=question.pk).exists())
        edit = reverse("surveys:question_edit", args=[self.survey.slug, question.pk])
        page = self.client.get(edit)
        self.assertContains(page, f'name="choice_{eagle.pk}"')
        self.assertNotContains(page, 'name="choices_text"')
        self.client.post(edit, {"prompt": "Best pub?", "kind": "single", f"choice_{eagle.pk}": "The Eagle", f"choice_{pickerel.pk}": "The Pickerel"})
        question.refresh_from_db()
        eagle.refresh_from_db()
        self.assertEqual(question.prompt, "Best pub?")
        self.assertEqual(eagle.label, "The Eagle")
        self.assertEqual(list(Answer.objects.get(question=question).choices.all()), [eagle])  # still the member's pick
        self.assertContains(self.client.post(edit, {"prompt": "Best pub?", "kind": "single", f"choice_{eagle.pk}": "Same", f"choice_{pickerel.pk}": "same"}), "same wording")
        too_long = self.client.post(edit, {"prompt": "Best pub?", "kind": "single", f"choice_{eagle.pk}": "x" * 141, f"choice_{pickerel.pk}": "The Pickerel"})
        self.assertEqual(len(too_long.context["form"].errors[f"choice_{eagle.pk}"]), 1)
        self.assertTrue(AuditLog.objects.filter(action="reword_survey_question", target="Term check").exists())

    def test_an_open_survey_keeps_its_last_question(self):
        for question in (self.kinds, self.more):
            self.client.force_login(self.runner)
            self.client.post(reverse("surveys:question_delete", args=[self.survey.slug, question.pk]))
        self.assertEqual(self.survey.questions.count(), 1)
        self.client.post(reverse("surveys:question_delete", args=[self.survey.slug, self.mood.pk]))
        self.assertEqual(self.survey.questions.count(), 1)
        self.survey.status = Survey.Status.DRAFT
        self.survey.save()
        self.client.post(reverse("surveys:question_delete", args=[self.survey.slug, self.mood.pk]))
        self.assertEqual(self.survey.questions.count(), 0)

    def test_a_live_survey_cannot_be_rescheduled_into_the_future(self):
        self.client.force_login(self.runner)
        response = self.client.post(reverse("surveys:edit", args=[self.survey.slug]), {
            "title": "Term check", "intro": "", "results_visibility": "admins", "allow_anonymous": "on",
            "opens_at": (timezone.localtime() + datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
        })
        self.assertContains(response, "already open")
        self.survey.refresh_from_db()
        self.assertIsNone(self.survey.opens_at)

    def test_closing_reopening_deleting_and_the_cron(self):
        self.client.force_login(self.member)
        self.client.post(self.url, self.answers(identity="named"))
        self.client.force_login(self.runner)
        self.client.post(reverse("surveys:close", args=[self.survey.slug]))
        self.survey.refresh_from_db()
        self.assertTrue(self.survey.is_closed)
        self.client.force_login(self.member)
        self.assertContains(self.client.get(reverse("surveys:index")), "Closed")
        self.assertContains(self.client.post(self.url, self.answers(), follow=True), "isn&#x27;t taking answers")
        self.client.force_login(self.runner)
        self.client.post(reverse("surveys:open", args=[self.survey.slug]))
        self.survey.refresh_from_db()
        self.assertTrue(self.survey.is_live)
        reopened = Notification.objects.filter(kind="survey", text__startswith="Reopened")
        self.assertEqual({n.recipient for n in reopened}, {self.admin, self.other})  # not the member who answered
        # A closing time in the past closes it: lazily on a visit, and from cron.
        self.survey.closes_at = timezone.now() - datetime.timedelta(minutes=1)
        self.survey.save()
        call_command("close_polls")
        self.survey.refresh_from_db()
        self.assertEqual(self.survey.status, Survey.Status.CLOSED)
        self.client.post(reverse("surveys:delete", args=[self.survey.slug]))
        self.assertFalse(Survey.objects.filter(pk=self.survey.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="delete_survey").exists())


class AnsweringTests(SurveyTestCase):
    def test_a_named_answer_is_recorded_and_can_be_changed(self):
        self.client.force_login(self.member)
        response = self.client.post(self.url, self.answers(identity="named"), follow=True)
        self.assertContains(response, "your answers have been sent")
        answer = Response.objects.get()
        self.assertEqual(answer.respondent, self.member)
        self.assertFalse(answer.is_anonymous)
        self.assertIsNotNone(answer.submitted_at)
        self.assertTrue(Participation.objects.filter(survey=self.survey, user=self.member).exists())
        self.assertEqual(answer.answers.get(question=self.mood).value, "4")
        self.assertEqual(list(answer.answers.get(question=self.kinds).choices.all()), [self.pub])
        page = self.client.get(self.url)
        self.assertContains(page, "Update my answers")
        self.client.post(self.url, self.answers(**{f"q{self.mood.pk}": "2", f"q{self.kinds.pk}": [str(self.walk.pk)]}))
        self.assertEqual(Response.objects.count(), 1)
        answer.refresh_from_db()
        self.assertEqual(answer.answers.get(question=self.mood).value, "2")
        self.assertIsNotNone(answer.updated_at)

    def test_an_anonymous_answer_has_no_link_to_the_member_and_is_final(self):
        self.client.force_login(self.member)
        self.client.post(self.url, self.answers(identity="anonymous"))
        answer = Response.objects.get()
        self.assertIsNone(answer.respondent)
        self.assertTrue(answer.is_anonymous)
        self.assertIsNone(answer.submitted_at)  # no record of when at all
        self.assertTrue(Participation.objects.filter(survey=self.survey, user=self.member).exists())
        page = self.client.get(self.url)
        self.assertContains(page, "answered this survey anonymously")
        self.assertNotContains(page, "Send my answers")
        self.assertContains(self.client.post(self.url, self.answers(), follow=True), "can&#x27;t be changed")
        self.assertEqual(Response.objects.count(), 1)
        # Nothing on the results page or in the CSV names them, or dates them.
        self.client.force_login(self.runner)
        results = self.client.get(reverse("surveys:results", args=[self.survey.slug]))
        self.assertContains(results, "More walks please")
        self.assertContains(results, "Anonymous")
        answers_html = results.content.decode().split('<ul class="answers">')[1].split("</ul>")[0]
        self.assertNotIn("Mem1", answers_html)
        self.assertNotIn("Mem1", results.content.decode())  # the page never says who answered
        self.assertContains(results, "1 member answered")
        csv_url = reverse("surveys:results", args=[self.survey.slug]) + "?format=csv"
        self.assertEqual(self.client.get(csv_url).status_code, 302)  # not while answers are still coming in
        self.survey.close()
        csv_lines = self.client.get(csv_url).content.decode().splitlines()
        self.assertEqual(csv_lines[1].split(",")[:2], ["", "Anonymous"])
        self.assertNotIn("Mem1", "\n".join(csv_lines))

    def test_two_submissions_at_once_leave_one_answer(self):
        first = ResponseForm(self.survey, self.answers(identity="anonymous"), user=self.member)
        second = ResponseForm(self.survey, self.answers(identity="named"), user=self.member)
        self.assertTrue(first.is_valid() and second.is_valid())
        first.save()
        with self.assertRaises(AlreadyAnswered):
            second.save()
        self.assertEqual(Response.objects.count(), 1)
        self.assertEqual(Participation.objects.count(), 1)

    def test_a_deleted_member_takes_their_name_with_them(self):
        self.client.force_login(self.member)
        self.client.post(self.url, self.answers(identity="named"))
        self.member.delete()
        answer = Response.objects.get()
        self.assertIsNone(answer.respondent)
        self.assertTrue(answer.is_anonymous)
        self.assertIsNone(answer.submitted_at)
        self.assertEqual(answer.answers.get(question=self.more).text, "More walks please")
        self.assertFalse(Participation.objects.exists())

    def test_required_questions_are_enforced(self):
        self.client.force_login(self.member)
        response = self.client.post(self.url, self.answers(**{f"q{self.mood.pk}": ""}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")
        self.assertEqual(Response.objects.count(), 0)

    def test_surveys_without_anonymity_say_so_and_skip_the_choice(self):
        self.survey.allow_anonymous = False
        self.survey.save()
        self.client.force_login(self.member)
        page = self.client.get(self.url)
        self.assertNotContains(page, "Anonymously")
        self.assertContains(page, "You answer this survey with your name")
        self.client.post(self.url, self.answers())
        self.assertEqual(Response.objects.get().respondent, self.member)

    def test_results_follow_the_visibility_setting_and_csv_is_for_runners(self):
        results = reverse("surveys:results", args=[self.survey.slug])
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(results).status_code, 403)
        self.survey.results_visibility = Survey.Results.RESPONDENTS
        self.survey.save()
        self.assertEqual(self.client.get(results).status_code, 403)
        self.client.post(self.url, self.answers(identity="named"))
        self.assertEqual(self.client.get(results).status_code, 200)
        self.assertEqual(self.client.get(results + "?format=csv").status_code, 403)
        self.survey.results_visibility = Survey.Results.EVERYONE
        self.survey.save()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(results).status_code, 200)
        self.client.force_login(self.runner)
        self.assertEqual(self.client.get(results + "?format=csv").status_code, 302)  # still open
        self.survey.close()
        self.assertEqual(self.client.get(results + "?format=csv")["Content-Type"], "text/csv; charset=utf-8")

    def test_results_add_up_and_the_csv_is_spreadsheet_safe(self):
        for user, score, picks, text in ((self.member, "5", [self.pub, self.walk], "Walks"), (self.other, "3", [self.pub], ""), (self.admin, "4", [], "=1+1")):
            response = Response.objects.create(survey=self.survey, respondent=user, submitted_at=timezone.now())
            Answer.objects.create(response=response, question=self.mood, value=score)
            picked = Answer.objects.create(response=response, question=self.kinds)
            picked.choices.set(picks)
            if text:
                Answer.objects.create(response=response, question=self.more, text=text)
        results = {item["question"].pk: item for item in self.survey.results()}
        self.assertEqual(results[self.mood.pk]["mean"], 4.0)
        self.assertEqual([o["n"] for o in results[self.mood.pk]["options"]], [0, 0, 1, 1, 1])
        self.assertEqual([(o["label"], o["n"], o["pct"]) for o in results[self.kinds.pk]["options"]], [("Pub", 2, 67), ("Walk", 1, 33)])
        self.assertEqual(results[self.more.pk]["texts"], [("=1+1", self.admin), ("Walks", self.member)])
        self.client.force_login(self.runner)
        page = self.client.get(reverse("surveys:results", args=[self.survey.slug]))
        self.assertContains(page, "average 4.0 out of 5")
        self.assertContains(page, 'style="width:67%"')
        self.survey.close()
        csv_body = self.client.get(reverse("surveys:results", args=[self.survey.slug]) + "?format=csv").content.decode()
        self.assertIn("'=1+1", csv_body)

    def test_anonymous_text_answers_are_listed_in_a_different_order_per_question(self):
        from .models import anonymous_order

        college = Question.objects.create(survey=self.survey, prompt="College?", kind="short", sort_order=4, required=False)
        rows = []
        for i in range(5):
            response = Response.objects.create(survey=self.survey, is_anonymous=True)
            Answer.objects.create(response=response, question=college, text=f"College {i}")
            Answer.objects.create(response=response, question=self.more, text=f"Comment {i}")
            rows.append(response)
        results = {item["question"].pk: item for item in self.survey.results()}
        for question, word in ((college, "College"), (self.more, "Comment")):
            expected = [f"{word} {i}" for i, _ in sorted(enumerate(rows), key=lambda pair: anonymous_order(question, pair[1]))]
            self.assertEqual([t for t, _ in results[question.pk]["texts"]], expected)
        self.assertNotEqual(anonymous_order(college, rows[0]), anonymous_order(self.more, rows[0]))

    def test_reminders_go_to_those_who_have_not_answered_once_a_day(self):
        self.client.force_login(self.member)
        self.client.post(self.url, self.answers(identity="anonymous"))
        self.client.force_login(self.runner)
        self.client.post(reverse("surveys:remind", args=[self.survey.slug]))
        reminded = Notification.objects.filter(kind="survey", text__startswith="Reminder")
        self.assertEqual({n.recipient for n in reminded}, {self.admin, self.other})  # not the member, not the runner
        self.client.post(reverse("surveys:remind", args=[self.survey.slug]))
        self.assertEqual(Notification.objects.filter(kind="survey", text__startswith="Reminder").count(), 2)

    def test_dashboard_menu_and_panel_show_surveys(self):
        self.client.force_login(self.member)
        home = self.client.get(reverse("dashboard:home"))
        self.assertContains(home, "Answer the survey")
        self.assertContains(home, "Term check")
        self.assertContains(home, "📋 Surveys")
        self.assertEqual(self.client.get(reverse("panel:surveys")).status_code, 403)
        scheduled = Survey.objects.create(
            title="Lent check", created_by=None, status=Survey.Status.OPEN,
            opens_at=timezone.now() + datetime.timedelta(days=3),
        )
        scheduled.admins.add(self.runner)
        self.client.force_login(self.runner)
        home = self.client.get(reverse("dashboard:home"))
        self.assertContains(home, "Surveys you run")
        self.assertContains(home, "opens ")
        self.client.force_login(self.admin)
        panel = self.client.get(reverse("panel:surveys"))
        self.assertContains(panel, "Term check")
        self.assertContains(panel, "Test Run1")
        self.assertContains(panel, "a former member")  # a survey whose creator is gone
