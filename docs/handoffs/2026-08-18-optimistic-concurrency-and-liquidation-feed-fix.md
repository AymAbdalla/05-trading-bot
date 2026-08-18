# 2026-08-18 - Optimistic concurrency control, and the liquidation recorder fixed

**Session:** Cody. **Status:** built, tested, both feeds running.
**Written through `engine.concurrency.safe_write` itself** - this file is the
first real dogfood of the module and its row is in `file_coordination`.

---

## 1. `engine/concurrency.py` - hash-before-write

Optimistic concurrency control for a tree that several agent sessions share.
`checkout()` snapshots content + SHA-256, `checkin()` re-reads and refuses if
the hash moved, handing back a unified diff of **what the other agent did**.

API: `checkout`, `checkin`, `release`, `safe_write`, `safe_edit`,
`who_is_editing`, plus `ensure_schema`, `hash_file`, `rel_path`.
CLI: `env -u PYTHONPATH python3 -m engine.concurrency {who,hash,init}`.

Design calls worth reviewing:

- **The advisory `flock` is held only across verify-then-write**, never across
  your work. Holding it across the work would make this pessimistic locking and
  a crashed agent would wedge the file for everyone. Without it the hash check
  is a textbook TOCTOU race; `test_concurrent_checkins_produce_exactly_one_winner`
  runs two real threads through a barrier and pins one winner, one loser.
- **A lock timeout does not raise - it proceeds unlocked and logs.** The hash
  check still runs, so the degraded case is the millisecond race, whereas
  raising would let one stuck holder block every other agent's writes.
- **Lock files live in the system temp dir**, keyed by a hash of the absolute
  path, not next to the target. Lock files in the tree get committed by
  accident. Honest cost: a temp sweeper could unlink one mid-flight and a later
  opener would take a different lock. Degrades to the unlocked race, never to a
  lost write.
- **A database failure never blocks a write.** The file is the truth, the table
  is the record. Failures are counted in `LOG_FAILURES` and logged at WARNING.
  Cost, stated not buried: `who_is_editing()` then under-reports.
- **The write happens BEFORE its log row.** A crash between the two leaves a
  written file with no row (a missing record of a real event) rather than a row
  claiming a write that never happened. Under-claiming beats over-claiming.
- **Absent is not empty.** `hash_file` returns `None` for a missing file and a
  real digest for an empty one, so a checkout of a file somebody then deleted
  raises instead of silently resurrecting it (convention 11).
- **Reads are binary.** Universal-newline reading would rewrite CRLF in memory,
  re-encode to different bytes, and make every checkin on such a file look like
  a foreign edit.
- **Permission bits are carried across the rename.** `mkstemp` creates 0600 and
  a silently un-executable `run_*.sh` after an edit is a confusing failure.
- **`who_is_editing` pairs by COUNT**, not "latest action wins" - two checkouts
  and one checkin correctly leaves one open.

### The limitation, stated plainly

It protects against any writer that goes through the module. Against one that
does not - a bare `open(p,'w')`, an editor, `git checkout`, or Claude Code's own
Write/Edit tools - it gives **detection, not prevention**: your checkin refuses
and shows you their diff. That distinction is the whole honest claim.

**This bit us during the session itself.** `CLAUDE.md` changed on disk between
my read and my edit, from one of the other live sessions. The edit applied, but
the harness warned the file had other changes not in my context - which is the
exact scenario this module exists for.

Tests: `tests/test_concurrency.py`, 64 tests. The load-bearing one is
`test_a_conflict_does_not_clobber_the_other_agents_write` - detecting a conflict
is worth nothing if the write happened anyway.

## 2. Git pre-commit hook (BUILT, NOT INSTALLED)

- `scripts/install_conflict_hook.sh` - installer, idempotent, `--uninstall`,
  `--status`. Uses `git rev-parse --git-path hooks` (not hardcoded
  `.git/hooks`, which breaks in worktrees) and honours `core.hooksPath`. Backs
  up a foreign pre-commit rather than destroying it; refuses to uninstall one
  that is not ours.
