"""Pooled analysis: judge rare patterns across tickers instead of per ticker.

THE PROBLEM THIS SOLVES (found 2026-08-13 by the silent assertions):

**13 of 35 strategies NEVER once reached the 20-trade verdict floor in
212,058 runs.** The SPEC's own core candlestick patterns are among them:

    morning_star    0 of 6,237 runs reached 20 trades (max ever: 6)
    piercing_line   0 of 6,237 runs reached 20 trades (max ever: 8)
    bullish_engulfing  17 of 6,237 runs (0.27%)
    hammer          18 of 6,237 runs (0.29%)

This is not because the patterns are bad. It is because a per-(ticker,
timeframe) verdict asks a 251-bar test window to produce 20 instances of a
pattern that fires on ~1% of bars. The arithmetic makes it impossible. Every
one of those runs was recorded as FAIL, which reads as "this pattern does not
work" when the truth is "this test could not answer the question."

THE FIX: pool. A candlestick pattern's edge is a property of the PATTERN, not
of AAPL specifically. Aggregating every bullish_engulfing trade across 180
tickers turns 1.1 trades per run into thousands - enough to actually measure.

WHAT POOLING COSTS (be honest about it):
- It assumes the pattern behaves similarly across instruments. That is a real
  assumption, and it is exactly what the per-ticker view was testing. So
  report BOTH: the pooled estimate for power, and the per-ticker dispersion
  to show whether pooling was legitimate.
- Pooled results are dominated by whichever tickers produce the most trades,
  so a pooled number can be one liquid name in disguise. The concentration
  column exists to expose that.
"""
import collections
import json
import logging
import math
import os
import statistics
import sys
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.asset_class_analysis import asset_class  # single shared mapping

logger = logging.getLogger(__name__)

MIN_POOLED_TRADES = 150   # SPEC 5.3 acceptance bar for a real verdict

# total_pnl_usd is a DOLLAR figure sized against notional (spot) for
# EQUITY/ETF/CRYPTO but against MARGIN or PREMIUM (a much smaller base) for
# FUTURES/OPTIONS - backtest/instruments.py's whole point. Pooling them would
# let one MES contract's $170 swing silently outvote a hundred $100 spot
# clips in the same pnl_per_trade average (ROADMAP P0.4's "denominators are
# different quantities"). Excluded by default here, the same call
# cross_sectional.py already makes for pooled dollar cells; asset_class_
# analysis.py is the module that DOES analyze these rows, keyed by class.
POOLED_DOLLAR_EXCLUDED_CLASSES = ('FUTURES', 'OPTIONS')


def pool(entries: List[dict], by=('strategy', 'exit_config'),
        exclude_asset_classes=POOLED_DOLLAR_EXCLUDED_CLASSES) -> List[dict]:
    """Aggregate result rows across tickers/timeframes.

    Profit factor cannot be averaged: it must be rebuilt from summed gross
    profit and gross loss. The stored rows carry total_pnl_usd and win_rate
    but not gross P/L, so pooled PF is reconstructed from per-row PnL as a
    lower-fidelity proxy and labelled as such.
    """
    groups = collections.defaultdict(list)
    for e in entries:
        if e.get('verdict') not in ('PASS', 'FAIL', 'PASS_BENCHMARK'):
            continue
        if not e.get('trades'):
            continue
        if exclude_asset_classes and asset_class(e) in exclude_asset_classes:
            continue
        groups[tuple(e.get(k) for k in by)].append(e)

    out = []
    for key, rows in groups.items():
        trades = sum(r['trades'] for r in rows)
        pnl = sum(r.get('total_pnl_usd') or 0.0 for r in rows)
        wins = sum((r.get('win_rate') or 0) * r['trades'] for r in rows)
        # Dispersion across tickers: does the pooled number describe them all?
        pnls = [r.get('total_pnl_usd') or 0.0 for r in rows]
        winners = sum(1 for p in pnls if p > 0)
        # Concentration: is the pooled result one ticker in disguise?
        by_ticker = collections.Counter()
        for r in rows:
            by_ticker[r['ticker']] += r['trades']
        top_share = (by_ticker.most_common(1)[0][1] / trades) if trades else 0

        out.append({
            **{k: v for k, v in zip(by, key)},
            'pooled_trades': trades,
            'runs_pooled': len(rows),
            'tickers': len(by_ticker),
            'pooled_pnl_usd': round(pnl, 2),
            'pooled_win_rate': round(wins / trades, 4) if trades else 0.0,
            'pnl_per_trade': round(pnl / trades, 4) if trades else 0.0,
            'profitable_run_fraction': round(winners / len(rows), 3),
            'top_ticker_trade_share': round(top_share, 3),
            'judgeable': trades >= MIN_POOLED_TRADES,
        })
    return sorted(out, key=lambda r: -r['pooled_trades'])


def per_strategy_summary(entries: List[dict]) -> List[dict]:
    return pool(entries, by=('strategy',))


def main():
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = (sys.argv[1] if len(sys.argv) > 1
            else os.path.join(root, 'research', 'graveyard', 'v0_graveyard_full.json'))
    if not os.path.exists(path):
        print(f'not found: {path}')
        return 1
    with open(path) as f:
        entries = json.load(f).get('entries', [])

    rows = per_strategy_summary(entries)
    print(f'POOLED ACROSS ALL TICKERS AND TIMEFRAMES ({len(entries):,} raw rows)\n')
    print(f"{'strategy':<24s} {'trades':>9} {'tickers':>8} {'win%':>7} "
          f"{'pnl/trade':>10} {'profitable runs':>16} {'top ticker':>11} {'judge?':>7}")
    for r in rows:
        print(f"{r['strategy']:<24s} {r['pooled_trades']:>9,} {r['tickers']:>8} "
              f"{r['pooled_win_rate']*100:>6.1f}% {r['pnl_per_trade']:>10.3f} "
              f"{r['profitable_run_fraction']*100:>15.0f}% "
              f"{r['top_ticker_trade_share']*100:>10.0f}% "
              f"{'YES' if r['judgeable'] else 'no':>7}")

    unjudgeable = [r['strategy'] for r in rows if not r['judgeable']]
    print(f"\nstill unjudgeable even pooled (<{MIN_POOLED_TRADES} trades): "
          f"{unjudgeable or 'none'}")

    out = os.path.join(root, 'research', 'graveyard', 'pooled.json')
    with open(out, 'w') as f:
        json.dump({'min_pooled_trades': MIN_POOLED_TRADES,
                   'by_strategy': rows,
                   'by_strategy_exit': pool(entries)}, f, indent=2)
    print(f'saved: {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
