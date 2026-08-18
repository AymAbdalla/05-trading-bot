"""Tests for PM_grid_hedge. Offline, no network, no database.

The job here is different from every other strategy test in this repo, because
this strategy is BLOCKED BY A REFUSAL rather than by market conditions. So the
tests are ordered by what actually matters:

  1. IT NEVER RETURNS ENTER. Not on the happy path, not on any skip path, not
     ever. `assert_not_enter` is called directly so the guard itself is proved
     wired rather than merely present (convention 22), and `evaluate` is run
     across the whole matrix of contexts to prove no path escapes it. If this
     file ever goes green with an ENTER in it, the strategy has started
     manufacturing maker fills, which is the exact failure the module docstring
     exists to prevent.

  2. THE BUDGET ACCOUNTING IDENTITY HOLDS. Shares round DOWN, which always
     leaves change, and two different drop causes each leave a whole slice.
     `allocated + unallocated == budget` is asserted on every ladder shape,
     including the ones where every rung drops. Convention 20: a silent
     `continue` in a filter loop is a missing number.

  3. EVERY NAMED REASON IS REACHABLE AND IS ITS OWN CAUSE. `DECISION_REASONS`
     is asserted against exactly what the tests can produce, so a reason added
     without a test, or two causes quietly merged into one string, fails here.

  4. grid_pnl IS EXACT. It is the only thing in this file the kill condition
     would ever read, and the only part that will still be correct on the day a
     maker fill model lands. It is also the part with no other check on it,
     because nothing can feed it real fills.

There is deliberately NO harness sweep and no fill simulation of any kind.
"""
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from engine.polymarket.types import (Market, Orderbook, Outcome,  # noqa: E402
                                     PriceLevel)
import strategies.polymarket.grid_hedge as gh  # noqa: E402
from strategies.polymarket.base import Decision, MarketContext  # noqa: E402
from strategies.polymarket.grid_hedge import (GridHedge, GridSide,  # noqa: E402
                                              assert_not_enter,
                                              build_grid_side, grid_pnl,
                                              grid_prices, implied_sigma_bps,
                                              realized_sigma_bps)

WINDOW_TS = 1699999800
UP_TOKEN = 'UP'
DOWN_TOKEN = 'DN'

# A book shape that clears every gate: 2c spreads, 100 shares a side.
ASK_UP, BID_UP = 0.55, 0.53
ASK_DOWN, BID_DOWN = 0.47, 0.45


def _book(token, asks=(), bids=()):
    return Orderbook(
        token_id=token,
        asks=tuple(PriceLevel(p, s) for p, s in sorted(asks)),
        bids=tuple(PriceLevel(p, s) for p, s in sorted(bids, reverse=True)),
    )


def _market(slug='btc-updown-5m-{}'.format(WINDOW_TS)):
    return Market(id=slug, question=slug, slug=slug, condition_id='c-' + slug,
                  outcomes=(Outcome('Up', UP_TOKEN),
                            Outcome('Down', DOWN_TOKEN)))


def _ctx(up_asks=((ASK_UP, 100),), up_bids=((BID_UP, 100),),
         down_asks=((ASK_DOWN, 100),), down_bids=((BID_DOWN, 100),),
         atr14=5.0, lead_bps=20.0, seconds_into_window=60.0,
         market='default', books='default'):
    if market == 'default':
        market = _market()
    if books == 'default':
        books = {}
        if up_asks is not None or up_bids is not None:
            books[UP_TOKEN] = _book(UP_TOKEN, up_asks or (), up_bids or ())
        if down_asks is not None or down_bids is not None:
            books[DOWN_TOKEN] = _book(DOWN_TOKEN, down_asks or (),
                                      down_bids or ())
    return MarketContext(window_ts=WINDOW_TS, market=market, books=books,
                         seconds_into_window=seconds_into_window,
                         atr14=atr14, lead_bps=lead_bps)


