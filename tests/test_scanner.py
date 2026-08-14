"""Tests for the signal scanner (T4).

Tests:
1. Scanner loads all builtin strategies
2. Regime filter: uptrend passes, downtrend fails, and (regression) a FALLING
   series with the last price popped above the EMA must NOT read as uptrend
   (the audited B4 float-truthiness bug)
3. Confirmation stack: RSI and volume filters block with real skip reasons
4. _drop_forming trims the in-progress candle, keeps closed ones (audited B1)
5. Signal logging: acted and skipped signals both written to DB
6. Scanner ALWAYS logs acted=0; the executor flips it later (audited M6)
7. Doji block_entries actually blocks entry scanning (audited: emitted but
   never enforced)
8. Exit signals bypass the confirmation stack but don't block entries
9. Dedup: scanner skips already-scanned candles
10. Queue items are 4-tuples (kind, pair, signal, signal_id)
"""
import os
import sys
import json
import time
import sqlite3
import pytest
from queue import Queue
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scanner import Scanner, ConfirmationResult
from strategies.base import Signal
from engine.db import init_schema, get_db_path


@pytest.fixture
def config():
    return {
        'exchange': {
            'name': 'binanceus',
            'pairs': ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'],
        },
        'timeframes': {
            'signal': '15m',
            'regime': '1h',
        },
        'strategy': {
            'confirmation': {
                'regime_ema_period': 50,
                'regime_lookback': 10,
                'rsi_period': 14,
                'rsi_max_entry': 60,
                'rsi_reversal_boost': 45,
                'volume_sma_period': 20,
                'volume_min_ratio': 1.5,
                'support_lookback': 100,
                'support_min_touches': 2,
                'support_cluster_atr_mult': 0.5,
                'location_atr_mult': 1.5,
                'spread_max': 0.001,
            }
        }
    }


@pytest.fixture
def mock_collector():
    """Mock collector that returns synthetic candle data (all candles fully
    closed: timestamps are far in the past, so _drop_forming keeps them)."""
    collector = MagicMock()
    collector.get_top_of_book_spread.return_value = 0.0005  # 0.05%, under 0.10% max

    def make_uptrend_candles():
        import random
        random.seed(42)
        n = 200
        base = 50000  # BTC price
        closes, opens, highs, lows, volumes = [], [], [], [], []

        for i in range(n):
            noise = random.uniform(-100, 100)
            close = base + i * 50 + noise
            open_ = close - random.uniform(-50, 50)
            high = max(close, open_) + random.uniform(10, 50)
            low = min(close, open_) - random.uniform(10, 50)
            vol = random.uniform(100, 200) * (1.8 if i >= n - 5 else 1.0)

            closes.append(round(close, 2))
            opens.append(round(open_, 2))
            highs.append(round(high, 2))
            lows.append(round(low, 2))
            volumes.append(round(vol, 2))

        return {
            'opens': opens, 'highs': highs, 'lows': lows,
            'closes': closes, 'volumes': volumes,
            'timestamps': [1700000000000 + i * 900000 for i in range(n)],
        }

    def make_regime_uptrend():
        import random
        random.seed(42)
        n = 100
        base = 50000
        closes = [base + i * 100 + random.uniform(-50, 50) for i in range(n)]
        highs = [c + random.uniform(10, 50) for c in closes]
        lows = [c - random.uniform(10, 50) for c in closes]
        return {'closes': closes, 'highs': highs, 'lows': lows}

    def get_candles_fn(pair, tf, limit=200):
        if tf == '1h':
            r = make_regime_uptrend()
            return {
                'opens': r['closes'], 'highs': r['highs'], 'lows': r['lows'],
                'closes': r['closes'], 'volumes': [100.0] * len(r['closes']),
                'timestamps': [1700000000000 + i * 3600000 for i in range(len(r['closes']))],
            }
        return make_uptrend_candles()

    collector.get_latest_candles.side_effect = get_candles_fn
    return collector


@pytest.fixture
def scanner(config, mock_collector, tmp_path, monkeypatch):
    """Scanner with temporary DB."""
    db_path = str(tmp_path / "test_trading.db")
    monkeypatch.setenv("TRADING_DB_PATH", db_path)
    init_schema()
    q = Queue()
    s = Scanner(config, mock_collector, q)
    return s


# ---------------------------------------------------------------------------
# Test stubs
# ---------------------------------------------------------------------------

class StubEntryStrategy:
    """Always fires a bullish entry signal; records whether it was consulted."""
    name = 'stub_entry'
    is_entry = True

    def __init__(self):
        self.scan_calls = 0

    def scan(self, candles):
        self.scan_calls += 1
        c = candles['closes'][-1]
        return Signal(
            pair='', pattern=self.name, direction='bullish', confidence=0.6,
            features={}, entry=c, stop=c * 0.97, target=c * 1.02,
        )


