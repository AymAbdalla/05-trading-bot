"""The WIRING tests for the Polymarket weather cycle. Fully offline.

Convention 22: a claim in a docstring is not a wiring test. Everything here is
written against the failure mode it exists to catch, and every one of those
failures is a thing that was actually true of this repo on 2026-08-18 morning:

  1. `PM_weather_arb` was in the registry and was polled 57 times a cycle
     against a BTC Up/Down 5m market, coming back `resolution_station_unknown`.
     The strategy was wired; the MARKETS were not. So the tests here prove that
     the loop reaches Gamma's weather tag route, that it turns the answer into
     real temperature markets, and that the strategy is then handed one.
  2. `engine/feeds/noaa_weather.py` and `engine/feeds/open_meteo.py` were
     "tested but not wired". A test asserting the feed classes exist would have
     passed the whole time. So the feed tests here SPY on the injected feeds and
     assert the ICAO and the coordinates that actually went into them.
  3. The crypto accounting identity is the only thing that catches a silently
     dropped decision. A weather cycle that quietly incremented `evaluations`
     would break it, so a test runs a weather cycle and asserts the crypto
     counters did not move at all.

NOTHING HERE TOUCHES THE NETWORK. The Gamma client, the CLOB books and all
three weather feeds are stubs.
"""
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import strategies.polymarket.weather_arb as wx  # noqa: E402
from engine.polymarket import shadow_loop as sl  # noqa: E402
from engine.polymarket.shadow_loop import PolymarketShadowLoop  # noqa: E402

NOW = 1787065200                      # 2026-08-18T15:00:00Z
DAY_START = 1787011200
DAY_END = DAY_START + 86400
LOCAL_DATE = '2026-08-18'

KNYC_RULES = ('This market resolves based on the daily high temperature '
              'reported at the KNYC station. The resolution source for this '
              'market measures temperatures to whole degrees Fahrenheit '
              '(eg, 21F).')


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ')


def _gamma_market(rung=85, volume=9330.59, comparator='or below', index=0):
    """One raw Gamma market in the shape `/events?tag_slug=weather` returns.

    Copied from the live response shape measured on 2026-08-18: `outcomes`,
    `outcomePrices` and `clobTokenIds` are JSON STRINGS inside the JSON
    document, which is the double encoding `parse_embedded_list` exists for.
    """
    return {
        'id': 'wx-%d' % index,
        'slug': 'nyc-temp-2026-08-18-%d' % rung,
        'question': ('Will the highest temperature in NYC be %d°F %s on '
                     'August 18?' % (rung, comparator)),
        'conditionId': 'cond-%d' % index,
        'outcomes': '["Yes", "No"]',
        'outcomePrices': '["0.40", "0.60"]',
        'clobTokenIds': '["YES-%d", "NO-%d"]' % (index, index),
        'active': True, 'closed': False, 'acceptingOrders': True,
        'endDate': _iso(NOW + 8 * 3600),
        'volume': volume, 'liquidity': 5000.0,
        'description': KNYC_RULES,
    }


#: The market family that must NOT win the poll budget. Measured live: the six
#: highest-volume markets under the weather tag were annual-ranking questions at
#: $393k to $820k, against $9,330 for the biggest genuine city ladder.
def _annual_ranking_market(index=99):
    return {
        'id': 'yr-%d' % index, 'slug': 'hottest-year-2026',
        'question': 'Will 2026 be the hottest year on record?',
        'conditionId': 'cond-yr', 'outcomes': '["Yes", "No"]',
        'outcomePrices': '["0.30", "0.70"]',
        'clobTokenIds': '["YES-YR", "NO-YR"]',
        'active': True, 'closed': False, 'acceptingOrders': True,
        'endDate': _iso(NOW + 100 * 86400),
        'volume': 820702.86, 'liquidity': 500000.0,
        'description': 'Resolves on the NASA GISS global surface record.',
    }


