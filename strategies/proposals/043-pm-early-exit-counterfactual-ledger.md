---
name: "pm_early_exit_counterfactual_ledger"
thesis: "The cycle brief names the exit-reason asymmetry the headline measurement: in both databases this window, target and profit_target exits carry nearly all positive P&L (+512.62 on 155 closes in trading.db, +676.22 on 131 in survivors) while stop, price_stop and salvage_floor carry the bleed (-665.14 on 267, -837.82 on 260), and salvage_floor is the single worst line in environment B at -514.10 on 115 closes. Re-derived read-only at 2026-08-19T23:57:33Z the same shape reproduces (trading.db since the 19:30Z cutoff: salvage_floor -234.86 on 63, price_stop -224.74 on 86, stop -206.61 on 113, against target +338.06 on 55 and profit_target +160.63 on 103). But the asymmetry is CIRCULAR and carries no information about exit policy. `sell:profit_target` is the name of the exit that fires BECAUSE the position is winning and `sell:price_stop` is the name of the exit that fires BECAUSE it is losing; sorting P&L by exit reason and finding that the winning exits hold the winners recovers the definition of the exits, not a fact about them. Every book ever traded has this asymmetry. The only non-circular version of the question is the one proposal 039 wrote down for the clock exit: what price did the exit take, and what did that same market-side turn out to be worth. Until this window that comparison could only be built from sibling inference, which proposal 038 measured to be recoverable on 38.8% of market-sides and biased toward losers. It no longer has to be. The 038 ledger is LIVE in db/trading.db - `market_resolutions` holds 350 venue-sourced rows over 175 market slugs - and `positions.pair` is the market slug, so an exit joins to its own resolution directly. Measured on that join: `sell:salvage_floor` sold 1136 shares for 72.52 USD that were worth 40.00 USD at resolution, so holding instead of salvaging would have cost 32.52 USD more, and salvage is 44.8% BETTER than the hold-through the brief proposes in its place. The realised settle rate on salvaged shares is 0.0352 against a break-even equal to the mean salvage price of 0.0638. That break-even is an identity and not an estimate: a position sold at price s returns s per share, a position held returns 1.00 times the settle rate, so holding wins if and only if the settle rate exceeds the mean salvage price, and no forecast enters anywhere. The same join says every early exit in the book is selling ABOVE realised value - profit_target 0.4177 into 0.3657 (n=98, +97.58 USD), price_stop 0.1822 into 0.1498 (n=85, +52.82), salvage_floor 0.0638 into 0.0352 (n=59, +32.52), model_stop 0.2954 into 0.1935 (n=7, +12.63) - which is not exit skill and must not be read as any: it is what a book with bad entries looks like, where anything that gets you out beats riding a losing binary to zero, and it is the 91%-model diagnosis showing up in the exit column. The ledger is checked against itself rather than trusted: on the 148 positions carrying BOTH an independent settlement (`exit_px` of exactly 0.00 or 1.00) and a ledger row, the ledger disagrees on 25 of 2016 shares, 1.24%, and the disagreement is near-symmetric (15 shares called losses that settled 1.00 against 10 shares called wins that settled 0.00), for a net directional bias of 5 shares or 0.25%. Carried onto the salvage population that bias is 2.82 shares against a 32.52-share shortfall to break-even, so it is 11.5x too small to flip the sign. What this proposal builds is the standing instrument that produced those numbers, so that the comparison is a maintained measurement rather than one forge cycle's query."
expected_edge_bps: null
kill_condition: "This is an experiment and records no edge (convention 11): it is built to find out which exit dominates, not because either is believed to. It is graded per exit reason and NEVER pooled across exit reasons, never pooled across the two databases (convention 32 and the tick-4 ruling), and never pooled across fill types. It grades ONLY positions whose market-side resolution is present in `market_resolutions` with `source` = `venue`, never sibling inference. The measurement path is `backtest/settlement_coverage.py --counterfactual`, reported by `agents/forge_shadow_eval.py --db db/trading.db`. RETIRE the salvage floor and record the answer as NEGATIVE - meaning holding beats salvaging and rule 4 of `fair_value_settlement_exit.py` should be reconsidered in a separate proposal - if, over 400 or more resolution-matched `sell:salvage_floor` positions, the realised settle rate on salvaged SHARES exceeds the share-weighted mean salvage price by 0.010 or more. CONFIRM the salvage floor, and close the brief's hold-through direction for this exit, if over those same 400 the mean salvage price exceeds the realised settle rate by 0.010 or more. Between those two the result is INCONCLUSIVE and is recorded as such. The 0.010 band is not arbitrary and is not a taste: it is 4x the 0.0025 net directional bias measured in the thesis against positions with known outcomes, so a verdict either way survives the ledger being wrong at four times the rate we can currently demonstrate it is wrong at. 400 is chosen because the present matched sample is 59 and the current fill rate is 64 salvage closes in the 7.2 hours since the ledger began, or 8.9 per hour, so 400 is roughly 45 hours of loop uptime and is reachable without a schedule change. SEPARATE and mandatory: re-run the ledger self-check every time the counterfactual is reported, and if the ledger's share-weighted disagreement rate against independently-settled positions rises above 0.0500, record the counterfactual as NOT_TESTED regardless of what it says, because at that rate the instrument is measuring itself. If 400 matched salvage positions have not landed within 14 days of this instrument going live, record NOT_TESTED and requeue; do NOT grade the 59 that motivated this proposal."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  0. Scope. This changes NO entry rule, NO exit rule, NO sizing and NO gate on any strategy. It adds a reporting path over data that already exists. In particular it does NOT remove, loosen or tighten the salvage floor, and it does not act on the direction its own thesis measures - the thesis is a 59-position reading and the kill condition asks for 400. Anyone reading this proposal as authority to change `SALVAGE_FLOOR` has read it backwards.
  1. New reporting script `backtest/settlement_coverage.py --counterfactual`, beside the existing `--backfill` mode rather than inside it. The backfill WRITES to `market_resolutions`; this READS it. They must not share a code path, because a reporting run that can write its own inputs is not a measurement.
  2. The join is `market_resolutions.market_slug = positions.pair` AND `market_resolutions.outcome_side = json_extract(signals.features_json, '$.outcome_side')`, with `positions.signal_id = signals.id`. Both halves are required. `positions.pair` alone identifies the MARKET and not the SIDE, and a market has two sides that resolve oppositely, so a join on slug alone would score half the book against the wrong outcome and would do it silently.
  3. There is NO `market_slug` key in `features_json` - verified at 2026-08-19T23:5xZ by dumping the keys of a salvage signal, which carries `outcome_side` and `window_ts` but no slug. The first attempt at this join used one and returned 0 matched rows out of 195. A silent zero is a missing number (convention 20): the script MUST fail loudly when the matched count is zero rather than reporting an empty counterfactual as a null result.
  4. Report per exit reason and never pooled across them, with n, share count, proceeds `sum(exit_px * qty)`, realised value `sum(qty where resolved_px >= 1.0)`, and the delta. Report per share as well as in dollars. The two must be shown together because the dollar delta is dominated by whichever exit happens to be most frequent and the per-share figure is the one the kill condition reads.
  5. Report the MATCH RATE alongside every figure, as numerator and denominator, per exit reason. At the snapshot 59 of 64 ledger-era salvage closes matched, which is 92.2% and is nothing like proposal 038's historical 38.8%, but the rate is a property of how long the ledger has been running and it will not stay there. A counterfactual computed on a matched subset whose match rate is not reported is proposal 041's censoring error wearing a different hat.
  6. Mandatory self-check, run and reported on every invocation, not on request: restrict to positions whose `exit_px` is exactly 0.00 or 1.00, which are settlements the position recorded independently of the ledger, and report the share-weighted rate at which the ledger disagrees, split by direction. At the snapshot this is 25 of 2016 shares, 1.24%, split 15 against 10. This is the instrument's own error bar and every counterfactual figure is quoted with it or is not quoted.
  7. The break-even is stated in every report as the identity it is: holding beats exiting at price s if and only if the realised settle rate on those shares exceeds the share-weighted mean of s. No probability model, no fair value, no calibration is used anywhere in this measurement, and none may be added to it. This is what makes the comparison admissible under D-342 R5 - the payoff difference is an identity in the two recorded prices, not the output of a forecaster.
  8. Do NOT wire any strategy to read this output. Resolution is knowable only after the window closes, so a strategy consuming it is look-ahead by construction. Consumers are `backtest/` and `agents/forge_shadow_eval.py` only. Same rule as proposal 038 rule 6 and proposal 042 rule 7, and for the same reason.
  9. Report `sell:mean_reverted` and `sell:time_stop` but mark both NOT GRADEABLE at n=2 and n=1. The time_stop row is the one that matters for proposal 039 and it currently reads 20 shares sold for 3.18 that were worth 0.00, which runs OPPOSITE to 039's sibling-inferred +0.184 per share. One position is not evidence against 039 and must not be reported as if it were; it is recorded because 039's own kill condition demands ledger-sourced observations and this is the first one that exists.
  10. Environment B is EXCLUDED from this instrument until it has a ledger. `db/trading-survivors.db` has no `market_resolutions` table at all - verified at the snapshot - so its 433 salvage closes and -1814.63 USD cannot be counterfactualled by any method, and the larger salvage population is the one we cannot measure. Do NOT satisfy this by pooling environment B's salvage rows against environment A's ledger: the two books trade different strategies on overlapping markets and a resolution row is per market-side, so the join would silently succeed and produce a number that means nothing.
