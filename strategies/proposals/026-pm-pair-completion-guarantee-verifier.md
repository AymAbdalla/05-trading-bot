---
name: "pm_pair_completion_guarantee_verifier"
thesis: "The corridor family's entire justification is a structural identity: one side of a binary resolves to 1.00, so a complementary pair bought for less than 1.00 combined is locked-in profit. That identity is real arithmetic. What the trade log shows is that we have never bought it. The one observed pair cost 0.31 plus 0.90 equals 1.21, above par, and both legs exited at 1.00, which a genuine complementary pair cannot do. Verified executed pairs with combined cost strictly below 1.00: 0 of 1. Meanwhile corridor_collector lost both legs twice and corridor_pair took a one-legged fill that lost 4.20 unhedged. The participant behaviour that would create a real sub-par pair is a directional taker lifting one side of a thin book hard enough to push the complement below par for a few seconds, and it would persist because nobody is watching both tokens of a 5-minute market simultaneously. Whether that ever actually happens is unknown. This proposal is the instrument that finds out: it monitors both tokens continuously, logs every instant where ask_yes plus ask_no is below 1.00 at simultaneously available depth, and books nothing. Until that log is non-empty the corridor family has no verified thesis, and the second-biggest open question in the project stays open."
expected_edge_bps: null
kill_condition: "Run agents/forge_shadow_eval.py over db/trading.db against the pair-cost log. Kill this verifier and mark the corridor structural thesis FALSE AS IMPLEMENTED in the hypothesis graph if fewer than 20 sub-par instants are observed across 20000 paired book snapshots. If 20 or more are observed, the surviving question is depth: kill it anyway if fewer than 5 of those instants show at least 5 shares available on BOTH legs at the sub-par prices simultaneously. backtest/validate_harness.py must exit 0 before either count is believed."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  This verifier books NO entries in its first phase. Convention 8 is vacuous while nothing is bought, and that is stated rather than dodged. The phase-two rules below define what a successor WOULD do and carry a real stop.
  
  PHASE ONE, measurement only:
  1. On every loop tick, for each market, fetch the book for BOTH the Yes token and the No token at the same instant. If either book is missing, skip with reason pair_book_incomplete. That is its own counter and must not share one with no_asks (convention 20).
  2. Compute pair_cost = best_ask_yes + best_ask_no. Log the timestamp, market, both asks, both bids, and the size available at each best ask.
  3. Log EVERY snapshot, not only the sub-par ones. A distribution of pair_cost is the deliverable. Logging only the exceptions would make the base rate unknowable.
  4. Compute fillable_size = min(size_at_best_ask_yes, size_at_best_ask_no). A sub-par pair with 1 share on one leg is not a tradeable observation and must be recorded with its size so it can be filtered later.
  5. Separately, re-read the existing corridor position rows and label each acted signal with both leg tokens and the combined entry cost. This is a read of data we already have and it settles the 0-of-1 question directly.
  
  PHASE TWO, only if phase one clears the kill condition:
  6. When pair_cost is at or below 0.98 and fillable_size is at least 5 on both legs, buy BOTH legs at ask, 5 shares each, in a single atomic attempt.
  7. If only one leg fills, that is the failure mode that already cost corridor_pair 4.20 unhedged. Immediately attempt the second leg at the new ask. If the second leg cannot be filled within 2 seconds at a price that keeps pair_cost at or below 1.00, sell the filled leg back at the bid at once and log reason pair_leg_unhedged_unwound.
  8. STOP on a one-legged position, strictly below entry: if the filled leg's best bid falls to entry minus 5 cents before the complement fills, cross out at the bid. The position is directional and unhedged at that moment, so it needs a real stop and this one is strictly below entry.
  9. A completed pair is held to resolution. There is no stop on a completed pair, because the identity IS the stop: the worst case is that the pair pays exactly 1.00 against a cost at or below 0.98. This is the only case in this document where the absence of a price stop is correct, and it is correct only when both legs are confirmed filled.
  10. Never let a one-legged position live past window close. Cross out at the bid at 30s remaining.
data_requirements: "Order book for the Yes token: WE HAVE THIS. Order book for the No token of the SAME market at the SAME instant: WE MAY NOT HAVE THIS, and this is the crux. The existing corridor code pairs a 5m market with a 15m market, per the corridor_pair_live card, which pairs different CLOCKS rather than complementary OUTCOMES, and that is the most likely explanation for a pair costing 1.21 with both legs resolving to 1.00. Whether the loop can currently fetch both outcome tokens of one market simultaneously must be confirmed by reading the code before this is built, and if it cannot, this becomes a repair on the market data layer. Depth at best ask on both legs: WE HAVE THIS where we have the book, and insufficient_ask_depth at 1345 skips proves depth is already inspected. Resolution outcome: WE HAVE THIS. Existing corridor position rows with leg tokens: WE HAVE THIS, it is a query, not new research."
markets: "Both tokens of every Polymarket up/down market already in the shadow loop: btc, eth, sol, 5m and 15m. Yes and No token of the same market, which is the pairing that makes the identity hold."
kind: experiment
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

## What this is

A verifier for the single largest unresolved claim in the project: whether the corridor family has ever bought the structural guarantee it says it buys.

## The arithmetic

