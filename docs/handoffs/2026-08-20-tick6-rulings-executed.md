# Handoff: D-356 recorded, 046 + 047 repairs built, dip_arb killed

**Session:** `cody-tick6-rulings`, 2026-08-20, ~00:52-01:30 EDT / 04:52-05:30 UTC
(measured with `date`).
**Brief:** `docs/handoffs/from-raven/2026-08-20-tick6-rulings-and-repairs.md`.
**Gate:** PASSED. HEAD `c97fa32` matched the brief, tree clean, ZERO active
concurrency checkouts, no `claude` sibling, both shadow loops alive and
untouched.

## What was done

All nine brief steps executed. Nothing deferred, nothing skipped.

1. **D-356 recorded** in `docs/DECISIONS.md` (line 3670), R1-R5 verbatim, plus
   the index line in `docs/DECISIONS-INDEX.md`.
2. **Proposals 045, 046, 047 amended** with dated RULED notes; **031** amended
   with the moot-carve-out note. Prose only, no field changed, status stays
   PROPOSED on all four (the D-354 R1 precedent shape).
3. **Repair 046 BUILT** - cluster counts, cluster-level sigma, 3-sigma verdict
   gate, same correction on the rule 6 self-check.
4. **Repair 047 BUILT** - `COUNTERFACTUAL_GRADED_SOURCES = ('venue',)`,
   refuse-and-report header, self-check on the same set, fixture test.
5. **dip_arb KILLED** by reversible `supported_market_types` override.
6. **Suite and harness RE-DERIVED FRESH.**

## Numbers (all point-in-time; both books moved under read all session)

**Suite:** `4,136 passed / 1 skipped / 0 failed` in 390.94s
(`tests/` less `test_dashboard_charts.py`). That is the inherited 4,116 plus the
**20 tests added this session**. **Harness:** `21/21 rc 0`. Both re-run fresh
because importable files changed; the inherited baseline is NOT quoted.

An AST pass confirms all **103** `test_` functions in
`tests/test_resolution_ledger.py` are module-or-class level with none nested
inside another function (the `5864461` dead-test trap, checked not assumed).

**Counterfactual at 2026-08-20T04:59:39Z**, NOT_TESTED in both books, carried as
nothing:

| | matched | shares | market-sides | pos/cl | sh/cl | settle | sigma | delta | sigma |
|---|---|---|---|---|---|---|---|---|---|
| env A | 157 | 3,014 | 135 | 1.16 | 22.3 | 0.0537 | 0.0194 | +0.0115 | 0.59 |
| env B | 145 | 2,831 | 124 | 1.17 | 22.8 | 0.0699 | 0.0229 | -0.0001 | 0.00 |

046's design effect reproduces exactly (it measured 22.4 / 23.1). The two arms
still disagree in SIGN and env B's delta is now one ten-thousandth of a dollar
per share - which is what 046 predicted a statistic with no signal keeps doing.
Both books are still short of the 400-position bar, so the new gate is not even
reached yet.

**Ledger drift within this one session:** rows 822 -> 840 (env A) and 420 -> 438
(env B); matched salvage 153 -> 157 and 141 -> 145.

**dip_arb, re-derived rather than taken from the brief (convention 25):**
354 closes / **-169.14** / WR 0.186 (env A) and 58 / -20.60 / 0.172 (env B).
Trailing-30 WR **0.100** and **0.200**, against its own 0.45 kill line.

## Rollback checks: both proposals' own checks run, all PASS

- **046 check A** (cluster count never exceeds matched count): PASS on every
  exit reason in both books.
