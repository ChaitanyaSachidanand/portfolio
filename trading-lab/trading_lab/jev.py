"""Jev (TypeSafe AI) as the reflex: typed judgments only. It never sizes or places orders.

Layering, as in the fund prompt and the public builds studied in docs/RESEARCH.md:
  Jev          -> five typed answers per bar, one request
  strategies   -> deterministic gates on those answers (JevStrategy)
  risk.py      -> hard limits; nothing a model says can loosen them
  brain.py     -> optional Opus 5.5 escalation that can only pause or flatten

Wire format: POST https://api.typesafe.ai/v1/systemone with {"model", "state", "questions"};
the response carries {"answers": {id: {...}}, "model", "usage"}. Choice answers have "choice",
"confidence" and "probabilities"; noul answers have "noul"; score answers have "score" and
"confidence". This matches TypeSafe's docs as cited by github.com/isaiahar027/trading; it could not be
fetched directly when this was written, so run `python -m trading_lab jev-ping` once with a real key.
Any error or malformed answer becomes JevDecision.abstain(), which the gates treat as "no trade".
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

from .data import Candle
from .strategies_math import atr, realized_vol, rsi, sma

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
REGIMES = ("trending", "mean_reverting", "high_vol", "crisis")
DIRECTIONS = ("long", "short", "neutral")
RISK_STATES = ("safe", "near_limit", "reduce")
STATE_BARS = 97  # candles build_state needs

QUESTIONS = {
    "regime": {
        "type": "choice",
        "instructions": "Which market regime does this snapshot show? Use `trend`, `vol`, `ret_pct` and `dd_from_high_pct`.",
        "criteria": {
            "trending": "`trend` is up/strong_up or down/strong_down and `ret_pct` points the same way; `vol` is not extreme.",
            "mean_reverting": "`trend` is flat, or `ret_pct` values disagree in sign; price oscillates inside its range.",
            "high_vol": "`vol` is high or extreme but there is no disorderly selling.",
            "crisis": "Disorderly market: `vol` is extreme AND `ret_pct.last24` is strongly negative, or `dd_from_high_pct` is below -15.",
        },
    },
    "direction": {
        "type": "choice",
        "instructions": "Over the next `horizon_bars` bars, which spot position is most likely to be profitable after "
                        "a round-trip cost of `cost_pct` percent? Shorting is not possible; short means stay out.",
        "criteria": {
            "long": "Trend and momentum favor higher prices by more than `cost_pct`.",
            "short": "Trend and momentum favor lower prices.",
            "neutral": "No clear edge, signals conflict, or the move is already exhausted.",
        },
    },
    "toxic_flow": {
        "type": "noul",
        "instructions": "Would a new long position likely be run over right now (capitulation, climactic volume at a "
                        "range extreme, a sharp move against the trend)? Use `vol`, `ret_pct.last1` and `volume_vs_avg`.",
        "criteria": {"true": "Entering now is dangerous.", "false": "Conditions look orderly."},
    },
    "setup_quality": {
        "type": "score",
        "instructions": "How good is the long setup right now?",
        "criteria": [
            "0: no setup. Signals conflict or there is nothing to trade.",
            "1: weak setup. One supporting signal, others neutral or opposed.",
            "2: good setup. `trend`, `ret_pct` and `rsi_14` mostly agree on higher prices and nothing contradicts it.",
            "3: excellent setup. Everything agrees, `vol` is not extreme, and price is not stretched far above `vs_sma_pct.sma96`.",
        ],
    },
    "risk_state": {
        "type": "choice",
        "instructions": "Given the account in `acct`, how should risk be treated right now?",
        "criteria": {
            "safe": "`acct.dd_pct` is below 5 and `acct.day_pct` is above -1.5.",
            "near_limit": "`acct.dd_pct` is between 5 and 10, or `acct.day_pct` is between -1.5 and -3.",
            "reduce": "`acct.dd_pct` is above 10, or `acct.day_pct` is below -3.",
        },
    },
}


class MalformedAnswer(ValueError):
    pass


@dataclass(frozen=True)
class JevDecision:
    regime: str
    regime_conf: float
    direction: str
    direction_conf: float
    direction_probs: dict
    toxic_flow: float
    setup_quality: float
    risk_state: str
    source: str = "jev"            # jev | mock | abstain
    note: str = ""
    latency_ms: float = 0.0

    @property
    def p_long(self) -> float:
        return float(self.direction_probs.get("long", 0.0))

    @property
    def confidence(self) -> float:
        """Weakest of the gating answers; used for escalation."""
        return min(self.direction_conf, self.regime_conf)

    def to_dict(self) -> dict:
        return {**asdict(self), "p_long": self.p_long}

    @classmethod
    def abstain(cls, reason: str) -> "JevDecision":
        return cls("crisis", 0.0, "neutral", 0.0, {}, 1.0, 0.0, "reduce", source="abstain", note=reason)


def _bucket(x: float, edges: list[float], names: list[str]) -> str:
    for edge, name in zip(edges, names):
        if x < edge:
            return name
    return names[-1]


def build_state(candles: list[Candle], horizon_bars: int, cost_pct: float, acct: dict | None = None) -> dict:
    """Compact, causal snapshot. Numbers are also turned into named buckets, which models read reliably."""
    closes = [c.close for c in candles]
    last = closes[-1]

    def ret(n: int) -> float:
        return round((last / closes[-1 - n] - 1) * 100, 2)

    vol24, vol72 = realized_vol(closes, 24), realized_vol(closes, 72)
    vs96 = (last / sma(closes, 96) - 1) * 100
    vs24 = (last / sma(closes, 24) - 1) * 100
    vol_ratio = vol24 / vol72 if vol72 else 1.0
    vols = [c.volume for c in candles[-24:]]
    high = max(c.high for c in candles[-72:])
    return {
        "market": "spot, long-only",
        "horizon_bars": horizon_bars,
        "cost_pct": round(cost_pct, 3),
        "trend": _bucket(vs96 / max(vol72 * 100, 1e-9), [-4, -1.5, 1.5, 4], ["strong_down", "down", "flat", "up", "strong_up"]),
        "vol": _bucket(vol_ratio, [0.7, 1.3, 2.0], ["low", "normal", "high", "extreme"]),
        "ret_pct": {"last1": ret(1), "last4": ret(4), "last24": ret(24), "last72": ret(72)},
        "vs_sma_pct": {"sma24": round(vs24, 2), "sma96": round(vs96, 2)},
        "rsi_14": round(rsi(closes, 14), 1),
        "atr_pct": round(atr(candles, 14) / last * 100, 3),
        "dd_from_high_pct": round((last / high - 1) * 100, 2),
        "volume_vs_avg": round(vols[-1] / (sum(vols) / len(vols)), 2) if sum(vols) else 1.0,
        "acct": acct or {"dd_pct": 0.0, "day_pct": 0.0, "exposure": 0.0},
    }


def _probs(name: str, answer: dict, options) -> dict:
    probs = answer.get("probabilities")
    if not probs:
        raise MalformedAnswer(f"{name}: no probabilities")
    if set(probs) != set(options):
        raise MalformedAnswer(f"{name}: options {sorted(probs)} != {sorted(options)}")
    if not 0.98 <= sum(probs.values()) <= 1.02:
        raise MalformedAnswer(f"{name}: probabilities sum to {sum(probs.values()):.3f}")
    return {k: float(v) for k, v in probs.items()}


def parse_answers(answers: dict, latency_ms: float = 0.0, source: str = "jev") -> JevDecision:
    try:
        reg, dirn, tox, setup, risk = (answers[k] for k in QUESTIONS)
        dir_probs = _probs("direction", dirn, DIRECTIONS)
        _probs("regime", reg, REGIMES)
        _probs("risk_state", risk, RISK_STATES)
        noul, score = float(tox["noul"]), float(setup["score"])
        decision = JevDecision(
            regime=reg["choice"], regime_conf=float(reg["confidence"]),
            direction=dirn["choice"], direction_conf=float(dirn["confidence"]), direction_probs=dir_probs,
            toxic_flow=noul, setup_quality=score, risk_state=risk["choice"],
            source=source, latency_ms=latency_ms,
        )
    except (KeyError, TypeError, ValueError) as e:
        raise MalformedAnswer(f"bad answer: {e!r}") from None
    if not (0 <= noul <= 1 and 0 <= score <= 3) or decision.regime not in REGIMES \
            or decision.direction not in DIRECTIONS or decision.risk_state not in RISK_STATES:
        raise MalformedAnswer(f"out of range: {decision}")
    return decision


class JevClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float = 5.0, retries: int = 2):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not self.api_key:
            raise RuntimeError("set TYPESAFE_API_KEY (or use --jev mock)")
        # Pin a version (e.g. JEV_MODEL=jev-1.13.0) once thresholds are tuned against it.
        self.model = model or os.environ.get("JEV_MODEL", "jev-latest")
        self.timeout, self.retries = timeout, retries

    def raw(self, state: dict) -> dict:
        body = json.dumps({"model": self.model, "state": state, "questions": QUESTIONS}).encode()
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(JEV_ENDPOINT, data=body, method="POST", headers={
                "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504, 529):
                    raise RuntimeError(f"Jev HTTP {e.code}: {e.read()[:300]!r}") from None
                last_err = e
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
            time.sleep(min(0.2 * 2 ** attempt, 2.0))
        raise RuntimeError(f"Jev unavailable: {last_err!r}")

    def decide(self, state: dict) -> JevDecision:
        t0 = time.perf_counter()
        try:
            body = self.raw(state)
            return parse_answers(body["answers"], (time.perf_counter() - t0) * 1000)
        except (RuntimeError, MalformedAnswer, KeyError, ValueError) as e:
            return JevDecision.abstain(f"jev_error: {e}")


def _softmax(scores: dict) -> dict:
    m = max(scores.values())
    ex = {k: math.exp(v - m) for k, v in scores.items()}
    total = sum(ex.values())
    return {k: v / total for k, v in ex.items()}


def _conf(probs: dict) -> float:
    """1 - normalized entropy."""
    h = -sum(p * math.log(p) for p in probs.values() if p > 0)
    return max(0.0, 1 - h / math.log(len(probs)))


class MockJev:
    """Offline stand-in with Jev's answer shape: a hand-written momentum heuristic.

    It exists so backtests, learning and tests run without a key. It is not Jev and has no known edge.
    """

    def raw(self, state: dict) -> dict:
        r, trend, vol = state["ret_pct"], state["trend"], state["vol"]
        t = {"strong_down": -2, "down": -1, "flat": 0, "up": 1, "strong_up": 2}[trend]
        crisis = (vol == "extreme" and r["last24"] < -5) or state["dd_from_high_pct"] < -15
        regime_scores = {"trending": abs(t) * 1.2, "mean_reverting": 1.5 - abs(t), "high_vol": 2.0 if vol in ("high", "extreme") else 0,
                         "crisis": 4.0 if crisis else -1}
        mom = t + r["last24"] / max(state["atr_pct"] * 5, 0.1)
        dir_probs = _softmax({"long": mom, "short": -mom, "neutral": 0.8})
        acct = state["acct"]
        risk = "reduce" if acct["dd_pct"] > 10 or acct["day_pct"] < -3 else \
            "near_limit" if acct["dd_pct"] > 5 or acct["day_pct"] < -1.5 else "safe"
        risk_probs = {k: (0.8 if k == risk else 0.1) for k in RISK_STATES}
        setup = max(0.0, min(3.0, 1.0 + t * 0.6 + (0.5 if 45 < state["rsi_14"] < 70 else -0.5) - (1 if vol == "extreme" else 0)))
        regime_probs = _softmax(regime_scores)
        return {"answers": {
            "regime": {"type": "choice", "choice": max(regime_probs, key=regime_probs.get),
                       "confidence": _conf(regime_probs), "probabilities": regime_probs},
            "direction": {"type": "choice", "choice": max(dir_probs, key=dir_probs.get),
                          "confidence": _conf(dir_probs), "probabilities": dir_probs},
            "toxic_flow": {"type": "noul", "noul": 0.8 if (vol == "extreme" or state["volume_vs_avg"] > 3) else 0.2},
            "setup_quality": {"type": "score", "score": setup, "confidence": 0.5},
            "risk_state": {"type": "choice", "choice": risk, "confidence": 0.7, "probabilities": risk_probs},
        }}

    def decide(self, state: dict) -> JevDecision:
        return parse_answers(self.raw(state)["answers"], 0.0, source="mock")


@dataclass
class Calibration:
    """Maps Jev's raw p_long to the win rate actually observed, per probability bin.

    Before evidence, p is shrunk halfway toward 0.5. As samples accumulate the observed
    rate takes over (weight n / (n + 30)). Fitted by review.py; promoted by a human.
    """
    edges: list = field(default_factory=lambda: [0.0, 0.4, 0.5, 0.6, 0.7, 0.8])
    win_rate: list = field(default_factory=lambda: [None] * 6)
    samples: list = field(default_factory=lambda: [0] * 6)
    version: int = 0

    def apply(self, p: float) -> float:
        i = max(j for j, e in enumerate(self.edges) if p >= e)
        prior = 0.5 + 0.5 * (p - 0.5)
        if self.win_rate[i] is None:
            return prior
        w = self.samples[i] / (self.samples[i] + 30)
        return w * self.win_rate[i] + (1 - w) * prior

    @classmethod
    def load(cls, path: str) -> "Calibration":
        if not os.path.exists(path):
            return cls()
        with open(path) as f:
            return cls(**json.load(f))

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp, path)
