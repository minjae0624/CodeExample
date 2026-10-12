"""
Decision Logic Layer
====================
Reads the latest ae_snapshot per zone every 5 minutes, runs the
three-stage pipeline (Veto → Confidence Score → LLM), applies
action inhibition, and writes the decision to decision_log.

On QUERY     → writes a row to feedback_prompt (Active Feedback Agent)
On INTERVENE → calls intervention_layer.run_intervention()
"""

import pymysql
import pymysql.cursors
import traceback
import atexit
import json
import os
import re
import time
import requests
from datetime import datetime, timedelta, date
# from intervention_layer import run_intervention, monitor_termination  # kept for future use

import numpy as np
import pytz
import anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

# =============================================================
# CONFIG
# =============================================================

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "user":     os.environ.get("DB_USER", ""),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", ""),
    "cursorclass": pymysql.cursors.DictCursor,
}

EASTERN = pytz.timezone("America/New_York")

# Anthropic: set ANTHROPIC_API_KEY in the environment (never hardcode it)
ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL             = "claude-sonnet-4-6"       # Decision Logic Layer
FEEDBACK_AGENT_MODEL  = "claude-haiku-4-5-20251001"  # Active Feedback Agent
RAG_TOP_K             = 5    # number of similar past snapshots to retrieve
FA_RAG_TOP_K          = 5    # past prompt records for feedback agent RAG

# Zones to evaluate (must match zone values in ae_snapshot)
ALL_ZONES = ["R-Lab", "Graduate Studio"]

# Feedback UI — push notification endpoint
FEEDBACK_UI_URL  = os.environ.get("FEEDBACK_UI_URL", "http://localhost:5001")
NOTIFY_API_KEY   = os.environ.get("NOTIFY_API_KEY", "")  # must match NOTIFY_API_KEY in app.py

# Scheduler: run every 5 minutes, 30s after the analytical engine
EVAL_INTERVAL_MINUTES = 30
STARTUP_DELAY_SECONDS = 30   # offset so snapshot is written before we read it

# =============================================================
# THRESHOLDS
# =============================================================

# --- Veto ---
VETO_NO_FEEDBACK_COUNT = 0   # real_comfort_count == 0 → only hard stop

# --- Confidence tiers ---
CONFIDENCE_LOW    = 0.4
CONFIDENCE_HIGH   = 0.7

# --- Confidence signal weights (must sum to 1.0) ---
WEIGHTS = {
    "c_cov":   0.25,   # coverage is most critical — no data = no confidence
    "c_fresh": 0.20,   # freshness promoted: stale data is unreliable
    "c_agree": 0.15,   # agreement still important but not equal to coverage
    "c_env":   0.15,   # env anomalies are a direct action trigger — keep
    "c_pref":  0.13,   # declining trend is highly actionable — was underweighted
    "c_fb":    0.07,   # overlaps with c_agree; reduced
    "c_ha":    0.05,   # secondary/indirect; can mislead during low-occupancy
}

# --- Inhibition ---
INHIBITION_SAME_ACTION_MINUTES = 15   # block same action within this window
QUERY_PENDING_BLOCK             = True # block new QUERY if one is already pending/delivered

# --- HIGH tier: INTERVENE requires both env anomaly AND occupant discomfort ---
INTERVENE_DISCOMFORT_THRESHOLD = 3.0   # feedback mean below this = discomfort

# =============================================================
# PHASE 3 EXPERIMENT — V2 ZONE-LEVEL AB/BA CROSSOVER
# =============================================================
# See MDS/EXPERIMENTAL_PLAN_V2.md for full rationale.
#
# Design: architecture (2-agent vs 1-agent) is the only live factor.
# Zones swap architectures between two 7-day periods:
#
#   Period 1 (2026-04-20 → 2026-04-26): R-Lab = 2-agent, Grad Studio = 1-agent
#   Period 2 (2026-04-27 → 2026-05-03): zones swap
#
# Every user sees both architectures. Prompts are always personalized (no
# template arm). No ablation, no daily rotation. `ablation_label` stays NULL
# in decision_log / feedback_prompt for all Phase 3 rows.

PHASE3_START         = date(2026, 4, 20)   # Phase 3 Period 1 begins (Mon)
PHASE3_PERIOD2_START = date(2026, 4, 27)   # Phase 3 Period 2 begins (Mon)
PHASE3_END           = date(2026, 5,  3)   # Experiment ends (Sun, informational)
PHASE3_ENABLED       = False                # flip to True on 2026-04-20

# Zone × period architecture assignment. Period 1 row is used from
# PHASE3_START up to (but not including) PHASE3_PERIOD2_START; Period 2 row
# is used from PHASE3_PERIOD2_START onward.
PHASE3_ZONE_ARCH = {
    "period1": {"R-Lab": "two_agent", "Graduate Studio": "one_agent"},
    "period2": {"R-Lab": "one_agent", "Graduate Studio": "two_agent"},
}

def get_experiment_condition(zone):
    """
    Returns (architecture, prompt_style, ablation_label) for this zone + today.

    V2: architecture is determined by zone × crossover period. No weekday
    rotation, no weekend freeze (crossover is week-level, not day-level).
    prompt_style is always "personalized"; ablation_label is always None.

    If PHASE3_ENABLED is False, returns the default 2-agent personalized —
    matches pre-Phase-3 behavior so the dress rehearsal can exercise the
    full pipeline without committing to an arm.
    """
    if not PHASE3_ENABLED:
        return "two_agent", "personalized", None

    today = date.today()
    period = "period2" if today >= PHASE3_PERIOD2_START else "period1"
    arch = PHASE3_ZONE_ARCH[period].get(zone, "two_agent")
    return arch, "personalized", None

# =============================================================
# DB HELPERS
# =============================================================

def get_db():
    try:
        return pymysql.connect(**DB_CONFIG)
    except Exception as e:
        print(f"[DLL] DB connection failed: {e}")
        return None


