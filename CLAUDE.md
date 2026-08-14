# Trading Bot Project - Cody Wake-Up File

> You are Cody (Claude Code). Raven (Hermes) is your collaborator.
> Aym is the decision maker. This file is your wake-up briefing.
> Read this FIRST at the start of every session. It replaces re-reading
> the whole project. If this file conflicts with DECISIONS.md, DECISIONS.md wins.
>
> **SESSION EPILOGUE RULE:** At the end of every session, you MUST:
> 1. Write the handoff note to docs/handoffs/ (as always).
> 2. Rewrite this file to reflect the current state. Read the current version,
>    read what you did this session, write a new version. Keep it under 3 pages.
>    Do not append. Rewrite. Preserve: current state, what's built, what's
>    running, what's next, open decisions, conventions. Drop: completed items
>    that are in DECISIONS.md already, stale running statuses, anything
>    superseded. The goal is that a fresh session reads this one file and is
>    immediately up to speed.
> 3. POST the handoff to the Hermes webhook so Raven reviews it automatically
>    (no Aym relay). Verified working 2026-08-13:
>
> ```bash
> SECRET=$(cat ~/.hermes/.webhook-cody-secret)
> BODY="{\"project\":\"05-trading-bot\",\"file\":\"docs/handoffs/<filename>\",\"summary\":\"<one line>\"}"
> SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | cut -d' ' -f2)
> curl -s -X POST http://127.0.0.1:8644/webhooks/cody-handoff \
>   -H "Content-Type: application/json" \
>   -H "X-Hub-Signature-256: sha256=$SIG" \
>   -d "$BODY"
> ```
>
> On webhook failure (connection refused / non-2xx): fall back to
> `mcp__hermes__messages_send` to telegram:8017725309. A 30-min Hermes cron
> sweep (marker: ~/.hermes/.cody-handoff-marker) backstops a crashed session
> that wrote the file but never POSTed - so the FILE is the durable artifact;
> write it even if everything else is on fire.

**Last updated by:** Cody, 2026-08-14 (post-purge state, README and docs committed)
**Project path:** ~/aym/projects/05-trading-bot/

## Current state in one paragraph

T1-T9 built. 559 tests passing, 1 skipped. `validate_harness.py` 21/21.
v0 verdict: 33 of 35 strategies zero gross edge. The graveyard sweep, the chain,
and the post-sweep repair are all COMPLETE (finished 13:45, Aug 14). FUTURES
purge complete (D-261): 23,595 contract rows dropped, futures rebuilt under the
fixed cost model, graveyard back to 535,425 entries across 55 strategies. The
judge evidence pack is DURABLE and the one on disk now is a real result, not the
old empty pack. Nothing is running. No live trading; paper and backtest only.
Aym's directive: backtesting and getting judge up to speed (D-264).

## What just happened (Aug 14)

- Post-sweep repair COMPLETE at 13:45. Purge, rebuild, judge, all exit 0.
  Log: `logs/post_sweep_repair.log`. The pre-purge graveyard is backed up in
  `research/graveyard/archive/`.
- Judge pack emitted: 535,425 entries, 55 strategies, 381 PASS,
  52 PASS_BENCHMARK, 155 distinct findings, status DURABLE, `degraded` null.
  Where those live in `research/judge_evidence_pack.json` (they are NOT
  top-level keys): pass counts at `graveyard_summary.verdict_counts`,
  509,080 tests at `graveyard_summary.multiple_comparisons.tests_completed`
  (mirrored at `expected_best_by_chance.tests_completed`), 155 at
  `distinct_findings.strategy_x_ticker_x_timeframe`.
- 4 of the 8 silent assertions FAIL on the current graveyard: quarantine_canary
  (MULN/SNDL rows present), trade_count_sanity, duplicate_strategies (C2 is
  identical to C5/D1/D2/S1/S2 across all 264 compared rows), timeframe_coherence.
  Known from D-226, not new, but not fixed either. Do not describe the pack as
  clean. DURABLE is a statement about harness validation only.
- README.md fully rewritten and COMMITTED (574c5d4). Handoff:
  `docs/handoffs/2026-08-14-readme-rewrite.md`.
- D-261's row count corrected in DECISIONS.md (said 12,936, actual 23,595).
  Factual correction, no version bump. Raven ruled the four remaining 12,936
  references in D-254/D-259 stay as written: they are the historical record of
  what was believed at decision time, and rewriting them would falsify it.
- The repo is PUBLIC on github.com/AymAbdalla/05-trading-bot. Docs are committed
  and pushed. The graveyard JSON files (`research/judge_evidence_pack.json`,
  `research/graveyard/harness_validation.json`) are modified but deliberately
  UNCOMMITTED - Raven wants to review the diff before they go public.

## Aym's decisions (2026-08-14, DECISIONS.md v9)

- **D-261:** Purge confirmed and COMPLETE. 23,595 rows dropped, graveyard
  rebuilt to 535,425 entries.
