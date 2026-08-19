"""Tests for PM_longshot_fade_hold_to_resolution (proposal 032).

Seven jobs, matching the handoff (`docs/handoffs/from-raven/
2026-08-18-complete-032.md`):

  1. **Sigma estimation**: the window tape's sample stdev, and the
     below-twenty-windows skip. `TestSigmaEstimation`.
  2. **t_rem math**: sigma_rem = sigma_window_bps * sqrt(t_rem / T), and the
     sigma_rem <= 0 skip. `TestTRemMath`.
  3. **d/z/p_tail**: normal-tail arithmetic spot-checked against known z
     values (z=0 -> p_tail=0.5, z=1.96 -> p_tail=0.025). `TestZAndPTail`.
  4. **Entry gates, all required**: each of the five gates fails alone on its
     own named skip. `TestEntryGates`.
  5. **Size cap**: fixed 10 USD notional, max 2 concurrent positions per
     instance. `TestSizeCap`.
  6. **Exit**: hold to resolution (no converged-mid / time-based exit) plus
     Exit B, the thesis-invalidation stop. `TestExit`.
  7. **Registry**: 23 strategies, this one at index 22, first 8 unchanged.
     `TestRegistry`.
"""
import math
import os
import sys
from statistics import NormalDist, stdev

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.paper_adapter import PaperPosition  # noqa: E402
from engine.polymarket.types import (Market, Orderbook, Outcome,  # noqa: E402
                                     PriceLevel)
from strategies.polymarket.base import (MARKET_TYPE_CRYPTO_UPDOWN,  # noqa: E402
                                        MarketContext, Window)
from strategies.polymarket.fair_value_arb import URGENT_SELL_LIMIT  # noqa: E402
from strategies.polymarket.longshot_fade_hold_to_resolution import (  # noqa: E402
    MAX_CONCURRENT_POSITIONS, MAX_FAVORITE_ASK, MIN_EDGE_VS_FAIR,
    MIN_FAVORITE_ASK, MIN_WINDOWS_FOR_SIGMA, NOTIONAL_USDC,
    STRIKE_PROXY_NOISE_FLOOR_BPS, TAIL_BID_MULTIPLE, THESIS_INVALIDATION_BID_CEILING,
    LongshotFadeHoldToResolution)

TS15 = 1755000000                          # a clean multiple of 900
FINAL_THIRD_OFFSET = 600                   # 900 - 300, the 5m sub-window's own start
WINDOW_TS = TS15 + FINAL_THIRD_OFFSET
UP_TOK = 'tok-up'
DOWN_TOK = 'tok-down'
SLUG_5M = 'btc-updown-5m-{}'.format(WINDOW_TS)
SLUG_15M = 'btc-updown-15m-{}'.format(TS15)
OPEN_15M = 100_000.0

#: Synthetic 20-window move series with real variance, reused everywhere a
#: seeded sigma tape is needed so every test's sigma_window_bps is the exact
#: same, independently-computable number.
MOVES_BPS = [((-1) ** i) * (5.0 + i) for i in range(MIN_WINDOWS_FOR_SIGMA)]
SIGMA_WINDOW_BPS = stdev(MOVES_BPS)
T_REM_BASELINE = 240.0                     # seconds_into_window = 60.0
SIGMA_REM_BASELINE = SIGMA_WINDOW_BPS * math.sqrt(T_REM_BASELINE / 900.0)
_NORMAL = NormalDist(0.0, 1.0)


def _spot_for_z(z, open_15m=OPEN_15M, sigma_rem=SIGMA_REM_BASELINE,
               floor_bps=STRIKE_PROXY_NOISE_FLOOR_BPS):
    """Inverts the strategy's own d_adj/z formula: the spot that produces
    exactly this z, given the baseline sigma_rem. Solving for an input given
    a desired output is not the same as re-deriving the gate logic itself."""
    d_bps = z * sigma_rem + floor_bps
    return open_15m * (1.0 + d_bps / 10000.0)


# ============ fixtures ============

def _market_5m():
    return Market(id='m5', question='btc updown 5m', slug=SLUG_5M,
                 condition_id='c5', outcomes=(Outcome('Up', UP_TOK),
                                              Outcome('Down', DOWN_TOK)))


