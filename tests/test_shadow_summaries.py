"""Tests for scripts/daily_shadow_summary.py and weekly_shadow_summary.py.

The cases that matter are the ones a reporting script gets quietly wrong:

  - a day with ZERO trades, which is the live state of the shadow loop right
    now. A win rate over zero trades is undefined, not 0%, and a profit factor
    over zero losses is undefined, not infinity. Printing either as a number
    would invent a result from an empty sample.
  - a MISSING or UNREADABLE database. That must produce an explicit error and
    a non-zero exit, never a summary reading "0 trades" (convention 11). A
    report that renders an outage as a flat day is worse than no report.
  - the skip accounting identity (convention 20): every skip lands in exactly
    one category and the categories sum back to the skip total.

Every fixture DB is built from `db/schema.sql`, so a schema change breaks these
tests rather than letting them pass against a shape the loop no longer writes.
"""
import datetime
import json
import os
import sqlite3
import subprocess
import sys
import uuid

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, 'scripts')
SCHEMA_PATH = os.path.join(REPO_ROOT, 'db', 'schema.sql')

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import shadow_summary_lib as lib            # noqa: E402
import daily_shadow_summary as daily        # noqa: E402
import weekly_shadow_summary as weekly      # noqa: E402


DAY = datetime.date(2026, 8, 17)
STRATEGIES = ('PM_streak_snapper', 'PM_corridor_collector')


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _new_db(path):
    conn = sqlite3.connect(path)
    with open(SCHEMA_PATH) as handle:
        conn.executescript(handle.read())
    conn.commit()
    return conn


def _mid_day_ms(day=DAY, hour=12):
    start_ms, _ = lib.et_day_bounds_ms(day)
    return start_ms + hour * 3600 * 1000


def _add_signal(conn, strategy, acted, skip_reason, ts_ms):
    conn.execute(
        'INSERT INTO signals (id, ts, pair, tf, strategy_id, pattern, '
        'direction, confidence, features_json, acted, skip_reason, mode) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (str(uuid.uuid4()), ts_ms, 'btc-updown-5m-1', '5m', strategy,
         strategy, 'long', 0.0, '{}', 1 if acted else 0, skip_reason, 'paper'))


def _add_position(conn, strategy, pnl_net, opened_ms, closed_ms,
                  qty=10.0, entry_px=0.45):
    conn.execute(
        'INSERT INTO positions (id, pair, strategy_id, signal_id, opened_ts, '
        'closed_ts, entry_px, exit_px, qty, stop_px, target_px, pnl_gross, '
        'pnl_net, fees, r_multiple, exit_reason, mode) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (str(uuid.uuid4()), 'btc-updown-5m-1', strategy, None, opened_ms,
         closed_ms, entry_px, 1.0 if (pnl_net or 0) > 0 else 0.0, qty, 0.0,
         1.0, pnl_net, pnl_net, 0.0, None,
         'target' if (pnl_net or 0) > 0 else 'stop', 'paper'))


def _add_equity(conn, ts_ms, equity, open_risk=0.0):
    conn.execute(
        'INSERT OR REPLACE INTO equity_snapshots (ts, equity, cash, '
        'open_risk, mode) VALUES (?, ?, ?, ?, ?)',
        (ts_ms, equity, equity, open_risk, 'paper'))


@pytest.fixture
def empty_csv(tmp_path):
    """A CSV with a header and no rows. Present but empty is not missing."""
    path = tmp_path / 'paper_log.csv'
    path.write_text('ts,iso,strategy,market_slug,action,reason\n')
    return str(path)


