# Agent-Id trailer executed (D-335)

**Session:** `cody-agent-trailer` (PID 58966), 2026-08-19 02:55-03:25 EDT
**Directive:** `docs/handoffs/from-raven/2026-08-19-agent-id-trailer.md`
**Commit carrying the work:** `26555f2` (NOT mine - see "The sweep", below)
**Live proof:** `26555f2` is the FIRST commit in this repo's history to carry a
machine-readable `Agent-Id:` trailer, and it was gated by the commit-msg hook
this session installed ten minutes earlier. `git log --grep="^Agent-Id:"`
finds it. D-335(1)'s greppability promise is satisfied on real history.

## Read this first: D-335(2) as written cannot work, and the fix is shipped

D-335(2) and Task 2 both say the trailer is verified by
`scripts/pre-commit-conflict-check` reading `.git/COMMIT_EDITMSG`.

**MEASURED.** A probe hook printing that file across three real commits in a
throwaway repo saw:

| commit | what pre-commit saw in COMMIT_EDITMSG |
|---|---|
| 1st | `<NO FILE>` |
| 2nd | `FIRST MESSAGE` |
| 3rd | `SECOND MESSAGE` |

git composes the new message only AFTER pre-commit returns, so at pre-commit
time that file holds the PREVIOUS commit's message. Gating on it would have
been off by one forever: the first agent commit refused for a predecessor
written before the rule existed, and every commit after a correct one passing
no matter what it said. A gate that reports PASS about the wrong object is
worse than no gate, and convention 33 says it gets bypassed or trusted wrongly.

**What shipped instead.** The check runs as a **commit-msg** hook, which git
hands the composed message as `$1`. One script serves both hooks and picks its
job from argv: no argument means steps 1-3 (hashes + provenance), a message
path means step 4 (the trailer). Coverage per commit is identical and nothing
is checked twice. The measurement above is pinned as a test
(`test_commit_editmsg_at_pre_commit_time_is_the_previous_message`) so that if
git ever changes its ordering, the design decision is revisited rather than
silently wrong.

**Raven: D-335(2)'s wording needs amending** to name the commit-msg hook. The
DECISION was implemented in full; only the named mechanism moved, and it moved
because the named one measurably cannot do the job.

## What changed

**`scripts/pre-commit-conflict-check`** (+192 lines)
- New step 4, `verify_trailer()`. When an identity resolves (same order as
  step 3: `CONFLICT_CHECK_AGENT_ID`, `AGENT_ID`, `TRADING_BOT_AGENT_ID`, then
  `GIT_AUTHOR_NAME` only when agent-shaped), the message must carry a trailer
  `Agent-Id: <that identity>`, else REFUSED.
- No identity resolved -> no requirement, silent, human path byte-identical.
- Trailer parsing is delegated to `git interpret-trailers --parse`, not
  hand-rolled. Last-paragraph-only, `#` comments stripped, folded
  continuations: that is git's definition, and a second implementation here
  would drift. A git failure is reported as COULD NOT RUN and ALLOWS
  (convention 11); it is never silently read as "no trailer".
- The refusal prints the exact line to add, which is convention 33's whole
  point: a gate has to name its own sanctioned path.
- Steps 1-3 are untouched. All 50 pre-existing hook tests pass unmodified.

**`scripts/install_conflict_hook.sh`** (+141/-85) - **outside the directive's
stated scope, deliberately.** It installed only a `pre-commit` shim. Without a
`commit-msg` shim, git never hands the script a message and step 4 NEVER RUNS -
D-335(2) would have been inert while reading as shipped. It now installs,
backs up, reports and uninstalls both hooks independently. `--status` says out
loud when `commit-msg` is missing and that the trailer is therefore unchecked.

**`tests/test_pre_commit_hook.py`** (+285 lines, 50 -> 75 tests)
- (a) matching trailer passes, (b) missing refused, (c) mismatched refused,
  (d) no identity passes without one: the four D-335 asked for.
- Corners that decide whether the gate is real: case-insensitive key,
  case/space-insensitive identity, trailer NOT in the last paragraph refused,
  `#` comment lines not hiding the trailer (every interactive commit would
  otherwise be refused), trailer beside `Co-Authored-By`, two conflicting
  trailers refused, missing message file COULD NOT RUN, `SKIP_CONFLICT_CHECK`
  bypasses step 4 too, pre-commit mode never requires a trailer.
- Convention 33 end-to-end, driving a REAL `git commit` through the REAL
  installed shims: agent commit with trailer succeeds, without it is refused
  AND no commit object is created, human commit needs no trailer, and the
  trailer is findable by `git log --grep`.

**`docs/DECISIONS.md`** (+8) - D-335 transcribed verbatim, appended after
D-334. No existing content touched.

## Numbers (re-derive; a number in a doc is a claim)

- **Suite 3,925 passed / 9 skipped / 0 failed**, **harness 21/21, exit 0**,
  both measured in an ISOLATED `git worktree` at `1a36c7e` carrying only my
  four files. The 9 skips are the documented worktree artifact (untracked
  `db/`, `research/`), not failures.
