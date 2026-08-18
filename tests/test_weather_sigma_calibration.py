"""Tests for the fitted daily-extreme sigma: the fit, the wiring, the gates.

WHAT THIS FILE IS DEFENDING, in order of how expensive the mistake would be:

  1. THE HARNESS FITS FORECAST ERROR, NOT CLIMATE SPREAD. The instruction that
     commissioned it asked for "the standard deviation of daily extremes", which
     is a completely different quantity - it is how much a city's afternoon peak
     wanders, not how wrong our predictor is. `test_climate_sigma_and_forecast_
     error_are_different_numbers` builds a series where the two differ by a
     factor of five and pins which one the harness reports.
  2. THE STRATEGY ACTUALLY READS THE ARTIFACT. Convention 22: a docstring
     saying "the sigma is fitted" is not a wiring test. There is one here that
     changes a number in an injected artifact and asserts the number that comes
     out of `evaluate` changes with it.
  3. AN UNFITTED STATION IS A CANNOT-RUN. Convention 11 and convention 20: it
     gets its OWN skip reason, it never shares one with "we have a sigma and it
     is too wide", and it never silently falls back to a house constant.
  4. NO NETWORK. Every fetch in this file goes through an injected callable
     that fails the test if it is called at all where it should not be.
"""
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import measure_daily_extreme_calibration as cal   # noqa: E402
from strategies.polymarket import weather_arb as wx             # noqa: E402


# ===========================================================================
# fixtures: a synthetic station whose numbers are known by construction
# ===========================================================================

def _hourly_day(date, values):
    """`(times, values)` for one local day of hourly readings."""
    times = ['%sT%02d:00' % (date, h) for h in range(len(values))]
    return times, list(values)


def _flat_day(peak, n=24):
    """A crude diurnal shape peaking at `peak` and troughing 20F below."""
    return [peak - 20.0 + 20.0 * math.sin(math.pi * h / (n - 1))
            for h in range(n)]


def _previous_runs_payload(dates, realised_peaks, forecast_peaks_by_lead,
                           utc_offset=0):
    """A previous-runs response shaped exactly like open-meteo's."""
    times = []
    realised = []
    per_lead = {lead: [] for lead in forecast_peaks_by_lead}
    for i, date in enumerate(dates):
        day_times, day_vals = _hourly_day(date, _flat_day(realised_peaks[i]))
        times.extend(day_times)
        realised.extend(day_vals)
        for lead, peaks in forecast_peaks_by_lead.items():
            per_lead[lead].extend(_flat_day(peaks[i]))
    hourly = {'time': times, 'temperature_2m': realised}
    for lead, values in per_lead.items():
        hourly['temperature_2m_previous_day%d' % lead] = values
    return {'latitude': 40.0, 'longitude': -3.0, 'utc_offset_seconds':
            utc_offset, 'timezone': 'UTC',
            'hourly_units': {'temperature_2m': '°F'}, 'hourly': hourly}


def _metar_rows(dates, station_peaks, per_day=24, utc_offset=0):
    """METAR rows in CELSIUS, as aviationweather.gov serves them."""
    rows = []
    for date, peak in zip(dates, station_peaks):
        for hour, value_f in enumerate(_flat_day(peak, per_day)):
            rows.append({'icaoId': 'TEST',
                         'reportTime': '%sT%02d:00:00Z' % (date, hour),
                         'temp': (value_f - 32.0) * 5.0 / 9.0,
                         'lat': 40.0, 'lon': -3.0})
    return rows


DATES = ['2026-07-%02d' % d for d in range(1, 21)]


# ===========================================================================
# 1. THE QUANTITY: forecast error, never climate spread
# ===========================================================================

def test_climate_sigma_and_forecast_error_are_different_numbers():
    """The whole reason this harness exists.

    The realised peaks swing over 20F across the window (climate spread ~7F).
    The forecast tracks them with a constant 1F error, so the FORECAST ERROR is
    zero spread and 1F of bias. Any implementation that reported the climate
    number would come back near 7F.
    """
    realised = [70.0 + 10.0 * math.sin(i) for i in range(len(DATES))]
    forecast = [value - 1.0 for value in realised]
    payload = _previous_runs_payload(DATES, realised, {1: forecast})
    series = cal.build_station_series('TEST', 'test', payload,
                                      _metar_rows(DATES, realised), leads=(1,))

    residuals = cal.grid_residuals(series, 'daily_high', 1)
    stats = cal.residual_stats(residuals)
    assert stats['n'] == len(DATES)
    assert stats['mean_f'] == pytest.approx(1.0, abs=1e-6)
    assert stats['sd_f'] == pytest.approx(0.0, abs=1e-6)

    climate = cal.climate_sigma(list(series.grid_realised['daily_high']
                                     .values()))
    assert climate > 5.0
    # Five times apart at least. Substituting one for the other is not a
    # rounding difference.
    assert climate > 5.0 * max(stats['rmse_f'], 1e-9) / 5.0
    assert climate > stats['rmse_f'] * 5.0


def test_rmse_is_taken_about_zero_not_about_the_sample_mean():
    """The strategy does NOT subtract a residual mean, so the spread of its
    predictive distribution around the mean it uses includes the bias."""
    stats = cal.residual_stats([3.0, 3.0, 3.0, 3.0])
    assert stats['sd_f'] == pytest.approx(0.0)
    assert stats['rmse_f'] == pytest.approx(3.0)
    assert stats['mean_f'] == pytest.approx(3.0)


