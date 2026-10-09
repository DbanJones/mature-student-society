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

# --- what the bundled fonts cannot measure ----------------------------------------
# Emoji come from the viewer's emoji font, not from the bundled fonts, and are
# about this many ems wide. One emoji may be several code points (a flag, a
# skin tone, a family joined with U+200D); it is still one glyph.
EMOJI_EM = 1.25
# Pictographs that are emoji on their own (the Emoji_Presentation set).
_PICTO = (
    "\U0001F000-\U0001FAFF\u231A\u231B\u23E9-\u23F3\u23F8-\u23FA\u25FD\u25FE\u2614\u2615"
    "\u2648-\u2653\u267F\u2693\u26A1\u26AA\u26AB\u26BD\u26BE\u26C4\u26C5\u26CE\u26D4\u26EA"
    "\u26F2-\u26F5\u26FA\u26FD\u2705\u270A\u270B\u2728\u274C\u274E\u2753-\u2755\u2757"
    "\u2795-\u2797\u27B0\u27BF\u2B1B\u2B1C\u2B50\u2B55"
)
# Text symbols (★ ✓ ❄ © …) are emoji only when U+FE0F asks for it.
_SYMBOL = "\u00A9\u00AE\u2122\u2190-\u2BFF\u3030\u303D\u3297\u3299"
_ONE = (
    "(?:[\U0001F1E6-\U0001F1FF]{2}"  # a flag
    "|[0-9#*]\uFE0F?\u20E3"  # a keycap
    "|(?:[" + _PICTO + "]|[" + _SYMBOL + "]\uFE0F)"
    "\uFE0F?[\U0001F3FB-\U0001F3FF]?[\U000E0020-\U000E007F]*)"  # with a skin tone and tags
)
_EMOJI = re.compile(_ONE + "(?:\u200D" + _ONE + ")*")
# Selectors, joiners and tag characters take no room of their own.
_INVISIBLE = re.compile("[\uFE0E\uFE0F\u200D\u20E3\U000E0020-\U000E007F]")
# Scripts the viewer's fallback font draws a full em wide.
_WIDE = re.compile("[\u2E80-\u9FFF\uAC00-\uD7AF\uF900-\uFAFF\uFF00-\uFFEF]")
_PROBE = "\U0010FFFD"  # in no font: how a glyph the font lacks measures


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


@lru_cache(maxsize=16)
def _missing_metrics(key, weight):
    font = _font(key, weight)
    return (font.getlength(_PROBE), font.getbbox(_PROBE)) if font else None


@lru_cache(maxsize=4096)
def _missing(key, weight, char):
    """True when the bundled font has no glyph for ``char`` (it measures
    exactly like the missing-glyph box), so the viewer sees a fallback."""
    font = _font(key, weight)
    return font is not None and (font.getlength(char), font.getbbox(char)) == _missing_metrics(key, weight)


def width(text, key="body", size=1.0, weight=400, letter_spacing=0.0):
    """Width of ``text`` at ``size`` (any unit) in the same unit: the bundled
    font's own advances for the glyphs it has, an estimate for the rest."""
    emoji = len(_EMOJI.findall(text))
    plain = _INVISIBLE.sub("", _EMOJI.sub("", text))
    font = _font(key, weight)
    if font is None:
        # Font file missing: a conservative average glyph width.
        base = len(plain) * (0.56 if key == "body" else 0.6)
    else:
        base = font.getlength(plain) / MEASURE_PX
        notdef = _missing_metrics(key, weight)[0] / MEASURE_PX
        for char in plain:
            if ord(char) >= 0x0250 and _missing(key, weight, char):
                base += (1.0 if _WIDE.match(char) else 0.75) - notdef
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
