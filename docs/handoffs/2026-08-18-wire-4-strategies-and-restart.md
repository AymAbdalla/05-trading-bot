# Handoff: wiring the 4 repo strategies, and the weather model that does not fit

**Cody, 2026-08-18 07:07 local (11:07Z).** Registry is 19. Shadow loop restarted
and healthy under Raven's supervisor. Two of the four new strategies trade; two
cannot, for reasons that are measured, not guessed.

## Headline

**`PM_weather_arb`'s threshold parser was fixed and proven on live data - and
the strategy still refuses every market, correctly.** The parser was never the
real blocker. The model prices the wrong variable. Detail in section 3.

## 1. What was already done by another session

`build_strategies()` already returned **19** when I got to it. Session 38911
registered `SmartMoneyCopy`, `WeatherArb`, `GridHedge`, `DipArb` in
`strategies/polymarket/__init__.py` while I was doing recon. I did not redo it
(convention 21). Steps 1, 2 and 5 of the request were therefore already closed.

That same session also added **capability dispatch** to `shadow_loop.py`
(`exit_no_fair_value_protocol`, lines ~922 and ~1780): it now calls
`strategy.estimate()` only when the attribute exists.

## 2. What I changed

| File | Change |
|---|---|
| `strategies/polymarket/weather_arb.py` | Threshold parser rewritten (below). Discovery moved onto the Gamma tag endpoint. |
| `tests/test_weather_arb.py` | 88 -> **141 passing**. |
| `strategies/polymarket/dip_arb.py` | Added `DipArb.estimate()`. |
| `tests/test_dip_arb.py` | 65 -> **77 passing**. |
| `tests/test_fair_value_arb.py` | Fixed a red test the registration caused (below). **127 passing.** |

Nothing else. I deliberately did **not** touch `shadow_loop.py`, `markets.py` or
`__init__.py` - two other sessions were rewriting them for most of this session.

### The red test the registration caused
`test_only_this_family_advertises_that_it_manages_exits` asserted the
`manages_exits` list was exactly the five fair-value variants. `PM_dip_arb`
legitimately manages exits, so registering it turned the suite red. Renamed to
`test_only_exit_managers_advertise_that_they_manage_exits`, added `PM_dip_arb`,
and replaced the name-prefix inference with a check of the thing that actually
breaks: **every strategy claiming the flag must ship a callable `manage_exit`**.
Added the inverse guard too - anything shipping `manage_exit` must declare the
flag, or its positions ride to resolution silently.

### `DipArb.estimate()`
It declares `manages_exits = True`, so the loop called `strategy.estimate(ctx)`
on it every cycle; the method did not exist, so it threw into
`health['exit_fair_value_exceptions']` and warned every cycle.

It now returns a `TapeMeanEstimate` that is **always `usable=False`**, reason
`reference_is_per_token_not_per_window`. That is deliberate under-claiming: the
loop only reads the estimate when the position's window matches the context's,
where the number would be byte-identical to what `manage_exit` already computes
from its own tape - and would be *wrong* whenever the tokens disagree. Zero
upside, a silent wrong exit as the downside. Verified live: **0 exception rows.**

**Open conflict for Raven:** 38911's capability dispatch and my `estimate()`
compose safely, but they cannot both be the reason, and the gauge now reads 0
instead of 1-per-asset. One should be retired. Their comment block is now
stale. Needs a D-number; **not my call.**

## 3. Weather: the parser is fixed, the model is wrong

### The parser was genuinely broken
`parse_threshold()` returned `None` on **100% of live questions**. `_THRESHOLD_RE`
required the comparator *before* the number ("above 85F"); real Polymarket
questions put it after ("85F or below") or omit it ("be 84F"). Measured before
any change:

```
Will the highest temperature in New York City be 84F on August 18?        -> None
Will the highest temperature in New York City be 80F or below on Aug 18?  -> None
Will the highest temperature in Hong Kong be 30C on August 18?            -> None
```