- `scripts/pre-commit-conflict-check` - the logic, runs standalone.

Behaviour: active checkouts **warn but allow**. A staged file whose hash does
not match the last coordinated write **refuses the commit**. Files with no
coordination row are `untracked-by-coordination` and allowed. It hashes the
**staged blob** (`git show :<path>`), not the worktree, and says so when the two
differ. Categories are counted with an asserted accounting identity
(`total == verified + untracked + MISMATCH + unreadable`).

Two judgement calls to review:
1. An unreadable table classifies staged files as `unreadable`, **not** as
   `untracked-by-coordination` - the latter reads as benign, and "could not
   verify" must never masquerade as "verified".
2. Only `MISMATCH` blocks. `unreadable` warns and allows.

**I did not install it.** Eight `claude -p` sessions are live in this tree and a
commit-blocking hook is not something to switch on under them unilaterally.
To install: `./scripts/install_conflict_hook.sh`. Bypass:
`SKIP_CONFLICT_CHECK=1` or `git commit --no-verify`.

## 3. Liquidation recorder - Binance removed, Hyperliquid REFUSED

### Binance: removed from the run path

Not in `SUPPORTED_EXCHANGES`; `--exchanges binance` now exits 2 with the
measurement printed. `parse_binance_frame` and its side-mapping are KEPT, marked
archive-only, so archived fixtures stay readable and the existing side-inversion
tests keep passing - same reasoning the legacy Bybit topic parser already had.

### Hyperliquid: there is no liquidation feed to add

The task asked to add one. **It does not exist**, and I did not fabricate it.
Measured (full log: `research/hyperliquid/liquidation_source_probe.md`):

- `POST /info` with `liquidations` / `recentLiquidations` / `allLiquidations`
  → HTTP 422, and the error body is **byte-identical** to the one returned for
  the invented type `zzzNotARealType`. Not deprecated, not gated - never heard
  of it.
- All five liquidation WS subscriptions → `{"channel":"error"}`, while `trades`
  ACKed on the same socket in the same run.
- Public `trades` complete key set over 197 trades:
  `[coin, hash, px, side, sz, tid, time, users]`. **No liquidation flag.**

A chased-and-killed false lead: 28% of trades have `hash == 0x000...0`, which
looked like a marker. Matched against `userFills`, every one was an ordinary
maker fill (`crossed: false`, negative fee, no `liquidation` key). Recording
those as liquidations would have manufactured a tape at 28% of volume.

`userFills` **does** carry a real `liquidation` object and is unauthenticated,
but it is address-scoped with no `allFills` - a biased sample, not a tape. All 7
HLP backstop vaults yielded 9 liquidation fills, newest 24 days old. If anyone
builds it: it needs its own table, dedup on `(hash, liquidatedUser, coin)`, and
its own side rule - the annotation lands on the **maker's** fill so the sign
flips **twice** relative to Bybit. Dropping in the Bybit mapping would silently
invert every row.

So: **the venue-wide tape is Bybit-only, 3 symbols.** `db/schema.sql` now says
so at the `liquidations` table.

### Silence is not health

`SILENCE_ALERT_SEC = 900`. `_heartbeat` now warns when a feed has been
CONNECTED that long having parsed zero events. This is the Binance lesson made
mechanical - its socket connected, logged `CONNECTED, reconnects=0`, and
recorded nothing. Worded as an instruction to check, not a verdict: Bybit does
go genuinely quiet.

### Second site fixed (convention 23)

`run_liquidation_recorder.sh` had its own `EXCHANGES=binance,bybit` default.
Now `bybit`, with the banner printing DEAD venues and their reasons.

## 4. Feed status - both RUNNING

| job | PID | state |
|---|---|---|
| liquidation recorder | **48637** | alive, Bybit CONNECTED + subscribe ACK, **0 rows** |
| hyperliquid poller | **37578** | alive, **1,157 rows** and climbing (988 → 1157 this session) |

Confirm with `ps -p`, not with this table (convention 25).

