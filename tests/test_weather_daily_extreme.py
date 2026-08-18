"""Tests for the DAILY EXTREME model in PM_weather_arb. Fully offline.

`tests/test_weather_arb.py` owns the parser, the station table and the gate
ordering. This file owns the thing that was actually wrong: the model priced a
single reading at a settlement timestamp while every live market resolves on the
station's daily extreme, and those are different random variables.

Four jobs:

  1. THE OBSERVED EXTREME IS A HARD BOUND, NOT A PRIOR. If the station has
     already reported 33.0C today then "the highest today is 30C or below" is
     false with probability EXACTLY zero. No sigma, no forecast, no argument.
     Half of these tests exist to pin that the arithmetic is an indicator
     function and not a very confident normal.

  2. THE TWO ROWS THAT MOTIVATED THE FIX. Madrid and Buenos Aires, same minute,
     2026-08-18, where the old model was wrong in OPPOSITE directions. Both are
     reconstructed from the numbers recorded in the module docstring and the new
     model is required not to reproduce them.

  3. THE FEEDS ARE PARSED FROM MEASURED PAYLOADS. Every fake response in here is
     the SHAPE that came back from the live endpoint on 2026-08-18, including
     the fields that broke something: `lat`/`lon` on a METAR row, `timezone`
     and `utc_offset_seconds` on an open-meteo forecast, and naive local hourly
     strings that are NOT UTC.

  4. UNITS. open-meteo's documented default is Celsius under the identical field
     name `temperature_2m_max`, so a dropped `temperature_unit` parameter is a
     silent 50-degree error that reads as a huge edge. The unit is verified and
     a Celsius response is converted with the substitution recorded.

NOTHING HERE TOUCHES THE NETWORK. Every session is a stub. If a test in this
file ever needs a live endpoint, the model has grown a hidden fetch and that is
the finding.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import strategies.polymarket.weather_arb as wx  # noqa: E402
from strategies.polymarket.weather_arb import (  # noqa: E402
    AirportWeatherFeed, DailyExtremeEstimate, StationForecastFeed,
    daily_extreme_cdf, daily_extreme_sigma_f, parse_threshold,
    probability_yes_daily_extreme, resolution_local_date_checked,
    resolution_month_day)

NOW = 1787065200                      # 2026-08-18T15:00:00Z
DAY_START = 1787011200                # 2026-08-18T00:00:00Z
DAY_END = DAY_START + 86400
LOCAL_DATE = '2026-08-18'


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _estimate(metric='daily_high', mu_f=95.0, sigma_f=2.0, observed_f=None,
              observations=6, bias_f=0.0, hours=9.0):
    return DailyExtremeEstimate(
        metric=metric, mu_f=mu_f, sigma_f=sigma_f,
        forecast_extreme_f=mu_f - bias_f, bias_f=bias_f,
        observed_extreme_f=observed_f, observations_used=observations,
        hours_to_window_close=hours, local_date=LOCAL_DATE,
        window_start_ts=DAY_START, window_end_ts=DAY_END)


class _Resp(object):
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError('not json')
        return self._payload


class _Session(object):
    """Records every request. Returns queued responses in order."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests = []

    def get(self, url, params=None, timeout=None):
        self.requests.append({'url': url, 'params': dict(params or {}),
                              'timeout': timeout})
        if not self._responses:
            raise AssertionError('stub session ran out of responses: %r'
                                 % (self.requests[-1],))
        out = self._responses.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


#: A METAR row in EXACTLY the shape aviationweather.gov returned on 2026-08-18
#: for `ids=KLGA&format=json`. The full key list was
#: altim, clouds, cover, dewp, elev, fltCat, icaoId, lat, lon, metarType, name,
#: obsTime, precip, qcField, rawOb, receiptTime, reportTime, slp, temp, visib,
#: wdir, wspd. Only the ones this code reads are kept; `lat`/`lon` are the two
#: that matter here, because they are what makes a station forecast possible
#: without a second hand-maintained coordinate table.
def _metar_row(temp_c=25.0, obs_ts=NOW - 300, icao='KLGA'):
    return {'icaoId': icao, 'temp': temp_c, 'obsTime': obs_ts,
            'reportTime': '2026-08-18T14:00:00.000Z',
            'lat': 40.7794, 'lon': -73.8803,
            'name': 'New York/La Guardia Arpt, NY, US',
            'elev': 3, 'rawOb': 'KLGA 181400Z 20008KT 10SM CLR 25/18 A3003'}


#: An open-meteo forecast in EXACTLY the shape measured on 2026-08-18 for
#: KLGA's own coordinates. Note three things that are not decoration:
#:   - `daily.time` are LOCAL dates, because `timezone=auto` was sent
#:   - `utc_offset_seconds` is -14400, so the naive hourly strings are NOT UTC
#:   - `daily_units.temperature_2m_max` is the degree sign plus F, which is what
#:     the unit verification reads back
def _forecast_payload(offset=-14400, unit='°F', tz='America/New_York',
                      daily_max=(80.2, 82.8, 89.1),
                      daily_min=(68.6, 71.2, 71.6),
                      dates=('2026-08-17', '2026-08-18', '2026-08-19'),
                      hours=('2026-08-18T10:00', '2026-08-18T11:00'),
                      hour_temps=(73.2, 76.7)):
    return {
        'latitude': 40.78, 'longitude': -73.88,
        'utc_offset_seconds': offset, 'timezone': tz,
        'daily_units': {'time': 'iso8601', 'temperature_2m_max': unit,
                        'temperature_2m_min': unit},
        'daily': {'time': list(dates),
                  'temperature_2m_max': list(daily_max),
                  'temperature_2m_min': list(daily_min)},
        'hourly_units': {'temperature_2m': unit},
        'hourly': {'time': list(hours), 'temperature_2m': list(hour_temps)},
    }


# ===========================================================================
# 1. THE OBSERVED EXTREME IS A HARD BOUND
# ===========================================================================

