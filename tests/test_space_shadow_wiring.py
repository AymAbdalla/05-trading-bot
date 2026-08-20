"""The WIRING tests for the general binary market spaces: event, sports,
political (D-313). Fully offline.

Convention 22: a claim in a docstring is not a wiring test. Every test here is
written against a failure mode that was ACTUALLY true of this repo at
2026-08-18 20:00, not against an imagined one:

  1. `MarketSpace`, `space_status`, the seven `SPACE_*` constants and the four
     `DEFAULT_SPACE_*` constants all existed and were DEAD. `MarketSpace` was
     never instantiated, `run_space_cycle` did not exist, and the run() cadence
     block had a weather guard and no space guard. `search_sports_markets` and
     `search_political_markets` were fully built, tested, and called by
     nothing. So the tests here prove the loop actually REACHES Gamma for each
     space and that a strategy is actually handed the market that comes back.

  2. `build_weather_context` built its `MarketContext` without passing
     `market_type=`, so every weather context was a weather market wearing the
     default `crypto_updown` label. Nothing raised, because `WeatherArb` does
     not call `assert_supports` - and the one strategy in that space that DOES
     call it (`SmartMoneyCopy`) declares every type and so accepted the wrong
     label silently. A routing declaration is only enforceable if the context
     carries the type the router selected on, so there is a test per space that
     asserts the stamped type.

  3. The crypto accounting identity is the only thing that catches a silently
     dropped decision, and the weather space needed its own for the same
     reason. Each space gets a third. A space cycle that quietly incremented
     `evaluations` would break the crypto identity, so a test runs every space
     and asserts the crypto counters did not move at all.

NOTHING HERE TOUCHES THE NETWORK. The Gamma client and the CLOB books are
stubs, and no test in this file constructs a real strategy.
"""
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from engine.polymarket import shadow_loop as sl  # noqa: E402
from engine.polymarket.shadow_loop import (MarketSpace,  # noqa: E402
                                           PolymarketShadowLoop,
                                           space_status)
from strategies.polymarket.base import (MARKET_TYPE_EVENT,  # noqa: E402
                                        MARKET_TYPE_POLITICAL,
                                        MARKET_TYPE_SPORTS, Decision,
                                        MarketContext)

NOW = 1787065200                      # 2026-08-18T15:00:00Z

#: Every space, with the market_type it must stamp and a question that its own
#: tag sweep would plausibly return. Parameterised rather than copied three
#: times: three hand-written copies of a cycle test is three places for the
#: assertions to drift apart, which is the same convention-23 argument that
#: kept `run_space_cycle` to one implementation.
SPACES = [
    ('event', MARKET_TYPE_EVENT, 'Will OpenAI release GPT-6 before July?'),
    ('sports', MARKET_TYPE_SPORTS, 'Will the Chiefs beat the Bills?'),
    ('political', MARKET_TYPE_POLITICAL, 'Will the Fed cut rates in September?'),
]

#: The Gamma route each space actually reads, and it is NOT uniform.
#:
#: `event` goes through `search_event_markets_checked`, which reads the flat
#: `/markets` route and ASKS Gamma to sort, on `volumeNum`. `sports` and
#: `political` go through the `/events?tag_slug=` sweep, which has no working
#: sort at all, and are ordered LOCALLY afterwards.
#:
#: That difference is load-bearing and is asserted rather than smoothed over.
#: `order=volume` sorts that column as TEXT and returns the SMALLEST markets
#: while still answering HTTP 200, which is the bug D-302 fixed; `volumeNum` is
#: the numeric field and is safe to ask for. So "never ask Gamma to sort" is
#: the right rule for the tag sweep and the WRONG rule for `/markets`.
SPACE_ROUTES = {'event': '/markets', 'sports': '/events',
                'political': '/events'}
#: The spaces whose ordering is done locally, on a list we already hold.
LOCAL_SORT_SPACES = ('sports', 'political')


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ')


