# Weekly strategy review — 2026-11-22

_Paper trading only. Hypothetical results; no real orders._

- Equity: **$94,950.75** (week -1,868.43 / -1.93%, since start -5.05%)
- Max drawdown this week: 4.45%
- Price-change autocorrelation (lag 1): -0.136
- Calibration fit: long beta=1.050 (n=124), short beta=1.019 (n=4040)

## Scorecard

| Strategy | Week P&L | Total P&L | Ret/sleeve | Sharpe | Sortino | Hit | Brier | MaxDD | Score | Alloc |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| event_arb | +0.00 | +0.00 | +0.00% | — | — | — | — | 0.0% | +0.00 | 16.4% → 13.6% |
| ladder_arb | +5.70 | +8.01 | +0.04% | 5.01 | 12.92 | — | — | 0.1% | +0.31 | 14.4% → 19.1% |
| longshot_fade | -1,202.70 | -1,320.36 | -9.19% | -15.09 | -13.35 | 0% | 0.706 | 9.7% | -1.00 | 13.5% → 9.3% |
| expiry_convergence | +2,304.74 | -338.60 | +23.37% | 0.16 | 0.22 | 100% | 0.097 | 16.7% | +1.00 | 10.2% → 22.9% |
| momentum | -1,842.57 | -1,842.57 | -21.24% | -10.88 | -10.26 | 0% | 0.036 | 26.4% | -1.00 | 8.9% → 7.0% |
| mean_reversion | +169.00 | +169.00 | +2.07% | 1.22 | 2.22 | 44% | 0.025 | 9.5% | +0.51 | 8.4% → 14.4% |
| market_making | -170.81 | -249.67 | -1.55% | -13.86 | -12.44 | 22% | — | 2.1% | -0.60 | 11.3% → 7.5% |
| weather_nws | -1,197.65 | -1,545.46 | -10.42% | -5.80 | -5.94 | 18% | 0.162 | 10.4% | -1.00 | 11.8% → 1.0% |

## What changed and why

- **weather_nws** status active -> probation: two straight losing weeks and cumulative loss > 8% of sleeve
- **event_arb** allocation 16.4% -> 13.6% (score +0.00, week P&L $+0.00)
- **ladder_arb** allocation 14.4% -> 19.1% (score +0.31, week P&L $+5.70)
- **longshot_fade** allocation 13.5% -> 9.3% (score -1.00, week P&L $-1,202.70)
- **expiry_convergence** allocation 10.2% -> 22.9% (score +1.00, week P&L $+2,304.74)
- **momentum** allocation 8.9% -> 7.0% (score -1.00, week P&L $-1,842.57)
- **mean_reversion** allocation 8.4% -> 14.4% (score +0.51, week P&L $+169.00)
- **market_making** allocation 11.3% -> 7.5% (score -0.60, week P&L $-170.81)
- **weather_nws** allocation 11.8% -> 1.0% (score -1.00, week P&L $-1,197.65)
- **longshot_fade** beta: 1.12 -> 1.05
- **expiry_convergence** beta: 1.0 -> 1.019
- **momentum** kappa: 0.35 -> 0.05
- **mean_reversion** kappa: -0.35 -> -0.136
- **mean_reversion** min_edge: 0.03 -> 0.027
- **weather_nws** sigma_scale: 1.0 -> 1.1
- **weather_nws** min_edge: 0.05 -> 0.06
- **weather_nws** model_weight: 0.35 -> 0.3

## Expiry bucket × take-profit combos (net of all fees)

Main book: net -5.05% (gross -4.61%), fees $440.57, Sharpe -6.73, max DD 5.05%, 101 closed.

