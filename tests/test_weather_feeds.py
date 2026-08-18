"""Tests for `engine/feeds/noaa_weather.py` and `engine/feeds/open_meteo.py`.

FULLY OFFLINE. Every test injects a fake session, a fake clock and a fake
sleep, so the suite makes no network call and takes no real time. A test that
reaches the internet is a test that fails when the internet does, and this repo
has already been bitten by that shape (Binance returns HTTP 451 from this
machine while the TLS handshake still succeeds).

The payload fixtures below are the REAL responses measured on 2026-08-18,
trimmed but not edited. Fixtures invented from a docstring test the docstring;
these test the API.

What is deliberately NOT tested here, so nobody reads a green suite as more
than it is:
  - That either endpoint is up. That is measured by hand and recorded in the
    module docstrings, and it cannot be asserted offline.
  - That the airport-versus-downtown gap is 3 to 8F. Nothing in this repo has
    measured that.
"""
import threading

import pytest

from engine.feeds import noaa_weather, open_meteo
from engine.feeds.noaa_weather import (DEFAULT_AIRPORTS, MetarObservation,
                                       NoaaWeatherFeed, c_to_f)
from engine.feeds.open_meteo import (DEFAULT_CITIES, CurrentTemperature,
                                     OpenMeteoFeed, normalise_unit)

# ---------------------------------------------------------------------------
# Real measured payloads, 2026-08-18T11:00Z
# ---------------------------------------------------------------------------

#: Verbatim from `GET aviationweather.gov/api/data/metar?ids=KLGA&format=json`.
#: Note it is a BARE LIST, not an envelope.
KLGA_ROW = {
    'icaoId': 'KLGA', 'receiptTime': '2026-08-18T10:54:07.802Z',
    'obsTime': 1787050260, 'reportTime': '2026-08-18T11:00:00.000Z',
    'temp': 22.8, 'dewp': 21.7, 'wdir': 20, 'wspd': 3, 'visib': '10+',
    'altim': 1011.3, 'slp': 1011, 'metarType': 'METAR',
    'rawOb': 'METAR KLGA 181051Z 02003KT 10SM OVC019 23/22 A2986 RMK AO2',
    'lat': 40.7794, 'lon': -73.8803, 'elev': 9,
    'name': 'New York/La Guardia Arpt, NY, US',
}
KLAX_ROW = dict(KLGA_ROW, icaoId='KLAX', temp=20.0, obsTime=1787050380,
                rawOb='METAR KLAX 181053Z 00000KT 10SM FEW009',
                name='Los Angeles Intl, CA, US')
KDEN_ROW = dict(KLGA_ROW, icaoId='KDEN', temp=18.3, obsTime=1787050380,
                rawOb='METAR KDEN 181053Z 29009KT 10SM FEW220 18/09',
                name='Denver Intl, CO, US')

#: A clock sitting a few minutes after those observations, so the default
#: `max_obs_age_sec` gate passes without any test having to disable it.
NOW = 1787050800.0

#: Verbatim from the open-meteo call in the module docstring.
OPEN_METEO_PAYLOAD = {
    'latitude': 40.710335, 'longitude': -73.99308,
    'generationtime_ms': 0.0147, 'utc_offset_seconds': 0,
    'timezone': 'GMT', 'timezone_abbreviation': 'GMT', 'elevation': 27.0,
    'current_units': {'time': 'iso8601', 'interval': 'seconds',
                      'temperature_2m': '°F'},
    'current': {'time': '2026-08-18T11:00', 'interval': 900,
                'temperature_2m': 71.3},
}


# ---------------------------------------------------------------------------
# Offline doubles
# ---------------------------------------------------------------------------

class FakeResponse(object):
    def __init__(self, status_code=200, payload=None, raises=None):
        self.status_code = status_code
        self._payload = payload
        self._raises = raises

    def json(self):
        if self._raises is not None:
            raise self._raises
        return self._payload


class FakeSession(object):
    """Returns queued responses in order; the last one repeats forever.

    A queue rather than a single value because the retry paths need the second
    attempt to differ from the first.
    """

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({'url': url, 'params': dict(params or {}),
                           'timeout': timeout})
        item = (self.responses.pop(0) if len(self.responses) > 1
                else self.responses[0])
        if isinstance(item, Exception):
            raise item
        return item


class FakeClock(object):
    """A clock the test moves by hand. No `time.sleep` anywhere in this file."""

    def __init__(self, start=NOW):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def noaa_feed(*responses, **kwargs):
    clock = kwargs.pop('clock', None) or FakeClock()
    session = FakeSession(*responses) if responses else FakeSession(
        FakeResponse(200, [KLGA_ROW]))
    feed = NoaaWeatherFeed(session=session, clock=clock,
                           sleep_fn=lambda _s: None, **kwargs)
    return feed, session, clock


def meteo_feed(*responses, **kwargs):
    clock = kwargs.pop('clock', None) or FakeClock()
    session = FakeSession(*responses) if responses else FakeSession(
        FakeResponse(200, OPEN_METEO_PAYLOAD))
    feed = OpenMeteoFeed(session=session, clock=clock,
                         sleep_fn=lambda _s: None, **kwargs)
    return feed, session, clock


# ---------------------------------------------------------------------------
# NOAA: happy path and units
# ---------------------------------------------------------------------------

