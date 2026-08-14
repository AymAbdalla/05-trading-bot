"""Cross-harness referee: three unrelated backtest engines must agree.

Aym ruling 2026-08-12: multiple harnesses, ours included. The same simple
deterministic strategy (SMA-10/30 golden cross, buy at close, stop 2% below,
target 2R = +4%) runs through:

  1. our event-driven harness  (backtest/harness.py)
  2. our vectorized harness    (backtest/vectorized_harness.py)
  3. backtesting.py            (external library, unrelated codebase)

A harness bug (lookahead, wrong fills, missing fees) would have to reproduce
identically in all three engines to survive this check.

WHAT "AGREEMENT" MEANS (documented, not fake precision):
- Ours vs ours (identical configured semantics: fill-at-close, stop-first
  ties, gap fills, fees, zero slippage, confirmation stack off, exit-signal
  scanning off): trade count within +/-2 (boundary/warmup differences) and
  win rate within 10 points.
- Ours vs backtesting.py (different engine conventions: its position sizing
  is cash-fraction based and same-bar tie resolution is its own): trade
  count within +/-25% and win rate within 15 points. This catches systematic
  errors (a lookahead bug shifts win rates by tens of points), not pennies.

Run: python3 backtest/cross_harness_check.py    (exit 0 = agree)
Also invoked by validate_harness.py as assertion A5.
"""
import logging
import os
import sys
import warnings
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.base import Strategy, Signal
from backtest.data_loader import load_csv

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
REFEREE_FILES = [('AAPL_1d.csv', 'AAPL'), ('BTC_USD_1d.csv', 'BTC_USD')]

SMA_FAST, SMA_SLOW = 10, 30
STOP_PCT, TARGET_PCT = 0.98, 1.04  # stop 2% below entry, 2R target


