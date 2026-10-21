"""Meaningful counts, private reporting and safe filtered downloads."""

import csv
import io
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from events.models import Category, Event
from formals.models import FormalBooking, FormalExchange, FormalListing, ReturnProposal
from formals.report_forms import statistics_filter
from formals.report_views import _attendance_rows, _exchange_rows
from formals.reporting import LONDON, attendance_ledger, booking_outcome, report_listings, safe_csv_cell, statistics


class FormalReportingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def member(name, college="wolfson", **kwargs):
            return User.objects.create_user(
                username=name, first_name=name.title(), last_name="Member",
                college=college, mobile="07700900123", email=f"{name}@example.test",
                terms_accepted_version=999, **kwargs,
            )
        cls.host = member("reporthost")
        cls.host2 = member("reporthost2", "darwin")
        cls.guest = member("reportguest", "darwin")
        cls.guest2 = member("reportguest2", "clare")
        cls.guest3 = member("reportguest3", "clare")
        cls.admin = member("reportadmin", is_portal_admin=True)
        cls.staff = member("reportstaff", is_staff=True, is_superuser=True)
        cls.category = Category.objects.create(name="Report formals", slug="report-formals")

    def setUp(self):
        self.counter = 0
        self.now = datetime(2026, 10, 21, 12, tzinfo=LONDON)
        self.filters = {"date_from": date(2026, 10, 1), "date_to": date(2026, 10, 31), "college": "", "ledger": "attended"}
        self.all_time = {"date_from": "", "date_to": "", "college": ""}
        self.url = reverse("formals:statistics")
        self.export_url = reverse("formals:statistics_export")

    def listing(self, start=None, host=None, college="wolfson", capacity=3, cancelled=False, title=None, end=None):
        self.counter += 1
        start = start or datetime(2026, 10, 10, 19, tzinfo=LONDON)
        event = Event.objects.create(
            title=title or f"Reporting formal {self.counter}", category=self.category,
            start=start, end=end or start + timedelta(hours=2),
            created_by=host or self.host, members_only=True, is_cancelled=cancelled,
        )
        return FormalListing.objects.create(event=event, college=college, capacity=capacity, booking_closes_at=start - timedelta(days=1))

    def booking(self, listing, guest=None, status="attended", purpose="original", **kwargs):
        return FormalBooking.objects.create(
            listing=listing, guest=guest or self.guest, status=status, purpose=purpose, **kwargs,
        )

    def exchange(self, listing, guest=None, status="completed"):
        booking = self.booking(listing, guest=guest)
        return FormalExchange.objects.create(
            original_booking=booking, creditor=listing.host, debtor=booking.guest,
            return_college="darwin", return_by=date(2027, 3, 31), status=status,
        )

    def csv_data(self, response):
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        return list(csv.reader(io.StringIO(content)))

    def test_reporting_requires_active_committee_membership(self):
        for url in (self.url, self.export_url):
            self.assertEqual(self.client.get(url).status_code, 302)
        for user in (self.guest, self.staff):
            self.client.force_login(user)
            for url in (self.url, self.export_url):
                self.assertEqual(self.client.get(url).status_code, 403)
        for update in ({"is_active": False}, {"is_banned": True}):
            User.objects.filter(pk=self.admin.pk).update(is_active=True, is_banned=False)
            User.objects.filter(pk=self.admin.pk).update(**update)
            self.admin.refresh_from_db()
            self.client.force_login(self.admin)
            for url in (self.url, self.export_url):
                self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_report_and_export_are_private_and_contain_no_contact_details(self):
        listing = self.listing()
        self.booking(listing)
        self.client.force_login(self.admin)
        response = self.client.get(self.url, self.all_time)
        self.assertContains(response, self.guest.get_full_name())
        self.assertContains(response, "Who went")
        self.assertNotContains(response, self.guest.email)
        self.assertNotContains(response, self.guest.mobile)
        for url in (self.url, self.export_url):
            response = self.client.get(url, self.all_time)
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response["Cache-Control"])
            self.assertIn("private", response["Cache-Control"])
            self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
            if response.streaming:
                data = str(self.csv_data(response))
                self.assertNotIn(self.guest.email, data)
                self.assertNotIn(self.guest.mobile, data)

    def test_counts_do_not_multiply_capacity_and_distinguish_visits_people_and_hosts(self):
        first = self.listing()
        self.booking(first)
        self.booking(first, self.guest2)
        self.booking(first, self.guest3, status="reserved")
        second = self.listing(host=self.host2, college="darwin", capacity=2)
        self.booking(second, purpose="return")
        cancelled = self.listing(cancelled=True)
        self.booking(cancelled, status="reserved")
        data = statistics(self.filters, self.now)
        totals = data["totals"]
        self.assertEqual(totals["formals_offered"], 2)
        self.assertEqual(totals["places_offered"], 5)
        self.assertEqual(totals["cancelled_formals"], 1)
        self.assertEqual(totals["supported_formals"], 2)
        self.assertEqual(totals["guest_visits"], 3)
        self.assertEqual(totals["unique_guests"], 2)
        self.assertEqual(totals["unique_hosts"], 2)
        self.assertEqual(totals["open_visits"], 2)
        self.assertEqual(totals["return_visits"], 1)
        self.assertEqual(totals["reserved_places"], 1)
        self.assertEqual(totals["pending_attendance"], 1)
        wolfson = next(row for row in data["college_rows"] if row["college"] == "wolfson")
        self.assertEqual(wolfson["places_offered"], 3)
        self.assertEqual(wolfson["guest_visits"], 2)

    def test_unconfirmed_or_disputed_attendance_is_not_a_confirmed_visit(self):
        listing = self.listing()
        reserved = self.booking(listing, status="reserved", host_confirmed=True)
        self.booking(listing, self.guest2, status="no_show")
        self.booking(listing, self.guest3, status="requested")
        totals = statistics(self.filters, self.now)["totals"]
        self.assertEqual(totals["guest_visits"], 0)
        self.assertEqual(totals["pending_attendance"], 1)
        self.assertEqual(totals["attendance_review"], 1)
        self.assertEqual(booking_outcome(reserved, self.now), "Awaiting attendance confirmation")

    def test_ongoing_dinner_does_not_need_attendance_followup_yet(self):
        ongoing = self.listing(start=self.now - timedelta(hours=1), end=self.now + timedelta(hours=1))
        self.booking(ongoing, status="reserved")
        totals = statistics(self.filters, self.now)["totals"]
        self.assertEqual(totals["pending_attendance"], 0)
        ongoing.event.end = None
        ongoing.event.save(update_fields=["end"])
        self.assertEqual(statistics(self.filters, self.now)["totals"]["pending_attendance"], 1)

    def test_closed_requests_and_expired_holds_are_not_reported_as_pending(self):
        original = self.listing()
        exchange = self.exchange(original)
        closed_request = self.booking(original, self.guest2, status="requested")
        self.assertEqual(booking_outcome(closed_request, self.now), "Request closed without a place")
        returning = self.listing(start=self.now + timedelta(days=7), host=self.host2)
        held = self.booking(returning, self.host, status="held", purpose="return")
        proposal = ReturnProposal.objects.create(
            exchange=exchange, booking=held, expires_at=self.now - timedelta(minutes=1),
        )
        self.assertEqual(booking_outcome(held, self.now), "Return invitation expired")
        proposal.expires_at = self.now + timedelta(days=1)
        proposal.save(update_fields=["expires_at"])
        held = attendance_ledger({**self.filters, "ledger": "all"}).get(pk=held.pk)
        self.assertEqual(booking_outcome(held, self.now), "Held for a return invitation")
        returning.event.is_cancelled = True
        returning.event.save(update_fields=["is_cancelled"])
        held = attendance_ledger({**self.filters, "ledger": "all"}).get(pk=held.pk)
        self.assertEqual(booking_outcome(held, self.now), "Formal cancelled")

    def test_host_fallback_and_explicit_host_are_counted_correctly(self):
        listing = self.listing()
        listing.event.host = self.host2
        listing.event.save(update_fields=["host"])
        self.booking(listing)
        row = list(_attendance_rows(self.filters))[1]
        self.assertEqual(row[5], self.host2.pk)
        self.assertEqual(row[6], self.host2.get_full_name())

    def test_date_filter_uses_inclusive_london_days_across_dst_boundary(self):
        # 25 October 2026 is a 25-hour day in Cambridge.
        before = self.listing(start=datetime(2026, 10, 24, 22, 59, tzinfo=dt_timezone.utc))
        first = self.listing(start=datetime(2026, 10, 24, 23, 0, tzinfo=dt_timezone.utc))
        last = self.listing(start=datetime(2026, 10, 25, 23, 59, tzinfo=dt_timezone.utc))
        after = self.listing(start=datetime(2026, 10, 26, 0, 0, tzinfo=dt_timezone.utc))
        filters = {"date_from": date(2026, 10, 25), "date_to": date(2026, 10, 25)}
        self.assertSetEqual(set(report_listings(filters).values_list("pk", flat=True)), {first.pk, last.pk})
        self.assertNotEqual(before.pk, after.pk)

    def test_college_filter_uses_dinner_college_not_guests_profile(self):
        first = self.listing()
        self.booking(first)
        second = self.listing(college="darwin", host=self.host2)
        self.booking(second, self.host)
        filters = {**self.filters, "college": "wolfson"}
        self.assertEqual(statistics(filters, self.now)["totals"]["guest_visits"], 1)
        self.assertEqual(list(attendance_ledger(filters).values_list("guest_id", flat=True)), [self.guest.pk])

    def test_exchange_totals_are_original_dinner_cohort_and_current_status(self):
        for status in FormalExchange.Status.values:
            self.exchange(self.listing(), status=status)
        outside = self.listing(start=datetime(2026, 9, 20, 19, tzinfo=LONDON))
        self.exchange(outside)
        totals = statistics(self.filters, self.now)["totals"]
        self.assertEqual(totals["exchanges_total"], 6)
        self.assertEqual(totals["exchanges_completed"], 1)
        self.assertEqual(totals["exchanges_active"], 3)
        self.assertEqual(len(list(_exchange_rows(self.filters))), 7)

    def test_return_dinner_counts_in_its_own_period_without_an_extra_exchange(self):
        original = self.listing(start=datetime(2026, 9, 20, 19, tzinfo=LONDON))
        self.exchange(original)
        returning = self.listing(host=self.host2, college="darwin")
        self.booking(returning, self.host, purpose="return")
        totals = statistics(self.filters, self.now)["totals"]
        self.assertEqual(totals["guest_visits"], 1)
        self.assertEqual(totals["return_visits"], 1)
        self.assertEqual(totals["exchanges_completed"], 0)
        self.assertEqual(totals["exchanges_total"], 0)

    def test_invalid_filters_fail_closed_for_page_and_exports(self):
        self.client.force_login(self.admin)
        for filters in (
            {"date_from": "garbage"}, {"date_from": "2026-11-01", "date_to": "2026-10-01"},
            {"college": "invented"}, {"date_to": "9999-12-31"}, {"ledger": "invented"},
        ):
            for url in (self.url, self.export_url):
                with self.subTest(url=url, filters=filters):
                    response = self.client.get(url, filters)
                    self.assertEqual(response.status_code, 400)
                    self.assertFalse(response.streaming)
        self.assertEqual(self.client.get(self.export_url, {"kind": "secrets"}).status_code, 400)

    def test_default_academic_year_and_all_time_are_explicit(self):
        with patch("formals.report_forms.timezone.localdate", return_value=date(2026, 9, 30)):
            form = statistics_filter({})
            self.assertTrue(form.is_valid())
            self.assertEqual(form.cleaned_data["date_from"], date(2025, 10, 1))
            self.assertEqual(form.cleaned_data["date_to"], date(2026, 9, 30))
        with patch("formals.report_forms.timezone.localdate", return_value=date(2026, 10, 1)):
            form = statistics_filter({})
            self.assertTrue(form.is_valid())
            self.assertEqual(form.cleaned_data["date_from"], date(2026, 10, 1))
        form = statistics_filter(self.all_time)
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["date_from"])
        self.assertIsNone(form.cleaned_data["date_to"])

    def test_guest_list_selector_only_changes_ledger_not_totals(self):
        listing = self.listing()
        confirmed = self.booking(listing)
        pending = self.booking(listing, self.guest2, status="reserved")
        self.booking(listing, self.guest3, status="requested")
        self.assertEqual(list(attendance_ledger(self.filters)), [confirmed])
        self.assertEqual(list(attendance_ledger({**self.filters, "ledger": "pending"}, self.now)), [pending])
        self.assertEqual(attendance_ledger({**self.filters, "ledger": "all"}, self.now).count(), 3)
        self.assertEqual(statistics(self.filters, self.now), statistics({**self.filters, "ledger": "all"}, self.now))

    def test_pagination_preserves_filters_and_export_contains_full_filtered_list(self):
        for _ in range(27):
            self.booking(self.listing())
        self.client.force_login(self.admin)
        response = self.client.get(self.url, {**self.all_time, "college": "wolfson", "page": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["attendance_rows"]), 2)
        self.assertEqual(response.context["page"].paginator.count, 27)
        self.assertContains(response, "college=wolfson")
        data = self.csv_data(self.client.get(self.export_url, {**self.all_time, "college": "wolfson", "page": 2}))
        self.assertEqual(len(data), 28)

    def test_csv_neutralises_formulas_and_retains_confirmation_flags(self):
        listing = self.listing(title="  =HYPERLINK(\"https://example.test\")")
        self.guest.first_name = "\t+1+1"
        self.guest.save(update_fields=["first_name"])
        self.booking(listing, host_confirmed=True, guest_confirmed=False)
        self.client.force_login(self.admin)
        rows = self.csv_data(self.client.get(self.export_url, self.all_time))
        self.assertEqual(rows[1][3], "'  =HYPERLINK(\"https://example.test\")")
        self.assertTrue(rows[1][8].startswith("'"))
        self.assertEqual(rows[1][12:14], ["Yes", "No"])
        for payload in ("=1", "+1", "-1", "@SUM(1)", " \t=1", "\nplain", "\tplain"):
            self.assertTrue(safe_csv_cell(payload).startswith("'"))
        self.assertEqual(safe_csv_cell("Member"), "Member")

    def test_no_email_fallback_for_members_without_display_names(self):
        self.guest.first_name = self.guest.last_name = ""
        self.guest.username = "private-contact@example.test"
        self.guest.save(update_fields=["first_name", "last_name", "username"])
        self.booking(self.listing())
        rows = list(_attendance_rows(self.filters))
        self.assertEqual(rows[1][8], f"Member #{self.guest.pk}")

    def test_csv_iteration_has_no_query_per_guest_or_exchange(self):
        for _ in range(8):
            self.exchange(self.listing())
        with self.assertNumQueries(1):
            self.assertEqual(len(list(_attendance_rows(self.filters))), 9)
        with self.assertNumQueries(1):
            self.assertEqual(len(list(_exchange_rows(self.filters))), 9)

    def test_confirmed_history_is_retained_if_event_marked_cancelled(self):
        self.booking(self.listing(cancelled=True))
        totals = statistics(self.filters, self.now)["totals"]
        self.assertEqual(totals["formals_offered"], 0)
        self.assertEqual(totals["guest_visits"], 1)

    def test_empty_report_has_zero_metrics_and_clear_message(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url, self.all_time)
        self.assertContains(response, "No confirmed guest visits in this period yet.")
        self.assertEqual(response.context["totals"]["guest_visits"], 0)
        self.assertEqual(response.context["totals"]["exchanges_total"], 0)

    def test_group_counts_use_actual_attendance_and_do_not_invent_unique_members(self):
        first = self.listing()
        self.booking(
            first, places=3, companion_names=["First companion", "Second companion"],
            host_attendance=[0, 2], guest_attendance=[0, 2],
        )
        second = self.listing(host=self.host2, college="darwin")
        self.booking(
            second, self.guest2, places=2, companion_names=["Companion only"],
            host_attendance=[1], guest_attendance=[1], purpose="return",
        )
        pending = self.listing()
        self.booking(pending, status="reserved", places=3)
        disputed = self.listing()
        self.booking(
            disputed, status="no_show", places=3,
            host_attendance=[0, 1, 2], guest_attendance=[0],
        )
        absent = self.listing()
        self.booking(absent, status="absent", places=2, host_attendance=[], guest_attendance=[])
        data = statistics(self.filters, self.now)
        totals = data["totals"]
        self.assertEqual(totals["guest_visits"], 3)
        self.assertEqual(totals["companion_visits"], 2)
        self.assertEqual(totals["unique_guests"], 1)
        self.assertEqual(totals["supported_formals"], 2)
        self.assertEqual(totals["open_visits"], 2)
        self.assertEqual(totals["return_visits"], 1)
        self.assertEqual(totals["reserved_places"], 3)
        self.assertEqual(totals["pending_attendance"], 1)
        self.assertEqual(totals["attendance_review"], 1)
        darwin = next(row for row in data["college_rows"] if row["college"] == "darwin")
        self.assertEqual(darwin["guest_visits"], 1)
        self.assertEqual(darwin["unique_guests"], 0)
        self.assertEqual(pending.event.going_count, 3)

    def test_group_exports_distinguish_organiser_from_confirmed_attendees(self):
        listing = self.listing()
        booking = self.booking(
            listing, places=3, companion_names=["=Companion", "Not present"],
            host_attendance=[1], guest_attendance=[1],
            host_confirmed=True, guest_confirmed=True,
        )
        exchange = FormalExchange.objects.create(
            original_booking=booking, creditor=listing.host, debtor=booking.guest,
            return_college="darwin", return_by=date(2027, 3, 31),
            return_places=2, status="owed",
        )
        self.client.force_login(self.admin)
        response = self.client.get(self.url, self.all_time)
        self.assertContains(response, "Member guests")
        self.assertContains(response, "1 of 3 attended")
        self.assertContains(response, "Booked by " + self.guest.get_full_name())
        rows = self.csv_data(self.client.get(self.export_url, self.all_time))
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(row["Member organiser"], self.guest.get_full_name())
        self.assertEqual(row["Places booked"], "3")
        self.assertEqual(row["Confirmed guest visits"], "1")
        self.assertEqual(row["Confirmed attendees"], "'=Companion")
        self.assertNotIn("Not present", row["Confirmed attendees"])
        rows = self.csv_data(self.client.get(self.export_url, {**self.all_time, "kind": "exchanges"}))
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(row["Exchange ID"], str(exchange.pk))
        self.assertEqual(row["Original places booked"], "3")
        self.assertEqual(row["Return places agreed"], "2")

    def test_member_participation_counts_roles_without_multiplying_groups_or_dinners(self):
        first = self.listing()
        group = self.booking(first, places=2, companion_names=["Companion"], host_attendance=[0, 1], guest_attendance=[0, 1])
        solo = self.booking(first, self.guest2)
        for booking in (group, solo):
            FormalExchange.objects.create(
                original_booking=booking, creditor=self.host, debtor=booking.guest,
                return_college="darwin", return_by=date(2027, 3, 31),
                return_places=1, status="completed",
            )
        returning = self.listing(host=self.guest, college="darwin")
        self.booking(returning, self.host, purpose="return")
        self.booking(self.listing(), self.guest)
        outside = self.listing(start=datetime(2026, 9, 20, 19, tzinfo=LONDON))
        self.exchange(outside, self.guest2)
        data = statistics(self.filters, self.now)
        rows = {row["member_id"]: row for row in data["member_rows"]}
        self.assertEqual(data["member_rows"][0]["member_id"], self.host.pk)
        self.assertEqual(rows[self.host.pk]["hosted_dinners"], 2)
        self.assertEqual(rows[self.host.pk]["guest_visits"], 1)
        self.assertEqual(rows[self.host.pk]["completed_exchanges"], 2)
        self.assertEqual(rows[self.host.pk]["completed_original_host"], 2)
        self.assertEqual(rows[self.host.pk]["completed_return_host"], 0)
        self.assertEqual(rows[self.guest.pk]["hosted_dinners"], 1)
        self.assertEqual(rows[self.guest.pk]["guest_visits"], 2)
        self.assertEqual(rows[self.guest.pk]["completed_exchanges"], 1)
        self.assertEqual(rows[self.guest.pk]["completed_return_host"], 1)
        self.assertEqual(rows[self.guest2.pk]["completed_exchanges"], 1)
        self.assertEqual(data["totals"]["exchanges_completed"], 2)

    def test_member_report_and_export_remain_admin_only_and_do_not_export_contacts(self):
        self.booking(self.listing())
        for member in (self.guest, self.staff):
            self.client.force_login(member)
            self.assertEqual(self.client.get(self.export_url, {**self.all_time, "kind": "members"}).status_code, 403)
            self.assertEqual(self.client.get(self.url, self.all_time).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url, self.all_time), "Member participation")
        response = self.client.get(self.export_url, {**self.all_time, "kind": "members"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        rows = self.csv_data(response)
        self.assertEqual(len(rows), 3)
        data = str(rows)
        for member in (self.host, self.guest):
            self.assertIn(member.get_full_name(), data)
            self.assertNotIn(member.email, data)
            self.assertNotIn(member.mobile, data)

    def test_personal_dashboard_attendance_excludes_companions_visits(self):
        self.booking(
            self.listing(), places=2, companion_names=["Companion"],
            host_attendance=[1], guest_attendance=[1],
        )
        self.booking(
            self.listing(), places=2, companion_names=["Companion"],
            host_attendance=[0, 1], guest_attendance=[0, 1],
        )
        self.client.force_login(self.guest)
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stats"]["attended"], 1)
