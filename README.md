# kal-grok — a hypothetical Kalshi paper-trading bot

> **DISCLAIMER — PAPER TRADING ONLY.** This bot is a simulation. It reads Kalshi's *public*
> market-data API, never uses Kalshi credentials, and **never places real orders**. The $100,000
> bankroll is imaginary, fills are simulated, and results are hypothetical. Simulated performance
> does not predict real results. Nothing here is financial advice.

A multi-strategy prediction-market bot that starts with a simulated **$100,000**, takes a market
snapshot every ~6–7 hours (GitHub Actions), trades on paper with conservative fills and Kalshi's real
fee formula, settles positions when markets resolve, re-evaluates and reallocates capital among its
strategies every week, and publishes everything to an installable iPhone PWA dashboard.

## Repo layout

```
kalbot/
  api.py            read-only client for https://api.elections.kalshi.com/trade-api/v2 (rate-limited, retries)
  fees.py           Kalshi fee math (taker 0.07, maker 0.0175 on maker-fee series, series multiplier)
  sizing.py         fractional Kelly for binary contracts + cap helpers
  models.py         favorite-longshot calibration model, MAP logistic fit, Brier, temperature bucket probs
  execution.py      orderbook walking fill simulator (buy at ask / sell at bid, depth-capped)
  portfolio.py      cash, positions, realized/unrealized P&L, settlement, interest on idle cash
  engine.py         risk manager + execution context shared by strategies
  strategies/       pluggable strategies (one capital sleeve + trade log each)
  history.py        rolling price history, calibration samples harvested from settlements
  evaluate.py       weekly scoring, bandit reallocation, parameter tuning, weekly report
  publish.py        writes docs/data/*.json for the dashboard
  orders.py         passive limit orders (maker) + conservative fill model
  exits.py          take-profit / stop-loss exit rules
  variants.py       24 shadow $100k books (expiry bucket x exit rule) + analysis + weekly adoption
  run.py            one full cycle (python -m kalbot.run)
state/              bot state (portfolio.json, history.json.gz, calibration.json.gz, series_fees.json)
docs/               PWA dashboard (GitHub Pages) + docs/data/*.json + docs/data/reports/
tests/              pytest suite (fees, Kelly, arbitrage, settlement, models, live dry run)
scripts/make_icons.py   regenerates the PNG app icons with Pillow
scripts/stamp_build.sh  stamps the PWA build id into docs/ at deploy time (auto-update)
.github/workflows/  trade.yml (every ~6-7h), weekly.yml (Sundays), pages.yml (deploy on your pushes)
```

## How a cycle works (`python -m kalbot.run`)

1. **Snapshot** — pages through `/events?status=open&with_nested_markets=true` (≈21k events / ≈205k
   markets) and keeps a *tradable universe* (two-sided quotes, 24h volume ≥ 20 or open interest ≥ 200,
   closes within 730 days — the main book only trades ≤ 120 days; full bucket sets kept for exhaustive events). Series fee settings
   (`fee_type`, `fee_multiplier`) come from `/series`, cached daily.
2. **Settle** — any held market that left the open universe is looked up via `/markets?tickers=…`;
   `finalized`/`settled` markets pay $1/$0 by `result` (scalar results use `settlement_value_dollars`).
3. **Mark** — positions are marked at the mid (fair value); the bid-side *liquidation value* is also
   tracked and shown.
4. **Trade** — strategies run their `manage()` (exits) then `run()` (entries) through the risk engine.
   Orderbooks (`/markets/{ticker}/orderbook`) are fetched for candidates (≤250 per run).
5. **Persist + publish** — compact state is saved and dashboard JSON is written to `docs/data/`.

Prices on the API are now `*_dollars` strings (e.g. `yes_ask_dollars: "0.4200"`) and sizes are
`*_fp`; the orderbook only lists **bids** — a YES ask at *p* is a NO bid at 1−*p*.

## Strategies