def test_an_already_observed_high_makes_a_lower_rung_exactly_zero():
    """THE CORE FIX, in one assertion.

    The station has reported 91.4F today. "The highest today is 84F or below"
    cannot be true. Not 0.001, not 1e-9: exactly 0.0, because a day that has
    already produced 91.4 cannot have a maximum of 84.
    """
    estimate = _estimate(metric='daily_high', mu_f=84.0, sigma_f=2.0,
                         observed_f=91.4)
    threshold = parse_threshold(
        'Will the highest temperature in NYC be 84°F or below on '
        'August 18?')
    assert threshold.hi_f == 84.5
    assert probability_yes_daily_extreme(threshold, estimate) == 0.0


def test_the_same_rung_is_not_zero_without_the_observation():
    """The floor is doing the work, not the mean. Drop the observation and the
    identical rung prices as an ordinary normal, so a test asserting 0.0 above
    cannot be passing for some unrelated reason."""
    threshold = parse_threshold(
        'Will the highest temperature in NYC be 84°F or below on '
        'August 18?')
    without = probability_yes_daily_extreme(
        threshold, _estimate(mu_f=84.0, sigma_f=2.0, observed_f=None))
    assert 0.4 < without < 0.7


def test_an_already_observed_high_does_not_zero_a_rung_above_it():
    """The bound is one-sided. 91.4 observed says nothing against "88F or
    higher" except that it is already satisfied."""
    estimate = _estimate(metric='daily_high', mu_f=92.0, sigma_f=2.0,
                         observed_f=91.4)
    threshold = parse_threshold(
        'Will the highest temperature in NYC be 88°F or higher on '
        'August 18?')
    assert probability_yes_daily_extreme(threshold, estimate) == 1.0


def test_a_daily_low_bound_runs_the_other_way():
    """`min(O, X)`, not `max`. A station that already reported 41.0F cannot have
    a daily MINIMUM of 55F, however warm the rest of the day is."""
    estimate = _estimate(metric='daily_low', mu_f=55.0, sigma_f=2.0,
                         observed_f=41.0)
    threshold = parse_threshold(
        'Will the lowest temperature in NYC be 55°F or higher on '
        'August 18?')
    assert probability_yes_daily_extreme(threshold, estimate) == 0.0
    # And the rung the observation already satisfies is certain.
    below = parse_threshold(
        'Will the lowest temperature in NYC be 45°F or below on '
        'August 18?')
    assert probability_yes_daily_extreme(below, estimate) == 1.0


def test_the_high_and_low_bounds_are_not_the_same_function():
    """A guard against the two metrics being wired to one branch. The SAME
    observation and the SAME rung must price differently under the two."""
    rung = parse_threshold('Will the highest temperature in NYC be 80°F '
                           'or below on August 18?')
    high = probability_yes_daily_extreme(
        rung, _estimate(metric='daily_high', mu_f=78.0, observed_f=85.0))
    low = probability_yes_daily_extreme(
        rung, _estimate(metric='daily_low', mu_f=78.0, observed_f=85.0))
    assert high == 0.0
    assert low > 0.85


def test_the_cdf_is_monotone_non_decreasing_with_an_observation_present():
    """An indicator multiplied into a CDF is still a CDF. If it were not, a
    ladder could price a lower rung above a higher one."""
    estimate = _estimate(metric='daily_high', mu_f=90.0, sigma_f=2.5,
                         observed_f=88.0)
    values = [daily_extreme_cdf(x, estimate)
              for x in range(70, 110)]
    assert values == sorted(values)
    assert values[0] == 0.0            # below the observed floor
    assert values[-1] > 0.99


def test_a_full_ladder_still_sums_to_about_one_under_the_new_model():
    """Eleven mutually exclusive rungs tile the line, so their probabilities
    must sum to 1 whatever model prices them. This is the check that catches a
    bound applied to one edge of an interval and not the other."""
    estimate = _estimate(metric='daily_high', mu_f=86.3, sigma_f=2.4,
                         observed_f=81.0)
    questions = ['Will the highest temperature in NYC be 80°F or below '
                 'on August 18?']
    questions += ['Will the highest temperature in NYC be {}°F on '
                  'August 18?'.format(v) for v in range(81, 90)]
    questions.append('Will the highest temperature in NYC be 90°F or '
                     'higher on August 18?')
    total = sum(probability_yes_daily_extreme(parse_threshold(q), estimate)
                for q in questions)
    assert 0.999 < total < 1.001


def test_no_rung_ever_prices_below_zero():
    """The indicator can make both CDF terms land on the same value, and
    floating point can then return a hair below zero - which would walk into a
    negative edge and an entry."""
    estimate = _estimate(metric='daily_high', mu_f=60.0, sigma_f=1.0,
                         observed_f=95.0)
    for value in range(40, 130):
        rung = parse_threshold('Will the highest temperature in NYC be '
                               '{}°F on August 18?'.format(value))
        assert 0.0 <= probability_yes_daily_extreme(rung, estimate) <= 1.0


# ===========================================================================
# 2. THE TWO ROWS THAT MOTIVATED THE FIX
# ===========================================================================

def test_madrid_the_old_model_said_0_000024_and_the_market_said_0_70():
    """Reconstructed from the module docstring's measured row.

    Station at 33.0C at 11:00 UTC, market "the HIGHEST temperature today is
    39C" quoted 0.70 because the afternoon peak had not happened. The
    point-in-time model priced the reading at the settlement stamp and said
    0.000024, which is what a model that has never heard of an afternoon says.

    The daily-extreme model is handed a forecast peak near 39C. It must land
    somewhere a market quoting 0.70 could plausibly be right about, and the bar
    here is deliberately loose: this test pins that the old pathological answer
    is GONE, not that the new one is correct. Nothing has scored the new one.
    """
    rung = parse_threshold('Will the highest temperature in Madrid be 39°C '
                           'on August 18?')
    old = wx.WeatherArb(allow_daily_extreme_markets=True).probability_yes(
        wx.c_to_f(33.0), rung, hours_remaining=1.0)
    assert old < 0.001                       # the recorded failure, reproduced

    # Forecast peak 39.2C, station already at 33.0C with the afternoon to come.
    new = probability_yes_daily_extreme(rung, _estimate(
        metric='daily_high', mu_f=wx.c_to_f(39.2), sigma_f=2.0,
        observed_f=wx.c_to_f(33.0)))
    assert new > 0.30
    assert new > old * 1000


