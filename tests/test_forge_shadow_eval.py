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
from tests import skip_reason_ast as sra  # noqa: E402


SIGNALS_DDL = """
CREATE TABLE signals (
    id TEXT PRIMARY KEY, ts INTEGER NOT NULL, pair TEXT NOT NULL,
    tf TEXT NOT NULL, strategy_id TEXT NOT NULL, pattern TEXT NOT NULL,
    direction TEXT NOT NULL, confidence REAL NOT NULL,
    features_json TEXT NOT NULL, acted INTEGER NOT NULL DEFAULT 0,
    skip_reason TEXT, mode TEXT NOT NULL DEFAULT 'paper',
    market_duration TEXT)
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
            'insert into signals values (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (f'sig{i}', 1_787_000_000_000 + i, 'btc-updown-5m-1', '5m',
             strategy, strategy, 'long', 0.0, '{}', acted, reason, 'paper',
             None))
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
# The 2026-08-18 classification gap
#
# The table went stale when concurrent sessions added strategies without
# adding their reasons, and 18.1% of all skips fell through to UNKNOWN. These
# tests pin the repair AND the mechanism that let it happen.
# ---------------------------------------------------------------------------

def test_no_skip_reason_is_defined_twice():
    """A duplicate dict key is not a Python error - the later one silently
    wins, and the earlier `missing_input` string is lost with no warning.
    `no_market` was written twice and shadowed exactly this way. The dict
    itself cannot show this after parsing, so the source is walked instead."""
    import ast
    src = open(os.path.join(ROOT, 'agents', 'forge_shadow_eval.py')).read()
    keys = None
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AnnAssign) and \
                getattr(node.target, 'id', '') == 'SKIP_CLASSIFICATION':
            keys = [k.value for k in node.value.keys]
    assert keys, 'SKIP_CLASSIFICATION literal not found'
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert dupes == [], f'skip reasons defined more than once: {dupes}'


def test_every_skip_reason_the_strategies_emit_is_classified():
    """The regression test for the gap itself.

    Grepping for `decide('SKIP', ...)` is what went stale in the first place,
    so this walks the AST of every strategy module and asserts the table covers
    what the code can actually emit. A new strategy that adds a reason without
    classifying it fails HERE, at 0 skips, instead of silently in the shadow
    log at several thousand.

    D-290: it now follows INDIRECTION too. Until 2026-08-18 this only saw
    string literals, and seven call sites pass a variable, so sixteen reasons
    were invisible and the suite was green over them by accident rather than
    by coverage. `tests/skip_reason_ast.py` resolves those; the companion test
    below is the one that makes an unfollowable site fail rather than vanish.
    """
    reasons, _prefixes, _unresolved = sra.skip_reason_sites()
    assert reasons, 'no skip reasons resolved at all - the sweep is broken'

    unclassified = {r: owners for r, owners in reasons.items()
                    if se.classify_skip_reason(r)[0] == se.UNKNOWN}
    assert unclassified == {}, (
        'skip reasons emitted by strategies but missing from '
        f'SKIP_CLASSIFICATION: {unclassified}')


def test_no_skip_reason_argument_is_left_unresolved():
    """D-290's load-bearing half.

    Without this, Option A is just a bigger version of the same blind spot -
    an expression the resolver cannot follow would contribute NOTHING to the
    test above and the suite would stay green over it, exactly as it did over
    the eight variable call sites for a day.

    A failure here is not "the resolver is broken". It is "a call site was
    written in a shape nobody can statically read", and the fix is either to
    teach `skip_reason_ast.py` that shape or to pass a literal.
    """
    _reasons, _prefixes, unresolved = sra.skip_reason_sites()
    assert unresolved == [], (
        'decide(\'SKIP\', ...) arguments that could not be resolved. Each is '
        'a reason nobody is checking is classified:\n  '
        + '\n  '.join(repr(u) for u in unresolved))


def test_the_resolver_actually_follows_the_variable_call_sites():
    """The test above passes trivially if the resolver returns nothing.

    So this pins the WORK: reasons that exist ONLY behind indirection must be
    in the resolved set. Each one below was invisible to the literal-only
    walk, and each is named here rather than counted, so deleting the rule
    that resolves it fails with the reason's own name.
    """
    reasons, prefixes, _unresolved = sra.skip_reason_sites()

    # near_liq_trigger.py: `decide('SKIP', feed.status)` - an ATTRIBUTE of a
    # dataclass returned by a call, resolved through FeedRead(status=...).
    assert 'hyperliquid_feed_stale' in reasons
    assert 'hyperliquid_single_snapshot_only' in reasons
    # `decide('SKIP', liq.reason)` in THREE modules - resolved through a
    # nested `fail(reason)` helper into `NO_DATA_REASONS`.
    for module in ('liq_cascade_chaser.py', 'small_liq_continuation.py',
                   'near_liq_trigger.py'):
        assert module in reasons['liquidation_feed_empty'], module
    assert 'liquidation_history_too_short' in reasons
    # grid_hedge.py: `decide('SKIP', implied_status)` - a local bound by
    # TUPLE-UNPACKING a call, resolved through that function's returns.
    assert reasons['implied_vol_undefined_at_the_money'] == ['grid_hedge.py']
    assert reasons['implied_vol_sign_inconsistent'] == ['grid_hedge.py']
    # weather_arb.py: `decide('SKIP', station_status)`, same shape.
    assert 'resolution_station_ambiguous' in reasons
    assert 'resolution_station_unknown' in reasons
    # fair_value_arb.py: `'fair_value_' + (est.reason or 'unusable')` - a
    # PREFIX family, claimed as a family rather than guessed at member by
    # member, and the classifier has to handle the prefix.
    assert prefixes == {'fair_value_': ['fair_value_arb.py']}
    assert se.classify_skip_reason('fair_value_anything')[0] != se.UNKNOWN


def test_every_classified_reason_is_still_emitted_by_something():
    """D-290's reverse check: the table must not accumulate dead entries.

    The forward test only ever grows the table. Nothing pushed back the other
    way, so a reason deleted from a strategy would sit here forever, and the
    next reader would take it as evidence that the code path exists.

    Retirement is explicit, not silent: a reason no strategy emits any more
    has to be named in `RETIRED_SKIP_REASONS`, which keeps it classifiable for
    historical rows while stating that it is historical.
    """
    reasons, prefixes, _unresolved = sra.skip_reason_sites()
    literals = sra.string_literals_in(('strategies', 'polymarket'),
                                      ('engine', 'polymarket'))

    dead = sorted(
        key for key in se.SKIP_CLASSIFICATION
        if key not in reasons
        and key not in literals
        and key not in se.RETIRED_SKIP_REASONS
        and not any(key.startswith(p) for p in prefixes))
    assert dead == [], (
        'SKIP_CLASSIFICATION entries nothing can emit. Either the strategy '
        'that emitted them is gone (move them to RETIRED_SKIP_REASONS) or '
        'they were never right: ' + repr(dead))


def test_a_retired_reason_that_comes_back_to_life_is_red():
    """The other direction of the same check.

    A live reason filed under "historical" is worse than an unclassified one:
    unclassified surfaces in `unknown_skip_reasons` where somebody sees it,
    while a mislabelled retirement reads as settled.
    """
    reasons, _prefixes, _unresolved = sra.skip_reason_sites()
    resurrected = sorted(r for r in se.RETIRED_SKIP_REASONS if r in reasons)
    assert resurrected == [], (
        'reasons listed as RETIRED that a strategy still emits: '
        + repr(resurrected))


def test_no_classified_reason_looks_like_a_success_sentinel():
    """Pins the resolver's one narrow filter.

    It drops `'ok'` and `'ok_*'`, because those are what a producer function
    returns when it SUCCEEDED and the guarded call site never passes them to
    `decide`. If a real skip reason is ever named `ok_something`, that filter
    would swallow it silently - so the naming rule is enforced here rather
    than trusted.
    """
    swallowed = sorted(k for k in se.SKIP_CLASSIFICATION
                       if sra.is_success_sentinel(k))
    assert swallowed == [], (
        'skip reasons named like a producer success sentinel; rename them or '
        'the AST resolver will drop them: ' + repr(swallowed))


@pytest.mark.parametrize('reason', [
    'not_final_third_of_15m',
    'too_late_for_leg1',
    'leg2_ask_above_cap',
    'leg1_ask_above_cap',
    'leg2_deadline_passed_unpaired',
    'ask_5m_above_cap',
    'ask_15m_above_cap',
    'edge_below_floor',
])
def test_evaluated_and_declined_reasons_are_genuine(reason):
    """Each names a threshold that was COMPUTED from inputs that were present
    and then not met. That is a measurement, thin but real."""
    assert se.classify_skip_reason(reason)[0] == se.GENUINE


def test_strike_inside_proxy_noise_floor_is_a_blocker_not_a_decline():
    """The one reason in the batch that is NOT a genuine decline.

    The strike is a MEASURED PROXY (Binance.US klines rebuilding the Chainlink
    60s TWAP) with a known error distribution, and
    STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0 is the floor below which the signal is
    inside our own instrument error. The strategy did not evaluate its edge and
    decline; it was refused an input it could trust. Classing it GENUINE would
    report ~19% of all skips as "looked and declined" - the exact inversion
    convention 11 exists to prevent - and flips PM_corridor_collector and
    PM_mid_price_continuation out of NOT_TESTED on live data.
    """
    cls, missing = se.classify_skip_reason('strike_inside_proxy_noise_floor')
    assert cls == se.DATA_BLOCKER
    assert missing, 'a blocker must name the input it is missing'


def test_strike_noise_floor_keeps_a_strategy_not_tested(tmp_path):
    path = str(tmp_path / 'x.db')
    _build_db(path, signals=[
        ('PM_strike', 0, 'strike_inside_proxy_noise_floor')] * 40)
    gaps = se.evaluate(path, str(tmp_path / 'gone.csv'))['gaps']
    assert [r['strategy'] for r in gaps['strategies_not_tested']] \
        == ['PM_strike']
    assert gaps['strategies_ran_no_entry'] == []


@pytest.mark.parametrize('reason', [
    'risk_gate: daily_loss_breaker: realized loss today =$30.08 > limit=$30.00',
    'risk_gate: max_positions_per_market_side: 2 >= 2',
    'risk_gate:anything_at_all',
    'adapter: refused',
])
def test_gates_that_embed_their_own_numbers_are_sim_limit(reason):
    """These carry a VARIABLE tail - the gate's own message, numbers included -
    so they can never be dict keys: every distinct dollar amount would be its
    own unclassified reason. Matched by prefix instead.

    SIM_LIMIT and not GENUINE because the strategy had already DECIDED to
    enter; our side refused. It never found out whether the market agreed.
    """
    assert se.classify_skip_reason(reason)[0] == se.SIM_LIMIT


def test_risk_gate_blocked_strategy_is_not_tested(tmp_path):
    path = str(tmp_path / 'x.db')
    _build_db(path, signals=[
        ('PM_gated', 0, 'risk_gate: daily_loss_breaker: $30.08 > $30.00')] * 40)
    gaps = se.evaluate(path, str(tmp_path / 'gone.csv'))['gaps']
    # It passed its own test and was stopped by us. Not "ran and found
    # nothing".
    assert [r['strategy'] for r in gaps['strategies_not_tested']] \
        == ['PM_gated']


def test_an_exact_entry_still_beats_a_prefix():
    """Ordering guard: the prefix table is consulted only after the exact
    table, so a specific reason can never be swallowed by a broad prefix."""
    se.SKIP_CLASSIFICATION['risk_gate:specific_case'] = (se.GENUINE, '')
    try:
        assert se.classify_skip_reason(
            'risk_gate:specific_case')[0] == se.GENUINE
    finally:
        del se.SKIP_CLASSIFICATION['risk_gate:specific_case']


def test_an_unprefixed_novel_reason_is_still_unknown():
    """The repair must not have turned the table into a catch-all: a reason
    nobody has classified still has to surface as UNKNOWN."""
    assert se.classify_skip_reason(
        'gate_invented_next_week')[0] == se.UNKNOWN


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
    # D-336: the binary floor (0.001 tick / 50c = 20bps) is now BELOW the
    # 30bps spot floor, not ~6.7x above it. 25bps clears the 20bps
    # binary floor but fails the 30bps spot floor.
    assert forge.validate(
        _candidate(asset_class='PREDICTION_MARKET', expected_edge_bps=25,
                   kill_condition='under 30bps over 200 trades in the '
                                  'vectorized harness'), []) is not None
    with pytest.raises(forge.ProposalRefused) as exc:
        forge.validate(_candidate(asset_class='CRYPTO', expected_edge_bps=25),
                       [])
    assert exc.value.category == 'below_min_edge_bps'
    assert forge.min_edge_bps_for('PREDICTION_MARKET') == 20
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


# ---------------------------------------------------------------------------
# The resolver itself (D-290)
#
# `skip_reason_ast.py` is now the thing standing between a new strategy and an
# unclassified reason. An untested guard is exactly what went stale the first
# time, so these run it against synthetic modules where the right answer is
# known by construction rather than by reading the real tree.
# ---------------------------------------------------------------------------

def _sweep(tmp_path, **modules):
    """Write `name.py: source` under a fake root and resolve it."""
    package = tmp_path / 'strategies' / 'polymarket'
    package.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, source in sorted(modules.items()):
        target = package / (name + '.py')
        target.write_text(source)
        paths.append(str(target))
    return sra.skip_reason_sites(paths=paths, index=sra.Index(str(tmp_path)))


def test_resolver_reads_a_plain_literal(tmp_path):
    reasons, _p, unresolved = _sweep(tmp_path, s="""
