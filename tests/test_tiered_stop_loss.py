"""The tiered stop loss: the rule, its edges, and the wiring of all six users.

## What this file is about

Until 2026-08-18 every exit-managing Polymarket strategy carried its own
`max_loss` constant - a FIXED distance in cents of a $1.00 contract, applied at
every entry price:

    fair_value_arb          0.03      fair_value_arb_wide      0.05
    fair_value_arb_hft      0.02      fair_value_arb_patient   0.03
    fair_value_arb_inverse  0.03      dip_arb                  0.05

A fixed cent distance is a wildly different amount of RISK depending on where
the contract is priced, because the loss is measured against the premium paid
and not against the $1.00 payout. `strategies.polymarket.base.tiered_stop_price`
replaces all six with one rule keyed to the entry tier.

## The part that must not be lost: this is NOT uniformly a risk reduction

`TestTheTiersAreNotMonotonicInRisk` pins the two places the tiers are worse than
the constants they replaced, so that nobody reads "tiered stops" as "smaller
losses" without meeting the counter-example first:

  * a 0.06 entry gets a 0.05 stop, which is 83% of the premium, worse than the
    3c-on-6c (50%) case that motivated the change;
  * a 0.10 entry gets a 0.08 stop (80%) where a 0.0999 entry gets 0.05 (50%),
    so risk as a fraction of premium JUMPS UP at the boundary rather than down.

Both are consequences of stating the distance against the payout. They are
recorded here as behaviour, not asserted as good.

## Convention 22

Six strategies "use the shared helper" is a claim about wiring, and a docstring
saying so is not a test of it. `TestEveryStrategyIsWired` proves it three ways
per strategy: the accessor returns the helper's answer, `manage_exit` fires
`price_stop` at the TIERED price rather than at `entry - max_loss`, and the
decision row carries the tiered feature block.
"""
import math

import pytest

from strategies.polymarket.base import (BINARY_STOP, STOP_SIDE_LABELS,
                                        STOP_TIERS, effective_stop_distance,
                                        tiered_stop_distance,
                                        tiered_stop_features,
                                        tiered_stop_price)
from strategies.polymarket.dip_arb import DipArb
from strategies.polymarket.fair_value_arb import FairValueArb
from strategies.polymarket.fair_value_arb_hft import FairValueArbHFT
from strategies.polymarket.fair_value_arb_inverse import FairValueArbInverse
from strategies.polymarket.fair_value_arb_patient import FairValueArbPatient
from strategies.polymarket.fair_value_arb_wide import FairValueArbWide

#: The six strategies Raven's instruction named. Kept as a module constant so a
#: seventh exit-managing strategy that forgets the helper shows up as a missing
#: entry here rather than as silence.
ALL_SIX = (FairValueArb, FairValueArbHFT, FairValueArbWide,
           FairValueArbPatient, FairValueArbInverse, DipArb)


class _Book:
    """The two fields `manage_exit` reads. Nothing else."""

    def __init__(self, best_bid=None, best_ask=None):
        self.best_bid = best_bid
        self.best_ask = best_ask


class _Position:
    """A stand-in for `PaperPosition` carrying only the read fields."""

    def __init__(self, avg_price, shares=20.0, opened_ts=1000.0,
                 window_ts=None, outcome_side='Up', token_id='tok',
                 strategy='PM_fair_value_arb'):
        self.position_id = 'pos-1'
        self.avg_price = avg_price
        self.shares = shares
        self.opened_ts = opened_ts
        self.window_ts = window_ts
        self.outcome_side = outcome_side
        self.token_id = token_id
        self.strategy = strategy


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

