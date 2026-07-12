"""Seed the Guide from the Master Report content (guide/seed_content.py).

Usage:  python manage.py seed_guide
Idempotent: a page whose slug already exists is never touched, so member
edits are always preserved. Attribution goes to the committee (the super
admin account if present, else the first admin).
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from guide.models import GuidePage
from guide.seed_content import GUIDE_PAGES


class Command(BaseCommand):
    help = "Seed guide pages from the Master Report (existing pages untouched)."

    def handle(self, *args, **options):
        User = get_user_model()
        editor = (
            User.objects.filter(is_super_admin=True).first()
            or User.objects.filter(is_portal_admin=True).first()
        )
        created = skipped = 0
        for title, section, content in GUIDE_PAGES:
            base = slugify(title)[:140] or "page"
            slug, n = base, 2
            while GuidePage.objects.filter(slug=slug).exclude(title=title).exists():
                slug = f"{base}-{n}"
                n += 1
            if GuidePage.objects.filter(slug=slug).exists():
                skipped += 1
                continue
            page = GuidePage.objects.create(
                title=title, slug=slug, section=section,
                content=content.strip(),
                created_by=editor, updated_by=editor,
            )
            page.save_revision(editor)
            created += 1
        self.stdout.write(self.style.SUCCESS(
            f"Guide seeded: {created} pages created, {skipped} already present."
        ))
