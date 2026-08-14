"""Tests for strategy_lab_v5 (P5 FORCED-FLOW HARVEST).

Each strategy gets: a synthetic fixture that MUST trigger it, the nearest
non-firing variants (each violated condition alone kills the signal), and
structural gating (timeframe + 24/7-tape discrimination). Fixture-driven,
same style as test_strategy_lab_v4. The crypto fixture injects a synthetic
funding-stress table so the tests do not depend on which real dates happened
to be stressed in the one-year funding files.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.builtin.strategy_lab_v5 import (
    STRATEGY_LAB_V5_STRATEGIES, ForcedFlowCrypto, CapitulationEquity,
)

_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000
_WEEK_MS = 7 * _DAY_MS
# 2020-09-14 00:00 UTC, a Monday - lets the weekday-only generator start
# cleanly and makes every date arithmetic in this file deterministic.
_T0 = 1_600_041_600_000


def _mk(rows, timestamps):
    """rows: list of (open, high, low, close, volume)."""
    assert len(rows) == len(timestamps)
    return {
        'opens':  [r[0] for r in rows],
        'highs':  [r[1] for r in rows],
        'lows':   [r[2] for r in rows],
        'closes': [r[3] for r in rows],
        'volumes': [r[4] for r in rows],
        'timestamps': timestamps,
    }


def _flat(n, px=100.0):
    """Flat tape with ALTERNATING volume (90/110) so the volume baseline has
    a nonzero std - a constant-volume baseline is rejected by design."""
    return [(px, px * 1.005, px * 0.99, px, 90.0 if i % 2 else 110.0)
            for i in range(n)]


def _daily_ts(n, start=_T0):
    """24/7 daily timestamps (crypto tape: weekend bars present)."""
    return [start + i * _DAY_MS for i in range(n)]


def _weekday_ts(n, start=_T0):
    """Daily timestamps skipping Saturday/Sunday (equity tape)."""
    out, ts = [], start
    while len(out) < n:
        wd = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).weekday()
        if wd < 5:
            out.append(ts)
        ts += _DAY_MS
    return out


def _assert_valid_long(sig):
    assert sig is not None
    assert sig.direction == 'bullish'
    assert sig.entry is not None and sig.stop is not None and sig.target is not None
    assert sig.stop < sig.entry < sig.target


# ============ V5_forced_flow_crypto ============

_N_CRYPTO = 120


def _stress_table(ts_list, pctl=0.10, on_prior=True):
    """Synthetic funding table: stress pctl on the PRIOR UTC date of the
    last bar (the date the strategy is defined to read). on_prior=False
    puts it on the bar's own date instead, which must NOT satisfy the gate."""
    last_day = datetime.fromtimestamp(ts_list[-1] / 1000, tz=timezone.utc).date()
    day = last_day - timedelta(days=1) if on_prior else last_day
    return {day: pctl}


def _cascade_fixture(cascade=True, expand=True, vol=300.0, break_swing=False,
                     ts_fn=_daily_ts):
    """Flat 24/7 tape (lows 99.0), then a 3-bar liquidation cascade:
    down candles, monotone lower closes, net range expansion (first range
    0.35 -> last range 0.65), climax volume, cascade low 99.05 holding
    above the 99.0 prior swing low."""
    rows = _flat(_N_CRYPTO)
    ts = ts_fn(_N_CRYPTO)
    if cascade:
        first_high = 100.05 if expand else 100.60   # widen bar 1 to kill expansion
        first_low = 99.70 if expand else 99.70
        rows[-3] = (100.0, first_high, first_low, 99.8, vol)
        rows[-2] = (99.8, 99.9, 99.45, 99.6, vol)
        last_low = 98.50 if break_swing else 99.05
        rows[-1] = (99.6, 99.7, last_low, 99.2, vol)
    return _mk(rows, ts)


def test_forced_flow_fires_on_textbook_cascade():
    fx = _cascade_fixture()
    strat = ForcedFlowCrypto(funding_stress=_stress_table(fx['timestamps']))
    sig = strat.scan(fx)
    _assert_valid_long(sig)
    # stop strictly below the event (cascade) low, per P5
    assert sig.stop < 99.05
    assert sig.stop == pytest.approx(99.05 * 0.999)
    assert sig.features['cascade_bars'] == 3
    assert sig.features['volume_z'] > 3
    # the one-regime caveat must travel with every signal
    assert 'funding_regime_caveat' in sig.features


def test_forced_flow_rejects_unstressed_funding():
    # identical tape, funding percentile comfortably mid-range: gate closed
    fx = _cascade_fixture()
    strat = ForcedFlowCrypto(funding_stress=_stress_table(fx['timestamps'], pctl=0.90))
    assert strat.scan(fx) is None


def test_forced_flow_rejects_missing_funding_date():
    # stress exists only for the bar's OWN date, not the prior date the
    # strategy reads (lookahead-safety: prior date only)
    fx = _cascade_fixture()
    strat = ForcedFlowCrypto(
        funding_stress=_stress_table(fx['timestamps'], on_prior=False))
    assert strat.scan(fx) is None


def test_forced_flow_rejects_no_cascade():
    fx = _cascade_fixture(cascade=False)
    strat = ForcedFlowCrypto(funding_stress=_stress_table(fx['timestamps']))
    assert strat.scan(fx) is None


def test_forced_flow_rejects_non_expanding_ranges():
    # same cascade, but the first bar's range is wider than the last's
    fx = _cascade_fixture(expand=False)
    strat = ForcedFlowCrypto(funding_stress=_stress_table(fx['timestamps']))
    assert strat.scan(fx) is None


def test_forced_flow_rejects_quiet_volume():
    # cascade volume inside the baseline noise: no climax, no forced flow
    fx = _cascade_fixture(vol=120.0)
    strat = ForcedFlowCrypto(funding_stress=_stress_table(fx['timestamps']))
    assert strat.scan(fx) is None


