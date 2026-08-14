# Strategy Graveyard Package: Everything Tested, Every Failure Mode

**Date:** 2026-08-13
**Project:** 05-trading-bot (Aym Abdalla)
**Purpose:** hand this to a fresh analyst to dissect and to generate new
strategy hypotheses. It contains what was tested, what happened, WHY, and the
methodological traps that were discovered the hard way.
**Status:** paper/backtest only. No live trading. No version goes live without
Aym's explicit approval.

---

## 0. Read this first: the one number that explains everything

**Round-trip cost on a $100 position = $0.30** (0.10% taker fee each way plus
0.05% slippage each way).

Every strategy below lands between **-$0.25 and -$0.35 PnL per trade.**

That is not a spread of outcomes. It means **gross edge is indistinguishable
from zero across the entire library.** These strategies are not bad at
predicting direction. They are not predicting anything. Net result equals
negative transaction cost, which is exactly what a coin flip pays.

Any new hypothesis must clear the cost hurdle in GROSS terms before it is
worth anything. It does not move with account size (verified: PF and return%
identical at $100 and $100,000, because percentage fees scale with position).

**UPDATE 2026-08-13 - the two numbers that reframe everything:**

**(a) Implied gross edge across the whole library: +$0.0011 per trade** over
1,390,451 pooled trades. Computed by adding the modeled cost back to net PnL.
That is eleven hundredths of a cent. The strategies do not have a small edge
eaten by costs. They have no edge. At ANY positive cost they lose, and at zero
cost they make nothing.

**(b) The cost parameter itself may be stale, and it is the highest-leverage
number in the system.** Two independent reviewers flag that Binance.US moved
to 0% maker / 0.02% taker in April 2026, versus our modeled 0.10% taker.
ccxt's public data still reports 0.10% (possibly its static default); real
rates are account-specific and UNVERIFIED. Counterfactuals from stored data:

| Cost model | Net PnL/trade |
|---|---|
| modeled 0.10% taker + 0.05% slip = $0.30 | -0.2989 |
| claimed 0.02% taker + 0.05% slip = $0.14 | -0.1389 |
| maker 0% + no slippage = $0.00 | +0.0011 |

This rescues NOTHING in the library (see (a)), but it roughly halves the bar
for all future work. **Cost reduction is not a footnote in "promising
directions" - it is the structural variable of the entire search space.**

**(c) Statistical power is not uniform, and FAIL does not mean the same thing
in every row.** A FAIL on grid_1.0atr (259,362 trades) is a verdict. A FAIL on
morning_star (1,699 pooled trades) is a shrug: the confidence interval is
wider than the effect being searched for. The library's verdict is properly
stated as **"no LARGE edge"**, not "no edge". Detecting +$0.09/trade requires
roughly 4,000-8,700 trades; several rows below have far fewer.

---

## 1. Every tested strategy, pooled across 180 tickers and 4 timeframes

**Scope note:** this verdict covers the **v0 library only**. Strategy Lab v2
(9 strategies) and v3 are built and currently running; their results are
pending and are NOT included below.

Source: 218,295 completed backtest rows, 35 strategies x 9 exit configs x 180
tickers x 4 timeframes, on a harness that passes its own validation suite.
Pooled because per-ticker samples are too small for rare patterns (see 3.1).

