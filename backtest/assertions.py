"""Silent assertions: result-quality tripwires over a finished graveyard.

Two classes of assertion exist in this project (per the harness-validation
review, section 1):

  HARNESS VALIDITY - "does the engine work at all?" Oracle control, fee
  application, look-ahead shift, survivorship, cross-harness agreement.
  These live in validate_harness.py and must pass BEFORE a run is trusted.

  RESULT QUALITY - "is this particular result trustworthy?" They read stored
  result rows and can run any time, retroactively. THAT IS THIS FILE.

These are deliberately NOT told to Forge or Quant (review section 9: hide the
tripwires, publish the physics). An agent that knows the tripwires will avoid
tripping them, and we lose the ability to detect the bug.

Every assertion returns a verdict dict with pass/fail, the evidence, and what
a failure implies. A failure is a claim about the ENGINE or the RESULT SET,
not about a strategy being bad.
"""
import collections
import json
import logging
import math
import os
import sys
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

# Names whose price series are pathological (reverse splits, dilution,
# delisting). A strategy showing real profit here is evidence the HARNESS is
# broken, not that the strategy is good.
QUARANTINE_TICKERS = {'MULN', 'SNDL', 'BBBYQ'}
QUARANTINE_PF_CEILING = 1.3     # review: 2.0 is too generous, tighten

# Leveraged pairs that move in OPPOSITE directions. A strategy that goes long
# both on the same candle is contradictory and indicates broken signal logic.
MIRROR_PAIRS = [('TQQQ', 'SQQQ'), ('SPXL', 'SPXU'), ('SOXL', 'SOXS'),
                ('TNA', 'TZA'), ('LABU', 'LABD'), ('UPRO', 'SPXU')]

WIN_RATE_CEILING = 0.85         # >85% win rate with real stops is suspicious
MIN_TICKERS_FOR_BREADTH = 10
ZERO_TRADE_FRACTION_LIMIT = 0.60  # strategy silent on >60% of tickers = broken


def _tested(entries: List[dict]) -> List[dict]:
    return [e for e in entries if e.get('verdict') in ('PASS', 'FAIL', 'PASS_BENCHMARK')]


def assert_quarantine_canary(entries: List[dict]) -> dict:
    """Profit on a pathological ticker means the engine is wrong."""
    hits = []
    for e in _tested(entries):
        if e.get('ticker') not in QUARANTINE_TICKERS:
            continue
        pf = e.get('pf')
        if pf is None or pf > QUARANTINE_PF_CEILING:   # None encodes infinite
            hits.append({'ticker': e['ticker'], 'strategy': e['strategy'],
                         'timeframe': e['timeframe'], 'exit_config': e.get('exit_config'),
                         'pf': 'inf' if pf is None else pf,
                         'trades': e.get('trades')})
    covered = {e['ticker'] for e in _tested(entries)} & QUARANTINE_TICKERS
    return {
        'assertion': 'quarantine_canary',
        'pass': not hits,
        'quarantine_tickers_present': sorted(covered),
        'violations': hits[:20],
        'violation_count': len(hits),
        'implication': (f'PF > {QUARANTINE_PF_CEILING} on a reverse-split / dilution '
                        'wreck suggests fake fills, missing borrow cost, or a bad '
                        'split adjustment - a HARNESS bug, not an edge.'),
        'note': ('no quarantine tickers in this run - the canary is not deployed'
                 if not covered else ''),
    }


def assert_mirror_pair_contradiction(entries: List[dict]) -> dict:
    """A strategy passing on BOTH sides of an inverse pair, same timeframe, is
    contradictory: they profit under opposite market conditions."""
    by_strategy_tf = collections.defaultdict(set)
    for e in _tested(entries):
        if e.get('verdict') in ('PASS', 'PASS_BENCHMARK'):
            by_strategy_tf[(e['strategy'], e['timeframe'])].add(e['ticker'])
    hits = []
    for (strategy, tf), tickers in by_strategy_tf.items():
        for bull, bear in MIRROR_PAIRS:
            if bull in tickers and bear in tickers:
                hits.append({'strategy': strategy, 'timeframe': tf,
                             'pair': f'{bull}/{bear}'})
    return {
        'assertion': 'mirror_pair_contradiction',
        'pass': not hits,
        'violations': hits,
        'implication': ('the same long strategy cannot genuinely profit on a 3x '
                        'bull ETF and its 3x bear twin over the same window; '
                        'suspect signal logic or data mislabeling.'),
    }