def test_buenos_aires_the_old_model_was_wrong_the_other_way():
    """Same minute, opposite error, which is what made a pooled win rate
    meaningless. Station 7.0C, "highest today is 8C or below" quoted 0.001
    because the afternoon would be warmer, and the old model said 0.87 because
    7.0 is below 8.5.

    The daily-extreme model is handed a forecast peak of 15C. The observed 7.0C
    is a floor and a floor does not help an "or below" rung at all, so the
    answer has to collapse.
    """
    rung = parse_threshold('Will the highest temperature in Buenos Aires be '
                           '8°C or below on August 18?')
    old = wx.WeatherArb(allow_daily_extreme_markets=True).probability_yes(
        wx.c_to_f(7.0), rung, hours_remaining=1.0)
    assert old > 0.80                        # the recorded failure, reproduced

    new = probability_yes_daily_extreme(rung, _estimate(
        metric='daily_high', mu_f=wx.c_to_f(15.0), sigma_f=2.0,
        observed_f=wx.c_to_f(7.0)))
    assert new < 0.01


def test_the_two_old_errors_pointed_in_opposite_directions():
    """The property that made the old failure worst-case, asserted rather than
    described: one row was far too LOW and the other far too HIGH, so averaging
    them produced something that looked unbiased."""
    strategy = wx.WeatherArb(allow_daily_extreme_markets=True)
    madrid = strategy.probability_yes(
        wx.c_to_f(33.0),
        parse_threshold('Will the highest temperature in Madrid be 39°C '
                        'on August 18?'), 1.0)
    buenos = strategy.probability_yes(
        wx.c_to_f(7.0),
        parse_threshold('Will the highest temperature in Buenos Aires be '
                        '8°C or below on August 18?'), 1.0)
    assert madrid < 0.70 and buenos > 0.001     # both on the wrong side
    assert (0.70 - madrid) > 0.5 and (buenos - 0.001) > 0.5


# ===========================================================================
# 3. SIGMA
# ===========================================================================

def test_sigma_grows_with_the_hours_left_in_the_day():
    values = [daily_extreme_sigma_f(h) for h in (0.0, 1.0, 6.0, 24.0, 36.0)]
    assert values == sorted(values)
    assert values[0] == pytest.approx(wx.DAILY_EXTREME_SIGMA_FLOOR_F)


def test_sigma_is_strictly_positive_even_at_zero_hours():
    """A zero sigma prices every rung 0.00 or 1.00 with a full-size position
    behind it."""
    assert daily_extreme_sigma_f(0.0) > 0.0
    assert daily_extreme_sigma_f(-5.0) > 0.0


def test_the_daily_extreme_sigma_is_smaller_than_the_point_in_time_one():
    """Not an inconsistency: the point-in-time model carries the whole
    deterministic diurnal swing in its noise term because it has no diurnal
    term, and the forecast path already contains the cycle in its mean."""
    assert (wx.DAILY_EXTREME_SIGMA_PER_SQRT_HOUR_F
            < wx.SIGMA_PER_SQRT_HOUR_F)


def test_the_sigma_constants_are_labelled_as_unfitted_estimates():
    """Convention 15: a number written before the run is an estimate and must
    say so. Convention 22: this checks the module text because the claim IS the
    documentation, and a row-level flag is asserted separately below."""
    source = open(wx.__file__).read()
    head = source[source.index('DAILY_EXTREME_SIGMA_FLOOR_F') - 1600:
                  source.index('DAILY_EXTREME_SIGMA_FLOOR_F')]
    assert 'CONVENTION 15' in head.upper() or 'convention 15' in head
    assert 'never fitted' in head


def test_every_estimate_row_names_the_harness_and_says_it_is_unscored():
    """Convention 6 wants a number and a named harness. The harness EXISTS as
    of 2026-08-18 (`backtest/measure_daily_extreme_calibration.py`), so the row
    stops claiming it does not - but "the predictor's error was measured" and
    "this model was scored against resolved markets" are two different facts and
    only the first one is true. No weather position has ever resolved, so the
    second flag stays False and the two never share a field.

    The default `_estimate()` carries the HOUSE constants, so it also has to
    keep saying its sigma was never fitted (convention 15)."""
    row = _estimate().to_dict()
    assert row['daily_extreme_calibration_harness_exists'] is True
    assert row['daily_extreme_calibration_harness'] == (
        'backtest/measure_daily_extreme_calibration.py')
    assert row['daily_extreme_model_scored_on_resolved_markets'] is False
    assert row['sigma_constants_are_estimates_never_fitted'] is True
    assert row['daily_extreme_sigma_source'] == 'house_constants_unfitted'
    assert row['model_prices_daily_extreme'] is True


# ===========================================================================
# 4. RESOLUTION DATE
# ===========================================================================

@pytest.mark.parametrize('question,expected', [
    ('Will the highest temperature in NYC be 85F or below on August 18?',
     (8, 18)),
    ('Will the highest temperature in Tokyo be 25C or below on Aug 3?', (8, 3)),
    ('Will the highest temperature in NYC be 85F on December 31?', (12, 31)),
    ('Will the highest temperature in NYC be 85F on Sept. 9?', (9, 9)),
])
def test_the_month_and_day_are_read_off_the_question(question, expected):
    assert resolution_month_day(question) == expected


def test_a_question_with_no_date_is_none_and_never_today():
    assert resolution_month_day(
        'Will the highest temperature in NYC be 85F today?') is None
    assert resolution_month_day('') is None
    assert resolution_month_day(None) is None


def test_the_year_comes_from_the_forecast_and_never_from_the_wall_clock():
    """A question says "August 18" and never says which year. Guessing one is
    guessing across a year boundary at exactly the moment the guess matters, so
    the year is resolved against the forecast's own list of real local dates."""
    feed = StationForecastFeed(session=_Session(
        _Resp(_forecast_payload(dates=('2029-12-31', '2030-01-01',
                                       '2030-01-02')))), clock=lambda: NOW)
    forecast, status = feed.forecast_checked(40.78, -73.88)
    assert status == 'ok'
    date, date_status = resolution_local_date_checked(
        'Will the highest temperature in NYC be 40F or below on January 1?',
        forecast)
    assert (date, date_status) == ('2030-01-01', 'ok')


