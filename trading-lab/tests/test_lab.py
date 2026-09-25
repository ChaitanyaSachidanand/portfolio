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


import math
import random

from trading_lab.jev import MockJev, build_state, parse_answers
from trading_lab.learn import Registry, WalkForward, brier, calibration_report, walk_forward
from trading_lab.strategies import JevStrategy


def trending(bars, seed=1):
    rng, price, out = random.Random(seed), 100.0, []
    for i in range(bars):
        c = price * math.exp(rng.gauss(0.002, 0.004))
        out.append(Candle(i * 3_600_000, price, max(price, c), min(price, c), c, 1))
        price = c
    return out


class LearnTests(unittest.TestCase):
    def test_walk_forward_promotes_a_real_edge(self):
        with tempfile.TemporaryDirectory() as d:
            reg = Registry(os.path.join(d, "r.json"), os.path.join(d, "l.jsonl"))
            wf = walk_forward("trend", trending(24 * 120), 24 * 40, 24 * 20)
            self.assertGreater(wf.oos_return, 0)
            ok, why = reg.consider(wf, RiskLimits())
            self.assertTrue(ok, why)
            self.assertEqual(reg.champion("trend")["version"], 1)
            # Same result again does not beat the champion by the required margin.
            ok, _ = reg.consider(wf, RiskLimits())
            self.assertFalse(ok)
            self.assertEqual(Registry(os.path.join(d, "r.json")).champion("trend")["version"], 1)

    def test_losing_challenger_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            reg = Registry(os.path.join(d, "r.json"), os.path.join(d, "l.jsonl"))
            wf = WalkForward("trend", 3, -0.1, -0.2, 0.15, -1.0, [], {"fast": 10, "slow": 100})
            ok, why = reg.consider(wf, RiskLimits())
            self.assertFalse(ok)
            self.assertIsNone(reg.champion("trend"))
            with open(os.path.join(d, "l.jsonl")) as f:
                self.assertIn("rejected", f.read())

    def test_brier_and_calibration(self):
        perfect = [(1.0, 1), (0.0, 0)] * 20
        self.assertEqual(brier(perfect), 0.0)
        self.assertIn("better than guessing", calibration_report(perfect))
        self.assertIn("NOT better", calibration_report([(0.9, 0), (0.1, 1)] * 20))


class JevTests(unittest.TestCase):
    def test_parse_answers_with_and_without_confidence(self):
        body = {"answers": {"regime": {"choice": "trending", "probabilities": {"trending": 0.7, "crisis": 0.3}},
                            "direction": {"choice": "up", "probabilities": {"up": 0.8, "down_or_flat": 0.2},
                                          "confidence": 0.65}}}
        d = parse_answers(body, 12.0)
        self.assertEqual((d.regime, d.direction, d.p_up, d.confidence), ("trending", "up", 0.8, 0.65))
        del body["answers"]["direction"]["confidence"]
        self.assertTrue(0 < parse_answers(body, 0).confidence < 1)

    def test_crisis_or_low_confidence_blocks_trade(self):
        class Fixed:
            def __init__(self, regime, p, conf):
                self.d = parse_answers({"regime": {"choice": regime},
                                        "direction": {"choice": "up", "probabilities": {"up": p, "down_or_flat": 1 - p},
                                                      "confidence": conf}}, 0)

            def decide(self, state):
                return self.d

        hist = trending(200)
        self.assertEqual(JevStrategy(Fixed("crisis", 0.9, 0.9)).target(hist, 0), 0.0)
        self.assertEqual(JevStrategy(Fixed("trending", 0.9, 0.3)).target(hist, 0), 0.0)
        self.assertGreater(JevStrategy(Fixed("trending", 0.9, 0.9)).target(hist, 0), 0.0)

    def test_state_is_compact_and_mock_runs(self):
        state = build_state(synthetic(200), 4, 0.3)
        self.assertLess(len(str(state)), 800)
        d = MockJev().decide(state)
        self.assertTrue(0 <= d.p_up <= 1)
