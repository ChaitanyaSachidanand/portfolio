"""Terminal dashboard: watch the paper trader live.

    python -m trading_lab watch                 # attach to a running `paper` process
    python -m trading_lab live --strategy jev   # paper trader + dashboard in one terminal

It only reads paper_state.json and the journal, so it can be opened, closed and reopened at any
time without touching the trader. Standard library only (curses).
"""
from __future__ import annotations

import html
import json
import os
import time
from datetime import datetime, timezone

from .data import INTERVAL_MS

Line = list  # list of (text, style) segments

SPARK = "▁▂▃▄▅▆▇█"
ANSI = {"normal": "0", "dim": "2", "title": "1;97", "good": "32", "bad": "31", "warn": "33",
        "accent": "36", "badge_good": "30;42", "badge_bad": "97;41", "badge_warn": "30;43"}
CSS = {"normal": "#d0d0d0", "dim": "#707070", "title": "#ffffff;font-weight:bold", "good": "#3fd67a",
       "bad": "#ff5f5f", "warn": "#ffd75f", "accent": "#5fd7ff", "badge_good": "#000;background:#3fd67a",
       "badge_bad": "#fff;background:#ff5f5f", "badge_warn": "#000;background:#ffd75f"}


def tail_lines(path: str, n: int) -> list[str]:
    """Last n lines of a file without reading all of it."""
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = pos = f.tell()
        data = b""
        while pos > 0 and data.count(b"\n") <= n:
            step = min(65536, pos)
            pos -= step
            f.seek(pos)
            data = f.read(end - pos)
    return [ln for ln in data.decode(errors="replace").splitlines()[-n:] if ln.strip()]


def load(state_path: str, journal_path: str, n: int = 600) -> tuple[dict, list[dict]]:
    state = {}
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                state = json.load(f)
        except (OSError, ValueError):
            pass  # mid-write; next refresh will get it
    rows = []
    for ln in tail_lines(journal_path, n):
        try:
            rows.append(json.loads(ln))
        except ValueError:
            pass
    return state, rows


def spark(values: list[float], width: int) -> str:
    vals = values[-width:]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return SPARK[3] * len(vals)
    return "".join(SPARK[int((v - lo) / (hi - lo) * (len(SPARK) - 1))] for v in vals)


def downsample(values: list[float], width: int) -> list[float]:
    """Keep the whole history in `width` points (last value of each bucket)."""
    if len(values) <= width:
        return values
    step = len(values) / width
    return [values[min(len(values) - 1, int((i + 1) * step) - 1)] for i in range(width)]


def bar(frac: float, width: int) -> str:
    frac = max(0.0, min(1.0, frac))
    n = round(frac * width)
    return "█" * n + "░" * (width - n)


def money(x: float) -> str:
    return f"${x:,.2f}" if abs(x) < 1e6 else f"${x:,.0f}"


def price(x: float) -> str:
    return f"{x:,.2f}" if x >= 1 else f"{x:.6f}"


def hhmm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")


def seg_len(line: Line) -> int:
    return sum(len(t) for t, _ in line)


def box(title: str, body: list[Line], width: int) -> list[Line]:
    inner = width - 4
    out = [[("┌─ ", "dim"), (title, "title"), (" " + "─" * max(0, width - len(title) - 5) + "┐", "dim")]]
    for line in body:
        clipped, used = [], 0
        for text, style in line:
            if used >= inner:
                break
            text = text[: inner - used]
            clipped.append((text, style))
            used += len(text)
        out.append([("│ ", "dim"), *clipped, (" " * (inner - used) + " │", "dim")])
    out.append([("└" + "─" * (width - 2) + "┘", "dim")])
    return out


def status(state: dict, now_ms: int) -> tuple[str, str]:
    if not state:
        return "WAITING FOR TRADER", "badge_warn"
    if state.get("kill"):
        return "KILL SWITCH ON", "badge_bad"
    if state.get("halted"):
        return "HALTED: MAX DRAWDOWN", "badge_bad"
    if state.get("last_error"):
        return "DATA ERROR", "badge_warn"
    if now_ms - state.get("last_ok", 0) > 5 * 60_000:
        return "STALE (trader not polling)", "badge_warn"
    if state.get("paused_until", 0) > state.get("last_bar", 0):
        return "PAUSED BY BRAIN", "badge_warn"
    return "RUNNING", "badge_good"


