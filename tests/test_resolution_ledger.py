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
import math
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.market_resolution import (  # noqa: E402
    MarketResolution, STATUS_NOT_CLOSED, STATUS_RESOLVED)
from engine.polymarket.resolution_ledger import (  # noqa: E402
    BACKFILL_SOURCES, COUNTERFACTUAL_GRADED_SOURCES, LIVE_SOURCES,
    RESOLUTION_SOURCES, SOURCE_INFERRED_TERMINAL_PRICE,
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


# ---------------------------------------------------------------------------
# 7. The counterfactual: what did the exit take, and what was it worth?
#    (Forge proposal 043, backtest/settlement_coverage.py --counterfactual)
# ---------------------------------------------------------------------------

#: `positions` as the counterfactual needs it - the coverage tests above never
#: look at money, so their DDL carries no `qty` and no `entry_px`. Kept as a
#: SECOND table definition rather than widening theirs, so a change here can
#: never silently move a coverage number.
BOOK_POSITIONS_DDL = (
    'CREATE TABLE positions (id TEXT, pair TEXT, signal_id TEXT, '
    'opened_ts INTEGER, closed_ts INTEGER, entry_px REAL, exit_px REAL, '
    'qty REAL, pnl_net REAL, exit_reason TEXT)')


def make_book(rows, resolutions=(), source=SOURCE_VENUE):
    """A book with share counts, joined to a ledger.

    `rows` are `(pair, side, exit_px, exit_reason, qty, opened_ts_ms)` and
    `resolutions` are `(market_slug, outcome_side, resolved_px, window_ts)`.

    The side is written into `signals.features_json` under `outcome_side` and
    NOWHERE else, because that is the only place the real database carries it -
    there is no `market_slug` key in `features_json` and a join that invents
    one matches nothing.
    """
    conn = sqlite3.connect(':memory:')
    conn.execute(BOOK_POSITIONS_DDL)
    conn.execute(SIGNALS_DDL)
    for i, (pair, side, exit_px, reason, qty, opened) in enumerate(rows):
        sid = 'book-sig-%d' % i
        features = 'null' if side is None else json.dumps(
            dict(outcome_side=side))
        conn.execute('INSERT INTO signals VALUES (?, ?)', (sid, features))
        conn.execute(
            'INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            ('book-pos-%d' % i, pair, sid, opened, opened + 1, 0.5, exit_px,
             qty, 0.0, reason))
    conn.commit()
    ensure_schema(conn)
    if resolutions:
        write_resolutions(conn, list(resolutions), source)
    return conn


def row_for(result, exit_reason):
    """The one per-exit-reason row, or a failure that names what is there."""
    for row in result['by_exit_reason']:
        if row['exit_reason'] == exit_reason:
            return row
    raise AssertionError('no row for %r; got %r' % (
        exit_reason, [r['exit_reason'] for r in result['by_exit_reason']]))


