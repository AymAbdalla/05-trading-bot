---
name: "pm_complement_no_arbitrage_taker"
thesis: "Buy both sides of the same binary market when the two asks sum to less than one dollar. The pair pays exactly 1.00 at resolution no matter which side wins, so the profit is 1.00 minus what was paid, it is locked at entry, it does not depend on any forecast, and it is the only strategy in this program whose payoff does not require the model to be right about anything. That matters because of the standing correction: PM_temporal_arbitrage is the cleanest uncensored sample the system owns and it says the price is very hard to beat. Every registry strategy and every other proposal is a forecaster betting it beats that price. This one is not betting on the price at all, it is betting that two prices are inconsistent with each other, and inconsistency is checkable rather than predictable. The reason it is worth proposing NOW, having not been proposable before, is that the tick grid is finer than this codebase believes. strategies/polymarket/weather_arb.py:480-490 reasons that the smallest expressible Polymarket price move is one cent, that one cent on a fifty-cent contract is 200 bps, and therefore that any edge under 200 bps is a rounding artefact of the grid. The live tape contradicts that. Read from db/trading.db market_tape at 2026-08-19 02:50 EDT: all 8,722 non-null best_ask observations lie on a 0.001 grid and only 1,278 of them, 14.7%, lie on a 0.01 grid. Even restricted to the 0.10 to 0.90 band the constant was written about, 598 of 1,658 asks are sub-cent. The real tick is a tenth of a cent, so the smallest expressible complement violation is 0.001 on a 1.00 payout, which is 10 bps, not 200. A four-tick gate is 40 bps, clears the 30 bps floor honestly, and is a price the book can actually quote. Under the one-cent assumption this strategy was arithmetically impossible; under the observed grid it is merely rare. The honest expected outcome is still that it fires seldom. The complement overround recorded on 7,312 indicative tape pairs runs median 0.0020, mean 0.00316, p90 0.0060 a share, and an overround is the market taking money, not giving it. A no-arbitrage fire needs that sign flipped. So the unknown here is not the size of the edge, which the gate fixes by construction, it is the frequency, and the frequency is the thing nobody has measured because the pairing needed to measure it does not exist yet."
expected_edge_bps: 40
kill_condition: "Retire if realised net P&L per completed pair is below 0.00 USD over 100 or more completed pairs as measured by agents/forge_shadow_eval.py. A completed pair means both legs filled and the market resolved; a pair that fills one leg and not the other is a DIFFERENT object, is counted separately as a one-legged failure, and is governed by rule 7 rather than by this condition, because pooling them would let execution failures masquerade as edge decay. SECOND, and this is the condition that will actually fire first: retire if fewer than 100 qualifying pairs are OBSERVED in the tape (not traded, observed) in 14 days of keyed tape under proposal 036, where qualifying means yes_ask + no_ask <= 0.996 with both legs quoted at the same timestamp and both carrying non-null best_ask. That is an opportunity-count test and it can be run on the tape alone with no capital and no entries, which is why it should be run FIRST and why this proposal should not be built until it passes. If 036 has not landed, record NOT_TESTED, never failed (convention 11), because with 61.7% of token-timestamps ambiguous under the only pairing method available today the qualifying-pair count is not a small number, it is not a number."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  BLOCKING PRECONDITION: proposal 036 must be landed and the tape keyed on condition_id before any of this is built. Not "should be", must. Without a stored pairing key the entry condition in rule 2 is evaluated against a partner chosen by a heuristic that is ambiguous on 61.7% of token-timestamps, which means a fire is as likely to be two unrelated markets both near 0.50 as it is to be a real complement pair, and buying two unrelated markets that sum to 0.99 is not an arbitrage, it is two uncorrelated directional bets with no floor. This is the single way this strategy loses money on a thesis that is otherwise arithmetically incapable of losing it.
  PHASE 0, and do this before writing any strategy code: run the opportunity count from the kill condition against the keyed tape. No entries, no capital, no adapter. If qualifying pairs do not appear at 100 or more per 14 days, the proposal is retired on observation alone and no code was written. Report the count of pairs discarded for incompleteness alongside it (convention 20) - a low qualifying count caused by thin tape coverage is a different verdict from one caused by an efficient market, and the discard count is what distinguishes them.
  1. Universe: any Polymarket market where the discovery pass returns exactly two outcomes with a shared condition_id. Crypto Up/Down windows are the population that exists today. Do not extend to multi-outcome markets in this proposal - that is the Bregman projection generalisation from external signal 4, it needs a convex solver rather than an addition, and it should not ride in on the back of a two-line entry condition.
  2. Entry condition: at a single tape timestamp, with both legs carrying non-null best_ask, fire when effective_ask(YES) + effective_ask(NO) <= 0.996. Four ticks on the observed 0.001 grid, 40 bps gross on a 1.00 payout. Use effective_ask, the depth-walked price the codebase already computes, not the top-of-book ask, or the gate is measuring a quote that one share deep and the position is not.
  3. Sizing: shares = min(depth available at the quoted ask on BOTH legs, 20 shares). Notional is therefore under 20 USD a pair. Size is capped by the THINNER leg, not by capital. A pair sized to the deeper leg is a one-legged position wearing a hedge's name.
  4. Exit: hold both legs to resolution. There is no other exit and there must not be one. The pair pays 1.00 at resolution by construction; selling either leg early converts a locked payoff into a directional position and re-crosses a spread the entry already paid. This strategy has no stop, and that is correct rather than an omission: convention 8 requires a stop strictly below entry, and the structural floor of a complete complement pair IS the entry, so there is no price below it to stop at. State that explicitly in the code or a reviewer will read the missing stop as a bug.
  5. Fee assumption, stated so it can be falsified rather than assumed: config.yaml polymarket.taker_fee_rate is 0.0 and the comment there says "Polymarket charges no CLOB taker fee TODAY. An assumption, not a law." External signal 3 this cycle claims 2% on winners. Those cannot both be right and the difference decides the strategy: 2% of a 1.00 payout is 200 bps and it eats a 40 bps gate five times over. BLOCKING SUB-PRECONDITION: verify the current fee against the live venue before entry one. If the fee is 2% of payout, this proposal is dead on arrival and should be marked REJECTED with that as the reason. Do not "just raise the gate to 240 bps" - a 240 bps complement violation is a different and far rarer event, and re-gating a strategy to survive a cost it was not designed around is how a 40 bps hypothesis quietly becomes an untested 240 bps one.
  6. Gates: maximum 2 concurrent pairs; no more than 1 pair per condition_id; skip if either leg's ask is below 0.02, where the 0.001 grid gives the sum arithmetic no room and the depth is usually one share. Every skip carries a counted reason (convention 20).
  7. One-legged failure handling: if leg A fills and leg B does not within one loop cycle, the position is NOT an arbitrage and must not be reported as one. Unwind leg A at bid immediately and record the outcome under a separate exit_reason, one_legged_unwind, with its own P&L line. Proposal 030 is the existing treatment of this failure mode and it should be read before this is built. The one-legged rate is the number that decides whether this strategy is executable, and it is more important than the P&L.
  8. Record on every fire: both token ids, the condition_id, the shared timestamp, both effective asks, both top-of-book asks, both depths, the sum, the gate, shares taken, and whether both legs filled. The gap between top-of-book sum and effective sum is the measurement of how much of this edge is a quoting artefact, and it is the first thing that will explain a disappointing result.
