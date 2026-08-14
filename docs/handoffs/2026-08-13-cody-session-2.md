# Handoff - Cody session 2, 2026-08-13 late evening

**For:** Raven (review), Aym (two decisions owed, neither blocking tonight)
**Session shape:** triage, not building. The assigned work was "read the sweep
results"; the sweeps are not done, so this is mostly what I found while
checking why, plus the D-249 follow-up tool that was the assigned fallback.

---

## What I did

1. Established what is actually running and what finished.
2. Found the sweep in flight is executing stale code, and scoped the damage.
3. Built `backtest/purge_stale_futures.py` - the D-249 follow-up (armed, not run).
4. Ran `agents/judge.py`, which surfaced a correctness bug in judge.py; fixed it.
5. Audited for that same bug class elsewhere; found and fixed the second-order
   case in judge.py's sub-sections (D-258). No other instances in the repo.
6. Wrote 15 tests for the purge tool before it ever touches real data (D-259).
7. Wrote `backtest/run_post_sweep_repair.sh` - the whole next sequence as one
   deliberate command. Not armed (D-260).
8. Wrote `docs/handoffs/sweep-results.md` (the requested summary).
9. Logged D-253 through D-260 in DECISIONS.md.

## Files created / modified

| File | What |
|---|---|
| `backtest/purge_stale_futures.py` | NEW. Drops contract rows so they rebuild under the D-249 fix. Dry-run default, refuses to run mid-sweep, atomic write, backs up first. |
| `tests/test_purge_stale_futures.py` | NEW, 15 tests on the destructive edges - scope, inert dry run, guard fails closed, backup integrity, no stray `.tmp`. |
| `backtest/run_post_sweep_repair.sh` | NEW. purge -> rebuild -> judge, gated behind `--confirm`. Waits for the chain too, not just the sweep. |
| `agents/judge.py` | `load_graveyard` no longer conflates unparseable with empty (`GraveyardUnreadable`, `UNREADABLE` status); sub-section failures now reported via a `degraded` field instead of silently nulled. |
| `tests/test_judge.py` | Replaced the test asserting the buggy behaviour; added 4. |
| `docs/handoffs/sweep-results.md` | NEW. The five-output summary, honest about 4 of 5 not existing yet. |
| `docs/DECISIONS.md` | NEW v8 section, D-253..D-260. |
| `CLAUDE.md` | Rewritten per the epilogue rule. |

Tests: **559 passed, 1 skipped** (543 before my additions, +16). See the
correction below about the 547 figure.

---

## The three findings that matter

### 1. The 6-hour sweep is running 16:01 code

Python snapshots source at import. The sweep started 16:01; `cost_model.py`
(the D-249 sizing fix) was modified 17:45, v4 at 16:19, v5 at 16:45. So the run
in flight has none of them.

Not inferred - the log says "49 new strategies" and the graveyard header says
`strategies_tested: 49`, exactly 28+7+9+5 with v4's 3 and v5's 2 missing.

I **did not kill it** (D-253). Only FUTURES/OPTIONS route through contract
sizing, which is 12,936 of 287,826 rows; the other ~96% is good work and the
futures bucket has to be re-run either way. Killing it trades 96% good output
for nothing.

The nastier half: the D-249 fix shipped **without a `COST_MODEL_VERSION` bump**,
so pre-fix and post-fix rows both read `'2026-08-13'`. The "never pool across
cost_model_version" convention is blind to this. That is why the purge tool
drops *all* contract rows instead of trying to date-split them - no metadata
supports the distinction (D-254).

### 2. judge.py was reporting confident nonsense

It returned **`status: DURABLE, entries: 0`** against a 287k-entry graveyard.
`load_graveyard` caught `json.JSONDecodeError` and returned `[]`, so a read
landing mid-`json.dump` (the sweep rewrites 12MB after every ticker) became "no
evidence" wearing a DURABLE stamp.

This is convention 11's exact failure - "could not run" recorded as "ran and
found nothing" - one layer up, at the evidence pack instead of the verdict. The
runbook's claim that judge is "safe to run at any time, including while a sweep
is writing" was true about not corrupting the sweep and false about producing
correct output. Fixed (D-255).

`research/judge_evidence_pack.json` currently on disk is the pre-fix empty pack.
It should be ignored and regenerated, not read.

Auditing for the same bug class found one more instance, in judge.py itself
(D-258): `assertions.run_all` and `summarize_graveyard.summarize` take the
graveyard *path*, so they re-read it independently - two more partial-write
chances - and both silently nulled their section on failure. `distinct_findings`
comes from that summary, which convention 2 requires citing, so the failure
mode was a pack with no multiple-comparisons correction and no sign that one
was missing. Now reported in a `degraded` field. Nothing else in the repo has
the pattern.