data_requirements: |
  HAVE, verified read-only in db/trading.db at 2026-08-19T23:57:33Z: `market_resolutions` exists and holds 350 rows over 175 distinct market slugs, `source` = `venue`, columns `market_slug`, `outcome_side`, `resolved_px`, `resolved_ts`, `window_ts`. This is proposal 038 landed and live; it did not exist when 038 was written and CLAUDE.md still carried "the table does NOT exist in the live db" as of this cycle.
  HAVE: `positions.pair` is the market slug in the exact form `market_resolutions.market_slug` uses (`btc-updown-5m-1787183400`), `positions.exit_px`, `qty`, `entry_px`, `pnl_net`, `exit_reason`, `opened_ts`, `closed_ts`, `signal_id`, and `signals.features_json.outcome_side`. Every figure in the thesis came from these and nothing else.
  HAVE: the self-check population. 148 positions carry both an independent settlement and a ledger row, which is what makes rule 6 computable today rather than aspirational.
  MISSING, and it bounds the verdict rather than blocking it: resolution for the 5 of 64 ledger-era salvage closes that did not match, and for all 132 salvage closes that predate the ledger's first window (`window_ts` 1787169600). The pre-ledger population cannot be recovered except by proposal 038's `--backfill`, which is unrun and is Raven's call, and which recovers a loser-biased 38.8% - so backfilled rows must be reported as a SEPARATE arm and never merged into the venue-sourced one. The kill condition grades the venue arm only.
  MISSING, blocking for environment B only: `market_resolutions` in `db/trading-survivors.db`. See rule 10. Creating it is not this proposal's lane and this proposal does not request it; it records that environment B's salvage population is unmeasurable until someone does.
  NOT NEEDED: the 15m keying change, `market_duration`, the calibration tape, `market_tape` in any form. This instrument reads settled outcomes and recorded exit prices, both of which are already stored per position.
  NOT NEEDED: the rebate, the taker fee schedule, or proposal 040's regrade. The counterfactual compares two exits on the SAME position, and the entry cost is common to both arms and cancels. A fee applies to the salvage sale and not to the redemption, which biases the comparison AGAINST salvage by roughly one taker fee per share - so under proposal 040's peaked schedule salvage would look worse than measured here, and the thesis's finding that salvage still wins by 44.8% is stated before that correction rather than after it. Recording this so the next reader does not discover it as a defect: it is a known, signed, quantifiable gap and it runs against the result rather than producing it.
