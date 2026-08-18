# D-280 through D-283 caveat rulings applied

**By:** Cody, 2026-08-17 ~22:45-22:55
**Directive:** `docs/handoffs/from-raven/2026-08-17-apply-d280-d283-caveat-rulings.md`
**Scope kept to:** `strategies/polymarket/` and `tests/`. Nothing in `engine/`,
`backtest/`, `strategies/cohorts.py`, `strategies/builtin/` or `docs/DECISIONS.md`
was touched.

## READ THIS FIRST: a fourth session was running and it is editing my scope

The directive named two concurrent sessions (PID 19963, PID 22530). There was a
**third**, PID 23667, started 22:39 from `/tmp/cody-final-integration.md`, and it
is the one that matters here because it edits the exact files I was given:

- it had already renamed `cross_window_relative_value.py` -> `corridor_pair_live.py`
- it had already applied a **2c** edge floor as "D-277"
- it had already added the D-283 final-third check to `corridor_collector`
- it is instructed to **commit everything and restart the shadow loop**
- its brief names the strategy `pm_corridor_pair_live`, not `PM_corridor_pair`

So the directive's premises were stale when I got them, and two of its
instructions describe a state that no longer exists. What I did about each is
below. **Files may have moved under me while I worked** (two edits reported the
file had changed on disk mid-write). Convention 21 applies to reading this
handoff too.

## D-280 PM_temporal_arbitrage: no change

As ruled. Nothing done, nothing needed.

## D-281 PM_corridor_pair

### Caveat 1: strategy_name -> `PM_corridor_pair`. DONE, and I overrode a conflict.

The directive said "keep the class name `CrossWindowRelativeValue`" and "if it
currently returns something like `PM_cross_window_relative_value`, change it".
Neither was true: the class was already `CorridorPairLive` and the name was
already `PM_corridor_pair_live`. I did **not** rename the class back.

The live conflict was the *string*. PID 23667's brief says `pm_corridor_pair_live`;
D-281 and D-283 in DECISIONS.md both say `PM_corridor_pair`. The file's own
comment cited **"D-281 as refined by D-284"** — and **D-284 does not exist in
DECISIONS.md.** The highest D-number in the file is D-283. So the `_live` suffix
rests on a decision number with no ruling behind it, while `PM_corridor_pair` is
written twice in the decision log. CLAUDE.md says DECISIONS.md wins, so:

| file | change |
|---|---|
| `strategies/polymarket/corridor_pair_live.py:171` | `strategy_name = 'PM_corridor_pair'`; the phantom D-284 citation corrected to D-281 (two places) |
| `strategies/polymarket/__init__.py` | docstring only: name updated, `_live` suffix explained as module-only |
| `tests/test_polymarket_new_strategies.py:137` | asserts `PM_corridor_pair` |
| `tests/test_fair_value_arb.py:1154` | asserts `PM_corridor_pair` |

The **module** and **class** keep `corridor_pair_live` / `CorridorPairLive`.
Only the graveyard/dashboard key changed, which is all D-281 rules on.

**Left stale, deliberately:** `engine/polymarket/shadow_loop.py:81` still says
`PM_corridor_pair_live` in its module docstring. `engine/` was forbidden. It is
a comment, not wiring — the loop reads `strategy_name` off the instance — so
nothing is broken, but somebody should fix the line.

**If Raven wants `PM_corridor_pair_live` after all, say so and add a real
D-284.** No data exists under either key yet (the running loop predates this
strategy), so a reversal is currently free. That stops being true the moment
the loop restarts.

### Caveat 2: 8c edge floor. DONE, and it overrode a 2c floor written 30 min earlier.

`MIN_EDGE_VS_BINNED_FAIR` 0.02 -> **0.08** in `corridor_pair_live.py:151`.
The 2c value was PID 23667's work with a written argument for why 2c and not 8c
("an 8c floor on an untested structure risks reporting no fires"). Raven's
D-281 ruling is later and explicit, so 8c stands; the docstring now records both
positions and why 8c won, so the argument is not lost if the floor is revisited.

The gate is now **three named bindings**, not two, so `pair_cap_binding` says
exactly which one stopped the trade (`corridor_pair_live.py:365-383`):

| binding | means |
|---|---|
| `max_pair_cost` | above the brief's 1.41 ceiling |
| `binned_fair_pair_value` | inside 1.41 but **above** binned fair for this lead |
| `edge_floor_8c` | **below** binned fair, but by less than 8c |

New reason string: `edge_below_floor`.

**One interpretation call you should check.** The directive says "add
`edge_vs_binned_fair >= 0.08` as a hard gate" AND "the `require_binned_fair`
second gate stays". Read literally as two independent gates,
`require_binned_fair=False` would still hit the 8c floor and the sensitivity
switch would become dead code. I implemented the floor as the **threshold of**
the binned-fair gate, so `require_binned_fair=False` still disables both — which
is what keeps the switch meaningful and matches its documented purpose
("sensitivity runs only, not a mode to trade in"). If "hard" meant literally
unbypassable, it is a two-line change; tell me.

The comparison is against the **rounded** threshold (`max_pair_cost_binned`).
Unrounded, `1.405 - 0.08` leaves float dust that refuses a pair at exactly 8c.

### Tests added

- `test_corridor_pair_live_needs_8c_of_edge_below_binned_fair` — the one Raven
  asked for: 0.07 edge refused with `edge_floor_8c`, 0.08 edge ENTERs.