def test_a_single_residual_has_no_spread_and_says_so():
    """A 0.0 there would read downstream as a perfect forecast."""
    stats = cal.residual_stats([2.0])
    assert stats['n'] == 1 and stats['sd_f'] is None
    assert stats['rmse_f'] == pytest.approx(2.0)


def test_no_residuals_is_a_cannot_run_not_a_zero():
    stats = cal.residual_stats([])
    assert stats == {'n': 0, 'mean_f': None, 'sd_f': None, 'rmse_f': None,
                     'min_f': None, 'max_f': None}


def test_nan_never_reaches_the_statistics():
    """Convention 19. `float('nan')` is a float and every comparison with it is
    False, so one would poison a mean silently."""
    stats = cal.residual_stats([1.0, float('nan'), -1.0])
    assert stats['n'] == 2
    assert stats['mean_f'] == pytest.approx(0.0)


# ===========================================================================
# 2. THE PREDICTOR IS RECONSTRUCTED, INCLUDING THE BIAS TERM
# ===========================================================================

def test_the_station_residual_subtracts_the_previous_days_bias():
    """The live path centres on `forecast + station_minus_grid_bias`. The
    reconstruction has to do the same or it is measuring a different model.

    Here the station reads exactly 4F above the grid at every hour, and the
    grid forecast is exact. A correct reconstruction returns ~0 residual; one
    that forgot the bias returns ~4.
    """
    realised = [80.0] * len(DATES)
    payload = _previous_runs_payload(DATES, realised, {1: realised})
    station = [value + 4.0 for value in realised]
    series = cal.build_station_series('TEST', 'test', payload,
                                      _metar_rows(DATES, station), leads=(1,))
    assert series.bias, 'no bias day was reconstructed at all'
    for value in series.bias.values():
        assert value == pytest.approx(4.0, abs=1e-6)
    residuals = cal.station_residuals(series, 'daily_high', 1)
    assert residuals
    for value in residuals:
        assert value == pytest.approx(0.0, abs=1e-6)


def test_the_bias_comes_from_the_day_before_never_the_day_itself():
    """A bias read from the target day carries information from after the
    decision. The first day of the window therefore has no residual at all."""
    realised = [80.0] * len(DATES)
    payload = _previous_runs_payload(DATES, realised, {1: realised})
    series = cal.build_station_series('TEST', 'test', payload,
                                      _metar_rows(DATES, realised), leads=(1,))
    assert len(cal.station_residuals(series, 'daily_high', 1)) == len(DATES) - 1


def test_a_day_without_enough_coverage_is_dropped_not_extremed_over():
    """A max over four hours of a 20F diurnal swing is wrong by more than the
    quantity being measured."""
    by_day = {'2026-07-01': {'2026-07-01T%02d:00' % h: 70.0 + h
                             for h in range(4)},
              '2026-07-02': {'2026-07-02T%02d:00' % h: 70.0 + h
                             for h in range(24)}}
    kept = cal.daily_extremes(by_day, 'daily_high')
    assert list(kept) == ['2026-07-02']


def test_daily_low_takes_the_minimum():
    by_day = {'d': {'d%02d' % h: 70.0 + h for h in range(24)}}
    assert cal.daily_extremes(by_day, 'daily_low')['d'] == 70.0
    assert cal.daily_extremes(by_day, 'daily_high')['d'] == 93.0


def test_metar_temperatures_are_converted_from_celsius():
    """A Fahrenheit number compared against a Celsius one is a 30-to-60 degree
    error that looks exactly like a screaming edge."""
    by_day, _by_hour = cal.metar_local_series(
        [{'reportTime': '2026-07-01T12:00:00Z', 'temp': 30.0}], 0)
    assert by_day['2026-07-01'] == [pytest.approx(86.0)]


def test_the_local_day_follows_the_stations_utc_offset():
    """A market resolves on the STATION's calendar day. Slicing a UTC day mixes
    two of them at every station more than a couple of hours off Greenwich."""
    rows = [{'reportTime': '2026-07-01T23:00:00Z', 'temp': 10.0}]
    same_day, _ = cal.metar_local_series(rows, 0)
    next_day, _ = cal.metar_local_series(rows, 7200)     # UTC+2
    assert list(same_day) == ['2026-07-01']
    assert list(next_day) == ['2026-07-02']


def test_a_celsius_response_is_refused_rather_than_converted():
    """We ask for Fahrenheit. A Celsius response means the request changed
    under us, and silently converting would hide that."""
    payload = _previous_runs_payload(DATES, [80.0] * len(DATES),
                                     {1: [80.0] * len(DATES)})
    payload['hourly_units']['temperature_2m'] = '°C'
    with pytest.raises(RuntimeError, match='expected Fahrenheit'):
        cal.build_station_series('TEST', 'test', payload, [], leads=(1,))


# ===========================================================================
# 3. THE CURVE FIT
# ===========================================================================

def test_the_curve_recovers_coefficients_it_was_built_from():
    points = [(h, 1.0 + 0.5 * math.sqrt(h)) for h in (36.0, 60.0, 84.0, 108.0)]
    fit = cal.fit_sqrt_curve(points)
    assert fit['ok'] is True
    assert fit['floor_f'] == pytest.approx(1.0, abs=1e-6)
    assert fit['per_sqrt_hour_f'] == pytest.approx(0.5, abs=1e-6)
    assert fit['r2'] == pytest.approx(1.0, abs=1e-6)
    assert fit['clamped'] == []