def log_llm_call(conn, architecture_condition, call_role, model, input_tokens,
                  output_tokens, latency_ms):
    """Log token usage and latency for every LLM call during Phase 3."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO llm_call_log
                    (called_at, architecture_condition, call_role, model,
                     input_tokens, output_tokens, latency_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (datetime.now(), architecture_condition, call_role, model,
                 input_tokens, output_tokens, latency_ms)
            )
        conn.commit()
    except Exception as e:
        print(f"[DLL] Token log failed (non-critical): {e}")


def get_latest_snapshot(conn, zone):
    """Return the most recent ae_snapshot row for this zone, or None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM ae_snapshot
            WHERE zone = %s
            ORDER BY snapshot_time DESC
            LIMIT 1
            """,
            (zone,)
        )
        return cur.fetchone()


# =============================================================
# STAGE 1 — VETO CHECK
# =============================================================

def veto_check(snap):
    """
    Returns (triggered: bool, reason: str | None).
    Only hard stop: zero real feedback submissions.
    All other conditions (low coverage, high anomaly count) are handled
    naturally by the confidence score — C_cov and C_env already pull
    the score down toward LOW tier → QUERY when data is thin or noisy.
    """
    feedback_cnt = snap.get("real_comfort_count") or 0

    if feedback_cnt <= VETO_NO_FEEDBACK_COUNT:
        return True, "no_feedback"

    return False, None


# =============================================================
# STAGE 2 — CONFIDENCE SCORING
# =============================================================

def compute_signals(snap):
    """
    Compute the seven analytical signals from ae_snapshot columns.
    Each signal is in [0, 1].
    """
    # C_cov: feedback coverage ratio
    c_cov = float(snap.get("fr_real_coverage") or 0.0)
    c_cov = max(0.0, min(1.0, c_cov))

    # C_agree: 1 - avg(MAD across dimensions) / 4
    mads = [
        snap.get("div_real_comfort_mad") or 0.0,
        snap.get("div_real_health_mad")  or 0.0,
        snap.get("div_real_emotion_mad") or 0.0,
    ]
    avg_mad = sum(mads) / len(mads)
    c_agree = max(0.0, 1.0 - avg_mad / 4.0)

    # C_fresh: 1 - (stale_count / actor_count)
    actor_count = snap.get("fr_actor_count") or 0
    stale_count = snap.get("fr_stale_count") or 0
    if actor_count > 0:
        c_fresh = max(0.0, 1.0 - stale_count / actor_count)
    else:
        c_fresh = 0.0

    # C_env: 1 / (1 + env_anomaly_count)
    ea_count = snap.get("ea_event_count") or 0
    c_env = 1.0 / (1.0 + ea_count)

    # C_fb: 1 / (1 + feedback_anomaly_count)
    fa_count = snap.get("fa_event_count") or 0
    c_fb = 1.0 / (1.0 + fa_count)

    # C_ha: 1 - avg(|bias|) / 4
    biases = [
        abs(snap.get("ha_comfort_bias") or 0.0),
        abs(snap.get("ha_health_bias")  or 0.0),
        abs(snap.get("ha_emotion_bias") or 0.0),
    ]
    avg_bias = sum(biases) / len(biases)
    c_ha = max(0.0, 1.0 - avg_bias / 4.0)

    # C_pref: preference trend direction
    pt_flag = (snap.get("pt_flag") or "").lower()
    if "decline" in pt_flag:
        c_pref = 0.0
    elif "improv" in pt_flag:
        c_pref = 1.0
    else:
        c_pref = 0.7  # stable or unknown

    return {
        "c_cov":   round(c_cov,   4),
        "c_agree": round(c_agree, 4),
        "c_fresh": round(c_fresh, 4),
        "c_env":   round(c_env,   4),
        "c_fb":    round(c_fb,    4),
        "c_ha":    round(c_ha,    4),
        "c_pref":  round(c_pref,  4),
    }


def compute_confidence(signals):
    """
    Weighted sum of 7 signals → score and tier.
    Returns (score: float, tier: str)
    """
    score = sum(WEIGHTS[k] * signals[k] for k in WEIGHTS)
    score = round(score, 4)

    if score < CONFIDENCE_LOW:
        tier = "low"
    elif score > CONFIDENCE_HIGH:
        tier = "high"
    else:
        tier = "medium"

    return score, tier


# =============================================================
# ACTION ROUTING
# =============================================================

def high_tier_action(snap):
    """
    HIGH confidence tier: rule-based routing.
    INTERVENE if both env anomaly exists AND occupant feedback shows discomfort.
    Otherwise IDLE.
    """
    ea_count = snap.get("ea_event_count") or 0
    if ea_count == 0:
        return "idle"

    comfort = snap.get("real_comfort_mean")
    health  = snap.get("real_health_mean")
    emotion = snap.get("real_emotion_mean")

    valid = [v for v in [comfort, health, emotion] if v is not None]
    if not valid:
        return "idle"

    avg_feedback = sum(valid) / len(valid)
    if avg_feedback < INTERVENE_DISCOMFORT_THRESHOLD:
        return "recommend"

    return "idle"


def signals_to_vector(signals):
    """Convert signal dict to a numpy feature vector (ordered consistently)."""
    keys = ["c_cov", "c_agree", "c_fresh", "c_env", "c_fb", "c_ha", "c_pref"]
    return np.array([float(signals.get(k) or 0.0) for k in keys], dtype=float)


def cosine_similarity(a, b):
    """Cosine similarity between two 1-D numpy arrays."""
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def fetch_rag_history(conn, zone, current_vec, top_k=RAG_TOP_K):
    """
    Retrieve the top-k most similar past decision_log entries to the
    current analytical state using cosine similarity on the 7 signals.

    Returns a list of dicts, each containing:
      signals, confidence_score, final_action, evaluated_at,
      outcome_summary (derived from subsequent actuation_log / feedback_prompt rows)
    """
    with conn.cursor() as cur:
        # Pull all past MEDIUM-tier decisions that have all 7 signals
        cur.execute(
            """
            SELECT dl.id, dl.evaluated_at, dl.zone,
                   dl.c_cov, dl.c_agree, dl.c_fresh, dl.c_env,
                   dl.c_fb, dl.c_ha, dl.c_pref,
                   dl.confidence_score, dl.final_action,
                   dl.snapshot_id
            FROM decision_log dl
            WHERE dl.confidence_tier = 'medium'
              AND dl.c_cov IS NOT NULL
            ORDER BY dl.evaluated_at DESC
            LIMIT 200
            """,
        )
        rows = cur.fetchall()

    if not rows:
        return []

    # Score by cosine similarity
    scored = []
    for r in rows:
        hist_signals = {
            "c_cov":   r["c_cov"],   "c_agree": r["c_agree"],
            "c_fresh": r["c_fresh"], "c_env":   r["c_env"],
            "c_fb":    r["c_fb"],    "c_ha":    r["c_ha"],
            "c_pref":  r["c_pref"],
        }
        hist_vec = signals_to_vector(hist_signals)
        sim = cosine_similarity(current_vec, hist_vec)
        scored.append((sim, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    # Enrich with outcome info
    results = []
    for sim, r in top:
        outcome = _fetch_outcome(conn, r["id"], r["snapshot_id"], r["final_action"])
        results.append({
            "similarity":       round(sim, 4),
            "evaluated_at":     str(r["evaluated_at"]),
            "zone":             r["zone"],
            "signals": {
                "c_cov":   r["c_cov"],   "c_agree": r["c_agree"],
                "c_fresh": r["c_fresh"], "c_env":   r["c_env"],
                "c_fb":    r["c_fb"],    "c_ha":    r["c_ha"],
                "c_pref":  r["c_pref"],
            },
            "confidence_score": r["confidence_score"],
            "action_taken":     r["final_action"],
            "outcome":          outcome,
        })

    return results


def _fetch_outcome(conn, decision_log_id, snapshot_id, action):
    """Build a human-readable outcome summary for a past decision."""
    outcome_parts = []

    with conn.cursor() as cur:
        if action == "recommend":
            cur.execute(
                """
                SELECT issue_type, severity, suggested_action, status,
                       contributing_feedback_count
                FROM facility_recommendation
                WHERE decision_log_id = %s
                LIMIT 3
                """,
                (decision_log_id,)
            )
            rows = cur.fetchall()
            for r in rows:
                parts = [f"issue={r['issue_type']}", f"severity={r['severity']}"]
                if r["contributing_feedback_count"]:
                    parts.append(f"based on {r['contributing_feedback_count']} feedback reports")
                parts.append(f"status={r['status']}")
                outcome_parts.append(", ".join(parts))

        elif action == "query":
            cur.execute(
                """
                SELECT uncertainty_type,
                       COUNT(*) as prompts_sent,
                       SUM(status = 'responded') as responded
                FROM feedback_prompt
                WHERE decision_log_id = %s
                GROUP BY uncertainty_type
                """,
                (decision_log_id,)
            )
            row = cur.fetchone()
            if row:
                outcome_parts.append(
                    f"type={row['uncertainty_type']}, "
                    f"sent={row['prompts_sent']}, "
                    f"responded={row['responded'] or 0}"
                )

    return "; ".join(outcome_parts) if outcome_parts else "no outcome data yet"


def build_llm_prompt(snap, signals, confidence_score, zone, rag_history):
    """
    Build the structured prompt for the LLM reasoning agent.
    """
    def fmt(v, d=3):
        return f"{v:.{d}f}" if v is not None else "N/A"

    # Current state summary
    current_block = f"""CURRENT ANALYTICAL STATE
Zone: {zone}
Confidence Score: {fmt(confidence_score)} (MEDIUM tier — 0.4 to 0.7)

Signal Breakdown:
  C_cov   (feedback coverage)       = {fmt(signals['c_cov'])}
  C_agree (occupant agreement/MAD)  = {fmt(signals['c_agree'])}
  C_fresh (feedback recency)        = {fmt(signals['c_fresh'])}
  C_env   (environmental stability) = {fmt(signals['c_env'])}
  C_fb    (feedback consistency)    = {fmt(signals['c_fb'])}
  C_ha    (human-agent alignment)   = {fmt(signals['c_ha'])}
  C_pref  (preference trend)        = {fmt(signals['c_pref'])}

Raw Environmental:
  Temperature = {fmt(snap.get('ieq_temperature'))}°F
  Humidity    = {fmt(snap.get('ieq_humidity'))}%
  CO2         = {fmt(snap.get('ieq_co2'))} ppm
  VOC         = {fmt(snap.get('ieq_voc'))} ppb
  PM2.5       = {fmt(snap.get('ieq_pm2_5'))} µg/m³

Occupant Feedback (real):
  Comfort = {fmt(snap.get('real_comfort_mean'))} (n={snap.get('real_comfort_count') or 0})
  Health  = {fmt(snap.get('real_health_mean'))}  (n={snap.get('real_health_count') or 0})
  Emotion = {fmt(snap.get('real_emotion_mean'))}  (n={snap.get('real_emotion_count') or 0})

Divergence: {snap.get('div_real_label') or 'N/A'}
Env Anomaly: {snap.get('ea_flag') or 'N/A'} ({snap.get('ea_event_count') or 0} events)
Feedback Anomaly: {snap.get('fa_flag') or 'N/A'} ({snap.get('fa_event_count') or 0} events)
Preference Trend: {snap.get('pt_flag') or 'N/A'}"""

    # RAG history block
    if rag_history:
        history_lines = []
        for i, h in enumerate(rag_history, 1):
            history_lines.append(
                f"  [{i}] similarity={h['similarity']:.3f} | "
                f"action={h['action_taken']} | "
                f"C={fmt(h['confidence_score'])} | "
                f"outcome: {h['outcome']}"
            )
        history_block = "SIMILAR PAST SITUATIONS (retrieved by cosine similarity on signals)\n" + \
                        "\n".join(history_lines)
    else:
        history_block = "SIMILAR PAST SITUATIONS\n  No historical data available yet."

    instructions = """TASK
You are the reasoning agent for a human-centered building intelligence system.
The system has reached a MEDIUM confidence state — the analytical signals are ambiguous,
and a deterministic rule is insufficient. You must select one of three actions:

  recommend — Generate a facility recommendation to building management
               identifying the environmental issue and suggested corrective action.
               Use when: environmental anomalies are present AND occupant feedback
               confirms discomfort, even if signals are mixed.

  query     — Send a targeted feedback prompt to one or two occupants to
               resolve analytical uncertainty.
               Use when: coverage, freshness, or agreement is weak enough that
               acting on current data would be unreliable.

  idle      — Take no action this cycle.
               Use when: conditions are ambiguous but no clear trigger exists,
               or a recent action already addressed the situation.

Base your reasoning on the current analytical state AND the similar past situations.
Consider what actions worked (or did not work) in comparable states.

