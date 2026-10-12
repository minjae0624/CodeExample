#!/usr/bin/env python
"""
paired_tests.py -- paired statistical tests on the seeded re-run predictions of a
multiscale indoor-environment forecasting study (folder AdditionalExperiment_StatTests).

Two input modes (pick one):
  --runs_dir <.../AdditionalExperiment_StatTests/runs> --reference <.../audit/reference_forecasts.csv>
        reads every runs/<run>/preds.npz + metrics.json (any number of seeds per configuration)
  --handover <.../Study3_StatTests_handover>
        reads the handover package (predictions/*.csv, tables/runs_overview.csv)

Writes only into --out: comparisons.csv, per_target.csv, references.csv,
dissertation_table.csv, summary.md.  Nothing else is touched.

Method
  * Every run predicted the same 5,804 chronological test windows, so every
    comparison is PAIRED on windows.
  * Primary test: paired moving-block bootstrap over contiguous time
    (Kunsch 1989).  Blocks of --block windows (default 120 = about 5 h) are
    resampled with replacement from the chronological test set; each metric is
    recomputed on the resampled series for both runs; the paired difference
    gives a 95% percentile CI and a two-sided p-value (CI inversion).  Works
    for all six metrics, including RMSE, Accuracy, F1 and AUROC, and keeps
    the windows' autocorrelation.  Sensitivity: the same at --block_alt.
  * Corroboration for MAE and R2 (exact means of a per-window loss):
    Diebold-Mariano test with a Bartlett/Newey-West HAC variance, bandwidth by
    Andrews' AR(1) rule from the loss differential.
  * Reference tests (assume independent windows; reported for information
    only): McNemar exact test on accuracy, DeLong test on AUROC.
  * Multiplicity: Holm correction within each comparison family and metric,
    over the primary comparisons.
  * Seeds: when both configurations have several seeds they are paired seed by
    seed (42-42, 43-43, ...); when one side has a single run, each seed of the
    other side is paired with it.  The primary row is the mean over the pairs
    of the paired difference; the per-pair rows are reported as well.
  * Two no-skill references are tested against every run: persistence
    (each target at t forecast by its value at t-1) for regression and the
    last visible preference label in the input window for classification.

Dependencies: numpy, pandas, scipy (no statsmodels, no sklearn).
"""
import argparse
import glob
import json
import os
import re
import sys
import time

import numpy as np
import pandas as pd
from scipy import stats

TARGETS = ["Radon", "CO2", "Humidity", "IndoorTemperature", "VOC", "IndoorPressure", "PM2.5"]
METRICS = ["RMSE", "MAE", "R2", "Accuracy", "F1", "AUROC"]
REG_METRICS = ["RMSE", "MAE", "R2"]
CLS_METRICS = ["Accuracy", "F1", "AUROC"]
HIGHER_IS_BETTER = {"RMSE": False, "MAE": False, "R2": True, "Accuracy": True, "F1": True, "AUROC": True}
LABEL = {"gpt2_fk8": "GPT2 freeze8", "gpt2_lora": "GPT2 LoRA", "gpt2_scratch": "GPT2 scratch",
         "gpt2-medium_fk20": "GPT2 Medium freeze20", "gpt2-medium_lora": "GPT2 Medium LoRA",
         "opt-125m_fk8": "OPT 125M freeze8", "opt-125m_lora": "OPT 125M LoRA",
         "opt-350m_fk20": "OPT 350M freeze20", "opt-350m_lora": "OPT 350M LoRA",
         "bert-base-uncased_fk8": "BERT Base freeze8", "bert-base-uncased_lora": "BERT Base LoRA",
         "bert-large-uncased_fk20": "BERT Large freeze20", "bert-large-uncased_lora": "BERT Large LoRA",
         "bert-large-uncased_fk0": "BERT Large freeze0", "bert-large-uncased_fk12": "BERT Large freeze12",
         "sgd": "SGD Linear", "lightgbm": "LightGBM", "xgboost": "XGBoost", "lstm": "LSTM",
         "transformer": "Transformer", "ann": "ANN"}
SEED_RE = re.compile(r"^(.*)_seed(\d+)$")


