"""Geoapify: one key for geocoding the venue and for the static map.

The key lives on SiteConfig and never reaches the browser; the map image
is fetched by ``views.map_image`` and passed through. Nothing is written to
disk. All network calls are best effort: a poster must never fail because a
map did.

Geoapify is not quick. Its geocoder routinely takes three to six seconds to
answer, and the first render of a given map up to ten (later requests come
from its cache). So the timeouts are generous, an event save only waits a
moment, and a lookup that fails is tried again later rather than written
off: ``geocoded_at`` records a definite answer, not an attempt.
"""

import datetime
import json
import urllib.parse
import urllib.request

from django.core.cache import cache
from django.utils import timezone

GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"
STATICMAP_URL = "https://maps.geoapify.com/v1/staticmap"
# Cambridge city centre, so ambiguous names ("The Eagle") resolve locally.
CAMBRIDGE = (52.2053, 0.1218)
GEOCODE_TIMEOUT = 12   # the poster studio waits this long for the venue
SAVE_TIMEOUT = 4       # saving an event only waits this long
MAP_TIMEOUT = 20      # the first render of a map has taken 13 s
RETRY_AFTER = 600      # seconds before a failed lookup is tried again
NO_MATCH_RETRY = datetime.timedelta(days=7)
NOT_FOUND = "not found"
JPEG_MAGIC = bytes((0xFF, 0xD8, 0xFF))


def api_key():
    from core.models import SiteConfig

    return (SiteConfig.get().geoapify_api_key or "").strip()


def _fetch(url, timeout=GEOCODE_TIMEOUT):
    request = urllib.request.Request(url, headers={"User-Agent": "MSS portal poster"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def geocode(address, timeout=GEOCODE_TIMEOUT):
    """(lat, lon) for an address near Cambridge; NOT_FOUND when Geoapify
    knows no such place; None when the lookup itself failed (timeout, outage,
    bad key) and is worth repeating."""
    key = api_key()
    if not key or not address.strip():
        return None
    query = urllib.parse.urlencode({
        "text": f"{address}, Cambridge, UK",
        "bias": f"proximity:{CAMBRIDGE[1]},{CAMBRIDGE[0]}",
        "filter": f"circle:{CAMBRIDGE[1]},{CAMBRIDGE[0]},40000",
        "limit": 1, "format": "json", "apiKey": key,
    })
    try:
        data = json.loads(_fetch(f"{GEOCODE_URL}?{query}", timeout))
    except Exception:
        return None
    result = (data.get("results") or [None])[0]
    if result and "lat" in result and "lon" in result:
        return float(result["lat"]), float(result["lon"])
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
    waiting = f"poster-geocode-wait:{event.pk}"
    if cache.get(waiting):
        return False  # a recent attempt failed; don't stall every page on it
    point = geocode(event.location, timeout)
    if point is None:
        cache.set(waiting, True, RETRY_AFTER)
        return False
    event.geocoded_at = timezone.now()
    if point != NOT_FOUND:
        event.latitude, event.longitude = point
    event.save(update_fields=["latitude", "longitude", "geocoded_at"])
    return point != NOT_FOUND


def static_map_url(lat, lon, width=600, height=400, zoom=15.5):
    key = api_key()
    if not key:
        return ""
    query = urllib.parse.urlencode({
        "style": "positron", "format": "png",
        "width": width, "height": height, "zoom": zoom,
        "center": f"lonlat:{lon},{lat}",
        "marker": f"lonlat:{lon},{lat};type:awesome;color:#b82818;size:large",
        "scaleFactor": 2,
        "apiKey": key,
    })
    return f"{STATICMAP_URL}?{query}"


def fetch_static_map(lat, lon):
    """Image bytes of the map, or None."""
    url = static_map_url(lat, lon)
    if not url:
        return None
    try:
        return _fetch(url, MAP_TIMEOUT)
    except Exception:
        return None


def image_type(data):
    """Geoapify is asked for PNG, but label whatever actually came back."""
    return "image/jpeg" if data[:3] == JPEG_MAGIC else "image/png"
