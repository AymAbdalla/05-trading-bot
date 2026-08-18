"""Tests for the dashboard's read-only data layer (`dashboard/db_reader.py`).

Three properties are under test, in rough order of how badly they would hurt
if they broke.

**The dashboard cannot write.** The engine is the single writer on a WAL
database. A dashboard that can open a write handle is a second writer waiting
for its moment, so `test_connection_is_read_only` asserts the handle actually
refuses an INSERT rather than trusting the URI to have been spelled right.

**Nothing raises on an empty or missing database.** The bot has not run in
shadow mode yet, so the empty path is the one that will actually be exercised.
Every reader is asserted to come back with an empty frame carrying the right
columns - the UI renders columns it can name, and a KeyError in a chart is a
blank tab with a stack trace in the terminal.

**Empty and unreadable do not render the same.** `db_status()` separates "the
tables are empty" from "I could not read the database" (convention 11: an
unreadable table is not an empty one). A dashboard that collapses those two
into one grey "no data" panel tells you the bot is quiet when it is actually
unobservable.

The metric tests exist because every number on the Overview tab is a claim.
Win rate that silently drops break-even trades, a Sharpe annualised on an
assumed daily bar, a profit factor that rounds infinity down to a large
number - each is a small lie that a reader has no way to catch.
"""
import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard import config, db_reader  # noqa: E402

SCHEMA = Path(__file__).parent.parent / 'db' / 'schema.sql'

MINUTE_MS = 60_000
FIFTEEN_MIN_MS = 15 * MINUTE_MS


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _make_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA.read_text())
    conn.commit()
    return conn


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    """A database with the real schema applied and not one row in it."""
    path = tmp_path / 'trading.db'
    _make_db(path).close()
    monkeypatch.setattr(config, 'DB_PATH', str(path))
    return str(path)


@pytest.fixture
def missing_db(tmp_path, monkeypatch):
    path = tmp_path / 'nope' / 'trading.db'
    monkeypatch.setattr(config, 'DB_PATH', str(path))
    return str(path)