def test_a_date_the_forecast_does_not_cover_is_refused_not_extrapolated():
    feed = StationForecastFeed(session=_Session(_Resp(_forecast_payload())),
                               clock=lambda: NOW)
    forecast, _ = feed.forecast_checked(40.78, -73.88)
    date, status = resolution_local_date_checked(
        'Will the highest temperature in NYC be 85F or below on March 4?',
        forecast)
    assert date is None
    assert status == 'resolution_date_outside_forecast_window'


# ===========================================================================
# 5. THE STATION FORECAST FEED, PARSED FROM THE MEASURED PAYLOAD
# ===========================================================================

def test_the_measured_open_meteo_payload_parses():
    session = _Session(_Resp(_forecast_payload()))
    feed = StationForecastFeed(session=session, clock=lambda: NOW)
    forecast, status = feed.forecast_checked(40.7794, -73.8803)
    assert status == 'ok'
    assert forecast.timezone_name == 'America/New_York'
    assert forecast.utc_offset_sec == -14400
    assert forecast.daily_dates == ('2026-08-17', '2026-08-18', '2026-08-19')
    assert forecast.extreme_for('2026-08-18', 'daily_high') == 82.8
    assert forecast.extreme_for('2026-08-18', 'daily_low') == 71.2
    assert forecast.extreme_for('2026-08-20', 'daily_high') is None


def test_the_request_asks_for_fahrenheit_local_time_and_both_blocks():
    """Convention 22: a docstring claiming `timezone=auto` is not a wiring test.
    This reads the parameters that actually went on the wire.

    `timezone=auto` is the load-bearing one. Without it open-meteo answers in
    GMT and `daily.time` stops meaning the station's own calendar day, which is
    the only calendar the market's question refers to.
    """
    session = _Session(_Resp(_forecast_payload()))
    StationForecastFeed(session=session, clock=lambda: NOW).forecast_checked(
        40.7794, -73.8803)
    params = session.requests[0]['params']
    assert params['temperature_unit'] == 'fahrenheit'
    assert params['timezone'] == 'auto'
    assert params['daily'] == 'temperature_2m_max,temperature_2m_min'
    assert params['hourly'] == 'temperature_2m'
    assert session.requests[0]['url'] == wx.OPEN_METEO_URL


def test_a_celsius_response_is_converted_and_the_substitution_is_recorded():
    """open-meteo's documented default is Celsius under the IDENTICAL field
    name, so a dropped parameter is a silent 50-degree error. 27.1C is a
    plausible temperature and so is 27.1F; nothing downstream could catch it."""
    session = _Session(_Resp(_forecast_payload(
        unit='°C', daily_max=(26.8, 28.2, 31.7),
        daily_min=(20.3, 21.8, 22.0))))
    feed = StationForecastFeed(session=session, clock=lambda: NOW)
    forecast, status = feed.forecast_checked(40.78, -73.88)
    assert status == 'ok'
    assert forecast.converted is True
    assert forecast.unit_received == 'C'
    assert forecast.extreme_for('2026-08-18', 'daily_high') == \
        pytest.approx(wx.c_to_f(28.2))


def test_an_unrecognised_unit_is_refused_and_never_assumed():
    session = _Session(_Resp(_forecast_payload(unit='kelvin')))
    feed = StationForecastFeed(session=session, clock=lambda: NOW)
    forecast, status = feed.forecast_checked(40.78, -73.88)
    assert forecast is None
    assert status == 'forecast_unexpected_unit'


def test_the_naive_hourly_strings_are_read_as_LOCAL_not_as_utc():
    """The single subtlest thing in the payload. `'2026-08-18T10:00'` carries no
    offset and is LOCAL, and `utc_offset_seconds` is -14400. Reading it as UTC
    would place every hourly point four hours early, which silently measures the
    station bias against the wrong part of the diurnal cycle."""
    session = _Session(_Resp(_forecast_payload()))
    feed = StationForecastFeed(session=session, clock=lambda: NOW)
    forecast, _ = feed.forecast_checked(40.78, -73.88)
    # 10:00 local at -04:00 is 14:00Z.
    expected = int(datetime(2026, 8, 18, 14, 0,
                            tzinfo=timezone.utc).timestamp())
    assert forecast.hourly_ts[0] == expected
    assert forecast.hourly_at(expected) == 73.2


def test_an_hour_further_than_the_tolerance_is_not_reached_for():
    """Nearest, but BOUNDED. Reaching four hours away for the nearest available
    point would measure the bias against a different part of the day."""
    session = _Session(_Resp(_forecast_payload()))
    feed = StationForecastFeed(session=session, clock=lambda: NOW)
    forecast, _ = feed.forecast_checked(40.78, -73.88)
    near = forecast.hourly_ts[0]
    assert forecast.hourly_at(near + 900) == 73.2
    assert forecast.hourly_at(near + 4 * 3600) is None


def test_a_forecast_read_is_cached_and_the_second_call_sends_no_request():
    session = _Session(_Resp(_forecast_payload()))
    feed = StationForecastFeed(session=session, clock=lambda: NOW)
    feed.forecast_checked(40.7794, -73.8803)
    feed.forecast_checked(40.7794, -73.8803)
    assert len(session.requests) == 1
    assert feed.stats['forecast_cache_hits'] == 1


def test_a_failed_forecast_read_is_never_cached():
    """A cached failure turns one bad minute into fifteen minutes of guaranteed
    refusals, and the retry that would have fixed it never happens."""
    session = _Session(_Resp(None, status_code=500),
                       _Resp(None, status_code=500),
                       _Resp(_forecast_payload()))
    feed = StationForecastFeed(session=session, retries=2, sleep_fn=lambda s: None,
                               clock=lambda: NOW)
    first, status = feed.forecast_checked(40.78, -73.88)
    assert first is None and status == 'feed_http_transient'
    second, status = feed.forecast_checked(40.78, -73.88)
    assert second is not None and status == 'ok'


@pytest.mark.parametrize('payload,expected', [
    ({'daily': {'time': ['2026-08-18']}}, 'forecast_no_hourly_block'),
    ({'hourly': {'time': []}}, 'forecast_no_daily_block'),
    ([{'not': 'a dict'}], 'forecast_unexpected_shape'),
])
def test_every_malformed_forecast_shape_has_its_own_reason(payload, expected):
    feed = StationForecastFeed(session=_Session(_Resp(payload)),
                               clock=lambda: NOW)
    forecast, status = feed.forecast_checked(40.78, -73.88)
    assert forecast is None
    assert status == expected