def _gamma_market(question, index=0, volume=50000.0):
    """One raw Gamma market in the shape `/events?tag_slug=...` returns.

    `outcomes`, `outcomePrices` and `clobTokenIds` are JSON STRINGS inside the
    JSON document. That double encoding is real and is what
    `parse_embedded_list` exists for; a fixture that used real lists would test
    a parser we do not have.
    """
    return {
        'id': 'm-%d' % index,
        'slug': 'market-%d' % index,
        'question': question,
        'conditionId': 'cond-%d' % index,
        'outcomes': '["Yes", "No"]',
        'outcomePrices': '["0.55", "0.45"]',
        'clobTokenIds': '["YES-%d", "NO-%d"]' % (index, index),
        'active': True, 'closed': False, 'acceptingOrders': True,
        'endDate': _iso(NOW + 30 * 86400),
        'volume': volume, 'liquidity': 20000.0,
        'description': 'Resolves per the official result.',
    }


class FakeGammaClient(object):
    """A Polymarket client stub. Records every path and every parameter set."""

    def __init__(self, events=None, gamma_fails=False, book=None):
        self._events = events
        self.gamma_calls = []
        self.clob_calls = []
        self.gamma_fails = gamma_fails
        self._book = book if book is not None else {
            'asks': [{'price': '0.55', 'size': '500'}],
            'bids': [{'price': '0.53', 'size': '500'}]}
        self.session = None
        self.stats = {}

    def gamma(self, path, params=None):
        self.gamma_calls.append((path, dict(params or {})))
        if self.gamma_fails:
            return None
        # Only the FIRST page has content, so pagination terminates.
        if int((params or {}).get('offset', 0)) > 0:
            return []
        events = list(self._events if self._events is not None else [])
        if path == '/events':
            return events
        if path == '/markets':
            # The `event` space goes through `search_event_markets_checked`,
            # which reads the FLAT `/markets` route rather than the tag sweep
            # the other two use. Serving both from one fixture keeps the three
            # spaces on the same market data; a fixture that only stubbed
            # `/events` would silently give the event space an empty board and
            # the test would pass for the wrong reason.
            return [m for ev in events for m in ev.get('markets', ())]
        return []

    def clob(self, path, params=None):
        self.clob_calls.append((path, dict(params or {})))
        return dict(self._book)


class SpyStrategy(object):
    """Records every context it was handed. Always SKIPs, never enters.

    Deliberately not a real strategy. These are WIRING tests: what is under
    test is that the loop reaches Gamma, builds a context of the right type and
    hands it over. A real strategy would add its own gates and turn a routing
    failure into a plausible-looking skip, which is the exact confusion this
    file exists to prevent.
    """

    is_entry = True
    needs_15m = False

    def __init__(self, name='PM_spy', supported=()):
        self.strategy_name = name
        self.supported_market_types = tuple(supported)
        self.contexts = []

    def evaluate(self, ctx):
        self.contexts.append(ctx)
        return Decision(action='SKIP', reason='spy_skip', legs=[],
                        features={'seen_market_type': ctx.market_type})


def _space(name, market_type, strategies=None, **kwargs):
    kwargs.setdefault('cycle_sec', sl.DEFAULT_SPACE_CYCLE_SEC)
    return MarketSpace(
        name=name, market_type=market_type,
        strategies=(strategies if strategies is not None
                    else [SpyStrategy(supported=(market_type,))]),
        query={'tag': None, 'keywords': ()}, **kwargs)


def _loop(client, spaces=None, **kwargs):
    """A loop with a throwaway DB and no crypto assets doing any work.

    `strategies=[]` empties the CRYPTO strategy list, so `run_cycle` is never
    exercised here and the crypto counters stay at zero for the identity test.
    The SPACES are injected explicitly for the same reason the weather tests
    inject `weather_strategies`: an injected `strategies=[]` also empties the
    registry the spaces would otherwise select from.
    """
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    return PolymarketShadowLoop(
        client=client, db_path=tmp.name, strategies=[],
        log_dir=tempfile.mkdtemp(), assets=['btc'],
        candle_source=lambda *a, **k: None,
        enable_weather=False, weather_strategies=[],
        spaces=spaces if spaces is not None else [],
        **kwargs)


