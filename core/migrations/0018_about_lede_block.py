from django.db import migrations

# The default of SiteConfig.tagline, and of the "about.lede" block in core/blocks.py.
DEFAULT = "Welcoming Cambridge's mature students."


def copy_tagline(apps, schema_editor):
    """The About page used to show the site tagline under its title. Keep a customised one."""
    SiteConfig = apps.get_model("core", "SiteConfig")
    TextBlock = apps.get_model("core", "TextBlock")
    config = SiteConfig.objects.first()
    if config is None:
        return
    tagline = " ".join((config.tagline or "").split())
    if not tagline or tagline == DEFAULT:
        return
    if TextBlock.objects.filter(key="about.lede").exists():
        return
    TextBlock.objects.create(key="about.lede", text=tagline[:400])


class Migration(migrations.Migration):

    dependencies = [("core", "0017_siteconfig_ball_event")]

    operations = [migrations.RunPython(copy_tagline, migrations.RunPython.noop)]
