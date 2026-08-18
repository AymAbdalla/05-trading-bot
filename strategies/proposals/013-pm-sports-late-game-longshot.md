---
name: "pm_sports_late_game_longshot"
thesis: "The stated idea is that a team that is nearly beaten trades at 3c to 6c late in a game and wins more often than that price implies, so the cheap side is underpriced; the literature on favourite-longshot bias says the opposite is true and the arithmetic below comes out at roughly minus 6,000 bps, so this is filed as a probe that measures the calibration of cheap late-game contracts and expects to find the edge is negative."
expected_edge_bps: null
kill_condition: "Kill the stated long-the-longshot version if backtest/polymarket_harness.py measures a realized win rate below the average entry price on 200 or more resolved sub-10c late-game entries, which is exactly the break-even condition on a binary. Kill the favourite-side inversion if backtest/polymarket_harness.py scores it below 200bps net edge, or a t_stat below 2.0, on 755 or more resolved trades, which is the sample the estimated 2c per share effect actually needs. Kill both immediately if the measured half spread on sub-10c sports books exceeds 1c per share, since at that point the cost model's 0.005 assumption is wrong by more than the entire trade."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "PROBE MODE (what is proposed). Take no positions. For every in-scope NFL, NBA, CS:GO and soccer market, record the best bid, best ask and mid at 1 minute resolution over the final 25 percent of scheduled game time, together with the game clock and score, and the eventual resolution. Bucket every observation by quoted price into 1c buckets from 0.01 to 0.10 and by game state, then compute realized frequency per bucket and compare it to the bucket price. That comparison, and only that comparison, is the deliverable. MEASUREMENT MODE if the probe finds cheap contracts are UNDERpriced: buy the losing side at ask when ask is 0.03 to 0.08 and the bucket's measured realized frequency exceeds ask plus 1c of modelled cost. INVERSION MODE if the probe finds cheap contracts are OVERpriced, which is what I expect: buy the WINNING side at 0.92 to 0.97, which on a binary is how you short the longshot without borrowing (D-267). Both modes are one leg, venue minimum lot, STOP 0.00 which is what a losing share is worth, TARGET resolution at 1.00, no time exit, held to settlement."
data_requirements: "Polymarket side, which we have: Gamma discovery, CLOB book, minute-grained price history via engine/polymarket/prices.py, resolution outcome, and backtest/polymarket_harness.py to score it. BLOCKER: THERE IS NO GAME CLOCK OR SCORE FEED IN THIS REPO. engine/feeds/ holds hyperliquid_client.py and liquidation_recorder.py and nothing else. Without a clock, late game can only be proxied by time to resolution, and without a score, losing side can only be proxied by the price itself, which makes the whole study circular: we would be selecting on price and then testing price. BLOCKER: NO SPORTS MARKET DISCOVERY IS WIRED. The Gamma tag parameter exists but nothing selects sports tags and the sports resolution path has never been exercised. BLOCKER: THE COST ASSUMPTIONS ARE FATAL AT THESE PRICES AND ARE UNMEASURED. backtest/cost_model.py flags PM_HALF_SPREAD_PER_SHARE 0.005 and PM_DEPTH_SLIP_PER_SHARE 0.005 as UNMEASURED ASSUMPTION taken from a one-tick BTC book. On a 4c contract that 1c is 2,500 bps, so on this strategy the guessed cost is not a detail, it is the dominant term. BLOCKER: NO HISTORICAL ORDERBOOK EXISTS. entries_from_decisions in the harness says so directly: the honest ceiling on any offline Polymarket backtest is a quote-based fill with a modelled spread, never a book walk. At 4c a quote-based fill assumption is the difference between a result and a fiction."
related_graveyard_findings: "There are NO PREDICTION_MARKET rows in the graveyard and no sports rows of any kind, so there is no burial to engage. The relevant prior evidence is external and it points against the stated thesis: Thaler and Ziemba 1988 on favourite-longshot bias in parimutuel racing, and Snowberg and Wolfers 2010 attributing it to misperception of small probabilities rather than risk-seeking. Both find longshots are systematically OVERPRICED, meaning the expected return on buying them is worse than on buying favourites. That is published work on other venues and it is a hypothesis about Polymarket, not evidence about it (convention 3). It is cited here as a prior that the probe is designed to check, not as a result."
kind: experiment
status: PROPOSED
source: "forge, sports market expansion brief"
forge_warnings: "none"
---


