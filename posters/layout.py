"""Lays a poster out as a scene: a list of primitives (rectangles, text
runs, images, the QR code, the map) in the poster's own units, for
``templates/posters/_scene.svg`` to draw. Nothing here touches the network
or the disk except reading the event picture's dimensions.

Every layout carries one QR code (to the event page, which has the map and
directions too) and, when the venue has been found, the map. Without a
photo the map takes the picture's place; with one, it sits beside the QR
code. Each layout is measured before it is drawn so that nothing overlaps:
the picture gives up height first, then the description, then the QR code
shrinks a little."""

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

QR_LABEL = "Scan to RSVP"
ROW_QR = 26  # units: the QR code when it shares a row with the map
COL_QR = 30  # units: the QR code on its own beside the text

# Geoapify draws map labels at a fixed pixel size, so the pixels asked for
# per poster unit decide how large street names come out: 8.4 per unit is
# about 4 per millimetre on A4, so labels print roughly 3 mm tall.
MAP_PX_PER_UNIT = 8.4
# Narrower than this and the map's OpenStreetMap credit no longer fits.
MAP_MIN_PX = 440
MAP_MAX_PX = 1200


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


NBSP = "\u00a0"


def _clock(moment):
    """"7:30 pm", "1 am": with a no-break space, so "pm" never wraps alone."""
    return moment.strftime("%I:%M %p").lstrip("0").lower().replace(":00", "").replace(" ", NBSP)


def when_text(event, short=False):
    start = timezone.localtime(event.start)
    day = date_format(start, "D j M" if short else "l j F")
    text = f"{day}, {_clock(start)}"
    if event.end and not short:
        text += f" to{NBSP}{_clock(timezone.localtime(event.end))}"
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


def map_pixels(w_units, h_units):
    """The map image size to ask Geoapify for, in its logical pixels, for a
    slot ``w_units`` by ``h_units``: the slot's own shape, so nothing is
    cropped, in steps of 10 so the cache gets reused."""
    scale = MAP_PX_PER_UNIT
    if w_units * scale < MAP_MIN_PX:
        scale = MAP_MIN_PX / w_units
    if w_units * scale > MAP_MAX_PX:
        scale = MAP_MAX_PX / w_units
    width = int(round(w_units * scale / 10) * 10)
    height = int(round(h_units * scale / 10) * 10)
    return width, min(MAP_MAX_PX, max(100, height))


