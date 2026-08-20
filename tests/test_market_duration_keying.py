"""D-339 clause (3): the 15m signal keying and the calibration tape.

The contract under test is ADDITIVE. Existing rows must mean exactly what
they meant before, pair and tf must not move, and a row nobody keyed must
read NULL rather than a plausible 5m. Those assertions exist because the
fill_was_maker column already made the opposite mistake: its NOT NULL
DEFAULT 0 backfilled thousands of rows into a value that reads like a
measurement nobody took, and the repo still carries a correction about it.
"""
import os
import sqlite3
import sys
import types
from collections import Counter

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.assets import (MARKET_DURATIONS,
                                      market_duration_for_slug)
from engine.polymarket.shadow_loop import (PolymarketShadowLoop, ShadowStore,
                                           window_ts_from_slug)

STRATEGY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'strategies', 'polymarket')


@pytest.fixture
def store(tmp_path):
    s = ShadowStore(str(tmp_path / 't.db'))
    s.ensure_schema()
    return s


def _signal(store, **kw):
    base = dict(strategy_id='PM_x', market_slug='btc-updown-5m-1787022000',
                pattern='p', direction='long', confidence=0.5,
                features={}, acted=False, skip_reason='r')
    base.update(kw)
    return store.record_signal(**base)


class TestDurationFromSlug:
    def test_reads_both_durations(self):
        assert market_duration_for_slug('btc-updown-5m-1787022000') == '5m'
        assert market_duration_for_slug('eth-updown-15m-1787022000') == '15m'

    def test_unknown_is_none_never_5m(self):
        """The whole column depends on this. A guess here is a fake row."""
        for slug in (None, '', 'highest-temperature-in-nyc', 'nonsense'):
            assert market_duration_for_slug(slug) is None

    def test_window_ts_off_the_tail(self):
        assert window_ts_from_slug('btc-updown-15m-1787064300') == 1787064300
        assert window_ts_from_slug('weather-nyc') is None
        assert window_ts_from_slug(None) is None


class TestAdditiveContract:
    def test_default_is_null_not_5m(self, store):
        """A caller that says nothing writes NULL. The fill_was_maker rule."""
        sid = _signal(store)
        row = store.conn.execute(
            'SELECT market_duration FROM signals WHERE id = ?', (sid,)).fetchone()
        assert row[0] is None

    def test_pair_and_tf_are_untouched(self, store):
        """Option A, not option B: the 15m slug must NOT reach pair."""
        sid = _signal(store, market_duration='15m')
        pair, tf, dur = store.conn.execute(
            'SELECT pair, tf, market_duration FROM signals WHERE id = ?',
            (sid,)).fetchone()
        assert pair == 'btc-updown-5m-1787022000'
        assert tf == '5m'
        assert dur == '15m'

    def test_every_value_round_trips(self, store):
        for value in MARKET_DURATIONS:
            sid = _signal(store, market_duration=value)
            got = store.conn.execute(
                'SELECT market_duration FROM signals WHERE id = ?',
                (sid,)).fetchone()[0]
            assert got == value


class TestMigration:
    def test_alter_adds_column_and_leaves_old_rows_null(self, tmp_path):
        """An existing db gains the column; its rows do NOT gain a value."""
        path = str(tmp_path / 'old.db')
        conn = sqlite3.connect(path)
        conn.execute(
            'CREATE TABLE signals (id TEXT PRIMARY KEY, ts INTEGER NOT NULL, '
            'pair TEXT NOT NULL, tf TEXT NOT NULL, strategy_id TEXT NOT NULL, '
            'pattern TEXT NOT NULL, direction TEXT NOT NULL, '
            'confidence REAL NOT NULL, features_json TEXT NOT NULL, '
            'acted INTEGER NOT NULL DEFAULT 0, skip_reason TEXT, '
            "mode TEXT NOT NULL DEFAULT 'paper')")
        conn.execute(
            'INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            ('old', 1, 'btc-updown-5m-1', '5m', 'PM_x', 'p', 'long',
             0.5, '{}', 0, 'r', 'paper'))
        conn.commit()
        conn.close()

        s = ShadowStore(path)
        s.ensure_schema()
        cols = [r[1] for r in s.conn.execute('PRAGMA table_info(signals)')]
        assert 'market_duration' in cols
        assert s.conn.execute(
            'SELECT market_duration FROM signals WHERE id = ?',
            ('old',)).fetchone()[0] is None

    def test_migration_is_idempotent(self, store):
        store.ensure_schema()
        store.ensure_schema()
        cols = [r[1] for r in store.conn.execute('PRAGMA table_info(signals)')]
        assert cols.count('market_duration') == 1


