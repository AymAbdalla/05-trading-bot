---
name: "pm_resolution_hold_no_roundtrip"
thesis: "Every losing dollar in this book so far has been paid to the round trip, not to the forecast. The proof is in the strategy card: PM_fair_value_arb_inverse takes the same signal as its parent and buys the opposite side, wins 48.1% of 154 trades which is close to a coin flip, and STILL loses $32.50. A near-coin-flip that bleeds is the signature of a cost paid on every trade regardless of direction. Fees are $0.00 across all 615 family trades, so that cost is the ASK-to-BID round trip. The participant creating the exploitable behaviour is the short-hold taker who enters at ask and exits at bid within 8 seconds: 398 of 615 family trades exited through a price stop for -$537.24, and `sell:converged`, the exit that means the thesis actually played out, fired 3 times in 615 trades for a combined +$0.15. This proposal takes the ONE structural feature of a binary that removes the round trip entirely: hold to resolution. A binary settles at exactly 1.00 or exactly 0.00 with no bid involved. You pay the spread once on entry and never again. The inefficiency I am buying is the late-window discount on a leg that is already through its strike: the market keeps quoting 0.88 for an outcome that is, with 60 seconds left and spot 40 bps through strike, worth far closer to 1.00. It persists because the marginal seller at 0.88 is a holder taking certainty over 12 cents of tail risk, and because there is no professional arb desk standing in a 5-minute BTC binary at $20 of size."
expected_edge_bps: 480
kill_condition: "Kill if net P&L per resolved position is below 0.0 cents over 200 or more resolved positions scored by forge_shadow_eval. Kill earlier on mechanism rather than P&L: if the realized win rate is below 82% at 100 or more resolved positions in forge_shadow_eval, the 88-cent entry gate is not selecting near-certain outcomes and the thesis is wrong regardless of whether the P&L has caught up yet."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "Universe: Polymarket btc, eth and sol up/down 5m and 15m windows. Gate 1, clock: only evaluate when time remaining in the window is between 30 and 120 seconds. Outside that band, skip with reason outside_terminal_band. Gate 2, direction: require spot to be through the window strike in the direction of the leg being bought, by at least 25 bps of lead_bps. Note the strike proxy noise floor is 1.0 bps per asset and the gate is abs(lead_bps) < floor -> skip, so a 25 bps requirement is 25x the floor and is deliberately far outside instrument error. Skip below that with reason lead_too_small_for_terminal_hold. Gate 3, price: the ask on the leading leg must be between 0.80 and 0.88 inclusive. Above 0.88 the edge is under the floor, see the arithmetic in the body. Below 0.80 the market disagrees with our lead reading strongly enough that we should believe the market, so skip with reason price_disagrees_with_lead. Gate 4, book: require ask depth of at least 40 shares so a 20-share order does not walk the book. Entry: single taker buy at ask, 20 shares, one position per window per asset, hard cap 3 concurrent positions across the whole strategy. Exit: HOLD TO RESOLUTION. No profit target, no time stop, no price stop. Target is 1.00 and stop is 0.00, which are the actual resolution bounds and not placeholders. 0.00 is strictly below any entry in the 0.80 to 0.88 band, satisfying convention 8. There is no intermediate sell because there is no intermediate fill on the paper adapter, and the vault is explicit that proposing one describes a fill that does not exist."
data_requirements: "Live ask and ask depth on the leg: HAVE IT, the loop reads books today and already counts insufficient_ask_depth 902 times and no_asks 3,153 times, so the field exists and is populated. Window strike via the Chainlink 60s TWAP proxy: HAVE IT, with the caveat that it is a proxy and no_spot_or_strike fires 803 times. Spot: HAVE IT. lead_bps: HAVE IT, it is the input to the existing per-asset noise floor gate and 18,940 skips are counted against it. Seconds remaining in window: HAVE IT, the loop already gates on not_final_third_of_15m, late_in_window, too_late_in_window and too_close_to_resolution, all of which are clock computations. Resolution settlement at 0.00 or 1.00: HAVE IT, every corridor and fair-value loss already settled that way. Maker fill simulation: DO NOT NEED IT. That is the whole point. This strategy is deliberately designed to be testable on the paper adapter as it exists today, unlike every 'use limit orders' proposal in the vault which is blocked on maker_fill_not_simulated at 2,534 skips."
markets: "Polymarket btc-updown-5m, btc-updown-15m, eth-updown-5m, eth-updown-15m, sol-updown-5m, sol-updown-15m"
kind: edge_hypothesis
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

