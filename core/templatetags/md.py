import markdown as md_lib
from django import template
from django.template.defaultfilters import stringfilter
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(name="markdown")
@stringfilter
def markdown_filter(text):
    """Render member-written Markdown.

    Raw HTML is escaped BEFORE rendering so members cannot inject scripts or
    markup into pages other members read (guide pages, event descriptions).
    """
    html = md_lib.markdown(
        escape(text),
        extensions=["extra", "sane_lists", "nl2br"],
    )
    return mark_safe(html)


@register.filter(name="stars")
def stars(value):
    """Render a numeric rating (e.g. 3.7) as ★★★★☆-style text."""
    if value is None:
        return ""
    rounded = round(float(value))
    return "★" * rounded + "☆" * (5 - rounded)
