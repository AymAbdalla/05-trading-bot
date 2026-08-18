"""Read-only access to the engine's SQLite database and research artifacts.

Two rules govern this module.

**It never writes.** Connections are opened with `mode=ro` on a URI and then
have `PRAGMA query_only=ON` set on top. Either one alone is enough; both are
here because the cost of the belt is zero and the cost of the engine finding a
second writer on its WAL is not.

**It never raises.** The dashboard has to be readable when the bot has never
run, when the DB file does not exist, when a table is missing, and when a
research JSON is half-written by a sweep that is still going. Every reader
returns an empty frame with the right columns and records why in
`last_error()`, so the UI can say "no trades yet" and mean it. A caller that
gets an empty frame back cannot tell "empty" from "broken" on its own - that
is what `db_status()` is for (convention 11: an unreadable table is not an
empty one, and the two must not render identically).

No streamlit import here on purpose. Caching is the app layer's job; this
layer stays a plain, testable function of the database file.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from . import config

# --------------------------------------------------------------------------
# Column contracts. An empty result still has to have the shape the UI expects,
# otherwise every table render needs its own "if empty" branch.
# --------------------------------------------------------------------------

EQUITY_COLUMNS = ['ts', 'time', 'equity', 'cash', 'open_risk', 'mode']

TRADE_COLUMNS = [
    'id', 'opened_ts', 'closed_ts', 'opened_at', 'closed_at', 'pair', 'asset_class',
    'strategy_id', 'side', 'entry_px', 'exit_px', 'qty', 'stop_px', 'target_px',
    'pnl_gross', 'pnl_net', 'fees', 'r_multiple', 'exit_reason', 'status', 'mode',
]

ORDER_COLUMNS = [
    'id', 'cl_ord_id', 'ts', 'time', 'pair', 'side', 'type', 'qty', 'limit_price',
    'stop_price', 'status', 'exchange_order_id', 'signal_id', 'mode',
    'filled_qty', 'avg_fill_px', 'fees',
]

STRATEGY_COLUMNS = [
    'strategy_id', 'name', 'version', 'status', 'added_ts', 'added_at',
    'status_changed_ts', 'status_changed_at', 'changed_by', 'params_json',
]

STRATEGY_PERF_COLUMNS = [
    'strategy_id', 'name', 'status', 'asset_class', 'total_trades', 'open_trades',
    'wins', 'losses', 'win_rate', 'pnl_net', 'avg_r', 'sharpe_trade', 'profit_factor',
]

RISK_EVENT_COLUMNS = ['id', 'ts', 'time', 'type', 'details_json']

AUDIT_COLUMNS = ['id', 'ts', 'time', 'actor', 'event_type', 'payload_json']

SIGNAL_COLUMNS = [
    'id', 'ts', 'time', 'pair', 'tf', 'strategy_id', 'pattern', 'direction',
    'confidence', 'acted', 'skip_reason', 'mode',
]

#: Audit events that describe something the engine or an agent DID, as opposed
#: to bookkeeping. Used to filter the Agent Activity feed.
AGENT_EVENT_TYPES = [
    'order_placed', 'order_filled', 'order_cancelled', 'position_opened',
    'position_closed', 'halt', 'resume', 'reconciliation', 'strategy_promoted',
    'strategy_demoted', 'mode_change',
]

_LAST_ERROR: Optional[str] = None


def last_error() -> Optional[str]:
    """The most recent read failure, or None. Reset on every successful read."""
    return _LAST_ERROR


def _fail(msg: str) -> None:
    global _LAST_ERROR
    _LAST_ERROR = msg


def _ok() -> None:
    global _LAST_ERROR
    _LAST_ERROR = None


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------

def db_path() -> str:
    return config.DB_PATH


def db_exists() -> bool:
    return os.path.exists(db_path())


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """A read-only connection. Raises if the file is missing - callers inside
    this module always go through `_read`, which handles that."""
    path = path or db_path()
    # `mode=ro` refuses to create the file, which is the behaviour we want:
    # a missing DB should read as "the bot has not run", not be conjured into
    # existence by the dashboard.
    uri = 'file:{}?mode=ro'.format(path.replace('?', '%3f').replace('#', '%23'))
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.execute('PRAGMA query_only = ON')
    conn.row_factory = sqlite3.Row
    return conn


def db_status() -> Tuple[str, str]:
    """('ok' | 'missing' | 'error', human-readable detail).

    Distinct from "the tables are empty". A dashboard that renders an
    unreadable database identically to an empty one is lying about what it
    knows.
    """
    path = db_path()
    if not os.path.exists(path):
        return 'missing', 'No database at {}. The engine has not created one yet.'.format(path)
    try:
        conn = connect(path)
        try:
            conn.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchall()
        finally:
            conn.close()
        return 'ok', path
    except Exception as exc:  # sqlite3.Error, OSError, anything
        return 'error', '{}: {}'.format(type(exc).__name__, exc)


def _read(sql: str, params: Tuple = (), columns: Optional[List[str]] = None) -> pd.DataFrame:
    """Run a query, or return an empty frame with `columns` and record why."""
    columns = columns or []
    path = db_path()
    if not os.path.exists(path):
        _fail('database not found at {}'.format(path))
        return pd.DataFrame(columns=columns)
    conn = None
    try:
        conn = connect(path)
        df = pd.read_sql_query(sql, conn, params=params)
        _ok()
        return df
    except Exception as exc:
        _fail('{}: {}'.format(type(exc).__name__, exc))
        return pd.DataFrame(columns=columns)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def table_exists(name: str) -> bool:
    df = _read(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (name,), ['name'],
    )
    return not df.empty


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _to_time(df: pd.DataFrame, src: str, dst: str) -> pd.DataFrame:
    """Unix-ms column -> pandas datetime column (UTC, tz-naive for display)."""
    if src not in df.columns:
        df[dst] = pd.NaT
        return df
    df[dst] = pd.to_datetime(df[src], unit='ms', errors='coerce')
    return df


def classify_asset(pair: Any, strategy_id: Any = None) -> str:
    """CRYPTO | POLYMARKET | UNKNOWN.

    Strategy prefix is checked before pair shape because the `pair` column
    carries a Polymarket market slug for binaries (see
    `strategies/polymarket/base.py`), and a slug can look like anything.
    """
    sid = '' if strategy_id is None else str(strategy_id)
    if sid.startswith(config.POLYMARKET_PREFIXES):
        return 'POLYMARKET'
    p = '' if pair is None else str(pair).strip()
    if not p:
        return 'UNKNOWN'
    if p.upper() == 'POLYMARKET':
        return 'POLYMARKET'
    if '/' in p:
        quote = p.split('/')[-1].strip().upper()
        if quote in config.CRYPTO_QUOTES:
            return 'CRYPTO'
    # A slug (`will-x-happen-by-2026`) or a bare token id. Not a crypto pair.
    if '-' in p or ' ' in p or len(p) > 20:
        return 'POLYMARKET'
    return 'UNKNOWN'


def _empty(columns: List[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


# --------------------------------------------------------------------------
# Equity
# --------------------------------------------------------------------------

def get_equity_curve(mode: Optional[str] = None, limit: int = config.MAX_EQUITY_POINTS) -> pd.DataFrame:
    """Equity snapshots oldest-first, ready to plot.

    `limit` takes the most RECENT n and then re-sorts ascending, so a long
    history truncates at the old end rather than showing a stale prefix.
    """
    where, params = '', []
    if mode:
        where = 'WHERE mode = ?'
        params.append(mode)
    sql = (
        'SELECT ts, equity, cash, open_risk, mode FROM equity_snapshots '
        '{} ORDER BY ts DESC LIMIT ?'.format(where)
    )
    params.append(int(limit))
    df = _read(sql, tuple(params), EQUITY_COLUMNS)
    if df.empty:
        return _empty(EQUITY_COLUMNS)
    df = df.sort_values('ts').reset_index(drop=True)
    return _to_time(df, 'ts', 'time')[EQUITY_COLUMNS]


def get_latest_equity(mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
    df = get_equity_curve(mode=mode, limit=1)
    if df.empty:
        return None
    return df.iloc[-1].to_dict()


# --------------------------------------------------------------------------
# Trades, orders, signals
# --------------------------------------------------------------------------

def get_trades(
    mode: Optional[str] = None,
    asset_class: Optional[str] = None,
    strategy_id: Optional[str] = None,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    limit: int = config.MAX_TRADE_ROWS,
    open_only: bool = False,
) -> pd.DataFrame:
    """The trade log: positions, enriched with realised fees from fills.

    `positions` is the spine because it is the only table with an entry and an
    exit on the same row. Fees come from `fills` (joined through `orders` on
    `signal_id`) because `positions.fees` is what the engine believed at close
    time and the fills are what the venue actually charged; where they
    disagree, the fills are the fact. `fees` below is the fill-derived number
    when fills exist and the position's own number otherwise.
    """
    clauses, params = [], []
    if mode:
        clauses.append('p.mode = ?')
        params.append(mode)
    if strategy_id:
        clauses.append('p.strategy_id = ?')
        params.append(strategy_id)
    if start_ms is not None:
        clauses.append('p.opened_ts >= ?')
        params.append(int(start_ms))
    if end_ms is not None:
        clauses.append('p.opened_ts <= ?')
        params.append(int(end_ms))
    if open_only:
        clauses.append('p.closed_ts IS NULL')
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''

    sql = """
        SELECT p.id, p.opened_ts, p.closed_ts, p.pair, p.strategy_id,
               p.entry_px, p.exit_px, p.qty, p.stop_px, p.target_px,
               p.pnl_gross, p.pnl_net, p.fees, p.r_multiple, p.exit_reason, p.mode,
               (SELECT SUM(f.fee) FROM fills f JOIN orders o ON f.order_id = o.id
                 WHERE o.signal_id = p.signal_id) AS fill_fees
        FROM positions p
        {}
        ORDER BY COALESCE(p.closed_ts, p.opened_ts) DESC
        LIMIT ?
    """.format(where)
    params.append(int(limit))
    df = _read(sql, tuple(params), TRADE_COLUMNS)
    if df.empty:
        return _empty(TRADE_COLUMNS)

    df = _to_time(df, 'opened_ts', 'opened_at')
    df = _to_time(df, 'closed_ts', 'closed_at')

    # Fill-derived fees win where they exist.
    df['fees'] = df['fill_fees'].where(df['fill_fees'].notna(), df['fees'])
    df = df.drop(columns=['fill_fees'])

    df['asset_class'] = [
        classify_asset(p, s) for p, s in zip(df['pair'], df['strategy_id'])
    ]
    # The schema has no side column on positions: entries are long-only
    # (signals.direction is 'long' | 'exit'), and a stop strictly below entry
    # is a harness invariant. Derive rather than invent a column.
    df['side'] = 'long'
    df['status'] = [_trade_status(row) for _, row in df.iterrows()]

    if asset_class and asset_class != 'ALL':
        df = df[df['asset_class'] == asset_class]

    return df.reset_index(drop=True).reindex(columns=TRADE_COLUMNS)


def _trade_status(row: pd.Series) -> str:
    """OPEN | WIN | LOSS | FLAT. Text, not color - see config's note on why."""
    if pd.isna(row.get('closed_ts')):
        return 'OPEN'
    pnl = row.get('pnl_net')
    if pnl is None or pd.isna(pnl):
        return 'FLAT'
    if pnl > 0:
        return 'WIN'
    if pnl < 0:
        return 'LOSS'
    return 'FLAT'


