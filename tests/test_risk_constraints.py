"""Tests for the deterministic risk module (D-342 R2).

Covers the matrix the directive names: each constraint binds and names itself,
every denial writes a `risk_events` row, the halt stays single-path, no
probability reaches the evaluator, and same-epoch BTC/ETH/SOL are treated as one
correlated bet.
"""
import json
import sqlite3

import pytest

from engine.risk import constraints as C
from engine.risk import events as E


FLAT = C.EquityState(current_usd=1000.0, peak_usd=1000.0)


def ex(slug, window_ts, notional):
    return C.Exposure.from_slug(slug, window_ts, notional)


@pytest.fixture()
def conn():
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    con.execute('CREATE TABLE risk_events (id TEXT PRIMARY KEY, ts INTEGER '
                'NOT NULL, type TEXT NOT NULL, details_json TEXT NOT NULL)')
    yield con
    con.close()


# ---------------------------------------------------------------------------
# Family resolution - the load-bearing correlation claim
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('slug', [
    'btc-updown-5m-1787022000',
    'eth-updown-5m-1787022000',
    'sol-updown-5m-1787022000',
    'btc-updown-15m-1787022000',
    'bitcoin-up-or-down-5m-1787022000',
    'solana-up-or-down-5m-1787022000',
])
def test_crypto_updown_slugs_share_one_family(slug):
    """btc, eth and sol Up/Down are ONE family. Both slug spellings included."""
    assert C.asset_family_for_slug(slug) == C.CRYPTO_UPDOWN_FAMILY


@pytest.mark.parametrize('slug', [
    None, '', 'trump-2028-winner', 'btc-something-else', 'nfl-chiefs-win',
])
def test_unknown_slugs_get_their_own_named_family(slug):
    """An unclassified slug is NAMED, never guessed into a real family."""
    assert C.asset_family_for_slug(slug) == C.UNKNOWN_FAMILY


# ---------------------------------------------------------------------------
# The per-event cap: the constraint that is genuinely new
# ---------------------------------------------------------------------------

def test_same_epoch_btc_eth_sol_stack_into_one_correlated_bet():
    """The whole point of the per-event cap: three assets, one epoch, one cap.

    Each leg is under the per-trade cap and the total is under the aggregate
    cap, so ONLY the per-event constraint can catch this.
    """
    window = 1787022000
    book = [ex('btc-updown-5m-{}'.format(window), window, 10.0),
            ex('eth-updown-5m-{}'.format(window), window, 10.0),
            ex('sol-updown-5m-{}'.format(window), window, 10.0)]
    candidate = ex('btc-updown-15m-{}'.format(window), window, 5.0)

    decision = C.check(book, candidate, FLAT)

    assert not decision.allowed
    assert decision.constraint == C.CONSTRAINT_PER_EVENT
    assert decision.detail['asset_family'] == C.CRYPTO_UPDOWN_FAMILY
    assert decision.detail['event_open_usd'] == pytest.approx(30.0)
    assert decision.detail['event_after_usd'] == pytest.approx(35.0)
    # Under the OTHER two caps - so nothing but the per-event cap caught it.
    assert decision.detail['candidate_notional_usd'] <= C.DEFAULT_LIMITS.per_trade_notional_usd
    assert 35.0 <= C.DEFAULT_LIMITS.aggregate_notional_usd


def test_different_epochs_do_not_stack():
    """Same family, DIFFERENT windows are separate bets and separate buckets."""
    book = [ex('btc-updown-5m-1787022000', 1787022000, 10.0),
            ex('eth-updown-5m-1787022300', 1787022300, 10.0),
            ex('sol-updown-5m-1787022600', 1787022600, 10.0)]
    candidate = ex('btc-updown-5m-1787022900', 1787022900, 10.0)

    assert C.check(book, candidate, FLAT).allowed


def test_missing_epoch_is_its_own_bucket_not_epoch_zero():
    """`None` window must not pool with every other undated position."""
    book = [C.Exposure(C.CRYPTO_UPDOWN_FAMILY, None, 25.0)]
    candidate = C.Exposure(C.CRYPTO_UPDOWN_FAMILY, 0, 10.0)

    assert C.check(book, candidate, FLAT).allowed


