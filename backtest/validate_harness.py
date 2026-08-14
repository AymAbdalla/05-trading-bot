"""Harness validation: runs control strategies THROUGH the harness and checks assertions.

Implements the blocking assertions A1-A4 from the harness validation review:
  A1  Oracle control      - a strategy that sees the next close, run through the
                            REAL run_strategy/fill/exit pipeline with zero costs,
                            must produce extreme PF. If it doesn't, signal-to-fill
                            wiring is broken.
  A2  Fee application     - the same seeded coin-flip run with and without fees
                            must show strictly lower PF with fees, and net PF must
                            never exceed gross PF. Buy-hold control's single trade
                            must reproduce the harness's own buy-and-hold PnL.
  A3  Look-ahead shift    - the oracle re-run with execution_delay=1 (its foresight
                            now stale) must collapse toward random. If delay doesn't
                            hurt a future-seeing strategy, fills are leaking data.
  A4  Survivorship        - the universe must contain delisted names (currently
                            only quarantine tickers; honest flag, not a hard fail).

DESIGN RULE (the bug this file replaces): every control MUST go through
VectorizedBacktestHarness.run_strategy. A control that computes its own trades
inline validates nothing about the harness. See controls.py for the
control-only future-data channel contract.

This script MUST pass before any graveyard results are treated as durable.
Exit code 0 = all pass, 1 = failures (and the graveyard stays PROVISIONAL).
"""
import json
import logging
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.vectorized_harness import VectorizedBacktestHarness, precompute_indicators
from strategies.builtin.controls import OracleControl, BuyHoldControl, CoinFlipControl
from backtest.data_loader import load_csv

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtest', 'data')

TEST_FILES = [
    ('AAPL_1d.csv', 'AAPL', '1d'),
    ('AAPL_1h.csv', 'AAPL', '1h'),
    ('MSFT_1d.csv', 'MSFT', '1d'),
    ('NVDA_1d.csv', 'NVDA', '1d'),
    ('SPY_1d.csv', 'SPY', '1d'),
    ('TSLA_1d.csv', 'TSLA', '1d'),
    ('BTC_USD_1d.csv', 'BTC_USD', '1d'),
    ('JPM_1d.csv', 'JPM', '1d'),
]

COINFLIP_SEEDS = 10


def _control_harness() -> VectorizedBacktestHarness:
    """Harness with the confirmation stack OFF: controls test wiring and
    accounting, not the regime/RSI/volume filters."""
    return VectorizedBacktestHarness({
        'strategy': {'confirmation': {'apply_confirmation_stack': False}},
        'exchange': {'fees': {'taker': 0.001}, 'slippage': {'market': 0.0005}},
        'risk': {'notional_cap_usd': 100},
    })


def _pf_str(pf: float) -> str:
    return 'inf' if pf == float('inf') else f'{pf:.3f}'


def run_oracle_checks(harness, ind, ticker: str, timeframe: str) -> dict:
    """A1 (wiring) + A3 (shift) + A2 (net<gross), all through run_strategy."""
    # A1: zero-cost oracle. Sees the next close, exits at it (time_1c).
    r_free = harness.run_strategy(OracleControl(), ind, ticker, timeframe, 'time_1c',
                                  fee_override=0.0, slippage_override=0.0)
    pf_free = r_free.profit_factor
    a1_pass = bool(r_free.trade_count >= 30 and pf_free > 5.0 and r_free.win_rate > 0.95)

    # A3: same oracle executed one bar late. Its information is stale, so PF
    # must collapse. If delay barely hurts a future-seeing strategy, the fill
    # path is leaking future data.
    r_late = harness.run_strategy(OracleControl(), ind, ticker, timeframe, 'time_1c',
                                  fee_override=0.0, slippage_override=0.0,
                                  execution_delay=1)
    pf_late = r_late.profit_factor
    if pf_free == float('inf'):
        a3_pass = bool(pf_late != float('inf') and pf_late < 3.0)
    else:
        a3_pass = bool(pf_late < pf_free * 0.5 and pf_late < 3.0)

    # A2 (partial): oracle with real costs - net PF strictly below gross PF.
    r_fees = harness.run_strategy(OracleControl(), ind, ticker, timeframe, 'time_1c')
    net_le_gross = bool(r_fees.trade_count == 0 or r_fees.profit_factor <= r_fees.gross_pf)

    return {
        'ticker': ticker, 'timeframe': timeframe,
        'oracle_trades': r_free.trade_count,
        'oracle_pf_zero_cost': None if pf_free == float('inf') else round(pf_free, 2),
        'oracle_win_rate': round(r_free.win_rate * 100, 1),
        'a1_pass': a1_pass,
        'delayed_pf': None if pf_late == float('inf') else round(pf_late, 3),
        'a3_pass': a3_pass,
        'net_le_gross': net_le_gross,
        'pass': bool(a1_pass and a3_pass and net_le_gross),
    }


