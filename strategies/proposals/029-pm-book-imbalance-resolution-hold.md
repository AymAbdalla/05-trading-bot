---
name: "pm_book_imbalance_resolution_hold"
thesis: "The participant is the retail directional taker in the last 60 to 120 seconds of a 15m Polymarket up/down window. When spot has already moved decisively through the strike, those takers keep paying up for the winning side and the ask on that side is bid toward 0.90 to 0.97 while the true resolution probability is higher than the price. The inefficiency persists because the market maker on Polymarket will not quote a binary at 0.99 with 90 seconds left: there is inventory risk from a late spot reversal, and the maker charges for that risk. We are not forecasting anything. We are selling the maker's late-window reversal insurance and holding to resolution, so the ASK-to-BID round trip that killed the fair_value family never gets paid. There is exactly one transaction: buy at ask, hold until the contract settles at 1.00 or 0.00."
expected_edge_bps: 300.0
kill_condition: "If mean net P&L per resolved position is below 0.00 cents across 200 or more resolved positions in forge_shadow_eval, retire it. Second, independent kill: if fewer than 60 positions have resolved after 40,000 evaluations in shadow_loop, retire it on frequency, because a strategy that cannot reach a verdict is not a strategy."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  Universe: 15m crypto up/down markets only. Evaluate every 5s loop tick.
  
  Entry, all six conditions required:
  1. Time remaining in the 15m window is between 45 and 150 seconds. Outside that band, skip with reason out_of_hold_band.
  2. Spot is through the strike in the direction of the side we would buy, by strictly more than 3x STRIKE_PROXY_NOISE_FLOOR_BPS (currently 5.0 bps, so 15.0 bps). Below that, skip with reason strike_inside_proxy_noise_floor and never widen this. The floor is instrument error, not a filter.
  3. The best ask on the through-strike side is between 0.88 and 0.97 inclusive. Below 0.88 the market disagrees with us and we do not overrule it. Above 0.97 there is under 200 bps of premium left to collect and it is below the floor.
  4. Ask depth at that price is at least 2x our intended size. Otherwise skip with reason insufficient_ask_depth.
  5. No open position from this strategy in this market. One position per market, ever.
  6. Realized net P&L for this strategy today is above -$30.00 so the existing daily_loss_breaker gate stays authoritative.
  
  Size: 5 shares per entry. Fixed. Do not scale by confidence.
  
  Exit: hold to resolution. There is exactly one intentional exit path and it is settlement.
  Stop: 0.00, which is the resolution value of a losing binary share and is strictly below every legal entry price of 0.88 to 0.97. This satisfies convention 8 honestly: the stop is where the loss actually lands, not an invented 3 cent price stop. The corridor note is explicit that no exit path exists between entry and resolution on the paper adapter, so a stop at any other number would describe a fill that does not exist.
  One forced exit only: if the loop observes spot back through the strike against us by more than 15.0 bps AND more than 20 seconds remain, sell at the best bid immediately and log exit reason sell:thesis_broken. This is the only place we pay a round trip, and it should be rare by construction.
  No profit target. A profit target on a hold-to-resolution binary is the ASK-to-BID round trip re-entering through a side door.
data_requirements: |
  1. Polymarket 15m up/down order book, best ask and ask depth. HAVE IT. The shadow loop already reads it; no_asks and insufficient_ask_depth are live skip counters.
  2. Crypto spot for BTC, ETH, SOL. HAVE IT. The fair_value family consumes it on every crypto evaluation, and its absence off-crypto is what produces the 2,470 fair_value_model_needs_crypto_spot rows.
  3. Window strike and window close timestamp. HAVE IT. not_final_third_of_15m, late_in_window and too_close_to_resolution all already compute from it.
  4. STRIKE_PROXY_NOISE_FLOOR_BPS. HAVE IT, currently 5.0, and it fires 30,108 times so the measurement path is live.
  5. Resolution settlement at 1.00 or 0.00 recorded on the position row. HAVE IT. Every corridor loss row exits at 0.000 and every corridor win at 1.000.
  6. Bid at entry and ask at entry logged on the fill row. DO NOT HAVE IT. Both vault notes flag the spread as NOT_MEASURED because there is no spread column. This proposal does not need the spread to trade, because it pays only one leg, but the post-hoc claim that the round trip was avoided cannot be verified without it. Log it or the verdict is weaker than it needs to be.
  7. Maker fill simulation. NOT NEEDED. This is a taker entry on purpose.
