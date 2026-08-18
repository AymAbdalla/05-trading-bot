"""Tests for PM_weather_arb. Fully offline: both weather feeds are injected.

Three jobs, in priority order:

  1. NOTHING TOUCHES THE NETWORK. `AirportWeatherFeed` and
     `DowntownWeatherFeed` are replaced by fakes, and `find_weather_markets` is
     handed a stub client. If a test in this file ever needs a live endpoint,
     the strategy has grown a hidden fetch and that is the finding.

  2. THE RESOLUTION SOURCE IS NEVER GUESSED. The strategy must refuse a market
     whose rules text does not name a station it recognises, and must refuse one
     naming two. Those are two different refusals with two different strings.
     Getting this wrong loses the whole position while being right about the
     weather, so it gets the most tests of anything here.

  3. EVERY NAMED SKIP REASON IS REACHABLE. `SKIP_REASONS` is asserted against
     exactly the set the tests can produce, so a reason added without a test, or
     a reason quietly renamed, fails here rather than showing up as a mystery
     bucket in a shadow log.

Two reasons are INVARIANT GUARDS rather than ordinary gates:
`effective_ask_above_cap`, and the `walked_price_ate_the_edge` flavour of
`edge_below_min`. Both are unreachable through the normal path because the
entry cap is floored onto the tick grid at or below `p_side - MIN_EDGE`, so any
fill under the cap already clears MIN_EDGE. They are reached here by patching
`weather_arb.effective_ask_for` and `weather_arb.floor_to_tick`, which is the
honest way to test a guard: it fires only if a future edit breaks the
invariant, and this proves the guard is wired rather than dead text
(convention 22).

There is deliberately NO harness sweep. Per D-268 this strategy is NOT_TESTED
until a resolution-PnL harness exists; running the price-path harness on a
binary would fabricate numbers.
"""
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from engine.polymarket.types import (Market, Orderbook, Outcome,  # noqa: E402
                                     PriceLevel)
import strategies.polymarket.weather_arb as wx  # noqa: E402
from strategies.polymarket.base import Decision, MarketContext  # noqa: E402
from strategies.polymarket.weather_arb import (AirportWeatherFeed,  # noqa: E402
                                               DowntownWeatherFeed, Reading,
                                               Threshold, WeatherArb,
                                               find_weather_markets,
                                               parse_threshold,
                                               parse_threshold_checked,
                                               resolution_station_checked)

# A fixed clock so every test is deterministic and nothing reads time.time().
NOW = 1787000000
HOT_F = 92.0
COLD_F = 70.0

YES_TOKEN = 'YES-TOK'
NO_TOKEN = 'NO-TOK'

KNYC_RULES = ('This market resolves based on the daily high temperature '
              'reported at the KNYC station.')
AMBIGUOUS_RULES = 'Resolves on the KNYC reading, or on KLAX if unavailable.'
NO_STATION_RULES = 'Resolves based on the official high temperature.'

#: The exact precision sentence Polymarket ships, copied off a live market.
#: A ladder rung's edges are only correct when the source rounds to whole
#: degrees, so a ladder question needs one of these in its rules text.
WHOLE_DEGREE_F_RULES = (
    KNYC_RULES + ' The resolution source for this market measures '
    'temperatures to whole degrees Fahrenheit (eg, 21F).')
WHOLE_DEGREE_C_RULES = (
    KNYC_RULES + ' The resolution source for this market measures '
    'temperatures to whole degrees Celsius (eg, 9C).')
ONE_DECIMAL_RULES = (
    KNYC_RULES + ' The resolution source for this market measures '
    'temperatures in Celsius to one decimal place (eg, 9.1C).')

QUESTION_ABOVE = 'Will NYC exceed 85F on August 18?'
QUESTION_SCALAR = 'Highest temperature in NYC on August 18?'

#: The shape every real market uses. Postfix comparator, degree sign, unit.
LADDER_TAIL_F = ('Will the highest temperature in NYC be 85°F or below '
                 'on August 18?')
LADDER_BUCKET_C = 'Will the highest temperature in NYC be 30°C on August 18?'
LADDER_RANGE_F = ('Will the highest temperature in NYC be between 76-77°F '
                  'on August 18?')
GLOBAL_TEMP_QUESTION = ('Will global temperature increase by more than '
                        '1.29ºC in August 2026?')


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ')


def _book(token, asks=(), bids=()):
    """asks/bids as (price, size) tuples. Orderbook sorts nothing itself."""
    return Orderbook(
        token_id=token,
        asks=tuple(PriceLevel(p, s) for p, s in sorted(asks)),
        bids=tuple(PriceLevel(p, s) for p, s in sorted(bids, reverse=True)),
    )


def _market(question=QUESTION_ABOVE, rules=KNYC_RULES, end_ts=None,
            slug='nyc-temp-2026-08-18', active=True, closed=False):
    if end_ts is None:
        end_ts = NOW + 6 * 3600
    return Market(
        id=slug, question=question, slug=slug, condition_id='c-' + slug,
        outcomes=(Outcome('Yes', YES_TOKEN), Outcome('No', NO_TOKEN)),
        active=active, closed=closed,
        end_date=None if end_ts == 'missing' else _iso(end_ts),
        raw={'description': rules} if rules is not None else {},
    )


class FakeAirportFeed(object):
    """Stands in for aviationweather.gov. Records every ICAO it was asked for."""

    def __init__(self, temp_f=HOT_F, obs_ts=NOW - 300, status='ok',
                 station=None):
        self.temp_f = temp_f
        self.obs_ts = obs_ts
        self.status = status
        self.station = station
        self.calls = []

    def observation(self, icao):
        self.calls.append(icao)
        if self.status != 'ok':
            return None, self.status
        return Reading(source='airport_metar',
                       station=self.station or str(icao).upper(),
                       temp_f=self.temp_f, observed_ts=self.obs_ts), 'ok'


class FakeDowntownFeed(object):
    def __init__(self, temp_f=86.0, status='ok'):
        self.temp_f = temp_f
        self.status = status
        self.calls = []

    def observation(self, lat, lon):
        self.calls.append((lat, lon))
        if self.status != 'ok':
            return None, self.status
        return Reading(source='downtown_open_meteo', station='x',
                       temp_f=self.temp_f, observed_ts=NOW - 60), 'ok'


def _strategy(temp_f=HOT_F, obs_ts=NOW - 300, airport_status='ok',
              downtown_status='ok', **kwargs):
    return WeatherArb(
        airport_feed=FakeAirportFeed(temp_f, obs_ts, airport_status),
        downtown_feed=FakeDowntownFeed(status=downtown_status),
        **kwargs)


def _ladder_strategy(**kwargs):
    """A strategy allowed to price a daily-extreme market.

    OFF by default in production because the model prices a single reading at
    settlement and these markets resolve on the day's extreme; see gate 2c.
    Tests of everything DOWNSTREAM of that gate have to get past it, and doing
    that with an explicit flag beats doing it with a question shape that no
    live market uses.
    """
    return _strategy(allow_daily_extreme_markets=True, **kwargs)


def _ctx(market='default', yes_asks=((0.40, 100),), no_asks=((0.58, 100),),
         yes_bids=((0.38, 100),), no_bids=((0.56, 100),), window_ts=NOW,
         books='default'):
    if market == 'default':
        market = _market()
    if books == 'default':
        books = {}
        if yes_asks is not None or yes_bids is not None:
            books[YES_TOKEN] = _book(YES_TOKEN, yes_asks or (), yes_bids or ())
        if no_asks is not None or no_bids is not None:
            books[NO_TOKEN] = _book(NO_TOKEN, no_asks or (), no_bids or ())
    return MarketContext(window_ts=window_ts, market=market, books=books,
                         seconds_into_window=0.0)


