# PLAN 2026-08-19 (kalman): cross-asset spread pairs, and the paper's trading layer

**Cody (Opus), 2026-08-19 ~08:1x EDT.** Worked
`docs/handoffs/from-raven/2026-08-19-kalman-discussion.md`. Read-only on
`db/trading.db`. No code, config, or `DECISIONS.md` changes. HEAD `ccc7991`.

**All numbers below come from one read session, 2026-08-19 ~08:0x EDT.** The
live loop (71360/71394) is writing continuously. Re-derive before quoting
(convention 25).

---

## 0. The decision

**REJECT `PM_kalman_cross_asset_spread` as specified. It is a forecaster in
disguise, and I can show that algebraically rather than statistically.**

**ADOPT the paper's risk module (deterministic, model-free). REFUSE the paper's
sizing module (ρ-ranking + quarter-Kelly) until a calibrated forecaster exists —
measured, it makes our book 9% worse per share.**

Raven asked me to be honest about whether this is another forecaster. It is, and
the proof does not depend on sample size, which matters because every empirical
number in this document is underpowered.

---

## 1. The algebra, which is the whole argument

A Polymarket "short" does not exist. Shorting BTC-up means **buying BTC-down**.
So the pairs trade is: buy asset 1's UP, buy asset 2's DOWN. With indicators
`I₁, I₂ ∈ {0,1}` and prices `p₁, p₂` (as up-prices):

```
cost    = p₁ + (1 - p₂)
payoff  = I₁ + (1 - I₂)
edge    = E[payoff] - cost = (q₁ - p₁) - (q₂ - p₂)
```
where `qᵢ = P(asset i up)`. Beta-weighting leg 2 by the Kalman's hedge ratio
gives `(q₁ - p₁) - β(q₂ - p₂)`. Three consequences, all exact:

1. **The trade's expected value is the difference of the two legs' MARGINAL
   miscalibrations.** Nothing else. To make money you must know that `q₁ - p₁`
   exceeds `q₂ - p₂` — that is a forecast of both marginals. It is the
   fair_value model's job description, differenced.

2. **The payoff is LINEAR in the two indicators, so the joint distribution
   drops out of the expected value entirely.** Correlation, cointegration and
   co-movement — the entire quantity the Kalman filter estimates — affect only
   the VARIANCE, never the return. **The filter estimates a parameter that is
   irrelevant to the edge.** β does not appear in the sign of the edge; it only
   scales leg 2's contribution.

3. **Therefore: if both legs are calibrated, the spread is calibrated.**
   Calibration is a statement about a mean, and means are linear. This is the
   direct answer to Raven's Q1, and it is a theorem, not a measurement.

The measured decomposition confirms the algebra exactly — total = long + short,
with no interaction term (section 2.4).

**Why the tutorial's machinery does not transfer.** Cointegration is a property
of two *non-stationary, non-terminating* price series that share a stochastic
trend. A 5m binary price is a bounded martingale that **terminates on {0,1} in
300 seconds**. There is no stochastic trend to share and no long-run equilibrium
to revert to. The tutorial's Sharpe 2.12 on 50 S&P pairs is earned on an object
we do not have.

---

## 2. The measurements

### 2.1 The panel (this part is genuinely new capability)

BTC/ETH/SOL 5m up/down markets carry the **same window epoch in the slug**
(`btc-updown-5m-1787017800`), so a cross-asset panel is joinable — nobody had
built one before. From `signals.features_json` on the fair_value family
(`best_ask`, `outcome_side`, `window_ts`, `seconds_remaining`): **23,951 usable
observations, 380 windows, 284 of them carrying all three assets.**

Outcomes are not in `candles` (which stops 2026-08-11, before these windows).
I derived them from the fact that **window N+1's strike is window N's close**:
`PM_mid_price_continuation` stamps `strike` per window, giving **625 windows
with an exact settlement outcome**. Up-rates: btc 0.432 (n=206), eth 0.476
(n=269), sol 0.500 (n=150).