def f(ctx):
    return decide('SKIP', 'a_literal')
""")
    assert unresolved == []
    assert reasons == {'a_literal': ['s.py']}


def test_resolver_takes_both_branches_of_an_ifexp(tmp_path):
    """Both are reachable. Taking one is how half a family goes missing."""
    reasons, _p, _u = _sweep(tmp_path, s="""
def f(ctx):
    return decide('SKIP', 'left' if ctx else 'right')
""")
    assert sorted(reasons) == ['left', 'right']


def test_resolver_expands_a_module_level_tuple(tmp_path):
    reasons, _p, unresolved = _sweep(tmp_path, s="""
REASONS = ('one', 'two')
def f(ctx):
    reason = REASONS
    return decide('SKIP', reason)
""")
    assert unresolved == []
    assert sorted(reasons) == ['one', 'two']


def test_one_unfollowable_branch_makes_the_whole_expression_unresolved(tmp_path):
    """Partial resolution is the dangerous answer.

    `REASONS[0] if ctx else 'two'` would resolve to {'two'} under a rule that
    kept what it could - and the site would then look COVERED while one of
    its two outcomes went unchecked. Reporting the whole site is the honest
    result: half a resolution is not a resolution.
    """
    reasons, _p, unresolved = _sweep(tmp_path, s="""