class TestTheThreeTiers:
    """entry < 0.10 -> 0.05, 0.10 <= entry < 0.50 -> 0.08, else 0.10."""

    @pytest.mark.parametrize('entry,distance', [
        (0.06, 0.05), (0.07, 0.05), (0.09, 0.05), (0.0999, 0.05),
        (0.11, 0.08), (0.25, 0.08), (0.35, 0.08), (0.4999, 0.08),
        (0.55, 0.10), (0.69, 0.10), (0.83, 0.10), (0.96, 0.10), (1.00, 0.10),
    ])
    def test_the_nominal_distance_for_each_tier(self, entry, distance):
        assert tiered_stop_distance(entry) == pytest.approx(distance)

    @pytest.mark.parametrize('entry,stop', [
        (0.06, 0.01), (0.0999, 0.0499),
        (0.11, 0.03), (0.35, 0.27), (0.4999, 0.4199),
        (0.55, 0.45), (0.6907, 0.5907), (0.96, 0.86),
    ])
    def test_the_stop_price_is_entry_minus_the_tier_distance(self, entry, stop):
        assert tiered_stop_price(entry) == pytest.approx(stop)

    def test_the_distance_is_absolute_price_not_a_fraction_of_entry(self):
        """0.10 of a $1.00 payout, never 10% of what we paid.

        The single most likely misreading of "stop at 10c" - and the two
        readings differ by a factor of ten on a 0.10 contract, so a test that
        did not pin it would let the wrong one ship.
        """
        # 10% of a 0.80 entry would be 0.08 and the stop would be 0.72.
        assert tiered_stop_price(0.80) == pytest.approx(0.70)
        # 5% of a 0.06 entry would be 0.003 and the stop would be 0.057.
        assert tiered_stop_price(0.06) == pytest.approx(0.01)

    def test_the_table_is_ordered_and_ends_unbounded(self):
        """A tier table read top-down is only correct if it is sorted."""
        bounds = [u for u, _d in STOP_TIERS]
        assert bounds == sorted(bounds)
        assert bounds[-1] == math.inf


class TestBoundaries:
    """0.10 and 0.50 exactly. Both belong to the tier ABOVE them."""

    def test_ten_cents_exactly_is_the_middle_tier(self):
        assert tiered_stop_distance(0.10) == pytest.approx(0.08)
        assert tiered_stop_price(0.10) == pytest.approx(0.02)

    def test_just_under_ten_cents_is_the_bottom_tier(self):
        assert tiered_stop_distance(0.0999999) == pytest.approx(0.05)

    def test_fifty_cents_exactly_is_the_top_tier(self):
        assert tiered_stop_distance(0.50) == pytest.approx(0.10)
        assert tiered_stop_price(0.50) == pytest.approx(0.40)

    def test_just_under_fifty_cents_is_the_middle_tier(self):
        assert tiered_stop_distance(0.4999999) == pytest.approx(0.08)

    def test_the_literal_float_0_10_lands_where_the_rule_says(self):
        """`0.10` is 0.1000000000000000055 in binary, so `entry < 0.10` is
        False for it and the middle tier is correct. Pinned because a rewrite
        that adds an epsilon to the comparison would silently move the
        boundary by one tier for exactly the value most likely to be typed
        into a config.
        """
        assert not (0.10 < STOP_TIERS[0][0])
        assert tiered_stop_distance(0.10) == STOP_TIERS[1][1]


class TestStopIsStrictlyBelowEntry:
    """Convention 8, enforced rather than assumed."""

    @pytest.mark.parametrize('entry', [
        0.01, 0.02, 0.03, 0.05, 0.06, 0.0999, 0.10, 0.11, 0.25, 0.4999,
        0.50, 0.75, 0.99, 1.00,
    ])
    def test_every_valid_entry_gets_a_stop_strictly_below_it(self, entry):
        stop = tiered_stop_price(entry)
        assert stop < entry
        assert stop >= BINARY_STOP

    def test_a_zero_entry_has_no_stop_and_raises(self):
        """There is no price strictly below 0.00, so this is a fault upstream
        rather than a trade with a bad stop (convention 11)."""
        with pytest.raises(ValueError):
            tiered_stop_price(0.0)

    def test_a_negative_entry_raises(self):
        with pytest.raises(ValueError):
            tiered_stop_price(-0.05)

    def test_an_entry_above_one_dollar_raises(self):
        """Not a binary at all."""
        with pytest.raises(ValueError):
            tiered_stop_price(1.01)

    def test_a_nan_entry_raises_rather_than_producing_a_nan_stop(self):
        with pytest.raises(ValueError):
            tiered_stop_price(float('nan'))


