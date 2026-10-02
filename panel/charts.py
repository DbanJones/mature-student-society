"""Inline SVG charts for the statistics page. No JavaScript, no CDN: each
function returns a string of SVG that the template marks safe. Labels are
escaped here, so member-supplied names are fine."""

from django.utils.html import escape
from django.utils.safestring import mark_safe


def line_chart(points, width=560, height=170, pad=28, colour="var(--pine-700)"):
    """``points`` is a list of (label, value). Returns an SVG line with dots,
    a hover title per point, and the first/last labels on the axis."""
    if not points:
        return mark_safe("")
    values = [v for _, v in points]
    top = max(values) or 1
    n = len(points)
    step = (width - 2 * pad) / max(1, n - 1)
    coords = []
    for i, (_, v) in enumerate(points):
        x = pad + i * step
        y = height - pad - (height - 2 * pad) * (v / top)
        coords.append((round(x, 1), round(y, 1)))
    path = " ".join(f"{x},{y}" for x, y in coords)
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Line chart, {escape(points[0][0])} to {escape(points[-1][0])}, '
        f'peak {top}">',
        f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" '
        'class="chart-axis"/>',
        f'<text x="{pad}" y="{height - 8}" class="chart-label">{escape(points[0][0])}</text>',
        f'<text x="{width - pad}" y="{height - 8}" class="chart-label" text-anchor="end">'
        f'{escape(points[-1][0])}</text>',
        f'<text x="{pad - 4}" y="{pad + 4}" class="chart-label" text-anchor="end">{top}</text>',
        f'<polyline points="{path}" fill="none" stroke="{colour}" stroke-width="2.5" '
        'stroke-linejoin="round" stroke-linecap="round"/>',
    ]
    for (x, y), (label, v) in zip(coords, points):
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="4" fill="{colour}"><title>{escape(label)}: {v}</title></circle>'
        )
    parts.append("</svg>")
    return mark_safe("".join(parts))


def heatmap(rows, columns, cell=34, label_width=44, gap=3):
    """``rows`` is a list of (label, [value per column]). Darker = more."""
    if not rows:
        return mark_safe("")
    top = max((v for _, vals in rows for v in vals), default=0) or 1
    width = label_width + len(columns) * (cell + gap)
    height = 18 + len(rows) * (cell + gap)
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Heatmap">'
    ]
    for j, col in enumerate(columns):
        x = label_width + j * (cell + gap) + cell / 2
        parts.append(
            f'<text x="{x}" y="12" class="chart-label" text-anchor="middle">{escape(str(col))}</text>'
        )
    for i, (label, vals) in enumerate(rows):
        y = 18 + i * (cell + gap)
        parts.append(
            f'<text x="{label_width - 6}" y="{y + cell / 2 + 4}" class="chart-label" '
            f'text-anchor="end">{escape(str(label))}</text>'
        )
        for j, v in enumerate(vals):
            x = label_width + j * (cell + gap)
            opacity = 0.08 + 0.92 * (v / top) if v else 0.06
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="5" '
                f'fill="var(--pine-700)" fill-opacity="{opacity:.2f}">'
                f'<title>{escape(str(label))} {escape(str(columns[j]))}: {v}</title></rect>'
            )
    parts.append("</svg>")
    return mark_safe("".join(parts))
