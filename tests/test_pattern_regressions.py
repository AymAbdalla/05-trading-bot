"""Regression tests for the 2026-08-12 strategy/pattern fixes.

Each test pins a bug that previously made a strategy dead (never fired) or
wrong (fired on the wrong geometry). The 2026-08-12 test-suite audit found
these four fixes had no pinning tests; this file closes that gap.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators.patterns_all import (upside_tasuki_gap, downside_tasuki_gap,
                                     piercing_line, BULLISH_PATTERNS)
from indicators.patterns import piercing_line as piercing_line_main


def _wrap(opens, closes, spread=0.5):
    highs = [max(o, c) + spread for o, c in zip(opens, closes)]
    lows = [min(o, c) - spread for o, c in zip(opens, closes)]
    return opens, highs, lows, closes


# ---------- tasuki gaps (were logically unsatisfiable: swapped comparisons) ----------

def _uptrend_tasuki(c3_open=128.0, c3_close=126.5):
    """22-bar uptrend, then green c1 (122->125), gap-up green c2 (127->129),
    red c3 opening in c2's body, closing into (default) the gap."""
    opens = [100.0 + i for i in range(22)] + [122.0, 127.0, c3_open]
    closes = [101.0 + i for i in range(22)] + [125.0, 129.0, c3_close]
    return _wrap(opens, closes)


def test_upside_tasuki_fires_on_textbook():
    o, h, l, c = _uptrend_tasuki()
    assert upside_tasuki_gap(o, h, l, c)['found'] is True


def test_upside_tasuki_rejects_full_gap_fill():
    # c3 closes BELOW c1's close: gap fully filled -> not a tasuki
    o, h, l, c = _uptrend_tasuki(c3_close=124.0)
    assert upside_tasuki_gap(o, h, l, c)['found'] is False


def test_upside_tasuki_rejects_open_outside_c2_body():
    # c3 opens above c2's close (outside the body)
    o, h, l, c = _uptrend_tasuki(c3_open=130.0)
    assert upside_tasuki_gap(o, h, l, c)['found'] is False


def test_downside_tasuki_fires_on_textbook():
    opens = [150.0 - i for i in range(22)] + [128.0, 123.0, 122.0]
    closes = [149.0 - i for i in range(22)] + [125.0, 121.0, 123.5]
    o, h, l, c = _wrap(opens, closes)
    assert downside_tasuki_gap(o, h, l, c)['found'] is True


# ---------- piercing line (fired on open < prior CLOSE; SPEC says prior LOW) ----------

def _piercing(open_offset_from='low'):
    opens = [150.0 - i for i in range(22)] + [130.0, 0.0]
    closes = [149.0 - i for i in range(22)] + [127.0, 129.0]
    o, h, l, c = _wrap(opens, closes, spread=0.3)
    prior_low = l[-2]
    prior_close = c[-2]
    o[-1] = prior_low - 0.1 if open_offset_from == 'low' else prior_close - 0.1
    l[-1] = min(o[-1], c[-1]) - 0.3
    return o, h, l, c


def test_piercing_fires_when_open_below_prior_low():
    o, h, l, c = _piercing('low')
    assert piercing_line(o, h, l, c)['found'] is True
    assert piercing_line_main(o, h, l, c)['found'] is True


def test_piercing_rejects_open_only_below_prior_close():
    o, h, l, c = _piercing('close')
    assert piercing_line(o, h, l, c)['found'] is False
    assert piercing_line_main(o, h, l, c)['found'] is False


# ---------- registry: on_neck / in_neck are NOT long entries ----------

def test_bearish_continuation_patterns_not_registered_bullish():
    assert 'on_neck' not in BULLISH_PATTERNS
    assert 'in_neck' not in BULLISH_PATTERNS


# ---------- FairValueGap (loop was an empty range: strategy was dead) ----------

def test_fair_value_gap_can_fire():
    from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
    fvg = next(s for s in ENTRY_STRATEGIES_EXPANDED if s.name == 'fair_value_gap')
    n = 60
    base = [100.0 + 0.3 * i for i in range(n)]
    opens, closes = list(base), [b + 0.2 for b in base]
    o, h, l, c = _wrap(opens, closes, spread=0.2)
    # 3-candle gap at the end: impulse bar leaves candle1.high < candle3.low,
    # then price retests the gap midpoint.
    h[-4] = c[-4] + 0.2                # candle 1 high
    o[-3], c[-3] = c[-4], c[-4] + 8.0  # impulse bar
    h[-3], l[-3] = c[-3] + 0.2, o[-3] - 0.2
    l[-2] = h[-4] + 4.0                # candle 3 low: gap of 4.0 vs candle 1 high
    o[-2], c[-2] = l[-2] + 0.5, l[-2] + 1.0
    h[-2] = c[-2] + 0.2
    mid = (h[-4] + l[-2]) / 2          # retest bar at the gap midpoint
    o[-1], c[-1] = mid + 0.3, mid
    h[-1], l[-1] = o[-1] + 0.2, c[-1] - 0.2
    sig = fvg.scan({'opens': o, 'highs': h, 'lows': l, 'closes': c,
                    'volumes': [100.0] * n, 'timestamps': list(range(n))})
    assert sig is not None and sig.direction == 'bullish'


# ---------- DCA cadence (was pinned dead by fixed-length scan windows) ----------

