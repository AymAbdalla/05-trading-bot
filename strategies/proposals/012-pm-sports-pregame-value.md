---
name: "pm_sports_pregame_value"
thesis: "Sportsbook closing lines are the sharpest public forecast of a sporting event, and where a thin Polymarket pregame market prices an underdog materially below the vig-removed consensus of several books, the Polymarket side is stale rather than informed, so buying it captures the gap at resolution."
expected_edge_bps: 650
kill_condition: "Kill if fewer than 300 qualifying gaps of 4c or more appear in 90 days of paired book and Polymarket snapshots, since below that the strategy cannot reach a judgeable sample. Kill if backtest/polymarket_harness.py scores below 200bps net edge, or a t_stat below 2.0, on 200 or more resolved entries. Kill immediately, regardless of PnL, if the median signed gap changes by more than 1c when proportional devig is swapped for Shin devig, because that means the measurement is reading the devig method and not the market."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "For each pregame market in scope, snapshot the Polymarket best ask on both sides and, within 60 seconds of the same wall clock, the moneyline from 3 or more independent sportsbooks. DEVIG each book with Shin's method, then take the median devigged probability across books as fair. GAP = fair_underdog - polymarket_ask_underdog. ENTER the underdog side when GAP is 4c or more AND the Polymarket ask is between 0.15 and 0.45 AND the game starts within 7 days AND no book line has moved more than 1c in the preceding 10 minutes AND we are more than 90 minutes from scheduled start. ONE leg, no hedge. SIZE: venue minimum lot, capped, one entry per market, never averaged down. STOP: 0.00, the value of a losing share, which satisfies convention 8. TARGET: resolution at 1.00. TIME EXIT: none, held to settlement. REFUSE the entry if the Polymarket quote is older than the book snapshot, because then the gap is our own latency rather than the market's error."
data_requirements: "Polymarket side, which we have: Gamma discovery with a tag filter and search_markets (engine/polymarket/markets.py), best ask from the CLOB book (engine/polymarket/orderbook.py), resolution outcome (engine/polymarket/prices.py), and a resolution-PnL scorer (backtest/polymarket_harness.py). BLOCKER: THERE IS NO SPORTSBOOK ODDS FEED ANYWHERE IN THIS REPO. Nothing reads any book, no odds API client exists, engine/feeds/ holds only hyperliquid_client.py and liquidation_recorder.py. Without it this strategy has no fair value and therefore no entry rule at all. BLOCKER: THERE IS NO EVENT MAPPING. Nothing maps a Polymarket slug to a sportsbook event id, and team naming across NFL, NBA, CS:GO and soccer is not consistent between providers. A wrong mapping does not error, it silently prices the wrong game. BLOCKER: THERE ARE NO PAIRED TIMESTAMPS. The gap is only meaningful if the two snapshots are simultaneous, and a stored odds history without the exact observation time cannot be used. BLOCKER: NO MEASURED SPREAD OR DEPTH FOR SPORTS BOOKS. cost_model.py flags PM_HALF_SPREAD_PER_SHARE and PM_DEPTH_SLIP_PER_SHARE as UNMEASURED ASSUMPTION, and they were guessed against a BTC 5-minute book, not a sports book."
related_graveyard_findings: "There are NO PREDICTION_MARKET rows in the graveyard and no sports rows of any kind, so nothing here is buried and nothing is confirmed. The closest thing in the repo by shape is proposal 005 pm_cross_window_relative_value, which is also a one-leg relative-value bet against a computed fair value with no floor and which can lose its whole premium. 005 is PROPOSED and UNBUILT for exactly the reason this one would be: its fair value depends on a measured distribution we do not have. The two must not be pooled. 005 compares two Polymarket windows on the same underlying; this compares Polymarket against an external forecast produced by a different market with a different fee structure and a different participant set."
kind: edge_hypothesis
status: PROPOSED
source: "forge, sports market expansion brief"
forge_warnings: "none"
---