# ============ 0. house rules ============

def test_paper_mode_true_in_the_module_and_on_the_class():
    assert wx.PAPER_MODE is True
    assert WeatherArb().paper_mode is True
    assert WeatherArb.paper_mode is True


def test_module_states_a_kill_condition_with_a_named_harness():
    """Convention 6: a proposal without a kill condition is a hope."""
    doc = wx.__doc__ or ''
    assert 'KILL CONDITION' in doc
    assert 'backtest/polymarket_harness.py' in doc
    assert '55%' in doc


def test_module_states_what_it_is_not_tested_on():
    """Convention 11. The docstring has to name the gap AND how to close it."""
    doc = wx.__doc__ or ''
    assert 'NOT_TESTED' in doc
    assert 'never measured' in doc.lower()
    assert 'recorder' in doc.lower()


def test_holds_to_resolution_and_never_manages_exits():
    assert WeatherArb.manages_exits is False
    assert not hasattr(WeatherArb, 'manage_exit')


def test_no_em_dash_and_no_double_hyphen_inside_a_word():
    """House writing rule. Section-rule comment lines are runs of hyphens and
    are not prose, so only word-adjacent double hyphens are checked."""
    path = wx.__file__.replace('.pyc', '.py')
    with open(path, 'r') as fh:
        text = fh.read()
    assert '—' not in text
    assert re.search(r'\w--\w', text) is None
    assert re.search(r'\w -- \w', text) is None


# ============ 1. the station table is an ASSUMPTION, and says so ============

def test_every_table_alias_maps_to_an_icao_in_station_aliases():
    """A table alias with no mapping would silently never match a rules text."""
    for key, city in wx.WEATHER_MARKETS.items():
        for alias in city['aliases']:
            assert alias.lower() in wx.STATION_ALIASES, (key, alias)


def test_every_city_primary_icao_is_a_known_station():
    for key, city in wx.WEATHER_MARKETS.items():
        assert city['icao'] in set(wx.STATION_ALIASES.values()), key


def test_all_five_required_cities_are_present():
    assert set(wx.WEATHER_MARKETS) == {'nyc', 'la', 'chicago', 'miami', 'denver'}


def test_the_table_is_documented_as_an_assumption_not_a_fact():
    """If this wording ever disappears, somebody has started trusting it."""
    path = wx.__file__.replace('.pyc', '.py')
    with open(path, 'r') as fh:
        text = fh.read()
    assert 'ASSUMPTIONS' in text
    assert 'It is NOT what a market' in text


# ============ 2. resolution station: read it, never guess it ============

def test_rules_text_naming_one_station_resolves_to_that_station():
    station, status = resolution_station_checked(_market(rules=KNYC_RULES))
    assert (station, status) == ('KNYC', 'ok')


def test_rules_text_naming_a_plain_english_station_resolves_too():
    station, status = resolution_station_checked(
        _market(rules='Resolves on the LaGuardia observation.'))
    assert (station, status) == ('KLGA', 'ok')


def test_rules_text_with_no_station_is_unknown_not_a_guess():
    station, status = resolution_station_checked(_market(rules=NO_STATION_RULES))
    assert station is None
    assert status == 'resolution_station_unknown'


def test_absent_rules_text_is_unknown_and_never_falls_back_to_the_table():
    market = _market(rules=None)
    assert wx.city_for_market(market) == 'nyc'      # the table DOES know it
    station, status = resolution_station_checked(market)
    assert station is None                          # and it is still refused
    assert status == 'resolution_station_unknown'


def test_two_stations_in_the_rules_text_is_ambiguous_not_first_wins():
    station, status = resolution_station_checked(_market(rules=AMBIGUOUS_RULES))
    assert station is None
    assert status == 'resolution_station_ambiguous'


def test_an_icao_is_matched_on_a_word_boundary_not_as_a_substring():
    station, _ = resolution_station_checked(
        _market(rules='See TOKDENX for details.'))
    assert station is None


def test_a_station_we_do_not_know_reads_as_unknown_rather_than_as_absent():
    station, status = resolution_station_checked(
        _market(rules='Resolves on the KBOS station.'))
    assert (station, status) == (None, 'resolution_station_unknown')


#: The two URL forms every live market uses, copied off real markets.
WUNDERGROUND_RULES = (
    "This market will resolve to the temperature range that contains the "
    "highest temperature recorded at the LaGuardia Airport Station in degrees "
    "Fahrenheit on 18 Aug '26. Available here: "
    'https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA')
NOAA_TIMESERIES_RULES = (
    'This market will resolve to the temperature range that contains the '
    'highest temperature recorded by NOAA at the Istanbul Airport, available '
    'here: https://www.weather.gov/wrh/timeseries?site=LTFM')


def test_the_icao_is_read_out_of_the_resolution_url():
    """The measured bug: 1,595 of 1,727 live markets read as
    `resolution_station_unknown` because the station only ever appears as the
    last path segment of the market's own resolution link."""
    station, status = resolution_station_checked(
        _market(rules=WUNDERGROUND_RULES))
    assert (station, status) == ('KLGA', 'ok')


def test_an_icao_in_a_site_query_parameter_is_read_too():
    """Istanbul, Moscow and Tel Aviv have an EMPTY resolutionSource and name
    their station only in a `site=` link inside the description."""
    station, status = resolution_station_checked(
        _market(rules=NOAA_TIMESERIES_RULES))
    assert (station, status) == ('LTFM', 'ok')


def test_a_url_station_needs_no_entry_in_the_alias_table():
    """Deliberately different from the prose route. A four-letter uppercase
    token in the market's own resolution link IS the station identifier; the
    market is telling us where to look, not being interpreted."""
    assert 'ltfm' not in wx.STATION_ALIASES
    assert 'zsjn' not in wx.STATION_ALIASES
    assert wx.stations_in_urls(
        'https://www.wunderground.com/history/daily/cn/jinan/ZSJN') == ['ZSJN']


def test_lowercase_url_path_segments_are_not_mistaken_for_stations():
    """`/history/`, `/daily/` and `/us/` sit in every one of these URLs."""
    assert wx.stations_in_urls(
        'https://www.wunderground.com/history/daily/us/ny/new-york-city/'
    ) == []


def test_the_prose_route_and_the_url_route_agreeing_is_not_ambiguity():
    """The real NYC rules text names the station twice, once each way. Taking
    the union before the count is what stops that reading as two stations."""
    text = WUNDERGROUND_RULES
    assert 'LaGuardia' in text and '/KLGA' in text
    assert wx.all_stations_named_in(text) == ['KLGA']
    assert resolution_station_checked(_market(rules=text))[1] == 'ok'


def test_two_genuinely_different_stations_are_still_ambiguous_via_urls():
    station, status = resolution_station_checked(_market(
        rules='See https://x/KLGA and https://x/KMIA for details.'))
    assert station is None
    assert status == 'resolution_station_ambiguous'


def test_the_city_fallback_table_is_off_by_default():
    """Hong Kong names no station anywhere in its rules: it resolves on the
    Hong Kong Observatory HQ, not an airport."""
    hk = _market(question='Will the highest temperature in Hong Kong be '
                          '30°C on August 18?',
                 rules='Resolves on the Hong Kong Observatory reading.')
    assert resolution_station_checked(hk) == (None,
                                              'resolution_station_unknown')
    assert WeatherArb().allow_station_fallback is False


