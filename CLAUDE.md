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
>    (no Aym relay). Verified working 2026-08-14:
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

**Last updated by:** Cody, 2026-08-14 (evidence pack pushed, key audit PASS)
**Project path:** ~/aym/projects/05-trading-bot/

## Current state in one paragraph

T1-T9 built. 559 tests passing, 1 skipped. `validate_harness.py` 21/21.
v0 verdict: 33 of 35 strategies zero gross edge. Graveyard sweep, chain, and
post-sweep repair all COMPLETE. FUTURES purge complete (D-261): 23,595 contract
rows dropped, graveyard rebuilt to 535,425 entries across 55 strategies. The
DURABLE judge pack, the regenerated summary.json, and a rewritten graveyard
README are now COMMITTED AND PUSHED to the public repo (5643bb0). The key audit
is DONE and came back clean (7a994e3). Nothing is running. No live trading;
paper and backtest only. Aym's directive: backtesting and getting judge up to
speed (D-264).

## What just happened (Aug 14, second session)

- Evidence pack committed and pushed. The public repo had been serving the old
  empty pack; it now serves the real one. `summary.json` regenerated and its
  numbers match the pack exactly (535,425 / 381 PASS / 155 distinct).
- `research/graveyard/README.md` rewritten. The old one declared everything
  PROVISIONAL as of 2026-08-12; no file in that directory still carries the
  flag. New version separates post-purge outputs from the ten that predate
  D-261 and are still stale.
- **Key audit PASS.** 1,327 tracked files plus all 1,320 blobs across all 5
  commits. Nothing exposed. Decisive check was exact-value search for the three
  live `.env` secrets and the Hermes webhook HMAC across every blob: zero hits.
  `.env` verified never committed by inspecting each commit tree, not by grep.
  Full writeup: `docs/handoffs/2026-08-14-key-audit.md`.

## CORRECTION to a standing belief (read this)

The previous version of this file said `duplicate_strategies` means "C2 is
identical to C5/D1/D2/S1/S2 across all 264 compared rows" and called it the
finding that most affects the distinct-finding count. **Both halves are wrong.**

C2 pairs with **all 54** other strategies at `identical_fraction` 1.0, and the
reason is in `trade_count_sanity`: C2 produces zero trades in all 264 rows it is
compared on, so every comparison is empty against empty. The other high-count
members of the duplicate list (`V2_vwap_magnet_sessionatr`,
`V5_capitulation_equity`, `V4_gap_hold_proxy`, `V4_trend_reclaim`,
`rising_three_methods`, `rsi_extreme`, `V3_intraday_momentum_crypto`,
`V5_forced_flow_crypto`) are the same eight strategies that top the zero-trade
list at 99%+.

`duplicate_strategies` and `trade_count_sanity` are ONE problem: **8 of 55
strategies do not fire.** Convention 3 failing out loud. They contribute no PASS
rows, so they are not inflating 155. The real cost is that 8 strategies were
never tested and are sitting in the graveyard looking like verdicts.

Raven has been asked whether D-226 needs the same correction. Per convention 15
this is a reasoning change, not a factual one, so it needs a decision or a
version bump, not an in-place edit. Do not silently fix DECISIONS.md.

## Aym's decisions (2026-08-14, DECISIONS.md v9)

- **D-261:** Purge confirmed and COMPLETE. 23,595 rows dropped, graveyard
  rebuilt to 535,425 entries.
- **D-262:** Alpaca keys already rotated by Aym; he will rotate again.
  .env lives at `~/aym/projects/05-trading-bot/.env` (gitignored, verified).
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
  `run_post_sweep_repair.sh`, `summarize_graveyard.py`
- `strategies/builtin/` - expanded.py (28), strategy_lab.py (7),
  strategy_lab_v2.py (9), v3 (6), v4 (3 ignitions), v5 (2 forced-flow) = 55
- `indicators/` - ATR, RSI, EMA, MACD/Stoch (ta-library facades)
- `agents/` - Quant SOUL.md (active, LLM), `judge.py` (active, pure Python, no
  LLM), Scout/Forge/Coach/Echo SOULs drafted but not active (split trigger is
  5+ live strategies; currently zero). `agents/README.md` has the org chart and
  the judge.py runbook.
