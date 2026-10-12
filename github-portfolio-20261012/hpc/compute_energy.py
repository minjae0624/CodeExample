#!/usr/bin/env python3
"""Estimate the ENERGY and CARBON cost of this project's compute, from sacct.

    python3 compute_energy.py                    # since 2026-01-01
    python3 compute_energy.py --since 2026-08-01
    python3 compute_energy.py --pue 1.3 --gco2 250

★ TRY SLURM'S OWN MEASUREMENT FIRST. If the cluster has
  AcctGatherEnergyType=acct_gather_energy/{rapl,ipmi}, then
      sacct -j <id> -X --format=JobID,Elapsed,ConsumedEnergy
  gives MEASURED joules and is strictly better than anything here. Check with
      scontrol show config | grep -i AcctGatherEnergy
  This script is the fallback for when that returns `none` or zeros, which is
  the usual case on shared university clusters.

WHAT THIS IS: GPU-hours x device TDP x PUE. It is an UPPER-ish BOUND on GPU
draw (TDP is the thermal design power, not the average draw — inference and
data loading run well below it, typically 50-80%), and it EXCLUDES CPU, RAM,
storage and network. The two errors push in opposite directions and do not
cancel in any principled way.

⇒ Report it as an order-of-magnitude estimate with the assumptions stated, in
  the style of Strubell et al. 2019 / Luccioni et al. 2023. Do not present it
  as a measurement.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date, timedelta

# Device TDP in watts. Board power, not whole-node.
TDP = {"a100": 400, "l40s": 350, "a30": 165, "v100": 300, "t4": 70, "p100": 250,
       # H200 SXM is 700 W (the NVL/PCIe part is 600). The H200 nodes this was
       # written for carry 8 GPUs on NDR-200 IB, which is the SXM configuration. Added 2026-08-14 — before
       # that, h200 rows fell through dev_of() and were counted as "unrecognised
       # device", i.e. silently EXCLUDED from the totals.
       "h200": 700}
# Partition name -> device, for when AllocTRES names no model
PART2DEV = [("a100", "a100"), ("l40s", "l40s"), ("a30", "a30"),
            ("v100", "v100"), ("t4", "t4"), ("h200", "h200"), ("dgx", "a100")]


def dev_of(partition, tres):
    m = re.search(r"gres/gpu:(\w+)", tres or "")
    if m and m.group(1).lower() in TDP:
        return m.group(1).lower()
    for pat, dev in PART2DEV:
        if pat in (partition or "").lower():
            return dev
    return None


def ngpu(tres):
    m = re.search(r"gres/gpu(?::\w+)?=(\d+)", tres or "")
    return int(m.group(1)) if m else 0


def secs(el):
    """Slurm elapsed: [DD-]HH:MM:SS"""
    if not el or el in ("", "INVALID"):
        return 0
    d, _, rest = el.partition("-")
    if not rest:
        rest, d = d, "0"
    p = rest.split(":")
    while len(p) < 3:
        p.insert(0, "0")
    try:
        return int(d) * 86400 + int(p[0]) * 3600 + int(p[1]) * 60 + int(float(p[2]))
    except ValueError:
        return 0


def sacct_rows(since, until, depth=0):
    """sacct over [since, until), splitting the window when the DB refuses it.

    Some clusters' SlurmDB rejects multi-month queries with
        sacct: error: Too wide of a date range in query
    and an earlier version of this script swallowed stderr, so that error was
    reported to the user as "no GPU jobs found" — a wrong answer that looked
    like a real one. Now: errors surface, and the range halves until accepted.
    """
    p = subprocess.run(
        ["sacct", "-S", since, "-E", until, "-X", "--parsable2", "--noheader",
         "--format=JobID,Partition,Elapsed,AllocTRES,State"],
        capture_output=True, text=True)
    err = (p.stderr or "").strip()
    if p.returncode == 0 and not err:
        return p.stdout.strip().splitlines()

    d0, d1 = date.fromisoformat(since), date.fromisoformat(until)
    if (d1 - d0).days <= 1 or depth > 12:
        print(f"  !! sacct failed for {since}..{until}: {err}", file=sys.stderr)
        return []
    mid = d0 + timedelta(days=(d1 - d0).days // 2)
    return (sacct_rows(since, mid.isoformat(), depth + 1)
            + sacct_rows(mid.isoformat(), until, depth + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--pue", type=float, default=1.5,
                    help="datacenter Power Usage Effectiveness (default 1.5)")
    ap.add_argument("--gco2", type=float, default=300.0,
                    help="grid carbon intensity, gCO2e/kWh (default 300, ~US mid-Atlantic)")
    ap.add_argument("--util", type=float, default=1.0,
                    help="mean draw as a fraction of TDP (default 1.0 = worst case)")
    a = ap.parse_args()

    lines = sacct_rows(a.since, (date.today() + timedelta(days=1)).isoformat())

    gpuh = defaultdict(float)
    njob = defaultdict(int)
    unknown = 0
    seen = set()
    for line in lines:
        f = line.split("|")
        if len(f) < 5:
            continue
        jid, part, el, tres, state = f[0], f[1], f[2], f[3], f[4]
        if jid in seen:          # windows are half-open but overlap at boundaries
            continue
        seen.add(jid)
        n = ngpu(tres)
        if n == 0:
            continue
        dev = dev_of(part, tres)
        if dev is None:
            unknown += 1
            continue
        gpuh[dev] += n * secs(el) / 3600.0
        njob[dev] += 1

    if not gpuh:
        raise SystemExit("no GPU jobs found — widen --since, or sacct has aged out "
                         "(most sites purge accounting after N months)")

    print(f"GPU-hours since {a.since}   (PUE {a.pue}, {a.util:.0%} of TDP, "
          f"{a.gco2:g} gCO2e/kWh)")
    print("=" * 78)
    print(f"{'device':<8} {'TDP W':>6} {'jobs':>7} {'GPU-hours':>12} {'kWh':>10} {'kgCO2e':>9}")
    tot_h = tot_kwh = 0.0
    for dev in sorted(gpuh, key=lambda d: -gpuh[d]):
        kwh = gpuh[dev] * TDP[dev] * a.util * a.pue / 1000.0
        tot_h += gpuh[dev]
        tot_kwh += kwh
        print(f"{dev:<8} {TDP[dev]:>6} {njob[dev]:>7} {gpuh[dev]:>12,.1f} "
              f"{kwh:>10,.1f} {kwh * a.gco2 / 1000:>9,.1f}")
    print("-" * 78)
    print(f"{'TOTAL':<8} {'':>6} {sum(njob.values()):>7} {tot_h:>12,.1f} "
          f"{tot_kwh:>10,.1f} {tot_kwh * a.gco2 / 1000:>9,.1f}")
    if unknown:
        print(f"\n⚠ {unknown} GPU job(s) had an unrecognised device — NOT counted.")
    print(f"\nEquivalents (rough): {tot_kwh / 30:,.0f} average US household-days of "
          f"electricity; {tot_kwh * a.gco2 / 1000 / 4600:,.2f} passenger-car-years.")
    print("\n⚠ ESTIMATE, NOT A MEASUREMENT. TDP overstates real draw (inference "
          "typically 50-80% of TDP — try --util 0.7); CPU/RAM/storage/network are "
          "EXCLUDED. State PUE and grid intensity as assumptions if you report this.")
    print("⚠ sacct only covers jobs still in the accounting DB; anything purged is "
          "silently missing, so this is a LOWER bound on job coverage.")


if __name__ == "__main__":
    main()
