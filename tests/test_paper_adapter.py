"""Tests for the paper adapter (T6).

Tests:
1. Market buy fills at ask + slippage (not mid)
2. Market sell fills at bid - slippage (not mid)
3. Order and fill rows written to DB
4. Open position creates position row with stop and target
5. Close position computes PnL, fees, R-multiple
6. Equity calculation: starting + closed PnL
7. Taker fee applied correctly
8. Audit log written before order
"""
import os
import sys
import time
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.adapters.paper import PaperAdapter
from engine.db import init_schema, get_db_path, get_connection
import sqlite3


@pytest.fixture
def config():
    return {
        'exchange': {
            'name': 'binanceus',
            'fees': {'maker': 0.001, 'taker': 0.001},
            'slippage': {'market': 0.0005, 'limit': 0.0},
        },
        'paper': {
            'starting_equity': 2000.0,
            'currency': 'USDT',
        }
    }


@pytest.fixture
def adapter(config):
    return PaperAdapter(config)


@pytest.fixture
def mock_collector():
    """Mock collector with controllable bid/ask."""
    collector = MagicMock()
    collector.exchange = MagicMock()

    def fetch_ticker(pair):
        return {
            'bid': 49990.0,
            'ask': 50010.0,
            'last': 50000.0,
        }

    collector.exchange.fetch_ticker.side_effect = fetch_ticker
    return collector


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_paper.db")
    monkeypatch.setenv("TRADING_DB_PATH", db_path)
    init_schema()
    return db_path


class FakeTickerCollector:
    """Collector stub with a controllable bid/ask for exit simulation."""

    def __init__(self, bid: float, ask: float):
        self._bid = bid
        self._ask = ask
        self.exchange = self  # adapter calls collector.exchange.fetch_ticker

    def set_price(self, bid: float, ask: float):
        self._bid = bid
        self._ask = ask

    def fetch_ticker(self, pair):
        return {'bid': self._bid, 'ask': self._ask,
                'last': (self._bid + self._ask) / 2}


TAKER = 0.001
SLIP = 0.0005


class TestFillModel:
    def test_buy_fills_at_ask_plus_slippage(self, adapter, mock_collector, db):
        """F8/R8 fix: fills at ask, NOT mid."""
        result = adapter.place_market_buy(mock_collector, 'BTC/USDT', 0.002)
        assert result is not None
        # ask=50010, slippage=0.05%, so fill = 50010 * 1.0005 = 50035.005
        assert result['fill_price'] == pytest.approx(50010 * 1.0005, rel=0.001)
        # Should NOT be mid (50000) or mid + slippage
        assert result['fill_price'] > 50010  # above ask

    def test_sell_fills_at_bid_minus_slippage(self, adapter, mock_collector, db):
        """F8/R8 fix: sells at bid, NOT mid."""
        result = adapter.place_market_sell(mock_collector, 'BTC/USDT', 0.002)
        assert result is not None
        # bid=49990, slippage=0.05%, so fill = 49990 * 0.9995 = 49965.005
        assert result['fill_price'] == pytest.approx(49990 * 0.9995, rel=0.001)
        # Should NOT be mid (50000)
        assert result['fill_price'] < 49990  # below bid

    def test_taker_fee_applied(self, adapter, mock_collector, db):
        """Taker fee = 0.10% applied on fill."""
        result = adapter.place_market_buy(mock_collector, 'BTC/USDT', 0.002)
        expected_fee = result['fill_price'] * 0.002 * 0.001
        assert result['fee'] == pytest.approx(expected_fee, rel=0.01)

    def test_no_price_data_returns_none(self, adapter, db):
        """If ticker fetch fails, return None."""
        collector = MagicMock()
        collector.exchange = MagicMock()
        collector.exchange.fetch_ticker.return_value = {}
        result = adapter.place_market_buy(collector, 'BTC/USDT', 0.002)
        assert result is None