def test_a_negative_slope_is_refused_and_the_curve_goes_flat():
    """A sigma that SHRINKS with the horizon is four noisy points, not a
    weather fact, and publishing it would hand the strategy a model that gets
    more confident the further out it looks."""
    points = [(36.0, 3.0), (60.0, 2.0), (84.0, 1.0)]
    fit = cal.fit_sqrt_curve(points)
    assert 'negative_slope' in fit['clamped']
    assert fit['per_sqrt_hour_f'] == pytest.approx(0.0)
    assert fit['floor_f'] == pytest.approx(2.0)


def test_a_floor_below_the_minimum_is_refitted_not_translated_upward():
    """MEASURED: translating instead of refitting turned San Francisco's
    5.70F lead-1 residual into an 11.0F published sigma. A pinned-intercept
    refit keeps the curve next to the data it came from."""
    points = [(36.0, 3.0), (108.0, 9.0)]
    fit = cal.fit_sqrt_curve(points)
    assert 'floor_below_min' in fit['clamped']
    assert fit['floor_f'] == pytest.approx(cal.MIN_FITTED_SIGMA_F)
    refitted = cal.sigma_at(fit['floor_f'], fit['per_sqrt_hour_f'], 36.0)

    # What the old behaviour would have published: the unconstrained slope with
    # the intercept simply raised to the floor.
    unconstrained_slope = (9.0 - 3.0) / (math.sqrt(108.0) - math.sqrt(36.0))
    translated = cal.sigma_at(cal.MIN_FITTED_SIGMA_F, unconstrained_slope, 36.0)
    assert translated > refitted + 2.0
    assert abs(refitted - 3.0) < abs(translated - 3.0)


def test_one_point_gives_a_flat_curve_and_says_so():
    fit = cal.fit_sqrt_curve([(36.0, 2.5)])
    assert fit['per_sqrt_hour_f'] == pytest.approx(0.0)
    assert fit['floor_f'] == pytest.approx(2.5)
    assert 'flat_single_point' in fit['clamped']


def test_zero_points_never_invents_a_curve():
    fit = cal.fit_sqrt_curve([])
    assert fit['ok'] is False and fit['floor_f'] is None


def test_the_two_sigma_formulas_agree():
    """Convention 23: the same formula lives in the harness and in the
    strategy, so a test has to hold them equal."""
    for hours in (0.0, 2.0, 24.0, 31.5, 36.0, 108.0):
        assert cal.sigma_at(1.0, 0.35, hours) == pytest.approx(
            wx.daily_extreme_sigma_f(hours, 1.0, 0.35))


# ===========================================================================
# 4. THE CEILING ARITHMETIC AND THE 0.55 FLOOR
# ===========================================================================

@pytest.mark.parametrize('width,sigma,expected', [
    # Hand-computed from `2 * Phi(w / (2 * sigma)) - 1`.
    (1.8, 2.96, 0.2394),        # the Madrid row that started all of this
    (1.8, 1.334, 0.5000),       # the sigma a Celsius bucket needs for 0.50
    (1.8, 1.191, 0.5500),       # ... and for the 0.55 entry floor
    (2.0, 1.324, 0.5500),
    (1.0, 1.0, 0.3829),         # a whole-degree F bucket, never priceable
])
def test_max_attainable_p_yes_against_hand_computed_values(width, sigma,
                                                           expected):
    assert cal.max_attainable_p_yes(width, sigma) == pytest.approx(expected,
                                                                  abs=5e-4)


def test_the_harness_and_the_strategy_compute_the_same_ceiling():
    """Convention 23 again. The strategy takes a `Threshold`, the harness takes
    a width, and the two must not drift."""
    rung = wx.parse_threshold('Will the highest temperature in Madrid be '
                              '36°C on August 19?')
    width = rung.hi_f - rung.lo_f
    for sigma in (1.0, 1.334, 2.74, 2.96):
        assert cal.max_attainable_p_yes(width, sigma) == pytest.approx(
            wx.max_attainable_p_yes(rung, sigma))


def test_an_unbounded_tail_has_no_ceiling_in_either_implementation():
    tail = wx.parse_threshold('Will the highest temperature in NYC be '
                              '85°F or below on August 18?')
    assert wx.max_attainable_p_yes(tail, 2.74) is None
    assert cal.max_attainable_p_yes(None, 2.74) is None


def test_sigma_needed_for_inverts_the_ceiling():
    for width in (1.0, 1.8, 2.0, 5.0):
        for target in (0.5, 0.55, 0.8):
            sigma = cal.sigma_needed_for(width, target)
            assert cal.max_attainable_p_yes(width, sigma) == pytest.approx(
                target, abs=1e-9)


def test_the_measured_answer_a_celsius_bucket_cannot_reach_the_entry_floor():
    """THE RESULT. The fitted pooled sigma at the live 24-48h lead is 2.74F.
    A 1.8F Celsius bucket needs 1.191F. Stated as a test so nobody has to take
    the handoff's word for it."""
    assert cal.sigma_needed_for(1.8, 0.55) == pytest.approx(1.191, abs=0.002)
    assert cal.sigma_needed_for(1.8, 0.50) == pytest.approx(1.334, abs=0.002)
    assert cal.max_attainable_p_yes(1.8, 2.74) < wx.MIN_ATTAINABLE_P_YES