Respond in this exact format:
ACTION: <recommend|query|idle>
REASONING: <2-4 sentences explaining your decision, referencing specific signals and past outcomes>"""

    return f"{current_block}\n\n{history_block}\n\n{instructions}"


def llm_action(conn, snap, signals, confidence_score, zone, arch_condition="two_agent"):
    """
    MEDIUM confidence tier: LLM Reasoning Agent with RAG.
    Embeds current state, retrieves similar past decisions, calls Claude.
    Falls back to heuristic if LLM is unavailable.
    """
    # Build feature vector for RAG retrieval
    current_vec = signals_to_vector(signals)

    # Retrieve similar historical decisions
    rag_history = fetch_rag_history(conn, zone, current_vec)

    # Build prompt
    prompt = build_llm_prompt(snap, signals, confidence_score, zone, rag_history)

    # Call LLM
    if not ANTHROPIC_API_KEY:
        print("[DLL] ANTHROPIC_API_KEY not set — using heuristic fallback")
        return _llm_heuristic_fallback(signals), "[FALLBACK] No API key configured."

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        t0 = time.time()
        message = client.messages.create(
            model=LLM_MODEL,
            max_tokens=256,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )
        latency = int((time.time() - t0) * 1000)
        response_text = message.content[0].text.strip()

        # Log token usage
        log_llm_call(conn, arch_condition, "decision", LLM_MODEL,
                      message.usage.input_tokens, message.usage.output_tokens, latency)

        # Parse ACTION and REASONING
        action    = "idle"
        reasoning = response_text

        for line in response_text.splitlines():
            if line.startswith("ACTION:"):
                raw = line.split(":", 1)[1].strip().lower()
                if raw in ("recommend", "query", "idle"):
                    action = raw
            elif line.startswith("REASONING:"):
                reasoning = line.split(":", 1)[1].strip()

        return action, reasoning

    except Exception as e:
        print(f"[DLL] LLM call failed: {e}")
        action = _llm_heuristic_fallback(signals)
        return action, f"[LLM ERROR: {e}] Heuristic fallback used."


def _llm_heuristic_fallback(signals):
    """Simple fallback when LLM is unavailable."""
    if signals["c_cov"] < 0.3 or signals["c_fresh"] < 0.4:
        return "query"
    if signals["c_env"] < 0.3 and signals["c_fb"] > 0.5:
        return "recommend"
    return "idle"


# =============================================================
# 1-AGENT COMBINED CALL (decision + targeting in single Sonnet)
# =============================================================

def build_single_agent_prompt(snap, signals, confidence_score, zone, rag_history,
                               candidates_info, uncertainty_type, prompt_rag_history):
    """
    Build a combined prompt for the 1-agent condition that handles both
    the decision (QUERY/RECOMMEND/IDLE) AND targeting (who to ask + message)
    in a single Sonnet call.
    """
    def fmt(v, d=3):
        return f"{v:.{d}f}" if v is not None else "N/A"

    # --- Analytical state (same as Decision Agent prompt) ---
    state_block = f"""CURRENT ANALYTICAL STATE
Zone: {zone}
Confidence Score: {fmt(confidence_score)} (MEDIUM tier — 0.4 to 0.7)

Signal Breakdown:
  C_cov   (feedback coverage)       = {fmt(signals['c_cov'])}
  C_agree (occupant agreement/MAD)  = {fmt(signals['c_agree'])}
  C_fresh (feedback recency)        = {fmt(signals['c_fresh'])}
  C_env   (environmental stability) = {fmt(signals['c_env'])}
  C_fb    (feedback consistency)    = {fmt(signals['c_fb'])}
  C_ha    (human-agent alignment)   = {fmt(signals['c_ha'])}
  C_pref  (preference trend)        = {fmt(signals['c_pref'])}

Raw Environmental:
  Temperature = {fmt(snap.get('ieq_temperature'))}°F
  Humidity    = {fmt(snap.get('ieq_humidity'))}%
  CO2         = {fmt(snap.get('ieq_co2'))} ppm
  VOC         = {fmt(snap.get('ieq_voc'))} ppb
  PM2.5       = {fmt(snap.get('ieq_pm2_5'))} µg/m³

Occupant Feedback (real):
  Comfort = {fmt(snap.get('real_comfort_mean'))} (n={snap.get('real_comfort_count') or 0})
  Health  = {fmt(snap.get('real_health_mean'))}  (n={snap.get('real_health_count') or 0})
  Emotion = {fmt(snap.get('real_emotion_mean'))}  (n={snap.get('real_emotion_count') or 0})

Divergence: {snap.get('div_real_label') or 'N/A'}
Env Anomaly: {snap.get('ea_flag') or 'N/A'} ({snap.get('ea_event_count') or 0} events)
Feedback Anomaly: {snap.get('fa_flag') or 'N/A'} ({snap.get('fa_event_count') or 0} events)
Preference Trend: {snap.get('pt_flag') or 'N/A'}
Uncertainty Type: {uncertainty_type}"""

    # --- RAG decision history ---
    if rag_history:
        history_lines = []
        for i, h in enumerate(rag_history, 1):
            history_lines.append(
                f"  [{i}] similarity={h['similarity']:.3f} | "
                f"action={h['action_taken']} | "
                f"C={fmt(h['confidence_score'])} | "
                f"outcome: {h['outcome']}"
            )
        history_block = "SIMILAR PAST DECISIONS\n" + "\n".join(history_lines)
    else:
        history_block = "SIMILAR PAST DECISIONS\n  No historical data available yet."

    # --- Candidate occupants ---
    cand_lines = []
    for c in candidates_info:
        cand_lines.append(
            f"  - {c['username']} (RAG match score: {c['rag_score']})\n"
            f"    Avg feedback: comfort={c['avg_comfort']}/5, health={c['avg_health']}/5, emotion={c['avg_emotion']}/5\n"
            f"    IEQ sensitivities — temp: {c['temp_sensitivity']}, humidity: {c['humidity_sensitivity']}, "
            f"CO2: {c['co2_sensitivity']}, VOC: {c['voc_sensitivity']}\n"
            f"    Submission recency score: {c['recency_score']} (1.0 = very recent)"
        )
    cand_text = "\n".join(cand_lines) if cand_lines else "  None available."

    # --- Past prompt response rates ---
    rate_lines = []
    for r in prompt_rag_history.get("response_rates", []):
        total = r["total_prompted"] or 1
        rate = round(r["responded_count"] / total * 100)
        rate_lines.append(f"  - {r['username']}: {r['responded_count']}/{r['total_prompted']} ({rate}%)")
    rate_text = "\n".join(rate_lines) if rate_lines else "  No history yet."

    instructions = """TASK
You are the sole reasoning agent for a human-centered building intelligence system.
You must perform TWO tasks in a single response:

TASK 1 — DECIDE AN ACTION:
  recommend — Generate a facility recommendation (env anomaly + occupant discomfort)
  query     — Send a targeted feedback prompt to resolve uncertainty
  idle      — Take no action this cycle

TASK 2 — IF ACTION IS "query", SELECT TARGETS:
  Pick 1-2 occupants from the candidates above and write a personalized 1-2 sentence message
  for each. Reference their IEQ sensitivity profile or the current conditions.

Respond ONLY with valid JSON — no markdown, no extra text:
{
  "action": "recommend|query|idle",
  "reasoning": "2-4 sentences explaining your decision",
  "targets": [
    {
      "username": "...",
      "message": "...",
      "reason": "..."
    }
  ]
}