class Scene:
    """Collects primitives. Coordinates are in canvas units; ``u`` is one
    percent of the canvas width, the unit every size is expressed in."""

    def __init__(self, size_key, bleed=False):
        self.w, self.h = CANVAS[size_key]
        self.u = self.w / 100
        # 3 mm of bleed in this sheet's own units: an A3 unit is 297/210 mm.
        mm_per_unit = {"a4": 1.0, "a3": 297 / 210}.get(size_key)
        self.bleed = BLEED_MM / mm_per_unit if (bleed and mm_per_unit) else 0
        self.items = []
        self.defs = []
        self.has_map = False

    def paper(self, fill=PAPER):
        """The whole sheet, bleed included."""
        b = self.bleed
        self.rect(-b, -b, self.w + 2 * b, self.h + 2 * b, fill)

    def band(self, y, h, fill, to_bottom=False):
        """A full-width band that reaches into the side bleed (and the
        bottom bleed when it is the footer)."""
        b = self.bleed
        self.rect(-b, y, self.w + 2 * b, h + (b if to_bottom else 0), fill)

    def rect(self, x, y, w, h, fill, rx=0, opacity=None, click_through=False):
        self.items.append({"t": "rect", "x": x, "y": y, "w": w, "h": h, "fill": fill, "rx": rx,
                           "opacity": opacity, "click_through": click_through})

    def text(self, x, y, lines, size, key="body", weight=400, fill=INK, anchor="start",
             lh=1.2, ls=0, upper=False):
        if isinstance(lines, str):
            lines = [lines]
        if upper:
            lines = [line.upper() for line in lines]
        self.items.append({
            "t": "text", "x": x, "y": y + size * 0.78, "lines": lines, "size": size,
            "family": fonts.FAMILIES[key], "weight": weight, "fill": fill, "anchor": anchor,
            "dy": size * lh, "ls": ls * size, "top": y, "key": key,
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

    def _clip(self, x, y, w, h, rx):
        clip = f"clip{len(self.items)}_{len(self.defs)}"
        self.defs.append({"t": "clip", "id": clip, "x": x, "y": y, "w": w, "h": h, "rx": rx})
        return clip

    def picture_panel(self, x, y, w, h, href, image_size, focal, colour, rx=0):
        """A photo clipped to the panel, or a generated colour scene. The
        image element is always present so a photo chosen in the browser can
        be dropped into it."""
        clip = self._clip(x, y, w, h, rx)
        grad = f"grad{len(self.items)}"
        self.defs.append({"t": "grad", "id": grad, "colour": colour})
        self.rect(x, y, w, h, f"url(#{grad})", rx=rx)
        self.items.append({"t": "grain", "x": x, "y": y, "w": w, "h": h, "clip": clip})
        if not href:
            lion_w = min(w * 0.9, h * 0.8 * 218 / 91)  # lion-mark.png is 218 by 91
            lion_h = lion_w * 91 / 218
            self.items.append({
                "t": "watermark", "href": "/static/img/lion-mark.png", "clip": clip,
                "x": x + w - lion_w * 0.92, "y": y + (h - lion_h) / 2, "w": lion_w, "h": lion_h,
            })
        if href:
            ix, iy, iw, ih = cover_box((x, y, w, h), image_size, focal)
        else:
            ix, iy, iw, ih = x, y, w, h
        self.items.append({
            "t": "photo", "href": href or "", "x": ix, "y": iy, "w": iw, "h": ih,
            "clip": clip, "panel": (x, y, w, h), "hidden": not href,
        })

    def photo_slot(self, x, y, w, h, rx=0):
        """An empty, hidden photo element over a panel the map is using, so a
        photo chosen in the browser still has somewhere to go."""
        clip = self._clip(x, y, w, h, rx)
        self.items.append({
            "t": "photo", "href": "", "x": x, "y": y, "w": w, "h": h,
            "clip": clip, "panel": (x, y, w, h), "hidden": True,
        })

    def qr_label_size(self, size):
        return max(size * 0.072, self.u * 2.2)

    def qr_height(self, size):
        """The QR code plus its label, down to the label's baseline."""
        return size + self.qr_label_size(size) * 1.18

    def qr(self, x, y, size, url, label=QR_LABEL):
        self.items.append({"t": "qr", "x": x, "y": y, "size": size, "url": url})
        mark_w = size * 0.3
        mark_h = mark_w * 91 / 218
        self.rect(x + size / 2 - mark_w / 2 - size * 0.025, y + size / 2 - mark_h / 2 - size * 0.025,
                  mark_w + size * 0.05, mark_h + size * 0.05, "#ffffff", rx=size * 0.02)
        self.items.append({"t": "image", "href": "/static/img/lion-mark.png",
                           "x": x + size / 2 - mark_w / 2, "y": y + size / 2 - mark_h / 2, "w": mark_w, "h": mark_h})
        label_size = self.qr_label_size(size)
        self.text(x + size / 2, y + size + label_size * 0.4, label, label_size, "body", 700, INK,
                  anchor="middle", ls=0.06, upper=True)
        return y + self.qr_height(size)

    def map(self, x, y, w, h, href, rx=None):
        """The venue map, asked for at exactly this slot's shape so nothing
        is cropped: the OpenStreetMap credit sits in its bottom corner."""
        rx = self.u * 1.6 if rx is None else rx
        self.has_map = True
        clip = self._clip(x, y, w, h, rx)
        self.rect(x, y, w, h, SAGE_SOFT, rx=rx)
        pw, ph = map_pixels(w / self.u, h / self.u)
        self.items.append({
            "t": "image", "href": f"{href}?w={pw}&h={ph}", "x": x, "y": y, "w": w, "h": h,
            "clip": clip, "map": True,
        })
        # The map is centred on the venue, so the pin's tip goes in the middle.
        # Drawn here rather than by Geoapify: always present, crisp in print.
        pin = min(9 * self.u, max(4.5 * self.u, h * 0.24))
        self.items.append({"t": "pin", "x": x + w / 2, "y": y + h / 2, "size": pin, "fill": BRICK})

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
        return f"{-b:g} {-b:g} {self.w + 2 * b:g} {self.h + 2 * b:g}"


def _tidy(text):
    """Typed text with every run of whitespace (no-break spaces pasted from
    an email included) made one ordinary space, so it always wraps."""
    return " ".join((text or "").split())


class Content:
    """Everything the templates draw, gathered once from the event.

    ``device_photo`` says the studio has a photo from the member's device
    that it will drop into the picture panel, so the layout keeps a panel
    for it (and puts the map beside the QR code instead)."""

    def __init__(self, event, settings, request, map_href="", device_photo=False):
        self.event = event
        self.settings = settings
        self.title = _tidy(settings.headline or event.title)
        self.when = when_text(event)
        self.when_short = when_text(event, short=True)
        self.where = _tidy(event.location)
        self.extra = _tidy(settings.extra_line)
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
            self.facts.append(("Open to", "members only" if event.members_only else "members, partners and guests"))
            self.facts.append(("Host", host.get_full_name() or host.username))
            if event.capacity:
                self.facts.append(("Places", f"{event.capacity}, book early"))
        self.host_domain = request.get_host()
        self.scan_url = request.build_absolute_uri(reverse("poster_scan", args=[event.slug]))
        self.map_href = map_href if settings.show_map else ""
        self.picture_href, self.picture_size = event_picture(event, settings)
        self.has_photo = bool(self.picture_href) or bool(device_photo)
        self.focal = (settings.focal_x, settings.focal_y)
        self.scene_colour = self.accent


def _visual(s, c, x, y, w, h, rx):
    """The picture slot: the event's photo; without one, the map; without
    that, a colour scene. Returns True when the map went here."""
    if c.map_href and not c.has_photo:
        s.map(x, y, w, h, c.map_href, rx=rx)
        s.photo_slot(x, y, w, h, rx)
        return True
    s.picture_panel(x, y, w, h, c.picture_href, c.picture_size, c.focal, c.scene_colour, rx=rx)
    return False


def _facts_flow(s, c, width, size):
    """The facts as "Label · value" pairs flowing across lines. Returns the
    lines, each a list of (x offset, label, label width, value)."""
    gap = size * 1.6
    lines, line, x = [], [], 0
    for label, value in c.facts:
        lead = f"{label} · "
        lead_w = fonts.width(lead, "body", size, 700)
        value_w = fonts.width(value, "body", size, 400)
        if line and x + lead_w + value_w > width:
            lines.append(line)
            line, x = [], 0
        if lead_w + value_w > width:
            value = (fonts.wrap(value, width - lead_w, "body", size, 400, max_lines=1) or [""])[0]
            value_w = fonts.width(value, "body", size, 400)
        line.append((x, lead, lead_w, value))
        x += lead_w + value_w + gap
    if line:
        lines.append(line)
    return lines


def _draw_facts(s, x, y, lines, size):
    for line in lines:
        for dx, lead, lead_w, value in line:
            s.text(x + dx, y, lead, size, "body", 700, INK)
            s.text(x + dx + lead_w, y, value, size, "body", 400, MUTED)
        y += size * 1.35
    return y


def _description(s, c, x, y, w, room, size, most=6):
    """As many lines of the description as fit in ``room`` (two at least,
    or none). Returns the y below it."""
    if not c.description:
        return y
    lines = min(most, int(room / (size * 1.3) + 1e-6))  # 1.9999… lines is two
    if lines < 2:
        return y
    return s.paragraph(x, y, c.description, w, size, "body", 400, INK, lh=1.3, max_lines=lines)


def _action_row(s, c, x, y, w, qr, height):
    """The map and the QR code side by side, bottoms aligned."""
    gap = 4 * s.u
    map_w = w
    if qr:
        s.qr(x + w - qr, y, qr, c.scan_url)
        map_w = w - qr - gap
    if c.map_href:
        s.map(x, y, map_w, height, c.map_href)


def _lower_need(s, c, w, row_map, desc_lines=3):
    """Height the lower block wants with ``desc_lines`` of description."""
    u = s.u
    fact = 2.6 * u
    desc_h = (3 * u * 1.3 * desc_lines + 2 * u) if c.description else 0
    if row_map:
        facts = _facts_flow(s, c, w, fact)[:2]
        facts_h = len(facts) * fact * 1.35 + (2.5 * u if facts else 0)
        row = s.qr_height(ROW_QR * u) if c.settings.show_qr else 26 * u
        return desc_h + facts_h + row
    if c.settings.show_qr:
        facts = _facts_flow(s, c, w - 36 * u - 5 * u, fact)
        return max(desc_h + len(facts) * fact * 1.35, s.qr_height(COL_QR * u))
    return desc_h + len(_facts_flow(s, c, w, fact)) * fact * 1.35


def _lower(s, c, x, y, w, bottom, row_map):
    """Description, facts, the QR code and, unless it is the picture, the
    map. With the map: the text runs across the full width above a row of
    map and QR code standing on the bottom margin. Without: text on the
    left, the QR code on the right."""
    u = s.u
    fact = 2.6 * u
    if row_map:
        facts = _facts_flow(s, c, w, fact)[:2]
        facts_h = len(facts) * fact * 1.35
        qr = ROW_QR * u if c.settings.show_qr else 0
        row = s.qr_height(qr) if qr else 26 * u
        short = (facts_h + 2.5 * u) - (bottom - row - y)
        if short > 0:  # squeezed: a smaller code and a shorter map before anything is lost
            if qr:
                qr = max(20 * u, qr - short)
                row = s.qr_height(qr)
            else:
                row = max(18 * u, row - short)
        row_y = bottom - row
        text_bottom = row_y - 2.5 * u
        ty = _description(s, c, x, y, w, text_bottom - y - facts_h - 2 * u, 3 * u)
        if ty > y:
            ty += 2 * u
        if facts and ty + facts_h <= text_bottom + 0.01:
            _draw_facts(s, x, ty, facts, fact)
        _action_row(s, c, x, row_y, w, qr, row)
        return
    left_w = w
    if c.settings.show_qr:
        right_w, gap = 36 * u, 5 * u
        left_w = w - right_w - gap
        qr = COL_QR * u
        over = s.qr_height(qr) - (bottom - y)
        if over > 0:
            qr = max(18 * u, qr - over)
        s.qr(x + w - right_w + (right_w - qr) / 2, y, qr, c.scan_url)
    facts = _facts_flow(s, c, left_w, fact)
    facts_h = len(facts) * fact * 1.35
    ty = _description(s, c, x, y, left_w, bottom - y - facts_h - 2 * u, 3 * u, most=8)
    if ty > y:
        ty += 2 * u
    for line in facts:
        if ty + fact * 1.35 > bottom + 0.01:
            break
        ty = _draw_facts(s, x, ty, [line], fact)


def _measure_text_block(s, c, w, title_size, with_when=True):
    """Heights of the title, when, where and extra lines, measured first so
    the picture can give up height before anything else is dropped."""
    u = s.u
    size, lines = fonts.fit(c.title, w, "display", title_size, 700, 3, 6 * u)
    when_lines = fonts.wrap(c.when, w, "body", 4.3 * u, 700, max_lines=2) if with_when else []
    where_lines = fonts.wrap(c.where, w, "body", 3.4 * u, 400, max_lines=2) if c.where else []
    extra_lines = fonts.wrap(c.extra, w, "body", 3.4 * u, 700, max_lines=1) if c.extra else []
    height = (
        size * 1.02 * len(lines) + 2.6 * u + (4.3 * u * 1.15 * len(when_lines) + 0.6 * u if when_lines else 0)
        + 3.4 * u * 1.2 * len(where_lines) + (3.4 * u * 1.2 + 1.2 * u if extra_lines else 0)
    )
    return {"size": size, "lines": lines, "when": when_lines, "where": where_lines, "extra": extra_lines, "height": height}


def _draw_text_block(s, c, x, y, block):
    u = s.u
    y = s.text(x, y, block["lines"], block["size"], "display", 700, INK, lh=1.02) + 2.6 * u
    if block["when"]:
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
    bottom = s.h - footer_h - m
    s.paper()
    s.logo(m, m, 7.5 * u)
    s.chip(s.w - m, m + 0.8 * u, c.chip, c.accent, 2.6 * u)
    block = _measure_text_block(s, c, w, 9.2 * u)
    map_on_top = bool(c.map_href) and not c.has_photo
    row_map = bool(c.map_href) and not map_on_top
    top = m + 10 * u
    room = bottom - top - 4.6 * u - block["height"] - 4 * u
    hero = room - _lower_need(s, c, w, row_map)
    hero = min((40 if map_on_top else 36) * u, max(18 * u, hero))
    _visual(s, c, m, top, w, hero, 2.4 * u)
    y = _draw_text_block(s, c, m, top + hero + 4.6 * u, block) + 4 * u
    _lower(s, c, m, y, w, bottom, row_map)
    _footer(s, c, "Mature Student Society · nearly 1,000 members")


def bold(s, c):
    u, m = s.u, 6 * s.u
    w = s.w - 2 * m
    footer_h = 9 * u
    bottom = s.h - footer_h - m
    s.paper()
    block = _measure_text_block(s, c, w, 8.4 * u, with_when=False)
    row_map = bool(c.map_href)
    block_h = bottom - 5 * u - block["height"] - 3.5 * u - _lower_need(s, c, w, row_map, desc_lines=2)
    block_h = min(56 * u, max(40 * u, block_h))
    s.rect(-s.bleed, -s.bleed, s.w + 2 * s.bleed, block_h + s.bleed, c.accent)
    logo_h = 7.5 * u
    s.logo(m, m, logo_h, on_paper=False)
    s.chip(s.w - m, m + 0.8 * u, c.chip, "#ffffff", 2.6 * u, text_fill=INK)
    start = timezone.localtime(c.event.start)
    day = str(start.day)
    # The numerals stand on a baseline just above the date line and must
    # clear the logo's paper badge (digits are about 0.7 of their size tall).
    month_top = block_h - 8.5 * u
    baseline = month_top - 2 * u
    clear_top = m + logo_h + logo_h * 0.18 + 2 * u
    digit_h = fonts.ink_height(day, "display", 700)
    day_size = min(24 * u, (baseline - clear_top) / digit_h)
    s.text(m, baseline - day_size * 0.78, day, day_size, "display", 700, "#ffffff", lh=1)
    suffix = {1: "st", 2: "nd", 3: "rd", 21: "st", 22: "nd", 23: "rd", 31: "st"}.get(start.day, "th")
    suffix_size = day_size * 0.3
    digits_top = baseline - digit_h * day_size
    suffix_top = digits_top + fonts.ink_height(suffix, "display", 700) * suffix_size - 0.78 * suffix_size
    s.text(m + fonts.width(day, "display", day_size, 700) + 0.5 * u, suffix_top, suffix, suffix_size, "display", 700, "#ffffff")
    # The date line is Bold's only date: the long form if it fits, else the
    # short one, shrunk to the width if it must be.
    times = c.when.split(", ", 1)[-1].replace(NBSP, " ")
    for date_line in (f"{date_format(start, 'l j F')} · {times}", f"{date_format(start, 'D j M')} · {times}"):
        per_unit = fonts.width(date_line.upper(), "body", 1, 700, letter_spacing=0.06)
        date_size = min(4.2 * u, w / per_unit)
        if date_size >= 3.6 * u:
            break
    s.text(m, month_top, date_line, date_size, "body", 700, "#ffffff", ls=0.06, upper=True)
    y = _draw_text_block(s, c, m, block_h + 5 * u, block) + 3.5 * u
    _lower(s, c, m, y, w, bottom, row_map)
    _footer(s, c, "Mature Student Society")


def photo(s, c):
    u, m = s.u, 6 * s.u
    w = s.w - 2 * m
    b = s.bleed
    s.picture_panel(-b, -b, s.w + 2 * b, s.h + 2 * b, c.picture_href, c.picture_size, c.focal, c.scene_colour)
    s.defs.append({"t": "shade", "id": "shade"})
    # Clicks pass through the shade to the photo, to move its centre.
    s.rect(-b, -b, s.w + 2 * b, s.h + 2 * b, "url(#shade)", click_through=True)
    s.logo(m, m, 7 * u, on_paper=False)
    s.chip(s.w - m, m + 0.8 * u, c.chip, c.accent, 2.6 * u)
    # The card of QR code and map, bottom right, then the text bottom-up.
    pad = 2.4 * u
    qr = ROW_QR * u if c.settings.show_qr else 0
    inner = s.qr_height(qr) if qr else (26 * u if c.map_href else 0)
    map_w = 36 * u if c.map_href else 0
    card_w = pad + (map_w + pad if map_w else 0) + (qr + pad if qr else 0) if inner else 0
    card_h = inner + 2 * pad if inner else 0
    domain_size = 2.6 * u
    domain_w = fonts.width(c.host_domain, "body", domain_size, 600)
    domain_beside = not card_w or m + domain_w + 3 * u < s.w - m - card_w
    if card_h:
        cx, cy = s.w - m - card_w, s.h - m - card_h
        s.rect(cx, cy, card_w, card_h, "rgba(251,250,245,.95)", rx=2 * u)
        ix = cx + pad
        if map_w:
            s.map(ix, cy + pad, map_w, inner, c.map_href)
            ix += map_w + pad
        if qr:
            s.qr(ix, cy + pad, qr, c.scan_url)
    if domain_beside:
        s.text(m, s.h - m - domain_size, c.host_domain, domain_size, "body", 600, "rgba(255,255,255,.9)", ls=0.03)
        text_bottom = (s.h - m - card_h - 3 * u) if card_h else (s.h - m - domain_size - 3 * u)
    else:
        text_bottom = s.h - m - card_h - 3 * u
    blocks = []
    if not domain_beside:
        blocks.append(([c.host_domain], domain_size, "body", 600, "rgba(255,255,255,.9)", 1.2))
    if c.description:
        blocks.append((fonts.wrap(c.description, w, "body", 2.9 * u, 400, max_lines=3), 2.9 * u, "body", 400, "rgba(255,255,255,.92)", 1.35))
    if c.extra:
        blocks.append((fonts.wrap(c.extra, w, "body", 3.4 * u, 700, max_lines=1), 3.4 * u, "body", 700, "#ffffff", 1.2))
    if c.where:
        blocks.append((fonts.wrap(c.where, w, "body", 3.4 * u, 400, max_lines=2), 3.4 * u, "body", 400, "rgba(255,255,255,.85)", 1.2))
    blocks.append((fonts.wrap(c.when, w, "body", 4.3 * u, 700, max_lines=2), 4.3 * u, "body", 700, "#f3d58a", 1.15))
    size, lines = fonts.fit(c.title, w, "display", 10 * u, 700, 3, 6 * u)
    blocks.append((lines, size, "display", 700, "#ffffff", 1.02))
    y = text_bottom
    for lines, size, key, weight, fill, lh in blocks:
        y -= size * lh * len(lines) + 1.2 * u
        s.text(m, y, lines, size, key, weight, fill, lh=lh)


def square(s, c):
    u, m = s.u, 6 * s.u
    w = s.w - 2 * m
    bottom = s.h - m
    s.paper()
    s.logo(m, m, 7.5 * u)
    s.chip(s.w - m, m + 0.8 * u, c.chip, c.accent, 2.6 * u)
    top = m + 10 * u
    # Bottom zone: the details on the left, the QR code on the right, the
    # site address under the details. Measured first; the picture (or the
    # map, when there's no photo) takes what is left.
    qr = 22 * u if c.settings.show_qr else 0
    side_w = w - qr - 4 * u if qr else w
    when = fonts.wrap(c.when_short, side_w, "body", 4 * u, 700, max_lines=2)
    where = fonts.wrap(c.where, side_w, "body", 3.4 * u, 400, max_lines=2) if c.where else []
    extra = fonts.wrap(c.extra, side_w, "body", 3.2 * u, 700, max_lines=1) if c.extra else []
    details_h = 4 * u * 1.2 * len(when) + 0.5 * u + 3.4 * u * 1.2 * len(where) + (u + 3.2 * u * 1.2 if extra else 0)
    domain = 2.6 * u
    zone_h = max(s.qr_height(qr) if qr else 0, details_h + 2 * u + domain)
    zone_y = bottom - zone_h
    size, lines = fonts.fit(c.title, w, "display", 8.4 * u, 700, 2, 5.5 * u)
    title_h = size * 1.02 * len(lines)
    panel_h = max(18 * u, zone_y - 3 * u - title_h - 3.4 * u - top)
    _visual(s, c, m, top, w, panel_h, 2.4 * u)
    s.text(m, top + panel_h + 3.4 * u, lines, size, "display", 700, INK, lh=1.02)
    y = s.text(m, zone_y, when, 4 * u, "body", 700, c.accent) + 0.5 * u
    if where:
        y = s.text(m, y, where, 3.4 * u, "body", 400, MUTED)
    if extra:
        s.text(m, y + u, extra, 3.2 * u, "body", 700, INK)
    if qr:
        s.qr(s.w - m - qr, bottom - s.qr_height(qr), qr, c.scan_url)
    s.text(m, bottom - domain, c.host_domain, domain, "body", 600, MUTED, ls=0.03)


def story(s, c):
    u, m = s.u, 7 * s.u
    w = s.w - 2 * m
    footer_h = 10 * u
    bottom = s.h - footer_h - 6 * u
    s.paper()
    s.logo(m, m, 7.5 * u)
    s.chip(s.w - m, m + 0.8 * u, c.chip, c.accent, 2.6 * u)
    top = m + 10 * u
    map_on_top = bool(c.map_href) and not c.has_photo
    row_map = bool(c.map_href) and not map_on_top
    size, lines = fonts.fit(c.title, w, "display", 11 * u, 700, 3, 7 * u)
    when = fonts.wrap(c.when, w, "body", 5.2 * u, 700, max_lines=2)
    where = fonts.wrap(c.where, w, "body", 4 * u, 400, max_lines=2) if c.where else []
    extra = fonts.wrap(c.extra, w, "body", 4 * u, 700, max_lines=1) if c.extra else []
    text_h = (size * 1.02 * len(lines) + 3 * u + 5.2 * u * 1.15 * len(when) + 0.6 * u
              + 4 * u * 1.2 * len(where) + (1.5 * u + 4 * u * 1.2 if extra else 0))
    if row_map:
        qr = 30 * u if c.settings.show_qr else 0
        lower = s.qr_height(qr) if qr else 34 * u
    else:
        qr = 40 * u if c.settings.show_qr else 0
        lower = s.qr_height(qr) if qr else 0
    gap = 6 * u
    panel_h = bottom - top - gap - text_h - (gap + lower if lower else 0)
    panel_h = min(72 * u, max(30 * u, panel_h))
    short = (top + panel_h + gap + text_h + (gap + lower if lower else 0)) - bottom
    if short > 0 and qr:  # squeezed: a smaller code before anything overlaps
        qr = max(22 * u, qr - short)
        lower = s.qr_height(qr)
    elif short > 0 and lower:
        lower = max(20 * u, lower - short)
    _visual(s, c, m, top, w, panel_h, 2.4 * u)
    y = top + panel_h + gap
    y = s.text(m, y, lines, size, "display", 700, INK, lh=1.02) + 3 * u
    y = s.text(m, y, when, 5.2 * u, "body", 700, c.accent, lh=1.15) + 0.6 * u
    if where:
        y = s.text(m, y, where, 4 * u, "body", 400, MUTED)
    if extra:
        s.text(m, y + 1.5 * u, extra, 4 * u, "body", 700, INK)
    if lower:
        lower_y = bottom - lower
        if row_map:
            _action_row(s, c, m, lower_y, w, qr, lower)
        else:
            s.qr(s.w / 2 - qr / 2, lower_y, qr, c.scan_url)
    s.band(s.h - footer_h, footer_h, PINE, to_bottom=True)
    fy = s.h - footer_h + (footer_h - 3 * u) / 2 - 0.3 * u
    s.text(m, fy, c.host_domain, 3 * u, "body", 600, "#ffffff", ls=0.03)
    s.text(s.w - m, fy, "MSS", 3 * u, "display", 700, "#ffffff", anchor="end")


TEMPLATES = {"classic": classic, "bold": bold, "photo": photo}


def build_scene(event, settings, request, map_href="", device_photo=False):
    """The scene for an event under ``settings``: print sizes use the
    chosen template; square and story have one layout each."""
    content = Content(event, settings, request, map_href, device_photo)
    scene = Scene(settings.size, bleed=settings.print_marks)
    if settings.size == "square":
        square(scene, content)
    elif settings.size == "story":
        story(scene, content)
    else:
        TEMPLATES.get(settings.template, classic)(scene, content)
        scene.crop_marks()
    return scene
