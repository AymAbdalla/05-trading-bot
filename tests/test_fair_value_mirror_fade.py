"""Tests for `PM_fair_value_mirror_fade` (D-326, the mirror-fade probe).

No network. Every orderbook, window, position and spot here is synthetic.

Five jobs, matching `docs/handoffs/from-raven/2026-08-19-mirror-fade-probe.md`:

  1. **A parent SKIP is a mirror SKIP**, with the parent's own reason intact.
     `TestParentSkipPassesThrough`.
  2. **The flip mechanics**: the side is `opposite(intended)`, re-priced and
     re-gated against its own book, and `mirror_edge` is exactly
     `(1 - side_fair_value) - no_ask`. `TestFlipMechanics`.
  3. **The three new entry gates**: ask cap (0.60), mirror edge (0.05), depth
     (2x the fixed 5-share size). `TestNewEntryGates`.
  4. **Concurrency self-cap and its bookkeeping**: max 2 open per instance,
     recorded from `manage_exit` on first fill sighting, never from
     `evaluate()`. `TestConcurrencyCap`.
  5. **No active exit, ever**: `manage_exit` always HOLDs regardless of how
     far the bid falls. `TestNoActiveExit`.
  6. **Registry**: 26 strategies, this one at index 25, PAUSED via the
     D-322/D-323 sentinel (D-326 amended, D-329) rather than crypto-only,
     first 8 unchanged. `TestRegistry`.

Per D-268 / convention 11: nothing here scores edge. Zero rows exist under
`PM_fair_value_mirror_fade` before this session wires it in.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.halt as halt_mod  # noqa: E402
from engine.polymarket.paper_adapter import PaperPosition  # noqa: E402
from engine.polymarket.types import (Market, Orderbook,  # noqa: E402
                                     Outcome, PriceLevel)
from strategies.polymarket.base import (MARKET_TYPE_CRYPTO_UPDOWN,  # noqa: E402
                                        MarketContext, Window)
from strategies.polymarket.fair_value_mirror_fade import (  # noqa: E402
    DEPTH_MULTIPLE, ENTRY_ASK_CAP, MAX_CONCURRENT_POSITIONS,
    MIRROR_EDGE_THRESHOLD, MIRROR_SHARES, FairValueMirrorFade)

UP_TOK = 'tok-up'
DOWN_TOK = 'tok-down'
WINDOW_TS = 1_700_000_000
SLUG = 'btc-updown-5m-1700000000'
STRATEGY_NAME = 'PM_fair_value_mirror_fade'


@pytest.fixture(autouse=True)
def never_halted(tmp_path, monkeypatch):
    """A real HALT file in the repo would turn every entry test into a
    `halted` skip - working correctly, but indistinguishable here from this
    strategy being broken."""
    monkeypatch.setattr(halt_mod, 'HALT_FILE', str(tmp_path / 'NO_SUCH_HALT'))


# ============ fixtures (same shape as test_fair_value_settlement_exit.py's) ==

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
         seconds_into_window=100.0, up_asks=((0.50, 200.0),),
         down_asks=((0.20, 200.0),), up_bids=((0.48, 200.0),),
         down_bids=((0.18, 200.0),), windows=None, market=True):
    """Default arguments give `fair(Up) ~= 0.71` (same model inputs as
    `test_fair_value_arb_inverse.py`'s baseline), against `ask(Up)=0.50` and
    `ask(Down)=0.20`. Both sides carry real edge on purpose - the parent
    picks Up (raw_edge ~0.21 beats Down's ~0.09) so `intended='Up'`,
    `flipped='Down'`, and the mirror's OWN edge on Down (`fair(Down) -
    ask(Down)` ~= 0.29 - 0.20 = 0.09) clears `MIRROR_EDGE_THRESHOLD` (0.05)
    without the fixture having to fake a mirror-specific gap. A fixture where
    only the parent's side has edge (the family's usual baseline: ask(Up)
    0.60 / ask(Down) 0.42) makes the FLIPPED side's edge strongly negative
    by construction (the two raw edges sum to roughly `1 - overround`) - that
    is expected behaviour, not a bug, and is exercised directly in
    `TestNewEntryGates.test_mirror_edge_below_threshold_on_the_familys_usual_baseline`.
    """
    books = {UP_TOK: _book(UP_TOK, up_asks, up_bids, ts=window_ts),
             DOWN_TOK: _book(DOWN_TOK, down_asks, down_bids, ts=window_ts)}
    return MarketContext(
        window_ts=window_ts,
        windows=_windows(window_ts) if windows is None else windows,
        market=_market(slug) if market else None,
        books=books, spot=spot, strike=None,
        seconds_into_window=seconds_into_window)


def _position(entry=0.20, shares=5.0, opened_ts=WINDOW_TS + 100,
             window_ts=WINDOW_TS, side='Down', token_id=DOWN_TOK,
             strategy=STRATEGY_NAME, position_id='pos-1', features=None):
    return PaperPosition(
        position_id=position_id, strategy=strategy, market_slug=SLUG,
        token_id=token_id, outcome_side=side, shares=shares,
        avg_price=entry, cost_usdc=entry * shares, fee_usdc=0.0,
        opened_ts=opened_ts, window_ts=window_ts, features=features or {})


# ============ 1. a parent SKIP is a mirror SKIP ==============================

class TestParentSkipPassesThrough:

    def test_no_orderbook_skip_passes_through_with_parents_reason(self):
        s = FairValueMirrorFade()
        ctx = _ctx(market=False)
        d = s.evaluate(ctx)
        assert d.action == 'SKIP'
        assert d.reason == 'no_market'
        assert 'mirrored_from' in d.features

    def test_edge_below_parents_own_threshold_skips_with_parents_reason(self):
        # Both sides priced at their fair value: no side clears the parent's
        # own 0.04 edge_threshold, so this never reaches the flip at all.
        s = FairValueMirrorFade()
        ctx = _ctx(up_asks=((0.71, 200.0),), down_asks=((0.29, 200.0),))
        d = s.evaluate(ctx)
        assert d.action == 'SKIP'
        assert d.reason == 'edge_below_threshold'
        assert d.features['flip_applied'] is False


# ============ 2. flip mechanics ==============================================

class TestFlipMechanics:

    def test_parent_picks_up_mirror_takes_down(self):
        s = FairValueMirrorFade()
        d = s.evaluate(_ctx())
        assert d.action == 'ENTER', (d.reason, d.features)
        assert d.features['parent_intended_side'] == 'Up'
        assert d.features['outcome_side'] == 'Down'
        assert d.features['mirror_side_taken'] == 'Down'
        assert d.features['flip_applied'] is True
        assert d.legs[0].outcome_side == 'Down'

    def test_mirror_edge_is_one_minus_side_fair_value_minus_no_ask(self):
        s = FairValueMirrorFade()
        d = s.evaluate(_ctx())
        parent_fair = d.features['parent_side_fair_value']
        mirror_fair = d.features['mirror_side_fair_value']
        assert mirror_fair == pytest.approx(1.0 - parent_fair, abs=1e-6)
        expected_edge = round(mirror_fair - d.features['mirror_best_ask'], 4)
        assert d.features['mirror_edge'] == expected_edge

    def test_shares_are_fixed_not_notional_scaled(self):
        s = FairValueMirrorFade()
        d = s.evaluate(_ctx())
        assert d.action == 'ENTER'
        assert d.legs[0].shares == MIRROR_SHARES == 5
        assert d.features['shares'] == 5

    def test_when_parent_picks_down_mirror_takes_up(self):
        s = FairValueMirrorFade()
        # `fair(Up) ~= 0.71` regardless of the asks passed (the model reads
        # spot/windows, not the book), so making the parent pick Down needs
        # Down's raw edge (`fair(Down) - ask(Down)` ~= `0.29 - ask`) to beat
        # Up's (`0.71 - ask`), while keeping `ask(Up) <= ENTRY_ASK_CAP` so
        # the MIRROR leg (which buys Up here) still clears its own cap.
        ctx = _ctx(up_asks=((0.60, 200.0),), down_asks=((0.05, 200.0),))
        d = s.evaluate(ctx)
        assert d.action == 'ENTER', (d.reason, d.features)
        assert d.features['parent_intended_side'] == 'Down'
        assert d.features['outcome_side'] == 'Up'


# ============ 3. the three new entry gates ===================================

class TestNewEntryGates:

    def test_mirror_entry_ask_above_cap(self):
        s = FairValueMirrorFade()
        ctx = _ctx(down_asks=((0.65, 200.0),))
        d = s.evaluate(ctx)
        assert d.action == 'SKIP'
        assert d.reason == 'mirror_entry_ask_above_cap'
        assert d.features['mirror_attempt_consumed_on_skip'] is True

    def test_mirror_entry_ask_at_cap_exactly_passes(self):
        s = FairValueMirrorFade()
        ctx = _ctx(down_asks=((ENTRY_ASK_CAP, 200.0),))
        d = s.evaluate(ctx)
        # fair(Down) ~= 0.29 vs ask 0.60 is a large negative edge, so this
        # still fails - but on the EDGE gate, not the cap, proving the cap
        # itself is inclusive at the boundary.
        assert d.reason != 'mirror_entry_ask_above_cap'

    def test_mirror_edge_below_threshold_on_the_familys_usual_baseline(self):
        # The family's usual baseline (ask(Up)=0.60, ask(Down)=0.42) is where
        # the parent's own tests and 034's tests both start from. The
        # flipped side's edge here is strongly negative by construction -
        # see the module docstring's arithmetic (the two raw edges sum to
        # roughly `1 - overround`).
        s = FairValueMirrorFade()
        ctx = _ctx(up_asks=((0.60, 200.0),), down_asks=((0.42, 200.0),))
        d = s.evaluate(ctx)
        assert d.action == 'SKIP'
        assert d.reason == 'mirror_edge_below_threshold'
        assert d.features['mirror_edge'] < MIRROR_EDGE_THRESHOLD

    def test_mirror_insufficient_book_depth(self):
        s = FairValueMirrorFade()
        # Top-of-book depth (5 shares) is below 2x the fixed 5-share size.
        ctx = _ctx(down_asks=((0.20, 5.0),))
        d = s.evaluate(ctx)
        assert d.action == 'SKIP'
        assert d.reason == 'mirror_insufficient_book_depth'
        assert d.features['mirror_min_depth_shares'] == \
            MIRROR_SHARES * DEPTH_MULTIPLE == 10.0


# ============ 4. concurrency self-cap ========================================

class TestConcurrencyCap:

    def test_two_fills_trip_the_cap_a_third_attempt_skips(self):
        s = FairValueMirrorFade()
        ctx = _ctx()
        book = ctx.book('Down')

        d1 = s.evaluate(ctx)
        assert d1.action == 'ENTER', (d1.reason, d1.features)
        pos1 = _position(position_id='pos-1',
                         features={'attempt_number': d1.features['attempt_number']})
        s.manage_exit(pos1, book, now=WINDOW_TS + 10.0)
        assert len(s._open) == 1

        d2 = s.evaluate(ctx)
        assert d2.action == 'ENTER', (d2.reason, d2.features)
        pos2 = _position(position_id='pos-2',
                         features={'attempt_number': d2.features['attempt_number']})
        s.manage_exit(pos2, book, now=WINDOW_TS + 10.0)
        assert len(s._open) == 2 == MAX_CONCURRENT_POSITIONS

        d3 = s.evaluate(ctx)
        assert d3.action == 'SKIP'
        assert d3.reason == 'strategy_concurrency_cap_reached'
        assert d3.features['open_count'] == 2

    def test_evaluate_alone_never_adds_to_open_only_manage_exit_does(self):
        """The self-starvation bug 034 and 032 were both fixed for: a burst
        of ENTER decisions that never actually filled must not trip the cap.
        Capped at 3 calls - the parent's own inherited `max_trades_per_window`
        (unchanged here) throttles a 4th ENTER in the same window regardless
        of this file's own concurrency cap, which is the fact under test."""
        s = FairValueMirrorFade()
        ctx = _ctx()
        for _ in range(3):
            d = s.evaluate(ctx)
            assert d.action == 'ENTER', (d.reason, d.features)
        assert len(s._open) == 0

    def test_pruning_frees_slots_once_the_window_resolves(self):
        s = FairValueMirrorFade()
        ctx = _ctx()
        book = ctx.book('Down')
        d1 = s.evaluate(ctx)
        pos1 = _position(position_id='pos-1',
                         features={'attempt_number': d1.features['attempt_number']})
        s.manage_exit(pos1, book, now=WINDOW_TS + 10.0)
        assert len(s._open) == 1

        next_ctx = _ctx(window_ts=WINDOW_TS + 300, seconds_into_window=1.0)
        d2 = s.evaluate(next_ctx)
        assert d2.action == 'ENTER', (d2.reason, d2.features)
        assert len(s._open) == 0
        assert d2.features['open_positions_this_instance'] == 0


# ============ 5. no active exit, ever ========================================

class TestNoActiveExit:

    @pytest.mark.parametrize('bid', [0.15, 0.10, 0.05, 0.01, 0.00])
    def test_manage_exit_never_returns_exit_at_any_bid_level(self, bid):
        s = FairValueMirrorFade()
        pos = _position(entry=0.20, shares=5.0)
        book = _book(DOWN_TOK, asks=((0.22, 200.0),), bids=((bid, 200.0),))
        d = s.manage_exit(pos, book, now=WINDOW_TS + 200.0)
        assert d.action == 'HOLD'
        assert d.reason == 'holding_to_resolution'
        assert d.features['stop_price'] == 0.00
        assert d.features['no_active_exit_by_design'] is True

    def test_manage_exit_holds_even_with_no_orderbook(self):
        s = FairValueMirrorFade()
        pos = _position()
        d = s.manage_exit(pos, None, now=WINDOW_TS + 200.0)
        assert d.action == 'HOLD'

    def test_manage_exit_still_notes_the_open_position_for_the_cap(self):
        s = FairValueMirrorFade()
        pos = _position()
        s.manage_exit(pos, None, now=WINDOW_TS + 200.0)
        assert len(s._open) == 1


# ============ 6. registry ====================================================

class TestRegistry:

    def test_registry_is_twenty_six_and_this_strategy_is_index_25(self):
        from strategies.polymarket import build_strategies
        names = [st.strategy_name for st in build_strategies()]
        assert len(names) == 26
        assert names[:8] == [
            'PM_streak_snapper', 'PM_mid_price_continuation', 'PM_box_builder',
            'PM_corridor_collector', 'PM_temporal_arbitrage', 'PM_corridor_pair',
            'PM_spread_harvest_taker', 'PM_fair_value_arb',
        ]
        assert names[24] == 'PM_fair_value_settlement_exit'
        assert names[25] == STRATEGY_NAME

    def test_declares_paused_sentinel_not_crypto(self):
        """D-326 amended (D-329): the taker-only kill bar is t>=2.0 on
        n>=250; today is t=1.19 on n=116, below it. Paused via the D-322/
        D-323 sentinel mechanism - never routed, still construction-valid
        and still in the registry (see the two tests below)."""
        from strategies.polymarket import FairValueArb
        assert MARKET_TYPE_CRYPTO_UPDOWN in FairValueArb.supported_market_types
        assert FairValueMirrorFade.supported_market_types == ('smart_money',)
        assert MARKET_TYPE_CRYPTO_UPDOWN not in \
            FairValueMirrorFade.supported_market_types

    def test_evaluate_still_works_directly_despite_the_pause(self):
        """The pause blocks ROUTING (`_supporting()` in `shadow_loop.py`),
        not `evaluate()` itself - the same shape D-322/D-323 leave every
        paused strategy in. Every test in this file calls `evaluate()`
        directly and must keep passing; this pins that the class still
        does the flip on a crypto-shaped `MarketContext` even though it
        would never receive one from the live loop while paused."""
        s = FairValueMirrorFade()
        d = s.evaluate(_ctx())
        assert d.action == 'ENTER', (d.reason, d.features)
        assert d.features['flip_applied'] is True

    def test_fresh_instances_do_not_share_open_state(self):
        a = FairValueMirrorFade()
        b = FairValueMirrorFade()
        a.evaluate(_ctx())
        pos = _position()
        a.manage_exit(pos, _ctx().book('Down'), now=WINDOW_TS + 10.0)
        assert len(a._open) == 1
        assert len(b._open) == 0
