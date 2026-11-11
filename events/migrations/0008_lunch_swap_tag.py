from django.db import IntegrityError, migrations, transaction

SLUG = "lunch-swap"
NAME = "Lunch Swap"
PAGE = (
    "## How a lunch swap works\n\n"
    "A member invites a few others to lunch at their college. It is an easy way "
    "to see inside another college and meet people over an ordinary meal.\n\n"
    "- **Hosting:** pick a day, say how many guests you can take and where to "
    "meet. Check your college's guest rules first.\n"
    "- **Going:** take a place on the lunch's page. If your plans change, give "
    "the place back so someone else can have it.\n"
    "- **Swapping:** enjoyed it? Invite people back to lunch at your college. "
    "Nobody keeps score.\n\n"
    "Lunch swaps are for members only. Who pays is agreed for each lunch and "
    "shown on its page."
)


def add_tag(apps, schema_editor):
    Category = apps.get_model("events", "Category")
    if Category.objects.filter(slug=SLUG).exists():
        return
    clash = Category.objects.filter(name__iexact=NAME).first()
    if clash is not None:
        # Someone already made a tag with this name: use theirs.
        clash.slug = SLUG
        clash.save(update_fields=["slug"])
        return
    try:
        with transaction.atomic():
            Category.objects.create(
                slug=SLUG, name=NAME, emoji="🥪", color="#8a6d0b", sort_order=65,
                description="Invite members to lunch at your college, and get invited back.",
                page_content=PAGE,
            )
    except IntegrityError:
        # Never stop the site starting over a tag; the form makes it on first use.
        pass


class Migration(migrations.Migration):
    dependencies = [("events", "0007_event_show_attendees")]
    operations = [migrations.RunPython(add_tag, migrations.RunPython.noop)]
