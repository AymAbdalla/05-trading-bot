"""Tests for the risk gate (T5).

Tests:
1. Fixed notional position sizing (does NOT scale with balance)
2. Fee-to-edge gate: blocks tight stops, allows wide stops
3. Max trades/day: blocks after limit; sells and rejected orders don't count
4. Max concurrent positions: blocks at limit
5. Max positions per pair: blocks duplicate pair entries
6. Consecutive loss pause: blocks after 4 losses, EXPIRES after 24h
7. Inverted stop (stop >= entry) blocked
8. Ops backstops wired to equity_snapshots written by the paper adapter
9. All checks pass: returns approved verdict with qty and notional
"""
import os
import sys
import time
import pytest
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.risk import RiskGate, RiskVerdict
from engine.db import init_schema, get_db_path, get_connection
from engine.adapters.paper import PaperAdapter


@pytest.fixture
def config():
    return {
        'exchange': {
            'name': 'binanceus',
            'fees': {'maker': 0.001, 'taker': 0.001},
        },
        'risk': {
            'notional_cap_usd': 100,
            'fee_to_edge_max': 0.15,
            'max_trades_per_day': 1,
            'consecutive_loss_pause': 4,
            'max_concurrent_positions': 2,
            'max_positions_per_pair': 1,
            'daily_ops_stop_multiplier': 3,
            'weekly_ops_stop_multiplier': 15,
        }
    }


