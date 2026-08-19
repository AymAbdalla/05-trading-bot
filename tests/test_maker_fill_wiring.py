"""The maker fill model, WIRED into the shadow loop.

The model has existed in `engine/polymarket/paper_adapter.py` since 2026-08-18
(`simulate_maker_buy`, `simulate_maker_sell`, `observe_resting_orders`) and
NOTHING CALLED IT. `engine/polymarket/shadow_loop.py` short-circuited every
`action == 'QUOTE'` into one counter (`maker_quote_not_simulable`) and threw the
legs away, so `PM_box_builder` and `PM_grid_hedge` produced one number forever
and that number described the loop, not the strategies.

This file is the wiring test, not a docstring claim (convention 22). Every test
here asserts on OBSERVED BEHAVIOUR of the loop - a call that happened, an order
that exists, a position that opened one cycle after the quote - rather than on a
comment or a feature key saying a capability is available.

## The four things that had to be true, and are asserted here

  1. A QUOTE decision REACHES `simulate_maker_buy` with the leg's own price and
     size. (`test_a_quote_decision_reaches_the_fill_model`)
  2. Resting is NOT entering, and a rest opens no position in the cycle that
     decided it. (`test_a_rest_is_not_an_entry_and_opens_no_position`)
  3. THE RESTING ORDER SURVIVES THE POLL CYCLE and can fill against a LATER
     book. This is the one that makes the whole path meaningful; without it the
     model can be called but can never fill.
     (`test_a_resting_order_survives_the_cycle_and_fills_on_a_later_book`)
  4. Every no-fill and every no-rest is COUNTED under its OWN reason, and no two
     causes share a counter (convention 20).

## The honest limitation, restated where a reader will hit it

We have no trade prints, only book snapshots roughly five seconds apart. A fill
that happened and reversed between two polls is invisible and is scored as a
no-fill. That makes this model PESSIMISTIC, which is the correct direction to be
wrong in for two strategies whose entire claimed edge is "our resting order got
hit". `test_a_touch_is_not_a_fill` pins the specific case a naive model steals
from.

Everything here is OFFLINE. No socket is opened and `engine.halt.HALT_FILE` is
redirected to a tmp path for every test by an autouse fixture, because a HALT
left behind by a test run would silently block a real session.
"""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import halt                                            # noqa: E402
from engine.polymarket import shadow_loop                          # noqa: E402
from engine.polymarket.paper_adapter import (MAKER_FILL_MODEL,     # noqa: E402
                                             ORDER_CANCELLED,
                                             ORDER_EXPIRED,
                                             ORDER_FILLED)
from engine.polymarket.shadow_loop import (PolymarketShadowLoop,   # noqa: E402
                                           ShadowStore)
from engine.polymarket.types import (Market, Orderbook, Outcome,   # noqa: E402
                                     PriceLevel)
from strategies.polymarket import build_strategies                 # noqa: E402
from strategies.polymarket.base import MarketContext               # noqa: E402
from strategies.polymarket.box_builder import BoxBuilder           # noqa: E402
from strategies.polymarket.grid_hedge import GridHedge             # noqa: E402

import agents.forge_shadow_eval as se                              # noqa: E402

WINDOW_TS = 1699999800
UP_TOKEN = 'UP'
DOWN_TOKEN = 'DN'
SLUG = 'btc-updown-5m-{}'.format(WINDOW_TS)


# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def halt_file(tmp_path, monkeypatch):
    """Redirect the kill switch. Never touch the repository's real HALT."""
    path = tmp_path / 'HALT'
    monkeypatch.setattr(halt, 'HALT_FILE', str(path))
    return path


class NoNetworkClient:
    """A client with no verb that reaches anywhere.

    `fetch_orderbook` is only called by the adapter when the caller passes no
    `book`, and every call in this file passes one. A client that returns None
    for everything therefore proves the books under test are the books the test
    built, not something fetched.
    """

    def __init__(self):
        self.stats = {'requests': 0, 'failures': 0}

    def gamma(self, path, params=None):
        return None

    def clob(self, path, params=None):
        return None

    def data(self, path, params=None):
        return None


