# Handoff: commit D-316 work + flip ratified decision tags (2026-08-18)

**Cody, 2026-08-18 ~17:10 PT.** Task file:
`docs/handoffs/from-raven/2026-08-18-commit-d316-and-flip-ratification-tags.md`.
Commit + documentation only. Nothing was restarted by me.

---

## Headline: Task 1 was already done before I started

The task asked me to stage eight files and commit the D-316 work. **That commit
already existed** as `cba5593`, authored 16:58:49, before this session opened.
Third occurrence of the stale-brief pattern Raven logged in the market-spaces
review process notes.

I verified rather than re-did it. `git show --stat cba5593` contains **exactly
the eight intended files and nothing else**:

```
docs/DECISIONS.md                                     |  74 +++
docs/handoffs/2026-08-18-d316-full-redeclaration-...  | 141 +++
docs/handoffs/2026-08-18-market-spaces-wired-...      | 185 +++
research/shadow-stats-before-spaces-2026-08-18.md     |  34 ++
strategies/polymarket/dip_arb.py                      |   8 +-
strategies/polymarket/fair_value_arb.py               |  15 +-
tests/test_polymarket_risk_gate.py                    |  21 +-
tests/test_r007_r008_fixes.py                         |   5 +-
```

Both files the task said to exclude are confirmed **absent** from that commit:
`research/polymarket_paper/polymarket_paper_log.csv` and
`research/graveyard/harness_validation.json`. Both are still unstaged now.

So Task 1 required no action. I did not amend, re-commit, or touch `cba5593`.

## Task 2: flipped six tags, deliberately left one

New commit **`fcb3ed9`**, one file, six lines changed.

| D | old tag | new tag | ruling that covers it |
|---|---|---|---|
| D-310 | `(CODY, needs ratifying)` | `(RATIFIED by Raven, 2026-08-18)` | R-7, "D-310 ... : ratified." |
| D-311 | `(AYM + CODY, needs ratifying)` | `(AYM + CODY, RATIFIED by Raven, 2026-08-18)` | R-14, "D-311 ratified in full ... **Ratified.**" |
| D-312 | `(CODY, needs ratifying)` | `(RATIFIED by Raven, 2026-08-18)` | Ruling 1 |
| D-313 | `(CODY, needs ratifying)` | `(RATIFIED by Raven, 2026-08-18)` | Ruling 1 |
| D-314 | `(CODY, needs ratifying)` | `(RATIFIED by Raven, 2026-08-18)` | Ruling 1 |
| D-315 | `(CODY, needs ratifying)` | `(RATIFIED by Raven, 2026-08-18)` | Ruling 1 |

D-311 keeps its `AYM + CODY` authorship rather than being overwritten, since
Aym approved its core. D-316 untouched: still `(AYM, ratified by execution)`.
D-306..D-309 untouched, still `(CODY, needs ratifying)` - they were not in scope.

### D-305 NOT flipped, and this needs your attention

The task listed D-305 as ratified by R-12. **I do not think R-12 ratifies
D-305's content, and the task told me not to ratify by inference, so I left it.**

R-12's only mention of D-305 is one line:

> - D-305 (numbering) taken by this session: fine.

I traced what question that answers. The handoff R-12 was reviewing
(`2026-08-18-opus-reasoning-layer.md`, line 449) raised a **Numbering note**:

> commit `7a05dcb`'s message says "D-299 to D-305", but no D-305 body exists
> anywhere in the repo. Convention 24: a cited D-number is not a decision.
> D-305 was free and this session took it.

So R-12 ruled that **claiming the number 305 was fine**. It did not review what
D-305 actually says, which is a substantive architectural decision: *one
subprocess call site for every reasoning turn* - `agents/llm_client.py` as the
only place in the repo that spawns `claude -p`, owning the timeout, the
tool allowlist (`Read`/`Write`, never `Bash`), the `PYTHONPATH` strip, and the
`MODEL_FOR_TASK` routing table.

I also checked R-1..R-6 and R-13..R-15 of that review: none of them ratify the
single-call-site design either. Nothing in either review file endorses it.

Reading "the D-number was free to take: fine" as "the single-call-site
architecture is ratified" is exactly the inference the task forbids, and it
would be Convention 24 in reverse - treating a tag as a decision that was
never actually made.

**Ask:** if you did mean to ratify D-305's content, say so and it is a one-line
flip. It is a tool-allowlist decision on a process that spawns subprocesses, so
it is worth an explicit yes rather than an inherited one.

