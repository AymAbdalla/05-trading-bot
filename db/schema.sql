-- Trading Bot v1 SQLite Schema
-- WAL mode, one writer (engine), Quant reads mode=ro

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Candles: raw OHLCV market data
CREATE TABLE IF NOT EXISTS candles (
    pair    TEXT NOT NULL,
    tf      TEXT NOT NULL,          -- timeframe: '15m' or '1h'
    ts      INTEGER NOT NULL,        -- unix ms timestamp
    open    REAL NOT NULL,
    high    REAL NOT NULL,
    low     REAL NOT NULL,
    close   REAL NOT NULL,
    volume  REAL NOT NULL,
    PRIMARY KEY (pair, tf, ts)
);

-- Signals: every signal emitted by the scanner (acted or skipped)
CREATE TABLE IF NOT EXISTS signals (
    id              TEXT PRIMARY KEY,    -- UUID
    ts              INTEGER NOT NULL,    -- unix ms
    pair            TEXT NOT NULL,
    tf              TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    pattern         TEXT NOT NULL,       -- e.g. 'bullish_engulfing'
    direction       TEXT NOT NULL,       -- 'long' | 'exit'
    confidence      REAL NOT NULL,      -- 0.0 to 1.0
    features_json   TEXT NOT NULL,       -- JSON snapshot: rsi, volume_ratio, trend_state, sr_distance, etc.
    acted           INTEGER NOT NULL DEFAULT 0,  -- 0 = skipped, 1 = acted on
    skip_reason     TEXT,                -- NULL if acted, reason if skipped
    mode            TEXT NOT NULL DEFAULT 'paper'  -- paper | live | shadow
);

-- Orders: every order attempt (before API call)
CREATE TABLE IF NOT EXISTS orders (
    id                  TEXT PRIMARY KEY,    -- UUID
    cl_ord_id           TEXT UNIQUE NOT NULL, -- client order reference (idempotency)
    ts                  INTEGER NOT NULL,
    pair                TEXT NOT NULL,
    side                TEXT NOT NULL,        -- 'buy' | 'sell'
    type                TEXT NOT NULL,        -- 'market' | 'limit' | 'stop'
    qty                 REAL NOT NULL,
    limit_price         REAL,                -- for limit orders
    stop_price          REAL,                -- for stop orders
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending | filled | cancelled | rejected | error
    exchange_order_id   TEXT,                -- exchange's order ID (filled in after creation)
    signal_id           TEXT,                -- signal that triggered this order
    mode                TEXT NOT NULL DEFAULT 'paper'
);

-- Fills: actual executions
CREATE TABLE IF NOT EXISTS fills (
    id          TEXT PRIMARY KEY,    -- UUID
    order_id    TEXT NOT NULL,
    ts          INTEGER NOT NULL,
    price       REAL NOT NULL,
    qty         REAL NOT NULL,
    fee         REAL NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

-- Positions: open and closed positions
CREATE TABLE IF NOT EXISTS positions (
    id              TEXT PRIMARY KEY,    -- UUID
    pair            TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    signal_id       TEXT,                -- signal that triggered entry
    opened_ts       INTEGER NOT NULL,
    closed_ts       INTEGER,             -- NULL if still open
    entry_px        REAL NOT NULL,
    exit_px         REAL,                -- NULL if still open
    qty             REAL NOT NULL,
    stop_px         REAL NOT NULL,
    target_px       REAL NOT NULL,
    pnl_gross       REAL,                -- NULL if still open
    pnl_net         REAL,                -- after fees, NULL if still open
    fees            REAL DEFAULT 0,
    r_multiple      REAL,                -- realized R, NULL if still open
    exit_reason     TEXT,                -- 'target' | 'stop' | 'signal_exit' | 'manual_halt' | 'daily_loss_halt'
    mode            TEXT NOT NULL DEFAULT 'paper'
);

-- Equity snapshots: every 15 min + 00:00 UTC
CREATE TABLE IF NOT EXISTS equity_snapshots (
    ts          INTEGER NOT NULL,
    equity      REAL NOT NULL,
    cash        REAL NOT NULL,
    open_risk   REAL NOT NULL,          -- unrealized risk in open positions
    mode        TEXT NOT NULL DEFAULT 'paper',
    PRIMARY KEY (ts, mode)
);

-- Strategy registry: tracks lifecycle of all strategies
CREATE TABLE IF NOT EXISTS strategy_registry (
    strategy_id         TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    version             INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'candidate',  -- candidate | shadow | live | retired
    params_json         TEXT NOT NULL,
    added_ts            INTEGER NOT NULL,
    status_changed_ts   INTEGER NOT NULL,
    changed_by           TEXT NOT NULL     -- 'quant' | 'aym' | 'engine'
);

-- Risk events: circuit breaker triggers, halts, restarts
CREATE TABLE IF NOT EXISTS risk_events (
    id          TEXT PRIMARY KEY,    -- UUID
    ts          INTEGER NOT NULL,
    type        TEXT NOT NULL,       -- 'daily_loss_halt' | 'weekly_loss_halt' | 'consecutive_losses' | 'api_error_storm' | 'stale_data' | 'manual_halt' | 'kill_switch'
    details_json TEXT NOT NULL
);

-- Audit log: append-only, no UPDATE or DELETE ever
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    actor       TEXT NOT NULL,        -- 'engine' | 'quant' | 'aym' | 'raven' | 'system'
    event_type  TEXT NOT NULL,        -- 'order_placed' | 'order_filled' | 'order_cancelled' | 'position_opened' | 'position_closed' | 'halt' | 'resume' | 'reconciliation' | 'strategy_promoted' | 'strategy_demoted' | 'mode_change'
    payload_json TEXT NOT NULL
);

