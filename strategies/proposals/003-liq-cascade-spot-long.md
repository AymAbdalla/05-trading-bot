---
name: "liq_cascade_spot_long"
thesis: "A cluster of forced SHORT liquidations on perp venues creates mechanical, price-insensitive buying that spot briefly continues past, because the liquidation engine must fill regardless of price while discretionary sellers step away."
expected_edge_bps: 70
kill_condition: "Any one of: net edge below 30bps over 200 or more signals; fewer than 200 qualifying signals in 24 months of recorded data, in which case the verdict is NOT_TESTED and not FAIL (convention 11); the move reversing through the entry within 5 minutes on more than 55% of signals. Scored by the existing vectorized harness against cost_model.py Binance.US spot taker fees."
asset_class: "CRYPTO"
entry_exit_rules: "SIGNAL: 50M USD or more of SHORT liquidations on BTC across Binance and Bybit perps within a trailing 2-minute window. ENTER: market buy BTC spot at the close of the first 1-minute bar after the cluster ends. Long only: this is SPOT, and the long-liquidation (downside) case is untradeable for us without borrow, so half the original signal is discarded by construction. STOP: 1.0 ATR(14, 1m) below entry, strictly below (convention 8). TARGET: 1.5 ATR. TIME EXIT: 15 minutes. RUN THIS COHORT WITH apply_confirmation_stack=False: a liquidation cascade is a violent down-move, so `close > rising EMA50` is false by construction and would remove the signal population entirely, exactly as it did to V5_capitulation_equity."
data_requirements: "BLOCKER, and it is the reason this cannot be built this week. Historical liquidation data does not exist on any public REST endpoint. Binance !forceOrder@arr and Bybit allLiquidation are real-time WebSocket streams with NO history, and our CSVs contain OHLCV only. This proposal therefore requires standing up a liquidation recorder and WAITING for the data to accumulate before it can be scored at all. Until then its verdict is NOT_TESTED. Also needs: BTC spot 1m OHLCV (have it) and ATR(14) (have it)."
related_graveyard_findings: "The forced-flow family has been tried twice. V2_liquidation_echo is in the graveyard with real trades and an observed best PF of 4.4501. V5_forced_flow_crypto is one of the nine non-firing strategies (11 trades across 7,898 rows), and we have now measured WHY: its funding table covers 2025-09-12 to 2026-08-13 while the Binance price slices run 2025-07-20 to 2025-09-30, an 18-day overlap, so 3,279 bar evaluations had no funding data at all. That is a DATA COVERAGE failure, not a threshold failure, and it is the same failure mode this proposal is exposed to. See the body and docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md."
kind: edge_hypothesis
status: PROPOSED
source: "moondevonyt liq_cascade_chaser, ported from Polymarket to spot"
---


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