class TestNoaaHappyPath(object):

    def test_parses_the_real_measured_payload(self):
        feed, session, _ = noaa_feed()
        obs = feed.observation('KLGA')
        assert isinstance(obs, MetarObservation)
        assert obs.station == 'KLGA'
        assert obs.observed_ts == 1787050260
        assert obs.raw_metar.startswith('METAR KLGA')
        assert obs.station_name == 'New York/La Guardia Arpt, NY, US'
        assert session.calls[0]['params'] == {'ids': 'KLGA', 'format': 'json'}

    def test_format_json_is_always_sent(self):
        # Without it the endpoint serves raw METAR text and `.json()` fails,
        # which would surface as `metar_bad_json` on every single call.
        feed, session, _ = noaa_feed()
        feed.observation('KLGA')
        assert session.calls[0]['params']['format'] == 'json'

    def test_the_metar_temperature_is_celsius_and_f_is_derived(self):
        # 22.8C is 73.04F. If this ever reads 22.8 the payload is being taken
        # for Fahrenheit, which prices 22.8F against a 73F market.
        feed, _, _ = noaa_feed()
        obs = feed.observation('KLGA')
        assert obs.temp_c == pytest.approx(22.8)
        assert obs.temp_f == pytest.approx(73.04)

    def test_the_raw_metar_group_corroborates_the_celsius_reading(self):
        # `rawOb` carries '23/22', temp/dewpoint in whole degrees C. The parsed
        # 22.8C rounds to 23. This is the independent check that the `temp`
        # field is Celsius and not something else.
        feed, _, _ = noaa_feed()
        obs = feed.observation('KLGA')
        assert '23/22' in obs.raw_metar
        assert round(obs.temp_c) == 23

    def test_there_is_no_ambiguously_named_temperature_field(self):
        # The whole point of `temp_c` / `temp_f`. A field called `temp` is a
        # 50-degree error waiting for a tired reader to grab it.
        feed, _, _ = noaa_feed()
        obs = feed.observation('KLGA')
        for banned in ('temp', 'temperature', 'value'):
            assert not hasattr(obs, banned)
        assert set(obs.to_dict()) & {'temp', 'temperature'} == set()

    def test_c_to_f_anchors(self):
        assert c_to_f(0.0) == pytest.approx(32.0)
        assert c_to_f(100.0) == pytest.approx(212.0)
        assert c_to_f(-40.0) == pytest.approx(-40.0)

    def test_icao_is_uppercased_on_the_way_out_and_in(self):
        feed, session, _ = noaa_feed()
        assert feed.observation('klga').station == 'KLGA'
        assert session.calls[0]['params']['ids'] == 'KLGA'

    def test_to_dict_states_its_units_in_the_key_names(self):
        feed, _, _ = noaa_feed()
        row = feed.observation('KLGA').to_dict()
        assert row['temp_c'] == pytest.approx(22.8)
        assert row['temp_f'] == pytest.approx(73.04)
        assert row['observed_ts'] == 1787050260

    def test_the_five_default_airports_are_the_ones_asked_for(self):
        assert set(DEFAULT_AIRPORTS.values()) == {
            'KLGA', 'KLAX', 'KORD', 'KDEN', 'KATL'}


# ---------------------------------------------------------------------------
# NOAA: cache
# ---------------------------------------------------------------------------

class TestNoaaCache(object):

    def test_second_call_inside_the_ttl_makes_no_request(self):
        feed, session, clock = noaa_feed()
        first = feed.observation('KLGA')
        clock.advance(299)
        second = feed.observation('KLGA')
        assert len(session.calls) == 1
        assert second is first
        assert feed.health['cache_hit'] == 1

    def test_the_ttl_boundary_is_exclusive(self):
        # `now - fetched_at >= ttl` expires. At exactly 300s the entry is gone.
        feed, session, clock = noaa_feed()
        feed.observation('KLGA')
        clock.advance(300)
        feed.observation('KLGA')
        assert len(session.calls) == 2
        assert feed.health['cache_expired'] == 1

    def test_an_expired_entry_is_evicted_not_just_ignored(self):
        feed, _, clock = noaa_feed()
        feed.observation('KLGA')
        clock.advance(301)
        feed._cached('KLGA')
        assert 'KLGA' not in feed._cache

    def test_a_failed_refetch_returns_none_and_never_the_stale_copy(self):
        # THE most important test in this file. Serving stale-on-error turns an
        # outage into a confident wrong number that no downstream gate can
        # detect.
        feed, _, clock = noaa_feed(FakeResponse(200, [KLGA_ROW]),
                                   FakeResponse(500, None))
        assert feed.observation('KLGA') is not None
        clock.advance(3600)
        obs, reason = feed.observation_checked('KLGA')
        assert obs is None
        assert reason == 'metar_http_transient'

    def test_stations_are_cached_independently(self):
        feed, session, _ = noaa_feed(FakeResponse(200, [KLGA_ROW]),
                                     FakeResponse(200, [KLAX_ROW]))
        assert feed.observation('KLGA').temp_c == pytest.approx(22.8)
        assert feed.observation('KLAX').temp_c == pytest.approx(20.0)
        assert len(session.calls) == 2

    def test_invalidate_drops_one_station_only(self):
        feed, session, _ = noaa_feed()
        feed.observation('KLGA')
        feed.invalidate('KLAX')
        feed.observation('KLGA')
        assert len(session.calls) == 1
        feed.invalidate('KLGA')
        feed.observation('KLGA')
        assert len(session.calls) == 2

    def test_invalidate_with_no_argument_clears_everything(self):
        feed, session, _ = noaa_feed()
        feed.observation('KLGA')
        feed.invalidate()
        feed.observation('KLGA')
        assert len(session.calls) == 2


# ---------------------------------------------------------------------------
# NOAA: every failure mode, its own reason and its own counter
# ---------------------------------------------------------------------------