def _cases():
    """One context per named reason, plus the QUOTE path. Reused everywhere."""
    return {
        'no_market': _ctx(market=None),
        'both_books_unavailable': _ctx(books={}),
        'one_book_unavailable': _ctx(down_asks=None, down_bids=None),
        'no_asks': _ctx(up_asks=()),
        'spread_undefined_no_bid': _ctx(up_bids=()),
        'spread_too_wide_for_grid': _ctx(up_bids=((0.40, 100),)),
        'book_too_thin_for_grid': _ctx(up_asks=((ASK_UP, 10),)),
        'grid_budget_exhausted': _ctx(),
        'vol_inputs_unavailable': _ctx(atr14=None),
        'implied_vol_inputs_unavailable': _ctx(lead_bps=None),
        'no_window_clock': _ctx(seconds_into_window=None),
        'implied_vol_undefined_at_the_money': _ctx(
            up_asks=((0.51, 100),), up_bids=((0.49, 100),)),
        'implied_vol_sign_inconsistent': _ctx(lead_bps=-20.0),
        'implied_vol_below_realized': _ctx(atr14=5000.0),
        'maker_fill_not_simulated': _ctx(),
    }


def _decisions():
    """Every case, evaluated. `grid_budget_exhausted` needs a tiny budget."""
    out = {}
    for reason, ctx in _cases().items():
        if reason == 'grid_budget_exhausted':
            strategy = GridHedge(grid_budget_usdc=5.0)
        else:
            strategy = GridHedge()
        out[reason] = strategy.evaluate(ctx)
    return out


# ============ 0. house rules ============

def test_paper_mode_true_in_the_module_and_on_the_class():
    assert gh.PAPER_MODE is True
    assert GridHedge().paper_mode is True
    assert GridHedge.paper_mode is True


def test_module_states_a_kill_condition_with_a_number_and_a_harness():
    """Convention 6. And it must say the condition is UNMEASURABLE today."""
    doc = gh.__doc__ or ''
    assert 'KILL CONDITION' in doc
    assert '-$5.00' in doc
    assert '50 grid fills' in doc
    assert 'grid_pnl' in doc
    assert 'backtest/polymarket_harness.py' in doc
    assert 'UNMEASURABLE' in doc


def test_module_says_it_is_blocked_by_a_refusal_not_by_a_bug():
    doc = gh.__doc__ or ''
    assert 'BLOCKED BY A REFUSAL' in doc
    assert 'maker fill model' in doc


def test_the_class_declares_itself_a_maker_strategy():
    assert GridHedge.uses_maker_orders is True
    assert GridHedge.strategy_name == 'PM_grid_hedge'


def test_no_em_dash_and_no_double_hyphen_inside_a_word():
    """House writing rule. Section-rule comment lines are runs of hyphens and
    are not prose, so only word-adjacent double hyphens are checked."""
    path = gh.__file__.replace('.pyc', '.py')
    with open(path, 'r') as fh:
        text = fh.read()
    assert '—' not in text
    assert re.search(r'\w--\w', text) is None
    assert re.search(r'\w -- \w', text) is None


# ============ 1. IT NEVER RETURNS ENTER ============

def test_assert_not_enter_raises_on_enter_and_passes_everything_else():
    with pytest.raises(AssertionError) as excinfo:
        assert_not_enter('ENTER')
    assert 'maker' in str(excinfo.value)
    assert assert_not_enter('QUOTE') == 'QUOTE'
    assert assert_not_enter('SKIP') == 'SKIP'


def test_no_context_in_the_whole_matrix_produces_an_enter():
    for reason, decision in _decisions().items():
        assert decision.action in ('SKIP', 'QUOTE'), (reason, decision.action)
        assert decision.is_entry is False, reason


