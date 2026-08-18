# Cody handoff: gitignore/env fixes + stale analysis regeneration

**Date:** 2026-08-14
**From:** Cody
**Re:** `docs/handoffs/from-raven/2026-08-14-gitignore-envexample-stale-analysis.md`
**Status:** All three tasks complete. Four findings that change how the regenerated files should be read. Two of them need a ruling before anything gets committed.

---

## READ THIS FIRST: conditional_edge.json now reports a false positive

The regenerated `conditional_edge.json` says survival is 90-96% and its own
DIAGNOSTIC prints:

> `class_x_timeframe: survival 94% - ABOVE chance, worth a proper out-of-sample test`
> `Mean PnL/trade of SELECTED cells when judged on unseen underlyings: +0.081 (cost floor -0.30)`

**Do not cite that. It is an artifact, not edge.** The committed version said
53-58% (at chance, correctly null). Nothing was added to the graveyard between
those two runs; the purge only REMOVED rows. A purge cannot manufacture edge.

The script's null is stated in its own docstring: "if selecting on noise,
roughly half of selected cells should survive by chance (a coin flip on which
side of the floor they land)." That holds only if `COST_FLOOR = -0.30` sits
near the median of the cell distribution. It no longer does. Post-purge the
median cell is about `-0.06`, comfortably above the floor, so nearly every
cell clears it whether or not it was selected.

I measured the real null using the script's own `aggregate()` on the same 20
seeded splits: per split, survival of SELECTED cells vs survival of EVERY
eligible verify cell with no selection at all.

| slice | selected survival | ALL cells, unselected | lift from selection |
|---|---|---|---|
| class | 96.0% | 95.0% | +1.0pp |
| sector | 90.4% | 85.9% | +4.5pp |
| timeframe | 93.9% | 88.0% | +5.8pp |
| class_x_timeframe | 94.1% | 89.2% | +5.0pp |
| class_x_exit | 98.4% | 96.8% | +1.6pp |

Selection buys 1-6 percentage points, not the ~45pp the 50% null implies. The
null result stands. The number that moved is the baseline, not the edge.

Why the baseline moved: the purged futures rows carried the pre-D-249 broken
sizing (a $100 account "trading" a contract needing $1,800 margin), which
produced heavily negative per-trade P&L that held the cell distribution down
around the -0.30 floor. Removing them lifted the distribution above it.

**This needs a decision, not a silent patch.** Two options: derive the floor
per-run from the actual cost model, or compute the null empirically (as above)
instead of assuming 50%. I did not touch the code - Raven's instruction was
regeneration only, and this is a design change.

---

## Finding 2: all 23,595 rebuilt FUTURES rows have zero trades

The D-261 purge dropped 23,595 contract rows and the incremental runner
rebuilt 23,595. That looks like a clean round-trip. It is not.

Every one of the rebuilt rows has `trades: 0`. Total futures trades in the
graveyard: **zero**. Cause is `notional_cap_usd: 100` in config.yaml against
initial margins of $900-$2,600:

```
ES_F->MES  margin 1800   size_for($100) = 0.00 contracts
NQ_F->MNQ  margin 2600   size_for($100) = 0.00
CL_F->MCL  margin 1400   size_for($100) = 0.00
GC_F->MGC  margin 1900   size_for($100) = 0.00
RTY_F->M2K margin  900   size_for($100) = 0.00
```

No futures trade can ever open at the configured capital. That is the D-249
fix working correctly (convention 12: unaffordable is a real answer).

The problem is the label. 22,297 of those rows are recorded **FAIL**, only
1,298 as NOT_TESTED. `vectorized_harness.py:1047` defines NOT_TESTED as "the
harness structurally could not have run this" and gates it solely on bar
count, so an instrument that cannot be sized at all falls through to FAIL. A
strategy whose every signal was rejected for lack of capital did not run and
fail; it did not run. This is convention 11 / the D-255 shape.

Consequence: 22,297 of the 508,647 FAIL verdicts are untested futures wearing
a verdict, on a public repo. Does NOT affect the 155 distinct findings or any
PASS (those need trades). Also a decision, not a patch.

Related but distinct, for context: 395,758 of 535,425 rows (73.9%) have zero
trades overall, and 369,413 of those are FAIL. Most of that is legitimate and
by design - a strategy simply not signalling on a given series is a real FAIL
per the harness's documented rule, and it is not concentrated in a few bad
strategies (the 9 near-non-firing ones contribute only 22% of zero-trade rows;
every strategy has at least 26%). I flag it only because "508,647 FAIL" reads
as 508,647 tested configurations, and the number with any trades is 139,667.

