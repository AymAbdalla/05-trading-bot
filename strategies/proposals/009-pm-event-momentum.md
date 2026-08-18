---
name: "pm_event_momentum"
thesis: "Information reaches an event market in lumps (a poll release, a debate, a CPI print) and the implied probability finishes repricing over days rather than seconds, because the marginal holder checks in once a day and the traders who would close the gap face months of locked capital to collect a one or two cent drift."
expected_edge_bps: 400
kill_condition: "Any one of: measured median residual drift below 2.0c per share in the 24 hours to 7 days AFTER a 10 point repricing, over 200 or more repricing events in the archive, which is the pre-registered claim and kills the idea before any code is written; OR net edge below 200bps over 200 or more RESOLVED entries scored by backtest/polymarket_harness.py; OR a t statistic below 2.0 on 2500 or more resolved entries, which is the sample this effect size actually needs and is reported on every run as trades_needed_for_2sigma. The drift-decay measurement has NO script today and is build step 2. The trading result is scored by backtest/polymarket_harness.py, which exists."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "UNIVERSE: non-crypto event markets discovered by Gamma tag (politics, Fed, macro), resolution date 14 to 90 days out. SIGNAL, all measured on CLOB daily closes from prices.price_history keyed on conditionId: the Yes implied probability has risen by at least 10 points over the trailing 5 days AND rose on at least 4 of those 5 days AND the current day move is smaller than the largest single day move in the window, so the entry is on the tail of a repricing rather than into the middle of one. ENTER the rising side when the ask is at or below 0.70, because above that the remaining upside cannot pay for the premium. NO ENTRY above 0.70 and NO ENTRY below 0.30, where the move is arithmetically bounded on one side. SIZE: whole shares affordable at the notional cap. STOP: none. A losing share settles at exactly 0.00, the true floor, which satisfies convention 8. TARGET: resolution at 1.00. TIME EXIT: none, the position is held to settlement. The natural version of this strategy sells the share once the drift is exhausted, and that version CANNOT be scored today because backtest/polymarket_harness.py deliberately computes no path metrics and no interim mark. Holding to resolution is the version that is scorable, and it is a different trade, stated here rather than hidden."
data_requirements: "HAVE: Gamma tag discovery (engine/polymarket/markets.py). CLOB daily price history keyed on conditionId, intervals up to 1y with a fidelity argument (engine/polymarket/prices.py price_history_checked, which reports read failure separately from an empty history). Orderbook depth. Resolution outcome by slug. Resolution PnL scoring (backtest/polymarket_harness.py). BLOCKER 1: NO EVENT MARKET PRICE HISTORY IS STORED. The fetch path exists, zero rows have been fetched, and a backtest that pulls history live per candidate at run time is a live-fetch experiment with no reproducibility, not a dataset. BLOCKER 2: NO NEWS OR CATALYST FEED EXISTS. The thesis says the move follows information arrival, and we cannot see information arrival. The strategy as written substitutes the price move itself for the catalyst, which means it cannot distinguish an informed repricing from a large order walking a thin book. That is precisely the condition proposal 010 trades in the opposite direction, and it is the honest reason the two are one hypothesis until the separator is measured. BLOCKER 3: NO DRIFT DECAY MEASUREMENT EXISTS. The whole edge is a single unmeasured fraction f, the share of a repricing already captured in the first 24 hours. No script measures it. This is build step 2 and it is the cheapest kill in the proposal. BLOCKER 4: cost_model.py PM_HALF_SPREAD_PER_SHARE = 0.005 is calibrated on BTC Up/Down 5m books and has never been measured on an event-market book."
related_graveyard_findings: "There are NO PREDICTION_MARKET rows in the graveyard, so this inherits no verdict. The nearest neighbour by mechanism is momentum_continuation, and its burial deserves to be engaged rather than dodged: 86,684 pooled trades across 173 tickers, minus 3.78c per trade, 36.98% pooled win rate, and only 39.8% of runs profitable. That is a verdict, not a shrug (convention 7). volume_surge is worse at minus 10.57c per trade on 55,684 trades. The defence is NOT that this instrument is exempt. It is that those two buy a continuing PRICE and are scored on a path with a stop, where a 37% win rate against a fixed R is arithmetically fatal, whereas this buys a continuing PROBABILITY and is scored on resolution, where a 52% win rate at a 50c entry is profitable and the same 52% at a 60c entry is not. If a reader wants to treat momentum_continuation as evidence against this, the specific thing they should point at is the 39.8% profitable-run fraction, which is a statement about the SIGNAL rather than about the exit, and that part does transfer. V3_intraday_momentum_crypto is not usable as a prior in either direction: 22 pooled trades on one ticker, judgeable false."
kind: edge_hypothesis
status: PROPOSED
source: "forge, event-market gap (asset class has zero graveyard coverage, D-268)"
forge_warnings: "none"
---


## Edge arithmetic, and the fact that I cannot actually derive it

The edge is a function of one number I do not have. Let m be the size of a
justified repricing in probability points and let f be the fraction of m that
the market captures inside the first 24 hours. The residual drift available to
us is m * (1 - f), and:

    gross edge in bps = 100 * m * (1 - f) / entry_price_in_cents

f is unmeasured. We hold zero event-market history, so any value I write for f
would be invented. Convention 11 says unknown is not zero, and it equally says
unknown is not 0.8.