class TestNoaaFailureModes(object):

    def test_network_exception_returns_none_and_never_raises(self):
        feed, _, _ = noaa_feed(IOError('dns'))
        obs, reason = feed.observation_checked('KLGA')
        assert obs is None
        assert reason == 'metar_network_failure'
        # ONE refusal, but one `exc:` tick PER ATTEMPT. The two counters answer
        # different questions on purpose: `fail_network` counts calls that gave
        # up, `exc:` counts wire attempts that threw. A feed retrying twice on
        # every call is a different health picture from one failing outright,
        # and a single pooled number could not tell them apart.
        assert feed.health['fail_network'] == 1
        assert feed.health['exc:OSError'] == feed.retries == 2

    def test_the_exception_type_is_counted_separately(self):
        # A DNS failure and a read timeout need different responses, so they
        # must stay distinguishable in the health dump.
        feed, _, _ = noaa_feed(ValueError('boom'))
        feed.observation('KLGA')
        assert feed.health['exc:ValueError'] == feed.retries
        assert 'exc:OSError' not in feed.health

    def test_a_4xx_is_not_retried(self):
        feed, session, _ = noaa_feed(FakeResponse(404, None))
        obs, reason = feed.observation_checked('KLGA')
        assert obs is None
        assert reason == 'metar_http_error'
        assert len(session.calls) == 1
        assert feed.health['fail_http_4xx'] == 1
        assert feed.health['http_404'] == 1

    def test_a_5xx_is_retried_then_refused(self):
        feed, session, _ = noaa_feed(FakeResponse(503, None))
        obs, reason = feed.observation_checked('KLGA')
        assert obs is None
        assert reason == 'metar_http_transient'
        assert len(session.calls) == 2
        assert feed.health['retries'] == 1
        assert feed.health['fail_http_transient'] == 1

    def test_a_429_is_retried_and_can_succeed(self):
        feed, session, _ = noaa_feed(FakeResponse(429, None),
                                     FakeResponse(200, [KLGA_ROW]))
        assert feed.observation('KLGA') is not None
        assert len(session.calls) == 2
        assert feed.health['http_429'] == 1

    def test_undecodable_body_is_its_own_reason(self):
        feed, _, _ = noaa_feed(FakeResponse(200, None,
                                            raises=ValueError('not json')))
        obs, reason = feed.observation_checked('KLGA')
        assert obs is None
        assert reason == 'metar_bad_json'
        assert feed.health['fail_bad_json'] == 1

    @pytest.mark.parametrize('payload', ['a string', 42, {'nope': 1}, None])
    def test_an_unrecognised_shape_is_refused_not_coerced(self, payload):
        feed, _, _ = noaa_feed(FakeResponse(200, payload))
        obs, reason = feed.observation_checked('KLGA')
        assert obs is None
        assert reason == 'metar_unexpected_shape'
        assert feed.health['fail_unexpected_shape'] == 1

    def test_the_data_envelope_shape_is_still_accepted(self):
        # Measured today the endpoint returns a bare list. The envelope is
        # accepted so a shape change degrades to a refusal, not a crash.
        feed, _, _ = noaa_feed(FakeResponse(200, {'data': [KLGA_ROW]}))
        assert feed.observation('KLGA').temp_c == pytest.approx(22.8)

    def test_an_empty_list_is_no_observation_not_an_http_failure(self):
        feed, _, _ = noaa_feed(FakeResponse(200, []))
        obs, reason = feed.observation_checked('KLGA')
        assert obs is None
        assert reason == 'metar_no_observation'
        assert feed.health['fail_no_observation'] == 1
        assert feed.health['fail_network'] == 0

    def test_a_missing_temp_field_is_its_own_reason(self):
        row = dict(KLGA_ROW)
        del row['temp']
        feed, _, _ = noaa_feed(FakeResponse(200, [row]))
        obs, reason = feed.observation_checked('KLGA')
        assert obs is None
        assert reason == 'metar_no_temperature_field'
        assert feed.health['fail_no_temperature_field'] == 1

    def test_a_null_temp_is_the_same_reason_not_a_crash(self):
        feed, _, _ = noaa_feed(FakeResponse(200, [dict(KLGA_ROW, temp=None)]))
        assert feed.observation_checked('KLGA')[1] == (
            'metar_no_temperature_field')

    def test_an_unparseable_temp_string_is_the_same_reason(self):
        feed, _, _ = noaa_feed(FakeResponse(200, [dict(KLGA_ROW, temp='warm')]))
        assert feed.observation_checked('KLGA')[1] == (
            'metar_no_temperature_field')

    @pytest.mark.parametrize('bad', [float('nan'), float('inf'),
                                     float('-inf')])
    def test_a_non_finite_temperature_is_refused(self, bad):
        # convention 19: `json.loads` accepts the literals NaN and Infinity, so
        # this arrives from a real payload and not only from a bad cast. A NaN
        # sails through `float()` and poisons every average downstream.
        feed, _, _ = noaa_feed(FakeResponse(200, [dict(KLGA_ROW, temp=bad)]))
        obs, reason = feed.observation_checked('KLGA')
        assert obs is None
        assert reason == 'metar_non_finite_temperature'
        assert feed.health['fail_non_finite_temperature'] == 1

    def test_a_missing_observation_time_is_refused_not_read_as_now(self):
        row = dict(KLGA_ROW)
        del row['obsTime']
        del row['reportTime']
        feed, _, _ = noaa_feed(FakeResponse(200, [row]))
        obs, reason = feed.observation_checked('KLGA')
        assert obs is None
        assert reason == 'metar_obs_time_missing'
        assert feed.health['fail_obs_time_missing'] == 1

    def test_report_time_is_the_fallback_when_obs_time_is_absent(self):
        row = dict(KLGA_ROW)
        del row['obsTime']
        feed, _, _ = noaa_feed(FakeResponse(200, [row]))
        obs = feed.observation('KLGA')
        assert obs.observed_ts == 1787050800   # 2026-08-18T11:00:00Z

    def test_an_observation_older_than_the_max_age_is_refused(self):
        feed, _, _ = noaa_feed(FakeResponse(200, [dict(KLGA_ROW,
                                                       obsTime=NOW - 7200)]))
        obs, reason = feed.observation_checked('KLGA')
        assert obs is None
        assert reason == 'metar_obs_stale'
        assert feed.health['fail_obs_stale'] == 1

    def test_the_obs_age_gate_can_be_disabled_for_a_recorder(self):
        feed, _, _ = noaa_feed(FakeResponse(200, [dict(KLGA_ROW,
                                                       obsTime=NOW - 7200)]),
                               max_obs_age_sec=None)
        obs = feed.observation('KLGA')
        assert obs is not None
        assert obs.obs_age_sec(NOW) == pytest.approx(7200)

    @pytest.mark.parametrize('bad', ['', '   ', None, 42, [], object()])
    def test_an_unusable_station_argument_never_reaches_the_network(self, bad):
        feed, session, _ = noaa_feed()
        obs, reason = feed.observation_checked(bad)
        assert obs is None
        assert reason == 'metar_bad_station_argument'
        assert session.calls == []

    def test_a_non_dict_row_is_refused(self):
        feed, _, _ = noaa_feed(FakeResponse(200, ['KLGA 22.8']))
        obs, reason = feed.observation_checked('KLGA')
        assert obs is None
        assert reason == 'metar_unexpected_shape'


