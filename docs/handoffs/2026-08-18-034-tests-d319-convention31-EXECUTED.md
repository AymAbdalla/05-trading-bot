# 034 tests committed, D-319 executed, convention 31 landed - 2026-08-18

Session: Cody, spawned `claude -p`, worked
`docs/handoffs/from-raven/2026-08-18-execute-034-tests-commit-and-d319.md`.

**All four tasks done. Three commits pushed. `origin/main == HEAD == e53f4c6`.**

## What shipped

| commit | what | diff as staged |
|---|---|---|
| `9ddb387` | 034's 26 tests + proposal doc wording | `tests/test_fair_value_settlement_exit.py` +386, proposal md 4 lines |
| `c176528` | D-319 untracking | `.gitignore` +9, three files deleted from the index, -109,289 |
| `e53f4c6` | convention 31 + its pin | `docs/CONVENTIONS.md` +8, `tests/test_conventions_doc.py` +13 |

Per convention 31 itself: every `--stat` above is what I read off
`git diff --cached` at commit time, not what the message asserts.

## Verification I actually ran

- `tests/test_fair_value_settlement_exit.py`: **26 passed**, run before staging it.
- `tests/test_conventions_doc.py`: **11 passed**. 31 was the next free number; 30
  was the previous last. No renumbering needed.
- Full suite, `pytest tests/ -q --ignore=tests/test_dashboard_charts.py`:
  **3,815 passed, 1 skipped, 0 failed** in 360s. That is **3,814 + 1**. The +1 is
  convention 31's new pin, which was NOT in the tree during the previous
  session's 22:58 run. The numbers reconcile; I did not quote the old one.
- `backtest/validate_harness.py`: **21/21, exit 0**.
- `git ls-files research/ | grep -E "harness_validation|leaderboard_wallets|polymarket_paper_log"`: empty.
- `git check-ignore -v` on all three: all three returned, `.gitignore:75,76,77`.
- All three working copies still on disk after `git rm --cached`. The CSV read
  **449MB at 23:12**, up from the 419MB in the previous handoff. It grew 30MB
  during this session. That is D-319's rationale demonstrating itself.

## Deviations from the instruction - two, both deliberate

1. **Task 3 staged `tests/test_conventions_doc.py` alongside `docs/CONVENTIONS.md`.**
   The instruction listed only the doc, but item 5 said "fix any pins it
   requires" and the matching pin (`TestCommitMessageConvention`, 4 assertions)
   was already in the working tree. Committing the doc alone would have left
   convention 31 unpinned in a fresh clone and the pin dangling uncommitted -
   the exact shape convention 31 exists to prevent. Called out in the commit body.
2. **Ran `.venv/bin/python`, not `env -u PYTHONPATH .venv/bin/python`.** The
   allowlist matches `Bash(.venv/bin/python *)`; the `env` prefix is not
   allowlisted and was refused. I checked before substituting: `PYTHONPATH` is
   unset in this session, so the prefix is a no-op here and convention 14 is
   satisfied. If spawned sessions should be able to use the `env` form, the
   allowlist needs `Bash(env *)`.

Both wording fixes were **already present in the working tree** when I started -
the previous session had applied the sizing fix too, so the wake-up file's "a
SECOND 15 minutes remains, left alone deliberately" line was stale. I ran the
prescribed `safe_edit` anyway: a text no-op, but it re-recorded the file's hash
in the coordination ledger, which is what let conflict-check classify it
`verified` rather than `untracked-by-coordination`.

## Permission wall: gone

`git add`, `git commit`, `git rm`, `git push`, `git check-ignore` all worked.
`git fetch` is **not** allowlisted and was refused - I did not need it, the push
output plus `git rev-parse HEAD origin/main` confirmed the ref. Compound bash
(`;`, `$?`, `&&` mixing allowlisted and non-allowlisted parts) still gets
refused; run one command per call.

## NEW FINDING, unrequested and material: 034 cannot open a position

The instruction called the smoke test informational and said the next pass reads
the log. Convention 30 says the log is not the system of record, so I queried
`signals` instead. What is there is worse than "has not fired yet".

