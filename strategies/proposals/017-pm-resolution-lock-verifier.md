---
name: "pm_resolution_lock_verifier"
thesis: "The corridor family's entire claim rests on buying complementary Up and Down legs of the same Polymarket window for a combined cost strictly below $1.00. The counterparty whose behaviour would create that inefficiency is the late-window directional taker: with minutes left on a 5m or 15m binary, one side attracts flow because it is 'obviously' winning, and the loser side gets marked down faster than the winner gets marked up, because nobody wants to pay 4 cents for a lottery ticket that dies in 90 seconds. That leaves the pair summing under 100 cents. The inefficiency persists because the two legs are separate order books with separate takers and nothing in Polymarket's matching engine enforces put-call parity across them. But the vault note 2026-08-18-corridor-pair-works.md says flatly that this has NEVER been observed in our own logs: 0 of 1 executed pairs cost under $1.00, the one observed pair cost $1.21, and both legs exited at 1.00 which is impossible for a genuine complementary pair. So this proposal does NOT claim the edge exists. It claims we cannot currently tell, and it builds the instrument that would tell us. It is a measurement harness, not a strategy."
expected_edge_bps: null
kill_condition: "Run for 10 acted signals or 20,000 evaluations, whichever comes first, scored by forge_shadow_eval. If fewer than 5 of the next 10 acted pairs record a combined two-leg entry cost strictly below $1.00, declare the structural thesis FALSE AS IMPLEMENTED and retire the entire corridor family including this verifier. If 0 pairs are logged after 20,000 evaluations, retire on frequency instead: the leg-pairing code path is not reachable in the live loop and the family is untestable."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "Fires only on Polymarket up/down windows where BOTH complementary token ids for the same market slug are resolvable in one scan. Gate 1: both legs must have a live ask. If either side has no ask, skip with reason no_asks_on_one_leg. Gate 2: compute pair_cost = ask_up + ask_down in cents. Gate 3: LOG pair_cost, both token ids, both asks, both bids, both book depths, and the market slug to a new skip/act row on EVERY evaluation that reaches this point, including the ones that do not trade. This logging is the deliverable. Gate 4: enter ONLY if pair_cost <= 97 cents, buying equal share counts of both legs. Size 10 shares per leg, which is half the fair-value family's 18 to 20, because this is a measurement run. Exit: hold both legs to resolution. Stop is 0.00 on each leg, which is where a losing binary share goes and is strictly below any entry above zero, satisfying convention 8. There is no intermediate exit and no price stop: the vault note is explicit that no such fill exists on the paper adapter and that proposing a 3c stop describes a fill that does not exist. Target is 1.00 on the winning leg. Net per pair at resolution is 100 cents minus pair_cost minus fees, before any adverse selection."
data_requirements: "Complementary token id resolution for both sides of one market: HAVE IT in principle, since the loop already prices individual legs, but whether the current corridor code actually pairs them is exactly what is unverified. Live ask on both legs: HAVE IT, the loop reads books today. Per-leg entry cost logged on the position row: DO NOT HAVE IT. The vault note names this as the missing measurement and says it is a read of existing data rather than new research. That single gap is what makes this a repair. Resolution outcome per token: HAVE IT, positions already resolve at 0.00 or 1.00 in the paper adapter. Fee schedule: HAVE IT, and it is currently $0.00 across all 615 fair-value-family trades and all 9 corridor-family trades."
markets: "Polymarket btc/eth/sol up-down 5m and 15m windows, both complementary legs of each"
kind: repair
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

## Why this is a repair and not an edge hypothesis

Convention 11 says NOT_TESTED means could not run. The corridor structural thesis has never run. The vault note is blunt about it: pairs verified to have cost under $1.00 is 0 of 1, and the one observed pair cost $1.21 with both legs exiting at 1.00, which is arithmetically impossible for genuine complementary legs. So we do not have a failed arbitrage. We have an unbuilt measurement.

Because the condition has never been evaluated, inventing an edge number for it would be worse than admitting I do not have one. expected_edge_bps is null, as rule 3 requires.

## The arithmetic I am NOT allowed to do yet

If a complementary pair could be bought at 97 cents, the gross edge on a 100-cent payout would be 3 cents on 97 cents of premium, which is 309 bps, above the 200 bps prediction-market floor. At 98 cents it is 204 bps, one tick above the floor. At 99 cents it is 101 bps and dead on arrival.

That arithmetic is why my entry gate is 97 and not 99: anything above 97 cannot clear the floor after even one cent of slippage. But I am explicitly NOT claiming that number as the expected edge, because the denominator is hypothetical. Zero pairs at any price under $1.00 have ever been logged. The whole point of this proposal is to find out whether the 97-cent row exists at all.

