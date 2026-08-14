"""Tests for backtest/pooled_analysis.py's asset-class exclusion.

ROADMAP P0.4: futures/options total_pnl_usd is sized against margin/premium,
not notional, so pooling it with spot dollar figures silently lets one
contract's swing outvote a hundred spot clips. pool()'s default now excludes
FUTURES/OPTIONS rows from dollar-pooled cells (matching the call already made
in cross_sectional.py), while asset_class_analysis.py remains where those
rows ARE analyzed, keyed by class.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.pooled_analysis import pool


def _row(strategy, ticker, asset_class, trades=200, pnl=10.0, win_rate=0.5,
        exit_config='fixed_2r', verdict='PASS'):
    return {
        'strategy': strategy, 'ticker': ticker, 'exit_config': exit_config,
        'verdict': verdict, 'trades': trades, 'total_pnl_usd': pnl,
        'win_rate': win_rate, 'asset_class': asset_class,
    }


def test_futures_excluded_from_default_pool():
    entries = [
        _row('S1', 'AAPL', 'EQUITY', trades=200, pnl=10.0),
        _row('S1', 'ES_F', 'FUTURES', trades=200, pnl=5_000.0),
    ]
    rows = pool(entries, by=('strategy',))
    assert len(rows) == 1
    assert rows[0]['pooled_trades'] == 200          # only the equity row
    assert rows[0]['pooled_pnl_usd'] == 10.0         # futures $5,000 never entered


def test_options_also_excluded_from_default_pool():
    entries = [_row('S1', 'SPY', 'OPTIONS', trades=200, pnl=900.0)]
    assert pool(entries, by=('strategy',)) == []


def test_spot_classes_pool_normally():
    entries = [
        _row('S1', 'AAPL', 'EQUITY', trades=100, pnl=5.0),
        _row('S1', 'MSFT', 'EQUITY', trades=100, pnl=5.0),
        _row('S1', 'BTC/USDT', 'CRYPTO', trades=100, pnl=5.0),
    ]
    rows = pool(entries, by=('strategy',))
    assert len(rows) == 1
    assert rows[0]['pooled_trades'] == 300
    assert rows[0]['pooled_pnl_usd'] == 15.0


def test_exclude_asset_classes_can_be_overridden():
    entries = [_row('S1', 'ES_F', 'FUTURES', trades=200, pnl=5_000.0)]
    rows = pool(entries, by=('strategy',), exclude_asset_classes=())
    assert len(rows) == 1
    assert rows[0]['pooled_trades'] == 200
