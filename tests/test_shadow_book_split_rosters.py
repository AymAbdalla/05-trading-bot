"""The D-361/D-362 two-book split, pinned against the real registry.

WHAT THE SPLIT IS
-----------------
Two shadow books, same code, same config, same venue, differing ONLY by which
strategies they run:

  * main   (`run_polymarket_shadow.sh`, `db/trading.db`) - the DIVERSIFIED
    book. Everything except the fair_value family.
  * env B  (`run_polymarket_shadow_envb.sh`, `db/trading-survivors.db`) - the
    fair_value ISOLATION book. The family, plus the nine it already ran.

WHY THIS FILE EXISTS
--------------------
The rosters are two `STRATEGIES=` lines in two shell scripts, and every
property that makes them an experiment rather than two unrelated runs is an
invariant BETWEEN those two lines. Nothing enforced it. Three ways it rots
silently, all of which happened or nearly happened during D-361/D-362:

  1. A name is added to env B and not removed from main, so fair_value runs in
     BOTH books and the split doubles the contention it exists to remove.
  2. A strategy is in NEITHER roster and is silently killed outright.
     `PM_fair_value_arb_wide` was exactly this - 113 closes in main, absent
     from the D-361 brief, and it would have died unremarked (D-362 R7).
  3. A roster names a sentinel-killed strategy. `--strategies` filters the
     ROUTED sets AFTER construction, so a sentinel-killed name matches nothing
     and the loop merely warns: the book runs smaller than the roster says and
     the log does not make that obvious. Both launchers refuse at startup now;
     this pins the rosters so they never have to.

These read the SHELL SCRIPTS, not a copy of the lists, because a test against
a copy of the roster passes forever while the launchers drift (convention 22).
"""

import os
import re

from strategies.polymarket import build_strategies

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_SH = os.path.join(REPO_ROOT, 'run_polymarket_shadow.sh')
ENVB_SH = os.path.join(REPO_ROOT, 'run_polymarket_shadow_envb.sh')

#: The `('smart_money',)` D-322 sentinel. A strategy declaring exactly this is
#: routed into no space at all, so it can never be reached by any cycle.
SENTINEL = ('smart_money',)
CRYPTO_DEFAULT = ('crypto_updown',)

#: The family the split isolates. Named here rather than pattern-matched on
#: 'fair_value' so that adding a new family member is a deliberate edit to this
#: list and not something a substring silently absorbs.
FAIR_VALUE_FAMILY = {
    'PM_fair_value_arb',
    'PM_fair_value_arb_patient',
    'PM_fair_value_arb_wide',
    'PM_fair_value_settlement_exit',
    # Sentinel-killed members. In NEITHER roster: D-362 R6 keeps D-322's pause
    # in force, and a sentinel name would be refused by the launcher gate.
    'PM_fair_value_arb_hft',
    'PM_fair_value_arb_inverse',
    'PM_fair_value_mirror_fade',
}


def _roster(path):
    """The `STRATEGIES="${STRATEGIES:-...}"` default, read off the script."""
    with open(path) as fh:
        source = fh.read()
    match = re.search(r'^STRATEGIES="\$\{STRATEGIES:-([^}]*)\}"\s*$',
                      source, re.M)
    assert match, 'no STRATEGIES default line in %s' % os.path.basename(path)
    return [n.strip() for n in match.group(1).split(',') if n.strip()]


def _registry():
    """(routed, dead) name sets from the REAL registry."""
    routed, dead = set(), set()
    for s in build_strategies():
        declared = tuple(getattr(s, 'supported_market_types', CRYPTO_DEFAULT))
        (dead if declared == SENTINEL else routed).add(s.strategy_name)
    return routed, dead


def test_both_launchers_declare_a_roster():
    """Main had none before D-362 - it ran the whole registry. If this line
    ever disappears again, main silently runs fair_value and the split is off
    without anything saying so."""
    assert _roster(MAIN_SH)
    assert _roster(ENVB_SH)


