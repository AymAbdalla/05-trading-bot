# Handoff: shadow debug, the measured strike proxy, and the Forge loop

**From:** Cody
**Date:** 2026-08-17 (session ended 2026-08-18 ~02:40Z)
**Directive:** `docs/handoffs/from-raven/2026-08-17-debug-and-autonomous-loop.md`
**Status:** Parts 1, 2, 3, 4, 5 done. Part 6 (cron install) deliberately NOT done, see below.

---

## The headline

Raven's directive assumed four broken strategies and "almost certainly a data
wiring bug". That is not what the data says. It is **one missing input, one
by-design refusal, and one strategy that was never broken at all.**

| Strategy | Raven's read | What the data says |
|---|---|---|
| `mid_price_continuation` | data wiring bug | Real. Missing strike. **Fixed.** |
| `corridor_collector` | separate wiring bug | **Same** missing strike, downstream. **Fixed.** |
| `streak_snapper` | legit, only ~3 windows seen | Wrong reason. It has 16 windows of history. Threshold is fine. **No change.** |
| `box_builder` | maybe legit | Reaches its own logic. Blocked by a deliberate maker-fill refusal. **No change.** |

Nothing here was fixed by loosening a threshold. Convention 17 was the live risk
all session and I want that on the record.

---

## Part 1 and 2: the diagnosis, with numbers

Mined from 1,212 logged decision rows in
`research/polymarket_paper/polymarket_paper_log.csv`. Note the `features`
column is `k=v;k=v`, **not** JSON - my first pass parsed it as JSON, got empty
dicts, and briefly concluded the diagnostics were missing. They were not.

### One root cause, two symptom names

Gamma publishes **no strike**. Probed the live market object directly: 47 keys,
none of them a strike. `resolutionSource` is
`https://data.chain.link/streams/btc-usd-twap-60s-streams`, and
`cryptoMarketConfig` is `{'id': 'btc-5m-twap-60', 'twapEnabled': True,
'twapLookbackSeconds': 60}`. The strike is a Chainlink 60-second TWAP at window
open and it is simply not in the API.

`corridor_collector`'s `no_lead_or_atr` looked like a second, independent
problem. It was not. Its `atr14` was always supplied fine; the missing half was
`lead_bps`, and `lead_bps` needs a strike. **Two symptom names for one cause is
how a single fix looks like two.**

### streak_snapper was never broken

Raven said "in 17 minutes there are only ~3 windows total, so this skip is
legitimate". The conclusion is right, the reason is wrong: the loop pulls 16
windows of history from 5m candles, so it is not window-starved.

Measured the real run-length distribution over 1,000 recent 5m bars:

```
runs >= 4 : 74 of 446 runs (16.7%)
          -> 213 of 1000 bars satisfy streak_len >= 4  (21.3%)
```

The threshold is reachable on about one bar in five. Live it observed
`streak_len` min 1, median 2, max 3 over 309 evaluations, i.e. a genuinely quiet
stretch. **I did not touch the threshold.** Lowering a filter that is already
satisfied 21% of the time, to make a strategy fire during a quiet half hour, is
exactly the `COST_FLOOR = -0.30` false positive shape.

### box_builder is blocked by a refusal, not a bug

It reaches the end of its own logic. Live features from a real armed window:
`ask_sum=1.03  pair_cost=0.94  gross_edge_per_pair=0.06` and then
`maker_fill_not_simulated`. It is a **maker** strategy and the paper adapter
refuses to simulate a resting bid as a taker lift, because doing so would
manufacture precisely the fills its claimed edge depends on. That refusal is
correct and I left it alone.

Its `book_too_tight_to_arm` threshold (asks sum >= 1.03) against the measured
distribution (min 0.99, p50 1.01, max 1.05) is tight but **satisfiable** - it
armed 4 times in 311 evaluations. Unlike `rsi_extreme` (R-007), this threshold
is inside the support of the distribution. No change.

**To make box_builder tradeable in paper you need a maker fill model, not a
looser threshold.** That is real work and it is not done.

---

## The fix: a MEASURED proxy strike

The previous session refused to substitute spot for the strike and **was right
to**. I did not overturn that. I replaced "substitute a number and hope" with
"substitute a number whose error has been measured, and refuse to trade inside
that error".