def test_the_city_fallback_table_is_reachable_when_explicitly_enabled():
    hk = _market(question='Will the highest temperature in Hong Kong be '
                          '30°C on August 18?',
                 rules='Resolves on the Hong Kong Observatory reading.')
    station, status = resolution_station_checked(hk, allow_fallback=True)
    assert (station, status) == ('VHHH', 'ok_from_city_fallback_table')


def test_a_fallback_station_is_stamped_as_a_guess_on_the_row():
    """It is a PROXY, 25km from the station the market resolves on. A row that
    used it has to be scoreable separately or it is laundering an assumption."""
    # The whole-degree sentence is synthetic. Real Hong Kong markets report to
    # one decimal place and stop at `source_precision_finer_than_ladder_step`
    # before any feed is touched; it is added here only so the station stamp is
    # observable at all, and the two refusals are tested separately.
    hk = _market(question='Will the highest temperature in Hong Kong be '
                          '30°C on August 18?',
                 rules='Resolves on the Hong Kong Observatory reading. The '
                       'resolution source for this market measures '
                       'temperatures to whole degrees Celsius (eg, 9C).')
    strategy = _strategy(allow_station_fallback=True,
                         allow_daily_extreme_markets=True)
    feats = strategy.evaluate(_ctx(market=hk)).features
    assert feats['station_is_a_fallback_guess'] is True
    assert feats['station_source'] == 'city_fallback_table'
    assert strategy.airport_feed.calls == ['VHHH']
    plain = _strategy().evaluate(_ctx(market=hk))
    assert plain.reason == 'resolution_station_unknown'
    assert plain.features['station_is_a_fallback_guess'] is False


def test_the_fallback_table_never_overrides_a_station_named_in_the_rules():
    """THE RULES WIN. Moscow's rules say UUWW; the table must not be able to
    substitute anything, and cannot even be reached."""
    assert wx.CITY_STATION_FALLBACK['moscow'] == 'UUWW'
    moscow = _market(
        question='Will the highest temperature in Moscow be 18°C on Aug 18?',
        rules='Recorded by NOAA at Vnukovo, https://www.weather.gov/wrh/'
              'timeseries?site=UUWW')
    assert resolution_station_checked(moscow, allow_fallback=True) == (
        'UUWW', 'ok')


def test_the_row_flags_when_the_table_disagrees_with_the_rules():
    """The only way a wrong table entry becomes visible instead of a loss."""
    strategy = _strategy()
    market = _market(rules='Resolves on the LaGuardia observation.')
    decision = strategy.evaluate(_ctx(market=market))
    assert decision.features['assumed_station_from_table'] == 'KNYC'
    assert decision.features['rules_station'] == 'KLGA'
    assert decision.features['station_assumption_matches_rules'] is False
    # And it FETCHED the rules station, not the assumed one.
    assert strategy.airport_feed.calls == ['KLGA']


# ============ 3. threshold parsing. No default, ever. ============

@pytest.mark.parametrize('question,value,above', [
    ('Will NYC exceed 85F on August 18?', 85.0, True),
    ('Will Chicago be above 90 degrees F?', 90.0, True),
    ('Will Denver go over 100F?', 100.0, True),
    ('Will Miami be at least 95F today?', 95.0, True),
    ('Will LA close below 60F?', 60.0, False),
    ('Will NYC be under 32 degrees Fahrenheit?', 32.0, False),
    ('Will Denver be less than -5F?', -5.0, False),
])
def test_parse_threshold_reads_value_and_direction(question, value, above):
    parsed = parse_threshold(question)
    assert isinstance(parsed, Threshold)
    assert parsed.value_f == value
    assert parsed.above is above


@pytest.mark.parametrize('question', [
    QUESTION_SCALAR,
    'What will the temperature be in NYC?',
    '',
    None,
])
def test_parse_threshold_returns_none_rather_than_a_default(question):
    assert parse_threshold(question) is None


# ---- 3a. the POSTFIX form, which is the only one live markets use ----

@pytest.mark.parametrize('question,kind,lo,hi,above', [
    # "85F or below" means REPORTED <= 85, and the source reports whole
    # degrees, so the continuous boundary is 85.5 and not 85.0.
    ('Will the highest temperature in NYC be 85°F or below on August 18?',
     'at_or_below', None, 85.5, False),
    ('Will the highest temperature in NYC be 88°F or higher on August 18?',
     'at_or_above', 87.5, None, True),
    ('Will the highest temperature in NYC be 88F or above on August 18?',
     'at_or_above', 87.5, None, True),
    ('Will the highest temperature in NYC be 60F or under on August 18?',
     'at_or_below', None, 60.5, False),
])
def test_postfix_comparators_parse_into_the_right_half_open_interval(
        question, kind, lo, hi, above):
    parsed = parse_threshold(question)
    assert parsed is not None
    assert parsed.kind == kind
    assert parsed.above is above
    assert parsed.lo_f == (None if lo is None else pytest.approx(lo))
    assert parsed.hi_f == (None if hi is None else pytest.approx(hi))
    assert parsed.is_ladder_rung is True


def test_the_original_bug_every_live_question_shape_now_parses():
    """These five strings were measured live on 2026-08-18 and every one of
    them returned None before this fix, so the strategy could not fire on a
    single real Polymarket market."""
    live = [
        'Will the highest temperature in New York City be 84°F on August 18?',
        'Will the highest temperature in New York City be 80°F or below on '
        'August 18?',
        'Will the highest temperature in New York City be 88°F or higher on '
        'August 18?',
        'Will the highest temperature in Hong Kong be 30°C on August 18?',
        'Will the highest temperature in Tokyo be 33°C or higher on August 18?',
        'Will the highest temperature in New York City be between 76-77°F on '
        'August 18?',
    ]
    for question in live:
        parsed, status = parse_threshold_checked(question)
        assert status == 'ok', question
        assert parsed is not None, question


# ---- 3b. UNITS. The safety-critical part. ----

def test_a_celsius_question_is_converted_and_never_stored_as_fahrenheit():
    """30C is 86F. Storing 30.0 in `value_f` would price a 56F error as edge
    on every rung of a Celsius ladder, and 1,485 of 1,771 live markets are
    Celsius."""
    parsed = parse_threshold(
        'Will the highest temperature in Tokyo be 30°C or higher on August 18?')
    assert parsed.unit == 'C'
    assert parsed.native_lo == pytest.approx(29.5)
    assert parsed.lo_f == pytest.approx(wx.c_to_f(29.5))
    assert parsed.lo_f == pytest.approx(85.1)
    assert parsed.lo_f != pytest.approx(29.5)


def test_a_celsius_bucket_is_widened_before_conversion_not_after():
    """THE ORDERING RULE. Half a degree Celsius is 0.9F, not 0.5F, so a 1C
    bucket is 1.8F wide. Building the interval in F first and widening there
    gives 1.0F, a 44% understatement that reads as free edge on every interior
    rung. The test pins the WIDTH, which is the thing the ordering changes."""
    parsed = parse_threshold(LADDER_BUCKET_C)
    assert parsed.kind == 'exact_bucket'
    assert (parsed.native_lo, parsed.native_hi) == (29.5, 30.5)
    assert parsed.hi_f - parsed.lo_f == pytest.approx(1.8)
    assert parsed.lo_f == pytest.approx(wx.c_to_f(29.5))
    assert parsed.hi_f == pytest.approx(wx.c_to_f(30.5))
    # The wrong ordering would have produced exactly this, so name it.
    wrong = wx.c_to_f(30.0) - 0.5, wx.c_to_f(30.0) + 0.5
    assert (parsed.lo_f, parsed.hi_f) != pytest.approx(wrong)