def get_open_positions(mode: Optional[str] = None) -> pd.DataFrame:
    return get_trades(mode=mode, open_only=True, limit=config.MAX_TRADE_ROWS)


def get_orders(mode: Optional[str] = None, limit: int = config.MAX_ORDER_ROWS) -> pd.DataFrame:
    """Order flow, including the ones that never became a position.

    Rejected and cancelled orders are invisible in `positions` by
    construction, and they are exactly what you want to see when the bot looks
    idle but is in fact failing at the venue.
    """
    where, params = '', []
    if mode:
        where = 'WHERE o.mode = ?'
        params.append(mode)
    sql = """
        SELECT o.id, o.cl_ord_id, o.ts, o.pair, o.side, o.type, o.qty,
               o.limit_price, o.stop_price, o.status, o.exchange_order_id,
               o.signal_id, o.mode,
               (SELECT SUM(f.qty) FROM fills f WHERE f.order_id = o.id) AS filled_qty,
               (SELECT SUM(f.price * f.qty) / NULLIF(SUM(f.qty), 0) FROM fills f
                 WHERE f.order_id = o.id) AS avg_fill_px,
               (SELECT SUM(f.fee) FROM fills f WHERE f.order_id = o.id) AS fees
        FROM orders o
        {}
        ORDER BY o.ts DESC
        LIMIT ?
    """.format(where)
    params.append(int(limit))
    df = _read(sql, tuple(params), ORDER_COLUMNS)
    if df.empty:
        return _empty(ORDER_COLUMNS)
    df = _to_time(df, 'ts', 'time')
    return df.reindex(columns=ORDER_COLUMNS)


