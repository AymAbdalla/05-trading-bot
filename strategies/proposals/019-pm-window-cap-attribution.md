---
name: "pm_window_cap_attribution"
thesis: "The single largest skip reason in the entire book is `max_trades_this_window` at 36,781 counts, 20.0% of all skips, and on the fair-value family alone it is 32,424 evaluations or 77.3% of that family's total. Every document in the vault says the same thing about it: we do not know what it is costing. The strategy card says 'Whether the cap is protecting the account or suppressing a profitable tail is NOT_MEASURED'. The cycle summary lists it as open unknown number 5 and says 'the answer differs per strategy'. Right now the cap is the largest single force shaping this book's P&L and nobody has measured its sign. That is not a strategy question, it is an attribution question, and it is answerable from data we are already throwing away. The mechanism is this: the cap fires AFTER a strategy has decided to trade. It is a GENUINE skip, not a data blocker, which means the strategy evaluated its condition, said yes, and was refused. Every one of those 36,781 refusals is a counterfactual trade with a knowable outcome, because the window it belonged to has since resolved. We can price all 36,781 of them against the actual resolution and learn the sign of the cap for free, with zero capital at risk."
expected_edge_bps: null
kill_condition: "This is an attribution experiment, so its kill condition is on statistical power rather than P&L. Abandon it if fewer than 500 capped evaluations can be matched to a resolved window with a recoverable entry ask in forge_shadow_eval. Below 500 matched rows the per-strategy split is under the 30-observation bar for most of the 6 affected strategies and the answer would be a shrug per convention 7. If it does run, the decision rule is: for any strategy whose counterfactual capped-trade P&L is below 0 cents per position across 200 or more matched rows in forge_shadow_eval, the cap is PROTECTIVE and must not be raised; above 0 cents on 200 or more, it is SUPPRESSIVE and a raise becomes proposable with a real number attached."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "This experiment executes ZERO live entries. It is a replay over logged rows and it risks no capital. The replay procedure: Step 1, select every skip row with reason `max_trades_this_window`, keyed by strategy, asset, market slug and timestamp. Step 2, for each, recover the ask that was live at the moment of the refusal. If the ask was not logged on the skip row, that row is unrecoverable, and the count of unrecoverable rows must be reported as a number rather than silently dropped, per convention 20. Step 3, join to the window's actual resolution, which is 1.00 or 0.00 and is already known for every window in the log. Step 4, compute counterfactual net per position as resolution minus entry ask, at the same 18 to 20 share size the fair-value family actually used, with the same $0.00 fees the log shows. Step 5, split the result BY STRATEGY, because the cycle summary is explicit that the answer differs per strategy, and pooling 6 strategies would average a protective cap and a suppressive one into a meaningless middle. Step 6, split each strategy's result by the ordinal position of the refused trade within its window: the 4th trade in a window may have a different sign from the 12th, and if the counterfactual P&L decays with ordinal position then the cap has an optimal value rather than a binary right-or-wrong. On the hypothetical entries used in the replay, the stop is 0.00 and the target is 1.00, matching the recorded resolution bounds on every real position row in the book and satisfying convention 8, although no order is ever sent."
data_requirements: "Skip rows keyed by reason, strategy, asset and timestamp: HAVE IT. The counter is categorised and reads 36,781, and convention 20 confirms every skip is counted and categorised rather than silently continued. Window resolution outcome: HAVE IT, every closed position in the book resolved at 0.00 or 1.00 and 784 closed positions exist. The ask at the moment of refusal: PROBABLY DO NOT HAVE IT. This is the load-bearing gap and I am flagging it plainly. The strategy card's 'What would revive it' item 2 says outright that there is no spread column in the evidence and that bid, ask and spread must be logged at entry and exit. If the ask is not on the skip row, this experiment cannot price the counterfactual and reduces to a logging change plus a wait. That possibility is why this is an experiment with a null edge and not an edge hypothesis. Ordinal position of the refused trade within its window: DO NOT KNOW. It may be derivable by counting acted signals per window per strategy from existing rows. If it is not, step 6 drops and steps 1 through 5 still stand."
kind: experiment
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

## Why the edge is null and must stay null

Rule 3 requires kind experiment to record expected_edge_bps as null. That is the right answer here on the merits and not merely on the rule: this experiment does not trade. It has no edge because it takes no position. Its output is a SIGN, positive or negative, on a counterfactual population, per strategy.

## The arithmetic that is available, which is a count and not an edge

36,781 capped evaluations, 20.0% of all skips. On the fair-value family specifically the strategy card gives 32,424 of 41,969 family evaluations, or 77.3%.

Against that, the family actually took 615 trades and lost $337.63, or -$0.549 per trade averaged across the family.

If the capped population had the same per-trade P&L as the acted population, then 32,424 additional trades at -$0.549 would be a loss of roughly **$17,800**. That is the number that makes the cap the most important risk control in the book, and it is why nobody should raise it before this measurement exists.

But that projection assumes the capped and acted populations are identical, and the whole point of the experiment is that they are NOT: capped trades sit later in their windows by construction. The $17,800 is an illustration of the stakes, not an estimate of the answer.

## Why this is worth a slot even though it might be unrunnable

