"""The settlement resolution ledger records what markets actually did.

Forge proposal 038 (`pm_settlement_resolution_ledger`). These tests pin the
five ways this instrument could quietly become the thing it was built to
replace.

1. **A DEFAULT ON `resolved_px`.** `fill_was_maker` shipped as
   `INTEGER NOT NULL DEFAULT 0`, 2,253 pre-existing rows backfilled to 0, and
   convention 32 became mechanically checkable only after a timestamp. If this
   table ever gains a default, an unrecorded market becomes a recorded loss and
   the whole repair inverts. `TestSchema` pins no-default and no-not-null
   column by column.

2. **RECORDING ONLY WHAT WE TRADED.** The defect being repaired is that
   resolution was recoverable only from a sibling position held to settlement,
   which selects for losers. If the ledger is ever wired to the entry path it
   reproduces that bias exactly. `TestWriterRecordsFetchedNotTraded` runs a
   market that was never traded end to end.

3. **`resolution_for` RETURNING 0.00 FOR UNKNOWN.** That converts every
   unrecorded market into a recorded loss - the same failure as (1), reached
   through the read path. `TestReadPath` pins None, and pins that it is
   distinguishable from a real 0.00.

4. **POOLED SOURCES.** A venue reading and a sibling inference have different
   error modes; a mixed column silently becomes the weaker of the two
   (convention 32's discipline). `TestSources` pins the vocabulary closed, and
   pins backfill out of the coverage number.

5. **A SILENT GAP.** A market fetched and never resolved must be a counted
   number with a reason (rule 7, convention 20), never an absence.
   `TestUnresolvedAccounting` pins the count, the reason and the window.
"""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.market_resolution import (  # noqa: E402
    MarketResolution, STATUS_NOT_CLOSED, STATUS_RESOLVED)
from engine.polymarket.resolution_ledger import (  # noqa: E402
    BACKFILL_SOURCES, LIVE_SOURCES, RESOLUTION_SOURCES,
    SOURCE_SIBLING_INFERENCE_BACKFILL, SOURCE_VENUE, ResolutionLedger,
    ensure_schema, resolution_for, resolution_row_for, table_exists,
    write_resolutions)
from engine.polymarket.types import Market, Outcome  # noqa: E402

import backtest.settlement_coverage as SC  # noqa: E402

UP_TOK = 'tok-up'
DOWN_TOK = 'tok-down'
COND = 'cond-1'
SLUG = 'btc-updown-5m-1787000000'
WINDOW = 1787000000
DURATION = 300


def make_market(slug=SLUG, condition_id=COND, up=UP_TOK, down=DOWN_TOK):
    # `Outcome.name`, not `.outcome`. The ledger read `.outcome` in its first
    # draft, which does not raise - it yields an empty outcomes tuple, so every
    # market is refused as `no_outcomes` and the recorder reports itself
    # healthy while writing nothing. Building the real dataclass here rather
    # than a stub is what caught it.
    return Market(
        id='m1', question='Will BTC go up?', slug=slug,
        condition_id=condition_id,
        outcomes=(Outcome(name='Up', token_id=up),
                  Outcome(name='Down', token_id=down)))


def resolved(winner=UP_TOK, loser=DOWN_TOK):
    return MarketResolution(
        condition_id=COND, closed=True, resolved=True, status=STATUS_RESOLVED,
        winning_token_ids=frozenset([winner]),
        losing_token_ids=frozenset([loser]),
        winning_outcomes=frozenset(['up']),
        losing_outcomes=frozenset(['down']), market_slug=SLUG)


def unresolved(status=STATUS_NOT_CLOSED):
    return MarketResolution(condition_id=COND, closed=False, resolved=False,
                            status=status, market_slug=SLUG)


class StubCache(object):
    """Stands in for MarketResolutionCache. Counts lookups, never networks."""

    def __init__(self, answer=None, answers=None):
        self.answer = answer
        self.answers = answers or {}
        self.lookups = []

    def get(self, condition_id):
        self.lookups.append(condition_id)
        if condition_id in self.answers:
            return self.answers[condition_id]
        return self.answer if self.answer is not None else unresolved()

    def __len__(self):
        return len(self.answers)


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    ensure_schema(c)
    return c