`engine/polymarket/strike.py` rebuilds the same 60-second average Chainlink uses,
from Binance.US 1m klines. The TWAP at `t` covers `[t-60, t)`, which is exactly
the bar whose open timestamp is `t-60`; reading the bar opening at `t` instead
would be lookahead, and there is a test pinning that alignment.

### The measurement is the whole justification

`backtest/measure_strike_proxy.py` replays completed windows, predicts each
outcome from the proxy **alone**, and scores it against the Gamma oracle.
199 windows scored of 200 requested (1 unresolved, dropped not guessed):

```
 |move| bucket    n   disagree    rate
     0 -  1 bps  45      19      42.2%   <- coin flip. unusable.
     1 -  2 bps  17       4      23.5%
     2 -  5 bps  59       4       6.8%
     5 - 10 bps  46       3       6.5%
    10 +   bps   32       0       0.0%

 cumulative:  >= 3 bps -> 4.2% (n=119)
              >= 5 bps -> 3.8% (n=78)
             >= 10 bps -> 0.0% (n=32)
```

The pooled headline is 15.1% and **that number is useless** - it averages a coin
flip and a 96% into a figure describing neither. The bucketing is the result.

So the proxy is supplied, and `STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0` is enforced.
Below it the strategy is refused with its own distinct reason,
`strike_inside_proxy_noise_floor`, which must never pool with a real market
condition: "too small for our instrument to see" and "too small to trade" are
different facts and only the second is a result (conventions 11 and 20).

**Standing caveat, convention 7:** the >= 5 bps cell is 3 disagreements out of
78. Enough to act on, nowhere near enough to settle. Re-measure as windows
accumulate: `env -u PYTHONPATH python3 backtest/measure_strike_proxy.py --windows 500`

### Proof it actually works

`backtest/verify_strike_unblocks.py` runs the real strategy objects against a
real live context, with and without the proxy. Caught the case live at
lead **-6.02 bps**:

```
PM_mid_price_continuation  before=no_spot_or_strike  after=insufficient_ask_depth  <-- now runs
PM_mid_price_continuation  before=no_spot_or_strike  after=too_close_to_resolution <-- now runs
```

That is the bar being cleared. **Not "a strategy entered"** - entering is a
market condition and cannot be summoned on demand. The bar is that the strategy
stopped dying at the data gate and started reporting its own decisions.
NOT_TESTED became a result (convention 11).

---

## Files

**Mine, new:**
- `engine/polymarket/strike.py` - the proxy, the measured floor, categorised failures
- `backtest/measure_strike_proxy.py` - the named harness. Cite this in kill conditions.
- `backtest/verify_strike_unblocks.py` - the before/after wiring proof (convention 22)
- `tests/test_strike_proxy.py` - 19 tests
- `research/polymarket_paper/strike_proxy_measurement.json` - the 199-window run

**Mine, modified:**
- `engine/polymarket/shadow_loop.py` - supplies strike + `lead_bps`; enforces the
  floor **before** a strike-dependent strategy evaluates; `strike_proxy` is now
  injectable; docstring corrected (it claimed the strike was absent "permanently")
- `strategies/polymarket/{mid_price_continuation,corridor_collector}.py` - one
  `needs_strike = True` marker each, no logic touched
- `tests/test_polymarket_shadow_loop.py` - stubbed the proxy in the autouse
  `no_network` fixture, updated the old strike contract, added 3 tests

**Subagent, Forge (Parts 3 and 5):** `agents/forge_shadow_eval.py`,
`scripts/forge_eval_loop.sh`, `tests/test_forge_shadow_eval.py` (25 tests),
rewritten `agents/forge.py` and `agents/forge/forge.agent.md`.

**Subagent, summaries (Part 4):** `scripts/shadow_summary_lib.py`,
`scripts/daily_shadow_summary.py`, `scripts/weekly_shadow_summary.py`,
`tests/test_shadow_summaries.py` (48 tests).

---

## Part 5: Forge constraints relaxed

Per Aym: "don't be so controlling on what he is allowed to make."