expected_edge_bps is null because kind is experiment, and that is the honest answer rather than a formality. If a sub-par pair exists at 0.98 combined, the payout is 1.00, so the gross capture is 2 cents on 98 cents of cost, which is 204 bps. That clears the PREDICTION_MARKET floor of 200 bps by 4 bps, which is a margin of exactly nothing. At 0.97 it is 309 bps. So even in the best case this idea only works if sub-par instants reach roughly 0.97 or below with real depth on both legs, and a 0.99 pair is not worth having. That is a strong reason to run the measurement before building anything: the interesting question is not whether sub-par instants exist, it is whether they go deep enough below par to clear the tick floor.

The measured record for comparison:

- One observed pair: 0.31 + 0.90 = 1.21. That is 21 cents ABOVE par, not below.
- Family: 9 closed trades, -$4.55 net, $0.00 fees.
- corridor_pair_live: 4874 evaluations, 1 acted, act rate 0.021%.
- corridor_collector: all 4 trades entered at an average of 0.185 and exited at 0.000.

## Evidence leaned on

- Vault note 2026-08-18-corridor-pair-works.md, in full. Its opening finding is that both legs exited at 1.00, which a complementary pair cannot do, and its first recommended measurement is the one implemented here.
- Strategy card corridor_pair_live.md: thesis states combined cost under 1.00, observed combined cost 1.21, status PROVISIONAL on 2 closed trades.
- Shadow skip table: insufficient_ask_depth 1345, no_asks 3863. Depth and book absence are already instrumented, so the counters this needs have precedent.

## What would change my mind

A single hour spent reading the corridor pairing code. If it pairs a 5m market with its 15m parent rather than pairing the Yes and No tokens of one market, then the structural thesis was never implemented, the 1.21 observation is fully explained, and this whole proposal collapses to a one-line documentation fix plus a decision about whether to build the real thing. That code read should happen BEFORE any of the logging described here, and I would rather be made redundant by it than build an instrument to discover something a grep would show.

## Convention notes

- Convention 8: the only unhedged state is a one-legged fill, and rule 8 gives it a stop strictly below entry. A completed pair correctly has no price stop, per rule 6 of the corridor vault note, which forbids inventing a stop on a binary held to resolution.
- Convention 20: pair_book_incomplete is its own counter and does not share one with no_asks.
- Convention 22: the corridor docstring claim is not a wiring test, which is exactly the failure this verifier exists to catch.

## Why this might fail

The most likely outcome is that the log comes back empty, because a true sub-par complementary pair is the most obvious arbitrage in all of prediction markets and there are bots whose entire job is to take it within milliseconds. If it existed at scale on liquid btc up/down markets, it would already be gone. An empty log is still a useful result, it kills the corridor thesis permanently, but it means this proposal generates no strategy. The second failure is that any sub-par instants we do see are stale-quote artefacts: two books read a few hundred milliseconds apart can appear to sum below 1.00 without either price being simultaneously available, which would make every observation an artefact of our own snapshot timing rather than a real opportunity. That is why fillable_size and same-instant fetching are in the rules, and if the fetch cannot be made genuinely simultaneous the measurement is worthless. Third, the leg risk is real and already paid for: corridor_pair took a one-legged fill that lost 4.20, and the unwinding logic in rule 7 pays the round-trip spread on the leg it unwinds, which is the exact cost that killed fair_value_arb. If sub-par instants are rare and leg failures are common, the strategy loses money on unwinds while waiting for an arbitrage that arrives too seldom to pay for them. Fourth, this may just re-derive what a code read would tell us in an hour: if the corridor code pairs 5m with 15m by design, then the structural claim was never implemented and no amount of book logging is needed to establish that.

## What past failure this addresses

Vault note 2026-08-18-corridor-pair-works.md is the direct source. It records that the one observed pair cost 1.21 rather than under 1.00, that verified sub-par executed pairs number 0 of 1, and that whether the strategy has ever bought the guarantee it claims to buy is NOT_MEASURED. Its What would change this verdict list opens with exactly this item: for every acted signal, log both leg tokens and the combined entry cost, with a threshold of 5 or more of the next 10 acted signals showing combined cost strictly below 1.00. This proposal implements that item and adds the book-side measurement needed to reach that many acted signals at all, given the observed act rate of 0.021%, 1 acted in 4874. It also respects that note's prohibition list: rule 1 forbids proposing a corridor variant claiming locked-in profit without first showing a logged executed pair under 1.00, which is precisely why phase two is gated behind phase one and why expected_edge_bps is null; rule 5 forbids buying cheap tails, and the 0.20 floor is absent here because this buys BOTH legs rather than a cheap one, with corridor_collector's 0.10, 0.11, 0.22 and 0.31 entries all resolving to 0.00 as the reason that rule exists; rule 6 forbids a non-zero stop on a binary held to resolution, which is why rule 9 above gives a completed pair no price stop and confines the stop in rule 8 to the genuinely directional one-legged state. On the hypothesis graph, no corridor row is currently TESTED_FAILED, so this does not re-propose a buried idea. The closest buried rows are ids 112, 113 and 114 in the fair_value_arb and dip_arb family, and the corridor note is explicit that their spread_eats_edge diagnosis does NOT transfer, because the corridor family pays 0.00 in fees and exits at resolution. The 4.20 unwind risk in rule 7 is the one place where those two failures meet, and it is named rather than assumed away.

## Forge warnings (non-blocking)

These used to be refusals. They no longer block a proposal, and they are recorded here so the information survives the downgrade.

- **no_graveyard_link_warning**: no related graveyard finding. Expected for PREDICTION_MARKET, EVENT and SPORTS: the graveyard has no rows in those classes.