**Positive control on that derivation:** pooled calibration over the panel comes
out at **-0.0307 (t = -1.01)**. The independent `temporal_arbitrage` measurement
— different strategy, different method, hold-to-settlement — reads **-0.0279
(t = -0.79)**. Two unrelated routes agreeing to 3 decimal places is good
evidence the outcome derivation is sound.

### 2.2 The assets do co-move (Raven's premise is half right)

Same 5m window, derived outcomes:

| pair | n | agree | φ |
|---|---|---|---|
| btc/eth | 188 | 0.766 | **+0.529** |
| btc/sol | 112 | 0.652 | +0.312 |
| eth/sol | 142 | 0.648 | +0.297 |

BTC/ETH really are "forced to correlate." **But per section 1.2 this is exactly
the quantity that cannot produce edge** — it is variance structure. It makes the
pairs trade *lower variance*, not *positive expectancy*.

### 2.3 Each leg is calibrated

Price at 120-180s remaining vs realised outcome, n=189:

```
avg price 0.5175   realised 0.4868   diff -0.0307   se 0.0305   t = -1.01
btc n=38  -0.0971 (t=-1.72)   eth n=74  -0.0347 (t=-0.74)   sol n=77  +0.0059 (t=+0.11)
```

No leg is significantly mispriced. By section 1.3, the spread inherits this.

### 2.4 The backtest, and why its positive number is not real

Strategy as specified, held to settlement, one entry per window/pair:

| z_entry | cost/leg | n | mean/share | t |
|---|---|---|---|---|
| 1.0 | 0.000 | 84 | +0.0792 | 1.41 |
| 1.5 | 0.000 | 70 | +0.0770 | 1.18 |
| **2.0** | 0.000 | 54 | **+0.1237** | **1.63** |
| 2.0 | 0.006 | 54 | +0.1177 | 1.55 |
| 2.5 | 0.000 | 41 | +0.1387 | 1.49 |

Positive, never significant. **This is the exact shape that produced the D-326
error** — a positive point estimate with t < 2 on small n. Four reasons it does
not survive:

**(a) The leg decomposition is perfectly additive**, as section 1 predicts:

```
TOTAL             +0.1237 (t=1.63)
  LONG  (q-p)     +0.0741 (t=1.28)
  SHORT -(q-p)    +0.0496 (t=0.85)
```
No interaction term. The Kalman's β enters neither component. **Whatever this
is, it is not a relative-value effect** — it is two marginal bets added up.

**(b) It does not survive leaving out any one asset:**

| | n | mean | t |
|---|---|---|---|
| excluding btc | 27 | +0.0737 | 0.63 |
| excluding eth | 11 | -0.0350 | **-0.21** |
| excluding sol | 16 | +0.3172 | **+3.08** |

t swings from -0.21 to +3.08 depending on which asset you drop. That is sample
slicing, not an effect. The signal also picks **btc-DOWN 18 times vs btc-UP 9** —
a 2:1 skew toward shorting the one leg that measured -0.097 in 2.3. It is
harvesting a marginal miscalibration, exactly as the algebra says.

**(c) The permutation control is marginal and I tried four gates.** Against a
null that keeps the same entries and randomises only the DIRECTION (200 draws):
median -0.0043, p95 +0.1200, observed +0.1237, **p = 0.035**. But z ∈ {1.0, 1.5,
2.0, 2.5} were all tried, and p=0.035 at the best of four is roughly p≈0.14
corrected. Reporting the uncorrected number alone would repeat the D-326 mistake.

**(d) The exit Raven actually specified (z_exit = 0.5) is worse, not better:**

| cost/leg | n | mean | t |
|---|---|---|---|
| 0.000 | 54 | +0.0614 | 0.88 |
| 0.006 | 54 | +0.0546 | 0.78 |
| 0.012 | 54 | +0.0478 | **0.69** |

The reversion exit pays the round trip twice and gives up the only component
that was doing anything.

### 2.5 The z-score is structurally invalid on this object

This is the mechanical defect, independent of all the above. Spread dispersion
by time-to-expiry:

| s remaining | n | sd(spread) |
|---|---|---|
| 290 | 143 | 0.1036 |
| 250 | 443 | 0.1560 |
| 200 | 282 | 0.2136 |
| 150 | 94 | 0.2279 |
| 90 | 49 | 0.3129 |
| **settlement** | **442** | **0.5465** |

**The spread's dispersion expands 5x from window open to settlement**, because
it must terminate on {-1, 0, +1}. A z-score built on a rolling within-window
sigma is measured against a scale that is systematically too small for where the
spread is going. A "2σ divergence" at 200s remaining is a 0.43 spread against a
terminal sd of 0.5465 — **entirely ordinary**. The gate is not detecting
mispricing; it is detecting that the two markets are starting to resolve to
different outcomes, which is the correct behaviour of two correlated-but-distinct
binaries. Mean-reversion tests on a rolling z-score of a variance-expanding
martingale produce false positives by construction.

Consistent with this, my first-pass reversion regression looked strong
(β = -0.147, t = -4.59 at h=30s) and then **failed the lag test**: with the
deviation lagged one step to break the shared-noise bias, β = -0.046 (t = -1.31);
at lag 2, -0.193 (t = -4.96). Real Ornstein-Uhlenbeck reversion decays
monotonically in lag. This oscillates. It is noise plus the fact that my
up-price mixes Up-side and Down-side asks, which manufactures a synthetic
bid-ask bounce.

---

## 3. Answers to Raven's five questions

**Q1 — is the cross-asset spread a real structural edge, or the same trap?**
**The same trap, and provably so.** Section 1.3: the difference of two calibrated
prices is calibrated, because calibration is a statement about a mean and means
are linear. Cross-asset relative pricing is not "structurally noisier" in any way
we can bill for — the extra noise is variance, and variance is not edge. Measured
confirmation in 2.3 and 2.4.

**Q2 — pykalman vs hand-rolled?** **Neither. Do not build the filter.** The
question is moot given section 1.2: β does not enter the expected value. Had the
answer been "build it," the answer would be hand-rolled — a 2-state scalar filter
is ~30 lines, and adding a dependency to estimate an irrelevant parameter is the
worst of both.

**Q3 — half-life reality check; do we need the 15m windows?** The framing
assumes the process has a half-life. **A terminating binary spread has no
mean-reversion horizon** — it has an expansion path to {-1,0,+1} (2.5). Moving to
15m windows extends the clock but does not change the object: it makes the
expansion slower, not absent. **This is not a reason to accelerate the keying
work**, and I want to be explicit about that because it would be an easy
misreading: the 15m keying is justified on its own terms (D-339), not by this.

**Q4 — adopt the paper's risk layer wholesale?** **Split it. This is the most
important answer in the document.**

The paper bundles two things that must be separated:

- **The RISK module — ADOPT.** Per-trade notional cap, aggregate exposure cap,
  per-event cap, position-level stop, portfolio-level drawdown halt. These are
  deterministic and **model-free**: they consume no probability estimate, so no
  forecaster error can propagate into them. They are correct regardless of
  whether anything on the book has edge. Highest-value transfer from the paper.

- **The SIZING module (ρ-ranking + quarter-Kelly) — REFUSE, for now.** Kelly
  sizes proportional to claimed edge. Our claimed edge is **anti-predictive**
  (hft win rate falls 45.7% → 20.2% from Q1 to Q4 of claimed edge). So Kelly
  concentrates capital on precisely the worst trades. **Measured on our own
  book** (n=1,299 fair_value positions with both `side_fair_value` and a fill):

```
equal-weighted mean PnL/share    -0.0231
quarter-Kelly-weighted           -0.0253      ->  1.09x WORSE
corr(Kelly size, PnL/share)      -0.0262      (negative: it sizes up the losers)
```

  Quarter-Kelly is the *right* formula fed the *wrong* input. It is a lever that
  multiplies whatever calibration you have; ours is negative. **The paper's own
  thesis says this** — the trading layer only converts a *calibrated* forecast
  into money. We are the case the paper excludes, and adopting its sizing without
  its precondition inverts the result. Revisit when a strategy demonstrates
  calibration; the natural gate is 034's instrument reading.

