"""Tests for backtest/purge_stale_futures.py (D-254).

This tool deletes rows from the real graveyard, so the things worth pinning
down are the destructive edges: that it only ever touches contract
instruments, that a dry run is genuinely inert, that it cannot fire while a
sweep would clobber it, and that a crash mid-write cannot leave a half file.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest.purge_stale_futures as purge

# Captured before the autouse fixture stubs it out, so the fail-closed test
# can exercise the real implementation.
_REAL_SWEEP_IS_RUNNING = purge.sweep_is_running


def _entry(ticker, asset_class, strategy='grid', verdict='FAIL', **kw):
    e = {
        'ticker': ticker, 'asset_class': asset_class, 'strategy': strategy,
        'timeframe': '1h', 'exit_config': 'fixed_2r', 'verdict': verdict,
        'trades': 40, 'pf': 1.1, 'cost_model_version': '2026-08-13',
        'inversion_flagged': False,
    }
    e.update(kw)
    return e


def _mixed_entries():
    return [
        _entry('AAPL', 'EQUITY'),
        _entry('SPY', 'ETF'),
        _entry('BTC_USD', 'CRYPTO'),
        _entry('ES_F', 'FUTURES'),
        _entry('CL_F', 'FUTURES', verdict='PASS'),
        _entry('SPX', 'OPTIONS'),
    ]


def _write(tmp_path, entries, name='g.json'):
    path = tmp_path / name
    path.write_text(json.dumps({
        'generated': '2026-08-13 16:00:00',
        'total_tests': len(entries),
        'passed': sum(1 for e in entries if e['verdict'] == 'PASS'),
        'failed': sum(1 for e in entries if e['verdict'] != 'PASS'),
        'inversions_flagged': 0,
        'strategies_tested': 49,
        'exit_configs_tested': 11,
        'entries': entries,
    }))
    return str(path)


@pytest.fixture(autouse=True)
def _no_sweep(monkeypatch):
    """Default every test to 'no sweep running'; the guard test overrides."""
    monkeypatch.setattr(purge, 'sweep_is_running', lambda: False)


def _run(path, *extra):
    argv = ['purge_stale_futures.py', '--graveyard', path, *extra]
    old = sys.argv
    sys.argv = argv
    try:
        purge.main()
    finally:
        sys.argv = old


# ---------- scope: only contract instruments ----------

def test_purges_only_contract_instruments(tmp_path):
    path = _write(tmp_path, _mixed_entries())
    _run(path, '--apply')
    kept = json.load(open(path))['entries']
    classes = sorted({e['asset_class'] for e in kept})
    assert classes == ['CRYPTO', 'EQUITY', 'ETF']
    assert len(kept) == 3


def test_equity_etf_crypto_rows_are_untouched_byte_for_byte(tmp_path):
    """The D-249 sizing path is contract-only; non-contract rows must survive
    unmodified, not merely survive."""
    entries = _mixed_entries()
    path = _write(tmp_path, entries)
    before = [e for e in entries if e['asset_class'] in ('EQUITY', 'ETF', 'CRYPTO')]
    _run(path, '--apply')
    assert json.load(open(path))['entries'] == before


def test_graveyard_with_no_contract_rows_is_left_alone(tmp_path):
    entries = [_entry('AAPL', 'EQUITY'), _entry('SPY', 'ETF')]
    path = _write(tmp_path, entries)
    original = open(path).read()
    _run(path, '--apply')
    assert open(path).read() == original


# ---------- dry run is inert ----------

def test_dry_run_writes_nothing(tmp_path):
    path = _write(tmp_path, _mixed_entries())
    original = open(path).read()
    _run(path)                      # no --apply
    assert open(path).read() == original


def test_dry_run_creates_no_backup(tmp_path, monkeypatch):
    archive = tmp_path / 'archive'
    monkeypatch.setattr(purge, 'ARCHIVE_DIR', str(archive))
    path = _write(tmp_path, _mixed_entries())
    _run(path)
    assert not archive.exists()


# ---------- the running-sweep guard ----------

def test_refuses_while_sweep_is_running(tmp_path, monkeypatch):
    """The sweep rewrites the whole graveyard after every ticker, so a purge
    landing mid-sweep is clobbered on its next save."""
    monkeypatch.setattr(purge, 'sweep_is_running', lambda: True)
    path = _write(tmp_path, _mixed_entries())
    original = open(path).read()
    with pytest.raises(SystemExit) as exc:
        _run(path, '--apply')
    assert exc.value.code == 2
    assert open(path).read() == original, 'refusal must not have written'


def test_force_overrides_the_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(purge, 'sweep_is_running', lambda: True)
    monkeypatch.setattr(purge, 'ARCHIVE_DIR', str(tmp_path / 'archive'))
    path = _write(tmp_path, _mixed_entries())
    _run(path, '--apply', '--force')
    assert len(json.load(open(path))['entries']) == 3


def test_undetectable_sweep_is_assumed_running(monkeypatch):
    """pgrep failing must fail closed: refusing a safe purge is cheap,
    clobbering a 6-hour sweep is not."""
    def _boom(*a, **kw):
        raise OSError('pgrep missing')
    monkeypatch.setattr(purge.subprocess, 'run', _boom)
    assert _REAL_SWEEP_IS_RUNNING() is True


# ---------- backup and atomicity ----------

def test_apply_backs_up_the_original_first(tmp_path, monkeypatch):
    archive = tmp_path / 'archive'
    monkeypatch.setattr(purge, 'ARCHIVE_DIR', str(archive))
    path = _write(tmp_path, _mixed_entries())
    original = json.load(open(path))
    _run(path, '--apply')

    backups = list(archive.glob('v0_graveyard_full.pre-D249-purge.*.json'))
    assert len(backups) == 1
    assert json.load(open(backups[0])) == original, 'backup must be the pre-purge file'


def test_apply_leaves_no_tmp_file_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(purge, 'ARCHIVE_DIR', str(tmp_path / 'archive'))
    path = _write(tmp_path, _mixed_entries())
    _run(path, '--apply')
    assert not os.path.exists(path + '.tmp')


# ---------- header counters are rebuilt ----------

def test_summary_counters_are_recomputed_for_survivors(tmp_path, monkeypatch):
    monkeypatch.setattr(purge, 'ARCHIVE_DIR', str(tmp_path / 'archive'))
    entries = _mixed_entries() + [_entry('MSFT', 'EQUITY', verdict='PASS')]
    path = _write(tmp_path, entries)
    _run(path, '--apply')

    out = json.load(open(path))
    assert out['total_tests'] == 4          # 3 non-contract + the new PASS
    assert out['passed'] == 1               # CL_F's PASS was purged, MSFT's kept
    assert out['failed'] == 3
    assert len(out['entries']) == out['total_tests']


def test_unrelated_header_fields_survive(tmp_path, monkeypatch):
    monkeypatch.setattr(purge, 'ARCHIVE_DIR', str(tmp_path / 'archive'))
    path = _write(tmp_path, _mixed_entries())
    _run(path, '--apply')
    out = json.load(open(path))
    assert out['strategies_tested'] == 49
    assert out['exit_configs_tested'] == 11


# ---------- partial reads ----------

def test_load_retries_a_partial_write_then_succeeds(tmp_path, monkeypatch):
    path = tmp_path / 'partial.json'
    good = json.dumps({'entries': [_entry('AAPL', 'EQUITY')]})
    path.write_text(good[:15])          # truncated, as a mid-dump read would be

    def _fix(_s):
        path.write_text(good)
    monkeypatch.setattr(purge.time, 'sleep', _fix)

    data = purge.load_graveyard(str(path), attempts=3, delay=0)
    assert len(data['entries']) == 1


def test_load_gives_up_loudly_rather_than_returning_empty(tmp_path, monkeypatch):
    """Never silently treat an unreadable graveyard as an empty one - purging
    'all contract rows' from a file that failed to parse would look like a
    successful no-op."""
    path = tmp_path / 'bad.json'
    path.write_text('{not valid json')
    monkeypatch.setattr(purge.time, 'sleep', lambda _s: None)
    with pytest.raises(SystemExit):
        purge.load_graveyard(str(path), attempts=2, delay=0)


def test_missing_graveyard_exits_rather_than_purging_nothing(tmp_path):
    with pytest.raises(SystemExit):
        _run(str(tmp_path / 'nope.json'), '--apply')
