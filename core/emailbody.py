"""Email bodies: a text part and an HTML part from one source.

Markdown (the What's On mailer) or plain text (every other email) goes in;
a readable text part and an HTML part come out. The HTML part never shows a
web address as its visible text: Outlook's Safe Links rewrites only the
hidden address of a link, so links written as words stay readable.

No pictures and no remote files, and every style is written inline, because
that is all Outlook and Gmail keep. Dependency-free on purpose: only
Python-Markdown and the project's own sanitiser (core/templatetags/md.py).
"""

import html as html_lib
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

import markdown as md_lib
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import escape
from django.utils.safestring import mark_safe

from core.templatetags.md import sanitize_html

logger = logging.getLogger(__name__)

# The light-mode design tokens of static/css/base.css, written out: email
# cannot use CSS variables. Dark mode is in templates/email/shell.html.
FONT_SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
FONT_SERIF = "'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif"
# One long unbroken word (a reference, a pasted code) wraps, so the email is never wider than a phone.
WRAP = "overflow-wrap:anywhere;word-break:break-word;"
STYLES = {
    "p": f"margin:0 0 14px;{WRAP}",
    "h2": f"margin:26px 0 10px;padding-top:16px;border-top:1px solid #e2e8e2;font-family:{FONT_SERIF};"
          "font-size:21px;line-height:27px;font-weight:bold;color:#14352a;",
    "h3": f"margin:20px 0 4px;font-family:{FONT_SERIF};font-size:18px;line-height:24px;"
          "font-weight:bold;color:#14352a;",
    "a": "color:#2e6b52;text-decoration:underline;",
    "ul": "margin:0 0 14px;padding:0 0 0 22px;",
    "ol": "margin:0 0 14px;padding:0 0 0 22px;",
    "li": f"margin:0 0 6px;{WRAP}",
    "blockquote": "margin:0 0 14px;padding:2px 0 2px 14px;border-left:3px solid #cfe0d4;color:#5f6d64;",
    "hr": "border:0;border-top:1px solid #e2e8e2;height:0;margin:22px 0;",
}
SMALL_STYLES = {**STYLES, "p": f"margin:0 0 6px;{WRAP}","a": "color:#5f6d64;text-decoration:underline;"}
BUTTON = (
    '<table role="presentation" class="em-btn" cellpadding="0" cellspacing="0" border="0" '
    'style="margin:4px 0 18px;"><tr>\n'
    '<td bgcolor="#276349" style="background:#276349;border-radius:6px;padding:9px 18px;">'
    f'<a href="%(href)s" style="color:#ffffff;font-family:{FONT_SANS};font-size:15px;line-height:20px;'
    'font-weight:bold;text-decoration:none;">%(words)s</a></td>\n</tr></table>\n'
)

RENAME = {"b": "strong", "i": "em", "h1": "h2", "h4": "h3", "h5": "h3", "h6": "h3", "pre": "p"}
INLINE = {"strong", "em", "u", "del", "s"}
BLOCKS = {"p", "h2", "h3", "ul", "ol", "li", "blockquote"}
BARE_RE = re.compile(
    r"""(?P<url>(?<![\w@])(?:https?://|www\.)[^\s<>"']+)"""
    r"""|(?P<mail>(?<![\w.+/:-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+)""",
    re.I,
)
ADDRESS_RE = re.compile(r"(?:https?://|www\.)", re.I)
ANGLED_RE = re.compile(r"""<((?:https?://|www\.)[^\s<>"']+)>""", re.I)  # <https://...>, Markdown's own bare link
# The words for a bare address of one of the site's own pages; first match wins.
SITE_WORDS = (
    (r"/$", "the {site} website"),
    (r"/events/$", "the events calendar"),
    (r"/events/groups/", "the group page"),
    (r"/events/[^/]+/$", "the event page"),
    (r"/polls/\d+/", "the poll"),
    (r"/surveys/[^/]+/", "the survey"),
    (r"/formals/", "Formal Exchange"),
    (r"/winter-ball/", "the Winter Ball page"),
    (r"/accounts/profile/", "your profile"),
    (r"/accounts/set-password/", "the page to set your password"),
    (r"/accounts/login/", "the log-in page"),
    (r"/unsubscribe/", "the unsubscribe page"),
    (r"/resubscribe/", "the page that puts it back"),
    (r"/messages/", "your messages"),
)
WRAPPED = 60  # a plain-text line this long was wrapped by hand: the next line carries on from it