def get_signals(limit: int = 200, acted_only: bool = False) -> pd.DataFrame:
    where = 'WHERE acted = 1' if acted_only else ''
    sql = (
        'SELECT id, ts, pair, tf, strategy_id, pattern, direction, confidence, '
        'acted, skip_reason, mode FROM signals {} ORDER BY ts DESC LIMIT ?'.format(where)
    )
    df = _read(sql, (int(limit),), SIGNAL_COLUMNS)
    if df.empty:
        return _empty(SIGNAL_COLUMNS)
    df = _to_time(df, 'ts', 'time')
    return df.reindex(columns=SIGNAL_COLUMNS)


def get_modes() -> List[str]:
    """Modes actually present in the data. Used to build the mode filter so it
    offers what exists rather than what the schema allows."""
    found = []
    for sql in (
        'SELECT DISTINCT mode FROM positions',
        'SELECT DISTINCT mode FROM equity_snapshots',
        'SELECT DISTINCT mode FROM orders',
    ):
        df = _read(sql, (), ['mode'])
        if not df.empty:
            found.extend(str(m) for m in df['mode'].dropna().tolist())
    return sorted(set(found))


# --------------------------------------------------------------------------
# Strategies
# --------------------------------------------------------------------------

def get_strategies() -> pd.DataFrame:
    sql = (
        'SELECT strategy_id, name, version, status, params_json, added_ts, '
        'status_changed_ts, changed_by FROM strategy_registry '
        'ORDER BY status_changed_ts DESC'
    )
    df = _read(sql, (), STRATEGY_COLUMNS)
    if df.empty:
        return _empty(STRATEGY_COLUMNS)
    df = _to_time(df, 'added_ts', 'added_at')
    df = _to_time(df, 'status_changed_ts', 'status_changed_at')
    return df.reindex(columns=STRATEGY_COLUMNS)


