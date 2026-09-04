"""Re-root the who-to-contact map on the four people you actually go to.

The map used to open with "What kind of problem is it?" and eight problem
categories. Members don't think in categories — they think "whose job is
this?" — so the root now asks which role to approach, and the existing
problem branches hang underneath the right one:

    Tutor       pastoral and college life, and a problem with a supervisor
    Supervisor  academic work
    MSS         advice on life in Cambridge, and the society itself
    Problem with your Tutor  → the existing Senior Tutor answer

Nothing is deleted: every existing branch is re-parented, so the detail (and
any wording admins have edited) survives. The emergency branch deliberately
stays at the root — an emergency should never be behind a question about
whose job it is.

Defensive by design: nodes are looked up by their option label and anything
already moved, renamed or removed is skipped, so a part-edited tree degrades
rather than breaking.
"""

from django.db import migrations

ROOT_QUESTION = "Who should I ask?"

TUTOR_LABEL = "🏛️ Tutor — pastoral or college issues, or a problem with your supervisor"
TUTOR_QUESTION = "What is it about?"
SUPERVISOR_LABEL = "📚 Supervisor — academic issues"
MSS_LABEL = "🦁 MSS — advice on life in Cambridge"
TUTOR_PROBLEM_LABEL = "⚠️ I have a problem with my Tutor"

# Existing branches, by the option label they were seeded with, that move
# under the Tutor category.
TO_TUTOR = [
    "💙 Health & wellbeing",
    "💷 Money",
    "🏠 Accommodation",
    "👨‍👩‍👧 Family, partner or childcare",
    "🛂 Visa or immigration",
    "⚖️ Harassment or discrimination",
    "Conflict with a supervisor",
    "I'm considering intermitting",
]

# The general "life in Cambridge" answer the MSS branch was missing.
LIFE_IN_CAMBRIDGE = {
    "option_label": "Settling in, or life in Cambridge generally",
    "who": "Any MSS member — the Open Forum, or a committee admin",
    "action": (
        "Ask in the WhatsApp Open Forum, or come to a coffee meet or Supper "
        "Club and ask in person. For the written version, the Guide covers "
        "arriving, colleges, money, family life and living in Cambridge."
    ),
    "escalate": (
        "If it turns out to be a college, academic or money problem, come "
        "back to this map and start again from the right role — MSS can point "
        "you there but cannot decide it for you."
    ),
    "keep": "Nothing formal — but note who told you what, so you can follow up.",
}


def reroot(apps, schema_editor):
    ContactNode = apps.get_model("faq", "ContactNode")

    root = ContactNode.objects.filter(parent__isnull=True).order_by("pk").first()
    if root is None:
        return  # empty map; the seed migration will build the new shape

    def by_label(label):
        return ContactNode.objects.filter(option_label=label).first()

    root.question = ROOT_QUESTION
    root.kind = "question"
    root.save()

    # Emergency stays first, and stays at the root.
    emergency = by_label("🚨 Emergency — danger to someone right now")
    if emergency is not None:
        emergency.parent = root
        emergency.sort_order = 10
        emergency.save()

    # --- Tutor: pastoral, college life, and trouble with a supervisor.
    tutor = by_label(TUTOR_LABEL)
    if tutor is None:
        tutor = ContactNode.objects.create(
            parent=root, option_label=TUTOR_LABEL, kind="question",
            sort_order=20, question=TUTOR_QUESTION,
        )
    else:
        tutor.parent = root
        tutor.sort_order = 20
        tutor.save()

    for order, label in enumerate(TO_TUTOR, start=1):
        node = by_label(label)
        if node is not None:
            node.parent = tutor
            node.sort_order = order * 10
            node.save()

    # --- Supervisor: the existing academic question, relabelled.
    academic = by_label("📚 Academic — essays, supervisions, deadlines, exams")
    if academic is not None:
        academic.parent = root
        academic.option_label = SUPERVISOR_LABEL
        academic.sort_order = 30
        academic.save()

    # --- MSS: the society branch, plus a general life-in-Cambridge answer.
    mss = by_label("🦁 Something about MSS itself")
    if mss is None:
        mss = by_label(MSS_LABEL)
    if mss is not None:
        mss.parent = root
        mss.option_label = MSS_LABEL
        mss.question = "What do you need?"
        mss.sort_order = 40
        mss.save()
        if not ContactNode.objects.filter(
            parent=mss, option_label=LIFE_IN_CAMBRIDGE["option_label"]
        ).exists():
            ContactNode.objects.create(
                parent=mss, kind="result", sort_order=5, **LIFE_IN_CAMBRIDGE
            )

    # --- A problem with your Tutor: promote the existing Senior Tutor answer
    # to the root, keeping its wording.
    tutor_problem = by_label("The problem is my Tutor themselves")
    if tutor_problem is None:
        tutor_problem = by_label(TUTOR_PROBLEM_LABEL)
    if tutor_problem is not None:
        tutor_problem.parent = root
        tutor_problem.option_label = TUTOR_PROBLEM_LABEL
        tutor_problem.sort_order = 50
        tutor_problem.save()


def unreroot(apps, schema_editor):
    """Put the problem-first root back.

    Best effort: the branches are returned to the root and the original
    question restored. Node-level wording edits made since are kept.
    """
    ContactNode = apps.get_model("faq", "ContactNode")
    root = ContactNode.objects.filter(parent__isnull=True).order_by("pk").first()
    if root is None:
        return

    def by_label(label):
        return ContactNode.objects.filter(option_label=label).first()

    root.question = "What kind of problem is it?"
    root.save()

    academic = by_label(SUPERVISOR_LABEL)
    if academic is not None:
        academic.option_label = "📚 Academic — essays, supervisions, deadlines, exams"
        academic.parent = root
        academic.save()

    mss = by_label(MSS_LABEL)
    if mss is not None:
        mss.option_label = "🦁 Something about MSS itself"
        mss.question = "What about MSS?"
        mss.parent = root
        mss.save()

    for label in TO_TUTOR:
        node = by_label(label)
        if node is not None:
            node.parent = root
            node.save()

    tutor_problem = by_label(TUTOR_PROBLEM_LABEL)
    if tutor_problem is not None:
        tutor_problem.option_label = "The problem is my Tutor themselves"
        health = by_label("💙 Health & wellbeing")
        tutor_problem.parent = health or root
        tutor_problem.save()

    # The two branches that were academic children before the re-root.
    for label in ("Conflict with a supervisor", "I'm considering intermitting"):
        node = by_label(label)
        if node is not None and academic is not None:
            node.parent = academic
            node.save()

    tutor = by_label(TUTOR_LABEL)
    if tutor is not None and not tutor.children.exists():
        tutor.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("faq", "0004_departmentcontact"),
    ]

    operations = [
        migrations.RunPython(reroot, unreroot),
    ]
