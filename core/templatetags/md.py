"""Safe Markdown rendering for member-written content.

Members write Markdown for guide pages, event descriptions, and the site
About text. That text is untrusted, so rendering happens in three defensive
layers:

1. ``escape`` the source first, so any literal HTML the member typed becomes
   inert text rather than markup.
2. Render with an explicit, conservative extension set. We deliberately do
   NOT use the ``extra`` bundle: it pulls in ``attr_list``, which lets source
   like ``## Heading {: onmouseover=alert(1)}`` attach arbitrary attributes
   (including event handlers) to generated elements — a stored-XSS vector.
3. Run the result through an allow-list sanitizer (below) that keeps only a
   small set of formatting tags and attributes and drops any ``href``/``src``
   whose URL scheme isn't http/https/mailto — closing ``javascript:`` links.

This is dependency-free on purpose: it must run on the SRCF without compiled
extras. The trade-off is that only the formatting in ALLOWED_TAGS survives.
"""

from html.parser import HTMLParser

import markdown as md_lib
from django import template
from django.template.defaultfilters import stringfilter
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

# Tags a member is allowed to produce. Everything else is dropped (its text
# content is kept). No <script>, <style>, <iframe>, event-handler hosts.
ALLOWED_TAGS = {
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "b", "i", "u", "del", "s", "sub", "sup", "mark",
    "code", "pre",
    "ul", "ol", "li",
    "blockquote",
    "a", "img",
    "table", "thead", "tbody", "tr", "th", "td",
}

# Per-tag attribute allow-list. href/src are validated separately for scheme.
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"align"},
    "td": {"align"},
}

SAFE_URL_SCHEMES = ("http:", "https:", "mailto:")


def _href_is_safe(value):
    v = (value or "").strip().lower()
    if v.startswith(("/", "#")):
        return True  # relative / in-page links
    if ":" not in v.split("/")[0]:
        return True  # scheme-less relative like "foo/bar"
    return v.startswith(SAFE_URL_SCHEMES)


class _Sanitizer(HTMLParser):
    """Rebuilds an HTML string keeping only allow-listed tags/attributes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            return
        allowed = ALLOWED_ATTRS.get(tag, set())
        kept = []
        for name, value in attrs:
            if name not in allowed:
                continue
            if name in ("href", "src") and not _href_is_safe(value):
                continue
            kept.append((name, value))
        rendered = "".join(
            f' {n}="{escape(v)}"' if v is not None else f" {n}"
            for n, v in kept
        )
        self.out.append(f"<{tag}{rendered}>")

    def handle_startendtag(self, tag, attrs):
        # Self-closing tags (<img …/>, <br/>) keep their vetted attributes.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in ALLOWED_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(escape(data))

    def result(self):
        return "".join(self.out)


def sanitize_html(html):
    parser = _Sanitizer()
    parser.feed(html)
    parser.close()
    return parser.result()


@register.filter(name="markdown")
@stringfilter
def markdown_filter(text):
    html = md_lib.markdown(
        escape(text),
        extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
        output_format="html",
    )
    return mark_safe(sanitize_html(html))


@register.filter(name="richtext")
@stringfilter
def richtext_filter(text):
    """Markdown plus basic literal HTML (used by the guide and tag pages).

    Unlike ``markdown``, the source is NOT pre-escaped, so hand-written tags
    like <b>, <u> or <img> survive — but only those on the allow-list: the
    sanitizer still rebuilds the output keeping allow-listed tags/attributes
    only, drops event handlers, and enforces http/https/mailto URLs. Anything
    else (scripts, styles, iframes…) is stripped.
    """
    html = md_lib.markdown(
        text,
        extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
        output_format="html",
    )
    return mark_safe(sanitize_html(html))


@register.filter(name="stars")
def stars(value):
    """Render a numeric rating (e.g. 3.7) as ★★★★☆-style text."""
    if value is None:
        return ""
    rounded = round(float(value))
    return "★" * rounded + "☆" * (5 - rounded)


def inline_richtext(text):
    """Rich text for one line: the rendered Markdown without the paragraph
    that wraps a single line, so it can sit inside a heading or a table."""
    html = str(richtext_filter(text)).strip()
    if html.startswith("<p>") and html.endswith("</p>") and html.count("<p>") == 1:
        html = html[3:-4]
    return mark_safe(html)


@register.simple_tag(name="text", takes_context=True)
def text_block(context, key):
    """The current wording of a text block on a fixed page: the admin's
    edit from the Pages panel, or the built-in default (core/blocks.py).
    The edited blocks are fetched once per page render."""
    from core.blocks import BLOCKS, render_value, stored_texts

    texts = context.render_context.get("core.textblocks")
    if texts is None:
        texts = stored_texts()
        context.render_context["core.textblocks"] = texts
    block = BLOCKS[key]
    return render_value(block, texts.get(key, block["default"]))
