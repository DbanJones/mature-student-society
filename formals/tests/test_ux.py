"""Guard error recovery and state-sensitive controls in the member interface."""
from datetime import timedelta
from html.parser import HTMLParser

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from formals import services
from formals.models import FormalListing
from formals.tests.test_views import member


class DisclosureParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.details = []
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "details":
            self.details.append("open" in attributes)
        elif tag in ("input", "select", "textarea") and attributes.get("name"):
            self.fields[attributes["name"]] = tuple(self.details)

    def handle_endtag(self, tag):
        if tag == "details":
            self.details.pop()


class FormalInterfaceTests(TestCase):
    def setUp(self):
        self.host = member("uxhost")
        self.guest = member("uxguest", "darwin")
        self.start = (timezone.now() + timedelta(days=7)).replace(second=0, microsecond=0)

    def data(self, **changes):
        data = {
            "title": "College dinner", "college": "wolfson", "location": "Porters' lodge",
            "start": self.start, "end": self.start + timedelta(hours=2),
            "booking_closes_at": self.start - timedelta(days=1), "capacity": 3,
            "price_text": "Paid to the host", "invitation_type": "reciprocal",
            "booking_mode": "instant", "return_by": timezone.localdate(self.start + timedelta(days=45)),
            "allowed_return_colleges": ["darwin"], "hosting_confirmed": True,
        }
        data.update(changes)
        return data

    def test_errors_reveal_optional_fields_without_losing_the_submission(self):
        data = self.data(end=self.start - timedelta(hours=1), allowed_return_colleges=["unknown-college"])
        for name in ("start", "end", "booking_closes_at"):
            data[name] = timezone.localtime(data[name]).strftime("%Y-%m-%dT%H:%M")
        data["return_by"] = data["return_by"].isoformat()
        self.client.force_login(self.host)
        response = self.client.post(reverse("formals:create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context["form"].errors), {"end", "allowed_return_colleges"})
        parser = DisclosureParser(response.content.decode())
        self.assertTrue(parser.fields["end"])
        self.assertTrue(all(parser.fields["end"]))
        self.assertTrue(parser.fields["allowed_return_colleges"])
        self.assertTrue(all(parser.fields["allowed_return_colleges"]))
        self.assertContains(response, 'href="#id_end"')
        self.assertContains(response, 'href="#id_allowed_return_colleges"')
        self.assertFalse(FormalListing.objects.exists())

    def test_a_paused_swap_cannot_present_an_acceptance_action(self):
        listing = services.create_listing(self.host, **self.data())
        booking = services.apply_for_place(self.guest, listing.pk, listing.terms_version,
                                           return_college="darwin", can_host=True, accept_terms=True)
        exchange = booking.exchange
        return_start = self.start + timedelta(days=14)
        returned = services.create_listing(self.guest, **self.data(
            college="darwin", title="Return dinner", start=return_start,
            end=return_start + timedelta(hours=2), booking_closes_at=return_start - timedelta(days=1),
            invitation_type="open", return_by=None, allowed_return_colleges=[],
        ))
        proposal = services.propose_return(self.guest, exchange.pk, returned.pk)
        self.client.force_login(self.host)
        action_url = reverse("formals:proposal_action", args=[proposal.pk, "accept"])
        self.assertContains(self.client.get(exchange.get_absolute_url()), action_url)
        services.report_issue(self.guest, exchange.pk, reason="Please review our attendance disagreement")
        response = self.client.get(exchange.get_absolute_url())
        self.assertNotContains(response, action_url)
        self.assertContains(response, "Arranging a return is paused")