def _market_15m(slug=SLUG_15M):
    return Market(id='m15', question='btc updown 15m', slug=slug,
                 condition_id='c15', outcomes=(Outcome('Up', UP_TOK),
                                               Outcome('Down', DOWN_TOK)))


def _book(token, asks=(), bids=()):
    return Orderbook(
        token_id=token,
        bids=tuple(PriceLevel(float(p), float(s)) for p, s in bids),
        asks=tuple(PriceLevel(float(p), float(s)) for p, s in asks),
        timestamp=WINDOW_TS)


def _ctx(seconds_into_window=60.0, spot=None, open_15m=OPEN_15M,
        up_asks=(), up_bids=(), down_asks=(), down_bids=(),
        market_5m=True, market_15m=True, has_window_open=True,
        slug_15m=SLUG_15M):
    if spot is None:
        spot = _spot_for_z(4.0, open_15m=open_15m)
    books_15m = {}
    if up_asks or up_bids:
        books_15m[UP_TOK] = _book(UP_TOK, up_asks, up_bids)
    if down_asks or down_bids:
        books_15m[DOWN_TOK] = _book(DOWN_TOK, down_asks, down_bids)
    windows = ([Window(ts=TS15, open=open_15m, close=open_15m, direction='UP')]
              if has_window_open else [])
    return MarketContext(
        window_ts=WINDOW_TS, windows=windows,
        market=_market_5m() if market_5m else None,
        market_15m=_market_15m(slug=slug_15m) if market_15m else None,
        books={}, books_15m=books_15m, spot=spot,
        seconds_into_window=seconds_into_window,
        market_type=MARKET_TYPE_CRYPTO_UPDOWN)


def _seed_tape(strategy, asset='btc', moves_bps=MOVES_BPS, start_ts=None):
    """Feeds `len(moves_bps)` completed 15m windows into `strategy.tape`,
    ending with the CURRENT in-progress bucket at `TS15` (matching every
    `_ctx()` fixture's own `ts15`) so a seeded strategy can be evaluated
    immediately without disturbing the count just seeded."""
    n = len(moves_bps)
    if start_ts is None:
        start_ts = TS15 - n * 900
    base = 100_000.0
    for i, bps in enumerate(moves_bps):
        ts = start_ts + i * 900
        close = base * (1.0 + bps / 10000.0)
        strategy.tape.observe(asset, ts, base)
        strategy.tape.observe(asset, ts, close)
    strategy.tape.observe(asset, start_ts + n * 900, base)


def _baseline_ctx(**overrides):
    """A context where every entry gate passes: z=4.0 (p_tail negligible),
    a_fav=0.94 (within band), ample depth and a tail bid clear of its floor."""
    params = dict(seconds_into_window=60.0, spot=_spot_for_z(4.0),
                 open_15m=OPEN_15M, up_asks=((0.94, 500.0),),
                 down_bids=((0.01, 500.0),))
    params.update(overrides)
    return _ctx(**params)


def _position(entry=0.94, shares=10.0, open_15m=OPEN_15M, position_id='pos-1',
             include_features=True):
    features = {'open_15m': open_15m} if include_features else {}
    return PaperPosition(
        position_id=position_id, strategy='PM_longshot_fade_hold_to_resolution',
        market_slug=SLUG_15M, token_id=UP_TOK, outcome_side='Up',
        shares=shares, avg_price=entry, cost_usdc=entry * shares, fee_usdc=0.0,
        opened_ts=WINDOW_TS, window_ts=WINDOW_TS, features=features)


# ============ 1. sigma estimation ============

class TestSigmaEstimation:

    def test_sigma_window_bps_matches_the_sample_stdev_of_the_moves(self):
        s = LongshotFadeHoldToResolution()
        _seed_tape(s)
        assert s.tape.count('btc') == MIN_WINDOWS_FOR_SIGMA
        assert s.tape.sigma_window_bps('btc', s.min_windows_for_sigma) == \
            pytest.approx(SIGMA_WINDOW_BPS)

    def test_below_twenty_windows_is_insufficient_window_history(self):
        s = LongshotFadeHoldToResolution()
        _seed_tape(s, moves_bps=MOVES_BPS[:19])
        assert s.tape.count('btc') == 19
        d = s.evaluate(_baseline_ctx())
        assert d.reason == 'insufficient_window_history'
        assert d.features['windows_stored'] == 19
        assert d.features['windows_required'] == MIN_WINDOWS_FOR_SIGMA