class TestOrderAndFillLogging:
    def test_order_row_written(self, adapter, mock_collector, db):
        adapter.place_market_buy(mock_collector, 'BTC/USDT', 0.002)
        conn = get_connection()
        rows = conn.execute("SELECT * FROM orders WHERE side = 'buy'").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]['pair'] == 'BTC/USDT'
        assert rows[0]['status'] == 'filled'
        assert rows[0]['mode'] == 'paper'

    def test_fill_row_written(self, adapter, mock_collector, db):
        adapter.place_market_buy(mock_collector, 'BTC/USDT', 0.002)
        conn = get_connection()
        rows = conn.execute("SELECT * FROM fills").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]['fee'] > 0

    def test_audit_log_written(self, adapter, mock_collector, db):
        adapter.place_market_buy(mock_collector, 'BTC/USDT', 0.002)
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE event_type = 'order_placed'"
        ).fetchall()
        conn.close()
        assert len(rows) >= 1


class TestPositionManagement:
    def test_open_position(self, adapter, mock_collector, db):
        """Open position creates row with entry, stop, target."""
        pos_id = adapter.open_position(
            mock_collector, 'BTC/USDT', 'bullish_engulfing',
            entry=50000, stop=49500, target=51000, qty=0.002
        )
        assert pos_id is not None

        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM positions WHERE id = ?", (pos_id,)
        ).fetchone()
        conn.close()

        assert row is not None
        assert row['pair'] == 'BTC/USDT'
        assert row['strategy_id'] == 'bullish_engulfing'
        assert row['stop_px'] == 49500
        assert row['target_px'] == 51000
        assert row['closed_ts'] is None
        assert row['mode'] == 'paper'

    def test_close_position_pnl(self, adapter, mock_collector, db):
        """Close position computes PnL, fees, R-multiple."""
        # Open at ~50035 (ask + slippage)
        pos_id = adapter.open_position(
            mock_collector, 'BTC/USDT', 'bullish_engulfing',
            entry=50000, stop=49500, target=51000, qty=0.002
        )
        assert pos_id is not None

        # Close at ~49965 (bid - slippage)
        result = adapter.close_position(mock_collector, pos_id, exit_reason='signal')
        assert result is not None

        # Entry ~50035, exit ~49965, so PnL is negative (bought high, sold low)
        assert result['pnl_gross'] < 0  # lost money
        assert result['fees'] > 0  # fees charged
        assert result['r_multiple'] < 0  # negative R
        assert result['exit_reason'] == 'signal'

        # Position should be closed in DB
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM positions WHERE id = ?", (pos_id,)
        ).fetchone()
        conn.close()
        assert row['closed_ts'] is not None
        assert row['pnl_net'] is not None

    def test_close_already_closed_returns_none(self, adapter, mock_collector, db):
        """Can't close a position that's already closed."""
        pos_id = adapter.open_position(
            mock_collector, 'BTC/USDT', 'test',
            entry=50000, stop=49500, target=51000, qty=0.002
        )
        adapter.close_position(mock_collector, pos_id)

        # Try to close again
        result = adapter.close_position(mock_collector, pos_id)
        assert result is None

    def test_get_open_positions(self, adapter, mock_collector, db):
        """get_open_positions returns only unclosed."""
        pos1 = adapter.open_position(
            mock_collector, 'BTC/USDT', 'test1',
            entry=50000, stop=49500, target=51000, qty=0.002
        )
        pos2 = adapter.open_position(
            mock_collector, 'ETH/USDT', 'test2',
            entry=3000, stop=2950, target=3100, qty=0.033
        )
        adapter.close_position(mock_collector, pos1)

        open_positions = adapter.get_open_positions()
        assert len(open_positions) == 1
        assert open_positions[0]['pair'] == 'ETH/USDT'


