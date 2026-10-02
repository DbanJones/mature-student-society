"""Testimonials: members submit, the committee approves, the public reads."""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.models import SiteConfig

from .models import Testimonial


def make_user(username, **extra):
    defaults = dict(
        first_name="Test", last_name=username.title(), college="wolfson",
        mobile="+447700900000", email=f"{username}@cam.ac.uk", crsid=username,
    )
    defaults.update(extra)
    return User.objects.create_user(username=username, **defaults)


class TestimonialTests(TestCase):
    def setUp(self):
        self.admin = make_user("adm1", is_portal_admin=True)
        self.alice = make_user("ali1")
        self.bob = make_user("bob1")
        self.index = reverse("testimonials:index")

    def _approved(self, user, body, **extra):
        return Testimonial.objects.create(
            author=user, author_name=user.get_full_name(), author_college="Wolfson",
            body=body, status=Testimonial.Status.APPROVED,
            reviewed_at=timezone.now(), **extra,
        )

    def test_public_page_shows_only_approved_and_hides_anonymous_authors(self):
        self._approved(self.alice, "Lovely people.")
        self._approved(self.bob, "Secretly great.", is_anonymous=True)
        Testimonial.objects.create(author=self.bob, author_name="Test Bob1", body="Still pending.")
        response = self.client.get(self.index)
        self.assertContains(response, "Lovely people.")
        self.assertContains(response, "Test Ali1")
        self.assertContains(response, "Secretly great.")
        self.assertContains(response, "An MSS member")
        self.assertNotContains(response, "Test Bob1")
        self.assertNotContains(response, "Still pending.")

    def test_member_submits_signed_then_anonymous_with_a_pending_cap(self):
        self.client.force_login(self.alice)
        submit = reverse("testimonials:submit")
        self.client.post(submit, {"body": "Great fun.", "is_anonymous": ""})
        first = Testimonial.objects.get()
        self.assertEqual(first.status, Testimonial.Status.PENDING)
        self.assertEqual(first.author_name, "Test Ali1")
        self.assertEqual(first.author_college, "Wolfson")
        self.assertFalse(first.is_anonymous)
        # One pending at a time.
        self.client.post(submit, {"body": "And again."})
        self.assertEqual(Testimonial.objects.count(), 1)
        first.review(self.admin, Testimonial.Status.APPROVED)
        self.client.post(submit, {"body": "Quietly brilliant.", "is_anonymous": "on"})
        self.assertEqual(Testimonial.objects.count(), 2)
        self.assertTrue(Testimonial.objects.latest("submitted_at").is_anonymous)

    def test_submission_requires_login_and_some_words(self):
        response = self.client.post(reverse("testimonials:submit"), {"body": "x"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
        self.client.force_login(self.alice)
        response = self.client.post(reverse("testimonials:submit"), {"body": "   "})
        self.assertContains(response, "Write a few words first.")
        self.assertFalse(Testimonial.objects.exists())

    def test_member_can_withdraw_their_own_but_not_someone_elses(self):
        mine = self._approved(self.alice, "Mine.")
        theirs = self._approved(self.bob, "Theirs.")
        self.client.force_login(self.alice)
        self.client.post(reverse("testimonials:withdraw", args=[mine.pk]))
        self.assertFalse(Testimonial.objects.filter(pk=mine.pk).exists())
        response = self.client.post(reverse("testimonials:withdraw", args=[theirs.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Testimonial.objects.filter(pk=theirs.pk).exists())

    def test_homepage_band_shows_featured_first(self):
        self._approved(self.alice, "Ordinary praise.")
        self._approved(self.bob, "Headline praise.", is_featured=True)
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, "In members")
        body = response.content.decode()
        self.assertLess(body.index("Headline praise."), body.index("Ordinary praise."))

    def test_nav_tab_follows_the_visibility_setting(self):
        nav_link = reverse("testimonials:index")
        self.assertContains(self.client.get(reverse("core:home")), nav_link)
        config = SiteConfig.get()
        config.tab_visibility = {"testimonials": "admins"}
        config.save()
        self.assertNotContains(self.client.get(reverse("core:home")), nav_link)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("core:home")), nav_link)
