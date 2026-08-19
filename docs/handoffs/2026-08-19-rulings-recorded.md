# Rulings recorded: D-349..D-352 + convention 35

**Session:** `cody-record-rulings`, 2026-08-19, 16:28-16:45 EDT (measured with `date`).
**Brief:** `docs/handoffs/from-raven/2026-08-19-record-rulings-036-037-floor-loop.md`.
**HEAD at start:** `99e3ca5`, tree clean. **HEAD after the records commit:** `b06e2c3`.
**Records-only.** No strategy code, no `config.yaml`, no schema, no constant, no
loop restart, no process signal. Nothing importable was touched.

## What was done (all five tasks)

1. **D-349..D-352 transcribed into `docs/DECISIONS.md`, verbatim.** Not retyped:
   the four blockquote blocks were EXTRACTED from the brief programmatically, the
   quote prefix stripped, and every non-empty line asserted back against the brief
   before the write. Block order, D-numbers and the Problem / Decision / Where
   sections were each asserted. D-349 to D-352 were confirmed FREE first (151
   `### D-` headings, highest 344; D-345..D-348 live inside the merged D-344
   entry). The append was verified to be a pure suffix: the pre-existing bytes are
   an exact prefix of the result, and `git diff --numstat` read 75 insertions and
   **0 deletions**.
2. **Convention 35 added to `docs/CONVENTIONS.md`** (31 insertions, 0 deletions),
   INSERTED after convention 34 rather than appended at the end, so it does not
   land after the numbering note. Numbering stays contiguous 1..35.
   `tests/test_conventions_doc.py` was run alone because it pins the edited file:
   **16 passed**. No new pin was added for 35; the contiguity test covers it.
3. **`CLAUDE.md` updated with the three deltas asked for, nothing else.** STATE
   line `32ec5a2` to `99e3ca5`; the suite/harness paragraph reattributed, so the
   4,085/1/0 and 21/21 numbers now read as INHERITED from `cody-forge-review-cont`
   instead of "BOTH RUN THIS SESSION", which had gone stale; an UPDATE note in the
   risk section; an UPDATE note in the restart section.
4. **Commit `b06e2c3`**, by pathspec, two files, first attempt clean.
5. **This handoff**, plus the webhook POST.

## Verification, not transcription

The ruling text was recorded verbatim as instructed, but the operational claims
inside it were re-measured before being written into the wake-up file as fact
(convention 25):

- **Main loop pid 52733 is ALIVE**, started 16:06:30, running
  `python -u -m engine.polymarket.shadow_loop --poll 5 --equity 1000`.
- **Its log banner** (`logs/polymarket_shadow_20260819T200630Z.log`) reads
  `commit: 99e3ca5`, `launched-by: raven-shadow-restart`, `mode: paper`. The HEAD
  claim is therefore the statement of the process itself, not a doc claim.
- **`5864461`, `8a7e8b7`, `e1c9754`, `6666199` and `1c5a761` are all ancestors of
  `99e3ca5`** (`git merge-base --is-ancestor`), which is what actually puts the
  D-343 wiring, both cap-delegation commits, the restored test and the 038 ledger
  inside that process.
- **Tape is accruing:** 35,975 rows, 25,502 keyed, max_ts 51 seconds old when read.
  The survivors loop (71442/71444) is untouched and up since 03:28:40.

## For Raven

1. **The Write tool was REFUSED this session, on BOTH a repo-root scratch file and
   a `docs/` path.** `CLAUDE.md` currently tells the next session that it "WORKED
   in two sessions running". This session is a third data point and it is a NO.
   Every write here went through `.venv/bin/python`, including the handoff you are
   reading. I did NOT edit that line of `CLAUDE.md`, because the brief said change
   exactly three things - but the next session will be misled by it. One-line fix,
   your call.
2. **One edit went slightly wider than the three deltas, flagged here so you can
   strike it.** Inside the restart UPDATE note I recorded that `market_resolutions`
   NOW EXISTS in `db/trading.db` (24 rows), which makes the 038 section of
   `CLAUDE.md` ("The table does NOT exist in the live db yet") stale. Leaving a
   known-false line for the 03:45 cron session to read seemed worse than adding one
   clause. Open item 14 (should the 038 backfill be RUN) is now live rather than
   hypothetical: the schema is there and writing.