def get_strategy_performance(mode: Optional[str] = None) -> pd.DataFrame:
    """Per-strategy aggregates over positions, left-joined onto the registry.

    Outer-joined in both directions on purpose: a registered strategy that has
    never traded must still appear (it is a real state - candidate), and a
    strategy that traded but is not in the registry must also appear (that is
    a reconciliation bug worth seeing, not worth hiding).
    """
    trades = get_trades(mode=mode, limit=1_000_000)
    registry = get_strategies()

    if trades.empty and registry.empty:
        return _empty(STRATEGY_PERF_COLUMNS)

    rows: List[Dict[str, Any]] = []
    reg_by_id = {r['strategy_id']: r for _, r in registry.iterrows()} if not registry.empty else {}

    ids = set(reg_by_id)
    if not trades.empty:
        ids |= set(trades['strategy_id'].dropna().unique())

    for sid in sorted(ids):
        sub = trades[trades['strategy_id'] == sid] if not trades.empty else _empty(TRADE_COLUMNS)
        closed = sub[sub['closed_ts'].notna()] if not sub.empty else sub
        reg = reg_by_id.get(sid)
        stats = _trade_stats(closed)
        rows.append({
            'strategy_id': sid,
            'name': (reg['name'] if reg is not None else sid),
            'status': (reg['status'] if reg is not None else 'unregistered'),
            'asset_class': (
                sub['asset_class'].mode().iloc[0]
                if not sub.empty and not sub['asset_class'].mode().empty
                else classify_asset(None, sid)
            ),
            'total_trades': int(len(closed)),
            'open_trades': int(len(sub) - len(closed)) if not sub.empty else 0,
            'wins': stats['wins'],
            'losses': stats['losses'],
            'win_rate': stats['win_rate'],
            'pnl_net': stats['pnl_net'],
            'avg_r': stats['avg_r'],
            'sharpe_trade': stats['sharpe_trade'],
            'profit_factor': stats['profit_factor'],
        })

    return pd.DataFrame(rows).reindex(columns=STRATEGY_PERF_COLUMNS)


