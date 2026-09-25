"""Command line: backtest, paper trade, kill switch, reports."""
from __future__ import annotations

import argparse
import os

from .backtest import run_backtest, stress_costs
from .broker import CostModel
from .data import INTERVAL_MS, load_csv, load_or_fetch, synthetic
from .paper import load_state, report, run, save_state
from .risk import RiskLimits
from .strategies import STRATEGIES


def main() -> None:
    p = argparse.ArgumentParser(prog="trading_lab", description="Backtest and paper-trade. Never places real orders.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--strategy", choices=sorted(STRATEGIES), default="trend")
        sp.add_argument("--symbol", default="BTCUSDT")
        sp.add_argument("--interval", choices=sorted(INTERVAL_MS), default="1h")
        sp.add_argument("--capital", type=float, default=30.0)
        sp.add_argument("--max-daily-loss", type=float, default=0.05)
        sp.add_argument("--max-drawdown", type=float, default=0.20)
        sp.add_argument("--fee", type=float, default=0.001)
        sp.add_argument("--slippage-bps", type=float, default=5.0)

    bt = sub.add_parser("backtest", help="test a strategy on historical data")
    common(bt)
    bt.add_argument("--days", type=int, default=180)
    bt.add_argument("--csv", help="use a saved CSV instead of downloading")
    bt.add_argument("--synthetic", action="store_true", help="offline fake data (tests the code, not the edge)")
    bt.add_argument("--compare", action="store_true", help="also run every other strategy for comparison")

    pp = sub.add_parser("paper", help="paper trade on live public data")
    common(pp)
    pp.add_argument("--once", action="store_true", help="process one bar and exit (for cron)")
    pp.add_argument("--poll", type=int, default=60)
    pp.add_argument("--state", default="paper_state.json")
    pp.add_argument("--journal", default="paper_journal.jsonl")

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
    elif a.cmd == "unkill":
        if os.path.exists("KILL"):
            os.remove("KILL")
        print("kill switch OFF")
    elif a.cmd == "reset-halt":
        state = load_state(a.state, 0)
        state["halted"] = False
        state["peak"] = state["cash"]  # new peak baseline, so the breaker doesn't re-trip at once
        save_state(a.state, state)
        print("halt cleared")
    elif a.cmd == "report":
        print(report(a.journal, a.capital))
    else:
        limits = RiskLimits(max_daily_loss=a.max_daily_loss, max_drawdown=a.max_drawdown)
        costs = CostModel(fee_rate=a.fee, slippage_bps=a.slippage_bps)
        strategy = STRATEGIES[a.strategy]()
        if a.cmd == "paper":
            print(f"paper trading {strategy.id} on {a.symbol} {a.interval} with ${a.capital} (simulated)")
            run(strategy, a.symbol, a.interval, a.capital, limits, costs, a.state, a.journal, a.once, a.poll)
            return

        if a.synthetic:
            candles = synthetic(a.days * 86_400_000 // INTERVAL_MS[a.interval], a.interval)
        elif a.csv:
            candles = load_csv(a.csv)
        else:
            candles = load_or_fetch(a.symbol, a.interval, a.days)
        print(f"{len(candles)} candles{' (SYNTHETIC)' if a.synthetic else ''}\n")
        names = sorted(STRATEGIES) if a.compare else [a.strategy]
        bt_limits = RiskLimits(max_daily_loss=a.max_daily_loss, max_drawdown=a.max_drawdown, kill_file="")
        for name in names:
            s = STRATEGIES[name]()
            base = run_backtest(candles, s, a.capital, bt_limits, costs, a.interval)
            stressed = run_backtest(candles, s, a.capital, bt_limits, stress_costs(costs), a.interval)
            print(base.summary())
            print(f"stressed costs     {stressed.total_return:+.2%} (2x fees, 3x slippage)\n")