# ---------------------------------------------------------------------------
# 1. Schema
# ---------------------------------------------------------------------------

class TestSchema(object):
    """No default, no NOT NULL on the value, and one row per market-side."""

    EXPECTED = (('market_slug', 'TEXT'), ('outcome_side', 'TEXT'),
                ('resolved_px', 'REAL'), ('resolved_ts', 'REAL'),
                ('window_ts', 'INTEGER'), ('source', 'TEXT'))

    def test_columns_and_types(self, conn):
        got = [(r[1], r[2]) for r in
               conn.execute('PRAGMA table_info(market_resolutions)')]
        assert got == list(self.EXPECTED)

    def test_no_column_carries_a_default(self, conn):
        for row in conn.execute('PRAGMA table_info(market_resolutions)'):
            assert row[4] is None, (
                '%s has DEFAULT %r; a default makes an unrecorded market '
                'indistinguishable from an observed one, which is the '
                'fill_was_maker mistake exactly' % (row[1], row[4]))

    def test_no_column_is_not_null(self, conn):
        for row in conn.execute('PRAGMA table_info(market_resolutions)'):
            assert row[3] == 0, (
                '%s is NOT NULL; rule 1 requires resolved_px nullable so that '
                'NULL can mean NOT RECORDED rather than 0.00' % row[1])

    def test_market_side_is_unique(self, conn):
        write_resolutions(conn, [(SLUG, 'Up', 1.0, WINDOW)], SOURCE_VENUE)
        # A second write for the same market-side is IGNORED, not applied. A
        # market resolves once; a re-observation must never rewrite it, and a
        # later backfill must never overwrite a live venue reading.
        written = write_resolutions(conn, [(SLUG, 'Up', 0.0, WINDOW)],
                                    SOURCE_SIBLING_INFERENCE_BACKFILL)
        assert written == 0
        rows = conn.execute('SELECT resolved_px, source FROM '
                            'market_resolutions').fetchall()
        assert rows == [(1.0, SOURCE_VENUE)]

    def test_ensure_schema_is_idempotent(self, conn):
        ensure_schema(conn)
        ensure_schema(conn)
        assert table_exists(conn)

    def test_table_exists_is_false_on_a_bare_database(self):
        assert table_exists(sqlite3.connect(':memory:')) is False


# ---------------------------------------------------------------------------
# 2. The writer records what was FETCHED, not what was traded
# ---------------------------------------------------------------------------