## What evidence I leaned on

- `2026-08-18-corridor-pair-works.md`: the 0-of-1 count, the $1.21 observed pair, the both-legs-at-1.00 impossibility, the $0.00 fees, the 5-of-10 test I adopted as the kill condition, and the six explicit do-not-propose items.
- `2026-08-18-cycle-001-day-1-lessons.md`: family net -$4.55 across 9 closed trades, corridor_collector entering at 0.10 to 0.31 and resolving to 0.00 on all 4, and item 3 under What to try next naming this measurement.
- The shadow evaluation block: `not_final_third_of_15m` at 11,646 skips and `late_in_window` at 4,753. I am deliberately NOT touching those gates in this proposal, because loosening a clock gate to buy frequency before knowing whether the underlying pair is ever cheap would just produce more of an unmeasured trade.

## What would change my mind

If 5 or more of the next 10 acted pairs log a combined cost strictly under $1.00, this stops being a repair and the next cycle should propose a real edge hypothesis with a measured bps number in the denominator we finally have. If fewer than 5 do, the corridor family should be retired entirely, including this verifier, and the 9 closed trades reclassified as directional binary outcomes rather than as failed arbitrage.

If the logging reveals that gate 1 is passing on non-complementary token pairs, that is a code defect and a bigger finding than the strategy question. It would mean the family's entire history is mislabelled.

## Convention check

- Convention 1: nothing here is durable until `backtest/validate_harness.py` exits 0.
- Convention 5: gross bps deliberately NOT_MEASURED and recorded as null, per rule 3.
- Convention 7: 9 closed trades in the family is a shrug, and I have treated it as one.
- Convention 8: stop is 0.00 on both legs, strictly below any nonzero entry.
- Convention 11: this is NOT_TESTED, so it is a repair.
- Convention 17: I loosen no filter. The 97-cent gate is a new, tighter constraint.

## Why this might fail

The strongest argument against this is that it may be measuring something that structurally cannot exist, and burning a proposal slot to prove a negative. If Polymarket's own UI or any competent market maker enforces ask_up + ask_down >= 100 cents by construction, then pair_cost will never drop below 97 and the verifier will log 20,000 evaluations of nothing and retire on the frequency clause. That is still information, but it is expensive information. The second failure mode is adverse selection in the moments the gate DOES open: a pair sums to 97 cents precisely when one leg's book is thin or stale, so the 97-cent quote is not fillable at size and the real fill is 100 or 101. Fees are $0.00 today so there is no commission drag, but a partial fill on one leg turns the locked pair into a naked directional binary, which is exactly how the corridor family already lost $4.55 across 9 trades. Third, and most awkward for me: the vault note observed a pair where BOTH legs exited at 1.00, which is arithmetically impossible for complementary legs. That means the existing code may not be pairing complementary tokens at all, in which case my gate 1 will silently pass on two non-complementary legs and log a meaningless pair_cost. I have no way to rule that out from the brief.

## What past failure this addresses

This is the direct execution of the open question in the vault note 2026-08-18-corridor-pair-works.md, which states 'Settle the pair question by reading the position rows. Measurement: for every acted signal, log both leg tokens and the combined entry cost. If 5 or more of the next 10 acted signals show combined cost strictly below $1.00, the structural thesis is live.' My kill condition adopts that exact 5-of-10 test. The same note's 'What NOT to propose again' item 1 forbids any corridor variant that claims risk-free profit without first showing a logged pair under $1.00, so this proposal makes no profit claim at all and carries expected_edge_bps null. Item 6 of that note forbids a stop other than 0.00 on a binary held to resolution, which my entry_exit_rules obey explicitly. It also honours 2026-08-18-cycle-001-day-1-lessons.md item 3 under 'What to try next', which names this as the settle-it task. It does NOT re-tread hypothesis_graph id 113 PM_fair_value_arb or id 114 PM_fair_value_arb_hft, both failure_mode stop_too_tight and both diagnosed as spread_eats_edge in 2026-08-18-fair-value-arb-spread-problem.md, because this proposal takes no round trip: it holds to resolution and therefore never sells at bid. It also does NOT re-tread id 112 PM_dip_arb, same failure_mode stop_too_tight, for the same reason.

## Forge warnings (non-blocking)

These used to be refusals. They no longer block a proposal, and they are recorded here so the information survives the downgrade.

- **no_graveyard_link_warning**: no related graveyard finding. Expected for PREDICTION_MARKET, EVENT and SPORTS: the graveyard has no rows in those classes.
