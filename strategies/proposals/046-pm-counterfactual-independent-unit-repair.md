---
name: "pm_counterfactual_independent_unit_repair"
thesis: "Proposal 043's counterfactual is the right instrument asking the right question, and its kill condition cannot deliver a trustworthy answer at the sample size it names, because it treats the SHARE as the unit of independence when the unit is the MARKET-SIDE. Measured this session: restrict the 043 join to matched `sell:salvage_floor` positions and group them by `(market_slug, outcome_side)`, and every cluster's settle rate takes the value 0.0 or 1.0 and nothing between - checked, the set of observed cluster-level rates is exactly {0.0, 1.0} in both databases. That is not a coincidence to be noted, it is a definition: a market-side resolves once, so every share of every position keyed to that market-side wins together or loses together. There are 144 matched salvage positions in db/trading.db carrying 2,754 shares across 123 distinct market-sides, and 127 positions carrying 2,481 shares across 107 market-sides in db/trading-survivors.db - so 22.4 and 23.2 shares per independent draw. A standard error computed per share understates the true one by sqrt(22.4) = 4.7x and sqrt(23.2) = 4.8x. Carry that to the bar 043 actually names. At 400 matched positions, at the observed 1.17 positions per market-side, the sample is ~342 independent draws, and the standard error on a settle rate of 0.0588 is 0.0127; in survivors, ~337 draws at 0.0717 gives 0.0141. Proposal 043's kill band is 0.010. So the band is 0.79 standard errors wide in environment A and 0.71 in environment B: NARROWER THAN ONE SIGMA of the statistic it grades. A verdict rendered against it would return CONFIRM or RETIRE on noise roughly half the time under a true null, and 043 rule 0's whole purpose - that nothing about the salvage counterfactual is readable before the bar - would be satisfied in form while being violated in substance, because passing the bar would not make the reading mean anything. This is not a hypothetical failure and the evidence is already on the record: the headline has now printed +0.0286/share at 59 matched, -0.0014 at 69, and +0.0063 at 143, and D-354 R3 recorded the first two flips as a caution without diagnosing the cause. Re-deriving the cumulative delta position by position this session reproduces those exact readings and shows the full walk - +0.0620 at n=25, +0.0422 at 50, +0.0283 at 59, -0.0014 at 69, -0.0140 at 100, -0.0022 at 126, +0.0063 at 143 - a range of 0.076 across nested subsamples of one book, which is 3.6 times the 0.0212 cluster-level standard error at that size and is exactly what a statistic with no signal and an underestimated error bar looks like. The two books currently disagree in sign (+0.0066 against -0.0022) and each book's own halves disagree in sign in survivors (-0.0524 early against +0.0476 late, split at the median opened_ts). Mean exit price meanwhile is stable to four decimals across those halves in environment A, 0.0653 against 0.0655, so the instability lives entirely in the realised settle rate, which is the clustered quantity. The root cause is traceable to one sentence in 043's own kill condition: the 0.010 band is justified there as '4x the 0.0025 net directional bias measured in the thesis', which is a budget for LEDGER MEASUREMENT ERROR and contains no allowance for SAMPLING ERROR at all. Two independent error sources, one of them budgeted. This proposal repairs the instrument's reporting so the second one is always visible, and deliberately does NOT re-size the band, because D-354 R2 already ruled that the band is not re-sized mid-experiment and that ruling should hold for a reason discovered later just as it held for the bias moving 0.0025 to 0.0043."
expected_edge_bps: null
kill_condition: "This is a repair and records no edge (convention 11). It changes no strategy, no threshold and no verdict that has already been rendered; it changes what the instrument PRINTS beside its verdict. The repair is DONE when `backtest/settlement_coverage.py --counterfactual` reports, for every exit reason and not only the graded one, (a) the count of distinct `(market_slug, outcome_side)` clusters alongside the existing n and share count, (b) the mean positions-per-cluster, and (c) the cluster-level standard error on the realised settle rate, computed as sqrt(p*(1-p)/clusters) where p is the share-weighted settle rate and `clusters` is (a) - and when the `VERDICT` line refuses to return anything other than NOT_TESTED while the absolute delta per share is below 3 times that standard error, REGARDLESS of whether the 400-matched bar has been passed. The 400-position bar stays exactly as 043 wrote it and becomes a NECESSARY rather than a sufficient condition: both must hold. The repair is measurably WRONG and must be rolled back if either of two checks fails on live output - if the reported cluster count ever EXCEEDS the matched position count for the same exit reason, which would mean the grouping key is inverted and each row is minting its own cluster, or if the printed standard error differs from sqrt(p*(1-p)/clusters) recomputed from the tool's own printed p and cluster count by more than 1e-6. Both are checkable from one invocation's stdout with no database access. SEPARATELY and NOT part of this repair: the question of what the band SHOULD be, and whether the bar should move from 400 positions to the ~5,800 (environment A) or ~7,100 (environment B) that would make the existing 0.010 band a 3-sigma test at the observed cluster ratio, is REFERRED TO RAVEN and is not decided here. D-354 R2 refused to re-size the band mid-experiment and this proposal does not ask for that ruling to be reversed; it asks that the sigma be printed so that whatever verdict eventually lands is read next to its own error bar instead of instead of it. If the repair is not implemented within 14 days, record NOT_TESTED and requeue; 043's verdict stays NOT_TESTED in the meantime on its own rule 0, so nothing is unblocked by the delay and nothing is graded in the gap."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  0. Scope. This changes NO entry rule, NO exit rule, NO sizing, NO gate, and NO strategy parameter. It does not touch `SALVAGE_FLOOR`. It does not grade the salvage counterfactual, does not carry the +0.0066 or the -0.0022 forward as findings, and does not weaken 043 rule 0 - it strengthens it, by adding a second condition a verdict must clear before it may be printed at all.
  1. The grouping key is `(market_slug, outcome_side)` and BOTH halves are required, exactly as 043 rule 2 requires them for the join itself. A market has two sides that resolve oppositely, so clustering on the slug alone would merge two anti-correlated outcomes into one draw and would understate the cluster count - producing an error bar that is too small in the opposite direction from the one this repair exists to fix.
  2. Report the cluster count for EVERY exit reason, not only `sell:salvage_floor`. The context rows are already printed with per-share deltas and a reader compares them; `sell:model_stop` currently shows +0.2165/share on 15 matched positions in trading.db and +0.2655 on 14 in survivors, which are the largest deltas in the table and the least supported. They carry a NOT GRADEABLE label today. They should also carry a number that shows how little supports them.
  3. Do NOT replace the share-weighted figures with cluster-weighted ones. The share weighting is correct for the ECONOMICS - the money at stake really is proportional to shares, and the break-even identity in 043 rule 7 is a share-weighted statement. What is wrong is using the share count as the sample size for an ERROR BAR. Report the share-weighted point estimate and the cluster-based standard error together; they answer different questions and neither substitutes for the other.
  4. Do NOT re-size `KILL_BAND` (0.010) or `KILL_MIN_MATCHED` (400) in this repair. Both stay. The gate this repair adds is additive and sits beside them. Changing a live experiment's decision threshold from inside a repair, on the basis of an analysis authored in the same session, is how an instrument gets fitted to the answer its author expects - and D-354 R2 already refused a band change on better-motivated grounds than these.
  5. Apply the same cluster correction to the rule 6 SELF-CHECK, which has the identical defect and is currently the instrument's stated error bar. It reports a share-weighted disagreement rate - 75 of 4,101 shares, 0.0183, over 307 positions in trading.db at this snapshot, and 0.0000 over 106 positions in survivors - and those shares cluster by market-side exactly as the graded ones do. The self-check's 0.0500 threshold is not re-sized either; it gets the same treatment as rule 4, a printed cluster count and sigma beside it.
  6. Do NOT wire any strategy to read this output, and do not let the added sigma become an entry or exit input. Same rule as 043 rule 8, 038 rule 6 and 042 rule 7, for the same reason: resolution is knowable only after the window closes. Consumers are `backtest/` and `agents/forge_shadow_eval.py` only.
  7. The environment A and environment B arms are computed and printed SEPARATELY, each against its own `--db`, and their cluster counts are never summed (convention 32, 043 rule 10 as amended by D-354 R1). Their current sign disagreement is the clearest available demonstration of why: pooling them would produce a delta near zero with a falsely narrow error bar built from 5,235 shares that are really ~230 draws.
