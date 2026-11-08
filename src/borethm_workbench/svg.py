"""Deterministic inline SVG charts for the results page and the report.

The charts plot values they are given; they compute no physical quantity.
Output depends only on the inputs, so the same run gives the same bytes.
"""

from html import escape
import math

PALETTE = ("#18807a", "#d0703c", "#5554a4", "#a35873", "#6b7b2c", "#596a78")
INK = "#25313b"
MUTED = "#66727d"
GRID = "#dde3e6"
FONT = "font-family=\"Segoe UI, Helvetica, Arial, sans-serif\""


def _n(value):
    """Fixed-precision coordinate text."""
    text = f"{value:.2f}"
    return "0.00" if text == "-0.00" else text


def nice_ticks(low, high, count=6):
    if not (math.isfinite(low) and math.isfinite(high)):
        return [0.0, 1.0]
    if high <= low:
        high = low + (abs(low) or 1.0) * 0.1
    span = high - low
    raw = span / max(count - 1, 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    for step in (1, 2, 2.5, 5, 10):
        if raw <= step * magnitude:
            step *= magnitude
            break
    first = math.floor(low / step) * step
    ticks = []
    value = first
    while value <= high + step * 1e-9:
        ticks.append(round(value, 10))
        value += step
    if ticks[-1] < high:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def _label(value):
    if value == 0:
        return "0"
    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{value:,.0f}"
    if magnitude >= 10:
        return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"
    if magnitude >= 1:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _frame(width, height, title):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="{escape(title)}" '
        f'style="max-width:100%;height:auto">',
        f"<title>{escape(title)}</title>",
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
    ]


def _legend(parts, items, x, y):
    for index, (name, colour, dash) in enumerate(items):
        row = y + index * 16
        if dash == "point":
            parts.append(
                f'<circle cx="{_n(x + 11)}" cy="{_n(row)}" r="3.5" fill="{colour}"/>'
            )
        else:
            dashes = f' stroke-dasharray="{dash}"' if dash else ""
            parts.append(
                f'<line x1="{_n(x)}" y1="{_n(row)}" x2="{_n(x + 22)}" y2="{_n(row)}" '
                f'stroke="{colour}" stroke-width="2.5"{dashes}/>'
            )
        parts.append(
            f'<text x="{_n(x + 28)}" y="{_n(row + 4)}" font-size="11" fill="{INK}" {FONT}>'
            f"{escape(name)}</text>"
        )