## The arithmetic, shown

Entry at the top of my allowed band, 88 cents. Payout on a correct resolution is 100 cents. Payout on an incorrect one is 0.

At an assumed true win probability p, expected value per share is:

    EV = p * 100 - 88

Gross edge in bps is EV divided by the premium paid, times 10,000:

    edge_bps = (p * 100 - 88) / 88 * 10,000

At p = 0.92: (92 - 88) / 88 = 0.04545 -> **454 bps**
At p = 0.93: (93 - 88) / 88 = 0.05682 -> **568 bps**
At p = 0.90: (90 - 88) / 88 = 0.02273 -> **227 bps**, one tick above the floor
At p = 0.89: (89 - 88) / 88 = 0.01136 -> **114 bps**, dead on arrival

I am claiming **480 bps**, which sits between p = 0.922 and p = 0.923. That is my honest estimate and it is a hypothesis, not a measurement.

At the bottom of the band, 80 cents, the same p = 0.922 gives (92.2 - 80) / 80 = 0.1525, or 1,525 bps. So 480 bps is the estimate at the WORST allowed entry, which is the conservative way to state it.

The break-even win rate at an 88-cent entry is exactly 88%. That is why my secondary kill condition is set at 82% and not at 88%: below 82% the strategy is not merely unprofitable, the selection mechanism is demonstrably not doing what it claims.

## Why 0.88 is the ceiling

Rule 2 sets the prediction-market floor at 200 bps, which the brief notes is one cent on a 50-cent contract. Solving for the entry price a at which one cent of edge clears 200 bps:

    100 / a - 1 >= 0.02 at p = 1.0 gives a <= 98

but at my realistic p = 0.922 the ceiling is much lower. Requiring edge_bps >= 200:

    (92.2 - a) / a >= 0.02  ->  a <= 90.4

So 90 cents is the mathematical ceiling and I set the gate at 88 to leave room for one cent of slippage. Anything quoted above 0.88 is refused not because I dislike it but because it cannot clear the floor.

## Why this is testable TODAY

The vault's standing recommendation for the spread problem is maker orders, repeated in `2026-08-18-fair-value-arb-spread-problem.md` under 'What MIGHT work' item 1, in the strategy card under 'What would revive it' item 1, and in the cycle summary under 'What to try next' item 2. All three are blocked on the same thing: `maker_fill_not_simulated`, 2,534 skips, class SIM_LIMIT. The paper adapter cannot price a maker fill.

This proposal routes around that entirely. It is a taker order on entry and a settlement on exit. Both are things the adapter already does 784 times over. No adapter work is required and no proposal is spent waiting on infrastructure.

## What would change my mind

The single measurement that decides this: log the realized resolution outcome, bucketed by entry ask, for every window where lead_bps exceeds 25 with under 120 seconds remaining. If the realized frequency in the 0.80 to 0.88 bucket comes in under 88%, the market is priced correctly and there is no discount. That is a read of the shadow log over enough windows, not new research, and it should be run BEFORE this strategy is allowed real size, per convention 4.

I would also change my mind if `implied_vol_sign_inconsistent` turns out to concentrate in exactly the high-lead terminal-window rows I am selecting. 497 of those skips exist and I have not checked their distribution. If the strike proxy is least reliable precisely where I am most confident, the strategy is inverted and dangerous.

## On sizing, deliberately small

20 shares at 88 cents is $17.60 at risk per position, against a $30.00 daily loss breaker that has already fired once at $30.08. Two losses trip it. I am proposing 3 concurrent positions maximum rather than the book-wide 5, because this strategy's loss shape is the most concentrated in the book: 7 wins to cover 1 loss. That asymmetry is the honest cost of removing the round trip, and it should be sized for, not hidden.

## Convention check