# ---------------------------------------------------------------------------
# Allow/deny matrix - each constraint binds and names itself
# ---------------------------------------------------------------------------

def test_allows_a_clean_candidate_on_an_empty_book():
    decision = C.check([], ex('btc-updown-5m-1787022000', 1787022000, 5.0), FLAT)
    assert decision.allowed
    assert decision.constraint is None
    assert decision.detail['open_total_usd'] == pytest.approx(0.0)


def test_per_trade_cap_binds():
    candidate = ex('btc-updown-5m-1787022000', 1787022000, 10.01)
    decision = C.check([], candidate, FLAT)
    assert not decision.allowed
    assert decision.constraint == C.CONSTRAINT_PER_TRADE
    assert decision.detail['limit_usd'] == C.DEFAULT_LIMITS.per_trade_notional_usd


def test_aggregate_cap_binds_across_unrelated_events():
    """Spread across distinct epochs so the per-event cap cannot fire first."""
    book = [ex('btc-updown-5m-{}'.format(1787022000 + i * 300),
               1787022000 + i * 300, 10.0) for i in range(6)]
    candidate = ex('btc-updown-5m-1787024000', 1787024000, 5.0)

    decision = C.check(book, candidate, FLAT)

    assert not decision.allowed
    assert decision.constraint == C.CONSTRAINT_AGGREGATE
    assert decision.detail['total_after_usd'] == pytest.approx(65.0)


def test_drawdown_binds_and_requests_a_halt():
    equity = C.EquityState(current_usd=700.0, peak_usd=1000.0)  # 30% > 25%
    decision = C.check([], ex('btc-updown-5m-1787022000', 1787022000, 1.0), equity)

    assert not decision.allowed
    assert decision.constraint == C.CONSTRAINT_DRAWDOWN
    assert decision.halt_required is True
    assert decision.detail['drawdown_frac'] == pytest.approx(0.30)


def test_drawdown_exactly_at_the_limit_does_not_bind():
    equity = C.EquityState(current_usd=750.0, peak_usd=1000.0)  # exactly 25%
    assert C.check([], ex('btc-updown-5m-1', 1, 1.0), equity).allowed


def test_only_the_drawdown_ever_requests_a_halt():
    """A cap breach must never engage the kill switch."""
    for candidate, book in [
        (ex('btc-updown-5m-1', 1, 999.0), []),
        (ex('btc-updown-5m-1', 1, 5.0), [ex('eth-updown-5m-1', 1, 29.0)]),
    ]:
        decision = C.check(book, candidate, FLAT)
        assert not decision.allowed
        assert decision.halt_required is False


# ---------------------------------------------------------------------------
# Failure direction: unreadable state fails CLOSED (convention 11)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1.0, 'abc', None])
def test_unreadable_open_exposure_denies_rather_than_undercounting(bad):
    book = [ex('btc-updown-5m-1', 1, 1.0), C.Exposure(C.CRYPTO_UPDOWN_FAMILY, 2, bad)]
    decision = C.check(book, ex('btc-updown-5m-3', 3, 1.0), FLAT)

    assert not decision.allowed
    assert decision.constraint == C.CONSTRAINT_UNREADABLE
    assert decision.detail['seen'] == 2
    assert decision.detail['unreadable'] == 1
    assert decision.detail['counted'] == 1


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1.0, 'abc', None])
def test_unreadable_candidate_is_denied(bad):
    decision = C.check([], C.Exposure(C.CRYPTO_UPDOWN_FAMILY, 1, bad), FLAT)
    assert not decision.allowed
    assert decision.constraint == C.CONSTRAINT_INVALID_CANDIDATE


@pytest.mark.parametrize('peak', [0.0, -5.0, float('nan')])
def test_unmeasurable_equity_denies_rather_than_reading_as_no_drawdown(peak):
    equity = C.EquityState(current_usd=100.0, peak_usd=peak)
    assert equity.drawdown_frac() is None
    decision = C.check([], ex('btc-updown-5m-1', 1, 1.0), equity)
    assert not decision.allowed
    assert decision.constraint == C.CONSTRAINT_UNREADABLE


def test_equity_above_peak_is_zero_drawdown_not_negative():
    assert C.EquityState(1200.0, 1000.0).drawdown_frac() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Purity: no probability, no clock, no I/O