def test_the_entry_floor_is_the_same_number_in_both_files():
    assert cal.ENTRY_P_FLOOR == wx.MIN_MODEL_P_SIDE == 0.55


def test_rung_tradeability_counts_tails_apart_from_buckets():
    """A tail that "clears" says nothing about whether fitting sigma unblocked
    anything, so it must never be pooled into the bounded count."""
    census = {'1.8': 729, '2.0': 117, 'unbounded': 188}
    wide = cal.rung_tradeability(census, sigma_f=2.74)
    assert wide['bounded_total'] == 846
    assert wide['bounded_clearing_entry_p'] == 0
    assert wide['unbounded_total'] == 188
    narrow = cal.rung_tradeability(census, sigma_f=0.8)
    assert narrow['bounded_clearing_entry_p'] == 846


def test_the_gate_admits_more_rungs_as_sigma_falls_not_as_it_rises():
    """CONVENTION 27, checked rather than asserted in a comment. The gate is
    `attainable < MIN_ATTAINABLE_P_YES -> refuse`, so a WIDER sigma refuses
    MORE. Anyone reaching for this threshold should see the direction first."""
    census = {'1.8': 100}
    counts = [cal.rung_tradeability(census, sigma_f=s)['bounded_clearing_floor']
              for s in (0.5, 1.0, 1.334, 2.0, 4.0)]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 100 and counts[-1] == 0


# ===========================================================================
# 5. THE CACHE, AND NO NETWORK
# ===========================================================================

def _cache(tmp_path, ttl=3600.0, clock=None):
    return cal.JsonCache(str(tmp_path / 'cache'), ttl_sec=ttl, clock=clock)


def test_a_second_fetch_is_served_from_cache_and_sends_no_request(tmp_path):
    calls = []

    def http(url, params):
        calls.append((url, params))
        return {'hourly': {'time': [], 'temperature_2m': []}}

    fetcher = cal.Fetcher(cache=_cache(tmp_path), http=http)
    first = fetcher.json('k', 'http://example', {'a': 1})
    second = fetcher.json('k', 'http://example', {'a': 1})
    assert first == second
    assert len(calls) == 1
    assert fetcher.requests_sent == 1


def test_an_expired_entry_is_refetched(tmp_path):
    import time as _time
    # The age is measured against the FILE's mtime, which is real wall clock,
    # so the injected clock has to advance from real time rather than from an
    # arbitrary epoch.
    offset = [0.0]
    now = lambda: _time.time() + offset[0]                  # noqa: E731
    calls = []

    def http(url, params):
        calls.append(1)
        return {'n': len(calls)}

    fetcher = cal.Fetcher(cache=_cache(tmp_path, ttl=10.0, clock=now),
                          http=http)
    fetcher.json('k', 'u', {})
    offset[0] += 1000.0
    fetcher.json('k', 'u', {})
    assert len(calls) == 2


def test_a_truncated_cache_file_is_a_miss_not_a_crash(tmp_path):
    cache = _cache(tmp_path)
    cache.put('k', {'a': 1})
    with open(cache.path_for('k'), 'w', encoding='utf-8') as handle:
        handle.write('{not json')
    assert cache.get('k') is None


def test_offline_never_sends_a_request_and_refuses_loudly(tmp_path):
    def http(url, params):                       # pragma: no cover - must not run
        raise AssertionError('offline fetcher sent a request')

    fetcher = cal.Fetcher(cache=_cache(tmp_path), http=http, offline=True)
    with pytest.raises(RuntimeError, match='offline and no cache entry'):
        fetcher.json('missing', 'u', {})


def test_offline_accepts_an_entry_past_its_ttl(tmp_path):
    """A stale cache is the whole point of `--offline`: refitting yesterday's
    bytes must not silently become a fresh fetch."""
    now = [1000.0]

    def http(url, params):                       # pragma: no cover - must not run
        raise AssertionError('offline fetcher sent a request')

    cache = _cache(tmp_path, ttl=10.0, clock=lambda: now[0])
    cache.put('k', {'a': 1})
    now[0] += 10_000.0
    fetcher = cal.Fetcher(cache=cache, http=http, offline=True)
    assert fetcher.json('k', 'u', {}) == {'a': 1}


# ===========================================================================
# 6. THE ARTIFACT
# ===========================================================================

def _series(realised=None, station=None, forecasts=None, leads=(1, 2)):
    realised = realised or [80.0] * len(DATES)
    station = station or realised
    forecasts = forecasts or {lead: list(realised) for lead in leads}
    payload = _previous_runs_payload(DATES, realised, forecasts)
    return cal.build_station_series('TEST', 'test', payload,
                                    _metar_rows(DATES, station), leads=leads)


def test_the_artifact_refuses_to_write_a_nan(tmp_path):
    """Convention 19. `json.dump` writes a bare `NaN` token and `json.loads`
    reads it back without complaint, so this is the only cheap place to catch
    it."""
    path = str(tmp_path / 'out.json')
    with pytest.raises(ValueError):
        cal.write_calibration(path, {'sigma': float('nan')})


