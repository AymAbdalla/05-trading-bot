"""Airport temperature from the NOAA/NWS aviation METAR feed.

WHAT THIS IS FOR. Polymarket city-temperature markets resolve on an official
airport ASOS/AWOS station, and this module is the read path for that station.
It is deliberately a plain feed with no strategy in it: it fetches, parses,
caches and counts, and it refuses rather than guesses. Scoring, thresholds and
entry logic live in `strategies/polymarket/weather_arb.py`.

## THE ENDPOINT WAS MEASURED, NOT GUESSED

Verified from this machine on 2026-08-18T11:00Z:

    curl 'https://aviationweather.gov/api/data/metar?ids=KLGA&format=json'
    HTTP 200
    [{"icaoId":"KLGA","receiptTime":"2026-08-18T10:54:07.802Z",
      "obsTime":1787050260,"reportTime":"2026-08-18T11:00:00.000Z",
      "temp":22.8,"dewp":21.7,"wdir":20,"wspd":3,"visib":"10+",
      "altim":1011.3,"slp":1011,"qcField":12,"precip":0.05,
      "metarType":"METAR",
      "rawOb":"METAR KLGA 181051Z 02003KT 10SM OVC019 23/22 A2986 RMK AO2 ...",
      "lat":40.7794,"lon":-73.8803,"elev":9,
      "name":"New York/La Guardia Arpt, NY, US","cover":"OVC","fltCat":"MVFR"}]

All five default airports returned 200 in one batched request in the same
minute: KLGA 22.8C, KLAX 20.0C, KORD 18.9C, KDEN 18.3C, KATL 23.3C. No auth,
no key, no wallet. GET only.

Note the endpoint returns a BARE JSON LIST, not an envelope. A parser that
reaches for `payload['data']` gets a TypeError on the real response, so both
shapes are handled and an unrecognised shape gets its own refusal rather than
being coerced.

## UNITS. READ THIS BEFORE TOUCHING THE PARSER

`temp` in this payload is degrees CELSIUS. The raw METAR group confirms it
independently: KLGA reported `temp: 22.8` alongside `rawOb: ... 23/22 ...`,
which is 23C/22C temp/dewpoint rounded to whole degrees. There is no
Fahrenheit anywhere in the payload.

A Polymarket US city ladder is usually quoted in FAHRENHEIT. Handing 22.8 to a
model expecting F prices 22.8F against a 73F market, a 50-degree error, and it
would look like an enormous edge on every rung rather than like a bug. So
`MetarObservation` carries BOTH numbers under names that say their unit -
`temp_c` and `temp_f` - and there is no field called `temp` or `temperature`
anywhere on it. A caller cannot pick the wrong one by accident because there is
no ambiguously-named one to pick.

The conversion is `temp_f = temp_c * 9/5 + 32`, done once, in `c_to_f`.

## QUANTISATION IS REAL AND IT IS NOT NOISE

METAR temperature is reported in whole or half degrees Celsius, so a converted
Fahrenheit value carries roughly 0.9F of quantisation. Against a market whose
rung edges sit on half-degrees F that is material near the line. `temp_c` is
kept on the observation precisely so a reader can see the native resolution
they are actually working with rather than the false precision of a converted
decimal.

## FRESHNESS: TWO DIFFERENT CLOCKS, NEVER CONFLATED

There are two separate ages here and mixing them is how a stale number gets
served as a fresh one:

  1. CACHE AGE - how long ago WE fetched. Governed by `ttl_sec` (default 300s).
     Past it the entry is evicted and refetched. A cache entry is NEVER served
     past its TTL, and a failed refetch returns None rather than falling back
     on the expired copy. Serving stale-on-error is the single most dangerous
     convenience a price feed can have: it converts an outage into a confident
     wrong number that no downstream gate can detect.
  2. OBSERVATION AGE - how long ago the STATION took the reading. Governed by
     `max_obs_age_sec` (default 3600s, matching `weather_arb.MAX_OBS_AGE_SEC`).
     A cold front can drop a station 15F in twenty minutes, so a 59-minute-old
     reading taken during one is simply a different temperature.

An observation whose timestamp is MISSING is refused under its own reason and
is never treated as "just now". An unaged reading and a fresh one are the same
number with completely different meanings.

Set `max_obs_age_sec=None` to disable gate 2. That is the right setting for a
RECORDER measuring the airport-versus-downtown gap, which wants every reading
the station published and can filter later. It is the wrong setting for
anything pricing a market.

## HEALTH COUNTERS: ONE CAUSE, ONE COUNTER (convention 20)

`self.health` is a `Counter` and every distinct failure cause increments its
OWN key. There is no silent `continue` and no pooled `failures` number: a run
that failed on the network and a run that got clean 404s on a bad ICAO need
completely different responses, and one combined count cannot tell them apart.
`FAILURE_REASONS` lists every refusal string, and the test suite asserts no two
causes share one.

Nothing in this module raises. A caller gets `None` (or `(None, reason)` from
the `_checked` variant) and decides what to do.

## THREAD SAFETY

The cache is guarded by an `RLock`. The shadow loop polls from one thread
today, but `engine/feeds/liquidation_recorder.py` is threaded and a gap
recorder polling five airports concurrently is the obvious next caller.

The lock is held around cache reads and writes ONLY, never across the HTTP
call. Two threads racing the same cold station therefore issue two requests and
the second write wins. That is a wasted request, not a wrong answer. Holding
the lock across a 2-second network timeout would serialise every caller behind
the slowest station, which is a worse failure.
"""
import math
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# Never False in this repo. This module is read-only and has no order path.
PAPER_MODE = True