# ---------------------------------------------------------------------------
# NOAA: the batch path
# ---------------------------------------------------------------------------

class TestNoaaBatch(object):

    def test_five_airports_cost_one_request(self):
        rows = [KLGA_ROW, KLAX_ROW, KDEN_ROW]
        feed, session, _ = noaa_feed(FakeResponse(200, rows))
        out = feed.observations(['KLGA', 'KLAX', 'KDEN'])
        assert len(session.calls) == 1
        assert session.calls[0]['params']['ids'] == 'KLGA,KLAX,KDEN'
        assert set(out) == {'KLGA', 'KLAX', 'KDEN'}
        assert out['KLAX'].temp_c == pytest.approx(20.0)

    def test_rows_are_matched_by_icao_not_by_position(self):
        # THE bug this guards: the endpoint does not promise request order.
        # Zipping the response against the request list assigns KDEN's
        # temperature to KLAX, which is a WRONG number rather than a missing
        # one, and nothing downstream could ever detect it.
        feed, _, _ = noaa_feed(FakeResponse(200, [KDEN_ROW, KLGA_ROW,
                                                  KLAX_ROW]))
        out = feed.observations(['KLGA', 'KLAX', 'KDEN'])
        assert out['KLGA'].temp_c == pytest.approx(22.8)
        assert out['KLAX'].temp_c == pytest.approx(20.0)
        assert out['KDEN'].temp_c == pytest.approx(18.3)

    def test_a_station_the_endpoint_omitted_is_none_not_missing(self):
        # A missing KEY would be a silent drop. Convention 20: every skip is
        # counted and every requested station appears in the output.
        feed, _, _ = noaa_feed(FakeResponse(200, [KLGA_ROW]))
        out = feed.observations_checked(['KLGA', 'KLAX'])
        assert set(out) == {'KLGA', 'KLAX'}
        assert out['KLAX'] == (None, 'metar_no_observation')
        assert feed.health['fail_no_observation'] == 1

    def test_a_transport_failure_marks_every_requested_station(self):
        feed, _, _ = noaa_feed(IOError('down'))
        out = feed.observations_checked(['KLGA', 'KLAX'])
        assert set(out) == {'KLGA', 'KLAX'}
        assert all(v[0] is None and v[1] == 'metar_network_failure'
                   for v in out.values())

    def test_cached_stations_are_not_requested_again(self):
        feed, session, _ = noaa_feed(FakeResponse(200, [KLGA_ROW]),
                                     FakeResponse(200, [KLAX_ROW]))
        feed.observation('KLGA')
        feed.observations(['KLGA', 'KLAX'])
        assert session.calls[1]['params']['ids'] == 'KLAX'

    def test_duplicate_requests_are_collapsed(self):
        feed, session, _ = noaa_feed(FakeResponse(200, [KLGA_ROW]))
        out = feed.observations(['KLGA', 'klga', 'KLGA'])
        assert session.calls[0]['params']['ids'] == 'KLGA'
        assert set(out) == {'KLGA'}

    def test_the_batch_path_gets_the_longer_measured_timeout(self):
        # Measured 2026-08-18: a cold five-station batch takes 4.28s while a
        # single station takes 0.17s. Under the 2.0s critical-path timeout the
        # batch failed on all five on the first call of the process and
        # succeeded thereafter, which reads as a flaky network and is actually
        # a deterministic cold start.
        feed, session, _ = noaa_feed(FakeResponse(200, [KLGA_ROW]))
        feed.observation('KLGA')
        feed.invalidate()
        feed.observations(['KLGA'])
        assert session.calls[0]['timeout'] == pytest.approx(2.0)
        assert session.calls[1]['timeout'] == pytest.approx(10.0)
        assert noaa_weather.BATCH_TIMEOUT_SEC > noaa_weather.DEFAULT_TIMEOUT_SEC

    def test_an_unusable_station_in_a_batch_is_counted_and_dropped(self):
        feed, _, _ = noaa_feed(FakeResponse(200, [KLGA_ROW]))
        out = feed.observations_checked(['KLGA', '', None])
        assert set(out) == {'KLGA'}
        assert feed.health['fail_bad_station_argument'] == 2

    def test_an_all_cached_batch_makes_no_request_at_all(self):
        feed, session, _ = noaa_feed(FakeResponse(200, [KLGA_ROW]))
        feed.observation('KLGA')
        feed.observations(['KLGA'])
        assert len(session.calls) == 1


