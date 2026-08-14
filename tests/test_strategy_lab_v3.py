"""Tests for strategies/builtin/strategy_lab_v3.py.

Two jobs:
  1. no strategy may ever raise, whatever garbage it is handed
  2. every strategy must be provably ALIVE - a synthetic tape that satisfies
     its genome produces a Signal, and a one-condition-off tape does not

Job 2 exists because this project has shipped strategies that were dead on
arrival (empty loop ranges, unsatisfiable comparisons, session-ATR versus
bar-ATR unit mismatches) and looked identical in the graveyard to strategies
that were merely wrong.
"""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.builtin.strategy_lab_v3 import (  # noqa: E402
    STRATEGY_LAB_V3_STRATEGIES, IntradayMomentum, MacroDrift, VacuumRefill,
    FOMC_DATES, NFP_DATES, _load_macro_calendar,
)
from strategies.builtin.strategy_lab import _in_xnys_session  # noqa: E402

ET = ZoneInfo('America/New_York')
UTC = ZoneInfo('UTC')


# ============ synthetic tape builders ============

def _ms(dt) -> float:
    return dt.timestamp() * 1000.0


def _bars(rows):
    """rows: list of (ts_ms, open, high, low, close, volume)."""
    return {
        'timestamps': [r[0] for r in rows],
        'opens': [r[1] for r in rows],
        'highs': [r[2] for r in rows],
        'lows': [r[3] for r in rows],
        'closes': [r[4] for r in rows],
        'volumes': [r[5] for r in rows],
    }


def _trading_days(count: int, end_before):
    """`count` NYSE trading dates ending on or before `end_before`, ascending."""
    out = []
    probe = end_before
    while len(out) < count:
        stamp = datetime.combine(probe, datetime.min.time(), tzinfo=ET).replace(hour=12)
        if _in_xnys_session(stamp.timestamp() * 1000.0):
            out.append(probe)
        probe = probe - timedelta(days=1)
    out.reverse()
    return out


def _rth_session_rows(day, closes, volume, start_min=570):
    """5m RTH bars for one ET date from `closes`, first bar stamped 9:30."""
    rows = []
    base = datetime(day.year, day.month, day.day, 0, 0, tzinfo=ET)
    for k, c in enumerate(closes):
        stamp = base + timedelta(minutes=start_min + 5 * k)
        prev = closes[k - 1] if k else c
        rows.append((_ms(stamp), prev, max(prev, c) + 0.02, min(prev, c) - 0.02,
                     c, volume))
    return rows


def _flat_path(start, n, wiggle=0.0002):
    return [start * (1.0 + (wiggle if i % 2 else -wiggle)) for i in range(n)]


def _rising_path(start, n, step=0.001):
    return [start * (1.0 + step * (i + 1)) for i in range(n)]


BARS_TO_1530 = 73  # 9:30 through 15:30 inclusive on 5m bars


def _intraday_momentum_tape(final_day_closes):
    """Two quiet baseline sessions plus a final session ending on its 15:30 bar."""
    days = _trading_days(3, datetime.now(ET).date() - timedelta(days=30))
    rows = []
    price = 100.0
    for day in days[:2]:
        path = _flat_path(price, BARS_TO_1530)
        rows += _rth_session_rows(day, path, volume=1000.0)
        price = path[-1]
    rows += _rth_session_rows(days[2], final_day_closes(price), volume=5000.0)
    return _bars(rows)


def _utc_day_rows(day, closes, volume, bars_per_day=96, start_min=0):
    rows = []
    base = datetime(day.year, day.month, day.day, 0, 0, tzinfo=UTC)
    for k, c in enumerate(closes):
        stamp = base + timedelta(minutes=start_min + 15 * k)
        prev = closes[k - 1] if k else c
        rows.append((_ms(stamp), prev, max(prev, c) + 0.5, min(prev, c) - 0.5,
                     c, volume))
    return rows


