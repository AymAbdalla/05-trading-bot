"""The candidate proposals Forge screens.

Separated from `agents/forge.py` so the machinery (loading, gap analysis,
schema enforcement, refusal accounting) can be tested without the content, and
so the content can be rewritten every cycle without touching the validator.

Each dict is a CANDIDATE, not a proposal. `agents/forge.py` decides which ones
become files. As of the 2026-08-17 creative mandate the refusal set is much
smaller: a candidate is refused for a missing core field, an edge below its
INSTRUMENT'S floor (30bps on spot, 200bps = one 1c tick on a 50c binary), a
kill condition with no number or no named harness, or a repair/experiment
claiming an edge it cannot know. Duplicating a swept name, an unlisted asset
class and a missing graveyard link are now WARNINGS, not refusals. Candidates
that do get refused are part of the result, so they stay in this list rather
than being deleted.

Numbers in `expected_edge_bps` are ESTIMATES made before any test, and every
body below shows the arithmetic that produced them. Convention 15: when the
harness scores one of these, the estimate gets corrected against the log.
"""

# Sources, so a reader can trace a claim back:
#   Dan1ro0      docs/handoffs/from-raven/2026-08-17-dan1ro0-article-analysis.md
#   moondevonyt  research/moondevonyt-polymarket-examples/ (his logs, his setup)
#   diagnosis    docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md (ours,
#                measured, reproduces the graveyard's trade counts exactly)
# The first two are not evidence. Both are hypotheses until our graveyard says
# otherwise (convention 3, restated in D-267). The third is measurement.


PM_DYNAMIC_ROTATION = {
    'name': 'pm_dynamic_rotation',
    'kind': 'edge_hypothesis',
    'asset_class': 'PREDICTION_MARKET',
    'source': 'Dan1ro0 concept 4A (dynamic position rotation)',
    'thesis': (
        'In BTC Up/Down 5m markets the CLOB reprices more slowly than BTC spot '
        'on an intra-window reversal, because most resting size belongs to '
        'participants who entered at window open and hold to expiry; a bot '
        'holding an independent fair value can take the newly favoured side '
        'while the book still reflects the pre-reversal state.'),
    'expected_edge_bps': 1200,
    'kill_condition': (
        'Any one of: net resolution PnL per rotation below 1.5c per share '
        '(300bps of a 50c premium) over 200 or more completed rotations scored '
        'by backtest/polymarket_harness.py; median rotations per market above '
        '3 (noise-driven thrashing); realised round-trip spread cost above 4c '
        'per rotation.'),
    'entry_exit_rules': (
        'Maintain a fair value P for the Up token, revised Bayesianly from BTC '
        'distance to strike, speed of the last move, short-horizon vol, and '
        'seconds to expiry. ENTER the side whose ask is at least 6c below its '
        'fair value, capped at 0.60. ROTATE (buy the opposite side) only when '
        'the fair-value gap flips sign AND exceeds 6c in the new direction, '
        'and not more than 3 times per market. STOP: none. The premium paid is '
        'the floor and a losing share is worth exactly 0.00, which satisfies '
        'convention 8. TARGET: resolution at 1.00. TIME EXIT: no new entry or '
        'rotation with under 45s remaining, because a rotation that cannot be '
        'repriced before expiry is a directional bet, not a rotation.'),
    'data_requirements': (
        'BTC spot at 1s or finer (Binance or Coinbase public WS). Polymarket '
        'CLOB book with DEPTH for both tokens at 1Hz or better. Window open '
        'price and expiry timestamp per market (Gamma). BLOCKER: we have no '
        'historical CLOB book depth. Price history via conditionId gives a '
        'midpoint series only, and rotation edge is depth-sensitive, so a '
        'midpoint-only backtest would OVERSTATE it. This must either record '
        'depth forward for 30 days before it can be scored, or be scored with '
        'an explicit depth assumption that is stated in the result.'),
    'related_graveyard_findings': (
        'No PREDICTION_MARKET rows exist in the graveyard: 0 of 138 asset-class '
        'cells, and the class is absent from the coverage map entirely. The '
        'closest thing already written is mid_price_continuation (ported, '
        'NOT_TESTED per D-268), which buys the leading side and never rotates; '
        'this proposal is that strategy plus a reversal rule, so the two must '
        'not be pooled. The nearest spot analogue among the 55 swept strategies '
        'is V4_trend_reclaim, whose 66 trades in 9,460 rows we have now '
        'diagnosed: 33 raw signals of which 27 were removed by a volume filter '
        'designed for intraday breakouts. So the reversal/reclaim family was '
        'never actually tested, which is a reason to test it, not a reason to '
        'expect it to work.'),
    'body': """
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
""",
}


