# Skip-classification blind spot: the AST test cannot see a variable

**Written by:** Cody, 2026-08-18 06:45
**Scope:** `agents/forge_shadow_eval.py`, `tests/test_forge_shadow_eval.py`
**Status:** classification gap CLOSED in the table. The TEST hole is NOT closed,
deliberately, and needs a D-number. One classification DISAGREEMENT is open.

Written as its own file rather than appended to
`docs/handoffs/2026-08-18-speed-audit.md`, which another session was writing
minutes earlier (mtime 06:20). Convention 21.

---

## What happened

`tests/test_forge_shadow_eval.py::test_every_skip_reason_the_strategies_emit_is_classified`
AST-walks `strategies/polymarket/*.py` for `decide('SKIP', <string literal>)`
and asserts each reason has an entry in `SKIP_CLASSIFICATION`.

It only matches a **string literal in the second argument position**. Eight call
sites in the package pass a variable or an expression instead, and every reason
reachable through those sites was invisible to the test:

| call site | second arg | reasons behind it |
|---|---|---|
| `fair_value_arb.py:446` | `'fair_value_' + est.reason` | covered by the `fair_value_` prefix rule |
| `grid_hedge.py:757` | `implied_status` | 2, both were UNCLASSIFIED |
| `weather_arb.py:1682` | `station_status` | 2, both already classified as exact keys |
| `liq_cascade_chaser.py:323` | `liq.reason` | 4, all were UNCLASSIFIED |
| `small_liq_continuation.py:298` | `liq.reason` | same 4 |
| `near_liq_trigger.py:964` | `window.reason` | same 4 |
| `near_liq_trigger.py:859` | `feed.status` | 6, all were UNCLASSIFIED |
| `spread_harvest_maker.py:288` | `IfExp` over two literals | 2, both were UNCLASSIFIED |

**16 distinct reason strings were unclassified while the suite was green.** Two
of them (`no_recent_liquidation`, `liquidation_below_second_lock_min`) were
literals the test DID catch, and were the only genuine red in the suite. The
other fourteen were green by accident.

The `weather_arb.py` station reasons were the one reported-clean case, and the
report held: `resolution_station_unknown` and `resolution_station_ambiguous` are
exact keys, not loose prefix matches. Verified directly, not trusted.

An UNKNOWN classification is not cosmetic. `derive_gaps()` splits on
`blocked_fraction`, which counts DATA_BLOCKER + SIM_LIMIT. An unclassified
DATA_BLOCKER lands in neither, drags `blocked_fraction` below 0.5, and flips a
strategy from NOT_TESTED to RAN_NO_ENTRY. That is exactly the "could not run"
to "ran and found nothing" inversion convention 11 exists to prevent, and every
one of the fourteen invisible reasons is a cannot-run.

## What is already fixed

All 16 are now in `SKIP_CLASSIFICATION`, enumerated by hand from the function
that produces each variable. See that block for the per-reason reasoning.

**Honest limit on the impact claim (convention 15):** `db/trading.db` held
41,530 skips at the time of writing and **zero** of them were any of these 16
strings. This was pre-emptive, not a correction of a miscount that already
landed in a report. Nobody has yet published a wrong number because of this.
The exposure was forward-looking: the liquidation and whale feeds are new, and
the first time either recorder dies mid-session those skips would have gone to
UNKNOWN.

## What is NOT fixed, and the ruling needed

The test still cannot see a variable. Adding dict entries fixed today's
instances; it did not fix the mechanism, and the next strategy that forwards a
feed status will reopen the hole silently. Convention 22: a claim in a docstring
is not a wiring test, and "we remembered to check the variable sites this time"
is a docstring-grade guarantee.

Two options. **Neither implemented** - this changes what the harness enforces,
so it is a decision, not a cleanup.

### Option A: teach the test to resolve simple indirection

Extend the AST walk so that when the second argument is not a literal it tries,
in order: a module-level constant tuple/list of strings (`NO_DATA_REASONS`), a
local variable assigned only from string constants in the same function, and an
`IfExp` whose branches are both constants. If it cannot resolve, **fail loudly**
naming the unresolvable site rather than skipping it.

- Pro: catches today's eight sites, and the failure mode is a red test rather
  than a silent pass. The `NO_DATA_REASONS`-style export is good design and
  should not be penalised.
- Con: real work in the test, and a partial resolver invites the belief that
  the test is complete when it is only more complete. The loud-fail clause is
  what keeps that honest, and it must not be dropped for convenience.

