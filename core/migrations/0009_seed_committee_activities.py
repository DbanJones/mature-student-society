"""Move the committee table and the "What we do" cards into the database,
seeded from the lists that used to be hardcoded in core/views.py, so the
committee can edit them from panel → Content → About & committee."""

from django.db import migrations

COMMITTEE = [
    ("Basma Al Ghamdi", "Undergrad liaison"),
    ("David Jones", "Tech"),
    ("Charlotte York", "Clubs/Events"),
    ("Samantha Neville", "Partners' liaison"),
    ("Tricia Postle", "Communications"),
    ("Oliver Samuel", "Postgrad liaison"),
    ("Richard Senior", "Treasurer Emeritus"),
    ("Tricia Sutton", "Staff/Well-being"),
    ("Barbara Toninato", "Staff liaison"),
    ("Rob Kelsey", "Treasurer"),
    ("Georgina Glasby", "External Relations"),
    ("Jess Mann", "Well-being"),
    ("Jen Greggs", "Events/alumni liaison"),
]

ACTIVITIES = [
    ("🏫", "College lunches & tours", "Get to know the University's 31 colleges."),
    ("🍺", "Pub nights", "Explore Cambridge's pub scene."),
    ("🕯️", "Formals and formal-swaps", "The traditional Cambridge dining experience."),
    ("🎤", "Research “flashtalks”", "Present your research and listen to others."),
    ("💬", "50+ sub-groups", "From parenting lawyers to LGBT+."),
    ("🤝", "Networking", "Events for making useful contacts."),
    ("🥾", "Walks & running", "Explore the local area, jog together."),
    ("🍽️", "Supper Clubs", "Cambridge's range of international cheap-eat cuisines."),
    ("☕", "Wellbeing coffee meets", "Share concerns and help others."),
    ("🖼️", "Outings", "Museum/gallery trips and getting out of Cambridge."),
    ("📣", "Advice and advocacy", "Hive-mind help and official representation."),
]


def seed(apps, schema_editor):
    CommitteeMember = apps.get_model("core", "CommitteeMember")
    Activity = apps.get_model("core", "Activity")
    if not CommitteeMember.objects.exists():
        for order, (name, role) in enumerate(COMMITTEE, 1):
            CommitteeMember.objects.create(name=name, role=role, sort_order=order * 10)
    if not Activity.objects.exists():
        for order, (emoji, name, blurb) in enumerate(ACTIVITIES, 1):
            Activity.objects.create(emoji=emoji, name=name, blurb=blurb, sort_order=order * 10)


def unseed(apps, schema_editor):
    apps.get_model("core", "CommitteeMember").objects.all().delete()
    apps.get_model("core", "Activity").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_banner_committee_activity"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
