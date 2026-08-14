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

**Last updated by:** Cody (session 2, 2026-08-13, end of session)
**Project path:** ~/aym/projects/05-trading-bot/

## Current state in one paragraph

T1-T9 built. **559 tests passing, 1 skipped** (the "547" in the previous
version of this file never reconciled; the verified pre-session baseline was
543 - see the session-2 handoff).
`validate_harness.py` 21/21, status DURABLE. v0 verdict stands: 33 of 35
strategies have zero gross edge - the measurement apparatus is the asset,
strategies are fungible. Cost model, cross-sectional harness, contract sizing,
Labs v2-v5 and Judge-as-code are all built. **A graveyard sweep started 16:01
is still running and is executing stale code** - it predates the D-249 sizing
fix, so its FUTURES rows are contaminated and need a purge + rebuild that is
built but not yet run. No live trading. Paper/backtest only until Aym says so.

## What's running right now

**Check with `pgrep -f run_incremental_graveyard`, NOT `ps aux | grep python`** -
the interpreter is capital-P `Python` and the plain grep misses it. Session 2
wrongly concluded the sweep had died that way.

- `run_incremental_graveyard.py` (PID 63767, started 16:01) - was ~60% done
  (114/191 tickers, at PLTR) at 22:26. ETA was ~4h from there.
- `run_queued_chain.sh` (PID 69639) - polls every 300s, then runs: incremental
  pass (backfills v4/v5), dispersion gate, horizon ladder, PLR.

## The one thing to understand before using any sweep output

The running sweep imported its code at 16:01. `cost_model.py` (the D-249
contract-sizing fix) changed at 17:45, v4 at 16:19, v5 at 16:45. So the run in
flight has **49 strategies, not 54, and pre-fix futures sizing**. Confirmed by
`strategies_tested: 49` in the graveyard header (28+7+9+5).

Only FUTURES/OPTIONS are affected - `is_contract` is true for those alone -
which is 12,936 of ~288k rows. EQUITY/ETF/CRYPTO are fine. That is why the
sweep was left running rather than killed (D-253).

The fix shipped **without a `COST_MODEL_VERSION` bump**, so every row reads
`'2026-08-13'` and the "never pool across cost_model_version" rule cannot see
the contamination. `backtest/purge_stale_futures.py` is the remedy: drops all
contract rows so they rebuild under current code. Dry-run default; refuses to
run while the sweep is alive; backs up and writes atomically; 15 tests on the
destructive edges (D-259). **Not run yet** - it discards 51 PASS rows, and Aym
should see that named first (D-254).

The whole repair is one gated command once he confirms:

```bash
nohup bash backtest/run_post_sweep_repair.sh --confirm > logs/post_sweep_repair.log 2>&1 &
```

It waits out the sweep AND the chain, dry-runs the purge into the log, applies
it, rebuilds futures + backfills v4/v5 in one pass, then emits a judge pack
(D-260). **Never edit `run_queued_chain.sh` while it is running** - bash reads
a script by byte offset as it executes.

## What's built (files that exist and work)

- `engine/` - collector, scanner, executor, risk, adapters (paper+live), main
- `sandbox/` - AST allowlist validator, subprocess runner, hash pinning
- `backtest/` - vectorized + event + cross-sectional harnesses, cost model
  (4 venue regimes), `instruments.py` (contract sizing), assertions, pooled
  analysis, asset-class analysis, conditional edge, inversion, graveyard
  builder, toll collector, dispersion gate, **`purge_stale_futures.py` +
  `run_post_sweep_repair.sh` (new, tested, not armed)**
- `strategies/builtin/` - expanded.py (28), strategy_lab.py (7),
  strategy_lab_v2.py (9), v3 (5), v4 (3 ignitions), v5 (2 forced-flow) = 54
- `indicators/` - ATR, RSI, EMA, MACD/Stoch (ta-library facades)
- `agents/` - Quant SOUL.md (active, LLM), `judge.py` (active, pure Python, no
  LLM), Scout/Forge/Coach/Echo SOULs drafted but not active (split trigger is
  5+ live strategies; currently zero). `agents/README.md` has the org chart and
  the judge.py runbook.
- `docs/ROADMAP.md` (P0-P6), `docs/DECISIONS.md` (now at v8, D-257)

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
11. NOT_TESTED means "could not run," never "ran and found nothing." **This
    applies to the evidence layer too**: an unreadable graveyard is not an empty
    one (D-255, the judge.py bug).
12. A toll/cost RATE can legitimately be `inf` when an instrument cannot be
    afforded at the configured capital. Correct answer, not a bug to paper over.
13. **Edits during a long run do not reach it.** Python snapshots source at
    import. Before editing anything a running sweep imports, check whether a
    sweep is running and note what it will and will not have (D-253).
14. **Run python as `env -u PYTHONPATH python3` from agent-spawned sessions.**
    Hermes leaks its 3.11 venv onto PYTHONPATH; numpy then fails to import in a
    way that looks like a broken install. The machine is fine (D-257).

## Open results question

The constraint sweep finished. Its own DIAGNOSTIC claims tightening the gate
"is selecting for something real." **It is not supported** (D-256): the effect
is non-monotonic (AGGRESSIVE -0.1793 beats BASE -0.4543 before CONSERVATIVE
+1.5380), and 78.5% of CONSERVATIVE's profit comes from two DCA variants on 282
trades. Recorded as underpowered, not disproven. Full read in
`docs/handoffs/sweep-results.md`.

## What's next (priority order)

1. Wait for PID 63767 to exit, let the chain finish dispersion/horizon/PLR.
2. `purge_stale_futures.py` dry run -> `--apply` (confirm with Aym first).
3. `run_incremental_graveyard.py` - rebuilds futures AND backfills v4/v5 in one
   pass, under current code.
4. Re-run `agents/judge.py` for a real evidence pack.
   `research/judge_evidence_pack.json` on disk now is the **pre-fix empty pack**
   - ignore it, do not read it as a result.
5. Then read all five outputs together, as designed.
6. Point Forge (not built - needs `.claude/agents/forge.md` per D-245) at the
   surviving v3/v4/v5 proposals once judge can evaluate what it writes.
7. Defer the 5-agent split until something survives the graveyard with real edge.

## Aym's owed items (not blocking)

- Rotate Alpaca key (open since v1 audit)
- First supervised paper run + kill-switch drill (needs Aym present)
- Live Binance.US fee verification (D-236)
- Ratify D-217's 11 SOUL rules (D-244)
- Confirm the purge-all-contract-rows call (D-254) before `--apply` runs

## How to talk to Raven

Raven is on Telegram. For a decision or a flag, use the Hermes MCP:
`messages_send` with `target="telegram:8017725309"`.

For non-urgent things, write to DECISIONS.md or a handoff file. Raven reads
them between sessions.
