# Verify / commit / restart - EXECUTED

> Verification was mine. The commit and the restart were performed by peer
> sessions mid-flight; I verified both after the fact against runs I did
> myself. I held the restart deliberately - a peer then did it, and it
> landed on the commit I had verified. Both facts are recorded below.

**Session:** `cody-verify-ship` (PID 37069, opus)
**Directive:** `docs/handoffs/from-raven/2026-08-19-verify-commit-and-restart.md`
**Window:** 2026-08-19 ~00:25 - ~01:05 EDT

## Bottom line

- **Task 1 (verify): DONE.** The directive's premise was incomplete. The
  uncommitted D-323 work **broke 26 tests**, which neither dead session
  caught. Found, diagnosed, attributed.
- **Task 2 (commit + push): DONE, but by a peer session, not by me.** Commit
  `7038ad4` is on `origin/main`. I verified its content byte-for-byte against
  a suite I ran myself in an isolated worktree.
- **Task 3 (restart the loop): DONE - by a peer at 00:56:17, not by me.** I
  held it (reasons below, they were valid at the time); a sibling session
  performed it minutes later on the exact commit I had verified. New main loop
  is **PID 41735** on `e033078`, log
  `logs/polymarket_shadow_20260819T045617Z.log`. **D-323 is confirmed live in
  the running process: `strategies: 17 per asset`** (was 19 - the two maker
  strategies are out). Old PIDs 35848 / 35815 / 35849 are gone.
- **Task 4 (epilogue): this file + CLAUDE.md + webhook.**

## Task 1: what I verified

### The diffs matched their DECISIONS entries

| Claim | Verdict |
|---|---|
| D-323 sentinels match the D-322 pattern | YES - `supported_market_types = ('smart_money',)` on both classes, same shape, same reversibility note |
| risk_gate comment fix says 10 | YES - `engine/polymarket/risk_gate.py:114`, comment now agrees with `DEFAULT_MAX_CONCURRENT_POSITIONS = 10` |
| 032 fix uses the BARE slug key | YES - and this is load-bearing. Cap check is `if slug_15 in self._open` (line 679); the ENTER decision stamps `market_slug=slug_15` (line 794), so `position.market_slug` on the fill IS the 15m slug. A tuple key would have silently never matched, exactly as D-324 warns. |
| `_note_open` moved to `manage_exit`, first-sight | YES - lines 838-845, idempotent, runs before every other exit branch |
| `window_ts` fallback present | YES - `_resolve_at_for`, lines 577-595. Imports check out: `Optional` (line 266), `CorridorPairLive.parent_15m_ts` (`corridor_pair_live.py:208`) |
| D-323 / D-324 / D-325 entries exist | YES - all three, `docs/DECISIONS.md` |

### The load-bearing grep (directive Task 1.2)

`_supporting(` has **exactly two call sites** in `engine/polymarket/shadow_loop.py`:

```
1249:        def _supporting(pool, market_type: str) -> list:      <- definition
1269:                strategies=_supporting(_registry(), MARKET_TYPE_CRYPTO_UPDOWN),
1509:                            strategies=_supporting(_registry(), market_type),
```

- `1269` passes the constant `MARKET_TYPE_CRYPTO_UPDOWN`.
- `1509`'s `market_type` is bound only from `space_defs`
  (`shadow_loop.py:1493-1498`) = `MARKET_TYPE_EVENT`, `MARKET_TYPE_SPORTS`,
  `MARKET_TYPE_POLITICAL`.
- Weather does not use `_supporting` at all - it tests membership directly at
  `shadow_loop.py:1452`.

**Neither call site ever passes `'smart_money'`.** Stronger than the D-323
entry claims: `grep -rn "MARKET_TYPE_SMART_MONEY" engine/` returns **zero
hits**, so no engine code routes on that type at all. The sentinel is sound.

### Registry

25 strategies. Indices **2 (BoxBuilder), 10 (FairValueArbHFT), 11
(FairValueArbInverse), 17 (GridHedge)** all carry `('smart_money',)`. Pinned
first eight intact.

## The thing the directive did not anticipate: D-323 broke 26 tests

