"""Tests for strategies/polymarket/ (D-267, D-268).

Same two jobs as tests/test_strategy_lab_v3.py:
  1. no strategy may ever raise, whatever garbage it is handed
  2. every strategy must be provably ALIVE - a synthetic context that satisfies
     its rules produces an entry, and a one-condition-off context does not

Job 2 matters more here than anywhere else in the repo. These four are ports of
somebody else's bots, they are NOT_TESTED by decision (D-268), and the whole
edge of a binary lives in a price cap. A port that silently moved 0.52 to 0.55,
or that never fires at all, looks identical in the graveyard to a strategy that
was honestly measured and failed.

So there is a test per cap: 52c (streak), 0.55 (mid-price and the corridor's 5m
leg), $0.94 (the box pair), 1.03 (the box's arm gate), 0.93 (the corridor's 15m
leg), plus the corridor's binned P(corridor) table, because a flat 0.413 there
loosens the price gate by 8.7c at a 6bps lead.

There is deliberately NO harness sweep here. Per D-268 these strategies are
NOT_TESTED until a resolution-PnL harness exists; running the price-path harness
on them would fabricate numbers.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.types import (Market, Orderbook, Outcome,  # noqa: E402
                                     PriceLevel)
from strategies.polymarket import (BoxBuilder, CorridorCollector,  # noqa: E402
                                   MidPriceContinuation, StreakSnapper,
                                   build_strategies, cap_bids,
                                   p_corridor_lookup)
from strategies.polymarket.base import MarketContext, Window  # noqa: E402
import strategies.polymarket.box_builder as box_mod  # noqa: E402
import strategies.polymarket.corridor_collector as corridor_mod  # noqa: E402
import strategies.polymarket.mid_price_continuation as mid_mod  # noqa: E402
import strategies.polymarket.streak_snapper as streak_mod  # noqa: E402


# ============ synthetic fixture builders ============

def _book(token, asks=(), bids=()):
    """asks/bids as (price, size) tuples, any order - Orderbook sorts nothing,
    so hand them best-first the way engine.polymarket.orderbook does."""
    return Orderbook(
        token_id=token,
        asks=tuple(PriceLevel(p, s) for p, s in sorted(asks)),
        bids=tuple(PriceLevel(p, s) for p, s in sorted(bids, reverse=True)),
    )


def _market(slug='btc-updown-5m-1000', up_token='UP', down_token='DN'):
    return Market(
        id=slug, question=slug, slug=slug, condition_id='c-' + slug,
        outcomes=(Outcome('Up', up_token), Outcome('Down', down_token)),
    )


def _windows(moves, start=60000.0, source='price', ts0=1000):
    """Build completed Windows from a list of signed USD moves."""
    out = []
    price = start
    for i, mv in enumerate(moves):
        o = price
        c = price + mv
        out.append(Window(ts=ts0 + 300 * i, open=o, close=c,
                          direction='UP' if c >= o else 'DOWN', source=source))
        price = c
    return out


def _alternating(n, mag=10.0):
    """n windows of +mag / -mag, ending DOWN so a following run is a clean streak.

    Built backwards from the end so the last element is -mag whatever the
    parity of `n`. The old form keyed off `i % 2` from the START, so an odd `n`
    ended UP and merged into a following UP run - silently handing the caller a
    streak one longer than it asked for.
    """
    return [(-mag if (n - 1 - i) % 2 == 0 else mag) for i in range(n)]


# -- streak_snapper contexts -------------------------------------------------

STREAK_SHARES = 19  # floor($10 / 0.52), his SIZE_USD sizing


def _streak_ctx(run_move=100.0, run_len=4, seconds_into_window=5,
                asks=((0.50, 10), (0.52, 20)), with_market=True):
    # Hold the TOTAL window count at MIN_WINDOWS so that lowering run_len
    # tests the streak rule and not the history-length guard. A plain
    # `_alternating(12) + [run] * 3` is 15 windows, one short of the 16
    # StreakSnapper requires, so it short-circuits on
    # `insufficient_window_history` and never counts the streak at all.
    moves = (_alternating(streak_mod.MIN_WINDOWS - run_len)
             + [run_move] * run_len)
    windows = _windows(moves)
    market = _market() if with_market else None
    books = {}
    if with_market:
        # The fade side of an UP run is Down.
        books = {'DN': _book('DN', asks=asks, bids=((0.45, 50),))}
    return MarketContext(
        window_ts=windows[-1].ts + 300, windows=windows,
        market=market, books=books, seconds_into_window=seconds_into_window,
    )


# -- mid_price contexts ------------------------------------------------------

def _mid_ctx(itm=0.001, seconds_into_window=100.0,
             asks=((0.48, 20),), with_market=True):
    strike = 60000.0
    spot = strike * (1.0 + itm)
    market = _market() if with_market else None
    books = {'UP': _book('UP', asks=asks, bids=((0.45, 50),))} if with_market else {}
    return MarketContext(
        window_ts=1000, windows=_windows(_alternating(16)),
        market=market, books=books, spot=spot, strike=strike,
        seconds_into_window=seconds_into_window,
    )


# -- box_builder contexts ----------------------------------------------------

def _box_ctx(ask_up=0.55, ask_down=0.50, bid_up=0.45, bid_down=0.44,
             seconds_into_window=10.0):
    market = _market()
    books = {
        'UP': _book('UP', asks=((ask_up, 50),),
                    bids=() if bid_up is None else ((bid_up, 50),)),
        'DN': _book('DN', asks=((ask_down, 50),),
                    bids=() if bid_down is None else ((bid_down, 50),)),
    }
    return MarketContext(window_ts=1000, market=market, books=books,
                         seconds_into_window=seconds_into_window)


# -- corridor contexts -------------------------------------------------------

def _corridor_ctx(lead_bps=12.0, atr14=6.0, ask_15=0.80, ask_5=0.50,
                  depth=20, seconds_into_window=30.0, window_ts=1500):
    """D-283: the default window is the FINAL THIRD of its 15m parent.

    1500 // 900 == 1, so the parent opens at 900 and 1500 - 900 == 600. The old
    default of 1600 is the second third, which the D-283 check now refuses, so
    every gate below it was unreachable and the fixture measured nothing.
    """
    ts15 = (window_ts // 900) * 900
    m5 = _market('btc-updown-5m-{}'.format(window_ts))
    m15 = Market(id='m15', question='m15',
                 slug='btc-updown-15m-{}'.format(ts15),
                 condition_id='c15',
                 outcomes=(Outcome('Up', 'UP15'), Outcome('Down', 'DN15')))
    return MarketContext(
        window_ts=window_ts, windows=_windows(_alternating(16)),
        market=m5, books={'DN': _book('DN', asks=((ask_5, depth),))},
        market_15m=m15, books_15m={'UP15': _book('UP15', asks=((ask_15, depth),))},
        lead_bps=lead_bps, atr14=atr14,
        seconds_into_window=seconds_into_window,
    )


# ============ 0. house rules ============

def test_paper_mode_true_in_every_module_and_class():
    """moondevonyt's originals ship PAPER_MODE = False. Ours must not."""
    for mod in (streak_mod, mid_mod, box_mod, corridor_mod):
        assert mod.PAPER_MODE is True, mod.__name__
    for strategy in build_strategies():
        assert strategy.paper_mode is True, strategy.name


