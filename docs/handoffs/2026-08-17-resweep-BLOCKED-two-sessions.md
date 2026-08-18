# BLOCKED: two Cody sessions were spawned for the same work, with conflicting rulings

**Date:** 2026-08-17 21:35
**By:** Cody (session A, PID 15357, started 21:22)
**Status:** stopped before the sweep. Nothing was re-run. No graveyard was overwritten.
**Needs:** Raven to pick one instruction file. This is not my call.

## What happened

Raven wrote two instruction files 24 minutes apart and spawned a Claude session
for each. Both cover the same four harness fixes. They disagree.

| | session A (me) | session B (PID 15666) |
|---|---|---|
| started | 21:22 | 21:24 |
| instruction file | `from-raven/2026-08-17-resweep-with-harness-fixes.md` | `from-raven/2026-08-17-rulings-and-shadow-go.md` |
| ruling ids | D-269..D-272 | R-005..R-011 |
| scope | harness fixes + re-sweep only | harness fixes + re-sweep + **Polymarket shadow loop, live** |
| method | single session | "USE SUBAGENTS", three in parallel |

Session B is alive and was editing `strategies/builtin/expanded.py` (21:29) and
`strategies/builtin/strategy_lab.py` (21:31) while I was reading them.

## The conflicts, precisely

**1. D-numbers collide and mean different things.**

| D-number | my file | session B's file |
|---|---|---|
| D-269 | bar starvation (min_idx) | rsi_extreme threshold (R-007) |
| D-270 | confirmation stack | C2 anchor lookback (R-008) |
| D-271 | rsi_extreme threshold | C2 stale rows (R-009) |
| D-272 | C2 lookback + stale rows | — |

`docs/DECISIONS.md` currently holds **my** numbering (D-269 bar starvation
through D-272 C2). Session B noticed the collision and renumbered on the fly:
the new `rsi_extreme` docstring cites "D-273 / R-007". So DECISIONS.md and the
code now disagree about what D-269 means.

**2. Opposite ruling on the 9,042 stale C2 rows.**
My file: *"Delete the 9,042 stale C2 rows."*
R-009: *"Archive, do not delete."*
These cannot both be executed. R-009 is the better call (it preserves the audit
trail), but it is not the one I was given.

**3. Different mean-reversion cohorts.**
R-006 names 8 strategies and includes `V3_intraday_momentum_crypto`. My D-270
says trend-following keeps the stack, and V3's thesis is momentum ("the first
half hour's return predicts the last half hour's return"), not reversion. Under
D-270's own criterion V3 does not belong in the cohort. R-006 also turns off
`require_regime_uptrend` separately; D-270 only turns off the whole stack.

**4. Both sessions were told to re-run the full sweep against the same file.**
This is the dangerous one. `research/graveyard/v0_graveyard_full.json` is 389MB
and the sweep is incremental-by-key. Two concurrent sweeps with different
harness configurations would interleave into one file with no way to tell which
row ran under which config. That is exactly the silent pooling the
`cost_model_version` stamp exists to prevent, and it would not raise.

## What I did and did not do

Did:
- Snapshotted the pre-sweep baseline to `docs/handoffs/pre-resweep-snapshot.json`
  (convention 17). **Neither of session B's instructions asks for this**, so it
  is worth keeping whichever file wins. It confirms Raven's 9,042 figure exactly.
- Backed up the graveyard to
  `research/graveyard/archive/v0_graveyard_full.pre-D269-D272.json` (copy, not move).
- Wrote `backtest/snapshot_graveyard.py`, a streaming reader so the 389MB file
  can be summarised without a 389MB `json.load`.
- Wrote `strategies/cohorts.py` with the cohort, the membership criterion, and
  the two held-out sets.
- Saved my harness implementation as a patch, then **reverted the shared file**
  so session B works from a clean base:
  `docs/handoffs/patches/2026-08-17-cody-session-A-vectorized_harness.patch`

Did NOT:
- Run the sweep. Not started, not queued.
- Overwrite or delete any graveyard.
- Touch `expanded.py` or `strategy_lab.py`. Session B owns those right now
  (convention 21).
- Touch `dashboard/`.

## Baseline captured (this is real and reusable)

```
total_rows          535,425
distinct_findings    48,675  (strategy x ticker x timeframe)
FAIL                486,350
NOT_TESTED           48,642
PASS                    381
PASS_BENCHMARK           52
C2 stale rows         9,042   <- matches Raven's number exactly
```

Per-timeframe rows: 5m 107,690 / 15m 109,505 / 1h 107,690 / 1d 105,875 /
1wk 104,665.

## Two findings that survive whichever file wins

**Two of the nine are not covered by any ruling.** D-269..272 and R-005..R-009
between them cover seven of the nine non-firing strategies. These two are
untouched, and will still be non-firing after the re-sweep:

- `rising_three_methods` (#2). Binding clause is `small_reds`, 2 hits in 32,679.
  The diagnosis's suggested fix is `0.7 -> 1.0` ATR, a pure threshold loosening.
  Nobody ruled on it.
- `V4_trend_reclaim` (#4). 27 of 27 candidates die on `volume_min_ratio >= 1.2`
  alone, not on the regime filter. Its thesis is trend-following, so both
  cohort rulings correctly exclude it, which means the cohort fix does not
  help it. The diagnosed fix is "exempt weekly bars from volume_min_ratio",
  which nobody ruled on.

Reporting these as "still non-firing after the fixes" without saying they were
never in scope would misread as evidence about the strategies.

**The `max(min_bars, 25)` floor scans into unconverged indicators.**
`_ema` seeds the pre-convergence region with `closes[0]`, so on bars before
index ~49 the precomputed `ema50` is a seed, not an EMA-50, and
`regime_uptrend` is False there by construction. On a 101-bar daily slice the
ruling takes the scan from 1 bar to 76, and roughly 24 of those 76 sit in the
seeded region. Most strategies self-guard on their own `len(closes)` checks so
this is not fatal, but any PASS that lands on bars 25-49 of a daily series
needs a second look before it is believed. Both rulings specify the floor of
25, so I flag rather than deviate.

## What I recommend

Kill one session, not both. Session B is further along, its ruling ids are
internally consistent, R-009 (archive, don't delete) is the better call, and it
also carries the shadow-loop work Aym asked for. Session A's distinct value is
the baseline snapshot and the two scope gaps above, which are files on disk and
survive independently.

Whichever runs the sweep should stamp each row with which arm it ran under.
The patch above adds `confirmation_stack_applied` and `scan_start_idx` to every
graveyard row for exactly that reason: without it, the post-sweep graveyard has
no record of which strategies had the stack disabled, and convention 17's
before/after comparison cannot be made from the artifact itself.

## Answering the four "after the sweep" questions

I cannot. There was no sweep. Convention 11 applies to this handoff too:
NOT_RUN, not ran-and-found-nothing.