class TestCollapse:
    def test_one_duration_survives(self):
        assert PolymarketShadowLoop._collapse_durations(['15m', '15m']) == '15m'

    def test_two_durations_are_mixed(self):
        assert PolymarketShadowLoop._collapse_durations(['5m', '15m']) == 'mixed'

    def test_nothing_readable_is_none(self):
        assert PolymarketShadowLoop._collapse_durations([]) is None
        assert PolymarketShadowLoop._collapse_durations([None, None]) is None


class TestDeclarationWins:
    """The skip path records the 5m slug even for a pure-15m strategy."""

    def _loop(self, strategy):
        stub = types.SimpleNamespace()
        stub._strategy_named = lambda name: strategy
        return stub

    def test_declaration_beats_the_slug(self):
        strategy = types.SimpleNamespace(market_duration_scope='15m')
        got = PolymarketShadowLoop._market_duration_for(
            self._loop(strategy), 'PM_longshot', 'btc-updown-5m-1787022000')
        assert got == '15m', 'the slug must not win over the declaration'

    def test_undeclared_falls_back_to_the_slug(self):
        strategy = types.SimpleNamespace(market_duration_scope=None)
        got = PolymarketShadowLoop._market_duration_for(
            self._loop(strategy), 'PM_x', 'btc-updown-5m-1787022000')
        assert got == '5m'

    def test_undeclared_and_unkeyable_is_none(self):
        strategy = types.SimpleNamespace(market_duration_scope=None)
        got = PolymarketShadowLoop._market_duration_for(
            self._loop(strategy), 'PM_x', 'highest-temperature-in-nyc')
        assert got is None


def test_every_15m_strategy_declares_a_scope():
    """The guard the slug fallback needs.

    _market_duration_for falls back to the recorded slug, which is ALWAYS
    the 5m market. A strategy that reads ctx.market_15m and forgets to
    declare its scope would have its 15m evaluations keyed 5m - silently,
    and that is the original bug this column exists to fix. This test fails
    the suite instead of letting it happen quietly.
    """
    missing = []
    for name in sorted(os.listdir(STRATEGY_DIR)):
        if not name.endswith('.py') or name in ('base.py', '__init__.py'):
            continue
        with open(os.path.join(STRATEGY_DIR, name)) as fh:
            source = fh.read()
        if 'ctx.market_15m' not in source:
            continue
        if 'market_duration_scope' not in source:
            missing.append(name)
    assert not missing, (
        'these strategies read ctx.market_15m but declare no '
        'market_duration_scope, so their skip rows would be keyed 5m: '
        + ', '.join(missing))


class TestCalibrationTape:
    def _stub(self, store, selected=()):
        return types.SimpleNamespace(store=store, health=Counter(),
                                     _cycle_selected_tokens=set(selected))

    def _ctx(self):
        def market(slug, tokens):
            outcomes = tuple(
                types.SimpleNamespace(name=n, token_id=t) for n, t in tokens)
            return types.SimpleNamespace(slug=slug, condition_id='c1',
                                         outcomes=outcomes)
        book = types.SimpleNamespace(
            midpoint=0.42, best_bid=0.41, best_ask=0.43,
            bids=(1, 2), asks=(3,))
        return types.SimpleNamespace(
            market=market('btc-updown-5m-1787022000',
                          (('Up', 'tok5up'), ('Down', 'tok5dn'))),
            books={'tok5up': book},
            market_15m=market('btc-updown-15m-1787022000',
                              (('Up', 'tok15up'), ('Down', 'tok15dn'))),
            books_15m={})

    def test_samples_every_token_selected_and_not(self, store):
        stub = self._stub(store, selected=['tok5up'])
        written = PolymarketShadowLoop.sample_calibration_tape(
            stub, {'btc': self._ctx()}, 1787022100.0)
        assert written == 4, '2 markets x 2 outcomes'
        rows = dict(store.conn.execute(
            'SELECT token_id, selected FROM calibration_tape').fetchall())
        assert rows == {'tok5up': 1, 'tok5dn': 0,
                        'tok15up': 0, 'tok15dn': 0}

    def test_unread_book_is_null_not_dropped(self, store):
        """Convention 11: an unreadable book is not an absent market."""
        stub = self._stub(store)
        PolymarketShadowLoop.sample_calibration_tape(
            stub, {'btc': self._ctx()}, 1787022100.0)
        mid, depth = store.conn.execute(
            'SELECT mid, book_depth_levels FROM calibration_tape '
            'WHERE token_id = ?', ('tok15up',)).fetchone()
        assert mid is None and depth is None

    def test_duration_and_seconds_remaining(self, store):
        stub = self._stub(store)
        PolymarketShadowLoop.sample_calibration_tape(
            stub, {'btc': self._ctx()}, 1787022100.0)
        dur, rem = store.conn.execute(
            'SELECT market_duration, seconds_remaining FROM calibration_tape '
            'WHERE token_id = ?', ('tok15up',)).fetchone()
        assert dur == '15m'
        assert rem == pytest.approx(1787022000 + 900 - 1787022100.0)

    def test_depth_counts_both_sides(self, store):
        stub = self._stub(store)
        PolymarketShadowLoop.sample_calibration_tape(
            stub, {'btc': self._ctx()}, 1787022100.0)
        depth = store.conn.execute(
            'SELECT book_depth_levels FROM calibration_tape '
            'WHERE token_id = ?', ('tok5up',)).fetchone()[0]
        assert depth == 3


