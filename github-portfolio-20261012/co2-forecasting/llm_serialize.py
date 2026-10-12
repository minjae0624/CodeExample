"""Pure serialization / parsing for the zero-shot instruct-LLM CO2 forecaster.

This module implements the text-serialization scheme of the LLM forecasting harness
(serialization, prompt assembly, parser). It is deliberately dependency-light —
``numpy`` + ``re`` + stdlib only, no ``torch`` / ``vllm`` / ``transformers`` — so it
is importable in any env and unit-testable in isolation. The heavy model wrapper
(``llm_forecaster.py``) imports these functions; all backend/decoding lives there.

Pipeline (per 288-step multivariate lookback window):
    raw window (T, C)  --scale_channel-->  per-channel (a, b), CO2 = channel 0
                       --format_number-->  fixed-point (prec decimals), decimal point dropped
                       --serialize_window-> row-stacked text (one CSV row per timestep)
    build_system_prompt + build_user_prompt + build_prompt --> chat messages
    model completion (CO2 column, H values)
                       --parse_completion--> np.ndarray[H] ppm + audit stats

Base (non-instruct) models have no chat template and cannot follow an "output the
CO2 column only" instruction, so they CONTINUE a raw serialized prompt (no roles,
no system text). Two base sub-modes, chosen by channel count:
    * univariate (C == 1): :func:`build_base_prompt` — a raw LLMTime-canonical CO2
      continuation (one comma line ending in a ``,`` cue), read back by
      :func:`parse_completion`.
    * multivariate (C > 1): :func:`build_base_multivariate_prompt` — the FULL
      row-stacked window (LLMTime extended to multivariate, identical to
      ``serialize_window``'s ``history_text``) plus a trailing ``\\n`` cue to emit the
      next ROW, read back column-wise by :func:`parse_base_multivariate_completion`.

Scaling (LLMTime, Gruver et al. 2023) is fit **per window on the 288-step lookback
only** — the horizon is never in ``X`` so there is no future leakage. Only the CO2
channel's ``(a0, b0)`` is needed downstream to un-scale the parsed forecast.

No external service calls.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Defaults (fixed by the experiment design).
# ---------------------------------------------------------------------------
ALPHA: float = 0.95        # LLMTime scale quantile
BETA: float = 0.30         # LLMTime offset fraction
PREC: int = 3              # fixed decimals; decimal point is dropped on format (finer quant floor)
FLAT_GUARD: float = 1e-6   # below this, treat channel as flat and set a = 1.0
CLIP_MIN_PPM: float = 300.0
CLIP_MAX_PPM: float = 5000.0
CO2_INDEX: int = 0         # CO2 is channel / column 0 everywhere in this project

# Regex that grabs each integer run (optional leading minus) in output order.
_NUM_RE = re.compile(r"-?\d+")

# System prompt. Fixed text; states the "numbers only" output contract.
DEFAULT_SYSTEM = (
    "You are a precise time-series forecasting engine. You receive a multivariate "
    "sensor window as rows of comma-separated integers, one row per 5-minute step, "
    "columns in the stated order. Each integer is a fixed-point value with an implied "
    "fixed decimal scaling, already normalized per column. Predict future values of the "
    "CO2 column only. Output ONLY the requested integers, comma-separated, on a single "
    "line. No words, no units, no explanation."
)

# Tiny format-only exemplar (behind ``use_exemplar``). Locks output shape,
# not content: a 3-row, 3-column toy history -> 2 continued CO2 integers.
_EXEMPLAR = (
    "Example (format only, unrelated values):\n"
    "History:\n"
    "012,044,061\n"
    "015,043,060\n"
    "019,041,059\n"
    "Task: output exactly H = 2 comma-separated integers = the col-1 values.\n"
    "Output:\n"
    "022,026\n"
    "---\n"
)


# ---------------------------------------------------------------------------
# Per-channel scaling (LLMTime), fit on the lookback only.
# ---------------------------------------------------------------------------
def scale_channel(
    values: np.ndarray,
    alpha: float = ALPHA,
    beta: float = BETA,
    flat_guard: float = FLAT_GUARD,
) -> tuple[float, float]:
    """Fit the LLMTime affine scaling ``(a, b)`` for one channel.

    Forward transform used by the serializer is ``scaled = (v - b) / a`` so that
    ``scaled`` lands in roughly ``[0, ~1.05]``. The inverse (used by the parser)
    is ``v = scaled * a + b``.

    Parameters
    ----------
    values : np.ndarray, shape (T,)
        Raw channel values over the lookback (no NaNs — ``make_windows`` drops
        NaN windows upstream).
    alpha, beta : float
        LLMTime quantile / offset fraction (0.95 / 0.30 by default).
    flat_guard : float
        If the fitted scale ``a`` is <= this (flat / near-constant channel), it
        is replaced by 1.0 to avoid divide-by-zero; the channel then serializes
        to all-zeros and un-scales back to its constant value ``b == min``.

    Returns
    -------
    (a, b) : tuple[float, float]
    """
    v = np.asarray(values, dtype=np.float64).ravel()
    mn = float(v.min())
    mx = float(v.max())
    b = mn - beta * (mx - mn)
    shifted = v - b
    a = float(np.quantile(shifted, alpha))
    if a <= flat_guard:
        a = 1.0
    return a, b


# ---------------------------------------------------------------------------
# Number format (fixed-point, decimal point dropped, BPE-safe).
# ---------------------------------------------------------------------------
def format_number(scaled: float, prec: int = PREC, space_digits: bool = False) -> str:
    """Format a scaled value as a fixed-point integer string (decimal dropped).

    At ``prec=3``: ``0.87 -> "870"``, ``1.05 -> "1050"``, ``0.00 -> "000"``. Because
    ``prec`` is fixed, the last ``prec`` characters are always the fraction, so the
    encoding is unambiguous regardless of the integer part's width (``12.34 -> "12340"``).
    Negatives keep a leading ``-`` (they do not arise for our data since scaled
    values are >= 0, but the parser tolerates them).

    ``space_digits`` (ablation knob, off by default) inserts a space between every
    digit for tokenizers that do not treat digits atomically. NOTE: spacing is for
    the **input** serialization only; :func:`parse_completion` assumes un-spaced
    output (instruct models emit compact "comma-separated integers").
    """
    s = f"{scaled:.{prec}f}".replace(".", "")
    if space_digits:
        neg = s.startswith("-")
        core = s[1:] if neg else s
        core = " ".join(core)
        s = ("- " + core) if neg else core
    return s


def format_series(
    values: np.ndarray,
    a: float,
    b: float,
    prec: int = PREC,
    space_digits: bool = False,
    sep: str = ",",
) -> str:
    """Scale + format a 1-D series into a single ``sep``-joined line.

    This is the exact string a well-behaved model is asked to emit for the CO2
    column, and is the encoder half of the round-trip check in the self-test.
    """
    v = np.asarray(values, dtype=np.float64).ravel()
    scaled = (v - b) / a
    return sep.join(format_number(float(x), prec=prec, space_digits=space_digits) for x in scaled)


# ---------------------------------------------------------------------------
# Layout — LLMPred row-stacking of the full multivariate window.
# ---------------------------------------------------------------------------
def serialize_window(
    window: np.ndarray,
    prec: int = PREC,
    alpha: float = ALPHA,
    beta: float = BETA,
    space_digits: bool = False,
    co2_index: int = CO2_INDEX,
) -> tuple[str, float, float]:
    """Row-stack a full multivariate lookback window into text.

    One comma-separated row per timestep, rows separated by ``\\n``; column order
    is preserved from ``window``'s channel axis (CO2 must be column ``co2_index``,
    which is 0 for this project — it becomes "column 1" in the 1-indexed prompt).

    Parameters
    ----------
    window : np.ndarray, shape (T, C)
        Raw lookback values (ppm / native units), CO2 at column ``co2_index``.

    Returns
    -------
    history_text : str
        The ``T``-row, ``C``-column serialized history (no trailing newline).
    a0, b0 : float
        The CO2 channel's scaling, needed by :func:`parse_completion` to un-scale
        the model's CO2 forecast.
    """
    w = np.asarray(window, dtype=np.float64)
    if w.ndim != 2:
        raise ValueError(f"serialize_window expects a 2-D (T, C) window, got shape {w.shape}")
    T, C = w.shape

    # Fit per-channel scaling on the lookback and pre-format every cell.
    scales = [scale_channel(w[:, c], alpha=alpha, beta=beta) for c in range(C)]
    # (T, C) matrix of formatted strings.
    formatted = [
        [
            format_number((w[t, c] - scales[c][1]) / scales[c][0], prec=prec, space_digits=space_digits)
            for c in range(C)
        ]
        for t in range(T)
    ]
    history_text = "\n".join(",".join(row) for row in formatted)

    a0, b0 = scales[co2_index]
    return history_text, a0, b0


# ---------------------------------------------------------------------------
# Prompt assembly.
# ---------------------------------------------------------------------------
def build_system_prompt() -> str:
    """Return the fixed system prompt."""
    return DEFAULT_SYSTEM


def build_user_prompt(
    columns: list[str] | tuple[str, ...],
    history_text: str,
    n_history: int,
    horizon: int,
    co2_index: int = CO2_INDEX,
    use_exemplar: bool = False,
) -> str:
    """Build the user prompt, stating H (``horizon``) explicitly.

    Parameters
    ----------
    columns : sequence of str
        Channel names in column order (e.g. the 11-channel tuple). CO2 must sit at
        ``co2_index``.
    history_text : str
        Output of :func:`serialize_window`.
    n_history : int
        Number of history rows (== lookback steps, typically 288).
    horizon : int
        H — number of future CO2 integers requested.
    use_exemplar : bool
        Prepend the tiny format-only exemplar (off by default).
    """
    col_str = ",".join(columns)
    col_num = co2_index + 1  # 1-indexed for the human-readable prompt
    head = _EXEMPLAR if use_exemplar else ""
    return (
        f"{head}"
        f"Columns (in order): {col_str}\n"
        f"There are {n_history} history rows.\n"
        f"History:\n"
        f"{history_text}\n"
        f"Task: output exactly H = {horizon} comma-separated integers = the "
        f"{columns[co2_index]} (column {col_num}) values for the next {horizon} rows. "
        f"Output only those {horizon} integers."
    )


def build_prompt(system: str, user: str) -> list[dict[str, str]]:
    """Assemble the chat-messages list from a system and user string.

    Returns the standard ``[{"role": "system", ...}, {"role": "user", ...}]`` that
    ``tokenizer.apply_chat_template`` / vLLM's chat API consume.
    """
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# Raw-completion prompt for BASE (non-instruct) models.
# ---------------------------------------------------------------------------
def build_base_prompt(
    window: np.ndarray,
    prec: int = PREC,
    alpha: float = ALPHA,
    beta: float = BETA,
    space_digits: bool = False,
    co2_index: int = CO2_INDEX,
) -> tuple[str, float, float]:
    """LLMTime-canonical raw continuation prompt for base (non-instruct) models.

    A base LM has no chat template and cannot follow an "output the CO2 column
    only" instruction, so it is fed the plain scaled CO2 series and asked to
    *continue* it — the classic LLMTime setup (Gruver et al. 2023). No system
    prompt, no roles, no words: just the per-window-scaled CO2 history as one
    comma-separated line ending in the separator, which cues the model to emit
    the next value(s). Base models are UNIVARIATE — only the CO2 channel
    (``co2_index``) is serialized; any other channels in ``window`` are ignored.

    Parameters
    ----------
    window : np.ndarray, shape (T, C)
        Raw lookback values; CO2 at column ``co2_index``. For the base-model
        path ``C`` is 1 (the wrapper forces ``input_cols=["co2"]``), but any
        ``C >= 1`` is accepted and only the CO2 column is used.

    Returns
    -------
    prompt_text : str
        The comma-separated scaled CO2 history, ending in a trailing ``,`` cue.
    a0, b0 : float
        The CO2 channel's scaling, needed by :func:`parse_completion` to un-scale
        the model's continuation.
    """
    w = np.asarray(window, dtype=np.float64)
    if w.ndim != 2:
        raise ValueError(f"build_base_prompt expects a 2-D (T, C) window, got shape {w.shape}")
    co2 = w[:, co2_index]
    a0, b0 = scale_channel(co2, alpha=alpha, beta=beta)
    hist = format_series(co2, a0, b0, prec=prec, space_digits=space_digits, sep=",")
    return hist + ",", a0, b0


def build_base_multivariate_prompt(
    window: np.ndarray,
    prec: int = PREC,
    alpha: float = ALPHA,
    beta: float = BETA,
    space_digits: bool = False,
    co2_index: int = CO2_INDEX,
) -> tuple[str, float, float]:
    """LLMTime-multivariate raw continuation prompt for BASE (non-instruct) models.

    This is the 4th cell of the 2x2 (base x multivariate): a base LM is fed the
    FULL row-stacked multivariate window — CO2 at ``co2_index``, every other channel
    in its own column, scaled per-channel exactly as :func:`serialize_window` does —
    and asked to *continue the rows*. No chat template, no system prompt, no
    instruction: just the serialized history plus a single trailing ``\\n`` that cues
    the model to emit the next ROW (the multivariate generalization of
    :func:`build_base_prompt`'s trailing ``,`` cue). The continuation is read back
    column-wise by :func:`parse_base_multivariate_completion`, which pulls the CO2
    field (``co2_index``) out of each generated row.

    The serialization is byte-for-byte ``serialize_window``'s ``history_text`` — the
    same per-channel LLMTime scaling, same column order, same fixed-point format — so
    the CO2 scaling ``(a0, b0)`` returned here matches both ``serialize_window`` and a
    direct ``scale_channel(window[:, co2_index])`` fit.

    Parameters
    ----------
    window : np.ndarray, shape (T, C)
        Raw lookback values (ppm / native units), CO2 at column ``co2_index``. For
        this path ``C > 1`` (a ``C == 1`` window should use :func:`build_base_prompt`);
        any ``C >= 1`` is accepted.

    Returns
    -------
    prompt_text : str
        ``serialize_window``'s row-stacked history + a single trailing ``\\n``.
    a0, b0 : float
        The CO2 channel's scaling, needed by
        :func:`parse_base_multivariate_completion` to un-scale the CO2 column.
    """
    history_text, a0, b0 = serialize_window(
        window,
        prec=prec,
        alpha=alpha,
        beta=beta,
        space_digits=space_digits,
        co2_index=co2_index,
    )
    # Trailing newline: the base LM continues the row grid, so cue the NEXT row.
    return history_text + "\n", a0, b0


# ---------------------------------------------------------------------------
# NATIVE covariate prompt (LLM-only arm; NO CO2 channel, NO substitute).
# ---------------------------------------------------------------------------
# The native arm is the one target-conditioned TSFMs cannot run: the model is
# handed ONLY the covariate window (CO2 is neither an input channel nor a
# substitute) and asked to predict CO2 in RAW ppm. The forecast is parsed with
# the IDENTITY transform (a0, b0, prec) = (1.0, 0.0, 0), i.e. the emitted
# integers ARE ppm. This digit variant is the controlled (expected-weak)
# counterpart to the primary NL-native arm (llm_serialize_nl): the covariate
# table is the SAME LLMTime-scaled digit grid ``serialize_window`` produces, but
# the requested OUTPUT is physical ppm (there is no CO2 channel to fit an output
# scale on, so raw ppm is the only well-defined target). Instruct-only — base
# LLMs cannot continue an absent CO2 pattern, so base-native is excluded from the
# grid.
DEFAULT_SYSTEM_NATIVE = (
    "You are a precise indoor-air-quality forecasting engine. You receive a "
    "multivariate window of environmental sensor covariates as rows of comma-"
    "separated integers, one row per 5-minute step, columns in the stated order. "
    "Each integer is a fixed-point value with an implied fixed decimal scaling, "
    "already normalized per column. The CO2 concentration is NOT provided. From "
    "the covariates alone, predict the room's future CO2 concentration in physical "
    "units (ppm). Output ONLY the requested CO2 integers in ppm, comma-separated, "
    "on a single line. No words, no units, no explanation."
)

# Tiny format-only exemplar for the native arm (behind ``use_exemplar``): a
# 3-row, 2-covariate-column toy history -> 2 continued CO2 ppm integers. Locks
# output shape (bare ppm integers), not content; carries NO CO2 input column.
_EXEMPLAR_NATIVE = (
    "Example (format only, unrelated values):\n"
    "Columns (in order): occupancy,supply_air_flow\n"
    "History:\n"
    "012,044\n"
    "015,043\n"
    "019,041\n"
    "Task: from these covariates, output exactly H = 2 comma-separated CO2 "
    "concentration values in ppm.\n"
    "Output:\n"
    "612,634\n"
    "---\n"
)


def build_native_system_prompt() -> str:
    """Return the fixed system prompt for the native covariate arm (predict CO2 ppm)."""
    return DEFAULT_SYSTEM_NATIVE


def build_native_user_prompt(
    columns: list[str] | tuple[str, ...],
    history_text: str,
    n_history: int,
    horizon: int,
    use_exemplar: bool = False,
) -> str:
    """Native-arm user prompt: covariate history + a request for H future CO2 ppm.

    CO2 is NOT one of ``columns`` (the arm excludes it), so the prompt never
    names a CO2 column index — it asks the model to INFER CO2 from the covariates.
    Output is physical ppm, so the matching parse is the identity transform
    (:func:`parse_completion` at ``prec=0``, ``a0=1``, ``b0=0``).
    """
    col_str = ",".join(columns)
    head = _EXEMPLAR_NATIVE if use_exemplar else ""
    return (
        f"{head}"
        f"Columns (in order): {col_str}\n"
        f"There are {n_history} history rows. CO2 is NOT included.\n"
        f"History:\n"
        f"{history_text}\n"
        f"Task: from these covariates alone, output exactly H = {horizon} "
        f"comma-separated CO2 concentration values in ppm for the next {horizon} "
        f"rows. Output only those {horizon} integers."
    )


def build_native_covariate_prompt(
    window: np.ndarray,
    columns: list[str] | tuple[str, ...],
    horizon: int,
    prec: int = PREC,
    alpha: float = ALPHA,
    beta: float = BETA,
    space_digits: bool = False,
    use_exemplar: bool = False,
) -> tuple[list[dict[str, str]], float, float]:
    """Native covariate -> CO2 prompt (digit arm; instruct chat messages).

    Serializes the covariate ``window`` with the SAME per-channel LLMTime scaling
    as :func:`serialize_window` (there is NO CO2 channel; the CO2 scale it returns
    is a covariate's scale, meaningless here, and is DISCARDED), then asks the
    model for H future CO2 values in RAW ppm. Returns ``(messages, 1.0, 0.0)`` —
    the identity CO2 transform — so the caller parses the completion with
    :func:`parse_completion` at ``prec=0`` and the emitted integers ARE ppm.

    ``columns`` must NOT contain ``"co2"`` (covariate-only honesty guard) and must
    match the window's channel count/order.
    """
    w = np.asarray(window, dtype=np.float64)
    if w.ndim != 2:
        raise ValueError(
            f"build_native_covariate_prompt expects a 2-D (T, C) window, got shape {w.shape}"
        )
    cols = list(columns)
    if "co2" in cols:
        raise ValueError(
            f"build_native_covariate_prompt is covariate-only: 'co2' must NOT be in "
            f"columns; got {cols!r}."
        )
    if len(cols) != w.shape[1]:
        raise ValueError(
            f"columns has {len(cols)} names but window has {w.shape[1]} channels"
        )
    # serialize_window scales EVERY channel per-window; every channel here is a
    # covariate (no CO2), so its returned (a0, b0) is a covariate's scale — DISCARDED.
    history_text, _a0_cov, _b0_cov = serialize_window(
        w, prec=prec, alpha=alpha, beta=beta, space_digits=space_digits,
    )
    user = build_native_user_prompt(
        cols, history_text, n_history=int(w.shape[0]), horizon=int(horizon),
        use_exemplar=use_exemplar,
    )
    msgs = build_prompt(build_native_system_prompt(), user)
    return msgs, 1.0, 0.0


# ---------------------------------------------------------------------------
# Parser + failure handling (must never raise).
# ---------------------------------------------------------------------------
def parse_completion(
    text: str,
    horizon: int,
    a0: float,
    b0: float,
    prec: int = PREC,
    clip_min: float = CLIP_MIN_PPM,
    clip_max: float = CLIP_MAX_PPM,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Parse a model completion back to ``H`` CO2 ppm values.

    Steps: regex-extract integer runs in order, un-format (``/10**prec``) and
    un-scale (``* a0 + b0``), clip to plausible ppm, then enforce length ``H`` by
    truncation (too long) or forward-fill of the last value (too short). An empty
    parse (refusal / pure prose / junk) yields an all-NaN array and ``refusal=True``
    so the caller can substitute its persistence / seasonal-naive fallback (the
    fallback needs the CO2 history, which lives in the model wrapper, not here).

    Returns
    -------
    forecast : np.ndarray, shape (H,), float64
        Un-scaled, clipped ppm. All-NaN iff ``refusal``/``failed``.
    stats : dict
        Audit counters for ``train.log`` (never written to metrics.json):
        ``n_ok`` (parsed values actually used), ``n_parsed`` (total extracted),
        ``truncated`` (bool), ``n_dropped`` (int), ``padded`` (bool),
        ``n_padded`` (int), ``n_clipped`` (int), ``refusal`` (bool),
        ``failed`` (bool).
    """
    H = int(horizon)
    stats: dict[str, Any] = {
        "n_ok": 0,
        "n_parsed": 0,
        "truncated": False,
        "n_dropped": 0,
        "padded": False,
        "n_padded": 0,
        "n_clipped": 0,
        "refusal": False,
        "failed": False,
    }

    toks = _NUM_RE.findall(text or "")
    stats["n_parsed"] = len(toks)

    if not toks:  # refusal / junk / empty -> caller applies persistence fallback
        stats["refusal"] = True
        stats["failed"] = True
        return np.full(H, np.nan, dtype=np.float64), stats

    scale = 10.0 ** prec
    raw = np.array([int(t) / scale for t in toks], dtype=np.float64) * a0 + b0

    clipped = np.clip(raw, clip_min, clip_max)
    stats["n_clipped"] = int(np.count_nonzero(clipped != raw))
    raw = clipped

    n = raw.shape[0]
    if n >= H:
        forecast = raw[:H].copy()
        if n > H:
            stats["truncated"] = True
            stats["n_dropped"] = n - H
        stats["n_ok"] = H
    else:  # 0 < n < H -> forward-fill with the last parsed value
        pad = np.full(H - n, raw[-1], dtype=np.float64)
        forecast = np.concatenate([raw, pad])
        stats["padded"] = True
        stats["n_padded"] = H - n
        stats["n_ok"] = n

    return forecast, stats


def parse_base_multivariate_completion(
    text: str,
    horizon: int,
    a0: float,
    b0: float,
    co2_index: int = CO2_INDEX,
    prec: int = PREC,
    clip_min: float = CLIP_MIN_PPM,
    clip_max: float = CLIP_MAX_PPM,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Parse a BASE-MULTIVARIATE row continuation back to ``H`` CO2 ppm values.

    Counterpart to :func:`parse_completion` for the base-multivariate cell. Where the
    univariate parser regex-scans a flat comma list, this one is ROW-AWARE: the model
    continued the row grid, so we split the completion on newlines and, from each
    non-empty row, pull the CO2 field (``co2_index``) — the CO2 forecast lives in one
    column, not in output order. Per field we take the first integer run (tolerant of
    stray whitespace / a residual decimal), un-format (``/10**prec``) and un-scale
    (``* a0 + b0``), then clip to plausible ppm. Length is enforced exactly as in
    :func:`parse_completion`: truncate to ``H`` if more CO2 rows were emitted, or
    forward-fill the last value if fewer. Rows lacking the CO2 column or any integer
    in it are skipped, not counted.

    Never raises: an empty / garbage completion with no readable CO2 field yields an
    all-NaN array and ``refusal=True`` (same contract as :func:`parse_completion`), so
    the caller substitutes its persistence / seasonal-naive fallback.

    Returns
    -------
    forecast : np.ndarray, shape (H,), float64
        Un-scaled, clipped CO2 ppm. All-NaN iff ``refusal``/``failed``.
    stats : dict
        Same shape as :func:`parse_completion`'s stats. ``n_parsed`` counts rows a CO2
        value was read from (not raw integer tokens).
    """
    H = int(horizon)
    stats: dict[str, Any] = {
        "n_ok": 0,
        "n_parsed": 0,
        "truncated": False,
        "n_dropped": 0,
        "padded": False,
        "n_padded": 0,
        "n_clipped": 0,
        "refusal": False,
        "failed": False,
    }

    scale = 10.0 ** prec
    vals: list[float] = []
    for line in (text or "").split("\n"):
        row = line.strip()
        if not row:  # blank line between rows / trailing newline -> skip
            continue
        fields = row.split(",")
        if co2_index < 0 or co2_index >= len(fields):
            continue  # row does not carry the CO2 column -> skip, don't count
        m = _NUM_RE.search(fields[co2_index])
        if m is None:
            continue  # CO2 field has no integer -> skip, don't count
        vals.append(int(m.group()) / scale * a0 + b0)

    stats["n_parsed"] = len(vals)

    if not vals:  # refusal / junk / no readable CO2 column -> caller does fallback
        stats["refusal"] = True
        stats["failed"] = True
        return np.full(H, np.nan, dtype=np.float64), stats

    raw = np.asarray(vals, dtype=np.float64)
    clipped = np.clip(raw, clip_min, clip_max)
    stats["n_clipped"] = int(np.count_nonzero(clipped != raw))
    raw = clipped

    n = raw.shape[0]
    if n >= H:
        forecast = raw[:H].copy()
        if n > H:
            stats["truncated"] = True
            stats["n_dropped"] = n - H
        stats["n_ok"] = H
    else:  # 0 < n < H -> forward-fill with the last parsed CO2 value
        pad = np.full(H - n, raw[-1], dtype=np.float64)
        forecast = np.concatenate([raw, pad])
        stats["padded"] = True
        stats["n_padded"] = H - n
        stats["n_ok"] = n

    return forecast, stats


# ---------------------------------------------------------------------------
# Token-budget estimate (rough; no tokenizer dependency).
# ---------------------------------------------------------------------------
def estimate_token_count(
    prompt_text: str,
    n_numbers: int | None = None,
    n_rows: int | None = None,
    tokens_per_number: float = 2.5,
    scaffold_tokens: int = 150,
) -> dict[str, int]:
    """Rough token estimate for a serialized prompt (no real tokenizer).

    Two independent estimates are returned:
      * ``char_based``  = ``len(text) / 4`` (generic English/BPE rule of thumb).
      * ``number_based`` = ``n_numbers * tokens_per_number + n_rows(newlines) +
        scaffold`` — closer to how digit-heavy BPE tokenizers behave (each 3-digit
        number + comma ~= 2-3 tokens). Used for the 11-channel context sanity check.

    ``number_based`` falls back to ``char_based`` when the counts are not supplied.
    """
    char_based = int(len(prompt_text) / 4.0)
    if n_numbers is None:
        number_based = char_based
    else:
        rows = n_rows if n_rows is not None else prompt_text.count("\n") + 1
        number_based = int(n_numbers * tokens_per_number + rows + scaffold_tokens)
    return {"char_based": char_based, "number_based": number_based}


# ---------------------------------------------------------------------------
# Self-test — run:  python -m co2_forecast.models.foundation.llm_serialize
#            or:    python src/co2_forecast/models/foundation/llm_serialize.py
# ---------------------------------------------------------------------------
def _selftest() -> None:
    rng = np.random.default_rng(42)
    T, H = 288, 288

    V_C_COLS = [
        "co2", "temperature", "humidity", "pm25", "pm10", "tvoc",
        "outdoor_temperature", "outdoor_humidity",
        "occupancy", "supply_air_flow", "supply_air_temp",
    ]
    C = len(V_C_COLS)
    assert C == 11

    # --- Build a synthetic, realistic 288 x 11 window --------------------------
    t = np.arange(T)
    window = np.zeros((T, C), dtype=np.float64)
    # co2 (col 0): moderate diurnal band ~ [~445, ~560] ppm. A moderate range is
    # chosen so prec=3 quantization stays well under 0.2 ppm (step = a0 * 10**-prec);
    # the bound scales with the CO2 dynamic range and is reported below.
    window[:, 0] = 500.0 + 50.0 * np.sin(2 * np.pi * t / T) + rng.normal(0, 2.0, T)
    window[:, 1] = 22.0 + 2.0 * np.sin(2 * np.pi * t / T)            # temperature
    window[:, 2] = 45.0 + 8.0 * np.cos(2 * np.pi * t / T)            # humidity
    window[:, 3] = 8.0 + 3.0 * rng.random(T)                        # pm25
    window[:, 4] = 12.0 + 4.0 * rng.random(T)                       # pm10
    window[:, 5] = 120.0 + 40.0 * rng.random(T)                    # tvoc
    window[:, 6] = 15.0 + 5.0 * np.sin(2 * np.pi * t / T)          # outdoor_temp
    window[:, 7] = 60.0 + 10.0 * np.cos(2 * np.pi * t / T)         # outdoor_humidity
    window[:, 8] = 0.0                                             # occupancy: FLAT (tests guard)
    window[:, 9] = 300.0 + 100.0 * rng.random(T)                  # supply_air_flow
    window[:, 10] = 18.0 + 1.0 * rng.random(T)                    # supply_air_temp

    # --- Test 1: flat-channel guard ------------------------------------------
    a_flat, b_flat = scale_channel(window[:, 8])
    assert a_flat == 1.0, f"flat guard failed: a={a_flat}"
    # A flat channel round-trips exactly: scaled == 0 -> raw == b == min.
    flat_scaled = (window[:, 8] - b_flat) / a_flat
    assert np.allclose(flat_scaled, 0.0)
    assert np.allclose(flat_scaled * a_flat + b_flat, window[:, 8])
    print(f"[1] flat-channel guard OK        a={a_flat} b={b_flat}")

    # --- Test 2: serialize the full window -----------------------------------
    history_text, a0, b0 = serialize_window(window, prec=PREC)
    rows = history_text.split("\n")
    assert len(rows) == T, f"expected {T} rows, got {len(rows)}"
    assert all(len(r.split(",")) == C for r in rows), "each row must have C columns"
    # CO2 (col 0) scaling from serialize must equal a direct scale_channel fit.
    a0_ref, b0_ref = scale_channel(window[:, 0])
    assert abs(a0 - a0_ref) < 1e-9 and abs(b0 - b0_ref) < 1e-9
    print(f"[2] serialize_window OK          rows={len(rows)} cols={C} a0={a0:.3f} b0={b0:.3f}")

    # --- Test 3: round-trip CO2 recovery (scale -> format -> parse -> unscale) -
    # Encode the CO2 column exactly as the model would emit it, then parse back.
    co2_true = window[:, 0]
    completion = format_series(co2_true, a0, b0, prec=PREC)
    recovered, stats = parse_completion(completion, H, a0, b0, prec=PREC)
    assert stats["n_ok"] == H and not stats["failed"] and not stats["truncated"]
    err = np.abs(recovered - co2_true)
    max_err, mean_err = float(err.max()), float(err.mean())
    median_err = float(np.median(err))
    quant_bound = 0.5 * a0 * (10.0 ** -PREC)   # theoretical half-step quantization bound
    assert max_err <= quant_bound + 1e-9, f"error {max_err} exceeds quant bound {quant_bound}"
    assert median_err < 0.2, f"round-trip median error {median_err:.4f} ppm >= 0.2 ppm (prec={PREC})"
    print(
        f"[3] round-trip CO2 recovery OK   prec={PREC}  max_err={max_err:.4f} ppm  "
        f"median_err={median_err:.4f} ppm  mean_err={mean_err:.4f} ppm  "
        f"quant_bound={quant_bound:.4f} ppm  (median < 0.2 ppm)"
    )

    # Honesty diagnostic: full-dynamic-range CO2 window (400 -> 1200 ppm). prec=2
    # recovery is quantization-bounded to a few ppm here, as expected.
    wide = 400.0 + 800.0 * (t / (T - 1))
    aw, bw = scale_channel(wide)
    rec_wide, _ = parse_completion(format_series(wide, aw, bw, prec=PREC), T, aw, bw, prec=PREC)
    wide_max = float(np.abs(rec_wide - wide).max())
    print(
        f"    (diagnostic) full-range 400-1200 ppm window: max_err={wide_max:.3f} ppm "
        f"= quant bound {0.5 * aw * 10 ** -PREC:.3f}; expected"
    )

    # --- Test 4: parser robustness (must never crash) ------------------------
    # 4a: junk-wrapped exactly-H numeric run -> clean extract, no pad/truncate.
    good = "087,090,101"
    ah, bh = 100.0, 400.0
    fc, st = parse_completion(f"Sure! here you go: {good} -- hope that helps!", 3, ah, bh, prec=PREC)
    assert st["n_parsed"] == 3 and st["n_ok"] == 3 and not st["truncated"] and not st["padded"]
    assert np.allclose(fc, np.array([87, 90, 101]) / 10.0 ** PREC * ah + bh)
    print(f"[4a] junk-wrapped parse OK       pred={np.round(fc, 2).tolist()}")

    # 4b: short output -> forward-fill to H.
    fc, st = parse_completion("050,060", 5, ah, bh, prec=PREC)
    assert st["padded"] and st["n_padded"] == 3 and st["n_ok"] == 2
    assert fc.shape == (5,) and fc[2] == fc[3] == fc[4] == fc[1]
    print(f"[4b] short/forward-fill OK       pred={np.round(fc, 2).tolist()} n_padded={st['n_padded']}")

    # 4c: long output -> truncate to H.
    fc, st = parse_completion("010,020,030,040,050,060", 3, ah, bh, prec=PREC)
    assert st["truncated"] and st["n_dropped"] == 3 and fc.shape == (3,)
    print(f"[4c] long/truncate OK            n_dropped={st['n_dropped']}")

    # 4d: refusal / pure prose -> NaN + refusal flag (caller does persistence).
    fc, st = parse_completion("I cannot help with that.", 4, ah, bh, prec=PREC)
    assert st["refusal"] and st["failed"] and fc.shape == (4,) and np.isnan(fc).all()
    print(f"[4d] refusal fallback OK         all-NaN, refusal={st['refusal']}")

    # 4e: empty string and clipping.
    fc, st = parse_completion("", 3, ah, bh, prec=PREC)
    assert st["refusal"] and np.isnan(fc).all()
    fc, st = parse_completion("999999,000", 2, ah, bh, prec=PREC)  # huge -> clip to 5000, tiny -> clip to 400? (400>=300)
    assert st["n_clipped"] >= 1 and fc.max() <= CLIP_MAX_PPM and fc.min() >= CLIP_MIN_PPM
    print(f"[4e] empty + clip OK             n_clipped={st['n_clipped']} range=[{fc.min():.0f},{fc.max():.0f}]")

    # --- Test 5: prompt assembly + 11-channel token-budget estimate -----------------
    sys = build_system_prompt()
    user = build_user_prompt(V_C_COLS, history_text, n_history=T, horizon=H)
    msgs = build_prompt(sys, user)
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert f"H = {H}" in user and "column 1" in user and "co2,temperature" in user
    n_numbers = T * C  # 3168 for 11 channels
    est = estimate_token_count(user, n_numbers=n_numbers, n_rows=T)
    max_new = int(np.ceil(H * 4 * 1.6)) + 16  # worst-case output budget
    print(
        f"[5] prompt assembly OK           11 channels: {C}ch x {T}rows = {n_numbers} numbers\n"
        f"    est input tokens: number_based~{est['number_based']}  char_based~{est['char_based']}  "
        f"(fits 32k; Phi-4 16k under its own tok.)\n"
        f"    est max output tokens (H={H}): ~{max_new}\n"
        f"    est total (11 channels, worst H): ~{est['number_based'] + max_new} tokens"
    )

    # --- Test 6: BASE-model raw continuation prompt (univariate CO2) ----------
    # Base models get a plain comma-separated CO2 line ending in a ',' cue — no
    # roles, no system text. It uses the same CO2 scaling as serialize_window, so
    # a well-behaved continuation parses back to ppm via the identical parser.
    co2_col = window[:, 0:1]  # (T, 1) — the univariate base window
    base_prompt, ab0, bb0 = build_base_prompt(co2_col, prec=PREC)
    a0u, b0u = scale_channel(window[:, 0])
    assert abs(ab0 - a0u) < 1e-9 and abs(bb0 - b0u) < 1e-9, "base CO2 scaling must match scale_channel"
    assert base_prompt.endswith(","), "base prompt must end in a continuation cue"
    assert "\n" not in base_prompt and " " not in base_prompt, "base prompt is one compact comma-line"
    assert base_prompt.count(",") == T, f"expected {T} separators (T values + trailing cue), got {base_prompt.count(',')}"
    # A model that perfectly continues the CO2 series parses back to ppm.
    base_cont = format_series(window[:, 0], ab0, bb0, prec=PREC)
    rec_base, st_base = parse_completion(base_cont, H, ab0, bb0, prec=PREC)
    assert st_base["n_ok"] == H and not st_base["failed"]
    base_median = float(np.median(np.abs(rec_base - window[:, 0])))
    assert base_median < 0.2, f"base round-trip median {base_median:.4f} ppm >= 0.2 ppm"
    print(
        f"[6] base raw-prompt OK           len={len(base_prompt)} chars, ends-in-cue, "
        f"round-trip median_err={base_median:.4f} ppm (<0.2 ppm)"
    )

    # --- Test 7: BASE-MULTIVARIATE raw row-continuation (the 4th 2x2 cell) -----
    # The base LM gets the FULL row-stacked window (identical to serialize_window's
    # history_text) + a trailing '\n' cue, and continues the ROWS. The parser reads
    # the CO2 column out of each continued row.
    mv_prompt, amv, bmv = build_base_multivariate_prompt(window, prec=PREC)
    # Scaling must equal serialize_window / scale_channel on the CO2 column.
    assert abs(amv - a0_ref) < 1e-9 and abs(bmv - b0_ref) < 1e-9, "base-mv CO2 scaling must match"
    # Prompt == the exact multivariate history + a single trailing newline cue.
    assert mv_prompt.endswith("\n") and not mv_prompt.endswith("\n\n"), "base-mv ends in one '\\n' cue"
    assert mv_prompt[:-1] == history_text, "base-mv body must equal serialize_window history_text"
    mv_rows = mv_prompt.rstrip("\n").split("\n")
    assert len(mv_rows) == T and all(len(r.split(",")) == C for r in mv_rows), "base-mv is a T x C grid"

    # Synthetic model continuation: H future ROWS, full C columns, CO2 at co2_index=0
    # carries a known future curve (scaled with the SAME (amv, bmv)); other columns
    # are arbitrary filler the parser must ignore.
    fut = np.arange(T, T + H)
    fut_co2 = 500.0 + 50.0 * np.sin(2 * np.pi * fut / T)  # within clip range
    mv_future_rows = []
    for i in range(H):
        cells = [format_number(0.123, prec=PREC)] * C   # filler in every column
        cells[CO2_INDEX] = format_number((fut_co2[i] - bmv) / amv, prec=PREC)
        mv_future_rows.append(",".join(cells))
    mv_completion = "\n".join(mv_future_rows) + "\n"
    rec_mv, st_mv = parse_base_multivariate_completion(mv_completion, H, amv, bmv, prec=PREC)
    assert st_mv["n_ok"] == H and not st_mv["failed"] and not st_mv["truncated"] and not st_mv["padded"]
    assert st_mv["n_parsed"] == H
    err_mv = np.abs(rec_mv - fut_co2)
    max_mv, median_mv = float(err_mv.max()), float(np.median(err_mv))
    quant_bound_mv = 0.5 * amv * (10.0 ** -PREC)
    assert max_mv <= quant_bound_mv + 1e-9, f"base-mv error {max_mv} exceeds quant bound {quant_bound_mv}"
    assert median_mv < 0.2, f"base-mv round-trip median {median_mv:.4f} ppm >= 0.2 ppm"
    print(
        f"[7] base-multivariate round-trip OK  rows={T}x{C}, ends-in-'\\n', H={H} CO2 recovered "
        f"col-wise  max_err={max_mv:.4f} ppm  median_err={median_mv:.4f} ppm (<0.2 ppm)"
    )

    # --- Test 7b: row-aware parser — non-zero co2_index + robustness ----------
    # CO2 lives in column 2 here (a0=1000, b0=0 -> field/1000*1000 == field ppm),
    # exercising column selection independent of the field's position.
    two = "100,200,500,300\n110,210,600,310\n"
    rec, st = parse_base_multivariate_completion(two, 2, 1000.0, 0.0, co2_index=2, prec=PREC)
    assert np.allclose(rec, [500.0, 600.0]) and st["n_ok"] == 2 and not st["padded"] and not st["truncated"]

    # 7b-fill: fewer CO2 rows than H -> forward-fill the last.
    rec, st = parse_base_multivariate_completion("0,0,500,0\n", 3, 1000.0, 0.0, co2_index=2, prec=PREC)
    assert st["padded"] and st["n_padded"] == 2 and rec[0] == rec[1] == rec[2] == 500.0

    # 7b-trunc: more CO2 rows than H -> truncate.
    many = "0,0,500,0\n0,0,600,0\n0,0,700,0\n0,0,800,0\n"
    rec, st = parse_base_multivariate_completion(many, 2, 1000.0, 0.0, co2_index=2, prec=PREC)
    assert st["truncated"] and st["n_dropped"] == 2 and np.allclose(rec, [500.0, 600.0])

    # 7b-skip: a row missing the CO2 column is skipped, not counted.
    skip = "10,20\n0,0,500,0\n0,0,600,0\n"
    rec, st = parse_base_multivariate_completion(skip, 2, 1000.0, 0.0, co2_index=2, prec=PREC)
    assert np.allclose(rec, [500.0, 600.0]) and st["n_parsed"] == 2

    # 7b-clip: out-of-range CO2 field clipped to plausible ppm.
    rec, st = parse_base_multivariate_completion("0,0,999999,0\n0,0,000,0\n", 2, 1000.0, 0.0, co2_index=2, prec=PREC)
    assert st["n_clipped"] >= 1 and rec.max() <= CLIP_MAX_PPM and rec.min() >= CLIP_MIN_PPM

    # 7b-refusal: prose / no readable CO2 column -> all-NaN + refusal (caller falls back).
    rec, st = parse_base_multivariate_completion("I cannot help with that.\n\n", 4, 1.0, 400.0, co2_index=2, prec=PREC)
    assert st["refusal"] and st["failed"] and rec.shape == (4,) and np.isnan(rec).all()

    # 7b-empty: empty string -> refusal.
    rec, st = parse_base_multivariate_completion("", 3, 1.0, 400.0, co2_index=0, prec=PREC)
    assert st["refusal"] and np.isnan(rec).all()
    print("[7b] base-mv parser robustness OK    col-select, fill, truncate, skip, clip, refusal, empty")

    # --- Test 8: NATIVE covariate prompt (digit arm; covariates only, no CO2) --
    # The native arm hands the model ONLY covariate columns (CO2 is neither an
    # input channel nor a substitute) and asks for CO2 in RAW ppm — parsed with
    # the identity transform (prec=0, a0=1, b0=0). Assert: emits NO "co2" column,
    # channel-count == len(covariate cols), and a perfect ppm-integer completion
    # round-trips exactly.
    cov_cols = [c for c in V_C_COLS if c != "co2"]     # 10 covariate columns, NO co2
    cov_window = window[:, 1:]                          # drop the CO2 channel (col 0)
    assert "co2" not in cov_cols and cov_window.shape[1] == len(cov_cols) == C - 1
    nat_msgs, na0, nb0 = build_native_covariate_prompt(cov_window, cov_cols, horizon=5)
    assert (na0, nb0) == (1.0, 0.0), f"native digit transform must be identity, got ({na0},{nb0})"
    assert [m["role"] for m in nat_msgs] == ["system", "user"]
    nat_user = nat_msgs[1]["content"]
    # (a) NO "co2" column in the serialized columns line.
    col_line = [ln for ln in nat_user.split("\n") if ln.startswith("Columns (in order):")][0]
    assert "co2" not in col_line.lower(), f"native columns must exclude co2: {col_line!r}"
    assert col_line == "Columns (in order): " + ",".join(cov_cols)
    # (b) history grid channel-count == number of covariate columns (no CO2 column).
    grid = nat_user.split("History:\n", 1)[1].split("\nTask:", 1)[0]
    grid_rows = grid.split("\n")
    assert len(grid_rows) == T and all(len(r.split(",")) == len(cov_cols) for r in grid_rows), (
        "native history grid must be T x len(cov_cols) with NO CO2 column"
    )
    # (c) identity round-trip: a perfect ppm-integer completion recovers the target.
    fut_co2 = np.linspace(480.0, 560.0, 5)
    nat_completion = ",".join(str(int(round(x))) for x in fut_co2)
    rec_nat, st_nat = parse_completion(nat_completion, 5, na0, nb0, prec=0)  # identity parse
    assert st_nat["n_ok"] == 5 and not st_nat["failed"] and not st_nat["truncated"]
    assert np.allclose(rec_nat, np.round(fut_co2)), (rec_nat, np.round(fut_co2))
    # (d) co2-in-columns honesty guard raises.
    try:
        build_native_covariate_prompt(window, V_C_COLS, 5)  # V_C_COLS includes "co2"
        _raised = False
    except ValueError:
        _raised = True
    assert _raised, "native digit builder must reject a co2 column"
    print(
        f"[8] native covariate prompt OK   {len(cov_cols)} covariate cols (NO co2), "
        f"grid {T}x{len(cov_cols)}, identity ppm round-trip exact, co2-guard raises"
    )

    print("\nALL SELF-TESTS PASSED.")


if __name__ == "__main__":
    _selftest()
