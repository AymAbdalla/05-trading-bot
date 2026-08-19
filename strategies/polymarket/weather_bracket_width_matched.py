"""PM_weather_bracket_width_matched - Forge proposal 033.

`strategies/proposals/033-pm-weather-bracket-width-matched.md` is the source
of truth for the thesis and the entry/exit rules; this docstring covers what
proposal 033 itself flagged as unverified and what building it actually found.

## The instrument, not the model

`PM_weather_arb` (this file's sibling) prices 1.8F-wide Celsius rungs off a
sigma the model cannot resolve them with (`rung_narrower_than_model_resolution`,
2,220 live skips, and the record is explicit that this is CORRECT, not
broken: `max_attainable_p_yes` caps a bounded rung at `2*Phi(w/(2*sigma))-1`,
and a rung narrower than the sigma has almost no room to move regardless of
the forecast). This file does not touch that model or that verdict. It buys
THREE adjacent rungs as one basket, so the traded width (5.4F) is the width
`research/weather_sigma_calibration.json`'s pooled 24-48h RMSE (2.7354F,
fitted 2026-08-18) can actually resolve, and prices the basket with the
proposal's own formula:

    p_bracket = Phi((c+2.7-mu)/sigma) - Phi((c-2.7-mu)/sigma)

`mu` is reused from `WeatherArb.daily_extreme_estimate` (forecast extreme
plus the measured station-minus-grid bias, floored by the day's observed
extreme where the observation window is open) - the SAME mu the sibling
strategy computes, via a private, read-only `WeatherArb` instance held only
for its feeds and that one method. `sigma` is NOT that method's per-station
fitted value (this basket does not need a per-station fit and would refuse
needlessly on any station `WeatherArb` has not measured); it is the flat,
POOLED lead-1 (24-48h) RMSE read straight out of the calibration artifact via
`pooled_bracket_sigma_f`, so a re-run of
`backtest/measure_daily_extreme_calibration.py` propagates with no code
change (the proposal's own requirement).

## Two wiring facts this build surfaced, neither invented by this file

**1. `MarketContext` carries one rung per `evaluate()` call, never a ladder.**
Proposal 033's own `data_requirements` flagged this as unverified. It is
confirmed absent: `run_weather_cycle` builds one `MarketContext` per market
per cycle and calls every weather strategy's `evaluate()` on it in turn, with
no sibling-rung object anywhere on the context. This strategy instance
persists across cycles (same pattern `WeatherArb`, `SmartMoneyCopy` and
`StatusQuoCollector` already rely on for their own per-instance caches), so
it accumulates every rung it is ever handed for a station-day into
`self._ladder_cache` and looks for a valid bracket in the ACCUMULATED set,
not just what one `evaluate()` call was handed. Consequence, stated plainly:
whether a bracket is ever buildable in a given cycle depends on how many of
that station-day's rungs the ranking (`rank_weather_markets`,
`DEFAULT_WEATHER_MARKET_LIMIT = 8` markets/cycle) happened to select into
recent cycles, not on this file. `LADDER_CACHE_FRESHNESS_SEC` (150s, ~2.5x
the 60s weather cadence) requires all 3 legs' cached books to be recent
before entering - "within one poll cycle" per the proposal, approximated as
"within about one missed cycle" because the ladder is not exposed atomically.

**2. Weather-space exit management has no execution hook, for ANY strategy.**
`manage_exits()` is called only from the crypto `run_cycle`, and it routes
positions by `asset_for_slug()`, which recognises only `btc-`/`eth-`/`sol-`
slugs. `run_weather_cycle` never calls it at all. This is a PRE-EXISTING gap:
`PM_fair_value_arb` and `PM_dip_arb` both declare `MARKET_TYPE_WEATHER`
support and `manages_exits = True`, and their weather-side exits are
equally unreachable today. This file's `manage_exit` (Exit B: sell all three
legs when the combined best bid falls to `0.55 * cost` or below, strictly
below entry per convention 8) is written to the same interface those two use
and is tested in isolation below, but it is DEAD CODE as shipped, exactly
like theirs. Likewise, `_attempt_entry` has no automatic partial-fill unwind
for any multi-leg strategy today - `corridor_pair_live`'s own pairs are only
COUNTED as `partial_pairs`, never unwound - so proposal 033's rule 7
("immediately sell filled legs back at bid, log leg_risk_unwind") has no
loop-level mechanism to execute it. Both gaps are named in the handoff for
Raven/Aym rather than patched inside this single-strategy change: fixing
either touches `engine/polymarket/shadow_loop.py`, a shared file every other
multi-leg and weather-exit strategy also depends on, and a fix scoped to one
proposal's needs is the wrong place to design it.

## Sizing and concurrency

`MAX_BRACKET_NOTIONAL_USDC = 10.0` total across all 3 legs (not per leg -
the proposal is explicit: "10 USD per bracket"), sized down exactly the way
`WeatherArb` sizes its own single-leg notional cap. "Maximum 1 concurrent
bracket" is NOT enforced by this file: the shared `PolymarketRiskGate`
(`max_concurrent_positions`, 5 slots system-wide, matching the proposal's own
"3 legs eat 3 of the 5 risk gate slots") already blocks a leg once the loop
is out of slots, and duplicating that cap here would be a second source of
truth for a number this file does not own. What IS enforced here, for a
different reason, is one entry attempt per station-day ever
(`self._entered_station_days`): this strategy has no visibility into when a
position it opened later closes (no context carries open positions), so it
cannot tell "still open" from "resolved" and does not try to.

Holds all 3 legs to resolution as the primary exit (proposal rule 9): exactly
one rung pays 1.00 and two pay 0.00, unless the observed high falls outside
the bracket entirely, in which case all three pay 0.00.
"""
import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Dict, List, Optional, Set, Tuple

