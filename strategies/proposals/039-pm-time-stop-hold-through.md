---
name: "pm_time_stop_hold_through"
thesis: "Of the five exits this system uses, four sell above what the position turned out to be worth and one sells below it. That comparison is forecast-free: it does not ask whether a model predicted anything, only whether the price taken at the exit was above or below the realised settlement of that same market-side, and both numbers are recorded. Read from db/trading.db at 2026-08-19 ~11:30 UTC, restricted to early exits whose market-side resolution is recoverable, exit price against realised settlement rate: sell:price_stop sells at 0.2464 into a realised 0.169 (n=231, -0.078 in the seller's favour); sell:profit_target 0.4376 into 0.203 (n=79, -0.235); sell:salvage_floor 0.0650 into 0.000 (n=22, -0.065); sell:model_stop 0.3337 into 0.308 (n=13, -0.026); and sell:time_stop 0.4410 into 0.625 (n=16, +0.184 AGAINST the seller). In counterfactual dollars over the same recoverable rows, holding instead of exiting would have cost 287.24 on price_stop, 292.67 on profit_target and 26.09 on salvage_floor, and would have GAINED 53.45 on time_stop. The effect is concentrated: 29 of the 45 time-stopped positions belong to PM_fair_value_arb, 14 of those have recoverable resolution, and 10 of those 14 settled at 1.00 against an 18.6% base rate for that strategy on 231 recoverable positions. The mechanism is plain in the code. `strategies/polymarket/fair_value_arb.py:178` sets TIME_STOP_SEC = 60.0 and `dip_arb.py:1380` shows the shape - the exit fires on position AGE alone, with no reference to price, model or book, and then sells at URGENT_SELL_LIMIT into the bid. Every other exit in the system conditions on something that moved. This one conditions on the clock, and a clock carries no information about a binary that settles at a fixed time regardless. What makes the result worth filing rather than dismissing is the direction of the known bias. Resolution recovery in this database over-represents markets that settled 0.00 by roughly 20 points (proposal 038), which inflates the measured performance of EVERY exit. time_stop measures badly anyway. The bias is working against this finding, not producing it. What makes it an experiment rather than an edge hypothesis is that n = 16, the one-sided binomial P(>= 10 of 16 | p = 0.441) is 0.1095, and that is not significant."
expected_edge_bps: null
kill_condition: "This is an experiment and records no edge (convention 11): it is run to FIND OUT whether the clock exit destroys value, not because it is believed to. It is graded ONLY on taker fills and never pooled across fill types (convention 32), and it is graded only on positions whose market-side resolution is available from the proposal 038 ledger - never from sibling inference, which is the biased method this result is already contaminated by. RETIRE the hypothesis, restore the 60s time stop and record the answer as NEGATIVE, if the held arm's realised settlement value is below the time-stopped arm's realised exit value by any amount over 120 or more resolved, taker-filled, matched observations as measured by agents/forge_shadow_eval.py. CONFIRM, and only then propose a permanent exit-policy change in a separate proposal, if the held arm is above the exit arm by 0.05 or more per share on those same 120 observations with a one-sided binomial p below 0.05 against the mean exit price of the time-stopped arm - both conditions, not either. If fewer than 120 matched observations have landed within 14 days of the 038 ledger going live, record NOT_TESTED and requeue (convention 11); do NOT grade the 16 observations that motivated this proposal, and do not grade the current 45 time-stopped positions, because 29 of them have no known outcome and grading the 16 that do is grading the recoverable subset, which is the exact error this condition exists to refuse."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  0. Scope. This changes ONE exit on ONE strategy and nothing else. No entry rule, no sizing, no gate, no other exit, no other strategy. The system's diagnosis is that the model is 91% of the loss and execution ~9%, and this proposal does not dispute that or try to repair the model. It asks a narrower question: given the entries the model already makes, is the 60-second clock exit taking money off the table. Changing more than one thing forfeits the answer.
  1. BLOCKING PRECONDITION: proposal 038, the settlement resolution ledger, must be landed first. Not "should be". Without it, resolution is available for 37.6% of market-sides and that 37.6% is selected on the outcome (proposal 038 thesis), so the matched comparison in rule 4 would be computed on a sample chosen by the thing it is measuring. This is the same treatment 037 gives 036 and for the same reason. Until 038 lands the honest status of this hypothesis is NOT_TESTED, never negative.
  2. Fork the arm, do not mutate the existing one. Register PM_fair_value_arb_hold_through alongside PM_fair_value_arb, sharing the entry model, the edge gate, the entry cap and the sizing unchanged. Both arms keep price_stop, profit_target, model_stop and salvage_floor EXACTLY as they are. The forked arm differs in one respect and one only: it has no time stop.
  3. The forked arm's replacement for the time stop is to hold to resolution. It is not a longer time stop. A 120s or 180s clock would be tuning a number this proposal has no evidence about, and the evidence it does have is about the clock exit existing at all, not about its setting.
  4. Matched-pair recording, and this is the measurement, not a log line. On every position in EITHER arm, at the moment the 60-second mark is crossed while the position is still open, record: `t60_bid`, the best bid at that instant; `t60_ask`; `t60_seconds_remaining` in the window; the arm name; and later the resolved value from the 038 ledger. The comparison is `t60_bid` against resolved value, per share, on the SAME instant for both arms. This gives a matched observation from the control arm too, so the experiment does not depend on the two arms drawing similar markets - which over 120 observations they will not.
  5. Sizing on the forked arm: 5 USD fixed, maximum 1 concurrent position, and the two arms share PM_fair_value_arb's existing concurrency budget rather than adding to it. The held arm carries each loser all the way to 0.00 instead of selling it at the 60s bid, so its per-position loss is strictly larger and its exposure must not grow to match. This is not a risk opinion, it is the same reasoning proposal 035 rule 3 applies to its own uncensored arm.
  6. Gates: the forked arm refuses any entry it cannot hold, which means any entry where `holding_seconds_available` is less than the remaining window. It also refuses entries whose fill is a maker fill, recorded via `fill_was_maker`, so that the arm is taker-only by construction and convention 32 is satisfied mechanically rather than by filtering afterwards. Every refusal carries a counted reason (convention 20).
  7. Report the two arms separately and NEVER pooled, and report the matched-pair difference with an interval, not a point. A 120-observation frequency estimate has a standard error near 0.045 and the confirm threshold in the kill condition sits close enough to that to matter.
