"""The who-to-contact decision map, stored as an editable tree, plus the
committee's recorded departmental contacts.

Admins add/edit/delete nodes from the panel; the public page renders the
tree both as an interactive flow and as a hierarchy diagram. A node is
either a QUESTION (has children reached via its option label) or a RESULT
(who to contact, what to do, when to escalate, what to keep).
"""

from django.conf import settings
from django.db import models


class ContactNode(models.Model):
    class Kind(models.TextChoices):
        QUESTION = "question", "Question (has options)"
        RESULT = "result", "Result (who to contact)"

    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE,
        related_name="children",
    )
    option_label = models.CharField(
        max_length=120, blank=True,
        help_text="The answer/button text that leads here from the parent "
                  "question (blank for the root).",
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    sort_order = models.PositiveSmallIntegerField(default=100)

    # QUESTION nodes
    question = models.CharField(max_length=200, blank=True)

    # RESULT nodes
    who = models.CharField("Who to contact", max_length=250, blank=True)
    action = models.TextField("What to do", blank=True)
    escalate = models.TextField("When/where to escalate", blank=True)
    keep = models.TextField("Evidence to keep", blank=True)
    email_template = models.TextField(
        blank=True,
        help_text="Optional tailored 'first useful email' for this result. "
                  "Leave blank to auto-generate one from the fields above. "
                  "Square brackets mark the blanks members fill in — the "
                  "tokens [name], [College], [course] and [CRSid] are filled "
                  "in automatically from the logged-in member's profile.",
    )

    class Meta:
        ordering = ["sort_order", "pk"]

    def __str__(self):
        label = self.option_label or "(root)"
        return f"{label} → {self.question or self.who}"

    @property
    def is_result(self):
        return self.kind == self.Kind.RESULT

    def build_email(self, user=None):
        """The tailored first-useful-email for this result node.

        When a logged-in member is supplied, the [name], [College], [course]
        and [CRSid] blanks are filled from their profile — in both
        auto-generated and admin-written templates.
        """
        email = self.email_template.strip() or self._default_email()
        return self._personalise(email, user)

    def _default_email(self):
        issue = (self.option_label or "the problem").strip()
        # Strip any leading emoji from the branch label.
        issue = issue.lstrip("🚨📚💙💷🏠👨‍👩‍👧🛂⚖️🦁✉️☎️ ").strip() or "the problem"
        who = self.who.split("—")[0].split("+")[0].strip().rstrip(",;")
        # "Your Director of Studies" → "Dear Director of Studies,"
        for prefix in ("your ", "the ", "Your ", "The "):
            if who.startswith(prefix):
                who = who[len(prefix):]
                break
        who = who or "[name/role]"
        keep = self.keep.rstrip(".") or "[relevant evidence]"
        return (
            f"SUBJECT: Action needed by [date] — {issue}\n\n"
            f"Dear {who},\n\n"
            f"I am a [year] [course] student at [College]. Since [date], "
            f"{issue.lower()} has affected [teaching / housing / health / "
            f"finance].\n\n"
            f"The immediate deadline or risk is [date/time]. I am requesting "
            f"[the specific outcome you need].\n\n"
            f"I have kept: {keep}. I have also contacted [person/service]. "
            f"Please confirm by [reasonable time] who will decide and what I "
            f"should do meanwhile.\n\n"
            f"Thank you,\n[name] ([CRSid])"
        )

    @staticmethod
    def _personalise(email, user):
        if user is None or not getattr(user, "is_authenticated", False):
            return email
        subs = {}
        if user.get_full_name():
            subs["[name]"] = user.get_full_name()
            subs["[Name]"] = user.get_full_name()
        if user.college:
            subs["[College]"] = user.get_college_display()
            subs["[college]"] = user.get_college_display()
        if user.course:
            subs["[course]"] = user.course
            # The profile's course usually carries the level ("MPhil …",
            # "2nd-year BA …"), so the generic year blank becomes noise.
            email = email.replace("a [year] [course]", "a [course]")
            email = email.replace("[year] [course]", "[course]")
        if user.crsid:
            subs["[CRSid]"] = user.crsid
        else:
            email = email.replace(" ([CRSid])", "")
        for token, value in subs.items():
            email = email.replace(token, value)
        return email

    @classmethod
    def get_root(cls):
        return cls.objects.filter(parent__isnull=True).order_by("pk").first()

    def as_dict(self, user=None):
        """Whole subtree as JSON-serialisable dict for the wizard."""
        if self.is_result:
            return {
                "id": self.pk,
                "label": self.option_label,
                "result": {
                    "who": self.who,
                    "do": self.action,
                    "escalate": self.escalate,
                    "keep": self.keep,
                    "email": self.build_email(user),
                },
            }
        return {
            "id": self.pk,
            "label": self.option_label,
            "q": self.question,
            "children": [child.as_dict(user) for child in self.children.all()],
        }


def _school_choices():
    from .data import DEPARTMENTS_INFO
    return [(slug, info["name"]) for slug, info in DEPARTMENTS_INFO.items()]


class DepartmentContact(models.Model):
    """A departmental email address the committee has recorded as worth
    keeping — especially offices that actually answer.

    Shown on the matching School's department page; managed by admins from
    the panel's Content tab.
    """

    school = models.CharField(
        max_length=30, choices=_school_choices,
        help_text="Which School's page this contact appears on.",
    )
    department = models.CharField(
        max_length=120,
        help_text="e.g. 'Faculty of History — Undergraduate Office'.",
    )
    email = models.EmailField()
    notes = models.CharField(
        max_length=250, blank=True,
        help_text="What they handle / when to use this address, e.g. "
                  "'timetable clashes and submission extensions'.",
    )
    responds_well = models.BooleanField(
        "runs well",
        default=False,
        help_text="Tick for offices members report as quick and helpful — "
                  "they get a ✅ on the public page.",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="department_contacts_added",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["school", "-responds_well", "department"]
        unique_together = [("school", "department")]

    def __str__(self):
        return f"{self.department} <{self.email}>"