def test_the_happy_path_is_a_quote_carrying_the_whole_ladder():
    decision = GridHedge().evaluate(_ctx())
    assert decision.action == 'QUOTE'
    assert decision.reason == 'maker_fill_not_simulated'
    assert decision.legs
    assert {leg.order_type for leg in decision.legs} == {'maker'}
    assert {leg.outcome_side for leg in decision.legs} == {'Up', 'Down'}


def test_a_quote_never_becomes_a_signal():
    """base.decision_to_signal only maps ENTER. A QUOTE must not reach the
    scanner as a tradeable signal, or the refusal is cosmetic."""
    strategy = GridHedge()
    assert strategy.decision_to_signal(strategy.evaluate(_ctx())) is None


def test_the_shadow_loop_can_tell_this_is_a_maker_quote_from_the_features():
    feats = GridHedge().evaluate(_ctx()).features
    assert feats['fill_model'] == 'maker_fills_not_simulated'
    assert feats['uses_maker_orders'] is True
    assert feats['blocked_by_refusal_not_by_bug'] is True
    assert feats['kill_condition_is_currently_unmeasurable'] is True


# ============ 2. the ladder geometry ============

def test_grid_prices_step_down_from_the_ask_on_the_tick_grid():
    assert grid_prices(0.55, 5, 0.03) == [0.52, 0.49, 0.46, 0.43, 0.40]


def test_grid_prices_honours_a_custom_level_count():
    assert grid_prices(0.55, 2, 0.03) == [0.52, 0.49]
    assert grid_prices(0.55, 0, 0.03) == []


def test_shares_round_down_and_the_leftover_is_reported_not_dropped():
    side = build_grid_side('Up', 0.55, 25.0, levels=5, spacing=0.03)
    assert side.per_rung_budget_usdc == pytest.approx(5.0)
    # floor(5.00 / price) at each rung, always DOWN.
    assert [(r.price, r.shares) for r in side.rungs] == [
        (0.52, 9), (0.49, 10), (0.46, 10), (0.43, 11), (0.40, 12)]
    for rung in side.rungs:
        assert rung.shares * rung.price <= rung.budget_usdc + 1e-12
        assert rung.leftover_usdc == pytest.approx(
            5.0 - rung.shares * rung.price)
    assert side.allocated_usdc == pytest.approx(23.71)
    assert side.unallocated_usdc == pytest.approx(1.29)


def test_the_budget_accounting_identity_holds_on_a_full_ladder():
    side = build_grid_side('Up', 0.55, 25.0)
    assert side.allocated_usdc + side.unallocated_usdc == pytest.approx(
        side.budget_usdc)


def test_rungs_below_the_price_floor_are_dropped_and_counted():
    side = build_grid_side('Up', 0.10, 25.0, levels=5, spacing=0.03)
    # 0.07 and 0.04 survive the 0.02 floor; 0.01, -0.02, -0.05 do not.
    assert [r.price for r in side.rungs] == [0.07, 0.04]
    assert side.rungs_below_floor == 3
    assert side.rungs_unaffordable == 0
    assert side.allocated_usdc + side.unallocated_usdc == pytest.approx(25.0)
    # The three dropped slices are findable in unallocated, not vanished.
    assert side.unallocated_usdc >= 3 * side.per_rung_budget_usdc - 1e-9


def test_rungs_that_cannot_afford_the_minimum_size_are_dropped_and_counted():
    side = build_grid_side('Up', 0.55, 5.0, levels=5, spacing=0.03)
    # per_rung is 1.00; floor(1.00 / 0.52) is 1 share, under the 5-share min.
    assert side.rungs == []
    assert side.rungs_unaffordable == 5
    assert side.rungs_below_floor == 0
    assert side.unallocated_usdc == pytest.approx(5.0)


