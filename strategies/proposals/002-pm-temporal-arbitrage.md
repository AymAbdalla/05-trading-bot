---
name: "pm_temporal_arbitrage"
thesis: "Within one 5m window the Up and Down legs are rarely cheap at the same instant but are often cheap at different instants, so buying each leg when its own price is depressed builds a pair whose combined cost is below 1.00, and a complete pair redeems at exactly 1.00 whatever the outcome."
expected_edge_bps: 638
kill_condition: "Any one of: median completed-pair cost above 0.97 (under 310bps) over 200 or more attempted pairs; fewer than 60% of first legs completing their second leg before expiry; net PnL negative once UNPAIRED legs are charged to this strategy at their realised resolution PnL. Scored by backtest/polymarket_harness.py."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "LEG 1: buy either side when its ask is at or below 0.47, in blocks of at most 50 shares, leaving at least 0.47 of pair budget for leg 2. LEG 2: buy the opposite side when its ask is at or below (0.94 - leg1_avg_price). Repeat in blocks; each block is an independent pair. STOP: none, the premium is the floor and a losing share is worth 0.00 (convention 8). TARGET: resolution, where a complete pair pays exactly 1.00 per share. TIME EXIT: stop attempting leg 2 with 60s remaining and mark the block UNPAIRED. An UNPAIRED leg is held to resolution and its PnL is charged to this strategy, never excluded."
data_requirements: "Live CLOB book for both tokens of the same market. For backtest, per-token price history via conditionId at 10s resolution or finer. The depth blocker from pm_dynamic_rotation applies but is WEAKER here: both legs rest at prices well inside the book rather than lifting through it, so a midpoint series is a usable if imperfect proxy for whether the price was ever reached. It still cannot tell us whether our size would have filled, so any result must report assumed fill size as an explicit parameter."
related_graveyard_findings: "No PREDICTION_MARKET rows exist in the graveyard. box_builder (ported, NOT_TESTED per D-268) is the SIMULTANEOUS version of this idea: it quotes both sides at once with the combined cap at 0.94 and is therefore never directional. This proposal is the temporal version and IS directional between legs, so it carries a risk box_builder does not have and the two must be scored separately. If box_builder later passes and this fails, the difference is leg-completion risk and that is the finding."
kind: edge_hypothesis
status: PROPOSED
source: "Dan1ro0 concept 4B (temporal arbitrage)"
---


## Edge arithmetic

A complete pair redeems at exactly 1.00 per share. The profit is 1.00 minus
what the pair cost:

| Pair cost | Profit per share | bps of outlay |
|---|---|---|
| 0.76 (Dan1ro0's example) | 24c | 3158bps |
| 0.94 (our cap) | 6c | 638bps |
| 0.97 (kill threshold) | 3c | 310bps |

We use the 0.94 cap, not Dan1ro0's example. His 0.76 is a single illustration
with no sample size behind it, and taking it as the expected value would be
citing a marketing number as a result.

638bps is the estimate. It assumes every pair completes, which is the whole
risk.

## The real risk is leg-completion, not direction

Between leg 1 and leg 2 this strategy holds a naked directional position. If
BTC keeps moving the way that made leg 1 cheap, leg 2 never gets cheap enough
and the window expires with an unpaired leg that resolves to 0.00.

So the arithmetic that matters is not the pair profit, it is:

    E = P(complete) * 6c - P(unpaired) * (expected loss on a naked leg)

At a 47c leg-1 entry an unpaired leg loses its full 47c when wrong. Break-even
completion rate is roughly 47 / (47 + 6) = 89%. **A 60% completion rate does not
make this profitable, it makes it a losing directional strategy wearing an
arbitrage label.**

That is why the kill condition charges UNPAIRED legs to this strategy. A version
of this proposal that reported only completed pairs would show a clean 638bps
and be entirely fictional. It is also why the 60% completion figure appears in
the kill condition as a floor rather than a target: below it, stop, but clearing
it is not sufficient on its own and the net-PnL clause is the binding one.

## Honest position on the estimate

638bps is the gross-if-completed figure and it is the number the schema asks
for. The expected value after completion risk could easily be negative. This
proposal is worth testing precisely because the completion rate is the unknown
and it is cheaply measurable: it needs no depth data, only whether a price was
ever touched.

## Block sizing

Small blocks (50 shares) rather than one large pair, per Dan1ro0's conservative
variant. This bounds the naked exposure per block instead of putting the whole
position on one leg and hoping. It also means partial success is possible: three
complete pairs and one unpaired block is a much better outcome than one large
unpaired position.
