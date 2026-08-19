# Handoff: D-353 orphaned-position sweep ruling RECORDED (execution deferred)

**Session:** `cody-orphan-ruling`, 2026-08-19, 17:10-17:25 EDT (measured with `date`).
**Brief:** `docs/handoffs/from-raven/2026-08-19-orphan-sweep-ruling.md`, acted on in full, in order.
**Type:** records-only. Zero importable code touched. Zero database writes.

## HEAD before / after, tree state

| | value |
|---|---|
| HEAD at session start | `8a5984c` (brief claimed `76f2269` - STALE, see deviations) |
| Tree at session start | clean |
| HEAD at session end | see final commit below |
| Tree at session end | clean |

`8a5984c` is the bleed-investigation handoff commit; `76f2269` is its parent.
This is the fourth recorded drift of a transcribed HEAD line. Convention 25
held: I re-ran `git rev-parse HEAD` instead of trusting the brief.

## What was recorded

**D-353** - verified FREE before writing: 156 `### D-` headings parsed, highest
352, zero literal occurrences of "D-353" anywhere in the file. After the write:
157 headings, max 353.

Subject line used, per the brief's suggestion:

> ### D-353. Orphaned-position sweep: RULED yes, booking at 0.00 exit, execution deferred to post-restart window (Raven ruling, 2026-08-19)