class FakeGammaClient(object):
    """A Polymarket client stub. Records every path and every parameter set."""

    def __init__(self, events=None, gamma_fails=False, book=None):
        self._events = events
        self.gamma_calls = []
        self.clob_calls = []
        self.gamma_fails = gamma_fails
        self._book = book if book is not None else {
            'asks': [{'price': '0.40', 'size': '500'}],
            'bids': [{'price': '0.38', 'size': '500'}]}
        self.session = None
        self.stats = {}

    def gamma(self, path, params=None):
        self.gamma_calls.append((path, dict(params or {})))
        if self.gamma_fails:
            return None
        if path == sl.__dict__.get('GAMMA_EVENTS_PATH', '/events') \
                or path == '/events':
            # Only the FIRST page has content, so pagination terminates.
            if int((params or {}).get('offset', 0)) > 0:
                return []
            return list(self._events if self._events is not None else [])
        return []

    def clob(self, path, params=None):
        self.clob_calls.append((path, dict(params or {})))
        return dict(self._book)


class SpyAirportFeed(object):
    """Records every ICAO and every history window it was asked for."""

    def __init__(self, temp_f=88.0, observed_extreme_f=88.0):
        self.temp_f = temp_f
        self.observed_extreme_f = observed_extreme_f
        self.calls = []
        self.history_calls = []

    def observation(self, icao):
        self.calls.append(icao)
        return wx.Reading(source='airport_metar', station=str(icao).upper(),
                          temp_f=self.temp_f, observed_ts=NOW - 300,
                          lat=40.7794, lon=-73.8803), 'ok'

    def daily_extreme_checked(self, icao, metric, start, end):
        self.history_calls.append((icao, metric, start, end))
        return wx.DailyObserved(
            station=str(icao).upper(), metric=metric,
            extreme_f=self.observed_extreme_f, observations=9,
            first_ts=start + 60, last_ts=NOW - 300,
            window_start_ts=start, window_end_ts=end), 'ok'


class SpyForecastFeed(object):
    """Records every coordinate pair it was asked for."""

    def __init__(self, daily_max_f=95.0, temp_grid=88.0):
        self.daily_max_f = daily_max_f
        #: The grid value at the observation hour. Equal to the spy airport
        #: feed's reading by default, so the station-minus-grid bias is exactly
        #: zero and a test asserting on the model does not have to reason about
        #: it. A test that wants a nonzero bias moves this, not the reading.
        self.temp_grid = temp_grid
        self.calls = []

    def forecast_checked(self, lat, lon):
        self.calls.append((lat, lon))
        return wx.StationForecast(
            req_lat=lat, req_lon=lon, grid_lat=lat, grid_lon=lon,
            utc_offset_sec=0, timezone_name='UTC',
            daily_dates=(LOCAL_DATE,),
            daily_max_f=(self.daily_max_f,), daily_min_f=(68.0,),
            hourly_ts=tuple(DAY_START + 3600 * h for h in range(48)),
            hourly_f=tuple([self.temp_grid] * 48),
            fetched_ts=float(NOW)), 'ok'


class SpyDowntownFeed(object):
    def __init__(self):
        self.calls = []

    def observation(self, lat, lon):
        self.calls.append((lat, lon))
        return wx.Reading(source='downtown_open_meteo', station='x',
                          temp_f=84.0, observed_ts=NOW - 60), 'ok'


def _weather_strategy(**kwargs):
    kwargs.setdefault('allow_daily_extreme_markets', True)
    return wx.WeatherArb(airport_feed=SpyAirportFeed(),
                         downtown_feed=SpyDowntownFeed(),
                         forecast_feed=SpyForecastFeed(),
                         **kwargs)


def _loop(client, strategies=None, **kwargs):
    """A loop with a throwaway DB and no crypto assets doing any work.

    `strategies=[]` empties the CRYPTO strategy list, so `run_cycle` is never
    exercised here and the crypto counters stay at zero for the identity test.
    The WEATHER strategies are a separate list and are injected explicitly.
    """
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    kwargs.setdefault('weather_strategies',
                      strategies if strategies is not None
                      else [_weather_strategy()])
    return PolymarketShadowLoop(
        client=client, db_path=tmp.name, strategies=[],
        log_dir=tempfile.mkdtemp(), assets=['btc'],
        candle_source=lambda *a, **k: None, **kwargs)


