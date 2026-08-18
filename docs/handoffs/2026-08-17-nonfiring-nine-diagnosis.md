# Diagnosis: the nine non-firing strategies

**Date:** 2026-08-17
**By:** Cody
**Status:** findings only, nothing changed. Items 1 and 2 below need a Raven
ruling BEFORE execution, because they move the graveyard's headline numbers.

## Method and validation

The sweep's own pipeline was re-run outside the harness with per-clause
counters: `load_csv` to last-20% test slice to `precompute_indicators` to
`_make_window` to `strategy.scan()`.

The replication reproduces the graveyard's exact trade counts for
`V3_intraday_momentum_crypto` (2 trades, SOL/USDT 15m), `V5_forced_flow_crypto`
(1 trade, BTC_USD 1h), `V4_trend_reclaim` (6 hits, exactly AAPL/AMGN/HON/HYG/
TLT/XLU 1wk), and `rising_three_methods` (1-2 hits on DUOL/META/TSLA/HD/REGN,
0 on SPY). Series count 860 + 25 slice-skipped = 885, matching the graveyard's
885.

Everything below is measured, not read. This matters: convention 3 and the
standing rule that "the threshold looks tight" is a guess, not a diagnosis.

## Headline: this is not 9 bugs. It is 2 bugs and 3 systemic conditions.

### Systemic condition 1: the sweep starves slow strategies of bars

`scan_all_bars` starts at `min_idx = min(SCAN_WINDOW, 100) = 100`
(`backtest/vectorized_harness.py:1024`), applied to a last-20% test slice.

| timeframe | series | median slice | median scannable bars | total scannable |
|---|---|---|---|---|
| 5m | 178 | 936 | 836 | 596,922 |
| 15m | 178 | 312 | 212 | 204,978 |
| 1h | 175 | 1,015 | 915 | 195,282 |
| **1d** | **175** | **101** | **1** | **5,100** |
| 1wk | 173 (25 skipped) | 157 | 57 | 7,898 |

**Daily series get a median of ONE scannable bar.** The sweep sees 5,100 of the
101,762 daily bars on disk, which is 5.01%.

Consequence beyond these nine: "509,080 tests" overstates the daily evidence by
roughly 20x. Every daily and weekly strategy in the graveyard is affected.

### Systemic condition 2: the confirmation stack is a trend filter applied to every strategy

`run_incremental_graveyard.py:125-127` overrides `volume_min_ratio`,
`rsi_max_entry` and `location_atr_mult` but never sets
`apply_confirmation_stack` or `require_regime_uptrend`, and `config.yaml` has no
such keys, so `vectorized_harness.py:610-611` defaults both to `True`. Every
signal must additionally satisfy `close > rising EMA50`
(`vectorized_harness.py:791`).

Measured baseline pass rate on scanned daily bars: **6.71%** (342/5,100).

What it removed:

| strategy | removed by the stack |
|---|---|
| V2_vwap_magnet_sessionatr | 100% (33 of 33) |
| V2_vwap_magnet (the control twin) | 99.5% (640 of 643) |
| V5_capitulation_equity | 92% of candidate days |
| V3_intraday_momentum_crypto | 87% (14 of 16) |
| V4_trend_reclaim | 82% (27 of 33) |

**A mean-reversion strategy filtered through `close > rising EMA50` has not been
tested and found wanting. It has not been tested.** Convention 11: every
zero-trade FAIL from a fade or capitulation strategy is currently mislabelled.

On capitulation days specifically, `regime_uptrend` is true 7.77% of the time
against a 49.71% unconditional baseline: a 6.4x suppression of exactly the
strategies designed to buy weakness.

The machinery to turn it off already exists and is already used elsewhere
(`constraint_sweep.py:64`, `dispersion_gate.py:353`). It was never wired into
the main sweep.

### Systemic condition 3: timestamp-grid and data-coverage assumptions go unvalidated

None of these raise. They produce silent zeros that read as verdicts.

- 1h equity bars stamp on the hour in ET, so V2's `[930, 945)` trigger box is
  permanently empty. Measured minute-of-day histogram: 240, 300, ... 900, 960,
  1020, 1140. There is no bar in the window.
- 1h crypto bars stamp minute 1380 (23:00), so V3's 23:30 trigger is
  unreachable.
- The funding table covers 2025-09-12 to 2026-08-13; the Binance price slices
  run 2025-07-20 to 2025-09-30. An 18-day overlap.

### The two genuine strategy-level defects

**`rsi_extreme`** (`strategies/builtin/expanded.py:263`, binding clause 281-284).
Entry requires `rsi14 < 35` AND `close > ema50`.

| clause | count | rate |
|---|---|---|
| `rsi14 < 35` | 4,783 | 11.39% |
| `close > ema50` | 21,982 | 52.33% |
| **both** | **0** | **0.0000%** |

Independence would predict ~2,500. RSI(14) conditional on `close > EMA50`
(n=21,976) has a hard floor: min 36.26, p0.01 38.51, p1 44.51, median 58.35.
**The threshold sits below the support of the conditional distribution.** RSI
under 35 needs ~14 bars of dominant downside, which mechanically drags close
under a 50-period EMA. Category (b): unsatisfiable, not tight.