def md_escape(text):
    """Member-written words made safe to drop into a line of Markdown: no
    link, heading, bold or quote can be made from them. Always put the
    result after other characters on its line (after "### ", inside "**")."""
    return re.sub(r"([\\`*_\[\]#>])", r"\\\1", " ".join(str(text).split()))


def _shield(found):
    """One typed web address with its ``_`` and ``*`` written as character
    references, so Markdown cannot read them as italics or bold. A backslash
    already put before one (``md_escape``) goes, or it would show. Stars and
    underscores at the very end are left: they close bold or italics put
    round the address."""
    url = found.group("url")
    if not url:
        return found.group(0)
    core = url.rstrip("*_")
    return re.sub(r"\\?_", "&#95;", re.sub(r"\\?\*", "&#42;", core)) + url[len(core):]


def render_markdown(text):
    """Markdown to a sanitised HTML fragment, not yet styled. Typed HTML is
    shown as text: ``&`` and ``<`` are made inert first (``>`` is kept, so
    quotes work), and the site's allow-list sanitiser runs afterwards.

    Two things are done for typed web addresses first. ``<https://...>``
    loses its angle brackets, which would otherwise be left as text either
    side of the link. And the ``_`` and ``*`` inside an address are hidden
    from Markdown as character references (decoded again when the result is
    read), so an address such as ``.../_layouts/15/Minutes_.docx`` is not
    cut in two by being read as italics."""
    source = ANGLED_RE.sub(lambda found: found.group(1), str(text))
    source = source.replace("&", "&amp;").replace("<", "&lt;")
    source = BARE_RE.sub(_shield, source)
    return sanitize_html(md_lib.markdown(source, extensions=["sane_lists", "nl2br"], output_format="html"))


def link_words(url, *, base="", site_name=""):
    """The words to show for an address nobody gave words to."""
    if url.lower().startswith("mailto:"):
        return url[7:].split("?")[0]
    parts = urlsplit(url)
    if base and (url == base or url.startswith((base + "/", base + "?", base + "#"))):
        site = site_name or "society"
        path = parts.path[len(urlsplit(base).path):] or "/"
        for pattern, words in SITE_WORDS:
            if re.match(pattern, path):
                return words.format(site=site)
        return f"this page on the {site} website"
    host = parts.hostname or ""
    return (host[4:] if host.startswith("www.") else host) or "this link"


def render_parts(markdown_text, *, base="", site_name="", small=False):
    """``(text, html)`` for one piece of Markdown: the text part, and the
    styled HTML fragment that goes inside ``document``. ``small`` is for
    footers: quieter links and no buttons."""
    tokens = _prepare(render_markdown(markdown_text), base, site_name, buttons=not small)
    return _emit_text(tokens), _emit_html(tokens, small)


def link_targets(markdown_text, *, base=""):
    """Every web address the rendered email would link to, as a frozenset
    (the check on an AI rewrite). Email addresses are left out."""
    found = set()
    for token in _prepare(render_markdown(markdown_text), base, ""):
        if token[0] == "button" or token[:2] == ("open", "a"):
            href = token[1] if token[0] == "button" else token[2]
            if not href.lower().startswith("mailto:"):
                found.add(href)
    return frozenset(found)


def mail_targets(markdown_text, *, base=""):
    """Every email address the rendered email would link to, in lower case
    (the other half of the check on an AI rewrite: it may reword or drop an
    address, never bring in one the draft did not have)."""
    found = set()
    for token in _prepare(render_markdown(markdown_text), base, ""):
        if token[:2] == ("open", "a") and token[2].lower().startswith("mailto:"):
            found.add(token[2][7:].split("?")[0].strip().lower())
    return frozenset(found)