@pytest.fixture
def populated_db(tmp_path, monkeypatch):
    """Two closed trades (one win, one loss), one open, and an equity curve.

    Numbers are chosen so every metric has a hand-checkable answer:
      wins 1, losses 1        -> win rate 0.5
      +30 gross win, -10 loss -> profit factor 3.0, net +20
      equity 100 -> 120 -> 90 -> 110 -> peak 120, trough 90 -> max dd -0.25
    """
    path = tmp_path / 'trading.db'
    conn = _make_db(path)
    now = int(time.time() * 1000)
    # The newest snapshot lands on `now`, so the fixture reads as ALIVE. Tests
    # that want STALE build their own database rather than aging this one.
    base = now - 3 * FIFTEEN_MIN_MS

    conn.executemany(
        'INSERT INTO equity_snapshots (ts, equity, cash, open_risk, mode) VALUES (?,?,?,?,?)',
        [
            (base + 0 * FIFTEEN_MIN_MS, 100.0, 100.0, 0.0, 'paper'),
            (base + 1 * FIFTEEN_MIN_MS, 120.0, 110.0, 5.0, 'paper'),
            (base + 2 * FIFTEEN_MIN_MS, 90.0, 90.0, 3.0, 'paper'),
            (base + 3 * FIFTEEN_MIN_MS, 110.0, 100.0, 2.0, 'paper'),
        ],
    )

    conn.executemany(
        'INSERT INTO signals (id, ts, pair, tf, strategy_id, pattern, direction, '
        'confidence, features_json, acted, skip_reason, mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        [
            ('sig-win', base, 'BTC/USDT', '15m', 'hammer', 'hammer', 'long', 0.8, '{}', 1, None, 'paper'),
            ('sig-loss', base, 'ETH/USDT', '15m', 'hammer', 'hammer', 'long', 0.6, '{}', 1, None, 'paper'),
            ('sig-skip', base, 'SOL/USDT', '15m', 'hammer', 'hammer', 'long', 0.4, '{}', 0, 'below_confidence', 'paper'),
        ],
    )

    conn.executemany(
        'INSERT INTO orders (id, cl_ord_id, ts, pair, side, type, qty, limit_price, '
        'stop_price, status, exchange_order_id, signal_id, mode) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        [
            ('ord-win', 'cl-win', base, 'BTC/USDT', 'buy', 'market', 1.0, None, None,
             'filled', 'x1', 'sig-win', 'paper'),
            ('ord-loss', 'cl-loss', base, 'ETH/USDT', 'buy', 'market', 2.0, None, None,
             'filled', 'x2', 'sig-loss', 'paper'),
            ('ord-rej', 'cl-rej', base, 'SOL/USDT', 'buy', 'market', 3.0, None, None,
             'rejected', None, None, 'paper'),
        ],
    )

    conn.executemany(
        'INSERT INTO fills (id, order_id, ts, price, qty, fee) VALUES (?,?,?,?,?,?)',
        [
            ('f1', 'ord-win', base, 100.0, 1.0, 0.10),
            ('f2', 'ord-loss', base, 50.0, 2.0, 0.20),
        ],
    )

    conn.executemany(
        'INSERT INTO positions (id, pair, strategy_id, signal_id, opened_ts, closed_ts, '
        'entry_px, exit_px, qty, stop_px, target_px, pnl_gross, pnl_net, fees, '
        'r_multiple, exit_reason, mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        [
            ('pos-win', 'BTC/USDT', 'hammer', 'sig-win', base, base + FIFTEEN_MIN_MS,
             100.0, 130.0, 1.0, 95.0, 130.0, 30.5, 30.0, 0.5, 2.0, 'target', 'paper'),
            ('pos-loss', 'ETH/USDT', 'hammer', 'sig-loss', base, base + 2 * FIFTEEN_MIN_MS,
             50.0, 45.0, 2.0, 45.0, 60.0, -9.5, -10.0, 0.5, -1.0, 'stop', 'paper'),
            ('pos-open', 'will-btc-hit-100k-by-2026', 'PM_box_builder', None,
             base + 3 * FIFTEEN_MIN_MS, None,
             0.42, None, 10.0, 0.30, 0.80, None, None, 0.0, None, None, 'paper'),
        ],
    )

    conn.executemany(
        'INSERT INTO strategy_registry (strategy_id, name, version, status, params_json, '
        'added_ts, status_changed_ts, changed_by) VALUES (?,?,?,?,?,?,?,?)',
        [
            ('hammer', 'Hammer', 1, 'shadow', '{}', base, base, 'quant'),
            ('idle_strategy', 'Idle', 1, 'candidate', '{}', base, base, 'aym'),
        ],
    )

    conn.executemany(
        'INSERT INTO risk_events (id, ts, type, details_json) VALUES (?,?,?,?)',
        [('re-1', base, 'daily_loss_halt', json.dumps({'loss_pct': 0.031}))],
    )

    conn.executemany(
        'INSERT INTO audit_log (ts, actor, event_type, payload_json) VALUES (?,?,?,?)',
        [
            (base, 'engine', 'position_opened', json.dumps({'pair': 'BTC/USDT'})),
            (base + 1, 'quant', 'strategy_promoted',
             json.dumps({'strategy_id': 'hammer', 'from': 'candidate', 'to': 'shadow'})),
        ],
    )

    conn.commit()
    conn.close()
    monkeypatch.setattr(config, 'DB_PATH', str(path))
    return str(path)


# --------------------------------------------------------------------------
# Read-only
# --------------------------------------------------------------------------

def test_connection_is_read_only(populated_db):
    """The handle must REFUSE a write, not merely be labelled read-only.

    The engine is the only writer on this WAL. This assertion is the one that
    would catch a future edit that drops `mode=ro` from the URI while leaving
    the reassuring comment in place.
    """
    conn = db_reader.connect()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO risk_events (id, ts, type, details_json) "
                         "VALUES ('x', 1, 'manual_halt', '{}')")
    finally:
        conn.close()


def test_read_only_connection_refuses_schema_changes(populated_db):
    conn = db_reader.connect()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute('DROP TABLE positions')
    finally:
        conn.close()


