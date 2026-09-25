"""Controlled self-improvement.

The lab learns in exactly three ways, all of them auditable:
  1. Walk-forward parameter search: pick parameters on a past window, score them only on the
     *following* unseen window, roll forward. The stitched out-of-sample (OOS) result is the only
     number that counts.
  2. Champion/challenger promotion: new parameters replace the current ones only if their OOS
     result is better under stressed costs and stays inside the drawdown limit. Every attempt is
     logged in learn_log.jsonl; the registry keeps versions.
  3. Calibration: every Jev probability is later scored against what actually happened (Brier
     score). A model whose probabilities are no better than the base rate is not trusted.

It never touches risk limits, and it never trades.
"""
from __future__ import annotations

import itertools
import json
import os
import time
from dataclasses import dataclass

from .backtest import run_backtest, stress_costs
from .broker import CostModel
from .data import Candle
from .risk import RiskLimits
from .strategies import STRATEGIES

GRIDS = {
    "trend": {"fast": [10, 20, 50], "slow": [100, 200]},
    "meanrev": {"entry": [25, 30, 35], "exit": [50, 60]},
    "donchian": {"entry": [20, 55], "exit": [10, 20], "trend_filter": [50, 100]},
    "jev": {"min_confidence": [0.3, 0.5, 0.8], "min_p_long": [0.5, 0.55, 0.6]},
}


def grid(name: str) -> list[dict]:
    g = GRIDS[name]
    keys = list(g)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(g[k] for k in keys))
            if not (name == "trend" and vals[0] >= vals[1])]


@dataclass
class WalkForward:
    strategy: str
    folds: int
    oos_return: float           # stitched, stressed costs
    oos_buy_hold: float
    oos_max_drawdown: float
    oos_sharpe: float           # mean of per-fold sharpes
    picks: list[dict]
    latest_params: dict         # best on the most recent training window: the challenger

    def summary(self) -> str:
        return (f"{self.strategy}: {self.folds} folds, OOS return {self.oos_return:+.2%} "
                f"(buy & hold {self.oos_buy_hold:+.2%}), OOS max DD {self.oos_max_drawdown:.2%}, "
                f"OOS sharpe {self.oos_sharpe:.2f}\n  challenger params {self.latest_params}")


def best_params(name, candles, capital, limits, costs, interval) -> dict:
    scored = []
    for params in grid(name):
        r = run_backtest(candles, STRATEGIES[name](**params), capital, limits, stress_costs(costs), interval)
        scored.append((r.sharpe, r.total_return, params))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return scored[0][2]


def walk_forward(name: str, candles: list[Candle], train_bars: int, test_bars: int, capital: float = 30.0,
                 limits: RiskLimits = RiskLimits(kill_file=""), costs: CostModel = CostModel(),
                 interval: str = "1h") -> WalkForward:
    warm = max(STRATEGIES[name](**p).warmup for p in grid(name)) + 2
    if len(candles) < train_bars + test_bars:
        raise ValueError(f"need {train_bars + test_bars} candles, have {len(candles)}")
    # Each fold starts a fresh backtest, so the drawdown breaker resets per fold; the stitched
    # drawdown can therefore exceed the limit. That makes promotion stricter, not looser.
    equity, bh, peak, mdd = 1.0, 1.0, 1.0, 0.0
    sharpes, picks = [], []
    start = train_bars
    while start + test_bars <= len(candles):
        params = best_params(name, candles[start - train_bars:start], capital, limits, costs, interval)
        test = candles[start - warm:start + test_bars]  # warmup bars precede the test window
        r = run_backtest(test, STRATEGIES[name](**params), capital, limits, stress_costs(costs), interval)
        for k in range(1, len(r.equity_curve)):
            v = equity * r.equity_curve[k] / capital
            peak, mdd = max(peak, v), max(mdd, 1 - v / peak)
        equity *= r.end_equity / capital
        bh *= candles[start + test_bars - 1].close / candles[start].open
        sharpes.append(r.sharpe)
        picks.append(params)
        start += test_bars
    latest = best_params(name, candles[-train_bars:], capital, limits, costs, interval)
    return WalkForward(name, len(picks), equity - 1, bh - 1, mdd, sum(sharpes) / len(sharpes), picks, latest)


