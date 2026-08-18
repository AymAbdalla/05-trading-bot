"""Tests for the three PM_fair_value_arb parameter variants.

No network. Every orderbook, window, position and spot here is a synthetic
fixture, so a result is a statement about the code and not about whatever
Polymarket happened to be quoting.

Four jobs, in descending order of how much they matter:

  1. **The min-hold must defer ONLY the discretionary exits.** That is the sole
     piece of new logic in the whole change set, and it is the risk-bearing
     one: deferring `price_stop` by 15 seconds means that for those 15 seconds
     the position's only stop is the structural 0.00 floor, so its worst case is
     the FULL PREMIUM and not `max_loss`. `TestMinHold` pins down both halves -
     suppression below 15s, release at 15s - and `TestMinHoldNeverSuppressesA
     SafetyExit` pins down that `window_close` gets through regardless, because
     suppressing that one could strand a position past its market close where a
     paper binary has no sell path at all.

  2. **The constants must be what was specified, asserted on the INSTANCE.**
     Convention 22: a claim in a docstring is not a wiring test, and a module
     constant is not proof that the constant reached the object. Every
     assertion here reads an attribute off a constructed strategy.

  3. **`min_hold_not_met` must never be pooled with a market condition.**
     Conventions 11 and 20: `waiting_for_convergence` means "the rules ran and
     none fired" and `min_hold_not_met` means "a rule fired and we refused it".
     Two causes, two numbers, and the refused rule is named on the row.

  4. **The registry must be eleven, unique, and appended.** Historical log
     lines are keyed by position, and the shadow loop's accounting identity is
     `evaluations == cycles * len(strategies)`.

There is deliberately NO harness sweep here. Per D-268 these are NOT_TESTED
until a resolution-PnL harness exists; running the price-path harness on them
would fabricate numbers. Nothing in this file measures edge, and nothing in it
should ever be cited as evidence that a variant works.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.halt as halt_mod  # noqa: E402
from engine.polymarket.paper_adapter import PaperPosition  # noqa: E402
from engine.polymarket.types import (Market, Orderbook,  # noqa: E402
                                     Outcome, PriceLevel)
from strategies.polymarket import build_strategies  # noqa: E402
from strategies.polymarket.base import MarketContext, Window  # noqa: E402
from strategies.polymarket.fair_value_arb import FairValueArb  # noqa: E402
import strategies.polymarket.fair_value_arb as fva  # noqa: E402
import strategies.polymarket.fair_value_arb_hft as hft_mod  # noqa: E402
import strategies.polymarket.fair_value_arb_patient as pat_mod  # noqa: E402
import strategies.polymarket.fair_value_arb_wide as wide_mod  # noqa: E402
from strategies.polymarket.fair_value_arb_hft import \
    FairValueArbHFT  # noqa: E402
from strategies.polymarket.fair_value_arb_patient import \
    FairValueArbPatient  # noqa: E402
from strategies.polymarket.fair_value_arb_wide import \
    FairValueArbWide  # noqa: E402

UP_TOK = 'tok-up'
DOWN_TOK = 'tok-down'
WINDOW_TS = 1_700_000_000
SLUG = 'btc-updown-5m-1700000000'

VARIANT_MODULES = (wide_mod, pat_mod, hft_mod)
VARIANT_CLASSES = (FairValueArbWide, FairValueArbPatient, FairValueArbHFT)
VARIANT_NAMES = ('PM_fair_value_arb_wide', 'PM_fair_value_arb_patient',
                 'PM_fair_value_arb_hft')


# ============ fixtures (mirroring tests/test_fair_value_arb.py) ============

@pytest.fixture(autouse=True)
def never_halted(tmp_path, monkeypatch):
    """Point the kill switch at a path that does not exist.

    Without this, a real HALT file left in the repo root by a drill turns every
    entry test into a `halted` skip - which would be the switch WORKING, and
    indistinguishable in the output from a variant being broken.
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
    """`n` 5m bars, the last of which is THIS window (ts == WINDOW_TS)."""
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

    Same fixture shape as `tests/test_fair_value_arb.py` so the parent and the
    variants are exercised on IDENTICAL inputs. P(Up) lands near 0.71 against a
    0.60 Up ask - an 11c gap, which clears every one of the four edge
    thresholds (4c, 8c, 6c, 2c) on 200 shares of depth, which clears both depth
    gates (50 and 100).
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
              strategy='PM_fair_value_arb_patient'):
    return PaperPosition(
        position_id='pos-1', strategy=strategy, market_slug=SLUG,
        token_id=token_id, outcome_side=side, shares=shares, avg_price=entry,
        cost_usdc=entry * shares, fee_usdc=0.0, opened_ts=opened_ts,
        window_ts=window_ts)