@pytest.fixture
def events():
    """One event carrying an eleven-rung ladder plus the annual-ranking decoy."""
    rungs = [_gamma_market(rung=r, volume=1000.0 + r, index=i)
             for i, r in enumerate(range(80, 91))]
    return [{'id': 'ev-1', 'slug': 'nyc-temp', 'markets': rungs},
            {'id': 'ev-2', 'slug': 'hottest-year',
             'markets': [_annual_ranking_market()]}]


# ===========================================================================
# 1. DISCOVERY ACTUALLY REACHES GAMMA AND ACTUALLY RETURNS WEATHER MARKETS
# ===========================================================================

def test_discovery_hits_the_weather_tag_route_and_returns_real_markets(events):
    """The finding that this whole task started from: the feeds were never the
    blocker, the MARKETS were. This asserts a request went out on the tag route
    and that temperature markets came back."""
    client = FakeGammaClient(events=events)
    loop = _loop(client)
    result = loop.discover_weather_markets(now=NOW)

    assert result['ok'] is True
    assert result['reason'] is None
    paths = {path for path, _params in client.gamma_calls}
    assert paths == {'/events'}
    tags = {params.get('tag_slug') for _p, params in client.gamma_calls}
    assert tags == set(wx.WEATHER_TAG_SLUGS)
    # 11 ladder rungs plus the annual decoy, deduplicated across both tags.
    assert len(result['markets']) == 12
    assert all('temperature' in m.question.lower()
               or 'hottest' in m.question.lower()
               for m in result['markets'])


def test_discovery_never_asks_gamma_to_sort_by_volume(events):
    """`order=volume` sorts that column as TEXT and returns the SMALLEST
    markets while still answering HTTP 200; Gamma returns 422 only for a
    genuinely unknown field, so a 200 does not mean the sort was understood.

    The tag route takes no ordering at all, so the safe thing is to send none
    and sort locally. This asserts no `order` parameter is anywhere in the
    discovery path.
    """
    client = FakeGammaClient(events=events)
    _loop(client).discover_weather_markets(now=NOW)
    for _path, params in client.gamma_calls:
        assert 'order' not in params, params
        assert 'ascending' not in params, params


def test_the_poll_list_is_volume_ordered_and_excludes_the_annual_family(events):
    """A plain volume sort would put the $820,702 annual-ranking market at the
    top of the poll list and spend two book reads on a market that structurally
    cannot trade."""
    loop = _loop(FakeGammaClient(events=events), weather_market_limit=3)
    loop.discover_weather_markets(now=NOW)
    slugs = [m.slug for m in loop.weather_markets]
    assert len(slugs) == 3
    assert 'hottest-year-2026' not in slugs
    volumes = [m.volume for m in loop.weather_markets]
    assert volumes == sorted(volumes, reverse=True)
    assert loop.weather_health['declined:poll_not_a_daily_extreme_market'] == 1


def test_a_discovery_outage_is_never_reported_as_an_empty_board():
    """Convention 11. A run that found nothing because Gamma was down and a run
    that found nothing because Polymarket listed nothing are the same empty list
    and completely different facts."""
    loop = _loop(FakeGammaClient(gamma_fails=True))
    result = loop.discover_weather_markets(now=NOW)
    assert result['ok'] is False
    assert result['reason'] == 'read_failed'
    assert loop.weather_markets == []
    assert loop.weather_health['discovery_read_failed'] == 1

    loop.run_weather_cycle(now=NOW)
    assert loop.weather_counts[sl.WX_DISCOVERY_FAILED] == 1
    assert loop.weather_counts[sl.WX_NO_MARKET_LISTED] == 0


