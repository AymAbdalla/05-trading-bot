---
name: "pm_one_legged_pair_unwind_guard"
thesis: "This is a repair, not an edge claim. D-314 settled that PM_corridor_pair_live pairs a 15m leader with a final-5m opposite: two clocks, one close. The designed payoff is best_case_pnl_per_pair = 2.00 - pair_cost, so both legs winning is the point and the $1.00 floor is real. But the floor holds only if BOTH legs fill. The legs are sequential takers. When leg 1 fills and leg 2 does not, the position is a naked directional binary with no floor, and that is exactly the $4.20 unhedged loss already in the book. The participant creating this is our own execution path, not the market. There is no edge to estimate here because the strategy has never been asked the question this repair asks. Ruled but not executed item 1 in CLAUDE.md, the maker fill wiring, is the adjacent open item; this one is separate and does not need maker fills."
expected_edge_bps: null
kill_condition: "Instrument first, then judge. If, across 40 or more acted pair signals measured by forge_shadow_eval, the one-legged fill rate is below 5%, the guard is unnecessary overhead and should be reverted rather than kept. Conversely, if one-legged fills exceed 20% of acted pairs and the guard's forced unwind still leaves mean net P&L per one-legged event below -$1.00, the corridor pair family is not executable on sequential takers and retires under Convention 7 at 30 or more closed pair trades in forge_shadow_eval."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  This repair changes execution, not entry selection. Entry selection for PM_corridor_pair_live is unchanged.
  
  New execution contract:
  1. Before submitting leg 1, snapshot best ask and ask depth for BOTH legs and record pair_cost_expected = ask_15m + ask_5m on the signal row. If either leg lacks depth for the full intended size, do not submit leg 1 at all. Skip reason: pair_leg2_depth_insufficient_preflight. This is the cheap half of the fix and it costs nothing.
  2. Submit leg 1. On fill, immediately submit leg 2 as a taker at the market.
  3. Open a guard window of 10 seconds from leg 1 fill. Poll every loop tick.
  4. If leg 2 fills inside the guard window: the pair is complete. Record pair_cost_actual and hold both legs to resolution. Stop on each leg is 0.00, which is the resolution value of a losing binary share and is strictly below any legal entry price. Target is 1.00 on both legs, per the D-314 payoff.
  5. If leg 2 does NOT fill inside the guard window: unwind leg 1 at the best bid immediately and record exit reason sell:one_legged_unwind, together with leg1_entry_px, unwind_bid_px, and the realized round-trip cost. Do not hold a one-legged position to resolution. That is precisely the trade that produced -$4.20 with no floor.
  6. If the unwind itself cannot fill because there is no bid, record no_bid_to_unwind, hold to resolution, and flag the position as UNHEDGED on the position row so it is never pooled with genuine pairs in any later analysis. A silent continue here is a missing number, per convention 20, and this is exactly the site where it would happen.
  
  No change to not_final_third_of_15m, late_in_window, lead_below_zone, or the ask caps. Those are the clock gates and they are a separate question with 3,239 and 1,142 evaluations behind them.
data_requirements: |
  1. Best ask and ask depth on both legs at signal time. HAVE IT partially. The loop already computes ask_5m_above_cap and ask_15m_above_cap so both asks are read, but there is no evidence in the brief that ask DEPTH is checked per leg before leg 1 submits. insufficient_ask_depth exists as a global skip reason at 1,784 rows, so the depth field is available; the pre-flight check on BOTH legs is what does not exist.
  2. Per-leg fill confirmation with timestamps. DO NOT HAVE IT in any form this brief can see. The corridor evidence shows two position rows from one acted signal, but nothing links them as legs of the same pair or records whether the second leg filled. The vault card states plainly that whether an executed pair ever cost under $1.00 is NOT_MEASURED, and the reason it is not measured is that the pair linkage is not logged. That missing linkage is the core of this repair.
  3. A pair_id joining the two legs on the positions table. DO NOT HAVE IT. This is the single most important new field and everything else depends on it.
  4. Best bid on leg 1 during the guard window, for the unwind. DO NOT HAVE IT logged. The loop reads the book so the value is obtainable, but there is no spread or bid column on the trade rows. Both vault notes call this out as NOT_MEASURED.
  5. A resting-order lifecycle across loop cycles. NOT NEEDED for this repair. Both legs are takers, which is why this is buildable now and the maker fill wiring is not.
  6. backtest/validate_harness.py exiting 0. HAVE IT as a gate, per convention 1.