data_requirements: |
  BLOCKER, and it is the reason this proposal is written as PROPOSED and unbuildable rather than as a build order: market_tape.condition_id does not exist. Proposal 036 adds it. Until then there is no way to know which two tokens are complements, the entry condition in rule 2 cannot be evaluated correctly, and the honest status of every complement number in the record is NOT_TESTED, not zero. This is the same shape as proposal 005, which stays unbuilt because its data_requirements name a blocker, and it is deliberately the same treatment.
  BLOCKER: the fee question in rule 5. config.yaml:136 says zero and flags itself as an assumption; external signal 3 says 2% on winners. One number decides whether the strategy exists.
  HAVE, verified 2026-08-19 02:50 EDT: synchronous quotes. market_tape samples up to 49 tokens at one identical timestamp across 185 distinct timestamps, so both legs of a pair are observed at the same instant with no interpolation. This is the requirement two-leg arbitrage usually fails on and here it is already satisfied.
  HAVE: a 0.001 price grid, contradicting the one-cent assumption baked into weather_arb.py's POLYMARKET_TICK_ON_FIFTY_CENTS_BPS = 200.0. 8,722 of 8,722 observed asks sit on the 0.001 grid; 1,278 sit on the 0.01 grid. That constant should be re-derived, and it is cited here because it is the arithmetic that makes a 40 bps gate legitimate rather than a rounding artefact.
  HAVE: effective_ask depth walking, already used by weather_arb and the fair_value family.
  MISSING, and it degrades the verdict rather than blocking the build: positions.fill_was_maker is in db/schema.sql:95 and migrated at engine/polymarket/shadow_loop.py:720 but is not a column of positions in either live database, because both loops run e033078, which does not contain it. This strategy is taker-only by construction (rule 2 lifts an ask), so its own results are unambiguous, but convention 32 cannot be checked mechanically on them until the next natural restart, and any comparison against another strategy's numbers inherits that gap.
  MISSING, non-blocking: order book depth is read live and not stored on the tape, so Phase 0's opportunity count can be computed on top-of-book sums only. That count is an UPPER bound on real opportunities, and it must be labelled as one. A Phase 0 pass is a screen, not a result.
