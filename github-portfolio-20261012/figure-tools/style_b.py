"""Publication figure style module.

One visual identity for every figure of a manuscript (palette, line styles,
markers, font), set once here and imported by each figure script:

  font        : Century Gothic (embedded, pdf.fonttype 42)
  baselines   : teal family, SOLID lines, filled round/polygon markers
                (single-hue OKLCH ramp, hue 200, dark -> light)
  foundation  : purple family, DASHED lines, open triangle markers
                (OKLCH ramp, hue 320)
  references  : neutral grays / near-black (persistence, hour-of-day mean,
                observed traces, climatology-fill-as-predictor)
  R2 < 0 zone : cool light gray shading

Group identity is triple-encoded (hue family + linestyle + marker family) so
the figures survive grayscale printing and color-vision deficiency; the
cross-family palette was checked with the OKLab CVD validator (worst
cross-family adjacent pair deltaE 6.1 under protanopia, legal with the
secondary encodings above; within-family members are separated by lightness
steps >= 0.07 L plus distinct markers and the legend).

Determinism: SOURCE_DATE_EPOCH is pinned before matplotlib import so the PDF
CreationDate is constant; nothing is sampled anywhere in the render scripts.
"""
import os

os.environ.setdefault("SOURCE_DATE_EPOCH", "0")  # constant PDF CreationDate

import matplotlib

matplotlib.use("Agg")

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt

# ---------------------------------------------------------------- fonts
# Century Gothic is looked up in $FIG_FONT_DIR first, then in the usual Windows / WSL / macOS font folders.
# If it is not installed, the module falls back to matplotlib's DejaVu Sans with a warning instead of failing.
import warnings

_FONT_NAMES = ["GOTHIC.TTF", "GOTHICB.TTF", "GOTHICI.TTF", "GOTHICBI.TTF"]
_FONT_DIRS = [os.environ.get("FIG_FONT_DIR", ""), "C:/Windows/Fonts", "/mnt/c/Windows/Fonts", "/Library/Fonts",
              os.path.expanduser("~/.fonts")]
FONT_DIR = next((d for d in _FONT_DIRS if d and all(os.path.exists(os.path.join(d, n)) for n in _FONT_NAMES)), None)
if FONT_DIR:
    for _n in _FONT_NAMES:
        fm.fontManager.addfont(os.path.join(FONT_DIR, _n))
    FONT_FAMILY = "Century Gothic"
else:
    warnings.warn("Century Gothic not found (set FIG_FONT_DIR); falling back to DejaVu Sans")
    FONT_FAMILY = "DejaVu Sans"

plt.rcParams.update({
    "font.family": FONT_FAMILY,
    "mathtext.fontset": "custom",
    "mathtext.rm": FONT_FAMILY,
    "mathtext.it": f"{FONT_FAMILY}:italic",
    "mathtext.bf": f"{FONT_FAMILY}:bold",
    "mathtext.cal": FONT_FAMILY,
    "mathtext.default": "rm",
    "pdf.fonttype": 42,          # embed TrueType glyphs in the PDF
    "ps.fonttype": 42,
    "font.size": 8.5,
    "axes.labelsize": 9.0,
    "axes.titlesize": 9.0,
    "xtick.labelsize": 8.0,
    "ytick.labelsize": 8.0,
    "legend.fontsize": 7.2,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.22,
    "grid.linewidth": 0.3,
    "axes.axisbelow": True,
    "axes.linewidth": 0.7,
    "savefig.dpi": 300,
})

# ---------------------------------------------------------------- palette
# Trained baselines: teal ramp (dark -> light), solid
BASE_COLOR = {
    "lstm":        "#004f54",
    "gru":         "#006368",
    "cnn":         "#10777c",
    "transformer": "#308c91",
    "dlinear":     "#49a2a7",
}
BASE_MARKER = {"lstm": "o", "gru": "s", "cnn": "D", "transformer": "p", "dlinear": "h"}

# Foundation (zero-shot): purple ramp, dashed, open triangles
FOUND_COLOR = {
    "chronos_2":    "#712482",
    "moirai_large": "#9346a4",
    "granite_ttm":  "#b668c8",
}
FOUND_MARKER = {"chronos_2": "^", "moirai_large": "v", "granite_ttm": "<"}
FOUND_DASH = (0, (4, 1.6))

# Neutral references
REF_GRAY = "#5c5c5c"     # persistence
REF_BLACK = "#1a1a1a"    # hour-of-day mean
OBS_GRAY = "#8a8a8a"     # observed CO2 traces
FILL_GRAY = "#4a4a4a"    # climatology fill as a predictor (F3)
NEG_SHADE = "#eef1f2"    # R2 < 0 zone (cool light gray)
NEG_TEXT = "#6b7a80"

# F4 bar shades (baseline-family teal for the emphasised group, grays otherwise)
BAR_TEAL = {5: "#49a2a7", 25: "#10777c", 75: "#004f54"}
BAR_GRAY = {5: "#c9c9c9", 25: "#9a9a9a", 75: "#6b6b6b"}