@pytest.fixture
def events():
    """One event per space, each carrying three liquid markets."""
    return [{'id': 'ev-%d' % i, 'slug': 'ev-%d' % i,
             'markets': [_gamma_market(q, index=i * 10 + j)
                         for j in range(3)]}
            for i, (_name, _mt, q) in enumerate(SPACES)]


# ===========================================================================
# 1. THE CYCLE ACTUALLY EXISTS AND ACTUALLY REACHES GAMMA
# ===========================================================================

@pytest.mark.parametrize('name,market_type,_q', SPACES)
def test_discovery_reaches_gamma_and_returns_markets(events, name, market_type,
                                                     _q):
    """The finding this task started from: the scanners were built and NOTHING
    called them. This asserts a request went out for each space."""
    client = FakeGammaClient(events=events)
    space = _space(name, market_type)
    loop = _loop(client, spaces=[space])

    result = loop.discover_space_markets(space, now=NOW)

    assert result['ok'] is True
    paths = {path for path, _params in client.gamma_calls}
    assert paths == {SPACE_ROUTES[name]}
    assert space.markets, 'discovery selected no market to poll'
    assert len(space.markets) <= space.market_limit


@pytest.mark.parametrize('name', LOCAL_SORT_SPACES)
def test_the_tag_sweep_never_asks_gamma_to_sort(events, name):
    """The `/events` route has no working sort, so the safe thing is to send
    none and order locally. A local sort cannot be silently backwards."""
    market_type = dict((n, mt) for n, mt, _q in SPACES)[name]
    client = FakeGammaClient(events=events)
    space = _space(name, market_type)
    loop = _loop(client, spaces=[space])
    loop.discover_space_markets(space, now=NOW)

    assert client.gamma_calls
    for _path, params in client.gamma_calls:
        assert 'order' not in params
        assert 'ascending' not in params


def test_the_event_route_sorts_on_volumenum_and_never_on_volume(events):
    """The other half of the same rule, and the bug D-302 fixed.

    `/markets?order=volume` sorts that column as TEXT: it returns the SMALLEST
    markets first and still answers HTTP 200, so the failure is silent. Gamma
    returns 422 only for a genuinely unknown field, so a 200 does not mean the
    sort was understood. `volumeNum` is the numeric field.
    """
    client = FakeGammaClient(events=events)
    space = _space('event', MARKET_TYPE_EVENT)
    loop = _loop(client, spaces=[space])
    loop.discover_space_markets(space, now=NOW)

    orders = {params.get('order') for _p, params in client.gamma_calls}
    assert orders == {'volumeNum'}
    assert 'volume' not in orders


# ===========================================================================
# 2. THE STRATEGY IS ACTUALLY HANDED THE MARKET, WITH THE RIGHT TYPE STAMPED
# ===========================================================================

@pytest.mark.parametrize('name,market_type,_q', SPACES)
def test_the_cycle_hands_the_market_to_the_strategy(events, name, market_type,
                                                    _q):
    """Wiring, end to end: discovery -> book read -> context -> evaluate."""
    client = FakeGammaClient(events=events)
    spy = SpyStrategy(supported=(market_type,))
    space = _space(name, market_type, strategies=[spy])
    loop = _loop(client, spaces=[space])

    summary = loop.run_space_cycle(space, now=NOW)

    assert summary['status'] == 'ok'
    assert summary['markets'] > 0
    assert spy.contexts, 'the strategy was never handed a context'
    assert client.clob_calls, 'no orderbook was ever read'