class TestCounterfactualJoin(object):
    """Rule 2: BOTH halves of the key, or half the book scores wrong."""

    def test_both_halves_of_the_key_are_required(self):
        # One market, two sides, opposite resolutions. A join on `pair` alone
        # would score the Down position against the Up outcome and would do it
        # silently - it is the same slug, so the row is THERE.
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000),
             ('m1', 'Down', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m1', 'Up', 0.0, 100), ('m1', 'Down', 1.0, 100)])
        row = row_for(SC.counterfactual(conn), 'sell:salvage_floor')
        assert row['matched'] == 2
        assert row['shares'] == 20.0
        # Exactly one side of a binary wins, so realised value is 10 shares.
        # A slug-only join would report 0 or 20 depending on which row it hit.
        assert row['realised_value_usd'] == 10.0
        assert row['proceeds_usd'] == pytest.approx(1.0)

    def test_the_side_is_matched_case_insensitively(self):
        # `signals.features_json` carries Up/Down capitalised; the ledger
        # stores whatever casing the venue used. A join that fails on case
        # looks exactly like a market that was never recorded.
        conn = make_book(
            [('m1', 'up', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m1', 'Up', 1.0, 100)])
        assert row_for(SC.counterfactual(conn),
                       'sell:salvage_floor')['matched'] == 1

    def test_a_position_with_no_outcome_side_is_named_not_matched(self):
        conn = make_book(
            [('m1', None, 0.05, 'sell:salvage_floor', 10.0, 1000),
             ('m2', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m1', 'Up', 1.0, 100), ('m2', 'Up', 1.0, 100)])
        result = SC.counterfactual(conn)
        assert result['no_outcome_side'] == 1
        row = row_for(result, 'sell:salvage_floor')
        assert row['matched'] == 1
        assert row['no_outcome_side'] == 1

    def test_a_null_resolved_px_is_unmatched_not_a_loss(self):
        # NULL means NOT RECORDED. Scoring it as 0.00 would convert every
        # unrecorded market into a realised loss and would push the
        # counterfactual the same way the old sibling inference already leans.
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000),
             ('m2', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m1', 'Up', None, 100), ('m2', 'Up', 1.0, 100)])
        row = row_for(SC.counterfactual(conn), 'sell:salvage_floor')
        assert row['matched'] == 1
        assert row['realised_value_usd'] == 10.0

    def test_since_filters_on_milliseconds(self):
        # `positions.opened_ts` is MILLISECONDS and the ledger is SECONDS.
        # Mixing them is a factor-of-1000 error that empties the result and
        # looks like a market that was never traded.
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000),
             ('m2', 'Up', 0.05, 'sell:salvage_floor', 10.0, 9000)],
            [('m1', 'Up', 0.0, 100), ('m2', 'Up', 0.0, 100)])
        row = row_for(SC.counterfactual(conn, since_ms=5000),
                      'sell:salvage_floor')
        assert row['matched'] == 1
        assert row['match_rate_denominator'] == 1


class TestCounterfactualFailsLoudly(object):
    """Rule 3: a silent zero is a missing number (convention 20)."""

    def test_a_ledger_with_rows_that_match_nothing_raises(self):
        # The first attempt at this join keyed on a `market_slug` field in
        # `features_json` that does not exist, and returned 0 of 195 while
        # reporting itself fine. A populated ledger plus zero matches is a
        # KEYING fault and must never be printed as an empty counterfactual.
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m2', 'Up', 1.0, 100)])
        with pytest.raises(SC.ZeroMatchError) as exc:
            SC.counterfactual(conn)
        assert 'outcome_side' in str(exc.value)

    def test_an_absent_ledger_table_is_not_tested_not_a_failure(self):
        conn = sqlite3.connect(':memory:')
        conn.execute(BOOK_POSITIONS_DDL)
        conn.execute(SIGNALS_DDL)
        result = SC.counterfactual(conn)
        assert result['status'] == 'NOT_TESTED'
        assert result['ledger_table_present'] is False
        assert result['by_exit_reason'] == []

    def test_an_empty_ledger_is_not_tested_not_a_keying_fault(self):
        # Present but holding nothing from the requested sources: there is no
        # join to get wrong yet. NOT_TESTED means could not run, never ran and
        # found nothing (convention 11), and this is the could-not-run case.
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)], [])
        result = SC.counterfactual(conn)
        assert result['status'] == 'NOT_TESTED'
        assert result['ledger_table_present'] is True
        assert result['ledger_rows'] == 0

    def test_no_keyed_position_is_not_tested_rather_than_a_failure(self):
        conn = make_book(
            [('m1', None, 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m1', 'Up', 1.0, 100)])
        result = SC.counterfactual(conn)
        assert result['status'] == 'NOT_TESTED'
        assert result['no_outcome_side'] == 1

    def test_backfill_rows_are_not_the_venue_arm(self):
        # A backfilled row IS the sibling inference, which is biased toward
        # losers by construction. It is a SEPARATE arm and never merges.
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m1', 'Up', 0.0, 100)],
            source=SOURCE_SIBLING_INFERENCE_BACKFILL)
        result = SC.counterfactual(conn)
        assert result['status'] == 'NOT_TESTED'
        # ...and visible only when the caller asks for that arm by name.
        row = row_for(SC.counterfactual(conn, sources=BACKFILL_SOURCES),
                      'sell:salvage_floor')
        assert row['matched'] == 1


class TestCounterfactualNeverPools(object):
    """Rule 4: per exit reason, never across. Rule 10: never across DBs."""

    def test_two_exit_reasons_stay_two_rows_with_opposite_signs(self):
        conn = make_book([
            ('m1', 'Up', 0.90, 'sell:profit_target', 10.0, 1000),
            ('m2', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000),
        ], [('m1', 'Up', 0.0, 100), ('m2', 'Up', 1.0, 100)])
        result = SC.counterfactual(conn)
        assert sorted(r['exit_reason'] for r in result['by_exit_reason']) == [
            'sell:profit_target', 'sell:salvage_floor']
        assert row_for(result, 'sell:profit_target')[
            'delta_per_share'] == pytest.approx(0.90)
        assert row_for(result, 'sell:salvage_floor')[
            'delta_per_share'] == pytest.approx(-0.95)
        # Pooling these two would report a delta near zero on 20 shares and
        # would hide both. There is no top-level money figure to pool INTO.
        for key in ('shares', 'proceeds_usd', 'realised_value_usd',
                    'delta_usd', 'delta_per_share', 'mean_exit_px'):
            assert key not in result

    def test_the_match_rate_carries_both_halves_per_exit_reason(self):
        conn = make_book([
            ('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000),
            ('m2', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000),
            ('m3', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000),
            ('m4', 'Up', 0.90, 'sell:profit_target', 10.0, 1000),
        ], [('m1', 'Up', 1.0, 100), ('m4', 'Up', 1.0, 100)])
        result = SC.counterfactual(conn)
        salvage = row_for(result, 'sell:salvage_floor')
        assert salvage['match_rate_numerator'] == 1
        assert salvage['match_rate_denominator'] == 3
        assert salvage['match_rate'] == pytest.approx(1.0 / 3.0)
        target = row_for(result, 'sell:profit_target')
        assert target['match_rate_numerator'] == 1
        assert target['match_rate_denominator'] == 1

    def test_two_databases_are_two_arms(self):
        # Environment B trades the same markets and has its own ledger or
        # none. Pooling its salvage rows against environment A's ledger would
        # join successfully and mean nothing.
        env_a = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m1', 'Up', 0.0, 100)])
        env_b = sqlite3.connect(':memory:')
        env_b.execute(BOOK_POSITIONS_DDL)
        env_b.execute(SIGNALS_DDL)
        assert row_for(SC.counterfactual(env_a),
                       'sell:salvage_floor')['matched'] == 1
        b = SC.counterfactual(env_b)
        assert b['status'] == 'NOT_TESTED'
        assert b['by_exit_reason'] == []


class TestCounterfactualSelfCheck(object):
    """Rule 6: the instrument checks itself on EVERY invocation.

    Positions whose `exit_px` is exactly 0.00 or 1.00 settled independently of
    the ledger - the paper adapter set that price from Gamma's `outcomePrices`
    while the ledger reads the CLOB's `winner` field. Different endpoint,
    different field, different failure modes, so where they overlap they are a
    real check and not a tautology.
    """

    def test_the_documented_baseline_reproduces(self):
        # 2016 settled shares, 25 disagreeing, 1.24%, split 15 against 10 -
        # the snapshot figure proposal 043 built its 0.010 kill band on.
        conn = make_book([
            ('m1', 'Up', 1.0, 'target', 15.0, 1000),
            ('m2', 'Up', 0.0, 'stop', 10.0, 1000),
            ('m3', 'Up', 1.0, 'target', 1991.0, 1000),
        ], [('m1', 'Up', 0.0, 100), ('m2', 'Up', 1.0, 100),
            ('m3', 'Up', 1.0, 100)])
        check = SC.counterfactual(conn)['self_check']
        assert check['positions'] == 3
        assert check['shares'] == 2016.0
        assert check['disagreeing_shares'] == 25.0
        assert check['rate'] == pytest.approx(0.0124, abs=5e-5)
        assert check['ledger_loss_position_settled_win_shares'] == 15.0
        assert check['ledger_win_position_settled_loss_shares'] == 10.0
        assert check['net_directional_bias'] == pytest.approx(5.0 / 2016.0)
        assert check['verdict'] == 'PASS'

    def test_the_strict_subset_keys_on_the_settlement_reason_too(self):
        # Rule 6 as written keys on the PRICE alone, and a sale CAN land
        # exactly at 0.00. The strict arm additionally requires the bare
        # settlement reason, which is the only thing that tells the two apart.
        conn = make_book([
            ('m1', 'Up', 0.0, 'sell:price_stop', 10.0, 1000),
            ('m2', 'Up', 0.0, 'stop', 10.0, 1000),
        ], [('m1', 'Up', 1.0, 100), ('m2', 'Up', 0.0, 100)])
        check = SC.counterfactual(conn)['self_check']
        assert check['positions'] == 2
        assert check['disagreeing_shares'] == 10.0
        assert check['strict']['positions'] == 1
        assert check['strict']['disagreeing_shares'] == 0.0

    def test_no_settled_overlap_is_not_tested_rather_than_a_clean_bill(self):
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m1', 'Up', 0.0, 100)])
        check = SC.counterfactual(conn)['self_check']
        assert check['positions'] == 0
        assert check['rate'] is None
        assert check['verdict'] == 'NOT_TESTED'


class TestCounterfactualKillVerdict(object):
    """Graded on sell:salvage_floor only, at 400, inside a 0.010 band."""

    def salvage_book(self, n, exit_px, settle_every=None):
        rows = [('s%d' % i, 'Up', exit_px, 'sell:salvage_floor', 10.0, 1000)
                for i in range(n)]
        resolutions = [
            ('s%d' % i, 'Up',
             1.0 if (settle_every and i % settle_every == 0) else 0.0, 100)
            for i in range(n)]
        return make_book(rows, resolutions)

    def test_below_four_hundred_matched_is_not_tested(self):
        verdict = SC.counterfactual(self.salvage_book(399, 0.05))['verdict']
        assert SC.KILL_MIN_MATCHED == 400
        assert verdict['matched'] == 399
        assert verdict['required_matched'] == 400
        assert verdict['verdict'] == 'NOT_TESTED'

    # The three sizes below are set so the delta clears BOTH the 0.010 band
    # and the 3-sigma gate D-356 R2 added. They were 400 positions each until
    # 046 showed that at 400 clusters the band is under one sigma wide, so a
    # verdict there was noise: the sizes moved, the thresholds did NOT
    # (046 rule 4). The gate's own blocking behaviour is pinned in
    # TestTheThreeSigmaGate, including the cases these used to assert.

    def test_salvage_beating_hold_by_the_band_confirms_the_floor(self):
        # 500 clusters at p=0.02: sigma 0.0063, 3 sigma 0.0188 < 0.03.
        verdict = SC.counterfactual(
            self.salvage_book(500, 0.05, settle_every=50))['verdict']
        assert verdict['realised_settle_rate'] == pytest.approx(0.02)
        assert verdict['mean_exit_px'] == pytest.approx(0.05)
        assert verdict['margin_sigma'] > 3.0
        assert verdict['verdict'] == 'CONFIRMED'

    def test_hold_beating_salvage_by_the_band_is_negative(self):
        # 5% of salvaged shares settle at 1.00 against a 0.02 exit price.
        # 600 clusters at p=0.05: sigma 0.0089, 3 sigma 0.0267 < 0.03.
        verdict = SC.counterfactual(
            self.salvage_book(600, 0.02, settle_every=20))['verdict']
        assert verdict['realised_settle_rate'] == pytest.approx(0.05)
        assert verdict['margin_sigma'] > 3.0
        assert verdict['verdict'] == 'NEGATIVE'

    def test_inside_the_band_is_inconclusive_not_a_verdict(self):
        # A margin INSIDE the band must still clear the gate to be called
        # anything, so this needs a sigma well under the band: 3000 clusters
        # at p=0.0033 give 0.0011, 3 sigma 0.0032, against a 0.0067 margin.
        verdict = SC.counterfactual(
            self.salvage_book(3000, 0.010, settle_every=300))['verdict']
        assert abs(verdict['margin']) < SC.KILL_BAND
        assert verdict['margin_sigma'] > 3.0
        assert verdict['verdict'] == 'INCONCLUSIVE'

    def test_nothing_but_the_salvage_floor_is_graded(self):
        conn = make_book(
            [('m%d' % i, 'Up', 0.9, 'sell:profit_target', 10.0, 1000)
             for i in range(400)],
            [('m%d' % i, 'Up', 0.0, 100) for i in range(400)])
        result = SC.counterfactual(conn)
        assert row_for(result, 'sell:profit_target')['gradeable'] is False
        assert result['verdict']['verdict'] == 'NOT_TESTED'

    def test_a_self_check_above_the_ceiling_forces_not_tested(self):
        # The salvage arm below would CONFIRM on its own numbers. It must not,
        # because at this disagreement rate the instrument is measuring itself.
        rows = [('s%d' % i, 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)
                for i in range(400)]
        resolutions = [('s%d' % i, 'Up', 0.0, 100) for i in range(400)]
        rows.append(('wrong', 'Up', 1.0, 'target', 10.0, 1000))
        rows.append(('right', 'Up', 1.0, 'target', 90.0, 1000))
        resolutions.append(('wrong', 'Up', 0.0, 100))
        resolutions.append(('right', 'Up', 1.0, 100))
        result = SC.counterfactual(make_book(rows, resolutions))
        assert result['self_check']['rate'] == pytest.approx(0.10)
        assert result['self_check']['verdict'] == 'FAILED'
        assert result['verdict']['verdict'] == 'NOT_TESTED'
        assert 'self-check' in result['verdict']['reason']


class TestCounterfactualReportsWithoutGrading(object):
    """Rule 9: report the thin exits, and refuse to call them evidence."""

    def test_thin_exits_are_reported_and_marked_not_gradeable(self):
        # `sell:time_stop` is the row proposal 039 cares about and at the 043
        # snapshot it was ONE position: 20 shares sold for 3.18 that were
        # worth 0.00, opposite to 039's sibling-inferred +0.184 per share.
        # One position is not evidence and must not be reported as if it were.
        conn = make_book([
            ('m1', 'Up', 0.86, 'sell:mean_reverted', 18.0, 1000),
            ('m2', 'Up', 0.159, 'sell:time_stop', 20.0, 1000),
        ], [('m1', 'Up', 1.0, 100), ('m2', 'Up', 0.0, 100)])
        result = SC.counterfactual(conn)
        for reason in ('sell:mean_reverted', 'sell:time_stop'):
            row = row_for(result, reason)
            assert row['matched'] == 1
            assert row['gradeable'] is False
            assert row['not_gradeable_reason']

    def test_settlement_reasons_are_marked_degenerate_not_early_exits(self):
        # `stop` and `target` are settlement, not a sale: `exit_px` IS the
        # resolution price, so their delta measures ledger disagreement and
        # nothing about exit policy at all.
        conn = make_book([('m1', 'Up', 1.0, 'target', 10.0, 1000)],
                         [('m1', 'Up', 1.0, 100)])
        row = row_for(SC.counterfactual(conn), 'target')
        assert row['early_exit'] is False
        assert row['gradeable'] is False
        assert row['delta_usd'] == 0.0

    def test_the_break_even_is_stated_as_an_identity(self):
        # Rule 7 and D-342 R5: the payoff difference is an identity in two
        # RECORDED prices. No probability model, no fair value, no
        # calibration - a forecaster here would make the whole thing
        # inadmissible on the same grounds the Kalman spread was refused.
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000),
             ('m2', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m1', 'Up', 1.0, 100), ('m2', 'Up', 0.0, 100)])
        result = SC.counterfactual(conn)
        assert result['break_even']['identity']
        row = row_for(result, 'sell:salvage_floor')
        assert row['mean_exit_px'] == pytest.approx(0.05)
        assert row['realised_settle_rate'] == pytest.approx(0.5)
        assert row['delta_per_share'] == pytest.approx(
            row['mean_exit_px'] - row['realised_settle_rate'])
        assert row['delta_usd'] == pytest.approx(
            row['proceeds_usd'] - row['realised_value_usd'])


class TestCounterfactualIsAReadNotAWrite(object):
    """Rule 1: a reporting run that can write its own inputs is not a
    measurement. --backfill WRITES market_resolutions; --counterfactual READS
    it, and the two do not share a code path."""

    def test_it_writes_nothing(self):
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000),
             ('m2', 'Up', 1.0, 'target', 10.0, 1000)],
            [('m1', 'Up', 0.0, 100)])
        before = conn.execute(
            'SELECT COUNT(*) FROM market_resolutions').fetchone()[0]
        SC.counterfactual(conn)
        after = conn.execute(
            'SELECT COUNT(*) FROM market_resolutions').fetchone()[0]
        assert (before, after) == (1, 1)

    def test_the_cli_refuses_the_two_modes_together(self, tmp_path):
        # Refused BEFORE the database is opened, so the writable connection
        # --backfill needs is never even created in a reporting run.
        missing = str(tmp_path / 'never-created.db')
        assert SC.main(['--db', missing, '--counterfactual',
                        '--backfill']) == 2
        assert not os.path.exists(missing)

    def test_the_cli_reports_the_counterfactual_read_only(self, tmp_path):
        path = str(tmp_path / 'book.db')
        disk = sqlite3.connect(path)
        disk.execute(BOOK_POSITIONS_DDL)
        disk.execute(SIGNALS_DDL)
        disk.execute('INSERT INTO signals VALUES (?, ?)',
                     ('s0', json.dumps(dict(outcome_side='Up'))))
        disk.execute(
            'INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            ('p0', 'm1', 's0', 1000, 1001, 0.26, 0.05, 10.0, 0.0,
             'sell:salvage_floor'))
        disk.commit()
        ensure_schema(disk)
        write_resolutions(disk, [('m1', 'Up', 0.0, 100)], SOURCE_VENUE)
        disk.close()
        assert SC.main(['--db', path, '--counterfactual', '--json']) == 0
        after = sqlite3.connect(path)
        assert after.execute(
            'SELECT COUNT(*) FROM market_resolutions').fetchone()[0] == 1

    def test_the_cli_exits_nonzero_when_the_join_matches_nothing(self,
                                                                tmp_path):
        path = str(tmp_path / 'unjoinable.db')
        disk = sqlite3.connect(path)
        disk.execute(BOOK_POSITIONS_DDL)
        disk.execute(SIGNALS_DDL)
        disk.execute('INSERT INTO signals VALUES (?, ?)',
                     ('s0', json.dumps(dict(outcome_side='Up'))))
        disk.execute(
            'INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            ('p0', 'm1', 's0', 1000, 1001, 0.26, 0.05, 10.0, 0.0,
             'sell:salvage_floor'))
        disk.commit()
        ensure_schema(disk)
        write_resolutions(disk, [('somewhere-else', 'Up', 0.0, 100)],
                          SOURCE_VENUE)
        disk.close()
        assert SC.main(['--db', path, '--counterfactual']) == 2


