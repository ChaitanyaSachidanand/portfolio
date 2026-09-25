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
