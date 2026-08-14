# Self-Audit v2 - Evening Session Work

**Date:** 2026-08-12 (late evening)
**Auditor:** Claude Code auditing its OWN evening-session code. v1 (the full
independent codebase audit) is `docs/handoffs/2026-08-12-claude-code-audit.md`.
**Scope:** everything written after the v1 fixes: ta indicator facades,
cross-harness referee, pandas data loader, session calendar, executor (T9),
botctl, main.py, and the test-gap closures.

## Honest limitation first

The independent re-audit agents for the backtest and engine layers were
terminated mid-run by the monthly spend limit (D-206). The test-suite auditor
DID complete: it mutation-tested three critical fixes (2 killed, 1 survivor
whose gap I closed with a pipeline-level test) and found the vacuous
known-answers suite (now converted to real assertions). So tonight's code is
verified by: 125 passing tests including mutation-hardened regression tests,
the 21/21 validation suite (oracle/delay/accounting/fees/cross-harness), and
this self-review. Self-review is weaker than independent review. Raven should
weight the backtest and engine layers accordingly, and an agent re-audit
should be rerun when the spend limit resets.

## Defects found in my own work during this self-audit (fixed)

1. `_in_xnys_session` built its lru_cache INSIDE the function body, so the
   cache was recreated on every call; it only performed acceptably because
   exchange_calendars keeps its own global registry. Moved to a module-level
   cache. (strategy_lab.py)
2. `botctl.py status` opened the DB in write mode when the file didn't exist,
   silently creating an empty database. Now prints "engine has never run".
3. `cross_harness_check.py` contained a dead first harness assignment and a
   stream-of-consciousness comment block. Cleaned.
4. Earlier in the evening (caught by test runs, recorded for completeness):
   my buy-hold validation tolerance was fixed-width and falsely failed
   high-return series (NVDA +714%); my strengthened twin assertion was wrong
   for uptrend fixtures (inf PF is legitimate there) and was moved to flat
   data where fee drag is provable.

## What I verified about tonight's code, and how

- **ta facades** (rsi/atr/ema/macd/bollinger + vectorized precompute):
  cross-check tests compare production against the original hand-rolled math
  (now `tests/reference_indicators.py`) on 600-bar series tails, plus a
  padding-convention test pinning the warmup behavior every guard depends
  on. Full suite + validation identical before/after the swap (117 -> 125
  tests as coverage grew, zero behavior regressions).
- **Cross-harness referee:** our two harnesses agree EXACTLY (AAPL 22
  trades/32% win rate both; BTC 13/15% both); backtesting.py agrees within
  the documented bands. Tolerance calibration is a judgment call (D-202):
  count drift max(4, 25%) on small samples, 15-point win-rate band as the
  systematic-error detector.
- **Pandas loader:** all three CSV formats load with identical results
  (integrity scan: 936 clean / 0 flagged before and after).
- **Executor (T9):** 8 dedicated tests cover the acted-flag protocol
  (approved -> acted=1, blocked -> skip_reason recorded), stale-data gate,
  close_long, tighten_stop (up-only), halt (closes positions, blocks entries,
  records 'halted' skips), monitor stop-trigger via check_exits, and equity
  snapshot writing. Not covered: thread lifecycle (start/stop), collector
  failure modes mid-close, reconciliation on boot (SPEC 7.3 - NOT BUILT, see
  gaps).
- **Session calendar:** D1/D2 now gate on XNYS trading minutes with a
  weekday fallback if the calendar errors. Not covered by a dedicated test
  (would need a holiday fixture; noted as a gap).

## Known gaps left open (for Raven's queue)

1. SPEC 7.3 reconciliation on boot: engine/main.py starts threads directly;
   no reconciliation pass exists (paper mode makes this low-risk - the DB IS
   the exchange - but live mode must not launch without it).
2. T8 strategy sandbox (AST allowlist) not built; ordered after T9 by D-208.
3. Telegram alerts and Notion briefings (T13/T14): not started.
4. SPEC 5.3 acceptance bar still not encoded as a gate anywhere.
5. No dedicated test for holiday exclusion in `_in_xnys_session`.
6. Intraday quarantined data still awaits re-download (needs rotated Alpaca
   key); graveyard re-run therefore covers fewer intraday equity sets.
7. The engine has never been run end-to-end against live Binance.US public
   data (network calls); first supervised paper session should be
   short and watched.

## Verification snapshot at close of session

- tests/: 119 passed; backtest/test_known_answers.py: 6 passed (now real
  assertions). Total 125.
- validate_harness.py: exit 0, 21/21 including A5 cross-harness.
- Graveyard re-run: launched fresh (old results archived, D-207), running in
  background against the fixed harness + clean data; check
  `logs/graveyard_rerun.log` and `research/graveyard/v0_graveyard_full.json`.
