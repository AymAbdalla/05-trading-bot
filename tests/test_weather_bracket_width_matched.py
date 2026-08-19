"""Tests for PM_weather_bracket_width_matched (Forge proposal 033).

Fully offline: both weather feeds are injected, and the sigma calibration
artifact is injected too (never read off disk) so these tests do not depend
on `research/weather_sigma_calibration.json`'s current contents.

This file tests what is NEW in this strategy - the 3-rung bracket
construction, the pooled (not per-station) sigma, the 24-48h lead band, the
station-day ladder accumulated across `evaluate()` calls, the basket's own
entry gates, and the `manage_exit` stop. It does NOT re-test
`WeatherArb.daily_extreme_estimate`'s own branches (station coordinates,
forecast fetch, date resolution, the observed-extreme floor): that method is
`WeatherArb`'s own, already covered by `tests/test_weather_arb.py` and
`tests/test_weather_daily_extreme.py`, and this file consumes it read-only.
One representative propagation test (`station_forecast_unavailable`) checks
the wiring; it does not re-derive the method's internals.

Six jobs, in the order the module docstring's own gaps are stated:

  1. Bracket construction: 3 contiguous rungs covering the required window,
     midpoint constraint, generalised beyond a hardcoded "1.8F x 3".
  2. `p_bracket` math against the proposal's own worked examples (a centred
     0.676 and an off-centre 0.644 at a 1.0F mu error), which doubles as a
     check that this file's formula matches the proposal's arithmetic and
     not just its own internal consistency.
  3. Gate logic: lead band, sigma availability, cost cap, edge floor, depth,
     notional sizing - each isolated to one failing gate.
  4. Ladder accumulation: two rungs alone cannot fire; the third completes
     the bracket; a repeat call in the same cycle and a repeat station-day
     both refuse for their own, distinct reasons.
  5. The partial-fill unwind path is NOT tested here as an executed sell -
     see the module docstring's wiring gap #2: `_attempt_entry` has no
     unwind mechanism for any multi-leg strategy today, so there is nothing
     running to test. What IS tested is `manage_exit`'s stop logic in
     isolation, which is real code on the documented interface.
  6. Registry append test.
"""
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from engine.polymarket.types import (Market, Orderbook,  # noqa: E402
                                     Outcome, PriceLevel)
from strategies.polymarket.base import MarketContext  # noqa: E402
import strategies.polymarket.weather_arb as wx  # noqa: E402
from strategies.polymarket.weather_arb import Reading  # noqa: E402
import strategies.polymarket.weather_bracket_width_matched as wbw  # noqa: E402
from strategies.polymarket.weather_bracket_width_matched import (  # noqa: E402
    BRACKET_HALF_WIDTH_F, MAX_BRACKET_COST, MAX_BRACKET_NOTIONAL_USDC,
    MIN_EDGE_VS_P_BRACKET, STOP_FRACTION_OF_COST, WeatherBracketWidthMatched,
    _RungSnapshot, find_bracket, p_bracket, pooled_bracket_sigma_f)

# 2026-08-18T00:00:00Z. Matches the constant every other weather test file in
# this repo uses, for the same reason: readable-by-eye arithmetic against a
# UTC-offset-0 fixture.
DAY_START = 1787011200
DAY_END = DAY_START + 86400
LOCAL_DATE = '2026-08-18'
#: Mid-band lead (24-48h), so the default fixture clears the lead-band gate
#: without sitting on either edge.
LEAD_HOURS = 36.0
NOW = int(DAY_END - LEAD_HOURS * 3600)
OBS_TS = NOW - 300

STATION = 'KNYC'
RULES = ('This market resolves based on the daily high temperature reported '
        'at the KNYC station. The resolution source for this market '
        'measures temperatures to whole degrees Celsius (eg, 9C).')

