"""Tests for the three Polymarket strategies added after the original four.

Same two jobs as tests/test_polymarket_strategies.py:
  1. no strategy may ever raise, whatever garbage it is handed
  2. every strategy must be provably ALIVE - a synthetic context that satisfies
     its rules produces an entry, and a one-condition-off context does not

Job 2 matters most on the price caps, because on a binary the entry price IS
half the edge and a cap that silently moved looks identical in the graveyard to
a strategy that was honestly measured and failed. So there is a test per cap:
0.35 and 0.49 and the 0.94 pair ceiling (temporal), 1.41 and the BINNED fair
value (cross-window), 0.40-0.48 and the 1.10 wide-book gate (spread harvest).

Three things here are not cap tests and matter as much:

  - `test_corridor_pair_live_refuses_a_window_that_is_not_the_final_third` guards the
    only reason the pair has a $1.00 floor at all. Pair the 15m leader with a
    5m window that does NOT settle off the same close and both legs can lose,
    and nothing in the pricing would say so.
  - `test_temporal_marks_an_unpaired_block_and_keeps_saying_so` guards the leg
    that resolves to 0.00. A version that stopped logging after the deadline
    would report only completed pairs, which is the fiction proposal 002 spends
    a section warning about.
  - `test_spread_harvest_reports_which_coin_flip_gate_it_used` guards against
    pooling two different gates into one population.

There is deliberately NO harness sweep here. Per D-268 these are NOT_TESTED
until a resolution-PnL harness exists; running the price-path harness on them
would fabricate numbers.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.types import (Market, Orderbook, Outcome,  # noqa: E402
                                     PriceLevel)
from strategies.polymarket import (CorridorPairLive,  # noqa: E402
                                   SpreadHarvestMaker, TemporalArbitrage,
                                   build_strategies)
from strategies.polymarket.base import MarketContext, Window  # noqa: E402
import strategies.polymarket.corridor_pair_live as cpl_mod  # noqa: E402
import strategies.polymarket.spread_harvest_maker as spread_mod  # noqa: E402
import strategies.polymarket.temporal_arbitrage as temporal_mod  # noqa: E402

NEW_MODULES = (temporal_mod, cpl_mod, spread_mod)

# A 5m window that is also the FINAL THIRD of its 15m parent, which
# corridor_pair_live requires and which is a third of all windows.
#   1699999800 % 300 == 0   and   1699999800 % 900 == 600
WINDOW_TS = 1699999800
TS15 = 1699999200
OPEN_PX = 60000.0


def _bps(base: float, bps: float) -> float:
    """Spot that sits `bps` basis points above `base`."""
    return base * (1.0 + bps / 10_000.0)


def _book(token, asks=(), bids=()):
    """asks/bids as (price, size) tuples. Orderbook sorts nothing itself."""
    return Orderbook(
        token_id=token,
        asks=tuple(PriceLevel(p, s) for p, s in sorted(asks)),
        bids=tuple(PriceLevel(p, s) for p, s in sorted(bids, reverse=True)),
    )


def _market(slug, up_token, down_token):
    return Market(
        id=slug, question=slug, slug=slug, condition_id='c-' + slug,
        outcomes=(Outcome('Up', up_token), Outcome('Down', down_token)),
    )


def _windows(n=16, current_open=OPEN_PX, include_ts=WINDOW_TS):
    """`n` contiguous 5m bars ending on the bar that opened at `include_ts`.

    The last bar is the IN-PROGRESS window, which is what the shadow loop
    actually supplies: `price_windows_checked` does not drop it. Both new
    reference-price lookups match on TIMESTAMP rather than taking the last bar,
    so the tests build the whole run and let them find their own.
    """
    out = []
    for i in range(n - 1, -1, -1):
        ts = include_ts - 300 * i
        out.append(Window(ts=ts, open=current_open, close=current_open,
                          direction='UP', source='price'))
    return out


# ---------------------------------------------------------------------------
# temporal_arbitrage fixtures
# ---------------------------------------------------------------------------

def _temporal_ctx(down_ask=0.30, up_ask=0.60, move_bps=20.0, atr14=10.0,
                  seconds_into_window=30.0, depth=50, windows=None):
    m5 = _market('btc-updown-5m-{}'.format(WINDOW_TS), 'UP', 'DN')
    return MarketContext(
        window_ts=WINDOW_TS,
        windows=_windows() if windows is None else windows,
        market=m5,
        books={'UP': _book('UP', asks=((up_ask, depth),)),
               'DN': _book('DN', asks=((down_ask, depth),))},
        spot=_bps(OPEN_PX, move_bps),
        strike=None,
        seconds_into_window=seconds_into_window,
        atr14=atr14,
    )


# ============ 0. house rules ============

def test_paper_mode_true_in_every_new_module_and_class():
    for mod in NEW_MODULES:
        assert mod.PAPER_MODE is True, mod.__name__
    for strategy in (TemporalArbitrage(), CorridorPairLive(),
                     SpreadHarvestMaker()):
        assert strategy.paper_mode is True, strategy.name


def test_every_new_module_states_a_kill_condition():
    """Convention 6: a proposal without a kill condition is a hope."""
    for mod in NEW_MODULES:
        assert 'KILL CONDITION' in (mod.__doc__ or ''), mod.__name__


def test_build_strategies_returns_eight_independent_instances():
    """Three of these carry per-window state, so a shared instance would share a
    block ledger between callers. FairValueArb additionally carries a BTC price
    tape, and two loops feeding one tape would interleave their observations."""
    first, second = build_strategies(), build_strategies()
    assert len(first) == 8
    names = [s.strategy_name for s in first]
    assert len(set(names)) == 8, names
    assert 'PM_corridor_pair' in names
    assert 'PM_fair_value_arb' in names
    for a, b in zip(first, second):
        assert a is not b


def test_the_taker_adaptation_is_not_named_as_his_maker_strategy():
    """A shared key would let one be quoted as evidence about the other."""
    strategy = SpreadHarvestMaker()
    assert strategy.strategy_name == 'PM_spread_harvest_taker'
    assert strategy.uses_maker_orders is False


def test_new_strategies_never_raise_and_always_give_a_reason():
    empty = MarketContext(window_ts=0)
    for strategy in (TemporalArbitrage(), CorridorPairLive(),
                     SpreadHarvestMaker()):
        decision = strategy.evaluate(empty)
        assert decision.action == 'SKIP'
        assert decision.reason, strategy.name


# ============ 1. temporal_arbitrage ============

def test_temporal_buys_the_cheap_side_after_btc_runs():
    decision = TemporalArbitrage().evaluate(_temporal_ctx())
    assert decision.action == 'ENTER', decision.reason
    leg = decision.primary_leg
    assert leg.outcome_side == 'Down'        # BTC ran UP, so Down got cheap
    assert leg.limit_price == 0.35
    assert leg.expected_price == 0.30
    assert leg.shares == 5
    assert decision.features['leg'] == 1
    assert decision.features['naked_until_leg2'] is True
    assert decision.features['stretch_ratio'] == 2.0


def test_temporal_completes_the_pair_on_the_reversal_then_stops():
    strategy = TemporalArbitrage()
    assert strategy.evaluate(_temporal_ctx()).action == 'ENTER'

    # BTC back on the open: no longer stretched, and Up is now cheap.
    reversal = _temporal_ctx(up_ask=0.45, move_bps=0.0,
                             seconds_into_window=200.0)
    second = strategy.evaluate(reversal)
    assert second.action == 'ENTER', second.reason
    assert second.primary_leg.outcome_side == 'Up'
    assert second.features['leg'] == 2
    assert second.features['pair_cost'] == 0.75
    assert second.features['gross_profit_per_pair'] == 0.25

    third = strategy.evaluate(reversal)
    assert third.action == 'SKIP'
    assert third.reason == 'pair_complete'
    assert third.features['pair_cost'] == 0.75


def test_temporal_waits_while_btc_is_still_running():
    strategy = TemporalArbitrage()
    strategy.evaluate(_temporal_ctx())
    still_running = _temporal_ctx(up_ask=0.45, move_bps=40.0,
                                  seconds_into_window=120.0)
    decision = strategy.evaluate(still_running)
    assert decision.action == 'SKIP'
    assert decision.reason == 'no_reversal_yet'


def test_temporal_marks_an_unpaired_block_and_keeps_saying_so():
    """The leg that resolves to 0.00 must stay visible for the whole window."""
    strategy = TemporalArbitrage()
    strategy.evaluate(_temporal_ctx())

    # T-40: past the leg-2 cutoff with leg 1 outstanding.
    late = _temporal_ctx(up_ask=0.45, move_bps=0.0, seconds_into_window=260.0)
    first = strategy.evaluate(late)
    assert first.action == 'SKIP'
    assert first.reason == 'leg2_deadline_passed_unpaired'
    assert first.features['unpaired_leg_side'] == 'Down'
    assert first.features['unpaired_max_loss_usdc'] == 1.5   # 0.30 x 5 shares

    repeat = strategy.evaluate(late)
    assert repeat.reason == 'unpaired_leg_held_to_resolution'


def test_temporal_never_claims_a_confirmed_fill():
    """`evaluate` sees decisions, not fills. Every row has to say so, or a
    completion rate gets computed off ENTER counts and is wrong."""
    strategy = TemporalArbitrage()
    for ctx in (_temporal_ctx(), _temporal_ctx(down_ask=0.80)):
        feats = strategy.evaluate(ctx).features
        assert feats['leg1_fill_confirmed'] is False
        assert feats['completion_rate_measurable_from_this_log'] is False


def test_temporal_leg1_cap_is_035_and_is_named_separately_from_depth():
    at_cap = TemporalArbitrage().evaluate(_temporal_ctx(down_ask=0.35))
    assert at_cap.action == 'ENTER', at_cap.reason

    over = TemporalArbitrage().evaluate(_temporal_ctx(down_ask=0.36))
    assert over.action == 'SKIP'
    assert over.reason == 'leg1_ask_above_cap'

    thin = TemporalArbitrage().evaluate(_temporal_ctx(down_ask=0.30, depth=2))
    assert thin.reason == 'insufficient_leg1_depth'


def test_temporal_leg2_cap_is_the_tighter_of_049_and_the_pair_ceiling():
    strategy = TemporalArbitrage()
    strategy.evaluate(_temporal_ctx())
    reversal = _temporal_ctx(up_ask=0.50, move_bps=0.0,
                             seconds_into_window=200.0)
    decision = strategy.evaluate(reversal)
    assert decision.action == 'SKIP'
    assert decision.reason == 'leg2_ask_above_cap'
    assert decision.features['leg2_cap'] == 0.49
    assert decision.features['leg2_cap_binding'] == 'leg2_ask_cap'


def test_temporal_pair_ceiling_binds_when_leg1_was_expensive():
    """With a loosened leg-1 cap the 0.94 pair ceiling is what stops leg 2."""
    strategy = TemporalArbitrage(leg1_ask_cap=0.60, leg2_ask_cap=0.90)
    first = strategy.evaluate(_temporal_ctx(down_ask=0.55))
    assert first.action == 'ENTER', first.reason

    reversal = _temporal_ctx(up_ask=0.45, move_bps=0.0,
                             seconds_into_window=200.0)
    second = strategy.evaluate(reversal)
    assert second.action == 'SKIP'
    assert second.reason == 'leg2_ask_above_cap'
    assert second.features['leg2_cap'] == 0.39      # 0.94 - 0.55
    assert second.features['leg2_cap_binding'] == 'pair_cost_cap'


def test_temporal_needs_a_move_worth_the_name():
    quiet = TemporalArbitrage().evaluate(_temporal_ctx(move_bps=2.0))
    assert quiet.reason == 'not_stretched'
    assert quiet.features['stretch_ratio'] == 0.2


def test_temporal_will_not_open_a_leg_it_cannot_pair():
    late = TemporalArbitrage().evaluate(
        _temporal_ctx(seconds_into_window=200.0))
    assert late.reason == 'too_late_for_leg1'


def test_temporal_refuses_a_window_whose_open_it_cannot_find():
    """A stale candle pull must not silently measure from another window."""
    stale = _temporal_ctx(windows=_windows(include_ts=WINDOW_TS - 300))
    decision = TemporalArbitrage().evaluate(stale)
    assert decision.reason == 'no_window_open'


def test_temporal_asymmetric_mode_declines_the_mirror_trade():
    down_move = _temporal_ctx(up_ask=0.30, down_ask=0.60, move_bps=-20.0)
    assert TemporalArbitrage().evaluate(down_move).action == 'ENTER'
    restricted = TemporalArbitrage(symmetric=False).evaluate(down_move)
    assert restricted.reason == 'symmetric_disabled'


# ---------------------------------------------------------------------------
# corridor_pair_live fixtures
# ---------------------------------------------------------------------------

def _cpl_ctx(ask_15=0.85, ask_5=0.47, lead_bps=12.0, seconds_into_window=30.0,
              depth=50, window_ts=WINDOW_TS, windows=None):
    """Default asks clear the D-281 8c floor: at a 12bps lead the binned fair
    pair is 1.405 and 0.85 + 0.47 = 1.32 is 8.5c below it. The old 0.90/0.48
    defaults were 2.5c below fair, which cleared D-277's 2c floor and does not
    clear this one."""
    m5 = _market('btc-updown-5m-{}'.format(window_ts), 'UP5', 'DN5')
    m15 = _market('btc-updown-15m-{}'.format(TS15), 'UP15', 'DN15')
    return MarketContext(
        window_ts=window_ts,
        windows=_windows(include_ts=WINDOW_TS) if windows is None else windows,
        market=m5,
        books={'UP5': _book('UP5', asks=((0.60, depth),)),
               'DN5': _book('DN5', asks=((ask_5, depth),))},
        spot=_bps(OPEN_PX, lead_bps),
        strike=None,
        seconds_into_window=seconds_into_window,
        market_15m=m15,
        books_15m={'UP15': _book('UP15', asks=((ask_15, depth),)),
                   'DN15': _book('DN15', asks=((0.60, depth),))},
    )


# ============ 2. corridor_pair_live ============

def test_corridor_pair_live_buys_the_15m_leader_and_the_5m_opposite():
    decision = CorridorPairLive().evaluate(_cpl_ctx())
    assert decision.action == 'ENTER', decision.reason
    assert len(decision.legs) == 2
    lead, opp = decision.legs
    assert lead.outcome_side == 'Up'            # BTC is above the 15m open
    assert lead.market_slug.startswith('btc-updown-15m-')
    assert round(lead.expected_price, 4) == 0.85
    assert opp.outcome_side == 'Down'
    assert opp.market_slug.startswith('btc-updown-5m-')
    assert round(opp.expected_price, 4) == 0.47
    feats = decision.features
    assert feats['lead_bps'] == 12.0
    assert feats['p_corridor'] == 0.405          # the 10-15bps bin
    assert feats['pair_cost'] == 1.32
    assert feats['payoff_floor'] == 1.00
    assert feats['worst_case_pnl_per_pair'] == -0.32
    assert feats['best_case_pnl_per_pair'] == 0.68
    assert feats['pair_cap_binding'] is None


def test_corridor_pair_live_refuses_a_window_that_is_not_the_final_third():
    """The $1.00 floor exists ONLY because both legs settle off the same close.

    Pair the 15m leader with an earlier third and both legs can lose, and
    nothing in the pricing would tell you.
    """
    first_third = _cpl_ctx(window_ts=TS15)
    decision = CorridorPairLive().evaluate(first_third)
    assert decision.action == 'SKIP'
    assert decision.reason == 'not_final_third_of_15m'
    assert decision.features['offset_sec'] == 0
    assert decision.features['required_offset_sec'] == 600


def test_corridor_pair_live_says_it_is_not_proposal_005s_hypothesis():
    """Proposal 005 is a ONE-leg mispricing bet with no floor. This is the
    floored pair it names as its nearest neighbour, and the two must never be
    pooled."""
    feats = CorridorPairLive().evaluate(_cpl_ctx()).features
    assert feats['implements_proposal_005_hypothesis'] is False
    assert feats['structure'] == 'floored_pair_not_relative_value'


def test_corridor_pair_live_141_ceiling_holds():
    over = CorridorPairLive().evaluate(
        _cpl_ctx(ask_15=0.93, ask_5=0.49, lead_bps=25.0))
    assert over.action == 'SKIP'
    assert over.reason == 'pair_cost_above_cap'
    assert over.features['pair_cost'] == 1.42
    assert over.features['pair_cap_binding'] == 'max_pair_cost'


def test_corridor_pair_live_also_refuses_a_pair_above_the_BINNED_fair_value():
    """1.41 is 1 + the BLENDED 0.413. The binned table reads 0.326 at a 6bps
    lead, so a 1.38 pair is inside the brief's ceiling and 5.4c above what the
    structure is worth. Entering there is negative expectancy following a rule.
    """
    ctx = _cpl_ctx(ask_15=0.90, ask_5=0.48, lead_bps=6.0)
    blocked = CorridorPairLive().evaluate(ctx)
    assert blocked.action == 'SKIP'
    assert blocked.reason == 'pair_cost_above_binned_fair'
    assert blocked.features['p_corridor'] == 0.326
    assert blocked.features['binned_fair_pair_value'] == 1.326
    assert blocked.features['pair_cost'] == 1.38
    assert blocked.features['pair_cap_binding'] == 'binned_fair_pair_value'

    # The sensitivity switch, and the reason it is not a mode to trade in.
    loosened = CorridorPairLive(
        require_binned_fair=False).evaluate(ctx)
    assert loosened.action == 'ENTER'
    assert loosened.features['edge_vs_binned_fair'] == -0.054


def test_corridor_pair_live_needs_8c_of_edge_below_binned_fair():
    """D-281. The gate above catches a pair priced ABOVE fair value. This one
    catches a pair priced below it but not by enough to survive fees.

    At a 12bps lead the binned fair pair is 1.405, so the floor sits at 1.325.
    Exactly 8c must be allowed - the boundary is where a threshold silently
    moves, and comparing against the unrounded 1.405 - 0.08 would refuse it on
    float dust alone.
    """
    at_7c = CorridorPairLive().evaluate(_cpl_ctx(ask_15=0.86, ask_5=0.475))
    assert at_7c.action == 'SKIP'
    assert at_7c.reason == 'edge_below_floor'
    assert at_7c.features['pair_cost'] == 1.335
    assert at_7c.features['binned_fair_pair_value'] == 1.405
    assert at_7c.features['edge_vs_binned_fair'] == 0.07
    assert at_7c.features['pair_cap_binding'] == 'edge_floor_8c'

    at_8c = CorridorPairLive().evaluate(_cpl_ctx(ask_15=0.85, ask_5=0.475))
    assert at_8c.action == 'ENTER', at_8c.reason
    assert at_8c.features['pair_cost'] == 1.325
    assert at_8c.features['edge_vs_binned_fair'] == 0.08
    assert at_8c.features['max_pair_cost_binned'] == 1.325
    assert at_8c.features['pair_cap_binding'] is None


def test_corridor_pair_live_edge_floor_is_the_8c_ruled_in_d281_not_d277s_2c():
    """The floor is a number in a ruling, and a number that drifts back to 2c
    would look identical in the graveyard to a structure that cleared fair
    value more often (convention 17)."""
    assert cpl_mod.MIN_EDGE_VS_BINNED_FAIR == 0.08
    assert CorridorPairLive().min_edge_vs_binned_fair == 0.08
    # 3c cleared the old floor and must not clear this one.
    at_3c = CorridorPairLive().evaluate(_cpl_ctx(ask_15=0.90, ask_5=0.475))
    assert at_3c.reason == 'edge_below_floor'
    assert at_3c.features['edge_vs_binned_fair'] == 0.03


def test_corridor_pair_live_lead_zone_is_5_to_30_bps():
    small = CorridorPairLive().evaluate(_cpl_ctx(lead_bps=3.0))
    assert small.reason == 'lead_below_zone'
    big = CorridorPairLive().evaluate(_cpl_ctx(lead_bps=45.0))
    assert big.reason == 'lead_above_zone'


def test_corridor_pair_live_5m_leg_cap_is_50c():
    decision = CorridorPairLive().evaluate(_cpl_ctx(ask_5=0.51))
    assert decision.reason == 'ask_5m_above_cap'


def test_corridor_pair_live_needs_depth_on_both_legs_before_committing_to_either():
    thin = CorridorPairLive().evaluate(_cpl_ctx(depth=2))
    assert thin.reason == 'insufficient_depth_for_pair'


def test_corridor_pair_live_refuses_when_the_15m_open_bar_is_missing():
    short_run = _windows(n=2, include_ts=WINDOW_TS)   # no bar at TS15
    decision = CorridorPairLive().evaluate(_cpl_ctx(windows=short_run))
    assert decision.reason == 'no_15m_window_open'


def test_corridor_pair_live_will_not_enter_late_in_the_final_third():
    late = CorridorPairLive().evaluate(
        _cpl_ctx(seconds_into_window=120.0))
    assert late.reason == 'late_in_window'


# ---------------------------------------------------------------------------
# spread_harvest fixtures
# ---------------------------------------------------------------------------

def _spread_ctx(up_ask=0.64, down_ask=0.46, up_bid=0.48, down_bid=0.34,
                seconds_into_window=180.0, depth=50, spot=None, strike=None,
                atr14=None):
    m5 = _market('btc-updown-5m-{}'.format(WINDOW_TS), 'UP', 'DN')
    return MarketContext(
        window_ts=WINDOW_TS,
        windows=_windows(),
        market=m5,
        books={'UP': _book('UP', asks=((up_ask, depth),),
                           bids=((up_bid, depth),)),
               'DN': _book('DN', asks=((down_ask, depth),),
                           bids=((down_bid, depth),))},
        spot=spot,
        strike=strike,
        seconds_into_window=seconds_into_window,
        atr14=atr14,
    )


def _book_implied_harvest(**kw):
    """D-282 ships the strategy with the book-implied gate OFF, so every test of
    that gate now has to opt in by name.

    Deliberately not a default-argument change: the whole point of the ruling is
    that `SpreadHarvestMaker()` fires on nothing without a strike feed, and a
    test suite that reached the book-implied path by default would keep passing
    if the default silently flipped back.
    """
    return SpreadHarvestMaker(allow_book_implied_coin_flip=True, **kw)


# ============ 3. spread_harvest (taker adaptation) ============

def test_spread_harvest_buys_the_underdog_in_a_wide_book():
    decision = _book_implied_harvest().evaluate(_spread_ctx())
    assert decision.action == 'ENTER', decision.reason
    leg = decision.primary_leg
    assert leg.outcome_side == 'Down'          # lower midpoint = the underdog
    assert leg.order_type == 'taker'
    assert leg.limit_price == 0.48
    feats = decision.features
    assert feats['effective_ask'] == 0.46
    assert round(leg.expected_price, 4) == 0.46
    assert feats['ask_sum'] == 1.10
    assert feats['edge_if_true_coin_flip'] == 0.04
    assert feats['is_moondev_maker_strategy'] is False


def test_spread_harvest_picks_the_underdog_by_midpoint_not_by_ask():
    """On a wide book the underdog's ASK can sit above the favourite's - his own
    log has dog asks at 0.60-0.68 in near-ties - so 'cheaper ask' is not 'less
    likely side'."""
    ctx = _spread_ctx(up_ask=0.46, down_ask=0.64, up_bid=0.34, down_bid=0.48)
    decision = _book_implied_harvest().evaluate(ctx)
    assert decision.features['dog_side'] == 'Up'       # mid 0.40 vs 0.56
    assert decision.action == 'ENTER', decision.reason
    assert decision.primary_leg.outcome_side == 'Up'


