# Handoff: Strategy Lab v3 (2026-08-13)

## What was built

**`strategies/builtin/strategy_lab_v3.py`** (new) — exports `STRATEGY_LAB_V3_STRATEGIES`,
five strategy instances from three classes:

| Name | Doc strategy | Anchor |
|---|---|---|
| `V3_intraday_momentum` | 2, "The 3:30 Verdict" | Gao/Han/Li/Zhou, JFE 2018 |
| `V3_intraday_momentum_crypto` | 2, UTC-day analog | same |
| `V3_macro_drift` | 4, "Macro Calendar Harvest" (FOMC) | Lucca & Moench JF 2015 |
| `V3_macro_drift_nfp` | 4, NFP variant | Savor & Wilson JFQA 2013 |
| `V3_vacuum_refill` | 1, "Vacuum Refill" | Nagel RFS 2012 |

Reuses the v2 helpers (`_et_parts`, `_in_session`, `_rth_day_indices`, `_session_atr`,
`_atr`, `_bar_seconds`, `_mean_std`) plus `_percentile_rank` / `_clamp` from v1. The
macro calendar (`backtest/data/aux/macro_calendar.json`, 29 FOMC + 144 NFP dates) is
parsed ONCE at module import into frozen date sets.

**`tests/test_strategy_lab_v3.py`** (new) — 72 tests, all passing. Covers degenerate
input, a fires case and at least one near-miss per strategy, statelessness,
window-immutability, name prefix/uniqueness, and calendar-load failure.

## Signal counts on real data (260-bar sliding window, like `scan_all_bars`)

| Strategy | SPY_5m | QQQ_5m | AAPL_5m | SPY_15m | BTC_USD_1d | BTC 15m merged |
|---|---|---|---|---|---|---|
| `V3_intraday_momentum` | 18 | 24 | 22 | 13 | 0 | 24 |
| `V3_intraday_momentum_crypto` | 8 | 10 | 6 | 8 | 0 | 26 |
| `V3_macro_drift` | 16 | 16 | 16 | 16 | 0 | 8 |
| `V3_macro_drift_nfp` | 22 | 22 | 22 | 22 | 0 | 11 |
| `V3_vacuum_refill` | 19 | 69 | 129 | 19 | 0 | 130 |

No strategy is dead. Every emitted signal passed `stop < entry < target`.

The 1d column is zero **by construction, not by defect**: the four clock-anchored
strategies need an intraday timestamp (15:30 ET / 23:30 UTC / 14:00 ET) and a daily bar
has none. Vacuum Refill does run on 1d; gate instrumentation on BTC_USD_1d showed
230 up-bars, 155 qualifying flushes, only 3 clearing volume z > 3, and 0 of those 3
clearing the realized-vol percentile. Daily crypto volume is too smooth for a z > 3
climax. Investigated and closed.

Cross-variant note: because `scan()` has no ticker identity, the ET variant also fires
on crypto files and the UTC variant also fires on the extended-hours equity CSVs
(23:30 UTC = 19:30 ET). Those crossed readings are noise. Judge `V3_intraday_momentum`
on equities and `V3_intraday_momentum_crypto` on crypto.

## Deviations from `references/strategy-lab-v3.md`

Every one is also written into the class docstring it belongs to.

