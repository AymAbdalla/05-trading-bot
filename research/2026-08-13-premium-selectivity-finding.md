# Is "Skip Expensive Contracts" Risk Management? No. It Is a Risk Amplifier.

**Date:** 2026-08-13
**Hypothesis (Aym):** the $500 account declined trades it could not afford,
which acted like risk management (protecting the balance, keeping the
strategy alive). The LOGIC might be extractable as a deliberate rule.
**Verdict:** the instinct is good, the specific mechanism runs backwards.
**Evidence:** 4,633 option trades, 25 tickers, 3 strategies, daily bars.

## Why the obvious test would have lied

Comparing a filtered run against an unfiltered run is invalid here: filtering
changes which trades happen AND shifts every subsequent entry, so the two runs
trade divergent populations. That is exactly what produced the misleading
"small account has PF 1.93 vs 1.45" result.

The valid test ignores filtered-vs-unfiltered entirely. Take every trade the
UNFILTERED strategy takes, bucket by entry premium (as % of spot, so tickers
are comparable), and measure expectancy per bucket. Same population, sliced.

## Result

| Bucket | Premium (% of spot) | n | Mean return | **Median** | **Win rate** | **Stdev** |
|---|---|---|---|---|---|---|
| 1 cheapest | 0.12-0.78% | 926 | **+23.39%** | **-16.20%** | 46.0% | **163.4%** |
| 2 | 0.78-1.29% | 926 | +13.81% | -7.09% | 48.4% | 107.1% |
| 3 | 1.29-1.75% | 926 | +6.09% | -20.34% | 44.9% | 106.4% |
| 4 | 1.75-2.71% | 926 | +7.02% | -11.62% | 47.1% | 84.9% |
| 5 priciest | 2.71-9.44% | 929 | +10.31% | **-0.73%** | **49.7%** | **89.3%** |

Not monotone (buckets 3-4-5 rise: 6.09 -> 7.02 -> 10.31), so there is no
clean "cheaper is better" gradient. But the distribution SHAPE differs
enormously, and that is the real finding.

## What the numbers actually say

**Cheap options are lottery tickets.** Highest mean (+23%) but the WORST
median (-16%), the WORST win rate (46%), and nearly DOUBLE the volatility
(163% vs 89%). The high mean is carried by rare large winners. The typical
trade in that bucket loses 16% of its premium.

**Expensive options behave more like the stock.** Median near zero (-0.7%),
best win rate (49.7%), lowest variance. Less exciting, far less punishing.

Risk-adjusted (mean/stdev) the buckets are roughly flat: 0.143, 0.129, 0.057,
0.083, 0.115. There is no free lunch in either direction. What changes is the
shape of what you sign up for.

## Why this inverts the hypothesis

A small account cannot afford expensive contracts, so **affordability forces
it into bucket 1: the highest-variance, worst-median, worst-win-rate part of
the distribution.**

That is the opposite of protecting the balance. If the goal is "do not go to
zero and stay alive to keep trading," the median trade and the variance are
what matter, and the cheap bucket is worst on both. The constraint is a risk
AMPLIFIER wearing the costume of discipline.

The earlier observation that the $500 account skipped 3 losers out of 4 was
n=4. At n=4,633 the effect reverses. That gap between 4 samples and 4,633 is
the entire lesson.

## The extractable logic, corrected

Aym's underlying instinct - that a constraint on WHICH trades you take can
function as risk management - is sound and worth keeping. The corrected form:

1. **For survival, prefer HIGHER premium (closer to the money), not lower.**
   Better median, better win rate, ~45% less variance.
2. **Cheap far-OTM buying is a convexity bet, not a strategy.** It needs a
   thesis about rare large moves and a bankroll that can absorb a long string
   of -16% medians. It is not risk management; it is the opposite trade.
3. **This compounds with the fee finding.** Cheap options ALSO carry the
   worst commission drag (39% of premium at 20% OTM vs 1.6% at 5% OTM).
   Cheap contracts are worse on fees AND worse on median AND higher variance.
   Three strikes.
4. **The SPEC's v2+ WSB / tail-risk ideas live in bucket 1.** They are
   explicitly convexity bets, which is legitimate, but they must be
   evaluated on tail behavior and bankroll survival, never on mean return.

## Caveats

- No IV smile in this model. In real markets far-OTM options cost MORE than
  modelled, so bucket 1's real returns are worse than shown. The finding gets
  stronger with real chain data, not weaker.
- Long calls only, 30 DTE, 5% OTM baseline, daily bars, 25 large-cap names.
- Not monotone, so treat this as a statement about distribution shape
  (variance and median), not as a rankable gradient.
- Mean returns look high across all buckets because of the missing smile.
  Compare buckets to each other, never take a level as tradeable.
