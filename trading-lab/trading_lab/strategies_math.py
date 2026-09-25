"""Indicator helpers shared by strategies and the Jev state builder."""
from __future__ import annotations

import math


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


def atr(candles, n: int) -> float:
    """Average true range over the last n bars."""
    trs = []
    for i in range(len(candles) - n, len(candles)):
        c, prev = candles[i], candles[i - 1].close
        trs.append(max(c.high - c.low, abs(c.high - prev), abs(c.low - prev)))
    return sum(trs) / n
