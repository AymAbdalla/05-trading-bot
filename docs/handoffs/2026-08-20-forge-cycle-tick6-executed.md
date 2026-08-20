# Forge cycle tick 6 - executed (`cody-forge-reasoner-tick6`)

**When:** 2026-08-20, measurements 04:24Z-04:35Z (local 00:24-00:35 EDT).
**Brief:** `docs/handoffs/from-raven/2026-08-20-forge-reasoner-cycle-tick6.md`
**HEAD at start:** `4aa3c5683593592785c4de8539464f6eaadb276c` - verified, matched
the brief's state line (third correct one in a row; still do not trust it,
convention 25).

**Written:** proposals 045, 046, 047 + one `forge_runs.jsonl` record.
**No code changed. No database written. No loop touched. Suite/harness NOT
re-run** - no importable file was modified (three `.md` files and one `.jsonl`
append).

---

## The one finding that reshaped the cycle

The brief's priority 1 was a TWAP amendment, on the premise that "the settlement
regime changed under every hold-to-resolution strategy on 2026-08-07 and the
shadow stack's exit economics were calibrated pre-TWAP." The brief correctly
required verifying the TWAP-affected subset before claiming anything.

Verified, one query:

- **0 of 3,803** positions in `db/trading.db` opened before 2026-08-07.
- **0 of 2,053** in `db/trading-survivors.db`.
- Earliest opens: **2026-08-18T03:02:21Z** and **2026-08-19T04:40:54Z** -
  eleven and twelve days *after* the change.

The cross-sectional control fails too. TWAP excludes hourly crypto and
non-crypto; we hold **0 hourly** positions in either book, and the non-crypto arm
is **8 weather positions with exactly 1 close ever** (+7.353, survivors - the
same single trade 044 already refused to read as evidence). 99.9% of the book is
TWAP-covered.

So there is no time-series contrast and no cross-sectional contrast. The regime
change is absorbed into our baseline. Every per-share figure this stack has ever
produced was already a post-TWAP measurement.

That also kills the brief's **priority 3** with the same number: **0 of 75**
maker fills predate TWAP (env B has 0 maker fills at all), so the pre/post-Aug-7
maker split has an empty arm at any future n. 042's markout instrument itself is
untouched - only the pre/post *cut* is refused.

---

## What was written

### 045 - `pm_twap_settlement_regime_unidentifiable` (governance)
Refuses brief priorities 1 and 3 on identification, with the counts above and
three numbered reversal conditions. Records one real gap it deliberately does
**not** act on: `SALVAGE_FLOOR = 0.10`
(`strategies/polymarket/fair_value_settlement_exit.py:271`) is a flat price
trigger with no time argument, justified in its docstring on a
terminal-distribution claim, and **no file in `engine/` or `strategies/`
mentions TWAP, averaging or a settlement window** (grepped). Rule 3 forbids
moving any exit threshold on the TWAP mechanic.

Reversal condition 1 (a 200-close single-print control arm) explicitly
**declines to buy the control** - it says do not add hourly or non-crypto
markets to satisfy it, because allocation in a bleeding book is a cost this
proposal did not price. Written so Raven can overrule it explicitly.

### 046 - `pm_counterfactual_independent_unit_repair` (repair) - the big one
**043's kill band is narrower than one sigma at 043's own bar.**

Group matched `sell:salvage_floor` positions by `(market_slug, outcome_side)`:
cluster settle rates take **exactly {0.0, 1.0}**. A market-side resolves once,
so every share keyed to it shares one outcome. Measured:

| | positions | shares | market-sides | shares/cluster |
|---|---|---|---|---|
| trading.db | 144 | 2,754 | 123 | 22.4 |
| survivors | 127 | 2,481 | 107 | 23.2 |

A per-share SE understates the true one by **4.7x / 4.8x**. At 043's
400-position bar the cluster-level SE is **0.0127** (A) and **0.0141** (B)
against a kill band of **0.010** - i.e. **0.79 and 0.71 sigma**.

Root cause traced to 043's own sentence: the band is justified as "4x the 0.0025
net directional bias", which budgets **ledger measurement error** and contains
no allowance for **sampling error**. Two error sources, one budgeted.

The three recorded sign flips are the symptom, and the full walk reproduces
043's and D-354's exact historical readings:

```
n=25 +0.0620   n=59 +0.0283   n=100 -0.0140   n=143 +0.0063
n=50 +0.0422   n=69 -0.0014   n=126 -0.0022   n=144 +0.0066
```