class TestWriterRecordsFetchedNotTraded(object):
    """The whole repair: no position is involved anywhere in this path."""

    def ledger(self, conn, cache, **kw):
        kw.setdefault('grace_sec', 0.0)
        return ResolutionLedger(conn=conn, cache=cache, **kw)

    def test_a_market_never_traded_is_recorded(self, conn):
        cache = StubCache(answer=resolved())
        ledger = self.ledger(conn, cache)
        assert ledger.observe(make_market(), WINDOW, DURATION) is True
        summary = ledger.sweep(now=WINDOW + DURATION + 1)
        assert summary['resolved'] == 1
        assert summary['written'] == 2
        rows = dict(conn.execute('SELECT outcome_side, resolved_px FROM '
                                 'market_resolutions').fetchall())
        assert rows == {'Up': 1.0, 'Down': 0.0}

    def test_both_sides_are_recorded_not_only_the_winner(self, conn):
        ledger = self.ledger(conn, StubCache(answer=resolved()))
        ledger.observe(make_market(), WINDOW, DURATION)
        ledger.sweep(now=WINDOW + DURATION + 1)
        assert conn.execute('SELECT COUNT(*) FROM market_resolutions'
                            ).fetchone()[0] == 2

    def test_the_source_is_the_venue(self, conn):
        ledger = self.ledger(conn, StubCache(answer=resolved()))
        ledger.observe(make_market(), WINDOW, DURATION)
        ledger.sweep(now=WINDOW + DURATION + 1)
        sources = set(r[0] for r in conn.execute(
            'SELECT DISTINCT source FROM market_resolutions'))
        assert sources == {SOURCE_VENUE}

    def test_outcome_side_keeps_the_venue_casing_not_the_lowered_one(self,
                                                                    conn):
        # `resolution_from_clob` lowercases display names into its outcome
        # sets. Writing THOSE would key the ledger 'up'/'down' while
        # signals.features_json carries 'Up'/'Down'.
        ledger = self.ledger(conn, StubCache(answer=resolved()))
        ledger.observe(make_market(), WINDOW, DURATION)
        ledger.sweep(now=WINDOW + DURATION + 1)
        sides = set(r[0] for r in conn.execute(
            'SELECT outcome_side FROM market_resolutions'))
        assert sides == {'Up', 'Down'}

    def test_the_price_comes_from_the_token_id_not_the_display_string(self,
                                                                     conn):
        # Down wins. If the writer matched on display strings it would have to
        # agree with the outcome sets, which still say 'up' won.
        answer = MarketResolution(
            condition_id=COND, closed=True, resolved=True,
            status=STATUS_RESOLVED,
            winning_token_ids=frozenset([DOWN_TOK]),
            losing_token_ids=frozenset([UP_TOK]),
            winning_outcomes=frozenset(['up']),
            losing_outcomes=frozenset(['down']), market_slug=SLUG)
        ledger = self.ledger(conn, StubCache(answer=answer))
        ledger.observe(make_market(), WINDOW, DURATION)
        ledger.sweep(now=WINDOW + DURATION + 1)
        rows = dict(conn.execute('SELECT outcome_side, resolved_px FROM '
                                 'market_resolutions').fetchall())
        assert rows == {'Down': 1.0, 'Up': 0.0}

    def test_nothing_is_written_before_the_window_closes(self, conn):
        ledger = self.ledger(conn, StubCache(answer=resolved()),
                             grace_sec=60.0)
        ledger.observe(make_market(), WINDOW, DURATION)
        summary = ledger.sweep(now=WINDOW + 10)
        assert summary['due'] == 0
        assert summary['lookups'] == 0
        assert conn.execute('SELECT COUNT(*) FROM market_resolutions'
                            ).fetchone()[0] == 0

    def test_an_unresolved_market_writes_nothing_at_all(self, conn):
        ledger = self.ledger(conn, StubCache(answer=unresolved()))
        ledger.observe(make_market(), WINDOW, DURATION)
        ledger.sweep(now=WINDOW + DURATION + 1)
        # NOT a row at 0.00. An absent row is the honest record.
        assert conn.execute('SELECT COUNT(*) FROM market_resolutions'
                            ).fetchone()[0] == 0
        assert ledger.stats()['pending'] == 1

    def test_re_observing_the_same_market_is_one_row(self, conn):
        ledger = self.ledger(conn, StubCache(answer=resolved()))
        for _ in range(60):
            ledger.observe(make_market(), WINDOW, DURATION)
        assert ledger.stats()['health']['observe_already_pending'] == 59
        ledger.sweep(now=WINDOW + DURATION + 1)
        for _ in range(10):
            ledger.observe(make_market(), WINDOW, DURATION)
        assert conn.execute('SELECT COUNT(*) FROM market_resolutions'
                            ).fetchone()[0] == 2

    def test_the_lookup_budget_defers_rather_than_dropping(self, conn):
        cache = StubCache(answer=unresolved())
        ledger = self.ledger(conn, cache, max_lookups_per_sweep=2)
        for i in range(5):
            ledger.observe(make_market(slug='s%d' % i), WINDOW, DURATION)
        summary = ledger.sweep(now=WINDOW + DURATION + 1)
        assert summary['due'] == 5
        assert summary['lookups'] == 2
        assert summary['deferred'] == 3
        # Deferred, never dropped: all five are still pending.
        assert ledger.stats()['pending'] == 5


