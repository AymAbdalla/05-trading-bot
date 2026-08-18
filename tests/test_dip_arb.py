"""Tests for PM_dip_arb. Offline only.

Every book, window and price here is a synthetic fixture, so a result is a
statement about the code and not about whatever Polymarket happened to be
quoting. No network, no database, no clock: the tape is timestamped from the
context's own `window_ts + seconds_into_window`, so nothing here mocks `time`.

Four jobs, in descending order of how much they matter:

  1. **The warmup gate must be real.** The whole strategy is a comparison
     against a mean, and a mean over three quotes is not a mean. `TestTape` and
     `test_insufficient_tape` pin down that the strategy refuses to quote one
     below MIN_OBSERVATIONS, and that the refusal is its own named reason
     rather than a silent "no dip found" (convention 11).

  2. **The strategy must be provably ALIVE and provably PICKY.** A warmed tape
     plus a real dip produces an entry; a warmed tape plus a 3% dip produces a
     NAMED skip. A strategy that silently never fires looks identical in a
     graveyard to one that was honestly measured and failed.

  3. **`mean_collapsed_to_entry` must fire.** It is the only mitigation this
     strategy has for its core risk (a dip that is the truth changing rather
     than a mispricing) and a version where it never fired would look exactly
     like a version where it did, right up until the losses.

  4. **`evaluate` never returns None, and every exit path returns an
     ExitDecision.**

There is deliberately NO harness sweep here. Per D-268 this strategy is
NOT_TESTED until the resolution-PnL harness exists.
"""
import inspect
import math
import os
import sys
import types
from collections import Counter

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.polymarket.shadow_loop as shadow_mod  # noqa: E402
from engine.polymarket.paper_adapter import PaperPosition  # noqa: E402
from engine.polymarket.types import (Market, Orderbook, Outcome,  # noqa: E402
                                     PriceLevel)
from strategies.polymarket.base import MarketContext, Window  # noqa: E402
import strategies.polymarket.dip_arb as dip_mod  # noqa: E402
from strategies.polymarket.dip_arb import (DipArb,  # noqa: E402
                                           PriceTapeByToken, SOURCE_ASK,
                                           SOURCE_MID, reference_price,
                                           token_id_for)
from strategies.polymarket.fair_value_arb import ExitDecision  # noqa: E402

UP_TOK = 'tok-up'
DOWN_TOK = 'tok-down'
WINDOW_TS = 1_700_000_000
SLUG = 'btc-updown-5m-1700000000'


# ============ fixtures ============

def _market(slug=SLUG):
    return Market(id='m1', question='BTC up or down?', slug=slug,
                  condition_id='cond-1',
                  outcomes=(Outcome('Up', UP_TOK), Outcome('Down', DOWN_TOK)))


def _book(token, asks=(), bids=()):
    return Orderbook(
        token_id=token,
        bids=tuple(PriceLevel(float(p), float(s)) for p, s in bids),
        asks=tuple(PriceLevel(float(p), float(s)) for p, s in asks),
        timestamp=WINDOW_TS)


def _ctx(seconds_into_window=100.0,
         up_asks=((0.60, 200.0),), up_bids=((0.59, 200.0),),
         down_asks=((0.42, 200.0),), down_bids=((0.40, 200.0),),
         books=None, market=True):
    if books is None:
        books = {UP_TOK: _book(UP_TOK, up_asks, up_bids),
                 DOWN_TOK: _book(DOWN_TOK, down_asks, down_bids)}
    return MarketContext(
        window_ts=WINDOW_TS,
        windows=[Window(ts=WINDOW_TS, open=100_000.0, close=100_010.0,
                        direction='UP', source='price')],
        market=_market() if market else None,
        books=books, spot=100_010.0,
        seconds_into_window=seconds_into_window)


def _warm(strategy, n=None, start=0.0, step=5.0, **ctx_kwargs):
    """Feed `n` quiet cycles so the Up tape has a mean.

    Uses `evaluate`, not a private hook: the tape is supposed to fill on
    SKIPPING cycles too, and warming it any other way would let a regression
    that only fills on tradeable cycles pass this file.
    """
    n = dip_mod.MIN_OBSERVATIONS if n is None else n
    last = None
    for i in range(n):
        last = strategy.evaluate(_ctx(seconds_into_window=start + i * step,
                                      **ctx_kwargs))
    return last