markets: "Polymarket crypto up/down pairs on BTC, ETH, SOL. The 15m leader leg plus the final-5m opposite leg, which is what PM_corridor_pair_live actually trades."
kind: repair
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

## What is broken

PM_corridor_pair_live is a two-legged strategy executed as two sequential taker orders and logged as two unlinked position rows. Everything downstream inherits that.

D-314 settled the payoff: the pair is the 15m leader plus the final-5m opposite, two clocks settling off one close, fair value is 1.00 + P(corridor), and best_case_pnl_per_pair = 2.00 - pair_cost. Both legs winning is the designed payday, not an anomaly. The 1.21 pair receiving 2.00 was a +0.79 profit.

The surviving gap is narrower and it is an execution gap. The floor exists only when both legs fill. Sequential takers mean leg 2 can miss. A one-legged fill is a naked directional binary at 0.84 with a stop at 0.00, which is the -$4.20 row.

Today we cannot even count how often that happens, because no pair_id joins the legs.

## Why expected_edge_bps is null

Convention 11: NOT_TESTED means could not run, and a strategy that has never evaluated its own condition has no knowable edge. The one-legged fill rate has never been measured. Attaching a bps number to a repair whose entire purpose is to produce a measurement would be inventing the thing the repair is meant to discover. Null is the honest value and the schema requires it for kind repair.

## Build order, and this order matters

**Stage 1, log only, no behaviour change.** Add pair_id to the positions table. Add leg_index, leg_target_px, leg_fill_px, leg_fill_ts, and leg2_latency_ms. Add pair_cost_expected at signal time and pair_cost_actual at completion. Snapshot best bid and best ask on both legs at signal and at each fill. Change no execution logic whatsoever. Run until 40 acted pair signals accumulate.

This stage alone answers three open questions that no amount of argument settles: how often leg 2 misses, how long it takes when it lands, and what pair_cost_actual really is against binned fair value of 1.00 + P(corridor).

**Stage 2, set the guard from the observed latency distribution.** Only after stage 1 has data. If the 95th percentile leg-2 latency is 2 seconds, the guard is 4 seconds, not the 10 written above. The 10 in entry_exit_rules is a placeholder and is labelled as such in why_it_might_fail. Do not ship stage 2 with an invented timer.

**Stage 3, enable the unwind.** Compare mean net P&L per one-legged event under unwind against the held-to-resolution rows from stage 1. If unwinding is worse, do not unwind. The -$4.20 row is one observation and one observation does not establish that unwinding beats holding.

The cheapest piece, the pre-flight depth check on both legs before leg 1 submits, can ship with stage 1 because it prevents the failure rather than reacting to it and costs one book read.

## The frequency problem, stated plainly

At 0.021% act rate, 40 acted pairs needs roughly 190,000 evaluations. The corridor card already has a live frequency kill on the strategy itself: kill if signals_acted is still below 10 after 20,000 evaluations, currently 1 in 4,874, described in the card as on track to trigger.

So this repair may be instrumenting a strategy that gets killed on frequency before the instrument reads anything. That is a real possibility and it is the strongest practical argument for doing stage 1 and nothing else until the numbers arrive. The clock gates that starve it, not_final_third_of_15m at 3,239 and late_in_window at 1,142, together 89.9% of evaluations, are a separate question with far more blocked volume behind them, and this proposal deliberately does not touch them. Two changes at once and neither is measurable.

## What would change my mind

1. If stage 1 shows a one-legged fill rate under 5% across 40 or more acted pairs, revert the guard and keep only the logging. A guard for a 2% event is overhead.
2. If stage 1 shows leg 2 fills within one loop tick essentially always, the guard is unnecessary and the -$4.20 row needs a different explanation, which would itself be worth knowing.
3. If pair_cost_actual comes in above 1.00 + P(corridor) on most acted pairs, the entry gate pair_cost_above_binned_fair is not doing its job and that is the bug, not the fill path.

Named harness: backtest/validate_harness.py must exit 0, per convention 1. Scoring for the kill condition is forge_shadow_eval over db/trading.db.

## Why this might fail

