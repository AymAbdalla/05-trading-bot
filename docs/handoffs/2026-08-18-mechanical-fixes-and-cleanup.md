# Handoff: mechanical fixes, cleanup, tree consolidation

**From:** Cody (PID 54689), 2026-08-18 07:30
**Acting on:** `docs/handoffs/from-raven/2026-08-18-mechanical-fixes-and-cleanup.md`
**Commit:** `031bbd6`

## Scoreboard

| Task | Status |
|---|---|
| 1. Remove unused `field` import | DONE |
| 2. `cp` forge agent into `.claude/agents/` | **BLOCKED - and it is not fixable by a spawned session. Needs Aym.** |
| 3. Split `no_underdog` into two reasons | DONE, with a test |
| 4. Re-run full suite, report stable count | DONE - 2,193 collected |
| 5. Rewrite CLAUDE.md | SUPERSEDED by a concurrent session. See below. |
| 6. Commit if clean | DONE - `031bbd6`, 99 paths |

## Task 2 is structurally impossible from a spawned session. Stop re-assigning it.

This has now been assigned for four sessions running and every one of them has
failed for the same reason. It is not an oversight and the next session will
fail too.

- `cp agents/forge/forge.agent.md .claude/agents/forge.md` via Bash: **denied.**
- The `Write` tool on `.claude/agents/forge.md`: **denied.**

The source file predicts this in its own install comment: `.claude/agents/` "is
outside what a spawned session is permitted to write."

**The state matters, because a stale copy is worse than a missing one.** The
file EXISTS, so nothing looks broken, but it is the wrong version:

| | bytes | mtime |
|---|---|---|
| `agents/forge/forge.agent.md` (source) | 11,250 | 2026-08-17 22:25 |
| `.claude/agents/forge.md` (installed) | 7,226 | 2026-08-17 21:19 |

The installed copy **predates the creative mandate**. A Forge spawned today
runs under the old rules: no `kind: experiment`, no `combination`, mandatory
`related_graveyard_findings`, a flat 30bps floor instead of the instrument-aware
200bps for binaries, and no shadow-evaluator section at all. It would refuse
proposals Aym explicitly authorised on 2026-08-17.

**Aym has to run the one-line `cp` from an interactive session.** Nothing else
unblocks it.

## Task 3: the split

`spread_harvest_maker._underdog()` returned `(None, 'book_implied', None)` for
two unrelated causes and the call site collapsed both into `no_underdog`.
Convention 20 forbids exactly this: two drop causes sharing one number.

| new reason | class | why |
|---|---|---|
| `no_book_midpoint` | DATA_BLOCKER | one-sided book or absent bid; no midpoint could be computed, so nothing was evaluated |
| `book_implied_exact_tie` | GENUINE | both mids present and exactly equal; the book WAS observed and the market is genuinely tied |

Classified as Raven specified. Three notes on how, not whether:

1. **The literals stay at the `decide()` call site**, not in `_underdog`'s
   return. At the time I wrote it, the AST guard only saw
   `decide('SKIP', <literal>)`, so a reason carried out in a variable would
   have been invisible to it. (A concurrent session has since landed D-290,
   which teaches the guard to follow indirection, so this is now belt-and-
   braces rather than load-bearing.)
2. **`no_underdog` is kept in the table**, marked retired. Rows logged before
   the split still carry it and would otherwise fall through to UNKNOWN.
3. **The `gate` / `coin_flip_source` feature is unchanged** - both paths still
   report `book_implied`. The skip name split; the gate did not. Results under
   the book-implied gate stay poolable with each other.

Pinned by `test_spread_harvest_names_a_missing_midpoint_apart_from_a_real_tie`,
which asserts both reasons AND that they classify on opposite sides of the
NOT_TESTED line.

**This closes open ruling 5 from the repo-mining handoff. It still needs a
D-number** - you specified the classifications, but the ruling is not recorded
in DECISIONS.md.

## Task 4: 2,193 collected. Three reds, none of them a broken strategy.

Measured 07:02-07:12 (the run takes 6-10 minutes, not the ~2 the old docs imply):

```
3 failed, 2189 passed, 1 skipped in 600.66s
```

The old CLAUDE.md baseline of 1,314 was stale by ~875 tests, not ~600.

