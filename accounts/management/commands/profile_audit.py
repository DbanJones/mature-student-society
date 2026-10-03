"""List the accounts that never finished signing up, and why.

Usage:  python manage.py profile_audit [--all]

Answers "why do some members have no college or mobile?" straight from the
data. An account is created at first Raven login (or when a waitlist request
is approved), BEFORE the terms gate and the profile form, so anyone who
stops at either step leaves a blank account behind. Run this on the SRCF to
see which step each one stopped at.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from core.models import TermsVersion


class Command(BaseCommand):
    help = "List accounts that never finished the terms/profile steps, and why."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all", action="store_true",
            help="List complete accounts too, not just the unfinished ones.",
        )

    def handle(self, *args, **options):
        current = TermsVersion.current_number()
        users = list(User.objects.order_by("created_at"))
        counts = {}
        rows = []
        for user in users:
            status = user.onboarding_status(current)
            counts[status] = counts.get(status, 0) + 1
            if status != User.Onboarding.COMPLETE or options["all"]:
                rows.append((user, status))

        self.stdout.write(f"Accounts: {len(users)}")
        for status in User.Onboarding:
            self.stdout.write(f"  {status.label:<20} {counts.get(status, 0)}")
        if current is None:
            self.stdout.write("  (no terms published, so nobody is gated on them)")
        if not rows:
            self.stdout.write("Nothing to list.")
            return

        self.stdout.write("")
        self.stdout.write(
            f"{'username':<30} {'type':<10} {'status':<20} "
            f"{'created':<11} {'last login':<11} missing"
        )
        for user, status in rows:
            missing = [
                label for label, value in (
                    ("name", user.first_name and user.last_name),
                    ("college", user.college),
                    ("mobile", user.mobile),
                ) if not value
            ]
            last = (
                f"{timezone.localtime(user.last_login):%Y-%m-%d}"
                if user.last_login else "never"
            )
            created = f"{timezone.localtime(user.created_at):%Y-%m-%d}"
            self.stdout.write(
                f"{user.username[:30]:<30} {user.account_type:<10} "
                f"{status.label:<20} {created:<11} {last:<11} "
                f"{', '.join(missing) or '-'}"
            )