def _fake_loop(strategy, positions):
    """A REAL `PolymarketShadowLoop`, unconstructed, carrying only the
    attributes `manage_exits` touches.

    `object.__new__` rather than the constructor on purpose: the constructor
    opens a client, a store and a paper adapter, and a test that needs those is
    testing the loop's setup rather than this strategy's exit wiring. The
    METHOD under test is the real one, so a rename or a contract change in
    `shadow_loop.py` fails here instead of at 3am in the live loop.

    `_fetch_book_checked` is stubbed to a value that would fail loudly: the
    context below carries the position's book, so the fetch path must never be
    reached, and a stub that quietly returned a book would hide it if it were.
    """
    loop = shadow_mod.PolymarketShadowLoop.__new__(
        shadow_mod.PolymarketShadowLoop)
    loop.assets = ['btc']
    loop.runtimes = {'btc': types.SimpleNamespace(strategies=[strategy])}
    loop.health = Counter()
    loop.exit_counts = Counter()
    loop.exit_no_fair_value_protocol = set()
    loop.adapter = types.SimpleNamespace(
        open_positions=lambda: list(positions),
        simulate_taker_sell=lambda **kw: pytest.fail(
            'no exit was expected on this fixture'))
    loop.store = None
    loop._fetch_book_checked = lambda token_id: (None, 'fetch_path_not_expected')
    return loop


def _position(entry=0.50, shares=20.0, opened_ts=WINDOW_TS + 100,
              window_ts=WINDOW_TS, side='Up', token_id=UP_TOK,
              strategy='PM_dip_arb'):
    return PaperPosition(
        position_id='pos-1', strategy=strategy, market_slug=SLUG,
        token_id=token_id, outcome_side=side, shares=shares, avg_price=entry,
        cost_usdc=entry * shares, fee_usdc=0.0, opened_ts=opened_ts,
        window_ts=window_ts)


# ============ 0. house rules ============

def test_paper_mode_is_true_in_the_module_and_on_the_class():
    assert dip_mod.PAPER_MODE is True
    assert DipArb().paper_mode is True


def test_it_manages_its_own_exits():
    assert DipArb().manages_exits is True


def test_the_strategy_name_is_the_one_the_kill_condition_names():
    assert DipArb().name == 'PM_dip_arb'


def test_the_break_even_is_computed_from_the_instance_not_written_down():
    # A constant restating 0.714 goes stale the first time somebody constructs
    # this class with a different max_loss, and then gets quoted as if it had
    # been measured (convention 22).
    default = DipArb()
    assert default.breakeven_win_rate == pytest.approx(0.05 / 0.07)

    wider = DipArb(min_profit=0.04, max_loss=0.04)
    assert wider.breakeven_win_rate == pytest.approx(0.5)
    assert wider.breakeven_win_rate != default.breakeven_win_rate

    assert math.isnan(DipArb(min_profit=0.0, max_loss=0.0).breakeven_win_rate)


def test_the_per_trade_break_even_refuses_a_target_at_or_below_entry():
    assert DipArb.breakeven_win_rate_for(0.50, 0.50, 0.45) is None
    assert DipArb.breakeven_win_rate_for(0.50, 0.60, 0.50) is None
    assert DipArb.breakeven_win_rate_for(0.50, 0.60, 0.45) == \
        pytest.approx(0.05 / 0.15)


def test_token_lookup_refuses_to_raise_on_a_malformed_market():
    # A cycle that raised is a cycle nobody counted (convention 20).
    assert token_id_for(_market(), 'Up') == UP_TOK
    assert token_id_for(_market(), 'Yes') is None
    assert token_id_for(object(), 'Up') is None
    assert token_id_for(None, 'Up') is None


# ============ 1. the tape ============