@pytest.fixture
def risk_gate(config):
    return RiskGate(config)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Fresh DB for each test."""
    db_path = str(tmp_path / "test_risk.db")
    monkeypatch.setenv("TRADING_DB_PATH", db_path)
    init_schema()
    return db_path


class TestPositionSizing:
    def test_fixed_notional_qty(self, risk_gate):
        """qty = notional_cap / entry. Does NOT scale with balance."""
        qty = risk_gate.compute_position_size(50000)
        assert qty == pytest.approx(0.002)  # $100 / $50000 = 0.002 BTC

    def test_notional_does_not_scale(self, risk_gate):
        """At $100k entry price, qty shrinks but notional stays $100."""
        qty = risk_gate.compute_position_size(100000)
        assert risk_gate.notional_cap == 100
        assert qty == pytest.approx(0.001)  # $100 / $100000

    def test_zero_entry_returns_zero(self, risk_gate):
        assert risk_gate.compute_position_size(0) == 0.0

    def test_negative_entry_returns_zero(self, risk_gate):
        assert risk_gate.compute_position_size(-100) == 0.0


class TestFeeToEdge:
    def test_blocks_tight_stop(self, risk_gate):
        """1% stop distance with 0.20% round-trip fees = 20% ratio, blocked."""
        # entry=50000, stop=49500 (1% stop), fee=0.20% round-trip
        passed, fee_cost, edge, ratio = risk_gate.check_fee_to_edge(50000, 49500)
        assert not passed
        assert ratio > 0.15  # above threshold

    def test_allows_wide_stop(self, risk_gate):
        """3% stop distance with 0.20% round-trip fees = ~6.7% ratio, passes."""
        # entry=50000, stop=48500 (3% stop)
        passed, fee_cost, edge, ratio = risk_gate.check_fee_to_edge(50000, 48500)
        assert passed
        assert ratio < 0.15

    def test_returns_fee_cost(self, risk_gate):
        passed, fee_cost, edge, ratio = risk_gate.check_fee_to_edge(50000, 49000)
        # fee_cost = $100 * 0.001 * 2 = $0.20
        assert fee_cost == pytest.approx(0.20)

    def test_zero_stop_blocks(self, risk_gate):
        passed, fee_cost, edge, ratio = risk_gate.check_fee_to_edge(50000, 0)
        assert not passed


class TestMaxTradesPerDay:
    def test_blocks_after_limit(self, risk_gate, db):
        """Insert 1 order today, verify next is blocked."""
        conn = get_connection()
        utc_now = int(time.time() * 1000)
        conn.execute(
            "INSERT INTO orders (id, cl_ord_id, ts, pair, side, type, qty, status, mode) "
            "VALUES ('test1', 'cl1', ?, 'BTC/USDT', 'buy', 'market', 0.002, 'filled', 'paper')",
            (utc_now,)
        )
        conn.commit()
        conn.close()

        verdict = risk_gate.check_order('ETH/USDT', 3000, 2950)
        assert not verdict.approved
        assert 'max_trades_per_day' in verdict.reason

    def test_allows_under_limit(self, risk_gate, db):
        """No orders today, clean DB, wide stop: must be approved outright
        (the old conditional assertion passed vacuously either way)."""
        verdict = risk_gate.check_order('BTC/USDT', 50000, 49000)
        assert verdict.approved is True
        assert verdict.reason == 'approved'

    def test_sell_orders_do_not_consume_entry_budget(self, risk_gate, db):
        """Regression (audited M2): only BUY orders count against the daily
        entry budget. A stop-out sell at 3am must not eat the day's one entry."""
        conn = get_connection()
        utc_now = int(time.time() * 1000)
        conn.execute(
            "INSERT INTO orders (id, cl_ord_id, ts, pair, side, type, qty, status, mode) "
            "VALUES ('sell1', 'cl-sell1', ?, 'BTC/USDT', 'sell', 'market', 0.002, 'filled', 'paper')",
            (utc_now,)
        )
        conn.commit()

        assert risk_gate.get_trades_today(conn) == 0
        conn.close()

        verdict = risk_gate.check_order('ETH/USDT', 3000, 2900)
        assert verdict.approved is True  # not blocked by max_trades_per_day

        # Now add a BUY today: budget consumed, next entry blocked (limit 1)
        conn = get_connection()
        conn.execute(
            "INSERT INTO orders (id, cl_ord_id, ts, pair, side, type, qty, status, mode) "
            "VALUES ('buy1', 'cl-buy1', ?, 'BTC/USDT', 'buy', 'market', 0.002, 'filled', 'paper')",
            (utc_now,)
        )
        conn.commit()
        assert risk_gate.get_trades_today(conn) == 1
        conn.close()

        verdict = risk_gate.check_order('ETH/USDT', 3000, 2900)
        assert not verdict.approved
        assert 'max_trades_per_day' in verdict.reason

    def test_rejected_orders_do_not_count(self, risk_gate, db):
        """A rejected buy today must not consume the entry budget."""
        conn = get_connection()
        utc_now = int(time.time() * 1000)
        conn.execute(
            "INSERT INTO orders (id, cl_ord_id, ts, pair, side, type, qty, status, mode) "
            "VALUES ('rej1', 'cl-rej1', ?, 'BTC/USDT', 'buy', 'market', 0.002, 'rejected', 'paper')",
            (utc_now,)
        )
        conn.commit()
        assert risk_gate.get_trades_today(conn) == 0
        conn.close()

        verdict = risk_gate.check_order('ETH/USDT', 3000, 2900)
        assert verdict.approved is True


class TestMaxConcurrentPositions:
    def test_blocks_at_limit(self, risk_gate, db):
        """Insert 2 open positions, verify next is blocked."""
        conn = get_connection()
        utc_now = int(time.time() * 1000)
        for i in range(2):
            conn.execute(
                "INSERT INTO positions (id, pair, strategy_id, opened_ts, entry_px, "
                "qty, stop_px, target_px, mode) "
                "VALUES (?, 'BTC/USDT', 'test', ?, 50000, 0.002, 49500, 51000, 'paper')",
                (f'pos{i}', utc_now)
            )
        conn.commit()
        conn.close()

        verdict = risk_gate.check_order('ETH/USDT', 3000, 2950)
        assert not verdict.approved
        assert 'max_concurrent' in verdict.reason


