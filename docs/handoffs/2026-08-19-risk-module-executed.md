# Risk module executed - D-342 recorded, code in tree, NOTHING wired

**Session:** `cody-risk-module`, 2026-08-19, ~07:40-08:15 EDT.
**Brief:** `docs/handoffs/from-raven/2026-08-19-kalman-rulings-risk-module.md`.
**HEAD:** `161b12f`. Two commits this session: `e32bdd7` (code + tests),
`161b12f` (D-342).
**Tree:** clean apart from this handoff and the `CLAUDE.md` rewrite.

## Verification (re-derived, not quoted)

- Full suite: **4,072 passed / 1 skipped / 0 failed** (378s).
  The 3,962 reference in the brief is stale by design - the delta is this
  session's 47 tests plus `cody-038-ledger`'s, which landed mid-session.
- `backtest/validate_harness.py`: **21/21, returncode 0.**
- New file `tests/test_risk_constraints.py`: **47 passing.**

## What was built

Three files, one rename, commit `e32bdd7`.

**`engine/risk/constraints.py`** - the pure evaluator.
`check(open_positions, candidate, equity, limits) -> Decision`. No probability
input, asserted structurally by a test that inspects the signature and every
input dataclass field for forecast-shaped names, and greps the source for
`time`/`sqlite3`/`os` imports. Three caps: per-trade notional, per-event
notional keyed on `(asset_family, window_ts)`, aggregate notional. Unreadable
state fails CLOSED (convention 11) - an exposure we cannot parse denies the
entry rather than being counted as zero, because a zero silently shrinks every
sum a cap is checked against. Every denial names exactly one constraint and
carries the numbers that produced it (convention 20).

