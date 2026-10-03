"""Lays a poster out as a scene: a list of primitives (rectangles, text
runs, images, the QR code, the map) in the poster's own units, for
``templates/posters/_scene.svg`` to draw. Nothing here touches the network
or the disk except reading the event picture's dimensions."""

import re

from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from . import fonts

PAPER = "#fbfaf5"
INK = "#1a2420"
MUTED = "#5b6a60"
PINE = "#14352a"
BRICK = "#b82818"
GOLD = "#a97e1f"
SAGE_SOFT = "#e7eee8"
ACCENTS = {"pine": PINE, "brick": BRICK, "gold": GOLD}

# Canvas sizes: (width, height) in the poster's own units. A3 reuses the A4
# layout and is scaled by the page size when printed.
CANVAS = {"a4": (210, 297), "a3": (210, 297), "square": (1080, 1080), "story": (1080, 1920)}
BLEED_MM = 3


def _hex_to_rgb(value):
    value = value.lstrip("#")
    if len(value) != 6:
        return (20, 53, 42)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(rgb):
    def chan(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def readable_on_white(colour):
    """Darken a colour until white text on it reaches 4.5:1."""
    rgb = _hex_to_rgb(colour)
    for _ in range(12):
        contrast = 1.05 / (_luminance(rgb) + 0.05)
        if contrast >= 4.5:
            break
        rgb = tuple(int(c * 0.85) for c in rgb)
    return "#%02x%02x%02x" % rgb


def accent_for(event, choice):
    if choice in ACCENTS:
        return ACCENTS[choice]
    if event.is_super:
        return GOLD
    return readable_on_white(event.category.color or PINE)


def plain_text(markdown, max_words=60):
    """The first words of a Markdown description as plain text."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", markdown or "")
    text = re.sub(r"[#>*_`~]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words]).rstrip(",;:") + "…"
    return text


def when_text(event, short=False):
    start = timezone.localtime(event.start)
    day = date_format(start, "D j M" if short else "l j F")
    clock = start.strftime("%I:%M %p").lstrip("0").lower().replace(":00", "")
    text = f"{day}, {clock}"
    if event.end and not short:
        end = timezone.localtime(event.end)
        text += " to " + end.strftime("%I:%M %p").lstrip("0").lower().replace(":00", "")
    return text


def cover_box(panel, image_size, focal):
    """Place an image of ``image_size`` so it covers ``panel`` (x, y, w, h)
    with the focal point (fractions) as near the centre as the crop allows.
    Returns (x, y, w, h) for the image."""
    px, py, pw, ph = panel
    iw, ih = image_size
    if not iw or not ih:
        return px, py, pw, ph
    scale = max(pw / iw, ph / ih)
    w, h = iw * scale, ih * scale
    fx, fy = focal
    x = px + pw / 2 - fx * w
    y = py + ph / 2 - fy * h
    x = min(px, max(px + pw - w, x))
    y = min(py, max(py + ph - h, y))
    return x, y, w, h


def event_picture(event, settings):
    """(href, (width, height)) for the event's own picture, if it is wanted
    and readable, else (None, None)."""
    if not settings.use_event_picture or not event.image:
        return None, None
    try:
        from PIL import Image

        with Image.open(event.image.path) as image:
            size = image.size
    except Exception:
        return None, None
    return event.image.url, size


class Scene:
    """Collects primitives. Coordinates are in canvas units; ``u`` is one
    percent of the canvas width, the unit every size is expressed in."""

    def __init__(self, size_key, bleed=False):
        self.w, self.h = CANVAS[size_key]
        self.u = self.w / 100
        self.bleed = BLEED_MM if (bleed and size_key in ("a4", "a3")) else 0
        self.items = []
        self.defs = []

    def paper(self, fill=PAPER):
        """The whole sheet, bleed included."""
        b = self.bleed
        self.rect(-b, -b, self.w + 2 * b, self.h + 2 * b, fill)

    def band(self, y, h, fill, to_bottom=False):
        """A full-width band that reaches into the side bleed (and the
        bottom bleed when it is the footer)."""
        b = self.bleed
        self.rect(-b, y, self.w + 2 * b, h + (b if to_bottom else 0), fill)

    def rect(self, x, y, w, h, fill, rx=0, opacity=None):
        self.items.append({"t": "rect", "x": x, "y": y, "w": w, "h": h, "fill": fill, "rx": rx, "opacity": opacity})

    def text(self, x, y, lines, size, key="body", weight=400, fill=INK, anchor="start",
             lh=1.2, ls=0, upper=False):
        if isinstance(lines, str):
            lines = [lines]
        if upper:
            lines = [line.upper() for line in lines]
        self.items.append({
            "t": "text", "x": x, "y": y + size * 0.78, "lines": lines, "size": size,
            "family": fonts.FAMILIES[key], "weight": weight, "fill": fill, "anchor": anchor,
            "dy": size * lh, "ls": ls * size,
        })
        return y + size * lh * len(lines)

    def paragraph(self, x, y, text, max_width, size, key="body", weight=400, fill=INK,
                  lh=1.3, max_lines=None):
        lines = fonts.wrap(text, max_width, key, size, weight, max_lines=max_lines)
        if not lines:
            return y
        return self.text(x, y, lines, size, key, weight, fill, lh=lh)

    def chip(self, x, y, label, fill, size, align="right", text_fill="#ffffff"):
        pad = size * 0.9
        w = fonts.width(label, "body", size, 700) + pad * 2
        h = size * 1.75
        if align == "right":
            x = x - w
        self.rect(x, y, w, h, fill, rx=h / 2)
        self.text(x + pad, y + (h - size) / 2 - size * 0.08, label, size, "body", 700, text_fill)
        return w

    def logo(self, x, y, h, on_paper=True):
        w = h * 500 / 176  # lion.png is 500 by 176
        if not on_paper:
            pad = h * 0.18
            self.rect(x - pad, y - pad, w + pad * 2, h + pad * 2, PAPER, rx=h * 0.2)
        self.items.append({"t": "image", "href": "/static/img/lion.png", "x": x, "y": y, "w": w, "h": h})
        return w

    def picture_panel(self, x, y, w, h, href, image_size, focal, colour, rx=0, hidden_photo=False):
        """A photo clipped to the panel, or a generated colour scene. The
        image element is always present so a photo chosen in the browser can
        be dropped into it."""
        n = len(self.items)
        self.defs.append({"t": "clip", "id": f"clip{n}", "x": x, "y": y, "w": w, "h": h, "rx": rx})
        self.defs.append({"t": "grad", "id": f"grad{n}", "colour": colour})
        self.rect(x, y, w, h, f"url(#grad{n})", rx=rx)
        self.items.append({"t": "grain", "x": x, "y": y, "w": w, "h": h, "clip": f"clip{n}"})
        if href:
            ix, iy, iw, ih = cover_box((x, y, w, h), image_size, focal)
        else:
            ix, iy, iw, ih = x, y, w, h
        self.items.append({
            "t": "photo", "href": href or "", "x": ix, "y": iy, "w": iw, "h": ih,
            "clip": f"clip{n}", "panel": (x, y, w, h), "hidden": not href,
        })

    def qr(self, x, y, size, url, label):
        self.items.append({"t": "qr", "x": x, "y": y, "size": size, "url": url})
        mark_w = size * 0.3
        mark_h = mark_w * 91 / 218
        self.rect(x + size / 2 - mark_w / 2 - size * 0.025, y + size / 2 - mark_h / 2 - size * 0.025,
                  mark_w + size * 0.05, mark_h + size * 0.05, "#ffffff", rx=size * 0.02)
        self.items.append({"t": "image", "href": "/static/img/lion-mark.png",
                           "x": x + size / 2 - mark_w / 2, "y": y + size / 2 - mark_h / 2, "w": mark_w, "h": mark_h})
        label_size = max(size * 0.072, self.u * 2.2)
        self.text(x + size / 2, y + size + label_size * 0.4, label, label_size, "body", 700, INK,
                  anchor="middle", ls=0.06, upper=True)
        return y + size + label_size * 2

    def map(self, x, y, w, h, href, caption):
        n = len(self.items)
        self.defs.append({"t": "clip", "id": f"clip{n}", "x": x, "y": y, "w": w, "h": h, "rx": self.u * 1.6})
        self.rect(x, y, w, h, SAGE_SOFT, rx=self.u * 1.6)
        self.items.append({"t": "image", "href": href, "x": x, "y": y, "w": w, "h": h, "clip": f"clip{n}", "cover": True})
        cap = self.u * 2.3
        lines = fonts.wrap(caption, w, "body", cap, 400, max_lines=2)
        return self.text(x + w / 2, y + h + cap * 0.5, lines, cap, "body", 400, MUTED, anchor="middle")

    def crop_marks(self):
        """Marks in the bleed area for a print shop."""
        b, w, h = self.bleed, self.w, self.h
        if not b:
            return
        for cx in (0, w):
            for cy in (0, h):
                dx = -1 if cx == 0 else 1
                dy = -1 if cy == 0 else 1
                self.items.append({"t": "line", "x1": cx + dx * b, "y1": cy, "x2": cx + dx * 0.8, "y2": cy})
                self.items.append({"t": "line", "x1": cx, "y1": cy + dy * b, "x2": cx, "y2": cy + dy * 0.8})

    def view_box(self):
        b = self.bleed
        return f"{-b} {-b} {self.w + 2 * b} {self.h + 2 * b}"


class Content:
    """Everything the templates draw, gathered once from the event."""

    def __init__(self, event, settings, request, map_href=""):
        self.event = event
        self.settings = settings
        self.title = (settings.headline or event.title).strip()
        self.when = when_text(event)
        self.when_short = when_text(event, short=True)
        self.where = event.location or ""
        self.extra = settings.extra_line.strip()
        self.description = plain_text(event.description) if settings.show_description else ""
        self.accent = accent_for(event, settings.accent)
        tag = event.category
        self.chip = f"{tag.emoji} {tag.name}".strip()
        if event.is_super:
            self.chip += " · super event"
        elif event.is_official:
            self.chip += " · official"
        host = event.effective_host
        self.facts = []
        if settings.show_host:
            self.facts.append(("Who it's for", "members only" if event.members_only else "members, partners and guests"))
            self.facts.append(("Host", host.get_full_name() or host.username))
            if event.capacity:
                self.facts.append(("Places", f"{event.capacity}, book early"))
        self.host_domain = request.get_host()
        self.scan_url = request.build_absolute_uri(reverse("poster_scan", args=[event.slug]))
        self.map_href = map_href if settings.show_map else ""
        self.directions_url = (
            "https://www.google.com/maps/search/?api=1&query="
            + re.sub(r"\s+", "+", f"{self.where}, Cambridge, UK") if self.where else ""
        )
        self.picture_href, self.picture_size = event_picture(event, settings)
        self.focal = (settings.focal_x, settings.focal_y)
        self.scene_colour = self.accent


def _body_need(s, c, with_map):
    """Height the lower block needs: the QR column, or the map/directions slot."""
    u = s.u
    if c.settings.show_qr:
        return (24 * u + 3.5 * u + 14 * u + 2.5 * u + 1.5 * u) if with_map else (30 * u + 4 * u)
    return (14 * u + 2.5 * u) if with_map else 10 * u


def _body_columns(s, c, x, y, w, bottom, with_map):
    """The two-column lower block shared by Classic and Bold: description
    and facts on the left, QR and map (or a directions code) on the right."""
    u = s.u
    right_w = 36 * u
    gap = 5 * u
    left_w = w - right_w - gap
    rx = x + w - right_w
    avail = bottom - y
    map_h = 14 * u if with_map else 0
    qr_size = 0
    if c.settings.show_qr:
        qr_size = min(24 * u if with_map else 30 * u, avail - 3.5 * u - (map_h + 4 * u if with_map else 0))
        if qr_size < 20 * u and with_map:
            with_map, map_h = False, 0
            qr_size = min(30 * u, avail - 4 * u)
        qr_size = max(0, qr_size)
    ry = y
    if qr_size:
        ry = s.qr(rx + (right_w - qr_size) / 2, ry, qr_size, c.scan_url, "Scan to RSVP") + u
    if with_map and c.map_href:
        s.map(rx, ry, right_w, map_h, c.map_href, c.where)
    elif with_map and c.directions_url:
        s.qr(rx + (right_w - map_h) / 2, ry, map_h, c.directions_url, "Scan for directions")

    ly = y
    if c.description:
        max_lines = max(2, int((bottom - ly - 8 * u) / (3 * u * 1.3)))
        ly = s.paragraph(x, ly, c.description, left_w, 3 * u, "body", 400, INK, lh=1.3, max_lines=max_lines) + 2 * u
    for label, value in c.facts:
        if ly + 3 * u > bottom:
            break
        size = 2.6 * u
        lead = f"{label} · "
        lead_w = fonts.width(lead, "body", size, 700)
        s.text(x, ly, lead, size, "body", 700, INK)
        s.text(x + lead_w, ly, fonts.wrap(value, left_w - lead_w, "body", size, 400, max_lines=1), size, "body", 400, MUTED)
        ly += size * 1.35


def _measure_text_block(s, c, w, title_size):
    """Heights of the title, when, where and extra lines, measured first so
    the picture can give up height before anything else is dropped."""
    u = s.u
    size, lines = fonts.fit(c.title, w, "display", title_size, 700, 3, 6 * u)
    when_lines = fonts.wrap(c.when, w, "body", 4.3 * u, 700, max_lines=2)
    where_lines = fonts.wrap(c.where, w, "body", 3.4 * u, 400, max_lines=2) if c.where else []
    extra_lines = fonts.wrap(c.extra, w, "body", 3.4 * u, 700, max_lines=1) if c.extra else []
    height = (
        size * 1.02 * len(lines) + 2.6 * u + 4.3 * u * 1.15 * len(when_lines) + 0.6 * u
        + 3.4 * u * 1.2 * len(where_lines) + (3.4 * u * 1.2 + 1.2 * u if extra_lines else 0)
    )
    return {"size": size, "lines": lines, "when": when_lines, "where": where_lines, "extra": extra_lines, "height": height}


def _draw_text_block(s, c, x, y, block):
    u = s.u
    y = s.text(x, y, block["lines"], block["size"], "display", 700, INK, lh=1.02) + 2.6 * u
    y = s.text(x, y, block["when"], 4.3 * u, "body", 700, c.accent, lh=1.15) + 0.6 * u
    if block["where"]:
        y = s.text(x, y, block["where"], 3.4 * u, "body", 400, MUTED)
    if block["extra"]:
        y = s.text(x, y + 1.2 * u, block["extra"], 3.4 * u, "body", 700, INK)
    return y


def _footer(s, c, right_text):
    u, m = s.u, 6 * s.u
    footer_h = 9 * u
    s.band(s.h - footer_h, footer_h, PINE, to_bottom=True)
    fy = s.h - footer_h + (footer_h - 2.5 * u) / 2 - 0.3 * u
    s.text(m, fy, c.host_domain, 2.5 * u, "body", 600, "#ffffff", ls=0.03)
    s.text(s.w - m, fy, right_text, 2.5 * u, "body", 400, "#e6ede8", anchor="end")


def classic(s, c):
    u, m = s.u, 6 * s.u
    w = s.w - 2 * m
    footer_h = 9 * u
    s.paper()
    s.logo(m, m, 7.5 * u)
    s.chip(s.w - m, m + 0.8 * u, c.chip, c.accent, 2.6 * u)
    block = _measure_text_block(s, c, w, 9.2 * u)
    want_map = c.settings.show_map and bool(c.map_href or c.directions_url)
    fixed = m + 10 * u + 4.6 * u + block["height"] + 4 * u + footer_h + m
    hero = s.h - fixed - _body_need(s, c, want_map)
    if hero < 20 * u and want_map:
        want_map = False
        hero = s.h - fixed - _body_need(s, c, want_map)
    hero = min(36 * u, max(18 * u, hero))
    y = m + 10 * u
    s.picture_panel(m, y, w, hero, c.picture_href, c.picture_size, c.focal, c.scene_colour, rx=2.4 * u)
    y = _draw_text_block(s, c, m, y + hero + 4.6 * u, block) + 4 * u
    _body_columns(s, c, m, y, w, s.h - footer_h - m, want_map)
    _footer(s, c, "Mature Student Society · nearly 1,000 members")


def bold(s, c):
    u, m = s.u, 6 * s.u
    w = s.w - 2 * m
    footer_h = 9 * u
    s.paper()
    block = _measure_text_block(s, c, w, 8.4 * u)
    want_map = c.settings.show_map and bool(c.map_href or c.directions_url)
    fixed = 5 * u + block["height"] + 3.5 * u + footer_h + m
    block_h = s.h - fixed - _body_need(s, c, want_map)
    if block_h < 36 * u and want_map:
        want_map = False
        block_h = s.h - fixed - _body_need(s, c, want_map)
    block_h = min(56 * u, max(36 * u, block_h))
    s.rect(-s.bleed, -s.bleed, s.w + 2 * s.bleed, block_h + s.bleed, c.accent)
    s.logo(m, m, 7.5 * u, on_paper=False)
    s.chip(s.w - m, m + 0.8 * u, c.chip, "#ffffff", 2.6 * u, text_fill=INK)
    start = timezone.localtime(c.event.start)
    day = str(start.day)
    day_size = min(24 * u, block_h * 0.42)
    s.text(m, block_h - 8.5 * u - day_size * 1.05, day, day_size, "display", 700, "#ffffff", lh=1)
    suffix = {1: "st", 2: "nd", 3: "rd", 21: "st", 22: "nd", 23: "rd", 31: "st"}.get(start.day, "th")
    s.text(m + fonts.width(day, "display", day_size, 700) + 0.5 * u, block_h - 8.5 * u - day_size * 0.95, suffix, day_size * 0.3, "display", 700, "#ffffff")
    month_line = f"{date_format(start, 'l j F')} · {c.when.split(', ', 1)[-1]}"
    s.text(m, block_h - 8.5 * u, month_line, 4.2 * u, "body", 700, "#ffffff", ls=0.06, upper=True)
    y = _draw_text_block(s, c, m, block_h + 5 * u, block) + 3.5 * u
    _body_columns(s, c, m, y, w, s.h - footer_h - m, want_map)
    _footer(s, c, "Mature Student Society")


def photo(s, c):
    u, m = s.u, 6 * s.u
    w = s.w - 2 * m
    b = s.bleed
    s.picture_panel(-b, -b, s.w + 2 * b, s.h + 2 * b, c.picture_href, c.picture_size, c.focal, c.scene_colour)
    s.defs.append({"t": "shade", "id": "shade"})
    s.rect(-b, -b, s.w + 2 * b, s.h + 2 * b, "url(#shade)")
    s.logo(m, m, 7 * u, on_paper=False)
    s.chip(s.w - m, m + 0.8 * u, c.chip, c.accent, 2.6 * u)
    # Lay the lower part out from the bottom up.
    card_h = 30 * u + 5 * u + 2 * 2.4 * u if (c.settings.show_qr or c.map_href) else 0
    url_y = s.h - m - 2.6 * u
    s.text(m, url_y, c.host_domain, 2.6 * u, "body", 600, "rgba(255,255,255,.9)", ls=0.03)
    card_y = s.h - m - card_h
    if card_h:
        card_w = (30 * u + 2.4 * u) * (2 if (c.settings.show_qr and c.map_href) else 1) + 2.4 * u
        cx = s.w - m - card_w
        s.rect(cx, card_y, card_w, card_h, "rgba(251,250,245,.95)", rx=2 * u)
        ix = cx + 2.4 * u
        if c.settings.show_qr:
            s.qr(ix, card_y + 2.4 * u, 30 * u, c.scan_url, "Scan to RSVP")
            ix += 30 * u + 2.4 * u
        if c.map_href:
            s.map(ix, card_y + 2.4 * u, 30 * u, 30 * u, c.map_href, "")
    text_bottom = card_y - 3 * u
    blocks = []
    if c.description:
        blocks.append(("desc", fonts.wrap(c.description, w, "body", 2.9 * u, 400, max_lines=3), 2.9 * u, "body", 400, "rgba(255,255,255,.92)", 1.35))
    if c.extra:
        blocks.append(("extra", [c.extra], 3.4 * u, "body", 700, "#ffffff", 1.2))
    if c.where:
        blocks.append(("where", fonts.wrap(c.where, w, "body", 3.4 * u, 400, max_lines=2), 3.4 * u, "body", 400, "rgba(255,255,255,.85)", 1.2))
    blocks.append(("when", fonts.wrap(c.when, w, "body", 4.3 * u, 700, max_lines=2), 4.3 * u, "body", 700, "#f3d58a", 1.15))
    size, lines = fonts.fit(c.title, w, "display", 10 * u, 700, 3, 6 * u)
    blocks.append(("title", lines, size, "display", 700, "#ffffff", 1.02))
    y = text_bottom
    for _, lines, size, key, weight, fill, lh in blocks:
        y -= size * lh * len(lines) + 1.2 * u
        s.text(m, y, lines, size, key, weight, fill, lh=lh)


def square(s, c):
    u, m = s.u, 6 * s.u
    w = s.w - 2 * m
    s.paper()
    s.logo(m, m, 7.5 * u)
    s.chip(s.w - m, m + 0.8 * u, c.chip, c.accent, 2.6 * u)
    y = m + 10 * u
    s.picture_panel(m, y, w, 34 * u, c.picture_href, c.picture_size, c.focal, c.scene_colour, rx=2.4 * u)
    y += 34 * u + 3.4 * u
    size, lines = fonts.fit(c.title, w, "display", 8.4 * u, 700, 2, 5.5 * u)
    y = s.text(m, y, lines, size, "display", 700, INK, lh=1.02) + 2 * u
    y = s.text(m, y, fonts.wrap(c.when_short, w, "body", 4 * u, 700, max_lines=1), 4 * u, "body", 700, c.accent) + 0.5 * u
    if c.where:
        y = s.text(m, y, fonts.wrap(c.where, w, "body", 3.4 * u, 400, max_lines=1), 3.4 * u, "body", 400, MUTED)
    if c.extra:
        s.text(m, y + 1 * u, fonts.wrap(c.extra, w * 0.6, "body", 3.2 * u, 700, max_lines=1), 3.2 * u, "body", 700, INK)
    qr_size = 22 * u
    if c.settings.show_qr:
        s.qr(s.w - m - qr_size, s.h - m - qr_size - 3 * u, qr_size, c.scan_url, "Scan to RSVP")
    s.text(m, s.h - m - 2.6 * u, c.host_domain, 2.6 * u, "body", 600, MUTED, ls=0.03)


def story(s, c):
    u, m = s.u, 7 * s.u
    w = s.w - 2 * m
    s.paper()
    s.logo(m, m, 7.5 * u)
    s.chip(s.w - m, m + 0.8 * u, c.chip, c.accent, 2.6 * u)
    y = m + 10 * u
    s.picture_panel(m, y, w, 56 * u, c.picture_href, c.picture_size, c.focal, c.scene_colour, rx=2.4 * u)
    y += 56 * u + 6 * u
    size, lines = fonts.fit(c.title, w, "display", 11 * u, 700, 3, 7 * u)
    y = s.text(m, y, lines, size, "display", 700, INK, lh=1.02) + 3 * u
    y = s.text(m, y, fonts.wrap(c.when, w, "body", 5.2 * u, 700, max_lines=2), 5.2 * u, "body", 700, c.accent, lh=1.15) + 0.6 * u
    if c.where:
        y = s.text(m, y, fonts.wrap(c.where, w, "body", 4 * u, 400, max_lines=2), 4 * u, "body", 400, MUTED)
    if c.extra:
        y = s.text(m, y + 1.5 * u, fonts.wrap(c.extra, w, "body", 4 * u, 700, max_lines=1), 4 * u, "body", 700, INK)
    footer_h = 10 * u
    if c.settings.show_qr:
        avail = s.h - footer_h - 10 * u - (y + 6 * u)
        qr_size = max(24 * u, min(44 * u, avail))
        s.qr(s.w / 2 - qr_size / 2, y + 6 * u + max(0, (avail - qr_size) / 2), qr_size, c.scan_url, "Scan to RSVP")
    s.band(s.h - footer_h, footer_h, PINE, to_bottom=True)
    fy = s.h - footer_h + (footer_h - 3 * u) / 2 - 0.3 * u
    s.text(m, fy, c.host_domain, 3 * u, "body", 600, "#ffffff", ls=0.03)
    s.text(s.w - m, fy, "MSS", 3 * u, "display", 700, "#ffffff", anchor="end")


TEMPLATES = {"classic": classic, "bold": bold, "photo": photo}


def build_scene(event, settings, request, map_href=""):
    """The scene for an event under ``settings``: print sizes use the
    chosen template; square and story have one layout each."""
    content = Content(event, settings, request, map_href)
    scene = Scene(settings.size, bleed=settings.print_marks)
    if settings.size == "square":
        square(scene, content)
    elif settings.size == "story":
        story(scene, content)
    else:
        TEMPLATES.get(settings.template, classic)(scene, content)
        scene.crop_marks()
    return scene