def test_no_moondev_api_dependency_anywhere_in_the_package():
    """No MoonDev API, no MOONDEV_API_KEY, no key-bearing feed (D-267)."""
    import strategies.polymarket as pkg
    pkg_dir = os.path.dirname(os.path.abspath(pkg.__file__))
    banned = ('MOONDEV_API_KEY', 'moondev_api', 'moondev.com', 'api.moondev',
              'MOON_DEV_API_PATH', 'moon-dev-trading-bots')
    for fname in sorted(os.listdir(pkg_dir)):
        if not fname.endswith('.py'):
            continue
        with open(os.path.join(pkg_dir, fname)) as fh:
            text = fh.read()
        for token in banned:
            assert token not in text, f'{fname} references {token}'


def test_every_strategy_implements_the_house_interface():
    for strategy in build_strategies():
        assert isinstance(strategy.name, str) and strategy.name
        assert strategy.is_entry is True
        assert callable(strategy.scan)
        assert callable(strategy.evaluate)


def test_no_strategy_raises_on_garbage():
    empty = MarketContext(window_ts=0)
    garbage_candles = [
        {}, {'closes': []}, {'closes': [1.0], 'timestamps': [1]},
        {'closes': [float('nan')] * 20, 'timestamps': list(range(20))},
    ]
    for strategy in build_strategies():
        decision = strategy.evaluate(empty)
        assert decision.action in ('ENTER', 'QUOTE', 'SKIP')
        assert decision.reason, f'{strategy.name} skipped without a reason'
        for candles in garbage_candles:
            assert strategy.scan(candles) is None