#: The pooled sigma this basket trades at, matching the artifact's own
#: 2026-08-18 fit (`pooled.daily_high.by_lead["1"].rmse_f`). Injected as a
#: dict rather than read off disk (see the module docstring above).
SIGMA_F = 2.7354
CALIBRATION = {'generated_utc': '2026-08-18T00:00:00Z',
              'pooled': {'daily_high': {'by_lead': {
                  '1': {'rmse_f': SIGMA_F, 'n': 537, 'midpoint_hours': 36.0}}}}}

#: Three adjacent whole-Celsius buckets. Each is a 1C step widened by half a
#: step either side (`_ladder`), so the F edges tile exactly:
#:   29C -> [83.3, 85.1)F   30C -> [85.1, 86.9)F   31C -> [86.9, 88.7)F
#: union [83.3, 88.7]F, width 5.4F, midpoint 86.0F.
RUNGS = (29, 30, 31)
BRACKET_CENTER_F = 86.0


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ')


def _book(token, asks=(), bids=()):
    return Orderbook(
        token_id=token,
        asks=tuple(PriceLevel(p, s) for p, s in sorted(asks)),
        bids=tuple(PriceLevel(p, s) for p, s in sorted(bids, reverse=True)))


def _slug(celsius):
    return 'nyc-temp-2026-08-18-%dc' % celsius


def _yes_token(celsius):
    return 'YES-%dC' % celsius


def _market(celsius=30, question=None, rules=RULES, end_ts=None, slug=None):
    slug = slug or _slug(celsius)
    yes = _yes_token(celsius)
    if question is None:
        question = ('Will the highest temperature in NYC be %d°C on '
                    'August 18?' % celsius)
    if end_ts is None:
        end_ts = DAY_END
    return Market(id=slug, question=question, slug=slug,
                 condition_id='c-' + slug,
                 outcomes=(Outcome('Yes', yes), Outcome('No', 'NO-' + yes)),
                 active=True, closed=False, end_date=_iso(end_ts),
                 raw={'description': rules})


class FakeAirportFeed(object):
    def __init__(self, temp_f=70.0, obs_ts=OBS_TS, status='ok',
                station=STATION, lat=40.7794, lon=-73.8803):
        self.temp_f = temp_f
        self.obs_ts = obs_ts
        self.status = status
        self.station = station
        self.lat = lat
        self.lon = lon
        self.calls = []
        self.history_calls = []

    def observation(self, icao):
        self.calls.append(icao)
        if self.status != 'ok':
            return None, self.status
        return Reading(source='airport_metar',
                       station=self.station or str(icao).upper(),
                       temp_f=self.temp_f, observed_ts=self.obs_ts,
                       lat=self.lat, lon=self.lon), 'ok'

    def daily_extreme_checked(self, icao, metric, window_start_ts,
                              window_end_ts):
        # Not reached by the default (36h lead) fixture: the observation
        # window is not open yet at that lead, so `daily_extreme_estimate`
        # never calls this. Reached by a <24h-lead ("too late") fixture,
        # where the window HAS opened - returns the current reading as the
        # running extreme, same shape as `test_weather_arb.py`'s own fake.
        self.history_calls.append((icao, metric, window_start_ts,
                                   window_end_ts))
        return wx.DailyObserved(
            station=str(icao).upper(), metric=metric, extreme_f=self.temp_f,
            observations=1, first_ts=int(window_start_ts) + 60,
            last_ts=int(self.obs_ts or window_start_ts),
            window_start_ts=int(window_start_ts),
            window_end_ts=int(window_end_ts)), 'ok'