If action is NOT "query", set "targets" to an empty array []."""

    return (f"{state_block}\n\n{history_block}\n\n"
            f"CANDIDATE OCCUPANTS (pre-filtered, ranked by IEQ sensitivity match)\n{cand_text}\n\n"
            f"PAST PROMPT RESPONSE RATES\n{rate_text}\n\n"
            f"{instructions}")


def single_agent_action(conn, snap, signals, confidence_score, zone,
                         candidates, uncertainty_type,
                         prompt_style="personalized"):
    """
    1-AGENT condition: single Sonnet call that handles BOTH the decision
    (QUERY/RECOMMEND/IDLE) and targeting (who to ask + personalized message).
    Falls back to heuristic + template on any failure.

    When prompt_style == "template", the LLM still decides the action and picks
    targets, but the per-target message text is replaced with the canned
    PROMPT_MESSAGES template for the uncertainty_type (so the prompt-style
    factor of the 2x2 is preserved inside the 1-agent arm).

    Returns (action, reasoning, targets_list).
    targets_list is a list of dicts with {id, username, rag_score, message, llm_reasoning, llm_used}.
    """
    # Build RAG context for decision history
    current_vec = signals_to_vector(signals)
    rag_history = fetch_rag_history(conn, zone, current_vec)

    # Build candidate profiles (same as 2-agent would)
    candidates_info = [describe_candidate(conn, u, snap) for u in candidates] if candidates else []
    prompt_rag_hist = fetch_prompt_rag_history(conn, zone, top_k=FA_RAG_TOP_K)

    prompt = build_single_agent_prompt(
        snap, signals, confidence_score, zone, rag_history,
        candidates_info, uncertainty_type, prompt_rag_hist
    )

    username_to_user = {u["username"]: u for u in candidates} if candidates else {}

    if not ANTHROPIC_API_KEY:
        action = _llm_heuristic_fallback(signals)
        return action, "[FALLBACK] No API key.", []

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        t0 = time.time()
        message = client.messages.create(
            model=LLM_MODEL,
            max_tokens=800,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )
        latency = int((time.time() - t0) * 1000)
        raw_text = message.content[0].text.strip()

        log_llm_call(conn, "one_agent", "combined", LLM_MODEL,
                      message.usage.input_tokens, message.usage.output_tokens, latency)

        # Strip markdown fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```", 2)[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        result = json.loads(raw_text)
        action = result.get("action", "idle").lower()
        if action not in ("recommend", "query", "idle"):
            action = "idle"
        reasoning = result.get("reasoning", "")

        # In template style, override the LLM-generated per-target message
        # with the canned copy for this uncertainty_type (for post_anomaly,
        # inject the specific variable/time context).
        template_msg = (_build_post_anomaly_message(snap)
                        if uncertainty_type == "post_anomaly"
                        else PROMPT_MESSAGES.get(uncertainty_type, ""))

        targets = []
        if action == "query":
            for t in result.get("targets", []):
                uname = t.get("username")
                if uname not in username_to_user:
                    continue
                u = username_to_user[uname]
                if prompt_style == "template":
                    msg       = template_msg
                    llm_used  = 0   # message itself isn't LLM-generated
                else:
                    msg       = t.get("message", template_msg)
                    llm_used  = 1
                targets.append({
                    "id":           u["id"],
                    "username":     uname,
                    "rag_score":    u.get("rag_score"),
                    "message":      msg,
                    "llm_reasoning": f"{reasoning} | {t.get('reason', '')}".strip(" |"),
                    "llm_used":     llm_used,
                })

        return action, reasoning, targets

    except Exception as e:
        print(f"[DLL] 1-agent call failed: {e}")
        action = _llm_heuristic_fallback(signals)
        return action, f"[LLM ERROR: {e}] Heuristic fallback used.", []


# =============================================================
# ACTION INHIBITION
# =============================================================

def inhibition_check(conn, zone, proposed_action):
    """
    Returns (inhibited: bool, reason: str | None).
    Blocks the proposed action if inhibition conditions are met.
    """
    with conn.cursor() as cur:

        # 1. Same action taken within cooldown window
        cutoff = datetime.now() - timedelta(minutes=INHIBITION_SAME_ACTION_MINUTES)
        cur.execute(
            """
            SELECT id FROM decision_log
            WHERE zone = %s
              AND final_action = %s
              AND evaluated_at >= %s
            LIMIT 1
            """,
            (zone, proposed_action, cutoff)
        )
        if cur.fetchone():
            return True, f"same_action_cooldown({proposed_action})"

        # 2. Block new QUERY if a prior prompt for this zone is still pending/delivered
        if proposed_action == "query" and QUERY_PENDING_BLOCK:
            cur.execute(
                """
                SELECT id FROM feedback_prompt
                WHERE zone = %s
                  AND status IN ('pending', 'delivered')
                  AND prompt_timestamp >= %s
                LIMIT 1
                """,
                (zone, datetime.now() - timedelta(minutes=30))
            )
            if cur.fetchone():
                return True, "query_already_pending"

    return False, None


# =============================================================
# WRITE DECISION
# =============================================================

def save_decision(conn, evaluated_at, zone, snapshot_id,
                  veto_triggered, veto_reason,
                  signals, confidence_score, confidence_tier,
                  llm_used, llm_reasoning,
                  raw_action, inhibited, inhibition_reason,
                  final_action,
                  arch_condition=None, prompt_style=None, ablation_label=None):

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO decision_log (
                evaluated_at, zone, snapshot_id,
                veto_triggered, veto_reason,
                c_cov, c_agree, c_fresh, c_env, c_fb, c_ha, c_pref,
                confidence_score, confidence_tier,
                llm_used, llm_reasoning,
                raw_action, inhibited, inhibition_reason,
                final_action,
                architecture_condition, prompt_style, ablation_label
            ) VALUES (
                %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s,
                %s,
                %s, %s, %s
            )
            """,
            (
                evaluated_at, zone, snapshot_id,
                int(veto_triggered), veto_reason,
                signals.get("c_cov"),   signals.get("c_agree"),
                signals.get("c_fresh"), signals.get("c_env"),
                signals.get("c_fb"),    signals.get("c_ha"),
                signals.get("c_pref"),
                confidence_score, confidence_tier,
                int(llm_used), llm_reasoning,
                raw_action, int(inhibited), inhibition_reason,
                final_action,
                arch_condition, prompt_style, ablation_label
            )
        )
        conn.commit()
        return cur.lastrowid


# =============================================================
# ACTIVE FEEDBACK AGENT — QUERY HANDLER (stub RAG targeting)
# =============================================================

PROMPT_MESSAGES = {
    "low_coverage":         "How are you feeling in the space right now? Your input helps us keep track of conditions.",
    "high_divergence":      "We are seeing mixed responses today. Can you share how you are currently feeling?",
    "human_agent_mismatch": "Your recent experience may differ from what the system is estimating. Can you confirm your current comfort, health, and emotional condition?",
    "post_anomaly":         "We noticed a recent change in the environment. How are you feeling right now?",
    "general":              "Please share your current comfort, health, and emotional condition.",
}

# Human-readable labels for IEQ anomaly variables used in post_anomaly messages
_ANOMALY_VAR_LABELS = {
    "humidity":    "humidity",
    "temp":        "temperature",
    "co2":         "CO₂ levels",
    "voc":         "air quality (VOC)",
    "pm2_5":       "particulate matter (PM2.5)",
    "pm2.5":       "particulate matter (PM2.5)",
}


def _build_post_anomaly_message(snap):
    """
    Build a specific post-anomaly prompt referencing the actual variable and time.
    Falls back to generic if ea_events_json is missing or unparseable.
    """
    try:
        events = json.loads(snap.get("ea_events_json") or "[]")
        if events:
            e       = events[0]
            var     = (e.get("variable") or "").lower()
            ts_raw  = e.get("timestamp") or ""
            label   = _ANOMALY_VAR_LABELS.get(var, var)

            # Format timestamp to "HH:MM AM/PM" if available
            time_clause = ""
            if ts_raw:
                try:
                    from datetime import datetime as _dt
                    t = _dt.fromisoformat(str(ts_raw))
                    time_clause = f" around {t.strftime('%-I:%M %p')}"
                except Exception:
                    pass

            return (
                f"We detected a {label} change{time_clause} in your space. "
                f"Did you notice anything different — did it affect your comfort or focus?"
            )
    except Exception:
        pass
    return PROMPT_MESSAGES["post_anomaly"]

GLOBAL_COOLDOWN_MINUTES  = 30
PER_USER_COOLDOWN_MINUTES = 60


def classify_uncertainty(snap):
    """Classify the dominant uncertainty type from the analytical context."""
    coverage = snap.get("fr_real_coverage") or 0.0
    if coverage < 0.30:
        return "low_coverage"
    div_label = (snap.get("div_real_label") or "").lower()
    if "strong" in div_label:
        return "high_divergence"
    gaps = [
        snap.get("ha_comfort_gap") or 0.0,
        snap.get("ha_health_gap")  or 0.0,
        snap.get("ha_emotion_gap") or 0.0,
    ]
    if any(g > 1.0 for g in gaps):
        return "human_agent_mismatch"
    if (snap.get("ea_event_count") or 0) > 0:
        return "post_anomaly"
    return "general"


def build_occupant_profile(conn, user_id):
    """
    Build an IEQ preference profile vector for an occupant from their
    historical feedback joined with the nearest ae_snapshot IEQ reading
    at each submission time.

    Vector dimensions (12):
      0  comfort_mean       — mean comfort rating, normalized 0-1
      1  health_mean        — mean health rating, normalized 0-1
      2  emotion_mean       — mean emotion rating, normalized 0-1
      3  comfort_volatility — std of comfort, normalized by max range (4)
      4  health_volatility  — std of health, normalized
      5  emotion_volatility — std of emotion, normalized
      6  temp_sensitivity   — |corr(temperature, comfort)|, normalized
      7  humidity_sensitivity — |corr(humidity, comfort)|
      8  co2_sensitivity    — |corr(co2, health)|
      9  voc_sensitivity    — |corr(voc, health)|
      10 submission_recency — 1 - (hours since last submission / 24), clamped 0-1
      11 hour_consistency   — fraction of submissions in ±2h window of current hour
    """
    with conn.cursor() as cur:
        # Get all feedback with nearest ae_snapshot IEQ values
        cur.execute(
            """
            SELECT
                ofb.comfort_level, ofb.health_level, ofb.emotion_level,
                ofb.timestamp,
                (
                    SELECT ieq_temperature FROM ae_snapshot s
                    WHERE s.zone = 'all'
                    ORDER BY ABS(TIMESTAMPDIFF(SECOND, s.snapshot_time, ofb.timestamp))
                    LIMIT 1
                ) AS temperature,
                (
                    SELECT ieq_humidity FROM ae_snapshot s
                    WHERE s.zone = 'all'
                    ORDER BY ABS(TIMESTAMPDIFF(SECOND, s.snapshot_time, ofb.timestamp))
                    LIMIT 1
                ) AS humidity,
                (
                    SELECT ieq_co2 FROM ae_snapshot s
                    WHERE s.zone = 'all'
                    ORDER BY ABS(TIMESTAMPDIFF(SECOND, s.snapshot_time, ofb.timestamp))
                    LIMIT 1
                ) AS co2,
                (
                    SELECT ieq_voc FROM ae_snapshot s
                    WHERE s.zone = 'all'
                    ORDER BY ABS(TIMESTAMPDIFF(SECOND, s.snapshot_time, ofb.timestamp))
                    LIMIT 1
                ) AS voc
            FROM occupant_feedback ofb
            WHERE ofb.user_id = %s
            ORDER BY ofb.timestamp DESC
            LIMIT 60
            """,
            (user_id,)
        )
        rows = cur.fetchall()

    if not rows:
        return np.zeros(12)

    comfort  = [r["comfort_level"]  for r in rows if r["comfort_level"]  is not None]
    health   = [r["health_level"]   for r in rows if r["health_level"]   is not None]
    emotion  = [r["emotion_level"]  for r in rows if r["emotion_level"]  is not None]
    temps    = [r["temperature"]    for r in rows if r["temperature"]    is not None]
    humidity = [r["humidity"]       for r in rows if r["humidity"]       is not None]
    co2      = [r["co2"]            for r in rows if r["co2"]            is not None]
    voc      = [r["voc"]            for r in rows if r["voc"]            is not None]
    hours    = [r["timestamp"].hour for r in rows if r["timestamp"]      is not None]

    def safe_mean(lst):
        return sum(lst) / len(lst) if lst else 3.0

    def safe_std(lst):
        if len(lst) < 2:
            return 0.0
        m = safe_mean(lst)
        return (sum((x - m) ** 2 for x in lst) / len(lst)) ** 0.5

    def safe_corr(xs, ys):
        """Pearson correlation, returns 0 if insufficient data."""
        pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
        if len(pairs) < 3:
            return 0.0
        n = len(pairs)
        mx = sum(p[0] for p in pairs) / n
        my = sum(p[1] for p in pairs) / n
        num = sum((p[0] - mx) * (p[1] - my) for p in pairs)
        dx  = sum((p[0] - mx) ** 2 for p in pairs) ** 0.5
        dy  = sum((p[1] - my) ** 2 for p in pairs) ** 0.5
        if dx == 0 or dy == 0:
            return 0.0
        return num / (dx * dy)

    # IEQ sensitivity: correlate each variable with the relevant feedback dim
    comfort_vals = [r["comfort_level"] for r in rows]
    health_vals  = [r["health_level"]  for r in rows]

    temp_corr     = abs(safe_corr(temps,    comfort_vals))
    humidity_corr = abs(safe_corr(humidity, comfort_vals))
    co2_corr      = abs(safe_corr(co2,      health_vals))
    voc_corr      = abs(safe_corr(voc,      health_vals))

    # Recency: hours since last submission
    last_ts = rows[0]["timestamp"]
    hours_ago = (datetime.now() - last_ts).total_seconds() / 3600
    recency = max(0.0, 1.0 - hours_ago / 24.0)

    # Hour consistency: fraction of past submissions near current hour (±2h)
    current_hour = datetime.now().hour
    near_hour = sum(1 for h in hours if abs(h - current_hour) <= 2 or
                    abs(h - current_hour) >= 22) / max(len(hours), 1)

    vec = np.array([
        (safe_mean(comfort) - 1) / 4,   # normalize 1-5 → 0-1
        (safe_mean(health)  - 1) / 4,
        (safe_mean(emotion) - 1) / 4,
        min(1.0, safe_std(comfort) / 4),
        min(1.0, safe_std(health)  / 4),
        min(1.0, safe_std(emotion) / 4),
        min(1.0, temp_corr),
        min(1.0, humidity_corr),
        min(1.0, co2_corr),
        min(1.0, voc_corr),
        recency,
        near_hour,
    ], dtype=float)

    return vec