# ---------------------------------------------------------------------------
# 8. The independent unit is the MARKET-SIDE, not the share (proposal 046,
#    D-356 R2), and the graded source set is VENUE-ONLY (proposal 047,
#    D-356 R3)
# ---------------------------------------------------------------------------


class TestClusterIsTheUnitOfIndependence(object):
    """046. A market-side resolves ONCE; its shares are one draw, not many."""

    def test_many_shares_on_one_market_side_are_one_cluster(self):
        # The whole argument in one assertion. Three positions, 300 shares,
        # ONE market-side. A per-share error bar would call this 300 draws.
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 100.0, 1000),
             ('m1', 'Up', 0.05, 'sell:salvage_floor', 100.0, 1000),
             ('m1', 'Up', 0.05, 'sell:salvage_floor', 100.0, 1000)],
            [('m1', 'Up', 0.0, 100)])
        row = row_for(SC.counterfactual(conn), 'sell:salvage_floor')
        assert row['matched'] == 3
        assert row['shares'] == 300.0
        assert row['clusters'] == 1
        assert row['positions_per_cluster'] == pytest.approx(3.0)
        assert row['shares_per_cluster'] == pytest.approx(300.0)

    def test_both_halves_of_the_key_make_the_cluster(self):
        # Clustering on the slug alone would merge Up and Down - two
        # ANTI-correlated outcomes - into one draw, understating the cluster
        # count and producing an error bar too small in the opposite
        # direction from the one this repair exists to fix (046 rule 1).
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000),
             ('m1', 'Down', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m1', 'Up', 0.0, 100), ('m1', 'Down', 1.0, 100)])
        row = row_for(SC.counterfactual(conn), 'sell:salvage_floor')
        assert row['clusters'] == 2

    def test_the_sigma_is_computed_on_clusters_not_shares(self):
        # 100 shares per side across 4 market-sides, one of which settled.
        # p = 0.25 share-weighted; the SE divides by 4 draws, not 400 shares.
        conn = make_book(
            [('m%d' % i, 'Up', 0.05, 'sell:salvage_floor', 100.0, 1000)
             for i in range(4)],
            [('m%d' % i, 'Up', 1.0 if i == 0 else 0.0, 100)
             for i in range(4)])
        row = row_for(SC.counterfactual(conn), 'sell:salvage_floor')
        assert row['clusters'] == 4
        assert row['realised_settle_rate'] == pytest.approx(0.25)
        assert row['settle_rate_se'] == pytest.approx(
            math.sqrt(0.25 * 0.75 / 4))
        # The per-SHARE error bar it replaces is exactly sqrt(400/4) = 10x
        # smaller. That ratio is the design effect, and on the live books it
        # is sqrt(22.4) = 4.7x.
        assert row['settle_rate_se'] == pytest.approx(
            10 * math.sqrt(0.25 * 0.75 / 400))

    def test_the_point_estimate_stays_share_weighted(self):
        # 046 rule 3. The money at stake really is proportional to shares and
        # the break-even identity is a share-weighted statement. Only the
        # SAMPLE SIZE was wrong, never the weighting. A 90-share loser and a
        # 10-share winner is a 0.10 settle rate, not the 0.50 an
        # equal-weighted cluster mean would report.
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 90.0, 1000),
             ('m2', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m1', 'Up', 0.0, 100), ('m2', 'Up', 1.0, 100)])
        row = row_for(SC.counterfactual(conn), 'sell:salvage_floor')
        assert row['realised_settle_rate'] == pytest.approx(0.10)
        assert row['clusters'] == 2

    def test_every_exit_reason_carries_its_own_cluster_count(self):
        # 046 rule 2: not only the graded one. The context rows carry the
        # largest deltas in the live table and the least support.
        conn = make_book([
            ('m1', 'Up', 0.90, 'sell:profit_target', 10.0, 1000),
            ('m2', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000),
            ('m3', 'Up', 0.31, 'sell:model_stop', 10.0, 1000),
        ], [('m1', 'Up', 1.0, 100), ('m2', 'Up', 0.0, 100),
            ('m3', 'Up', 0.0, 100)])
        result = SC.counterfactual(conn)
        for row in result['by_exit_reason']:
            assert 'clusters' in row
            assert 'settle_rate_se' in row
        assert row_for(result, 'sell:model_stop')['clusters'] == 1

    def test_the_cluster_count_never_exceeds_the_matched_count(self):
        # 046's own rollback check A: a cluster count above the position count
        # would mean the grouping key is inverted and each row is minting its
        # own cluster.
        conn = make_book(
            [('m%d' % (i % 3), 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)
             for i in range(9)],
            [('m%d' % i, 'Up', 0.0, 100) for i in range(3)])
        for row in SC.counterfactual(conn)['by_exit_reason']:
            assert row['clusters'] <= row['matched']

    def test_the_self_check_carries_the_same_correction(self):
        # 046 rule 5. The self-check is the instrument's ONLY error bar and
        # has the identical defect. Two settlements on ONE market-side are one
        # draw there too.
        conn = make_book(
            [('m1', 'Up', 1.0, 'target', 10.0, 1000),
             ('m1', 'Up', 1.0, 'target', 10.0, 1000),
             ('m2', 'Up', 0.0, 'stop', 10.0, 1000)],
            [('m1', 'Up', 1.0, 100), ('m2', 'Up', 0.0, 100)])
        check = SC.counterfactual(conn)['self_check']
        assert check['positions'] == 3
        assert check['clusters'] == 2
        assert check['positions_per_cluster'] == pytest.approx(1.5)

    def test_the_ceiling_and_the_band_are_not_re_sized(self):
        # 046 rule 4 and D-356 R2: the gate is ADDITIVE. D-354 R2 refused to
        # re-size a live experiment's threshold mid-experiment and that
        # refusal holds for a reason discovered later just as it held before.
        assert SC.KILL_BAND == 0.010
        assert SC.KILL_MIN_MATCHED == 400
        assert SC.SELF_CHECK_MAX_DISAGREEMENT_RATE == 0.0500
        assert SC.KILL_SIGMA_MULTIPLE == 3.0


class TestTheThreeSigmaGate(object):
    """046. The 400-position bar is NECESSARY and is not SUFFICIENT."""

    def salvage_book(self, n, exit_px, settle_every=None):
        rows = [('s%d' % i, 'Up', exit_px, 'sell:salvage_floor', 10.0, 1000)
                for i in range(n)]
        resolutions = [
            ('s%d' % i, 'Up',
             1.0 if (settle_every and i % settle_every == 0) else 0.0, 100)
            for i in range(n)]
        return make_book(rows, resolutions)

    def test_the_bar_can_be_met_and_the_verdict_still_refused(self):
        # 400 matched positions, a delta of 0.03 that clears the 0.010 band
        # in the NEGATIVE direction - and 400 clusters at p=0.05 give a
        # sigma of 0.0109, so 3 sigma is 0.0327 and 0.03 does not reach it.
        # Before 046 this returned NEGATIVE on noise.
        verdict = SC.counterfactual(
            self.salvage_book(400, 0.02, settle_every=20))['verdict']
        assert verdict['matched'] == 400
        assert verdict['margin'] == pytest.approx(0.03)
        assert verdict['margin'] >= SC.KILL_BAND
        assert verdict['clusters'] == 400
        assert verdict['margin_sigma'] < 3.0
        assert verdict['verdict'] == 'NOT_TESTED'
        assert 'sigma' in verdict['reason']

    def test_a_delta_past_three_sigma_and_past_the_band_still_grades(self):
        # The gate does not close the instrument, it raises the evidence bar.
        # 600 clusters at p=0.05: sigma 0.0089, 3 sigma 0.0267 < 0.03.
        verdict = SC.counterfactual(
            self.salvage_book(600, 0.02, settle_every=20))['verdict']
        assert verdict['clusters'] == 600
        assert verdict['margin_sigma'] > 3.0
        assert verdict['verdict'] == 'NEGATIVE'

    def test_a_zero_sigma_fails_closed_rather_than_admitting_anything(self):
        # Every cluster settling the same way makes sqrt(p*(1-p)/clusters)
        # exactly 0.0, which would satisfy any delta VACUOUSLY. A zero error
        # bar is an absent one, not a narrow one, so this fails CLOSED
        # (convention 11). Not in 046's literal text, which assumes an
        # interior p; recorded in D-356's handoff as a boundary judgment.
        verdict = SC.counterfactual(self.salvage_book(400, 0.05))['verdict']
        assert verdict['realised_settle_rate'] == 0.0
        assert verdict['settle_rate_se'] == 0.0
        assert verdict['verdict'] == 'NOT_TESTED'
        assert 'zero error bar' in verdict['reason']

    def test_the_gate_is_reported_even_when_the_bar_is_what_blocked(self):
        # The reader must be able to see the sigma the verdict was measured
        # against, not only the count that stopped it short.
        report = SC.counterfactual(self.salvage_book(100, 0.02,
                                                     settle_every=20))
        verdict = report['verdict']
        assert verdict['verdict'] == 'NOT_TESTED'
        assert verdict['clusters'] == 100
        assert verdict['sigma_multiple'] == 3.0
        assert any('sigma' in line for line
                   in SC.format_counterfactual(report))


class TestCounterfactualGradesVenueOnly(object):
    """047 / D-356 R3: 043 says `source = venue`; the code now says it too."""

    def test_the_default_graded_set_is_venue_only(self):
        assert COUNTERFACTUAL_GRADED_SOURCES == (SOURCE_VENUE,)

    def test_live_sources_is_unchanged_because_038_depends_on_it(self):
        # 047 rule 1. Coverage and grading genuinely need DIFFERENT source
        # sets: for COVERAGE an inferred terminal price IS a legitimately
        # recovered resolution and counting it is right. The fix is a second
        # named constant, never a change to the first.
        assert LIVE_SOURCES == (SOURCE_VENUE, SOURCE_INFERRED_TERMINAL_PRICE)
        assert SOURCE_INFERRED_TERMINAL_PRICE in RESOLUTION_SOURCES

    def two_source_book(self):
        """One venue market-side and one inferred_terminal_price market-side.

        047's kill condition asks for both sources on the SAME market-side.
        That is UNCONSTRUCTIBLE: `market_resolutions` carries
        `UNIQUE (market_slug, outcome_side)`, so a market-side holds exactly
        one row whatever its source, and `write_resolutions` uses
        `INSERT OR IGNORE` so the first writer wins. The realisable failure
        is therefore whole ADDITIONAL market-sides entering the graded arm,
        which is what this builds and what actually matters - the same-side
        variant is pinned as impossible in the test below.
        """
        conn = make_book(
            [('m-venue', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000),
             ('m-inferred', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m-venue', 'Up', 0.0, 100)])
        write_resolutions(conn, [('m-inferred', 'Up', 0.0, 100)],
                          SOURCE_INFERRED_TERMINAL_PRICE)
        return conn

    def test_only_the_venue_row_is_graded(self):
        row = row_for(SC.counterfactual(self.two_source_book()),
                      'sell:salvage_floor')
        assert row['matched'] == 1
        assert row['clusters'] == 1

    def test_the_old_default_would_have_graded_both(self):
        # Proves the fixture really carries both sources, so the test above
        # passes for the RIGHT reason rather than because the second row is
        # missing. This is the assertion that would have caught the defect.
        row = row_for(
            SC.counterfactual(self.two_source_book(), sources=LIVE_SOURCES),
            'sell:salvage_floor')
        assert row['matched'] == 2

    def test_the_excluded_source_is_reported_not_silently_dropped(self):
        # 047 rule 3, convention 20: silent exclusion and silent inclusion are
        # the same defect facing opposite directions.
        report = SC.counterfactual(self.two_source_book())
        census = report['source_census']
        assert census['excluded'] == [
            dict(source=SOURCE_INFERRED_TERMINAL_PRICE, rows=1)]
        assert census['graded_rows'] == 1
        assert census['total_rows'] == 2
        header = '\n'.join(SC.format_counterfactual(report))
        assert SOURCE_INFERRED_TERMINAL_PRICE in header
        assert 'EXCLUDED' in header

    def test_an_all_venue_ledger_reports_no_exclusion(self):
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m1', 'Up', 0.0, 100)])
        census = SC.counterfactual(conn)['source_census']
        assert census['excluded'] == []
        assert census['excluded_rows'] == 0

    def test_the_self_check_uses_the_graded_set_too(self):
        # 047 rule 4. The self-check's independence warrant is a claim about
        # the venue WINNER FIELD against Gamma's outcomePrices. A terminal
        # BOOK PRICE is the same kind of quantity as outcomePrices, so
        # admitting it would turn the check from field-against-price into
        # price-against-price and the disagreement rate would FALL for
        # reasons unrelated to the ledger being more correct. An error bar
        # that shrinks because the instrument got worse is the most dangerous
        # failure available to this design.
        conn = make_book(
            [('m-venue', 'Up', 1.0, 'target', 10.0, 1000),
             ('m-inferred', 'Up', 1.0, 'target', 10.0, 1000)],
            [('m-venue', 'Up', 1.0, 100)])
        write_resolutions(conn, [('m-inferred', 'Up', 1.0, 100)],
                          SOURCE_INFERRED_TERMINAL_PRICE)
        assert SC.counterfactual(conn)['self_check']['positions'] == 1
        assert SC.counterfactual(
            conn, sources=LIVE_SOURCES)['self_check']['positions'] == 2

    def test_one_market_side_cannot_hold_two_sources(self):
        # Pins the schema fact that makes 047's literal fixture
        # unconstructible, so a later session reads this as a DELIBERATE
        # deviation rather than a weakened test.
        conn = make_book(
            [('m1', 'Up', 0.05, 'sell:salvage_floor', 10.0, 1000)],
            [('m1', 'Up', 0.0, 100)])
        write_resolutions(conn, [('m1', 'Up', 1.0, 100)],
                          SOURCE_INFERRED_TERMINAL_PRICE)
        rows = conn.execute(
            'SELECT source, resolved_px FROM market_resolutions '
            'WHERE market_slug = ?', ('m1',)).fetchall()
        assert rows == [(SOURCE_VENUE, 0.0)]
