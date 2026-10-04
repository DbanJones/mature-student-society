"""The poster studio, the printable poster page, the SVG fragment, the map
proxy and the QR short link."""

import base64
import html
import mimetypes
import re
from urllib.parse import parse_qs, quote, urlsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import urlencode

from events.models import Event

from . import geocode, layout
from .forms import PosterSettingsForm
from .models import EventPoster, PosterScan
from .qr import qr_svg

# CSS page size for printing: (plain, with 3 mm bleed, width, height)
PAGE_SIZES = {
    "a4": ("A4", "216mm 303mm", "210mm", "297mm"),
    "a3": ("A3", "303mm 426mm", "297mm", "420mm"),
}


def _event(request, slug):
    event = get_object_or_404(
        Event.objects.select_related("category", "created_by", "host"), slug=slug
    )
    if (
        event.created_by.is_shadow_banned
        and request.user != event.created_by
        and not request.user.is_portal_admin
    ):
        raise Http404("No event found.")
    return event


BOOLS = {"use_event_picture", "show_map", "show_qr", "show_description", "show_host", "print_marks"}
TRUTHY = {"on", "1", "true", "yes"}


def _settings(event, data):
    """The saved settings with any overrides from ``data`` (a QueryDict):
    only keys that are present override, so a link with just ``size=story``
    keeps everything else the organiser chose. What a member tweaks for
    their own print never touches the saved row."""
    saved = EventPoster.for_event(event)
    if not data:
        return saved, PosterSettingsForm(instance=saved)
    merged = {}
    for field in EventPoster.OVERRIDABLE:
        value = getattr(saved, field)
        merged[field] = "on" if value is True else ("" if value is False else value)
    for field in EventPoster.OVERRIDABLE:
        if field in data:
            raw = data.get(field)  # the last value wins: hidden "" then checkbox "on"
            merged[field] = ("on" if str(raw).lower() in TRUTHY else "") if field in BOOLS else raw
    form = PosterSettingsForm(merged, instance=saved)
    if form.is_valid():
        settings = form.save(commit=False)
        settings.event = event
        return settings, form
    return saved, PosterSettingsForm(instance=saved)


def _device_photo(request):
    """The studio says it has a photo from this device to drop in."""
    return request.GET.get("device_photo") == "1"


def _map_href(request, event, settings):
    if not settings.show_map or not geocode.api_key():
        return ""
    if geocode.ensure_geocoded(event):
        return reverse("posters:map", args=[event.slug])
    return ""


def map_note(event, settings, user, scene=None):
    """Why the poster has no map, in a sentence, or "" when it has one (or
    the map is switched off)."""
    if not settings.show_map or (scene is not None and scene.has_map):
        return ""
    if not geocode.api_key():
        if getattr(user, "is_super_admin", False):
            return "No map yet: add a Geoapify key on the Super admin tab to put maps on posters."
        return "No map: maps aren't switched on for this site yet."
    if not event.location:
        return "No map: the event doesn't have a location."
    if event.latitude is not None and event.longitude is not None:
        if scene is not None and settings.size == "square":
            return "No map on a square poster with a photo: there isn't room. A4, A3 and Story have one."
        return ""
    if geocode.lookup_failed_recently(event) or not event.geocoded_at:
        return "No map yet: the map service didn't answer in time. It tries again in a few minutes."
    return ("No map: the location couldn't be found on the map. Name a college, building "
            "or street in the event's location and the map appears.")


def _render(request, event, settings, inline=False):
    """The poster as SVG text, and the scene it was drawn from."""
    scene = layout.build_scene(
        event, settings, request, _map_href(request, event, settings), _device_photo(request)
    )
    for el in scene.items:
        if el["t"] == "qr":
            el["svg"] = qr_svg(el["url"], el["size"], el["x"], el["y"])
    svg = render_to_string("posters/_scene.svg", {
        "scene": scene, "event": event, "grain_r": round(scene.u * 0.08, 3),
    })
    return (_inline_images(svg, event) if inline else svg), scene


def _svg(request, event, settings, inline=False):
    return _render(request, event, settings, inline)[0]


def _map_size(data):
    """The map size asked for, kept to sensible steps and bounds."""
    def number(name, default, low):
        try:
            value = int(data.get(name, default))
        except (TypeError, ValueError):
            value = default
        value = min(layout.MAP_MAX_PX, max(low, value))
        return round(value / 10) * 10
    return number("w", 600, 200), number("h", 300, 100)


def _map_bytes(event, width, height):
    """The venue map at this size, from the memory cache or Geoapify."""
    if event.latitude is None or event.longitude is None:
        return None
    key = (f"poster-map:{event.pk}:{event.latitude:.5f}:{event.longitude:.5f}:"
           f"{width}x{height}:{geocode.MAP_STYLE}")
    data = cache.get(key)
    if data is None:
        data = geocode.fetch_static_map(event.latitude, event.longitude, width, height)
        if data is not None:
            cache.set(key, data, 3600)
    return data


