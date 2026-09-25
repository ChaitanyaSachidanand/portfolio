# trading-lab

A small, honest crypto research lab: download market data, backtest strategies with
real costs, then paper trade them 24/7 behind hard risk limits.

**It never places real orders and has no exchange API keys.** That is deliberate.

## What to expect

- Turning $30 into $1,000 in a day (33x) requires ~50–100x leverage on one bet. At 50x a 2% move
  against you liquidates the account. This lab does not do that and never will: exposure is capped
  at 1.0 (no leverage), long-only spot.
- Most simple strategies do **not** beat buy-and-hold after fees. The backtester exists to prove
  that before you lose money finding out.
- Any backtest result is an estimate on past data, not a promise.

## Layout

```
trading_lab/
  data.py        Binance public klines (GET /api/v3/klines, no key), CSV cache, synthetic data
  strategies.py  Strategy -> target exposure in [0, 1]. trend, meanrev, buy_hold baseline
  risk.py        Deterministic risk engine: kill switch, max drawdown (latching), daily loss, no leverage
  broker.py      Paper broker: fees, slippage, min order size, rebalance band
  backtest.py    Decide on bar close, fill at next bar open (no lookahead); metrics; cost stress test
  paper.py       24/7 loop: strategy -> risk -> simulated fill, JSON state, JSONL journal
  jev.py         Jev (TypeSafe AI) client + offline MockJev: typed regime/direction judgments
  learn.py       Walk-forward search, champion/challenger registry, Brier calibration
  cli.py         Command line
tests/           python -m unittest discover -s tests
```

Pipeline for every decision: **data → strategy (proposes) → risk engine (disposes) → broker**.
Strategies can't size orders or bypass risk; the risk engine is plain code with no overrides.

## Usage

Python 3.10+, no dependencies.

```bash
cd trading-lab

# 1. Backtest on real history (downloads from Binance's public API)
python -m trading_lab backtest --symbol BTCUSDT --interval 1h --days 365 --compare

# Offline check of the code paths (fake data, says nothing about real edge)
python -m trading_lab backtest --synthetic --compare

# 2. Paper trade with $30 of pretend money, forever (Ctrl-C to stop)
python -m trading_lab paper --strategy meanrev --capital 30
#    ...or once per hour from cron:  5 * * * *  cd /path/trading-lab && python -m trading_lab paper --once
python -m trading_lab report

# Kill switch: flatten and hold cash on the next bar
python -m trading_lab kill
python -m trading_lab unkill
# After a max-drawdown halt, read the journal, understand why, then:
python -m trading_lab reset-halt
```

## Jev as the decision engine

`--strategy jev` asks Jev two typed questions each bar: **regime** (trending / mean_reverting / high_vol /
crisis) and **direction** (up / down_or_flat, with probabilities and confidence). Deterministic gates then
decide: no position in `crisis`, none if confidence < `min_confidence` (default 0.60) or p_up < `min_p_up`.
Jev never sizes or places anything, and the risk engine still has the final say.

```bash
export TYPESAFE_API_KEY=...        # from typesafe.ai
python -m trading_lab jev-ping                         # 1 real call: check the response format parses
python -m trading_lab paper --strategy jev --jev live  # hourly Jev decisions, paper fills
python -m trading_lab calibrate --journal paper_journal.jsonl   # were Jev's probabilities any good?
```

Without `--jev live` a `MockJev` heuristic stands in so everything runs offline. The raw-HTTP request
format in `jev.py` follows the public jev-trader project and TypeSafe's documented endpoint, but could not
be verified end-to-end when this was written: run `jev-ping` first.

## Self-learning (controlled)

```bash
python -m trading_lab learn --strategy trend --days 365      # also: meanrev, jev (mock only)
python -m trading_lab backtest --strategy trend --champion   # backtest the promoted parameters
```

- `learn` does a walk-forward search: choose parameters on 90 days, score them on the *next* 30 days
  they were never tuned on, roll forward. Scoring always uses stressed costs.
- A challenger is promoted to `registry.json` only if its out-of-sample return is positive, its drawdown is
  inside the limit, it has some edge over buy-and-hold, and it beats the current champion's Sharpe.
  Every attempt, including rejections, goes to `learn_log.jsonl`.
- `paper` automatically uses the promoted parameters.
- `calibrate` scores Jev's p_up against what happened (Brier score vs. guessing the base rate). If Jev
  isn't beating the base rate on your journal, its "confidence" means nothing and you should not trade it.
- Learning never changes risk limits.

## Daily loop (cron)

```cron
5 * * * *  cd ~/portfolio/trading-lab && python -m trading_lab paper --strategy jev --jev live --once
0 3 * * 0  cd ~/portfolio/trading-lab && python -m trading_lab learn --strategy trend && python -m trading_lab learn --strategy meanrev
30 3 * * * cd ~/portfolio/trading-lab && python -m trading_lab calibrate --journal paper_journal.jsonl && python -m trading_lab report
```

## About Elefin

Not supported, on purpose. As of Sept 2026 Elefin is an offshore CFD broker registered in Saint Lucia, offering
leverage up to 1:2000, and at least one broker-review site lists it as a suspicious broker, with concerns about
withdrawals. CFDs mean you never own the coin, and at 1:2000 a 0.05% move wipes out the account. Its Standard
account also needs a $50 minimum. If a strategy here ever graduates, use a large, regulated spot exchange
available in your country.

Every backtest also runs a **stress test** (2x fees, 3x slippage). If the edge disappears there, there
is no edge.

Risk defaults (flags): `--max-drawdown 0.20`, `--max-daily-loss 0.05`, `--fee 0.001`, `--slippage-bps 5`.
In backtests the drawdown halt latches for the rest of the test, just as it does live until you reset it.

## Graduation rules before any real money

1. Beats buy-and-hold on risk-adjusted terms over 1+ year of real data **and** under stressed costs.
2. Works on both BTCUSDT and ETHUSDT, and in the second half of the data, not just the first.
3. 4+ weeks of paper trading with results in line with the backtest.
4. Even then: start with money you are fully prepared to lose.

## What could be wrong

- **Overfitting:** tweaking parameters until a backtest looks good fits noise. Pick parameters, then test on data you didn't look at.
- **Fill realism:** paper fills at the bar close plus a fixed slippage. Real fills can be worse, especially in fast markets.
- **Data feed:** if Binance is unreachable or lagging, the paper trader logs `error` / `stale_data` and does not trade.
  Binance is geo-restricted in some countries; check that you're allowed to use it.
- **Regime change:** a strategy that worked in a trending year can bleed in a choppy one.
- **Correlation:** BTC and ETH move together; running the same strategy on both is not diversification.
