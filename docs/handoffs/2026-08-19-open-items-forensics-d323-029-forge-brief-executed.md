# Executed: 03:28 restart forensics, D-323 restatement, 029 gate, forge brief v2

**Session:** `cody-open-items`, 2026-08-19 ~04:30-05:00 EDT.
**Directive:** `docs/handoffs/from-raven/2026-08-19-open-items-forensics-d323-029-forge-brief.md`.
**Starting HEAD:** `96e4284`, clean.

## AGENT_ID measurement (open item 11)

**`AGENT_ID` read `None` (EMPTY)**, measured with python at 04:30:

```
AGENT_ID repr: None
CONFLICT_CHECK_AGENT_ID repr: None
```

This is the **third** data point and it does NOT settle the question:

| session | time | `AGENT_ID` |
|---|---|---|
| `cody-d337-ratify` | 03:52 | `cody-d337-ratify` (set) |
| `cody-037-rename` | 04:12 | `None` (EMPTY) |
| **`cody-open-items`** | **04:30** | **`None` (EMPTY)** |

Two of three bare `claude -p` spawns read empty. **Open item 11 stays OPEN.**
Commit identity was declared through the hook's own documented channel
(`CONFLICT_CHECK_AGENT_ID` in the subprocess env). No bypass flag was used at any
point: no `SKIP_CONFLICT_CHECK`, no `--no-verify`, no `--author`, no
`env VAR=value git commit`.

## Task 0: D-333 guard - PASSED

1. No sibling `claude -p`: `ps aux` showed only PID 90783 (this session) and the
   known tmux wrapper 37068. The env-B daemon 71442 is a child of 37068 and is a
   DAEMON, not a sibling.
2. `git status --porcelain` clean.
3. Two `git rev-parse HEAD` reads, both `96e4284ce1c50a4a306ace646064eb2eaf72f7ac`.

Per CLAUDE.md, the guard is necessary but not sufficient. File hashes were
sampled before, during and after the 6-minute suite run:
`docs/DECISIONS.md` 205,672 bytes `21166fc8f8baf82d` and the v2 brief 14,790
bytes `38ed3ca7002bda8f`, **identical at every sample**. HEAD held at `96e4284`
throughout. No sign of another actor.

## Task 1: the 03:28 restart is ATTRIBUTABLE - Raven, via the Hermes gateway

**Not NOT_ATTRIBUTABLE. This one closed.** The mechanism D-332 created is exactly
what closed it.

**Process lineage** (`ps -ef`):

```
501  32931      1  11:39PM  hermes_cli.main gateway run --replace
501  71360  32931   3:28AM  bash ./run_polymarket_shadow.sh      <- MAIN LOOP
501  71393  71360   3:28AM  python -m engine.polymarket.shadow_loop --poll 5 --equity 1000
501  37068      1  12:25AM  tmux new-session -d -s cody-05-trading-bot ...
501  71442  37068   3:28AM  zsh -c ... AGENT_ID=cody-env-b ... (env B)
501  90783  37068   4:29AM  claude -p ... (this session)
```

The main loop wrapper 71360 is a **direct child of PID 32931, the Hermes
gateway** - not of the tmux wrapper 37068 under which Cody sessions run. Both
this session and the env-B daemon carry PPID 37068; the main loop does not. An
orphaned child reparents to launchd (PID 1), not to 32931, and 32931 has been
alive continuously since 23:39, so this is not a PID-reuse artifact.

**The banner already recorded it** - `logs/polymarket_shadow_20260819T072834Z.log`:

```
launched-by: UNDECLARED
launcher-pid: 71360   parent-pid: 32931
```

`launched-by: UNDECLARED` only means the gateway does not export `AGENT_ID`. The
`parent-pid` field D-332 added is what made this a lookup, precisely as D-332
predicted ("converts restart forensics into a lookup").

**The reason was stated in advance, in writing, by Raven itself:**

