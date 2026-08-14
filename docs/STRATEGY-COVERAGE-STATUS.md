# Strategy & Data Coverage Status

**Date:** 2026-08-13
**Purpose:** answer, with measurements, what has actually been built and
tested vs what is still open. Updated whenever coverage changes.

## 1. Entry strategies: 35 built, 34 tested

| Group | Count | Status |
|---|---|---|
| `expanded.py` entry strategies | 28 | all tested |
| `strategy_lab.py` (S1, S2, S6, C2, C5, D1, D2) | 7 | 6 tested, **C2 NOT_TESTED** |

**C2 (WeekendVacuumReversion)** needs 840 bars of history; the vectorized
scan window is 260. It is reported `NOT_TESTED` with a reason rather than
counted as a failure (D-109). Fix requires either raising SCAN_WINDOW for
that strategy or rewriting it to work in a smaller window.

Every other strategy has graveyard entries across all four tested timeframes.

## 2. Exit strategies: 14 built, NOW WIRED IN (2026-08-13)

`EXIT_STRATEGIES_EXPANDED` contains 14 bearish/neutral patterns (bearish
engulfing, shooting star, hanging man, evening star, dark cloud cover, three
black crows, three inside down, tweezer top, and others).

**FIXED 2026-08-13.** Two new exit configs put them into the sweep:
`signal_exit` (stop + bearish-pattern exit) and `signal_exit_2r` (stop +
pattern exit + 2R target). Exits fill at the NEXT bar's open, since a
pattern is only known once its candle closes (SPEC 5.1 #6 "close any open
long at next candle open"). Grid total is now 11 exit configs.

Verified on AAPL 1h: 1,261 bearish exit bars detected, 588 signal exits
fired across 1,107 trades, stops still honored alongside. Result is
consistent with the v0 verdict: -$0.299 per trade, no better than the
mechanical exits.

Previously (for the record) none of them had ever appeared in a graveyard
result, which meant:

- SPEC 5.1 pattern **#6** (shooting star / bearish engulfing as EXIT) is
  untested.
- SPEC 5.1 pattern **#5** (three white soldiers as a trailing-stop tightener)
  is untested.
- SPEC 5.1 pattern **#7** (doji blocks entries) is untested in backtest,
  though it IS enforced live in the scanner.

The event harness (`harness.py`) does scan exit signals, but the graveyard
was built entirely with the vectorized harness, so no exit-signal result
exists anywhere. Closing this needs exit-signal support in the vectorized
harness (or a dedicated exit-overlay run like the inversion module).

## 3. Asset classes

| Class | Tested | Notes |
|---|---|---|
| Equities (20+ sectors) | YES | 180 tickers |
| Sector / index / leveraged / bond / commodity / volatility ETFs | YES | |
| Futures (CL, ES, GC, NQ, NG, RTY, YM, ZB) | YES | 8 contracts |
| Crypto (BTC/ETH/SOL) | YES | Binance 15m/1h + Yahoo daily |
| **Options** | **NO** | overlay built 2026-08-13, not yet in the sweep |
| **Weekly timeframe** | **YES (fixed 2026-08-13)** | 164 files re-downloaded with 15y history; 148 now clear the 100-bar test floor (was 53 bars, silently skipped) |

Timeframes exercised: 5m, 15m, 1h, 1d, and now 1wk. Not 4h (no data anywhere).

## 4. Parameter variations: ad hoc only, and that is deliberate

What exists today is a handful of hand-picked variants: `grid_1.0atr` /
`grid_2.0atr`, `breakout_20` / `breakout_50`, `dca_7` / `dca_14`,
`time_4c/8c/16c`. There has been no systematic parameter sweep.

**Recommendation: do NOT build a parameter-optimization sweep as foundation.**

The current grid is already ~227,000 tests (35 strategies x 9 exits x 180
tickers x 4 timeframes), where chance alone produces a ~4.4 sigma best result
(D-218). Multiplying that by a parameter grid does not build a foundation, it
builds a machine for manufacturing false discoveries. Every parameter value
you test is another hypothesis, and the correction burden grows with the
count whether or not anyone tracks it.

**What IS worth building as foundation: parameter SENSITIVITY, not parameter
SEARCH.** The question is not "which lookback is best" (that answer will be
noise) but "does this strategy survive a +/-10% change in its parameters."
A strategy that only works at exactly lookback=20 and dies at 18 or 22 is
curve-fit and should be killed before Forge ever sees it. The SPEC already
calls for this as a stress probe (parameter jitter), and the event harness
implements it; it is not yet part of the graveyard sweep.

**Optimization belongs to Forge, with hypothesis accounting.** SPEC 5.5's
edit ladder starts at parameter tuning for exactly this reason, and Judge's
SOUL requires correcting on hypotheses GENERATED rather than submitted. If we
pre-sweep every parameter now, we spend that budget before any agent exists
to account for it, and the graveyard inherits an uncorrected multiple-
comparisons debt.

Proposed sequence: (1) add parameter-jitter sensitivity to the sweep as a
robustness FLAG per strategy, (2) leave value optimization to Forge.

## 5. Data status (as of this writing)

| Item | Status |
|---|---|
| Equities/ETF/futures daily + intraday | present, adjusted, 936 clean files |
| Quarantined intraday (sector ETFs, MULN, NVAX) | **re-downloading now** via Alpaca with `adjustment='all'` |
| Weekly 15-year history | **downloading now** via yfinance (recent IPOs correctly skipped: DASH, DUOL, HOOD, IBIT lack 15y) |
| 4h timeframe | not downloaded anywhere |
| Options chains | none (overlay uses synthetic Black-Scholes; real chains are a V4 purchase) |
| Delisted/survivorship-complete universe | none (needs CRSP/Norgate/Sharadar) |

MULN intraday has been recovered from Alpaca (13,954 15m bars), which
restores the survivorship canary that Yahoo could no longer serve.

## 6. Honest summary

- Strategies: essentially complete for entries, **absent for exits**.
- Asset classes: broad for spot instruments, **options and weekly are open**.
- Parameters: intentionally unswept; sensitivity yes, search no.
- Data: two known gaps closing today, two structural gaps (4h, real options
  chains, survivorship) that need either downloads or money.