from strategies.polymarket.base import (MARKET_TYPE_WEATHER, Decision, Leg,
                                        MarketContext, PolymarketStrategy)
from strategies.polymarket.fair_value_arb import ExitDecision
from strategies.polymarket.weather_arb import (WeatherArb,
                                               is_global_temperature_market,
                                               load_sigma_calibration,
                                               looks_like_a_temperature_market,
                                               market_metric,
                                               parse_threshold_checked,
                                               reporting_step_checked,
                                               resolution_station_checked)

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

#: Proposal 033 rule 4: the bracket must cover a window this wide, centred on
#: `c`. 2*2.7 = 5.4F, three 1.8F Celsius rungs.
BRACKET_HALF_WIDTH_F = 2.7
#: Proposal 033 rule 3: `abs(c - mu) <= 0.5F`.
MAX_CENTER_OFFSET_F = 0.5
MIN_LEAD_HOURS = 24.0
MAX_LEAD_HOURS = 48.0
#: Proposal 033 rule 6: `p_bracket - cost >= 0.04`.
MIN_EDGE_VS_P_BRACKET = 0.04
#: Proposal 033 rule 6: `cost <= 0.85`.
MAX_BRACKET_COST = 0.85
#: Proposal 033 rule 8: "10 USD per bracket" - total across all 3 legs.
MAX_BRACKET_NOTIONAL_USDC = 10.0
MIN_SHARES_PER_LEG = 1
#: Proposal 033 Exit B: strictly below entry cost (convention 8).
STOP_FRACTION_OF_COST = 0.55
#: ~2.5x the weather cycle's 60s cadence. A cached rung older than this did
#: not come from "this poll cycle" in any sense worth trusting.
LADDER_CACHE_FRESHNESS_SEC = 150.0
#: Key into `pooled.daily_high.by_lead` in
#: `research/weather_sigma_calibration.json`. lead_days=1, midpoint_hours=36 -
#: the 24-48h band this strategy trades, fitted 2026-08-18 at rmse_f=2.7354.
POOLED_LEAD_BUCKET = '1'
#: `WeatherArb`'s own default (`MAX_HOURS_TO_WINDOW_CLOSE = 36.0`) is
#: narrower than this strategy's 48h band ceiling and would refuse rungs
#: between 36 and 48 hours out before this file's own lead-band gate ever
#: saw them. Widened on the private model instance only; `WeatherArb`'s own
#: default is untouched (this file does not import or edit that constant).
MODEL_MAX_HOURS_TO_WINDOW_CLOSE = 60.0