PM_TEMPORAL_ARBITRAGE = {
    'name': 'pm_temporal_arbitrage',
    'kind': 'edge_hypothesis',
    'asset_class': 'PREDICTION_MARKET',
    'source': 'Dan1ro0 concept 4B (temporal arbitrage)',
    'thesis': (
        'Within one 5m window the Up and Down legs are rarely cheap at the same '
        'instant but are often cheap at different instants, so buying each leg '
        'when its own price is depressed builds a pair whose combined cost is '
        'below 1.00, and a complete pair redeems at exactly 1.00 whatever the '
        'outcome.'),
    'expected_edge_bps': 638,
    'kill_condition': (
        'Any one of: median completed-pair cost above 0.97 (under 310bps) over '
        '200 or more attempted pairs; fewer than 60% of first legs completing '
        'their second leg before expiry; net PnL negative once UNPAIRED legs '
        'are charged to this strategy at their realised resolution PnL. Scored '
        'by backtest/polymarket_harness.py.'),
    'entry_exit_rules': (
        'LEG 1: buy either side when its ask is at or below 0.47, in blocks of '
        'at most 50 shares, leaving at least 0.47 of pair budget for leg 2. '
        'LEG 2: buy the opposite side when its ask is at or below '
        '(0.94 - leg1_avg_price). Repeat in blocks; each block is an '
        'independent pair. STOP: none, the premium is the floor and a losing '
        'share is worth 0.00 (convention 8). TARGET: resolution, where a '
        'complete pair pays exactly 1.00 per share. TIME EXIT: stop attempting '
        'leg 2 with 60s remaining and mark the block UNPAIRED. An UNPAIRED leg '
        'is held to resolution and its PnL is charged to this strategy, never '
        'excluded.'),
    'data_requirements': (
        'Live CLOB book for both tokens of the same market. For backtest, '
        'per-token price history via conditionId at 10s resolution or finer. '
        'The depth blocker from pm_dynamic_rotation applies but is WEAKER here: '
        'both legs rest at prices well inside the book rather than lifting '
        'through it, so a midpoint series is a usable if imperfect proxy for '
        'whether the price was ever reached. It still cannot tell us whether '
        'our size would have filled, so any result must report assumed fill '
        'size as an explicit parameter.'),
    'related_graveyard_findings': (
        'No PREDICTION_MARKET rows exist in the graveyard. box_builder '
        '(ported, NOT_TESTED per D-268) is the SIMULTANEOUS version of this '
        'idea: it quotes both sides at once with the combined cap at 0.94 and '
        'is therefore never directional. This proposal is the temporal version '
        'and IS directional between legs, so it carries a risk box_builder does '
        'not have and the two must be scored separately. If box_builder later '
        'passes and this fails, the difference is leg-completion risk and that '
        'is the finding.'),
    'body': """
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
""",
}


