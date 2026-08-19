---
name: "pm_settlement_resolution_ledger"
thesis: "The system cannot answer the one question every structural hypothesis in this directory depends on: what did this market actually settle at. Nothing records resolution. The only way to recover it today is to infer it from a SIBLING position on the same market and side that happened to be held into settlement, because settlement is written as exit_reason 'stop' at exit_px 0.00 and 'target' at exit_px 1.00 rather than as a resolution record. Read from db/trading.db at 2026-08-19 ~11:30 UTC: 2,216 closed positions touch 864 distinct (pair, outcome_side) market-sides, and resolution is recoverable for 325 of them, 37.6%. For 539 market-sides, 62.4%, the system holds positions whose outcome it does not know and cannot compute. A further 193 closed positions carry no recoverable outcome_side at all because they have no signal row or no features_json, so even the pair key is unavailable. The inference method itself is sound: of the 17 pairs where BOTH sides were independently recovered, 17 of 17 show exactly one side settling at 1.00 and the other at 0.00, which is the arithmetic a binary must satisfy and is a real validation rather than an assumption. But sound and unbiased are different claims, and this sample is biased in a direction that is knowable and one-way. Of the 291 market-sides where only one side was recovered, 28.5% settled at 1.00. For crypto Up/Down windows the unconditional rate for a chosen side is near 50%. The gap is a selection effect with an obvious mechanism: a position on a side that is winning gets sold early by profit_target and leaves NO settlement row, so its market becomes unrecoverable, while a position on a losing side rots to 0.00 and records one. Recovery therefore preferentially captures losses. That is not a nuisance, it is a bias that flatters every early exit in the system, and it means the counterfactual comparison 'was exiting better than holding' - the only forecast-free test available for an exit policy - is currently computed on a sample selected on the outcome it is testing. Two market-sides in the raw map carry BOTH 0.00 and 1.00, which is arithmetically impossible for one side of one binary and is an unexplained data-integrity fault that this repair would also surface. The repair is not a new feed and not a schema change to market_tape. It is to write the resolution of every market the loop touched, at window close, independent of whether any position was still open in it."
expected_edge_bps: null
kill_condition: "This is a repair and records no edge (convention 11, and the README's rule that a repair naming a number becomes a cited figure two documents later). Its success condition is a coverage measurement, not a P&L. The repair is DONE when, over 200 or more closed positions booked after the ledger lands, resolution is available from the ledger for 99% or more of the distinct (pair, outcome_side) market-sides those positions touch, measured by the same query that produced the 37.6% figure above and reported as a fraction with both numerator and denominator (convention 20). Today's baseline is 325/864 = 37.6%; anything below 95% means the ledger is missing markets rather than recording them and should be treated as FAILED and reverted rather than tuned. The repair is ALSO failed, separately and regardless of coverage, if the ledger's resolution disagrees with the sibling-inference resolution on any market-side where both are available - agreement on the overlap is the only check that the new writer is recording the same event the old inference was approximating, and a disagreement rate above 0.00 on that overlap means one of the two is wrong and the ledger cannot be trusted until it is known which. Report the 2 currently-contradictory market-sides as the first test cases. If 200 closed positions have not landed within 7 days of the ledger going live, record NOT_TESTED and requeue; do not grade a partial sample."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  0. Scope, stated first because this proposal is easy to misread as a strategy. This adds NO strategy, NO entry, NO exit and NO position. It changes no trading behaviour whatsoever. It is a write-only instrument, and its entire content is a table and the loop-level code that populates it. It is filed here rather than as a bare engineering ticket because every structural proposal in this directory is blocked on it and the blockage is not currently written down anywhere.
  1. New table, `market_resolutions`, additive, and NOT a column added to `market_tape`. Do not touch `market_tape` before ~03:28 2026-08-20: proposals 026 and 037 are mid-measurement on it and a schema change under a running measurement invalidates it. Columns: `market_slug` TEXT, `outcome_side` TEXT, `resolved_px` REAL, `resolved_ts` REAL, `window_ts` INTEGER, `source` TEXT, with a UNIQUE constraint on (`market_slug`, `outcome_side`). No default on any column and no NOT NULL on `resolved_px` - a NOT NULL DEFAULT is exactly the `fill_was_maker` mistake, where 2,253 rows were backfilled to 0 and became indistinguishable from observations, and the standing corrections name that as the reason `market_duration` must be nullable too.
  2. Writer: the loop, at window close, for every market it FETCHED in that window - not for every market it traded. This is the whole point. The 539 unrecoverable market-sides are unrecoverable precisely because recovery was conditioned on holding a position, and conditioning the RECORD on holding a position reproduces the bias the record exists to remove.
  3. `source` is mandatory on every row and takes one of exactly two values, `venue` when the resolution was read from the venue's resolution field, and `inferred_terminal_price` when it was taken from a terminal book price at or after window close. They are never pooled in any report, for the same reason maker and taker fills are never pooled (convention 32): one is an observation and the other is a reading, they have different error modes, and a mixed column silently becomes the weaker of the two.
  4. Backfill is PERMITTED but must be marked. Historical rows recovered by the sibling-inference method described in the thesis may be written with `source = 'sibling_inference_backfill'`, a third value used for nothing else, so that the 325 recoverable market-sides are not lost. They must never be counted toward the rule-2 coverage number in the kill condition, which is measured on markets fetched AFTER the ledger lands. A backfill that cannot be distinguished from an observation is not a backfill, it is contamination.
  5. Read path: one helper, `resolution_for(market_slug, outcome_side)`, returning None rather than a guess when the market is absent. Every consumer must tolerate None. Returning 0.00 for "unknown" would convert every unrecorded market into a recorded loss and would bias every downstream number in the same direction the current inference already leans.
  6. Do NOT wire any strategy to this table in the same change. A resolution record is readable at window close, which is AFTER every entry and exit decision for that window, so it cannot inform a live decision and any strategy reading it is either look-ahead or a bug. Its only consumers are `backtest/` and `agents/forge_shadow_eval.py`.
  7. Record the count of markets fetched but NOT resolved in each window, with a reason, counted (convention 20). A silent gap in this table is a missing number, and this table exists to stop exactly that.