markets: "Polymarket crypto Up/Down 5m windows, db/trading.db only. `sell:salvage_floor` is exclusively PM_fair_value_settlement_exit in both databases (196 of 196 lifetime closes in trading.db, 433 of 433 in survivors), so the graded arm is that one strategy; the other exit reasons are reported across all strategies as context. Explicitly NOT db/trading-survivors.db, which has no resolution ledger."
kind: experiment
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

> **This proposal REFUSES the direction the cycle brief suggested.** The brief
> proposed testing whether holding losers to resolution beats the salvage exit,
> on the grounds that `salvage_floor` is the single worst P&L line in
> environment B. Measured against the venue resolution ledger, holding is
> **44.8% worse**: 1136 salvaged shares sold for 72.52 USD were worth 40.00 USD
> at resolution. The proposal is filed to make that comparison standing and to
> grade it at n = 400, not to endorse the hold-through.

> **The 59-position reading is NOT a result.** It is the motivation. The kill
> condition demands 400 matched positions and explicitly forbids grading these
> 59. Anyone carrying the -32.52 USD forward as a finding is carrying a sample
> one seventh the size of the test that would decide it.

## The circularity, stated once, because it will recur

An exit named for the condition that triggers it cannot be evaluated by the
P&L of the positions it triggered on. `sell:profit_target` fires when a
position is up and `sell:price_stop` fires when it is down, so a table of P&L
by exit reason is a table of the exits' own definitions. The brief's headline
figures - +512.62 on the winning exits against -665.14 on the losing ones -
would appear in an identical shape in a book that was making money, in a book
that was losing money, and in a book of coin flips. They cannot distinguish
those cases, which is another way of saying they carry no information about
exit policy.

This matters beyond this cycle because the circular reading has an action
attached to it: "the losing exits hold the losses, so change the losing exits."
That inference is invalid, and in this case it is also measurably backwards.
Every early exit in the book is selling above what the position turned out to
be worth. The exits are not where the money is going.