def build_loop(tmp_path, strategies=None, **kw):
    """One asset, injected strategies, its own tmp db and CSV."""
    store = ShadowStore(str(tmp_path / 'trading.db'))
    kw.setdefault('assets', ('btc',))
    injected = list(strategies) if strategies is not None else [BoxBuilder()]
    loop = PolymarketShadowLoop(
        client=NoNetworkClient(), store=store,
        log_dir=str(tmp_path / 'paperlog'),
        strategies=injected,
        candle_source=lambda: None,
        include_15m=False,
        enable_weather=False,
        **kw)
    # D-323 paused box_builder/grid_hedge out of the crypto_updown routing
    # (`supported_market_types = ('smart_money',)`), so the constructor's own
    # `_supporting()` filter drops them even when explicitly injected above.
    # This fixture exists to exercise the maker-fill wiring THROUGH these two
    # strategies regardless of their live-routing status, so restore the
    # exact injected list post-construction.
    loop.strategies = injected
    for runtime in loop.runtimes.values():
        runtime.strategies = injected
    return loop


def book(token, asks=(), bids=()):
    return Orderbook(
        token_id=token,
        asks=tuple(PriceLevel(p, s) for p, s in sorted(asks)),
        bids=tuple(PriceLevel(p, s) for p, s in sorted(bids, reverse=True)))


def market():
    return Market(id=SLUG, question=SLUG, slug=SLUG, condition_id='c-' + SLUG,
                  outcomes=(Outcome('Up', UP_TOKEN),
                            Outcome('Down', DOWN_TOKEN)))


def box_ctx(up_asks=((0.55, 100),), up_bids=((0.45, 1),),
            down_asks=((0.55, 100),), down_bids=((0.45, 1),),
            seconds_into_window=30.0, books=None):
    """A book box_builder ARMS on, and a queue small enough to get through.

    ask_up + ask_down = 1.10 clears ARM_ASK_SUM_MIN (1.03); the joined bids sum
    to 0.90, under MAX_PAIR_COST (0.94), so `cap_bids` returns them unchanged
    and the quotes are 0.45 / 0.45.

    The BID SIZES are 1, deliberately. `queue_ahead_shares` is measured at rest
    time as everything bid at our price or better, and with a 100-share bid
    sitting at our own limit we would be behind a queue no plausible cross could
    clear - which is a real market fact but makes for a test that can only ever
    assert a no-fill.
    """
    if books is None:
        books = {UP_TOKEN: book(UP_TOKEN, up_asks, up_bids),
                 DOWN_TOKEN: book(DOWN_TOKEN, down_asks, down_bids)}
    return MarketContext(window_ts=WINDOW_TS, market=market(), books=books,
                         seconds_into_window=seconds_into_window,
                         spot=60000.0, strike=60000.0,
                         atr14=5.0, lead_bps=20.0)


def grid_ctx():
    """The book from `tests/test_grid_hedge.py` that reaches its QUOTE path.

    2c spreads, 100 shares a side, lead 20 bps and ATR 5 bps so implied beats
    realized. Reproduced rather than imported so a change over there fails
    there, loudly, instead of silently changing what this file tests.
    """
    books = {UP_TOKEN: book(UP_TOKEN, ((0.55, 100),), ((0.53, 100),)),
             DOWN_TOKEN: book(DOWN_TOKEN, ((0.47, 100),), ((0.45, 100),))}
    return MarketContext(window_ts=WINDOW_TS, market=market(), books=books,
                         seconds_into_window=60.0, spot=60000.0, strike=60000.0,
                         atr14=5.0, lead_bps=20.0)


def crossed_book(token, through_price=0.40, through_size=200):
    """A later snapshot with size resting STRICTLY BELOW 0.45.

    A real book cannot stay crossed, so an offer under our own bid is the only
    snapshot-visible evidence that sell flow came down through our price. This
    is the shape that fills a resting buy at 0.45 and nothing else does.
    """
    return book(token, asks=((through_price, through_size),), bids=())


DETAIL = {'cycle': 1, 'asset': 'btc'}


# ---------------------------------------------------------------------------
# 0. The short-circuit is gone
# ---------------------------------------------------------------------------

def test_the_skip_maker_short_circuit_no_longer_exists():
    """The literal removal, asserted on the module rather than on the source.

    `SKIP_MAKER` was the constant the short-circuit counted under. If it comes
    back, some path is dropping QUOTE legs again, and this file's other tests
    would keep passing while the live loop stopped resting anything.
    """
    assert not hasattr(shadow_loop, 'SKIP_MAKER'), (
        'shadow_loop.SKIP_MAKER is back; the QUOTE short-circuit was restored')
    # No module attribute CARRIES the retired string either. Asserted on the
    # imported module rather than on the source text, because the module
    # docstring names the retirement on purpose and a source grep would fail on
    # the explanation rather than on a regression (convention 29).
    carriers = sorted(name for name, value in vars(shadow_loop).items()
                      if isinstance(value, str) and not name.startswith('__')
                      and value == 'maker_quote_not_simulable')
    assert carriers == [], (
        'the retired reason is still a live constant in shadow_loop: '
        + repr(carriers))