def test_a_fahrenheit_bucket_is_one_degree_wide():
    parsed = parse_threshold(
        'Will the highest temperature in NYC be 84°F on August 18?')
    assert parsed.unit == 'F'
    assert (parsed.lo_f, parsed.hi_f) == (83.5, 84.5)
    assert parsed.hi_f - parsed.lo_f == pytest.approx(1.0)


@pytest.mark.parametrize('question', [
    'Will the highest temperature in NYC be 84 on August 18?',
    'Will the highest temperature in NYC be 84 degrees on August 18?',
    'Will the highest temperature in NYC be 84 or below on August 18?',
])
def test_a_missing_unit_is_refused_and_never_assumed_fahrenheit(question):
    parsed, status = parse_threshold_checked(question)
    assert parsed is None
    assert status == 'unit_missing_or_ambiguous'


def test_a_question_naming_two_units_is_ambiguous_not_first_wins():
    parsed, status = parse_threshold_checked(
        'Will the high be 30°C, that is 86°F, or higher on August 18?')
    assert parsed is None
    assert status == 'unit_missing_or_ambiguous'


def test_the_parsed_unit_is_on_every_logged_row():
    feats = _ladder_strategy(temp_f=COLD_F).evaluate(_ctx(
        market=_market(question=LADDER_BUCKET_C,
                       rules=WHOLE_DEGREE_C_RULES))).features
    assert feats['threshold_unit'] == 'C'
    assert feats['threshold_native_lo'] == 29.5
    assert feats['threshold_native_hi'] == 30.5


# ---- 3c. buckets as intervals ----

def test_a_range_bucket_spans_both_named_degrees():
    """The Fahrenheit ladders are two degrees per rung: "between 76-77F"."""
    parsed = parse_threshold(LADDER_RANGE_F)
    assert parsed.kind == 'range_bucket'
    assert (parsed.lo_f, parsed.hi_f) == (75.5, 77.5)
    assert parsed.ladder_step_native == 2.0
    assert parsed.above is None


def test_the_eleven_rungs_of_a_real_ladder_tile_with_no_gap_or_overlap():
    """The tiling is the proof the half-step is right. A gap means some
    outcome prices to nothing; an overlap means the eleven prices sum past 1."""
    rungs = ['Will the highest temperature in NYC be 75°F or below on Aug 18?']
    rungs += ['Will the highest temperature in NYC be between {}-{}°F on '
              'Aug 18?'.format(lo, lo + 1) for lo in range(76, 94, 2)]
    rungs += ['Will the highest temperature in NYC be 94°F or higher on '
              'Aug 18?']
    parsed = [parse_threshold(q) for q in rungs]
    assert all(p is not None for p in parsed)
    assert parsed[0].lo_f is None and parsed[-1].hi_f is None
    for lower, upper in zip(parsed, parsed[1:]):
        assert lower.hi_f == pytest.approx(upper.lo_f)


def test_a_bucket_prices_as_a_band_and_not_as_a_tail():
    """A 1F bucket centred on the reading must price WELL under a half. Pricing
    it as `above` would return 0.5 and manufacture edge on every rung."""
    strategy = WeatherArb()
    bucket = parse_threshold(
        'Will the highest temperature in NYC be 84°F on August 18?')
    p_bucket = strategy.probability_yes(84.0, bucket, 6.0)
    assert 0.0 < p_bucket < 0.15
    tail = Threshold(84.0, True, 'above 84F')
    assert strategy.probability_yes(84.0, tail, 6.0) == pytest.approx(0.5)


def test_a_bucket_probability_is_never_negative_far_out_in_a_tail():
    strategy = WeatherArb()
    bucket = parse_threshold(
        'Will the highest temperature in NYC be 84°F on August 18?')
    assert strategy.probability_yes(-200.0, bucket, 0.0) >= 0.0
    assert strategy.probability_yes(400.0, bucket, 0.0) >= 0.0


def test_the_rungs_of_one_ladder_price_to_roughly_one_in_total():
    """Not exactly one: the tails are unbounded and the model is a normal, so
    this is a sanity check on the interval algebra, not a calibration claim."""
    strategy = WeatherArb()
    rungs = ['Will the highest temperature in NYC be 75°F or below on Aug 18?']
    rungs += ['Will the highest temperature in NYC be between {}-{}°F on '
              'Aug 18?'.format(lo, lo + 1) for lo in range(76, 94, 2)]
    rungs += ['Will the highest temperature in NYC be 94°F or higher on '
              'Aug 18?']
    total = sum(strategy.probability_yes(84.0, parse_threshold(q), 6.0)
                for q in rungs)
    assert total == pytest.approx(1.0, abs=1e-9)


# ---- 3d. the global temperature family ----

def test_the_global_temperature_family_is_excluded_under_its_own_reason():
    """Convention 20. "This is not a city airport market" and "we could not
    read the threshold" are two facts, and they never share one number."""
    parsed, status = parse_threshold_checked(GLOBAL_TEMP_QUESTION)
    assert parsed is None
    assert status == 'global_temperature_market'
    decision = _strategy().evaluate(
        _ctx(market=_market(question=GLOBAL_TEMP_QUESTION)))
    assert decision.reason == 'global_temperature_market_excluded'
    assert decision.reason != 'threshold_unparseable'
    assert decision.reason != 'resolution_station_unknown'


def test_the_global_family_is_refused_before_the_station_is_even_read():
    """It has no station either, so checking the station first would pool it
    with every unreadable rules text."""
    strategy = _strategy()
    strategy.evaluate(_ctx(market=_market(question=GLOBAL_TEMP_QUESTION,
                                          rules=KNYC_RULES)))
    assert strategy.airport_feed.calls == []


def test_the_masculine_ordinal_character_is_read_as_a_degree_sign():
    """The global family uses U+00BA, not U+00B0. A parser that knows only the
    degree sign reads "1.29ºC" as a bare number with no unit."""
    assert 'º' in GLOBAL_TEMP_QUESTION
    assert parse_threshold(
        'Will the highest temperature in Tokyo be 30ºC on August 18?'
    ).unit == 'C'


# ---- 3e. the reporting-precision gate on ladder edges ----

def test_a_ladder_needs_the_source_to_report_whole_degrees():
    decision = _ladder_strategy().evaluate(_ctx(
        market=_market(question=LADDER_TAIL_F, rules=ONE_DECIMAL_RULES)))
    assert decision.reason == 'source_precision_finer_than_ladder_step'
    assert decision.features['source_reporting_step_native'] == 0.1


def test_an_unstated_reporting_precision_is_its_own_refusal():
    decision = _ladder_strategy().evaluate(_ctx(
        market=_market(question=LADDER_TAIL_F, rules=KNYC_RULES)))
    assert decision.reason == 'source_reporting_precision_unknown'
    assert decision.features['source_reporting_step_native'] is None


def test_a_legacy_prefix_threshold_does_not_need_a_precision_statement():
    """It is a comparison on the temperature, not on a rounded report, so
    there is no rounding step for the source to disagree with."""
    decision = _strategy().evaluate(_ctx(market=_market(rules=KNYC_RULES)))
    assert decision.action == 'ENTER'
    assert decision.features['threshold_is_ladder_rung'] is False