REASONS = ('one', 'two')
def f(ctx):
    return decide('SKIP', REASONS[0] if ctx else 'two')
""")
    assert reasons == {}
    assert len(unresolved) == 1


def test_resolver_follows_a_constant_imported_from_a_sibling(tmp_path):
    """Convention 20's cost: shared reasons live in ONE module, so the
    resolver has to cross module boundaries or Option A buys nothing."""
    reasons, _p, unresolved = _sweep(
        tmp_path,
        feed="NO_DATA = ('feed_empty', 'feed_stale')\n",
        s="""
from strategies.polymarket.feed import NO_DATA
LOCAL = tuple(NO_DATA)
def f(ctx):
    reason = LOCAL
    return decide('SKIP', reason)
""")
    assert unresolved == []
    assert sorted(reasons) == ['feed_empty', 'feed_stale']


def test_resolver_follows_a_tuple_unpacked_local(tmp_path):
    """`_, status = producer()` - grid_hedge and weather_arb's shape."""
    reasons, _p, unresolved = _sweep(tmp_path, s="""
def producer(x):
    if x:
        return None, 'undefined_here'
    if x is None:
        return None, 'inconsistent_here'
    return 1.0, 'ok'

def f(ctx):
    value, status = producer(ctx)
    if value is None:
        return decide('SKIP', status)
""")
    assert unresolved == []
    # 'ok' is the producer's SUCCESS value and is filtered, not classified.
    assert sorted(reasons) == ['inconsistent_here', 'undefined_here']


