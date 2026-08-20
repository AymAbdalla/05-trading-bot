# Keying restart session: code BUILT, the ONE restart HELD on a drawdown halt

**Session:** `cody-keying-restart`, 2026-08-20, spawned by Hermes cron
`b4b677c33385` at **03:45:30 EDT** exactly as scheduled (pid 93117, parent
tmux 37068).
**Brief:** `docs/handoffs/from-raven/2026-08-20-keying-restart.md`.
**Identity:** `AGENT_ID` read `cody-keying-restart` - **SET** on this gateway
spawn, no `CONFLICT_CHECK_AGENT_ID` fallback needed. Tally moves to **8 SET /
12 EMPTY**.

## THE HEADLINE: the book halted itself 24 minutes before this session started.

`HALT` at the repo root, written **03:21:42 EDT 2026-08-20**:

    {"halt_id": "b7bd22a8", "ts": 1787210502675,
     "reason": "auto: portfolio drawdown 0.4011 exceeds 0.4000"}

This is the automatic risk backstop firing, not a human halt. The main loop
logged it at 03:21:42 and env B picked it up at 03:21:45. **Both loops are
ALIVE and blocking entries** - the `halted` counter was climbing in both
(253 and 174 by 03:45).

**It is the PAPER book. No real money moved.** Peak equity USD 1,027.96;
USD 614.01 at the halt (drawdown 0.4011); USD 652.41 at 03:42 (drawdown
0.3653, already back under the line). The halt persists across restarts by
design and clears only via `botctl.py resume --ack b7bd22a8`, which is a
human decision. **I did not clear it and did not try.**

Worth Aym eye: **the 0.40 limit is already a widened one.**
`engine/risk/constraints.py` sets `max_drawdown_frac=0.25` by default and
`shadow_loop.py` overrides it to 0.40 for this book, with an in-code comment
recording that the book historical worst was 35.99 percent. The book has now
gone past the widened line too.

## What I did NOT do, and why

**The ONE restart did not happen. It is HELD, not skipped.** Three reasons,
heaviest first:

1. **A restart would orphan the evidence.** D-353 records that a restart
   orphans every open position, and the orphan sweep it ruled for is still
   unimplemented. The open book right now IS the drawdown incident.
   Restarting during it destroys the record of what caused the breach, and
   that is not reversible.
2. **The restart could not have been verified, so it would have been spent
   for nothing.** The kill-switch check sits at the TOP of the entry path
   (`shadow_loop.py`, before any leg is priced): under a halt the loop
   records a `halted` skip and never reaches the entry writer. The design
   own verifications V5 (corridor entry rows read `mixed`) and V6 (positions
   join back to a 15m signal) both need entries. D-339 schedules ONE
   restart; spending it on an activation nobody could check is worse than
   waiting.
3. **Task 0.3 failed on its own terms.** The tree was not clean: `HALT` was
   untracked. R8 says a dirty file that is not an `engine/risk/` file is a
   real guard failure, to be reported rather than worked around.

The brief anticipated this shape of answer - "If the gate fails, WAIT, then
report - do not force it." This is that report.

**Consequently NOT done, both blocked by the hold, both carried forward:**

- **R4, env B whitelist corrections.** They only bind on an env B restart.
- **R7, the 038 backfill and first coverage baseline.** R7 sequences it
  AFTER "loop up, keying verified", which never arrived. Unrun on both
  databases; D-354 R4 stands.

**Also untouched, deliberately:** `config.yaml`, the registry,
`docs/CONVENTIONS.md`, the proposal files, the forge briefs, `market_tape`
(beyond the read-only V7 query), both live databases (no writes), the
wallet, any live path. No orphan sweep. No second halt path. Nothing was
signalled to either loop.

## Task 0 guard, measured

| gate | result |
|---|---|
| 0.1 complement window warm | **PASS** |
| 0.2 no sibling `claude -p` | **PASS** |
| 0.3 `git status --porcelain` clean | **FAIL** - `?? HALT` |
| 0.4 two identical HEAD reads | **PASS** - `acb37ad` twice |

**0.1, the gate the whole session was time-locked on, PASSES with room.**
The V7 query on `db/trading.db`:

    SELECT MIN(ts), MAX(ts), COUNT(*) FROM market_tape
     WHERE complement_id IS NOT NULL;
    -> 55,964 rows, 2026-08-19T07:28:36Z .. 2026-08-20T07:46:34Z
    -> span 24.299 h

The minimum is still the 03:28:34 EDT 2026-08-19 restart, so **the window
closed INTACT and was never reset**. 026/037 keep their instrument. (The
prep doc quoted 4,922 complement rows; it is 55,964 now. Both are
point-in-time - the loops were live under every read this session.)

