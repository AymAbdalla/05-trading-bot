"""--strategies CLI filter: restricts ROUTED sets, never the registry.

`filter_strategies_by_name` (engine/polymarket/shadow_loop.py) is the
mechanism behind the survivors-only A/B shadow environment (env B,
docs/handoffs/2026-08-19-shadow-env-b.md): a second `shadow_loop.py` process
on its own DB, restricted to a whitelist, run alongside the main loop's full
registry. It must narrow what actually evaluates (crypto runtimes, weather,
event/sports/political spaces) without touching `build_strategies()` itself -
the registry and its pinned indices (`test_the_first_eight_did_not_move`) must
describe the same twenty-five strategies whether or not a filter is applied.

This file is OFFLINE, same as test_polymarket_shadow_loop.py: no test here
opens a socket or runs a cycle, so no candle/book fakes are needed - only
enough of a client to let `PolymarketShadowLoop` construct itself.
"""
from collections import Counter

import pytest

from engine import halt
from engine.polymarket import shadow_loop
from engine.polymarket.shadow_loop import (
    PolymarketShadowLoop, ShadowStore, filter_strategies_by_name)
from strategies.polymarket import build_strategies
from strategies.polymarket.base import (
    MARKET_TYPE_CRYPTO_UPDOWN, MARKET_TYPE_WEATHER)

_REGISTRY = build_strategies()
N_REGISTRY = len(_REGISTRY)

#: Derived from the real registry, not hardcoded, so this file does not go
#: stale the way a literal strategy list would the next time one is added,
#: renamed, or re-routed (the same reasoning as N_STRATEGIES in
#: test_polymarket_shadow_loop.py).
CRYPTO_NAMES = [s.strategy_name for s in _REGISTRY
               if MARKET_TYPE_CRYPTO_UPDOWN in getattr(
                   s, 'supported_market_types', (MARKET_TYPE_CRYPTO_UPDOWN,))]
WEATHER_NAMES = [s.strategy_name for s in _REGISTRY
                if MARKET_TYPE_WEATHER in getattr(
                    s, 'supported_market_types', ())]
assert len(CRYPTO_NAMES) >= 2 and WEATHER_NAMES, (
    'fixture assumption broken: need >=2 crypto-routed and >=1 '
    'weather-routed strategy in the registry for this file to test anything')
WHITELIST = tuple(CRYPTO_NAMES[:2] + WEATHER_NAMES[:1])


class FakeClient:
    """Enough of PolymarketClient for construction. No test here runs a cycle."""

    def gamma(self, path, params=None):
        return []

    def clob(self, path, params=None):
        return None

    def data(self, path, params=None):
        return None


class OfflineStrikeProxy:
    """No Binance. Same shape as the one in test_polymarket_shadow_loop.py."""

    def __init__(self, *a, **kw):
        self.health = Counter()

    def twap60(self, at_ts, now=None):
        return None

    def strike_for(self, window_ts, now=None):
        return {'strike': None, 'source': None, 'is_proxy': True,
                'noise_floor_bps': shadow_loop.STRIKE_PROXY_NOISE_FLOOR_BPS,
                'bar_age_sec': 0.0, 'window_ts': window_ts}


@pytest.fixture(autouse=True)
def halt_file(tmp_path, monkeypatch):
    """Never touch the repository's real HALT file."""
    monkeypatch.setattr(halt, 'HALT_FILE', str(tmp_path / 'HALT'))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(shadow_loop, 'fetch_spot_checked',
                        lambda client, asset='btc': {
                            'spot': 60000.0, 'source': 'stub',
                            'failures': {}, 'asset': asset})
    monkeypatch.setattr(shadow_loop, 'fetch_btc_spot_checked',
                        lambda client: {'spot': 60000.0, 'source': 'stub',
                                        'failures': {}, 'asset': 'btc'})
    monkeypatch.setattr(shadow_loop, 'StrikeProxy',
                        lambda *a, **kw: OfflineStrikeProxy())


def build_loop(tmp_path, **kw):
    store = ShadowStore(str(tmp_path / 'trading.db'))
    kw.setdefault('assets', ('btc', 'eth'))
    return PolymarketShadowLoop(
        client=FakeClient(), store=store,
        log_dir=str(tmp_path / 'paperlog'), include_15m=False, **kw)


def _all_routed_names(loop):
    names = set()
    for rt in loop.runtimes.values():
        names |= {getattr(s, 'strategy_name', None) for s in rt.strategies}
    names |= {getattr(s, 'strategy_name', None) for s in loop.weather_strategies}
    for space in loop.spaces:
        names |= {getattr(s, 'strategy_name', None) for s in space.strategies}
    names.discard(None)
    return names


def test_no_filter_builds_all_twenty_five(tmp_path):
    loop = build_loop(tmp_path)
    assert len(loop._registry_names) == N_REGISTRY == len(build_strategies())
    # Every routed name is a real registry member; nothing has been removed.
    registry_names = {s.strategy_name for s in build_strategies()}
    assert _all_routed_names(loop) <= registry_names


def test_filter_restricts_every_routed_set_to_the_whitelist(tmp_path):
    loop = build_loop(tmp_path)
    filter_strategies_by_name(loop, ','.join(WHITELIST))

    for rt in loop.runtimes.values():
        names = {getattr(s, 'strategy_name', None) for s in rt.strategies}
        assert names <= set(WHITELIST)
    weather_names = {getattr(s, 'strategy_name', None)
                     for s in loop.weather_strategies}
    assert weather_names <= set(WHITELIST)
    for space in loop.spaces:
        space_names = {getattr(s, 'strategy_name', None)
                       for s in space.strategies}
        assert space_names <= set(WHITELIST)

    # only the named strategies evaluate: every whitelisted name that the
    # registry actually routes anywhere is present, and nothing else is.
    assert _all_routed_names(loop) == set(WHITELIST)

    # the registry itself never moved.
    assert len(build_strategies()) == N_REGISTRY
    assert len(loop._registry_names) == N_REGISTRY


def test_filter_shrinks_the_evaluations_per_cycle_identity(tmp_path):
    loop = build_loop(tmp_path, assets=('btc',))
    before = loop.evaluations_per_cycle
    filter_strategies_by_name(loop, ','.join(WHITELIST))
    after = loop.evaluations_per_cycle

    assert after < before
    # the property is summed over the same lists the filter mutated, so it
    # (and the accounting identity built on it) follows automatically.
    assert after == len(loop.strategies) == sum(
        len(rt.strategies) for rt in loop.runtimes.values())


def test_unknown_name_in_the_whitelist_matches_nothing_silently(tmp_path):
    """A typo does not raise; it just routes zero strategies for that name.

    Matches how `filter_strategies_by_name` is written (a set-membership
    filter, no validation) - documented here so a future change in behaviour
    (e.g. raising on an unmatched name) is a deliberate decision, not a
    silent regression either way.
    """
    loop = build_loop(tmp_path, assets=('btc',))
    filter_strategies_by_name(loop, 'PM_this_strategy_does_not_exist')
    assert loop.strategies == []
    assert loop.weather_strategies == []
    assert all(space.strategies == [] for space in loop.spaces)
