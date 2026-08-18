"""Tests for `PM_fair_value_arb_inverse` - the parent's signal read backwards.

No network. Every orderbook, window, position and spot here is synthetic, so a
result is a statement about the code and never about whatever Polymarket
happened to be quoting.

Five jobs, in descending order of how much they matter:

  1. **THE FLIP MUST ACTUALLY HAPPEN.** `TestTheFlip` builds a real
     `MarketContext`, runs the PARENT on it, runs the INVERSE on the SAME
     context, and asserts the two `outcome_side` values are literal opposites -
     in both directions, because a bug that only flips Up would look correct in
     half the log. Nothing here mocks the flip; mocking the flip would test the
     mock. This is the wiring test for the entire hypothesis (convention 22).

  2. **The flipped side must be priced against ITS OWN book.** `TestOverround`
     pins that `ask(Up) + ask(Down) > 1.00` on the fixture and that the inverse
     entry is NOT `1 - parent_entry`. If that ever collapses to the naive
     arithmetic the strategy is reporting a fill nobody could have got.

  3. **Stops and targets come from the fill, never from mirrored arithmetic.**
     `TestStopsComeFromTheActualFill`, plus the convention 8 check that the stop
     is strictly below the entry.

  4. **A failing opposite-side book gets its OWN reason.** Conventions 11 and
     20: "the market had no opportunity" and "the opportunity was on the side we
     refuse to take" are two causes and must never share one counter.

  5. **The model exits must be off.** `model_stop` fires by definition on every
     inverse position; inheriting it would close each one on its first poll and
     the hypothesis would never be tested. `TestModelExitsAreDisabled` pins the
     parent firing and the inverse not, on the same inputs.

There is deliberately NO harness sweep here. Per D-268 this strategy is
NOT_TESTED until a resolution-PnL harness scores it. Nothing in this file
measures edge and nothing in it should ever be cited as evidence that the
inversion works.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.halt as halt_mod  # noqa: E402
import strategies.polymarket.fair_value_arb as fva  # noqa: E402
import strategies.polymarket.fair_value_arb_inverse as inv_mod  # noqa: E402
from engine.polymarket.paper_adapter import PaperPosition  # noqa: E402
from engine.polymarket.types import (Market, Orderbook,  # noqa: E402
                                     Outcome, PriceLevel)
from strategies.polymarket.base import MarketContext, Window  # noqa: E402
from strategies.polymarket.fair_value_arb import FairValueArb  # noqa: E402
from strategies.polymarket.fair_value_arb_inverse import \
    FairValueArbInverse  # noqa: E402

UP_TOK = 'tok-up'
DOWN_TOK = 'tok-down'
WINDOW_TS = 1_700_000_000
SLUG = 'btc-updown-5m-1700000000'

PARENT_NAME = 'PM_fair_value_arb'
INVERSE_NAME = 'PM_fair_value_arb_inverse'


# ============ fixtures (identical shape to test_fair_value_arb_variants) ======

@pytest.fixture(autouse=True)
def never_halted(tmp_path, monkeypatch):
    """Point the kill switch at a path that does not exist.

    A real HALT file left in the repo root by a drill would otherwise turn every
    entry test into a `halted` skip - the switch WORKING, and indistinguishable
    in the output from this strategy being broken.
    """
    monkeypatch.setattr(halt_mod, 'HALT_FILE', str(tmp_path / 'NO_SUCH_HALT'))


def _market(slug=SLUG):
    return Market(id='m1', question='BTC up or down?', slug=slug,
                  condition_id='cond-1',
                  outcomes=(Outcome('Up', UP_TOK), Outcome('Down', DOWN_TOK)))


def _book(token, asks=(), bids=()):
    """Synthetic book. `asks` ascending best-first, `bids` descending."""
    return Orderbook(
        token_id=token,
        bids=tuple(PriceLevel(float(p), float(s)) for p, s in bids),
        asks=tuple(PriceLevel(float(p), float(s)) for p, s in asks),
        timestamp=WINDOW_TS)


def _windows(n=13, open_price=100_000.0, move=90.0):
    out = []
    for i in range(n):
        ts = WINDOW_TS - (n - 1 - i) * 300
        close = open_price + (move if i % 2 == 0 else -move)
        out.append(Window(ts=ts, open=open_price, close=close,
                          direction='UP' if close >= open_price else 'DOWN',
                          source='price'))
    return out


def _ctx(spot=100_060.0, seconds_into_window=100.0,
         up_asks=((0.60, 200.0),), down_asks=((0.42, 200.0),),
         up_bids=((0.58, 200.0),), down_bids=((0.40, 200.0),),
         windows=None, market=True):
    """Default arguments make a valid parent ENTRY on the **Up** side.

    P(Up) lands near 0.71 against a 0.60 Up ask - an 11c gap on 200 shares of
    depth. The Down side is 0.42 against a 0.29 fair value, i.e. 13c RICH, which
    is exactly why the parent refuses it and exactly what this strategy buys.
    Note `0.60 + 0.42 = 1.02`: the book has an overround, on purpose.
    """
    books = {UP_TOK: _book(UP_TOK, up_asks, up_bids),
             DOWN_TOK: _book(DOWN_TOK, down_asks, down_bids)}
    return MarketContext(
        window_ts=WINDOW_TS,
        windows=_windows() if windows is None else windows,
        market=_market() if market else None,
        books=books, spot=spot, strike=None,
        seconds_into_window=seconds_into_window)


def _ctx_parent_buys_down(**kw):
    """A context where the PARENT chooses Down, so the inverse must buy Up.

    fair(Down) is ~0.29 against a 0.20 ask (9c gap) while fair(Up) ~0.71 sits
    below a 0.75 ask (-4c). The parent takes the larger edge, which is Down.
    Needed because a flip bug that only handles one direction would look correct
    across half of a real log.
    """
    kw.setdefault('up_asks', ((0.75, 200.0),))
    kw.setdefault('down_asks', ((0.20, 200.0),))
    kw.setdefault('up_bids', ((0.73, 200.0),))
    kw.setdefault('down_bids', ((0.18, 200.0),))
    return _ctx(**kw)


def _position(entry=0.42, shares=20.0, opened_ts=WINDOW_TS + 100,
              window_ts=WINDOW_TS, side='Down', token_id=DOWN_TOK,
              strategy=INVERSE_NAME):
    """An OPEN inverse position: the Down token, filled at the Down ask."""
    return PaperPosition(
        position_id='pos-inv-1', strategy=strategy, market_slug=SLUG,
        token_id=token_id, outcome_side=side, shares=shares, avg_price=entry,
        cost_usdc=entry * shares, fee_usdc=0.0, opened_ts=opened_ts,
        window_ts=window_ts)


# ============ 1. THE FLIP. The wiring test for the whole hypothesis ==========

class TestTheFlip:
    """Same context, two strategies, literally opposite sides.

    Constructed contexts and real `evaluate` calls throughout. Nothing here
    patches the flip - a test that mocked the inversion would be testing the
    mock, which is the exact failure convention 22 names.
    """

    def test_parent_buys_up_and_the_inverse_buys_down(self):
        ctx = _ctx()
        parent = FairValueArb().evaluate(ctx)
        inverse = FairValueArbInverse().evaluate(ctx)

        assert parent.action == 'ENTER', (parent.reason, parent.features)
        assert inverse.action == 'ENTER', (inverse.reason, inverse.features)
        assert parent.legs[0].outcome_side == 'Up'
        assert inverse.legs[0].outcome_side == 'Down'

    def test_parent_buys_down_and_the_inverse_buys_up(self):
        # The other direction. A flip that only handles Up would look correct
        # across roughly half of a real log.
        ctx = _ctx_parent_buys_down()
        parent = FairValueArb().evaluate(ctx)
        inverse = FairValueArbInverse().evaluate(ctx)

        assert parent.action == 'ENTER', (parent.reason, parent.features)
        assert inverse.action == 'ENTER', (inverse.reason, inverse.features)
        assert parent.legs[0].outcome_side == 'Down'
        assert inverse.legs[0].outcome_side == 'Up'

    @pytest.mark.parametrize('ctx_factory', [_ctx, _ctx_parent_buys_down])
    def test_the_two_sides_are_opposites_not_merely_different(self, ctx_factory):
        ctx = ctx_factory()
        parent_side = FairValueArb().evaluate(ctx).legs[0].outcome_side
        inverse_side = FairValueArbInverse().evaluate(ctx).legs[0].outcome_side

        assert {parent_side, inverse_side} == {'Up', 'Down'}
        assert parent_side != inverse_side

    @pytest.mark.parametrize('ctx_factory', [_ctx, _ctx_parent_buys_down])
    def test_the_flip_is_auditable_from_the_row_alone(self, ctx_factory):
        # An analyst must be able to prove the inversion happened without
        # trusting the class name (convention 22).
        d = FairValueArbInverse().evaluate(ctx_factory())
        f = d.features

        assert f['inverted_from'] == PARENT_NAME
        assert f['flip_applied'] is True
        assert f['parent_intended_side'] != f['outcome_side']
        assert f['inverse_side_taken'] == f['outcome_side']
        assert f['outcome_side'] == d.legs[0].outcome_side

    def test_the_row_is_stamped_with_the_inverses_own_strategy_name(self):
        d = FairValueArbInverse().evaluate(_ctx())
        assert d.strategy == INVERSE_NAME
        assert FairValueArb().evaluate(_ctx()).strategy == PARENT_NAME

    def test_it_buys_the_side_the_model_prices_BELOW_the_ask(self):
        # The definition of the strategy, asserted rather than described: the
        # side taken is rich against the model, i.e. negative realized edge.
        f = FairValueArbInverse().evaluate(_ctx()).features
        assert f['side_fair_value'] < f['effective_ask']
        assert f['realized_edge'] < 0
        assert f['realized_edge_is_negative_by_construction'] is True

    def test_a_parent_skip_passes_through_with_the_parents_reason(self):
        # There is nothing to invert about "no opportunity". Giving these our
        # own reason strings would split one cause across two counters.
        ctx = _ctx(up_asks=((0.71, 200.0),), down_asks=((0.71, 200.0),))
        parent = FairValueArb().evaluate(ctx)
        inverse = FairValueArbInverse().evaluate(ctx)

        assert parent.reason == 'edge_below_threshold'
        assert inverse.action == 'SKIP'
        assert inverse.reason == 'edge_below_threshold'
        assert inverse.features['flip_applied'] is False

    def test_an_unresolvable_side_label_refuses_rather_than_guessing(self,
                                                                    monkeypatch):
        # `opposite()` returns its input for a label it does not recognise.
        # Emitting the PARENT's own side under this class name is the worst
        # failure available here: an un-inverted trade logged as an inverted one.
        monkeypatch.setattr(inv_mod, 'opposite', lambda s: s)
        d = FairValueArbInverse().evaluate(_ctx())

        assert d.action == 'SKIP'
        assert d.reason == 'inverse_side_unresolvable'
        assert d.legs == []
        assert d.features['flip_applied'] is False


# ============ 2. the overround: the flipped side has its OWN book ============

class TestOverround:
    """`ask(Up) + ask(Down)` is about 1.03, so `1 - parent_entry` is a lie."""

    def test_the_fixture_book_actually_has_an_overround(self):
        # If this ever became 1.00 the tests below would pass vacuously.
        f = FairValueArbInverse().evaluate(_ctx()).features
        assert f['ask_sum'] == pytest.approx(1.02)
        assert f['overround'] == pytest.approx(0.02)

    def test_the_entry_is_the_real_ask_not_one_minus_the_parents(self):
        ctx = _ctx()
        parent_entry = FairValueArb().evaluate(ctx).legs[0].premium
        inverse_entry = FairValueArbInverse().evaluate(ctx).legs[0].premium

        assert parent_entry == pytest.approx(0.60)
        assert inverse_entry == pytest.approx(0.42)
        # The naive number would have been 0.40. It is 2c wrong, and 2c is
        # twice this strategy's entire claimed edge.
        assert inverse_entry != pytest.approx(1.0 - parent_entry)

    def test_the_gap_against_the_naive_number_is_on_the_row(self):
        f = FairValueArbInverse().evaluate(_ctx()).features
        assert f['naive_inverse_price_one_minus_parent'] == pytest.approx(0.40)
        assert f['overround_cost_vs_naive'] == pytest.approx(0.02)

    def test_the_cap_is_book_derived_and_sits_above_the_flipped_ask(self):
        # The parent's model-derived cap (fair - 0.04 = 0.25 here) is BELOW the
        # 0.42 flipped ask and would make this strategy fire exactly never.
        f = FairValueArbInverse().evaluate(_ctx()).features
        assert f['inverse_cap_is_book_derived_not_model_derived'] is True
        assert f['entry_cap'] == pytest.approx(0.43)
        assert f['entry_cap'] > f['best_ask']
        assert f['entry_cap'] != f['parent_entry_cap']

    def test_a_model_derived_cap_would_have_been_unsatisfiable(self):
        # The justification for replacing the cap, asserted on real numbers
        # rather than argued in a docstring.
        f = FairValueArbInverse().evaluate(_ctx()).features
        model_cap = f['side_fair_value'] - FairValueArbInverse().edge_threshold
        assert model_cap < f['best_ask']

    def test_the_size_rule_is_the_parents_even_though_the_count_is_not(self):
        ctx = _ctx_parent_buys_down()
        inverse = FairValueArbInverse().evaluate(ctx)
        f = inverse.features
        s = FairValueArbInverse()

        # cap 0.76 -> floor(10 / 0.76) = 13 shares, under the 20 target.
        assert f['affordable_shares_at_cap'] == 13
        assert f['shares'] == 13
        assert f['shares_capped_by_notional'] is True
        assert f['notional_usdc'] <= s.max_notional_usdc
        # Same RULE, different COUNT, because the flipped side costs more.
        assert f['shares'] != f['parent_shares']


# ============ 3. stops and targets come from the FILL ============

class TestStopsComeFromTheActualFill:

    def test_the_entry_rows_stop_and_target_are_off_the_flipped_fill(self):
        f = FairValueArbInverse().evaluate(_ctx()).features
        s = FairValueArbInverse()

        assert f['effective_ask'] == pytest.approx(0.42)
        assert f['profit_target_price'] == pytest.approx(0.42 + s.min_profit)
        # The TIERED stop: 0.42 is in the [0.10, 0.50) tier, so 0.08 away.
        # It was `0.42 - s.max_loss` while `max_loss` was the stop.
        assert f['stop_price'] == pytest.approx(0.34)
        assert f['stop_price'] == pytest.approx(s.stop_price_for(0.42))
        assert f['stop_and_target_from_actual_fill_not_mirrored'] is True

    def test_they_are_NOT_the_parents_numbers_mirrored(self):
        ctx = _ctx()
        p = FairValueArb().evaluate(ctx).features
        i = FairValueArbInverse().evaluate(ctx).features

        # Mirroring would have produced 1 - 0.57 = 0.43 for the stop, which is
        # this strategy's PROFIT TARGET. A mirrored stop is not merely wrong by
        # a couple of cents, it is on the wrong side of the entry.
        mirrored_stop = round(1.0 - p['stop_price'], 4)
        assert i['stop_price'] != pytest.approx(mirrored_stop)
        assert i['stop_price'] < i['effective_ask'] < mirrored_stop

    def test_manage_exit_reads_the_positions_own_avg_price(self):
        s = FairValueArbInverse()
        book = _book(DOWN_TOK, asks=[(0.43, 100)], bids=[(0.41, 100)])
        d = s.manage_exit(_position(entry=0.42), book, now=WINDOW_TS + 150)

        assert d.features['entry_price'] == pytest.approx(0.42)
        # Tiered: 0.42 - 0.08. Was 0.39 under the flat 3c stop.
        assert d.features['stop_price'] == pytest.approx(0.34)
        assert d.features['profit_target_price'] == pytest.approx(0.43)

    def test_a_different_fill_moves_the_stop_with_it(self):
        # The proof that nothing is hardcoded or mirrored: change only the fill.
        s = FairValueArbInverse()
        book = _book(DOWN_TOK, asks=[(0.80, 100)], bids=[(0.78, 100)])
        d = s.manage_exit(_position(entry=0.79), book, now=WINDOW_TS + 150)

        # 0.79 sits in the >= 0.50 tier, so the stop is 0.10 away, not 0.03.
        # The point of the test is unchanged: only the fill moved.
        assert d.features['stop_price'] == pytest.approx(0.69)
        assert d.features['profit_target_price'] == pytest.approx(0.80)

    def test_the_price_stop_fires_off_the_flipped_entry(self):
        s = FairValueArbInverse()
        # bid 0.34 is the TIERED stop for a 0.42 entry. It was 0.38 against
        # the flat 3c stop at 0.39.
        book = _book(DOWN_TOK, asks=[(0.36, 100)], bids=[(0.34, 100)])
        d = s.manage_exit(_position(entry=0.42), book, now=WINDOW_TS + 150)

        assert d.action == 'EXIT'
        assert d.reason == 'price_stop'
        # A stop that refuses a bad price is not a stop.
        assert d.limit_price == fva.URGENT_SELL_LIMIT

    def test_the_profit_target_fires_off_the_flipped_entry(self):
        s = FairValueArbInverse()
        book = _book(DOWN_TOK, asks=[(0.45, 100)], bids=[(0.43, 100)])
        d = s.manage_exit(_position(entry=0.42), book, now=WINDOW_TS + 150)

        assert d.action == 'EXIT'
        assert d.reason == 'profit_target'
        assert d.limit_price == pytest.approx(0.43)


class TestStopIsStrictlyBelowEntry:
    """Convention 8, on both the discretionary stop and the structural one."""

    @pytest.mark.parametrize('ctx_factory', [_ctx, _ctx_parent_buys_down])
    def test_the_entry_row_has_a_stop_strictly_below_the_entry(self,
                                                               ctx_factory):
        f = FairValueArbInverse().evaluate(ctx_factory()).features
        assert f['stop_price'] < f['effective_ask']
        assert f['stop_price'] < f['entry_cap']

    @pytest.mark.parametrize('ctx_factory', [_ctx, _ctx_parent_buys_down])
    def test_the_structural_binary_stop_is_also_strictly_below(self,
                                                               ctx_factory):
        # On a binary a losing share is worth exactly 0.00, which satisfies
        # convention 8 by construction - but only if the entry is positive.
        s = FairValueArbInverse()
        signal = s.decision_to_signal(s.evaluate(ctx_factory()))

        assert signal is not None
        assert signal.stop == 0.0
        assert signal.target == 1.0
        assert signal.stop < signal.entry <= 1.0

    def test_an_entry_above_the_target_ceiling_is_refused(self):
        # At an entry of 0.99 the 1c target is 1.00, where no bid can sit, so
        # the position could only ever scratch, stop or time out.
        s = FairValueArbInverse()
        d = s.evaluate(_ctx(down_asks=((0.99, 200.0),),
                            down_bids=((0.97, 200.0),)))

        assert d.action == 'SKIP'
        assert d.reason == 'inverse_entry_above_profit_target_ceiling'
        assert d.features['max_entry_price'] == pytest.approx(0.98)
        assert d.features['profit_target_would_be'] == pytest.approx(1.00)

    def test_the_ceiling_is_derived_from_the_instance_not_written_down(self):
        assert FairValueArbInverse().max_entry_price == pytest.approx(0.98)
        assert FairValueArbInverse(
            min_profit=0.05).max_entry_price == pytest.approx(0.94)


# ============ 4. the flipped book's own refusals get their own names =========

class TestInverseSideRefusalsAreNeverPooled:
    """Conventions 11 and 20: two causes, two numbers.

    The parent's side passed every gate in each of these; the FLIPPED side did
    not. That is a different fact from "the market had no opportunity" and it
    must never land in the parent's counter.
    """

    def test_a_thin_opposite_book_gets_the_inverse_depth_reason(self):
        # 10 shares on the Down side, 200 on the Up side. The parent enters.
        ctx = _ctx(down_asks=((0.42, 10.0),))
        parent = FairValueArb().evaluate(ctx)
        inverse = FairValueArbInverse().evaluate(ctx)

        assert parent.action == 'ENTER', (parent.reason, parent.features)
        assert inverse.action == 'SKIP'
        assert inverse.reason == 'inverse_side_insufficient_book_depth'
        # NOT the parent's reason. This is the whole point of the test.
        assert inverse.reason != 'insufficient_book_depth'
        assert inverse.reason.startswith('inverse_')
        assert inverse.features['inverse_ask_depth_within_band'] == 10.0

    def test_an_empty_opposite_book_gets_its_own_reason(self):
        ctx = _ctx(down_asks=())
        assert FairValueArb().evaluate(ctx).action == 'ENTER'
        d = FairValueArbInverse().evaluate(ctx)

        assert d.action == 'SKIP'
        assert d.reason == 'inverse_side_no_ask'
        assert d.reason not in ('no_asks', 'no_orderbook')

    def test_an_unfillable_opposite_book_gets_its_own_reason(self):
        # Depth passes inside the 3c band (60 shares at 0.42 + 50 at 0.44 =
        # 110 >= 50) but only 5 shares sit at or under the 0.43 cap, so 20
        # shares cannot be filled without breaching the limit. A partial fill is
        # not an entry (convention 12).
        ctx = _ctx(down_asks=((0.42, 5.0), (0.44, 100.0)))
        assert FairValueArb().evaluate(ctx).action == 'ENTER'
        d = FairValueArbInverse().evaluate(ctx)

        assert d.action == 'SKIP'
        assert d.reason == 'inverse_side_unfillable_at_cap'
        assert d.reason != 'unfillable_at_cap'

    def test_every_inverse_refusal_is_namespaced(self):
        # A grep-able guarantee: no refusal produced AFTER the flip may share a
        # string with a parent refusal.
        contexts = [_ctx(down_asks=((0.42, 10.0),)),
                    _ctx(down_asks=()),
                    _ctx(down_asks=((0.42, 5.0), (0.44, 100.0))),
                    _ctx(down_asks=((0.99, 200.0),))]
        for ctx in contexts:
            d = FairValueArbInverse().evaluate(ctx)
            assert d.action == 'SKIP'
            assert d.reason.startswith('inverse_'), d.reason
            assert d.features['flip_applied'] is True

    def test_a_post_flip_skip_says_the_attempt_was_already_burned(self):
        # The parent burned one of its three attempts deciding ENTER before we
        # ever looked at the flipped book. Three of these exhaust a window
        # without a single fill, and that must be countable.
        d = FairValueArbInverse().evaluate(_ctx(down_asks=((0.42, 10.0),)))
        assert d.features['inverse_attempt_consumed_on_skip'] is True
        assert d.features['attempt_number'] == 1

    def test_a_pre_flip_skip_does_NOT_claim_a_burned_attempt(self):
        d = FairValueArbInverse().evaluate(
            _ctx(up_asks=((0.71, 200.0),), down_asks=((0.71, 200.0),)))
        assert d.reason == 'edge_below_threshold'
        assert 'inverse_attempt_consumed_on_skip' not in d.features


# ============ 5. the model exits are OFF, and that is deliberate ============

class TestModelExitsAreDisabled:
    """`model_stop` would fire on the first poll of every inverse position."""

    BOOK = staticmethod(lambda: _book(DOWN_TOK, asks=[(0.43, 100)],
                                      bids=[(0.41, 100)]))
    FAIR_DOWN = 0.29   # the model's view of the side we bought

    def test_the_parent_would_have_model_stopped_immediately(self):
        # The control. Without this the next test proves nothing: it could pass
        # because the fixture simply does not trigger anything.
        d = FairValueArb().manage_exit(_position(strategy=PARENT_NAME),
                                       self.BOOK(), now=WINDOW_TS + 150,
                                       fair_value=self.FAIR_DOWN)
        assert d.action == 'EXIT'
        assert d.reason == 'model_stop'

    def test_the_inverse_does_not(self):
        d = FairValueArbInverse().manage_exit(_position(), self.BOOK(),
                                              now=WINDOW_TS + 150,
                                              fair_value=self.FAIR_DOWN)
        assert d.action == 'HOLD'
        assert d.reason == 'waiting_for_convergence'

    def test_the_observed_fair_value_is_recorded_not_discarded(self):
        # Seen and refused is a different fact from never had it (convention 20).
        d = FairValueArbInverse().manage_exit(_position(), self.BOOK(),
                                              now=WINDOW_TS + 150,
                                              fair_value=self.FAIR_DOWN)
        assert d.features['model_fair_value_observed_not_acted_on'] == \
            pytest.approx(self.FAIR_DOWN)
        assert d.features['fair_value'] is None
        assert d.features['inverse_model_exits_disabled'] is True

    def test_convergence_is_off_too(self):
        # `converged` needs the ask back at fair value, which for an inverse
        # position is a level the ask never left. Bid at entry, ask above fair.
        book = _book(DOWN_TOK, asks=[(0.43, 100)], bids=[(0.42, 100)])
        d = FairValueArbInverse().manage_exit(_position(entry=0.42), book,
                                              now=WINDOW_TS + 150,
                                              fair_value=self.FAIR_DOWN)
        assert d.reason != 'converged'

    def test_the_four_surviving_rules_are_named_on_every_row(self):
        d = FairValueArbInverse().manage_exit(_position(), self.BOOK(),
                                              now=WINDOW_TS + 150,
                                              fair_value=self.FAIR_DOWN)
        assert d.features['inverse_active_exit_rules'] == [
            'window_close', 'price_stop', 'profit_target', 'time_stop']

    def test_window_close_still_fires(self):
        # The hard deadline is untouched: a Polymarket binary held past its
        # close has no sell path in paper mode.
        d = FairValueArbInverse().manage_exit(
            _position(opened_ts=WINDOW_TS + 260),
            _book(DOWN_TOK, asks=[(0.43, 100)], bids=[(0.41, 100)]),
            now=WINDOW_TS + 280, fair_value=self.FAIR_DOWN)
        assert d.action == 'EXIT'
        assert d.reason == 'window_close'

    def test_the_time_stop_still_fires(self):
        # And it will fire a LOT more often than the parent's, because the two
        # exits that used to catch a stalled position early are gone.
        d = FairValueArbInverse().manage_exit(
            _position(opened_ts=WINDOW_TS + 60),
            self.BOOK(), now=WINDOW_TS + 150, fair_value=self.FAIR_DOWN)
        assert d.action == 'EXIT'
        assert d.reason == 'time_stop'

    def test_exit_decisions_only_touches_this_strategys_positions(self):
        # Four-plus strategies poll the same open-position list. One that
        # managed another's positions would close trades it did not open and
        # book the PnL to the wrong population.
        s = FairValueArbInverse()
        books = {DOWN_TOK: self.BOOK()}
        positions = [_position(strategy=INVERSE_NAME),
                     _position(strategy=PARENT_NAME),
                     _position(strategy='PM_fair_value_arb_wide')]
        assert len(s.exit_decisions(positions, books, now=WINDOW_TS + 150)) == 1


# ============ 6. break-even, computed from the instance ============

class TestBreakevenWinRate:

    def test_it_is_the_parents_geometry_and_therefore_the_parents_number(self):
        # This variant moves NO constants, so 1c / 3c gives 75% - the same as
        # the parent and the same as the kill line. Not a coincidence and not a
        # tuned number: it is the same payoff shape.
        s = FairValueArbInverse()
        assert s.breakeven_win_rate == pytest.approx(0.75)
        assert s.breakeven_win_rate == pytest.approx(
            fva.MAX_LOSS / (fva.MIN_PROFIT + fva.MAX_LOSS))

    def test_it_is_computed_from_the_instance_not_hardcoded(self):
        s = FairValueArbInverse(min_profit=0.01, max_loss=0.01)
        assert s.breakeven_win_rate == pytest.approx(0.5)

    def test_a_degenerate_payoff_does_not_raise(self):
        assert FairValueArbInverse(min_profit=0.0,
                                   max_loss=0.0).breakeven_win_rate != \
            FairValueArbInverse(min_profit=0.0, max_loss=0.0).breakeven_win_rate

    def test_it_lands_on_the_entry_row(self):
        f = FairValueArbInverse().evaluate(_ctx()).features
        assert f['breakeven_win_rate'] == pytest.approx(0.75)

    def test_the_measured_parent_numbers_are_the_ones_from_the_log(self):
        # Source: research/polymarket_paper/polymarket_paper_log.csv,
        # PM_fair_value_arb, action == CLOSE. 33 closes is a SHRUG, not a
        # verdict (convention 7), and the flag says so on the row.
        m = inv_mod.PARENT_MEASURED
        assert m['closes'] == 33 and m['wins'] == 7
        assert m['win_rate'] == pytest.approx(7 / 33)
        assert m['total_pnl_usdc'] == pytest.approx(-28.0340)
        assert m['exits'] == {'price_stop': 26, 'profit_target': 7}
        assert m['sides'] == {'Down': 17, 'Up': 16}
        assert m['sample_is_a_shrug_not_a_verdict'] is True

        f = FairValueArbInverse().evaluate(_ctx()).features
        assert f['parent_measured_closes'] == 33
        assert f['parent_sample_is_a_shrug_not_a_verdict'] is True


# ============ 7. it inherits everything else, and it is not a fork ===========

class TestItIsAnInversionNotAFork:

    def test_every_parent_constant_is_untouched(self):
        # The hypothesis is inverted; the parameters are not. A variant that
        # changed both would tell you nothing about either.
        s, parent = FairValueArbInverse(), FairValueArb()
        for attr in ('edge_threshold', 'min_profit', 'max_loss',
                     'model_stop_margin', 'convergence_eps', 'time_stop_sec',
                     'window_close_exit_sec', 'min_entry_seconds_remaining',
                     'max_trades_per_window', 'target_shares',
                     'min_book_depth_shares', 'depth_band', 'max_notional_usdc',
                     'min_shares', 'min_fair_value', 'max_fair_value',
                     'model_uncertainty', 'atr_windows'):
            assert getattr(s, attr) == getattr(parent, attr), attr

    def test_the_fair_value_band_is_symmetric_so_inheriting_it_is_correct(self):
        # [0.10, 0.90] is symmetric about 0.5, so p in band implies 1 - p in
        # band. The gate means the same thing on both sides and needs no
        # adjustment - the justification for leaving it alone, asserted.
        s = FairValueArbInverse()
        assert s.min_fair_value + s.max_fair_value == pytest.approx(1.0)

    def test_it_uses_the_parents_fair_value_model_verbatim(self):
        assert FairValueArbInverse.estimate is FairValueArb.estimate
        assert FairValueArbInverse.observe is FairValueArb.observe

    def test_the_entry_timing_gates_are_the_parents(self):
        # Same clock, same window budget, same lateness rule - because they run
        # in the parent's own `evaluate`, which this calls unchanged.
        for kw in ({'seconds_into_window': 280.0},
                   {'seconds_into_window': None}):
            ctx = _ctx(**kw)
            assert (FairValueArbInverse().evaluate(ctx).reason
                    == FairValueArb().evaluate(ctx).reason)

    def test_the_attempt_cap_is_the_parents_three(self):
        s = FairValueArbInverse()
        for _ in range(3):
            assert s.evaluate(_ctx()).action == 'ENTER'
        d = s.evaluate(_ctx())
        assert d.action == 'SKIP'
        assert d.reason == 'max_trades_this_window'

    def test_two_instances_do_not_share_a_tape_or_a_ledger(self):
        a, b = FairValueArbInverse(), FairValueArbInverse()
        a.tape.observe(1.0, 100.0)
        a._note_attempt(WINDOW_TS)
        assert len(b.tape.samples) == 0
        assert b.trades_this_window(WINDOW_TS) == 0

    def test_it_manages_its_own_exits(self):
        assert FairValueArbInverse().manages_exits is True

    def test_it_never_raises_on_garbage(self):
        garbage = [
            MarketContext(window_ts=0),
            MarketContext(window_ts=WINDOW_TS, windows=[], market=_market(),
                          spot=float('nan'), seconds_into_window=10.0),
            MarketContext(window_ts=WINDOW_TS, windows=_windows(),
                          market=_market(), spot=0.0,
                          seconds_into_window=10.0),
            _ctx(up_asks=(), down_asks=()),
            _ctx(market=False),
        ]
        s = FairValueArbInverse()
        for ctx in garbage:
            d = s.evaluate(ctx)
            assert d.action in ('ENTER', 'QUOTE', 'SKIP')
            assert d.reason or d.action == 'ENTER'

    def test_manage_exit_survives_a_junk_position(self):
        class Junk:
            pass
        d = FairValueArbInverse().manage_exit(Junk(), None, now=WINDOW_TS + 100)
        assert d.action == 'HOLD' and d.reason


# ============ 8. house rules ============

class TestHouseRules:

    def test_paper_mode_in_the_module_on_the_class_and_on_the_row(self):
        assert inv_mod.PAPER_MODE is True
        assert FairValueArbInverse().paper_mode is True
        assert FairValueArbInverse().evaluate(_ctx()).features['paper_mode'] \
            is True

    def test_the_module_states_a_kill_condition_with_a_named_harness(self):
        # Convention 6: a proposal without a kill condition is a hope.
        doc = inv_mod.__doc__ or ''
        assert 'KILL CONDITION' in doc
        assert 'backtest/polymarket_harness.py' in doc
        assert '75%' in doc

    def test_beating_the_parent_is_explicitly_not_a_kill_condition(self):
        # Both of these can be losers. The bar is 75%, not "better than -$28".
        doc = inv_mod.__doc__ or ''
        assert 'NOT a kill condition' in doc

    def test_the_module_says_it_is_below_the_DOA_floor(self):
        # Convention 5. The naive inverse EV is +0.0016/share, which is at or
        # under the 30bps floor BEFORE the round-trip spread.
        doc = inv_mod.__doc__ or ''
        assert '0.0016' in doc
        assert '30bps' in doc or '30 bps' in doc

    def test_the_module_says_the_spread_does_not_invert(self):
        assert 'the spread does not invert' in (inv_mod.__doc__ or '')

    def test_the_status_is_NOT_TESTED(self):
        # Convention 11: NOT_TESTED means "could not run", never "ran and found
        # nothing". This strategy has never been scored.
        assert 'NOT_TESTED' in (inv_mod.__doc__ or '')

    def test_the_unverified_vendor_number_is_still_stamped(self):
        # Inverting a strategy does not launder its provenance. The 99.3% is
        # still a Reddit screenshot.
        for ctx in (_ctx(), _ctx(down_asks=((0.42, 10.0),))):
            f = FairValueArbInverse().evaluate(ctx).features
            assert f['claimed_win_rate_is_unverified_vendor_number'] is True
            assert f['trade_count_is_attempts_not_fills'] is True

    def test_the_signal_maps_onto_the_binary_payoff(self):
        s = FairValueArbInverse()
        signal = s.decision_to_signal(s.evaluate(_ctx()))
        assert signal is not None
        assert signal.pattern == INVERSE_NAME
        assert signal.stop == 0.0 and signal.target == 1.0
        assert 0.0 < signal.entry <= 1.0
        # Down side -> bearish, and the parent on this context is bullish.
        assert signal.direction == 'bearish'
        assert signal.features['outcome_side'] == 'Down'

    def test_the_scanner_path_never_invents_an_entry_without_a_book(self):
        candles = {
            'timestamps': [1000 + 300 * i for i in range(20)],
            'opens': [60000.0 + 100 * i for i in range(20)],
            'closes': [60100.0 + 100 * i for i in range(20)],
        }
        assert FairValueArbInverse().scan(candles) is None

    def test_ceil_to_tick_does_not_push_an_on_grid_price_up_a_tick(self):
        # `0.43 / 0.01` is 42.99999999999999 in binary floating point. Without
        # the epsilon this returns 0.44, and one tick is six times this
        # strategy's entire claimed edge.
        assert inv_mod.ceil_to_tick(0.43) == pytest.approx(0.43)
        assert inv_mod.ceil_to_tick(0.29) == pytest.approx(0.29)
        assert inv_mod.ceil_to_tick(0.421) == pytest.approx(0.43)
        assert inv_mod.ceil_to_tick(0.5, tick=0.0) == pytest.approx(0.5)
