"""Surveys: the committee asks the membership a set of questions.

Each survey is run by its own admins (any members the committee names) and
sits on the Surveys page while open, then stays there closed. Members
choose, per survey, whether to answer with their name or anonymously.

Anonymity is built into the data, not promised by a policy. Who has
answered is recorded in ``Participation`` (so one answer per member, and
reminders, work); what they said is in ``Response``. An anonymous response
has no member on it and records no time at all, and every row that makes
up a response (the response, its answers, the choices picked) is keyed by
a random UUID rather than a running number, so neither a timestamp nor a
sequence can line it up with the participation list. A member who is
deleted takes their name with them: their named responses become
anonymous. (SQLite, used only for development, still keeps a hidden row
number on every table; MySQL on the live site does not.)
"""

import hashlib
import uuid
from collections import Counter, defaultdict

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

RESERVED_SLUGS = {"new"}  # routes under /surveys/ that are not surveys


def anonymous_order(question, response):
    """Where an anonymous answer sits in a question's list: a hash of the
    question and the response, so every question lists them in a different
    order and one member's answers can't be lined up across questions."""
    return hashlib.sha256(f"{question.pk}:{response.pk}".encode()).hexdigest()


class SurveyQuerySet(models.QuerySet):
    def live(self):
        """Taking answers right now."""
        now = timezone.now()
        return (
            self.filter(status=Survey.Status.OPEN)
            .filter(Q(opens_at__isnull=True) | Q(opens_at__lte=now))
            .filter(Q(closes_at__isnull=True) | Q(closes_at__gt=now))
        )

    def scheduled(self):
        return self.filter(status=Survey.Status.OPEN, opens_at__gt=timezone.now())

    def due(self):
        """Open surveys whose closing time has passed."""
        return self.filter(status=Survey.Status.OPEN, closes_at__lte=timezone.now())

    def visible_to(self, user):
        """Open and closed surveys, plus the drafts this member runs."""
        if user.is_authenticated and user.is_portal_admin:
            return self.all()
        published = ~Q(status=Survey.Status.DRAFT)
        if user.is_authenticated:
            return self.filter(published | Q(admins=user)).distinct()
        return self.filter(published)

    def for_dashboard(self, user):
        """Live surveys this member hasn't answered yet, soonest to close first."""
        answered = Participation.objects.filter(user=user).values("survey_id")
        return (
            self.live()
            .exclude(pk__in=answered)
            .order_by(F("closes_at").asc(nulls_last=True), "-created_at")
        )