def test_the_artifact_round_trips_and_carries_its_provenance(tmp_path):
    payload = cal.build_calibration({'TEST': _series()}, leads=(1, 2),
                                    metrics=('daily_high',), now=1_000_000.0)
    path = cal.write_calibration(str(tmp_path / 'out.json'), payload)
    with open(path, encoding='utf-8') as handle:
        back = json.load(handle)
    assert back['schema_version'] == cal.SCHEMA_VERSION
    assert back['harness'] == 'backtest/measure_daily_extreme_calibration.py'
    assert back['verification_source'] == (
        'aviationweather_metar_station_observations')
    assert back['quantity'].startswith('forecast_error')
    # The gaps are on the artifact, not only in a docstring somebody has to go
    # and find.
    assert any('sub_24h' in gap for gap in back['known_gaps'])
    assert any('metar_history_is_capped' in gap for gap in back['known_gaps'])
    assert 'TEST' in back['stations']


def test_a_station_with_too_few_samples_is_not_fitted(tmp_path):
    series = _series()
    cell = cal.calibrate_station(series, 'daily_high', leads=(1, 2),
                                 min_samples=10_000)
    assert cell['fit_ok'] is False
    assert cell['unfit_reason'] == 'no_lead_bucket_reached_min_samples'
    assert cell['sigma_floor_f'] is None


def test_the_climate_number_is_reported_next_to_the_fitted_one():
    """Both on the artifact so the size of the difference is a fact rather
    than an argument."""
    realised = [70.0 + 10.0 * math.sin(i) for i in range(len(DATES))]
    series = _series(realised=realised,
                     forecasts={1: [v - 1.0 for v in realised]}, leads=(1,))
    cell = cal.calibrate_station(series, 'daily_high', leads=(1,))
    assert cell['climate_sigma_f'] > 5.0
    lead1 = cell['station_verified_by_lead']['1']
    assert lead1['rmse_f'] < 2.0


# ===========================================================================
# 7. WIRING: the strategy READS the artifact (convention 22)
# ===========================================================================

def _calibration(icao='KNYC', metric='daily_high', rmse=2.0, n=12,
                 floor=1.0, slope=0.35, min_samples=6, leads=(0, 1)):
    return {
        'schema_version': cal.SCHEMA_VERSION,
        'generated_utc': '2026-08-18T00:00:00Z',
        'stations': {icao: {'city': 'test', 'metrics': {metric: {
            'metric': metric, 'fit_ok': True, 'sigma_basis': 'rmse',
            'min_samples': min_samples,
            'sigma_floor_f': floor, 'sigma_per_sqrt_hour_f': slope,
            'station_verified_by_lead': {
                str(lead): {'n': n, 'rmse_f': rmse, 'sd_f': rmse,
                            'mean_f': 0.0, 'lead_days': lead}
                for lead in leads},
        }}}},
    }


def test_the_fitted_bucket_wins_over_the_fitted_curve():
    """Inside a measured lead bucket the strategy uses the MEASUREMENT, not the
    curve's value at that hour. The curve is a compromise across 36 to 108
    hours and reads high inside the bucket it was partly fitted on."""
    calibration = _calibration(rmse=2.0, floor=1.0, slope=0.35)
    sigma, feats = wx.fitted_daily_extreme_sigma(calibration, 'KNYC',
                                                 'daily_high', 31.5)
    assert sigma == pytest.approx(2.0)
    assert feats['daily_extreme_sigma_source'] == 'fitted_lead_bucket'
    assert feats['daily_extreme_sigma_lead_days'] == 1
    assert feats['sigma_horizon_is_extrapolated'] is False
    assert feats['daily_extreme_sigma_n'] == 12


def test_outside_a_measured_bucket_the_curve_is_used_and_flagged():
    """Lead 5 is not in the artifact, so the curve answers and the row says the
    horizon was extrapolated."""
    calibration = _calibration(floor=1.0, slope=0.35)
    sigma, feats = wx.fitted_daily_extreme_sigma(calibration, 'KNYC',
                                                 'daily_high', 130.0)
    assert sigma == pytest.approx(1.0 + 0.35 * math.sqrt(130.0))
    assert feats['sigma_horizon_is_extrapolated'] is True
    assert feats['daily_extreme_sigma_source'] == (
        'fitted_curve_outside_measured_bucket')


def test_a_bucket_with_too_few_samples_falls_through_to_the_curve():
    calibration = _calibration(n=2, min_samples=6, floor=1.0, slope=0.35)
    sigma, feats = wx.fitted_daily_extreme_sigma(calibration, 'KNYC',
                                                 'daily_high', 31.5)
    assert feats['daily_extreme_sigma_source'] == (
        'fitted_curve_outside_measured_bucket')
    assert sigma == pytest.approx(1.0 + 0.35 * math.sqrt(31.5))


@pytest.mark.parametrize('calibration,expected', [
    (None, 'calibration_artifact_missing'),
    ({}, 'station_not_in_calibration'),
    ({'stations': {}}, 'station_not_in_calibration'),
])
def test_every_way_of_having_no_fit_has_its_own_status(calibration, expected):
    """Convention 20: four causes, four strings. A missing artifact and a
    station absent from a present one need different responses."""
    sigma, feats = wx.fitted_daily_extreme_sigma(calibration, 'KNYC',
                                                 'daily_high', 31.5)
    assert sigma is None
    assert feats['sigma_fit_status'] == expected


def test_a_metric_that_was_not_fitted_is_its_own_status():
    calibration = _calibration(metric='daily_high')
    sigma, feats = wx.fitted_daily_extreme_sigma(calibration, 'KNYC',
                                                 'daily_low', 31.5)
    assert sigma is None
    assert feats['sigma_fit_status'] == 'metric_not_fitted_for_this_station'


