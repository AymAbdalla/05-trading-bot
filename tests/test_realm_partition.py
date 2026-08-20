"""The three shadow realms must PARTITION the strategy registry (D-363 R2/R5).

Every strategy runs in exactly one realm: no strategy in two books, and no
strategy in none. The second half is the one that needs a test - a duplicated
strategy is at least visible in two sets of results, whereas a strategy nobody
gave a realm to is simply never measured and nothing anywhere says so.

The rosters are parsed out of the LAUNCHERS rather than duplicated here. A test
that carried its own copy of the three lists would pass while the shell scripts
that actually start the books said something else.
"""

import re

import pytest

from engine.polymarket.shadow_loop import (CAP_SENTINEL_USDC,
                                           REALM_C_UNPAUSED_MARKET_TYPES,
                                           SENTINEL_MARKET_TYPES,
                                           SHADOW_LIFTED_GATE_CAPS,
                                           SHADOW_RISK_LIMITS,
                                           lift_shadow_capital_caps,
                                           unpause_sentinel_strategies)
from strategies.polymarket import build_strategies

LAUNCHERS = {
    'main': 'run_polymarket_shadow.sh',
    'env_b': 'run_polymarket_shadow_envb.sh',
    'realm_c': 'run_polymarket_shadow_realmc.sh',
}

#: Matches the `STRATEGIES="${STRATEGIES:-a,b,c}"` default-assignment line, and
#: ONLY that line - `UNPAUSE="${UNPAUSE:-$STRATEGIES}"` in the realm C launcher
#: must not be mistaken for a roster.
ROSTER_RE = re.compile(r'^STRATEGIES="\$\{STRATEGIES:-([^}]*)\}"', re.M)


def roster(launcher_path):
    """The roster a launcher would start with, read from the file itself."""
    with open(launcher_path) as fh:
        matches = ROSTER_RE.findall(fh.read())
    assert len(matches) == 1, (
        '%s: expected exactly one STRATEGIES default line, found %d'
        % (launcher_path, len(matches)))
    return [n.strip() for n in matches[0].split(',') if n.strip()]


@pytest.fixture
def restore_sentinels():
    """Undo `unpause_sentinel_strategies`' CLASS mutation after the test.

    It sets attributes on the strategy CLASSES, which every other test in this
    session shares. Without this, a test here would silently un-pause dip_arb
    for the rest of the suite and some unrelated test asserting the pause would
    fail far from the cause.
    """
    classes = {s.strategy_name: type(s) for s in build_strategies()}
    #: OWN attribute only. A class that INHERITS `supported_market_types` must
    #: be restored by deleting the override again, not by writing the inherited
    #: value onto it - that would turn an inherited declaration into an own one
    #: and quietly change what a later subclass edit does.
    sentinel = object()
    saved = {name: cls.__dict__.get('supported_market_types', sentinel)
             for name, cls in classes.items()}
    yield
    for name, cls in classes.items():
        if saved[name] is sentinel:
            # `cls.__dict__` is a mappingproxy and has no .pop - delattr is the
            # only way to remove a class attribute.
            try:
                delattr(cls, 'supported_market_types')
            except AttributeError:
                pass
        else:
            cls.supported_market_types = saved[name]


# -- the partition ----------------------------------------------------------

def test_rosters_cover_the_registry_exactly():
    """Union of the three rosters == the registry. No gaps."""
    registry = {s.strategy_name for s in build_strategies()}
    union = set()
    for path in LAUNCHERS.values():
        union |= set(roster(path))

    unrouted = sorted(registry - union)
    assert not unrouted, (
        'strategies with NO realm (they would never be measured, and nothing '
        'at runtime would say so): %s' % ', '.join(unrouted))
    assert not sorted(union - registry), (
        'rostered names that are not in the registry: %s'
        % ', '.join(sorted(union - registry)))


def test_rosters_are_disjoint():
    """No strategy appears in two realms (D-363 R5)."""
    seen = {}
    for realm, path in LAUNCHERS.items():
        for name in roster(path):
            seen.setdefault(name, []).append(realm)
    dupes = {n: r for n, r in seen.items() if len(r) > 1}
    assert not dupes, 'strategies in more than one realm: %r' % dupes


def test_no_roster_repeats_a_name():
    for path in LAUNCHERS.values():
        names = roster(path)
        assert len(names) == len(set(names)), '%s has a duplicate name' % path


def test_partition_arithmetic_is_what_the_comments_claim():
    """16 + 4 + 6 == 26. The launcher comments all state this."""
    sizes = {realm: len(roster(path)) for realm, path in LAUNCHERS.items()}
    assert sizes == {'main': 16, 'env_b': 4, 'realm_c': 6}, sizes
    assert sum(sizes.values()) == len(build_strategies()) == 26