-- Indexes for query performance
CREATE INDEX IF NOT EXISTS idx_candles_pair_tf_ts ON candles(pair, tf, ts);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);
CREATE INDEX IF NOT EXISTS idx_signals_pair ON signals(pair);
CREATE INDEX IF NOT EXISTS idx_signals_strategy ON signals(strategy_id);
CREATE INDEX IF NOT EXISTS idx_orders_ts ON orders(ts);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_positions_pair ON positions(pair);
CREATE INDEX IF NOT EXISTS idx_positions_open ON positions(closed_ts) WHERE closed_ts IS NULL;
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_risk_events_ts ON risk_events(ts);

-- ---------------------------------------------------------------------------
-- Research feeds (read-only inputs; nothing here is part of the order path)
--
-- These two tables are ALSO declared as SCHEMA_SQL inside their own modules,
-- which apply them with CREATE ... IF NOT EXISTS at startup. That is what let
-- them exist before this file knew about them. The module copies remain so a
-- feed can bootstrap its own storage; this file is the copy a fresh db is
-- built from. If you change one, change both - they are asserted to agree by
-- tests/test_liquidation_recorder.py and tests/test_hyperliquid_client.py.
-- ---------------------------------------------------------------------------

-- Liquidations: forced-order feed. BYBIT ONLY, 3 symbols, as of 2026-08-18.
-- The `exchange` column is not aspirational: Binance is geoblocked from this
-- machine (HTTP 451, and its socket connects while delivering nothing), and
-- Hyperliquid has no public venue-wide liquidation feed at all. Both are
-- refused by the recorder with the measurement attached. So a query over this
-- table is a query over Bybit, not over "the market" - see the docstring of
-- engine/feeds/liquidation_recorder.py before drawing a conclusion from it.
-- `side` is the side that was LIQUIDATED, not the side of the liquidating
-- order (a forced SELL closes a LONG). The recorder inverts it on the way in;
-- nothing downstream may translate it again.
-- `id` is deterministic so a websocket reconnect replaying events is an
-- INSERT OR IGNORE no-op rather than a double count.
CREATE TABLE IF NOT EXISTS liquidations (
    id TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    value_usd REAL NOT NULL
);

-- Hyperliquid whale positions: periodic snapshots of large open positions.
-- Append-only SNAPSHOTS, not a position ledger: there is no primary key and
-- the same (wallet, symbol) recurs at every poll. Query it with a ts filter.
CREATE TABLE IF NOT EXISTS hyperliquid_positions (
    ts          INTEGER NOT NULL,
    wallet      TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    size_usd    REAL NOT NULL,
    entry_price REAL NOT NULL,
    liq_price   REAL,
    leverage    REAL
);

-- ---------------------------------------------------------------------------
-- Agent coordination (not market data, not part of the order path)
--
-- Optimistic concurrency control for a working tree that several agent
-- sessions share. Written by engine/concurrency.py, which also declares this
-- DDL as its own SCHEMA_SQL - same two-copy arrangement as the feeds above,
-- and asserted to agree by tests/test_schema_matches_feed_modules.py.
--
-- Append-only audit trail. `action` is one of checkout / checkin / conflict /
-- write / release. It is a RECORD, not the correctness mechanism: the SHA-256
-- comparison is what prevents a lost edit, and a failure to insert here never
-- blocks a write. So a gap in this table means the log was degraded, never
-- that the write did not happen.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS file_coordination (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    action TEXT NOT NULL,
    old_hash TEXT,
    new_hash TEXT,
    conflict_diff TEXT
);

CREATE INDEX IF NOT EXISTS idx_file_coordination_ts
    ON file_coordination(ts);
CREATE INDEX IF NOT EXISTS idx_file_coordination_path_ts
    ON file_coordination(file_path, ts);

-- Indexes for the research feeds
CREATE INDEX IF NOT EXISTS idx_liquidations_ts ON liquidations (ts);
CREATE INDEX IF NOT EXISTS idx_liquidations_symbol_ts ON liquidations (symbol, ts);
CREATE INDEX IF NOT EXISTS idx_hyperliquid_positions_ts
    ON hyperliquid_positions(ts);
CREATE INDEX IF NOT EXISTS idx_hyperliquid_positions_symbol_ts
    ON hyperliquid_positions(symbol, ts);