def test_the_loop_has_a_place_for_a_resting_order_to_live_across_cycles(
        tmp_path):
    """THE hard question, asserted rather than argued.

    A resting order does not fill in the instant it is placed, so the loop must
    hold it somewhere that survives a poll. It does, because the ADAPTER does:
    `self.adapter` is built once in `__init__` and `adapter.resting_orders` is
    a dict on it. If a cycle ever rebuilt the adapter, every resting order
    would be discarded once per poll and no maker order could ever fill - the
    fill model would be callable and unreachable, which is the state this whole
    change is fixing.
    """
    loop = build_loop(tmp_path)
    assert isinstance(loop.adapter.resting_orders, dict)
    assert hasattr(loop.adapter, 'observe_resting_orders')
    assert hasattr(loop, 'observe_maker_orders')

    first = loop.adapter
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))
    ids = {o.order_id for o in loop.adapter.open_resting_orders()}
    assert len(ids) == 2

    loop.run_cycle(now=time.time())

    assert loop.adapter is first
    assert ids <= set(loop.adapter.resting_orders), (
        'a resting order did not survive a full run_cycle')


# ---------------------------------------------------------------------------
# 1. A QUOTE reaches the fill model
# ---------------------------------------------------------------------------

def test_a_quote_decision_reaches_the_fill_model(tmp_path):
    """Convention 22: the call is observed, not claimed.

    Spying on the adapter rather than on the strategy is the point - the bug
    being fixed was a caller that never called, and only the CALLEE can prove
    it was reached.
    """
    loop = build_loop(tmp_path)
    strategy = loop.strategies[0]
    calls = []
    real = loop.adapter.simulate_maker_buy

    def spy(**kw):
        calls.append(dict(kw))
        return real(**kw)

    loop.adapter.simulate_maker_buy = spy

    decision = strategy.evaluate(box_ctx())
    assert decision.action == 'QUOTE', decision.reason
    assert len(decision.legs) == 2

    disposition = loop.evaluate_strategy(strategy, box_ctx(), dict(DETAIL))

    assert disposition == shadow_loop.SKIP_MAKER_RESTED
    assert len(calls) == 2, 'both legs of the box must reach the fill model'
    by_side = {c['outcome_side']: c for c in calls}
    assert set(by_side) == {'Up', 'Down'}
    # The leg's OWN price, not the ask and not a rounded stand-in. Resting at
    # the ask would delete the entire maker claim.
    assert by_side['Up']['limit_price'] == 0.45
    assert by_side['Down']['limit_price'] == 0.45
    assert by_side['Up']['token_id'] == UP_TOKEN
    assert by_side['Down']['token_id'] == DOWN_TOKEN
    # The book the loop already holds is handed down. A None here would send
    # the adapter to the network mid-decision.
    assert by_side['Up']['book'] is not None


def test_the_quote_becomes_a_real_resting_order_on_the_adapter(tmp_path):
    loop = build_loop(tmp_path)
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))

    orders = loop.adapter.open_resting_orders()
    assert len(orders) == 2
    for order in orders:
        assert order.side == 'BUY'
        assert order.strategy == 'PM_box_builder'
        assert order.limit_price == 0.45
        assert order.is_resting
        # Convention 8: a stop strictly below entry. A losing binary is 0.00.
        assert order.stop_price < order.limit_price
        assert order.expires_ts is not None, 'a resting order with no TTL never'\
                                             ' terminates and never gets counted'


def test_grid_hedge_also_gets_past_the_old_gate(tmp_path):
    """The second maker strategy, on its own book shape and its own ladder.

    Convention 23: a fix at one site is not a fix. box_builder quotes two legs
    and grid_hedge up to ten, through the same code path, so both are checked.
    """
    loop = build_loop(tmp_path, strategies=[GridHedge()],
                      config={'polymarket': {'max_resting_maker_orders': 10}})
    strategy = loop.strategies[0]
    decision = strategy.evaluate(grid_ctx())
    assert decision.action == 'QUOTE', decision.reason
    assert len(decision.legs) > 2, 'the ladder should carry several rungs'

    disposition = loop.evaluate_strategy(strategy, grid_ctx(), dict(DETAIL))

    assert disposition == shadow_loop.SKIP_MAKER_RESTED
    orders = loop.adapter.open_resting_orders()
    assert orders, 'no grid rung reached the book'
    assert {o.strategy for o in orders} == {'PM_grid_hedge'}
    assert {o.outcome_side for o in orders} == {'Up', 'Down'}, (
        'a one-sided grid is a directional ladder, not a self-hedge')