def test_scan_without_a_book_never_invents_an_entry():
    """The scanner path has no orderbook, so no strategy may return a Signal."""
    candles = {
        'timestamps': [1000 + 300 * i for i in range(20)],
        'opens': [60000.0 + 100 * i for i in range(20)],
        'closes': [60100.0 + 100 * i for i in range(20)],
        'highs': [60200.0 + 100 * i for i in range(20)],
        'lows': [59900.0 + 100 * i for i in range(20)],
        'volumes': [1.0] * 20,
    }
    for strategy in build_strategies():
        assert strategy.scan(candles) is None


# ============ 1. streak_snapper ============

def test_streak_snapper_fires():
    decision = StreakSnapper().evaluate(_streak_ctx())
    assert decision.action == 'ENTER', decision.reason
    assert decision.features['streak_len'] == 4
    assert decision.features['streak_dir'] == 'UP'
    assert decision.features['stretch_ratio'] > 3.0
    leg = decision.primary_leg
    assert leg.outcome_side == 'Down'          # fade an UP run
    assert leg.limit_price == 0.52
    assert leg.shares == STREAK_SHARES


def test_streak_snapper_entry_is_the_walked_premium_not_the_cap():
    """D-268: entry is the per-share premium. 19 shares eat two levels."""
    decision = StreakSnapper().evaluate(_streak_ctx())
    expected = (10 * 0.50 + 9 * 0.52) / 19.0
    assert decision.primary_leg.expected_price == pytest.approx(expected)
    signal = StreakSnapper().decision_to_signal(decision)
    assert signal is not None
    assert signal.entry == pytest.approx(expected)
    assert signal.entry < 0.52                 # cheaper than the cap
    assert signal.stop == 0.00                 # a losing share is worth zero
    assert signal.target == 1.00
    assert signal.stop < signal.entry          # convention 8
    assert signal.direction == 'bearish'


def test_streak_snapper_52c_cap_is_hard():
    decision = StreakSnapper().evaluate(_streak_ctx(asks=((0.53, 100),)))
    assert decision.action == 'SKIP'
    assert decision.reason == 'ask_above_cap'


def test_streak_snapper_takes_exactly_52c():
    decision = StreakSnapper().evaluate(_streak_ctx(asks=((0.52, 100),)))
    assert decision.action == 'ENTER'
    assert decision.primary_leg.expected_price == pytest.approx(0.52)


def test_streak_snapper_needs_four_windows():
    decision = StreakSnapper().evaluate(_streak_ctx(run_len=3))
    assert decision.action == 'SKIP'
    assert decision.reason == 'no_streak'
    assert decision.features['streak_len'] == 3


def test_streak_snapper_needs_three_atr_of_stretch():
    decision = StreakSnapper().evaluate(_streak_ctx(run_move=5.0))
    assert decision.action == 'SKIP'
    assert decision.reason == 'not_stretched'
    assert decision.features['streak_len'] == 4      # the run is there
    assert decision.features['stretch_ratio'] <= 3.0  # the stretch is not


