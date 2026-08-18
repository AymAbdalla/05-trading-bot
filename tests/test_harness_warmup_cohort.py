"""R-005 (timeframe-aware scan start) and R-006 (confirmation-stack cohort).

Both rulings LOOSEN something, so convention 17 governs what these tests are
for. They do not assert that results improved - improvement is not evidence.
They assert the three things that make an improvement interpretable:

  1. intraday is BIT-IDENTICAL to the pre-fix harness, so any change in the
     re-swept intraday numbers is a bug and not the ruling. R-010 has since
     moved the runner's intraday SLICE GATE by one bar (100 -> 101), which is
     the single deliberate exception: it changes which series are admitted, not
     how an admitted series is scanned, and zero real series sit at the
     boundary (measured: the shortest intraday test slice is 312 bars). The
     harness-side intraday warmup is still untouched;
  2. the cohort the harness actually resolves is exactly the eight strategies
     R-006 named, by whichever route - so the ruling and the code cannot
     drift apart silently;
  3. a non-cohort strategy is bit-identical with the cohort configured and
     without it, so "the stack was turned off" cannot leak sideways.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.vectorized_harness import (  # noqa: E402
    SCAN_WINDOW, SLOW_TIMEFRAMES, SLOW_TF_MIN_SCAN_START, GATE_VERSION,
    VectorizedBacktestHarness, precompute_indicators, EXIT_CONFIGS)
from strategies.cohorts import (  # noqa: E402
    R006_COHORT, COHORT_DECLARED_ON_CLASS, COHORT_WIDER_PENDING_RULING)
from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED  # noqa: E402
from strategies.builtin.strategy_lab import STRATEGY_LAB_STRATEGIES  # noqa: E402
from strategies.builtin.strategy_lab_v2 import STRATEGY_LAB_V2_STRATEGIES  # noqa: E402
from strategies.builtin.strategy_lab_v3 import STRATEGY_LAB_V3_STRATEGIES  # noqa: E402
from strategies.builtin.strategy_lab_v4 import STRATEGY_LAB_V4_STRATEGIES  # noqa: E402
from strategies.builtin.strategy_lab_v5 import STRATEGY_LAB_V5_STRATEGIES  # noqa: E402
from backtest.data_loader import load_csv  # noqa: E402

ALL_STRATEGIES = (ENTRY_STRATEGIES_EXPANDED + STRATEGY_LAB_STRATEGIES
                  + STRATEGY_LAB_V2_STRATEGIES + STRATEGY_LAB_V3_STRATEGIES
                  + STRATEGY_LAB_V4_STRATEGIES + STRATEGY_LAB_V5_STRATEGIES)
BY_NAME = {s.name: s for s in ALL_STRATEGIES}

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'backtest', 'data')

# The pre-R-005 constant. Hardcoded on purpose: if someone changes the intraday
# warmup, these tests must fail rather than track the change.
LEGACY_MIN_IDX = 100

INTRADAY = ('5m', '15m', '1h')
SLOW = ('1d', '1wk')


# The three names the deleted `COHORT_BRIDGE_EXPANDED_PY` used to carry. Spelled
# out here rather than imported, because the constant is gone on purpose: this
# is the OLD route, and the tests below exist to show it now changes nothing.
LEGACY_BRIDGE_NAMES = ('bollinger_reversion', 'rsi_extreme', 'stoch_rsi_oversold')


def _sweep_harness(bridge_names=None):
    """The harness as the sweep configures it (run_incremental_graveyard).

    The sweep passes NO name list any more: all seven R-006 members declare
    `mean_reversion = True` on their own class, so route (2) in
    `_stack_applies` is unused and `COHORT_BRIDGE_EXPANDED_PY` is deleted.
    `bridge_names` reconstructs the pre-closure config, so the claim "the route
    change is a no-op" is measured against it rather than asserted
    (convention 22).
    """
    conf = {'volume_min_ratio': 1.2, 'location_atr_mult': 5.0, 'rsi_max_entry': 70}
    if bridge_names:
        conf['no_confirmation_stack_strategies'] = sorted(bridge_names)
    return VectorizedBacktestHarness({
        'strategy': {'confirmation': conf},
        'risk': {'notional_cap_usd': 100},
        'use_cost_model': True,
    })


@pytest.fixture(scope='module')
def spy_daily():
    """SPY daily, sliced exactly as the sweep slices it (last 20%)."""
    candles = load_csv(os.path.join(DATA_DIR, 'SPY_1d.csv'))
    if len(candles) <= 200:
        pytest.skip('SPY_1d.csv missing or too short')
    return precompute_indicators(candles[int(len(candles) * 0.8):])


@pytest.fixture(scope='module')
def spy_hourly():
    candles = load_csv(os.path.join(DATA_DIR, 'SPY_1h.csv'))
    if len(candles) <= 200:
        pytest.skip('SPY_1h.csv missing or too short')
    return precompute_indicators(candles[int(len(candles) * 0.8):])


# ===================== R-005: the scan start =====================

def test_slow_timeframes_start_at_the_floor_not_100():
    """1d and 1wk start at 50 for a strategy that declares no warmup.

    D-277 raised the floor from 25 to 50. Bars 25-49 were scannable but not
    evaluable: `_ema` seeds the pre-convergence region with closes[0], so ema50
    there is a seed and `regime_uptrend` is False by construction.
    """
    h = _sweep_harness()
    plain = BY_NAME['bollinger_reversion']       # min_bars not declared
    assert getattr(plain, 'min_bars', 0) in (0, None)
    for tf in SLOW:
        assert h._scan_start(plain, tf) == SLOW_TF_MIN_SCAN_START == 50


def test_intraday_scan_start_is_unchanged_at_100():
    """The whole intraday graveyard must stay comparable across the re-sweep."""
    h = _sweep_harness()
    for tf in INTRADAY:
        for strategy in ALL_STRATEGIES:
            assert h._scan_start(strategy, tf) == LEGACY_MIN_IDX, (
                f'{strategy.name} on {tf} moved off the legacy warmup')


def test_unknown_timeframe_keeps_the_conservative_default():
    """An unrecognised timeframe must not silently inherit the loose floor."""
    h = _sweep_harness()
    plain = BY_NAME['bollinger_reversion']
    for tf in (None, '', '4h', '1min', 'D', 'WEIRD'):
        assert h._scan_start(plain, tf) == LEGACY_MIN_IDX


def test_slow_start_respects_a_declared_min_bars():
    """max(min_bars, 25): a strategy that needs more history still gets it."""
    h = _sweep_harness()
    v4 = BY_NAME['V4_trend_reclaim']
    assert v4.min_bars == 58
    assert h._scan_start(v4, '1wk') == 58
    v5 = BY_NAME['V5_capitulation_equity']
    assert v5.min_bars == 70
    assert h._scan_start(v5, '1d') == 70


def test_slow_start_never_exceeds_the_series_guard():
    """Lowering the outer floor must not produce short-window scans.

    `scan()` self-guards on `n < min_bars`, so the contract that matters is
    that the harness never STARTS below what the strategy asked for.
    """
    h = _sweep_harness()
    for strategy in ALL_STRATEGIES:
        declared = int(getattr(strategy, 'min_bars', 0) or 0)
        for tf in SLOW:
            assert h._scan_start(strategy, tf) >= min(declared, SCAN_WINDOW) or \
                   h._scan_start(strategy, tf) >= declared


def test_scan_all_bars_and_run_strategy_agree_on_the_start(spy_daily):
    """The two must match exactly or signals are cached where nothing reads.

    This is the bug that made the first pass of R-005 inert: run_strategy
    scanned from 25 while scan_all_bars still filled from 100, so on daily
    every newly-scannable bar replayed as None.
    """
    h = _sweep_harness()
    strategy = BY_NAME['bollinger_reversion']
    start = h._scan_start(strategy, '1d')
    signals = h.scan_all_bars(strategy, spy_daily, None, timeframe='1d')
    assert len(signals) == spy_daily.n
    assert all(s is None for s in signals[:start]), 'signal cached before the start'
    assert any(s is not None for s in signals[start:LEGACY_MIN_IDX]), (
        'no signal in bars 25-99: R-005 bought nothing on this series')


def test_exit_signal_bars_uses_the_same_start(spy_daily):
    """Third starvation site. Entry side and exit side must share one rule.

    2 of the 11 exit configs are signal exits. With this left at 100 a daily
    trade entered at bar 30 could not exit on a bearish pattern until bar 100.
    """
    h = _sweep_harness()
    spy_daily.exit_bars_cache = None
    slow = h.exit_signal_bars(spy_daily, '1d')
    spy_daily.exit_bars_cache = None
    legacy = h.exit_signal_bars(spy_daily, '1h')
    spy_daily.exit_bars_cache = None
    assert not legacy[:LEGACY_MIN_IDX].any()
    assert slow[:LEGACY_MIN_IDX].any(), 'no exit pattern found in the newly-scanned region'
    assert np.array_equal(slow[LEGACY_MIN_IDX:], legacy[LEGACY_MIN_IDX:]), (
        'the shared region must be identical; only the start moved')


def test_daily_scannable_bars_actually_increase(spy_daily):
    """The measurement the ruling was made on, asserted rather than asserted about."""
    h = _sweep_harness()
    plain = BY_NAME['bollinger_reversion']
    before = max(0, spy_daily.n - LEGACY_MIN_IDX)
    after = max(0, spy_daily.n - h._scan_start(plain, '1d'))
    assert after > before
    # 50 since D-277, was 75 when the floor was 25. The bars given back are now
    # only the ones on which EMA-50 has actually converged.
    assert after - before == LEGACY_MIN_IDX - SLOW_TF_MIN_SCAN_START == 50


def test_runner_slice_gate_is_timeframe_aware_not_a_bare_100():
    """The FOURTH starvation site, in the runner rather than the harness.

    `run_incremental_graveyard` drops a whole series - all 55 strategies, all 11
    exit configs - when its last-20% test slice is too short. That gate carried
    the same hardcoded 100 R-005 removed from `_scan_start`, so it excluded 25
    weekly series from every sweep the project has run. It must now track the
    harness floor.
    """
    from backtest.run_incremental_graveyard import min_test_slice_bars
    for tf in SLOW:
        assert min_test_slice_bars(tf) == SLOW_TF_MIN_SCAN_START + 1 == 51


def test_runner_slice_gate_intraday_is_the_floor_plus_one_r010():
    """R-010. This gate used to return exactly LEGACY_MIN_IDX, held there so the
    intraday half of the re-sweep stayed bit-comparable (convention 17). That
    made it the one branch that broke this function's own contract: a slice of
    exactly 100 bars cleared the gate and then scanned from bar 100, leaving
    ZERO scannable bars - the same off-by-one RIVN exposes on the weekly side.

    Both branches are now floor + 1. The floors themselves are still different
    numbers and must stay different; this is not one constant written twice.
    """
    from backtest.run_incremental_graveyard import min_test_slice_bars
    for tf in INTRADAY:
        assert min_test_slice_bars(tf) == LEGACY_MIN_IDX + 1 == 101
    # An unrecognised label must NOT inherit the loose slow-timeframe bar,
    # exactly as `_scan_start` refuses to.
    for tf in (None, '', '4h', 'WEIRD'):
        assert min_test_slice_bars(tf) == LEGACY_MIN_IDX + 1
    # The two branches are still genuinely distinct.
    assert min_test_slice_bars('1wk') != min_test_slice_bars('1h')


def test_runner_slice_gate_admits_only_slices_with_something_to_scan():
    """The contract the R-010 fix restores, stated once for every timeframe:
    a slice that clears the gate has at least one bar the harness will scan,
    and a slice one bar shorter does not. Asserted against `_scan_start`
    itself rather than against a literal, so the gate cannot drift from the
    harness it is supposed to track (convention 23)."""
    from backtest.run_incremental_graveyard import min_test_slice_bars
    h = _sweep_harness()
    # bollinger_reversion declares no min_bars_for and sits at the floor on
    # every timeframe, so it is the strategy that scans EARLIEST - the gate
    # must admit a slice as soon as *any* strategy has a bar (convention 11).
    plain = BY_NAME['bollinger_reversion']
    for tf in INTRADAY + SLOW:
        gate = min_test_slice_bars(tf)
        floor = h._scan_start(plain, tf)
        assert gate - floor == 1, f'{tf}: gate={gate} floor={floor}'
        assert gate > floor, f'{tf}: a slice of {gate} bars must have something to scan'


def test_runner_slice_gate_still_drops_a_slice_with_nothing_to_scan():
    """Loosening it is not the same as removing it. A weekly slice of exactly
    50 bars starts scanning at bar 50 and has ZERO scannable bars, so it is
    genuinely untestable and must still be recorded NOT_TESTED (convention 11).
    RIVN is the one real series in this position."""
    from backtest.run_incremental_graveyard import min_test_slice_bars
    assert min_test_slice_bars('1wk') > SLOW_TF_MIN_SCAN_START


def test_slow_timeframe_set_is_exactly_daily_and_weekly():
    assert '1d' in SLOW_TIMEFRAMES and '1wk' in SLOW_TIMEFRAMES
    for tf in INTRADAY:
        assert tf not in SLOW_TIMEFRAMES


# ===================== R-006: the cohort =====================

def test_resolved_cohort_is_exactly_the_seven_that_survived_d276():
    """Whichever route each strategy takes, the resolved set must match the
    ruling as amended. R-006 named eight; D-276 removed
    `V3_intraday_momentum_crypto` because its thesis is momentum, not
    reversion, so seven is the current answer."""
    h = _sweep_harness()
    resolved = {s.name for s in ALL_STRATEGIES if not h._stack_applies(s)}
    assert resolved == R006_COHORT, (
        f'extra={sorted(resolved - R006_COHORT)} '
        f'missing={sorted(R006_COHORT - resolved)}')
    assert len(R006_COHORT) == 7
    assert 'V3_intraday_momentum_crypto' not in R006_COHORT


def test_the_declared_property_is_now_the_only_route():
    """All SEVEN opt out via their own class attribute. The bridge is closed.

    Was four of seven while `strategies/builtin/expanded.py` was owned by a
    concurrent session. If a future member is added by name only, this fails.
    """
    declared = {s.name for s in ALL_STRATEGIES if getattr(s, 'mean_reversion', False)}
    assert declared == R006_COHORT == COHORT_DECLARED_ON_CLASS
    for name in LEGACY_BRIDGE_NAMES:
        assert getattr(BY_NAME[name], 'mean_reversion', False) is True, (
            f'{name} still needs the deleted bridge')


def test_closing_the_bridge_resolved_the_same_seven():
    """The deletion is a no-op or it is a silent semantic change. Measure it.

    The sweep no longer supplies a name list. If the class declarations do not
    cover exactly what the list covered, the cohort quietly moved and every row
    of the next sweep is answering a different question at the same
    GATE_VERSION.
    """
    now = {s.name for s in ALL_STRATEGIES if not _sweep_harness()._stack_applies(s)}
    before = {s.name for s in ALL_STRATEGIES
              if not _sweep_harness(LEGACY_BRIDGE_NAMES)._stack_applies(s)}
    assert now == before == R006_COHORT


def test_the_sweep_supplies_no_cohort_name_list():
    """Route (2) is dead config. A list reappearing means the bridge reopened."""
    assert not _sweep_harness().no_confirmation_stack_strategies


def test_a_strategy_declaring_mean_reversion_needs_no_config():
    """The declaration alone is sufficient - that is the point of it."""
    h = _sweep_harness()     # no name list at all
    assert not h._stack_applies(BY_NAME['V5_capitulation_equity'])
    assert not h._stack_applies(BY_NAME['V2_vwap_magnet'])
    assert not h._stack_applies(BY_NAME['rsi_extreme'])


def test_both_v3_variants_face_the_stack_after_d276():
    """R-006 exempted the crypto variant; D-276 put it back under the stack.

    V3's thesis is momentum - the first half hour's return predicts the last
    half hour's return - so the confirmation stack is not suppressing the bars
    it is built to trade, which was the whole basis for the exemption.

    The equity twin is asserted alongside it because the flag was per-INSTANCE
    (the two variants share a class). A regression that restored it at class
    level would exempt both, and only checking the crypto one would miss that.
    """
    h = _sweep_harness()
    crypto = BY_NAME['V3_intraday_momentum_crypto']
    equity = [s for s in STRATEGY_LAB_V3_STRATEGIES
              if s.name == 'V3_intraday_momentum'][0]
    assert not hasattr(crypto, 'mean_reversion'), (
        'the D-276 removal is the deleted attribute, not the cohort list: '
        '_stack_applies reads the attribute first')
    assert h._stack_applies(crypto)
    assert h._stack_applies(equity)


def test_trend_strategies_still_face_the_stack():
    h = _sweep_harness()
    for name in ('V4_trend_reclaim', 'V4_52w_high_breakout', 'V4_gap_hold_proxy'):
        assert h._stack_applies(BY_NAME[name]), f'{name} lost its trend gate'


def test_pending_wider_cohort_is_declared_but_inert():
    """The parallel session's broader cohort is recorded, not applied."""
    h = _sweep_harness()
    for name in sorted(COHORT_WIDER_PENDING_RULING):
        if name in BY_NAME:
            assert h._stack_applies(BY_NAME[name]), (
                f'{name} is pending a ruling but is already exempt')


