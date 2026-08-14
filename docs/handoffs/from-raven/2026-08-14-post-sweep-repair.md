# From Raven: Post-sweep repair sequence

**Date:** 2026-08-14
**Status:** BLOCKED on Aym's confirmation of D-254 (purge all contract rows)
**Prerequisite:** Aym confirms the purge before running this

## Context

The graveyard sweep (PID 63767) and the queued chain (PID 69639) both completed:
- Main sweep finished 03:48 Aug 14
- Incremental pass (v4+CPI+v5) finished 08:01 Aug 14
- Dispersion gate finished 08:57 Aug 14
- Horizon ladder finished 08:58 Aug 14
- PLR finished 08:58 Aug 14

The graveyard now has 535,425 entries with 55 strategies tested. The incremental
pass backfilled v4/v5 under current code, so those entries are clean.

BUT: the original sweep's 12,936 FUTURES rows were generated with pre-D-249 code
(stale contract sizing). Those rows are still in the graveyard and need purging.

## What to do (once Aym confirms)

Run the one-command repair sequence you built:

```bash
nohup bash backtest/run_post_sweep_repair.sh --confirm > logs/post_sweep_repair.log 2>&1 &
```

This will:
1. Wait for any running processes (should be none now)
2. Dry-run the purge into the log
3. Apply the purge (drops 12,936 contract rows, including 51 PASS/PASS_BENCHMARK)
4. Rebuild futures rows under current code (D-249 fix included)
5. Emit a judge evidence pack

After that, read all five outputs together as designed:
1. P0.3 graveyard control run
2. Constraint sweep
3. Dispersion gate (full)
4. Horizon ladder
5. PLR

Use `env -u PYTHONPATH python3` if running from an agent-spawned session (D-257).

## What I reviewed from your session 2 handoff

Good work across the board. Three things stood out:

1. The judge.py bug (D-255) was a real correctness issue. "Could not read" returning
   as "DURABLE, entries: 0" is exactly the convention 11 failure mode, one layer up.
   The fix (GraveyardUnreadable + UNREADABLE status) is correct. The follow-up audit
   (D-258) finding the same pattern in sub-sections was thorough.

2. The constraint sweep teardown (D-256) was honest and well-reasoned. Non-monotonic
   + two-strategy concentration = NOT SUPPORTED, not disproven. Correct call keeping
   the PRELIMINARY doc.

3. The decision NOT to kill the running sweep was right. 96% good work vs discarding
   it all to fix a bucket that needs rebuilding anyway.

One note: the COST_MODEL_VERSION bump issue (D-254) is a process gap. The fix shipped
without a version bump, and the "never pool across cost_model_version" rule is blind
to it. Worth a convention addition so this doesn't recur.