---

## Finding 3: the committed FUTURES numbers were fabricated, and the purge removed them

Direct before/after proof that the purge did its job:

- `asset_class.json` OLD: 128 cells including **31 FUTURES cells**.
  NEW: 138 cells, **0 FUTURES cells**.
- `dispersion_gate_smoke.json` OLD: `ES_F c_bps = 1.422`.
  NEW: `ES_F c_bps = inf`.

The old ES_F cost of 1.4bps was the pre-D-249 artifact: floor the contract to
1, and a huge notional makes the toll look negligible. `inf` is correct.

**Minor portability issue this creates:** `dispersion_gate_smoke.json` now
contains 9 bare `Infinity` tokens. Python writes and reads them; strict JSON
(RFC 8259) does not allow them, so `JSON.parse` and most non-Python parsers
reject the file. It is a committed file on a public repo. The value is right,
the serialization is not portable. Flagging, not fixing.

---

## Finding 4: inversions covers 35 of 55 strategies and caps at 200

`run_inversions.py:47` builds its strategy lookup from
`ENTRY_STRATEGIES_EXPANDED` + `STRATEGY_LAB_STRATEGIES` only - 28 + 7 = 35.
The v2/v3/v4/v5 sets (20 strategies) are absent, so they can never be
inversion-tested. In `inversion.py:195` a candidate whose strategy is missing
from the lookup hits `if strategy is None: continue` and is dropped with no
log line and no entry in `candidates_rejected_by_reason`.

Separately, `max_candidates` defaults to 200 and `run_inversions.py` does not
override it, so only the first 200 eligible candidates are ever considered.

Net effect this run: 562 eligible, **196 tested**. The 366-candidate gap is
visible in the file (eligible vs tested) but nothing in the output says WHY,
and `candidates_rejected_by_reason` does not account for it. Only 15 distinct
strategies were actually tested, all from expanded/strategy_lab. Predates this
session; not caused by the purge. Worth a silent-cap log line (the project's
own "no silent caps" instinct).

---

## Task 1 + 2: committed and pushed (`3048e3c`)

- **F1** `.gitignore`: `.env` -> `.env*` plus `!.env.example`.
- **F2** `research/graveyard/archive/v0_graveyard_full*.json` ->
  `research/graveyard/archive/`. The 128MB flatcost_partial JSON is now ignored.
- **F4** `.env.example`: added `ALPACA_ENDPOINT` (after `ALPACA_API_SECRET`)
  and `FRED_API_KEY`.
- `research/graveyard/assertions.json` untracked and added to `.gitignore`.

Verified with `git check-ignore -v`: `.env`, `.env.local`, `.env.production`,
the 128MB archive file and `assertions.json` all match; `.env.example` does
not (exit 1, correct).

**Deviation:** I used `git rm --cached`, not `git rm`. Raven's file said
`git rm`, which would also delete the local copy. Untracking achieves the
stated goal (stop serving it publicly, avoid confusion) and is reversible.
The local file is still on disk. Say the word if you want it gone.

---

## Task 3: regeneration results

All run with `env -u PYTHONPATH python3` (convention 14). Input
`research/graveyard/v0_graveyard_full.json` (535,425 entries, 385MB).

**Staleness cut line.** Raven's table assumed everything predating the 13:45
post-sweep repair is stale. That is right for graveyard consumers and wrong
for the rest: five of these scripts never read the graveyard, they re-run from
CSVs. For those the real cut line is the last change to shared logic,
`backtest/cost_model.py` at **Aug 13 17:45** (the D-249 sizing fix). Data CSVs
have not changed since Aug 13 09:53. Sorting by that line predicted exactly
which files moved, and it held on every one.

| File | Reads graveyard? | Before -> After |
|---|---|---|
| `pooled.json` | yes | **34 -> 52 strategies**, 1,390,451 -> 1,900,086 pooled trades |
| `asset_class.json` | yes | **128 -> 138 cells**, 34 -> 52 strategies, FUTURES 31 -> 0 |
| `conditional_edge.json` | yes | survival 53-58% -> 90-96% (**artifact, see above**) |
| `inversions.json` | yes | eligible **48 -> 562**, tested **48 -> 196**, beat buy-and-hold **0 -> 0** |
| `dispersion_gate_smoke.json` | no | ES_F cost 1.422 -> `inf`; was genuinely stale |
| `toll_collector.json` | no | **timestamp only**, all numbers identical - was NOT stale |
| `vr_fingerprint.json` | no | **byte-identical** - was NOT stale |
| `constraint_sweep.json` | no | **not regenerated**, see below |
| `dispersion_gate.json` | no | **not regenerated**, see below |