def test_non_cohort_strategy_is_bit_identical_with_and_without_the_cohort(spy_daily):
    """Turning the stack off for the cohort must not touch the other 48.

    The two arms are now "the old bridge name list" and "no list at all", which
    is also the before/after of closing the bridge.
    """
    with_cohort = _sweep_harness(LEGACY_BRIDGE_NAMES)
    without = _sweep_harness()
    strategy = BY_NAME['bullish_engulfing']
    assert with_cohort._stack_applies(strategy)
    sigs = with_cohort.scan_all_bars(strategy, spy_daily, None, timeframe='1d')
    for exit_config in ('fixed_2r', 'time_8c', 'signal_exit'):
        a = with_cohort.run_strategy(strategy, spy_daily, 'SPY', '1d', exit_config,
                                     precomputed_signals=sigs)
        b = without.run_strategy(strategy, spy_daily, 'SPY', '1d', exit_config,
                                 precomputed_signals=sigs)
        assert a.trade_count == b.trade_count
        assert a.profit_factor == b.profit_factor


def test_global_stack_off_still_overrides_everything(spy_daily):
    """apply_confirmation_stack=False is what validate_harness and the
    cross-harness referee rely on. The cohort must not weaken it."""
    h = VectorizedBacktestHarness({
        'strategy': {'confirmation': {'apply_confirmation_stack': False}}})
    for strategy in ALL_STRATEGIES:
        assert not h._stack_applies(strategy)