def test_a_whole_degree_ladder_market_reaches_the_pricing_stage():
    """The end of the original bug: a real market shape now gets a price."""
    decision = _ladder_strategy(temp_f=COLD_F).evaluate(_ctx(
        market=_market(question=LADDER_TAIL_F, rules=WHOLE_DEGREE_F_RULES),
        yes_asks=((0.40, 100),)))
    assert decision.features['threshold_hi_f'] == 85.5
    assert decision.features['model_p_yes'] > 0.9
    assert decision.action == 'ENTER'


# ---- 3f. the daily-extreme refusal ----

def test_a_daily_extreme_market_is_refused_by_default():
    """100% of the live universe resolves on the day's extreme and this model
    prices a single reading at settlement. Measured live, the mismatch produced
    seven entries with 45c to 99.9c of "edge". That is model error with a price
    attached, so the default is a refusal."""
    decision = _strategy().evaluate(_ctx(
        market=_market(question=LADDER_TAIL_F, rules=WHOLE_DEGREE_F_RULES)))
    assert decision.action == 'SKIP'
    assert decision.reason == 'daily_extreme_not_priced_by_point_in_time_model'
    assert WeatherArb().allow_daily_extreme_markets is False


def test_the_daily_extreme_refusal_costs_nothing_downstream_of_it():
    """A cannot-run, not a result (convention 11). It must not fetch a METAR
    to reach a conclusion it already had."""
    strategy = _strategy()
    strategy.evaluate(_ctx(market=_market(question=LADDER_TAIL_F,
                                          rules=WHOLE_DEGREE_F_RULES)))
    assert strategy.airport_feed.calls == []


def test_the_daily_extreme_refusal_can_be_turned_off_for_tape_gathering():
    decision = _ladder_strategy(temp_f=COLD_F).evaluate(_ctx(
        market=_market(question=LADDER_TAIL_F, rules=WHOLE_DEGREE_F_RULES)))
    assert decision.action == 'ENTER'
    assert decision.features['allow_daily_extreme_markets'] is True


def test_a_market_that_is_not_a_daily_extreme_is_not_caught_by_the_gate():
    """The gate keys on the METRIC, not on the ladder. A point-in-time question
    is exactly what this model does price."""
    assert wx.market_metric(QUESTION_ABOVE) is None
    assert _strategy().evaluate(_ctx()).action == 'ENTER'


def test_the_market_metric_is_recorded_so_the_two_ladders_are_not_pooled():
    """A point-in-time model is biased in OPPOSITE directions on a daily-high
    ladder and a daily-low one. Pooling them averages the bias away on paper."""
    high = _ladder_strategy().evaluate(_ctx(
        market=_market(question=LADDER_TAIL_F, rules=WHOLE_DEGREE_F_RULES)))
    low = _ladder_strategy().evaluate(_ctx(market=_market(
        question='Will the lowest temperature in NYC be 60°F or below on '
                 'August 18?', rules=WHOLE_DEGREE_F_RULES)))
    assert high.features['market_metric'] == 'daily_high'
    assert low.features['market_metric'] == 'daily_low'
    assert high.features['model_prices_point_in_time_not_daily_extreme'] is True


def test_direction_is_not_hardcoded_a_below_market_prices_the_other_way():
    """A cold reading against a BELOW threshold is a high Yes probability.

    The whole point: nothing assumes which way the gap or the question runs.
    """
    strategy = _strategy(temp_f=COLD_F)
    below = _market(question='Will NYC close below 85F on August 18?')
    decision = strategy.evaluate(_ctx(market=below, yes_asks=((0.40, 100),)))
    assert decision.features['yes_needs_above'] is False
    assert decision.features['model_p_yes'] > 0.9


# ============ 4. the happy path ============

def test_happy_path_enters_on_the_side_the_airport_favours():
    strategy = _strategy(temp_f=HOT_F)
    decision = strategy.evaluate(_ctx())
    assert isinstance(decision, Decision)
    assert decision.action == 'ENTER'
    assert decision.reason == ''
    leg = decision.primary_leg
    assert leg.outcome_side == 'Yes'
    assert leg.order_type == 'taker'
    assert leg.expected_price == pytest.approx(0.40)
    assert leg.limit_price >= leg.expected_price
    assert decision.features['realized_edge'] >= wx.MIN_EDGE


def test_the_entry_maps_onto_a_signal_with_a_stop_strictly_below_entry():
    """Convention 8. On a binary a losing share is worth exactly 0.00."""
    strategy = _strategy()
    signal = strategy.decision_to_signal(strategy.evaluate(_ctx()))
    assert signal is not None
    assert signal.stop == 0.0
    assert signal.stop < signal.entry <= 1.0
    assert signal.target == 1.0
    assert signal.pattern == 'PM_weather_arb'


def test_the_walked_price_is_reported_not_the_cap():
    """base.Leg.premium's house rule: report what the fill cost, not the limit."""
    strategy = _strategy()
    decision = strategy.evaluate(_ctx(yes_asks=((0.40, 6), (0.44, 100))))
    assert decision.action == 'ENTER'
    leg = decision.primary_leg
    assert 0.40 < leg.premium < 0.44
    assert leg.premium < leg.limit_price
    # `effective_ask` on the row is the same number, rounded for the log.
    assert decision.features['effective_ask'] == pytest.approx(leg.premium,
                                                               abs=5e-5)


def test_size_is_capped_by_the_ten_dollar_notional_not_rejected_downstream():
    strategy = _strategy()
    decision = strategy.evaluate(_ctx())
    feats = decision.features
    assert feats['shares'] <= feats['target_shares']
    assert feats['shares'] * feats['entry_cap'] <= wx.MAX_NOTIONAL_USDC + 1e-9


# ============ 5. provenance stamps, on EVERY row ============

def _all_decisions():
    """One decision per ordinary named skip reason, keyed by that reason.

    The two invariant guards are absent on purpose; they have their own tests
    below and cannot be reached without patching.
    """
    out = {}
    out['no_market'] = _strategy().evaluate(_ctx(market=None))
    out['no_clock'] = _strategy().evaluate(_ctx(window_ts=0))
    out['global_temperature_market_excluded'] = _strategy().evaluate(
        _ctx(market=_market(question=GLOBAL_TEMP_QUESTION)))
    out['source_reporting_precision_unknown'] = _ladder_strategy().evaluate(
        _ctx(market=_market(question=LADDER_TAIL_F, rules=KNYC_RULES)))
    out['source_precision_finer_than_ladder_step'] = _ladder_strategy().evaluate(
        _ctx(market=_market(question=LADDER_TAIL_F, rules=ONE_DECIMAL_RULES)))
    out['daily_extreme_not_priced_by_point_in_time_model'] = _strategy().evaluate(
        _ctx(market=_market(question=LADDER_TAIL_F,
                            rules=WHOLE_DEGREE_F_RULES)))
    out['resolution_station_unknown'] = _strategy().evaluate(
        _ctx(market=_market(rules=NO_STATION_RULES)))
    out['resolution_station_ambiguous'] = _strategy().evaluate(
        _ctx(market=_market(rules=AMBIGUOUS_RULES)))
    out['threshold_unparseable'] = _strategy().evaluate(
        _ctx(market=_market(question=QUESTION_SCALAR)))
    out['resolution_time_unknown'] = _strategy().evaluate(
        _ctx(market=_market(end_ts='missing')))
    out['market_past_resolution_time'] = _strategy().evaluate(
        _ctx(market=_market(end_ts=NOW - 3600)))
    out['resolution_too_far_out'] = _strategy().evaluate(
        _ctx(market=_market(end_ts=NOW + 72 * 3600)))
    out['airport_reading_unavailable'] = _strategy(
        airport_status='feed_network_failure').evaluate(_ctx())
    out['airport_obs_time_missing'] = _strategy(obs_ts=None).evaluate(_ctx())
    out['airport_obs_stale'] = _strategy(obs_ts=NOW - 7200).evaluate(_ctx())
    out['no_orderbook'] = _strategy().evaluate(_ctx(books={}))
    out['no_asks'] = _strategy().evaluate(
        _ctx(yes_asks=(), no_asks=(), yes_bids=((0.38, 10),),
             no_bids=((0.56, 10),)))
    out['market_implied_direction_unreadable'] = _strategy().evaluate(
        _ctx(yes_asks=((0.50, 100),)))
    out['airport_agrees_with_market'] = _strategy().evaluate(
        _ctx(yes_asks=((0.90, 100),)))
    out['unsizable_at_notional_cap'] = _strategy(
        max_notional_usdc=1.0).evaluate(_ctx())
    out['unfillable_at_cap'] = _strategy().evaluate(_ctx(yes_asks=((0.40, 3),)))
    out['edge_below_min'] = _strategy(min_edge=0.99).evaluate(_ctx())
    return out