### 3. The constraint sweep's own conclusion is wrong

Its DIAGNOSTIC says tightening the gate "is selecting for something real."
It isn't, on the evidence in its own JSON:

- **Not monotonic.** AGGRESSIVE -$0.1793/trade, BASE -$0.4543, CONSERVATIVE
  +$1.5380. The first tightening makes it worse. The headline +1.7174 is the
  endpoints subtracted with the middle ignored.
- **Two strategies.** dca_7 (195 trades) and dca_14 (87) are 5.6% of
  CONSERVATIVE's trades and **78.5% of its profit**. Remove them: +1.5380 ->
  +0.3502. V2_expiry_pin is 15 trades at $59.51/trade; rsi_extreme is 3 trades.

Recorded as NOT SUPPORTED rather than disproven (D-256) - the experiment is
underpowered at the conservative end, which is a statement about the experiment.
The PRELIMINARY doc stays PRELIMINARY.

---

## Corrections to the record

- **Test count.** CLAUDE.md said 547 passing. Actual is 543 passed + 1 skipped,
  544 collected, zero errors, nothing failing to collect. My change was net +2
  tests, so the gap predates this session; the 547 appears to have been wrong
  when written. I put the verified number in the rewritten CLAUDE.md rather
  than restating 547. Worth a glance if anyone remembers where 547 came from.
- **Futures row count.** CLAUDE.md said "79,642 futures rows" need re-running.
  The current graveyard - restarted fresh at 16:01 - has 12,936. The 79,642
  figure belongs to the older/archived graveyard, not this one.
- **`ps aux | grep python` misses the sweep.** The interpreter is capital-P
  `Python`. My first check wrongly concluded the sweep had died. Use
  `pgrep -f run_incremental_graveyard`.

---

## Deliberately not done

- **Did not run the purge.** It refuses while the sweep is alive, correctly -
  the runner would clobber it on its next per-ticker save. Armed for after.
- **Did not kill or restart the sweep.** Reasoning above; that is Aym's call if
  he disagrees.
- **Did not bump `COST_MODEL_VERSION`.** After the purge every surviving
  contract row is post-fix by construction, and a bump would falsely mark ~275k
  unaffected EQUITY/ETF/CRYPTO rows as stale.
- **Did not touch the queued chain.** Checked rather than assumed: none of
  `dispersion_gate.py`, `run_horizon_ladder.py` or `cross_sectional.py` reads
  `v0_graveyard_full.json`. dispersion_gate only imports the ticker-universe
  constants from the runner and writes its own output file. So steps 3/4/5
  compute fresh from price data and are **not** contaminated by the stale
  futures rows - letting the chain finish before purging is safe, not merely
  convenient. (It also picks up current code, so it sees all 54 strategies.)
- **Did not re-run judge for a real pack.** Pointless until the graveyard is
  final and purged.

## Owed by Aym (unchanged, plus one)

- Rotate Alpaca key (open since v1 audit)
- First supervised paper run + kill-switch drill
- Live Binance.US fee verification (D-236)
- Ratify D-217's 11 SOUL rules (D-244)
- **New, low stakes:** confirm the purge-all-contract-rows call (D-254) is what
  he wants before I run `--apply`. It discards 51 PASS/PASS_BENCHMARK futures
  rows - which is the point, since inflated sizing is what would manufacture a
  passing futures row, but it is a deletion and he should see it named.

## Next session, in order

Steps 2-4 below are now one command, once Aym confirms the purge:

```bash
nohup bash backtest/run_post_sweep_repair.sh --confirm > logs/post_sweep_repair.log 2>&1 &
```

It waits out both the sweep and the chain, dry-runs the purge into the log,
applies it, rebuilds, and emits a judge pack - aborting before the rebuild if
the purge fails. Or do it by hand:

1. Check PID 63767; when gone, let the chain finish 3/4/5.
2. `purge_stale_futures.py` dry run -> `--apply`.
3. `run_incremental_graveyard.py` - rebuilds futures *and* backfills v4/v5 in
   one pass, under current code.
4. Re-run `agents/judge.py` for a real evidence pack.
5. Read all five outputs together.

**Do not edit `run_queued_chain.sh` while PID 69639 is running it.** Bash reads
a script by byte offset as it executes, so editing a live script can make it
run garbage. That is why the repair sequence is a new file (D-260).

Run python as `env -u PYTHONPATH python3` from any agent-spawned session
(D-257): Hermes leaks its 3.11 venv onto `PYTHONPATH` and numpy fails to import
in a way that looks like a broken install. The machine is fine; only spawned
sessions see it.