# ===================== the audit trail =====================

def test_every_row_records_which_arm_it_ran_under(spy_daily):
    """Without these stamps the before/after comparison cannot be made from
    the graveyard itself, only from the source at some later date."""
    h = _sweep_harness()
    cohort_member = BY_NAME['V5_capitulation_equity']
    trend = BY_NAME['V4_gap_hold_proxy']
    for strategy, expect_stack in ((cohort_member, False), (trend, True)):
        sigs = h.scan_all_bars(strategy, spy_daily, None, timeframe='1d')
        r = h.run_strategy(strategy, spy_daily, 'SPY', '1d', 'fixed_2r',
                           precomputed_signals=sigs)
        row = r.to_report()
        assert row['confirmation_stack_applied'] is expect_stack
        assert row['scan_start_idx'] == h._scan_start(strategy, '1d')


def test_gate_version_moved_with_the_semantics():
    """R-005 and R-006 both change which signals become trades. A gate-2 row
    and a gate-3 row are answers to different questions, and
    assert_gate_version_uniform is the only thing stopping them being pooled.
    """
    assert GATE_VERSION >= 3


def test_short_series_row_still_carries_the_stamps():
    """The early return for a too-short series must not emit an unstamped row."""
    h = _sweep_harness()
    ind = precompute_indicators([
        {'ts': 1_700_000_000_000 + i * 86_400_000, 'open': 100.0, 'high': 101.0,
         'low': 99.0, 'close': 100.0, 'volume': 1000.0} for i in range(10)])
    r = h.run_strategy(BY_NAME['V5_capitulation_equity'], ind, 'X', '1d', 'fixed_2r')
    row = r.to_report()
    assert row['trades'] == 0
    assert row['confirmation_stack_applied'] is False
    assert row['scan_start_idx'] == 70


