"""Automated checks for publication figures: text collisions, sizes, banned terms, grayscale.

collisions(): every visible text artist's bounding box (tick labels included) against every other text box and against
every data line of every axes (ax.lines: traces, leaders, stems, the "now" line, markers), after densifying each line to
0.4 pt steps and padding by its half line width or marker radius; a text box is also checked against the figure canvas.
"""
from __future__ import annotations

import math
import re

import numpy as np
from matplotlib.text import Text

BANNED = ["anchor", "skill", "beats", "pipeline", "foundation model", "deep baselines", "trained baselines",
          "building data", "univariate", "multivariate", ".py", "::", "build_", "serialize"]


def _k(fig):
    return 72.0 / fig.dpi


def texts(fig):
    return [t for t in fig.findobj(Text) if t.get_visible() and t.get_text().strip()]


def text_boxes(fig, rend):
    k = _k(fig)
    out = []
    for t in texts(fig):
        bb = t.get_window_extent(rend)
        if bb.width <= 0 or bb.height <= 0:
            continue
        out.append((t, np.array([bb.x0, bb.y0, bb.x1, bb.y1]) * k))
    return out


def line_samples(fig, step_pt=0.4):
    k = _k(fig)
    out = []
    for ai, ax in enumerate(fig.axes):
        for ln in ax.lines:
            if not ln.get_visible():
                continue
            xy = ln.get_xydata()
            if len(xy) == 0:
                continue
            disp = ln.get_transform().transform(xy) * k
            pts = [disp[0]]
            for a, b in zip(disp[:-1], disp[1:]):
                d = math.hypot(*(b - a))
                n = max(1, int(math.ceil(d / step_pt)))
                pts.extend(a + (b - a) * (np.arange(1, n + 1)[:, None] / n))
            pts = np.asarray(pts)
            has_line = ln.get_linestyle() not in ("None", "none", "", " ") and ln.get_linewidth() > 0
            has_marker = ln.get_marker() not in (None, "None", "none", "", " ")
            pad = (ln.get_linewidth() / 2 if has_line else 0.0) + (ln.get_markersize() / 2 if has_marker else 0.0)
            out.append((f"axes{ai}:{ln.get_label() or ln.get_color()}", pts, pad))
    return out


def collisions(fig, rend, pad_pt=0.5):
    boxes = text_boxes(fig, rend)
    lines = line_samples(fig)
    k = _k(fig)
    W, H = fig.bbox.width * k, fig.bbox.height * k
    tt = []
    for i in range(len(boxes)):
        a = boxes[i][1]
        ga = getattr(boxes[i][0], "_group", None)          # segments of one typeset line share a group and may abut
        for j in range(i + 1, len(boxes)):
            b = boxes[j][1]
            if ga is not None and ga == getattr(boxes[j][0], "_group", None):
                continue
            if a[0] - pad_pt < b[2] and b[0] - pad_pt < a[2] and a[1] - pad_pt < b[3] and b[1] - pad_pt < a[3]:
                tt.append((boxes[i][0].get_text(), boxes[j][0].get_text()))
    tl, min_clear, min_pair = [], math.inf, None
    for t, bb in boxes:
        for name, pts, lpad in lines:
            dx = np.maximum(np.maximum(bb[0] - pts[:, 0], pts[:, 0] - bb[2]), 0.0)
            dy = np.maximum(np.maximum(bb[1] - pts[:, 1], pts[:, 1] - bb[3]), 0.0)
            d = np.hypot(dx, dy) - lpad
            dm = float(d.min())
            if dm < min_clear:
                min_clear, min_pair = dm, (t.get_text(), name)
            if dm < pad_pt:
                tl.append((t.get_text(), name, round(dm, 2)))
    clipped = [t.get_text() for t, bb in boxes if bb[0] < 0 or bb[1] < 0 or bb[2] > W or bb[3] > H]
    return dict(n_texts=len(boxes), n_lines=len(lines), pad_pt=pad_pt, text_text=tt, text_line=tl, clipped=clipped,
                min_text_line_clearance_pt=round(min_clear, 2), min_clearance_pair=min_pair)


def sizes(fig):
    """Smallest text size and smallest drawn line width (data lines, spines, tick marks, patch edges)."""
    fs = min(t.get_fontsize() for t in texts(fig))
    lws = []
    for ax in fig.axes:
        lws += [ln.get_linewidth() for ln in ax.lines if ln.get_visible()
                and ln.get_linestyle() not in ("None", "none", "", " ")]
        lws += [sp.get_linewidth() for sp in ax.spines.values() if sp.get_visible()]
        for axis in (ax.xaxis, ax.yaxis):
            for tick in axis.get_major_ticks():
                for tl in (tick.tick1line, tick.tick2line):
                    if tl.get_visible() and tick.get_visible():
                        lws.append(tl.get_markeredgewidth())
        lws += [p.get_linewidth() for p in ax.patches if p.get_visible() and p.get_edgecolor()[3] > 0 and p.get_linewidth() > 0]
    return dict(min_fontsize_pt=float(fs), min_linewidth_pt=float(min(lws)), n_lines=len(lws))


def term_sweep(fig, banned=BANNED):
    flat = " ".join(" ".join(t.get_text().split()) for t in texts(fig)).lower()
    return sorted({w for w in banned if w.lower() in flat})


def strings(fig):
    """Every distinct in-figure string (mathtext kept as written)."""
    return sorted({" ".join(t.get_text().split()) for t in texts(fig)})


def luminance(hexcolor):
    """WCAG relative luminance of a hex colour (0 = black, 1 = white)."""
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
    return round(0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b), 3)


def gray_level(hexcolor):
    """Grayscale level 0-255 as PIL's L conversion computes it (0.299 R + 0.587 G + 0.114 B)."""
    r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
    return int(round(0.299 * r + 0.587 * g + 0.114 * b))


def grayscale_png(png_in, png_out):
    from PIL import Image
    Image.open(png_in).convert("L").save(png_out)


def content_margins_pt(png, page_w_pt, page_h_pt):
    """Whitespace margins (pt) around the non-white content of a rendered page."""
    from PIL import Image
    im = np.asarray(Image.open(png).convert("L"))
    ys, xs = np.where(im < 250)
    sx, sy = page_w_pt / im.shape[1], page_h_pt / im.shape[0]
    return dict(left=round(xs.min() * sx, 1), right=round((im.shape[1] - 1 - xs.max()) * sx, 1),
                top=round(ys.min() * sy, 1), bottom=round((im.shape[0] - 1 - ys.max()) * sy, 1))


def pdf_has_images(pdf_path):
    b = open(pdf_path, "rb").read()
    return bool(re.search(rb"/Subtype\s*/Image", b))
