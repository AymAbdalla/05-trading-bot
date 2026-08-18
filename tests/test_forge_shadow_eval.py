"""Tests for the Forge shadow evaluator and the relaxed Forge schema.

Two things are being pinned here.

1. `agents/forge_shadow_eval.py` must never turn "could not read" or "could not
   run" into "ran and found nothing" (convention 11), and every decision row
   must land in exactly one counted bucket (convention 20).

2. `agents/forge.py` gave up most of its refusals on 2026-08-17. The ones that
   survive must still fire, the ones that were retired must still appear in the
   counter schema at zero, and the accounting identities must still hold.
"""
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agents import forge  # noqa: E402
from agents import forge_shadow_eval as se  # noqa: E402


SIGNALS_DDL = """
CREATE TABLE signals (
    id TEXT PRIMARY KEY, ts INTEGER NOT NULL, pair TEXT NOT NULL,
    tf TEXT NOT NULL, strategy_id TEXT NOT NULL, pattern TEXT NOT NULL,
    direction TEXT NOT NULL, confidence REAL NOT NULL,
    features_json TEXT NOT NULL, acted INTEGER NOT NULL DEFAULT 0,
    skip_reason TEXT, mode TEXT NOT NULL DEFAULT 'paper')
"""
POSITIONS_DDL = """
CREATE TABLE positions (
    id TEXT PRIMARY KEY, pair TEXT NOT NULL, strategy_id TEXT NOT NULL,
    signal_id TEXT, opened_ts INTEGER NOT NULL, closed_ts INTEGER,
    entry_px REAL NOT NULL, exit_px REAL, qty REAL NOT NULL,
    stop_px REAL NOT NULL, target_px REAL NOT NULL, pnl_gross REAL,
    pnl_net REAL, fees REAL DEFAULT 0, r_multiple REAL, exit_reason TEXT,
    mode TEXT NOT NULL DEFAULT 'paper')
"""
EQUITY_DDL = """
CREATE TABLE equity_snapshots (
    ts INTEGER NOT NULL, equity REAL NOT NULL, cash REAL NOT NULL,
    open_risk REAL NOT NULL, mode TEXT NOT NULL DEFAULT 'paper',
    PRIMARY KEY (ts, mode))
"""


def _build_db(path, signals=(), positions=(), equity=()):
    conn = sqlite3.connect(path)
    conn.executescript(SIGNALS_DDL + ';' + POSITIONS_DDL + ';' + EQUITY_DDL)
    for i, (strategy, acted, reason) in enumerate(signals):
        conn.execute(
            'insert into signals values (?,?,?,?,?,?,?,?,?,?,?,?)',
            (f'sig{i}', 1_787_000_000_000 + i, 'btc-updown-5m-1', '5m',
             strategy, strategy, 'long', 0.0, '{}', acted, reason, 'paper'))
    for i, (strategy, closed_ts, pnl) in enumerate(positions):
        conn.execute(
            'insert into positions values '
            '(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (f'pos{i}', 'btc-updown-5m-1', strategy, None, 1, closed_ts,
             0.5, 1.0, 10.0, 0.0, 1.0, pnl, pnl, 0.0, None, 'resolution',
             'paper'))
    for ts, eq in equity:
        conn.execute('insert into equity_snapshots values (?,?,?,?,?)',
                     (ts, eq, eq, 0.0, 'paper'))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Convention 11: unreadable is not empty
# ---------------------------------------------------------------------------

def test_missing_db_is_unreadable_not_empty(tmp_path):
    result = se.evaluate(str(tmp_path / 'nope.db'), str(tmp_path / 'nope.csv'))
    assert result['status'] == 'unreadable'
    assert 'no such database' in result['error']
    # The failure mode this guards: a caller reading zero entries out of a
    # missing file and recording "no strategy fired".
    assert 'decisions' not in result
    assert se.shadow_candidates(result) == []