@pytest.mark.parametrize('name,market_type,_q', SPACES)
def test_the_context_carries_the_market_type_the_router_selected_on(
        events, name, market_type, _q):
    """The latent defect this file was written against.

    `MarketContext.market_type` DEFAULTS to crypto_updown. A space context that
    does not stamp its type hands a sports market to a strategy under a crypto
    label, and `assert_supports` - the thing that makes the routing declaration
    enforceable - then checks the wrong fact and passes.
    """
    client = FakeGammaClient(events=events)
    spy = SpyStrategy(supported=(market_type,))
    space = _space(name, market_type, strategies=[spy])
    loop = _loop(client, spaces=[space])
    loop.run_space_cycle(space, now=NOW)

    assert spy.contexts
    assert all(ctx.market_type == market_type for ctx in spy.contexts)
    # `is_crypto_window` gates `spot`, `strike` and the 300s clock. A space
    # context claiming those are real is the downstream half of the same
    # defect: `seconds_remaining` would be arithmetic on a constant that
    # describes nothing for a market resolving in three weeks.
    assert all(not ctx.is_crypto_window for ctx in spy.contexts)


def test_the_weather_context_also_stamps_its_type():
    """The same defect, on the path this one was copied from. Regression pin:
    `build_weather_context` built its context without `market_type=` and every
    weather row carried a crypto label."""
    from strategies.polymarket.base import MARKET_TYPE_WEATHER

    client = FakeGammaClient(events=[])
    loop = _loop(client)

    class _Outcome(object):
        token_id = 'YES-wx'
        name = 'Yes'

    class _Market(object):
        slug = 'nyc-temp'
        question = 'Will the high in NYC be 85F or below?'
        outcomes = (_Outcome(),)

    ctx, status, _detail = loop.build_weather_context(_Market(), float(NOW))

    assert status == 'ok'
    assert ctx.market_type == MARKET_TYPE_WEATHER


# ===========================================================================
# 2b. `market_tape` IS WRITTEN BY THE LOOP, NOT BY A STRATEGY (D-362 R4)
# ===========================================================================

def _tape_rows(loop):
    return loop.store.conn.execute(
        'SELECT market_id, condition_id, complement_id FROM market_tape'
    ).fetchall()


@pytest.mark.parametrize('name,market_type,_q', SPACES)
def test_a_space_cycle_writes_market_tape_with_no_strategy_that_does(
        events, name, market_type, _q):
    """THE D-362 R4 REGRESSION, pinned.

    The only `market_tape` writer in production used to be
    `strategies/polymarket/dip_arb.py`, called from inside `DipArb.evaluate`.
    So the tape only filled on cycles that evaluated `PM_dip_arb`, and when
    that strategy was sentinel-killed the table froze in BOTH live books -
    including `db/trading-survivors.db`, which had never run dip_arb at all
    and therefore could not self-heal. Everything downstream
    (`agents/forge_complement_check.py`, Forge proposal 031) was reading a
    dead table without anything failing.

    `_loop` here passes `strategies=[]`, so DipArb is not merely off the
    roster, it does not exist in this process. Rows must appear anyway - that
    is the whole claim.
    """
    client = FakeGammaClient(events=events)
    space = _space(name, market_type, strategies=[SpyStrategy(
        supported=(market_type,))])
    loop = _loop(client, spaces=[space])
    assert loop._registry_names == [], 'the registry must be empty here'

    loop.run_space_cycle(space, now=NOW)

    rows = _tape_rows(loop)
    assert rows, 'the loop wrote no market_tape rows'
    assert loop.tape_rows_written == len(rows)
    assert loop.tape_contexts > 0