def get_strategy_lifecycle() -> pd.DataFrame:
    """Promotion/demotion events, newest first.

    Reads the audit log rather than the registry: the registry keeps only the
    CURRENT status and the single most recent change timestamp, so it cannot
    answer "when was this promoted" for anything that moved twice. The audit
    log is append-only and does.
    """
    df = get_audit_log(limit=1000, event_types=['strategy_promoted', 'strategy_demoted', 'mode_change'])
    if df.empty:
        return _empty(['time', 'ts', 'event_type', 'strategy_id', 'from_status', 'to_status', 'actor'])
    out = []
    for _, r in df.iterrows():
        payload = _safe_json(r.get('payload_json'))
        out.append({
            'time': r['time'],
            'ts': r['ts'],
            'event_type': r['event_type'],
            'strategy_id': payload.get('strategy_id') or payload.get('strategy') or '-',
            'from_status': payload.get('from') or payload.get('from_status') or '-',
            'to_status': payload.get('to') or payload.get('to_status') or '-',
            'actor': r['actor'],
        })
    return pd.DataFrame(out)


# --------------------------------------------------------------------------
# Risk, audit, status
# --------------------------------------------------------------------------

def get_risk_events(limit: int = config.MAX_RISK_ROWS) -> pd.DataFrame:
    sql = 'SELECT id, ts, type, details_json FROM risk_events ORDER BY ts DESC LIMIT ?'
    df = _read(sql, (int(limit),), RISK_EVENT_COLUMNS)
    if df.empty:
        return _empty(RISK_EVENT_COLUMNS)
    df = _to_time(df, 'ts', 'time')
    return df.reindex(columns=RISK_EVENT_COLUMNS)


def get_audit_log(limit: int = config.MAX_AUDIT_ROWS,
                  event_types: Optional[List[str]] = None,
                  actor: Optional[str] = None) -> pd.DataFrame:
    clauses, params = [], []
    if event_types:
        clauses.append('event_type IN ({})'.format(','.join('?' * len(event_types))))
        params.extend(event_types)
    if actor:
        clauses.append('actor = ?')
        params.append(actor)
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    sql = (
        'SELECT id, ts, actor, event_type, payload_json FROM audit_log '
        '{} ORDER BY ts DESC LIMIT ?'.format(where)
    )
    params.append(int(limit))
    df = _read(sql, tuple(params), AUDIT_COLUMNS)
    if df.empty:
        return _empty(AUDIT_COLUMNS)
    df = _to_time(df, 'ts', 'time')
    return df.reindex(columns=AUDIT_COLUMNS)


def read_halt() -> Optional[Dict[str, Any]]:
    """The kill-switch record, or None when clear.

    Delegates to `engine.halt`, which is the single definition of where the
    HALT file lives and what an unreadable one means. The fallback path exists
    only for the case where the dashboard is running somewhere the engine
    package cannot be imported; it is a degraded mode and says so.
    """
    try:
        import sys
        if config.PROJECT_ROOT not in sys.path:
            sys.path.insert(0, config.PROJECT_ROOT)
        from engine import halt as engine_halt  # type: ignore
        return engine_halt.read_halt()
    except Exception:
        path = config.HALT_FILE_FALLBACK
        if not os.path.exists(path):
            return None
        try:
            with open(path) as fh:
                data = json.load(fh)
        except Exception as exc:
            return {'_unreadable': '{}: {}'.format(type(exc).__name__, exc)}
        data = data if isinstance(data, dict) else {'_unreadable': 'not a JSON object'}
        data['_source'] = 'fallback path (engine.halt not importable)'
        return data


