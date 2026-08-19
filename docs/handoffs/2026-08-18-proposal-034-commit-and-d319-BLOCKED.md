# Proposal 034 commit + D-319 execution: verified and prepared, COMMITS BLOCKED

**Session:** Cody, 2026-08-18 ~22:51-23:05 EDT
**Instruction:** `docs/handoffs/from-raven/2026-08-18-proposal-034-commit-and-d319-execution.md`
**Status:** verification PASSED, working-tree prep DONE, **no commit and no push happened.**

---

## TL;DR for Raven

1. Both gates re-derived and green: **3,814 passed / 1 skipped / 0 failed** (exit 0),
   harness **21/21 exit 0**. Registry re-derived: **25**, index 24 =
   `FairValueSettlementExit`, `('crypto_updown',)`.
2. **This session has no git write permission.** `git add` is not on the
   allowlist and there is no one to approve it in a `-p` session. Tasks 1 and 2
   are prepared in the working tree but **uncommitted and unpushed**.
3. **New finding, and it matters: commit `79ba55d` shipped proposal 034 WITHOUT
   its test file.** `tests/test_fair_value_settlement_exit.py` is still
   untracked. The commit message says "26 tests"; the diff contains none of them.
4. **New finding: the shadow loop restarted NATURALLY at 22:50:58 EDT, on its
   own, before this session started.** New PID **27490** on commit `79ba55d`.
   All 25 strategies are live, including 034. I did not touch it. The wake-up
   file's "PID 3108 / PRE-027 source" claim is now stale.

---

## What I actually verified (Task 1, step 1)

Re-derived, not quoted:

    .venv/bin/python -m pytest tests/ -q --ignore=tests/test_dashboard_charts.py
      -> 3814 passed, 1 skipped, 2 warnings in 367.42s   exit 0

    .venv/bin/python backtest/validate_harness.py
      -> Harness-validity checks: 21/21 passed
      -> Overall: ALL PASS                                exit 0

    registry: 25   index 24 = FairValueSettlementExit ('crypto_updown',)

Both match the numbers Raven expected. Nothing red. Under Raven's own rule
("if anything is red, STOP") there was no reason not to commit - the reason is
the permission wall, not a test failure.

---

## FINDING 1: `79ba55d` committed 034 without its 26 tests

Raven's instruction said to commit 034's file list. Checking first showed
**034 is already committed** - `79ba55d` landed the strategy, the registry
entry, `agents/forge_shadow_eval.py`, `docs/DECISIONS.md` (D-320), the handoff
and all five pin-updated test files.

It did **not** land `tests/test_fair_value_settlement_exit.py`.

    $ git show --stat 79ba55d
     agents/forge_shadow_eval.py                          |  11 +
     docs/DECISIONS.md                                    |  47 +++
     docs/handoffs/2026-08-18-proposal-034.md             | 239 +++
     strategies/polymarket/__init__.py                    |  32 +
     strategies/polymarket/fair_value_settlement_exit.py  | 444 +++
     tests/test_fair_value_arb.py                         |  18 +-
     tests/test_longshot_fade_hold_to_resolution.py       |   9 +-
     tests/test_maker_fill_wiring.py                      |   7 +-
     tests/test_weather_bracket_width_matched.py          |   6 +-
     tests/test_weather_shadow_wiring.py                  |   9 +-
            <- no test_fair_value_settlement_exit.py

    $ git status --short
    ?? tests/test_fair_value_settlement_exit.py     <- still untracked

    $ .venv/bin/python -m pytest tests/test_fair_value_settlement_exit.py --collect-only -q
    26 tests collected

The commit message reads "proposal 034: ... **26 tests**, ...". The 26 tests
exist and pass, but they exist **only on this disk**. A fresh clone gets the
strategy with zero coverage, and the suite there is 3,788, not 3,814.

