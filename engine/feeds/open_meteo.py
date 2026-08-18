"""Downtown grid-cell temperature from open-meteo.

WHAT THIS IS FOR, AND WHAT IT MUST NEVER BE USED FOR. This is the CONSUMER-APP
ANCHOR: the number a retail participant's phone shows for a city. It is a
forecast MODEL grid cell, not a station observation, and NOTHING resolves on
it. Polymarket city-temperature markets resolve on an airport station, which is
`engine/feeds/noaa_weather.py`.

Both readings exist so the airport-versus-downtown gap can be MEASURED. That
gap is the whole thesis of `strategies/polymarket/weather_arb.py`, it comes
from a social media post claiming 3 to 8 degrees Fahrenheit, and this repo has
never measured it. Until a recorder has paired readings on tape, "3 to 8
degrees" is a rumour. This module is half of the instrument for turning it into
a measurement. It is not half of an entry gate.

## THE ENDPOINT WAS MEASURED, NOT GUESSED

Verified from this machine on 2026-08-18T11:00Z:

    curl 'https://api.open-meteo.com/v1/forecast?latitude=40.71&longitude=-74.01
          &current=temperature_2m&temperature_unit=fahrenheit'
    HTTP 200
    {"latitude":40.710335,"longitude":-73.99308,
     "generationtime_ms":0.0147,"utc_offset_seconds":0,
     "timezone":"GMT","timezone_abbreviation":"GMT","elevation":27.0,
     "current_units":{"time":"iso8601","interval":"seconds",
                      "temperature_2m":"°F"},
     "current":{"time":"2026-08-18T11:00","interval":900,
                "temperature_2m":71.3}}

All five default cities returned 200 in the same minute: NYC 71.8F, LA 66.7F,
Chicago 61.4F, Denver 62.7F, Atlanta 73.0F. No auth, no key. GET only.

## UNITS ARE ASKED FOR EXPLICITLY, AND THEN VERIFIED

`temperature_unit=fahrenheit` is sent on every request AND the response's own
`current_units.temperature_2m` is read back and checked. Asking is not the same
as receiving: open-meteo's documented default is CELSIUS, so a dropped or
renamed parameter silently returns Celsius under the identical field name
`temperature_2m`. 22.0 and 71.6 are both plausible temperatures, so nothing
downstream could catch it, and a 50-degree error would read as a huge edge
rather than as a bug.

So the unit is verified, and:
  - degF in, degF used.
  - degC in (the parameter did not take), CONVERTED here, and the row is
    stamped `unit_requested='F'`, `unit_received='C'`, `converted=True` so the
    substitution is visible in a log rather than silent.
  - anything else is REFUSED under `open_meteo_unexpected_unit`. Guessing which
    scale an unrecognised marker means is exactly the mistake this block
    exists to prevent.

`CurrentTemperature` carries `temp_f` and `temp_c` under names that state their
unit, and has no field called `temp` or `temperature` for a tired reader to
grab.

## NOTE THE COORDINATES YOU GET BACK ARE NOT THE ONES YOU SENT

We asked for -74.0100 and open-meteo answered for -73.99308: it snaps to its
model grid. The response's own `latitude`/`longitude` are recorded as
`grid_lat`/`grid_lon` alongside the requested pair. For a gap measurement the
distance between the requested point and the served cell is part of the
measurement, not a rounding detail to be discarded.

## FRESHNESS: TWO CLOCKS, NEVER CONFLATED

  1. CACHE AGE, `ttl_sec` (default 300s). An entry is NEVER served past its
     TTL, and a failed refetch returns None rather than the expired copy.
     Serving stale-on-error turns an outage into a confident wrong number that
     no downstream gate can detect.
  2. OBSERVATION AGE, `max_obs_age_sec` (default 3600s). `current.interval` is
     900 seconds, so this feed updates every 15 minutes and an hour-old value
     means the upstream model stalled.

`current.time` is naive ISO with NO offset ('2026-08-18T11:00') and the
response says `timezone: GMT`, `utc_offset_seconds: 0`, so it is read as UTC.
That is an ASSUMPTION and it is the correct one only while we never send a
`timezone` parameter, which this module never does. The response's
`utc_offset_seconds` is checked and a non-zero offset is refused rather than
being read as UTC anyway - an off-by-one-timezone age check is 3600 seconds of
silent error in exactly the field that decides whether a reading is fresh.

A missing timestamp is refused under its own reason and is never read as "just
now". Set `max_obs_age_sec=None` to disable gate 2 for a recorder.

## HEALTH COUNTERS: ONE CAUSE, ONE COUNTER (convention 20)

`self.health` is a `Counter` and every distinct failure cause increments its
own key. No silent `continue`, no pooled `failures` number. `FAILURE_REASONS`
lists every refusal string and the tests assert they stay distinct.

Nothing here raises. A caller gets `None`, or `(None, reason)` from the
`_checked` variant.

## THREAD SAFETY

An `RLock` guards the cache, held around cache reads and writes only and never
across the HTTP call. Two threads racing the same cold city issue two requests
and the second write wins: a wasted request, not a wrong answer. Holding the
lock across a 2-second timeout would serialise every caller behind the slowest
city.
"""
import math
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

