"""Honest graveyard summary: distinct findings, not raw pass counts.

Why this exists: the first fresh run produced 12 PASS rows that look like 12
discoveries. Eleven of them were the SAME grid strategy family on the SAME
ticker and timeframe (ADBE 1h) across 11 different exit configs. That is one
observation reported eleven times.

This script collapses passes to distinct findings at three levels and puts
them next to what pure chance would produce at this grid size (the
validation review's section 5: in a large grid you should EXPECT a
spectacular-looking best result even when nothing works).

Usage: python3 backtest/summarize_graveyard.py [graveyard.json]
"""
import collections
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def summarize(path: str) -> dict:
    with open(path) as f:
        data = json.load(f)
    entries = data.get('entries', [])
    if not entries:
        return {}

    verdicts = collections.Counter(e.get('verdict') for e in entries)
    tested = [e for e in entries if e.get('verdict') in ('PASS', 'FAIL', 'PASS_BENCHMARK')]
    passes = [e for e in entries if e.get('verdict') == 'PASS']
    bench_passes = [e for e in entries if e.get('verdict') == 'PASS_BENCHMARK']

    # Distinct findings at three collapse levels.
    by_combo = {(e['strategy'], e['ticker'], e['timeframe']) for e in passes}
    by_strategy_ticker = {(e['strategy'], e['ticker']) for e in passes}
    by_ticker = {e['ticker'] for e in passes}
    # Strategy FAMILY: strip trailing parameter suffixes (grid_1.0atr and
    # grid_2.0atr are one idea with two settings, not two ideas).
    def family(name: str) -> str:
        # Strip a trailing parameter token: grid_1.0atr -> grid,
        # breakout_50 -> breakout, dca_14 -> dca, time_8c -> time.
        import re
        return re.sub(r'_[\d.]+[a-z]*$', '', name) or name
    by_family_ticker = {(family(e['strategy']), e['ticker'], e['timeframe']) for e in passes}

    n = len(tested)
    # Expected maximum z-score under the pure null for n independent tests:
    # sqrt(2 ln n). At n=15,000 that is ~4.4 sigma FROM CHANCE ALONE.
    expected_max_z = math.sqrt(2 * math.log(n)) if n > 1 else 0.0

    concentration = collections.Counter(
        (e['ticker'], e['timeframe']) for e in passes).most_common(5)

    # WHY A ROW WAS NOT TESTED (R-002). Two structurally different reasons,
    # kept apart because collapsing them hides the one that is a config
    # choice rather than a data limitation: `unsizable_at_cap` rows would
    # become testable tomorrow by raising notional_cap, whereas a series that
    # is too short stays too short.
    def not_tested_code(e: dict) -> str:
        reason = e.get('not_tested_reason') or ''
        if reason == 'unsizable_at_cap':
            return 'unsizable_at_cap'
        if 'bars' in reason:
            return 'insufficient_bars'
        return 'unspecified'

    nt_counts = collections.Counter(
        not_tested_code(e) for e in entries if e.get('verdict') == 'NOT_TESTED')

    # A zero-trade FAIL is a real verdict by the harness's rule (the strategy
    # was runnable and never signalled), but it is NOT a tested configuration
    # in the sense most readers assume. Publish both numbers so nobody has to
    # guess which one a headline count means.
    tested_with_trades = sum(1 for e in tested if e.get('trades'))

    return {
        'graveyard': os.path.basename(path),
        'entries_total': len(entries),
        'verdict_counts': dict(verdicts),
        'raw_pass_rows': len(passes),
        'benchmark_pass_rows': len(bench_passes),
        'not_tested_breakdown': dict(nt_counts),
        'unsizable_at_cap': nt_counts.get('unsizable_at_cap', 0),
        'tested_rows': len(tested),
        'tested_rows_with_trades': tested_with_trades,
        'distinct_findings': {
            'strategy_x_ticker_x_timeframe': len(by_combo),
            'strategy_family_x_ticker_x_timeframe': len(by_family_ticker),
            'strategy_x_ticker': len(by_strategy_ticker),
            'tickers_with_any_pass': len(by_ticker),
        },
        'pass_concentration_top5': [
            {'ticker_timeframe': f'{t} {tf}', 'pass_rows': c}
            for (t, tf), c in concentration
        ],
        'multiple_comparisons': {
            'tests_completed': n,
            'expected_max_z_under_null': round(expected_max_z, 2),
            'note': (f'With {n} tests, chance alone is expected to produce a '
                     f'best result around {expected_max_z:.1f} sigma. A single '
                     f'impressive row is the base rate, not evidence. Judge '
                     f'must correct on hypotheses GENERATED, and use effective '
                     f'test count (correlated tickers/timeframes are not '
                     f'independent), not raw row count.'),
        },
        'reading_guide': (
            'raw_pass_rows counts (strategy, ticker, timeframe, exit_config) '
            'rows. One strategy that works on one ticker across 9 exit configs '
            'produces 9 rows and ONE finding. Always cite distinct_findings.'
        ),
    }


def main():
    path = (sys.argv[1] if len(sys.argv) > 1
            else os.path.join(ROOT, 'research', 'graveyard', 'v0_graveyard_full.json'))
    if not os.path.exists(path):
        print(f'not found: {path}')
        return 1
    s = summarize(path)
    if not s:
        print('empty graveyard')
        return 1
    out = os.path.join(ROOT, 'research', 'graveyard', 'summary.json')
    with open(out, 'w') as f:
        json.dump(s, f, indent=2)

    print(f"Graveyard: {s['graveyard']}  ({s['entries_total']} entries)")
    print(f"Verdicts: {s['verdict_counts']}")
    print(f"NOT_TESTED breakdown: {s['not_tested_breakdown']}")
    print(f"Tested rows: {s['tested_rows']}  "
          f"(of which {s['tested_rows_with_trades']} actually placed trades)")
    print(f"\nRaw PASS rows:        {s['raw_pass_rows']}")
    print(f"Benchmark PASS rows:  {s['benchmark_pass_rows']} (not discoveries)")
    print("\nDISTINCT FINDINGS (what you should actually cite):")
    for k, v in s['distinct_findings'].items():
        print(f"  {k:<42s} {v}")
    if s['pass_concentration_top5']:
        print("\nPass concentration (a cluster on one ticker is ONE finding):")
        for row in s['pass_concentration_top5']:
            print(f"  {row['ticker_timeframe']:<20s} {row['pass_rows']} rows")
    mc = s['multiple_comparisons']
    print(f"\nMultiple comparisons: {mc['tests_completed']} tests, "
          f"expected best-by-chance ~{mc['expected_max_z_under_null']} sigma")
    print(f"Saved: {out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