@pytest.fixture
def zero_trade_db(tmp_path):
    """The state the shadow loop is actually in: many skips, zero entries."""
    path = str(tmp_path / 'zero.db')
    conn = _new_db(path)
    base = _mid_day_ms()
    for index in range(20):
        _add_signal(conn, STRATEGIES[0], False, 'no_streak', base + index)
        _add_signal(conn, STRATEGIES[1], False, 'no_lead_or_atr', base + index)
    _add_equity(conn, base, 1000.0)
    _add_equity(conn, base + 600_000, 1000.0)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def win_loss_db(tmp_path):
    """Three wins, two losses, one flat, plus one open position."""
    path = str(tmp_path / 'wl.db')
    conn = _new_db(path)
    base = _mid_day_ms()
    for index in range(6):
        _add_signal(conn, STRATEGIES[0], True, None, base + index)
    for index in range(4):
        _add_signal(conn, STRATEGIES[1], False, 'no_lead_or_atr', base + index)
    for pnl in (2.0, 3.0, 5.0):
        _add_position(conn, STRATEGIES[0], pnl, base, base + 60_000)
    for pnl in (-1.0, -4.0):
        _add_position(conn, STRATEGIES[0], pnl, base, base + 60_000)
    _add_position(conn, STRATEGIES[0], 0.0, base, base + 60_000)
    conn.execute(
        'INSERT INTO positions (id, pair, strategy_id, signal_id, opened_ts, '
        'closed_ts, entry_px, exit_px, qty, stop_px, target_px, pnl_gross, '
        'pnl_net, fees, r_multiple, exit_reason, mode) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (str(uuid.uuid4()), 'btc-updown-5m-1', STRATEGIES[0], None, base,
         None, 0.40, None, 10.0, 0.0, 1.0, None, None, 0.10, None, None,
         'paper'))
    _add_equity(conn, base, 1000.0)
    _add_equity(conn, base + 300_000, 1004.0)
    _add_equity(conn, base + 600_000, 1005.0)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def all_wins_db(tmp_path):
    """Wins only. Profit factor must be undefined here, not infinite."""
    path = str(tmp_path / 'wins.db')
    conn = _new_db(path)
    base = _mid_day_ms()
    for pnl in (1.5, 2.5):
        _add_position(conn, STRATEGIES[0], pnl, base, base + 60_000)
        _add_signal(conn, STRATEGIES[0], True, None, base)
    _add_equity(conn, base, 1000.0)
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# Pure arithmetic: undefined is not zero, and nothing divides by zero
# ---------------------------------------------------------------------------

def test_win_rate_over_zero_trades_is_undefined():
    assert lib.win_rate(0, 0) is None
    assert lib.fmt_rate(lib.win_rate(0, 0), 0) == 'n/a (0 trades)'


def test_win_rate_zero_percent_is_not_the_same_as_undefined():
    """A strategy that traded and lost every time HAS a win rate: 0%."""
    assert lib.win_rate(0, 5) == 0.0
    assert lib.fmt_rate(lib.win_rate(0, 5), 5) == '0.0%'
    assert lib.fmt_rate(lib.win_rate(0, 0), 0) != '0.0%'


def test_win_rate_normal():
    assert lib.win_rate(3, 1) == 0.75
    assert lib.fmt_rate(lib.win_rate(3, 1), 4) == '75.0%'


def test_profit_factor_with_no_losses_is_undefined_not_infinity():
    assert lib.profit_factor(10.0, 0.0) is None
    assert lib.fmt_pf(lib.profit_factor(10.0, 0.0), 0) == \
        'n/a (0 losing trades)'


def test_profit_factor_with_no_trades_at_all_is_undefined():
    assert lib.profit_factor(0.0, 0.0) is None


def test_profit_factor_normal():
    assert lib.profit_factor(10.0, 4.0) == 2.5
    assert lib.fmt_pf(lib.profit_factor(10.0, 4.0), 2) == '2.50'


def test_safe_div_never_raises_and_never_returns_inf():
    assert lib.safe_div(1, 0) is None
    assert lib.safe_div(0, 0) is None
    assert lib.safe_div(1, None) is None
    assert lib.safe_div(1.0, 4.0) == 0.25


def test_fmt_pct_of_handles_a_zero_whole():
    assert lib.fmt_pct_of(0, 0) == 'n/a'


# ---------------------------------------------------------------------------
# Skip taxonomy (convention 20)
# ---------------------------------------------------------------------------