**0.3 is the only failure, and its cause is the halt.** No `engine/risk/`
file was dirty - that sibling work had already committed - so R8 did not
fire on its own terms. Its ELSE branch fired instead, on `HALT`.

## What WAS built (all of Tasks 1, 2, 3, 5)

All of it is **INERT until a restart** (convention 13: Python snapshots
source at import, and both running loops imported theirs yesterday). Landing
the code without restarting is safe by construction, and that is why the
build went ahead while the restart did not.

### Task 1: the 15m signal keying (design option A, additive)

- `signals.market_duration TEXT`, **nullable, NO DEFAULT**, in
  `db/schema.sql` and in a guarded `ALTER TABLE` migration
  (`_migrate_signals_duration_column`) that runs before `executescript`.
- `record_signal` takes a keyword-only `market_duration=None` and writes it
  verbatim. **None becomes NULL, never `5m`.**
- All three writers key their rows: the skip path, the taker entry path
  (collapsed from the per-leg markets, which is what produces `mixed`), and
  the maker path (off the resting order own market).
- `pair` and `tf` are untouched. Option B was not implemented by accident;
  a test asserts it.

**Measured, not assumed:** the `ALTER TABLE` was timed against a synthetic
**700,000-row** table (the live table order of magnitude) at **0.0004 s**,
backfilling **zero** rows. The design header-only claim holds.

### Task 2: the calibration tape (029 condition (b), R1 narrow scope)

- Two new tables, `calibration_tape` and `calibration_resolution`. Nothing
  existing was altered; `market_tape` was not touched (R5).
- `sample_calibration_tape` writes one row per token per poll: 3 assets x
  {5m, 15m} x 2 outcomes = **12 tokens/cycle**, the narrow R1 scope and
  nothing wider.