class StubExitStrategy:
    """Always fires an exit signal with a configurable action."""
    is_entry = False

    def __init__(self, action, name='stub_exit'):
        self.action = action
        self.name = name

    def scan(self, candles):
        return Signal(
            pair='', pattern=self.name, direction='bearish', confidence=0.7,
            features={'atr': 1.0}, action=self.action,
        )


def passing_confirmation(signal, candles, pair):
    return ConfirmationResult(True, None, 'uptrend', {'rsi': 50.0})


class TestScannerInit:
    def test_loads_entry_strategies(self, scanner):
        assert len(scanner.entry_strategies) > 0
        names = [s.name for s in scanner.entry_strategies]
        assert 'bullish_engulfing' in names
        assert 'hammer' in names
        assert 'morning_star' in names
        assert 'piercing_line' in names

    def test_loads_exit_strategies(self, scanner):
        assert len(scanner.exit_strategies) > 0
        names = [s.name for s in scanner.exit_strategies]
        assert 'bearish_exit' in names
        assert 'three_white_soldiers' in names
        assert 'doji_filter' in names


class TestRegimeCheck:
    def test_regime_check_uptrend(self, scanner):
        # Strong uptrend: prices rising, EMA slope positive, price above EMA
        closes = [50000 + i * 200 for i in range(100)]
        highs = [c + 50 for c in closes]
        lows = [c - 50 for c in closes]
        candles = {
            'closes': closes, 'highs': highs, 'lows': lows,
            'regime_closes': closes, 'regime_highs': highs, 'regime_lows': lows,
        }
        passed, regime = scanner._check_regime(candles)
        assert passed is True
        assert regime == 'uptrend'

    def test_regime_check_downtrend(self, scanner):
        # Strong downtrend: slope negative AND price below EMA
        closes = [50000 - i * 200 for i in range(100)]
        highs = [c + 50 for c in closes]
        lows = [c - 50 for c in closes]
        candles = {
            'closes': closes, 'highs': highs, 'lows': lows,
            'regime_closes': closes, 'regime_highs': highs, 'regime_lows': lows,
        }
        passed, regime = scanner._check_regime(candles)
        assert passed is False
        assert regime == 'downtrend'

    def test_falling_series_with_price_pop_is_not_uptrend(self, scanner):
        """Regression for the audited B4 float-truthiness bug: a steadily
        FALLING regime series whose last price pops above the (lagging) EMA
        has a NEGATIVE slope. Old code did `if slope:` on the raw float, so
        the negative slope passed and this read as (True, 'uptrend')."""
        closes = [50000 - i * 100 for i in range(100)]
        closes[-1] += 5000  # pop the last price above the lagging EMA
        highs = [c + 50 for c in closes]
        lows = [c - 50 for c in closes]
        candles = {
            'closes': closes, 'highs': highs, 'lows': lows,
            'regime_closes': closes, 'regime_highs': highs, 'regime_lows': lows,
        }
        passed, regime = scanner._check_regime(candles)
        assert passed is False
        assert regime != 'uptrend'


class TestDropForming:
    def test_forming_candle_trimmed(self):
        """A candle whose open ts + interval is in the future is still forming
        and must be dropped (audited B1: scanning the forming candle)."""
        now_ms = int(time.time() * 1000)
        interval = 900000
        cols = {
            'closes': [100.0, 101.0, 102.0],
            'volumes': [10.0, 11.0, 12.0],
            'timestamps': [now_ms - 2 * interval, now_ms - interval, now_ms - 1000],
        }
        out = Scanner._drop_forming(cols, interval)
        assert len(out['closes']) == 2
        assert out['closes'] == [100.0, 101.0]
        assert out['timestamps'][-1] == now_ms - interval

    def test_closed_candles_kept(self):
        """If the last candle is fully closed, nothing is trimmed."""
        now_ms = int(time.time() * 1000)
        interval = 900000
        cols = {
            'closes': [100.0, 101.0, 102.0],
            'volumes': [10.0, 11.0, 12.0],
            'timestamps': [now_ms - 4 * interval, now_ms - 3 * interval,
                           now_ms - 2 * interval],
        }
        out = Scanner._drop_forming(cols, interval)
        assert len(out['closes']) == 3
        assert out['closes'] == [100.0, 101.0, 102.0]


