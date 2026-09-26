# Research: how the Polymarket 5-minute BTC "Up or Down" bots actually make money

Researched 2026-09-26. x.com, Medium, dev.to and docs.polymarket.com are blocked from the environment this
was written in, so several sources were read through search-result summaries only. Those are marked
*(summary)*. Facts are separated from interpretation, and every number has a source.

## 1. The market

- Polymarket opened **5-minute BTC Up/Down** markets on **2026-02-13**. Every 5 minutes a market asks whether
  BTC will close the window at or above its opening price. Resolution uses Chainlink's BTC/USD feed.
  ([Polymarket 5M](https://polymarket.com/crypto/5M), [BenjaminCup, Medium](https://medium.com/@benjamin.bigdev/unlocking-edges-in-polymarkets-5-minute-crypto-markets-last-second-dynamics-bot-strategies-and-db8efcb5c196) *(summary)*)
- Each market has two tokens, **Up** and **Down**. One pays **$1.00**, the other **$0.00**. One Up plus one Down
  (a "complete set") is always worth exactly $1 at settlement.
- 15-minute and 1-hour versions exist for BTC, ETH and SOL.

## 2. The rule changes that decide everything (early 2026)

| Date | Change | Source |
|---|---|---|
| 2026-01-05 | **Taker fees** switch on for 15-minute crypto markets (previously zero), explicitly "to neutralise latency-based arbitrage" | [Finance Magnates](https://www.financemagnates.com/cryptocurrency/polymarket-introduces-dynamic-fees-to-curb-latency-arbitrage-in-short-term-crypto-markets/), [KuCoin](https://www.kucoin.com/news/flash/polymarket-introduces-taker-fees-for-15-minute-crypto-prediction-markets) |
| 2026-02-18 | The **500 ms taker delay is removed**. "The entire class of pure latency arbitrage strategies stopped working the same day." | [dev.to/lkto1m](https://dev.to/lkto1m/february-2026-changed-polymarket-forever-heres-what-happened-to-my-bots-numbers-2fi5) *(summary)* |
| by 2026-03-06 | Taker fees extended to **all crypto timeframes**, including 5-minute | [KuCoin fee guide](https://www.kucoin.com/blog/polymarket-fees-trading-guide-2026) *(summary)* |

**The fee formula:** `fee = C × θ × p × (1 − p)`, where C is shares, p is price and θ depends on the category. It
peaks at p = $0.50, exactly where these markets trade, and falls to zero near $0 or $1. **Makers (resting
limit orders) pay nothing** and share a rebate pool funded by taker fees; sources say 20% of crypto fees go
back to makers, paid daily. ([startpolymarket](https://startpolymarket.com/learn/polymarket-fees/), [Prediction Hunt](https://www.predictionhunt.com/blog/polymarket-fees-complete-guide) *(summaries)*)

Sources disagree on the peak crypto fee rate: ~1.56% (dev.to, Feb), 1.80% (fee guides, March), and an
earlier "~3.15% on a 50-cent contract" (Finance Magnates, Jan). The calculations below use 1.56–1.80%.
**Check the live value in Polymarket's docs before relying on it.**

## 3. The strategies, and which still work

| Strategy | How it makes money | Status in 2026 |
|---|---|---|
| **Latency arbitrage** | BTC moves on Binance; Polymarket's book lags 2–10 s; buy the side that is about to win. The famous "$313 → $414,000 in a month" bot did this with $4–5k bets and a 98% win rate. ([Yahoo Finance](https://finance.yahoo.com/news/arbitrage-bots-dominate-polymarket-millions-100000888.html), [Predik](https://predik.io/en/blog/bot-trading-polymarket-exploit-latencia-arbitraje-en) *(summaries)*) | **Killed by design.** Taker fees peak at 50¢, where it traded, and the 500 ms delay it relied on is gone. |
| **Taker "complete set" arbitrage** | Buy Up + Down at market when asks sum below $1. | **Mostly gone.** Windows now last **~2.7 s on average (12.3 s in 2024)**, and **73% of arbitrage profit goes to sub-100 ms bots.** ([ILLUMINATION, Medium](https://medium.com/illumination/beyond-simple-arbitrage-4-polymarket-strategies-bots-actually-profit-from-in-2026-ddacc92c5b4f) *(summary)*) Fees also wipe out small gaps (section 4). |
| **Maker complete-set accumulation** ("gabagool" style, and the +$220,792 bot VALIX posted) | Rest limit bids on **both** sides at low prices, get filled when panicky takers dump one side, keep averaging until the combined average cost is well under $1 (that bot reported **$0.9387**). Pay no fee and earn rebates. ([VALIX on X](https://x.com/RetroValix/status/2088744044226855035), [KuCoin insight](https://www.kucoin.com/news/insight/BTC/695f8764e781bf0007e01c10) *(summaries)*) | **This is what works now.** "The core advantage has moved from taker arbitrage to providing liquidity as a maker." ([BlockBeats](https://en.theblockbeats.news/news/61326) *(summary)*) |
| **Near-resolution sniping** | Seconds before close, when the outcome is nearly certain from the Chainlink feed, buy the winning side at 98–99¢. | Works in small size. Low fee near $1, but one wrong call wipes out ~50 wins. |
| **Directional prediction** | Predict BTC's next 5 minutes from Binance data and bet. | This is what Jev-style bots attempt. It pays peak fees at 50/50 and needs a real forecasting edge. No public evidence of one. |

## 4. The fee math, worked out

A taker buying one Up and one Down pays θ × [p_up(1−p_up) + p_down(1−p_down)] per pair. At the peak rate,
θ = 0.0312 (1.56%) to 0.036 (1.80%):

| Pair cost (Up + Down asks) | Fees per pair | Net per pair as a **taker** |
|---|---|---|
| $0.99 | $0.0156–0.0180 | **−$0.006 to −$0.008 (a loss)** |
| $0.98 | ~$0.016–0.018 | about break-even |
| $0.94 | $0.0155–0.0179 | +$0.042 to +$0.045, but asks this low almost never sit on the book long enough for a taker to buy them |

So **a taker needs the pair below about $0.98 just to break even.** The public bot
[solyasa/Polymarket-trading-bot-15min-BTC](https://github.com/solyasa/Polymarket-trading-bot-15min-BTC)
(last commit 2026-01-19, before 5-minute fees) buys at `TARGET_PAIR_COST=0.99` by default, which **loses about
0.6–0.8¢ per pair** under current fees. Code written for 2025 rules is not safe to run in 2026.

**Interpretation:** the $0.9387 average from the profitable bot is only realistic as a **maker**, getting filled
on resting limit orders during sharp moves, not by buying displayed asks.

## 5. Why the maker strategy is still hard

From the sources above and [market-making write-ups](https://www.polysyncer.com/blog/polymarket-market-making-bot) *(summary)*:

- **Leg risk.** A complete set is risk-free only once both sides fill. When one side fills and price runs away, you
  hold a plain directional bet. That's why the $220k bot uses "inventory rebalancing, dynamic hedging".
- **Adverse selection.** Your bids fill precisely when informed or fast traders want to sell to you. Makers need
  cancel/replace loops under ~100 ms to avoid being picked off.
- **Capital.** "The capital required to keep meaningful quotes on the book runs into five and six figures
  before any edge appears." The $220k bot averaged **$49.8 per trade and 284 trades per active hour**.
- **Competition.** Every successful wallet is public on-chain. Once a pattern is posted on X, others copy it and
  the fills get worse.
- **Win rate is not the point.** That bot's win rate was ~50%. Its edge was buying the set cheap, not predicting.

## 6. How big the overall prize has been

The peer-reviewed paper *Unravelling the Probabilistic Forest* (IMDEA Networks, AFT 2025) measured **~$40M of
arbitrage profit on Polymarket from April 2024 to April 2025**, across 86M bets and 7,000+ markets with
mispricings, captured mostly by a small number of sophisticated accounts.
([arXiv 2508.03474](https://arxiv.org/abs/2508.03474))

## 7. Access, withdrawal and security

- **Geography:** Polymarket is blocked in ~33 countries, with a close-only mode in others. Examples include France,
  Germany, Italy, Belgium, Poland, Singapore, Thailand, Australia, most of Canada, and the Netherlands (since
  February 2026). Brazil and Slovakia are close-only; Indonesia has been blocked since May 2026.
  ([Polymarket Help Center](https://help.polymarket.com/en/articles/13364163-geographic-restrictions), [datawallet](https://www.datawallet.com/crypto/polymarket-restricted-countries) *(summaries)*)
  Using a VPN to get around this breaks the terms of service and can get funds frozen.
- **Withdrawals:** a guide cites a 2% withdrawal fee
  ([BenjaminCup](https://benjamincup.substack.com/p/the-ultimate-guide-to-building-a) *(summary)*). On $30, that's $0.60 every time.
- **Malware:** in April 2026 StepSecurity found malicious "Polymarket trading bot" repos in a **hijacked verified
  GitHub organization** (20+ look-alike repos with inflated stars). They read the private key from `.env` and
  send it to the attacker through typosquatted npm packages, and also open an SSH backdoor.
  ([StepSecurity](https://www.stepsecurity.io/blog/malicious-polymarket-bot-hides-in-hijacked-dev-protocol-github-org-and-steals-wallet-keys), [Cryptopolitan](https://www.cryptopolitan.com/polymarket-copy-traders-warned/))
  Every trading bot needs `POLYMARKET_PRIVATE_KEY`. **Use a fresh wallet holding only what you can lose, and read
  every dependency first.** If you've already run one, move funds to a new wallet immediately.

## 8. What this means for a $30 account

| Requirement for the strategy that works (maker complete sets) | $30 account |
|---|---|
| Resting bids on both sides, many markets, hundreds of fills an hour at ~$50 each | Can't fund even one side of a typical fill |
| Sub-100 ms cancel/replace near Polymarket's servers | Home internet and a laptop |
| Hedging leg risk | No spare capital to hedge |
| Access | Depends on your country (section 7) |

**Interpretation:** the posts show real wallets making real money, but through a capital- and speed-intensive
market-making operation that is now crowded. It isn't a script that turns $30 into $1,000. The gap between the
two is where copy-trading repos and paid "bot" products make their money.

## 9. What would be worth building here

A **read-only scanner** (public market data, no wallet, no key) that logs, for every 5-minute BTC market:
best Up and Down asks and bids, pair cost, fees at current prices, how long any sub-$0.98 gap lasts, the size
available at that price, and how the final seconds trade. A day of this data answers from primary evidence
whether any of these strategies is reachable from your connection, before any money moves. Polymarket is
blocked from this build environment, so it would have to run on your own machine.
