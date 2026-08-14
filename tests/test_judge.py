"""Tests for agents/judge.py: the evidence-pack emitter.

Synthetic graveyard-entries lists only - no dependency on the real
multi-GB graveyard file. Field shapes mirror VResult.to_report() in
backtest/vectorized_harness.py (pf=None encodes infinite PF, NOT_TESTED
rows carry not_tested_reason instead of trade metrics).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.judge as judge_mod
from agents.judge import (
    GraveyardUnreadable, _confidence_label, build_evidence_pack,
    emit_evidence_pack, load_graveyard,
)


def _entry(strategy, ticker='AAPL', timeframe='1d', exit_config='fixed_2r',
          trades=40, verdict='PASS', pf=1.3, win_rate=0.55, twin_percentile=0.7,
          cost_model_version='2026-08-13', asset_class='EQUITY', **kw):
    e = {
        'strategy': strategy, 'ticker': ticker, 'timeframe': timeframe,
        'exit_config': exit_config, 'trades': trades, 'verdict': verdict,
        'pf': pf, 'gross_pf': pf, 'win_rate': win_rate,
        'total_pnl_usd': 12.5, 'buy_hold_pct': 1.0, 'buy_hold_pnl_usd': 1.0,
        'random_twin_pf': 1.05, 'twin_percentile': twin_percentile,
        'gate_version': 2, 'cost_model_version': cost_model_version,
        'asset_class': asset_class, 'instrument': ticker,
    }
    e.update(kw)
    return e


def _write_graveyard(tmp_path, entries, name='g.json'):
    path = tmp_path / name
    path.write_text(json.dumps({'entries': entries}))
    return str(path)


# ---------- load_graveyard ----------

def test_load_graveyard_missing_file_returns_empty_list():
    assert load_graveyard('/nonexistent/path/does-not-exist.json') == []


def test_load_graveyard_malformed_json_raises_rather_than_reporting_empty(tmp_path):
    # Was: asserted []. That conflated "could not read the evidence" with
    # "there is no evidence" and produced a confident DURABLE/0-entry pack
    # against a live 287k-entry graveyard mid-write. attempts=1 keeps the
    # test fast (no retry sleeps).
    path = tmp_path / 'bad.json'
    path.write_text('{not valid json')
    with pytest.raises(GraveyardUnreadable):
        load_graveyard(str(path), attempts=1)


def test_load_graveyard_retries_then_succeeds_on_partial_write(tmp_path):
    """A truncated read that becomes valid on a later attempt must succeed."""
    path = tmp_path / 'partial.json'
    entries = [_entry('grid')]
    good = json.dumps({'entries': entries})
    path.write_text(good[:20])  # truncated, as a mid-`json.dump` read would be

    calls = {'n': 0}
    real_sleep = judge_mod.time.sleep

    def _fix_it_on_second_try(_seconds):
        calls['n'] += 1
        path.write_text(good)   # the sweep finishes its save

    judge_mod.time.sleep = _fix_it_on_second_try
    try:
        assert load_graveyard(str(path), attempts=3, delay=0) == entries
        assert calls['n'] == 1
    finally:
        judge_mod.time.sleep = real_sleep


def test_failed_subsection_is_reported_not_silently_nulled(tmp_path):
    """A summary that cannot be produced must say so in `degraded`.

    distinct_findings comes from that summary and convention 2 requires
    citing it, so a silent None is how a pack ends up with no
    multiple-comparisons correction and nothing marking its absence.
    """
    path = _write_graveyard(tmp_path, [_entry('grid')])
    real = judge_mod.summarize_graveyard.summarize

    def _boom(_p, *a, **kw):
        raise json.JSONDecodeError('truncated', '{', 0)

    judge_mod.summarize_graveyard.summarize = _boom
    real_sleep = judge_mod.time.sleep
    judge_mod.time.sleep = lambda _s: None   # don't burn the retry backoff
    try:
        pack = build_evidence_pack(str(path), validation_fn=lambda: True)
    finally:
        judge_mod.summarize_graveyard.summarize = real
        judge_mod.time.sleep = real_sleep

    assert pack['distinct_findings'] is None
    assert pack['degraded'], 'a missing summary must be reported, not silent'
    assert any('distinct_findings' in d for d in pack['degraded'])


def test_unreadable_graveyard_is_never_durable(tmp_path):
    """Green harness must not launder an unparseable graveyard into DURABLE."""
    path = tmp_path / 'bad.json'
    path.write_text('{not valid json')
    pack = build_evidence_pack(str(path), validation_fn=lambda: True)
    assert pack['status'] == 'UNREADABLE'
    assert pack['entries_total'] == 0
    assert 'could not be parsed' in pack['note']


def test_load_graveyard_reads_entries(tmp_path):
    entries = [_entry('grid')]
    path = _write_graveyard(tmp_path, entries)
    assert load_graveyard(path) == entries


# ---------- confidence label boundaries ----------

def test_confidence_label_boundaries():
    assert _confidence_label(29) == 'cold_start'
    assert _confidence_label(30) == 'reviewable_not_promotable'
    assert _confidence_label(49) == 'reviewable_not_promotable'
    assert _confidence_label(50) == 'evaluable'


# ---------- build_evidence_pack: status gating ----------

def test_status_durable_when_validation_true(tmp_path):
    entries = [_entry('grid', trades=60)]
    path = _write_graveyard(tmp_path, entries)
    pack = build_evidence_pack(path, validation_fn=lambda: True)
    assert pack['harness_validated'] is True
    assert pack['status'] == 'DURABLE'
    assert all(row['status'] == 'DURABLE' for row in pack['strategies'])


def test_status_provisional_when_validation_false(tmp_path):
    entries = [_entry('grid', trades=60)]
    path = _write_graveyard(tmp_path, entries)
    pack = build_evidence_pack(path, validation_fn=lambda: False)
    assert pack['harness_validated'] is False
    assert pack['status'] == 'PROVISIONAL'
    # SOUL: the flag must be stamped on every per-strategy row too, never
    # silently dropped.
    assert pack['strategies'], 'need at least one row to assert the stamp on'
    assert all(row['status'] == 'PROVISIONAL' for row in pack['strategies'])


# ---------- NOT_TESTED handling ----------

def test_not_tested_entry_stays_not_tested(tmp_path):
    entries = [
        _entry('C2', trades=0, verdict='NOT_TESTED', pf=None, win_rate=None,
              twin_percentile=None, not_tested_reason='needs 840 bars, scan window is 260'),
    ]
    path = _write_graveyard(tmp_path, entries)
    pack = build_evidence_pack(path, validation_fn=lambda: True)
    rows = {r['strategy']: r for r in pack['strategies']}
    row = rows['C2']
    assert row['verdict'] == 'NOT_TESTED'
    assert row['not_tested_reason'] == 'needs 840 bars, scan window is 260'
    assert row['n_trades'] == 0
    # Never converted into a failure.
    assert row['confidence'] is None
    assert row['observed_best_pf'] is None


def test_not_tested_never_pooled_with_tested_rows_of_other_strategies(tmp_path):
    entries = [
        _entry('C2', trades=0, verdict='NOT_TESTED', pf=None, win_rate=None,
              twin_percentile=None, not_tested_reason='needs 840 bars'),
        _entry('grid', trades=60, verdict='PASS'),
    ]
    path = _write_graveyard(tmp_path, entries)
    pack = build_evidence_pack(path, validation_fn=lambda: True)
    rows = {r['strategy']: r for r in pack['strategies']}
    assert rows['C2']['verdict'] == 'NOT_TESTED'
    assert rows['grid']['verdict'] is None
    assert rows['grid']['n_trades'] == 60
    assert rows['grid']['confidence'] == 'evaluable'


# ---------- shape of build_evidence_pack ----------

def test_evidence_pack_shape(tmp_path):
    entries = [
        _entry('grid', ticker='AAPL', trades=60, verdict='PASS', pf=1.4,
              twin_percentile=0.8),
        _entry('grid', ticker='MSFT', trades=40, verdict='FAIL', pf=0.9,
              twin_percentile=0.3),
        _entry('bullish_engulfing', ticker='BTC/USDT', asset_class='CRYPTO',
              trades=4, verdict='FAIL', pf=None, win_rate=1.0, twin_percentile=1.0),
    ]
    path = _write_graveyard(tmp_path, entries)
    pack = build_evidence_pack(path, validation_fn=lambda: True)

    for key in ('status', 'harness_validated', 'graveyard', 'strategy_filter',
               'entries_total', 'silent_assertions', 'degraded',
               'distinct_findings', 'graveyard_summary', 'strategies',
               'asset_class_breakdown', 'expected_best_by_chance'):
        assert key in pack, f'missing top-level key: {key}'

    # a clean read degrades nothing
    assert pack['degraded'] is None

    assert pack['entries_total'] == 3
    assert isinstance(pack['silent_assertions'], dict)
    assert 'results' in pack['silent_assertions']
    assert isinstance(pack['distinct_findings'], dict)
    assert isinstance(pack['strategies'], list) and len(pack['strategies']) == 2
    assert isinstance(pack['asset_class_breakdown'], list)

    ebc = pack['expected_best_by_chance']
    assert ebc['n_strategies_tested'] == 2
    assert 'grid' in ebc['observed_best_pf_by_strategy']
    assert ebc['observed_best_pf_by_strategy']['grid'] == 1.4

    grid_row = next(r for r in pack['strategies'] if r['strategy'] == 'grid')
    assert grid_row['n_trades'] == 100
    assert grid_row['observed_best_pf'] == 1.4
    assert grid_row['twin_percentile_median'] is not None
    assert grid_row['cost_model_version'] == '2026-08-13'
    assert grid_row['asset_class'] == 'EQUITY'

    # infinite-PF row (pf=None with trades>0) is surfaced, not dropped.
    bull_row = next(r for r in pack['strategies'] if r['strategy'] == 'bullish_engulfing')
    assert bull_row['infinite_pf_row_count'] == 1
    assert bull_row['observed_best_pf'] is None  # no finite pf available


def test_distinct_findings_never_reports_raw_pass_rows_as_headline(tmp_path):
    # Same strategy/ticker/timeframe, many exit configs: one finding, not N.
    entries = [_entry('grid', exit_config=f'cfg{i}', trades=30) for i in range(9)]
    path = _write_graveyard(tmp_path, entries)
    pack = build_evidence_pack(path, validation_fn=lambda: True)
    assert pack['graveyard_summary']['raw_pass_rows'] == 9
    assert pack['distinct_findings']['strategy_x_ticker_x_timeframe'] == 1


# ---------- degrades gracefully ----------

def test_build_evidence_pack_missing_graveyard_degrades(tmp_path):
    missing = str(tmp_path / 'does-not-exist.json')
    pack = build_evidence_pack(missing, validation_fn=lambda: True)
    assert pack['entries_total'] == 0
    assert pack['strategies'] == []
    assert pack['silent_assertions'] is None
    assert pack['status'] == 'DURABLE'
    assert 'note' in pack


def test_build_evidence_pack_empty_entries_degrades(tmp_path):
    path = _write_graveyard(tmp_path, [])
    pack = build_evidence_pack(path, validation_fn=lambda: True)
    assert pack['entries_total'] == 0
    assert pack['strategies'] == []


def test_build_evidence_pack_strategy_filter_with_no_matches_degrades(tmp_path):
    entries = [_entry('grid', trades=60)]
    path = _write_graveyard(tmp_path, entries)
    pack = build_evidence_pack(path, strategy='nope', validation_fn=lambda: True)
    assert pack['entries_total'] == 0
    assert pack['strategy_filter'] == 'nope'


def test_provisional_status_survives_missing_graveyard(tmp_path):
    missing = str(tmp_path / 'does-not-exist.json')
    pack = build_evidence_pack(missing, validation_fn=lambda: False)
    assert pack['status'] == 'PROVISIONAL'
    assert pack['harness_validated'] is False


# ---------- strategy filter ----------

def test_strategy_filter_only_returns_that_strategy(tmp_path):
    entries = [_entry('grid', trades=60), _entry('breakout', trades=60)]
    path = _write_graveyard(tmp_path, entries)
    pack = build_evidence_pack(path, strategy='grid', validation_fn=lambda: True)
    assert pack['entries_total'] == 1
    assert [r['strategy'] for r in pack['strategies']] == ['grid']


# ---------- emit_evidence_pack ----------

def test_emit_evidence_pack_writes_valid_json_and_returns_same_dict(tmp_path):
    entries = [_entry('grid', trades=60)]
    graveyard_path = _write_graveyard(tmp_path, entries)
    out_path = tmp_path / 'nested' / 'judge_evidence_pack.json'

    returned = emit_evidence_pack(graveyard_path, str(out_path), validation_fn=lambda: True)

    assert out_path.exists()
    with open(out_path) as f:
        on_disk = json.load(f)
    assert on_disk == returned
    assert returned['status'] == 'DURABLE'


def test_emit_evidence_pack_missing_graveyard_still_writes_degenerate_pack(tmp_path):
    missing = str(tmp_path / 'nope.json')
    out_path = tmp_path / 'pack.json'
    returned = emit_evidence_pack(missing, str(out_path), validation_fn=lambda: True)
    assert out_path.exists()
    with open(out_path) as f:
        on_disk = json.load(f)
    assert on_disk['entries_total'] == 0
    assert on_disk == returned