@pytest.mark.parametrize('reason', sorted(_all_decisions()))
def test_every_row_carries_the_unverified_vendor_stamps(reason):
    feats = _all_decisions()[reason].features
    assert feats['claimed_gap_is_unverified_vendor_number'] is True
    assert feats['gap_never_measured_by_us'] is True
    assert feats['gap_sign_is_not_constant'] is True
    assert feats['resolution_source_read_from_rules_text'] is True
    assert feats['paper_mode'] is True


def test_the_entry_row_carries_them_too():
    feats = _strategy().evaluate(_ctx()).features
    assert feats['claimed_gap_is_unverified_vendor_number'] is True
    assert feats['gap_never_measured_by_us'] is True
    assert feats['sigma_model_is_wrong_at_known_times_of_day'] is True
    assert feats['confidence_is_model_output_not_measured_win_rate'] is True


# ============ 6. every named skip reason is reachable, and named once ============

def test_every_decision_returns_a_decision_and_never_none():
    for reason, decision in _all_decisions().items():
        assert isinstance(decision, Decision), reason
        assert decision.strategy == 'PM_weather_arb', reason


def test_each_constructed_case_produces_exactly_the_reason_it_is_named_for():
    for reason, decision in _all_decisions().items():
        assert decision.action == 'SKIP', reason
        assert decision.reason == reason, (reason, decision.reason)


def test_skip_reasons_tuple_has_no_duplicates():
    assert len(wx.SKIP_REASONS) == len(set(wx.SKIP_REASONS))


def test_skip_reasons_tuple_matches_what_the_tests_can_reach():
    """The one guard not in `_all_decisions` is reached by the patch test
    below. Nothing else may be missing."""
    reached = set(_all_decisions())
    guards = {'effective_ask_above_cap'}
    assert reached | guards == set(wx.SKIP_REASONS)


# ============ 7. the invariant guards ============

def test_effective_ask_above_cap_guard_is_wired(monkeypatch):
    """walk_book cannot return this, but the cap IS the edge and a silent
    regression here would be invisible."""
    monkeypatch.setattr(wx, 'effective_ask_for', lambda *a, **k: 0.999)
    decision = _strategy().evaluate(_ctx())
    assert decision.reason == 'effective_ask_above_cap'


def test_walked_price_flavour_of_edge_below_min_is_wired(monkeypatch):
    """The second flavour of edge_below_min, reached by breaking the invariant
    it guards: a `floor_to_tick` that rounds UP produces a cap too generous for
    MIN_EDGE, and a fill at that cap no longer clears it."""
    monkeypatch.setattr(wx, 'floor_to_tick',
                        lambda price, tick=wx.PRICE_TICK: round(price + 0.02, 4))
    monkeypatch.setattr(wx, 'effective_ask_for',
                        lambda book, shares, limit: limit)
    decision = _strategy().evaluate(_ctx())
    assert decision.reason == 'edge_below_min'
    assert decision.features['edge_reason'] == 'walked_price_ate_the_edge'
    assert decision.features['realized_edge'] < wx.MIN_EDGE


def test_the_two_edge_below_min_flavours_are_distinguishable():
    decision = _strategy(min_edge=0.99).evaluate(_ctx())
    assert decision.reason == 'edge_below_min'
    assert decision.features['edge_reason'] == \
        'min_edge_exceeds_model_probability'


# ============ 8. staleness ============

def test_a_fresh_observation_passes_the_age_gate():
    decision = _strategy(obs_ts=NOW - wx.MAX_OBS_AGE_SEC + 1).evaluate(_ctx())
    assert decision.action == 'ENTER'


def test_an_observation_one_second_past_the_gate_is_refused():
    decision = _strategy(obs_ts=NOW - wx.MAX_OBS_AGE_SEC - 1).evaluate(_ctx())
    assert decision.reason == 'airport_obs_stale'
    assert decision.features['airport_obs_age_sec'] > wx.MAX_OBS_AGE_SEC


def test_an_unaged_observation_is_refused_separately_from_a_stale_one():
    """An observation we cannot age is not a fresh one, and the two causes must
    never share a reason string."""
    unaged = _strategy(obs_ts=None).evaluate(_ctx())
    stale = _strategy(obs_ts=NOW - 7200).evaluate(_ctx())
    assert unaged.reason == 'airport_obs_time_missing'
    assert stale.reason == 'airport_obs_stale'
    assert unaged.reason != stale.reason


# ============ 9. the downtown reading is diagnostic, never a gate ============

def test_a_failed_downtown_read_costs_the_measurement_not_the_trade():
    strategy = _strategy(downtown_status='feed_network_failure')
    decision = strategy.evaluate(_ctx())
    assert decision.action == 'ENTER'
    assert decision.features['gap_measured_this_row'] is False
    assert decision.features['airport_minus_downtown_f'] is None
    assert decision.features['downtown_feed_status'] == 'feed_network_failure'


def test_a_successful_downtown_read_records_the_signed_gap():
    strategy = WeatherArb(airport_feed=FakeAirportFeed(temp_f=92.0),
                          downtown_feed=FakeDowntownFeed(temp_f=86.0))
    decision = strategy.evaluate(_ctx())
    assert decision.features['airport_minus_downtown_f'] == pytest.approx(6.0)
    assert decision.features['gap_within_claimed_range'] is True


def test_the_gap_sign_is_recorded_both_ways_and_never_normalised():
    """Urban heat island one way, sea breeze the other. Nothing takes abs()
    before storing it."""
    warmer = WeatherArb(airport_feed=FakeAirportFeed(temp_f=92.0),
                        downtown_feed=FakeDowntownFeed(temp_f=86.0))
    cooler = WeatherArb(airport_feed=FakeAirportFeed(temp_f=92.0),
                        downtown_feed=FakeDowntownFeed(temp_f=98.0))
    assert warmer.evaluate(_ctx()).features['airport_minus_downtown_f'] > 0
    assert cooler.evaluate(_ctx()).features['airport_minus_downtown_f'] < 0


def test_downtown_can_be_turned_off_and_the_row_says_so():
    strategy = _strategy(fetch_downtown=False)
    decision = strategy.evaluate(_ctx())
    assert decision.action == 'ENTER'
    assert decision.features['downtown_feed_status'] == 'not_fetched'
    assert strategy.downtown_feed.calls == []