def test_the_tape_row_carries_the_complement_key_the_check_joins_on(events):
    """Proposal 036's key, stamped by the LOOP now. `forge_complement_check`
    joins `market_tape` to itself on (condition_id, complement_id); a row
    written without them is invisible to it, so moving the writer had to carry
    the stamping across intact rather than reimplement it."""
    client = FakeGammaClient(events=events)
    name, market_type, _q = SPACES[0]
    space = _space(name, market_type)
    loop = _loop(client, spaces=[space])

    loop.run_space_cycle(space, now=NOW)

    rows = _tape_rows(loop)
    assert rows
    by_token = {r[0]: r for r in rows}
    for market_id, condition_id, complement_id in rows:
        assert condition_id, 'condition_id missing on %s' % market_id
        # Two-outcome markets: the complement must be present AND must itself
        # be a token we taped, pointing back. A one-way link is a keying bug
        # that a COUNT(*) on this table would never surface.
        assert complement_id in by_token
        assert by_token[complement_id][2] == market_id


def test_a_crypto_context_is_still_never_taped(events):
    """Unchanged from proposal 031 phase 1, and deliberately so: a crypto
    token id is new every 5-minute window, so a persisted crypto tape is never
    read back after a restart and would only multiply the table's volume.
    `_write_market_tape` returning 0 here is the exclusion, not a failure."""
    from strategies.polymarket.base import MARKET_TYPE_CRYPTO_UPDOWN

    client = FakeGammaClient(events=events)
    loop = _loop(client)

    class _Outcome(object):
        token_id = 'UP-1'
        name = 'Up'

    class _Market(object):
        slug = 'btc-updown-5m-x'
        condition_id = 'cond-crypto'
        outcomes = (_Outcome(),)

    ctx = MarketContext(window_ts=int(NOW), market=_Market(), books={},
                        seconds_into_window=5.0,
                        market_type=MARKET_TYPE_CRYPTO_UPDOWN)
    assert loop._write_market_tape(ctx) == 0
    assert loop.tape_contexts == 0
    assert _tape_rows(loop) == []


def test_a_tape_write_that_throws_never_takes_the_cycle_down(events,
                                                             monkeypatch):
    """Instrumentation must not be able to kill a trading cycle. The failure
    is COUNTED under its own health key rather than swallowed (convention 20),
    so "the tape is broken" and "the board was empty" stay distinguishable."""
    client = FakeGammaClient(events=events)
    name, market_type, _q = SPACES[0]
    space = _space(name, market_type)
    loop = _loop(client, spaces=[space])

    def _boom(*a, **k):
        raise RuntimeError('tape exploded')

    monkeypatch.setattr(sl, 'observe_market_into_tape', _boom)

    summary = loop.run_space_cycle(space, now=NOW)

    assert summary['status'] == 'ok'
    assert loop.health['market_tape_write_exception'] > 0
    assert loop.tape_rows_written == 0


def test_stats_reports_the_tape_outside_the_identity(events):
    client = FakeGammaClient(events=events)
    name, market_type, _q = SPACES[0]
    space = _space(name, market_type)
    loop = _loop(client, spaces=[space])
    loop.run_space_cycle(space, now=NOW)

    tape = loop.stats()['market_tape']
    assert tape['rows_written'] == loop.tape_rows_written > 0
    # The DENOMINATOR is the point: rows==0 with contexts==0 means nothing
    # off-crypto was pollable, rows==0 with contexts>0 means the writer ran
    # and every book was empty. The old defect - nobody writing at all - now
    # shows as contexts==0 on a loop that is polling.
    assert tape['contexts'] == loop.tape_contexts > 0
    assert tape['db_path'] == loop.store.db_path


# ===========================================================================
# 3. THE ACCOUNTING. THREE CAUSES NEVER SHARE ONE COUNTER (CONVENTION 20)
# ===========================================================================

@pytest.mark.parametrize('name,market_type,_q', SPACES)
def test_a_space_cycle_never_touches_the_crypto_identity(events, name,
                                                         market_type, _q):
    """The crypto identity is the only thing that catches a silently dropped
    decision. A space evaluation landing in it would make it stop describing
    anything."""
    client = FakeGammaClient(events=events)
    space = _space(name, market_type)
    loop = _loop(client, spaces=[space])

    before_evals, before_counts = loop.evaluations, dict(loop.counts)
    loop.run_space_cycle(space, now=NOW)

    assert loop.evaluations == before_evals
    assert dict(loop.counts) == before_counts
    assert space.evaluations > 0