def test_the_two_drop_causes_are_counted_separately_never_pooled():
    """Convention 20. One number for two causes is a missing number.

    Ask 0.10 on a 1.00 budget makes both causes fire at once: 0.07 is above the
    floor but can only afford 2 shares, and the last three rungs fall through
    the floor entirely.
    """
    side = build_grid_side('Up', 0.10, 1.0, levels=5, spacing=0.03)
    assert side.rungs_unaffordable == 1
    assert side.rungs_below_floor == 3
    assert [r.price for r in side.rungs] == [0.04]
    assert side.allocated_usdc + side.unallocated_usdc == pytest.approx(1.0)


def test_per_rung_budget_uses_the_configured_count_not_the_surviving_count():
    """Dividing by survivors would inflate the remaining rungs every time one
    dropped, which is a position that grows when the book gets worse."""
    side = build_grid_side('Up', 0.10, 25.0, levels=5, spacing=0.03)
    assert len(side.rungs) == 2
    assert side.per_rung_budget_usdc == pytest.approx(25.0 / 5)


def test_a_zero_level_ladder_leaves_the_whole_budget_unallocated():
    side = build_grid_side('Up', 0.55, 25.0, levels=0)
    assert side.rungs == []
    assert side.unallocated_usdc == pytest.approx(25.0)


def test_both_sides_get_exactly_half_the_budget_never_split_by_price():
    strategy = GridHedge(grid_budget_usdc=50.0)
    up, down = strategy.build_grid(0.55, 0.47)
    assert up.budget_usdc == pytest.approx(25.0)
    assert down.budget_usdc == pytest.approx(25.0)
    assert isinstance(up, GridSide) and isinstance(down, GridSide)


def test_max_loss_is_the_whole_allocation_because_the_stop_is_zero():
    """Convention 8: on a binary a losing share is worth exactly 0.00, and that
    IS the stop. No separate stop price exists in this strategy."""
    side = build_grid_side('Up', 0.55, 25.0)
    assert side.max_loss_usdc == pytest.approx(side.allocated_usdc)
    feats = GridHedge().evaluate(_ctx()).features
    assert feats['stop_is_zero_because_a_losing_binary_is_worth_zero'] is True


def test_the_quote_legs_match_the_ladder_exactly():
    decision = GridHedge().evaluate(_ctx())
    feats = decision.features
    expected = feats['grid_up']['rung_count'] + feats['grid_down']['rung_count']
    assert len(decision.legs) == expected == feats['leg_count']
    up_legs = [lg for lg in decision.legs if lg.outcome_side == 'Up']
    assert [lg.limit_price for lg in up_legs] == [
        r['price'] for r in feats['grid_up']['rungs']]
    assert [lg.shares for lg in up_legs] == [
        r['shares'] for r in feats['grid_up']['rungs']]


# ============ 3. grid_pnl ============

def test_grid_pnl_on_a_winner_and_a_loser_with_no_fee():
    result = grid_pnl([(0.40, 0.43, 10), (0.50, 0.47, 10)])
    assert result['fills'] == 2
    assert result['gross_usdc'] == pytest.approx(0.0)
    assert result['fees_usdc'] == pytest.approx(0.0)
    assert result['net_usdc'] == pytest.approx(0.0)
    assert result['winners'] == 1
    assert result['losers'] == 1
    assert result['scratches'] == 0


def test_grid_pnl_charges_the_fee_on_both_legs():
    result = grid_pnl([(0.40, 0.43, 10)], fee_rate=0.01)
    # cost 4.00, proceeds 4.30, fee 1% of 8.30 = 0.083.
    assert result['gross_usdc'] == pytest.approx(0.30)
    assert result['fees_usdc'] == pytest.approx(0.083)
    assert result['net_usdc'] == pytest.approx(0.217)
    assert result['per_fill'][0]['net_usdc'] == pytest.approx(0.217)


def test_grid_pnl_accepts_mappings_as_well_as_triples():
    triples = grid_pnl([(0.40, 0.43, 10)])
    mappings = grid_pnl([{'rung_price': 0.40, 'exit_price': 0.43,
                          'shares': 10}])
    assert triples['net_usdc'] == mappings['net_usdc']


