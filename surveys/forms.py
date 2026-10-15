"""Forms for setting up a survey, writing its questions, and answering it."""

from django import forms
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from accounts.models import User

from .models import CHOICE_KINDS, Answer, Choice, Participation, Question, Response, Survey

DATETIME_LOCAL = "%Y-%m-%dT%H:%M"


class AlreadyAnswered(Exception):
    """A second answer from a member who has one in already."""


def _local_datetime():
    return forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_LOCAL)


class SurveyForm(forms.ModelForm):
    class Meta:
        model = Survey
        fields = [
            "title", "intro", "admins", "allow_anonymous", "results_visibility",
            "opens_at", "closes_at",
        ]
        labels = {
            "intro": "Introduction",
            "admins": "Run by",
            "allow_anonymous": "Members may answer anonymously",
            "results_visibility": "Who can see the results",
            "opens_at": "Opens",
            "closes_at": "Closes",
        }
        widgets = {
            "intro": forms.Textarea(attrs={"rows": 5}),
            "admins": forms.CheckboxSelectMultiple,
            "opens_at": _local_datetime(),
            "closes_at": _local_datetime(),
        }

    def __init__(self, *args, can_set_admins=True, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("opens_at", "closes_at"):
            self.fields[name].input_formats = [DATETIME_LOCAL]
        if can_set_admins:
            field = self.fields["admins"]
            field.queryset = User.objects.filter(is_active=True, is_banned=False).order_by("last_name", "first_name")
            field.label_from_instance = lambda user: f"{user.get_full_name() or user.username} ({user.username})"
        else:
            del self.fields["admins"]

    def clean(self):
        data = super().clean()
        opens, closes = data.get("opens_at"), data.get("closes_at")
        now = timezone.now()
        if opens and closes and closes <= opens:
            self.add_error("closes_at", "The survey has to close after it opens.")
        elif closes and closes <= now and self.instance.status != Survey.Status.CLOSED:
            self.add_error("closes_at", "That closing time has already passed.")
        # self.instance still holds the saved values here.
        if self.instance.pk and self.instance.is_live and opens and opens > now:
            self.add_error("opens_at", "The survey is already open. Close it first if you want to reschedule it.")
        return data


class QuestionForm(forms.ModelForm):
    """A question and, for choice kinds, its choices one per line. Once the
    survey has answers the choices are edited one field each, keyed by the
    choice itself, so rewording can never move an answer onto a different
    choice."""

    choices_text = forms.CharField(
        label="Choices", required=False, widget=forms.Textarea(attrs={"rows": 5}),
        help_text="One per line. Only for choose-one, choose-any and dropdown questions.",
    )

    class Meta:
        model = Question
        fields = ["prompt", "help_text", "kind", "required", "scale_low", "scale_high"]
        labels = {
            "prompt": "Question",
            "help_text": "Help text",
            "kind": "Kind of answer",
            "required": "An answer is required",
            "scale_low": "What 1 means",
            "scale_high": "What 5 means",
        }

    def __init__(self, *args, survey, locked=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.survey = survey
        self.locked = locked
        self.existing = list(self.instance.choices.all()) if self.instance.pk else []
        if locked:
            self.fields["kind"].disabled = True
            self.fields["kind"].help_text = "Can't change once the survey has answers."
            if self.existing:
                del self.fields["choices_text"]
                for choice in self.existing:
                    self.fields[f"choice_{choice.pk}"] = forms.CharField(
                        label=f"Choice {choice.sort_order + 1}", max_length=140, initial=choice.label,
                    )
        elif self.existing and not self.is_bound:
            self.initial["choices_text"] = "\n".join(c.label for c in self.existing)

    def choice_fields(self):
        """The per-choice fields of a locked question, in order."""
        return [self[f"choice_{c.pk}"] for c in self.existing if f"choice_{c.pk}" in self.fields]

    def clean(self):
        data = super().clean()
        kind = data.get("kind") or self.instance.kind
        labels, seen = [], set()
        if kind in CHOICE_KINDS and "choices_text" in self.fields:
            for line in (data.get("choices_text") or "").splitlines():
                label = " ".join(line.split())[:140]
                if label and label.lower() not in seen:
                    seen.add(label.lower())
                    labels.append(label)
            if len(labels) < 2:
                self.add_error("choices_text", "Give at least two choices, one per line.")
        elif kind in CHOICE_KINDS:
            for choice in self.existing:
                name = f"choice_{choice.pk}"
                if name in self.errors:  # blank or too long: the field has said so already
                    labels.append(choice.label)
                    continue
                label = " ".join(data.get(name, "").split())[:140]
                if label.lower() in seen:
                    self.add_error(name, "Two choices can't have the same wording.")
                seen.add(label.lower())
                labels.append(label)
        data["labels"] = labels
        return data

    def save(self, commit=True):
        question = super().save(commit=False)
        question.survey = self.survey
        if question.pk is None:
            last = self.survey.questions.aggregate(models.Max("sort_order"))["sort_order__max"]
            question.sort_order = (last or 0) + 1
        question.save()
        labels = self.cleaned_data["labels"]
        if question.has_choices and self.locked and self.existing:
            for choice, label in zip(self.existing, labels):  # each choice keeps its answers
                if choice.label != label:
                    choice.label = label
                    choice.save(update_fields=["label"])
        elif question.has_choices:
            question.choices.all().delete()
            Choice.objects.bulk_create(
                [Choice(question=question, label=label, sort_order=i) for i, label in enumerate(labels)]
            )
        elif self.existing:
            question.choices.all().delete()
        return question


class ResponseForm(forms.Form):
    """The answer sheet: one field per question, and how to answer."""

    IDENTITY = (
        ("named", "With my name"),
        ("anonymous", "Anonymously"),
    )

    def __init__(self, survey, *args, user=None, existing=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.survey = survey
        self.user = user
        self.existing = existing
        self.questions = list(survey.questions.prefetch_related("choices"))
        if survey.allow_anonymous and existing is None:
            self.fields["identity"] = forms.ChoiceField(
                choices=self.IDENTITY, widget=forms.RadioSelect, initial="named",
                label="How do you want to answer?",
            )
        previous = {}
        if existing is not None:
            previous = {a.question_id: a for a in existing.answers.prefetch_related("choices")}
        for question in self.questions:
            field = self._field_for(question)
            if question.pk in previous:
                field.initial = self._initial_from(question, previous[question.pk])
            self.fields[f"q{question.pk}"] = field

    @staticmethod
    def _field_for(question):
        common = {"label": question.prompt, "help_text": question.help_text, "required": question.required}
        kinds = Question.Kind
        if question.kind == kinds.SHORT:
            return forms.CharField(max_length=300, **common)
        if question.kind == kinds.LONG:
            return forms.CharField(max_length=4000, widget=forms.Textarea(attrs={"rows": 4}), **common)
        choices = [(str(c.pk), c.label) for c in question.choices.all()]
        if question.kind == kinds.SINGLE:
            return forms.ChoiceField(choices=choices, widget=forms.RadioSelect, **common)
        if question.kind == kinds.MULTI:
            return forms.MultipleChoiceField(choices=choices, widget=forms.CheckboxSelectMultiple, **common)
        if question.kind == kinds.DROPDOWN:
            return forms.ChoiceField(choices=[("", "Choose…")] + choices, **common)
        if question.kind == kinds.SCALE:
            return forms.ChoiceField(
                choices=[(str(i), str(i)) for i in range(1, 6)], widget=forms.RadioSelect, **common,
            )
        return forms.ChoiceField(choices=[("yes", "Yes"), ("no", "No")], widget=forms.RadioSelect, **common)

    @staticmethod
    def _initial_from(question, answer):
        if question.has_choices:
            pks = [str(c.pk) for c in answer.choices.all()]
            return pks if question.picks_many else (pks[0] if pks else None)
        if question.is_text:
            return answer.text
        return answer.value

    def question_fields(self):
        """(question, bound field) pairs, in order."""
        for question in self.questions:
            yield question, self[f"q{question.pk}"]

    @property
    def anonymous(self):
        return self.cleaned_data.get("identity") == "anonymous"

    def save(self):
        """Store the answers and remember that this member has answered.
        Returns the Response; raises AlreadyAnswered if an answer from this
        member got in first (two submissions at once, say)."""
        anonymous = self.existing.is_anonymous if self.existing is not None else self.anonymous
        with transaction.atomic():
            if self.existing is not None:
                response = self.existing
                response.updated_at = timezone.now()
                response.save(update_fields=["updated_at"])
                response.answers.all().delete()
            else:
                # The participation row's unique key is what stops a double
                # submission: whoever creates it answers, the other does not.
                try:
                    _, created = Participation.objects.get_or_create(survey=self.survey, user=self.user)
                except IntegrityError:  # the other of two simultaneous submissions won
                    created = False
                if not created:
                    raise AlreadyAnswered
                response = Response.objects.create(
                    survey=self.survey,
                    respondent=None if anonymous else self.user,
                    is_anonymous=anonymous,
                    submitted_at=None if anonymous else timezone.now(),
                )
            for question in self.questions:
                value = self.cleaned_data.get(f"q{question.pk}")
                if value in (None, "", []):
                    continue
                answer = Answer(response=response, question=question)
                if question.has_choices:
                    answer.save()
                    picked = value if isinstance(value, list) else [value]
                    answer.choices.set(question.choices.filter(pk__in=picked))
                elif question.is_text:
                    answer.text = value.strip()
                    answer.save()
                else:
                    answer.value = value
                    answer.save()
        return response