def frame(state: dict, rows: list[dict], width: int, now_ms: int | None = None) -> list[Line]:
    now_ms = now_ms or int(time.time() * 1000)
    width = max(width, 60)
    decisions = [r for r in rows if "decision_id" in r]
    last = decisions[-1] if decisions else None
    interval = state.get("interval", "1h")
    capital = state.get("capital", 30.0)
    limits = state.get("limits", {"max_daily_loss": 0.05, "max_drawdown": 0.20})

    mark_fresh = state.get("mark") and now_ms - state.get("mark_ts", 0) < 5 * 60_000
    px = state["mark"] if mark_fresh else (last["close"] if last else 0.0)
    qty, cash = state.get("qty", 0.0), state.get("cash", capital)
    equity = cash + qty * px
    pnl = equity - capital
    peak = max(state.get("peak", capital), equity)
    dd = 1 - equity / peak if peak else 0.0
    day_start = state.get("day_start", capital) or capital
    day = equity / day_start - 1
    exposure = qty * px / equity if equity > 0 else 0.0

    label, badge = status(state, now_ms)
    mode = state.get("mode", "live data")
    header = [(" trading-lab ", "title"), ("│ PAPER ", "accent"),
              (f"│ {state.get('symbol', '-')} {interval} ", "normal"),
              (f"│ {state.get('strategy_id', '-')} ", "normal"),
              (f"│ {mode} ", "warn" if "SIMULATED" in mode.upper() else "dim"),
              (f"│ {datetime.now(timezone.utc):%H:%M:%S} UTC ", "dim"), (f" {label} ", badge)]

    two_col = width >= 100
    colw = (width - 1) // 2 if two_col else width

    # ---- market
    closes = [r["close"] for r in decisions]
    chg_base = closes[-25] if len(closes) >= 25 else (closes[0] if closes else px)
    chg = px / chg_base - 1 if chg_base else 0.0
    market = [
        [("price   ", "dim"), (price(px), "title"), ("  live" if mark_fresh else "  last close", "dim"),
         (f"   {chg:+.2%}", "good" if chg >= 0 else "bad"), (" vs 24 bars ago", "dim")],
        [(spark(closes + ([px] if mark_fresh else []), colw - 4), "accent")],
        [("range   ", "dim"), (f"{price(min(closes[-(colw - 4):]))} – {price(max(closes[-(colw - 4):]))}" if closes else "-", "normal"),
         (f"   bars seen {len(decisions)}", "dim")],
    ]
    if last:
        market.append([("last bar ", "dim"), (hhmm(last["bar_open_time"]), "normal"),
                       (f"   next in ~{max(0, (last['bar_open_time'] + 2 * INTERVAL_MS[interval] - now_ms) // 60000)} min"
                        if "SIMULATED" not in mode.upper() else "", "dim")])

    # ---- account
    eq_series = [r["equity"] for r in decisions]
    bw = max(10, colw - 36)
    account = [
        [("equity  ", "dim"), (money(equity), "title"), (f"   {pnl:+,.2f} ({pnl / capital:+.2%})", "good" if pnl >= 0 else "bad"),
         (f"  from {money(capital)}", "dim")],
        [(spark(downsample(eq_series + [equity], colw - 4), colw - 4), "good" if pnl >= 0 else "bad")],
        [("cash    ", "dim"), (money(cash), "normal"), ("   position ", "dim"),
         (f"{qty:.6f} = {money(qty * px)}", "normal")],
        [("exposure ", "dim"), (bar(exposure, bw), "accent"), (f" {exposure:5.1%}", "normal")],
        [("drawdown ", "dim"), (bar(dd / limits["max_drawdown"], bw), "bad" if dd > limits["max_drawdown"] * 0.5 else "warn"),
         (f" {dd:5.1%} / {limits['max_drawdown']:.0%} limit", "normal")],
        [("today    ", "dim"), (bar(max(0.0, -day) / limits["max_daily_loss"], bw), "bad" if -day > limits["max_daily_loss"] * 0.5 else "warn"),
         (f" {day:+5.1%} / -{limits['max_daily_loss']:.0%} limit", "normal")],
        [("fees     ", "dim"), (money(state.get("fees_paid", 0.0)), "normal")],
    ]

    # ---- decision
    decision: list[Line] = []
    if not last:
        decision.append([("waiting for the first closed bar...", "dim")])
    else:
        decision.append([("bar ", "dim"), (hhmm(last["bar_open_time"]), "normal"), ("   target ", "dim"),
                         (f"{last['raw_target']:.2f}", "normal"), (" → risk-approved ", "dim"),
                         (f"{last['approved_target']:.2f}", "title")])
        if last.get("risk_reasons"):
            decision.append([("risk     ", "dim"), (", ".join(last["risk_reasons"]), "bad")])
        j = last.get("jev")
        if j:
            src = {"jev": ("Jev", "accent"), "mock": ("MockJev (offline stand-in)", "warn"),
                   "abstain": ("ABSTAINED: " + j.get("note", "")[:40], "bad")}[j["source"]]
            decision.append([("engine   ", "dim"), src])
            decision.append([("regime   ", "dim"), (j["regime"], "bad" if j["regime"] == "crisis" else "normal"),
                             (f"  conf {j['regime_conf']:.2f}", "dim")])
            pw = max(8, colw - 32)
            for k in ("long", "short", "neutral"):
                p = j["direction_probs"].get(k, 0.0)
                decision.append([(f"  {k:<8}", "dim"), (bar(p, pw), {"long": "good", "short": "bad"}.get(k, "dim")),
                                 (f" {p:5.1%}", "normal"), (" ◀" if j["direction"] == k else "", "title")])
            cal = last.get("p_long_cal")
            decision.append([("p_long   ", "dim"), (f"{j['p_long']:.3f}", "normal"),
                             (f" → calibrated {cal:.3f}" if cal is not None else "", "accent"),
                             (f"   dir conf {j['direction_conf']:.2f}", "dim")])
            decision.append([("setup    ", "dim"), (bar(j["setup_quality"] / 3, 12), "accent"),
                             (f" {j['setup_quality']:.1f}/3", "normal"), ("   toxic ", "dim"),
                             (bar(j["toxic_flow"], 8), "bad" if j["toxic_flow"] >= 0.5 else "dim"),
                             (f" {j['toxic_flow']:.2f}", "normal")])
            decision.append([("risk     ", "dim"), (j["risk_state"], {"safe": "good", "near_limit": "warn"}.get(j["risk_state"], "bad"))])
        gates = last.get("gates") or []
        if gates:
            decision.append([("blocked  ", "dim"), (gates[0], "warn")])
            for g in gates[1:4]:
                decision.append([("         ", "dim"), (g, "warn")])
        elif j and last["raw_target"] > 0:
            decision.append([("gates    ", "dim"), ("all entry gates passed", "good")])
        if last.get("brain"):
            b = last["brain"]
            decision.append([("brain    ", "dim"), (f"{b['action']} {b['pause_hours']}h", "warn"),
                             (f"  {b['reasoning'][:colw - 30]}", "dim")])

    # ---- trades
    fills = [r for r in decisions if r.get("fill")][-8:][::-1]
    trades: list[Line] = [[(f"{'time':<12}{'side':<5}{'qty':>10}{'price':>11}{'value':>9}{'fee':>7}", "dim")]]
    for r in fills:
        f = r["fill"]
        trades.append([(f"{hhmm(r['bar_open_time']):<12}", "normal"), (f"{f['side']:<5}", "good" if f["side"] == "buy" else "bad"),
                       (f"{f['qty']:>10.6f}{price(f['fill_price']):>11}{money(f['notional']):>9}{money(f['fee']):>7}", "normal")])
    if not fills:
        trades.append([("no trades yet: the gates and risk limits decide when to trade", "dim")])

    # ---- events
    events: list[Line] = []
    for r in rows[-200:]:
        t = hhmm(r.get("bar_open_time") or r.get("ts", now_ms))
        if r.get("event") == "error":
            events.append([(f"{t} ", "dim"), ("error ", "bad"), (r.get("error", "")[:colw - 20], "normal")])
        elif r.get("event") == "stale_data":
            events.append([(f"{t} ", "dim"), ("stale data: skipped bar", "warn")])
        elif r.get("risk_reasons"):
            events.append([(f"{t} ", "dim"), ("risk ", "bad"), (", ".join(r["risk_reasons"]), "normal")])
        elif r.get("brain"):
            events.append([(f"{t} ", "dim"), ("brain ", "warn"), (r["brain"]["action"], "normal")])
        elif r.get("fill"):
            f = r["fill"]
            events.append([(f"{t} ", "dim"), (f["side"], "good" if f["side"] == "buy" else "bad"),
                           (f" {money(f['notional'])} @ {price(f['fill_price'])}", "normal")])
    events = events[-6:] or [[("nothing yet", "dim")]]

    left = box("MARKET", market, colw) + box("ACCOUNT", account, colw)
    right = box("DECISION", decision, colw) + box("TRADES", trades, colw)
    out: list[Line] = [header, []]
    if two_col:
        for i in range(max(len(left), len(right))):
            lft = left[i] if i < len(left) else [(" " * colw, "normal")]
            rgt = right[i] if i < len(right) else []
            out.append(lft + [(" " * (colw - seg_len(lft) + 1), "normal")] + rgt)
    else:
        out += left + right
    out += box("EVENTS", events, width)
    out.append([(" PAPER TRADING · simulated fills · no real money · ", "dim"), ("q", "title"), (" quit", "dim")])
    return out


