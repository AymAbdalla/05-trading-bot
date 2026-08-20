---
name: "pm_maker_fill_markout_probe"
thesis: "The venue now pays makers out of the taker fees it started charging, which makes the maker side the only part of our book with a venue-level tailwind. We have exactly one strategy on that side and we cannot currently tell whether its fills are good or whether it is being run over. PM_maker_rebate_quote_ladder is the only strategy in either database with observed maker fills. Read read-only from db/trading.db on 2026-08-19, split at the 2026-08-19 07:28:34 UTC boundary where `fill_was_maker` stopped being a backfilled zero and started being an observation: BEFORE the boundary, 16 closed positions, all carrying fill_was_maker = 0 which is BACKFILL and not a reading, P&L -4.85 USD, 80 shares, paid 0.4981 realised 0.4375, -0.0606 per share. AFTER the boundary, 27 closed positions, all 27 carrying an observed fill_was_maker = 1, P&L +2.60 USD, 135 shares, paid 0.5363 realised 0.5556, +0.0193 per share. Pooling the two gives the -2.25 USD lifetime figure that a naive query returns, and convention 32 forbids reporting it. The observed maker era is 27 positions and its outcome is 15 settlements at 1.00 against 12 at 0.00, which is 0.5556 against a mean paid price of 0.5363, and the one-sided binomial P(X >= 15 | n = 27, p = 0.5363) is 0.4988. That is not a weak result, it is a coin flip to four decimal places: the ladder's maker fills have so far settled at exactly what they cost. The strategy is therefore neither confirmed nor refuted, and the number that would decide it is not the settlement rate at all - under a rebate regime the ladder does not need to beat its fill price, it needs to lose less than the rebate pays. Nothing in the system measures that, because nothing records what the book did after a maker fill. All we store is the fill and, much later, the settlement, and between them sits the entire question Signal 3 raises: if the venue's existing makers are passive rebate farmers who widen after impulse, then a resting bid gets hit precisely when the market is about to move through it, and the rebate is compensation for adverse selection rather than free money. A post-fill markout - the mid at fixed horizons after the fill, against the fill price - is the standard measurement of exactly that, it is forecast-free in the sense that matters here since it asks only what the book did and not what it will do, and it is absent. Two facts make this urgent rather than merely missing. First, the ladder rests where the new fee is most expensive to cross: its 43 lifetime entries cluster at 0.4 to 0.8, with 10 at 0.5, 9 at 0.6 and 8 at 0.4, and the dynamic taker fee peaks at 50/50, so the rebate pool it would draw from is fed by takers crossing in precisely its band. Second, all 43 of its closes are settlements at 0.00 or 1.00 and none is a round-trip sale, so it pays no taker fee on either leg under proposal 040's accounting - it is the one strategy in the book whose fee incidence under the new regime is zero on both sides and positive on the rebate. That is the structural case for measuring it. Against that: the ladder lives only in db/trading.db, the main shadow loop that runs it stopped at 2026-08-19 16:17 UTC, and environment B's whitelist does not include it, so the observed maker sample is frozen at 27 and is currently growing at zero rows per hour."
expected_edge_bps: null
kill_condition: "This is an experiment and records no edge (convention 11): it is run to find out whether the ladder's maker fills are adversely selected, not because they are believed to be. It is graded on MAKER fills only, never pooled with taker fills and never pooled across the 2026-08-19 07:28:34 UTC backfill boundary (convention 32), over 200 or more maker fills with a complete markout record, measured by `agents/forge_shadow_eval.py --db db/trading.db`. The 27 fills that motivated this proposal are NOT gradeable and must not be graded; at the observed rate of 27 fills in the 8.81 hours from 07:28:34 to 16:17:03 UTC, or 3.07 per hour, 200 fills is approximately 65 hours of loop uptime. RETIRE the maker-ladder thesis, and record the answer as NEGATIVE, if the mean 60-second markout on those 200 fills is below -0.0315 per share - the taker fee at 50/50, which is the most the rebate pool can pay out per share crossed and therefore a hard ceiling on any rebate the venue can fund. A markout worse than the entire fee the venue collects means adverse selection exceeds the maximum possible rebate and no rebate schedule rescues the strategy, which is a verdict that does not depend on knowing the actual rebate number. CONFIRM, and only then propose sizing up in a separate proposal, if the mean 60-second markout is above -0.0100 per share AND the 10-second markout is not more negative than the 60-second one - both conditions, not either. The second condition is the adverse-selection signature and it is the point of the probe: a fill that is immediately underwater and stays there is a spread cost, while a fill that is fine at 10 seconds and underwater at 60 is a maker being run over slowly, and only the first is survivable. Between -0.0315 and -0.0100 the result is INCONCLUSIVE and is recorded as such, because that band is where the answer genuinely depends on the rebate number and the rebate number is not known (proposal 040, data_requirements). If 200 maker fills with markouts have not landed within 14 days of the instrumentation going live, record NOT_TESTED and requeue; do not grade a partial sample and do not extend the window by pooling in taker fills to reach n."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  0. Scope. This changes NO entry rule, NO exit rule, NO sizing and NO gate on any strategy, including the ladder. It adds a recording. The ladder quotes exactly as it does today and its P&L is unaffected by this proposal; what changes is that each of its fills leaves behind enough of the book to answer whether it was a good fill. Filed as an experiment rather than a repair because the instrument exists to answer a question about the strategy, not to fix a defect in the books.
  1. New table, `maker_fill_markouts`, additive, and NOT columns bolted onto `positions`. `positions` already carries 13 Polymarket-only columns and the venue-neutral-spine problem is a known one; this is per-fill data on one side of the book and belongs beside `positions`, keyed to it. Columns: `position_id` TEXT, `fill_ts` REAL, `fill_px` REAL, `fill_side` TEXT, `mid_at_fill` REAL, `mid_10s` REAL, `mid_30s` REAL, `mid_60s` REAL, `best_bid_at_fill` REAL, `best_ask_at_fill` REAL, `seconds_remaining_at_fill` REAL, with UNIQUE on `position_id`.
  2. NO DEFAULT and NO NOT NULL on any markout column. This is the `fill_was_maker` lesson applied before the fact rather than after: that column was declared NOT NULL DEFAULT 0, 2,253 historical rows were backfilled to 0, and the result is that this proposal's own thesis had to split its sample at a timestamp to find the 27 rows that are observations. A markout that is missing because the window ended must be distinguishable from a markout of 0.00, and a default makes those two the same row.
  3. A markout horizon that falls after the market's close is NULL and is COUNTED, with a reason (convention 20). This is not an edge case, it is the common case: these are 5-minute windows and a fill at 60 seconds remaining has no 60-second markout at all. Rule 8 is what stops that from silently becoming the sample.
  4. `mid_*` is read from the BOOK, never from the gamma summary fields. The standing trap is measured: gamma's `bestBid`/`bestAsk` read 0.63/0.64 while the live CLOB book for the same token was 0.06/0.08 three minutes from expiry. A markout computed off gamma would be fiction with a timestamp on it, and would be fiction in the direction that flatters a maker.
  5. Markout is defined once and written down: for a resting BUY filled at `fill_px`, markout at horizon h is `mid_h - fill_px`, so negative means the book moved away from us after we were filled. Report per share, never as a percentage, and never annualised. The comparison quantities in the kill condition - the 0.0315 fee ceiling and the -0.0100 threshold - are per share and the units must match without conversion.
  6. Record markouts for TAKER fills too, on every strategy, even though the kill condition grades only maker fills. The taker markout is the control: a maker fill that looks adversely selected is only interesting if the taker fills in the same markets over the same window do not look the same way, and without the control the probe cannot distinguish adverse selection from the market simply trending inside a window. Report the two separately and never pooled (convention 32).
  7. Do NOT wire any strategy to read this table. Every markout is readable only after the horizon has elapsed, which is after the fill, so a strategy consuming it is either look-ahead or a bug. Its only consumers are `backtest/` and `agents/forge_shadow_eval.py`. This is the same rule proposal 038 rule 6 applies to the resolution ledger and for the same reason.
  8. Report, alongside every markout figure, the fraction of fills for which that horizon was NULL, as numerator and denominator. If the 60-second horizon is available on only the early-window fills, then the 60-second markout is a measurement of early-window fills and calling it the ladder's markout is a selection effect - the same shape of error proposal 041 records against the settlement-frequency test.
  9. OPERATIONAL PRECONDITION, stated because it is the binding constraint and not a formality: the ladder runs only in the main shadow loop against db/trading.db, that loop stopped at 2026-08-19 16:17 UTC, and environment B does not carry PM_maker_rebate_quote_ladder in its whitelist. Until the main loop runs again this experiment accumulates nothing. Restarting it is not this proposal's decision and not its lane; the proposal records only that its clock has not started. Do NOT satisfy this by adding the ladder to environment B - that book is mid-measurement on a survivors-only A/B and its results are never crossed with the main loop's.