class TestMaxPositionsPerPair:
    def test_blocks_duplicate_pair(self, risk_gate, db):
        """1 open position on BTC/USDT, verify second BTC/USDT is blocked."""
        conn = get_connection()
        utc_now = int(time.time() * 1000)
        conn.execute(
            "INSERT INTO positions (id, pair, strategy_id, opened_ts, entry_px, "
            "qty, stop_px, target_px, mode) "
            "VALUES ('pos1', 'BTC/USDT', 'test', ?, 50000, 0.002, 49500, 51000, 'paper')",
            (utc_now,)
        )
        conn.commit()
        conn.close()

        verdict = risk_gate.check_order('BTC/USDT', 50000, 49000)
        assert not verdict.approved
        assert 'max_per_pair' in verdict.reason


class TestConsecutiveLossPause:
    def test_blocks_after_four_losses(self, risk_gate, db):
        """Insert 4 consecutive losing trades, verify next is blocked."""
        conn = get_connection()
        utc_now = int(time.time() * 1000)
        for i in range(4):
            conn.execute(
                "INSERT INTO positions (id, pair, strategy_id, opened_ts, closed_ts, "
                "entry_px, exit_px, qty, stop_px, target_px, pnl_net, mode) "
                "VALUES (?, 'BTC/USDT', 'test', ?, ?, 50000, 49500, 0.002, 49500, 51000, -1.0, 'paper')",
                (f'loss{i}', utc_now - 400000 + i * 100000, utc_now - 300000 + i * 100000)
            )
        conn.commit()
        conn.close()

        verdict = risk_gate.check_order('ETH/USDT', 3000, 2950)
        assert not verdict.approved
        assert 'consecutive_loss_pause' in verdict.reason

    @staticmethod
    def _insert_losses(count, closed_ts_base):
        """Insert `count` consecutive losing closed positions ending near
        closed_ts_base (each 1 minute apart)."""
        conn = get_connection()
        for i in range(count):
            conn.execute(
                "INSERT INTO positions (id, pair, strategy_id, opened_ts, closed_ts, "
                "entry_px, exit_px, qty, stop_px, target_px, pnl_net, mode) "
                "VALUES (?, 'BTC/USDT', 'test', ?, ?, 50000, 49500, 0.002, 49500, 51000, -1.0, 'paper')",
                (f'loss{i}', closed_ts_base - (count - i) * 120000,
                 closed_ts_base - (count - i) * 60000)
            )
        conn.commit()
        conn.close()

    def test_pause_expires_after_24h(self, risk_gate, db):
        """Regression (audited B2): the 4-loss pause must EXPIRE after 24h.
        Old code had no time window, so 4 losses deadlocked the bot forever."""
        utc_now = int(time.time() * 1000)
        self._insert_losses(4, utc_now - 25 * 3600 * 1000)  # 25h ago

        verdict = risk_gate.check_order('ETH/USDT', 3000, 2900)
        assert verdict.approved is True
        assert 'consecutive_loss_pause' not in verdict.reason

    def test_pause_active_within_24h(self, risk_gate, db):
        """4 losses 1h ago: pause is still active and must block."""
        utc_now = int(time.time() * 1000)
        self._insert_losses(4, utc_now - 1 * 3600 * 1000)  # 1h ago

        verdict = risk_gate.check_order('ETH/USDT', 3000, 2900)
        assert not verdict.approved
        assert 'consecutive_loss_pause' in verdict.reason

    def test_get_consecutive_losses_returns_tuple(self, risk_gate, db):
        """get_consecutive_losses returns (count, last_loss_ts) - the ts is
        what makes the pause expirable."""
        utc_now = int(time.time() * 1000)
        self._insert_losses(3, utc_now - 3600 * 1000)

        conn = get_connection()
        count, last_loss_ts = risk_gate.get_consecutive_losses(conn)
        conn.close()
        assert count == 3
        assert last_loss_ts == pytest.approx(utc_now - 3600 * 1000 - 60000, abs=5000)

    def test_allows_after_win(self, risk_gate, db):
        """3 losses then 1 win, should not be blocked by consecutive loss."""
        conn = get_connection()
        utc_now = int(time.time() * 1000)
        for i in range(3):
            conn.execute(
                "INSERT INTO positions (id, pair, strategy_id, opened_ts, closed_ts, "
                "entry_px, exit_px, qty, stop_px, target_px, pnl_net, mode) "
                "VALUES (?, 'BTC/USDT', 'test', ?, ?, 50000, 49500, 0.002, 49500, 51000, -1.0, 'paper')",
                (f'loss{i}', utc_now - 400000 + i * 100000, utc_now - 300000 + i * 100000)
            )
        # Winning trade breaks the streak
        conn.execute(
            "INSERT INTO positions (id, pair, strategy_id, opened_ts, closed_ts, "
            "entry_px, exit_px, qty, stop_px, target_px, pnl_net, mode) "
            "VALUES ('win1', 'BTC/USDT', 'test', ?, ?, 50000, 51000, 0.002, 49500, 51000, 2.0, 'paper')",
            (utc_now - 100000, utc_now - 50000)
        )
        conn.commit()
        conn.close()

        verdict = risk_gate.check_order('ETH/USDT', 3000, 2900)
        # Streak broken by the win: nothing else in a clean DB blocks this,
        # so the verdict must be an unconditional approval (the old version
        # asserted inside an `if not approved` and passed vacuously).
        assert verdict.approved is True


