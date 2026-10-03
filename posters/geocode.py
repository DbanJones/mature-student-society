"""Geoapify: one key for geocoding the venue and for the static map.

The key lives on SiteConfig and never reaches the browser; the map image
is fetched by ``views.map_image`` and passed through. Nothing is written to
disk. All network calls are best effort with short timeouts: a poster must
never fail because a map did.
"""

import json
import urllib.parse
import urllib.request

from django.utils import timezone

GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"
STATICMAP_URL = "https://maps.geoapify.com/v1/staticmap"
# Cambridge city centre, so ambiguous names ("The Eagle") resolve locally.
CAMBRIDGE = (52.2053, 0.1218)
TIMEOUT = 6


def api_key():
    from core.models import SiteConfig

    return (SiteConfig.get().geoapify_api_key or "").strip()


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "MSS portal poster"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def geocode(address):
    """(lat, lon) for an address near Cambridge, or None."""
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
        data = json.loads(_fetch(f"{GEOCODE_URL}?{query}"))
        result = (data.get("results") or [None])[0]
        if result and "lat" in result and "lon" in result:
            return float(result["lat"]), float(result["lon"])
    except Exception:
        return None
    return None


def ensure_geocoded(event):
    """Geocode the event's location once; cache the result on the event.
    Returns True when coordinates are available."""
    if event.latitude is not None and event.longitude is not None:
        return True
    if not event.location or event.geocoded_at is not None:
        return False  # already tried, or nothing to look up
    point = geocode(event.location)
    event.geocoded_at = timezone.now()
    if point:
        event.latitude, event.longitude = point
    event.save(update_fields=["latitude", "longitude", "geocoded_at"])
    return point is not None


def static_map_url(lat, lon, width=600, height=400, zoom=15.5):
    key = api_key()
    if not key:
        return ""
    query = urllib.parse.urlencode({
        "style": "positron",
        "width": width, "height": height, "zoom": zoom,
        "center": f"lonlat:{lon},{lat}",
        "marker": f"lonlat:{lon},{lat};type:awesome;color:#b82818;size:large",
        "scaleFactor": 2,
        "apiKey": key,
    })
    return f"{STATICMAP_URL}?{query}"


def fetch_static_map(lat, lon):
    """PNG bytes of the map, or None."""
    url = static_map_url(lat, lon)
    if not url:
        return None
    try:
        return _fetch(url)
    except Exception:
        return None