markets: "Polymarket crypto up/down 15m binaries on BTC, ETH, SOL. 15m only, never 5m."
kind: edge_hypothesis
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

## Thesis

In the last 45 to 150 seconds of a Polymarket 15m crypto up/down window, when spot is decisively through the strike, the winning side does not price at 0.99. It prices around 0.90 to 0.95. That gap is the market maker's charge for late-window reversal risk on inventory they cannot hedge cheaply. We buy that insurance premium and hold to settlement.

The participant is the maker, not a mispricing retail crowd. Makers on a binary with a hard clock are short gamma with no offsetting instrument. They quote below true probability because a reversal costs them the whole notional. That is a structural reason for the gap to persist rather than a behavioural one, which is why it does not get arbitraged away by anyone unwilling to hold to resolution.

## The arithmetic

The denominator on a Polymarket binary is the premium in cents. One tick is 1 cent. On a 0.92 contract, 1 cent is 108.7 bps, so the floor of 200 bps is roughly 1.8 ticks.

Buy at 0.92, settle at 1.00. Gross gain on a win is 0.08 per share, which is 8.70% of the 0.92 paid, or 870 bps.
Lose, settle at 0.00. Loss is 0.92 per share, which is 10,000 bps of the entry.

Breakeven win rate: 0.92 / 1.00 = 92.0%.

Assume the true resolution rate under these entry conditions is 95%. That number is an ASSUMPTION, not a measurement, and it is the weakest link in this proposal. It is stated here so it can be attacked.

Expected value per share at 95% true, 0.92 entry:
0.95 x (+0.08) + 0.05 x (-0.92)
= +0.0760 - 0.0460
= +0.0300 per share.

As bps of the 0.92 premium at risk: 0.0300 / 0.92 = 3.26%, so 326 bps.

Haircut for the rare sell:thesis_broken exit, which is the only place a round trip is paid. Assume it fires on 8% of entries at an average round-trip cost of 3 cents. That is 0.08 x 0.03 = 0.0024 per share, which is 26 bps of 0.92.

326 - 26 = 300 bps. That is the number in expected_edge_bps and it is a gross estimate, not a promise. Fees on this venue are $0.00 across 616 fair_value trades and 9 corridor trades, so there is no commission line to subtract.

Sensitivity, because the 95% assumption carries the entire result:

| true resolution rate | EV per share at 0.92 | bps of premium |
|---|---|---|
| 97% | +0.0500 | +543 |
| 95% | +0.0300 | +326 |
| 93% | +0.0100 | +109, below the 200 bps floor |
| 92% | 0.0000 | 0 |
| 90% | -0.0200 | -217 |

Read that table honestly. A 2 point error in the resolution rate flips this from a pass to a fail. That is the whole risk and no amount of parameter work changes it. This is a proposal to MEASURE a number we do not have, structured so that measuring it is also trading it.

## Evidence leaned on

- 2026-08-18-fair-value-arb-spread-problem.md, for the finding that the round trip and not the model is the cost, and specifically the inverse variant at 48.1% win rate and -$32.50 on 154 trades.
- 2026-08-18-corridor-pair-works.md and corridor_pair_live.md, for the fact that fees are $0.00 and exits are at resolution in the hold-to-settlement path, so the spread diagnosis does not transfer to a single-leg hold, and for the stop-at-0.00 rule.
- hypothesis_graph ids 112, 113, 114, 136, 137, 138, 139, 142, 148, 149.
- The live shadow skip table for STRIKE_PROXY_NOISE_FLOOR_BPS at 5.0 with 30,108 strike_inside_proxy_noise_floor rows, which is the measurement that lets condition 2 be written as a multiple of a measured floor rather than an invented threshold.

## What would change my mind

1. Compute the historical resolution rate directly, before trading a single share. Take every 15m crypto window in the existing tape, find every moment in the 45 to 150 second band where spot was through the strike by more than 15 bps, and record whether that side resolved to 1.00. If the observed rate is below 93% on 500 or more such moments, this proposal is dead on arrival and should never reach the shadow loop. This is a read of existing data, not new research, and it should be done first.
2. If entry asks under these conditions cluster above 0.97 rather than in the 0.88 to 0.97 band, there is less than 200 bps of premium available and the strategy is below its floor by construction. Measure the ask distribution before assuming the band is populated.
3. If sell:thesis_broken fires on more than 25% of entries, the round trip is back and this becomes fair_value_arb with extra steps. Kill it there rather than tuning the 20 second guard.

