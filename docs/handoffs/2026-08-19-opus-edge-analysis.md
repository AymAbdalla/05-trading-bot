# Opus edge analysis: what actually has edge in the shadow data

**Cody, 2026-08-19 ~00:45 EDT.** Worked
`docs/handoffs/from-raven/2026-08-19-opus-edge-analysis.md`.
Read-only on `db/trading.db`. No code, config, or DB writes. Main loop
(PID 35848) untouched and still live.

**All numbers come from one consistent snapshot taken 2026-08-19T00:44:07-04:00**
inside a single read transaction. The DB is being written continuously by the
live loop: the closed-position count moved 1700 to 1716 during this session.
Re-derive before quoting. Convention 25 applies to counts, not just PIDs.

---

## Headline

**The critic's "execution cost" diagnosis is wrong.** The loss is not the spread
and it is not the round trip. It is directional: the book systematically buys
binary contracts that are worth less than it pays. Four independent measurements
say so, and one of them is exact arithmetic rather than inference.

The whole book: **1,716 closed positions, net -$873.11, t = -7.96.** That is not
noise. The per-trade standard deviation is 2.64, so a zero-edge book of this size
would have a sum standard error of about $109. The observed loss is eight of them.

---

## Task 1.1: the edge sweep

Every strategy with >= 15 closed positions. `c/share` is net divided by total
shares, the only unit comparable across strategies (quantity ranges 5 to 28).
`t` is the per-trade expectancy over its standard error.

| strategy | n | WR% | net | exp/tr | c/share | t | verdict |
|---|---|---|---|---|---|---|---|
| PM_fair_value_arb | 502 | 38.8 | -221.31 | -0.4409 | -2.34 | -4.90 | KILL |
| PM_fair_value_arb_hft | 376 | 32.7 | -221.48 | -0.5890 | -3.20 | -6.30 | KILL |
| PM_fair_value_arb_inverse | 266 | 51.5 | -65.11 | -0.2448 | -1.73 | -2.68 | KILL |
| PM_dip_arb | 198 | 26.3 | -79.31 | -0.4006 | -2.06 | -2.25 | KILL |
| PM_grid_hedge | 100 | 26.0 | -178.16 | -1.7816 | -6.27 | -2.94 | KILL |
| PM_temporal_arbitrage | 82 | 17.1 | -4.06 | -0.0495 | -0.99 | -0.25 | UNPROVEN |
| PM_box_builder | 65 | 24.6 | -54.30 | -0.8354 | -16.71 | -3.35 | KILL |
| PM_fair_value_arb_wide | 54 | 50.0 | -17.38 | -0.3218 | -1.68 | -0.98 | UNPROVEN |
| PM_mid_price_continuation | 21 | 47.6 | -6.56 | -0.3124 | -3.23 | -0.30 | UNPROVEN |
| PM_corridor_pair | 17 | 52.9 | -11.25 | -0.6618 | -13.24 | -1.12 | UNPROVEN |

Drawdown and streak detail (same snapshot family, taken minutes earlier, so
counts differ by a few trades):

| strategy | avg win | avg loss | W/L | break-even WR | max DD | longest losing streak |
|---|---|---|---|---|---|---|
| PM_fair_value_arb | +1.432 | -1.638 | 0.87 | 53.4% | -257.46 | 15 |
| PM_fair_value_arb_hft | +1.312 | -1.513 | 0.87 | 53.6% | -221.48 | 13 |
| PM_fair_value_arb_inverse | +0.772 | -1.325 | 0.58 | 63.2% | -69.23 | 7 |
| PM_dip_arb | +3.098 | -1.646 | 1.88 | 34.7% | -81.72 | 17 |
| PM_grid_hedge | +6.216 | -4.591 | 1.35 | 42.5% | -197.20 | 7 |
| PM_temporal_arbitrage | +3.771 | -0.836 | 4.51 | 18.1% | -14.05 | 15 |
| PM_box_builder | +2.353 | -1.877 | 1.25 | 44.4% | -54.30 | 7 |
| PM_fair_value_arb_wide | +1.747 | -2.391 | 0.73 | 57.8% | -21.95 | 5 |
| PM_mid_price_continuation | +4.731 | -4.917 | 0.96 | 51.0% | -29.74 | 4 |
| PM_corridor_pair | +1.389 | -2.969 | 0.47 | 68.1% | -15.25 | 2 |

