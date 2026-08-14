"""Tests for the execution layer (T9): signal consumption, risk gating,
acted-flag protocol, halt handling, and stop-tightening."""
import json
import os
import sys
import time
import uuid
from pathlib import Path
from queue import Queue

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.db import init_schema, get_connection, insert_signal
from engine.adapters.paper import PaperAdapter
from engine.executor import Executor, HALT_FILE
from strategies.base import Signal


@pytest.fixture
def config():
    return {
        'risk': {'notional_cap_usd': 100, 'fee_to_edge_max': 0.15,
                 'max_trades_per_day': 5, 'consecutive_loss_pause': 4,
                 'max_concurrent_positions': 2, 'max_positions_per_pair': 1},
        'exchange': {'fees': {'taker': 0.001, 'maker': 0.001},
                     'slippage': {'market': 0.0005}},
        'paper': {'starting_equity': 2000.0},
        'timeframes': {'signal': '15m', 'regime': '1h'},
        'strategy': {'confirmation': {}},
    }


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'test.db')
    monkeypatch.setenv('TRADING_DB_PATH', db_path)
    init_schema()
    return db_path


@pytest.fixture(autouse=True)
def no_halt_file():
    """Never let a stray HALT file leak into or out of a test."""
    if os.path.exists(HALT_FILE):
        os.remove(HALT_FILE)
    yield
    if os.path.exists(HALT_FILE):
        os.remove(HALT_FILE)


class FakeCollector:
    def __init__(self, bid: float, ask: float):
        self._bid, self._ask = bid, ask
        self.exchange = self

    def fetch_ticker(self, pair):
        return {'bid': self._bid, 'ask': self._ask, 'last': (self._bid + self._ask) / 2}


def _fresh_candle(conn, pair='BTC/USDT'):
    """A recent candle so the stale-data check passes."""
    conn.execute(
        "INSERT OR REPLACE INTO candles (pair, tf, ts, open, high, low, close, volume) "
        "VALUES (?, '15m', ?, 100, 101, 99, 100, 1000)",
        (pair, int(time.time() * 1000)))
    conn.commit()


def _log_signal(conn, pair='BTC/USDT') -> str:
    return insert_signal(conn, {
        'ts': int(time.time() * 1000), 'pair': pair, 'tf': '15m',
        'strategy_id': 'stub', 'pattern': 'stub', 'direction': 'bullish',
        'confidence': 0.6, 'features_json': '{}', 'acted': 0,
        'skip_reason': None, 'mode': 'paper'})


def _executor(config, bid=100.0, ask=100.1):
    q = Queue()
    collector = FakeCollector(bid, ask)
    adapter = PaperAdapter(config)
    return Executor(config, collector, adapter, q), q, adapter, collector


def _entry_signal(entry=100.1, stop=97.0, target=None):
    return Signal(pair='BTC/USDT', pattern='stub', direction='bullish',
                  confidence=0.6, features={}, entry=entry, stop=stop,
                  target=target)


class TestEntryPath:
    def test_approved_entry_opens_position_and_marks_acted(self, config, tmpdb):
        ex, q, adapter, _ = _executor(config)
        conn = get_connection()
        _fresh_candle(conn)
        sid = _log_signal(conn)
        conn.commit(); conn.close()

        ex.handle_entry('BTC/USDT', _entry_signal(), sid)

        assert len(adapter.get_open_positions()) == 1
        conn = get_connection()
        row = conn.execute('SELECT acted FROM signals WHERE id = ?', (sid,)).fetchone()
        pos = conn.execute('SELECT signal_id, target_px FROM positions').fetchone()
        conn.close()
        assert row['acted'] == 1
        assert pos['signal_id'] == sid
        # No signal.target -> 2R default from stop
        assert pos['target_px'] == pytest.approx(100.1 + (100.1 - 97.0) * 2)

    def test_risk_blocked_entry_records_skip_reason(self, config, tmpdb):
        config['risk']['max_concurrent_positions'] = 0  # gate always blocks
        ex, q, adapter, _ = _executor(config)
        conn = get_connection()
        _fresh_candle(conn)
        sid = _log_signal(conn)
        conn.commit(); conn.close()

        ex.handle_entry('BTC/USDT', _entry_signal(), sid)

        assert adapter.get_open_positions() == []
        conn = get_connection()
        row = conn.execute('SELECT acted, skip_reason FROM signals WHERE id = ?', (sid,)).fetchone()
        conn.close()
        assert row['acted'] == 0
        assert 'risk_gate' in row['skip_reason']

    def test_stale_data_blocks_entry(self, config, tmpdb):
        ex, q, adapter, _ = _executor(config)
        conn = get_connection()
        # candle from 2 hours ago: > 2x the 15m interval
        conn.execute(
            "INSERT INTO candles (pair, tf, ts, open, high, low, close, volume) "
            "VALUES ('BTC/USDT', '15m', ?, 100, 101, 99, 100, 1000)",
            (int((time.time() - 7200) * 1000),))
        sid = _log_signal(conn)
        conn.commit(); conn.close()

        ex.handle_entry('BTC/USDT', _entry_signal(), sid)

        assert adapter.get_open_positions() == []
        conn = get_connection()
        row = conn.execute('SELECT skip_reason FROM signals WHERE id = ?', (sid,)).fetchone()
        conn.close()
        assert row['skip_reason'] == 'stale_data'


