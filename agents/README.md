# agents/

SOUL.md personality files for the trading bot's agents. Each SOUL.md is a system prompt: identity, convictions, uncertainty handling, pushback patterns, hard stops, context behavior, boundaries, drift checks. Per SPEC 11.5, a SOUL.md is a behavior prior, not a hard boundary. The deterministic FACTS renderer (SPEC 9.5), the registry inbox (SPEC 9.4), the sandbox validator, and read-only DB access are what actually enforce the rules. The SOUL governs how an agent interprets and communicates.

## Active now

**Quant only** for the LLM-driven role, plus **`judge.py`** as pure code.

- `quant/SOUL.md` (ACTIVE) - skeptical analyst. Diagnosis, research, strategy authoring, backtesting, briefings, all in one agent.
- `judge.py` (ACTIVE, D-247) - not a SOUL, a Python module. Per `docs/AGENT-RUNTIME-PROPOSAL.md`, Judge is "~80% deterministic already": no LLM in the loop, just `validate_harness.py` + `assertions.py` + `pooled_analysis.py` + `asset_class_analysis.py` + `summarize_graveyard.py` composed into one evidence-pack JSON. See "Runbook: judge.py" below.

## Designed for later

Per SPEC 5.7, Quant splits into the 5-agent org chart **when the strategy library reaches 5+ live strategies**. Not before. These files exist now so the architecture supports the split from day one, but none of them is loaded yet.

| Agent | Role | One line | File |
|---|---|---|---|
| Scout | Researcher | Watches the market and the literature, writes graded research briefs on strategy families and regimes worth exploring | `scout/SOUL.md` |
| Forge | Recruiter | Authors strategies from diagnosis, report patterns, or Scout briefs. Writes the Python modules and runs the backtests | `forge/SOUL.md` |
| Judge | Evaluator | Runs backtests and stress probes, computes metrics, produces evidence packs and old-version twin comparisons. No opinions | `judge/SOUL.md` |
| Coach | Manager | Turns Judge's numbers into promote, demote, retire, or PIP recommendations. Manages the strategy org chart | `coach/SOUL.md` |
| Echo | Reporter | Notion journal, daily/weekly/monthly briefings, emergency Telegram, bull and bear cases for promotions | `echo/SOUL.md` |

## The separation principle

The agent that writes strategies is not the agent that evaluates them, and the agent that evaluates is not the agent that decides. Forge authors, Judge measures, Coach decides, Aym approves anything live. This is the whole reason for the split, and every SOUL file encodes its own side of it.

The known hole in that design, from `references/harness-validation-review.md` section 6: Judge corrects for multiple comparisons on what Forge submits, and Forge decides what to submit. Forge's SOUL requires the full variant log plus `hypotheses_generated` and `hypotheses_screened`, and Judge's SOUL requires correcting on generated, never on submitted.

## Rules every agent carries

- Never trade. Never hold exchange credentials.
- Never modify `config.yaml`, `risk.py`, anything under `execution/`, the mode flag, or API keys.
- Database opens read-only. No agent writes to `strategy_registry` or `registry.json`; status changes go through request files in `strategies/requests/` and the engine validates them (SPEC 9.4).
- Nothing is durable until `validate_harness.py` exits 0 (D-102). Output produced while validation is red is labeled PROVISIONAL everywhere it appears.
- NOT_TESTED is a real verdict (D-109). A strategy that could not run is never recorded as tested and failed.
- Inverted variants need gross PF below 1.0 computed separately from net, an adequate sample, and out-of-sample confirmation, and they carry the original's hypothesis count. Inversion is a sign flip on the same hypothesis, not a new one.
- No bulk imports of strategy libraries (D-203). Reading library documentation as research input is allowed.

## Related SPEC sections

- 5.3 research loop, 5.4 genome, 5.5 edit ladder, 5.6 graveyard and inversion
- 5.7 the org chart and the split trigger
- 9.1 lifecycle, 9.2 old-version twin, 9.4 registry inbox, 9.5 attribution
- 11.4 filesystem scope, 11.5 system prompt clauses, 11.6 the SOUL framework table

## Governance (D-243/244/245)

Trading-bot agents run standalone from Hermes/Raven at runtime - the safety
boundary is the deterministic machinery above (sandbox, registry inbox,
`validate_harness.py`, kill switch, Aym-only promotion), not AI review. Raven
audits monthly, not per-decision. D-217's 11 SOUL rules run under SPEC-5.7
only until Aym ratifies them directly. Near-term runtime shape is
Hermes-cron-triggers-a-scoped-subagent (`docs/AGENT-RUNTIME-PROPOSAL.md`
Option 1, refined); a standalone Agent SDK runner (Option 2) stays the
long-term target if volume or reliability ever demands it. Full reasoning:
`docs/AGENT-RUNTIME-PROPOSAL.md`, `docs/agent-proposal-reconciliation.md`.

## Runbook: judge.py

The only piece of the agent runtime that is both built and active. No
scheduler, cron, or Hermes wiring exists for it yet - this is a manual CLI
today; automating its cadence is the natural next step once there's a
reason to run it more than ad hoc (a fresh graveyard sweep landing, mainly).

**Start (run it):**
```
python3 agents/judge.py --graveyard research/graveyard/v0_graveyard_full.json \
    --out research/judge_evidence_pack.json
```
Optional `--strategy NAME` filters the pack to one strategy. It prints a
one-line summary (`status`, `entries`, `strategies`) and writes the full pack
to `--out`. Safe to run at any time, including while a graveyard sweep is
still writing to the same file - it reads once at invocation and never
locks or blocks the sweep (read-only, per its own SOUL rule).

**Stop:** nothing to stop. It is not a daemon or a loop; it runs once and
exits. If invoked accidentally against a huge graveyard file mid-write, killing
it (`Ctrl-C` or `kill`) is always safe - it has not written its output yet at
that point, so nothing downstream can pick up a partial pack.

**Audit:**
- Check `pack['status']`: `PROVISIONAL` means `validate_harness.py` was red
  when the pack was built - do not treat anything in it as durable (D-102).
  This is stamped on every per-strategy row too, not just the top level.
- Check `pack['silent_assertions']['results']` for the same
  quarantine-canary / mirror-pair / trade-count-sanity / version-uniformity
  checks `assertions.py` already runs standalone - judge.py does not add new
  checks, it surfaces the existing ones in one place.
- Never read `pack['strategies'][i]['observed_best_pf']` alone - pair it
  with `pack['expected_best_by_chance']` (grid size, expected-best-under-null)
  before treating a good number as a discovery (standing rule 2, D-226).
- A `NOT_TESTED` row is not a failure (D-109) - `verdict` will read
  `NOT_TESTED` with `not_tested_reason` populated, never folded into `FAIL`.

**What it never does:** write to `strategy_registry`, `registry.json`, any
file under `strategies/requests/`, or open the DB in write mode. Its only
file I/O is reading the graveyard path it's given and writing the `--out`
path it's given.
