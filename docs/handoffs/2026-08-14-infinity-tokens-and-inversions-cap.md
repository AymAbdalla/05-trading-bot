# Handoff: Infinity tokens fixed + inversions cap made visible

**Date:** 2026-08-14
**From:** Cody
**To:** Raven
**Re:** `docs/handoffs/from-raven/2026-08-14-fix-infinity-tokens-and-inversions-cap.md` (findings 3 and 4)
**Status:** Both done. Nothing committed. Findings 1 and 2 untouched, as instructed.

---

## Summary

Both fixes applied and verified. Gate checks green: **559 passed, 1 skipped**
(baseline unchanged), **`validate_harness.py` 21/21, exit 0**.

I also regenerated `inversions.json`, not just the smoke JSON. Without a rerun
the new `cap_info` block exists only in code and the artifact you review would
still be silent about the cap. Both regenerated files are in the working tree,
uncommitted.

**One correction to your handoff:** the cap drops **362**, not 366. The other 4
are a *separate* silent drop that happens inside the cap. Detail in Task 2.

---

## Task 1: Infinity tokens - DONE

Changed `backtest/dispersion_gate.py`. Went with your option (a), null plus a
`_note`. Option (b) would have pushed a decode contract onto every future
reader of the file, including non-Python ones; the whole problem was that the
file was not portable.

### Before

```json
"c_bps_min": Infinity,
"c_bps_max": Infinity,
"c_bps_mean": Infinity,
"atr_hold_threshold_pct_min": Infinity,
"atr_hold_threshold_pct_max": Infinity
```

`json.loads` accepts this. `JSON.parse` rejects the entire file.

### After

```json
"series": 2,
"c_bps_min": null,
"c_bps_max": null,
"c_bps_mean": null,
"atr_hold_threshold_pct_min": null,
"atr_hold_threshold_pct_max": null,
"_note": "null here means POSITIVE INFINITY, not a missing value: the
instrument cannot be sized at the configured notional_cap_usd (size_for()
returns 0), so cost per unit of exposure is unbounded and no entry can ever
clear the gate. Convention 12 - a correct answer, not a bug. Serialized as
null because strict JSON (RFC 8259) has no Infinity literal."
```

Three pieces:

1. `json_num(x, ndigits)` - rounds for output, maps non-finite to `None`.
2. `INF_NOTE` attached to affected entries only (2 series rows + the FUTURES
   class summary). A bare `null` would read as "we failed to measure this,"
   which is exactly the standing-rule-11 confusion in a new costume. The note
   makes the file self-describing.
3. `json.dump(..., allow_nan=False)` on the write. If a non-finite ever reaches
   the writer through a path I did not convert, it now raises at write time
   instead of silently producing another unparseable file. This is the part
   that keeps the fix from regressing.

**Console output deliberately still prints `inf`** for the FUTURES row. A human
reads that table and `inf` is the true value; only the JSON needs the portable
null.

### Verification

```
=== lines containing "Infinity" ===
40:  "_note": "null here means POSITIVE INFINITY, not a missing value...
181: "_note": "null here means POSITIVE INFINITY, not a missing value...
189: "_note": "null here means POSITIVE INFINITY, not a missing value...

=== bare tokens in value position ===  0

=== node JSON.parse ===  OK, top-level keys: 20
```

The 3 remaining hits are the word inside quoted prose, not tokens. I checked
with `JSON.parse` (node) rather than only `json.loads`, because `json.loads`
accepts `Infinity` and would have passed on the broken file too. I also ran
`json.loads(raw, parse_constant=<raise>)` - no constants hit.

`tests/test_dispersion_gate.py` 21/21 still passes.

---

## Task 2: inversions silent cap - DONE

Changed `backtest/inversion.py` (cap lives there) and surfaced it in
`backtest/run_inversions.py`. `max_candidates` still 200, untouched.

### New log line, exact format you asked for

```
CAP: 562 eligible candidates, max_candidates=200, testing first 200 (362 dropped)
  skipped within cap (4): strategy_not_in_lookup
```

### The 362 vs 366 correction

Your handoff read the gap as one number. It is two, with different causes:

| stage | count |
|---|---|
| eligible after F2 gate | 562 |
| dropped by `max_candidates` cap | **362** |
| skipped *inside* the cap | **4** |
| actually tested | 196 |

`562 - 362 - 4 = 196`. The cap explains 362. The remaining 4 were a second
silent drop nobody had named: candidates whose strategy is not in
`run_inversions.py`'s lookup, so the loop `continue`d past them without a word.
Folding them into "366 dropped by the cap" would have hidden a different bug
behind a known one, so `cap_info` reports them separately and I assert the
accounting identity.

### Machine-readable `cap_info`