def test_grid_pnl_on_an_empty_list_is_zero_over_zero_fills():
    result = grid_pnl([])
    assert result['fills'] == 0
    assert result['net_usdc'] == 0.0


def test_grid_pnl_stamps_the_fills_as_hypothetical():
    """Nothing in this repo can produce a real one, and a log reader must not be
    able to mistake these for measured fills."""
    result = grid_pnl([(0.4, 0.43, 10)])
    assert result['fills_are_hypothetical_maker_fills'] is True


def test_a_fifty_fill_ladder_reaches_the_kill_condition_arithmetic():
    """Not evidence of anything. It proves the kill condition's number is
    computable from grid_pnl once something can feed it, and nothing more."""
    result = grid_pnl([(0.40, 0.30, 10)] * 50)
    assert result['fills'] == 50
    assert result['net_usdc'] < -5.00


@pytest.mark.parametrize('bad', [
    (1.5, 0.4, 10),           # price above 1.00
    (-0.1, 0.4, 10),          # negative price
    (0.4, 1.2, 10),           # exit above 1.00
    (0.4, 0.5, 0),            # zero shares
    (0.4, 0.5, -3),           # negative shares
    (float('nan'), 0.5, 10),  # convention 19
    (0.4, float('inf'), 10),
    ('x', 0.5, 10),
    (0.4, 0.5),               # wrong arity
])
def test_grid_pnl_raises_on_garbage_rather_than_returning_zero(bad):
    """Returning 0.0 for a malformed fill reads identically to "the grid made
    nothing", which files a code defect under the strategy's performance."""
    with pytest.raises(ValueError):
        grid_pnl([bad])


def test_grid_pnl_raises_on_a_negative_fee_rate():
    with pytest.raises(ValueError):
        grid_pnl([(0.4, 0.5, 10)], fee_rate=-0.01)


# ============ 4. the vol comparison ============

def test_implied_sigma_inverts_the_normal_cdf():
    # Phi(1) is 0.8413, so a 20bps lead at p=0.8413 implies sigma of 20bps.
    sigma, status = implied_sigma_bps(20.0, 0.841344746)
    assert status == 'ok'
    assert sigma == pytest.approx(20.0, rel=1e-4)


def test_implied_sigma_is_undefined_at_exactly_the_money():
    sigma, status = implied_sigma_bps(20.0, 0.5)
    assert sigma is None
    assert status == 'implied_vol_undefined_at_the_money'


@pytest.mark.parametrize('p', [0.0, 1.0, -0.2, 1.4])
def test_implied_sigma_refuses_a_probability_outside_the_open_unit_interval(p):
    sigma, status = implied_sigma_bps(20.0, p)
    assert sigma is None
    assert status == 'implied_vol_undefined_at_the_money'


def test_a_negative_lead_against_an_above_fifty_book_is_sign_inconsistent():
    """The book and the strike proxy disagree about which side is ahead. A
    data-quality fact, and it must not be pooled with a vol reading."""
    sigma, status = implied_sigma_bps(-20.0, 0.60)
    assert sigma is None
    assert status == 'implied_vol_sign_inconsistent'


def test_realized_sigma_converts_a_mean_absolute_move_and_scales_by_sqrt_time():
    full = realized_sigma_bps(10.0, 300.0)
    assert full == pytest.approx(10.0 / math.sqrt(2.0 / math.pi))
    quarter = realized_sigma_bps(10.0, 75.0)
    assert quarter == pytest.approx(full * 0.5)
    assert realized_sigma_bps(10.0, 0.0) == 0.0


def test_a_missing_atr_and_a_missing_lead_are_two_different_reasons():
    """Convention 20. Different owners, different fixes, never one gate."""
    no_atr = GridHedge().evaluate(_ctx(atr14=None))
    no_lead = GridHedge().evaluate(_ctx(lead_bps=None))
    assert no_atr.reason == 'vol_inputs_unavailable'
    assert no_atr.features['missing_input'] == 'atr14'
    assert no_lead.reason == 'implied_vol_inputs_unavailable'
    assert no_lead.features['missing_input'] == 'lead_bps'
    assert no_atr.reason != no_lead.reason


