# Review Brief for Claude (Round 2)

**Date:** 2026-08-12
**From:** Raven (AI Stack Leader)
**To:** Claude (Reviewer)
**Subject:** SPEC.md updated since your 2026-08-11 review. Please review the changes and flag anything broken, missing, or contradictory.

---

## What happened since your last review

Aym and I had a design session that produced significant changes to the SPEC. Your original 10 findings and 3 model recommendations were all accepted (with modifications to F3 and F10). Those changes are now patched into the SPEC. But we also went further on several fronts.

## What changed (focus your review here)

### 1. Fixed notional cap (Section 6.1, replaces your F4 sizing model)

Your F4 recommended a notional cap. Aym went further: the cap is FIXED at $100 per trade and does NOT scale with balance. At $2k it's 5% risk. At $100k it's 0.1%. No compounding. No scale ceiling (defers your F7 entirely to V4+). The fee-to-edge gate and max trades/day from your R6 are still in.

**Review question:** Does fixed-notional interact badly with anything in the risk model? The daily loss shutdown (15% of equity) and weekly stop (25%) still reference equity percentages, not notional. Is that consistent?

### 2. EvoQuant techniques integrated (Sections 5.3-5.6)

We researched EvoQuant (arXiv 2607.12455, self-evolving verifier-guided strategy optimization) and stole 5 techniques:
- Strategy genome with typed layers (signal, entry, risk, exit)
- Diagnosis before generation (identify bottleneck first, then propose targeted fix)
- Hierarchical edit ladder (parameter tuning, local repair, redesign, family migration)
- Queryable rejection knowledge base (graveyard is searchable, not just a list)
- Stress probes in backtest (fee doubling, slippage doubling, execution delay, parameter jitter)

**Review question:** Are these techniques compatible with the strategy sandbox (your F1/R1)? The genome requires parsing strategy code into layers. The sandbox uses AST allowlist + subprocess isolation. Do they conflict?

### 3. Multi-agent org chart (Section 5.7)

The full system is designed now (5 agents: Researcher, Recruiter, Evaluator, Manager, Reporter). V1 ships with a single Quant agent. The split happens at 5+ live strategies. Build timing is a phase decision, but the architecture supports the full system from day one.

Key principle: the agent that writes strategies (Recruiter) is NOT the agent that evaluates them (Evaluator).

**Review question:** Is there anything in the V1 architecture that would block the multi-agent split later? Specifically, does the current Quant profile design (read-only DB, restricted filesystem, no exchange keys) work if we split it into 5 profiles?

### 4. 4-phase version lifecycle (Section 9.2)

When Quant writes v2 of a live strategy:
- Phase 1: v1 stays live, v2 shadows (2+ weeks, 20+ signals)
- Phase 2: v2 promotes to live, v1 demotes to shadow (not retired)
- Phase 3: if v2 underperforms, v1 is still in shadow for immediate rollback
- Phase 4: if v2 proves itself over 30+ live trades, v1 retires to graveyard

**Review question:** Does this interact correctly with the lifecycle gates from your F3 (50 shadow signals, CIs, regime tags)? The 20-signal minimum in Phase 1 is for the v1-vs-v2 comparison, which is separate from the 50-signal minimum for candidate-to-shadow promotion. Are these two thresholds confusing or contradictory?

### 5. IP layering and novelty analysis (Section 14)

The trading bot is the vehicle. The portable IP is 3 layers: (1) self-improving strategy loop, (2) org chart management pattern, (3) governance protocol (FACTS/ANALYSIS/RECOMMENDATIONS). Architecture separates domain code from management framework so the pattern can be applied to GTM outreach or other domains.

We also documented what EvoQuant and TradingAgents do vs what we do, and what we stole from each.

**Review question:** Does the IP layering claim hold up? Is there anything in the current SPEC that contradicts the "domain code is separable" principle?

### 6. Build plan reordered (Section 15)

Backtest harness moved from T11 to T7. Rationale: if patterns don't profit after fees on real data, the Quant agent is iterating on garbage. Better to know at T7 than T13.

New order: T1-T15 (was T1-T14). Strategy sandbox is now T8 (before execution at T9).

### 7. F10 correction

You said "remove product vision as design driver." Aym pushed back. Correction: architect for the destination, build for today. No premature features, but no throwaway architecture either. The management framework is designed to be portable from day one.

### 8. Bull/bear promotion cases (from TradingAgents)

Promotion briefings now include both bull and bear cases. One agent (Reporter in V2, Quant in V1) writes both from the same evidence pack. No separate debate agents for V1.

---

## What did NOT change (no need to re-review)

- Exchange: Binance.US, same fee math, same pairs
- Data layer: ccxt, REST polling, same design
- Signal layer: 7 candlestick patterns, same confirmation stack
- Risk model: daily loss shutdown, circuit breakers, kill switch (all same)
- SQLite schema: same tables (code_hash column still needs to be added per F8, that's a T2 code change not a SPEC change)
- Notion journal: same databases, same cron schedule
- Go-live criteria: same 5 requirements

---

## What to send Claude

Send Claude:
1. This review brief
2. The SPEC.md (full file, it's been significantly updated)
3. The raven-review-response.md (so Claude can see what was accepted/modified/rejected from their original review)

Do NOT send QUANT-FULL-DESIGN.md. It's now stale and would create confusion. The SPEC is the source of truth.

---

## What I need from Claude

1. Flag anything broken, contradictory, or missing in the new sections
2. Answer the 5 review questions above
3. Flag anything that would block the multi-agent split later
4. Confirm the build order makes sense (especially T7 backtest before T9 execution)
5. Any new findings or concerns introduced by the EvoQuant techniques

Keep it concise. The original review was thorough. This round is about the deltas.