def test_a_forecast_with_no_utc_offset_is_refused():
    """With `timezone=auto` the offset is the ONLY thing that turns a naive
    local string into an instant."""
    payload = _forecast_payload()
    del payload['utc_offset_seconds']
    feed = StationForecastFeed(session=_Session(_Resp(payload)),
                               clock=lambda: NOW)
    forecast, status = feed.forecast_checked(40.78, -73.88)
    assert forecast is None
    assert status == 'forecast_no_utc_offset'


@pytest.mark.parametrize('lat,lon', [(None, -73.0), (91.0, -73.0),
                                     (40.0, 200.0), (float('nan'), 1.0)])
def test_a_bad_coordinate_is_caught_here_and_never_sent(lat, lon):
    """open-meteo answers an out-of-range coordinate with a 200 and an error
    body, which would arrive as the much less specific no-daily-block."""
    session = _Session()
    feed = StationForecastFeed(session=session, clock=lambda: NOW)
    forecast, status = feed.forecast_checked(lat, lon)
    assert forecast is None
    assert status == 'forecast_bad_coordinates'
    assert session.requests == []


def test_the_local_day_bounds_are_half_open_and_use_the_response_offset():
    session = _Session(_Resp(_forecast_payload()))
    feed = StationForecastFeed(session=session, clock=lambda: NOW)
    forecast, _ = feed.forecast_checked(40.78, -73.88)
    start, end = forecast.local_day_bounds('2026-08-18')
    assert end - start == 86400
    # Local midnight on the 18th at -04:00 is 04:00Z.
    assert start == int(datetime(2026, 8, 18, 4, 0,
                                 tzinfo=timezone.utc).timestamp())
    # The two ends are two different local midnights. An observation at exactly
    # the closing one belongs to the NEXT day's market, so `end` must equal the
    # next day's `start` and never be counted in both.
    next_start, _ = forecast.local_day_bounds('2026-08-19')
    assert next_start == end


# ===========================================================================
# 6. METAR: COORDINATES AND HISTORY
# ===========================================================================

def test_the_station_coordinates_come_off_the_metar_row():
    """The whole reason a second coordinate table for 51 cities is not needed."""
    session = _Session(_Resp([_metar_row()]))
    feed = AirportWeatherFeed(session=session, clock=lambda: NOW)
    reading, status = feed.observation('KLGA')
    assert status == 'ok'
    assert (reading.lat, reading.lon) == (40.7794, -73.8803)
    assert reading.temp_f == pytest.approx(wx.c_to_f(25.0))


@pytest.mark.parametrize('lat,lon', [(None, -73.8), ('x', -73.8),
                                     (400.0, -73.8), (40.0, 999.0)])
def test_an_unusable_metar_coordinate_becomes_none_not_a_guess(lat, lon):
    row = _metar_row()
    row['lat'], row['lon'] = lat, lon
    feed = AirportWeatherFeed(session=_Session(_Resp([row])),
                              clock=lambda: NOW)
    reading, status = feed.observation('KLGA')
    assert status == 'ok'                # the TEMPERATURE is still readable
    assert reading.lat is None or reading.lon is None


def test_the_history_request_asks_for_hours_and_its_own_timeout():
    """Convention 22 again. `hours` is a MEASURED parameter: live on 2026-08-18
    `ids=KLGA&format=json` returned 1 row and adding `hours=18` returned 21."""
    session = _Session(_Resp([_metar_row()]))
    feed = AirportWeatherFeed(session=session, clock=lambda: NOW,
                              history_hours=26, history_timeout=12.0)
    feed.history('KLGA')
    request = session.requests[0]
    assert request['params']['hours'] == 26
    assert request['params']['ids'] == 'KLGA'
    assert request['timeout'] == 12.0


def test_the_single_observation_call_does_not_ask_for_hours():
    """The two calls are different responses at different costs. A single-row
    read that quietly pulled 26 hours would spend the decision path's budget."""
    session = _Session(_Resp([_metar_row()]))
    feed = AirportWeatherFeed(session=session, clock=lambda: NOW)
    feed.observation('KLGA')
    assert 'hours' not in session.requests[0]['params']
    assert session.requests[0]['timeout'] == wx.FEED_TIMEOUT_SEC


def test_the_running_extreme_is_the_max_inside_the_window():
    rows = [_metar_row(temp_c=25.0, obs_ts=DAY_START + 3600),
            _metar_row(temp_c=31.5, obs_ts=DAY_START + 7 * 3600),
            _metar_row(temp_c=28.0, obs_ts=DAY_START + 9 * 3600)]
    feed = AirportWeatherFeed(session=_Session(_Resp(rows)), clock=lambda: NOW)
    observed, status = feed.daily_extreme_checked('KLGA', 'daily_high',
                                                 DAY_START, DAY_END)
    assert status == 'ok'
    assert observed.extreme_f == pytest.approx(wx.c_to_f(31.5))
    assert observed.observations == 3
    assert (observed.window_start_ts, observed.window_end_ts) == (DAY_START,
                                                                 DAY_END)


def test_the_running_minimum_is_taken_for_a_daily_low():
    rows = [_metar_row(temp_c=25.0, obs_ts=DAY_START + 3600),
            _metar_row(temp_c=18.5, obs_ts=DAY_START + 4 * 3600)]
    feed = AirportWeatherFeed(session=_Session(_Resp(rows)), clock=lambda: NOW)
    observed, status = feed.daily_extreme_checked('KLGA', 'daily_low',
                                                 DAY_START, DAY_END)
    assert status == 'ok'
    assert observed.extreme_f == pytest.approx(wx.c_to_f(18.5))


def test_observations_outside_the_window_are_excluded():
    """Yesterday's 40C is not today's floor."""
    rows = [_metar_row(temp_c=40.0, obs_ts=DAY_START - 3600),
            _metar_row(temp_c=22.0, obs_ts=DAY_START + 3600)]
    feed = AirportWeatherFeed(session=_Session(_Resp(rows)), clock=lambda: NOW)
    observed, _ = feed.daily_extreme_checked('KLGA', 'daily_high',
                                            DAY_START, DAY_END)
    assert observed.extreme_f == pytest.approx(wx.c_to_f(22.0))
    assert observed.observations == 1


