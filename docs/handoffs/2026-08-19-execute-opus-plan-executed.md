# D-329 execute-opus-plan - EXECUTED RECORD (independent verification)

> **Written by `cody-reconcile`, 2026-08-19 ~01:50 EDT.** `cody-execute-plan`
> (PID 42688, sonnet, directive
> `docs/handoffs/from-raven/2026-08-19-execute-opus-plan.md`) landed its work
> and **exited without writing a handoff, without running a full suite, and
> without committing**. Ordered by
> `docs/handoffs/from-raven/2026-08-19-reconcile-unverified-work.md` Task 3.3.

## Read the other file first, then this one

**A peer session already wrote `docs/handoffs/2026-08-19-execute-opus-plan.md`
(committed `7f1a6d6`, 01:21 EDT) covering the same session.** That file is
accurate and this one does not restate it. This file exists to add what that
one could not: **independent re-verification**, and one correction.

## The correction

That file reports *"Harness: 21/21 (reported by verify-commit-restart session
on the same tree)"* - a relayed claim, not its own run, and the tree had
changed since. **I ran it myself on the committed tree at `f25bab2`: 21/21,
exit 0.** The claim was right; it is now measured rather than relayed
(convention 31).

## Verified by me, on `f25bab2`, no peer session active

| Check | Result |
|---|---|
| Full suite | **3,850 passed, 1 skipped, 0 failed** (346.74s) |
| Harness `backtest/validate_harness.py` | **21/21, exit 0** |
| Registry | **26**; index 25 `FairValueMirrorFade` = `('smart_money',)`; first eight pinned intact |
| `counter_ask` populated, not instrumentation-only | YES - `fair_value_arb.py` `evaluate()` stamps `counter_side`, `counter_ask`, `counter_token_id` into `feats`, inherited by every family member (convention 23). LOGGING ONLY: no gate reads them. |
| `fill_was_maker` populated, not instrumentation-only | YES - `shadow_loop.py` `_migrate_positions_fill_provenance_column` ALTERs the live table, and `record_entry`'s INSERT writes `1 if position.entry_liquidity == 'maker' else 0`, read off the field the maker/taker stats already use rather than re-derived. |
| `db/schema.sql` | `fill_was_maker INTEGER NOT NULL DEFAULT 0` (backfills existing rows false) |
| Convention 32 | Present in `docs/CONVENTIONS.md` |
| Env B whitelist correction | NOTED for next restart, NOT applied to the running filter (D-329 item 4) - correct, the `--strategies` list is bound once at construction |

## The commit, and a convention-16 finding

All of it is in **`4d03681`**, message: "D-329: Opus plan executed - fade
probe paused (evidence-cited), counter_ask + fill_was_maker measurements,
Conv 32 fill-provenance rule, 3,850 pass". Pushed; `origin/main == HEAD`.

**That commit swept a third session's work.** `cody-whitelist-warn` records
in `docs/handoffs/2026-08-19-whitelist-warning.md` that a peer ran
`git add -A` and pulled its `filter_strategies_by_name()` warning change and
`tests/test_shadow_loop_strategy_filter.py` into `4d03681` - whose message
does not mention them. **Convention 16 violated** (`never git add -A`), and
convention 31's hazard realised in the direction nobody predicted: Raven
warned the whitelist-warn commit might sweep D-329's work; the sweep went the
other way.

Consequence for a later reader: `4d03681` is a **combined** commit covering
`cody-execute-plan` (D-329), `cody-mirror-fade` (D-326/D-327) and
`cody-whitelist-warn` (the `--strategies` warning). Its message names only
the first. Run `git show --stat 4d03681` before citing it.

## Not done, explicitly

- No main loop restart (PID 41735 on `e033078`), no env B restart (PID 38881).
- No backtesting. Nothing unpaused.
- **D-328 was ruled but never written** - see
  `docs/handoffs/2026-08-19-mirror-fade-executed.md`, "The gap".
- Complement-mapping logic itself not attempted (NOT_TESTED, convention 11:
  mid-sum matching over-matches 61.7% of token-timestamps).