First full suite run, against the tree as the two dead sessions left it:

```
26 failed, 3794 passed, 1 skipped in 386.80s
```

All 26 in two files - 25 in `tests/test_maker_fill_wiring.py`, 1 in
`tests/test_polymarket_shadow_loop.py::test_maker_quote_never_becomes_an_entry`.

**Root cause.** `build_loop()` injected `[BoxBuilder()]`, but injected
strategies are still filtered through `_supporting(..., MARKET_TYPE_CRYPTO_UPDOWN)`
in the constructor (`shadow_loop.py:1269`). With D-323's sentinel, BoxBuilder
is filtered out, `loop.strategies` is empty, and every test reaching
`loop.strategies[0]` dies on `IndexError`.

This falsifies one sentence in the D-323 entry: *"The code path stays live and
tested; nothing routes to it."* The wiring stayed live, but it stopped being
**tested**, because the tests reached it only through production routing.

**I did not fix these**, and that was the right call: mid-session a peer
(PID 37634, sonnet) appeared acting on
`docs/handoffs/from-raven/2026-08-19-fix-tests-after-d323.md` - Raven had
already diagnosed the same failure and dispatched a session for it. That peer
repaired both files (`build_loop` now restores the injected list
post-construction) and then **died at ~100s without running a full suite and
without writing its handoff**. Its fix is correct: those two files went
75 passed / 0 failed.

## Verification of the actual shipped commit

The tree was being mutated by live peers throughout, so a plain run in the
working directory was not trustworthy - my 3,820-green run overlapped a peer's
edit to `shadow_loop.py` by ~20s (edit landed 00:38:46, run started 00:38:26),
so it could not be cleanly attributed. **I re-verified in an isolated `git
worktree` instead**, which no peer could touch:

| State verified | Result |
|---|---|
| `27a9d84` + the 8 changed files (= the intended commit) | **3,812 passed, 9 skipped, 0 failed** (363s) |
| `e033078` (current HEAD, includes peers' work) | **3,816 passed, 9 skipped, 0 failed** (330s) |
| `backtest/validate_harness.py` | **21/21, exit 0** |

The 9-vs-1 skip difference is untracked local data (`db/`, `research/`) absent
in a fresh worktree - those tests skip, they do not fail. Totals reconcile:
3,812+9 = 3,821 = 3,820+1.

## Task 2: commit and push - done by a peer, verified by me

While I was verifying, a peer committed and pushed the exact work I was
staging:

```
7038ad4 D-323 pause box_builder/grid_hedge, D-324 fix 032 open-leak,
        D-325 caller_feed blocked, 26 tests repaired, suite 3,820 green
```

Per convention 31 I did not trust the message. `git show --stat` confirms
exactly the 8 expected files, and I hashed all 8 blobs out of the commit
against the content I had verified green:

```
COMMIT MATCHES VERIFIED CONTENT EXACTLY
```

`origin/main` **is** `7038ad4`. So the ledger claim in that commit message is
now independently corroborated rather than self-asserted.

I had already reconciled all 8 files through
`engine.concurrency.safe_edit(..., agent_id='cody-verify-ship')` (no-op
transform, content hash unchanged before/after) so the pre-commit hook could
verify them. No `SKIP_CONFLICT_CHECK`, no `--no-verify`.

`main` is currently **ahead of origin by 1** - `e033078`, a docs-only handoff
commit from the opus edge-analysis peer. I left it unpushed; it is not mine.

## Why I did not restart the loop

The directive gates the restart on "only after Task 2 is pushed and the suite
was green". Both are now true, but a restart runs from the **working tree**,
not from the commit - and the working tree is no longer the thing I verified.

At the moment I would have restarted:

```
 M strategies/polymarket/__init__.py            <- THE REGISTRY
 M strategies/polymarket/fair_value_settlement_exit.py
 M tests/... (4 files)
?? strategies/polymarket/fair_value_mirror_fade.py   <- a NEW strategy
?? tests/test_fair_value_mirror_fade.py
```

Three peer sessions were still live (PIDs 40291, 40151, 42688). Restarting
would have imported a **half-written registry carrying a brand-new, unverified
strategy** into the main production shadow loop - convention 13's exact
hazard, at the worst possible moment.

Two other live Raven directives also explicitly forbid it:

- `2026-08-19-fix-tests-after-d323.md`: *"The shadow loop is LIVE - do not
  restart it."*
- `2026-08-19-shadow-env-b.md` (spawned 00:35, **after** my 00:32 directive):
  *"Do NOT touch the main loop (PID 35848) or its DB."* - env B was just
  launched as a controlled A/B against the main loop as the **control arm**.
  Restarting the control mid-experiment changes its code and its continuity.

**I did not TERM anything, did not halt or resume anything, and did not touch
the liquidation recorder (48637) or the hyperliquid poller (37578).**

**UPDATE, ~01:05:** a peer session performed the restart at **00:56:17** while
I was writing this. It landed on `e033078` - the exact commit I had just
verified green in an isolated worktree - so the outcome is the one my
directive wanted, reached by a different hand. Verified from the log banner
(`commit: e033078`) and from the running process, not from a claim:

```
strategies      : 17 per asset, 51 evaluations per cycle
PM SHADOW start: mode=paper equity=$1000.00 poll=5.0s strategies=17 pid=41735
```

**17 is the proof D-323 took effect** - crypto-routed was 21, minus 2 for
D-322, minus 2 more for D-323 = 17. `caller_feed_unreachable ... TLSV1_ALERT_PROTOCOL_VERSION`
also appears in the new log, exactly as D-325 predicts. Env B is separately
alive as **PID 38881** on `db/trading-survivors.db`.

The two costs listed below are therefore **CLOSED, not open** - both D-323 and
D-324 are now live in the running process. I have left the reasoning intact
because the hold was correct on the information I had, and because the race it
describes is the real finding.

### The cost of not restarting, stated plainly

The live loop is running pre-D-323 / pre-D-324 source. Concretely:

1. **D-323 has zero effect until a restart.** `PM_box_builder` and
   `PM_grid_hedge` are still routed and still taking real maker fills - the
   -$54.30 / -$178.16 bleed the pause was ruled for is still accruing.
2. **D-324 has zero effect until a restart.** 032's `_open` leak is fixed in
   git but not in the running process. Its sigma tape warms on a ~5h clock
   from 00:16, so roughly **05:16 EDT** is when the latent leak can start
   costing real slots.

**This is Raven's call, not mine to force.** The restart is one command once
the tree is quiet and someone has verified the registry change:

```bash
kill -TERM 35815          # wrapper; forwards TERM to python, flushes final equity
# wait for BOTH 35815 and 35848 to disappear (kill -0), then:
tmux new-session -d -s shadow-poly "cd /Users/aympulse/aym/projects/05-trading-bot && ./run_polymarket_shadow.sh"
```

## Numbers to re-derive, not quote

- Suite at HEAD `e033078`: 3,816 passed / 9 skipped / 0 failed (isolated worktree)
- Harness: 21/21, exit 0
- Registry: 25, paused at 2 / 10 / 11 / 17
- Machine clock is **EDT**

## Open for Raven

1. **The restart is DONE** (PID 41735, `e033078`, 00:56:17, D-323 confirmed
   live at 17 strategies/asset). Nothing owed here. But note *how* it
   happened: three separate directives told three sessions "do not restart",
   and a fourth restarted it anyway. It landed on a verified commit - that was
   not guaranteed by anything in the process.
2. **`main` is ahead of `origin/main` by 1** (`e033078`, docs only).
3. **Convention 21 is being stress-tested badly tonight.** Four-plus sessions
   ran concurrently in one working directory; one died mid-task leaving
   unverified edits, another committed the work a different session was
   staging. Nobody used the concurrency ledger except me
   (`engine.concurrency who` showed 0 checkouts throughout). This worked out,
   but it worked out by luck.
4. **The D-323 entry's "stays live and tested" sentence is now false** as
   written - the maker path is tested only via a fixture that restores the
   injected list post-construction. Worth a one-line amendment so the next
   reader is not misled.