Where it is going is the entries, and the counterfactual says so from a new
direction. Salvaged positions were bought at a mean of 0.2608 and were worth
0.0352 at resolution. The salvage exit recovered 0.0638 of that. The exit
policy is arguing over the last six cents of a twenty-six cent mistake.

## Why this might fail

The strongest objection is that the matched subset is selected on time rather
than at random, and time is not neutral here. The ledger's first window is
1787169600 and the loop restarted at 20:06Z with a fresh 1000.00 reset, so
every matched position comes from the post-restart book. If the restarted loop
draws different markets, or if the venue's own conditions changed across the
16:17-to-20:06 gap, then the counterfactual is a measurement of four hours and
is being read as a measurement of the strategy. The kill condition's 400 is a
partial answer, since 45 hours of uptime spans many more conditions than four
hours does, but it is not a complete one and no sample size fixes a regime
change. The match-rate reporting in rule 5 is what would surface it.

The second failure is subtler and I want it on the record because it cuts at
the identity the whole proposal rests on. The break-even comparison is exact
for a position that is actually held to resolution with no further decision.
But the counterfactual arm is not "hold" in the sense of doing nothing - it is
"decline to salvage AND then face every subsequent exit rule that is still
armed", and `fair_value_settlement_exit` still carries a profit target. A
position that declines salvage at 0.07 and later recovers to 0.40 would hit
`profit_target`, not resolution. So the arm I have priced at 1.00-times-the-
settle-rate is the floor of the hold arm, not the hold arm, and the true
counterfactual is weakly better than 40.00 USD. It is bounded above by the
salvage population's own recovery rate, which is not measured here and which
rule 4 does not currently compute. If that recovery rate is large the 32.52 USD
gap narrows. I judge it small - these are positions whose bid reached 0.10 with
minutes left in a 5-minute window - but "I judge it small" is not a
measurement, and a reader should treat the 44.8% as an upper bound on salvage's
advantage rather than a point estimate of it.

Third, the instrument may simply not accumulate. The ledger is seven hours old,
environment B has no ledger at all, and the strategy that owns every single
salvage close is the same PM_fair_value_settlement_exit family whose kill is
already pending under proposal 041. If that family is retired before 400
matched positions land, this experiment records NOT_TESTED and the salvage
question dies with its strategy - which is a legitimate outcome and is why
rule 0 is emphatic that this proposal is not authority to change the floor.

Fourth, the near-symmetric 1.24% ledger disagreement is measured on 148
positions and 5 disagreeing ones. Five is not enough to characterise a
direction. I have used it as a 0.0025 net bias and built a 4x margin into the
kill threshold for exactly that reason, but if the true disagreement is
asymmetric in the direction that understates wins, and larger than measured,
the margin is the only thing between this instrument and a wrong verdict.
Rule 6 makes the self-check mandatory on every run rather than a one-off so
that the error bar is re-measured as the sample grows rather than inherited
from this document.

## What past failure this addresses

It addresses proposal 041's finding directly. 041 recorded the
`PM_fair_value_settlement_exit` kill as undecidable because most of that
strategy's closes are `sell:salvage_floor` early exits that never settled, so
the settlement frequency could only be bracketed and 0.30 sat inside the
bracket. Re-derived at this snapshot with 813 more trades in the system, the
bracket is [0.1711, 0.3575] in trading.db on 179 resolved of 374 closes, and
[0.2206, 0.5725] in survivors on 269 resolved of 698. 0.30 is still inside both
and the censoring rate did not fall - 52.1% and 61.5% against 041's 53.0% and
61.9%. That is 041's own prediction confirmed: it argued the question stays
undecidable at any n because the censoring rate does not fall with n, and a
window that added 811 closes moved the censoring rate by less than a point.
Environment B has now passed 034's 200-resolved bar at 269 and the kill still
does not fire, because the point estimate 0.5725 is above 0.30 rather than
below it.

This proposal is the instrument that dissolves that undecidability rather than
re-litigating it. The censored positions are censored because they were
salvaged, and the ledger now tells us what those censored positions were worth.
A settlement frequency that could only be bracketed can, on the matched subset,
be computed - not by assuming the censored rows, which is what the honest
bracket refused to do, but by looking them up.

It also addresses the failure named in proposal 038's own thesis: resolution
was recoverable for 345 of 889 market-sides, 38.8%, and biased toward losers
because winners get sold early by `profit_target`. That bias is the reason
039's thesis figures are contaminated and the reason 039 made 038 a blocking
precondition. On venue-sourced rows the bias is 0.25% of shares rather than
roughly 20 points, which is the difference between an instrument and an
anecdote, and it is the single largest thing that changed for the measurement
family this cycle.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET; the graveyard is crypto spot and perp and has no rows in
  this class. The engaged prior failures are proposals 038, 039 and 041, cited
  above by measurement rather than by graveyard id.
