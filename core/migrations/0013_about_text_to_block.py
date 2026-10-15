# The About page's introduction used to be the "about_text" field of Site
# configuration, editable only in the Django admin. It is now the
# "about.body" text block on the Pages tab; carry any wording across.

from django.db import migrations


def copy_about_text(apps, schema_editor):
    SiteConfig = apps.get_model("core", "SiteConfig")
    TextBlock = apps.get_model("core", "TextBlock")
    config = SiteConfig.objects.first()
    if config and (config.about_text or "").strip():
        TextBlock.objects.update_or_create(key="about.body", defaults={"text": config.about_text})


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_sitepage_section_textblock"),
    ]

    operations = [
        migrations.RunPython(copy_about_text, migrations.RunPython.noop),
    ]
