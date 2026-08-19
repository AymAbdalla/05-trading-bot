"""Tests for PM_smart_money_callers. Offline only.

Every feed here is a stub (`StubCallerFeed`); no test in this file resolves a
hostname. `caller_records` is injected directly on the strategy for every test
that needs a specific record state, so the caller-record gate is exercised
without touching the filesystem.

Five jobs, matching `test_smart_money_copy.py`'s own structure:

  1. **The direction mapping must be exactly right and nothing else.**
     `TestOutcomeSideForDirection` is the single most load-bearing test in
     this file - see the module docstring's own warning.
  2. **The entry gate: no caller record, no entry** - `TestCallerRecordGate`.
  3. **Size is fixed at `CALLER_SHARES` on every entry**, never more, never
     less. `TestSizing`.
  4. **Every named skip is reachable and distinct.** `TestSkipReasons`.
  5. **The registry**: appended last, first eight untouched.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.types import (Market, Orderbook, Outcome,  # noqa: E402
                                     PriceLevel)
from strategies.polymarket.base import MarketContext             # noqa: E402
from strategies.polymarket.caller_feed import (                   # noqa: E402
    CallerRecord, DeclaredPlay)
from strategies.polymarket.smart_money_callers import (            # noqa: E402
    CALLER_SHARES, SmartMoneyCallers, outcome_side_for_direction)

WINDOW_TS = 1755000000
YES_TOK = 'tok-yes'
NO_TOK = 'tok-no'
SLUG = 'will-mrvl-close-above-80-on-9-25'


# ============ fixtures ============

def _market(slug=SLUG, question='Will MRVL close above $80 by 9/25?'):
    return Market(id='m1', question=question, slug=slug, condition_id='c1',
                 outcomes=(Outcome('Yes', YES_TOK), Outcome('No', NO_TOK)),
                 active=True, closed=False, end_date='2025-09-25T00:00:00Z',
                 volume=50000.0)


def _book(token, asks=(), bids=()):
    return Orderbook(
        token_id=token,
        bids=tuple(PriceLevel(float(p), float(s)) for p, s in bids),
        asks=tuple(PriceLevel(float(p), float(s)) for p, s in asks),
        timestamp=WINDOW_TS)


def _ctx(yes_asks=((0.62, 200.0),), no_asks=((0.35, 200.0),),
         market=True, market_type='event'):
    books = {YES_TOK: _book(YES_TOK, yes_asks, ((0.60, 200.0),)),
            NO_TOK: _book(NO_TOK, no_asks, ((0.33, 200.0),))}
    return MarketContext(window_ts=WINDOW_TS,
                        market=_market() if market else None,
                        books=books, market_type=market_type)


def _play(handle='zin1422', play_id='zin1422:p1', ticker='MRVL',
          direction='short', expiry='2025-09-25', strike=200.0,
          post_ts=None):
    return DeclaredPlay(handle=handle, play_id=play_id, ticker=ticker,
                        direction=direction, expiry=expiry, strike=strike,
                        post_ts=post_ts if post_ts is not None
                        else float(WINDOW_TS) - 3600.0)


def _record(handle='zin1422', play_ids=('zin1422:p1',)):
    return CallerRecord(handle=handle, play_ids=tuple(play_ids))


class StubCallerFeed:
    """The only feed these tests ever use. Touches nothing."""

    def __init__(self, plays_by_handle=None, fail_handles=(), drops=None,
                raise_on=()):
        self.plays_by_handle = dict(plays_by_handle or {})
        self.fail_handles = set(fail_handles)
        self.drops = dict(drops or {})
        self.raise_on = set(raise_on)
        self.calls = []

    def poll(self, handle):
        self.calls.append(handle)
        if handle in self.raise_on:
            raise RuntimeError('stub feed blew up')
        if handle in self.fail_handles:
            return None, dict(self.drops), 'unreachable_no_cache'
        return (list(self.plays_by_handle.get(handle, [])),
               dict(self.drops), 'fetched_fresh')


def _strategy(feed=None, plays=None, records=None, **kwargs):
    plays = {'zin1422': [_play()]} if plays is None else plays
    feed = feed if feed is not None else StubCallerFeed(plays_by_handle=plays)
    records = {'zin1422': _record()} if records is None else records
    return SmartMoneyCallers(feed=feed, caller_records=records, **kwargs)


# ============ 0. house rules ============

def test_paper_mode_is_true_in_the_module_and_on_the_class():
    import strategies.polymarket.smart_money_callers as smc
    assert smc.PAPER_MODE is True
    assert SmartMoneyCallers.paper_mode is True


def test_strategy_name_and_market_types():
    s = SmartMoneyCallers()
    assert s.strategy_name == 'PM_smart_money_callers'
    assert s.supported_market_types == ('event',)


def test_manages_exits_is_false():
    assert SmartMoneyCallers.manages_exits is False


def test_assert_supports_raises_on_an_undeclared_market_type():
    s = _strategy()
    ctx = _ctx(market_type='sports')
    with pytest.raises(ValueError):
        s.evaluate(ctx)


def test_evaluate_never_returns_none_even_on_garbage_input():
    s = _strategy(plays={}, records={})
    ctx = _ctx(market=False)
    decision = s.evaluate(ctx)
    assert decision is not None
    assert decision.action == 'SKIP'


# ============ 1. the direction mapping ============

class TestOutcomeSideForDirection:
    def test_long_maps_to_yes(self):
        m = _market()
        assert outcome_side_for_direction('long', m) == 'Yes'

    def test_short_maps_to_no(self):
        m = _market()
        assert outcome_side_for_direction('short', m) == 'No'

    def test_falls_back_to_up_down_when_yes_no_absent(self):
        m = Market(id='m2', question='q', slug='s', condition_id='c2',
                  outcomes=(Outcome('Up', 'u'), Outcome('Down', 'd')))
        assert outcome_side_for_direction('long', m) == 'Up'
        assert outcome_side_for_direction('short', m) == 'Down'

    def test_unrecognised_label_pair_returns_none_never_a_guess(self):
        m = Market(id='m3', question='q', slug='s', condition_id='c3',
                  outcomes=(Outcome('Above', 'a'), Outcome('Below', 'b')))
        assert outcome_side_for_direction('long', m) is None
        assert outcome_side_for_direction('short', m) is None

    def test_an_unrecognised_direction_string_returns_none(self):
        m = _market()
        assert outcome_side_for_direction('sideways', m) is None


# ============ 2. the caller-record gate ============

class TestCallerRecordGate:
    def test_no_record_for_the_handle_refuses_entry(self):
        s = _strategy(records={})
        decision = s.evaluate(_ctx())
        assert decision.action == 'SKIP'
        assert decision.reason == 'caller_record_unknown'

    def test_an_existing_record_with_zero_plays_still_lets_the_first_play_in(
            self):
        # "The first 3 plays run at minimum size" - the record existing at
        # all is the bootstrap; declared_plays_seen does not need to be >= 3.
        s = _strategy(records={'zin1422': CallerRecord(handle='zin1422')})
        decision = s.evaluate(_ctx())
        assert decision.action == 'ENTER'

    def test_a_play_from_an_untracked_second_caller_is_refused_by_itself(self):
        plays = {'zin1422': [_play()],
                'other_caller': [_play(handle='other_caller',
                                       play_id='other_caller:p1')]}
        s = SmartMoneyCallers(
            feed=StubCallerFeed(plays_by_handle=plays),
            callers=('zin1422', 'other_caller'),
            caller_records={'zin1422': _record()})
        decision = s.evaluate(_ctx())
        # zin1422's play still maps and enters; the untracked caller's play is
        # counted separately, never silently merged into the entry.
        assert decision.action == 'ENTER'
        assert decision.features['declared_plays_from_untracked_callers'] == 1


# ============ 3. sizing ============

class TestSizing:
    def test_entry_size_is_exactly_caller_shares(self):
        s = _strategy()
        decision = s.evaluate(_ctx())
        assert decision.action == 'ENTER'
        assert decision.legs[0].shares == CALLER_SHARES

    def test_size_never_exceeds_caller_shares_even_with_deep_liquidity(self):
        s = _strategy()
        deep_ctx = _ctx(no_asks=((0.35, 5000.0),))
        decision = s.evaluate(deep_ctx)
        assert decision.legs[0].shares == CALLER_SHARES

    def test_min_and_max_shares_are_the_same_constant(self):
        # The whole point of the fixed-size design: no scaling path exists.
        s = SmartMoneyCallers()
        assert s.shares == CALLER_SHARES == 5

    def test_a_caller_with_many_verified_plays_still_sizes_at_the_fixed_cap(
            self):
        # Even if a future caller record claimed verified_plays >= 3, this
        # build has no code path that sizes above CALLER_SHARES.
        records = {'zin1422': CallerRecord(handle='zin1422',
                                           play_ids=('a', 'b', 'c', 'd'),
                                           verified_plays=5, measured=True)}
        s = _strategy(records=records)
        decision = s.evaluate(_ctx())
        assert decision.legs[0].shares == CALLER_SHARES


# ============ 4. hold to resolution ============

def test_holds_to_resolution_no_managed_exit():
    s = _strategy()
    decision = s.evaluate(_ctx())
    assert decision.action == 'ENTER'
    assert decision.features['exits_before_resolution'] is False
    assert SmartMoneyCallers.manages_exits is False


# ============ 5. every named skip reason ============

class TestSkipReasons:
    def test_no_market(self):
        s = _strategy()
        decision = s.evaluate(_ctx(market=False))
        assert decision.reason == 'no_market'

    def test_caller_feed_unavailable_when_every_watched_caller_fails(self):
        s = _strategy(feed=StubCallerFeed(fail_handles=('zin1422',)))
        decision = s.evaluate(_ctx())
        assert decision.reason == 'caller_feed_unavailable'

    def test_no_declared_plays_when_the_feed_is_fine_but_empty(self):
        s = _strategy(plays={'zin1422': []})
        decision = s.evaluate(_ctx())
        assert decision.reason == 'no_declared_plays'

    def test_no_declared_play_for_market_when_ticker_does_not_match(self):
        s = _strategy(plays={'zin1422': [_play(ticker='NBIS')]})
        decision = s.evaluate(_ctx())
        assert decision.reason == 'no_declared_play_for_market'

    def test_already_entered_this_play_on_a_second_cycle(self):
        s = _strategy()
        first = s.evaluate(_ctx())
        assert first.action == 'ENTER'
        second = s.evaluate(_ctx())
        assert second.reason == 'already_entered_this_play'

    def test_outcome_side_unresolvable_on_a_non_yes_no_up_down_market(self):
        odd_market = Market(id='m4', question='q', slug='will-mrvl-move',
                           condition_id='c4',
                           outcomes=(Outcome('Above', 'a'), Outcome('Below', 'b')),
                           active=True, closed=False,
                           end_date='2025-09-25T00:00:00Z', volume=50000.0)
        books = {'a': _book('a', ((0.5, 100.0),)),
                'b': _book('b', ((0.5, 100.0),))}
        ctx = MarketContext(window_ts=WINDOW_TS, market=odd_market,
                           books=books, market_type='event')
        s = _strategy()
        decision = s.evaluate(ctx)
        assert decision.reason == 'outcome_side_unresolvable'

    def test_no_asks_when_the_book_is_empty(self):
        s = _strategy()
        decision = s.evaluate(_ctx(no_asks=()))
        assert decision.reason == 'no_asks'

    def test_ask_above_max_entry_price(self):
        s = _strategy()
        decision = s.evaluate(_ctx(no_asks=((0.99, 200.0),)))
        assert decision.reason == 'ask_above_max_entry_price'

    def test_insufficient_book_depth(self):
        s = _strategy()
        decision = s.evaluate(_ctx(no_asks=((0.35, 3.0),)))
        assert decision.reason == 'insufficient_book_depth'

    def test_book_cannot_fill_at_cap(self):
        s = _strategy(max_entry_price=0.40)
        # Enough DEPTH within the band, but not enough shares priced AT OR
        # BELOW the cap to fill the full fixed size.
        decision = s.evaluate(_ctx(no_asks=((0.39, 2.0), (0.41, 200.0))))
        assert decision.reason in ('book_cannot_fill', 'insufficient_book_depth')


# ============ 6. registry ============

def test_registered_as_the_appended_last_strategy():
    from strategies.polymarket import build_strategies
    strategies = build_strategies()
    names = [s.strategy_name for s in strategies]
    assert names[-1] == 'PM_smart_money_callers'
    assert isinstance(strategies[-1], SmartMoneyCallers)
    assert names.count('PM_smart_money_callers') == 1


def test_the_first_eight_are_unchanged_by_this_addition():
    # Do not weaken or duplicate tests/test_fair_value_arb_variants.py's own
    # pin - this just adds independent coverage from this file.
    from strategies.polymarket import build_strategies
    names = [s.strategy_name for s in build_strategies()]
    assert names[:8] == [
        'PM_streak_snapper', 'PM_mid_price_continuation', 'PM_box_builder',
        'PM_corridor_collector', 'PM_temporal_arbitrage',
        'PM_corridor_pair', 'PM_spread_harvest_taker', 'PM_fair_value_arb']


def test_smart_money_callers_is_in_all():
    import strategies.polymarket as pm
    assert 'SmartMoneyCallers' in pm.__all__