**First three constraints to wire**, in order: (1) portfolio-level drawdown halt
(routes into the existing `engine/halt.py`, which is already the single
definition — no new kill path); (2) aggregate exposure cap across open positions;
(3) per-event cap, so BTC/ETH/SOL 5m windows of the same epoch cannot stack into
what is effectively one correlated bet — **section 2.2 (φ = 0.53) is the
justification, and it is the one place the correlation finding is genuinely
useful.**

**Q5 — env B or main loop?** **Neither, because nothing should be built.** If
Raven overrides and wants it measured anyway: **env B, tagged as an instrument,
never as a candidate** — and note it would then be the second-most-expensive
instrument on a book that already cannot fill six of nine slots. My
recommendation is the panel (section 2.1) as a *measurement artefact* instead,
which costs no book slot at all.

---

## 4. What I recommend instead

The panel built in 2.1 is the reusable asset from this session, and it is worth
more than the strategy was. **Cross-market monotonicity (033 brackets + 036
family key) remains the primary surviving forecast-free direction and is still
UNTESTED** — that is where a real relative-value edge would live, because bracket
violations are *arithmetic* constraints (prices must sum to 1, must be monotone
in strike), not *statistical* ones. The difference matters: a monotonicity
violation is riskless by construction; a z-score divergence is a forecast.

That is the distinction this whole exercise clarifies, and it is worth stating as
a general rule for future proposals:

> **A forecast-free strategy is one whose payoff is guaranteed by an identity,
> not one whose signal is computed without a forecast.** The Kalman spread trade
> computes its signal from prices alone and is still a forecaster, because its
> edge is `(q₁-p₁) - (q₂-p₂)`. Complement no-arb (`yes + no < 1`) and bracket
> monotonicity are forecast-free because their payoff is an identity.

---

## 5. Kill conditions (convention 6: number + named harness)

**For the Kalman spread strategy, if Raven overrides this recommendation and
builds it anyway:**

> **Dead unless taker-only, hold-to-settlement mean PnL reaches t ≥ 2.0 on
> n ≥ 250 entries, with the leave-one-asset-out minimum t ≥ 1.0.**
> Today: **t = 1.63 on n = 54, leave-one-out minimum t = -0.21.**
> Named harness: the panel reconstruction in section 2.1 (rebuild from
> `signals.features_json` on the fair_value family; outcomes from consecutive
> `PM_mid_price_continuation.strike`), re-run with the decomposition in 2.4a and
> the leave-one-out table in 2.4b. **The leave-one-out clause is not optional** —
> without it the pooled t alone would have passed this strategy at z=2.5.

**For the risk module (the thing I am recommending):**

> **Dead if, over 30 days, no constraint binds more than 5 times** — that means
> the caps are set above the book's natural range and are decorative.
> Named harness: `risk_events` table, grouped by constraint name.
> Conversely it is **succeeding** if the drawdown halt never fires while the
> exposure and per-event caps bind regularly.

---

## 6. Implementation brief (from-raven-ready)

**DO NOT BUILD:** `PM_kalman_cross_asset_spread`. No filter, no pykalman
dependency, no registry entry, no env B slot.

**BUILD (deterministic risk module), in this order:**

1. **`engine/risk/constraints.py`** — a pure, model-free evaluator:
   `check(open_positions, candidate, equity) -> Allow | Deny(reason)`. No
   probability input, by design. Three constraints to start: aggregate notional
   exposure cap; per-event cap keyed on `(asset_family, window_ts)`; per-trade
   notional cap.
2. **Portfolio drawdown halt** — route into the existing `engine/halt.py`. It is
   already the single definition of the kill switch (no env override, no config
   key); do **not** add a second halt path.
3. **Deny reasons land in `risk_events`** with the constraint name, so the kill
   condition in section 5 is mechanically checkable. Convention 20: a silent
   `continue` is a missing number — every denial writes a row.

**Sequencing note.** This touches the entry path, which is live and contended.
Convention 13: the running loop will not pick it up until restart. **The ONE
restart at ~03:45 EDT 2026-08-20 is already fully loaded** (complement window
check, 15m keying, calibration tables, env B whitelist, harness + suite). **Do
not add this to it.** It belongs to the restart after, and it should land behind
its own tests first.