def _crypto_momentum_tape(final_day_closes):
    """One quiet UTC day plus a second running to its 23:30 bar (96th half hour)."""
    day_a = datetime(2025, 6, 10).date()
    day_b = datetime(2025, 6, 11).date()
    path_a = _flat_path(30000.0, 96, wiggle=0.0002)
    rows = _utc_day_rows(day_a, path_a, volume=100.0)
    rows += _utc_day_rows(day_b, final_day_closes(path_a[-1]), volume=900.0)
    return _bars(rows)


def _macro_tape(day, bars=55):
    """5m RTH bars for one ET date, last bar stamped exactly 14:00."""
    start_min = 840 - 5 * (bars - 1)
    path = _flat_path(400.0, bars, wiggle=0.0005)
    return _bars(_rth_session_rows(day, path, volume=2000.0, start_min=start_min))


def _pre_event_day(dates):
    """A date whose NEXT calendar day is in `dates` and which is itself a
    trading day with a real 14:00 ET session bar."""
    for event in sorted(dates):
        prior = event - timedelta(days=1)
        stamp = datetime(prior.year, prior.month, prior.day, 14, 0, tzinfo=ET)
        if _in_xnys_session(stamp.timestamp() * 1000.0):
            return prior
    return None


def _vacuum_tape(flush_pct=0.015, n=300):
    """A tape whose realized vol rises steadily, then a volume-climax flush,
    then one bar closing above its own open.

    The base path is MONOTONE rising with a growing per-bar step. Rising gives
    the realized-vol filter a climbing series to clear its 60th percentile
    against, and monotone means the only decline anywhere in the tape is the
    injected flush - so when the near-miss cases return None it is provably
    the gate under test and not some accidental multi-bar decline elsewhere in
    the synthetic tape. The refill bounce is a fraction of the flush for the
    same reason: a fixed bounce would overshoot the 50% retrace target on
    small flushes and make the cost gate untestable.
    """
    closes, highs, lows, opens, vols = [], [], [], [], []
    price = 100.0
    for i in range(n - 2):
        price *= (1.0 + 0.00005 + 0.000004 * i)
        closes.append(price)
    ref = closes[-1]
    flush_close = ref * (1.0 - flush_pct)
    closes.append(flush_close)                             # n-2: the flush bar
    closes.append(flush_close * (1.0 + 0.25 * flush_pct))  # n-1: the refill

    for i in range(n):
        c = closes[i]
        prev = closes[i - 1] if i else c
        opens.append(prev)
        highs.append(max(prev, c) + 0.01)
        lows.append(min(prev, c) - 0.01)
        vols.append(1000.0 + (i % 7) * 10.0)
    vols[n - 2] = 60000.0  # climax volume on the flush bar

    base = datetime(2025, 3, 1, 0, 0, tzinfo=UTC)
    ts = [_ms(base + timedelta(minutes=15 * i)) for i in range(n)]
    return {'timestamps': ts, 'opens': opens, 'highs': highs, 'lows': lows,
            'closes': closes, 'volumes': vols}


# ============ degenerate input ============

DEGENERATE = {
    'empty_dict': {},
    'empty_lists': {'opens': [], 'highs': [], 'lows': [], 'closes': [],
                    'volumes': [], 'timestamps': []},
    'five_bars': {
        'opens': [10.0] * 5, 'highs': [10.5] * 5, 'lows': [9.5] * 5,
        'closes': [10.0] * 5, 'volumes': [100.0] * 5,
        'timestamps': [1700000000000 + 300000 * i for i in range(5)],
    },
    'flat': {
        'opens': [10.0] * 400, 'highs': [10.0] * 400, 'lows': [10.0] * 400,
        'closes': [10.0] * 400, 'volumes': [100.0] * 400,
        'timestamps': [1700000000000 + 300000 * i for i in range(400)],
    },
    'zero_volume': {
        'opens': [10.0] * 400, 'highs': [10.6] * 400, 'lows': [9.4] * 400,
        'closes': [10.2] * 400, 'volumes': [0.0] * 400,
        'timestamps': [1700000000000 + 300000 * i for i in range(400)],
    },
    'ragged': {
        'opens': [10.0] * 400, 'highs': [10.6] * 400, 'lows': [9.4] * 400,
        'closes': [10.2] * 399, 'volumes': [5.0] * 400,
        'timestamps': [1700000000000 + 300000 * i for i in range(400)],
    },
    'none_input': None,
}


