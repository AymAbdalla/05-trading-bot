# Handoff: inverse fair-value arb, liquidation strategies wired, feeds live

**Session:** Cody, 2026-08-18 05:26 - 06:00
**Branch:** main, nothing committed, tree left for review
**Registry:** `build_strategies()` 11 -> **15**

---

## Headline: two findings that outrank the code

### 1. Binance is GEOBLOCKED from this machine. The liquidation tape is Bybit-only.

`engine/feeds/liquidation_recorder.py` connects to Binance and reports
`CONNECTED, connects=1, reconnects=0`. It will never deliver a single row.

Measured, not inferred:

```
https://fapi.binance.com/fapi/v1/ping  ->  HTTP 451
  "Service unavailable from a restricted location"
wss://fstream.binance.com/ws/btcusdt@aggTrade  ->  0 frames in 25s
```

`btcusdt@aggTrade` is one of the highest-volume streams in crypto. Zero frames
is not a quiet market, it is a wall. The websocket TLS handshake succeeds, which
is why the recorder's own health line reads as healthy. **A silent permanent
zero that looks like uptime.**

The control that proves the code is fine:

```
wss://stream.bybit.com/v5/public/linear  publicTrade.BTCUSDT
  -> 25 frames, 118 real trades in 20s
```

Bybit's socket works. Its `allLiquidation` topic ACKs `success:true` and is
simply quiet. Bybit REST is also blocked (403 CloudFront) but the recorder does
not use REST.

**Consequence:** liquidation coverage is Bybit-only, 3 symbols, which is a
minority of market-wide liquidation volume. `liq_cascade_chaser` and
`small_liq_continuation` will accumulate a real but thin tape. Binance.US has no
futures, so it is not a substitute. Hyperliquid has its own liquidation feed and
IS reachable from here - that is the obvious replacement and it needs a D-number.

**I did not edit the recorder.** It belongs to another live session and is
running. Raven's call.

### 2. The inverse fires, and on live data the parent and the inverse BOTH lost the same window.

From a 2-cycle probe against an isolated db (`/tmp/pmprobe`, live run untouched):

```
PM_fair_value_arb          ENTER sol Up   @ 0.1375  -> CLOSE @ 0.0825  pnl -1.10  price_stop
PM_fair_value_arb_inverse  ENTER sol Down @ 0.9200  -> CLOSE @ 0.8650  pnl -0.55  price_stop
```

Same window, opposite sides, both stopped out. That is the concern in the brief
made concrete: **the spread does not invert.** Measured overround on the three
inverse entries:

```
ask_sum 1.01 / 1.05 / 1.04   overround_cost_vs_naive  0.01 / 0.0575 / 0.0525
```

The strategy's entire profit target is `min_profit = 0.01`. So the overround
cost ran **1x to 5.75x the whole target**. Inverting a losing strategy does not
flip its sign when a large part of the loss is structural cost paid in both
directions.

This is a 2-cycle sample - a shrug, not a verdict (convention 7). What is
demonstrated is the MECHANISM, not the edge. The flip itself is verified:
`parent_intended_side=Up, inverse_side_taken=Down, flip_applied=True`.

**Aym's premise as stated ("79% loss becomes 79% win") does not hold**, for two
reasons now both measured or derived:
- the naive arithmetic is `0.79*0.01 - 0.21*0.03 = +0.0016/share` = 16 bps,
  already below convention 5's 30 bps DOA floor BEFORE costs;
- the parent's 21% wins were +1c moves, which flip to -1c - inside the 3c stop,
  so they do not stop out. They run to the time stop. The inverse's loss
  population is not the parent's win population reflected.

It is still worth shadowing. It is free, it is testable, and it now has a kill
condition. But it should not be expected to print money.

---

## What was built

| file | what |
|---|---|
| `strategies/polymarket/fair_value_arb_inverse.py` | `FairValueArbInverse`, `PM_fair_value_arb_inverse` |
| `strategies/polymarket/liquidation_feed.py` | shared read-only reader for the `liquidations` table |
| `strategies/polymarket/liq_cascade_chaser.py` | `LiqCascadeChaser`, `PM_liq_cascade_chaser` |
| `strategies/polymarket/small_liq_continuation.py` | `SmallLiqContinuation`, `PM_small_liq_continuation` |
| `strategies/polymarket/near_liq_trigger.py` | `NearLiqTrigger`, `PM_near_liq_trigger` |
| `tests/test_fair_value_arb_inverse.py` | 66 tests |
| `tests/test_liquidation_strategies.py` | 30 tests |
| `tests/test_near_liq_trigger.py` | 41 tests |

Modified: `strategies/polymarket/__init__.py` (imports, `build_strategies`,
`__all__`), and six registry tests in `test_fair_value_arb.py`,
`test_fair_value_arb_variants.py`, `test_polymarket_new_strategies.py`.

### The inverse: two decisions that needed judgement

**The parent's entry cap is unsatisfiable on the flipped side, so it was
replaced.** The parent picks the side with the largest `fair - ask`, so the side
it rejects is rich by that edge plus the overround. A model-derived cap on the
flipped side is therefore always below the flipped ask - on the standard fixture
fair(Down) 0.29, cap 0.25, actual ask 0.42, unreachable by 17c. Inheriting it
would produce a strategy that fires exactly never, which in a graveyard is
indistinguishable from one honestly measured and found dead. Replaced with a
book-derived cap. **Stated consequence: the inverse has no price-based entry
filter of its own.** The fair-value BAND `[0.10, 0.90]` IS inherited, and that
is provably correct - it is symmetric about 0.5, so `p in band` implies
`1-p in band`.