DISPLAY = {
    "lstm": "LSTM", "gru": "GRU", "cnn": "CNN",
    "transformer": "Transformer", "dlinear": "DLinear",
    "persistence": "persistence",
    "hour_climatology": "hour-of-day mean",
    "chronos_2": "Chronos-2",
    "moirai_large": "Moirai-1.1-R-large",
    "granite_ttm": "Granite-TTM-r2",
}

# ---------------------------------------------------------------- horizons
HORIZONS = ["15min", "30min", "1h", "3h", "6h", "12h", "24h"]      # evaluated
HORIZONS_SIX = HORIZONS[:6]      # the six reported horizons (24 h is evaluated but not reported)
HZ_MINUTES = {"15min": 15, "30min": 30, "1h": 60, "3h": 180, "6h": 360, "12h": 720, "24h": 1440}
HZ_TICKLABEL = {"15min": "15 min", "30min": "30 min", "1h": "1 h", "3h": "3 h",
                "6h": "6 h", "12h": "12 h", "24h": "24 h"}

# ---------------------------------------------------------------- sizes / weights
LW_SERIES = 1.3
LW_REF = 1.0
MS_BASE = 4.3
MS_FOUND = 4.8
FIGSIZE_TWO_PANEL = (4.6, 5.6)   # for 0.6\textwidth inclusion
FIGSIZE_TRACE = (7.2, 3.1)       # for full \textwidth inclusion
FIGSIZE_BARS = (5.6, 3.4)


# ------------------------------------------------- section 3.2 additions
# Okabe-Ito reference set (kept for reuse; no red/vermilion anywhere)
OKABE = {"blue": "#0072B2", "orange": "#E69F00", "sky": "#56B4E9",
         "green": "#009E73", "yellow": "#F0E442", "purple": "#CC79A7",
         "black": "#000000"}
# CO2-substitute colors, same family grammar (purple / teal / gray)
SUB_COLOR = {"climatology": "#9346a4", "constant": "#308c91",
             "zeros": "#6b6b6b"}

# train / validation / test background tints + boundary style, identical in
# every figure that shows the chronological split
SPLIT_TINT = {"train": "#f7f7f7", "val": "#ececec", "test": "#e1e1e1"}
SPLIT_EDGE = "#999999"


def shade_splits(ax, x0, b1, b2, x1):
    """Split shading: three tints + thin boundary lines at b1/b2."""
    ax.axvspan(x0, b1, color=SPLIT_TINT["train"], zorder=0, linewidth=0)
    ax.axvspan(b1, b2, color=SPLIT_TINT["val"], zorder=0, linewidth=0)
    ax.axvspan(b2, x1, color=SPLIT_TINT["test"], zorder=0, linewidth=0)
    for b in (b1, b2):
        ax.axvline(b, color=SPLIT_EDGE, linewidth=0.5, zorder=1)


def lighten(hexcolor, f=0.65):
    """Blend a hex color toward white by fraction f."""
    r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(c + (255 - c) * f) for c in (r, g, b))


def save_dual(fig, stem, tight=True, dpi=350):
    """Vector PDF + high-dpi PNG preview with the same stem.

    The PDF is the deliverable (vector, resolution-independent); the PNG is a
    preview only. tight=False keeps the exact canvas size (use when
    print-scale font sizes must hold).
    """
    bbox = "tight" if tight else None
    fig.savefig(f"{stem}.pdf", bbox_inches=bbox)
    fig.savefig(f"{stem}.png", dpi=dpi, bbox_inches=bbox)


def log_horizon_axis(ax, label=True, horizons=None):
    """Log-spaced horizon x-axis with the canonical ticks.

    Default: the seven evaluated horizons (existing renders unchanged); the 24 h-cut figures
    pass HORIZONS_SIX.
    """
    hz = list(horizons) if horizons is not None else HORIZONS
    ax.set_xscale("log")
    xs = [HZ_MINUTES[h] for h in hz]
    ax.set_xticks(xs)
    ax.set_xticklabels([HZ_TICKLABEL[h] for h in hz])
    ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax.set_xlim(xs[0] * 0.85, xs[-1] * 1.18)
    if label:
        ax.set_xlabel("Prediction horizon")


def shade_negative_r2(ax, lo, label="worse than mean"):
    """Shade the R2 < 0 region and draw the zero reference line.

    `label` is the tag in the shaded corner, e.g. r"$\mathit{R}^2 < 0$".
    """
    ax.axhspan(lo, 0, color=NEG_SHADE, zorder=0)
    ax.axhline(0, color="#666666", linewidth=0.6, linestyle=(0, (2, 2)), zorder=1)
    ax.text(0.985, 0.03, label, transform=ax.transAxes,
            ha="right", va="bottom", fontsize=6.8, style="italic", color=NEG_TEXT,
            bbox=dict(facecolor="white", edgecolor="#d3dadd", linewidth=0.4,
                      boxstyle="round,pad=0.22", alpha=0.92), zorder=10)