def test_a_station_whose_fit_failed_is_refused():
    calibration = _calibration()
    calibration['stations']['KNYC']['metrics']['daily_high']['fit_ok'] = False
    sigma, _feats = wx.fitted_daily_extreme_sigma(calibration, 'KNYC',
                                                  'daily_high', 31.5)
    assert sigma is None


def test_a_non_positive_fitted_sigma_is_refused_not_floored():
    """A zero sigma prices every rung 0.00 or 1.00 with a full-size position
    behind it."""
    calibration = _calibration(n=0, floor=0.0, slope=0.0)
    sigma, feats = wx.fitted_daily_extreme_sigma(calibration, 'KNYC',
                                                 'daily_high', 31.5)
    assert sigma is None
    assert feats['sigma_fit_status'] == 'fitted_sigma_not_positive'


# --- the artifact loader ---------------------------------------------------

def test_the_loader_reads_a_real_file_and_caches_it(tmp_path):
    path = str(tmp_path / 'sigma.json')
    cal.write_calibration(path, _calibration())
    wx.clear_sigma_calibration_cache()
    assert wx.load_sigma_calibration(path)['stations']['KNYC']['city'] == 'test'
    # Second read comes from the cache and still answers.
    assert wx.load_sigma_calibration(path) is not None


def test_a_rewritten_artifact_is_picked_up_without_a_restart(tmp_path):
    """The loop stays up for days while the harness is re-run underneath it. A
    cache keyed on the path alone would pin the first read forever."""
    path = str(tmp_path / 'sigma.json')
    cal.write_calibration(path, _calibration(rmse=2.0))
    wx.clear_sigma_calibration_cache()
    first = wx.load_sigma_calibration(path)
    assert (first['stations']['KNYC']['metrics']['daily_high']
            ['station_verified_by_lead']['1']['rmse_f']) == 2.0
    os.utime(path, (0, 0))                       # force a different mtime
    cal.write_calibration(path, _calibration(rmse=3.5))
    second = wx.load_sigma_calibration(path)
    assert (second['stations']['KNYC']['metrics']['daily_high']
            ['station_verified_by_lead']['1']['rmse_f']) == 3.5


@pytest.mark.parametrize('content', ['{not json', '[]', '{"stations": []}',
                                     '{"stations": {}}'])
def test_a_malformed_artifact_reads_as_absent(tmp_path, content):
    """Convention 28: half a calibration priced as a whole one is worse than
    none. The last case is well-formed but has no `schema_version`."""
    path = str(tmp_path / 'sigma.json')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(content)
    wx.clear_sigma_calibration_cache()
    assert wx.load_sigma_calibration(path) is None


def test_a_missing_file_is_absent_not_an_exception(tmp_path):
    wx.clear_sigma_calibration_cache()
    assert wx.load_sigma_calibration(str(tmp_path / 'nope.json')) is None


# --- through evaluate() ----------------------------------------------------

def _ladder_ctx():
    import tests.test_weather_arb as base
    return base._ctx(market=base._market(question=base.LADDER_TAIL_F,
                                         rules=base.WHOLE_DEGREE_F_RULES))


def _fitted_strategy(**kwargs):
    import tests.test_weather_arb as base
    return base._ladder_strategy(use_fitted_sigma=True, **kwargs)


def test_evaluate_prices_with_the_number_in_the_artifact(monkeypatch):
    """CONVENTION 22, the load-bearing test in this file. Change the artifact,
    and the sigma on the decision row changes with it."""
    rows = {}
    for rmse in (1.5, 4.25):
        strategy = _fitted_strategy(
            sigma_calibration=_calibration(icao='KNYC', rmse=rmse))
        decision = strategy.evaluate(_ladder_ctx())
        rows[rmse] = decision.features
    assert rows[1.5]['daily_extreme_sigma_f'] == pytest.approx(1.5)
    assert rows[4.25]['daily_extreme_sigma_f'] == pytest.approx(4.25)
    assert rows[1.5]['daily_extreme_sigma_source'] == 'fitted_lead_bucket'
    assert rows[1.5]['sigma_constants_are_estimates_never_fitted'] is False
    assert rows[1.5]['use_fitted_sigma'] is True


def test_with_the_flag_off_the_house_constants_are_used_and_labelled():
    import tests.test_weather_arb as base
    decision = base._ladder_strategy().evaluate(_ladder_ctx())
    feats = decision.features
    assert feats['use_fitted_sigma'] is False
    assert feats['sigma_fit_status'] == 'fitted_sigma_not_requested'
    assert feats['daily_extreme_sigma_source'] == 'house_constants_unfitted'
    assert feats['sigma_constants_are_estimates_never_fitted'] is True
    assert feats['daily_extreme_sigma_f'] == pytest.approx(
        wx.daily_extreme_sigma_f(feats['hours_to_window_close']), abs=1e-3)


