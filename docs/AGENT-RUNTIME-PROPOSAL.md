# Proposal: Standalone Agent Runtime (independent of Hermes and Raven)

**Date:** 2026-08-13
**Author:** Claude Code
**Status:** PROPOSAL - needs Aym's decision on three items at the bottom
**Question answered:** "Can we build the bot's agents outside of Hermes and
Raven? Is that even possible?"

## Short answer

Yes. Nothing in the code depends on Hermes or Raven. The dependency is
governance, not architecture, and governance is Aym's to assign. The bot's
agents can run as a self-contained loop on this machine tonight, because the
things that actually keep the system safe were never the AI leaders - they
are deterministic code that already exists and already passes tests.

## Why independence is technically trivial

SPEC 11.5 already states the design principle: a SOUL.md is a behavior
prior, not a boundary. The boundaries are code:

| Guardrail | Where it lives | Status |
|---|---|---|
| Paper-only, refuses live mode | engine/main.py hard check + env ack | BUILT |
| Strategy code safety | sandbox/ AST allowlist, subprocess conformance, hash pinning | BUILT |
| Lifecycle gates | registry inbox (SPEC 9.4): engine validates every status change against the gate before applying | BUILT |
| Result trust | validate_harness.py exit 0, silent assertions, cost-model version fencing | BUILT |
| Risk caps | engine/risk.py, kill switch via botctl | BUILT |
| Live promotion | Aym only, one line: botctl promote (SPEC 9.1) | BUILT |

An agent - any agent, from any vendor, run any way - cannot skip these.
Forge's output goes through the sandbox or it does not run. A status change
passes the gate or the engine rejects it. Nothing goes live without Aym's
command. That is what makes "who directs the agents" a preference rather
than a safety question.

## What Hermes and Raven actually provide today

1. **Raven:** strategy/roadmap review, and a pending ruling on D-217 (11
   SOUL rules that exceed SPEC 5.7).
2. **Hermes:** direction of Claude Code sessions, spec ownership, memory.

Neither is in the loop at runtime. Neither gates a backtest, a shadow
signal, or a promotion. Removing them from the AGENT loop removes review
cadence, not enforcement.

## The proposal: code where possible, model where necessary

Do not build five LLM agents. Two of the five roles are already mostly
deterministic code, and code does not drift, hallucinate, or bill tokens.

| Role | Build as | Why |
|---|---|---|
| **Judge** | Pure Python (exists ~80%) | Judge is validate_harness + assertions + gates + summarize_graveyard + twin comparisons. SPEC says Judge "has no opinions" - that is a program, not a personality. Wrap the existing modules in one `judge.py` that emits an evidence pack JSON. |
| **Echo** | Python + small model call | Numbers, tables, and alerts are code (report.py, Telegram sender). One Haiku/Sonnet call turns an evidence pack into briefing prose. |
| **Forge** | LLM agent (Sonnet/Opus) | Genuinely creative: writes strategy modules from diagnosis or briefs. Output lands in the sandbox inbox, nowhere else. Must log hypotheses_generated / hypotheses_screened (the multiple-comparisons hole from harness-validation-review section 6). |
| **Scout** | LLM agent, scheduled | Reads market data summaries + literature, writes graded research briefs. Cheapest to run weekly, not daily. |
| **Coach** | LLM recommendation only | Reads Judge's evidence packs, writes promote/demote/PIP recommendations to the inbox. The ENGINE applies demotions automatically (safety), and only AYM applies promotions. Coach never needs write access to anything but its own recommendation file. |

### Runtime options, in order of standing-up cost

**Option 1 - Claude Code subagents + schedule (fastest, near-zero new code).**
The SOUL.md files become `.claude/agents/forge.md` etc. Each scheduled run
is a headless invocation on a cron (the existing schedule/cron tooling does
this). Pros: SOULs already written, zero new infrastructure, full tool
access to the repo. Cons: tied to this machine and to a Claude Code
subscription session; concurrency is limited.

**Option 2 - Claude Agent SDK runner (cleanest long-term).**
A small `agents/runtime.py` using the Agent SDK: each agent = SOUL.md as
system prompt + a narrow tool belt (run_backtest, query_graveyard,
write_inbox, read_briefs). Runs from launchd on a schedule, logs every tool
call to the audit trail. Pros: fully standalone, per-agent model choice
(Haiku for Echo, Sonnet for Forge), testable like any Python program, no
dependence on any interactive session. Cons: ~1-2 build sessions of work.

**Option 3 - hybrid (recommended).**
Judge and Echo as pure code NOW (they are nearly done already). Forge and
Scout via Option 1 subagents NOW, migrating to the Option 2 SDK runner when
the loop proves out. Coach last - it only matters once something survives
the graveyard.

### The sequencing honesty

SPEC 5.7's own trigger says the 5-agent split happens at 5+ live
strategies. The library currently has ZERO strategies with any edge - the
graveyard's verdict. So the near-term runtime is really:

1. **Judge-as-code** (useful immediately: it re-runs the P0.3 control and
   every future experiment without a human in the loop)
2. **Forge pointed at the lab docs** (v5's surviving proposals give it
   pre-registered hypotheses to implement, which is exactly the discipline
   its SOUL demands)
3. Scout/Coach/Echo activate when there is something to scout for, coach,
   and report on.

The agents' job is to work the measurement apparatus. The apparatus is
built. What is missing is a survivor for them to manage - and the honest
version of this proposal says the agent runtime and the search for a
survivor can proceed in parallel, but the runtime earns nothing until the
search finds something.

## Governance: what changes and what Aym must decide

Technically possible: yes, fully. What it costs is review structure, and
three decisions replace it:

1. **D-217 ratification.** 11 SOUL rules exceed SPEC 5.7 and are queued for
   Raven. Options: (a) Aym reads and ratifies them directly (they are
   conservative - mostly extra honesty requirements), (b) run agents under
   SPEC-5.7 rules only until ratified, (c) keep waiting for Raven. The
   twin-methodology conflict named in D-217 is already resolved in code
   (percentile gate), so (b) is safe.
2. **Hierarchy carve-out.** The global stack rules make Hermes leader and
   Raven the strategy reviewer. If this project's agents run standalone,
   Aym should state the carve-out explicitly (one line in this repo's
   docs: "trading-bot agents run standalone; Raven audits monthly, not
   per-decision") so the two rule systems do not silently conflict.
3. **Audit cadence without Raven.** Replace per-decision review with: every
   agent action lands in the existing audit log + a weekly Echo digest that
   Aym (and optionally Raven) reads. Drift checks are already written into
   each SOUL; schedule them monthly as a cron.

## What I would build first (concrete, ~1 session)

1. `agents/judge.py` - wraps validate_harness + assertions + pooled/asset
   analysis + gate checks into one evidence-pack emitter. Pure code, tests.
2. `.claude/agents/forge.md` from forge/SOUL.md, tool-restricted, writing
   only to the sandbox inbox.
3. A cron entry: nightly Judge run over any new graveyard/experiment
   output; weekly Forge run over open lab proposals.
4. One-page runbook in agents/README.md: how to start, stop, and audit the
   loop.

Nothing in this plan touches live trading, the engine, or the promotion
gate. The worst an escaped agent can do is write a strategy file that the
sandbox rejects or a recommendation file the engine ignores.
