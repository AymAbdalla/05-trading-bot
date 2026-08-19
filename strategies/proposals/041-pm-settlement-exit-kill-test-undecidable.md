---
name: "pm_settlement_exit_kill_test_undecidable"
thesis: "PM_fair_value_settlement_exit was flagged this cycle as having breached both of its kill conditions, on the reading that its win rate of 0.119 in db/trading.db and 0.185 in db/trading-survivors.db is far below D-327's 0.30 settlement-frequency threshold. I re-derived it and the flag does not survive. Win rate and settlement frequency are two different quantities in this strategy, and the gap between them is more than half the population. Read read-only on 2026-08-19: db/trading.db holds 249 lifetime closes of this strategy, of which 117 reached settlement (exit_px exactly 0.00 or 1.00, recorded as exit_reason 'stop' and 'target') and 132 - 53.0% - exited early as sell:salvage_floor. db/trading-survivors.db holds 506, of which 193 settled and 313 - 61.9% - salvaged. A salvage exit is not a settlement that lost. It is a position removed from the sample before the event the kill condition measures, and counting it as a failed settlement is what produces the 0.119. Measured as written, on the positions that actually resolved, settlement frequency is 45/117 = 0.3846 in db/trading.db and 109/193 = 0.5648 in db/trading-survivors.db. Both are ABOVE 0.30, not far below it. And 034's own kill - 'retire if net P&L per resolved position is below 0.00 over 200 or more resolved positions' - fails to fire twice over: the resolved counts are 117 and 193, so neither database has reached the 200 the condition requires, and the sign is positive anyway at +3.0790 and +5.2413 per resolved position. The two databases must not be pooled to reach 200; environment B runs a different strategy whitelist and its results are never crossed with the main loop's. So neither kill condition is breached, and I decline to record a kill that the evidence does not support. What I will NOT do is record an acquittal either, because the reason the two readings disagree is a censoring that makes both of them untrustworthy. The salvage exit does not remove positions at random: it fires when a position has collapsed, so it strips the sample of exactly the entries most likely to settle at 0.00, which biases the surviving settlement frequency UP. That is proposal 035's thesis and the numbers here are its confirmation. On the settled subset of db/trading.db the strategy paid 0.2195 per share and realised 0.3765, an apparent +0.1570 per share with a one-sided binomial p of 3.9e-05; on the salvaged subset it paid 0.2513 and realised 0.0689, -0.1824 per share; pooled across all 249 closes it paid 0.2362 and realised 0.2150, -0.0213 per share, and the strategy is down 102.71 USD. In db/trading-survivors.db the same three readings are +0.2699, -0.2087 and -0.0268 per share on 1,011.56, -1,275.37 and -263.81 USD. The overwhelming significance of the settled subset is not evidence that the selector works; it is the signature of a sample selected on the outcome being tested, and a p of 3.9e-05 on a censored sample is a reason to distrust the sample rather than to believe the number. The honest bracket on settlement frequency runs from the settled-subset reading down to the case where every censored position would have settled at 0.00: 0.1807 to 0.3846 in db/trading.db, 0.2154 to 0.5648 in db/trading-survivors.db. D-327's 0.30 threshold sits INSIDE both brackets. The kill test is not passed and not failed. It is undecidable on the instrument that exists, and it will stay undecidable no matter how many more positions this arm books, because the censoring rate does not fall with n. One further defect in the test itself: D-327 states the threshold 'against a mean entry ask of 0.33', and the measured mean entry price is 0.2402 in db/trading.db and 0.2883 in db/trading-survivors.db. The reference price the threshold was derived against is not the price the strategy is paying, so even a decidable version of this test would be comparing a frequency to a breakeven that is roughly 4 to 9 points too high."
expected_edge_bps: null
kill_condition: "This is a governance record and records no edge (convention 11). It asserts one falsifiable claim, and the claim is a NEGATIVE: that neither of PM_fair_value_settlement_exit's kill conditions can currently fire, in either direction, on either database. This record is WRONG and must be withdrawn if a recomputation over the same rows by `agents/forge_shadow_eval.py --db db/trading.db` and `--db db/trading-survivors.db`, reported separately and never pooled, produces EITHER a net P&L per resolved position below 0.00 on a database holding 200 or more resolved positions, OR a settlement-frequency bracket whose UPPER bound - the settled-subset reading - falls below 0.30. Either result decides the test and this record's central claim is then false. The record is SUPERSEDED, and the underlying question becomes decidable, when censoring on some arm of this family falls below 0.05 of closes over 200 or more closes, which is the only condition under which the settled subset and the population are the same sample. Two named paths reach that: proposal 035's uncensored arm, which removes the salvage floor by construction and is written but NOT wired, and proposal 038's settlement ledger, which supplies the resolution of the markets the salvaged positions left, collapsing the bracket from below without changing any trading behaviour. 038 is the cheaper of the two and it is already landed in code. UNTIL one of those lands, the correct status of the D-327 settlement-frequency test is NOT_TESTED, never negative and never positive (convention 11: an unreadable state is not an empty one). If neither path has landed within 14 days, this record is re-derived rather than aged out; a stale undecidable is still undecidable."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  0. Scope. This adds NO strategy, NO entry, NO exit, NO position and NO code. It changes nothing in any running loop and could not, since both loops snapshot source at import (convention 13). It is a record of a measurement and of a refusal, filed because a kill recommendation was made this cycle on a number that does not measure what the kill condition names, and an unrecorded refusal becomes a repeated recommendation.
  1. RECOMMENDATION, stated plainly: do NOT retire PM_fair_value_settlement_exit on this evidence, and do NOT record it as vindicated either. Its status is NOT_TESTED against D-327's threshold and BELOW-N against 034's own. Anyone retiring it should do so on the pooled per-share reading of -0.0213 and -0.0268, which is a real negative and is the honest aggregate, while stating explicitly that this is a different and weaker basis than the kill condition that was written - retiring on a number the condition does not name is a decision, not a test firing.
  2. The distinction that produced this record, written down so it is not lost: WIN RATE counts `pnl_net > 0` over all closes. SETTLEMENT FREQUENCY counts `exit_px = 1.00` over closes that reached settlement. For a strategy that exits 53% to 62% of its positions early, these differ by a factor of three, and D-327's kill names the second. Any future report of this family states which one it is reporting, with numerator and denominator (convention 20), and never the bare rate.
  3. The censored fraction is REPORTED, always, alongside any settlement-frequency figure from this family, as its own numerator and denominator: 132/249 and 313/506 as of this reading. A settlement frequency quoted without its censoring rate is not interpretable and should be treated as unsourced.
  4. The bracket is the reporting format, not the point estimate. Report [lower, upper] where lower assumes every censored position settles at 0.00 and upper is the settled-subset reading. Where a threshold sits inside the bracket, the answer is NOT_TESTED and is written as NOT_TESTED. This is the same discipline 037 got over the complement result and it exists for the same reason.
  5. Do NOT pool db/trading.db and db/trading-survivors.db to reach 034's 200-resolved bar. Pooling gets to 310 resolved and would let the condition fire, which is precisely why it must not be done: environment B runs a different strategy whitelist against a different book, and the standing rule is that its results are never crossed with the main loop's. A kill condition satisfied only by crossing that line is not satisfied.
  6. Carry the fee interaction from proposal 040, because it changes which half of this strategy is expensive. This family settles 47% and 38% of its closes and round-trips the rest through the salvage floor, so under a dynamic taker fee it would pay the tax ONCE on the settled half and TWICE on the salvaged half. That is a reason the salvage floor gets more costly under the new regime, not less, and it should be priced into 035's arm before it is wired rather than discovered afterwards.
  7. This record does not reopen the fair_value family's TESTED_FAILED verdict and must not be cited as doing so. That verdict is about PM_fair_value_arb and its variants, which are a different strategy with a different exit policy and a 1% settlement share. Nothing here bears on it.