def test_exit_configs_include_signal_exits():
    """Guards the premise of test_exit_signal_bars_uses_the_same_start."""
    signal_exits = [k for k, v in EXIT_CONFIGS.items() if v['type'] == 'signal']
    assert len(signal_exits) == 2


# ===================== the referee's escape hatch =====================

def test_scan_start_override_pins_every_timeframe():
    """The cross-harness referee compares three engines with three different
    warmups. It can only compare MECHANICS if all three start on one bar.
    R-005 broke that (BTC_USD went 13 trades -> 15, clearing the referee's
    count tolerance against backtesting.py's 9) until the pin was added.
    """
    from backtest.cross_harness_check import REFEREE_SCAN_START
    h = VectorizedBacktestHarness({'scan_start_override': REFEREE_SCAN_START})
    for tf in SLOW + INTRADAY + (None, 'anything'):
        for name in ('bollinger_reversion', 'V4_trend_reclaim', 'V5_capitulation_equity'):
            assert h._scan_start(BY_NAME[name], tf) == REFEREE_SCAN_START


def test_the_sweep_does_not_set_the_override():
    """A sweep that pins the start is opting out of R-005 silently."""
    assert _sweep_harness().scan_start_override is None
    assert _sweep_harness(LEGACY_BRIDGE_NAMES).scan_start_override is None