# ============ 2. t_rem math ============

class TestTRemMath:

    def test_sigma_rem_in_features_matches_the_formula(self):
        s = LongshotFadeHoldToResolution()
        _seed_tape(s)
        d = s.evaluate(_baseline_ctx())
        assert d.action == 'ENTER'
        assert d.features['t_rem'] == pytest.approx(T_REM_BASELINE)
        assert d.features['sigma_window_bps'] == pytest.approx(SIGMA_WINDOW_BPS,
                                                                abs=1e-3)
        assert d.features['sigma_rem_bps'] == pytest.approx(SIGMA_REM_BASELINE,
                                                             abs=1e-3)

    def test_zero_variance_tape_gives_non_positive_sigma_rem_skip(self):
        s = LongshotFadeHoldToResolution()
        _seed_tape(s, moves_bps=[0.0] * MIN_WINDOWS_FOR_SIGMA)
        d = s.evaluate(_baseline_ctx())
        assert d.reason == 'non_positive_sigma_rem'
        assert d.features['sigma_window_bps'] == pytest.approx(0.0)
        assert d.features['sigma_rem'] == pytest.approx(0.0)


# ============ 3. d / z / p_tail ============

class TestZAndPTail:

    def test_spot_at_window_open_gives_z_zero_and_p_tail_one_half(self):
        s = LongshotFadeHoldToResolution()
        _seed_tape(s)
        # abs(d_bps) == the noise floor exactly -> d_adj == 0 -> z == 0.
        spot = OPEN_15M * (1.0 + STRIKE_PROXY_NOISE_FLOOR_BPS / 10000.0)
        d = s.evaluate(_baseline_ctx(spot=spot, up_asks=(), down_bids=()))
        assert d.features['z'] == pytest.approx(0.0, abs=1e-9)
        assert d.features['p_tail'] == pytest.approx(0.5, abs=1e-9)
        assert d.features['fair_value_favorite'] == pytest.approx(0.5, abs=1e-9)

    def test_z_196_gives_p_tail_about_0_025(self):
        s = LongshotFadeHoldToResolution()
        _seed_tape(s)
        spot = _spot_for_z(1.96)
        d = s.evaluate(_baseline_ctx(spot=spot, up_asks=(), down_bids=()))
        assert d.features['z'] == pytest.approx(1.96, abs=1e-6)
        expected_p_tail = 1.0 - _NORMAL.cdf(1.96)
        assert d.features['p_tail'] == pytest.approx(expected_p_tail, abs=1e-6)
        assert expected_p_tail == pytest.approx(0.025, abs=1e-3)


# ============ 4. entry gates, all required ============