# ---------------------------------------------------------------------------
# 3. The read path never guesses (rule 5)
# ---------------------------------------------------------------------------

class TestReadPath(object):

    def test_absent_market_is_none_not_zero(self, conn):
        assert resolution_for(conn, 'no-such-market', 'Up') is None

    def test_absent_side_is_none_not_zero(self, conn):
        write_resolutions(conn, [(SLUG, 'Up', 1.0, WINDOW)], SOURCE_VENUE)
        assert resolution_for(conn, SLUG, 'Sideways') is None

    def test_none_is_distinguishable_from_a_real_zero(self, conn):
        write_resolutions(conn, [(SLUG, 'Down', 0.0, WINDOW)], SOURCE_VENUE)
        assert resolution_for(conn, SLUG, 'Down') == 0.0
        assert resolution_for(conn, SLUG, 'Up') is None

    def test_a_null_resolved_px_reads_as_unknown_not_as_a_loss(self, conn):
        write_resolutions(conn, [(SLUG, 'Up', None, WINDOW)], SOURCE_VENUE)
        assert resolution_for(conn, SLUG, 'Up') is None

    def test_empty_arguments_are_none(self, conn):
        assert resolution_for(conn, None, 'Up') is None
        assert resolution_for(conn, SLUG, None) is None
        assert resolution_for(conn, '', '') is None

    def test_outcome_side_matches_case_insensitively(self, conn):
        write_resolutions(conn, [(SLUG, 'Up', 1.0, WINDOW)], SOURCE_VENUE)
        for spelling in ('Up', 'up', 'UP'):
            assert resolution_for(conn, SLUG, spelling) == 1.0

    def test_the_row_helper_carries_the_source(self, conn):
        write_resolutions(conn, [(SLUG, 'Up', 1.0, WINDOW)], SOURCE_VENUE)
        row = resolution_row_for(conn, SLUG, 'Up')
        assert row['source'] == SOURCE_VENUE
        assert row['window_ts'] == WINDOW
        assert row['resolved_px'] == 1.0


# ---------------------------------------------------------------------------
# 4. Sources are a closed vocabulary and are never pooled (rules 3 and 4)
# ---------------------------------------------------------------------------

class TestSources(object):

    def test_the_vocabulary_is_exactly_three_values(self):
        assert RESOLUTION_SOURCES == (
            'venue', 'inferred_terminal_price', 'sibling_inference_backfill')

    def test_backfill_is_not_a_live_source(self):
        assert SOURCE_SIBLING_INFERENCE_BACKFILL not in LIVE_SOURCES
        assert SOURCE_SIBLING_INFERENCE_BACKFILL in BACKFILL_SOURCES
        assert SOURCE_VENUE in LIVE_SOURCES

    def test_an_unknown_source_raises_rather_than_opening_a_bucket(self, conn):
        with pytest.raises(ValueError) as exc:
            write_resolutions(conn, [(SLUG, 'Up', 1.0, WINDOW)], 'guessed')
        assert 'guessed' in str(exc.value)
        assert conn.execute('SELECT COUNT(*) FROM market_resolutions'
                            ).fetchone()[0] == 0

    def test_a_reader_can_exclude_backfill(self, conn):
        write_resolutions(conn, [(SLUG, 'Up', 1.0, WINDOW)],
                          SOURCE_SIBLING_INFERENCE_BACKFILL)
        # Present when unrestricted...
        assert resolution_for(conn, SLUG, 'Up') == 1.0
        # ...and invisible to the coverage number, which is measured on
        # markets FETCHED after the ledger landed (rule 4).
        assert resolution_for(conn, SLUG, 'Up', LIVE_SOURCES) is None

    def test_backfill_rows_are_marked_on_disk(self, conn):
        write_resolutions(conn, [(SLUG, 'Up', 1.0, WINDOW)],
                          SOURCE_SIBLING_INFERENCE_BACKFILL)
        source = conn.execute(
            'SELECT source FROM market_resolutions').fetchone()[0]
        assert source == SOURCE_SIBLING_INFERENCE_BACKFILL

    def test_the_two_sources_stay_separable_in_one_table(self, conn):
        write_resolutions(conn, [(SLUG, 'Up', 1.0, WINDOW)], SOURCE_VENUE)
        write_resolutions(conn, [('older-market', 'Down', 0.0, None)],
                          SOURCE_SIBLING_INFERENCE_BACKFILL)
        by_source = dict(conn.execute(
            'SELECT source, COUNT(*) FROM market_resolutions '
            'GROUP BY source').fetchall())
        expected = dict()
        expected[SOURCE_VENUE] = 1
        expected[SOURCE_SIBLING_INFERENCE_BACKFILL] = 1
        assert by_source == expected