data_requirements: |
  BLOCKER: `market_resolutions` from proposal 038. Everything in rule 4 and the entire kill condition reads from it. See rule 1.
  HAVE, verified in db/trading.db at 2026-08-19 ~11:30 UTC: `positions.exit_px`, `positions.exit_reason`, `positions.entry_px`, `positions.qty`, `positions.pnl_net`, `positions.opened_ts`, and `signals.features_json.outcome_side`. These produced every number in the thesis. Also `signals.features_json.holding_seconds_available` and `seconds_remaining`, which rule 6 needs and which are already emitted.
  HAVE, and it is new since the 03:28 restart: `positions.fill_was_maker` is now a real column with 8 observed maker fills and 2,253 zeros. RE-DERIVE BEFORE TRUSTING IT. The zeros run back to 2026-08-18 03:02, well before the column existed, so pre-restart zeros are BACKFILL and not observations; only rows opened after 2026-08-19 07:28:34 UTC carry an observed value. Rule 6 depends on the observed era only, which is why the arm gates on it at entry rather than filtering on it in the report.
  MISSING, and it is the reason rule 4 records `t60_bid` rather than reconstructing it: the book at the 60-second mark is not stored for positions that did NOT time-stop, so the control arm currently has no matched observation and the comparison cannot be built from history. This is why the experiment must be run forward and cannot be answered by a query, and it is the single largest cost of the proposal.
  MISSING, non-blocking, and it bounds the verdict: `leg_bid_at_signal`, `leg_ask_at_signal`, `leg_bid_at_fill` and `leg_ask_at_fill` are populated on 10 of 2,140 positions, all multi-leg corridor rows, and every single-leg entry is NULL. Data requirement 6 is still unmet. The held arm is single-leg, so its entry slippage stays uninstrumented and no execution-quality claim can be made from this experiment either way.
  NOT NEEDED: the 15m keying change, `market_duration`, the calibration tape, `market_tape.condition_id` or `complement_id`. This experiment is scoped entirely to 5m windows and to fields that exist today plus the 038 ledger.
