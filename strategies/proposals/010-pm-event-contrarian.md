---
name: "pm_event_contrarian"
thesis: "Event-market books are thin, so a single large order can walk implied probability 10 or 15 points in hours without any new information about an event that will not resolve for months, and when the book refills the price gives part of that move back."
expected_edge_bps: 900
kill_condition: "Any one of: measured median retracement below 1.0c per share in the 7 days after a fast move, across 200 or more qualifying moves in the archive, which kills the idea before any code is written since at zero retracement the trade is a pure spread donation; OR net edge below 200bps over 200 or more RESOLVED entries scored by backtest/polymarket_harness.py; OR a t statistic below 2.0 on 384 or more resolved entries, which is the sample this effect size needs and which backtest/polymarket_harness.py reports on every run as trades_needed_for_2sigma; OR a measured error rate on the liquidity-versus-information proxy that leaves it unable to separate the two, on the standard set by backtest/measure_strike_proxy.py, which measured its own proxy at 3.8% wrong at or above 5bps and gated on the result. The retracement measurement and the proxy error measurement have NO scripts today and are build steps 2 and 3."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "UNIVERSE: non-crypto event markets discovered by Gamma tag (politics, Fed, macro), resolution date 30 to 180 days out, so that the fundamental horizon is far enough away that a same-day repricing is unlikely to be information. SIGNAL: the Yes implied probability moved at least 15 points in under 6 hours, AND the number of public trade prints over that window (engine/polymarket/prices.py recent_trades_checked) is below the market's own trailing median print count, AND the move is concentrated in fewer than 5 prints. Thin prints plus a large move is the observable proxy for one order walking the book rather than a crowd repricing on news. ENTER the side OPPOSITE the move. On a Yes move from 30c to 45c that means buying No at 55c. NO ENTRY when the entry ask is above 0.75, where the payoff cannot cover the premium. SIZE: whole shares affordable at the notional cap. STOP: none. A losing share settles at exactly 0.00, the true floor, which satisfies convention 8. TARGET: resolution at 1.00. TIME EXIT: none, held to settlement. THIS IS THE PROBLEM WITH THE STRATEGY AND IT IS STATED HERE RATHER THAN BURIED: the retracement being traded takes days and the position is held for months, so the payoff actually collected is the event outcome, not the retracement. The version that sells into the bounce is not scorable today."
data_requirements: "HAVE: Gamma tag discovery (engine/polymarket/markets.py). Intraday CLOB price history keyed on conditionId with a fidelity argument (engine/polymarket/prices.py). Public trade prints and open interest from the Data API (recent_trades_checked, open_interest). Orderbook depth (engine/polymarket/orderbook.py). Resolution outcome by slug. Resolution PnL scoring (backtest/polymarket_harness.py). BLOCKER 1: NO EVENT MARKET HISTORY IS STORED, price or prints. Zero rows. The fetch paths exist and nothing has been fetched. BLOCKER 2: NO NEWS FEED EXISTS, so information arrival is unobservable and the strategy leans entirely on the print-density PROXY. That proxy has an unmeasured error rate. This project has a standing precedent for exactly this situation: engine/polymarket/strike.py supplies a proxy strike only because backtest/measure_strike_proxy.py MEASURED its error over 199 windows and STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0 is enforced before any strike-dependent strategy evaluates. An unmeasured proxy gating entries here would be below the bar this project already set for itself. BLOCKER 3: NO RETRACEMENT MEASUREMENT EXISTS. The entire edge rides on one unmeasured fraction r, the share of a fast move that gives back. At r = 0 the edge is negative. BLOCKER 4: cost_model.py PM_HALF_SPREAD_PER_SHARE = 0.005 was fitted to BTC Up/Down 5m books, and this strategy deliberately enters markets that were just shown to be thin, which is the worst case for that constant."
related_graveyard_findings: "There are NO PREDICTION_MARKET rows in the graveyard, so nothing here inherits a verdict. Nearest neighbours by mechanism are the mean-reversion cohort, and they are buried rather than promising: bollinger_reversion at minus 4.33c per trade over 48,554 pooled trades with a 31.78% win rate, stoch_rsi_oversold at minus 1.96c over 160,271 trades with a 30.46% win rate, and neither clears a third of its runs profitable. Those are verdicts on real samples, not shrugs. The honest transfer is partial: their exits are path exits with a stop, so a 30% win rate against a fixed R is arithmetically doomed regardless of signal quality, whereas a binary held to resolution has no stop to be shaken out of. What DOES transfer is the finding that a fast adverse move is more often information than noise, which is the same claim this proposal is making in reverse, and 209,000 pooled crypto trades say it did not hold there. rsi_extreme is not usable as a prior: 66 pooled trades, judgeable false, and it fires on 0.0014% of bars."
kind: edge_hypothesis
status: PROPOSED
source: "forge, event-market gap (asset class has zero graveyard coverage, D-268)"
forge_warnings: "none"
---


## Edge arithmetic

Yes moves from 30c to 45c in under 6 hours on 4 prints. Assume one third of the
move retraces, so fair value is 40c. Fade it by buying No at 55c.

    fair value of No     = 1.00 - 0.40 = 0.60
    EV per share         = 0.60 * 1.00 - 0.55 = 0.05, that is 5c
    gross edge           = 5c / 55c = 909 bps of premium paid

I record 900.

Per D-267, entry price and win rate together:

    entry 55c on the No side
    breakeven win rate    = 55.0%
    hypothesised win rate = 60.0%
    margin = 5.0 percentage points

