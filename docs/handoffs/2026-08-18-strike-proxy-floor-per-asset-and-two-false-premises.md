# Strike proxy floor goes per-asset, and two "unblock" premises were wrong

**Session:** Cody, 2026-08-18 06:57 - 07:30
**Trigger:** Aym directive - "the #1 blocker is the strike proxy noise floor,
11 of 19 strategies are blocked, fix it first, then fix the other blockers."
**Decision recorded:** D-299.

## Headline

The directive's GOAL was implemented. Its NUMBERS were not, because they did
the opposite of the goal. Three of its premises were measurably wrong, and the
measurement is in this note so nobody has to re-derive it.

| Premise as given | Measured |
|---|---|
| "the gate rejects windows where proxy disagreement is ABOVE 5bps" | Backwards. Gate is `abs(lead_bps) < floor -> skip` |
| "lower the floor to 15bps (was 5bps)" to fire MORE | 15bps admits **0.0%** more. It is a TIGHTENING |
| "11 of 19 strategies are blocked" | **3**: corridor_collector, grid_hedge, mid_price_continuation |
| "SOL is noisier, give it 25bps" | 25bps takes SOL from 3.5% admitted to **0.0%** |

## What was built

**Per-asset noise floor, 5.0 -> 1.0 bps.** `NOISE_FLOOR_BPS_BY_ASSET` in
`engine/polymarket/strike.py`, overridable from `config.yaml` at
`polymarket.strike_proxy.noise_floor_bps_by_asset`. Unregistered assets fall
back to the strict 5.0 - an unmeasured asset gets the TIGHT floor, because a
strategy firing on a strike whose error is unknown is unreadable, not just noisy.

1.0 bps is the lowest floor still OUTSIDE the measured coin-flip band. Below
1 bp the proxy disagrees with the oracle 42.2% of the time; firing there samples
an RNG rather than testing a strategy (convention 11: NOT_TESTED, not a result).

**Honesty guard.** `NOISE_FLOOR_ERROR_BY_ASSET` was measured AT 5.0 bps and no
longer describes the active floor. `error_at_floor_pct_for` still returns the
5.0 figure; new `active_floor_error_pct_for` returns it ONLY when the active
floor equals `NOISE_FLOOR_ERROR_MEASURED_AT_BPS`, else None. Without this the
loop would keep publishing a 5.0-bps error rate under a field that reads as
"the error at the floor" - the exact stale-number shape this repo keeps hunting.

**Per-evaluation disagreement stamp.** Every row (not just rejected ones) now
carries `strike_proxy_disagreement_pct`. A strategy that FIRES at 1.2 bps is now
readable afterwards as having fired inside a 23.5%-error band. Without it, a
loss there is indistinguishable from a loss caused by a bad strike.

## Measured effect

Projected on the 10,276 historically gated rows: btc 46.6% now admitted, eth
46.8%, sol 3.5%.

Observed live, before vs after (same running loop, floor 5.0 -> 1.0):

```
                             floor 5.0      floor 1.0
3 gated strategies' rows     83.0% gated    26.2% gated
new reasons appearing        -              not_through_strike (216)
```

`not_through_strike` only exists PAST the floor gate. Those strategies are
evaluating their own condition for the first time. That is the whole point.

## SOL: not a noise problem, and a wider gate would have killed it

95.3% of SOL's blocked windows carry `lead_bps` EXACTLY 0.0. Across the whole
log SOL has only **two** distinct nonzero leads: 3.953 and 3.955.

That is tick quantization, not measurement noise. SOL trades near $75.89 against
a $0.01 Binance.US tick, so **one tick IS 1.318 bps**, and a quiet 1m bar is
perfectly flat (O==H==L==C), making spot and the TWAP proxy bit-identical. BTC
near $64,210 has a 0.002 bps tick and is effectively continuous.

**This puts D-285 in a different light.** SOL's 14.3% disagreement at 5 bps is
plausibly a DISCRETIZATION artifact rather than a worse proxy. Not resolved
here. Open work, and it bears on item 5 of the old "what's next" list (whether
a per-asset floor should replace the single constant - the answer is that for
SOL the floor is the wrong knob entirely).

## Tasks 2 and 3: both "unblock" premises were also wrong

Two subagents measured before building. Neither strategy is unblocked by the
work that was asked for.

**weather_arb is not blocked by missing weather feeds.** It skips
`resolution_station_unknown` 579/579 because the shadow loop hands it a
**BTC 5-minute up/down market**. Log row: `btc-updown-5m-...,
city_key=None; rules_text_present=True; rules_station=None`. `rules_text_present=True`
is the tell - the market has rules, they just name no weather station.
`weather_arb.py` ALREADY CONTAINS working `AirportWeatherFeed` and
`DowntownWeatherFeed` classes hitting the same two endpoints. Two more gates sit
behind it: `allow_daily_extreme_markets=False` at registration, and the
strategy's own docstring says 100% of the live universe is daily-extreme, so it
would then skip `daily_extreme_not_priced_by_point_in_time_model`.

**smart_money_copy is not blocked by missing market scanning.** It skips
`wallet_address_unresolved` at `smart_money_copy.py:839` - gate 2 of ~15,
BEFORE any network call. `TRACKED_WALLETS` has all seven values `None`, and
`build_strategies()` constructs it with no arguments. No config key, no env var,
nothing in the repo. The source article gave display handles and two 4-hex
prefixes, never proxy addresses, which is what the Data API requires. Three
blockers stacked; market scanning is the THIRD.

