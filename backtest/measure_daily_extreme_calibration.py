#!/usr/bin/env python3
"""Fit the daily-extreme sigma that `weather_arb` prices with, from real data.

THE HARNESS `DailyExtremeEstimate` NAMES AND `weather_arb`'s THIRD KILL
CONDITION NAMES. It did not exist until now, which is why every weather row
carried `daily_extreme_calibration_harness_exists: false`.

## THE QUANTITY. READ THIS BEFORE READING THE CODE.

Raven's instruction said "fit sigma (standard deviation of daily extremes) per
city". Taken literally that is the WRONG NUMBER and it is wrong by a lot.

The standard deviation of the daily high over 30 days is CLIMATE VARIABILITY:
how much a city's afternoon peak wanders from day to day, plus whatever
seasonal trend runs through the window. Madrid's daily high over the last 93
days has a standard deviation of about 8F. That number says nothing whatsoever
about how wrong OUR predictor is.

`weather_arb` does not predict the daily extreme with a climatological mean. It
predicts it with

    mu = open-meteo's forecast daily extreme for that local date at the
         station's own coordinates
       + (the station's current METAR reading - the grid's hourly value at the
          same hour)                                       <- the station bias

and then prices `M = max(O, X)`, `X ~ Normal(mu, sigma)`. The sigma that model
needs is the standard deviation of

    (the station's REALISED daily extreme) - mu

i.e. the FORECAST ERROR of the predictor the strategy actually uses at decision
time. Substituting climate sigma for forecast-error sigma is exactly the kind of
mistake that produces a number several times too wide, and a too-wide sigma is
what drives `max_attainable_p_yes` under `MIN_ATTAINABLE_P_YES` and refuses
every interior rung on the board.

So this harness reconstructs the predictor from archived data and measures its
residuals. It never computes a climate sigma, and there is a test that asserts
the fitted number is far below the climate one for the same station and window.

## THE THREE SERIES, AND WHICH ONE VERIFIES WHICH

    1. previous-runs API, `temperature_2m_previous_dayN`
       open-meteo's ARCHIVED hourly forecast for a past date, as issued by the
       model run N days before that date. Take the max over the LOCAL calendar
       day and you have exactly what `forecast.extreme_for(date, metric)` would
       have returned to `daily_extreme_estimate` at a decision made N days out.
       This is the predictor's mean, reconstructed honestly - it contains no
       information from after the run that produced it.

    2. previous-runs API, `temperature_2m`
       the same endpoint's CURRENT best value for that past date. Used for the
       GRID-verified residual and for the hourly grid values the station bias
       is measured against.

    3. aviationweather.gov METAR history
       the station's OWN observations. This is the only series that is a
       measurement rather than a model, and it is the one the market resolves
       on, so it is the verification target.

Two residuals are therefore computed and they are NEVER pooled:

    grid-verified     realised_grid_max(d) - forecast_grid_max(d, lead N)
                      Large n (about 93 days per station) but it verifies the
                      model against ITSELF. It measures run-to-run drift and is
                      a LOWER BOUND on the error against a thermometer. It is
                      reported because the gap between it and the
                      station-verified number IS the station-vs-grid problem,
                      quantified for the first time in this repo.

    station-verified  realised_station_max(d)
                        - (forecast_grid_max(d, lead N) + bias(d - 1))
                      THE OPERATIVE NUMBER. It is the residual of the actual
                      live predictor against the actual resolution source.
                      Small n, because METAR history is capped (see below).

`bias(d - 1)` is the median over the PREVIOUS local day of (station hourly mean
- grid hourly value). Using the previous day rather than the target day is
deliberate: a bias read from day `d` itself would carry information from after
the decision, which is the whole failure mode this file exists to avoid. The
live path reads the bias at the decision instant, which is inside day `d - 1`
for the next-day ladders that make up the tradable board, so this is the closest
honest reconstruction rather than a convenience.

## WHAT IS NOT RECONSTRUCTED, STATED RATHER THAN HIDDEN

  - SUB-24-HOUR LEADS DO NOT EXIST IN THIS ARCHIVE. open-meteo's previous-runs
    API resolves model runs to whole days: `previous_day1` is the run from one
    day before the target date, and there is no "the run from six hours ago".
    So the finest lead bucket this can fit is 24-48 hours. Every fitted sigma
    below 24 hours is an EXTRAPOLATION of the fitted curve and is flagged
    `sigma_horizon_is_extrapolated` on the artifact and on the decision row.
    This is not a large practical gap TODAY: every live weather row in
    `db/trading.db` sits between 24.5 and 37.6 hours to the local day's close,
    which is inside the lead-1 bucket the fit is measured on.

  - METAR HISTORY IS CAPPED AT ABOUT 400 ROWS PER STATION, which is 8 to 16
    local days, not 30. So the station-verified per-station `n` is single or
    low double digits and convention 7 applies with full force: a per-station
    fit on 8 days is a shrug. The POOLED station-verified fit across all
    stations is the number with real weight behind it, and both are written to
    the artifact with their own `n` so nobody has to guess which one they are
    reading.

  - ERA5 / archive-api IS NOT USED. It is a reanalysis on a grid, so it would
    be a third model rather than a second thermometer, and mixing it into the
    verification target would have inflated every residual by an amount nobody
    could later separate out.

## SIGMA IS RMSE, NOT SD, AND THAT IS A CHOICE

The station-verified residuals have large per-station MEANS (measured: +2.5F at
Madrid, -1.9F at LaGuardia, +2.2F at Haneda). `weather_arb` does NOT correct
that mean - it centres `X` on `forecast + bias` and nothing else - so the
predictive spread around the mean it actually uses is `sqrt(mean^2 + sd^2)`,
which is the RMSE. Reporting the SD alone would flatter the model by exactly the
bias it is not correcting. Both are written; `--sigma-basis` selects which one
the fitted curve is built from and the artifact records the choice.

## OUTPUT

A JSON artifact (default `research/weather_sigma_calibration.json`) that
`strategies.polymarket.weather_arb.load_sigma_calibration` reads. Written with
`allow_nan=False` (convention 19): a NaN sigma would sail through `json.loads`
and price a rung at a probability nothing downstream could interpret.

Fetched history is cached under `backtest/data/weather_calibration_cache/` so a
re-run does not re-hammer two public APIs; `--offline` refuses to send a single
request and fails loudly rather than silently fitting on a short cache.

USAGE

    # full run: discover the live station universe, fetch, fit, write
    env -u PYTHONPATH .venv/bin/python -m backtest.measure_daily_extreme_calibration

    # refit from cache only, no network at all
    env -u PYTHONPATH .venv/bin/python -m backtest.measure_daily_extreme_calibration \
        --stations file --stations-file backtest/data/weather_calibration_cache/stations.json \
        --offline
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:                            # pragma: no cover
    sys.path.insert(0, _REPO_ROOT)

# ---------------------------------------------------------------------------
# Endpoints. GET only, no auth, no key.
# ---------------------------------------------------------------------------

PREVIOUS_RUNS_URL = 'https://previous-runs-api.open-meteo.com/v1/forecast'
METAR_URL = 'https://aviationweather.gov/api/data/metar'
USER_AGENT = '05-trading-bot/paper (read-only)'

DEFAULT_CACHE_DIR = os.path.join(_REPO_ROOT, 'backtest', 'data',
                                 'weather_calibration_cache')
DEFAULT_OUT_PATH = os.path.join(_REPO_ROOT, 'research',
                                'weather_sigma_calibration.json')
DEFAULT_DB_PATH = os.path.join(_REPO_ROOT, 'db', 'trading.db')

#: Cache entries older than this are refetched. A day of history does not
#: change once it is past, but the newest day does, and a stale cache that
#: silently drops the most recent lead-1 samples would shrink `n` without
#: saying so.
DEFAULT_CACHE_TTL_SEC = 6 * 3600.0

HTTP_TIMEOUT_SEC = 60.0
HTTP_RETRIES = 3
HTTP_BACKOFF_SEC = 1.0

# ---------------------------------------------------------------------------
# Fit parameters. Every one is an assumption with an expiry date (convention 17)
# and every one is written into the artifact so a reader never has to guess
# which settings produced a number.
# ---------------------------------------------------------------------------

#: Model runs this harness can reconstruct. `previous_dayN` is the run issued N
#: days before the target date, so lead N covers decisions taken between N*24
#: and (N+1)*24 hours before the local day closes.
DEFAULT_LEAD_DAYS: Tuple[int, ...] = (1, 2, 3, 4)

#: How many days of previous-runs history to ask for. 92 is the endpoint's
#: usable ceiling as measured 2026-08-18 (2,232 hourly points came back).
DEFAULT_PAST_DAYS = 92

#: How far back to ask aviationweather.gov for observations. The endpoint caps
#: the response at about 400 rows whatever we ask, which is 8 to 16 local days
#: at a 30-to-60-minute reporting cadence. Asking for 720 costs nothing and
#: takes whatever the cap allows.
DEFAULT_METAR_HOURS = 720

#: Coverage floors. A "daily extreme" computed from four hours of a day is not
#: a daily extreme, and letting one in would put a number that is wrong by the
#: whole diurnal swing into the residual set.
MIN_GRID_HOURS_PER_DAY = 20
MIN_METAR_OBS_PER_DAY = 18
MIN_BIAS_HOURS_PER_DAY = 12

#: Residual count below which a per-(station, metric, lead) cell is NOT fitted.
#: Convention 7: this is thin either way, and the artifact carries `n` so a
#: reader can apply their own floor. What this number controls is only whether
#: the strategy is allowed to price that station at all.
DEFAULT_MIN_SAMPLES = 6

#: A fitted sigma is clamped into this range before it is written. Both ends are
#: refusals to publish an impossible number rather than tuning:
#:   - a sigma at or below zero prices every rung 0.00 or 1.00 with a full-size
#:     position behind it,
#:   - a sigma above 12F is wider than the entire diurnal range of most of these
#:     stations and means the fit failed, not that the weather is uncertain.
#: Clamps are COUNTED and reported; a silent clamp is a fit that failed while
#: looking like it worked.
MIN_FITTED_SIGMA_F = 0.25
MAX_FITTED_SIGMA_F = 12.0

#: The probability floor an entry has to clear, restated here so the rung
#: tradeability table in this harness and the gate in `weather_arb` cannot
#: drift apart silently. `weather_arb.MIN_MODEL_P_SIDE` is the live one and
#: `test_weather_sigma_calibration` pins them equal.
ENTRY_P_FLOOR = 0.55

SCHEMA_VERSION = 2

C_TO_F_SCALE = 9.0 / 5.0
C_TO_F_OFFSET = 32.0
SECONDS_PER_DAY = 86400


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------

def _finite(value) -> Optional[float]:
    """A finite float or None. convention 19: NaN sails through `float()`."""
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def c_to_f(temp_c: float) -> float:
    return float(temp_c) * C_TO_F_SCALE + C_TO_F_OFFSET


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    """Round, or pass None through. Never emits a NaN into the artifact."""
    if value is None:
        return None
    out = _finite(value)
    return None if out is None else round(out, digits)


def lead_midpoint_hours(lead_days: int) -> float:
    """Hours-to-local-day-close at the MIDDLE of a lead bucket.

    `previous_dayN` is the run issued N days before the target date, and it is
    the freshest run available to a decision taken anywhere from N*24 to
    (N+1)*24 hours before that date's local day closes. The midpoint is the
    horizon a residual measured against that run is representative of.

    Lead 1 lands on 36.0 hours, which is exactly
    `weather_arb.MAX_HOURS_TO_WINDOW_CLOSE` and covers the whole live board
    (measured: every weather row in `db/trading.db` sits at 24.5 to 37.6 hours).
    """
    return (float(int(lead_days)) + 0.5) * 24.0


# ---------------------------------------------------------------------------
# Residual statistics
# ---------------------------------------------------------------------------

def residual_stats(values: Sequence[float]) -> Dict[str, object]:
    """Mean, SD and RMSE of a residual set, plus its `n`.

    RMSE is taken ABOUT ZERO, not about the sample mean, and that is the whole
    point of computing it: the strategy centres `X` on `forecast + bias` and
    does not subtract any residual mean, so the spread of its predictive
    distribution around the mean it actually uses is `sqrt(mean^2 + sd^2)`.
    Reporting SD alone would credit the model with a bias correction it does not
    perform.

    `n < 2` returns `sd_f=None` rather than 0.0. One sample has no spread, and a
    zero there would be read downstream as a perfect forecast.
    """
    clean = [v for v in (_finite(x) for x in values) if v is not None]
    n = len(clean)
    if n == 0:
        return {'n': 0, 'mean_f': None, 'sd_f': None, 'rmse_f': None,
                'min_f': None, 'max_f': None}
    mean = statistics.fmean(clean)
    sd = statistics.pstdev(clean) if n >= 2 else None
    rmse = math.sqrt(statistics.fmean([v * v for v in clean]))
    return {'n': n, 'mean_f': _round(mean), 'sd_f': _round(sd),
            'rmse_f': _round(rmse), 'min_f': _round(min(clean)),
            'max_f': _round(max(clean))}


def climate_sigma(extremes: Sequence[float]) -> Optional[float]:
    """The number this harness deliberately does NOT fit. Reported to contrast.

    The standard deviation of the realised daily extremes themselves, over the
    fetched window. It is the answer to "how much does this city's afternoon
    peak wander", which is not a question anything in `weather_arb` asks. It is
    written into the artifact next to the forecast-error sigma so the size of
    the difference is visible rather than argued about.
    """
    clean = [v for v in (_finite(x) for x in extremes) if v is not None]
    if len(clean) < 2:
        return None
    return statistics.pstdev(clean)


def fit_sqrt_curve(points: Sequence[Tuple[float, float]]
                   ) -> Dict[str, object]:
    """Least squares for `sigma(h) = floor + b * sqrt(h)`.

    `points` are `(hours, sigma)`. Returns the two coefficients, the `r2` of the
    fit and a `clamped` list naming every coefficient that had to be pulled back
    into a publishable range.

    Two clamps, both refusals rather than tuning:

      - a NEGATIVE `b` says the forecast gets worse as the target gets closer.
        That is not a weather fact, it is four noisy points, and publishing it
        would give the strategy a sigma that SHRINKS with the horizon. `b` is
        floored at zero, which degrades the fit to a flat sigma - honest, and
        visible in `clamped`.
      - a `floor` at or below `MIN_FITTED_SIGMA_F` says the forecast is exact at
        zero lead. Nothing is. The curve is REFITTED with the intercept pinned
        at the floor rather than the intercept simply being raised, and the
        difference is not cosmetic: raising an intercept without refitting the
        slope shifts the whole curve UP by the correction, which at San
        Francisco turned a measured 5.7F lead-1 residual into an 11.0F published
        sigma. A pinned-intercept refit stays next to the data it was fitted on.

    One point cannot determine two coefficients, so a single point returns a
    FLAT curve at that point's sigma with `b = 0` and says so. Zero points
    return `ok=False`; it never invents one.
    """
    clean = [(float(h), float(s)) for h, s in points
             if _finite(h) is not None and _finite(s) is not None
             and float(h) >= 0.0 and float(s) > 0.0]
    if not clean:
        return {'ok': False, 'reason': 'no_usable_points', 'floor_f': None,
                'per_sqrt_hour_f': None, 'r2': None, 'clamped': [],
                'points': 0}
    clamped: List[str] = []
    if len(clean) == 1:
        floor = clean[0][1]
        slope = 0.0
        clamped.append('flat_single_point')
        r2 = None
    else:
        xs = [math.sqrt(h) for h, _ in clean]
        ys = [s for _, s in clean]
        mean_x = statistics.fmean(xs)
        mean_y = statistics.fmean(ys)
        sxx = sum((x - mean_x) ** 2 for x in xs)
        if sxx <= 0:
            floor, slope, r2 = mean_y, 0.0, None
            clamped.append('flat_degenerate_x')
        else:
            sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
            slope = sxy / sxx
            floor = mean_y - slope * mean_x
            ss_tot = sum((y - mean_y) ** 2 for y in ys)
            ss_res = sum((y - (floor + slope * x)) ** 2
                         for x, y in zip(xs, ys))
            r2 = None if ss_tot <= 0 else 1.0 - ss_res / ss_tot
    if slope < 0.0:
        # Refit as a flat line at the mean sigma rather than keeping an
        # intercept that was only sensible with the negative slope attached.
        slope = 0.0
        floor = statistics.fmean([s for _, s in clean])
        clamped.append('negative_slope')
    if floor < MIN_FITTED_SIGMA_F:
        floor = MIN_FITTED_SIGMA_F
        # Pinned-intercept refit: minimise sum((y - floor - b*sqrt(h))^2) over
        # b alone. Without this the curve is translated upward by the whole
        # correction and stops describing the residuals it came from.
        xs = [math.sqrt(h) for h, _ in clean]
        ys = [s for _, s in clean]
        denom = sum(x * x for x in xs)
        slope = (max(0.0, sum(x * (y - floor) for x, y in zip(xs, ys)) / denom)
                 if denom > 0 else 0.0)
        clamped.append('floor_below_min')
    if floor > MAX_FITTED_SIGMA_F:
        floor = MAX_FITTED_SIGMA_F
        clamped.append('floor_above_max')
    return {'ok': True, 'reason': None, 'floor_f': _round(floor),
            'per_sqrt_hour_f': _round(slope), 'r2': _round(r2),
            'clamped': clamped, 'points': len(clean)}


def sigma_at(floor_f: float, per_sqrt_hour_f: float, hours: float) -> float:
    """`floor + b * sqrt(max(0, hours))`. The same form `weather_arb` uses.

    Restated here rather than imported so this harness can be run against an
    artifact without importing the strategy, and `test_weather_sigma_calibration`
    pins the two implementations equal (convention 23: a fix at one site is not
    a fix, and a formula at two sites needs a test that they agree).
    """
    return float(floor_f) + float(per_sqrt_hour_f) * math.sqrt(
        max(0.0, float(hours)))


def max_attainable_p_yes(width_f: Optional[float], sigma_f: float
                         ) -> Optional[float]:
    """`2 * Phi(w / (2 * sigma)) - 1`, or None for an unbounded rung.

    Duplicated from `weather_arb.max_attainable_p_yes` in WIDTH form so the
    tradeability table can be computed from a width census without building a
    `Threshold`. Pinned equal to the strategy's version by a test.
    """
    if width_f is None:
        return None
    sigma = _finite(sigma_f)
    if sigma is None or sigma <= 0:
        return 1.0
    return 2.0 * statistics.NormalDist(0.0, 1.0).cdf(
        float(width_f) / (2.0 * sigma)) - 1.0


def sigma_needed_for(width_f: float, p_target: float) -> Optional[float]:
    """The LARGEST sigma at which a rung of width `w` can reach `p_target`.

    Inverts the ceiling: `p = 2 * Phi(w / (2 * sigma)) - 1` gives
    `sigma = w / (2 * Phi^-1((1 + p) / 2))`.

    This is the number that decides whether fitting sigma can ever unblock the
    interior of a Celsius ladder, and it is worth stating in the open: a 1.8F
    bucket needs sigma under 1.19F to reach 0.55, and under 1.33F to reach 0.50.
    """
    p = _finite(p_target)
    w = _finite(width_f)
    if p is None or w is None or not 0.0 < p < 1.0 or w <= 0:
        return None
    z = statistics.NormalDist(0.0, 1.0).inv_cdf((1.0 + p) / 2.0)
    if z <= 0:
        return None
    return w / (2.0 * z)


# ---------------------------------------------------------------------------
# Series assembly
# ---------------------------------------------------------------------------

def hourly_by_local_day(times: Sequence[str], values: Sequence
                        ) -> Dict[str, Dict[str, float]]:
    """`{'2026-08-18': {'2026-08-18T14:00': 91.2, ...}, ...}`.

    The timestamps are ALREADY LOCAL: every request goes out with
    `timezone=auto`, so open-meteo returns the station's own calendar. That
    matters more than it looks - a Polymarket temperature market resolves on the
    station's LOCAL day, and slicing a UTC day would mix two of them at every
    station more than a couple of hours off Greenwich.
    """
    out: Dict[str, Dict[str, float]] = {}
    for stamp, raw in zip(times, values):
        if not isinstance(stamp, str) or len(stamp) < 13:
            continue
        value = _finite(raw)
        if value is None:
            continue
        out.setdefault(stamp[:10], {})[stamp[:13] + ':00'] = value
    return out


def daily_extremes(by_day: Dict[str, Dict[str, float]], metric: str,
                   min_hours: int = MIN_GRID_HOURS_PER_DAY
                   ) -> Dict[str, float]:
    """The max (or min) per local day, for days with enough coverage.

    A day with fewer than `min_hours` readings is DROPPED, not extremed over
    what is there. The diurnal swing at these stations is 15 to 30F, so a max
    over the eight hours we happen to hold can be wrong by more than the entire
    quantity being measured.
    """
    pick = max if metric == 'daily_high' else min
    out: Dict[str, float] = {}
    for day, hours in by_day.items():
        if len(hours) < int(min_hours):
            continue
        out[day] = pick(hours.values())
    return out


def metar_local_series(rows: Iterable[dict], utc_offset_sec: int
                       ) -> Tuple[Dict[str, List[float]],
                                  Dict[str, List[float]]]:
    """METAR rows -> `({local_day: [temp_f]}, {local_hour: [temp_f]})`.

    `temp` on an aviationweather.gov row is CELSIUS. Converting here rather than
    at the call site keeps the one unit conversion in one place; a Fahrenheit
    number compared against a Celsius one is a 30-to-60 degree error that looks
    exactly like a screaming edge.

    Rows with no `reportTime` or no `temp` are dropped silently HERE only
    because the caller reports the kept/dropped counts on the artifact.
    """
    by_day: Dict[str, List[float]] = {}
    by_hour: Dict[str, List[float]] = {}
    offset = int(utc_offset_sec)
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = row.get('reportTime')
        temp_c = _finite(row.get('temp'))
        if not isinstance(stamp, str) or temp_c is None:
            continue
        ts = _parse_utc_seconds(stamp)
        if ts is None:
            continue
        local = datetime.fromtimestamp(ts + offset, tz=timezone.utc)
        day = local.strftime('%Y-%m-%d')
        hour = local.strftime('%Y-%m-%dT%H:00')
        by_day.setdefault(day, []).append(c_to_f(temp_c))
        by_hour.setdefault(hour, []).append(c_to_f(temp_c))
    return by_day, by_hour


def _parse_utc_seconds(stamp: str) -> Optional[int]:
    """`'2026-08-18T15:00:00.000Z'` -> unix seconds, or None.

    Naive strings are read as UTC, which is what aviationweather.gov serves.
    """
    text = stamp.strip().replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def station_daily_extremes(by_day: Dict[str, List[float]], metric: str,
                           min_obs: int = MIN_METAR_OBS_PER_DAY
                           ) -> Dict[str, float]:
    """Realised station extreme per local day, for days with enough reports."""
    pick = max if metric == 'daily_high' else min
    return {day: pick(temps) for day, temps in by_day.items()
            if len(temps) >= int(min_obs)}


def station_minus_grid_bias(station_hours: Dict[str, List[float]],
                            grid_hours: Dict[str, float], day: str,
                            min_hours: int = MIN_BIAS_HOURS_PER_DAY
                            ) -> Optional[float]:
    """Median hourly (station - grid) over one local day, or None.

    MEDIAN rather than mean: one METAR with a stuck sensor or one hour where the
    grid cell sat under a shower moves a mean by degrees and a median by
    nothing. The live path takes a single instantaneous difference, which is
    noisier still - so this is, if anything, a slightly kinder reconstruction of
    the live bias than the live bias itself, and the direction of that
    difference is stated rather than hidden.
    """
    diffs = []
    for hour, temps in station_hours.items():
        if hour[:10] != day:
            continue
        grid = grid_hours.get(hour)
        if grid is None:
            continue
        diffs.append(statistics.fmean(temps) - grid)
    if len(diffs) < int(min_hours):
        return None
    return statistics.median(diffs)


def previous_local_day(day: str) -> str:
    """`'2026-08-18'` -> `'2026-08-17'`. Calendar arithmetic, not `-86400`."""
    return (datetime.strptime(day, '%Y-%m-%d')
            - timedelta(days=1)).strftime('%Y-%m-%d')


# ---------------------------------------------------------------------------
# Residual construction
# ---------------------------------------------------------------------------

@dataclass
class StationSeries:
    """Everything one station contributes, already sliced into local days."""

    icao: str
    city: str
    lat: float
    lon: float
    utc_offset_sec: int
    timezone_name: str
    #: `{lead_days: {local_day: forecast_extreme_f}}` per metric
    forecast: Dict[str, Dict[int, Dict[str, float]]] = field(
        default_factory=dict)
    #: `{local_day: realised_grid_extreme_f}` per metric
    grid_realised: Dict[str, Dict[str, float]] = field(default_factory=dict)
    #: `{local_day: realised_station_extreme_f}` per metric
    station_realised: Dict[str, Dict[str, float]] = field(default_factory=dict)
    #: `{local_day: bias_f}`
    bias: Dict[str, float] = field(default_factory=dict)
    notes: Dict[str, object] = field(default_factory=dict)


def grid_residuals(series: StationSeries, metric: str, lead: int
                   ) -> List[float]:
    """`realised_grid_extreme - forecast_extreme`, per local day.

    Model against its own later self. LOWER BOUND on the error that matters.
    """
    forecasts = series.forecast.get(metric, {}).get(lead, {})
    realised = series.grid_realised.get(metric, {})
    return [realised[day] - forecasts[day]
            for day in sorted(realised) if day in forecasts]


def station_residuals(series: StationSeries, metric: str, lead: int
                      ) -> List[float]:
    """THE OPERATIVE RESIDUAL: station realised minus the live predictor.

    `realised_station_extreme(d) - (forecast_extreme(d, lead) + bias(d - 1))`.

    The bias comes from the day BEFORE the target day, so nothing in this
    number is knowable only after the decision it reconstructs.
    """
    forecasts = series.forecast.get(metric, {}).get(lead, {})
    realised = series.station_realised.get(metric, {})
    out: List[float] = []
    for day in sorted(realised):
        forecast = forecasts.get(day)
        bias = series.bias.get(previous_local_day(day))
        if forecast is None or bias is None:
            continue
        out.append(realised[day] - (forecast + bias))
    return out


# ---------------------------------------------------------------------------
# HTTP + cache
# ---------------------------------------------------------------------------

class JsonCache(object):
    """On-disk JSON cache keyed by a caller-supplied name.

    Exists so a re-run does not re-hammer two free public APIs, and so a fit can
    be re-run offline against exactly the bytes the previous run saw.

    `ttl_sec <= 0` disables reading (every call refetches) but still writes.
    """

    def __init__(self, directory: str, ttl_sec: float = DEFAULT_CACHE_TTL_SEC,
                 clock: Optional[Callable[[], float]] = None):
        self.directory = directory
        self.ttl_sec = float(ttl_sec)
        self._clock = clock or time.time
        self.hits = 0
        self.misses = 0
        self.writes = 0

    def path_for(self, key: str) -> str:
        safe = ''.join(c if (c.isalnum() or c in '-_.') else '_' for c in key)
        return os.path.join(self.directory, safe + '.json')

    def get(self, key: str) -> Optional[object]:
        path = self.path_for(key)
        if not os.path.exists(path):
            self.misses += 1
            return None
        if self.ttl_sec > 0:
            age = float(self._clock()) - os.path.getmtime(path)
            if age > self.ttl_sec:
                self.misses += 1
                return None
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            # A truncated cache file is a miss, never a fatal error and never a
            # silently empty result set.
            self.misses += 1
            return None
        self.hits += 1
        return payload

    def put(self, key: str, payload: object) -> None:
        os.makedirs(self.directory, exist_ok=True)
        path = self.path_for(key)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, allow_nan=False)
        os.replace(tmp, path)
        self.writes += 1

    def stale_only(self, key: str) -> Optional[object]:
        """Read a cache entry IGNORING the TTL. Used by `--offline`.

        Separate method rather than a flag because "I accepted an entry older
        than the TTL" is a fact a run has to be able to report.
        """
        path = self.path_for(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None


def http_get_json(url: str, params: dict, timeout: float = HTTP_TIMEOUT_SEC,
                  retries: int = HTTP_RETRIES) -> object:
    """GET and decode, with a bounded retry. RAISES on final failure.

    Deliberately raises rather than returning None: this is an offline research
    harness, not a decision path, and a fit quietly computed on the stations
    that happened to answer would be a worse outcome than a traceback.
    """
    import requests                                        # lazy, see feeds

    last = None
    for attempt in range(max(1, int(retries))):
        try:
            resp = requests.get(url, params=params, timeout=timeout,
                                headers={'User-Agent': USER_AGENT})
            if resp.status_code == 200:
                return resp.json()
            last = 'HTTP %s: %s' % (resp.status_code, resp.text[:200])
        except Exception as exc:                           # noqa: BLE001
            last = '%s: %s' % (type(exc).__name__, exc)
        if attempt < retries - 1:
            time.sleep(HTTP_BACKOFF_SEC * (2 ** attempt))
    raise RuntimeError('GET %s failed: %s' % (url, last))


@dataclass
class Fetcher:
    """Cache + HTTP, with the HTTP half injectable so tests never touch a wire.

    `offline=True` refuses to call `http` at all and raises when the cache
    cannot answer. That is the property that makes the test suite's "no network
    calls" assertion checkable rather than hopeful.
    """

    cache: JsonCache
    http: Callable[[str, dict], object] = http_get_json
    offline: bool = False
    requests_sent: int = 0

    def json(self, key: str, url: str, params: dict) -> object:
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        if self.offline:
            stale = self.cache.stale_only(key)
            if stale is not None:
                return stale
            raise RuntimeError(
                'offline and no cache entry for %r (run once without '
                '--offline to populate %s)' % (key, self.cache.directory))
        payload = self.http(url, params)
        self.requests_sent += 1
        self.cache.put(key, payload)
        return payload


# ---------------------------------------------------------------------------
# Fetch wrappers
# ---------------------------------------------------------------------------

def fetch_previous_runs(fetcher: Fetcher, icao: str, lat: float, lon: float,
                        past_days: int = DEFAULT_PAST_DAYS,
                        leads: Sequence[int] = DEFAULT_LEAD_DAYS) -> dict:
    """Archived hourly forecasts at `lat`/`lon`, in Fahrenheit, local calendar.

    `temperature_unit=fahrenheit` is requested rather than converted, matching
    `StationForecastFeed`, and the response's `hourly_units` is checked by the
    caller: a silent Celsius response would be a 30-to-60F error in every
    residual.
    """
    variables = ['temperature_2m'] + [
        'temperature_2m_previous_day%d' % int(n) for n in leads]
    params = {
        'latitude': float(lat), 'longitude': float(lon),
        'hourly': ','.join(variables),
        'past_days': int(past_days), 'forecast_days': 1,
        'timezone': 'auto', 'temperature_unit': 'fahrenheit',
    }
    key = 'previous_runs_%s_%d_%s' % (icao, int(past_days),
                                      '-'.join(str(int(n)) for n in leads))
    payload = fetcher.json(key, PREVIOUS_RUNS_URL, params)
    if not isinstance(payload, dict) or 'hourly' not in payload:
        raise RuntimeError('previous-runs payload for %s has no hourly block: '
                           '%r' % (icao, str(payload)[:200]))
    return payload


def fetch_metar_history(fetcher: Fetcher, icao: str,
                        hours: int = DEFAULT_METAR_HOURS) -> List[dict]:
    """Raw METAR rows for one station, newest first, as the endpoint returns."""
    params = {'ids': icao, 'format': 'json', 'hours': int(hours)}
    key = 'metar_%s_%dh' % (icao, int(hours))
    payload = fetcher.json(key, METAR_URL, params)
    if not isinstance(payload, list):
        raise RuntimeError('METAR payload for %s is %s, not a list'
                           % (icao, type(payload).__name__))
    return [row for row in payload if isinstance(row, dict)]


def fetch_station_coordinates(fetcher: Fetcher, icaos: Sequence[str]
                              ) -> Dict[str, Tuple[float, float]]:
    """ICAO -> `(lat, lon)` straight off the METAR payload.

    The station tells us where it is. The alternative was a hand-maintained
    coordinate table for 50 stations, which is 50 assumptions to keep in sync,
    and `weather_arb` already made this call for the same reason.
    """
    out: Dict[str, Tuple[float, float]] = {}
    batch = [i for i in icaos if i]
    for start in range(0, len(batch), 20):
        chunk = batch[start:start + 20]
        params = {'ids': ','.join(chunk), 'format': 'json'}
        payload = fetcher.json('coords_' + '_'.join(chunk), METAR_URL, params)
        for row in payload if isinstance(payload, list) else []:
            if not isinstance(row, dict):
                continue
            icao = row.get('icaoId')
            lat = _finite(row.get('lat'))
            lon = _finite(row.get('lon'))
            if icao and lat is not None and lon is not None:
                out[str(icao)] = (lat, lon)
    return out


# ---------------------------------------------------------------------------
# Building one station's series
# ---------------------------------------------------------------------------

def build_station_series(icao: str, city: str, previous_runs: dict,
                         metar_rows: Sequence[dict],
                         leads: Sequence[int] = DEFAULT_LEAD_DAYS,
                         metrics: Sequence[str] = ('daily_high', 'daily_low')
                         ) -> StationSeries:
    """Slice one station's two payloads into everything the fit needs.

    Pure: no network, no clock, no disk. Every fetch happened before this call,
    which is what makes the whole fit testable against fixtures.
    """
    hourly = previous_runs.get('hourly') or {}
    units = previous_runs.get('hourly_units') or {}
    unit = str(units.get('temperature_2m', ''))
    if unit and 'F' not in unit:
        # Never converted silently. A Celsius response where we asked for
        # Fahrenheit means the request changed under us, and a 30-to-60F error
        # in every residual would look like a fitted sigma of 30F.
        raise RuntimeError('%s: previous-runs returned temperature in %r, '
                           'expected Fahrenheit' % (icao, unit))
    times = hourly.get('time') or []
    offset = int(_finite(previous_runs.get('utc_offset_seconds')) or 0)

    series = StationSeries(
        icao=icao, city=city,
        lat=float(_finite(previous_runs.get('latitude')) or 0.0),
        lon=float(_finite(previous_runs.get('longitude')) or 0.0),
        utc_offset_sec=offset,
        timezone_name=str(previous_runs.get('timezone') or ''))

    grid_by_day = hourly_by_local_day(times, hourly.get('temperature_2m') or [])
    grid_hours: Dict[str, float] = {}
    for day_map in grid_by_day.values():
        grid_hours.update(day_map)

    for metric in metrics:
        series.grid_realised[metric] = daily_extremes(grid_by_day, metric)
        series.forecast[metric] = {}
        for lead in leads:
            key = 'temperature_2m_previous_day%d' % int(lead)
            by_day = hourly_by_local_day(times, hourly.get(key) or [])
            series.forecast[metric][int(lead)] = daily_extremes(by_day, metric)

    station_days, station_hours = metar_local_series(metar_rows, offset)
    for metric in metrics:
        series.station_realised[metric] = station_daily_extremes(station_days,
                                                                 metric)
    for day in sorted({hour[:10] for hour in station_hours}):
        bias = station_minus_grid_bias(station_hours, grid_hours, day)
        if bias is not None:
            series.bias[day] = bias

    series.notes = {
        'grid_days': len(grid_by_day),
        'grid_days_with_full_coverage': len(series.grid_realised
                                            .get('daily_high', {})),
        'metar_rows': len(metar_rows),
        'metar_local_days': len(station_days),
        'metar_days_with_full_coverage': len(series.station_realised
                                             .get('daily_high', {})),
        'bias_days': len(series.bias),
        'timezone': series.timezone_name,
        'utc_offset_sec': offset,
    }
    return series


def calibrate_station(series: StationSeries, metric: str,
                      leads: Sequence[int] = DEFAULT_LEAD_DAYS,
                      min_samples: int = DEFAULT_MIN_SAMPLES,
                      sigma_basis: str = 'rmse') -> Dict[str, object]:
    """Per-lead residual stats plus the fitted `floor + b*sqrt(h)` curve.

    `fit_ok` is False when no lead bucket reached `min_samples`. That is a
    convention 11 CANNOT-RUN and the strategy treats it as one: an unfitted
    station is skipped, never priced with a house number wearing a fitted
    label.
    """
    by_lead: Dict[str, object] = {}
    grid_by_lead: Dict[str, object] = {}
    points: List[Tuple[float, float]] = []
    for lead in leads:
        stats = residual_stats(station_residuals(series, metric, lead))
        stats['lead_days'] = int(lead)
        stats['midpoint_hours'] = lead_midpoint_hours(lead)
        stats['used_in_fit'] = bool(stats['n'] >= int(min_samples)
                                    and stats.get(sigma_basis + '_f'))
        by_lead[str(int(lead))] = stats
        gstats = residual_stats(grid_residuals(series, metric, lead))
        gstats['lead_days'] = int(lead)
        gstats['midpoint_hours'] = lead_midpoint_hours(lead)
        grid_by_lead[str(int(lead))] = gstats
        if stats['used_in_fit']:
            points.append((stats['midpoint_hours'], stats[sigma_basis + '_f']))

    curve = fit_sqrt_curve(points)
    realised = list(series.station_realised.get(metric, {}).values())
    return {
        'metric': metric,
        'fit_ok': bool(curve['ok']),
        'unfit_reason': (None if curve['ok']
                         else 'no_lead_bucket_reached_min_samples'),
        'sigma_basis': sigma_basis,
        'min_samples': int(min_samples),
        'sigma_floor_f': curve['floor_f'],
        'sigma_per_sqrt_hour_f': curve['per_sqrt_hour_f'],
        'fit_r2': curve['r2'],
        'fit_points': curve['points'],
        'fit_clamped': curve['clamped'],
        'station_verified_by_lead': by_lead,
        'grid_verified_by_lead': grid_by_lead,
        # The number this harness refuses to use, kept next to the one it does,
        # so the size of the difference is a fact on the artifact.
        'climate_sigma_f': _round(climate_sigma(realised)),
        'climate_sigma_n': len(realised),
    }


# ---------------------------------------------------------------------------
# Station universe
# ---------------------------------------------------------------------------

def stations_from_db(db_path: str = DEFAULT_DB_PATH) -> Dict[str, dict]:
    """Stations this strategy has already priced, read off its own rows.

    Offline and reproducible, but it only ever contains stations that survived
    to the pricing stage at least once - 7 of the 50 on the live board when this
    was written. `--stations discovery` is the one that covers the board.
    """
    import sqlite3

    out: Dict[str, dict] = {}
    if not os.path.exists(db_path):
        return out
    conn = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True)
    try:
        rows = conn.execute(
            "select features_json from signals where strategy_id="
            "'PM_weather_arb'").fetchall()
    finally:
        conn.close()
    for (blob,) in rows:
        try:
            feats = json.loads(blob or '{}')
        except ValueError:
            continue
        icao = feats.get('rules_station') or feats.get('airport_station')
        lat = _finite(feats.get('station_lat'))
        lon = _finite(feats.get('station_lon'))
        if icao and lat is not None and lon is not None:
            out.setdefault(str(icao), {'city': feats.get('city_name') or '',
                                       'lat': lat, 'lon': lon, 'markets': 0})
    return out


def stations_from_discovery(client, fetcher: Fetcher) -> Tuple[Dict[str, dict],
                                                               Dict[str, int]]:
    """The LIVE board's station universe, plus a rung-width census.

    Runs the strategy's own `find_weather_markets` and its own
    `resolution_station_checked` / `parse_threshold_checked`, so the universe
    fitted here is by construction the universe traded, rather than a second
    list that drifts (convention 23).

    Returns `(stations, width_census)` where `width_census` counts rungs by
    their Fahrenheit width and uses the key `'unbounded'` for ladder tails.
    """
    from strategies.polymarket import weather_arb as wx

    found = wx.find_weather_markets(client)
    if not found.get('ok'):
        raise RuntimeError('Gamma discovery failed: %s' % found.get('reason'))
    stations: Dict[str, dict] = {}
    widths: Dict[str, int] = {}
    for market in found['markets']:
        icao, _status = wx.resolution_station_checked(market,
                                                      allow_fallback=False)
        if icao is None:
            continue
        threshold, _tstatus = wx.parse_threshold_checked(
            getattr(market, 'question', None))
        if threshold is None:
            continue
        if wx.market_metric(getattr(market, 'question', None)) is None:
            continue
        city = (wx.city_name_from_question(market)
                or wx.city_for_market(market) or '')
        entry = stations.setdefault(str(icao), {'city': city, 'lat': None,
                                                'lon': None, 'markets': 0})
        entry['markets'] += 1
        if threshold.lo_f is None or threshold.hi_f is None:
            key = 'unbounded'
        else:
            key = '%.1f' % round(threshold.hi_f - threshold.lo_f, 3)
        widths[key] = widths.get(key, 0) + 1

    coords = fetch_station_coordinates(fetcher, sorted(stations))
    for icao, entry in list(stations.items()):
        pair = coords.get(icao)
        if pair is None:
            # No coordinates means no forecast request can be built. Dropped
            # with a named count rather than carried with a None that would
            # fail four functions later.
            stations.pop(icao)
            continue
        entry['lat'], entry['lon'] = pair
    return stations, widths


# ---------------------------------------------------------------------------
# Rung tradeability
# ---------------------------------------------------------------------------

def rung_tradeability(width_census: Dict[str, int], sigma_f: float,
                      p_floor: float = ENTRY_P_FLOOR,
                      min_attainable: float = 0.5) -> Dict[str, object]:
    """How much of the real board a given sigma can have an opinion about.

    CONVENTION 27, AND IT IS THE POINT OF THIS FUNCTION. The gate under test is

        attainable < MIN_ATTAINABLE_P_YES  ->  refuse

    so the gate admits MORE rungs as sigma FALLS, not as the threshold falls.
    Nothing here changes a threshold; it recomputes the ceiling at the fitted
    sigma against the widths actually seen on the board and counts what clears.

    An unbounded tail has no ceiling at all, so it is counted separately and
    never pooled with the buckets: a tail that "clears" says nothing about
    whether fitting sigma unblocked anything.
    """
    out = {
        'sigma_f': _round(sigma_f), 'p_floor': p_floor,
        'min_attainable_p_yes': min_attainable,
        'by_width': {}, 'bounded_total': 0, 'bounded_clearing_floor': 0,
        'bounded_clearing_entry_p': 0, 'unbounded_total': 0,
    }
    for key, count in sorted(width_census.items()):
        count = int(count)
        if key == 'unbounded':
            out['unbounded_total'] += count
            out['by_width'][key] = {'count': count, 'max_attainable_p_yes':
                                    None, 'clears_min_attainable': True,
                                    'clears_entry_p': True,
                                    'sigma_needed_for_entry_p': None}
            continue
        width = _finite(key)
        if width is None:
            continue
        attainable = max_attainable_p_yes(width, sigma_f)
        clears_floor = attainable is not None and attainable >= min_attainable
        clears_entry = attainable is not None and attainable > p_floor
        out['bounded_total'] += count
        out['bounded_clearing_floor'] += count if clears_floor else 0
        out['bounded_clearing_entry_p'] += count if clears_entry else 0
        out['by_width'][key] = {
            'count': count,
            'max_attainable_p_yes': _round(attainable, 6),
            'clears_min_attainable': bool(clears_floor),
            'clears_entry_p': bool(clears_entry),
            'sigma_needed_for_entry_p': _round(
                sigma_needed_for(width, p_floor)),
        }
    return out


# ---------------------------------------------------------------------------
# The artifact
# ---------------------------------------------------------------------------

def build_calibration(series_by_station: Dict[str, StationSeries],
                      leads: Sequence[int] = DEFAULT_LEAD_DAYS,
                      metrics: Sequence[str] = ('daily_high', 'daily_low'),
                      min_samples: int = DEFAULT_MIN_SAMPLES,
                      sigma_basis: str = 'rmse',
                      width_census: Optional[Dict[str, int]] = None,
                      now: Optional[float] = None) -> dict:
    """Assemble everything into the artifact the strategy reads."""
    now = time.time() if now is None else float(now)
    stations: Dict[str, object] = {}
    pooled_residuals: Dict[str, Dict[int, List[float]]] = {
        m: {int(l): [] for l in leads} for m in metrics}

    for icao, series in sorted(series_by_station.items()):
        entry = {'city': series.city, 'lat': _round(series.lat, 5),
                 'lon': _round(series.lon, 5),
                 'timezone': series.timezone_name,
                 'utc_offset_sec': series.utc_offset_sec,
                 'coverage': series.notes, 'metrics': {}}
        for metric in metrics:
            entry['metrics'][metric] = calibrate_station(
                series, metric, leads=leads, min_samples=min_samples,
                sigma_basis=sigma_basis)
            for lead in leads:
                pooled_residuals[metric][int(lead)].extend(
                    station_residuals(series, metric, lead))
        stations[icao] = entry

    pooled: Dict[str, object] = {}
    for metric in metrics:
        by_lead: Dict[str, object] = {}
        points: List[Tuple[float, float]] = []
        for lead in leads:
            stats = residual_stats(pooled_residuals[metric][int(lead)])
            stats['lead_days'] = int(lead)
            stats['midpoint_hours'] = lead_midpoint_hours(lead)
            by_lead[str(int(lead))] = stats
            value = stats.get(sigma_basis + '_f')
            if stats['n'] >= min_samples and value:
                points.append((stats['midpoint_hours'], value))
        curve = fit_sqrt_curve(points)
        pooled[metric] = {
            'by_lead': by_lead, 'sigma_floor_f': curve['floor_f'],
            'sigma_per_sqrt_hour_f': curve['per_sqrt_hour_f'],
            'fit_r2': curve['r2'], 'fit_clamped': curve['clamped'],
            'fit_ok': curve['ok'],
        }

    payload: Dict[str, object] = {
        'schema_version': SCHEMA_VERSION,
        'harness': 'backtest/measure_daily_extreme_calibration.py',
        'generated_ts': int(now),
        'generated_utc': datetime.fromtimestamp(
            now, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'quantity': 'forecast_error_of_the_live_predictor_not_climate_spread',
        'predictor': ('open_meteo_previous_run_daily_extreme_at_station_coords'
                      '_plus_station_minus_grid_bias_from_the_previous_day'),
        'verification_source': 'aviationweather_metar_station_observations',
        'sigma_basis': sigma_basis,
        'sigma_form': 'sigma_f = sigma_floor_f + sigma_per_sqrt_hour_f*sqrt(h)',
        'lead_days': [int(l) for l in leads],
        'lead_midpoint_hours': {str(int(l)): lead_midpoint_hours(l)
                                for l in leads},
        'min_samples': int(min_samples),
        'known_gaps': [
            'sub_24h_leads_are_not_in_the_archive_so_sigma_below_24h_is_an_'
            'extrapolation_of_the_fitted_curve',
            'metar_history_is_capped_near_400_rows_so_per_station_n_is_8_to_16'
            '_station_days_and_convention_7_applies',
            'the_bias_term_is_reconstructed_from_the_previous_local_day_not_'
            'from_the_decision_instant',
            'no_weather_position_has_ever_resolved_so_this_is_a_calibration_'
            'of_the_predictor_not_a_measured_win_rate',
        ],
        'stations': stations,
        'pooled': pooled,
    }
    if width_census:
        payload['rung_width_census'] = dict(width_census)
        table = {}
        for metric in metrics:
            floor = pooled[metric].get('sigma_floor_f')
            slope = pooled[metric].get('sigma_per_sqrt_hour_f')
            if floor is None or slope is None:
                continue
            for hours in (36.0, 30.0, 24.0, 12.0, 2.0):
                table['%s@%gh' % (metric, hours)] = rung_tradeability(
                    width_census, sigma_at(floor, slope, hours))
        payload['rung_tradeability_at_pooled_sigma'] = table
    return payload


def write_calibration(path: str, payload: dict) -> str:
    """Write the artifact. `allow_nan=False`, convention 19.

    A NaN sigma would be written by `json.dump` as the bare token `NaN`, read
    back by `json.loads` without complaint, and would then price a rung at a
    probability no downstream reader could interpret. Refusing at write time is
    the only place this can be caught cheaply.
    """
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    text = json.dumps(payload, indent=1, sort_keys=True, allow_nan=False)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        handle.write(text + '\n')
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--stations', choices=('discovery', 'db', 'file'),
                        default='discovery',
                        help='where the station universe comes from')
    parser.add_argument('--stations-file', default=None,
                        help='JSON {icao: {city, lat, lon}} for --stations file')
    parser.add_argument('--db-path', default=DEFAULT_DB_PATH)
    parser.add_argument('--cache-dir', default=DEFAULT_CACHE_DIR)
    parser.add_argument('--cache-ttl-sec', type=float,
                        default=DEFAULT_CACHE_TTL_SEC)
    parser.add_argument('--out', default=DEFAULT_OUT_PATH)
    parser.add_argument('--past-days', type=int, default=DEFAULT_PAST_DAYS)
    parser.add_argument('--metar-hours', type=int, default=DEFAULT_METAR_HOURS)
    parser.add_argument('--leads', default=','.join(
        str(n) for n in DEFAULT_LEAD_DAYS))
    parser.add_argument('--min-samples', type=int, default=DEFAULT_MIN_SAMPLES)
    parser.add_argument('--sigma-basis', choices=('rmse', 'sd'), default='rmse')
    parser.add_argument('--offline', action='store_true',
                        help='never send a request; fail if the cache cannot '
                             'answer')
    parser.add_argument('--limit-stations', type=int, default=0,
                        help='0 = all')
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    leads = tuple(int(x) for x in str(args.leads).split(',') if x.strip())
    cache = JsonCache(args.cache_dir, ttl_sec=args.cache_ttl_sec)
    fetcher = Fetcher(cache=cache, offline=bool(args.offline))

    width_census: Dict[str, int] = {}
    if args.stations == 'db':
        stations = stations_from_db(args.db_path)
    elif args.stations == 'file':
        with open(args.stations_file, 'r', encoding='utf-8') as handle:
            blob = json.load(handle)
        stations = blob.get('stations', blob)
        width_census = blob.get('widths', {}) or {}
    else:
        from engine.polymarket.client import PolymarketClient
        with PolymarketClient() as client:
            stations, width_census = stations_from_discovery(client, fetcher)
        # Persisted so a later `--stations file --offline` run reproduces this
        # exact universe rather than whatever Gamma lists that day.
        cache.put('stations', {'stations': stations, 'widths': width_census,
                               'measured_ts': int(time.time())})

    ordered = sorted(stations.items(),
                     key=lambda kv: (-int(kv[1].get('markets') or 0), kv[0]))
    if args.limit_stations:
        ordered = ordered[:args.limit_stations]
    print('stations: %d  (%s)' % (len(ordered), args.stations))
    if width_census:
        print('rung width census: %s' % width_census)

    series_by_station: Dict[str, StationSeries] = {}
    failures: Dict[str, str] = {}
    for icao, meta in ordered:
        try:
            runs = fetch_previous_runs(fetcher, icao, meta['lat'], meta['lon'],
                                       past_days=args.past_days, leads=leads)
            metar = fetch_metar_history(fetcher, icao, hours=args.metar_hours)
            series_by_station[icao] = build_station_series(
                icao, meta.get('city') or '', runs, metar, leads=leads)
        except Exception as exc:                           # noqa: BLE001
            # Named, counted, and never silently dropped: a station missing
            # from the artifact is a station the strategy will refuse to price,
            # and the operator has to be able to see why.
            failures[icao] = '%s: %s' % (type(exc).__name__, exc)
            print('  FAILED %s: %s' % (icao, failures[icao]))

    payload = build_calibration(series_by_station, leads=leads,
                                min_samples=args.min_samples,
                                sigma_basis=args.sigma_basis,
                                width_census=width_census)
    payload['fetch_failures'] = failures
    payload['requests_sent'] = fetcher.requests_sent
    payload['cache'] = {'hits': cache.hits, 'misses': cache.misses,
                        'writes': cache.writes, 'dir': args.cache_dir}
    write_calibration(args.out, payload)

    print('\nwrote %s' % args.out)
    for metric in ('daily_high', 'daily_low'):
        pooled = payload['pooled'][metric]
        print('\n== pooled %s: sigma = %s + %s*sqrt(h)  (r2=%s)'
              % (metric, pooled['sigma_floor_f'],
                 pooled['sigma_per_sqrt_hour_f'], pooled['fit_r2']))
        for lead, stats in sorted(pooled['by_lead'].items()):
            print('   lead %s (%.0fh): n=%-4d mean=%-7s sd=%-7s rmse=%s'
                  % (lead, stats['midpoint_hours'], stats['n'],
                     stats['mean_f'], stats['sd_f'], stats['rmse_f']))
    print('\n== per-station daily_high, lead 1 (24-48h), the live regime')
    print('   %-6s %-16s %5s %8s %8s %8s %8s' % (
        'icao', 'city', 'n', 'mean', 'sd', 'rmse', 'climate'))
    for icao, entry in sorted(payload['stations'].items()):
        cell = entry['metrics']['daily_high']
        lead1 = cell['station_verified_by_lead'].get('1', {})
        print('   %-6s %-16s %5s %8s %8s %8s %8s'
              % (icao, (entry['city'] or '')[:16], lead1.get('n'),
                 lead1.get('mean_f'), lead1.get('sd_f'), lead1.get('rmse_f'),
                 cell.get('climate_sigma_f')))
    table = payload.get('rung_tradeability_at_pooled_sigma') or {}
    if table:
        print('\n== rung tradeability at the pooled daily_high sigma')
        for key in sorted(table):
            if not key.startswith('daily_high'):
                continue
            row = table[key]
            print('   %-18s sigma=%-6s bounded %d/%d clear 0.50, %d/%d clear '
                  '%.2f  (+%d unbounded tails)'
                  % (key, row['sigma_f'], row['bounded_clearing_floor'],
                     row['bounded_total'], row['bounded_clearing_entry_p'],
                     row['bounded_total'], row['p_floor'],
                     row['unbounded_total']))
    return 0


if __name__ == '__main__':                                 # pragma: no cover
    raise SystemExit(main())
