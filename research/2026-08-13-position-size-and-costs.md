# Does Bigger Position Size Beat the Fees?

**Date:** 2026-08-13
**Question (Aym):** "Isn't the round trip cost fixed? If so, a larger position
could earn more than the fees."
**Short answer:** Not on Binance.US. Fees there are a PERCENTAGE, so they
scale with position size exactly as profits do. But the intuition is correct
and important for any venue with FIXED per-order costs.

## The measurement

Same strategy (grid_1.0atr), same data (ADBE 1h holdout), same 16 trades,
only the notional changed:

| Notional | Trades | PF | Win rate | Net PnL | Fees paid | Return % |
|---|---|---|---|---|---|---|
| $100 | 16 | 1.6113 | 56.2% | $5.91 | $3.21 | 0.370% |
| $1,000 | 16 | 1.6113 | 56.2% | $59.14 | $32.09 | 0.370% |
| $10,000 | 16 | 1.6113 | 56.2% | $591.43 | $320.91 | 0.370% |
| $100,000 | 16 | 1.6113 | 56.2% | $5,914.30 | $3,209.12 | 0.370% |

Profit factor, win rate and percentage return are IDENTICAL to four decimal
places. Dollars scale, quality does not. Multiplying position size multiplies
both sides of the ledger and changes nothing about whether the edge exists.

This is why the harness has a `test_scale_invariance` known-answer test: if
these numbers had differed, the harness itself would be broken.

## Where the intuition IS right

The same strategy under a HYPOTHETICAL fixed $1-per-order commission
(cost that does NOT scale):

| Notional | Gross PnL | Fixed fees | Net PnL | Verdict |
|---|---|---|---|---|
| $100 | $9.12 | $32.00 | -$22.88 | LOSS |
| $1,000 | $91.23 | $32.00 | $59.23 | PROFIT |
| $10,000 | $912.34 | $32.00 | $880.34 | PROFIT |
| $100,000 | $9,123.42 | $32.00 | $9,091.42 | PROFIT |

Here size is everything: the same trades flip from a 250% loss to a profit
purely by amortizing a fixed cost. Venues where this applies: brokers with
per-trade commissions or minimums (some equity brokers), on-chain DEX trading
(gas is per-transaction, not per-dollar), and anything with a flat ticket
charge. If this project ever routes through such a venue, position sizing
becomes a first-order strategy decision, not just a risk decision.

## The direction nobody hopes for

At genuinely large size, real costs get WORSE, not better. A $100 order fills
at the top of the book. A $1,000,000 order eats through it, and the average
fill price slips. That is market impact, and it grows with size. The SPEC
calls this the scale ceiling (F7) and defers it by holding notional fixed at
$100. So the realistic curve is: flat while small (percentage fees), then
degrading once orders are large enough to move the book.

## Consequences for this project

1. **The inversion finding stands.** -$0.31 per exit on $100 becomes -$3.10
   per exit on $1,000. The fade still loses; it just loses ten times as much.
2. **The fee-to-edge gate (SPEC 6.4) is scale-invariant too.** Both terms are
   percentages, so the gate's verdict never depends on notional. Good: it
   means the gate tests the strategy, not the account size.
3. **Fixed notional (SPEC 6.1) costs nothing in edge terms.** It caps dollar
   outcomes, not percentage quality. The trade-off is real but it is about
   compounding, not about beating fees.
4. **Minimum order size is the one place small hurts.** Below the exchange
   minimum the trade is skipped entirely (SPEC 6.1). That is a floor, not a
   scaling effect.

## The honest summary

You cannot outgrow a percentage fee. You can only outgrow a fixed fee. On
Binance.US every cost in the model is a percentage, so the only way through
is a strategy whose gross edge exceeds ~0.30% per round trip. That is the bar,
and it does not move no matter how much capital is behind it.

---

# Addendum: Account-Balance Sweep (Aym's follow-up, $100 cap disregarded)

Ran the same strategy and signals across balances from $500 to $100,000 for
both cost structures. Tool: `backtest/balance_sweep.py`.

## Spot (percentage fees): still exactly invariant

| Notional | Trades | PF | Win % | Return % |
|---|---|---|---|---|
| $500 | 2 | 0.0000 | 0.0% | -3.201% |
| $100,000 | 2 | 0.0000 | 0.0% | -3.201% |

Every intermediate balance identical. Dollars scaled 200x, quality metrics
did not move at all. Nothing more to test here: on percentage fees, account
size is irrelevant to whether a strategy works.

## Options (fixed per-contract fees): size matters, but NOT how you'd hope

| Budget | Trades | Avg contracts | Unspent capital | Commission as % of premium | PF |
|---|---|---|---|---|---|
| $500 | 25 | 1.6 | 26.4% | 0.58% | 1.93 |
| $2,500 | 29 | 9.5 | 4.2% | 0.51% | 1.48 |
| $25,000 | 29 | 97.9 | 0.4% | 0.51% | 1.45 |
| $100,000 | 29 | 392.4 | 0.1% | 0.51% | 1.45 |

Three real effects, and one trap:

1. **Commission ratio barely moves** (0.58% -> 0.51%). This is arithmetic:
   commission/premium = (2 x fee x contracts) / (premium x 100 x contracts).
   The contract count CANCELS. The ratio is set by the PREMIUM PER CONTRACT
   (i.e. strike and expiry choice), not by account size.
2. **Order minimums bite only at the bottom.** With a $1 order minimum the
   $500 account pays 0.72% instead of 0.58%; by $2,500 the minimum never
   binds. Small accounts pay a surcharge, and it disappears quickly.
3. **Idle capital is the real small-account tax.** Contracts are indivisible,
   so at $500 **26.4% of the budget sits unspent** on an average trade. At
   $25,000 it is 0.4%. That is capital earning nothing, and no fee schedule
   shows it to you.

## THE TRAP: the $500 account posted the best profit factor (1.93), and it is meaningless

It did not trade better. It traded a **different population**. Four signals
priced contracts it could not afford, so it declined them - and three of
those four were losers. Removing losers you could not afford is luck, not
edge.

Worse, the populations are not even nested. Declining one trade frees the
scanner to enter a LATER trade the funded account was still holding through,
so the two balances follow genuinely divergent trade sequences. Both facts
are now pinned by a regression test.

**Rule: never compare profit factor across account sizes without first
confirming the trade counts match.** If they differ, you are comparing two
different strategies that happen to share a name.

## Bottom line for the project

- Spot: pick position size for risk reasons only. It has zero effect on edge.
- Options: the lever that matters is PREMIUM PER CONTRACT (strike/expiry),
  not account size. Bigger accounts get two modest gifts: order minimums stop
  binding, and less capital sits idle from rounding.
- The SPEC's $100 cap is unusable for options at all (cannot afford one
  contract on most liquid names). Options need a separate sizing rule, and
  the honest floor is roughly "enough to buy 5-10 contracts of your typical
  premium" so rounding waste stays under ~5%.
- REMINDER: the option PnL numbers above are inflated by the missing IV
  smile. Use this table for COST STRUCTURE conclusions only, never as
  evidence that the strategy makes money.
