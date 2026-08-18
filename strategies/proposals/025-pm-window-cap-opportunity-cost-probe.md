---
name: "pm_window_cap_opportunity_cost_probe"
thesis: "max_trades_this_window is the single largest skip reason in the entire shadow loop: 47343 counts, 20.7% of all skips, classified GENUINE. On the fair_value_arb family alone it blocks 32619 of 42164 evaluations, 77.4%. Every judgement we have made about that family, and about several others, was made on the 2% of signals that happened to arrive before the cap was hit. Nobody has ever measured what the blocked 77% would have done. The participant creating the inefficiency here is us: the cap is an ordering artefact, not a selection rule. It admits signals by arrival time within a window, not by quality, so the sample we grade every strategy on is a time-ordered prefix of the signal stream rather than a random or a best-first sample. If early-in-window signals differ systematically from late ones, and on a 5-minute binary where the strike is fixed at window open they almost certainly do, then every per-trade P&L number in the vault is measured on a biased subsample. This is a measurement, not a trading idea, and it is the measurement that decides whether several existing verdicts are safe."
expected_edge_bps: null
kill_condition: "Run agents/forge_shadow_eval.py over db/trading.db after shadow-only counterfactual logging is added. Kill this probe if it produces fewer than 500 logged counterfactual signals across 20000 evaluations, which means the instrumentation is not capturing the blocked pool. If it does produce a sample, the decision rule is: if the counterfactual P&L per blocked signal differs from the acted P&L per signal by less than 5 cents over 500 or more blocked signals, declare the cap unbiased, record it in the hypothesis graph, and never re-open this question. Both numbers are computed by forge_shadow_eval and backtest/validate_harness.py must exit 0 first."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  This probe takes NO real positions and books NO entries. That is the point of it, and it is why it cannot lose money. The rules describe what gets LOGGED, not what gets bought.
  
  1. When any strategy would have acted but the loop returns max_trades_this_window, do not skip silently. Write a counterfactual row: strategy, market, timestamp, seconds remaining in window, the side it wanted, the best bid and best ask at that instant, and the intended size.
  2. At window resolution, score every counterfactual row against the actual resolution outcome. Fill price is the ask that was live at the counterfactual timestamp, exit is 1.00 or 0.00 at resolution. That is a shadow-of-a-shadow fill and it must be labelled as such in the row, never merged into the real trades table.
  3. Because no position exists, there is no stop. To be explicit about convention 8: this proposal books zero entries, so the entry-plus-stop requirement is vacuous here. If this probe is ever converted into a strategy that books entries, that successor must carry a stop strictly below entry, and a losing binary share is 0.00.
  4. Partition the counterfactual rows by seconds-remaining decile within the window and report P&L per signal per decile. This is the actual output. A flat curve across deciles says the cap is unbiased. A monotone curve says arrival order is selecting.
  5. Hard cap on the probe itself: log at most 200 counterfactual rows per strategy per hour, so this cannot flood the signals table, which is already at 180k rows and carries an open retention question.
data_requirements: "The skip event with its reason string: WE HAVE THIS, max_trades_this_window is already counted 47343 times, so the code path is live and only needs to write a row instead of incrementing a counter. Best bid and best ask at skip time: WE HAVE THIS, the book is already read before the cap is evaluated. Market resolution outcome: WE HAVE THIS, engine/polymarket/market_resolution.py exists in the working tree and the corridor family already exits at resolution. Seconds remaining in window: WE HAVE THIS, multiple clock gates such as not_final_third_of_15m, late_in_window and past_quote_window already compute it. What we DO NOT have is a counterfactual table and the scoring job. Nothing here needs a new feed, a new venue, or a new API. It is instrumentation on an existing branch."
markets: "All Polymarket up/down markets already in the shadow loop: btc, eth, sol, 5m and 15m. No new market data required."
kind: experiment
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

## What this is

Instrumentation, not a strategy. It measures whether the largest skip reason in the loop is a neutral throttle or a biased sampler.

## The arithmetic

expected_edge_bps is null and the kind is experiment, deliberately. There is no edge to estimate here because this books no positions. Convention 11: a condition that has never been evaluated has no knowable edge, and inventing one is worse than admitting it is unknown.

The scale of what is unmeasured is the argument for running it:

- max_trades_this_window: 47343 skips, 20.7% of all skips loop-wide.
- On fair_value_arb alone: 32619 of 42164 evaluations, 77.4%.
- The parent variant acted 259 times out of 12332 evaluations, an act rate of 2.10%.

So the family verdict of -$162.99 on 259 trades rests on 2.10% of the signal stream, selected by arrival time. That is not a random sample and it has never been checked for bias.

## Evidence leaned on

- Shadow skip table: max_trades_this_window 47343, share_of_skips 0.207425, class GENUINE.
- Strategy card fair_value_arb.md: "Whether the cap is protecting the account or suppressing a profitable tail is NOT_MEASURED."
- Vault note 2026-08-18-corridor-pair-works.md: 89.9% of corridor_pair_live evaluations die on two clock gates, and the note names that pool as the only one large enough to move the trade count into verdict range.

## What would change my mind

If the decile curve is flat, the cap is unbiased, the existing verdicts stand unqualified, and this question closes permanently with a row in the hypothesis graph. That is a good outcome and it is the one I expect. A monotone curve is the outcome that matters, and even then the counterfactual fill assumption means a small effect should be treated as noise.

## Explicit non-goal

This proposal does NOT ask to raise max_trades_this_window and must not be cited as support for doing so. Item 5 of the vault lesson's prohibition list forbids that on the fair_value_arb family, and that prohibition stands regardless of what this probe returns, because the family is negative on the trades it already took.

## Why this might fail

The strongest argument against this is that it may answer a question that does not matter. The fair_value_arb family lost $338.60 on the trades it DID take, and the card is explicit that the prior should be that the cap is helping. If the blocked signals are drawn from the same losing distribution, the counterfactual will simply show a larger loss, we will have spent engineering effort to confirm a filter is doing its job, and no strategy improves. The second argument is that the counterfactual fill is fiction. Scoring a blocked signal at the ask that was live at that instant assumes we could have been filled at that price for our full size, which ignores depth and ignores our own market impact. It will systematically flatter the counterfactual, so a small positive result should NOT be believed. Third, this adds write volume to a signals table already producing roughly 78k rows a day with an unresolved retention decision, which is why the 200-row-per-strategy-per-hour cap is in the rules and not optional. Fourth, and most seriously: a counterfactual that comes back positive creates enormous pressure to raise the cap, and both the vault lesson note (item 5 of What NOT to propose again) and the strategy card explicitly forbid raising max_trades_this_window on this family. This probe must be able to produce a positive number WITHOUT that being read as permission to raise the cap on a losing family. If that discipline cannot be held, do not run the probe.

## What past failure this addresses

This does not re-propose anything in the graveyard. It attacks the measurement layer under hypothesis_graph ids 112 PM_dip_arb, 113 PM_fair_value_arb and 114 PM_fair_value_arb_hft, all TESTED_FAILED with failure_mode stop_too_tight on samples of 33, 120 and 69 closed trades. Every one of those verdicts was reached on a time-ordered prefix of the signal stream: vault note 2026-08-18-fair-value-arb-spread-problem.md records max_trades_this_window blocking 32619 of 42164 family evaluations, 77.4%, and the strategy card fair_value_arb.md states plainly that whether the cap suppresses a profitable tail is NOT_MEASURED. The card also names this as an open item and warns against acting on it blind. This proposal converts NOT_MEASURED into a number without touching the cap, which is the one move both notes permit. It also touches the corridor family: vault note 2026-08-18-corridor-pair-works.md shows PM_corridor_pair_live dying 89.9% on two clock gates, not_final_third_of_15m at 3239 and late_in_window at 1142 of 4874 evaluations, and names attacking that blocked pool as the only route to verdict-range volume. The seconds-remaining decile output here is exactly the evidence that question needs, so one instrument answers both. This is distinct from the deterministic repairs already in the evidence, shadow_unblock_grid_hedge, shadow_unblock_liq_cascade_chaser, shadow_unblock_smart_money_copy and shadow_unblock_weather_arb: each of those restores a missing INPUT to one blocked strategy, while this measures a GENUINE gate that is working as designed and whose selection effect has never been quantified.

## Forge warnings (non-blocking)

These used to be refusals. They no longer block a proposal, and they are recorded here so the information survives the downgrade.

- **no_graveyard_link_warning**: no related graveyard finding. Expected for PREDICTION_MARKET, EVENT and SPORTS: the graveyard has no rows in those classes.