def build_context_vector(snap, uncertainty_type):
    """
    Build a context vector from the current analytical state.
    Same 12 dimensions as the occupant profile so cosine similarity is meaningful.

    Dimensions:
      0-2  current occupant consensus (comfort/health/emotion), normalized
      3-5  divergence per dimension (1 = high disagreement)
      6-9  IEQ anomaly flags (temperature, humidity, co2, voc)
      10   urgency (always 1 — we always want fresh data)
      11   time-of-day weight (1 — current hour is always relevant)
    """
    def norm_feedback(v):
        return ((v or 3) - 1) / 4

    # Anomaly flags per IEQ variable from ea_events_json
    temp_flag = hum_flag = co2_flag = voc_flag = 0.0
    try:
        events = json.loads(snap.get("ea_events_json") or "[]")
        for e in events:
            var = (e.get("variable") or "").lower()
            if "temp"     in var: temp_flag = 1.0
            if "humid"    in var: hum_flag  = 1.0
            if "co2"      in var: co2_flag  = 1.0
            if "voc"      in var: voc_flag  = 1.0
    except Exception:
        pass

    # Divergence per dimension (MAD-based, normalized)
    div_comfort = min(1.0, (snap.get("div_real_comfort_mad") or 0.0) / 4)
    div_health  = min(1.0, (snap.get("div_real_health_mad")  or 0.0) / 4)
    div_emotion = min(1.0, (snap.get("div_real_emotion_mad") or 0.0) / 4)

    vec = np.array([
        norm_feedback(snap.get("real_comfort_mean")),
        norm_feedback(snap.get("real_health_mean")),
        norm_feedback(snap.get("real_emotion_mean")),
        div_comfort,
        div_health,
        div_emotion,
        temp_flag,
        hum_flag,
        co2_flag,
        voc_flag,
        1.0,   # urgency
        1.0,   # time-of-day (always current)
    ], dtype=float)

    return vec