class TestEntryGates:

    def test_all_gates_pass_enters_on_the_favorite(self):
        s = LongshotFadeHoldToResolution()
        _seed_tape(s)
        d = s.evaluate(_baseline_ctx())
        assert d.action == 'ENTER'
        assert len(d.legs) == 1
        leg = d.legs[0]
        assert leg.outcome_side == 'Up'
        assert leg.market_slug == SLUG_15M
        assert leg.shares == 10
        assert d.features['favorite_side'] == 'Up'
        assert d.features['primary_exit'] == 'hold_to_resolution'
        assert d.features['exits_before_resolution'] is False
        assert d.features['has_thesis_invalidation_stop'] is True

    def test_t_rem_outside_entry_window_skips(self):
        s = LongshotFadeHoldToResolution()
        _seed_tape(s)
        # t_rem = 300 - 290 = 10, below the 60s floor.
        d = s.evaluate(_baseline_ctx(seconds_into_window=290.0))
        assert d.reason == 't_rem_outside_entry_window'

    def test_favorite_ask_outside_entry_band_skips(self):
        s = LongshotFadeHoldToResolution()
        _seed_tape(s)
        d = s.evaluate(_baseline_ctx(up_asks=((0.80, 500.0),)))
        assert d.reason == 'favorite_ask_outside_entry_band'

    def test_edge_below_min_skips(self):
        s = LongshotFadeHoldToResolution()
        _seed_tape(s)
        # z=1.96 -> fair_fav ~= 0.975; a_fav=0.96 is in-band but the edge
        # (~0.015) is below the 0.025 floor.
        spot = _spot_for_z(1.96)
        d = s.evaluate(_baseline_ctx(spot=spot, up_asks=((0.96, 500.0),)))
        assert d.reason == 'edge_below_min'
        assert d.features['edge_vs_fair'] < MIN_EDGE_VS_FAIR

    def test_tail_bid_already_converged_with_model_skips(self):
        s = LongshotFadeHoldToResolution()
        _seed_tape(s)
        # Same z=1.96 fair value, but a_fav=0.93 leaves enough edge (~0.045)
        # to pass that gate - only the tail bid is deliberately too low here.
        spot = _spot_for_z(1.96)
        d = s.evaluate(_baseline_ctx(spot=spot, up_asks=((0.93, 500.0),),
                                     down_bids=((0.01, 500.0),)))
        assert d.reason == 'tail_bid_already_converged_with_model'
        assert d.features['tail_best_bid'] < d.features['tail_bid_floor']

    def test_insufficient_ask_depth_skips(self):
        s = LongshotFadeHoldToResolution()
        _seed_tape(s)
        d = s.evaluate(_baseline_ctx(up_asks=((0.94, 3.0),)))
        assert d.reason == 'insufficient_ask_depth'
        assert d.features['shares'] > d.features['favorite_ask_depth']


# ============ 5. size cap ============