## The honest finding up front

I do not believe the stated version of this strategy has a positive edge. The
best available prior says its edge is negative, the arithmetic below puts it at
roughly minus 6,000 bps, and the modelled cost at these prices is 2,500 bps
before anyone has an opinion about the game. This is written as a probe with a
null edge because `agents/forge.py` refuses an `edge_hypothesis` below the
200bps PREDICTION_MARKET floor, and it should. Recording a negative expectation
as a proposal is more useful than not recording it, because the idea is
intuitive enough that somebody will suggest it again in three months.

## A cheap price on a nearly-lost game is usually correctly cheap

The intuition behind the strategy is that people give up on a losing team too
early. The evidence runs the other way. In parimutuel betting, and in
sportsbook markets, longshots are systematically overbet: bettors pay too much
for small probabilities of large payoffs, so the expected return on a longshot
is worse than on a favourite, not better. Thaler and Ziemba documented the
effect in 1988, and Snowberg and Wolfers in 2010 attributed it to misperception
of small probabilities rather than to a taste for risk. The same bias shows up
in low-priced prediction market contracts.

So the specific instrument this strategy proposes to buy is the instrument the
literature identifies as the most reliably overpriced one on the board. That is
not a reason to refuse to measure it on Polymarket, which nobody here has done.
It is a reason to state the expected sign before measuring, which is what
convention 4 requires.

## Edge arithmetic on the stated version (D-267, price and win rate together)

Take an NFL case that is easy to check against published in-game win
probability models. Team A is down 10 points with 4:00 left and does not have
the ball.

    Polymarket ask on Team A          = 0.04
    break-even win rate before costs  = 4 percent
    modelled cost                     = 1.0c per share (0.005 + 0.005)
    effective all-in entry            = 0.05
    break-even win rate after costs   = 5 percent

Published win probability models put that game state at roughly 1 to 3 percent.
Call it 2 percent, which is the generous end of what I would defend.

    EV per share = 0.02 * 1.00 - 0.05 = -0.03
    return on capital = -0.03 / 0.05  = -60 percent = -6,000 bps

The number to notice is not the minus 6,000. It is that the cost line alone is
1c on a 5c all-in position, which is **2,500 bps of the premium**, so this
strategy loses to the cost model before the game is even considered. Every
strategy whose average entry is under 10c is paying more than 1,000 bps in
modelled costs, and both components of that cost are marked UNMEASURED
ASSUMPTION in `backtest/cost_model.py`, guessed from a BTC book. At 4c we are
not measuring sport, we are measuring a guessed spread.

## The inversion, which is the interesting half, and it is too small

If longshots are overpriced then the tradeable side is selling them. On
Polymarket you cannot short, but buying the other side IS the short (D-267), so
selling a 4c longshot means buying the favourite at 0.96.

    entry                             = 0.96
    break-even win rate before costs  = 96 percent
    all-in with 1c modelled cost      = 0.97, so 97 percent after costs

Suppose the true probability is 98 percent, meaning cheap contracts are
overpriced by 2 points, which is roughly the magnitude the longshot-bias
literature reports at these odds.

    EV per share = 0.98 * (1.00 - 0.96) - 0.02 * 0.96
                 = 0.0392 - 0.0192
                 = +0.0200 per share

    gross edge = 0.0200 / 0.96 = 208 bps
    net edge   = (0.0200 - 0.0100) / 0.96 = 104 bps