# ============ 1. the constants actually reached the objects ============

class TestWideConstants:
    """Convention 22: assert on the INSTANCE, never on the module constant."""

    def test_the_four_specified_constants(self):
        s = FairValueArbWide()
        assert s.strategy_name == 'PM_fair_value_arb_wide'
        assert s.edge_threshold == pytest.approx(0.08)
        assert s.min_profit == pytest.approx(0.03)
        assert s.max_loss == pytest.approx(0.05)
        assert s.max_trades_per_window == 2

    def test_everything_else_is_the_parents_number(self):
        # "Everything else inherited unchanged" is a claim about behaviour, and
        # a variant that quietly moved a fifth constant would not be comparable
        # to the parent - which is the only reason it exists.
        s, parent = FairValueArbWide(), FairValueArb()
        for attr in ('model_stop_margin', 'convergence_eps', 'time_stop_sec',
                     'window_close_exit_sec', 'min_entry_seconds_remaining',
                     'target_shares', 'min_book_depth_shares', 'depth_band',
                     'max_notional_usdc', 'min_shares', 'min_fair_value',
                     'max_fair_value', 'model_uncertainty', 'atr_windows'):
            assert getattr(s, attr) == getattr(parent, attr), attr

    def test_it_moved_the_parents_numbers_and_says_what_they_were(self):
        assert wide_mod.PARENT_MIN_PROFIT == pytest.approx(fva.MIN_PROFIT)
        assert wide_mod.PARENT_MAX_LOSS == pytest.approx(fva.MAX_LOSS)
        assert FairValueArbWide().min_profit != FairValueArb().min_profit


class TestPatientConstants:

    def test_the_specified_constants(self):
        s = FairValueArbPatient()
        assert s.strategy_name == 'PM_fair_value_arb_patient'
        assert s.edge_threshold == pytest.approx(0.06)
        assert s.min_profit == pytest.approx(0.02)
        assert s.max_loss == pytest.approx(0.03)
        assert s.max_trades_per_window == 2
        assert s.time_stop_sec == pytest.approx(120.0)
        assert s.min_hold_sec == pytest.approx(15.0)

    def test_the_stop_is_the_parents_and_that_is_the_point(self):
        # The spec says max_loss is UNCHANGED. If this ever drifts, the
        # min-hold's risk statement stops describing the code.
        assert FairValueArbPatient().max_loss == FairValueArb().max_loss

    def test_everything_else_is_the_parents_number(self):
        s, parent = FairValueArbPatient(), FairValueArb()
        for attr in ('model_stop_margin', 'convergence_eps',
                     'window_close_exit_sec', 'min_entry_seconds_remaining',
                     'target_shares', 'min_book_depth_shares', 'depth_band',
                     'max_notional_usdc', 'min_shares', 'min_fair_value',
                     'max_fair_value', 'model_uncertainty', 'atr_windows'):
            assert getattr(s, attr) == getattr(parent, attr), attr

    def test_min_hold_sec_is_not_a_parent_concept(self):
        # If the parent ever grows one, this variant's override is redundant
        # and the two would silently both apply.
        assert not hasattr(FairValueArb(), 'min_hold_sec')


class TestHftConstants:

    def test_the_specified_constants(self):
        s = FairValueArbHFT()
        assert s.strategy_name == 'PM_fair_value_arb_hft'
        assert s.edge_threshold == pytest.approx(0.02)
        assert s.max_loss == pytest.approx(0.02)
        assert s.min_profit == pytest.approx(0.01)
        assert s.max_trades_per_window == 5
        assert s.min_book_depth_shares == 100
        assert s.time_stop_sec == pytest.approx(30.0)

    def test_the_profit_target_is_the_parents_and_that_is_the_point(self):
        # Its whole break-even improvement comes from the stop, not the target.
        assert FairValueArbHFT().min_profit == FairValueArb().min_profit

    def test_the_depth_gate_went_UP_not_down(self):
        # The one TIGHTENING in this variant. A reader skimming a diff full of
        # loosened thresholds must not assume this one moved the same way.
        assert (FairValueArbHFT().min_book_depth_shares
                > FairValueArb().min_book_depth_shares)

    def test_everything_else_is_the_parents_number(self):
        s, parent = FairValueArbHFT(), FairValueArb()
        for attr in ('model_stop_margin', 'convergence_eps',
                     'window_close_exit_sec', 'min_entry_seconds_remaining',
                     'target_shares', 'depth_band', 'max_notional_usdc',
                     'min_shares', 'min_fair_value', 'max_fair_value',
                     'model_uncertainty', 'atr_windows'):
            assert getattr(s, attr) == getattr(parent, attr), attr