# ---------------------------------------------------------------------------
# 2. Resting is not entering
# ---------------------------------------------------------------------------

def test_a_rest_is_not_an_entry_and_opens_no_position(tmp_path):
    loop = build_loop(tmp_path)
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))

    assert loop.adapter.open_positions() == []
    assert loop.counts['entry'] == 0
    assert loop.counts[shadow_loop.SKIP_MAKER_RESTED] == 1
    # ONE evaluation, one disposition. The identity does not care that two legs
    # were rested: it counts windows, not orders.
    assert loop.evaluations == 1


def test_the_accounting_identity_survives_the_maker_path(tmp_path):
    """`evaluations == entries + skips` with the maker path live.

    The fills land in `maker_counts`, which is OUTSIDE the identity, and this is
    the assertion that says so: three cycles of quoting, resting and observing
    must not move `evaluations` by anything other than cycles * strategies.
    """
    loop = build_loop(tmp_path)
    for cycle in range(3):
        loop.cycles += 1
        loop.evaluate_strategy(loop.strategies[0], box_ctx(),
                               dict(DETAIL, cycle=cycle + 1))
        loop.observe_maker_orders({'btc': box_ctx()})

    entries = loop.counts['entry']
    skips = sum(v for k, v in loop.counts.items() if k != 'entry')
    assert loop.evaluations == entries + skips
    assert loop.evaluations == 3
    assert loop.check_identity() is True


# ---------------------------------------------------------------------------
# 3. THE ONE THAT MATTERS: it fills on a LATER book
# ---------------------------------------------------------------------------

def test_a_resting_order_survives_the_cycle_and_fills_on_a_later_book(tmp_path):
    """Cycle 1 rests. Cycle 2 shows a cross. Only then is there a position.

    If this passes and `observe_maker_orders` is later removed from
    `run_cycle`, the loop rests orders that can never fill and reports activity
    with no results - which reads exactly like a strategy that never signalled.
    """
    loop = build_loop(tmp_path)

    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))
    assert loop.adapter.open_positions() == [], 'filled in the deciding cycle'
    order_ids = {o.order_id for o in loop.adapter.open_resting_orders()}
    assert len(order_ids) == 2

    # A LATER cycle, with offers resting strictly below our 0.45 bid.
    later = box_ctx(books={UP_TOKEN: crossed_book(UP_TOKEN),
                           DOWN_TOKEN: crossed_book(DOWN_TOKEN)})
    result = loop.observe_maker_orders({'btc': later})

    assert result['filled'] == 2
    positions = loop.adapter.open_positions()
    assert len(positions) == 2
    for position in positions:
        assert position.strategy == 'PM_box_builder'
        # AT OUR OWN PRICE. Filling at the ask would delete the edge; filling
        # better than our limit would invent one.
        assert position.avg_price == 0.45
        assert position.entry_liquidity == 'maker'
    assert loop.maker_counts['fill:maker_fill'] == 2
    assert loop.adapter.open_resting_orders() == []


def test_a_maker_fill_is_written_to_the_database_as_an_entry(tmp_path):
    """The taker path records its entry inside `_attempt_entry`. A maker fill
    has no such moment, so without `_record_maker_entry` the position would
    exist in the adapter and the CSV and NOWHERE in db/trading.db - the table
    Forge, the critic and the dashboard actually read."""
    loop = build_loop(tmp_path)
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))
    loop.observe_maker_orders({'btc': box_ctx(
        books={UP_TOKEN: crossed_book(UP_TOKEN),
               DOWN_TOKEN: crossed_book(DOWN_TOKEN)})})

    acted = loop.store.conn.execute(
        "SELECT * FROM signals WHERE strategy_id='PM_box_builder' "
        'AND acted = 1').fetchall()
    assert len(acted) == 2, 'a maker fill did not reach the signals table'
    positions = loop.store.conn.execute('SELECT * FROM positions').fetchall()
    assert len(positions) == 2


def test_a_touch_is_not_a_fill(tmp_path):
    """The bucket a naive model steals from.

    `best_ask == our limit` is a LOCKED market, not a trade through our level,
    and we do not know how deep in that queue we are. Change the strict `<` in
    `_through_and_touch` to `<=` and both maker strategies become profitable on
    paper for no reason at all - so it gets its own test and its own reason.
    """
    loop = build_loop(tmp_path)
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))

    touching = box_ctx(books={
        UP_TOKEN: book(UP_TOKEN, asks=((0.45, 500),), bids=()),
        DOWN_TOKEN: book(DOWN_TOKEN, asks=((0.45, 500),), bids=())})
    result = loop.observe_maker_orders({'btc': touching})

    assert result['filled'] == 0
    assert loop.adapter.open_positions() == []
    assert len(loop.adapter.open_resting_orders()) == 2
    assert all(o.touched for o in loop.adapter.open_resting_orders())