class TestTheDegenerateLowEntry:
    """Entry at or below its own tier distance: 0.03 with a 0.05 rule.

    The stop CANNOT be placed where the rule asks. The choice made is to clamp
    onto the structural binary floor at 0.00 and to say so on the row, never to
    invent a different distance.
    """

    @pytest.mark.parametrize('entry', [0.01, 0.02, 0.03, 0.04, 0.05])
    def test_the_stop_clamps_to_zero_not_to_a_negative_price(self, entry):
        assert tiered_stop_price(entry) == pytest.approx(BINARY_STOP)

    @pytest.mark.parametrize('entry', [0.01, 0.03, 0.05])
    def test_it_is_flagged_as_the_structural_floor(self, entry):
        f = tiered_stop_features(entry)
        assert f['stop_is_structural_floor'] is True
        # The rule asked for 0.05 and could only deliver `entry`. BOTH numbers
        # are on the row: one counter for two facts is convention 20's whole
        # complaint.
        assert f['stop_distance_nominal'] == pytest.approx(0.05)
        assert f['stop_distance'] == pytest.approx(entry)
        assert f['stop_loss_fraction_of_entry'] == pytest.approx(1.0)

    def test_effective_distance_is_the_whole_premium_there(self):
        assert effective_stop_distance(0.03) == pytest.approx(0.03)
        assert effective_stop_distance(0.05) == pytest.approx(0.05)

    def test_but_it_is_still_strictly_below_entry(self):
        """0.00 < 0.03. Convention 8 survives the degenerate case; what does
        not survive is the CLAIM that the loss is bounded at 5c."""
        assert tiered_stop_price(0.03) < 0.03

    def test_just_above_the_degenerate_band_the_stop_exists_again(self):
        f = tiered_stop_features(0.06)
        assert f['stop_is_structural_floor'] is False
        assert f['stop_price'] == pytest.approx(0.01)


class TestTheTiersAreNotMonotonicInRisk:
    """The counter-examples. Read these before calling this a risk reduction.

    Raven's brief called a 3c stop on a 6c entry "50% loss per tick" and
    catastrophic. Both facts below are worse than that, and both follow
    directly from stating the distance against the $1.00 payout rather than
    against the premium.
    """

    def test_a_six_cent_entry_now_risks_83_percent_not_50(self):
        f = tiered_stop_features(0.06)
        assert f['stop_loss_fraction_of_entry'] == pytest.approx(0.8333, abs=1e-4)
        # The old fixed 3c stop on the same fill:
        assert (0.03 / 0.06) == pytest.approx(0.50)

    def test_risk_as_a_fraction_of_premium_jumps_UP_at_the_ten_cent_boundary(self):
        below = tiered_stop_features(0.0999)['stop_loss_fraction_of_entry']
        at = tiered_stop_features(0.10)['stop_loss_fraction_of_entry']
        assert below == pytest.approx(0.5005, abs=1e-3)
        assert at == pytest.approx(0.80)
        assert at > below

    def test_where_the_tiers_DO_cut_risk_is_the_bucket_that_holds_the_volume(self):
        """261 of 295 `PM_fair_value_arb` fills sat in [0.10, 0.50) and 29 at
        or above 0.50 (db/trading.db, 2026-08-18). At the family's measured
        mean fill of 0.3522 the stop widened 0.03 -> 0.08, so the per-trade
        loss got BIGGER and the number of stop-outs should fall. Which of
        those dominates is NOT_MEASURED and this test does not claim it.
        """
        assert tiered_stop_distance(0.3522) == pytest.approx(0.08)
        assert tiered_stop_distance(0.6907) == pytest.approx(0.10)


class TestSides:
    """Every position in this package is a LONG of one outcome token."""

    @pytest.mark.parametrize('side', ['Up', 'Down', 'Yes', 'No',
                                      'up', 'DOWN', ' yes '])
    def test_a_known_side_is_accepted_and_changes_nothing(self, side):
        assert tiered_stop_price(0.35, side) == tiered_stop_price(0.35, None)

    def test_both_sides_of_the_same_market_stop_below_their_own_entry(self):
        """Up at 0.62 and Down at 0.41 are two separate longs at two separate
        prices, not one position and its mirror. The Down stop is NOT
        `1 - up_stop`."""
        up = tiered_stop_price(0.62, 'Up')
        down = tiered_stop_price(0.41, 'Down')
        assert up == pytest.approx(0.52)
        assert down == pytest.approx(0.33)
        assert down != pytest.approx(1.0 - up)

    def test_an_unknown_side_raises_rather_than_guessing(self):
        with pytest.raises(ValueError):
            tiered_stop_price(0.35, 'Long')

    def test_the_known_labels_are_the_four_this_package_trades(self):
        assert set(STOP_SIDE_LABELS) == {'up', 'down', 'yes', 'no'}


