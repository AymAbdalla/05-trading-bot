"""D-383: the shadow drawdown limit is MEASURED, not enforced.

D-383 (Aym ruling 2026-08-20) moved `SHADOW_RISK_LIMITS.max_drawdown_frac` from
1.0 to 0.25 so proposal 049's drawdown-attribution instrument - dormant by data,
because at 1.0 the constraint could never fire - finally gets breach events.

R2 is the whole difficulty: "This is MEASUREMENT ONLY. ... it does NOT halt
trading. The book still runs to $0 and re-funds per D-358."

The number alone does not deliver that, and it fails SILENTLY in the dangerous
direction. `constraints.check` tests drawdown FIRST, so a book past the limit
denies EVERY entry on drawdown, and the first denial writes a HALT file that is
process-wide rather than per-database - which would freeze entries on all three
shadow books at once and leave them looking alive. So this file pins both
halves, because either one alone is a broken D-383:

  * the breach is RECORDED, with 049's attribution payload (D-380 R2 hold 3);
  * it does NOT halt, and it does NOT refuse - and the constraints AFTER it
    still enforce normally, so measuring drawdown does not quietly unmeasure
    the per-trade cap;
  * the REAL-MONEY path is untouched: `DEFAULT_LIMITS` still denies and still
    halts, byte-for-byte as before.

The end-to-end wiring test - a live loop in breach that still enters and still
counts no block - lives in `tests/test_polymarket_shadow_loop.py`, where the
no-network and HALT-redirect autouse fixtures are.
"""
import json
import sqlite3

import pytest

import engine.halt as halt
from engine.risk import constraints as C
from engine.risk import events as E
from engine.polymarket.shadow_loop import (
    SHADOW_MEASURE_ONLY_CONSTRAINTS, SHADOW_RISK_LIMITS)

import tests.test_drawdown_attribution as DAT


#: 40% down on a $1,000 peak - past 0.25, and past the 35.99% this book's own
#: measured history reached, so it is a drawdown the live books can really hit.
BREACH = C.EquityState(current_usd=600.0, peak_usd=1000.0)

#: Comfortably inside the limit.
FLAT = C.EquityState(current_usd=1000.0, peak_usd=1000.0)