# ---------------------------------------------------------------------------
# open-meteo: happy path and the unit verification
# ---------------------------------------------------------------------------

class TestOpenMeteoHappyPath(object):

    def test_parses_the_real_measured_payload(self):
        feed, _, _ = meteo_feed()
        obs = feed.current(40.71, -74.01)
        assert isinstance(obs, CurrentTemperature)
        assert obs.temp_f == pytest.approx(71.3)
        assert obs.temp_c == pytest.approx(21.8333, abs=1e-3)
        assert obs.observed_ts == 1787050800     # 2026-08-18T11:00Z
        assert obs.converted is False

    def test_fahrenheit_is_asked_for_explicitly(self):
        # The documented DEFAULT IS CELSIUS and it comes back under the
        # identical field name, so dropping this parameter is a silent
        # 50-degree error.
        feed, session, _ = meteo_feed()
        feed.current(40.71, -74.01)
        assert session.calls[0]['params']['temperature_unit'] == 'fahrenheit'
        assert session.calls[0]['params']['current'] == 'temperature_2m'

    def test_the_response_unit_is_verified_not_trusted(self):
        # Asking is not receiving. If the parameter silently stops taking
        # effect the value arrives in Celsius under the same field name, and
        # 22.0 and 71.6 are both plausible temperatures.
        payload = dict(OPEN_METEO_PAYLOAD,
                       current_units={'temperature_2m': '°C'},
                       current={'time': '2026-08-18T11:00',
                                'temperature_2m': 21.8333})
        feed, _, _ = meteo_feed(FakeResponse(200, payload))
        obs = feed.current(40.71, -74.01)
        assert obs.temp_f == pytest.approx(71.3, abs=1e-3)
        assert obs.temp_c == pytest.approx(21.8333)
        assert obs.converted is True
        assert obs.unit_received == 'C'
        assert obs.unit_requested == 'F'
        assert feed.health['unit_conversion_applied'] == 1

    def test_an_unrecognised_unit_is_refused_not_guessed(self):
        payload = dict(OPEN_METEO_PAYLOAD,
                       current_units={'temperature_2m': 'kelvin'})
        feed, _, _ = meteo_feed(FakeResponse(200, payload))
        obs, reason = feed.current_checked(40.71, -74.01)
        assert obs is None
        assert reason == 'open_meteo_unexpected_unit'
        assert feed.health['fail_unexpected_unit'] == 1

    def test_a_missing_units_block_is_refused(self):
        payload = dict(OPEN_METEO_PAYLOAD)
        del payload['current_units']
        feed, _, _ = meteo_feed(FakeResponse(200, payload))
        assert feed.current_checked(40.71, -74.01)[1] == (
            'open_meteo_unexpected_unit')

    @pytest.mark.parametrize('marker,expected', [
        ('°F', 'F'), ('F', 'F'), ('degF', 'F'), ('fahrenheit', 'F'),
        ('°C', 'C'), ('C', 'C'), ('degC', 'C'), ('celsius', 'C'),
        ('kelvin', None), ('', None), (None, None), (42, None),
    ])
    def test_normalise_unit(self, marker, expected):
        assert normalise_unit(marker) == expected

    def test_there_is_no_ambiguously_named_temperature_field(self):
        feed, _, _ = meteo_feed()
        obs = feed.current(40.71, -74.01)
        for banned in ('temp', 'temperature', 'value'):
            assert not hasattr(obs, banned)

    def test_the_served_grid_cell_is_recorded_alongside_the_request(self):
        # We asked for -74.0100 and open-meteo answered for -73.99308. For a
        # gap measurement that displacement is part of the measurement.
        feed, _, _ = meteo_feed()
        obs = feed.current(40.71, -74.01)
        assert obs.req_lon == pytest.approx(-74.01)
        assert obs.grid_lon == pytest.approx(-73.99308)
        assert obs.grid_lon != obs.req_lon
        assert obs.point == '40.7100,-74.0100'

    def test_lookup_by_city_key(self):
        feed, session, _ = meteo_feed()
        obs = feed.current_for_city('nyc')
        assert obs.req_lat == pytest.approx(40.71)
        assert session.calls[0]['params']['latitude'] == pytest.approx(40.71)

    def test_an_unknown_city_is_refused_never_a_guessed_coordinate(self):
        feed, session, _ = meteo_feed()
        obs, reason = feed.current_for_city_checked('atlantis')
        assert obs is None
        assert reason == 'open_meteo_bad_coordinates'
        assert session.calls == []

    def test_the_five_default_cities_are_the_ones_asked_for(self):
        assert set(DEFAULT_CITIES) == {'nyc', 'la', 'chicago', 'denver',
                                       'atlanta'}

    def test_downtown_points_are_not_the_airport_points(self):
        # Making these agree would delete the measurement this pair exists for.
        assert set(DEFAULT_CITIES) == set(DEFAULT_AIRPORTS)
        assert DEFAULT_CITIES['nyc'] != (40.7794, -73.8803)   # KLGA


# ---------------------------------------------------------------------------
# open-meteo: cache
# ---------------------------------------------------------------------------