class FakeForecastFeed(object):
    def __init__(self, daily_max_f=BRACKET_CENTER_F, grid_now_f=70.0,
                status='ok', dates=(LOCAL_DATE,), obs_ts=OBS_TS):
        self.daily_max_f = daily_max_f
        self.grid_now_f = grid_now_f
        self.status = status
        self.dates = tuple(dates)
        self.obs_ts = obs_ts
        self.calls = []

    def forecast_checked(self, lat, lon):
        self.calls.append((lat, lon))
        if self.status != 'ok':
            return None, self.status
        # Shifted so the hourly grid COVERS `self.obs_ts` (the matching
        # airport reading's own timestamp), not just the target day - the
        # two can be many hours apart at a 24-48h lead, and `hourly_at`'s
        # bias lookup needs a grid point near the OBSERVATION, not near the
        # day it is forecasting.
        shift = self.obs_ts - DAY_START
        hourly_ts = tuple(DAY_START + shift + 3600 * h for h in range(48))
        n = len(self.dates)
        return wx.StationForecast(
            req_lat=lat, req_lon=lon, grid_lat=lat, grid_lon=lon,
            utc_offset_sec=0, timezone_name='UTC',
            daily_dates=self.dates,
            daily_max_f=tuple([self.daily_max_f] * n),
            daily_min_f=tuple([60.0] * n),
            hourly_ts=hourly_ts,
            hourly_f=tuple([self.grid_now_f] * len(hourly_ts)),
            fetched_ts=float(NOW)), 'ok'


def _strategy(airport_feed=None, forecast_feed=None, calibration=CALIBRATION,
             **kwargs):
    model = wx.WeatherArb(
        airport_feed=(airport_feed if airport_feed is not None
                      else FakeAirportFeed()),
        forecast_feed=(forecast_feed if forecast_feed is not None
                       else FakeForecastFeed()),
        use_fitted_sigma=False, require_observed_extreme=None,
        max_hours_to_window_close=wbw.MODEL_MAX_HOURS_TO_WINDOW_CLOSE)
    return WeatherBracketWidthMatched(model=model, sigma_calibration=calibration,
                                      **kwargs)


def _ctx(celsius=30, now=NOW, asks=((0.20, 100.0),), bids=((0.18, 100.0),),
         question=None, rules=RULES, end_ts=None, slug=None):
    market = _market(celsius, question=question, rules=rules, end_ts=end_ts,
                     slug=slug)
    yes = _yes_token(celsius)
    books = {}
    if asks is not None or bids is not None:
        books[yes] = _book(yes, asks or (), bids or ())
    return MarketContext(window_ts=now, market=market, books=books,
                         seconds_into_window=0.0, market_type='weather')


def _enter_bracket(strategy=None, now=NOW, asks=((0.20, 100.0),),
                   bids=((0.18, 100.0),)):
    """Drive `evaluate()` across all 3 rungs and return `(strategy, decisions)`.

    The first two decisions are expected SKIPs (incomplete ladder); the third
    is the ENTER (or whatever the caller's fixture produces).
    """
    strategy = strategy or _strategy()
    decisions = [strategy.evaluate(_ctx(c, now=now, asks=asks, bids=bids))
                for c in RUNGS]
    return strategy, decisions


# ============ 0. house rules ============

def test_paper_mode_true_in_the_module_and_on_the_class():
    assert wbw.PAPER_MODE is True
    assert WeatherBracketWidthMatched().paper_mode is True


def test_supports_weather_only():
    from strategies.polymarket.base import MARKET_TYPE_WEATHER
    assert (WeatherBracketWidthMatched.supported_market_types
           == (MARKET_TYPE_WEATHER,))


def test_manages_exits_and_ships_the_interface():
    s = WeatherBracketWidthMatched()
    assert s.manages_exits is True
    assert callable(s.manage_exit)


def test_module_names_both_wiring_gaps_it_found():
    """Convention 22: a claim about what is wired is not the same as a test
    that it is. This asserts the honest disclosure exists, not that either
    gap is closed."""
    doc = wbw.__doc__ or ''
    assert 'run_weather_cycle` never calls it at all' in doc
    assert 'no automatic partial-fill unwind' in doc.lower()


def test_no_em_dash_and_no_double_hyphen_inside_a_word():
    path = wbw.__file__.replace('.pyc', '.py')
    with open(path, 'r') as fh:
        text = fh.read()
    assert '—' not in text
    import re
    for match in re.finditer(r'\w--\w', text):
        pytest.fail('double hyphen inside a word: %r' % match.group(0))


# ============ 1. pooled_bracket_sigma_f ============