# ---------------------------------------------------------------------------
# 5. A gap is a counted number with a reason (rule 7, convention 20)
# ---------------------------------------------------------------------------

class TestUnresolvedAccounting(object):

    def ledger(self, conn, cache, **kw):
        kw.setdefault('grace_sec', 0.0)
        return ResolutionLedger(conn=conn, cache=cache, **kw)

    def test_a_market_that_never_resolves_is_abandoned_and_counted(self, conn):
        ledger = self.ledger(conn, StubCache(answer=unresolved()),
                             max_pending_sec=100.0)
        ledger.observe(make_market(), WINDOW, DURATION)
        # Inside the pending window: chased, not abandoned.
        ledger.sweep(now=WINDOW + DURATION + 50)
        assert ledger.stats()['pending'] == 1
        assert ledger.stats()['unresolved_total'] == 0
        # Past it: abandoned, with the venue's own last status as the reason.
        summary = ledger.sweep(now=WINDOW + DURATION + 200)
        assert summary['abandoned'] == 1
        stats = ledger.stats()
        assert stats['pending'] == 0
        assert stats['unresolved_total'] == 1
        expected = dict()
        expected[STATUS_NOT_CLOSED] = 1
        assert stats['unresolved_by_window'][str(WINDOW)] == expected

    def test_an_abandoned_market_writes_no_row_at_zero(self, conn):
        ledger = self.ledger(conn, StubCache(answer=unresolved()),
                             max_pending_sec=0.0)
        ledger.observe(make_market(), WINDOW, DURATION)
        ledger.sweep(now=WINDOW + DURATION + 10)
        assert conn.execute('SELECT COUNT(*) FROM market_resolutions'
                            ).fetchone()[0] == 0

    def test_a_market_with_no_condition_id_is_named_not_dropped(self, conn):
        ledger = self.ledger(conn, StubCache())
        assert ledger.observe(make_market(condition_id=''), WINDOW,
                              DURATION) is False
        stats = ledger.stats()
        assert stats['pending'] == 0
        assert stats['unresolved_by_window'][str(WINDOW)] == dict(
            no_condition_id=1)

    def test_a_resolved_market_whose_token_is_absent_is_abandoned(self, conn):
        # A resolved market that does not contain our token id is a KEYING
        # fault. Retrying will not fix it and 0.00 is not the answer.
        answer = MarketResolution(
            condition_id=COND, closed=True, resolved=True,
            status=STATUS_RESOLVED,
            winning_token_ids=frozenset(['someone-elses-token']),
            losing_token_ids=frozenset(['another-one']),
            market_slug=SLUG)
        ledger = self.ledger(conn, StubCache(answer=answer))
        ledger.observe(make_market(), WINDOW, DURATION)
        summary = ledger.sweep(now=WINDOW + DURATION + 1)
        assert summary['abandoned'] == 1
        assert conn.execute('SELECT COUNT(*) FROM market_resolutions'
                            ).fetchone()[0] == 0
        assert ledger.stats()['unresolved_by_window'][str(WINDOW)] == dict(
            token_not_in_resolved_market=1)

    def test_reasons_are_counted_per_window_not_pooled(self, conn):
        ledger = self.ledger(conn, StubCache(), max_pending_sec=0.0)
        ledger.observe(make_market(slug='a', condition_id=''), 100, DURATION)
        ledger.observe(make_market(slug='b', condition_id=''), 200, DURATION)
        ledger.observe(make_market(slug='c', condition_id=''), 200, DURATION)
        stats = ledger.stats()
        expected = dict()
        expected['100'] = dict(no_condition_id=1)
        expected['200'] = dict(no_condition_id=2)
        assert stats['unresolved_by_window'] == expected
        assert stats['unresolved_total'] == 3

    def test_a_raising_cache_does_not_take_the_loop_with_it(self, conn):
        class Boom(object):
            def get(self, cid):
                raise RuntimeError('venue on fire')

            def __len__(self):
                return 0

        ledger = self.ledger(conn, Boom())
        ledger.observe(make_market(), WINDOW, DURATION)
        summary = ledger.sweep(now=WINDOW + DURATION + 1)
        assert summary['resolved'] == 0
        assert ledger.stats()['health']['sweep_raised'] == 1

    def test_observe_never_raises_on_a_junk_market(self, conn):
        ledger = self.ledger(conn, StubCache())
        assert ledger.observe(None, WINDOW, DURATION) is False
        assert ledger.observe(object(), WINDOW, DURATION) is False