| # | Expiry | Exit rule | Net ret | Gross ret | Fees | Saved vs taker | Fill rate | Trades | Closed | Win | MaxDD | Sharpe |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | ≤1 day (<48h) | +10% | -6.04% | -5.78% | $263.64 | $498.10 | 5% | 172 | 125 | 36% | 6.6% | -7.02 |
| 2 | ≤1 day (<48h) | +25% | -6.04% | -5.78% | $263.64 | $498.10 | 5% | 172 | 125 | 36% | 6.6% | -7.02 |
| 3 | ≤1 day (<48h) | Hold to settle | -6.07% | -5.81% | $262.68 | $498.03 | 5% | 171 | 125 | 35% | 6.6% | -7.08 |
| 4 | ≤1 day (<48h) | +50% | -6.08% | -5.81% | $262.78 | $498.03 | 5% | 171 | 126 | 36% | 6.6% | -7.08 |
| 5 | ≤1 day (<48h) | +100% | -6.07% | -5.81% | $262.68 | $498.03 | 5% | 171 | 125 | 35% | 6.6% | -7.08 |
| 6 | ≤1 day (<48h) | +25% / −25% SL | -6.14% | -5.87% | $270.05 | $498.00 | 5% | 170 | 126 | 35% | 6.6% | -7.09 |
| 7 | >6 months | Hold to settle | -0.20% | -0.20% | $0.00 | $67.01 | 7% | 70 | 53 | 51% | 0.2% | -7.15 |
| 8 | >6 months | +50% | -0.20% | -0.20% | $0.00 | $67.01 | 7% | 70 | 53 | 51% | 0.2% | -7.15 |
| 9 | >6 months | +100% | -0.20% | -0.20% | $0.00 | $67.01 | 7% | 70 | 53 | 51% | 0.2% | -7.15 |
| 10 | >6 months | +25% | -0.23% | -0.23% | $2.02 | $67.01 | 7% | 70 | 54 | 52% | 0.3% | -8.39 |
| 11 | >6 months | +10% | -0.23% | -0.23% | $3.51 | $65.41 | 7% | 67 | 54 | 54% | 0.3% | -8.41 |
| 12 | 2–31 days | +25% / −25% SL | -8.02% | -7.89% | $129.50 | $490.63 | 15% | 129 | 88 | 30% | 8.3% | -9.65 |

_* small sample_

Main rules unchanged — top combo `1d|tp10` not adopted: sample too small, Sharpe edge < 0.5, net return edge < 1.0 pts.

## Selectivity: probability-of-profit books (main MIN_POP = 0.65)

| Book | Net ret | Fees | Trades | Closed | Win | Avg POP → hit | MaxDD | Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| All trades (no POP filter) | -10.87% | $0.27 | 118 | 40 | 25% | 34% → 19% | 10.9% | — |
| POP ≥ 0.60 | -2.91% | $0.27 | 14 | 2 | 0% | 67% → 0% | 2.9% | — |
| POP ≥ 0.70 | -0.45% | $0.27 | 5 | 0 | — | — → — | 0.5% | — |
| POP ≥ 0.80 | -0.06% | $0.27 | 3 | 0 | — | — → — | 0.1% | — |

MIN_POP unchanged: POP ≥ 0.70: sample too small, POP ≥ 0.60: sample too small.

## Tuning notes

- longshot_fade: min_edge: unchanged (only 2 closed trades)
- longshot_fade: beta 1.12 -> 1.05: refit on 124 settled markets (alpha=-0.230)
- expiry_convergence: beta 1.0 -> 1.019: refit on 4040 short-horizon settlements (alpha=0.013)
- momentum: min_edge: unchanged (only 3 closed trades)
- momentum: kappa 0.35 -> 0.05: pooled lag-1 autocorr of price changes = -0.136
- mean_reversion: min_edge 0.03 -> 0.027: profitable (Sharpe 1.22), loosen to trade more
- mean_reversion: kappa -0.35 -> -0.136: pooled lag-1 autocorr of price changes = -0.136
- weather_nws: min_edge 0.05 -> 0.06: losing week, demand more edge
- weather_nws: sigma_scale -> 1.1: overconfident by 0.18
- weather_nws: model_weight -> 0.3: market Brier 0.138 beat forecast 0.162
