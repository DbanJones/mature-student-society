"""core/emailbody.py: one piece of writing in, a text part and an HTML part
out, and never a web address as the visible words of the HTML part."""

import html as html_lib
import re
from html.parser import HTMLParser
from unittest import mock

from django.test import SimpleTestCase, TestCase

from core import emailbody
from core.models import SiteConfig

BASE = "https://mss.example"
# Any text between two tags that shows a web address (what Outlook would garble).
VISIBLE_ADDRESS = r">[^<]*(?:https?://|www\.)[^<]*<"
BUTTON_CLASS = 'class="em-btn"'


def parts(body, **kwargs):
    return emailbody.render_parts(body, **kwargs)


def links(html):
    """``[(address, words), ...]`` for every link in a piece of HTML."""
    return [(href, re.sub(r"<[^>]+>", "", words))
            for href, words in re.findall(r'<a href="([^"]*)"[^>]*>(.*?)</a>', html, re.S)]


class RenderPartsTests(SimpleTestCase):
    def test_link_shows_its_words(self):
        _text, html = parts("Please [RSVP](https://x.example/a) by Friday.")
        self.assertIn('href="https://x.example/a"', html)
        self.assertEqual(links(html), [("https://x.example/a", "RSVP")])

    def test_no_address_is_ever_visible(self):
        _text, html = parts(
            "See https://x.example/one and www.example.org/two today.\n\n"
            "[https://x.example/a](https://x.example/a)\n\n"
            "- item [https://x.example/b](https://x.example/b)\n"
            "- www.example.net\n"
        )
        self.assertEqual(re.findall(VISIBLE_ADDRESS, html), [])
        # every address is still there to follow, behind words
        self.assertEqual([href for href, _words in links(html)], [
            "https://x.example/one", "https://www.example.org/two", "https://x.example/a",
            "https://x.example/b", "https://www.example.net",
        ])

    def test_bare_site_address_gets_site_words(self):
        _text, html = parts(
            "Go to https://mss.example/events/pub-night/ or https://mss.example/winter-ball/ "
            "or https://mss.example/nowhere/else/ now.",
            base=BASE, site_name="MSS",
        )
        self.assertEqual(links(html), [
            ("https://mss.example/events/pub-night/", "the event page"),
            ("https://mss.example/winter-ball/", "the Winter Ball page"),
            ("https://mss.example/nowhere/else/", "this page on the MSS website"),
        ])

    def test_bare_foreign_address_shows_its_host(self):
        _text, html = parts("Map: https://www.maps.example.org/x_y here.")
        self.assertEqual(links(html), [("https://www.maps.example.org/x_y", "maps.example.org")])

    def test_link_on_its_own_line_is_a_button(self):
        _text, html = parts("Hello.\n\n[RSVP and details](https://x.example/a)\n\nBye.")
        self.assertEqual(html.count(BUTTON_CLASS), 1)
        self.assertEqual(links(html), [("https://x.example/a", "RSVP and details")])
        # a bare address alone on its line is a button too, and says what it opens
        _text, html = parts("https://mss.example/events/", base=BASE, site_name="MSS")
        self.assertEqual(html.count(BUTTON_CLASS), 1)
        self.assertEqual(links(html), [("https://mss.example/events/", "Open the events calendar")])

    def test_link_in_a_sentence_is_not(self):
        _text, html = parts("Please [RSVP](https://x.example/a) by Friday.")
        self.assertNotIn(BUTTON_CLASS, html)
        self.assertNotIn("<table", html)

    def test_link_in_a_list_or_quote_is_not(self):
        _text, html = parts("- [One](https://x.example/a)\n\n> [Two](https://x.example/b)\n")
        self.assertNotIn(BUTTON_CLASS, html)
        self.assertEqual(links(html), [("https://x.example/a", "One"), ("https://x.example/b", "Two")])

    def test_small_has_no_buttons(self):
        _text, html = parts("[RSVP and details](https://x.example/a)", small=True)
        self.assertNotIn(BUTTON_CLASS, html)
        self.assertEqual(links(html), [("https://x.example/a", "RSVP and details")])

    def test_typed_html_is_shown_as_text(self):
        _text, html = parts("<script>alert(1)</script><img src=x onerror=alert(1)>")
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)

    def test_only_web_and_email_links_survive(self):
        for target in ("javascript:alert(1)", "data:text/html,hi", "vbscript:x", "ftp://x.example/",
                       "java\tscript:alert(1)", "<java\nscript:alert(1)>"):
            _text, html = parts(f"Before [x]({target}) after.")
            self.assertNotIn("href", html, target)
            self.assertNotIn("<a", html, target)
            self.assertIn("x", re.sub(r"<[^>]+>", "", html), target)

    def test_relative_link(self):
        _text, html = parts("A [x](/events/) here.", base=BASE)
        self.assertEqual(links(html), [("https://mss.example/events/", "x")])
        _text, html = parts("A [x](/events/) here.")
        self.assertNotIn("href", html)
        self.assertIn("A x here.", html)
        _text, html = parts("A [x](//x.example/a) and [x](www.example.org) here.")
        self.assertEqual(links(html), [("https://x.example/a", "x"), ("https://www.example.org", "x")])

    def test_picture_becomes_a_link(self):
        _text, html = parts("![The hall](/media/public/pictures/a.jpg)", base=BASE)
        self.assertNotIn("<img", html)
        self.assertEqual(html.count(BUTTON_CLASS), 1)
        self.assertEqual(links(html), [("https://mss.example/media/public/pictures/a.jpg", "The hall")])
        _text, html = parts("![](/media/public/pictures/a.jpg)", base=BASE)
        self.assertNotIn("<img", html)
        self.assertEqual(links(html), [("https://mss.example/media/public/pictures/a.jpg", "View picture")])

    def test_email_address_is_linked_as_typed(self):
        _text, html = parts("Write to me@example.org.")
        self.assertIn('href="mailto:me@example.org"', html)
        self.assertEqual(links(html), [("mailto:me@example.org", "me@example.org")])
        self.assertIn("</a>.</p>", html)  # the full stop is outside the link

    def test_code_is_escaped_once(self):
        _text, html = parts("Use `a & b <c>` here.")
        self.assertIn("a &amp; b &lt;c&gt;", html)
        self.assertNotIn("&amp;amp;", html)
        self.assertNotIn("&amp;lt;", html)

    def test_trailing_punctuation_is_not_part_of_an_address(self):
        _text, html = parts("(see https://x.example/a_(b)).")
        self.assertEqual([href for href, _words in links(html)], ["https://x.example/a_(b)"])
        self.assertIn("</a>).</p>", html)

    def test_text_part(self):
        text, _html = parts(
            "## This week\n\n"
            "### Pub night\nSocial · Fri 6 Nov, 7pm\nThe Anchor\n[RSVP and details](https://x.example/e/1/)\n\n"
            "Read [the guide](https://x.example/guide) first.\n\n"
            "- one\n- two\n\n"
            "<b>Bold</b> typed as HTML.\n"
        )
        self.assertIn("This week\n---------\n", text)  # a heading, underlined
        self.assertIn("RSVP and details: https://x.example/e/1/", text)  # a button
        self.assertIn("Read the guide (https://x.example/guide) first.", text)  # a link in a sentence
        self.assertIn("\n- one\n- two", text)
        # an event's details sit straight under its title
        self.assertIn(
            "Pub night\nSocial · Fri 6 Nov, 7pm\nThe Anchor\nRSVP and details: https://x.example/e/1/", text
        )
        # no tag of the email's own making; what was typed is shown as typed
        self.assertEqual(re.findall(r"</?(?:p|h2|h3|a|ul|li|br|table|td)\b", text), [])
        self.assertIn("<b>Bold</b> typed as HTML.", text)

    def test_md_escape(self):
        title = "Free [beer](http://evil.example) # <b>now</b> & 1 > 0"
        text, html = parts("### " + emailbody.md_escape(title))
        self.assertNotIn("href", html)
        self.assertNotIn("<b>", html)
        self.assertEqual(html.count("<h3"), 1)
        self.assertEqual(text, title)  # every character is still there to read
        self.assertIn("Free [beer](http://evil.example) # &lt;b&gt;now&lt;/b&gt; &amp; 1 &gt; 0", html)
        # the same words in a sentence and in bold stay words
        _text, html = parts("**" + emailbody.md_escape("*stars* and _lines_ and `ticks`") + "** tonight")
        self.assertIn("<strong>*stars* and _lines_ and `ticks`</strong> tonight", html)
        self.assertEqual(emailbody.md_escape("  two\n lines  "), "two lines")

    def test_link_targets(self):
        found = emailbody.link_targets(
            "[a](/events/) and https://x.example/b and me@example.org and "
            "[m](mailto:a@b.example) and www.example.org/c",
            base=BASE,
        )
        self.assertIsInstance(found, frozenset)
        self.assertEqual(found, {"https://mss.example/events/", "https://x.example/b", "https://www.example.org/c"})
        self.assertEqual(emailbody.link_targets("[a](/events/)"), frozenset())  # no base: not a link
        # a button counts, and the words of a link do not
        self.assertEqual(
            emailbody.link_targets("[One](https://x.example/a)"),
            emailbody.link_targets("Now [another name](https://x.example/a) in a sentence."),
        )

    def test_mail_targets(self):
        found = emailbody.mail_targets(
            "Write to Me@Example.org or [the committee](mailto:committee@example.org?subject=Hi) "
            "or see https://x.example/b",
        )
        self.assertIsInstance(found, frozenset)
        self.assertEqual(found, {"me@example.org", "committee@example.org"})
        self.assertEqual(emailbody.mail_targets("No address here, only https://x.example/b"), frozenset())

    def test_emphasis_characters_inside_a_typed_address(self):
        # "_layouts ... Minutes_" and "*wine*" must not be read as italics: the
        # address is one link, whole, in both parts
        addresses = (
            "https://universityofcambridgecloud.sharepoint.com/:w:/r/sites/MSS/_layouts/15/Doc.aspx"
            "?sourcedoc=%7BABC%7D&file=Minutes_.docx&action=default",
            "https://example.org/path/_private_/doc",
            "https://github.com/x/y/blob/main/__init__.py",
            "https://example.org/search?q=*wine*&page=2",
        )
        for address in addresses:
            for shape in ("Sign up at {} today", "Here:\n\n{}\n\nThanks", "**{}**", "Use `{}` now",
                          "- one {}\n- two", "> {}", "[Sign up]({})"):
                with self.subTest(address=address, shape=shape):
                    text, html = parts(shape.format(address), base=BASE)
                    self.assertEqual([html_lib.unescape(href) for href, _words in links(html)], [address])
                    self.assertIn(address, text)
                    self.assertNotIn("\\", html)
                    self.assertNotIn("&#", html)
                    self.assertNotIn("<em>", html)

    def test_an_address_already_escaped_in_the_draft_still_works(self):
        text, html = parts("Zoom https://zoom.us/j/1\\_2\\_3?pwd=a\\*b here", base=BASE)
        self.assertEqual([href for href, _words in links(html)], ["https://zoom.us/j/1_2_3?pwd=a*b"])
        self.assertIn("https://zoom.us/j/1_2_3?pwd=a*b", text)
        self.assertNotIn("\\", html)

    def test_link_targets_sees_the_whole_address(self):
        self.assertEqual(
            emailbody.link_targets("See https://example.org/path/_private_/doc", base=BASE),
            frozenset({"https://example.org/path/_private_/doc"}),
        )

    def test_bold_and_italics_round_an_address_still_work(self):
        _text, html = parts("Read **https://x.example/a_b** and _https://x.example/c_ today.")
        self.assertEqual([href for href, _words in links(html)], ["https://x.example/a_b", "https://x.example/c"])
        self.assertIn("<strong><a", html)
        self.assertIn("<em><a", html)

    def test_an_address_in_angle_brackets_loses_the_brackets(self):
        text, html = parts("See <https://example.org/plain> and <www.example.net/x>, or <b>this</b>.")
        self.assertEqual(links(html), [
            ("https://example.org/plain", "example.org"), ("https://www.example.net/x", "example.net"),
        ])
        shown = html_lib.unescape(re.sub(r"<[^>]+>", "", html))
        self.assertIn("See example.org and example.net, or <b>this</b>.", shown)  # other typed HTML is still text
        self.assertIn("See https://example.org/plain and https://www.example.net/x, or", text)

    def test_an_address_among_a_links_words_is_shown_as_words(self):
        page = f"{BASE}/events/lunch-12-nov-2026/"
        for words, shown in (
            ("Lunch see https://evil.test/x for menu", "Lunch see evil.test for menu"),
            ("https://menu.example lunch at Wolfson", "menu.example lunch at Wolfson"),
            ("Book at www.example.org/ball.", "Book at example.org."),
        ):
            with self.subTest(words=words):
                text, html = parts(f"- Thu 12 Nov: [{words}]({page}) (2 places left)", base=BASE)
                self.assertEqual(links(html), [(page, shown)])
                self.assertEqual(re.findall(VISIBLE_ADDRESS, html), [])
                self.assertIn(f"{shown} ({page})", text)

    def test_words_that_are_only_an_address_take_the_words_of_where_the_link_goes(self):
        _text, html = parts("See [www.other.example](https://www.cam.ac.uk/x) now", base=BASE)
        self.assertEqual(links(html), [("https://www.cam.ac.uk/x", "cam.ac.uk")])

    def test_a_long_unbroken_word_can_wrap(self):
        _text, html = parts("Reference " + "A" * 200 + "\n\n- " + "B" * 200)
        self.assertRegex(html, r'<p style="[^"]*overflow-wrap:anywhere;')
        self.assertRegex(html, r'<li style="[^"]*overflow-wrap:anywhere;')
        page = emailbody.document(title="t", site_name="MSS", content=html, footer="<p>Bye</p>")
        for cell in re.findall(r'<td class="em-card[^>]*>', page):
            self.assertIn("overflow-wrap:anywhere;", cell)
        self.assertEqual(len(re.findall(r'<td class="em-card[^>]*>', page)), 2)

    def test_nothing_in_nothing_out(self):
        self.assertEqual(parts(""), ("", ""))
        self.assertEqual(parts("   "), ("", ""))

    def test_only_the_allowed_tags_and_attributes(self):
        seen = []

        class Collect(HTMLParser):
            def handle_starttag(self, tag, attrs):
                seen.append((tag, sorted(name for name, _value in attrs)))

        _text, html = parts(
            "# Title\n\n## Section\n\n### Event\nA line\nAnother line\n[RSVP](https://x.example/a)\n\n"
            "Some **bold**, *italic*, <u>underlined</u>, `code` and a [link](https://x.example/b \"a title\").\n\n"
            "- one\n- two https://x.example/c\n\n1. first\n2. second\n\n> quoted me@example.org\n\n---\n\n"
            "![A picture](https://x.example/p.jpg)\n\n"
            "    indented code\n\n"
            "<div onclick=\"x()\" style=\"color:red\">typed</div> <iframe src=\"https://x.example/\"></iframe>\n",
            base=BASE,
        )
        parser = Collect()
        parser.feed(html)
        parser.close()
        allowed = set("p h2 h3 a ul ol li blockquote hr br strong em u del s table tr td".split())
        self.assertTrue(seen)
        self.assertEqual({tag for tag, _names in seen} - allowed, set())
        anchors = [names for tag, names in seen if tag == "a"]
        self.assertGreaterEqual(len(anchors), 5)
        for names in anchors:
            self.assertEqual(names, ["href", "style"])
        for tag, names in seen:  # nothing typed can bring an attribute of its own
            self.assertNotIn("onclick", names, tag)
            self.assertNotIn("src", names, tag)
            self.assertNotIn("title", names, tag)

    def test_document(self):
        page = emailbody.document(title="<b>x</b>", site_name="MSS", heading="<b>x</b>", content="<p>Hello</p>")
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("<title>&lt;b&gt;x&lt;/b&gt;</title>", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;</h1>", page)
        self.assertIn("<p>Hello</p>", page)
        self.assertNotIn('<base target="_blank">', page)
        self.assertNotIn("em-muted em-rule", page)  # no footer, so no footer cell
        self.assertNotIn("<h1", emailbody.document(title="t", site_name="MSS", content="<p>Hello</p>"))
        page = emailbody.document(
            title="t", site_name="MSS", content="<p>Hello</p>", footer="<p>Goodbye</p>", preview=True,
        )
        self.assertIn('<base target="_blank">', page)
        self.assertIn("em-muted em-rule", page)
        self.assertIn("<p>Goodbye</p>", page)


class PlainHtmlTests(TestCase):
    """The HTML part made for the site's plain-text emails."""

    def test_plain_html(self):
        long_line = "This line was wrapped by hand and it is a good deal more than sixty characters"
        self.assertGreaterEqual(len(long_line), emailbody.WRAPPED)
        text = (
            "Hi Ada,\n\n"
            "Pick up where you left off:\n\n"
            "    https://mss.example/accounts/login/\n\n"
            "You can also look at https://mss.example/events/ at any time.\n\n"
            f"{long_line}\nlong, so it carries on here.\n\n"
            "Short line\nnext line\n\n"
            "— The MSS committee\n"
        )
        html = emailbody.plain_html(text, base="https://mss.example/", title="Finish setting up")
        config = SiteConfig.get()
        self.assertIn(config.society_name, html)  # the header
        self.assertIn("<title>Finish setting up</title>", html)
        self.assertEqual(links(html), [
            ("https://mss.example/accounts/login/", "Open the log-in page"),
            ("https://mss.example/events/", "the events calendar"),
        ])
        self.assertEqual(html.count(BUTTON_CLASS), 1)
        self.assertIn(f"{long_line} long, so it carries on here.</p>", html)  # joined with a space
        self.assertIn("Short line<br>\nnext line</p>", html)  # short lines keep their break
        self.assertEqual(re.findall(VISIBLE_ADDRESS, html), [])

    def test_plain_html_uses_the_site_address_when_none_is_given(self):
        with self.settings(SITE_URL="https://mss.example"):
            html = emailbody.plain_html("See https://mss.example/polls/3/ today.")
        self.assertEqual(links(html), [("https://mss.example/polls/3/", "the poll")])

    def test_plain_html_never_raises(self):
        with mock.patch("core.emailbody.document", side_effect=RuntimeError("no template")), \
                self.assertLogs("core.emailbody", "WARNING") as logs:
            self.assertIsNone(emailbody.plain_html("Hello https://mss.example/ there"))
        self.assertIn("text only", logs.output[0])
