"""The orphaned-position sweep (D-353, executed under D-363 R1).

`closed_ts IS NULL` must mean "live right now". Every process death breaks that:
the rows the dead loop held open are never closed by anyone. The sweep restores
the invariant and books the dead premium as the loss it actually was.

The two properties worth pinning are opposites of each other, and the second is
the dangerous one:
  1. every pre-boundary open row IS swept, and
  2. NO post-boundary row is - those belong to the live loop, and sweeping one
     books a loss on a position that is still trading.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.sweep_orphan_positions import (  # noqa: E402
    ORPHAN_EXIT_REASON, sweep)

BOUNDARY_MS = 1_787_244_127_000

POSITIONS_DDL = """
CREATE TABLE positions (
    id INTEGER PRIMARY KEY,
    pair TEXT,
    strategy_id TEXT,
    signal_id INTEGER,
    opened_ts INTEGER,
    closed_ts INTEGER,
    entry_px REAL,
    exit_px REAL,
    qty REAL,
    stop_px REAL,
    pnl_gross REAL,
    pnl_net REAL,
    fees REAL,
    r_multiple REAL,
    exit_reason TEXT
)
"""


def _db(rows):
    """A temp database holding `rows` in `positions`. Returns its path."""
    path = tempfile.mktemp(suffix='.db')
    conn = sqlite3.connect(path)
    conn.execute(POSITIONS_DDL)
    conn.executemany(
        'INSERT INTO positions (id, pair, strategy_id, opened_ts, closed_ts, '
        'entry_px, qty, stop_px, fees) VALUES (?,?,?,?,?,?,?,?,?)', rows)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def book():
    """Two orphans, one live position, one already-closed row."""
    path = _db([
        # id, pair, strategy, opened_ts, closed_ts, entry_px, qty, stop, fees
        (1, 'btc-a', 'PM_x', BOUNDARY_MS - 90_000, None, 0.40, 10.0, 0.20, 0.0),
        (2, 'btc-b', 'PM_y', BOUNDARY_MS - 10_000, None, 0.60, 5.0, 0.30, 0.05),
        (3, 'btc-c', 'PM_x', BOUNDARY_MS + 60_000, None, 0.50, 4.0, 0.25, 0.0),
        (4, 'btc-d', 'PM_x', BOUNDARY_MS - 50_000, 999, 0.30, 2.0, 0.10, 0.0),
    ])
    yield path
    os.unlink(path)


def _rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    out = {r['id']: dict(r) for r in conn.execute('SELECT * FROM positions')}
    conn.close()
    return out


# -- the census --------------------------------------------------------------

def test_dry_run_counts_the_orphans_and_writes_nothing(book):
    before = _rows(book)
    res = sweep(book, BOUNDARY_MS, apply=False)

    assert res['orphans'] == 2
    # 0.40*10 + 0.60*5 = 7.00
    assert res['cost_basis'] == pytest.approx(7.00)
    assert res['left_live'] == 1
    assert res['applied'] is False
    assert _rows(book) == before, 'a dry run wrote to the database'


# -- the sweep ---------------------------------------------------------------

def test_sweep_closes_every_pre_boundary_open_row(book):
    res = sweep(book, BOUNDARY_MS, apply=True)
    assert res['applied'] is True
    assert res['changed'] == 2
    assert res['integrity_check'] == 'ok'
    assert res['remaining_pre_boundary_open'] == 0

    rows = _rows(book)
    for pid in (1, 2):
        assert rows[pid]['closed_ts'] is not None
        assert rows[pid]['exit_reason'] == ORPHAN_EXIT_REASON
        assert rows[pid]['exit_px'] == 0.0


def test_the_live_position_is_not_touched(book):
    """The dangerous direction: row 3 opened AFTER the boundary and is live."""
    before = _rows(book)[3]
    sweep(book, BOUNDARY_MS, apply=True)
    assert _rows(book)[3] == before


def test_an_already_closed_row_is_not_reopened_or_rebooked(book):
    before = _rows(book)[4]
    sweep(book, BOUNDARY_MS, apply=True)
    assert _rows(book)[4] == before


def test_booking_is_exit_at_zero_full_premium_lost(book):
    """D-353 R2. Flat-booking would perpetuate the understatement."""
    sweep(book, BOUNDARY_MS, apply=True)
    rows = _rows(book)

    # id 1: 0.40 * 10 shares, no fees.
    assert rows[1]['pnl_gross'] == pytest.approx(-4.00)
    assert rows[1]['pnl_net'] == pytest.approx(-4.00)
    # id 2: 0.60 * 5 shares = 3.00 premium, plus 0.05 of entry fees.
    assert rows[2]['pnl_gross'] == pytest.approx(-3.00)
    assert rows[2]['pnl_net'] == pytest.approx(-3.05)


def test_r_multiple_uses_the_same_arithmetic_as_an_ordinary_close(book):
    """`pnl_net / ((entry - stop) * qty)`, matching engine/adapters/paper.py."""
    sweep(book, BOUNDARY_MS, apply=True)
    rows = _rows(book)
    assert rows[1]['r_multiple'] == pytest.approx(-4.00 / ((0.40 - 0.20) * 10))


def test_sweeping_twice_is_a_no_op(book):
    sweep(book, BOUNDARY_MS, apply=True)
    second = sweep(book, BOUNDARY_MS, apply=True)
    assert second['orphans'] == 0
    assert second.get('changed', 0) == 0


def test_a_boundary_before_every_row_sweeps_nothing(book):
    res = sweep(book, 1, apply=True)
    assert res['orphans'] == 0
    assert res['left_live'] == 3


# -- D-353 R4: swept rows are not settlement observations --------------------

COVERAGE_DDL = """
CREATE TABLE signals (id INTEGER PRIMARY KEY, features_json TEXT);
CREATE TABLE market_resolutions (
    market_slug TEXT, outcome_side TEXT, settlement_price REAL, source TEXT,
    window_ts INTEGER, resolved_ts INTEGER
);
"""


def test_swept_rows_are_excluded_from_038_coverage():
    """A swept orphan must not enter the coverage denominator OR `observed`.

    Its `exit_px = 0.00` is a record of a dead process, not a market that
    settled NO. Counted, it would both inflate the denominator and manufacture
    a settlement price out of process hygiene.
    """
    from backtest.settlement_coverage import sibling_inference_map

    path = tempfile.mktemp(suffix='.db')
    conn = sqlite3.connect(path)
    conn.execute(POSITIONS_DDL)
    conn.executescript(COVERAGE_DDL)
    conn.executemany('INSERT INTO signals (id, features_json) VALUES (?,?)',
                     [(10, json.dumps({'outcome_side': 'Up'})),
                      (11, json.dumps({'outcome_side': 'Up'}))])
    conn.executemany(
        'INSERT INTO positions (id, pair, signal_id, opened_ts, closed_ts, '
        'entry_px, exit_px, qty, exit_reason) VALUES (?,?,?,?,?,?,?,?,?)',
        [(1, 'real-market', 10, 1, 2, 0.5, 1.0, 10.0, 'target'),
         (2, 'swept-market', 11, 1, 2, 0.5, 0.0, 10.0, ORPHAN_EXIT_REASON)])
    conn.commit()

    _resolved, report = sibling_inference_map(conn)
    conn.close()
    os.unlink(path)

    assert ('real-market', 'Up') in report['touched']
    assert ('swept-market', 'Up') not in report['touched'], (
        'a swept orphan entered the 038 coverage denominator (D-353 R4)')
    assert report['closed_positions'] == 1


# -- the CLI -----------------------------------------------------------------

def test_cli_refuses_without_a_boundary(book):
    proc = subprocess.run(
        [sys.executable, 'scripts/sweep_orphan_positions.py', '--db', book],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert proc.returncode == 1
    assert 'exactly one of --pid or --boundary-ms' in proc.stderr


def test_cli_dry_run_is_the_default(book):
    proc = subprocess.run(
        [sys.executable, 'scripts/sweep_orphan_positions.py', '--db', book,
         '--boundary-ms', str(BOUNDARY_MS)],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert proc.returncode == 0, proc.stderr
    assert 'DRY RUN' in proc.stdout
    assert _rows(book)[1]['closed_ts'] is None
