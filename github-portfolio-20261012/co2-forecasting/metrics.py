"""Comprehensive forecasting metrics for the autoregressive CO2 task.

`compute_all_metrics(y_true, y_pred, ...)` returns a nested dict containing
every metric the paper will report. Categories:

- A. Absolute-error family   (MAE, RMSE, MSE, MdAE, max-AE, Huber)
- B. Percentage-error family (MAPE, sMAPE, WAPE, MPE)
- C. Skill metrics           (R², adjusted R², MASE-vs-persistence, Theil's U2)
- D. Bias metrics            (ME signed mean error)
- E. Residual stats          (Pearson, Spearman, KS, mean/std/skew/kurtosis)
- F. Per-horizon-step breakdown
- G. Per-room breakdown
- H. Per-time-of-day breakdown (hour-of-day, weekday vs weekend)
- I. CO2 threshold detection (precision/recall/F1 at 1000 / 1500 ppm)
- J. Bootstrap 95% CI on MAE + RMSE

All metrics are NaN-mask aware: NaN entries in y_true are ignored.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

# Default thresholds for IAQ-relevant high-CO2 detection (ppm).
DEFAULT_CO2_THRESHOLDS: tuple[float, ...] = (1000.0, 1500.0)
DEFAULT_HUBER_DELTA = 100.0
DEFAULT_BOOTSTRAP_N = 1000
USEFUL_HORIZON_MAE_THRESHOLD = 100.0  # ppm — first step where MAE exceeds this


# ---------------------------------------------------------------------------
# Primitive metric functions (per-metric; pure, NaN-aware)
# ---------------------------------------------------------------------------


def _flatten_and_mask(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Flatten both arrays and drop entries where y_true is NaN."""
    yt = np.asarray(y_true, dtype=np.float64).ravel()
    yp = np.asarray(y_pred, dtype=np.float64).ravel()
    keep = ~np.isnan(yt)
    return yt[keep], yp[keep]


def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size == 0:
        return float("nan")
    return float(np.mean(np.abs(yt - yp)))


def mse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size == 0:
        return float("nan")
    return float(np.mean((yt - yp) ** 2))


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size == 0:
        return float("nan")
    return float(np.sqrt(np.mean((yt - yp) ** 2)))


def mdae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Median absolute error — robust alternative to MAE."""
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size == 0:
        return float("nan")
    return float(np.median(np.abs(yt - yp)))


def max_ae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size == 0:
        return float("nan")
    return float(np.max(np.abs(yt - yp)))


def huber_loss(y_true: np.ndarray, y_pred: np.ndarray, delta: float = DEFAULT_HUBER_DELTA) -> float:
    """Mean Huber loss; quadratic for |err| <= delta, linear beyond."""
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size == 0:
        return float("nan")
    err = yt - yp
    abs_err = np.abs(err)
    quad = np.minimum(abs_err, delta)
    lin = abs_err - quad
    return float(np.mean(0.5 * quad ** 2 + delta * lin))


def mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Mean Absolute Percentage Error, in percent. Skips entries where |y_true|<1e-6."""
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size == 0:
        return float("nan")
    safe = np.abs(yt) > 1e-6
    if not safe.any():
        return float("nan")
    return float(np.mean(np.abs((yt[safe] - yp[safe]) / yt[safe])) * 100.0)


def smape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Symmetric MAPE — bounded in [0, 200]."""
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size == 0:
        return float("nan")
    denom = (np.abs(yt) + np.abs(yp)) / 2.0
    safe = denom > 1e-6
    if not safe.any():
        return float("nan")
    return float(np.mean(np.abs(yt[safe] - yp[safe]) / denom[safe]) * 100.0)


def wape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Weighted Absolute Percentage Error — more robust than MAPE."""
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size == 0:
        return float("nan")
    denom = np.sum(np.abs(yt))
    if denom < 1e-6:
        return float("nan")
    return float(np.sum(np.abs(yt - yp)) / denom * 100.0)


