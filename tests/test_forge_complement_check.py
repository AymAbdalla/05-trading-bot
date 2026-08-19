"""Tests for `agents/forge_complement_check.py` (Forge proposal 036,
pm_complement_pair_keying).

Pins down the success condition the handoff states directly:
`complement_pairs` must find synchronous pairs by an exact key join only
(zero ambiguity, never a price-based match), and `evaluate` must apply the
two stated thresholds - `insufficient_data` below `min_pairs`, and
`failed_null_threshold` above 5% NULL `condition_id` - rather than silently
reading either state as an ordinary 'ok'.
"""
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agents import forge_complement_check as fcc  # noqa: E402
from strategies.polymarket.dip_arb import SCHEMA_SQL  # noqa: E402


def _build_db(path, rows=()):
    """`rows`: (market_id, ts, mid, best_bid, best_ask, source, condition_id,
    complement_id) tuples, written straight into `market_tape`."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.executemany(
        'INSERT INTO market_tape (market_id, ts, mid, best_bid, best_ask, '
        'source, condition_id, complement_id) VALUES (?,?,?,?,?,?,?,?)',
        rows)
    conn.commit()
    conn.close()


def _pair_row(cond, ts, tok_a, tok_b, ask_a, ask_b):
    """One synchronous complement pair as two `market_tape` rows."""
    return [
        (tok_a, ts, ask_a, ask_a - 0.01, ask_a, 'test', cond, tok_b),
        (tok_b, ts, ask_b, ask_b - 0.01, ask_b, 'test', cond, tok_a),
    ]


class TestComplementPairs:
    def test_finds_a_synchronous_pair_by_exact_key_only(self, tmp_path):
        db_path = str(tmp_path / 't.db')
        rows = _pair_row('cond-1', 100.0, 'tok-a', 'tok-b', 0.40, 0.55)
        _build_db(db_path, rows)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        pairs = fcc.complement_pairs(conn)
        conn.close()

        assert len(pairs) == 1
        p = pairs[0]
        assert p['condition_id'] == 'cond-1'
        assert {p['token_a'], p['token_b']} == {'tok-a', 'tok-b'}

    def test_two_independent_markets_near_050_are_not_paired(self, tmp_path):
        # The exact case the old mid-sum heuristic could not resolve: two
        # unrelated tokens both quoting near 0.50 at the same timestamp, with
        # no condition_id/complement_id linking them at all.
        db_path = str(tmp_path / 't.db')
        rows = [
            ('tok-x', 100.0, 0.50, 0.49, 0.51, 'test', None, None),
            ('tok-y', 100.0, 0.50, 0.49, 0.51, 'test', None, None),
        ]
        _build_db(db_path, rows)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        pairs = fcc.complement_pairs(conn)
        conn.close()
        assert pairs == []

    def test_a_one_sided_complement_id_is_not_a_pair(self, tmp_path):
        # tok-a claims tok-b as its complement, but tok-b was written without
        # ever claiming tok-a back (e.g. a partial/corrupt write). The join
        # requires both directions, so this must not surface as a pair.
        db_path = str(tmp_path / 't.db')
        rows = [
            ('tok-a', 100.0, 0.40, 0.39, 0.40, 'test', 'cond-1', 'tok-b'),
            ('tok-b', 100.0, 0.55, 0.54, 0.55, 'test', 'cond-1', None),
        ]
        _build_db(db_path, rows)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        pairs = fcc.complement_pairs(conn)
        conn.close()
        assert pairs == []

    def test_since_ts_excludes_earlier_rows(self, tmp_path):
        db_path = str(tmp_path / 't.db')
        rows = (_pair_row('cond-1', 50.0, 'tok-a', 'tok-b', 0.40, 0.55) +
                _pair_row('cond-1', 150.0, 'tok-a', 'tok-b', 0.41, 0.56))
        _build_db(db_path, rows)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        pairs = fcc.complement_pairs(conn, since_ts=100.0)
        conn.close()
        assert len(pairs) == 1
        assert pairs[0]['ts'] == 150.0


class TestAmbiguityFraction:
    def test_zero_pairs_found_is_zero_by_construction(self):
        pairs = [
            {'condition_id': 'c1', 'ts': 1.0, 'token_a': 'a', 'token_b': 'b'},
            {'condition_id': 'c1', 'ts': 2.0, 'token_a': 'c', 'token_b': 'd'},
        ]
        assert fcc._ambiguity_fraction(pairs) == 0.0

    def test_no_pairs_is_none_not_zero(self):
        # None distinguishes "measured, found nothing ambiguous" from
        # "nothing was measured" (convention 11).
        assert fcc._ambiguity_fraction([]) is None


class TestNullConditionFraction:
    def test_computes_the_fraction(self, tmp_path):
        db_path = str(tmp_path / 't.db')
        rows = [
            ('tok-a', 1.0, 0.4, 0.39, 0.4, 'test', 'cond-1', 'tok-b'),
            ('tok-b', 1.0, 0.5, 0.49, 0.5, 'test', 'cond-1', 'tok-a'),
            ('tok-c', 1.0, 0.3, 0.29, 0.3, 'test', None, None),
        ]
        _build_db(db_path, rows)
        conn = sqlite3.connect(db_path)
        out = fcc.null_condition_fraction(conn)
        conn.close()
        assert out == {'total_rows': 3, 'null_condition_id_rows': 1,
                       'null_fraction': pytest.approx(1 / 3)}


class TestEvaluate:
    def test_unreadable_db_is_not_an_empty_ok(self, tmp_path):
        result = fcc.evaluate(db_path=str(tmp_path / 'does-not-exist.db'))
        assert result['status'] == 'unreadable'

    def test_below_min_pairs_is_insufficient_data_not_a_failure(self, tmp_path):
        db_path = str(tmp_path / 't.db')
        rows = _pair_row('cond-1', 1.0, 'tok-a', 'tok-b', 0.40, 0.55)
        _build_db(db_path, rows)
        result = fcc.evaluate(db_path=db_path, min_pairs=1000)
        assert result['status'] == 'insufficient_data'
        assert result['pairs_found'] == 1
        assert result['ambiguity_fraction'] == 0.0

    def test_enough_low_null_pairs_is_ok(self, tmp_path):
        db_path = str(tmp_path / 't.db')
        rows = []
        for i in range(10):
            rows += _pair_row('cond-1', float(i), 'tok-a', 'tok-b',
                              0.40, 0.55)
        _build_db(db_path, rows)
        result = fcc.evaluate(db_path=db_path, min_pairs=10)
        assert result['status'] == 'ok'
        assert result['pairs_found'] == 10
        assert result['ambiguity_fraction'] == 0.0
        assert result['no_arbitrage_distribution']['n'] == 10
        assert result['no_arbitrage_distribution']['mean'] == \
            pytest.approx(0.95)

    def test_above_5_percent_null_condition_id_fails(self, tmp_path):
        db_path = str(tmp_path / 't.db')
        rows = []
        for i in range(20):
            rows += _pair_row('cond-1', float(i), 'tok-a', 'tok-b',
                              0.40, 0.55)
        # 4 NULL rows against 44 total (20*2 keyed + 4 null) = ~8.3%, above
        # the 5% threshold.
        for i in range(4):
            rows.append((f'tok-legacy-{i}', float(100 + i), 0.5, 0.49, 0.5,
                        'test', None, None))
        _build_db(db_path, rows)
        result = fcc.evaluate(db_path=db_path, min_pairs=10)
        assert result['status'] == 'failed_null_threshold'
        assert result['null_condition_id']['null_fraction'] > 0.05
        # The prescribed fix is documented in the result, not applied by it.
        assert 'REVERT' in result['note']

    def test_null_threshold_is_checked_before_the_pair_count_floor(
            self, tmp_path):
        # A tiny sample that is ALSO majority-NULL must report the failure,
        # not quietly fall through to insufficient_data and hide it.
        db_path = str(tmp_path / 't.db')
        rows = _pair_row('cond-1', 1.0, 'tok-a', 'tok-b', 0.40, 0.55)
        rows.append(('tok-legacy', 2.0, 0.5, 0.49, 0.5, 'test', None, None))
        _build_db(db_path, rows)
        result = fcc.evaluate(db_path=db_path, min_pairs=1000)
        assert result['status'] == 'failed_null_threshold'