data_requirements: |
  HAVE, verified read-only in db/trading.db on 2026-08-19: `positions.fill_was_maker` as a real observed column for rows opened after 2026-08-19 07:28:34 UTC - 27 closed ladder positions, all reading 1. `positions.entry_px`, `exit_px`, `qty`, `pnl_net`, `exit_reason`, `opened_ts`, `strategy_id` produced every figure in the thesis.
  HAVE: the ladder's fills are already logged with the book at submission time by the paper adapter's maker path, so `best_bid_at_fill` / `best_ask_at_fill` / `mid_at_fill` require no new venue call - they are values the adapter already holds and discards. That is the cheap half of this proposal.
  MISSING, and it is the whole build: the book at fill + 10s, + 30s and + 60s. Nothing samples the book on a per-fill schedule today. `market_tape` samples on a loop cadence rather than relative to a fill, holds 34,700 rows over 2026-08-19 03:31 to 16:17 UTC, and is keyed by `market_id` with `condition_id` and `complement_id` present - so a markout could in principle be interpolated from it rather than sampled fresh. That is a real option and it is cheaper, but the interpolation error is unbounded near expiry where the book moves fastest, which is exactly where a maker gets run over. Sample fresh; if the tape is used instead, the interpolation gap must be recorded per row and the kill condition's thresholds re-derived, not reused.
  MISSING, and it is why the kill condition is written in markout rather than in profit: the REBATE. Its size, its mechanism, and whether it is per-fill or a pro-rata share of a daily pool are all unknown (proposal 040, same gap). A pro-rata daily pool cannot be attributed to a fill at all, and if that is the mechanism then no per-fill measurement can price the rebate and the kill condition's -0.0315 ceiling is the only threshold that survives. That ceiling was chosen for exactly that reason: it is derivable from the fee the venue collects without knowing how it pays it back.
  MISSING, non-blocking: `leg_bid_at_signal` / `leg_ask_at_signal` / `leg_bid_at_fill` / `leg_ask_at_fill` are populated on 10 of 2,140 positions, all multi-leg corridor rows, so data requirement 6 is still unmet and the ladder's own entry slippage is uninstrumented. Rule 1 records the book at fill directly rather than depending on those fields being fixed.
  NOT NEEDED: `market_resolutions` from proposal 038. The markout is a book measurement between two timestamps inside the window and needs no resolution. Settlement is already recorded on the position as `exit_px` 0.00 or 1.00 and the ladder settles 43 of 43.
  NOT NEEDED: the 15m keying change or `market_duration`. The ladder trades 5m windows.