def test_resolver_follows_an_attribute_through_a_nested_helper(tmp_path):
    """`liq.reason` - the liquidation feed's shape, and the hardest one.

    The strings never appear near the call site: they go into a nested
    `fail(reason)` helper, out through a dataclass keyword, and only then
    into `decide`.
    """
    reasons, _p, unresolved = _sweep(tmp_path, s="""
class Window:
    pass

def read_window(db):
    def fail(reason, **kw):
        return Window(ok=False, reason=reason, **kw)
    if db is None:
        return fail('table_missing')
    if db == 0:
        return fail('feed_empty')
    return Window(ok=True, reason=None)

def f(ctx):
    window = read_window(ctx)
    if not window.ok:
        return decide('SKIP', window.reason)
""")
    assert unresolved == []
    # `reason=None` on the success path is dropped, not reported as a reason.
    assert sorted(reasons) == ['feed_empty', 'table_missing']


def test_resolver_reports_a_prefix_family_rather_than_guessing_members(tmp_path):
    reasons, prefixes, unresolved = _sweep(tmp_path, s="""
def f(ctx):
    return decide('SKIP', 'family_' + ctx.reason)
""")
    assert unresolved == []
    assert reasons == {}
    assert prefixes == {'family_': ['s.py']}


def test_an_unfollowable_expression_is_reported_loudly_with_its_site(tmp_path):
    """THE clause D-290 calls load-bearing.

    A resolver that quietly returned nothing here would leave the suite green
    over an unclassified reason - the precise failure it was written to end.
    """
    _r, _p, unresolved = _sweep(tmp_path, s="""
def f(ctx):
    return decide('SKIP', ctx.some_dict[ctx.key])
""")
    assert len(unresolved) == 1
    site = unresolved[0]
    assert site.module == 's.py'
    assert site.lineno == 3
    assert 'some_dict' in site.expr
    # The report has to be actionable on its own: file, line, expression.
    assert 's.py:3' in repr(site)