class TestExitPath:
    def _open_position(self, config, adapter, collector):
        return adapter.open_position(collector, 'BTC/USDT', 'stub',
                                     entry=100.1, stop=97.0, target=106.0,
                                     qty=1.0, signal_id=None)

    def test_close_long_signal_closes_position(self, config, tmpdb):
        ex, q, adapter, collector = _executor(config)
        pid = self._open_position(config, adapter, collector)
        assert pid is not None

        sig = Signal(pair='BTC/USDT', pattern='bearish_engulfing_exit',
                     direction='bearish', confidence=0.6, features={},
                     action='close_long')
        ex.handle_exit('BTC/USDT', sig, None)
        assert adapter.get_open_positions() == []

    def test_tighten_stop_only_raises(self, config, tmpdb):
        ex, q, adapter, collector = _executor(config)
        pid = self._open_position(config, adapter, collector)

        sig = Signal(pair='BTC/USDT', pattern='three_white_soldiers',
                     direction='trail', confidence=0.6, features={},
                     stop=99.0, action='tighten_stop')
        ex.handle_exit('BTC/USDT', sig, None)
        conn = get_connection()
        stop = conn.execute('SELECT stop_px FROM positions WHERE id = ?', (pid,)).fetchone()['stop_px']
        conn.close()
        assert stop == 99.0

        # A LOWER stop must be ignored: stops only ever move up.
        sig2 = Signal(pair='BTC/USDT', pattern='three_white_soldiers',
                      direction='trail', confidence=0.6, features={},
                      stop=95.0, action='tighten_stop')
        ex.handle_exit('BTC/USDT', sig2, None)
        conn = get_connection()
        stop = conn.execute('SELECT stop_px FROM positions WHERE id = ?', (pid,)).fetchone()['stop_px']
        conn.close()
        assert stop == 99.0


class TestHalt:
    def test_halt_closes_positions_and_blocks_entries(self, config, tmpdb):
        ex, q, adapter, collector = _executor(config)
        adapter.open_position(collector, 'BTC/USDT', 'stub',
                              entry=100.1, stop=97.0, target=106.0, qty=1.0)
        with open(HALT_FILE, 'w') as f:
            json.dump({'halt_id': 'test', 'reason': 'test'}, f)

        ex.step(queue_timeout=0.01)
        assert adapter.get_open_positions() == []

        conn = get_connection()
        _fresh_candle(conn)
        sid = _log_signal(conn)
        conn.commit(); conn.close()
        ex.handle_entry('BTC/USDT', _entry_signal(), sid)
        assert adapter.get_open_positions() == []
        conn = get_connection()
        row = conn.execute('SELECT skip_reason FROM signals WHERE id = ?', (sid,)).fetchone()
        conn.close()
        assert row['skip_reason'] == 'halted'


class TestStepMonitor:
    def test_step_triggers_stop_via_check_exits(self, config, tmpdb):
        # Position with stop above the current bid: one step must close it.
        ex, q, adapter, collector = _executor(config, bid=94.0, ask=94.1)
        conn = get_connection(); _fresh_candle(conn); conn.close()
        adapter.open_position(collector, 'BTC/USDT', 'stub',
                              entry=100.0, stop=95.0, target=110.0, qty=1.0)
        ex.step(queue_timeout=0.01)
        assert adapter.get_open_positions() == []
        conn = get_connection()
        row = conn.execute("SELECT exit_reason FROM positions").fetchone()
        conn.close()
        assert row['exit_reason'] == 'stop'

    def test_step_writes_equity_snapshot(self, config, tmpdb):
        ex, q, adapter, _ = _executor(config)
        ex.step(queue_timeout=0.01)  # first step: snapshot interval elapsed (ts 0)
        conn = get_connection()
        row = conn.execute('SELECT COUNT(*) AS n FROM equity_snapshots').fetchone()
        conn.close()
        assert row['n'] == 1


