"""Tests for the honest graveyard summary: raw pass ROWS must never be
reported as distinct findings."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.summarize_graveyard import summarize


def _entry(strategy, ticker, tf, cfg, verdict='PASS', **kw):
    e = {'strategy': strategy, 'ticker': ticker, 'timeframe': tf,
         'exit_config': cfg, 'verdict': verdict, 'trades': 30}
    e.update(kw)
    return e


def test_one_strategy_many_exit_configs_is_one_finding(tmp_path):
    """The real case that motivated this: grid on ADBE 1h passed 11 exit
    configs. That is ONE finding, not 11."""
    entries = [_entry('grid_1.0atr', 'ADBE', '1h', f'cfg{i}') for i in range(7)]
    entries += [_entry('grid_2.0atr', 'ADBE', '1h', f'cfg{i}') for i in range(4)]
    entries += [_entry('x', 'Y', '1d', 'cfg0', verdict='FAIL')]
    path = tmp_path / 'g.json'
    path.write_text(json.dumps({'entries': entries}))

    s = summarize(str(path))
    assert s['raw_pass_rows'] == 11
    # Two parameterizations of one idea on one ticker/timeframe
    assert s['distinct_findings']['strategy_x_ticker_x_timeframe'] == 2
    assert s['distinct_findings']['strategy_family_x_ticker_x_timeframe'] == 1
    assert s['distinct_findings']['tickers_with_any_pass'] == 1
    assert s['pass_concentration_top5'][0]['pass_rows'] == 11


def test_benchmark_passes_counted_separately(tmp_path):
    entries = [_entry('dca_14', 'ETH', '15m', 'c', verdict='PASS_BENCHMARK',
                      is_benchmark=True),
               _entry('real', 'AAPL', '1d', 'c')]
    path = tmp_path / 'g.json'
    path.write_text(json.dumps({'entries': entries}))
    s = summarize(str(path))
    assert s['benchmark_pass_rows'] == 1
    assert s['raw_pass_rows'] == 1  # the benchmark is NOT in the discovery count


def test_expected_max_z_grows_with_grid_size(tmp_path):
    """At 15k tests chance alone yields a ~4.4 sigma best result; the summary
    must say so next to any headline."""
    small = [_entry('a', 'T', '1d', 'c', verdict='FAIL') for _ in range(10)]
    big = [_entry('a', f'T{i}', '1d', 'c', verdict='FAIL') for i in range(15000)]
    for entries, lo, hi in ((small, 2.0, 2.5), (big, 4.2, 4.6)):
        path = tmp_path / f'g{len(entries)}.json'
        path.write_text(json.dumps({'entries': entries}))
        z = summarize(str(path))['multiple_comparisons']['expected_max_z_under_null']
        assert lo < z < hi


def test_gate_version_stamped_on_entries():
    """Entries must carry the gate version so results from different
    PASS/FAIL semantics can never be silently pooled (the incremental
    resume key has no code fingerprint)."""
    from backtest.vectorized_harness import VectorizedBacktestHarness, GATE_VERSION
    from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
    candles = [{'ts': i * 900000, 'open': 100.0 + i * 0.05, 'high': 100.6 + i * 0.05,
                'low': 99.4 + i * 0.05, 'close': 100.2 + i * 0.05, 'volume': 100.0}
               for i in range(300)]
    h = VectorizedBacktestHarness({})
    reports = h.run_sweep(candles, 'X', '15m',
                          strategies=ENTRY_STRATEGIES_EXPANDED[:1],
                          exit_configs=['fixed_2r'])
    assert reports[0]["gate_version"] == GATE_VERSION >= 2


def test_not_tested_entries_also_carry_gate_version():
    """Every entry must be attributable to a gate era, including skips."""
    from backtest.vectorized_harness import VectorizedBacktestHarness, GATE_VERSION
    from strategies.builtin.strategy_lab import WeekendVacuumReversion
    candles = [{'ts': i * 900000, 'open': 100.0, 'high': 100.5, 'low': 99.5,
                'close': 100.0, 'volume': 100.0} for i in range(150)]
    h = VectorizedBacktestHarness({})
    reports = h.run_sweep(candles, 'X', '15m',
                          strategies=[WeekendVacuumReversion()],
                          exit_configs=['fixed_2r'])
    assert reports[0]['verdict'] == 'NOT_TESTED'
    assert reports[0]['gate_version'] == GATE_VERSION