class TestTape:

    def test_a_mean_is_refused_below_the_minimum(self):
        tape = PriceTapeByToken()
        for i in range(dip_mod.MIN_OBSERVATIONS - 1):
            tape.observe(UP_TOK, 1000.0 + i, 0.60, SOURCE_MID)
        # None means CANNOT MEASURE, never "the mean is 0.60".
        assert tape.mean(UP_TOK) is None
        tape.observe(UP_TOK, 1000.0 + dip_mod.MIN_OBSERVATIONS, 0.60,
                     SOURCE_MID)
        assert tape.count(UP_TOK) == dip_mod.MIN_OBSERVATIONS
        assert tape.mean(UP_TOK) == pytest.approx(0.60)

    def test_each_token_gets_its_own_tape(self):
        tape = PriceTapeByToken()
        for i in range(dip_mod.MIN_OBSERVATIONS):
            tape.observe(UP_TOK, 1000.0 + i, 0.60, SOURCE_MID)
            tape.observe(DOWN_TOK, 1000.0 + i, 0.40, SOURCE_MID)
        # Averaging Up and Down together would give ~0.50 forever.
        assert tape.mean(UP_TOK) == pytest.approx(0.60)
        assert tape.mean(DOWN_TOK) == pytest.approx(0.40)

    def test_out_of_order_observations_are_refused_and_counted(self):
        tape = PriceTapeByToken()
        assert tape.observe(UP_TOK, 1000.0, 0.60, SOURCE_MID) is True
        assert tape.observe(UP_TOK, 999.0, 0.60, SOURCE_MID) is False
        assert tape.drops['out_of_order'] == 1
        assert tape.count(UP_TOK) == 1

    @pytest.mark.parametrize('price,reason', [
        (0.0, 'price_out_of_range'),
        (-0.1, 'price_out_of_range'),
        (1.5, 'price_out_of_range'),
        (float('nan'), 'non_finite'),
        (float('inf'), 'non_finite'),
        ('abc', 'unparseable'),
    ])
    def test_a_corrupt_price_is_refused_with_its_own_name(self, price, reason):
        tape = PriceTapeByToken()
        assert tape.observe(UP_TOK, 1000.0, price, SOURCE_MID) is False
        assert tape.drops[reason] == 1

    def test_a_missing_token_id_is_its_own_drop(self):
        tape = PriceTapeByToken()
        assert tape.observe('', 1000.0, 0.60, SOURCE_MID) is False
        assert tape.drops['no_token_id'] == 1

    def test_the_tape_is_length_bounded(self):
        tape = PriceTapeByToken(max_len=5)
        for i in range(50):
            tape.observe(UP_TOK, 1000.0 + i, 0.60, SOURCE_MID)
        assert tape.count(UP_TOK) == 5

    def test_stale_observations_are_pruned_by_age(self):
        tape = PriceTapeByToken(max_len=1000, max_age_sec=100.0)
        tape.observe(UP_TOK, 1000.0, 0.20, SOURCE_MID)
        tape.observe(UP_TOK, 1500.0, 0.80, SOURCE_MID)
        # A stalled loop must not keep old samples and call their mean recent.
        assert tape.count(UP_TOK) == 1
        assert tape.mean(UP_TOK, min_observations=1) == pytest.approx(0.80)

    def test_the_source_mix_is_reported_not_pooled(self):
        tape = PriceTapeByToken()
        tape.observe(UP_TOK, 1000.0, 0.60, SOURCE_MID)
        tape.observe(UP_TOK, 1001.0, 0.61, SOURCE_ASK)
        assert tape.source_mix(UP_TOK) == {SOURCE_MID: 1, SOURCE_ASK: 1}

    def test_reference_price_prefers_the_midpoint(self):
        price, source = reference_price(_book(UP_TOK, ((0.61, 10),),
                                              ((0.59, 10),)))
        assert price == pytest.approx(0.60) and source == SOURCE_MID

    def test_reference_price_falls_back_to_the_ask_on_a_one_sided_book(self):
        price, source = reference_price(_book(UP_TOK, ((0.61, 10),), ()))
        # Skipping one-sided books entirely would put holes in the tape exactly
        # where the market was thin, which is where the dips are.
        assert price == pytest.approx(0.61) and source == SOURCE_ASK

    def test_reference_price_on_an_empty_or_missing_book(self):
        assert reference_price(None) == (None, None)
        assert reference_price(_book(UP_TOK)) == (None, None)


# ============ 2. the entry path ============