class TestCheckExits:
    """Regression tests for the audited B3 blocker: nothing in the paper path
    ever compared live price to stop_px/target_px, so positions could sit
    open through a 30% drop forever. check_exits is that missing loop."""

    def test_stop_hit_fills_at_live_bid_not_stop_price(self, adapter, db):
        """Bid gaps below the stop: exit fills at bid*(1-slip), NOT at the
        stop price - honest about gap-throughs."""
        collector = FakeTickerCollector(bid=99.9, ask=100.1)
        pos_id = adapter.open_position(
            collector, 'BTC/USDT', 'test',
            entry=100, stop=95, target=110, qty=1.0
        )
        assert pos_id is not None
        entry_px = 100.1 * (1 + SLIP)  # ask + slippage

        # Price gaps through the stop: bid drops straight to 94
        collector.set_price(bid=94.0, ask=94.2)
        closed = adapter.check_exits(collector)

        assert len(closed) == 1
        result = closed[0]
        assert result['position_id'] == pos_id
        assert result['exit_reason'] == 'stop'
        expected_exit = 94.0 * (1 - SLIP)
        assert result['exit_px'] == pytest.approx(expected_exit, rel=1e-9)
        assert abs(result['exit_px'] - 95.0) > 0.9  # NOT filled at the stop

        # DB row closed with consistent PnL: fees on both legs
        conn = get_connection()
        row = conn.execute("SELECT * FROM positions WHERE id = ?", (pos_id,)).fetchone()
        conn.close()
        assert row['closed_ts'] is not None
        assert row['exit_reason'] == 'stop'
        buy_fee = entry_px * 1.0 * TAKER
        sell_fee = expected_exit * 1.0 * TAKER
        expected_pnl = (expected_exit - entry_px) * 1.0 - buy_fee - sell_fee
        assert row['pnl_net'] == pytest.approx(expected_pnl, rel=1e-6)
        assert row['pnl_net'] < 0

    def test_target_hit_closes_with_target_reason(self, adapter, db):
        """Bid rises through the target: closed with exit_reason='target'."""
        collector = FakeTickerCollector(bid=99.9, ask=100.1)
        pos_id = adapter.open_position(
            collector, 'BTC/USDT', 'test',
            entry=100, stop=95, target=110, qty=1.0
        )
        assert pos_id is not None
        entry_px = 100.1 * (1 + SLIP)

        collector.set_price(bid=111.0, ask=111.2)
        closed = adapter.check_exits(collector)

        assert len(closed) == 1
        result = closed[0]
        assert result['exit_reason'] == 'target'
        expected_exit = 111.0 * (1 - SLIP)  # market sell at live bid
        assert result['exit_px'] == pytest.approx(expected_exit, rel=1e-9)

        conn = get_connection()
        row = conn.execute("SELECT * FROM positions WHERE id = ?", (pos_id,)).fetchone()
        conn.close()
        assert row['closed_ts'] is not None
        buy_fee = entry_px * 1.0 * TAKER
        sell_fee = expected_exit * 1.0 * TAKER
        expected_pnl = (expected_exit - entry_px) * 1.0 - buy_fee - sell_fee
        assert row['pnl_net'] == pytest.approx(expected_pnl, rel=1e-6)
        assert row['pnl_net'] > 0

    def test_no_exit_when_price_between_stop_and_target(self, adapter, db):
        """Price inside the bracket: nothing closes."""
        collector = FakeTickerCollector(bid=99.9, ask=100.1)
        pos_id = adapter.open_position(
            collector, 'BTC/USDT', 'test',
            entry=100, stop=95, target=110, qty=1.0
        )
        collector.set_price(bid=100.5, ask=100.7)
        closed = adapter.check_exits(collector)
        assert closed == []
        assert len(adapter.get_open_positions()) == 1


class TestSignalIdPersistence:
    def test_open_position_persists_signal_id(self, adapter, db):
        """Regression: positions.signal_id was silently dropped on insert,
        breaking the signal->position audit chain."""
        collector = FakeTickerCollector(bid=99.9, ask=100.1)
        pos_id = adapter.open_position(
            collector, 'BTC/USDT', 'test',
            entry=100, stop=95, target=110, qty=1.0, signal_id='sig-abc-123'
        )
        assert pos_id is not None

        conn = get_connection()
        row = conn.execute("SELECT signal_id FROM positions WHERE id = ?",
                           (pos_id,)).fetchone()
        conn.close()
        assert row['signal_id'] == 'sig-abc-123'