def test_an_unfitted_station_is_a_cannot_run_with_its_own_reason():
    """Convention 11 and convention 20. It is NOT
    `rung_narrower_than_model_resolution` (which means we HAVE a sigma) and it
    is NOT `station_forecast_unavailable` (which means the API did not answer),
    and it never silently becomes a house constant."""
    decision = _fitted_strategy(sigma_calibration={}).evaluate(_ladder_ctx())
    assert decision.action == 'SKIP'
    assert decision.reason == 'daily_extreme_sigma_unfitted_for_station'
    feats = decision.features
    assert feats['sigma_fit_status'] == 'station_not_in_calibration'
    assert feats['use_fitted_sigma'] is True
    # And no probability was published off a sigma we do not have.
    assert 'model_p_yes' not in feats
    assert 'daily_extreme_sigma_f' not in feats


def test_the_unfitted_reason_is_in_the_strategys_own_vocabulary():
    assert ('daily_extreme_sigma_unfitted_for_station' in wx.SKIP_REASONS)
    assert 'model_confidence_below_entry_floor' in wx.SKIP_REASONS
    assert len(wx.SKIP_REASONS) == len(set(wx.SKIP_REASONS))


def test_both_new_reasons_are_classified_for_forge():
    """An unclassified reason lands in UNKNOWN, and 18% of the evidence sitting
    in UNKNOWN is how the last skip-classification drift was found."""
    from agents import forge_shadow_eval as se
    blocker, missing = se.classify_skip_reason(
        'daily_extreme_sigma_unfitted_for_station')
    assert blocker == se.DATA_BLOCKER and missing
    genuine, _ = se.classify_skip_reason('model_confidence_below_entry_floor')
    assert genuine == se.GENUINE


# ===========================================================================
# 8. THE 0.55 ENTRY FLOOR
# ===========================================================================

def test_the_conviction_gate_is_strictly_greater_than(monkeypatch):
    """0.55 is the smallest conviction allowed to trade, not the largest that
    is not. A model sitting exactly on the floor is refused."""
    import tests.test_weather_arb as base

    # The default book asks 0.40 on Yes, so the MARKET side is No. A model
    # above 0.5 on Yes therefore disagrees, and `p_side` is `P(Yes)` itself.
    for p_side, expect_entry in ((0.5500, False), (0.5501, True)):
        strategy = base._strategy(min_model_p_side=0.55)
        monkeypatch.setattr(strategy, 'probability_yes',
                            lambda *a, _p=p_side, **k: _p)
        decision = strategy.evaluate(base._ctx())
        if expect_entry:
            assert decision.action == 'ENTER', p_side
        else:
            assert decision.reason == 'model_confidence_below_entry_floor'
        assert decision.features['min_model_p_side'] == 0.55


def test_the_conviction_gate_is_not_the_edge_gate():
    """A conviction refusal and a price refusal are different facts and must
    never share a counter. A 0.52 model against a 0.40 book clears an 8c edge
    and is still a coin flip."""
    import tests.test_weather_arb as base
    strategy = base._strategy(min_model_p_side=0.55, min_edge=0.08)
    # p_yes 0.48 -> model side No at 0.52, market implied Yes 0.90 -> No is
    # cheap, so the edge is wide and only conviction can refuse it.
    strategy.probability_yes = lambda *a, **k: 0.48
    decision = strategy.evaluate(base._ctx(yes_asks=((0.90, 100),),
                                           no_asks=((0.10, 100),)))
    assert decision.reason == 'model_confidence_below_entry_floor'
    assert decision.features['model_p_side'] == pytest.approx(0.52)


def test_the_gate_reduces_to_p_yes_when_the_yes_leg_is_the_one_bought():
    """The deviation from the literal instruction, pinned. When the model takes
    the Yes side, `p_side` IS `P(Yes)`, so the gate is exactly
    "P(Yes) > 0.55"."""
    import tests.test_weather_arb as base
    strategy = base._strategy(min_model_p_side=0.55)
    strategy.probability_yes = lambda *a, **k: 0.70
    decision = strategy.evaluate(base._ctx(yes_asks=((0.20, 100),),
                                           no_asks=((0.78, 100),)))
    assert decision.features['outcome_side'] == 'Yes'
    assert decision.features['model_p_side'] == pytest.approx(0.70)
    assert decision.features['model_p_yes'] == pytest.approx(0.70)


def test_the_default_floor_is_the_module_constant():
    assert wx.WeatherArb().min_model_p_side == wx.MIN_MODEL_P_SIDE == 0.55


# ===========================================================================
# 8b. THE HOLE THE FITTED SIGMA OPENED, AND THE GATE THAT CLOSES IT
# ===========================================================================

def test_a_rung_that_clears_half_but_not_the_entry_floor_is_refused():
    """THE LIVE ROW THAT MADE THIS GATE NECESSARY, reproduced.

    Ankara's fitted sigma is 1.333F, which puts a 1.8F Celsius bucket's ceiling
    at 0.500335 - three ten-thousandths above `MIN_ATTAINABLE_P_YES`. With only
    the 0.5 gate in place that row priced the rung at a model 0.097, took the No
    side at 0.903 against a book at 0.51 and booked a 0.39 "edge", which is the
    Madrid pathology arriving through a rounding error.
    """
    import tests.test_weather_arb as base
    ceiling = cal.max_attainable_p_yes(1.8, 1.333)
    assert wx.MIN_ATTAINABLE_P_YES < ceiling <= wx.MIN_MODEL_P_SIDE, ceiling

    strategy = _fitted_strategy(
        sigma_calibration=_calibration(icao='KNYC', rmse=1.333))
    decision = strategy.evaluate(base._ctx(market=base._market(
        question=base.LADDER_BUCKET_C, rules=base.WHOLE_DEGREE_C_RULES)))
    assert decision.action == 'SKIP'
    assert decision.reason == 'rung_cannot_reach_entry_conviction_on_yes'
    feats = decision.features
    assert feats['max_attainable_p_yes'] > feats['min_attainable_p_yes']
    assert feats['max_attainable_p_yes'] <= feats[
        'min_attainable_p_yes_for_entry']
    # And it refused BEFORE pricing, so no probability was published off a rung
    # whose side was already decided.
    assert 'model_p_yes' not in feats