def test_missing_db_is_not_created_by_reading(missing_db):
    """`mode=ro` refuses to conjure the file into existence. A dashboard that
    creates an empty database the engine then finds is worse than one that
    shows nothing."""
    db_reader.get_trades()
    db_reader.get_equity_curve()
    db_reader.get_bot_status()
    assert not os.path.exists(missing_db)


# --------------------------------------------------------------------------
# Empty and missing states
# --------------------------------------------------------------------------

READERS = [
    ('get_equity_curve', db_reader.EQUITY_COLUMNS),
    ('get_trades', db_reader.TRADE_COLUMNS),
    ('get_open_positions', db_reader.TRADE_COLUMNS),
    ('get_orders', db_reader.ORDER_COLUMNS),
    ('get_signals', db_reader.SIGNAL_COLUMNS),
    ('get_strategies', db_reader.STRATEGY_COLUMNS),
    ('get_risk_events', db_reader.RISK_EVENT_COLUMNS),
    ('get_audit_log', db_reader.AUDIT_COLUMNS),
]


@pytest.mark.parametrize('name,columns', READERS)
def test_empty_db_returns_empty_frame_with_columns(empty_db, name, columns):
    df = getattr(db_reader, name)()
    assert df.empty
    assert list(df.columns) == list(columns), (
        '{} dropped columns when empty; the UI names these columns'.format(name))


@pytest.mark.parametrize('name,columns', READERS)
def test_missing_db_returns_empty_frame_with_columns(missing_db, name, columns):
    df = getattr(db_reader, name)()
    assert df.empty
    assert list(df.columns) == list(columns)


def test_empty_db_strategy_performance(empty_db):
    df = db_reader.get_strategy_performance()
    assert df.empty
    assert list(df.columns) == db_reader.STRATEGY_PERF_COLUMNS


def test_db_status_separates_empty_from_missing(empty_db, tmp_path, monkeypatch):
    state, _ = db_reader.db_status()
    assert state == 'ok', 'an empty database is readable, not broken'

    monkeypatch.setattr(config, 'DB_PATH', str(tmp_path / 'gone.db'))
    state, detail = db_reader.db_status()
    assert state == 'missing'
    assert 'gone.db' in detail


def test_db_status_reports_a_corrupt_file_as_error(tmp_path, monkeypatch):
    """Convention 11 at the UI boundary: a file that is not a database must
    not read as 'no trades yet'."""
    path = tmp_path / 'trading.db'
    path.write_bytes(b'this is not a sqlite file, not even close' * 40)
    monkeypatch.setattr(config, 'DB_PATH', str(path))

    state, detail = db_reader.db_status()
    assert state == 'error'
    assert detail

    # And the readers still come back empty rather than exploding.
    assert db_reader.get_trades().empty
    assert db_reader.last_error() is not None


def test_empty_db_bot_status_is_idle(empty_db):
    status = db_reader.get_bot_status()
    assert status['state'] == 'IDLE'
    assert status['equity'] is None


# --------------------------------------------------------------------------
# Reading real rows
# --------------------------------------------------------------------------

def test_get_trades_shape_and_derived_columns(populated_db):
    df = db_reader.get_trades()
    assert len(df) == 3
    assert list(df.columns) == db_reader.TRADE_COLUMNS

    by_id = df.set_index('id')
    assert by_id.loc['pos-win', 'status'] == 'WIN'
    assert by_id.loc['pos-loss', 'status'] == 'LOSS'
    assert by_id.loc['pos-open', 'status'] == 'OPEN'

    assert by_id.loc['pos-win', 'asset_class'] == 'CRYPTO'
    assert by_id.loc['pos-open', 'asset_class'] == 'POLYMARKET'


def test_fill_fees_override_position_fees(populated_db):
    """Where the two disagree the fills are the fact: `positions.fees` is what
    the engine believed at close, the fills are what the venue charged."""
    df = db_reader.get_trades().set_index('id')
    assert df.loc['pos-win', 'fees'] == pytest.approx(0.10)   # from fills, not the 0.5 on the position
    assert df.loc['pos-loss', 'fees'] == pytest.approx(0.20)
    # No fills for the open Polymarket position, so its own number stands.
    assert df.loc['pos-open', 'fees'] == pytest.approx(0.0)