def ex(slug, window_ts, notional):
    return C.Exposure.from_slug(slug, window_ts, notional)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Redirect the ONE kill switch and clear the throttle between tests.

    The throttle is process-global (each shadow book is its own process), so a
    test that recorded a breach would otherwise suppress the next test's.
    """
    monkeypatch.setattr(halt, 'HALT_FILE', str(tmp_path / 'HALT'))
    E.reset_measure_only_throttle()
    assert not halt.is_halted()
    yield
    E.reset_measure_only_throttle()


@pytest.fixture()
def conn():
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    con.execute('CREATE TABLE risk_events (id TEXT PRIMARY KEY, ts INTEGER '
                'NOT NULL, type TEXT NOT NULL, details_json TEXT NOT NULL)')
    yield con
    con.close()


@pytest.fixture()
def book():
    """A real book: the four tables 049's reporter reads, one epoch, 4 closes.

    Built from `test_drawdown_attribution`'s own helpers so the payload this
    file asserts on is the payload that file already pins the arithmetic of.
    """
    con = DAT._db()
    con.row_factory = sqlite3.Row
    DAT._snapshots(con, [(DAT.T0, 1000.0), (DAT.T0 + DAT.HOUR, 990.0),
                         (DAT.T0 + 2 * DAT.HOUR, 970.0)])
    DAT._close(con, 1, DAT.T0 + DAT.HOUR // 2, -10.0, pair='m1', side='up')
    DAT._close(con, 2, DAT.T0 + 3 * DAT.HOUR // 2, -20.0, pair='m1', side='up')
    DAT._close(con, 3, DAT.T0 + 5 * DAT.HOUR // 2, -30.0, pair='m2', side='down')
    DAT._close(con, 4, DAT.T0 + int(3.2 * DAT.HOUR), -5.0, pair='m3', side='up')
    yield con
    con.close()


def _shadow(conn, candidate, equity):
    """One entry evaluation exactly as the shadow loop makes it."""
    return E.evaluate_and_record(
        conn, [], candidate, equity, limits=SHADOW_RISK_LIMITS,
        measure_only=SHADOW_MEASURE_ONLY_CONSTRAINTS)


def _drawdown_rows(conn):
    return [json.loads(r['details_json']) for r in conn.execute(
        "SELECT details_json FROM risk_events WHERE "
        "json_extract(details_json, '$.constraint') = ?",
        (C.CONSTRAINT_DRAWDOWN,))]


# ---------------------------------------------------------------------------
# 1. The policy numbers, read from source
# ---------------------------------------------------------------------------

def test_shadow_drawdown_limit_is_the_d383_number():
    """D-383: 1.0 -> 0.25. At 1.0 the constraint could not fire at all."""
    assert SHADOW_RISK_LIMITS.max_drawdown_frac == 0.25


def test_real_money_drawdown_limit_is_unchanged():
    """D-383 is a SHADOW ruling. The real-money default was already 0.25."""
    assert C.DEFAULT_LIMITS.max_drawdown_frac == 0.25


def test_drawdown_is_the_only_measured_constraint():
    """A measured constraint is an UNENFORCED one. Exactly one is intended."""
    assert SHADOW_MEASURE_ONLY_CONSTRAINTS == frozenset(
        {C.CONSTRAINT_DRAWDOWN})


def test_measure_only_defaults_to_empty():
    """The real-money path must get enforcement without asking for it."""
    import inspect

    sig = inspect.signature(E.evaluate_and_record)
    assert sig.parameters['measure_only'].default == frozenset()


def test_the_instrument_reads_the_live_limit_and_is_no_longer_dormant():
    """049's reporter reads the limit FROM SOURCE (convention 25).

    Its dormancy note is keyed on `frac >= 1.0`. After D-383 it must report the
    live number instead of the "CANNOT fire" branch.
    """
    from backtest.drawdown_attribution import shadow_limit_note

    note = shadow_limit_note()
    assert '0.25' in note
    assert 'CANNOT fire' not in note
    assert 'DORMANT' not in note


# ---------------------------------------------------------------------------
# 2. The breach is recorded - with 049's payload
# ---------------------------------------------------------------------------

def test_a_shadow_breach_writes_a_drawdown_row(conn):
    _shadow(conn, ex('btc-updown-5m-1', 1, 10.0), BREACH)

    rows = _drawdown_rows(conn)
    assert len(rows) == 1
    assert rows[0]['constraint'] == C.CONSTRAINT_DRAWDOWN
    assert rows[0]['drawdown_frac'] == pytest.approx(0.40)
    # The limit it was measured against, so the row is self-contained even
    # after the policy number moves again (convention 20).
    assert rows[0]['limit_frac'] == 0.25


def test_the_breach_row_carries_the_049_attribution_payload(book):
    """D-380 R2 hold 3: sigma and the clock, captured AT the breach.

    This is the entire point of D-383 - the row without the payload would
    unblock nothing.
    """
    _shadow(book, ex('btc-updown-5m-1', 1, 10.0), BREACH)

    rows = _drawdown_rows(book)
    assert len(rows) == 1
    payload = rows[0]
    for field in ('sigma_observed', 'hours_to_limit', 'epoch_closes',
                  'epoch_market_sides', 'epoch_realised_usd_per_hour'):
        assert field in payload, sorted(payload)
    # 046: a close count never travels without its market-side cluster count.
    assert payload['epoch_closes'] == 4
    assert payload['epoch_market_sides'] == 3


def test_a_breach_that_cannot_be_attributed_is_still_recorded(conn):
    """Hold 3 again: the annotation must never block the record.

    `conn` has a `risk_events` table and nothing else, so the reporter raises.
    """
    _shadow(conn, ex('btc-updown-5m-1', 1, 10.0), BREACH)

    rows = _drawdown_rows(conn)
    assert len(rows) == 1
    assert 'sigma_observed' not in rows[0]


# ---------------------------------------------------------------------------
# 3. ... and then does NOT halt, and does NOT refuse
# ---------------------------------------------------------------------------

def test_a_shadow_breach_writes_no_halt_file(conn):
    """D-383 R2, and the reason it matters: the HALT file is process-wide, so
    a halt here would freeze entries on ALL THREE shadow books."""
    _shadow(conn, ex('btc-updown-5m-1', 1, 10.0), BREACH)

    assert halt.is_halted() is False


def test_a_shadow_breach_does_not_refuse_the_entry(conn):
    """The book still runs to $0 and re-funds per D-358."""
    decision = _shadow(conn, ex('btc-updown-5m-1', 1, 10.0), BREACH)

    assert decision.allowed is True
    assert decision.constraint is None


def test_no_halt_row_is_written_either(conn):
    """`engage_drawdown_halt` writes its OWN `risk_events` row. In measurement
    mode that row must not exist - its presence would mean a halt was engaged
    even if the file were later cleared."""
    _shadow(conn, ex('btc-updown-5m-1', 1, 10.0), BREACH)

    rows = [json.loads(r['details_json'])
            for r in conn.execute('SELECT details_json FROM risk_events')]
    assert [r for r in rows if r.get('event') == 'halt_engaged'] == []


def test_entries_keep_flowing_for_as_long_as_the_breach_lasts(conn):
    """Not just the first one. A drawdown persists for hours."""
    for _ in range(25):
        assert _shadow(conn, ex('btc-updown-5m-1', 1, 10.0), BREACH).allowed

    assert halt.is_halted() is False


# ---------------------------------------------------------------------------
# 4. Measuring drawdown must not unmeasure its NEIGHBOURS
# ---------------------------------------------------------------------------

def test_the_other_constraints_still_bind_while_in_breach(conn):
    """Stepping over drawdown must not become "allow everything".

    The three notional ceilings are at the D-363 R3 sentinel on this book, so
    this uses an order past even that.
    """
    huge = ex('btc-updown-5m-1', 1, 500_000.0)

    decision = _shadow(conn, huge, BREACH)

    assert decision.allowed is False
    assert decision.constraint == C.CONSTRAINT_PER_TRADE
    # Denied under its OWN name, and still without a halt.
    assert halt.is_halted() is False


def test_a_neighbour_denial_in_breach_records_both_rows(conn):
    """The drawdown measurement AND the real denial. Neither is swallowed."""
    _shadow(conn, ex('btc-updown-5m-1', 1, 500_000.0), BREACH)

    counts = E.denials_by_constraint(conn, since_ts_ms=0)
    assert counts[C.CONSTRAINT_DRAWDOWN] == 1
    assert counts[C.CONSTRAINT_PER_TRADE] == 1


def test_an_unbreached_book_is_completely_untouched(conn):
    """measure_only must be inert when the constraint does not bind."""
    decision = _shadow(conn, ex('btc-updown-5m-1', 1, 10.0), FLAT)

    assert decision.allowed is True
    assert conn.execute('SELECT COUNT(*) FROM risk_events').fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 5. The real-money path, unchanged
# ---------------------------------------------------------------------------

def test_real_money_still_denies_and_still_halts(conn):
    """No `measure_only`: exactly the behaviour that existed before D-383."""
    decision = E.evaluate_and_record(conn, [], ex('btc-updown-5m-1', 1, 1.0),
                                     BREACH)

    assert decision.allowed is False
    assert decision.constraint == C.CONSTRAINT_DRAWDOWN
    assert halt.is_halted() is True
    assert 'drawdown' in halt.read_halt()['reason']


def test_real_money_records_both_the_denial_and_the_halt_row(conn):
    E.evaluate_and_record(conn, [], ex('btc-updown-5m-1', 1, 1.0), BREACH)

    rows = [json.loads(r['details_json'])
            for r in conn.execute('SELECT details_json FROM risk_events')]
    assert len(rows) == 2
    assert any(r.get('event') == 'halt_engaged' for r in rows)


def test_real_money_is_not_throttled(conn):
    """The throttle is a measurement-mode device only. Enforced, every denial
    is a row - convention 20, and `test_an_existing_halt_is_never_reminted`
    depends on it."""
    for _ in range(3):
        E.evaluate_and_record(conn, [], ex('btc-updown-5m-1', 1, 1.0), BREACH)

    assert E.denials_by_constraint(conn, since_ts_ms=0)[
        C.CONSTRAINT_DRAWDOWN] == 3


# ---------------------------------------------------------------------------
# 6. The throttle (a deliberate departure from convention 20 - see events.py)
# ---------------------------------------------------------------------------

def test_a_persistent_breach_does_not_flood_the_kill_condition_harness(conn):
    """Unthrottled, drawdown would out-count every other constraint purely by
    repeating, and `denials_by_constraint` is what the kill condition reads."""
    for _ in range(200):
        _shadow(conn, ex('btc-updown-5m-1', 1, 10.0), BREACH)

    assert E.denials_by_constraint(conn, since_ts_ms=0)[
        C.CONSTRAINT_DRAWDOWN] == 1


def test_the_throttle_lets_a_later_episode_through(conn, monkeypatch):
    """It bounds the RATE, it does not drop episodes. 049 reads episodes."""
    monkeypatch.setattr(E, 'MEASURE_ONLY_RECORD_INTERVAL_SEC', 300.0)
    clock = {'t': 1000.0}
    monkeypatch.setattr(E.time, 'monotonic', lambda: clock['t'])

    _shadow(conn, ex('btc-updown-5m-1', 1, 10.0), BREACH)
    clock['t'] += 299.0
    _shadow(conn, ex('btc-updown-5m-1', 1, 10.0), BREACH)
    assert len(_drawdown_rows(conn)) == 1

    clock['t'] += 2.0
    _shadow(conn, ex('btc-updown-5m-1', 1, 10.0), BREACH)
    assert len(_drawdown_rows(conn)) == 2


def test_a_zero_interval_records_every_breach(conn, monkeypatch):
    """The escape hatch, if Raven/Aym rule that every attempt must be a row."""
    monkeypatch.setattr(E, 'MEASURE_ONLY_RECORD_INTERVAL_SEC', 0.0)

    for _ in range(6):
        _shadow(conn, ex('btc-updown-5m-1', 1, 10.0), BREACH)

    assert len(_drawdown_rows(conn)) == 6


# ---------------------------------------------------------------------------
# 7. Structural: no second halt path was introduced
# ---------------------------------------------------------------------------

def test_events_module_still_defines_no_second_halt_path():
    """D-342 R2's structural rule, re-pinned because D-383 touched this file."""
    import inspect

    source = inspect.getsource(E)
    assert 'HALT_FILE' not in source
    assert "'HALT'" not in source
    assert 'from engine.halt import' in source


def test_the_shadow_loop_asks_for_measurement_explicitly():
    """Wiring: the loop must PASS the measured set, not rely on a default.

    A default would make every caller of `evaluate_and_record` - including the
    real-money one - measurement-only the day someone changed it.
    """
    import inspect

    from engine.polymarket import shadow_loop

    source = inspect.getsource(shadow_loop.PolymarketShadowLoop
                               ._check_risk_constraints)
    assert 'measure_only=SHADOW_MEASURE_ONLY_CONSTRAINTS' in source
