"""Pictures for pages: who may add them, and where each one is used."""

from django.apps import apps
from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone


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
    upcoming = apps.get_model("events", "Event").objects.filter(start__gte=timezone.now())
    for title, description, info in upcoming.values_list("title", "description", "attendee_info"):
        if mark in (description or "") or mark in (info or ""):
            yield f"Event: {title}", f"{description}\n{info}"


def picture_uses(pictures):
    """{picture.pk: [where it is used, ...]} for the pictures given."""
    texts = list(_texts())
    uses = {}
    for picture in pictures:
        name = picture.image.name
        found = []
        for label, text in texts:
            if name and name in text and label not in found:
                found.append(label)
        uses[picture.pk] = found
    return uses