def document(*, title, site_name, heading="", content, footer="", preview=False):
    """The whole HTML email around an already styled fragment. ``content``
    and ``footer`` must come from ``render_parts`` or ``_emit_html``, which
    escape every piece of text."""
    return render_to_string("email/shell.html", {
        "title": title, "site_name": site_name, "heading": heading,
        "content": mark_safe(content), "footer": mark_safe(footer), "preview": preview,
    })


def plain_html(text, *, base=None, site_name="", society_name="", title=""):
    """The HTML part for a plain-text email: the same words in the society's
    shell, with each web address shown as words (or as a button when it has
    a line to itself). The names come from the site's settings unless given.
    Never raises: on any error it returns None, and the email goes as text
    alone."""
    try:
        base = settings.SITE_URL if base is None else base.rstrip("/")
        if not (site_name and society_name):
            from core.models import SiteConfig  # here, not at the top: core.models must not be needed to import this

            config = SiteConfig.get()
            site_name, society_name = site_name or config.short_name, society_name or config.society_name
        tokens = []
        for chunk in re.split(r"\n[ \t]*\n", str(text).strip()):
            lines = [line.strip() for line in chunk.split("\n") if line.strip()]
            if not lines:
                continue
            tokens.append(("open", "p"))
            for index, line in enumerate(lines):
                if index:
                    before = lines[index - 1]
                    joined = len(before) >= WRAPPED and not (_only_address(before) or _only_address(line))
                    tokens.append(("text", " ") if joined else ("void", "br"))
                tokens.append(("text", line))
            tokens.append(("close", "p"))
        tokens = _buttons(_bare_links(tokens, base, site_name))
        return document(title=title, site_name=society_name or site_name, content=_emit_html(tokens))
    except Exception:
        logger.warning("Could not build the HTML part of an email; it went as text only", exc_info=True)
        return None


# --- the pieces ---------------------------------------------------------------
# A fragment becomes a flat list of tokens:
#   ("open", tag)  ("open", "a", href, auto)  ("close", tag)  ("text", words)
#   ("void", "br" or "hr")  ("button", href, words, auto, tight)
# "auto" marks a link whose words this module chose (a bare address).

