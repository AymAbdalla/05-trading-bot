"""Tests for PM_status_quo_collector.

Six jobs:

  1. **The classifier gate: only STATUS_QUO trades.** `TestClassifierGate`.
  2. **The price band and the never-chase-above-0.90 rule.** `TestPriceBand`.
  3. **The ladder: initial, then scale_82, then scale_89, each once.**
     `TestLadder`.
  4. **Side is always NO.** `TestSideIsAlwaysNo`.
  5. **Sizing stays under 5% of the paper bankroll per rung.** `TestSizing`.
  6. **The registry**: appended after `PM_smart_money_callers`, first eight
     untouched.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.types import (Market, Orderbook, Outcome,  # noqa: E402
                                     PriceLevel)
from strategies.polymarket.base import MARKET_TYPE_POLITICAL, MarketContext  # noqa: E402
from strategies.polymarket.status_quo_collector import (          # noqa: E402
    MAX_NOTIONAL_USDC, SCALE_RUNG_1, SCALE_RUNG_2, StatusQuoCollector)

WINDOW_TS = 1755000000
NO_TOK = 'tok-no'
YES_TOK = 'tok-yes'
SLUG = 'will-putin-remain-president-until-2027'
STATUS_QUO_QUESTION = 'Will Vladimir Putin remain president of Russia until 2027?'
CHANGE_EVENT_QUESTION = 'Will Donald Trump win the 2028 presidential election?'
UNKNOWN_QUESTION = 'Will there be a major earthquake this year?'


# ============ fixtures ============

def _market(slug=SLUG, question=STATUS_QUO_QUESTION, end_date='2027-01-01',
           is_binary=True):
    outcomes = ((Outcome('Yes', YES_TOK), Outcome('No', NO_TOK)) if is_binary
               else (Outcome('Yes', YES_TOK),))
    return Market(id='m1', question=question, slug=slug, condition_id='c1',
                 outcomes=outcomes, active=True, closed=False,
                 end_date=end_date, volume=50000.0)


def _book(token, asks=(), bids=()):
    return Orderbook(
        token_id=token,
        bids=tuple(PriceLevel(float(p), float(s)) for p, s in bids),
        asks=tuple(PriceLevel(float(p), float(s)) for p, s in asks),
        timestamp=WINDOW_TS)


def _ctx(no_asks=((0.85, 200.0),), market=True, market_type=MARKET_TYPE_POLITICAL,
         **market_kwargs):
    books = {NO_TOK: _book(NO_TOK, no_asks, ((0.83, 200.0),))}
    return MarketContext(window_ts=WINDOW_TS,
                        market=_market(**market_kwargs) if market else None,
                        books=books, market_type=market_type)


def _strategy(**kwargs):
    return StatusQuoCollector(**kwargs)


# ============ 1. classifier gate ============

class TestClassifierGate:

    def test_status_quo_question_can_enter(self):
        s = _strategy()
        decision = s.evaluate(_ctx(question=STATUS_QUO_QUESTION))
        assert decision.action == 'ENTER'
        assert decision.features['classifier_label'] == 'STATUS_QUO'

    def test_change_event_question_never_enters(self):
        s = _strategy()
        decision = s.evaluate(_ctx(question=CHANGE_EVENT_QUESTION))
        assert decision.action == 'SKIP'
        assert decision.reason == 'classifier_change_event_shape'

    def test_unknown_question_never_enters(self):
        s = _strategy()
        decision = s.evaluate(_ctx(question=UNKNOWN_QUESTION))
        assert decision.action == 'SKIP'
        assert decision.reason == 'classifier_unknown_shape'

    def test_no_market_is_its_own_reason(self):
        s = _strategy()
        decision = s.evaluate(_ctx(market=False))
        assert decision.reason == 'no_market'

    def test_no_resolution_date_never_enters(self):
        s = _strategy()
        decision = s.evaluate(_ctx(end_date=None))
        assert decision.action == 'SKIP'
        assert decision.reason == 'no_resolution_date'

    def test_non_binary_market_never_enters(self):
        s = _strategy()
        decision = s.evaluate(_ctx(is_binary=False))
        assert decision.action == 'SKIP'
        assert decision.reason == 'not_binary'


# ============ 2. price band ============

class TestPriceBand:

    def test_below_080_never_enters(self):
        s = _strategy()
        decision = s.evaluate(_ctx(no_asks=((0.75, 200.0),)))
        assert decision.reason == 'price_outside_entry_band'

    def test_above_090_never_enters(self):
        s = _strategy()
        decision = s.evaluate(_ctx(no_asks=((0.93, 200.0),)))
        assert decision.reason == 'price_outside_entry_band'

    def test_exactly_080_can_enter(self):
        s = _strategy()
        decision = s.evaluate(_ctx(no_asks=((0.80, 200.0),)))
        assert decision.action == 'ENTER'

    def test_exactly_090_can_enter(self):
        s = _strategy()
        decision = s.evaluate(_ctx(no_asks=((0.90, 200.0),)))
        assert decision.action == 'ENTER'

    def test_never_pays_above_090_even_if_book_would_walk_higher(self):
        s = _strategy()
        # Best ask in band, but the book only has size ABOVE the cap once the
        # first level is exhausted - effective_ask_for must refuse rather
        # than walk through 0.90.
        decision = s.evaluate(_ctx(no_asks=((0.85, 1.0), (0.95, 500.0))))
        assert decision.reason in ('unfillable_at_cap', 'insufficient_ask_depth')
        assert decision.action == 'SKIP'


# ============ 3. the ladder ============

class TestLadder:

    def test_first_entry_is_the_initial_rung(self):
        s = _strategy()
        decision = s.evaluate(_ctx(no_asks=((0.81, 200.0),)))
        assert decision.action == 'ENTER'
        assert decision.features['rung'] == 'initial'

    def test_same_market_same_price_does_not_reenter_initial(self):
        s = _strategy()
        s.evaluate(_ctx(no_asks=((0.81, 200.0),)))
        decision = s.evaluate(_ctx(no_asks=((0.81, 200.0),)))
        assert decision.action == 'SKIP'
        assert decision.reason == 'ladder_rung_not_yet_reached'

    def test_scale_82_requires_initial_first(self):
        s = _strategy()
        # Jump straight to 0.85 with no prior initial entry: still the
        # initial rung, not scale_82, because the ladder is sequential.
        decision = s.evaluate(_ctx(no_asks=((0.85, 200.0),)))
        assert decision.features['rung'] == 'initial'

    def test_scale_82_fires_after_initial_once_price_reaches_082(self):
        s = _strategy()
        s.evaluate(_ctx(no_asks=((0.80, 200.0),)))          # initial
        decision = s.evaluate(_ctx(no_asks=((0.83, 200.0),)))  # scale_82
        assert decision.action == 'ENTER'
        assert decision.features['rung'] == 'scale_82'

    def test_scale_89_fires_after_scale_82_once_price_reaches_089(self):
        s = _strategy()
        s.evaluate(_ctx(no_asks=((0.80, 200.0),)))          # initial
        s.evaluate(_ctx(no_asks=((0.83, 200.0),)))          # scale_82
        decision = s.evaluate(_ctx(no_asks=((0.90, 200.0),)))  # scale_89
        assert decision.action == 'ENTER'
        assert decision.features['rung'] == 'scale_89'

    def test_ladder_fully_filled_never_enters_a_fourth_time(self):
        s = _strategy()
        s.evaluate(_ctx(no_asks=((0.80, 200.0),)))
        s.evaluate(_ctx(no_asks=((0.83, 200.0),)))
        s.evaluate(_ctx(no_asks=((0.90, 200.0),)))
        decision = s.evaluate(_ctx(no_asks=((0.90, 200.0),)))
        assert decision.action == 'SKIP'
        assert decision.reason == 'ladder_fully_filled'

    def test_different_markets_have_independent_ladders(self):
        s = _strategy()
        s.evaluate(_ctx(no_asks=((0.80, 200.0),), slug='market-a'))
        decision = s.evaluate(_ctx(no_asks=((0.80, 200.0),), slug='market-b'))
        assert decision.action == 'ENTER'
        assert decision.features['rung'] == 'initial'

    def test_scale_rungs_use_the_module_constants(self):
        assert SCALE_RUNG_1 == 0.82
        assert SCALE_RUNG_2 == 0.89


# ============ 4. side is always No ============

class TestSideIsAlwaysNo:

    def test_entered_leg_is_no(self):
        s = _strategy()
        decision = s.evaluate(_ctx())
        assert decision.legs[0].outcome_side == 'No'

    def test_no_code_path_ever_builds_a_yes_leg(self):
        import inspect
        import strategies.polymarket.status_quo_collector as mod
        source = inspect.getsource(mod)
        assert "outcome_side='Yes'" not in source
        assert 'outcome_side="Yes"' not in source


# ============ 5. sizing ============

class TestSizing:

    def test_worst_case_notional_is_strictly_under_5_percent_of_bankroll(self):
        s = _strategy()
        decision = s.evaluate(_ctx(no_asks=((0.90, 200.0),)))
        leg = decision.legs[0]
        worst_case_notional = leg.shares * 0.90
        assert worst_case_notional < 50.0

    def test_shares_is_positive(self):
        s = _strategy()
        decision = s.evaluate(_ctx())
        assert decision.legs[0].shares > 0

    def test_insufficient_depth_refuses_rather_than_partial_fills(self):
        s = _strategy()
        decision = s.evaluate(_ctx(no_asks=((0.85, 1.0),)))
        assert decision.action == 'SKIP'
        assert decision.reason == 'insufficient_ask_depth'


# ============ 6. registry ============

def test_registered_after_smart_money_callers():
    from strategies.polymarket import build_strategies
    names = [s.strategy_name for s in build_strategies()]
    assert 'PM_status_quo_collector' in names
    idx_callers = names.index('PM_smart_money_callers')
    idx_status_quo = names.index('PM_status_quo_collector')
    assert idx_status_quo == idx_callers + 1
    assert names.count('PM_status_quo_collector') == 1


def test_the_first_eight_are_unchanged_by_this_addition():
    from strategies.polymarket import build_strategies
    names = [s.strategy_name for s in build_strategies()]
    assert names[:8] == [
        'PM_streak_snapper', 'PM_mid_price_continuation', 'PM_box_builder',
        'PM_corridor_collector', 'PM_temporal_arbitrage',
        'PM_corridor_pair', 'PM_spread_harvest_taker', 'PM_fair_value_arb']


def test_status_quo_collector_is_in_all():
    import strategies.polymarket as pm
    assert 'StatusQuoCollector' in pm.__all__