class TestEntry:

    def test_the_happy_path_fires_after_the_tape_is_warm(self):
        s = DipArb()
        warm = _warm(s)
        assert warm.reason == 'dip_below_threshold', warm.reason

        d = s.evaluate(_ctx(seconds_into_window=100.0,
                            up_asks=((0.50, 200.0),),
                            up_bids=((0.49, 200.0),)))
        assert d.action == 'ENTER', d.reason
        leg = d.primary_leg
        assert leg.outcome_side == 'Up'
        assert leg.order_type == 'taker'
        assert leg.expected_price == pytest.approx(0.50)
        # The cap is the edge: the worst price at which the dip still clears.
        assert leg.limit_price < d.features['rolling_mean']
        assert d.features['realized_dip_fraction'] > dip_mod.DIP_THRESHOLD

    def test_the_tape_fills_on_skipping_cycles_too(self):
        s = DipArb()
        _warm(s, n=5)
        # A tape that only fills on tradeable cycles has holes exactly where
        # the market was quiet, which is where the mean comes from.
        assert s.tape.count(UP_TOK) == 5
        assert s.tape.count(DOWN_TOK) == 5

    def test_the_reference_is_the_outcomes_own_price_not_a_model(self):
        s = DipArb()
        _warm(s)
        d = s.evaluate(_ctx(up_asks=((0.50, 200.0),), up_bids=((0.49, 200.0),)))
        assert d.features['reference_is_historical_mean_not_model'] is True
        assert d.features['mean_is_a_lagging_estimate'] is True
        # It must never be pooled with the resolution population.
        assert d.features['exits_before_resolution'] is True

    def test_the_entry_is_sized_to_the_notional_cap(self):
        s = DipArb()
        _warm(s)
        d = s.evaluate(_ctx(up_asks=((0.50, 200.0),), up_bids=((0.49, 200.0),)))
        assert d.features['notional_usdc'] <= dip_mod.MAX_NOTIONAL_USDC + 1e-9
        assert d.primary_leg.shares >= dip_mod.MIN_SHARES

    def test_the_signal_carries_a_stop_strictly_below_entry(self):
        s = DipArb()
        _warm(s)
        d = s.evaluate(_ctx(up_asks=((0.50, 200.0),), up_bids=((0.49, 200.0),)))
        signal = s.decision_to_signal(d)
        # On a binary a losing share is worth exactly 0.00 (convention 8).
        assert signal.stop == 0.0 and signal.entry > signal.stop

    def test_the_per_trade_break_even_is_stamped_on_the_row(self):
        s = DipArb()
        _warm(s)
        d = s.evaluate(_ctx(up_asks=((0.50, 200.0),), up_bids=((0.49, 200.0),)))
        # The instance property is the WORST case (floor target); the row
        # carries the one for the target actually taken.
        assert d.features['breakeven_win_rate_this_trade'] < \
            d.features['breakeven_win_rate_floor']
        assert d.features['confidence_is_dip_size_not_win_probability'] is True

    def test_the_down_side_dips_too(self):
        s = DipArb()
        _warm(s)
        d = s.evaluate(_ctx(down_asks=((0.30, 200.0),),
                            down_bids=((0.29, 200.0),)))
        assert d.action == 'ENTER', d.reason
        assert d.primary_leg.outcome_side == 'Down'

    def test_the_attempt_counter_counts_attempts_not_fills(self):
        s = DipArb()
        _warm(s)
        d = s.evaluate(_ctx(up_asks=((0.50, 200.0),), up_bids=((0.49, 200.0),)))
        assert d.features['attempt_number'] == 1
        assert d.features['trade_count_is_attempts_not_fills'] is True


# ============ 3. every named skip ============

class TestSkipReasons:

    def test_no_market(self):
        assert DipArb().evaluate(_ctx(market=False)).reason == 'no_market'

    def test_no_window_clock(self):
        d = DipArb().evaluate(_ctx(seconds_into_window=None))
        assert d.reason == 'no_window_clock'

    def test_no_outcomes(self):
        ctx = MarketContext(window_ts=WINDOW_TS, market=object(),
                            seconds_into_window=10.0)
        d = DipArb().evaluate(ctx)
        # "the market object carries no readable outcome" is a different fact
        # from "the outcome has no book".
        assert d.reason == 'no_outcomes'

    def test_too_late_in_window(self):
        s = DipArb()
        _warm(s)
        d = s.evaluate(_ctx(seconds_into_window=250.0,
                            up_asks=((0.50, 200.0),)))
        assert d.reason == 'too_late_in_window'

    def test_max_trades_this_window(self):
        s = DipArb()
        _warm(s)
        for _ in range(dip_mod.MAX_TRADES_PER_WINDOW):
            s._note_attempt(WINDOW_TS)
        d = s.evaluate(_ctx(up_asks=((0.50, 200.0),), up_bids=((0.49, 200.0),)))
        assert d.reason == 'max_trades_this_window'

    def test_no_orderbook(self):
        assert DipArb().evaluate(_ctx(books={})).reason == 'no_orderbook'

    def test_no_asks(self):
        books = {UP_TOK: _book(UP_TOK, asks=(), bids=((0.58, 200.0),)),
                 DOWN_TOK: _book(DOWN_TOK, asks=(), bids=((0.40, 200.0),))}
        # An empty book and a bids-only book are the same fact for a BUY.
        assert DipArb().evaluate(_ctx(books=books)).reason == 'no_asks'

    def test_insufficient_tape(self):
        s = DipArb()
        _warm(s, n=dip_mod.MIN_OBSERVATIONS - 2)
        d = s.evaluate(_ctx(seconds_into_window=200.0,
                            up_asks=((0.50, 200.0),)))
        # CANNOT MEASURE, not "no dip found" (convention 11).
        assert d.reason == 'insufficient_tape'
        assert d.features['tape_observations']['Up'] < dip_mod.MIN_OBSERVATIONS

    def test_dip_below_threshold(self):
        s = DipArb()
        _warm(s)
        d = s.evaluate(_ctx(up_asks=((0.58, 200.0),), up_bids=((0.57, 200.0),)))
        # This is the strategy WORKING, and it is the overwhelming majority of
        # cycles.
        assert d.reason == 'dip_below_threshold'

    def test_mean_outside_tradeable_band(self):
        s = DipArb()
        _warm(s, up_asks=((0.96, 200.0),), up_bids=((0.94, 200.0),),
              down_asks=((0.06, 200.0),), down_bids=((0.04, 200.0),))
        d = s.evaluate(_ctx(seconds_into_window=150.0,
                            up_asks=((0.60, 200.0),), up_bids=((0.59, 200.0),),
                            down_asks=((0.01, 200.0),),
                            down_bids=((0.01, 200.0),)))
        # Below 0.10 a 10% relative dip is one tick, which is noise.
        assert d.reason == 'mean_outside_tradeable_band'

    def test_insufficient_book_depth(self):
        s = DipArb()
        _warm(s)
        d = s.evaluate(_ctx(up_asks=((0.50, 8.0),), up_bids=((0.49, 200.0),)))
        assert d.reason == 'insufficient_book_depth'

    def test_unsizable_at_notional_cap(self):
        s = DipArb(max_notional_usdc=2.0)
        _warm(s)
        d = s.evaluate(_ctx(up_asks=((0.50, 200.0),), up_bids=((0.49, 200.0),)))
        # Could not run, did not lose (convention 11).
        assert d.reason == 'unsizable_at_notional_cap'

    def test_unfillable_at_cap(self):
        # The depth gate is loosened so the WALK is the thing that refuses.
        # With the default 50-share depth floor this branch is unreachable,
        # which is worth knowing rather than faking.
        s = DipArb(min_book_depth_shares=1)
        _warm(s)
        d = s.evaluate(_ctx(up_asks=((0.50, 3.0),), up_bids=((0.49, 200.0),)))
        # A partial fill is not an entry (convention 12).
        assert d.reason == 'unfillable_at_cap'

    def test_every_skip_has_a_non_empty_reason(self):
        contexts = [_ctx(), _ctx(market=False), _ctx(books={}),
                    _ctx(seconds_into_window=None),
                    _ctx(seconds_into_window=280.0),
                    _ctx(up_asks=((0.50, 8.0),))]
        for ctx in contexts:
            s = DipArb()
            _warm(s)
            d = s.evaluate(ctx)
            if d.action == 'SKIP':
                assert d.reason, 'a silent skip is a missing number'