def depth_chart(series, x_label, y_label, title, points=(), bands=(), width=640, height=440):
    """Profiles against depth, depth increasing downwards.

    series: dicts with name, x, y, and optional colour and dash.
    points: dicts with name, x, y (lists) drawn as markers, optional colour.
    bands: dicts with name, low, high (depth interval shaded across the plot).
    """
    xs = [v for s in series for v in s["x"] if v is not None and math.isfinite(v)]
    xs += [v for p in points for v in p["x"] if v is not None and math.isfinite(v)]
    ys = [v for s in series for v in s["y"] if v is not None and math.isfinite(v)]
    ys += [v for p in points for v in p["y"] if v is not None and math.isfinite(v)]
    if not xs or not ys:
        parts = _frame(width, height, title)
        parts.append(
            f'<text x="20" y="40" font-size="13" fill="{MUTED}" {FONT}>No values to plot.</text></svg>'
        )
        return "".join(parts)
    xt = nice_ticks(min(xs), max(xs))
    yt = nice_ticks(min(0.0, min(ys)), max(ys))
    legend_rows = len(series) + len(points) + len(bands)
    left, right, top, bottom = 70, 20, 24 + 16 * legend_rows, 48
    plot_w, plot_h = width - left - right, height - top - bottom

    def px(x):
        return left + (x - xt[0]) / (xt[-1] - xt[0]) * plot_w

    def py(y):
        return top + (y - yt[0]) / (yt[-1] - yt[0]) * plot_h

    parts = _frame(width, height, title)
    for band in bands:
        y0, y1 = sorted((py(band["low"]), py(band["high"])))
        parts.append(
            f'<rect x="{_n(left)}" y="{_n(y0)}" width="{_n(plot_w)}" height="{_n(max(y1 - y0, 1))}" '
            f'fill="{band.get("colour", "#e9eef0")}"/>'
        )
    for tick in xt:
        x = px(tick)
        parts.append(
            f'<line x1="{_n(x)}" y1="{_n(top)}" x2="{_n(x)}" y2="{_n(top + plot_h)}" stroke="{GRID}"/>'
        )
        parts.append(
            f'<text x="{_n(x)}" y="{_n(top + plot_h + 16)}" font-size="11" text-anchor="middle" fill="{MUTED}" {FONT}>{_label(tick)}</text>'
        )
    for tick in yt:
        y = py(tick)
        parts.append(
            f'<line x1="{_n(left)}" y1="{_n(y)}" x2="{_n(left + plot_w)}" y2="{_n(y)}" stroke="{GRID}"/>'
        )
        parts.append(
            f'<text x="{_n(left - 6)}" y="{_n(y + 4)}" font-size="11" text-anchor="end" fill="{MUTED}" {FONT}>{_label(tick)}</text>'
        )
    parts.append(
        f'<rect x="{_n(left)}" y="{_n(top)}" width="{_n(plot_w)}" height="{_n(plot_h)}" fill="none" stroke="#b9c3c8"/>'
    )
    legend = []
    for index, s in enumerate(series):
        colour = s.get("colour") or PALETTE[index % len(PALETTE)]
        coordinates = [
            f"{_n(px(x))},{_n(py(y))}"
            for x, y in zip(s["x"], s["y"])
            if x is not None and y is not None and math.isfinite(x) and math.isfinite(y)
        ]
        dash = s.get("dash")
        dashes = f' stroke-dasharray="{dash}"' if dash else ""
        if coordinates:
            parts.append(
                f'<polyline fill="none" stroke="{colour}" stroke-width="{s.get("width", 2)}"{dashes} points="{" ".join(coordinates)}"/>'
            )
        legend.append((s["name"], colour, dash))
    for index, p in enumerate(points):
        colour = p.get("colour") or PALETTE[(len(series) + index) % len(PALETTE)]
        for x, y in zip(p["x"], p["y"]):
            if x is None or y is None:
                continue
            parts.append(
                f'<circle cx="{_n(px(x))}" cy="{_n(py(y))}" r="2.6" fill="{colour}" fill-opacity="0.85"/>'
            )
        legend.append((p["name"], colour, "point"))
    for band in bands:
        legend.append((band["name"], band.get("colour", "#e9eef0"), None))
    _legend(parts, legend, left, 14)
    parts.append(
        f'<text x="{_n(left + plot_w / 2)}" y="{_n(height - 10)}" font-size="12" text-anchor="middle" fill="{INK}" {FONT}>{escape(x_label)}</text>'
    )
    parts.append(
        f'<text transform="translate(16 {_n(top + plot_h / 2)}) rotate(-90)" font-size="12" text-anchor="middle" fill="{INK}" {FONT}>{escape(y_label)}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def interval_chart(rows, x_label, title, width=640, row_height=26, label_width=250):
    """Horizontal intervals with optional markers.

    rows: dicts with label, low, high (either may be None), optional
    marker (value), marker_label, colour and status ('ok' or 'fail').
    reference: optional dict with value and label drawn as a vertical line
    per row via row['reference'].
    """
    values = []
    for row in rows:
        for key in ("low", "high", "marker", "reference"):
            value = row.get(key)
            if value is not None and math.isfinite(value):
                values.append(value)
    height = 50 + row_height * max(len(rows), 1) + 30
    parts = _frame(width, height, title)
    if not values:
        parts.append(
            f'<text x="20" y="40" font-size="13" fill="{MUTED}" {FONT}>No values to plot.</text></svg>'
        )
        return "".join(parts)
    ticks = nice_ticks(min(values), max(values))
    left, right, top = label_width, 24, 20
    plot_w = width - left - right
    plot_h = row_height * len(rows)

    def px(x):
        return left + (x - ticks[0]) / (ticks[-1] - ticks[0]) * plot_w

    for tick in ticks:
        x = px(tick)
        parts.append(
            f'<line x1="{_n(x)}" y1="{_n(top)}" x2="{_n(x)}" y2="{_n(top + plot_h)}" stroke="{GRID}"/>'
        )
        parts.append(
            f'<text x="{_n(x)}" y="{_n(top + plot_h + 16)}" font-size="11" text-anchor="middle" fill="{MUTED}" {FONT}>{_label(tick)}</text>'
        )
    fits = max(int((left - 16) / 6.0), 8)
    for index, row in enumerate(rows):
        y = top + index * row_height + row_height / 2
        if row.get("heading"):
            parts.append(
                f'<text x="8" y="{_n(y + 4)}" font-size="12" font-weight="600" fill="{INK}" {FONT}>{escape(row["label"])}</text>'
            )
            continue
        colour = row.get("colour") or ("#b4483a" if row.get("status") == "fail" else PALETTE[0])
        label = row["label"]
        # Shorten long labels in the middle so their distinguishing ends stay.
        head = (fits - 1) // 2
        shown = label if len(label) <= fits else label[:head].rstrip() + "…" + label[len(label) - (fits - 1 - head):].lstrip()
        parts.append(
            f'<text x="{_n(left - 8)}" y="{_n(y + 4)}" font-size="11" text-anchor="end" fill="{INK}" {FONT}><title>{escape(label)}</title>{escape(shown)}</text>'
        )
        low, high = row.get("low"), row.get("high")
        if low is not None and high is not None:
            x0, x1 = sorted((px(low), px(high)))
            parts.append(
                f'<rect x="{_n(x0)}" y="{_n(y - 6)}" width="{_n(max(x1 - x0, 1.5))}" height="12" rx="2" fill="{colour}" fill-opacity="0.35" stroke="{colour}"/>'
            )
        elif low is not None or high is not None:
            value = low if low is not None else high
            parts.append(
                f'<line x1="{_n(px(value))}" y1="{_n(y - 7)}" x2="{_n(px(value))}" y2="{_n(y + 7)}" stroke="{colour}" stroke-width="2"/>'
            )
        reference = row.get("reference")
        if reference is not None:
            parts.append(
                f'<line x1="{_n(px(reference))}" y1="{_n(y - 10)}" x2="{_n(px(reference))}" y2="{_n(y + 10)}" stroke="#b4483a" stroke-width="2"/>'
            )
        marker = row.get("marker")
        if marker is not None:
            parts.append(
                f'<circle cx="{_n(px(marker))}" cy="{_n(y)}" r="4" fill="{INK}"/>'
            )
    parts.append(
        f'<text x="{_n(left + plot_w / 2)}" y="{_n(height - 8)}" font-size="12" text-anchor="middle" fill="{INK}" {FONT}>{escape(x_label)}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)
