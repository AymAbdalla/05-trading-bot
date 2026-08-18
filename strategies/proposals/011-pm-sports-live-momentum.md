---
name: "pm_sports_live_momentum"
thesis: "A scoring event moves the true win probability discontinuously, and if the Polymarket book reprices slower than we can observe the score and send an order, the stale quote between the event and the reprice is free money; the honest reading of our latency budget is that we cannot reach that window, so this is written as a probe to measure the window rather than as an edge we claim to have."
expected_edge_bps: null
kill_condition: "Kill if the measured median Polymarket reprice completion after a scoring event is FASTER than our measured median event-to-order latency, over 50 or more recorded scoring events. Kill also if any entry version of this ever reaches backtest/polymarket_harness.py and scores below 200bps net edge, or a t_stat below 2.0, on 200 or more resolved entries. The reprice measurement needs a live sub-minute book recorder that DOES NOT EXIST in this repo today; building it is step 1 and it is deliberately left unnamed rather than given a fake path."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "PROBE MODE (what is actually proposed). For every live NFL, NBA, CS:GO and top-flight soccer market in scope, record a timestamped tick tape of the best bid and best ask at 1 second resolution, plus a timestamped log of every scoring event as our score feed reports it. Emit no orders. Measure three quantities: t_event_true (best available estimate of when the event happened), t_feed (when our feed told us), t_reprice_50 and t_reprice_90 (when the book had absorbed 50 and 90 percent of the eventual move). ENTRY MODE (only if the probe says the window is reachable): on a scoring event that moves model win probability by 5 points or more, buy the scoring side at ask, size capped at the venue minimum lot, single leg, no hedge. STOP: 0.00, which is what a losing share is worth and satisfies convention 8. TARGET: resolution at 1.00. TIME EXIT: none, held to resolution. NO ENTRY if the ask has already moved more than 30 percent of the expected probability move at the moment we can act, because at that point we are paying for information we no longer have."
data_requirements: "Polymarket side, which we have: Gamma discovery (engine/polymarket/markets.py supports a tag filter and search_markets), CLOB books (engine/polymarket/orderbook.py), resolution outcome (engine/polymarket/prices.py resolution_price). Everything else is missing. BLOCKER: THERE IS NO LIVE SCORE OR PLAY-BY-PLAY FEED IN THIS REPO. engine/feeds/ contains hyperliquid_client.py and liquidation_recorder.py and nothing else. No module parses a game clock, a score, a possession, or a team name. BLOCKER: THERE IS NO SUB-MINUTE POLYMARKET PRICE TAPE. The CLOB /prices-history endpoint serves minute-grained fidelity, so a reprice that completes in 3 seconds cannot be measured retrospectively at all; it has to be recorded live before it can be studied, which is why this is a probe. BLOCKER: NO SPORTS MARKET DISCOVERY IS WIRED. The tag parameter exists but nothing selects sports tags, nothing maps a Polymarket slug to a game, and the sports resolution path has never been exercised; only BTC Up/Down has. BLOCKER: NO WIN PROBABILITY MODEL. The 5 point entry threshold assumes a per-sport in-game win probability model that does not exist here."
related_graveyard_findings: "There are NO PREDICTION_MARKET rows in the graveyard, and no sports rows of any kind. The graveyard is crypto spot and perp plus equities, ETFs and futures, scored on a price path. Nothing in it is evidence for or against this in either direction. The nearest neighbour in this repo is PM_mid_price_continuation, which is also a continuation bet, but it trades a BTC 5-minute window where the underlying is continuously observable and where the information we act on is a price we can read directly. Here the information is an event we can only learn about through a third party feed, which is the whole difficulty and is exactly what makes the two non-comparable. Do not pool them."
kind: experiment
status: PROPOSED
source: "forge, sports market expansion brief"
forge_warnings: "none"
---


## Why this records a null edge and not a number

`agents/forge.py` refuses an `experiment` that names an `expected_edge_bps`,
and that refusal is the right one here. The arithmetic below comes out
negative on our real latency. Writing a positive number would be inventing
one, and an invented number in a proposal becomes a cited number two documents
later.

## The latency budget, stated explicitly

This is the whole argument. Every figure below is an ESTIMATE unless marked
measured, and none of the external ones have been measured on our setup
(convention 15).

| Leg | Time | Source |
|---|---|---|
| Event happens to official venue feed | under 1s to 3s | ESTIMATE. Sportradar and Genius run the licensed low-latency feeds. Priced for sportsbooks, not for us. |
| Event happens to a public or cheap score API | 10s to 60s, often only at play or possession boundaries | ESTIMATE, unmeasured |
| Event happens to a broadcast or stream | 5s to 45s | ESTIMATE, unmeasured |
| Our poll phase | up to 5.0s, 2.5s average | MEASURED from code. `DEFAULT_POLL_SEC = 5.0` in `engine/polymarket/shadow_loop.py` |
| Our HTTP read of the book | a few hundred ms typical, `DEFAULT_TIMEOUT = 10.0` worst case | MEASURED from code, `engine/polymarket/client.py` |
| Order submission | paper is instant. Live needs CLOB SDK V2 with EIP-712 signing, which is not implemented (D-267) and would add roughly a second | ESTIMATE |

