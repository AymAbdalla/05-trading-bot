"""Pool strategies BY ASSET CLASS, not by ticker.

Aym's insight (2026-08-13): a strategy that cannot reach 20 trades on any
single ticker is not ticker-specific - it is a GENERIC pattern. Judging it
per ticker asks the wrong question. The right unit is the pattern itself,
and the interesting question is whether the pattern behaves DIFFERENTLY
across asset classes.

This is the SPEC's v2 ticker-fingerprinting thesis in its simplest testable
form. If bullish_engulfing has edge on crypto and none on utilities, that is
a routing rule, and routing rules are what the fingerprinting work would
formalize. If a strategy is uniformly at zero across every class, no amount
of routing saves it.

WHAT POOLING BY CLASS COSTS (state it, do not hide it):
- Within a class, tickers still differ. A crypto number dominated by BTC is
  a BTC number wearing a costume. The `top_ticker_share` column exposes that.
- More slices means more comparisons. 44 strategies x 4 classes is 176
  hypotheses, and the best of those will look good by chance. The report
  prints the expected-best-by-chance reminder for exactly this reason.

Usage: python3 backtest/asset_class_analysis.py [graveyard.json] [--strategy NAME]
"""
import collections
import json
import math
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, ROOT)
from backtest.instruments import resolve_asset_class  # single shared mapping

MIN_TRADES_TO_JUDGE = 150     # SPEC 5.3 bar, applied to the POOLED count


def asset_class(entry: dict) -> str:
    # Prefer the stamp the harness wrote; fall back to resolving for
    # pre-stamp entries.
    stamped = entry.get('asset_class')
    if stamped and stamped != 'FLAT':
        return stamped
    return resolve_asset_class(entry.get('ticker') or '',
                               entry.get('sector'))