def get_bot_status(mode: Optional[str] = None) -> Dict[str, Any]:
    """One dict describing what the bot is doing right now.

    `state` is one of HALTED, ALIVE, STALE, IDLE.

      HALTED  the kill switch is engaged. Beats everything else - a halted
              engine that is still snapshotting equity is still halted.
      ALIVE   an equity snapshot inside the staleness window.
      STALE   snapshots exist but the newest is old. The process is probably
              dead; the dashboard does not claim to know why.
      IDLE    no snapshots at all. The bot has not run.
    """
    halt = read_halt()
    latest = get_latest_equity(mode=mode)
    now_ms = int(time.time() * 1000)

    age_min: Optional[float] = None
    if latest and latest.get('ts') is not None:
        age_min = (now_ms - float(latest['ts'])) / 60000.0

    if halt is not None:
        state = 'HALTED'
    elif latest is None:
        state = 'IDLE'
    elif age_min is not None and age_min <= config.STALE_EQUITY_MINUTES:
        state = 'ALIVE'
    else:
        state = 'STALE'

    active_mode = mode or (latest.get('mode') if latest else None)
    if not active_mode:
        modes = get_modes()
        active_mode = modes[0] if len(modes) == 1 else (','.join(modes) if modes else None)

    latest_risk = get_risk_events(limit=1)
    return {
        'state': state,
        'mode': active_mode,
        'halt': halt,
        'equity': (latest or {}).get('equity'),
        'cash': (latest or {}).get('cash'),
        'open_risk': (latest or {}).get('open_risk'),
        'last_snapshot_ts': (latest or {}).get('ts'),
        'last_snapshot_age_min': age_min,
        'last_risk_event': (latest_risk.iloc[0].to_dict() if not latest_risk.empty else None),
    }


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def _trade_stats(closed: pd.DataFrame) -> Dict[str, Any]:
    """Win rate, PnL, avg R, per-trade Sharpe, profit factor over CLOSED trades.

    Break-even trades (pnl_net exactly 0) count in the denominator of win rate
    but as neither a win nor a loss. Inflating win rate by dropping them is a
    small lie that compounds.
    """
    empty = {'wins': 0, 'losses': 0, 'win_rate': None, 'pnl_net': 0.0,
             'avg_r': None, 'sharpe_trade': None, 'profit_factor': None}
    if closed is None or closed.empty:
        return empty
    pnl = pd.to_numeric(closed['pnl_net'], errors='coerce').dropna()
    if pnl.empty:
        return empty

    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    n = int(len(pnl))

    gross_win = float(pnl[pnl > 0].sum())
    gross_loss = float(-pnl[pnl < 0].sum())
    if gross_loss > 0:
        profit_factor = gross_win / gross_loss
    elif gross_win > 0:
        # No losing trade. `inf` is the correct answer, not a bug to paper
        # over (convention 12). The UI renders it as a glyph and never lets it
        # reach a JSON serializer.
        profit_factor = math.inf
    else:
        profit_factor = None

    r = pd.to_numeric(closed['r_multiple'], errors='coerce').dropna() if 'r_multiple' in closed else pd.Series(dtype=float)

    # Per-trade Sharpe: mean/std of trade PnL, NOT annualised. A different
    # quantity from the equity-curve Sharpe on the Overview tab, which is why
    # it is named differently everywhere it is shown.
    sharpe = None
    if n >= 2:
        sd = float(pnl.std(ddof=1))
        if sd > 0:
            sharpe = float(pnl.mean()) / sd

    return {
        'wins': wins,
        'losses': losses,
        'win_rate': (wins / n) if n else None,
        'pnl_net': float(pnl.sum()),
        'avg_r': (float(r.mean()) if not r.empty else None),
        'sharpe_trade': sharpe,
        'profit_factor': profit_factor,
    }


def compute_drawdown(equity: pd.Series) -> pd.Series:
    """Fractional drawdown from the running peak. 0.0 at every new high."""
    eq = pd.to_numeric(equity, errors='coerce')
    peak = eq.cummax()
    return (eq / peak) - 1.0