Both were built anyway, tested, and left unwired:

- `engine/feeds/noaa_weather.py`, `engine/feeds/open_meteo.py` - 132 tests,
  both endpoints verified live from this machine (KLGA 22.8C, NYC 71.3F).
- `engine/polymarket/markets.py::search_event_markets[_checked]` - 38 tests,
  verified live against Gamma.

**Not wired into `shadow_loop.py`, deliberately.** Wiring either today changes
no strategy's behaviour and adds load: weather polling cannot pass a gate that
fires on market identity, and event polling would produce 20x more identical
`wallet_address_unresolved` rows per minute before any fetch. Both need an Aym
ruling first (below). One flag away either way.

## Two bugs found in passing, NOT fixed

**Gamma's `order=volume` returns the LOWEST-volume markets.** It sorts that
column as text: the page came back `99.99, 999.88, 9997.5, 9.99, 999.35...`,
every value starting with digit 9. Correct field is `order=volumeNum`
($83.4M -> $42.2M, strictly monotonic). Gamma returns **HTTP 422 on an unknown
order field**, so `volume` is RECOGNISED and sorts backwards - worse than being
ignored, because it returns 200 and a page that looks like an answer.

Consequence: `list_markets_checked` defaults to `order='volume', ascending=False`
and its docstring claims "highest volume first by default." It returns the
lowest. **Zero production callers**, so nothing is corrupted today. Left for a
D-number rather than silently edited.

**A `null` JSON body decoded to `None`, the same value the feed helpers used as
their failure sentinel** - so a refusal was returned labelled `'ok'`. Caught by
the new tests, fixed in both feed modules.

## Test state

`env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q --ignore=tests/test_dashboard_charts.py`

**2,421 passed, 3 failed, 1 skipped** (9m06s). None of the three are mine:

- `test_polymarket_risk_gate.py::TestConfigWiring::test_config_yaml_matches_the_module_defaults`
  - the permanently-red one CLAUDE.md documents. Verified it is still red for
    the OLD reason (`daily_loss_limit_usdc` 0.0 vs 30.0), not for anything added
    here. Still needs Aym's ruling.
- `test_dip_arb.py::TestEstimate::...` and
  `test_fair_value_arb_variants.py::TestRegistry::...` - **both pass in
    isolation.** Concurrent-session mid-edit collisions during a 9-minute run.
    Convention 21, exactly as CLAUDE.md predicts.

New: `tests/test_strike_proxy_per_asset.py`, 33 tests. Includes
`test_a_bigger_floor_never_admits_more`, which goes red if anyone ever
"loosens" this gate by raising the number again.

## Running jobs (convention 25 - confirm with `ps -p`, do not trust this table)

| what | PID | notes |
|---|---|---|
| Polymarket shadow loop | **64196** | restarted 07:26 onto current source |
| shadow_runner wrapper | 51148 | alive, **0 blowups**, auto-restarts the loop |
| graveyard re-sweep | 18543 | ~9h30m elapsed |
| hyperliquid poller | 37578 | alive |
| liquidation recorder | 48637 | alive, still 0 rows (Bybit-only, quiet) |

**The wrapper respawns the loop when its child dies.** To restart, `kill` the
loop PID and wait. Running `./run_polymarket_shadow.sh` as well starts a SECOND
loop. PID 51187 and 59357 both died during this session without my killing them.

Post-restart health: 987 rows in ~75s, 19 ENTER, 18 CLOSE, 57 evaluations/cycle.

## Needs a ruling from Aym

1. **The 1.0 bps value.** The mechanism is per-asset and config-driven; the
   number is a one-line edit. Convention 17 warning stated in advance: this
   LOOSENS a gate that was DERIVED from a measurement. If a win rate improves
   after this, that is the exact shape of a false positive. Compare against the
   pre-change baseline deliberately.
2. **SOL's tick quantization.** Does D-285's "SOL is 5x worse" survive? The
   floor is the wrong knob for SOL either way.
3. **Wallet addresses for smart_money_copy.** A human data task, not code.
   Without them the strategy cannot run at all.
4. **`allow_daily_extreme_markets` for weather_arb.** A modelling call: the
   strategy's own docstring says its point-in-time model does not price daily
   extremes, and 100% of the live universe is daily-extreme.
5. **`no_trade_in_this_market` is classified GENUINE** (`forge_shadow_eval.py:227`).
   Since the loop structurally only shows smart_money_copy 5m crypto binaries, a
   whale never appearing there is a SCOPE blocker, not a genuine no-signal.
   Masked today because gate 1 fires first.
6. **`list_markets` volume ordering** - needs a D-number before the default flips.
7. Still open from before: the daily-loss-breaker posture and the permanently-red
   config-wiring test.

## Git

Commit `031bbd6` ("nine concurrent Cody sessions") swept this session's
`strike.py`, `shadow_loop.py`, `config.yaml` and the subagents' new files into
history. **Neither I nor the subagents staged or committed anything.** Verified
the committed content matches the working tree. Only `docs/DECISIONS.md`
(D-299) is unstaged. Flagging because the diff landed without review.