def mpe(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Mean Percentage Error (signed) — positive => model underforecasts."""
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size == 0:
        return float("nan")
    safe = np.abs(yt) > 1e-6
    if not safe.any():
        return float("nan")
    return float(np.mean((yt[safe] - yp[safe]) / yt[safe]) * 100.0)


def me(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Mean Error (signed) — positive => model underforecasts."""
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size == 0:
        return float("nan")
    return float(np.mean(yt - yp))


def r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size < 2:
        return float("nan")
    ss_res = np.sum((yt - yp) ** 2)
    ss_tot = np.sum((yt - yt.mean()) ** 2)
    if ss_tot < 1e-12:
        return float("nan")
    return float(1.0 - ss_res / ss_tot)


def adjusted_r2(y_true: np.ndarray, y_pred: np.ndarray, n_params: int) -> float:
    yt, yp = _flatten_and_mask(y_true, y_pred)
    n = yt.size
    if n - n_params - 1 <= 0:
        return float("nan")
    r = r2(yt, yp)
    if np.isnan(r):
        return float("nan")
    return float(1.0 - (1.0 - r) * (n - 1) / (n - n_params - 1))


def mase(y_true: np.ndarray, y_pred: np.ndarray, persistence_pred: np.ndarray) -> float:
    """MAE / MAE_of_persistence. < 1 = beats naive forecast."""
    yt, yp = _flatten_and_mask(y_true, y_pred)
    yt2, pp = _flatten_and_mask(y_true, persistence_pred)
    if yt.size == 0 or yt2.size == 0:
        return float("nan")
    mae_model = mae(yt, yp)
    mae_persistence = mae(yt2, pp)
    if mae_persistence < 1e-6:
        return float("nan")
    return float(mae_model / mae_persistence)


def theil_u2(y_true: np.ndarray, y_pred: np.ndarray, persistence_pred: np.ndarray) -> float:
    """Theil's U2 — RMSE of model / RMSE of persistence."""
    rmse_model = rmse(y_true, y_pred)
    rmse_pers = rmse(y_true, persistence_pred)
    if np.isnan(rmse_pers) or rmse_pers < 1e-6:
        return float("nan")
    return float(rmse_model / rmse_pers)


def pearson_corr(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size < 2 or np.std(yt) < 1e-12 or np.std(yp) < 1e-12:
        return float("nan")
    return float(np.corrcoef(yt, yp)[0, 1])


def spearman_corr(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size < 2:
        return float("nan")
    return float(stats.spearmanr(yt, yp).statistic)


def residual_stats(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size < 2:
        return {"mean": float("nan"), "std": float("nan"),
                "skew": float("nan"), "kurtosis": float("nan"),
                "ks_stat": float("nan"), "ks_pvalue": float("nan")}
    res = yt - yp
    ks = stats.kstest(res, "norm", args=(res.mean(), res.std() or 1.0))
    return {
        "mean":     float(res.mean()),
        "std":      float(res.std()),
        "skew":     float(stats.skew(res)),
        "kurtosis": float(stats.kurtosis(res)),
        "ks_stat":  float(ks.statistic),
        "ks_pvalue": float(ks.pvalue),
    }


# ---------------------------------------------------------------------------
# Threshold detection (CO2 > X ppm)
# ---------------------------------------------------------------------------


def threshold_classification(y_true: np.ndarray, y_pred: np.ndarray, threshold: float) -> dict[str, float]:
    """Precision/recall/F1 for `y > threshold` binary classification."""
    yt, yp = _flatten_and_mask(y_true, y_pred)
    if yt.size == 0:
        return {"precision": float("nan"), "recall": float("nan"), "f1": float("nan"),
                "n_true_positive_examples": 0}
    true_high = yt > threshold
    pred_high = yp > threshold
    tp = int(np.sum(true_high & pred_high))
    fp = int(np.sum(~true_high & pred_high))
    fn = int(np.sum(true_high & ~pred_high))
    precision = tp / (tp + fp) if (tp + fp) > 0 else float("nan")
    recall = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if (precision and recall and not np.isnan(precision) and not np.isnan(recall) and (precision + recall) > 0)
          else float("nan"))
    return {
        "threshold_ppm": float(threshold),
        "precision":     float(precision) if not np.isnan(precision) else float("nan"),
        "recall":        float(recall) if not np.isnan(recall) else float("nan"),
        "f1":            float(f1) if not np.isnan(f1) else float("nan"),
        "n_true_positive_examples": int(np.sum(true_high)),
    }


# ---------------------------------------------------------------------------
# Bootstrap CIs
# ---------------------------------------------------------------------------


def bootstrap_ci(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    metric_fn: Any,
    n_bootstrap: int = DEFAULT_BOOTSTRAP_N,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Bootstrap (lo, point, hi) CI for `metric_fn(y_true, y_pred)`.

    Resamples (y_true, y_pred) rows with replacement `n_bootstrap` times.
    Returns the 100*(1-alpha) confidence interval percentiles plus the
    point estimate on the full sample.
    """
    yt, yp = _flatten_and_mask(y_true, y_pred)
    point = metric_fn(yt, yp)
    if yt.size < 2:
        return float("nan"), point, float("nan")
    rng = np.random.default_rng(seed)
    n = yt.size
    samples = np.empty(n_bootstrap, dtype=np.float64)
    for i in range(n_bootstrap):
        idx = rng.integers(0, n, n)
        samples[i] = metric_fn(yt[idx], yp[idx])
    lo = float(np.percentile(samples, 100 * (alpha / 2)))
    hi = float(np.percentile(samples, 100 * (1 - alpha / 2)))
    return lo, float(point), hi


# ---------------------------------------------------------------------------
# Helper: persistence baseline
# ---------------------------------------------------------------------------


def persistence_forecast(
    co2_history: np.ndarray,   # (n_windows, lookback)
    horizon: int,
) -> np.ndarray:
    """Repeat the last lookback value `horizon` times as the naive forecast.

    Returns shape (n_windows, horizon).
    """
    last = co2_history[:, -1:]
    return np.repeat(last, horizon, axis=1)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def _aggregate_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    persistence_pred: np.ndarray | None = None,
    huber_delta: float = DEFAULT_HUBER_DELTA,
    n_params: int = 1,
    compute_bootstrap: bool = True,
    n_bootstrap: int = DEFAULT_BOOTSTRAP_N,
    seed: int = 0,
    thresholds: tuple[float, ...] = DEFAULT_CO2_THRESHOLDS,
) -> dict[str, Any]:
    """Compute all scalar metrics for one (y_true, y_pred) pair."""
    out: dict[str, Any] = {}

    # A. Absolute-error
    out["mae"] = mae(y_true, y_pred)
    out["mse"] = mse(y_true, y_pred)
    out["rmse"] = rmse(y_true, y_pred)
    out["mdae"] = mdae(y_true, y_pred)
    out["max_ae"] = max_ae(y_true, y_pred)
    out["huber"] = huber_loss(y_true, y_pred, delta=huber_delta)

    # B. Percentage
    out["mape"] = mape(y_true, y_pred)
    out["smape"] = smape(y_true, y_pred)
    out["wape"] = wape(y_true, y_pred)
    out["mpe"] = mpe(y_true, y_pred)

    # C. Skill
    out["r2"] = r2(y_true, y_pred)
    out["adj_r2"] = adjusted_r2(y_true, y_pred, n_params=n_params)
    if persistence_pred is not None:
        out["mase_persistence"] = mase(y_true, y_pred, persistence_pred)
        out["theil_u2"] = theil_u2(y_true, y_pred, persistence_pred)
    else:
        out["mase_persistence"] = float("nan")
        out["theil_u2"] = float("nan")

    # D. Bias
    out["me"] = me(y_true, y_pred)

    # E. Residual distribution
    out["pearson"] = pearson_corr(y_true, y_pred)
    out["spearman"] = spearman_corr(y_true, y_pred)
    out["residual"] = residual_stats(y_true, y_pred)

    # I. Threshold detection
    out["thresholds"] = {
        f"co2_gt_{int(t)}_ppm": threshold_classification(y_true, y_pred, t)
        for t in thresholds
    }

    # J. Bootstrap CIs (optional)
    if compute_bootstrap:
        lo_m, _, hi_m = bootstrap_ci(y_true, y_pred, mae, n_bootstrap=n_bootstrap, seed=seed)
        lo_r, _, hi_r = bootstrap_ci(y_true, y_pred, rmse, n_bootstrap=n_bootstrap, seed=seed + 1)
        out["bootstrap_ci_95"] = {
            "mae":  {"lo": lo_m, "hi": hi_m, "n_bootstrap": n_bootstrap},
            "rmse": {"lo": lo_r, "hi": hi_r, "n_bootstrap": n_bootstrap},
        }

    return out


def compute_all_metrics(
    y_true: np.ndarray,                  # (n_windows, horizon)
    y_pred: np.ndarray,                  # (n_windows, horizon)
    *,
    group_ids: np.ndarray | None = None,         # (n_windows,) integer room ids
    room_names: list[str] | None = None,         # idx → name mapping
    timestamps: pd.DatetimeIndex | np.ndarray | None = None,  # window-start timestamps
    co2_history: np.ndarray | None = None,       # (n_windows, lookback) for persistence baseline
    huber_delta: float = DEFAULT_HUBER_DELTA,
    n_params: int = 1,
    compute_bootstrap: bool = True,
    n_bootstrap: int = DEFAULT_BOOTSTRAP_N,
    seed: int = 0,
    thresholds: tuple[float, ...] = DEFAULT_CO2_THRESHOLDS,
    useful_horizon_threshold: float = USEFUL_HORIZON_MAE_THRESHOLD,
) -> dict[str, Any]:
    """Compute the full comprehensive metrics panel.

    Returns a nested dict::

        {
            "aggregate":        { ... all scalar metrics ... },
            "per_horizon_step": { "mae": [...], "rmse": [...], ... },
            "useful_horizon_steps": int,
            "per_room":         {room_name: {... metrics ...}},
            "per_hour_of_day":  {hour: {... metrics ...}},
            "per_weekday_weekend": {"weekday": {...}, "weekend": {...}},
        }
    """
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    if y_true.ndim == 1:
        y_true = y_true.reshape(-1, 1)
        y_pred = y_pred.reshape(-1, 1)
    n_windows, horizon = y_true.shape

    persistence_pred = None
    if co2_history is not None:
        persistence_pred = persistence_forecast(co2_history, horizon)

    # ---- aggregate (whole test set) ----
    aggregate = _aggregate_metrics(
        y_true, y_pred,
        persistence_pred=persistence_pred,
        huber_delta=huber_delta,
        n_params=n_params,
        compute_bootstrap=compute_bootstrap,
        n_bootstrap=n_bootstrap,
        seed=seed,
        thresholds=thresholds,
    )

    # ---- per-horizon-step ----
    per_step = {"mae": [], "rmse": [], "mape": [], "smape": [], "me": []}
    for h in range(horizon):
        per_step["mae"].append(mae(y_true[:, h], y_pred[:, h]))
        per_step["rmse"].append(rmse(y_true[:, h], y_pred[:, h]))
        per_step["mape"].append(mape(y_true[:, h], y_pred[:, h]))
        per_step["smape"].append(smape(y_true[:, h], y_pred[:, h]))
        per_step["me"].append(me(y_true[:, h], y_pred[:, h]))

    # "useful horizon" — first step where MAE exceeds the threshold
    useful = horizon
    for h, m in enumerate(per_step["mae"]):
        if not np.isnan(m) and m > useful_horizon_threshold:
            useful = h
            break

    # ---- per-room ----
    per_room: dict[str, Any] = {}
    if group_ids is not None and room_names is not None:
        for i, name in enumerate(room_names):
            mask = group_ids == i
            if not mask.any():
                continue
            per_room[name] = _aggregate_metrics(
                y_true[mask], y_pred[mask],
                persistence_pred=persistence_pred[mask] if persistence_pred is not None else None,
                huber_delta=huber_delta,
                n_params=n_params,
                compute_bootstrap=False,  # skip per-room bootstrap to save compute
                thresholds=thresholds,
            )

    # ---- per-hour-of-day ----
    per_hour: dict[int, Any] = {}
    per_weekday: dict[str, Any] = {}
    if timestamps is not None:
        ts = pd.DatetimeIndex(timestamps)
        for h in range(24):
            mask = ts.hour == h
            if not mask.any():
                continue
            yt_h = y_true[mask]
            yp_h = y_pred[mask]
            per_hour[h] = {
                "mae": mae(yt_h, yp_h),
                "rmse": rmse(yt_h, yp_h),
                "n_windows": int(mask.sum()),
            }
        is_weekend = ts.dayofweek >= 5
        for label, mask in [("weekday", ~is_weekend), ("weekend", is_weekend)]:
            if not mask.any():
                continue
            per_weekday[label] = {
                "mae": mae(y_true[mask], y_pred[mask]),
                "rmse": rmse(y_true[mask], y_pred[mask]),
                "n_windows": int(mask.sum()),
            }

    return {
        "aggregate": aggregate,
        "per_horizon_step": per_step,
        "useful_horizon_steps": int(useful),
        "per_room": per_room,
        "per_hour_of_day": per_hour,
        "per_weekday_weekend": per_weekday,
    }