# ---------------------------------------------------------------------------
# 4. Convention 20: every no-fill has its OWN counter
# ---------------------------------------------------------------------------

def test_a_quote_that_never_fills_terminates_under_its_own_reason(tmp_path):
    """`maker_never_touched` is not `maker_touched_not_crossed` is not
    `maker_queue_ahead_not_cleared`. Three different findings about a maker's
    pricing, three different fixes, three different numbers."""
    loop = build_loop(tmp_path)
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))

    # A book that never comes near 0.45, then a clock past the TTL.
    far = box_ctx(books={
        UP_TOKEN: book(UP_TOKEN, asks=((0.80, 100),), bids=((0.20, 100),)),
        DOWN_TOKEN: book(DOWN_TOKEN, asks=((0.80, 100),), bids=((0.20, 100),))})
    loop.observe_maker_orders({'btc': far})
    assert len(loop.adapter.open_resting_orders()) == 2

    expired = loop.observe_maker_orders(
        {'btc': far}, now=time.time() + loop.adapter.maker_ttl_seconds + 1)

    assert expired['terminated'] == 2
    assert loop.maker_counts['expire:maker_never_touched'] == 2
    assert loop.maker_counts['fill:maker_fill'] == 0
    assert loop.adapter.open_resting_orders() == []
    orders = list(loop.adapter.resting_orders.values())
    assert {o.status for o in orders} == {ORDER_EXPIRED}


def test_a_touched_but_uncrossed_quote_expires_under_a_different_reason(tmp_path):
    """The pair with the test above. Same outcome (no fill), different CAUSE,
    and they must never share a counter."""
    loop = build_loop(tmp_path)
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))

    touching = box_ctx(books={
        UP_TOKEN: book(UP_TOKEN, asks=((0.45, 500),), bids=()),
        DOWN_TOKEN: book(DOWN_TOKEN, asks=((0.45, 500),), bids=())})
    loop.observe_maker_orders({'btc': touching})
    loop.observe_maker_orders(
        {'btc': touching},
        now=time.time() + loop.adapter.maker_ttl_seconds + 1)

    assert loop.maker_counts['expire:maker_touched_not_crossed'] == 2
    assert loop.maker_counts['expire:maker_never_touched'] == 0


def test_an_order_nobody_handed_a_book_is_not_a_no_fill(tmp_path):
    """Convention 11. `maker_never_observed` is a could-not-look, and it must
    not be read as a market that refused to fill us."""
    loop = build_loop(tmp_path)
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))

    empty = MarketContext(window_ts=WINDOW_TS, market=market(), books={})
    loop.observe_maker_orders(
        {'btc': empty},
        now=time.time() + loop.adapter.maker_ttl_seconds + 1)

    assert loop.maker_counts['expire:maker_never_observed'] == 2
    assert loop.maker_counts['expire:maker_never_touched'] == 0


def test_a_second_quote_for_the_same_token_is_refused_not_re_rested(tmp_path):
    """We do not chase. box_builder quotes for 150 seconds of every window and
    this loop polls every ~5 seconds; without this it would rest ~30 orders per
    side per window and reset its queue position every time."""
    loop = build_loop(tmp_path)
    strategy = loop.strategies[0]
    loop.evaluate_strategy(strategy, box_ctx(), dict(DETAIL))
    disposition = loop.evaluate_strategy(strategy, box_ctx(), dict(DETAIL))

    assert disposition == shadow_loop.SKIP_MAKER_ALREADY_RESTING
    assert loop.counts[shadow_loop.SKIP_MAKER_ALREADY_RESTING] == 1
    assert len(loop.adapter.open_resting_orders()) == 2, 'a duplicate rested'
    assert loop.health['maker_leg_already_resting'] == 2


def test_the_maker_budget_stops_makers_from_starving_the_taker_path(tmp_path):
    """The adapter counts resting BUYS against the same slot cap as open
    positions. Without a budget, the first cycle box_builder and grid_hedge
    quote fills every slot and all 17 taker strategies are refused
    `max_concurrent_positions` for the rest of the session."""
    loop = build_loop(tmp_path,
                      config={'polymarket': {'max_resting_maker_orders': 1}})
    disposition = loop.evaluate_strategy(loop.strategies[0], box_ctx(),
                                         dict(DETAIL))

    # One leg rested, so the EVALUATION is still a rest - but the refused leg
    # is counted under its own name rather than vanishing.
    assert disposition == shadow_loop.SKIP_MAKER_RESTED
    assert len(loop.adapter.open_resting_orders()) == 1
    assert loop.health['maker_budget_blocks'] == 1
    assert loop.health['maker_partial_quotes'] == 1