class TestOpenMeteoCache(object):

    def test_second_call_inside_the_ttl_makes_no_request(self):
        feed, session, clock = meteo_feed()
        first = feed.current(40.71, -74.01)
        clock.advance(299)
        assert feed.current(40.71, -74.01) is first
        assert len(session.calls) == 1
        assert feed.health['cache_hit'] == 1

    def test_the_ttl_boundary_is_exclusive(self):
        feed, session, clock = meteo_feed()
        feed.current(40.71, -74.01)
        clock.advance(300)
        feed.current(40.71, -74.01)
        assert len(session.calls) == 2
        assert feed.health['cache_expired'] == 1

    def test_a_failed_refetch_returns_none_and_never_the_stale_copy(self):
        feed, _, clock = meteo_feed(FakeResponse(200, OPEN_METEO_PAYLOAD),
                                    IOError('down'))
        assert feed.current(40.71, -74.01) is not None
        clock.advance(3600)
        obs, reason = feed.current_checked(40.71, -74.01)
        assert obs is None
        assert reason == 'open_meteo_network_failure'

    def test_coordinates_are_rounded_onto_a_stable_cache_key(self):
        feed, session, _ = meteo_feed()
        feed.current(40.71, -74.01)
        feed.current(40.710001, -74.010001)
        assert len(session.calls) == 1

    def test_different_cities_are_cached_independently(self):
        feed, session, _ = meteo_feed()
        feed.current_for_city('nyc')
        feed.current_for_city('denver')
        assert len(session.calls) == 2

    def test_invalidate_drops_one_point_then_everything(self):
        feed, session, _ = meteo_feed()
        feed.current(40.71, -74.01)
        feed.invalidate(34.05, -118.24)
        feed.current(40.71, -74.01)
        assert len(session.calls) == 1
        feed.invalidate()
        feed.current(40.71, -74.01)
        assert len(session.calls) == 2


# ---------------------------------------------------------------------------
# open-meteo: every failure mode
# ---------------------------------------------------------------------------

class TestOpenMeteoFailureModes(object):

    def test_network_exception_returns_none_and_never_raises(self):
        feed, _, _ = meteo_feed(IOError('dns'))
        obs, reason = feed.current_checked(40.71, -74.01)
        assert obs is None
        assert reason == 'open_meteo_network_failure'
        # One refusal, one `exc:` tick per attempt. See the NOAA twin of this
        # test for why the two counters are deliberately not the same number.
        assert feed.health['fail_network'] == 1
        assert feed.health['exc:OSError'] == feed.retries == 2

    def test_a_4xx_is_not_retried(self):
        feed, session, _ = meteo_feed(FakeResponse(400, None))
        obs, reason = feed.current_checked(40.71, -74.01)
        assert obs is None
        assert reason == 'open_meteo_http_error'
        assert len(session.calls) == 1
        assert feed.health['fail_http_4xx'] == 1

    def test_a_5xx_is_retried_then_refused(self):
        feed, session, _ = meteo_feed(FakeResponse(502, None))
        obs, reason = feed.current_checked(40.71, -74.01)
        assert obs is None
        assert reason == 'open_meteo_http_transient'
        assert len(session.calls) == 2
        assert feed.health['retries'] == 1

    def test_undecodable_body_is_its_own_reason(self):
        feed, _, _ = meteo_feed(FakeResponse(200, None,
                                             raises=ValueError('nope')))
        assert feed.current_checked(40.71, -74.01)[1] == 'open_meteo_bad_json'
        assert feed.health['fail_bad_json'] == 1

    @pytest.mark.parametrize('payload', ['a string', 42, [1, 2], None])
    def test_an_unrecognised_shape_is_refused(self, payload):
        feed, _, _ = meteo_feed(FakeResponse(200, payload))
        obs, reason = feed.current_checked(40.71, -74.01)
        assert obs is None
        assert reason == 'open_meteo_unexpected_shape'
        assert feed.health['fail_unexpected_shape'] == 1

    def test_an_error_body_behind_a_200_is_its_own_reason(self):
        # open-meteo answers a bad request with a 200 and `{'error': true}`.
        feed, _, _ = meteo_feed(FakeResponse(200, {'error': True,
                                                   'reason': 'bad param'}))
        obs, reason = feed.current_checked(40.71, -74.01)
        assert obs is None
        assert reason == 'open_meteo_no_current_block'
        assert feed.health['fail_no_current_block'] == 1

    def test_a_missing_temperature_field_is_its_own_reason(self):
        payload = dict(OPEN_METEO_PAYLOAD,
                       current={'time': '2026-08-18T11:00'})
        feed, _, _ = meteo_feed(FakeResponse(200, payload))
        obs, reason = feed.current_checked(40.71, -74.01)
        assert obs is None
        assert reason == 'open_meteo_no_temperature_field'
        assert feed.health['fail_no_temperature_field'] == 1

    @pytest.mark.parametrize('bad', [None, 'warm', {}])
    def test_an_unusable_temperature_value_is_the_same_reason(self, bad):
        payload = dict(OPEN_METEO_PAYLOAD,
                       current={'time': '2026-08-18T11:00',
                                'temperature_2m': bad})
        feed, _, _ = meteo_feed(FakeResponse(200, payload))
        assert feed.current_checked(40.71, -74.01)[1] == (
            'open_meteo_no_temperature_field')

    @pytest.mark.parametrize('bad', [float('nan'), float('inf')])
    def test_a_non_finite_temperature_is_refused(self, bad):
        payload = dict(OPEN_METEO_PAYLOAD,
                       current={'time': '2026-08-18T11:00',
                                'temperature_2m': bad})
        feed, _, _ = meteo_feed(FakeResponse(200, payload))
        obs, reason = feed.current_checked(40.71, -74.01)
        assert obs is None
        assert reason == 'open_meteo_non_finite_temperature'
        assert feed.health['fail_non_finite_temperature'] == 1

    def test_a_non_utc_response_is_refused_not_absorbed(self):
        # `current.time` is naive. Reading a local timestamp as UTC shifts the
        # freshness check by whole hours in exactly the field that decides
        # whether a reading is fresh.
        payload = dict(OPEN_METEO_PAYLOAD, utc_offset_seconds=-14400)
        feed, _, _ = meteo_feed(FakeResponse(200, payload))
        obs, reason = feed.current_checked(40.71, -74.01)
        assert obs is None
        assert reason == 'open_meteo_non_utc_response'
        assert feed.health['fail_non_utc_response'] == 1

    def test_a_missing_time_is_refused_not_read_as_now(self):
        payload = dict(OPEN_METEO_PAYLOAD,
                       current={'temperature_2m': 71.3})
        feed, _, _ = meteo_feed(FakeResponse(200, payload))
        obs, reason = feed.current_checked(40.71, -74.01)
        assert obs is None
        assert reason == 'open_meteo_obs_time_missing'
        assert feed.health['fail_obs_time_missing'] == 1

    def test_an_observation_older_than_the_max_age_is_refused(self):
        payload = dict(OPEN_METEO_PAYLOAD,
                       current={'time': '2026-08-18T09:00',
                                'temperature_2m': 71.3})
        feed, _, _ = meteo_feed(FakeResponse(200, payload))
        obs, reason = feed.current_checked(40.71, -74.01)
        assert obs is None
        assert reason == 'open_meteo_obs_stale'
        assert feed.health['fail_obs_stale'] == 1

    def test_the_obs_age_gate_can_be_disabled_for_a_recorder(self):
        payload = dict(OPEN_METEO_PAYLOAD,
                       current={'time': '2026-08-18T09:00',
                                'temperature_2m': 71.3})
        feed, _, _ = meteo_feed(FakeResponse(200, payload),
                                max_obs_age_sec=None)
        assert feed.current(40.71, -74.01) is not None

    @pytest.mark.parametrize('lat,lon', [
        (91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0),
        ('warm', 0.0), (None, 0.0), (0.0, None),
        (float('nan'), 0.0), (float('inf'), 0.0),
    ])
    def test_an_unusable_coordinate_never_reaches_the_network(self, lat, lon):
        # Caught locally because open-meteo answers an out-of-range coordinate
        # with a 200 and an error body, which would otherwise arrive as the
        # less specific `open_meteo_no_current_block`.
        feed, session, _ = meteo_feed()
        obs, reason = feed.current_checked(lat, lon)
        assert obs is None
        assert reason == 'open_meteo_bad_coordinates'
        assert session.calls == []