def test_trade_filters(populated_db):
    assert len(db_reader.get_trades(asset_class='CRYPTO')) == 2
    assert len(db_reader.get_trades(asset_class='POLYMARKET')) == 1
    assert len(db_reader.get_trades(strategy_id='hammer')) == 2
    assert len(db_reader.get_trades(mode='live')) == 0
    assert len(db_reader.get_open_positions()) == 1


def test_get_orders_includes_orders_that_never_filled(populated_db):
    """A rejected order has no position, so it is invisible in the trade log.
    It is also the first thing you want to see when the bot looks idle."""
    df = db_reader.get_orders()
    assert set(df['status']) == {'filled', 'rejected'}
    rejected = df[df['status'] == 'rejected'].iloc[0]
    assert pd.isna(rejected['filled_qty'])

    filled = df[df['id'] == 'ord-loss'].iloc[0]
    assert filled['filled_qty'] == pytest.approx(2.0)
    assert filled['avg_fill_px'] == pytest.approx(50.0)


def test_bot_status_alive_when_snapshot_is_recent(populated_db, monkeypatch):
    monkeypatch.setattr(db_reader, 'read_halt', lambda: None)
    status = db_reader.get_bot_status()
    assert status['state'] == 'ALIVE'
    assert status['equity'] == pytest.approx(110.0)
    assert status['mode'] == 'paper'


def test_bot_status_stale_when_snapshot_is_old(tmp_path, monkeypatch):
    path = tmp_path / 'trading.db'
    conn = _make_db(path)
    old = int(time.time() * 1000) - 5 * 60 * MINUTE_MS
    conn.execute('INSERT INTO equity_snapshots (ts, equity, cash, open_risk, mode) '
                 'VALUES (?,?,?,?,?)', (old, 100.0, 100.0, 0.0, 'paper'))
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, 'DB_PATH', str(path))
    monkeypatch.setattr(db_reader, 'read_halt', lambda: None)

    status = db_reader.get_bot_status()
    assert status['state'] == 'STALE'
    assert status['last_snapshot_age_min'] > config.STALE_EQUITY_MINUTES


def test_halt_beats_a_live_heartbeat(populated_db, monkeypatch):
    """A halted engine that is still snapshotting equity is still halted. The
    kill switch is not outvoted by a heartbeat."""
    monkeypatch.setattr(db_reader, 'read_halt',
                        lambda: {'halt_id': 'abc123', 'ts': 1, 'reason': 'drill'})
    status = db_reader.get_bot_status()
    assert status['state'] == 'HALTED'
    assert status['halt']['halt_id'] == 'abc123'


def test_strategy_performance_keeps_registered_and_unregistered(populated_db):
    """A registered strategy that never traded is a real state (candidate). A
    strategy that traded but is not in the registry is a reconciliation gap.
    Both must survive the join."""
    perf = db_reader.get_strategy_performance().set_index('strategy_id')
    assert set(perf.index) == {'hammer', 'idle_strategy', 'PM_box_builder'}

    assert perf.loc['idle_strategy', 'total_trades'] == 0
    assert perf.loc['idle_strategy', 'status'] == 'candidate'
    assert perf.loc['PM_box_builder', 'status'] == 'unregistered'
    assert perf.loc['PM_box_builder', 'open_trades'] == 1

    hammer = perf.loc['hammer']
    assert hammer['total_trades'] == 2
    assert hammer['win_rate'] == pytest.approx(0.5)
    assert hammer['pnl_net'] == pytest.approx(20.0)
    assert hammer['profit_factor'] == pytest.approx(3.0)


def test_strategy_lifecycle_reads_the_audit_log(populated_db):
    """The registry stores only the CURRENT status, so it cannot answer 'when
    was this promoted' for anything that moved twice. The audit log can."""
    df = db_reader.get_strategy_lifecycle()
    assert len(df) == 1
    row = df.iloc[0]
    assert row['strategy_id'] == 'hammer'
    assert row['from_status'] == 'candidate'
    assert row['to_status'] == 'shadow'


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def test_overview_metrics_are_hand_checkable(populated_db):
    trades = db_reader.get_trades()
    equity = db_reader.get_equity_curve()
    m = db_reader.compute_overview_metrics(trades, equity)

    assert m['total_trades'] == 2
    assert m['open_positions'] == 1
    assert m['win_rate'] == pytest.approx(0.5)
    assert m['total_pnl'] == pytest.approx(20.0)
    assert m['profit_factor'] == pytest.approx(3.0)     # 30 / 10
    assert m['avg_r'] == pytest.approx(0.5)             # (+2.0 + -1.0) / 2
    assert m['max_drawdown'] == pytest.approx(-0.25)    # 90 / 120 - 1