_NORMAL = NormalDist(0.0, 1.0)


@dataclass
class _RungSnapshot:
    """One rung of a station-day's ladder, as last observed."""

    lo_f: float
    hi_f: float
    market_slug: str
    best_ask: float
    best_bid: Optional[float]
    ask_depth_at_ask: float
    mu_f: float
    hours_to_window_close: float
    seen_at: float


def pooled_bracket_sigma_f(calibration: Optional[dict],
                           lead_bucket: str = POOLED_LEAD_BUCKET
                           ) -> Tuple[Optional[float], Dict[str, object]]:
    """The flat, POOLED (not per-station) forecast-error RMSE for one lead bucket.

    Deliberately NOT `fitted_daily_extreme_sigma` (which is per-station and
    refuses a station with no fit): this basket's whole premise is a single
    instrument-width argument that holds independent of which station it is
    on, so it reads the SAME pooled number weather_sigma_calibration.json
    already carries at `pooled.daily_high.by_lead[lead_bucket].rmse_f`,
    rather than a second, hand-maintained config value that could drift from
    the artifact a refit actually writes.

    Returns `(sigma_f, features)`. `None` on anything short of a positive
    number at that exact key - a missing artifact, a missing bucket and a
    non-positive value are different facts, but this basket has exactly one
    thing to do with any of them (refuse), so they share one reason
    (`bracket_sigma_unavailable`) and the row's features say which.
    """
    feats: Dict[str, object] = {
        'sigma_calibration_present': isinstance(calibration, dict)}
    if not isinstance(calibration, dict):
        feats['pooled_sigma_fit_status'] = 'calibration_artifact_missing'
        return None, feats
    feats['sigma_calibration_generated_utc'] = calibration.get('generated_utc')
    pooled = calibration.get('pooled')
    daily_high = pooled.get('daily_high') if isinstance(pooled, dict) else None
    by_lead = (daily_high.get('by_lead')
              if isinstance(daily_high, dict) else None)
    bucket = by_lead.get(lead_bucket) if isinstance(by_lead, dict) else None
    if not isinstance(bucket, dict):
        feats['pooled_sigma_fit_status'] = 'pooled_lead_bucket_missing'
        return None, feats
    value = bucket.get('rmse_f')
    if not isinstance(value, (int, float)) or not (float(value) > 0):
        feats['pooled_sigma_fit_status'] = 'pooled_rmse_not_positive'
        return None, feats
    feats.update({'pooled_sigma_fit_status': 'ok',
                  'pooled_sigma_source': 'pooled_daily_high_by_lead',
                  'pooled_sigma_lead_bucket': lead_bucket,
                  'pooled_sigma_n': bucket.get('n'),
                  'pooled_sigma_midpoint_hours': bucket.get('midpoint_hours')})
    return float(value), feats


def _contiguous(a: _RungSnapshot, b: _RungSnapshot, tol: float = 1e-6) -> bool:
    """True when rung `a`'s upper edge is rung `b`'s lower edge (half-open)."""
    return abs(a.hi_f - b.lo_f) <= tol


