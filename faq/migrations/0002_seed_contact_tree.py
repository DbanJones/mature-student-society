"""Seed the who-to-contact tree from the static starter data in faq.data.

Runs only when the table is empty, so admin edits are never clobbered by a
re-deploy.
"""

from django.db import migrations


def seed(apps, schema_editor):
    ContactNode = apps.get_model("faq", "ContactNode")
    if ContactNode.objects.exists():
        return
    from faq.data import CONTACT_TREE

    def create(node_id, parent, label, order):
        node = CONTACT_TREE[node_id]
        if "result" in node:
            r = node["result"]
            return ContactNode.objects.create(
                parent=parent, option_label=label, kind="result",
                sort_order=order, who=r["who"], action=r["do"],
                escalate=r["escalate"], keep=r["keep"],
            )
        created = ContactNode.objects.create(
            parent=parent, option_label=label, kind="question",
            sort_order=order, question=node["q"],
        )
        for i, (opt_label, child_id) in enumerate(node["options"]):
            create(child_id, created, opt_label, (i + 1) * 10)
        return created

    create("start", None, "", 10)


class Migration(migrations.Migration):

    dependencies = [
        ("faq", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