def test_forced_flow_rejects_broken_swing_low():
    # cascade takes out the prior swing low: breakdown, not absorbable flush
    fx = _cascade_fixture(break_swing=True)
    strat = ForcedFlowCrypto(funding_stress=_stress_table(fx['timestamps']))
    assert strat.scan(fx) is None


def test_forced_flow_rejects_weekday_only_tape():
    # equity-shaped tape (no weekend bars) must never enter the crypto
    # cohort, even with matching funding dates - the 24/7 gate is the
    # discriminator (module docstring DEVIATION 2)
    fx = _cascade_fixture(ts_fn=_weekday_ts)
    strat = ForcedFlowCrypto(funding_stress=_stress_table(fx['timestamps']))
    assert strat.scan(fx) is None


def test_forced_flow_ignores_weekly_bars():
    fx = _cascade_fixture(ts_fn=lambda n: [_T0 + i * _WEEK_MS for i in range(n)])
    strat = ForcedFlowCrypto(funding_stress=_stress_table(fx['timestamps']))
    assert strat.scan(fx) is None


def test_forced_flow_ignores_15m_bars():
    fx = _cascade_fixture(ts_fn=lambda n: [_T0 + i * 900_000 for i in range(n)])
    strat = ForcedFlowCrypto(funding_stress=_stress_table(fx['timestamps']))
    assert strat.scan(fx) is None


def test_forced_flow_empty_table_emits_nothing():
    # degrade-to-empty contract: no funding data -> no signals, no crash
    fx = _cascade_fixture()
    assert ForcedFlowCrypto(funding_stress={}).scan(fx) is None


# ============ V5_capitulation_equity ============

_N_EQ = 80


def _capitulation_fixture(gap_open=98.0, close=94.3, low=94.0, vol=300.0,
                          ts_fn=_weekday_ts):
    """Weekday tape, then a capitulation day: gap-down open (-2%), close in
    the bottom decile of the day's range, climax volume z >> 4."""
    rows = _flat(_N_EQ)
    rows[-1] = (gap_open, 98.5, low, close, vol)
    return _mk(rows, ts_fn(_N_EQ))


def test_capitulation_fires_on_textbook_day():
    sig = CapitulationEquity().scan(_capitulation_fixture())
    _assert_valid_long(sig)
    # stop strictly below the event (day) low, per P5
    assert sig.stop < 94.0
    assert sig.stop == pytest.approx(94.0 * 0.999)
    assert sig.features['volume_z'] > 4
    # the uncontrolled-earnings deviation must travel with every signal
    assert sig.features['earnings_exclusion'] == 'OMITTED_NO_EARNINGS_CALENDAR'


def test_capitulation_rejects_no_gap_down():
    # opens flat at the prior close: no overnight forced-liquidation signature
    assert CapitulationEquity().scan(_capitulation_fixture(gap_open=100.0)) is None


def test_capitulation_rejects_midrange_close():
    # closes mid-range: buyers absorbed it intraday, not a capitulation close
    assert CapitulationEquity().scan(_capitulation_fixture(close=96.5)) is None


def test_capitulation_rejects_quiet_volume():
    assert CapitulationEquity().scan(_capitulation_fixture(vol=120.0)) is None


def test_capitulation_rejects_24_7_tape():
    # crypto-shaped tape (weekend bars) must never enter the equity cohort:
    # the cohorts stay structurally disjoint for the mechanism-coherence
    # kill condition
    assert CapitulationEquity().scan(_capitulation_fixture(ts_fn=_daily_ts)) is None


def test_capitulation_ignores_weekly_bars():
    fx = _capitulation_fixture(
        ts_fn=lambda n: [_T0 + i * _WEEK_MS for i in range(n)])
    assert CapitulationEquity().scan(fx) is None


def test_capitulation_ignores_hourly_bars():
    fx = _capitulation_fixture(
        ts_fn=lambda n: [_T0 + i * _HOUR_MS for i in range(n)])
    assert CapitulationEquity().scan(fx) is None


# ============ family invariants ============

def test_expected_roster():
    assert {s.name for s in STRATEGY_LAB_V5_STRATEGIES} == {
        'V5_forced_flow_crypto', 'V5_capitulation_equity',
    }
    # separate cohorts per P5's mechanism-coherence kill condition: the two
    # legs must be distinct strategies, never pooled under one name
    assert len(STRATEGY_LAB_V5_STRATEGIES) == 2


def test_scan_never_raises_on_garbage():
    garbage = [
        {},
        {'closes': []},
        {'opens': [1], 'highs': [1], 'lows': [1], 'closes': [1],
         'volumes': [1], 'timestamps': [1]},
        _mk(_flat(5), _daily_ts(5)),
        {'opens': [1] * 130, 'highs': [1] * 130, 'lows': [1] * 130,
         'closes': [0.0] * 130, 'volumes': [0.0] * 130,
         'timestamps': list(range(130))},
        {'opens': [1] * 130, 'highs': [float('nan')] * 130, 'lows': [1] * 130,
         'closes': [1] * 130, 'volumes': [1] * 130,
         'timestamps': [float('inf')] * 130},
    ]
    for s in STRATEGY_LAB_V5_STRATEGIES:
        for g in garbage:
            assert s.scan(g) is None


def test_min_bars_within_scan_window():
    from backtest.vectorized_harness import SCAN_WINDOW
    for s in STRATEGY_LAB_V5_STRATEGIES:
        assert s.min_bars <= SCAN_WINDOW, (
            f'{s.name} needs {s.min_bars} bars; scan window is {SCAN_WINDOW} '
            f'- it would be NOT_TESTED everywhere')