@pytest.mark.parametrize('name,market_type,_q', SPACES)
def test_each_space_holds_its_own_identity(events, name, market_type, _q):
    """`space.evaluations == sum(space.counts.values())`. Convention 20's
    actual claim: every evaluation landed in exactly one named bucket."""
    client = FakeGammaClient(events=events)
    space = _space(name, market_type)
    loop = _loop(client, spaces=[space])
    loop.run_space_cycle(space, now=NOW)

    assert loop.check_space_identity(space) is True
    assert sum(space.counts.values()) == space.evaluations
    assert space.identity_violations == 0


def test_two_spaces_failing_the_same_way_are_two_counters(events):
    """A shared `no_orderbook` bucket across sports and political would answer
    "which universe has no books" with a number describing neither."""
    client = FakeGammaClient(events=[], gamma_fails=True)
    sports = _space('sports', MARKET_TYPE_SPORTS)
    political = _space('political', MARKET_TYPE_POLITICAL)
    loop = _loop(client, spaces=[sports, political])

    loop.run_space_cycle(sports, now=NOW)
    loop.run_space_cycle(political, now=NOW)

    assert space_status('sports', sl.SPACE_DISCOVERY_FAILED) in sports.counts
    assert (space_status('political', sl.SPACE_DISCOVERY_FAILED)
            in political.counts)
    # The two counters are DIFFERENT strings, so neither space's failure can be
    # read off the other's number.
    assert set(sports.counts) != set(political.counts)


def test_a_failed_read_is_not_an_empty_board():
    """Convention 11. "Gamma was unreachable" and "Gamma listed nothing" are
    different facts and must never share a counter."""
    failed = _space('sports', MARKET_TYPE_SPORTS)
    loop_failed = _loop(FakeGammaClient(gamma_fails=True), spaces=[failed])
    loop_failed.run_space_cycle(failed, now=NOW)

    empty = _space('sports', MARKET_TYPE_SPORTS)
    loop_empty = _loop(FakeGammaClient(events=[]), spaces=[empty])
    loop_empty.run_space_cycle(empty, now=NOW)

    assert space_status('sports', sl.SPACE_DISCOVERY_FAILED) in failed.counts
    assert space_status('sports', sl.SPACE_NO_MARKET_LISTED) in empty.counts
    assert (space_status('sports', sl.SPACE_DISCOVERY_FAILED)
            not in empty.counts)


def test_a_space_with_no_strategy_is_a_disposition_not_a_silent_return():
    """A space nobody declared support for and a space that found no market are
    different facts. Both are counted; neither is a silent return."""
    space = _space('sports', MARKET_TYPE_SPORTS, strategies=[])
    loop = _loop(FakeGammaClient(events=[]), spaces=[space])

    summary = loop.run_space_cycle(space, now=NOW)

    assert summary['status'] == space_status('sports', sl.SPACE_NO_STRATEGY)
    assert space.counts[summary['status']] == 1
    assert space.evaluations == 1


def test_a_disabled_space_is_counted_separately_from_an_empty_one():
    """Running with sports OFF and running with sports on and finding nothing
    produce the same empty log otherwise, and only one is a fact about the
    board."""
    space = _space('sports', MARKET_TYPE_SPORTS, enabled=False)
    loop = _loop(FakeGammaClient(events=[]), spaces=[space])

    summary = loop.run_space_cycle(space, now=NOW)

    assert summary['status'] == space_status('sports', sl.SPACE_DISABLED)
    assert space.evaluations == 1


# ===========================================================================
# 4. THE CYCLE NEVER RAISES, AND A ROUTING BUG IS A STACK TRACE NOT A SKIP
# ===========================================================================