def test_an_empty_board_and_an_unusable_board_are_different_counters():
    """The third split the brief asked for: "Gamma listed nothing" and "Gamma
    listed markets and not one is pollable" point at completely different
    things - a quiet day versus our own parser."""
    empty = _loop(FakeGammaClient(events=[]))
    empty.run_weather_cycle(now=NOW)
    assert empty.weather_counts[sl.WX_NO_MARKET_LISTED] == 1
    assert empty.weather_counts[sl.WX_NONE_POLLABLE] == 0

    decoys = _loop(FakeGammaClient(events=[
        {'id': 'ev', 'slug': 'yr', 'markets': [_annual_ranking_market()]}]))
    decoys.run_weather_cycle(now=NOW)
    assert decoys.weather_counts[sl.WX_NONE_POLLABLE] == 1
    assert decoys.weather_counts[sl.WX_NO_MARKET_LISTED] == 0


def test_discovery_never_raises_out_of_the_loop(monkeypatch):
    """A network call on the critical path of a long-running loop. A raise here
    used to be impossible only because the call did not exist."""
    loop = _loop(FakeGammaClient(events=[]))

    def boom(*_a, **_k):
        raise RuntimeError('gamma exploded')

    monkeypatch.setattr(wx, 'find_weather_markets', boom)
    result = loop.discover_weather_markets(now=NOW)
    assert result['ok'] is False
    assert loop.weather_health['discovery_exceptions'] == 1


# ===========================================================================
# 2. THE FEEDS ARE ACTUALLY REACHED, WITH THE RIGHT ARGUMENTS
# ===========================================================================

def test_the_metar_feed_is_reached_with_the_station_the_rules_named(events):
    """Convention 22. Not "an AirportWeatherFeed class exists" - this asserts
    the ICAO that went INTO the feed, and that it came from the market's own
    rules text rather than from the station table."""
    strategy = _weather_strategy()
    loop = _loop(FakeGammaClient(events=events),
                 strategies=[strategy], weather_market_limit=2)
    loop.run_weather_cycle(now=NOW)
    assert strategy.airport_feed.calls == ['KNYC', 'KNYC']


def test_the_station_forecast_feed_is_reached_at_the_stations_own_coordinates(
        events):
    """The coordinates come off the METAR payload, not off a coordinate table.
    This asserts the exact pair that reached open-meteo."""
    strategy = _weather_strategy()
    loop = _loop(FakeGammaClient(events=events),
                 strategies=[strategy], weather_market_limit=2)
    loop.run_weather_cycle(now=NOW)
    assert strategy.forecast_feed.calls == [(40.7794, -73.8803)] * 2


def test_the_metar_history_feed_is_reached_over_the_local_day(events):
    """The running observed extreme is the hard floor. This asserts the window
    handed to the history feed is a full local day, half open."""
    strategy = _weather_strategy()
    loop = _loop(FakeGammaClient(events=events),
                 strategies=[strategy], weather_market_limit=1)
    loop.run_weather_cycle(now=NOW)
    assert len(strategy.airport_feed.history_calls) == 1
    icao, metric, start, end = strategy.airport_feed.history_calls[0]
    assert icao == 'KNYC'
    assert metric == 'daily_high'
    assert end - start == 86400
    assert start <= NOW < end


def test_the_downtown_feed_still_runs_and_still_gates_nothing(events):
    """The airport-versus-downtown gap is still unmeasured, and this feed is
    half the instrument that would measure it. It must keep running even though
    the daily-extreme model does not read it."""
    strategy = _weather_strategy()
    loop = _loop(FakeGammaClient(events=events),
                 strategies=[strategy], weather_market_limit=1)
    loop.run_weather_cycle(now=NOW)
    assert strategy.downtown_feed.calls == [(40.7128, -74.0060)]


# ===========================================================================
# 3. THE STRATEGY GETS PAST resolution_station_unknown
# ===========================================================================