def test_env_b_is_pure_fair_value_isolation():
    """Env B holds fair_value family members and nothing else (D-363 R5)."""
    names = roster(LAUNCHERS['env_b'])
    assert all(n.startswith('PM_fair_value') for n in names), names


def test_main_runs_no_fair_value():
    names = roster(LAUNCHERS['main'])
    assert not [n for n in names if n.startswith('PM_fair_value')], names


# -- realm C ----------------------------------------------------------------

def test_realm_c_is_exactly_the_sentinel_paused_set():
    """Realm C's roster == the strategies actually carrying the sentinel.

    Both directions matter. A sentinel-paused strategy missing from realm C is
    a strategy D-363 R2 leaves unmeasured; a realm C name that is NOT paused is
    a strategy being measured in two books.
    """
    paused = {s.strategy_name for s in build_strategies()
              if tuple(s.supported_market_types) == SENTINEL_MARKET_TYPES}
    assert set(roster(LAUNCHERS['realm_c'])) == paused


def test_unpause_table_covers_realm_c():
    assert set(roster(LAUNCHERS['realm_c'])) == set(REALM_C_UNPAUSED_MARKET_TYPES)


def test_unpause_routes_every_realm_c_strategy(restore_sentinels):
    """After the un-pause, no realm C strategy still carries the sentinel."""
    names = roster(LAUNCHERS['realm_c'])
    restored = unpause_sentinel_strategies(names)
    assert set(restored) == set(names)

    rebuilt = {s.strategy_name: tuple(s.supported_market_types)
               for s in build_strategies()}
    for name in names:
        assert rebuilt[name] != SENTINEL_MARKET_TYPES, name
        assert rebuilt[name] == REALM_C_UNPAUSED_MARKET_TYPES[name], name


def test_unpause_refuses_a_strategy_that_is_not_paused(restore_sentinels):
    """Refusing here is what stops an un-pause from silently widening a book."""
    with pytest.raises(ValueError):
        unpause_sentinel_strategies(['PM_temporal_arbitrage'])


def test_unpause_refuses_an_unknown_name(restore_sentinels):
    with pytest.raises(ValueError):
        unpause_sentinel_strategies(['PM_does_not_exist'])


def test_unpause_of_an_already_unpaused_name_refuses(restore_sentinels):
    """Second call must not silently overwrite a live declaration."""
    unpause_sentinel_strategies(['PM_dip_arb'])
    with pytest.raises(ValueError):
        unpause_sentinel_strategies(['PM_dip_arb'])


# -- D-363 R3, the capital caps ---------------------------------------------

def test_shadow_risk_limits_lift_the_three_named_ceilings():
    assert SHADOW_RISK_LIMITS.per_trade_notional_usd == CAP_SENTINEL_USDC
    assert SHADOW_RISK_LIMITS.per_event_notional_usd == CAP_SENTINEL_USDC
    assert SHADOW_RISK_LIMITS.aggregate_notional_usd == CAP_SENTINEL_USDC


def test_real_money_limits_are_untouched():
    """D-363 R3 lifts caps in SHADOW only. The real-money defaults stay."""
    from engine.risk import constraints

    assert constraints.DEFAULT_LIMITS.per_trade_notional_usd == 10.0
    assert constraints.DEFAULT_LIMITS.per_event_notional_usd == 30.0
    assert constraints.DEFAULT_LIMITS.aggregate_notional_usd == 60.0
    assert constraints.DEFAULT_LIMITS.max_drawdown_frac == 0.25


class _FakeGate(object):
    """Just the cap attributes, at their config.yaml values."""

    max_total_exposure_usdc = 60.0
    max_exposure_per_market_type_usdc = 40.0
    max_correlated_exposure_usdc = 50.0
    notional_cap_usdc = 10.0
    max_positions_per_market = 2


def test_lift_shadow_capital_caps_raises_the_ceilings():
    gate = _FakeGate()
    changed = lift_shadow_capital_caps(gate)

    assert set(changed) == set(SHADOW_LIFTED_GATE_CAPS)
    for attr in SHADOW_LIFTED_GATE_CAPS:
        assert getattr(gate, attr) == CAP_SENTINEL_USDC


def test_lift_shadow_capital_caps_leaves_the_sizing_quantum_alone():
    """`notional_cap_usdc` IS the order size under `sizing_mode: flat`.

    Lifting it would not remove a cap, it would try to buy $100,000 of premium
    per trade on a $1,000 paper book. See `lift_shadow_capital_caps`' docstring.
    """
    gate = _FakeGate()
    lift_shadow_capital_caps(gate)
    assert gate.notional_cap_usdc == 10.0
    assert gate.max_positions_per_market == 2


def test_lift_shadow_capital_caps_is_idempotent():
    gate = _FakeGate()
    lift_shadow_capital_caps(gate)
    assert lift_shadow_capital_caps(gate) == {}