Every one of the ten is below its own break-even win rate. Not one strategy in
the book has a positive expectancy.

---

## Task 1.2: execution or model? The answer is model, and it is not close

The directive asked me to apply the inverse-variant control logic. I did, and
then I found four sharper tests that all point the same way. I am reporting the
disagreement with the critic explicitly because the whole survivor plan rests on
which diagnosis is right.

### Fact 1: there is almost no execution cost to blame

- **`fees` is 0.00 on all 1,716 closed positions.** Not null, zero.
- **`pnl_net == (exit_px - entry_px) * qty` on all 1,716, with zero mismatches.**
  There is no cost term in the PnL at all beyond what is embedded in the prices.
- **Entry lands at the ask the strategy saw.** Over the 1,430 positions whose
  signal recorded `best_ask`: `entry_px - best_ask` has mean +0.00157, median
  0.00000, and 1,079 of 1,430 are exact to within 1e-9.
- **The book spread is tiny.** Over 2,820 tape rows carrying both sides: mean
  spread 0.0052, **median 0.0010**, p90 0.0100.

Total realistic round trip: about **0.26 cents per share** (0.16 slippage vs ask
plus 0.10 half-spread). The book loses **2.97 cents per share**. Execution
explains at most **9%** of the loss.

### Fact 2: the model's own claimed edge is anti-predictive

If the loss were a fixed execution toll, higher claimed edge would produce better
net results, because a bigger edge clears a fixed toll more easily. Instead:

| PM_fair_value_arb | n | avg claimed edge | WR% | avg PnL |
|---|---|---|---|---|
| Q1 [+0.044,+0.086] | 124 | 0.064 | 37.1 | -0.591 |
| Q2 [+0.086,+0.151] | 124 | 0.116 | 43.5 | -0.451 |
| Q3 [+0.153,+0.226] | 124 | 0.186 | 41.1 | -0.362 |
| Q4 [+0.226,+0.652] | 125 | 0.328 | 32.8 | -0.394 |

| PM_fair_value_arb_hft | n | avg claimed edge | WR% | avg PnL |
|---|---|---|---|---|
| Q1 [+0.021,+0.057] | 94 | 0.037 | 45.7 | -0.267 |
| Q2 [+0.057,+0.146] | 94 | 0.098 | 30.9 | -0.541 |
| Q3 [+0.147,+0.287] | 94 | 0.209 | 34.0 | -0.802 |
| Q4 [+0.288,+0.552] | 94 | 0.379 | 20.2 | -0.746 |

For hft the win rate **falls from 45.7% to 20.2%** as the model's confidence
rises. Every quartile of every variant loses money. The model is not a weak
signal being eaten by costs. It is pointing the wrong way, and pointing harder
makes it worse.

### Fact 3: the mechanism. The model barely looks at the market

Regression of the model's `side_fair_value` on the market's `best_ask`, over
17,336 signals carrying both:

```
ALL signals              n=17336   FV = 0.3821 + 0.3015*ask   r=+0.4751
NOT acted (gate refused) n=16139   FV = 0.3771 + 0.3121*ask   r=+0.4872
ACTED (gate fired)       n= 1197   FV = 0.4359 + 0.1963*ask   r=+0.3429
```

A forecaster that respects the market has slope near 1.0. This one has **slope
0.30**, and on the trades it actually takes, **0.196**. Its output is compressed:
sd 0.111 against the market's 0.174, and **87% of its forecasts land between 0.4
and 0.6** while market asks span 0.01 to 0.99.

The consequence is mechanical. A model pinned near 0.5 declares an "edge" of
`0.5 - price` every time the market moves away from 0.5. It is not finding
mispricing. It is rediscovering that cheap things are cheap and calling that
edge. And the gate makes it worse: acting selects precisely the signals where
the model diverges most from the market, which is why the acted slope (0.196) is
lower than the refused slope (0.312). **The entry gate is a filter for model
error.**

