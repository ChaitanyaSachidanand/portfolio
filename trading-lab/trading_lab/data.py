"""Market data: Binance public klines (no API key), CSV cache, synthetic data."""
from __future__ import annotations

import csv
import json
import math
import os
import random
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

# Documented public endpoints: GET /api/v3/klines (max 1000 rows) and GET /api/v3/ticker/price.
# Binance.com blocks some countries (including the US); set TRADING_LAB_BINANCE=https://api.binance.us,
# which serves the same endpoints for its own markets.
BINANCE_BASE = os.environ.get("TRADING_LAB_BINANCE", "https://api.binance.com").rstrip("/")
BINANCE_KLINES = f"{BINANCE_BASE}/api/v3/klines"
BINANCE_TICKER = f"{BINANCE_BASE}/api/v3/ticker/price"

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


@dataclass(frozen=True)
class Candle:
    open_time: int  # ms since epoch, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Candle]:
    """Fetch closed candles in [start_ms, end_ms), paginating 1000 at a time."""
    step = INTERVAL_MS[interval]
    now = int(time.time() * 1000)
    out: list[Candle] = []
    cursor = start_ms
    while cursor < end_ms:
        query = urllib.parse.urlencode(
            {"symbol": symbol, "interval": interval, "startTime": cursor, "endTime": end_ms - 1, "limit": 1000}
        )
        with urllib.request.urlopen(f"{BINANCE_KLINES}?{query}", timeout=15) as resp:
            rows = json.load(resp)
        if not rows:
            break
        for r in rows:
            close_time = int(r[6])
            if close_time >= now:  # still forming; never trade on it
                continue
            out.append(Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
        cursor = int(rows[-1][0]) + step
        time.sleep(0.2)  # stay well under rate limits
    return out


def fetch_ticker(symbol: str) -> float:
    """Latest trade price, for marking the paper account between closed bars."""
    query = urllib.parse.urlencode({"symbol": symbol})
    with urllib.request.urlopen(f"{BINANCE_TICKER}?{query}", timeout=10) as resp:
        return float(json.load(resp)["price"])


def fetch_recent(symbol: str, interval: str, bars: int) -> list[Candle]:
    step = INTERVAL_MS[interval]
    end = int(time.time() * 1000)
    return fetch_klines(symbol, interval, end - (bars + 1) * step, end)


def save_csv(path: str, candles: list[Candle]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["open_time", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.open_time, c.open, c.high, c.low, c.close, c.volume])


def load_csv(path: str) -> list[Candle]:
    with open(path, newline="") as f:
        return [
            Candle(int(r["open_time"]), float(r["open"]), float(r["high"]), float(r["low"]),
                   float(r["close"]), float(r["volume"]))
            for r in csv.DictReader(f)
        ]


def load_or_fetch(symbol: str, interval: str, days: int, cache_dir: str = "data") -> list[Candle]:
    path = os.path.join(cache_dir, f"{symbol}_{interval}_{days}d.csv")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 6 * 3600:
        return load_csv(path)
    end = int(time.time() * 1000)
    candles = fetch_klines(symbol, interval, end - days * 86_400_000, end)
    save_csv(path, candles)
    return candles


class ReplayFeed:
    """Plays synthetic candles as if they were arriving live: one new closed bar per `seconds_per_bar`.

    For demos and testing the dashboard offline. Everything it produces is labeled SIMULATED.
    """

    def __init__(self, candles: list[Candle], start_index: int, seconds_per_bar: float, interval: str):
        self.candles, self.start, self.speed = candles, start_index, seconds_per_bar
        self.step_ms = INTERVAL_MS[interval]
        self.t0 = time.time()

    def index(self) -> int:
        return min(self.start + int((time.time() - self.t0) / self.speed), len(self.candles) - 1)

    def now_ms(self) -> int:
        """Simulated clock: one minute after the latest bar closed."""
        return self.candles[self.index()].open_time + self.step_ms + 60_000

    def fetch(self, symbol: str, interval: str, bars: int) -> list[Candle]:
        i = self.index()
        return self.candles[max(0, i + 1 - bars): i + 1]

    def ticker(self, symbol: str) -> float:
        c = self.candles[self.index()]
        frac = ((time.time() - self.t0) / self.speed) % 1.0
        nxt = self.candles[min(self.index() + 1, len(self.candles) - 1)]
        return c.close + (nxt.open - c.close) * frac


def synthetic(bars: int, interval: str = "1h", seed: int = 7, start_price: float = 60_000.0) -> list[Candle]:
    """Random-walk prices with switching trend/chop/crash regimes, for offline testing.

    This is NOT a model of real markets; it only exercises the code paths.
    """
    rng = random.Random(seed)
    step = INTERVAL_MS[interval]
    hourly = step / 3_600_000
    regimes = [(0.0004, 0.006), (0.0, 0.004), (-0.0003, 0.008), (-0.0015, 0.015)]  # drift, vol per hour
    regime = 0
    t = 1_700_000_000_000
    price = start_price
    out = []
    for _ in range(bars):
        if rng.random() < 0.005:
            regime = rng.choices(range(4), weights=[4, 4, 3, 1])[0]
        drift, vol = regimes[regime]
        r = rng.gauss(drift * hourly, vol * math.sqrt(hourly))
        o = price
        c = o * math.exp(r)
        wick = abs(rng.gauss(0, vol * math.sqrt(hourly) / 2))
        out.append(Candle(t, o, max(o, c) * (1 + wick), min(o, c) * (1 - wick), c, rng.uniform(100, 1000)))
        price = c
        t += step
    return out