class _Tokens(HTMLParser):
    """Sanitised HTML to tokens, keeping only what an email can show."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.code, self.pre, self.links = [], 0, 0, 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("code", "pre"):
            self.code += 1
            self.pre += tag == "pre"
        if tag == "img":  # no pictures in email: a link to the picture instead
            words = (attrs.get("alt") or "").strip() or "View picture"
            if self.links:
                self.out.append(("text", words))
            else:
                self.out += [("open", "a", attrs.get("src") or "", False), ("text", words), ("close", "a")]
            return
        tag = RENAME.get(tag, tag)
        if tag in ("br", "hr"):
            self.out.append(("void", tag))
        elif tag == "a":
            self.links += 1
            self.out.append(("open", "a", attrs.get("href") or "", False))
        elif tag in BLOCKS or tag in INLINE:
            self.out.append(("open", tag))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in ("code", "pre"):
            self.code = max(0, self.code - 1)
            self.pre = max(0, self.pre - (tag == "pre"))
        tag = RENAME.get(tag, tag)
        if tag == "a":
            self.links = max(0, self.links - 1)
            self.out.append(("close", "a"))
        elif tag in BLOCKS or tag in INLINE:
            self.out.append(("close", tag))

    def handle_data(self, data):
        if self.code:  # Markdown escapes code itself, so "&" arrives escaped twice
            data = html_lib.unescape(data)
        if not self.pre:
            self.out.append(("text", re.sub(r"\s+", " ", data)))
            return
        for index, line in enumerate(data.strip("\n").split("\n")):  # indented text keeps its lines
            if index:
                self.out.append(("void", "br"))
            self.out.append(("text", line.strip()))


def _absolute(href, base):
    """A full http, https or mailto address, or None for anything else."""
    href = "".join((href or "").split())
    if any(ord(char) < 32 or ord(char) == 127 for char in href):
        return None
    low = href.lower()
    if low.startswith("mailto:"):
        return href if "@" in href else None
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        if not base:
            return None
        href = base + href
    elif low.startswith("www."):
        href = "https://" + href
    elif not low.startswith(("http://", "https://")):
        return None
    try:
        return href if urlsplit(href).hostname else None
    except ValueError:
        return None


def _links(tokens, base, site_name):
    """Make each link's address absolute; unwrap links that are not web or
    email addresses; give words to a link whose text is itself an address."""
    out, index = [], 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token[:2] != ("open", "a"):
            out.append(token)
            continue
        end = index
        while end < len(tokens) and tokens[end] != ("close", "a"):
            end += 1
        inner = [t for t in tokens[index:end] if t[:2] != ("open", "a")]
        index = end + 1
        href = _absolute(token[2], base)
        if href is None:
            out += inner
            continue
        words = "".join(t[1] for t in inner if t[0] == "text").strip()
        auto = not words or _only_address(words) or words == href
        if auto and not href.lower().startswith("mailto:"):
            inner = [("text", link_words(href, base=base, site_name=site_name))]
        elif not words:
            inner = [("text", link_words(href))]
            auto = False
        else:  # the link's own words stay, but an address among them is shown as words too
            auto = False
            inner = [("text", _worded(t[1], base, site_name)) if t[0] == "text" else t for t in inner]
        out += [("open", "a", href, auto), *inner, ("close", "a")]
    return out


def _worded(text, base, site_name):
    """A link's words with every web address among them shown as words
    ("Lunch, see https://x.example/menu" reads "Lunch, see x.example")."""
    def swap(found):
        typed = _trim(found.group("url") or "")
        href = _absolute(typed, base) if typed else None
        if href is None:
            return found.group(0)
        return link_words(href, base=base, site_name=site_name) + found.group("url")[len(typed):]
    return BARE_RE.sub(swap, text)


def _trim(address):
    """Punctuation that follows a typed address is not part of it."""
    while address:
        last = address[-1]
        if (last in ".,;:!?'\"*" or (last == ")" and address.count(")") > address.count("("))
                or (last == "]" and address.count("]") > address.count("["))):
            address = address[:-1]
        else:
            break
    return address


def _only_address(line):
    found = BARE_RE.fullmatch(line.strip())
    return bool(found and found.group("url"))


def _bare_links(tokens, base, site_name):
    """Typed web and email addresses become links. A web address is shown as
    words; an email address is shown as it is. Headings are left alone."""
    out, inside = [], 0
    for token in tokens:
        if token[0] in ("open", "close") and token[1] in ("a", "h2", "h3"):
            inside += 1 if token[0] == "open" else -1
        if token[0] != "text" or inside > 0:
            out.append(token)
            continue
        text, last = token[1], 0
        for found in BARE_RE.finditer(text):
            if found.group("mail"):
                shown, href, auto = found.group("mail"), "mailto:" + found.group("mail"), False
                stop = found.end()
            else:
                typed = _trim(found.group("url"))
                href = _absolute(typed, base)
                if href is None:
                    continue
                shown, auto, stop = link_words(href, base=base, site_name=site_name), True, found.start() + len(typed)
            if found.start() > last:
                out.append(("text", text[last:found.start()]))
            out += [("open", "a", href, auto), ("text", shown), ("close", "a")]
            last = stop
        if last < len(text):
            out.append(("text", text[last:]))
    return out


def _button(line):
    """``(href, words, auto)`` when a line holds one web link and nothing else."""
    solid = [t for t in line if not (t[0] == "text" and not t[1].strip())
             and not (t[0] in ("open", "close") and t[1] in INLINE)]
    if len(solid) < 3 or solid[0][:2] != ("open", "a") or solid[-1] != ("close", "a"):
        return None
    if any(t[0] != "text" for t in solid[1:-1]) or solid[0][2].lower().startswith("mailto:"):
        return None
    words = "".join(t[1] for t in solid[1:-1]).strip()
    return (solid[0][2], words, solid[0][3]) if words else None


def _buttons(tokens):
    """In a paragraph of its own (not in a list or quote), a line that is one
    link and nothing else becomes a button."""
    out, depth, index = [], 0, 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token[0] in ("open", "close") and token[1] in ("ul", "ol", "li", "blockquote"):
            depth += 1 if token[0] == "open" else -1
        if token != ("open", "p") or depth > 0:
            out.append(token)
            continue
        end = index
        while end < len(tokens) and tokens[end] != ("close", "p"):
            end += 1
        lines = [[]]
        for inner in tokens[index:end]:
            if inner == ("void", "br"):
                lines.append([])
            else:
                lines[-1].append(inner)
        index = end + 1
        held = []  # ordinary lines waiting to be written as one paragraph
        for number, line in enumerate(lines):
            button = _button(line)
            if button:
                out += _paragraph(held)
                held = []
                href, words, auto = button
                out.append(("button", href, words, auto, number > 0))
            elif any(t[0] != "text" or t[1].strip() for t in line) or held:
                held.append(line)
        out += _paragraph(held)
    return out


def _paragraph(lines):
    """Lines of tokens as one paragraph, a line break between them."""
    if not lines:
        return []
    out = [("open", "p")]
    for number, line in enumerate(lines):
        out += ([("void", "br")] if number else []) + line
    return out + [("close", "p")]


def _tidy(tokens):
    """Drop the spaces HTML ignores: at the start of a block or a line."""
    out = []
    for token in tokens:
        before = out[-1] if out else None
        fresh = before is None or before[0] in ("void", "button") or (
            before[0] in ("open", "close") and before[1] in BLOCKS)
        if token[0] == "text" and fresh:
            token = ("text", token[1].lstrip())
        if token[0] == "text" and not token[1]:
            continue
        out.append(token)
    return out


def _prepare(fragment, base, site_name, buttons=True):
    parser = _Tokens()
    parser.feed(fragment)
    parser.close()
    tokens = _bare_links(_links(parser.out, base, site_name), base, site_name)
    return _tidy(_buttons(tokens) if buttons else tokens)


def _emit_html(tokens, small=False):
    """Tokens to HTML: only the tags above, each with its fixed inline
    style, the address of a link as its only attribute, all text escaped."""
    styles, out = (SMALL_STYLES if small else STYLES), []
    for token in tokens:
        kind = token[0]
        if kind == "text":
            out.append(escape(token[1]))
        elif kind == "button":
            words = f"Open {token[2]}" if token[3] else token[2]
            out.append(BUTTON % {"href": escape(token[1]), "words": escape(words)})
        elif kind == "void":
            out.append("<br>\n" if token[1] == "br" else f'<hr class="em-rule" style="{styles["hr"]}">\n')
        elif kind == "close":
            out.append(f"</{token[1]}>" + ("\n" if token[1] in BLOCKS else ""))
        elif token[1] == "a":
            out.append(f'<a href="{escape(token[2])}" style="{styles["a"]}">')
        elif token[1] in styles:
            rule = ' class="em-rule"' if token[1] == "h2" else ""
            out.append(f'<{token[1]}{rule} style="{styles[token[1]]}">')
        else:
            out.append(f"<{token[1]}>")
    return "".join(out)


class _Page:
    """Text being written: the pieces, and the line breaks owed before the next one."""

    def __init__(self):
        self.out, self.owed, self.bullet, self.titled = [], 0, False, False

    def gap(self, lines):
        if not self.bullet:  # straight after a list marker the text stays on its line
            self.owed = max(self.owed, lines)

    def put(self, piece, keep=False):
        if not keep and (self.owed or not self.out):
            piece = piece.lstrip()
        if not piece:
            return
        if self.out:
            self.out.append("\n" * self.owed)
        self.owed, self.bullet, self.titled = 0, False, False
        self.out.append(piece)

    def text(self):
        return "".join(self.out)


def _emit_text(tokens):
    """Tokens to the text part: headings underlined, lists with dashes,
    links as "words (address)", a button as "words: address"."""
    pages, lists, links = [_Page()], [], []
    for token in tokens:
        kind, page = token[0], pages[-1]
        if kind == "text":
            page.put(token[1])
        elif kind == "button":
            href, words, auto, tight = token[1:]
            if tight and page.out:
                page.owed = 1
            else:
                page.gap(2)
            page.put(href if auto else f"{words}: {href}")
            page.gap(2)
        elif kind == "void":
            if token[1] == "br":
                page.owed += 1
            else:
                page.gap(2)
                page.put("----------")
                page.gap(2)
        elif kind == "open":
            tag = token[1]
            if tag == "a":
                links.append(token)
                pages.append(_Page())
            elif tag in ("h2", "blockquote"):
                page.gap(2)
                pages.append(_Page())
            elif tag in ("ul", "ol"):
                page.bullet = False
                page.gap(1 if lists else 2)
                lists.append(1 if tag == "ol" else 0)
            elif tag == "li":
                page.bullet = False
                page.gap(1)
                mark = "- "
                if lists and lists[-1]:
                    mark = f"{lists[-1]}. "
                    lists[-1] += 1
                page.put("  " * max(0, len(lists) - 1) + mark, keep=True)
                page.bullet = True
            elif tag == "p":  # an event's details sit straight under its title
                page.gap(1 if lists or page.titled else 2)
            elif tag == "h3":
                page.gap(2)
        elif kind == "close":
            tag = token[1]
            if tag == "a" and links and len(pages) > 1:
                words, opened = pages.pop().text().strip(), links.pop()
                href = opened[2]
                address = href[7:] if href.lower().startswith("mailto:") else href
                same = opened[3] or words == address or not words
                pages[-1].put(address if same else f"{words} ({address})")
            elif tag == "h2" and len(pages) > 1:
                words = pages.pop().text().strip()
                pages[-1].put(words)
                pages[-1].owed = 1
                pages[-1].put("-" * len(words))
                pages[-1].gap(2)
            elif tag == "blockquote" and len(pages) > 1:
                quoted = pages.pop().text().strip("\n")
                pages[-1].put("\n".join(f"> {line}".rstrip() for line in quoted.split("\n")), keep=True)
                pages[-1].gap(2)
            elif tag in ("ul", "ol"):
                if lists:
                    lists.pop()
                page.bullet = False
                page.gap(1 if lists else 2)
            elif tag == "li":
                page.bullet = False
                page.gap(1)
            elif tag == "p":
                page.gap(1 if lists else 2)
            elif tag == "h3":
                page.gap(1)
                page.titled = True
    while len(pages) > 1:  # never lose text to a tag left open
        tail = pages.pop().text()
        pages[-1].put(tail)
    text = re.sub(r"[ \t]+\n", "\n", pages[0].text())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --- the mailer's "own design" mode: a complete HTML email, sent as written ---

OWN_HTML_RE = re.compile(
    r"\s*(?:<!--.*?-->\s*)*<(?:!doctype\s+html|html|head|body|table|div|center|style)\b", re.I | re.S
)
MAX_OWN_HTML = 300000  # characters; a designed email is 10 to 60 thousand
SCRIPT_RE = re.compile(r"<script\b.*?(?:</script\s*>|\Z)", re.I | re.S)
BODY_END_RE = re.compile(r"</body\s*>", re.I)
OWN_FOOTER = (
    '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr>'
    '<td align="center" style="padding:16px 12px 24px;font-family:Arial,Helvetica,sans-serif;'
    'font-size:12px;line-height:18px;color:#6b7480;">%s</td></tr></table>'
)


def is_own_html(body):
    """True when the mailer's body is a complete HTML email (it starts with
    ``<!DOCTYPE html>``, ``<html>``, ``<table>`` and the like) and not Markdown."""
    return bool(OWN_HTML_RE.match(str(body)))


def body_problem(body):
    """Why the mailer's body must not be built, or None. Markdown has the
    site's usual limits; an admin's own HTML only has a size limit, because
    it is never run through the Markdown renderer."""
    if is_own_html(body):
        if len(body) > MAX_OWN_HTML:
            return f"it is too long ({len(body):,} characters; the limit is {MAX_OWN_HTML:,})"
        return None
    from core.richtext import richtext_problem

    return richtext_problem(body)


class _TextOf(HTMLParser):
    """The words of an HTML email as plain text, for the text part: each
    block on its own line, a link as its words then its address, and
    nothing from the head, styles or hidden (preview-line) elements."""

    SKIP = {"head", "style", "script", "title"}
    BLOCK = {"p", "div", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "blockquote", "hr"}
    VOID = {"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.open, self.hidden_from, self.href = [], [], None, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in self.VOID:
            self.open.append(tag)
            style = (attrs.get("style") or "").replace(" ", "").lower()
            if self.hidden_from is None and (tag in self.SKIP or "display:none" in style):
                self.hidden_from = len(self.open)
        if self.hidden_from is not None:
            return
        if tag == "br":
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in ("td", "th"):
            self.out.append(" ")
        elif tag in self.BLOCK:
            self.out.append("\n\n")
        elif tag == "a":
            self.href = (attrs.get("href") or "").strip()
            self.link_at = len(self.out)

    def handle_endtag(self, tag):
        if tag not in self.open:
            return
        while self.open:
            closed = self.open.pop()
            if self.hidden_from is not None and len(self.open) < self.hidden_from:
                self.hidden_from = None
            if closed == tag:
                break
        if self.hidden_from is not None:
            return
        if tag == "a" and self.href is not None:
            words = " ".join("".join(self.out[self.link_at:]).split())
            address = self.href[7:] if self.href.lower().startswith("mailto:") else self.href
            if address and address not in words and self.href.lower().startswith(("http://", "https://", "mailto:")):
                self.out.append(f" ({address})" if words else address)
            self.href = None
        elif tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_data(self, data):
        if self.hidden_from is None:
            self.out.append(re.sub(r"\s+", " ", data))

    def text(self):
        lines = [" ".join(line.split()) for line in "".join(self.out).replace("͏", "").split("\n")]
        kept = []
        for line in lines:
            if line or (kept and kept[-1]):
                kept.append(line)
        return "\n".join(kept).strip()


def own_html_parts(body, footer_text="", footer_html=""):
    """``(text, html)`` for an admin's own complete HTML email. The HTML
    goes out as written (scripts removed: no mail program runs them), with
    the audience's footer, when there is one, put just before ``</body>``.
    The text part is the same words read out of the HTML."""
    html = SCRIPT_RE.sub("", str(body)).strip()
    reader = _TextOf()
    reader.feed(html)
    reader.close()
    text = reader.text()
    if footer_text:
        text = f"{text}\n\n--\n{footer_text}"
    if footer_html:
        block = OWN_FOOTER % footer_html
        ends = list(BODY_END_RE.finditer(html))
        html = html[:ends[-1].start()] + block + html[ends[-1].start():] if ends else html + block
    return text, html


class _Targets(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.web, self.mail, self.images, self.other = set(), set(), set(), set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            href = attrs["href"].strip()
            if href.lower().startswith("mailto:"):
                self.mail.add(href[7:].split("?")[0].strip().lower())
            elif href.lower().startswith(("http://", "https://")):
                self.web.add(href)
            elif not href.startswith("#"):
                self.other.add(href)
        elif tag == "img" and attrs.get("src"):
            self.images.add(attrs["src"].strip())

    handle_startendtag = handle_starttag


def html_targets(html):
    """What an HTML email points at: ``(web addresses, email addresses,
    pictures, anything else)``, each a frozenset. The check on an AI's
    HTML: "anything else" is a link that is neither a full web address nor
    an email address, which cannot work in an email."""
    reader = _Targets()
    reader.feed(str(html))
    reader.close()
    return frozenset(reader.web), frozenset(reader.mail), frozenset(reader.images), frozenset(reader.other)