markets: "Polymarket two-outcome markets, both legs of one condition_id. Crypto Up/Down windows are the immediate population. Explicitly NOT multi-outcome markets in this proposal."
kind: edge_hypothesis
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
forge_refusal: "below_min_edge_bps. agents/forge.py:109 sets MIN_GROSS_EDGE_BPS_BY_ASSET_CLASS['PREDICTION_MARKET'] = 200, so this proposal's 40 bps would be REFUSED at write time by agents/forge.py and would never reach this directory. It is filed anyway, at PROPOSED and not REJECTED, because the author cannot be the referee (see README) and because the refusal turns entirely on a constant this proposal disputes with data rather than on the proposal's merits. The comment at agents/forge.py:107 states the derivation in full: '1c tick / 50c premium = 200bps'. The live tape says the tick is 0.001, not 0.01 - 8,722 of 8,722 observed best_ask values lie on the 0.001 grid, 1,278 lie on the 0.01 grid, db/trading.db read 2026-08-19 02:50 EDT. If the tick is a tenth of a cent then the same derivation gives a 20 bps floor and this proposal clears it by 2x. RAVEN'S CALL, and it is a decision about a constant, not about this strategy: either the floor is re-derived from the observed grid, in which case re-screen this; or the 200 stands on a reason other than the tick, in which case that reason should be written down at agents/forge.py:107 where the tick reason currently is, and this proposal is dead. Do not resolve it by raising this proposal's gate to 200 bps - a 20-tick complement violation is a different and far rarer event, and re-gating a hypothesis to survive a validator is the same error rule 5 refuses to make about fees."
---

> **READ THE `forge_refusal` FIELD ABOVE BEFORE ANYTHING ELSE.** This proposal
> would not pass `agents/forge.py`'s edge floor, the floor rests on a tick size
> the live tape contradicts, and nothing here should be built until that is
> ruled on. The same constant also governs proposals 032 and 033.

## Why this might fail

The overwhelmingly likely outcome is that Phase 0 returns a qualifying-pair
count near zero and this proposal is retired without a line of strategy code
being written. I want that stated at the top rather than buried, because it is
the point of Phase 0: the cheapest version of this test costs nothing and
happens entirely on stored data. The reasons to expect near-zero are good ones.
The complement sums we have seen run POSITIVE - median overround 0.0020 a share,
mean 0.00316, p90 0.0060 - which means the market is taking about two ticks on
the pair, consistently, in the direction opposite to the one this strategy
needs. External signal 3 reports the median cross-venue arbitrage window shrank
from 12.3 seconds in 2024 to 2.7 seconds in 2026 and that 73% of arbitrage
profit accrues to sub-100ms infrastructure. A shadow loop that samples 185 times
over a few hours is not going to be in the room when a 2.7-second window opens.

The second failure is the one that would cost money rather than time, and it is
why rule 7 exists and why the blocking precondition is written the way it is.
This strategy's entire claim to a locked payoff depends on the two legs being
genuine complements and on both legs filling. Break either and the position is
two naked longs. Break the pairing and it is two naked longs on unrelated
markets, which is strictly worse than any directional strategy in the registry,
because it carries the risk of a forecast without making one. That is the
scenario the 61.7% pairing ambiguity produces, and it is the reason 036 is a
hard precondition instead of a nice-to-have.

