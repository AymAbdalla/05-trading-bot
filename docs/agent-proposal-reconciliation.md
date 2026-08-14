# Reconciliation: Agent Runtime Proposal vs Joint Debrief

**Date:** 2026-08-13
**Author:** Claude Code
**Purpose:** Aym asked whether the three-way joint debrief matches the original agent runtime proposal, or whether something got lost or changed along the way. Direct answers below.

---

## 1. Does the debrief accurately represent the proposal? What's missing?

Mostly yes, but three things from the proposal did not make it into the debrief.

**Captured correctly:**
- The five-role table (Judge/Echo/Forge/Scout/Coach, build-as, why) carried over almost word for word.
- The Judge correction (pure code, not a subagent) carried over with the reasoning intact.
- D-217 recommendation carried over correctly (see question 4).
- The "premature process work, zero live strategies" framing carried over as the ROI point.

**Missing or thinned out:**
- **The governance carve-out line.** The proposal said, explicitly: if this project's agents run standalone, write one line in this repo's docs stating that trading-bot agents run standalone and Raven audits monthly, not per-decision, so the two rule systems (this project's flat hierarchy vs the global Hermes-leads-Claude-Code stack) don't silently conflict. The debrief agrees to a flat hierarchy in spirit but never writes that carve-out line anywhere. That's a gap, not a disagreement, nobody rejected it, it just didn't get restated. It should still get written down somewhere (SOUL.md or DECISIONS.md), or the global stack instructions and this project's practice stay in quiet tension.
- **The audit cadence mechanism.** The proposal specified: every agent action lands in the existing audit log, plus a weekly Echo digest, plus monthly drift-check crons per SOUL. The debrief's "primary channel" section talks about DECISIONS.md/HANDOVER.md for Hermes-Claude Code sync, which is a different (and fine) channel, but the weekly-digest and monthly-drift-check mechanics from the proposal aren't mentioned. Not contradicted, just not carried forward yet.
- **The runbook deliverable.** See question 5. It's the one build item that quietly dropped out.

Nothing in the debrief contradicts the proposal. The gaps are omissions, not disagreements.

---

## 2. Is "Hermes cron triggers Claude Code subagents" the same as Option 1?

Close, but it's a refinement, not a straight match, and it quietly drops a piece of Option 2.

The proposal's Option 1 said scheduled runs happen "on a cron (the existing schedule/cron tooling does this)" without saying which tooling or who owns the schedule. That was left open on purpose, it was the "fastest, near-zero new code" option precisely because it didn't commit to an owner.

The debrief closes that gap by naming Hermes as the cron owner: "Hermes cron triggers a scoped, tool-restricted Claude Code subagent invocation." That's a real decision the proposal hadn't made. It's consistent with Option 1's spirit (SOULs become `.claude/agents/*.md`, subagent boundary is the safety mechanism) but it is a specific commitment the proposal left as a blank.

What it also does, without saying so, is shelve Option 2 (the standalone Agent SDK runner) rather than treat it as a later migration target. The proposal's Option 3 (recommended) explicitly kept Option 2 in view as the place Forge/Scout migrate to once the loop proves out. The debrief's architecture line doesn't mention that migration path at all. That's worth a decision, not a silent drop: is Option 2 still the long-term target, or has "Hermes cron + subagent" become the permanent shape? If it's permanent, the standalone/independent-of-Hermes framing that was the whole point of the original proposal's title needs revisiting, since routing the schedule through Hermes reintroduces exactly the dependency the proposal was checking whether you could avoid.

**Recommendation:** worth one explicit line in DECISIONS.md: "Hermes-cron-triggers-subagent is the near-term shape (Option 1 refined); Option 2 (Agent SDK runner) stays the long-term target if/when volume or reliability demands it." Otherwise this reads as decided when it's actually just default-continued.

---

## 3. Is Judge-as-code still the right first build?

Yes, unchanged, and the work since then (cost model, cross-sectional harness, Lab v3/v4, ROADMAP.md) reinforces the order rather than changing it.

The debrief's agreed priority list is: (1) Aym's owed items, (2) Judge-as-code, (3) point search at pre-registered hypotheses (Lab v3/v4, C2 fix), (4) defer the 5-agent split. That's the same order the proposal argued for at the bottom: Judge-as-code first, then Forge pointed at lab docs, then Scout/Coach/Echo once there's something to scout/coach/report on.

The ROADMAP.md P0/P1/P2 items (cost model, cross-sectional harness, the four knob experiments) are search-track work, not agent-runtime work. They don't compete with Judge-as-code, they're what Judge and Forge will eventually operate on. If anything they strengthen the case: now that Lab v3/v4 and the cross-sectional harness exist, there's a growing backlog of pre-registered hypotheses for Forge to work from, which is exactly the input the proposal said Forge needed before it was worth building.

One thing worth flagging: ROADMAP.md's P0.1 (fee verification) and the first supervised paper run are Aym-owed and sit ahead of Judge-as-code in the agreed priority list. That's correct and nobody's disputing it, just noting the two docs agree on this without either one saying so explicitly.

---

## 4. Is "run under SPEC-5.7 rules until ratified" still the D-217 recommendation?

Yes. The debrief's language is actually a slightly better version of the proposal's option (b), not a different choice.

The proposal offered three options: (a) Aym ratifies the 11 rules directly, (b) run under SPEC-5.7 only until ratified, (c) keep waiting for Raven. It flagged (b) as safe because the one substantive conflict named in D-217 (twin methodology) is already resolved in code via the percentile gate.

The debrief's agreed language combines (a) and (b) rather than picking one: run under SPEC-5.7 now (interim safety), and separately, Aym can ratify the 11 rules directly since they're mostly extra honesty requirements (permanent resolution), instead of waiting on Raven's ruling. That's consistent with the proposal, and arguably resolves D-217 faster than any single option in the original proposal did, since it doesn't leave ratification hanging indefinitely on Raven.

No conflict here. If anything, this is the one place the debrief improved on the proposal rather than just restating it.

---

## 5. The 4 concrete build items: judge.py, forge.md, cron entry, runbook. Anything changed?

Three of the four are intact. The runbook dropped out, and the cron entry got more specific.

- **`agents/judge.py`** - unchanged, still item 1, still first priority per question 3.
- **`.claude/agents/forge.md`** - unchanged in substance (Forge = Claude Code subagent per the role table), but its build order shifted. The proposal treated all four items as roughly one session's work. The debrief's priority list puts Judge-as-code and "point search at hypotheses" as separate, sequential steps (2 and 3), which means forge.md now waits until there's a specific lab doc to point it at, rather than getting built in the same pass as judge.py. That's a reasonable sequencing choice, not a contradiction, but it does mean "build forge.md now" from the proposal became "build forge.md when Lab v3/v4 has something ready" in practice.
- **Cron entry** - not dropped, but changed shape. The proposal's cron entry was generic ("a cron entry: nightly Judge run, weekly Forge run"). The debrief's architecture decision (question 2) specifies it runs through Hermes rather than a bare cron/launchd job. Same deliverable, different owner.
- **Runbook (`agents/README.md`)** - this is the one that actually disappeared. It's not mentioned anywhere in the debrief, not agreed to, not rejected. Worth explicitly deciding whether it's still in scope, since the debrief's audit-cadence gap (question 1) makes a runbook more useful, not less. Without it, "how you start, stop, and audit the loop" only lives in this proposal doc and in Claude Code's head.

**Recommendation:** re-add the runbook to the next agreed priority list, even as a stub, before Judge-as-code ships, since Judge-as-code is the first thing that will need "how do I audit this" written down.

---

## 6. Does ROADMAP.md conflict with or supersede the agent proposal?

No conflicts. But ROADMAP.md doesn't reference the agent runtime at all, which is a gap worth naming.

ROADMAP.md's P0-P5 sections are entirely about the search-and-measurement track: cost model, contract sizing, cross-sectional harness, the four knob experiments, strategy labs, engine work before paper trading, and data gaps. None of it touches Judge/Echo/Forge/Scout/Coach, judge.py, or the subagent architecture. That's fine, they're different tracks, but it means ROADMAP.md, which is the living plan doc, currently has no line item for "Judge-as-code" even though the debrief agreed it's priority 2 overall.

The one place they connect: ROADMAP's P4 (engine work before any paper session matters) lists "Telegram alerts" and "registry loader for shadow-mode strategies," both of which Echo and the agent loop will eventually depend on. ROADMAP doesn't call that dependency out, and the debrief doesn't reference ROADMAP's P4 either. Neither doc is wrong, they just haven't been cross-linked.

**Recommendation:** add a short "Agent runtime" line to ROADMAP.md, even just pointing at this proposal and the debrief, so the plan doc doesn't read as if the agent work doesn't exist.

---

## Bottom line

No contradictions between the proposal and the debrief. Everything agreed to is a legitimate refinement or a reasonable choice among the proposal's own options. The gaps are three specific things that got agreed on in spirit but never written down as a concrete decision or deliverable:

1. The governance carve-out line (standalone-agents-Raven-audits-monthly) never got written anywhere.
2. Whether Option 2 (Agent SDK runner) is still the long-term target now that Hermes owns the cron, or whether that's been quietly superseded.
3. The runbook deliverable dropped off the build list with no discussion.

None of these block Judge-as-code, which remains the correct next build in both docs. They're worth one line each in DECISIONS.md before they're forgotten entirely.