So I am doing this the other way round. Instead of asserting f and deriving an
edge, I derive the value of f at which the trade stops being worth doing, and I
pre-register that as the thing to measure first.

    entry price                                       50c
    cost, modelled walked fill at 1.0c per share      1.0c
    lockup, 30 day hold at a 4% risk free rate        0.16c   (0.329% of 50c)
    minimum residual drift to net 200bps (1.0c)       1.0c
    ------------------------------------------------------
    required residual drift, gross                    2.16c, call it 2.0c

    gross edge at 2.0c on a 50c share = 400 bps

For a 10 point repricing, 2.0c of residual drift means f <= 0.80. That is the
pre-registered claim in one sentence: **the market captures no more than 80% of
a 10 point repricing within 24 hours.** It is falsifiable off price history
alone, without trading anything, and if f comes back at 0.95 this proposal dies
having cost nothing.

400 is therefore an estimate in the strict convention 15 sense, and it is worse
than that: it is not a forecast of the edge, it is the smallest edge that would
be worth the trouble. If the measurement says the true value is 250bps, the
proposal is dead even though 250 clears the 200bps floor, because 250 gross does
not survive the cost and lockup lines above.

### Entry price and win rate together, per D-267

    entry 50c on the rising side
    breakeven win rate    = 50.0%
    hypothesised win rate = 52.0%
    margin = 2.0 percentage points

Two points. State that alone and it sounds like nothing. State the 400bps alone
and it sounds like a real trade. They are the same claim, which is exactly why
D-267 refuses to let either be quoted by itself.

### The sample problem, which is the strongest argument against this proposal

A 2 point calibration edge at a 50c entry sits at close to the maximum possible
variance for a binary.

    win return = +100%, lose return = -100%
    mean = +4.00% of capital
    sd   = 2 * sqrt(0.52 * 0.48) = 0.999
    n for t = 2 = (2 * 0.999 / 0.04)^2 = 2,496 trades

About 2,500 resolved event markets. Compare that against the harness gates in
backtest/polymarket_harness.py: MIN_RESOLVED_TRADES is 200 and MIN_T_STAT is
2.0. At 200 trades this effect produces

    t = 0.04 / 0.999 * sqrt(200) = 0.57

so it cannot clear the t gate at the harness minimum no matter how real it is.
The only verdicts available at n = 200 are FAIL with underpowered true, or
NOT_TESTED. Neither is evidence about the strategy.

Polymarket may list a few hundred non-crypto event markets a year with enough
depth to trade. 2,500 resolved entries is measured in years, not months. This
is not a reason to lower the threshold. It is a reason to prefer the drift
decay measurement, which reads a CONTINUOUS quantity off price history and
needs a few hundred repricing events rather than a few thousand resolutions, as
the primary kill test.

## Why the edge would persist

Under-reaction survives where the correction is expensive to enforce. On a spot
instrument anybody can close a 2c gap in a second for a fee measured in basis
points. Here, closing a 2c gap means locking capital until an election happens.
The participants who would do it are also the participants who would rather buy
a T-bill, and the ones who remain are holding a view rather than a book.

That is a real mechanism. It is also weak enough that it should not be believed
without the measurement.

## What would make this wrong

**This and proposal 010 may be one hypothesis with two arms.** 010 fades a fast
probability move; this one follows a slower one. The only thing separating them
is speed plus a print-density proxy for whether the move was informed. If that
separator does not survive measurement, then running both and reporting
whichever wins is p-hacking with a coat on. The graveyard already ran 486,783
tests with an expected best result around 5.1 sigma under the null. Adding a
matched pair of opposite-sign strategies to that budget and keeping the winner
is the exact abuse that number exists to warn about. **If they are built, they
count as ONE hypothesis with two arms in the multiple comparisons budget, and
neither is allowed to be reported without the other's result.**

**The scorable version is not the version I believe in.** The natural momentum
trade sells the share once the drift stops. Held to resolution, a 2c drift edge
is diluted by three months of event risk that has nothing to do with the
signal. So the number the harness will produce is a measurement of a DIFFERENT
strategy than the one described in the thesis, and a FAIL would not cleanly
falsify the under-reaction claim. Fixing that needs a mark-to-market exit
extension to backtest/polymarket_harness.py, which does not exist and which the
harness header argues against on purpose. Say the limitation out loud or the
result gets over-read.

**Survivorship inside the signal.** Requiring 4 up days out of 5 selects
markets that trended, and markets that trended are disproportionately markets
where the outcome was already becoming obvious. Backing the eventual winner
after it became likely is not an edge, it is buying at a fair price late. The
control is whether the residual drift after the signal exceeds what the price
level alone predicts, not whether the signal-following trades won.

**Probability series break the indicators people reach for.** engine/polymarket/
prices.py says it plainly: a Polymarket series lives on [0, 1], is bounded, and
terminates in a jump to exactly 1 or 0. Any percent-of-price momentum measure
on it produces numbers with no meaning near the boundaries. That is why the
rule above is written in probability POINTS and why the 0.30 to 0.70 band is a
structural constraint rather than a tuned filter. A future version that widens
that band to get more trades is fitting the gate, not the market.

**The cost constant.** 0.5c half spread came from BTC Up/Down 5m books. On a
50c share, moving that to a realistic event-market 1.5c takes 300bps off a
400bps gross estimate and leaves 67bps net after lockup. One unmeasured
constant is the difference between a trade and a fee.
