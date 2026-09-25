"""Nightly review: score every Jev call against what happened, refit calibration, audit the gates.

  1. Label each decision after the fact: did the close `horizon` bars later end above the close then?
  2. Score raw and calibrated p_long with Brier, against always guessing the base rate.
  3. Gate audit: for each entry gate, how many skipped bars went up (winners missed) vs down (losers avoided).
  4. Refit the calibration map and write it as PENDING. A human promotes it with `approve-calibration`.

Nothing here touches risk limits, gate thresholds or the live calibration file.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass

from .jev import Calibration
from .learn import brier, calibration_report


@dataclass
class Record:
    bar_open_time: int
    close: float
    p_long: float
    p_cal: float
    gates: list


def records_from_journal(journal_path: str) -> tuple[list[Record], dict[int, float]]:
    with open(journal_path) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = [r for r in rows if "decision_id" in r]
    closes = {r["bar_open_time"]: r["close"] for r in rows}
    recs = [Record(r["bar_open_time"], r["close"], r["jev"]["p_long"], r.get("p_long_cal", r["jev"]["p_long"]),
                   r.get("gates", [])) for r in rows if r.get("jev") and r["jev"]["source"] != "abstain"]
    return recs, closes


def label(recs: list[Record], closes: dict[int, float], horizon_ms: int) -> list[tuple[Record, int]]:
    out = []
    for r in recs:
        later = closes.get(r.bar_open_time + horizon_ms)
        if later is not None:
            out.append((r, int(later > r.close)))
    return out


def fit(labeled: list[tuple[Record, int]], current: Calibration) -> Calibration:
    new = Calibration(edges=list(current.edges), win_rate=[None] * len(current.edges),
                      samples=[0] * len(current.edges), version=current.version + 1)
    wins = [0] * len(current.edges)
    for r, y in labeled:
        i = max(j for j, e in enumerate(new.edges) if r.p_long >= e)
        new.samples[i] += 1
        wins[i] += y
    for i, n in enumerate(new.samples):
        new.win_rate[i] = round(wins[i] / n, 4) if n else None
    # Calibrated probabilities must not fall as raw p rises: pool adjacent violators (PAVA).
    blocks = [[wins[i], n, [i]] for i, n in enumerate(new.samples) if n]
    merged: list = []
    for b in blocks:
        merged.append(b)
        while len(merged) > 1 and merged[-2][0] / merged[-2][1] > merged[-1][0] / merged[-1][1]:
            w, n, idx = merged.pop()
            merged[-1][0] += w
            merged[-1][1] += n
            merged[-1][2] += idx
    for w, n, idx in merged:
        for i in idx:
            new.win_rate[i] = round(w / n, 4)
    return new


def gate_audit(labeled: list[tuple[Record, int]]) -> str:
    stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r, y in labeled:
        for g in r.gates:
            key = g.split(" ")[0]
            stats[key][0 if y else 1] += 1
    if not stats:
        return "gate audit: no blocked entries"
    lines = ["gate audit (bars blocked by each gate):  went up = winner missed, went down = loser avoided"]
    for key, (up, down) in sorted(stats.items(), key=lambda kv: -sum(kv[1])):
        lines.append(f"  {key:<14} blocked {up + down:<5} went up {up:<5} went down {down}")
    return "\n".join(lines)


def nightly(recs: list[Record], closes: dict[int, float], horizon_ms: int,
            calibration_path: str = "calibration.json", pending_path: str = "calibration.pending.json") -> str:
    labeled = label(recs, closes, horizon_ms)
    out = ["raw p_long:", calibration_report([(r.p_long, y) for r, y in labeled])]
    if len(labeled) >= 30:
        out += [f"calibrated p_long: Brier {brier([(r.p_cal, y) for r, y in labeled]):.4f}"]
    out.append(gate_audit(labeled))
    if len(labeled) >= 30:
        current = Calibration.load(calibration_path)
        proposal = fit(labeled, current)
        proposal.save(pending_path)
        after = brier([(proposal.apply(r.p_long), y) for r, y in labeled])
        out.append(f"proposed calibration v{proposal.version} written to {pending_path} "
                   f"(in-sample Brier {after:.4f}; in-sample always looks better, judge it on the next nights). "
                   f"Promote with: python -m trading_lab approve-calibration")
    return "\n".join(out)


def approve(pending_path: str = "calibration.pending.json", calibration_path: str = "calibration.json") -> str:
    if not os.path.exists(pending_path):
        return "no pending calibration"
    os.replace(pending_path, calibration_path)
    return f"calibration v{Calibration.load(calibration_path).version} is now live"