def test_spread_harvest_reports_which_coin_flip_gate_it_used():
    """Two different gates. Their results must never be pooled."""
    book_implied = _book_implied_harvest().evaluate(_spread_ctx())
    assert book_implied.features['coin_flip_source'] == 'book_implied'
    assert book_implied.features['coa'] is None
    assert book_implied.features['book_implied_p_dog'] == 0.4167

    with_strike = SpreadHarvestMaker().evaluate(
        _spread_ctx(spot=_bps(OPEN_PX, 1.0), strike=OPEN_PX, atr14=10.0))
    assert with_strike.features['coin_flip_source'] == 'cushion_atr'
    assert with_strike.features['coa'] == 0.1
    assert with_strike.action == 'ENTER', with_strike.reason


def test_spread_harvest_applies_his_coa_gate_when_a_strike_exists():
    off_flip = SpreadHarvestMaker().evaluate(
        _spread_ctx(spot=_bps(OPEN_PX, 10.0), strike=OPEN_PX, atr14=10.0))
    assert off_flip.action == 'SKIP'
    assert off_flip.reason == 'not_a_coin_flip'
    assert off_flip.features['coa'] == 1.0


def test_spread_harvest_can_be_told_to_refuse_the_book_implied_gate():
    strict = SpreadHarvestMaker(allow_book_implied_coin_flip=False)
    decision = strict.evaluate(_spread_ctx())
    assert decision.reason == 'no_cushion_data'
    assert decision.features['coin_flip_source'] == 'unavailable'