def test_streak_snapper_first_20_seconds_only():
    assert StreakSnapper().evaluate(
        _streak_ctx(seconds_into_window=20)).action == 'ENTER'
    late = StreakSnapper().evaluate(_streak_ctx(seconds_into_window=21))
    assert late.action == 'SKIP'
    assert late.reason == 'late_in_window'


def test_streak_snapper_needs_depth_for_the_full_size():
    thin = StreakSnapper().evaluate(_streak_ctx(asks=((0.50, 5),)))
    assert thin.action == 'SKIP'
    assert thin.reason == 'insufficient_ask_depth'


def test_streak_snapper_refuses_oracle_only_windows():
    """Oracle windows carry direction only; the stretch filter needs magnitude."""
    ctx = _streak_ctx()
    ctx.windows = [Window(w.ts, w.open, w.close, w.direction, 'oracle')
                   for w in ctx.windows]
    decision = StreakSnapper().evaluate(ctx)
    assert decision.action == 'SKIP'
    assert decision.reason == 'no_magnitude_data'


# ============ 2. mid_price_continuation ============

def test_mid_price_fires():
    decision = MidPriceContinuation().evaluate(_mid_ctx())
    assert decision.action == 'ENTER', decision.reason
    assert decision.features['leading_side'] == 'Up'
    assert decision.features['itm_bps'] == pytest.approx(10.0)
    leg = decision.primary_leg
    assert leg.outcome_side == 'Up'
    assert leg.limit_price == 0.55            # the order's limit is the cap
    assert leg.expected_price == pytest.approx(0.48)   # what we actually pay
    signal = MidPriceContinuation().decision_to_signal(decision)
    assert signal.entry == pytest.approx(0.48)
    assert signal.stop == 0.00 and signal.target == 1.00
    assert signal.direction == 'bullish'


def test_mid_price_fires_on_the_down_side_too():
    decision = MidPriceContinuation().evaluate(_mid_ctx(itm=-0.001))
    # Leading side is Down, and _mid_ctx only books 'UP', so this must not
    # silently trade the wrong side.
    assert decision.features['leading_side'] == 'Down'
    assert decision.action == 'SKIP'
    assert decision.reason == 'no_orderbook'


def test_mid_price_055_cap_never_chases():
    """Top of book inside the cap but the size is not: no entry, no chase."""
    decision = MidPriceContinuation().evaluate(
        _mid_ctx(asks=((0.54, 5), (0.60, 100))))
    assert decision.action == 'SKIP'
    assert decision.reason == 'insufficient_ask_depth'
    # And a book entirely above the cap is likewise untouchable.
    above = MidPriceContinuation().evaluate(_mid_ctx(asks=((0.56, 100),)))
    assert above.action == 'SKIP'
    assert above.features['best_ask'] == 0.56


def test_mid_price_takes_exactly_055():
    decision = MidPriceContinuation().evaluate(_mid_ctx(asks=((0.55, 100),)))
    assert decision.action == 'ENTER'
    assert decision.primary_leg.expected_price == pytest.approx(0.55)


def test_mid_price_040_floor():
    decision = MidPriceContinuation().evaluate(_mid_ctx(asks=((0.39, 100),)))
    assert decision.action == 'SKIP'
    assert decision.reason == 'effective_ask_below_band'
    assert MidPriceContinuation().evaluate(
        _mid_ctx(asks=((0.40, 100),))).action == 'ENTER'


def test_mid_price_needs_5bps_through_the_strike():
    """His gate is 0.05 PERCENT. 0.05 as a ratio would be a 5% five-minute move."""
    assert MidPriceContinuation().evaluate(
        _mid_ctx(itm=0.0004)).reason == 'not_through_strike'
    assert MidPriceContinuation().evaluate(_mid_ctx(itm=0.0005)).action == 'ENTER'
    assert MidPriceContinuation().evaluate(_mid_ctx(itm=0.05)).action == 'ENTER'