@pytest.mark.parametrize('strategy', STRATEGY_LAB_V3_STRATEGIES,
                         ids=[s.name for s in STRATEGY_LAB_V3_STRATEGIES])
@pytest.mark.parametrize('case', sorted(DEGENERATE))
def test_degenerate_input_returns_none(strategy, case):
    assert strategy.scan(DEGENERATE[case]) is None


@pytest.mark.parametrize('strategy', STRATEGY_LAB_V3_STRATEGIES,
                         ids=[s.name for s in STRATEGY_LAB_V3_STRATEGIES])
def test_interface_contract(strategy):
    from strategies.base import Strategy
    assert isinstance(strategy, Strategy)
    assert strategy.is_entry is True
    assert isinstance(strategy.name, str) and strategy.name


def test_names_are_v3_prefixed_and_unique():
    names = [s.name for s in STRATEGY_LAB_V3_STRATEGIES]
    assert all(n.startswith('V3_') for n in names), names
    assert len(names) == len(set(names)), names


def test_expected_roster():
    assert {s.name for s in STRATEGY_LAB_V3_STRATEGIES} == {
        'V3_intraday_momentum', 'V3_intraday_momentum_crypto',
        'V3_macro_drift', 'V3_macro_drift_nfp', 'V3_macro_drift_cpi',
        'V3_vacuum_refill',
    }


def test_macro_calendar_loaded():
    assert FOMC_DATES, "FOMC dates missing: V3_macro_drift would be dead"
    assert NFP_DATES, "NFP dates missing: V3_macro_drift_nfp would be dead"


def test_macro_calendar_missing_file_degrades_quietly():
    fomc, nfp, cpi = _load_macro_calendar('/nonexistent/macro_calendar.json')
    assert fomc == frozenset() and nfp == frozenset() and cpi == frozenset()


def _assert_valid_long(sig):
    assert sig is not None
    assert sig.direction == 'bullish'
    assert sig.entry is not None and sig.stop is not None and sig.target is not None
    assert sig.stop < sig.entry, (sig.stop, sig.entry)
    assert sig.target > sig.entry, (sig.target, sig.entry)
    assert 0.0 <= sig.confidence <= 1.0


# ============ V3_intraday_momentum ============

def test_intraday_momentum_fires():
    tape = _intraday_momentum_tape(lambda p: _rising_path(p, BARS_TO_1530))
    sig = IntradayMomentum().scan(tape)
    _assert_valid_long(sig)
    assert sig.pattern == 'V3_intraday_momentum'
    assert sig.features['r1'] > 0 and sig.features['r12'] > 0
    assert sig.features['amplifiers'] >= 2


def test_intraday_momentum_near_miss_on_sign_disagreement():
    def path(p):
        rising = _rising_path(p, BARS_TO_1530)
        # r1 stays positive; the twelfth half hour (15:00-15:30, the last 7
        # bars here) is dragged negative, so sign agreement fails.
        peak = rising[-8]
        for k in range(7):
            rising[-7 + k] = peak * (1.0 - 0.002 * (k + 1))
        return rising
    assert IntradayMomentum().scan(_intraday_momentum_tape(path)) is None


def test_intraday_momentum_near_miss_without_amplifiers():
    """Same rising day, but on quiet volume and quiet bar-to-bar moves: the
    amplifier count drops below 2 and the trade is skipped."""
    days = _trading_days(3, datetime.now(ET).date() - timedelta(days=30))
    rows = []
    price = 100.0
    for day in days[:2]:
        path = _flat_path(price, BARS_TO_1530, wiggle=0.004)
        rows += _rth_session_rows(day, path, volume=90000.0)
        price = path[-1]
    rows += _rth_session_rows(days[2], _rising_path(price, BARS_TO_1530, step=0.00002),
                              volume=10.0)
    sig = IntradayMomentum().scan(_bars(rows))
    assert sig is None


