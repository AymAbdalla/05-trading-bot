# Constraint Sensitivity: PRELIMINARY, and my prediction was wrong

**Date:** 2026-08-13
**Status:** PRELIMINARY - 3 series only. Full 14-series run in progress.
**Do not cite this yet.** It is recorded because the prediction was made in
advance and the result contradicted it, which is worth keeping either way.

## The prediction, made before the run

"I expect the curve to be FLAT - per-trade PnL essentially unchanged from
aggressive to conservative, with trade count dropping several-fold. That would
mean the confirmation stack is filtering volume, not quality."

## The result (AAPL, MSFT, NVDA on 1h, fixed_2r exit)

| Level | Trades | PnL/trade | Strategies firing |
|---|---|---|---|
| AGGRESSIVE (no gate) | 13,711 | -0.2435 | 38 |
| BASE (RSI 70, vol 1.2) | 3,956 | -0.2904 | 35 |
| CONSERVATIVE (RSI 45, vol 2.0) | 116 | **+0.0943** | 11 |

Tightening improved per-trade PnL by +0.338, and the conservative level is
the **first positive number this project has produced.** The prediction was
wrong on its face.

## Why it is not yet believable

Composition of the +$10.94 total:

| Strategy | Trades | PnL | Per trade |
|---|---|---|---|
| stoch_rsi_oversold | 23 | +4.66 | +0.203 |
| dca_7 | 8 | +2.93 | +0.366 |
| **hammer** | **1** | +1.48 | +1.479 |
| **S1** | **1** | +1.26 | +1.262 |
| V2_round_number_decay | 2 | +0.93 | +0.467 |
| grid_1.0atr | 26 | +0.48 | +0.018 |
| grid_2.0atr | 26 | -2.43 | -0.094 |

Three problems, stated plainly:

1. **39% of the profit comes from strategies that fired 1-2 times.** Hammer
   and S1 contributed $2.74 between them on a single trade each. That is
   noise wearing a decimal point.
2. **The only samples with any weight are flat or negative.** grid_1.0atr at
   26 trades is +0.018 (zero). grid_2.0atr at 26 trades is -0.094. The one
   meaningful positive is stoch_rsi_oversold at 23 trades, which is still
   below any reasonable bar.
3. **Three tickers, all mega-cap tech, highly correlated.** AAPL, MSFT and
   NVDA are close to one observation for this purpose.

## Why it is not dismissible either

The conservative gate is "deeply oversold (RSI < 45) plus a genuine volume
surge (>2x) in an uptrend." That is a coherent mean-reversion setup with a
stated mechanism, not an arbitrary threshold. If any configuration in this
library were to work, something shaped like that is a reasonable candidate.

And the direction is consistent across the two levels that have sample:
-0.244 (no gate) -> -0.290 (base) -> +0.094 (harsh). Note the BASE level is
WORSE than aggressive, which is itself interesting: the default gate may be
filtering out the good trades while the harsh gate keeps them.

## What decides it

1. **The full 14-series sweep** (equities, ETFs, crypto, futures, 5m/1h/1d,
   3 exit configs) is running. More sample, more asset classes, less
   correlation.
2. **If it holds, run `backtest/conditional_edge.py` on it** - select the
   winning configuration on half the underlyings, verify on the other half.
   That is the test that killed every previous positive result today.
3. **Trade count is the gating problem.** At 118x selectivity reduction, a
   conservative gate produces so few trades that reaching the SPEC's 150-trade
   bar requires pooling across many instruments. Pooled analysis exists.

Until those run, the correct statement is: "the first non-negative signal,
built on samples too thin to trust, worth the compute to test properly."
