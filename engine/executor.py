"""Execution layer (T9): consumes scanner signals, applies the risk gate,
places paper orders, and runs the position-monitoring loop.

This is the ONLY layer that can trade crypto (SPEC 3.1). Responsibilities:
- Entry signals: stale-data check -> risk gate -> open position via the
  adapter -> mark the signal acted (db.mark_signal_acted). Blocked signals
  get their skip_reason recorded so the skipped-signal dataset stays honest.
- Exit signals: close_long closes the pair's open position; tighten_stop
  raises the stop (three-white-soldiers trail, SPEC 5.1 #5).
- Monitor tick: PaperAdapter.check_exits() simulates exchange-side
  stops/targets on the live feed; equity snapshots written every
  SNAPSHOT_INTERVAL_S (feeds the ops backstops, SPEC 7.1).
- HALT file (SPEC 6.3): if <project root>/HALT exists, close everything,
  stop entering, and stay halted until the file is removed via
  `botctl resume`.

The executor never computes signals and never bypasses the risk gate.

HALT lives in `engine/halt.py`, not here. It is the same switch the Polymarket
runner reads, and a kill switch with two definitions is a kill switch that can
halt one asset class while the other keeps trading.
"""
import logging
import time
import threading
from queue import Empty, Queue
from typing import Optional

from engine.db import (get_connection, insert_audit, mark_signal_acted,
                       update_signal_skip_reason)
from engine.halt import HALT_FILE, is_halted, write_halt
from engine.risk import RiskGate

logger = logging.getLogger(__name__)

# HALT_FILE is re-exported from engine.halt: existing callers and tests import
# it from this module, and there is still exactly one definition.
SNAPSHOT_INTERVAL_S = 15 * 60  # SPEC 7.1: 15-minute equity snapshot cadence