Realistic total from event to our order landing: **15 to 70 seconds**.

The people we would be trading against are watching the game and hold a feed
somewhere in the 1 to 5 second band. The stale-quote window is theirs. We
arrive between 3 and 70 times too late.

The counterparty who fills us 30 seconds after a touchdown is someone who saw
the touchdown 29 seconds before we did. That is not a mispricing we captured.
That is us being the exit liquidity.

## Edge arithmetic, showing the negative

Take a concrete NFL case. Team A is at 0.50. Team A scores a touchdown and the
fair probability moves to 0.62. That is a 12c move, which is a large event, so
this is the friendly case, not the marginal one.

Costs, from `backtest/cost_model.py`, both flagged UNMEASURED ASSUMPTION in
that file and both per share in dollars:

    PM_HALF_SPREAD_PER_SHARE   0.005
    PM_DEPTH_SLIP_PER_SHARE    0.005
    PM_TAKER_FEE_RATE          0.0
    total modelled cost        0.01 per share, one leg, held to resolution

If we buy at 0.62 after the book has fully repriced, and 0.62 is correct:

    gross edge  = 0.62 - 0.62 = 0.00c per share  =    0 bps
    net edge    = 0.00 - 1.00c                   = -161 bps of a 62c premium

To clear the PREDICTION_MARKET floor of 200bps gross (`agents/forge.py`,
`MIN_GROSS_EDGE_BPS_BY_ASSET_CLASS`, which is one tick on a 50c contract) we
would need to buy at least 1.24c under fair, so at 0.6076 or better, which
means arriving before the book has absorbed roughly 10 percent of a 12c move.
On a liquid NFL market that is the first 1 to 5 seconds.

Our arrival is 15 to 70 seconds. The edge as specified is unreachable.

## Entry price and win rate, stated together (D-267)

At a 62c entry the break-even win rate is 62 percent before costs and 63
percent after the modelled 1c. Either number alone says nothing. So the entire
question is whether the team that just scored, and is therefore priced at 62c,
wins more than 63 percent of the time.

If the market is calibrated the answer is exactly 62 percent and we lose the
full 1c on every trade. This strategy only works if the post-score price is
systematically too low, which is a claim about market calibration that nobody
here has measured on a single sports market.

## What would upgrade this to an edge_hypothesis

One measurement, and only one. If the recorded tape shows that on some subset
of markets, most plausibly CS:GO and lower-tier soccer at unsocial hours, the
median `t_reprice_50` is above 60 seconds, then our 15 to 70 second arrival is
inside the window and the trade exists. At that point the edge is
(fair minus stale ask) per share, it can be estimated from the tape, and this
gets rewritten as an `edge_hypothesis` with a number derived from data instead
of asserted.

The trap in that upgrade is that the slowest-repricing markets are also the
thinnest, and the 1c cost figure above is a guess taken from a one-tick-wide
book. On a thin CS:GO market the real cost of lifting an ask could be 3c or
more. That has to be measured from the same tape, not assumed, or the upgrade
is just moving the optimism from the latency line to the cost line.

## What would make this wrong

Three ways this reasoning fails, in decreasing order of how much I believe them.

1. **The reprice may be slower than I think on the specific markets in scope.**
   Polymarket sports books are retail heavy and, outside the headline NFL and
   NBA games, quite thin. A thin book can sit stale for a long time simply
   because nobody is there. I have no measurement either way, which is the
   reason this is a probe and not a refusal.

2. **The score feed may not be the binding constraint.** If the book reprices
   at t plus 45 seconds because the marginal Polymarket participant is also
   watching a delayed stream, then everyone in that market is late and being 30
   seconds late is not a disadvantage relative to the people setting the price.
   The relevant latency is ours against the marginal price setter, not ours
   against the venue.

3. **The direction of the residual may not be continuation.** Even if there is
   drift after the reprice, it could be a fade rather than a follow-through.
   The probe measures the sign; it does not assume it. If a future version of
   this file states the sign before the tape has been read, that is the failure
   mode convention 4 describes and it should be rejected on sight.

## Cost of being wrong about this quietly

If someone builds the entry version without running the probe, the backtest
will not obviously fail. It will produce entries at post-reprice prices, score
them against resolution, and return something close to break-even minus costs,
which reads as a marginal strategy rather than as a structural impossibility.
The 1c per share loss is small enough per trade to hide inside noise for
several hundred trades. That is why the latency measurement is the kill
condition and not a footnote.