def to_ansi(lines: list[Line]) -> str:
    return "\n".join("".join(f"\x1b[{ANSI[s]}m{t}\x1b[0m" for t, s in line) for line in lines)


def to_html(lines: list[Line]) -> str:
    body = "\n".join("".join(f'<span style="color:{CSS[s]}">{html.escape(t)}</span>' for t, s in line) for line in lines)
    return ("<!doctype html><meta charset=utf-8><title>trading-lab</title>"
            "<body style='margin:0;background:#101214'><pre style=\"margin:0;padding:14px;font:13px/1.35 "
            "'DejaVu Sans Mono',Menlo,monospace;color:#d0d0d0\">" + body + "</pre></body>")


def watch(state_path: str, journal_path: str, refresh: float = 1.0) -> None:
    import curses

    def main(scr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        palette = {"good": curses.COLOR_GREEN, "bad": curses.COLOR_RED, "warn": curses.COLOR_YELLOW,
                   "accent": curses.COLOR_CYAN}
        attrs = {"normal": curses.A_NORMAL, "dim": curses.A_DIM, "title": curses.A_BOLD}
        for i, (name, color) in enumerate(palette.items(), start=1):
            curses.init_pair(i, color, -1)
            attrs[name] = curses.color_pair(i)
        for i, (name, color) in enumerate(palette.items(), start=10):
            curses.init_pair(i, curses.COLOR_BLACK if name != "bad" else curses.COLOR_WHITE, color)
            attrs["badge_" + name] = curses.color_pair(i) | curses.A_BOLD
        scr.timeout(int(refresh * 1000))
        while True:
            h, w = scr.getmaxyx()
            state, rows = load(state_path, journal_path)
            scr.erase()
            for y, line in enumerate(frame(state, rows, w - 1)[: h - 1]):
                x = 0
                for text, style in line:
                    if x >= w - 1:
                        break
                    try:
                        scr.addnstr(y, x, text, w - 1 - x, attrs.get(style, curses.A_NORMAL))
                    except curses.error:
                        pass
                    x += len(text)
            scr.refresh()
            if scr.getch() in (ord("q"), ord("Q"), 27):
                return

    curses.wrapper(main)
