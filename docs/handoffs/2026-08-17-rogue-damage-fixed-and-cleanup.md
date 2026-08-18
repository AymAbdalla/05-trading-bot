# Rogue-session damage reverted + open items 4 and 6 closed

**From:** Cody
**Date:** 2026-08-17 ~23:20
**Directive:** `docs/handoffs/from-raven/2026-08-17-fix-rogue-damage-and-cleanup.md`
**Status:** all 4 tasks done. Nothing committed, nothing staged.
**Suite:** `1314 passed, 1 skipped, 0 failed` (full run, 324s).
**Harness:** `validate_harness.py` 21/21, exit 0, A5 AGREE.

## Read this first: two corrections to the directive's premises

1. **The shadow loop PID in CLAUDE.md is stale.** 17603 is dead. The loop is
   now **PID 27030**, started 23:02, same args (`--poll 5 --equity 1000`).
   Somebody restarted it between the CLAUDE.md write and this session. It was
   not touched here. The sweep is still **PID 18543** and was not touched.
2. **"28 weekly series excluded" and "25 weekly series excluded" were both
   right, and they are two different numbers.** Measured this session:
   - 25 dropped by the `< 100` test-slice gate (task 3's target)
   - 3 more dropped earlier, by the separate `len(candles) > 200` total-bars
     gate: ARM (153 weekly bars), IBIT (136), RDDT (126)
   - 25 + 3 = 28.

   Convention 20: two drop causes were sharing one number. Task 3 only moves
   the first one. The 3 are a different question and are untouched.

## Task 1 - D-281 name restored (DONE)

DECISIONS.md tops out at D-283. **D-284 does not exist**, so every citation of
it the rogue session wrote was a citation of nothing. D-281 says the
`strategy_name` must be `PM_corridor_pair`. Applied:

| file | change |
|---|---|
| `strategies/polymarket/corridor_pair_live.py` | `strategy_name = 'PM_corridor_pair'`; the D-284 comment block replaced with a D-281 one |
| `strategies/polymarket/__init__.py` | 2 sites: the D-284 line in the rename note, and the `PM_corridor_pair_live` row in the scoring table |
| `tests/test_polymarket_new_strategies.py:137` | asserts `PM_corridor_pair` |
| `tests/test_fair_value_arb.py:1154` | asserts `PM_corridor_pair` |
| `engine/polymarket/shadow_loop.py:81` | docstring (this is task 4) |

The module and the class keep `corridor_pair_live` / `CorridorPairLive`. Only
the key moved, which is exactly what D-281 rules.

**One file outside the directive's scope list was also fixed:**
`strategies/proposals/README.md` lines 116-117 said the name was
"`PM_corridor_pair_live` (D-281, settled by D-284)". Left alone, the rogue
session's false citation would have survived in the one document a reviewer
reads to understand the 005 boundary. Flagging it rather than hiding it. Grep
now returns zero `D-284` and zero `PM_corridor_pair_live` anywhere outside
`docs/handoffs/` (which is history and should keep saying what it said).

**Still open for Aym:** the directive notes he may want `PM_corridor_pair_live`
after all, on "one strategy, one name everywhere" grounds. That argument is not
wrong - the module and class do carry `_live`. No rows exist under either key
(never scored, D-268), so this is still free to change. It needs a real
D-number either way; D-284 is currently an empty slot.

## Task 2 - cohort bridge closed (DONE, open item 6)

`mean_reversion = True` declared on the class for `RsiExtreme`,
`BollingerReversion`, `StochRsiOversold` in `strategies/builtin/expanded.py`.
Class attribute, not `__init__` - that is the D-276 lesson, `_stack_applies`
reads the attribute and an instance-level set would have made this inert the
same way the V3 cohort edit was.

Then `COHORT_BRIDGE_EXPANDED_PY` deleted from `strategies/cohorts.py`, its
import and its use deleted from `run_incremental_graveyard.py`. The sweep now
passes **no cohort name list at all**; route (2) in `_stack_applies` is dead
config. `COHORT_DECLARED_ON_CLASS` is now just `R006_COHORT`.

`_assert_consistent()` lost its `stray` check, which only had meaning while the
bridge existed. The other two assertions stand.

**This is a semantic no-op and it is asserted as one, not claimed as one.**
New test `test_closing_the_bridge_resolved_the_same_seven` builds the harness
both ways - with the old three names, and with nothing - and asserts both
resolve the identical seven. That matters because a silent cohort change at an
unchanged `GATE_VERSION` is precisely the pooling `assert_gate_version_uniform`
cannot catch. **GATE_VERSION is NOT bumped**, and that is the justification.

The test file kept `LEGACY_BRIDGE_NAMES` as a literal so the before/after arm
survives the constant's deletion. `_sweep_harness(cohort=bool)` became
`_sweep_harness(bridge_names=None)`.

## Task 3 - fourth starvation site fixed (DONE, open item 4)

`run_incremental_graveyard.py` had the same hardcoded `100` R-005 removed from
`_scan_start`. New `min_test_slice_bars(timeframe)` mirrors the harness floor.

A series dropped here is dropped for **all 55 strategies and all 11 exit
configs at once**, so the gate is set at the LOOSEST bar that still leaves
something scannable - the floor of `_scan_start` plus one bar. Per-strategy
starvation is the harness's job and it already writes NOT_TESTED for it
(convention 11).

```
  tf  series  min_slice  skip_before  skip_after
  1d     175         51            0           0
 1wk     173         51           25           1
  1h     178        100            0           0
  5m     178        100            0           0
 15m     181        100            0           0
```

**24 weekly series unblocked.** Slice sizes 53 to 98. Daily was already clear
at 100 (its slices are ~250 bars), so this is weekly-only in practice.

**Intraday is byte-for-byte unmoved**, deliberately. `min(SCAN_WINDOW, 100)`
is now written as the constant instead of the literal `100`, so it tracks the
harness rather than drifting from it (convention 23), but it evaluates to the
same 100 and no intraday series changes state. That is the control that makes
the weekly improvement readable at all (convention 17).

**One weekly series is still skipped and should be: RIVN**, 249 bars, slice of
exactly 50. Scan starts at bar 50, so it has zero scannable bars. Loosening a
gate is not the same as removing it; NOT_TESTED is the honest verdict there.

**Off-by-one noted, deliberately NOT fixed.** The intraday gate admits a
100-bar slice, which scans from bar 100 and therefore has zero scannable bars
too - the same defect RIVN's row exposes on the weekly side. Fixing it means
moving the intraday threshold, which breaks the bit-identical intraday control
mid-re-sweep. Raven's call, not Cody's. It costs nothing today: zero intraday
series are anywhere near the boundary.

Also fixed: the `not_tested_reason` string and the SKIP log line said
"< 100 minimum" as a literal. Both now interpolate the actual threshold, so a
weekly NOT_TESTED row no longer claims a requirement that was never applied
to it.

## Task 4 - shadow_loop docstring (DONE)

`engine/polymarket/shadow_loop.py:81` now reads `PM_corridor_pair`, aligned in
the table's column. Comment only, no wiring. This is the only `engine/` change.

## What this means for the running sweep - READ BEFORE PROMOTING

**PID 18543 cannot see any of this** (convention 13, source snapshotted at
import). It is running the pre-fix runner, so:

- its weekly coverage is still the OLD 25-series exclusion, not the new 1
- its cohort still resolves via the bridge name list

The cohort route is a no-op, so that half does not invalidate anything. **The
weekly slice gate is not a no-op.** 18543's output will be missing 24 weekly
series that the fixed runner would test. Its intraday and daily halves are
unaffected.

That is on top of what CLAUDE.md already flagged: 18543 also predates
D-276..D-279 and the `min_bars_for()` wiring. **Re-run scope is Raven's call.**
Cody did not kill it and did not restart it.

If it is promoted as-is, the 24 weekly series simply stay absent, exactly as
they have been for the life of the project - no worse than the baseline, just
not better. Nothing here forces a re-run.

## Tests

+5 net, all in `tests/test_harness_warmup_cohort.py`:

- `test_runner_slice_gate_is_timeframe_aware_not_a_bare_100`
- `test_runner_slice_gate_leaves_intraday_exactly_where_it_was` (the control)
- `test_runner_slice_gate_still_drops_a_slice_with_nothing_to_scan`
- `test_closing_the_bridge_resolved_the_same_seven` (the no-op proof)
- `test_the_sweep_supplies_no_cohort_name_list`
- `test_the_declared_property_is_the_mechanism_not_the_name_list` renamed to
  `..._is_now_the_only_route` and now asserts all seven, not four

## Files touched

```
strategies/polymarket/corridor_pair_live.py    task 1
strategies/polymarket/__init__.py              task 1
tests/test_polymarket_new_strategies.py        task 1
tests/test_fair_value_arb.py                   task 1
strategies/proposals/README.md                 task 1, OUTSIDE stated scope
engine/polymarket/shadow_loop.py               task 4, docstring only
strategies/builtin/expanded.py                 task 2
strategies/cohorts.py                          task 2
backtest/run_incremental_graveyard.py          tasks 2 and 3
tests/test_harness_warmup_cohort.py            tasks 2 and 3
research/graveyard/harness_validation.json     regenerated by validate_harness
```

Untouched as instructed: `backtest/vectorized_harness.py`, all of `engine/`
except the one docstring line.

## For Raven

1. **D-284 is an empty slot.** The name question needs a real D-number whichever
   way Aym rules. Right now DECISIONS.md and the code agree on
   `PM_corridor_pair`; before this session they did not.
2. **Re-sweep scope.** 18543 is missing 24 weekly series relative to the fixed
   runner. Open items 1-2 already implied a re-run; this adds a third reason
   but does not force one.
3. **Intraday off-by-one** in `min_test_slice_bars` - documented above, left
   alone on purpose, needs a ruling.
4. **`rising_three_methods` retirement** (old open item 5) still unruled.
5. Open items 4 and 6 from CLAUDE.md are now CLOSED.
