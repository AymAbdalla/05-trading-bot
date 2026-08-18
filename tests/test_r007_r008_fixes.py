"""Regression tests for R-007 / R-008 / R-009 (D-273, D-274, D-275).

Each test pins a defect that made a strategy silently dead:

* rsi_extreme required `rsi14 < 35` AND `close > ema50`. RSI(14) conditional
  on `close > EMA50` has a hard floor near 36, so the pair was unsatisfiable
  - zero bars in the whole graveyard universe could ever fire it.
* C2 expressed every horizon as a bar COUNT that only meant the intended
  duration on 1h bars: `24 * 4` was "4 days" on 1h and 24 hours on 15m, so
  the Friday anchor was unreachable on 100% of sub-hourly trigger bars.
* 9,042 C2 graveyard rows carry a reason string emitted by no code in the
  tree, so they are pre-fix artefacts. They are archived, not deleted.

Convention 3: two of these assert on REAL market data, not only synthetics.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from backtest.data_loader import load_csv  # noqa: E402
from backtest.vectorized_harness import (  # noqa: E402
    MAX_STRATEGY_WINDOW, SCAN_WINDOW, VectorizedBacktestHarness,
    precompute_indicators,
)
from strategies.builtin.expanded import RsiExtreme  # noqa: E402
from strategies.builtin.strategy_lab import (  # noqa: E402
    WeekendVacuumReversion, _bar_seconds, _bars_for,
)

DATA = ROOT / 'backtest' / 'data'
ARCHIVE = ROOT / 'research' / 'graveyard' / 'archive' / 'c2_stale_rows.json'
STALE_REASON = 'needs 840 bars, scan window is 260'

# Measured floor of RSI(14) conditional on close > EMA50 over the full
# graveyard bar universe (docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md).
CONDITIONAL_RSI_FLOOR = 36.26


# ============================================================ R-007 / D-273

def test_rsi_extreme_threshold_sits_above_the_conditional_floor():
    """The bug in one assertion. A threshold below the conditional floor of
    the distribution it filters is unsatisfiable, not tight; no amount of
    data will ever produce a signal. 35 < 36.26 was the whole defect."""
    assert RsiExtreme.RSI_MAX_ENTRY > CONDITIONAL_RSI_FLOOR, (
        f'rsi_extreme can never fire: RSI given close>EMA50 has floor '
        f'{CONDITIONAL_RSI_FLOOR}, threshold is {RsiExtreme.RSI_MAX_ENTRY}')
    assert RsiExtreme.RSI_MAX_ENTRY == 45.0, 'R-007 ruled 35 -> 45'


def _pullback_in_uptrend(rsi_target_low=True):
    """A long rise followed by a shallow pullback: close stays above EMA50
    while RSI(14) drops into the low 40s. This is the geometry the strategy
    was written for and could never actually see."""
    closes = [100.0 + 1.6 * i for i in range(90)]
    if rsi_target_low:
        # 8 down bars of 2.5: drags RSI(14) to ~44.1, which is inside the
        # (36.26, 45) band - above the conditional floor so it is reachable
        # in the real world, below the new threshold so it fires. A 50-period
        # EMA 60+ points below is untouched, so `close > ema50` still holds.
        last = closes[-1]
        closes += [last - 2.5 * (i + 1) for i in range(8)]
    else:
        closes += [closes[-1] + 1.6 * (i + 1) for i in range(8)]
    n = len(closes)
    return {
        'closes': closes,
        'opens': [c - 0.2 for c in closes],
        'highs': [c + 1.0 for c in closes],
        'lows': [c - 1.0 for c in closes],
        'volumes': [1000.0] * n,
        'timestamps': [1_700_000_000_000 + i * 86_400_000 for i in range(n)],
    }


def test_rsi_extreme_fires_on_a_pullback_in_an_uptrend():
    w = _pullback_in_uptrend()
    from indicators.rsi import latest_rsi
    from indicators.ema import latest_ema
    rsi = latest_rsi(w['closes'], 14)
    ema50 = latest_ema(w['closes'], 50)
    # The window really is the intended geometry, not an accident.
    assert w['closes'][-1] > ema50, 'fixture is not in an uptrend'
    assert CONDITIONAL_RSI_FLOOR < rsi < 45.0, f'fixture RSI is {rsi}'

    sig = RsiExtreme().scan(w)
    assert sig is not None, 'rsi_extreme still does not fire on its own thesis'
    assert sig.stop < sig.entry < sig.target, 'convention 8: stop below entry'


def test_rsi_extreme_would_not_have_fired_under_the_old_threshold():
    """Pins that the test above is testing the FIX and not something else."""
    from indicators.rsi import latest_rsi
    w = _pullback_in_uptrend()
    assert latest_rsi(w['closes'], 14) >= 35.0, (
        'the old threshold would have fired here too - this fixture pins '
        'nothing')


def test_rsi_extreme_still_rejects_a_bar_with_no_pullback():
    assert RsiExtreme().scan(_pullback_in_uptrend(rsi_target_low=False)) is None


@pytest.mark.parametrize('ticker', ['AAPL', 'MSFT', 'SPY', 'XLU', 'JNJ'])
def test_rsi_extreme_fires_somewhere_on_real_daily_data(ticker):
    """Convention 3: a synthetic fixture proves the logic, not the market.

    Scanned over the FULL daily series (not the sweep's last-20% test slice,
    which is starved to a median of ONE bar per series - see D-269), the
    fixed threshold must produce at least one signal on a large-cap name.
    Zero here would mean 45 is the next 35."""
    path = DATA / f'{ticker}_1d.csv'
    if not path.exists():
        pytest.skip(f'{path.name} not in backtest/data')
    candles = load_csv(str(path), tf='1d')
    if len(candles) < 400:
        pytest.skip(f'{ticker} has only {len(candles)} daily bars')

    harness = VectorizedBacktestHarness({})
    ind = precompute_indicators(candles)
    strategy = RsiExtreme()
    fires = sum(1 for i in range(100, ind.n)
                if strategy.scan(harness._make_window(ind, i)) is not None)
    assert fires > 0, f'rsi_extreme fired 0 times on {ticker} 1d ({ind.n} bars)'


# ============================================================ R-008 / D-274

@pytest.mark.parametrize('bar_seconds,label', [
    (300, '5m'), (900, '15m'), (3600, '1h'), (86400, '1d'),
])
def test_bar_seconds_inferred_from_a_regular_grid(bar_seconds, label):
    ts = [1_700_000_000_000 + i * bar_seconds * 1000 for i in range(200)]
    assert _bar_seconds(ts) == float(bar_seconds), label


def test_bar_seconds_survives_weekend_gaps():
    """Median, not mean: equity series have two-day holes every week and a
    mean would silently inflate the inferred bar size."""
    ts, t = [], 1_700_000_000_000
    for week in range(6):
        for _ in range(5):
            ts.append(t)
            t += 86_400_000
        t += 2 * 86_400_000          # weekend
    assert _bar_seconds(ts) == 86400.0


def test_c2_horizons_are_time_not_bar_counts():
    """The units bug, pinned. `24 * 4` bars is 4 days on 1h and 8 hours on
    5m; the Friday anchor is then unreachable and the strategy fails 100% of
    sub-hourly trigger bars in total silence."""
    c2 = WeekendVacuumReversion
    assert _bars_for(c2.ANCHOR_LOOKBACK_SECONDS, 3600) == 96      # unchanged on 1h
    assert _bars_for(c2.ANCHOR_LOOKBACK_SECONDS, 900) == 384      # was 96
    assert _bars_for(c2.ANCHOR_LOOKBACK_SECONDS, 300) == 1152     # was 96
    assert _bars_for(c2.WEEK_SECONDS, 3600) == 168                # unchanged on 1h
    assert _bars_for(c2.WEEK_SECONDS, 900) == 672                 # was 168


def test_c2_min_bars_for_timeframe_and_the_reachability_it_exposes():
    """D-274's uncomfortable half. Fixing the lookback is not enough: the
    harness reads the CLASS constant `min_bars` (840, the 1h value) before it
    knows the timeframe, and hands scan() a window of max(SCAN_WINDOW,
    min_bars) bars. On 15m the strategy genuinely needs 3,360 bars, so it can
    never clear its own history guard inside an 840-bar window. 3,360 and
    10,080 both exceed MAX_STRATEGY_WINDOW, which means the honest label for
    sub-hourly C2 is NOT_TESTED (convention 11).

    The harness now DOES call min_bars_for - see
    tests/test_d276_d279_min_bars_wiring.py for the wiring test. This one still
    guards the strategy-side numbers those decisions rest on."""
    c2 = WeekendVacuumReversion
    assert c2.min_bars == 840, 'existing harness wiring keys on the 1h value'
    assert c2.min_bars_for(3600) == 840
    assert c2.min_bars_for(900) == 3360
    assert c2.min_bars_for(300) == 10080

    supplied = max(SCAN_WINDOW, c2.min_bars)
    assert c2.min_bars_for(900) > supplied, (
        'if this ever passes, 15m became reachable inside the default window '
        'and the D-274 caveat can be retired')
    assert c2.min_bars_for(900) > MAX_STRATEGY_WINDOW
    assert c2.min_bars_for(300) > MAX_STRATEGY_WINDOW


def _c2_15m_window(n=4000, weekend_drop=-0.05):
    """A 15m series ending Sunday 22:00 UTC with a large, quiet down weekend.

    5+ weeks long so the time-aware history guard (3,360 bars) is satisfied -
    which is exactly the window the harness does NOT currently supply, hence
    this test builds it directly rather than going through run_sweep.
    """
    import datetime as _dt_mod
    import math
    end = _dt_mod.datetime(2025, 3, 30, 22, 0, tzinfo=_dt_mod.timezone.utc)
    step = 900
    ts = [int((end.timestamp() - (n - 1 - i) * step) * 1000) for i in range(n)]

    closes, volumes = [], []
    for i, t in enumerate(ts):
        d = _dt_mod.datetime.fromtimestamp(t / 1000, tz=_dt_mod.timezone.utc)
        # A ~1% weekly swing so the 12-weekend baseline has a NON-ZERO median
        # move (a flat baseline makes the 1.5x gate degenerate), plus a tiny
        # intraday wobble.
        base = (100.0 * (1 + 0.01 * math.sin(2 * math.pi * i / 672.0))
                + 0.5 * (i % 96) / 96.0)
        weekend = d.weekday() >= 5
        closes.append(base)
        volumes.append(300.0 if weekend else 1000.0)

    # The final weekend: a big, quiet slide from Friday 20:00 to the last bar.
    fri_cut = None
    for i in range(n - 1, -1, -1):
        d = _dt_mod.datetime.fromtimestamp(ts[i] / 1000, tz=_dt_mod.timezone.utc)
        if d.weekday() == 4 and d.hour == 20 and d.minute == 0:
            fri_cut = i
            break
    assert fri_cut is not None
    span = n - 1 - fri_cut
    anchor_px = closes[fri_cut]
    for k in range(1, span + 1):
        closes[fri_cut + k] = anchor_px * (1 + weekend_drop * k / span)
        volumes[fri_cut + k] = 50.0                   # bottom-decile volume

    return {
        'closes': closes,
        'opens': [c for c in closes],
        'highs': [c * 1.001 for c in closes],
        'lows': [c * 0.999 for c in closes],
        'volumes': volumes,
        'timestamps': [float(t) for t in ts],
    }


def test_c2_anchor_resolves_on_15m_after_the_units_fix():
    """The pre-fix 96-bar lookback reaches back 24 hours on 15m bars, so from
    a Sunday 22:00 trigger it cannot see Friday at all. 384 bars can."""
    from strategies.builtin.strategy_lab import _dt
    w = _c2_15m_window()
    ts = w['timestamps']
    m = len(ts)

    def anchor(lookback):
        j, fri, sun = m - 1, None, None
        while j >= 0 and j >= m - lookback:
            d = _dt(ts[j])
            if sun is None and d.weekday() == 6 and d.hour <= 20:
                sun = j
            if d.weekday() == 4 and d.hour <= 20:
                fri = j
                break
            j -= 1
        return fri, sun

    assert anchor(96)[0] is None, 'pre-fix fixture does not pin the bug'
    fri, sun = anchor(_bars_for(WeekendVacuumReversion.ANCHOR_LOOKBACK_SECONDS, 900))
    assert fri is not None and sun is not None and sun > fri


def test_c2_fires_on_15m_when_given_the_history_it_actually_needs():
    sig = WeekendVacuumReversion().scan(_c2_15m_window())
    assert sig is not None, 'C2 still cannot fire on 15m after the units fix'
    assert sig.stop < sig.entry < sig.target


def test_c2_still_rejects_an_up_weekend_because_v1_is_long_only():
    """Not a bug: spot cannot be shorted. It is the measured reason C2 fires
    so rarely (36 of 46 fully-qualified 1h vacuums were up moves), and it is
    the kill condition C2 will be retired against."""
    assert WeekendVacuumReversion().scan(
        _c2_15m_window(weekend_drop=+0.05)) is None


def test_c2_cannot_fire_on_daily_bars_by_construction():
    """Measured, not smoothed over: daily bars stamp at hour 0/4/5 UTC, and
    C2's trigger needs a Sunday bar at hour >= 22. No lookback fix reaches
    this. C2 is an hourly-or-finer strategy; on 1d/1wk it is structurally
    dead and the graveyard should say NOT_TESTED, not FAIL."""
    from strategies.builtin.strategy_lab import _dt
    path = DATA / 'BTC_USD_1d.csv'
    if not path.exists():
        pytest.skip('BTC_USD_1d.csv not in backtest/data')
    candles = load_csv(str(path), tf='1d')
    hours = {_dt(c['ts']).hour for c in candles}
    assert hours and max(hours) < 22, (
        f'daily bars stamp at hours {sorted(hours)}; if any is >= 22 this '
        f'claim needs re-measuring')


# ============================================================ R-009 / D-275

def test_stale_reason_string_is_emitted_by_no_live_code_path():
    """The justification for archiving. If a live gate could still emit this
    string, the rows would not be stale and archiving them would be hiding
    evidence rather than filing it.

    The string does still appear in the tree, in three places that only
    REFERENCE it as a historical constant (the snapshot reader, the archiver,
    and the judge's stale-row test). What must not exist is a reason-emitting
    code path.

    The gate string changed again when min_bars_for() was wired in: the reason
    is now built from a `need` clause that names the timeframe-resolved
    requirement and, when it differs, the class constant it replaced. Asserting
    on the format literals is the point - if the gate is reworded again, this
    test must fail so that WHICH rows are stale gets re-derived rather than
    assumed."""
    harness = (ROOT / 'backtest' / 'vectorized_harness.py').read_text()
    assert STALE_REASON not in harness, (
        'vectorized_harness.py can still emit the stale reason; these rows '
        'are not stale and must not be archived')
    for literal in ('{need}, series has {ind.n}',
                    '{need}, max strategy window is {MAX_STRATEGY_WINDOW}',
                    'needs {min_bars} bars at this bar size '):
        assert literal in harness, (
            f'the current NOT_TESTED gate string changed ({literal!r} is gone) '
            '- re-derive which rows are stale before trusting the archive')

    r = subprocess.run(
        ['grep', '-rIl', '--include=*.py', STALE_REASON, str(ROOT)],
        capture_output=True, text=True)
    allowed = {'snapshot_graveyard.py', 'archive_c2_stale_rows.py',
               'test_judge.py', Path(__file__).name}
    unexpected = [h for h in r.stdout.split() if Path(h).name not in allowed]
    assert not unexpected, (
        f'new reference to the stale reason string in: {unexpected}')


def test_c2_stale_rows_are_archived_not_deleted():
    if not ARCHIVE.exists():
        pytest.skip('run backtest/archive_c2_stale_rows.py first')
    payload = json.loads(ARCHIVE.read_text())
    acct = payload['accounting']

    assert len(payload['entries']) == 9042
    assert acct['c2_stale_archived'] == 9042
    # Convention 20: the partition is asserted, not assumed.
    assert acct['rows_scanned'] == acct['c2_rows'] + acct['non_c2_rows']
    assert acct['c2_rows'] == (acct['c2_stale_archived']
                               + acct['c2_current_gate_reason']
                               + acct['c2_other'])
    for e in payload['entries']:
        assert e['strategy'] == 'C2'
        assert e['verdict'] == 'NOT_TESTED'
        assert e.get('not_tested_reason') == STALE_REASON


def test_archive_is_portable_json_not_just_python_json():
    """Convention 19: json.loads accepts Infinity and NaN, no other parser
    does. Round-tripping through the strict writer proves portability."""
    if not ARCHIVE.exists():
        pytest.skip('run backtest/archive_c2_stale_rows.py first')
    payload = json.loads(ARCHIVE.read_text())
    json.dumps(payload, allow_nan=False)   # raises on any non-finite float