**`liquidations` is 0 rows and that is the honest state.** Bybit is quiet; it is
not broken. The subscribe ACKed. If it is still 0 after 15 minutes connected,
the new silence alarm will say so in the heartbeat - that is the whole point of
adding it.

**It fired, on the live feed, and this is the log line** (convention 22, not a
docstring claim):

```
06:40:30 WARNING liquidation_recorder: bybit: CONNECTED 959s and has parsed
ZERO events. A healthy socket recording nothing is exactly how the Binance
geoblock presented. Check the venue is genuinely quiet before treating this
uptime as data.
```

Under the old build this same state printed `connects=1 reconnects=0` and
nothing else, forever.

I did **not** start a second Hyperliquid poller; 37578 was already running.

## 5. Schema

`file_coordination` added to `db/schema.sql` with two indexes, and registered in
`tests/test_schema_matches_feed_modules.py::FEED_TABLES` so the two-copy DDL
cannot drift. Created in the live db via `python3 -m engine.concurrency init`.

## 6. Convention numbering - needs a ruling

The task asked for this as **Convention 22**. **22 was already taken** ("A claim
in a docstring is not a wiring test"). CLAUDE.md runs 1–25. I added
hash-before-write as **Convention 26** and noted the requested number inline.
If Aym wants it renumbered, that is a one-line change - but silently overwriting
an existing convention would have been the worse failure.

## 7. Test state - read this carefully

- `tests/test_concurrency.py` - 64 pass.
- `tests/test_liquidation_recorder.py` - 79 pass (added 5: Binance refused,
  Hyperliquid refused, default is bybit-only, empty set refused, silence alarm).
- `tests/test_schema_matches_feed_modules.py` - 11 pass.
- Full suite, final run: **2,093 passed, 1 skipped, 5 failed** (11m15s),
  excluding `test_dashboard_charts.py` which needs `.venv` (plotly is not in
  system python, per CLAUDE.md).

**All 5 failures belong to another live session, not to this work.** The
evidence, rather than the assertion:

1. The pass count GREW mid-session, 2,046 -> 2,093. Tests were being added
   underneath the run.
2. The failing files CHANGED between two runs of the same tree. Run 1: 17
   failures, all `test_near_liq_trigger.py`. Run 2: those 17 are green and 5
   different ones fail.
3. `test_near_liq_trigger.py` passes 56/56 on re-run, individually and as a file.
4. The residual failure is diagnosed to the line:
   `test_every_skip_reason_the_strategies_emit_is_classified` fails because
   **`weather_arb.py`** emits `global_temperature_market_excluded`,
   `source_reporting_precision_unknown` and
   `source_precision_finer_than_ladder_step`, none of which are in
   `SKIP_CLASSIFICATION` yet. `weather_arb.py` is being written right now by
   another session. Likewise
   `test_build_strategies_returns_fifteen_independent_instances` fails because
   that session is taking the registry past 15.

I did not touch `agents/forge_shadow_eval.py`, `weather_arb.py`, or any
strategy module, and I did NOT "fix" their half-finished work to make the suite
green (convention 21). **Re-run the suite once the other sessions are idle
before treating any of this as a real number** (convention 1).

This is convention 21 exactly as documented: a red suite can mean "another
session is mid-edit".

I rewrote one existing test: `test_one_feed_dying_does_not_stop_the_other` used
Binance as the dying feed, so retiring Binance killed it. It now injects two
**synthetic** feeds via a new `feed_runners` parameter. The property belongs to
`_supervise`, not to any venue - pinning it to a real venue is what let it die.

## 8. Not done / open

- **Hook not installed.** Deliberate, see §2.
- **`validate_harness.py` not re-run.** Nothing I touched is in the graveyard
  or cost-model path, and the re-sweep (PID 18543) is still running. Convention
  1 says no result is durable without it - flagging rather than assuming.
- **Nothing committed or staged.** Tree left for review.
- **Convention number 26 vs 22** needs Aym's ruling (§6).
- The Hyperliquid `userFills` sample feed is scoped but unbuilt (§3). It needs a
  D-number decision before anyone starts, because it is a sample and would be
  dangerous pooled with the Bybit tape.