data_requirements: |
  HAVE, verified in db/trading.db at 2026-08-19 ~11:30 UTC: `positions.pair` (the market slug), `positions.exit_px`, `positions.exit_reason`, `positions.signal_id`, and `signals.features_json.outcome_side`, which is the field that makes the side recoverable at all and is present on 2,023 of 2,216 closed positions. Those five fields are what produced every number in the thesis and they are the whole of the current, biased, method.
  HAVE, and it is the reason this repair is cheap: the loop already fetches every market in the universe each cycle, so the slug and side are in hand at window close with no new call. The only genuinely new thing needed is the resolution VALUE.
  MISSING, and it is the single open question in this proposal: whether the venue exposes a resolution field the loop can read directly at window close, or whether resolution must be taken from a terminal book price. Rule 3 is written to accommodate either and to keep them separated, so this does not block the build, but it does decide which value `source` takes and it should be settled by reading the venue response before the table is written rather than after. NOTE THE STANDING TRAP: gamma's `bestBid`/`bestAsk` read 0.63/0.64 while the live CLOB book for the same token was 0.06/0.08 three minutes from expiry. If resolution is taken from a price rather than a resolution field, it must be read from the BOOK, never from the gamma summary fields, or this ledger will record fiction with a timestamp on it.
  NOT NEEDED: the 15m keying change, the calibration tape, `market_tape.condition_id`, `complement_id`, or any part of the ~03:45 EDT 2026-08-20 restart payload. This repair is deliberately independent of all of them and could land before or after without interacting, which is the reason it is proposed now rather than queued behind them.
  NOT NEEDED: `fill_was_maker`. This instrument records what the market did, not how we filled.
markets: "Polymarket crypto Up/Down windows, all assets and all durations the discovery pass returns. Not restricted to markets the loop traded - that restriction is the defect being repaired."
kind: repair
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

## Why this might fail

The most likely failure is that it lands, coverage goes to 99%, and the
unbiased numbers it produces are duller than the biased ones. That is the
expected outcome and it is worth stating plainly, because the biased sample
currently makes the exit policy look excellent - `profit_target` appears to
sell 0.235 a share above realised value on 79 recoverable exits, and
`price_stop` 0.078 above on 231. Both of those are computed on a sample that
over-represents markets that settled 0.00 by roughly 20 points. Removing the
bias should shrink both toward zero, and the honest prior is that most of that
apparent exit skill is the selection effect and not skill. A repair whose
main result is deleting a flattering number is still worth doing; it is just
not going to feel like progress.

The second failure is the one that would waste the work. If the venue does not
expose a resolution field and terminal book prices at window close are thin,
stale or absent for the markets nobody traded - which is plausible, since the
untraded markets are disproportionately the illiquid ones - then coverage
stalls somewhere well below 99% and it stalls on exactly the subpopulation the
repair exists to reach. That would not be a neutral partial success. Coverage
that is high on liquid markets and low on illiquid ones is a NEW bias replacing
the old one, and rule 7's unresolved-count-with-reason is what would expose it.
If that happens the correct response is to report the coverage split and stop,
not to fill the gap with a guess.

Third, and this is a limit rather than a failure: this ledger fixes the
denominator, not the sample size. 864 market-sides over roughly 32 hours is a
small history, and a forecast-free exit comparison needs hundreds of clean
observations before its sign is worth anything. The ledger makes the numbers
honest. It does not make them significant, and nobody should read a clean 99%
coverage as licence to grade a 16-observation result.

## What past failure this addresses

It addresses a failure that is not attributed to any strategy, which is why
nothing has fixed it: every structural measurement this program has attempted
has been graded on a sample selected by the trading policy being graded.
Proposal 035 is the clearest case. Its thesis is that the salvage floor
censors the settlement-exit sample in one direction, and it is right about the
mechanism - all 21 recoverable salvage exits settled at 0.00, which is the
loser subset exactly as 035 predicts. But 035's own measurement of that
censoring would be computed on the same biased recovery, so the instrument
proposed to fix the censoring inherits it. See the amendment appended to 035.

Proposal 039 is blocked on this in a sharper way. It reports that `time_stop`
is the only exit in the system that loses to holding, by 0.184 a share on 16
recoverable observations. That result survives the current bias rather than
being produced by it, because the bias pushes every exit's measured performance
UP and `time_stop` still measures down - which is the reason 039 is filed at
all. But 16 is 16, and the reason it is not 45 is that 29 of the 45 time-stopped
positions are in the 62.4% whose market outcome is simply unknown. This ledger
is where those 29 observations come from.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET; the graveyard is crypto spot and perp and has no rows in
  this class. The engaged prior failure is a measurement practice rather than a
  buried strategy, so there is no honest link to supply and inventing one would
  be the fabrication the field's own comment at `agents/forge.py:174` describes.