class TestFeatureBlock:

    def test_the_keys_are_the_same_shape_for_every_entry(self):
        keys = {frozenset(tiered_stop_features(e))
                for e in (0.02, 0.06, 0.10, 0.35, 0.50, 0.99)}
        assert len(keys) == 1

    def test_the_top_tier_bound_is_None_and_never_inf(self):
        """These features are serialised with `allow_nan=False` (convention
        19); an infinity here would get the key silently stripped."""
        assert tiered_stop_features(0.75)['stop_tier_upper_bound'] is None
        assert tiered_stop_features(0.35)['stop_tier_upper_bound'] == 0.50
        assert tiered_stop_features(0.06)['stop_tier_upper_bound'] == 0.10

    def test_every_value_is_json_strict_serialisable(self):
        import json
        for e in (0.01, 0.06, 0.10, 0.35, 0.50, 1.00):
            json.dumps(tiered_stop_features(e), allow_nan=False)


# ---------------------------------------------------------------------------
# Wiring (convention 22)
# ---------------------------------------------------------------------------

class TestEveryStrategyIsWired:
    """All six reach `base.tiered_stop_price`, proved on the instance."""

    @pytest.mark.parametrize('cls', ALL_SIX, ids=lambda c: c.__name__)
    @pytest.mark.parametrize('entry', [0.06, 0.35, 0.75])
    def test_stop_price_for_returns_the_shared_helpers_answer(self, cls, entry):
        s = cls()
        assert s.stop_price_for(entry) == pytest.approx(
            tiered_stop_price(entry))
        assert s.stop_distance_for(entry) == pytest.approx(
            effective_stop_distance(entry))

    @pytest.mark.parametrize('cls', ALL_SIX, ids=lambda c: c.__name__)
    def test_the_six_all_agree_with_each_other_at_the_same_entry(self, cls):
        """The point of one rule in one place: the stop is a property of the
        FILL, not of which strategy took it."""
        assert cls().stop_price_for(0.35) == pytest.approx(
            FairValueArb().stop_price_for(0.35))

    @pytest.mark.parametrize('cls', ALL_SIX, ids=lambda c: c.__name__)
    def test_no_strategy_still_stops_at_entry_minus_its_max_loss(self, cls):
        """The regression this whole change is about.

        Each of the six has a `max_loss` that USED to be the stop. At a 0.35
        entry none of the old distances (0.02, 0.03, 0.05) equals the tiered
        0.08, so a strategy still reading its constant fails here.
        """
        s = cls()
        assert s.stop_distance_for(0.35) == pytest.approx(0.08)
        assert s.stop_distance_for(0.35) != pytest.approx(s.max_loss)

    @pytest.mark.parametrize('cls', ALL_SIX, ids=lambda c: c.__name__)
    def test_an_explicit_max_loss_kwarg_no_longer_moves_the_stop(self, cls):
        """`max_loss` survives as the NOMINAL geometry `breakeven_win_rate` is
        stated against. It is not a second stop rule, and a caller who sets it
        expecting one should find out here rather than from a P&L column."""
        s = cls(max_loss=0.01)
        assert s.max_loss == pytest.approx(0.01)
        assert s.stop_distance_for(0.35) == pytest.approx(0.08)


