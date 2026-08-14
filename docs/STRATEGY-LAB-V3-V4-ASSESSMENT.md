# Assessment: Strategy Lab v3 and v4

**Date:** 2026-08-13
**Reviewer:** Claude Code
**Verdict:** v3 is the strongest strategy input this project has received. v4 is
intellectually sound but blocked on validation we cannot perform.

## v3: what makes it different from v0/v2

Every prior strategy set was **pattern geometry** - "this candle shape means
something." 218,295 backtests said it does not. v3 is anchored in **documented
effects with measured sizes and named mechanisms**, which is a different kind
of claim entirely.

More importantly, v3's doctrine section states the exact thing this project
discovered the hard way:

> "most published gross edges are smaller than retail round-trip costs. The
> fee-to-edge gate is not a compliance step - it is the strategy."

That is our v0 verdict, arrived at independently. Every one of our 34
strategies landed at -$0.25 to -$0.35 per trade against a $0.30 cost floor,
because their gross edge was zero. v3 is the first input written with that
constraint in the foreground.

The McLean & Pontiff framing is also correct and useful: published predictors
decayed ~26% out of sample and ~58% post-publication, and decayed LEAST where
limits to arbitrage bite. A capacity-constrained retail bot is the protected
habitat for exactly the edges institutions cannot scale into. That is a
coherent theory of why anything should be left, which no previous doc offered.

## v3: the falsifiable bar

Every v3 strategy is a bet that a documented effect survives costs. So the
test is not "does it profit" but:

**Does gross edge (pre-cost) exceed ~30bps per round trip?**

If gross edge is 15bps, the strategy is real, published, reproducible, and
still loses money at retail. That distinction is the whole game, and our
harness measures gross_pf separately from net precisely so we can see it.

## v3: what is testable now vs blocked

| Strategy | Status | Blocker |
|---|---|---|
| 2. The 3:30 Verdict (intraday momentum) | **TESTABLE NOW** | none - have intraday equity data and the macro calendar |
| 4. Macro Calendar Harvest (pre-FOMC drift) | **TESTABLE NOW** | have FOMC + NFP dates; CPI needs the free FRED key; VIX conditioning not available inside scan() |
| 1. Vacuum Refill (liquidation echo) | **TESTABLE, ADAPTED** | doc wants 1m crypto bars, we have 15m minimum; cross-pair idiosyncrasy test needs multi-ticker data |
| 3. Same-Clock Echo (Heston et al.) | **BLOCKED** | needs a CROSS-SECTIONAL harness |
| 5. Paid Liquidity Reversal (Nagel) | **BLOCKED** | needs cross-sectional harness + earnings calendar + VIX inside the strategy |

### The real blocker is architectural, not data

Strategies 3 and 5 rank tickers AGAINST EACH OTHER (top-decile cells, bottom-
quintile residual losers). Our harness scans one series at a time and has no
concept of a universe at a point in time. That is a genuine new capability:
a cross-sectional harness that steps through time holding all tickers, ranks
them per bar, and trades the top/bottom slice.

This is worth building. Cross-sectional is a fundamentally different edge
geometry from everything tested so far (all time-series), and it neutralizes
market drift by construction - which is exactly the confound that made our
buy-and-hold comparisons awkward. It is also the geometry most of the
surviving academic literature lives in.

Estimated scope: a new harness class, not a modification of the existing one.

## v4 (DEEP RENT / LEAPS): sound reasoning, unvalidatable core

The doctrine is correct on every technical point I can check:
- Deep ITM (0.70-0.80 delta) minimizes the extrinsic fraction, which is the
  only part that pays theta and vol crush. Correct.
- Frazzini-Pedersen embedded leverage: leverage-constrained buyers overpay for
  OTM magnification, so the cheap end of the spectrum is the rebate side.
  Correct, and it inverts the retail default.
- The "structural mercy" argument - being wrong in an 18-month option costs
  delta P&L while being wrong in a 2-month option approaches total loss - is
  correct and is the strongest argument in the document.
- Goyal-Saretto IV-rank gate: buying only when IV is not rich. Correct.

**Why we cannot validate it:**

1. **No implied volatility history.** The IV-rank gate (IV rank <= 40) is the
   central risk control, and we have no IV data. Proxying with realized-vol
   rank tests a different, weaker strategy and should not be reported as
   testing v4.
2. **Our options model has no smile.** The overlay prices with Black-Scholes
   on realized vol, so far-OTM is too cheap and the term structure is flat.
   v4's whole thesis is about WHERE ON THE SURFACE you buy. A model with no
   surface cannot test a surface strategy. The doc concedes this in Stage 1.
3. **Capital.** One deep ITM 18-month contract runs $800-2,500 on liquid mid-
   priced names, $8,000+ on SPY. The doc is honest that this needs $15-25k+
   before it can run live under sane concentration.

**What Stage 1 CAN honestly deliver:** the shares-versus-LEAPS structural
comparison. Take the ignition signals (PEAD, 52-week-high breakout, trend
reclaim), trade them in SHARES, and see whether the signals have edge at all.
That requires no options data and answers the prior question. If the ignitions
have no edge in shares, no options wrapper rescues them.

**Recommendation: implement the three v4 IGNITIONS as share strategies first.**
They are testable today, they are the falsifiable core, and the LEAPS wrapper
is a sizing/leverage decision layered on top of a signal that must work
regardless.

Silent assertion #16 from the doc (every options trade must beat both a
shares-twin and a random-LEAPS-twin) is a good idea and should be adopted
whenever Stage 1 runs.

## What I am building now

1. v3 Strategy 2 (intraday momentum) + a crypto UTC-day variant
2. v3 Strategy 4 (macro drift) for FOMC and NFP
3. v3 Strategy 1 (vacuum refill), adapted to 15m

## Queued, in priority order

1. **Cross-sectional harness** - unblocks v3 #3 and #5, and opens an entire
   edge geometry we have never tested.
2. **v4 ignitions as share strategies** (PEAD, 52w-high, trend reclaim).
3. Earnings calendar (needed by v3 #5 and v4's earnings guard).
4. FRED key for CPI dates (Aym, 30 seconds).
5. 1m crypto bars for a faithful Vacuum Refill.