class TestPooledBracketSigma:

    def test_reads_the_pooled_lead_one_rmse(self):
        value, feats = pooled_bracket_sigma_f(CALIBRATION)
        assert value == pytest.approx(SIGMA_F)
        assert feats['pooled_sigma_fit_status'] == 'ok'
        assert feats['pooled_sigma_n'] == 537

    def test_none_calibration_is_a_named_status(self):
        value, feats = pooled_bracket_sigma_f(None)
        assert value is None
        assert feats['pooled_sigma_fit_status'] == 'calibration_artifact_missing'

    def test_missing_lead_bucket_is_a_named_status(self):
        value, feats = pooled_bracket_sigma_f(CALIBRATION, lead_bucket='9')
        assert value is None
        assert feats['pooled_sigma_fit_status'] == 'pooled_lead_bucket_missing'

    def test_missing_pooled_daily_high_key_is_a_named_status(self):
        value, feats = pooled_bracket_sigma_f({'pooled': {}})
        assert value is None
        assert feats['pooled_sigma_fit_status'] == 'pooled_lead_bucket_missing'

    def test_non_positive_rmse_is_refused(self):
        bad = {'pooled': {'daily_high': {'by_lead': {'1': {'rmse_f': 0.0}}}}}
        value, feats = pooled_bracket_sigma_f(bad)
        assert value is None
        assert feats['pooled_sigma_fit_status'] == 'pooled_rmse_not_positive'


# ============ 2. find_bracket ============

class TestFindBracket:

    def _rungs(self, values=RUNGS):
        out = []
        for c in values:
            t, _ = wx.parse_threshold_checked(
                'Will the highest temperature in NYC be %d°C on August 18?'
                % c)
            out.append(_RungSnapshot(
                lo_f=t.lo_f, hi_f=t.hi_f, market_slug=_slug(c),
                best_ask=0.2, best_bid=0.18, ask_depth_at_ask=100.0,
                mu_f=BRACKET_CENTER_F, hours_to_window_close=LEAD_HOURS,
                seen_at=NOW))
        return out

    def test_three_contiguous_rungs_centred_on_mu(self):
        found = find_bracket(self._rungs(), BRACKET_CENTER_F)
        assert found is not None
        r0, r1, r2, center = found
        assert (r0.market_slug, r1.market_slug, r2.market_slug) == (
            _slug(29), _slug(30), _slug(31))
        assert center == pytest.approx(BRACKET_CENTER_F, abs=1e-6)

    def test_fewer_than_three_rungs_cannot_complete(self):
        assert find_bracket(self._rungs(RUNGS[:2]), BRACKET_CENTER_F) is None

    def test_a_gap_in_the_ladder_is_not_contiguous(self):
        # 29C and 31C only - skips 30C, so the union has a hole.
        rungs = self._rungs((29, 31))
        # Pad with an unrelated far-away rung so len >= 3 but no valid triple
        # exists; find_bracket must not fabricate contiguity across the gap.
        extra = self._rungs((40,))
        assert find_bracket(rungs + extra, BRACKET_CENTER_F) is None

    def test_center_too_far_from_mu_is_refused(self):
        # mu is 10F away from the union's own centre (86.0F).
        assert find_bracket(self._rungs(), BRACKET_CENTER_F + 10.0) is None

    def test_center_within_half_a_degree_of_mu_is_accepted(self):
        found = find_bracket(self._rungs(), BRACKET_CENTER_F + 0.49)
        assert found is not None

    def test_finds_a_valid_triple_among_extra_unrelated_rungs(self):
        rungs = self._rungs() + self._rungs((50, 51, 52))
        found = find_bracket(rungs, BRACKET_CENTER_F)
        assert found is not None
        r0, _, r2, _ = found
        assert r0.market_slug == _slug(29)
        assert r2.market_slug == _slug(31)


# ============ 3. p_bracket ============