class TestKwargsStillOverride:
    """A variant is a set of DEFAULTS, not a hardcoded configuration."""

    @pytest.mark.parametrize('cls', VARIANT_CLASSES)
    def test_a_caller_can_still_override_any_of_them(self, cls):
        s = cls(edge_threshold=0.11, max_loss=0.07, target_shares=3)
        assert s.edge_threshold == pytest.approx(0.11)
        assert s.max_loss == pytest.approx(0.07)
        assert s.target_shares == 3


# ============ 2. break-even arithmetic, computed not written down ============

class TestBreakevenWinRate:
    """`max_loss / (min_profit + max_loss)`. One line, and the most useful
    number any of these variants carries."""

    EXPECTED = {
        'PM_fair_value_arb_wide': 0.625,
        'PM_fair_value_arb_patient': 0.60,
        'PM_fair_value_arb_hft': 2.0 / 3.0,
    }

    @pytest.mark.parametrize('cls', VARIANT_CLASSES)
    def test_it_matches_the_specified_number(self, cls):
        s = cls()
        assert s.breakeven_win_rate == pytest.approx(
            self.EXPECTED[s.strategy_name])

    @pytest.mark.parametrize('cls', VARIANT_CLASSES)
    def test_it_is_computed_from_the_instance_not_hardcoded(self, cls):
        # A constant restating 0.625 would go stale the moment somebody
        # constructed the class differently, and would then be quoted as if it
        # had been measured (convention 22).
        s = cls(min_profit=0.01, max_loss=0.01)
        assert s.breakeven_win_rate == pytest.approx(0.5)

    def test_every_variants_breakeven_is_below_the_parents_and_that_is_not_good_news(self):
        # The parent needs 75%. All three variants need less. That is a
        # different PAYOFF SHAPE, not a better strategy, and every one of these
        # shapes was reached by moving a threshold rather than by measuring
        # anything - convention 17's exact warning. This test exists so the
        # comparison is on the record, not so it can be quoted as a result.
        parent_be = fva.MAX_LOSS / (fva.MIN_PROFIT + fva.MAX_LOSS)
        assert parent_be == pytest.approx(0.75)
        for cls in VARIANT_CLASSES:
            assert cls().breakeven_win_rate < parent_be


# ============ 3. the min-hold: the only real logic in the change set ============