# ============ 4. exits ============

class TestExit:
    """Rule ORDER is the contract here, not just the individual triggers."""

    def test_the_bid_returning_to_the_mean_closes_the_position(self):
        book = _book(UP_TOK, asks=((0.61, 100),), bids=((0.60, 100),))
        d = DipArb().manage_exit(_position(entry=0.50), book,
                                 now=WINDOW_TS + 150, fair_value=0.60)
        assert d.action == 'EXIT'
        assert d.reason == 'mean_reverted'
        # Limit at the profit floor, not at the bid: walking depth must not
        # average us below the rule we exited on.
        assert d.limit_price == pytest.approx(0.52)

    def test_a_mean_that_fell_to_meet_us_is_not_a_reversion(self):
        # The bid is above the mean, but the mean came DOWN to it. Booking that
        # as a successful reversion would log a scratch as a win.
        book = _book(UP_TOK, asks=((0.52, 100),), bids=((0.51, 100),))
        d = DipArb().manage_exit(_position(entry=0.50), book,
                                 now=WINDOW_TS + 150, fair_value=0.505)
        assert d.reason == 'mean_collapsed_to_entry'
        assert d.limit_price == dip_mod.URGENT_SELL_LIMIT

    def test_the_hard_stop_closes_at_any_price(self):
        # 0.40 is the TIERED stop for a 0.50 entry (>= 0.50 tier, 0.10 away).
        # It was 0.45 while `MAX_LOSS = 0.05` was the stop; the stop now comes
        # from `base.tiered_stop_price` and is shared with the fair-value
        # family. See `tests/test_tiered_stop_loss.py`.
        book = _book(UP_TOK, asks=((0.41, 100),), bids=((0.40, 100),))
        d = DipArb().manage_exit(_position(entry=0.50), book,
                                 now=WINDOW_TS + 150, fair_value=0.60)
        assert d.reason == 'price_stop'
        # A stop that refuses a bad price is not a stop.
        assert d.limit_price == dip_mod.URGENT_SELL_LIMIT

    def test_the_stop_outranks_the_mean_collapse(self):
        # Both fire. The action is the same either way, but the reason must be
        # the one an operator can act on.
        book = _book(UP_TOK, asks=((0.41, 100),), bids=((0.40, 100),))
        d = DipArb().manage_exit(_position(entry=0.50), book,
                                 now=WINDOW_TS + 150, fair_value=0.50)
        assert d.reason == 'price_stop'

    def test_the_time_stop_fires_after_its_interval(self):
        book = _book(UP_TOK, asks=((0.52, 100),), bids=((0.51, 100),))
        s = DipArb()
        pos = _position(entry=0.50, opened_ts=WINDOW_TS + 100)
        early = s.manage_exit(pos, book, now=WINDOW_TS + 200, fair_value=0.60)
        late = s.manage_exit(pos, book, now=WINDOW_TS + 230, fair_value=0.60)
        assert early.action == 'HOLD'
        assert early.reason == 'waiting_for_mean_reversion'
        assert late.reason == 'time_stop'

    def test_the_window_close_outranks_everything(self):
        book = _book(UP_TOK, asks=((0.52, 100),), bids=((0.51, 100),))
        d = DipArb().manage_exit(_position(entry=0.50), book,
                                 now=WINDOW_TS + 280, fair_value=0.60)
        # Past here the position is a directional bet on the resolution, which
        # is a different strategy.
        assert d.reason == 'window_close'

    def test_an_unsellable_position_is_loud_not_a_patient_hold(self):
        book = _book(UP_TOK, asks=((0.52, 100),), bids=())
        d = DipArb().manage_exit(_position(entry=0.50), book,
                                 now=WINDOW_TS + 150, fair_value=0.60)
        assert d.action == 'HOLD'
        assert d.reason == 'no_bid_liquidity'
        assert d.features['unsellable'] is True

    def test_a_missing_book_is_its_own_hold(self):
        d = DipArb().manage_exit(_position(), None, now=WINDOW_TS + 150)
        assert d.reason == 'no_orderbook'

    def test_a_junk_position_is_refused_not_computed_off_zero(self):
        book = _book(UP_TOK, asks=((0.52, 100),), bids=((0.51, 100),))
        d = DipArb().manage_exit(_position(entry=0.0, shares=0.0), book,
                                 now=WINDOW_TS + 150)
        assert d.reason == 'unreadable_position'

    def test_no_reference_mean_is_not_the_same_as_waiting_for_one(self):
        book = _book(UP_TOK, asks=((0.52, 100),), bids=((0.51, 100),))
        d = DipArb().manage_exit(_position(entry=0.50), book,
                                 now=WINDOW_TS + 150)
        # Nothing warmed the tape and the caller passed no mean.
        assert d.action == 'HOLD'
        assert d.reason == 'no_reference_mean'
        assert d.features['rolling_mean'] is None

    def test_it_falls_back_to_its_own_tape_when_the_caller_passes_nothing(self):
        s = DipArb()
        _warm(s)
        book = _book(UP_TOK, asks=((0.61, 100),), bids=((0.60, 100),))
        d = s.manage_exit(_position(entry=0.50), book, now=WINDOW_TS + 150)
        assert d.features['rolling_mean_source'] == 'own_tape'
        assert d.reason == 'mean_reverted'

    def test_the_price_stop_still_works_without_a_reference_mean(self):
        book = _book(UP_TOK, asks=((0.41, 100),), bids=((0.40, 100),))
        d = DipArb().manage_exit(_position(entry=0.50), book,
                                 now=WINDOW_TS + 150)
        # A stop that stops working because the mean is unavailable is not a
        # stop.
        assert d.reason == 'price_stop'

    def test_every_exit_path_returns_an_exit_decision(self):
        s = DipArb()
        books = [None,
                 _book(UP_TOK),
                 _book(UP_TOK, asks=((0.52, 100),), bids=((0.51, 100),)),
                 _book(UP_TOK, asks=((0.46, 100),), bids=((0.45, 100),)),
                 _book(UP_TOK, asks=((0.61, 100),), bids=((0.60, 100),))]
        for book in books:
            for now in (WINDOW_TS + 150, WINDOW_TS + 280):
                for mean in (None, 0.50, 0.60):
                    d = s.manage_exit(_position(), book, now=now,
                                      fair_value=mean)
                    assert isinstance(d, ExitDecision)
                    assert d.action in ('EXIT', 'HOLD')
                    assert d.reason, 'a silent hold is a missing number'

    def test_the_batch_helper_only_touches_our_own_positions(self):
        s = DipArb()
        books = {UP_TOK: _book(UP_TOK, asks=((0.61, 100),),
                               bids=((0.60, 100),))}
        positions = [_position(),
                     _position(strategy='PM_fair_value_arb'),
                     _position(token_id='unknown-token')]
        out = s.exit_decisions(positions, books, now=WINDOW_TS + 150,
                               fair_value_by_side={'Up': 0.60})
        assert len(out) == 2
        assert out[0].reason == 'mean_reverted'
        # A position whose token has no book still gets a decision.
        assert out[1].reason == 'no_orderbook'