def get_eligible_users(conn, zone, snap, uncertainty_type):
    """
    Return up to 2 eligible users for prompting, ranked by RAG-based
    responsiveness score (cosine similarity between occupant IEQ preference
    profile and current analytical context).

    Pipeline:
      1. Global cooldown check
      2. Filter to active users (have ever submitted)
      3. Per-user cooldown filter
      4. Hard rule pre-filter by uncertainty type
      5. RAG ranking via cosine similarity
      6. Return top 2
    """
    now = datetime.now()
    global_cutoff   = now - timedelta(minutes=GLOBAL_COOLDOWN_MINUTES)
    per_user_cutoff = now - timedelta(minutes=PER_USER_COOLDOWN_MINUTES)

    with conn.cursor() as cur:
        # 1. Global cooldown
        cur.execute(
            "SELECT id FROM feedback_prompt WHERE zone = %s AND prompt_timestamp >= %s LIMIT 1",
            (zone, global_cutoff)
        )
        if cur.fetchone():
            return []

        # 2. Get active users in zone who have ever submitted
        if zone != "all":
            cur.execute(
                """
                SELECT n.id, n.username FROM newusers n
                WHERE n.zone = %s
                  AND EXISTS (
                      SELECT 1 FROM occupant_feedback o WHERE o.user_id = n.id
                  )
                """,
                (zone,)
            )
        else:
            cur.execute(
                """
                SELECT n.id, n.username FROM newusers n
                WHERE EXISTS (
                    SELECT 1 FROM occupant_feedback o WHERE o.user_id = n.id
                )
                """
            )
        users = cur.fetchall()

        if not users:
            return []

        # 3. Per-user cooldown
        eligible = []
        for u in users:
            cur.execute(
                "SELECT id FROM feedback_prompt WHERE target_user_id = %s AND prompt_timestamp >= %s LIMIT 1",
                (u["id"], per_user_cutoff)
            )
            if not cur.fetchone():
                eligible.append(u)

        if not eligible:
            return []

        # 4. Hard rule pre-filter by uncertainty type
        if uncertainty_type == "low_coverage":
            # Only consider users with no feedback in last 2h
            recent_cutoff = now - timedelta(hours=2)
            no_recent = []
            for u in eligible:
                cur.execute(
                    "SELECT id FROM occupant_feedback WHERE user_id = %s AND timestamp >= %s LIMIT 1",
                    (u["id"], recent_cutoff)
                )
                if not cur.fetchone():
                    no_recent.append(u)
            if no_recent:
                eligible = no_recent

        elif uncertainty_type == "high_divergence":
            # Only consider users whose last comfort rating is furthest from group mean
            group_mean = snap.get("real_comfort_mean") or 3.0
            scored_div = []
            for u in eligible:
                cur.execute(
                    "SELECT comfort_level FROM occupant_feedback WHERE user_id = %s ORDER BY timestamp DESC LIMIT 1",
                    (u["id"],)
                )
                row = cur.fetchone()
                deviation = abs((row["comfort_level"] or 3) - group_mean) if row else 0
                scored_div.append((deviation, u))
            scored_div.sort(key=lambda x: x[0], reverse=True)
            # Keep top half by deviation as candidate pool for RAG
            eligible = [u for _, u in scored_div[:max(2, len(scored_div) // 2)]]

    # 5. RAG ranking: build profile per occupant, compare to context vector
    context_vec = build_context_vector(snap, uncertainty_type)

    scored = []
    for u in eligible:
        profile_vec = build_occupant_profile(conn, u["id"])
        sim = cosine_similarity(profile_vec, context_vec)
        scored.append((sim, u))
        print(f"[DLL][RAG] user={u['username']!r} sim={sim:.4f}")

    scored.sort(key=lambda x: x[0], reverse=True)

    # 6. Return top 2 with their scores
    return [{"id": u["id"], "username": u["username"], "rag_score": round(sim, 4)}
            for sim, u in scored[:2]]


def get_eligible_users_random(conn, zone, snap, uncertainty_type):
    """
    Phase 3b no_rag condition: same cooldown + pre-filter pipeline as
    get_eligible_users(), but SKIP the RAG cosine-similarity ranking.
    Instead, randomly select up to 2 from the eligible pool.
    """
    import random as _random
    now = datetime.now()
    global_cutoff   = now - timedelta(minutes=GLOBAL_COOLDOWN_MINUTES)
    per_user_cutoff = now - timedelta(minutes=PER_USER_COOLDOWN_MINUTES)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM feedback_prompt WHERE zone = %s AND prompt_timestamp >= %s LIMIT 1",
            (zone, global_cutoff)
        )
        if cur.fetchone():
            return []

        if zone != "all":
            cur.execute(
                "SELECT n.id, n.username FROM newusers n WHERE n.zone = %s "
                "AND EXISTS (SELECT 1 FROM occupant_feedback o WHERE o.user_id = n.id)",
                (zone,)
            )
        else:
            cur.execute(
                "SELECT n.id, n.username FROM newusers n "
                "WHERE EXISTS (SELECT 1 FROM occupant_feedback o WHERE o.user_id = n.id)"
            )
        users = cur.fetchall()
        if not users:
            return []

        eligible = []
        for u in users:
            cur.execute(
                "SELECT id FROM feedback_prompt WHERE target_user_id = %s AND prompt_timestamp >= %s LIMIT 1",
                (u["id"], per_user_cutoff)
            )
            if not cur.fetchone():
                eligible.append(u)
        if not eligible:
            return []

        # Apply same hard pre-filter as get_eligible_users
        if uncertainty_type == "low_coverage":
            recent_cutoff = now - timedelta(hours=2)
            no_recent = []
            for u in eligible:
                cur.execute(
                    "SELECT id FROM occupant_feedback WHERE user_id = %s AND timestamp >= %s LIMIT 1",
                    (u["id"], recent_cutoff)
                )
                if not cur.fetchone():
                    no_recent.append(u)
            if no_recent:
                eligible = no_recent

        elif uncertainty_type == "high_divergence":
            group_mean = snap.get("real_comfort_mean") or 3.0
            scored_div = []
            for u in eligible:
                cur.execute(
                    "SELECT comfort_level FROM occupant_feedback WHERE user_id = %s ORDER BY timestamp DESC LIMIT 1",
                    (u["id"],)
                )
                row = cur.fetchone()
                deviation = abs((row["comfort_level"] or 3) - group_mean) if row else 0
                scored_div.append((deviation, u))
            scored_div.sort(key=lambda x: x[0], reverse=True)
            eligible = [u for _, u in scored_div[:max(2, len(scored_div) // 2)]]

    # Random selection instead of RAG ranking
    _random.shuffle(eligible)
    return [{"id": u["id"], "username": u["username"], "rag_score": None}
            for u in eligible[:2]]


# =============================================================
# ACTIVE FEEDBACK AGENT — LLM-powered targeting
# =============================================================

def fetch_prompt_rag_history(conn, zone, top_k=5):
    """Fetch past prompt records for RAG context: per-user response rates
    and the most recent prompt outcomes for this zone."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT n.username,
                   COUNT(*) AS total_prompted,
                   SUM(CASE WHEN fp.status = 'responded' THEN 1 ELSE 0 END) AS responded_count
            FROM feedback_prompt fp
            JOIN newusers n ON n.id = fp.target_user_id
            WHERE fp.zone = %s
            GROUP BY n.username
        """, (zone,))
        response_rates = cur.fetchall()

        cur.execute("""
            SELECT fp.prompt_timestamp, n.username, fp.uncertainty_type,
                   fp.status, ROUND(fp.rag_score, 4) AS rag_score
            FROM feedback_prompt fp
            JOIN newusers n ON n.id = fp.target_user_id
            WHERE fp.zone = %s
            ORDER BY fp.prompt_timestamp DESC
            LIMIT %s
        """, (zone, top_k))
        recent = cur.fetchall()

    return {"response_rates": response_rates, "recent_prompts": recent}


def describe_candidate(conn, user, snap):
    """Build a human-readable profile dict for one candidate occupant."""
    vec = build_occupant_profile(conn, user["id"])
    # vec layout: [comfort_mean, health_mean, emotion_mean,
    #              comfort_vol, health_vol, emotion_vol,
    #              temp_corr, humidity_corr, co2_corr, voc_corr,
    #              recency, hour_consistency]
    return {
        "username":            user["username"],
        "rag_score":           round(float(user.get("rag_score") or 0), 4),
        "avg_comfort":         round(float(vec[0]) * 4 + 1, 2),   # un-normalize to 1-5
        "avg_health":          round(float(vec[1]) * 4 + 1, 2),
        "avg_emotion":         round(float(vec[2]) * 4 + 1, 2),
        "temp_sensitivity":    round(float(vec[6]), 3),
        "humidity_sensitivity":round(float(vec[7]), 3),
        "co2_sensitivity":     round(float(vec[8]), 3),
        "voc_sensitivity":     round(float(vec[9]), 3),
        "recency_score":       round(float(vec[10]), 3),
    }


def build_feedback_agent_prompt(snap, zone, candidates_info, uncertainty_type, rag_history):
    """Construct the LLM prompt for the Active Feedback Agent."""
    def fmt(v): return round(v, 2) if v is not None else "N/A"

    temp_c = snap.get("ieq_temperature")
    temp_f = round(temp_c * 9/5 + 32, 1) if temp_c else "N/A"

    # Build specific anomaly context for post_anomaly type
    _anomaly_reason = "environmental anomaly detected — need to confirm whether occupants perceive it"
    try:
        _ea_events = json.loads(snap.get("ea_events_json") or "[]")
        if _ea_events and uncertainty_type == "post_anomaly":
            _e   = _ea_events[0]
            _var = _ANOMALY_VAR_LABELS.get((_e.get("variable") or "").lower(), _e.get("variable", "environmental"))
            _ts  = _e.get("timestamp") or ""
            _time_clause = ""
            if _ts:
                try:
                    from datetime import datetime as _dt2
                    _time_clause = f" at {_dt2.fromisoformat(str(_ts)).strftime('%-I:%M %p')}"
                except Exception:
                    pass
            _anomaly_reason = f"{_var} anomaly detected{_time_clause} — need to confirm whether occupants perceived it"
    except Exception:
        pass

    uncertainty_reasons = {
        "low_coverage":          f"only {snap.get('real_comfort_count') or 0} real feedback submission(s) in the current window",
        "high_divergence":       "high disagreement across occupant comfort/health/emotion ratings (high MAD)",
        "human_agent_mismatch":  "simulated agent ratings diverge significantly from real occupant ratings",
        "post_anomaly":          _anomaly_reason,
        "general":               "general uncertainty in the current analytical state",
    }
    reason = uncertainty_reasons.get(uncertainty_type, "general uncertainty")

    # Candidates block
    cand_lines = []
    for c in candidates_info:
        cand_lines.append(
            f"  - {c['username']} (RAG match score: {c['rag_score']})\n"
            f"    Avg feedback: comfort={c['avg_comfort']}/5, health={c['avg_health']}/5, emotion={c['avg_emotion']}/5\n"
            f"    IEQ sensitivities — temp: {c['temp_sensitivity']}, humidity: {c['humidity_sensitivity']}, "
            f"CO2: {c['co2_sensitivity']}, VOC: {c['voc_sensitivity']}\n"
            f"    Submission recency score: {c['recency_score']} (1.0 = very recent)"
        )
    cand_text = "\n".join(cand_lines) if cand_lines else "  None available."

    # Response rates block
    rate_lines = []
    for r in rag_history.get("response_rates", []):
        total = r["total_prompted"] or 1
        rate = round(r["responded_count"] / total * 100)
        rate_lines.append(f"  - {r['username']}: {r['responded_count']}/{r['total_prompted']} responses ({rate}%)")
    rate_text = "\n".join(rate_lines) if rate_lines else "  No history yet."

    # Recent prompts block
    recent_lines = []
    for p in rag_history.get("recent_prompts", []):
        ts = str(p["prompt_timestamp"])[:16]
        recent_lines.append(f"  - {ts}: {p['username']} [{p['uncertainty_type']}] → {p['status']}")
    recent_text = "\n".join(recent_lines) if recent_lines else "  No recent prompts."

    # Build impact context — recent recommendations generated from feedback in this zone
    _impact_text = "  No recommendations generated yet."
    try:
        conn = get_db()
        if conn:
            with conn.cursor() as _ic:
                _ic.execute("""
                    SELECT issue_type, suggested_action, created_at
                    FROM facility_recommendation
                    WHERE zone = %s AND created_at >= NOW() - INTERVAL 7 DAY
                    ORDER BY created_at DESC LIMIT 3
                """, (zone,))
                _impacts = _ic.fetchall()
                if _impacts:
                    _lines = []
                    for _imp in _impacts:
                        _ts = str(_imp["created_at"])[:16]
                        _lines.append(f"  - {_ts}: {_imp['issue_type']} → {_imp['suggested_action']}")
                    _impact_text = "\n".join(_lines)
            conn.close()
    except Exception:
        pass

    return f"""You are the Active Feedback Agent for a human-building interaction digital twin system.
The Decision Logic Layer has determined that occupant feedback is urgently needed to reduce uncertainty.

CURRENT SITUATION
Zone: {zone}
Uncertainty type: {uncertainty_type}
Reason: {reason}

CURRENT IEQ CONDITIONS
Temperature: {temp_f}°F  |  Humidity: {fmt(snap.get('ieq_humidity'))}%  |  CO2: {fmt(snap.get('ieq_co2'))} ppm  |  VOC: {fmt(snap.get('ieq_voc'))} ppb

OCCUPANT FEEDBACK CONSENSUS (n={snap.get('real_comfort_count') or 0} real submissions)
Comfort: {fmt(snap.get('real_comfort_mean'))}/5  |  Health: {fmt(snap.get('real_health_mean'))}/5  |  Emotion: {fmt(snap.get('real_emotion_mean'))}/5

RECENT FEEDBACK IMPACT (recommendations generated from occupant feedback in this zone)
{_impact_text}

CANDIDATE OCCUPANTS (pre-filtered by cooldown rules, ranked by IEQ sensitivity match)
{cand_text}

PAST PROMPT RESPONSE RATES (this zone)
{rate_text}

RECENT PROMPT HISTORY (this zone)
{recent_text}

TASK
Select 1-2 occupants from the candidates above to query. For each selected occupant:
1. Write a personalized 1-2 sentence message. Begin with the base "How are you feeling in the space right now?" and add one specific reference to their IEQ sensitivity profile or the current environmental conditions.
2. If recent feedback from this zone led to a recommendation (see RECENT FEEDBACK IMPACT above), briefly mention it to show the occupant their input matters — e.g., "Your recent feedback helped us flag a humidity issue to building management."
3. Provide a brief reason for selecting them (reference profile, response history, or current IEQ fit).

Respond ONLY with valid JSON — no markdown, no extra text:
{{
  "targets": [
    {{
      "username": "...",
      "message": "...",
      "reason": "..."
    }}
  ],
  "overall_reasoning": "..."
}}"""


def llm_targeting(conn, snap, zone, candidates, uncertainty_type, arch_condition="two_agent"):
    """
    Call Claude Haiku to select final targets and generate personalized messages.
    Falls back to rule-based (top RAG candidates + template message) on any failure.
    Returns list of dicts: {id, username, rag_score, message, llm_reasoning, llm_used}
    """
    if not candidates:
        return []

    candidates_info  = [describe_candidate(conn, u, snap) for u in candidates]
    rag_history      = fetch_prompt_rag_history(conn, zone, top_k=FA_RAG_TOP_K)
    prompt           = build_feedback_agent_prompt(snap, zone, candidates_info,
                                                   uncertainty_type, rag_history)
    username_to_user = {u["username"]: u for u in candidates}

    try:
        client   = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        t0       = time.time()
        response = client.messages.create(
            model=FEEDBACK_AGENT_MODEL,
            max_tokens=600,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )
        latency = int((time.time() - t0) * 1000)
        log_llm_call(conn, arch_condition, "targeting", FEEDBACK_AGENT_MODEL,
                      response.usage.input_tokens, response.usage.output_tokens, latency)
        raw             = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        result          = json.loads(raw)
        overall_reason  = result.get("overall_reasoning", "")
        final = []
        for t in result.get("targets", []):
            uname = t.get("username")
            if uname not in username_to_user:
                continue
            u = username_to_user[uname]
            reasoning = f"{overall_reason} | {t.get('reason', '')}".strip(" |")
            fallback_msg = (_build_post_anomaly_message(snap)
                            if uncertainty_type == "post_anomaly"
                            else PROMPT_MESSAGES[uncertainty_type])
            final.append({
                "id":           u["id"],
                "username":     uname,
                "rag_score":    u.get("rag_score"),
                "message":      t.get("message") or fallback_msg,
                "llm_reasoning":reasoning,
                "llm_used":     1,
            })

        if final:
            print(f"[FA-LLM] Targets: {[f['username'] for f in final]}")
            return final

        print("[FA-LLM] No valid targets in LLM response — falling back")

    except Exception as e:
        print(f"[FA-LLM] Failed ({e}) — falling back to rule-based")

    # Fallback: top RAG candidates with template message
    fallback_msg = (_build_post_anomaly_message(snap)
                    if uncertainty_type == "post_anomaly"
                    else PROMPT_MESSAGES[uncertainty_type])
    return [{
        "id":           u["id"],
        "username":     u["username"],
        "rag_score":    u.get("rag_score"),
        "message":      fallback_msg,
        "llm_reasoning":None,
        "llm_used":     0,
    } for u in candidates[:2]]


def issue_query(conn, decision_log_id, snapshot_id, snap, signals, zone,
                 arch_condition="two_agent", prompt_style="personalized",
                 ablation_label=None, prefetched_targets=None):
    """
    Write feedback_prompt rows and send push notifications.

    Handles all 7 experiment conditions via (arch_condition, prompt_style, ablation_label):
      Phase 3a (2x2 factorial):
        two_agent  + personalized  → RAG candidates + Haiku LLM messages
        one_agent  + personalized  → targets already resolved by single_agent_action()
        two_agent  + template      → RAG candidates + template messages
        one_agent  + template      → targets already resolved by single_agent_action() with templates
      Phase 3b (ablation):
        no_rag                     → random candidates + Haiku LLM messages
        no_llm_dec                 → RAG candidates + Haiku LLM messages (decision was rule-based)
        minimal                    → random candidates + template messages
    """
    uncertainty_type = classify_uncertainty(snap)
    now = datetime.now()

    # --- Determine targets based on condition ---
    if prefetched_targets is not None:
        # 1-agent conditions: targets already computed by single_agent_action()
        targets = prefetched_targets
    else:
        # Select candidates based on targeting method
        use_rag = (ablation_label not in ("no_rag", "minimal"))
        if use_rag:
            candidates = get_eligible_users(conn, zone, snap, uncertainty_type)
        else:
            candidates = get_eligible_users_random(conn, zone, snap, uncertainty_type)

        if not candidates:
            print(f"[DLL] QUERY for zone={zone!r}: no eligible users (cooldown active or zone empty)")
            return

        # Generate messages based on prompt style
        use_llm_msg = (prompt_style == "personalized" and ablation_label != "minimal")
        if use_llm_msg:
            targets = llm_targeting(conn, snap, zone, candidates, uncertainty_type,
                                     arch_condition=arch_condition)
        else:
            fallback_msg = (_build_post_anomaly_message(snap)
                            if uncertainty_type == "post_anomaly"
                            else PROMPT_MESSAGES[uncertainty_type])
            targets = [{
                "id":           u["id"],
                "username":     u["username"],
                "rag_score":    u.get("rag_score"),
                "message":      fallback_msg,
                "llm_reasoning": None,
                "llm_used":     0,
            } for u in candidates[:2]]

    if not targets:
        return

    # --- Build condition label for logging ---
    prompt_condition = prompt_style  # "personalized" or "template"

    print(f"[DLL] Experiment: arch={arch_condition} prompt={prompt_style} "
          f"ablation={ablation_label or 'none'}")

    # --- Insert feedback_prompt rows ---
    with conn.cursor() as cur:
        for t in targets:
            cur.execute(
                """
                INSERT INTO feedback_prompt
                    (prompt_timestamp, decision_log_id, snapshot_id, zone,
                     uncertainty_type, target_user_id, message_text, status,
                     rag_score, llm_used, llm_reasoning, prompt_condition,
                     architecture_condition, ablation_label)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s)
                """,
                (now, decision_log_id, snapshot_id, zone,
                 uncertainty_type, t["id"], t["message"],
                 t.get("rag_score"), t["llm_used"], t.get("llm_reasoning"),
                 prompt_condition, arch_condition, ablation_label)
            )
            print(f"[DLL] QUERY → user={t['username']!r} arch={arch_condition} "
                  f"prompt={prompt_condition} ablation={ablation_label or 'none'} "
                  f"llm={t['llm_used']} msg={t['message'][:60]!r}…")
    conn.commit()

    # --- Send push notification ---
    user_ids = [t["id"] for t in targets]
    try:
        resp = requests.post(
            f"{FEEDBACK_UI_URL}/api/notify",
            json={"user_ids": user_ids, "title": "HBI Feedback",
                  "body": targets[0]["message"]},
            headers={"X-Notify-Key": NOTIFY_API_KEY},
            timeout=10
        )
        print(f"[DLL] Push notify → {resp.json()}")
    except Exception as e:
        print(f"[DLL] Push notify failed (non-critical): {e}")


# =============================================================
# FACILITY RECOMMENDATION (replaces device intervention)
# =============================================================

def _llm_generate_recommendations(zone, snap, issues, occupant_summary, feedback_count, ea_events,
                                    conn=None, arch_condition="two_agent"):
    """
    Call Haiku to generate contextual facility recommendations.
    Falls back to rule-based suggestions on any failure.

    When `conn` is provided, token usage + latency are logged to llm_call_log
    under call_role='recommend' (for Phase 3 compute-budget analysis).
    """
    fallback = [issue[4] for issue in issues]

    # Build context for the LLM
    issues_desc = "\n".join(
        f"  - {itype} ({sev}): current={cval}, threshold={thr}"
        for itype, sev, cval, thr, _ in issues
    )

    anomaly_desc = ""
    if ea_events:
        anomaly_desc = "\nRecent anomaly events:\n" + "\n".join(
            f"  - {e.get('type','?')}: {e.get('variable','?')} = {e.get('value','?')}"
            for e in ea_events[:5]
        )

    now = datetime.now(EASTERN)
    time_context = now.strftime("%A, %B %d at %I:%M %p")

    prompt = f"""You are an intelligent building management assistant. Generate actionable facility recommendations for building management staff.

Zone: {zone}
Time: {time_context}
Occupant feedback: {occupant_summary or 'No feedback available'} ({feedback_count} submissions in window)

Current IEQ conditions:
  Temperature: {snap.get('ieq_temperature')}°F
  Humidity: {snap.get('ieq_humidity')}%
  CO2: {snap.get('ieq_co2')} ppm
  VOC: {snap.get('ieq_voc')} ppb
  PM2.5: {snap.get('ieq_pm2_5')} µg/m³
{anomaly_desc}

Issues detected:
{issues_desc}

For EACH issue above, write a specific, actionable recommendation (1-2 sentences) that:
- References the actual readings and zone name
- Considers time of day and occupant feedback
- Suggests a concrete action for building staff (not occupants)
- Is professional in tone

Output ONLY a JSON array of strings, one per issue, in the same order. No markdown, no extra text.
Example: ["Increase fresh air damper position in R-Lab — CO2 at 1200 ppm with 4 occupants reporting discomfort.", "Check HVAC filter in R-Lab — VOC at 850 ppb suggests inadequate filtration."]"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        t0 = time.time()
        resp = client.messages.create(
            model=FEEDBACK_AGENT_MODEL,
            max_tokens=500,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )
        latency = int((time.time() - t0) * 1000)
        if conn is not None:
            log_llm_call(conn, arch_condition, "recommend", FEEDBACK_AGENT_MODEL,
                          resp.usage.input_tokens, resp.usage.output_tokens, latency)
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        result = json.loads(raw)
        if isinstance(result, list) and len(result) >= len(issues):
            print(f"[DLL-REC] Haiku generated {len(result)} recommendation(s)")
            return result
        print(f"[DLL-REC] Haiku returned {len(result)} items but expected {len(issues)} — using fallback")
    except Exception as e:
        print(f"[DLL-REC] Haiku failed ({e}) — using rule-based fallback")

    return fallback


def generate_recommendation(conn, decision_log_id, snapshot_id, zone, snap,
                             arch_condition="two_agent"):
    """
    Generate a facility recommendation based on environmental anomalies
    and occupant feedback. Writes to facility_recommendation table.
    `arch_condition` is forwarded to the Haiku call so llm_call_log
    attributes the recommend-token usage to the correct experiment arm.
    """
    ea_events = []
    try:
        raw = snap.get("ea_events_json")
        if raw:
            ea_events = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        pass

    # Identify issues from snapshot
    issues = []

    humidity = snap.get("ieq_humidity")
    if humidity is not None and humidity < 20:
        issues.append(("low_humidity", "high", humidity, 20.0,
                        "Increase humidification — humidity is critically low"))

    voc = snap.get("ieq_voc")
    if voc is not None and voc > 700:
        issues.append(("high_voc", "high", voc, 700.0,
                        "Improve ventilation — VOC levels exceed safe threshold"))

    pm25 = snap.get("ieq_pm2_5")
    if pm25 is not None and pm25 > 35:
        issues.append(("high_pm25", "high", pm25, 35.0,
                        "Improve air filtration — PM2.5 exceeds EPA 24h standard"))

    co2 = snap.get("ieq_co2")
    if co2 is not None and co2 > 1000:
        issues.append(("high_co2", "moderate", co2, 1000.0,
                        "Increase fresh air intake — CO2 suggests poor ventilation"))

    temp = snap.get("ieq_temperature")
    comfort = snap.get("real_comfort_mean")
    if comfort is not None and comfort < 3.0 and temp is not None:
        if temp > 78:
            issues.append(("high_temperature", "moderate", temp, 78.0,
                            "Reduce cooling setpoint — occupants report thermal discomfort"))
        elif temp < 65:
            issues.append(("low_temperature", "moderate", temp, 65.0,
                            "Increase heating setpoint — occupants report thermal discomfort"))

    # If no specific issue detected, create a general recommendation from anomaly events
    if not issues and ea_events:
        var = ea_events[0].get("variable", "environmental") if ea_events else "environmental"
        issues.append(("environmental_anomaly", "moderate", None, None,
                        f"Investigate {var} anomaly — occupants report discomfort"))

    if not issues:
        print(f"[DLL] RECOMMEND zone={zone!r}: no actionable issues detected")
        return

    # Count contributing feedback
    feedback_count = 0
    for dim in ["real_comfort_count", "real_health_count", "real_emotion_count"]:
        feedback_count += snap.get(dim) or 0

    # Build occupant summary
    parts = []
    for label, key in [("comfort", "real_comfort_mean"), ("health", "real_health_mean"),
                        ("emotion", "real_emotion_mean")]:
        val = snap.get(key)
        if val is not None:
            parts.append(f"{label}={val:.1f}/5")
    occupant_summary = f"Zone avg: {', '.join(parts)}" if parts else None

    # ── LLM-generated recommendation via Haiku ──
    llm_recommendations = _llm_generate_recommendations(
        zone, snap, issues, occupant_summary, feedback_count, ea_events,
        conn=conn, arch_condition=arch_condition
    )

    now = datetime.now(EASTERN).replace(tzinfo=None)

    with conn.cursor() as cur:
        for i, (issue_type, severity, current_val, threshold, rule_suggested) in enumerate(issues):
            suggested = llm_recommendations[i] if i < len(llm_recommendations) else rule_suggested
            cur.execute(
                """
                INSERT INTO facility_recommendation
                    (decision_log_id, snapshot_id, zone, created_at,
                     issue_type, severity, current_value, threshold,
                     suggested_action, occupant_summary,
                     contributing_feedback_count, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (decision_log_id, snapshot_id, zone, now,
                 issue_type, severity, current_val, threshold,
                 suggested, occupant_summary, feedback_count)
            )
        conn.commit()

    print(f"[DLL] RECOMMEND zone={zone!r}: {len(issues)} recommendation(s) generated")
    for i, (issue_type, severity, current_val, threshold, _) in enumerate(issues):
        suggested = llm_recommendations[i] if i < len(llm_recommendations) else issues[i][4]
        print(f"       → {issue_type} ({severity}): {suggested[:100]}")


# =============================================================
# MAIN EVALUATION CYCLE
# =============================================================

def run_decision_cycle():
    """Run one full evaluation for every zone."""
    try:
        eastern      = pytz.timezone("America/New_York")
        evaluated_at = datetime.now(eastern).replace(tzinfo=None)

        conn = get_db()
        if not conn:
            print("[DLL] Skipping cycle — DB unavailable")
            return

        try:
            # monitor_termination disabled — intervention layer not active
            # try:
            #     monitor_termination(conn, issue_query_fn=issue_query)
            # except Exception as e:
            #     print(f"[DLL] monitor_termination error: {e}")

            for zone in ALL_ZONES:
                try:
                    _evaluate_zone(conn, evaluated_at, zone)
                except Exception as e:
                    print(f"[DLL ERROR] zone={zone!r}: {e}")
                    traceback.print_exc()
        finally:
            conn.close()

    except Exception as e:
        print(f"[DLL ERROR] outer: {e}")
        traceback.print_exc()


def _evaluate_zone(conn, evaluated_at, zone):
    snap = get_latest_snapshot(conn, zone)
    if not snap:
        print(f"[DLL] zone={zone!r}: no snapshot found, skipping")
        return

    snapshot_id = snap["id"]

    # ── Get experiment condition for this zone + today (V2) ───
    arch_condition, prompt_style, ablation_label = get_experiment_condition(zone)

    # ── Stage 1: Veto ─────────────────────────────────────────
    veto_triggered, veto_reason = veto_check(snap)

    # For 1-agent condition, we need targets before saving decision
    one_agent_targets = None

    if veto_triggered:
        raw_action = "query"
        signals    = {k: None for k in WEIGHTS}
        confidence_score = None
        confidence_tier  = None
        llm_used         = False
        llm_reasoning    = None

    else:
        # ── Stage 2: Confidence scoring ───────────────────────
        signals          = compute_signals(snap)
        confidence_score, confidence_tier = compute_confidence(signals)
        llm_used         = False
        llm_reasoning    = None

        if confidence_tier == "low":
            raw_action = "query"

        elif confidence_tier == "high":
            raw_action = high_tier_action(snap)

        else:  # medium
            if arch_condition == "one_agent":
                uncertainty_type = classify_uncertainty(snap)
                candidates = get_eligible_users(conn, zone, snap, uncertainty_type)
                raw_action, llm_reasoning, one_agent_targets = \
                    single_agent_action(conn, snap, signals,
                                        confidence_score, zone,
                                        candidates, uncertainty_type,
                                        prompt_style=prompt_style)
            elif ablation_label == "no_llm_dec":
                raw_action = _llm_heuristic_fallback(signals)
                llm_reasoning = "[ABLATION no_llm_dec] Rule-based heuristic used instead of LLM."
            elif ablation_label == "minimal":
                raw_action = _llm_heuristic_fallback(signals)
                llm_reasoning = "[ABLATION minimal] No LLM used — rule-based decision + random targeting."
            else:  # two_agent (default)
                raw_action, llm_reasoning = llm_action(
                    conn, snap, signals, confidence_score, zone,
                    arch_condition=arch_condition
                )
            llm_used = (ablation_label not in ("no_llm_dec", "minimal"))

    # ── Action inhibition ─────────────────────────────────────
    inhibited, inhibition_reason = inhibition_check(conn, zone, raw_action)
    final_action = "idle" if inhibited else raw_action

    # ── Save to decision_log ──────────────────────────────────
    decision_log_id = save_decision(
        conn, evaluated_at, zone, snapshot_id,
        veto_triggered, veto_reason,
        signals, confidence_score, confidence_tier,
        llm_used, llm_reasoning,
        raw_action, inhibited, inhibition_reason,
        final_action,
        arch_condition=arch_condition, prompt_style=prompt_style,
        ablation_label=ablation_label
    )

    print(
        f"[DLL] zone={zone!r:20s} "
        f"veto={str(veto_triggered):5s} "
        f"C={f'{confidence_score:.3f}' if confidence_score is not None else 'N/A':>6s} "
        f"tier={confidence_tier or 'veto':8s} "
        f"raw={raw_action:10s} "
        f"inhibited={str(inhibited):5s} "
        f"final={final_action} "
        f"exp=[{arch_condition}/{prompt_style}"
        f"{'/' + ablation_label if ablation_label else ''}]"
    )

    # ── Execute final action ──────────────────────────────────
    if final_action == "query":
        issue_query(conn, decision_log_id, snapshot_id, snap, signals, zone,
                    arch_condition=arch_condition, prompt_style=prompt_style,
                    ablation_label=ablation_label,
                    prefetched_targets=one_agent_targets)

    elif final_action == "recommend":
        generate_recommendation(conn, decision_log_id, snapshot_id, zone, snap,
                                 arch_condition=arch_condition)

        # Notify zone occupants that the system escalated to facilities
        try:
            with conn.cursor() as _cur:
                _cur.execute("SELECT id FROM newusers WHERE zone = %s", (zone,))
                zone_user_ids = [r["id"] for r in _cur.fetchall()]
            if zone_user_ids:
                requests.post(
                    f"{FEEDBACK_UI_URL}/api/notify",
                    json={
                        "user_ids": zone_user_ids,
                        "title": "Building Update",
                        "body": "Your feedback helped — we've sent a recommendation to building management to address conditions in your space.",
                    },
                    headers={"X-Notify-Key": NOTIFY_API_KEY},
                    timeout=10,
                )
        except Exception as _e:
            print(f"[DLL] RECOMMEND notify failed (non-critical): {_e}")


# =============================================================
# SCHEDULER
# =============================================================

if __name__ == "__main__":
    print("[DLL] Starting Decision Logic Layer...")

    # Run once immediately on startup (after a short delay)
    time.sleep(STARTUP_DELAY_SECONDS)
    run_decision_cycle()

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=run_decision_cycle,
        trigger=IntervalTrigger(minutes=EVAL_INTERVAL_MINUTES),
        id="decision_logic_job",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    print(f"[DLL] Scheduler running every {EVAL_INTERVAL_MINUTES} min.")

    atexit.register(lambda: scheduler.shutdown())

    # Keep alive
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        print("[DLL] Shutting down.")