data_requirements: |
  HAVE, verified read-only in both databases at 2026-08-20T04:2xZ: everything this repair needs is already inside the join 043 rule 2 defines. `market_resolutions.market_slug`, `market_resolutions.outcome_side`, `positions.pair`, `positions.qty`, `positions.exit_px`, `positions.exit_reason`, `positions.signal_id` and `signals.features_json.outcome_side`. The cluster count is a `COUNT(DISTINCT ...)` over the rows the counterfactual already walks; no new query shape, no new table, no new feed, and no second pass over the database.
  HAVE: the numbers that motivate it, re-derived this session and quoted with their timestamps because both databases are LIVE UNDER READ and moved during the session - the counterfactual tool reported 143 matched salvage positions in trading.db and 126 in survivors, and a direct re-query minutes later returned 144 and 127, with ledger rows moving 780 to 786 and 372 to 390. Every figure here is point-in-time and is stated as such (convention 25).
  HAVE: the cluster census. trading.db 144 matched salvage positions / 2,754 shares / 123 market-sides / 22.4 shares per cluster / settle rate 0.0588 / delta +0.0066 per share. survivors 127 / 2,481 / 107 / 23.2 / 0.0717 / -0.0022 per share. Cluster-level settle rates observed: exactly {0.0, 1.0} in both.
  MISSING, and it BOUNDS the repair rather than blocking it: any correction for correlation BETWEEN market-sides. Two different 5m windows on the same asset minutes apart are not independent draws either, and neither are the Up and Down sides of the same window, which are perfectly anti-correlated. This repair takes the market-side as the unit and therefore still OVERSTATES the effective sample - so every sigma it prints is a LOWER BOUND on the true one, and the band-versus-sigma comparison in the thesis is conservative in the direction that favours 043's existing kill. Stating the direction of the residual error is the point: it cannot rescue the 0.010 band, only worsen its position.
  MISSING, non-blocking: a within-cluster share-weight correction for positions of unequal size. Positions per cluster are 1.17 and 1.19, so clusters are very nearly single positions and the refinement would move the sigma by little. It is named so a future session does not rediscover it as a defect.
  NOT NEEDED: `market_tape`, the calibration tape, `market_duration`, the 15m keying change, proposal 038's `--backfill`, the taker fee schedule, or any TWAP conditioning (see proposal 045, which measured that the TWAP contrast does not exist in these books).