data_requirements: |
  HAVE, verified read-only in db/trading.db and db/trading-survivors.db on 2026-08-19: `positions.strategy_id`, `positions.entry_px`, `positions.exit_px`, `positions.qty`, `positions.pnl_net`, `positions.exit_reason`, `positions.closed_ts`. Those seven fields produced every number above. The settlement test is `exit_px` exactly 0.00 or 1.00, which is exact rather than approximate here: the three exit_reason values on this strategy are sell:salvage_floor (132 and 313), stop (72 and 84) and target (45 and 109), and stop plus target equals the settled count exactly in both databases, so the classification has no residual.
  HAVE: the censoring is directly observable and needs no inference. It is a distinct exit_reason on a distinct row.
  MISSING, and it is the entire reason this record says NOT_TESTED instead of a number: the resolution of the markets the 132 and 313 salvaged positions were in. Those positions left before their market settled, and nothing currently records what the market did afterwards. That is exactly the gap proposal 038's `market_resolutions` table exists to close, and it is why 038 is named in the kill condition as a path to decidability rather than as a nice-to-have. Note that 038's table does NOT yet exist in the live db/trading.db - `ensure_schema` creates it at the next restart - so this cannot be resolved by a query today.
  MISSING, non-blocking: whether the salvage floor's threshold is itself the tunable that matters. This record deliberately does not ask; it is a report on a test, not a proposal to change an exit. A salvage-threshold proposal would be a separate filing and would need 038 landed first for the same reason 039 does.
  NOT NEEDED: `fill_was_maker`. Every position in this family reads fill_was_maker = 0, and rows opened before 2026-08-19 07:28:34 UTC carry a backfilled zero rather than an observation, so the column is neither informative nor trustworthy here. The family is taker by construction and the record makes no maker claim.
  NOT NEEDED: the 15m keying change, `market_duration`, the calibration tape. This family trades 5m windows and every figure above is from 5m closes.
