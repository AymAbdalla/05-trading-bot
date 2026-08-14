"""Database connection and helper functions for the trading engine.

Single writer (engine), read-only for Quant.
"""
import sqlite3
import os
from typing import Optional


def get_db_path() -> str:
    """Get the trading database path from env or default."""
    return os.environ.get("TRADING_DB_PATH", "db/trading.db")


def get_connection(read_only: bool = False) -> sqlite3.Connection:
    """Get a SQLite connection.

    Args:
        read_only: If True, opens in read-only mode (for Quant).
    """
    db_path = get_db_path()
    if read_only:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_schema():
    """Initialize the database schema from schema.sql."""
    db_path = get_db_path()
    schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "db", "schema.sql")
    conn = sqlite3.connect(db_path)
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    conn.close()


def insert_candle(conn: sqlite3.Connection, pair: str, tf: str, ts: int,
                  o: float, h: float, l: float, c: float, v: float):
    """Insert or replace a candle."""
    conn.execute(
        "INSERT OR REPLACE INTO candles (pair, tf, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pair, tf, ts, o, h, l, c, v)
    )


def get_candles(conn: sqlite3.Connection, pair: str, tf: str, limit: int = 200) -> list:
    """Get the most recent candles for a pair/timeframe."""
    rows = conn.execute(
        "SELECT * FROM candles WHERE pair = ? AND tf = ? ORDER BY ts DESC LIMIT ?",
        (pair, tf, limit)
    ).fetchall()
    # Return in chronological order
    return list(reversed(rows))


def insert_signal(conn: sqlite3.Connection, signal_data: dict) -> str:
    """Insert a signal record."""
    import uuid
    signal_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO signals (id, ts, pair, tf, strategy_id, pattern, direction, "
        "confidence, features_json, acted, skip_reason, mode) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (signal_id, signal_data['ts'], signal_data['pair'], signal_data['tf'],
         signal_data['strategy_id'], signal_data['pattern'], signal_data['direction'],
         signal_data['confidence'], signal_data['features_json'],
         signal_data.get('acted', 0), signal_data.get('skip_reason'),
         signal_data.get('mode', 'paper'))
    )
    return signal_id


def mark_signal_acted(conn: sqlite3.Connection, signal_id: str):
    """Flip a signal to acted=1 AFTER the risk gate approved it and an order
    was actually placed. The scanner always logs acted=0; only the execution
    layer may call this. That split keeps the skipped-signal dataset honest."""
    conn.execute("UPDATE signals SET acted = 1, skip_reason = NULL WHERE id = ?", (signal_id,))


def update_signal_skip_reason(conn: sqlite3.Connection, signal_id: str, reason: str):
    """Record why the execution layer declined a scanner-approved signal
    (risk gate block, stale data, etc.) so skipped-signal mining sees it."""
    conn.execute("UPDATE signals SET acted = 0, skip_reason = ? WHERE id = ?", (reason, signal_id))


def insert_audit(conn: sqlite3.Connection, actor: str, event_type: str, payload: dict):
    """Insert an audit log entry."""
    import json
    import time
    conn.execute(
        "INSERT INTO audit_log (ts, actor, event_type, payload_json) VALUES (?, ?, ?, ?)",
        (int(time.time() * 1000), actor, event_type, json.dumps(payload))
    )
