# Research: the "kid who turned $30 into $1,000" and the setup behind it

Researched 2026-09-25. x.com, dev.to, cybernews.com, typesafe.ai and most review sites are blocked from the
environment this was written in, so posts on X were only seen through search-result snippets. Facts are
marked with a source; interpretation is marked as such.

## 1. The post and the prompt

| Item | What the evidence shows |
|---|---|
| The link you sent | `x.com/lihazadn_ai/status/2103117108506980749`. The account is **Adnan Sharif Lihaz (@lihazadn_Ai)**, which posts lists of "must-use AI tools". Search found nothing identifying him as Japanese, a kid, or a trader, and nothing verifying a $30 → $1,000 result. The post itself could not be opened. |
| The prompt in the screenshot | Originates from **Roan (@RohOnChain)**, posted ~2026-09-23: *"send this 24/7 AI trading agent prompt to claude opus 5.5. it will probably make you a lot of money."* ([x.com/RohOnChain/status/2102790572281139213](https://x.com/RohOnChain/status/2102790572281139213)) |
| Roan's related article | "How to Use Jev to Build a 24/7 HFT Trading System", which recommends wiring agents with AgenKit. ([x.com/RohOnChain/article/2101311813908652069](https://x.com/RohOnChain/article/2101311813908652069)) |

**Interpretation:** this follows the usual viral pattern: an impressive claim, a copy-paste prompt, and a
product link (AgenKit). No wallet, trade history or broker statement was found for the $30 → $1,000 claim.

## 2. The tools named in the prompt

| Tool | Facts |
|---|---|
| **Jev** (TypeSafe AI) | Released 2026-09-15. A "System One" model: you send state plus typed questions (`choice`, `score`, `noul` = yes/no probability) and get back calibrated answers with probabilities and confidence in 70–500 ms. `POST https://api.typesafe.ai/v1/systemone`, key in `TYPESAFE_API_KEY`, $0.042 per 1M input tokens, output free. TypeSafe raised $40M led by DCVC; founder Diogo Almeida. ([MarkTechPost, 2026-09-19](https://www.marktechpost.com/2026/09/19/typesafe-ai-releases-jev/), [KuCoin](https://www.kucoin.com/news/flash/typesafe-ai-model-jev-launches-on-b-ai-api-with-0-042-1m-token-input-cost)) |
| **AgenKit** | "An AI engineering team for Claude Code, Cursor & Codex": specialist agents and a `/agenkit` command that drives a project through six phases (spec → architecture → plan → build → review → ship). It is a code generator, not a trading edge. ([agenkit.xyz](https://agenkit.xyz/)) |
| **Claude Opus 5.5** | Anthropic model `claude-opus-5-5`, used in these setups as the slow "brain" for research, nightly review and escalations. |

## 3. Public builds of this exact setup, and their results

| Project | What it is | Result it reports |
|---|---|---|
| [isaiahar027/trading](https://github.com/isaiahar027/trading) (2026-09-23) | The prompt built out almost literally with AgenKit's six phases: Hyperliquid data → state engine → Jev (5 questions: regime, direction, toxic_flow, setup_quality, risk_state) → policy gates → risk layer → paper/live execution; Opus 5.5 escalation desk; nightly Brier review and calibration refit. 72 tests. | **Backtest −2.10%** over ~208 days, max drawdown 5.45%, 34 trades (offline heuristic, not Jev). *"It has not been run with a real Jev key or real money."* The nightly review found its heuristic's direction Brier score of 0.91 was worse than the naive 0.667. |
| [jarrodwatts/jev-trader](https://github.com/jarrodwatts/jev-trader) (2026-09-16) | Jev market-making MON-USDC on Kuru (Monad), one decision per ~300 ms block. | Deployed as a dry run with the mock model. Sample totals in the README: P&L −$0.003. No live P&L reported. |
| [Jev finance projects survey](https://gist.github.com/drillan/6916b16e8ea31a8ec36c8f59d6483150) (2026-09-20) | 15+ Jev trading and forecasting projects. | "No evidence of profitability"; all proofs of concept, paper trading or research. |
| [Wataru1987/gmo-coin-trend-lab](https://github.com/Wataru1987/gmo-coin-trend-lab) (2026-09-19, Japan) | Daily-bar Donchian breakout on GMO Coin spot, long-only, 1% risk per trade. The author runs it live with about ¥24,000 (~$160). | 2018-09 to 2026-09: CAGR 16.9%, Sharpe 1.10, max drawdown −17.7%, versus BTC (20% vol target) 17.4% / 0.78 / −35.7%. **Walk-forward out-of-sample: Sharpe 0.85, +9%/yr**, "that lower figure is the one I plan around." |

**Interpretation:** the closest match to "a Japanese person running a small AI-assisted crypto system" that has
public, checkable numbers is Wataru Suda's. His numbers are modest: about buy-and-hold returns with half the
drawdown. He also wrote [a fact-check of 5 viral "my AI bot made money" posts](https://dev.to/wataru_suda_d295dab9cca4f/i-fact-checked-5-viral-my-ai-bot-made-money-posts-against-primary-data-none-survived-5c3d)
(2026-09-17) that included "$52 → $3,292" and "$89 → $7,769" claims. None survived a check against primary data.

## 4. What $30 → $1,000 in a day implies

- 33.3x in 24 hours is ~+15.8% **every hour** compounded, or one bet at ~50–100x leverage.
- At 50x, a 2% move against the position liquidates it. BTC's typical daily range is larger than 2%.
- The prompt's own `<rules>` say "Never promise profitability" and require a hard daily-loss limit, which
  rules out the trades needed for 33x.

## 5. What this lab takes from the research

| Idea | Source | Where it lives here |
|---|---|---|
| Five typed Jev questions in one call, strict parsing, fail closed to "no trade" on any error | isaiahar027/trading | `jev.py` |
| Entry gates: setup ≥ 2, direction confidence ≥ 0.80, risk_state safe, not crisis, toxic flow < 0.5 | isaiahar027/trading | `strategies.JevStrategy` |
| Calibration map: Jev probability → observed win rate, shrunk toward 0.5 until there are enough samples; nightly refit written as *pending* and promoted by a human | isaiahar027/trading | `review.py` |
| Opus 5.5 escalation that can only return `no_change` / `pause` / `flatten`, failing closed to `pause` | isaiahar027/trading | `brain.py` |
| Mock model so everything runs without keys; dry-run by default | jev-trader | `jev.MockJev` |
| Daily Donchian breakout, long-only, ATR risk sizing, walk-forward as the headline number | gmo-coin-trend-lab | `strategies.Donchian` |

Code was written for this repo, not copied. isaiahar027/trading has no licence file; the other two are MIT.

## 6. What could be wrong

- **Jev's edge is unproven.** Nobody has published live Jev P&L. Calibration on *your* journal decides whether it gets money.
- **The Donchian result is one author's backtest** on JPY pairs; USDT pairs and 2026 conditions may differ. Re-run it here on real data.
- **Search-snippet evidence:** the X posts could not be read directly.
- **Regime change:** trend following earns little in ranging years (Wataru's 2019, 2025 and 2026 were roughly flat).
