"""The mailer's "own design" mode: a complete HTML email pasted into the
body goes out as written, with the audience's footer and a text part."""

from unittest import mock

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from core import emailbody

from .tests import PanelTestCase, html_part

OWN = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>The MSS Winter Ball 2026</title>
<!--[if mso]><noscript><xml>office</xml></noscript><![endif]-->
<style>
  body { margin:0; }
  @media screen and (max-width:620px) { .h1 { font-size:40px !important; } }
</style>
</head>
<body style="margin:0; background-color:#eef1f5;">
<div style="display:none; max-height:0; overflow:hidden;">Last year it sold out in days. &#847; &#847;</div>
<table role="presentation" width="100%"><tr><td align="center" bgcolor="#13233b" style="background-color:#13233b; padding:44px;">
  <h1 class="h1" style="font-family:Georgia, serif; font-size:52px; color:#f7f2e7;">The Winter Ball</h1>
  <p style="color:#f7f2e7;"><strong>Wednesday 2 December 2026</strong><br>7pm to 11pm &middot; Churchill College</p>
  <a href="https://www.cambridgesu.co.uk/shop/product/10046380/" target="_blank" style="color:#13233b;">Buy your ticket &middot; &pound;45</a>
</td></tr>
<tr><td>
  <ul><li><strong>&pound;45</strong> covers dinner</li><li>Guests welcome</li></ul>
  <p>Questions? Email <a href="mailto:maturesoc@cambridgesu.co.uk">maturesoc@cambridgesu.co.uk</a></p>
  <script>alert(1)</script>
