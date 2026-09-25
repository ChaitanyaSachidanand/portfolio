"""Strategies turn candle history into a target exposure in [0, 1] (fraction of equity held long).

Strategies only ever see candles that have already closed. They never size orders or
trade; the risk engine and broker do that.
"""
from __future__ import annotations

from .data import Candle
from .strategies_math import atr, realized_vol, rsi, sma  # noqa: F401 (re-exported)


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


class Donchian(Strategy):
    """Daily-bar breakout, the rule behind the one public small-account system with checkable numbers
    (github.com/Wataru1987/gmo-coin-trend-lab; see docs/RESEARCH.md). Reimplemented, stateless variant.

    Entry: close above the prior `entry`-bar high and above the `trend_filter`-bar average.
    Exit:  close below the prior `exit`-bar low, or below (highest close of the last `entry` bars
           minus `stop_atr` x ATR), a stateless stand-in for a trailing stop.
    Size:  risk `risk_per_trade` of equity at a `stop_atr` x ATR stop, capped at `max_exposure`.
    Designed for --interval 1d.
    """

    def __init__(self, entry: int = 20, exit: int = 10, trend_filter: int = 50, atr_n: int = 14,
                 stop_atr: float = 2.0, risk_per_trade: float = 0.01, max_exposure: float = 0.4):
        self.entry, self.exit, self.trend_filter, self.atr_n = entry, exit, trend_filter, atr_n
        self.stop_atr, self.risk_per_trade, self.max_exposure = stop_atr, risk_per_trade, max_exposure
        self.id = f"donchian_{entry}_{exit}_{trend_filter}"
        self.warmup = max(entry, exit, trend_filter, atr_n) + 1

    def target(self, history, current_exposure):
        bar, closes = history[-1], [c.close for c in history]
        a = atr(history, self.atr_n)
        if current_exposure > 0.01:
            prior_low = min(c.low for c in history[-self.exit - 1:-1])
            trail = max(closes[-self.entry:]) - self.stop_atr * a
            return 0.0 if bar.close < prior_low or bar.close < trail else current_exposure
        prior_high = max(c.high for c in history[-self.entry - 1:-1])
        if bar.close > prior_high and bar.close > sma(closes, self.trend_filter) and a > 0:
            return min(self.max_exposure, self.risk_per_trade / (self.stop_atr * a / bar.close))
        return 0.0


class JevStrategy(Strategy):
    """Jev judges; deterministic gates here decide; the risk engine still has the last word.

    Entry needs ALL of: setup_quality >= min_setup, direction long with confidence >= min_confidence,
    calibrated p_long >= min_p_long, risk_state safe, regime not crisis, toxic_flow < max_toxic.
    Exits need no confidence: abstain or crisis -> flat; risk_state reduce -> halve; a confident
    short call -> flat. If a `brain` is attached, low confidence or crisis escalates to it, and it can
    only pause new entries or flatten.
    """

    def __init__(self, engine=None, min_confidence: float = 0.80, min_p_long: float = 0.55,
                 min_setup: float = 2.0, max_toxic: float = 0.5, horizon_bars: int = 4, cost_pct: float = 0.3,
                 target_vol: float = 0.008, calibration=None, brain=None, escalate_below: float = 0.60,
                 on_decision=None):
        from .jev import STATE_BARS, Calibration, MockJev
        self.engine = engine or MockJev()
        self.calibration = calibration or Calibration()
        self.min_confidence, self.min_p_long, self.min_setup, self.max_toxic = min_confidence, min_p_long, min_setup, max_toxic
        self.horizon_bars, self.cost_pct, self.target_vol = horizon_bars, cost_pct, target_vol
        self.brain, self.escalate_below, self.on_decision = brain, escalate_below, on_decision
        self.id = f"jev_c{min_confidence:.2f}_p{min_p_long:.2f}"
        self.warmup = STATE_BARS
        self.account = None          # set by the caller each bar: dd_pct, day_pct, exposure
        self.paused_until = 0        # bar open_time (ms) before which no new risk is taken
        self.last_decision = self.last_p_cal = self.last_verdict = None
        self.last_gates: list[str] = []

    def entry_gates(self, d, p_cal) -> list[str]:
        failed = []
        if d.setup_quality < self.min_setup:
            failed.append(f"setup {d.setup_quality:.2f}<{self.min_setup}")
        if d.direction != "long":
            failed.append(f"direction {d.direction}")
        if d.direction_conf < self.min_confidence:
            failed.append(f"confidence {d.direction_conf:.2f}<{self.min_confidence}")
        if p_cal < self.min_p_long:
            failed.append(f"p_long_cal {p_cal:.3f}<{self.min_p_long}")
        if d.risk_state != "safe":
            failed.append(f"risk_state {d.risk_state}")
        if d.toxic_flow >= self.max_toxic:
            failed.append(f"toxic {d.toxic_flow:.2f}")
        return failed

    def target(self, history, current_exposure):
        from .jev import build_state
        now = history[-1].open_time
        state = build_state(history, self.horizon_bars, self.cost_pct, self.account)
        d = self.engine.decide(state)
        p_cal = self.calibration.apply(d.p_long)
        self.last_decision, self.last_p_cal, self.last_verdict, self.last_gates = d, p_cal, None, []
        result = self._decide(history, current_exposure, now, state, d, p_cal)
        if self.on_decision:
            self.on_decision(history[-1], d, p_cal, list(self.last_gates))
        return result

    def _decide(self, history, current_exposure, now, state, d, p_cal):

        if self.brain and d.source == "jev" and now >= self.paused_until and \
                (d.regime == "crisis" or d.confidence < self.escalate_below):
            v = self.brain.review({"snapshot": state, "jev": d.to_dict(), "p_long_calibrated": p_cal,
                                   "position_exposure": current_exposure})
            self.last_verdict = v
            if v.action in ("pause", "flatten"):
                self.paused_until = now + max(v.pause_hours, 1) * 3_600_000
            if v.action == "flatten":
                return 0.0

        if d.source == "abstain" or d.regime == "crisis":
            self.last_gates = [f"exit: {d.source if d.source == 'abstain' else 'crisis'}"]
            return 0.0
        if current_exposure > 0.01:
            if d.risk_state == "reduce":
                return current_exposure / 2
            if d.direction == "short" and d.direction_conf >= self.min_confidence:
                return 0.0
            return current_exposure
        if now < self.paused_until:
            self.last_gates = ["paused by brain"]
            return 0.0
        self.last_gates = self.entry_gates(d, p_cal)
        if self.last_gates:
            return 0.0
        vol = state["atr_pct"] / 100
        return 1.0 if vol == 0 else min(1.0, self.target_vol / vol)


STRATEGIES = {
    "buy_hold": BuyAndHold,
    "trend": TrendFollow,
    "meanrev": MeanReversion,
    "donchian": Donchian,
    "jev": JevStrategy,
}