class TestConfirmationStack:
    def _make_candles(self, closes, volumes=None):
        """Signal candles + an uptrend regime so the regime check passes."""
        n = len(closes)
        regime = [40000 + i * 100 for i in range(100)]
        return {
            'closes': closes,
            'highs': [c + 50 for c in closes],
            'lows': [c - 50 for c in closes],
            'opens': list(closes),
            'volumes': volumes if volumes is not None else [100.0] * n,
            'timestamps': [1700000000000 + i * 900000 for i in range(n)],
            'regime_closes': regime,
            'regime_highs': [c + 50 for c in regime],
            'regime_lows': [c - 50 for c in regime],
        }

    def test_rsi_blocks_overbought(self, scanner):
        """A monotonically rising close series has RSI ~100 > 60: the stack
        must skip with an rsi_high reason (regime passes first)."""
        closes = [50000.0 + i * 100 for i in range(120)]
        candles = self._make_candles(closes)
        signal = Signal(
            pair="BTC/USDT", pattern="bullish_engulfing", direction="bullish",
            confidence=0.6, features={}, entry=closes[-1], stop=closes[-1] * 0.97,
            target=closes[-1] * 1.02,
        )
        result = scanner._confirmation_stack(signal, candles, "BTC/USDT")
        assert result.passed is False
        assert result.skip_reason.startswith('rsi_high')
        assert result.features['rsi'] > 60

    def test_volume_blocks_low_volume(self, scanner):
        """Flat volume (ratio ~1.0 < 1.5) must skip with volume_low, given
        regime and RSI pass (alternating closes keep RSI ~50)."""
        closes = [50000.0 + (100 if i % 2 == 0 else -100) for i in range(120)]
        candles = self._make_candles(closes, volumes=[100.0] * 120)
        signal = Signal(
            pair="BTC/USDT", pattern="bullish_engulfing", direction="bullish",
            confidence=0.6, features={}, entry=closes[-1], stop=closes[-1] * 0.97,
            target=closes[-1] * 1.02,
        )
        result = scanner._confirmation_stack(signal, candles, "BTC/USDT")
        assert result.passed is False
        assert result.skip_reason.startswith('volume_low')

    def test_spread_threshold_config(self, scanner):
        assert scanner.spread_max == 0.001


class TestSignalLogging:
    def test_acted_signal_logged(self, scanner):
        signal = Signal(
            pair="BTC/USDT", pattern="bullish_engulfing", direction="bullish",
            confidence=0.6, features={'rsi': 40}, entry=50000, stop=49500, target=51000,
        )
        result = ConfirmationResult(True, None, 'uptrend', {'rsi': 40})
        signal_id = scanner._log_signal("BTC/USDT", "15m", signal, result, acted=True)

        assert signal_id is not None

        # Verify it's in the DB
        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        conn.close()

        assert row is not None
        assert row['pair'] == 'BTC/USDT'
        assert row['pattern'] == 'bullish_engulfing'
        assert row['acted'] == 1
        assert row['skip_reason'] is None
        assert row['mode'] == 'paper'

    def test_skipped_signal_logged(self, scanner):
        signal = Signal(
            pair="ETH/USDT", pattern="hammer", direction="bullish",
            confidence=0.5, features={}, entry=3000, stop=2950, target=3100,
        )
        result = ConfirmationResult(False, "regime_fail: downtrend", 'downtrend', {'rsi': 35})
        signal_id = scanner._log_signal("ETH/USDT", "15m", signal, result, acted=False)

        assert signal_id is not None

        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        conn.close()

        assert row is not None
        assert row['acted'] == 0
        assert 'regime_fail' in row['skip_reason']

    def test_features_json_logged(self, scanner):
        signal = Signal(
            pair="SOL/USDT", pattern="morning_star", direction="bullish",
            confidence=0.55, features={}, entry=100, stop=98, target=104,
        )
        features = {'rsi': 42.5, 'volume_ratio': 1.8, 'atr': 0.5, 'regime': 'uptrend'}
        result = ConfirmationResult(True, None, 'uptrend', features)
        signal_id = scanner._log_signal("SOL/USDT", "15m", signal, result, acted=True)

        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        conn.close()

        logged_features = json.loads(row['features_json'])
        assert logged_features['rsi'] == 42.5
        assert logged_features['volume_ratio'] == 1.8
        assert logged_features['regime'] == 'uptrend'