def test_the_window_is_half_open_at_the_closing_midnight():
    """An observation at exactly the closing midnight belongs to the NEXT day's
    market. Counting it in both would make one reading a floor under two
    different contracts."""
    rows = [_metar_row(temp_c=40.0, obs_ts=DAY_END),
            _metar_row(temp_c=22.0, obs_ts=DAY_START)]
    feed = AirportWeatherFeed(session=_Session(_Resp(rows)), clock=lambda: NOW)
    observed, _ = feed.daily_extreme_checked('KLGA', 'daily_high',
                                            DAY_START, DAY_END)
    assert observed.observations == 1
    assert observed.extreme_f == pytest.approx(wx.c_to_f(22.0))


def test_a_window_with_no_observations_is_its_own_reason():
    """The normal state in the first minutes of a local day. NOT pooled with a
    failed read, because one is the clock and the other is an outage."""
    rows = [_metar_row(temp_c=22.0, obs_ts=DAY_START - 7200)]
    feed = AirportWeatherFeed(session=_Session(_Resp(rows)), clock=lambda: NOW)
    observed, status = feed.daily_extreme_checked('KLGA', 'daily_high',
                                                 DAY_START, DAY_END)
    assert observed is None
    assert status == 'airport_history_no_observation_in_window'


def test_a_history_read_failure_is_a_different_reason_from_an_empty_window():
    feed = AirportWeatherFeed(session=_Session(_Resp(None, status_code=404)),
                              clock=lambda: NOW)
    observed, status = feed.daily_extreme_checked('KLGA', 'daily_high',
                                                 DAY_START, DAY_END)
    assert observed is None
    assert status == 'feed_http_error'
    assert status != 'airport_history_no_observation_in_window'


def test_an_unknown_metric_is_refused_rather_than_defaulting_to_max():
    feed = AirportWeatherFeed(session=_Session(), clock=lambda: NOW)
    observed, status = feed.daily_extreme_checked('KLGA', 'daily_median',
                                                 DAY_START, DAY_END)
    assert observed is None
    assert status == 'airport_history_metric_unknown'


def test_history_rows_that_will_not_parse_are_counted_not_silently_dropped():
    """Convention 20: a silent `continue` is a missing number."""
    rows = [_metar_row(temp_c=25.0, obs_ts=DAY_START + 3600),
            {'icaoId': 'KLGA', 'temp': None, 'obsTime': DAY_START + 4000},
            {'icaoId': 'KLGA', 'temp': 30.0, 'obsTime': None,
             'reportTime': None}]
    feed = AirportWeatherFeed(session=_Session(_Resp(rows)), clock=lambda: NOW)
    readings, status = feed.history('KLGA')
    assert status == 'ok'
    assert len(readings) == 1
    assert feed.stats['history_row_airport_no_temperature_field'] == 1
    assert feed.stats['history_row_no_obs_time'] == 1


def test_a_page_whose_rows_all_fail_to_parse_is_not_an_empty_station():
    """An empty tuple would read as "the station reported nothing", which is a
    fact about the station rather than about our parser."""
    rows = [{'icaoId': 'KLGA', 'temp': 'banana', 'obsTime': DAY_START + 10}]
    feed = AirportWeatherFeed(session=_Session(_Resp(rows)), clock=lambda: NOW)
    readings, status = feed.history('KLGA')
    assert readings is None
    assert status == 'airport_no_observation'


def test_the_history_cache_is_separate_from_the_single_observation_cache():
    """Two facts, two caches, two TTLs. One dict under one TTL would mean
    whichever was written last decided the freshness of both."""
    wx.clear_metar_cache()
    session = _Session(_Resp([_metar_row()]),
                       _Resp([_metar_row(obs_ts=DAY_START + 3600)]))
    feed = AirportWeatherFeed(session=session, clock=lambda: NOW)
    feed.observation('KLGA')
    assert wx.metar_cache_size() == 1
    assert wx.metar_history_cache_size() == 0
    feed.history('KLGA')
    assert wx.metar_cache_size() == 1
    assert wx.metar_history_cache_size() == 1
    # A second history call is served from the cache; two requests total.
    feed.history('KLGA')
    assert len(session.requests) == 2


def test_clearing_the_cache_clears_both_halves():
    """A conftest fixture that cleared only half would leave the other half
    contaminating its neighbours in exactly the silent way it exists to stop."""
    feed = AirportWeatherFeed(
        session=_Session(_Resp([_metar_row()]), _Resp([_metar_row()])),
        clock=lambda: NOW)
    feed.observation('KLGA')
    feed.history('KLGA')
    assert wx.metar_cache_size() and wx.metar_history_cache_size()
    wx.clear_metar_cache()
    assert wx.metar_cache_size() == 0
    assert wx.metar_history_cache_size() == 0


# ===========================================================================
# 7. THE CONFIG KEY
# ===========================================================================

def test_the_config_key_is_what_turns_the_daily_extreme_model_on():
    """Convention 17: not hardcoded. The module default stays False so every
    existing caller keeps its behaviour; the live loop flips it from
    `config.yaml` through this one setter."""
    before = wx.weather_config()
    try:
        assert wx.WeatherArb().allow_daily_extreme_markets is False
        wx.set_weather_config({'allow_daily_extreme_markets': True})
        assert wx.WeatherArb().allow_daily_extreme_markets is True
        # An explicit argument always beats the config.
        assert wx.WeatherArb(
            allow_daily_extreme_markets=False
        ).allow_daily_extreme_markets is False
    finally:
        wx.set_weather_config(before)


def test_config_yaml_actually_turns_it_on():
    """The deliverable, checked against the FILE rather than against a claim.

    Aym approved `allow_daily_extreme_markets = true` on 2026-08-18. A test that
    asserted the module default would pass whatever config.yaml said.
    """
    import yaml
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'config.yaml')
    with open(path) as handle:
        weather = (yaml.safe_load(handle)['polymarket'] or {}).get('weather')
    assert weather is not None, 'config.yaml has no polymarket.weather block'
    assert weather['allow_daily_extreme_markets'] is True
    assert weather['require_observed_extreme'] is True


