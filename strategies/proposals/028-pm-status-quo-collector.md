---
name: "pm_status_quo_collector"
thesis: "A top-500 Polymarket politics wallet (Llalalala, rank #129) made $412,688 over 21 months with one play: systematically buying NO at 80-90 cents on 'status quo' questions (Putin stays in power, Iranian government remains intact, world looks the same in December). The market overprices change on stable geopolitical questions ('selling panic'); buying NO at 80-90c harvests 10-25% yield with the understanding that one tail event wipes months of gains. The wallet's own rule, learned from its single $93k loss: NEVER bet on the black swan side. This is the inverse shape of every strategy currently in the registry: instead of many small losing bets hoping for rare winners, it is many small winning bets hoping to survive the rare loss. Running both shapes in shadow gives Forge the full payoff distribution to reason over."
expected_edge_bps: null
kill_condition: "After the political space is live and this strategy is polled there, if PM_status_quo_collector does not enter on at least 1% of evaluations over 500 or more shadow cycles as measured by agents/forge_shadow_eval.py against db/trading.db, or resolves fewer than 100 positions, it is NOT_TESTED and stays unrated. Once 100+ positions resolve: if net PnL is below 0 or the largest single loss exceeds 40% of starting bankroll (the tail that the whole design accepts), the strategy is retired. The tail loss is expected and is not by itself the kill trigger; what kills it is a loss rate higher than the 80-90c entry band implies, which means the 'status quo' classification is wrong, not unlucky."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "Buy NO on political markets where: (1) implied NO price is between 0.80 and 0.90, (2) the question is a dated status-quo statement ('X will/will not happen by date Y'), (3) the resolution is binary and public. Position sizing: fixed small premium, capped so the account survives its own tail (a single NO position going to zero must lose less than 5% of bankroll). Scale-up only when the market drifts toward YES (price improves): add size at 82c, 89c like the reference wallet, never chase above 0.90 where the remaining yield no longer compensates the tail. Hold to resolution (paper mode has no sell path). NEVER take the YES side of a black-swan question, and never take a NO position below 0.80 on a question whose tail is regime change: the reference wallet's only catastrophic loss came from exactly that."
data_requirements: "NEW: political market discovery is already wired (space exists, 100 markets per sweep). BLOCKER: a status-quo classifier that reliably separates 'stable geopolitical question' from 'binary event with real change probability' using only the market question text and resolution date. Until that classifier exists the strategy is NOT_TESTED (convention 11). The classifier must be able to say 'I do not know' rather than force a status-quo label on an ambiguous question."
related_graveyard_findings: "None. The graveyard has no status-quo or tail-selling family; every existing strategy is a taker of tail risk, not a seller. D-268: every Polymarket strategy is NOT_TESTED until backtest/polymarket_harness.py scores it."
kind: new
status: PROPOSED
source: "Raven analysis of r/PredictionsMarkets post 1vqyxpx (2026-08-17): wallet Llalalala, 21 months, 119 predictions, $412,688. Iran position scale-up ($1.5k@50c, $94k@82c, $106k@89c, closed $620k turnover at 93c for $11.8k) and the single $93k loss from breaking the no-black-swan rule. Thread comments confirm the shape: 'selling options with more steps', 'reverse lottery', survivorship-bias warnings, and the ~1.8% ROI-on-volume reality."
forge_warnings: "Survivorship bias is the biggest trap in this source class: the top-500 list only shows the survivors, and the comments document the erased accounts (the million-dollar sports better who lost it all when 'the impossible' happened). The strategy MUST be evaluated on whether the 80-90c band is mispriced on stable questions, not on the reference wallet's PnL. Also note the honest yield reality: $412k over 21 months on ~$2M of turnover is a grinder's edge, not a forecaster's. Position sizing is the entire game: the tail loss is guaranteed to eventually happen, and the design accepts it only because the cap keeps it survivable."
---

## What this is

A tail-risk SELLER, the inverse of everything in the registry.

Every existing strategy buys cheap upside (streak, dip, corridor) — small premiums, high loss rate, hoping for the rare big winner. This strategy buys expensive safety: NO at 80-90c on stable political questions, collecting 10-25% when nothing changes, accepting that one tail event will eventually happen and cost the position.

## Why the reference wallet's play is a real pattern

The Iran scale-up is the evidence: $1.5k at 50c, then $94k at 82c, then $106k at 89c — adding as the market drifted. The wallet did NOT chase above 90c, and it held to resolution (or sold into the final drift). And its one $93k loss came from betting the Supreme Leader would be gone by March 31 — the black-swan YES side — which it never did again. The rule set is learnable and testable.

## The honest limits

- ~1.8% ROI on volume. This strategy is a yield grinder, not a moonshot. It belongs in shadow as a data source for Forge, not as a get-rich play.
- The tail is guaranteed, eventually. The design only works if the position cap makes the tail survivable. A single NO going to zero must cost less than 5% of bankroll.
- The status-quo classifier is the whole edge and the whole risk. A question that LOOKS stable but isn't (a government that looks stable until it collapses) is where the tail lives. The classifier must default to 'I do not know'.
- Survivorship bias: top-500 lists only show survivors. This proposal rests on the pattern (selling overpriced change on stable questions), not on the wallet's PnL.

## What is needed to implement

1. Status-quo classifier: given market question text + resolution date, decide stable-geopolitical vs binary-event vs unknown. Must have an honest unknown state.
2. Wire into the political space (already exists, 60s cadence, currently smart_money_copy only).
3. Position cap: max 5% of bankroll per position, no YES side ever, no entry below 0.80 on regime-change questions.
4. Shadow wiring: register, add to loop, size at minimum until 100+ resolutions.

## Queue note

Queued behind proposal 027 (smart_money_callers). Both are additive, independent of the 20 existing strategies, and both use the new market spaces (political for this one, stock-event for 027). Neither requires touching existing strategy code.