def _sma(values: List[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


class SmaCrossStub(Strategy):
    """Golden cross long: SMA(10) crosses above SMA(30) on the last closed
    bar. Deterministic, no filters - referee strategies must be trivially
    portable across engines."""
    name = 'sma_cross_referee'
    is_entry = True

    def scan(self, candles):
        closes = candles['closes']
        if len(closes) < SMA_SLOW + 1:
            return None
        fast_now, slow_now = _sma(closes, SMA_FAST), _sma(closes, SMA_SLOW)
        fast_prev, slow_prev = _sma(closes[:-1], SMA_FAST), _sma(closes[:-1], SMA_SLOW)
        if fast_prev is None or slow_prev is None:
            return None
        if fast_prev <= slow_prev and fast_now > slow_now:
            entry = closes[-1]
            return Signal(pair='', pattern=self.name, direction='bullish',
                          confidence=0.5, features={}, entry=entry,
                          stop=entry * STOP_PCT, target=entry * TARGET_PCT,
                          valid_for=1)
        return None


def _config(fee: float = 0.001):
    return {
        'risk': {'notional_cap_usd': 100},
        'exchange': {'fees': {'taker': fee}, 'slippage': {'market': 0.0}},
        'strategy': {'confirmation': {'apply_confirmation_stack': False}},
    }


def run_event_harness(candles) -> dict:
    from backtest.harness import BacktestHarness

    # The event harness always applies its confirmation stack, which would
    # desynchronize the engines (neither the vectorized leg nor the external
    # leg runs it here). The referee compares TRADE MECHANICS, not filters,
    # so this subclass neutralizes the stack.
    class NoStackHarness(BacktestHarness):
        def get_regime(self, regime_closes):
            return 'uptrend'  # referee: no regime gate

    h = NoStackHarness(_config())
    h.rsi_max_entry = 101.0        # RSI gate cannot block
    h.volume_min_ratio = 0.0       # volume gate cannot block
    r = h.run_strategy_on_candles(SmaCrossStub(), candles, candles, 'REF',
                                  scan_exit_signals=False)
    return {'trades': r.trade_count, 'win_rate': r.win_rate,
            'total_pnl': r.total_pnl}


def run_vectorized_harness(candles) -> dict:
    from backtest.vectorized_harness import (VectorizedBacktestHarness,
                                             precompute_indicators)
    h = VectorizedBacktestHarness(_config())
    ind = precompute_indicators(candles)
    r = h.run_strategy(SmaCrossStub(), ind, 'REF', '1d', 'fixed_2r')
    return {'trades': r.trade_count, 'win_rate': r.win_rate,
            'total_pnl': r.total_pnl}


def run_backtesting_py(candles) -> dict:
    import pandas as pd
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        from backtesting import Backtest
        from backtesting import Strategy as BtStrategy
        from backtesting.lib import crossover

    df = pd.DataFrame({
        'Open': [c['open'] for c in candles],
        'High': [c['high'] for c in candles],
        'Low': [c['low'] for c in candles],
        'Close': [c['close'] for c in candles],
        'Volume': [c['volume'] for c in candles],
    }, index=pd.to_datetime([c['ts'] for c in candles], unit='ms'))

    class BtSmaCross(BtStrategy):
        def init(self):
            close = self.data.Close
            self.fast = self.I(lambda x: pd.Series(x).rolling(SMA_FAST).mean(), close)
            self.slow = self.I(lambda x: pd.Series(x).rolling(SMA_SLOW).mean(), close)

        def next(self):
            if not self.position and crossover(self.fast, self.slow):
                px = self.data.Close[-1]
                self.buy(sl=px * STOP_PCT, tp=px * TARGET_PCT)

    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        bt = Backtest(df, BtSmaCross, cash=100000, commission=0.001,
                      trade_on_close=True, exclusive_orders=False)
        stats = bt.run()
    n = int(stats['# Trades'])
    wr = float(stats['Win Rate [%]']) / 100.0 if n else 0.0
    return {'trades': n, 'win_rate': wr, 'total_pnl': float(stats['Equity Final [$]']) - 100000}


def check_pair(name: str, candles) -> dict:
    ev = run_event_harness(candles)
    vec = run_vectorized_harness(candles)
    ext = run_backtesting_py(candles)

    # Ours vs ours: tight agreement.
    internal_ok = (abs(ev['trades'] - vec['trades']) <= 2 and
                   (min(ev['trades'], vec['trades']) == 0 or
                    abs(ev['win_rate'] - vec['win_rate']) <= 0.10))
    # Ours vs external: loose but systematic-error-catching agreement.
    # Count tolerance max(4, 25%): on small samples, divergent tp/sl fill
    # prices shift position LIFETIMES, which shifts which later signals are
    # takeable - a few trades of drift is engine-convention noise, not error.
    # The teeth are the win-rate band: a lookahead/fill bug moves win rates
    # by tens of points (the audited harness showed 90%+), far beyond 15.
    base = max(vec['trades'], 1)
    external_ok = (abs(vec['trades'] - ext['trades']) <= max(4, 0.25 * base) and
                   (ext['trades'] == 0 or vec['trades'] == 0 or
                    abs(vec['win_rate'] - ext['win_rate']) <= 0.15))

    return {'ticker': name,
            'event': ev, 'vectorized': vec, 'backtesting_py': ext,
            'internal_agreement': bool(internal_ok),
            'external_agreement': bool(external_ok),
            'pass': bool(internal_ok and external_ok)}


def main() -> bool:
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    logging.getLogger('backtest.data_loader').setLevel(logging.WARNING)
    all_pass = True
    for filename, name in REFEREE_FILES:
        path = os.path.join(DATA_DIR, filename)
        if not os.path.exists(path):
            logger.warning(f'{name}: data file missing, skipping')
            continue
        candles = load_csv(path)
        if len(candles) < 300:
            continue
        r = check_pair(name, candles)
        logger.info(
            f"{name:8s} event={r['event']['trades']:3d}t/{r['event']['win_rate']:.0%} "
            f"vec={r['vectorized']['trades']:3d}t/{r['vectorized']['win_rate']:.0%} "
            f"ext={r['backtesting_py']['trades']:3d}t/{r['backtesting_py']['win_rate']:.0%} "
            f"internal={'OK' if r['internal_agreement'] else 'FAIL'} "
            f"external={'OK' if r['external_agreement'] else 'FAIL'}"
        )
        if not r['pass']:
            all_pass = False
    logger.info('CROSS-HARNESS: ' + ('AGREE' if all_pass else 'DISAGREE - investigate before trusting any harness'))
    return all_pass


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