markets: "Polymarket crypto Up/Down 5m and 15m windows - the population proposal 043 already grades. `sell:salvage_floor` remains exclusively PM_fair_value_settlement_exit in both databases. Each database is repaired and reported as its OWN arm on its own `--db` and the two are NEVER pooled (convention 32)."
kind: repair
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

> **This repair does not overturn proposal 043 and does not grade it.** 043 asks
> the right question with the right join and its rule 0 is correct. What is
> wrong is one number: the sample size used for the error bar. 043 counts
> SHARES. The venue resolves MARKET-SIDES. There are 22.4 shares per
> market-side, so the error bar is understated 4.7x.

> **The consequence is specific and checkable.** At the 400-matched-position
> bar 043 names, the cluster-level standard error on the settle rate is
> **0.0127** (environment A) and **0.0141** (environment B). 043's kill band is
> **0.010**. The band is **0.79 and 0.71 sigma wide** - narrower than one
> standard error of the quantity it grades.

## The one measurement that decides it

Group the matched salvage positions by `(market_slug, outcome_side)` and ask
what settle rates the clusters take. The answer is `{0.0, 1.0}` and nothing
else, in both databases.

That is the whole argument. A market-side resolves once. Every share keyed to
it shares one outcome. Twenty-two shares from one market-side are one draw
observed twenty-two times, not twenty-two draws, and dividing by their count
inside a standard error is the classic clustered-sampling error - here with a
design effect of 22.4.

Everything downstream follows arithmetically:

| sample | clusters | SE(settle rate) | 043 band / SE |
|---|---|---|---|
| trading.db today, 144 pos | 123 | 0.0212 | 0.47 sigma |
| survivors today, 127 pos | 107 | 0.0249 | 0.40 sigma |
| **at 043's bar, 400 pos (A)** | **342** | **0.0127** | **0.79 sigma** |
| **at 043's bar, 400 pos (B)** | **337** | **0.0141** | **0.71 sigma** |
| 5,831 pos (A) | 4,981 | 0.0033 | 3.0 sigma |
| 7,110 pos (B) | 5,990 | 0.0033 | 3.0 sigma |

Either the bar moves to roughly 5,800-7,100 matched positions, or the band
widens to roughly 0.038-0.042 at 400, or the verdict is printed next to a sigma
that tells the reader it does not separate. This proposal implements only the
third, and refers the first two to Raven.

## The sign instability was a symptom, and it now has a diagnosis

D-354 R3 recorded that the headline flipped from +0.0286/share at 59 matched to
-0.0014 at 69 and instructed that neither be carried. That instruction was
right, and it was issued without a cause. Walking the cumulative delta position
by position this session reproduces both readings exactly and shows the rest of
the path:

```
n=25   +0.0620      n=69   -0.0014      n=126  -0.0022
n=50   +0.0422      n=100  -0.0140      n=143  +0.0063
n=59   +0.0283                          n=144  +0.0066
```

A spread of 0.076 across nested subsamples of one book, against a cluster-level
sigma of 0.0212 at that size: 3.6 sigma of wander, which is what a statistic
with no signal and a 4.7x-understated error bar does. Split survivors at its
median `opened_ts` and the two halves read **-0.0524** and **+0.0476** - a full
sign flip inside one book, one strategy, one exit reason.

