"""Risk gate: pure functions that evaluate every order before it's placed.

The execution layer calls check_order() before placing ANY order.
If check_order returns False, the order is blocked and logged.

Checks:
1. Position sizing: fixed notional cap (does NOT scale with balance)
2. Fee-to-edge gate: reject if fees eat > 15% of edge
3. Max trades/day: block after daily limit
4. Max concurrent positions: block at `max_concurrent_positions` (D-362 R1
   defaults this to the 100_000 sentinel - no count cap - until real money
   funds the crypto path)
5. Max positions per pair: block if pair already has open position
6. Consecutive loss pause: block if 4 losses in a row (24h pause)
7. Ops backstops: daily/weekly equity drop limits (tail-event)

This module has NO side effects. It reads state and returns a verdict.
The execution layer is responsible for all writes and API calls.
"""
import time
import logging
import sqlite3
from typing import Optional, Tuple, List
from dataclasses import dataclass

from engine.db import get_connection, get_db_path

logger = logging.getLogger(__name__)


@dataclass
class RiskVerdict:
    """Result of a risk gate check."""
    approved: bool
    reason: str  # 'approved' if passed, otherwise the block reason
    qty: float = 0.0  # computed quantity if approved
    notional: float = 0.0  # computed notional if approved
    fee_cost: float = 0.0  # estimated round-trip fee cost
    edge: float = 0.0  # price distance to stop