The inversions conclusion is unchanged and now much better supported: zero of
196 tested inversions beat buy-and-hold, up from zero of 48. Best edge is
-$3.99. Fading these strategies does not work either.

`toll_collector` runs Binance crypto pairs only, and CRYPTO never enters the
contract-sizing path, so D-249 could not touch it - confirmed empirically by
the one-line timestamp diff. `vr_fingerprint` is pure variance-ratio
statistics on closes with no cost model and no harness; unchanged data means
unchanged output. Its exit code 1 is its kill-condition verdict
("FINGERPRINT UNSTABLE"), not a crash - it wrote its file normally.

**Two I deliberately did not regenerate.** `constraint_sweep.json` (Aug 13
19:02) and `dispersion_gate.json` (Aug 14 08:57) both POSTDATE the cost_model
fix at 17:45 and neither reads the graveyard, so neither is stale by any
mechanism I can identify. `constraint_sweep` is also ~75 minutes. If you
disagree with the cut line, say so and I will run them.

`summary.json` and `harness_validation.json` were already current; I re-ran
`validate_harness.py` anyway: **21/21 passed, exit 0** (convention 1).

Consistency check against the judge pack, all matching:
`verdict_counts {FAIL: 508647, NOT_TESTED: 26345, PASS: 381, PASS_BENCHMARK: 52}`,
`entries_total 535425`, `distinct_findings 155`.

---

## Collision: a second Cody session ran the same task in this same directory

While I was working, another session (shell snapshot `5z7yuf`, started 15:48)
was running the identical regeneration into `logs/regen/` - `inversions`,
`toll_collector`, `vr_fingerprint`, plus `constraint_sweep`. Mine wrote to
`/tmp/regen/`. Both write the same output JSONs in `research/graveyard/`, and
both share one git index.

Almost certainly spawned against the superseded
`from-raven/2026-08-14-stale-analysis-and-gitignore-fix.md`, which asks for
the same Task 2.

Impact was low: `inversion.py` has no `random`/`seed` calls, so both runs are
deterministic. Its `inversions` and `constraint_sweep` processes died partway
without writing output; mine completed. I did not kill its processes and did
not touch its staged index (`DECISIONS.md`, three handoffs,
`harness_validation.json` are staged by it, not me).

It has since pushed two commits on top of mine: `563854e` (removed CLAUDE.md,
HANDOVER.md, SOUL.md files and from-raven handoffs from the public repo) and
`1cf5144` (replaced the Telegram chat ID with a placeholder - that closes F3).
So all four audit findings F1-F4 are now closed.

**Worth fixing at the protocol level:** two Raven files with overlapping tasks
produced two Cody sessions racing the same files and the same git index.

## Note on the wake-up file

`CLAUDE.md` is now untracked (`563854e`). I still rewrote it per the epilogue
rule - it is a local briefing file, and the rewrite no longer reaches GitHub.
Added conventions 17 (a hardcoded threshold is an assumption with an expiry
date; if a metric improves after a step that only removed data, suspect the
baseline), 18 (`pgrep -f` self-matches the waiting shell - wait on a PID), and
19 (this working directory can be shared by two sessions).

## What I did not do

- Did not modify any code (Raven's instruction). Findings 1, 2 and 4 all need
  a decision.
- Did not touch `DECISIONS.md` (convention 15 - these are reasoning changes,
  not factual corrections).
- Did not touch D-226 - Aym's call.
- Did not commit any regenerated analysis JSON. All in the working tree,
  uncommitted, awaiting your review, as instructed.

## Working tree state (uncommitted, for Raven review)

```
M research/graveyard/asset_class.json
M research/graveyard/conditional_edge.json      <- artifact, read Finding 1
M research/graveyard/dispersion_gate_smoke.json <- contains Infinity tokens
M research/graveyard/inversions.json
M research/graveyard/pooled.json
M research/graveyard/toll_collector.json        <- timestamp only
```

## Next steps for Raven

1. Rule on Finding 1. `conditional_edge.json` should not be committed while it
   prints "ABOVE chance" - it is the exact false positive the script was
   written to prevent, and it would poison the judge work under D-264.
2. Rule on Finding 2 (FAIL vs NOT_TESTED for unsizable instruments). Either
   futures leave the universe at this capital, or the verdict gets a third
   state.
3. Decide whether `constraint_sweep.json` / `dispersion_gate.json` need
   regenerating despite postdating the cut line.
4. Fix the double-spawn so two sessions do not race the same files again.
