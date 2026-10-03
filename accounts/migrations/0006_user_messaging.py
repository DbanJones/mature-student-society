"""Replace the mute flag with a three-state messaging setting.

Member-to-member messaging is now off by default: ``default`` members can
write to committee admins only, ``enabled`` members can message anyone, and
``muted`` members can send nothing. Previously muted members stay muted;
everyone else starts at the default.
"""

from django.db import migrations, models


def forwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(can_send_messages=False).update(messaging="muted")


def backwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(messaging="muted").update(can_send_messages=False)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_user_terms_accepted_at_user_terms_accepted_version_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="messaging",
            field=models.CharField(
                choices=[
                    ("default", "Committee only"),
                    ("enabled", "Can message anyone"),
                    ("muted", "Muted"),
                ],
                default="default",
                help_text=(
                    "Who this member may send direct messages to. When "
                    "site-wide messaging is restricted (the default) members "
                    "can only write to the committee unless an admin enables "
                    "them; muted members can read but never send. Admins can "
                    "always message anyone."
                ),
                max_length=10,
            ),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(model_name="user", name="can_send_messages"),
    ]