# ============ 10. the disagreement gate ============

def test_agreement_with_the_market_is_a_skip_not_a_trade():
    """The claimed edge comes from retail anchoring on the WRONG thermometer.
    When they are anchored on the right one there is nothing here."""
    decision = _strategy(temp_f=HOT_F).evaluate(_ctx(yes_asks=((0.90, 100),)))
    assert decision.reason == 'airport_agrees_with_market'
    assert decision.features['market_implied_side'] == 'Yes'
    assert decision.features['model_side'] == 'Yes'


def test_a_market_sitting_exactly_on_fifty_cents_has_no_direction_to_disagree():
    decision = _strategy().evaluate(_ctx(yes_asks=((0.50, 100),)))
    assert decision.reason == 'market_implied_direction_unreadable'


def test_implied_probability_inferred_from_the_no_ask_is_labelled_as_inferred():
    decision = _strategy(temp_f=HOT_F).evaluate(
        _ctx(yes_asks=(), yes_bids=((0.38, 10),), no_asks=((0.70, 100),)))
    assert decision.features['market_implied_source'] == 'inferred_from_no_ask'
    assert decision.features['market_implied_p_yes'] == pytest.approx(0.30)


# ============ 11. the probability model ============

def test_sigma_grows_with_the_square_root_of_hours_remaining():
    strategy = WeatherArb()
    assert strategy.sigma_f(0.0) == pytest.approx(wx.SIGMA_FLOOR_F)
    assert strategy.sigma_f(4.0) == pytest.approx(
        wx.SIGMA_FLOOR_F + 2.0 * wx.SIGMA_PER_SQRT_HOUR_F)
    assert strategy.sigma_f(9.0) > strategy.sigma_f(4.0)


def test_a_reading_on_the_threshold_prices_a_coin_flip():
    strategy = WeatherArb()
    threshold = Threshold(85.0, True, 'exceed 85F')
    assert strategy.probability_yes(85.0, threshold, 6.0) == pytest.approx(0.5)


def test_below_thresholds_are_the_exact_complement_of_above_thresholds():
    strategy = WeatherArb()
    above = Threshold(85.0, True, 'x')
    below = Threshold(85.0, False, 'x')
    assert (strategy.probability_yes(90.0, above, 6.0)
            + strategy.probability_yes(90.0, below, 6.0)) == pytest.approx(1.0)


# ============ 12. discovery ============

class _StubClient(object):
    """Answers `gamma('/events', ...)`, which is all discovery uses.

    Returns the SAME page for every offset on purpose. A short page (fewer than
    GAMMA_PAGE_LIMIT events) must end pagination, so a stub that never runs out
    proves the terminator rather than relying on it.
    """

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def gamma(self, path, params=None):
        self.calls.append((path, params))
        return self.payload


def _gamma_market(slug, question, closed=False, active=True, end_ts=None,
                  accepting=True):
    if end_ts is None:
        end_ts = NOW + 6 * 3600
    return {'id': slug, 'slug': slug, 'question': question,
            'conditionId': 'c-' + slug, 'closed': closed, 'active': active,
            'acceptingOrders': accepting,
            'endDate': None if end_ts == 'missing' else _iso(end_ts),
            'outcomes': '["Yes", "No"]',
            'clobTokenIds': '["{}-y", "{}-n"]'.format(slug, slug)}


#: The stub serves the same page to every tag, so a single raw market is seen
#: once PER TAG. Spelling that out beats a bare 2 that goes stale the day a
#: third tag is added.
_TAGS = len(wx.WEATHER_TAG_SLUGS)


def _discover(payload, **kwargs):
    kwargs.setdefault('now', NOW)
    return find_weather_markets(_StubClient(payload), **kwargs)


def test_find_weather_markets_returns_matching_markets():
    payload = {'events': [{'markets': [
        _gamma_market('nyc-temp-1', 'Will NYC exceed 85F on August 18?'),
        _gamma_market('btc-updown-5m-1', 'Will BTC go up?'),
    ]}]}
    result = _discover(payload)
    assert result['ok'] is True
    assert result['reason'] is None
    assert [m.slug for m in result['markets']] == ['nyc-temp-1']
    assert 'nyc' in result['by_city']


def test_discovery_shortlists_a_real_question_that_has_no_keyword_in_it():
    """"Will NYC exceed 85F" contains none of TEMPERATURE_KEYWORDS. A
    keyword-only filter would drop the exact markets this strategy exists for."""
    market = _market(question='Will NYC exceed 85F on August 18?',
                     slug='unmapped-slug')
    assert not any(k in market.question.lower()
                   for k in wx.TEMPERATURE_KEYWORDS)
    assert wx.looks_like_a_temperature_market(market) is True


def test_discovery_does_not_shortlist_a_crypto_threshold_question():
    """"Will BTC exceed 50000?" parses as a threshold under the loose regex and
    must not be shortlisted on that alone."""
    market = _market(question='Will BTC exceed 50000?', slug='btc-updown-5m-1')
    assert wx.looks_like_a_temperature_market(market) is False


def test_no_matching_market_is_no_weather_market_and_not_a_result():
    payload = {'events': [{'markets': [
        _gamma_market('btc-updown-5m-1', 'Will BTC go up?')]}]}
    result = _discover(payload)
    assert result['ok'] is True
    assert result['reason'] == 'no_weather_market'
    assert result['markets'] == []


def test_a_failed_gamma_read_is_read_failed_not_an_empty_market_list():
    """Convention 11. A run that found nothing because the API was down and one
    that found nothing because nothing was listed are different facts."""
    result = _discover(None)
    assert result['ok'] is False
    assert result['reason'] == 'read_failed'
    assert result['markets'] == []


def test_closed_markets_are_excluded_from_discovery():
    payload = {'events': [{'markets': [
        _gamma_market('nyc-temp-1', 'Will NYC exceed 85F?', closed=True)]}]}
    result = _discover(payload)
    assert result['reason'] == 'no_weather_market'
    assert result['drops'] == {'closed': _TAGS}


# ---- discovery, the parts the tag route added ----

def test_discovery_uses_the_tag_route_and_only_the_client():
    """Convention: no raw sockets, no second copy of the Gamma host. The stub
    client has ONE method, so anything else would raise."""
    client = _StubClient({'events': []})
    find_weather_markets(client, now=NOW)
    paths = {path for path, _params in client.calls}
    assert paths == {'/events'}
    tags = {params['tag_slug'] for _path, params in client.calls}
    assert tags == set(wx.WEATHER_TAG_SLUGS)
    assert all(params['closed'] == 'false' for _p, params in client.calls)


def test_a_stale_market_is_dropped_at_discovery_under_its_own_reason():
    """51 stale markets sat under the tag when this was measured. Letting them
    through spends a METAR fetch per rung to learn what endDate already said."""
    payload = {'events': [{'markets': [
        _gamma_market('nyc-temp-old', LADDER_TAIL_F, end_ts=NOW - 3600),
        _gamma_market('nyc-temp-new', LADDER_TAIL_F),
    ]}]}
    result = _discover(payload)
    assert [m.slug for m in result['markets']] == ['nyc-temp-new']
    assert result['drops']['end_date_past'] == _TAGS