def test_spread_harvest_ships_with_the_book_implied_gate_OFF():
    """D-282. The DEFAULT refuses to trade without a real strike feed.

    This is the ruling, not a convenience: with no strike published by Gamma
    the strategy fires on nothing, which is the honest NOT_TESTED state
    (convention 11). A default that flipped back to True would produce a
    graveyard full of rows that read as a measurement of the ported strategy
    and are a measurement of a different gate.
    """
    shipped = SpreadHarvestMaker()
    assert shipped.allow_book_implied_coin_flip is False

    # A context that would ENTER on the book-implied gate, and does not.
    ctx = _spread_ctx()
    assert _book_implied_harvest().evaluate(ctx).action == 'ENTER'
    decision = shipped.evaluate(ctx)
    assert decision.action == 'SKIP'
    assert decision.reason == 'no_cushion_data'
    assert decision.features['coin_flip_source'] == 'unavailable'
    assert decision.features['coa'] is None

    # The strike path is untouched: the gate is disabled, not removed.
    with_strike = shipped.evaluate(
        _spread_ctx(spot=_bps(OPEN_PX, 1.0), strike=OPEN_PX, atr14=10.0))
    assert with_strike.action == 'ENTER', with_strike.reason
    assert with_strike.features['coin_flip_source'] == 'cushion_atr'