def test_a_strategy_that_raises_is_counted_and_does_not_kill_the_cycle(events):
    """`run_space_cycle` never raises; it counts. A strategy exception is a
    health event, and the cycle keeps going for every other strategy."""

    class Exploding(SpyStrategy):
        def evaluate(self, ctx):
            raise ValueError('boom')

    client = FakeGammaClient(events=events)
    bad = Exploding(name='PM_bad', supported=(MARKET_TYPE_SPORTS,))
    good = SpyStrategy(name='PM_good', supported=(MARKET_TYPE_SPORTS,))
    space = _space('sports', MARKET_TYPE_SPORTS, strategies=[bad, good])
    loop = _loop(client, spaces=[space])

    summary = loop.run_space_cycle(space, now=NOW)

    assert summary['status'] == 'ok'
    assert space.health['strategy_exceptions'] > 0
    assert good.contexts, 'one strategy raising stopped the others'
    assert loop.check_space_identity(space) is True


def test_a_routing_mismatch_raises_rather_than_writing_a_skip_row(events):
    """`assert_supports` is an exception on purpose. A skip reason would put a
    WIRING bug into db/trading.db as a row that looks like a decision and would
    later be read as evidence about the market."""
    from strategies.polymarket.base import PolymarketStrategy

    class Strict(PolymarketStrategy):
        strategy_name = 'PM_strict'
        supported_market_types = (MARKET_TYPE_EVENT,)

        def evaluate(self, ctx):
            self.assert_supports(ctx)
            return Decision(action='SKIP', reason='never', legs=[],
                            features={})

    client = FakeGammaClient(events=events)
    # Deliberately mis-routed: an event-only strategy placed in the SPORTS
    # space, which is exactly what a routing bug looks like.
    space = _space('sports', MARKET_TYPE_SPORTS, strategies=[Strict()])
    loop = _loop(client, spaces=[space])

    loop.run_space_cycle(space, now=NOW)

    assert space.health['strategy_exceptions'] > 0
    assert loop.check_space_identity(space) is True


# ===========================================================================
# 5. THE LOOP ACTUALLY DRIVES THE SPACES, AND REPORTS THEM
# ===========================================================================

def test_the_default_loop_builds_all_three_spaces():
    """The wiring that did not exist: `MarketSpace` was defined and never
    instantiated. This asserts the real constructor builds all three."""
    loop = _loop(FakeGammaClient(events=[]), spaces=None)
    # `spaces=None` in this helper means "let the loop build them", so it is
    # passed through rather than defaulted to the empty list.
    loop = PolymarketShadowLoop(
        client=FakeGammaClient(events=[]),
        db_path=tempfile.NamedTemporaryFile(suffix='.db',
                                            delete=False).name,
        log_dir=tempfile.mkdtemp(), assets=['btc'],
        candle_source=lambda *a, **k: None, enable_weather=False)

    assert [s.name for s in loop.spaces] == ['event', 'sports', 'political']
    assert [s.market_type for s in loop.spaces] == [
        MARKET_TYPE_EVENT, MARKET_TYPE_SPORTS, MARKET_TYPE_POLITICAL]
    assert all(s.cycle_sec == sl.DEFAULT_SPACE_CYCLE_SEC for s in loop.spaces)


def test_smart_money_copy_is_routed_into_every_space():
    """Task 1e: it follows WALLETS, not markets, so it is the one strategy that
    declares every type. If it is not in all three spaces the routing is by
    name somewhere."""
    loop = PolymarketShadowLoop(
        client=FakeGammaClient(events=[]),
        db_path=tempfile.NamedTemporaryFile(suffix='.db',
                                            delete=False).name,
        log_dir=tempfile.mkdtemp(), assets=['btc'],
        candle_source=lambda *a, **k: None, enable_weather=False)

    for space in loop.spaces:
        assert 'PM_smart_money_copy' in space.strategy_names, (
            'smart_money_copy missing from the %s space' % space.name)