| Strategy | Pooled trades | Tickers | Win rate | PnL/trade | Silent on % of runs |
|---|---|---|---|---|---|
| `grid_1.0atr` | 259,362 | 180 | 19.7% | -0.297 | 21% |
| `fair_value_gap` | 165,110 | 180 | 13.9% | -0.297 | 23% |
| `stoch_rsi_oversold` | 121,575 | 180 | 17.4% | -0.290 | 23% |
| `grid_2.0atr` | 113,490 | 180 | 24.8% | -0.271 | 24% |
| `S2` | 105,364 | 180 | 19.3% | -0.281 | 24% |
| `dca_7` | 93,890 | 180 | 24.7% | -0.272 | 24% |
| `ema_pullback` | 74,528 | 180 | 13.9% | -0.297 | 27% |
| `breakout_20` | 65,622 | 180 | 23.6% | -0.314 | 26% |
| `momentum_continuation` | 65,589 | 180 | 23.6% | -0.314 | 26% |
| `dca_14` | 54,360 | 180 | 25.4% | -0.285 | 28% |
| `volume_surge` | 41,709 | 180 | 20.2% | -0.335 | 42% |
| `breakout_50` | 41,148 | 180 | 23.5% | -0.353 | 33% |
| `bollinger_reversion` | 38,509 | 180 | 19.6% | -0.293 | 33% |
| `macd_crossover` | 38,461 | 180 | 23.7% | -0.322 | 34% |
| `bullish_marubozu` | 34,356 | 180 | 17.2% | -0.345 | 42% |
| `S6` | 18,503 | 180 | 23.7% | -0.322 | 38% |
| `tweezer_bottom` | 10,622 | 180 | 18.4% | -0.311 | 56% |
| `bullish_engulfing` | 7,140 | 180 | 21.7% | -0.256 | 63% |
| `hammer` | 6,839 | 180 | 18.3% | -0.300 | 66% |
| `S1` | 5,599 | 180 | 20.4% | -0.335 | 75% |
| `bullish_harami` | 5,567 | 180 | 17.5% | -0.333 | 70% |
| `D2` | 4,551 | 180 | 27.5% | -0.278 | 70% |
| `C5` | 4,204 | 180 | 36.4% | -0.709 | 81% |
| `inverted_hammer` | 3,374 | 180 | 14.6% | -0.249 | 77% |
| `three_inside_up` | 2,881 | 180 | 21.9% | -0.319 | 77% |
| `dragonfly_doji` | 1,980 | 180 | 11.4% | -0.443 | 85% |
| `morning_star` | 1,699 | 180 | 21.7% | -0.398 | 84% |
| `piercing_line` | 1,378 | 180 | 17.8% | -0.270 | 89% |
| `D1` | 1,367 | 180 | 15.7% | -0.294 | 89% |
| `upside_tasuki_gap` | 855 | 180 | 11.9% | -0.301 | 90% |
| `mat_hold` | 396 | 180 | 34.1% | -0.016 ** | 95% |
| `bullish_abandoned_baby` | 333 | 180 | 11.4% | -0.452 | 97% |
| `rsi_extreme` | 45 | 180 | 17.8% | -0.287 | 99% |
| `rising_three_methods` | 45 | 180 | 24.4% | -0.495 | 99% |


`**` marks anything better than -0.20/trade. Note that none of them are
positive, and the two closest to zero are explained in section 4.

---

## 2. Strategy families and their theses

**Candlestick patterns** (bullish_engulfing, hammer, morning_star,
piercing_line, three_inside_up, tweezer_bottom, bullish_harami,
bullish_marubozu, dragonfly_doji, inverted_hammer, upside_tasuki_gap,
mat_hold, rising_three_methods, bullish_abandoned_baby)
Thesis: specific price geometries encode order-flow exhaustion or absorption.
Result: all at the cost floor. The rare ones (morning_star, piercing_line)
could not even be judged per-ticker.

**Mechanical/indicator** (grid_1.0atr, grid_2.0atr, ema_pullback,
macd_crossover, bollinger_reversion, stoch_rsi_oversold, rsi_extreme,
volume_surge, breakout_20, breakout_50, momentum_continuation,
fair_value_gap)
Thesis: standard technical triggers. Result: identical cost-floor behavior,
including the highest-frequency strategies with 100k+ trades. Frequency did
not help.

**Benchmarks** (dca_7, dca_14)
Not strategies. DCA has no signal. Flagged `is_benchmark` so a PASS reads as
"the market went up", not as edge. One DCA variant DID pass the gate on
ETH, which is exactly why the label exists.

**Strategy Lab v1** (S1, S2, S6, C2, C5, D1, D2)
Custom hypotheses: volatility-percentile entries, squeeze breakouts, weekend
vacuum reversion, opening-range fades, midday VWAP. Same result. C5 was
notably WORSE than the cost floor (-0.709/trade over 4,204 trades), meaning
genuinely negative gross edge.

**Strategy Lab v2** (9 strategies, added 2026-08-13, results pending)
Unorthodox cross-asset hypotheses: funding-rate shadow, wick absorption
fingerprint, round-number defense decay, liquidation echo, second-break
verdict, volume desert breakout, VWAP magnet, expiry pin drift, 0DTE
amplifier. Currently running.

---

## 3. Failure modes discovered, and what they cost

These matter more than the strategy results. Several of them silently
invalidated earlier conclusions.