def test_intraday_momentum_ignores_bars_before_1530():
    tape = _intraday_momentum_tape(lambda p: _rising_path(p, BARS_TO_1530))
    truncated = {k: v[:-1] for k, v in tape.items()}  # last bar is now 15:25
    assert IntradayMomentum().scan(truncated) is None


def test_intraday_momentum_fires_only_once_per_session():
    """A later afternoon bar must not re-fire the same day's verdict."""
    days = _trading_days(3, datetime.now(ET).date() - timedelta(days=30))
    rows = []
    price = 100.0
    for day in days[:2]:
        path = _flat_path(price, BARS_TO_1530)
        rows += _rth_session_rows(day, path, volume=1000.0)
        price = path[-1]
    rows += _rth_session_rows(days[2], _rising_path(price, BARS_TO_1530 + 3),
                              volume=5000.0)  # runs through 15:45
    assert IntradayMomentum().scan(_bars(rows)) is None


# ============ V3_intraday_momentum_crypto ============

def test_intraday_momentum_crypto_fires():
    tape = _crypto_momentum_tape(lambda p: _rising_path(p, 95, step=0.0006))
    sig = IntradayMomentum(crypto=True).scan(tape)
    _assert_valid_long(sig)
    assert sig.pattern == 'V3_intraday_momentum_crypto'
    assert sig.features['clock'] == 'UTC'


def test_intraday_momentum_crypto_near_miss_on_negative_r1():
    def path(p):
        rising = _rising_path(p * 0.97, 95, step=0.0006)
        return rising  # first UTC half hour opens 3% below the prior close
    assert IntradayMomentum(crypto=True).scan(_crypto_momentum_tape(path)) is None


# ============ V3_macro_drift ============

def test_macro_drift_fires():
    day = _pre_event_day(FOMC_DATES)
    assert day is not None
    sig = MacroDrift(event='FOMC').scan(_macro_tape(day))
    _assert_valid_long(sig)
    assert sig.pattern == 'V3_macro_drift'
    assert sig.features['event'] == 'FOMC'
    assert sig.features['vix_conditioning'] == 'OMITTED_NO_SERIES'


def test_macro_drift_near_miss_on_non_event_day():
    day = _pre_event_day(FOMC_DATES)
    probe = day - timedelta(days=7)
    while (probe + timedelta(days=1)) in FOMC_DATES or not _in_xnys_session(
            datetime(probe.year, probe.month, probe.day, 14, 0, tzinfo=ET).timestamp() * 1000.0):
        probe -= timedelta(days=1)
    assert MacroDrift(event='FOMC').scan(_macro_tape(probe)) is None


def test_macro_drift_near_miss_before_1400():
    day = _pre_event_day(FOMC_DATES)
    tape = _macro_tape(day)
    truncated = {k: v[:-1] for k, v in tape.items()}  # last bar is now 13:55
    assert MacroDrift(event='FOMC').scan(truncated) is None


def test_macro_drift_does_not_fire_on_the_event_day_itself():
    event = _pre_event_day(FOMC_DATES) + timedelta(days=1)
    if not _in_xnys_session(datetime(event.year, event.month, event.day, 14, 0,
                                     tzinfo=ET).timestamp() * 1000.0):
        pytest.skip("event day is not a trading session")
    assert MacroDrift(event='FOMC').scan(_macro_tape(event)) is None


# ============ V3_macro_drift_nfp ============

def test_macro_drift_nfp_fires():
    day = _pre_event_day(NFP_DATES)
    assert day is not None
    sig = MacroDrift(event='NFP').scan(_macro_tape(day))
    _assert_valid_long(sig)
    assert sig.pattern == 'V3_macro_drift_nfp'
    assert sig.features['event'] == 'NFP'