# ----------------------------------------------------------------------------- metrics
def auroc(scores, labels):
    """Mann-Whitney AUROC with average ranks for ties (identical to sklearn.roc_auc_score)."""
    labels = np.asarray(labels).astype(bool)
    n_pos = labels.sum()
    n_neg = labels.size - n_pos
    if n_pos == 0 or n_neg == 0:
        return np.nan
    r = stats.rankdata(scores)
    return (r[labels].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def full_metrics(pred, prob, y, lab, ytrue_pref):
    """The paper's six metrics: mean over the 7 targets of per-target RMSE/MAE/R2
    (native units), Accuracy/F1/AUROC on labeled windows at threshold 0.5."""
    e = pred - y
    rmse_t = np.sqrt((e ** 2).mean(0))
    mae_t = np.abs(e).mean(0)
    sst = ((y - y.mean(0)) ** 2).sum(0)
    r2_t = 1.0 - (e ** 2).sum(0) / sst
    yl = ytrue_pref[lab]
    pl = (prob[lab] >= 0.5).astype(int)
    tp = int(((pl == 1) & (yl == 1)).sum())
    fp = int(((pl == 1) & (yl == 0)).sum())
    fn = int(((pl == 0) & (yl == 1)).sum())
    acc = float((pl == yl).mean())
    f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) > 0 else 0.0
    return dict(RMSE=rmse_t.mean(), MAE=mae_t.mean(), R2=r2_t.mean(), Accuracy=acc, F1=f1,
                AUROC=auroc(prob[lab], yl), per_target=dict(RMSE=rmse_t, MAE=mae_t, R2=r2_t))


def per_window_losses(pred, y, var_pop):
    """Per-window loss whose mean over windows is exactly the overall MAE, MSE and
    (1 - R2): mean over targets of |e|, e^2 and e^2 / Var_test(target)."""
    e = pred - y
    return dict(MAE=np.abs(e).mean(1), MSE=(e ** 2).mean(1), R2=((e ** 2) / var_pop).mean(1))


# ----------------------------------------------------------------------------- loading
def load_handover(handover):
    tw = pd.read_csv(os.path.join(handover, "predictions", "test_windows.csv"), float_precision="round_trip")
    assert (tw["window"].values == np.arange(len(tw))).all(), "test_windows.csv is not in window order"
    y = tw[[f"y_true_{t}" for t in TARGETS]].to_numpy(dtype=np.float64)
    lab = tw["pref_mask"].to_numpy() == 1
    ytrue_pref = tw["pref_true"].to_numpy().astype(int)
    overview = pd.read_csv(os.path.join(handover, "tables", "runs_overview.csv")).set_index("run")

    def read(name):
        p = pd.read_csv(os.path.join(handover, "predictions", f"{name}.csv"), float_precision="round_trip")
        m = tw[["window", "test_row_index"]].merge(p, on=["window", "test_row_index"], how="left", validate="one_to_one")
        assert len(m) == len(tw) and not m["pred_CO2"].isna().any(), f"{name}: rows do not pair with test_windows"
        return m[[f"pred_{t}" for t in TARGETS]].to_numpy(dtype=np.float64), m["pref_prob"].to_numpy(dtype=np.float64)

    P, Q, REP = {}, {}, {}
    for r in overview.index:
        P[r], Q[r] = read(r)
        REP[r] = {k: float(overview.loc[r, k]) for k in METRICS}
    pers, last = read("reference_no_skill")
    return y, lab, ytrue_pref, P, Q, REP, pers, last, f"handover package {handover}"


def load_runs(runs_dir, reference_csv):
    names = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(runs_dir, "*", "preds.npz"))
                   if os.path.exists(os.path.join(os.path.dirname(p), "metrics.json")))
    assert names, f"no runs/<run>/preds.npz + metrics.json under {runs_dir}"
    P, Q, REP = {}, {}, {}
    ref = None
    for r in names:
        z = np.load(os.path.join(runs_dir, r, "preds.npz"))
        mj = json.load(open(os.path.join(runs_dir, r, "metrics.json")))
        tri = np.asarray(z["test_row_index"], dtype=np.int64)
        yt = np.asarray(z["y_true_real"], dtype=np.float64)
        pt = np.asarray(z["pref_true"]).astype(int)
        pm = np.asarray(z["pref_mask"]).astype(int)
        tn = [str(t) for t in z["target_names"]]
        assert tn == TARGETS, f"{r}: target order {tn}"
        if ref is None:
            ref = (tri, yt, pt, pm)
        else:
            assert np.array_equal(tri, ref[0]) and np.array_equal(yt, ref[1]) and np.array_equal(pt, ref[2]) \
                and np.array_equal(pm, ref[3]), f"{r}: pairing arrays differ from {names[0]}"
        P[r] = np.asarray(z["y_pred_real"], dtype=np.float64)
        Q[r] = np.asarray(z["pref_prob"], dtype=np.float64)
        assert P[r].shape == yt.shape and Q[r].shape == (yt.shape[0],), f"{r}: prediction shapes {P[r].shape} {Q[r].shape}"
        REP[r] = dict(RMSE=mj["overall"]["RMSE"], MAE=mj["overall"]["MAE"], R2=mj["overall"]["R2"],
                      Accuracy=mj["pref_metrics"]["Accuracy"], F1=mj["pref_metrics"]["F1"], AUROC=mj["pref_metrics"]["AUROC"])
    tri, y, ytrue_pref, pm = ref
    lab = pm == 1
    rf = pd.read_csv(reference_csv, float_precision="round_trip")
    assert (rf["window"].values == np.arange(len(rf))).all() and np.array_equal(rf["test_row_index"].values, tri), \
        "reference_forecasts.csv does not pair with the runs"
    pers = rf[[f"persist_{t}" for t in TARGETS]].to_numpy(dtype=np.float64)
    last = rf["last_visible_label"].to_numpy(dtype=np.float64)
    return y, lab, ytrue_pref, P, Q, REP, pers, last, f"{len(names)} runs under {runs_dir}"


