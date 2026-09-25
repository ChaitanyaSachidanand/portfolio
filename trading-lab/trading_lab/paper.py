"""24/7 paper trader: polls closed candles, runs strategy -> risk -> simulated fill, journals everything.

State lives in a JSON file so the process can be restarted (or run from cron with --once)
without losing positions. No exchange keys are used; no real orders exist.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from .backtest import utc_day
from .broker import CostModel, PaperBroker
from .data import INTERVAL_MS, fetch_recent, fetch_ticker
from .risk import RiskEngine, RiskLimits
from .strategies import Strategy


def load_state(path: str, capital: float) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"cash": capital, "qty": 0.0, "peak": capital, "day": None, "day_start": capital,
            "halted": False, "last_bar": 0, "fees_paid": 0.0}


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written state file


def journal(path: str, record: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def step(strategy: Strategy, symbol: str, interval: str, state: dict, limits: RiskLimits,
         costs: CostModel, journal_path: str, fetch=fetch_recent, now_ms: int | None = None) -> dict:
    """Process at most one new closed bar. Returns the updated state."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    candles = fetch(symbol, interval, strategy.warmup + 5)
    if len(candles) <= strategy.warmup:
        raise RuntimeError(f"only {len(candles)} candles returned; need {strategy.warmup + 1}")
    bar = candles[-1]
    if bar.open_time <= state["last_bar"]:
        return state  # nothing new yet
    step_ms = INTERVAL_MS[interval]
    if now_ms - (bar.open_time + step_ms) > 2 * step_ms:
        journal(journal_path, {"ts": now_ms, "event": "stale_data", "bar_open_time": bar.open_time})
        return state  # feed is lagging; never trade on stale prices

    broker = PaperBroker(state["cash"], costs, state["qty"])
    broker.fees_paid = state["fees_paid"]
    equity = broker.equity(bar.close)
    state["peak"] = max(state["peak"], equity)
    if utc_day(bar.open_time) != state["day"]:
        state["day"], state["day_start"] = utc_day(bar.open_time), equity

    if hasattr(strategy, "account"):
        strategy.account = {"dd_pct": round((1 - equity / state["peak"]) * 100, 2),
                            "day_pct": round((equity / state["day_start"] - 1) * 100, 2),
                            "exposure": round(broker.exposure(bar.close), 3)}
    if hasattr(strategy, "paused_until"):
        strategy.paused_until = state.get("paused_until", 0)
    risk = RiskEngine(limits, halted=state["halted"])
    raw = strategy.target(candles, broker.exposure(bar.close))
    decision = risk.check(raw, equity, state["peak"], state["day_start"])
    # Paper approximation: fill at the just-closed price plus slippage.
    fill = broker.rebalance(decision.approved, bar.close)

    jev = getattr(strategy, "last_decision", None)
    verdict = getattr(strategy, "last_verdict", None)
    journal(journal_path, {
        "ts": now_ms,
        "decision_id": str(uuid.uuid4()),
        "strategy_id": strategy.id,
        "symbol": symbol,
        "bar_open_time": bar.open_time,
        "close": bar.close,
        "raw_target": raw,
        "approved_target": decision.approved,
        "risk_reasons": decision.reasons,
        "fill": asdict(fill) if fill else None,
        "jev": jev.to_dict() if jev else None,
        "p_long_cal": getattr(strategy, "last_p_cal", None),
        "gates": getattr(strategy, "last_gates", []),
        "brain": asdict(verdict) if verdict else None,
        "equity": broker.equity(bar.close),
    })
    state.update(cash=broker.cash, qty=broker.qty, halted=risk.halted,
                 last_bar=bar.open_time, fees_paid=broker.fees_paid,
                 paused_until=getattr(strategy, "paused_until", 0))
    return state


def run(strategy: Strategy, symbol: str, interval: str, capital: float, limits: RiskLimits,
        costs: CostModel, state_path: str, journal_path: str, once: bool = False,
        poll_seconds: float = 60, fetch=fetch_recent, ticker=fetch_ticker, clock=None,
        quiet: bool = False, stop=None, mode: str = "live data") -> None:
    """Paper loop. `fetch`, `ticker` and `clock` are swappable so the same loop drives replays."""
    state = load_state(state_path, capital)
    state.update(strategy_id=strategy.id, symbol=symbol, interval=interval, capital=capital, mode=mode,
                 started=state.get("started") or int(time.time() * 1000),
                 limits={"max_daily_loss": limits.max_daily_loss, "max_drawdown": limits.max_drawdown})
    while stop is None or not stop.is_set():
        now_ms = clock() if clock else int(time.time() * 1000)
        try:
            state = step(strategy, symbol, interval, state, limits, costs, journal_path, fetch, now_ms)
            state["last_ok"] = int(time.time() * 1000)
            state.pop("last_error", None)
        except Exception as exc:  # network/exchange failure: log, keep the old state, try again
            state["last_error"] = repr(exc)[:200]
            journal(journal_path, {"ts": now_ms, "event": "error", "error": repr(exc)})
            if not quiet:
                print(f"{datetime.now(timezone.utc):%H:%M:%S} error: {exc!r}")
        try:
            state["mark"], state["mark_ts"] = ticker(symbol), int(time.time() * 1000)
        except Exception:
            pass  # the dashboard shows the mark's age; a stale mark is visible, not hidden
        state["kill"] = os.path.exists(limits.kill_file) if limits.kill_file else False
        save_state(state_path, state)
        if once:
            return
        if stop is not None:
            stop.wait(poll_seconds)
        else:
            time.sleep(poll_seconds)


def report(journal_path: str, capital: float) -> str:
    if not os.path.exists(journal_path):
        return "no journal yet"
    with open(journal_path) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    decisions = [r for r in rows if "decision_id" in r]
    fills = [r for r in decisions if r["fill"]]
    errors = [r for r in rows if r.get("event") in ("error", "stale_data")]
    blocked = [r for r in decisions if r["risk_reasons"] and r["approved_target"] == 0 and r["raw_target"] > 0]
    equity = decisions[-1]["equity"] if decisions else capital
    return (
        f"decisions {len(decisions)}   fills {len(fills)}   blocked by risk {len(blocked)}   "
        f"errors/stale {len(errors)}\n"
        f"equity ${capital:,.2f} -> ${equity:,.2f} ({equity / capital - 1:+.2%})"
    )