def find_bracket(rungs: List[_RungSnapshot], mu_f: float
                 ) -> Optional[Tuple[_RungSnapshot, _RungSnapshot,
                                    _RungSnapshot, float]]:
    """First contiguous 3-rung window covering `[c-2.7, c+2.7]`, centred close
    enough to `mu_f`.

    Proposal 033 rule 3, read literally: `c` is DEFINED as the union's own
    midpoint, so "covers `[c-2.7, c+2.7]`" is exactly the condition that the
    union's own half-width is at least 2.7F - for three contiguous 1.8F
    rungs that is 5.4F/2 = 2.7F, satisfied with equality. Generalised rather
    than hardcoded to "three 1.8F rungs" so an odd edge-of-ladder width does
    not silently pass or fail on an assumption this function does not need to
    make.

    `rungs` need not be sorted or deduplicated by caller; every station-day's
    accumulated cache is scanned each call, which is cheap (a station-day
    ladder is at most a few dozen rungs).
    """
    ordered = sorted({(r.lo_f, r.hi_f): r for r in rungs}.values(),
                     key=lambda r: r.lo_f)
    for i in range(len(ordered) - 2):
        a, b, c_rung = ordered[i], ordered[i + 1], ordered[i + 2]
        if not (_contiguous(a, b) and _contiguous(b, c_rung)):
            continue
        lo, hi = a.lo_f, c_rung.hi_f
        if (hi - lo) + 1e-9 < 2.0 * BRACKET_HALF_WIDTH_F:
            continue
        center = (lo + hi) / 2.0
        if abs(center - mu_f) > MAX_CENTER_OFFSET_F:
            continue
        return a, b, c_rung, center
    return None


def p_bracket(center: float, mu_f: float, sigma_f: float) -> float:
    """Proposal 033 rule 5, verbatim: `Phi((c+2.7-mu)/sigma) - Phi((c-2.7-mu)/sigma)`.

    Plain normal CDF difference - NOT `daily_extreme_cdf`/
    `probability_yes_daily_extreme`, which additionally floor the
    probability by the day's already-observed extreme. The proposal's own
    formula has no such floor, so this does not add one; the observed floor
    is still a live consideration inside `mu_f` itself, because `mu_f` comes
    from `DailyExtremeEstimate`, whose forecast is anchored on the SAME
    station reading the floor would use.
    """
    hi = (center + BRACKET_HALF_WIDTH_F - mu_f) / sigma_f
    lo = (center - BRACKET_HALF_WIDTH_F - mu_f) / sigma_f
    return max(0.0, min(1.0, _NORMAL.cdf(hi) - _NORMAL.cdf(lo)))