This is the same failure shape as the one Raven caught in `aafc768` (message
claims D-319 untracking, diff does not contain it). Two commits in a row whose
message asserts work the diff does not contain. **A commit message is a claim,
not a fact** - worth a convention number alongside 22, 24 and 25.

Consequence for Raven's Task 1: the task is not "commit 034", it is "commit the
one file 034 was missing". The other seven paths in Raven's list are already in.

## FINDING 2: the shadow loop restarted on its own; 034 is LIVE

Raven's Task 3 said do not restart the loop, and that the smoke test happens at
the next natural restart, not this session. **The natural restart already
happened**, at 22:50:58 EDT - roughly 60 seconds before this session was
spawned. I did not cause it and did not touch it.

    PID 3108   GONE (not in ps)
    PID 27490  NEW - the loop, started 22:50 PM by the wrapper
    PID 90158  wrapper shadow_runner.py    - alive, unchanged
    PID 48637  liquidation recorder        - alive, unchanged
    PID 37578  hyperliquid poller          - alive, unchanged

`logs/polymarket_shadow_20260819T025057Z.log` header:

    commit:   79ba55d
    strategies : 21 per asset, 63 evaluations per cycle
      ... PM_longshot_fade_hold_to_resolution, PM_fair_value_settlement_exit
    weather cycle : ... PM_weather_bracket_width_matched
    pid=27490

So 027, 028, 032, 033 and 034 are all in evaluation now. Every "PID 3108 runs
PRE-027 source and will not evaluate these" statement in the wake-up file is
stale as of 22:50 EDT. I have rewritten that section.

**034 has not fired yet** - in ~14 minutes it appears exactly once in the log,
in the startup roster, with no ENTER. Not alarming for a strategy gated at
`EDGE_THRESHOLD 0.05` / `ENTRY_ASK_CAP 0.60` and capped at 2 concurrent, and
not something I was asked to tune. But the 5-position smoke test Raven deferred
is now **collecting data on its own**, unsupervised, which is a different
situation from the one Raven's instruction assumed. Worth a decision.

---

## BLOCKER: no git write permission in this session

`git add` returns "This command requires approval". This is a `claude -p`
session with no approver. The project allowlist
(`.claude/settings.local.json`) grants `Bash(.venv/bin/python *)`,
`Bash(python3 *)` and a few `echo` forms. No `git add`, `git commit`, `git rm`
or `git push`. Read-only git (`status`, `log`, `show`, `diff`, `ls-files`) runs
fine; `git check-ignore` and `git ls-tree` are also blocked.

I did **not** route git through the allowlisted `.venv/bin/python` via
`subprocess` to get around this. The permission layer withheld git writes
specifically; using an allowlisted interpreter to execute the withheld command
would be defeating a control, not satisfying a task.

So Tasks 1 and 2 are **prepared but not executed**. Task 2's step 5 ("only
after BOTH commits are in ... push") is therefore also not done.

---

## What IS done, in the working tree

     M .gitignore                                    <- D-319 block added
     M strategies/proposals/034-...-experiment.md    <- wording fix
    ?? tests/test_fair_value_settlement_exit.py      <- still untracked
     M research/graveyard/harness_validation.json         )
     M research/hyperliquid/leaderboard_wallets.json      ) the three D-319 files
     M research/polymarket_paper/polymarket_paper_log.csv )

Both edits went through `engine.concurrency.safe_edit` with
`agent_id='cody-034-commit'`. Ledger was clean at session start (0 active
checkouts) and no peer `claude -p` was running (the only match was this
session itself).

**Doc wording fix (Task 1 step 3):** `data_requirements` now reads "5-minute
holds". One line changed, nothing else touched.

**Deliberately NOT changed:** there is a *second* 15-minute phrase in the same
document, line 13, in the **sizing** field: "Holds go from roughly 8 seconds to
up to 15 minutes". Raven's instruction scoped the fix to `data_requirements`
and to the phrase "15-minute holds", and said to leave the rest untouched, so I
left it. It is inconsistent with `WINDOW_SECONDS = 300` for the same reason the
other one was. Flagging rather than silently widening scope or silently
ignoring it - Raven's call.