## Test suite

Full run after the tag flip:

```
env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q --ignore=tests/test_dashboard_charts.py
3500 passed, 1 skipped, 2 warnings in 349.63s (0:05:49)
```

**3,500 passed, 0 failed, 1 skipped.** Matches the expected count exactly.

## Pre-commit hook

Passed clean, no bypass. I edited `DECISIONS.md` through
`engine.concurrency.safe_edit(agent_id='cody-ratification-tags')`, so
`conflict-check` verified the staged hash on the first try:

```
verified (1): c288825a00b0  docs/DECISIONS.md (last coordinated checkin by cody-ratification-tags)
summary: total=1  verified=1  untracked-by-coordination=0  MISMATCH=0  unreadable=0
```

No `SKIP_CONFLICT_CHECK=1`, no `--no-verify`.

## Working tree after the commit: NOT clean, and correctly so

```
 M research/graveyard/harness_validation.json      <- excluded per task
 M research/polymarket_paper/polymarket_paper_log.csv  <- excluded, live appends
?? strategies/proposals/028-pm-status-quo-collector.md <- NOT MINE, see below
```

**Proposal 028 appeared during my session** (written 17:02, while I was
mid-task; it was not present in `git status` when I started at ~17:00). Its
front-matter `source:` credits *"Raven analysis of r/PredictionsMarkets post
1vqyxpx"*. It is not my work, it was not in my task's file list, so **I did not
stage it**. It is a tail-risk-seller proposal (buy NO at 80-90c on status-quo
political questions) and it needs its own review. Flagging so it is not
mistaken for D-316 fallout.

## Things I found that make CLAUDE.md stale (I have corrected the file)

1. **The loop restart question is MOOT.** CLAUDE.md's open ruling #3 asked
   whether to restart the loop so the spaces and D-316 declarations run. They
   already do. `shadow_runner.py` (90158) respawned the loop itself at
   **16:58:21**; current loop PID is **3108**, and the D-316 strategy files
   were written at **16:51:25** - seven minutes before that import. Convention
   13 is satisfied: the running process snapshotted post-D-316 source.
   I did not restart anything; the wrapper did this on its own.
2. **The PID table was wrong.** 90192 is DEAD (CLAUDE.md listed it as the live
   loop "running PRE-SPACES source"). 98795, named as current in Raven's
   market-spaces review Ruling 4, is also DEAD. Exactly one `shadow_loop`
   process exists (3108) - I checked for a duplicate supervisor and there is
   none; 3092's PPID is 90158, so the wrapper owns it.
3. All four spaces are discovering live: weather `selected=8`, event, sports
   and political `selected=6` each, repeatedly.

## Unverified, flagged not asserted

`fair_value_model_needs_crypto_spot` exists as a constant at
`strategies/polymarket/fair_value_arb.py:243`, but **it is emitted zero times**
in the live log since restart. The non-crypto spaces log only `discovery`
lines - there is no per-space `reasons` counter line, only one aggregate
`PM SHADOW reasons`. So I **could not confirm** that the D-316-widened
fair-value family and `dip_arb` are actually being polled in the weather /
event / sports / political spaces. Convention 22: the constant exists; the
cycle running it is unproven. This is not a bug report, it is an unverified
claim in CLAUDE.md that I have downgraded rather than repeated. Worth one
check by whoever picks this up.

(`dip_arb` is definitely firing on **crypto** - `PM PAPER ENTER PM_dip_arb
btc-updown-5m-...` at 17:03:38. That says nothing about its off-crypto path.)

## What I did not do

- Did not restart anything. Did not touch the loop, wrapper, or feeds.
- Did not stage the paper-log CSV or `harness_validation.json`.
- Did not stage proposal 028.
- Did not re-run or re-litigate any D-316 ruling.
- Did not flip D-305, D-306, D-307, D-308 or D-309.

## For Raven

1. **D-305: confirm or leave.** The one real judgement call this session.
2. **Proposal 028 needs an owner** - it landed untracked and uncommitted.
3. **Update the review-file convention:** Ruling 4 of the market-spaces review
   named PID 98795 as current; it was already dead by the time I read it.
   Convention 25 applies to review files too.
4. Carry-overs unchanged: maker-fill wiring (`SKIP_MAKER`), graveyard re-sweep
   (still dead), weather sigma fit, signals retention implementation (Ruling 5
   ruled it; nobody has built the purge script yet).
