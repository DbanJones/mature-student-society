from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_seed_terms_draft_and_wellbeing_page"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="messaging_mode",
            field=models.CharField(
                choices=[
                    ("open", "Open: any member can message any other member"),
                    (
                        "restricted",
                        "Restricted: members can message the committee; only "
                        "members an admin has enabled can message each other",
                    ),
                ],
                default="restricted",
                help_text=(
                    "Admins can always message anyone, and anyone can reply "
                    "to an admin, whichever mode is chosen."
                ),
                max_length=10,
            ),
        ),
    ]