#: Measured working URL. See the module docstring for the verbatim response.
#: `format=json` is REQUIRED: without it the endpoint serves raw METAR text and
#: `resp.json()` fails with `feed_bad_json`.
AVIATION_WEATHER_URL = 'https://aviationweather.gov/api/data/metar'

#: On the critical path of a decision, so the timeout is SHORT. A 10s hang on a
#: weather API is a decision that arrives after the book has moved, which is
#: worse than no decision at all.
DEFAULT_TIMEOUT_SEC = 2.0
DEFAULT_RETRIES = 2
BACKOFF_SEC = 0.25

#: The BATCH path gets its own, much longer timeout. MEASURED on 2026-08-18,
#: three trials each:
#:
#:     single KLGA                 0.17s, 0.03s, 0.03s
#:     batch KLGA,KLAX,KORD,KDEN,KATL   4.28s, 0.03s, 0.03s
#:
#: A cold five-station request takes 4.3s upstream and then 0.03s once their
#: side has it warm. Under the 2.0s single-station default the batch failed on
#: ALL FIVE stations with `metar_network_failure` on the first call of the
#: process and succeeded on every call after, which is the worst shape of bug:
#: it looks like a flaky network and it is actually a deterministic cold-start.
#:
#: 2.0s is still right for `observation()`, which sits on a decision's critical
#: path where a late answer is worse than no answer. The batch path is for a
#: RECORDER on a fixed cadence, which is off that path and would rather wait.
BATCH_TIMEOUT_SEC = 10.0

#: Cache lifetime. METAR is published roughly hourly (with SPECIALs in
#: between), so 300s is far finer than the underlying data changes and exists
#: to stop a 19-strategy x 3-asset cycle issuing 57 identical requests.
DEFAULT_TTL_SEC = 300.0

#: Default refusal age for the OBSERVATION (not the cache). Matches
#: `weather_arb.MAX_OBS_AGE_SEC`; restated rather than imported so this module
#: stays importable without the strategy package. If the two ever diverge, the
#: strategy's own gate is the binding one - it re-checks age itself.
DEFAULT_MAX_OBS_AGE_SEC = 3600.0

USER_AGENT = '05-trading-bot/paper (read-only)'

#: Celsius to Fahrenheit, defined in exactly one place in this module.
C_TO_F_SCALE = 9.0 / 5.0
C_TO_F_OFFSET = 32.0