markets: "Polymarket crypto Up/Down 5m windows. PM_fair_value_settlement_exit only, in both db/trading.db and db/trading-survivors.db, reported separately."
kind: governance
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning, contradicts_cycle_brief, validator_kind_not_registered"
---

> **This record CONTRADICTS the kill recommendation it was asked to write up.**
> The cycle brief flagged both of this strategy's kill conditions as breached on
> a win rate of 0.119 / 0.185 against a 0.30 threshold. Measured as the
> conditions are written, neither fires. The 0.119 and 0.185 are correct
> numbers - I reproduced them exactly - but they are win rates over all closes,
> and the threshold is a settlement frequency over resolved closes. Please read
> the bracket in the thesis before citing either figure.

## Why this might fail

The most likely way this record is wrong is that it is too clever by half and
the family really is dead. The pooled per-share edge is negative in both
databases, the strategy is down 102.71 and 263.81 USD, and a governance note
explaining why a losing strategy's kill condition has not technically fired can
easily be a sophisticated way of not killing something. I have tried to guard
that by making rule 1 explicit: retiring it on the pooled reading is a
defensible decision and I say so. What I am refusing is narrower - recording
that a NAMED THRESHOLD was crossed when the measurement says it was not,
because a kill condition that fires on a number it did not name stops being a
kill condition and becomes a rationalisation, and every proposal in this
directory depends on that not happening.

The second failure is that my bracket's lower bound is too generous. Assuming
every censored position would have settled at 0.00 is the worst case, and the
worst case is not the expected case - salvage fires at a floor near 0.065 per
share, and a position trading at 6 cents settles at 1.00 sometimes. But it is
also not obviously conservative in the direction I need: if the true censored
settlement rate is anywhere near the observed salvage exit price, the bracket
collapses toward the low end and D-327's threshold clears it. I cannot resolve
that without 038, which is the whole argument.

Third, a specific caution about the +0.1570 and +0.2699 per-share readings on
the settled subsets. Those look like a working selector and they are the most
quotable numbers in this document. They are almost certainly not a working
selector. They are what a sample looks like after the losers have been removed
from it by a rule that removes losers, and the p of 3.9e-05 measures how
thoroughly the removal happened rather than how good the entries were. If one
sentence from this record gets carried into another document, it should be that
one and not the number it warns about.

Fourth, the two databases agreeing is weaker evidence than it looks. They run
different strategy whitelists over the same venue at the same time, so their
samples are not independent draws - they are two views of one market session.
Both showing the same censoring pattern tells us the pattern is not a
db/trading.db artefact. It does not double the sample.

## What past failure this addresses

It addresses the failure proposal 035 named and could not fix: an instrument
proposed to measure a censoring, whose own measurement would be computed on the
censored sample. This record is the first time that circularity has been
measured rather than asserted. 035 argued from 5 closed positions with 60%
censoring that the bias existed; there are now 249 and 506 closes with 53.0%
and 61.9% censoring, and the direction of the bias is exactly as 035 predicted.
That is a confirmation of 035's mechanism and an argument for wiring its arm,
which remains written and unwired.

It also addresses a governance failure with no strategy attached: this program
writes kill conditions with numbers in them, which is convention 6 working, and
then evaluates them against whatever number is nearest to hand at review time.
D-327's condition named a settlement frequency and a reference ask of 0.33.
Neither was measured this cycle before the kill was recommended, and the
reference ask turns out to be wrong by 4 to 9 points against the strategy's
actual mean entry. A kill condition nobody re-derives is a kill condition that
fires on vibes, and convention 25 already says a pass count in a document is a
claim - so is a win rate in a brief.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET; the graveyard is crypto spot and perp and has no rows in
  this class.
- **contradicts_cycle_brief**: this record's conclusion is the opposite of the
  action the 2026-08-19 tick-4 cycle brief requested. The brief asked for the
  kill to be recorded; the measurement says it cannot fire. Flagged rather than
  silently resolved, because a reasoner quietly declining an instruction it was
  given is worse than one that declines it in writing. Raven's call.
- **validator_kind_not_registered**: `kind: governance` is the value the cycle
  brief names, and it is NOT in `agents/forge.py:208`, where
  `KINDS = ('edge_hypothesis', 'combination', 'repair', 'experiment')`. Passed
  through `forge.py:491` this proposal would be refused `unknown_kind`, and
  since `governance` is also absent from `NULL_EDGE_KINDS` at `:209` it would
  be refused a second time for a null `expected_edge_bps`. Nothing refuses it
  today because it is a hand-written file rather than a generated candidate,
  so this is a latent conflict rather than a live one. It is NOT resolved by
  relabelling this proposal `repair`: it repairs nothing, changes no code and
  changes no behaviour, and picking a registered kind that does not describe
  the artefact to satisfy a tuple is the same move this record objects to in
  its own subject matter. Raven's call, and it is a one-line change either
  way - add `governance` to both tuples, or rule the kind out and tell the
  reasoner what governance records should be filed as.