**`.gitignore` (Task 2 step 2):** the three paths added under a D-319 comment.
Inert until the files are untracked.

---

## D-319 sizing note

The CSV is **419MB** on disk (Raven said 432MB; it grows every cycle, both
readings are right for their moment). The **committed blob is 66MB** per
D-319's own text, and `origin/main == HEAD` with 0 unpushed commits - so every
blob in history already passed GitHub's 100MB check. **No history rewrite is
needed**; `git rm --cached` plus the gitignore is a complete fix. The failure
Raven predicted is the *next* push after the CSV gets committed again, not a
push that is broken today.

---

## Exact commands for whoever has git permission

    # Task 1 - the one file 79ba55d missed, plus the doc wording fix
    git add tests/test_fair_value_settlement_exit.py \
            strategies/proposals/034-pm-fair-value-settlement-exit-experiment.md
    git commit    # suggested message:

      test: add proposal 034's 26 tests (79ba55d shipped the strategy without them)

      79ba55d's message claims 26 tests; its diff contains none. The file was
      never staged, so a fresh clone had fair_value_settlement_exit.py with zero
      coverage. Also corrects proposal 034's data_requirements wording from
      '15-minute holds' to '5-minute holds' (Raven ruling: Forge templating
      artifact from 032; 034 and its parent trade WINDOW_SECONDS = 300).

    # Task 2 - execute D-319
    git rm --cached research/graveyard/harness_validation.json \
                    research/hyperliquid/leaderboard_wallets.json \
                    research/polymarket_paper/polymarket_paper_log.csv
    git add .gitignore
    git commit    # suggested message:

      docs: execute D-319 - untrack three live research files

      aafc768 was messaged 'D-319 research file untracking' but its diff touched
      only DECISIONS.md and four test files. The three files stayed tracked and
      unignored. Working copies stay on disk; the loop and the harness read them
      live.

    # Verify, then push
    git ls-files research/ | grep -E "harness_validation|leaderboard|paper_log"
      # expect empty
    git check-ignore -v research/graveyard/harness_validation.json \
                        research/hyperliquid/leaderboard_wallets.json \
                        research/polymarket_paper/polymarket_paper_log.csv
      # expect all three returned
    git push

Re-run both gates before pushing if any time has passed - the loop is live and
writing.

---

## Boundaries respected

- No shadow-loop restart, no `kill`, no second `run_polymarket_shadow.sh`. The
  restart that happened was the wrapper's own, before I was spawned.
- Wrapper, liquidation recorder and hyperliquid poller untouched.
- No backtests run. `validate_harness.py` is a harness-validity check, not a
  backtest, and Raven's Task 1 asked for it explicitly.
- No `SKIP_CONFLICT_CHECK`, no `--no-verify` (moot - I never reached a commit).
- Did not re-raise the commit-policy question. D-319 is the standing ruling;
  the blocker here is a permission wall, not a policy gap. The "five proposals
  deep" claim is retired and removed from the wake-up file.

## What Raven needs to decide

1. Who runs the two commits - re-spawn Cody with git on the allowlist, or do it
   by hand? If re-spawning is the answer, `.claude/settings.local.json` needs
   `Bash(git add *)`, `Bash(git commit *)`, `Bash(git rm *)`, `Bash(git push *)`
   or the session needs a different permission mode.
2. The second "15 minutes" phrase in 034's sizing field - fix or leave.
3. 034 is live and unsupervised right now. The 5-position smoke test is
   happening whether or not anyone is watching it. Supervise, or let it run?
4. Whether "a commit message is a claim, not a fact" earns a convention number.
   Two consecutive commits have now asserted work their diffs did not contain.
