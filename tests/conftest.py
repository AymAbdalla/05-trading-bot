"""Shared pytest fixtures.

Kept deliberately small. Anything here runs for EVERY test in the suite, so a
fixture earns its place only by fixing cross-test contamination that a test
cannot reasonably defend against on its own.
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_metar_cache():
    """Empty the module-level METAR cache around every test.

    `strategies.polymarket.weather_arb` caches airport observations at MODULE
    level on purpose (see `METAR_CACHE_TTL_SEC`): a city's ladder is many
    markets standing on one station, and the cache is what stops one request
    per rung. The cost of that choice is that the cache outlives any instance,
    including the stub-session feeds tests build.

    Without this fixture the contamination is silent and it points the wrong
    way: the FIRST test to fetch 'KNYC' successfully seeds the cache, and every
    later test asking for 'KNYC' gets that reading back without ever touching
    its own stub session. Five feed tests - the timeout, the retry, the network
    exception, the NaN refusal and the empty-list refusal - passed a stale
    success instead of exercising the branch they were written for.

    Cleared both BEFORE and AFTER so neither the suite's order nor a test that
    seeds the cache deliberately can leak into a neighbour.
    """
    from strategies.polymarket.weather_arb import clear_metar_cache

    clear_metar_cache()
    yield
    clear_metar_cache()