# Never False in this repo. Read-only module, no order path.
PAPER_MODE = True

#: Measured working URL. See the module docstring for the verbatim response.
OPEN_METEO_URL = 'https://api.open-meteo.com/v1/forecast'

DEFAULT_TIMEOUT_SEC = 2.0
DEFAULT_RETRIES = 2
BACKOFF_SEC = 0.25

#: `current.interval` is 900s upstream, so a 300s cache is finer than the data
#: changes and exists to stop a polling loop issuing identical requests.
DEFAULT_TTL_SEC = 300.0

#: Refusal age for the OBSERVATION, not the cache. Generous relative to the
#: 900s update interval: this is the "the upstream model stalled" gate, not a
#: precision gate.
DEFAULT_MAX_OBS_AGE_SEC = 3600.0

USER_AGENT = '05-trading-bot/paper (read-only)'

C_TO_F_SCALE = 9.0 / 5.0
C_TO_F_OFFSET = 32.0

#: Requested rounding for the cache key. Two callers asking for the same city
#: with 40.71 and 40.710001 must hit the same entry, and 4 decimals is about
#: 11 metres, far finer than open-meteo's grid.
COORD_KEY_DECIMALS = 4

#: City centres, the consumer-app anchor. Approximate to about a city block,
#: which is far finer than the model grid these resolve onto anyway.
#:
#: These are DOWNTOWN points, deliberately NOT the airports in
#: `noaa_weather.DEFAULT_AIRPORTS`. The distance between the two is the thing
#: being measured. Making them agree would delete the measurement.
DEFAULT_CITIES: Dict[str, Tuple[float, float]] = {
    'nyc': (40.71, -74.01),
    'la': (34.05, -118.24),
    'chicago': (41.88, -87.63),
    'denver': (39.74, -104.99),
    'atlanta': (33.75, -84.39),
}

#: Every reason this feed can refuse. One string per cause, never pooled.
FAILURE_REASONS = (
    'open_meteo_bad_coordinates',
    'open_meteo_network_failure',
    'open_meteo_http_transient',
    'open_meteo_http_error',
    'open_meteo_bad_json',
    'open_meteo_unexpected_shape',
    'open_meteo_no_current_block',
    'open_meteo_no_temperature_field',
    'open_meteo_non_finite_temperature',
    'open_meteo_unexpected_unit',
    'open_meteo_non_utc_response',
    'open_meteo_obs_time_missing',
    'open_meteo_obs_stale',
)

#: What `current_units.temperature_2m` is allowed to say. The degree sign is
#: U+00B0. open-meteo has been seen to send it with and without.
_UNIT_MARKERS = {
    '°f': 'F', 'f': 'F', 'degf': 'F', 'fahrenheit': 'F',
    '°c': 'C', 'c': 'C', 'degc': 'C', 'celsius': 'C',
}


def c_to_f(temp_c: float) -> float:
    """Celsius to Fahrenheit. The only conversion in this module."""
    return float(temp_c) * C_TO_F_SCALE + C_TO_F_OFFSET


def f_to_c(temp_f: float) -> float:
    """The inverse, so a row can carry both units without a second formula."""
    return (float(temp_f) - C_TO_F_OFFSET) / C_TO_F_SCALE


def normalise_unit(marker) -> Optional[str]:
    """'degF' / 'F' / 'fahrenheit' -> 'F'. Unrecognised -> None.

    None means REFUSE, never "assume the default". An unrecognised unit marker
    on a temperature is not a formatting quirk, it is a number whose scale we
    do not know.
    """
    if not isinstance(marker, str):
        return None
    return _UNIT_MARKERS.get(marker.strip().lower().replace(' ', ''))