def _dca_window(ts_last_bar_index: int, n: int = 60, interval_ms: int = 900000):
    ts = [(ts_last_bar_index - (n - 1) + i) * interval_ms for i in range(n)]
    base = [100.0 + (i % 3) * 0.2 for i in range(n)]
    o, h, l, c = _wrap(list(base), [b + 0.1 for b in base], spread=0.4)
    return {'opens': o, 'highs': h, 'lows': l, 'closes': c,
            'volumes': [100.0] * n, 'timestamps': ts}


def test_dca_fires_on_timestamp_cadence_not_window_length():
    from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
    dca = next(s for s in ENTRY_STRATEGIES_EXPANDED if s.name == 'dca_7')
    # Bar index divisible by 7 (in timestamp units) fires...
    assert dca.scan(_dca_window(700)) is not None
    # ...regardless of window length (the old bug keyed on len(closes),
    # which is CONSTANT once a bounded scan window fills up)
    assert dca.scan(_dca_window(700, n=53)) is not None
    # ...and a non-multiple does not fire
    assert dca.scan(_dca_window(699)) is None


# ---------- C2 min_bars honesty ----------

def test_c2_declares_min_bars_and_sweep_reports_not_tested_on_a_short_series():
    """A series shorter than C2's own min_bars is NOT_TESTED (D-109: could
    not run, not tested-and-failed) - this is now a per-series check, not a
    blanket "min_bars > SCAN_WINDOW" rejection (see the window-widening test
    below); a 150-bar series is genuinely too short for an 840-bar strategy
    either way."""
    from strategies.builtin.strategy_lab import WeekendVacuumReversion
    from backtest.vectorized_harness import (VectorizedBacktestHarness,
                                             SCAN_WINDOW, precompute_indicators)
    assert WeekendVacuumReversion.min_bars == 24 * 7 * 5 > SCAN_WINDOW

    harness = VectorizedBacktestHarness({})
    candles = [{'ts': i * 900000, 'open': 100.0, 'high': 100.5, 'low': 99.5,
                'close': 100.0, 'volume': 100.0} for i in range(150)]
    reports = harness.run_sweep(candles, 'X', '15m',
                                strategies=[WeekendVacuumReversion()],
                                exit_configs=['fixed_2r'])
    assert reports[0]['verdict'] == 'NOT_TESTED'
    assert 'series has 150' in reports[0]['not_tested_reason']


def test_c2_gets_its_own_widened_window_on_a_long_enough_series():
    """The old gate rejected C2 outright because min_bars(840) > SCAN_WINDOW
    (260), even when the actual series had thousands of bars available. Fix:
    scan_all_bars widens the window it hands C2 specifically to
    max(SCAN_WINDOW, min_bars); this proves the WIRING (window width), not
    the firing logic itself (flat synthetic candles never satisfy C2's
    move-size gate, so this cannot and does not assert a signal fires)."""
    from strategies.builtin.strategy_lab import WeekendVacuumReversion
    from backtest.vectorized_harness import (VectorizedBacktestHarness,
                                             SCAN_WINDOW, precompute_indicators)

    harness = VectorizedBacktestHarness({})
    candles = [{'ts': i * 3_600_000, 'open': 100.0, 'high': 100.5, 'low': 99.5,
                'close': 100.0, 'volume': 100.0} for i in range(1000)]
    ind = precompute_indicators(candles)
    strategy = WeekendVacuumReversion()

    windows_seen = []
    orig_scan = strategy.scan
    def spy_scan(window):
        windows_seen.append(len(window['closes']))
        return orig_scan(window)
    strategy.scan = spy_scan

    harness.scan_all_bars(strategy, ind)
    assert max(windows_seen) > SCAN_WINDOW, (
        f'C2 never saw more than {max(windows_seen)} bars; '
        f'its {strategy.min_bars}-bar min_bars was not honored')

    # And the sweep-level gate no longer NOT_TESTs it just for asking for
    # more than SCAN_WINDOW - a long-enough series gets a real attempt.
    reports = harness.run_sweep(candles, 'X', '1h',
                                strategies=[strategy],
                                exit_configs=['fixed_2r'])
    assert reports[0]['verdict'] != 'NOT_TESTED', (
        f"expected a real attempt on 1000 bars, got NOT_TESTED: "
        f"{reports[0].get('not_tested_reason')}")


def test_benchmark_strategies_labeled_not_discoveries():
    """A benchmark (DCA) that clears the gate must be reported as
    PASS_BENCHMARK, never PASS: in a rising market a signal-less strategy
    beats buy-and-hold on timing luck, and the first fresh graveyard run
    produced exactly that (dca_14 on ETH, +$0.31 over 28 trades)."""
    from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
    from backtest.vectorized_harness import VectorizedBacktestHarness
    dca = next(s for s in ENTRY_STRATEGIES_EXPANDED if s.name.startswith('dca_'))
    assert getattr(dca, 'is_benchmark', False) is True

    # Rising market: DCA trades and (usually) clears the gate.
    candles = [{'ts': i * 900000, 'open': 100 * 1.002 ** i, 'high': 100 * 1.002 ** i * 1.01,
                'low': 100 * 1.002 ** i * 0.995, 'close': 100 * 1.002 ** i,
                'volume': 100.0} for i in range(400)]
    h = VectorizedBacktestHarness({})
    reports = h.run_sweep(candles, 'X', '15m', strategies=[dca],
                          exit_configs=['fixed_2r'])
    assert reports[0]['verdict'] in ('PASS_BENCHMARK', 'FAIL')
    assert reports[0].get('is_benchmark') is True