class Registry:
    """Champion parameters per strategy, versioned. Paper trading reads from here."""

    def __init__(self, path: str = "registry.json", log_path: str = "learn_log.jsonl"):
        self.path, self.log_path = path, log_path
        self.data = {}
        if os.path.exists(path):
            with open(path) as f:
                self.data = json.load(f)

    def champion(self, name: str) -> dict | None:
        return self.data.get(name)

    def consider(self, wf: WalkForward, limits: RiskLimits, min_improvement: float = 0.05) -> tuple[bool, str]:
        champ = self.champion(wf.strategy)
        if wf.oos_return <= 0:
            ok, why = False, "rejected: out-of-sample return <= 0 under stressed costs"
        elif wf.oos_max_drawdown >= limits.max_drawdown:
            ok, why = False, f"rejected: OOS drawdown {wf.oos_max_drawdown:.1%} >= limit {limits.max_drawdown:.0%}"
        elif wf.oos_return <= wf.oos_buy_hold and wf.oos_max_drawdown >= 0.5 * limits.max_drawdown:
            ok, why = False, "rejected: no better than buy & hold, without a big enough drawdown benefit"
        elif champ and wf.oos_sharpe < champ["oos_sharpe"] + min_improvement:
            ok, why = False, f"rejected: OOS sharpe {wf.oos_sharpe:.2f} does not beat champion {champ['oos_sharpe']:.2f}"
        else:
            ok, why = True, "promoted"
        entry = {"ts": int(time.time()), "strategy": wf.strategy, "decision": why,
                 "params": wf.latest_params, "oos_return": wf.oos_return, "oos_sharpe": wf.oos_sharpe,
                 "oos_max_drawdown": wf.oos_max_drawdown, "oos_buy_hold": wf.oos_buy_hold, "folds": wf.folds}
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        if ok:
            version = (champ or {}).get("version", 0) + 1
            self.data[wf.strategy] = {**entry, "version": version}
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)
        return ok, why


def brier(pairs: list[tuple[float, int]]) -> float:
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def calibration_report(pairs: list[tuple[float, int]]) -> str:
    """Brier score vs. always predicting the base rate, plus a reliability table."""
    if len(pairs) < 30:
        return f"only {len(pairs)} scored predictions; need at least 30 before calibration means anything"
    base = sum(y for _, y in pairs) / len(pairs)
    b, b0 = brier(pairs), brier([(base, y) for _, y in pairs])
    skill = f"skill {1 - b / b0:+.1%}" if b0 > 0 else "skill n/a (every outcome was the same)"
    lines = [f"predictions {len(pairs)}   Brier {b:.4f}   base-rate Brier {b0:.4f}   "
             f"{skill} ({'better' if b < b0 else 'NOT better'} than guessing the base rate)",
             "p bucket      n     predicted  actual"]
    for lo in [0.0, 0.2, 0.4, 0.5, 0.6, 0.8]:
        hi = {0.0: 0.2, 0.2: 0.4, 0.4: 0.5, 0.5: 0.6, 0.6: 0.8, 0.8: 1.01}[lo]
        bucket = [(p, y) for p, y in pairs if lo <= p < hi]
        if bucket:
            lines.append(f"{lo:.1f}-{min(hi, 1):.1f}      {len(bucket):<5} {sum(p for p, _ in bucket) / len(bucket):.3f}"
                         f"      {sum(y for _, y in bucket) / len(bucket):.3f}")
    return "\n".join(lines)


def score_decisions(decisions: list[tuple[int, float, float]], closes: dict[int, float],
                    horizon_ms: int) -> list[tuple[float, int]]:
    """decisions: (bar_open_time, close_at_decision, p_up). Outcome = close after horizon > close now."""
    pairs = []
    for t, close, p_up in decisions:
        later = closes.get(t + horizon_ms)
        if later is not None:
            pairs.append((p_up, int(later > close)))
    return pairs