**`engine/risk/events.py`** - the only side effects in the module.
Every denial writes a `risk_events` row (`type = 'risk_constraint'`, constraint
name in `details_json.constraint`). A drawdown breach routes into
`engine.halt` - the single definition - and is CONDITIONAL on `is_halted()`,
because unlike the executor's periodic backstop this path runs per entry
attempt, and reminting the HALT file each time would invalidate an ack id a
human is already holding (`write_halt`'s own docstring names this caller).
The denial row is still written every time, so no count is ever lost.

**`tests/test_risk_constraints.py`** - 47 tests: the allow/deny matrix, each
constraint binding and naming itself, same-epoch BTC/ETH/SOL treated as one
correlated bet, different epochs NOT stacking, unreadable-input matrix failing
closed, no-probability-input asserted structurally, determinism and
non-mutation of inputs, a row written on every denial and none on an allow,
the kill-condition harness, and halt single-path (including that the pure
evaluator neither imports nor calls `engine.halt`, asserted via AST).

**`engine/risk.py` -> `engine/risk/__init__.py`** - forced, see below.

## The rename the brief did not anticipate

PLAN section 6 specifies `engine/risk/constraints.py`, but `engine/risk.py`
already existed as a MODULE and a package shadows a sibling module of the same
name. I verified that empirically rather than assuming it. Creating the package
naively would have silently broken `from engine.risk import RiskGate` in
`engine/executor.py` - the live crypto order path. `git mv` to
`engine/risk/__init__.py` keeps the import surface byte-identical (100% rename
similarity) and is pinned by a test.

## Kill-condition status: implemented as CODE, not left as a doc note

> DEAD if, over 30 days, no constraint binds more than 5 times.
> Named harness: `risk_events` grouped by constraint name.

`engine.risk.events.denials_by_constraint(conn, since_ts_ms)` IS that query
(it excludes the `halt_engaged` row so the halt cannot inflate its own count,
and reports never-bound constraints as an explicit `0` rather than omitting
them). `is_decorative(counts)` evaluates the threshold.

**Status today: NOT YET MEASURABLE. Zero rows exist, because nothing is
wired.** The 30-day clock cannot start until activation. What I can report is
the counterfactual from the existing book, which is what the caps were sized
against:

| cap | default | measured book | would bind |
|---|---|---|---|
| per-trade notional | $10 | p50 $6.20, p90 $9.50, max $10.00 (n=2,333) | already binds hard (existing PM gate cap) |
| per-event notional | $30 | peak concurrent p50 $18.76, p90 $43.12, max $76.20 (298 events) | **75 of 298 events (25.2%)** |
| aggregate notional | $60 | concurrent p99 $57.34, peak $76.97 | top ~1% |
| drawdown halt | 25% | book max drawdown **35.99%** | 3 times |

Every number was read read-only off `db/trading.db` this session. I sized the
caps from the book precisely because a cap above the natural range is
decorative, and decorative is the kill condition.

## The per-event cap is the only genuinely new constraint

It is also the best-supported one. Measured: **247 of 298 events (82.9%) span
more than one asset, 173 span all three**, and at the book's worst moment a
SINGLE event held $76.20 of a $76.97 total concurrent book - almost the entire
book was one correlated epoch. The existing `correlation_key` in
`engine/polymarket/risk_gate.py` cannot see this: it aggregates on
`(declared_group, direction)` with per-asset groups, so it pools one asset
across ALL windows and never pools three assets within ONE window.

## What was NOT wired, and what was NOT done

- **`evaluate_and_record` has no caller.** No entry-path wiring, no config key,
  no strategy file, no loop file, no registry entry. Activation is the restart
  AFTER the ONE, per R2.
- **The ONE restart is untouched.** `docs/handoffs/from-raven/2026-08-20-keying-restart.md`
  and `docs/keying-prep/` were not opened for writing and not amended. No cron
  was touched.
- **No loop was restarted, signalled or touched.** Nothing was written to the
  live `db/trading.db` - every query this session was
  `mode=ro`.
- **`db/schema.sql` was NOT modified.** `risk_events` already exists with a
  generic `(id, ts, type, details_json)` shape, so no schema change was needed.
  I used ONE new `type` value rather than one per constraint, to keep the blast
  radius on the dashboard reader and its tests at zero. The `type` column
  comment in `db/schema.sql` does not list `risk_constraint`; I left it alone
  rather than touch a file 038 had just landed in. **Small open item.**
- Nothing from R1 (Kalman), R3 (rho-ranking, quarter-Kelly), R4 (the panel) or
  R5 (monotonicity) was built. R1/R3 are refusals; R4 is "do not wire"; R5's
  monotonicity work is explicitly not this session's.

## TWO THINGS FOR RAVEN. Both deliberately not self-resolved.

**1. The per-trade and aggregate caps duplicate the Polymarket gate.**
`notional_cap_usdc` ($10) and `max_total_exposure_usdc` ($100) already exist in
`engine/polymarket/risk_gate.py`. R2 names all three constraints so I built all
three, but shipping two copies of one control is exactly the failure
`engine/halt.py` was written to end: *"three copies of a kill switch is three
chances for one of them to point somewhere else, and the failure mode is
silent."* **Before wiring, exactly one must be authoritative.** The house
pattern is delegation, not a second gate - the Polymarket gate already defers
its equity tail backstops to `engine.risk.RiskGate` rather than reimplementing
them. Documented in the `constraints.py` docstring. My read: the PM gate should
delegate to `engine.risk.constraints`, and its own two caps should be deleted
in the same change, not left as dead numbers.

Worth noting on its own: **the PM gate's existing $100 aggregate cap NEVER
bound on this book** (peak concurrent exposure $76.97). It is already
decorative by the PLAN section 5 definition.

**2. Any non-decorative drawdown halt would stop the current shadow book.**
Measured on `equity_snapshots`: max drawdown from running peak is **35.99%**.
A halt would have fired 8 times at 10%, 3 times at 25%, 3 times even at 35%;
only a threshold of **>=40% never fires**. I set the default at 25% -
deliberately not decorative - but activating it means accepting that the shadow
measurement book becomes haltable, and halting it stops the measurement that
026, 037 and every calibration read depend on. That is a call for Raven and
Aym. It is a live decision, not a default to accept silently.

## Sequencing evidence (the brief made this session wait on 038)

- At session start the concurrency ledger showed
  `engine/polymarket/resolution_ledger.py` checked out by `cody-038-ledger`
  **68s earlier, CHANGED SINCE CHECKOUT** - live. Phase 1 therefore built only
  new files and touched nothing 038 held.
- **038 landed during this session's suite run**, at `1c5a761` (the ledger) and
  `b028798` (its handoff).
- D-333 guard cleared on all four conditions before any shared file was
  touched: handoff `docs/handoffs/2026-08-19-038-ledger-executed.md` exists;
  `git status` clean of `db/schema.sql`, `shadow_loop.py`,
  `resolution_ledger.py`, `DECISIONS.md`; two consecutive `git rev-parse HEAD`
  reads three seconds apart both `b028798`.
- 038's `docs/DECISIONS.md` checkout still shows open and CHANGED. **It is
  STALE** - 038 committed that entry in `1c5a761` and exited. The hook reports
  open checkouts as advisory for exactly this case.

**Convention 25 bit twice, and the second one is new.** First, the familiar
trap: `ps aux | grep "claude -p"` returns tmux server 37068 carrying its
ORIGINAL 12:25AM argv, which reads exactly like a live sibling - it is this
session's own grandparent (claude 6865 -> tmux 37068). Second, and worth adding
to the canon: **my own grep for a live 038 process matched my own shell command
text**, because the search string appeared in the argv of the command doing the
searching. A `ps | grep` for a sibling will find itself. Only filtering on
`comm == claude` gave the true answer - exactly one claude process, PID 6865,
this session.

## D-342 hash-guard result

H0 `f7c93f0aa85d7fc921bac02b289d3bbf9fad433e74d506484ee4e0ed0883fe79` taken at
read, re-checked immediately before each of four `safe_edit` writes, all held.
Append-only verified mechanically: `git diff --numstat` reported **30
insertions, 0 deletions**. R1-R5 transcribed verbatim; the recording note is
outside the ruling text (convention 31).

## AGENT_ID reading - open item 12 is STILL NOT SETTLED

**`AGENT_ID` read EMPTY** (`os.environ.get('AGENT_ID')` is `None`) on this
Hermes gateway spawn. Both commits went through the sanctioned
`CONFLICT_CHECK_AGENT_ID` channel from a python subprocess - no bypass flag, no
`--author`, no `--no-verify`; both hooks passed, 0 MISMATCH, 0 FOREIGN-OWNED.

That is the **second EMPTY reading on the gateway path** (`cody-forge-reasoner-c2`,
07:02) against `cody-kalman-discuss`'s **SET** reading at 07:5x on the *same*
path. D-341 R4's account - gateway does not export it, tmux does - fits three
of the four gateway readings but not all four. **Open item 12 stays open;
probe with python, never assume.** The Write tool was also refused on this
spawn, so every file here was written with the heredoc fallback.

## Suggested next steps

1. Rule on the two flagged items above - both block wiring.
2. Decide whether `db/schema.sql`'s `risk_events.type` comment should gain
   `risk_constraint` (cosmetic; I left 038's file alone).
3. The activation change itself belongs to the restart AFTER the ONE, behind
   its own tests, and should be a delegation change rather than an addition.