- **D-262:** Alpaca keys already rotated by Aym; he will rotate again.
  .env lives at `~/aym/projects/05-trading-bot/.env` (gitignored).
  Keys: `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `ALPACA_ENDPOINT`. D-110 closed.
- **D-263:** Binance.US live fee verification RETIRED as a checklist item.
- **D-264:** Paper run + kill-switch drill deferred. Focus: backtesting + judge.
- **D-265:** `references/broker-fee-reference-2026.md` is the single source of
  truth for costs.

## What's built (files that exist and work)

- `engine/` - collector, scanner, executor, risk, adapters (paper+live), main
- `sandbox/` - AST allowlist validator, subprocess runner, hash pinning
- `backtest/` - vectorized + event + cross-sectional harnesses, cost model
  (4 venue regimes), `instruments.py` (contract sizing), assertions, pooled
  analysis, asset-class analysis, conditional edge, inversion, graveyard
  builder, toll collector, dispersion gate, `purge_stale_futures.py` +
  `run_post_sweep_repair.sh` (both used in anger Aug 14, no longer untried)
- `strategies/builtin/` - expanded.py (28), strategy_lab.py (7),
  strategy_lab_v2.py (9), v3 (6), v4 (3 ignitions), v5 (2 forced-flow) = 55
- `indicators/` - ATR, RSI, EMA, MACD/Stoch (ta-library facades)
- `agents/` - Quant SOUL.md (active, LLM), `judge.py` (active, pure Python, no
  LLM), Scout/Forge/Coach/Echo SOULs drafted but not active (split trigger is
  5+ live strategies; currently zero). `agents/README.md` has the org chart and
  the judge.py runbook.
- `docs/ROADMAP.md` (P0-P6), `docs/DECISIONS.md` (v9, D-101 through D-265)

## Key conventions (learned the hard way, follow these)

1. No result is durable unless `validate_harness.py` exits 0.
2. Cite `distinct_findings` from summary.json, never raw pass counts.
3. Verify a strategy FIRES on real data before interpreting results.
4. Conditions must be predicted before testing, never discovered by scanning.
5. Estimate gross edge in bps before writing code; under 30bps = dead on arrival.
6. Every proposal states a kill condition.
7. A FAIL on a 200k-trade strategy is a verdict; a FAIL on 1,700 trades is a
   shrug. This cuts both ways - a PASS on 87 trades is also a shrug (D-256).
8. Every entry needs stop strictly below entry. Harness rejects inverted stops.
9. Write a handoff note to docs/handoffs/ after every build session. Not optional.
10. Write decisions to DECISIONS.md with a D-number, who decided, why, where.
11. NOT_TESTED means "could not run," never "ran and found nothing." This
    applies to the evidence layer too: an unreadable graveyard is not an empty
    one (D-255, the judge.py bug).
12. A toll/cost RATE can legitimately be `inf` when an instrument cannot be
    afforded at the configured capital. Correct answer, not a bug to paper over.
13. Edits during a long run do not reach it. Python snapshots source at import.
    Before editing anything a running sweep imports, check whether a sweep is
    running and note what it will and will not have (D-253).
14. Run python as `env -u PYTHONPATH python3` from agent-spawned sessions.
    Hermes leaks its 3.11 venv onto PYTHONPATH; numpy then fails to import in a
    way that looks like a broken install. The machine is fine (D-257).
15. A number written into a decision BEFORE the run is an estimate. When the run
    finishes, correct the entry against the log (D-261 said 12,936, actual
    23,595). Correct in place, note it, do not bump the version. This applies to
    factual corrections only (row counts, durations, entry counts). It does not
    apply to changes in reasoning or conclusions, which require a new decision or
    a version bump.

## Open results question

The constraint sweep finished. Its own DIAGNOSTIC claims tightening the gate
"is selecting for something real." It is NOT supported (D-256): the effect is
non-monotonic (AGGRESSIVE -0.1793 beats BASE -0.4543 before CONSERVATIVE
+1.5380), and 78.5% of CONSERVATIVE's profit comes from two DCA variants on 282
trades. Recorded as underpowered, not disproven. Full read in
`docs/handoffs/sweep-results.md`.

## What's next (priority order)

1. Read the five graveyard outputs together, as designed, now that the pack is
   real. This is the backtesting work D-264 actually asked for.
2. A real key audit, not a scan. The earlier pass was a scan. Do not treat the
   repo as cleared. BLOCKED: do not start until Aym authorizes it. Raven is
   surfacing it to him.
3. The graveyard JSON diffs need Raven's review before they can be committed.
4. Decide what to do about the 4 failing silent assertions. The duplicate
   strategies finding (C2 == C5/D1/D2/S1/S2) is the one that most affects how
   many distinct findings the pack really has.
5. Point Forge (not built - needs `.claude/agents/forge.md` per D-245) at the
   surviving v3/v4/v5 proposals once judge can evaluate what it writes.
6. Defer the 5-agent split until something survives the graveyard with real edge.

## Aym's owed items (not blocking)

- Rotate the Alpaca key again (D-262, he said he will)
- First supervised paper run + kill-switch drill (needs Aym present, deferred
  by D-264)
- Ratify D-217's 11 SOUL rules (D-244)
- Authorize the key audit (repo is already public; the audit is still owed)

## How to talk to Raven

Raven is on Telegram. For a decision or a flag, use the Hermes MCP:
`messages_send` with `target="telegram:8017725309"`.

For non-urgent things, write to DECISIONS.md or a handoff file. Raven reads
them between sessions.
