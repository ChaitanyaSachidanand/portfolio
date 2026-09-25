"""Strategies turn candle history into a target exposure in [0, 1] (fraction of equity held long).

Strategies only ever see candles that have already closed. They never size orders or
trade; the risk engine and broker do that.
"""
from __future__ import annotations

import math

from .data import Candle


def sma(values: list[float], n: int) -> float:
    return sum(values[-n:]) / n


def realized_vol(closes: list[float], n: int) -> float:
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes))]
    mean = sum(rets) / n
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1))


def rsi(closes: list[float], n: int) -> float:
    gains = losses = 0.0
    for i in range(len(closes) - n, len(closes)):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


class Strategy:
    id = "base"
    warmup = 1

    def target(self, history: list[Candle], current_exposure: float) -> float:
        raise NotImplementedError


class BuyAndHold(Strategy):
    """Baseline: every other strategy should be judged against this."""

    id = "buy_hold"

    def target(self, history, current_exposure):
        return 1.0


class TrendFollow(Strategy):
    """Long when the fast average is above the slow one, sized down when volatility is high."""

    def __init__(self, fast: int = 20, slow: int = 100, vol_window: int = 48, target_vol: float = 0.008):
        self.fast, self.slow, self.vol_window, self.target_vol = fast, slow, vol_window, target_vol
        self.id = f"trend_{fast}_{slow}"
        self.warmup = max(slow, vol_window + 1)

    def target(self, history, current_exposure):
        closes = [c.close for c in history[-self.warmup - 1:]]
        if sma(closes, self.fast) <= sma(closes, self.slow) or closes[-1] <= sma(closes, self.slow):
            return 0.0
        vol = realized_vol(closes, self.vol_window)
        return 1.0 if vol == 0 else min(1.0, self.target_vol / vol)


class MeanReversion(Strategy):
    """Buy oversold dips (low RSI) only while the longer trend is up; exit when RSI recovers."""

    def __init__(self, period: int = 14, entry: float = 30, exit: float = 55, trend: int = 200):
        self.period, self.entry, self.exit, self.trend = period, entry, exit, trend
        self.id = f"meanrev_{period}_{int(entry)}_{int(exit)}"
        self.warmup = max(trend, period + 1)

    def target(self, history, current_exposure):
        closes = [c.close for c in history[-self.warmup - 1:]]
        value = rsi(closes, self.period)
        in_uptrend = closes[-1] > sma(closes, self.trend)
        if current_exposure > 0.01:
            return 0.0 if value >= self.exit or not in_uptrend else current_exposure
        return 0.5 if value <= self.entry and in_uptrend else 0.0


class JevStrategy(Strategy):
    """Jev judges regime and direction; deterministic gates here decide whether that is enough.

    Gates (from the fund prompt): no position in a crisis regime, and none unless Jev's confidence
    is at least `min_confidence` and p_up clears `min_p_up`. Size is volatility-scaled like TrendFollow.
    """

    def __init__(self, engine=None, min_confidence: float = 0.60, min_p_up: float = 0.55,
                 horizon_bars: int = 4, round_trip_cost_pct: float = 0.3, target_vol: float = 0.008,
                 on_decision=None):
        from .jev import STATE_BARS, MockJev
        self.engine = engine or MockJev()
        self.min_confidence, self.min_p_up = min_confidence, min_p_up
        self.horizon_bars, self.round_trip_cost_pct, self.target_vol = horizon_bars, round_trip_cost_pct, target_vol
        self.on_decision = on_decision
        self.last_decision = None
        self.id = f"jev_c{min_confidence:.2f}_p{min_p_up:.2f}"
        self.warmup = STATE_BARS

    def target(self, history, current_exposure):
        from .jev import build_state
        state = build_state(history, self.horizon_bars, self.round_trip_cost_pct)
        d = self.engine.decide(state)
        self.last_decision = d
        if self.on_decision:
            self.on_decision(history[-1], d)
        if d.regime == "crisis" or d.confidence < self.min_confidence or d.p_up < self.min_p_up:
            return 0.0
        vol = state["realized_vol_pct"]["last72"] / 100
        return 1.0 if vol == 0 else min(1.0, self.target_vol / vol)


STRATEGIES = {
    "buy_hold": BuyAndHold,
    "trend": TrendFollow,
    "meanrev": MeanReversion,
    "jev": JevStrategy,
}