def test_break_even_trades_count_in_the_win_rate_denominator():
    """Dropping flat trades from the denominator inflates win rate. Small lie,
    compounds fast, invisible to the reader."""
    closed = pd.DataFrame({
        'pnl_net': [10.0, 0.0, -5.0, 0.0],
        'r_multiple': [1.0, 0.0, -1.0, 0.0],
        'closed_ts': [1, 2, 3, 4],
    })
    stats = db_reader._trade_stats(closed)
    assert stats['wins'] == 1
    assert stats['losses'] == 1
    assert stats['win_rate'] == pytest.approx(0.25), 'denominator must be all 4 trades'


def test_profit_factor_is_infinite_with_no_losing_trade():
    """`inf` is the correct answer when there is no loss to divide by, not a
    bug to paper over (convention 12). It must not be squashed to a big float
    or to None."""
    closed = pd.DataFrame({'pnl_net': [10.0, 5.0], 'r_multiple': [1.0, 1.0],
                           'closed_ts': [1, 2]})
    assert math.isinf(db_reader._trade_stats(closed)['profit_factor'])


def test_profit_factor_is_none_when_nothing_moved():
    closed = pd.DataFrame({'pnl_net': [0.0, 0.0], 'r_multiple': [0.0, 0.0],
                           'closed_ts': [1, 2]})
    assert db_reader._trade_stats(closed)['profit_factor'] is None


def test_sharpe_is_annualised_from_observed_spacing_not_an_assumed_bar():
    """The engine snapshots every 15 min and the crypto book runs 24/7.
    Hardcoding 252 trading days would overstate Sharpe by roughly 2.4x."""
    base = 1_700_000_000_000
    df = pd.DataFrame({
        'ts': [base + i * FIFTEEN_MIN_MS for i in range(20)],
        'equity': [100.0 * (1.01 ** i) if i % 2 == 0 else 100.0 * (1.01 ** i) * 0.995
                   for i in range(20)],
    })
    out = db_reader.compute_equity_metrics(df)
    assert out['periods_per_year'] == pytest.approx(365 * 24 * 4)  # 35,040
    assert out['sharpe'] is not None and math.isfinite(out['sharpe'])


def test_metrics_return_none_rather_than_zero_when_uncomputable():
    """A zero and an unknown look nothing alike in a trading context."""
    out = db_reader.compute_equity_metrics(pd.DataFrame(columns=['ts', 'equity']))
    assert out['sharpe'] is None
    assert out['max_drawdown'] is None
    assert out['n_points'] == 0

    two = pd.DataFrame({'ts': [0, FIFTEEN_MIN_MS], 'equity': [100.0, 110.0]})
    out = db_reader.compute_equity_metrics(two)
    assert out['max_drawdown'] == pytest.approx(0.0)
    assert out['sharpe'] is None, 'two points is not a Sharpe ratio'


def test_flat_equity_curve_has_no_sharpe_rather_than_zero():
    df = pd.DataFrame({'ts': [i * FIFTEEN_MIN_MS for i in range(10)],
                       'equity': [100.0] * 10})
    out = db_reader.compute_equity_metrics(df)
    assert out['sharpe'] is None, 'zero dispersion has no Sharpe, it is not Sharpe 0'
    assert out['max_drawdown'] == pytest.approx(0.0)


def test_pnl_since_uses_close_time_not_open_time(populated_db):
    """A trade opened yesterday and closed today put its money in today's
    column. Bucketing by open time moves PnL into the wrong day."""
    trades = db_reader.get_trades()
    win_closed = int(trades.set_index('id').loc['pos-win', 'closed_ts'])

    assert db_reader.pnl_since(trades, 0) == pytest.approx(20.0)
    # Cut just after the winner closed: only the loser remains.
    assert db_reader.pnl_since(trades, win_closed + 1) == pytest.approx(-10.0)