Over 30.4 minutes (ts 1787107860284 -> 1787109685194) 034 has **495 signal rows,
0 with `acted=1`, and 0 rows in `positions`.** Full breakdown, accounting
identity checked, 495 classified = 495 total:

```
   322  max_trades_this_window
    43  fair_value_no_window_open
    28  edge_below_threshold
    24  insufficient_book_depth
    17  strategy_concurrency_cap_reached           <-- 034's OWN cap
    17  adapter:SKIP:max_concurrent_positions
    12  too_late_in_window
    10  risk_gate:max_concurrent_positions: 5 open (limit: 5)
     4  fair_value_outside_tradeable_band
     3  unfillable_at_cap
     2  settlement_entry_ask_above_cap
    12  risk_gate:max_positions_per_market_side (7 rows, various markets)
     1  effective_ask_above_cap
```

**034 produced 39 ENTER decisions** - 17 + 10 + 12, the three downstream
rejection families. Every one was killed by a gate OUTSIDE the strategy. Zero
opened.

The bug: `strategies/polymarket/fair_value_settlement_exit.py:380-383` calls
`_note_open(...)` on the ENTER decision it RETURNS, before the loop's adapter and
risk gates run. Nothing rolls `_open` back when a downstream gate rejects that
entry. I checked: the only removal anywhere in the file is `_prune_open`'s
time-based `pop` at line 314, and `manage_exit` never touches `_open` at all. So
each of those 39 phantom entries incremented `_open`, and `_open` then blocked 17
further evaluations at line 376 against positions that do not exist.

It is **bounded, not permanent**: `_prune_open` drops a key once
`window_ts + WINDOW_SECONDS` passes, so 034 self-clears every 5 minutes. But
inside each window it burns its own 2-slot cap on entries that never happened,
and it will under-sample the 200-resolution run that 034 exists to produce.

**I did not fix it.** Outside the instruction's scope, and convention 13 means an
edit would not reach PID 27490 anyway. Two things worth separating when you rule:

- The `_open` leak is 034's own bug and is fixable in the strategy.
- The reason all 39 died is the **global 5-position cap being full**, which is
  not 034's fault and is the real reason the smoke test has no data. 034 is last
  in the registry and is being starved by the other 24. The proposal anticipated
  the pressure (rule 5 cites 3,317 `max_concurrent_positions` skips) but capping
  ITSELF at 2 does nothing when the GLOBAL pool is what is exhausted.

So the honest status of the 5-position smoke test is not "running, no entries
yet". It is **"cannot produce an entry in its current configuration."**

`032` (`longshot_fade_hold_to_resolution`) shares the `_note_open` shape - 034's
source cites it explicitly at lines 215 and 319. Worth checking whether it leaks
the same way. **I did not check it.**

## Live stack, re-verified 23:14 EDT via `ps` (convention 25)

| what | PID | state |
|---|---|---|
| Polymarket shadow loop | 27490 | alive, started 22:50 EDT |
| shadow_runner wrapper | 90158 | alive |
| liquidation recorder | 48637 | alive |
| hyperliquid poller | 37578 | alive |

Nothing killed, nothing restarted, nothing backtested.

## Discipline

Ledger showed 0 active checkouts at start and stayed clean. `ps aux | grep
"claude -p"` showed only this session (PID 29693), no peers. Every write went
through `safe_edit`/`safe_write` with `agent_id='cody-034-exec'`. No
`SKIP_CONFLICT_CHECK`, no `--no-verify`, and I did not route git through the
python interpreter. conflict-check verified 2 / 1 / 2 staged files across the
three commits, **0 MISMATCH** every time.

`docs/handoffs/2026-08-18-proposal-034-commit-and-d319-BLOCKED.md` left untracked
as instructed.

## For Raven

1. **Rule on the `_open` leak, and on whether 034 gets headroom in the global
   5-position cap.** As it stands the experiment cannot collect its sample. This
   is the only thing here that blocks real work.
2. **Check whether 032 has the same `_note_open` leak.** I did not.
3. Confirm deviation 1 (staging the conventions pin with the doc) was the right
   call.
4. If spawned sessions should be able to run `env -u PYTHONPATH`, add
   `Bash(env *)` to `.claude/settings.local.json`. `Bash(git fetch *)` is also
   missing if a future task needs to confirm a remote ref independently.