def test_a_market_with_no_end_date_is_its_own_drop_not_pooled_with_stale():
    """We cannot tell a fresh one from a stale one. That is not the same fact
    as knowing it is stale, so it does not share the number."""
    payload = {'events': [{'markets': [
        _gamma_market('nyc-temp-x', LADDER_TAIL_F, end_ts='missing')]}]}
    result = _discover(payload)
    assert result['drops'] == {'end_date_missing': _TAGS}
    assert 'end_date_past' not in result['drops']


def test_the_global_temperature_family_never_reaches_discovery_output():
    payload = {'events': [{'markets': [
        _gamma_market('global-temp-1', GLOBAL_TEMP_QUESTION),
        _gamma_market('nyc-temp-1', LADDER_TAIL_F),
    ]}]}
    result = _discover(payload)
    assert [m.slug for m in result['markets']] == ['nyc-temp-1']
    assert result['drops']['not_a_temperature_market'] == _TAGS


def test_discovery_accounts_for_every_raw_market_exactly_once():
    """Convention 20. The identity is asserted inside `find_weather_markets`;
    this checks the numbers it reports add up for a reader too."""
    payload = {'events': [{'markets': [
        _gamma_market('a', LADDER_TAIL_F),
        _gamma_market('b', LADDER_TAIL_F, closed=True),
        _gamma_market('c', LADDER_TAIL_F, active=False),
        _gamma_market('d', LADDER_TAIL_F, accepting=False),
        _gamma_market('e', LADDER_TAIL_F, end_ts='missing'),
        _gamma_market('f', LADDER_TAIL_F, end_ts=NOW - 1),
        _gamma_market('g', 'Will BTC go up?'),
        _gamma_market('h', GLOBAL_TEMP_QUESTION),
    ]}]}
    result = _discover(payload)
    assert result['raw_count'] == 8 * _TAGS
    assert len(result['markets']) + sum(result['drops'].values()) == \
        result['raw_count']
    assert set(result['drops']) <= set(wx.DISCOVERY_DROP_REASONS)


def test_the_same_market_under_two_tags_is_counted_once_and_named():
    payload = {'events': [{'markets': [_gamma_market('nyc-1', LADDER_TAIL_F)]}]}
    result = _discover(payload)
    assert len(result['markets']) == 1
    # Two tags, one market each pass, one of them a duplicate.
    assert result['drops']['duplicate_across_tags'] == 1


def test_by_city_uses_the_question_city_not_just_the_five_table_cities():
    """The old grouping put every real market in `{'unmapped': 33}`."""
    payload = {'events': [{'markets': [
        _gamma_market('t-1', 'Will the highest temperature in Tokyo be '
                             '30°C on August 18?'),
        _gamma_market('s-1', 'Will the highest temperature in Seoul (Incheon) '
                             'be 30°C on August 18?'),
    ]}]}
    result = _discover(payload)
    assert set(result['by_city']) == {'tokyo', 'seoul'}


def test_a_limit_marks_the_result_truncated_rather_than_looking_complete():
    payload = {'events': [{'markets': [
        _gamma_market('a', LADDER_TAIL_F), _gamma_market('b', LADDER_TAIL_F),
    ]}]}
    result = _discover(payload, limit=1)
    assert len(result['markets']) == 1
    assert result['truncated'] is True


def test_an_unexpected_payload_shape_is_a_read_failure_not_an_empty_page():
    """An empty page ends pagination. A shape we cannot read must NOT look like
    one, or the universe silently truncates at the first bad response."""
    result = _discover('not a payload')
    assert result['ok'] is False
    assert result['reason'] == 'read_failed'
    assert result['read_failures'] == {'unexpected_shape': 2}


# ============ 13. the feeds themselves, offline ============

class _StubResponse(object):
    def __init__(self, status_code=200, payload=None, raises=False):
        self.status_code = status_code
        self._payload = payload
        self._raises = raises

    def json(self, **kwargs):
        if self._raises:
            raise ValueError('not json')
        return self._payload


class _StubSession(object):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _airport(responses, retries=2):
    return AirportWeatherFeed(session=_StubSession(responses),
                              retries=retries, sleep_fn=lambda s: None)


def test_airport_feed_parses_celsius_into_fahrenheit():
    payload = [{'icaoId': 'KNYC', 'temp': 30.0, 'obsTime': NOW - 60,
                'rawOb': 'KNYC ...'}]
    reading, status = _airport(
        [_StubResponse(payload=payload)]).observation('knyc')
    assert status == 'ok'
    assert reading.temp_f == pytest.approx(86.0)
    assert reading.station == 'KNYC'
    assert reading.observed_ts == NOW - 60


def test_airport_feed_uses_a_two_second_timeout():
    session = _StubSession([_StubResponse(payload=[{'temp': 20, 'obsTime': 1}])])
    AirportWeatherFeed(session=session,
                       sleep_fn=lambda s: None).observation('KNYC')
    assert session.calls[0][2] == wx.FEED_TIMEOUT_SEC == 2.0


def test_airport_feed_retries_a_transient_failure_then_succeeds():
    payload = [{'temp': 10.0, 'obsTime': NOW}]
    feed = _airport([_StubResponse(status_code=503),
                     _StubResponse(payload=payload)])
    reading, status = feed.observation('KNYC')
    assert status == 'ok'
    assert reading.temp_f == pytest.approx(50.0)
    assert feed.stats['retries'] == 1


def test_airport_feed_does_not_retry_a_4xx():
    feed = _airport([_StubResponse(status_code=404), _StubResponse(payload=[])])
    reading, status = feed.observation('KZZZ')
    assert reading is None
    assert status == 'feed_http_error'
    assert feed.stats.get('retries', 0) == 0


def test_a_network_exception_never_escapes_the_feed():
    feed = _airport([RuntimeError('boom'), RuntimeError('boom again')])
    reading, status = feed.observation('KNYC')
    assert reading is None
    assert status == 'feed_network_failure'


def test_a_nan_temperature_is_refused_rather_than_propagated():
    """convention 19: float('NaN') succeeds and then poisons every average."""
    feed = _airport([_StubResponse(payload=[{'temp': float('nan'),
                                             'obsTime': NOW}])])
    reading, status = feed.observation('KNYC')
    assert reading is None
    assert status == 'airport_non_finite_temperature'


def test_an_empty_metar_list_is_no_observation_not_a_temperature():
    reading, status = _airport([_StubResponse(payload=[])]).observation('KNYC')
    assert reading is None
    assert status == 'airport_no_observation'


def test_downtown_feed_reads_fahrenheit_straight_through():
    payload = {'current': {'temperature_2m': 78.4, 'time': '2026-08-18T12:00'}}
    feed = DowntownWeatherFeed(
        session=_StubSession([_StubResponse(payload=payload)]),
        sleep_fn=lambda s: None)
    reading, status = feed.observation(40.7128, -74.0060)
    assert status == 'ok'
    assert reading.temp_f == pytest.approx(78.4)
    assert reading.observed_ts is not None


def test_downtown_feed_asks_open_meteo_for_fahrenheit():
    session = _StubSession([_StubResponse(payload={'current': {
        'temperature_2m': 70.0, 'time': '2026-08-18T12:00'}})])
    DowntownWeatherFeed(session=session,
                        sleep_fn=lambda s: None).observation(1.0, 2.0)
    assert session.calls[0][1]['temperature_unit'] == 'fahrenheit'


def test_a_missing_current_block_is_its_own_reason():
    feed = DowntownWeatherFeed(
        session=_StubSession([_StubResponse(payload={})]),
        sleep_fn=lambda s: None)
    reading, status = feed.observation(1.0, 2.0)
    assert reading is None
    assert status == 'downtown_no_current_block'