def test_mid_price_time_band():
    late = MidPriceContinuation().evaluate(_mid_ctx(seconds_into_window=200.0))
    assert late.reason == 'too_close_to_resolution'   # 100s left
    early = MidPriceContinuation().evaluate(_mid_ctx(seconds_into_window=-1.0))
    assert early.reason == 'window_not_open'          # 301s left
    assert MidPriceContinuation().evaluate(
        _mid_ctx(seconds_into_window=180.0)).action == 'ENTER'   # 120s left


def test_mid_price_skips_without_a_strike():
    ctx = _mid_ctx()
    ctx.strike = None
    decision = MidPriceContinuation().evaluate(ctx)
    assert decision.action == 'SKIP'
    assert decision.reason == 'no_spot_or_strike'


# ============ 3. box_builder ============

def test_box_builder_quotes_both_sides():
    decision = BoxBuilder().evaluate(_box_ctx())
    assert decision.action == 'QUOTE', decision.reason
    sides = sorted(lg.outcome_side for lg in decision.legs)
    assert sides == ['Down', 'Up']
    assert all(lg.order_type == 'maker' for lg in decision.legs)
    assert decision.features['pair_cost'] <= 0.94


def test_box_builder_never_returns_enter():
    """Maker fills are not simulated. QUOTE is not an entry (D-268)."""
    strategy = BoxBuilder()
    decision = strategy.evaluate(_box_ctx())
    assert decision.is_entry is False
    assert strategy.decision_to_signal(decision) is None
    assert strategy.uses_maker_orders is True


def test_box_builder_arms_only_on_a_wide_book():
    tight = BoxBuilder().evaluate(_box_ctx(ask_up=0.51, ask_down=0.51))
    assert tight.action == 'SKIP'
    assert tight.reason == 'book_too_tight_to_arm'
    assert tight.features['ask_sum'] == pytest.approx(1.02)
    # Exactly 1.03 arms.
    edge = BoxBuilder().evaluate(_box_ctx(ask_up=0.52, ask_down=0.51))
    assert edge.features['ask_sum'] == pytest.approx(1.03)
    assert edge.action == 'QUOTE'


def test_box_builder_094_pair_cap_backs_off_from_the_bids():
    decision = BoxBuilder().evaluate(
        _box_ctx(bid_up=0.50, bid_down=0.50, ask_up=0.60, ask_down=0.55))
    quotes = {lg.outcome_side: lg.limit_price for lg in decision.legs}
    assert quotes['Up'] + quotes['Down'] <= 0.94 + 1e-9
    assert quotes == {'Up': 0.47, 'Down': 0.47}
    assert decision.features['gross_edge_per_pair'] >= 0.06


def test_box_builder_lowballs_it_never_quotes_above_the_bid():
    """The 0.94 is a CEILING, not a budget to spend. A cheap book stays cheap."""
    decision = BoxBuilder().evaluate(
        _box_ctx(bid_up=0.30, bid_down=0.30, ask_up=0.60, ask_down=0.55))
    quotes = {lg.outcome_side: lg.limit_price for lg in decision.legs}
    assert quotes == {'Up': 0.30, 'Down': 0.30}
    assert decision.features['gross_edge_per_pair'] == pytest.approx(0.40)


def test_cap_bids_backs_off_the_higher_leg_first():
    assert cap_bids(0.80, 0.30)[0] < 0.80        # higher leg gives first
    assert sum(cap_bids(0.80, 0.30)) <= 0.94 + 1e-9
    assert cap_bids(0.45, 0.44) == (0.45, 0.44)  # already under the cap
    assert cap_bids(None, 0.44) is None          # an absent bid is not zero


def test_box_builder_skips_a_one_sided_book():
    decision = BoxBuilder().evaluate(_box_ctx(bid_down=None))
    assert decision.action == 'SKIP'
    assert decision.reason == 'no_bids_to_join'


def test_box_builder_first_half_of_the_window_only():
    assert BoxBuilder().evaluate(
        _box_ctx(seconds_into_window=150.0)).action == 'QUOTE'
    late = BoxBuilder().evaluate(_box_ctx(seconds_into_window=151.0))
    assert late.action == 'SKIP'
    assert late.reason == 'past_quote_window'