class RiskGate:
    """Pure risk gate. No side effects. Execution layer calls before every order."""

    def __init__(self, config: dict):
        risk = config.get('risk', {})
        self.notional_cap = risk.get('notional_cap_usd', 100)
        self.fee_to_edge_max = risk.get('fee_to_edge_max', 0.15)
        self.max_trades_per_day = risk.get('max_trades_per_day', 1)
        self.consecutive_loss_pause = risk.get('consecutive_loss_pause', 4)
        # D-362 R1: 100_000 is a SENTINEL meaning "no count cap", not a limit
        # anybody expects to bind. Aym: "the crypto cap makes no sense because
        # I have no real money in the shadow realm rn." This is the crypto /
        # Alpaca path, so the cap comes back the day real money funds it -
        # restore a small integer here AND at config.yaml `risk:` together.
        # Capital is the cap until then (engine/risk/constraints.py).
        self.max_concurrent_positions = risk.get('max_concurrent_positions',
                                                 100_000)
        self.max_positions_per_pair = risk.get('max_positions_per_pair', 1)
        self.daily_ops_stop_mult = risk.get('daily_ops_stop_multiplier', 3)
        self.weekly_ops_stop_mult = risk.get('weekly_ops_stop_multiplier', 15)
        self.taker_fee = config.get('exchange', {}).get('fees', {}).get('taker', 0.001)
        self.maker_fee = config.get('exchange', {}).get('fees', {}).get('maker', 0.001)

    def compute_position_size(self, entry: float) -> float:
        """Compute quantity from fixed notional cap.

        qty = notional_cap / entry_price
        Does NOT scale with balance.
        """
        if entry <= 0:
            return 0.0
        return self.notional_cap / entry

    def check_fee_to_edge(self, entry: float, stop: float) -> Tuple[bool, float, float, float]:
        """Check if fees eat too much of the edge.

        Returns (passed, fee_cost, edge_value, ratio).
        """
        if entry <= 0 or stop <= 0:
            return False, 0, 0, 1.0

        # Round-trip fee cost: entry + exit, both at taker fee
        fee_cost = self.notional_cap * self.taker_fee * 2

        # Edge: SIGNED dollar distance from entry down to stop (the risk).
        # abs() here (the audited M4 bug) let an inverted stop (above entry
        # on a long) sail through with a fake "edge".
        edge = (entry - stop) * (self.notional_cap / entry)

        if edge <= 0:
            return False, fee_cost, 0, 1.0

        ratio = fee_cost / edge
        passed = ratio <= self.fee_to_edge_max
        return passed, fee_cost, edge, ratio

    def get_trades_today(self, conn: sqlite3.Connection) -> int:
        """Count ENTRY orders placed today (UTC midnight).

        Only buy-side fills count against the daily entry budget. Counting
        sells (the audited M2 bug) meant a stop-out at 3am consumed the day's
        single entry slot. Rejected/errored orders don't count either.
        """
        utc_now = int(time.time() * 1000)
        utc_midnight = utc_now - (utc_now % 86400000)
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM orders WHERE ts >= ? AND side = 'buy' "
            "AND status NOT IN ('cancelled', 'rejected', 'error')",
            (utc_midnight,)
        ).fetchone()
        return row['cnt'] if row else 0

    def get_open_position_count(self, conn: sqlite3.Connection) -> int:
        """Count currently open positions."""
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM positions WHERE closed_ts IS NULL"
        ).fetchone()
        return row['cnt'] if row else 0

    def get_open_positions_for_pair(self, conn: sqlite3.Connection, pair: str) -> int:
        """Count open positions for a specific pair."""
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM positions WHERE pair = ? AND closed_ts IS NULL",
            (pair,)
        ).fetchone()
        return row['cnt'] if row else 0

    def get_consecutive_losses(self, conn: sqlite3.Connection) -> Tuple[int, Optional[int]]:
        """Consecutive losing trades (most recent, any strategy).

        Returns (streak_length, closed_ts of the most recent loss in the
        streak). The timestamp is what makes the pause EXPIRE: without it
        (the audited B2 bug) four losses blocked entries forever - and since
        blocked entries can never produce the winning trade that breaks the
        streak, the bot deadlocked permanently.
        """
        rows = conn.execute(
            "SELECT pnl_net, closed_ts FROM positions WHERE closed_ts IS NOT NULL "
            "ORDER BY closed_ts DESC LIMIT 20"
        ).fetchall()
        count = 0
        last_loss_ts = None
        for row in rows:
            if row['pnl_net'] is not None and row['pnl_net'] < 0:
                if count == 0:
                    last_loss_ts = row['closed_ts']
                count += 1
            else:
                break  # streak broken
        return count, last_loss_ts

    def get_current_equity(self, conn: sqlite3.Connection) -> Optional[float]:
        """Get latest equity from snapshots."""
        row = conn.execute(
            "SELECT equity FROM equity_snapshots ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        return row['equity'] if row else None

    def _period_open_equity(self, conn: sqlite3.Connection,
                            boundary_ms: int) -> Optional[float]:
        """Equity at a period boundary, falling FORWARD when there is no carry-in.

        This used to look backward only (`ts <= boundary`). On any period whose
        first snapshot lands after the boundary - a fresh database, a restart
        after downtime, the first day of a deployment - that returns None, and
        `check_ops_backstops` then skips the stop entirely because it guards on
        `is not None`. The backstop was silently absent on exactly the days it
        was most likely to be needed, and nothing logged that it had not run.

        Convention 11: "no carry-in row" is not "no drawdown". When nothing
        exists at or before the boundary, the earliest snapshot AFTER it is the
        best available open. Using it errs toward TRIPPING the backstop rather
        than disabling it, which is the correct direction for a safety control.
        """
        row = conn.execute(
            "SELECT equity FROM equity_snapshots WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
            (boundary_ms,)
        ).fetchone()
        if row is not None:
            return row['equity']
        row = conn.execute(
            "SELECT equity FROM equity_snapshots WHERE ts > ? ORDER BY ts ASC LIMIT 1",
            (boundary_ms,)
        ).fetchone()
        return row['equity'] if row else None

    def get_day_open_equity(self, conn: sqlite3.Connection) -> Optional[float]:
        """Get equity at UTC midnight (start of trading day)."""
        utc_now = int(time.time() * 1000)
        utc_midnight = utc_now - (utc_now % 86400000)
        return self._period_open_equity(conn, utc_midnight)

    def get_week_open_equity(self, conn: sqlite3.Connection) -> Optional[float]:
        """Get equity at start of week (7 days ago)."""
        utc_now = int(time.time() * 1000)
        week_ago = utc_now - (7 * 86400000)
        return self._period_open_equity(conn, week_ago)

    def check_ops_backstops(self, conn: sqlite3.Connection) -> Tuple[bool, str]:
        """Check daily and weekly ops backstops.

        These are tail-event backstops for data bugs and marking errors,
        not primary trading-risk controls. Under fixed notional, actual
        trading losses can't trigger these.

        Returns (ok, reason).
        """
        current_equity = self.get_current_equity(conn)
        if current_equity is None:
            return True, 'no_equity_data'

        max_single_loss = self.notional_cap  # worst case: full notional lost

        # Daily ops stop: equity dropped > N x notional_cap in one day
        day_open = self.get_day_open_equity(conn)
        if day_open is not None:
            daily_drop = day_open - current_equity
            daily_threshold = self.daily_ops_stop_mult * max_single_loss
            if daily_drop > daily_threshold:
                return False, f'daily_ops_stop: drop=${daily_drop:.2f} > threshold=${daily_threshold:.2f}'

        # Weekly ops stop: equity dropped > N x notional_cap x 5 in a week
        week_open = self.get_week_open_equity(conn)
        if week_open is not None:
            weekly_drop = week_open - current_equity
            weekly_threshold = self.weekly_ops_stop_mult * max_single_loss
            if weekly_drop > weekly_threshold:
                return False, f'weekly_ops_stop: drop=${weekly_drop:.2f} > threshold=${weekly_threshold:.2f}'

        return True, 'ok'

    def check_order(self, pair: str, entry: float, stop: float,
                    mode: str = 'paper') -> RiskVerdict:
        """Full risk gate check before placing an order.

        This is the single entry point the execution layer calls.
        Returns RiskVerdict with approved/reason/qty/notional.
        """
        conn = get_connection()
        try:
            # 1. Ops backstops (daily/weekly equity limits)
            ops_ok, ops_reason = self.check_ops_backstops(conn)
            if not ops_ok:
                return RiskVerdict(False, ops_reason)

            # 2. Consecutive loss pause (SPEC 6.2: 24h pause, then it EXPIRES)
            consecutive, last_loss_ts = self.get_consecutive_losses(conn)
            if consecutive >= self.consecutive_loss_pause and last_loss_ts is not None:
                pause_ms = 24 * 3600 * 1000
                elapsed = int(time.time() * 1000) - last_loss_ts
                if elapsed < pause_ms:
                    hours_left = (pause_ms - elapsed) / 3600000
                    return RiskVerdict(
                        False,
                        f'consecutive_loss_pause: {consecutive} losses in a row '
                        f'(limit: {self.consecutive_loss_pause}), {hours_left:.1f}h of 24h pause remaining'
                    )

            # 3. Max trades/day
            trades_today = self.get_trades_today(conn)
            if trades_today >= self.max_trades_per_day:
                return RiskVerdict(
                    False,
                    f'max_trades_per_day: {trades_today} today (limit: {self.max_trades_per_day})'
                )

            # 4. Max concurrent positions
            open_count = self.get_open_position_count(conn)
            if open_count >= self.max_concurrent_positions:
                return RiskVerdict(
                    False,
                    f'max_concurrent: {open_count} open (limit: {self.max_concurrent_positions})'
                )

            # 5. Max positions per pair
            pair_count = self.get_open_positions_for_pair(conn, pair)
            if pair_count >= self.max_positions_per_pair:
                return RiskVerdict(
                    False,
                    f'max_per_pair: {pair_count} open for {pair} (limit: {self.max_positions_per_pair})'
                )

            # 6. Position sizing (fixed notional)
            qty = self.compute_position_size(entry)
            if qty <= 0:
                return RiskVerdict(False, 'invalid_entry: qty computed as 0')

            # 6b. Stop must be BELOW entry for a long. A strategy emitting an
            # inverted stop is buggy; block it here rather than opening an
            # unprotectable position.
            if stop >= entry:
                return RiskVerdict(
                    False, f'invalid_stop: stop {stop} >= entry {entry} for a long'
                )

            # 7. Fee-to-edge gate
            fee_passed, fee_cost, edge, ratio = self.check_fee_to_edge(entry, stop)
            if not fee_passed:
                return RiskVerdict(
                    False,
                    f'fee_to_edge: ratio={ratio:.2f} > {self.fee_to_edge_max} (fee=${fee_cost:.4f}, edge=${edge:.4f})'
                )

            # All checks passed
            return RiskVerdict(
                True, 'approved',
                qty=qty,
                notional=self.notional_cap,
                fee_cost=fee_cost,
                edge=edge
            )
        finally:
            conn.close()
