# Shadow Stats Snapshot — PRE market-spaces restart

**Captured:** 2026-08-18 16:28 ET (EDT), before restarting the shadow loop onto the market-spaces source (commit ea30111).
**Purpose:** baseline for comparison. The loop was running pre-spaces source (PID 90192) until this restart.

## Account
- Closed positions: 1,304 | net PnL: **-$695.20**
- Open positions: 28
- Last equity: **$888.07** (16:28)
- Blowups: 0

## Per-strategy closed positions (strategy_id: trades / pnl / wins)

| Strategy | Trades | PnL | Wins |
|---|---|---|---|
| PM_fair_value_arb | 395 | -207.00 | 150 |
| PM_fair_value_arb_hft | 293 | -174.72 | 89 |
| PM_fair_value_arb_inverse | 228 | -43.42 | 116 |
| PM_dip_arb | 175 | -67.71 | 50 |
| PM_temporal_arbitrage | 56 | +0.04 | 11 |
| PM_grid_hedge | 44 | -101.53 | 11 |
| PM_fair_value_arb_wide | 39 | -5.38 | 21 |
| PM_box_builder | 23 | -21.30 | 7 |
| PM_mid_price_continuation | 13 | -24.24 | 4 |
| PM_corridor_pair | 11 | -11.30 | 5 |
| PM_streak_snapper | 9 | -23.44 | 3 |
| PM_spread_harvest_taker | 6 | -18.24 | 1 |
| PM_corridor_collector | 4 | -3.70 | 0 |
| PM_small_liq_continuation | 3 | +3.20 | 1 |
| PM_fair_value_arb_patient | 3 | -0.41 | 2 |
| PM_corridor_pair_live | 2 | +3.95 | 2 |

## Note
Strategy registry was 19 at this snapshot (crypto-routed). After restart: registry 20, four market spaces live (crypto/weather/event/sports/political), all strategies will be re-declared per Aym's ruling (see from-raven task for D-316).