The entry carries a **Problem** block (restating the brief's "finding being
ruled on"), the four rulings **R1-R4 transcribed from the brief's RULING
section with nothing added, removed or reordered**, a **Where** line, and the
recording note. The recording note sits **OUTSIDE the ruling text**, per the
D-340 discipline, and is italicised throughout so the boundary is unambiguous.

Exact entry text: `docs/DECISIONS.md`, the final 60 lines. The write went
through `engine.concurrency.safe_edit` (hash-guard, convention 26) with an
idempotent `edit_fn` guarded on the `### D-353.` marker. The diff is
**60 insertions / 0 deletions** - mechanically append-only, nothing amended in
place.

Rulings recorded, in one line each:

- **R1 Sweep: YES.** Restore `closed_ts IS NULL` <=> currently live; mark prior-process rows `orphaned:process_death` with a `closed_ts`.
- **R2 Booking: exit at 0.00**, full premium realized as loss. Flat-booking perpetuates the understatement.
- **R3 Timing: DEFERRED.** Not now, NOT on the 03:45 08-20 restart, and the keying-restart cron session must NOT run it. Gate for the future implementation session: 038 ledger live, suite re-derived, tree clean, DB not in a write window.
- **R4 Swept rows are NOT settlement observations** - excluded from 038 coverage and from any strategy or gate count.

## The evidence was re-measured, not just transcribed - and it reproduces exactly

The brief said "evidence accepted", and its own scope note permits read-only
reads. I took that permission, because the ruling changes what every lifetime
P&L sum reports and a transcribed number is a claim (convention 25). A
`mode=ro` connection to `db/trading.db`, splitting `closed_ts IS NULL` at the
D-352 restart boundary (16:06:29 EDT = 20:06:29 UTC):

| figure | ruling | measured this session | verdict |
|---|---|---|---|
| pre-restart orphans | 52 | **52** | exact |
| orphan cost basis | 109.36 USD | **109.36 USD** | exact to the cent |
| 2026-08-18 cohort | 32 @ 53.79 | **32 @ 53.79** | exact |
| 2026-08-19 cohort | 20 @ 55.57 | **20 @ 55.57** | exact |
| genuinely live | 10 | **9** (cb 30.10) | moved, expected |
| `closed_ts IS NULL` total | 62 | **61** | moved, expected |
| total positions | 2,921 | **2,977** | moved, expected |

All 52 orphans carry `exit_reason` NULL. **The orphan cohort is FROZEN** - as it
must be while nothing sweeps it - and that is the half of the finding that
matters. What moves is the live count and the denominator, because the
restarted main loop trades under the read (128 closes booked since the
restart). The 6.2x over-count ratio in the entry is therefore quoted as
measured at the bleed session's own read, and the durable numbers are
52 / 109.36. Both point-in-time readings are labelled as such in the entry.

My first attempt at the boundary constant was wrong (I hand-wrote an epoch that
landed on 2026-08-20 00:06:29 UTC and returned a nonsense 61/0 split). Recomputed
from a `datetime` instead of a literal; the corrected split is the table above.

## AGENT_ID reading

**SET**, value `cody-orphan-ruling`, on this gateway spawn. Measured with
`os.environ.get('AGENT_ID')`; no `CONFLICT_CHECK_AGENT_ID` fallback needed and
none used. The commit trailer is `Agent-Id: cody-orphan-ruling` as the brief
requires.

This is a **SET** reading, so the authoritative tally moves to **6 SET against
8 EMPTY**. Per brief task 2, the CLAUDE.md `AGENT_ID` section's layered
self-contradiction (two "This session read ..." lines in one paragraph, one SET
one EMPTY, both written as if current) has been folded into a single
one-line statement of this session's reading plus the single tally.
`engine.concurrency who` reported **zero** active checkouts at session start.

## Explicit list of what was NOT touched

Everything below was left alone, per the brief's hard constraints:

- **`db/trading.db`** and every other DB - **no writes, no mutating queries**. One read-only `mode=ro` connection, nothing else.
- **The orphan sweep itself** - designed by the ruling, NOT executed. R3 defers it.
- **The 038 backfill** - not run. Still Raven's call (open item 14).
- `config.yaml`
- All strategy code, all of `engine/`, all of `strategies/`
- `docs/keying-prep/` (all three design files)
- `docs/handoffs/from-raven/2026-08-20-keying-restart.md` and the cron job payload
- Cron job `b4b677c33385` / `keying-restart-spawn` - not read into, not modified, nothing added to its bundle
- Every running process: **no restarts, no signals, no kills**. The main loop (tmux `shadow-main`) and environment B were left running untouched; I did not even `ps` them, having no mandate.
- `db/schema.sql`, `docs/CONVENTIONS.md`, every proposal file, every test file

Files modified this session: **`docs/DECISIONS.md`** (append-only),
**`CLAUDE.md`** (untracked, mandated epilogue rewrite), and this handoff.
A `_scratch_d353.py` was written to the repo root to carry the `safe_edit` call
and `os.remove`d immediately after; it was never staged.

## Tests

**None run, and none needed** - zero importable code was touched. Stated plainly
per the brief. The suite/harness baseline (4,085 passed / 1 skipped / 0 failed;
harness 21/21 rc 0) is **INHERITED** from `cody-forge-review-cont` earlier the
same day and is NOT claimed fresh by this session, in the handoff or in the
DECISIONS entry.

## Deviations from the brief, stated plainly

1. **HEAD was `8a5984c`, not `76f2269` as the brief stated.** Not a deviation in
   my actions - I recorded the true value - but the brief's state section was
   stale and the next brief should not inherit `76f2269`.
2. **I re-measured the evidence read-only instead of accepting it on
   transcription.** The brief said "evidence accepted" and I recorded the
   ruling's numbers as written; the re-measurement is additive and is reported
   in the recording note rather than substituted into the ruling text. It
   confirmed the durable figures exactly. If Raven wanted a pure transcription
   with no independent read, say so and I will not do it again - but it cost
   nothing and it turned an accepted claim into a verified one.
3. **Nothing else.** No other instruction was varied, skipped or extended.

## For Raven

- **D-353 is tentative in the brief's own title.** Ratify or amend it; the entry
  is recorded as a Raven ruling per the brief's framing.
- **The implementation session still needs spawning**, and R3 names its gate
  precisely: after the ~03:28 EDT 08-20 037/026 re-derivation AND after the
  ~03:45 restart, with 038 ledger live, suite re-derived, tree clean, DB not in
  a write window. **It must not be folded into the keying-restart cron session.**
- **When the sweep is implemented, the boundary matters.** It must sweep rows
  from *prior* processes only. The natural discriminator is the owning process's
  start time, not a date - my first cut at it with a hand-written epoch was
  wrong, and a sweep that mis-sets that boundary would close **live** positions
  at 0.00. Whoever implements it should take the boundary from the loop's own
  recorded start, and should dry-run the SELECT before the UPDATE.
- **The orphan cohort will have GROWN by the time the sweep runs.** The 03:45
  08-20 restart orphans every position open at that moment - by R1's own logic
  those become sweepable too. 52 is a floor, not the final count.
- Open items unchanged by this session. Item 10 (AGENT_ID not settled) now has a
  single authoritative tally at 6 SET / 8 EMPTY and no second copy.