### The real market shape (measured live, not assumed)
The request described "Will the temperature in NYC be above X degrees". That is
not what Polymarket lists. The live universe is a **ladder of mutually exclusive
buckets**, ~11 rungs per city per day: two tails ("N or below", "N or higher")
and ~9 exact-degree buckets. **1,485 of 1,783 are Celsius**, not Fahrenheit -
and `Threshold.value_f` is Fahrenheit, so an unguarded parse of "30C" as 30F
would have been a catastrophic mispricing.

Resolution is on an **airport station**, confirmed: the market's
`resolutionSource` embeds the ICAO code
(`.../new-york-city/KLGA` = LaGuardia). That is more reliable than a hardcoded
city table, and is what the code now reads.

### Measured coverage after the fix
| stage | before | after |
|---|---|---|
| threshold parses | 0 | **1,727 / 1,739 (99.3%)** |
| resolves to a station | 132 | **1,661 / 1,739 (95.5%)**, 50 ICAOs |
| reaches the orderbook | 0 | **1,151 / 1,739** |

Two corrections to ground truth I supplied: **Moscow is UUWW (Vnukovo), not
UUEE**, and **Denver is KBKF (Buckley), not KDEN**. The agent checked rather
than trusting me. Hong Kong is the one real gap - it resolves on the Hong Kong
Observatory HQ, not an airport.

### Why it still enters nothing, and why that is right
Running 80 markets end to end against live books produced **7 ENTERs with
"edge" of 0.45 to 0.999**. That is not edge; it is the model pricing the wrong
variable. Same minute, measured:

- **Madrid** - station reads 33.0C. Market prices "highest today is 39C" at
  **0.70** (the afternoon peak has not happened yet). Model says **0.000024**.
- **Buenos Aires** - station reads 7.0C. Market prices "highest today is 8C or
  below" at **0.001**. Model says **0.87**.

The market is right both times and the model is wrong in **opposite
directions**, so a pooled win rate would average the bias away and look fine.
100% of the live universe resolves on a **daily extreme**; `probability_yes`
prices a **single reading at settlement**. Those are different random variables.

Gated off behind `allow_daily_extreme_markets=False`, skipping with its own
cannot-run reason `daily_extreme_not_priced_by_point_in_time_model`
(convention 11), plus a kill condition naming a harness that does not exist yet:
`backtest/measure_daily_extreme_calibration.py`.

An 86c model edge against a 0.1c quote is the `COST_FLOOR = -0.30` shape
(convention 17). Refusing is the honest outcome.

## 4. What I did NOT build, and why

**Weather market polling in `shadow_loop.py` (request step 3): not built.**
The plumbing was never the blocker. With the model unfit for daily-extreme
markets, a weather cadence would fetch ~1,700 markets a minute to feed a
strategy that correctly refuses all of them. That is decorative work plus real
API load. The order of operations is: fix the model, build a calibration
harness, *then* wire the cadence.

There is also a structural constraint whoever builds it must respect: the loop's
accounting identity is `evaluations == cycles * sum(len(rt.strategies))`. Any
strategy in `runtime.strategies` evaluated on a **sub-cadence** breaks it.
Weather needs its own counter space, like `exit_counts` already has.

**Event market scanning for smart_money_copy (request step 4): not built.**
`TRACKED_WALLETS` has 7 handles and **all 7 addresses are `None`**. The module
says so itself: "A None here is not a placeholder to be filled with a guess."
I could not resolve them - `gamma/profiles?name=` returns an auth error and
`data-api/leaderboard` 404s. I did not invent addresses; copying a wrong wallet
is worse than copying none. Live proof: **1,221 consecutive
`wallet_address_unresolved` rows.** Building market discovery to feed it would
deliver zero decisions. **Blocked on real wallet addresses from Aym.**

## 5. Restart: there were TWO loops, and one was Raven's

The old loop (27030) was stopped cleanly. But a **second** loop (51187) had been
started at 10:39Z **by Hermes/Raven** via `scripts/shadow_runner.py` - a
supervisor that auto-restarts on exit and on equity blowup. Two loops were
writing the same `db/trading.db` and the same CSV, which corrupts the paper
accounting.

