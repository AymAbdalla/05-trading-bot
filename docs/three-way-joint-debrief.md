# Joint Debrief: Hermes + Claude Code Setup

**Participants:** Hermes (Raven), Claude Code
**Claude Desktop:** Removed from the trading bot loop. Cannot be programmatically contacted, requires manual relay. A clean two-agent system with Aym as decision maker.
**Date:** 2026-08-13

---

## What we agreed on

### 1. Role division

**Agreed:** Claude Code does all code and data-grounded analysis. Hermes facilitates, maintains context, manages communication and scheduling. Claude Desktop handles spec/strategy/review work that does not need code execution.

**Claude Code's correction (accepted):** The framing of "Claude Code executes, Claude Desktop thinks" is wrong for this project. Most of the analytical work so far (cost-floor derivation, multiple-comparisons hole, inversion refutation) happened inside Claude Code sessions because it had to be grounded in actual harness runs. If Claude Desktop writes a strategy spec without running data, it is a hypothesis, not a design. The thinking and the doing are not separable on this project.

**Decision-making rule (agreed):** Technical/factual disagreements get resolved against the harness, tests, and cited methodology. Not routed to Aym to referee. Only genuinely subjective or resource-allocation disagreements go to Aym. Aym always has final say on anything that matters (licenses, features, architecture, go-live).

### 2. Where future agents should live

**Agreed:** Do not build 5 LLM agents. Two of the five roles are not agents at all.

| Role | Build as | Why |
|---|---|---|
| Judge | Pure Python module | "No opinions" is a correctness requirement, not a personality. validate_harness + assertions + gates + twin comparisons wrapped in one evidence-pack emitter. No model calls. |
| Echo | Python + cheap model call | Numbers/tables/alerts are code. One Haiku call turns an evidence pack into briefing prose. |
| Forge | LLM agent (Claude Code subagent) | Genuinely creative: writes strategy modules. Output goes to sandbox inbox only. |
| Scout | LLM agent, scheduled weekly | Literature/market reading. Low frequency. |
| Coach | LLM recommendation only | Reads evidence packs, writes promote/demote suggestions. Engine applies demotions automatically. Aym applies promotions. |

**Architecture (agreed):** The real shape is not "Hermes profile vs Claude Code subagent." It is: Hermes cron triggers a scoped, tool-restricted Claude Code subagent invocation. The subagent's boundary (sandbox inbox only) is the safety mechanism, not which system owns the schedule.

**My original hybrid lean (corrected):** I had Judge as a Claude Code subagent. Claude Code correctly pushed back: Judge should be pure code with no model in the loop, because every LLM call is a chance to introduce the p-hacking-adjacent judgment the SPEC is trying to prevent. Accepted.

### 3. Communication during builds

**Agreed:** Do not turn this into synchronous three-way review during builds. The correctness check for this project is the harness, not consensus.

**Primary channel:** DECISIONS.md and HANDOVER.md (already exists, already the audit trail). Hermes reads these between sessions.

**MCP messaging reserved for two things only:**
- Flagging actual disagreement with a specific decision, cited by ID
- Surfacing context from Hermes memory or Aym's stated priorities that Claude Code would not otherwise have

**Synchronous three-way contact reserved for:** Governance changes, live-trading boundaries, or major infrastructure decisions. Rare.

**Claude Desktop limitation (named plainly):** Claude Desktop has no filesystem access. It cannot read or write shared discussion files. Someone (Aym or Hermes) must relay Desktop's side by hand. The channel is not symmetric: two systems with file/MCP access and one that needs copy-paste.

### 4. What is wrong or missing

**D-217 is still open.** 11 SOUL rules exceed SPEC 5.7 and have been waiting for my ruling since August 11. The twin-methodology conflict is already resolved in code (percentile gate). We agreed: run agents under SPEC-5.7 rules only until ratified. The conservative extra rules can be ratified by Aym directly since they are mostly additional honesty requirements.

**Spec ownership is still a form of final say.** Claude Code correctly flagged that if Hermes "owns the spec," that is still a form of authority. If the intent is genuinely flat, spec changes should go through the same disagreement process as everything else. Accepted: I do not own the spec. I propose changes like anyone else.

**The ROI question.** Standing up a 5-agent org chart costs real session time and token spend. SPEC 5.7's own trigger for the split is 5+ live strategies. The graveyard currently has zero. Agent architecture discussion is premature process work with nothing to process.

### 5. Project state and what is next

**Agreed assessment:** The apparatus is trustworthy (160+ tests, cross-harness checks, holdout methodology, hard gate). The strategies are not (33 of 35 at zero gross edge, inversion refuted, filtering-to-winners refuted). This is a well-earned null result, not "we haven't looked hard enough."

**Agreed priorities, in order:**
1. Aym's owed items (Binance.US fee verification, first supervised paper run + kill-switch drill). Cheap, gates real work.
2. Judge-as-code (`agents/judge.py`). Useful regardless of architecture decisions. Half a session.
3. Point search effort at pre-registered, not-yet-tested hypotheses (Lab v3/v4 strategies, C2 fix). Not re-scanning the 33 dead strategies.
4. Defer the 5-agent split until something survives the graveyard.

---

## What we disagree on (stated fairly)

Not much. One point: Claude Code thinks this discussion itself is a symptom of premature optimization and would rather spend the next session on strategy search. I think the discussion was worth having once to settle the governance questions, but I agree it does not need a round two before there is new evidence.

---

## Bottom line

The two of us agreed on: flat hierarchy, harness as the referee for technical disputes, code-where-possible for agents, defer the 5-agent split until something survives, and focus the next session on owed items + Judge-as-code + untested hypotheses. No ego, no power plays. The project's bottleneck is edge, not architecture.