class TestMinHold:
    """Suppression below 15s, release at 15s, for BOTH discretionary exits."""

    OPENED = WINDOW_TS + 100

    def _stop_book(self):
        # bid 0.49 is below the TIERED stop for a 0.60 entry, which is 0.50
        # (`base.tiered_stop_price`, the >= 0.50 tier at 0.10 away). It was
        # 0.56 against `entry - max_loss = 0.57` while `max_loss` was the stop.
        return _book(UP_TOK, asks=[(0.60, 100)], bids=[(0.49, 100)])

    def _target_book(self):
        # bid 0.62 is at or above entry 0.60 + min_profit 0.02.
        return _book(UP_TOK, asks=[(0.64, 100)], bids=[(0.62, 100)])

    def test_the_price_stop_is_suppressed_below_fifteen_seconds(self):
        s = FairValueArbPatient()
        d = s.manage_exit(_position(opened_ts=self.OPENED), self._stop_book(),
                          now=self.OPENED + 10.0)

        assert d.action == 'HOLD'
        assert d.reason == 'min_hold_not_met'
        # The refused rule is NAMED. Two causes never share one number.
        assert d.features['min_hold_suppressed_reason'] == 'price_stop'
        assert d.features['min_hold_seconds_to_go'] == pytest.approx(5.0)
        # And the widened risk is stated on the ROW, not only in a docstring.
        assert d.features['stop_deferred_worst_case_is_full_premium'] is True

    def test_the_price_stop_is_released_at_fifteen_seconds(self):
        s = FairValueArbPatient()
        book = self._stop_book()
        pos = _position(opened_ts=self.OPENED)

        at = s.manage_exit(pos, book, now=self.OPENED + 15.0)
        after = s.manage_exit(pos, book, now=self.OPENED + 20.0)

        assert at.action == 'EXIT' and at.reason == 'price_stop'
        assert after.action == 'EXIT' and after.reason == 'price_stop'
        # A stop that refuses a bad price is not a stop.
        assert at.limit_price == fva.URGENT_SELL_LIMIT

    def test_the_profit_target_is_suppressed_below_fifteen_seconds(self):
        s = FairValueArbPatient()
        d = s.manage_exit(_position(opened_ts=self.OPENED), self._target_book(),
                          now=self.OPENED + 1.0)

        assert d.action == 'HOLD'
        assert d.reason == 'min_hold_not_met'
        assert d.features['min_hold_suppressed_reason'] == 'profit_target'
        # Not a stop deferral, so this flag must be False rather than absent -
        # an absent flag cannot be counted (convention 20).
        assert d.features['stop_deferred_worst_case_is_full_premium'] is False

    def test_the_profit_target_is_released_after_fifteen_seconds(self):
        s = FairValueArbPatient()
        d = s.manage_exit(_position(opened_ts=self.OPENED), self._target_book(),
                          now=self.OPENED + 16.0)
        assert d.action == 'EXIT'
        assert d.reason == 'profit_target'

    def test_the_suppressed_hold_keeps_the_position_id(self):
        s = FairValueArbPatient()
        d = s.manage_exit(_position(opened_ts=self.OPENED), self._stop_book(),
                          now=self.OPENED + 5.0)
        assert d.position_id == 'pos-1'
        assert d.limit_price is None and d.shares is None

    def test_min_hold_is_stamped_on_EVERY_row_not_only_when_it_bites(self):
        # A counter that only appears when it fires cannot be used to work out
        # how often it did not (convention 20).
        s = FairValueArbPatient()
        quiet = _book(UP_TOK, asks=[(0.61, 100)], bids=[(0.59, 100)])
        rows = [
            s.manage_exit(_position(opened_ts=self.OPENED), quiet,
                          now=self.OPENED + 5.0),
            s.manage_exit(_position(opened_ts=self.OPENED), quiet,
                          now=self.OPENED + 40.0),
            s.manage_exit(_position(opened_ts=self.OPENED), self._stop_book(),
                          now=self.OPENED + 5.0),
            s.manage_exit(_position(opened_ts=self.OPENED), self._stop_book(),
                          now=self.OPENED + 40.0),
        ]
        for d in rows:
            assert d.features['min_hold_sec'] == pytest.approx(15.0)
            assert 'min_hold_met' in d.features
        assert [d.features['min_hold_met'] for d in rows] == [False, True,
                                                              False, True]

    def test_a_quiet_book_inside_the_min_hold_is_NOT_min_hold_not_met(self):
        # The load-bearing distinction. `waiting_for_convergence` means the
        # rules ran and none fired; `min_hold_not_met` means one fired and we
        # refused it. Pooling them would hide how often the min-hold bit.
        s = FairValueArbPatient()
        quiet = _book(UP_TOK, asks=[(0.61, 100)], bids=[(0.59, 100)])
        d = s.manage_exit(_position(opened_ts=self.OPENED), quiet,
                          now=self.OPENED + 5.0)

        assert d.action == 'HOLD'
        assert d.reason == 'waiting_for_convergence'
        assert 'min_hold_suppressed_reason' not in d.features

    def test_an_unknown_age_is_not_treated_as_young(self):
        # A position that cannot prove it is inside its min-hold gets its stop.
        # The safe direction to fail on a stop is toward taking it.
        s = FairValueArbPatient()
        d = s.manage_exit(_position(opened_ts=None), self._stop_book(),
                          now=self.OPENED + 1.0)

        assert d.action == 'EXIT'
        assert d.reason == 'price_stop'
        assert d.features['min_hold_age_unknown'] is True
        assert d.features['min_hold_met'] is None

    def test_a_zero_min_hold_reproduces_the_parents_behaviour(self):
        # The control. If this ever diverges, the override is doing something
        # other than deferring.
        s = FairValueArbPatient(min_hold_sec=0.0)
        d = s.manage_exit(_position(opened_ts=self.OPENED), self._stop_book(),
                          now=self.OPENED + 0.5)
        assert d.action == 'EXIT' and d.reason == 'price_stop'