# ---------------------------------------------------------------------------
# Convention 20 across both modules
# ---------------------------------------------------------------------------

class TestOneCauseOneCounter(object):

    @pytest.mark.parametrize('module', [noaa_weather, open_meteo])
    def test_no_two_failure_causes_share_a_reason_string(self, module):
        reasons = module.FAILURE_REASONS
        assert len(reasons) == len(set(reasons))

    def test_the_two_modules_do_not_share_a_reason_string(self):
        # A pooled reason across two feeds would make an airport outage and a
        # downtown outage indistinguishable in the same log column.
        assert not (set(noaa_weather.FAILURE_REASONS)
                    & set(open_meteo.FAILURE_REASONS))

    @pytest.mark.parametrize('module,prefix', [
        (noaa_weather, 'metar_'), (open_meteo, 'open_meteo_')])
    def test_every_reason_is_namespaced_to_its_feed(self, module, prefix):
        assert all(r.startswith(prefix) for r in module.FAILURE_REASONS)

    def test_every_declared_noaa_reason_is_actually_reachable(self):
        # convention 22: a claim in a docstring is not a wiring test. This
        # asserts the LIST matches the code, so a reason that stops being
        # emitted (or one that is emitted but never declared) fails here.
        row_no_temp = dict(KLGA_ROW)
        del row_no_temp['temp']
        row_no_time = dict(KLGA_ROW)
        del row_no_time['obsTime']
        del row_no_time['reportTime']
        cases = [
            ('metar_bad_station_argument', lambda f: f.observation_checked('')),
            ('metar_network_failure', None),
            ('metar_http_transient', None),
            ('metar_http_error', None),
            ('metar_bad_json', None),
            ('metar_unexpected_shape', None),
            ('metar_no_observation', None),
            ('metar_no_temperature_field', None),
            ('metar_non_finite_temperature', None),
            ('metar_obs_time_missing', None),
            ('metar_obs_stale', None),
        ]
        responses = {
            'metar_network_failure': IOError('x'),
            'metar_http_transient': FakeResponse(500, None),
            'metar_http_error': FakeResponse(404, None),
            'metar_bad_json': FakeResponse(200, None, raises=ValueError('x')),
            'metar_unexpected_shape': FakeResponse(200, 'nope'),
            'metar_no_observation': FakeResponse(200, []),
            'metar_no_temperature_field': FakeResponse(200, [row_no_temp]),
            'metar_non_finite_temperature': FakeResponse(
                200, [dict(KLGA_ROW, temp=float('nan'))]),
            'metar_obs_time_missing': FakeResponse(200, [row_no_time]),
            'metar_obs_stale': FakeResponse(
                200, [dict(KLGA_ROW, obsTime=NOW - 99999)]),
        }
        seen = set()
        for reason, direct in cases:
            if direct is not None:
                feed, _, _ = noaa_feed()
                seen.add(direct(feed)[1])
                continue
            feed, _, _ = noaa_feed(responses[reason])
            seen.add(feed.observation_checked('KLGA')[1])
        assert seen == set(noaa_weather.FAILURE_REASONS)

    def test_every_declared_open_meteo_reason_is_actually_reachable(self):
        no_time = dict(OPEN_METEO_PAYLOAD, current={'temperature_2m': 71.3})
        no_temp = dict(OPEN_METEO_PAYLOAD, current={'time': '2026-08-18T11:00'})
        stale = dict(OPEN_METEO_PAYLOAD,
                     current={'time': '2020-01-01T00:00',
                              'temperature_2m': 71.3})
        nonfinite = dict(OPEN_METEO_PAYLOAD,
                         current={'time': '2026-08-18T11:00',
                                  'temperature_2m': float('nan')})
        responses = {
            'open_meteo_network_failure': IOError('x'),
            'open_meteo_http_transient': FakeResponse(500, None),
            'open_meteo_http_error': FakeResponse(404, None),
            'open_meteo_bad_json': FakeResponse(200, None,
                                                raises=ValueError('x')),
            'open_meteo_unexpected_shape': FakeResponse(200, 'nope'),
            'open_meteo_no_current_block': FakeResponse(200, {'error': True}),
            'open_meteo_no_temperature_field': FakeResponse(200, no_temp),
            'open_meteo_non_finite_temperature': FakeResponse(200, nonfinite),
            'open_meteo_unexpected_unit': FakeResponse(
                200, dict(OPEN_METEO_PAYLOAD,
                          current_units={'temperature_2m': 'kelvin'})),
            'open_meteo_non_utc_response': FakeResponse(
                200, dict(OPEN_METEO_PAYLOAD, utc_offset_seconds=3600)),
            'open_meteo_obs_time_missing': FakeResponse(200, no_time),
            'open_meteo_obs_stale': FakeResponse(200, stale),
        }
        seen = set()
        for reason in responses:
            feed, _, _ = meteo_feed(responses[reason])
            seen.add(feed.current_checked(40.71, -74.01)[1])
        feed, _, _ = meteo_feed()
        seen.add(feed.current_checked(999.0, 0.0)[1])
        assert seen == set(open_meteo.FAILURE_REASONS)

    @pytest.mark.parametrize('module,factory', [
        (noaa_weather, lambda: noaa_feed()[0]),
        (open_meteo, lambda: meteo_feed()[0])])
    def test_a_clean_run_records_no_failure_counter(self, module, factory):
        # The counters have to be quiet on success, otherwise a health dump
        # cannot be read at a glance.
        feed = factory()
        if module is noaa_weather:
            assert feed.observation('KLGA') is not None
        else:
            assert feed.current(40.71, -74.01) is not None
        assert [k for k in feed.health if k.startswith('fail_')] == []
        assert [k for k in feed.health if k.startswith('exc:')] == []
        assert feed.health['fetch_ok'] == 1

    @pytest.mark.parametrize('factory', [lambda: noaa_feed()[0],
                                         lambda: meteo_feed()[0]])
    def test_health_snapshot_is_a_copy(self, factory):
        feed = factory()
        snap = feed.health_snapshot()
        snap['requests'] = 999999
        assert feed.health.get('requests', 0) != 999999


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety(object):

    def test_concurrent_readers_agree_and_the_cache_stays_consistent(self):
        feed, _, _ = noaa_feed()
        results = []
        errors = []

        def worker():
            try:
                for _ in range(40):
                    obs = feed.observation('KLGA')
                    results.append(None if obs is None else obs.temp_c)
            except Exception as exc:                          # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(results) == 320
        assert set(results) == {22.8}

    def test_concurrent_open_meteo_readers_agree(self):
        feed, _, _ = meteo_feed()
        results = []

        def worker():
            for _ in range(40):
                obs = feed.current(40.71, -74.01)
                results.append(None if obs is None else round(obs.temp_f, 2))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert set(results) == {71.3}