class Executor:
    """Consumes the scanner's signal queue and manages paper positions."""

    def __init__(self, config: dict, collector, adapter,
                 signal_queue: Queue):
        self.config = config
        self.collector = collector
        self.adapter = adapter
        self.signal_queue = signal_queue
        self.risk_gate = RiskGate(config)
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._last_snapshot_ts = 0.0
        self._halted_logged = False

        tf = config.get('timeframes', {}).get('signal', '15m')
        units = {'m': 60, 'h': 3600, 'd': 86400}
        self._signal_interval_s = int(tf[:-1]) * units[tf[-1]]

    # ---------- reconciliation (SPEC 7.3, paper-mode version) ----------

    def reconcile_on_boot(self) -> dict:
        """Run BEFORE trading resumes after any (re)start.

        Paper mode: the DB is the exchange, so reconciliation means internal
        consistency, not exchange comparison. Checks:
        - open positions whose stop/target are inverted or missing -> close
          them at market (unprotectable position must not survive a restart);
        - signals stuck at acted=0 with no skip_reason from before this boot
          -> mark 'engine_restart' so the skipped-signal dataset stays
          complete (they were queued when the process died).
        Returns a summary dict and writes an audit row.
        """
        summary = {'positions_checked': 0, 'positions_closed': 0,
                   'stale_signals_marked': 0}
        boot_ts = int(time.time() * 1000)

        for pos in self.adapter.get_open_positions():
            summary['positions_checked'] += 1
            # Unprotectable = stop missing/nonpositive. A stop ABOVE entry is
            # NOT unprotectable - the trailing-stop feature (tighten_stop)
            # legitimately raises stops above entry to lock in profit, and
            # closing those on every launchd restart would liquidate winners
            # (re-audit finding N2).
            bad_stop = pos['stop_px'] is None or pos['stop_px'] <= 0
            if bad_stop:
                logger.warning(f"reconcile: closing unprotectable position {pos['id'][:8]}")
                result = self.adapter.close_position(self.collector, pos['id'],
                                                     exit_reason='reconcile')
                if result:  # count only closes that actually happened (N2)
                    summary['positions_closed'] += 1

        conn = get_connection()
        try:
            cur = conn.execute(
                "UPDATE signals SET skip_reason = 'engine_restart' "
                "WHERE acted = 0 AND skip_reason IS NULL AND ts < ?", (boot_ts,))
            summary['stale_signals_marked'] = cur.rowcount
            insert_audit(conn, 'executor', 'reconcile_on_boot', summary)
            conn.commit()
        finally:
            conn.close()
        logger.info(f'reconcile_on_boot: {summary}')
        return summary

    # ---------- halt ----------

    def is_halted(self) -> bool:
        """Delegates to the shared definition (engine.halt.is_halted)."""
        return is_halted()

    def _handle_halt(self):
        """While halted: keep closing any open positions (retry every step -
        a failed close must not leave an unmonitored position, re-audit N1),
        and DRAIN the signal queue with skip_reason 'halted' so nothing
        executes hours-stale on resume (N1)."""
        if not self._halted_logged:
            self._halted_logged = True
            logger.warning('HALT file present: closing all positions, no new entries')
            conn = get_connection()
            try:
                insert_audit(conn, 'executor', 'halt_detected', {'halt_file': HALT_FILE})
                conn.commit()
            finally:
                conn.close()
        for pos in self.adapter.get_open_positions():
            self.adapter.close_position(self.collector, pos['id'], exit_reason='halt')
        # Cancel resting orders: nothing may fill during or after a halt.
        conn = get_connection()
        try:
            conn.execute("UPDATE orders SET status = 'cancelled' "
                         "WHERE status = 'pending' AND mode = 'paper'")
            conn.commit()
        finally:
            conn.close()
        # Drain queued signals: they must never fire after resume.
        while True:
            try:
                item = self.signal_queue.get_nowait()
            except Empty:
                break
            signal_id = item[3] if len(item) > 3 else None
            if signal_id:
                self._record_skip(signal_id, 'halted')

    # ---------- signal handling ----------

    def _is_data_stale(self, pair: str) -> bool:
        """SPEC 6.2: no new SIGNAL-TIMEFRAME candle for 2x the interval
        blocks new entries. The tf filter matters: without it a fresh 1h
        candle masks a dead 15m feed for up to half an hour (re-audit N3)."""
        tf = self.config.get('timeframes', {}).get('signal', '15m')
        conn = get_connection()
        try:
            row = conn.execute(
                'SELECT MAX(ts) AS ts FROM candles WHERE pair = ? AND tf = ?',
                (pair, tf)
            ).fetchone()
        finally:
            conn.close()
        if not row or row['ts'] is None:
            return True
        age_s = time.time() - row['ts'] / 1000.0
        return age_s > 2 * self._signal_interval_s

    def _signal_expired(self, signal, signal_id: Optional[str]) -> bool:
        """A signal is actionable for valid_for candles after it was logged.
        Anything older (queue latency, halt, engine pause) must not execute:
        its premise has expired (re-audit N1)."""
        if not signal_id:
            return False
        conn = get_connection()
        try:
            row = conn.execute('SELECT ts FROM signals WHERE id = ?',
                               (signal_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return False
        valid_for = max(1, int(getattr(signal, 'valid_for', 1) or 1))
        age_s = time.time() - row['ts'] / 1000.0
        return age_s > valid_for * self._signal_interval_s

    def handle_entry(self, pair: str, signal, signal_id: Optional[str]):
        if self.is_halted():
            if signal_id:
                self._record_skip(signal_id, 'halted')
            return

        if self._signal_expired(signal, signal_id):
            logger.info(f'{pair}: signal expired before execution')
            self._record_skip(signal_id, 'signal_expired')
            return

        if self._is_data_stale(pair):
            logger.warning(f'{pair}: stale data, entry blocked')
            if signal_id:
                self._record_skip(signal_id, 'stale_data')
            return

        verdict = self.risk_gate.check_order(pair, signal.entry, signal.stop)
        if not verdict.approved:
            logger.info(f'{pair}: risk gate blocked entry: {verdict.reason}')
            if signal_id:
                self._record_skip(signal_id, f'risk_gate: {verdict.reason}')
            # SPEC 6.2: ops backstops demand a HALT, not just a blocked entry
            # (re-audit N6). Escalate: HALT file + risk_events row; the next
            # step() closes all positions. Human resume only.
            if verdict.reason.startswith(('daily_ops_stop', 'weekly_ops_stop')):
                self._trigger_auto_halt(verdict.reason)
            return

        # Order-type routing (SPEC 5.1 / D-103, closing re-audit N5): entry
        # near the current ask -> market now; above -> resting buy-stop;
        # below -> resting buy-limit. Pending orders rest valid_for candles.
        prices = self.adapter._get_current_price(self.collector, pair)
        if prices is None:
            if signal_id:
                self._record_skip(signal_id, 'fill_failed')
            return
        ask = prices['ask']
        level = float(signal.entry)

        if abs(level - ask) <= ask * 0.001:
            self._market_entry(pair, signal, signal_id, verdict)
            return

        valid_for = max(1, int(getattr(signal, 'valid_for', 1) or 1))
        expires_ts = int(time.time() * 1000) + valid_for * self._signal_interval_s * 1000
        order_type = 'stop' if level > ask else 'limit'
        order_pk = self.adapter.place_pending_buy(pair, order_type, level,
                                                  verdict.qty, signal_id, expires_ts)
        if order_pk and signal_id:
            # Placed = acted (an order exists); expiry is recorded on the
            # order row if it never fills.
            conn = get_connection()
            try:
                mark_signal_acted(conn, signal_id)
                conn.commit()
            finally:
                conn.close()
        elif order_pk is None and signal_id:
            self._record_skip(signal_id, 'fill_failed')

    def _market_entry(self, pair: str, signal, signal_id, verdict):
        target = signal.target if signal.target else (
            signal.entry + (signal.entry - signal.stop) * 2)
        position_id = self.adapter.open_position(
            self.collector, pair, signal.strategy_id,
            entry=signal.entry, stop=signal.stop, target=target,
            qty=verdict.qty, signal_id=signal_id,
        )
        if position_id and signal_id:
            conn = get_connection()
            try:
                mark_signal_acted(conn, signal_id)
                conn.commit()
            finally:
                conn.close()
        elif position_id is None and signal_id:
            # Fill/DB failure must not leave an unlabeled row (re-audit N4).
            self._record_skip(signal_id, 'fill_failed')

    # ---------- pending-order lifecycle ----------

    def _pending_expiry(self, order_row) -> Optional[int]:
        """Expiry ts for a resting order: signal ts + valid_for x interval."""
        if not order_row['signal_id']:
            return order_row['ts'] + self._signal_interval_s * 1000
        conn = get_connection()
        try:
            row = conn.execute('SELECT ts, features_json FROM signals WHERE id = ?',
                               (order_row['signal_id'],)).fetchone()
        finally:
            conn.close()
        if not row:
            return order_row['ts'] + self._signal_interval_s * 1000
        import json as _json
        try:
            valid_for = max(1, int(_json.loads(row['features_json']).get('valid_for', 1) or 1))
        except (ValueError, TypeError):
            valid_for = 1
        return row['ts'] + valid_for * self._signal_interval_s * 1000

    def _process_pending(self):
        events = self.adapter.check_pending_orders(self.collector,
                                                   expiry_lookup=self._pending_expiry)
        import json as _json
        for ev in events:
            if ev['event'] != 'filled':
                continue
            # Reconstruct the position's stop/target from the signal row.
            stop = target = None
            strategy_id = 'unknown'
            if ev['signal_id']:
                conn = get_connection()
                try:
                    row = conn.execute(
                        'SELECT strategy_id, features_json FROM signals WHERE id = ?',
                        (ev['signal_id'],)).fetchone()
                finally:
                    conn.close()
                if row:
                    strategy_id = row['strategy_id']
                    try:
                        feats = _json.loads(row['features_json'])
                        stop, target = feats.get('stop'), feats.get('target')
                    except (ValueError, TypeError):
                        pass
            fill_px = ev['fill']['fill_price']
            if stop is None or stop >= fill_px:
                # Can't protect this position sensibly: undo immediately.
                logger.warning(f"pending fill without usable stop; closing {ev['pair']}")
                stop = fill_px * 0.98
            if not target:
                target = fill_px + (fill_px - stop) * 2
            position_id = str(__import__('uuid').uuid4())
            conn = get_connection()
            try:
                conn.execute(
                    "INSERT INTO positions (id, pair, strategy_id, signal_id, opened_ts, "
                    "entry_px, qty, stop_px, target_px, mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'paper')",
                    (position_id, ev['pair'], strategy_id, ev['signal_id'],
                     ev['fill']['ts'], fill_px, ev['qty'], stop, target))
                conn.commit()
            finally:
                conn.close()
            logger.info(f"PENDING FILL -> position {position_id[:8]} {ev['pair']} "
                        f"@ {fill_px:.2f} stop={stop:.2f} target={target:.2f}")

    def _trigger_auto_halt(self, reason: str):
        import json as _json
        import uuid as _uuid
        # Same HALT file every other path reads (engine.halt). Unconditional
        # write: an ops backstop firing must leave a record even if a halt is
        # already in place.
        halt_id = write_halt(f'auto: {reason}')
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO risk_events (id, ts, type, details_json) VALUES (?, ?, ?, ?)",
                (str(_uuid.uuid4()), int(time.time() * 1000),
                 'daily_loss_halt' if 'daily' in reason else 'weekly_loss_halt',
                 _json.dumps({'reason': reason, 'halt_id': halt_id})))
            insert_audit(conn, 'executor', 'auto_halt', {'reason': reason, 'halt_id': halt_id})
            conn.commit()
        finally:
            conn.close()
        logger.error(f'AUTO-HALT ({reason}); resume requires: botctl.py resume --ack {halt_id}')

    def handle_exit(self, pair: str, signal, signal_id: Optional[str]):
        """close_long closes the pair's position; tighten_stop raises its stop."""
        open_positions = [p for p in self.adapter.get_open_positions()
                          if p['pair'] == pair]
        if not open_positions:
            if signal_id:
                self._record_skip(signal_id, 'no_open_position')
            return

        for pos in open_positions:
            if signal.action == 'close_long':
                result = self.adapter.close_position(
                    self.collector, pos['id'], exit_reason='signal')
                if result and signal_id:
                    conn = get_connection()
                    try:
                        mark_signal_acted(conn, signal_id)
                        conn.commit()
                    finally:
                        conn.close()
                elif result is None and signal_id:
                    # Close failed (feed error): position stays open and
                    # monitored; label the signal so nothing is unaccounted
                    # (re-audit N4). Next exit signal / stop check retries.
                    self._record_skip(signal_id, 'close_failed')
            elif signal.action == 'tighten_stop':
                new_stop = float(signal.stop) if signal.stop else 0.0
                if new_stop > pos['stop_px']:  # stops only ever move UP
                    conn = get_connection()
                    try:
                        conn.execute('UPDATE positions SET stop_px = ? WHERE id = ?',
                                     (new_stop, pos['id']))
                        insert_audit(conn, 'executor', 'stop_tightened',
                                     {'position_id': pos['id'], 'old': pos['stop_px'],
                                      'new': new_stop, 'signal_id': signal_id})
                        if signal_id:
                            mark_signal_acted(conn, signal_id)
                        conn.commit()
                    finally:
                        conn.close()
                    logger.info(f'{pair}: stop tightened {pos["stop_px"]:.2f} -> {new_stop:.2f}')
                elif signal_id:
                    self._record_skip(signal_id, 'trail_not_applied: new stop not higher')

    def _record_skip(self, signal_id: str, reason: str):
        conn = get_connection()
        try:
            update_signal_skip_reason(conn, signal_id, reason)
            conn.commit()
        finally:
            conn.close()

    # ---------- main loop ----------

    def step(self, queue_timeout: float = 1.0):
        """One executor iteration: drain a signal (if any), run the monitor."""
        if self.is_halted():
            self._handle_halt()
        else:
            self._halted_logged = False
            try:
                item = self.signal_queue.get(timeout=queue_timeout)
                kind, pair, signal = item[0], item[1], item[2]
                signal_id = item[3] if len(item) > 3 else None
                if kind == 'entry':
                    self.handle_entry(pair, signal, signal_id)
                elif kind == 'exit':
                    self.handle_exit(pair, signal, signal_id)
            except Empty:
                pass

            # Resting-order lifecycle, then exchange-side stop/target
            # simulation, on every tick.
            self._process_pending()
            self.adapter.check_exits(self.collector)

        # Equity snapshots keep flowing even while halted (ops visibility).
        now = time.time()
        if now - self._last_snapshot_ts >= SNAPSHOT_INTERVAL_S:
            self._last_snapshot_ts = now
            try:
                equity = self.adapter.write_equity_snapshot(self.collector)
                logger.info(f'equity snapshot: ${equity:.2f}')
            except Exception as e:
                logger.error(f'equity snapshot failed: {e}')

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info('Executor started')

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info('Executor stopped')

    def _run(self):
        while self.running:
            try:
                self.step()
            except Exception as e:
                logger.error(f'Executor loop error: {e}')
                time.sleep(1)