def test_spread_harvest_needs_a_wide_book():
    tight = SpreadHarvestMaker().evaluate(_spread_ctx(up_ask=0.55,
                                                      down_ask=0.46))
    assert tight.reason == 'book_not_wide_enough'
    assert tight.features['ask_sum'] == 1.01


def test_spread_harvest_band_is_040_to_048_on_the_effective_ask():
    over = _book_implied_harvest().evaluate(_spread_ctx(up_ask=0.60,
                                                        down_ask=0.55))
    assert over.reason == 'ask_above_band'

    under = _book_implied_harvest().evaluate(
        _spread_ctx(up_ask=0.80, down_ask=0.35, up_bid=0.60, down_bid=0.10))
    assert under.reason == 'effective_ask_below_band'

    # Two levels: 0.46 for 4 shares then 0.52, so the full block averages above
    # the band even though top-of-book is inside it.
    m5 = _market('btc-updown-5m-{}'.format(WINDOW_TS), 'UP', 'DN')
    laddered = MarketContext(
        window_ts=WINDOW_TS, windows=_windows(), market=m5,
        books={'UP': _book('UP', asks=((0.64, 50),), bids=((0.48, 50),)),
               'DN': _book('DN', asks=((0.46, 4), (0.52, 50)),
                           bids=((0.34, 50),))},
        seconds_into_window=180.0)
    decision = _book_implied_harvest().evaluate(laddered)
    assert decision.reason == 'insufficient_ask_depth'
    assert decision.features['dog_depth_at_band_high'] == 4


def test_spread_harvest_honours_his_time_band():
    early = SpreadHarvestMaker().evaluate(_spread_ctx(seconds_into_window=30.0))
    assert early.reason == 'out_of_time_band'
    late = SpreadHarvestMaker().evaluate(_spread_ctx(seconds_into_window=290.0))
    assert late.reason == 'out_of_time_band'


def test_spread_harvest_enters_once_per_window():
    strategy = _book_implied_harvest()
    assert strategy.evaluate(_spread_ctx()).action == 'ENTER'
    again = strategy.evaluate(_spread_ctx())
    assert again.reason == 'already_entered_this_window'
