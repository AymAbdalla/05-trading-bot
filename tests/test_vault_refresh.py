"""Tests for scripts/vault_refresh.py, the evidence builder behind the vault.

The point of this script is that the NUMBERS in a vault note are re-derived
from the database rather than retyped. So these tests are mostly about the
evidence block: that it contains what the model is allowed to say, that it
refuses to let "never fired" look like "fired and lost", and that a refresh
replaces a note in place instead of growing a second copy beside it.

No test here spawns a model turn.
"""
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import vault_refresh  # noqa: E402
from agents import llm_client, vault_reader  # noqa: E402


SCHEMA = '''
CREATE TABLE positions (
    id INTEGER PRIMARY KEY, pair TEXT, strategy_id TEXT, signal_id INTEGER,
    opened_ts INTEGER, closed_ts INTEGER, entry_px REAL, exit_px REAL,
    qty REAL, stop_px REAL, target_px REAL, pnl_gross REAL, pnl_net REAL,
    fees REAL, r_multiple REAL, exit_reason TEXT, mode TEXT);
CREATE TABLE signals (
    id INTEGER PRIMARY KEY, ts INTEGER, pair TEXT, tf TEXT,
    strategy_id TEXT, pattern TEXT, direction TEXT, confidence REAL,
    features_json TEXT, acted INTEGER, skip_reason TEXT, mode TEXT,
    market_duration TEXT);
CREATE TABLE equity_snapshots (
    ts INTEGER PRIMARY KEY, equity REAL, cash REAL, open_risk REAL,
    mode TEXT);
'''

BASE_TS = 1787022141000


@pytest.fixture
def db(tmp_path):
    """A throwaway database. Never the real one: the loop is mid-write on it."""
    path = str(tmp_path / 'trading.db')
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)

    def position(i, strategy, pnl, reason, entry=0.06, exit_px=0.03):
        conn.execute(
            'INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (i, 'btc-updown-5m-1', strategy, i, BASE_TS + i * 1000,
             BASE_TS + i * 1000 + 8700, entry, exit_px, 5.0, 0.03, 0.07,
             pnl, pnl, 0.0, -1.0, reason, 'paper'))

    # A loser with a big sample, and a winner with a tiny one.
    for i in range(1, 41):
        position(i, 'PM_loser', -0.5 if i % 3 else 1.0,
                 'sell:price_stop' if i % 3 else 'sell:profit_target')
    position(100, 'PM_tiny_winner', 3.45, 'target', entry=0.31, exit_px=1.0)
    position(101, 'PM_tiny_winner', 0.50, 'target', entry=0.90, exit_px=1.0)

    # A strategy that was evaluated many times and never traded.
    for i in range(1, 51):
        conn.execute(
            'INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (i, BASE_TS + i, 'btc-updown-5m-1', '5m', 'PM_never_fires',
             'p', 'up', 0.5, '{}', 0, 'wallet_address_unresolved',
             'paper', '5m'))
    for i in range(100, 130):
        conn.execute(
            'INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (i, BASE_TS + i, 'btc-updown-5m-1', '5m', 'PM_loser',
             'p', 'up', 0.5, '{}', 0, 'max_trades_this_window',
             'paper', '5m'))
    conn.execute('INSERT INTO signals VALUES '
                 '(200,?,"btc-updown-5m-1","5m","PM_loser","p","up",0.5,'
                 '"{}",1,NULL,"paper","5m")', (BASE_TS + 200,))
    for i in range(60):
        conn.execute('INSERT INTO equity_snapshots VALUES (?,?,?,0,"paper")',
                     (BASE_TS + i * 1000, 1000.0 - i, 1000.0 - i))
    conn.commit()
    conn.close()
    return path


def test_evidence_carries_the_real_totals(db):
    conn = vault_refresh.connect(db)
    text = vault_refresh.strategy_evidence(conn, ('PM_loser',))
    conn.close()
    # 40 trades, 13 winners (i % 3 == 0 for i in 1..40) -> 32.5%
    assert '| PM_loser | 40 | 32.5 |' in text.replace(' |', ' |')
    assert 'sell:price_stop' in text
    assert 'sell:profit_target' in text


def test_a_small_sample_is_flagged_provisional(db):
    """Convention 7: a FAIL on 200k trades is a verdict, on 1,700 a shrug."""
    conn = vault_refresh.connect(db)
    text = vault_refresh.strategy_evidence(conn, ('PM_tiny_winner',))
    conn.close()
    assert 'PROVISIONAL' in text
    assert 'PM_tiny_winner' in text
    assert 'Convention 7' in text


def test_a_large_sample_is_not_flagged_provisional(db):
    conn = vault_refresh.connect(db)
    text = vault_refresh.strategy_evidence(conn, ('PM_loser',))
    conn.close()
    assert 'PROVISIONAL' not in text


def test_a_strategy_that_never_traded_is_not_tested_not_failed(db):
    """Convention 11. This is the distinction the whole loop rests on."""
    conn = vault_refresh.connect(db)
    text = vault_refresh.strategy_evidence(conn, ('PM_never_fires',))
    conn.close()
    assert 'NOT_TESTED' in text
    assert 'it is not a failure' in text
    assert 'NO CLOSED TRADES' in text