class TestPBracket:

    def test_centred_bracket_matches_the_proposals_own_worked_constant(self):
        # Proposal 033's "Why this might fail": centred, 2*Phi(2.7/2.74)-1
        # = 0.676. p_bracket at c == mu is exactly that same expression.
        value = p_bracket(BRACKET_CENTER_F, BRACKET_CENTER_F, SIGMA_F)
        assert value == pytest.approx(0.676, abs=1e-3)

    def test_one_degree_off_centre_matches_the_proposals_second_worked_value(self):
        # Same section: mu off by 1.0F -> Phi(3.7/2.74) - Phi(-1.7/2.74)
        # = 0.9115 - 0.2676 = 0.644.
        mu = BRACKET_CENTER_F - 1.0
        value = p_bracket(BRACKET_CENTER_F, mu, SIGMA_F)
        assert value == pytest.approx(0.644, abs=1e-3)

    def test_clamped_to_zero_and_one(self):
        # A centre absurdly far from mu still returns a valid probability.
        assert p_bracket(BRACKET_CENTER_F, BRACKET_CENTER_F + 1000.0,
                         SIGMA_F) == pytest.approx(0.0, abs=1e-9)


# ============ 4. evaluate(): identification and lead-band gates ============

class TestIdentificationGates:

    def test_no_market_is_no_market(self):
        s = _strategy()
        ctx = MarketContext(window_ts=NOW, market=None, market_type='weather')
        d = s.evaluate(ctx)
        assert d.action == 'SKIP' and d.reason == 'no_market'

    def test_global_temperature_market_is_excluded(self):
        s = _strategy()
        q = 'Will global temperature increase by more than 1.29ºC in August 2026?'
        d = s.evaluate(_ctx(question=q))
        assert d.reason == 'global_temperature_market_excluded'

    def test_not_a_temperature_market_at_all(self):
        s = _strategy()
        # `slug` must be non-weather too: `looks_like_a_temperature_market`
        # shortlists on the SLUG (`city_for_market`) as well as the question,
        # and every other fixture's slug contains 'nyc'.
        d = s.evaluate(_ctx(question='Will the Lakers win their game on '
                                     'August 18?',
                            slug='lakers-game-2026-08-18'))
        assert d.reason == 'not_a_temperature_market'

    def test_daily_low_market_is_refused_not_daily_high(self):
        s = _strategy()
        q = ('Will the lowest temperature in NYC be 30°C on '
            'August 18?')
        d = s.evaluate(_ctx(question=q))
        assert d.reason == 'market_metric_not_daily_high'
        assert d.features['market_metric'] == 'daily_low'

    def test_point_in_time_question_has_no_metric_at_all(self):
        s = _strategy()
        d = s.evaluate(_ctx(question='Will NYC exceed 85°F on August 18?'))
        assert d.reason == 'market_metric_not_daily_high'
        assert d.features['market_metric'] is None

    def test_a_tail_is_not_a_ladder_rung(self):
        s = _strategy()
        q = ('Will the highest temperature in NYC be 85°F or below '
            'on August 18?')
        d = s.evaluate(_ctx(question=q))
        assert d.reason == 'threshold_not_a_bounded_bucket'

    def test_unresolvable_station_is_refused(self):
        s = _strategy()
        d = s.evaluate(_ctx(rules='Resolves based on the official high '
                                  'temperature.'))
        assert d.reason == 'resolution_station_unknown'


class TestLeadBand:

    def test_too_early_is_named_and_directional(self):
        s = _strategy()
        too_early_now = int(DAY_END - 60.0 * 3600)  # 60h out
        d = s.evaluate(_ctx(now=too_early_now))
        assert d.reason == 'outside_24_48h_lead_band'
        assert d.features['lead_band_direction'] == 'too_early'

    def test_too_late_is_named_and_directional(self):
        too_late_now = int(DAY_END - 10.0 * 3600)  # 10h out
        # A fresh airport reading AND a forecast grid shifted to match, both
        # relative to THIS `now` - otherwise the module default's fixed
        # OBS_TS reads as stale, or outside the hourly grid, and the wrong
        # gate fires first.
        obs_ts = too_late_now - 300
        s = _strategy(airport_feed=FakeAirportFeed(obs_ts=obs_ts),
                     forecast_feed=FakeForecastFeed(obs_ts=obs_ts))
        d = s.evaluate(_ctx(now=too_late_now))
        assert d.reason == 'outside_24_48h_lead_band'
        assert d.features['lead_band_direction'] == 'too_late'

    def test_the_default_fixture_sits_mid_band(self):
        s = _strategy()
        d = s.evaluate(_ctx())
        assert d.reason != 'outside_24_48h_lead_band'


