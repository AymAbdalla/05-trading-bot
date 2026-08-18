"""Strategy cohorts: which strategies the harness gates with the confirmation
stack, and which it does not (R-006).

The confirmation stack (`close > rising EMA50`, `rsi <= rsi_max_entry`,
`volume_ratio >= volume_min_ratio`) is applied inside
`VectorizedBacktestHarness.run_strategy`, AFTER `scan()`. Until R-006 the main
sweep never set `apply_confirmation_stack`, so it defaulted True and every
strategy - including the ones whose entire thesis is buying a down move - had
to additionally satisfy "price is above a rising 50-period EMA".

Measured effect (docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md): on
capitulation days `regime_uptrend` is true 7.77% of the time against a 49.71%
unconditional baseline. A 6.4x suppression of exactly the bars a fade strategy
is built to trade. Convention 11: those strategies were not tested and found
wanting, they were not tested.

## The mechanism is a declared property, and now it is the ONLY route

`vectorized_harness._stack_applies` checks `strategy.mean_reversion` FIRST and
only then falls back to a runner-supplied name list. A declaration on the class
travels with the definition; a list in another module goes stale the first time
somebody adds a strategy and forgets. Convention 17 - a hardcoded list is an
assumption with an expiry date.

`COHORT_BRIDGE_EXPANDED_PY` used to carry three of the seven by name, because
`strategies/builtin/expanded.py` was owned by a concurrent session when R-006
was applied and editing a file another session is mid-write on loses one of the
two edits (convention 21). That bridge is now CLOSED: RsiExtreme,
BollingerReversion and StochRsiOversold each declare `mean_reversion = True` on
the class, the constant is deleted, and the runner no longer passes a name list
at all. All seven members take route (1).

This is a semantic no-op by construction - the same seven strategies resolve
out of the stack, by a different route - so no re-sweep is owed for it and
GATE_VERSION does not move. `tests/test_harness_warmup_cohort.py` asserts the
RESOLVED set, not the route, which is why it passes across the change.

Every graveyard row is stamped `confirmation_stack_applied`, so which arm a
row ran under is recoverable from the artifact rather than only from this file.
"""

# ---------------------------------------------------------------------------
# R-006's cohort as amended by D-276. Seven strategies.
# ---------------------------------------------------------------------------
# Kept as one authoritative tuple so the "declared on the class" members and
# the "bridged by name" members can be checked against it, and any drift
# between the ruling and the implementation raises instead of passing quietly.
#
# D-276 (Raven, 2026-08-17): `V3_intraday_momentum_crypto` was REMOVED. R-006
# named it; the objection recorded below in CONTESTED_MEMBERSHIP was upheld.
# V3's thesis is momentum - the first half hour's return predicts the last half
# hour's return - not reversion, so exempting it from the confirmation stack
# exempted the wrong strategy. It is back under the stack like every other
# non-cohort strategy.
R006_COHORT = frozenset({
    'V2_vwap_magnet',
    'V2_vwap_magnet_sessionatr',
    'V5_capitulation_equity',
    'V5_forced_flow_crypto',
    'rsi_extreme',
    'bollinger_reversion',
    'stoch_rsi_oversold',
})

# Every member now declares `mean_reversion = True` on its own class, so this
# is the whole cohort by the intended route. Listed for the cross-check in the
# tests only; the harness never reads this set. The bridge that used to sit
# here (`COHORT_BRIDGE_EXPANDED_PY`) is deleted - see the module docstring.
COHORT_DECLARED_ON_CLASS = R006_COHORT


# ---------------------------------------------------------------------------
# Recorded disagreements. Neither set is active. Both need a Raven ruling.
# ---------------------------------------------------------------------------

# A parallel session (PID 15357, see docs/handoffs/2026-08-17-resweep-BLOCKED-
# two-sessions.md) worked the same ruling under the id D-270 and derived a
# BROADER cohort from the criterion "entry is conditioned on a preceding
# adverse price move the thesis expects to revert". Its extra members are kept
# here rather than thrown away, because the reasoning is sound and only the
# scope differs. Not active: R-006 named eight, D-276 cut that to seven, and
# these eleven have never been ruled on either way.
COHORT_WIDER_PENDING_RULING = frozenset({
    'V3_vacuum_refill',      # buys the first green candle after a climax flush
    'V2_liquidation_echo',   # buys a forced cascade that failed to reach the pool
    'V2_wick_autopsy',       # repeated lower-wick probes absorbed, then expansion
    'V2_second_break',       # break BELOW the OR low that closes back inside
    'V2_expiry_pin',         # price just below the pin strike, bought toward it
    'C2',                    # docstring: "Fade abnormally large weekend moves"
    'D2',                    # docstring: "Fade the first 5-min bar that re-enters"
    'D1',                    # docstring: "Buy dips below session VWAP"
    'grid_1.0atr',           # buys the drop to the grid level below
    'grid_2.0atr',           # buys the drop to the grid level below
    'ema_pullback',          # buys the pullback INTO the EMA, the adverse leg
})

# RESOLVED by D-276 (Raven, 2026-08-17). This set held the one place where
# R-006 and the parallel session's D-270 flatly contradicted each other: R-006
# named `V3_intraday_momentum_crypto` in the cohort, D-270's own criterion
# excluded it because V3's thesis is momentum, not reversion.
#
# The contest was recorded rather than silently decided, and Raven ruled for
# exclusion. V3 is out of R006_COHORT and the `mean_reversion = True` line is
# gone from `strategies/builtin/strategy_lab_v3.py.__init__`. Nothing is
# contested now, so this is empty - kept (not deleted) because the assertion
# below is the thing that stops a future contested member from being added to
# one place and forgotten in the other.
CONTESTED_MEMBERSHIP: frozenset = frozenset()


def _assert_consistent():
    """The ruling and the implementation must not drift apart silently."""
    overlap = R006_COHORT & COHORT_WIDER_PENDING_RULING
    if overlap:
        raise AssertionError(
            f'strategy is both active and pending-ruling: {sorted(overlap)}')
    if not CONTESTED_MEMBERSHIP <= R006_COHORT:
        raise AssertionError('contested member is not actually in the active cohort')


_assert_consistent()