LIQ_CASCADE_SPOT_LONG = {
    'name': 'liq_cascade_spot_long',
    'kind': 'edge_hypothesis',
    'asset_class': 'CRYPTO',
    'source': "moondevonyt liq_cascade_chaser, ported from Polymarket to spot",
    'thesis': (
        'A cluster of forced SHORT liquidations on perp venues creates '
        'mechanical, price-insensitive buying that spot briefly continues past, '
        'because the liquidation engine must fill regardless of price while '
        'discretionary sellers step away.'),
    'expected_edge_bps': 70,
    'kill_condition': (
        'Any one of: net edge below 30bps over 200 or more signals; fewer than '
        '200 qualifying signals in 24 months of recorded data, in which case '
        'the verdict is NOT_TESTED and not FAIL (convention 11); the move '
        'reversing through the entry within 5 minutes on more than 55% of '
        'signals. Scored by the existing vectorized harness against '
        'cost_model.py Binance.US spot taker fees.'),
    'entry_exit_rules': (
        'SIGNAL: 50M USD or more of SHORT liquidations on BTC across Binance '
        'and Bybit perps within a trailing 2-minute window. ENTER: market buy '
        'BTC spot at the close of the first 1-minute bar after the cluster ends. '
        'Long only: this is SPOT, and the long-liquidation (downside) case is '
        'untradeable for us without borrow, so half the original signal is '
        'discarded by construction. STOP: 1.0 ATR(14, 1m) below entry, strictly '
        'below (convention 8). TARGET: 1.5 ATR. TIME EXIT: 15 minutes. RUN THIS '
        'COHORT WITH apply_confirmation_stack=False: a liquidation cascade is a '
        'violent down-move, so `close > rising EMA50` is false by construction '
        'and would remove the signal population entirely, exactly as it did to '
        'V5_capitulation_equity.'),
    'data_requirements': (
        'BLOCKER, and it is the reason this cannot be built this week. '
        'Historical liquidation data does not exist on any public REST '
        'endpoint. Binance !forceOrder@arr and Bybit allLiquidation are '
        'real-time WebSocket streams with NO history, and our CSVs contain '
        'OHLCV only. This proposal therefore requires standing up a '
        'liquidation recorder and WAITING for the data to accumulate before it '
        'can be scored at all. Until then its verdict is NOT_TESTED. Also '
        'needs: BTC spot 1m OHLCV (have it) and ATR(14) (have it).'),
    'related_graveyard_findings': (
        'The forced-flow family has been tried twice. V2_liquidation_echo is in '
        'the graveyard with real trades and an observed best PF of 4.4501. '
        'V5_forced_flow_crypto is one of the nine non-firing strategies (11 '
        'trades across 7,898 rows), and we have now measured WHY: its funding '
        'table covers 2025-09-12 to 2026-08-13 while the Binance price slices '
        'run 2025-07-20 to 2025-09-30, an 18-day overlap, so 3,279 bar '
        'evaluations had no funding data at all. That is a DATA COVERAGE '
        'failure, not a threshold failure, and it is the same failure mode this '
        'proposal is exposed to. See the body and '
        'docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md.'),
    'body': """
## Edge arithmetic, and why the threshold is 50M and not 10M

moondevonyt's original fires on 10k USD of trailing-2-minute liquidations and
trades the Polymarket binary. Ported to spot, the arithmetic changes completely,
because on spot we pay a real round-trip cost and collect a real price move
rather than a 1.00 redemption.

Binance.US spot cost stack:

| Item | bps |
|---|---|
| Taker fee in | 10 |
| Taker fee out | 10 |
| Spread and slippage on BTC | ~2 |
| **Round-trip cost floor** | **~22** |

At moondevonyt's 10k trigger the expected continuation on BTC spot is on the
order of 10-30bps. Against a 22bps cost floor that nets somewhere between -12
and +8bps. **That version is dead on arrival and this proposal does not make
it.** Worth saying explicitly, because "port the strategy" was the task and the
honest answer is that the strategy as written does not survive the translation.

Raising the trigger to 50M USD selects the cascades large enough to move spot
meaningfully. Estimated gross continuation at that tier: ~70bps. Net after the
22bps floor: ~48bps. That clears the 30bps bar with room, which is the only
reason this proposal exists at that threshold and not the original one.

**The 70bps is an estimate, not a measurement.** There is no liquidation history
to measure it against (see data_requirements), so it comes from the size of the
cascade rather than from our data. Convention 15: correct it against the log the
first time this is actually scored.

## The trap this proposal is walking into, named up front

The forced-flow family already has a member that does not fire, and we now know
precisely why. V5_forced_flow_crypto did not fail because its threshold was too
tight in the abstract; it failed because **the data its gate depended on barely
overlapped the data it was tested on** - an 18-day intersection between the
funding table and the Binance price slices, leaving 3,279 bar evaluations with
no funding date at all. Those evaluations currently sit inside FAIL rows for a
series the harness structurally could not evaluate, which convention 11 and
D-255 say should be NOT_TESTED.

This proposal has exactly the same shape of exposure. Its gate depends on
liquidation data we do not have and would have to record forward. If the
recorder runs for 60 days and the price history we score against runs for 24
months, we will reproduce V5_forced_flow_crypto's failure precisely: a strategy
that reads as FAIL when it was never evaluable.

So the mitigation is a requirement, not a nicety: **the scored window must be
the INTERSECTION of the liquidation record and the price history, and any bar
outside it is NOT_TESTED, never FAIL.** That constraint goes into the harness
call, not into a reviewer's memory.

The second exposure is frequency. Raising a threshold 5000x, from 10k to 50M, is
the move that empties a signal population. Here the frequency question is IN the
kill condition: fewer than 200 signals in 24 months and the verdict is
NOT_TESTED, not FAIL. Rough sanity check: 50M USD short-liquidation clusters on
BTC are a several-times-per-month event in normal conditions and cluster heavily
in volatile regimes, so plausibly 100-300 signals over 24 months. **This may
well come back underpowered.** Known and accepted, not a surprise to be
explained later.

## What long-only costs us

Spot cannot short, so the downside cascade (long liquidations, forced selling)
is not tradeable here. That discards roughly half the signal population and
makes the underpowered risk above worse. Recovering it needs futures or options,
which D-267 puts in scope for backtest but which is a different proposal.

## One harness note that is not optional

This strategy buys immediately after a violent move. `close > rising EMA50` is
false by construction at that moment. The main sweep applies that filter to
every strategy by default (`vectorized_harness.py:610-611`), and it removed 92%
of V5_capitulation_equity's candidate days and 100% of
V2_vwap_magnet_sessionatr's signals. Running this cohort with the stack on would
produce a zero and it would mean nothing.
""",
}


