---
name: "pm_settlement_exit_uncensored_arm"
thesis: "D-327 re-gated proposal 034 from a profit strategy to a MEASUREMENT INSTRUMENT, on the argument that it is the only strategy in the registry that holds the fair_value selector to settlement with the round trip instrumented, and that this calibration data exists nowhere else in the system. The instrument is now live as PM_fair_value_settlement_exit and it does not measure what D-327 wants, because the salvage floor it inherited from 034 rule 4 censors the sample, and censors it in one direction. Read from db/trading.db at 2026-08-19 02:45 EDT: 6 entries, 5 closed. Three of the five exited on sell:salvage_floor at 6.2s, 48.7s and 80.1s. Only two reached a terminal binary price (one target at exit_px 1.00 after 541s, one stop at exit_px 0.00 after 657s). That is a 60% censoring rate on the only sample D-327 named. The censoring is not random. A position reaches the salvage floor precisely when its bid has collapsed to 0.10, which is to say precisely when it is losing, so the censored subset is the loser subset by construction. D-327's kill condition is 'dead if realised settlement frequency over the first 60 entries is below 0.30 against a mean entry ask of 0.33'. That is a LOWER bound test, and deleting losers from the numerator's complement raises the measured frequency. The salvage floor therefore pushes the instrument systematically AWAY from its own kill condition. An instrument whose sampling rule protects it from the test it exists to fail is not an instrument, it is a filter, and the number it produces cannot be used either to retire the fair_value family or to rescue it. Second, independent defect, observed in the same six entries rather than reasoned about: the degenerate case the module documents at fair_value_settlement_exit.py:177-189 was written for entries at or below the 0.10 floor, where salvage_stop_price collapses onto the structural 0.00. It does not cover entries marginally above the floor, and that band is populated. Position 5b9643c3 entered at 0.11 against a stop at 0.10, held one cent of room, and closed 6.2 seconds later for -0.17 USD. The module's own convention 8 assertion passes (0.10 is strictly below 0.11) while the economic content of the stop is nil. The repair is not to change 034's thesis, which D-327 already settled, and not to widen the floor, which would be tuning. It is to run the instrument arm uncensored and let the salvage floor keep its own separate arm, so that the calibration number D-327 asked for is computed on entries that all reach 0.00 or 1.00."
expected_edge_bps: null
kill_condition: "This is a repair and records no edge (convention 11, and the README's rule that a repair naming a number becomes a cited figure two documents later). Its own success condition is a measurement, not a P&L: the repair is DONE when agents/forge_shadow_eval.py can report realised settlement frequency for PM_fair_value_settlement_exit_uncensored on 60 or more entries with a censoring rate of 0.00, meaning every one of those 60 closed at exit_px in {0.00, 1.00}. The repair is FAILED, and should be reverted rather than tuned, if after 60 entries the uncensored arm's censoring rate is above 0.00 from any cause other than an explicit halt, because that means some other exit path is also truncating the sample and the instrument is still not measuring settlement. D-327's own kill condition then applies unchanged to the uncensored arm and to NO other arm: 034's family is dead if realised settlement frequency over those 60 entries is below 0.30 against a mean entry ask of 0.33. At the observed entry rate (6 entries over the 3.88 hours of signal history in db/trading.db at 02:45 EDT, 1.55 entries/hour) 60 entries is roughly 35 hours, so if 60 entries have not landed within 7 days, record NOT_TESTED and requeue rather than grading the partial sample."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  0. Scope. This changes the EXIT path only. Nothing about the entry model, the tightened edge gate (model edge >= 0.05), the 0.60 entry ask cap, the 10 USD sizing or the 2-slot concurrency cap changes. All of those are 034 rules that D-327 left standing, and changing more than one thing at a time is the confound 034's own "why this might fail" section already flagged.
  1. Fork the arm, do not mutate the existing one. Register PM_fair_value_settlement_exit_uncensored alongside PM_fair_value_settlement_exit. Both share the entry model. They are never pooled in any report, for the same reason maker and taker fills are never pooled (convention 32): they have different sampling rules, so a pooled settlement frequency is a mixture of two populations and means nothing.
  2. Uncensored arm exit, the only exit: hold to window resolution. No salvage floor, no time stop, no converged-mid sale, no model stop. The position closes at 0.00 or 1.00 and at no other price. If the adapter cannot express "no stop", set the stop to the structural floor BINARY_STOP = 0.00, which is unreachable above zero and is already the value salvage_stop_price returns in its degenerate branch, so no new constant is invented.
  3. Uncensored arm sizing: 5 USD fixed, maximum 1 concurrent position. Halving the size against 034 is not a risk opinion, it is because this arm carries every loser to 0.00 instead of salvaging 0.10, so its per-position loss is strictly larger and the 2-slot budget the pair now shares must not grow. Total exposure across both arms stays at 034's original 2 slots.
  4. Salvage arm: PM_fair_value_settlement_exit continues exactly as it is, unchanged, 034 rule 4 intact. It is now explicitly a P&L arm and not the instrument. Do not delete it. The difference between the two arms IS the measurement of what the salvage floor is worth, in cents per share, and that number does not exist anywhere in the system either.
  5. Degenerate-band gate, applies to the SALVAGE arm only: refuse an entry whose ask is at or below salvage_floor + 0.02, emitting a new skip reason settlement_entry_inside_salvage_band, counted (convention 20). This is the fix for position 5b9643c3's one cent of room. It is stated as a gate on entry rather than as an adjustment to the floor deliberately: moving the floor is tuning a number that D-327 did not rule on, refusing an entry that cannot express the rule is not. The uncensored arm needs no such gate because it has no floor.
  6. Record on every entry in both arms: model_edge_at_entry, entry_ask, best_bid_at_entry, the arm name, the terminal exit_px, and a boolean censored (true iff exit_px is not in {0.00, 1.00}). The censoring rate is the field that makes this proposal falsifiable and it is currently derivable only by eyeballing exit_reason strings.