def test_skip_reasons_reach_the_evidence(db):
    """A gate count is not a loss, but the model has to be able to see it."""
    conn = vault_refresh.connect(db)
    text = vault_refresh.strategy_evidence(conn, ('PM_never_fires',))
    conn.close()
    assert 'wallet_address_unresolved' in text
    assert '50' in text


def test_the_cycle_block_separates_never_traded_from_lost(db):
    conn = vault_refresh.connect(db)
    text = vault_refresh.cycle_evidence(conn)
    conn.close()
    assert 'Evaluated but NEVER traded' in text
    assert 'PM_never_fires' in text.split('Evaluated but NEVER traded')[1]
    # A strategy that traded must NOT appear in the never-traded section.
    never_section = text.split('Evaluated but NEVER traded')[1].split('##')[0]
    assert 'PM_loser' not in never_section


def test_the_cycle_block_carries_the_equity_path(db):
    conn = vault_refresh.connect(db)
    text = vault_refresh.cycle_evidence(conn)
    conn.close()
    assert 'Equity path' in text
    assert 'acted' in text


def test_individual_trades_are_shown_not_just_aggregates(db):
    """An aggregate cannot tell you a stop fired at 0.00 on a binary."""
    conn = vault_refresh.connect(db)
    text = vault_refresh.strategy_evidence(conn, ('PM_tiny_winner',))
    conn.close()
    assert 'btc-updown-5m-1' in text
    assert 'hold_s' in text
    assert '0.31' in text


def test_the_evidence_says_when_it_was_pulled(db):
    """A number with no timestamp is a number that will be cited when stale."""
    conn = vault_refresh.connect(db)
    text = vault_refresh.strategy_evidence(conn, ('PM_loser',))
    conn.close()
    assert 'Data pulled at:' in text
    assert 'Closed-trade window:' in text


# ---------------------------------------------------------------------------
# The note registry
# ---------------------------------------------------------------------------

def test_every_note_targets_an_explicit_filename():
    """A refresh must REPLACE a note, not grow a dated copy beside it.

    Without a pinned filename, `write_strategy_lesson` defaults to a
    date-stamped name and every re-run leaves another near-duplicate in the
    vault, which then gets read back as four separate lessons saying the same
    thing with four different numbers.
    """
    for key, spec in vault_refresh.NOTES.items():
        assert spec.get('filename'), key
        assert spec['filename'].endswith('.md'), key


def test_the_targeted_filenames_are_the_ones_raven_wrote():
    """The five notes named in the instruction file, pinned by name."""
    targets = {spec['filename'] for spec in vault_refresh.NOTES.values()}
    assert targets == {
        '2026-08-18-fair-value-arb-spread-problem.md',
        '2026-08-18-corridor-pair-works.md',
        'fair_value_arb.md',
        'corridor_pair_live.md',
        '2026-08-18-cycle-001-day-1-lessons.md',
    }


def test_each_note_kind_is_one_the_writer_knows(monkeypatch, db, tmp_path):
    monkeypatch.setattr(
        vault_refresh.vault_writer.llm_client, 'run_task',
        lambda task, prompt, **kw: llm_client.LLMResult(
            '# Note\n' + 'x' * 400, 'opus', task, 1.0))
    monkeypatch.setattr(vault_reader, 'render_context', lambda *a, **k: 'CTX')
    conn = vault_refresh.connect(db)
    try:
        for key in vault_refresh.NOTES:
            result = vault_refresh.refresh(key, conn,
                                           out_dir=str(tmp_path / 'out'))
            assert result.written, key
            assert result.path.endswith(
                vault_refresh.NOTES[key]['filename']), key
    finally:
        conn.close()


def test_a_refresh_cannot_write_into_the_real_vault_from_a_test(
        monkeypatch, db, tmp_path):
    monkeypatch.setattr(
        vault_refresh.vault_writer.llm_client, 'run_task',
        lambda task, prompt, **kw: llm_client.LLMResult(
            '# Note\n' + 'x' * 400, 'opus', task, 1.0))
    monkeypatch.setattr(vault_reader, 'render_context', lambda *a, **k: 'CTX')
    conn = vault_refresh.connect(db)
    try:
        result = vault_refresh.refresh('cycle-001', conn,
                                       out_dir=str(tmp_path / 'out'))
    finally:
        conn.close()
    assert vault_refresh.vault_writer.VAULT_ROOT not in result.path


def test_the_connection_is_read_only(db):
    """The shadow loop owns this file. We are a guest on it."""
    conn = vault_refresh.connect(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute('DELETE FROM positions')
    finally:
        conn.close()


def test_evidence_only_mode_calls_no_model(monkeypatch, db, capsys):
    def _forbidden(*_a, **_k):
        raise AssertionError('a model turn was spawned in --evidence-only')

    monkeypatch.setattr(vault_refresh.vault_writer.llm_client, 'run_task',
                        _forbidden)
    rc = vault_refresh.main(['--note', 'cycle-001', '--evidence-only',
                            '--db', db])
    assert rc == 0
    assert 'Equity path' in capsys.readouterr().out