The strongest argument against: this repair may be measuring a phenomenon that barely happens, at real cost. PM_corridor_pair_live has 1 acted signal in 4,874 evaluations, an act rate of 0.021%. At that rate, reaching 40 acted pairs takes roughly 190,000 evaluations. The instrumentation could sit there for the entire life of the strategy and never collect a sample large enough to say anything. The kill condition is written to force that admission rather than hide it, but writing an honest kill condition does not make the sample appear.

Second, the 10 second guard window is an invented number. Nothing in the evidence measures leg-to-leg fill latency, because leg linkage is not logged, which is the same gap this repair exists to close. 10 seconds could be far too long, in which case we hold naked risk we meant to avoid, or too short, in which case we unwind pairs that would have completed on the next tick and pay a round trip for nothing. The honest position is that the first version of this guard should LOG the leg-2 fill latency without unwinding at all, run for 40 acted pairs, and only then set the threshold from data. A guard that fires on a made-up timer is a new failure mode wearing the costume of a fix.

Third, and most seriously: the unwind pays exactly the ASK-to-BID round trip that killed the fair_value family, 616 trades and -$338.60 in 2026-08-18-fair-value-arb-spread-problem.md. If one-legged fills are frequent, this repair converts a rare large loss into a frequent small one, and the frequent small one may be worse in aggregate. That is not a hypothetical: it is the exact arithmetic that made _hft the worst variant in its family at -$0.7842 per trade. The kill condition's -$1.00 per one-legged event bound exists to catch this, but the possibility that unwinding is worse than holding is real and is not settled by this document.

Fourth, the corridor family is -$4.55 across 9 closed trades. Repairing the execution of a family that may have no edge is work spent on the wrong layer. The counterargument is that you cannot measure the edge at all while one-legged fills contaminate the sample, which is why this is a repair and why expected_edge_bps is null.

## What past failure this addresses

This targets hypothesis_graph id 147 (PM_corridor_pair, failure_mode unclassified, 5 of 8 closed trades unclassified by agents/critic.py) and id 148 (PM_corridor_collector, unclassified, 4 of 4). Both are classified unclassified, and unclassified is what a critic returns when the trade rows do not contain the field that would explain the outcome. For a two-legged strategy logged as two unlinked single-leg rows, unclassified is the expected output. This repair supplies the missing field.

The governing document is the strategy card corridor_pair_live.md, which carries the D-314 correction at the top and states the gap in one sentence: the $1.00 floor holds only if BOTH legs fill, legs are sequential takers, so a one-legged fill has no floor, and that is the $4.20 unhedged loss. The card's what-would-revive-it item 1 asks to settle whether the two legs are a complementary pair by reading the position rows, and calls it a read of existing data rather than new research. This repair is the execution-side counterpart: the read cannot be done today because no pair_id links the rows. The vault note 2026-08-18-corridor-pair-works.md makes the same point from the other direction, recording 0 of 1 executed pairs verified against fair value and the fact that whether the strategy ever bought the guarantee it claims is NOT_MEASURED.

It respects that note's explicit prohibitions rather than walking into them. Prohibition 3 says do not lower edge_below_floor or loosen pair_cost_above_binned_fair, which block 10 and 4 evaluations out of 4,874; this repair touches neither. Prohibition 4 says do not propose maker orders or a spread filter as the fix for this family, because fees are $0.00 and exits are at resolution; this repair uses takers on both legs and adds no spread filter. Prohibition 6 says the stop must be 0.00; the stop here is 0.00 on both legs. Prohibition 1 says do not claim risk-free profit without a logged executed pair meeting its fair-value bar; this document claims no edge at all and records expected_edge_bps as null.

It is also deliberately NOT the repair the deterministic half of Forge already writes. Those cover shadow_unblock_liq_cascade_chaser, shadow_unblock_smart_money_copy and shadow_unblock_weather_arb, which are DATA_BLOCKER unblocks on strategies that never evaluated their condition. PM_corridor_pair_live evaluates its condition 4,874 times and acts. Its problem is at the fill layer, not the input layer.

## Forge warnings (non-blocking)

These used to be refusals. They no longer block a proposal, and they are recorded here so the information survives the downgrade.

- **no_graveyard_link_warning**: no related graveyard finding. Expected for PREDICTION_MARKET, EVENT and SPORTS: the graveyard has no rows in those classes.