# ---------------------------------------------------------------------------
# 6. Coverage and agreement, the acceptance test (backtest/settlement_coverage)
# ---------------------------------------------------------------------------

POSITIONS_DDL = (
    'CREATE TABLE positions (id TEXT, pair TEXT, signal_id TEXT, '
    'opened_ts INTEGER, closed_ts INTEGER, exit_px REAL, exit_reason TEXT)')
SIGNALS_DDL = 'CREATE TABLE signals (id TEXT, features_json TEXT)'


def make_history(rows):
    """A tiny db carrying `positions` joined to `signals`, plus the ledger.

    `rows` are `(pair, side, exit_px, exit_reason, opened_ts_ms)`. Built by
    hand rather than copied from db/schema.sql because these tests are about
    the JOIN and the settlement keying, not about the full schema.
    """
    conn = sqlite3.connect(':memory:')
    conn.execute(POSITIONS_DDL)
    conn.execute(SIGNALS_DDL)
    for i, (pair, side, exit_px, reason, opened) in enumerate(rows):
        sid = 'sig-%d' % i
        features = 'null' if side is None else json.dumps(
            dict(outcome_side=side))
        conn.execute('INSERT INTO signals VALUES (?, ?)', (sid, features))
        conn.execute(
            'INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('pos-%d' % i, pair, sid, opened, opened + 1, exit_px, reason))
    conn.commit()
    ensure_schema(conn)
    return conn


class TestSiblingInference(object):
    """The old, biased method - reproduced so it can be checked AGAINST."""

    def test_settlement_is_recovered_and_a_sale_is_not(self):
        conn = make_history([
            ('m1', 'Up', 1.0, 'target', 1000),
            ('m2', 'Up', 1.0, 'sell:profit_target', 1000),
        ])
        resolved, report = SC.sibling_inference_map(conn)
        # m1 was held to settlement; m2 was SOLD at 1.00, which is not a
        # resolution. Settlement is the BARE reason; everything sold is
        # prefixed 'sell:'.
        assert ('m1', 'Up') in resolved
        assert ('m2', 'Up') not in resolved
        # Both still count in the DENOMINATOR: they were touched.
        assert report['touched_count'] == 2
        assert report['recoverable'] == 1

    def test_a_position_with_no_outcome_side_is_counted_not_dropped(self):
        conn = make_history([
            ('m1', 'Up', 0.0, 'stop', 1000),
            ('m2', None, 0.0, 'stop', 1000),
        ])
        _resolved, report = SC.sibling_inference_map(conn)
        assert report['no_outcome_side'] == 1
        assert report['touched_count'] == 1

    def test_a_market_side_at_both_prices_is_contradictory_not_guessed(self):
        conn = make_history([
            ('m1', 'Up', 1.0, 'target', 1000),
            ('m1', 'Up', 0.0, 'stop', 1000),
        ])
        resolved, report = SC.sibling_inference_map(conn)
        assert ('m1', 'Up') not in resolved
        assert report['contradictory'] == [
            dict(market_slug='m1', outcome_side='Up', prices=[0.0, 1.0])]

    def test_since_ms_filters_on_milliseconds(self):
        conn = make_history([
            ('m1', 'Up', 1.0, 'target', 1000),
            ('m2', 'Up', 1.0, 'target', 9000),
        ])
        _r, report = SC.sibling_inference_map(conn, since_ms=5000)
        assert report['touched_count'] == 1


class TestDisagreementDetection(object):
    """038's second kill clause: the overlap must agree exactly."""

    def test_agreement_on_the_overlap_passes(self):
        conn = make_history([('m1', 'Up', 1.0, 'target', 1000)])
        write_resolutions(conn, [('m1', 'Up', 1.0, 100)], SOURCE_VENUE)
        result = SC.disagreements(conn)
        assert result['overlap'] == 1
        assert result['disagreement_count'] == 0
        assert result['rate'] == 0.0
        assert result['verdict'] == 'PASS'

    def test_a_single_disagreement_fails_the_repair(self):
        conn = make_history([('m1', 'Up', 1.0, 'target', 1000)])
        # The ledger says the side LOST; the sibling inference says it won.
        write_resolutions(conn, [('m1', 'Up', 0.0, 100)], SOURCE_VENUE)
        result = SC.disagreements(conn)
        assert result['overlap'] == 1
        assert result['disagreement_count'] == 1
        assert result['rate'] == 1.0
        assert result['verdict'] == 'FAILED'
        bad = result['disagreements'][0]
        assert bad['ledger_px'] == 0.0
        assert bad['inferred_px'] == 1.0
        assert bad['ledger_source'] == SOURCE_VENUE

    def test_a_contradictory_side_is_reported_but_is_not_a_disagreement(self):
        conn = make_history([
            ('m1', 'Up', 1.0, 'target', 1000),
            ('m1', 'Up', 0.0, 'stop', 1000),
        ])
        write_resolutions(conn, [('m1', 'Up', 1.0, 100)], SOURCE_VENUE)
        result = SC.disagreements(conn)
        # A known-bad input must not be able to manufacture a failure.
        assert result['disagreement_count'] == 0
        assert len(result['contradictory_inference']) == 1

    def test_no_overlap_is_not_tested_rather_than_a_pass(self):
        conn = make_history([('m1', 'Up', 1.0, 'target', 1000)])
        result = SC.disagreements(conn)
        assert result['overlap'] == 0
        assert result['verdict'] == 'NOT_TESTED'

    def test_backfill_rows_are_not_the_check(self):
        # A backfilled row IS the sibling inference. Checking it against
        # itself would agree by construction and measure nothing, so the
        # default source filter excludes it from the overlap entirely.
        conn = make_history([('m1', 'Up', 1.0, 'target', 1000)])
        write_resolutions(conn, [('m1', 'Up', 1.0, 100)],
                          SOURCE_SIBLING_INFERENCE_BACKFILL)
        assert SC.disagreements(conn)['overlap'] == 0


class TestCoverage(object):
    """The rule-2 number, reported with BOTH numerator and denominator."""

    def test_an_absent_table_is_not_tested_rather_than_zero(self):
        conn = sqlite3.connect(':memory:')
        conn.execute(POSITIONS_DDL)
        conn.execute(SIGNALS_DDL)
        result = SC.coverage(conn)
        assert result['verdict'] == 'NOT_TESTED'
        assert result['ledger_table_present'] is False
        assert result['numerator'] is None

    def test_the_fraction_carries_both_halves(self):
        conn = make_history([
            ('m1', 'Up', 1.0, 'target', 1000),
            ('m2', 'Down', 0.0, 'stop', 1000),
        ])
        write_resolutions(conn, [('m1', 'Up', 1.0, 100)], SOURCE_VENUE)
        result = SC.coverage(conn)
        assert result['numerator'] == 1
        assert result['denominator'] == 2
        assert result['fraction'] == 0.5
        assert result['missing_total'] == 1
        assert result['missing_sample'][0]['market_slug'] == 'm2'

    def test_backfill_does_not_count_toward_coverage(self):
        conn = make_history([('m1', 'Up', 1.0, 'target', 1000)])
        write_resolutions(conn, [('m1', 'Up', 1.0, 100)],
                          SOURCE_SIBLING_INFERENCE_BACKFILL)
        assert SC.coverage(conn)['numerator'] == 0
        # ...but it IS visible when the caller asks for every source.
        assert SC.coverage(conn, sources=None)['numerator'] == 1

    def test_a_thin_sample_is_not_tested_not_graded(self):
        # 038: do not grade a partial sample. Under 200 closed positions the
        # verdict is NOT_TESTED even at perfect coverage.
        conn = make_history([('m1', 'Up', 1.0, 'target', 1000)])
        write_resolutions(conn, [('m1', 'Up', 1.0, 100)], SOURCE_VENUE)
        result = SC.coverage(conn)
        assert result['fraction'] == 1.0
        assert result['verdict'] == 'NOT_TESTED'

    def test_full_coverage_on_a_large_enough_sample_passes(self):
        rows = [('m%d' % i, 'Up', 1.0, 'target', 1000) for i in range(200)]
        conn = make_history(rows)
        write_resolutions(conn, [('m%d' % i, 'Up', 1.0, 100)
                                 for i in range(200)], SOURCE_VENUE)
        result = SC.coverage(conn)
        assert result['numerator'] == 200
        assert result['denominator'] == 200
        assert result['verdict'] == 'PASS'

    def test_low_coverage_is_failed_not_tuned(self):
        rows = [('m%d' % i, 'Up', 1.0, 'target', 1000) for i in range(200)]
        conn = make_history(rows)
        write_resolutions(conn, [('m%d' % i, 'Up', 1.0, 100)
                                 for i in range(100)], SOURCE_VENUE)
        result = SC.coverage(conn)
        assert result['fraction'] == 0.5
        assert result['verdict'] == 'FAILED'


class TestBackfill(object):
    """Permitted, marked, and never silently destructive (rule 4)."""

    def test_dry_run_writes_nothing(self):
        conn = make_history([('m1', 'Up', 1.0, 'target', 1000)])
        result = SC.backfill(conn, dry_run=True)
        assert result['candidates'] == 1
        assert result['written'] == 0
        assert conn.execute('SELECT COUNT(*) FROM market_resolutions'
                            ).fetchone()[0] == 0

    def test_a_real_backfill_is_marked(self):
        conn = make_history([('m1', 'Up', 1.0, 'target', 1000)])
        result = SC.backfill(conn, dry_run=False)
        assert result['written'] == 1
        row = resolution_row_for(conn, 'm1', 'Up')
        assert row['source'] == SOURCE_SIBLING_INFERENCE_BACKFILL

    def test_backfill_never_overwrites_a_venue_row(self):
        conn = make_history([('m1', 'Up', 1.0, 'target', 1000)])
        write_resolutions(conn, [('m1', 'Up', 0.0, 100)], SOURCE_VENUE)
        SC.backfill(conn, dry_run=False)
        row = resolution_row_for(conn, 'm1', 'Up')
        assert row['source'] == SOURCE_VENUE
        assert row['resolved_px'] == 0.0

    def test_contradictory_sides_are_skipped_and_counted(self):
        conn = make_history([
            ('m1', 'Up', 1.0, 'target', 1000),
            ('m1', 'Up', 0.0, 'stop', 1000),
        ])
        result = SC.backfill(conn, dry_run=False)
        assert result['contradictory_skipped'] == 1
        assert result['written'] == 0