#: The airports this feed is built for. NYC, LA, Chicago, Denver, Atlanta.
#:
#: THESE ARE FETCH TARGETS, NOT RESOLUTION SOURCES. Which station a market
#: actually resolves on is read off that market's own rules text by
#: `weather_arb.resolution_station_checked`, and nothing here may be used to
#: override it. In particular NYC markets have referenced BOTH KNYC (Central
#: Park) and KLGA (LaGuardia) at different times; KLGA is listed because it is
#: the airport station, and it is not a claim about any specific contract.
DEFAULT_AIRPORTS: Dict[str, str] = {
    'nyc': 'KLGA',
    'la': 'KLAX',
    'chicago': 'KORD',
    'denver': 'KDEN',
    'atlanta': 'KATL',
}

#: Every reason this feed can refuse to return an observation. One string per
#: cause, never pooled. A test asserts these are distinct and that each is
#: reachable, because a docstring claiming it is not a wiring test
#: (convention 22).
FAILURE_REASONS = (
    'metar_bad_station_argument',
    'metar_network_failure',
    'metar_http_transient',
    'metar_http_error',
    'metar_bad_json',
    'metar_unexpected_shape',
    'metar_no_observation',
    'metar_no_temperature_field',
    'metar_non_finite_temperature',
    'metar_obs_time_missing',
    'metar_obs_stale',
)


def c_to_f(temp_c: float) -> float:
    """Celsius to Fahrenheit. The only conversion in this module."""
    return float(temp_c) * C_TO_F_SCALE + C_TO_F_OFFSET