class TestEquity:
    def test_starting_equity(self, adapter, db):
        """With no trades, equity = starting."""
        assert adapter.get_equity() == 2000.0

    def test_equity_after_loss(self, adapter, mock_collector, db):
        """After a losing trade, equity decreases."""
        pos_id = adapter.open_position(
            mock_collector, 'BTC/USDT', 'test',
            entry=50000, stop=49500, target=51000, qty=0.002
        )
        adapter.close_position(mock_collector, pos_id)

        equity = adapter.get_equity()
        assert equity < 2000.0  # lost money + fees

    def test_equity_after_profit(self, adapter, mock_collector, db):
        """After a winning trade, equity increases."""
        # Use a ticker with enough spread for profit even after fees
        # Buy at ask=50010, sell at bid=51500 (price went up)
        call_count = [0]
        def favorable_ticker(pair):
            call_count[0] += 1
            if call_count[0] <= 1:
                # Buy: price at 50000
                return {'bid': 49990.0, 'ask': 50010.0, 'last': 50000.0}
            else:
                # Sell: price went up to 51500
                return {'bid': 51490.0, 'ask': 51510.0, 'last': 51500.0}

        mock_collector.exchange.fetch_ticker.side_effect = favorable_ticker

        pos_id = adapter.open_position(
            mock_collector, 'BTC/USDT', 'test',
            entry=50000, stop=49500, target=51000, qty=0.002
        )
        result = adapter.close_position(mock_collector, pos_id)
        # Buy at ~50035, sell at ~51464, gross = (51464 - 50035) * 0.002 = ~$2.86
        # Fees = ~$0.20 + ~$0.20 = ~$0.40, net = ~$2.46
        assert result['pnl_net'] > 0

        equity = adapter.get_equity()
        assert equity > 2000.0

    def test_equity_includes_unrealized_pnl_of_open_positions(self, adapter, db):
        """Regression: get_equity with a collector must mark open positions to
        the live bid. The old closed-PnL-only version showed no change while
        an open position bled out."""
        collector = FakeTickerCollector(bid=99.9, ask=100.1)
        pos_id = adapter.open_position(
            collector, 'BTC/USDT', 'test',
            entry=100, stop=90, target=110, qty=1.0
        )
        assert pos_id is not None
        entry_px = 100.1 * (1 + SLIP)  # ~100.15

        # Price drops to 90: open position is ~$10/unit underwater
        collector.set_price(bid=90.0, ask=90.2)
        equity = adapter.get_equity(collector)

        expected = 2000.0 + (90.0 - entry_px) * 1.0
        assert equity == pytest.approx(expected, rel=1e-6)
        assert equity < 2000.0 - 10.0  # down by ~$10 (qty 1) plus entry slippage

        # Without a collector the drop is invisible (closed PnL only) - that
        # fallback is why the executor must always pass the collector.
        assert adapter.get_equity() == pytest.approx(2000.0)


class TestSlippageInstrumentation:
    """Execution speed does not matter at 15m bars, but the PRICE DRIFT that
    latency causes is the only execution number the backtest depends on
    (0.05% assumed). This records realized-vs-assumed so paper mode can tell
    us whether that assumption holds BEFORE money is at risk."""

    def test_realized_slippage_recorded_against_reference(self, config, db):
        import json as _json
        adapter = PaperAdapter(config)
        collector = FakeTickerCollector(bid=100.0, ask=100.2)
        # Signal said 100.0; market moved to 100.2 ask by the time we filled.
        adapter.place_market_buy(collector, 'BTC/USDT', 1.0, reference_px=100.0)

        conn = get_connection()
        row = conn.execute(
            "SELECT payload_json FROM audit_log WHERE event_type = 'order_placed' "
            "ORDER BY ts DESC LIMIT 1").fetchone()
        conn.close()
        payload = _json.loads(row['payload_json'])

        assert payload['reference_px'] == 100.0
        # ask 100.2 plus 0.05% slippage = 100.2501 -> ~25 bps above reference
        assert payload['realized_slippage_bps'] == pytest.approx(25.01, abs=0.5)
        assert payload['assumed_slippage_bps'] == pytest.approx(5.0)
        # Realized far exceeding assumed is the signal that the backtest's
        # cost model is too optimistic.
        assert payload['realized_slippage_bps'] > payload['assumed_slippage_bps']
        assert payload['spread_bps'] == pytest.approx(20.0, abs=0.5)

    def test_no_reference_price_records_none_not_zero(self, config, db):
        """A missing reference must not masquerade as zero slippage."""
        import json as _json
        adapter = PaperAdapter(config)
        collector = FakeTickerCollector(bid=100.0, ask=100.2)
        adapter.place_market_buy(collector, 'BTC/USDT', 1.0)
        conn = get_connection()
        row = conn.execute(
            "SELECT payload_json FROM audit_log WHERE event_type = 'order_placed' "
            "ORDER BY ts DESC LIMIT 1").fetchone()
        conn.close()
        assert _json.loads(row['payload_json'])['realized_slippage_bps'] is None