def test_corrupt_db_is_unreadable_not_empty(tmp_path):
    path = tmp_path / 'corrupt.db'
    path.write_bytes(b'this is not a sqlite file' * 100)
    result = se.evaluate(str(path), str(tmp_path / 'nope.csv'))
    assert result['status'] == 'unreadable'


def test_db_missing_signals_table_is_unreadable(tmp_path):
    path = str(tmp_path / 'partial.db')
    conn = sqlite3.connect(path)
    conn.executescript(POSITIONS_DDL + ';' + EQUITY_DDL)
    conn.commit()
    conn.close()
    result = se.evaluate(path, str(tmp_path / 'nope.csv'))
    assert result['status'] == 'unreadable'
    assert 'signals' in result['error']


def test_absent_paper_log_is_reported_not_silent(tmp_path):
    path = str(tmp_path / 'x.db')
    _build_db(path, signals=[('S', 0, 'no_streak')])
    result = se.evaluate(path, str(tmp_path / 'gone.csv'))
    assert result['status'] == 'ok'
    assert result['paper_log']['status'] == 'absent'


# ---------------------------------------------------------------------------
# The distinction the module exists for
# ---------------------------------------------------------------------------

def test_data_blocked_strategy_is_not_tested(tmp_path):
    path = str(tmp_path / 'x.db')
    _build_db(path, signals=[('PM_blocked', 0, 'no_spot_or_strike')] * 40)
    result = se.evaluate(path, str(tmp_path / 'gone.csv'))
    gaps = result['gaps']
    assert [r['strategy'] for r in gaps['strategies_not_tested']] \
        == ['PM_blocked']
    assert gaps['strategies_ran_no_entry'] == []
    row = gaps['strategies_not_tested'][0]
    assert row['verdict'] == 'NOT_TESTED'
    assert row['missing_input']


def test_genuine_skip_is_a_measurement_not_not_tested(tmp_path):
    path = str(tmp_path / 'x.db')
    _build_db(path, signals=[('PM_looked', 0, 'no_streak')] * 40)
    gaps = se.evaluate(path, str(tmp_path / 'gone.csv'))['gaps']
    assert [r['strategy'] for r in gaps['strategies_ran_no_entry']] \
        == ['PM_looked']
    assert gaps['strategies_not_tested'] == []


def test_maker_quote_counts_as_sim_limit_not_genuine(tmp_path):
    path = str(tmp_path / 'x.db')
    _build_db(path,
              signals=[('PM_maker', 0, 'maker_fill_not_simulated')] * 40)
    gaps = se.evaluate(path, str(tmp_path / 'gone.csv'))['gaps']
    # The adapter could not model the fill. That is our limitation, not the
    # market declining, so it must not read as "ran and found nothing".
    assert [r['strategy'] for r in gaps['strategies_not_tested']] \
        == ['PM_maker']


def test_thin_sample_is_underpowered_not_a_gap(tmp_path):
    path = str(tmp_path / 'x.db')
    n = se.MIN_EVALUATIONS_FOR_GAP - 1
    _build_db(path, signals=[('PM_thin', 0, 'no_spot_or_strike')] * n)
    gaps = se.evaluate(path, str(tmp_path / 'gone.csv'))['gaps']
    assert [r['strategy'] for r in gaps['strategies_underpowered']] \
        == ['PM_thin']
    assert gaps['strategies_not_tested'] == []


def test_unknown_skip_reason_is_surfaced_not_folded_in(tmp_path):
    path = str(tmp_path / 'x.db')
    _build_db(path, signals=[('PM_weird', 0, 'a_reason_from_the_future')] * 40)
    result = se.evaluate(path, str(tmp_path / 'gone.csv'))
    assert result['gaps']['unknown_skip_reasons'] == {
        'a_reason_from_the_future': 40}
    cls, detail = se.classify_skip_reason('a_reason_from_the_future')
    assert cls == se.UNKNOWN
    assert 'SKIP_CLASSIFICATION' in detail