def test_weather_arb_no_longer_skips_resolution_station_unknown(events):
    """THE DELIVERABLE, asserted rather than claimed.

    Before the weather cycle existed, `PM_weather_arb` was handed a BTC Up/Down
    5m market on every poll and returned `resolution_station_unknown` on every
    one. Handed a real temperature market it reaches the pricing stage.
    """
    loop = _loop(FakeGammaClient(events=events), weather_market_limit=4)
    loop.run_weather_cycle(now=NOW)
    assert loop.weather_evaluations == 4
    assert 'strategy:resolution_station_unknown' not in loop.weather_counts
    priced = sum(count for reason, count in loop.weather_counts.items()
                 if reason == 'entry'
                 or reason.startswith('strategy:airport_agrees')
                 or reason.startswith('strategy:edge_below_min'))
    assert priced == 4, dict(loop.weather_counts)


def test_the_crypto_cycle_now_says_not_a_temperature_market_instead():
    """The other half of the same fix. A BTC Up/Down 5m market has no station
    because it is not a weather market, and reporting that as
    `resolution_station_unknown` pooled two completely different facts under one
    counter (convention 20)."""
    from engine.polymarket.types import Market, Outcome
    from strategies.polymarket.base import MarketContext

    btc = Market(id='b', question='Bitcoin Up or Down - August 18, 3PM ET?',
                 slug='btc-updown-5m-1787065200', condition_id='c',
                 outcomes=(Outcome('Up', 'U'), Outcome('Down', 'D')),
                 active=True, closed=False, end_date=_iso(NOW + 300), raw={})
    decision = _weather_strategy().evaluate(
        MarketContext(window_ts=NOW, market=btc, books={},
                      seconds_into_window=0.0))
    assert decision.action == 'SKIP'
    assert decision.reason == 'not_a_temperature_market'
    assert decision.reason != 'resolution_station_unknown'


def test_an_entry_carries_the_daily_extreme_provenance(events):
    """If the cycle books anything, the row must say which model priced it and
    that the calibration harness does not exist."""
    # Every rung's book quotes Yes at 0.40, so the MARKET's side is No on all
    # eleven. A forecast peak of 84.0F against an 82.0F observed floor makes the
    # model say Yes on the upper rungs, which is the disagreement the strategy
    # exists to act on.
    strategy = wx.WeatherArb(
        airport_feed=SpyAirportFeed(temp_f=82.0, observed_extreme_f=82.0),
        downtown_feed=SpyDowntownFeed(),
        forecast_feed=SpyForecastFeed(daily_max_f=84.0, temp_grid=82.0),
        allow_daily_extreme_markets=True)
    loop = _loop(FakeGammaClient(events=events), strategies=[strategy],
                 weather_market_limit=11)
    summary = loop.run_weather_cycle(now=NOW)
    assert summary['entries'] >= 1
    assert loop.weather_counts['entry'] == summary['entries']

    # And the provenance is on the row that got written.
    rows = loop.store.conn.execute(
        'SELECT features_json FROM signals WHERE acted = 1').fetchall()
    assert rows, 'an entry was counted but no acted signal row was written'
    import json as _json
    feats = _json.loads(rows[0][0])
    assert feats['pricing_model'] == 'daily_extreme_forecast_anchored_normal'
    # The harness exists now; what it has NOT done is score this model against
    # a resolved market, because no weather position has ever resolved.
    assert feats['daily_extreme_calibration_harness_exists'] is True
    assert feats['daily_extreme_model_scored_on_resolved_markets'] is False
    assert feats['model_prices_point_in_time_not_daily_extreme'] is False
    assert feats['observed_extreme_f'] == 82.0
    assert feats['edge_clears_binary_tick_floor'] is True


# ===========================================================================
# 4. THE CRYPTO IDENTITY IS UNTOUCHED
# ===========================================================================