class TestAirportFreshness:

    def test_stale_reading_is_refused(self):
        stale_feed = FakeAirportFeed(obs_ts=NOW - 7200)  # 2h old
        s = _strategy(airport_feed=stale_feed)
        d = s.evaluate(_ctx())
        assert d.reason == 'airport_obs_stale'

    def test_unavailable_reading_is_refused(self):
        s = _strategy(airport_feed=FakeAirportFeed(status='feed_network_failure'))
        d = s.evaluate(_ctx())
        assert d.reason == 'airport_reading_unavailable'


def test_estimate_failure_propagates_as_its_own_named_status():
    """One representative check that a `daily_extreme_estimate` failure
    reaches `evaluate` under ITS OWN status string, not a generic one. The
    method's own branches are `WeatherArb`'s and are not re-tested here."""
    s = _strategy(forecast_feed=FakeForecastFeed(status='feed_network_failure'))
    d = s.evaluate(_ctx())
    assert d.reason == 'station_forecast_unavailable'


# ============ 5. bracket construction and its own entry gates ============

class TestBracketAccumulationAndEntry:

    def test_two_rungs_alone_cannot_complete_a_bracket(self):
        s = _strategy()
        d1 = s.evaluate(_ctx(29))
        d2 = s.evaluate(_ctx(30))
        assert d1.reason == 'no_contiguous_bracket_available'
        assert d2.reason == 'no_contiguous_bracket_available'

    def test_the_third_rung_completes_the_bracket_and_enters(self):
        strategy, decisions = _enter_bracket()
        d1, d2, d3 = decisions
        assert d1.reason == 'no_contiguous_bracket_available'
        assert d2.reason == 'no_contiguous_bracket_available'
        assert d3.action == 'ENTER'
        assert len(d3.legs) == 3
        assert {leg.market_slug for leg in d3.legs} == {
            _slug(c) for c in RUNGS}
        assert all(leg.outcome_side == 'Yes' for leg in d3.legs)
        assert d3.features['bracket_center_f'] == pytest.approx(
            BRACKET_CENTER_F, abs=1e-3)
        assert d3.features['bracket_cost'] == pytest.approx(0.60, abs=1e-6)
        assert d3.features['p_bracket'] == pytest.approx(0.676, abs=1e-3)
        assert d3.features['edge'] == pytest.approx(0.076, abs=1e-3)
        assert d3.features['shares'] == 16
        assert all(leg.shares == 16 for leg in d3.legs)
        assert all(leg.expected_price == pytest.approx(0.20) for leg in d3.legs)

    def test_legs_are_ordered_thinnest_book_first(self):
        # shares = floor(10 / 0.60) = 16, so every depth below must clear 16
        # for the bracket to fill; only the ORDER should vary.
        strategy = _strategy()
        strategy.evaluate(_ctx(29, asks=((0.20, 100.0),)))
        strategy.evaluate(_ctx(30, asks=((0.20, 20.0),)))
        d3 = strategy.evaluate(_ctx(31, asks=((0.20, 50.0),)))
        assert d3.action == 'ENTER'
        assert [leg.market_slug for leg in d3.legs] == [
            _slug(30), _slug(31), _slug(29)]

    def test_missing_book_on_a_rung_is_its_own_reason(self):
        s = _strategy()
        d = s.evaluate(_ctx(asks=None, bids=None))
        assert d.reason == 'bracket_leg_missing_book'

    def test_sigma_unavailable_refuses_before_any_gate_needs_it(self):
        # `{}` injects the ABSENCE of a calibration without hitting disk;
        # `None` would mean "not injected, lazy-load the real artifact"
        # (same contract as `WeatherArb.sigma_calibration` - see that
        # property's own docstring).
        s = _strategy(calibration={})
        _, decisions = _enter_bracket(s)
        assert decisions[-1].reason == 'bracket_sigma_unavailable'

    def test_cost_above_cap_is_refused(self):
        # 3 legs at 0.30 = 0.90 > MAX_BRACKET_COST (0.85).
        _, decisions = _enter_bracket(asks=((0.30, 100.0),))
        assert decisions[-1].reason == 'bracket_cost_above_cap'
        assert decisions[-1].features['bracket_cost'] == pytest.approx(0.90)

    def test_edge_below_min_is_refused(self):
        # cost 0.65 against p_bracket 0.676 -> edge 0.026, under the 0.04 floor.
        _, decisions = _enter_bracket(asks=((0.2167, 100.0),))
        d = decisions[-1]
        assert d.reason == 'bracket_edge_below_min'
        assert d.features['edge'] < MIN_EDGE_VS_P_BRACKET

    def test_insufficient_ask_depth_is_refused(self):
        strategy = _strategy()
        strategy.evaluate(_ctx(29, asks=((0.20, 100.0),)))
        strategy.evaluate(_ctx(30, asks=((0.20, 100.0),)))
        d3 = strategy.evaluate(_ctx(31, asks=((0.20, 1.0),)))  # far under shares
        assert d3.reason == 'bracket_insufficient_ask_depth'
        assert _slug(31) in d3.features['under_depth_legs']

    def test_unsizable_at_notional_cap_is_reachable_with_a_raised_floor(self):
        # Invariant guard: under the default MIN_SHARES_PER_LEG=1 this can
        # never fire while cost <= MAX_BRACKET_COST (floor(10/0.85) >= 11).
        # Raising `min_shares` on the instance is the same technique
        # `test_weather_arb.py` uses to exercise its own unreachable-through-
        # the-normal-path guards: it proves the gate is wired, not dead text.
        model = wx.WeatherArb(airport_feed=FakeAirportFeed(),
                              forecast_feed=FakeForecastFeed(),
                              use_fitted_sigma=False,
                              require_observed_extreme=None,
                              max_hours_to_window_close=
                              wbw.MODEL_MAX_HOURS_TO_WINDOW_CLOSE)
        s = WeatherBracketWidthMatched(model=model, sigma_calibration=CALIBRATION,
                                       min_shares=20)
        _, decisions = _enter_bracket(s)
        assert decisions[-1].reason == 'bracket_unsizable_at_notional_cap'

    def test_stale_cached_leg_is_refused(self):
        strategy = _strategy()
        strategy.evaluate(_ctx(29, now=NOW))
        strategy.evaluate(_ctx(30, now=NOW))
        later = NOW + int(wbw.LADDER_CACHE_FRESHNESS_SEC) + 1
        d3 = strategy.evaluate(_ctx(31, now=later))
        assert d3.reason == 'bracket_leg_data_stale'

    def test_repeat_call_same_cycle_after_entry_is_its_own_reason(self):
        strategy, decisions = _enter_bracket()
        assert decisions[-1].action == 'ENTER'
        repeat = strategy.evaluate(_ctx(31, now=NOW))
        assert repeat.reason == 'bracket_already_entered_this_cycle'

    def test_repeat_station_day_a_different_cycle_is_its_own_reason(self):
        strategy, decisions = _enter_bracket()
        assert decisions[-1].action == 'ENTER'
        later = NOW + 60
        repeat = strategy.evaluate(_ctx(31, now=later))
        assert repeat.reason == 'bracket_already_attempted_this_station_day'

    def test_entered_leg_carries_bracket_metadata_for_manage_exit(self):
        _, decisions = _enter_bracket()
        d3 = decisions[-1]
        assert d3.features['bracket_leg_slugs'] == [
            _slug(c) for c in RUNGS]
        assert d3.features['bracket_cost'] == pytest.approx(0.60, abs=1e-6)