data_requirements: |
  HAVE, verified in db/trading.db at 2026-08-19 02:45 EDT: the entry model and its signal stream (3,294 signals for PM_fair_value_settlement_exit, 6 acted); positions.exit_px and positions.exit_reason, which is how the 60% censoring rate above was derived; hold duration, from closed_ts - opened_ts; the instrumented model_edge_at_entry and entry_ask that D-327 credits the strategy with.
  HAVE, but note the shape: settlement is recorded as exit_reason 'stop' when the token settles to 0.00 and 'target' when it settles to 1.00. It is NOT a distinct exit_reason. PM_temporal_arbitrage's 91 closed positions show the same pattern (76 'stop' all at exit_px 0.00, 15 'target' all at exit_px 1.00). Any code or query that computes a censoring rate must key on exit_px in {0.00, 1.00}, NOT on the exit_reason string, or it will classify every settled loss as a stop-out and report a censoring rate near 1.00. This is the single easiest way to get this proposal's own measurement wrong.
  MISSING, non-blocking: a settled boolean or a distinct exit_reason for resolution. Rule 6 above adds it. Until it exists the exit_px test is the workaround, not the design.
  MISSING, non-blocking but it degrades the result: positions.fill_was_maker is in db/schema.sql:95 and in engine/polymarket/shadow_loop.py:720 but is NOT a column of the positions table in either db/trading.db or db/trading-survivors.db, because both live loops run commit e033078, which does not contain it (verified: git show e033078:engine/polymarket/shadow_loop.py greps zero hits). Convention 32 is therefore unenforceable on every position either arm books until the next natural restart. This does not block the repair, because both arms are affected identically and the comparison between them survives. It does block reporting either arm's absolute number as an executable result. State that caveat in the verdict or the verdict is a convention 32 violation.
  NOT NEEDED: any new feed, any new market, any orderbook field the parent does not already read.
markets: "Polymarket crypto Up/Down windows, parent PM_fair_value_arb entry signal only, unchanged from 034. No other variant, no other asset."
kind: repair
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

## Why this might fail