**V3_intraday_momentum / _crypto**
1. **Exit.** The doc exits flat at 15:58. The harness has no forced intraday time exit,
   so the trade is the doc's 0.5x ATR disaster stop plus a mirrored 0.5x ATR target.
   The position survives into later bars and days. **Sweep this one with `time_4c` /
   `time_8c`** (20 and 40 minutes on 5m bars, bracketing the paper's 28-minute hold);
   any verdict from a non-time exit config is measuring a different trade.
2. **ATR scale.** 0.5x ATR is read against v2's session-scale ATR, not the raw 5m bar
   ATR. 0.5 of a 5m bar ATR is a few cents on SPY. Same units mismatch class that
   killed strategies in the v2 lab.
3. **Amplifier lookback.** The doc wants the 70th percentile of the trailing 60 DAYS.
   60 days of 5m bars is ~11,500 bars against a 260-bar scan window. Substituted: the
   RTH bars of the prior sessions inside the window (about one prior session on 5m,
   three on 15m, sixteen on 1h). Today's mean absolute RTH bar return must exceed the
   70th percentile of prior sessions' absolute RTH bar returns, same for mean volume.
   The direction and rough strictness survive; the 60-day depth does not. A faithful
   version needs a per-ticker daily history table the harness does not pass.
   Extended-hours bars are deliberately excluded from the baseline, otherwise the thin
   after-hours tape drags both percentiles down and the amplifier passes every day.
4. **Entry timing.** Entry is the CLOSE of the 15:30 bar, so on 5m the fill is 15:35.
   Acting at the 15:30 print itself would be lookahead.
5. **Macro-day amplifier INCLUDED** (the task allowed skipping it) using the same
   calendar. The third amplifier is live.
6. Crypto variant: `r12`'s analog is the PENULTIMATE UTC half hour (23:00-23:30),
   trigger at 23:30 into the final half hour. Exact structural mapping of
   9:30-10:00 / 15:00-15:30 / 15:30-16:00 onto a 24h clock. ATR is rescaled to a
   24h horizon, not the 6.5h equity session.

**V3_macro_drift / _nfp**
1. **VIX conditioning OMITTED — prominent.** The doc sizes up when VIX is above its
   1-year median, following Lucca & Moench's own conditioning. `backtest/data/VIX_1d.csv`
   exists but `scan()` receives one instrument's candles and no second series, so VIX is
   unreachable. There is NO VIX condition here at all. Expect weaker and noisier results
   than the citation implies, and **do not read a failure as a refutation of the
   conditional claim.** Restoring it needs a VIX-aware harness channel or a precomputed
   VIX-percentile-by-date table.
2. **Exit.** The doc exits 5 minutes before the release. Substituted: symmetric 1.0x
   session-ATR stop and target. **`time_8c` / `time_16c` are the closest analogs when
   sweeping** (40/80 min on 5m, 2/4h on 15m — all inside the pre-release window).
3. ATR scale: session-scale, same reason as above.
4. "The day before" is the previous CALENDAR day, not the previous trading day. FOMC is
   midweek and NFP is Friday so they coincide in practice, but the rule would miss an
   event whose preceding calendar day is a holiday.

**V3_vacuum_refill**
1. **Cross-pair idiosyncrasy test OMITTED — the largest omission.** The doc's central
   discriminator is that the flushing pair must move > 3x the median move of the other
   two pairs, which is what separates mechanical forced flow from market-wide
   information. No cross-ticker channel exists. **This implementation cannot tell a
   liquidation cascade from a news shock**, and buying informed selling is the documented
   way reversal traders die.
2. **Timeframe.** The doc specifies 1m-5m crypto bars; the lowest available is 15m
   crypto / 5m equity. A 1m cascade is smoothed inside a 15m candle, so the counts above
   are a floor, not the doc's "2-5 on stressed days".
3. Volume baseline is 96 bars — exact for 24h on 15m crypto, an approximation elsewhere.
4. No 30-minute time box (harness has none); `time_4c` is the nearest analog.
5. **No deviation on the cost gate**: the flush must be >= 2.5 x 0.3% = 0.75%. The doc is
   explicit that removing the multiple turns this into a fee donation machine, so it
   stays literal.

**Not implemented at all** (need a cross-sectional harness that does not exist):
doc Strategy 3 "Same-Clock Echo", Strategy 5 "Paid Liquidity Reversal", Bonus
"Attention Gap Fade" (also needs shorting).

## Deliberately NOT done

- **Not registered in `backtest/run_incremental_graveyard.py`.** `ALL_STRATEGIES` there
  is untouched, so no graveyard sweep behaviour changed. Wiring it in is a one-line
  import plus list concat, but it kicks off a full sweep and that is Aym's call, not
  mine.
- No decay monitors built. Each doc strategy ships one in its genome (250-day hit rate
  for intraday momentum, 24-window mean for macro drift, 90-day expectancy for vacuum
  refill). None exist in this codebase yet and none were asked for.

## Next steps for Raven

1. Decide whether to register `STRATEGY_LAB_V3_STRATEGIES` in the graveyard runner.
2. If yes, sweep with the exit configs named above, not the default grid — three of the
   five strategies have time-based exits in the literature that this harness cannot
   express, and the wrong exit config will produce a verdict about the exit rather than
   about the hypothesis.
3. `V3_macro_drift` produces ~16 signals per equity file, under the harness's 20-trade
   gate. It needs pooling across tickers to be judgeable at all.
4. The VIX gap blocks a faithful test of both Strategy 4 and (eventually) Strategy 5.
   A VIX-percentile-by-date aux table would unblock both.