def test_a_budget_of_zero_makes_the_refusal_the_disposition(tmp_path):
    loop = build_loop(tmp_path,
                      config={'polymarket': {'max_resting_maker_orders': 0}})
    disposition = loop.evaluate_strategy(loop.strategies[0], box_ctx(),
                                         dict(DETAIL))
    assert disposition == shadow_loop.SKIP_MAKER_BUDGET
    assert loop.adapter.open_resting_orders() == []


def test_a_leg_with_no_book_is_no_liquidity_not_a_maker_refusal(tmp_path):
    """Two different drop causes. "Nobody is quoting this token" is not "the
    adapter refused our quote", and they need opposite responses."""
    loop = build_loop(tmp_path)
    ctx = box_ctx()
    # box_builder needs both books to decide, so the decision is made on a full
    # context and the book is removed before the legs are rested. That is the
    # real shape of the race: a context built, then a token dropping out.
    decision = loop.strategies[0].evaluate(ctx)
    stripped = box_ctx(books={UP_TOKEN: ctx.books[UP_TOKEN]})
    disposition = loop._attempt_maker_quotes(
        loop.strategies[0], decision, stripped, {}, 0.0)

    assert disposition == shadow_loop.SKIP_MAKER_RESTED
    assert loop.health['maker_leg_no_book'] == 1
    assert len(loop.adapter.open_resting_orders()) == 1


def test_a_quote_with_no_legs_is_its_own_reason(tmp_path):
    """The mirror of `enter_without_legs`: a strategy bug, not a market."""
    from strategies.polymarket.base import Decision

    loop = build_loop(tmp_path)
    decision = Decision(action='QUOTE', reason='maker_fill_not_simulated',
                        strategy='PM_box_builder', legs=[])
    disposition = loop._attempt_maker_quotes(loop.strategies[0], decision,
                                             box_ctx(), {}, 0.0)
    assert disposition == shadow_loop.SKIP_MAKER_NO_LEGS


def test_a_crossing_quote_is_refused_by_the_adapter_and_carries_its_reason(
        tmp_path):
    """A bid at or above the best ask is a TAKER order with a maker label.
    Filling it here would be the single most attractive bug available: the
    strategy would book a maker fill while paying the spread."""
    from strategies.polymarket.base import Decision, Leg

    loop = build_loop(tmp_path)
    ctx = box_ctx(up_asks=((0.50, 100),), down_asks=((0.60, 100),))
    decision = Decision(action='QUOTE', reason='maker_fill_not_simulated',
                        strategy='PM_box_builder',
                        legs=[Leg('Up', 0.55, order_type='maker', shares=5)])
    disposition = loop._attempt_maker_quotes(loop.strategies[0], decision, ctx,
                                             {}, 0.0)

    # The adapter's OWN taxonomy, carried verbatim including its `SKIP:`
    # action prefix, exactly as the taker path carries `adapter:SKIP:...`. Our
    # counters report its reason rather than our guess at it.
    assert disposition == (shadow_loop.MAKER_ADAPTER_PREFIX
                           + 'SKIP:maker_would_cross_book')
    assert loop.adapter.open_resting_orders() == []
    assert loop.health['maker_adapter_refusals'] == 1


def test_no_two_maker_drop_causes_share_a_counter():
    """Convention 20, asserted on the constants themselves. The old single
    bucket pooled all of these and several thousand rows of it said nothing
    about either strategy."""
    reasons = [shadow_loop.SKIP_MAKER_RESTED, shadow_loop.SKIP_MAKER_HALTED,
               shadow_loop.SKIP_MAKER_NO_LEGS,
               shadow_loop.SKIP_MAKER_ALREADY_RESTING,
               shadow_loop.SKIP_MAKER_BUDGET, shadow_loop.SKIP_HALTED,
               shadow_loop.SKIP_NO_LIQUIDITY, shadow_loop.SKIP_UNKNOWN_TOKEN]
    assert len(set(reasons)) == len(reasons)
    # And the maker halt is NOT the taker halt: the observable consequence
    # differs (resting buys are cancelled), so it gets its own number.
    assert shadow_loop.SKIP_MAKER_HALTED != shadow_loop.SKIP_HALTED
    assert shadow_loop.MAKER_ADAPTER_PREFIX != 'adapter:'


