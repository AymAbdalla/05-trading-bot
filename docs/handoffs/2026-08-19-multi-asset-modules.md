# Handoff: multi-asset modular architecture design

**Session:** `cody-multi-asset` (Opus), 2026-08-19 11:36-11:5x EDT.
**Brief:** `docs/handoffs/from-raven/2026-08-19-multi-asset-modules.md`.
**HEAD:** `a4832f2`, unchanged. **Nothing committed. Nothing in `engine/`,
`strategies/`, `backtest/` or `db/` was touched.** Read-only on code, as the
brief required.

## What was built

One file: **`docs/DESIGN-2026-08-19-multi-asset-modules.md`** (~32 KB, 10
sections, matching the structure the brief specified). Registered through
`engine.concurrency` (checkout/checkin no-op round trip) so a later session's
commit will not hit a FOREIGN-OWNED refusal.

Untracked; **not committed** - the brief did not ask for a commit and no tracked
file was in scope. Raven's call whether it should be.

## The verdict, in four lines

1. **Option A (monorepo, `core/` + `payoff/` + `modules/`) now; Option B on a
   stated trigger.** Rejects Raven's lean toward B-immediately.
2. **The module carve is by PAYOFF SHAPE, not venue.** Three engines (PATH /
   BINARY / CONVEX) + five venue adapters. Crypto, equities and prop futures are
   ONE engine. Aym's five modules cost roughly three builds.
3. **Crypto is module #2, not prop futures** - because it inherits an
   already-validated harness, not because its data looks like Polymarket's.
4. **Options is cut** on D-342 R5's own terms.

## The load-bearing finding

This repo already ran the experiment - Polymarket was bolted onto a candle-based
crypto/equities engine - so the design is an autopsy, not a guess. What held
(halt, risk constraints, ledger, Forge) takes only **notional, time and
identity**. What forked (strategy interface, harness, fill model) touches the
**price path or the payoff**. That is the seam.

## Three things I found that are not design opinions - they are current defects

1. **`backtest/validate_harness.py` contains zero references to Polymarket.**
   Convention 1 says no result is durable unless it exits 0. That gate has never
   covered module #1's payoff engine. `backtest/assertions.py`'s 8 assertions
   (including the MULN/SNDL/BBBYQ quarantine canary) are path-family only, same
   hole. Written up as **Gate 0** in section 6.1.
2. **`engine/risk/constraints.py:139` `asset_family_for_slug` string-parses a
   Polymarket slug.** A Binance `BTCUSDT` perp lands in `UNKNOWN_FAMILY`, so a
   BTC perp and a `btc-updown` UP would stack uncapped in the per-event cap -
   the exact correlated-epoch exposure that cap was measured to stop
   (phi = +0.529, 82.9% of events multi-asset). Needs to become
   `asset_family(instrument)` with the family **declared** by the adapter.
3. **`positions` already carries 13 Polymarket-only columns**, and `signals.tf`
   is written as the literal `'5m'`. Proposed spine + per-family side tables in
   section 3.2.

Defect 2 touches a file wired into the entry path as of D-343, inactive until
the restart-after-the-2026-08-20-one. **Sequencing that is a Raven call, and it
must NOT be added to the ~03:45 2026-08-20 restart**, which is already fully
loaded.

## Convention 25 catches

- The wake-up file says the `signals.tf` `'5m'` literal is at
  `shadow_loop.py:850`. **It is at 877.** Line numbers in `shadow_loop.py` have
  shifted. The doc says so about its own line numbers too.
- I **could not find** a selection-bias test in this repo by that name. The
  brief cites "winners avg -$0.30/trade on unseen instruments" as a current
  lesson; the quarantine/canary half of the old-project lessons IS live code,
  that half is not. Recorded as an honest gap, not as an absence
  (convention 11).

## Not done / deferred

- **No refactor.** The `engine/` -> `core/`/`payoff/`/`modules/` rename is a
  paper plan. My recommendation is to do it at module #2 start, not now - churn
  against a shared working directory with three live processes.
- **Gate 1's number is deliberately blank.** The t-stat and minimum n for the
  Polymarket structural proof are Aym's and Raven's to set. Inventing a
  clearable threshold is convention 17's exact failure.
- I did **not** re-run the suite or the harness. The brief was design-only and
  no code changed; the `cody-suite-baseline` reading at HEAD `2e1184a`
  (4,085/1/0, harness 21/21) is untouched by this session. HEAD is now `a4832f2`
  and I did not re-derive against it.
- Nothing was verified about live processes; no process was touched.

## For Raven to review

- **Section 2.3's extraction trigger** - three conditions, one of which
  (30 days of zero single-module commits to `core/`) is the real test. Is it the
  right test?
- **Section 7: DECISIONS.md and CONVENTIONS.md stay singular under any option.**
  If Aym ever picks B, that constraint has to survive a repo split and the
  mechanism is not obvious. Worth deciding before B, not during.
- **Section 9's six kill conditions** for the design itself, each with a number.
  K6 (Gate 0 gets skipped) is the one I would bet on failing.
- **Section 10: seven open questions for Aym**, of which #1 (accept payoff-family
  over venue?) and #2 (Gate 1's number) are blocking for any roadmap work.

## Environment notes

- `AGENT_ID` read **SET** (`cody-multi-asset`) on this gateway spawn. Tally is
  now **5 SET / 5 EMPTY** on the same path. Still not settled - keep probing.
- Write tool worked on a new file under `docs/`. Zero hook friction; the
  checkout/checkin registration round trip went through clean.