Named harness: backtest/validate_harness.py must exit 0 before any of this counts, per convention 1. Scoring is forge_shadow_eval over db/trading.db signals and positions.

## Why this might fail

The strongest argument against it: a 0.92 ask on a through-strike binary with 90 seconds left may already be a fair price, in which case there is no edge at all and we have built an expensive way to earn zero minus adverse selection. The market maker quoting 0.92 knows the same spot we know, sees the same clock, and has a faster feed. If their 0.92 is correct, our expectancy is exactly zero before any cost and negative after the rare sell:thesis_broken round trip.

Worse, the losses are structurally brutal. Buying at 0.92 risks 0.92 to win 0.08. One loss erases 11.5 wins. That means the win rate needed is 92%, and a win rate estimate is exactly the kind of thing this project has been wrong about repeatedly. The fair_value family needed 58.5% and delivered 32.8%. If the true resolution rate at our entry conditions is 90% instead of 95%, the strategy loses money while looking like it is winning 9 times out of 10, which is the most psychologically dangerous shape a losing strategy can have.

The adverse selection case is specific and real: the late spot reversals that resolve against us are not independent of our entry. We enter after a decisive move, and decisive short-horizon crypto moves partially mean-revert. We may be systematically buying the tail we think we are selling. corridor_collector already proved this family of error in the opposite direction: it bought 0.10 to 0.31 tails and went 0 for 4, every one to 0.000.

Finally the frequency risk. Requiring a 15m window, a 45 to 150 second band, a 15 bps through-strike move and an ask in 0.88 to 0.97 is a conjunction of four filters. corridor_pair_live acted 1 time in 4,874 evaluations on a comparable conjunction. This may simply never reach 200 resolved positions, which is why the frequency kill is written as a separate condition rather than a footnote.

## What past failure this addresses

Directly addresses the mechanism named in hypothesis_graph ids 113, 114 and 149 (PM_fair_value_arb, PM_fair_value_arb_hft, failure_mode spread_eats_edge and stop_too_tight) and ids 136, 137, 139 (model_miscalibrated on PM_fair_value_arb, PM_fair_value_arb_hft, PM_fair_value_arb_inverse). The vault note 2026-08-18-fair-value-arb-spread-problem.md states the finding in one line: enter at ASK, exit at BID, 616 trades, -$338.60, $0.00 fees, and the inverse variant winning 48.1% while still losing $32.50 proves the cost is direction-agnostic and therefore is the round trip and not the model. That note's prohibition 1 is do not propose any strategy that pays market orders on both legs of a Polymarket 5m binary. This proposal pays market orders on ONE leg, holds the other side of the round trip to settlement, and is explicitly a 15m strategy, not a 5m one. That is the difference and it is a mechanism difference, not a rename.

It also respects the negative results from that same note rather than re-running them: it does not invert a losing signal (prohibition 2, already run as _inverse, id 139), does not lower an edge threshold to buy frequency (prohibition 3, already run as _hft, id 114 and id 142), does not touch max_trades_this_window (prohibition 5, which blocks 77.4% of that family's evaluations while the family is negative), and quotes no spread number as a filter (prohibition 8, spread is NOT_MEASURED).

From 2026-08-18-corridor-pair-works.md and the strategy card corridor_pair_live.md it takes two things. First, the stop rule: prohibition 6 in that note says do not propose a corridor variant whose stop is anything other than 0.00, because there is no exit path between entry and resolution on the paper adapter. This proposal's stop is 0.00 for exactly that reason. Second, its inverse lesson: prohibition 5 says do not buy cheap tails, because corridor_collector entered at 0.185 average and exited at 0.000 on all 4 trades (see also hypothesis_graph id 148, PM_corridor_collector, unclassified). This strategy buys the expensive side, which is the other end of the same distribution, and its risk is therefore the mirror image and must be stated as such, which why_it_might_fail does.

id 112 and id 138 (PM_dip_arb, stop_too_tight then entry_signal_wrong, 78.8% of 33 closes stop-like) is the third relevant record. Its named failure is stop-like exits dominating. This proposal has no discretionary price stop at all, so that specific failure mode cannot recur here. If it fails it must fail on resolution outcomes, which is a cleaner verdict.

## Forge warnings (non-blocking)

These used to be refusals. They no longer block a proposal, and they are recorded here so the information survives the downgrade.

- **no_graveyard_link_warning**: no related graveyard finding. Expected for PREDICTION_MARKET, EVENT and SPORTS: the graveyard has no rows in those classes.