### 3.1 Rare patterns cannot be judged per ticker
**13 of 35 strategies NEVER reached 20 trades in 212,058 runs.**
morning_star never exceeded 6 trades in any single run; piercing_line 8. All
were recorded FAIL, which reads as "does not work" when the truth was "this
test could not answer the question." A 251-bar window cannot produce 20
instances of a pattern firing on 1% of bars.
**Fix:** pool across tickers. A pattern's edge belongs to the pattern, not to
AAPL.

### 3.2 Strategies that were structurally unable to fire
- `rsi_extreme`: requires price ABOVE the 50-EMA and RSI BELOW 35
  simultaneously. Nearly contradictory. 45 trades in 6,237 runs.
- `rising_three_methods`: rare 5-candle geometry. 45 trades total.
- Both tasuki gap patterns: comparison operators were swapped, making the
  conditions mathematically unsatisfiable. Verified against 200,000 random
  candle sets: never fired once.
- `fair_value_gap`: loop was `range(-3, -12)` with no negative step, an empty
  range. The strategy could never execute.
**Lesson for new strategies: verify the thing FIRES on real data before
interpreting any result. A strategy that cannot fire looks identical to a
strategy that fails.**

### 3.3 Duplicate strategies inflating apparent breadth
`breakout_20` and `momentum_continuation` have **99.4% identical trade counts**
across 6,237 runs. They are one idea counted twice. Any claim of "N
independent strategies tested" must account for this.

### 3.4 Aggregates hiding concentration (three separate instances)
- "12 strategies PASSED" was really **2 distinct findings**; 11 of the 12 were
  the same strategy family on ONE ticker across 11 exit configs.
- The best cell in the asset-class study (`bullish_harami` on crypto,
  -0.036/trade) was **-0.417 without SOL alone**, worse than the cost floor.
  A single 9-trade cell produced +2.53/trade.
- A small-account options test posted the best profit factor in its sweep
  purely because it could not AFFORD the trades that lost money.
**Fix: leave-one-asset-out is now automatic. Any result that moves more than
0.15/trade when its top underlying is removed is flagged as one asset in a
costume.**

### 3.5 Selection bias: filtering to winners produces exactly nothing
Tested rigorously: select winning (strategy, condition) cells on half the
underlyings, judge those exact cells on the other half, 20 random splits.

| Condition slice | Survival on unseen instruments |
|---|---|
| asset class | 53.7% |
| sector | 58.2% |
| timeframe | 53.5% |
| class x timeframe | 58.7% |
| class x exit | 54.3% |

Coin flip everywhere. And the decisive number: cells that WON on the selection
half averaged **-$0.302/trade on unseen instruments against a -$0.30 floor.**
Selecting winners bought exactly zero.
**Any new hypothesis that arrives already filtered to its winners is not a
hypothesis.**

### 3.6 Harness bugs that produced false results (all fixed, listed as warnings)
Regime filter reading future data; unadjusted stock splits creating fake -90%
crashes; validation controls that never called the harness they certified;
Binance switching kline timestamps to microseconds mid-dataset; caches keyed
on `id()` returning stale arrays after garbage collection; clock-anchored
strategies compared against untimed random twins.
**Every one of these produced confident, wrong numbers before it was found.**

---

## 4. What a noise artifact looks like when you catch it

(Previously framed as "the only non-negative signal". Both reviewers correctly
pushed back: it is explained, and presenting it as intriguing undercuts the
rigor applied everywhere else. Reframed as the teaching case it actually is.)

Constraint sensitivity test: same strategies, three levels of entry gate.

| Gate | Trades | PnL/trade |
|---|---|---|
| AGGRESSIVE (no gate) | 13,711 | -0.244 |
| BASE (RSI<70, vol>1.2x) | 3,956 | -0.290 |
| CONSERVATIVE (RSI<45, vol>2.0x) | 116 | **+0.094** |

One genuinely open question: BASE is WORSE than no gate at all. A proposed
mechanism (from review): volatility filters are non-monotonic for
reversion-tilted entries - moderate vol selects trending continuation (worst
regime for buying dips) while extreme vol selects forced-flow dislocation
(best). That predicts a U-shape across vol deciles, which is testable and
pre-registered as Lab v5 P3.

Why the +0.094 itself is noise, not signal: 39% of that profit came from strategies that
fired 1-2 times (hammer contributed $1.48 on a SINGLE trade). Every sample
with real weight is flat or negative. Only 3 tickers, all mega-cap tech.