class Survey(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    class Results(models.TextChoices):
        ADMINS = "admins", "Only the people running the survey"
        RESPONDENTS = "respondents", "Members who have answered"
        EVERYONE = "everyone", "Every member"

    title = models.CharField(max_length=140)
    slug = models.SlugField(max_length=150, unique=True, blank=True)
    intro = models.TextField(
        blank=True,
        help_text="Shown above the questions: what this is for and what happens with the answers. Markdown works.",
    )
    admins = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="surveys_run",
        help_text="The members who run this survey: they edit it, open and close it, "
                  "see the results and send reminders. Society admins always can.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="surveys_created",
    )
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    opens_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Leave blank to open as soon as you press Open.",
    )
    closes_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Leave blank to keep it open until you close it.",
    )
    allow_anonymous = models.BooleanField(
        default=True, help_text="Let members answer without their name.",
    )
    results_visibility = models.CharField(
        max_length=12, choices=Results.choices, default=Results.ADMINS,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    last_reminded_at = models.DateTimeField(null=True, blank=True)

    objects = SurveyQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:120] or "survey"
            slug, n = base, 2
            while slug in RESERVED_SLUGS or Survey.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("surveys:detail", args=[self.slug])

    # --- state --------------------------------------------------------------

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    @property
    def is_scheduled(self):
        return self.status == self.Status.OPEN and self.opens_at is not None and self.opens_at > timezone.now()

    @property
    def is_live(self):
        if self.status != self.Status.OPEN or self.is_scheduled:
            return False
        return self.closes_at is None or self.closes_at > timezone.now()

    @property
    def is_closed(self):
        """Closed by hand, or open with a closing time that has passed."""
        if self.status == self.Status.CLOSED:
            return True
        return self.status == self.Status.OPEN and self.closes_at is not None and self.closes_at <= timezone.now()

    @property
    def ended_at(self):
        return self.closed_at or self.closes_at

    def resolve_if_due(self):
        if self.status == self.Status.OPEN and self.closes_at and self.closes_at <= timezone.now():
            self.close()

    def close(self):
        self.status = self.Status.CLOSED
        self.closed_at = timezone.now()
        self.save(update_fields=["status", "closed_at", "updated_at"])

    def reopen(self):
        """Back to taking answers; a closing time already past is dropped."""
        self.status = self.Status.OPEN
        self.closed_at = None
        if self.closes_at and self.closes_at <= timezone.now():
            self.closes_at = None
        self.save(update_fields=["status", "closed_at", "closes_at", "updated_at"])

    # --- people -------------------------------------------------------------

    def can_manage(self, user):
        if user is None or not user.is_authenticated:
            return False
        return user.is_portal_admin or any(a.pk == user.pk for a in self.admins.all())

    def has_answered(self, user):
        return user.is_authenticated and self.participations.filter(user=user).exists()

    def named_response_for(self, user):
        if not user.is_authenticated:
            return None
        return self.responses.filter(respondent=user).first()

    def can_see_results(self, user):
        if self.can_manage(user):
            return True
        if not user.is_authenticated:
            return False
        if self.results_visibility == self.Results.EVERYONE:
            return True
        if self.results_visibility == self.Results.RESPONDENTS:
            return self.has_answered(user)
        return False

    @property
    def is_locked(self):
        """Once anyone has answered, questions and choices can be reworded
        but not added or removed: the answers would stop making sense."""
        return self.responses.exists()

    @property
    def response_count(self):
        return self.responses.count()

    def runners(self):
        return self.admins.filter(is_banned=False).order_by("last_name", "first_name")

    # --- results ------------------------------------------------------------

    def results(self):
        """One entry per question: option counts and shares, a distribution
        and mean for scales, yes/no tallies, or the free-text answers. Named
        answers carry the member; anonymous ones come in an order that is
        different for every question and says nothing about who or when."""
        questions = list(self.questions.prefetch_related("choices"))
        responses = {r.pk: r for r in self.responses.select_related("respondent")}
        by_question = defaultdict(list)
        for answer in Answer.objects.filter(response__survey=self).prefetch_related("choices"):
            by_question[answer.question_id].append(answer)
        out = []
        for question in questions:
            rows = by_question.get(question.pk, [])
            item = {"question": question, "answered": len(rows), "kind": question.kind}

            def share(n):
                return round(100 * n / len(rows)) if rows else 0

            if question.has_choices:
                counts = Counter()
                for answer in rows:
                    for choice in answer.choices.all():
                        counts[choice.pk] += 1
                item["options"] = [
                    {"label": c.label, "n": counts[c.pk], "pct": share(counts[c.pk])}
                    for c in question.choices.all()
                ]
            elif question.kind == Question.Kind.SCALE:
                counts = Counter(a.value for a in rows if a.value)
                item["options"] = [
                    {"label": str(i), "n": counts[str(i)], "pct": share(counts[str(i)])} for i in range(1, 6)
                ]
                scored = [int(v) for v, n in counts.items() for _ in range(n)]
                item["mean"] = round(sum(scored) / len(scored), 1) if scored else None
            elif question.kind == Question.Kind.YESNO:
                counts = Counter(a.value for a in rows)
                item["options"] = [
                    {"label": "Yes", "n": counts["yes"], "pct": share(counts["yes"])},
                    {"label": "No", "n": counts["no"], "pct": share(counts["no"])},
                ]
            else:
                named, anonymous = [], []
                for answer in rows:
                    text = answer.text.strip()
                    if not text:
                        continue
                    response = responses[answer.response_id]
                    if response.is_anonymous:
                        anonymous.append((text, None, anonymous_order(question, response)))
                    else:
                        who = response.respondent
                        named.append((text, who, (who.last_name, who.first_name) if who else ("", "")))
                named.sort(key=lambda row: row[2])
                anonymous.sort(key=lambda row: row[2])
                item["texts"] = [(t, who) for t, who, _ in named] + [(t, None) for t, _, _ in anonymous]
            out.append(item)
        return out


