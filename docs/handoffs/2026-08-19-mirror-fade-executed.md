# Mirror-fade probe (D-326 / D-327) - EXECUTED RECORD

> **Written by `cody-reconcile`, 2026-08-19 ~01:50 EDT, NOT by the session
> that did the work.** `cody-mirror-fade` (PID 40151, sonnet, directive
> `docs/handoffs/from-raven/2026-08-19-mirror-fade-probe.md`) landed its work
> in the tree and then **exited without writing a handoff**. This file is
> reconstructed from the tree, the DECISIONS entries and the directive, per
> `docs/handoffs/from-raven/2026-08-19-reconcile-unverified-work.md` Task 3.3.
> Everything under "Verified by me" I ran myself; everything else is
> attribution, and convention 31 applies to this file as much as to a commit
> message.

## Directive vs what landed

| Directive task | Landed? | Notes |
|---|---|---|
| T1 `fair_value_mirror_fade.py`, reuse parent model via `super()` | YES | Subclasses `FairValueArb`, calls `super().evaluate()`; no reimplementation of the model. |
| T1.2 mirror the side | YES | `flipped = opposite(intended)`, re-priced against the flipped side's OWN book, never `1 - parent_price` (the overround makes that wrong). |
| T1.3 gates: mirror edge >= 0.05, ask <= 0.60, depth >= 2x | YES | `MIRROR_EDGE_THRESHOLD = 0.05`, `ENTRY_ASK_CAP = 0.60`, `DEPTH_MULTIPLE = 2.0`. |
| T1.4 fixed 5 shares, max 2 concurrent | YES | `MIRROR_SHARES = 5`, `MAX_CONCURRENT_POSITIONS = 2`. |
| T1.5 hold to settlement, stop 0.00 target 1.00 | YES | `manage_exit` never returns EXIT; `manages_exits = True` is bookkeeping only, `_note_open` fires on first sight of a FILLED position (the 032/034 self-starvation fix shape). |
| T1.6 register at index 25, **declare crypto_updown only** | **DEVIATED, correctly** | Registered at index 25 (`len == 26`, first eight pinned intact). But it declares `supported_market_types = ('smart_money',)` - **PAUSED**, not crypto. See below. |
| T2 034 re-gate: relax throttle, new kill condition | YES | `MAX_TRADES_PER_WINDOW = 12` (was inherited 3); kill condition in module docstring and D-327. |
| T3 DECISIONS D-326, D-327, **D-328** | **PARTIAL** | D-326 and D-327 present. **D-328 IS MISSING** - see "The gap" below. |
| T4 suite + harness | Not run by that session | Run by me, numbers below. |
| T4 handoff, CLAUDE.md rewrite, webhook | **NOT DONE** | The session died first. This file is the substitute handoff. |
| Env B whitelist correction | NOTED, not applied | `filter_strategies_by_name()` takes its list once at construction (`--strategies`), so the running env B (PID 38881) cannot be updated without a restart, and nobody restarted it. Correction recorded in D-329 item 4 for the next natural restart. |

## The one deviation, and why it is right

The directive said "Declare crypto_updown only." The shipped file declares
`('smart_money',)` - the D-322/D-323 pause sentinel. **This is not a mistake
and not drift.** A later Raven directive
(`docs/handoffs/from-raven/2026-08-19-execute-opus-plan.md`, D-329) amended
D-326 after Opus split the mirror evidence by fill provenance:

| subset | n | mirror net | t |
|---|---|---|---|
| ALL settled (the original D-326 evidence) | 355 | +$281.74 | 3.46 |
| TAKER (executable) | 169 | +$51.15 | 1.52 |
| TAKER excl. ask <= 0.10 | 116 | +$40.24 | **1.19** |

80% of the approved signal was MAKER fills, and a maker fill cannot be
mirrored: `paper_adapter._through_and_touch` fills a resting BUY only once
the market has already moved through the limit and books it AT the limit, so
that fill exists only in states that already moved against us. Its mirror is
a counterfactual that never happens. The executable portion is t=1.19 on
n=116, below the t>=2.0 bar the file was always going to be judged against -
so it ships PAUSED, before its first shadow cycle. It has **zero rows in
`db/trading.db` under any environment** and has never traded.

Reverting is one line: restore `supported_market_types =
(MARKET_TYPE_CRYPTO_UPDOWN,)` (re-add the import) once this file's OWN
taker-only signals clear t >= 2.0 on n >= 250 excluding entries below ask
0.10.

## The gap: D-328 was ruled but never written

The directive's Task 3 ordered three entries. **D-326 and D-327 exist;
D-328 does not.** `grep "^### D-32" docs/DECISIONS.md` runs D-325, D-326,
D-327, then jumps straight to D-329.

This matters because `strategies/polymarket/fair_value_mirror_fade.py`
already cites it in its module docstring: *"ratified as the standing edge
assessment at D-328."* **That is a dangling citation - a live example of
convention 24 (a cited D-number is not a decision).**

I did NOT back-fill it. Ratifying a standing edge assessment is Raven's
ruling to record, not mine to invent, and my directive bounded record
repairs to three named items. Raven's own directive text specifies the
content, so this should be a one-step paste:

> D-328: Opus analysis ratified as the standing edge assessment (execution =
> 9%, model = 91%; env-b whitelist corrected; no time-of-day edge).

**Until that entry exists, the mirror-fade docstring's D-328 citation should
be read as a claim, not a decision.**

## Verified by me (`cody-reconcile`), not inherited

Run on the committed tree at `f25bab2`, with no peer session active:

- **Full suite: 3,850 passed, 1 skipped, 0 failed** (346.74s).
  `.venv/bin/python -m pytest tests/ -q --ignore=tests/test_dashboard_charts.py`
- **Harness: 21/21, exit 0.** `.venv/bin/python backtest/validate_harness.py`
- **Registry: 26.** Index 25 = `FairValueMirrorFade`, `('smart_money',)`.
  Pinned first eight unchanged. Paused set is indices 2, 10, 11, 17, 25.
- 034: `MAX_TRADES_PER_WINDOW = 12` present, kill condition (realised
  settlement frequency over first 60 entries below 0.30 against mean entry
  ask 0.33) present in both the module docstring and D-327.

## Where the work is committed

`4d03681` - "D-329: Opus plan executed - fade probe paused (evidence-cited),
counter_ask + fill_was_maker measurements, Conv 32 fill-provenance rule,
3,850 pass". The mirror-fade session's files were committed by a PEER
session inside that commit, whose message names D-329 rather than D-326/327.
Honest reading: `4d03681` is a combined commit covering three sessions' work.

## Not done, explicitly

- No main loop restart (PID 41735 stays on `e033078`), no env B restart
  (PID 38881), no backtesting, nothing unpaused.
- CLAUDE.md was not rewritten by this record (a peer may own it).
- D-328 not written (above).
