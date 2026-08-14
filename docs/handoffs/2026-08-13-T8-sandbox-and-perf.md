# Handoff: T8 Sandbox + Sweep Performance Fix + Reconciliation

**Date:** 2026-08-12 -> 13 (night session)
**Built by:** Claude Code

## Built

1. **Sweep performance fix** (D-209): ta facades made per-call indicators
   ~19x slower; graveyard re-run would have taken weeks. `run_sweep` now
   scans each strategy once (`scan_all_bars`) and replays cached signals
   across exit configs. Equivalence verified (0 mismatches / 24 combos) and
   pinned as a test. Re-run restarted; watch `logs/graveyard_rerun.log` and
   `research/graveyard/v0_graveyard_full.json`.
2. **T8 strategy sandbox** (D-210): `sandbox/validator.py` (AST allowlist,
   fails closed), `sandbox/_runner.py` (subprocess conformance, 15s timeout),
   hash pinning + family-drift enforcement in strategy_registry (migrated
   with family/code_hash columns). 21 tests. NOT yet wired: an engine-side
   loader that loads shadow/live registry strategies with verify_hash - the
   engine currently runs builtins only, so the loader lands with shadow mode.
3. **Reconciliation on boot** (D-211): paper-mode semantics in
   `Executor.reconcile_on_boot()`, wired into main.py, tested.

## State at handoff

- Tests: 142 passing (tests/) + 6 known-answers. validate_harness 21/21.
- Two fresh independent re-audit agents (backtest layer; engine+strategies)
  were relaunched after the spend-limit reset; their reports had not landed
  when this note was written - check with Aym/Claude Code session.
- Graveyard re-run in progress in background.

## Aym's owed items (unchanged)
1. Rotate the Alpaca key. 2. First supervised paper run + kill-switch drill.
3. Review pass over DECISIONS.md (CC DECISION entries are challengeable).

## Suggested next build steps (for Raven to sequence)
- Engine-side registry loader (shadow-mode strategies) using sandbox.verify_hash.
- Telegram alerts (SPEC 6.2/6.3 notification paths), then launchd plist (T11).
- SPEC 5.3 acceptance bar encoded as a real gate.
- Full live-mode reconciliation before any live consideration (far off).

## Watch item
The restarted graveyard re-run holds 100% CPU but averaged well below that
over its first half hour (first intraday ticker still in progress at note
time). If daily tickers don't clear at ~30s each once it reaches them,
profile the scan path (`scan_all_bars` -> strategy.scan -> ta facades) before
letting it run for days. It saves per ticker, so partial progress is never lost.
