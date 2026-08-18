"""Strategy cohorts: which strategies get which harness gate (D-270).

The confirmation stack (`close > rising EMA50`, `rsi <= rsi_max_entry`,
`volume_ratio >= volume_min_ratio`) is applied inside
`VectorizedBacktestHarness.run_strategy`, AFTER `scan()`. Until D-270 the main
sweep never set `apply_confirmation_stack`, so it defaulted True and every
strategy - including the ones whose entire thesis is buying a down move - had
to additionally satisfy "price is above a rising 50-period EMA".

Measured effect (docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md): on
capitulation days `regime_uptrend` is true 7.77% of the time against a 49.71%
unconditional baseline. A 6.4x suppression of exactly the bars a fade strategy
is built to trade. Convention 11: those strategies were not tested and found
wanting, they were not tested.

D-270 (Raven ruling): set `apply_confirmation_stack=False` for the
mean-reversion cohort. Trend-following strategies keep the stack.

MEMBERSHIP CRITERION, applied literally as Raven stated it: a strategy is in
the cohort when its entry is conditioned on a PRECEDING ADVERSE PRICE MOVE that
the thesis expects to revert. "Price is falling and expected to revert."
Membership is decided by thesis, never by whether the strategy happened to fire
in the pre-fix sweep - a firing-based carve-out would mean "we only correct the
measurement where it was most broken", which is itself a selection effect.

Every graveyard row is stamped with `confirmation_stack_applied` so the two
arms can never be pooled by accident, and so this list is auditable after the
fact rather than only in this docstring.
"""

# Strategies whose entry buys a preceding down move expecting reversion.
# The comment on each line is the clause that puts it here.
MEAN_REVERSION_COHORT = frozenset({
    # --- named explicitly in D-270 -------------------------------------
    'V2_vwap_magnet',              # price stretched BELOW session VWAP, bought toward it
    'V2_vwap_magnet_sessionatr',   # same thesis, session-ATR scale
    'V5_capitulation_equity',      # buys the capitulation day itself

    # --- lab strategies whose docstring thesis is fade / absorb --------
    'V5_forced_flow_crypto',       # buys the final bar of a liquidation cascade
    'V3_vacuum_refill',            # buys the first green candle after a climax flush
    'V2_liquidation_echo',         # buys a forced cascade that failed to reach the pool
    'V2_wick_autopsy',             # repeated lower-wick probes absorbed, then expansion
    'V2_second_break',             # break BELOW the OR low that closes back inside, bought
    'V2_expiry_pin',               # price sitting just below the pin strike, bought toward it
    'C2',                          # docstring: "Fade abnormally large ... weekend moves"
    'D2',                          # docstring: "Fade the first 5-min bar that re-enters"
    'D1',                          # docstring: "Buy dips below session VWAP"

    # --- classical mean-reversion indicators in expanded.py ------------
    'bollinger_reversion',         # buys the lower band touch
    'rsi_extreme',                 # buys oversold RSI
    'stoch_rsi_oversold',          # buys oversold Stochastic RSI
    'grid_1.0atr',                 # buys the drop to the grid level below
    'grid_2.0atr',                 # buys the drop to the grid level below
    'ema_pullback',                # buys the pullback INTO the EMA, i.e. the adverse leg
})

# Strategies that meet the letter of the criterion (a bullish reversal pattern
# fires after a down move and expects it to revert) but are NOT in the cohort
# for this sweep, because D-270's evidence base is the non-firing nine and
# these all fired at scale under the stack. Held out deliberately so the
# before/after comparison isolates the diagnosed defect rather than re-testing
# two thirds of the library at once. Raven should rule on whether they belong;
# see docs/handoffs/2026-08-17-resweep-results.md.
CANDIDATE_COHORT_PENDING_RULING = frozenset({
    'bullish_engulfing', 'hammer', 'inverted_hammer', 'morning_star',
    'piercing_line', 'three_inside_up', 'tweezer_bottom', 'bullish_harami',
    'bullish_abandoned_baby', 'dragonfly_doji',
})

# Explicitly NOT in the cohort even though the diagnosis suggested "drop stack"
# as its fix: the thesis is momentum (the first half hour predicts the last
# half hour), not reversion. D-270 says trend-following keeps the stack, and
# the ruling's criterion outranks the diagnosis's suggestion.
TREND_FOLLOWING_KEEPS_STACK = frozenset({
    'V3_intraday_momentum', 'V3_intraday_momentum_crypto',
    'V4_trend_reclaim', 'V4_52w_high_breakout', 'V4_gap_hold_proxy',
    'momentum_continuation', 'breakout_20', 'breakout_50',
    'V2_0dte_amplifier',
})


def _assert_disjoint():
    """A strategy in two cohorts is a contradiction, not a preference."""
    overlap = (MEAN_REVERSION_COHORT & TREND_FOLLOWING_KEEPS_STACK) | \
              (MEAN_REVERSION_COHORT & CANDIDATE_COHORT_PENDING_RULING) | \
              (TREND_FOLLOWING_KEEPS_STACK & CANDIDATE_COHORT_PENDING_RULING)
    if overlap:
        raise AssertionError(f'strategy in more than one cohort: {sorted(overlap)}')


_assert_disjoint()
