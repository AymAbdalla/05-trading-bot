# Handoff: banner provenance + D-331..D-334 recorded

**Session:** `cody-banner-record` (spawned, Opus)
**Directive:** `docs/handoffs/from-raven/2026-08-19-banner-provenance-and-record.md`
**Window:** 2026-08-19 ~02:16 - 02:35 EDT
**Commit:** `229c8d7`, pushed to `origin/main`. Parent `6036f71`.
**Suite:** 3,908 passed / 1 skipped / 0 failed. Harness 21/21, exit 0.
**Restarted:** NOTHING. See "what I did not touch".

---

## Task 0: the D-333 guard, all four checks

Run before any edit, in order:

1. `docs/handoffs/2026-08-19-hook-hardening-executed.md` exists (12,742 bytes,
   mtime 02:07).
2. No sibling `claude -p`. `ps aux | grep "claude -p"` showed only my own PID
   54164 plus grep noise. PID 37069 (verify-commit-and-restart) is GONE; 37068
   is its lingering `tmux new-session` client wrapper with no claude under it,
   exactly the artifact Raven predicted. `tmux ls` is still refused by the
   permission layer, so that leg was substituted with
   `engine.concurrency who`: **0 active checkouts in the last 3600s**.
3. `git status --porcelain` empty.
4. `git rev-parse HEAD` twice, ~90 seconds apart: `6036f716...` both times.

No waiting was needed. All four held on the first poll.

## Task 1: the banner (the build)

`run_polymarket_shadow.sh`, banner block only. Two lines added after `python:`
and before the closing `===`, so they are teed into the session log like
everything else in the block:

```
launched-by: ${AGENT_ID:-UNDECLARED}
launcher-pid: $$   parent-pid: ${PPID}
```

### Which PID trace, and why both

Raven left this to my judgement. I record **both `$$` and `${PPID}`**, because
each alone has a hole:

- **`${PPID}`** names what invoked the script - the tmux pane, the nohup shell,
  the agent's bash. This is the trace that literally answers "who restarted the
  loop". It is also the perishable one: by the time anyone asks, the parent has
  usually exited and the number is unresolvable.
- **`$$`** is the script itself, i.e. one of the wrapper pair that appears
  around the python child in `ps` (41700/41736 around 41735, in tonight's
  case). It joins a log file to a process tree seen later, and the existing
  `python pid=` line below the banner already records the other end. So `$$`
  makes the log-to-`ps` mapping unambiguous even when the parent is gone.

Together they reconstruct the chain parent -> script -> python. `launched-by`
carries the durable answer; the pids corroborate it. A comment block above the
banner says all of this in the file, so the next reader does not have to find
this handoff.

