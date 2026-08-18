---
name: "pm_maker_rebate_corridor_quote_ladder"
thesis: "The Polymarket 5m/15m up/down book is quoted almost entirely by a small set of automated market makers plus a stream of retail takers who cross the spread to express a directional view on BTC, ETH or SOL over the next few minutes. The taker pays the full ask-to-bid gap; the resting quoter collects it. Our own evidence says this transfer is large: 616 fair_value_arb trades paid the round trip and lost $338.60 with $0.00 in fees, and the inverse variant, which took the opposite side of every signal, still lost money at a 48.1% win rate. That is a cost charged to whoever crosses, in both directions. The participant creating the inefficiency is the impatient retail taker in a 5-minute window: they have seconds to express a view and no incentive to work an order, so they lift the offer. It persists because the window is short, the notional is tiny, and there is no cross-venue arbitrageur competing the quote away. This proposal does not add a new directional model. It takes the side of the transfer that our data says is being paid, by resting quotes instead of crossing. It is an experiment, not an edge hypothesis, because the paper adapter has never simulated a single maker fill in the shadow loop, so the fill rate is unknown and any edge number would be invented."
expected_edge_bps: null
kill_condition: "Run agents/forge_shadow_eval.py over db/trading.db after the maker path is wired. Kill this experiment if EITHER (a) fewer than 100 resting quotes are simulated as filled after 20000 evaluations, which means the fill model is not producing a testable sample, OR (b) net P&L per filled quote is below 0.0 cents over 200 or more filled quotes. Both thresholds are checked by forge_shadow_eval, and backtest/validate_harness.py must exit 0 before either number counts."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  1. Only arm in a window whose remaining time is between 240s and 60s for a 5m market, or between 600s and 120s for a 15m market. Outside that band, skip with reason quote_outside_arm_band.
  2. Read the full book. If either side is missing, skip with no_asks or no_bids. If best_ask minus best_bid is less than 2 cents, skip with book_too_tight_to_arm, because there is nothing to capture.
  3. Place ONE resting BUY at best_bid, size 5 shares, on the side whose mid is between 0.20 and 0.80. Never quote outside that band: a 0.05 contract cannot pay a 1 cent capture and a 0.95 contract has no room to fall.
  4. Never quote both sides of the same market in the same window. One resting order per market per window, hard cap.
  5. Fill rule, enforced by the paper adapter and not by this strategy: the order fills only when a trade prints strictly through our price, and only for the size remaining after the queue ahead of us at that price level is consumed. A print AT our price with queue ahead of us does not fill us.
  6. On fill, immediately place a resting SELL at entry plus 2 cents. Cancel and re-place once if the book moves such that our sell is more than 3 cents inside the best ask.
  7. STOP, strictly below entry: if the best bid falls to entry minus 4 cents, cross out at the bid immediately. This is a real stop and it is strictly below the entry price by construction. If entry is at or below 0.04, the stop is 0.00, which means the position is held to resolution and resolves worthless. Log which of the two stop regimes applied.
  8. TIME STOP: at 30s before window close, cancel any unfilled resting order. If filled and still open, cross out at the bid. Do not carry a maker position into resolution.
  9. HALT: engine/halt.py is_halted() refuses new resting BUYs and cancels existing ones. This is already the behaviour of the maker path and must not be weakened.
data_requirements: "Full order book with best bid, best ask and size at each level: WE HAVE THIS, the shadow loop already reads it and the skip counters book_too_tight_to_arm (6350) and book_not_wide_enough (5254) prove it is being evaluated. Trade prints with price and size for the queue model: WE HAVE THIS in the paper adapter, which per the project notes already implements a strict-cross, queue-aware maker fill. Window open and close timestamps: WE HAVE THIS. What we DO NOT have is the WIRING: shadow_loop short-circuits every QUOTE into SKIP_MAKER and never calls the adapter's maker path, which is why maker_fill_not_simulated appears 3130 times in the skip table as a SIM_LIMIT. That missing wiring is the whole reason this is an experiment rather than an edge hypothesis. We also do NOT have a logged spread column on acted signals, which both vault notes flag as NOT_MEASURED, so this proposal requires bid, ask and spread to be written on every quote placement and every fill."
markets: "Polymarket btc-updown-5m, eth-updown-5m, sol-updown-5m, and the 15m parents, on the three SHADOW_ASSETS only"
kind: experiment
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

## What this is

An experiment to take the paid side of the round trip on Polymarket 5m and 15m up/down binaries by resting quotes instead of crossing the spread.

## The arithmetic, and why it is not an edge estimate

A 2 cent capture on a 0.35 contract is 2/35 = 5.71%, or 571 bps gross. Against the PREDICTION_MARKET floor of 200 bps that has headroom. But the honest position is that this number is not an edge estimate, it is a capture size. Edge is capture times fill rate minus adverse selection cost times fill rate, and the fill rate is exactly the unknown here: shadow_loop has never simulated a single maker fill. maker_fill_not_simulated appears 3130 times in the skip table, classified SIM_LIMIT. Convention 11 says a strategy that never evaluated its condition has no knowable edge, so expected_edge_bps is null and the kind is experiment.

