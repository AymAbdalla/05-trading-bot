"""Tests for strategy_lab_v4 (DEEP RENT ignitions as share strategies).

Each ignition gets: a synthetic fixture that MUST trigger it, the nearest
non-firing variants (each violated condition alone kills the signal), and
timeframe gating. Fixture-driven, same style as test_strategy_lab_v3.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.builtin.strategy_lab_v4 import (
    STRATEGY_LAB_V4_STRATEGIES, GapAndHoldProxy, FiftyTwoWeekHighBreakout,
    TrendReclaim,
)

_DAY_MS = 86_400_000
_WEEK_MS = 7 * _DAY_MS
_T0 = 1_600_000_000_000


def _mk(rows, bar_ms):
    """rows: list of (open, high, low, close, volume)."""
    return {
        'opens':  [r[0] for r in rows],
        'highs':  [r[1] for r in rows],
        'lows':   [r[2] for r in rows],
        'closes': [r[3] for r in rows],
        'volumes': [r[4] for r in rows],
        'timestamps': [_T0 + i * bar_ms for i in range(len(rows))],
    }


def _flat(n, px=100.0, vol=100.0):
    return [(px, px * 1.01, px * 0.99, px, vol)] * n


def _assert_valid_long(sig):
    assert sig is not None
    assert sig.direction == 'bullish'
    assert sig.entry is not None and sig.stop is not None and sig.target is not None
    assert sig.stop < sig.entry < sig.target


# ============ I1 · gap-and-hold proxy ============

def _gap_fixture(gap_open=104.0, close=105.5, vol=250.0):
    rows = _flat(30)
    # last day: gap up, hold in upper half, heavy volume
    rows[-1] = (gap_open, 106.0, 103.5, close, vol)
    return _mk(rows, _DAY_MS)


def test_gap_hold_fires_on_textbook_gap():
    sig = GapAndHoldProxy().scan(_gap_fixture())
    _assert_valid_long(sig)
    # thesis stop is the gap-fill level (pre-gap close), just below it
    assert sig.stop == pytest.approx(100.0 * 0.999)
    assert sig.features['proxy_for'].startswith('PEAD')


def test_gap_hold_rejects_small_gap():
    assert GapAndHoldProxy().scan(_gap_fixture(gap_open=101.5)) is None


def test_gap_hold_rejects_fading_close():
    # closes in the LOWER half of the day's range: gap did not hold
    assert GapAndHoldProxy().scan(_gap_fixture(close=103.9)) is None


def test_gap_hold_rejects_quiet_volume():
    assert GapAndHoldProxy().scan(_gap_fixture(vol=120.0)) is None


def test_gap_hold_ignores_weekly_bars():
    rows = _flat(30)
    rows[-1] = (104.0, 106.0, 103.5, 105.5, 250.0)
    assert GapAndHoldProxy().scan(_mk(rows, _WEEK_MS)) is None


# ============ I2 · 52-week-high breakout ============

def _breakout_fixture(n_flat=64, breakout_close=103.0, last_vol=300.0):
    rows = _flat(n_flat)                      # range: highs 101
    rows[-1] = (100.0, breakout_close + 0.5, 99.0, breakout_close, last_vol)
    return _mk(rows, _WEEK_MS)


def test_52w_breakout_fires():
    sig = FiftyTwoWeekHighBreakout().scan(_breakout_fixture())
    _assert_valid_long(sig)
    # thesis stop is the prior 52w high (the breakout level)
    assert sig.stop == pytest.approx(101.0)


def test_52w_breakout_rejects_close_below_prior_high():
    assert FiftyTwoWeekHighBreakout().scan(
        _breakout_fixture(breakout_close=100.5)) is None


def test_52w_breakout_rejects_without_volume():
    assert FiftyTwoWeekHighBreakout().scan(
        _breakout_fixture(last_vol=100.0)) is None


def test_52w_breakout_rejects_second_breakout_in_quiet_window():
    # a breakout 3 weeks ago violates the ">= 8 weeks below" condition
    rows = _flat(64)
    rows[-4] = (100.0, 103.5, 99.0, 103.0, 300.0)
    rows[-1] = (103.0, 104.5, 102.0, 104.0, 300.0)
    assert FiftyTwoWeekHighBreakout().scan(_mk(rows, _WEEK_MS)) is None


def test_52w_breakout_ignores_daily_bars():
    rows = _flat(64)
    rows[-1] = (100.0, 103.5, 99.0, 103.0, 300.0)
    assert FiftyTwoWeekHighBreakout().scan(_mk(rows, _DAY_MS)) is None


# ============ I3 · trend reclaim ============

def _reclaim_fixture(dip=0.90, pop=1.06, mom_break=False):
    """50 weeks rising (MA rises), 4 weeks dipped below MA, then reclaim."""
    rows = []
    px = 100.0
    for i in range(54):
        px *= 1.01                             # steady uptrend
        rows.append((px, px * 1.01, px * 0.99, px, 100.0))
    ma_zone = px
    for i in range(4):                         # 4 closes below the 20w MA
        rows.append((ma_zone * dip, ma_zone * dip * 1.01,
                     ma_zone * dip * 0.99, ma_zone * dip, 100.0))
    close = ma_zone * pop
    rows.append((close, close * 1.01, close * 0.99, close, 100.0))
    if mom_break:
        # raise the 12-months-ago base (bar -53 = index len-53) so the
        # 12-1 momentum ratio goes negative
        for i in range(len(rows) - 53, len(rows) - 50):
            rows[i] = (400.0, 404.0, 396.0, 400.0, 100.0)
    return _mk(rows, _WEEK_MS)


def test_trend_reclaim_fires():
    sig = TrendReclaim().scan(_reclaim_fixture())
    _assert_valid_long(sig)
    assert sig.features['mom_12_1'] > 0


def test_trend_reclaim_rejects_when_still_below_ma():
    # a fifth week at the dip level: no reclaim happened
    assert TrendReclaim().scan(_reclaim_fixture(pop=0.90)) is None


def test_trend_reclaim_rejects_negative_momentum():
    assert TrendReclaim().scan(_reclaim_fixture(mom_break=True)) is None


def test_trend_reclaim_ignores_daily_bars():
    fx = _reclaim_fixture()
    fx['timestamps'] = [_T0 + i * _DAY_MS for i in range(len(fx['closes']))]
    assert TrendReclaim().scan(fx) is None


# ============ family invariants ============

def test_expected_roster():
    assert {s.name for s in STRATEGY_LAB_V4_STRATEGIES} == {
        'V4_gap_hold_proxy', 'V4_52w_high_breakout', 'V4_trend_reclaim',
    }
    # proxy status must be visible in the NAME (deviation 1), not only docs
    assert any('proxy' in s.name for s in STRATEGY_LAB_V4_STRATEGIES)


def test_scan_never_raises_on_garbage():
    garbage = [
        {},
        {'closes': []},
        {'opens': [1], 'highs': [1], 'lows': [1], 'closes': [1],
         'volumes': [1], 'timestamps': [1]},
        _mk(_flat(5), _WEEK_MS),
        {'opens': [1] * 70, 'highs': [1] * 70, 'lows': [1] * 70,
         'closes': [0.0] * 70, 'volumes': [0.0] * 70,
         'timestamps': list(range(70))},
    ]
    for s in STRATEGY_LAB_V4_STRATEGIES:
        for g in garbage:
            assert s.scan(g) is None


def test_min_bars_within_scan_window():
    from backtest.vectorized_harness import SCAN_WINDOW
    for s in STRATEGY_LAB_V4_STRATEGIES:
        assert s.min_bars <= SCAN_WINDOW, (
            f'{s.name} needs {s.min_bars} bars; scan window is {SCAN_WINDOW} '
            f'- it would be NOT_TESTED everywhere')
