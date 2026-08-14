"""Constraint sensitivity: does gating harder actually select better trades?

THE REQUEST (Aym, 2026-08-13): give each strategy an AGGRESSIVE (loose, fires
often) and a CONSERVATIVE (harsh, fires rarely) constraint set and see whether
performance changes.

WHY THIS IS A DIAGNOSTIC, NOT AN OPTIMIZATION
The useless version asks "which constraint level scores best" and keeps the
winner. That is parameter search on results you have already seen, and it
manufactures edge exactly like subset-filtering does (see
research/2026-08-13-conditional-edge-finding.md).

The useful version asks a falsifiable question about MECHANISM:

    If a strategy has real edge, tightening its entry gate should raise
    PnL PER TRADE while lowering trade count. Fewer, better trades.

    If the gate carries no information, tightening only shrinks the sample.
    Per-trade PnL stays flat at the cost floor and frequency drops.

So the shape of the curve IS the finding, independent of which level "wins".
A flat curve across a 10x change in selectivity says the confirmation stack
is not selecting for anything.

WHY THE CONFIRMATION STACK IS THE KNOB
The harness already owns a uniform gate (regime filter, RSI ceiling, volume
ratio). Varying THAT applies the same tightening to all 44 strategies without
editing 44 files, and keeps the comparison honest: every strategy faces the
identical change. Strategy-internal thresholds are Forge's territory (SPEC
5.5 edit ladder), deliberately not touched here.

Usage: python3 backtest/constraint_sweep.py [--tickers N] [--exits fixed_2r,time_8c]
"""
import collections
import json
import logging
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from backtest.data_loader import load_csv
from backtest.vectorized_harness import (VectorizedBacktestHarness,
                                         precompute_indicators)
from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
from strategies.builtin.strategy_lab import STRATEGY_LAB_STRATEGIES
from strategies.builtin.strategy_lab_v2 import STRATEGY_LAB_V2_STRATEGIES

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'backtest', 'data')

ALL_STRATEGIES = (ENTRY_STRATEGIES_EXPANDED + STRATEGY_LAB_STRATEGIES
                  + STRATEGY_LAB_V2_STRATEGIES)

# Three levels of the SAME gate. Ordered loose -> harsh.
CONSTRAINT_LEVELS = {
    'AGGRESSIVE': {
        'apply_confirmation_stack': False,   # no gate at all: take every signal
        'require_regime_uptrend': False,
        'rsi_max_entry': 100.0,
        'volume_min_ratio': 0.0,
    },
    'BASE': {
        'apply_confirmation_stack': True,
        'require_regime_uptrend': True,
        'rsi_max_entry': 70.0,
        'volume_min_ratio': 1.2,
    },
    'CONSERVATIVE': {
        'apply_confirmation_stack': True,
        'require_regime_uptrend': True,
        'rsi_max_entry': 45.0,      # only deeply oversold entries
        'volume_min_ratio': 2.0,    # demand a real volume surge
    },
}

# A spread across asset classes and timeframes, not a cherry-picked set.
# The sector tag resolves each series to its cost regime (D-235).
DEFAULT_TICKERS = [
    ('AAPL', '1h', None), ('MSFT', '1h', None), ('NVDA', '1h', None),
    ('JPM', '1h', None), ('XOM', '1h', None),
    ('SPY', '5m', 'Index ETFs'), ('QQQ', '5m', 'Index ETFs'),
    ('AAPL', '5m', None),
    ('BTC_USD', '1d', 'Crypto (Yahoo)'), ('ETH_USD', '1d', 'Crypto (Yahoo)'),
    ('GLD', '1d', 'Commodity ETFs'), ('XLK', '1d', 'Sector ETFs'),
    ('ES_F', '1h', 'Futures'), ('CL_F', '1h', 'Futures'),
]
DEFAULT_EXITS = ['fixed_2r', 'time_8c', 'trailing_atr']


def build_harness(level: str, base_config: dict) -> VectorizedBacktestHarness:
    cfg = json.loads(json.dumps(base_config))     # deep copy
    conf = cfg.setdefault('strategy', {}).setdefault('confirmation', {})
    conf.update(CONSTRAINT_LEVELS[level])
    return VectorizedBacktestHarness(cfg)