The mean exit price across those same environment A halves is 0.0653 and
0.0655. Stable to four decimals. So the instability is not in what the salvage
exit sold at; it is entirely in what those market-sides turned out to be worth,
which is precisely the clustered quantity.

## Where the band came from, which is the actual root cause

043's kill condition justifies its threshold in one sentence:

> "The 0.010 band is not arbitrary and is not a taste: it is 4x the 0.0025 net
> directional bias measured in the thesis against positions with known
> outcomes, so a verdict either way survives the ledger being wrong at four
> times the rate we can currently demonstrate it is wrong at."

That is a careful, well-reasoned budget for **ledger measurement error** - the
risk that the instrument reports the wrong outcome. It is a complete answer to
that question and it contains no allowance for **sampling error** - the risk
that the outcomes are right and the sample is too small to distinguish the
delta from zero. Two independent error sources; the band was sized against one.

This is worth stating without blame: the ledger bias was the live worry at the
time, it was the novel risk, and 043 measured it and budgeted for it, which is
more than most instruments do. The sampling term was simply never in frame, and
the share-weighting made it invisible - 2,754 shares reads like a large sample
and 123 market-sides does not.

Note also that the self-check bias itself, now 0.0183 over 307 positions in
environment A with the direction flipped from +0.0043 to -0.0012, is a
share-weighted statistic with the identical clustering defect. Rule 5 of this
repair applies the same correction there. Its 0.0500 threshold is not re-sized.

## Why this repair might be wrong

The strongest objection is that I have understated the problem while claiming
to fix it, and I want that on the record rather than discovered later. The
market-side is not really the independent unit either. The Up and Down sides of
one window are perfectly anti-correlated, and two 5m windows on the same asset
four minutes apart are strongly dependent through the same spot path. So the
true number of independent draws is smaller than 123, the true sigma is larger
than 0.0212, and every figure in the table above is a **lower bound**. The
direction is the saving grace: the residual error can only make 043's band look
worse, never better, so the conclusion is robust to the correction I have not
made. But a reader should not take 0.0127 at the 400-bar as the answer. It is a
ceiling on how good the answer can be.

Second, the design effect is measured on today's sample and is assumed to
persist to 400 positions. Positions-per-cluster is 1.17 and 1.19 now. If the
loop's behaviour changes - concurrency raised, sizing changed, a restart
altering how many positions it opens per market-side - that ratio moves and the
projection moves with it. This is why rule 1 requires the cluster count be
**computed and printed every run** rather than the projection being hard-coded
as a constant. An instrument that prints its own current design effect cannot
go stale the way this document can.

Third, I am asserting that a sub-one-sigma band makes a verdict meaningless,
and that framing imports a significance convention the project has not
explicitly adopted. 043 nowhere claims to be a hypothesis test; it could be read
as a decision rule that deliberately accepts a high error rate in exchange for
deciding quickly, which is a defensible thing to want for a strategy that is
bleeding. If Raven reads it that way, then the correct output of this proposal
is not the 3-sigma gate in rule 4 of the kill condition but only the printed
sigma, and the gate should be dropped. I have written the two as separable for
exactly that reason.

Fourth, this repair makes 043 HARDER to conclude, and there is a real cost to
that. `PM_fair_value_settlement_exit` is the family carrying -986.94 USD of
salvage losses in trading.db and -2,392.52 in survivors, and it may be retired
under proposal 041's line of argument long before 5,800 matched positions
exist. In that case this repair's effect is that the salvage question is
recorded NOT_TESTED rather than answered wrongly. I think that is the right
trade and 043 rule 0 already committed to it, but it is a trade and not a free
improvement.

## What past failure this addresses

It addresses the exact failure convention 11 and convention 20 were written
against, in a new location: a number that looks like a result because it has
enough decimal places. The counterfactual's per-share deltas are printed to four
decimals against share counts in the thousands, and every instinct a reader has
says that is a well-determined quantity. It is 123 coin flips.

It also addresses D-354 R3 by supplying the cause it lacked. R3 saw two
readings flip sign and correctly ordered that neither be carried, treating the
instability as a hazard to be guarded against. It is not a hazard; it is the
expected behaviour of this statistic at this sample size, and once that is
established the guard can be replaced by a printed sigma that makes the same
point automatically on every run, for every exit reason, without needing a
ruling each time it happens.

Finally it addresses the standing correction that the exit column is not where
the money is going. 043 established that on a 59-position reading; this repair
establishes that neither the 59-position reading nor the 144-position one
establishes anything, which leaves the standing correction resting where it
always properly rested - on the 91%-model diagnosis and the entry prices, not
on the exit counterfactual.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET; the graveyard is crypto spot and perp and holds no rows in
  this class. The engaged prior work is proposals 038, 041 and 043 and rulings
  D-354 R2 and R3, cited by measurement rather than by graveyard id.
