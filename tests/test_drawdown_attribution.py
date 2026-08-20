"""Tests for the drawdown attribution instrument (proposal 049, D-380 R2).

Constructed fixtures throughout. All three shadow books are LIVE and cannot be
rewound, so nothing here reads a real database - and a test that did would fail
on a different minute anyway.

The matrix these cover is the one 049's kill condition and rules name:

- epochs are DERIVED from `equity_snapshots`, including the jump DOWN to the
  re-base that a book which peaked above it produces on restart;
- every close count is accompanied by a market-side cluster count (046), and
  positions that cannot be keyed are counted and named rather than dropped;
- the rate, hourly mean/sd, hours-to-limit and sigma arithmetic;
- losers and winners are SEPARATE subtotals (rule 5);
- **no code path computes a book-without-strategy-X number** (rule 4), checked
  against the source the same way the risk evaluator's no-probability rule is;
- the breach payload enrichment never blocks the breach record (hold 3).
"""
import math
import sqlite3

import pytest

from engine.risk import constraints as C
from engine.risk import events as E

import backtest.drawdown_attribution as DA


HOUR = 3600 * 1000
T0 = 1787000000000


def _db():
    """A book with the four tables the reporter reads, and nothing else."""
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE equity_snapshots (ts INTEGER, equity REAL, '
                'cash REAL, open_risk REAL, mode TEXT)')
    con.execute('CREATE TABLE positions (id TEXT, pair TEXT, strategy_id TEXT, '
                'signal_id TEXT, opened_ts INTEGER, closed_ts INTEGER, '
                'pnl_net REAL, exit_reason TEXT, mode TEXT)')
    con.execute('CREATE TABLE signals (id TEXT, features_json TEXT)')
    con.execute('CREATE TABLE risk_events (id TEXT PRIMARY KEY, ts INTEGER '
                'NOT NULL, type TEXT NOT NULL, details_json TEXT NOT NULL)')
    return con


def _snapshots(con, pairs):
    con.executemany(
        'INSERT INTO equity_snapshots VALUES (?, ?, ?, ?, ?)',
        [(ts, equity, equity, 0.0, 'paper') for ts, equity in pairs])


def _close(con, n, ts, pnl, strategy='PM_a', reason='stop',
           pair='btc-updown-5m-1', side='up'):
    """One closed position, with a signal row unless `side` is None."""
    signal_id = None
    if side is not None:
        signal_id = 'sig-%d' % n
        con.execute('INSERT INTO signals VALUES (?, ?)',
                    (signal_id, '{"outcome_side": "%s"}' % side))
    con.execute('INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                ('pos-%d' % n, pair, strategy, signal_id, ts - 1000, ts, pnl,
                 reason, 'paper'))