def test_an_unknown_config_key_raises_rather_than_being_ignored():
    """A typo that silently does nothing is a setting somebody believes is in
    force and is not."""
    with pytest.raises(ValueError) as exc:
        wx.set_weather_config({'allow_daily_extream_markets': True})
    assert 'unknown' in str(exc.value)


@pytest.mark.parametrize('value', ['true', 'false', 1, 0, None])
def test_a_non_boolean_config_value_raises_and_is_never_coerced(value):
    """`'false'` is a TRUTHY string. Coercing it would turn a refusal into
    permission while the row stamp kept saying the flag was off."""
    with pytest.raises(ValueError):
        wx.set_weather_config({'allow_daily_extreme_markets': value})


def test_no_weather_block_at_all_leaves_the_defaults_alone():
    """Every config that predates this block has no `weather:` key, and that is
    not an error."""
    before = wx.weather_config()
    assert wx.set_weather_config(None) == before


# ===========================================================================
# 8. MARKET RANKING FOR THE POLL BUDGET
# ===========================================================================

class _FakeMarket(object):
    def __init__(self, question, volume, slug='m', rules=None):
        self.question = question
        self.volume = volume
        self.slug = slug
        self.raw = {'description': rules} if rules else {}


KNYC_RULES = ('Resolves on the KNYC station. The resolution source for this '
              'market measures temperatures to whole degrees Fahrenheit '
              '(eg, 21F).')


def test_the_annual_ranking_family_never_wins_the_poll_budget():
    """MEASURED 2026-08-18: the six highest-volume markets under the weather tag
    were "Will 2026 be the hottest year on record?" and its siblings, at $393k
    to $820k against $9,330 for the biggest genuine city ladder. A plain volume
    sort spends the whole budget on markets that structurally cannot trade."""
    markets = [
        _FakeMarket('Will 2026 be the hottest year on record?', 820702.86),
        _FakeMarket('Will 2026 be the second-hottest year on record?',
                    393536.06),
        _FakeMarket('Will the highest temperature in NYC be 85F or below on '
                    'August 18?', 9330.59, slug='nyc-temp', rules=KNYC_RULES),
    ]
    result = wx.rank_weather_markets(markets, limit=8)
    assert [m.volume for m in result['selected']] == [9330.59]
    assert result['declined']['poll_not_a_daily_extreme_market'] == 2


def test_the_ranking_accounting_balances():
    """Convention 20: selected plus declined equals considered, by cause."""
    markets = [
        _FakeMarket('Will 2026 be the hottest year on record?', 100.0),
        _FakeMarket('Will the highest temperature in Nowhere be 85F or below '
                    'on August 18?', 100.0),                  # no station
        _FakeMarket('Will the highest temperature in NYC be 85F or below on '
                    'August 18?', 100.0, slug='nyc-temp', rules=KNYC_RULES),
        _FakeMarket('Will the highest temperature in NYC be 85F or below on '
                    'August 18?', None, slug='nyc-temp', rules=KNYC_RULES),
    ]
    result = wx.rank_weather_markets(markets, limit=8)
    assert result['considered'] == 4
    assert len(result['selected']) + sum(result['declined'].values()) == 4
    assert result['declined']['poll_station_unreadable'] == 1
    assert result['declined']['poll_below_volume_floor'] == 1


def test_the_selection_is_ordered_by_volume_descending():
    markets = [
        _FakeMarket('Will the highest temperature in NYC be {}F or below on '
                    'August 18?'.format(v), float(v * 10), slug='nyc-temp',
                    rules=KNYC_RULES)
        for v in (81, 85, 83)
    ]
    result = wx.rank_weather_markets(markets, limit=3)
    assert result['volume_ordered'] == [850.0, 830.0, 810.0]


def test_markets_past_the_budget_are_counted_not_silently_dropped():
    markets = [
        _FakeMarket('Will the highest temperature in NYC be {}F or below on '
                    'August 18?'.format(v), float(v), slug='nyc-temp',
                    rules=KNYC_RULES)
        for v in range(80, 90)
    ]
    result = wx.rank_weather_markets(markets, limit=3)
    assert len(result['selected']) == 3
    assert result['declined']['poll_outside_poll_budget'] == 7


def test_every_decline_reason_is_a_declared_one():
    """A typo becomes a new bucket that silently splits one count into two."""
    markets = [
        _FakeMarket('Will 2026 be the hottest year on record?', 1.0),
        _FakeMarket('Will the highest temperature in Nowhere be 85F on '
                    'August 18?', 1.0),
    ]
    result = wx.rank_weather_markets(markets, limit=1)
    assert set(result['declined']) <= set(wx.POLL_DECLINE_REASONS)
    assert len(wx.POLL_DECLINE_REASONS) == len(set(wx.POLL_DECLINE_REASONS))


def test_a_poll_decline_reason_never_collides_with_a_skip_reason():
    """They live in different logs and mean different things: a declined market
    never reaches `evaluate` and never produces a decision row."""
    overlap = set(wx.POLL_DECLINE_REASONS) & set(wx.SKIP_REASONS)
    assert overlap == set(), overlap


# ===========================================================================
# 9. HOUSE RULES
# ===========================================================================

def test_no_em_dash_and_no_double_hyphen_inside_a_word():
    """Aym's house rule, applied to the new code as well as the old."""
    source = open(wx.__file__).read()
    # `chr(0x2014)` rather than the character itself, so this file does not
    # break the rule it is enforcing. The escaped literal in `_RANGE_RE` is
    # excluded: that regex has to MATCH an em dash in a market question.
    assert chr(0x2014) not in source.replace("'\\u2014'", '')
    import re as _re
    assert _re.search(r'\w--\w', source) is None


def test_the_new_skip_reasons_have_no_duplicates_and_all_are_used():
    """Convention 20, checked against the CODE rather than against a comment:
    every daily-extreme reason must actually be returned somewhere."""
    source = open(wx.__file__).read()
    new = ('station_coordinates_unknown', 'station_forecast_unavailable',
           'resolution_date_unparseable',
           'resolution_date_outside_forecast_window',
           'forecast_extreme_missing_for_date',
           'forecast_hour_missing_for_bias', 'observation_window_closed',
           'observation_window_too_far_out',
           'daily_extreme_history_unavailable', 'not_a_temperature_market')
    for reason in new:
        assert reason in wx.SKIP_REASONS, reason
        # Named in SKIP_REASONS AND returned from a code path, not just listed.
        assert source.count("'" + reason + "'") >= 2, reason