class TestInvertedStop:
    def test_stop_above_entry_blocked(self, risk_gate, db):
        """Regression (audited M4): stop >= entry on a long is an inverted
        stop. abs() in the fee-to-edge math used to give it a fake 'edge' and
        let it through; it must be blocked with invalid_stop."""
        verdict = risk_gate.check_order('BTC/USDT', 100, 105)
        assert not verdict.approved
        assert 'invalid_stop' in verdict.reason

    def test_stop_equal_entry_blocked(self, risk_gate, db):
        verdict = risk_gate.check_order('BTC/USDT', 100, 100)
        assert not verdict.approved
        assert 'invalid_stop' in verdict.reason


class TestOpsBackstopWiring:
    def test_paper_adapter_snapshot_feeds_risk_gate(self, risk_gate, db):
        """Regression (audited M5): equity_snapshots was read by the ops
        backstops but written by NOTHING. PaperAdapter.write_equity_snapshot
        must produce a row that get_current_equity actually returns."""
        adapter = PaperAdapter({
            'exchange': {'fees': {'maker': 0.001, 'taker': 0.001},
                         'slippage': {'market': 0.0005}},
            'paper': {'starting_equity': 2000.0},
        })
        equity = adapter.write_equity_snapshot()  # no collector: closed PnL only

        conn = get_connection()
        stored = risk_gate.get_current_equity(conn)
        conn.close()

        assert equity == pytest.approx(2000.0)
        assert stored is not None
        assert stored == pytest.approx(2000.0)


class TestFullCheckOrder:
    def test_all_checks_pass(self, risk_gate, db):
        """Clean DB, valid entry/stop, all checks should pass."""
        # Insert an equity snapshot so ops backstop has data
        conn = get_connection()
        utc_now = int(time.time() * 1000)
        conn.execute(
            "INSERT INTO equity_snapshots (ts, equity, cash, open_risk, mode) "
            "VALUES (?, 2000.0, 2000.0, 0.0, 'paper')",
            (utc_now,)
        )
        conn.commit()
        conn.close()

        # entry=50000, stop=48500 (3% stop, passes fee-to-edge)
        verdict = risk_gate.check_order('BTC/USDT', 50000, 48500)
        assert verdict.approved
        assert verdict.reason == 'approved'
        assert verdict.qty == pytest.approx(0.002)  # $100 / $50000
        assert verdict.notional == 100

    def test_returns_fee_and_edge_in_verdict(self, risk_gate, db):
        """Verdict should include fee_cost and edge for logging."""
        conn = get_connection()
        utc_now = int(time.time() * 1000)
        conn.execute(
            "INSERT INTO equity_snapshots (ts, equity, cash, open_risk, mode) "
            "VALUES (?, 2000.0, 2000.0, 0.0, 'paper')",
            (utc_now,)
        )
        conn.commit()
        conn.close()

        verdict = risk_gate.check_order('BTC/USDT', 50000, 48500)
        assert verdict.approved
        assert verdict.fee_cost > 0
        assert verdict.edge > 0