def _image_bytes(url, event):
    """(bytes, mime) for an image the poster links to, or (None, None)."""
    parts = urlsplit(url)
    path = parts.path
    if path.startswith("/static/"):
        found = finders.find(path[len("/static/"):])
        if not found:
            return None, None
        with open(found, "rb") as handle:
            return handle.read(), mimetypes.guess_type(found)[0] or "application/octet-stream"
    if path == reverse("posters:map", args=[event.slug]):
        data = _map_bytes(event, *_map_size({k: v[0] for k, v in parse_qs(parts.query).items()}))
        return (data, geocode.image_type(data)) if data else (None, None)
    if event.image and path == event.image.url:
        try:
            with event.image.open("rb") as handle:
                data = handle.read()
        except OSError:
            return None, None
        return data, mimetypes.guess_type(event.image.name)[0] or "image/jpeg"
    return None, None


def _inline_images(svg, event):
    """Swap every image link for the image itself, so a downloaded SVG opens
    in design software with its logo, map and photo in place."""
    def swap(match):
        data, mime = _image_bytes(html.unescape(match.group(1)), event)
        if data is None:
            return match.group(0)
        return 'href="data:%s;base64,%s"' % (mime, base64.b64encode(data).decode("ascii"))
    return re.sub(r'href="(/[^"]*)"', swap, svg)


def _query(settings):
    """The current choices as a query string, for the print and SVG links."""
    data = {}
    for field in EventPoster.OVERRIDABLE:
        value = getattr(settings, field)
        if isinstance(value, bool):
            if value:
                data[field] = "on"
        else:
            data[field] = value
    return urlencode(data)


@login_required
def studio(request, slug):
    event = _event(request, slug)
    can_save = event.can_edit(request.user)
    if request.method == "POST":
        if not can_save:
            messages.error(request, "Only the organiser or an admin can save the poster settings.")
            return redirect("posters:studio", slug=slug)
        form = PosterSettingsForm(request.POST, instance=EventPoster.for_event(event))
        if form.is_valid():
            settings = form.save(commit=False)
            settings.event = event
            settings.saved_by = request.user
            settings.save()
            messages.success(request, "Poster settings saved for everyone.")
            return redirect("posters:studio", slug=slug)
        messages.error(request, "Couldn't save the poster settings — check the form.")
        settings = EventPoster.for_event(event)
    else:
        settings, form = _settings(event, request.GET)
    query = _query(settings)
    svg, scene = _render(request, event, settings)
    return render(request, "posters/studio.html", {
        "nav_active": "calendar",
        "event": event,
        "form": form,
        "settings": settings,
        "can_save": can_save,
        "svg": svg,
        "map_note": map_note(event, settings, request.user, scene),
        "print_url": reverse("posters:print", args=[slug]) + "?" + query,
        "svg_url": reverse("posters:svg", args=[slug]) + "?" + query,
        "scan_count": event.poster_scans.count() if can_save else None,
        "saved": EventPoster.objects.filter(event=event).select_related("saved_by").first(),
    })


@login_required
def svg(request, slug):
    event = _event(request, slug)
    settings, _form = _settings(event, request.GET)
    download = bool(request.GET.get("download"))
    svg_text, scene = _render(request, event, settings, inline=download)
    response = HttpResponse(svg_text, content_type="image/svg+xml; charset=utf-8")
    # The studio shows this under the preview, so a missing map explains itself.
    response["X-Map-Note"] = quote(map_note(event, settings, request.user, scene))
    if download:
        response["Content-Disposition"] = f'attachment; filename="{event.slug}-poster.svg"'
    return response


@login_required
def print_page(request, slug):
    """The poster on its own, sized for the printer: the browser's print
    dialog turns it into a vector PDF. Works without JavaScript; with it, a
    photo chosen in the studio is dropped in and printing waits for every
    picture, the map included."""
    event = _event(request, slug)
    settings, _form = _settings(event, request.GET)
    size = settings.size if settings.size in PAGE_SIZES else "a4"
    name, bleed_size, width, height = PAGE_SIZES[size]
    if settings.print_marks:
        page_size = bleed_size
        width, height = bleed_size.split()
    else:
        page_size = name
    return render(request, "posters/print.html", {
        "event": event,
        "settings": settings,
        "svg": _svg(request, event, settings),
        "page_size": page_size, "page_w": width, "page_h": height,
        "is_print_size": settings.size in PAGE_SIZES,
        "auto_print": request.GET.get("print") == "1",
        "device_photo": _device_photo(request),
    })


@login_required
def map_image(request, slug):
    """The Geoapify static map for the venue, passed through so the key stays
    on the server. Cached in memory for an hour; never written to disk."""
    event = _event(request, slug)
    if event.latitude is None or event.longitude is None:
        raise Http404("No map for this event.")
    data = _map_bytes(event, *_map_size(request.GET))
    if data is None:
        raise Http404("Map unavailable.")
    response = HttpResponse(data, content_type=geocode.image_type(data))
    response["Cache-Control"] = "private, max-age=3600"
    return response


def scan(request, slug):
    """The short link printed in the QR code: count the scan, go to the event."""
    event = get_object_or_404(Event, slug=slug)
    PosterScan.objects.create(
        event=event, device=PosterScan.device_from(request.META.get("HTTP_USER_AGENT", ""))
    )
    return redirect(event)