- `stamp_calibration_resolutions` is write-once via `PRIMARY KEY (token_id)'
  plus `INSERT OR IGNORE`; a second, DIFFERENT resolution is counted into
  `health` rather than silently overwriting the first.

**The sampler costs NO extra network.** `build_context` already fetches both
books for both markets through `fetch_orderbook`, so every price on the tape
comes off the CLOB book and `market.raw` is never read. The D-339 gamma trap
(0.63/0.64 summary against a 0.06/0.08 live book) is closed by construction,
not by discipline. It runs AFTER the evaluation phase so `selected` is a
fact about the cycle rather than a guess from the registry, and `selected=0`
is WRITTEN, never omitted.

### Two deviations from the design docs. Both deliberate, neither silent.

**(1) The skip path needed a DECLARATION mechanism the design left open.**
Design 3.3 says a skip takes its duration from "the strategy own declared
scope", but **no strategy declares anything today**, and verification V4
requires `PM_longshot_fade_hold_to_resolution` to read 100 percent `15m`
**including its skip rows**. Reading the slug cannot deliver that: the slug
recorded on a skip is always `ctx.market`, the 5m market, even for a
strategy that only ever looks at the 15m book.

So I added `PolymarketStrategy.market_duration_scope` (default None, the
same opt-in shape as the existing `supported_market_types`) and declared it
on the three strategies that read `ctx.market_15m`: `15m` on longshot_fade,
`mixed` on `PM_corridor_collector` and `PM_corridor_pair`. Undeclared
strategies fall back to reading the duration off the recorded slug - a true
statement about that row, not a default - and where neither can say, the
answer is NULL.

That fallback has an obvious fragility: a FUTURE 15m strategy that forgets
to declare would be keyed `5m` silently, which is the original bug wearing a
new column. **Closed by a static test** that scans the strategy package and
fails the suite if any file referencing `ctx.market_15m` carries no
declaration.

**(2) The resolution stamp reads each market by its own slug, not through
`resolved_windows_checked`.** Spec section 5 names that helper, but it is
`get_updown_5m_checked` underneath and **cannot see a 15m market at all** -
and the 15m arm is half of what this tape exists to measure. The stamp uses
`get_market_by_slug_checked` per pending token instead, and KEEPS the
failure taxonomy the spec asked for: `read_failed`, `not_listed`,
`unresolved` and `not_binary` each land in `health` under their own name.

### One defect found and fixed inside R3

R3 says put `market_duration` in `agents/forge_shadow_eval.py` explicit
select list. Doing that **literally** turns `read_decisions` into an
`OperationalError` against any database that has not migrated - and **env B
has not**, verified this session: `market_duration present: False` on
`db/trading-survivors.db`, which keeps no such column until its own restart.
Archived databases are in the same position. The column is therefore
selected **conditionally** off `PRAGMA table_info`, reporting None where it
does not exist - which is the same thing the column says about a pre-key
row. Two tests cover both paths.

## Files changed

    db/schema.sql                                       signals column + 2 tables
    engine/polymarket/assets.py                         MARKET_DURATIONS, market_duration_for_slug
    engine/polymarket/shadow_loop.py                    migration, writer, 3 call sites, sampler, stamp
    strategies/polymarket/base.py                       market_duration_scope declaration
    strategies/polymarket/longshot_fade_hold_to_resolution.py    15m
    strategies/polymarket/corridor_collector.py         mixed
    strategies/polymarket/corridor_pair_live.py         mixed
    agents/forge_shadow_eval.py                         R3, migration-tolerant
    tests/test_critic.py                                R2 fixture
    tests/test_forge_shadow_eval.py                     R2 fixture
    tests/test_vault_refresh.py                         R2 fixture + 3 positional inserts
    tests/test_market_duration_keying.py                NEW
    docs/DECISIONS.md                                   D-357

**R2 needed more edits than it names, and the audit undercounted them.**
Hand-written fixtures also carry POSITIONAL `insert into signals values`
statements with twelve placeholders, which raise against a 13-column table.
The consumer audit flagged ONE such insert (`test_vault_refresh.py`) and
called it safe BECAUSE the fixture was isolated - R2 removes that
isolation. It **missed a second**, in `test_forge_shadow_eval.py`
`_build_db`, which took the full suite from green to **15 failures** before
it was found and fixed. Four positional inserts in total gained a
thirteenth value (three in vault_refresh, one in forge_shadow_eval); the
forge one writes None, since a fixture row predating the key is exactly
what NULL means.

**Lesson worth keeping: adding a column to a hand-written fixture is never
a one-line DDL change.** Grep for positional inserts against that table in
the SAME file before assuming the fixture is done.

## What Raven and Aym need to decide

**1. The drawdown halt itself. This is the live question.** The paper book
breached a limit that had ALREADY been widened from 0.25 to 0.40 against a
historical worst of 35.99 percent. Options, none of which I took:
resume via `botctl.py resume --ack b7bd22a8` and keep measuring; leave it
halted and treat the open book as the forensic record; or widen again, which
would be the second widening and should probably not happen without a
reason that is not "it keeps firing".

**2. When the ONE restart happens.** The code is on disk and inert. Whoever
runs it should know that a restart while halted cannot verify V5 or V6, and
that it orphans every open position (D-353, sweep still unimplemented). The
natural order is: decide the halt, THEN restart, THEN run the design section
6 verifications, THEN R4 (env B whitelist) and R7 (038 backfill).

**3. The restart still carries everything it carried before**, plus this
session code: the `signals` keying, the calibration tape, the env B
whitelist corrections, and the `dip_arb` kill from D-356 (still INERT for
the same convention-13 reason).

## Verification queries for whoever runs the restart

Record the changeover timestamp **T** = the restart time. Every query below
depends on it. Full set in `docs/keying-prep/15m-keying-design.md` section 6.
The pre-restart baseline, measured this session, read-only:

| | env A (`trading.db`) | env B (`trading-survivors.db`) |
|---|---|---|
| `signals` rows | 1,178,690 | 305,828 |
| `pair LIKE %-5m-%` | 884,640 | 231,690 |
| `pair LIKE %-15m-%` | **0** | **0** |
| distinct `tf` | `5m` only | `5m` only |
| `market_duration` column | absent | absent |
| `calibration_*` tables | absent | absent |

All point-in-time; both books were live under every read. **The audit
quoted 699,660 signals rows on 2026-08-19 - it is 1,178,690 now. Do not
read that as a discontinuity; it is 25 hours of a live loop.**

V1 (`ts < T AND market_duration IS NOT NULL` MUST be 0) and V3
(`ts >= T AND pair LIKE %-15m-%` MUST be 0) are the two that catch the
mistakes worth catching: a leaked DEFAULT and an accidental option B.

## Honest gaps

- **Nothing in this session was verified against a running loop.** Every
  claim about what the loop will WRITE rests on unit tests and reading, not
  on observed rows. The design own V1-V6 are unrun by construction.
- **`selected` has never been exercised against real fills**, only against a
  stubbed cycle.
- **The 48h instrument kill condition** (spec section 9: fewer than 500
  tokens with both a tape row and a resolution row means NOT_TESTED) has not
  started, because the clock starts at the restart.
- I did not touch the three test files that would need `market_duration` if
  a reader ever REQUIRES it; they now have it (R2), but the reader was made
  tolerant anyway, so neither depends on the other.