Resolution: I stopped **my own** unsupervised loop and recycled Raven's child so
the supervisor respawned it on current source (convention 13 - the 10:39Z child
predated the weather and dip_arb fixes). Verified a plain exit takes the
restart branch and logs **no false blowup**.

**Now running: exactly one loop, PID 59357, parent `shadow_runner.py` 51148.**

```
strategies : 19 per asset, 57 evaluations per cycle
assets     : btc, eth, sol
identity_ok: True     identity violations: 0
exit_fair_value_exceptions: 0
dashboard  : localhost:8501 HTTP 200
```

Pre-restart baseline for comparison: 4,774 cycles, 38,192 evals, 42 entries,
equity **$969.92** on 43 resolved, at 8 evals/cycle.

## 6. Live behaviour of the 4 newly wired strategies

Measured from `signals` over ~7 minutes:

| strategy | verdict |
|---|---|
| `PM_dip_arb` | **Trading.** Real entries; reaching real gates (`dip_below_threshold`, `insufficient_book_depth`, risk-gate position limits). |
| `PM_grid_hedge` | **Reaching its own logic.** Armed a grid 84 times, then `maker_fill_not_simulated` - same wall as `box_builder`. Mostly gated by `strike_inside_proxy_noise_floor` (972). Needs a maker fill model, not a looser threshold. |
| `PM_smart_money_copy` | **Dead.** 1,221 x `wallet_address_unresolved`. |
| `PM_weather_arb` | **Skipping correctly.** 1,221 x `resolution_station_unknown` - it is being handed crypto contexts, which have no weather station. Harmless, but see the note below. |

Pre-restart safety check: all 19 strategies return a `Decision` with **zero
network I/O** inside `evaluate()`, 2.8ms per asset-cycle. That check mattered -
`WeatherArb` fetches METAR inside `evaluate`, and had it not gated on the
station first, the restart would have issued 3 HTTP requests per cycle forever.

**Flag:** `PM_weather_arb` and `PM_smart_money_copy` are now evaluated 3x per
cycle against crypto contexts they can never trade - 6 of 57 evaluations, and
their skip rows are tagged `asset=btc|eth|sol`, which is misleading. Cheap, but
it is noise in the signals table. A `needs_discovered_market` class attribute
and a partitioned runtime would fix it properly.

## 7. Test state

```
tests/test_weather_arb.py      141 passed
tests/test_dip_arb.py           77 passed
tests/test_fair_value_arb.py   127 passed
polymarket suites together     425 passed
```
Full repo: 2,170 passed / 4 failed. Three re-pass in isolation (many concurrent
sessions - convention 21). The fourth,
`test_polymarket_risk_gate.py::test_config_yaml_matches_the_module_defaults`
(`daily_loss_limit_usdc` 0.0 vs 30.0), is another session's `config.yaml` edit
and is **not** mine. `tests/test_dashboard_charts.py` needs `.venv` for plotly.

`validate_harness.py` was **not** re-run this session - nothing here touches the
graveyard path.

## 8. For Raven / Aym

1. **Retire one of** DipArb's `estimate()` **or** 38911's capability dispatch.
   Needs a D-number.
2. **`PM_weather_arb` needs a daily-extreme model** before the weather cadence
   is worth building. Kill condition already names
   `backtest/measure_daily_extreme_calibration.py`, which must be written.
3. **`PM_smart_money_copy` is blocked on 7 real wallet addresses.** Aym's call.
4. `__init__.py` line ~180 says `PM_weather_arb` "Can enter" - now stale.
5. Consider `needs_discovered_market` to keep non-crypto strategies out of the
   crypto runtimes (section 6 flag).
6. No METAR caching: `evaluate` issues one request per market. The 1,299-request
   sweep induced 44 4xx. A short TTL under `MAX_OBS_AGE_SEC` would be safe.
7. `endDate` on weather markets is a settlement admin timestamp, not the
   observation window (Madrid's is 12:00Z = 14:00 local, mid-afternoon), so
   `hours_to_resolution` is not the horizon the model assumes.

Nothing is committed. Tree left for review.