# ============ 5. it never returns None ============

class TestNeverNone:

    JUNK = [
        MarketContext(window_ts=0),
        MarketContext(window_ts=WINDOW_TS, market=_market(), books={}),
        MarketContext(window_ts=WINDOW_TS, market=_market(),
                      seconds_into_window=-5.0),
        MarketContext(window_ts=WINDOW_TS, market=object(),
                      seconds_into_window=10.0),
        MarketContext(window_ts=WINDOW_TS, market=_market(),
                      books={UP_TOK: _book(UP_TOK)},
                      seconds_into_window=10.0),
    ]

    @pytest.mark.parametrize('ctx', JUNK)
    def test_junk_contexts_still_produce_a_decision(self, ctx):
        d = DipArb().evaluate(ctx)
        assert d is not None
        assert d.action in ('ENTER', 'SKIP', 'QUOTE')
        assert d.strategy == 'PM_dip_arb'
        assert d.reason, 'a silent skip is a missing number'

    def test_a_decision_round_trips_to_a_dict(self):
        s = DipArb()
        _warm(s)
        d = s.evaluate(_ctx(up_asks=((0.50, 200.0),), up_bids=((0.49, 200.0),)))
        payload = d.to_dict()
        assert payload['strategy'] == 'PM_dip_arb'
        assert payload['legs']

    def test_a_skip_never_produces_a_signal(self):
        s = DipArb()
        assert s.decision_to_signal(s.evaluate(_ctx())) is None