Fix is one character. `rsi_val >= 45` gives 1.28% firing; `>= 50` gives 10.12%.
The thesis (oversold pullback in an uptrend) is sound.

**`C2`** (`strategies/builtin/strategy_lab.py:286`). Two separate problems.

*All 9,735 C2 rows in the graveyard are stale.* 9,042 carry the reason string
`"needs 840 bars, scan window is 260"`, and **that string does not exist
anywhere in the current codebase.** The current gate
(`vectorized_harness.py:1089-1093`) emits `"needs 840 bars, series has {n}"`,
which appears on only 154 rows. Those 9,042 rows were written by a pre-fix
harness that refused to widen the scan window; `scan_all_bars:1022` now widens
to `max(SCAN_WINDOW, min_bars)`. **C2 has never been run under current code.**
Under it, C2 would be testable on 401 of 860 series.

*A units bug makes C2 impossible on sub-hourly bars.* The anchor search at
`strategy_lab.py:311` is `while i >= 0 and i >= n - 24 * 4`. That is 96 bars,
intended as "4 days", true only for hourly bars. On 15m it reaches back 24
hours; on 5m, 8 hours. It can never reach Friday. Measured: the anchor fails
100% of trigger bars on BTC/USDT 15m (72/72), BTC_USD 15m (8/8), BTC_USD 5m
(24/24).

*And when it does run, long-only kills it.* Funnel over 26,444 crypto bars with
the 840-bar window supplied: 10 fully-qualified weekend vacuums, and **all 10
were moves UP.** `strategy_lab.py:357-359` discards every one. C2 cannot reach
20 trades as a long-only spot strategy.

## Per-strategy summary

| # | strategy | file:line | binding clause | measured | cause | minimal fix |
|---|---|---|---|---|---|---|
| 1 | `rsi_extreme` | expanded.py:263 | `rsi<35` and `close>ema50` | 0 / 42,010; conditional RSI floor 36.26 | (b) | `35` to `45` |
| 2 | `rising_three_methods` | patterns_all.py:227 | `small_reds` | 0.76% marginal; 2 / 32,679 | (a) | `0.7` to `1.0` ATR, or judge pooled |
| 3 | `V4_gap_hold_proxy` | strategy_lab_v4.py:130 | none internal, bar supply | 0.511% base rate; 18 raw to 4 | (f) | scan daily from `min_bars`, not 100 |
| 4 | `V4_trend_reclaim` | strategy_lab_v4.py:249 | 4 weeks below MA (99.0%), then `volume>=1.2` | 33 raw to 6; 27/27 lost to volume alone | (a)+(f) | exempt weekly bars from `volume_min_ratio` |
| 5 | `V5_capitulation_equity` | strategy_lab_v5.py:443 | `require_regime_uptrend` | 103 events / 101,746 bars; regime true 7.77% vs 49.71%; E[trades] = 0.40 | (b) vs harness | `apply_confirmation_stack=False` for this cohort |
| 6 | `V2_vwap_magnet_sessionatr` | strategy_lab_v2.py:772 | session-ATR scaling (x8.83 on 5m) then stack | 33/4,068 raw; 0/33 survive; 1h has 0 trigger bars | (a)+(b)+(c) | drop stack; bar-size-aware trigger window |
| 7 | `C2` | strategy_lab.py:286 | `weekend_move >= 0` long-only | 9,042 rows stale; 10/10 vacuums were up moves; 96-bar anchor fails 100% sub-hourly | (d)+(f) | re-run; fix bars-vs-hours; then retire or short-enable |
| 8 | `V3_intraday_momentum_crypto` | strategy_lab_v3.py:192 | universe size | 282 day-opportunities total; 16 raw (5.7%) to 2 | (f) | more crypto history; drop stack |
| 9 | `V5_forced_flow_crypto` | strategy_lab_v5.py:261 | funding-table coverage; cascade geometry | 3,279 bars have no funding date; cascade rejects 94.5%; 5 raw to 1 | (c)+(a) | extend funding history; relabel uncovered as NOT_TESTED |

Category key: (a) threshold too tight, (b) logically impossible, (c) missing
data field, (d) structurally could not run, (f) other.

Note on #5's zero: expected trades in the scanned window =
5,100 bars x 0.00101 x 0.0777 = **0.40**. Observing zero is the expected
outcome, not evidence about the hypothesis.

Note on #9: 3,279 bar-evaluations currently sit inside FAIL rows for a series
the harness structurally could not evaluate. Convention 11 / D-255 applies:
those should be NOT_TESTED.

## Recommended order of work

1. Set `apply_confirmation_stack=False` for the mean-reversion cohort and
   re-run. A config change that unblocks four strategies.
2. Lower `min_idx` to `max(strategy.min_bars, 25)` for daily and weekly series.
3. Fix `rsi_extreme`'s threshold and `C2`'s lookback units. Both one-liners,
   both deserve a D-number.
4. Delete C2's 9,042 stale rows before anyone cites them.

**Items 1 and 2 change the graveyard's headline numbers, so they need a Raven
ruling before execution, not after.**

## Scope caveat, to be read narrowly

The full sweep was not re-run. The "would become N findings" figures are
raw-signal counts from the replication, not PASS counts. A strategy that starts
firing may still fail on economics.

The claim here is only this: **seven of these nine were never given the chance
to lose.**