class TestSizeCap:

    def test_notional_is_fixed_at_ten_usd(self):
        assert NOTIONAL_USDC == 10.0
        s = LongshotFadeHoldToResolution()
        _seed_tape(s)
        d = s.evaluate(_baseline_ctx())
        assert d.action == 'ENTER'
        assert d.features['notional_usdc_target'] == 10.0
        assert d.features['shares'] == int(10.0 // 0.94)

    def test_same_window_twice_is_already_entered(self):
        s = LongshotFadeHoldToResolution()
        _seed_tape(s)
        d1 = s.evaluate(_baseline_ctx())
        assert d1.action == 'ENTER'
        d2 = s.evaluate(_baseline_ctx())
        assert d2.reason == 'already_entered_this_window'

    def test_max_two_concurrent_then_third_is_capped(self):
        assert MAX_CONCURRENT_POSITIONS == 2
        s = LongshotFadeHoldToResolution()
        _seed_tape(s)
        d1 = s.evaluate(_baseline_ctx(slug_15m='btc-updown-15m-a'))
        d2 = s.evaluate(_baseline_ctx(slug_15m='btc-updown-15m-b'))
        d3 = s.evaluate(_baseline_ctx(slug_15m='btc-updown-15m-c'))
        assert d1.action == 'ENTER'
        assert d2.action == 'ENTER'
        assert d3.action == 'SKIP'
        assert d3.reason == 'strategy_concurrency_cap_reached'
        assert d3.features['open_count'] == 2


# ============ 6. exit ============

class TestExit:

    def test_estimate_usable_in_the_final_third(self):
        s = LongshotFadeHoldToResolution()
        est = s.estimate(_baseline_ctx())
        assert est.usable is True
        assert est.spot == pytest.approx(_spot_for_z(4.0))

    def test_estimate_unusable_outside_the_final_third(self):
        s = LongshotFadeHoldToResolution()
        ctx = _baseline_ctx()
        # Move window_ts off the final-third offset without touching anything
        # else - offset becomes 300 (the middle third).
        ctx.window_ts = TS15 + 300
        est = s.estimate(ctx)
        assert est.usable is False
        assert est.reason == 'not_final_third_of_15m'

    def test_estimate_unusable_with_no_spot_or_market(self):
        s = LongshotFadeHoldToResolution()
        est = s.estimate(_baseline_ctx(market_15m=False))
        assert est.usable is False
        assert est.reason == 'no_spot_or_15m_market'

    def test_for_side_returns_spot_for_up_or_down_and_refuses_anything_else(self):
        s = LongshotFadeHoldToResolution()
        est = s.estimate(_baseline_ctx())
        assert est.for_side('Up') == pytest.approx(_spot_for_z(4.0))
        assert est.for_side('down') == pytest.approx(_spot_for_z(4.0))
        with pytest.raises(ValueError):
            est.for_side('Yes')

    def test_thesis_intact_holds(self):
        s = LongshotFadeHoldToResolution()
        pos = _position()
        book = _book(UP_TOK, bids=((0.95, 500.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS + 60.0,
                          fair_value=_spot_for_z(4.0))
        assert d.action == 'HOLD'
        assert d.reason == 'thesis_intact'

    def test_thesis_invalidated_and_bid_at_or_below_ceiling_exits(self):
        s = LongshotFadeHoldToResolution()
        pos = _position()
        book = _book(UP_TOK, bids=((0.75, 500.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS + 60.0, fair_value=OPEN_15M)
        assert d.action == 'EXIT'
        assert d.reason == 'thesis_invalidated_spot_crossed_strike'
        assert d.limit_price == URGENT_SELL_LIMIT
        assert d.limit_price == 0.0
        assert d.shares == pos.shares

    def test_thesis_invalidated_but_bid_above_ceiling_still_holds(self):
        assert THESIS_INVALIDATION_BID_CEILING == 0.80
        s = LongshotFadeHoldToResolution()
        pos = _position()
        book = _book(UP_TOK, bids=((0.90, 500.0),))  # above the 0.80 ceiling
        d = s.manage_exit(pos, book, now=WINDOW_TS + 60.0, fair_value=OPEN_15M)
        assert d.action == 'HOLD'
        assert d.reason == 'thesis_intact'

    def test_no_orderbook_holds(self):
        s = LongshotFadeHoldToResolution()
        pos = _position()
        d = s.manage_exit(pos, None, now=WINDOW_TS, fair_value=OPEN_15M)
        assert d.action == 'HOLD'
        assert d.reason == 'no_orderbook'

    def test_no_bid_liquidity_holds(self):
        s = LongshotFadeHoldToResolution()
        pos = _position()
        book = _book(UP_TOK, asks=((0.96, 500.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS, fair_value=OPEN_15M)
        assert d.action == 'HOLD'
        assert d.reason == 'no_bid_liquidity'

    def test_no_live_spot_holds(self):
        s = LongshotFadeHoldToResolution()
        pos = _position()
        book = _book(UP_TOK, bids=((0.95, 500.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS, fair_value=None)
        assert d.action == 'HOLD'
        assert d.reason == 'no_live_spot_for_stop'

    def test_missing_strike_reference_holds(self):
        s = LongshotFadeHoldToResolution()
        pos = _position(include_features=False)
        book = _book(UP_TOK, bids=((0.95, 500.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS, fair_value=_spot_for_z(4.0))
        assert d.action == 'HOLD'
        assert d.reason == 'no_strike_reference_for_stop'

    def test_unreadable_position_holds(self):
        s = LongshotFadeHoldToResolution()
        pos = _position(shares=0.0)
        book = _book(UP_TOK, bids=((0.95, 500.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS, fair_value=_spot_for_z(4.0))
        assert d.action == 'HOLD'
        assert d.reason == 'unreadable_position'

    def test_no_time_based_exit_holds_regardless_of_elapsed_time(self):
        """No converged-mid sale, no time-based sale - `now` is accepted by
        the signature (interface compatibility) and never inspected."""
        s = LongshotFadeHoldToResolution()
        pos = _position()
        book = _book(UP_TOK, bids=((0.95, 500.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS + 100_000.0,
                          fair_value=_spot_for_z(4.0))
        assert d.action == 'HOLD'
        assert d.reason == 'thesis_intact'


# ============ 7. registry ============

class TestRegistry:

    def test_registry_is_twenty_three_and_this_strategy_is_index_22(self):
        from strategies.polymarket import build_strategies
        names = [s.strategy_name for s in build_strategies()]
        assert len(names) == 23
        assert names[:8] == [
            'PM_streak_snapper', 'PM_mid_price_continuation', 'PM_box_builder',
            'PM_corridor_collector', 'PM_temporal_arbitrage', 'PM_corridor_pair',
            'PM_spread_harvest_taker', 'PM_fair_value_arb',
        ]
        assert names[22] == 'PM_longshot_fade_hold_to_resolution'