markets: "Polymarket crypto Up/Down 5m windows, db/trading.db only. PM_maker_rebate_quote_ladder for the graded maker arm; all strategies for the taker control arm in rule 6. Explicitly NOT db/trading-survivors.db, which does not run the ladder."
kind: experiment
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

> **This proposal reports NO result.** The observed maker sample is 27 fills and
> its settlement outcome is 15 of 27 against a mean paid 0.5363, one-sided
> binomial p = 0.4988. That is not a weak signal, it is the absence of one.
> The +2.60 USD and +0.0193 per share are quoted to show the sample exists and
> its sign, not to suggest the ladder works. Anyone carrying either number
> forward as evidence is carrying noise.

## Why this might fail

The most likely failure is that the loop never restarts on a schedule that
lets 200 maker fills accumulate, and this lands as an empty table. At 3.07
fills an hour the experiment needs roughly 65 hours of uptime, the strategy
runs in one loop only, and that loop has been dead since 16:17 UTC. Rule 9
states this rather than working around it, because the honest failure mode of
this proposal is operational and not statistical, and dressing it up as a
measurement problem would hide the actual blocker.

The second failure is that the markout is real, negative, and tells us nothing
we can act on. If the ladder's fills mark out at -0.02 a share and the rebate
turns out to be worth 0.01, the answer is "it loses less than before and still
loses", which lands in the kill condition's INCONCLUSIVE band by construction.
I have written that band deliberately rather than forcing a binary, but a
reader should know in advance that the most probable single outcome of this
experiment is a number in the middle of it. The reason to run it anyway is that
the ceiling test - markout worse than the entire fee the venue collects - is a
verdict that does not need the rebate number, and that test can only come back
decisive or not-yet.