# ---------------------------------------------------------------------------

def test_no_probability_input_reaches_the_evaluator():
    """The evaluator's signature must carry no forecast, by design (D-342 R2).

    A probability argument is how a model-free control quietly becomes
    model-dependent, so this is asserted structurally rather than trusted.
    """
    import inspect
    params = set(inspect.signature(C.check).parameters)
    assert params == {'open_positions', 'candidate', 'equity', 'limits'}

    forecast_words = ('prob', 'kelly', 'edge', 'fair_value', 'confidence',
                      'win_p', 'p_win', 'forecast')
    for cls in (C.Exposure, C.EquityState, C.Limits):
        for name in inspect.signature(cls).parameters:
            assert not any(w in name.lower() for w in forecast_words), (cls, name)

    source = inspect.getsource(C)
    for banned in ('import time', 'import sqlite3', 'import os',
                   'datetime.now', 'time.time'):
        assert banned not in source, banned


def test_check_is_deterministic_and_does_not_mutate_its_inputs():
    book = [ex('btc-updown-5m-1', 1, 10.0), ex('eth-updown-5m-1', 1, 10.0)]
    snapshot = list(book)
    candidate = ex('sol-updown-5m-1', 1, 9.0)

    first = C.check(book, candidate, FLAT)
    second = C.check(book, candidate, FLAT)

    assert (first.allowed, first.constraint) == (second.allowed, second.constraint)
    assert first.detail == second.detail
    assert book == snapshot


# ---------------------------------------------------------------------------
# Recording: every denial writes a row
# ---------------------------------------------------------------------------

def test_every_denial_writes_exactly_one_risk_events_row(conn):
    candidate = ex('btc-updown-5m-1', 1, 50.0)
    decision = E.evaluate_and_record(conn, [], candidate, FLAT)

    assert not decision.allowed
    rows = conn.execute('SELECT * FROM risk_events').fetchall()
    assert len(rows) == 1
    assert rows[0]['type'] == E.RISK_EVENT_TYPE
    details = json.loads(rows[0]['details_json'])
    assert details['constraint'] == C.CONSTRAINT_PER_TRADE
    assert details['limit_usd'] == C.DEFAULT_LIMITS.per_trade_notional_usd
    assert 'reason' in details


def test_an_allow_writes_no_row(conn):
    decision = E.evaluate_and_record(conn, [], ex('btc-updown-5m-1', 1, 5.0), FLAT)
    assert decision.allowed
    assert conn.execute('SELECT COUNT(*) FROM risk_events').fetchone()[0] == 0


def test_record_denial_refuses_an_allowing_decision(conn):
    with pytest.raises(ValueError):
        E.record_denial(conn, C.allow())


def test_denials_by_constraint_is_the_kill_condition_harness(conn):
    """The named harness: group `risk_events` by constraint name."""
    for _ in range(7):
        E.evaluate_and_record(conn, [], ex('btc-updown-5m-1', 1, 50.0), FLAT)
    book = [ex('btc-updown-5m-2', 2, 10.0), ex('eth-updown-5m-2', 2, 10.0),
            ex('sol-updown-5m-2', 2, 10.0)]
    E.evaluate_and_record(conn, book, ex('btc-updown-15m-2', 2, 5.0), FLAT)

    counts = E.denials_by_constraint(conn, since_ts_ms=0)

    assert counts[C.CONSTRAINT_PER_TRADE] == 7
    assert counts[C.CONSTRAINT_PER_EVENT] == 1
    # Constraints that never bound report an explicit 0, not a missing key.
    assert counts[C.CONSTRAINT_AGGREGATE] == 0
    assert set(C.ALL_CONSTRAINTS).issubset(counts)
    # 7 > 5, so the module is NOT decorative on this tape.
    assert E.is_decorative(counts) is False


def test_is_decorative_when_nothing_binds_enough(conn):
    for _ in range(E.DECORATIVE_BINDING_THRESHOLD):
        E.evaluate_and_record(conn, [], ex('btc-updown-5m-1', 1, 50.0), FLAT)
    counts = E.denials_by_constraint(conn, since_ts_ms=0)
    assert counts[C.CONSTRAINT_PER_TRADE] == E.DECORATIVE_BINDING_THRESHOLD
    assert E.is_decorative(counts) is True


