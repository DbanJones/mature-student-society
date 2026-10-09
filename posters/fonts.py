"""Text measuring for the poster layout, using the same font files the
browser renders with (static/fonts), so line breaks decided here match
what is printed."""

import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from PIL import ImageFont

FONT_DIR = Path(settings.BASE_DIR) / "static" / "fonts"
FILES = {
    "display": "LibreCaslonText-Variable.ttf",
    "body": "SourceSans3-Variable.ttf",
}
FAMILIES = {
    "display": '"Libre Caslon Text", "Iowan Old Style", Palatino, Georgia, serif',
    "body": '"Source Sans 3", "Segoe UI", system-ui, sans-serif',
}
MEASURE_PX = 100
# Emoji come from the viewer's emoji font, not from Source Sans, and are
# about this many ems wide; the bundled fonts would measure them as a
# narrow missing-glyph box.
EMOJI_EM = 1.25
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")
_INVISIBLE = re.compile("[\uFE0F\u200D]")  # variation selectors and joiners take no room


@lru_cache(maxsize=16)
def _font(key, weight):
    try:
        font = ImageFont.truetype(str(FONT_DIR / FILES[key]), MEASURE_PX)
        try:
            font.set_variation_by_axes([weight])
        except Exception:
            pass
        return font
    except OSError:
        return None


def width(text, key="body", size=1.0, weight=400, letter_spacing=0.0):
    """Width of ``text`` at ``size`` (any unit) in the same unit."""
    plain = _INVISIBLE.sub("", text)
    emoji = len(_EMOJI.findall(plain))
    plain = _EMOJI.sub("", plain)
    font = _font(key, weight)
    if font is None:
        # Font file missing: a conservative average glyph width.
        base = len(plain) * (0.56 if key == "body" else 0.6)
    else:
        base = font.getlength(plain) / MEASURE_PX
    base += emoji * EMOJI_EM
    return base * size + letter_spacing * size * max(0, len(plain) + emoji - 1)


def wrap(text, max_width, key="body", size=1.0, weight=400, max_lines=None):
    """Greedy word wrap. Returns the lines; a final line may be truncated with
    an ellipsis when ``max_lines`` is set."""
    # Ordinary spaces only: a no-break space keeps "7:30 pm" on one line.
    words = [word for word in re.split(r"[ \t\r\n]+", text) if word]
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and width(candidate, key, size, weight) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and width(last + "…", key, size, weight) > max_width:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        lines[-1] = last.rstrip(" .,;:·—–-") + "…"
    return lines


def ink_height(text, key="display", weight=700, fallback=0.82):
    """How far ``text`` rises above its baseline, as a fraction of its size
    (Libre Caslon's bold digits are about 0.8)."""
    font = _font(key, weight)
    if font is None:
        return fallback
    try:
        top = font.getbbox(text, anchor="ls")[1]
    except Exception:
        return fallback
    return max(0.1, -top / MEASURE_PX)


def fit(text, max_width, key, size, weight, max_lines, min_size):
    """Shrink ``size`` in 8% steps until ``text`` wraps within ``max_lines``
    and no line (a long unbroken word, say) is wider than ``max_width``.
    Returns (size, lines)."""
    while True:
        lines = wrap(text, max_width, key, size, weight)
        too_wide = any(width(line, key, size, weight) > max_width for line in lines)
        if (len(lines) <= max_lines and not too_wide) or size <= min_size:
            if len(lines) > max_lines:
                lines = wrap(text, max_width, key, size, weight, max_lines=max_lines)
            return size, lines
        size *= 0.92
