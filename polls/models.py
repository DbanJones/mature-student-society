"""Polls: a question with options, a closing time, and an outcome.

Four kinds share the mechanism:

- ``venue`` and ``date`` polls belong to an event. When they close, the
  winning option is written back to the event (its location, or its start
  with the end shifted by the original duration) and everyone going is told.
- ``general`` polls are the committee asking the membership something.
- ``volunteers`` polls are rotas: each option is a slot with a capacity, and
  the votes are sign-ups. The host sees a roster with contact details under
  the same privacy rule as an event's attendee export.

Polls close lazily (``resolve_if_due`` runs whenever one is loaded) and from
the ``close_polls`` management command on cron.
"""

from django.conf import settings
from django.db import models
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

MAX_OPTIONS = 8


class PollQuerySet(models.QuerySet):
    def open(self):
        return self.filter(status=Poll.Status.OPEN)

    def due(self):
        return self.open().filter(closes_at__lte=timezone.now())

    def for_dashboard(self, user):
        """Open polls this member can vote in and hasn't yet: general polls,
        plus polls on events they're going to or hosting."""
        from events.models import RSVP

        going = RSVP.objects.filter(user=user, status=RSVP.Status.GOING).values("event")
        return (
            self.open()
            .filter(
                Q(event__isnull=True)
                | Q(event__in=going) | Q(event__host=user) | Q(event__created_by=user)
            )
            .exclude(votes__user=user)
            .select_related("event")
            .order_by("closes_at")
        )


class Poll(models.Model):
    class Kind(models.TextChoices):
        VENUE = "venue", "Where should it be?"
        DATE = "date", "When should it be?"
        GENERAL = "general", "A question for members"
        VOLUNTEERS = "volunteers", "Volunteers needed"

    class Results(models.TextChoices):
        ALWAYS = "always", "Always visible"
        AFTER_VOTE = "after_vote", "After you have voted"
        AFTER_CLOSE = "after_close", "Only once the poll closes"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    event = models.ForeignKey(
        "events.Event", null=True, blank=True, on_delete=models.CASCADE,
        related_name="polls",
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    question = models.CharField(max_length=140)
    description = models.TextField(
        blank=True, help_text="Optional context. Markdown supported.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="polls_created",
    )
    closes_at = models.DateTimeField(
        help_text="Voting stops at this time. Venue and date polls then set "
                  "the event automatically.",
    )
    allow_multiple = models.BooleanField(
        default=False, help_text="Let people pick more than one option.",
    )
    show_results = models.CharField(
        max_length=12, choices=Results.choices, default=Results.ALWAYS,
    )
    status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.OPEN,
    )
    outcome_option = models.ForeignKey(
        "PollOption", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    needs_decision = models.BooleanField(
        default=False,
        help_text="Closed on a tie: the host must pick the winner.",
    )
    applied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PollQuerySet.as_manager()

    class Meta:
        ordering = ["closes_at"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.question} [{self.status}]"

    def get_absolute_url(self):
        return reverse("polls:detail", args=[self.pk])

    # --- state --------------------------------------------------------------

    @property
    def is_open(self):
        return self.status == self.Status.OPEN and self.closes_at > timezone.now()

    @property
    def is_rota(self):
        return self.kind == self.Kind.VOLUNTEERS

    @property
    def sets_event(self):
        return self.kind in (self.Kind.VENUE, self.Kind.DATE) and self.event_id

    def can_manage(self, user):
        """Who may close, decide or delete: the event's editors, or admins
        for a general poll."""
        if not user.is_authenticated:
            return False
        if self.event_id:
            return self.event.can_edit(user)
        return user.is_portal_admin

    def can_see_results(self, user):
        if self.status == self.Status.CLOSED or self.can_manage(user):
            return True
        if self.show_results == self.Results.ALWAYS:
            return True
        if self.show_results == self.Results.AFTER_VOTE:
            return user.is_authenticated and self.votes.filter(user=user).exists()
        return False

    def choices_of(self, user):
        if not user.is_authenticated:
            return set()
        return set(self.votes.filter(user=user).values_list("option_id", flat=True))

    def results(self):
        """Options with vote counts, share of the top count, and voters."""
        options = list(
            self.options.annotate(n=Count("votes")).prefetch_related("votes__user")
        )
        top = max((o.n for o in options), default=0)
        for option in options:
            option.pct = int(round(100 * option.n / top)) if top else 0
            option.voters = [v.user for v in option.votes.all()]
        return options

    def leaders(self):
        """The option(s) with the most votes, ignoring opt-outs and zero."""
        counted = [
            o for o in self.options.annotate(n=Count("votes"))
            if not o.is_opt_out and o.n > 0
        ]
        if not counted:
            return []
        top = max(o.n for o in counted)
        return [o for o in counted if o.n == top]

    # --- closing ------------------------------------------------------------

    def resolve_if_due(self):
        if self.status == self.Status.OPEN and self.closes_at <= timezone.now():
            self.close()

    def close(self, by=None, option=None):
        """Close the poll. ``option`` forces the outcome (a host deciding a
        tie); otherwise the single leader wins, and a tie waits."""
        from .services import apply_outcome

        self.status = self.Status.CLOSED
        winner = option
        if winner is None:
            leaders = self.leaders()
            if len(leaders) == 1:
                winner = leaders[0]
            elif len(leaders) > 1 and self.sets_event:
                self.needs_decision = True
        if winner is not None:
            self.outcome_option = winner
            self.needs_decision = False
        self.save(update_fields=["status", "outcome_option", "needs_decision"])
        if winner is not None and self.sets_event and self.applied_at is None:
            apply_outcome(self, by)
        return winner


class PollOption(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="options")
    label = models.CharField(max_length=140)
    start = models.DateTimeField(
        null=True, blank=True, help_text="Date polls: the proposed start.",
    )
    capacity = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Volunteer slots: how many people are needed.",
    )
    is_opt_out = models.BooleanField(
        default=False, help_text="“None of these” — never wins.",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.label

    @property
    def is_full(self):
        return self.capacity is not None and self.votes.count() >= self.capacity


class PollVote(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="votes")
    option = models.ForeignKey(
        PollOption, on_delete=models.CASCADE, related_name="votes"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="poll_votes"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("option", "user")]
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.user} → {self.option}"