# ---------------------------------------------------------------------------
# Paper-mode and import hygiene
# ---------------------------------------------------------------------------

class TestModuleInvariants(object):

    @pytest.mark.parametrize('module', [noaa_weather, open_meteo])
    def test_paper_mode_is_true(self, module):
        assert module.PAPER_MODE is True

    @pytest.mark.parametrize('module,expected', [
        (noaa_weather, 'https://aviationweather.gov/api/data/metar'),
        (open_meteo, 'https://api.open-meteo.com/v1/forecast')])
    def test_the_measured_urls_are_the_ones_shipped(self, module, expected):
        url = (module.AVIATION_WEATHER_URL if module is noaa_weather
               else module.OPEN_METEO_URL)
        assert url == expected
        assert url.startswith('https://')

    @pytest.mark.parametrize('module', [noaa_weather, open_meteo])
    def test_the_default_ttl_is_five_minutes(self, module):
        assert module.DEFAULT_TTL_SEC == 300.0

    @pytest.mark.parametrize('module', [noaa_weather, open_meteo])
    def test_zero_retries_still_sends_one_request(self, module):
        # retries=0 would mean "never send the request", which is never what a
        # caller means by it.
        if module is noaa_weather:
            feed, session, _ = noaa_feed(retries=0)
            feed.observation('KLGA')
        else:
            feed, session, _ = meteo_feed(retries=0)
            feed.current(40.71, -74.01)
        assert len(session.calls) == 1