**The inversion clears the 200bps gross floor by 8 bps and then dies on costs.**
That is the honest read of the one version of this idea that the literature
supports. It is a real effect pointed in the right direction and it is smaller
than our guessed transaction cost.

Sample size, since a 2 percent per-trade effect with 96c at risk is a slow
thing to see. At p of 0.98 a winner returns plus 4.17 percent and a loser minus
100 percent, so the per-trade standard deviation is about 0.286 against a mean
of 0.0208.

    trades for a 2-sigma read = (2 * 0.286 / 0.0208)^2 = about 755

That is the number in the kill condition. It is reachable in a season or two of
NFL plus NBA plus soccer, which makes the inversion the only version of this
worth any engineering time, and even then only if the measured spread on those
books turns out to be well under the 1c assumption.

## The tail risk nobody mentions when they pitch this

Buying at 0.96 for a 4c gain risks 96c to make 4. Twenty-four winners pay for
one loser. A single mispriced market, a resolution dispute, or a sport where
comebacks are more common than the model thinks wipes out weeks of it. The
payoff shape is the same one that makes selling deep out-of-the-money options
look wonderful right up until it does not, and it deserves the same suspicion.
It is not a reason to refuse the trade. It is a reason that a PASS on 200 trades
is a shrug here in a stronger sense than convention 7 usually means, because
the losses are rare by construction and a 200 trade window can easily contain
too few of them.

## Why the probe is still worth running

Three outputs, none of which require believing the stated thesis.

1. **A calibration curve for cheap Polymarket sports contracts.** Realized
   frequency against quoted price in 1c buckets from 0.01 to 0.10. That is a
   reusable measurement. Every future strategy that touches a cheap contract
   needs it, and nobody has it.

2. **A measured spread and depth table at low prices.** This directly replaces
   two UNMEASURED ASSUMPTION constants in `backtest/cost_model.py` that
   currently ride under every Polymarket number in this repo, not just this
   strategy's.

3. **The sign of the bias on this specific venue.** Polymarket is not a
   parimutuel pool and its participants are not racetrack bettors. The bias
   could be absent, or reversed, because the marginal Polymarket participant is
   more likely to be running a model. If the bias is reversed then the stated
   version of this strategy is right after all and I am wrong, which is the
   point of measuring instead of arguing.

## What would make this wrong

- **The 2 percent true probability is a model output, not a measurement.**
  In-game win probability models disagree with each other, are fit on
  historical play data, and are themselves uncertain at the tails. If the real
  figure at that game state is 4 percent rather than 2, the stated version goes
  from minus 6,000 bps to break-even before costs. My estimate could be wrong
  by a factor of two and I would not currently know.

- **The favourite-longshot literature is about other venues.** Racetracks and
  sportsbooks have a house take, a captive audience and no ability to sell.
  Polymarket has a two-sided book, near-zero fees today, and anybody can take
  either side. Every structural reason the bias exists elsewhere is weaker
  here, so its magnitude on this venue is unknown and could be zero. Citing
  external literature as if it were our result is exactly what D-267 forbids
  for moondevonyt's win rates, and it applies to academic sources too.

- **Price-based selection is circular and the probe as specified is partly
  guilty of it.** Without a score feed, "the losing side late in the game" can
  only be identified by its price being low, and then the study asks whether
  low prices are calibrated. That is a weaker question than the intended one
  and it cannot separate "cheap because losing" from "cheap because thin".
  Fixing it requires the score feed named as a BLOCKER, which is the real
  reason this cannot be built today.

- **Selection survivorship in the resolved market set.** If disputed or voided
  markets are dropped from the sample, and disputes correlate with unexpected
  outcomes, the measured longshot win rate is biased downward and the inversion
  looks better than it is. The probe has to count dropped markets by reason
  (convention 20) rather than filter them away.
