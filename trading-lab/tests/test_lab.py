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

from trading_lab.brain import Brain, Verdict
from trading_lab.jev import Calibration, JevDecision, MalformedAnswer, MockJev, build_state, parse_answers
from trading_lab.review import Record, fit, label, nightly
from trading_lab.strategies import Donchian
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


def answers(regime="trending", direction="long", p_long=0.9, conf=0.9, toxic=0.1, setup=2.5, risk="safe"):
    rest = (1 - p_long) / 2
    return {
        "regime": {"type": "choice", "choice": regime, "confidence": conf,
                   "probabilities": {"trending": 0.25, "mean_reverting": 0.25, "high_vol": 0.25, "crisis": 0.25}},
        "direction": {"type": "choice", "choice": direction, "confidence": conf,
                      "probabilities": {"long": p_long, "short": rest, "neutral": rest}},
        "toxic_flow": {"type": "noul", "noul": toxic},
        "setup_quality": {"type": "score", "score": setup, "confidence": 0.7},
        "risk_state": {"type": "choice", "choice": risk, "confidence": 0.8,
                       "probabilities": {"safe": 0.8, "near_limit": 0.1, "reduce": 0.1}},
    }


class Fixed:
    def __init__(self, **kw):
        self.d = parse_answers(answers(**kw))

    def decide(self, state):
        return self.d


class FakeBrain:
    def __init__(self, action, hours=4):
        self.verdict, self.calls = Verdict(action, hours, "test", "opus"), 0

    def review(self, packet):
        self.calls += 1
        return self.verdict


class JevTests(unittest.TestCase):
    def test_parse_strict_and_fail_closed(self):
        d = parse_answers(answers())
        self.assertEqual((d.regime, d.direction, d.p_long, d.setup_quality), ("trending", "long", 0.9, 2.5))
        bad = answers()
        bad["direction"]["probabilities"] = {"up": 1.0}
        with self.assertRaises(MalformedAnswer):
            parse_answers(bad)
        bad = answers()
        del bad["toxic_flow"]
        with self.assertRaises(MalformedAnswer):
            parse_answers(bad)

    def test_gates(self):
        hist = trending(200)
        cal = Calibration()
        cal.win_rate, cal.samples = [None] * 5 + [0.9], [0] * 5 + [10_000]  # trust p=0.9 as-is
        ok = JevStrategy(Fixed(), calibration=cal)
        self.assertGreater(ok.target(hist, 0), 0.0)
        for kw in [dict(regime="crisis"), dict(conf=0.5), dict(setup=1.0), dict(risk="near_limit"),
                   dict(toxic=0.7), dict(direction="neutral")]:
            s = JevStrategy(Fixed(**kw), calibration=cal)
            self.assertEqual(s.target(hist, 0), 0.0, kw)
            self.assertTrue(s.last_gates, kw)
        # Uncalibrated, p=0.9 shrinks to 0.7, still above 0.55; p=0.6 shrinks to 0.55 boundary.
        self.assertEqual(JevStrategy(Fixed(p_long=0.58)).target(hist, 0), 0.0)

    def test_exits_need_no_confidence(self):
        hist = trending(200)
        self.assertEqual(JevStrategy(Fixed(regime="crisis", conf=0.1)).target(hist, 0.5), 0.0)
        self.assertEqual(JevStrategy(Fixed(risk="reduce")).target(hist, 0.5), 0.25)

        class Abstains:
            def decide(self, state):
                return JevDecision.abstain("down")

        self.assertEqual(JevStrategy(Abstains()).target(hist, 0.5), 0.0)

    def test_brain_can_only_reduce(self):
        hist = trending(200)

        class LowConfJev(Fixed):
            def __init__(self):
                super().__init__(conf=0.5)
                self.d = JevDecision(**{**self.d.__dict__, "source": "jev"})

        s = JevStrategy(LowConfJev(), brain=FakeBrain("flatten"))
        self.assertEqual(s.target(hist, 0.5), 0.0)
        s = JevStrategy(LowConfJev(), brain=FakeBrain("pause", 4), min_confidence=0.4)
        self.assertEqual(s.target(hist, 0.0), 0.0)
        self.assertIn("paused by brain", s.last_gates)
        self.assertEqual(s.target(hist, 0.3), 0.3)  # pause keeps, never adds

    def test_brain_fails_closed_without_sdk_or_key(self):
        v = Brain().review({"x": 1})
        self.assertIn(v.action, ("pause", "no_change", "flatten"))
        if not Brain().available:
            self.assertEqual((v.action, v.source), ("pause", "fallback"))

    def test_state_is_compact_and_mock_runs(self):
        state = build_state(synthetic(200), 4, 0.3)
        self.assertLess(len(str(state)), 700)
        d = MockJev().decide(state)
        self.assertTrue(0 <= d.p_long <= 1)
        self.assertEqual(d.source, "mock")


class DonchianTests(unittest.TestCase):
    def test_breakout_entry_and_sizing(self):
        hist = trending(120)
        top = max(c.high for c in hist[-21:])
        last = hist[-1]
        hist.append(Candle(last.open_time + 3_600_000, last.close, top * 1.01, last.close, top * 1.01, 1))
        t = Donchian().target(hist, 0.0)
        self.assertGreater(t, 0.0)
        self.assertLessEqual(t, 0.4)

    def test_exit_on_breakdown(self):
        hist = trending(120)
        last = hist[-1]
        crash = Candle(last.open_time + 3_600_000, last.close, last.close, last.close * 0.8, last.close * 0.8, 1)
        self.assertEqual(Donchian().target(hist + [crash], 0.3), 0.0)
        self.assertEqual(Donchian().target(hist, 0.3), 0.3)  # holds while trend intact


class ReviewTests(unittest.TestCase):
    def test_fit_is_monotone_and_shrinks(self):
        recs, closes = [], {}
        rng = random.Random(3)
        for i in range(400):
            p = rng.random()
            t = i * 2 * 3_600_000
            recs.append(Record(t, 100.0, p, p, ["setup x"]))
            closes[t + 3_600_000] = 101.0 if rng.random() < 0.5 else 99.0
        labeled = label(recs, closes, 3_600_000)
        self.assertEqual(len(labeled), 400)
        cal = fit(labeled, Calibration())
        rates = [r for r in cal.win_rate if r is not None]
        self.assertEqual(rates, sorted(rates))
        self.assertEqual(cal.version, 1)
        self.assertAlmostEqual(Calibration().apply(0.9), 0.7)

    def test_nightly_writes_pending_not_live(self):
        with tempfile.TemporaryDirectory() as d:
            live, pending = os.path.join(d, "c.json"), os.path.join(d, "p.json")
            recs = [Record(i * 7_200_000, 100.0, 0.7, 0.6, []) for i in range(50)]
            closes = {i * 7_200_000 + 3_600_000: 101.0 for i in range(50)}
            out = nightly(recs, closes, 3_600_000, live, pending)
            self.assertTrue(os.path.exists(pending))
            self.assertFalse(os.path.exists(live))
            self.assertIn("proposed calibration v1", out)


if __name__ == "__main__":
    unittest.main()