def _parse_iso_seconds(value) -> Optional[int]:
    """ISO 8601 -> unix seconds, or None. A naive string is read as UTC.

    `reportTime` arrives as '2026-08-18T11:00:00.000Z' with no offset marker
    other than the Z, which `datetime.fromisoformat` on Python 3.9 will not
    accept, hence the explicit replacement.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if math.isfinite(float(value)) else None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


@dataclass(frozen=True)
class MetarObservation:
    """One airport temperature reading, in BOTH units, explicitly named.

    There is deliberately no field called `temp` or `temperature`. The METAR
    payload is Celsius and most US Polymarket ladders are Fahrenheit, so an
    ambiguously-named field is a 50-degree error waiting for a tired reader.

    `observed_ts` is when the STATION took the reading (unix seconds).
    `fetched_ts` is when WE asked. They are different clocks and both are kept.
    """

    station: str            # ICAO, uppercased, as the payload reported it
    temp_c: float
    temp_f: float
    observed_ts: int
    fetched_ts: float
    raw_metar: Optional[str] = None
    station_name: Optional[str] = None
    source: str = 'noaa_aviationweather_metar'

    def obs_age_sec(self, now: Optional[float] = None) -> float:
        """Seconds since the STATION took this reading."""
        return float(time.time() if now is None else now) - float(
            self.observed_ts)

    def to_dict(self) -> dict:
        """Flat row for a log or a recorder. Units are in the key names."""
        return {'source': self.source,
                'station': self.station,
                'station_name': self.station_name,
                'temp_c': round(self.temp_c, 2),
                'temp_f': round(self.temp_f, 2),
                'observed_ts': self.observed_ts,
                'fetched_ts': round(float(self.fetched_ts), 3),
                'raw_metar': self.raw_metar}


class NoaaWeatherFeed(object):
    """Cached, thread-safe, never-raising METAR reader.

    `session`, `clock` and `sleep_fn` are injectable so tests run fully offline
    with no real network and no real sleeps.
    """

    url = AVIATION_WEATHER_URL
    source_name = 'noaa_aviationweather_metar'

    def __init__(self, session=None, timeout: float = DEFAULT_TIMEOUT_SEC,
                 retries: int = DEFAULT_RETRIES,
                 ttl_sec: float = DEFAULT_TTL_SEC,
                 max_obs_age_sec: Optional[float] = DEFAULT_MAX_OBS_AGE_SEC,
                 batch_timeout: float = BATCH_TIMEOUT_SEC,
                 clock=None, sleep_fn=None):
        if session is None:
            # Imported lazily so this module stays importable on a machine with
            # no network stack, the same reason `weather_arb._HttpFeed` defers
            # its own import.
            import requests
            session = requests.Session()
            session.headers.update({'User-Agent': USER_AGENT})
        self.session = session
        self.timeout = float(timeout)
        self.batch_timeout = float(batch_timeout)
        # retries=0 would mean "never send the request", which is never what a
        # caller means by it.
        self.retries = max(1, int(retries))
        self.ttl_sec = float(ttl_sec)
        self.max_obs_age_sec = (None if max_obs_age_sec is None
                                else float(max_obs_age_sec))
        self._clock = clock or time.time
        self._sleep = sleep_fn or time.sleep
        #: One counter per distinct cause. See the module docstring.
        self.health: Counter = Counter()
        self._lock = threading.RLock()
        #: icao -> (fetched_at, MetarObservation)
        self._cache: Dict[str, Tuple[float, MetarObservation]] = {}

    # -- cache --------------------------------------------------------------

    def _cached(self, icao: str) -> Optional[MetarObservation]:
        """A live cache entry, or None. Expired entries are EVICTED here.

        Eviction on read rather than on a timer means an expired entry can
        never be returned by a later code path that forgot to check the age.
        """
        now = self._clock()
        with self._lock:
            entry = self._cache.get(icao)
            if entry is None:
                self.health['cache_miss'] += 1
                return None
            fetched_at, obs = entry
            if now - fetched_at >= self.ttl_sec:
                del self._cache[icao]
                self.health['cache_expired'] += 1
                return None
            self.health['cache_hit'] += 1
            return obs

    def _store(self, icao: str, obs: MetarObservation) -> None:
        with self._lock:
            self._cache[icao] = (self._clock(), obs)

    def invalidate(self, icao: Optional[str] = None) -> None:
        """Drop one station, or the whole cache. For tests and for a recorder
        that wants a guaranteed fresh read at a fixed cadence."""
        with self._lock:
            if icao is None:
                self._cache.clear()
            else:
                self._cache.pop(str(icao).upper(), None)

    # -- transport ----------------------------------------------------------

    def _get_json(self, params: dict,
                  timeout: Optional[float] = None
                  ) -> Tuple[Optional[object], str]:
        """GET and decode. `(payload, 'ok')` or `(None, reason)`.

        Retries network errors, 429 and 5xx. Does NOT retry other 4xx: a 400 on
        a malformed ICAO is a real answer from the server, and retrying it is
        extra load for the same answer.

        `timeout` overrides `self.timeout` for the batch path. See
        `BATCH_TIMEOUT_SEC` for the measurement that made it necessary.
        """
        effective_timeout = self.timeout if timeout is None else float(timeout)
        for attempt in range(self.retries):
            is_last = attempt == self.retries - 1
            self.health['requests'] += 1
            try:
                resp = self.session.get(self.url, params=params,
                                        timeout=effective_timeout)
            except Exception as exc:                          # noqa: BLE001
                # Deliberately broad. A feed exception must never escape into a
                # strategy's `evaluate`, whatever the session decided to raise.
                # The exception TYPE is counted so a DNS failure and a read
                # timeout stay distinguishable in the health dump.
                self.health['exc:' + type(exc).__name__] += 1
                if is_last:
                    self.health['fail_network'] += 1
                    return None, 'metar_network_failure'
                self.health['retries'] += 1
                self._sleep(BACKOFF_SEC * (2 ** attempt))
                continue

            code = getattr(resp, 'status_code', None)
            if code == 200:
                try:
                    payload = resp.json()
                except Exception:                             # noqa: BLE001
                    self.health['fail_bad_json'] += 1
                    return None, 'metar_bad_json'
                if payload is None:
                    # A body of literal `null` decodes to None, which is the
                    # SAME value this function uses as its failure sentinel.
                    # Returning `(None, 'ok')` would hand the caller a refusal
                    # labelled as a success - a failure pooled with the happy
                    # path, which is the exact shape convention 20 exists to
                    # forbid. Caught by
                    # `test_an_unrecognised_shape_is_refused_not_coerced[None]`.
                    self.health['fail_unexpected_shape'] += 1
                    return None, 'metar_unexpected_shape'
                return payload, 'ok'

            if code == 429 or (isinstance(code, int) and code >= 500):
                self.health['http_%s' % code] += 1
                if is_last:
                    self.health['fail_http_transient'] += 1
                    return None, 'metar_http_transient'
                self.health['retries'] += 1
                self._sleep(BACKOFF_SEC * (2 ** attempt))
                continue

            self.health['http_%s' % code] += 1
            self.health['fail_http_4xx'] += 1
            return None, 'metar_http_error'

        # Unreachable: the loop returns on its last iteration either way.
        self.health['fail_network'] += 1                      # pragma: no cover
        return None, 'metar_network_failure'                  # pragma: no cover

    # -- parsing ------------------------------------------------------------

    def _rows(self, payload) -> Tuple[Optional[List[dict]], str]:
        """Normalise the two shapes this endpoint has been seen to return.

        Measured today it returns a BARE LIST. The `{'data': [...]}` envelope is
        accepted too because the endpoint has historically served it and a
        shape change should degrade to a refusal, not to a crash.
        """
        if isinstance(payload, list):
            return payload, 'ok'
        if isinstance(payload, dict):
            data = payload.get('data')
            if isinstance(data, list):
                return data, 'ok'
            self.health['fail_unexpected_shape'] += 1
            return None, 'metar_unexpected_shape'
        self.health['fail_unexpected_shape'] += 1
        return None, 'metar_unexpected_shape'

    def _observation_from_row(self, row: dict, requested_icao: str
                              ) -> Tuple[Optional[MetarObservation], str]:
        """One payload row -> a checked observation, or `(None, reason)`."""
        if not isinstance(row, dict):
            self.health['fail_unexpected_shape'] += 1
            return None, 'metar_unexpected_shape'

        raw_temp = row.get('temp')
        try:
            temp_c = float(raw_temp)
        except (TypeError, ValueError):
            self.health['fail_no_temperature_field'] += 1
            return None, 'metar_no_temperature_field'
        if not math.isfinite(temp_c):
            # convention 19: `float('nan')` and `float('inf')` sail straight
            # through `float()` and poison every average they touch downstream.
            # `json.loads` accepts the literals `NaN` and `Infinity`, so this is
            # reachable from a real payload and not just from a bad cast.
            self.health['fail_non_finite_temperature'] += 1
            return None, 'metar_non_finite_temperature'

        observed = row.get('obsTime')
        observed_ts = _parse_iso_seconds(observed)
        if observed_ts is None:
            observed_ts = _parse_iso_seconds(row.get('reportTime'))
        if observed_ts is None:
            # NOT the same as "just now", and never treated as such.
            self.health['fail_obs_time_missing'] += 1
            return None, 'metar_obs_time_missing'

        now = float(self._clock())
        if self.max_obs_age_sec is not None:
            if now - float(observed_ts) > self.max_obs_age_sec:
                self.health['fail_obs_stale'] += 1
                return None, 'metar_obs_stale'

        station = str(row.get('icaoId') or requested_icao).upper()
        name = row.get('name')
        return MetarObservation(
            station=station,
            temp_c=temp_c,
            temp_f=c_to_f(temp_c),
            observed_ts=int(observed_ts),
            fetched_ts=now,
            raw_metar=(row.get('rawOb') if isinstance(row.get('rawOb'), str)
                       else None),
            station_name=name if isinstance(name, str) else None,
            source=self.source_name), 'ok'

    # -- public API ---------------------------------------------------------

    def observation_checked(self, icao: str
                            ) -> Tuple[Optional[MetarObservation], str]:
        """`(MetarObservation, 'ok')` or `(None, reason)`.

        The `_checked` variant exists so a caller can put the CAUSE on its
        decision row. `observation()` is the same call for callers that only
        need the value.
        """
        if not isinstance(icao, str) or not icao.strip():
            self.health['fail_bad_station_argument'] += 1
            return None, 'metar_bad_station_argument'
        key = icao.strip().upper()

        cached = self._cached(key)
        if cached is not None:
            return cached, 'ok'

        payload, status = self._get_json({'ids': key, 'format': 'json'})
        if payload is None:
            return None, status

        rows, status = self._rows(payload)
        if rows is None:
            return None, status
        if not rows:
            # The station reported nothing, or the ICAO does not exist. Either
            # way we have no observation, and no observation is not a
            # temperature. Distinct from an HTTP failure on purpose.
            self.health['fail_no_observation'] += 1
            return None, 'metar_no_observation'

        obs, status = self._observation_from_row(rows[0], key)
        if obs is None:
            return None, status

        self.health['fetch_ok'] += 1
        self._store(key, obs)
        return obs, 'ok'

    def observation(self, icao: str) -> Optional[MetarObservation]:
        """The observation, or None on any failure. Never raises."""
        obs, _ = self.observation_checked(icao)
        return obs

    def observations(self, icaos) -> Dict[str, Optional[MetarObservation]]:
        """Several stations in ONE request where possible.

        The endpoint accepts a comma-separated `ids`, so five airports cost one
        round trip instead of five. Stations already live in the cache are
        served from it and are not requested again.

        Every requested station appears in the returned mapping, mapped to None
        when it could not be read. A missing key would be a silent drop, and a
        silent drop in a filter loop is a missing number (convention 20). Use
        `observations_checked` when the per-station cause matters.
        """
        return {k: v for k, (v, _) in self.observations_checked(icaos).items()}

    def observations_checked(self, icaos
                             ) -> Dict[str, Tuple[Optional[MetarObservation],
                                                  str]]:
        """As `observations`, keeping each station's own refusal reason."""
        out: Dict[str, Tuple[Optional[MetarObservation], str]] = {}
        wanted: List[str] = []
        for raw_icao in icaos or ():
            if not isinstance(raw_icao, str) or not raw_icao.strip():
                # Cannot key an output row on an unusable argument, so it is
                # counted and dropped rather than silently ignored.
                self.health['fail_bad_station_argument'] += 1
                continue
            key = raw_icao.strip().upper()
            if key in out or key in wanted:
                continue
            cached = self._cached(key)
            if cached is not None:
                out[key] = (cached, 'ok')
            else:
                wanted.append(key)

        if not wanted:
            return out

        payload, status = self._get_json({'ids': ','.join(wanted),
                                          'format': 'json'},
                                         timeout=self.batch_timeout)
        if payload is None:
            for key in wanted:
                out[key] = (None, status)
            return out

        rows, status = self._rows(payload)
        if rows is None:
            for key in wanted:
                out[key] = (None, status)
            return out

        # The endpoint does NOT guarantee request order, and it omits stations
        # it has nothing for. Indexing the response by position against the
        # request list would silently assign KDEN's temperature to KATL when
        # one of the five is missing, which is a wrong number rather than a
        # missing one. Match on the payload's own `icaoId` instead.
        by_icao: Dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                self.health['fail_unexpected_shape'] += 1
                continue
            key = str(row.get('icaoId') or '').upper()
            if key and key not in by_icao:
                by_icao[key] = row

        for key in wanted:
            row = by_icao.get(key)
            if row is None:
                self.health['fail_no_observation'] += 1
                out[key] = (None, 'metar_no_observation')
                continue
            obs, row_status = self._observation_from_row(row, key)
            if obs is None:
                out[key] = (None, row_status)
                continue
            self.health['fetch_ok'] += 1
            self._store(key, obs)
            out[key] = (obs, 'ok')
        return out

    def health_snapshot(self) -> Dict[str, int]:
        """A copy of the counters. A copy so a caller cannot mutate them."""
        with self._lock:
            return dict(self.health)