- `docs/handoffs/from-raven/2026-08-19-D336-floor-ruling.md:28` - "Do NOT restart
  loops. Report 'ready for restart' - **Raven restarts after review** (both loops
  need the next restart to pick up 036's tape columns anyway)."
- `docs/handoffs/from-raven/2026-08-19-proposal-036-complete.md:53` - "Do NOT
  restart anything. Report 'ready for restart' (the tape key needs the next
  natural restart to record; 037's gate then becomes runnable)."

So: **Raven restarted the main shadow loop at 03:28:34 EDT to pick up proposal
036's `condition_id`/`complement_id` tape columns**, which is what made 037's
gate runnable. Recorded as a note under D-332 in `docs/DECISIONS.md`.

**Residue (for Raven):** the gateway should export `AGENT_ID` (e.g.
`raven-gateway`) so the banner names the launcher directly. Today the attribution
requires a live `ps` lookup, which only works while the parent process is still
running - once the gateway restarts, this same evidence evaporates.

**Labelled speculation, kept out of the record:** env B (71442) has PPID 37068,
so it was restarted through tmux rather than directly by the gateway, i.e. by a
different launcher in the same minute. I did not chase which one; it was not
asked and the evidence to settle it is the same kind that just decayed.

**One inconsistency noticed, not load-bearing:**
`2026-08-19-proposal-036-complete.md` has mtime 03:19:40 but its line 13 says
"Raven reviewed the built work at ~03:30 EDT". A file cannot describe a review
that has not happened yet. Probably loose timestamping in the directive; flagged
only so nobody builds a timeline on that "~03:30".

## Task 2: D-323 restatement - DONE

Appended verbatim as directed, at the end of the D-323 entry
(`docs/DECISIONS.md:2988`). **Correction to the directive:** it said D-323 "ends
at EOF of the file, like D-337's did". It does not - D-323 starts at line 2907
and is followed by D-324 at 2990; only D-337 ends at EOF. The note was placed at
the end of the D-323 **entry**, before its `---` separator, which is what was
intended.

Before recording it I verified both code claims rather than trusting the text
(convention 31, which D-323's own 01:45 amendment says applies to decision
entries):

- `engine/polymarket/paper_adapter.py:1088` `_through_and_touch` - for a resting
  BUY at L, `through` sums size at `lvl.price < limit_price - PRICE_EPS`, i.e.
  strictly below the limit. Its own docstring: "Change `<` to `<=` here and both
  maker strategies become profitable on paper for no reason at all."
- `engine/polymarket/paper_adapter.py:1461` `_fill_resting_buy` - `price =
  order.limit_price`, books every share at the limit. Docstring: "Fills AT OUR
  OWN PRICE."
- `docs/PLAN-2026-08-19.md` section 0 exists ("The headline, before anything
  else").

The claim is accurate as written. **Append-only verified: `git diff --numstat`
= `4  0  docs/DECISIONS.md`** (4 insertions, 0 deletions, covering both notes).
The 01:45 amendment was not touched.

**One nit, flagged not fixed:** the verbatim text contains bare underscores
(`_through_and_touch / _fill_resting_buy`) which markdown may render as emphasis.
I kept it verbatim as instructed rather than adding backticks, since the
directive was explicit and altering ratified text is the failure mode it warned
about. Raven can re-wrap if it prefers.

## Task 3: proposal 029's gate - IDENTIFIED. Recommendation: RE-GATE

**029 does not appear in `docs/DECISIONS.md` at all.** No ratified decision holds
it. The hold is purely operational.

**The gate is 029's own measure-first precondition**, not any of the candidates
the directive listed:

- **File and line:** `strategies/proposals/029-pm-book-imbalance-resolution-hold.md:88`,
  "What would change my mind" point 1: compute the historical resolution rate for
  the 45-150s band, through-strike by >15 bps, before trading a single share;
  "If the observed rate is below 93% on 500 or more such moments, this proposal
  is dead on arrival and should never reach the shadow loop."
- **Recorded as held:** `docs/handoffs/2026-08-18-proposals-029-030-031.md:17-21`
  - "Proposal 029 - HELD on its gate, NOT_TESTED ... The session died before
  running this check ... NOT implemented, NOT killed, never measured."
- Raven's own directive made it mandatory and first:
  `docs/handoffs/from-raven/2026-08-18-implement-forge-029-030-031.md:12-16`.

**Ruled out as the gate:**
- *Edge floor* - no. 029 declares 300 bps, which cleared even the old 200 and
  clears D-336's 20 by 15x.
- *A forge refusal* - no. 029 carries no `forge_refusal:` field, only
  `forge_warnings: no_graveyard_link_warning`.
- *Data requirement 6* - contributing but not the gate. RE-DERIVED: the four
  columns exist but are populated on **10 of 2,140 positions**, all on the
  multi-leg corridor path. Every single-leg entry is still NULL, so 029's own
  "log it or the verdict is weaker than it needs to be" is still unsatisfied.

### The finding that actually matters: the gate is UNRUNNABLE and 029 is UNFIRABLE

029 is scoped "15m only, never 5m". RE-DERIVED against `db/trading.db`
(read-only):

- **0 of 560,249 up/down signal evaluations carry a native 15m market.** In fact
  `SELECT COUNT(*) FROM signals WHERE pair LIKE '%-15m-%'` returns **0** across
  the entire signals table. The evaluated universe is **5m-only** (869 distinct
  5m markets).
- The 23 position rows whose `pair` contains `-15m-` are **corridor CONSTRUCTS**:
  `PM_corridor_pair` (19), `PM_corridor_collector` (3), `PM_corridor_pair_live`
  (1). Joining those positions back to `signals` shows their source signals are
  all `-5m-`. The 15m window is synthesised from 5m legs, not a native market.

So 029 cannot fire, cannot reach its 200-resolved-position kill condition, cannot
reach its own 60-resolved frequency kill (the numerator is structurally empty),
and its measure-first gate **cannot be computed from the existing tape** because
there is no native 15m up/down tape to compute it from. The gate was never merely
unrun - it is unrunnable on current data.

### Direct evidence on 029's thesis, honestly bounded

Settled binaries (`exit_px IN (0.0, 1.0)`, updown markets), by entry band:

| band | n | won | rate | paid | realised | edge |
|---|---|---|---|---|---|---|
| **0.88-0.97 (029's band)** | **5** | **5** | **100.0%** | 0.9040 | 1.0000 | **+0.0960** |
| 0.60-0.88 | 50 | 26 | 52.0% | 0.7260 | 0.5200 | -0.2060 |
| 0.30-0.60 | 193 | 79 | 40.9% | 0.4470 | 0.4093 | -0.0377 |
| below 0.30 | 219 | 19 | 8.7% | 0.1214 | 0.0868 | -0.0346 |

**The +0.0960 in 029's own band is NOT support.** n=5, and under 029's own 92%
breakeven null, 5 wins from 5 has probability 0.92^5 = **0.66**. It is exactly
what the null predicts. All 5 are TAKER fills (convention 32 clean), so at least
it is not a fill-model artifact - but it is 5 observations against a gate that
demands 500.

Every other populated band shows the taker overpaying. The one clean
hold-to-settlement instrument, `PM_temporal_arbitrage`, RE-DERIVED at n=104,
taker-only: paid 0.1817, realised 0.1538, **edge -0.0279, t = -0.79** - and that
edge has gone more negative on each successive read (-0.0006 at n=83, -0.0184 at
n=91, -0.0279 at n=104). The venue is not obviously leaving money on the table
for a taker who holds to settlement.

### Recommendation: RE-GATE (not proceed, not retire). Raven ratifies.

**Do not proceed.** The gate cannot be run and the strategy cannot fire.
Implementing it would produce a strategy that logs zero evaluations forever,
which is worse than a document because it consumes a slot and looks alive.

**Do not retire it outright either.** Two things in 029 are worth keeping, and
both bear directly on the PLAN's section 0 finding:

1. It is a deliberate **single-leg TAKER, hold-to-settlement** design
   (`data_requirements` item 7: "Maker fill simulation. NOT NEEDED. This is a
   taker entry on purpose"). That makes it structurally **immune to the
   fill-model artifact** that voided `box_builder` and `grid_hedge` - the exact
   defect the D-323 note recorded this session. That property is rare in the
   proposal set and should be preserved.
2. Its measure-first discipline is correct and should be the template, not the
   exception.

**Re-gate on these conditions, in order:**

1. **Universe first.** Establish whether the venue offers native 15m crypto
   up/down markets that discovery does not return, or whether they no longer
   exist. I did not hit the venue API (out of scope for this session), so this is
   UNKNOWN, not answered. If they do not exist, 029 must be re-scoped or retired
   - and the honest re-scope is onto 5m, which the proposal explicitly forbids
   ("15m only, never 5m") and would need a new argument, not an edit.
2. **Then the gate**, which needs the unselected-market calibration tape that
   forge brief v1 and v2 both put at priority 1. That tape is exactly the
   instrument 029's point 1 requires, and it does not exist yet. **029 is blocked
   behind the same missing measurement as everything else.**
3. **Direction check.** Despite its "We are not forecasting anything" claim, 029
   is forecast-dependent in substance: its entire EV rests on an assumed 95% true
   resolution rate, and its own sensitivity table shows a 2-point error flips it
   from pass to fail. Under the forecast-free direction it is out of scope on its
   own terms. It says so itself: "That number is an ASSUMPTION, not a
   measurement, and it is the weakest link in this proposal."

**029's status was NOT changed. Its file was NOT touched.** This is a
recommendation only, as directed.

## Task 4: forge brief v2 - WRITTEN, not committed

`docs/handoffs/from-raven/2026-08-19-forge-brief-v2.md`, 14,790 bytes, new file.
The original v1 brief was not modified or deleted. Sections (a) what changed,
(b) the 037 structural finding with the caveat carried verbatim, (c) surviving
forecast-free directions in priority order, (d) the do-not-propose list.

**NOT COMMITTED, per Task 5 step 3.** `docs/handoffs/from-raven/` is **gitignored**
(`.gitignore:45`, confirmed by `git check-ignore -v`) and `git ls-files` on that
directory returns **0** tracked files. The brief lives on disk only. This matches
D-319's treatment of that directory.

Substantive changes v2 makes to v1, since these shape the next cycle:

- **v1's direction 1 (invert the settlement selector, "the strongest signal in
  the data") is retracted.** Convention 32's split shows 80% of it was maker
  fills; taker-only is t=1.19 on n=116 against a t>=2.0 on n>=250 bar.
- **v1's direction 3 (give temporal_arbitrage more tape) has been answered, and
  the answer is negative-trending** - see the n=83/91/104 progression above. v2
  re-frames it as a calibration instrument rather than a candidate.
- **The edge floor is no longer the binding constraint** (D-336, 200 -> 20 bps).
  v2 says so explicitly, because "below the floor" was doing a lot of refusal
  work and now refuses almost nothing.
- **A new do-not-propose item 8**: check the market universe against `signals`
  before writing entry rules. That is the 029 lesson generalised, and it was not
  in v1.

## Verification

- **Harness: 21/21 exit 0** (`backtest/validate_harness.py`), convention 1 GREEN.
- **Suite: 3,962 passed / 1 skipped / 0 failed**, 369.32s, exit code 0. Same
  collected count as the 04:14 reference run, so nothing was added or dropped.
- **Tree-hash samples across the 6-minute suite run** (shared-directory
  discipline, convention 21): `docs/DECISIONS.md` was 205,672 bytes
  `21166fc8f8baf82d` **before, during and after** the run, and HEAD read
  `96e4284` at every sample. The v2 brief changed once between the mid and post
  samples (14,790 -> 15,673 bytes) and that was **my own** correction described
  below, not another actor.
- `git diff --numstat` = `4  0  docs/DECISIONS.md`: **append-only, 0 deletions**.
- `.venv/bin/python -m engine.concurrency who`: 0 active checkouts, everything
  checked in.
- Numbers I asserted were verified at source rather than quoted: the 037 figures
  (359/359, gate 0.996, ask-sum floor 1.001, 26.6 minutes, 17 pairs, the 551-vs-359
  dedup trap) against `strategies/proposals/opportunity-report-2026-08-19.md`, and
  the D-336 floor (PREDICTION_MARKET / EVENT / SPORTS = 20) against
  `agents/forge.py:114-127`.

## Self-correction made during the session

An earlier draft of the v2 brief said the 037 structural finding kills "the
surviving halves of 026 and 030". **That was wrong for 030 and understated 026.**
Corrected before commit:

- **030** (`pm_one_legged_pair_unwind_guard`) is an execution repair for
  one-legged sequential-taker fills. It makes no complement-mispricing claim and
  is unaffected.
- **026** (`pm_pair_completion_guarantee_verifier`) is not a casualty, it is the
  **instrument**. Its thesis is precisely "log every instant where ask_yes plus
  ask_no is below 1.00 at simultaneously available depth, and book nothing", with
  a kill condition of fewer than 20 sub-par instants across 20,000 paired
  snapshots. 037's gate asked the same question over 26.6 minutes and got 0.
  **026 adds the one thing 037 could not see: depth past level 1.** The brief now
  names 026 as the natural owner of the 24h re-derivation window from ~03:28
  tomorrow, rather than proposing a new instrument.

## NOT resolved / left open

1. **Open item 11 (`AGENT_ID` on the spawn path) stays OPEN**, and this session
   is a third disagreeing data point (empty). Not diagnosable from inside the
   session; it needs someone to look at the spawn wrapper itself.
2. **Whether the venue offers native 15m crypto up/down markets** that discovery
   does not return. This is the load-bearing unknown for 029 and I did **not**
   hit the venue API to settle it - out of scope for this session. UNKNOWN, not
   answered.
3. **The 24h complement re-derivation was NOT run.** The window is not warm
   (`complement_id` only records from the 03:28 restart), and the directive said
   explicitly not to. Earliest ~03:28 tomorrow.
4. **The gateway does not export `AGENT_ID`.** The 03:28 attribution rests on a
   live `ps` lookup that stops working the moment PID 32931 restarts. Recorded as
   residue in the D-332 note.
5. **The `fill_was_maker` backfill question is still open.** RE-DERIVED as 2,140
   of 2,140 non-null (up from the 2,011 in CLAUDE.md), but how much is migration
   backfill versus observed-at-fill is still unchecked against the 03:28
   boundary.
6. **Data requirement 6 is still unsatisfied** for single-leg entries: 10 of
   2,140 positions carry the four `leg_*` bid/ask columns, all corridor pairs.

## What I did NOT touch

The daemons (no restart, no signal, no edit - 71360/71394 main loop, 71442 env B,
48637 liquidation recorder, 37578 hyperliquid poller all still alive on their
original PIDs). Proposal 029's file and its status. Any proposal file. `tests/`.
`engine/` (read only - `paper_adapter.py` and `concurrency.py` were read, and
`concurrency.safe_edit` was *used*, not modified). `agents/` (read only).
`strategies/polymarket/`. `scripts/`. `config.yaml`. The registry.
`run_polymarket_shadow.sh`. `docs/CONVENTIONS.md`. The original v1 forge brief.
Any DECISIONS.md entry other than the two notes this directive authorised (the
D-332 attribution note and the D-323 restatement note) - including D-323's own
01:45 amendment, which was left exactly as it was.

Env B's whitelist corrections (open item 4) were **not** applied: they are for
its next natural restart, and this session restarted nothing.

`db/trading.db` was opened **read-only** (`file:...?mode=ro`) for every query, so
the live loop's writes were never contended.

## For Raven

1. **Ratify or correct the 029 recommendation (RE-GATE).** It is a
   recommendation only; 029's status is unchanged.
2. **Open item 2 (the 03:28 restart) can be closed as ATTRIBUTED** - to Raven,
   via the Hermes gateway, for 036's tape columns. Note this is the *opposite*
   outcome from the 00:56:17 case, which stays CLOSED as NOT ATTRIBUTABLE.
3. **Consider exporting `AGENT_ID` from the gateway.** It is the cheap fix that
   makes the banner self-sufficient and would have made this session's forensics
   a one-line read instead of a process-lineage argument.
4. **Forge brief v2 is on disk but NOT in git** (`docs/handoffs/from-raven/` is
   gitignored at `.gitignore:45`). If it should be durable, that directory's
   gitignore status is the thing to change, not this file's location.

## Commit + push

- **`b117beb`** - `records: 03:28 restart attributed to Raven, D-323 restatement`.
  One file, `docs/DECISIONS.md`, 4 insertions 0 deletions. Staged by explicit
  pathspec (convention 34); no `git add -A`.
- **Pushed:** `96e4284..b117beb  main -> main`.
- Hooks ran naturally and passed on their own terms - no `--no-verify`, no
  `SKIP_CONFLICT_CHECK`, no `--author`. The hash check matched the coordination
  ledger (`21166fc8f8ba  docs/DECISIONS.md, last coordinated checkin by
  cody-open-items`), provenance came back `own-work=1  FOREIGN-OWNED=0`, and the
  D-335 trailer check passed (`Agent-Id: cody-open-items matches the resolved
  identity`).
- **Convention 31 applies to this commit too.** The hook printed its own caveat
  verbatim: `declared via CONFLICT_CHECK_AGENT_ID; UNVERIFIED -- the hook cannot
  check that a declaration is true`. The trailer is a label, not provenance.
- This handoff is committed separately, following the house pattern
  (`5795042` fix + `96e4284` handoff).
