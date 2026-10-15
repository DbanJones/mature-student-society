"""Geoapify: one key for geocoding the venue and for the static map.

The key lives on SiteConfig and never reaches the browser; the map image
is fetched by ``views.map_image`` and passed through. Nothing is written to
disk. All network calls are best effort: a poster must never fail because a
map did.

Geoapify is not quick. Its geocoder routinely takes three to ten seconds to
answer, and the first render of a given map up to twenty (later requests
come from its cache). So the timeouts are generous, an event save only
waits a moment, and a lookup that fails is tried again later rather than
written off: ``geocoded_at`` records a definite answer, not an attempt.

Venues are often written "room, building, street". The full text is looked
up together with its broader parts ("Queens' College, Silver Street",
"Queens' College") at the same time, and a named place beats a street. A
match for the whole city is no use for a pin, so it counts as not found.
"""

import datetime
import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from django.core.cache import cache
from django.utils import timezone

GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"
STATICMAP_URL = "https://maps.geoapify.com/v1/staticmap"
# Cambridge city centre, so ambiguous names ("The Eagle") resolve locally.
CAMBRIDGE = (52.2053, 0.1218)
# Warm buildings, green parks and a blue river: it prints with more contrast
# than the grey styles and still leaves the pin the strongest thing on it.
MAP_STYLE = "klokantech-basic"
MAP_ZOOM = 15.5
GEOCODE_TIMEOUT = 12   # the poster studio waits this long for the venue
SAVE_TIMEOUT = 4       # saving an event only waits this long
MAP_TIMEOUT = 20       # the first render of a map has taken 16 s
RETRY_AFTER = 600      # seconds before a failed lookup is tried again
NO_MATCH_RETRY = datetime.timedelta(days=7)
NOT_FOUND = "not found"
JPEG_MAGIC = bytes((0xFF, 0xD8, 0xFF))
# Result types: a named place or building is what a pin wants; a street
# will do; anything broader would put the pin in the wrong place.
PRECISE = {"amenity", "building"}
TOO_BROAD = {"suburb", "district", "postcode", "city", "county", "state", "country"}


def api_key():
    from core.models import SiteConfig

    return (SiteConfig.get().geoapify_api_key or "").strip()


def _fetch(url, timeout=GEOCODE_TIMEOUT):
    request = urllib.request.Request(url, headers={"User-Agent": "MSS portal poster"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def candidates(address):
    """The address, then its broader parts: "A, B, C" gives "A, B, C",
    "B, C" and "B". The leading part on its own is never tried: "The Old
    Hall" alone could be anywhere."""
    parts = [part.strip() for part in address.split(",") if part.strip()]
    texts = [", ".join(parts)]
    if len(parts) > 1:
        texts.append(", ".join(parts[1:]))
    if len(parts) > 2:
        texts.append(parts[1])
    unique = []
    for text in texts:
        if text and text.lower() not in [seen.lower() for seen in unique]:
            unique.append(text)
    return unique


def _lookup(text, key, timeout):
    """(rank, lat, lon) for one query; NOT_FOUND; or None if the call failed."""
    query = urllib.parse.urlencode({
        "text": f"{text}, Cambridge, UK",
        "bias": f"proximity:{CAMBRIDGE[1]},{CAMBRIDGE[0]}",
        "filter": f"circle:{CAMBRIDGE[1]},{CAMBRIDGE[0]},40000",
        "limit": 1, "format": "json", "apiKey": key,
    })
    try:
        data = json.loads(_fetch(f"{GEOCODE_URL}?{query}", timeout))
    except Exception:
        return None
    result = (data.get("results") or [None])[0]
    if not result or "lat" not in result or "lon" not in result:
        return NOT_FOUND
    kind = result.get("result_type") or "unknown"
    if kind in TOO_BROAD:
        return NOT_FOUND
    return (2 if kind in PRECISE else 1, float(result["lat"]), float(result["lon"]))


def geocode(address, timeout=GEOCODE_TIMEOUT):
    """(lat, lon) for an address near Cambridge; NOT_FOUND when Geoapify
    knows no such place; None when a lookup itself failed (timeout, outage,
    bad key) and is worth repeating."""
    key = api_key()
    if not key or not address.strip():
        return None
    texts = candidates(address)
    if not texts:  # only commas and spaces
        return NOT_FOUND
    with ThreadPoolExecutor(max_workers=len(texts)) as pool:
        results = list(pool.map(lambda text: _lookup(text, key, timeout), texts))
    hits = [result for result in results if isinstance(result, tuple)]
    for hit in hits:  # a named place, the most specific text first
        if hit[0] == 2:
            return hit[1], hit[2]
    if any(result is None for result in results):
        return None  # a lookup that failed might still name the building: try later
    for hit in hits:
        return hit[1], hit[2]  # a street will do
    return NOT_FOUND


def ensure_geocoded(event, timeout=GEOCODE_TIMEOUT):
    """Geocode the event's location and remember the answer on the event.
    Returns True when coordinates are available."""
    if event.latitude is not None and event.longitude is not None:
        return True
    if not event.location:
        return False
    if event.geocoded_at and event.geocoded_at > timezone.now() - NO_MATCH_RETRY:
        return False  # Geoapify had no match for this address recently
    if lookup_failed_recently(event):
        return False  # don't stall every page on a service that isn't answering
    point = geocode(event.location, timeout)
    if point is None:
        cache.set(_waiting_key(event), True, RETRY_AFTER)
        return False
    event.geocoded_at = timezone.now()
    if point != NOT_FOUND:
        event.latitude, event.longitude = point
    event.save(update_fields=["latitude", "longitude", "geocoded_at"])
    return point != NOT_FOUND


def _waiting_key(event):
    return f"poster-geocode-wait:{event.pk}"


def lookup_failed_recently(event):
    return bool(cache.get(_waiting_key(event)))


def static_map_url(lat, lon, width=600, height=300, zoom=MAP_ZOOM):
    key = api_key()
    if not key:
        return ""
    query = urllib.parse.urlencode({
        "style": MAP_STYLE, "format": "png",
        "width": width, "height": height, "zoom": zoom,
        "center": f"lonlat:{lon},{lat}",  # no marker: the poster draws its own pin here
        "scaleFactor": 2,
        "apiKey": key,
    })
    return f"{STATICMAP_URL}?{query}"


def fetch_static_map(lat, lon, width=600, height=300):
    """Image bytes of the map, or None."""
    url = static_map_url(lat, lon, width, height)
    if not url:
        return None
    try:
        return _fetch(url, MAP_TIMEOUT)
    except Exception:
        return None


def image_type(data):
    """Geoapify is asked for PNG, but label whatever actually came back."""
    return "image/jpeg" if data[:3] == JPEG_MAGIC else "image/png"