def test_classify_separates_missing_input_from_a_strategy_declining():
    assert lib.classify_reason('no_lead_or_atr') == lib.DATA_BLOCKER
    assert lib.classify_reason('no_spot_or_strike') == lib.DATA_BLOCKER
    assert lib.classify_reason('maker_fill_not_simulated') == lib.DATA_BLOCKER
    assert lib.classify_reason('no_streak') == lib.NO_TRADE
    assert lib.classify_reason('book_too_tight_to_arm') == lib.NO_TRADE


def test_classify_prefixed_reasons():
    assert lib.classify_reason('api_error:attempt_3') == lib.DATA_BLOCKER
    assert lib.classify_reason('halted') == lib.OPERATIONAL
    assert lib.classify_reason('risk_gate:over_notional_cap') == \
        lib.OPERATIONAL
    assert lib.classify_reason('adapter:book_above_limit') == lib.NO_TRADE
    assert lib.classify_reason('strategy:no_streak') == lib.NO_TRADE
    assert lib.classify_reason('adapter:halted') == lib.OPERATIONAL
    assert lib.classify_reason('enter_without_legs') == lib.ERROR
    assert lib.classify_reason('cycle_exception') == lib.ERROR


def test_unknown_reason_is_unclassified_not_guessed():
    assert lib.classify_reason('brand_new_reason_nobody_mapped') == \
        lib.UNCLASSIFIED
    assert lib.classify_reason(None) == lib.UNCLASSIFIED
    assert lib.classify_reason('') == lib.UNCLASSIFIED


def test_skip_categories_sum_to_the_skip_total():
    reasons = {'no_streak': 5, 'no_lead_or_atr': 3, 'halted': 1,
               'mystery_reason': 2}
    categories, unclassified = lib.skip_category_counts(reasons)
    assert sum(categories.values()) == sum(reasons.values()) == 11
    assert unclassified == {'mystery_reason': 2}
    assert categories[lib.UNCLASSIFIED] == 2


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def test_aggregate_signals_asserts_the_identity():
    rows = [{'strategy_id': 'a', 'acted': 0, 'skip_reason': 'no_streak',
             'ts': 1},
            {'strategy_id': 'a', 'acted': 1, 'skip_reason': None, 'ts': 2}]
    out = lib.aggregate_signals(rows)
    assert out['evaluations'] == 2
    assert out['entries'] == 1
    assert out['skips'] == 1
    assert out['identity_ok'] is True


def test_aggregate_trades_keeps_flats_out_of_wins_and_losses():
    closed = [{'strategy_id': 'a', 'pnl_net': 2.0},
              {'strategy_id': 'a', 'pnl_net': -1.0},
              {'strategy_id': 'a', 'pnl_net': 0.0}]
    out = lib.aggregate_trades(closed)
    assert (out['wins'], out['losses'], out['flats']) == (1, 1, 1)
    assert out['win_rate'] == 0.5
    assert out['pnl'] == pytest.approx(1.0)


def test_aggregate_trades_excludes_unpriced_rows_from_pnl():
    """A closed row with pnl_net NULL is a bookkeeping fault, not a zero."""
    closed = [{'strategy_id': 'a', 'pnl_net': 3.0},
              {'strategy_id': 'a', 'pnl_net': None}]
    out = lib.aggregate_trades(closed)
    assert out['unpriced'] == 1
    assert out['closed'] == 2
    assert out['wins'] == 1
    assert out['pnl'] == pytest.approx(3.0)


def test_aggregate_trades_on_nothing_is_all_undefined():
    out = lib.aggregate_trades([])
    assert out['closed'] == 0
    assert out['win_rate'] is None
    assert out['profit_factor'] is None
    assert out['avg_pnl'] is None
    assert out['pnl'] == 0.0


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def test_et_day_bounds_are_half_open_and_contiguous():
    start, end = lib.et_day_bounds_ms(DAY)
    next_start, _ = lib.et_day_bounds_ms(DAY + datetime.timedelta(days=1))
    assert end == next_start
    assert end - start == 24 * 3600 * 1000


