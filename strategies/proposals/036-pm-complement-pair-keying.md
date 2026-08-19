---
name: "pm_complement_pair_keying"
thesis: "Every forecast-free strategy the program wants to build next needs to know which two tokens are the two sides of the same market, and the system does not record that. The forge brief in docs/PLAN-2026-08-19.md section 2 Q4 asks for FORECAST-FREE strategies only: complement no-arbitrage, cross-market monotonicity, resolution mechanics. Proposal 026 is a pair-completion guarantee verifier. External signal 4 this cycle is Bregman projection arbitrage, which is a multi-outcome generalisation of the same idea. All three of them start with the sentence 'take the ask on YES and the ask on NO of the same market', and none of them can be written today, because market_tape (strategies/polymarket/dip_arb.py:280) has exactly six columns - market_id, ts, mid, best_bid, best_ask, source - and market_id is a bare ERC-1155 token id. Read at 2026-08-19 02:45 EDT: 8,583 rows, 51 distinct token ids, each a 76 to 78 digit numeric string, and nothing anywhere in the row that says which other token is its complement. This is the concrete cause of the failure already in the record. The old finding that yes_ask + no_ask sums below 1.00 in 7.85% of pairs was downgraded to NOT_TESTED because 61.7% of token-timestamps admitted more than one candidate partner under mid-sum matching, and that ambiguity is not a flaw in the matching heuristic, it is the heuristic existing at all. Two independent markets both trading near 0.50 are indistinguishable from a complement pair to any rule that works from prices, and near 0.50 is exactly where crypto Up/Down windows live. No heuristic will fix that. What makes this worth writing rather than filing as a chore is that the expensive half is already built. The tape samples synchronously: 185 distinct timestamps carry up to 49 token quotes at the identical float ts, so a complement pair is already observed at the same instant with no interpolation, which is the part that is usually hard. And the join key already exists in memory and is thrown away. engine/polymarket/markets.py:110 parses clobTokenIds, line 130 stamps token_id onto each outcome, and line 139 stamps condition_id onto the market. The mapping is constructed on every discovery pass and never written down. This proposal is to write it down. It ships no strategy and claims no edge. It converts one indicative, ambiguous, NOT_TESTED number into a measurable one, and it unblocks a family."
expected_edge_bps: null
kill_condition: "This is a repair and records no edge (convention 11). Its success condition is a measurement: the repair is DONE when agents/forge_shadow_eval.py can report, over 1,000 or more synchronous complement pairs drawn from market_tape with zero heuristic matching, the distribution of yes_ask + no_ask, and when the fraction of token-timestamps requiring any ambiguity resolution is exactly 0.000 rather than the 0.617 the mid-sum matcher produced. Zero, not low: the point of a stored key is that ambiguity becomes structurally impossible, so any non-zero figure means the key is not being used and the repair has not landed. The repair is FAILED if, after wiring, more than 5% of tape rows carry a NULL condition_id from any cause other than a market the discovery pass genuinely never saw, and in that case it must be reverted rather than patched with a fallback matcher, because a partially-keyed tape silently re-admits the heuristic through the back door and produces a number that looks exact and is not. If 1,000 pairs have not accumulated within 14 days, record NOT_TESTED and requeue (convention 11)."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  This proposal has no entry or exit rules because it is not a strategy. What follows are the implementation rules, stated with the same specificity a strategy would get, because a vague repair is how a schema change becomes a second source of truth.
  1. Add three columns to market_tape via ALTER TABLE ADD COLUMN IF MISSING, following the migration shape already used for fill_was_maker at engine/polymarket/shadow_loop.py:720: condition_id TEXT, outcome_index INTEGER, outcome_name TEXT. Nullable, no default. A NULL condition_id means "the discovery pass had not seen this token when the quote was taken", which is a real and different state from "this token has no complement" (convention 20 - a value that never arrived is a different fact from a value that arrived and was unused).
  2. Populate at write time, not at read time. The writer is dip_arb.py:387. It must take condition_id, outcome_index and outcome_name from the PolymarketMarket record the discovery pass already holds. Do NOT add a lookup call: this is a copy of data already in memory, and turning a tape write into a network round trip would change the sampling rate and therefore change what the tape measures.
  3. Do NOT backfill the 8,583 existing rows. Their pairing is exactly as ambiguous as it was and inventing a mapping for them would launder a NOT_TESTED result into a tested one, which is the failure this proposal exists to end. Old rows keep NULL and are excluded from every pair query by that NULL. The 61.7%-ambiguous sample is not rescued; it is retired.
  4. Derive the complement in SQL, not in a column. With condition_id and outcome_index stored, the complement of a row is the row with the same condition_id and the same ts and a different outcome_index. Storing a complement_token_id column instead would create a second place the truth lives and a second place it can go stale. Add INDEX idx_market_tape_condition_ts ON market_tape(condition_id, ts).
  5. Assert the pair is complete before using it. A synchronous pair requires BOTH sides present at the identical ts AND both carrying a non-NULL best_ask. Note the constraint this hits today: of 8,583 rows, the 1,037 with source='ask' have best_bid NULL and only best_ask populated, while the 7,546 with source='mid' carry both. A no-arbitrage test needs both asks, so source='mid' rows are the usable population and the ask-only rows are half-quotes. Count the pairs discarded for incompleteness and report the count (convention 20). A silent drop here is a missing number and it is the number that decides whether the tape is dense enough to trade on at all.
  6. Emit no signal and take no position. This proposal changes the tape and nothing else. Proposal 037 is the strategy that consumes it and it is explicitly BLOCKED on this landing. Shipping the two together would mean the first arbitrage number ever produced arrives already entangled with an entry rule, and there would be no way to tell a measurement error from a bad strategy.
  7. Extend the same three fields to the signals features_json written by fair_value_arb.py:670, which already computes counter_ask from the complement book at decision time. That field currently records nothing: zero signals in db/trading.db contain the string counter_ask, because both live loops run commit e033078 and D-329's measurement code is not in it. Wiring the key does not fix that; a restart does. Both are needed and they are independent.