| Strategy | Idea | Math |
|---|---|---|
| `event_arb` | Mutually exclusive outcome sets. **NO basket**: buying NO on any *k* outcomes pays ≥ *k*−1, so whenever the YES **bids** sum to more than $1 (+fees) the basket is a locked profit. **YES basket**: only on provably exhaustive sets (contiguous integer buckets, e.g. temperature), when YES asks sum < $1 − fees. | Σ yes_bid − 1 − fees > min_profit |
| `ladder_arb` | Cross-market consistency: in "above X" ladders P(>a) ≥ P(>b) for a<b. YES(>a)+NO(>b) pays ≥ $1 in every state, so if yes_bid(>b) > yes_ask(>a) + fees it's free money (mirror logic for "below X"). Grouped by ticker prefix + strike type + custom strike so different players/assets never mix. | 1 − ask_yes(a) − ask_no(b) − fees |
| `longshot_fade` | Favorite-longshot bias: longshots are overpriced, favorites underpriced. Buys NO on cheap YES (≤ 20¢) and YES on mirror favorites, 2–60 days out. | logit p = α + β·logit(mid), β>1 |
| `expiry_convergence` | Within 48h of expiry, near-certain favorites (≥ 85¢) still trade at a discount; buy the favorite and let time decay pull it to $1. | separate short-horizon α, β |
| `momentum` / `mean_reversion` | Big snapshot-to-snapshot moves (z ≥ 1.5 vs the market's own volatility, ≥ 3¢): fair = mid + κ·Δmid. κ>0 follows, κ<0 fades. Positions exit after `hold` runs. | κ set weekly from pooled lag-1 autocorrelation of price changes |
| `market_making` | Wide-spread markets (≥ 6¢): post bids 1¢ inside on both YES and NO; a YES+NO pair nets to $1 (Kalshi nets opposite positions), locking ≈ spread − 2¢. Inventory-skewed quoting. **Conservative fills**: only if the next snapshot shows the market *traded through* our price, and at most `fill_frac` × new volume. Maker fees applied on maker-fee series. | |
| `weather_nws` | Kalshi daily-high temperature markets for 24 cities vs the free NWS forecast (`api.weather.gov`) at each settlement station. Forecast error ~ Normal(0, σ(lead)), with integer continuity correction; the model is **ensembled with the market mid** (weight learned weekly from Brier(model) vs Brier(market)). Same-day markets are skipped. | P(bucket) = Φ((cap+½−μ)/σ) − Φ((floor−½−μ)/σ) |

### Fees (verified against Kalshi's fee schedule, Nov 2026)

`fee = round_up(M × 0.07 × C × P × (1−P))` for takers; makers pay `M × 0.0175 × C × P × (1−P)`
only on series with `fee_type = quadratic_with_maker_fees` (≈160 series), else 0. `M` is the series
`fee_multiplier` (1 for most; 0.5 or 0 for a few). The schedule now rounds to a centicent; the bot
rounds **up to the cent** per price level, which is slightly conservative. No settlement fees.

### Order placement: limit orders first (fees minimised)

`ORDER_MODE = "limit"` (config.py). Directional entries are **passive limit buys** posted 1¢ inside
the best bid (or joining the bid when the spread is 1¢); they never cross the spread. The edge and
Kelly size are computed at our limit price net of the **maker** fee, which is $0 on most series and
`round_up(M × 0.0175 × C × P × (1−P))` on the ~160 series whose `fee_type` is
`quadratic_with_maker_fees`. Take-profit exits are passive limit sells 1¢ inside the ask (never
below the target). We still **take liquidity** only when the edge is time-critical and clears the
full taker fee: arbitrage baskets (all legs must fill together), `expiry_convergence`, any entry in a
market expiring within 24h, stop-losses, momentum/reversion time exits, and a take-profit whose
limit exit expired unfilled.

**Limit fill model (conservative; read this):** the bot only sees the market every 6–7 hours, so
fills are coarse. A resting order is checked on the next snapshot and fills only if the market
*traded through* our price: for a buy at q, the side's ask is now ≤ q, or new volume printed with a
last trade ≤ q − 1¢ (mirror rules for sells). Even then we get only a queue-position share of the new
volume: 10% (35% if price moved ≥ 2¢ through us), so partial fills are normal. Unfilled remainders
are cancelled after 2 snapshots (market-making quotes after 1). Because a buy fills only when price
comes *down* through it, adverse selection is built in. Real intra-snapshot fills (and misses) are
not observed, so live fill rates could be higher or lower.

For every maker fill the bot also records the **all-taker baseline**: the taker fee at the ask (bid
for sells) seen when the order was posted, plus the price improvement versus crossing. The app shows
fees paid, maker vs taker fees, fees saved, price improvement and fill rate.

### Expiry-bucket × take-profit shadow books

Every cycle also runs **24 shadow paper portfolios**, each with its own $100k: 4 expiry buckets
(≤1 day / <48h, 2–31 days, 32–183 days, >6 months, by expected expiration) × 6 exit rules (hold to
settlement, +10%, +25%, +50%, +100% take-profit, and +25% TP with a −25% stop). Each uses the main
book's strategies, parameters, allocation and risk rules, but may only open positions in its bucket
and exits by its own rule. A take-profit triggers when the position's mid-mark value ≥ cost × (1+TP).
Results are **net of every entry and exit fee**; gross (pre-fee) results are shown alongside. They
reuse the one market snapshot and the shared orderbook cache, so they add no market-data API calls
(they share the 450-orderbook budget). State: `state/variants.json.gz`. The main book is unchanged
except that it now uses the same limit-order logic and has its own (initially off) TP/SL and
expiry-weight knobs (`main_rules`).

The **Analysis** tab shows a heatmap of bucket × rule (net/gross return, fees, fees saved, fill
rate, realized, unrealized, fills, win rate, max drawdown, Sharpe once there are ≥ 8 snapshots), a
details card per cell, an equity comparison (main vs the best combo in each bucket), and a "best
combo so far" callout with a small-sample warning (< 12 snapshots or < 20 closed trades).

Weekly, the report ranks all combos. If one beats the main book **clearly and robustly** (≥ 30
closed and ≥ 15 settled trades, ≥ 20 snapshots, Sharpe ≥ main + 0.5, net return ≥ main + 1 pt,
drawdown no worse than main + 5 pts), the main book moves one step toward it: its take-profit moves
one level along hold → +10% → +25% → +50% → +100%, it adopts the stop-loss if the winner has one,
and expiry weights shift (winner bucket +0.25, others −0.10, bounded 0.5–1.5; weights scale Kelly
size). Every change is logged with the numbers behind it.

### Risk & sizing

* **Fractional Kelly (0.25×)** per trade: f* = (p − c)/(1 − c), where c = price + fee per contract,
  applied to the strategy's own sleeve.
* **Exposure caps** (fractions of total equity): 2.5% per market, 5% per event (8% for risk-free
  baskets), 10% per series, 30% per category, plus each strategy's sleeve and a 5% cash reserve.
  Event/series caps are the correlation-aware limits (outcomes of one event are highly correlated).
* **Drawdown circuit breaker**: −15% from peak halves all sizes; −25% halts new directional trades
  (arbitrage still allowed).
* **No double-dipping**: a strategy never re-buys a market it already holds (a snapshot's displayed
  liquidity can't be consumed twice); strategies never take opposite sides of the same market
  (ensemble conflict guard).
* **Fee-dominated trades skipped**: edge after fees must clear `min_edge` *and* fees can't exceed 50%
  of gross edge. Edge is re-checked on the actual depth-walked average price.
* **Conservative fills**: buys at the ask, sells at the bid, walking real displayed depth, never more
  than displayed size, fees charged per level.

## Selectivity: only high probability-of-profit trades (`kalbot/quality.py`)

Every candidate entry gets a **POP** (estimated chance the trade ends in profit after fees) and an
**EV** (expected net profit per contract after entry + exit fees):

1. Start from the strategy's probability and keep only part of its claimed edge over the market mid
   (80%; 50% for momentum/mean-reversion; 100% for the NWS weather model, which is already blended).
2. Scale by the strategy's realized/predicted hit rate on its last 300 closed trades, shrunk toward
   1.0 with a Beta prior of 20 pseudo-trades (bounded 0.70–1.10).
3. Cut the edge by up to 30% for markets 7→120 days from closing; subtract a spread penalty
   (0.5 × spread beyond 3¢) and 2 pts for thin markets.
4. Hold-to-settle trades profit iff the side wins, so POP = the adjusted probability. Drift trades
   exit at the mark, so POP = Φ(EV / σ), σ = max(6¢, predicted move). Arbitrage baskets: 0.99.

Main-book gate (defaults in `config.SELECT`): POP ≥ **0.65**, EV ≥ **2¢/contract**, EV/cost ≥ **4%**.
Survivors are ranked by EV × POP and only the best **12** open per cycle. Kelly size × 1.0 at the
threshold rising linearly to × 1.5 at POP ≥ 0.90, still inside every exposure cap. Market-making
quotes are only posted on sides that clear the same gate. Each cycle logs candidates seen / passed /
opened / skipped with reasons (Last run tab, `summary.json → last_cycle.selection`).

Comparison books (each $100k, same universe, rules, fee and limit-fill model): **All trades** (old
behaviour) vs **POP ≥ 0.60 / 0.70 / 0.80**, shown in the Analysis tab with a calibration check
(average predicted POP vs realized hit rate). The weekly review moves the main book's MIN_POP one
0.05 step (bounded 0.55–0.85) toward a POP book only when it beats the book nearest the current
setting under the same sample-size / Sharpe / return / drawdown guardrails used for combo adoption.

## Weekly re-evaluation (`python -m kalbot.evaluate`, Sundays)

For each strategy: weekly realized + mark-to-market P&L, return on sleeve, annualized Sharpe and
Sortino (per-run returns), hit rate, Brier score and calibration gap on settled trades (and the
market's own Brier for comparison), max drawdown.

**Reallocation** blends *multiplicative weights* (w ← w·e^{η·score}, score = tanh(20·weekly return) +
0.1·Sharpe, clipped to [−1, 1]) with *Thompson sampling* over Beta(1+wins, 1+losses) (its weight
grows with evidence), then applies a 3% floor, 30% cap and 5% cash reserve. A strategy with two
losing weeks in a row and a cumulative loss > 8% of its sleeve goes on **probation** (1% sleeve, keeps
collecting evidence) and is reinstated when it recovers.

**Tuning** (inside hard bounds): `min_edge` loosens 10% for profitable strategies and tightens 20%
after losing weeks; the favorite-longshot curve (α, β) is refit by MAP logistic regression on
settled markets harvested from the bot's own price history (short and long horizons separately);
momentum/reversion κ is set from the pooled lag-1 autocorrelation; weather σ-scale tracks calibration
and the model/market blend weight tracks relative Brier. A markdown + JSON report is written to
`docs/data/reports/` and shown in the app.

## Other profit levers implemented

* Cross-market ladder consistency arbitrage (`ladder_arb`) and NO-basket arbitrage on any subset of a
  mutually exclusive set.
* Exhaustive-bucket YES baskets.
* Interest on idle cash (assumed 3.5% APY, `CASH_APY` in `config.py`; set 0 to disable).
* Fee-aware filtering, depth-aware limit prices, per-level fee rounding.
* Signal ensembling (NWS model × market mid) and an opposite-side conflict guard across strategies.
* Self-calibration: every settlement the bot observes improves its longshot/favorite model.
* Pair netting for market making (YES+NO → $1) to recycle capital immediately.

### Ideas considered but not implemented (and why)

* **Cross-exchange arbitrage (Polymarket, PredictIt, sportsbooks)** — needs reliable contract
  matching across venues and their resolution rules differ; easy to fool yourself in paper trading.
* **Sports models (Elo / odds-feed consensus)** — free, reliable odds APIs need keys or have tight
  quotas; may add later via The Odds API with a free key.
* **Economic-release models (CPI, jobs) vs Cleveland Fed nowcasts** — feasible, but each series needs
  bespoke parsing of strikes and rules; a good next strategy.
* **Daily-low temperature markets** — NWS "overnight low" periods don't align cleanly with the
  calendar-day low Kalshi settles on; skipped to avoid a systematic bias.
* **Intraday trading / faster loop** — the user asked for 6–8 hour snapshots; faster loops would
  better capture market-making and momentum but would also need websocket data.
* **Selling (closing) arbitrage baskets early when they converge** — small gain, adds complexity;
  baskets are held to settlement.

## Run it locally

```bash
python -m pip install -r requirements.txt
python -m pytest -q                 # includes a live read-only dry run (KALGROK_OFFLINE=1 to skip)
python -m kalbot.run                # one paper cycle, updates state/ and docs/data/
python -m kalbot.evaluate           # weekly review now
python -m http.server -d docs 8000  # open http://localhost:8000
```

To start over: delete `state/*` and `docs/data/*.json` and run a cycle.

## Deploy (GitHub Actions + Pages)

1. Push to a GitHub repo named `kal-grok` (branch `main`).
2. **Settings → Pages → Build and deployment → Source: GitHub Actions** (the workflows deploy
   `/docs`). Dashboard: `https://<user>.github.io/kal-grok/`.
3. **Settings → Actions → General → Workflow permissions: Read and write**.
4. Actions → *paper-trade cycle* → *Run workflow* once to kick things off.

On iPhone: open the dashboard in **Safari → Share → Add to Home Screen**. It runs full-screen,
works offline with the last data, and supports pull-to-refresh.

**Auto-update:** the deploy workflows stamp a build id (git SHA + hash of the app-shell files) into
`sw.js`, `index.html` and `app.js`. When the code changes, the service worker gets new bytes, installs,
calls `skipWaiting()` + `clients.claim()`, and the page reloads once ("App updated ✓" toast). The app
calls `registration.update()` on launch, whenever it returns to the foreground, and every 30 minutes,
and the SW is registered with `updateViaCache: "none"`. HTML/JS/CSS and data are network-first, with
the cache used only offline. `start_url`/`scope` never change, so the home-screen icon keeps working.
Data-only bot commits don't change the build id, so they don't force reloads.

Notes: GitHub may delay scheduled runs by several minutes at busy times, and disables schedules
in repos with no activity for 60 days (the bot's own commits keep it active). Each cycle commits
~100–200 KB of compact JSON; if the repo ever gets large, squash old history on a branch.