def test_every_new_maker_reason_is_classified(tmp_path):
    """A reason the classifier cannot place lands in UNKNOWN, and 18.1% of all
    skips sat there once already because reasons were added without classifying
    them."""
    for reason in (shadow_loop.SKIP_MAKER_RESTED, shadow_loop.SKIP_MAKER_HALTED,
                   shadow_loop.SKIP_MAKER_NO_LEGS,
                   shadow_loop.SKIP_MAKER_ALREADY_RESTING,
                   shadow_loop.SKIP_MAKER_BUDGET,
                   shadow_loop.MAKER_ADAPTER_PREFIX + 'maker_would_cross_book',
                   shadow_loop.MAKER_ADAPTER_PREFIX + 'no_orderbook'):
        assert se.classify_skip_reason(reason)[0] != se.UNKNOWN, reason
    # And the retired one stays classifiable, because ~220k historical rows
    # carry it.
    assert se.classify_skip_reason('maker_quote_not_simulable')[0] != se.UNKNOWN
    assert 'maker_quote_not_simulable' in se.RETIRED_SKIP_REASONS


# ---------------------------------------------------------------------------
# 5. The kill switch
# ---------------------------------------------------------------------------

def test_halt_refuses_a_new_resting_buy(tmp_path, halt_file):
    """A resting bid that fills is a NEW ENTRY, and the Polymarket halt
    contract blocks entries. Refusing it is the only honest reading."""
    loop = build_loop(tmp_path)
    halt_file.write_text('{"reason": "drill"}')
    assert halt.is_halted() is True

    disposition = loop.evaluate_strategy(loop.strategies[0], box_ctx(),
                                         dict(DETAIL))

    assert disposition == shadow_loop.SKIP_MAKER_HALTED
    assert loop.adapter.open_resting_orders() == []
    assert loop.counts[shadow_loop.SKIP_MAKER_HALTED] == 1


def test_halt_cancels_resting_buys_already_on_the_book(tmp_path, halt_file):
    """Refusing new ones is half a kill switch (convention 23).

    An UNCROSSED resting bid would otherwise sit armed through the halt and
    fill the moment it lifted, on a book that has since moved. Cancelling
    rather than deferring is the whole point.
    """
    loop = build_loop(tmp_path)
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))
    assert len(loop.adapter.open_resting_orders()) == 2

    halt_file.write_text('{"reason": "drill"}')
    result = loop.observe_maker_orders({'btc': box_ctx()})

    assert result['cancelled_by_halt'] == 2
    assert loop.adapter.open_resting_orders() == []
    assert loop.maker_counts['cancel:maker_cancelled_by_halt'] == 2
    assert {o.status for o in loop.adapter.resting_orders.values()} == \
        {ORDER_CANCELLED}


def test_halt_cancels_even_a_bid_the_book_never_came_near(tmp_path, halt_file):
    """The adapter cancels a CROSSED buy at observation time. That is not
    enough on its own: an order the book never reaches is never observed as
    crossed, so the loop has to take it off the book itself."""
    loop = build_loop(tmp_path)
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))
    halt_file.write_text('{"reason": "drill"}')

    far = box_ctx(books={
        UP_TOKEN: book(UP_TOKEN, asks=((0.90, 100),), bids=((0.10, 100),)),
        DOWN_TOKEN: book(DOWN_TOKEN, asks=((0.90, 100),), bids=((0.10, 100),))})
    loop.observe_maker_orders({'btc': far})

    assert loop.adapter.open_resting_orders() == []
    assert loop.maker_counts['cancel:maker_cancelled_by_halt'] == 2


def test_halt_leaves_a_resting_sell_alone(tmp_path, halt_file):
    """The same asymmetry `simulate_taker_sell` already has. A halt says stop
    taking risk, and an ask resting over an open position reduces it."""
    loop = build_loop(tmp_path)
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))
    loop.observe_maker_orders({'btc': box_ctx(
        books={UP_TOKEN: crossed_book(UP_TOKEN),
               DOWN_TOKEN: crossed_book(DOWN_TOKEN)})})
    position = loop.adapter.open_positions()[0]

    sell = loop.adapter.simulate_maker_sell(
        position.position_id, limit_price=0.70,
        book=book(position.token_id, asks=((0.75, 50),), bids=((0.60, 50),)),
        reason='profit_target')
    assert sell is not None and sell.side == 'SELL'

    halt_file.write_text('{"reason": "drill"}')
    loop.observe_maker_orders({'btc': box_ctx()})

    still = loop.adapter.open_resting_orders()
    assert [o.side for o in still] == ['SELL']
    assert still[0].order_id == sell.order_id


