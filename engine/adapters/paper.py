"""Paper trading adapter: internal fill simulator.

Simulates order execution on live market data without placing real orders.
Maintains a virtual $2,000 ledger in SQLite.

Key fix from Claude review (F8/R8): fills at ask/bid, NOT mid.
- Buy market orders fill at ask + slippage
- Sell market orders fill at bid - slippage
- Limit orders fill when live price crosses the limit
- Stops trigger off the live feed

All fills, fees, and PnL are recorded identically to live mode.
"""
import time
import logging
import uuid
from typing import Optional, Dict

from engine.db import get_connection, insert_audit

logger = logging.getLogger(__name__)


class PaperAdapter:
    """Simulates order execution on live market data."""

    def __init__(self, config: dict):
        self.config = config
        self.starting_equity = config.get('paper', {}).get('starting_equity', 2000.0)
        self.currency = config.get('paper', {}).get('currency', 'USDT')

        fees = config.get('exchange', {}).get('fees', {})
        self.taker_fee = fees.get('taker', 0.001)   # 0.10%
        self.maker_fee = fees.get('maker', 0.001)   # 0.10%

        slip = config.get('exchange', {}).get('slippage', {})
        self.market_slippage = slip.get('market', 0.0005)  # 0.05% adverse
        self.limit_slippage = slip.get('limit', 0.0)

        self.mode = 'paper'

    def _gen_order_id(self) -> str:
        """Generate a unique client order ID."""
        return f"paper-{uuid.uuid4().hex[:12]}"

    def _get_current_price(self, collector, pair: str) -> Optional[Dict]:
        """Get current bid/ask/mid from the live feed."""
        try:
            ticker = collector.exchange.fetch_ticker(pair)
            bid = ticker.get('bid')
            ask = ticker.get('ask')
            if bid and ask:
                mid = (bid + ask) / 2
                return {'bid': bid, 'ask': ask, 'mid': mid}
            # Fallback to last price
            last = ticker.get('last')
            if last:
                return {'bid': last, 'ask': last, 'mid': last}
            return None
        except Exception as e:
            logger.error(f"Error fetching ticker for {pair}: {e}")
            return None

    def place_market_buy(self, collector, pair: str, qty: float,
                         signal_id: Optional[str] = None,
                         reference_px: Optional[float] = None) -> Optional[dict]:
        """Simulate a market buy order.

        Fills at ask + adverse slippage (you pay the ask, plus a bit more).
        Taker fee applied.

        reference_px: the price the SIGNAL was based on. Recording the gap
        between that and the actual fill is how we find out whether the
        backtest's slippage assumption (0.05%) matches reality. Execution
        LATENCY does not matter at 15m bars, but the price drift that
        latency causes is exactly what this measures - and it is the only
        execution number the backtest depends on.
        """
        prices = self._get_current_price(collector, pair)
        if prices is None:
            logger.error(f"Cannot fill paper buy: no price data for {pair}")
            return None

        fill_price = prices['ask'] * (1 + self.market_slippage)
        fee = fill_price * qty * self.taker_fee

        order_id = self._gen_order_id()
        order_pk = str(uuid.uuid4())  # PK for orders table
        ts = int(time.time() * 1000)

        # Write order + fill to DB (audit before fill, same as live)
        conn = get_connection()
        try:
            # Audit log first
            # Realized-vs-assumed slippage. Assumed is a config constant;
            # realized is what the market actually did between signal and
            # fill. If these diverge in paper, every backtest number is
            # built on the wrong cost and we find out BEFORE risking money.
            slip_bps = None
            if reference_px:
                slip_bps = (fill_price - reference_px) / reference_px * 10_000
            insert_audit(conn, 'paper_adapter', 'order_placed', {
                'cl_ord_id': order_id, 'pair': pair, 'side': 'buy',
                'type': 'market', 'qty': qty, 'signal_id': signal_id,
                'reference_px': reference_px, 'fill_px': fill_price,
                'realized_slippage_bps': None if slip_bps is None else round(slip_bps, 2),
                'assumed_slippage_bps': round(self.market_slippage * 10_000, 2),
                'spread_bps': round((prices['ask'] - prices['bid'])
                                    / prices['bid'] * 10_000, 2) if prices['bid'] else None,
            })

            # Order row (id = PK, cl_ord_id = client ref)
            conn.execute(
                "INSERT INTO orders (id, cl_ord_id, ts, pair, side, type, qty, "
                "status, exchange_order_id, signal_id, mode) "
                "VALUES (?, ?, ?, ?, 'buy', 'market', ?, 'filled', ?, ?, 'paper')",
                (order_pk, order_id, ts, pair, qty,
                 f"paper-{order_id}", signal_id)
            )

            # Fill row (order_id references orders.id = PK)
            fill_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO fills (id, order_id, ts, price, qty, fee) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (fill_id, order_pk, ts, fill_price, qty, fee)
            )

            conn.commit()
            logger.info(
                f"PAPER BUY {pair}: {qty} @ {fill_price:.2f} "
                f"(ask={prices['ask']:.2f}, fee={fee:.4f})"
            )

            return {
                'order_id': order_id,
                'fill_price': fill_price,
                'qty': qty,
                'fee': fee,
                'side': 'buy',
                'pair': pair,
                'ts': ts,
            }
        except Exception as e:
            logger.error(f"Failed to write paper buy order: {e}")
            conn.rollback()
            return None
        finally:
            conn.close()

    def place_market_sell(self, collector, pair: str, qty: float,
                          signal_id: Optional[str] = None,
                          position_id: Optional[str] = None) -> Optional[dict]:
        """Simulate a market sell order (closing a position).

        Fills at bid - adverse slippage (you get the bid, minus a bit).
        Taker fee applied.
        """
        prices = self._get_current_price(collector, pair)
        if prices is None:
            logger.error(f"Cannot fill paper sell: no price data for {pair}")
            return None

        fill_price = prices['bid'] * (1 - self.market_slippage)
        fee = fill_price * qty * self.taker_fee

        order_id = self._gen_order_id()
        order_pk = str(uuid.uuid4())  # PK for orders table
        ts = int(time.time() * 1000)

        conn = get_connection()
        try:
            insert_audit(conn, 'paper_adapter', 'order_placed', {
                'cl_ord_id': order_id, 'pair': pair, 'side': 'sell',
                'type': 'market', 'qty': qty, 'signal_id': signal_id,
                'position_id': position_id,
            })

            conn.execute(
                "INSERT INTO orders (id, cl_ord_id, ts, pair, side, type, qty, "
                "status, exchange_order_id, signal_id, mode) "
                "VALUES (?, ?, ?, ?, 'sell', 'market', ?, 'filled', ?, ?, 'paper')",
                (order_pk, order_id, ts, pair, qty,
                 f"paper-{order_id}", signal_id)
            )

            fill_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO fills (id, order_id, ts, price, qty, fee) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (fill_id, order_pk, ts, fill_price, qty, fee)
            )

            conn.commit()
            logger.info(
                f"PAPER SELL {pair}: {qty} @ {fill_price:.2f} "
                f"(bid={prices['bid']:.2f}, fee={fee:.4f})"
            )

            return {
                'order_id': order_id,
                'fill_price': fill_price,
                'qty': qty,
                'fee': fee,
                'side': 'sell',
                'pair': pair,
                'ts': ts,
            }
        except Exception as e:
            logger.error(f"Failed to write paper sell order: {e}")
            conn.rollback()
            return None
        finally:
            conn.close()

    def open_position(self, collector, pair: str, strategy_id: str,
                      entry: float, stop: float, target: float,
                      qty: float, signal_id: Optional[str] = None) -> Optional[str]:
        """Open a new position: place market buy, create position row, attach stop.

        Returns position_id if successful.
        """
        fill = self.place_market_buy(collector, pair, qty, signal_id,
                                     reference_px=entry)
        if fill is None:
            return None

        position_id = str(uuid.uuid4())
        ts = int(time.time() * 1000)

        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO positions (id, pair, strategy_id, signal_id, opened_ts, "
                "entry_px, qty, stop_px, target_px, mode) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'paper')",
                (position_id, pair, strategy_id, signal_id, ts,
                 fill['fill_price'], qty, stop, target)
            )
            conn.commit()

            logger.info(
                f"POSITION OPENED: {position_id[:8]} {pair} "
                f"qty={qty} entry={fill['fill_price']:.2f} stop={stop:.2f} target={target:.2f}"
            )
            return position_id
        except Exception as e:
            logger.error(f"Failed to open position: {e}")
            conn.rollback()
            return None
        finally:
            conn.close()

    def close_position(self, collector, position_id: str,
                       exit_reason: str = 'signal') -> Optional[dict]:
        """Close an open position: place market sell, update position row with PnL.

        Returns closing dict with exit_price, pnl_gross, pnl_net, fees, r_multiple.
        """
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM positions WHERE id = ? AND closed_ts IS NULL",
                (position_id,)
            ).fetchone()

            if row is None:
                logger.error(f"Cannot close: position {position_id} not found or already closed")
                return None

            pair = row['pair']
            entry_px = row['entry_px']
            qty = row['qty']
            stop_px = row['stop_px']
            target_px = row['target_px']
            strategy_id = row['strategy_id']

            # Sell at bid
            fill = self.place_market_sell(collector, pair, qty,
                                          position_id=position_id)
            if fill is None:
                return None

            exit_px = fill['fill_price']
            sell_fee = fill['fee']

            # Get the buy fee from the original fill
            buy_fill_row = conn.execute(
                "SELECT f.fee FROM fills f "
                "JOIN orders o ON f.order_id = o.id "
                "JOIN positions p ON p.id = ? "
                "WHERE o.pair = p.pair AND o.side = 'buy' AND o.status = 'filled' "
                "AND o.ts <= p.opened_ts "
                "ORDER BY f.ts DESC LIMIT 1",
                (position_id,)
            ).fetchone()
            buy_fee = buy_fill_row['fee'] if buy_fill_row else 0.0

            total_fees = buy_fee + sell_fee
            pnl_gross = (exit_px - entry_px) * qty
            pnl_net = pnl_gross - total_fees

            # R-multiple: profit/loss relative to initial risk
            risk = (entry_px - stop_px) * qty
            r_multiple = pnl_net / risk if risk > 0 else 0.0

            ts = int(time.time() * 1000)
            conn.execute(
                "UPDATE positions SET closed_ts = ?, exit_px = ?, pnl_gross = ?, "
                "pnl_net = ?, fees = ?, r_multiple = ?, exit_reason = ? "
                "WHERE id = ?",
                (ts, exit_px, pnl_gross, pnl_net, total_fees, r_multiple,
                 exit_reason, position_id)
            )
            conn.commit()

            result = {
                'position_id': position_id,
                'pair': pair,
                'strategy_id': strategy_id,
                'entry_px': entry_px,
                'exit_px': exit_px,
                'qty': qty,
                'pnl_gross': pnl_gross,
                'pnl_net': pnl_net,
                'fees': total_fees,
                'r_multiple': r_multiple,
                'exit_reason': exit_reason,
            }

            logger.info(
                f"POSITION CLOSED: {position_id[:8]} {pair} "
                f"PnL=${pnl_net:.4f} ({r_multiple:.2f}R, reason={exit_reason})"
            )
            return result
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            conn.rollback()
            return None
        finally:
            conn.close()

    def get_open_positions(self) -> list:
        """Get all open positions."""
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM positions WHERE closed_ts IS NULL ORDER BY opened_ts"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def place_pending_buy(self, pair: str, order_type: str, trigger_price: float,
                          qty: float, signal_id: Optional[str],
                          expires_ts: int) -> Optional[str]:
        """Rest a buy-stop or buy-limit order (SPEC 5.1 order semantics,
        re-audit N5). No fill happens here; check_pending_orders() monitors
        the live feed. Position stop/target are reconstructed at fill time
        from the originating signal row."""
        assert order_type in ('stop', 'limit')
        order_id = self._gen_order_id()
        order_pk = str(uuid.uuid4())
        ts = int(time.time() * 1000)
        conn = get_connection()
        try:
            insert_audit(conn, 'paper_adapter', 'pending_order_placed', {
                'cl_ord_id': order_id, 'pair': pair, 'type': order_type,
                'trigger': trigger_price, 'qty': qty, 'signal_id': signal_id,
                'expires_ts': expires_ts,
            })
            conn.execute(
                "INSERT INTO orders (id, cl_ord_id, ts, pair, side, type, qty, "
                "limit_price, stop_price, status, signal_id, mode) "
                "VALUES (?, ?, ?, ?, 'buy', ?, ?, ?, ?, 'pending', ?, 'paper')",
                (order_pk, order_id, ts, pair, order_type, qty,
                 trigger_price if order_type == 'limit' else None,
                 trigger_price if order_type == 'stop' else None,
                 signal_id))
            conn.commit()
            logger.info(f"PENDING {order_type.upper()} BUY {pair}: {qty} @ {trigger_price:.2f}")
            return order_pk
        except Exception as e:
            logger.error(f"Failed to place pending order: {e}")
            conn.rollback()
            return None
        finally:
            conn.close()

    def check_pending_orders(self, collector, expiry_lookup=None) -> list:
        """Monitor resting orders against the live feed. Buy-stop triggers
        when the ask reaches the level (fills as a market buy at current
        ask + slippage - honest about gaps past the trigger). Buy-limit
        fills AT the limit when the bid trades through it (resting maker
        order: maker fee, no slippage). expiry_lookup(order_row) -> ts is
        supplied by the executor; expired orders are cancelled."""
        events = []
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status = 'pending' AND side = 'buy' "
                "AND mode = 'paper'").fetchall()
        finally:
            conn.close()

        now = int(time.time() * 1000)
        for row in rows:
            expires_ts = expiry_lookup(row) if expiry_lookup else None
            if expires_ts is not None and now > expires_ts:
                conn = get_connection()
                try:
                    conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?",
                                 (row['id'],))
                    insert_audit(conn, 'paper_adapter', 'pending_order_expired',
                                 {'order_id': row['id'], 'signal_id': row['signal_id']})
                    conn.commit()
                finally:
                    conn.close()
                events.append({'order_id': row['id'], 'event': 'expired',
                               'signal_id': row['signal_id']})
                continue

            prices = self._get_current_price(collector, row['pair'])
            if prices is None:
                continue

            filled = None
            if row['type'] == 'stop' and prices['ask'] >= row['stop_price']:
                filled = self.place_market_buy(collector, row['pair'], row['qty'],
                                               signal_id=row['signal_id'])
            elif row['type'] == 'limit' and prices['bid'] <= row['limit_price']:
                # Resting maker order fills AT the limit, maker fee, no slip.
                filled = self._fill_at_price(row['pair'], row['qty'],
                                             row['limit_price'], row['signal_id'])
            if filled:
                conn = get_connection()
                try:
                    conn.execute("UPDATE orders SET status = 'filled' WHERE id = ?",
                                 (row['id'],))
                    conn.commit()
                finally:
                    conn.close()
                events.append({'order_id': row['id'], 'event': 'filled',
                               'signal_id': row['signal_id'], 'fill': filled,
                               'pair': row['pair'], 'qty': row['qty']})
        return events

    def _fill_at_price(self, pair: str, qty: float, price: float,
                       signal_id: Optional[str]) -> Optional[dict]:
        """Book a maker fill at an exact price (resting limit order)."""
        fee = price * qty * self.maker_fee
        order_id = self._gen_order_id()
        order_pk = str(uuid.uuid4())
        ts = int(time.time() * 1000)
        conn = get_connection()
        try:
            insert_audit(conn, 'paper_adapter', 'order_placed', {
                'cl_ord_id': order_id, 'pair': pair, 'side': 'buy',
                'type': 'limit_fill', 'qty': qty, 'signal_id': signal_id})
            conn.execute(
                "INSERT INTO orders (id, cl_ord_id, ts, pair, side, type, qty, "
                "status, signal_id, mode) VALUES (?, ?, ?, ?, 'buy', 'limit', ?, "
                "'filled', ?, 'paper')",
                (order_pk, order_id, ts, pair, qty, signal_id))
            conn.execute(
                "INSERT INTO fills (id, order_id, ts, price, qty, fee) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), order_pk, ts, price, qty, fee))
            conn.commit()
            return {'order_id': order_id, 'fill_price': price, 'qty': qty,
                    'fee': fee, 'side': 'buy', 'pair': pair, 'ts': ts}
        except Exception as e:
            logger.error(f"Failed limit fill: {e}")
            conn.rollback()
            return None
        finally:
            conn.close()

    def check_exits(self, collector) -> list:
        """Simulate exchange-side stop-loss and take-profit for every open
        paper position against the LIVE feed. Must be called on every
        executor/monitor tick.

        This is the paper equivalent of SPEC 6.2's 'stops are placed as
        exchange-side orders the moment an entry fills'. Before this method
        existed, nothing in the paper path ever compared live price to
        stop_px/target_px: a position could drop 30% and sit open forever
        (audited blocker B3).

        Stop fills happen at the current bid (a market order after the
        trigger), NOT at the stop price - honest about gap-throughs.
        Returns a list of close-result dicts for positions that exited.
        """
        closed = []
        for pos in self.get_open_positions():
            prices = self._get_current_price(collector, pos['pair'])
            if prices is None:
                continue
            bid = prices['bid']
            if bid <= pos['stop_px']:
                result = self.close_position(collector, pos['id'], exit_reason='stop')
                if result:
                    closed.append(result)
            elif pos['target_px'] and bid >= pos['target_px']:
                result = self.close_position(collector, pos['id'], exit_reason='target')
                if result:
                    closed.append(result)
        return closed

    def get_equity(self, collector=None) -> float:
        """Current equity: starting cash + closed PnL + unrealized PnL.

        With a collector, open positions are marked to the live bid so a
        losing open position shows up in equity immediately (the old version
        ignored open positions entirely - a 30% open drawdown read as no
        change). Without a collector, falls back to closed-PnL-only and says
        so in the log.
        """
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl_net), 0) as total_pnl FROM positions "
                "WHERE closed_ts IS NOT NULL"
            ).fetchone()
            closed_pnl = row['total_pnl'] if row else 0.0
        finally:
            conn.close()

        unrealized = 0.0
        if collector is not None:
            for pos in self.get_open_positions():
                prices = self._get_current_price(collector, pos['pair'])
                if prices is not None:
                    unrealized += (prices['bid'] - pos['entry_px']) * pos['qty']
        elif self.get_open_positions():
            logger.debug("get_equity: no collector given; open-position PnL not marked")

        return self.starting_equity + closed_pnl + unrealized

    def write_equity_snapshot(self, collector=None) -> float:
        """Write an equity_snapshots row (SPEC 7.1: 15-min cadence, called by
        the executor/monitor loop). Before this existed nothing ever wrote
        the table, so the daily/weekly ops backstops read no data and were
        permanently inert (audited M5)."""
        equity = self.get_equity(collector)
        open_positions = self.get_open_positions()
        open_risk = sum((p['entry_px'] - p['stop_px']) * p['qty'] for p in open_positions)
        cash = equity - sum(p['entry_px'] * p['qty'] for p in open_positions)
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO equity_snapshots (ts, equity, cash, open_risk, mode) "
                "VALUES (?, ?, ?, ?, 'paper')",
                (int(time.time() * 1000), equity, cash, open_risk)
            )
            conn.commit()
        finally:
            conn.close()
        return equity
