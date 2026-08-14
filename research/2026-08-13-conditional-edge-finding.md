# Conditional Edge: Filtering to Winners Produces Exactly Nothing

**Date:** 2026-08-13
**Question (Aym):** test strategies across sectors, classes and conditions,
pool them generically, then keep the subsets where the pattern wins, so we
know it works under those specific tickers/classes/conditions.
**Answer:** the question is right, and the test says the winners are noise.
**Tool:** `backtest/conditional_edge.py`

## Why the obvious version of this would have lied

Looking at results, removing losers, and keeping winners ALWAYS produces a
beautiful backtest. With 44 strategies x 180 tickers x 11 exit configs, some
subset is profitable by chance guaranteed. Reporting that filtered subset as
edge is the most common way a backtest lies, and it is indistinguishable from
real conditional edge unless you test it properly.

## The method that makes it valid

Select on one set of instruments, verify on a different set never looked at.

1. Split underlyings randomly into SELECT and VERIFY halves (seeded).
2. On SELECT only, find (strategy, condition) cells beating the cost floor
   with >=100 trades.
3. Judge those EXACT cells on VERIFY (>=50 trades).
4. Repeat over 20 random splits.

Under pure noise, a selected cell should land above the floor on verify about
half the time. Meaningfully above 50% is evidence of real conditional edge.
At 50% the selection carried no information.

## Result

| Condition slice | Selected | Judged | Survived | Survival | Mean verify $/trade |
|---|---|---|---|---|---|
| class | 953 | 909 | 488 | **53.7%** | -0.311 |
| sector | 4,561 | 4,185 | 2,434 | **58.2%** | -0.277 |
| timeframe | 864 | 863 | 462 | **53.5%** | -0.323 |
| class x timeframe | 2,584 | 2,471 | 1,450 | **58.7%** | -0.303 |
| class x exit | 5,915 | 5,640 | 3,065 | **54.3%** | -0.297 |

**Survival is a coin flip across every slice.**

The decisive number is the last column. Take the cells that WON on half the
instruments, apply them to instruments they were not selected on, and the
average result is **-$0.302 per trade against a cost floor of -$0.30.**

Selecting winners bought exactly zero. Not a reduced edge, not a decayed
edge - the cost of trading and nothing else. That is what conditioning on
noise looks like when it is measured honestly.

## The one slice that is marginally above chance, and why it is not evidence

`sector` shows 58.2% survival and -0.277 mean, slightly better than the floor.
Two reasons not to believe it:

1. **It has the most slices (4,561 selected cells), so it gets the most
   chances to find noise.** More comparisons, more apparent winners.
2. **Splitting by underlying does NOT break sector correlation.** If you
   select on AAPL and MSFT and verify on NVDA and AMD, you have not tested on
   independent data - those names move together. For a SECTOR-conditioned
   hypothesis, a ticker-level split is not a clean holdout. The apparent
   survival is partly the sector correlating with itself.

A proper test of sector conditioning needs a holdout in TIME, not just across
tickers. That is the next experiment, and it is worth running only if
something first shows gross edge above zero.

## What this settles

- **Filtering the current library to its winners does not produce a strategy.**
  It produces the cost of trading, applied to a smaller sample.
- This is consistent with, and independently confirms, the v0 verdict: gross
  edge across the library is indistinguishable from zero. If gross edge is
  zero everywhere, no filter over subsets can create it - filtering only
  changes WHICH zero you are looking at.
- The tooling now exists. The moment any strategy shows gross edge above
  costs, this same script tests whether the edge is real or a subset artifact,
  in one command.

## What would change the answer

Conditional edge is a real phenomenon; this result does not say it never
exists. It says it does not exist in THIS library. Finding it would require:

1. A strategy with positive GROSS edge somewhere (before costs). Nothing in
   the current library has that.
2. A condition with a mechanistic reason stated BEFORE looking at results
   (for example "this strategy needs targets larger than the spread, so it
   should only work on instruments with ATR% above X"). Conditions found by
   scanning results are hypotheses, not findings.
3. Survival on a time-based holdout as well as an instrument-based one.