def test_a_weather_cycle_does_not_move_the_crypto_counters(events):
    """The crypto identity `evaluations == cycles * strategies * assets` is the
    only thing that catches a silently dropped decision. A weather evaluation is
    not a (cycle, asset, strategy) triple and must never land in it."""
    loop = _loop(FakeGammaClient(events=events), weather_market_limit=5)
    before_evals = loop.evaluations
    before_counts = dict(loop.counts)
    before_cycles = loop.cycles

    loop.run_weather_cycle(now=NOW)

    assert loop.evaluations == before_evals
    assert dict(loop.counts) == before_counts
    assert loop.cycles == before_cycles
    assert loop.check_identity() is True
    assert loop.weather_evaluations == 5


def test_the_weather_space_has_its_own_identity_and_it_holds(events):
    """Convention 20 in the weather space: every evaluation lands in exactly one
    named bucket and none is silently dropped."""
    loop = _loop(FakeGammaClient(events=events), weather_market_limit=6)
    for _ in range(3):
        loop.run_weather_cycle(now=NOW)
    assert sum(loop.weather_counts.values()) == loop.weather_evaluations
    assert loop.check_weather_identity() is True
    assert loop.weather_identity_violations == 0


def test_a_strategy_that_raises_is_counted_and_does_not_stop_the_cycle(events):
    """A weather strategy blowing up must not take the loop, the other markets
    or the crypto cycle with it. It is still one evaluation and it is still
    counted."""
    class Exploding(object):
        strategy_name = 'PM_exploding'
        needs_weather_market = True
        paper_mode = True
        manages_exits = False

        def evaluate(self, _ctx):
            raise RuntimeError('boom')

    loop = _loop(FakeGammaClient(events=events),
                 strategies=[Exploding()], weather_market_limit=3)
    loop.run_weather_cycle(now=NOW)
    assert loop.weather_health['strategy_exceptions'] == 3
    assert loop.weather_counts[sl.WX_CYCLE_EXCEPTION] == 3
    assert loop.check_weather_identity() is True
    assert loop.evaluations == 0


def test_weather_off_is_a_named_disposition_not_a_silent_return():
    """A session running with weather off and one running with weather on and
    finding nothing produce the same empty log otherwise, and only one of them
    is a fact about the board."""
    loop = _loop(FakeGammaClient(events=[]), enable_weather=False)
    loop.run_weather_cycle(now=NOW)
    assert loop.weather_counts[sl.WX_DISABLED] == 1
    assert loop.weather_stats()['enabled'] is False


# ===========================================================================
# 5. BOOKS, TIMEOUTS AND FAIL-CLOSED BEHAVIOUR
# ===========================================================================

def test_a_market_with_no_book_is_its_own_counted_reason(events):
    """Fail closed: no book means skip with a named reason, never a hang and
    never a fabricated price."""
    client = FakeGammaClient(events=events, book={'asks': [], 'bids': []})
    loop = _loop(client, weather_market_limit=2)
    loop.run_weather_cycle(now=NOW)
    assert loop.weather_counts[sl.WX_NO_BOOK] == 2
    assert loop.check_weather_identity() is True


def test_two_book_reads_per_selected_market_and_no_more(events):
    """The poll budget is the reason `weather_market_limit` exists. 8 markets
    is 16 CLOB reads a minute; a naive sweep of the live 1,034-market board
    would be 2,068."""
    client = FakeGammaClient(events=events)
    loop = _loop(client, weather_market_limit=3)
    loop.run_weather_cycle(now=NOW)
    assert len(client.clob_calls) == 6
    assert all(path == '/book' for path, _p in client.clob_calls)


def test_discovery_is_not_re_run_inside_the_same_cadence(events):
    """One tag sweep per weather cycle, not one per market."""
    client = FakeGammaClient(events=events)
    loop = _loop(client, weather_market_limit=3, weather_cycle_sec=60.0)
    loop.run_weather_cycle(now=NOW)
    first = len(client.gamma_calls)
    loop.run_weather_cycle(now=NOW + 10)
    assert len(client.gamma_calls) == first
    loop.run_weather_cycle(now=NOW + 120)
    assert len(client.gamma_calls) > first


