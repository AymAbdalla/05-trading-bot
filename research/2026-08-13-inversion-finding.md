# Research Finding: Strategy Inversion (SPEC 5.6) Is Fee Drag, Not Signal

**Date:** 2026-08-13
**Author:** Claude Code
**Status:** Empirical result, first real test of the inversion premise
**Data:** `research/graveyard/inversions.json` (48 tests), partial v0 graveyard
(2,205 entries) on the fixed harness + clean data

## The claim being tested

SPEC 5.6 and Aym's original idea: "A strategy with PF 0.07 on 55 trades isn't
just bad. It's reliably wrong. That's a signal pointing the wrong direction.
Invert it and you might have PF 1.5+."

The harness-validation review (finding F2) predicted this would fail:
inverting a strategy inverts its gross edge but NOT its costs, so a
net-PF failure that is mostly fee drag inverts into a differently-shaped
fee-drag loser.

Until tonight, inversion had never actually been tested. The graveyard
carried 1,733 `inversion_flagged` markers and zero inverted runs.

## Method

V1 long-only inversion type: SIGNAL-AS-EXIT (fade). Base position is always
long; the failed entry signal becomes an exit trigger; re-enter next bar.
Compared against plain buy-and-hold over the identical window, identical
notional, full fees and slippage on every leg. Measured out-of-sample (last
20% holdout only).

F2 gate applied BEFORE testing (all three required):
1. Gross PF (pre-fee) <= 0.90 - the failure must be a negative gross edge,
   not a cost artifact.
2. >= 30 trades - sample adequacy.
3. Out-of-sample measurement, carrying the original's hypothesis count.

Of the flagged failures: 498 flagged, 9 rejected outright as cost-driven
(gross PF > 0.90), many more rejected for sample size, 48 eligible and tested.

## Result

**48 tested. 0 beat buy-and-hold. Median edge: -$132.58.**

The decisive detail is not that they lost, but HOW they lost:

| Exits taken | Fade edge vs buy-and-hold |
|---|---|
| 65 | -$16.75 |
| 2,757 | -$861.16 |

Edge per exit is essentially constant at **-$0.31**. Round-trip cost on a
$100 notional position at 0.10% taker each way plus 0.05% slippage each way
is **$0.30**. The "inverted edge" is, to the cent, the trading cost of
stepping out and back in.

There is no anti-signal. There is a toll booth.

## What this means

1. **The V1 inversion premise is dead as stated.** A failed long entry
   signal does not carry exploitable contrarian information as an exit
   trigger. It carries a fee bill.
2. **F2 was right and the gate earns its keep.** The 9 flags rejected for
   gross PF > 0.90 would have been pure noise mining; the sample-size
   rejections would have been tail-of-distribution draws.
3. **Do not report `inversions_flagged` as a finding count.** A flag means
   "eligible for a test that has now been run and failed," not "an
   opportunity found."
4. **The remaining inversion type is untested.** The contrarian-filter form
   ("block new entries for N candles after this signal") is not implemented,
   because it only means anything paired with a base entry strategy, making
   it a combinatorial test. Whether it is worth building is now a much
   weaker bet than it looked yesterday.

## Recommendation

- Keep the inversion machinery. It is cheap, gated, and it converts a
  standing hypothesis into a settled question per graveyard run.
- Retire the expectation. Forge and Quant should read this file before
  proposing inversion-based strategies. The graveyard entry for any flagged
  failure should link here.
- If V3 shorting ever lands, full inversion (buy when it sells) is a
  different test and this result does not settle it. But it does set the
  prior: costs are the dominant term at this notional and timeframe.

## Caveats

- Measured on a PARTIAL graveyard (2,205 of ~271k planned entries), heavily
  weighted to crypto 15m and a few equity 1h series. The per-exit cost
  arithmetic is universal, but the strategy mix is not yet representative.
- Single fade design (exit on signal, re-enter next bar). A variant that
  stays out for N candles instead of re-entering immediately would pay fewer
  tolls; that is a different test and is not run here.
- These carry the ORIGINAL hypotheses' multiple-comparison burden. Even if
  one had "won," it would need out-of-sample confirmation against the full
  grid's hypothesis count before meaning anything.