def test_a_utc_timestamp_after_midnight_utc_is_still_the_previous_et_day():
    """The live loop stamps 02:15Z, which is 22:15 ET the day BEFORE.

    Getting this wrong would silently split one trading evening across two
    daily reports.
    """
    start, end = lib.et_day_bounds_ms(datetime.date(2026, 8, 17))
    ts_ms = int(datetime.datetime(
        2026, 8, 18, 2, 15, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    assert start <= ts_ms < end


def test_parse_date_rejects_garbage():
    with pytest.raises(lib.DataSourceError):
        lib.parse_date('not-a-date')


# ---------------------------------------------------------------------------
# Daily: the zero-trade case that is live right now
# ---------------------------------------------------------------------------

def test_daily_zero_trade_day(zero_trade_db, empty_csv, capsys):
    code = daily.main(['--date', DAY.isoformat(), '--db', zero_trade_db,
                       '--csv', empty_csv])
    out = capsys.readouterr().out
    assert code == 0
    assert 'win rate         n/a (0 trades)' in out
    assert 'profit factor    n/a (0 losing trades)' in out
    assert 'evaluations         40' in out
    assert 'entries              0' in out
    assert 'identity            OK' in out
    assert 'DATA BLOCKER' in out or 'DATA_BLOCKER' in out
    # The failure this test exists to prevent: an invented 0% win rate.
    assert 'win rate         0.0%' not in out


def test_daily_zero_trade_day_marks_data_blocked_strategies(
        zero_trade_db, empty_csv, capsys):
    daily.main(['--date', DAY.isoformat(), '--db', zero_trade_db,
                '--csv', empty_csv])
    out = capsys.readouterr().out
    assert 'PM_corridor_collector  100% of skips are data blockers' in out
    assert 'NOT_TESTED' in out


def test_daily_zero_trade_json_is_strict_and_undefined_stays_null(
        zero_trade_db, empty_csv, capsys):
    daily.main(['--date', DAY.isoformat(), '--db', zero_trade_db,
                '--csv', empty_csv, '--json'])
    payload = json.loads(capsys.readouterr().out)
    assert payload['trades']['win_rate'] is None
    assert payload['trades']['profit_factor'] is None
    assert payload['decisions']['entries'] == 0
    # allow_nan=False means a non-finite would have raised at dump time.
    assert 'Infinity' not in json.dumps(payload)
    assert 'NaN' not in json.dumps(payload)


def test_daily_on_a_day_with_no_rows_at_all_says_so(tmp_path, empty_csv,
                                                    capsys):
    """An empty but READABLE database is allowed to report zero, and must
    still say the window contained no evaluation rows rather than implying a
    quiet market."""
    path = str(tmp_path / 'blank.db')
    _new_db(path).close()
    code = daily.main(['--date', DAY.isoformat(), '--db', path,
                       '--csv', empty_csv])
    out = capsys.readouterr().out
    assert code == 0
    assert 'no evaluation rows in this window' in out
    assert 'NOT_TESTED' in out


# ---------------------------------------------------------------------------
# Daily: wins and losses
# ---------------------------------------------------------------------------

def test_daily_with_wins_and_losses(win_loss_db, empty_csv, capsys):
    code = daily.main(['--date', DAY.isoformat(), '--db', win_loss_db,
                       '--csv', empty_csv, '--json'])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    trades = payload['trades']
    assert trades['closed'] == 6
    assert (trades['wins'], trades['losses'], trades['flats']) == (3, 2, 1)
    assert trades['win_rate'] == pytest.approx(0.6)
    assert trades['gross_profit'] == pytest.approx(10.0)
    assert trades['gross_loss'] == pytest.approx(5.0)
    assert trades['profit_factor'] == pytest.approx(2.0)
    assert trades['pnl'] == pytest.approx(5.0)
    assert payload['decisions']['entries'] == 6
    assert payload['open_at_cutoff']['count'] == 1
    assert payload['open_at_cutoff']['premium_at_risk'] == pytest.approx(4.10)
    assert payload['first_trade_ever_is_today'] is True


def test_daily_with_wins_and_losses_renders_real_numbers(win_loss_db,
                                                         empty_csv, capsys):
    daily.main(['--date', DAY.isoformat(), '--db', win_loss_db,
                '--csv', empty_csv])
    out = capsys.readouterr().out
    assert 'win rate         60.0%' in out
    assert 'profit factor    2.00' in out
    assert 'realised P&L     $5.00' in out
    assert 'FIRST TRADE EVER recorded today' in out
    assert 'n/a (0 trades)' not in out.split('PER STRATEGY')[0]


def test_daily_equity_curve_start_end_min_max(win_loss_db, empty_csv, capsys):
    daily.main(['--date', DAY.isoformat(), '--db', win_loss_db,
                '--csv', empty_csv, '--json'])
    equity = json.loads(capsys.readouterr().out)['equity']
    assert equity['status'] == 'OK'
    assert equity['start'] == 1000.0
    assert equity['end'] == 1005.0
    assert equity['min'] == 1000.0
    assert equity['max'] == 1005.0
    assert equity['change'] == pytest.approx(5.0)


def test_all_wins_profit_factor_is_undefined_end_to_end(all_wins_db,
                                                        empty_csv, capsys):
    daily.main(['--date', DAY.isoformat(), '--db', all_wins_db,
                '--csv', empty_csv])
    out = capsys.readouterr().out
    assert 'win rate         100.0%' in out
    assert 'profit factor    n/a (0 losing trades)' in out
    assert 'inf' not in out.lower().split('per strategy')[0]


# ---------------------------------------------------------------------------
# Missing / unreadable database: convention 11
# ---------------------------------------------------------------------------

def test_open_db_raises_on_a_missing_file(tmp_path):
    with pytest.raises(lib.DataSourceError) as exc:
        lib.open_db(str(tmp_path / 'nope.db'))
    assert 'not found' in str(exc.value)


def test_open_db_raises_on_a_file_that_is_not_a_database(tmp_path):
    path = tmp_path / 'garbage.db'
    path.write_bytes(b'this is definitely not a sqlite database' * 40)
    with pytest.raises(lib.DataSourceError):
        lib.open_db(str(path))


def test_check_schema_refuses_a_database_missing_our_tables(tmp_path):
    path = str(tmp_path / 'wrong.db')
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE unrelated (x INTEGER)')
    conn.commit()
    conn.close()
    read = lib.open_db(path)
    try:
        with pytest.raises(lib.DataSourceError) as exc:
            lib.check_schema(read, path)
    finally:
        read.close()
    assert 'missing table' in str(exc.value)


def test_daily_missing_db_reports_unreadable_and_never_a_flat_day(
        tmp_path, capsys):
    code = daily.main(['--date', DAY.isoformat(),
                       '--db', str(tmp_path / 'gone.db')])
    out = capsys.readouterr().out
    assert code == 2
    assert 'UNREADABLE' in out
    assert 'NOT_TESTED' in out
    # The exact misreading this guards against.
    assert '0 trades' not in out
    assert 'win rate' not in out
    assert 'realised P&L' not in out


def test_daily_unreadable_db_json_is_an_error_object(tmp_path, capsys):
    code = daily.main(['--date', DAY.isoformat(),
                       '--db', str(tmp_path / 'gone.db'), '--json'])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload['status'] == 'ERROR'
    assert 'trades' not in payload
    assert 'decisions' not in payload


def test_weekly_missing_db_reports_unreadable(tmp_path, capsys):
    code = weekly.main(['--week-ending', DAY.isoformat(),
                        '--db', str(tmp_path / 'gone.db')])
    out = capsys.readouterr().out
    assert code == 2
    assert 'UNREADABLE' in out
    assert '0 trades' not in out


def test_daily_corrupt_db_reports_unreadable(tmp_path, capsys):
    path = tmp_path / 'corrupt.db'
    path.write_bytes(b'\x00\x01\x02not a db' * 100)
    code = daily.main(['--date', DAY.isoformat(), '--db', str(path)])
    out = capsys.readouterr().out
    assert code == 2
    assert 'UNREADABLE' in out


# ---------------------------------------------------------------------------
# Missing CSV is NOT fatal, but it is NOT_TESTED
# ---------------------------------------------------------------------------

def test_missing_csv_is_not_tested_not_zero(zero_trade_db, tmp_path, capsys):
    code = daily.main(['--date', DAY.isoformat(), '--db', zero_trade_db,
                       '--csv', str(tmp_path / 'nothing.csv')])
    out = capsys.readouterr().out
    assert code == 0
    assert 'csv MISSING' in out
    assert 'NOT_TESTED as a cross check' in out


def test_csv_rows_outside_the_window_are_excluded(tmp_path):
    path = tmp_path / 'log.csv'
    start_ms, end_ms = lib.et_day_bounds_ms(DAY)
    inside = int(start_ms / 1000) + 100
    outside = int(end_ms / 1000) + 100
    path.write_text(
        'ts,action,reason\n'
        '{},SKIP,no_streak\n'
        '{},SKIP,no_streak\n'
        'bad,SKIP,no_streak\n'.format(inside, outside))
    stats = lib.read_csv_rows(str(path), start_ms / 1000.0, end_ms / 1000.0)
    assert stats['status'] == 'OK'
    assert stats['rows'] == 1
    assert stats['unparsable_ts'] == 1


# ---------------------------------------------------------------------------
# Weekly
# ---------------------------------------------------------------------------

def test_weekly_zero_trade_week(zero_trade_db, empty_csv, capsys):
    code = weekly.main(['--week-ending', DAY.isoformat(),
                        '--db', zero_trade_db, '--csv', empty_csv])
    out = capsys.readouterr().out
    assert code == 0
    assert 'win rate      n/a (0 trades)' in out
    assert 'profit factor n/a (0 losing trades)' in out
    assert 'no strategy closed a trade this week' in out
    assert 'Undefined, not last place.' in out
    assert 'RECOMMENDATIONS FOR NEXT WEEK' in out


def test_weekly_day_by_day_has_seven_rows(zero_trade_db, empty_csv, capsys):
    weekly.main(['--week-ending', DAY.isoformat(), '--db', zero_trade_db,
                 '--csv', empty_csv, '--json'])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload['daily']) == 7
    assert payload['daily'][-1]['date_et'] == DAY.isoformat()
    assert payload['week_start_et'] == '2026-08-11'
    assert sum(day['evaluations'] for day in payload['daily']) == \
        payload['decisions']['evaluations']


def test_weekly_best_and_worst_with_real_trades(win_loss_db, empty_csv,
                                                capsys):
    weekly.main(['--week-ending', DAY.isoformat(), '--db', win_loss_db,
                 '--csv', empty_csv])
    out = capsys.readouterr().out
    assert 'by P&L   best  PM_streak_snapper' in out
    assert 'by win % best  PM_streak_snapper' in out
    assert 'decided trades' in out          # the convention 7 caveat


def test_weekly_ranking_excludes_strategies_with_no_decided_trades():
    per = {'traded': {'pnl': 1.0, 'win_rate': 0.5, 'wins': 1, 'losses': 1},
           'never_traded': {'pnl': 0.0, 'win_rate': None, 'wins': 0,
                            'losses': 0}}
    ranked = weekly._ranked(per, 'win_rate', require_trades=True)
    assert [name for name, _, _ in ranked] == ['traded']


def test_weekly_strategy_changes_are_derived_not_guessed(zero_trade_db,
                                                         empty_csv, capsys):
    weekly.main(['--week-ending', DAY.isoformat(), '--db', zero_trade_db,
                 '--csv', empty_csv, '--json'])
    changes = json.loads(capsys.readouterr().out)['strategy_changes']
    assert {item['strategy'] for item in changes['added']} == set(STRATEGIES)
    assert changes['removed'] == []
    assert 'strategy_registry is empty' in changes['derivation']


def test_weekly_does_not_call_a_strategy_stopped_over_a_millisecond(
        tmp_path, empty_csv, capsys):
    """The regression this test exists for.

    Inside one poll cycle the loop writes its strategies milliseconds apart in
    list order. Comparing raw max timestamps made whichever strategy was
    written last look alive and every other one look retired. Comparison is on
    ET calendar days for exactly this reason.
    """
    path = str(tmp_path / 'cycle.db')
    conn = _new_db(path)
    base = _mid_day_ms()
    for cycle in range(3):
        for offset, strategy in enumerate(STRATEGIES):
            _add_signal(conn, strategy, False, 'no_streak',
                        base + cycle * 5000 + offset)
    conn.commit()
    conn.close()
    weekly.main(['--week-ending', DAY.isoformat(), '--db', path,
                 '--csv', empty_csv, '--json'])
    changes = json.loads(capsys.readouterr().out)['strategy_changes']
    assert changes['removed'] == []
    assert sorted(changes['still_active_at_window_end']) == sorted(STRATEGIES)


def test_weekly_flags_a_strategy_that_stopped_on_an_earlier_day(
        tmp_path, empty_csv, capsys):
    """A strategy silent on the last active day IS reported as stopped."""
    path = str(tmp_path / 'stopped.db')
    conn = _new_db(path)
    earlier = _mid_day_ms(DAY - datetime.timedelta(days=2))
    latest = _mid_day_ms(DAY)
    _add_signal(conn, 'PM_retired', False, 'no_streak', earlier)
    _add_signal(conn, 'PM_alive', False, 'no_streak', earlier + 1)
    _add_signal(conn, 'PM_alive', False, 'no_streak', latest)
    conn.commit()
    conn.close()
    weekly.main(['--week-ending', DAY.isoformat(), '--db', path,
                 '--csv', empty_csv, '--json'])
    changes = json.loads(capsys.readouterr().out)['strategy_changes']
    assert [item['strategy'] for item in changes['removed']] == ['PM_retired']
    assert changes['last_active_day_et'] == DAY.isoformat()


def test_weekly_recommendations_are_rule_driven(zero_trade_db, empty_csv,
                                                capsys):
    weekly.main(['--week-ending', DAY.isoformat(), '--db', zero_trade_db,
                 '--csv', empty_csv])
    out = capsys.readouterr().out
    block = out.split('RECOMMENDATIONS FOR NEXT WEEK')[1]
    assert '0 entries' in block
    assert 'DATA BLOCKERS' in block
    assert 'Every line above is emitted by a rule' in block


# ---------------------------------------------------------------------------
# --send writes a file and says it did not send
# ---------------------------------------------------------------------------

def test_send_writes_a_file_and_does_not_claim_to_deliver(
        zero_trade_db, empty_csv, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lib, 'SUMMARY_DIR', str(tmp_path / 'summaries'))
    code = daily.main(['--date', DAY.isoformat(), '--db', zero_trade_db,
                       '--csv', empty_csv, '--send'])
    out = capsys.readouterr().out
    assert code == 0
    target = tmp_path / 'summaries' / 'daily_2026-08-17.txt'
    assert target.exists()
    assert 'SHADOW DAILY' in target.read_text()
    assert 'NOT sent' in out


def test_weekly_send_writes_a_file(zero_trade_db, empty_csv, tmp_path,
                                   monkeypatch, capsys):
    monkeypatch.setattr(lib, 'SUMMARY_DIR', str(tmp_path / 'summaries'))
    weekly.main(['--week-ending', DAY.isoformat(), '--db', zero_trade_db,
                 '--csv', empty_csv, '--send'])
    assert (tmp_path / 'summaries' / 'weekly_2026-08-17.txt').exists()


# ---------------------------------------------------------------------------
# Convention 19: a non-finite must fail at WRITE time
# ---------------------------------------------------------------------------

def test_dump_json_refuses_non_finite():
    with pytest.raises(ValueError):
        lib.dump_json({'x': float('inf')})
    with pytest.raises(ValueError):
        lib.dump_json({'x': float('nan')})


# ---------------------------------------------------------------------------
# End to end against the real database, if it is there
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not os.path.exists(lib.DEFAULT_DB_PATH),
                    reason='no live db/trading.db in this checkout')
def test_scripts_run_against_the_live_database():
    """Both scripts must exit 0 against the real, concurrently written DB."""
    for script in ('daily_shadow_summary.py', 'weekly_shadow_summary.py'):
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, script), '--json'],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[-2000:]
        payload = json.loads(result.stdout)
        assert payload['status'] == 'OK'
        assert payload['decisions']['identity_ok'] is True