Three separate vault documents name this as an open unknown, and the cycle summary's very first 'What to try next' item says no proposal should be graded against the P&L until the data is reconciled. A book where the largest single force on P&L has an unmeasured sign is a book where every strategy verdict is provisional in a way nobody is accounting for.

If it turns out the ask is not logged on skip rows, the experiment fails fast against its own 500-row kill condition and the deliverable becomes a specific, small logging change: ask, bid, book depth and within-window ordinal on every GENUINE skip row. That is a cheap failure and an actionable one.

## What would change my mind

If the replay shows the capped counterfactual is strongly negative for every one of the 6 affected strategies, the cap is settled as protective, the question closes permanently, and future proposals should stop listing it as an unknown. If it splits by strategy, which the cycle summary predicts, then the cap should become per-strategy rather than global, and that becomes a proposable change with a number behind it.

If the ordinal split in step 6 shows counterfactual P&L is flat across positions 1 through N, that undermines my own confounding worry and makes the naive comparison more trustworthy than I have argued.

## What I am deliberately not doing

I am not proposing to raise the cap. I am not proposing a variant of any fair-value strategy. I am not proposing to loosen `edge_below_threshold`, which blocks 1,421 of 41,969 family evaluations at 3.4% and has no volume behind it, and which convention 17 and both vault documents forbid touching.

## Convention check

- Convention 1: not durable until `backtest/validate_harness.py` exits 0.
- Convention 5: null edge, correctly, because this takes no position.
- Convention 7: the 500-row power bar and the 200-row decision bar are both set above 30 deliberately.
- Convention 8: stop 0.00 on the hypothetical entries, though none are sent.
- Convention 11: capped evaluations are refused entries, not declined ones. The strategy said yes and the gate said no.
- Convention 17: I loosen nothing. I measure before anyone proposes loosening.
- Convention 20: unrecoverable rows must be reported as a count, never silently dropped.

## Why this might fail

The strongest argument against this is that it is very likely to be unrunnable on the data as it sits, and I would be spending a proposal slot on a logging request dressed up as an experiment. The strategy card says there is no spread column. If there is no ask column on a skip row either, then step 2 fails on all 36,781 rows, the kill condition trips at the 500-row bar immediately, and the deliverable shrinks to 'add three columns to the skip logger'. That is a real outcome and I cannot rule it out from the brief. Second, the counterfactual is not honest even if the data exists. A refused trade is not a trade that would have happened at the logged ask. It would have consumed book depth, and if the cap were lifted, all 36,781 would have competed for the same books simultaneously. `insufficient_book_depth` already fires 831 times and `insufficient_ask_depth` 902 times WITH the cap in place. The replay assumes infinite liquidity at the top of book and therefore systematically overstates the case for lifting the cap. Third, and worst: the replay's answer may be biased by the very thing it is measuring. The cap fires after the first few trades in a window, so capped trades are systematically LATER in their windows than acted ones. Later trades in a binary window have different odds by construction, since less time remains for a reversal. So the capped population and the acted population are not comparable samples, and a naive comparison of their P&L is confounded by clock position. My step 6 ordinal split is an attempt to control for that, but it is a partial control, not a fix. Fourth, there is an unresolved data-integrity problem sitting underneath all of this: the cycle summary flags an unexplained equity step back to exactly $1,000.00 at 11:26 and says plainly that until it is explained, the P&L numbers do not reconcile. If the underlying position table has a reset in it, my replay inherits that corruption.

## What past failure this addresses

This does not re-propose any buried hypothesis. It targets the explicit open unknown number 5 in `2026-08-18-cycle-001-day-1-lessons.md`: 'What max_trades_this_window is actually costing. It is the largest skip reason in the book at 36,239 across 6 strategies, 19.9% of all evaluations. Whether it is protecting the account from more fair-value losses or starving a profitable strategy is NOT_MEASURED, and the answer differs per strategy.' It also answers the corresponding NOT_MEASURED in `fair_value_arb.md` under Assessment: 'Whether the cap is protecting the account or suppressing a profitable tail is NOT_MEASURED, but given the family is -$337.63 on the trades it did take, the prior should be that the cap is helping.' Critically, this proposal does NOT ask to raise the cap. Both that card and the cycle summary forbid it: the card's 'What not to propose' says 'Do not raise max_trades_this_window. It blocks 77.3% of evaluations on a family that is -$337.63 on the trades it did take. That is a request for a larger loss.' I agree with that prohibition and I am not asking for an exception to it. I am asking to MEASURE the sign so that the prohibition rests on a number instead of a prior. Regarding hypothesis_graph, the capped strategies are the fair-value family, id 113 PM_fair_value_arb and id 114 PM_fair_value_arb_hft, both failure_mode stop_too_tight, plus id 112 PM_dip_arb, same failure_mode. This experiment does not attempt to rescue any of them. If the replay shows the capped counterfactual trades were also losers, that is confirmation their kill conditions were correctly triggered, which is a useful result in the opposite direction from a rescue.

## Forge warnings (non-blocking)

These used to be refusals. They no longer block a proposal, and they are recorded here so the information survives the downgrade.

- **no_graveyard_link_warning**: no related graveyard finding. Expected for PREDICTION_MARKET, EVENT and SPORTS: the graveyard has no rows in those classes.
