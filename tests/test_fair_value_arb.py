"""Tests for the fair value model, PM_fair_value_arb, and the taker SELL path.

No network. Every orderbook, window and spot here is a synthetic fixture, so a
result is a statement about the code and not about whatever Polymarket happened
to be quoting.

Three jobs, in descending order of how much they matter:

  1. **The correlation rule must actually hold.** This strategy's only way to
     invent edge that does not exist is to count one BTC move as five
     independent confirmations. `TestSignalCorrelation` asserts that N
     correlated signals combine to the STRONGEST, never the product, and that
     the accounting identity over suppressed signals holds. If that test ever
     goes green while the implementation multiplies, every other number in this
     file is decoration.

  2. **The strategy must be provably ALIVE and provably PICKY.** A synthetic
     context that satisfies the rules produces an entry, and a
     one-condition-off context produces a NAMED skip. A port that silently
     never fires looks identical in a graveyard to one that was honestly
     measured and failed.

  3. **The exit rules must fire in the documented order and the UNSELLABLE case
     must stay loud.** The whole claimed edge is "we exit before resolution", so
     the case where the book will not let us is the case the tests have to pin
     down. `simulate_taker_sell` refusing a partial and LEAVING THE POSITION
     OPEN is asserted, not assumed.

There is deliberately NO harness sweep here. Per D-268 this strategy is
NOT_TESTED until a resolution-PnL harness exists; running the price-path harness
on it would fabricate numbers.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.halt as halt_mod  # noqa: E402
from engine.polymarket.fair_value import (CLUSTER_BTC_MOVE,  # noqa: E402
                                          MEAN_ABS_TO_SIGMA, PROB_CEIL,
                                          PROB_FLOOR, FairValueSignal,
                                          PriceTape, book_imbalance,
                                          combine_multipliers,
                                          diffusion_signal, estimate_fair_value,
                                          imbalance_signal, normal_cdf, odds,
                                          revise_probability, speed_signal)
from engine.polymarket.paper_adapter import (PaperPosition,  # noqa: E402
                                             PolymarketPaperAdapter)
from engine.polymarket.types import Market, Orderbook, Outcome  # noqa: E402
from engine.polymarket.types import PriceLevel  # noqa: E402
from strategies.polymarket import build_strategies  # noqa: E402
from strategies.polymarket.base import MarketContext, Window  # noqa: E402
from strategies.polymarket.fair_value_arb import (FairValueArb,  # noqa: E402
                                                  floor_to_tick)
import strategies.polymarket.fair_value_arb as fva  # noqa: E402


UP_TOK = 'tok-up'
DOWN_TOK = 'tok-down'
WINDOW_TS = 1_700_000_000
SLUG = 'btc-updown-5m-1700000000'


# ============ fixtures ============

@pytest.fixture(autouse=True)
def never_halted(tmp_path, monkeypatch):
    """Point the kill switch at a path that does not exist.

    Without this, a real HALT file left in the repo root by a drill turns every
    entry test into a `halted` skip - which would be the switch WORKING, and
    indistinguishable in the output from the strategy being broken.
    """
    monkeypatch.setattr(halt_mod, 'HALT_FILE', str(tmp_path / 'NO_SUCH_HALT'))


class StubClient:
    """Any network call from these tests is a test bug."""

    def __init__(self):
        self.calls = []
        self.stats = {'requests': 0, 'retries': 0, 'failures': 0,
                      'rate_limit_waits': 0}

    def clob(self, path, params=None):
        self.calls.append((path, params))
        return None

    gamma = clob
    data = clob


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
    """`n` 5m bars, the last of which is THIS window (ts == WINDOW_TS).

    Alternating +/- `move` so `window_atr` over 12 bars is exactly `move`, which
    makes every sigma in these tests hand-checkable.
    """
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
    """A context that, with default arguments, is a valid ENTRY for Up.

    Defaults chosen so fair value lands mid-band: BTC is $60 above a $100,000
    open with an ATR of $90 and 200 seconds left, which puts P(Up) near 0.71
    while the Up ask sits at 0.60 - an 11c gap, comfortably over the 4c
    threshold, on 200 shares of depth.
    """
    books = {UP_TOK: _book(UP_TOK, up_asks, up_bids),
             DOWN_TOK: _book(DOWN_TOK, down_asks, down_bids)}
    return MarketContext(
        window_ts=WINDOW_TS,
        windows=_windows() if windows is None else windows,
        market=_market() if market else None,
        books=books, spot=spot, strike=None,
        seconds_into_window=seconds_into_window)


def _position(entry=0.60, shares=20.0, opened_ts=WINDOW_TS + 100,
              window_ts=WINDOW_TS, side='Up', token_id=UP_TOK,
              strategy='PM_fair_value_arb'):
    return PaperPosition(
        position_id='pos-1', strategy=strategy, market_slug=SLUG,
        token_id=token_id, outcome_side=side, shares=shares, avg_price=entry,
        cost_usdc=entry * shares, fee_usdc=0.0, opened_ts=opened_ts,
        window_ts=window_ts)


def make_adapter(tmp_path, **cfg):
    cfg.setdefault('notional_cap_usdc', 100.0)
    cfg.setdefault('starting_equity_usdc', 2000.0)
    return PolymarketPaperAdapter(client=StubClient(),
                                  config={'polymarket': cfg},
                                  log_dir=str(tmp_path / 'pmlog'))


# ============ 1. the Bayesian revision rule ============

class TestBayesianRevision:

    def test_a_neutral_multiplier_changes_nothing(self):
        for p in (0.1, 0.35, 0.5, 0.8):
            assert revise_probability(p, 1.0) == pytest.approx(p, abs=1e-12)

    def test_the_articles_worked_example(self):
        # prior odds 1.0 * 3 = 3, posterior = 3/4.
        assert revise_probability(0.5, 3.0) == pytest.approx(0.75)

    def test_it_is_odds_multiplication_not_probability_multiplication(self):
        # The whole reason for the odds form. Naive p * m would give 0.5 * 1.5
        # = 0.75; the correct answer is 0.6, and the difference is the size of
        # this strategy's entire edge threshold three times over.
        assert revise_probability(0.5, 1.5) == pytest.approx(0.6)

    def test_inverse_multipliers_are_symmetric_about_the_prior(self):
        up = revise_probability(0.5, 4.0)
        down = revise_probability(0.5, 0.25)
        assert up + down == pytest.approx(1.0)

    def test_the_prior_is_clamped_so_certainty_is_never_absorbing(self):
        # p=1.0 would give infinite prior odds and a posterior pinned at 1.0
        # whatever the evidence said. Clamping is what keeps evidence able to
        # move the estimate at all.
        assert revise_probability(1.0, 0.001) < 1.0
        assert revise_probability(0.0, 1000.0) > 0.0

    @pytest.mark.parametrize('bad', [0.0, -1.0, float('inf'), float('nan')])
    def test_a_degenerate_multiplier_raises_rather_than_asserting_certainty(
            self, bad):
        # Returning 0.0 here would file a code defect under "the model is sure
        # the answer is Down" (convention 11).
        with pytest.raises(ValueError):
            revise_probability(0.5, bad)

    def test_odds_round_trips_through_the_clamp(self):
        assert odds(0.5) == pytest.approx(1.0)
        assert odds(0.8) == pytest.approx(4.0)
        assert math.isfinite(odds(1.0))


# ============ 2. correlated signals must not multiply ============

class TestSignalCorrelation:
    """The load-bearing test in this file. See the module docstring."""

    @staticmethod
    def _sig(name, mult, cluster=CLUSTER_BTC_MOVE):
        return FairValueSignal(name, cluster, mult)

    def test_correlated_signals_collapse_to_the_strongest(self):
        sigs = [self._sig('a', 1.2), self._sig('b', 2.5), self._sig('c', 1.4)]
        combined, winners, census = combine_multipliers(sigs)

        assert combined == pytest.approx(2.5)
        assert [w.name for w in winners] == ['b']
        assert census['winner_by_cluster'] == {CLUSTER_BTC_MOVE: 'b'}
        assert census['suppressed_correlated'] == 2

    def test_the_strongest_rule_is_strictly_weaker_than_the_product(self):
        # 1.2 * 2.5 * 1.4 = 4.2, i.e. P(Up) = 0.808 instead of 0.714. Nine
        # points of fabricated confidence out of one BTC move, which is more
        # than twice this strategy's entry threshold.
        sigs = [self._sig('a', 1.2), self._sig('b', 2.5), self._sig('c', 1.4)]
        combined, _w, _c = combine_multipliers(sigs)
        product = 1.2 * 2.5 * 1.4

        assert combined < product
        assert revise_probability(0.5, product) - revise_probability(0.5, combined) \
            > fva.EDGE_THRESHOLD

    def test_strength_is_direction_blind_so_a_strong_bearish_signal_can_win(self):
        # 1/3 is a stronger claim than 1.5, and it points the other way.
        sigs = [self._sig('bullish', 1.5), self._sig('bearish', 1.0 / 3.0)]
        combined, winners, _c = combine_multipliers(sigs)

        assert winners[0].name == 'bearish'
        assert combined == pytest.approx(1.0 / 3.0)
        assert revise_probability(0.5, combined) < 0.5

    def test_declared_independent_clusters_do_multiply(self):
        sigs = [self._sig('a', 2.0), self._sig('b', 3.0, cluster='other')]
        combined, winners, census = combine_multipliers(sigs)

        assert combined == pytest.approx(6.0)
        assert len(winners) == 2
        assert census['suppressed_correlated'] == 0

    def test_the_accounting_identity_holds(self):
        sigs = [self._sig('a', 1.1), self._sig('b', 1.9),
                self._sig('c', 1.3, cluster='other'),
                self._sig('d', 2.2, cluster='other')]
        _m, _w, census = combine_multipliers(sigs)

        assert census['used'] + census['suppressed_correlated'] == census['seen']
        assert census['seen'] == 4

    def test_no_signals_is_a_neutral_multiplier(self):
        combined, winners, census = combine_multipliers([])
        assert combined == 1.0
        assert winners == []
        assert census['seen'] == 0

    def test_every_real_signal_the_model_builds_is_in_one_cluster(self):
        # The claim the design rests on: displacement, speed and book imbalance
        # are three views of one BTC move. If somebody adds a fourth signal in a
        # NEW cluster it will start multiplying, and this test is where they
        # have to justify that.
        est = estimate_fair_value(spot=100_060.0, window_open=100_000.0,
                                  atr_usd=90.0, seconds_remaining=200.0,
                                  up_book=_book(UP_TOK, [(0.60, 200)], [(0.58, 400)]),
                                  down_book=_book(DOWN_TOK, [(0.42, 200)], [(0.40, 50)]),
                                  recent_speed=2.0, baseline_speed=0.5)

        assert {s.cluster for s in est.signals} == {CLUSTER_BTC_MOVE}
        assert len(est.winners) == 1
        assert est.census['suppressed_correlated'] == len(est.signals) - 1

    def test_imbalance_is_capped_below_the_speed_cap(self):
        # Book depth is the closest thing here to reading the market's own
        # price, so it is capped tightest on purpose: "our" fair value must not
        # be able to become "their" price restated. Even at a maximally
        # one-sided book it moves fair value by ~6c, which is inside the range
        # the entry threshold refuses on its own.
        strongest_imbalance = imbalance_signal(1.0)
        strongest_speed = speed_signal(1000.0, 0.001)

        assert strongest_imbalance.strength < strongest_speed.strength
        assert abs(revise_probability(0.5, strongest_imbalance.multiplier)
                   - 0.5) < 0.07


# ============ 3. the diffusion signal: distance, vol and time as ONE signal ==

class TestDiffusionSignal:

    def test_it_matches_phi_of_z_computed_by_hand(self):
        # displacement 60, ATR 90 -> sigma_window = 90 * sqrt(pi/2) = 112.79
        # tau = 200/300 -> sigma_remaining = 112.79 * sqrt(2/3) = 92.09
        # z = 60 / 92.09 = 0.6515 -> Phi(z) = 0.7426
        sig = diffusion_signal(60.0, 90.0 * MEAN_ABS_TO_SIGMA, 200.0)
        expected_z = 60.0 / (90.0 * MEAN_ABS_TO_SIGMA * math.sqrt(200.0 / 300.0))

        assert sig.detail['z'] == pytest.approx(expected_z)
        assert sig.detail['p_up_implied'] == pytest.approx(normal_cdf(expected_z))

    def test_the_mean_absolute_to_sigma_conversion_is_applied_end_to_end(self):
        # Skipping sqrt(pi/2) understates sigma by 20% and overstates z by 25%
        # on EVERY window - a systematic overconfidence, not a rounding error.
        est = estimate_fair_value(spot=100_060.0, window_open=100_000.0,
                                  atr_usd=90.0, seconds_remaining=200.0)
        assert est.inputs['sigma_window_usd'] == pytest.approx(
            90.0 * MEAN_ABS_TO_SIGMA)

    def test_probability_converges_toward_one_as_the_window_closes(self):
        far = estimate_fair_value(100_060.0, 100_000.0, 90.0, 290.0).probability
        near = estimate_fair_value(100_060.0, 100_000.0, 90.0, 60.0).probability
        closing = estimate_fair_value(100_060.0, 100_000.0, 90.0, 1.0).probability

        assert far < near < closing
        assert closing > 0.9

    def test_a_displacement_of_zero_is_a_coin_flip_at_any_time(self):
        for remaining in (300.0, 120.0, 1.0):
            est = estimate_fair_value(100_000.0, 100_000.0, 90.0, remaining)
            assert est.probability == pytest.approx(0.5, abs=1e-9)

    def test_higher_realized_vol_pulls_fair_value_back_toward_a_coin_flip(self):
        calm = estimate_fair_value(100_060.0, 100_000.0, 90.0, 200.0,
                                   realized_sigma_usd=90.0 * MEAN_ABS_TO_SIGMA)
        wild = estimate_fair_value(100_060.0, 100_000.0, 90.0, 200.0,
                                   realized_sigma_usd=250.0 * MEAN_ABS_TO_SIGMA)
        assert 0.5 < wild.probability < calm.probability

    def test_direction_follows_the_sign_of_the_displacement(self):
        up = estimate_fair_value(100_060.0, 100_000.0, 90.0, 200.0).probability
        down = estimate_fair_value(99_940.0, 100_000.0, 90.0, 200.0).probability
        assert up > 0.5 > down
        assert up + down == pytest.approx(1.0)

    def test_a_zero_sigma_raises_rather_than_making_every_move_infinite(self):
        with pytest.raises(ValueError):
            diffusion_signal(60.0, 0.0, 200.0)

    @pytest.mark.parametrize('kwargs,reason', [
        (dict(spot=None), 'no_spot'),
        (dict(window_open=None), 'no_window_open'),
        (dict(atr_usd=None), 'no_atr'),
        (dict(seconds_remaining=None), 'no_window_clock'),
        (dict(atr_usd=0.0), 'zero_atr_undefined_sigma'),
        (dict(spot=float('nan')), 'non_finite_input'),
        (dict(spot=-1.0), 'non_positive_price'),
        (dict(seconds_remaining=-5.0), 'window_already_closed'),
    ])
    def test_missing_or_corrupt_inputs_are_named_not_defaulted(self, kwargs,
                                                               reason):
        # `usable=False` means CANNOT ESTIMATE. It must never look like a
        # confident 0.5 (convention 11).
        base = dict(spot=100_060.0, window_open=100_000.0, atr_usd=90.0,
                    seconds_remaining=200.0)
        base.update(kwargs)
        est = estimate_fair_value(**base)

        assert est.usable is False
        assert est.reason == reason

    def test_probabilities_stay_inside_the_clamp_and_serialisable(self):
        est = estimate_fair_value(120_000.0, 100_000.0, 5.0, 1.0)
        assert PROB_FLOOR <= est.probability <= PROB_CEIL
        assert math.isfinite(est.combined_multiplier)

    def test_for_side_refuses_to_guess_an_unknown_outcome_label(self):
        est = estimate_fair_value(100_060.0, 100_000.0, 90.0, 200.0)
        assert est.for_side('Up') == est.probability
        assert est.for_side('down') == pytest.approx(1.0 - est.probability)
        with pytest.raises(ValueError):
            est.for_side('Maybe')


class TestSpeedAndImbalanceSignals:

    def test_speed_is_signed_and_bounded_by_its_cap(self):
        from engine.polymarket.fair_value import SPEED_LOG_CAP

        fast_up = speed_signal(50.0, 1.0)
        fast_down = speed_signal(-50.0, 1.0)
        absurd = speed_signal(40_000.0, 0.001)

        assert fast_up.multiplier > 1.0 > fast_down.multiplier
        assert fast_up.strength == pytest.approx(fast_down.strength)
        # A 40,000x speed spike must not become a 40,000x multiplier.
        assert absurd.strength <= SPEED_LOG_CAP + 1e-9

    def test_speed_without_a_baseline_is_neutral_not_confident(self):
        sig = speed_signal(50.0, 0.0)
        assert sig.multiplier == 1.0
        assert sig.detail['usable'] == 0.0

    def test_imbalance_reads_both_tokens(self):
        # Heavy Up bids AND heavy Down offers both mean "buy Up".
        up = _book(UP_TOK, asks=[(0.60, 10)], bids=[(0.58, 500)])
        down = _book(DOWN_TOK, asks=[(0.42, 500)], bids=[(0.40, 10)])
        value, detail = book_imbalance(up, down)

        assert value is not None and value > 0.5
        assert detail['pressure_up'] > detail['pressure_down']

    def test_imbalance_with_no_depth_is_unmeasurable_not_balanced(self):
        value, _detail = book_imbalance(None, None)
        assert value is None
        assert imbalance_signal(value).detail['usable'] == 0.0

    def test_far_away_resting_size_is_not_counted_as_an_opinion(self):
        # A 5,000-share offer parked at 0.02 is not a view on this window.
        near = _book(UP_TOK, asks=[(0.60, 20)], bids=[(0.58, 20)])
        far = _book(UP_TOK, asks=[(0.60, 20), (0.99, 5000)], bids=[(0.58, 20)])
        a, _ = book_imbalance(near, None)
        b, _ = book_imbalance(far, None)
        assert a == pytest.approx(b)


# ============ 4. the price tape ============

class TestPriceTape:

    def test_it_refuses_corrupt_and_out_of_order_observations(self):
        tape = PriceTape()
        assert tape.observe(100.0, 50_000.0) is True
        assert tape.observe(105.0, float('nan')) is False
        assert tape.observe(105.0, 0.0) is False
        assert tape.observe(99.0, 50_100.0) is False   # backwards in time
        assert len(tape.samples) == 1

    def test_speed_is_signed_usd_per_second(self):
        tape = PriceTape()
        for i in range(7):
            tape.observe(1000.0 + i * 5.0, 50_000.0 + i * 10.0)
        # 30 seconds of samples, +$60 total -> +$2/sec.
        assert tape.speed(30.0) == pytest.approx(2.0)

    def test_a_lookback_the_tape_does_not_span_returns_none(self):
        # A "30-second speed" measured over 5 seconds is a different statistic
        # wearing the same name, and it is the one that fires on poll jitter.
        tape = PriceTape()
        tape.observe(1000.0, 50_000.0)
        tape.observe(1005.0, 50_050.0)
        assert tape.speed(30.0) is None

    def test_baseline_speed_is_direction_blind(self):
        tape = PriceTape()
        prices = [50_000, 50_100, 50_000, 50_100, 50_000]
        for i, p in enumerate(prices):
            tape.observe(1000.0 + i * 60.0, float(p))
        # 400 dollars of travel over 240 seconds, net zero.
        assert tape.baseline_speed(300.0) == pytest.approx(400.0 / 240.0)
        assert tape.speed(240.0) == pytest.approx(0.0)

    def test_realized_sigma_is_none_on_a_flat_tape(self):
        # Zero variance is not a claim that price cannot move next minute, and
        # feeding a 0 into the diffusion would raise.
        tape = PriceTape()
        for i in range(20):
            tape.observe(1000.0 + i * 15.0, 50_000.0)
        assert tape.realized_sigma(300.0) is None

    def test_realized_sigma_grows_with_dispersion(self):
        def _sigma(step):
            tape = PriceTape()
            for i in range(21):
                tape.observe(1000.0 + i * 15.0,
                             50_000.0 + (step if i % 2 else -step))
            return tape.realized_sigma(300.0)

        assert _sigma(200.0) > _sigma(20.0) > 0

    def test_old_samples_are_dropped(self):
        tape = PriceTape(max_age_seconds=60.0)
        for i in range(40):
            tape.observe(1000.0 + i * 5.0, 50_000.0 + i)
        assert tape.samples[0][0] >= tape.samples[-1][0] - 60.0


# ============ 5. entry ============

class TestEntry:

    def test_a_clean_mispricing_produces_an_entry(self):
        s = FairValueArb()
        d = s.evaluate(_ctx())

        assert d.action == 'ENTER', d.reason
        assert d.features['outcome_side'] == 'Up'
        assert d.features['realized_edge'] > fva.EDGE_THRESHOLD
        leg = d.primary_leg
        assert leg.order_type == 'taker'
        assert leg.expected_price == pytest.approx(0.60)
        assert leg.limit_price >= leg.expected_price

    def test_the_entry_cap_is_fair_value_minus_the_threshold(self):
        s = FairValueArb()
        d = s.evaluate(_ctx())
        fair = d.features['side_fair_value']

        assert d.features['entry_cap'] == pytest.approx(
            floor_to_tick(fair - fva.EDGE_THRESHOLD))

    def test_the_reported_premium_is_the_walked_average_not_the_cap(self):
        # base.Leg.premium's house rule: reporting the cap as the entry is how
        # a binary backtest books a 47c fill as a 55c one. Here the top level
        # is 8 shares and the order needs more, so the walked average must land
        # strictly between the two levels.
        s = FairValueArb()
        d = s.evaluate(_ctx(up_asks=((0.58, 8.0), (0.60, 200.0))))

        leg = d.primary_leg
        assert d.action == 'ENTER', d.reason
        assert leg.shares > 8
        assert 0.58 < leg.expected_price < 0.60
        assert leg.expected_price != leg.limit_price

    def test_it_takes_whichever_side_is_mispriced(self):
        # BTC $60 BELOW the open, so the model's favourite is Down and the
        # mispricing to look for is on the Down ask. Neither side cheap enough
        # -> skip; Down cheap -> Down entry. The Up side is never chosen here,
        # which is the point: the strategy follows the gap, not the direction.
        both_fair = FairValueArb().evaluate(
            _ctx(spot=99_940.0, up_asks=((0.42, 200.0),),
                 down_asks=((0.70, 200.0),)))
        assert both_fair.action == 'SKIP'
        assert both_fair.reason == 'edge_below_threshold'

        down_cheap = FairValueArb().evaluate(
            _ctx(spot=99_940.0, up_asks=((0.60, 200.0),),
                 down_asks=((0.55, 200.0),)))
        assert down_cheap.action == 'ENTER', down_cheap.reason
        assert down_cheap.features['outcome_side'] == 'Down'

    def test_no_mispricing_is_a_named_skip(self):
        s = FairValueArb()
        d = s.evaluate(_ctx(up_asks=((0.70, 200.0),), down_asks=((0.60, 200.0),)))

        assert d.action == 'SKIP'
        assert d.reason == 'edge_below_threshold'
        assert d.features['raw_edge'] <= fva.EDGE_THRESHOLD

    def test_the_threshold_actually_binds(self):
        # 3c of gap is not enough, 5c is. If this ever passes at 3c the
        # threshold has been moved and the edge arithmetic no longer holds.
        s = FairValueArb()
        fair = s.evaluate(_ctx()).features['side_fair_value']

        thin = FairValueArb().evaluate(
            _ctx(up_asks=((round(fair - 0.03, 2), 200.0),)))
        fat = FairValueArb().evaluate(
            _ctx(up_asks=((round(fair - 0.05, 2), 200.0),)))

        assert thin.reason == 'edge_below_threshold'
        assert fat.action == 'ENTER', fat.reason

    def test_a_thin_book_is_refused_even_with_a_huge_gap(self):
        # A 4c gap against a 6-share top level is one stale quote, which is
        # proposal 001's own stated open risk.
        s = FairValueArb()
        d = s.evaluate(_ctx(up_asks=((0.45, 6.0), (0.99, 500.0))))

        assert d.reason == 'insufficient_book_depth'
        assert d.features['ask_depth_within_band'] < fva.MIN_BOOK_DEPTH_SHARES

    def test_depth_is_only_counted_within_the_band(self):
        # 500 shares exist, but 8c away. That is not depth at this price.
        s = FairValueArb()
        d = s.evaluate(_ctx(up_asks=((0.60, 10.0), (0.68, 500.0))))
        assert d.reason == 'insufficient_book_depth'

    def test_three_attempts_per_window_then_it_stops(self):
        s = FairValueArb()
        for i in range(fva.MAX_TRADES_PER_WINDOW):
            assert s.evaluate(_ctx()).action == 'ENTER', 'attempt {}'.format(i)

        d = s.evaluate(_ctx())
        assert d.reason == 'max_trades_this_window'
        assert d.features['trades_this_window'] == fva.MAX_TRADES_PER_WINDOW

    def test_the_attempt_counter_is_per_window(self):
        s = FairValueArb()
        for _ in range(fva.MAX_TRADES_PER_WINDOW):
            s.evaluate(_ctx())
        assert s.trades_this_window(WINDOW_TS) == fva.MAX_TRADES_PER_WINDOW
        assert s.trades_this_window(WINDOW_TS + 300) == 0

    def test_the_attempt_counter_says_it_counts_attempts_not_fills(self):
        # Convention 22. Nothing downstream may compute a fill rate from this.
        d = FairValueArb().evaluate(_ctx())
        assert d.features['trade_count_is_attempts_not_fills'] is True

    def test_no_new_entry_late_in_the_window(self):
        s = FairValueArb()
        d = s.evaluate(_ctx(seconds_into_window=260.0))

        assert d.reason == 'too_late_in_window'
        assert d.features['seconds_remaining'] == pytest.approx(40.0)

    def test_the_entry_deadline_leaves_room_for_the_close_out(self):
        assert fva.MIN_ENTRY_SECONDS_REMAINING > fva.WINDOW_CLOSE_EXIT_SEC
        d = FairValueArb().evaluate(_ctx(seconds_into_window=240.0))
        assert d.features['holding_seconds_available'] == pytest.approx(30.0)

    def test_an_extreme_fair_value_is_out_of_band(self):
        # Deep in the money this becomes Dan1ro0 4E (near-resolution capture),
        # which Raven's analysis says not to build without its own limits.
        s = FairValueArb()
        d = s.evaluate(_ctx(spot=100_600.0, seconds_into_window=200.0,
                            up_asks=((0.50, 500.0),)))

        assert d.reason == 'fair_value_outside_tradeable_band'
        assert d.features['fair_value_up'] > fva.MAX_TRADEABLE_FAIR_VALUE

    def test_an_unusable_fair_value_is_a_named_skip_not_a_coin_flip(self):
        s = FairValueArb()
        d = s.evaluate(_ctx(spot=None))

        assert d.action == 'SKIP'
        assert d.reason == 'fair_value_no_spot'
        assert d.features['fair_value_usable'] is False

    def test_no_window_clock_is_a_skip(self):
        ctx = _ctx()
        ctx.seconds_into_window = None
        assert FairValueArb().evaluate(ctx).reason == 'no_window_clock'

    def test_no_market_is_a_skip(self):
        assert FairValueArb().evaluate(_ctx(market=False)).reason == 'no_market'

    def test_an_empty_book_is_a_skip(self):
        s = FairValueArb()
        d = s.evaluate(_ctx(up_asks=(), down_asks=()))
        assert d.reason == 'no_asks'

    def test_size_is_scaled_down_to_the_notional_cap_not_rejected(self):
        # 20 shares at a 0.67 cap is $13.40, over the $10 per-trade cap. The
        # strategy must report a smaller size, not let the adapter refuse the
        # order as a risk block - those are different facts.
        s = FairValueArb()
        d = s.evaluate(_ctx())

        assert d.action == 'ENTER', d.reason
        shares = d.features['shares']
        assert shares * d.features['entry_cap'] <= fva.MAX_NOTIONAL_USDC + 1e-9
        assert d.features['shares_capped_by_notional'] is (
            shares < fva.TARGET_SHARES)

    def test_twenty_shares_survives_when_the_cap_allows_it(self):
        # BTC barely off the open, so fair value is near 0.52 and the entry cap
        # lands under 0.50 - where 20 shares fits inside the $10 per-trade cap,
        # which is the brief's stated sizing.
        s = FairValueArb()
        d = s.evaluate(_ctx(spot=100_005.0, up_asks=((0.40, 300.0),)))

        assert d.action == 'ENTER', d.reason
        assert d.features['entry_cap'] <= 0.50
        assert d.features['shares'] == fva.TARGET_SHARES
        assert d.features['shares_capped_by_notional'] is False

    def test_a_notional_too_small_for_the_exchange_minimum_is_cannot_run(self):
        # Convention 11 / the D-249 shape: could not run, did not lose.
        s = FairValueArb(max_notional_usdc=2.0)
        d = s.evaluate(_ctx())

        assert d.reason == 'unsizable_at_notional_cap'
        assert d.features['affordable_shares_at_cap'] < fva.MIN_SHARES

    def test_the_depth_gate_normally_makes_the_walk_gate_unreachable(self):
        # An invariant worth pinning rather than discovering later: the depth
        # band (3c) is NARROWER than the edge threshold (4c), so anything
        # counted as depth is priced strictly under the entry cap, and the
        # required size (<= 20) is always under the depth floor (50). With the
        # default constants `unfillable_at_cap` is therefore a defensive guard,
        # not a live branch. If someone widens DEPTH_BAND past EDGE_THRESHOLD
        # this stops holding and the walk gate becomes load-bearing.
        assert fva.DEPTH_BAND < fva.EDGE_THRESHOLD
        assert fva.TARGET_SHARES < fva.MIN_BOOK_DEPTH_SHARES

    def test_a_book_that_cannot_fill_the_size_under_the_cap_is_refused(self):
        # Reachable only with the depth floor relaxed (see the test above), so
        # the guard itself is still exercised: 4 shares sit under the cap and
        # everything else is priced over it, so the walk cannot complete and a
        # PARTIAL is not an entry.
        fair = FairValueArb().evaluate(_ctx()).features['side_fair_value']
        cap = floor_to_tick(fair - fva.EDGE_THRESHOLD)
        d = FairValueArb(min_book_depth_shares=1.0).evaluate(
            _ctx(up_asks=((round(cap - 0.05, 2), 4.0),
                          (round(cap + 0.05, 2), 500.0))))

        assert d.reason == 'unfillable_at_cap'
        assert d.features['shares'] > 4

    def test_every_row_carries_the_unverified_provenance_stamp(self):
        for ctx in (_ctx(), _ctx(market=False), _ctx(spot=None),
                    _ctx(seconds_into_window=290.0)):
            d = FairValueArb().evaluate(ctx)
            assert d.features['claimed_win_rate_is_unverified_vendor_number'] \
                is True

    def test_the_tape_is_fed_on_skipping_cycles_too(self):
        # A tape that only fills on tradeable cycles has holes exactly where the
        # market was quiet, which is where the vol baseline comes from.
        s = FairValueArb()
        s.evaluate(_ctx(up_asks=((0.70, 200.0),), seconds_into_window=10.0))
        s.evaluate(_ctx(up_asks=((0.70, 200.0),), seconds_into_window=20.0))
        assert len(s.tape.samples) == 2

    def test_every_skip_has_a_non_empty_reason(self):
        contexts = [_ctx(market=False), _ctx(spot=None), _ctx(up_asks=(), down_asks=()),
                    _ctx(seconds_into_window=280.0),
                    _ctx(up_asks=((0.70, 200.0),), down_asks=((0.60, 200.0),)),
                    _ctx(up_asks=((0.45, 6.0),))]
        for ctx in contexts:
            d = FairValueArb().evaluate(ctx)
            if d.action == 'SKIP':
                assert d.reason, 'a silent skip is a missing number'


# ============ 6. exits ============

class TestExit:
    """Rule ORDER is the contract here, not just the individual triggers."""

    def test_a_bid_at_the_target_takes_profit(self):
        s = FairValueArb()
        book = _book(UP_TOK, asks=[(0.63, 100)], bids=[(0.61, 100)])
        d = s.manage_exit(_position(entry=0.60), book, now=WINDOW_TS + 110)

        assert d.action == 'EXIT'
        assert d.reason == 'profit_target'
        # Limit at the target, not at the bid: walking depth must not average
        # us below the rule we exited on.
        assert d.limit_price == pytest.approx(0.61)

    def test_a_bid_at_the_stop_closes_at_any_price(self):
        s = FairValueArb()
        book = _book(UP_TOK, asks=[(0.60, 100)], bids=[(0.57, 100)])
        d = s.manage_exit(_position(entry=0.60), book, now=WINDOW_TS + 110)

        assert d.reason == 'price_stop'
        # A stop that refuses a bad price is not a stop.
        assert d.limit_price == fva.URGENT_SELL_LIMIT

    def test_the_time_stop_fires_after_sixty_seconds(self):
        s = FairValueArb()
        book = _book(UP_TOK, asks=[(0.61, 100)], bids=[(0.59, 100)])
        pos = _position(entry=0.60, opened_ts=WINDOW_TS + 100)

        early = s.manage_exit(pos, book, now=WINDOW_TS + 155)   # age 55
        late = s.manage_exit(pos, book, now=WINDOW_TS + 165)    # age 65

        assert early.action == 'HOLD'
        assert early.reason == 'waiting_for_convergence'
        assert late.reason == 'time_stop'

    def test_the_window_close_out_beats_every_other_rule(self):
        # Under 30s left the position is a directional bet on the resolution,
        # which is a different strategy. Cut it whatever the PnL says.
        s = FairValueArb()
        book = _book(UP_TOK, asks=[(0.63, 100)], bids=[(0.61, 100)])
        d = s.manage_exit(_position(entry=0.60), book, now=WINDOW_TS + 280)

        assert d.reason == 'window_close'
        assert d.features['seconds_remaining'] == pytest.approx(20.0)

    def test_the_model_stop_fires_on_fair_value_alone(self):
        # The book has not moved. The reason we bought has. This is the exit
        # that makes this a model and not a trailing stop.
        s = FairValueArb()
        book = _book(UP_TOK, asks=[(0.58, 100)], bids=[(0.59, 100)])
        d = s.manage_exit(_position(entry=0.60), book, now=WINDOW_TS + 110,
                          fair_value=0.605)

        assert d.reason == 'model_stop'

    def test_convergence_closes_the_trade_when_the_ask_catches_fair_value(self):
        s = FairValueArb()
        book = _book(UP_TOK, asks=[(0.615, 100)], bids=[(0.60, 100)])
        d = s.manage_exit(_position(entry=0.60), book, now=WINDOW_TS + 110,
                          fair_value=0.62)

        assert d.reason == 'converged'

    def test_convergence_never_books_a_loss_as_a_success(self):
        # Same convergence, but the bid is below entry. That is a losing trade
        # and must not be logged as a mispricing successfully harvested.
        s = FairValueArb()
        book = _book(UP_TOK, asks=[(0.615, 100)], bids=[(0.585, 100)])
        d = s.manage_exit(_position(entry=0.60), book, now=WINDOW_TS + 110,
                          fair_value=0.62)

        assert d.reason != 'converged'

    def test_profit_is_taken_ahead_of_the_model_stop(self):
        # Both fire. Taking money is right.
        s = FairValueArb()
        book = _book(UP_TOK, asks=[(0.62, 100)], bids=[(0.61, 100)])
        d = s.manage_exit(_position(entry=0.60), book, now=WINDOW_TS + 110,
                          fair_value=0.60)

        assert d.reason == 'profit_target'

    def test_the_stop_is_checked_before_the_profit_target(self):
        assert fva.MAX_LOSS > fva.MIN_PROFIT   # they cannot both be true anyway
        s = FairValueArb()
        book = _book(UP_TOK, asks=[(0.60, 100)], bids=[(0.55, 100)])
        d = s.manage_exit(_position(entry=0.60), book, now=WINDOW_TS + 110)
        assert d.reason == 'price_stop'

    def test_no_bids_is_an_UNSELLABLE_hold_named_as_such(self):
        # Not a patient hold. This position cannot be closed and will resolve.
        s = FairValueArb()
        book = _book(UP_TOK, asks=[(0.60, 100)], bids=[])
        d = s.manage_exit(_position(), book, now=WINDOW_TS + 110)

        assert d.action == 'HOLD'
        assert d.reason == 'no_bid_liquidity'
        assert d.features['unsellable'] is True

    def test_a_missing_book_is_a_hold_with_a_reason(self):
        d = FairValueArb().manage_exit(_position(), None, now=WINDOW_TS + 110)
        assert d.action == 'HOLD'
        assert d.reason == 'no_orderbook'

    def test_a_position_with_no_cost_basis_is_refused_not_managed(self):
        s = FairValueArb()
        book = _book(UP_TOK, asks=[(0.60, 100)], bids=[(0.50, 100)])
        d = s.manage_exit(_position(entry=0.0, shares=0.0), book,
                          now=WINDOW_TS + 110)
        assert d.reason == 'unreadable_position'

    def test_every_exit_decision_names_a_reason(self):
        s = FairValueArb()
        books = [None,
                 _book(UP_TOK, asks=[(0.60, 100)], bids=[]),
                 _book(UP_TOK, asks=[(0.63, 100)], bids=[(0.61, 100)]),
                 _book(UP_TOK, asks=[(0.60, 100)], bids=[(0.50, 100)])]
        for book in books:
            for now in (WINDOW_TS + 110, WINDOW_TS + 280):
                d = s.manage_exit(_position(), book, now=now, fair_value=0.7)
                assert d.reason, 'a silent hold is a missing number'

    def test_exit_decisions_only_touches_this_strategys_positions(self):
        s = FairValueArb()
        books = {UP_TOK: _book(UP_TOK, asks=[(0.63, 100)], bids=[(0.61, 100)])}
        positions = [_position(), _position(strategy='PM_streak_snapper')]
        out = s.exit_decisions(positions, books, now=WINDOW_TS + 110)

        assert len(out) == 1
        assert out[0].position_id == 'pos-1'


# ============ 7. the paper adapter's taker SELL ============

class TestSimulateTakerSell:

    def _open(self, adapter, entry_levels=((0.60, 100.0),), shares=20):
        book = _book(UP_TOK, asks=entry_levels, bids=[(0.58, 100.0)])
        return adapter.simulate_taker_buy(
            strategy='PM_fair_value_arb', market_slug=SLUG, token_id=UP_TOK,
            outcome_side='Up', limit_price=0.65, shares=shares,
            window_ts=WINDOW_TS, book=book)

    def test_a_sell_walks_the_bids_and_realises_pnl(self, tmp_path):
        adapter = make_adapter(tmp_path)
        pos = self._open(adapter)
        assert pos is not None and pos.avg_price == pytest.approx(0.60)

        # 10 shares at 0.64 then 10 at 0.62 -> average 0.63.
        sell_book = _book(UP_TOK, asks=[(0.66, 50)],
                          bids=[(0.64, 10.0), (0.62, 10.0), (0.50, 100.0)])
        closed = adapter.simulate_taker_sell(pos.position_id, limit_price=0.61,
                                             book=sell_book,
                                             reason='profit_target')

        assert closed is not None
        assert closed.exit_kind == 'sell'
        assert closed.exit_price == pytest.approx(0.63)
        assert closed.pnl_usdc == pytest.approx((0.63 - 0.60) * 20)
        assert closed.resolution == 'WIN'
        assert closed.is_open is False
        assert closed.closed_early is True

    def test_the_average_sell_is_worse_than_top_of_book(self, tmp_path):
        # The reason this walks instead of taking best_bid: a 20-share exit
        # against a 10-share top level does not get the top price.
        adapter = make_adapter(tmp_path)
        pos = self._open(adapter)
        sell_book = _book(UP_TOK, bids=[(0.64, 10.0), (0.62, 50.0)])
        closed = adapter.simulate_taker_sell(pos.position_id, book=sell_book,
                                             reason='time_stop')

        assert closed.exit_price < sell_book.best_bid

    def test_a_partial_is_refused_and_the_position_stays_open(self, tmp_path):
        # The honest failure mode: a position we cannot sell is still exposed
        # and will resolve. It is NOT quietly downsized.
        adapter = make_adapter(tmp_path)
        pos = self._open(adapter)
        sell_book = _book(UP_TOK, bids=[(0.64, 5.0)])

        out = adapter.simulate_taker_sell(pos.position_id, book=sell_book,
                                          reason='price_stop')

        assert out is None
        assert pos.is_open is True
        assert pos.shares == 20
        assert adapter.decision_counts.get('NO_FILL:partial_sell_refused') == 1

    def test_a_limit_the_book_will_not_meet_is_a_named_no_fill(self, tmp_path):
        adapter = make_adapter(tmp_path)
        pos = self._open(adapter)
        sell_book = _book(UP_TOK, bids=[(0.55, 100.0)])

        assert adapter.simulate_taker_sell(pos.position_id, limit_price=0.61,
                                           book=sell_book) is None
        assert pos.is_open is True
        assert adapter.decision_counts.get('NO_FILL:bid_below_limit') == 1

    def test_an_empty_bid_side_is_distinct_from_a_limit_that_is_too_high(
            self, tmp_path):
        # Two different facts needing opposite responses (convention 20).
        adapter = make_adapter(tmp_path)
        pos = self._open(adapter)
        adapter.simulate_taker_sell(pos.position_id, book=_book(UP_TOK, bids=[]))

        assert adapter.decision_counts.get('SKIP:no_bid_liquidity') == 1
        assert 'NO_FILL:bid_below_limit' not in adapter.decision_counts

    def test_a_default_limit_of_zero_accepts_any_bid(self, tmp_path):
        adapter = make_adapter(tmp_path)
        pos = self._open(adapter)
        closed = adapter.simulate_taker_sell(
            pos.position_id, book=_book(UP_TOK, bids=[(0.02, 100.0)]),
            reason='window_close')

        assert closed is not None
        assert closed.pnl_usdc < 0
        assert closed.resolution == 'LOSS'

    def test_a_scratch_is_not_a_win(self, tmp_path):
        adapter = make_adapter(tmp_path)
        pos = self._open(adapter)
        closed = adapter.simulate_taker_sell(
            pos.position_id, book=_book(UP_TOK, bids=[(0.60, 100.0)]),
            reason='converged')

        assert closed.pnl_usdc == pytest.approx(0.0)
        assert closed.resolution == 'LOSS'

    def test_selling_a_closed_position_twice_is_refused(self, tmp_path):
        adapter = make_adapter(tmp_path)
        pos = self._open(adapter)
        book = _book(UP_TOK, bids=[(0.64, 100.0)])
        first = adapter.simulate_taker_sell(pos.position_id, book=book)
        second = adapter.simulate_taker_sell(pos.position_id, book=book)

        assert first is not None
        assert second is None
        assert adapter.decision_counts.get('SKIP:position_not_open') == 1

    def test_an_unknown_position_is_logged_not_silently_dropped(self, tmp_path):
        adapter = make_adapter(tmp_path)
        assert adapter.simulate_taker_sell('nope') is None
        assert adapter.decision_counts.get('SKIP:unknown_position') == 1

    @pytest.mark.parametrize('shares', [0, -5, 999])
    def test_a_degenerate_sell_size_is_refused(self, tmp_path, shares):
        adapter = make_adapter(tmp_path)
        pos = self._open(adapter)
        out = adapter.simulate_taker_sell(
            pos.position_id, shares=shares,
            book=_book(UP_TOK, bids=[(0.64, 1000.0)]))

        assert out is None
        assert pos.is_open is True
        assert adapter.decision_counts.get('SKIP:invalid_sell_size') == 1

    def test_a_halt_does_not_block_an_exit(self, tmp_path, monkeypatch):
        # A halt says "stop taking risk". Closing a position reduces risk, and
        # a stop that stops working when the switch is pulled is not a stop.
        adapter = make_adapter(tmp_path)
        pos = self._open(adapter)

        halt_file = tmp_path / 'HALT'
        halt_file.write_text('{"reason": "drill"}')
        monkeypatch.setattr(halt_mod, 'HALT_FILE', str(halt_file))
        assert halt_mod.is_halted() is True

        closed = adapter.simulate_taker_sell(
            pos.position_id, book=_book(UP_TOK, bids=[(0.64, 100.0)]),
            reason='price_stop')
        assert closed is not None

        # ...but entries stay blocked, which is the documented contract.
        blocked = adapter.simulate_taker_buy(
            strategy='PM_fair_value_arb', market_slug=SLUG, token_id=UP_TOK,
            outcome_side='Up', limit_price=0.65, shares=20,
            book=_book(UP_TOK, asks=[(0.60, 100)]))
        assert blocked is None
        assert adapter.decision_counts.get('SKIP:halted') == 1

    def test_the_exit_fee_is_charged_on_the_proceeds(self, tmp_path):
        adapter = make_adapter(tmp_path, taker_fee_rate=0.02)
        pos = self._open(adapter)
        closed = adapter.simulate_taker_sell(
            pos.position_id, book=_book(UP_TOK, bids=[(0.64, 100.0)]))

        proceeds = 0.64 * 20
        assert closed.exit_fee_usdc == pytest.approx(proceeds * 0.02)
        assert closed.total_fee_usdc == pytest.approx(
            closed.fee_usdc + closed.exit_fee_usdc)
        assert closed.pnl_usdc == pytest.approx(
            proceeds - closed.exit_fee_usdc - closed.cost_usdc - closed.fee_usdc)

    def test_equity_reflects_a_closed_trade(self, tmp_path):
        adapter = make_adapter(tmp_path)
        start = adapter.get_equity()
        pos = self._open(adapter)
        assert adapter.get_equity() < start          # premium at risk

        adapter.simulate_taker_sell(pos.position_id,
                                    book=_book(UP_TOK, bids=[(0.64, 100.0)]))
        assert adapter.capital_at_risk() == pytest.approx(0.0)
        assert adapter.get_equity() == pytest.approx(start + (0.64 - 0.60) * 20)

    def test_a_close_writes_a_CLOSE_row_to_the_csv(self, tmp_path):
        adapter = make_adapter(tmp_path)
        pos = self._open(adapter)
        adapter.simulate_taker_sell(pos.position_id,
                                    book=_book(UP_TOK, bids=[(0.64, 100.0)]),
                                    reason='profit_target')

        import csv
        with open(adapter.log_path) as f:
            rows = list(csv.DictReader(f))
        closes = [r for r in rows if r['action'] == 'CLOSE']

        assert len(closes) == 1
        assert closes[0]['reason'] == 'profit_target'
        assert float(closes[0]['avg_price']) == pytest.approx(0.64)
        assert 'exit_kind=sell' in closes[0]['features']

    def test_the_csv_schema_is_unchanged(self, tmp_path):
        # A running shadow loop holds this module's OLD code in memory
        # (convention 13) and appends rows shaped by the old column list. Adding
        # a column here would misalign every row it writes after this change.
        from engine.polymarket.paper_adapter import LOG_COLUMNS
        assert LOG_COLUMNS[0] == 'ts'
        assert LOG_COLUMNS[-1] == 'features'
        assert 'exit_kind' not in LOG_COLUMNS


class TestSummaryKeepsExitKindsApart:

    def _closed_and_resolved(self, tmp_path, monkeypatch):
        adapter = make_adapter(tmp_path)
        sold = adapter.simulate_taker_buy(
            strategy='PM_fair_value_arb', market_slug=SLUG, token_id=UP_TOK,
            outcome_side='Up', limit_price=0.65, shares=20, window_ts=WINDOW_TS,
            book=_book(UP_TOK, asks=[(0.60, 100)]))
        adapter.simulate_taker_sell(sold.position_id,
                                    book=_book(UP_TOK, bids=[(0.62, 100.0)]),
                                    reason='profit_target')

        held = adapter.simulate_taker_buy(
            strategy='PM_streak_snapper', market_slug='other-slug',
            token_id=DOWN_TOK, outcome_side='Down', limit_price=0.55, shares=20,
            window_ts=WINDOW_TS, book=_book(DOWN_TOK, asks=[(0.50, 100)]))
        monkeypatch.setattr(
            'engine.polymarket.paper_adapter.resolution_price',
            lambda client, slug, side: 1.0)
        adapter.resolve_positions()
        return adapter, sold, held

    def test_the_two_populations_are_reported_separately(self, tmp_path,
                                                         monkeypatch):
        adapter, _sold, _held = self._closed_and_resolved(tmp_path, monkeypatch)
        summary = adapter.summary()

        assert summary['by_exit_kind']['sell']['closed'] == 1
        assert summary['by_exit_kind']['resolution']['closed'] == 1
        assert summary['closed_early'] == 1

    def test_breakeven_covers_redeemed_positions_only(self, tmp_path,
                                                      monkeypatch):
        # A trade sold at 0.62 never had a 1.00-or-0.00 payoff, so folding it
        # into breakeven_win_rate would move the one number whose whole job is
        # to be compared against a resolution win rate.
        adapter, _sold, held = self._closed_and_resolved(tmp_path, monkeypatch)
        summary = adapter.summary()

        assert summary['share_weighted_entry_price'] == pytest.approx(0.50)
        assert summary['breakeven_win_rate'] == pytest.approx(0.50)

    def test_the_note_warns_that_the_pooled_win_rate_pools(self, tmp_path,
                                                          monkeypatch):
        adapter, _s, _h = self._closed_and_resolved(tmp_path, monkeypatch)
        assert 'by_exit_kind' in adapter.summary()['note']

    def test_a_resolution_exit_is_tagged_as_one(self, tmp_path, monkeypatch):
        _adapter, _sold, held = self._closed_and_resolved(tmp_path, monkeypatch)
        assert held.exit_kind == 'resolution'
        assert held.closed_early is False

    def test_a_run_with_no_early_exits_reports_exactly_as_before(self, tmp_path,
                                                                 monkeypatch):
        # Legacy behaviour is preserved: before simulate_taker_sell existed
        # every resolved position was a resolution exit.
        adapter = make_adapter(tmp_path)
        adapter.simulate_taker_buy(
            strategy='PM_streak_snapper', market_slug=SLUG, token_id=UP_TOK,
            outcome_side='Up', limit_price=0.55, shares=20, window_ts=WINDOW_TS,
            book=_book(UP_TOK, asks=[(0.50, 100)]))
        monkeypatch.setattr(
            'engine.polymarket.paper_adapter.resolution_price',
            lambda client, slug, side: 1.0)
        adapter.resolve_positions()
        summary = adapter.summary()

        assert summary['win_rate'] == 1.0
        assert summary['breakeven_win_rate'] == pytest.approx(0.50)
        assert list(summary['by_exit_kind']) == ['resolution']
        assert summary['closed_early'] == 0


# ============ 8. wiring ============

class TestWiring:

    def test_the_strategy_is_in_the_shadow_loops_list(self):
        names = [s.strategy_name for s in build_strategies()]
        assert 'PM_fair_value_arb' in names
        assert len(names) == len(set(names))

    def test_new_strategies_are_appended_never_inserted(self):
        # Every historical log line is keyed by position in somebody's head.
        names = [s.strategy_name for s in build_strategies()]
        assert names[:7] == [
            'PM_streak_snapper', 'PM_mid_price_continuation', 'PM_box_builder',
            'PM_corridor_collector', 'PM_temporal_arbitrage',
            'PM_corridor_pair', 'PM_spread_harvest_taker']
        assert names[7] == 'PM_fair_value_arb'

    def test_build_strategies_hands_out_fresh_tapes(self):
        # Two loops feeding one tape would interleave observations into a
        # series neither of them saw.
        a = [s for s in build_strategies() if s.strategy_name == 'PM_fair_value_arb'][0]
        b = [s for s in build_strategies() if s.strategy_name == 'PM_fair_value_arb'][0]
        a.tape.observe(1.0, 100.0)
        assert len(b.tape.samples) == 0

    def test_only_exit_managers_advertise_that_they_manage_exits(self):
        # The three parameter VARIANTS inherit `manages_exits = True`, which is
        # the point of them - same exit machinery, different constants.
        #
        # `PM_dip_arb` is the one member from OUTSIDE this family, added when it
        # was registered in `build_strategies()`. It is legitimate: it is the
        # only other strategy in the package that sells before resolution, and
        # it ships its own `manage_exit` AND `exit_decisions`. Everything else
        # here holds to resolution, and the shadow loop would start polling
        # `manage_exit` on a strategy that does not have one.
        #
        # So the list is no longer a prefix test. What it still guards is the
        # thing that actually breaks: a strategy claiming the flag WITHOUT the
        # methods behind it. That is asserted directly below rather than
        # inferred from the name.
        managers = [s for s in build_strategies()
                    if getattr(s, 'manages_exits', False)]
        names = [s.strategy_name for s in managers]
        assert names == ['PM_fair_value_arb', 'PM_fair_value_arb_wide',
                         'PM_fair_value_arb_patient',
                         'PM_fair_value_arb_hft',
                         'PM_fair_value_arb_inverse',
                         'PM_dip_arb']
        # The flag is a promise about the interface. Check the interface.
        for s in managers:
            assert callable(getattr(s, 'manage_exit', None)), s.strategy_name

    def test_no_unregistered_strategy_claims_it_manages_exits(self):
        # The inverse guard: a strategy that sells early but forgot the flag
        # would never be polled, and its positions would ride to resolution
        # silently. Anything shipping `manage_exit` must also declare the flag.
        for s in build_strategies():
            if callable(getattr(s, 'manage_exit', None)):
                assert getattr(s, 'manages_exits', False) is True, (
                    '%s ships manage_exit but does not declare manages_exits'
                    % s.strategy_name)

    def test_it_is_paper_mode_and_says_so(self):
        s = FairValueArb()
        assert fva.PAPER_MODE is True
        assert s.paper_mode is True
        assert s.evaluate(_ctx()).features['paper_mode'] is True

    def test_a_signal_maps_onto_the_binary_payoff(self):
        # entry = premium, stop = 0.00 (a losing share IS worth zero, which
        # satisfies convention 8), target = 1.00.
        s = FairValueArb()
        signal = s.decision_to_signal(s.evaluate(_ctx()))

        assert signal is not None
        assert signal.stop == 0.0
        assert signal.target == 1.0
        assert 0.0 < signal.entry <= 1.0
        assert signal.features['payoff'] == 'binary_resolution'


# ============ 9. it must never raise ============

class TestNeverRaises:

    GARBAGE = [
        MarketContext(window_ts=0),
        MarketContext(window_ts=WINDOW_TS, windows=[], market=_market(),
                      spot=float('nan'), seconds_into_window=10.0),
        MarketContext(window_ts=WINDOW_TS, windows=_windows(), market=_market(),
                      spot=0.0, seconds_into_window=10.0),
        MarketContext(window_ts=WINDOW_TS, windows=_windows(), market=_market(),
                      books={UP_TOK: _book(UP_TOK)}, spot=100_060.0,
                      seconds_into_window=-50.0),
        MarketContext(window_ts=WINDOW_TS,
                      windows=[Window(ts=WINDOW_TS, open=0.0, close=0.0,
                                      direction='UP')],
                      market=_market(), spot=100_000.0,
                      seconds_into_window=10.0),
    ]

    @pytest.mark.parametrize('ctx', GARBAGE)
    def test_evaluate_always_returns_a_decision(self, ctx):
        d = FairValueArb().evaluate(ctx)
        assert d.action in ('ENTER', 'SKIP', 'QUOTE')
        assert d.action != 'ENTER' or d.legs

    @pytest.mark.parametrize('ctx', GARBAGE)
    def test_scan_never_raises(self, ctx):
        # The standard scanner contract. A book-less context falls through to
        # the book gate and returns None, which is correct.
        FairValueArb().scan({'closes': [1.0] * 20, 'opens': [1.0] * 20,
                             'timestamps': list(range(20))})

    def test_manage_exit_survives_a_junk_position(self):
        class Junk:
            position_id = 'x'
            avg_price = float('nan')
            shares = float('nan')
            opened_ts = None
            window_ts = None

        d = FairValueArb().manage_exit(
            Junk(), _book(UP_TOK, bids=[(0.5, 10)]), now=WINDOW_TS)
        assert d.action == 'HOLD'
        assert d.reason