def test_implied_below_realized_is_a_result_and_carries_both_numbers():
    decision = GridHedge().evaluate(_ctx(atr14=5000.0))
    assert decision.reason == 'implied_vol_below_realized'
    assert decision.features['implied_vol_exceeds_realized'] is False
    assert decision.features['implied_sigma_bps'] is not None
    assert decision.features['realized_sigma_bps'] is not None


def test_the_row_says_atr14_must_be_in_basis_points():
    """The corridor_collector unit trap: a USD ATR here is 10,000x too small and
    the gate silently never passes."""
    assert GridHedge().evaluate(_ctx()).features['atr14_must_be_in_bps'] is True


# ============ 5. every named reason is reachable and is its own cause ============

def test_evaluate_always_returns_a_decision_and_never_none():
    for reason, decision in _decisions().items():
        assert isinstance(decision, Decision), reason
        assert decision.strategy == 'PM_grid_hedge', reason


def test_each_constructed_case_produces_exactly_the_reason_it_is_named_for():
    for reason, decision in _decisions().items():
        assert decision.reason == reason, (reason, decision.reason)


def test_decision_reasons_tuple_has_no_duplicates():
    assert len(gh.DECISION_REASONS) == len(set(gh.DECISION_REASONS))


def test_decision_reasons_tuple_matches_exactly_what_the_tests_can_reach():
    assert set(_decisions()) == set(gh.DECISION_REASONS)


def test_one_missing_book_is_a_different_fact_from_two_missing_books():
    """Half a grid is a directional ladder, not a hedge. Pooling the two would
    hide that."""
    both = GridHedge().evaluate(_ctx(books={}))
    one = GridHedge().evaluate(_ctx(down_asks=None, down_bids=None))
    assert both.reason == 'both_books_unavailable'
    assert one.reason == 'one_book_unavailable'
    assert one.features['has_book_up'] is True
    assert one.features['has_book_down'] is False


def test_an_unmeasurable_spread_is_not_a_narrow_spread():
    no_bid = GridHedge().evaluate(_ctx(up_bids=()))
    wide = GridHedge().evaluate(_ctx(up_bids=((0.40, 100),)))
    assert no_bid.reason == 'spread_undefined_no_bid'
    assert wide.reason == 'spread_too_wide_for_grid'


def test_the_depth_gate_is_labelled_a_liveness_proxy_not_a_fill_model():
    """Whether OUR resting bid fills is not observable from a snapshot at all,
    and the row must not let a reader think otherwise."""
    decision = GridHedge().evaluate(_ctx(up_asks=((ASK_UP, 10),)))
    assert decision.reason == 'book_too_thin_for_grid'
    assert decision.features[
        'depth_is_a_liveness_proxy_not_a_fill_model'] is True


def test_a_budget_that_buys_no_rung_is_a_cannot_run_not_a_market_view():
    decision = GridHedge(grid_budget_usdc=5.0).evaluate(_ctx())
    assert decision.reason == 'grid_budget_exhausted'
    assert decision.features['grid_up']['rungs_unaffordable'] == 5
    assert decision.features['grid_down']['rungs_unaffordable'] == 5


def test_skips_after_the_ladder_is_built_still_carry_the_whole_ladder():
    """So the row a fill model would want to replay is not thrown away just
    because the vol comparison refused."""
    decisions = _decisions()
    for reason in ('vol_inputs_unavailable', 'implied_vol_inputs_unavailable',
                   'no_window_clock', 'implied_vol_below_realized'):
        feats = decisions[reason].features
        assert feats['grid_up']['rungs'], reason
        assert feats['grid_down']['rungs'], reason
        assert feats['total_rungs'] > 0, reason