def _parse_iso_seconds(value) -> Optional[int]:
    """ISO 8601 -> unix seconds, or None. A naive string is read as UTC.

    Naive is the normal case here: open-meteo returns '2026-08-18T11:00' with
    no offset while reporting `timezone: GMT`. The caller checks
    `utc_offset_seconds` before trusting that reading.
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
class CurrentTemperature:
    """One downtown grid-cell reading, in BOTH units, explicitly named.

    `req_lat`/`req_lon` are what we ASKED for. `grid_lat`/`grid_lon` are the
    cell open-meteo actually served. They differ, and for a gap measurement the
    difference is part of the measurement.

    `unit_received` records what the API said it sent, and `converted` is True
    when we had to convert because it was not what we asked for. A silent
    conversion would hide a parameter that stopped working.
    """

    req_lat: float
    req_lon: float
    grid_lat: Optional[float]
    grid_lon: Optional[float]
    temp_f: float
    temp_c: float
    observed_ts: int
    fetched_ts: float
    unit_requested: str = 'F'
    unit_received: str = 'F'
    converted: bool = False
    source: str = 'open_meteo_current'

    @property
    def point(self) -> str:
        """The requested coordinate as a stable label, e.g. '40.7100,-74.0100'."""
        return '{:.4f},{:.4f}'.format(self.req_lat, self.req_lon)

    def obs_age_sec(self, now: Optional[float] = None) -> float:
        return float(time.time() if now is None else now) - float(
            self.observed_ts)

    def to_dict(self) -> dict:
        return {'source': self.source,
                'point': self.point,
                'req_lat': self.req_lat,
                'req_lon': self.req_lon,
                'grid_lat': self.grid_lat,
                'grid_lon': self.grid_lon,
                'temp_f': round(self.temp_f, 2),
                'temp_c': round(self.temp_c, 2),
                'observed_ts': self.observed_ts,
                'fetched_ts': round(float(self.fetched_ts), 3),
                'unit_requested': self.unit_requested,
                'unit_received': self.unit_received,
                'converted': self.converted}


class OpenMeteoFeed(object):
    """Cached, thread-safe, never-raising current-temperature reader.

    `session`, `clock` and `sleep_fn` are injectable so tests run fully offline
    with no real network and no real sleeps.
    """

    url = OPEN_METEO_URL
    source_name = 'open_meteo_current'

    def __init__(self, session=None, timeout: float = DEFAULT_TIMEOUT_SEC,
                 retries: int = DEFAULT_RETRIES,
                 ttl_sec: float = DEFAULT_TTL_SEC,
                 max_obs_age_sec: Optional[float] = DEFAULT_MAX_OBS_AGE_SEC,
                 clock=None, sleep_fn=None):
        if session is None:
            import requests
            session = requests.Session()
            session.headers.update({'User-Agent': USER_AGENT})
        self.session = session
        self.timeout = float(timeout)
        self.retries = max(1, int(retries))
        self.ttl_sec = float(ttl_sec)
        self.max_obs_age_sec = (None if max_obs_age_sec is None
                                else float(max_obs_age_sec))
        self._clock = clock or time.time
        self._sleep = sleep_fn or time.sleep
        self.health: Counter = Counter()
        self._lock = threading.RLock()
        #: 'lat,lon' -> (fetched_at, CurrentTemperature)
        self._cache: Dict[str, Tuple[float, CurrentTemperature]] = {}

    # -- cache --------------------------------------------------------------

    @staticmethod
    def cache_key(lat: float, lon: float) -> str:
        fmt = '{:.' + str(COORD_KEY_DECIMALS) + 'f}'
        return (fmt + ',' + fmt).format(float(lat), float(lon))

    def _cached(self, key: str) -> Optional[CurrentTemperature]:
        """A live cache entry, or None. Expired entries are EVICTED here, so no
        later code path can return one by forgetting to check the age."""
        now = self._clock()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self.health['cache_miss'] += 1
                return None
            fetched_at, obs = entry
            if now - fetched_at >= self.ttl_sec:
                del self._cache[key]
                self.health['cache_expired'] += 1
                return None
            self.health['cache_hit'] += 1
            return obs

    def _store(self, key: str, obs: CurrentTemperature) -> None:
        with self._lock:
            self._cache[key] = (self._clock(), obs)

    def invalidate(self, lat: Optional[float] = None,
                   lon: Optional[float] = None) -> None:
        """Drop one point, or the whole cache."""
        with self._lock:
            if lat is None or lon is None:
                self._cache.clear()
            else:
                self._cache.pop(self.cache_key(lat, lon), None)

    # -- transport ----------------------------------------------------------

    def _get_json(self, params: dict) -> Tuple[Optional[object], str]:
        """GET and decode. `(payload, 'ok')` or `(None, reason)`.

        Retries network errors, 429 and 5xx. Does NOT retry other 4xx: a 400 on
        an out-of-range latitude is a real answer, and retrying it is extra load
        for the same answer.
        """
        for attempt in range(self.retries):
            is_last = attempt == self.retries - 1
            self.health['requests'] += 1
            try:
                resp = self.session.get(self.url, params=params,
                                        timeout=self.timeout)
            except Exception as exc:                          # noqa: BLE001
                # Deliberately broad. A feed exception must never escape into a
                # strategy's `evaluate`. The exception TYPE is counted so a DNS
                # failure and a read timeout stay distinguishable.
                self.health['exc:' + type(exc).__name__] += 1
                if is_last:
                    self.health['fail_network'] += 1
                    return None, 'open_meteo_network_failure'
                self.health['retries'] += 1
                self._sleep(BACKOFF_SEC * (2 ** attempt))
                continue

            code = getattr(resp, 'status_code', None)
            if code == 200:
                try:
                    payload = resp.json()
                except Exception:                             # noqa: BLE001
                    self.health['fail_bad_json'] += 1
                    return None, 'open_meteo_bad_json'
                if payload is None:
                    # A body of literal `null` decodes to None, which is the
                    # SAME value this function uses as its failure sentinel.
                    # Returning `(None, 'ok')` would label a refusal as a
                    # success - a failure pooled with the happy path, which is
                    # exactly what convention 20 forbids.
                    self.health['fail_unexpected_shape'] += 1
                    return None, 'open_meteo_unexpected_shape'
                return payload, 'ok'

            if code == 429 or (isinstance(code, int) and code >= 500):
                self.health['http_%s' % code] += 1
                if is_last:
                    self.health['fail_http_transient'] += 1
                    return None, 'open_meteo_http_transient'
                self.health['retries'] += 1
                self._sleep(BACKOFF_SEC * (2 ** attempt))
                continue

            self.health['http_%s' % code] += 1
            self.health['fail_http_4xx'] += 1
            return None, 'open_meteo_http_error'

        self.health['fail_network'] += 1                      # pragma: no cover
        return None, 'open_meteo_network_failure'             # pragma: no cover

    # -- parsing ------------------------------------------------------------

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if math.isfinite(out) else None

    def _from_payload(self, payload, lat: float, lon: float
                      ) -> Tuple[Optional[CurrentTemperature], str]:
        if not isinstance(payload, dict):
            self.health['fail_unexpected_shape'] += 1
            return None, 'open_meteo_unexpected_shape'

        current = payload.get('current')
        if not isinstance(current, dict):
            # An error payload from open-meteo is a dict with `error: true` and
            # a `reason`, so this is the shape a bad request arrives in after a
            # 200. Distinct from a transport failure on purpose.
            self.health['fail_no_current_block'] += 1
            return None, 'open_meteo_no_current_block'

        raw_temp = current.get('temperature_2m')
        if raw_temp is None:
            self.health['fail_no_temperature_field'] += 1
            return None, 'open_meteo_no_temperature_field'
        try:
            value = float(raw_temp)
        except (TypeError, ValueError):
            self.health['fail_no_temperature_field'] += 1
            return None, 'open_meteo_no_temperature_field'
        if not math.isfinite(value):
            # convention 19: `json.loads` accepts the literals `NaN` and
            # `Infinity`, so this is reachable from a real payload.
            self.health['fail_non_finite_temperature'] += 1
            return None, 'open_meteo_non_finite_temperature'

        # THE UNIT IS VERIFIED, NOT ASSUMED. See the module docstring: asking
        # for Fahrenheit and being handed Celsius is a 50-degree error that
        # nothing downstream can detect, because both numbers are plausible.
        units = payload.get('current_units')
        marker = units.get('temperature_2m') if isinstance(units, dict) else None
        received = normalise_unit(marker)
        if received is None:
            self.health['fail_unexpected_unit'] += 1
            return None, 'open_meteo_unexpected_unit'
        if received == 'F':
            temp_f, temp_c, converted = value, f_to_c(value), False
        else:
            temp_f, temp_c, converted = c_to_f(value), value, True
            # Loud, because it means `temperature_unit=fahrenheit` stopped
            # taking effect. The number is still right; the request is not.
            self.health['unit_conversion_applied'] += 1

        # A timezone offset we did not ask for makes the naive `current.time`
        # local, and reading it as UTC would shift the freshness check by whole
        # hours. Refuse rather than absorb it.
        offset = self._safe_float(payload.get('utc_offset_seconds'))
        if offset is not None and offset != 0.0:
            self.health['fail_non_utc_response'] += 1
            return None, 'open_meteo_non_utc_response'

        observed_ts = _parse_iso_seconds(current.get('time'))
        if observed_ts is None:
            # NOT the same as "just now", and never treated as such.
            self.health['fail_obs_time_missing'] += 1
            return None, 'open_meteo_obs_time_missing'

        now = float(self._clock())
        if self.max_obs_age_sec is not None:
            if now - float(observed_ts) > self.max_obs_age_sec:
                self.health['fail_obs_stale'] += 1
                return None, 'open_meteo_obs_stale'

        return CurrentTemperature(
            req_lat=float(lat),
            req_lon=float(lon),
            grid_lat=self._safe_float(payload.get('latitude')),
            grid_lon=self._safe_float(payload.get('longitude')),
            temp_f=temp_f,
            temp_c=temp_c,
            observed_ts=int(observed_ts),
            fetched_ts=now,
            unit_requested='F',
            unit_received=received,
            converted=converted,
            source=self.source_name), 'ok'

    # -- public API ---------------------------------------------------------

    def current_checked(self, lat, lon
                        ) -> Tuple[Optional[CurrentTemperature], str]:
        """`(CurrentTemperature, 'ok')` or `(None, reason)`.

        The `_checked` variant exists so a caller can put the CAUSE on its
        decision row.
        """
        lat_f = self._safe_float(lat)
        lon_f = self._safe_float(lon)
        if (lat_f is None or lon_f is None
                or not -90.0 <= lat_f <= 90.0
                or not -180.0 <= lon_f <= 180.0):
            # Caught here rather than sent, because open-meteo answers an
            # out-of-range coordinate with a 200 and an error body, which would
            # arrive as the less specific `open_meteo_no_current_block`.
            self.health['fail_bad_coordinates'] += 1
            return None, 'open_meteo_bad_coordinates'

        key = self.cache_key(lat_f, lon_f)
        cached = self._cached(key)
        if cached is not None:
            return cached, 'ok'

        payload, status = self._get_json({
            'latitude': lat_f,
            'longitude': lon_f,
            'current': 'temperature_2m',
            # Explicit. The documented DEFAULT IS CELSIUS, and it comes back
            # under the identical field name, so dropping this line is a silent
            # 50-degree error. The response unit is verified anyway.
            'temperature_unit': 'fahrenheit',
        })
        if payload is None:
            return None, status

        obs, status = self._from_payload(payload, lat_f, lon_f)
        if obs is None:
            return None, status

        self.health['fetch_ok'] += 1
        self._store(key, obs)
        return obs, 'ok'

    def current(self, lat, lon) -> Optional[CurrentTemperature]:
        """The reading, or None on any failure. Never raises."""
        obs, _ = self.current_checked(lat, lon)
        return obs

    def current_for_city_checked(self, city
                                 ) -> Tuple[Optional[CurrentTemperature], str]:
        """By `DEFAULT_CITIES` key. An unknown city is its own refusal, never a
        guessed coordinate."""
        point = DEFAULT_CITIES.get(str(city).strip().lower()) if city else None
        if point is None:
            self.health['fail_bad_coordinates'] += 1
            return None, 'open_meteo_bad_coordinates'
        return self.current_checked(point[0], point[1])

    def current_for_city(self, city) -> Optional[CurrentTemperature]:
        obs, _ = self.current_for_city_checked(city)
        return obs

    def health_snapshot(self) -> Dict[str, int]:
        """A copy of the counters. A copy so a caller cannot mutate them."""
        with self._lock:
            return dict(self.health)
