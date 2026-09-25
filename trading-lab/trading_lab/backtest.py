"""Event-driven backtest. Decide on bar i's close, fill at bar i+1's open: no lookahead."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from .broker import CostModel, PaperBroker
from .data import INTERVAL_MS, Candle
from .risk import RiskEngine, RiskLimits
from .strategies import Strategy


@dataclass
class Result:
    strategy_id: str
    start_equity: float
    end_equity: float
    total_return: float
    max_drawdown: float
    sharpe: float
    trades: int
    fees_paid: float
    time_in_market: float
    buy_hold_return: float
    risk_events: dict[str, int] = field(default_factory=dict)
    equity_curve: list[float] = field(default_factory=list)

    def summary(self) -> str:
        events = ", ".join(f"{k}={v}" for k, v in sorted(self.risk_events.items())) or "none"
        return (
            f"strategy           {self.strategy_id}\n"
            f"equity             ${self.start_equity:,.2f} -> ${self.end_equity:,.2f}\n"
            f"total return       {self.total_return:+.2%}   (buy & hold {self.buy_hold_return:+.2%})\n"
            f"max drawdown       {self.max_drawdown:.2%}\n"
            f"sharpe (annual)    {self.sharpe:.2f}\n"
            f"trades             {self.trades}   fees ${self.fees_paid:,.2f}\n"
            f"time in market     {self.time_in_market:.0%}\n"
            f"risk events        {events}"
        )


def utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def run_backtest(
    candles: list[Candle],
    strategy: Strategy,
    capital: float = 30.0,
    limits: RiskLimits = RiskLimits(kill_file=""),
    costs: CostModel = CostModel(),
    interval: str = "1h",
) -> Result:
    if len(candles) <= strategy.warmup + 1:
        raise ValueError(f"need more than {strategy.warmup + 1} candles, got {len(candles)}")

    broker = PaperBroker(capital, costs)
    risk = RiskEngine(limits)
    peak = capital
    day, day_start = None, capital
    curve = [capital]
    trades = 0
    bars_in_market = 0
    events: dict[str, int] = {}

    for i in range(strategy.warmup, len(candles) - 1):
        bar, nxt = candles[i], candles[i + 1]
        equity = broker.equity(bar.close)
        peak = max(peak, equity)
        if utc_day(bar.open_time) != day:
            day, day_start = utc_day(bar.open_time), equity

        window = candles[max(0, i - strategy.warmup - 1): i + 1]
        raw = strategy.target(window, broker.exposure(bar.close))
        decision = risk.check(raw, equity, peak, day_start)
        for reason in decision.reasons:
            key = reason.split("_from_")[0]
            events[key] = events.get(key, 0) + 1

        if broker.rebalance(decision.approved, nxt.open):
            trades += 1
        if broker.qty > 0:
            bars_in_market += 1
        curve.append(broker.equity(nxt.close))

    rets = [curve[k] / curve[k - 1] - 1 for k in range(1, len(curve)) if curve[k - 1] > 0]
    mean = sum(rets) / len(rets)
    std = math.sqrt(sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1))
    bars_per_year = 365 * 86_400_000 / INTERVAL_MS[interval]
    running_peak, mdd = curve[0], 0.0
    for v in curve:
        running_peak = max(running_peak, v)
        mdd = max(mdd, 1 - v / running_peak)

    first = candles[strategy.warmup + 1].open
    return Result(
        strategy_id=strategy.id,
        start_equity=capital,
        end_equity=curve[-1],
        total_return=curve[-1] / capital - 1,
        max_drawdown=mdd,
        sharpe=0.0 if std == 0 else mean / std * math.sqrt(bars_per_year),
        trades=trades,
        fees_paid=broker.fees_paid,
        time_in_market=bars_in_market / (len(curve) - 1),
        buy_hold_return=candles[-1].close / first - 1,
        risk_events=events,
        equity_curve=curve,
    )


def stress_costs(costs: CostModel) -> CostModel:
    """Double fees, triple slippage: a strategy whose edge vanishes here has no real edge."""
    return replace(costs, fee_rate=costs.fee_rate * 2, slippage_bps=costs.slippage_bps * 3)