class TestReconcile:
    def test_reconcile_closes_unprotectable_and_marks_stale_signals(self, config, tmpdb):
        ex, q, adapter, collector = _executor(config)
        # Position with a MISSING stop (0.0 = unprotectable) and a healthy
        # one. NOTE: a stop ABOVE entry is legitimate (trailed winner) and
        # must survive reconcile - covered by the test below.
        conn = get_connection()
        for pid, stop in ((str(uuid.uuid4()), 0.0), (str(uuid.uuid4()), 95.0)):
            conn.execute(
                "INSERT INTO positions (id, pair, strategy_id, opened_ts, entry_px, "
                "qty, stop_px, target_px, mode) VALUES (?, 'BTC/USDT', 's', ?, 100.0, "
                "1.0, ?, 110.0, 'paper')", (pid, int(time.time()*1000), stop))
        sid = _log_signal(conn)  # queued signal from before the crash
        conn.commit(); conn.close()

        summary = ex.reconcile_on_boot()
        assert summary['positions_checked'] == 2
        assert summary['positions_closed'] == 1        # only the inverted-stop one
        assert summary['stale_signals_marked'] == 1
        assert len(adapter.get_open_positions()) == 1  # healthy one survives
        conn = get_connection()
        row = conn.execute('SELECT skip_reason FROM signals WHERE id = ?', (sid,)).fetchone()
        conn.close()
        assert row['skip_reason'] == 'engine_restart'

    def test_reconcile_keeps_trailed_stop_winners(self, config, tmpdb):
        """Re-audit N2: a stop trailed ABOVE entry locks in profit and is the
        OPPOSITE of unprotectable. Reconcile must never close it."""
        ex, q, adapter, collector = _executor(config)
        conn = get_connection()
        conn.execute(
            "INSERT INTO positions (id, pair, strategy_id, opened_ts, entry_px, "
            "qty, stop_px, target_px, mode) VALUES (?, 'BTC/USDT', 's', ?, 100.0, "
            "1.0, 104.0, 120.0, 'paper')", (str(uuid.uuid4()), int(time.time()*1000)))
        conn.commit(); conn.close()
        summary = ex.reconcile_on_boot()
        assert summary['positions_closed'] == 0
        assert len(adapter.get_open_positions()) == 1


class TestHaltQueueDrain:
    def test_halt_drains_queue_and_labels_signals(self, config, tmpdb):
        """Re-audit N1: signals queued during a halt must be drained and
        labeled, never executed on resume."""
        ex, q, adapter, collector = _executor(config)
        conn = get_connection(); _fresh_candle(conn)
        sid = _log_signal(conn); conn.commit(); conn.close()
        q.put(('entry', 'BTC/USDT', _entry_signal(), sid))
        with open(HALT_FILE, 'w') as f:
            json.dump({'halt_id': 'x', 'reason': 'test'}, f)
        ex.step(queue_timeout=0.01)
        assert q.empty()
        conn = get_connection()
        row = conn.execute('SELECT acted, skip_reason FROM signals WHERE id = ?',
                           (sid,)).fetchone()
        conn.close()
        assert row['acted'] == 0 and row['skip_reason'] == 'halted'


class TestSignalExpiry:
    def test_stale_queued_signal_never_executes(self, config, tmpdb):
        """Re-audit N1: a signal older than valid_for x interval is expired.
        Its premise is gone; executing it at current price is a random trade."""
        ex, q, adapter, collector = _executor(config)
        conn = get_connection(); _fresh_candle(conn)
        sid = insert_signal(conn, {
            'ts': int((time.time() - 7200) * 1000),  # 2h old, 15m interval
            'pair': 'BTC/USDT', 'tf': '15m', 'strategy_id': 'stub',
            'pattern': 'stub', 'direction': 'bullish', 'confidence': 0.6,
            'features_json': '{}', 'acted': 0, 'skip_reason': None,
            'mode': 'paper'})
        conn.commit(); conn.close()
        ex.handle_entry('BTC/USDT', _entry_signal(), sid)
        assert adapter.get_open_positions() == []
        conn = get_connection()
        row = conn.execute('SELECT skip_reason FROM signals WHERE id = ?', (sid,)).fetchone()
        conn.close()
        assert row['skip_reason'] == 'signal_expired'


