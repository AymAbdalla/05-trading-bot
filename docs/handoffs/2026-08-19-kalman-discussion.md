# Handoff: Kalman pairs discussion + the belief-to-trade paper

**Session:** `cody-kalman-discuss`, 2026-08-19 ~08:2x EDT.
**Directive:** `docs/handoffs/from-raven/2026-08-19-kalman-discussion.md`.
**Deliverable:** `docs/PLAN-2026-08-19-kalman.md`.
**HEAD at start:** `ccc7991`, tree clean. Read-only on `db/trading.db`.

---

## The decision, in one line

**Reject the Kalman cross-asset spread strategy. Adopt the paper's risk module.
Refuse the paper's quarter-Kelly sizing module until a forecaster demonstrates
calibration.**

## Why (the part that does not depend on sample size)

A Polymarket short is a purchase of the complement, so the pairs trade is
`buy asset1-UP + buy asset2-DOWN`. Its payoff is **linear** in the two outcome
indicators, so:

```
edge = (q1 - p1) - (q2 - p2)
```

Two exact consequences:

1. The edge is the difference of the two legs' **marginal** miscalibrations.
   That is a forecast of both marginals — the fair_value model's job, differenced.
   **It is a forecaster in disguise**, which is what Raven asked me to check.
2. Because the payoff is linear, the **joint** distribution drops out of the
   expected value. Correlation/cointegration/beta — everything the Kalman filter
   estimates — affects **variance only, never return**. The filter estimates a
   parameter irrelevant to the edge.

Corollary: the difference of two calibrated prices is calibrated (means are
linear). That is Raven's Q1, answered as a theorem rather than a measurement.

## What I built and measured (new capability, worth keeping)

BTC/ETH/SOL 5m markets share the window epoch in the slug, so a **cross-asset
price panel is joinable** — nobody had built one. 23,951 observations, 380
windows, 284 with all three assets. Outcomes are NOT in `candles` (stops
2026-08-11); I derived 625 window outcomes from the fact that **window N+1's
strike is window N's close**.

**Positive control:** panel calibration reads -0.0307 (t=-1.01); the independent
`temporal_arbitrage` route reads -0.0279 (t=-0.79). Two unrelated methods
agreeing to 3 decimals.

Findings:
- **Assets do co-move** (btc/eth φ=+0.529, agree 76.6%, n=188) — but that is
  variance structure, not edge.
- **Every leg is calibrated** (n=189, diff -0.031, t=-1.01).
- **Backtest is positive but never significant**: best t=1.63 on n=54 at z=2.0.
  Same shape as the D-326 error.
- **Leg decomposition is perfectly additive** (+0.1237 = +0.0741 + 0.0496, no
  interaction) — confirms the algebra empirically.
- **Leave-one-asset-out swings t from -0.21 to +3.08.** Sample slicing.
- **The z-score is structurally invalid**: spread dispersion expands 0.104 →
  0.547 (settlement). A "2σ divergence" mid-window is entirely ordinary against
  the terminal scale.
- **The reversion exit Raven specified is worse** than hold-to-settlement
  (t=0.69 with costs).
- **Quarter-Kelly on our book makes it 9% worse per share** (-0.0231 → -0.0253,
  n=1,299; corr(size, PnL) = -0.026). Right formula, wrong input.

## What Raven needs to decide

1. **Accept or override the rejection.** If overridden, the kill condition is in
   §5 of the plan: dead unless taker-only t ≥ 2.0 on n ≥ 250 **with
   leave-one-out minimum t ≥ 1.0**. The leave-one-out clause is load-bearing —
   without it the pooled number alone would have passed this at z=2.5.
2. **The risk module sequencing.** I recommend it does NOT go on the ~03:45
   2026-08-20 restart (already fully loaded). It belongs to the restart after,
   behind its own tests.
3. **Whether to keep the panel** as a standing measurement artefact. My read:
   yes, it costs no book slot and it is the reusable output of this session.

## Questionable / incomplete — flagging honestly

- **Everything empirical here is underpowered.** n=54 backtest trades, 189
  price/outcome pairs, leave-one-out cells of 11 and 16. The data cannot prove
  the strategy fails; the algebra is what carries the argument.
- **The up-price is derived**, not observed (`up = ask` or `1-ask` by
  `outcome_side`), which mixes two sides and manufactures a synthetic bid-ask
  bounce. This is why my first reversion regression looked strong (t=-4.59) and
  then failed the lag test (t=-1.31 at lag 1, back to -4.96 at lag 2 — a
  non-monotone pattern that real mean reversion does not produce). I report the
  first regression as **not a finding**.
- **Outcomes are derived**, not read from venue resolution. This is exactly the
  gap **proposal 038** closes — 038 would materially improve this panel's
  trustworthiness. One more argument for landing it.
- Costs are modelled (0/0.006/0.012 per leg), not measured. Conclusion does not
  turn on it: the strategy fails at zero cost.
- **`market_tape` untouched** (026/037 mid-measurement until ~03:28 2026-08-20).

## Not done

- No code, config, DB writes, or `DECISIONS.md` changes. Read-only as directed.
- No test suite run — docs-only session, no code touched.
- Did not touch the running loops (71360/71394/71442) or the restart plan.

## Environment note (contradicts CLAUDE.md, worth recording)

**`AGENT_ID` read `cody-kalman-discuss` this session** — SET, on a gateway spawn.
CLAUDE.md currently states the Hermes gateway spawn path does not export it
(open item 12, from `cody-forge-reasoner-c2`'s empty reading at 07:02). Either
the gateway now exports it or Raven set it explicitly on this spawn. **Open item
12 should be re-examined rather than treated as settled in either direction.**
The Write tool was also available.
