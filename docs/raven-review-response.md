# Raven Response to Claude's Review

**Date:** 2026-08-11
**Status:** Aym-approved, SPEC patching begins

## Accepted (no modification)

- F1: Strategy sandbox (AST allowlist + subprocess + hash-pinning)
- F2: Overfitting defenses (train/validation split + holdout + graveyard + pre-registration)
- F4: Sizing/fee contradiction (notional cap, max trades/day, fee-to-edge gate, benchmark)
- F5: Attribution fixes (empirical quantiles, cold-start, event-exclusion cap, random twin)
- F6: SOUL.md not a boundary (deterministic FACTS renderer)
- F7: Scale ceiling (capacity limits, depth-aware execution, off-exchange sweeps)
- F8: Git + hash on orders (code hash stamped on every order/fill row)
- F9: LLM viability (recalibrate expectations)
- M1: Tiered routing (Haiku daily, Opus research, Sonnet default)
- M3: Model-agnostic config (per-job model parameter, quarterly bake-off)

## Modified

- F3: Lifecycle gates. Claude said "one window in five" for PIP on healthy strategies. Math shows 3.5%, not 20%. Still significant enough to fix. Accepted: 50 shadow signals, CIs, regime tags, old-version twin. Rejected: dropping the PIP metaphor (it's for Aym's mental model, not the evaluation logic).
- F10: Product vision. Removed as design driver. Kept as career context. Don't optimize for the exit.

**Update (2026-08-12):** Aym pushed back on F10 framing. Correction: architect for the destination, build for today. The IP layers (org chart management pattern, governance protocol, self-improving loop) should be designed to be portable across domains from day one. Domain-specific code (Binance, candlesticks, execution) cleanly separable from management framework. This is not premature feature building. It's architecture that keeps options open. SPEC Section 14 updated with the IP layering and novelty analysis.
- M2: Cross-vendor reviewer. Deferred to v1.5.

## Rejected

- None fully rejected. F3's "PIP metaphor is wrong" is rejected but the statistical fixes are accepted.

## Aym decisions

- Time allocation: Aym decides project focus per session. No fixed split.
- Trade frequency: Determined by what makes the bot profitable. Fine-tuned before going live. Not locked in v1.
- Kraken vs Binance.US: Binance.US for v1. Revisit before live with significant capital.

## SPEC changes to implement

1. Strategy sandbox (before T9, was T7) — AST + subprocess + hash-pin
2. Overfitting defenses in backtest harness (T7, moved up from T13)
3. Lifecycle gate math fix (Section 9, T7)
4. Fixed notional cap + fee-to-edge gate + max trades/day (T5, Section 6.1) — UPDATED 2026-08-12: Aym ruling, fixed notional does NOT scale with balance
5. Buy-and-hold benchmark (Section 8, T7)
6. Attribution engine fixes (Section 9, T7)
7. Deterministic FACTS renderer (T13/T14)
8. Code hash on order/fill rows (T2 schema update)
9. Paper fill model fix: fill at ask/bid, not mid (T6)
10. Section 14 update with honest fee-bleed math + IP layering + novelty analysis
11. Section 2 update: future scope includes applying org chart pattern to non-trading domains
12. Model routing: Haiku daily, Opus research, Sonnet default (T14)
13. Build plan reordered: backtest harness moved to T7 (moment of truth before building execution layer)
14. Architecture: domain layer cleanly separable from management framework for future portability

## Round 2 Review (2026-08-12)

Claude reviewed the updated SPEC and found 19 items. All accepted, no modifications, no rejections.

**Critical fixes (my patching misses):**
- Attribution engine (Section 9.5) was entirely missing despite being accepted as F5. Now written: empirical quantiles, cold-start, event-exclusion cap, random twin, buy-and-hold benchmark, attribution report format.
- F3 restoration: 50 shadow signals (not 20), CIs, regime tags were accepted but never patched. Now restored.
- Section 6.5 fee-to-edge gate was referenced but didn't exist. Now written with threshold and formula.
- Buy-and-hold benchmark was accepted but absent. Now in backtest harness requirements, go-live criteria, and attribution report.
- Overfitting defenses were prose only, not harness requirements. Now mandatory: train/val split, holdout, walk-forward, pre-registration, random twin, stress probes.

**Bugs found:**
- Registry write-path: Quant had no legal way to flip candidate->shadow (read-only DB, no registry write access). Fixed with inbox pattern (Section 9.4).
- Genome risk layer included position sizing, which is engine-owned. Restricted to stop logic only.
- Phase-2 twins would get auto-retired by 60-day rule before v2 proves itself. Exempted.
- Cost table had stale fee math ($60-120/mo, should be $18/mo at fixed notional).

**Architecture improvements:**
- framework/ module added to project structure (lifecycle.py, evaluation.py, attribution.py, briefing.py, inbox.py). Domain code separable from management framework.
- Edit-ladder clock: iterations judged on backtest (minutes), not shadow (weeks).
- Graveyard storage: research/graveyard/ with JSON index, not in trading.db.
- Semantic-drift enforcement: strategy family in registry, sandbox validator rejects family changes.
- T7 as formal go/no-go checkpoint with explicit decision paths.
- Daily/weekly stops relabeled as ops backstops (vestigial under fixed notional).
- Stress probes: per-trade simulation mode for execution-delay probe.

**SPEC is now locked. No open items from either reviewer.**