def run_buyhold_check(harness, ind, ticker: str, timeframe: str) -> dict:
    """A2 (accounting): one hold-to-end trade through the harness must
    reproduce the harness's own buy-and-hold PnL almost exactly (identical
    formula, identical costs), and land within fee+slip of the raw price
    return."""
    r = harness.run_strategy(BuyHoldControl(), ind, ticker, timeframe, 'hold')
    if r.trade_count != 1:
        return {'ticker': ticker, 'timeframe': timeframe, 'pass': False,
                'error': f'expected exactly 1 trade, got {r.trade_count}'}

    # The single trade's net PnL vs the harness's independent BH computation.
    accounting_diff = abs(r.total_pnl - r.buy_hold_pnl_usd)
    # And vs the raw price return: only fees + slippage apart. The allowed
    # difference SCALES with the growth factor g (fees are charged on exit
    # notional too): diff ~= 100*(2*slip*g + fee*(1+g)). A fixed tolerance
    # falsely failed correct runs on high-return series (NVDA +714%).
    ret_diff_pct = abs(r.strategy_return_pct - r.buy_hold_return)
    g = 1.0 + r.buy_hold_return / 100.0
    fee, slip = harness.taker_fee, harness.slippage
    allowed_diff = 100.0 * (2 * slip * g + fee * (1 + g)) * 1.5 + 0.05

    return {
        'ticker': ticker, 'timeframe': timeframe,
        'trade_pnl_usd': round(r.total_pnl, 4),
        'bh_pnl_usd': round(r.buy_hold_pnl_usd, 4),
        'accounting_diff_usd': round(accounting_diff, 4),
        'net_return_pct': round(r.strategy_return_pct, 2),
        'buy_hold_pct': round(r.buy_hold_return, 2),
        'ret_diff_pct': round(ret_diff_pct, 3),
        'allowed_diff_pct': round(allowed_diff, 3),
        'pass': bool(accounting_diff < 0.01 and ret_diff_pct < allowed_diff),
    }