Third, the control arm may not control. Rule 6 compares maker markouts against
taker markouts in the same markets, but our takers and our maker do not quote
in the same conditions: the ladder rests at 0.4 to 0.8 while the taker
population is spread across the whole price range, with 83 of 521 recent taker
entries below 0.10. Comparing markouts across those two populations is
comparing different parts of the book. The comparison should be restricted to
the 0.4-to-0.6 band where both are present, and if that leaves too few taker
fills to compare against, the control fails and the probe reports maker
markouts with no control rather than pretending the pooled taker figure is one.

Fourth, and this cuts against the whole framing: the rebate tailwind is an
assumption sourced from press coverage of a venue announcement, not from
anything we have observed. We have never received a rebate, we do not model
one, and proposal 040 keeps `DEFAULT_MAKER_FEE_RATE` at 0.0 for exactly that
reason. If the rebate turns out to be small, discretionary, or gated on volume
tiers we will never reach, then the maker side has no tailwind and this probe
measures the quality of fills in a strategy with no economic case. The markout
is still worth knowing - it is the first fill-quality measurement this system
would have on either side of the book - but the "venue pays us" premise should
not be treated as established.

## What past failure this addresses

It addresses the failure named in the standing corrections as the reason
convention 32 exists: `fill_was_maker` is mostly backfill, 2,261 non-null rows
of which only 8 read 1 at the time that correction was written, and a maker
claim pooled across that boundary is meaningless. This proposal is the first
one to actually split at the boundary and report both sides, and the split is
worth the filing on its own - the pre-boundary rows say -0.0606 a share and the
post-boundary rows say +0.0193, which is a sign flip produced entirely by
whether the rows are observations. Anyone who had graded the ladder on its
lifetime -2.25 USD would have graded 16 rows of unknown fill type.

It also addresses the gap between our maker ambitions and our maker evidence.
Proposal 024 proposed the ladder on a rebate thesis; the ladder was built and
has now run 43 positions; and in that entire time the system has recorded
nothing about whether a maker fill is a good fill. The rebate was always the
mechanism and the fill quality was always the risk, and only the mechanism got
instrumented. Signal 3's complaint about venue makers - passive rebate farmers
who widen after impulse - is a description of a strategy that collected rebates
without measuring markouts, which is the strategy we currently are.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET; the graveyard is crypto spot and perp and has no rows in
  this class. The engaged prior failure is proposal 024's own unmeasured
  premise, cited above by position count rather than by graveyard id.