- **046 check B** (printed sigma equals `sqrt(p*(1-p)/clusters)` recomputed from
  the tool's OWN PRINTED p and cluster count): run by parsing stdout with no
  database access, exactly as worded. PASS on 9 rows env A, 7 rows env B.
- **047 zero-delta**: PASS. 964 = 964 matched in env A, 433 = 433 in env B,
  self-check positions identical at 332 and 123.

## THREE THINGS RAVEN SHOULD LOOK AT

### 1. The brief contradicts itself on its own D-number. I used D-356.

The title, the numbering note and the RULING heading all say **D-356**. Step 6,
step 8 and the Constraints section say **"D-355 R4"** and "the D-355 entry". I
followed the numbering note, because it is the clause that reasons about the
conflict: it states D-355 is already allocated to
`2026-08-20-orphan-sweep-implement.md` and says "Do not use D-355 for anything."
Verified before writing: zero literal occurrences of "D-356" AND zero of "D-355"
in `docs/DECISIONS.md`; highest heading was 354. **The in-code comment on the
dip_arb override cites D-356 R4.** If Raven wanted D-355, the fix is a rename in
three places and the orphan-sweep session needs a new number.

### 2. 047's test fixture is UNCONSTRUCTIBLE as worded. Schema fact it did not know.

Clause 3 asks for "one `venue` row and one `inferred_terminal_price` row **for
the same market-side**". `market_resolutions` carries
`UNIQUE (market_slug, outcome_side)` - verified by direct insert this session,
`IntegrityError: UNIQUE constraint failed` - and `write_resolutions` uses
`INSERT OR IGNORE`, so **a market-side holds exactly one row whatever its source
and the first writer wins.**

This narrows 047's threat model and does not remove it. The same-market-side
double-grading it describes is **impossible**; the realisable failure is whole
**additional market-sides** entering the graded arm and the self-check, which is
the mechanism that actually carried its argument. The fixture builds that: one
venue market-side, one inferred market-side, asserting the default grades ONE
while `sources=LIVE_SOURCES` grades TWO - so the test passes for the right
reason rather than because the second row is missing. A separate test pins the
UNIQUE constraint so this reads as deliberate, not as a weakened test.

### 3. The 3-sigma gate turned three existing tests red. That was 046's thesis arriving as test failures.

`TestCounterfactualKillVerdict` asserted CONFIRMED, NEGATIVE and INCONCLUSIVE on
400-position books whose deltas the cluster sigma cannot separate from zero.
I **re-sized them** (to 500, 600 and 3,000 positions) so the band logic they
exist to test is still reached, and pinned the blocking behaviour they used to
assert in a new `TestTheThreeSigmaGate` - including a test that 400 matched
positions with a delta **past** the 0.010 band still returns NOT_TESTED.

**No threshold was re-sized.** `KILL_BAND` 0.010, `KILL_MIN_MATCHED` 400,
`SELF_CHECK_MAX_DISAGREEMENT_RATE` 0.0500, each pinned by assertion.

## Two deliberate departures from proposal text, both flagged in D-356

**(a) 046's SE formula at the boundary - STRICTER, not looser.** At p = 0.0 or
1.0, `sqrt(p*(1-p)/clusters)` is exactly ZERO, which would make the 3-sigma gate
**vacuously satisfied by any delta whatever**. 046 does not address this because
it assumes an interior p. The gate **fails CLOSED** on a zero sigma and records
NOT_TESTED naming the degeneracy (convention 11). This is live today: env A's
`sell:mean_reverted` and `sell:time_stop` rows both print sigma 0.0000.

**(b) 047's zero-delta check could not be run as worded.** "The pre-repair count"
and the post-repair count are minutes apart and **both books move under read**,
so that comparison measures drift. A first attempt showed exactly that: `narrow`
matched **429** against `wide` **428** in env B - a direction a NARROWER filter
**cannot** produce. Re-run with both source sets read inside ONE pinned WAL
snapshot (`BEGIN` ... `COMMIT`), which is the comparison the kill condition
means. **This is a reusable lesson: any A/B on these databases needs a pinned
snapshot or it measures the loop, not the change.**

## What was NOT touched (brief constraints, all honoured)

No loop restart, no process signalled, no `config.yaml`, no 038 `--backfill` on
either database, no orphan sweep, 037 left BLOCKED, 039's counting scheme
untouched, no wallet or API key, no live path, no backtest run. `LIVE_SOURCES` is
unchanged and `SOURCE_INFERRED_TERMINAL_PRICE` is not deleted (047 rules 1-2).

**The dip_arb kill is NOT yet in effect.** Both shadow loops are running the
source they imported at their own start times (convention 13). It takes effect at
the next natural restart - the pending ~03:45 EDT one, if that still fires.

## Session facts worth carrying

- `AGENT_ID` read **`cody-tick6-rulings`** - SET on this gateway spawn. Tally
  moves to **7 SET / 12 EMPTY**.
- **Write and Edit were BOTH GRANTED.** Every edit went through
  `engine.concurrency.safe_edit` regardless (convention 26). Zero friction, sixth
  session running.
- Shadow loops confirmed alive by `ps` throughout: **52733**
  (`raven-shadow-restart`) and **73117** (`raven-env-b-restart`).

## Open for Raven

1. The D-355/D-356 numbering, above. Ruling needed if D-355 was intended.
2. 046's referred threshold question is answered NEITHER by R2, so the bar stays
   400 and the band stays 0.010 - but at the current design effect the salvage
   question now needs **~5,800 / ~7,100** matched positions to clear 3 sigma at a
   0.010-scale delta. At 157 and 145 matched, that is far away. R2 accepted this
   cost explicitly ("recorded NOT_TESTED rather than answered wrongly"), but it
   is worth Aym seeing that 043 may now never conclude before
   `PM_fair_value_settlement_exit` is retired on 041's line of argument instead.
3. `dip_arb`'s kill is executed but INERT until a restart. If that matters
   before ~03:45 EDT, someone has to say so in words - I restarted nothing.
4. 045's reversal condition 1 (declining to buy a control arm) is ratified but
   remains overrulable only by Aym in words.
