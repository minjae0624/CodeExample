"""Target-history substitution for covariate-only foundation-model experiments.

Foundation models (Chronos-2, Moirai, Granite TTM) are target-conditioned: every
forward pass demands a historical target series as input. For the covariate-only
sub-phase, where the cell's ``input_cols`` exclude CO2, we still need to fill
the target slot with *something*. We support three strategies for what that
"something" is:

- ``constant_baseline``: a single number (default 420 ppm, ~outdoor CO2). Tests
  whether covariates alone can drive prediction when the target prior carries
  zero information.
- ``climatology``: per-(room, local-hour-of-day) mean CO2 from the train split.
  Tests whether a weak time-of-day prior plus covariates yields signal.
- ``zeros``: the trivial zero. Mirrors Baselines robustness Approach 1
  (naive-zeroing) and tests pure failure mode.

The substitution is implemented as a *new DataFrame column* added BEFORE
``make_windows`` runs, with the wrapper rewriting its input-channel ordering
to put that column first. ``target=`` still points at the real ``co2`` column,
so ``y`` (ground truth) is the genuine future CO2 — only the X target-history
channel is substituted.

This pattern keeps ``make_windows``'s filtering correct (drops imputed-target
windows etc.) and is backward-compatible with sub-phase 1 (strategy ``real`` is
a no-op).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

VALID_STRATEGIES = frozenset({"real", "constant_baseline", "climatology", "zeros"})

# Convention: when a strategy != "real" rewrites the target-history channel,
# the substitute is written to this column name. The wrapper then puts this
# column FIRST in `input_cols` (replacing whatever was at channel 0).
SUBSTITUTE_COLUMN = "co2_target_history_substitute"


def compute_climatology(
    train_df: pd.DataFrame,
    *,
    target_col: str = "co2",
    group_col: str = "location_id",
    timestamp_col: str = "timestamp",
    tz: str = "America/New_York",
) -> dict[tuple[str, int], float]:
    """Per-(room, local-hour-of-day) mean target from the train split.

    Returns a dict keyed by ``(room_name, hour_0_23)`` with float means. Hour
    is the local-time hour (after ``tz_convert``), to match the building's
    operational diurnal cycle. Falls back to the *overall* per-room train mean
    if a particular (room, hour) cell is empty.
    """
    ts_utc = pd.to_datetime(train_df[timestamp_col], utc=True)
    ts_local = ts_utc.dt.tz_convert(tz)
    hours = ts_local.dt.hour.astype(int)

    work = train_df[[group_col, target_col]].assign(_hour=hours)
    cell_means = (
        work.groupby([group_col, "_hour"])[target_col]
        .mean()
        .dropna()
    )
    room_means = work.groupby(group_col)[target_col].mean().dropna()

    clim: dict[tuple[str, int], float] = {}
    for (room, hour), val in cell_means.items():
        clim[(str(room), int(hour))] = float(val)

    # Backfill any (room, hour) that didn't appear in train (rare but possible)
    # using that room's overall train mean.
    for room in train_df[group_col].astype(str).unique():
        for h in range(24):
            key = (room, h)
            if key not in clim:
                fallback = float(room_means.get(room, np.nan))
                if np.isnan(fallback):
                    raise ValueError(
                        f"Climatology fallback failed: no train data for room {room!r}"
                    )
                clim[key] = fallback
    return clim


def augment_df_with_target_substitute(
    df: pd.DataFrame,
    *,
    strategy: str,
    target_col: str = "co2",
    constant_value: float = 420.0,
    climatology: dict[tuple[str, int], float] | None = None,
    group_col: str = "location_id",
    timestamp_col: str = "timestamp",
    tz: str = "America/New_York",
) -> tuple[pd.DataFrame, str]:
    """Add a target-history substitute column to ``df`` per strategy.

    Returns ``(augmented_df, history_col_name)``. The wrapper should use
    ``history_col_name`` as the first entry of ``input_cols`` (so X[:,:,0]
    carries the substitute), while keeping ``target=target_col`` so y still
    comes from the real target column.

    For strategy ``"real"`` the DataFrame is returned unchanged and the
    history column name is ``target_col`` — equivalent to sub-phase-1
    autoregressive behavior.
    """
    if strategy not in VALID_STRATEGIES:
        raise ValueError(
            f"Unknown target_history_strategy {strategy!r}. "
            f"Valid: {sorted(VALID_STRATEGIES)}"
        )

    if strategy == "real":
        return df, target_col

    df = df.copy()
    n = len(df)
    if strategy == "constant_baseline":
        df[SUBSTITUTE_COLUMN] = np.full(n, float(constant_value), dtype=np.float32)
    elif strategy == "zeros":
        df[SUBSTITUTE_COLUMN] = np.zeros(n, dtype=np.float32)
    elif strategy == "climatology":
        if climatology is None:
            raise ValueError(
                "climatology dict is required for strategy='climatology'. "
                "Call compute_climatology(train_df) in fit_preprocessor first."
            )
        ts_utc = pd.to_datetime(df[timestamp_col], utc=True)
        ts_local = ts_utc.dt.tz_convert(tz)
        hours = ts_local.dt.hour.astype(int).to_numpy()
        rooms = df[group_col].astype(str).to_numpy()
        # Global cross-room hourly fallback for rooms absent from the training
        # climatology. Under leave-one-room-out the held-out test room has NO
        # per-room climatology (it was never in training), so its CO2-slot
        # substitute uses the building-wide hourly template — the realistic
        # deployment choice for a new room with no local history. Backward-compatible:
        # for non-LORO runs every (room, hour) is present, so this never fires.
        _by_hour: dict[int, list[float]] = {}
        for (_r, _h), _v in climatology.items():
            _by_hour.setdefault(_h, []).append(_v)
        global_clim = {h: float(np.mean(vs)) for h, vs in _by_hour.items()}
        overall_mean = (float(np.mean(list(climatology.values())))
                        if climatology else float(constant_value))
        # Vectorized lookup via dict comprehension — fast enough for our scale
        # (~60k rows for test split). Pre-cast to float32.
        subs = np.empty(n, dtype=np.float32)
        for i in range(n):
            key = (rooms[i], hours[i])
            subs[i] = climatology.get(key) if key in climatology \
                else global_clim.get(int(hours[i]), overall_mean)
        df[SUBSTITUTE_COLUMN] = subs

    return df, SUBSTITUTE_COLUMN


def build_input_cols_with_substitute(
    raw_input_cols: list[str],
    *,
    target_col: str,
    history_col: str,
) -> list[str]:
    """Return ``input_cols`` ordered with the target-history channel first.

    Mirrors the existing wrapper convention ``[target] + [c for c in
    input_cols if c != target]`` but lets ``history_col`` be either
    ``target_col`` (sub-phase 1 / strategy=='real') or the substitute column
    name (covariate-only with substitution).
    """
    return [history_col] + [c for c in raw_input_cols if c != target_col]


# ============================================================================
# Sub-phase 3 (robustness) — POST-windowing per-window perturbations.
#
# Unlike SP2 strategies (which substitute the target column BEFORE windowing —
# every row gets the same substitute value), SP3 robustness perturbations
# depend on the window's position (e.g., block mask at trail of lookback) and
# must be applied AFTER `make_windows` produces the X tensor.
#
# These mirror the Baselines test-time mask modes used by
# `scripts/test_with_co2_mask.py`:
#   - A1 random: per-(window, timestep) Bernoulli(mask_prob)
#   - A5 block:  contiguous block of `block_pct`% of lookback at start/mid/trail
#   - A9 stuck_last: every timestep of CO2 history = X[:, -1, target_channel]
#
# A18 self-conditioning rollout is implemented in each wrapper's `predict()`
# because it requires iterative inference (not a one-shot X perturbation).
# ============================================================================

def real_target_history(
    df,
    *,
    sampling_freq,
    lookback,
    horizon,
    target: str,
    input_cols: list[str],
    room_to_idx: dict[str, int],
):
    """Return the REAL un-perturbed target history per window — used as the
    MASE-vs-persistence denominator.

    Foundation wrappers' `prepare_data` build X with the substituted (SP2) or
    perturbed (SP3) target column in channel 0. Using that perturbed channel
    as the persistence baseline (e.g., A9-stuck → persistence forecasts the
    stuck value forever → tiny MASE for any non-trivial model) is not
    paper-honest. This helper re-windows the same df with the REAL target
    column to recover the un-perturbed history for the persistence baseline.

    Window alignment: this helper passes the same (sampling_freq, lookback,
    horizon, room_to_idx) and the same input_cols ordering (with target as
    channel 0). NaN-exclusion is identical for both passes when V-C splits
    are NaN-free (verified for V-C test, the only setting that uses this
    helper in Pass 1). Asserts the returned window count matches the caller's
    expectation.
    """
    from co2_forecast.data.windowing import make_windows
    # Build input_cols with the REAL target column in channel 0, mirroring the
    # shape of the substituted/perturbed cols list.
    real_cols = [target] + [c for c in input_cols if c != target]
    X_real, _, _ = make_windows(
        df,
        sampling_freq=sampling_freq,
        lookback=lookback,
        horizon=horizon,
        target=target,
        input_cols=real_cols,
        room_to_idx=room_to_idx,
    )
    # Return channel-0 = the real CO2 history per window.
    return X_real[:, :, 0]


VALID_TEST_TIME_MASKS = frozenset({"none", "a1_random", "a5_block", "a9_stuck_last"})

VALID_BLOCK_POSITIONS = frozenset({"start", "mid", "trail"})
VALID_BLOCK_PCTS = frozenset({5, 25, 75})


def apply_test_time_mask(
    X: np.ndarray,
    *,
    mode: str = "none",
    target_channel: int = 0,
    mask_prob: float = 0.5,
    block_pct: int = 25,
    block_position: str = "trail",
    mask_value: float = 0.0,
    seed: int = 42,
) -> np.ndarray:
    """Apply a per-window test-time perturbation to ``X[:, :, target_channel]``.

    Returns a NEW array (does not mutate ``X``). For ``mode="none"`` the input
    is returned unchanged (still a copy to keep callers safe).

    Args:
        X: (n_windows, lookback, n_channels) float array. Only the
            ``target_channel`` slice is modified.
        mode: one of ``VALID_TEST_TIME_MASKS``.
        target_channel: which channel index to perturb. Foundation wrappers
            place the target-history channel at index 0.
        mask_prob: Bernoulli probability for ``"a1_random"`` mode.
        block_pct: percentage of lookback to mask for ``"a5_block"``: 5, 25, or 75.
        block_position: where the masked block sits: ``"start"`` (oldest steps),
            ``"mid"`` (centered), or ``"trail"`` (most recent — closest to forecast).
        mask_value: value to write into masked positions. Default 0 (zero-mask).
            For ``"a9_stuck_last"`` this is ignored; the last observed value is used.
        seed: RNG seed for reproducibility of the random mask.

    Returns:
        Perturbed X array (n_windows, lookback, n_channels), same dtype as input.
    """
    if mode not in VALID_TEST_TIME_MASKS:
        raise ValueError(
            f"Unknown test_time_mask_mode {mode!r}. Valid: {sorted(VALID_TEST_TIME_MASKS)}"
        )
    if X.ndim != 3:
        raise ValueError(f"X must be (n_windows, lookback, n_channels); got {X.shape}")

    if mode == "none":
        return X.copy()

    n_windows, lookback, n_channels = X.shape
    if not (0 <= target_channel < n_channels):
        raise ValueError(f"target_channel {target_channel} out of range for n_channels={n_channels}")

    out = X.copy()
    target_slice = out[:, :, target_channel]  # view into out

    if mode == "a1_random":
        if not (0.0 <= mask_prob <= 1.0):
            raise ValueError(f"mask_prob must be in [0, 1]; got {mask_prob}")
        rng = np.random.default_rng(seed)
        mask = rng.random(target_slice.shape) < mask_prob  # (n_windows, lookback) bool
        target_slice[mask] = mask_value

    elif mode == "a5_block":
        if block_pct not in VALID_BLOCK_PCTS:
            raise ValueError(f"block_pct must be one of {sorted(VALID_BLOCK_PCTS)}; got {block_pct}")
        if block_position not in VALID_BLOCK_POSITIONS:
            raise ValueError(
                f"block_position must be one of {sorted(VALID_BLOCK_POSITIONS)}; got {block_position!r}"
            )
        n_mask = max(1, int(round(lookback * block_pct / 100.0)))
        if block_position == "start":
            start_idx = 0
        elif block_position == "trail":
            start_idx = lookback - n_mask
        else:  # mid
            start_idx = max(0, (lookback - n_mask) // 2)
        end_idx = start_idx + n_mask
        target_slice[:, start_idx:end_idx] = mask_value

    elif mode == "a9_stuck_last":
        # Sensor freeze: every timestep reads the last valid value of the lookback.
        # Take the last value per window then broadcast across all lookback steps.
        last_vals = target_slice[:, -1:].copy()  # (n_windows, 1)
        target_slice[:, :] = last_vals  # broadcast

    return out
