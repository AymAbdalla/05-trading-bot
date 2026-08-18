---
name: "pm_cross_window_relative_value"
thesis: "The BTC 5m and 15m windows price the same underlying path but absorb new information at different speeds, so when the 15m implied probability has not moved to reflect a move the 5m chain has already priced, the 15m side is stale and reprices toward the 5m-implied value."
expected_edge_bps: 400
kill_condition: "Any one of: net resolution PnL per trade below 1.0c per share (200bps of a 50c premium) over 200 or more trades; measured gap half-life above 90 seconds, since a divergence that closes more slowly than the window remaining cannot be harvested; fewer than 200 events at abs(score) >= 2 in 90 days of paired history. Scored by backtest/polymarket_harness.py."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "Compute an independent fair value for the 15m window and for the implied 15m outcome derived from the 5m chain, from the same BTC state. GAP = implied_15m - implied_from_5m_chain. SCORE = (gap - trailing_mean_gap) / trailing_gap_stdev over a 30-day window. ENTER the stale side when abs(SCORE) >= 2 AND that side ask is at or below 0.60. ONE leg only, no hedge. STOP: none, the premium is the floor (convention 8). TARGET: resolution at 1.00. TIME EXIT: no entry with under 90s remaining in the stale window."
data_requirements: "Simultaneous CLOB books for the 5m and 15m BTC markets, which means resolving both slugs and all four token ids per observation. BTC spot. BLOCKER: the trailing gap distribution needs 30 days of PAIRED history across both windows, and we have none. The mean and stdev in the score are not tunable constants, they are measured quantities, and until they are measured this strategy has no entry rule at all. That accumulation is the first build step, not a detail."
related_graveyard_findings: "No PREDICTION_MARKET rows exist in the graveyard. corridor_collector (ported, NOT_TESTED per D-268) trades the same two windows and is the nearest neighbour, but it is a different bet and must not be pooled with this: corridor_collector buys the leading side of the 15m AND the opposite side of the final 5m, so at least one leg always wins and the pair is floored at 1.00. It is a structural floor play. This is a relative-value play with ONE leg and no floor, which can lose its entire premium outright. Same markets, opposite risk shape."
kind: edge_hypothesis
status: PROPOSED
source: "Dan1ro0 concept 3 (cross-market relative value)"
---


## Edge arithmetic

A 2-sigma divergence between the two windows' implied probabilities is roughly
4c on these books. Assume only HALF the gap closes before expiry, which is the
conservative read of a mean-reversion trade that must complete inside a fixed
window:

    2c captured / 50c premium = 400bps gross

Not the 800bps a full close would give. Assuming full convergence inside a
5-to-15 minute window is the standard way a relative-value backtest flatters
itself, and the half-life clause in the kill condition exists to catch it: if
the measured half-life is above 90 seconds, the gap does not close in time and
the 400bps was never available.

## Why this is not corridor_collector

Worth being explicit, because they trade the same two markets and a reader
skimming would pool them.

| | corridor_collector | this |
|---|---|---|
| Legs | 2, opposite sides | 1 |
| Worst case | pair floored at 1.00, so a leg always wins | lose the full premium |
| Bet | structure | mispricing |
| Needs a fair-value model | no | yes |

corridor_collector is a floor play that works whether or not anyone is
mispricing anything. This needs the 15m side to actually be stale. If both are
tested and corridor_collector passes while this fails, the finding is that the
structure was doing the work and there was no stale-pricing edge, which is a
useful thing to learn and is only learnable if they are scored separately.

## Why the edge would persist

The two windows have different participant sets. The 15m market attracts
slower, more directional flow; the 5m market attracts the bots. The lag is a
composition effect rather than a latency effect, which again means our speed is
adequate.

## What would make this wrong

The gap may be a real risk premium rather than staleness. A 15m window carries
more variance than a 5m window, so a systematic difference in implied
probability could be correct pricing of that extra variance, not an error.
Standardising against the trailing distribution is supposed to strip out the
typical gap and leave only the anomaly, but if the risk premium itself moves
with volatility, the score will read a regime change as a signal. Testing this
means checking whether the entry score predicts convergence CONDITIONAL on
realised volatility, not just on average.

This is also the concrete reason the 30 days of paired history is a blocker and
not a nice-to-have: without the distribution there is no score, and with too
short a distribution the score is measuring the sample rather than the market.

## A warning inherited from the spot side

The trailing mean and stdev in the score are measured quantities, and the moment
they are frozen into constants they become assumptions with expiry dates
(convention 17). This is the same class of mistake as `COST_FLOOR = -0.30`,
which kept its number while the data distribution moved underneath it and turned
a null result into an apparent 90% survival rate. Recompute the distribution on
a rolling basis, and if a future version hardcodes a "typical gap", that is the
first thing to suspect when the results improve.