class TestScanPair:
    def test_passing_signal_queued_and_logged_acted_zero(self, scanner):
        """Regression for the audited acted-flag bug: the scanner must log
        acted=0 even for signals that PASS confirmation (the executor flips it
        after an order is actually placed). Queue items are 4-tuples."""
        entry = StubEntryStrategy()
        scanner.entry_strategies = [entry]
        scanner.exit_strategies = []
        scanner._confirmation_stack = passing_confirmation

        scanner._scan_pair('BTC/USDT')

        assert entry.scan_calls == 1
        assert scanner.signal_queue.qsize() == 1
        item = scanner.signal_queue.get_nowait()
        assert len(item) == 4
        kind, pair, signal, signal_id = item
        assert kind == 'entry'
        assert pair == 'BTC/USDT'
        assert signal.pattern == 'stub_entry'
        assert signal_id is not None

        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        conn.close()
        assert row is not None
        assert row['acted'] == 0  # NEVER 1 from the scanner

    def test_scan_once_queues_entry_per_pair(self, scanner):
        """scan_once with an always-firing entry strategy queues one entry
        item per configured pair (replaces the old qsize() >= 0 tautology)."""
        entry = StubEntryStrategy()
        scanner.entry_strategies = [entry]
        scanner.exit_strategies = []
        scanner._confirmation_stack = passing_confirmation

        scanner.scan_once()

        assert scanner.signal_queue.qsize() == 3  # BTC, ETH, SOL
        pairs = set()
        while not scanner.signal_queue.empty():
            kind, pair, signal, signal_id = scanner.signal_queue.get_nowait()
            assert kind == 'entry'
            pairs.add(pair)
        assert pairs == {'BTC/USDT', 'ETH/USDT', 'SOL/USDT'}

    def test_dedup_skips_same_candle(self, scanner):
        """Second scan of the same (unchanged) candle must not queue or log
        anything new (replaces the old `assert True` version)."""
        entry = StubEntryStrategy()
        scanner.entry_strategies = [entry]
        scanner.exit_strategies = []
        scanner._confirmation_stack = passing_confirmation

        scanner._scan_pair('BTC/USDT')
        assert scanner.signal_queue.qsize() == 1
        assert entry.scan_calls == 1

        conn = sqlite3.connect(get_db_path())
        count_after_first = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        conn.close()

        # Same candle data again: dedup must skip the whole scan
        scanner._scan_pair('BTC/USDT')
        assert scanner.signal_queue.qsize() == 1  # unchanged
        assert entry.scan_calls == 1              # strategy never re-consulted

        conn = sqlite3.connect(get_db_path())
        count_after_second = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        conn.close()
        assert count_after_second == count_after_first


class TestExitSignals:
    def test_block_entries_blocks_entry_scanning(self, scanner):
        """Regression: a block_entries exit signal (doji) must stop entry
        scanning for that candle. The audit found it was emitted but never
        enforced anywhere."""
        entry = StubEntryStrategy()
        blocker = StubExitStrategy(action='block_entries', name='doji_filter')
        scanner.entry_strategies = [entry]
        scanner.exit_strategies = [blocker]
        scanner._confirmation_stack = passing_confirmation

        scanner._scan_pair('BTC/USDT')

        # Entry strategies were never consulted
        assert entry.scan_calls == 0

        # block_entries is enforced IN the scanner and therefore NOT queued
        # (queueing it gave the executor an item it could only mislabel -
        # re-audit N4). The queue must be completely empty.
        items = []
        while not scanner.signal_queue.empty():
            items.append(scanner.signal_queue.get_nowait())
        assert items == []

        # And the signal row records that the block was enforced here.
        from engine.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT acted, skip_reason FROM signals WHERE pattern = 'doji_filter' "
            "ORDER BY ts DESC LIMIT 1").fetchone()
        conn.close()
        assert row is not None
        assert row['acted'] == 0
        assert row['skip_reason'] == 'block_enforced_in_scanner'

    def test_exit_signal_not_gated_and_does_not_block_entries(self, scanner):
        """A close_long exit signal bypasses the confirmation stack (queued
        and logged acted=0 even though no stack ran) and does NOT block entry
        scanning on the same candle."""
        entry = StubEntryStrategy()
        closer = StubExitStrategy(action='close_long', name='bearish_exit')
        scanner.entry_strategies = [entry]
        scanner.exit_strategies = [closer]
        scanner._confirmation_stack = passing_confirmation

        scanner._scan_pair('BTC/USDT')

        # Both the exit and the entry were queued
        items = []
        while not scanner.signal_queue.empty():
            items.append(scanner.signal_queue.get_nowait())
        kinds = [i[0] for i in items]
        assert kinds == ['exit', 'entry']
        assert entry.scan_calls == 1

        # Exit signal was logged acted=0 with no skip reason
        exit_signal_id = items[0][3]
        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals WHERE id = ?",
                           (exit_signal_id,)).fetchone()
        conn.close()
        assert row is not None
        assert row['pattern'] == 'bearish_exit'
        assert row['acted'] == 0
