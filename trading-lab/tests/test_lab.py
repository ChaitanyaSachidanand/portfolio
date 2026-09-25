import os
import tempfile
import unittest

from trading_lab.backtest import run_backtest, stress_costs
from trading_lab.broker import CostModel, PaperBroker
from trading_lab.data import Candle, synthetic
from trading_lab.paper import load_state, step
from trading_lab.risk import RiskEngine, RiskLimits
from trading_lab.strategies import STRATEGIES, Strategy


class RiskTests(unittest.TestCase):
    def test_kill_switch_flattens(self):
        with tempfile.TemporaryDirectory() as d:
            kill = os.path.join(d, "KILL")
            engine = RiskEngine(RiskLimits(kill_file=kill))
            self.assertEqual(engine.check(1.0, 100, 100, 100).approved, 1.0)
            open(kill, "w").close()
            self.assertEqual(engine.check(1.0, 100, 100, 100).approved, 0.0)

    def test_drawdown_latches_until_reset(self):
        engine = RiskEngine(RiskLimits(max_drawdown=0.2, kill_file=""))
        self.assertEqual(engine.check(1.0, 79, 100, 79).reasons, ["max_drawdown_breached"])
        # Equity recovers, but the halt stays until a human resets it.
        self.assertEqual(engine.check(1.0, 99, 100, 99).approved, 0.0)
        engine.reset()
        self.assertEqual(engine.check(1.0, 99, 100, 99).approved, 1.0)

    def test_daily_loss_and_no_leverage(self):
        engine = RiskEngine(RiskLimits(max_daily_loss=0.05, kill_file=""))
        self.assertEqual(engine.check(1.0, 94, 100, 100).approved, 0.0)
        self.assertEqual(engine.check(5.0, 100, 100, 100).approved, 1.0)
        self.assertEqual(engine.check(-1.0, 100, 100, 100).approved, 0.0)


class BrokerTests(unittest.TestCase):
    def test_round_trip_costs_money(self):
        b = PaperBroker(30.0, CostModel(fee_rate=0.001, slippage_bps=5))
        self.assertIsNotNone(b.rebalance(1.0, 100.0))
        self.assertIsNotNone(b.rebalance(0.0, 100.0))
        self.assertEqual(b.qty, 0.0)
        self.assertLess(b.cash, 30.0)
        self.assertAlmostEqual(30.0 - b.cash, 30 * (0.002 + 0.001), delta=0.01)

    def test_never_spends_more_than_cash(self):
        b = PaperBroker(30.0, CostModel())
        b.rebalance(1.0, 100.0)
        self.assertGreaterEqual(b.cash, 0.0)

    def test_min_notional_blocks_dust(self):
        b = PaperBroker(4.0, CostModel(min_notional=5.0))
        self.assertIsNone(b.rebalance(1.0, 100.0))


class NoLookahead(Strategy):
    id = "probe"
    warmup = 3

    def __init__(self):
        self.seen = []

    def target(self, history, current_exposure):
        self.seen.append(history[-1].open_time)
        return 1.0


class BacktestTests(unittest.TestCase):
    def test_strategy_never_sees_the_bar_it_fills_on(self):
        candles = synthetic(50)
        probe = NoLookahead()
        run_backtest(candles, probe)
        self.assertEqual(probe.seen[-1], candles[-2].open_time)

    def test_all_strategies_run_and_respect_limits(self):
        candles = synthetic(24 * 120)
        for cls in STRATEGIES.values():
            r = run_backtest(candles, cls())
            self.assertGreater(r.end_equity, 0)
            self.assertLessEqual(r.time_in_market, 1.0)

    def test_stressed_costs_never_beat_base(self):
        candles = synthetic(24 * 120)
        s = STRATEGIES["trend"]()
        base = run_backtest(candles, s)
        stressed = run_backtest(candles, s, costs=stress_costs(CostModel()))
        self.assertLessEqual(stressed.end_equity, base.end_equity + 1e-9)

    def test_drawdown_breaker_caps_losses(self):
        # Steady crash: 1% down every hour.
        candles = [Candle(i * 3_600_000, 100 * 0.99**i, 100 * 0.99**i, 100 * 0.99**(i + 1),
                          100 * 0.99**(i + 1), 1) for i in range(200)]
        r = run_backtest(candles, STRATEGIES["buy_hold"](), limits=RiskLimits(max_drawdown=0.2, kill_file=""))
        self.assertLess(r.max_drawdown, 0.25)
        self.assertEqual(r.risk_events.get("max_drawdown_breached"), 1)


class PaperTests(unittest.TestCase):
    def test_stale_data_does_not_trade(self):
        with tempfile.TemporaryDirectory() as d:
            journal = os.path.join(d, "j.jsonl")
            old = synthetic(200)  # timestamps from 2023: stale
            state = load_state(os.path.join(d, "s.json"), 30.0)
            new = step(STRATEGIES["buy_hold"](), "X", "1h", dict(state), RiskLimits(kill_file=""),
                       CostModel(), journal, fetch=lambda *a: old)
            self.assertEqual(new["qty"], 0.0)
            with open(journal) as f:
                self.assertIn("stale_data", f.read())


if __name__ == "__main__":
    unittest.main()
