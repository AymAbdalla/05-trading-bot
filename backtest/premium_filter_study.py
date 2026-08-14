"""Is "skip expensive contracts" a real edge or a small-account artifact?

Aym's hypothesis: the $500 account's inadvertent refusal of high-premium
trades behaved like risk management, and the LOGIC might be extractable as a
deliberate rule.

The naive test (compare PF of a filtered run vs an unfiltered run) is
invalid: filtering changes which trades happen AND shifts every subsequent
entry, so the two runs trade divergent populations. That is what produced the
misleading 1.93-vs-1.45 result in the balance sweep.

THE VALID TEST: forget filtered-vs-unfiltered. Take every option trade the
UNFILTERED strategy takes, bucket those trades by their entry premium
percentile, and measure expectancy per bucket. If expensive-premium trades
systematically lose more than cheap ones across many tickers, the rule is
real and the small account stumbled onto something. If expectancy is flat
across buckets, the four skipped losers were luck.

Mechanism if real: premium here is driven by trailing realized volatility.
Expensive premium = you are buying after a volatility spike. Volatility
mean-reverts, so the option you bought decays as vol normalizes. That is a
documented way to overpay, and it would show up as a real gradient.

CAVEAT: this model has no IV smile, so premium tracks realized vol only. In
live markets premium also carries the smile and the variance risk premium.
Direction should survive; magnitude should not be trusted.

Usage: python3 backtest/premium_filter_study.py
"""
import json
import logging
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from backtest.data_loader import load_csv
from backtest.vectorized_harness import VectorizedBacktestHarness, precompute_indicators
from backtest.options_overlay import run_option_overlay
from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'backtest', 'data')

# Liquid, optionable names across sectors. Daily bars so the option horizon
# (30 DTE) is meaningful relative to the bar size.
TICKERS = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'JPM', 'XOM',
           'JNJ', 'WMT', 'ADBE', 'AMD', 'CAT', 'DIS', 'BA', 'ORCL', 'PFE',
           'KO', 'PEP', 'CVX', 'HD', 'INTC', 'CSCO', 'T', 'VZ']
STRATEGIES = ['grid_1.0atr', 'breakout_20', 'rsi_extreme', 'bollinger_reversion']
N_BUCKETS = 5
BUDGET = 25_000     # large enough that affordability never filters anything


def collect_trades():
    with open(os.path.join(ROOT, 'config.yaml')) as f:
        config = yaml.safe_load(f)
    harness = VectorizedBacktestHarness(config)
    lookup = {s.name: s for s in ENTRY_STRATEGIES_EXPANDED}

    rows = []
    for ticker in TICKERS:
        path = os.path.join(DATA_DIR, f'{ticker}_1d.csv')
        if not os.path.exists(path):
            continue
        candles = load_csv(path)
        if len(candles) < 400:
            continue
        ind = precompute_indicators(candles)   # full history, more trades
        for sname in STRATEGIES:
            strategy = lookup.get(sname)
            if strategy is None:
                continue
            signals = harness.scan_all_bars(strategy, ind)
            res = run_option_overlay(strategy, ind, ticker, '1d', signals=signals,
                                     budget_usd=BUDGET)
            for t in res.trades:
                premium_per_contract = t.premium_in * 100
                # Normalize premium by the underlying price: a $5 option on a
                # $500 stock is CHEAP, on a $50 stock is expensive. Comparing
                # raw dollars across tickers would just rank share prices.
                rows.append({
                    'ticker': ticker, 'strategy': sname,
                    'premium_pct_of_spot': t.premium_in / t.underlying_entry,
                    'premium_per_contract': premium_per_contract,
                    'pnl_per_contract': t.pnl_net / t.contracts,
                    'return_on_premium': ((t.premium_out - t.premium_in)
                                          / t.premium_in),
                    'exit_reason': t.exit_reason,
                })
    return rows


def bucket_analysis(rows, key='premium_pct_of_spot'):
    rows = sorted(rows, key=lambda r: r[key])
    n = len(rows)
    size = n // N_BUCKETS
    out = []
    for b in range(N_BUCKETS):
        lo = b * size
        hi = n if b == N_BUCKETS - 1 else (b + 1) * size
        chunk = rows[lo:hi]
        if not chunk:
            continue
        rets = [r['return_on_premium'] for r in chunk]
        wins = sum(1 for r in rets if r > 0)
        out.append({
            'bucket': b + 1,
            'n': len(chunk),
            'premium_pct_range': (round(chunk[0][key] * 100, 3),
                                  round(chunk[-1][key] * 100, 3)),
            'mean_return_on_premium': round(statistics.mean(rets) * 100, 2),
            'median_return_on_premium': round(statistics.median(rets) * 100, 2),
            'win_rate': round(wins / len(chunk) * 100, 1),
            'stdev': round(statistics.pstdev(rets) * 100, 2),
        })
    return out


def main():
    print('Collecting option trades across tickers and strategies...')
    rows = collect_trades()
    if not rows:
        print('no trades collected')
        return 1
    print(f'{len(rows)} option trades collected '
          f'({len({r["ticker"] for r in rows})} tickers, '
          f'{len({r["strategy"] for r in rows})} strategies)\n')

    buckets = bucket_analysis(rows)
    print('EXPECTANCY BY ENTRY-PREMIUM BUCKET (cheap -> expensive)')
    print('premium measured as % of spot, so tickers are comparable\n')
    print(f"{'bucket':>7} {'n':>6} {'premium % of spot':>20} "
          f"{'mean ret':>10} {'median':>9} {'win%':>7}")
    for b in buckets:
        rng = f"{b['premium_pct_range'][0]}-{b['premium_pct_range'][1]}%"
        print(f"{b['bucket']:>7} {b['n']:>6} {rng:>20} "
              f"{b['mean_return_on_premium']:>9.2f}% {b['median_return_on_premium']:>8.2f}% "
              f"{b['win_rate']:>6.1f}%")

    cheap, expensive = buckets[0], buckets[-1]
    gap = cheap['mean_return_on_premium'] - expensive['mean_return_on_premium']
    print(f"\ncheapest bucket mean return: {cheap['mean_return_on_premium']:.2f}%")
    print(f"priciest bucket mean return: {expensive['mean_return_on_premium']:.2f}%")
    print(f"gap: {gap:.2f} percentage points")

    # Is the gradient monotone, or is this two noisy endpoints?
    means = [b['mean_return_on_premium'] for b in buckets]
    monotone = all(means[i] >= means[i + 1] for i in range(len(means) - 1))
    print(f"monotone decreasing across all {len(buckets)} buckets: {monotone}")

    out = os.path.join(ROOT, 'research', 'premium_filter_study.json')
    with open(out, 'w') as f:
        json.dump({'trades': len(rows), 'buckets': buckets,
                   'cheap_minus_expensive_pp': round(gap, 2),
                   'monotone': monotone}, f, indent=2)
    print(f'\nsaved: {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