def assert_win_rate_ceiling(entries: List[dict]) -> dict:
    """Very high win rates with real stops usually mean the stop never fires
    (wrong direction, wrong scale) or exits are being resolved optimistically."""
    hits = [{'strategy': e['strategy'], 'ticker': e['ticker'],
             'timeframe': e['timeframe'], 'exit_config': e.get('exit_config'),
             'win_rate': e.get('win_rate'), 'trades': e.get('trades')}
            for e in _tested(entries)
            if (e.get('win_rate') or 0) > WIN_RATE_CEILING and (e.get('trades') or 0) >= 20]
    return {
        'assertion': 'win_rate_ceiling',
        'pass': not hits,
        'violations': hits[:20],
        'violation_count': len(hits),
        'implication': (f'win rate > {WIN_RATE_CEILING:.0%} on 20+ trades with a stop '
                        'on every trade suggests the stop is unreachable or exits '
                        'resolve in the strategy favor.'),
    }


def assert_trade_count_sanity(entries: List[dict]) -> dict:
    """A strategy silent on most tickers is broken, not selective."""
    per_strategy = collections.defaultdict(lambda: {'zero': 0, 'total': 0})
    for e in _tested(entries):
        s = per_strategy[e['strategy']]
        s['total'] += 1
        if (e.get('trades') or 0) == 0:
            s['zero'] += 1
    hits = []
    for strategy, c in per_strategy.items():
        if c['total'] < MIN_TICKERS_FOR_BREADTH:
            continue
        frac = c['zero'] / c['total']
        if frac > ZERO_TRADE_FRACTION_LIMIT:
            hits.append({'strategy': strategy, 'zero_trade_fraction': round(frac, 3),
                         'zero': c['zero'], 'of': c['total']})
    return {
        'assertion': 'trade_count_sanity',
        'pass': not hits,
        'violations': sorted(hits, key=lambda h: -h['zero_trade_fraction']),
        'implication': (f'a strategy producing zero trades on more than '
                        f'{ZERO_TRADE_FRACTION_LIMIT:.0%} of tickers has a broken '
                        'trigger condition, not a selective one.'),
    }


def assert_duplicate_strategies(entries: List[dict]) -> dict:
    """Two 'different' strategies with identical trade counts everywhere are
    probably the same idea, which inflates apparent breadth of evidence and
    corrupts the effective test count."""
    sig = collections.defaultdict(dict)
    for e in _tested(entries):
        sig[e['strategy']][(e['ticker'], e['timeframe'], e.get('exit_config'))] = e.get('trades')
    names = sorted(sig)
    hits = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared = set(sig[a]) & set(sig[b])
            if len(shared) < 20:
                continue
            same = sum(1 for k in shared if sig[a][k] == sig[b][k])
            frac = same / len(shared)
            if frac > 0.95:
                hits.append({'a': a, 'b': b, 'identical_fraction': round(frac, 3),
                             'compared_on': len(shared)})
    return {
        'assertion': 'duplicate_strategies',
        'pass': not hits,
        'violations': hits,
        'implication': ('near-identical trade counts across many tickers means '
                        'these are one idea counted twice; effective test count '
                        'and any breadth claim are overstated.'),
    }