class TestPendingOrders:
    """Resting buy-stop/limit simulation (re-audit N5, SPEC 5.1 / D-103)."""

    def _signal_row(self, conn, entry, stop, target, valid_for=2):
        return insert_signal(conn, {
            'ts': int(time.time() * 1000), 'pair': 'BTC/USDT', 'tf': '15m',
            'strategy_id': 'stub', 'pattern': 'stub', 'direction': 'bullish',
            'confidence': 0.6,
            'features_json': json.dumps({'entry': entry, 'stop': stop,
                                         'target': target, 'valid_for': valid_for}),
            'acted': 0, 'skip_reason': None, 'mode': 'paper'})

    def test_buy_stop_rests_then_fills_on_touch(self, config, tmpdb):
        ex, q, adapter, collector = _executor(config, bid=100.0, ask=100.1)
        conn = get_connection(); _fresh_candle(conn)
        sid = self._signal_row(conn, entry=103.0, stop=99.0, target=111.0)
        conn.commit(); conn.close()

        # Entry above ask -> resting buy-stop, no position yet, acted=1
        ex.handle_entry('BTC/USDT', _entry_signal(entry=103.0, stop=99.0), sid)
        assert adapter.get_open_positions() == []
        conn = get_connection()
        order = conn.execute("SELECT type, status, stop_price FROM orders "
                             "WHERE status='pending'").fetchone()
        acted = conn.execute('SELECT acted FROM signals WHERE id = ?', (sid,)).fetchone()['acted']
        conn.close()
        assert order['type'] == 'stop' and order['stop_price'] == 103.0
        assert acted == 1

        # Price stays below trigger: still resting
        ex._process_pending()
        assert adapter.get_open_positions() == []

        # Price crosses the trigger: fills, position created with the
        # SIGNAL's stop/target (reconstructed from the signal row)
        collector._bid, collector._ask = 103.4, 103.5
        ex._process_pending()
        positions = adapter.get_open_positions()
        assert len(positions) == 1
        assert positions[0]['stop_px'] == 99.0
        assert positions[0]['target_px'] == 111.0
        assert positions[0]['signal_id'] == sid

    def test_buy_limit_fills_at_limit_price(self, config, tmpdb):
        ex, q, adapter, collector = _executor(config, bid=100.0, ask=100.1)
        conn = get_connection(); _fresh_candle(conn)
        sid = self._signal_row(conn, entry=98.0, stop=95.0, target=104.0)
        conn.commit(); conn.close()

        ex.handle_entry('BTC/USDT', _entry_signal(entry=98.0, stop=95.0), sid)
        collector._bid, collector._ask = 97.8, 97.9  # trades through the limit
        ex._process_pending()
        positions = adapter.get_open_positions()
        assert len(positions) == 1
        assert positions[0]['entry_px'] == 98.0  # fills AT the limit, no slippage

    def test_pending_order_expires(self, config, tmpdb):
        ex, q, adapter, collector = _executor(config, bid=100.0, ask=100.1)
        conn = get_connection(); _fresh_candle(conn)
        sid = insert_signal(conn, {
            'ts': int((time.time() - 3600) * 1000),  # signal 1h old
            'pair': 'BTC/USDT', 'tf': '15m', 'strategy_id': 'stub',
            'pattern': 'stub', 'direction': 'bullish', 'confidence': 0.6,
            'features_json': json.dumps({'valid_for': 2}),  # 2x15m << 1h
            'acted': 0, 'skip_reason': None, 'mode': 'paper'})
        conn.commit(); conn.close()
        adapter.place_pending_buy('BTC/USDT', 'stop', 103.0, 1.0, sid,
                                  expires_ts=0)
        ex._process_pending()
        assert adapter.get_open_positions() == []
        conn = get_connection()
        status = conn.execute("SELECT status FROM orders WHERE type='stop'").fetchone()['status']
        conn.close()
        assert status == 'cancelled'

    def test_halt_cancels_pending_orders(self, config, tmpdb):
        ex, q, adapter, collector = _executor(config)
        adapter.place_pending_buy('BTC/USDT', 'stop', 103.0, 1.0, None,
                                  expires_ts=int(time.time() * 1000) + 10**7)
        with open(HALT_FILE, 'w') as f:
            json.dump({'halt_id': 'x', 'reason': 'test'}, f)
        ex.step(queue_timeout=0.01)
        conn = get_connection()
        status = conn.execute("SELECT status FROM orders WHERE type='stop'").fetchone()['status']
        conn.close()
        assert status == 'cancelled'
