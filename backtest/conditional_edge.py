"""Conditional-edge search, done the only way that proves anything.

THE REQUEST (Aym, 2026-08-13): test strategies across sectors, classes and
conditions; pool them generically; then keep the subsets where the pattern
wins, so we know a strategy works under those specific tickers/classes/
conditions.

THE HAZARD: "keep the subsets where it wins" applied to results you have
already seen is the textbook definition of selection bias. With 44 strategies
x 180 tickers x 11 exits, some subset ALWAYS looks profitable by chance. You
can manufacture a gorgeous equity curve from pure noise this way, and it will
fail the moment it meets new data. Reporting such a filtered result as edge
is the single most common way backtests lie.

THE DISCIPLINE THAT MAKES IT VALID: select on one set of instruments, verify
on a DIFFERENT set you did not look at.

  1. Split tickers randomly into SELECT and VERIFY halves (seeded).
  2. On SELECT only, find (strategy, condition) cells that beat the cost
     floor with adequate sample.
  3. Evaluate those EXACT cells on VERIFY.
  4. Compare the survival rate against the null: if selecting on noise, roughly
     half of selected cells should survive by chance (a coin flip on which
     side of the floor they land). Survival meaningfully above that is
     evidence; survival at or below it is data mining exposed.

Repeat over many random splits so the answer is not itself one lucky split.

Conditions supported (all derivable from what the graveyard already stores):
  asset class, sector, timeframe, exit config, and combinations of them.

Usage: python3 backtest/conditional_edge.py [graveyard.json] [--splits N]
"""
import collections
import json
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.asset_class_analysis import asset_class

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COST_FLOOR = -0.30      # round-trip cost per trade on $100 notional
MIN_TRADES_SELECT = 100  # sample needed before a cell may be SELECTED
MIN_TRADES_VERIFY = 50   # sample needed before a cell may be JUDGED on verify
N_SPLITS = 20


def underlying(ticker: str) -> str:
    return (ticker or '').split('/')[0].replace('_USD', '').replace('_F', '')


def cell_keys(e: dict):
    """The condition slices tested. Each is a hypothesis about WHERE a
    strategy might work. Keep them mechanistic and few - every extra slice
    is another comparison the correction has to pay for."""
    cls = asset_class(e)
    sector = e.get('sector') or 'Unknown'
    return {
        'class': (e['strategy'], cls),
        'sector': (e['strategy'], sector),
        'timeframe': (e['strategy'], e['timeframe']),
        'class_x_timeframe': (e['strategy'], cls, e['timeframe']),
        'class_x_exit': (e['strategy'], cls, e.get('exit_config')),
    }


def aggregate(entries, key_kind, ticker_filter=None):
    agg = collections.defaultdict(lambda: [0, 0.0])
    for e in entries:
        if e.get('verdict') not in ('PASS', 'FAIL', 'PASS_BENCHMARK'):
            continue
        if not e.get('trades'):
            continue
        if ticker_filter is not None and underlying(e.get('ticker')) not in ticker_filter:
            continue
        k = cell_keys(e)[key_kind]
        agg[k][0] += e['trades']
        agg[k][1] += e.get('total_pnl_usd') or 0.0
    return {k: (n, p, p / n) for k, (n, p) in agg.items() if n}