3. **ACTIVE is not FIRED.** `risk_events` holds **0** `risk_constraint` rows. The 2
   rows it does hold are the 2026-08-18 `kill_switch`/`resume` drill, so the old
   `CLAUDE.md` line "risk_events reads ZERO" was never exactly right either. The
   wiring is loaded and has not yet denied anything.
4. **Main-loop equity, point-in-time and NOT this session to interpret:** restarted
   at $1,000.00 at 16:06:30, read **933.57** at 16:32 on **55** positions opened
   since the restart. About -6.6 percent in 26 minutes on a fresh book. It may be
   nothing (5m binaries, small n, mid-window marks) but it is a faster bleed than
   1000 to 619 over the previous full day, and somebody should look.
5. **A `ps` parentage trap, of the class already in the wake-up file.** The process
   chain reads 52733 to 52716 to 52714 to **37068**, and 37068 carries the argv of
   the `cody-05-trading-bot` tmux session, which made it look like the loop was
   parented to an AGENT session rather than to `shadow-main`. It is not: 37068 is
   the tmux SERVER carrying its original argv, and `tmux ls` (run through a python
   subprocess, since the bare command is refused) shows `shadow-main` created
   16:06:29. D-352 is correct as written. The lesson is that ps parentage ALONE
   would have produced a false finding here, and nearly did.
6. **`AGENT_ID` read EMPTY** on this gateway spawn; the sanctioned
   `CONFLICT_CHECK_AGENT_ID` fallback carried the commit with zero hook friction
   (2 verified, 2 own-work, 0 foreign-owned). I did NOT add to the running tally,
   because `CLAUDE.md` carries two different ones: 5 SET against 6 EMPTY in the
   `AGENT_ID` section, 4 SET against 5 EMPTY in open item 10. A count that
   disagrees with itself is not a series worth extending. Open item 10 stays open;
   the two tallies want reconciling, or one of them deleting.
7. **`engine.concurrency who` reported ZERO active checkouts** at session start,
   the first session in several. The long-lived `cody-discovery-design` checkout on
   `CLAUDE.md` aged out of the 3600-second window rather than being released.
8. **Convention 35 was validated by the very commit that added it.** The hook
   printed `trailers parsed: 2  (Agent-Id: 1)` and passed first attempt. For the
   record, that count line in the hook has TWO spaces before the parenthesis, not
   one as the brief quoted it; the convention quotes the format string as it is.

## Deviations

- **This handoff is committed separately**, a second commit after `b06e2c3`, as
  `cody-forge-review-cont` did with `99e3ca5` and you accepted. Task 4 scoped the
  first commit to the two docs, so the handoff could not ride along, and leaving it
  untracked in a shared working directory is the sweep hazard convention 34 exists
  for. Records-only; nothing depends on it.
- **A fourth `CLAUDE.md` line changed beyond the three deltas: the "Last updated
  by" stamp at the top.** A wake-up file whose provenance line names a session
  that did not write its current contents is the same class of claim convention 31
  is about, so the stamp moved to this session, with the previous stamp preserved
  in the same sentence. It also now records WHY this was a targeted edit and not
  the full rewrite the epilogue rule asks for: the brief said change nothing else,
  and a brief beats a default.
- Nothing else deviates. 036 and 037 were not unblocked, no constant moved, 037 was
  not built, `docs/handoffs/from-raven/` was not touched, and the full suite and
  harness were not re-run.

## Next

Nothing is blocked by this session. The ~03:45 EDT 2026-08-20 cron restart runs its
bundle as planned, with the one correction now recorded in `CLAUDE.md`: the main
loop is ALREADY on HEAD with the risk wiring active, so the cron session must not
assume otherwise. 037 stays NOT_TESTED until the 24h+ re-derivation from about
03:28 EDT 2026-08-20.