data_requirements: |
  HAVE, and this is the finding that makes the repair cheap: the join key is already parsed and already in memory on every discovery pass. engine/polymarket/markets.py:110 parses clobTokenIds into an ordered list, line 122 asserts len(names) == len(token_ids), line 130 stamps token_id per outcome, line 139 stamps condition_id = str(raw['conditionId']). Nothing needs to be fetched, inferred, or matched. It needs to be passed to the writer.
  HAVE: synchronous sampling. 185 distinct timestamps in market_tape, with 49 token quotes sharing a single identical float ts at the top of the distribution (verified 2026-08-19 02:45 EDT). A complement pair is therefore observed at the same instant, which removes the interpolation error that normally dominates a two-leg no-arbitrage measurement. This is the reason the repair is worth doing on this tape rather than building a new one.
  HAVE: both asks, on 7,546 of 8,583 rows (source='mid'). See rule 5.
  MISSING and this is the whole proposal: market_tape.condition_id, market_tape.outcome_index, market_tape.outcome_name. Absent from the schema at strategies/polymarket/dip_arb.py:280 and absent from both db/trading.db and db/trading-survivors.db.
  MISSING, separate, and it blocks any fill-provenance reading of what follows: positions.fill_was_maker is defined in db/schema.sql:95 and migrated at engine/polymarket/shadow_loop.py:720, and is NOT a column of positions in either live database, because both loops run e033078, which does not contain it. Likewise counter_ask at fair_value_arb.py:670 appears in zero signals rows. Both D-329 measurements are dark in every running environment. They are in the tree and they are not in the process (convention 13). This is not a code defect and needs no code change; it needs the next natural restart. Until then convention 32 cannot be applied to any position booked since 00:56 EDT.
  NOT NEEDED: any new API, feed, credential or venue. No network call is added.
markets: "All Polymarket markets the discovery pass already returns. Crypto Up/Down windows are the immediate population, but the key is market-agnostic by construction and is the precondition for ever extending to multi-outcome markets, which is where the Bregman projection idea from external signal 4 actually lives."
kind: repair
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

## Why this might fail