The fair value observed by ask bucket makes it visual. The model says roughly the
same thing at every price:

| ask paid | n | avg model FV | claimed edge | WR% | total |
|---|---|---|---|---|---|
| 0.0 | 15 | 0.407 | +0.376 | 20.0 | -7.13 |
| 0.1 | 67 | 0.465 | +0.359 | 22.4 | -26.95 |
| 0.2 | 171 | 0.472 | +0.268 | 31.0 | -96.41 |
| 0.3 | 188 | 0.496 | +0.195 | 35.6 | -110.51 |
| 0.4 | 276 | 0.513 | +0.120 | 38.4 | -154.77 |
| 0.5 | 173 | 0.546 | +0.047 | 46.8 | -65.80 |
| 0.6 | 112 | 0.542 | -0.057 | 48.2 | -21.83 |
| 0.7 | 86 | 0.588 | -0.108 | 53.5 | -24.59 |
| 0.8 | 76 | 0.575 | -0.217 | 46.1 | -16.26 |
| 0.9 | 25 | 0.586 | -0.311 | 68.0 | -2.72 |

The win rate tracks the **market price**, not the model. The market is the better
forecaster at every level.

### Fact 4: the exact test. Mirroring the settled trades flips the sign

345 positions exited at exactly 0 or 1, meaning they were held to settlement.
For those, mirroring is **exact arithmetic, not a simulation**: buying the
complement at `1-e` and settling at `1-x` gives per-share PnL of `e-x`, the
precise negation. Only the round trip is estimated.

```
observed : n=345  shares=4850  net=-294.35  (-6.07 c/share)
mirrored : n=345  shares=4850  net=+281.74  (+5.81 c/share)
realised 0.252 vs paid 0.339; edge -0.087 = -3.73 SE  -> REAL
```

Per strategy, settled only:

| strategy | n | avg paid | realised | edge | mirrored net |
|---|---|---|---|---|---|
| PM_grid_hedge | 100 | 0.393 | 0.260 | -0.133 | +170.77 |
| PM_temporal_arbitrage | 82 | 0.181 | 0.171 | -0.010 | +2.99 |
| PM_box_builder | 65 | 0.413 | 0.246 | -0.167 | +53.45 |
| PM_mid_price_continuation | 21 | 0.503 | 0.476 | -0.027 | +6.03 |
| PM_dip_arb | 19 | 0.046 | 0.000 | -0.046 | +16.08 |
| PM_corridor_pair | 17 | 0.662 | 0.529 | -0.132 | +11.03 |

There is no stop, no target, no round trip inside these numbers. Entry at a
price, settlement at 0 or 1, zero fees. **Every one buys above the realised
frequency.** That is a pure model error measurement and it cannot be attributed
to execution by any argument.

### Why the inverse variant misled the critic

`PM_fair_value_arb_inverse` loses (51.5% WR, -$65.11), and the critic read
"inverse also loses, therefore the cost is execution." That inference does not
hold, because **inverse is not the mirror of the parent.** It re-runs the whole
pipeline on the other side with its own gate and its own barriers:

- Different trade population: 266 trades vs the parent's 502.
- Different barrier geometry: target 5.19% / stop 9.85% of entry, against the
  parent's 13.43% / 24.38%.

It is a second independently-broken strategy, not a control. Its own numbers say
so: at 51.5% WR with a 0.58 win/loss ratio it needs **63.2%** to break even. It
loses because it risks 9.85% to make 5.19% with no directional edge, which is a
different failure from the parent's, not the same toll paid twice.

The real barrier problem is worth stating on its own. For the whole fair_value
family the stop is roughly twice the target in price terms:

| strategy | median win move | median loss move | ratio | null P(target) | observed P |
|---|---|---|---|---|---|
| PM_fair_value_arb | 13.43% | 24.38% | 0.55 | 0.645 | 0.392 |
| PM_fair_value_arb_hft | 13.64% | 27.31% | 0.50 | 0.667 | 0.332 |
| PM_fair_value_arb_inverse | 5.19% | 9.85% | 0.53 | 0.655 | 0.515 |
| PM_dip_arb | 37.82% | 38.10% | 0.99 | 0.502 | 0.291 |
| PM_fair_value_arb_wide | 20.51% | 41.82% | 0.49 | 0.671 | 0.500 |