# ============ 6. manage_exit ============

class TestManageExit:

    def _position(self, celsius=29, cost=0.60, shares=16):
        return SimpleNamespace(
            position_id='pos-%d' % celsius, market_slug=_slug(celsius),
            shares=shares,
            features={'bracket_cost': cost,
                     'bracket_leg_slugs': [_slug(c) for c in RUNGS]})

    def test_missing_metadata_holds(self):
        s = WeatherBracketWidthMatched()
        pos = SimpleNamespace(position_id='p', market_slug=_slug(29),
                              shares=16, features={})
        d = s.manage_exit(pos, _book(_yes_token(29), bids=((0.10, 100.0),)),
                          now=NOW)
        assert d.action == 'HOLD' and d.reason == 'bracket_metadata_missing'

    def test_unknown_sibling_bid_holds(self):
        s = WeatherBracketWidthMatched()
        pos = self._position(29)
        d = s.manage_exit(pos, _book(_yes_token(29), bids=((0.10, 100.0),)),
                          now=NOW)
        assert d.action == 'HOLD'
        assert d.reason == 'bracket_sibling_bid_unavailable'

    def test_combined_bid_above_stop_holds(self):
        s = WeatherBracketWidthMatched()
        s._latest_bid_by_slug[_slug(30)] = 0.18
        s._latest_bid_by_slug[_slug(31)] = 0.18
        pos = self._position(29, cost=0.60)
        d = s.manage_exit(pos, _book(_yes_token(29), bids=((0.18, 100.0),)),
                          now=NOW)
        # combined 0.54 > stop 0.55 * 0.60 = 0.33
        assert d.action == 'HOLD' and d.reason == 'combined_bid_above_stop'

    def test_combined_bid_at_or_below_stop_exits(self):
        s = WeatherBracketWidthMatched()
        s._latest_bid_by_slug[_slug(30)] = 0.10
        s._latest_bid_by_slug[_slug(31)] = 0.10
        pos = self._position(29, cost=0.60, shares=16)
        d = s.manage_exit(pos, _book(_yes_token(29), bids=((0.10, 100.0),)),
                          now=NOW)
        # combined 0.30 <= stop 0.55 * 0.60 = 0.33
        assert d.action == 'EXIT' and d.reason == 'bracket_stop'
        assert d.position_id == 'pos-29'
        assert d.limit_price == pytest.approx(0.10)
        assert d.shares == 16

    def test_stop_fraction_is_strictly_below_entry_cost(self):
        # Convention 8. STOP_FRACTION_OF_COST is a fraction of cost, cost is
        # a positive premium, so the stop level is always < cost.
        assert 0.0 < STOP_FRACTION_OF_COST < 1.0

    def test_self_bid_falls_back_to_cached_value_when_book_is_none(self):
        s = WeatherBracketWidthMatched()
        s._latest_bid_by_slug[_slug(29)] = 0.10
        s._latest_bid_by_slug[_slug(30)] = 0.10
        s._latest_bid_by_slug[_slug(31)] = 0.10
        pos = self._position(31, cost=0.60, shares=16)
        d = s.manage_exit(pos, None, now=NOW)
        assert d.action == 'EXIT' and d.reason == 'bracket_stop'


# ============ 7. registry ============

class TestRegistry:

    def test_registry_is_twenty_six_and_this_strategy_is_index_23(self):
        # 26 since `PM_fair_value_settlement_exit` (proposal 034) and
        # `PM_fair_value_mirror_fade` (D-326) were APPENDED at indices 24
        # and 25, after this strategy.
        from strategies.polymarket import build_strategies
        names = [s.strategy_name for s in build_strategies()]
        assert len(names) == 26
        assert names[:8] == [
            'PM_streak_snapper', 'PM_mid_price_continuation', 'PM_box_builder',
            'PM_corridor_collector', 'PM_temporal_arbitrage', 'PM_corridor_pair',
            'PM_spread_harvest_taker', 'PM_fair_value_arb',
        ]
        assert names[23] == 'PM_weather_bracket_width_matched'