The most likely failure is that it works exactly as designed and the answer is
boring: the uncensored arm settles at some frequency near its mean entry ask,
the salvage arm loses slightly less, and the difference between them is one or
two cents a share with a standard error just as large. Sixty entries is a small
sample for a frequency estimate. If the true settlement frequency is 0.33 and
n = 60, the standard error is about 0.061, so the 0.30 kill threshold sits well
inside one standard error of the null. **The kill condition can fire on noise,
and it can equally fail to fire on a genuinely dead model.** I am proposing it
anyway because D-327 already chose that threshold and re-deriving it here would
be inventing a number under cover of a repair; but whoever grades it should
report the interval, not the point.

Second, this arm is deliberately worse in P&L terms than the one it forks from,
and it will look bad on the dashboard. Carrying every loser to 0.00 instead of
salvaging 0.10 costs, at 5 USD and roughly 15 shares a position, about 1.50 USD
per losing position that would otherwise have salvaged. Over 60 entries with a
two-thirds loss rate that is on the order of 60 USD of deliberately-forfeited
salvage value, against an account already at -920 USD. That is the price of the
measurement and it should be stated as a line item, not discovered later and
attributed to the model.

Third, and this is the failure mode I would bet on: the salvage floor may not
be the only thing censoring the sample. Three of the five closes hit the floor,
but I have five closes. Five. Every number in this proposal's thesis rests on a
sample of five closed positions read at one moment from a database a live
process is still writing to. The 60% censoring rate could be 30% or 80% by
tomorrow. What does NOT rest on n = 5 is the structural argument, which is that
a stop that fires when the position is losing removes losers from a
settlement-frequency estimate, and that is true at any n including n = 0. The
repair is justified by the mechanism; the 60% is an illustration of it, and I
have labelled it with a timestamp so nobody quotes it as a finding
(convention 25).

Fourth: forking an arm doubles the strategy count in a registry already running
17 per asset, and both arms draw on the same 2-slot budget that 034 sized to
avoid starving everything else. If the shared budget turns out to bind, the two
arms will steal entries from each other non-randomly (the arm that happens to
see a signal first wins), and that is a new selection effect sitting on top of
the one this proposal exists to remove. Rule 3's 1-slot cap on the uncensored
arm is meant to bound that, but it does not eliminate it, and if the entry rate
rises the arms should be split across separate slot pools instead.

## What past failure this addresses

This addresses a failure of the *record*, not of a strategy, which is why it is
a repair and not an edge hypothesis. The buried hypotheses in play are
hypothesis_graph ids 113, 136, 140 and 149 (PM_fair_value_arb, failure modes
stop_too_tight, model_miscalibrated, spread_eats_edge) plus 114, 137, 139, 142,
143 and 144 across the hft, inverse and wide variants. Nine kill recommendations
for that family hang on whether the model has directional content. 034 was
written to settle it, D-327 narrowed it to a calibration measurement, and the
thing that would actually settle it is a settlement-frequency number computed on
an unfiltered sample. That number does not yet exist and, with the salvage floor
in the exit path, will not exist after 60 entries either. This proposal's whole
content is that the instrument as wired cannot produce the evidence its own
governing decision asked for.

What is DIFFERENT from 034: nothing in the entry path, and 034's rule 4 is not
deleted, it is moved into its own arm and kept as a control. What is different
from the parent fair_value family: this does not invert a loser, does not lower
an edge threshold, and does not raise max_trades_this_window, which are the
three prohibitions the vault note `fair_value_arb.md` records. Rule 5 tightens
an entry gate rather than loosening one, consistent with the standing correction
that tightening an edge gate to make a strategy fire selects FOR model error;
here the gate is on price room, not on edge, so it does not select on the model
at all.

The standing correction that most constrains this proposal is the one about
what the market's calibration actually is, and it moved while I was reading. The
CLAUDE.md headline says PM_temporal_arbitrage shows the market calibrated to
within 0.06 percentage points (83 positions, paid 0.1813, realised 0.1807). At
02:45 EDT db/trading.db has 91 closed positions on that strategy, 455 shares:
paid 0.1832 a share, realised 0.1648 a share, **edge -0.0184, which is 1.84
percentage points against us, roughly thirty times the quoted figure.** Eight
additional positions moved the headline number by that much, which is the real
lesson: at n = 91 that estimate is not stable enough to bound anything tightly,
in either direction. It does not rescue the fair_value model, and it does not
make forecasting look good. It does mean the sentence "the market is calibrated
to within 0.06 points" should not be carried into another document without
re-deriving it, and it is a direct argument for building instruments that reach
larger uncensored samples, which is what this proposal is.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET, EVENT and SPORTS: the graveyard has no rows in those
  classes. The hypothesis_graph ids cited above are the substitute and they are
  engaged, not dodged.