def run_coinflip_fee_check(harness, ind, ticker: str, timeframe: str) -> dict:
    """A2 (fee application): identical seeded runs with and without fees.
    Entries/exits are identical (fees don't move fills), so PF with fees must
    be STRICTLY lower for every seed that trades. Zero exceptions."""
    seeds_checked = 0
    violations = []
    pf_fees_list = []
    for seed in range(COINFLIP_SEEDS):
        r_free = harness.run_strategy(CoinFlipControl(seed=seed), ind, ticker, timeframe,
                                      'fixed_2r', fee_override=0.0)
        r_fees = harness.run_strategy(CoinFlipControl(seed=seed), ind, ticker, timeframe,
                                      'fixed_2r')
        if r_free.trade_count == 0:
            continue
        seeds_checked += 1
        pf_free, pf_fees = r_free.profit_factor, r_fees.profit_factor
        pf_fees_list.append(pf_fees)
        if r_free.trade_count != r_fees.trade_count:
            violations.append(f'seed {seed}: trade counts differ ({r_free.trade_count} vs {r_fees.trade_count})')
        elif pf_free == float('inf'):
            if pf_fees == float('inf') and r_fees.total_pnl >= r_free.total_pnl:
                violations.append(f'seed {seed}: fees did not reduce PnL')
        elif pf_fees >= pf_free:
            violations.append(f'seed {seed}: pf with fees {_pf_str(pf_fees)} >= without {_pf_str(pf_free)}')
        if r_fees.trade_count > 0 and r_fees.profit_factor > r_fees.gross_pf:
            violations.append(f'seed {seed}: net PF exceeds gross PF')

    finite = [p for p in pf_fees_list if p != float('inf')]
    avg_pf = sum(finite) / len(finite) if finite else None
    return {
        'ticker': ticker, 'timeframe': timeframe,
        'seeds_checked': seeds_checked,
        'avg_pf_with_fees': None if avg_pf is None else round(avg_pf, 3),
        'violations': violations,
        'pass': bool(seeds_checked >= COINFLIP_SEEDS // 2 and not violations),
    }


def check_survivorship() -> dict:
    """A4: flag if the universe contains no delisted names."""
    tickers = set()
    for f in os.listdir(DATA_DIR):
        if f.endswith('.csv') and '_' in f:
            tickers.add(f.rsplit('_', 1)[0])
    known_delisted = {'MULN', 'SNDL', 'BBBYQ'}
    found = known_delisted & tickers
    return {
        'total_tickers': len(tickers),
        'known_delisted_present': sorted(found),
        'pass': bool(found),
        'note': ('Universe should include delisted names for unbiased backtesting. '
                 'Currently only quarantine tickers are present. Full survivorship-complete '
                 'data requires a paid source (CRSP/Norgate/Sharadar).'),
    }


def _sanitize(obj):
    """Native JSON only: bool stays bool, inf/nan become None. The old
    default=str serialization produced the string 'False', which is truthy."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float) and (math.isinf(obj) or math.isnan(obj)):
        return None
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, float, str)):
        return obj
    return str(obj)


def main() -> bool:
    harness = _control_harness()

    logger.info('=' * 70)
    logger.info('HARNESS VALIDATION SUITE (controls run THROUGH the harness)')
    logger.info('=' * 70)

    all_pass = True
    results = {'oracle': [], 'buyhold': [], 'coinflip': [], 'assertions': []}
    indicators_cache = {}

    for filename, ticker, tf in TEST_FILES:
        filepath = os.path.join(DATA_DIR, filename)
        if not os.path.exists(filepath):
            logger.warning(f'  missing data file, skipping: {filename}')
            continue
        candles = load_csv(filepath)
        if len(candles) < 300:
            logger.warning(f'  insufficient candles ({len(candles)}), skipping: {filename}')
            continue
        indicators_cache[(ticker, tf)] = precompute_indicators(candles)

    if not indicators_cache:
        logger.error('No usable data files - cannot validate. FAIL.')
        return False

    # --- A1 + A3 + A2(net<=gross): oracle through the harness ---
    logger.info('')
    logger.info('--- A1/A3: ORACLE CONTROL (through run_strategy) ---')
    for (ticker, tf), ind in indicators_cache.items():
        r = run_oracle_checks(harness, ind, ticker, tf)
        results['oracle'].append(r)
        if not r['pass']:
            all_pass = False
        logger.info(
            f"  {ticker:8s} {tf:4s}: trades={r['oracle_trades']:5d} "
            f"pf={r['oracle_pf_zero_cost'] if r['oracle_pf_zero_cost'] is not None else 'inf':>8} "
            f"wr={r['oracle_win_rate']:5.1f}% delayed_pf={r['delayed_pf'] if r['delayed_pf'] is not None else 'inf':>7} "
            f"{'PASS' if r['pass'] else 'FAIL'}"
        )

    # --- A2: buy-hold accounting through the harness ---
    logger.info('')
    logger.info('--- A2: BUY-HOLD CONTROL (P&L accounting) ---')
    for (ticker, tf), ind in indicators_cache.items():
        r = run_buyhold_check(harness, ind, ticker, tf)
        results['buyhold'].append(r)
        if not r['pass']:
            all_pass = False
        logger.info(
            f"  {ticker:8s} {tf:4s}: trade=${r.get('trade_pnl_usd', float('nan')):8.2f} "
            f"bh=${r.get('bh_pnl_usd', float('nan')):8.2f} "
            f"diff=${r.get('accounting_diff_usd', float('nan')):.4f} "
            f"{'PASS' if r['pass'] else 'FAIL'}"
        )

    # --- A2: fee application via seeded coin flips ---
    logger.info('')
    logger.info('--- A2: COIN FLIP CONTROL (fee application, %d seeds) ---' % COINFLIP_SEEDS)
    for (ticker, tf), ind in list(indicators_cache.items())[:5]:
        r = run_coinflip_fee_check(harness, ind, ticker, tf)
        results['coinflip'].append(r)
        if not r['pass']:
            all_pass = False
        logger.info(
            f"  {ticker:8s} {tf:4s}: seeds={r['seeds_checked']:2d} "
            f"avg_pf_fees={r['avg_pf_with_fees']} "
            f"violations={len(r['violations'])} {'PASS' if r['pass'] else 'FAIL'}"
        )
        for v in r['violations']:
            logger.error(f'    VIOLATION: {v}')

    # --- A5: cross-harness referee (three engines must agree) ---
    logger.info('')
    logger.info('--- A5: CROSS-HARNESS REFEREE (event vs vectorized vs backtesting.py) ---')
    try:
        from backtest.cross_harness_check import main as cross_harness_main
        cross_ok = cross_harness_main()
    except Exception as e:
        logger.error(f'  cross-harness check errored: {e}')
        cross_ok = False
    results['assertions'].append({'check': 'cross_harness', 'pass': bool(cross_ok)})
    if not cross_ok:
        all_pass = False
        logger.error('  BLOCKING: harnesses disagree - investigate before trusting results.')

    # --- A4: survivorship ---
    logger.info('')
    logger.info('--- A4: SURVIVORSHIP CHECK ---')
    surv = check_survivorship()
    results['assertions'].append(surv)
    logger.info(f"  tickers={surv['total_tickers']} delisted_present={surv['known_delisted_present']} "
                f"{'PASS' if surv['pass'] else 'FLAG'}")
    if not surv['pass']:
        # Honest flag, not a wiring failure - but it must be visible.
        logger.warning('  WARNING: no delisted names in universe; long-biased results inflated.')

    # --- summary ---
    logger.info('')
    logger.info('=' * 70)
    logger.info('VALIDATION SUMMARY')
    logger.info('=' * 70)
    n_ok = sum(1 for group in ('oracle', 'buyhold', 'coinflip')
               for r in results[group] if r['pass'])
    n_all = sum(len(results[g]) for g in ('oracle', 'buyhold', 'coinflip'))
    logger.info(f'Harness-validity checks: {n_ok}/{n_all} passed')
    logger.info('Overall: ' + ('ALL PASS - harness wiring, accounting, and fee application verified'
                               if all_pass else
                               'FAILURES DETECTED - graveyard results stay PROVISIONAL'))
    if not all_pass:
        logger.error('BLOCKING: do not trust or regenerate graveyard results until this suite passes.')

    output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               'research', 'graveyard', 'harness_validation.json')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(_sanitize({
            'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
            'all_pass': all_pass,
            'results': results,
        }), f, indent=2, allow_nan=False)
    logger.info(f'Saved to: {output_path}')

    return all_pass


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