- `test_corridor_pair_live_edge_floor_is_the_8c_ruled_in_d281_not_d277s_2c` —
  pins the constant, and asserts a 3c pair (which cleared the old floor) now
  refuses. Convention 17: a floor that drifts back to 2c looks identical in the
  graveyard to a structure that started clearing fair value more often.

### One fixture had to move

`_cpl_ctx` defaults were `ask_15=0.90, ask_5=0.48` = 1.38, which is 2.5c below
binned fair at a 12bps lead. That cleared the 2c floor and does not clear 8c, so
the main ENTER test would have gone green-to-red for the right reason. Defaults
are now `0.85 / 0.47` = 1.32, 8.5c of edge. Every test that passes explicit asks
is unaffected.

## D-282 PM_spread_harvest_taker: default `allow_book_implied_coin_flip=False`. DONE.

`spread_harvest_maker.py:174`. The gate is still fully implemented and tested;
only the default flipped. **The strategy now fires on nothing** on live data,
which is the ruling's intent.

I also had to **rewrite a docstring section that said the opposite.** PID 23667
had added `## WHAT IT SHIPS WITH TODAY: gate=book_implied`, describing the
book-implied default as "the shipped configuration, ruled on and accepted".
D-282 rules the other way. That section now records the reversal rather than
being deleted, so the earlier reasoning is still readable.

### Tests

- `test_spread_harvest_ships_with_the_book_implied_gate_OFF` — the one Raven
  asked for. Asserts the default is False, that a context which *would* ENTER on
  the book-implied gate SKIPs `no_cushion_data` instead, and that the
  `cushion_atr` path still ENTERs (disabled, not removed).
- Six existing tests exercised the book-implied path through the default
  constructor and would have silently started testing the skip path. They now go
  through a new `_book_implied_harvest()` helper that opts in **by name**. I did
  not just change the default arg back in the fixture: a suite that reaches the
  book-implied path by default would keep passing if the shipped default flipped
  back, which is the exact regression D-282 exists to prevent.

## D-283 corridor_collector final-third: check already present, TESTS WERE RED

The one-line fix was already in `corridor_collector.py:189-194` (PID 23667 added
it, labelled D-278). **It was landed without updating the tests, and it left 8
tests failing.** Not a convention-21 phantom — reproducible, and still failing
after the full suite run.

Cause: `_corridor_ctx` built `window_ts=1600`. 1600 // 900 = 900, offset 700, so
it is the **second** third. Every corridor_collector test was skipping at the new
first gate, and every gate below it — price, caps, lead zone, depth, timing —
was unreachable. The fixture was measuring nothing.

Fixed in `tests/test_polymarket_strategies.py`: `_corridor_ctx` now takes
`window_ts=1500` (1500 - 900 = 600, the final third) and derives both slugs from
it. Two slug assertions updated to match.

Test added: `test_corridor_collector_refuses_the_first_and_second_thirds` —
asserts SKIP `not_final_third_of_15m` on both the first (offset 0) and second
(offset 300) third, checks the recorded offsets, and asserts the same context on
the final third still ENTERs, so the check cannot pass by breaking everything.

## Test results

```
tests/test_polymarket_new_strategies.py
tests/test_polymarket_shadow_loop.py
tests/test_polymarket_strategies.py
tests/test_fair_value_arb.py          -> 248 passed
```

Full suite, `env -u PYTHONPATH python3 -m pytest -q`:

```
1309 passed, 1 skipped, 8 warnings in 414.77s
```

**Zero failures.** No other session's in-progress work was red at that moment.
The 8 warnings are the pre-existing `PytestReturnNotNoneWarning` in
`backtest/test_known_answers.py`, unrelated.

## Running jobs: not touched

| job | PID | state after my work |
|---|---|---|
| graveyard re-sweep | 18543 | **ALIVE**, `kill -0` verified |
| Polymarket shadow loop | 17603 | **ALIVE**, `kill -0` verified |

Neither was signalled. Nothing was staged, nothing was committed, no
`git add`. The shadow loop is running 4 strategies from a source snapshot taken
at import (convention 13), so none of today's edits reach it — including the
rename and the two gate changes.

## What Raven should look at

1. **The name.** `PM_corridor_pair` vs `PM_corridor_pair_live`. I went with
   DECISIONS.md over a brief citing a non-existent D-284. If PID 23667 committed
   before I finished, the tree may hold both. Free to reverse **only until the
   loop restarts**.
2. **D-284 is cited in code and does not exist.** Either write it or the
   citation should go.
3. **The "hard gate" reading** on the 8c floor (above). One-line consequence.
4. **`corridor_collector` shipped a gate with 8 red tests.** The check is right
   and the ruling is right; the landing was not. Worth knowing that PID 23667 is
   landing changes without running the suite, given it is also instructed to
   commit everything.
5. `engine/polymarket/shadow_loop.py:81` docstring still says the old name.
6. Convention 5 unaddressed and out of scope: none of these three has a gross
   edge estimate scored by `polymarket_harness.py`. All still NOT_TESTED per
   D-268.

## Files changed

```
strategies/polymarket/corridor_pair_live.py     D-281 name + 8c floor + bindings
strategies/polymarket/spread_harvest_maker.py   D-282 default False + docstring
strategies/polymarket/__init__.py               docstring only
tests/test_polymarket_new_strategies.py         fixture + 3 new tests + opt-in helper
tests/test_polymarket_strategies.py             D-283 fixture fix + 1 new test
tests/test_fair_value_arb.py                    strategy_name assertion
```

`corridor_collector.py` was **not** modified: its D-283 fix was already in place
and correct. Only its tests were.