The strongest argument against this proposal is that it is a schema change
dressed up as an insight, and the insight is one line long: store the
condition_id. That is a fair reading and I will not pad it. What I would say
back is that the record already contains one measurement destroyed by its
absence, and three proposals blocked by it, so the cost of the line being
unwritten has already been paid twice.

The failure I would actually bet on is that the repair lands, the ambiguity goes
to zero, and the answer is that there is no arbitrage. Complement overround is
recorded as median 0.0020 a share, mean 0.00316, p90 0.0060, on 7,312 indicative
tape pairs. Those are overrounds, which is the market taking money, not giving
it. A no-arbitrage opportunity is the sign flipped: yes_ask + no_ask below 1.00.
The prior should be that it is rare, small and gone before a paper adapter can
reach it, and the honest expected outcome of this repair is a clean number
saying so. **That is still worth having.** A measured zero on a structural
strategy is a different object from an unmeasured hope, and it would retire
proposal 026 and the Bregman idea with evidence instead of leaving them queued
forever. The program's problem right now is not a shortage of ideas, it is that
almost nothing gets measured cleanly enough to be killed.

Second failure mode: the tape may simply be too thin. 185 timestamps over the
sampled period, 51 tokens, and only the 7,546 mid-sourced rows carry both sides.
If complement coverage within a single timestamp turns out to be sparse - if
most sampled tokens are the YES side of markets whose NO side is never
sampled - then the pair count per hour could be small enough that the 1,000-pair
threshold in the kill condition takes weeks. Rule 5's discard count is what
would reveal that, and it should be read on day one rather than at the end.

Third: I have not verified that the discovery pass and the tape writer share a
call frame. markets.py holds the mapping and dip_arb.py:387 does the write, and
if there is no existing path between them then rule 2's "copy of data already in
memory" becomes a plumbing change rather than a field addition, with a
correspondingly larger blast radius on a module that feeds a live strategy.
I looked at both ends and not at the middle. Whoever implements this should
check that first, and if the two are not connected, the right move is a small
token_id to condition_id map populated by the discovery pass and read by the
writer, not a refactor of either.

Fourth, and most likely to be missed: rule 3 refuses to backfill, which means
for a while the tape has two populations and every query must filter on
condition_id IS NOT NULL. Somebody will forget, and the resulting number will
quietly be computed over the old ambiguous rows. The index in rule 4 does not
protect against that. A view that filters the NULLs, used everywhere in
preference to the raw table, would.

## What past failure this addresses

The named failure is the one the record already carries: the result that
`yes_ask + no_ask < 1` in 7.85% of pairs was reclassified as NOT_TESTED, on the
ground that 61.7% of token-timestamps admitted more than one candidate partner
under mid-sum matching. That reclassification was correct and it is a convention
11 case in its purest form - the measurement could not run, and it was
originally reported as though it had. This proposal is the only thing that
changes that state, because no better heuristic exists: two unrelated markets
near 0.50 are genuinely indistinguishable from a complement pair by price alone,
and crypto Up/Down windows sit near 0.50 for most of their life.

It also addresses, at one remove, why the forecast-free family has produced
proposals and no measurements. Proposal 026 (pair completion guarantee verifier)
and proposal 030 (one-legged pair unwind guard) both reason about two legs of
the same market. The Bregman projection idea from external signal 4 is the
multi-outcome version and needs the same key plus an outcome index, which is why
rule 1 stores outcome_index and not just a boolean side flag. None of that
family can produce a number until the key exists, and the standing correction
that forecasting edge is bounded and unproven is precisely the argument for
unblocking the family that does not forecast.

What is DIFFERENT from a "just match harder" approach, which is the obvious
alternative and the one I am rejecting: a heuristic matcher produces a number
with an error bar nobody can compute, because the error depends on how many
independent markets happened to trade near the complement of each price. A
stored key produces a number with no matching error at all. The kill condition
demands 0.000 ambiguity rather than "low" ambiguity for exactly this reason, and
the FAILED branch forbids adding a fallback matcher, because a keyed tape with a
heuristic fallback reports exact-looking numbers on a mixed population and is
worse than either approach alone.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET: the graveyard has no rows in that class. The engaged prior
  failure is the NOT_TESTED complement result and it is named above.