class TestMinHoldNeverSuppressesASafetyExit:
    """The guarantee that keeps the deferral from becoming unbounded."""

    def test_window_close_fires_inside_the_min_hold(self):
        # 20s of window left, position 5s old, AND the bid is through the stop.
        # window_close must win: past here the position is a directional bet on
        # a resolution it has no sell path out of.
        s = FairValueArbPatient()
        book = _book(UP_TOK, asks=[(0.60, 100)], bids=[(0.50, 100)])
        d = s.manage_exit(_position(opened_ts=WINDOW_TS + 275), book,
                          now=WINDOW_TS + 280)

        assert d.action == 'EXIT'
        assert d.reason == 'window_close'
        assert d.features['seconds_remaining'] == pytest.approx(20.0)
        assert d.features['min_hold_met'] is False
        assert d.features['min_hold_did_not_suppress'] == 'window_close'

    def test_the_model_stop_is_not_suppressed(self):
        # The spec names the profit target and the price stop. This is neither,
        # and it is the exit that distinguishes a model from a trailing stop.
        s = FairValueArbPatient()
        book = _book(UP_TOK, asks=[(0.58, 100)], bids=[(0.59, 100)])
        d = s.manage_exit(_position(opened_ts=WINDOW_TS + 100), book,
                          now=WINDOW_TS + 105, fair_value=0.605)

        assert d.reason == 'model_stop'
        assert d.features['min_hold_did_not_suppress'] == 'model_stop'

    def test_convergence_is_not_suppressed(self):
        s = FairValueArbPatient()
        book = _book(UP_TOK, asks=[(0.615, 100)], bids=[(0.60, 100)])
        d = s.manage_exit(_position(opened_ts=WINDOW_TS + 100), book,
                          now=WINDOW_TS + 105, fair_value=0.62)

        assert d.reason == 'converged'

    def test_an_unsellable_book_stays_an_unsellable_hold(self):
        # Not a min-hold. This position cannot be closed and will resolve, and
        # a run full of these must not read as a run of patient holds.
        s = FairValueArbPatient()
        book = _book(UP_TOK, asks=[(0.60, 100)], bids=[])
        d = s.manage_exit(_position(opened_ts=WINDOW_TS + 100), book,
                          now=WINDOW_TS + 105)

        assert d.action == 'HOLD'
        assert d.reason == 'no_bid_liquidity'
        assert d.features['unsellable'] is True

    def test_a_missing_book_and_a_junk_position_keep_their_own_reasons(self):
        s = FairValueArbPatient()
        assert s.manage_exit(_position(), None,
                             now=WINDOW_TS + 105).reason == 'no_orderbook'
        book = _book(UP_TOK, asks=[(0.60, 100)], bids=[(0.50, 100)])
        assert s.manage_exit(_position(entry=0.0, shares=0.0), book,
                             now=WINDOW_TS + 105).reason == 'unreadable_position'

    def test_the_suppressed_set_is_exactly_two_and_matched_exactly(self):
        # `price_stop` and `model_stop` both end in `_stop`. A substring match
        # would silently defer the model stop too.
        assert set(pat_mod.SUPPRESSED_DURING_MIN_HOLD) == {'price_stop',
                                                           'profit_target'}


class TestOnlyPatientHasAMinHold:
    """The other two variants must behave exactly like the parent on exits."""

    @pytest.mark.parametrize('cls', [FairValueArbWide, FairValueArbHFT,
                                     FairValueArb])
    def test_a_five_second_old_position_still_gets_its_stop(self, cls):
        s = cls()
        # 0.50 is through every one of the three stops (3c, 5c, 2c) at a 0.60
        # entry, so one book proves it for all three classes.
        book = _book(UP_TOK, asks=[(0.60, 100)], bids=[(0.50, 100)])
        d = s.manage_exit(_position(opened_ts=WINDOW_TS + 100,
                                    strategy=s.strategy_name), book,
                          now=WINDOW_TS + 105)

        assert d.action == 'EXIT'
        assert d.reason == 'price_stop'
        assert 'min_hold_sec' not in d.features


# ============ 4. every variant still evaluates, and still fires ============