# ============ 6. estimate(): the shadow loop's per-cycle reference call ======
#
# `manages_exits = True` obliges this method to exist. While it did not, the
# loop's `strategy.estimate(ctx)` raised AttributeError on every cycle, was
# swallowed by a try/except, and pinned `health['exit_fair_value_exceptions']`
# - a counter that exists to catch real model failures.
#
# The tests that matter here are the last two: they run the REAL
# `PolymarketShadowLoop.manage_exits` over a real DipArb, because a docstring
# claiming "this satisfies the loop's contract" is not a wiring test
# (convention 22).

class TestEstimate:

    def test_a_cold_tape_says_so_in_its_own_words(self):
        # A fresh instance has no mean for its first ~20 cycles. That is a
        # startup condition, not the permanent design fact below, and one
        # reason string for both would make the startup case invisible
        # (convention 20).
        est = DipArb().estimate(_ctx())
        assert est.usable is False
        assert est.reason == dip_mod.EST_INSUFFICIENT_TAPE
        assert est.for_side('Up') is None
        assert est.observations_by_side == {'Up': 0, 'Down': 0}

    def test_a_partly_cold_tape_is_still_the_warm_reason(self):
        s = DipArb()
        _warm(s, n=dip_mod.MIN_OBSERVATIONS - 1)
        assert s.estimate(_ctx()).reason == dip_mod.EST_INSUFFICIENT_TAPE
        # Continues the same 5s cadence rather than jumping the clock: a gap
        # past TAPE_MAX_AGE_SEC would prune the first 19 and this would test
        # the age pruner instead of the warmup gate.
        _warm(s, n=1, start=(dip_mod.MIN_OBSERVATIONS - 1) * 5.0)
        assert s.estimate(_ctx()).reason == dip_mod.EST_PER_TOKEN_MEAN

    def test_a_warm_tape_reports_the_means_but_still_refuses_to_publish(self):
        s = DipArb()
        _warm(s)
        est = s.estimate(_ctx())
        assert est.reason == dip_mod.EST_PER_TOKEN_MEAN
        # The number is real and it is carried. It is just not offered to the
        # loop as a fair value: `manage_exit` reads the same tape one line
        # later, so publishing it is neutral at best and, if the position's
        # token ever differs from this market's token for that side, a stop
        # against a reference the position was never bought against.
        assert est.for_side('Up') == pytest.approx(s.mean_for(UP_TOK))
        assert est.for_side('Down') == pytest.approx(s.mean_for(DOWN_TOK))
        assert est.usable is False

    def test_the_two_sides_are_not_mirror_images(self):
        # This is why the estimate is NOT a FairValueEstimate: that class
        # derives Down as 1 - p. Two tape means are two independent quote
        # series and they do not sum to 1.
        s = DipArb()
        _warm(s, up_asks=((0.60, 200.0),), up_bids=((0.58, 200.0),),
              down_asks=((0.44, 200.0),), down_bids=((0.40, 200.0),))
        est = s.estimate(_ctx())
        assert est.for_side('Up') + est.for_side('Down') != pytest.approx(1.0)

    def test_it_never_writes_to_the_tape(self):
        # manage_exits is phase 2 of the loop's cycle and evaluate is phase 3,
        # so this runs BEFORE the cycle's quote is observed. The tape only
        # refuses timestamps that go backwards, so an observe() in here would
        # add a second copy of the same quote at the same second and weight it
        # double in the mean.
        s = DipArb()
        _warm(s)
        before = (s.tape.count(UP_TOK), s.tape.count(DOWN_TOK),
                  s.mean_for(UP_TOK), dict(s.tape.drops))
        for _ in range(5):
            s.estimate(_ctx())
        assert (s.tape.count(UP_TOK), s.tape.count(DOWN_TOK),
                s.mean_for(UP_TOK), dict(s.tape.drops)) == before

    @pytest.mark.parametrize('ctx,reason', [
        (None, dip_mod.EST_NO_CONTEXT),
        (MarketContext(window_ts=WINDOW_TS, market=None),
         dip_mod.EST_NO_MARKET),
        (MarketContext(window_ts=WINDOW_TS, market=object()),
         dip_mod.EST_NO_OUTCOME_TOKENS),
    ])
    def test_junk_contexts_get_a_named_reason_not_an_exception(self, ctx,
                                                               reason):
        # A strategy that throws here is caught and counted by the loop, and a
        # counted exception is a cycle nobody can read.
        est = DipArb().estimate(ctx)
        est.to_dict()
        assert est.usable is False
        assert est.reason == reason

    def test_every_estimate_reason_is_distinct(self):
        reasons = [dip_mod.EST_NO_CONTEXT, dip_mod.EST_NO_MARKET,
                   dip_mod.EST_NO_OUTCOME_TOKENS,
                   dip_mod.EST_INSUFFICIENT_TAPE,
                   dip_mod.EST_PER_TOKEN_MEAN]
        assert len(set(reasons)) == len(reasons)
        assert all(reasons)

    def test_an_unknown_outcome_label_raises_rather_than_guessing_up(self):
        # The loop counts this as `exit_unknown_outcome_side`, and it catches
        # ValueError specifically - a different exception type would escape
        # that handler and land in the outer one.
        s = DipArb()
        _warm(s)
        est = s.estimate(_ctx())
        with pytest.raises(ValueError):
            est.for_side('Sideways')
        with pytest.raises(ValueError):
            est.for_side('')
        # Known label, market carries no such token: absent, not an error.
        assert est.for_side('Yes') is None
        # The four labels this strategy trades, in any casing.
        assert est.for_side('up') == est.for_side('UP') == est.for_side('Up')

    # -- the wiring, against the real loop ---------------------------------

    def test_the_estimate_satisfies_what_the_real_loop_reads_off_it(self):
        # Asserted against the loop's ACTUAL source, not against a copy of it
        # here: `manage_exits` reads `.usable` and then calls
        # `.for_side(pos.outcome_side)`. If either name moves, this fails.
        src = inspect.getsource(
            shadow_mod.PolymarketShadowLoop.manage_exits)
        assert "getattr(est, 'usable', False)" in src
        assert 'est.for_side(pos.outcome_side)' in src

        est = DipArb().estimate(_ctx())
        assert isinstance(getattr(est, 'usable'), bool)
        assert callable(getattr(est, 'for_side'))

    def test_the_real_loop_polls_it_without_raising(self):
        # The whole point of the change. Before it, this call incremented
        # health['exit_fair_value_exceptions'] once per (asset, strategy) per
        # cycle and logged a warning with it.
        s = DipArb()
        _warm(s)
        pos = _position(entry=0.50)
        book = _book(UP_TOK, asks=((0.56, 100),), bids=((0.55, 100),))
        loop = _fake_loop(s, [pos])

        seen = {}
        real_manage_exit = s.manage_exit

        def spy(position, bk, now, fair_value=None):
            seen['fair_value'] = fair_value
            d = real_manage_exit(position, bk, now, fair_value=fair_value)
            seen['decision'] = d
            return d

        s.manage_exit = spy
        ctx = _ctx(books={UP_TOK: book})

        for _ in range(3):
            out = shadow_mod.PolymarketShadowLoop.manage_exits(
                loop, {'btc': ctx}, now=WINDOW_TS + 150)

        assert out['checked'] == 1
        assert loop.health['exit_fair_value_exceptions'] == 0
        assert loop.health['exit_decision_exceptions'] == 0
        assert loop.exit_counts['decision_exception'] == 0
        # usable=False, so the loop passes nothing and manage_exit falls back
        # to its own tape. That fallback is now EXPLICIT and stamped, not the
        # residue of a swallowed AttributeError.
        assert seen['fair_value'] is None
        assert seen['decision'].features['rolling_mean_source'] == 'own_tape'
        assert seen['decision'].features['rolling_mean'] == \
            pytest.approx(s.mean_for(UP_TOK))
        assert sum(loop.exit_counts.values()) == 3
        assert list(loop.exit_counts) == ['hold:waiting_for_mean_reversion']