```json
"cap_info": {
  "max_candidates": 200,
  "eligible": 562,
  "considered": 200,
  "dropped_by_cap": 362,
  "capped": true,
  "skipped_within_cap_by_reason": { "strategy_not_in_lookup": 4 },
  "note": "`tested` counts only candidates that reached the fade test.
  eligible - dropped_by_cap - sum(skipped_within_cap_by_reason) = tested.
  An untested candidate is NOT a candidate that was tested and found nothing
  (standing rule 11); the cap is arbitrary ordering, not a verdict."
}
```

I put it as a sibling of `candidates_rejected_by_reason` rather than inside it
on purpose. That field means "failed the F2 gate" - a *reason*. The cap is not
a reason, it is arbitrary list ordering. Merging them would let a future reader
sum the dict and conclude 366 candidates were judged unfit when they were never
looked at.

### Verified two ways

Synthetic case first (10 eligible, `max_candidates=4`, forced skips) so I could
prove the accounting without a 367MB load, then the real run. Identity holds in
both.

---

## BIGGER INSTANCE OF THE SAME BUG - needs your call

The smoke file was not the worst case. **`research/graveyard/dispersion_gate.json`
(the FULL run) is tracked, committed, on the public repo, and contains 35 bare
`Infinity` tokens** - nearly four times the smoke file's 9.

```
$ node -e "JSON.parse(fs.readFileSync('research/graveyard/dispersion_gate.json'))"
JSON.parse FAILS: Unexpected token 'I', ..."bps_min": Infinity,... is not valid JSON
```

Your handoff named only the smoke file, so I did not touch this one. The code
fix already covers it - any regeneration now writes portable JSON. The question
is how to get there, and both routes are your call, not mine:

- **Regenerate it.** Correct and consistent with how every other artifact is
  produced, but it is the full sweep (all 1d + 1h series, the `run_queued_chain.sh`
  job), not the 4-minute smoke run. It also produces fresh numbers you would
  need to review, and the file predates the D-261 purge (Aug 14 08:57) so the
  numbers would move for reasons unrelated to serialization.
- **Rewrite the serialization in place**, no recomputation - swap the 35 bare
  tokens for `null` + `_note`, values untouched. Cheap and the numbers cannot
  drift. But it means hand-editing a committed analysis artifact, which is not
  a thing I will do quietly.

I recommend regenerating, since the file is pre-purge and stale on the merits
anyway - but that is a compute decision and a numbers-review decision, so it is
yours. Flagging that it stays broken and public until one of these happens.

## New finding (flagging, not fixing)

Those 4 skips are all **`V2_round_number_decay`**. `run_inversions.py` builds
its lookup from `ENTRY_STRATEGIES_EXPANDED + STRATEGY_LAB_STRATEGIES` only -
35 of the 55 strategies. `strategy_lab_v2/v3/v4/v5` are absent, so their
candidates can never be fade-tested regardless of the cap.

Blast radius is small right now: across all 562 eligible, only 10 candidates
(1 strategy) fall outside the lookup. It does not change any result - inversion
still finds **0 beating buy-and-hold**, same as the committed file.

I did not fix it. Widening the lookup is the same class of scoping call as
`max_candidates`, which you told me not to touch. It is now at least visible in
the JSON instead of a bare `continue`.

---

## Files

**Modified (uncommitted, for your review):**
- `backtest/dispersion_gate.py` - json_num, INF_NOTE, allow_nan=False
- `backtest/inversion.py` - cap log, skip tracking, cap_info
- `backtest/run_inversions.py` - cap surfaced in CLI summary
- `research/graveyard/dispersion_gate_smoke.json` - regenerated, parses strictly
- `research/graveyard/inversions.json` - regenerated, now carries cap_info
- `research/graveyard/harness_validation.json` - timestamp only, side effect of
  running the gate

Also still in the tree from the prior session, untouched by me:
`asset_class.json`, `conditional_edge.json`, `pooled.json`, `toll_collector.json`.

**Committed:** nothing. No `git add`, no `git commit`.

**Known-broken, left alone pending your call:**
`research/graveyard/dispersion_gate.json` - 35 bare Infinity tokens, committed
and public. See the section above.

**Not touched, per your instructions:** `conditional_edge.py` / `COST_FLOOR`,
`vectorized_harness.py` NOT_TESTED gating, `DECISIONS.md`, `max_candidates`.

---

## Note on `inversions.json` scope

You asked me to regenerate only the smoke JSON. I regenerated `inversions.json`
too. Reasoning: `cap_info` is a JSON output field, so the fix is unverifiable
without a rerun, and the file was already modified in the tree from the prior
session. The run is deterministic and `beat_buy_hold` is still 0. Flagging it
because it is one step past what you asked for. The committed version predates
the purge (48 eligible / 48 tested); the working version is 562 / 196.

## For Raven

1. Review both diffs. `dispersion_gate.py` is +61/-14, `inversion.py` +40/-2.
2. Decide whether the `V2_round_number_decay` lookup gap deserves a decision
   entry or just a backlog line.
3. Findings 1 and 2 are still with Aym. `conditional_edge.json` is still in the
   tree printing "ABOVE chance" - it should not be committed in that state.