def test_null_skip_reason_is_counted(tmp_path):
    path = str(tmp_path / 'x.db')
    _build_db(path, signals=[('PM_null', 0, None)] * 40)
    result = se.evaluate(path, str(tmp_path / 'gone.csv'))
    assert result['gaps']['unknown_skip_reasons'] == {'<null_skip_reason>': 40}


# ---------------------------------------------------------------------------
# Convention 20: identities
# ---------------------------------------------------------------------------

def test_every_decision_row_lands_in_exactly_one_bucket(tmp_path):
    path = str(tmp_path / 'x.db')
    _build_db(path, signals=(
        [('A', 1, None)] * 3
        + [('A', 0, 'no_streak')] * 5
        + [('B', 0, 'no_spot_or_strike')] * 7
        + [('B', 2, 'neither_acted_nor_skipped')] * 2))
    d = se.evaluate(path, str(tmp_path / 'gone.csv'))['decisions']
    assert d['n_rows'] == 17
    assert d['n_entries'] + d['n_skips'] + d['n_malformed'] == d['n_rows']
    assert d['n_malformed'] == 2
    for rec in d['by_strategy'].values():
        assert sum(rec['skip_classes'].values()) == rec['n_skips']


def test_positions_accounting_and_pnl(tmp_path):
    path = str(tmp_path / 'x.db')
    _build_db(path,
              signals=[('A', 1, None)],
              positions=[('A', 100, 5.0), ('A', 101, -2.0), ('A', 102, 0.0),
                         ('A', None, None)],
              equity=[(1, 1000.0), (2, 900.0), (3, 1100.0)])
    result = se.evaluate(path, str(tmp_path / 'gone.csv'))
    pos = result['positions']
    assert (pos['n_closed'], pos['n_open']) == (3, 1)
    assert (pos['wins'], pos['losses'], pos['flats']) == (1, 1, 1)
    assert pos['pnl_net_total'] == 3.0
    eq = result['equity']
    assert eq['equity_last'] == 1100.0
    assert eq['max_drawdown_pct'] == pytest.approx(10.0)


def test_result_is_strict_json_serialisable(tmp_path):
    path = str(tmp_path / 'x.db')
    _build_db(path, signals=[('A', 0, 'no_streak')] * 40,
              equity=[(1, 1000.0)])
    result = se.evaluate(path, str(tmp_path / 'gone.csv'))
    # Convention 19: allow_nan=False raises rather than emitting `NaN`, which
    # json.loads would accept and every other parser would reject.
    json.dumps(result, allow_nan=False)


# ---------------------------------------------------------------------------
# The relaxed Forge schema
# ---------------------------------------------------------------------------

def _candidate(**over):
    base = {
        'name': 'a_new_idea',
        'kind': 'edge_hypothesis',
        'asset_class': 'PREDICTION_MARKET',
        'thesis': 'something',
        'expected_edge_bps': 400,
        'kill_condition': ('net under 1c per share over 200 trades scored by '
                           'backtest/polymarket_harness.py'),
        'entry_exit_rules': 'buy low',
        'data_requirements': 'a book',
        'related_graveyard_findings': 'none',
        'body': 'why',
    }
    base.update(over)
    return base


def test_duplicate_of_graveyard_name_warns_and_does_not_refuse():
    warnings = forge.validate(_candidate(name='rsi_extreme'), ['rsi_extreme'])
    assert [w['category'] for w in warnings] == ['duplicate_name_warning']


def test_missing_graveyard_link_warns_and_does_not_refuse():
    warnings = forge.validate(
        _candidate(related_graveyard_findings=''), [])
    assert 'no_graveyard_link_warning' in [w['category'] for w in warnings]


def test_unlisted_asset_class_warns_and_does_not_refuse():
    warnings = forge.validate(_candidate(asset_class='WEATHER'), [])
    assert 'unlisted_asset_class_warning' in [w['category'] for w in warnings]


def test_multi_class_edge_hypothesis_warns_and_does_not_refuse():
    warnings = forge.validate(_candidate(asset_class='MULTI'), [])
    assert 'multi_class_warning' in [w['category'] for w in warnings]