def test_the_fair_value_family_runs_in_exactly_one_book():
    """Failure mode 1, stated at the RIGHT width.

    THE SPLIT IS A PARTITION OF THE FAIR_VALUE FAMILY, NOT OF THE REGISTRY.
    Env B's other seven strategies (temporal_arbitrage, streak_snapper,
    weather_arb, small_liq_continuation, corridor_collector,
    longshot_fade_hold_to_resolution, weather_bracket_width_matched) run in
    BOTH books and always have - env B is a SURVIVORS book, and D-362 R8 says
    it keeps "the rest it already runs". That overlap predates the split and
    is not what the split is about, so asserting a whole-registry partition
    here would fail on a deliberate arrangement Aym signed off on.

    What must NOT overlap is the family being isolated: fair_value in both
    books is the same strategy trading two books against one venue, which is
    the contention the isolation exists to measure without.
    """
    overlap = (set(_roster(MAIN_SH)) & set(_roster(ENVB_SH))
               & FAIR_VALUE_FAMILY)
    assert overlap == set(), 'fair_value in both books: %s' % sorted(overlap)


def test_every_routable_strategy_lands_in_at_least_one_book():
    """Failure mode 2 - the `PM_fair_value_arb_wide` defect, generalised.

    A live strategy in NEITHER roster is not paused and not retired: it is
    silently killed, with no decision recorded anywhere. Whatever the answer
    is for a new strategy, it has to be WRITTEN DOWN in one of the two
    launchers, and this is what forces that.
    """
    routed, _dead = _registry()
    covered = set(_roster(MAIN_SH)) | set(_roster(ENVB_SH))
    orphans = routed - covered
    assert orphans == set(), (
        'routed but in no book, so silently killed: %s' % sorted(orphans))


def test_neither_roster_names_a_strategy_that_does_not_exist():
    routed, dead = _registry()
    known = routed | dead
    for path in (MAIN_SH, ENVB_SH):
        unknown = set(_roster(path)) - known
        assert unknown == set(), '%s names unknown: %s' % (
            os.path.basename(path), sorted(unknown))


def test_neither_roster_names_a_sentinel_killed_strategy():
    """Failure mode 3. `--strategies` runs AFTER routing, so a sentinel name
    matches nothing: the book runs smaller than its roster claims. The
    launchers refuse at startup; this catches it at commit time instead."""
    _routed, dead = _registry()
    for path in (MAIN_SH, ENVB_SH):
        inert = set(_roster(path)) & dead
        assert inert == set(), '%s names sentinel-killed: %s' % (
            os.path.basename(path), sorted(inert))


def test_main_is_the_diversified_book_and_runs_no_fair_value():
    """D-362 R5/R8. This is the split's whole content on main's side."""
    main = set(_roster(MAIN_SH))
    assert main & FAIR_VALUE_FAMILY == set(), (
        'fair_value still in main: %s' % sorted(main & FAIR_VALUE_FAMILY))


def test_env_b_is_the_isolation_book_and_runs_every_live_family_member():
    """D-362 R7 specifically: `_wide` must be here. It was live in main with
    113 closes and the D-361 brief did not mention it at all, so the split as
    briefed would have deleted it from the experiment."""
    routed, _dead = _registry()
    envb = set(_roster(ENVB_SH))
    live_family = FAIR_VALUE_FAMILY & routed
    missing = live_family - envb
    assert missing == set(), 'live family member not in env B: %s' % sorted(
        missing)
    assert 'PM_fair_value_arb_wide' in envb


def test_the_paused_family_members_are_in_neither_book():
    """D-362 R6: D-322's pause on `_hft` and `_inverse` STANDS. D-361 wanted
    them live in env B, which would have been one Aym ruling reversing
    another; Aym ruled to keep them paused."""
    both = set(_roster(MAIN_SH)) | set(_roster(ENVB_SH))
    for name in ('PM_fair_value_arb_hft', 'PM_fair_value_arb_inverse'):
        assert name not in both


def test_neither_roster_repeats_a_name():
    for path in (MAIN_SH, ENVB_SH):
        names = _roster(path)
        assert len(names) == len(set(names)), '%s has duplicates' % (
            os.path.basename(path))


def test_both_launchers_actually_pass_the_roster_to_the_loop():
    """Convention 22: a `STRATEGIES=` line nothing forwards is a comment. Both
    scripts must reach `--strategies "${STRATEGIES}"` on the launch line."""
    for path in (MAIN_SH, ENVB_SH):
        with open(path) as fh:
            source = fh.read()
        assert '--strategies "${STRATEGIES}"' in source, (
            '%s never forwards its roster' % os.path.basename(path))