class TestVariantsStillEvaluate:

    @pytest.mark.parametrize('cls', VARIANT_CLASSES)
    def test_it_returns_a_decision_with_a_reason_on_a_fixture_context(self, cls):
        d = cls().evaluate(_ctx())
        assert d.action in ('ENTER', 'QUOTE', 'SKIP')
        assert d.action == 'ENTER' or d.reason
        assert d.strategy == cls().strategy_name

    @pytest.mark.parametrize('cls', VARIANT_CLASSES)
    def test_it_is_provably_ALIVE_on_the_parents_own_entry_fixture(self, cls):
        # An 11c gap clears 8c, 6c and 2c alike. A variant that silently never
        # fires looks identical in a graveyard to one honestly measured and
        # found to have no edge (convention 3).
        d = cls().evaluate(_ctx())
        assert d.action == 'ENTER', (cls.__name__, d.reason, d.features)
        assert d.legs and d.legs[0].outcome_side == 'Up'

    @pytest.mark.parametrize('cls', VARIANT_CLASSES)
    def test_it_is_provably_PICKY_with_a_one_condition_off_context(self, cls):
        # Ask sitting at fair value: no gap for anybody.
        d = cls().evaluate(_ctx(up_asks=((0.71, 200.0),),
                                down_asks=((0.71, 200.0),)))
        assert d.action == 'SKIP'
        assert d.reason == 'edge_below_threshold', d.reason

    def test_the_wide_threshold_actually_binds(self):
        # A 5c gap: over the parent's 4c and the patient's 6c is borderline, so
        # this is pinned on the two that bracket it. Fair value is ~0.71.
        ctx_5c = _ctx(up_asks=((0.66, 200.0),), down_asks=((0.90, 200.0),))
        assert FairValueArb().evaluate(ctx_5c).action == 'ENTER'
        wide = FairValueArbWide().evaluate(ctx_5c)
        assert wide.action == 'SKIP'
        assert wide.reason == 'edge_below_threshold'
        assert wide.features['edge_threshold'] == pytest.approx(0.08)

    def test_the_hft_depth_gate_actually_binds(self):
        # 60 shares clears the parent's 50 and fails the hft's 100. Same book,
        # two different answers, and each names its own gate.
        ctx_thin = _ctx(up_asks=((0.60, 60.0),))
        assert FairValueArb().evaluate(ctx_thin).action == 'ENTER'
        d = FairValueArbHFT().evaluate(ctx_thin)
        assert d.action == 'SKIP'
        assert d.reason == 'insufficient_book_depth'
        assert d.features['min_book_depth_shares'] == 100

    @pytest.mark.parametrize('cls', VARIANT_CLASSES)
    def test_the_attempt_cap_is_the_variants_own(self, cls):
        s = cls()
        for _ in range(s.max_trades_per_window):
            assert s.evaluate(_ctx()).action == 'ENTER'
        d = s.evaluate(_ctx())
        assert d.action == 'SKIP'
        assert d.reason == 'max_trades_this_window'
        assert d.features['max_trades_per_window'] == s.max_trades_per_window

    @pytest.mark.parametrize('cls', VARIANT_CLASSES)
    def test_it_never_raises_on_garbage(self, cls):
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
        s = cls()
        for ctx in garbage:
            d = s.evaluate(ctx)
            assert d.action in ('ENTER', 'QUOTE', 'SKIP')
            assert d.reason or d.action == 'ENTER'

    def test_manage_exit_survives_a_junk_position(self):
        class Junk:
            pass
        for cls in VARIANT_CLASSES:
            d = cls().manage_exit(Junk(), None, now=WINDOW_TS + 100)
            assert d.action == 'HOLD' and d.reason


# ============ 5. registry wiring ============

