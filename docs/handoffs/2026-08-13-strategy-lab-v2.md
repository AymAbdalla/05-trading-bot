# Handoff: Strategy Lab v2 implementation

Date: 2026-08-13
Built by: Claude Code
Source spec: `references/strategy-lab-v2.md`

## What was built

- `strategies/builtin/strategy_lab_v2.py` (new). Exports
  `STRATEGY_LAB_V2_STRATEGIES`, a list of 8 instantiated entry strategies.
- `tests/test_strategy_lab_v2.py` (new). 110 tests, all green.

The 8 strategies, all long only, all pure OHLCV plus timestamps:

| Name | Spec | Home timeframe |
|---|---|---|
| V2_wick_autopsy | 1.2 Wick Autopsy | 5m / 15m |
| V2_round_number_decay | 1.3 Round-Number Defense Decay | any |
| V2_liquidation_echo | 1.5 Liquidation Echo | 5m / 15m |
| V2_second_break | 2.1 Second-Break Verdict | 5m / 15m |
| V2_volume_desert | 2.5 Volume Desert Breakout | 5m / 15m / 1h |
| V2_vwap_magnet | 2.6 VWAP Magnet Close | 5m / 15m |
| V2_expiry_pin | 4.1 Expiry Pin Drift | intraday |
| V2_0dte_amplifier | 4.2 0DTE Afternoon Amplifier | 5m |

## Not implemented and why

1.1 Funding Shadow (scan gets no pair identity, so the funding CSVs cannot be
joined), 1.4 TradFi Handoff (needs a per-pair backtested sign table), 2.2 Gap
Context (needs premarket volume percentiles per ticker), 2.3 Sector Orphan
(needs the sector ETF series alongside the stock), 2.4 Ghost Levels (needs a
split history table), 2.7 Halt Resumption (needs the surprise scanner lane),
3.1 / 3.2 / 3.3 (futures sessions, settlements, macro calendar), 5.1 / 5.2
(cross asset by construction).

## Deviations from the spec

Each is also written into the relevant class docstring.

1. **Session scale ATR** for V2_second_break (opening range height) and
   V2_volume_desert (1 ATR stop). The spec quotes these against a session
   sized ATR, but the harness hands strategies the ATR of the bar it is
   scanning. Measured: the opening range is 2.4x to 6.4x the 5m ATR, so the
   literal 0.5 to 2.0 band passed 0% of AAPL days and 1.2% of SPY days.
   `_session_atr` rescales by sqrt(bars per session). Calibration check
   against real daily ATR: the proxy runs about 1.8x smaller than the true
   14 day ATR, which is documented in the helper.
2. **V2_vwap_magnet deliberately does NOT use session scale.** At session
   scale the trigger fires on 1.5% of ticker-days; at bar scale it fires on
   ~35%, which is what the spec's own "30-40% of ticker-days" estimate
   describes. The consequence is a tight target (median 0.14% of price on
   SPY, 0.32% on AAPL) against a ~0.3% round trip cost. The spec already
   flags this. Expect the fee-to-edge gate to veto most days and judge the
   strategy on gate-passing days only.
3. **Round level spacing is derived from price magnitude**, not from the pair,
   because scan() receives no ticker. Ladder reproduces BTC $1000, ETH $100
   and the equity rules exactly. SOL near $150 gets $5 rather than $10, which
   is a superset: no spec level is lost, extra levels are added.
4. **V2_volume_desert omits the SPY-flat filter** entirely. It needs a second
   instrument's series that the harness does not pass. Idiosyncratic flow is
   therefore not isolated from index level moves. This is the largest single
   omission in the set.
5. **Lunch and liquidation baselines are short.** The 260 bar scan window on
   5m data covers only about 1.35 sessions, so the lunch volume baseline is
   1 to 3 prior sessions and the liquidation volume baseline is capped at the
   bars available rather than a true 7 days.
6. **Long only everywhere.** 2.1 takes only failed break-downs, 4.2 takes only
   upside morning range breaks, 2.5 only up moves.
7. **V2_vwap_magnet triggers on the first bar inside 15:30-15:45 ET** rather
   than an exact 15:30 stamp, so it survives different bar sizes.
8. **V2_wick_autopsy counts the 60% top-third condition over non degenerate
   candles only.** Flat extended hours bars have no "top third" and were
   penalising the fingerprint for a data artifact.

## Verified on real data

Sliding 260 bar window, same shape as `VectorizedBacktestHarness.scan_all_bars`.
Signal counts:

| Strategy | AAPL_5m | SPY_5m | BTC_1d | AAPL_1h | BTC_15m | ETH_1h | total |
|---|---|---|---|---|---|---|---|
| V2_wick_autopsy | 22 | 17 | 0 | 0 | 0 | 2 | 41 |
| V2_round_number_decay | 1071 | 964 | 165 | 320 | 161 | 760 | 3441 |
| V2_liquidation_echo | 7 | 30 | 0 | 1 | 5 | 4 | 47 |
| V2_second_break | 156 | 69 | 0 | 0 | 4 | 0 | 229 |
| V2_volume_desert | 293 | 335 | 0 | 21 | 20 | 37 | 706 |
| V2_vwap_magnet | 196 | 171 | 0 | 0 | 13 | 0 | 380 |
| V2_expiry_pin | 940 | 1315 | 0 | 72 | 67 | 52 | 2446 |
| V2_0dte_amplifier | 16 | 14 | 0 | 0 | 0 | 0 | 30 |

Every emitted signal had `stop < entry` and, where a target was set,
`target > entry`. No strategy is dead.

## Questionable or incomplete

- **V2_wick_autopsy is alive but rare** (about 1 signal per 4,000 bars at the
  spec's literal 60% threshold). Per file it will land under the harness's
  20 trade gate. It needs pooled analysis across the universe or it will be
  reported as untestable rather than tested. `MIN_TOP_THIRD_FRAC` is exposed
  as a class attribute if Raven wants to sweep it.
- **V2_liquidation_echo is also thin** (47 signals). The spec calls it bursty,
  so this may be correct, but the same pooling caveat applies.
- **V2_round_number_decay and V2_expiry_pin are the high frequency pair.**
  Round number fires on roughly 1% to 4% of bars. Worth checking that the
  fee-to-edge gate and the max-position rules are actually binding on them
  before reading any edge into the results.
- **Equity session strategies fire on crypto files** because
  `_in_xnys_session` is true for crypto bars during NYSE hours. Not a bug,
  but crypto results for 2.1 / 2.5 / 2.6 / 4.1 / 4.2 are not what the spec
  intended and should probably be excluded at the sweep level.
- **The strategies are not registered anywhere yet.** Nothing imports
  `STRATEGY_LAB_V2_STRATEGIES`. Wiring it into the graveyard sweep is a
  deliberate next step, not an oversight.
- The spec's integration note 1 (time matched random twins for time anchored
  strategies) is NOT implemented. Six of these eight are time anchored, so
  until silent assertion #15 exists their twin comparisons will credit the
  clock rather than the signal.

## Next steps for Raven

1. Decide whether to wire `STRATEGY_LAB_V2_STRATEGIES` into the graveyard
   sweep now or after the time matched random twin assertion lands.
2. Rule on deviation 2 (V2_vwap_magnet bar scale vs session scale). It is the
   one judgement call in the set where the spec's stated frequency and the
   spec's economics disagree.
3. Decide whether the SPY-flat filter for 2.5 is worth plumbing a second
   instrument into the scan payload, or whether the strategy ships without it.