class TestManageExitFiresAtTheTieredPrice:
    """The stop is not wired until an EXIT rule reads it."""

    @pytest.mark.parametrize('cls', ALL_SIX, ids=lambda c: c.__name__)
    def test_price_stop_fires_at_the_tiered_stop(self, cls):
        s = cls()
        pos = _Position(avg_price=0.35, strategy=cls.strategy_name)
        # 0.27 is the tiered stop for a 0.35 entry.
        d = s.manage_exit(pos, _Book(best_bid=0.27, best_ask=0.29), now=1020.0)
        assert d.action == 'EXIT'
        assert d.reason == 'price_stop'
        assert d.features['stop_price'] == pytest.approx(0.27)
        assert d.features['stop_is_tiered'] is True
        assert d.features['max_loss'] == pytest.approx(0.08)

    @pytest.mark.parametrize('cls', ALL_SIX, ids=lambda c: c.__name__)
    def test_it_does_NOT_fire_where_the_old_fixed_stop_would_have(self, cls):
        """Bid 0.31 on a 0.35 entry is past every old constant (0.02, 0.03,
        0.05) and inside the tiered 0.08. Under the old rule five of the six
        would have stopped out here; none may now."""
        s = cls()
        pos = _Position(avg_price=0.35, strategy=cls.strategy_name)
        d = s.manage_exit(pos, _Book(best_bid=0.31, best_ask=0.33), now=1005.0)
        assert d.reason != 'price_stop'

    @pytest.mark.parametrize('cls', ALL_SIX, ids=lambda c: c.__name__)
    def test_a_high_entry_stops_ten_cents_down_not_three(self, cls):
        s = cls()
        pos = _Position(avg_price=0.70, strategy=cls.strategy_name)
        held = s.manage_exit(pos, _Book(best_bid=0.65, best_ask=0.67),
                             now=1020.0)
        assert held.reason != 'price_stop'
        stopped = s.manage_exit(pos, _Book(best_bid=0.60, best_ask=0.62),
                                now=1020.0)
        assert stopped.reason == 'price_stop'
        assert stopped.features['stop_price'] == pytest.approx(0.60)

    @pytest.mark.parametrize('cls', ALL_SIX, ids=lambda c: c.__name__)
    def test_a_degenerate_entry_can_never_hit_its_price_stop(self, cls):
        """A 0.03 fill has a 0.00 stop, so no bid on the grid reaches it. The
        row must SAY that rather than looking like a patient hold."""
        s = cls()
        pos = _Position(avg_price=0.03, strategy=cls.strategy_name)
        d = s.manage_exit(pos, _Book(best_bid=0.01, best_ask=0.02), now=1005.0)
        assert d.reason != 'price_stop'
        assert d.features['stop_is_structural_floor'] is True
        assert d.features['stop_price'] == pytest.approx(0.0)

    @pytest.mark.parametrize('cls', ALL_SIX, ids=lambda c: c.__name__)
    def test_every_exit_row_carries_the_stop_block(self, cls):
        """Including HOLDs. A stop that only appears on the row where it fired
        cannot be used to work out how often it did not (convention 20)."""
        s = cls()
        pos = _Position(avg_price=0.35, strategy=cls.strategy_name)
        d = s.manage_exit(pos, _Book(best_bid=0.34, best_ask=0.36), now=1005.0)
        for key in ('stop_price', 'stop_distance', 'stop_distance_nominal',
                    'stop_is_tiered', 'stop_loss_fraction_of_entry',
                    'stop_is_structural_floor'):
            assert key in d.features, key

    @pytest.mark.parametrize('cls', ALL_SIX, ids=lambda c: c.__name__)
    def test_an_unreadable_position_says_so_instead_of_stopping_at_zero(self,
                                                                       cls):
        s = cls()
        pos = _Position(avg_price=0.0, strategy=cls.strategy_name)
        d = s.manage_exit(pos, _Book(best_bid=0.34, best_ask=0.36), now=1005.0)
        assert d.reason == 'unreadable_position'
        assert d.features['stop_price'] is None
        assert d.features['stop_uncomputable_reason'] == \
            'entry_price_not_positive'


class TestPatientStillDefersTheTieredStop:
    """The min-hold defers whatever stop the parent computed, tiered or not."""

    def test_the_tiered_price_stop_is_suppressed_inside_the_min_hold(self):
        s = FairValueArbPatient()
        pos = _Position(avg_price=0.35, opened_ts=1000.0,
                        strategy='PM_fair_value_arb_patient')
        d = s.manage_exit(pos, _Book(best_bid=0.27, best_ask=0.29), now=1005.0)
        assert d.action == 'HOLD'
        assert d.reason == 'min_hold_not_met'
        assert d.features['min_hold_suppressed_reason'] == 'price_stop'
        assert d.features['stop_deferred_worst_case_is_full_premium'] is True

    def test_and_taken_once_the_min_hold_elapses(self):
        s = FairValueArbPatient()
        pos = _Position(avg_price=0.35, opened_ts=1000.0,
                        strategy='PM_fair_value_arb_patient')
        d = s.manage_exit(pos, _Book(best_bid=0.27, best_ask=0.29), now=1020.0)
        assert d.action == 'EXIT'
        assert d.reason == 'price_stop'
        assert d.features['stop_price'] == pytest.approx(0.27)