def test_the_two_ceiling_gates_are_separate_counters():
    """Convention 20. "cannot resolve this rung at all" and "can resolve it but
    never confidently enough to prefer Yes" are different distances from
    tradeable, and one counter would hide how close the board is to the line."""
    import tests.test_weather_arb as base
    ctx = base._ctx(market=base._market(question=base.LADDER_BUCKET_C,
                                        rules=base.WHOLE_DEGREE_C_RULES))
    wide = _fitted_strategy(sigma_calibration=_calibration(rmse=2.74))
    narrow = _fitted_strategy(sigma_calibration=_calibration(rmse=1.333))
    assert wide.evaluate(ctx).reason == 'rung_narrower_than_model_resolution'
    assert narrow.evaluate(ctx).reason == (
        'rung_cannot_reach_entry_conviction_on_yes')
    assert ('rung_cannot_reach_entry_conviction_on_yes'
            != 'rung_narrower_than_model_resolution')


def test_a_narrow_enough_sigma_still_gets_through_both_ceiling_gates():
    """The gates are a resolution limit, not a ban on buckets. At 0.6F the 1.8F
    rung's ceiling is 0.866 and the rung is priced normally."""
    import tests.test_weather_arb as base
    assert cal.max_attainable_p_yes(1.8, 0.6) > wx.MIN_MODEL_P_SIDE
    decision = _fitted_strategy(
        sigma_calibration=_calibration(rmse=0.6)).evaluate(
            base._ctx(market=base._market(question=base.LADDER_BUCKET_C,
                                          rules=base.WHOLE_DEGREE_C_RULES)))
    assert decision.reason not in ('rung_narrower_than_model_resolution',
                                   'rung_cannot_reach_entry_conviction_on_yes')
    assert 'model_p_yes' in decision.features


def test_an_unbounded_tail_never_reaches_either_ceiling_gate():
    """A tail has no ceiling, so neither gate may fire on one - that is what
    would silently delete the only rung shape this model can still price."""
    import tests.test_weather_arb as base
    decision = _fitted_strategy(
        sigma_calibration=_calibration(rmse=2.74)).evaluate(_ladder_ctx())
    assert decision.features['rung_is_bounded_on_both_sides'] is False
    assert decision.reason not in ('rung_narrower_than_model_resolution',
                                   'rung_cannot_reach_entry_conviction_on_yes')


def test_the_new_gate_is_classified_for_forge():
    from agents import forge_shadow_eval as se
    blocker, missing = se.classify_skip_reason(
        'rung_cannot_reach_entry_conviction_on_yes')
    assert blocker == se.DATA_BLOCKER and missing


# ===========================================================================
# 9. CONFIG
# ===========================================================================

def test_use_fitted_sigma_is_a_config_key_and_a_bad_value_raises():
    before = wx.weather_config()
    try:
        assert wx.set_weather_config(
            {'use_fitted_sigma': True})['use_fitted_sigma'] is True
        with pytest.raises(ValueError):
            wx.set_weather_config({'use_fitted_sigma': 'true'})
    finally:
        wx.set_weather_config(before)


def test_the_live_config_turns_the_fitted_sigma_on():
    """A flag nothing sets is a flag nobody is using."""
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, 'config.yaml'), encoding='utf-8') as handle:
        config = yaml.safe_load(handle)
    weather = (config.get('polymarket') or {}).get('weather') or {}
    assert weather.get('use_fitted_sigma') is True


def test_the_module_default_stays_off_so_existing_callers_do_not_move():
    assert wx.DEFAULT_USE_FITTED_SIGMA is False


# ===========================================================================
# 10. THE REAL ARTIFACT, if it is on disk
# ===========================================================================

def test_the_committed_artifact_is_readable_and_says_zero_rungs_clear():
    """THE ANSWER, asserted against the artifact rather than a handoff note.

    Skipped when the artifact is absent, because a machine that has never run
    the harness is a legitimate state and a hard failure here would make the
    suite depend on a network run.
    """
    if not os.path.exists(wx.SIGMA_CALIBRATION_PATH):
        pytest.skip('calibration artifact not present on this machine')
    wx.clear_sigma_calibration_cache()
    payload = wx.load_sigma_calibration()
    assert payload is not None, 'artifact present but unreadable'
    pooled = payload['pooled']['daily_high']['by_lead']['1']
    assert pooled['n'] > 200, 'convention 7: this fit needs a real sample'
    # The measured number, and the consequence of it.
    assert 2.0 < pooled['rmse_f'] < 4.0
    assert cal.max_attainable_p_yes(1.8, pooled['rmse_f']) < wx.MIN_MODEL_P_SIDE
    census = payload.get('rung_width_census') or {}
    if census:
        table = cal.rung_tradeability(census, pooled['rmse_f'])
        assert table['bounded_clearing_entry_p'] == 0, (
            'a bounded rung now clears 0.55 - re-read the handoff before '
            'believing it')