def test_stats_report_every_space_separately():
    """No pooled total across spaces. "How many evaluations" summed over four
    universes on different cadences is a number describing none of them."""
    space = _space('sports', MARKET_TYPE_SPORTS)
    loop = _loop(FakeGammaClient(events=[]), spaces=[space])
    loop.run_space_cycle(space, now=NOW)

    stats = loop.stats()

    assert 'spaces' in stats
    assert 'sports' in stats['spaces']
    assert stats['spaces']['sports']['market_type'] == MARKET_TYPE_SPORTS
    assert stats['spaces']['sports']['identity_ok'] is True
    assert stats['spaces']['sports']['evaluations'] > 0


# ===========================================================================
# 6. THE OFF-CRYPTO SPACES ARE VISIBLE IN THE LOG, NOT ONLY IN THE DATABASE
# ===========================================================================
#
# D-317. The stats flush logged `stats['counts']`, the CRYPTO identity, and
# nothing else. Space dispositions reached the `signals` table and never
# stdout, so grepping the log for a space skip reason returned 0 BY
# CONSTRUCTION and was read as 'that space evaluated nothing'. Convention 30.


def test_a_space_disposition_is_absent_from_the_crypto_reasons_line():
    """The bug this decision is written against, asserted directly.

    The crypto counter is the surface an operator greps. A space skip reason
    is not in it and never was, which is why a grep returning 0 proves
    nothing.
    """
    space = _space('sports', MARKET_TYPE_SPORTS)
    loop = _loop(FakeGammaClient(events=[]), spaces=[space])
    loop.run_space_cycle(space, now=NOW)

    stats = loop.stats()

    assert space.counts, 'the space cycle counted nothing'
    for reason in space.counts:
        assert reason.startswith('sports_')
        assert reason not in stats['counts'], (
            '%s leaked into the crypto identity' % reason)


def test_every_off_crypto_space_gets_its_own_stats_line():
    """One line per space, weather included, and no pooled total."""
    spaces = [_space(name, market_type) for name, market_type, _q in SPACES]
    loop = _loop(FakeGammaClient(events=[]), spaces=spaces)
    for space in spaces:
        loop.run_space_cycle(space, now=NOW)

    lines = loop.space_reason_lines()

    assert len(lines) == len(SPACES) + 1, lines
    named = [line.split()[3] for line in lines]
    assert named == ['weather', 'event', 'political', 'sports']
    assert all(line.startswith('PM SHADOW space ') for line in lines)
    # Convention 20: no line pools the four universes into one number.
    assert not any('total' in line for line in lines)


def test_the_space_line_carries_the_reason_the_log_used_to_hide():
    """The point of the decision: the counter an operator could only reach by
    opening the database is now in the line the loop prints."""
    space = _space('sports', MARKET_TYPE_SPORTS)
    loop = _loop(FakeGammaClient(events=[]), spaces=[space])
    loop.run_space_cycle(space, now=NOW)

    line = [ln for ln in loop.space_reason_lines()
            if ln.startswith('PM SHADOW space sports ')][0]

    for reason, count in space.counts.items():
        assert reason in line, '%s missing from the sports line' % reason
        assert str(count) in line
    assert 'identity_ok=True' in line
    assert 'evals=%d' % space.evaluations in line


def test_the_flush_survives_a_broken_per_space_line():
    """Instrumentation may never take the run loop down.

    `flush_stats` runs inside the main loop, which catches KeyboardInterrupt
    and nothing else, so a raise here would stop a live session over a log
    line. A per-space formatter that blows up must cost the lines, not the
    loop.
    """
    space = _space('sports', MARKET_TYPE_SPORTS)
    loop = _loop(FakeGammaClient(events=[]), spaces=[space])
    loop.run_space_cycle(space, now=NOW)

    def boom(_stats=None):
        raise ValueError('deliberate')

    loop.space_reason_lines = boom
    stats = loop.flush_stats()

    assert stats['spaces']['sports']['evaluations'] > 0