def test_box_builder_completion_lift_is_a_real_taker_entry():
    ctx = _box_ctx()
    strategy = BoxBuilder()
    decision = strategy.completion_lift(ctx, 'Up', 0.45)
    assert decision.action == 'ENTER', decision.reason
    assert decision.features['lift_cap'] == pytest.approx(0.54)
    leg = decision.primary_leg
    assert leg.outcome_side == 'Down'
    assert leg.expected_price == pytest.approx(0.50)   # the ask we cross
    signal = strategy.decision_to_signal(decision)
    assert signal.entry == pytest.approx(0.50)
    assert decision.features['gross_edge_per_pair'] == pytest.approx(0.05)


def test_box_builder_completion_lift_refuses_an_unprofitable_pair():
    strategy = BoxBuilder()
    expensive = strategy.completion_lift(_box_ctx(ask_down=0.60), 'Up', 0.45)
    assert expensive.action == 'SKIP'
    assert expensive.reason == 'completion_ask_above_cap'
    # First leg so rich that no completion can total under $1.00.
    hopeless = strategy.completion_lift(_box_ctx(), 'Up', 0.99)
    assert hopeless.action == 'SKIP'
    assert hopeless.reason == 'no_profitable_completion'


# ============ 4. corridor_collector ============

def test_corridor_collector_fires():
    decision = CorridorCollector().evaluate(_corridor_ctx())
    assert decision.action == 'ENTER', decision.reason
    assert decision.features['lead_side_15m'] == 'Up'
    assert decision.features['opposite_side_5m'] == 'Down'
    assert len(decision.legs) == 2
    leader, opposite_leg = decision.legs
    assert leader.outcome_side == 'Up'
    assert leader.market_slug == 'btc-updown-15m-900'
    assert leader.expected_price == pytest.approx(0.80)
    assert opposite_leg.outcome_side == 'Down'
    assert opposite_leg.market_slug == 'btc-updown-5m-1500'
    assert opposite_leg.expected_price == pytest.approx(0.50)
    signal = CorridorCollector().decision_to_signal(decision)
    assert signal.entry == pytest.approx(0.80)        # premium, not the 0.93 cap
    assert signal.features['multi_leg'] is True
    assert len(signal.features['legs']) == 2


def test_corridor_collector_at_least_one_leg_always_wins():
    """Structural, not empirical: both legs settle off the same close."""
    decision = CorridorCollector().evaluate(_corridor_ctx())
    assert decision.features['payoff_floor'] == 1.00
    assert decision.features['floor_is_structural_not_empirical'] is True
    assert decision.features['worst_case_pnl_per_pair'] == pytest.approx(
        1.00 - 1.30)


def test_corridor_collector_uses_the_binned_corridor_table():
    """A flat 0.413 loosens the price gate by 8.7c at a 6bps lead."""
    assert p_corridor_lookup(1.0) == 0.072
    assert p_corridor_lookup(6.0) == 0.326
    assert p_corridor_lookup(12.0) == 0.405
    assert p_corridor_lookup(25.0) == 0.464
    assert p_corridor_lookup(999.0) == 0.497
    # 1.33 clears a flat-0.413 gate (max 1.333) but not the table's 1.246.
    decision = CorridorCollector().evaluate(
        _corridor_ctx(lead_bps=6.0, atr14=3.0, ask_15=0.80, ask_5=0.53))
    assert decision.features['p_corridor'] == 0.326
    assert decision.features['max_pair_cost'] == pytest.approx(1.246)
    assert decision.action == 'SKIP'
    assert decision.reason == 'pair_cost_above_edge_threshold'


def test_corridor_collector_price_gate_needs_8c_of_edge():
    ok = CorridorCollector().evaluate(_corridor_ctx(ask_15=0.80, ask_5=0.52))
    assert ok.features['max_pair_cost'] == pytest.approx(1.325)
    assert ok.action == 'ENTER'                        # 1.32 <= 1.325
    rich = CorridorCollector().evaluate(_corridor_ctx(ask_15=0.83, ask_5=0.50))
    assert rich.action == 'SKIP'                       # 1.33 > 1.325
    assert rich.reason == 'pair_cost_above_edge_threshold'