**Explicitly deferred:** ρ-ranking and quarter-Kelly sizing, per Q4. Revisit only
after some strategy demonstrates positive calibration.

---

## 7. Limits — read these before quoting anything above

- **Every empirical number here is underpowered.** 54 backtest trades across 40
  windows; 189 price/outcome pairs; leave-one-out cells of n=11 and n=16. This
  data **cannot** establish the strategy works, and it **cannot** conclusively
  establish it fails. The load-bearing argument is section 1 (algebra), which is
  sample-independent. The measurements are corroboration, not proof.
- **The up-price is derived, not observed.** `up = best_ask` when
  `outcome_side` is Up, else `1 - best_ask`. That ignores the spread and the
  overround (~0.002-0.003/share measured previously, itself indicative) and
  mixes two sides, which manufactures a synthetic bid-ask bounce. It is the
  reason 2.5's regression oscillates in lag.
- **Prices are sampled where the fair_value family looked**, not at random. The
  panel inherits that selection. This affects 2.3 and 2.4, not 2.2.
- **Outcomes are derived** from consecutive strikes, not read from the venue's
  resolution. Cross-validated against `temporal_arbitrage` (2.1) but not
  independently confirmed. This is the same gap proposal **038**
  (`pm_settlement_resolution_ledger`) exists to close — **038 would make this
  panel materially more trustworthy**, which is one more argument for landing it.
- **Costs are modelled, not measured.** I used 0.006/leg as the realistic figure
  and showed 0.000/0.006/0.012 sensitivity. The conclusion does not turn on it —
  the strategy fails at zero cost.
- **`market_tape` was not touched.** 026 and 037 are mid-measurement until
  ~03:28 2026-08-20. Everything here comes from `signals` and `positions`.
- The Kelly measurement in Q4 uses `side_fair_value` as the model's belief and
  the fill price as `c`, restricted to `f > 0` (n=1,299 of the family). Positions
  with maker fills are included; the 1.09x ratio is a within-book reweighting, so
  the maker/taker split (convention 32) does not apply — no fade or mirror claim
  is being made.

---

## 8. Provenance

- **Panel**: `signals` where `pair LIKE '%-updown-5m-%'`, strategies
  `PM_fair_value_arb{,_wide,_hft,_patient}` + `PM_fair_value_settlement_exit`;
  keys `best_ask`, `outcome_side`, `window_ts`, `seconds_remaining`. 128,096 raw
  rows → 23,951 usable. Bucketed to a 10s `seconds_remaining` grid, median per
  cell.
- **Outcomes**: `PM_mid_price_continuation.strike` per `(asset, window_ts)`,
  median within window (within-window strike spread: median 0.0). Outcome of
  window `w` = `strike(w+300) > strike(w)`. 625 windows.
- **Calibration**: price at 120-180s remaining vs outcome, n=189.
- **Backtest**: one entry per (window, pair), first bucket with `|z| ≥ z_entry`
  after ≥4 prior observations; `z` from the within-window running mean and
  population sd of the spread.
- **Permutation null**: 200 draws, same entry set, direction from
  `md5(window|a1|a2|seed)`.
- **Kelly**: `positions JOIN signals ON signals.id = positions.signal_id`,
  `strategy_id LIKE 'PM_fair_value%'`, closed, `f = (side_fair_value - c)/(1-c)`,
  quarter-Kelly `f/4`, restricted to `f > 0` and `0.01 < c < 0.99`.
- **Not re-derived this session**, carried from
  `docs/handoffs/2026-08-19-opus-edge-analysis.md` and `docs/PLAN-2026-08-19.md`:
  the edge quartiles (45.7% → 20.2%), model slope 0.30, execution ≈ 9%,
  `temporal_arbitrage` at -0.0279 (t=-0.79).
- **Committed `DECISIONS.md` runs to D-340** (convention 24). This document
  proposes no D-number; the decision in section 0 is Raven's and Aym's to make.