### The number this rests on, and how fast it collapses

The one third retracement is invented. It is not measured, it is not sourced,
and it is the entire proposal. Here is the sensitivity, which is more
informative than the headline:

    retracement 1/3 (5.0c)  -> fair No 60.0c -> gross  909 bps
    retracement 1/6 (2.5c)  -> fair No 57.5c -> gross  455 bps
    retracement 1/10 (1.5c) -> fair No 56.5c -> gross  273 bps
    retracement 0           -> fair No 55.0c -> gross    0 bps

At zero retracement this is not a flat trade, it is a loss: you paid the spread
and gave up months of carry for nothing. So the honest summary of the edge
estimate is that a 900bps headline falls out of one guessed fraction, and the
guess has to be right within a factor of about three for the trade to survive
costs. That shape, a large number produced by an unmeasured constant, is the
COST_FLOOR = -0.30 shape (convention 17), and the correct response is to
measure r before writing any strategy code, not after.

### Costs and lockup

    cost, modelled walked fill 1.0c on 55c        182 bps
    cost, realistic 1.5c half spread on 55c       273 bps
    lockup, 120 day hold at 4% risk free          132 bps  (1.315% of premium)

    net at 900 gross, modelled cost               586 bps
    net at 900 gross, realistic wide book         495 bps
    net at 455 gross (r = 1/6), realistic wide     50 bps
    net at 273 gross (r = 1/10), realistic wide  -132 bps

A strategy whose net edge swings from 586bps to negative on one unmeasured
parameter is not a strategy yet. It is a measurement request.

Note also that this strategy selects INTO thin books by construction. The
realistic column is the relevant one for it, not the modelled column.

### Sample size

    entry 55c, hypothesised win rate 60%
    win return = 45/55 = +81.82%, lose return = -100%
    mean = 0.60 * 0.8182 - 0.40 = +9.09% of capital
    sd   = sqrt(0.60 * 0.40) * (0.8182 + 1.0) = 0.490 * 1.818 = 0.891
    n for t = 2 = (2 * 0.891 / 0.0909)^2 = 384 trades

384 resolved entries. That is above the harness floor of 200, so a PASS at
n = 200 would be underpowered by this strategy's own arithmetic and the harness
would say so in trades_needed_for_2sigma. It also means a run of 200 is not
worth interpreting in either direction (convention 7, and D-256 which says a
PASS on 87 trades is a shrug just as loudly as a FAIL is).

## What would make this wrong

**The trade I described is not the trade the harness will score, and the gap is
worse here than in proposal 009.** The retracement is hypothesised to happen
over days. The position is held for up to 180 days because there is no sell
path in the scoring model. So we enter on a liquidity signal and get paid on an
election result. Those are barely related. A PASS would most likely mean we
found a way to buy underpriced No shares, which is proposal 008's claim, not
this one; a FAIL would not falsify the overshoot thesis at all. The only clean
version of this strategy exits on the bounce, and backtest/polymarket_harness.py
refuses to compute interim marks by design, with reasons given in its header
that I agree with. **That makes this the weakest of the three proposals and the
one I would build last.**

**This and proposal 009 may be one hypothesis with two arms.** They take
opposite sides of the same observable. The separator is speed plus print
density, and if the print-density proxy cannot actually tell an informed
repricing from an order walking a book, then 009 and 010 are the same test with
a sign flip and reporting whichever wins is p-hacking. The graveyard's own
number is the warning: 486,783 tests, expected best around 5.1 sigma under the
null. **If both are built they count as ONE hypothesis with two arms, and
neither result may be reported without the other.** SOUL is explicit that an
inverted variant carries the original's hypothesis count, and a pair proposed
together is the same problem arriving earlier.

**The crypto graveyard already voted against the mechanism.** bollinger_reversion
and stoch_rsi_oversold pool to 209,000 trades and lose money at roughly 30%
win rates. The claim that a violent move is mostly noise did not hold on spot.
Arguing that binaries are different is a real argument, since the payoff and
the exit are genuinely different, but it is an argument, not evidence, and the
signal-quality part of that finding transfers even though the exit part does
not.

**A 15 point move in 6 hours on 4 prints is probably somebody who knows
something.** The proposal reads thin prints as an absence of information. The
opposite reading is at least as plausible: on a market that trades rarely, the
person who does trade is the person with a reason. If informed flow is
concentrated in exactly the low-print-count windows the signal selects, the
proxy is not neutral, it is inverted, and the strategy systematically takes the
other side of the best-informed order in the book.

**Print count is public trade data, and public trade data is incomplete.** The
Data API reports fills. It does not report the resting size that got lifted or
the depth that was there before. Two prints can be 200 dollars or 200,000
dollars. Weighting the proxy by notional rather than by count is an obvious
improvement and it is still a proxy, and per this project's own precedent it
must have its error measured before it gates an entry.

## Build order

1. Archive resolved event markets, their intraday price history, and their
   trade prints. Nothing exists today.
2. Measure r, the retracement fraction, over 200 or more qualifying fast moves.
   If the median is under 1.0c, stop here. No code, no sweep, no strategy.
3. Measure the print-density proxy's error against the cases where a catalyst
   is identifiable by hand, and set a floor the way
   STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0 is set in engine/polymarket/strike.py.
   An unmeasured proxy does not gate anything in this project.
4. Only then score with backtest/polymarket_harness.py.

Until step 4 runs this is NOT_TESTED in the convention 11 sense, meaning could
not run. It is never "ran and found nothing", and no interim number from steps
1 to 3 is a verdict on the strategy.
