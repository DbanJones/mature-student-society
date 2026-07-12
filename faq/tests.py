"""Guide-toolbox tests: the DB-backed contact map (including admin editing
and the tailored email), and the college/department data pages."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import COLLEGES

from .data import COLLEGES_INFO, DEPARTMENTS_INFO
from .models import ContactNode

User = get_user_model()


def make_admin():
    return User.objects.create_user(
        username="adm9", password="pw", email="adm9@cam.ac.uk",
        first_name="Ada", last_name="Admin", college="wolfson",
        mobile="+44 7700 900000", is_portal_admin=True,
    )


class FaqPageTests(TestCase):
    def test_old_faq_index_redirects_to_guide(self):
        response = self.client.get(reverse("faq:index"))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, reverse("guide:index"))

    def test_contacts_page_renders_flow_and_hierarchy(self):
        response = self.client.get(reverse("faq:contacts"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "contact-tree")   # wizard payload
        self.assertContains(response, "org-tree")       # hierarchy diagram
        self.assertContains(response, "org-question")

    def test_college_index_and_detail(self):
        response = self.client.get(reverse("faq:colleges"))
        self.assertEqual(response.status_code, 200)
        for slug in ("hughes-hall", "wolfson", "trinity", "darwin"):
            detail = self.client.get(reverse("faq:college", args=[slug]))
            self.assertEqual(detail.status_code, 200, slug)
        self.assertEqual(
            self.client.get(reverse("faq:college", args=["hogwarts"])).status_code,
            404,
        )

    def test_department_index_and_detail(self):
        response = self.client.get(reverse("faq:departments"))
        self.assertEqual(response.status_code, 200)
        for slug in DEPARTMENTS_INFO:
            detail = self.client.get(reverse("faq:department", args=[slug]))
            self.assertEqual(detail.status_code, 200, slug)


class FaqDataTests(TestCase):
    def test_every_real_college_has_a_page(self):
        expected = {slug for slug, _ in COLLEGES if slug != "other"}
        self.assertEqual(expected, set(COLLEGES_INFO))


class ContactTreeTests(TestCase):
    """The seeded tree is sound and every result carries a tailored email."""

    def test_seeded_tree_is_closed_and_terminates(self):
        root = ContactNode.get_root()
        self.assertIsNotNone(root)
        self.assertFalse(root.is_result)

        def walk(node):
            if node.is_result:
                for field in ("who", "action", "escalate", "keep"):
                    self.assertTrue(getattr(node, field), f"{node} missing {field}")
                email = node.build_email()
                self.assertIn("SUBJECT:", email)
                self.assertIn("[date", email)
            else:
                self.assertTrue(node.question, f"{node} missing question")
                children = list(node.children.all())
                self.assertTrue(children, f"{node} has no options")
                for child in children:
                    self.assertTrue(child.option_label)
                    walk(child)

        walk(root)

    def test_email_uses_custom_template_when_set(self):
        node = ContactNode.objects.filter(kind="result").first()
        node.email_template = "SUBJECT: custom\nDear [X], help."
        node.save()
        self.assertEqual(node.build_email(), "SUBJECT: custom\nDear [X], help.")

    def test_email_prepopulated_from_profile(self):
        member = User.objects.create_user(
            username="pp001", password="pw", crsid="pp001",
            first_name="Priya", last_name="Natarajan",
            college="wolfson", course="PhD Plant Sciences",
            mobile="+44 7700 900001",
        )
        node = ContactNode.objects.filter(kind="result").first()
        email = node.build_email(member)
        self.assertIn("Priya Natarajan", email)
        self.assertIn("Wolfson", email)
        self.assertIn("PhD Plant Sciences student at Wolfson", email)
        self.assertIn("(pp001)", email)
        self.assertNotIn("[name]", email)
        self.assertNotIn("[College]", email)
        self.assertNotIn("[year]", email)  # course carries the level already
        # Anonymous visitors keep the blanks.
        anon_email = node.build_email()
        self.assertIn("[name]", anon_email)
        self.assertIn("[College]", anon_email)

    def test_custom_template_tokens_also_personalised(self):
        member = User.objects.create_user(
            username="pp002", password="pw", crsid="pp002",
            first_name="Rob", last_name="Tan", college="darwin",
            mobile="+44 7700 900002",
        )
        node = ContactNode.objects.filter(kind="result").first()
        node.email_template = "Dear porter, I am [name] of [College] ([CRSid])."
        node.save()
        self.assertEqual(
            node.build_email(member),
            "Dear porter, I am Rob Tan of Darwin (pp002).",
        )

    def test_contacts_page_embeds_personalised_email_for_member(self):
        member = User.objects.create_user(
            username="pp003", password="pw", first_name="Elena",
            last_name="Wright", college="lucy-cavendish",
            mobile="+44 7700 900003",
        )
        self.client.force_login(member)
        response = self.client.get(reverse("faq:contacts"))
        self.assertContains(response, "Elena Wright")
        self.assertContains(response, "Lucy Cavendish")


class ContactMapAdminTests(TestCase):
    """Admins can add, edit and delete nodes; the root is protected."""

    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)
        self.root = ContactNode.get_root()

    def test_non_admin_cannot_edit(self):
        member = User.objects.create_user(
            username="mem9", password="pw", first_name="Mia", last_name="M",
            college="darwin", mobile="+44 7700 900001",
        )
        self.client.force_login(member)
        self.assertEqual(
            self.client.get(reverse("panel:contact_map")).status_code, 403
        )

    def test_add_edit_delete_node(self):
        # Add a result option under the root.
        response = self.client.post(
            reverse("panel:contact_node_add") + f"?parent={self.root.pk}",
            {
                "parent": self.root.pk,
                "kind": "result", "option_label": "🚲 My bike vanished",
                "sort_order": 95,
                "question": "",
                "who": "The Porters' Lodge, then the police non-emergency line",
                "action": "Report it with the frame number.",
                "escalate": "Insurance and 101 if stolen.",
                "keep": "Frame number, lock remains, photos.",
                "email_template": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        node = ContactNode.objects.get(option_label="🚲 My bike vanished")
        self.assertIn("Porters", node.build_email())

        # Edit it.
        response = self.client.post(
            reverse("panel:contact_node_edit", args=[node.pk]),
            {
                "kind": "result", "option_label": "🚲 Bike stolen",
                "sort_order": 95, "question": "",
                "who": node.who, "action": node.action,
                "escalate": node.escalate, "keep": node.keep,
                "email_template": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        node.refresh_from_db()
        self.assertEqual(node.option_label, "🚲 Bike stolen")

        # It appears on the public page; then delete it.
        self.assertContains(self.client.get(reverse("faq:contacts")), "Bike stolen")
        response = self.client.post(
            reverse("panel:contact_node_delete", args=[node.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ContactNode.objects.filter(pk=node.pk).exists())

    def test_root_cannot_be_deleted(self):
        self.client.post(reverse("panel:contact_node_delete", args=[self.root.pk]))
        self.assertTrue(ContactNode.objects.filter(pk=self.root.pk).exists())

    def test_question_validation(self):
        response = self.client.post(
            reverse("panel:contact_node_add") + f"?parent={self.root.pk}",
            {
                "parent": self.root.pk,
                "kind": "question", "option_label": "Half-made branch",
                "sort_order": 96, "question": "",
                "who": "", "action": "", "escalate": "", "keep": "",
                "email_template": "",
            },
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with errors
        self.assertFalse(
            ContactNode.objects.filter(option_label="Half-made branch").exists()
        )
