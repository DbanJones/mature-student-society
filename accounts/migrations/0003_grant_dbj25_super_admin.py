"""Grant the webmaster (dbj25) super admin rights on existing databases.

New/dev databases get this from ``seed_demo``; this covers a deployed DB
that already has the account.
"""

from django.db import migrations


def grant(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(crsid="dbj25").update(
        is_super_admin=True, is_portal_admin=True
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_user_bio_user_can_send_messages_user_course_and_more"),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