def main():
    with open(os.path.join(ROOT, 'config.yaml')) as f:
        base_config = yaml.safe_load(f)

    exits = DEFAULT_EXITS
    if '--exits' in sys.argv:
        exits = sys.argv[sys.argv.index('--exits') + 1].split(',')
    tickers = DEFAULT_TICKERS
    if '--tickers' in sys.argv:
        tickers = tickers[:int(sys.argv[sys.argv.index('--tickers') + 1])]

    # Venue-accurate costs (D-235): each series is charged its own regime.
    base_config['use_cost_model'] = True

    series = []
    for ticker, tf, sector in tickers:
        path = os.path.join(DATA_DIR, f'{ticker}_{tf}.csv')
        if not os.path.exists(path):
            continue
        candles = load_csv(path)
        if len(candles) < 400:
            continue
        series.append((ticker, tf, sector, precompute_indicators(candles)))
    if not series:
        print('no usable series')
        return 1

    print(f'{len(ALL_STRATEGIES)} strategies x {len(series)} series x '
          f'{len(exits)} exits x 3 constraint levels\n')

    # level -> strategy -> [trades, pnl]
    agg = {lvl: collections.defaultdict(lambda: [0, 0.0]) for lvl in CONSTRAINT_LEVELS}
    harnesses = {lvl: build_harness(lvl, base_config) for lvl in CONSTRAINT_LEVELS}
    scanner = harnesses['AGGRESSIVE']       # scan gate is identical across levels
    total = len(series) * len(ALL_STRATEGIES)
    done = 0
    for ticker, tf, sector, ind in series:
        for strat in ALL_STRATEGIES:
            done += 1
            if getattr(strat, 'min_bars', 0) > 260:
                continue
            try:
                # Signals do NOT depend on the confirmation stack (applied
                # inside run_strategy, after scan), so scan ONCE and reuse
                # across all three levels.
                sigs = scanner.scan_all_bars(strat, ind)
            except Exception:
                continue
            for level, h in harnesses.items():
                for ex in exits:
                    try:
                        r = h.run_strategy(strat, ind, ticker, tf, ex,
                                           precomputed_signals=sigs,
                                           sector=sector)
                    except Exception:
                        continue
                    cell = agg[level][strat.name]
                    cell[0] += r.trade_count
                    cell[1] += r.total_pnl
        print(f'  {ticker} {tf}: {done}/{total} strategy-series done', flush=True)

    print(f"{'level':<14s}{'trades':>10s}{'pnl':>12s}{'pnl/trade':>12s}"
          f"{'strategies firing':>19s}")
    print('-' * 67)
    totals = {}
    for level in ('AGGRESSIVE', 'BASE', 'CONSERVATIVE'):
        n = sum(v[0] for v in agg[level].values())
        p = sum(v[1] for v in agg[level].values())
        firing = sum(1 for v in agg[level].values() if v[0] > 0)
        totals[level] = (n, p, p / n if n else 0.0)
        print(f'{level:<14s}{n:>10,}{p:>12,.2f}{(p/n if n else 0):>12.4f}{firing:>19}')

    a, b, c = totals['AGGRESSIVE'], totals['BASE'], totals['CONSERVATIVE']
    print(f'\nselectivity: AGGRESSIVE fires {a[0]/max(c[0],1):.1f}x more than CONSERVATIVE')
    print(f'per-trade PnL across the three levels: '
          f'{a[2]:+.4f} -> {b[2]:+.4f} -> {c[2]:+.4f}')

    improvement = c[2] - a[2]
    print('\nDIAGNOSTIC')
    if improvement > 0.05:
        print(f'  Tightening the gate IMPROVES per-trade PnL by {improvement:+.4f}.')
        print('  The confirmation stack is selecting for something real. Worth')
        print('  testing whether the improvement survives on unseen instruments')
        print('  (backtest/conditional_edge.py).')
    elif improvement < -0.05:
        print(f'  Tightening the gate WORSENS per-trade PnL by {improvement:+.4f}.')
        print('  The stack is filtering out the better trades - it is')
        print('  anti-selective and should be inverted or removed.')
    else:
        print(f'  Per-trade PnL is FLAT across a {a[0]/max(c[0],1):.0f}x change in '
              f'selectivity (delta {improvement:+.4f}).')
        print('  The confirmation stack is not selecting for anything. It changes')
        print('  how OFTEN you trade, not how WELL. That is the signature of a')
        print('  gate applied to signals with no gross edge to sort.')

    # Per-strategy detail: any strategy whose curve differs from the aggregate
    # is the interesting case, so surface the biggest movers explicitly.
    print(f"\n{'strategy':<26s}{'AGGR $/t':>11s}{'BASE $/t':>11s}{'CONS $/t':>11s}"
          f"{'delta':>9s}{'trades A/C':>14s}")
    print('-' * 82)
    rows = []
    for name in sorted(agg['BASE']):
        vals = {}
        for lvl in ('AGGRESSIVE', 'BASE', 'CONSERVATIVE'):
            n, p = agg[lvl][name]
            vals[lvl] = (n, p / n if n else None)
        if vals['AGGRESSIVE'][1] is None or vals['CONSERVATIVE'][1] is None:
            continue
        if vals['AGGRESSIVE'][0] < 100 or vals['CONSERVATIVE'][0] < 30:
            continue      # too small to read
        rows.append((vals['CONSERVATIVE'][1] - vals['AGGRESSIVE'][1], name, vals))
    rows.sort(reverse=True)
    for delta, name, vals in rows[:12]:
        print(f"{name:<26s}{vals['AGGRESSIVE'][1]:>11.4f}"
              f"{vals['BASE'][1] if vals['BASE'][1] is not None else 0:>11.4f}"
              f"{vals['CONSERVATIVE'][1]:>11.4f}{delta:>9.4f}"
              f"{str(vals['AGGRESSIVE'][0]) + '/' + str(vals['CONSERVATIVE'][0]):>14s}")

    from backtest.cost_model import COST_MODEL_VERSION
    out = os.path.join(ROOT, 'research', 'graveyard', 'constraint_sweep.json')
    with open(out, 'w') as f:
        json.dump({'cost_model_version': COST_MODEL_VERSION,
                   'levels': CONSTRAINT_LEVELS, 'totals': totals,
                   'per_strategy': {n: {l: agg[l][n] for l in CONSTRAINT_LEVELS}
                                    for n in agg['BASE']}}, f, indent=1)
    print(f'\nsaved: {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