def test_a_halted_cycle_never_fills_a_resting_buy(tmp_path, halt_file):
    """The end-to-end version: halt on, a book that WOULD have crossed, and no
    position at the end of it."""
    loop = build_loop(tmp_path)
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))
    halt_file.write_text('{"reason": "drill"}')

    loop.observe_maker_orders({'btc': box_ctx(
        books={UP_TOKEN: crossed_book(UP_TOKEN),
               DOWN_TOKEN: crossed_book(DOWN_TOKEN)})})

    assert loop.adapter.open_positions() == []
    assert loop.maker_counts['fill:maker_fill'] == 0


# ---------------------------------------------------------------------------
# 6. It is actually IN the cycle, and the registry is untouched
# ---------------------------------------------------------------------------

def test_run_cycle_observes_resting_orders(tmp_path, monkeypatch):
    """The wiring that makes every other test in this file matter in
    production. `_attempt_maker_quotes` only puts an order on the book; if
    `run_cycle` stops calling `observe_maker_orders`, nothing ever fills."""
    loop = build_loop(tmp_path)
    seen = []
    real = loop.observe_maker_orders
    monkeypatch.setattr(loop, 'observe_maker_orders',
                        lambda *a, **kw: (seen.append((a, kw)), real(*a, **kw))[1])

    detail = loop.run_cycle(now=time.time())

    assert seen, 'run_cycle did not observe resting orders'
    assert 'maker' in detail
    assert 'cycle_maker_observe' in loop.timings


def test_stats_reports_the_maker_numbers_outside_the_identity(tmp_path):
    loop = build_loop(tmp_path)
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))
    stats = loop.stats()

    assert stats['maker_orders']['resting_buys_now'] == 2
    assert stats['maker_orders']['fill_model'] == MAKER_FILL_MODEL
    assert stats['maker_orders']['max_resting_maker_orders'] == \
        shadow_loop.DEFAULT_MAX_RESTING_MAKER_ORDERS
    assert stats['maker_orders']['capital_committed_usdc'] > 0
    assert 'maker_counts' in stats
    # `entries` counts FILLS, and nothing has filled. Reporting a rest as an
    # entry is the exact fabrication the QUOTE refusal existed to prevent.
    assert stats['entries'] == 0


def test_the_first_eight_registry_slots_did_not_move():
    """Guard, not a new claim: this change must not reorder the registry.
    `tests/test_polymarket_shadow_loop.py` pins these too; a maker change that
    quietly renumbered them would make every historical counter unreadable."""
    names = [s.strategy_name for s in build_strategies()]
    assert names[:8] == [
        'PM_streak_snapper', 'PM_mid_price_continuation', 'PM_box_builder',
        'PM_corridor_collector', 'PM_temporal_arbitrage', 'PM_corridor_pair',
        'PM_spread_harvest_taker', 'PM_fair_value_arb',
    ]
    # 25 since `PM_weather_bracket_width_matched` was APPENDED at index 23
    # (proposal 033), `PM_fair_value_settlement_exit` at index 24
    # (proposal 034), after `PM_longshot_fade_hold_to_resolution` at index 22
    # (proposal 032), after `PM_status_quo_collector` at index 21
    # (proposal 028), after `PM_smart_money_callers` at index 20
    # (proposal 027), after `PM_maker_rebate_quote_ladder` at index 19
    # (proposal 024). The prefix pin above is what protects the historical log
    # positions; the total is free to grow as long as nothing before it moves.
    assert len(names) == 25


def test_a_fill_is_priced_at_our_own_limit_and_pays_the_maker_fee(tmp_path):
    """The one number in this path that must not be fudged in either
    direction: filling at the ask deletes the edge, filling better than our own
    limit invents one."""
    loop = build_loop(tmp_path)
    loop.evaluate_strategy(loop.strategies[0], box_ctx(), dict(DETAIL))
    loop.observe_maker_orders({'btc': box_ctx(
        books={UP_TOKEN: crossed_book(UP_TOKEN, through_price=0.30),
               DOWN_TOKEN: crossed_book(DOWN_TOKEN, through_price=0.30)})})

    for position in loop.adapter.open_positions():
        assert position.avg_price == 0.45
        assert position.cost_usdc == pytest.approx(0.45 * position.shares)
        assert position.fee_usdc == pytest.approx(
            position.cost_usdc * loop.adapter.maker_fee_rate)
    filled = [o for o in loop.adapter.resting_orders.values()
              if o.status == ORDER_FILLED]
    assert len(filled) == 2