CHOICE_KINDS = ("single", "multi", "dropdown")


class Question(models.Model):
    class Kind(models.TextChoices):
        SHORT = "short", "Short answer"
        LONG = "long", "Paragraph"
        SINGLE = "single", "Choose one"
        MULTI = "multi", "Choose any that apply"
        DROPDOWN = "dropdown", "Dropdown list"
        SCALE = "scale", "Scale of 1 to 5"
        YESNO = "yesno", "Yes or no"

    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="questions")
    prompt = models.CharField(max_length=200)
    help_text = models.CharField(
        max_length=300, blank=True, help_text="A line under the question, if it needs one.",
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.SINGLE)
    required = models.BooleanField(default=True)
    scale_low = models.CharField(
        max_length=40, blank=True, help_text="What 1 means, e.g. “Not at all”.",
    )
    scale_high = models.CharField(
        max_length=40, blank=True, help_text="What 5 means, e.g. “Very much”.",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "pk"]

    def __str__(self):
        return self.prompt

    @property
    def has_choices(self):
        return self.kind in CHOICE_KINDS

    @property
    def is_text(self):
        return self.kind in (self.Kind.SHORT, self.Kind.LONG)

    @property
    def picks_many(self):
        return self.kind == self.Kind.MULTI


class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    label = models.CharField(max_length=140)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "pk"]

    def __str__(self):
        return self.label


class Response(models.Model):
    """One member's answers. An anonymous response has no respondent and no
    time; every row of it is keyed by a random id."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="responses")
    respondent = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="survey_responses",
    )
    is_anonymous = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(null=True, blank=True)  # never set for anonymous responses
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # Plain, not conditional: MySQL would silently skip a conditional
            # one. NULL respondents (anonymous) never collide in a unique index.
            models.UniqueConstraint(
                fields=["survey", "respondent"], name="surveys_one_named_response_per_member",
            ),
        ]

    def __str__(self):
        return f"{'Anonymous' if self.is_anonymous else self.respondent} on {self.survey}"

    @property
    def who(self):
        """The member to show with the answers, or None for anonymous."""
        return None if self.is_anonymous else self.respondent


class Answer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    response = models.ForeignKey(Response, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    text = models.TextField(blank=True)
    value = models.CharField(max_length=20, blank=True)  # scale "1"-"5"; yes/no
    choices = models.ManyToManyField(Choice, through="AnswerChoice", blank=True, related_name="answers")

    class Meta:
        unique_together = [("response", "question")]

    def __str__(self):
        return f"Answer to {self.question_id}"

    def display(self):
        """The answer as text, for exports and listings."""
        if self.question.has_choices:
            return "; ".join(c.label for c in self.choices.all())
        if self.question.kind == Question.Kind.YESNO:
            return {"yes": "Yes", "no": "No"}.get(self.value, "")
        if self.question.kind == Question.Kind.SCALE:
            return self.value
        return self.text


class AnswerChoice(models.Model):
    """A choice picked in an answer; its own random key, so the order the
    picks were saved in is not recorded either."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE)
    choice = models.ForeignKey(Choice, on_delete=models.CASCADE)

    class Meta:
        unique_together = [("answer", "choice")]


class Participation(models.Model):
    """Who has answered a survey. Kept apart from the responses on purpose:
    it lets the site stop a second answer and send reminders without ever
    knowing which anonymous response is whose."""

    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="participations")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="survey_participations",
    )
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("survey", "user")]

    def __str__(self):
        return f"{self.user} answered {self.survey}"


def forget_member(sender, instance, **kwargs):
    """A member being deleted takes their name with them: their named
    responses become anonymous rather than dangling. Connected to the
    user model's pre_delete signal in apps.py."""
    Response.objects.filter(respondent=instance).update(
        respondent=None, is_anonymous=True, submitted_at=None, updated_at=None,
    )
