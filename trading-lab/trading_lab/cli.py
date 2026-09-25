"""Command line: backtest, learn, review, paper trade, kill switch, reports."""
from __future__ import annotations

import argparse
import json
import os

from .backtest import run_backtest, stress_costs
from .broker import CostModel
from .data import INTERVAL_MS, fetch_recent, load_csv, load_or_fetch, synthetic
from .paper import load_state, report, run, save_state
from .risk import RiskLimits
from .strategies import STRATEGIES


def make_strategy(name: str, jev_mode: str = "mock", params: dict | None = None, on_decision=None,
                  calibration_path: str = "calibration.json", brain: bool = False):
    params = dict(params or {})
    if name == "jev":
        from .jev import Calibration, JevClient, MockJev
        params["engine"] = JevClient() if jev_mode == "live" else MockJev()
        params["calibration"] = Calibration.load(calibration_path)
        if brain:
            from .brain import Brain
            params["brain"] = Brain()
            if not params["brain"].available:
                print("note: brain unavailable (pip install anthropic, set ANTHROPIC_API_KEY); escalations will pause")
        if on_decision:
            params["on_decision"] = on_decision
    return STRATEGIES[name](**params)


def get_candles(a) -> list:
    if getattr(a, "synthetic", False):
        return synthetic(a.days * 86_400_000 // INTERVAL_MS[a.interval], a.interval)
    if getattr(a, "csv", None):
        return load_csv(a.csv)
    return load_or_fetch(a.symbol, a.interval, a.days)


def main() -> None:
    p = argparse.ArgumentParser(prog="trading_lab", description="Backtest and paper-trade. Never places real orders.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, days=180):
        sp.add_argument("--strategy", choices=sorted(STRATEGIES), default="trend")
        sp.add_argument("--symbol", default="BTCUSDT")
        sp.add_argument("--interval", choices=sorted(INTERVAL_MS), default="1h")
        sp.add_argument("--capital", type=float, default=30.0)
        sp.add_argument("--max-daily-loss", type=float, default=0.05)
        sp.add_argument("--max-drawdown", type=float, default=0.20)
        sp.add_argument("--fee", type=float, default=0.001)
        sp.add_argument("--slippage-bps", type=float, default=5.0)
        sp.add_argument("--jev", choices=["mock", "live"], default="mock",
                        help="jev strategy: offline mock, or the real API (needs TYPESAFE_API_KEY)")
        sp.add_argument("--days", type=int, default=days)
        sp.add_argument("--csv", help="use a saved CSV instead of downloading")
        sp.add_argument("--synthetic", action="store_true", help="offline fake data (tests the code, not the edge)")
        sp.add_argument("--registry", default="registry.json")
        sp.add_argument("--calibration", default="calibration.json")

    bt = sub.add_parser("backtest", help="test a strategy on historical data")
    common(bt)
    bt.add_argument("--compare", action="store_true", help="also run every other strategy for comparison")
    bt.add_argument("--champion", action="store_true", help="use the registry's promoted parameters")

    ln = sub.add_parser("learn", help="walk-forward search; promote new parameters only if they win out-of-sample")
    common(ln, days=365)
    ln.add_argument("--train-days", type=int, default=90)
    ln.add_argument("--test-days", type=int, default=30)

    cb = sub.add_parser("review", help="nightly review: Brier, gate audit, propose a calibration refit")
    common(cb, days=180)
    cb.add_argument("--journal", help="review the paper journal (default: replay Jev over history)")
    cb.add_argument("--horizon-bars", type=int, default=4)

    sub.add_parser("approve-calibration", help="promote the pending calibration from the last review")

    pp = sub.add_parser("paper", help="paper trade on live public data")
    common(pp)
    pp.add_argument("--once", action="store_true", help="process one bar and exit (for cron)")
    pp.add_argument("--poll", type=int, default=60)
    pp.add_argument("--state", default="paper_state.json")
    pp.add_argument("--journal", default="paper_journal.jsonl")
    pp.add_argument("--brain", action="store_true", help="escalate low-confidence/crisis bars to Claude Opus 5.5")

    lv = sub.add_parser("live", help="paper trader + live dashboard in one terminal")
    common(lv)
    lv.add_argument("--poll", type=float, default=60)
    lv.add_argument("--state", default="paper_state.json")
    lv.add_argument("--journal", default="paper_journal.jsonl")
    lv.add_argument("--brain", action="store_true")
    lv.add_argument("--replay", action="store_true",
                    help="SIMULATED: play synthetic candles as if live (offline demo; separate state files)")
    lv.add_argument("--seconds-per-bar", type=float, default=2.0, help="replay speed")

    wt = sub.add_parser("watch", help="dashboard for a running paper trader")
    wt.add_argument("--state", default="paper_state.json")
    wt.add_argument("--journal", default="paper_journal.jsonl")
    wt.add_argument("--once", action="store_true", help="print one frame and exit")
    wt.add_argument("--snapshot", help="write one frame as an HTML file")
    wt.add_argument("--width", type=int, default=0)

    jp = sub.add_parser("jev-ping", help="send one real state to Jev and print the raw response")
    jp.add_argument("--symbol", default="BTCUSDT")
    jp.add_argument("--interval", choices=sorted(INTERVAL_MS), default="1h")

    rp = sub.add_parser("report", help="summarize the paper journal")
    rp.add_argument("--journal", default="paper_journal.jsonl")
    rp.add_argument("--capital", type=float, default=30.0)

    sub.add_parser("kill", help="engage kill switch: flatten and stop trading")
    sub.add_parser("unkill", help="remove kill switch")
    rs = sub.add_parser("reset-halt", help="clear a max-drawdown halt after you have reviewed why it happened")
    rs.add_argument("--state", default="paper_state.json")

    a = p.parse_args()

    if a.cmd == "kill":
        open("KILL", "w").close()
        print("kill switch ON: paper trader will flatten and hold cash")
        return
    if a.cmd == "unkill":
        if os.path.exists("KILL"):
            os.remove("KILL")
        print("kill switch OFF")
        return
    if a.cmd == "reset-halt":
        state = load_state(a.state, 0)
        state["halted"] = False
        state["peak"] = state["cash"]  # new peak baseline, so the breaker doesn't re-trip at once
        save_state(a.state, state)
        print("halt cleared")
        return
    if a.cmd == "approve-calibration":
        from .review import approve
        print(approve())
        return
    if a.cmd == "watch":
        from . import dashboard
        if a.once or a.snapshot:
            state, rows = dashboard.load(a.state, a.journal)
            width = a.width or (os.get_terminal_size(0).columns if os.isatty(0) else 120)
            lines = dashboard.frame(state, rows, width)
            if a.snapshot:
                with open(a.snapshot, "w") as f:
                    f.write(dashboard.to_html(lines))
                print(f"wrote {a.snapshot}")
            else:
                print(dashboard.to_ansi(lines))
        else:
            dashboard.watch(a.state, a.journal)
        return
    if a.cmd == "report":
        print(report(a.journal, a.capital))
        return
    if a.cmd == "jev-ping":
        from .jev import STATE_BARS, JevClient, build_state, parse_answers
        state = build_state(fetch_recent(a.symbol, a.interval, STATE_BARS + 2), 4, 0.3)
        body = JevClient().raw(state)
        print(json.dumps(body, indent=2))
        print("\nparsed:", parse_answers(body["answers"]))
        return

    from .learn import Registry, walk_forward
    limits = RiskLimits(max_daily_loss=a.max_daily_loss, max_drawdown=a.max_drawdown)
    bt_limits = RiskLimits(max_daily_loss=a.max_daily_loss, max_drawdown=a.max_drawdown, kill_file="")
    costs = CostModel(fee_rate=a.fee, slippage_bps=a.slippage_bps)
    registry = Registry(a.registry)

    if a.cmd == "paper":
        champ = registry.champion(a.strategy)
        if not champ:
            print(f"note: no promoted parameters for {a.strategy}; using defaults (run `learn` first)")
        strategy = make_strategy(a.strategy, a.jev, champ["params"] if champ else None,
                                 calibration_path=a.calibration, brain=a.brain)
        print(f"paper trading {strategy.id} on {a.symbol} {a.interval} with ${a.capital} (simulated)")
        run(strategy, a.symbol, a.interval, a.capital, limits, costs, a.state,
            a.journal, a.once, a.poll)
        return

    if a.cmd == "live":
        import threading
        from . import dashboard
        from .data import ReplayFeed, fetch_recent, fetch_ticker
        champ = registry.champion(a.strategy)
        strategy = make_strategy(a.strategy, a.jev, champ["params"] if champ else None,
                                 calibration_path=a.calibration, brain=a.brain)
        kwargs = dict(fetch=fetch_recent, ticker=fetch_ticker, clock=None, mode="live data")
        state_path, journal_path, poll = a.state, a.journal, a.poll
        if a.replay:
            state_path, journal_path = "replay_state.json", "replay_journal.jsonl"
            for p in (state_path, journal_path):
                if os.path.exists(p):
                    os.remove(p)
            candles = synthetic(max(a.days, 30) * 86_400_000 // INTERVAL_MS[a.interval], a.interval)
            feed = ReplayFeed(candles, strategy.warmup + 5, a.seconds_per_bar, a.interval)
            kwargs = dict(fetch=feed.fetch, ticker=feed.ticker, clock=feed.now_ms, mode="SIMULATED REPLAY")
            poll = min(a.seconds_per_bar / 2, 1.0)
        stop = threading.Event()
        t = threading.Thread(target=run, daemon=True, args=(strategy, a.symbol, a.interval, a.capital, limits, costs,
                                                            state_path, journal_path),
                             kwargs=dict(poll_seconds=poll, quiet=True, stop=stop, **kwargs))
        t.start()
        try:
            dashboard.watch(state_path, journal_path)
        finally:
            stop.set()
        print(report(journal_path, a.capital))
        return

    if a.cmd == "review":
        from .review import Record, nightly, records_from_journal
        horizon_ms = a.horizon_bars * INTERVAL_MS[a.interval]
        if a.journal:
            recs, closes = records_from_journal(a.journal)
        else:
            candles = get_candles(a)
            recs = []
            s = make_strategy("jev", a.jev, {"horizon_bars": a.horizon_bars}, calibration_path=a.calibration,
                              on_decision=lambda bar, d, p, gates: recs.append(Record(bar.open_time, bar.close, d.p_long, p, gates)))
            run_backtest(candles, s, a.capital, bt_limits, costs, a.interval)
            closes = {c.open_time: c.close for c in candles}
            print(f"replay on {len(candles)} candles{' (SYNTHETIC)' if a.synthetic else ''}, jev={a.jev}\n")
        print(nightly(recs, closes, horizon_ms, a.calibration))
        return

    candles = get_candles(a)
    print(f"{len(candles)} candles{' (SYNTHETIC)' if a.synthetic else ''}\n")

    if a.cmd == "learn":
        if a.strategy == "jev" and a.jev == "live":
            raise SystemExit("learn with --jev live would make thousands of paid API calls; "
                             "tune on mock, then judge live Jev with `calibrate --journal`")
        if a.strategy == "buy_hold":
            raise SystemExit("buy_hold has no parameters to learn")
        per_day = 86_400_000 // INTERVAL_MS[a.interval]
        wf = walk_forward(a.strategy, candles, a.train_days * per_day, a.test_days * per_day,
                          a.capital, bt_limits, costs, a.interval)
        print(wf.summary())
        ok, why = registry.consider(wf, limits)
        print(f"  -> {why}")
        return

    names = sorted(STRATEGIES) if a.compare else [a.strategy]
    for name in names:
        champ = registry.champion(name) if a.champion else None
        s = make_strategy(name, a.jev, champ["params"] if champ else None)
        base = run_backtest(candles, s, a.capital, bt_limits, costs, a.interval)
        stressed = run_backtest(candles, make_strategy(name, a.jev, champ["params"] if champ else None),
                                a.capital, bt_limits, stress_costs(costs), a.interval)
        print(base.summary())
        print(f"stressed costs     {stressed.total_return:+.2%} (2x fees, 3x slippage)\n")
