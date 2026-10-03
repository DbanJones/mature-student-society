"""QR codes as inline SVG, via segno (pure Python, nothing stored)."""

import segno

INK = "#1a2420"


def qr_svg(url, size, x=0, y=0):
    """An inline <svg> for ``url`` placed at (x, y) and ``size`` wide, in the
    poster's own units. Error correction H leaves room for the lion mark
    in the middle; the four-module quiet zone is part of the symbol."""
    code = segno.make(url, error="h")
    inner = code.svg_inline(scale=1, border=4, dark=INK, light="#ffffff", omitsize=True)
    # segno emits <svg viewBox="0 0 n n" ...>: give it a position and size.
    return inner.replace(
        "<svg ", f'<svg x="{x}" y="{y}" width="{size}" height="{size}" ', 1
    )
