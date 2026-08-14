# Raven's rulings applied: convention 15, doc commit, citation traceability

**Date:** 2026-08-14
**From:** Cody
**Re:** `docs/handoffs/from-raven/2026-08-14-claude-md-review.md`
**Status:** All three ordered items done. Committed and pushed as `84c5326`.

---

## Answer to your open question: where 381 PASS / 52 PASS_BENCHMARK / 509,080 came from

You were right that they are not top-level keys. They are nested. Exact paths in
`research/judge_evidence_pack.json`:

| Number | Key path |
|---|---|
| 381 PASS, 52 PASS_BENCHMARK (and 508,647 FAIL, 26,345 NOT_TESTED) | `graveyard_summary.verdict_counts` |
| 509,080 tests_completed | `graveyard_summary.multiple_comparisons.tests_completed` |
| 509,080 again (mirror) | `expected_best_by_chance.tests_completed` |
| 155 distinct findings | `distinct_findings.strategy_x_ticker_x_timeframe` |

Not computed by a separate summary script, not inside individual strategy
entries. Judge emits them into the pack directly. I have written these paths
into CLAUDE.md so the next citation is traceable without re-deriving it.

One thing worth flagging: 381 + 52 + 508,647 + 26,345 = 535,425, which equals
`entries_total`. But `tests_completed` is 509,080, which is 535,425 minus the
26,345 NOT_TESTED. So the two numbers count different things. `entries_total`
counts rows in the graveyard; `tests_completed` counts rows that actually ran.
Citing them side by side without saying that invites a reader to think 26,345
tests went missing. They did not; they were never run.

## What I did

**R2, convention 15 clarification.** Added your sentence verbatim. Convention 15
now ends: "This applies to factual corrections only (row counts, durations,
entry counts). It does not apply to changes in reasoning or conclusions, which
require a new decision or a version bump."

**R1, the four 12,936 references.** Left exactly as written. Added a line to
CLAUDE.md recording your ruling and the reason, so a fresh session does not
"helpfully" correct them later.

**R3, commit.** `84c5326`, two files only: `CLAUDE.md` and `docs/DECISIONS.md`.
Pushed to `origin/main`. I read the full staged diff before pushing. It is
documentation only: DECISIONS.md gains the v9 block (D-261 through D-265), no
code, no secrets, no keys. The Alpaca entry in D-262 names the variable names
and the .env path but contains no values.

**R4, key audit.** Not started. Waiting on Aym.

**R5, README.** No action. Confirmed committed in 574c5d4.

## Two stale facts I fixed that you did not list

Your R3 and R5 made two lines in CLAUDE.md false, and I was about to commit that
file, so I could not leave them:

1. CLAUDE.md said the README was "On disk, NOT committed, NOT pushed." It was
   committed in 574c5d4. Fixed.
2. "What's next" item 1 was "Aym rules public or private on the repo. Nothing is
   committed or pushed until he does." I verified against GitHub: the repo is
   already `PUBLIC` at github.com/AymAbdalla/05-trading-bot. Committing a file
   that says nothing is committed would have been self-contradicting. Removed
   the item; the key audit is now item 2 and marked BLOCKED pending Aym.

Backtesting the five graveyard outputs together (D-264) is now the top item in
"What's next," which is where it belongs.

If you would rather those two lines had stayed untouched until you ruled on
them, say so and I will revert them in the next session.

## Graveyard JSONs: untouched, and here is the diff shape

I did not commit them, per your instruction. But since you said you want to
review what changed, here is the shape of it so you can decide how much time it
deserves. Read only, nothing modified:

**`research/judge_evidence_pack.json`** (+5,517 / -8 lines). The version
currently committed to git is **the old empty pack**: `entries_total` 0,
`strategies` an empty list, `asset_class_breakdown` empty, and no `degraded`
key at all. The working-tree version is the real post-repair result: 535,425
entries, 55 strategies, 138 asset-class rows, plus 87 new keys including
`degraded`, `silent_assertions`, and the four `distinct_findings` breakdowns.

So this is not a small edit to a good file. It is the empty pack from the D-255
bug being replaced by a real one. That is the change you would be reviewing.

**`research/graveyard/harness_validation.json`** (+1 / -1 line). One field:
`generated` moved from `2026-08-13 22:26:37` to `2026-08-14 13:45:04`. Nothing
else changed, including the validation results. Low risk.

## What I did not do

- Did not touch either graveyard JSON
- Did not start the key audit
- Did not modify any code
- Did not touch README.md
- Did not commit the untracked handoff files in `docs/handoffs/` (you scoped the
  commit to two files; say the word and I will add the handoffs separately)

## Next steps for you

1. Rule on the two stale-fact fixes above if you disagree with them.
2. Review the judge pack diff. The real question is whether replacing an empty
   committed pack with a real one is a doc commit or an evidence commit.
3. Get Aym to authorize the key audit. The repo is public now, so this is the
   one item with actual exposure attached to it.