- `docs/ROADMAP.md` (P0-P6), `docs/DECISIONS.md` (v9, D-101 through D-265)

## Where the judge pack numbers live

In `research/judge_evidence_pack.json`, these are NOT top-level keys:
- pass counts: `graveyard_summary.verdict_counts`
- 509,080 tests: `graveyard_summary.multiple_comparisons.tests_completed`
  (mirrored at `expected_best_by_chance.tests_completed`)
- 155 distinct findings: `distinct_findings.strategy_x_ticker_x_timeframe`
- the 4 failing assertions: `silent_assertions.failed`

`research/graveyard/summary.json` carries the same numbers in flatter form.

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
    finishes, correct the entry against the log. Correct in place, note it, do
    not bump the version. Factual corrections only (row counts, durations, entry
    counts). Changes in reasoning or conclusions require a new decision or a
    version bump.
16. **Never `git add -A` in this repo.** A 128MB untracked archive JSON is not
    covered by .gitignore and exceeds GitHub's 100MB hard limit. Stage by
    explicit path. See F2 below.

## Repo hygiene (from the Aug 14 audit, none of it leaking)

- **F1** `.gitignore` covers `.env` exactly, not `.env.local` /
  `.env.production`. Fix is `.env*` plus `!.env.example`. Not applied.
- **F2** `research/graveyard/archive/v0_graveyard_flatcost_partial_2026-08-13.json`
  is 128MB, untracked, NOT ignored (pattern only covers `v0_graveyard_full*`).
  Fix is `research/graveyard/archive/*.json`. Not applied.
- **F3** Telegram chat ID 8017725309 is public in this file and in
  `docs/handoffs/2026-08-13-cody-session-1.md`. Not a credential. Aym's call.
- **F4** `.env.example` is missing `FRED_API_KEY` and `ALPACA_ENDPOINT`
  placeholders. Completeness gap, not a leak.

## Open results question

The constraint sweep finished. Its own DIAGNOSTIC claims tightening the gate
"is selecting for something real." It is NOT supported (D-256): the effect is
non-monotonic (AGGRESSIVE -0.1793 beats BASE -0.4543 before CONSERVATIVE
+1.5380), and 78.5% of CONSERVATIVE's profit comes from two DCA variants on 282
trades. Recorded as underpowered, not disproven. Full read in
`docs/handoffs/sweep-results.md`. Note the sweep is also PRE-PURGE.

## What's next (priority order)

1. Read the five graveyard outputs together, as designed, now that the pack is
   real. This is the backtesting work D-264 actually asked for.
2. Fix the 8 non-firing strategies. This is the concrete version of "decide what
   to do about the failing assertions" and should clear two of the four at once.
3. Rebuild the ten pre-purge outputs in `research/graveyard/` (asset_class,
   pooled, toll_collector, dispersion_gate, inversions, vr_fingerprint,
   conditional_edge, constraint_sweep, assertions, dispersion_gate_smoke).
   They were built against the graveyard that still had the bad futures rows.
   The README now labels them; nobody should cite them until they are rebuilt.
4. Point Forge (not built - needs `.claude/agents/forge.md` per D-245) at the
   surviving v3/v4/v5 proposals once judge can evaluate what it writes.
5. Defer the 5-agent split until something survives the graveyard with real edge.

## Aym's owed items (not blocking)

- Rotate the Alpaca key again (D-262, he said he will). The audit found no
  exposure, so this is principle, not incident response.
- First supervised paper run + kill-switch drill (needs Aym present, deferred
  by D-264)
- Ratify D-217's 11 SOUL rules (D-244)
- Decide on F1/F2 (.gitignore) and F3 (chat ID)

## How to talk to Raven

Raven is on Telegram. For a decision or a flag, use the Hermes MCP:
`messages_send` with `target="telegram:8017725309"`.

For non-urgent things, write to DECISIONS.md or a handoff file. Raven reads
them between sessions.