"null P(target)" is what a **driftless, cost-free random walk** would hit given
those barriers. Every strategy comes in 14 to 34 points **below** the coin-flip
null. Caveat, stated plainly: the barriers are dispersed (p10 to p90 spans
3% to 48% on the parent), so this uses medians and is an approximation, not an
exact test. The size of the gap is the point. A 25 point win-rate shortfall
cannot be produced by a 0.26 cent per share toll.

### Verdict on Task 1.2

**Execution accounts for roughly 9% of the loss. The remaining 91% is the model
buying contracts worth less than it pays.** The repair the critic proposed
(hold to settlement, or move to the maker path) targets the 9%. Worse, as Task
1.5 shows, holding to settlement makes the other 91% strictly larger.

---

## Task 1.3: time of day. No window survives a permutation test

Pooled by EDT hour of entry, 19 hours are represented. Two are positive: hour 0
(n=31, +$7.62) and hour 21 (n=58, +$2.50). Worst is hour 15 (n=41, -$88.12).

Splitting by strategy and hour gives 50 buckets with n >= 10, of which 10 are
positive. Best single bucket: `PM_grid_hedge` at hour 21, n=10, +$23.19.

I tested whether that best bucket is real by shuffling the PnL labels across
buckets 2,000 times and recording the best bucket each time:

```
observed best bucket net = +23.19
null distribution:  med=+19.51  p95=+38.06  max=+68.41
p-value = 0.342  ->  NOT significant
```

**There is no time-of-day edge in this data.** The best window is comfortably
inside what pure noise produces once you look at 50 buckets. Anything built on
"strategy X wins in window Y" from this dataset is fitting noise. This is the
answer for temporal_arb in volatility regimes and dip_arb after big moves alike:
the data cannot support either claim yet.

---

## Task 1.4: the cody-env-b whitelist, validated and corrected

The env-b whitelist (10 strategies):
`PM_temporal_arbitrage, PM_dip_arb, PM_fair_value_arb_wide,
PM_small_liq_continuation, PM_fair_value_arb_patient,
PM_longshot_fade_hold_to_resolution, PM_weather_bracket_width_matched,
PM_fair_value_settlement_exit, PM_weather_arb, PM_streak_snapper`

| whitelisted | closed n | evidence | my call |
|---|---|---|---|
| PM_temporal_arbitrage | 82 | -$4.06, t=-0.25, settled edge -0.010 | **KEEP.** Best in the book. Closest to fair of anything measured. |
| PM_dip_arb | 198 | -$79.31, **t=-2.25** | **DROP.** Confirmed loser at 2 sigma. Does not belong on a clean book. |
| PM_fair_value_arb_wide | 54 | -$17.38, t=-0.98 | **DROP.** Same broken model as the parent, just a wider gate. Unproven only because n is small. |
| PM_fair_value_settlement_exit (034) | 0 | never entered | **KEEP but re-gate.** See Task 1.5. Its premise is refuted; run it only as an instrumented probe. |
| PM_small_liq_continuation | 3 | +$3.20 | KEEP. No evidence either way. |
| PM_fair_value_arb_patient | 4 | +$1.99 | KEEP with the same caveat as wide: it inherits the broken model. Low priority. |
| PM_longshot_fade_hold_to_resolution (032) | 0 | never entered | **KEEP, raise to top priority.** It is the only thing in the registry pointed the right way. See the Forge brief. |
| PM_weather_bracket_width_matched (033) | 0 | never entered | KEEP. Untested, off the crypto tape, genuinely independent. |
| PM_weather_arb | 1 open, 0 closed | never settled | KEEP. Same reason. |
| PM_streak_snapper | 13 | -$4.75, t=-0.14 | KEEP. Below the n>=15 bar, no verdict possible. |

