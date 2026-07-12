"""Guide app tests: public read access, member-only writing, revision
history, restore, and slug generation.

Note: templates extend base.html, which reverses URL names from every app,
so the read-view tests pass once all apps' URLconfs are integrated.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.shortcuts import resolve_url
from django.test import TestCase
from django.urls import reverse

from .models import GuidePage

User = get_user_model()


def make_member(username="rt489", **extra):
    """A member with a COMPLETE profile — ProfileCompletionMiddleware would
    otherwise redirect them away from every guide URL."""
    defaults = dict(
        first_name="Robert",
        last_name="Tanaka",
        college="st-edmunds",
        mobile="+44 7700 900123",
        email=f"{username}@example.com",
    )
    defaults.update(extra)
    return User.objects.create_user(username=username, password="pw", **defaults)


def make_page(**extra):
    defaults = dict(
        title="Choosing a mature college",
        slug="choosing-a-mature-college",
        section="colleges",
        content="Four colleges admit mature undergraduates only.",
    )
    defaults.update(extra)
    return GuidePage.objects.create(**defaults)


class GuideReadAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = make_member()
        cls.page = make_page(created_by=cls.member, updated_by=cls.member)
        cls.page.save_revision(cls.member)
        cls.draft = make_page(
            title="Unfinished draft", slug="unfinished-draft",
            section="faq", content="Half-written.", is_published=False,
            created_by=cls.member, updated_by=cls.member,
        )
        cls.draft.save_revision(cls.member)

    def test_anonymous_can_read_index(self):
        response = self.client.get(reverse("guide:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.page.title)

    def test_anonymous_can_read_page(self):
        response = self.client.get(reverse("guide:page", args=[self.page.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mature undergraduates")

    def test_search_filters_pages(self):
        response = self.client.get(reverse("guide:index"), {"q": "undergraduates"})
        self.assertContains(response, self.page.title)
        response = self.client.get(reverse("guide:index"), {"q": "zzz-no-match"})
        self.assertNotContains(response, self.page.title)

    def test_unpublished_hidden_from_index_and_search(self):
        response = self.client.get(reverse("guide:index"))
        self.assertNotContains(response, "Unfinished draft")
        response = self.client.get(reverse("guide:index"), {"q": "Unfinished"})
        self.assertNotContains(response, "Unfinished draft")

    def test_unpublished_page_404_for_anonymous(self):
        response = self.client.get(reverse("guide:page", args=["unfinished-draft"]))
        self.assertEqual(response.status_code, 404)

    def test_unpublished_page_visible_to_member_with_banner(self):
        self.client.login(username="rt489", password="pw")
        response = self.client.get(reverse("guide:page", args=["unfinished-draft"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "unpublished")

    def test_anonymous_redirected_to_login_for_write_views(self):
        login_url = resolve_url(settings.LOGIN_URL)
        urls = [
            reverse("guide:new"),
            reverse("guide:edit", args=[self.page.slug]),
            reverse("guide:history", args=[self.page.slug]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith(login_url))

    def test_anonymous_cannot_restore(self):
        login_url = resolve_url(settings.LOGIN_URL)
        rev = self.page.revisions.first()
        response = self.client.post(
            reverse("guide:restore", args=[self.page.slug, rev.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(login_url))


class GuideEditingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = make_member("efw22", first_name="Elena", last_name="Fitzwilliam-Wright")
        cls.editor = make_member("jm901", first_name="James", last_name="MacAllister")

    def test_create_generates_slug_and_initial_revision(self):
        self.client.login(username="efw22", password="pw")
        response = self.client.post(reverse("guide:new"), {
            "title": "Cycling with Children",
            "section": "family",
            "content": "Get a **trailer**, not a seat.",
        })
        page = GuidePage.objects.get(title="Cycling with Children")
        self.assertEqual(page.slug, "cycling-with-children")
        self.assertRedirects(
            response, page.get_absolute_url(), fetch_redirect_response=False
        )
        self.assertEqual(page.created_by, self.author)
        self.assertEqual(page.updated_by, self.author)
        self.assertEqual(page.revisions.count(), 1)
        self.assertEqual(page.revisions.first().content, "Get a **trailer**, not a seat.")
        self.assertEqual(page.revisions.first().editor, self.author)

    def test_slug_collision_gets_numeric_suffix(self):
        self.client.login(username="efw22", password="pw")
        data = {"title": "Test Page", "section": "faq", "content": "First."}
        self.client.post(reverse("guide:new"), data)
        self.client.post(reverse("guide:new"), {**data, "content": "Second."})
        self.client.post(reverse("guide:new"), {**data, "content": "Third."})
        slugs = set(GuidePage.objects.values_list("slug", flat=True))
        self.assertIn("test-page", slugs)
        self.assertIn("test-page-2", slugs)
        self.assertIn("test-page-3", slugs)

    def test_reserved_slug_new_is_never_assigned(self):
        self.client.login(username="efw22", password="pw")
        self.client.post(reverse("guide:new"), {
            "title": "New", "section": "faq", "content": "Reserved-word title.",
        })
        page = GuidePage.objects.get(title="New")
        self.assertEqual(page.slug, "new-2")

    def test_edit_saves_revision_and_keeps_slug(self):
        page = make_page(created_by=self.author, updated_by=self.author)
        page.save_revision(self.author)
        self.client.login(username="jm901", password="pw")
        response = self.client.post(reverse("guide:edit", args=[page.slug]), {
            "title": "Choosing a mature college (2026 update)",
            "section": "colleges",
            "content": "Fresh advice for the new admissions round.",
        })
        self.assertEqual(response.status_code, 302)
        page.refresh_from_db()
        self.assertEqual(page.slug, "choosing-a-mature-college")  # immutable
        self.assertEqual(page.content, "Fresh advice for the new admissions round.")
        self.assertEqual(page.updated_by, self.editor)
        revisions = list(page.revisions.all())  # newest first
        self.assertEqual(len(revisions), 2)
        self.assertEqual(revisions[0].content, "Fresh advice for the new admissions round.")
        self.assertEqual(revisions[0].editor, self.editor)

    def test_preview_does_not_save(self):
        self.client.login(username="efw22", password="pw")
        self.client.post(reverse("guide:new"), {
            "title": "Draft thoughts", "section": "faq",
            "content": "Not ready.", "preview": "1",
        })
        self.assertFalse(GuidePage.objects.filter(title="Draft thoughts").exists())

    def test_restore_round_trips_content(self):
        page = make_page(
            title="Punting", slug="punting", section="social",
            content="Original wisdom.", created_by=self.author, updated_by=self.author,
        )
        page.save_revision(self.author)
        self.client.login(username="jm901", password="pw")
        self.client.post(reverse("guide:edit", args=["punting"]), {
            "title": "Punting", "section": "social", "content": "Regrettable rewrite.",
        })
        original = page.revisions.get(content="Original wisdom.")
        response = self.client.post(
            reverse("guide:restore", args=["punting", original.pk])
        )
        self.assertRedirects(
            response, page.get_absolute_url(), fetch_redirect_response=False
        )
        page.refresh_from_db()
        self.assertEqual(page.content, "Original wisdom.")
        self.assertEqual(page.updated_by, self.editor)
        # Restore is a normal edit: it appends a revision rather than
        # rewriting history.
        self.assertEqual(page.revisions.count(), 3)
        self.assertEqual(page.revisions.first().content, "Original wisdom.")

    def test_restore_rejects_get(self):
        page = make_page(created_by=self.author, updated_by=self.author)
        page.save_revision(self.author)
        rev = page.revisions.first()
        self.client.login(username="jm901", password="pw")
        response = self.client.get(
            reverse("guide:restore", args=[page.slug, rev.pk])
        )
        self.assertEqual(response.status_code, 405)

    def test_write_views_render_for_members(self):
        page = make_page(created_by=self.author, updated_by=self.author)
        page.save_revision(self.author)
        self.client.login(username="jm901", password="pw")
        self.client.post(  # a second revision so history shows a delta
            reverse("guide:edit", args=[page.slug]),
            {"title": page.title, "section": page.section, "content": "Longer text than before."},
        )
        rev = page.revisions.first()
        for url in [
            reverse("guide:new"),
            reverse("guide:edit", args=[page.slug]),
            reverse("guide:history", args=[page.slug]),
            reverse("guide:revision", args=[page.slug, rev.pk]),
        ]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_restore_404_for_revision_of_other_page(self):
        page = make_page(created_by=self.author, updated_by=self.author)
        page.save_revision(self.author)
        other = make_page(
            title="Other", slug="other", section="faq", content="x",
            created_by=self.author, updated_by=self.author,
        )
        rev = page.revisions.first()
        self.client.login(username="jm901", password="pw")
        response = self.client.post(
            reverse("guide:restore", args=[other.slug, rev.pk])
        )
        self.assertEqual(response.status_code, 404)