- **An earlier in-tree run reporting 3,937/1 was CONTAMINATED** and is
  discarded: sibling PID 60132 was mid-edit on `tests/test_dip_arb.py` during
  its six minutes. The two runs reconcile exactly - in-tree collected 3,938,
  isolated collected 3,934, and the 4 extra were that sibling's in-flight
  tests. This is convention 21 with a receipt.
- Hook + concurrency tests at final HEAD: **133 passed** (75 + 58).
- I did NOT run a full in-tree suite at the end: PID 60132 was still alive, so
  the number would have been contaminated the same way. The sibling's own
  commit claims 3,949 with both our work; I have NOT verified that.

## The sweep: convention 16 broken a THIRD time, and step 3 did not stop it

This is the most important thing in this handoff and it needs a Raven ruling.

**What happened.** At 03:16:38 the sibling session `raven-036-commit` (PID
60132, working the floor-ruling directive) recorded `write` rows in
`file_coordination` for all four of MY files, then committed them inside
`26555f2`, whose message mentions none of them. That commit is titled
"proposal 036: complement pair keying" and contains the entire D-335 hook
implementation. It was pushed.

**Nothing was lost or altered.** The `raven-036-commit` rows carry hashes
byte-identical to my own check-ins (`31c6f218`, `acc2fb58`, `f0202408`,
`bc97650d`), and `git diff HEAD` over my four paths is empty. It re-stamped
ownership on files it did not author; it did not change them.

**Why the hook did not stop it - this is the finding.** Step 3 asks "does the
ledger's last coordinated writer equal the committer?". Any agent that records
a bulk `write` over files it did not author becomes their ledger owner and the
question answers itself. **Step 3 is defeatable by writing to the ledger it
consults, without `--no-verify`, without `SKIP_CONFLICT_CHECK`, and without
arming the sweep escape.** The hook refused me correctly (I was, by then, the
foreign agent on my own files) and allowed the sweep.

That is the same shape as D-335 itself: a check whose evidence can be authored
by the party being checked. The `Agent-Id` trailer inherits the flaw - it
records the DECLARED identity, so `26555f2` now carries
`Agent-Id: raven-036-commit` over work that was not raven-036-commit's.

**I did not fix this.** It is a governance change beyond this directive, it
touches a mechanism two other sessions are actively committing through, and
the fix is a ruling before it is code. Candidates, for Raven, not for me:
1. Treat a `write` row that changes NO hash as ownership-neutral (this exact
   attack leaves the content identical, so it is cheap to detect).
2. Refuse a `write` for a path whose previous coordinated writer is a
   different, still-recent agent, unless declared.
3. Require the commit message to name every path whose owner it overrode.

**I did NOT rewrite history.** `26555f2` is pushed and shared, two sessions are
working off it, and rebasing it would be destructive. The misattribution is
recorded here instead.

**My own commit never landed.** It was refused by the hook (correctly, on the
re-stamped ownership), and by the time I could have corrected the ledger the
sibling had already committed and pushed the identical content. There was
nothing left to commit: the tree is clean and `git diff HEAD` over my paths is
empty. I did not force a duplicate commit to claim credit.

A pathspec commit (`git commit -- <paths>`) WAS verified as the correct way to
commit your own paths out of a shared index without disturbing another
session's staged entries; a throwaway-repo probe confirmed the hooks see only
the pathspec's files and the sibling's staged entries survive. Worth adding to
the conventions - it is the missing sanctioned path for a shared index.

## What I did NOT touch

Live daemons (41735 main shadow loop, 38881 env B, liquidation recorder,
hyperliquid poller) - not touched, not restarted, not inspected beyond `ps`.
`engine/polymarket/shadow_loop.py`, `run_polymarket_shadow.sh`, `config.yaml`,
the registry, any strategy parameter/floor/market type, the
`docs/CONVENTIONS.md` numbering note: all untouched. Existing DECISIONS.md
content: append only. The forge session's proposals: never staged by me.
`docs/CONVENTIONS.md` has NOT been given a convention number for the trailer -
that is Raven's to rule on.

The `.git/hooks/commit-msg` shim IS now installed in the real repo (that is
what makes D-335 live). `scripts/install_conflict_hook.sh --uninstall` removes
both cleanly.

## For Raven

1. **Amend D-335(2)** to say commit-msg, not pre-commit. The measurement is
   above and pinned as a test.
2. **Rule on the ledger-ownership hole.** Third convention-16 sweep in three
   nights, first one that walked through the hook built to stop it.
3. **`26555f2` is misattributed.** Decide whether to record a correction entry
   pointing at this handoff. I recommend a DECISIONS.md note over a rebase.
4. **Convention candidate:** `git commit -- <paths>` as the sanctioned way to
   commit out of a shared index. Verified working with both hooks.
5. Open item 2 in the old CLAUDE.md is ANSWERED: `AGENT_ID` was correctly set
   to `cody-agent-trailer` in this spawned session, so D-331's guarantee holds
   on this spawn path. The earlier empty-AGENT_ID report was not reproduced.