## Devig arithmetic, shown rather than asserted

An undevigged comparison manufactures an edge out of the bookmaker's margin
and nothing else. Here is the full working on an NFL moneyline.

Quoted American odds: favourite -400, underdog +320.

    favourite implied  = 400 / 500       = 0.8000
    underdog  implied  = 100 / 420       = 0.2381
    overround          = 0.8000 + 0.2381 = 1.0381   (3.81 percent of vig)

**Proportional devig**, which divides each side by the overround:

    underdog fair = 0.2381 / 1.0381 = 0.2294

**Shin devig**, which solves for the insider-trading parameter z in

    p_i = [ sqrt(z^2 + 4(1-z) * pi_i^2 / overround) - z ] / (2(1-z))

subject to the probabilities summing to 1. Solving numerically gives z of
about 0.039, and:

    favourite fair = 0.7809
    underdog  fair = 0.2190
    sum            = 0.9999

So the two standard methods disagree by

    0.2294 - 0.2190 = 0.0104 per share, about 1c

**That single methodological choice is larger than the entire modelled cost
budget of the trade.** Proportional devig spreads the margin evenly, which is
known to overstate longshots, because books load more margin onto the longshot
side. Shin pulls the longshot down. If the strategy uses proportional devig it
will find "underdog edges" that are pure artifact, so Shin is the entry rule
and proportional is the control. That is what the third clause of the kill
condition tests: if swapping the method moves the median gap by more than 1c,
the strategy is measuring the arithmetic and not the market.

## Edge arithmetic

Entry filter is a gap of 4c or more after Shin devig. Apply a 50 percent
haircut, on the same reasoning proposal 005 used for a mean-reversion gap: some
of the measured gap is devig residual, some is snapshot staleness, and assuming
full capture is the standard way this class of backtest flatters itself.

    expected realized gap = 4c * 0.50            = 2.0c per share
    typical entry premium (the 0.15 to 0.45 band)= 30c
    gross edge = 2.0 / 30                        = 667 bps

Recorded as 650 bps, rounded down. It is an ESTIMATE from one hand-worked
example and a haircut, not a measurement (convention 15).

Costs, from `backtest/cost_model.py`, one leg only because a binary held to
resolution redeems rather than being sold:

    half spread 0.005 + depth slip 0.005 = 1.0c per share
    1.0 / 30                             = 333 bps

    net edge = 667 - 333 = 334 bps

Both cost terms are marked UNMEASURED ASSUMPTION in that file and were guessed
against a one-tick-wide BTC book. On a thin CS:GO market they are optimistic.

Note what the fixed per-share cost does across the price range, because it
decides the 0.15 lower bound in the entry rule:

| Entry premium | 1c modelled cost | as bps |
|---|---|---|
| 45c | 1c | 222 |
| 30c | 1c | 333 |
| 20c | 1c | 500 |
| 10c | 1c | 1000 |
| 5c  | 1c | 2000 |

Below about 15c the modelled cost alone eats more than the floor, which is why
this strategy refuses to buy cheap underdogs and why proposal 013 is a separate
and much worse-looking bet.

## Entry price and win rate, together (D-267)

At a 30c entry the break-even win rate is 30 percent before costs and 31
percent after the modelled 1c. The claim is not "underdogs win more often than
people think". The claim is precisely this: **markets where Polymarket asks 30c
and Shin-devigged consensus says 34c resolve YES about 34 percent of the time,
so we are paying 31 all-in for a 34.** Either half of that sentence read alone
is meaningless on a binary.

## The base rate says Polymarket is right and we are the sucker

This has to be stated plainly because it is the most likely outcome.

Prediction market prices on liquid sports markets track sharp book prices
closely. When they disagree, the ordering of explanations, most likely first:

1. **Our book snapshot is stale and the line has already moved.** News, an
   injury, a lineup, weather, a starting goalkeeper. We are then trading
   against our own latency and every "edge" is a measurement of it. The 10
   minute line-stability filter and the timestamp-ordering refusal in the entry
   rules exist for this and are the two rules I would fight hardest to keep.

2. **We picked the wrong books.** A retail book with a soft line is not
   consensus. Three books that all copy the same feed are one book. The median
   across 3 or more independent books is a partial defence and not a complete
   one.

3. **Our event mapping is wrong** and we are comparing two different games. It
   fails silently. It needs an assertion, not a hope.

4. **There is a real term Polymarket is pricing that our fair value omits.**
   Capital is locked until resolution. A market settling in 3 months should
   trade below its fair probability by roughly the cost of that capital: at 5
   percent annual, 3 months is about 1.25 percent of notional, or 0.4c on a 30c
   contract. That is why entries are restricted to games within 7 days, where
   this term is under 0.1c and cannot be mistaken for edge. There is also
   resolution risk, meaning the small chance the market resolves in a way the
   sportsbook contract would not have, and it is a real discount rather than an
   error.

5. **Polymarket is simply right and the books are wrong.** Least likely on a
   liquid market, but not zero on a thin one where a single informed
   participant has moved the price.

Only after all five are excluded is the gap an edge. The uncomfortable
implication is that the gaps big enough to pay for our costs, 4c and up, will
mostly live in the thinnest markets, which are exactly where the 1c cost
assumption is least trustworthy and where explanation 5 is most likely.

## Sample size, which is the quiet problem

At a 30c entry, a winner returns plus 233 percent of capital and a loser minus
100 percent. At the win rate implied by a 4 percent per-trade edge, about 31.2
percent, the standard deviation of per-trade return is roughly 1.55.

    trades for a 2-sigma read = (2 * 1.55 / 0.04)^2 = about 5,970

`backtest/polymarket_harness.py` reports `trades_needed_for_2sigma` on every
run and will say this out loud. An NFL season is 272 regular season games. An
NBA season is 1,230. Adding soccer and CS:GO gets the market count into the
thousands per season, but the count of entries that clear a 4c gap filter will
be a small fraction of that. **This strategy may be unconfirmable within the
data horizon we can actually assemble.** The harness minimum of 200 resolved
trades gets it to judgeable, and convention 7 says a PASS at 200 on an effect
that needs 6,000 is a shrug, not a verdict.

## What would make this wrong

- **The haircut may be too generous in the wrong direction.** I assumed we
  capture half the gap. If the gap is mostly devig residual we capture close to
  none of it, and the strategy is a 333bps cost with no offsetting edge. The
  Shin against proportional control is the direct test of this and it is why
  that clause kills the strategy on its own, without waiting for PnL.

- **Closing line value is not the same as entry value.** Books get sharp near
  the close. A line 3 days out is much softer than a line 20 minutes out, so
  gaps found early are more likely to be book softness than Polymarket error,
  and the direction of that error is unknown. Restricting to the 90 minutes
  before start would use the sharpest line, but 90 minutes before start is also
  when Polymarket is most liquid and most correct. The current rule excludes
  that window for the injury news reason, which means it deliberately trades
  against a softer line. That tension is unresolved and a reviewer should push
  on it.

- **Selection on gap size selects on our own error.** A 4c filter picks the
  observations where either the market or our measurement is most extreme, and
  our measurement has at least three failure modes above. This is the
  regression-to-the-mean trap that convention 17 describes in the
  `COST_FLOOR = -0.30` case: the filter looks like it is finding signal and is
  partly ranking our own noise.

- **The 4c threshold is an assumption with an expiry date.** It was chosen so
  that a 50 percent haircut still clears costs, not because 4c was observed to
  be special. If a future version lowers it because too few trades qualified,
  that is fitting the gate rather than the market, and it must be logged as an
  additional hypothesis rather than as the same test.