def run_split(entries, assets, key_kind, seed):
    rng = random.Random(seed)
    shuffled = sorted(assets)
    rng.shuffle(shuffled)
    half = len(shuffled) // 2
    select_set, verify_set = set(shuffled[:half]), set(shuffled[half:])

    sel = aggregate(entries, key_kind, select_set)
    ver = aggregate(entries, key_kind, verify_set)

    # Selection: cells that beat the cost floor on adequate sample.
    picked = [k for k, (n, _p, ppt) in sel.items()
              if n >= MIN_TRADES_SELECT and ppt > COST_FLOOR]
    survived, judged, verify_ppts = 0, 0, []
    for k in picked:
        if k not in ver:
            continue
        n, _p, ppt = ver[k]
        if n < MIN_TRADES_VERIFY:
            continue
        judged += 1
        verify_ppts.append(ppt)
        if ppt > COST_FLOOR:
            survived += 1
    return {
        'selected': len(picked), 'judged_on_verify': judged,
        'survived': survived,
        'survival_rate': survived / judged if judged else None,
        'mean_verify_ppt': statistics.mean(verify_ppts) if verify_ppts else None,
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    path = args[0] if args else os.path.join(ROOT, 'research', 'graveyard',
                                             'v0_graveyard_full.json')
    n_splits = N_SPLITS
    if '--splits' in sys.argv:
        n_splits = int(sys.argv[sys.argv.index('--splits') + 1])
    if not os.path.exists(path):
        print(f'not found: {path}')
        return 1
    with open(path) as f:
        entries = json.load(f).get('entries', [])

    assets = {underlying(e.get('ticker')) for e in entries if e.get('ticker')}
    assets.discard('')
    print(f'{len(entries):,} rows | {len(assets)} distinct underlyings | '
          f'{n_splits} random select/verify splits\n')
    print('METHOD: pick winning cells on HALF the underlyings, then judge those')
    print('exact cells on the OTHER half. Selecting on noise should survive at')
    print('about 50%. Meaningfully above 50% is evidence of real conditional')
    print('edge; at or below 50% is selection bias caught in the act.\n')

    print(f"{'condition slice':<22s}{'selected':>10s}{'judged':>8s}"
          f"{'survived':>10s}{'survival':>10s}{'mean verify $/trade':>21s}")
    print('-' * 81)

    summary = {}
    for key_kind in ('class', 'sector', 'timeframe', 'class_x_timeframe', 'class_x_exit'):
        runs = [run_split(entries, assets, key_kind, seed) for seed in range(n_splits)]
        judged = sum(r['judged_on_verify'] for r in runs)
        surv = sum(r['survived'] for r in runs)
        sel = sum(r['selected'] for r in runs)
        rates = [r['survival_rate'] for r in runs if r['survival_rate'] is not None]
        ppts = [r['mean_verify_ppt'] for r in runs if r['mean_verify_ppt'] is not None]
        rate = surv / judged if judged else None
        summary[key_kind] = {
            'selected_total': sel, 'judged_total': judged, 'survived_total': surv,
            'survival_rate': rate,
            'survival_rate_stdev': statistics.pstdev(rates) if len(rates) > 1 else None,
            'mean_verify_pnl_per_trade': statistics.mean(ppts) if ppts else None,
        }
        rate_s = f'{rate:.1%}' if rate is not None else 'n/a'
        ppt_s = f'{statistics.mean(ppts):+.3f}' if ppts else 'n/a'
        print(f'{key_kind:<22s}{sel:>10}{judged:>8}{surv:>10}{rate_s:>10}{ppt_s:>21}')

    print('\nINTERPRETATION')
    any_real = False
    for kind, s in summary.items():
        r = s['survival_rate']
        if r is None:
            continue
        if r > 0.65 and s['judged_total'] >= 30:
            any_real = True
            print(f'  {kind}: survival {r:.0%} - ABOVE chance, worth a proper '
                  f'out-of-sample test on held-out TIME as well')
        elif r < 0.55:
            print(f'  {kind}: survival {r:.0%} - at or below coin flip. Winners '
                  f'selected here do NOT generalize to unseen instruments.')
    if not any_real:
        print('\n  No condition slice produced above-chance survival. Filtering to')
        print('  winning tickers/classes on this library manufactures results that')
        print('  do not transfer. This is exactly the failure the method exists to')
        print('  detect, and it is the expected outcome when gross edge is zero.')

    mean_ppt = [s['mean_verify_pnl_per_trade'] for s in summary.values()
                if s['mean_verify_pnl_per_trade'] is not None]
    if mean_ppt:
        print(f'\n  Mean PnL/trade of SELECTED cells when judged on unseen '
              f'underlyings: {statistics.mean(mean_ppt):+.3f} '
              f'(cost floor {COST_FLOOR:+.2f})')

    out = os.path.join(ROOT, 'research', 'graveyard', 'conditional_edge.json')
    with open(out, 'w') as f:
        json.dump({'cost_floor': COST_FLOOR, 'splits': n_splits,
                   'min_trades_select': MIN_TRADES_SELECT,
                   'min_trades_verify': MIN_TRADES_VERIFY,
                   'summary': summary}, f, indent=1)
    print(f'\nsaved: {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
