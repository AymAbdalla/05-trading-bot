"""Run SPEC 5.6 inversion tests over a completed graveyard.

Usage: python3 backtest/run_inversions.py [graveyard.json]
Default input: research/graveyard/v0_graveyard_full.json
Output:        research/graveyard/inversions.json
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from backtest.inversion import test_inversions
from backtest.data_loader import load_csv
from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
from strategies.builtin.strategy_lab import STRATEGY_LAB_STRATEGIES

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logging.getLogger('backtest.data_loader').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'backtest', 'data')


def load_for(ticker: str, timeframe: str):
    """Resolve a graveyard (ticker, timeframe) back to candles."""
    if '/' in ticker:  # crypto pair from the Binance merger
        from backtest.run_incremental_graveyard import load_binance_merged
        return load_binance_merged(ticker.replace('/', ''), timeframe)
    path = os.path.join(DATA_DIR, f'{ticker}_{timeframe}.csv')
    return load_csv(path) if os.path.exists(path) else []


def main():
    graveyard = (sys.argv[1] if len(sys.argv) > 1
                 else os.path.join(ROOT, 'research', 'graveyard', 'v0_graveyard_full.json'))
    if not os.path.exists(graveyard):
        logger.error(f'graveyard not found: {graveyard}')
        return 1

    with open(os.path.join(ROOT, 'config.yaml')) as f:
        config = yaml.safe_load(f)

    lookup = {s.name: s for s in list(ENTRY_STRATEGIES_EXPANDED) + list(STRATEGY_LAB_STRATEGIES)}
    report = test_inversions(
        graveyard_path=graveyard,
        data_loader_fn=load_for,
        strategy_lookup=lookup,
        output_path=os.path.join(ROOT, 'research', 'graveyard', 'inversions.json'),
        config=config,
    )
    print(f"\nEligible after F2 gate: {report['candidates_eligible']}")
    cap = report.get('cap_info', {})
    if cap.get('capped'):
        print(f"CAP: {cap['eligible']} eligible candidates, "
              f"max_candidates={cap['max_candidates']}, testing first "
              f"{cap['max_candidates']} ({cap['dropped_by_cap']} dropped)")
    for reason, count in sorted(cap.get('skipped_within_cap_by_reason', {}).items(),
                                key=lambda kv: -kv[1]):
        print(f"  skipped within cap ({count}): {reason}")
    print(f"Tested: {report['tested']}, beat buy-and-hold: {report['beat_buy_hold']}")
    for r in report['results'][:10]:
        print(f"  {r['strategy']:<28s} {r['ticker']:<10s} {r['timeframe']:<4s} "
              f"edge=${r['edge_usd']:+.2f} exits={r['exits_taken']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