def plot_baseline(ax, x, y, model):
    return ax.plot(x, y, color=BASE_COLOR[model], linestyle="-",
                   marker=BASE_MARKER[model], markersize=MS_BASE,
                   linewidth=LW_SERIES, markeredgewidth=0.6,
                   markeredgecolor="#222222", label=DISPLAY[model], zorder=4)


def plot_foundation(ax, x, y, model):
    return ax.plot(x, y, color=FOUND_COLOR[model], linestyle=FOUND_DASH,
                   marker=FOUND_MARKER[model], markersize=MS_FOUND,
                   linewidth=LW_SERIES, markerfacecolor="white",
                   markeredgewidth=0.9, markeredgecolor=FOUND_COLOR[model],
                   label=DISPLAY[model], zorder=5)


# ------------------------------------------------- time-window figures (prediction task, input settings)
CO2_INPUT = "#004f54"         # observed CO2 lookback drawn as the model input
CO2_TARGET = "#49a2a7"        # observed future drawn as the forecast targets (the lighter tint of CO2_INPUT)
LOOKBACK_SHADE = "#edf3f3"    # band over the lookback span (= lighten(CO2_INPUT, 0.93))
NOW_LINE = dict(color="#555555", linewidth=0.8, linestyle=(0, (4, 2.4)))                 # issue-time ("now") line
MASK_HATCH = dict(facecolor="white", alpha=0.5, edgecolor="#aaaaaa", hatch="//", linewidth=0.6)  # masked span
SUB_LS = {"climatology": (0, (4, 2)), "constant": (0, (5, 1.6, 1, 1.6)), "zeros": (0, (1.2, 1.6))}  # substitute line styles (dashed / dash-dot / dotted: distinct in grayscale)
LW_WINDOW = 1.0               # CO2 traces in the window figures
LW_ROW = 0.5                  # covariate sparkline rows
ROW_GRAY = "#8f8f8f"          # covariate sparkline rows
HZ_MARK = dict(marker="o", markersize=2.6, color="#333333", linestyle="none")              # horizon markers
HZ_STEM = dict(color="#555555", linewidth=0.7)                                             # short tick under a marker
LEADER = dict(color="#aaaaaa", linewidth=0.5)                                              # label leader lines
BRACKET = dict(color="#777777", linewidth=0.9)                                             # group bracket
TEXT_DARK, TEXT_MID, TEXT_LIGHT = "#333333", "#555555", "#999999"                          # annotation text shades
FS_LETTER, FS_LABEL, FS_ANNOT = 9, 8, 7                                                    # panel letter (bold) / axis label / annotations and ticks
TIME_FMT, TIME_TICK_HOURS = "%b %-d %H:%M", [0, 12]                                        # time axis of the window figures
FIGSIZE_FULL = (7.0, 4.5)     # full-width canvas shared by the time-window figures
HIDDEN_GRAY = "#c4c4c4"       # observed CO2 hidden from the model; lighter than OBS_GRAY so that its
                              # grayscale level (196 of 255) separates from CO2_TARGET's (136) by more than 40

# ------------------------------------------------- prompt-card and flow-diagram figures
MONO_FONT_FILES = [os.path.join(FONT_DIR, n) for n in ("consola.ttf", "consolab.ttf")] if FONT_DIR else []   # Consolas regular / bold for prompt text
FLOW_BOX = dict(boxstyle="round,pad=0.35", facecolor="#e9e9e9", edgecolor="#666666", linewidth=0.8)   # flow-strip boxes
FLOW_ARROW = dict(arrowstyle="-|>", color="#555555", linewidth=0.8, shrinkA=0, shrinkB=0)             # flow-strip arrows
CARD_FACE = dict(facecolor="#fbfbfb", edgecolor="#c9c9c9", linewidth=0.6)                             # prompt card background
CARD_HEAD = dict(facecolor=lighten("#308c91", 0.86), edgecolor="#b9cdd0", linewidth=0.6)              # prompt card title bar
TINT_INPUT = lighten(CO2_INPUT, 0.88)        # tint behind CO2 values the model receives (grayscale-safe highlight)
TINT_SUB = lighten(SUB_COLOR["climatology"], 0.62)   # tint behind the climatology substitute

# ------------------------------------------------- protocol-diagram figures
EXPOSURE_PRETRAINED = "#9346a4"   # pretrained-model exposure (purple: open = zero-shot, filled = trained on local windows)
EXPOSURE_TRAINED = "#10777c"      # reference models trained on local data (teal)
DATA_WINDOW = "#333333"           # data windows (ticks), neutral dark gray
MS_RUN = 4.5                      # run markers in the protocol diagram
FLOW_ARROW_HEAD = dict(length=4.0, half_width=2.0)   # drawn arrow heads: the "-|>" head of FLOW_ARROW at mutation scale 10 (length 0.4, half width 0.2)
# pooled-bar fill and the open run marker's edge
POOL_FILL = lighten(DATA_WINDOW, 0.68)   # #bdbdbd: uniform light tint of the tick colour for the pooled bar
MS_RUN_EDGE = 0.9                        # edge width of the open (zero-shot) run marker; its path is shrunk so the outer diameter stays MS_RUN