**`model_stop` had to be withheld from the exit chain.** It fires when
`fair_value <= entry + margin`. An inverse position is BY DEFINITION on the side
the model prices below our entry, so it would have fired on the first poll of
every position ever opened, closed at the spread, and the hypothesis would never
have been tested once. The observed value is still recorded as
`model_fair_value_observed_not_acted_on` so "seen and refused" stays countable.
Consequence: 4 live exit rules vs the parent's 6, so exit populations are not
comparable across the two.

A new gate `inverse_entry_above_profit_target_ceiling` refuses entries above
0.98, where a 1c target stops existing. It fired twice in 2 cycles.

### The liquidation strategies: honest no-data behaviour

They degrade through named reasons, never a crash and never a fake signal:
`liquidation_table_missing` -> `liquidation_feed_empty` ->
`liquidation_history_too_short` -> `liquidation_feed_stale` -> `no_cascade`.
Stale is checked BEFORE history-too-short: a dead recorder trips both and "it is
dead" is the actionable fact. Live right now they return
`liquidation_feed_empty` (12 evaluations in the probe) - correct, since the
table exists with zero rows.

`near_liq_trigger` is already PAST its feed gates and returning a real
evaluation (`no_liq_cluster_near_spot`, 6 in the probe), because the Hyperliquid
poller is live.

---

## Feeds: I started both

| job | pid file | status |
|---|---|---|
| liquidation recorder | `logs/liquidation_recorder.pid` | ALIVE, Binance dead / Bybit healthy-but-quiet, **0 rows** |
| hyperliquid poller | `logs/hyperliquid_feed.pid` | ALIVE, **299+ rows**, 30s cadence |

Confirm any PID with `ps -p` before trusting it (convention 25).

Hyperliquid reality check from the newest snapshot: 5 BTC positions, nothing
within 50 bps of liquidation, and 2 of 5 carry no `liq_price` at all. Requiring
2 near-liq positions out of ~25 watched wallets may be unreachable in practice.
That is a **coverage** problem (raise `--top-n`), NOT a reason to lower
`NEAR_BPS` - that would be the `COST_FLOOR = -0.30` shape again.

---

## Tests

Full suite before my changes: 7 failures. After: **1**, and it is not mine.

`test_the_shadow_loop_identity_is_computed_from_the_list_length` fails because
the **multi-asset session edited `shadow_loop.py` at 05:47** - `check_identity`
now reads `self.evaluations_per_cycle` instead of `len(self.strategies)`. That
is their refactor mid-flight. I left it alone (convention 21).

The six I fixed were all genuine consequences of 11 -> 15, updated preserving
intent, not weakened:
- counts 11 -> 15
- `names[8:] == VARIANT_NAMES` -> `names[8:11]`, because the three PARAMETER
  variants are no longer the tail and `[8:]` would silently pool the inverse and
  the liquidation strategies in with them
- fair-value family 4 -> 5 instances, tapes still independent
- `manages_exits` now legitimately includes the inverse; the three liquidation
  strategies hold to resolution and must NOT appear there

Live probe: 15 strategies x 3 assets = 45 evaluations/cycle, `identity_ok=True`.

---

## I did NOT restart the shadow loop

As instructed. PID 27030 is alive and still running the **11-strategy,
BTC-only** code - Python snapshots source at import (convention 13). It will not
see any of this until Aym restarts it.

Note that a restart now also picks up the other session's multi-asset change:
the loop goes to 15 strategies x 3 assets. Equity dropped $1000 -> $975 in 2
probe cycles, so the burn rate will be materially higher than the current run.
Paper money, but worth knowing before it is restarted.

---

## Needs a ruling

1. **Binance geoblock.** Drop Binance from the recorder, or add Hyperliquid's
   liquidation feed as the reachable substitute? Right now the recorder claims
   an exchange it cannot reach.
2. **D-number for the inverse**, and for the two deviations inside it (the
   replaced entry cap, the withheld `model_stop`).
3. **`liq_cascade_chaser`'s band is negative-EV at its own top under its own
   vendor number.** At 58.8% directional, break-even entry is 0.588; the vendor
   band runs to 0.85. Not moved - that would be picking a threshold off a number
   we do not believe. Rows stamp `vendor_breakeven_entry` so the population can
   be split without a re-run.
4. **`liq_cascade_chaser` drops the vendor's tick-rate confirmation** (we record
   no trade tape), so it fires on a strictly larger population than his did and
   is not comparable to his 95% figure. Stamped
   `tick_rate_confirmation_applied=False`.
5. **`near_liq_trigger` is missing the vendor's second lock** (arm on whale, then
   require a real liquidation print within 120s). Wiring `liquidation_feed.py`
   in as that second lock is the highest-value next change. Stamped
   `second_lock_wired=False`.
6. **`near_liq_trigger` MAX_ENTRY_PRICE tightened 0.95 -> 0.60.** At 0.95 the cap
   IS the break-even. Vendor value kept visible as `VENDOR_MAX_ENTRY_PRICE`.

## Still open from before

`PM_corridor_pair` vs `PM_corridor_pair_live` naming (D-284 is still an empty
slot). Untouched.