### Option B: require strategies to emit literals

Ban a non-literal second argument outright: assert every `decide('SKIP', ...)`
passes a string constant, and make the strategies expand their variable returns
into explicit per-branch `decide` calls.

- Pro: the simplest possible test, and it stays correct with no maintenance.
- Con: it forces `liquidation_feed`'s four shared reasons to be re-spelled in
  three strategies, which is the exact duplication convention 20 warns about
  ("one cause, one name, across modules") and which `NO_DATA_REASONS` exists to
  prevent. It would make the code worse to make the test easier.

### Recommendation: Option A, with the loud failure mandatory

Option B trades a real code property (one cause, one name) for a test
convenience. Option A costs more test code but leaves the strategies alone and,
critically, converts an invisible gap into a named red. The load-bearing part is
the unresolvable-site failure: without it, Option A is just a bigger version of
the same blind spot.

**Needs a D-number from Raven or Aym.** DECISIONS.md is the record; this file
is only the argument.

## THREE SMALLER ITEMS, all needing a call

### 1. OPEN DISAGREEMENT: `global_temperature_market_excluded`

While this pass was running, a concurrent session rewrote `weather_arb.py`
(06:33) adding three new literal reasons, and then added them to the table
itself (06:40) before this session could. Their entries were left in place
rather than reverted (convention 21). Two of the three,
`source_reporting_precision_unknown` and
`source_precision_finer_than_ladder_step`, are DATA_BLOCKER and match what this
session had independently concluded.

The third does not. They classified `global_temperature_market_excluded` as
**GENUINE**, arguing "the question WAS read and the product was declined". This
session had concluded **DATA_BLOCKER**.

The disagreement matters because GENUINE feeds RAN_NO_ENTRY. If `weather_arb`
is ever pointed at a universe containing global-anomaly markets, every one of
those skips counts as "the strategy looked at this market and found no edge" -
on a product it was never built to trade and has no station for. That reads as
a measurement and is not one.

The honest answer may be that **neither class fits**, and the table needs a
fourth: `OUT_OF_UNIVERSE`, for a market the strategy declines on identity rather
than on inputs or on edge. Today it would hold exactly one reason, which is a
fair argument against adding it. Raven's call. Left GENUINE in the meantime -
this session did not silently overwrite another session's reasoned entry.

### 2. `no_underdog` pools two causes

`spread_harvest_maker._underdog()` returns `(None, 'book_implied', None)` both
when a midpoint is MISSING (a one-sided book, an absent input) and when
`mid_up == mid_down` exactly (a real tie, a market condition). Convention 20
says those are two numbers. Classified DATA_BLOCKER for now under the table's
own tie-break rule, and flagged in a comment. The fix belongs in the strategy,
which this session does not own.

### 3. A reverse check, if Option A lands

The resolver should also assert that every key in `SKIP_CLASSIFICATION` is
reachable from some strategy, so the table does not silently accumulate dead
entries. Not required, but it is the same walk.

## Test state

`tests/test_forge_shadow_eval.py`: **44 passed**.

Full suite (`--ignore=tests/test_dashboard_charts.py`, whose plotly collection
error is pre-existing): **2,108 passed, 4 failed, 1 skipped** on a run that
started before the concurrent weather_arb and risk-gate edits landed. Re-running
the four failures immediately afterwards left **one**:

- `tests/test_polymarket_risk_gate.py::TestConfigWiring::test_config_yaml_matches_the_module_defaults`
  (`daily_loss_limit_usdc` 30.0 vs config)

`config.yaml` and `engine/polymarket/risk_gate.py` were both written at 06:38 by
an active session with nine `claude -p` processes running. That red is theirs,
mid-edit, and was left alone (convention 21). The other two -
`test_fair_value_arb.py` and `test_polymarket_shadow_loop.py` - passed on
re-run, the transient-red pattern convention 21 predicts.

Nothing in this session's change touches the risk gate, the fair-value family
or the shadow loop.

## Files touched

- `agents/forge_shadow_eval.py` - 16 entries added to `SKIP_CLASSIFICATION`,
  additions only. No existing entry altered or removed, and the concurrent
  session's 3 weather_arb entries were preserved verbatim.
- `docs/handoffs/2026-08-18-skip-classification-blind-spot.md` - this file.
- Nothing under `strategies/polymarket/` was edited. `near_liq_trigger.py`,
  `grid_hedge.py`, `spread_harvest_maker.py`, `weather_arb.py` and the
  liquidation feed modules were read only.
