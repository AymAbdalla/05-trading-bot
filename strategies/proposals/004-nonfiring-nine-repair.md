---
name: "nonfiring_nine_repair"
thesis: "The nine strategies that never fire are not nine independent bugs: measurement shows two genuine strategy defects and three systemic harness conditions, and seven of the nine were removed by the harness before their logic was ever judged."
expected_edge_bps: null
kill_condition: "Per strategy: if after its named fix it still fires on under 1% of rows tested, it is retired from the roster rather than repaired a second time. For the batch: if the two systemic fixes (apply_confirmation_stack=False for the mean-reversion cohort, and min_idx lowered to max(min_bars, 25) for daily and weekly series) do not raise at least 4 of the 9 above the 1% firing rate, the shared-cause theory is wrong and these revert to nine separate problems triaged individually."
asset_class: "MULTI"
entry_exit_rules: "Not applicable: this is a repair, not a new entry rule. The per-clause measurements that constitute the diagnosis are already done and are recorded in docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md. What this proposal asks for is a ruling on the two systemic changes, which move the graveyard headline numbers, followed by the two one-line strategy fixes and the deletion of C2 stale rows."
data_requirements: "The existing CSVs for everything except V5_forced_flow_crypto, which needs the FUNDING_STRESS_PCTL table extended back to cover the Binance price slices (current overlap is 18 days). Until it is extended, the 3,279 bar evaluations with no funding date are NOT_TESTED, not FAIL (convention 11, D-255)."
related_graveyard_findings: "These nine ARE the graveyard finding. C2 (0 trades / 264 rows), V2_vwap_magnet_sessionatr (0 / 9,460), V5_capitulation_equity (0 / 9,460), V5_forced_flow_crypto (11 / 7,898), V3_intraday_momentum_crypto (22 / 9,306), V4_gap_hold_proxy (33 / 9,460), rising_three_methods (55 / 9,460), V4_trend_reclaim (66 / 9,460), rsi_extreme (66 / 9,460). None contributes a PASS row, so none inflates the 155 distinct findings. D-266 is the binding context: the duplicate_strategies assertion flags C2 as 100% identical to all 54 other strategies, which is not 54 duplicates, it is C2 producing zero trades so every comparison is empty against empty."
kind: repair
status: PROPOSED
source: "docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md (measured)"
---


## What this is and is not

This is a repair, so `expected_edge_bps` is null. Convention 11: the edge of a
strategy that has never fired is not knowable, and unknown is not zero. Writing
a number here would put a fabricated figure into the record, and fabricated
figures get cited.

The full measured diagnosis is in
`docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md`. It re-runs the sweep's
own pipeline with per-clause counters and reproduces the graveyard's exact trade
counts for four of the nine, so the numbers below are measurements, not reads.

## The finding: 2 bugs and 3 systemic conditions, not 9 bugs

**Two genuine strategy defects.**

`rsi_extreme` requires `rsi14 < 35` AND `close > ema50`. Measured over 42,010
bars: 4,783 bars satisfy the first, 21,982 satisfy the second, and **zero
satisfy both.** RSI(14) conditional on `close > EMA50` has a hard floor at
36.26, so the threshold sits below the support of the conditional distribution.
This is category (b), unsatisfiable, not a tight threshold. One-character fix.

`C2` computes its anchor lookback as `24 * 4` BARS while meaning four DAYS. True
only for hourly bars; on 5m it reaches back 8 hours and can never find Friday.
Measured: 100% anchor failure on every sub-hourly series tested.

**Three systemic conditions produce the other seven.**

1. *Bar starvation.* `min_idx = 100` against a last-20% test slice leaves daily
   series a median of ONE scannable bar, 5,100 across 175 series, which is 5.01%
   of the daily bars on disk. This is the highest-leverage item in the list and
   it reaches well beyond these nine: it means the daily evidence behind
   "509,080 tests" is overstated by roughly 20x.

2. *The confirmation stack is a trend filter applied to every strategy.* The
   sweep never sets `apply_confirmation_stack` or `require_regime_uptrend`, so
   both default to True and every signal must satisfy `close > rising EMA50`. It
   removed 100% of V2_vwap_magnet_sessionatr, 99.5% of its control twin, 92% of
   V5_capitulation_equity's candidate days, 87% of V3_intraday_momentum_crypto
   and 82% of V4_trend_reclaim. **A mean-reversion strategy filtered through
   "price is above a rising EMA50" has not been tested and found wanting. It has
   not been tested.**

3. *Unvalidated grid and coverage assumptions.* 1h equity bars stamp on the
   hour, so V2's `[930, 945)` trigger box is permanently empty. 1h crypto bars
   stamp 23:00, so V3's 23:30 trigger is unreachable. The funding table overlaps
   the Binance price slices by 18 days. None of these raise; they produce silent
   zeros that read as verdicts.

## Why this needs a ruling before execution, not after

Items 1 and 2 change the graveyard's headline numbers. Lowering `min_idx`
multiplies the daily evidence base; turning off the confirmation stack for the
mean-reversion cohort changes what counts as a tested strategy. Both are
defensible and both are arguably corrections of a measurement error rather than
a change in method, but neither is Forge's call to make and neither should
happen quietly between two sweeps.

The specific risk: re-running with these changes will make some numbers look
better, and convention 17 says that when a metric improves after a step that
only loosened a filter, suspect the baseline before believing the result. That
is exactly what happened with `COST_FLOOR = -0.30` and the conditional-edge
false positive. Ruling first, then run, then compare against the pre-change
numbers deliberately.

## Ordering, by value per unit of work

1. `apply_confirmation_stack=False` for the mean-reversion cohort, then re-run.
   A config change that unblocks four strategies. The machinery already exists
   and is already used by `constraint_sweep.py:64` and `dispersion_gate.py:353`.
2. `min_idx` to `max(strategy.min_bars, 25)` for daily and weekly series.
3. `rsi_extreme` threshold and `C2` lookback units. One line each, one D-number
   each.
4. Delete C2's 9,042 stale rows before anyone cites them. They carry a reason
   string that no longer exists in the codebase, which means they were written
   by a pre-fix harness and C2 has never run under current code.

## The honest limit on this claim

The full sweep was not re-run, so the "would become N findings" figures in the
diagnosis are raw-signal counts, not PASS counts. A strategy that starts firing
may still fail on economics, and several probably will.

The claim is narrow and should be read narrowly: **seven of these nine were
never given the chance to lose.**
