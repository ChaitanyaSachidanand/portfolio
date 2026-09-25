"""Jev (TypeSafe AI) as a decision engine: it judges market state, it never sizes or places orders.

Layering, as in the fund prompt:
  Jev          -> typed judgments (regime, direction) with probabilities and confidence
  risk.py      -> deterministic limits; nothing Jev says can loosen them
  broker.py    -> paper fills

API notes (Sept 2026): TypeSafe documents one endpoint, POST https://api.typesafe.ai/v1/systemone,
with the API key in TYPESAFE_API_KEY and model "jev-latest". The question format below mirrors the
public jev-trader project (github.com/jarrodwatts/jev-trader, src/model.ts): each question has a
"type", "instructions" and "criteria"; each answer has "choice" and "probabilities".
The exact raw-HTTP field names could not be verified from this environment, so parse_answers()
is defensive. Check a live response with `python -m trading_lab jev-ping` before relying on it.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.request
from dataclasses import dataclass

from .data import Candle
from .strategies import realized_vol, rsi, sma

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"

QUESTIONS = {
    "regime": {
        "type": "choice",
        "instructions": {
            "question": "What regime is this market in right now?",
            "goal": "Classify the market so deterministic code can decide whether a long spot position is allowed.",
            "inputs": "returns_pct are close-to-close returns over the last N bars. realized_vol_pct is per-bar "
                      "volatility. drawdown_from_high_pct is distance below the recent high. rsi_14 is momentum.",
        },
        "criteria": {
            "trending": "Persistent move in one direction, price on one side of its averages.",
            "mean_reverting": "Choppy, range-bound, moves tend to reverse.",
            "high_vol": "Volatility well above normal but no disorderly selling.",
            "crisis": "Disorderly selling, large fast drawdown, liquidity likely thin.",
        },
    },
    "direction": {
        "type": "choice",
        "instructions": {
            "question": "Over the next `horizon_bars` bars, is the close more likely to be higher or lower than now?",
            "goal": "Decide whether holding spot is worth it. A round trip costs about `round_trip_cost_pct`, "
                    "so only say up if the expected move beats that cost.",
        },
        "criteria": {
            "up": "Close more likely higher after horizon_bars, by more than the round-trip cost.",
            "down_or_flat": "Close more likely lower, or the move will not beat the cost.",
        },
    },
}


@dataclass
class JevDecision:
    regime: str
    regime_probs: dict[str, float]
    direction: str
    p_up: float
    confidence: float
    latency_ms: float


def build_state(candles: list[Candle], horizon_bars: int, round_trip_cost_pct: float) -> dict:
    """Compact, relative, human-readable state. No absolute prices beyond the last close."""
    closes = [c.close for c in candles]
    last = closes[-1]

    def ret(n: int) -> float:
        return round((last / closes[-1 - n] - 1) * 100, 3)

    high = max(c.high for c in candles[-72:])
    return {
        "market": "spot, long-only",
        "last_close": last,
        "horizon_bars": horizon_bars,
        "round_trip_cost_pct": round(round_trip_cost_pct, 3),
        "returns_pct": {"last1": ret(1), "last4": ret(4), "last24": ret(24), "last72": ret(72)},
        "realized_vol_pct": {"last24": round(realized_vol(closes, 24) * 100, 3),
                             "last72": round(realized_vol(closes, 72) * 100, 3)},
        "vs_sma_pct": {"sma24": round((last / sma(closes, 24) - 1) * 100, 3),
                       "sma96": round((last / sma(closes, 96) - 1) * 100, 3)},
        "rsi_14": round(rsi(closes, 14), 1),
        "drawdown_from_high_pct": round((last / high - 1) * 100, 3),
    }


STATE_BARS = 97  # candles build_state needs


def confidence_from(probs: dict[str, float]) -> float:
    """1 - normalized entropy: 1.0 = certain, 0.0 = uniform. Used if the API omits confidence."""
    ps = [p for p in probs.values() if p > 0]
    if len(probs) < 2 or not ps:
        return 0.0
    h = -sum(p * math.log(p) for p in ps)
    return max(0.0, 1 - h / math.log(len(probs)))


def parse_answers(body: dict, latency_ms: float) -> JevDecision:
    answers = body.get("answers", body)
    reg, dirn = answers["regime"], answers["direction"]
    reg_probs = reg.get("probabilities") or {reg["choice"]: 1.0}
    dir_probs = dirn.get("probabilities") or {dirn["choice"]: 1.0}
    p_up = float(dir_probs.get("up", 1.0 if dirn["choice"] == "up" else 0.0))
    conf = dirn.get("confidence")
    return JevDecision(
        regime=reg["choice"],
        regime_probs={k: float(v) for k, v in reg_probs.items()},
        direction=dirn["choice"],
        p_up=p_up,
        confidence=float(conf) if conf is not None else confidence_from({"up": p_up, "down_or_flat": 1 - p_up}),
        latency_ms=latency_ms,
    )


class JevClient:
    def __init__(self, api_key: str | None = None, model: str = "jev-latest", timeout: float = 5.0):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not self.api_key:
            raise RuntimeError("set TYPESAFE_API_KEY (or use --jev mock)")
        self.model, self.timeout = model, timeout

    def raw(self, state: dict) -> dict:
        req = urllib.request.Request(
            JEV_ENDPOINT,
            data=json.dumps({"model": self.model, "state": state, "questions": QUESTIONS}).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)

    def decide(self, state: dict) -> JevDecision:
        t0 = time.perf_counter()
        body = self.raw(state)
        return parse_answers(body, (time.perf_counter() - t0) * 1000)


class MockJev:
    """Offline stand-in with the same interface: a logistic blend of momentum and trend.

    It exists so backtests, learning and tests run without an API key. It is not Jev.
    """

    def decide(self, state: dict) -> JevDecision:
        r, v = state["returns_pct"], state["realized_vol_pct"]
        score = r["last24"] / max(v["last24"] * 5, 1e-6) + state["vs_sma_pct"]["sma96"] / max(v["last72"] * 10, 1e-6)
        p_up = 1 / (1 + math.exp(-score))
        if state["drawdown_from_high_pct"] < -15 or v["last24"] > 2.5 * v["last72"]:
            regime = "crisis"
        elif v["last24"] > 1.5 * v["last72"]:
            regime = "high_vol"
        elif abs(state["vs_sma_pct"]["sma96"]) > 2 * v["last72"]:
            regime = "trending"
        else:
            regime = "mean_reverting"
        return JevDecision(regime, {regime: 1.0}, "up" if p_up >= 0.5 else "down_or_flat", p_up,
                           confidence_from({"up": p_up, "down_or_flat": 1 - p_up}), 0.0)