def test_macro_drift_nfp_ignores_fomc_days():
    day = _pre_event_day(FOMC_DATES)
    if (day + timedelta(days=1)) in NFP_DATES:
        pytest.skip("this FOMC eve is also an NFP eve")
    assert MacroDrift(event='NFP').scan(_macro_tape(day)) is None


# ============ V3_vacuum_refill ============

def test_vacuum_refill_fires():
    sig = VacuumRefill().scan(_vacuum_tape())
    _assert_valid_long(sig)
    assert sig.pattern == 'V3_vacuum_refill'
    assert sig.features['flush_pct'] >= VacuumRefill.MIN_FLUSH
    assert sig.features['volume_z'] > VacuumRefill.MIN_VOL_Z
    assert sig.features['cross_pair_test'] == 'OMITTED_NO_CROSS_TICKER_DATA'
    # features round to 6dp; the stop itself is the raw flush low
    assert sig.stop == pytest.approx(sig.features['flush_low'], abs=1e-6)


def test_vacuum_refill_near_miss_flush_below_cost_hurdle():
    """0.3% is exactly one round trip, not the doc's mandatory 2.5x."""
    assert VacuumRefill().scan(_vacuum_tape(flush_pct=0.003)) is None


def test_vacuum_refill_near_miss_without_climax_volume():
    tape = _vacuum_tape()
    tape['volumes'][len(tape['volumes']) - 2] = 1005.0
    assert VacuumRefill().scan(tape) is None


def test_vacuum_refill_near_miss_when_trigger_bar_closes_down():
    tape = _vacuum_tape()
    last = len(tape['closes']) - 1
    tape['closes'][last] = tape['opens'][last] * 0.999
    assert VacuumRefill().scan(tape) is None


def test_vacuum_refill_needs_full_history():
    tape = _vacuum_tape()
    short = {k: v[-200:] for k, v in tape.items()}
    assert VacuumRefill().scan(short) is None


# ============ statelessness ============

def _all_tapes():
    tapes = [_vacuum_tape(),
             _intraday_momentum_tape(lambda p: _rising_path(p, BARS_TO_1530)),
             _crypto_momentum_tape(lambda p: _rising_path(p, 95, step=0.0006))]
    for dates in (FOMC_DATES, NFP_DATES):
        day = _pre_event_day(dates)
        if day:
            tapes.append(_macro_tape(day))
    return tapes


@pytest.mark.parametrize('strategy', STRATEGY_LAB_V3_STRATEGIES,
                         ids=[s.name for s in STRATEGY_LAB_V3_STRATEGIES])
def test_scan_is_stateless(strategy):
    """The harness reuses ONE instance across every bar of every ticker, so a
    verdict must depend on the window alone. Scanning other tapes in between
    must not change the answer, and neither must scanning the same tape twice.
    """
    tapes = _all_tapes()
    first = [strategy.scan(t) for t in tapes]
    for junk in (DEGENERATE['flat'], DEGENERATE['zero_volume']):
        strategy.scan(junk)
    for tape in reversed(tapes):
        strategy.scan(tape)
    second = [strategy.scan(t) for t in tapes]
    for a, b in zip(first, second):
        assert (a is None) == (b is None)
        if a is not None:
            assert (a.entry, a.stop, a.target) == (b.entry, b.stop, b.target)


@pytest.mark.parametrize('strategy', STRATEGY_LAB_V3_STRATEGIES,
                         ids=[s.name for s in STRATEGY_LAB_V3_STRATEGIES])
def test_scan_does_not_mutate_the_window(strategy):
    """A strategy that edits the window in place would corrupt every later
    strategy in the sweep, since the harness builds one window per bar."""
    for tape in _all_tapes():
        snapshot = {k: list(v) for k, v in tape.items()}
        strategy.scan(tape)
        assert tape == snapshot