The geometry that matters more than the capture: target is entry plus 2 cents, stop is entry minus 4 cents. Break-even win rate is 4/(2+4) = 66.7%. That is a demanding bar and it is stated up front rather than buried. If the first 200 filled quotes come in below 60%, the 2/4 geometry is wrong and should be re-sized before the idea is judged, not after.

## Evidence leaned on

- Vault note 2026-08-18-fair-value-arb-spread-problem.md: 616 family trades, -$338.60, $0.00 fees, and the inverse variant at 48.1% win rate still losing $32.50. That is the argument that the cost is the round trip and not the model.
- Strategy card fair_value_arb.md, What would revive it, item 1: maker fill simulation is named as the only fix that addresses the diagnosed mechanism.
- Shadow skip table: maker_fill_not_simulated 3130 (SIM_LIMIT), book_too_tight_to_arm 6350, book_not_wide_enough 5254. The book gates are already firing, so the book data exists.

## What would change my mind

Two things. First, if the logged spread on acted signals comes back with a median under 1 cent, then the spread was never the cost, the fair_value_arb diagnosis is wrong, and this proposal is built on a misattribution. Both vault notes flag the spread as NOT_MEASURED and require that measurement before any spread-based fix. Second, if the first 200 simulated fills show a win rate materially below the fill-weighted rate of the taker side over the same windows, that is adverse selection showing up in the data and the maker side is not free money on this book.

## Convention notes

- Convention 8 is satisfied: the stop at entry minus 4 cents is strictly below entry. The degenerate case where entry is at or below 0.04 is stated explicitly and logged separately rather than silently collapsed.
- Convention 20: every skip path above names its own reason string. quote_outside_arm_band, no_asks, no_bids and book_too_tight_to_arm are four separate counters, not one.
- Convention 1: backtest/validate_harness.py must exit 0 before any number here counts.

## Why this might fail

Adverse selection is the strongest argument against this, and it is not a small one. A resting bid on a 5-minute binary gets filled precisely when the market is moving against it. The taker who lifts our bid is selling because spot just moved through the strike, so our fill rate will be highest exactly when the fill is worst. That is the classic maker problem and nothing in our evidence says this book is friendly to it. The second argument is that our fill sample may never exist: the adapter requires a strict cross plus queue consumption, and if we are behind a large resting quoter at the same price for the whole window we get zero fills across thousands of evaluations, which produces NOT_TESTED rather than a result. The third is that a 2 cent capture on a 0.35 contract is 571 bps gross, which looks comfortable against the 200 bps floor, but the stop is 4 cents. At that geometry we need better than a 2-to-1 win rate to break even, and the fair_value_arb family showed win rates of 22% to 48% on this exact instrument. If the maker fill inherits the same hit rate the capture does not save it. Fourth, the paper adapter's queue model is a model. A simulated maker fill is not evidence that a real Polymarket resting order would have filled, and promoting this on paper results alone would be promoting the adapter's assumptions.

## What past failure this addresses

hypothesis_graph id 113 PM_fair_value_arb, id 114 PM_fair_value_arb_hft and id 112 PM_dip_arb are all recorded TESTED_FAILED with failure_mode stop_too_tight, and vault note 2026-08-18-fair-value-arb-spread-problem.md attributes the family loss to spread_eats_edge: 616 trades, -$338.60, $0.00 fees, and the inverse variant losing $32.50 at a 48.1% win rate. Both that lesson note and the strategy card fair_value_arb.md name the SAME fix as the only one that addresses the diagnosed mechanism: build maker fill simulation into the paper adapter and enter at bid instead of ask. The card lists it as item 1 under What would revive it, and explicitly calls it a paper-adapter task that also unblocks box_builder and grid_hedge. This proposal is that item, written as a strategy so the wiring has something to be tested against. It does NOT violate the note's prohibition list: rule 1 there forbids paying market orders on BOTH legs, and this pays a market order on neither leg at entry; rule 8 forbids quoting a spread NUMBER as a filter while the spread is NOT_MEASURED, which is why the 2 cent arm gate here is defined as a measurement to be logged and re-sized rather than a validated threshold, and why this is kind experiment with expected_edge_bps null. It is also distinct from the deterministic repair shadow_unblock_grid_hedge in the evidence: that repair unblocks an existing strategy's inputs, this specifies what a maker-side strategy should do once the path exists.

## Forge warnings (non-blocking)

These used to be refusals. They no longer block a proposal, and they are recorded here so the information survives the downgrade.

- **no_graveyard_link_warning**: no related graveyard finding. Expected for PREDICTION_MARKET, EVENT and SPORTS: the graveyard has no rows in those classes.
