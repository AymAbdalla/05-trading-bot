---
name: "pm_dynamic_rotation"
thesis: "In BTC Up/Down 5m markets the CLOB reprices more slowly than BTC spot on an intra-window reversal, because most resting size belongs to participants who entered at window open and hold to expiry; a bot holding an independent fair value can take the newly favoured side while the book still reflects the pre-reversal state."
expected_edge_bps: 1200
kill_condition: "Any one of: net resolution PnL per rotation below 1.5c per share (300bps of a 50c premium) over 200 or more completed rotations scored by backtest/polymarket_harness.py; median rotations per market above 3 (noise-driven thrashing); realised round-trip spread cost above 4c per rotation."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "Maintain a fair value P for the Up token, revised Bayesianly from BTC distance to strike, speed of the last move, short-horizon vol, and seconds to expiry. ENTER the side whose ask is at least 6c below its fair value, capped at 0.60. ROTATE (buy the opposite side) only when the fair-value gap flips sign AND exceeds 6c in the new direction, and not more than 3 times per market. STOP: none. The premium paid is the floor and a losing share is worth exactly 0.00, which satisfies convention 8. TARGET: resolution at 1.00. TIME EXIT: no new entry or rotation with under 45s remaining, because a rotation that cannot be repriced before expiry is a directional bet, not a rotation."
data_requirements: "BTC spot at 1s or finer (Binance or Coinbase public WS). Polymarket CLOB book with DEPTH for both tokens at 1Hz or better. Window open price and expiry timestamp per market (Gamma). BLOCKER: we have no historical CLOB book depth. Price history via conditionId gives a midpoint series only, and rotation edge is depth-sensitive, so a midpoint-only backtest would OVERSTATE it. This must either record depth forward for 30 days before it can be scored, or be scored with an explicit depth assumption that is stated in the result."
related_graveyard_findings: "No PREDICTION_MARKET rows exist in the graveyard: 0 of 138 asset-class cells, and the class is absent from the coverage map entirely. The closest thing already written is mid_price_continuation (ported, NOT_TESTED per D-268), which buys the leading side and never rotates; this proposal is that strategy plus a reversal rule, so the two must not be pooled. The nearest spot analogue among the 55 swept strategies is V4_trend_reclaim, whose 66 trades in 9,460 rows we have now diagnosed: 33 raw signals of which 27 were removed by a volume filter designed for intraday breakouts. So the reversal/reclaim family was never actually tested, which is a reason to test it, not a reason to expect it to work."
kind: edge_hypothesis
status: PROPOSED
source: "Dan1ro0 concept 4A (dynamic position rotation)"
---


## Edge arithmetic

The rotation trigger is a 6c gap between our fair value and the opposing side's
ask. On a 50c premium that is 6/50 = 1200bps gross.

That number is gross and it is per rotation event, not per market. Costs:

| Item | Cost |
|---|---|
| Lifting the new side's ask through a thin book | ~1.5c |
| Taker fee (0.0 today, a config knob per convention 17) | 0.0c |
| Model-uncertainty safety margin | 1.5c |
| **Remaining** | **~3c = 600bps** |

The 6c trigger is what makes this survivable. At a 2c trigger the same cost
stack eats the whole thing, which is the specific failure Dan1ro0 warns about:
noise causes repeated switching and the edge leaves through the spread.

## Why the edge would persist

The counterparty is not a slower bot, it is a holder. Someone who bought Up at
window open at 45c and watched BTC reverse is not repricing their resting size
every second; they are waiting for expiry. The lag is structural to who is on
the other side, not a latency race, which is why this is worth testing at our
speed rather than needing colocation.

## What would make this wrong

The obvious way: the book is thin enough that the 6c gap is an artifact of one
stale quote for 5 shares, and any size that matters walks straight through it.
That is a depth question, and depth is exactly the data we do not have
historically. This is the honest reason the proposal is not ready to build: not
that the thesis is weak, but that we cannot score it without recording depth
forward first.

The subtler way: our fair value treats correlated signals as independent. A BTC
move causes volume, imbalance, ETH and SOL moves all at once. Counting those as
five confirmations manufactures confidence, the gap looks like 6c when it is
2c, and the strategy rotates into noise. Dan1ro0 flags this and it is the part
of the model that needs adversarial testing before anything is built.

## Sizing note

Fractional Kelly at 20% (Dan1ro0 concept 6) is the intended sizing, but that is
a separate module (`engine/polymarket/sizing.py`, not built) and this proposal
does not depend on it. Flat sizing under the notional cap is enough to test
whether the edge exists at all.
