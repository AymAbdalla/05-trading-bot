"""Does account balance / position size change strategy economics?

Sweeps the same strategy and signals across account balances for BOTH cost
structures the project touches:

  1. Equity/crypto spot: fees are a PERCENTAGE of notional.
  2. Long options: commissions are a FIXED DOLLAR AMOUNT per contract,
     often with a per-ORDER minimum, and contracts are indivisible.

Prediction going in (from research/2026-08-13-position-size-and-costs.md):
percentage fees cancel out exactly, fixed fees do not. This script measures
rather than assumes, and it isolates the three mechanisms that CAN make size
matter even when the headline fee rate does not:

  a) order minimums  - max(min_fee, per_contract * n) gets cheaper per
     contract as n grows
  b) integer rounding - you cannot buy 2.7 contracts, so small balances
     leave budget unspent (capital that earns nothing)
  c) affordability    - below one contract's premium you cannot trade at all

Usage: python3 backtest/balance_sweep.py [TICKER] [TIMEFRAME]
"""
import logging
import os
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
BALANCES = [500, 1_000, 2_500, 5_000, 10_000, 25_000, 100_000]


def sweep_spot(strategy, ind, ticker, tf, config, signals):
    print('\n=== SPOT (percentage fees: 0.10% each way) ===')
    print(f"{'notional':>10} {'trades':>7} {'PF':>8} {'win%':>7} {'return%':>9} "
          f"{'net PnL':>12} {'fees':>11}")
    base = None
    for bal in BALANCES:
        c = dict(config)
        c['risk'] = dict(config.get('risk', {}))
        c['risk']['notional_cap_usd'] = bal
        h = VectorizedBacktestHarness(c)
        r = h.run_strategy(strategy, ind, ticker, tf, 'fixed_2r',
                           precomputed_signals=signals)
        if not r.trades:
            print(f'{bal:>10,} {"no trades":>7}')
            continue
        fees = sum(t.fee_cost for t in r.trades)
        pf = r.profit_factor
        print(f'{bal:>10,} {r.trade_count:>7} {pf:>8.4f} {r.win_rate*100:>6.1f}% '
              f'{r.strategy_return_pct:>8.3f}% {r.total_pnl:>12,.2f} {fees:>11,.2f}')
        if base is None:
            base = (pf, r.win_rate, round(r.strategy_return_pct, 6))
        else:
            same = (round(pf, 6), round(r.win_rate, 6),
                    round(r.strategy_return_pct, 6)) == (round(base[0], 6),
                                                         round(base[1], 6), base[2])
            if not same:
                print('   ^ DIVERGENCE from the smallest balance - investigate')
    print('  -> percentage fees: quality metrics identical, dollars scale linearly')


def sweep_options(strategy, ind, ticker, tf, signals, otm=0.05, dte=30,
                  per_contract=0.65, order_min=0.0):
    label = (f'commission ${per_contract}/contract'
             + (f', ${order_min} order minimum' if order_min else ', no order minimum'))
    print(f'\n=== LONG CALLS ({label}) ===')
    print(f"{'budget':>10} {'trades':>7} {'contracts':>10} {'unspent%':>9} "
          f"{'comm%prem':>10} {'PF':>8} {'net PnL':>12}")
    for bal in BALANCES:
        r = run_option_overlay(strategy, ind, ticker, tf, signals=signals,
                               otm_pct=otm, dte=dte, budget_usd=bal,
                               commission_per_contract=per_contract,
                               order_minimum=order_min)
        if not r.trades:
            print(f'{bal:>10,} {"no trades (cannot afford 1 contract)":>7}')
            continue
        contracts = sum(t.contracts for t in r.trades) / len(r.trades)
        # Capital that could not be deployed because contracts are integers.
        unspent = sum(bal - t.premium_in * 100 * t.contracts for t in r.trades)
        unspent_pct = unspent / (bal * len(r.trades)) * 100
        pf = r.profit_factor
        pf_s = 'inf' if pf == float('inf') else f'{pf:.4f}'
        print(f'{bal:>10,} {r.trade_count:>7} {contracts:>10.1f} {unspent_pct:>8.1f}% '
              f'{r.commission_pct_of_premium:>9.2f}% {pf_s:>8} {r.total_pnl:>12,.2f}')


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else 'AAPL'
    tf = sys.argv[2] if len(sys.argv) > 2 else '1d'
    path = os.path.join(ROOT, 'backtest', 'data', f'{ticker}_{tf}.csv')
    if not os.path.exists(path):
        print(f'no data: {path}')
        return 1
    with open(os.path.join(ROOT, 'config.yaml')) as f:
        config = yaml.safe_load(f)

    candles = load_csv(path)
    ind = precompute_indicators(candles[int(len(candles) * 0.8):])
    strategy = next(s for s in ENTRY_STRATEGIES_EXPANDED if s.name == 'grid_1.0atr')
    h = VectorizedBacktestHarness(config)
    signals = h.scan_all_bars(strategy, ind)

    print(f'{ticker} {tf} | strategy={strategy.name} | '
          f'{ind.n} test bars | signals={sum(1 for s in signals if s)}')
    sweep_spot(strategy, ind, ticker, tf, config, signals)
    sweep_options(strategy, ind, ticker, tf, signals, per_contract=0.65, order_min=0.0)
    sweep_options(strategy, ind, ticker, tf, signals, per_contract=0.65, order_min=1.0)
    return 0


if __name__ == '__main__':
    sys.exit(main())