def test_combination_kind_is_allowed():
    assert forge.validate(_candidate(kind='combination'), []) == []


def test_kill_condition_without_a_number_is_still_refused():
    with pytest.raises(forge.ProposalRefused) as exc:
        forge.validate(
            _candidate(kill_condition='it stops working, per the harness'), [])
    assert exc.value.category == 'unmeasurable_kill_condition'


def test_kill_condition_without_a_named_harness_is_refused():
    with pytest.raises(forge.ProposalRefused) as exc:
        forge.validate(
            _candidate(kill_condition='net edge below 30bps over 200 trades'),
            [])
    assert exc.value.category == 'kill_condition_names_no_harness'


def test_edge_floor_is_instrument_aware():
    # 100bps clears the 30bps spot floor and fails the 200bps binary floor,
    # because one 1c tick on a 50c contract IS 200bps.
    assert forge.validate(
        _candidate(asset_class='CRYPTO', expected_edge_bps=100,
                   kill_condition='under 30bps over 200 trades in the '
                                  'vectorized harness'), []) is not None
    with pytest.raises(forge.ProposalRefused) as exc:
        forge.validate(_candidate(expected_edge_bps=100), [])
    assert exc.value.category == 'below_min_edge_bps'
    assert forge.min_edge_bps_for('PREDICTION_MARKET') == 200
    assert forge.min_edge_bps_for('CRYPTO') == forge.MIN_GROSS_EDGE_BPS


def test_experiment_must_not_claim_an_edge_it_cannot_know():
    with pytest.raises(forge.ProposalRefused) as exc:
        forge.validate(_candidate(kind='experiment',
                                  expected_edge_bps=900), [])
    assert exc.value.category == 'unknowable_edge_claimed'
    assert forge.validate(
        _candidate(kind='experiment', expected_edge_bps=None), []) == []


def test_retired_categories_stay_in_the_counter_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, 'PROPOSALS_DIR', str(tmp_path))
    record = forge.generate(
        [_candidate(name='rsi_extreme'),
         _candidate(name='bad', kill_condition='it stops working')],
        {'known_strategies': ['rsi_extreme']})
    assert record['candidates_screened'] == 2
    assert len(record['written']) == 1
    assert len(record['refused']) == 1
    # Convention 20: the retired refusals are reported at zero, not absent.
    for retired in forge.RETIRED_REFUSAL_CATEGORIES:
        assert retired not in record['refused_by_category']
    assert record['retired_refusal_categories'] == \
        dict(forge.RETIRED_REFUSAL_CATEGORIES)
    assert set(record['refused_by_category']) == set(forge.REFUSAL_CATEGORIES)
    assert sum(record['refused_by_category'].values()) == 1
    assert record['warned_by_category']['duplicate_name_warning'] == 1
    assert sum(record['warned_by_category'].values()) == len(record['warned'])
    # And the warning is on the proposal itself, not only in the run log.
    written = (tmp_path / os.path.basename(record['written'][0]['path'])).read_text()
    assert 'duplicate_name_warning' in written
    json.dumps(record, allow_nan=False)


def test_shadow_candidates_are_repairs_with_a_null_edge(tmp_path):
    path = str(tmp_path / 'x.db')
    _build_db(path, signals=[('PM_blocked', 0, 'no_spot_or_strike')] * 40)
    result = se.evaluate(path, str(tmp_path / 'gone.csv'))
    cands = se.shadow_candidates(result)
    assert len(cands) == 1
    assert cands[0]['kind'] == 'repair'
    assert cands[0]['expected_edge_bps'] is None
    # And they survive the validator they were built for.
    forge.validate(cands[0], [])


def test_attach_shadow_records_an_unreadable_db_rather_than_dropping_it():
    gaps = forge.attach_shadow({}, {'status': 'unreadable',
                                    'db_path': 'db/nope.db',
                                    'error': 'no such database'})
    assert gaps['shadow'] is None
    assert gaps['shadow_error']['error'] == 'no such database'