def test_the_weather_stats_block_is_separate_from_the_crypto_one(events):
    loop = _loop(FakeGammaClient(events=events), weather_market_limit=2)
    loop.run_weather_cycle(now=NOW)
    stats = loop.stats()
    assert 'weather' in stats
    assert stats['weather']['evaluations'] == 2
    assert stats['weather']['identity_ok'] is True
    assert stats['weather']['strategies'] == ['PM_weather_arb']
    # The crypto block is untouched by any of it.
    assert stats['evaluations'] == 0
    assert stats['identity_ok'] is True


def test_the_registry_still_returns_twenty_and_weather_arb_is_index_16():
    """Log lines are read by position (CLAUDE.md). Adding a weather CYCLE must
    not move the registry.

    The INDEX pin is the load-bearing half and it has not moved.
    `PM_maker_rebate_quote_ladder` (proposal 024) was appended at index 19, so
    the total is 20 and every historical position is unchanged.
    """
    from strategies.polymarket import build_strategies
    names = [s.strategy_name for s in build_strategies()]
    assert len(names) == 20
    assert names[16] == 'PM_weather_arb'
    assert names[19] == 'PM_maker_rebate_quote_ladder'


def test_the_loop_selects_weather_strategies_by_capability_not_by_name():
    """A name check is a list somebody has to remember to update; a second
    weather strategy added later would silently never be polled.

    Selection runs over the population the loop was GIVEN. This used to assert
    against the global registry while injecting `strategies=[]`, which only
    passed because the pre-D-312 loop selected weather from `build_strategies()`
    unconditionally and ignored the injected list. That made "no strategies"
    silently mean "one strategy", so the test is now written against a real
    mixed population: a second weather-capable strategy must be picked up on
    its DECLARATION, and a crypto-only one must not be.
    """
    class _SecondWeather(object):
        strategy_name = 'PM_weather_two'
        supported_market_types = (sl.MARKET_TYPE_WEATHER,)

    class _CryptoOnly(object):
        strategy_name = 'PM_crypto_only'
        supported_market_types = (sl.MARKET_TYPE_CRYPTO_UPDOWN,)

    population = [_CryptoOnly(), _weather_strategy(), _SecondWeather()]
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    # Built directly rather than through `_loop`, whose `strategies` argument
    # is the WEATHER list and is not forwarded to the loop.
    loop = PolymarketShadowLoop(
        client=FakeGammaClient(events=[]), db_path=tmp.name,
        log_dir=tempfile.mkdtemp(), assets=['btc'],
        candle_source=lambda *a, **k: None, strategies=population)

    assert [s.strategy_name for s in loop.weather_strategies] == \
        ['PM_weather_arb', 'PM_weather_two']


def test_the_default_registry_puts_weather_arb_in_the_weather_space():
    """The other half: with nothing injected, the REAL registry routes
    `PM_weather_arb` into the weather space by declaration.

    `PM_smart_money_copy` is in there too and that is CORRECT, not a leak. It
    follows wallets rather than markets, so it declares every market type and
    is deliberately polled in every space.
    """
    import tempfile as _tempfile

    tmp = _tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    loop = PolymarketShadowLoop(
        client=FakeGammaClient(events=[]), db_path=tmp.name,
        log_dir=_tempfile.mkdtemp(), assets=['btc'],
        candle_source=lambda *a, **k: None)

    names = [s.strategy_name for s in loop.weather_strategies]
    assert 'PM_weather_arb' in names
    assert 'PM_smart_money_copy' in names
    # Nothing crypto-only leaked in.
    assert 'PM_streak_snapper' not in names


def test_the_weather_strategy_instances_are_not_the_crypto_ones():
    """Sharing one instance would merge two feed-cache streams and two health
    counters into one that describes neither."""
    loop = _loop(FakeGammaClient(events=[]), weather_strategies=None)
    crypto = [s for s in loop.runtimes['btc'].strategies
              if getattr(s, 'needs_weather_market', False)]
    for weather in loop.weather_strategies:
        for other in crypto:
            assert weather is not other