def test_corridor_collector_ask_caps():
    over5 = CorridorCollector().evaluate(_corridor_ctx(ask_5=0.56, ask_15=0.60))
    assert over5.action == 'SKIP'
    assert over5.reason == 'ask_5m_above_cap'
    over15 = CorridorCollector().evaluate(_corridor_ctx(ask_15=0.94, ask_5=0.30))
    assert over15.action == 'SKIP'
    assert over15.reason == 'ask_15m_above_cap'
    # Exactly on both caps is allowed; the price gate is what stops it.
    on_cap = CorridorCollector().evaluate(_corridor_ctx(ask_15=0.55, ask_5=0.55))
    assert on_cap.action == 'ENTER'


def test_corridor_collector_sweet_zone():
    assert CorridorCollector().evaluate(
        _corridor_ctx(lead_bps=4.9)).reason == 'lead_below_zone'
    assert CorridorCollector().evaluate(
        _corridor_ctx(lead_bps=30.1, atr14=6.0)).reason == 'lead_above_zone'
    # Lead inside the noise: 12bps against a 20bps ATR is 0.6x.
    quiet = CorridorCollector().evaluate(_corridor_ctx(atr14=20.0))
    assert quiet.reason == 'lead_inside_noise'
    assert quiet.features['lead_atr_ratio'] == pytest.approx(0.6)


def test_corridor_collector_first_90_seconds_only():
    assert CorridorCollector().evaluate(
        _corridor_ctx(seconds_into_window=90.0)).action == 'ENTER'
    late = CorridorCollector().evaluate(_corridor_ctx(seconds_into_window=91.0))
    assert late.action == 'SKIP'
    assert late.reason == 'late_in_window'


def test_corridor_collector_needs_depth_on_both_legs():
    thin = CorridorCollector().evaluate(_corridor_ctx(depth=4))
    assert thin.action == 'SKIP'
    assert thin.reason == 'insufficient_depth_for_pair'


def test_corridor_collector_refuses_the_first_and_second_thirds():
    """D-283. The $1.00 floor exists ONLY because both markets settle off the
    same close, which is true only for the FINAL 5m third of a 15m window.

    On the first or second third the two legs settle on DIFFERENT closes and
    BOTH CAN LOSE - and nothing in the pricing would tell you, because the pair
    still costs the same and `worst_case_pnl_per_pair` would still report a
    floor that is not there. moondevonyt's bot omits this check.

    Checked BEFORE lead, price and depth, so a window on the wrong third can
    never be talked into an entry by an attractive book.
    """
    for window_ts, offset in ((900, 0), (1200, 300)):
        decision = CorridorCollector().evaluate(_corridor_ctx(window_ts=window_ts))
        assert decision.action == 'SKIP', window_ts
        assert decision.reason == 'not_final_third_of_15m', window_ts
        assert decision.features['parent_15m_ts'] == 900
        assert decision.features['offset_sec'] == offset
        assert decision.features['required_offset_sec'] == 600
        assert decision.features['floor_is_structural_not_empirical'] is True

    # Same context on the final third, and every later gate is reachable again.
    assert CorridorCollector().evaluate(
        _corridor_ctx(window_ts=1500)).action == 'ENTER'


def test_corridor_collector_needs_both_markets():
    ctx = _corridor_ctx()
    ctx.market_15m = None
    decision = CorridorCollector().evaluate(ctx)
    assert decision.action == 'SKIP'
    assert decision.reason == 'missing_market_leg'


# ============ 5. every strategy documents a kill condition (convention 6) ============

@pytest.mark.parametrize('module', [streak_mod, mid_mod, box_mod, corridor_mod])
def test_kill_condition_is_documented(module):
    doc = (module.__doc__ or '').upper()
    assert 'KILL CONDITION' in doc, module.__name__
    assert '30BPS' in doc.replace(' ', ''), (
        f'{module.__name__} must state the D-268 default: dies below 30bps net')