</td></tr></table>
</body>
</html>
"""


@override_settings(SITE_URL="")
class OwnHtmlMailerTests(PanelTestCase):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_it_is_told_apart_from_markdown(self):
        for body in (OWN, "<html><body>Hi</body></html>", "  <table><tr><td>Hi</td></tr></table>",
                     "<!-- note -->\n<div>Hi</div>"):
            self.assertTrue(emailbody.is_own_html(body), body[:30])
        for body in ("Hello all,\n\n<b>not a document</b>", "<https://example.org>", "## Heading", ""):
            self.assertFalse(emailbody.is_own_html(body), body[:30])

    def test_a_send_goes_out_as_written_with_the_footer_and_a_text_part(self):
        self.client.post(reverse("panel:mailer"), {
            "audience": "members", "subject": "The MSS Winter Ball 2026", "body": OWN, "action": "send",
        })
        message = next(m for m in mail.outbox if "mem1@cam.ac.uk" in m.bcc)
        html = html_part(message)
        # the design, untouched: no site shell, no escaped tags
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn('<h1 class="h1" style="font-family:Georgia, serif; font-size:52px; color:#f7f2e7;">The Winter Ball</h1>', html)
        self.assertIn('bgcolor="#13233b"', html)
        self.assertIn("@media screen and (max-width:620px)", html)
        self.assertNotIn("&lt;table", html)
        self.assertNotIn("<script", html)
        # the unsubscribe footer is inside the document, before </body>
        self.assertIn("/unsubscribe/", html)
        self.assertLess(html.index("/unsubscribe/"), html.lower().rindex("</body>"))
        self.assertIn("List-Unsubscribe", message.extra_headers)
        # the text part: the words, links with their addresses, nothing from the head or the hidden line
        text = message.body
        self.assertIn("The Winter Ball", text)
        self.assertIn("Wednesday 2 December 2026\n7pm to 11pm · Churchill College", text)
        self.assertIn("Buy your ticket · £45 (https://www.cambridgesu.co.uk/shop/product/10046380/)", text)
        self.assertIn("- £45 covers dinner\n- Guests welcome", text)
        self.assertIn("Email maturesoc@cambridgesu.co.uk", text)
        self.assertNotIn("maturesoc@cambridgesu.co.uk (", text)
        for absent in ("margin:0", "sold out in days", "alert(1)", "office", "<"):
            self.assertNotIn(absent, text.split("\n--\n")[0])
        self.assertIn("unsubscribe", text.split("\n--\n")[1])

    def test_the_preview_is_the_email(self):
        response = self.client.post(reverse("panel:mailer_preview"), {
            "body": OWN, "subject": "The MSS Winter Ball 2026", "audience": "members",
        })
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn('bgcolor="#13233b"', page)
        self.assertIn("/unsubscribe/", page)
        self.assertIn("img-src https:", response["Content-Security-Policy"])

    def test_one_address_gets_no_footer_and_size_is_capped(self):
        self.client.post(reverse("panel:mailer"), {
            "audience": "address", "recipient": "someone@example.org",
            "subject": "Ball", "body": OWN, "action": "send",
        })
        self.assertNotIn("/unsubscribe/", html_part(mail.outbox[0]))
        # many more "<" than Markdown may have is fine; an enormous paste is not
        many = "<html><body>" + "<p>x</p>" * 2000 + "</body></html>"
        self.assertIsNone(emailbody.body_problem(many))
        self.assertIn("too long", emailbody.body_problem("<html>" + "x" * emailbody.MAX_OWN_HTML))
        response = self.client.post(reverse("panel:mailer"), {
            "audience": "members", "subject": "Ball", "body": "<html><body><style>p{}</style></body></html>", "action": "send",
        })
        self.assertContains(response, "no words in this HTML")

    # --- "Draft with AI" and HTML ---

    def ai(self, answer, **data):
        from core.models import SiteConfig

        config = SiteConfig.get()
        config.email_api_key = "k"
        config.save()
        post = {"audience": "members", "subject": "Ball", "body": OWN, "action": "ai_draft"}
        post.update(data)
        with mock.patch("panel.ai.draft_email", return_value=answer) as draft:
            response = self.client.post(reverse("panel:mailer"), post)
        return response, draft

    def test_ai_revises_an_html_email_and_keeps_it_html(self):
        revised = OWN.replace("The Winter Ball</h1>", "The Winter Ball is back</h1>").replace("<script>alert(1)</script>", "")
        response, draft = self.ai("```html\n" + revised + "```", instructions="Make the headline punchier")
        self.assertEqual(response.context["form"]["body"].value(), revised.strip())
        system_prompt = draft.call_args.args[2]
        self.assertIn("complete HTML email", system_prompt)
        self.assertIn("never a web address", system_prompt)
        self.assertIn("Make the headline punchier", system_prompt)
        self.assertEqual(draft.call_args.kwargs["max_tokens"], 8000)
        self.assertEqual(draft.call_args.kwargs["timeout"], 100)

    def test_ai_designs_html_from_the_ordinary_draft_when_ticked(self):
        markdown = (
            "Hello all,\n\n## Quiz night\n\n[RSVP and details](http://testserver/events/quiz/)\n\n"
            "Write to [us](mailto:mss@example.org)."
        )
        designed = (
            '<!DOCTYPE html><html><body><table role="presentation"><tr><td><h1>Quiz night</h1>'
            '<a href="http://testserver/events/quiz/">Book your seat</a></td></tr></table></body></html>'
        )
        response, draft = self.ai(designed, body=markdown, ai_html="on")
        self.assertEqual(response.context["form"]["body"].value(), designed)
        self.assertRegex(response.content.decode(), r'name="ai_html"[^>]*checked')
        self.assertIn("into a handsome designed HTML email", draft.call_args.args[2])
        # without the tick the ordinary draft stays Markdown, with the ordinary limits
        response, draft = self.ai(markdown.replace("Hello all", "Hi everyone"), body=markdown)
        self.assertIn("must stay in Markdown", draft.call_args.args[2])
        self.assertNotIn("max_tokens", draft.call_args.kwargs)
        self.assertEqual(response.context["form"]["body"].value(), markdown.replace("Hello all", "Hi everyone"))

    def test_an_ai_answer_that_changes_links_pictures_or_is_cut_short_is_refused(self):
        ok = OWN.replace("<script>alert(1)</script>", "")
        refused = {
            "a changed link": ok.replace("product/10046380/", "product/999/"),
            "a dropped link": ok.replace('<a href="https://www.cambridgesu.co.uk/shop/product/10046380/" target="_blank" style="color:#13233b;">Buy your ticket &middot; &pound;45</a>', ""),
            "a new email address": ok.replace("</td></tr></table>", '<a href="mailto:else@example.org">x</a></td></tr></table>', 1),
            "a relative link": ok.replace("</td></tr></table>", '<a href="/events/">x</a></td></tr></table>', 1),
            "a new picture": ok.replace("<ul>", '<img src="https://tracker.example/p.gif"><ul>'),
            "cut short": ok[: ok.index("<ul>")],
            "not HTML": "Here is your email: Winter Ball on 2 December.",
        }
        for why, answer in refused.items():
            response, _draft = self.ai(answer)
            self.assertContains(response, "AI drafting failed", msg_prefix=why)
            self.assertEqual(response.context["form"]["body"].value(), OWN, why)