## Amendment 2026-08-19 (forge cycle tick 5): the clock has STARTED, and the
## observed sample has flipped sign

Rule 9 and the third failure mode both rest on an operational claim that is now
FALSE. They read: the ladder runs only in the main shadow loop, that loop
stopped at 2026-08-19 16:17 UTC, and "until the main loop runs again this
experiment accumulates nothing", at "zero rows per hour".

The loop was restarted by Raven and its first equity snapshot after the restart
is 1000.00 at 2026-08-19T20:06:30Z. Read read-only in `db/trading.db` at
2026-08-19T23:57:33Z: `PM_maker_rebate_quote_ladder` now holds **45** closed
positions carrying an observed `fill_was_maker = 1`, against the **27** this
proposal was written on, plus 4 still open. So the observed maker sample grew
by 18 fills, the experiment's clock is running, and rule 9's "accumulates
nothing" is superseded. At 45 of the 200 the kill condition requires, the
experiment is 22.5% of the way to a gradeable sample rather than 13.5%.

**The sign flipped, and this is the point of the amendment.** The 27 observed
fills this proposal was filed on carried +2.60 USD and +0.0193 per share. The
45 observed fills carry **-2.40 USD over 225 shares, or -0.0107 per share**, so
the 18 new fills took -5.00 USD between them and reversed the sign of the whole
observed era. That is exactly what the proposal's own banner said would happen
to a coin flip: the one-sided binomial on the original 27 was p = 0.4988, the
+0.0193 was declared noise in the filing, and it has now behaved like noise.
Nobody should read the new negative as a result either. It is the same sample
being resampled.

**Do NOT read -0.0107 per share against the kill condition's -0.0100 and
-0.0315 thresholds.** Those thresholds are denominated in **60-second post-fill
markout**, which nothing in the system records yet - that is the entire build
this proposal asks for. Realised P&L per share and markout are different
quantities: the first includes the settlement outcome of a 5-minute binary and
the second is a book measurement over a fixed horizon inside the window. The
numerical coincidence that -0.0107 sits a hair outside the INCONCLUSIVE band's
upper edge is a coincidence and must not be reported as the experiment
returning a verdict. The experiment has not run.

### Signal 2, filed as a risk factor and not a kill

Cycle 4's external scan reports Polymarket recruiting an internal
market-making team, including approaching sports bettors, to provide liquidity
on its own books. If that desk quotes in the 0.4-to-0.8 band where this ladder
rests, three things this proposal assumes get worse at once and they are not
independent: the rebate pool is split more ways, so the unknown rebate this
proposal already refuses to model gets smaller; the queue position a passive
resting bid can achieve degrades against a counterparty with venue-side
latency; and the adverse-selection risk the markout exists to measure rises,
because the fills that survive a better-informed competing maker are
disproportionately the ones that competitor declined.

This changes NO threshold in the kill condition and it must not. The -0.0315
ceiling is derived from the taker fee the venue collects, which is the hard
upper bound on any rebate the venue can fund, and an internal MM desk taking a
share of that pool can only move the real rebate DOWN and therefore can only
make the ceiling test more conservative, never less. That is the property the
ceiling was chosen for. What Signal 2 does change is the priority of running
the experiment: the measurement gets harder to interpret the longer the venue's
own desk is in the book, because a markout measured after the desk arrives
cannot be compared with one measured before it. The clean pre-desk baseline is
available now and will not be available indefinitely. Source is a web3.career
job listing surfaced 2026-08-19 and reported by coin360; it is a hiring
signal, not an announced product, and it is recorded here at that strength.