# ---------------------------------------------------------------------------
# Halt routing: one definition, `engine.halt`, never a second path
# ---------------------------------------------------------------------------

@pytest.fixture()
def halt_file(tmp_path, monkeypatch):
    """Point the ONE kill switch at a temp path, as `engine/halt.py` instructs."""
    import engine.halt as halt
    path = tmp_path / 'HALT'
    monkeypatch.setattr(halt, 'HALT_FILE', str(path))
    assert not halt.is_halted()
    return path


def test_drawdown_breach_engages_the_one_kill_switch(conn, halt_file):
    import engine.halt as halt
    equity = C.EquityState(current_usd=600.0, peak_usd=1000.0)  # 40% drawdown

    decision = E.evaluate_and_record(conn, [], ex('btc-updown-5m-1', 1, 1.0), equity)

    assert not decision.allowed
    assert decision.constraint == C.CONSTRAINT_DRAWDOWN
    # The halt is visible through engine.halt, not through any local state.
    assert halt.is_halted()
    assert halt_file.exists()
    record = halt.read_halt()
    assert 'drawdown' in record['reason']
    assert record['halt_id']


def test_drawdown_writes_both_a_denial_row_and_a_halt_row(conn, halt_file):
    equity = C.EquityState(current_usd=600.0, peak_usd=1000.0)
    E.evaluate_and_record(conn, [], ex('btc-updown-5m-1', 1, 1.0), equity)

    rows = conn.execute('SELECT details_json FROM risk_events').fetchall()
    assert len(rows) == 2
    parsed = [json.loads(r['details_json']) for r in rows]
    assert any(p.get('event') == 'halt_engaged' for p in parsed)
    assert all(p['constraint'] == C.CONSTRAINT_DRAWDOWN for p in parsed)

    # The halt row must NOT inflate the kill-condition count.
    counts = E.denials_by_constraint(conn, since_ts_ms=0)
    assert counts[C.CONSTRAINT_DRAWDOWN] == 1


def test_an_existing_halt_is_never_reminted(conn, halt_file):
    """Per entry attempt, so rewriting would invalidate a held ack id."""
    import engine.halt as halt
    original = halt.write_halt('manual: a human is holding this ack id')
    equity = C.EquityState(current_usd=600.0, peak_usd=1000.0)

    for _ in range(3):
        E.evaluate_and_record(conn, [], ex('btc-updown-5m-1', 1, 1.0), equity)

    assert halt.read_halt()['halt_id'] == original
    # Every denial is still counted - the record is never lost.
    assert E.denials_by_constraint(conn, since_ts_ms=0)[C.CONSTRAINT_DRAWDOWN] == 3


def test_module_defines_no_second_halt_path(conn):
    """Structural: this module must route into engine.halt, not reimplement it."""
    import inspect
    source = inspect.getsource(E)
    assert 'HALT_FILE' not in source
    assert "'HALT'" not in source
    assert 'from engine.halt import' in source

    # And the pure evaluator must not touch the kill switch at all. Asserted on
    # imports and calls, not on prose - its docstring discusses the halt.
    import ast
    pure = inspect.getsource(C)
    imported = set()
    for node in ast.walk(ast.parse(pure)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or '')
    assert not any('halt' in name for name in imported), imported
    assert 'write_halt(' not in pure
    assert 'is_halted(' not in pure


def test_engage_drawdown_halt_refuses_a_non_halt_decision(conn, halt_file):
    import engine.halt as halt
    decision = C.check([], ex('btc-updown-5m-1', 1, 50.0), FLAT)
    assert decision.constraint == C.CONSTRAINT_PER_TRADE
    with pytest.raises(ValueError):
        E.engage_drawdown_halt(conn, decision)
    assert not halt.is_halted()


# ---------------------------------------------------------------------------
# The package move must not have broken the existing import surface
# ---------------------------------------------------------------------------

def test_engine_risk_still_exports_the_original_risk_gate():
    """`engine/risk.py` became `engine/risk/__init__.py`; callers are unchanged."""
    from engine.risk import RiskGate, RiskVerdict
    assert RiskGate is not None and RiskVerdict is not None