markets: "Polymarket crypto Up/Down 5m windows, PM_fair_value_arb entry signal only. Explicitly NOT extended to the 15m markets - that universe is not keyed until the 2026-08-20 restart and a proposal scoped to it is not evaluable yet."
kind: experiment
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

> **This proposal reports a result that is NOT statistically significant.**
> n = 16, one-sided binomial p = 0.1095. It is filed as an `experiment`, which
> is the kind reserved for a probe run to find out, precisely because the
> evidence is suggestive and thin. Anyone quoting the +0.184 figure as a
> finding rather than as a motivation is misreading it.

## Why this might fail

The most likely failure is that it is noise and the honest answer is negative.
Ten of sixteen is not a lot of ten, the p-value is 0.11, and a system that
computes five exit-reason comparisons and reports the one that came out
backwards has computed five comparisons. Correct that for the five buckets in
the thesis and 0.11 stops being interesting at all. I am filing it anyway
because the kill condition demands 120 fresh observations rather than
re-grading these sixteen, so the multiple-comparison problem lives in the
motivation and not in the test - but a reader who stops at the thesis will
carry the wrong number away, which is why the banner above exists.

The second failure is survivorship, and it is the strongest argument against
this proposal. A time stop fires when a position has neither hit its target nor
its stop after 60 seconds. That is a SURVIVOR by construction. Survivors of a
60-second window are positions whose price did not collapse, and positions
whose price did not collapse settle at 1.00 more often than the unconditional
base rate. So a 71.4% settlement rate on time-stopped fair_value_arb positions
against an 18.6% strategy base rate is not evidence of anything by itself - the
comparison is rigged by the conditioning. This is exactly why the thesis
reports the exit-price comparison instead: at the moment time_stop fires the
market was bidding 0.4410, and 0.4410 already prices in whatever survivorship
implies. The realised 0.625 is measured against the market's own contemporaneous
price, not against the base rate, and that is the only version of the comparison
that survives the objection. If a reviewer takes one thing from this section it
should be that the 18.6%-versus-71.4% framing is WRONG and the
0.4410-versus-0.625 framing is the claim.

Third, the bias in proposal 038 cuts both ways more than the thesis admits.
I argued that loser-over-sampling makes every exit look good and therefore that
time_stop looking bad is robust. That is right in aggregate but it is not
guaranteed to be right within a bucket. time_stopped positions are survivors,
survivors are more likely to be on the winning side, and a winning side is
LESS likely to leave a settlement row - so the recoverable 16 may be an odd
corner of the 45 rather than a random draw from it. The 29 unrecoverable ones
could plausibly run the other way. That is not a caveat I can resolve with the
data in hand, and it is the second reason rule 1 makes 038 a hard precondition
rather than a nice-to-have.

Fourth, the economics are small even if the effect is real. 0.184 a share on
positions sized around 10 USD at roughly 0.44 a share is on the order of 4
dollars a position, and 45 time stops over 32 hours is not a large population.
Against an account down roughly 39 dollars on the session and 3.9% from start,
removing a bad exit is a rounding correction, not a recovery, and it should not
be sold as one. Its value is that it is the first forecast-free claim about
this system's EXIT policy that can be tested without predicting anything.