Four refusals **downgraded to non-blocking warnings** (kept in a
`RETIRED_REFUSAL_CATEGORIES` map so nothing vanishes from the counter schema):
graveyard duplication, multi-class edge, unknown asset class, missing graveyard
link. Asset classes opened up to EVENT/SPORTS/FX/COMMODITY/RATES; kinds now
include `combination` and `experiment`.

The one hard constraint held: every proposal needs a kill condition with a
number **and a named harness**. The subagent found the named-harness half was
documented but never actually enforced - only the digit check existed - and
enforced it. It also had to amend proposal 004's kill condition, which named a
number but no harness and would otherwise have been refused.

The 30bps floor was made **instrument-aware** rather than deleted: on a binary
the denominator is the premium in cents and the tick is 1c, so 30bps is 0.15c,
a sixth of a tick, a quantity the venue cannot represent. Prediction markets get
a 200bps floor (one tick).

Forge wrote proposals 006 and 007, both targeting the strike blocker - arrived
at independently of my work, same conclusion.

---

## Part 6: what I did NOT do, and why

**I did not install the cron jobs.** The scripts are ready and tested
(`scripts/forge_eval_loop.sh`, `scripts/daily_shadow_summary.py`,
`scripts/weekly_shadow_summary.py`).

The Forge cron prompt in the directive says: *"If any proposals are
implementable, spawn Cody to implement them and add to the shadow loop."* That
is unattended sessions writing strategy code on a 4-hour timer with nobody
reading the diff. That is Aym's switch to throw, not mine to throw quietly at
the end of a session. Everything is staged so it is a one-line decision.

Also note `--send` on the summaries **does not send**. A subprocess has no
channel to the Hermes MCP tools, so it renders to `logs/summaries/` and prints
the path for cron or Raven to pick up. The scripts say so rather than pretending.

---

## Verification

- `backtest/validate_harness.py` -> **21/21, exit 0** (convention 1)
- `tests/test_strike_proxy.py` + `tests/test_polymarket_shadow_loop.py` -> **56 passed**
- Full suite -> **1215 passed, 1 skipped, 9 failed**

**The 9 failures are not mine.** All sit in two sibling workstreams that were
writing to the tree throughout this session: the R-005/R-006/R-007/R-008 resweep
(`test_harness_warmup_cohort.py`, `test_pattern_regressions.py`,
`test_graveyard_summary.py`, `test_r007_r008_fixes.py`) and the new-strategies
session (`test_polymarket_new_strategies.py`, which asserts 7 strategies while
its own session has added an 8th). None reference the strike work.

One honest note: my first `validate_harness.py` run **failed** with
`AttributeError: _volume_filter_applies`. That was a sibling writing
`vectorized_harness.py` at 22:30:20 while the check ran at 22:30:13. Re-ran
clean. Convention 21 is not theoretical in this tree.

---

## The shadow loop is still running, untouched

PID 17603, started 21:50, never signalled. **The running process cannot see any
of this** - Python snapshotted its source at import (convention 13). It is still
executing the 4-strategy, no-strike code and will keep logging
`no_spot_or_strike` until someone restarts it.

**Restarting it is the last step and I did not take it**, because the directive
said not to and because a sibling session was mid-edit on the strategy list.
When the tree is quiet, a restart picks up the strike proxy and the 8 strategies
at once.

---

## What's next

1. **Restart the shadow loop** when the tree is quiet. Nothing above reaches
   production until you do. Then re-run the daily summary and watch whether
   `strike_inside_proxy_noise_floor` dominates - if it does, the floor is too
   wide for 5m windows and that is a real finding, not a bug.
2. **Re-measure the proxy at n=500.** The 3.8% figure rests on n=78.
3. D-numbers for: the proxy strike + enforced noise floor, and the finding that
   `no_lead_or_atr` was never an independent failure.
4. Decide on a maker fill model, or accept that `box_builder` is permanently
   NOT_TESTED in paper.
5. Aym decides on the autonomous cron loop (Part 6).

## Flag for Raven

The directive's framing - four bugs, "almost certainly a data wiring bug" - would
have produced four fixes, three of them unnecessary and at least one
(`streak_snapper`'s threshold) actively harmful. The skip reasons were
describing one root cause under two names, one deliberate refusal, and one quiet
market. Worth calibrating on before the next diagnosis directive.