- Convention 1: not durable until `backtest/validate_harness.py` exits 0.
- Convention 5: 480 bps stated, above the 200 bps PREDICTION_MARKET floor, arithmetic shown above.
- Convention 7: no claim here rests on any existing sample. The 200-position kill condition is set above the 30-trade bar on purpose, because the 7-to-1 payoff shape needs a large n before it means anything.
- Convention 8: stop 0.00, strictly below an 0.80 to 0.88 entry.
- Convention 15: 480 bps is an ESTIMATE written before the run and must be corrected after it.
- Convention 27: I read the direction of the noise floor gate. It is abs(lead_bps) < floor -> skip, floor is 1.0 bps per asset, and my 25 bps requirement is a TIGHTER constraint that admits fewer windows, not a loosening.

## Why this might fail

The strongest argument against me is that the 88-cent ask is not a discount, it is a correct price, and I am selling a 12-cent option for free. Polymarket 5m BTC windows are quoted by people watching the same spot tape I am. If the market says 0.88 with 60 seconds left and a 25 bps lead, the honest prior is that the true probability IS around 0.88, and the residual 12% covers exactly the reversals that a 25 bps lead cannot survive. My whole edge assumes the true probability is 92% or better while the market quotes 88, and I have no measurement supporting that gap. The bps arithmetic below is a hypothesis about a number I have not observed. Second failure: the payoff is brutally asymmetric in the wrong direction for a small sample. I win 12 cents on 88 cents of risk. One loss wipes out roughly 7 wins. At 20 shares that is +$2.40 per win against -$17.60 per loss. A run of two early losses buries this strategy at n=15 and it would take 15 straight wins to recover, so the strategy will look catastrophic long before it can look right, and there is a real risk it gets killed by the daily loss breaker, which already fired at $30.08 against a $30.00 limit on the fair-value parent. Two losses is $35.20 and trips it. Third: the strike is a PROXY, not the published Chainlink TWAP, and the disagreement is measured. `implied_vol_sign_inconsistent` fires 497 times specifically because the book and the strike proxy disagree on which side is ahead. If the proxy is wrong about direction on even 3% of my entries, those are near-certain LOSSES rather than near-certain wins, and at a 7-to-1 loss ratio 3% wrong direction costs more than the entire edge. Fourth: it is the mirror image of a strategy the vault already buried. PM_corridor_collector bought cheap tails at 0.10 to 0.31 and every single one resolved to 0.00. I am buying the other side of exactly that trade. If cheap tails always die then expensive favourites always win and I am right, but if the four corridor_collector losses were just a 4-trade sample of noise, then so is my inverse reading of them.

## What past failure this addresses

This is a direct response to hypothesis_graph id 113 PM_fair_value_arb (failure_mode stop_too_tight, 27.5% WR on 120 closed, net -91.91), id 114 PM_fair_value_arb_hft (failure_mode stop_too_tight, 23.2% WR on 69, net -56.65) and id 112 PM_dip_arb (failure_mode stop_too_tight, 21.2% WR on 33, net -33.06). All three share failure_mode stop_too_tight, and in all three the vault records that 70.8%, 76.8% and 78.8% of closes respectively were stop-like exits. What is DIFFERENT here is not the signal and not the stop level: it is that this strategy has NO INTERMEDIATE EXIT AT ALL. It cannot stop out because it never sells before resolution. That removes the stop_too_tight failure mode by construction rather than by re-tuning it, which is what `2026-08-18-fair-value-arb-spread-problem.md` explicitly forbids under 'Do NOT tighten the stop loss'. It also obeys that note's 'Do NOT propose another spread-based strategy with market orders on both sides': this pays the spread on ONE side, on entry only, and the strategy card confirms the diagnostic that makes this the right fix, namely that inverse at 48.1% WR still lost money so the cost and not the model is the problem. It obeys `2026-08-18-cycle-001-day-1-lessons.md` item 1 of 'What NOT to try next' for the same reason: this is not market-order-on-both-sides. It obeys item 2, do not invert a losing strategy, because this is not the fair-value signal inverted, it is a different signal, namely terminal-window lead persistence. And it obeys item 6 and `2026-08-18-corridor-pair-works.md` item 6, do not propose a stop other than 0.00 on a binary held to resolution.

## Forge warnings (non-blocking)

These used to be refusals. They no longer block a proposal, and they are recorded here so the information survives the downgrade.

- **no_graveyard_link_warning**: no related graveyard finding. Expected for PREDICTION_MARKET, EVENT and SPORTS: the graveyard has no rows in those classes.