def analyze(entries, strategy_filter=None):
    rows = collections.defaultdict(list)
    for e in entries:
        if e.get('verdict') not in ('PASS', 'FAIL', 'PASS_BENCHMARK'):
            continue
        if not e.get('trades'):
            continue
        if strategy_filter and e['strategy'] != strategy_filter:
            continue
        rows[(e['strategy'], asset_class(e))].append(e)

    out = []
    for (strategy, cls), rs in rows.items():
        trades = sum(r['trades'] for r in rs)
        pnl = sum(r.get('total_pnl_usd') or 0.0 for r in rs)
        wins = sum((r.get('win_rate') or 0) * r['trades'] for r in rs)
        by_ticker = collections.Counter()
        for r in rs:
            by_ticker[r['ticker']] += r['trades']
        top_share = by_ticker.most_common(1)[0][1] / trades if trades else 0.0
        per_run = [(r.get('total_pnl_usd') or 0.0) / r['trades'] for r in rs if r['trades']]

        # LEAVE-ONE-ASSET-OUT robustness. A pooled number carried by a single
        # underlying is that underlying wearing a costume. Verified case:
        # bullish_harami on CRYPTO reported -0.036/trade (best cell in the
        # whole study) but was -0.417 without SOL, i.e. worse than the cost
        # floor. Group by UNDERLYING (BTC/USDT and BTC_USD are one asset).
        def underlying(t):
            return (t or '').split('/')[0].replace('_USD', '').replace('_F', '')
        by_asset = collections.defaultdict(lambda: [0, 0.0])
        for r in rs:
            a = by_asset[underlying(r['ticker'])]
            a[0] += r['trades']
            a[1] += r.get('total_pnl_usd') or 0.0
        worst_without = None
        for asset, (an, ap) in by_asset.items():
            rem_n = trades - an
            if rem_n <= 0:
                continue
            rem_ppt = (pnl - ap) / rem_n
            if worst_without is None or rem_ppt < worst_without[1]:
                worst_without = (asset, rem_ppt)
        out.append({
            'strategy': strategy, 'class': cls,
            'trades': trades, 'tickers': len(by_ticker), 'runs': len(rs),
            'pnl_per_trade': pnl / trades if trades else 0.0,
            'win_rate': wins / trades if trades else 0.0,
            'top_ticker_share': top_share,
            'dispersion': statistics.pstdev(per_run) if len(per_run) > 1 else 0.0,
            'judgeable': trades >= MIN_TRADES_TO_JUDGE,
            'assets': len(by_asset),
            'worst_drop_asset': worst_without[0] if worst_without else None,
            'pnl_per_trade_worst_drop': worst_without[1] if worst_without else None,
            'carried_by_one_asset': bool(
                worst_without and (pnl / trades if trades else 0) - worst_without[1] > 0.15),
        })
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    path = args[0] if args else os.path.join(ROOT, 'research', 'graveyard',
                                             'v0_graveyard_full.json')
    strategy_filter = None
    if '--strategy' in sys.argv:
        strategy_filter = sys.argv[sys.argv.index('--strategy') + 1]
    if not os.path.exists(path):
        print(f'not found: {path}')
        return 1
    with open(path) as f:
        entries = json.load(f).get('entries', [])

    rows = analyze(entries, strategy_filter)
    if not rows:
        print('no rows')
        return 1

    classes = ['CRYPTO', 'EQUITY', 'ETF', 'FUTURES']
    by_strategy = collections.defaultdict(dict)
    for r in rows:
        by_strategy[r['strategy']][r['class']] = r

    print(f'PnL PER TRADE BY ASSET CLASS  (round-trip cost is about -$0.30)')
    print(f'a strategy at -0.30 everywhere has NO gross edge; it pays the toll and nothing else\n')
    header = f"{'strategy':<26s}" + ''.join(f'{c:>13s}' for c in classes) + f"{'spread':>9s}"
    print(header)
    print('-' * len(header))

    ranked = []
    for strategy, cls_rows in by_strategy.items():
        vals = {c: cls_rows[c]['pnl_per_trade'] for c in classes
                if c in cls_rows and cls_rows[c]['judgeable']}
        spread = (max(vals.values()) - min(vals.values())) if len(vals) > 1 else 0.0
        ranked.append((spread, strategy, cls_rows, vals))
    ranked.sort(reverse=True)

    for spread, strategy, cls_rows, vals in ranked:
        cells = ''
        for c in classes:
            r = cls_rows.get(c)
            if not r:
                cells += f"{'-':>13s}"
            elif not r['judgeable']:
                cells += f"{'n=' + str(r['trades']):>13s}"
            else:
                cells += f"{r['pnl_per_trade']:>13.3f}"
        print(f'{strategy:<26s}{cells}{spread:>9.3f}')

    print('\nlegend: a number is judgeable pooled PnL/trade; "n=NN" means fewer '
          f'than {MIN_TRADES_TO_JUDGE} pooled trades even across the whole class; '
          '"-" means no data in that class.')

    judged = [r for r in rows if r['judgeable']]
    print(f'\njudgeable strategy-class cells: {len(judged)} of {len(rows)}')
    if judged:
        n = len(judged)
        print(f'expected best-by-chance at {n} comparisons: about '
              f'{math.sqrt(2 * math.log(n)):.1f} sigma. Treat the top row as the '
              f'base rate, not a discovery.')
        best = max(judged, key=lambda r: r['pnl_per_trade'])
        print(f"\nbest cell: {best['strategy']} on {best['class']} at "
              f"{best['pnl_per_trade']:+.3f}/trade over {best['trades']:,} trades, "
              f"{best['tickers']} tickers, top ticker {best['top_ticker_share']:.0%} of trades")
        if best.get('carried_by_one_asset'):
            print(f"  WARNING: drop {best['worst_drop_asset']} and it becomes "
                  f"{best['pnl_per_trade_worst_drop']:+.3f}/trade. This cell is "
                  f"one asset wearing a costume, not a class-level effect.")

        carried = [r for r in judged if r.get('carried_by_one_asset')]
        if carried:
            print(f"\ncells carried by a single underlying ({len(carried)} of "
                  f"{len(judged)} judgeable) - read these as one-asset results:")
            for r in sorted(carried, key=lambda r: -r['pnl_per_trade'])[:8]:
                print(f"  {r['strategy']:<24s} {r['class']:<8s} "
                      f"{r['pnl_per_trade']:+.3f} -> {r['pnl_per_trade_worst_drop']:+.3f} "
                      f"without {r['worst_drop_asset']}")

    out = os.path.join(ROOT, 'research', 'graveyard', 'asset_class.json')
    with open(out, 'w') as f:
        json.dump({'min_trades_to_judge': MIN_TRADES_TO_JUDGE, 'cells': rows}, f, indent=1)
    print(f'saved: {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