def test_every_json_feature_a_decision_row_carries_is_serialisable():
    """Convention 19: `json.loads` is not strict, so write with allow_nan=False
    and prove the new fields survive it."""
    row = _estimate(observed_f=88.0).to_dict()
    assert json.loads(json.dumps(row, allow_nan=False)) == row


# ===========================================================================
# 10. THE MODEL'S OWN RESOLUTION LIMIT
# ===========================================================================

def test_a_bounded_rung_has_a_ceiling_that_depends_only_on_width_and_sigma():
    """`2 * Phi(w / (2 * sigma)) - 1`. Not on the temperature, not on the
    forecast, not on the observation."""
    rung = parse_threshold('Will the highest temperature in Madrid be 36°C '
                           'on August 19?')
    assert rung.hi_f - rung.lo_f == pytest.approx(1.8)
    ceiling = wx.max_attainable_p_yes(rung, sigma_f=2.96)
    assert ceiling == pytest.approx(0.239, abs=0.002)
    # Whatever the mean, the model cannot beat it.
    for mu in range(60, 130):
        assert probability_yes_daily_extreme(
            rung, _estimate(mu_f=float(mu), sigma_f=2.96)) <= ceiling + 1e-12


def test_a_tail_rung_has_no_ceiling():
    """Unbounded on one side, so a far enough mean makes it certain. The
    concept does not bite and the gate must not fire on it."""
    tail = parse_threshold('Will the highest temperature in NYC be 85°F or '
                           'below on August 18?')
    assert wx.max_attainable_p_yes(tail, sigma_f=2.96) is None


def test_the_madrid_row_that_exposed_the_fabricated_edge():
    """MEASURED live 2026-08-18. The model returned 0.238 for the 36C rung
    against a book at 0.64 and booked a 0.43 "edge". 0.238 IS the ceiling to
    three decimals: the model was already maxed out and the "disagreement" was
    the bucket width against our sigma, not information about Madrid."""
    rung = parse_threshold('Will the highest temperature in Madrid be 36°C '
                           'on August 19?')
    sigma = daily_extreme_sigma_f(31.483)
    assert sigma == pytest.approx(2.963, abs=0.002)
    priced = probability_yes_daily_extreme(
        rung, _estimate(mu_f=93.8 + 2.7, sigma_f=sigma, hours=31.483))
    ceiling = wx.max_attainable_p_yes(rung, sigma)
    assert priced == pytest.approx(0.238, abs=0.002)
    assert priced == pytest.approx(ceiling, abs=0.002)
    assert ceiling < wx.MIN_ATTAINABLE_P_YES


def test_below_the_floor_the_model_side_is_fixed_before_any_temperature():
    """The property the gate exists for: the model cannot prefer Yes on that
    rung whatever the weather does, so it would take the No side of nine of the
    eleven rungs of every ladder, every cycle, forever."""
    rung = parse_threshold('Will the highest temperature in Madrid be 36°C '
                           'on August 19?')
    sigma = daily_extreme_sigma_f(31.483)
    sides = {('Yes' if probability_yes_daily_extreme(
        rung, _estimate(mu_f=float(mu), sigma_f=sigma)) > 0.5 else 'No')
        for mu in range(50, 140)}
    assert sides == {'No'}


def test_a_whole_degree_fahrenheit_bucket_is_never_priceable_by_this_sigma():
    """Its ceiling is 0.31 even half an hour before the day closes. Stated so
    nobody later reads an empty Fahrenheit-bucket population as "no signal"."""
    rung = parse_threshold('Will the highest temperature in NYC be 84°F on '
                           'August 18?')
    for hours in (36.0, 9.0, 2.0, 0.5, 0.0):
        ceiling = wx.max_attainable_p_yes(rung, daily_extreme_sigma_f(hours))
        assert ceiling < wx.MIN_ATTAINABLE_P_YES, hours


def test_a_wide_fahrenheit_range_bucket_becomes_priceable_near_the_close():
    """The gate is a resolution limit, not a blanket ban on buckets. The widest
    live rung shape - a Fahrenheit RANGE bucket, [75.5, 77.5) and so 2.0F wide
    against a 1.0F whole-degree one - clears the floor once the horizon is short
    enough. That is exactly the behaviour a resolution limit should have, and it
    is the only interior shape this sigma can ever price.

    Note how late it is: 0.5 hours to the local day's close. Anything earlier in
    the day is refused. The way to widen this reach is to FIT the sigma, not to
    lower `MIN_ATTAINABLE_P_YES`."""
    rung = parse_threshold('Will the highest temperature in NYC be between '
                           '76-77°F on August 18?')
    assert rung.hi_f - rung.lo_f == pytest.approx(2.0)
    assert wx.max_attainable_p_yes(rung, daily_extreme_sigma_f(31.5)) < 0.5
    assert wx.max_attainable_p_yes(rung, daily_extreme_sigma_f(2.0)) < 0.5
    assert wx.max_attainable_p_yes(rung, daily_extreme_sigma_f(0.5)) > 0.5


def test_the_floor_is_half_and_is_not_a_tuning_knob():
    """0.5 is where "the model cannot prefer Yes" flips, which is a property of
    the arithmetic rather than a threshold somebody picked."""
    assert wx.MIN_ATTAINABLE_P_YES == 0.5


def test_the_refusal_row_carries_the_two_numbers_that_justify_it():
    """A skip whose cause cannot be checked from its own row is a skip nobody
    can audit later."""
    import tests.test_weather_arb as base
    decision = base._ladder_strategy().evaluate(base._ctx(
        market=base._market(question=base.LADDER_BUCKET_C,
                            rules=base.WHOLE_DEGREE_C_RULES)))
    assert decision.reason == 'rung_narrower_than_model_resolution'
    feats = decision.features
    assert feats['max_attainable_p_yes'] < feats['min_attainable_p_yes']
    assert feats['rung_is_bounded_on_both_sides'] is True
    # And it refused BEFORE pricing, so no probability was ever published.
    assert 'model_p_yes' not in feats
