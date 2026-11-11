"""Pictures for pages: who may add them, and where each one is used."""

import datetime
from urllib.parse import unquote

from django.apps import apps
from django.conf import settings
from django.db.models import Q
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.encoding import filepath_to_uri


def can_upload_pictures(user):
    """Admins, people named as an editor of a custom page, and group owners.

    Ordinary members may not: files under media/public/ are readable by the
    whole internet, disk space is tight, and nobody reviews them.
    """
    if not user.is_authenticated:
        return False
    return (user.is_portal_admin or user.site_pages_editable.exists()
            or user.tags_owned.exists())


def enable_uploads(form, field_name, user):
    """Switch on "Add a picture" for one editor box, if this person may upload."""
    if not can_upload_pictures(user):
        return
    try:
        attrs = {"data-upload": reverse("core:picture_upload"),
                 "data-upload-max": str(settings.MAX_UPLOAD_SIZE_MB)}
        if user.is_portal_admin:
            attrs["data-pictures"] = reverse("panel:pictures")
    except NoReverseMatch:  # cut-down URL lists in some test modules
        return
    form.fields[field_name].widget.attrs.update(attrs)


def _texts():
    """(where it is, the text) for every place a page picture can be shown.

    Poll and survey wording, past events and old page revisions are left out
    on purpose: a finished event would otherwise block a deletion for ever.
    Every place counted here is one an admin can edit to take the picture out.
    """
    from .blocks import PAGES_BY_KEY

    mark = "public/pictures/"
    for key, text in apps.get_model("core", "TextBlock").objects.filter(
            text__contains=mark).values_list("key", "text"):
        page = PAGES_BY_KEY.get(key.split(".")[0])
        yield (page["title"] if page else key), text
    for title, text in apps.get_model("core", "SitePage").objects.filter(
            content__contains=mark).values_list("title", "content"):
        yield title, text
    for title, text in apps.get_model("guide", "GuidePage").objects.filter(
            content__contains=mark).values_list("title", "content"):
        yield f"Guide: {title}", text
    for name, text in apps.get_model("events", "Category").objects.filter(
            page_content__contains=mark).values_list("name", "page_content"):
        yield f"Group: {name}", text
    for text in apps.get_model("core", "TermsVersion").objects.filter(
            content__contains=mark).values_list("content", flat=True):
        yield "Terms", text
    for text in apps.get_model("core", "SiteConfig").objects.filter(
            banner_text__contains=mark).values_list("banner_text", flat=True):
        yield "Site banner", text
    # Not over yet: an event that has started still shows its page to guests.
    # One with no end time counts for a day after it starts.
    now = timezone.now()
    current = apps.get_model("events", "Event").objects.filter(
        Q(end__gte=now) | Q(end__isnull=True, start__gte=now - datetime.timedelta(days=1)))
    for title, description, info in current.values_list("title", "description", "attendee_info"):
        if mark in (description or "") or mark in (info or ""):
            yield f"Event: {title}", f"{description}\n{info}"


def picture_uses(pictures):
    """{picture.pk: [where it is used, ...]} for the pictures given."""
    # A page holds the picture's web address, where a space or an accent in
    # the file's name is written as %20 or %C3%A9; someone may equally have
    # typed the name as it reads. Both spellings of both sides are compared.
    texts = [(label, text, unquote(text)) for label, text in _texts()]
    uses = {}
    for picture in pictures:
        name = picture.image.name
        spellings = {name, filepath_to_uri(name)} if name else set()
        found = []
        for label, text, decoded in texts:
            if label not in found and any(s in text or s in decoded for s in spellings):
                found.append(label)
        uses[picture.pk] = found
    return uses