**Statistical power calculation:** to distinguish +$0.09/trade from zero
requires roughly **4,000-8,700 trades.** We have 116. This also means the
SPEC's 150-trade acceptance bar is adequate only for detecting LARGE edges
(+$0.30/trade needs ~400-800 trades) and wildly inadequate for small ones.

---

## 5. What is RULED OUT (do not propose these again without new evidence)

1. Generic candlestick patterns on liquid instruments, 5m to 1d, with
   mechanical exits. Zero gross edge, 218k tests.
2. Bearish patterns as exit signals: tested, -0.299/trade, no better than
   mechanical exits.
3. Strategy inversion ("the failed signal predicts the opposite"): tested on
   48 gated candidates, ZERO beat buy-and-hold. Edge was -$0.31 per exit
   against a $0.30 cost. The "anti-signal" is trading friction.
4. Routing existing strategies by asset class. Cross-class spread is tiny
   (0.06-0.36); strategies are uniformly at zero, not class-specific.
5. Filtering the library to winning subsets (section 3.5).
6. Larger position size to outrun fees. Percentage fees scale with size;
   verified identical PF from $100 to $100,000. (Only true for percentage-fee
   venues. FIXED per-contract costs, like options commissions, behave the
   opposite way and size matters enormously there.)

---

## 6. What is UNTESTED and most promising

1. **Conditional edge with a mechanism stated FIRST.** Not "scan for where it
   wins" but "this strategy needs targets bigger than the spread, so it should
   only work where ATR% exceeds X." The condition must be predicted, then
   tested, then verified out of sample.
2. **Instrument fingerprinting beyond asset class** (variance ratio, gap
   propensity, spread, volume curve). Asset class was too coarse to matter;
   finer instrument character has not been tried.
3. **Time-based holdout validation.** Everything so far splits by instrument.
   A regime-conditioned hypothesis needs a split in TIME.
4. **The conservative-gate direction** (deeply oversold + volume surge in an
   uptrend), pending the full sweep and select/verify validation.
5. **Options structure exploited via the underlying** (expiry pin, 0DTE
   amplifier) - built, not yet swept.
6. **Cost reduction as an edge source.** Every strategy fails by roughly the
   cost. Maker-only entries, wider targets, or a cheaper venue change the
   entire equation more than any signal improvement tested so far.

---

## 7. Rules any new strategy proposal must follow

Learned from the failures above. A proposal that skips these is not testable.

1. **State a falsifiable thesis** - the behavioral or structural reason the
   edge exists. "RSI is oversold" is not a thesis. "Forced sellers must
   liquidate regardless of price, and that pressure ends when the margin call
   clears" is.
2. **State a kill condition** - what result would prove it wrong.
3. **Estimate gross edge per trade in basis points.** If it is under 30bps
   round trip it cannot survive costs, and the strategy is dead before it is
   written.
4. **Estimate frequency.** Under ~4,000 expected trades, a small edge cannot
   be distinguished from zero, so either the edge must be large or the
   strategy must be poolable across many instruments.
5. **Verify it FIRES on real data before interpreting results** (section 3.2).
6. **No filtering to winners.** Conditions must be predicted, not discovered.
7. **Every entry needs a stop strictly below entry.** The harness and the risk
   gate both reject inverted stops.

---

## 8. Environment notes for whoever picks this up

- Data: 936 clean OHLCV files, split/dividend adjusted, 5m/15m/1h/1d/1wk.
  180 tickers spanning equities (20+ sectors), ETFs, 8 futures contracts,
  3 crypto pairs. Plus session calendars, split history, perp funding rates
  (1 year), premarket bars.
- Harness passes a 21-check validation suite including an oracle control that
  runs THROUGH the harness, a delayed-oracle lookahead detector, fee
  application checks, and cross-engine agreement with an external backtester.
- Strategy interface: subclass `Strategy`, implement
  `scan(candles) -> Optional[Signal]`. Read only index -1 and earlier.
  Any use of future data is a critical bug.
- Analysis tooling available: pooled analysis, asset-class breakdown with
  leave-one-out, conditional-edge select/verify validation, constraint
  sensitivity sweep, silent assertions, strategy inversion.

The apparatus is trustworthy. The strategies are not. That asymmetry is the
project's actual asset right now.
