"""Tests for `PM_fair_value_settlement_exit` (proposal 034).

No network. Every orderbook, window, position and spot here is synthetic.

Six jobs, matching `docs/handoffs/from-raven/2026-08-18-proposal-034.md`:

  1. **Task 0's finding is pinned**: the live exit path cannot read
     `positions.stop_px` because the object it decides against
     (`PaperPosition`) carries no such field at all - the stop is always
     computed live from the fill. `TestTask0Finding`.
  2. **The entry model is the parent's, unchanged, only post-filtered**:
     same fair value, same side selection, same depth gate.
     `TestEntryReusesParentModel`.
  3. **The tightened gate, both halves**: edge_threshold 0.05 (not the
     parent's 0.04) and the new 0.60 entry-ask cap. `TestTightenedGate`.
  4. **The salvage-floor stop, and its degenerate collapse below 0.10**.
     `TestSalvageFloorExit`.
  5. **Concurrency self-cap**: max 2 open per instance. `TestConcurrencyCap`.
  6. **Registry**: 25 strategies, this one at index 24, crypto-only,
     first 8 unchanged. `TestRegistry`.

Per D-268 / convention 11: nothing here scores edge. Zero rows exist under
`PM_fair_value_settlement_exit` before this session wires it in.
"""
import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.halt as halt_mod  # noqa: E402
from engine.polymarket.paper_adapter import PaperPosition  # noqa: E402
from engine.polymarket.types import (Market, Orderbook,  # noqa: E402
                                     Outcome, PriceLevel)
from strategies.polymarket.base import (MARKET_TYPE_CRYPTO_UPDOWN,  # noqa: E402
                                        MarketContext, Window,
                                        tiered_stop_price)
from strategies.polymarket.fair_value_arb import (URGENT_SELL_LIMIT,  # noqa: E402
                                                  FairValueArb)
from strategies.polymarket.fair_value_settlement_exit import (  # noqa: E402
    ENTRY_ASK_CAP, MAX_CONCURRENT_POSITIONS, SALVAGE_FLOOR,
    FairValueSettlementExit, salvage_stop_price)

UP_TOK = 'tok-up'
DOWN_TOK = 'tok-down'
WINDOW_TS = 1_700_000_000
SLUG = 'btc-updown-5m-1700000000'
STRATEGY_NAME = 'PM_fair_value_settlement_exit'


@pytest.fixture(autouse=True)
def never_halted(tmp_path, monkeypatch):
    """A real HALT file in the repo would turn every entry test into a
    `halted` skip - working correctly, but indistinguishable here from this
    strategy being broken."""
    monkeypatch.setattr(halt_mod, 'HALT_FILE', str(tmp_path / 'NO_SUCH_HALT'))


# ============ fixtures (same shape as test_fair_value_arb_inverse.py's) =====

def _market(slug=SLUG):
    return Market(id='m-' + slug, question='BTC up or down?', slug=slug,
                 condition_id='cond-' + slug,
                 outcomes=(Outcome('Up', UP_TOK), Outcome('Down', DOWN_TOK)))


def _book(token, asks=(), bids=(), ts=WINDOW_TS):
    return Orderbook(
        token_id=token,
        bids=tuple(PriceLevel(float(p), float(s)) for p, s in bids),
        asks=tuple(PriceLevel(float(p), float(s)) for p, s in asks),
        timestamp=ts)


def _windows(window_ts, n=13, open_price=100_000.0, move=90.0):
    out = []
    for i in range(n):
        ts = window_ts - (n - 1 - i) * 300
        close = open_price + (move if i % 2 == 0 else -move)
        out.append(Window(ts=ts, open=open_price, close=close,
                          direction='UP' if close >= open_price else 'DOWN',
                          source='price'))
    return out


def _ctx(window_ts=WINDOW_TS, slug=SLUG, spot=100_060.0,
         seconds_into_window=100.0, up_asks=((0.60, 200.0),),
         down_asks=((0.42, 200.0),), up_bids=((0.58, 200.0),),
         down_bids=((0.40, 200.0),), windows=None, market=True):
    """Default arguments match `test_fair_value_arb_inverse.py`'s own
    baseline: P(Up) lands near 0.71 against a 0.60 Up ask, an 11c gap well
    clear of both the parent's 0.04 edge floor and this file's 0.05 one, and
    exactly AT (not above) `ENTRY_ASK_CAP` so it is a genuine baseline pass,
    not a value chosen to dodge the new gate.
    """
    books = {UP_TOK: _book(UP_TOK, up_asks, up_bids, ts=window_ts),
             DOWN_TOK: _book(DOWN_TOK, down_asks, down_bids, ts=window_ts)}
    return MarketContext(
        window_ts=window_ts,
        windows=_windows(window_ts) if windows is None else windows,
        market=_market(slug) if market else None,
        books=books, spot=spot, strike=None,
        seconds_into_window=seconds_into_window)


