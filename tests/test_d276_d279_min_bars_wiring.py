"""D-276 through D-279, plus the min_bars_for() wiring that closes the live
convention 11 violation D-274 left open.

Each ruling gets a test that fails if the ruling is reverted, and - where the
ruling is a threshold - a test that the threshold is actually READ by the code
path it is supposed to govern. Convention 22: a claim in a docstring is not a
wiring test.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.vectorized_harness import (  # noqa: E402
    MAX_STRATEGY_WINDOW, SCAN_WINDOW, SLOW_TF_MIN_SCAN_START,
    SLOW_TIMEFRAMES, WEEKLY_TIMEFRAMES, VectorizedBacktestHarness,
    precompute_indicators, resolve_min_bars, timeframe_seconds)
from strategies.builtin.strategy_lab import WeekendVacuumReversion  # noqa: E402
from strategies.builtin.strategy_lab_v3 import STRATEGY_LAB_V3_STRATEGIES  # noqa: E402
from strategies.builtin.strategy_lab_v4 import STRATEGY_LAB_V4_STRATEGIES  # noqa: E402
from strategies.cohorts import R006_COHORT, CONTESTED_MEMBERSHIP  # noqa: E402
from indicators.patterns_all import rising_three_methods  # noqa: E402
from indicators.atr import latest_atr  # noqa: E402
from indicators.patterns import is_upswing  # noqa: E402


def _harness(**conf):
    return VectorizedBacktestHarness({'strategy': {'confirmation': conf}})


def _by_name(strategies, name):
    return [s for s in strategies if s.name == name][0]


# ===================== D-276: V3 out of the cohort =====================

def test_d276_v3_crypto_is_out_of_the_cohort_at_both_sites():
    """Two sites, and only one of them changes behaviour.

    `_stack_applies` reads `strategy.mean_reversion` BEFORE it consults the
    name list, so removing V3 from R006_COHORT without deleting the attribute
    would have been completely inert. That is the failure mode this asserts
    against (convention 23).
    """
    crypto = _by_name(STRATEGY_LAB_V3_STRATEGIES, 'V3_intraday_momentum_crypto')
    assert 'V3_intraday_momentum_crypto' not in R006_COHORT
    assert not hasattr(crypto, 'mean_reversion')
    assert _harness()._stack_applies(crypto)


def test_d276_closed_the_contested_membership_set():
    """The contest was recorded rather than silently decided, and then ruled
    on. An empty set here with V3 still in the cohort would mean the objection
    was dropped instead of resolved."""
    assert CONTESTED_MEMBERSHIP == frozenset()
    assert len(R006_COHORT) == 7


# ===================== D-277: the EMA convergence floor =====================

def test_d277_floor_is_50_not_25():
    assert SLOW_TF_MIN_SCAN_START == 50


def test_d277_floor_is_where_ema50_has_converged():
    """The ruling's actual justification, asserted rather than asserted about.

    `_ema` fills the pre-convergence region with closes[0] verbatim. So on a
    monotonically RISING series - where every honest reading of "is the regime
    up" is yes - ema50 is a flat line at the first close, "ema50 rising" is
    false because a constant does not rise, and `regime_uptrend` is therefore
    False by construction rather than by measurement.

    That is what a scan starting at 25 was buying: 25 bars on which one leg of
    the confirmation stack could never be true. Not "the regime was down" - the
    number had not been computed yet.
    """
    candles = [{'ts': (1700000000 + i * 86400) * 1000,
                'open': 100.0 + i, 'high': 101.0 + i, 'low': 99.0 + i,
                'close': 100.0 + i, 'volume': 1000.0} for i in range(400)]
    ind = precompute_indicators(candles)

    seed = float(ind.closes[0])
    seeded = [i for i in range(400) if float(ind.ema50[i]) == seed]
    assert max(seeded) >= SLOW_TF_MIN_SCAN_START - 2, (
        f'ema50 leaves the closes[0] seed at bar {max(seeded) + 1}; the D-277 '
        'premise that bars below ~49 are seeded no longer holds')

    # The old floor sat inside the seeded region; the new one does not.
    assert 25 in seeded, 'bar 25 is no longer seeded - re-derive D-277'
    assert SLOW_TF_MIN_SCAN_START not in seeded, (
        f'bar {SLOW_TF_MIN_SCAN_START} is still seeded; the floor is too low')

    # The harm, stated as the harness sees it.
    assert not ind.regime_uptrend[:max(seeded) + 1].any(), (
        'regime_uptrend is true somewhere in the seeded region')
    assert ind.regime_uptrend[SLOW_TF_MIN_SCAN_START:100].all(), (
        'a monotonically rising series is not read as an uptrend at the floor')


def test_d277_only_moves_slow_timeframes():
    """Intraday must stay bit-identical or the whole graveyard is repartitioned."""
    h = _harness()
    plain = type('Plain', (), {'name': 'plain'})()
    for tf in ('5m', '15m', '1h'):
        assert h._scan_start(plain, tf) == min(SCAN_WINDOW, 100) == 100
    for tf in SLOW_TIMEFRAMES:
        assert h._scan_start(plain, tf) == 50


# ===================== D-278: rising_three_methods =====================

def _rtm_raw(pullback_range):
    """A textbook rising three methods, with the three pullback candles built
    to an ABSOLUTE range. The prefix trends up so `is_upswing` (which needs a
    2-ATR rise off the 20-bar low) is satisfied for reasons unrelated to the
    threshold under test."""
    opens, highs, lows, closes = [], [], [], []
    price = 100.0
    for _ in range(20):                      # rising quiet prefix, TR == 1.0
        opens.append(price); closes.append(price)
        highs.append(price + 0.5); lows.append(price - 0.5)
        price += 0.5
    o, c = price, price + 2.0                # c1: the big green leg
    opens.append(o); closes.append(c)
    highs.append(c + 0.3); lows.append(o - 0.3)
    hi_cap, lo_cap = c + 0.3, o - 0.3
    top = c
    for _ in range(3):                       # three reds, inside c1's range
        ro, rc = top, top - pullback_range * 0.5
        opens.append(ro); closes.append(rc)
        highs.append(min(ro + pullback_range * 0.25, hi_cap - 0.01))
        lows.append(max(rc - pullback_range * 0.25, lo_cap + 0.01))
        top = rc
    opens.append(top); closes.append(c + 0.5)   # c6: continuation
    highs.append(c + 0.8); lows.append(top - 0.1)
    return opens, highs, lows, closes


def _rtm_candles(small_red_range_atr):
    """Same, but with the pullback range expressed as a multiple of the ATR the
    pattern function will actually compute.

    Solved by fixed point rather than assumed: the pullback candles are
    themselves inside the 14-bar ATR window, so their size moves the number
    they are compared against. Two passes are enough to converge here, and the
    result is asserted rather than trusted.
    """
    rng = small_red_range_atr                  # ATR starts near 1.0
    for _ in range(6):
        o, h, l, c = _rtm_raw(rng)
        rng = small_red_range_atr * latest_atr(h, l, c, 14)
    o, h, l, c = _rtm_raw(rng)
    atr = latest_atr(h, l, c, 14)
    actual = (h[-4] - l[-4]) / atr
    assert abs(actual - small_red_range_atr) < 0.02, (
        f'fixture did not converge: wanted {small_red_range_atr} ATR, built '
        f'{actual:.3f} ATR')
    return o, h, l, c


def test_d278_threshold_is_1_0_atr_not_0_7():
    """A pullback candle sized between 0.7 and 1.0 ATR is the entire population
    the ruling unblocked: rejected before, accepted now.

    0.85 is chosen to sit strictly inside that band, so this test fails if the
    threshold is reverted to 0.7 AND fails if it is pushed past 1.0 for the
    wrong reason.
    """
    o, h, l, c = _rtm_candles(small_red_range_atr=0.85)
    assert rising_three_methods(o, h, l, c)['found'], (
        'a 0.85-ATR pullback is still rejected; D-278 did not take effect')


def test_d278_pattern_was_genuinely_blocked_at_the_old_threshold():
    """The claim the ruling rests on: these candles are a valid rising three
    methods in every respect EXCEPT the old size gate. If they failed some
    other condition too, D-278 would not have unblocked anything."""
    o, h, l, c = _rtm_candles(small_red_range_atr=0.85)
    atr = latest_atr(h, l, c, 14)
    assert all(c[-4 + i] < o[-4 + i] for i in range(3)), 'not three reds'
    assert all(h[-4 + i] < h[-5] and l[-4 + i] > l[-5] for i in range(3)), 'not inside'
    assert c[-5] > o[-5] and (h[-5] - l[-5]) > atr, 'no big green leg'
    assert c[-1] > o[-1] and c[-1] > c[-5], 'no continuation'
    assert is_upswing(l, atr), 'not an upswing'
    # And the size gate itself: passes at 1.0 ATR, would have failed at 0.7.
    assert all((h[-4 + i] - l[-4 + i]) < atr * 1.0 for i in range(3))
    assert not all((h[-4 + i] - l[-4 + i]) < atr * 0.7 for i in range(3))


def test_d278_did_not_remove_the_condition_entirely():
    """Loosening is not deleting. A pullback candle LARGER than the average
    range is still not a 'small red' and must still be rejected, or the pattern
    stops meaning anything.

    Built from an absolute range rather than the fixed-point helper: the reds
    are clamped inside c1's range, so above roughly 1.4 ATR the fixture cannot
    hit a requested multiple and the honest thing is to measure what was built.
    """
    o, h, l, c = _rtm_raw(2.0)
    atr = latest_atr(h, l, c, 14)
    built = (h[-4] - l[-4]) / atr
    assert built > 1.0, (
        f'fixture built a {built:.3f}-ATR pullback, which the 1.0 threshold '
        'should accept - this test is no longer testing rejection')
    assert not rising_three_methods(o, h, l, c)['found'], (
        f'a {built:.3f}-ATR pullback now passes; the size condition was '
        'removed, not loosened')


# ===================== D-279: the weekly volume exemption =====================

def test_d279_exemption_is_declared_on_the_strategy():
    v4 = _by_name(STRATEGY_LAB_V4_STRATEGIES, 'V4_trend_reclaim')
    assert getattr(v4, 'exempt_weekly_volume_filter', False) is True


def test_d279_applies_on_weekly_only_and_to_this_strategy_only():
    """Scope is the whole ruling. A general opt-out would let any strategy
    delete whichever gate it keeps failing."""
    h = _harness()
    v4 = _by_name(STRATEGY_LAB_V4_STRATEGIES, 'V4_trend_reclaim')
    other = _by_name(STRATEGY_LAB_V4_STRATEGIES, 'V4_52w_high_breakout')

    for tf in WEEKLY_TIMEFRAMES:
        assert not h._volume_filter_applies(v4, tf), f'{tf} should be exempt'
        assert h._volume_filter_applies(other, tf), (
            f'{other.name} is exempt on {tf}; the exemption leaked')

    # Daily and monthly are slow too, and must NOT inherit it.
    for tf in ('1d', 'daily', '1mo', '1h', '15m', None):
        assert h._volume_filter_applies(v4, tf), (
            f'the weekly exemption leaked onto {tf}')

    assert WEEKLY_TIMEFRAMES < SLOW_TIMEFRAMES


def test_d279_leaves_the_other_two_stack_legs_alone():
    """The ruling narrows ONE condition. V4_trend_reclaim still faces the
    rising-EMA and RSI legs, so it is not exempt from the stack."""
    h = _harness()
    v4 = _by_name(STRATEGY_LAB_V4_STRATEGIES, 'V4_trend_reclaim')
    assert h._stack_applies(v4), 'V4_trend_reclaim lost the confirmation stack'


# ===================== min_bars_for() wiring (D-274 / convention 11) =========

def test_timeframe_seconds_covers_every_label_the_sweep_uses():
    for label, expected in (('5m', 300), ('15m', 900), ('1h', 3600),
                            ('1d', 86400), ('daily', 86400),
                            ('1wk', 604800), ('weekly', 604800),
                            ('1mo', 2592000)):
        assert timeframe_seconds(label) == expected, label
    # Unknown is None, not a guess. A caller that guessed a bar size here would
    # gate a strategy against a requirement nobody derived.
    for label in (None, '', 'WEIRD', 'D'):
        assert timeframe_seconds(label) is None, label


def test_resolve_min_bars_prefers_the_timeframe_aware_value():
    c2 = WeekendVacuumReversion
    assert resolve_min_bars(c2, timeframe_seconds('1h')) == 840
    assert resolve_min_bars(c2, timeframe_seconds('15m')) == 3360
    assert resolve_min_bars(c2, timeframe_seconds('5m')) == 10080
    # No label: fall back to the constant the strategy publishes, which is the
    # pre-D-274 behaviour, not a guess.
    assert resolve_min_bars(c2, None) == c2.min_bars == 840


def test_resolve_min_bars_falls_back_for_strategies_without_min_bars_for():
    """54 of the 55 strategies have no min_bars_for and must be untouched."""
    v4 = _by_name(STRATEGY_LAB_V4_STRATEGIES, 'V4_trend_reclaim')
    assert not hasattr(v4, 'min_bars_for')
    for tf in ('5m', '15m', '1h', '1d', '1wk', None):
        assert resolve_min_bars(v4, timeframe_seconds(tf)) == v4.min_bars == 58


def _flat_series(n, step_ms, start_ms=1_700_000_000_000):
    return [{'ts': start_ms + i * step_ms, 'open': 100.0, 'high': 100.5,
             'low': 99.5, 'close': 100.0, 'volume': 100.0} for i in range(n)]


@pytest.mark.parametrize('timeframe,step_ms,needed', [
    ('15m', 900_000, 3360),
    ('5m', 300_000, 10080),
])
def test_sub_hourly_c2_is_not_tested_not_failed(timeframe, step_ms, needed):
    """THE convention 11 fix. Before this wiring C2 cleared the 840-bar gate on
    a sub-hourly series, was handed an 840-bar window, failed its own in-scan
    history guard on every bar because 840 < 3,360, produced zero signals and
    was written to the graveyard as FAIL - a claim that the idea was tested and
    lost money. It was never run.
    """
    harness = VectorizedBacktestHarness({})
    reports = harness.run_sweep(_flat_series(4000, step_ms), 'X', timeframe,
                                strategies=[WeekendVacuumReversion()],
                                exit_configs=['fixed_2r'])
    assert reports, 'no rows emitted at all - a silent skip is worse (convention 20)'
    for row in reports:
        assert row['verdict'] == 'NOT_TESTED', (
            f'{timeframe} C2 reported {row["verdict"]}; it cannot run here')
        assert f'needs {needed} bars' in row['not_tested_reason']
        assert needed > MAX_STRATEGY_WINDOW


def test_hourly_c2_is_still_tested_normally():
    """The fix must not turn a runnable series into NOT_TESTED. On 1h the
    requirement is the 840 it always was, which is under MAX_STRATEGY_WINDOW,
    so C2 is genuinely tested and a zero-signal result is a real FAIL."""
    harness = VectorizedBacktestHarness({})
    reports = harness.run_sweep(_flat_series(2000, 3_600_000), 'X', '1h',
                                strategies=[WeekendVacuumReversion()],
                                exit_configs=['fixed_2r'])
    assert reports
    assert all(r['verdict'] != 'NOT_TESTED' for r in reports), (
        '1h C2 became NOT_TESTED; the gate is now rejecting a series it can run')


def test_scan_all_bars_widens_the_window_to_the_timeframe_aware_value():
    """The window handed to scan() must match what the gate let through, or
    the strategy silently refuses every bar inside a window too small for it -
    which is the original bug wearing a different hat."""
    harness = VectorizedBacktestHarness({})
    ind = precompute_indicators(_flat_series(1000, 3_600_000))
    strategy = WeekendVacuumReversion()

    seen = []
    original = strategy.scan
    strategy.scan = lambda w: (seen.append(len(w['closes'])), original(w))[1]

    harness.scan_all_bars(strategy, ind, None, timeframe='1h')
    assert max(seen) > SCAN_WINDOW
    assert max(seen) == resolve_min_bars(strategy, timeframe_seconds('1h')) == 840


def test_run_strategy_and_scan_all_bars_still_agree_on_the_start():
    """R-005's lesson, re-asserted for the new argument. If these two disagree
    a signal is cached at a bar the replay never visits, and the fix goes
    inert without failing anything."""
    harness = VectorizedBacktestHarness({})
    candles = _flat_series(600, 86_400_000)
    ind = precompute_indicators(candles)
    strategy = WeekendVacuumReversion()
    for tf in ('1d', '1wk', '1h', '15m'):
        expected = harness._scan_start(strategy, tf, timeframe_seconds(tf))
        signals = harness.scan_all_bars(strategy, ind, None, timeframe=tf)
        assert all(s is None for s in signals[:expected]), (
            f'{tf}: a signal was cached before the declared scan start')