| failure | verdict |
|---|---|
| `TestConfigWiring::test_config_yaml_matches_the_module_defaults` | known, permanently red by construction. Left alone as instructed. |
| `test_dip_arb.py::TestEstimate::test_the_estimate_satisfies_what_the_real_loop_reads_off_it` | concurrent-session artifact |
| `test_fair_value_arb_variants.py::TestRegistry::test_the_shadow_loop_identity_is_computed_from_the_list_length` | concurrent-session artifact |

### The bottom two are worth reading properly, because the mechanism is new

I first called them transient, re-ran them in isolation, got 2 passed, and
moved on. They then failed **again** in a second full run, which killed the
"transient" reading. They are not order-dependent either - the whole of both
files is green (164 tests, 0.39s).

The actual mechanism: **both tests call `inspect.getsource()` on
`PolymarketShadowLoop` methods and assert over the returned text.**
`getsource` does not read the imported code - it re-reads the file **from disk
at call time** and indexes it by the code object's line numbers. When another
session writes that file mid-run, the line numbers shift and getsource hands
back the wrong function body.

Measured, not inferred: `engine/polymarket/shadow_loop.py` has mtime
**07:05:52**, inside the 07:02-07:12 run window.

This is the complement to convention 13. Convention 13 says edits during a long
run do not reach it, because Python snapshots source at import - true, and it
is exactly why these tests are safe in a quiet tree. `inspect.getsource`
defeats that snapshot. **Before believing a getsource failure, `stat` the file
it reads and compare the mtime to the run window.**

Whether that is worth a convention 27 is your call, not mine.

## Task 5: superseded, deliberately not fought over

CLAUDE.md was written by another session at 07:00, again at 07:13 (mine), and
again at **07:25**, which rewrote it wholesale. I did not revert them and I am
not going to re-take it - their version is **better than mine**: it carries
D-290, D-296, the kill clock, the live liquidation tape and shadow loop PID
59357, none of which I had.

What I put in and what survives in their rewrite is the important part, and the
split IS documented there. Two things from my version that theirs does not
carry, recorded here instead:

- **The `inspect.getsource` mechanism above.** Their file says the two failures
  "passed on re-run in isolation", which is the reading I started with and then
  disproved. The failures recur; the cause is a mid-run write, not flakiness.
- **The Task 2 forge state** - specifically that the installed copy is stale
  rather than missing, and that no spawned session can fix it.

Worth noting for the epilogue rule generally: with nine sessions live, "REWRITE
this file" makes CLAUDE.md a contended resource that gets clobbered several
times an hour. Every one of today's rewrites lost something. Not my call to
change, but you should know the rule is misfiring at this concurrency.

## Task 6: commit `031bbd6`, 99 paths

Staged by explicit path from a filtered list. **No `git add -A`** (convention
16). Verified nothing sensitive staged: `.env`, `db/trading.db`, `work/` all
confirmed gitignored.

`.gitignore` covers CLAUDE.md, HANDOVER.md, `from-raven/`, `from-cody/` as you
asked. **`.claude/` is NOT covered** and shows as untracked. I left it out of
the commit rather than add a gitignore rule you did not ask for - flagging it
because `forge.agent.md`'s install comment asserts `.claude/agents/` IS
gitignored, and that is simply false today.

The commit is a snapshot of a nine-session tree. It certainly contains other
sessions' intermediate states. That was the instruction and I think it was the
right call - the last commit was 2026-08-17 and 99 paths of work was sitting
uncommitted - but it is not a clean single-purpose commit and should not be
read as one.

## What I did NOT touch

Strategy logic, `STRIKE_PROXY_NOISE_FLOOR_BPS`, the poll interval, any running
process, the WebSocket, the config-wiring test, `weather_arb.py`,
`fair_value_arb*.py`, `dip_arb.py`. No rulings made.

## For you

1. **Tell Aym to run the forge `cp`.** One line, interactive session, unblocks
   a subagent that has been running on 2026-08-17-morning rules.
2. **D-number for the `no_underdog` split.**
3. Decide whether the `inspect.getsource` finding becomes convention 27.
4. The epilogue "REWRITE CLAUDE.md" rule at nine-session concurrency.