Fifth, a correction to my own framing before someone else makes it. The thesis
presents "four exits sell above realised value and one sells below" as if the
four were skill. They are almost certainly not. `profit_target` selling 0.235
above realised value on a sample that over-represents 0.00 outcomes by ~20
points is mostly the bias, and reading it as evidence that the exit policy is
good would be the same error in the opposite direction. The four are context
for the sign of the fifth. They are not a finding, and no proposal should be
built on them.

## What past failure this addresses

It addresses the fair_value family's bleed from the one angle the family's
repairs have not tried. PM_fair_value_arb is 744 closed positions and the
largest single loss centre in the book; sell:price_stop across all strategies
is 929 positions and -1,675.76. Every repair aimed at that family so far has
been a repair of the FORECAST - tighten the edge gate, invert the selector,
hold to settlement, change the stop. Proposal 037 responded by leaving the
forecast behind entirely. This one does neither: it accepts the entries as
given and asks whether one specific exit rule is subtracting value on top of
them, which is a question about policy rather than about prediction and is
answerable without any view on direction.

What is DIFFERENT from proposal 034 and 035: those hold the fair_value selector
to SETTLEMENT to measure its calibration, and D-327 re-gated 034 to a
measurement instrument for that reason. This proposal is not measuring the
selector. It keeps all four price-driven exits intact and removes only the
clock, so the held arm is not a hold-to-settlement arm and its results are not
comparable to 034's or 035's and must never be pooled with them. What is
DIFFERENT from proposal 025, the window-cap opportunity-cost probe: 025 asks
what the throttle costs at ENTRY, this asks what the clock costs at EXIT.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET; the graveyard is crypto spot and perp. The engaged prior
  failures are the fair_value family's hypothesis_graph entries, cited above by
  P&L rather than by id because the argument is about one exit rule shared
  across the family rather than about any single burial.


## Amendment 2026-08-19 (forge cycle tick 4): the observation source is gone

Rule 1 makes proposal 038 a blocking precondition. A second blocker has since
appeared and it is operational rather than methodological, so it is recorded
here rather than folded into rule 1.

This experiment forks `PM_fair_value_arb`. Read read-only on 2026-08-19:
`db/trading.db` holds all 976 of that strategy's positions and 33 of the
system's 49 `time_stop` exits; `db/trading-survivors.db` holds **zero**
`PM_fair_value_arb` rows, because environment B's whitelist does not include
it, and its only 15 time-stopped positions belong to `PM_fair_value_arb_wide`
and `PM_fair_value_arb_patient`, which are different strategies with different
entry gates and are not substitutes.

The main shadow loop that writes `db/trading.db` stopped at **2026-08-19
16:17:57 UTC** (last position close; last equity snapshot 619.05 at 16:17:44
UTC). Confirmed by `ps` the same day: only PID 71442/71444, the environment B
survivors loop, is running. So the count of matched observations available to
this experiment is currently growing at **zero per hour**, and environment B
cannot supply them.

Three consequences, none of which change the design:

1. The 14-day `NOT_TESTED` clock in the kill condition starts when the 038
   ledger goes live **and** the loop that runs `PM_fair_value_arb` is running.
   Either one alone is not enough. If the clock is started on the ledger date
   while the loop is down, the experiment will record `NOT_TESTED` for a
   reason that has nothing to do with the hypothesis.
2. Do **not** satisfy the observation gap by adding `PM_fair_value_arb` or the
   forked arm to environment B. That book is mid-measurement on a
   survivors-only A/B and its results are never crossed with the main loop's;
   an arm added to it would produce observations that cannot be compared with
   the 33 time stops the thesis is built on.
3. The thesis figures are now **frozen**, not merely stale. The 16 recoverable
   observations and the +0.184 per share cannot grow until the loop runs
   again, so re-deriving them before then will reproduce the same numbers and
   should not be read as confirmation.

Restarting the loop is not this proposal's lane and this amendment does not
request it. It records that the experiment's clock has not started.