class TestBreakevenAtTheTieredStop:
    """`breakeven_win_rate` describes the SPEC; the tiered one describes the
    position. Both are on the entry row so a reader is never left guessing."""

    def test_the_parent_needed_75_percent_on_paper_and_more_in_practice(self):
        """The parent has no `breakeven_win_rate` property - only the variants
        do - so its specified 0.75 is stated in the docstring and computed
        here from the constants the docstring names."""
        import strategies.polymarket.fair_value_arb as fva
        specified = fva.MAX_LOSS / (fva.MIN_PROFIT + fva.MAX_LOSS)
        assert specified == pytest.approx(0.75)
        # 0.08 / (0.01 + 0.08) at a mid-tier fill.
        s = FairValueArb()
        assert s.breakeven_win_rate_at(0.35) == pytest.approx(8 / 9, abs=1e-6)

    def test_it_moves_with_the_tier(self):
        s = FairValueArb()
        assert s.breakeven_win_rate_at(0.35) > s.breakeven_win_rate_at(0.06)

    def test_hft_and_the_parent_now_share_a_stop_and_a_breakeven(self):
        """The consequence flagged in `fair_value_arb_hft.MAX_LOSS_HFT`: the
        stop was `_hft`'s only payoff-axis differentiator and it is now shared.
        Pinned so the collapse is a decision on the record, not a surprise."""
        import strategies.polymarket.fair_value_arb as fva
        assert FairValueArbHFT().breakeven_win_rate_at(0.35) == pytest.approx(
            FairValueArb().breakeven_win_rate_at(0.35))
        # ...while the SPECIFIED geometries still differ, 0.667 vs 0.75.
        assert FairValueArbHFT().breakeven_win_rate == pytest.approx(2 / 3)
        assert FairValueArbHFT().breakeven_win_rate != pytest.approx(
            fva.MAX_LOSS / (fva.MIN_PROFIT + fva.MAX_LOSS))


class TestPersistence:
    """`stop_px` on the positions row. The reason the audit could not read the
    family's stop at all: 616 closed trades all recorded 0.00."""

    def _store(self, tmp_path):
        import sqlite3

        from engine.polymarket.shadow_loop import ShadowStore
        db = tmp_path / 'stops.db'
        conn = sqlite3.connect(str(db))
        conn.executescript(open('db/schema.sql').read())
        conn.commit()
        conn.close()
        return ShadowStore(str(db))

    def test_record_entry_writes_the_stop_it_is_given(self, tmp_path):
        store = self._store(tmp_path)
        pos = _Position(avg_price=0.35)
        pos.market_slug = 'btc-updown-5m-1'
        pos.fee_usdc = 0.0
        store.record_entry(pos, signal_id=None, limit_price=0.36,
                           strategy_id='PM_fair_value_arb', stop_px=0.27)
        row = store.conn.execute(
            'SELECT stop_px, target_px FROM positions').fetchone()
        assert row['stop_px'] == pytest.approx(0.27)
        assert row['target_px'] == pytest.approx(1.00)
        store.close()

    def test_None_still_writes_the_structural_floor(self, tmp_path):
        """Correct for a hold-to-resolution strategy, and now it MEANS that
        rather than meaning "nobody wrote this column"."""
        store = self._store(tmp_path)
        pos = _Position(avg_price=0.35)
        pos.market_slug = 'btc-updown-5m-1'
        pos.fee_usdc = 0.0
        store.record_entry(pos, signal_id=None, limit_price=0.36,
                           strategy_id='PM_streak_snapper')
        row = store.conn.execute('SELECT stop_px FROM positions').fetchone()
        assert row['stop_px'] == pytest.approx(0.0)
        store.close()

    @pytest.mark.parametrize('cls', ALL_SIX, ids=lambda c: c.__name__)
    def test_the_loop_resolves_a_stop_for_each_of_the_six(self, cls):
        """`_entry_stop_px` dispatches on `stop_price_for`. Called unbound so
        the test does not have to build a whole loop."""
        from engine.polymarket.shadow_loop import PolymarketShadowLoop

        class _FakeLoop:
            health = {}
        pos = _Position(avg_price=0.35, strategy=cls.strategy_name)
        got = PolymarketShadowLoop._entry_stop_px(_FakeLoop(), cls(), pos)
        assert got == pytest.approx(0.27)

    def test_a_hold_to_resolution_strategy_resolves_to_None(self):
        from engine.polymarket.shadow_loop import PolymarketShadowLoop
        from strategies.polymarket.streak_snapper import StreakSnapper

        class _FakeLoop:
            health = {}
        pos = _Position(avg_price=0.35)
        assert PolymarketShadowLoop._entry_stop_px(
            _FakeLoop(), StreakSnapper(), pos) is None