class TestRegistry:

    def test_build_strategies_returns_at_least_eleven_unique_names(self):
        # This file is about THREE parameter variants. An absolute count makes
        # it fail on somebody else's legal append - which it already did once,
        # at 11 -> 15 (the fair-value inverse plus the three liquidation-fed
        # strategies). What this file is entitled to assert is that our three
        # arrived and that nobody shadowed a name: a duplicate key would pool
        # two strategies' rows into one population, which is a scoring bug and
        # not a registry cosmetic. Guarding the TOTAL against an accidental
        # addition is the registry's own test to own, not this one's.
        strategies = build_strategies()
        names = [s.strategy_name for s in strategies]
        assert len(strategies) >= 11
        assert len(set(names)) == len(names), names
        assert set(VARIANT_NAMES) <= set(names), names

    def test_the_three_variants_are_appended_after_the_parent(self):
        # Historical log lines are keyed by position in somebody's head, so the
        # three PARAMETER variants must stay CONTIGUOUS and stay immediately
        # after their parent. Their absolute index is not ours to pin, and
        # neither is what follows them: the inverse and the liquidation
        # strategies are appended after, and neither is a parameter variant.
        names = [s.strategy_name for s in build_strategies()]
        parent = names.index('PM_fair_value_arb')
        idx = [names.index(n) for n in VARIANT_NAMES]

        assert idx[0] == parent + 1, names
        # Contiguous AND in the declared order - a reordering would silently
        # re-key every historical log line that is read by position.
        assert idx == list(range(parent + 1, parent + 1 + len(VARIANT_NAMES))), \
            names

    def test_the_first_eight_did_not_move(self):
        names = [s.strategy_name for s in build_strategies()]
        assert names[:8] == [
            'PM_streak_snapper', 'PM_mid_price_continuation', 'PM_box_builder',
            'PM_corridor_collector', 'PM_temporal_arbitrage',
            'PM_corridor_pair', 'PM_spread_harvest_taker', 'PM_fair_value_arb']

    def test_each_variant_gets_a_fresh_tape_and_a_fresh_ledger(self):
        # Two loops feeding one tape would interleave observations into a
        # series neither of them saw; a shared ledger would share attempt
        # counts across callers.
        a = {s.strategy_name: s for s in build_strategies()}
        b = {s.strategy_name: s for s in build_strategies()}
        for name in VARIANT_NAMES:
            assert a[name] is not b[name]
            a[name].tape.observe(1.0, 100.0)
            a[name]._note_attempt(WINDOW_TS)
            assert len(b[name].tape.samples) == 0
            assert b[name].trades_this_window(WINDOW_TS) == 0

    def test_the_fair_value_instances_do_not_share_one_tape(self):
        # Independent copies of the same observations: wasteful and deliberate.
        # A shared tape would couple their state, so a bug in one would silently
        # move every other member of the family. Asserted over WHOEVER is in the
        # family today (the inverse joined it after this file was written), not
        # over a pinned count - the property is "no two share a tape", and that
        # is true at four members and at forty.
        fam = [s for s in build_strategies()
               if s.strategy_name.startswith('PM_fair_value_arb')]
        assert len(fam) >= 4
        # The four this file is responsible for are all present.
        assert {'PM_fair_value_arb'} | set(VARIANT_NAMES) <= {
            s.strategy_name for s in fam}

        # Distinct objects, and distinct tape objects behind them.
        assert len({id(s) for s in fam}) == len(fam)
        assert len({id(s.tape) for s in fam}) == len(fam)

        # And observing on one moves exactly one.
        fam[0].tape.observe(1.0, 100.0)
        assert [len(s.tape.samples) for s in fam] == [1] + [0] * (len(fam) - 1)

    def test_every_variant_manages_its_own_exits(self):
        by_name = {s.strategy_name: s for s in build_strategies()}
        # Our three variants sell before resolution exactly like the parent, so
        # each must manage its own exits. Checked BY NAME rather than by
        # comparing the whole manager set, because who else manages exits is
        # somebody else's question: the inverse joined the family and does, the
        # liquidation strategies hold to resolution and do not, and this file
        # should not go red when that population changes.
        for name in ('PM_fair_value_arb',) + VARIANT_NAMES:
            assert getattr(by_name[name], 'manages_exits', False) is True, name

        # The control: a hold-to-resolution strategy must NOT be in the set, or
        # "manages_exits" would be true of everything and assert nothing.
        assert getattr(by_name['PM_streak_snapper'], 'manages_exits',
                       False) is False

    def test_exit_decisions_only_touches_this_variants_positions(self):
        # Four strategies now poll the same open-position list. A variant that
        # managed the parent's positions would close trades it did not open and
        # book the PnL to the wrong population.
        s = FairValueArbPatient()
        books = {UP_TOK: _book(UP_TOK, asks=[(0.64, 100)], bids=[(0.62, 100)])}
        positions = [_position(strategy='PM_fair_value_arb_patient'),
                     _position(strategy='PM_fair_value_arb'),
                     _position(strategy='PM_fair_value_arb_wide'),
                     _position(strategy='PM_streak_snapper')]
        out = s.exit_decisions(positions, books, now=WINDOW_TS + 200)
        assert len(out) == 1

    def test_the_shadow_loop_identity_is_computed_from_the_list_length(self):
        # The identity is now `evaluations == cycles * strategies * assets`:
        # `check_identity` multiplies by `self.evaluations_per_cycle`, and that
        # property SUMS `len(rt.strategies)` over the per-asset runtimes. So the
        # denominator is still derived from a list length and adding a strategy
        # still needs no code change there - the chain just runs through one
        # more hop than it did when this file was written.
        #
        # Asserted across BOTH hops, because `check_identity` alone no longer
        # contains a length: stopping at the first hop would pass on a property
        # that had been hardcoded (convention 22).
        import inspect

        from engine.polymarket.shadow_loop import PolymarketShadowLoop

        src = inspect.getsource(PolymarketShadowLoop.check_identity)
        assert 'self.evaluations_per_cycle' in src

        per_cycle = inspect.getsource(
            PolymarketShadowLoop.evaluations_per_cycle.fget)
        assert 'len(' in per_cycle and '.strategies)' in per_cycle

        # No literal strategy count anywhere in the chain. ` 8`/` 11`/` 15` as
        # standalone tokens would mean somebody pinned the denominator, which is
        # the failure this test exists to catch.
        import re
        for text in (src, per_cycle):
            assert not re.search(r'(?<![\w.])(?:8|11|15)(?![\w.])', text), text