def test_a_runtime_built_reason_is_unresolved_not_silently_empty(tmp_path):
    """An f-string reads like a literal and is not one."""
    _r, _p, unresolved = _sweep(tmp_path, s="""
def f(ctx):
    return decide('SKIP', f'gate_{ctx.name}')
""")
    assert len(unresolved) == 1


def test_a_loop_variable_is_unresolved_rather_than_mistaken_for_a_constant(tmp_path):
    """A local shadowing a module constant must not inherit its values."""
    _r, _p, unresolved = _sweep(tmp_path, s="""
REASONS = ('one', 'two')
def f(ctx):
    for REASONS in ctx.things:
        return decide('SKIP', REASONS)
""")
    assert len(unresolved) == 1


def test_the_resolver_terminates_on_mutual_recursion(tmp_path):
    """A cycle must come back unresolved, not hang the suite."""
    _r, _p, unresolved = _sweep(tmp_path, s="""
def a(x):
    value, status = b(x)
    return value, status

def b(x):
    value, status = a(x)
    return value, status

def f(ctx):
    value, status = a(ctx)
    return decide('SKIP', status)
""")
    assert len(unresolved) == 1


# ---------------------------------------------------------------------------
# Proposal 043: the exit counterfactual is reported here, and never by a
# strategy (043 rule 8)
# ---------------------------------------------------------------------------

def test_the_counterfactual_is_reported_and_is_not_tested_without_a_ledger(
        tmp_path):
    path = str(tmp_path / 'shadow.db')
    _build_db(path, signals=[('PM_a', 1, None)],
              positions=[('PM_a', 2, 1.0)])
    report = se.evaluate(path, str(tmp_path / 'gone.csv'))['counterfactual']
    # No `market_resolutions` in this database: NOT_TESTED, never an empty
    # table of zeroes (convention 11).
    assert report['status'] == 'NOT_TESTED'
    assert report['ledger_table_present'] is False


def test_a_counterfactual_zero_match_is_a_named_status_not_an_exception(
        tmp_path):
    path = str(tmp_path / 'shadow.db')
    _build_db(path, signals=[('PM_a', 1, None)],
              positions=[('PM_a', 2, 1.0)])
    conn = sqlite3.connect(path)
    conn.execute('update positions set signal_id = ?, exit_reason = ?',
                 ('sig0', 'sell:salvage_floor'))
    conn.execute('update signals set features_json = ?',
                 (json.dumps(dict(outcome_side='Up')),))
    conn.execute(
        'CREATE TABLE market_resolutions (market_slug TEXT, '
        'outcome_side TEXT, resolved_px REAL, resolved_ts REAL, '
        'window_ts INTEGER, source TEXT, '
        'UNIQUE (market_slug, outcome_side))')
    conn.execute('insert into market_resolutions values (?,?,?,?,?,?)',
                 ('somewhere-else', 'Up', 0.0, 1.0, 1, 'venue'))
    conn.commit()
    conn.close()
    report = se.evaluate(path, str(tmp_path / 'gone.csv'))['counterfactual']
    # A populated ledger that joins to nothing is a KEYING fault. The
    # evaluator must NAME it rather than die on it, and must never print it
    # as an empty counterfactual.
    assert report['status'] == 'ZERO_MATCH'
    assert 'outcome_side' in report['reason']
