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
# content is kept). No <script>, <style>, <iframe>, <img>, event-handler hosts.
ALLOWED_TAGS = {
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "b", "i", "del", "code", "pre",
    "ul", "ol", "li",
    "blockquote",
    "a",
    "table", "thead", "tbody", "tr", "th", "td",
}

# Per-tag attribute allow-list. href is validated separately for scheme.
ALLOWED_ATTRS = {
    "a": {"href", "title"},
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
            if name == "href" and not _href_is_safe(value):
                continue
            kept.append((name, value))
        rendered = "".join(
            f' {n}="{escape(v)}"' if v is not None else f" {n}"
            for n, v in kept
        )
        self.out.append(f"<{tag}{rendered}>")

    def handle_startendtag(self, tag, attrs):
        if tag in ALLOWED_TAGS:
            self.out.append(f"<{tag}>")

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


@register.filter(name="stars")
def stars(value):
    """Render a numeric rating (e.g. 3.7) as ★★★★☆-style text."""
    if value is None:
        return ""
    rounded = round(float(value))
    return "★" * rounded + "☆" * (5 - rounded)