def test_start_of_utc_day_is_a_day_boundary():
    ts = db_reader.start_of_utc_day_ms(1_700_000_000_000)
    assert ts % 86_400_000 == 0
    assert ts <= 1_700_000_000_000


# --------------------------------------------------------------------------
# Asset classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize('pair,strategy,expected', [
    ('BTC/USDT', 'hammer', 'CRYPTO'),
    ('ETH/USD', None, 'CRYPTO'),
    ('SOL/USDC', 'bullish_engulfing', 'CRYPTO'),
    ('will-btc-hit-100k-by-2026', None, 'POLYMARKET'),
    ('POLYMARKET', None, 'POLYMARKET'),
    # The strategy prefix wins: the `pair` column carries a market slug for
    # binaries, and a slug can be shaped like anything.
    ('BTC/USDT', 'PM_corridor_collector', 'POLYMARKET'),
    ('anything', 'polymarket_base', 'POLYMARKET'),
    ('', None, 'UNKNOWN'),
    (None, None, 'UNKNOWN'),
])
def test_classify_asset(pair, strategy, expected):
    assert db_reader.classify_asset(pair, strategy) == expected


# --------------------------------------------------------------------------
# Research artifacts
# --------------------------------------------------------------------------

def test_load_json_artifact_flags_bare_infinity_tokens(tmp_path):
    """`json.loads` accepts bare `Infinity`; `JSON.parse` and most other
    parsers reject it (convention 19). The dashboard still renders the file,
    but it says out loud that the file is not portable."""
    path = tmp_path / 'artifact.json'
    path.write_text('{"c_bps": Infinity, "ok": 1}')

    data, note = db_reader.load_json_artifact(str(path))
    assert data['ok'] == 1
    assert note is not None and 'Infinity' in note


def test_load_json_artifact_clean_file_has_no_note(tmp_path):
    path = tmp_path / 'artifact.json'
    path.write_text(json.dumps({'a': 1, 'b': None}))
    data, note = db_reader.load_json_artifact(str(path))
    assert data == {'a': 1, 'b': None}
    assert note is None


def test_load_json_artifact_missing_and_corrupt(tmp_path):
    data, note = db_reader.load_json_artifact(str(tmp_path / 'nope.json'))
    assert data is None and 'not found' in note

    bad = tmp_path / 'bad.json'
    bad.write_text('{not json at all')
    data, note = db_reader.load_json_artifact(str(bad))
    assert data is None and 'invalid JSON' in note


def test_graveyard_strategy_health_marks_non_firing_strategies():
    """A strategy with tested rows and zero trades did not run and fail - it
    did not run. The two are different verdicts."""
    pack = {'strategies': [
        {'strategy': 'C2', 'n_trades': 0, 'n_rows_tested': 264, 'asset_class': 'FUTURES'},
        {'strategy': 'hammer', 'n_trades': 435, 'n_rows_tested': 143, 'asset_class': 'CRYPTO'},
    ]}
    df = db_reader.graveyard_strategy_health(pack).set_index('strategy')
    assert bool(df.loc['C2', 'fires']) is False
    assert bool(df.loc['hammer', 'fires']) is True


def test_graveyard_strategy_health_handles_a_missing_pack():
    assert db_reader.graveyard_strategy_health(None).empty
    assert db_reader.graveyard_strategy_health({}).empty


def test_summarize_payload_never_raises_on_junk():
    assert db_reader.summarize_payload(None) == ''
    assert db_reader.summarize_payload('not json') == 'not json'
    assert 'loss_pct' in db_reader.summarize_payload('{"loss_pct": 0.031}')


def test_real_research_artifacts_parse_if_present():
    """Smoke test against the committed artifacts. Skips rather than fails if
    a sweep has not produced them - the dashboard tolerates their absence and
    so does this test."""
    summary, note = db_reader.load_graveyard_summary()
    if summary is None:
        pytest.skip('no graveyard summary on disk: {}'.format(note))
    assert 'verdict_counts' in summary
    assert 'distinct_findings' in summary