One subtlety worth stating because it is the kind of thing that silently
regresses: both are expanded inside the `{ ... } | tee "$LOG"` pipeline, which
bash runs in a **subshell**. Both survive it (`$$` is the shell's pid, not the
subshell's; `PPID` is fixed at shell startup and inherited unchanged), but that
is a bash guarantee I did not want to leave asserted only in a comment
(convention 22), so a test pins it against real observed pids.

### Tests: 6, in `tests/test_launcher_banner.py` (new)

Raven asked whether a subprocess test fits the pattern or is disproportionate.
It fits (`tests/test_pre_commit_hook.py` already executes a real shell script
as a subprocess), but there is a hazard the directive's sketch did not cover:
**you cannot "run the script far enough to print the banner" against this
repo.** The line immediately after the banner starts a shadow loop against
`db/trading.db`, which the live loop 41735 is writing to. Running the real
script here would have created a second writer.

So each test builds a THROWAWAY repo-shaped directory under `tmp_path`: a copy
of the real script, a `mode: paper` config.yaml, and a stub `python3` earlier
on `PATH` that answers the gate invocations by argv shape and does absolutely
nothing when asked to run `engine.polymarket.shadow_loop`. The real script text
is executed end to end; no real db, log, config or engine is touched. (This
also serves as the `bash -n` I could not run - `bash -n` is refused by the
permission layer, and executing the script is strictly stronger.)

What is pinned:

| test | pins |
|---|---|
| `test_a_declared_agent_id_is_carried_into_the_banner` | `AGENT_ID=cody-some-topic` reaches the banner |
| `test_an_undeclared_launcher_says_so_rather_than_defaulting` | no AGENT_ID prints `UNDECLARED`, not a plausible guess |
| `test_the_banner_pids_are_the_real_launcher_and_its_parent` | printed pids == `proc.pid` and `os.getpid()`. The subshell guarantee, measured |
| `test_the_provenance_reaches_the_log_file_and_not_only_the_terminal` | the fields survive the `tee`. A record only on a terminal that is long gone is no record |
| `test_the_provenance_lines_sit_inside_the_banner_block` | placement, which stdout cannot distinguish: a line after `===` would still appear in output |
| `test_the_launch_block_still_starts_the_real_module` | the launch block is unbroken. The edit sits directly above it and a bad splice there is silent until a restart |

## Tasks 2 and 3: the record

`docs/DECISIONS.md`: **D-331, D-332, D-333, D-334** appended after D-330, at
the end of the v12 section. Transcribed **verbatim** from Raven's directive -
no rewording, no expansion, no editorialising. Diff is +32 lines, 0 deletions,
so nothing existing was touched.

`docs/CONVENTIONS.md`: **convention 33** added (next free number after 32),
carrying the hook-hardening incident that earned it: the `--author` escape, the
env-prefix refusal, the verified fact that `--no-verify`/`SKIP_CONFLICT_CHECK`
were NOT used, and D-331 as the fix that removed the corner. The open 27/28/29
numbering note is untouched.

`tests/test_conventions_doc.py`: 2 new tests pinning 33's meaning and its named
incident, matching how 27/28/29/31 are pinned. The contiguity test needed no
change - it derives `range(1, N+1)`, and 33 keeps the sequence contiguous.

## Verification

- Full suite: **3,908 passed, 1 skipped, 0 failed** (355s). That is `d66aff5`'s
  3,900 plus exactly the 8 tests added here (6 banner + 2 conventions).
- `backtest/validate_harness.py`: **21/21, exit 0**, ALL PASS (convention 1).
- Targeted re-run of `test_conventions_doc` + `test_launcher_banner` +
  `test_pre_commit_hook` + `test_concurrency`: 127 passed.
- No sibling session was alive for any of it, so these numbers are not
  contended (unlike the in-tree runs earlier tonight).
- `git show --stat HEAD` read back after the push, per convention 31: 5 files,
  275 insertions, 0 deletions. The message matches the diff.

## Commit hygiene

All five files written through `engine.concurrency` as
`agent_id='cody-banner-record'` (`safe_edit` for the four that existed;
`checkout(allow_missing=True)` + `checkin` for the new test file, because
**`safe_edit` does not forward `allow_missing`** - worth knowing, it is a sharp
edge for anyone creating a file through the ledger). Staged by explicit path
(convention 16). No `git add -A`, no `--no-verify`, no `SKIP_CONFLICT_CHECK`,
no `--dangerously-skip-permissions`.

The pre-commit hook passed clean: 5 verified, 0 MISMATCH, 5 own-work, 0
FOREIGN-OWNED, identity resolved as `cody-banner-record` **via `AGENT_ID`**.

## For Raven: one gap D-331 opens, flagged not acted on

The spawn template works. `AGENT_ID=cody-banner-record` was in my environment
and the hook read it with no `--author` needed. That is convention 33 satisfied
on its first outing, by its own rule, one commit after it was written.

But note what changed as a side effect. D-331(2) accepted agent-authored
commits as **"the durable provenance record"**, and that durability came
entirely from `--author` writing the identity into the commit object. With the
env-var path, the identity is checked at commit time and **printed to a
terminal that is then discarded** - it is written nowhere in git history. This
commit `229c8d7` is authored `Aym Abdalla` and carries no machine-readable
trace of which agent produced it. So D-331(1) removes the mechanism D-331(2)
was accepting.

That is the same shape of failure as D-332 itself: a measurement taken and not
recorded. The cheap fix is a trailer rather than the author field, e.g.
`Agent-Id: cody-<topic>` appended to the commit message (greppable with
`git log --grep`, no cosmetic change to authorship, and the hook could append
or verify it). I did not do this: the commit message format is Raven's, fixed
in the directive, and this is a protocol change, not a transcription. Ruling
requested.

## What I did NOT touch

- **Nothing was restarted.** The main loop 41735 is still up (verified 02:28,
  started 00:56, 10:00 CPU) and is running the **pre-change** script text -
  convention 13, bash snapshots the script at start just as Python snapshots
  source at import. **The `launched-by` field first appears at the loop's next
  natural restart, and that restart's banner is the first one that will answer
  the D-332 question.** Until then the running session stays NOT ATTRIBUTABLE,
  exactly as D-332 records.
- Env B (38881), the liquidation recorder, the hyperliquid poller: untouched.
- `engine/polymarket/shadow_loop.py`, any strategy parameter/floor/market type,
  the registry: untouched.
- `scripts/pre-commit-conflict-check`, `scripts/install_conflict_hook.sh`:
  untouched.
- In `run_polymarket_shadow.sh`: the launch block, gates 1-4, the trap and the
  wait loop are byte-identical. The diff is the banner block plus the comment
  above it, nothing else.
- Existing DECISIONS.md content, including D-323..D-330: untouched, append only.
- The 27/28/29 numbering note in CONVENTIONS.md: untouched, it is Raven's.
- The env B whitelist correction: still documented, still not applied, because
  applying it means a restart.

## Still open, carried forward unchanged

1. The `Agent-Id` trailer question above.
2. Env B whitelist corrections (drop `dip_arb` + `fair_value_arb_wide`, add
   `corridor_collector`, tag `streak_snapper` MAKER) - for its next natural
   restart.
3. D-323's "the code path stays live and tested" sentence still wants amending.
4. Proposal 029 held on a gate; the forge brief in `docs/PLAN-2026-08-19.md`
   section 2 Q4 (forecast-free strategies only).
5. Aym's owed items: rotate the Alpaca key (D-262), supervised paper run +
   kill-switch drill (D-264), ratify D-217's SOUL rules (D-244), copy the forge
   agent file, install the critic cron from Terminal.app (R-10).