**Corrections to the whitelist: drop `PM_dip_arb` and `PM_fair_value_arb_wide`.**

- `dip_arb` is a **confirmed** loser (t=-2.25), which is the same standard that
  got grid_hedge and box_builder paused under D-323. Including it is
  inconsistent with the rule that paused the others. Its rationale in the env-b
  directive was "subject of the 031 tape experiment," which is a reason to keep
  measuring it, not a reason to put it on a book meant to reveal survivor edge.
- `fair_value_arb_wide` is the parent's model with a looser gate. The model is
  the thing that is broken. Three of its four siblings are confirmed killers and
  the fourth (patient) has n=4. Keeping wide on the survivor book risks
  reproducing the exact bleed env-b was built to remove.

Also worth flagging: **six of the ten whitelisted strategies have never opened a
closed position** (034, 032, 033, weather_arb has 1 open, plus patient at n=4 and
small_liq at n=3). Env B will be measuring almost nothing for a while. That is
fine as a design, but it means the A/B will not produce a verdict on the
"starved survivors" hypothesis quickly, and nobody should read an early flat
equity curve on env B as a result.

The env-b hypothesis itself ("the fair_value family was dragging the book down
and starving the survivors") is **half right**. It was dragging the book down:
the three fair_value variants are -$507.90 of the -$873.11, which is 58%. But
"starving" implies the survivors have edge that concurrency slots were blocking.
Nothing in this data supports that. The starvation is real and measurable
(034's top skip reason is `max_trades_this_window` at 643 of 1,131), but a clean
book only helps if something on it has edge, and the only candidate at present
is temporal_arb at t=-0.25.

---

## Task 1.5: what 034 needs to prove, and why the premise is backwards

**034 has never entered a trade.** 1,131 signals, zero acted, zero positions.
Skip reasons:

```
max_trades_this_window                    643   (57%)
edge_below_threshold                      139
fair_value_no_window_open                 138
insufficient_book_depth                    45
too_late_in_window                         40
unfillable_at_cap                           29
strategy_concurrency_cap_reached            27
adapter:SKIP:max_concurrent_positions       27
risk_gate:max_concurrent_positions          17
settlement_entry_ask_above_cap               5
```

### The break-even number

034 holds to settlement, so a share pays 0 or 1 and **break-even win rate equals
the entry ask.** Over the 10,630 fair_value-family signals that would pass 034's
gate (`edge >= 0.05`, `ask <= 0.60`):

```
entry ask: mean=0.3300  median=0.3400  p10=0.1500  p90=0.5000
=> 034 must win 33.0% of settled trades to break even.
```

### Does the tightened gate plausibly get there? No, on three counts.

**1. The gate barely tightens anything.** Adding `ask <= 0.60` on top of
`edge >= 0.05` removes only **13.2%** of the population (12,244 to 10,630), and
in the live record only **5 of 1,131** signals were refused for
`settlement_entry_ask_above_cap`. The cap is close to non-binding.

**2. Raising the edge threshold moves 034 into the parent's worst region.**
034 uses `EDGE_THRESHOLD = 0.05` against the parent's 0.04. But the parent's
quartile table above shows results get **worse** as claimed edge rises: hft
falls from 45.7% WR in Q1 to 20.2% in Q4. Tightening the edge gate selects
harder for the model error, not against it. The gate is pointed the wrong way.

**3. The empirical calibration in 034's own entry band falls well short.**
Pooling every settled position in the book, restricted to entry 0.15 to 0.55
(69% of 034's gate population):

```
n=203  avg_paid=0.352  realised=0.256
034 needs realised >= 0.330. Observed in the band: 0.256.
```

A 7.4 point shortfall against the requirement. Caveat stated honestly: this
curve is measured on contracts **these strategies selected**, not a random
sample of the market, so it measures the selection, not the market. That is
exactly why it applies to 034, which uses the same fair_value selector.

### The premise is backwards: settlement is worse than the stop

034 exists to "halve the round trip" by holding to settlement instead of exiting
twice. Two problems.

First, the round trip it is halving is **0.26 cents per share**. Halving it saves
0.13 cents against a 2.97 cent loss.

Second, and much worse, the book already contains both styles and settlement is
the **losing** one:

```
intraday exit (stop/target)  n=1371  24,516 shares  -578.76   -2.36 c/share
hold to settlement           n= 345   4,850 shares  -294.35   -6.07 c/share

restricted to entry 0.15-0.55 (034's band):
intraday exit                n= 953  18,210 shares  -472.24   -2.59 c/share
hold to settlement           n= 203   2,003 shares  -176.37   -8.80 c/share
```

**Holding to settlement is 3.4x worse per share than stopping out** in exactly
034's entry band. The stop is not the disease. The stop is the only thing
currently **limiting** the damage from a model that picks the wrong side: it
truncates the loss at roughly 24% of entry instead of letting it run to -100%.
Remove it and you convert a bounded directional error into an unbounded one.

Caveat: this is a between-strategy comparison, not a within-strategy A/B, so it
is strong evidence rather than proof. The direction is unambiguous and it is the
opposite of 034's premise.

### What 034 should do instead

Do not delete it. It is the only instrumented settlement path in the registry
and it already stamps `model_edge_at_entry` and `entry_ask` on every signal.
Run it as a **calibration probe, not a profit strategy**: let it enter, hold to
settlement, and produce the first within-strategy measurement of realised
frequency against price paid for the fair_value selector. That is a measurement
the system does not currently have (see the Forge brief). It needs its
`max_trades_this_window` throttle relaxed to produce any data at all.

Expected result if my analysis is right: roughly 25% realised against a 33%
requirement, about -9 cents per share. If it comes in near 33% I am wrong and
the settlement thesis survives. **That is the kill condition, with a number and
a named measurement (convention 6): 034 is dead if realised settlement frequency
over its first 60 entries is below 0.30 against mean entry ask 0.33.**

---

## What I did not do

- No code, config, DB, or `DECISIONS.md` changes. Read-only as directed.
- Did not restart or touch the main loop (PID 35848), which is live.
- Did not act on the whitelist corrections. Dropping `dip_arb` and
  `fair_value_arb_wide` from env B is Raven's call, and env B may already be
  launched by `cody-env-b` with the original list.
- Did not re-verify the 032 latent `_open` leak. Confirmed only that 032 still
  has zero acted signals (skips: `not_final_third_of_15m` 846,
  `insufficient_window_history` 267, `t_rem_outside_entry_window` 60), so the
  bug still has not fired.
- Did not run the full test suite. No code changed, and the suite is contended
  by `cody-tests-d323`.

## Numbers most likely to be challenged, and where they came from

- **"execution is 9% of the loss"**: round trip 0.0026/share (0.00157 mean
  `entry_px - best_ask` over 1,430 positions, plus 0.0010 median book spread over
  2,820 tape rows) divided by 0.0297/share observed loss.
- **"fees are zero"**: `select sum(fees) from positions` is 0.00 with zero nulls,
  and `pnl_net == (exit_px-entry_px)*qty` matched on all 1,716 rows.
- **"model slope 0.30"**: OLS of `side_fair_value` on `best_ask` over 17,336
  signals from `features_json`.
- **"settled mirror is exact"**: only for the 345 rows where `exit_px in (0,1)`.
  For the 1,371 intraday rows mirroring is indicative and I have labelled it so.
- **The whole book at t = -7.96**: sum -873.11, per-trade sd 2.64, n 1,716.

---

## Open questions for Raven

1. **Drop `dip_arb` and `fair_value_arb_wide` from env B?** They are the two
   whitelist entries my analysis contradicts.
2. **Does the execution-vs-model correction change the D-323 pause set?** If the
   loss is directional rather than executional, `box_builder` and `grid_hedge`
   were paused for the right outcome but the wrong stated reason, and the maker
   path is not the fix it was framed as. Note box_builder is the worst
   per-share loser in the book at -16.71 c/share.
3. **Relax 034's `max_trades_this_window` so it can produce calibration data?**
   That is a code change and outside this session's read-only scope.
4. **The fade direction (Forge brief, direction 1) is the one real signal in this
   data.** It needs a ruling before Forge builds on it.