def assert_timeframe_coherence(entries: List[dict]) -> dict:
    """Adjacent timeframes on the same ticker should not disagree wildly. A
    strategy that is excellent on 5m and dead on 15m suggests a resampling
    or alignment bug on one of them."""
    by = collections.defaultdict(dict)
    for e in _tested(entries):
        if (e.get('trades') or 0) >= 10 and e.get('pf') is not None:
            by[(e['strategy'], e['ticker'], e.get('exit_config'))][e['timeframe']] = e['pf']
    hits = []
    for key, tfs in by.items():
        if '5m' in tfs and '15m' in tfs:
            a, b = tfs['5m'], tfs['15m']
            if max(a, b) > 0 and (min(a, b) / max(a, b)) < 0.25 and max(a, b) > 1.2:
                hits.append({'strategy': key[0], 'ticker': key[1],
                             'exit_config': key[2], 'pf_5m': a, 'pf_15m': b})
    return {
        'assertion': 'timeframe_coherence',
        'pass': len(hits) <= max(5, int(0.01 * max(1, len(by)))),
        'violations': hits[:20],
        'violation_count': len(hits),
        'compared': len(by),
        'implication': ('large 5m-vs-15m divergence on the same ticker and exit '
                        'suggests a resampling or timestamp-alignment bug.'),
    }


def assert_gate_version_uniform(entries: List[dict]) -> dict:
    """Results from different PASS/FAIL semantics must never be pooled."""
    versions = collections.Counter(e.get('gate_version') for e in entries)
    return {
        'assertion': 'gate_version_uniform',
        'pass': len(versions) == 1,
        'versions_present': {str(k): v for k, v in versions.items()},
        'implication': ('mixed gate versions mean some verdicts were produced under '
                        'different rules; the graveyard cannot be read as one dataset.'),
    }


def assert_cost_model_version_uniform(entries: List[dict]) -> dict:
    """Results costed under different models must never be pooled.

    A '2026-08-13' equity trade paid ~4bps; a 'flat:taker=0.001' one paid
    ~30bps. Averaging them produces a number that describes NO venue.
    Entries missing the stamp are the pre-cost-model era and count as their
    own version ('unstamped') - so merging an old graveyard into a new run
    trips this immediately.
    """
    versions = collections.Counter(
        e.get('cost_model_version') or 'unstamped' for e in entries)
    return {
        'assertion': 'cost_model_version_uniform',
        'pass': len(versions) == 1,
        'versions_present': {str(k): v for k, v in versions.items()},
        'implication': ('mixed cost-model versions mean the same trade costs '
                        'different amounts in different rows; pooled edges and '
                        'per-trade means are meaningless across them.'),
    }


ASSERTIONS = [
    assert_quarantine_canary,
    assert_mirror_pair_contradiction,
    assert_win_rate_ceiling,
    assert_trade_count_sanity,
    assert_duplicate_strategies,
    assert_timeframe_coherence,
    assert_gate_version_uniform,
    assert_cost_model_version_uniform,
]


def run_all(graveyard_path: str, output_path: str = None) -> dict:
    with open(graveyard_path) as f:
        entries = json.load(f).get('entries', [])
    results = [fn(entries) for fn in ASSERTIONS]
    report = {
        'graveyard': os.path.basename(graveyard_path),
        'entries': len(entries),
        'assertions_run': len(results),
        'failed': [r['assertion'] for r in results if not r['pass']],
        'results': results,
    }
    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(report, f, indent=2)
    return report


def main():
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = (sys.argv[1] if len(sys.argv) > 1
            else os.path.join(root, 'research', 'graveyard', 'v0_graveyard_full.json'))
    if not os.path.exists(path):
        print(f'not found: {path}')
        return 1
    report = run_all(path, os.path.join(root, 'research', 'graveyard', 'assertions.json'))
    print(f"Silent assertions over {report['entries']} entries\n")
    for r in report['results']:
        status = 'PASS' if r['pass'] else 'FAIL'
        extra = ''
        if not r['pass']:
            extra = f" ({r.get('violation_count', len(r.get('violations', [])))} violations)"
        print(f"  [{status}] {r['assertion']}{extra}")
        if r.get('note'):
            print(f"         note: {r['note']}")
        if not r['pass']:
            for v in (r.get('violations') or [])[:3]:
                print(f"         {v}")
            print(f"         -> {r['implication']}")
    print(f"\nfailed: {report['failed'] or 'none'}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