def compute_equity_metrics(equity_df: pd.DataFrame) -> Dict[str, Any]:
    """Sharpe and max drawdown from the equity curve.

    Sharpe is annualised from the OBSERVED snapshot spacing (median dt), not
    from an assumed daily bar: the engine snapshots every 15 minutes and the
    crypto book runs 24/7, so hardcoding 252 would overstate it by roughly
    2.4x. Excess return is over zero - a risk-free rate on a paper book that
    has not run is false precision.

    Returns None for each metric it cannot honestly compute. With fewer than
    3 snapshots there is no dispersion to speak of and the answer is None,
    not 0.
    """
    out = {'sharpe': None, 'max_drawdown': None, 'periods_per_year': None,
           'total_return': None, 'n_points': 0}
    if equity_df is None or equity_df.empty:
        return out
    df = equity_df.dropna(subset=['equity']).sort_values('ts')
    out['n_points'] = int(len(df))
    if len(df) < 2:
        return out

    eq = pd.to_numeric(df['equity'], errors='coerce')
    dd = compute_drawdown(eq)
    out['max_drawdown'] = float(dd.min()) if not dd.dropna().empty else None

    first, last = float(eq.iloc[0]), float(eq.iloc[-1])
    if first > 0:
        out['total_return'] = (last / first) - 1.0

    if len(df) < 3:
        return out

    rets = eq.pct_change().dropna()
    rets = rets[~rets.isin([math.inf, -math.inf])]
    if len(rets) < 2:
        return out

    dt_ms = pd.to_numeric(df['ts'], errors='coerce').diff().dropna()
    dt_ms = dt_ms[dt_ms > 0]
    if dt_ms.empty:
        return out
    median_dt_s = float(dt_ms.median()) / 1000.0
    periods_per_year = (365.0 * 24.0 * 3600.0) / median_dt_s
    out['periods_per_year'] = periods_per_year

    sd = float(rets.std(ddof=1))
    if sd > 0:
        out['sharpe'] = (float(rets.mean()) / sd) * math.sqrt(periods_per_year)
    return out


def compute_overview_metrics(trades: pd.DataFrame, equity_df: pd.DataFrame) -> Dict[str, Any]:
    """Everything the Overview tab's metric row needs, in one dict."""
    closed = trades[trades['closed_ts'].notna()] if not trades.empty else _empty(TRADE_COLUMNS)
    stats = _trade_stats(closed)
    eq = compute_equity_metrics(equity_df)
    return {
        'total_trades': int(len(closed)),
        'open_positions': int(len(trades) - len(closed)) if not trades.empty else 0,
        'win_rate': stats['win_rate'],
        'profit_factor': stats['profit_factor'],
        'total_pnl': stats['pnl_net'],
        'avg_r': stats['avg_r'],
        'sharpe': eq['sharpe'],
        'max_drawdown': eq['max_drawdown'],
        'periods_per_year': eq['periods_per_year'],
        'equity_points': eq['n_points'],
    }


def pnl_since(trades: pd.DataFrame, since_ms: int) -> float:
    """Realised net PnL on trades CLOSED at or after `since_ms`.

    Closed, not opened: a trade that opened yesterday and closed today put its
    money in today's column.
    """
    if trades is None or trades.empty:
        return 0.0
    closed = trades[trades['closed_ts'].notna()]
    if closed.empty:
        return 0.0
    recent = closed[pd.to_numeric(closed['closed_ts'], errors='coerce') >= since_ms]
    return float(pd.to_numeric(recent['pnl_net'], errors='coerce').fillna(0).sum())


