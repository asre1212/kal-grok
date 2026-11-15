# Weekly strategy review — 2026-11-15

_Paper trading only. Hypothetical results; no real orders._

- Equity: **$96,819.18** (week -3,180.82 / -3.18%, since start -3.18%)
- Max drawdown this week: 2.85%
- Price-change autocorrelation (lag 1): —
- Calibration fit: long beta=1.120 (n=0), short beta=0.977 (n=1582)

## Scorecard

| Strategy | Week P&L | Total P&L | Ret/sleeve | Sharpe | Sortino | Hit | Brier | MaxDD | Score | Alloc |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| event_arb | +0.00 | +0.00 | +0.00% | — | — | — | — | 0.0% | +0.00 | 14.0% → 16.4% |
| ladder_arb | +2.31 | +2.31 | +0.02% | 20.94 | 136.88 | — | — | 0.0% | +0.30 | 10.0% → 14.4% |
| longshot_fade | -117.66 | -117.66 | -0.79% | -28.68 | -28.13 | — | — | 0.8% | -0.46 | 15.0% → 13.5% |
| expiry_convergence | -2,643.34 | -2,643.34 | -17.80% | -24.54 | -25.06 | 75% | 0.215 | 16.7% | -1.00 | 15.0% → 10.2% |
| momentum | +0.00 | +0.00 | +0.00% | — | — | — | — | 0.0% | +0.00 | 7.0% → 8.9% |
| mean_reversion | +0.00 | +0.00 | +0.00% | — | — | — | — | 0.0% | +0.00 | 7.0% → 8.4% |
| market_making | -78.86 | -78.86 | -0.66% | -26.49 | -26.53 | 50% | — | 0.7% | -0.43 | 12.0% → 11.3% |
| weather_nws | -347.81 | -347.81 | -2.34% | -33.87 | -31.53 | — | — | 1.2% | -0.74 | 15.0% → 11.8% |

## What changed and why

- **event_arb** allocation 14.0% -> 16.4% (score +0.00, week P&L $+0.00)
- **ladder_arb** allocation 10.0% -> 14.4% (score +0.30, week P&L $+2.31)
- **longshot_fade** allocation 15.0% -> 13.5% (score -0.46, week P&L $-117.66)
- **expiry_convergence** allocation 15.0% -> 10.2% (score -1.00, week P&L $-2,643.34)
- **momentum** allocation 7.0% -> 8.9% (score +0.00, week P&L $+0.00)
- **mean_reversion** allocation 7.0% -> 8.4% (score +0.00, week P&L $+0.00)
- **market_making** allocation 12.0% -> 11.3% (score -0.43, week P&L $-78.86)
- **weather_nws** allocation 15.0% -> 11.8% (score -0.74, week P&L $-347.81)
- **expiry_convergence** min_edge: 0.012 -> 0.0144
- **expiry_convergence** beta: 1.221 -> 1.0

## Expiry bucket × take-profit combos (net of all fees)

Main book: net -3.18% (gross -2.88%), fees $297.50, Sharpe —, max DD 3.18%, 14 closed.

| # | Expiry | Exit rule | Net ret | Gross ret | Fees | Saved vs taker | Fill rate | Trades | Closed | Win | MaxDD | Sharpe |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | >6 months | Hold to settle | -0.03% | -0.03% | $0.00 | $6.20 | 7% | 7 | 0 | — | 0.0% | — * |
| 2 | >6 months | +10% | -0.03% | -0.03% | $0.00 | $6.20 | 7% | 7 | 0 | — | 0.0% | — * |
| 3 | >6 months | +25% | -0.03% | -0.03% | $0.00 | $6.20 | 7% | 7 | 0 | — | 0.0% | — * |
| 4 | >6 months | +50% | -0.03% | -0.03% | $0.00 | $6.20 | 7% | 7 | 0 | — | 0.0% | — * |
| 5 | >6 months | +100% | -0.03% | -0.03% | $0.00 | $6.20 | 7% | 7 | 0 | — | 0.0% | — * |
| 6 | >6 months | +25% / −25% SL | -0.03% | -0.03% | $0.00 | $6.20 | 7% | 7 | 0 | — | 0.0% | — * |
| 7 | 32–183 days | Hold to settle | -0.07% | -0.07% | $1.41 | $20.41 | 11% | 15 | 0 | — | 0.1% | — * |
| 8 | 32–183 days | +10% | -0.07% | -0.07% | $1.41 | $20.41 | 11% | 15 | 0 | — | 0.1% | — * |
| 9 | 32–183 days | +25% | -0.07% | -0.07% | $1.41 | $20.41 | 11% | 15 | 0 | — | 0.1% | — * |
| 10 | 32–183 days | +50% | -0.07% | -0.07% | $1.41 | $20.41 | 11% | 15 | 0 | — | 0.1% | — * |
| 11 | 32–183 days | +100% | -0.07% | -0.07% | $1.41 | $20.41 | 11% | 15 | 0 | — | 0.1% | — * |
| 12 | 32–183 days | +25% / −25% SL | -0.07% | -0.07% | $1.41 | $20.41 | 11% | 15 | 0 | — | 0.1% | — * |

_* small sample_

Main rules unchanged — top combo `6m+|hold` not adopted: sample too small, Sharpe edge < 0.5.

## Tuning notes

- longshot_fade: min_edge: unchanged (only 0 closed trades)
- longshot_fade: beta unchanged: only 0 calibration samples
- expiry_convergence: min_edge 0.012 -> 0.0144: losing week, demand more edge
- expiry_convergence: beta 1.221 -> 1.0: refit on 1582 short-horizon settlements (alpha=-0.037)
- momentum: min_edge: unchanged (only 0 closed trades)
- mean_reversion: min_edge: unchanged (only 0 closed trades)
- weather_nws: min_edge: unchanged (only 0 closed trades)