# ----------------------------------------------------------------------------- Diebold-Mariano with HAC variance
def andrews_bandwidth(d):
    """Andrews (1991) AR(1) plug-in bandwidth for the Bartlett kernel."""
    T = d.size
    x = d - d.mean()
    denom = (x[:-1] ** 2).sum()
    rho = (x[1:] * x[:-1]).sum() / denom if denom > 0 else 0.0
    rho = float(np.clip(rho, -0.97, 0.97))
    alpha = 4 * rho ** 2 / ((1 - rho) ** 2 * (1 + rho) ** 2) if abs(rho) > 1e-12 else 0.0
    m = 1.1447 * (alpha * T) ** (1.0 / 3.0)
    return int(max(1, min(np.floor(m), T // 4))), rho


def hac_variance(d, m):
    """Newey-West long-run variance of the mean of d with Bartlett weights, lags 1..m."""
    T = d.size
    x = d - d.mean()
    v = (x ** 2).sum() / T
    for k in range(1, m + 1):
        w = 1.0 - k / (m + 1.0)
        v += 2.0 * w * (x[k:] * x[:-k]).sum() / T
    return max(v, 1e-300)


def dm_test(loss_a, loss_b, m=None):
    """Two-sided Diebold-Mariano test of E[loss_a - loss_b] = 0."""
    d = np.asarray(loss_a, dtype=np.float64) - np.asarray(loss_b, dtype=np.float64)
    T = d.size
    if m is None:
        m, rho = andrews_bandwidth(d)
    else:
        rho = np.nan
    lrv = hac_variance(d, m)
    dm = d.mean() / np.sqrt(lrv / T)
    p = 2 * stats.norm.sf(abs(dm))
    return dict(dm=dm, p=p, m=m, rho1=rho, mean_diff=d.mean())


# ----------------------------------------------------------------------------- McNemar and DeLong (independence assumed)
def mcnemar_exact(correct_a, correct_b):
    b = int((correct_a & ~correct_b).sum())  # A right, B wrong
    c = int((~correct_a & correct_b).sum())  # A wrong, B right
    n = b + c
    if n == 0:
        return dict(p=1.0, b=b, c=c)
    if hasattr(stats, "binomtest"):
        p = stats.binomtest(min(b, c), n, 0.5, alternative="two-sided").pvalue
    else:  # scipy < 1.7
        p = stats.binom_test(min(b, c), n, 0.5, alternative="two-sided")
    return dict(p=min(1.0, p), b=b, c=c)


def delong_components(scores, labels):
    """Structural components of the AUC (Sun & Xu 2014, fast DeLong)."""
    labels = labels.astype(bool)
    pos, neg = scores[labels], scores[~labels]
    m, n = pos.size, neg.size
    allr = stats.rankdata(np.concatenate([pos, neg]))
    rp = stats.rankdata(pos)
    rn = stats.rankdata(neg)
    v10 = (allr[:m] - rp) / n           # placement of each positive among negatives
    v01 = 1.0 - (allr[m:] - rn) / m     # placement of each negative among positives
    return v10, v01, (allr[:m].sum() - m * (m + 1) / 2.0) / (m * n)


def delong_test(scores_a, scores_b, labels):
    v10a, v01a, auc_a = delong_components(scores_a, labels)
    v10b, v01b, auc_b = delong_components(scores_b, labels)
    m, n = v10a.size, v01a.size
    s10 = np.cov(np.vstack([v10a, v10b]))
    s01 = np.cov(np.vstack([v01a, v01b]))
    s = s10 / m + s01 / n
    var = s[0, 0] + s[1, 1] - 2 * s[0, 1]
    if var <= 0:
        return dict(z=np.nan, p=np.nan, auc_a=auc_a, auc_b=auc_b)
    z = (auc_a - auc_b) / np.sqrt(var)
    return dict(z=z, p=2 * stats.norm.sf(abs(z)), auc_a=auc_a, auc_b=auc_b)


# ----------------------------------------------------------------------------- moving-block bootstrap
def make_block_resamples(T, block, B, rng):
    """B resamples of the window index 0..T-1: moving blocks of length `block`
    drawn with replacement from the chronological series (last block truncated)."""
    n_blocks = int(np.ceil(T / block))
    starts = rng.integers(0, T - block + 1, size=(B, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(B, -1)[:, :T]
    return idx.astype(np.int32)


def bootstrap_metrics(pred, prob, y, lab, ytrue_pref, idx):
    """Metric vectors (B,) recomputed on each block resample."""
    B = idx.shape[0]
    e_abs = np.abs(pred - y)
    e_sq = (pred - y) ** 2
    correct = (prob >= 0.5).astype(int) == ytrue_pref
    pred_pos = prob >= 0.5
    out = {k: np.empty(B) for k in METRICS}
    for b in range(B):
        ii = idx[b]
        ea = e_abs[ii]
        es = e_sq[ii]
        yy = y[ii]
        sst = ((yy - yy.mean(0)) ** 2).sum(0)
        out["RMSE"][b] = np.sqrt(es.mean(0)).mean()
        out["MAE"][b] = ea.mean(0).mean()
        out["R2"][b] = (1.0 - es.sum(0) / sst).mean()
        li = ii[lab[ii]]
        yl = ytrue_pref[li]
        pl = pred_pos[li]
        tp = np.sum(pl & (yl == 1))
        fp = np.sum(pl & (yl == 0))
        fn = np.sum(~pl & (yl == 1))
        out["Accuracy"][b] = correct[li].mean()
        out["F1"][b] = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) > 0 else 0.0
        out["AUROC"][b] = auroc(prob[li], yl)
    return out


def bootstrap_per_target(pred, y, idx):
    """Per-target RMSE and R2 vectors (B, 7) on each block resample."""
    B = idx.shape[0]
    es = (pred - y) ** 2
    rmse = np.empty((B, len(TARGETS)))
    r2 = np.empty((B, len(TARGETS)))
    for b in range(B):
        ii = idx[b]
        e = es[ii]
        yy = y[ii]
        sst = ((yy - yy.mean(0)) ** 2).sum(0)
        rmse[b] = np.sqrt(e.mean(0))
        r2[b] = 1.0 - e.sum(0) / sst
    return dict(RMSE=rmse, R2=r2)


def boot_summary(delta_star):
    """95% percentile CI and a two-sided CI-inversion p-value with the (k+1)/(B+1)
    convention (Davison & Hinkley 1997), so the smallest reportable p is 2/(B+1)."""
    lo, hi = np.percentile(delta_star, [2.5, 97.5])
    B = delta_star.size
    p_le = ((delta_star <= 0).sum() + 1) / (B + 1)
    p_ge = ((delta_star >= 0).sum() + 1) / (B + 1)
    return lo, hi, min(1.0, 2 * min(p_le, p_ge))


def holm(pvals):
    """Holm step-down adjusted p-values (NaN-safe)."""
    p = np.asarray(pvals, dtype=float)
    out = np.full(p.shape, np.nan)
    ok = ~np.isnan(p)
    pv = p[ok]
    n = pv.size
    if n == 0:
        return out
    order = np.argsort(pv)
    adj = np.empty(n)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, (n - rank) * pv[i])
        adj[i] = min(1.0, running)
    out[ok] = adj
    return out


# ----------------------------------------------------------------------------- comparison plan
def config_of(run):
    m = SEED_RE.match(run)
    return (m.group(1), int(m.group(2))) if m else (run, None)


def runs_of(cfg, names):
    out = {}
    for r in names:
        c, s = config_of(r)
        if c == cfg:
            out[s] = r
    return dict(sorted(out.items(), key=lambda kv: (kv[0] is None, kv[0])))


def pairs_for(cfg_a, cfg_b, names):
    """Seed pairing between two configurations. Returns (pairs, description) or None."""
    A, B = runs_of(cfg_a, names), runs_of(cfg_b, names)
    if not A or not B:
        return None
    if len(A) > 1 and len(B) > 1:
        shared = [s for s in A if s in B]
        if len(shared) > 1:
            return [(A[s], B[s]) for s in shared], f"seed-paired, n={len(shared)}"
    if len(A) > 1 and len(B) == 1:
        b = next(iter(B.values()))
        return [(A[s], b) for s in A], f"mean over {len(A)} seeds of A vs the single B run"
    if len(A) == 1 and len(B) > 1:
        a = next(iter(A.values()))
        return [(a, B[s]) for s in B], f"single A run vs mean over {len(B)} seeds of B"
    return [(next(iter(A.values())), next(iter(B.values())))], "single runs"


def build_plan(names):
    """(family, key, label, pairs, description)."""
    plan = []

    def add(family, cfg_a, cfg_b):
        pr = pairs_for(cfg_a, cfg_b, names)
        if pr is None:
            print(f"[plan] skipped {family}: {cfg_a} vs {cfg_b} (missing runs)")
            return
        pairs, desc = pr
        plan.append((family, f"{cfg_a}__vs__{cfg_b}", f"{LABEL.get(cfg_a, cfg_a)} vs {LABEL.get(cfg_b, cfg_b)}", pairs, desc))

    for fa, fb in [("gpt2_fk8", "gpt2_lora"), ("gpt2-medium_fk20", "gpt2-medium_lora"), ("opt-125m_fk8", "opt-125m_lora"),
                   ("opt-350m_fk20", "opt-350m_lora"), ("bert-base-uncased_fk8", "bert-base-uncased_lora"),
                   ("bert-large-uncased_fk20", "bert-large-uncased_lora")]:
        add("LoRA vs freezing", fa, fb)
    for base in ["sgd", "lightgbm", "xgboost", "lstm", "transformer", "ann"]:
        add("GPT2 vs baselines", "gpt2_fk8", base)
    add("pretrained vs scratch", "gpt2_fk8", "gpt2_scratch")
    for other in ["gpt2-medium_fk20", "opt-125m_fk8", "opt-350m_fk20", "bert-base-uncased_fk8", "bert-large-uncased_fk20"]:
        add("GPT2 vs other LLMs", "gpt2_fk8", other)
    return plan


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs_dir", help="AdditionalExperiment_StatTests/runs (reads <run>/preds.npz + metrics.json)")
    ap.add_argument("--reference", help="AdditionalExperiment_StatTests/audit/reference_forecasts.csv (required with --runs_dir)")
    ap.add_argument("--handover", help="Study3_StatTests_handover folder (alternative input)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--B", type=int, default=2000)
    ap.add_argument("--block", type=int, default=120, help="primary block length in windows (120 = about 5 h; the "
                    "loss-differential autocorrelation is about 0.1 at lag 60 and about 0.03 at lag 120)")
    ap.add_argument("--block_alt", type=int, nargs="*", default=[60, 240], help="sensitivity block lengths")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    t0 = time.time()
    os.makedirs(args.out, exist_ok=True)

    if args.runs_dir:
        assert args.reference, "--reference is required with --runs_dir"
        y, lab, ytrue_pref, P, Q, REP, pers, last, source = load_runs(args.runs_dir, args.reference)
    elif args.handover:
        y, lab, ytrue_pref, P, Q, REP, pers, last, source = load_handover(args.handover)
    else:
        sys.exit("give --runs_dir/--reference or --handover")
    names = sorted(P)
    T = y.shape[0]
    var_pop = ((y - y.mean(0)) ** 2).mean(0)
    print(f"[load] {source}: {T} test windows, {int(lab.sum())} labeled ({int(ytrue_pref[lab].sum())} positive); "
          f"{len(names)} runs: {', '.join(names)}")

    # ---- recompute every run's metrics and check them against the recorded ones
    M, L = {}, {}
    worst = 0.0
    for r in names:
        M[r] = full_metrics(P[r], Q[r], y, lab, ytrue_pref)
        L[r] = per_window_losses(P[r], y, var_pop)
        for k in METRICS:
            d = abs(M[r][k] - REP[r][k])
            worst = max(worst, d)
            assert d < 1e-8, f"{r} {k}: recomputed {M[r][k]} vs recorded {REP[r][k]}"
        assert abs(L[r]["MAE"].mean() - M[r]["MAE"]) < 1e-10 and abs(1.0 - L[r]["R2"].mean() - M[r]["R2"]) < 1e-10
    print(f"[check] all {len(names)} runs: recomputed RMSE/MAE/R2/Accuracy/F1/AUROC match the recorded metrics "
          f"(max abs diff {worst:.2e}); per-window MAE and R2 losses average to the overall metrics")

    # ---- references
    P["persistence"], Q["persistence"] = pers, last
    P["last_label"], Q["last_label"] = pers, last
    M["persistence"] = M["last_label"] = full_metrics(pers, last, y, lab, ytrue_pref)
    L["persistence"] = per_window_losses(pers, y, var_pop)
    print(f"[reference] persistence: RMSE {M['persistence']['RMSE']:.4f} MAE {M['persistence']['MAE']:.4f} "
          f"R2 {M['persistence']['R2']:.4f}; last visible label: Accuracy {M['last_label']['Accuracy']:.4f} "
          f"F1 {M['last_label']['F1']:.4f} AUROC {M['last_label']['AUROC']:.4f}")

    # ---- block resamples and per-run bootstrap metric vectors
    rng = np.random.default_rng(args.seed)
    blocks = [args.block] + [b for b in args.block_alt if b != args.block]
    IDX = {b: make_block_resamples(T, b, args.B, rng) for b in blocks}
    S = {b: {} for b in blocks}
    ST = {}
    all_names = names + ["persistence", "last_label"]
    for b in blocks:
        for r in all_names:
            S[b][r] = bootstrap_metrics(P[r], Q[r], y, lab, ytrue_pref, IDX[b])
            if b == args.block:
                ST[r] = bootstrap_per_target(P[r], y, IDX[b])
        print(f"[bootstrap] block {b}: {args.B} resamples x {len(all_names)} runs done ({time.time()-t0:.0f} s)")

    # ---- comparison plan
    plan = build_plan(names)
    comps = []   # (family, key, label, pairs, primary, description)
    for family, key, label, pairs, desc in plan:
        if len(pairs) > 1:
            for a, bb in pairs:
                sa, sb = config_of(a)[1], config_of(bb)[1]
                comps.append((family, key, f"{label} (seed {sa} vs {sb})", [(a, bb)], False, "single pair"))
            comps.append((family, key, f"{label} ({desc})", pairs, True, desc))
        else:
            comps.append((family, key, f"{label} ({desc})", pairs, True, desc))
    for r in names:
        comps.append(("vs persistence", f"{r}__vs__persistence", f"{r} vs persistence", [(r, "persistence")], True, "single run"))
        comps.append(("vs last label", f"{r}__vs__last_label", f"{r} vs last visible label", [(r, "last_label")], True, "single run"))
    for family, key, label, pairs, desc in plan:
        print(f"[plan] {family}: {label} -> {desc}: " + "; ".join(f"{a} vs {bb}" for a, bb in pairs))

    # ---- run every comparison
    rows = []
    for fam, key, label, pairs, primary, desc in comps:
        metrics_here = REG_METRICS if fam == "vs persistence" else CLS_METRICS if fam == "vs last label" else METRICS
        for k in metrics_here:
            a_val = np.mean([M[a][k] for a, _ in pairs])
            b_val = np.mean([M[bb][k] for _, bb in pairs])
            delta = np.mean([M[a][k] - M[bb][k] for a, bb in pairs])
            row = dict(family=fam, key=key, comparison=label, primary=primary, pairing=desc, metric=k,
                       n_pairs=len(pairs), runs_A=" ".join(a for a, _ in pairs), runs_B=" ".join(bb for _, bb in pairs),
                       A=a_val, B=b_val, delta=delta)
            for b in blocks:
                ds = np.mean([S[b][a][k] - S[b][bb][k] for a, bb in pairs], axis=0)
                lo, hi, p = boot_summary(ds)
                tag = "" if b == args.block else f"_block{b}"
                row[f"ci_lo{tag}"], row[f"ci_hi{tag}"], row[f"p_boot{tag}"] = lo, hi, p
            if k in ("MAE", "R2"):
                la = np.mean([L[a][k] for a, _ in pairs], axis=0)
                lb = np.mean([L[bb][k] for _, bb in pairs], axis=0)
                dm = dm_test(la, lb)
                row.update(dm_stat=dm["dm"], p_dm=dm["p"], dm_bandwidth=dm["m"], rho1=dm["rho1"], p_dm_m60=dm_test(la, lb, m=60)["p"])
            if k == "Accuracy" and len(pairs) == 1:
                a, bb = pairs[0]
                ca = ((Q[a] >= 0.5).astype(int) == ytrue_pref)[lab]
                cb = ((Q[bb] >= 0.5).astype(int) == ytrue_pref)[lab]
                mc = mcnemar_exact(ca, cb)
                row.update(p_mcnemar=mc["p"], mcnemar_b=mc["b"], mcnemar_c=mc["c"])
            if k == "AUROC" and len(pairs) == 1:
                a, bb = pairs[0]
                dl = delong_test(Q[a][lab], Q[bb][lab], ytrue_pref[lab])
                row.update(p_delong=dl["p"], delong_z=dl["z"])
            rows.append(row)
    df = pd.DataFrame(rows)

    # ---- Holm within family x metric over primary comparisons
    df["p_holm"] = np.nan
    for (fam, k), g in df[df["primary"]].groupby(["family", "metric"]):
        df.loc[g.index, "p_holm"] = holm(g["p_boot"].values)

    def favours(r):
        better_a = (r["delta"] > 0) == HIGHER_IS_BETTER[r["metric"]]
        if not np.isnan(r["p_holm"]) and r["p_holm"] < 0.05:
            return "A" if better_a else "B"
        if r["p_boot"] < 0.05:
            return ("A" if better_a else "B") + " (unadjusted only)"
        return "n.s."
    df["favours"] = df.apply(favours, axis=1)

    # per-pair consistency for the primary rows with several pairs
    df["pairs_sig_same_dir"] = ""
    for i, r in df[df["primary"] & (df["n_pairs"] > 1)].iterrows():
        per = df[(df["key"] == r["key"]) & (df["metric"] == r["metric"]) & (~df["primary"])]
        n_same = int(((per["p_boot"] < 0.05) & (np.sign(per["delta"]) == np.sign(r["delta"]))).sum())
        df.loc[i, "pairs_sig_same_dir"] = f"{n_same}/{len(per)}"
    df.to_csv(os.path.join(args.out, "comparisons.csv"), index=False)

    # ---- per-target bootstrap for the primary LoRA/baseline/scratch comparisons
    prow = []
    for fam, key, label, pairs, primary, desc in comps:
        if not primary or fam in ("vs persistence", "vs last label", "GPT2 vs other LLMs"):
            continue
        for k in ("RMSE", "R2"):
            for j, tname in enumerate(TARGETS):
                a_val = np.mean([M[a]["per_target"][k][j] for a, _ in pairs])
                b_val = np.mean([M[bb]["per_target"][k][j] for _, bb in pairs])
                ds = np.mean([ST[a][k][:, j] - ST[bb][k][:, j] for a, bb in pairs], axis=0)
                lo, hi, p = boot_summary(ds)
                prow.append(dict(family=fam, comparison=label, metric=k, target=tname, A=a_val, B=b_val,
                                 delta=a_val - b_val, ci_lo=lo, ci_hi=hi, p_boot=p))
    pt = pd.DataFrame(prow)
    pt["p_holm"] = np.nan
    for (fam, lbl, k), g in pt.groupby(["family", "comparison", "metric"]):
        pt.loc[g.index, "p_holm"] = holm(g["p_boot"].values)
    pt.to_csv(os.path.join(args.out, "per_target.csv"), index=False)

    # ---- references table
    df[df["family"].isin(["vs persistence", "vs last label"])].to_csv(os.path.join(args.out, "references.csv"), index=False)

    # ---- dissertation table: primary rows of the four comparison families, one line per comparison x metric
    dt = df[df["primary"] & ~df["family"].isin(["vs persistence", "vs last label"])].copy()
    dt["p_boot_txt"] = dt["p_boot"].map(lambda p: "<0.001" if p < 0.001 else f"{p:.3f}")
    dt["p_holm_txt"] = dt["p_holm"].map(lambda p: "<0.001" if p < 0.001 else f"{p:.3f}")
    dt["ci_txt"] = dt.apply(lambda r: f"[{r['ci_lo']:+.3f}, {r['ci_hi']:+.3f}]", axis=1)
    dt[["family", "comparison", "metric", "n_pairs", "A", "B", "delta", "ci_txt", "p_boot_txt", "p_holm_txt",
        "favours", "pairs_sig_same_dir"]].to_csv(os.path.join(args.out, "dissertation_table.csv"), index=False)

    # ---- summary
    lines = [f"# Paired tests on the Study 3 re-run predictions (source: {source}; B={args.B}, block={args.block} windows, "
             f"sensitivity blocks {args.block_alt}, seed {args.seed})\n",
             "Delta = A - B (mean over pairs where n>1). Lower is better for RMSE/MAE; higher for R2/Accuracy/F1/AUROC. "
             "p_boot = paired moving-block bootstrap; p_dm = Diebold-Mariano (HAC) on per-window losses; "
             "p_holm = Holm over the primary comparisons within family x metric; favours = A or B after Holm.\n"]
    for fam in ["LoRA vs freezing", "GPT2 vs baselines", "pretrained vs scratch", "GPT2 vs other LLMs"]:
        lines.append(f"\n## {fam}\n")
        lines.append("| comparison | metric | A | B | Delta | 95% CI | p_boot | p_dm | p_holm | favours | pairs sig. same dir. |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for _, r in df[(df.family == fam) & (df.primary)].iterrows():
            pdm = "" if np.isnan(r.get("p_dm", np.nan)) else f"{r['p_dm']:.3g}"
            lines.append(f"| {r['comparison']} | {r['metric']} | {r['A']:.4f} | {r['B']:.4f} | {r['delta']:+.4f} | "
                         f"[{r['ci_lo']:+.4f}, {r['ci_hi']:+.4f}] | {r['p_boot']:.3g} | {pdm} | {r['p_holm']:.3g} | "
                         f"{r['favours']} | {r['pairs_sig_same_dir']} |")
    lines.append("\n## Every run vs the no-skill references (primary block)\n")
    lines.append("| run | RMSE run | RMSE persist. | p_boot | R2 run | R2 persist. | p_boot | Acc run | Acc last-label | p_boot | "
                 "F1 run | F1 last-label | p_boot | AUROC run | AUROC last-label | p_boot |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in names:
        g = df[df.comparison.isin([f"{r} vs persistence", f"{r} vs last visible label"])].set_index("metric")
        lines.append(f"| {r} | {g.loc['RMSE','A']:.3f} | {g.loc['RMSE','B']:.3f} | {g.loc['RMSE','p_boot']:.2g} | "
                     f"{g.loc['R2','A']:.3f} | {g.loc['R2','B']:.3f} | {g.loc['R2','p_boot']:.2g} | "
                     f"{g.loc['Accuracy','A']:.3f} | {g.loc['Accuracy','B']:.3f} | {g.loc['Accuracy','p_boot']:.2g} | "
                     f"{g.loc['F1','A']:.3f} | {g.loc['F1','B']:.3f} | {g.loc['F1','p_boot']:.2g} | "
                     f"{g.loc['AUROC','A']:.3f} | {g.loc['AUROC','B']:.3f} | {g.loc['AUROC','p_boot']:.2g} |")
    prim = df[df.primary & ~df.family.isin(["vs persistence", "vs last label"])]
    for b in blocks[1:]:
        flips = int(((prim["p_boot"] < 0.05) != (prim[f"p_boot_block{b}"] < 0.05)).sum())
        lines.append(f"\nSensitivity: {flips} of {len(prim)} primary verdicts (5% level, unadjusted) change between block "
                     f"{args.block} and block {b}.")
    dmagree = prim.dropna(subset=["p_dm"])
    agree = ((dmagree["p_boot"] < 0.05) == (dmagree["p_dm"] < 0.05)).mean()
    lines.append(f"Agreement of the DM test with the bootstrap on MAE/R2 verdicts (5% level): {agree:.0%} of {len(dmagree)}.")
    single = df.dropna(subset=["p_mcnemar"])
    if len(single):
        lines.append(f"Agreement of McNemar with the bootstrap on accuracy (single-pair rows): "
                     f"{((single['p_mcnemar'] < 0.05) == (single['p_boot'] < 0.05)).mean():.0%} of {len(single)}.")
    single = df.dropna(subset=["p_delong"])
    if len(single):
        lines.append(f"Agreement of DeLong with the bootstrap on AUROC (single-pair rows): "
                     f"{((single['p_delong'] < 0.05) == (single['p_boot'] < 0.05)).mean():.0%} of {len(single)}.")
    summary = "\n".join(lines)
    with open(os.path.join(args.out, "summary.md"), "w") as f:
        f.write(summary + "\n")
    print(summary)
    print(f"\n[done] wrote comparisons.csv, per_target.csv, references.csv, dissertation_table.csv, summary.md to {args.out} "
          f"in {time.time()-t0:.0f} s")


if __name__ == "__main__":
    sys.exit(main())