# ============ 6. house rules ============

class TestHouseRules:

    @pytest.mark.parametrize('mod', VARIANT_MODULES)
    def test_paper_mode_in_every_module(self, mod):
        assert mod.PAPER_MODE is True, mod.__name__

    @pytest.mark.parametrize('cls', VARIANT_CLASSES)
    def test_paper_mode_on_every_class_and_on_every_row(self, cls):
        s = cls()
        assert s.paper_mode is True
        assert s.evaluate(_ctx()).features['paper_mode'] is True

    @pytest.mark.parametrize('mod', VARIANT_MODULES)
    def test_every_module_states_a_kill_condition(self, mod):
        # Convention 6: a proposal without a kill condition is a hope.
        assert 'KILL CONDITION' in (mod.__doc__ or ''), mod.__name__

    @pytest.mark.parametrize('cls', VARIANT_CLASSES)
    def test_the_vendor_number_is_still_stamped_as_unverified(self, cls):
        # Inherited provenance is still provenance: the 99.3% claim is a Reddit
        # screenshot and no variant of it becomes a measurement.
        feats = cls().evaluate(_ctx()).features
        assert feats['claimed_win_rate_is_unverified_vendor_number'] is True
        assert feats['trade_count_is_attempts_not_fills'] is True

    @pytest.mark.parametrize('cls', VARIANT_CLASSES)
    def test_a_signal_maps_onto_the_binary_payoff(self, cls):
        # entry = premium, stop = 0.00 (a losing share IS worth zero, which
        # satisfies convention 8), target = 1.00.
        s = cls()
        signal = s.decision_to_signal(s.evaluate(_ctx()))
        assert signal is not None
        assert signal.stop == 0.0 and signal.target == 1.0
        assert 0.0 < signal.entry <= 1.0
        assert signal.pattern == s.strategy_name

    @pytest.mark.parametrize('cls', VARIANT_CLASSES)
    def test_the_scanner_path_never_invents_an_entry_without_a_book(self, cls):
        candles = {
            'timestamps': [1000 + 300 * i for i in range(20)],
            'opens': [60000.0 + 100 * i for i in range(20)],
            'closes': [60100.0 + 100 * i for i in range(20)],
        }
        assert cls().scan(candles) is None

    @pytest.mark.parametrize('cls', VARIANT_CLASSES)
    def test_it_is_a_thin_variant_and_not_a_fork(self, cls):
        # If a variant ever reimplements `evaluate`, it stops being the same
        # hypothesis and its results stop being comparable to the parent's.
        assert cls.evaluate is FairValueArb.evaluate
        assert cls.estimate is FairValueArb.estimate

    def test_only_patient_overrides_manage_exit(self):
        assert FairValueArbWide.manage_exit is FairValueArb.manage_exit
        assert FairValueArbHFT.manage_exit is FairValueArb.manage_exit
        assert FairValueArbPatient.manage_exit is not FairValueArb.manage_exit