PM_CROSS_WINDOW_RELATIVE_VALUE = {
    'name': 'pm_cross_window_relative_value',
    'kind': 'edge_hypothesis',
    'asset_class': 'PREDICTION_MARKET',
    'source': 'Dan1ro0 concept 3 (cross-market relative value)',
    'thesis': (
        'The BTC 5m and 15m windows price the same underlying path but absorb '
        'new information at different speeds, so when the 15m implied '
        'probability has not moved to reflect a move the 5m chain has already '
        'priced, the 15m side is stale and reprices toward the 5m-implied '
        'value.'),
    'expected_edge_bps': 400,
    'kill_condition': (
        'Any one of: net resolution PnL per trade below 1.0c per share (200bps '
        'of a 50c premium) over 200 or more trades; measured gap half-life '
        'above 90 seconds, since a divergence that closes more slowly than the '
        'window remaining cannot be harvested; fewer than 200 events at '
        'abs(score) >= 2 in 90 days of paired history. Scored by '
        'backtest/polymarket_harness.py.'),
    'entry_exit_rules': (
        'Compute an independent fair value for the 15m window and for the '
        'implied 15m outcome derived from the 5m chain, from the same BTC '
        'state. GAP = implied_15m - implied_from_5m_chain. SCORE = '
        '(gap - trailing_mean_gap) / trailing_gap_stdev over a 30-day window. '
        'ENTER the stale side when abs(SCORE) >= 2 AND that side ask is at or '
        'below 0.60. ONE leg only, no hedge. STOP: none, the premium is the '
        'floor (convention 8). TARGET: resolution at 1.00. TIME EXIT: no entry '
        'with under 90s remaining in the stale window.'),
    'data_requirements': (
        'Simultaneous CLOB books for the 5m and 15m BTC markets, which means '
        'resolving both slugs and all four token ids per observation. BTC spot. '
        'BLOCKER: the trailing gap distribution needs 30 days of PAIRED history '
        'across both windows, and we have none. The mean and stdev in the score '
        'are not tunable constants, they are measured quantities, and until '
        'they are measured this strategy has no entry rule at all. That '
        'accumulation is the first build step, not a detail.'),
    'related_graveyard_findings': (
        'No PREDICTION_MARKET rows exist in the graveyard. corridor_collector '
        '(ported, NOT_TESTED per D-268) trades the same two windows and is the '
        'nearest neighbour, but it is a different bet and must not be pooled '
        'with this: corridor_collector buys the leading side of the 15m AND the '
        'opposite side of the final 5m, so at least one leg always wins and the '
        'pair is floored at 1.00. It is a structural floor play. This is a '
        'relative-value play with ONE leg and no floor, which can lose its '
        'entire premium outright. Same markets, opposite risk shape.'),
    'body': """
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
""",
}