0.076 range = 3.6x the cluster sigma. Mean exit price is stable to four decimals
across halves (0.0653 vs 0.0655), so the instability is **entirely in the
clustered settle rate**. Survivors flips sign between its own halves: **-0.0524**
early, **+0.0476** late.

The repair only **prints** the sigma and gates the verdict at 3 sigma. It
deliberately does **not** re-size the band or the bar - D-354 R2 refused a
mid-experiment re-size and that should hold for a reason found later too. The
threshold question (move the bar to ~5,800/~7,100 positions, or widen the band
to ~0.038/0.042) is **referred to Raven**.

### 047 - `pm_counterfactual_source_filter_gap` (repair) - latent, not live
043 grades `source = 'venue'`. `counterfactual()`
(`backtest/settlement_coverage.py:603`) defaults to `LIVE_SOURCES` =
`('venue', 'inferred_terminal_price')` (`resolution_ledger.py:110`), and the
tool prints the wider set in its own header on every run. **Zero rows affected
today** - both ledgers are venue-only (786 / 390) and nothing writes the second
source. Filed because the day something does, the failure is silent in three
places, the worst being that rule 6's self-check would **lose the independence
its own code comment claims** and its disagreement rate would *fall* - an error
bar shrinking because the instrument got worse.

---

## Questionable / for Raven

1. **046 and 047 both question a live instrument that tick 5 built and D-354
   ruled on.** Neither re-scopes 043, changes its band/bar, or grades it. But
   two repairs against one instrument in one cycle is a lot, and if Raven reads
   043 as a fast decision rule that deliberately accepts a high error rate
   rather than as a hypothesis test, then 046's 3-sigma gate should be dropped
   and only the printed sigma kept. I wrote the two as separable for that.
2. **047 may have the arrow backwards.** The fix could equally be to loosen
   043's *text* rather than tighten the *code*. I recommended tightening the
   code because the self-check's independence argument is written for the venue
   field specifically - but it is a ruling, and the kill condition says so.
3. **046's sigma is a lower bound.** The market-side is not truly independent
   either (Up/Down of one window are perfectly anti-correlated; adjacent windows
   on one asset share a spot path). The residual can only make 043's band look
   *worse*, so the conclusion is robust - but 0.0127 is a ceiling on how good
   the answer can be, not the answer.
4. **`dip_arb` - not re-proposed, referred as an execution question.** The vault
   `_DIGEST.md` **already records KILL RECOMMENDED** at 138 trades / -49.73.
   Lifetime is now **348 closes / -179.23 / WR 0.181** (trading.db) and 58 /
   -20.60 / WR 0.172 (survivors) - the loss has grown ~3.6x and the
   recommendation is unexecuted under convention 13. A new proposal would add a
   second recommendation, not a decision.
5. **`mid_price_continuation` is not a rotation candidate yet.** Brief flagged
   +22.33 / WR 0.600 on n=15. Lifetime is n=114 / **+19.39** / WR **0.518** - so
   this window is *more than the entire lifetime P&L* and the lifetime win rate
   is a coin flip.
6. **034 censoring re-derived, flat again.** trading.db 527 closes / 282
   salvaged = **53.5%**; survivors 926 / 574 = **62.0%**. Against 041's
   53.0%/61.9% and tick 5's 52.1%/61.5%: three windows, ~1,300 added closes,
   rate unmoved. 041's prediction confirmed a third time. Folded in as context
   per the brief; not re-litigated.
7. **`AGENT_ID` read EMPTY** on this gateway spawn; committed via the sanctioned
   `CONFLICT_CHECK_AGENT_ID` fallback. Tally becomes **6 SET / 12 EMPTY**.
   **Write GRANTED and Edit GRANTED** this session (Write 5/4, Edit 2/1).

---

## Deferred / not done

- 037 left **blocked** (not unblocked, per brief constraint). The Signal 3
  dump-and-hedge external corroboration is real, but I did not file the
  corridor_pair cross-window probe: `corridor_pair` is n=73 / -2.00 lifetime and
  absent from env B, and filing a new measurement family while its shared
  resolution-join instrument has an open sizing defect (046) would silt the
  queue. Recorded as `blocked_upstream` in `forge_runs.jsonl`.
- 039 clock left young and its counting scheme untouched (3 resolution-matched
  time_stop observations in env A, 0 in env B).
- 038 backfill **not run** (still deferred, D-354 R4).
- Suite and harness **not re-run** - inherited, now two sessions stale.

## Caveat on every number here

**Both books are LIVE UNDER READ and moved during the session:** ledger rows
780 -> 786 (env A) and 372 -> 390 (env B), matched salvage 143 -> 144 and
126 -> 127, maker fills 73 -> 75. Every figure is point-in-time (convention 25).
