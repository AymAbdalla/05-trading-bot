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