def start_of_utc_day_ms(now_ms: Optional[int] = None) -> int:
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    day_ms = 86_400_000
    return (now_ms // day_ms) * day_ms


# --------------------------------------------------------------------------
# Research artifacts (Graveyard tab)
# --------------------------------------------------------------------------

class _NonFinite(Exception):
    pass


def _json_constant(name: str):
    """`json.loads` accepts bare `Infinity` and `NaN`; most other parsers do
    not (convention 19). Rather than crash on a file the rest of the toolchain
    can read, map non-finites to None and let the loader report that it
    happened - so the tab can show a warning instead of silently printing a
    number that came from nowhere."""
    raise _NonFinite(name)


def load_json_artifact(path: str) -> Tuple[Optional[Any], Optional[str]]:
    """(parsed, note). `note` is non-None when the file is missing, unreadable,
    or contained non-finite tokens that a strict JSON parser would reject."""
    if not os.path.exists(path):
        return None, 'not found: {}'.format(path)
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError as exc:
        return None, '{}: {}'.format(type(exc).__name__, exc)

    try:
        return json.loads(text, parse_constant=_json_constant), None
    except _NonFinite as exc:
        pass
    except json.JSONDecodeError as exc:
        return None, 'invalid JSON: {}'.format(exc)

    # Non-finite tokens present. Re-parse permissively so the tab still works,
    # and flag it: this file is not portable to a strict JSON parser.
    try:
        data = json.loads(text)
    except Exception as exc:
        return None, 'invalid JSON: {}'.format(exc)
    return data, (
        'contains bare Infinity/NaN tokens - readable by Python, rejected by '
        'strict JSON parsers. Values shown, portability is not guaranteed.'
    )


def load_graveyard_summary() -> Tuple[Optional[Dict], Optional[str]]:
    return load_json_artifact(config.GRAVEYARD_SUMMARY_PATH)


def load_judge_pack() -> Tuple[Optional[Dict], Optional[str]]:
    return load_json_artifact(config.JUDGE_PACK_PATH)


def load_harness_validation() -> Tuple[Optional[Dict], Optional[str]]:
    return load_json_artifact(config.HARNESS_VALIDATION_PATH)


def graveyard_strategy_health(judge_pack: Optional[Dict]) -> pd.DataFrame:
    """Per-strategy firing health from the judge pack.

    `zero_trade` is the headline: a strategy with rows tested but no trades
    did not run and fail, it did not fire. Those two are not the same verdict
    and the table keeps them apart.
    """
    cols = ['strategy', 'asset_class', 'n_trades', 'n_rows_tested',
            'n_rows_not_tested', 'observed_best_pf', 'confidence', 'fires']
    if not judge_pack or not isinstance(judge_pack.get('strategies'), list):
        return _empty(cols)
    rows = []
    for s in judge_pack['strategies']:
        if not isinstance(s, dict):
            continue
        n_trades = s.get('n_trades') or 0
        rows.append({
            'strategy': s.get('strategy'),
            'asset_class': _join_classes(s.get('asset_class')),
            'n_trades': n_trades,
            'n_rows_tested': s.get('n_rows_tested') or 0,
            'n_rows_not_tested': s.get('n_rows_not_tested') or 0,
            'observed_best_pf': s.get('observed_best_pf'),
            'confidence': s.get('confidence'),
            'fires': bool(n_trades and n_trades > 0),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return _empty(cols)
    return df.sort_values(['fires', 'n_trades'], ascending=[True, True]).reindex(columns=cols)


def _join_classes(value: Any) -> Optional[str]:
    """Flatten the judge pack's `asset_class`, which is a LIST for 54 of 55
    strategies since the SPEC multi-asset expansion and a bare string for the
    one that was not expanded.

    Not cosmetic: a column holding a mix of lists and strings cannot be
    serialized to Arrow, which is how streamlit ships a dataframe to the
    browser. The table silently fails to render rather than raising, so the
    join happens here where the shape is known.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        return ', '.join(str(v) for v in value)
    return str(value)


def _safe_json(text: Any) -> Dict[str, Any]:
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {'_raw': str(text)}
    return data if isinstance(data, dict) else {'_value': data}


def summarize_payload(text: Any, max_len: int = 90) -> str:
    """A one-line human summary of an audit/risk JSON payload for a log table."""
    data = _safe_json(text)
    if not data:
        return ''
    if '_raw' in data:
        return str(data['_raw'])[:max_len]
    parts = []
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            v = '{}[{}]'.format(type(v).__name__, len(v))
        elif isinstance(v, float):
            v = '{:.4g}'.format(v)
        parts.append('{}={}'.format(k, v))
    line = ' '.join(parts)
    return line if len(line) <= max_len else line[:max_len - 1] + '…'