@pytest.fixture()
def book():
    """One epoch from T0, four closes, deliberately round arithmetic.

    Closes at +0.5h (-10), +1.5h (-20), +2.5h (-30), +3.2h (-5).
    Three FULL hours: -10, -20, -30 -> mean -20, sd 10.
    Net -65 over a 2.7h close span -> -24.0741 USD/h.
    """
    con = _db()
    _snapshots(con, [(T0, 1000.0), (T0 + HOUR, 990.0), (T0 + 2 * HOUR, 970.0)])
    _close(con, 1, T0 + HOUR // 2, -10.0, pair='m1', side='up')
    _close(con, 2, T0 + 3 * HOUR // 2, -20.0, pair='m1', side='up')
    _close(con, 3, T0 + 5 * HOUR // 2, -30.0, pair='m2', side='down')
    _close(con, 4, T0 + int(3.2 * HOUR), -5.0, pair='m3', side='up')
    return con


# ---------------------------------------------------------------------------
# Epoch derivation - from the data, never from a launcher flag
# ---------------------------------------------------------------------------

def test_rebase_equity_is_derived_from_the_series_not_assumed():
    con = _db()
    _snapshots(con, [(T0, 1000.0), (T0 + 1, 987.5), (T0 + 2, 1000.0),
                     (T0 + 3, 964.25), (T0 + 4, 1000.0)])
    value, occurrences = DA.rebase_equity(con)
    assert value == 1000.0
    assert occurrences == 2


def test_a_series_with_no_restart_signature_is_one_ASSUMED_epoch():
    """A never-restarted book still gets an epoch - that is exactly when a
    first drawdown can fire - but the boundary is FLAGGED as assumed, because
    a restart that re-based to a never-repeated value would merge two epochs
    into one and over-state the uptime."""
    con = _db()
    _snapshots(con, [(T0, 1000.0), (T0 + 1, 987.5), (T0 + 2, 964.25)])
    assert DA.rebase_equity(con) == (None, 0)
    found = DA.epochs(con)
    assert len(found) == 1
    assert found[0]['rebase_derived'] is False


def test_an_empty_series_yields_no_epoch_at_all():
    assert DA.epochs(_db()) == []


def test_an_assumed_boundary_is_named_in_the_rendered_report():
    con = _db()
    _snapshots(con, [(T0, 1000.0), (T0 + 1, 987.5)])
    _close(con, 1, T0 + 100, -4.0, pair='m1')
    rendered = '\n'.join(DA.format_report(DA.report(con, 0.40), ':memory:'))
    assert 'ASSUMED' in rendered
    assert 'OVER-estimate' in rendered


def test_epoch_boundary_catches_the_jump_DOWN_to_the_rebase():
    """A book that peaked ABOVE the re-base falls TO it on restart.

    A rule that only looked for jumps UP would silently merge those two epochs
    into one - which is exactly the case env A produced at 1027.9641 -> 1000.00.
    """
    con = _db()
    _snapshots(con, [(T0, 1000.0), (T0 + 1, 1027.9641), (T0 + 2, 1000.0),
                     (T0 + 3, 980.0), (T0 + 4, 1000.0)])
    found = DA.epochs(con)
    assert [e['start_ts'] for e in found] == [T0, T0 + 2, T0 + 4]


def test_epoch_end_is_the_next_epoch_start_and_current_has_none(book):
    found = DA.epochs(book)
    assert len(found) == 1
    assert found[0]['end_ts'] is None
    assert found[0]['first_equity'] == 1000.0


# ---------------------------------------------------------------------------
# Cluster counts (046) - never a close count on its own
# ---------------------------------------------------------------------------

def test_market_side_count_collapses_shares_on_one_side(book):
    """Two closes on the same (pair, outcome_side) are ONE independent draw."""
    stats = DA.epoch_stats(book, DA.epochs(book)[0], 0.40)
    assert stats['closes'] == 4
    assert stats['market_sides'] == 3        # m1/up counted once, not twice
    assert stats['unkeyed_closes'] == 0


def test_opposite_sides_of_one_market_are_two_market_sides():
    con = _db()
    _snapshots(con, [(T0, 1000.0), (T0 + HOUR, 990.0)])
    _close(con, 1, T0 + 100, -1.0, pair='m1', side='up')
    _close(con, 2, T0 + 200, -1.0, pair='m1', side='down')
    stats = DA.epoch_stats(con, DA.epochs(con)[0], 0.40)
    assert stats['market_sides'] == 2


def test_a_position_with_no_signal_row_is_counted_and_named_not_dropped():
    """Convention 20: a position that cannot be KEYED still moved money."""
    con = _db()
    _snapshots(con, [(T0, 1000.0), (T0 + HOUR, 990.0)])
    _close(con, 1, T0 + 100, -4.0, pair='m1', side='up')
    _close(con, 2, T0 + 200, -6.0, pair='m1', side=None)
    stats = DA.epoch_stats(con, DA.epochs(con)[0], 0.40)
    assert stats['closes'] == 2
    assert stats['market_sides'] == 1
    assert stats['unkeyed_closes'] == 1
    assert stats['realised_usd'] == -10.0     # the unkeyed row is still in it


def test_every_printed_close_count_carries_a_cluster_count(book):
    """The defect 046 was filed for, checked on the rendered output."""
    out = DA.report(book, 0.40)
    for line in DA.format_report(out, ':memory:'):
        if 'closes' in line and 'reported' not in line:
            assert 'sides' in line or 'market-sides' in line, line


# ---------------------------------------------------------------------------
# Rate, clock and sigma
# ---------------------------------------------------------------------------

def test_rate_hourly_moments_and_span(book):
    stats = DA.epoch_stats(book, DA.epochs(book)[0], 0.40)
    assert stats['realised_usd'] == -65.0
    assert stats['full_hours'] == 3
    assert stats['hourly_usd'] == [-10.0, -20.0, -30.0]
    assert stats['hourly_mean_usd'] == -20.0
    assert stats['hourly_sd_usd'] == 10.0
    assert stats['close_span_hours'] == pytest.approx(2.7, abs=1e-3)
    assert stats['realised_usd_per_hour'] == pytest.approx(-65 / 2.7, abs=1e-3)


def test_hours_to_limit_is_the_clock_at_the_epochs_own_mean(book):
    stats = DA.epoch_stats(book, DA.epochs(book)[0], 0.40)
    assert stats['limit_usd'] == 400.0        # 0.40 of the 1000.00 re-base
    assert stats['hours_to_limit'] == pytest.approx(20.0)


def test_sigma_arithmetic_matches_the_hand_calculation(book):
    stats = DA.epoch_stats(book, DA.epochs(book)[0], 0.40)
    sd_sum = 10.0 * math.sqrt(3)
    assert stats['sigma_at_limit'] == pytest.approx((-400 + 60) / sd_sum,
                                                   abs=1e-3)
    assert stats['sigma_observed'] == pytest.approx((-65 + 60) / sd_sum,
                                                    abs=1e-3)


def test_a_book_that_is_not_losing_never_arrives_at_the_limit():
    """None, not a negative or infinite forecast."""
    con = _db()
    _snapshots(con, [(T0, 1000.0), (T0 + HOUR, 1010.0)])
    for i in range(3):
        _close(con, i, T0 + i * HOUR + 100, +10.0, pair='m%d' % i)
    stats = DA.epoch_stats(con, DA.epochs(con)[0], 0.40)
    assert stats['hourly_mean_usd'] > 0
    assert stats['hours_to_limit'] is None


def test_one_hour_has_no_spread_so_sigma_is_unmeasurable_not_zero():
    con = _db()
    _snapshots(con, [(T0, 1000.0), (T0 + HOUR, 990.0)])
    _close(con, 1, T0 + 100, -5.0, pair='m1')
    _close(con, 2, T0 + int(1.5 * HOUR), -5.0, pair='m2')
    stats = DA.epoch_stats(con, DA.epochs(con)[0], 0.40)
    assert stats['full_hours'] == 1
    assert stats['hourly_sd_usd'] is None
    assert stats['sigma_at_limit'] is None


def test_orphan_closes_are_named_separately_but_still_counted():
    """D-353 R2 bookings are real money out, and a restart artifact. Both."""
    con = _db()
    _snapshots(con, [(T0, 1000.0), (T0 + HOUR, 990.0)])
    _close(con, 1, T0 + 100, -4.0, pair='m1')
    _close(con, 2, T0 + 200, -6.0, pair='m2', reason=DA.ORPHAN_REASON)
    stats = DA.epoch_stats(con, DA.epochs(con)[0], 0.40)
    assert stats['orphan_closes'] == 1
    assert stats['orphan_usd'] == -6.0
    assert stats['realised_usd'] == -10.0


# ---------------------------------------------------------------------------
# Composition - rules 4 and 5
# ---------------------------------------------------------------------------

def test_losers_and_winners_are_separate_subtotals_not_only_the_net():
    con = _db()
    _snapshots(con, [(T0, 1000.0), (T0 + HOUR, 990.0)])
    _close(con, 1, T0 + 100, -30.0, strategy='PM_a', pair='m1')
    _close(con, 2, T0 + 200, +20.0, strategy='PM_b', pair='m2',
           reason='target')
    comp = DA.composition(con, DA.epochs(con)[0])
    assert comp['net_usd'] == -10.0
    assert comp['loss_channels']['usd'] == -30.0
    assert comp['win_channels']['usd'] == 20.0
    assert comp['loss_channels']['closes'] == 1
    assert comp['win_channels']['closes'] == 1


def test_a_group_pulling_against_the_net_reports_no_share_of_it():
    """A "share" of a net a group is pulling AGAINST reads like an
    attribution and is not one."""
    con = _db()
    _snapshots(con, [(T0, 1000.0), (T0 + HOUR, 990.0)])
    _close(con, 1, T0 + 100, -30.0, strategy='PM_loser', pair='m1')
    _close(con, 2, T0 + 200, +20.0, strategy='PM_winner', pair='m2')
    by_name = {e['name']: e for e in
               DA.composition(con, DA.epochs(con)[0])['by_strategy']}
    assert by_name['PM_loser']['share_of_net'] == pytest.approx(3.0)
    assert by_name['PM_winner']['share_of_net'] is None


def test_composition_carries_the_accounting_label_into_json(book):
    comp = DA.composition(book, DA.epochs(book)[0])
    assert 'NOT A COUNTERFACTUAL' in comp['label']
    assert 'COUNTERFACTUAL' in DA.ACCOUNTING_LABEL


def test_no_code_path_computes_a_book_without_strategy_x_number():
    """Rule 4, enforced against the source rather than by convention.

    The reporter may subtotal by strategy; it may NOT re-aggregate the book
    with a strategy excluded. Any `!=`/`NOT IN`/`exclude` filter on
    `strategy_id` would be that number, and it must not exist.

    Prose is stripped before the check on purpose: the module docstring SAYS
    "no counterfactual" repeatedly, and a guard that tripped on its own
    warning label would have to be deleted to make the module importable -
    which is how source guards quietly stop guarding anything.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(DA))

    # 1. No SQL anywhere filters a strategy OUT. Checked on string literals
    #    only, so the module's own prose warnings cannot trip it.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            for banned in ('strategy_id !=', 'strategy_id not in',
                           'strategy_id <>'):
                assert banned not in lowered, node.value

    # 2. No identifier offers the number either - a `counterfactual` or
    #    `book_without_x` helper is the same defect with a friendlier name.
    identifiers = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            identifiers.add(node.name)
        elif isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.arg):
            identifiers.add(node.arg)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
    offenders = [n for n in identifiers
                 if 'counterfactual' in n.lower() or 'without' in n.lower()
                 or 'exclude' in n.lower()]
    assert not offenders, offenders


def test_the_sigma_label_travels_with_every_sigma(book):
    """FIRST-ORDER is named at the point of printing, not only in a docstring."""
    stats = DA.epoch_stats(book, DA.epochs(book)[0], 0.40)
    assert 'FIRST-ORDER' in stats['sigma_note']
    fields = DA.breach_payload_fields(book, 0.40)
    assert 'FIRST-ORDER' in fields['sigma_note']


# ---------------------------------------------------------------------------
# The kill condition's rollback test
# ---------------------------------------------------------------------------

def test_self_check_agrees_with_a_direct_one_line_query(book):
    epoch = DA.epochs(book)[0]
    stats = DA.epoch_stats(book, epoch, 0.40)
    check = DA.self_check(book, epoch, stats)
    assert check['agrees'] is True
    assert check['closes_agree'] is True
    assert check['delta_usd_per_hour'] < DA.SELF_CHECK_TOLERANCE_USD_PER_HOUR


def test_self_check_reports_unmeasurable_rather_than_passing():
    """One close has no span. That is "could not run", never "agrees"."""
    con = _db()
    _snapshots(con, [(T0, 1000.0), (T0 + HOUR, 990.0)])
    _close(con, 1, T0 + 100, -4.0, pair='m1')
    epoch = DA.epochs(con)[0]
    check = DA.self_check(con, epoch, DA.epoch_stats(con, epoch, 0.40))
    assert check['agrees'] is None


def test_self_check_catches_a_reporting_path_that_dropped_rows(book):
    """A rate that disagrees with the direct query is ROLLBACK, not a note."""
    epoch = DA.epochs(book)[0]
    stats = dict(DA.epoch_stats(book, epoch, 0.40))
    stats['realised_usd_per_hour'] = stats['realised_usd_per_hour'] + 5.0
    check = DA.self_check(book, epoch, stats)
    assert check['agrees'] is False


# ---------------------------------------------------------------------------
# Grading - 049's bar, evaluated rather than described
# ---------------------------------------------------------------------------

def _breach(sigma, enriched=True):
    return {'id': 'x', 'ts': 1, 'event': None, 'drawdown_frac': 0.4,
            'limit_frac': 0.4, 'sigma_observed': sigma,
            'hours_to_limit': 13.0, 'enriched': enriched}


def test_grade_is_not_tested_below_the_five_breach_bar():
    assert DA.grade([_breach(-0.36)] * 4)['verdict'] == 'NOT_TESTED'


def test_an_unenriched_breach_is_not_a_breach_that_read_low():
    """It was never measured. Counting it either way is the mistake
    convention 11 exists to stop."""
    graded = DA.grade([_breach(None, enriched=False)] * 6)
    assert graded['verdict'] == 'NOT_TESTED'
    assert graded['enriched_breaches'] == 0
    assert graded['total_breaches'] == 6


def test_five_quiet_breaches_confirm_the_limit_carries_no_information():
    graded = DA.grade([_breach(-0.36)] * 5)
    assert graded['verdict'] == 'CONFIRMED'
    assert 'RATE test' in graded['reason']


def test_one_loud_breach_refutes_the_thesis():
    graded = DA.grade([_breach(-0.36)] * 4 + [_breach(-2.4)])
    assert graded['verdict'] == 'TESTED_FAILED'


def test_the_middle_band_keeps_counting():
    assert DA.grade([_breach(-1.4)] * 5)['verdict'] == 'NOT_TESTED'


def test_recorded_breaches_report_pre_enrichment_rows_as_unenriched(book):
    book.execute(
        'INSERT INTO risk_events VALUES (?, ?, ?, ?)',
        ('r1', 1, E.RISK_EVENT_TYPE,
         '{"constraint": "portfolio_drawdown", "drawdown_frac": 0.41}'))
    found = DA.recorded_breaches(book)
    assert len(found) == 1
    assert found[0]['enriched'] is False
    assert found[0]['sigma_observed'] is None


# ---------------------------------------------------------------------------
# The payload enrichment (049 hold 3) - and its refusal to block a breach
# ---------------------------------------------------------------------------

def test_breach_payload_fields_carry_the_sigma_and_the_clock(book):
    fields = DA.breach_payload_fields(book, 0.40)
    assert fields['hours_to_limit'] == pytest.approx(20.0)
    assert fields['sigma_observed'] == pytest.approx(-0.2887, abs=1e-3)
    assert fields['epoch_closes'] == 4
    assert fields['epoch_market_sides'] == 3


def test_breach_payload_fields_are_empty_on_a_book_with_no_epoch():
    con = _db()
    assert DA.breach_payload_fields(con, 0.40) == {}


def test_a_drawdown_denial_row_gains_the_two_fields(book):
    decision = C.check([], DA_candidate(), C.EquityState(500.0, 1000.0),
                       C.DEFAULT_LIMITS)
    assert decision.constraint == C.CONSTRAINT_DRAWDOWN
    E.record_denial(book, decision)
    details = _only_details(book)
    assert 'sigma_observed' in details
    assert 'hours_to_limit' in details
    assert details['drawdown_frac'] == pytest.approx(0.5)


def test_a_breach_is_still_recorded_when_attribution_is_impossible():
    """Property 2: a breach that went unrecorded because its ANNOTATION raised
    would be a far worse defect than one recorded without a sigma."""
    con = sqlite3.connect(':memory:')          # risk_events and nothing else
    con.execute('CREATE TABLE risk_events (id TEXT PRIMARY KEY, ts INTEGER '
                'NOT NULL, type TEXT NOT NULL, details_json TEXT NOT NULL)')
    decision = C.check([], DA_candidate(), C.EquityState(500.0, 1000.0),
                       C.DEFAULT_LIMITS)
    E.record_denial(con, decision)
    details = _only_details(con)
    assert details['constraint'] == C.CONSTRAINT_DRAWDOWN
    assert 'sigma_observed' not in details


def test_a_non_drawdown_denial_gains_nothing(book):
    decision = C.check([], DA_candidate(notional=999.0),
                       C.EquityState(1000.0, 1000.0), C.DEFAULT_LIMITS)
    assert decision.constraint == C.CONSTRAINT_PER_TRADE
    E.record_denial(book, decision)
    assert 'sigma_observed' not in _only_details(book)


def DA_candidate(notional=1.0):
    return C.Exposure.from_slug('btc-updown-5m-1787022000', 1787022000,
                                notional)


def _only_details(con):
    import json
    rows = con.execute('SELECT details_json FROM risk_events').fetchall()
    assert len(rows) == 1
    return json.loads(rows[0][0])