def _position(entry=0.60, shares=16.0, opened_ts=WINDOW_TS + 100,
             window_ts=WINDOW_TS, side='Up', token_id=UP_TOK,
             strategy=STRATEGY_NAME, position_id='pos-1', features=None):
    return PaperPosition(
        position_id=position_id, strategy=strategy, market_slug=SLUG,
        token_id=token_id, outcome_side=side, shares=shares,
        avg_price=entry, cost_usdc=entry * shares, fee_usdc=0.0,
        opened_ts=opened_ts, window_ts=window_ts, features=features or {})


# ============ 1. Task 0's finding, pinned ====================================

class TestTask0Finding:
    """The exit path reads a LIVE stop derived from the fill, never a stored
    `positions.stop_px`. See the module docstring and D-320."""

    def test_paper_position_carries_no_stop_px_field_at_all(self):
        field_names = {f.name for f in dataclasses.fields(PaperPosition)}
        assert 'stop_px' not in field_names, (
            'PaperPosition grew a stop_px field; the Task 0 finding that the '
            'live exit path cannot possibly read the stored DB column '
            'because the runtime object has no such field needs review')

    def test_parent_manage_exit_stop_price_is_derived_live_from_the_fill(self):
        """`FairValueArb.manage_exit`'s `stop_price` feature is exactly
        `tiered_stop_price(entry, side)` - computed from `avg_price` and
        `outcome_side` on the object handed in, never from a stored value."""
        s = FairValueArb()
        pos = PaperPosition(
            position_id='pos-parent', strategy='PM_fair_value_arb',
            market_slug=SLUG, token_id=UP_TOK, outcome_side='Up', shares=20.0,
            avg_price=0.42, cost_usdc=8.4, fee_usdc=0.0, opened_ts=WINDOW_TS)
        book = _book(UP_TOK, bids=((0.50, 200.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS + 30.0)
        assert d.features['stop_price'] == tiered_stop_price(0.42, 'Up')


# ============ 2. entry reuses the parent model, unchanged ====================

class TestEntryReusesParentModel:

    def test_baseline_context_enters_on_the_same_side_as_the_parent(self):
        ctx = _ctx()
        parent = FairValueArb().evaluate(ctx)
        settlement = FairValueSettlementExit().evaluate(ctx)
        assert parent.action == 'ENTER', (parent.reason, parent.features)
        assert settlement.action == 'ENTER', (settlement.reason,
                                              settlement.features)
        assert settlement.legs[0].outcome_side == parent.legs[0].outcome_side
        assert settlement.legs[0].outcome_side == 'Up'

    def test_entry_row_carries_the_settlement_specific_features(self):
        d = FairValueSettlementExit().evaluate(_ctx())
        assert d.action == 'ENTER'
        f = d.features
        assert f['primary_exit'] == 'hold_to_resolution'
        assert f['exits_before_resolution'] is False
        assert f['has_salvage_floor_stop'] is True
        assert f['entry_ask_cap'] == ENTRY_ASK_CAP
        # Task 1 rule 2/6: the raw model edge and the entry ask are on the
        # row under explicit names, not only under the parent's own keys.
        assert f['model_edge_at_entry'] == pytest.approx(f['raw_edge'])
        assert f['model_edge_at_entry'] > 0.05
        assert f['entry_ask'] == pytest.approx(0.60)
        assert f['best_bid_at_entry'] == pytest.approx(0.58)

    def test_a_parent_skip_passes_through_with_the_parents_reason(self):
        # No market at all: the parent refuses on the first line, and this
        # class must never invent its own reason for a cause it did not
        # create.
        ctx = _ctx(market=False)
        parent = FairValueArb().evaluate(ctx)
        settlement = FairValueSettlementExit().evaluate(ctx)
        assert parent.reason == 'no_market'
        assert settlement.reason == 'no_market'

    def test_manages_exits_and_supported_market_types(self):
        s = FairValueSettlementExit()
        assert s.manages_exits is True
        assert s.supported_market_types == (MARKET_TYPE_CRYPTO_UPDOWN,)


# ============ 3. the tightened gate, both halves =============================

class TestTightenedGate:

    def test_edge_threshold_is_tightened_to_point_zero_five(self):
        assert FairValueSettlementExit().edge_threshold == 0.05

    def test_edge_between_parents_floor_and_ours_is_now_below_threshold(self):
        # fair(Up) ~0.71, ask=0.665 -> edge ~0.045: clears the PARENT's 0.04
        # floor but not this file's 0.05 one. Proves the tightening is real,
        # not merely documented.
        ctx = _ctx(up_asks=((0.665, 200.0),))
        parent = FairValueArb().evaluate(ctx)
        settlement = FairValueSettlementExit().evaluate(ctx)
        assert parent.action == 'ENTER', (parent.reason, parent.features)
        assert 0.04 < parent.features['raw_edge'] < 0.05
        assert settlement.action == 'SKIP'
        assert settlement.reason == 'edge_below_threshold'

    def test_entry_ask_cap_is_point_six_zero(self):
        assert FairValueSettlementExit().entry_ask_cap == 0.60

    def test_ask_at_the_cap_exactly_still_enters(self):
        # Boundary check: 0.60 <= 0.60, not a genuine skip.
        d = FairValueSettlementExit().evaluate(_ctx(up_asks=((0.60, 200.0),)))
        assert d.action == 'ENTER'

    def test_ask_above_the_cap_skips_even_though_the_edge_is_fine(self):
        # fair(Up) ~0.71, ask=0.62 -> edge ~0.09, comfortably above 0.05.
        # Only the NEW cap should refuse this one.
        ctx = _ctx(up_asks=((0.62, 200.0),))
        parent = FairValueArb().evaluate(ctx)
        settlement = FairValueSettlementExit().evaluate(ctx)
        assert parent.action == 'ENTER', (parent.reason, parent.features)
        assert parent.features['raw_edge'] > 0.05
        assert settlement.action == 'SKIP'
        assert settlement.reason == 'settlement_entry_ask_above_cap'


# ============ 4. the salvage-floor exit =======================================

class TestSalvageFloorExit:

    def test_salvage_stop_price_is_the_flat_floor_above_it(self):
        assert salvage_stop_price(0.60, SALVAGE_FLOOR) == 0.10
        assert salvage_stop_price(0.11, SALVAGE_FLOOR) == 0.10

    def test_salvage_stop_price_collapses_to_the_structural_floor_below_it(self):
        # An entry at or below the 0.10 floor has no price strictly between
        # 0.00 and itself that the flat rule could mean - convention 8.
        assert salvage_stop_price(0.10, SALVAGE_FLOOR) == 0.0
        assert salvage_stop_price(0.06, SALVAGE_FLOOR) == 0.0

    def test_salvage_stop_price_rejects_a_non_positive_entry(self):
        with pytest.raises(ValueError):
            salvage_stop_price(0.0, SALVAGE_FLOOR)

    def test_bid_above_floor_holds_to_resolution(self):
        s = FairValueSettlementExit()
        pos = _position(entry=0.60)
        book = _book(UP_TOK, bids=((0.55, 200.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS + 60.0, fair_value=0.71)
        assert d.action == 'HOLD'
        assert d.reason == 'holding_to_resolution'
        assert d.features['fair_value_observed_not_acted_on'] == 0.71

    def test_bid_at_the_floor_exactly_exits(self):
        s = FairValueSettlementExit()
        pos = _position(entry=0.60)
        book = _book(UP_TOK, bids=((0.10, 200.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS + 60.0)
        assert d.action == 'EXIT'
        assert d.reason == 'salvage_floor'
        assert d.limit_price == URGENT_SELL_LIMIT
        assert d.shares == pos.shares
        assert d.features['salvage_floor_is_structural_floor'] is False

    def test_bid_below_the_floor_exits(self):
        s = FairValueSettlementExit()
        pos = _position(entry=0.60)
        book = _book(UP_TOK, bids=((0.05, 200.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS + 60.0)
        assert d.action == 'EXIT'
        assert d.reason == 'salvage_floor'

    def test_degenerate_entry_below_floor_needs_a_zero_bid_to_fire(self):
        # entry=0.06 collapses the salvage stop onto the structural 0.00
        # floor. A 0.08 bid is BELOW the nominal 0.10 floor but ABOVE the
        # collapsed stop, so this must HOLD, not exit - the flat rule cannot
        # fire above where the entry itself sits (convention 8).
        s = FairValueSettlementExit()
        pos = _position(entry=0.06)
        book = _book(UP_TOK, bids=((0.08, 200.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS + 60.0)
        assert d.action == 'HOLD'
        assert d.reason == 'holding_to_resolution'
        assert d.features['salvage_stop_price'] == 0.0
        assert d.features['salvage_floor_is_structural_floor'] is True

    def test_no_time_based_or_convergence_exit_regardless_of_elapsed_time(self):
        """No converged-mid sale, no time-based sale (proposal rule 3):
        `now` and `fair_value` are accepted for interface compatibility and
        never gate an exit on their own."""
        s = FairValueSettlementExit()
        pos = _position(entry=0.60)
        book = _book(UP_TOK, bids=((0.55, 200.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS + 100_000.0,
                          fair_value=0.99)
        assert d.action == 'HOLD'
        assert d.reason == 'holding_to_resolution'

    def test_no_orderbook_holds(self):
        s = FairValueSettlementExit()
        d = s.manage_exit(_position(), None, now=WINDOW_TS)
        assert d.action == 'HOLD'
        assert d.reason == 'no_orderbook'

    def test_no_bid_liquidity_holds(self):
        s = FairValueSettlementExit()
        book = _book(UP_TOK, asks=((0.62, 200.0),))
        d = s.manage_exit(_position(), book, now=WINDOW_TS)
        assert d.action == 'HOLD'
        assert d.reason == 'no_bid_liquidity'

    def test_unreadable_position_holds(self):
        s = FairValueSettlementExit()
        pos = _position(shares=0.0)
        book = _book(UP_TOK, bids=((0.55, 200.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS)
        assert d.action == 'HOLD'
        assert d.reason == 'unreadable_position'


# ============ 5. concurrency self-cap =========================================
#
# `self._open` is populated from the POSITION STREAM (`manage_exit`'s first
# sight of a filled `PaperPosition`), not from `evaluate()`'s ENTER decision -
# see the module docstring's 2026-08-19 fix note. Every test below that wants
# a slot occupied must therefore call `manage_exit` with a synthetic filled
# position carrying the matching `attempt_number`, exactly as the real
# adapter/`manage_exits` path would once a fill actually happens.

class TestConcurrencyCap:

    def test_downstream_rejection_no_longer_leaks_the_cap(self):
        """The bug this file was fixed for (2026-08-19): before the fix,
        `evaluate()` noted every ENTER as open at decision time, so three
        ENTER decisions in a row - none of which ever became a real position
        (`manage_exit` is never called here) - used to cap the third one
        under `strategy_concurrency_cap_reached` against zero real
        positions. Live evidence: 25 self-inflicted
        `strategy_concurrency_cap_reached` skips against 0 opened positions
        in the 45 minutes after the 2026-08-18 22:50 shadow-loop restart."""
        s = FairValueSettlementExit()
        ctx = _ctx()
        d1 = s.evaluate(ctx)
        d2 = s.evaluate(ctx)
        d3 = s.evaluate(ctx)
        assert d1.action == 'ENTER', (d1.reason, d1.features)
        assert d2.action == 'ENTER', (d2.reason, d2.features)
        assert d3.action == 'ENTER', (d3.reason, d3.features)
        assert len(s._open) == 0

    def test_manage_exit_notes_the_open_on_first_sight_of_a_filled_position(self):
        s = FairValueSettlementExit()
        ctx = _ctx()
        d1 = s.evaluate(ctx)
        assert d1.action == 'ENTER', (d1.reason, d1.features)
        pos = _position(window_ts=WINDOW_TS,
                        features={'attempt_number': d1.features['attempt_number']})
        book = _book(UP_TOK, bids=((0.55, 200.0),))
        assert len(s._open) == 0
        s.manage_exit(pos, book, now=WINDOW_TS + 60.0)
        assert len(s._open) == 1
        assert (SLUG, d1.features['attempt_number']) in s._open
        # Idempotent: seeing the SAME still-open position again on a later
        # cycle does not double-count it.
        s.manage_exit(pos, book, now=WINDOW_TS + 90.0)
        assert len(s._open) == 1

    def test_open_key_falls_back_to_position_id_when_attempt_number_missing(self):
        # Should not happen on a real fill (the parent always stamps
        # `attempt_number` on every ENTER, convention 11) - covered as a
        # defensive fallback, not the expected path.
        s = FairValueSettlementExit()
        pos = _position(position_id='pos-fallback', window_ts=WINDOW_TS,
                        features={})
        book = _book(UP_TOK, bids=((0.55, 200.0),))
        s.manage_exit(pos, book, now=WINDOW_TS + 10.0)
        assert (SLUG, 'pos-fallback') in s._open

    def test_max_two_concurrent_then_third_is_capped(self):
        # Realistic overlap for a hold-to-resolution single-window strategy:
        # consecutive 5-minute windows never overlap (one resolves exactly
        # when the next opens), so genuine concurrency here comes from the
        # parent's own `max_trades_per_window` (3 attempts allowed inside
        # ONE window) rather than from distinct windows.
        assert MAX_CONCURRENT_POSITIONS == 2
        s = FairValueSettlementExit()
        ctx = _ctx()
        book = _book(UP_TOK, bids=((0.55, 200.0),))

        d1 = s.evaluate(ctx)
        assert d1.action == 'ENTER', (d1.reason, d1.features)
        pos1 = _position(position_id='pos-1', window_ts=WINDOW_TS,
                         features={'attempt_number': d1.features['attempt_number']})
        s.manage_exit(pos1, book, now=WINDOW_TS + 10.0)

        d2 = s.evaluate(ctx)
        assert d2.action == 'ENTER', (d2.reason, d2.features)
        assert d1.features['attempt_number'] != d2.features['attempt_number']
        pos2 = _position(position_id='pos-2', window_ts=WINDOW_TS,
                         features={'attempt_number': d2.features['attempt_number']})
        s.manage_exit(pos2, book, now=WINDOW_TS + 10.0)

        d3 = s.evaluate(ctx)
        assert d3.action == 'SKIP'
        assert d3.reason == 'strategy_concurrency_cap_reached'
        assert d3.features['open_positions_this_instance'] == 2

    def test_open_positions_prune_once_their_window_has_resolved(self):
        s = FairValueSettlementExit()
        ctx = _ctx()
        book = _book(UP_TOK, bids=((0.55, 200.0),))

        d1 = s.evaluate(ctx)
        d2 = s.evaluate(ctx)
        assert d1.action == 'ENTER' and d2.action == 'ENTER'
        pos1 = _position(position_id='pos-1', window_ts=WINDOW_TS,
                         features={'attempt_number': d1.features['attempt_number']})
        pos2 = _position(position_id='pos-2', window_ts=WINDOW_TS,
                         features={'attempt_number': d2.features['attempt_number']})
        s.manage_exit(pos1, book, now=WINDOW_TS + 10.0)
        s.manage_exit(pos2, book, now=WINDOW_TS + 10.0)
        assert len(s._open) == 2

        # Advance to the NEXT window, past the first window's resolve time
        # (window_ts + 300s). Both positions opened in the first window
        # resolve together, since they share its window_ts. `evaluate()`'s
        # ENTER here does not re-add to `_open` itself (only `manage_exit`
        # does, on a real fill), so the count reflects pruning alone.
        next_ctx = _ctx(window_ts=WINDOW_TS + 300, slug=SLUG,
                        seconds_into_window=1.0)
        d3 = s.evaluate(next_ctx)
        assert d3.action == 'ENTER', (d3.reason, d3.features)
        assert len(s._open) == 0
        assert d3.features['open_positions_this_instance'] == 0


# ============ 6. registry ======================================================

class TestRegistry:

    def test_registry_is_twenty_five_and_this_strategy_is_index_24(self):
        from strategies.polymarket import build_strategies
        names = [st.strategy_name for st in build_strategies()]
        assert len(names) == 25
        assert names[:8] == [
            'PM_streak_snapper', 'PM_mid_price_continuation', 'PM_box_builder',
            'PM_corridor_collector', 'PM_temporal_arbitrage', 'PM_corridor_pair',
            'PM_spread_harvest_taker', 'PM_fair_value_arb',
        ]
        assert names[22] == 'PM_longshot_fade_hold_to_resolution'
        assert names[23] == 'PM_weather_bracket_width_matched'
        assert names[24] == STRATEGY_NAME

    def test_declares_crypto_only_unlike_the_parent(self):
        from strategies.polymarket import FairValueArb, FairValueSettlementExit
        assert MARKET_TYPE_CRYPTO_UPDOWN in FairValueArb.supported_market_types
        assert len(FairValueArb.supported_market_types) > 1
        assert FairValueSettlementExit.supported_market_types == \
            (MARKET_TYPE_CRYPTO_UPDOWN,)