class TestResolutionStamp:
    def _stamp(self, store, **kw):
        base = dict(token_id='t1', market_slug='btc-updown-5m-1787022000',
                    market_duration='5m', window_ts=1787022000,
                    resolved_outcome='UP', won=1, resolved_ts=1.0)
        base.update(kw)
        return store.stamp_calibration_resolution(**base)

    def test_write_once(self, store):
        assert self._stamp(store) is True
        assert self._stamp(store, resolved_outcome='DOWN', won=0) is False
        outcome = store.conn.execute(
            'SELECT resolved_outcome FROM calibration_resolution').fetchone()[0]
        assert outcome == 'UP', 'the FIRST reading survives, not the second'

    def test_pending_excludes_open_windows(self, store):
        store.record_calibration_rows([(
            't1', 'btc-updown-5m-1787022000', '5m', 'Up', 'c', 1.0,
            1787022000, None, None, None, None, None, 0)])
        assert store.pending_calibration_tokens(1787022100.0) == []
        assert len(store.pending_calibration_tokens(1787022400.0)) == 1

    def test_15m_window_is_not_closed_on_the_5m_clock(self, store):
        store.record_calibration_rows([(
            't15', 'btc-updown-15m-1787022000', '15m', 'Up', 'c', 1.0,
            1787022000, None, None, None, None, None, 0)])
        assert store.pending_calibration_tokens(1787022400.0) == []
        assert len(store.pending_calibration_tokens(1787022900.0)) == 1

    def test_stamped_tokens_drop_out_of_pending(self, store):
        store.record_calibration_rows([(
            't1', 'btc-updown-5m-1787022000', '5m', 'Up', 'c', 1.0,
            1787022000, None, None, None, None, None, 0)])
        assert len(store.pending_calibration_tokens(1787022400.0)) == 1
        self._stamp(store)
        assert store.pending_calibration_tokens(1787022400.0) == []


class TestForgeReaderTolerance:
    """read_decisions must survive a database that has not migrated yet.

    R3 puts market_duration in an explicit select list. Naming a column
    unconditionally makes that select an OperationalError against every
    pre-migration database - and env B carries no such column until its own
    restart, which is a different event from the main loop restart. A
    database without the column must report None, which is exactly what
    the column says about a row written before the key existed.
    """

    SIGNALS_OLD = (
        'CREATE TABLE signals (id TEXT PRIMARY KEY, ts INTEGER, pair TEXT, '
        'tf TEXT, strategy_id TEXT, pattern TEXT, direction TEXT, '
        'confidence REAL, features_json TEXT, acted INTEGER, '
        'skip_reason TEXT, mode TEXT)')

    def _conn(self, ddl, row):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute(ddl)
        conn.execute(
            'INSERT INTO signals VALUES (' +
            ','.join('?' * len(row)) + ')', row)
        return conn

    def test_unmigrated_database_reports_none(self):
        from agents.forge_shadow_eval import read_decisions
        conn = self._conn(self.SIGNALS_OLD, (
            'a', 1, 'btc-updown-5m-1', '5m', 'PM_x', 'p', 'long', 0.5,
            '{}', 0, 'r', 'paper'))
        rows = read_decisions(conn)
        assert rows[0]['market_duration'] is None

    def test_migrated_database_reports_the_key(self):
        from agents.forge_shadow_eval import read_decisions
        conn = self._conn(
            self.SIGNALS_OLD[:-1] + ', market_duration TEXT)', (
                'a', 1, 'btc-updown-5m-1', '5m', 'PM_x', 'p', 'long', 0.5,
                '{}', 0, 'r', 'paper', '15m'))
        rows = read_decisions(conn)
        assert rows[0]['market_duration'] == '15m'