NONFIRING_NINE_REPAIR = {
    'name': 'nonfiring_nine_repair',
    'kind': 'repair',
    'asset_class': 'MULTI',
    'source': 'docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md (measured)',
    'thesis': (
        'The nine strategies that never fire are not nine independent bugs: '
        'measurement shows two genuine strategy defects and three systemic '
        'harness conditions, and seven of the nine were removed by the harness '
        'before their logic was ever judged.'),
    'expected_edge_bps': None,
    'kill_condition': (
        'Per strategy: if after its named fix it still fires on under 1% of '
        'rows tested, it is retired from the roster rather than repaired a '
        'second time. For the batch: if the two systemic fixes '
        '(apply_confirmation_stack=False for the mean-reversion cohort, and '
        'min_idx lowered to max(min_bars, 25) for daily and weekly series) do '
        'not raise at least 4 of the 9 above the 1% firing rate, the '
        'shared-cause theory is wrong and these revert to nine separate '
        'problems triaged individually. Both clauses are measured by '
        're-running backtest/run_incremental_graveyard.py (which drives '
        'backtest/vectorized_harness.py) and reading the per-strategy trade '
        'counts out of research/judge_evidence_pack.json.'),
    'entry_exit_rules': (
        'Not applicable: this is a repair, not a new entry rule. The per-clause '
        'measurements that constitute the diagnosis are already done and are '
        'recorded in docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md. What '
        'this proposal asks for is a ruling on the two systemic changes, which '
        'move the graveyard headline numbers, followed by the two one-line '
        'strategy fixes and the deletion of C2 stale rows.'),
    'data_requirements': (
        'The existing CSVs for everything except V5_forced_flow_crypto, which '
        'needs the FUNDING_STRESS_PCTL table extended back to cover the Binance '
        'price slices (current overlap is 18 days). Until it is extended, the '
        '3,279 bar evaluations with no funding date are NOT_TESTED, not FAIL '
        '(convention 11, D-255).'),
    'related_graveyard_findings': (
        'These nine ARE the graveyard finding. C2 (0 trades / 264 rows), '
        'V2_vwap_magnet_sessionatr (0 / 9,460), V5_capitulation_equity '
        '(0 / 9,460), V5_forced_flow_crypto (11 / 7,898), '
        'V3_intraday_momentum_crypto (22 / 9,306), V4_gap_hold_proxy '
        '(33 / 9,460), rising_three_methods (55 / 9,460), V4_trend_reclaim '
        '(66 / 9,460), rsi_extreme (66 / 9,460). None contributes a PASS row, '
        'so none inflates the 155 distinct findings. D-266 is the binding '
        'context: the duplicate_strategies assertion flags C2 as 100% identical '
        'to all 54 other strategies, which is not 54 duplicates, it is C2 '
        'producing zero trades so every comparison is empty against empty.'),
    'body': """
## What this is and is not

This is a repair, so `expected_edge_bps` is null. Convention 11: the edge of a
strategy that has never fired is not knowable, and unknown is not zero. Writing
a number here would put a fabricated figure into the record, and fabricated
figures get cited.

The full measured diagnosis is in
`docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md`. It re-runs the sweep's
own pipeline with per-clause counters and reproduces the graveyard's exact trade
counts for four of the nine, so the numbers below are measurements, not reads.

## The finding: 2 bugs and 3 systemic conditions, not 9 bugs

**Two genuine strategy defects.**

`rsi_extreme` requires `rsi14 < 35` AND `close > ema50`. Measured over 42,010
bars: 4,783 bars satisfy the first, 21,982 satisfy the second, and **zero
satisfy both.** RSI(14) conditional on `close > EMA50` has a hard floor at
36.26, so the threshold sits below the support of the conditional distribution.
This is category (b), unsatisfiable, not a tight threshold. One-character fix.

`C2` computes its anchor lookback as `24 * 4` BARS while meaning four DAYS. True
only for hourly bars; on 5m it reaches back 8 hours and can never find Friday.
Measured: 100% anchor failure on every sub-hourly series tested.

**Three systemic conditions produce the other seven.**

1. *Bar starvation.* `min_idx = 100` against a last-20% test slice leaves daily
   series a median of ONE scannable bar, 5,100 across 175 series, which is 5.01%
   of the daily bars on disk. This is the highest-leverage item in the list and
   it reaches well beyond these nine: it means the daily evidence behind
   "509,080 tests" is overstated by roughly 20x.

2. *The confirmation stack is a trend filter applied to every strategy.* The
   sweep never sets `apply_confirmation_stack` or `require_regime_uptrend`, so
   both default to True and every signal must satisfy `close > rising EMA50`. It
   removed 100% of V2_vwap_magnet_sessionatr, 99.5% of its control twin, 92% of
   V5_capitulation_equity's candidate days, 87% of V3_intraday_momentum_crypto
   and 82% of V4_trend_reclaim. **A mean-reversion strategy filtered through
   "price is above a rising EMA50" has not been tested and found wanting. It has
   not been tested.**

3. *Unvalidated grid and coverage assumptions.* 1h equity bars stamp on the
   hour, so V2's `[930, 945)` trigger box is permanently empty. 1h crypto bars
   stamp 23:00, so V3's 23:30 trigger is unreachable. The funding table overlaps
   the Binance price slices by 18 days. None of these raise; they produce silent
   zeros that read as verdicts.

## Why this needs a ruling before execution, not after

Items 1 and 2 change the graveyard's headline numbers. Lowering `min_idx`
multiplies the daily evidence base; turning off the confirmation stack for the
mean-reversion cohort changes what counts as a tested strategy. Both are
defensible and both are arguably corrections of a measurement error rather than
a change in method, but neither is Forge's call to make and neither should
happen quietly between two sweeps.

The specific risk: re-running with these changes will make some numbers look
better, and convention 17 says that when a metric improves after a step that
only loosened a filter, suspect the baseline before believing the result. That
is exactly what happened with `COST_FLOOR = -0.30` and the conditional-edge
false positive. Ruling first, then run, then compare against the pre-change
numbers deliberately.

## Ordering, by value per unit of work

1. `apply_confirmation_stack=False` for the mean-reversion cohort, then re-run.
   A config change that unblocks four strategies. The machinery already exists
   and is already used by `constraint_sweep.py:64` and `dispersion_gate.py:353`.
2. `min_idx` to `max(strategy.min_bars, 25)` for daily and weekly series.
3. `rsi_extreme` threshold and `C2` lookback units. One line each, one D-number
   each.
4. Delete C2's 9,042 stale rows before anyone cites them. They carry a reason
   string that no longer exists in the codebase, which means they were written
   by a pre-fix harness and C2 has never run under current code.

## The honest limit on this claim

The full sweep was not re-run, so the "would become N findings" figures in the
diagnosis are raw-signal counts, not PASS counts. A strategy that starts firing
may still fail on economics, and several probably will.

The claim is narrow and should be read narrowly: **seven of these nine were
never given the chance to lose.**
""",
}


CANDIDATES = [
    PM_DYNAMIC_ROTATION,
    PM_TEMPORAL_ARBITRAGE,
    LIQ_CASCADE_SPOT_LONG,
    NONFIRING_NINE_REPAIR,
    PM_CROSS_WINDOW_RELATIVE_VALUE,
]