## AMENDMENT 2026-08-19 (forge cycle 2)

**Additive. Nothing above is retracted or rewritten.** Appended by the forge
reasoner cycle-2 session after measuring the salvage floor's realised cost in
`db/trading.db` at ~11:30 UTC. Three corrections, in order of how much they
change this proposal.

**(1) This proposal's MECHANISM is confirmed. Its framing as a bleed is not.**
The thesis says a position reaches the salvage floor "precisely when it is
losing, so the censored subset is the loser subset by construction." That is
now measured rather than reasoned: of the 37 `sell:salvage_floor` exits, 21
have a recoverable market-side resolution and **21 of 21 settled at 0.00. Zero
settled at 1.00.** The censored subset is the loser subset, exactly as claimed,
and the one-directional censoring this repair exists to remove is real.

But the separate claim, made in the 2026-08-19 cycle-2 Raven brief rather than
in this file, that `salvage_floor` is "a new, expensive exit" and "the
settlement-exit family's censoring mechanism" costing -130.56, conflates two
different things. The -130.56 is a loss that was already incurred by the time
the floor fired. Against holding those same 21 positions to resolution, the
salvage floor **saved 26.09 USD**: actual -70.79 versus a counterfactual
-96.88. Selling at a mean 0.0650 into a realised 0.000 was the correct
economic decision on every recoverable observation. `salvage_floor` is a
measurement problem, which is this proposal's point, and it is NOT a P&L
problem. Do not repair it as though it were, and do not widen or remove the
floor to stop the bleeding - the bleeding is upstream of the exit.

**(2) The refutation in (1) is itself computed on a biased sample, and this
proposal now has a hard precondition.** Resolution is recoverable only by
inferring it from a sibling position on the same market and side that was held
into settlement. That covers 325 of 864 distinct (pair, outcome_side)
market-sides, 37.6%, and the recoverable subset over-represents markets that
settled 0.00 by roughly 20 points, because a winning side gets sold early by
`profit_target` and leaves no settlement row. **The 0-of-21 result above sits
in exactly the stratum that bias favours**, so it is directionally right and
quantitatively soft. More seriously, this proposal's own censoring-rate
measurement - the number in its kill condition - would be computed on the same
biased recovery, which means the instrument proposed to fix the censoring
inherits a second selection effect from the recovery method.

**Proposal 038, `pm_settlement_resolution_ledger`, is therefore a BLOCKING
precondition for grading this repair**, though not for building it. The
uncensored arm can be forked and run at any time; its censoring rate and its
settlement frequency must not be graded until resolution is read from the 038
ledger rather than inferred. Recorded here rather than by editing the kill
condition above, which stays as written.

**(3) One data_requirement above is now STALE.** It states that
`positions.fill_was_maker` "is NOT a column of the positions table in either
db/trading.db or db/trading-survivors.db, because both live loops run commit
e033078." That was true when written and is no longer. After the 2026-08-19
03:28 EDT restart the column exists and carries 2,261 non-null values in
`db/trading.db`. **Re-derive before trusting it:** only 8 rows read 1, all
opened between 07:29 and 08:52 UTC, and the 2,253 zeros run back to 2026-08-18
03:02 - well before the column existed. Pre-restart zeros are BACKFILL, not
observations. Convention 32 is now mechanically checkable on positions opened
after 2026-08-19 07:28:34 UTC and on no others, so the caveat in that
data_requirement still binds for every position booked before then.

*Numbers above re-derive from `db/trading.db` at 2026-08-19 ~11:30 UTC and are
claims about that read (convention 25). See `038-pm-settlement-resolution-ledger.md`
and `039-pm-time-stop-hold-through.md`.*