class WeatherBracketWidthMatched(PolymarketStrategy):
    """Buy a 3-rung contiguous temperature bracket as one basket.

    See the module docstring for the full thesis, the two wiring gaps this
    build surfaced (ladder exposure, weather-space exit management), and why
    neither is patched inside this file.

    Holds to resolution (proposal rule 9). `manages_exits = True` for Exit B
    (proposal rule 10, the 0.55x-cost stop) - written to the standard
    interface and tested in isolation, but currently unreachable from
    `run_weather_cycle`; see the module docstring.
    """

    strategy_name = 'PM_weather_bracket_width_matched'
    paper_mode = PAPER_MODE
    manages_exits = True
    supported_market_types = (MARKET_TYPE_WEATHER,)

    def __init__(self, model: Optional[WeatherArb] = None,
                 sigma_calibration: Optional[dict] = None,
                 lead_bucket: str = POOLED_LEAD_BUCKET,
                 min_edge: float = MIN_EDGE_VS_P_BRACKET,
                 max_cost: float = MAX_BRACKET_COST,
                 max_notional_usdc: float = MAX_BRACKET_NOTIONAL_USDC,
                 min_shares: int = MIN_SHARES_PER_LEG,
                 stop_fraction_of_cost: float = STOP_FRACTION_OF_COST,
                 cache_freshness_sec: float = LADDER_CACHE_FRESHNESS_SEC,
                 allow_station_fallback: bool = False):
        #: A private `WeatherArb`, used ONLY for its feeds and its
        #: `daily_extreme_estimate` method - read-only reuse of the sibling
        #: model's mu computation (forecast, station-minus-grid bias,
        #: observed-extreme floor). `use_fitted_sigma=False` so this basket
        #: never depends on a PER-STATION sigma fit it does not use (its
        #: sigma is the flat pooled number below); a station `WeatherArb`
        #: has never fitted would otherwise refuse here for no reason that
        #: applies to this strategy.
        self._model = model if model is not None else WeatherArb(
            use_fitted_sigma=False,
            require_observed_extreme=None,
            max_hours_to_window_close=MODEL_MAX_HOURS_TO_WINDOW_CLOSE,
            allow_station_fallback=allow_station_fallback)
        self._sigma_calibration = sigma_calibration
        self.lead_bucket = lead_bucket
        self.min_edge = min_edge
        self.max_cost = max_cost
        self.max_notional_usdc = max_notional_usdc
        self.min_shares = min_shares
        self.stop_fraction_of_cost = stop_fraction_of_cost
        self.cache_freshness_sec = cache_freshness_sec
        #: (station, local_date) -> {(lo_f, hi_f): _RungSnapshot}. See the
        #: module docstring's wiring-gap #1: this is how a ladder gets
        #: assembled when `MarketContext` only ever hands over one rung.
        self._ladder_cache: Dict[Tuple[str, str],
                                 Dict[Tuple[float, float], _RungSnapshot]] = {}
        #: market_slug -> latest observed best_bid, across every rung this
        #: instance has ever evaluated. Read by `manage_exit` to reconstruct
        #: a bracket's COMBINED bid from three single-position calls; see
        #: that method's docstring for why it cannot see its sibling legs
        #: any other way.
        self._latest_bid_by_slug: Dict[str, float] = {}
        #: (station, local_date) keys this instance has ever entered. See
        #: the module docstring: this strategy cannot see its own open
        #: positions, so "already tried today" is the only concurrency
        #: control it can honestly keep for itself; the shared risk gate
        #: enforces the actual slot cap.
        self._entered_station_days: Set[Tuple[str, str]] = set()
        #: (station, local_date) -> the `now` of the last ENTER this
        #: instance returned. Several `evaluate()` calls can land on
        #: different rungs of the SAME bracket inside the SAME cycle (each
        #: rung is its own market); without this, each would independently
        #: see the completed bracket in the cache and re-fire it.
        self._last_entry_now: Dict[Tuple[str, str], float] = {}

    @property
    def sigma_calibration(self) -> Optional[dict]:
        """The fitted-sigma artifact, loaded once, refreshed when it changes.

        Same lazy-load contract as `WeatherArb.sigma_calibration`, and reuses
        `load_sigma_calibration`'s own mtime-based cache rather than keeping
        a second one - two caches for one file on disk is a schema an editor
        of the artifact would have to remember to invalidate twice.
        """
        if self._sigma_calibration is None:
            self._sigma_calibration = load_sigma_calibration()
        return self._sigma_calibration

    # -- entry ----------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        """Decide one rung. Fires an ENTER only once its bracket completes."""
        slug = getattr(ctx.market, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            feats.setdefault('structure', 'three_rung_contiguous_bracket')
            feats.setdefault('consumes_weather_arb_daily_extreme_model', True)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market')

        now = self._model.clock(ctx)
        if now is None:
            return decide('SKIP', 'no_clock')

        question = getattr(ctx.market, 'question', None)
        if is_global_temperature_market(question):
            return decide('SKIP', 'global_temperature_market_excluded',
                          question=question)
        if not looks_like_a_temperature_market(ctx.market):
            return decide('SKIP', 'not_a_temperature_market', question=question)

        # Proposal 033: "daily high temperature markets" only. `WeatherArb`'s
        # point-in-time model (metric is None) is a different random
        # variable and this basket does not price it.
        metric = market_metric(question)
        if metric != 'daily_high':
            return decide('SKIP', 'market_metric_not_daily_high',
                          question=question, market_metric=metric)

        station, station_status = resolution_station_checked(
            ctx.market, allow_fallback=self._model.allow_station_fallback)
        if station is None:
            return decide('SKIP', station_status, question=question)

        threshold, parse_status = parse_threshold_checked(question or '')
        if threshold is None:
            return decide('SKIP', 'threshold_unparseable',
                          threshold_parse_status=parse_status)
        if threshold.lo_f is None or threshold.hi_f is None:
            # `is_ladder_rung` is True for a TAIL too (it comes from the same
            # `_ladder()` parse, just with one edge unbounded) - not what
            # this gate needs. A tail has no fixed width and cannot
            # participate in a 3-rung contiguous window the way a bounded
            # interior bucket does, so the real test is both edges present.
            return decide('SKIP', 'threshold_not_a_bounded_bucket',
                          **threshold.to_dict())

        step, step_status = reporting_step_checked(ctx.market)
        if step is None:
            return decide('SKIP', 'source_reporting_precision_unknown',
                          source_reporting_status=step_status)
        if step < 1.0:
            return decide('SKIP', 'source_precision_finer_than_ladder_step',
                          source_reporting_step_native=step)

        reading, read_status = self._model.airport_feed.observation(station)
        if reading is None:
            return decide('SKIP', 'airport_reading_unavailable',
                          airport_feed_status=read_status)

        # Same freshness gate `WeatherArb` applies before it will price
        # anything off a METAR reading (its own `max_obs_age_sec`, reused
        # rather than re-declared): a stale reading is a stale STATION
        # BIAS measurement inside `mu_f` even at a 24-48h lead, and an
        # unhealthy feed is a fact about the feed, not the forecast.
        age = reading.age_sec(now)
        if age is None:
            return decide('SKIP', 'airport_obs_time_missing')
        if age > self._model.max_obs_age_sec:
            return decide('SKIP', 'airport_obs_stale',
                          airport_obs_age_sec=round(age, 1),
                          max_obs_age_sec=self._model.max_obs_age_sec)

        estimate, estimate_status, estimate_feats = \
            self._model.daily_extreme_estimate(ctx.market, station, reading,
                                               metric, now)
        if estimate is None:
            # `estimate_status` IS the reason string (same contract
            # `WeatherArb.evaluate` relies on for this same call), but it is
            # produced by a function in another module and the skip-reason
            # AST resolver only follows same-module producers - it cannot see
            # through the indirection here the way it can inside
            # weather_arb.py itself. Spelling out the (small, closed) set of
            # statuses `daily_extreme_estimate` can return keeps every reason
            # its own name (convention 20) while staying a literal the
            # resolver - and `test_estimate_failure_propagates_as_its_own_
            # named_status` - can check.
            if estimate_status == 'station_coordinates_unknown':
                return decide('SKIP', 'station_coordinates_unknown',
                              **estimate_feats)
            if estimate_status == 'station_forecast_unavailable':
                return decide('SKIP', 'station_forecast_unavailable',
                              **estimate_feats)
            if estimate_status == 'resolution_date_unparseable':
                return decide('SKIP', 'resolution_date_unparseable',
                              **estimate_feats)
            if estimate_status == 'resolution_date_outside_forecast_window':
                return decide('SKIP',
                              'resolution_date_outside_forecast_window',
                              **estimate_feats)
            if estimate_status == 'observation_window_closed':
                return decide('SKIP', 'observation_window_closed',
                              **estimate_feats)
            if estimate_status == 'observation_window_too_far_out':
                return decide('SKIP', 'observation_window_too_far_out',
                              **estimate_feats)
            if estimate_status == 'forecast_extreme_missing_for_date':
                return decide('SKIP', 'forecast_extreme_missing_for_date',
                              **estimate_feats)
            if estimate_status == 'forecast_hour_missing_for_bias':
                return decide('SKIP', 'forecast_hour_missing_for_bias',
                              **estimate_feats)
            if estimate_status == 'daily_extreme_history_unavailable':
                return decide('SKIP', 'daily_extreme_history_unavailable',
                              **estimate_feats)
            if estimate_status == 'daily_extreme_sigma_unfitted_for_station':
                return decide('SKIP',
                              'daily_extreme_sigma_unfitted_for_station',
                              **estimate_feats)
            # `daily_extreme_estimate`'s closed set above did not match - a
            # new status was added there without a matching branch here.
            # Convention 11: this is a cannot-run (we do not know how to name
            # it), never silently folded into a status this reader chose.
            return decide('SKIP', 'daily_extreme_estimate_status_unmapped',
                          estimate_status=estimate_status, **estimate_feats)

        hours_to_close = estimate.hours_to_window_close
        if not (MIN_LEAD_HOURS <= hours_to_close <= MAX_LEAD_HOURS):
            # ONE reason for both edges of the band, per the proposal's own
            # wording ("Outside that band, skip with a distinct reason
            # code"). The direction is on the row rather than in a second
            # counter, so the two causes stay tellable apart without
            # pooling them under one name either.
            return decide('SKIP', 'outside_24_48h_lead_band',
                          hours_to_window_close=round(hours_to_close, 3),
                          min_lead_hours=MIN_LEAD_HOURS,
                          max_lead_hours=MAX_LEAD_HOURS,
                          lead_band_direction=(
                              'too_early' if hours_to_close > MAX_LEAD_HOURS
                              else 'too_late'))

        sigma_f, sigma_feats = pooled_bracket_sigma_f(self.sigma_calibration,
                                                       self.lead_bucket)
        if sigma_f is None:
            return decide('SKIP', 'bracket_sigma_unavailable', **sigma_feats)

        book = ctx.book('Yes')
        if book is None or book.best_ask is None:
            return decide('SKIP', 'bracket_leg_missing_book')

        station_day_key = (station, estimate.local_date)
        ladder = self._ladder_cache.setdefault(station_day_key, {})
        rung_key = (round(threshold.lo_f, 3), round(threshold.hi_f, 3))
        ladder[rung_key] = _RungSnapshot(
            lo_f=threshold.lo_f, hi_f=threshold.hi_f, market_slug=slug,
            best_ask=book.best_ask, best_bid=book.best_bid,
            ask_depth_at_ask=book.ask_depth(book.best_ask),
            mu_f=estimate.mu_f, hours_to_window_close=hours_to_close,
            seen_at=now)
        if slug and book.best_bid is not None:
            self._latest_bid_by_slug[slug] = book.best_bid

        found = find_bracket(list(ladder.values()), estimate.mu_f)
        if found is None:
            return decide('SKIP', 'no_contiguous_bracket_available',
                          ladder_rungs_cached=len(ladder),
                          station=station, local_date=estimate.local_date,
                          **sigma_feats)
        r0, r1, r2, center = found
        legs_snaps = [r0, r1, r2]

        stale = [r.market_slug for r in legs_snaps
                if now - r.seen_at > self.cache_freshness_sec]
        if stale:
            return decide('SKIP', 'bracket_leg_data_stale',
                          stale_legs=stale,
                          cache_freshness_sec=self.cache_freshness_sec)

        cost = round(sum(r.best_ask for r in legs_snaps), 6)
        if cost > self.max_cost:
            return decide('SKIP', 'bracket_cost_above_cap',
                          bracket_cost=cost, max_cost=self.max_cost,
                          bracket_center_f=round(center, 3))

        p_yes_bracket = p_bracket(center, estimate.mu_f, sigma_f)
        edge = p_yes_bracket - cost
        if edge < self.min_edge:
            return decide('SKIP', 'bracket_edge_below_min',
                          p_bracket=round(p_yes_bracket, 6),
                          bracket_cost=cost, edge=round(edge, 6),
                          min_edge=self.min_edge,
                          bracket_center_f=round(center, 3),
                          bracket_sigma_f=round(sigma_f, 3))

        shares = math.floor(self.max_notional_usdc / cost + 1e-9)
        if shares < self.min_shares:
            return decide('SKIP', 'bracket_unsizable_at_notional_cap',
                          bracket_cost=cost,
                          max_notional_usdc=self.max_notional_usdc,
                          affordable_shares=shares)

        # Thinnest book first (proposal rule 8): if the loop can only fill
        # part of the basket this cycle, it fails on the leg least likely to
        # have filled anyway, which is the leg order `_attempt_entry` walks.
        thin_first = sorted(legs_snaps, key=lambda r: r.ask_depth_at_ask)
        under_depth = [r.market_slug for r in thin_first
                      if r.ask_depth_at_ask < shares]
        if under_depth:
            return decide('SKIP', 'bracket_insufficient_ask_depth',
                          under_depth_legs=under_depth, shares=shares)

        if self._last_entry_now.get(station_day_key) == now:
            return decide('SKIP', 'bracket_already_entered_this_cycle')
        if station_day_key in self._entered_station_days:
            return decide('SKIP', 'bracket_already_attempted_this_station_day')

        leg_slugs = [r.market_slug for r in legs_snaps]
        legs = [Leg(outcome_side='Yes', limit_price=r.best_ask,
                    order_type='taker', market_slug=r.market_slug,
                    shares=shares, expected_price=r.best_ask)
               for r in thin_first]

        self._last_entry_now[station_day_key] = now
        self._entered_station_days.add(station_day_key)

        return decide(
            'ENTER', '', legs=legs,
            bracket_center_f=round(center, 3),
            bracket_mu_f=round(estimate.mu_f, 3),
            bracket_sigma_f=round(sigma_f, 3),
            p_bracket=round(p_yes_bracket, 6),
            bracket_cost=cost, edge=round(edge, 6), shares=shares,
            # Carried on every leg's own position (see `_attempt_entry`:
            # `feats` is the shared decision-level dict, written identically
            # to each leg's `PaperPosition.features`) so `manage_exit` can
            # find its two siblings from a single-position call. See that
            # method's docstring.
            bracket_leg_slugs=leg_slugs,
            **estimate_feats, **sigma_feats)

    # -- exit -------------------------------------------------------------

    def manage_exit(self, position, book, now: float,
                    fair_value: Optional[float] = None) -> ExitDecision:
        """Exit B: sell all 3 legs once the combined bid <= 0.55 * cost.

        DEAD CODE AS SHIPPED - see the module docstring's wiring-gap #2.
        `run_weather_cycle` never calls `manage_exits()`, so this is never
        invoked in the live loop today, exactly like `PM_fair_value_arb`'s
        and `PM_dip_arb`'s own weather-side exits. Written to the documented
        `manages_exits` interface and tested in isolation (see
        `tests/test_weather_bracket_width_matched.py`) so the day that gap
        closes this fires correctly rather than needing to be built then.

        Called ONCE PER LEG (one `PaperPosition` per rung), never per
        bracket - `manage_exits()` iterates open positions individually and
        hands each its own book, with no way to see its two siblings'
        current books in the same call. The combined bid is reconstructed
        from `self._latest_bid_by_slug`, which every `evaluate()` call on
        ANY rung keeps current; a sibling this instance has not observed
        recently enough to have a bid for is an HONEST unknown, not a
        assumed-safe one, and the position is held rather than guessed at.
        """
        def hold(reason, **feats):
            return ExitDecision('HOLD', reason,
                                position_id=position.position_id,
                                features=feats)

        cost = position.features.get('bracket_cost')
        leg_slugs = position.features.get('bracket_leg_slugs') or []
        if cost is None or not leg_slugs:
            return hold('bracket_metadata_missing')

        self_bid = book.best_bid if book is not None else None
        if self_bid is not None:
            self._latest_bid_by_slug[position.market_slug] = self_bid
        else:
            self_bid = self._latest_bid_by_slug.get(position.market_slug)

        sibling_slugs = [s for s in leg_slugs if s != position.market_slug]
        sibling_bids = [self._latest_bid_by_slug.get(s) for s in sibling_slugs]
        known = sum(1 for b in sibling_bids if b is not None)
        if self_bid is None or known < len(sibling_slugs):
            return hold('bracket_sibling_bid_unavailable',
                        bracket_cost=cost, self_bid=self_bid,
                        sibling_bids_known=known,
                        sibling_bids_needed=len(sibling_slugs))

        combined_bid = round(self_bid + sum(sibling_bids), 6)
        stop_level = round(self.stop_fraction_of_cost * float(cost), 6)
        feats = {'bracket_cost': cost, 'combined_bid': combined_bid,
                 'stop_level': stop_level,
                 'stop_fraction_of_cost': self.stop_fraction_of_cost}
        if combined_bid > stop_level:
            return hold('combined_bid_above_stop', **feats)

        return ExitDecision('EXIT', 'bracket_stop',
                            position_id=position.position_id,
                            limit_price=self_bid, shares=position.shares,
                            features=feats)