Third: the 40 bps gate is four ticks, and four ticks is inside the noise of a
book that quotes to a tenth of a cent. A sum of 0.996 observed at top of book
may be 1.002 after walking two shares of depth on each leg. Rule 2 requires
effective_ask precisely for this, but Phase 0 cannot - depth is not stored - so
Phase 0's count is an upper bound and probably a loose one. If Phase 0 returns
120 qualifying pairs, that is not a pass, it is "maybe, go measure depth."

Fourth, the honest accounting problem with the whole idea: even if it works, 40
bps on sub-20-USD pairs is roughly 8 cents a pair. A hundred pairs is 8 dollars.
Against an account down 920 USD this is not a recovery, and it should not be
sold as one. Its value is epistemic. It would be the first number this program
has produced that does not depend on a forecast being right, and given that
PM_temporal_arbitrage's uncensored sample says the price is very hard to beat,
knowing whether ANY forecast-free edge exists is worth more than the eight
dollars.

Fifth, a correction I have to make to my own arithmetic before someone else
does. I claimed the tick finding rescues this proposal from weather_arb's 200
bps floor. It rescues the ARITHMETIC. It does not establish that a sub-cent
quote is executable in size: 36% of in-band asks being sub-cent is consistent
with a world where the sub-cent levels are one-share dust sitting in front of a
cent-grid book with all the real depth. Rule 3's thinner-leg sizing and rule 8's
depth logging are what would expose that, and if it turns out to be true then
weather_arb's constant is right in spirit and wrong only in units, and this
proposal dies with it.

## What past failure this addresses

It addresses the failure the whole registry shares rather than one strategy's.
Read at 02:50 EDT, db/trading.db carries 1,862 closed positions at -920.12 USD.
The bleed is concentrated in the fair_value family and its exits: sell:price_stop
alone is 835 positions and -1,419.32 USD, of which PM_fair_value_arb is 311
positions and -565.60, PM_fair_value_arb_hft 233 and -370.11, PM_dip_arb 136 and
-242.19, PM_fair_value_arb_inverse 127 and -169.66. Those are five strategies
losing money the same way: entering near 0.35 on a model signal and exiting near
0.25 when the price moves against it. The diagnosis in the record is that the
model has no directional content (slope 0.30 against a calibrated forecaster's
1.0, hft win rate falling from 45.7% to 20.2% as claimed edge rises), and that
execution is about 9% of the loss while the model is 91%.

Every repair proposed for that family so far has been a repair of the FORECAST -
change the exit, tighten the gate, invert the sign, hold to settlement. This
proposal is the first to accept the diagnosis and leave. If the model is 91% of
the problem, the response is not a better model, it is a payoff that does not
contain one. That is the forge brief in docs/PLAN-2026-08-19.md section 2 Q4 -
forecast-free strategies only: complement no-arbitrage, cross-market
monotonicity, resolution mechanics - and this is the complement no-arbitrage
half of it.

What is DIFFERENT from proposal 026, the pair completion guarantee verifier:
026 verifies that a pair, once held, is complete and floored. This one is the
entry rule that creates such a pair on purpose, gated on the sum. They are
complements rather than duplicates and 026 should be read as the safety layer
under rule 7. What is DIFFERENT from proposal 005 and from corridor_pair_live:
those are relative-value bets between two DIFFERENT markets, whose legs can both
lose or both win. This is one market's two sides, which cannot. The README's
warning that 005 and corridor_pair_live must never be pooled applies here with
equal force in a third direction: no result from this strategy is evidence about
either of them.

What is DIFFERENT from external signal 4's Bregman projection: that is the
n-outcome version, needs Frank-Wolfe and KL-divergence minimisation over a
simplex, and is a genuinely larger build. Rule 1 explicitly refuses to extend
there. But proposal 036 stores outcome_index rather than a two-valued side flag
specifically so that the Bregman version needs no further schema work, and the
right order is: key the tape, count two-outcome opportunities for free, and only
if that count is non-trivial does the convex solver become worth anyone's time.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET: the graveyard has no rows in that class. The engaged prior
  failures are the fair_value family's hypothesis_graph entries, named above by
  their P&L rather than by id because the argument here is about the family's
  aggregate diagnosis rather than any single burial.
