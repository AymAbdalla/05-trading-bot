# Handoff: Raven rulings D-289 through D-297 implemented

**From:** Cody, 2026-08-18 ~07:20
**Acting on:** `docs/handoffs/from-raven/2026-08-18-implement-raven-rulings-d289-d297.md`
**Tree:** uncommitted, as instructed. Nothing staged, nothing committed.

## Summary

Five tasks. Two were already done by concurrent sessions before I got to them
(convention 21 again). Three I built. One task's premise changed under me
mid-session and that is the most important thing in this document.

| Task | Ruling | State |
|---|---|---|
| 1. AST indirection resolver | D-290 | **BUILT.** New module + 16 tests |
| 2. `global_temperature_market_excluded` -> DATA_BLOCKER | D-291 | **DONE** |
| 3. Kill-clock guard on empty liquidation tape | D-296 | **BUILT.** 10 tests |
| 4. Per-asset measured error on strike rows | D-297 | **DONE**, partly pre-done |
| 5. Split `no_underdog` | - | **Already done** by another session |

## Task 1: D-290, the AST resolver (the big one)

New file: **`tests/skip_reason_ast.py`** (~500 lines, read-only static
analysis, imports nothing from the strategies and runs none of their code).

The old test only saw `decide('SKIP', <string literal>)`. Seven call sites pass
a variable, so their reasons were invisible and the suite was green over them
by accident. All seven now resolve. **Unresolved sites: 0. Reasons visible to
the guard: 150, up from ~118 literals.**

Resolution rules, in order: literal; `IfExp`; `BoolOp`; string concat with a
constant part (-> a PREFIX family, claimed as a family, never expanded into
members it cannot know); module-level constant including tuples built by `+`
and constants imported from a sibling module; a local bound by tuple-unpacking
a call (resolved through the producing function's returns); an attribute of a
returned dataclass (resolved through every value that flows into that field
name, following one level of nested-helper parameter substitution).

That last rule is what reads `decide('SKIP', liq.reason)`: the strings go into
a nested `fail(reason)` helper, out through a `LiquidationWindow(reason=...)`
keyword, and only then to `decide`.

**The loud-failure clause is implemented and tested.** D-290 called it
load-bearing and it is: a resolver that quietly returned nothing on an
unfollowable expression would be the same blind spot, bigger.
`test_an_unfollowable_expression_is_reported_loudly_with_its_site` builds a
synthetic module with a subscript reason and asserts the report carries file,
line and expression.

Three deliberate design calls Raven should look at:

1. **Partial resolution is refused.** `REASONS[0] if ctx else 'two'` resolves
   to nothing, not to `{'two'}`. Half a resolution would make the site look
   COVERED while one of its two outcomes went unchecked. Pinned by its own
   test.
2. **One narrow filter: `'ok'` and `'ok_*'` are dropped.** The resolver
   over-approximates (for `_, status = producer()` it takes every return), so
   producer SUCCESS values come back too. The call sites are guarded, but the
   guards take four different shapes across the seven sites and reading them
   is a worse dependency than the package's existing naming convention
   (`FeedRead.status` is documented as "'ok' or a FEED_SKIP_REASONS member").
   The filter is pinned by
   `test_no_classified_reason_looks_like_a_success_sentinel`, which goes red
   if a real skip reason is ever named `ok_something`.
3. **The reverse check uses a crude literal sweep, on purpose.** Forward
   (code -> table) a false positive puts an unclassified reason in the record,
   so that direction gets the careful resolver. Backward (table -> code) the
   question is only "does this string appear anywhere", and over-reporting
   reachability at worst leaves a dead entry, while under-reporting deletes a
   classification still in use. It also catches `shadow_loop.py`, which
   attributes its own cycle failures (`api_error`, `cycle_exception`) without
   ever going through `decide`.

**Reverse check result: exactly one dead entry, `no_underdog`.** It is
deliberately retained for historical rows, so I added
`RETIRED_SKIP_REASONS` to `forge_shadow_eval.py` - same shape as the existing
`forge.RETIRED_REFUSAL_CATEGORIES`. A retired reason that a strategy starts
emitting again is ALSO red, because a live reason filed as historical reads as
settled and is worse than an unclassified one.

16 new tests, 11 of which run the resolver against synthetic modules where the
right answer is known by construction. Untested guard infrastructure is what
went stale the first time.

## Task 3: D-296, the kill clock — READ THIS, THE PREMISE CHANGED

Built `kill_clock_status()` and `liquidation_row_count()` in
`near_liq_trigger.py`, plus 10 tests. There was **no existing kill-condition
evaluator anywhere in the repo** - the clause lived only in the module
docstring - so this is new apparatus, not a guard bolted onto old code. I kept
it to the one clause D-296 names.

**`liquidations` is no longer empty. It has 2 rows, both Bybit, both `long`,
$1,803.89 BTCUSDT and $780.80 ETHUSDT, both stamped 1787050458699.** Raven's
instruction file, D-296 and CLAUDE.md all say zero. The tape printed during
this session.

So the guard fired exactly once and then handed over. Live reading right now:

```
{"clock_running": true, "status": "kill_clock_running", "liquidation_rows": 2,
 "clock_started_ms": 1787050458699, "days_elapsed": 0.012, "days_required": 30,
 "entries_to_date": 0, "evaluated": false, "fired": false}
```

The clock is anchored to `min(ts)`, the tape's FIRST print, not to deployment
and not to now - which is the whole point of D-296 and is why day 0 is today
rather than whenever the strategy shipped. **The 30-day window now closes
around 2026-09-17.** Note both prints are under `SECOND_LOCK_MIN_USD = 5,000`,
so the second lock still cannot pass on this data; a live tape and a USABLE
tape are not the same thing and the clock only cares about the first.

Three states, not two, deliberately: `clock_running` / `evaluated` / `fired`.
`entries_to_date=None` reports unevaluated rather than passing - a strategy
surviving a test nobody ran is the same convention 11 error one level down.
`no liquidations table` and `table present but empty` are separate names: same
consequence today, different owners.

The FIRST kill clause (trailing-50 win rate vs average entry price) is
untouched. It is gated on 50 resolved trades existing, so it is already
self-deferring. Only the calendar clause needed a guard, because a calendar
runs whether or not anything happens.

## Task 4: D-297 — mostly pre-done, and its numbers are already stale

A concurrent session had already added `NOISE_FLOOR_ERROR_BY_ASSET` and
stamped the whole three-asset dict on gated rows. What was missing was the
per-row scalar D-297 actually asks for, so I added
`error_at_floor_pct_for()` in `strike.py` and
`strike_proxy_error_at_floor_pct` on the gated row, with
`strike_proxy_error_unavailable` flagged when an asset has no measurement.
An unmeasured asset reads `None`, never `0.0` - zero would be a perfect proxy
produced by a total absence of evidence.

I did NOT add a runtime JSON read. The existing design hardcodes the constants
and pins them to the file with a drift test, which is stronger than loading at
import; Raven's fallback spec (`None` + a flag if the file is missing) is
honoured at the asset level instead.

**D-297's numbers were superseded within the same session.** The ruling cites
BTC 2.7 / ETH 6.6 / SOL 14.3 from a ~220-window run. A concurrent session
re-ran the harness at 500 windows, wrote
`research/strike_proxy_by_asset_500w.json`, updated the constants and
repointed the drift test:

| asset | D-297 (220w) | now (500w) | n at floor |
|---|---|---|---|
| btc | 2.7% | **5.1%** | 175 |
| eth | 6.6% | **9.3%** | 248 |
| sol | 14.3% | **15.8%** | 196 |

Every rate went UP and the BTC/SOL spread narrowed from ~5x to ~3x. The old
220-window file is still on disk and no longer sourced. D-297 as written now
quotes numbers nothing reads. **This needs a D-number or an amendment**, and
the per-asset-floor question (item 5 on the CLAUDE.md list) should be decided
against 500 windows, not 220.

## Tasks 2 and 5

Task 2: flipped `global_temperature_market_excluded` from GENUINE to
DATA_BLOCKER per D-291, with the reasoning and the OUT_OF_UNIVERSE note in the
comment. No test asserted the old class.

Task 5: **already done by a concurrent session, with better names than the
instruction file asked for.** `no_underdog` is split into `no_book_midpoint`
(DATA_BLOCKER) and `book_implied_exact_tie` (GENUINE), plus a third,
`no_cushion_data`, that the instruction file did not anticipate. Both literals
sit at the call site rather than riding out of `_underdog()` in a variable -
which is the shape the AST guard can read. Raven asked for
`no_underdog_missing_midpoint` / `no_underdog_tied_mids`. **I did not rename.**
The semantics match exactly, the existing names are shorter and already
classified, and renaming would churn a file another session is in. Flagging it
rather than deciding it.

## Tests

```
env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q --ignore=tests/test_dashboard_charts.py
2289 passed, 1 skipped, 3 failed  (313s)
```

Two of the three failures were **concurrent-session mid-edit collisions** and
pass on re-run in isolation (`test_dip_arb.py::...test_the_estimate_satisfies...`,
`test_fair_value_arb_variants.py::...test_the_shadow_loop_identity...`).
Convention 21: a red suite can mean "another session is mid-edit".

The third is the **known permanently-red** `TestConfigWiring::
test_config_yaml_matches_the_module_defaults` (config 0.0 vs module 30.0). It
is red by construction, pre-existing, untouched by me, and still waiting on
Aym's ruling. It is item 2 on the CLAUDE.md list.

The 434 tests in the files I touched all pass:

```
tests/test_forge_shadow_eval.py tests/test_near_liq_trigger.py
tests/test_strike_proxy.py tests/test_weather_arb.py
tests/test_liquidation_strategies.py tests/test_polymarket_shadow_loop.py
tests/test_grid_hedge.py   ->  434 passed
```

## Files

Created:
- `tests/skip_reason_ast.py`
- `docs/handoffs/2026-08-18-raven-rulings-d289-d297-implemented.md`

Modified:
- `agents/forge_shadow_eval.py` - D-291 reclassification, `RETIRED_SKIP_REASONS`
- `engine/polymarket/strike.py` - `error_at_floor_pct_for`, `ERROR_UNAVAILABLE_FLAG`
- `engine/polymarket/shadow_loop.py` - per-asset scalar on the gated row
  (written via `engine.concurrency.safe_edit`, convention 26)
- `strategies/polymarket/near_liq_trigger.py` - kill clock + docstring
- `tests/test_forge_shadow_eval.py` - rewritten guard + 16 tests
- `tests/test_near_liq_trigger.py` - 10 tests
- `tests/test_strike_proxy.py` - 4 tests

## Needs a ruling

1. **D-297's numbers are stale.** 500 windows supersede 220. Amend the D, and
   decide the per-asset-floor question against the new table.
2. **Task 5's naming.** `no_book_midpoint` / `book_implied_exact_tie` shipped
   instead of `no_underdog_missing_midpoint` / `no_underdog_tied_mids`.
   Confirm or rename.
3. **D-296's premise moved.** The tape is live (2 rows) and the clock started
   today. Both prints are under the $5,000 second-lock floor, so the strategy
   still cannot pass its own gate. Does the clock deserve a second guard on
   USABLE prints rather than any print? I did not build one - D-296 says "at
   least one row" and I implemented what it says.
4. Still open from before: the daily-loss-breaker posture and the permanently
   red config test.
