"""Tests for the Polymarket paper adapter (D-267).

No network. Every orderbook here is a synthetic fixture, so a fill result is a
statement about the adapter's arithmetic and nothing else.

The thing under test is whether a simulated fill is HONEST. On a 5-minute
binary the top level is routinely 5-20 shares, so the difference between "you
got the best ask" and "you walked three levels" is most of the theoretical
edge. A paper adapter that quietly fills the full requested size at the best
price does not have a rounding bug, it has a fabricated-edge bug, and it will
report a profitable strategy that cannot be traded.

Every test here asserts behaviour the adapter gets RIGHT today, so the whole
file is a set of regression locks: if the fill model ever drifts back to
top-of-book, if the notional cap stops being enforced, if the tick rounding
loses its epsilon, or if a skip taxonomy collapses two causes into one bucket,
these fail.

This file previously carried ten `xfail(strict=True)` markers documenting
defects in `engine/polymarket/paper_adapter.py` that the authoring session
could not fix, on the rule that `strict=True` turns the eventual fix into an
XPASS failure so a fix cannot land without also removing the marker. The
adapter has since been written to satisfy all ten, every marker reported
XPASS, and all ten were removed after verifying the behaviour against the
adapter source rather than against the marker text. There are no known
outstanding defects recorded here; a green suite now means what it says.
"""
import ast
import calendar
import csv
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket import paper_adapter as pa_module
from engine.polymarket.paper_adapter import (LOG_COLUMNS, ORDER_CANCELLED,
                                             ORDER_EXPIRED, ORDER_FILLED,
                                             ORDER_RESTING, PaperPosition,
                                             PolymarketPaperAdapter,
                                             RestingOrder)
from engine.polymarket.types import (LOSING_REDEMPTION, MIN_SHARES,
                                     WINNING_REDEMPTION, Orderbook, PriceLevel)


# -- fixtures ---------------------------------------------------------------

class StubClient:
    """Stands in for PolymarketClient. Any network call is a test bug.

    `clob` returns None by default, which is the client's documented
    "read failed" answer, so a test that forgets to inject a book exercises the
    skip path rather than reaching for the wire.
    """

    def __init__(self, payload=None):
        self.payload = payload
        self.calls = []
        self.stats = {'requests': 0, 'retries': 0, 'failures': 0,
                      'rate_limit_waits': 0}

    def clob(self, path, params=None):
        self.calls.append((path, params))
        return self.payload

    gamma = clob
    data = clob


def make_book(asks, bids=((0.40, 100.0),), token_id='tok-1'):
    """Synthetic book. `asks` best-first ascending, `bids` best-first descending.

    Built directly rather than through `orderbook_from_api` so a test states the
    exact depth it means and does not also depend on the parser.
    """
    return Orderbook(
        token_id=token_id,
        bids=tuple(PriceLevel(float(p), float(s)) for p, s in bids),
        asks=tuple(PriceLevel(float(p), float(s)) for p, s in asks),
        timestamp=1700000000,
    )


@pytest.fixture
def adapter(tmp_path):
    """Adapter with a cap wide enough that sizing never masks a fill result."""
    return PolymarketPaperAdapter(
        client=StubClient(),
        config={'polymarket': {'notional_cap_usdc': 100.0,
                               'starting_equity_usdc': 2000.0}},
        log_dir=str(tmp_path / 'pmlog'),
    )


def make_adapter(tmp_path, **cfg):
    return PolymarketPaperAdapter(
        client=StubClient(), config={'polymarket': cfg},
        log_dir=str(tmp_path / 'pmlog'))


def log_rows(adapter):
    """Every row in the decision log, as dicts."""
    if not os.path.exists(adapter.log_path):
        return []
    with open(adapter.log_path, newline='') as f:
        return list(csv.DictReader(f))


# -- the fill model ---------------------------------------------------------

class TestBookWalking:
    """The single most important property: a taker fill consumes real depth."""

    def test_walk_consumes_levels_in_order_and_size_weights_them(self, adapter):
        """20 shares across 5@0.50, 5@0.52, 10@0.55.

        Size-weighted average is (5*.50 + 5*.52 + 10*.55) / 20 = 10.60/20 = .53.
        """
        book = make_book([(0.50, 5), (0.52, 5), (0.55, 10)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.60, shares=20, book=book)
        assert pos is not None
        assert pos.shares == pytest.approx(20.0)
        assert pos.avg_price == pytest.approx(0.53)
        assert pos.cost_usdc == pytest.approx(10.60)

    def test_average_entry_is_worse_than_best_ask(self, adapter):
        """The whole point. Best ask 0.50; you do not get 20 shares at 0.50."""
        book = make_book([(0.50, 5), (0.52, 5), (0.55, 10)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.60, shares=20, book=book)
        assert pos.avg_price > book.best_ask
        # 3c per share of real slippage on a 1c-tick binary.
        assert pos.avg_price - book.best_ask == pytest.approx(0.03)
        # The fabricated-edge number this test exists to rule out.
        assert pos.cost_usdc != pytest.approx(20 * book.best_ask)

    def test_levels_consumed_are_logged(self, adapter):
        """The log must show WHY the average was worse, not just that it was."""
        book = make_book([(0.50, 5), (0.52, 5), (0.55, 10)])
        adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.60, shares=20, book=book)
        row = log_rows(adapter)[0]
        assert row['action'] == 'ENTER'
        assert row['levels_consumed'] == '0.5@5.0|0.52@5.0|0.55@10.0'
        assert float(row['best_ask']) == pytest.approx(0.50)
        assert float(row['slippage_vs_top']) == pytest.approx(0.03)

    def test_buy_walks_asks_not_bids(self, adapter):
        """Sides must not be crossed.

        Bids sit at 0.10, asks at 0.50. An adapter that walked the bid side
        would report a 0.10 entry - a 5x overstatement of edge that would look
        like a wildly profitable strategy.
        """
        book = make_book(asks=[(0.50, 50)], bids=[(0.10, 50)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.90, shares=20, book=book)
        assert pos.avg_price == pytest.approx(0.50)
        assert pos.avg_price != pytest.approx(0.10)

    def test_limit_price_stops_the_walk(self, adapter):
        """Levels above the limit are not consumed, and that is a partial."""
        book = make_book([(0.50, 5), (0.52, 5), (0.80, 100)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.55, shares=20, book=book)
        assert pos.shares == pytest.approx(10.0)      # only the two cheap levels
        assert pos.avg_price == pytest.approx(0.51)
        assert pos.avg_price <= 0.55
        row = log_rows(adapter)[0]
        # Priced out, not out of depth. The two must stay distinguishable.
        assert row['exhausted_book'] == 'False'

    def test_all_levels_above_limit_is_no_fill(self, adapter):
        book = make_book([(0.80, 100)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.55, shares=20, book=book)
        assert pos is None
        row = log_rows(adapter)[0]
        assert (row['action'], row['reason']) == ('NO_FILL', 'book_above_limit')


class TestPartialFills:
    """A book that cannot fill the size must not be rounded up to one that can."""

    def test_insufficient_depth_produces_a_partial_not_a_full_fill(self, adapter):
        """50 requested, 8 available (5@0.50 + 3@0.52)."""
        book = make_book([(0.50, 5), (0.52, 3)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.60, shares=50, book=book)
        assert pos is not None
        assert pos.shares == pytest.approx(8.0)
        assert pos.shares < 50
        assert pos.cost_usdc == pytest.approx(5 * 0.50 + 3 * 0.52)
        assert pos.avg_price == pytest.approx(4.06 / 8)

    def test_partial_is_never_the_full_size_at_the_best_price(self, adapter):
        """The specific fabrication this suite exists to prevent."""
        book = make_book([(0.50, 5), (0.52, 3)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.60, shares=50, book=book)
        assert pos.shares != pytest.approx(50.0)
        assert pos.cost_usdc != pytest.approx(50 * 0.50)
        assert pos.max_loss_usdc == pytest.approx(4.06)   # not 25.00

    def test_partial_is_flagged_in_the_log_and_the_counters(self, adapter):
        book = make_book([(0.50, 5), (0.52, 3)])
        adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.60, shares=50, book=book)
        row = log_rows(adapter)[0]
        assert row['action'] == 'ENTER'
        assert row['reason'] == 'partial_fill'
        assert float(row['requested_shares']) == 50.0
        assert float(row['filled_shares']) == 8.0
        assert row['exhausted_book'] == 'True'
        # A partial entry must not be counted as a clean entry.
        assert adapter.decision_counts == {'ENTER:partial_fill': 1}

    def test_partial_below_exchange_minimum_is_no_fill(self, adapter):
        """3 shares available, exchange minimum is 5: the order could not exist."""
        book = make_book([(0.50, 3)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.60, shares=20, book=book)
        assert pos is None
        assert adapter.positions == {}
        row = log_rows(adapter)[0]
        assert (row['action'], row['reason']) == ('NO_FILL',
                                                  'partial_below_min_shares')


class TestEmptyAndUnusableBooks:
    """Zero liquidity is a skip. It is never a fill at some default price."""

    def test_book_with_no_asks_does_not_fill(self, adapter):
        book = Orderbook(token_id='tok-1', bids=(PriceLevel(0.40, 100.0),),
                         asks=())
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.60, shares=20, book=book)
        assert pos is None
        assert adapter.positions == {}
        assert len(log_rows(adapter)) == 1        # evaluated, so it leaves a row

    def test_failed_book_read_logs_a_skip(self, tmp_path):
        """`clob` returns None -> fetch_orderbook returns None -> SKIP.

        `no_orderbook` is a cannot-run window (convention 11), not a loss.
        """
        adapter = make_adapter(tmp_path, notional_cap_usdc=100.0)
        adapter.client = StubClient(payload=None)
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.60, shares=20)
        assert pos is None
        row = log_rows(adapter)[0]
        assert (row['action'], row['reason']) == ('SKIP', 'no_orderbook')
        assert adapter.decision_counts == {'SKIP:no_orderbook': 1}

    def test_empty_book_reason_says_no_liquidity(self, adapter):
        """A book with no asks is `no_liquidity`, never `book_above_limit`.

        "Nobody was quoting" and "our price was too tight" are opposite
        diagnoses and must not share a bucket. Asserting the exact reason
        rather than merely `!= book_above_limit` is the point: a third,
        equally wrong bucket would satisfy the weaker assertion.
        """
        book = Orderbook(token_id='tok-1', bids=(PriceLevel(0.40, 100.0),),
                         asks=())
        adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.60, shares=20, book=book)
        row = log_rows(adapter)[0]
        assert (row['action'], row['reason']) == ('SKIP', 'no_liquidity')
        assert adapter.decision_counts == {'SKIP:no_liquidity': 1}

    def test_book_fetch_exception_still_leaves_a_row(self, tmp_path, monkeypatch):
        adapter = make_adapter(tmp_path, notional_cap_usdc=100.0)

        def boom(client, token_id):
            raise RuntimeError('transport exploded')

        monkeypatch.setattr(pa_module, 'fetch_orderbook', boom)
        try:
            adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                       limit_price=0.60, shares=20)
        except RuntimeError:
            pass
        assert len(log_rows(adapter)) == 1


class TestPriceBounds:
    """A share pays exactly $1.00 or exactly $0.00. Prices live on [0, 1]."""

    def test_normal_prices_are_inside_the_unit_interval(self, adapter):
        book = make_book([(0.50, 5), (0.99, 100)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=1.00, shares=20, book=book)
        assert 0.0 < pos.avg_price <= 1.0
        assert pos.max_gain_usdc > 0

    def test_price_above_one_dollar_is_rejected(self, adapter):
        book = make_book([(1.20, 100)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=1.50, shares=20, book=book)
        assert pos is None

    def test_max_gain_is_never_negative(self, adapter):
        book = make_book([(1.20, 100)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=1.50, shares=20, book=book)
        assert pos is None or pos.max_gain_usdc >= 0


class TestSizing:
    """Unsizable is cannot-run, not a loss (convention 11 / the D-249 shape)."""

    def test_shares_for_respects_the_notional_cap(self, tmp_path):
        adapter = make_adapter(tmp_path, notional_cap_usdc=10.0)
        assert adapter.shares_for(0.50) == 20
        assert adapter.shares_for(0.25) == 40

    def test_shares_for_returns_zero_below_the_exchange_minimum(self, tmp_path):
        """$2 cap at 90c buys 2 shares; the minimum is 5, so nothing is sizable."""
        adapter = make_adapter(tmp_path, notional_cap_usdc=2.0)
        assert adapter.shares_for(0.90) == 0

    def test_shares_for_rejects_a_zero_price(self, tmp_path):
        adapter = make_adapter(tmp_path, notional_cap_usdc=10.0)
        assert adapter.shares_for(0.0) == 0
        assert adapter.shares_for(-0.10) == 0

    def test_unsizable_order_logs_a_skip_not_a_loss(self, adapter):
        book = make_book([(0.50, 100)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.50, shares=2, book=book)
        assert pos is None
        row = log_rows(adapter)[0]
        assert (row['action'], row['reason']) == ('SKIP', 'unsizable_at_cap')
        assert adapter.realized_pnl() == 0.0        # not booked as a loss

    def test_notional_cap_is_enforced_on_the_fill(self, tmp_path):
        adapter = make_adapter(tmp_path, notional_cap_usdc=10.0)
        book = make_book([(0.90, 500)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.90, shares=500, book=book)
        assert pos is None or pos.cost_usdc <= 10.0 + 1e-9

    def test_max_concurrent_positions_skip_is_logged(self, tmp_path):
        adapter = make_adapter(tmp_path, notional_cap_usdc=100.0,
                               max_concurrent_positions=1)
        book = make_book([(0.50, 500)])
        first = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                           limit_price=0.60, shares=20, book=book)
        second = adapter.simulate_taker_buy('strat', 'slug-2', 'tok-2', 'Up',
                                            limit_price=0.60, shares=20, book=book)
        assert first is not None
        assert second is None
        rows = log_rows(adapter)
        assert (rows[1]['action'], rows[1]['reason']) == (
            'SKIP', 'max_concurrent_positions')


class TestPositionPercentCap:
    """D-366: an entry costs at most a PERCENTAGE of available capital.

    The ruling is as much about what does NOT happen as what does. Aym rejected
    skipping a trade the book cannot fully fund ("i dont want it to skip the
    trade if the available capital is less than the capital needed"), so every
    test that asserts a smaller fill here is also asserting the absence of a
    refusal. The one skip left is physical, not a policy: 90% of what remains
    cannot buy the exchange minimum of 5 shares.
    """

    def test_the_default_is_ninety_percent(self):
        assert pa_module.DEFAULT_MAX_POSITION_PCT == 0.90

    def test_max_cost_is_the_percentage_of_available_capital(self, tmp_path):
        """The two numbers from the ruling, verbatim: $1,000 -> $900, $100 -> $90."""
        thousand = make_adapter(tmp_path, starting_equity_usdc=1000.0)
        assert thousand.max_position_cost() == pytest.approx(900.0)
        hundred = make_adapter(tmp_path / 'b', starting_equity_usdc=100.0)
        assert hundred.max_position_cost() == pytest.approx(90.0)

    def test_the_percentage_is_configurable(self, tmp_path):
        half = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                            max_position_pct=0.5)
        assert half.max_position_cost() == pytest.approx(500.0)

    def test_an_entry_inside_the_cap_fills_at_the_size_asked_for(self, tmp_path):
        """The normal case. The cap must be invisible until capital is short."""
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         notional_cap_usdc=1000.0)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=100,
                                   book=make_book([(0.50, 500)]))
        assert pos is not None
        assert pos.shares == 100
        assert a.sizing_counts == {}

    def test_an_entry_over_the_cap_is_sized_down_and_still_fills(self, tmp_path):
        """The ruling's whole point: a smaller trade, never a refused one."""
        a = make_adapter(tmp_path, starting_equity_usdc=100.0,
                         notional_cap_usdc=1000.0)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=200,
                                   book=make_book([(0.50, 500)]))
        assert pos is not None                       # NOT skipped
        assert pos.shares == 180                     # 90% of $100 at 50c
        assert pos.cost_usdc == pytest.approx(90.0)
        assert a.decision_counts == {'ENTER': 1}     # no skip row anywhere

    def test_the_down_size_is_counted_but_not_as_a_decision(self, tmp_path):
        """`decision_counts` keeps one count per CSV row; this is not a row."""
        a = make_adapter(tmp_path, starting_equity_usdc=100.0,
                         notional_cap_usdc=1000.0)
        a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                             limit_price=0.50, shares=200,
                             book=make_book([(0.50, 500)]))
        assert a.sizing_counts == {'taker_capped_at_position_pct': 1}
        assert len(log_rows(a)) == sum(a.decision_counts.values())

    def test_the_log_row_keeps_the_size_that_was_asked_for(self, tmp_path):
        """Requested and filled are both on the row, so the clip is readable."""
        a = make_adapter(tmp_path, starting_equity_usdc=100.0,
                         notional_cap_usdc=1000.0)
        a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                             limit_price=0.50, shares=200,
                             book=make_book([(0.50, 500)]))
        row = log_rows(a)[0]
        assert float(row['requested_shares']) == 200
        assert float(row['filled_shares']) == 180

    def test_the_cap_shrinks_with_the_book_trade_after_trade(self, tmp_path):
        """Available capital is net of premium already at risk, so it decays.

        This is the natural floor D-366 R5 names: each entry leaves 10% of what
        was there behind, so entries alone can never zero the book.
        """
        a = make_adapter(tmp_path, starting_equity_usdc=100.0,
                         notional_cap_usdc=1000.0)
        book = make_book([(0.50, 500)])
        first = a.simulate_taker_buy('s', 'slug-1', 'tok-1', 'Up',
                                     limit_price=0.50, shares=200, book=book)
        assert first.cost_usdc == pytest.approx(90.0)
        assert a.get_equity() == pytest.approx(10.0)
        second = a.simulate_taker_buy('s', 'slug-2', 'tok-2', 'Up',
                                      limit_price=0.50, shares=200, book=book)
        assert second is not None
        assert second.cost_usdc == pytest.approx(9.0)   # 90% of the $10 left
        assert a.get_equity() == pytest.approx(1.0)

    def test_a_book_too_small_for_the_minimum_is_a_cannot_run(self, tmp_path):
        """90c shares, $2 left: the cap buys 2 and the exchange minimum is 5."""
        a = make_adapter(tmp_path, starting_equity_usdc=2.0,
                         notional_cap_usdc=1000.0)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.90, shares=20,
                                   book=make_book([(0.90, 500)]))
        assert pos is None
        assert a.decision_counts == {'SKIP:unsizable_at_position_pct': 1}
        assert a.realized_pnl() == 0.0               # not booked as a loss

    def test_a_resting_maker_bid_is_sized_down_the_same_way(self, tmp_path):
        a = make_maker_adapter(tmp_path, starting_equity_usdc=100.0,
                               notional_cap_usdc=1000.0)
        order = rest_a_bid(a, limit=0.45, shares=500)
        assert order is not None                     # NOT refused
        assert order.shares == 200                   # 90% of $100 at 45c
        assert a.sizing_counts == {'maker_capped_at_position_pct': 1}

    def test_the_cap_never_raises_an_order_above_the_notional_cap(self, tmp_path):
        """The percentage ceiling only ever shrinks. $10 stays $10 at $1,000."""
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         notional_cap_usdc=10.0)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=20,
                                   book=make_book([(0.50, 500)]))
        assert pos is not None
        assert pos.cost_usdc == pytest.approx(10.0)
        assert a.sizing_counts == {}


class TestRoundToTick:
    def test_rounds_off_grid_prices_in_the_right_direction(self, adapter):
        assert adapter.round_to_tick(0.5449, 'down') == 0.54
        assert adapter.round_to_tick(0.5449, 'up') == 0.55

    @pytest.mark.parametrize('price', [0.29, 0.58, 0.07, 0.14, 0.28, 0.56])
    def test_on_grid_prices_are_unchanged(self, adapter, price):
        assert adapter.round_to_tick(price, 'down') == pytest.approx(price)
        assert adapter.round_to_tick(price, 'up') == pytest.approx(price)


class TestDecisionLogging:
    """"The logging IS the product." Every evaluated window leaves a row."""

    def test_log_has_a_header_matching_the_declared_columns(self, adapter):
        adapter.log_skip('strat', 'slug-1', 'no_signal')
        with open(adapter.log_path, newline='') as f:
            header = next(csv.reader(f))
        assert header == LOG_COLUMNS

    def test_every_row_has_every_column(self, adapter):
        """Mixed dispositions must not produce ragged rows."""
        book = make_book([(0.50, 500)])
        adapter.log_skip('strat', 'slug-1', 'spread_too_wide')
        adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.60, shares=20, book=book)
        adapter.simulate_taker_buy('strat', 'slug-2', 'tok-2', 'Up',
                                   limit_price=0.30, shares=20, book=book)
        for row in log_rows(adapter):
            assert set(row.keys()) == set(LOG_COLUMNS)
            assert None not in row.values()      # no short rows

    def test_a_skipped_window_still_produces_a_log_row(self, adapter):
        """The requirement in one line: rejection is a recorded event."""
        adapter.log_skip('strat', 'slug-1', 'btc_move_below_threshold',
                         window_ts=1700000300)
        rows = log_rows(adapter)
        assert len(rows) == 1
        assert rows[0]['action'] == 'SKIP'
        assert rows[0]['reason'] == 'btc_move_below_threshold'
        assert rows[0]['window_ts'] == '1700000300'
        assert rows[0]['strategy'] == 'strat'

    def test_skips_are_counted_by_reason_not_lumped_together(self, adapter):
        """Two different silent drops reported as one number is convention 20's
        actual failure mode. Reasons must stay separate."""
        adapter.log_skip('strat', 'slug-1', 'spread_too_wide')
        adapter.log_skip('strat', 'slug-1', 'spread_too_wide')
        adapter.log_skip('strat', 'slug-1', 'stale_book')
        assert adapter.decision_counts == {'SKIP:spread_too_wide': 2,
                                           'SKIP:stale_book': 1}

    def test_decision_counts_and_row_count_agree(self, adapter):
        """The accounting identity: nothing counted that was not written."""
        book = make_book([(0.50, 5), (0.52, 3)])
        adapter.log_skip('strat', 'slug-1', 'no_signal')
        adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.60, shares=50, book=book)
        adapter.simulate_taker_buy('strat', 'slug-2', 'tok-2', 'Up',
                                   limit_price=0.10, shares=20, book=book)
        assert sum(adapter.decision_counts.values()) == len(log_rows(adapter))

    def test_every_disposition_is_represented(self, adapter, monkeypatch):
        """ENTER, NO_FILL, SKIP and RESOLVE all reach the log."""
        book = make_book([(0.50, 500)])
        adapter.log_skip('strat', 'slug-1', 'no_signal')
        adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.60, shares=20, book=book)
        adapter.simulate_taker_buy('strat', 'slug-2', 'tok-2', 'Up',
                                   limit_price=0.10, shares=20, book=book)
        monkeypatch.setattr(pa_module, 'resolution_price',
                            lambda c, s, o: WINNING_REDEMPTION)
        adapter.resolve_positions()
        actions = [r['action'] for r in log_rows(adapter)]
        assert actions == ['SKIP', 'ENTER', 'NO_FILL', 'RESOLVE']

    def test_timestamps_are_utc_and_marked_as_such(self, adapter):
        adapter.log_skip('strat', 'slug-1', 'no_signal')
        row = log_rows(adapter)[0]
        assert row['iso'].endswith('Z')
        parsed = time.strptime(row['iso'], '%Y-%m-%dT%H:%M:%SZ')
        # calendar.timegm, not time.mktime: mktime reinterprets the struct as
        # LOCAL time, so this test would pass on a UTC machine and drift by the
        # offset everywhere else.
        assert abs(calendar.timegm(parsed) - time.time()) < 120
        assert abs(int(row['ts']) - int(time.time())) < 120

    def test_log_appends_across_adapter_instances(self, tmp_path):
        """A restarted session must not truncate the previous session's log."""
        a1 = make_adapter(tmp_path, notional_cap_usdc=100.0)
        a1.log_skip('strat', 'slug-1', 'first')
        a2 = make_adapter(tmp_path, notional_cap_usdc=100.0)
        a2.log_skip('strat', 'slug-1', 'second')
        rows = log_rows(a2)
        assert [r['reason'] for r in rows] == ['first', 'second']

    def test_header_is_written_when_the_log_exists_but_is_empty(self, adapter):
        os.makedirs(adapter.log_dir, exist_ok=True)
        open(adapter.log_path, 'w').close()          # zero-byte file
        adapter.log_skip('strat', 'slug-1', 'no_signal')
        with open(adapter.log_path, newline='') as f:
            assert next(csv.reader(f)) == LOG_COLUMNS

    def test_window_ts_zero_is_not_logged_as_missing(self, adapter):
        book = make_book([(0.50, 500)])
        adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.60, shares=20, book=book,
                                   window_ts=0)
        assert log_rows(adapter)[0]['window_ts'] == '0'


class TestResolution:
    """PnL is resolution-based: exactly $1.00 or exactly $0.00 per share."""

    def _enter(self, adapter, price=0.50, shares=20):
        book = make_book([(price, 500)])
        return adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                          limit_price=price, shares=shares,
                                          book=book)

    def test_a_win_pays_one_dollar_per_share(self, adapter, monkeypatch):
        pos = self._enter(adapter)                     # 20 @ 0.50 = $10.00
        monkeypatch.setattr(pa_module, 'resolution_price',
                            lambda c, s, o: WINNING_REDEMPTION)
        settled = adapter.resolve_positions()
        assert len(settled) == 1
        assert pos.resolution == 'WIN'
        assert pos.pnl_usdc == pytest.approx(20 * 1.00 - 10.00)
        assert adapter.realized_pnl() == pytest.approx(10.00)

    def test_a_loss_costs_exactly_the_premium(self, adapter, monkeypatch):
        pos = self._enter(adapter)
        monkeypatch.setattr(pa_module, 'resolution_price',
                            lambda c, s, o: LOSING_REDEMPTION)
        adapter.resolve_positions()
        assert pos.resolution == 'LOSS'
        assert pos.pnl_usdc == pytest.approx(-10.00)
        # Max loss is the premium. That IS the stop.
        assert pos.pnl_usdc == pytest.approx(-pos.max_loss_usdc)

    def test_an_unresolved_market_stays_pending(self, adapter, monkeypatch):
        pos = self._enter(adapter)
        monkeypatch.setattr(pa_module, 'resolution_price', lambda c, s, o: None)
        assert adapter.resolve_positions() == []
        assert pos.is_open
        assert pos.resolution is None
        assert adapter.summary()['pending'] == 1

    def test_pending_is_never_folded_into_wins_or_losses(self, adapter,
                                                         monkeypatch):
        """A market quoting 0.99 has not resolved. Booking it is how a paper
        log drifts optimistic exactly where the rare loss is expensive."""
        self._enter(adapter)
        monkeypatch.setattr(pa_module, 'resolution_price', lambda c, s, o: None)
        adapter.resolve_positions()
        s = adapter.summary()
        assert (s['wins'], s['losses'], s['resolved'], s['pending']) == (0, 0, 0, 1)
        assert s['win_rate'] is None                   # not 0.0, not 1.0
        assert s['realized_pnl_usdc'] == 0.0

    def test_capital_at_risk_and_equity_hold_open_positions_at_zero(self, adapter):
        self._enter(adapter)
        assert adapter.capital_at_risk() == pytest.approx(10.00)
        assert adapter.get_equity() == pytest.approx(2000.0 - 10.00)

    def test_resolve_is_idempotent(self, adapter, monkeypatch):
        self._enter(adapter)
        monkeypatch.setattr(pa_module, 'resolution_price',
                            lambda c, s, o: WINNING_REDEMPTION)
        assert len(adapter.resolve_positions()) == 1
        assert adapter.resolve_positions() == []       # no double-booking
        assert adapter.realized_pnl() == pytest.approx(10.00)


class TestBreakeven:
    """On a binary the entry price IS the hurdle. Fees raise it."""

    def test_position_breakeven_includes_the_fee(self, tmp_path):
        adapter = make_adapter(tmp_path, notional_cap_usdc=100.0,
                               taker_fee_rate=0.02)
        book = make_book([(0.50, 500)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.50, shares=20, book=book)
        assert pos.fee_usdc == pytest.approx(0.20)
        assert pos.breakeven_win_rate == pytest.approx(0.51)   # not 0.50

    def test_summary_breakeven_includes_the_fee(self, tmp_path, monkeypatch):
        adapter = make_adapter(tmp_path, notional_cap_usdc=100.0,
                               taker_fee_rate=0.02)
        book = make_book([(0.50, 500)])
        adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=20, book=book)
        monkeypatch.setattr(pa_module, 'resolution_price',
                            lambda c, s, o: WINNING_REDEMPTION)
        adapter.resolve_positions()
        s = adapter.summary()
        assert s['share_weighted_entry_price'] == pytest.approx(0.50)
        assert s['breakeven_win_rate'] == pytest.approx(0.51)

    def test_summary_breakeven_is_correct_at_a_zero_fee(self, adapter,
                                                        monkeypatch):
        """The default path, which is why the defect above is easy to miss."""
        book = make_book([(0.50, 500)])
        adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=20, book=book)
        monkeypatch.setattr(pa_module, 'resolution_price',
                            lambda c, s, o: WINNING_REDEMPTION)
        adapter.resolve_positions()
        assert adapter.summary()['breakeven_win_rate'] == pytest.approx(0.50)

    def test_walked_entry_raises_the_breakeven_hurdle(self, adapter):
        """The reason walking the book matters, stated as a hurdle.

        Best ask 0.50 implies a 50% breakeven. The real average entry is 0.53,
        so the strategy actually needs 53%. Three points of win rate is the
        difference between a live edge and a dead one.
        """
        book = make_book([(0.50, 5), (0.52, 5), (0.55, 10)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.60, shares=20, book=book)
        assert pos.breakeven_win_rate == pytest.approx(0.53)
        assert pos.breakeven_win_rate > book.best_ask


class TestPaperOnly:
    """No live order path may exist in this module."""

    # Import roots that would mean a wallet, a signer or an order SDK is in
    # play. Checked against the parsed AST, not the raw text, so the module's
    # own prose about EIP-712 being out of scope does not trip it.
    FORBIDDEN_IMPORTS = {'web3', 'eth_account', 'eth_utils', 'eth_keys',
                         'coincurve', 'ecdsa', 'hexbytes', 'py_clob_client',
                         'poly_eip712_structs', 'eip712'}
    FORBIDDEN_NAMES = {'post', 'put', 'patch', 'delete', 'sign', 'sign_order',
                       'signer', 'private_key', 'privatekey', 'place_order',
                       'post_order', 'submit_order', 'create_order',
                       'from_key', 'sign_typed_data'}

    @staticmethod
    def _module_symbols():
        """(imported roots, every identifier actually referenced in code).

        Parsed with `ast`, so docstrings and comments are excluded by
        construction - the point is what the module DOES, not what it says.
        """
        with open(pa_module.__file__) as f:
            tree = ast.parse(f.read())
        imports, names = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split('.')[0].lower())
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split('.')[0].lower())
            elif isinstance(node, ast.Name):
                names.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                names.add(node.attr.lower())
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name.lower())
        return imports, names

    def test_mode_is_paper(self, adapter):
        assert adapter.mode == 'paper'

    def test_module_imports_no_wallet_signer_or_order_sdk(self):
        imports, _ = self._module_symbols()
        assert not (imports & self.FORBIDDEN_IMPORTS)

    def test_module_calls_nothing_that_could_send_or_sign_an_order(self):
        _, names = self._module_symbols()
        assert not (names & self.FORBIDDEN_NAMES)

    def test_adapter_exposes_no_order_placing_methods(self):
        for name in dir(PolymarketPaperAdapter):
            assert 'place' not in name.lower()
            assert 'submit' not in name.lower()
            assert 'sign' not in name.lower()

    def test_client_used_here_is_read_only(self):
        """PolymarketClient has no non-GET verb to call."""
        from engine.polymarket.client import PolymarketClient
        for verb in ('post', 'put', 'delete', 'patch'):
            assert not hasattr(PolymarketClient, verb)

    def test_paper_mode_constant_is_true_and_unconditional(self):
        assert pa_module.PAPER_MODE is True


class TestSummaryShape:
    def test_summary_reports_the_log_path_and_counters(self, adapter):
        adapter.log_skip('strat', 'slug-1', 'no_signal')
        s = adapter.summary()
        assert s['mode'] == 'paper'
        assert s['log_path'] == adapter.log_path
        assert s['decision_counts'] == {'SKIP:no_signal': 1}
        assert s['entries'] == 0

    def test_a_skipped_only_session_reports_no_edge_either_way(self, adapter):
        """Zero entries is not zero win rate. Convention 11 at the summary
        layer: did-not-run must not read as ran-and-lost."""
        for _ in range(5):
            adapter.log_skip('strat', 'slug-1', 'no_signal')
        s = adapter.summary()
        assert s['entries'] == 0
        assert s['win_rate'] is None
        assert s['share_weighted_entry_price'] is None
        assert s['realized_pnl_usdc'] == 0.0
        assert sum(s['decision_counts'].values()) == 5


class TestFillRecord:
    def test_build_fill_carries_the_walked_average_not_the_best_ask(self,
                                                                   adapter):
        book = make_book([(0.50, 5), (0.52, 5), (0.55, 10)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.60, shares=20, book=book)
        fill = adapter.build_fill(pos)
        assert fill.side == 'BUY'
        assert fill.avg_price == pytest.approx(0.53)
        assert fill.cost_usdc == pytest.approx(10.60)
        assert fill.max_loss_usdc == pytest.approx(10.60)
        assert fill.max_gain_usdc == pytest.approx(20 * 1.00 - 10.60)

    def test_position_cost_and_average_are_internally_consistent(self, adapter):
        """Guards float drift between avg_price and cost_usdc."""
        book = make_book([(0.47, 3), (0.49, 7), (0.53, 11), (0.61, 4)])
        pos = adapter.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                         limit_price=0.70, shares=25, book=book)
        assert pos.shares == pytest.approx(25.0)
        assert pos.avg_price * pos.shares == pytest.approx(pos.cost_usdc,
                                                           abs=1e-9)
        expected = (3 * 0.47 + 7 * 0.49 + 11 * 0.53 + 4 * 0.61) / 25
        assert pos.avg_price == pytest.approx(expected, abs=1e-12)


# ===========================================================================
# The maker fill model
# ===========================================================================
#
# `box_builder` and `grid_hedge` were both blocked on the same missing thing:
# the adapter simulated marketable orders only, so a resting bid had no fill
# model at all and both strategies returned QUOTE with the reason
# `maker_fill_not_simulated`.
#
# Every test below exists to stop that model from being loosened. The failure
# mode here is NOT a rounding error. A maker strategy's entire claimed edge is
# "our resting order got hit at our own price", so a fill rule that is one
# comparison operator too generous does not make the backtest slightly
# optimistic - it manufactures the P&L from nothing. The most important tests in
# this file are the ones that assert a fill did NOT happen:
# `test_a_touch_at_our_price_is_not_a_fill` and
# `test_the_queue_ahead_of_us_has_to_be_cleared_first`.


def make_maker_adapter(tmp_path, **cfg):
    """Adapter whose caps never mask a maker result. TTL long by default."""
    cfg.setdefault('notional_cap_usdc', 100.0)
    cfg.setdefault('starting_equity_usdc', 2000.0)
    cfg.setdefault('maker_ttl_seconds', 300)
    return make_adapter(tmp_path, **cfg)


def rest_a_bid(adapter, limit=0.45, shares=20, bids=((0.45, 30.0),),
               asks=((0.55, 50.0),), **kw):
    """Rest a BUY at `limit` into a book with `bids` already at that price."""
    book = make_book(asks, bids=bids)
    return adapter.simulate_maker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                      limit_price=limit, shares=shares,
                                      book=book, **kw)


def crossing_book(price=0.40, size=60.0):
    """A book that has traded DOWN through 0.45: offers resting under it.

    Bids are kept below the asks so the snapshot is a legal uncrossed book. Our
    own order is not in it - we are paper - which is exactly why an offer
    resting below our bid is evidence that our bid is gone.
    """
    return make_book(((price, size),), bids=((price - 0.05, 10.0),))


class TestMakerResting:
    """Resting is not filling, and the return type says so."""

    def test_a_rested_order_opens_no_position(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a)
        assert isinstance(order, RestingOrder)
        assert order.status == ORDER_RESTING
        assert a.positions == {}
        assert a.open_positions() == []
        assert a.summary()['entries'] == 0

    def test_resting_writes_a_row_and_a_counter(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        rest_a_bid(a)
        rows = log_rows(a)
        assert [r['action'] for r in rows] == ['REST']
        assert rows[0]['reason'] == 'maker_buy_resting'
        assert a.decision_counts == {'REST:maker_buy_resting': 1}

    def test_the_queue_ahead_is_measured_at_rest_time(self, tmp_path):
        """Everyone already bidding our price or better is in front of us."""
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45,
                           bids=((0.50, 12.0), (0.45, 30.0), (0.40, 99.0)))
        # 12 at a better price plus 30 at ours. The 99 below us is behind us.
        assert order.queue_ahead_shares == pytest.approx(42.0)

    def test_joining_an_empty_price_level_is_the_front_of_the_queue(self,
                                                                   tmp_path):
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45, bids=((0.30, 50.0),))
        assert order.queue_ahead_shares == pytest.approx(0.0)

    def test_a_crossing_bid_is_a_post_only_reject_not_a_fill(self, tmp_path):
        """A bid at or above the ask is a taker order with a maker label on it.

        Filling it here is the single most attractive bug available: the
        strategy books a maker fill while actually paying the spread, which is
        precisely the number box_builder exists to claim.
        """
        a = make_maker_adapter(tmp_path)
        order = a.simulate_maker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                     limit_price=0.55, shares=20,
                                     book=make_book(((0.55, 50.0),),
                                                    bids=((0.45, 30.0),)))
        assert order is None
        assert a.decision_counts == {'SKIP:maker_would_cross_book': 1}
        assert a.resting_orders == {}

    def test_a_bid_above_the_ask_is_also_refused(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        assert a.simulate_maker_buy(
            'strat', 'slug-1', 'tok-1', 'Up', limit_price=0.60, shares=20,
            book=make_book(((0.55, 50.0),), bids=((0.45, 30.0),))) is None
        assert 'SKIP:maker_would_cross_book' in a.decision_counts

    def test_resting_needs_a_book_to_measure_the_queue_against(self, tmp_path):
        """No book means assuming we are at the FRONT, which is optimistic."""
        a = make_maker_adapter(tmp_path)
        assert a.simulate_maker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                    limit_price=0.45, shares=20) is None
        assert a.decision_counts == {'SKIP:no_orderbook': 1}


class TestMakerRestGates:
    """The rest-time gates mirror the taker's, name for name."""

    def test_an_unsizable_order_is_a_cannot_run_not_a_loss(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        assert rest_a_bid(a, shares=2) is None
        assert a.decision_counts == {'SKIP:unsizable_at_cap': 1}

    def test_the_notional_cap_is_enforced_before_anything_rests(self, tmp_path):
        a = make_maker_adapter(tmp_path, notional_cap_usdc=5.0)
        assert rest_a_bid(a, limit=0.45, shares=20) is None
        assert a.decision_counts == {'SKIP:over_notional_cap': 1}

    def test_a_price_outside_the_unit_interval_is_refused(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        assert rest_a_bid(a, limit=1.40) is None
        assert rest_a_bid(a, limit=0.0) is None
        assert a.decision_counts == {'SKIP:limit_price_out_of_range': 2}

    def test_a_halt_refuses_to_rest_anything(self, tmp_path, monkeypatch):
        a = make_maker_adapter(tmp_path)
        monkeypatch.setattr(pa_module, 'is_halted', lambda: True)
        assert rest_a_bid(a) is None
        assert a.decision_counts == {'SKIP:halted': 1}

    def test_resting_orders_count_against_the_concurrency_cap(self, tmp_path):
        """A resting bid is a position the moment it is crossed into.

        Counting only filled positions would let a strategy rest twenty orders
        under a cap of five and discover the cap was decorative exactly once.
        """
        a = make_maker_adapter(tmp_path, max_concurrent_positions=2)
        assert rest_a_bid(a) is not None
        assert rest_a_bid(a) is not None
        assert rest_a_bid(a) is None
        assert a.decision_counts['SKIP:max_concurrent_positions'] == 1

    def test_a_resting_bid_blocks_a_taker_entry_too(self, tmp_path):
        """Both entry paths share one slot count, so the cap is one cap."""
        a = make_maker_adapter(tmp_path, max_concurrent_positions=1)
        assert rest_a_bid(a) is not None
        assert a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                    limit_price=0.60, shares=20,
                                    book=make_book(((0.50, 500.0),))) is None
        assert a.decision_counts['SKIP:max_concurrent_positions'] == 1

    def test_with_no_resting_orders_the_slot_count_is_the_old_one(self,
                                                                 tmp_path):
        """Convention 23's opposite: prove the taker path did NOT change."""
        a = make_maker_adapter(tmp_path)
        assert a.committed_slots() == len(a.open_positions()) == 0
        a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up', limit_price=0.60,
                             shares=20, book=make_book(((0.50, 500.0),)))
        assert a.committed_slots() == len(a.open_positions()) == 1


class TestTheFillRule:
    """A strict cross, minus the queue. The heart of the whole path."""

    def test_a_touch_at_our_price_is_not_a_fill(self, tmp_path):
        """THE test. An offer sitting exactly AT our bid is a locked market.

        Touch-means-fill is the model that books all of the good fills and none
        of the adverse selection. moondevonyt's own logs record the gap it
        cannot reproduce: a fable maker filled 57% at T-240 while a v5 bot armed
        35 times at 0.89 and got ZERO fills.
        """
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45, bids=((0.30, 50.0),))
        touching = make_book(((0.45, 500.0),), bids=((0.44, 10.0),))
        a.observe_resting_orders({'tok-1': touching})
        assert order.status == ORDER_RESTING
        assert order.filled_shares == 0.0
        assert order.touched is True
        assert order.max_through_shares == 0.0
        assert a.positions == {}
        assert a.maker_counts == {'observed:touched_not_crossed': 1}

    def test_a_strict_cross_fills_at_our_own_price(self, tmp_path):
        """The economic point: a maker is PAID the spread, not charged it."""
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45, shares=20, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        assert order.status == ORDER_FILLED
        assert order.fill_price == pytest.approx(0.45)
        assert order.filled_shares == pytest.approx(20.0)
        pos = a.positions[order.position_id]
        assert pos.avg_price == pytest.approx(0.45)
        assert pos.cost_usdc == pytest.approx(9.0)
        assert pos.entry_liquidity == 'maker'

    def test_the_fill_price_is_our_limit_and_never_the_offer(self, tmp_path):
        """Filling at 0.40 would invent an edge; at 0.55 it would delete one."""
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        assert order.fill_price == pytest.approx(0.45)
        assert order.fill_price != pytest.approx(0.40)

    def test_a_maker_fill_is_adverse_and_the_log_says_so(self, tmp_path):
        """The two spread numbers are different and must not be pooled.

        The flattering one is `spread_declined_usdc`: at rest time the offer was
        0.55 and we quoted 0.45, so resting instead of lifting was worth 10c a
        share IF the fill is not adverse. It usually is. By the time our bid was
        crossed the offer had come down to 0.40, so we own shares 5c above the
        current market, and `slippage_vs_top` records that as POSITIVE.

        An earlier draft of this adapter asserted the opposite in a comment -
        "negative by construction, we filled below the offer" - which is the
        exact optimistic reading a maker backtest dies of. It is wrong: the
        offer at fill time is not the offer we declined.
        """
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        enter = [r for r in log_rows(a) if r['action'] == 'ENTER'][0]
        assert enter['reason'] == 'maker_fill'
        assert float(enter['slippage_vs_top']) == pytest.approx(0.05)
        assert order.spread_declined_usdc == pytest.approx(0.10)
        assert 'spread_declined_usdc=0.1' in enter['features']

    def test_the_queue_ahead_of_us_has_to_be_cleared_first(self, tmp_path):
        """30 shares of flow into a 30 share queue reaches us with nothing."""
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45, shares=20, bids=((0.45, 30.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 30.0)})
        assert order.status == ORDER_RESTING
        assert order.max_through_shares == pytest.approx(30.0)
        assert order.fillable_shares == pytest.approx(0.0)
        assert a.positions == {}

    def test_clearing_the_queue_and_then_some_does_fill(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45, shares=20, bids=((0.45, 30.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 55.0)})
        assert order.status == ORDER_FILLED
        assert order.filled_shares == pytest.approx(20.0)

    def test_a_cross_below_the_exchange_minimum_is_not_a_fill(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45, shares=20, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 3.0)})
        assert order.status == ORDER_RESTING
        assert a.maker_counts == {'observed:crossed_below_min_shares': 1}

    def test_a_partial_cross_fills_partially_and_cancels_the_rest(self,
                                                                 tmp_path):
        """8 shares of flow, 20 rested. The other 12 never traded."""
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45, shares=20, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 8.0)})
        assert order.status == ORDER_FILLED
        assert order.terminal_reason == 'maker_partial_fill'
        assert order.filled_shares == pytest.approx(8.0)
        assert order.unfilled_shares == pytest.approx(12.0)
        pos = a.positions[order.position_id]
        assert pos.shares == pytest.approx(8.0)
        assert pos.cost_usdc == pytest.approx(3.6)

    def test_snapshots_are_maxed_and_never_summed(self, tmp_path):
        """Two snapshots showing the same 3 shares are 3 shares, not 6.

        Summing them would invent depth the way top-of-book fills invent price,
        and it would do it silently: 3 is under the exchange minimum and 6 is
        over it, so a sum would turn an impossible order into a filled one.
        """
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45, shares=20, bids=((0.30, 50.0),))
        for _ in range(2):
            a.observe_resting_orders({'tok-1': crossing_book(0.40, 3.0)})
        assert order.max_through_shares == pytest.approx(3.0)
        assert order.status == ORDER_RESTING
        assert a.positions == {}

    def test_a_token_with_no_book_is_not_an_observation(self, tmp_path):
        """Could-not-look is not looked-and-found-nothing (convention 11)."""
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a)
        a.observe_resting_orders({})
        a.observe_resting_orders({'other-token': crossing_book()})
        assert order.observations == 0
        assert a.maker_counts == {}

    def test_a_terminated_order_is_never_observed_again(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        seen = order.observations
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        assert order.observations == seen
        assert len(a.positions) == 1


class TestUnfilledIsAnOutcome:
    """A resting order that never fills is a number, not an absence."""

    def test_expiry_writes_a_row_with_a_named_reason(self, tmp_path):
        a = make_maker_adapter(tmp_path, maker_ttl_seconds=60)
        order = rest_a_bid(a, limit=0.45, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': make_book(((0.55, 50.0),))},
                                 now_ts=order.placed_ts + 999)
        assert order.status == ORDER_EXPIRED
        assert order.terminal_reason == 'maker_never_touched'
        row = [r for r in log_rows(a) if r['action'] == 'EXPIRE'][0]
        assert row['reason'] == 'maker_never_touched'
        assert a.decision_counts['EXPIRE:maker_never_touched'] == 1

    def test_never_observed_is_not_never_touched(self, tmp_path):
        """Two different facts about a strategy, two different fixes."""
        a = make_maker_adapter(tmp_path, maker_ttl_seconds=1)
        order = rest_a_bid(a)
        a.expire_resting_orders(now_ts=order.placed_ts + 999)
        assert order.terminal_reason == 'maker_never_observed'

    def test_touched_but_never_crossed_has_its_own_reason(self, tmp_path):
        """The bucket an optimistic fill model steals every fill from."""
        a = make_maker_adapter(tmp_path, maker_ttl_seconds=60)
        order = rest_a_bid(a, limit=0.45, bids=((0.30, 50.0),))
        touching = make_book(((0.45, 500.0),), bids=((0.44, 10.0),))
        a.observe_resting_orders({'tok-1': touching},
                                 now_ts=order.placed_ts + 999)
        assert order.terminal_reason == 'maker_touched_not_crossed'

    def test_a_small_cross_and_a_big_queue_are_different_reasons(self,
                                                                tmp_path):
        """An order that could not be legal alone at the front was not beaten
        by the queue, so the two never share a counter (convention 20)."""
        a = make_maker_adapter(tmp_path, maker_ttl_seconds=60)
        small = rest_a_bid(a, limit=0.45, shares=20, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 3.0)},
                                 now_ts=small.placed_ts + 999)
        assert small.terminal_reason == 'maker_cross_below_min_shares'

        b = make_maker_adapter(tmp_path, maker_ttl_seconds=60)
        queued = rest_a_bid(b, limit=0.45, shares=20, bids=((0.45, 40.0),))
        b.observe_resting_orders({'tok-1': crossing_book(0.40, 30.0)},
                                 now_ts=queued.placed_ts + 999)
        assert queued.terminal_reason == 'maker_queue_ahead_not_cleared'

    def test_a_cancel_is_not_an_expiry(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a)
        a.cancel_resting_order(order.order_id)
        assert order.status == ORDER_CANCELLED
        assert order.terminal_reason == 'cancelled_by_strategy'
        assert a.decision_counts['CANCEL:cancelled_by_strategy'] == 1

    def test_cancelling_twice_is_refused_and_logged(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a)
        a.cancel_resting_order(order.order_id)
        assert a.cancel_resting_order(order.order_id) is None
        assert a.decision_counts['SKIP:resting_order_not_open'] == 1

    def test_an_unknown_order_id_still_leaves_a_row(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        assert a.cancel_resting_order('no-such-order') is None
        assert a.decision_counts == {'SKIP:unknown_resting_order': 1}

    def test_a_dead_feed_does_not_make_an_order_immortal(self, tmp_path):
        """No book arrived, but the window still closed."""
        a = make_maker_adapter(tmp_path, maker_ttl_seconds=60)
        order = rest_a_bid(a)
        a.observe_resting_orders({}, now_ts=order.placed_ts + 999)
        assert order.status == ORDER_EXPIRED

    def test_every_declared_no_fill_reason_is_reachable(self, tmp_path):
        """Convention 22: the tuple is a claim until something produces each.

        Built by exercising the paths rather than by reading the constant, so a
        reason that becomes unreachable fails here instead of living on as a
        string nothing can emit.
        """
        seen = set()

        a = make_maker_adapter(tmp_path, maker_ttl_seconds=1)
        o = rest_a_bid(a)
        a.expire_resting_orders(now_ts=o.placed_ts + 99)
        seen.add(o.terminal_reason)                       # never_observed

        for shares_through, bids, expected in (
                (0.0, ((0.30, 50.0),), 'maker_never_touched'),
                (3.0, ((0.30, 50.0),), 'maker_cross_below_min_shares'),
                (30.0, ((0.45, 40.0),), 'maker_queue_ahead_not_cleared')):
            b = make_maker_adapter(tmp_path, maker_ttl_seconds=1)
            order = rest_a_bid(b, limit=0.45, shares=20, bids=bids)
            book = (make_book(((0.55, 50.0),)) if shares_through == 0
                    else crossing_book(0.40, shares_through))
            b.observe_resting_orders({'tok-1': book},
                                     now_ts=order.placed_ts + 99)
            assert order.terminal_reason == expected
            seen.add(order.terminal_reason)

        c = make_maker_adapter(tmp_path, maker_ttl_seconds=1)
        order = rest_a_bid(c, limit=0.45, shares=20, bids=((0.30, 50.0),))
        touching = make_book(((0.45, 500.0),), bids=((0.44, 10.0),))
        c.observe_resting_orders({'tok-1': touching},
                                 now_ts=order.placed_ts + 99)
        seen.add(order.terminal_reason)                   # touched_not_crossed

        d = make_maker_adapter(tmp_path)
        seen.add(d.cancel_resting_order(rest_a_bid(d).order_id).terminal_reason)

        e = make_maker_adapter(tmp_path)
        halted_order = rest_a_bid(e, limit=0.45, bids=((0.30, 50.0),))
        e_halt = {'v': False}
        pa_module_is_halted = pa_module.is_halted
        try:
            pa_module.is_halted = lambda: e_halt['v']
            e_halt['v'] = True
            e.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        finally:
            pa_module.is_halted = pa_module_is_halted
        seen.add(halted_order.terminal_reason)            # cancelled_by_halt

        f = make_maker_adapter(tmp_path)
        bad = rest_a_bid(f, limit=0.45, bids=((0.30, 50.0),))
        bad.stop_price = 0.45           # not STRICTLY below entry
        f.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        seen.add(bad.terminal_reason)                     # stop_not_below_entry

        g = make_maker_adapter(tmp_path, maker_ttl_seconds=1)
        pos = g.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.60, shares=20,
                                   book=make_book(((0.50, 500.0),)))
        sell = g.simulate_maker_sell(pos.position_id, limit_price=0.60,
                                     book=make_book(((0.65, 50.0),),
                                                    bids=((0.55, 30.0),)))
        g.observe_resting_orders(
            {'tok-1': make_book(((0.75, 10.0),), bids=((0.70, 10.0),))},
            now_ts=sell.placed_ts + 99)
        seen.add(sell.terminal_reason)                    # sell_partial_only

        h = make_maker_adapter(tmp_path)
        pos = h.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.60, shares=20,
                                   book=make_book(((0.50, 500.0),)))
        sell = h.simulate_maker_sell(pos.position_id, limit_price=0.60,
                                     book=make_book(((0.65, 50.0),),
                                                    bids=((0.55, 30.0),)))
        pos.resolution = 'WIN'          # resolved out from under the order
        h.observe_resting_orders(
            {'tok-1': make_book(((0.95, 10.0),), bids=((0.90, 90.0),))})
        seen.add(sell.terminal_reason)                    # position_not_open

        assert seen == set(pa_module.MAKER_NO_FILL_REASONS)


class TestMakerAndTheHalt:
    """Entries blocked, exits not. The same asymmetry the taker pair has."""

    def test_a_halt_cancels_a_resting_buy_instead_of_filling_it(self, tmp_path,
                                                               monkeypatch):
        """A resting bid that fills is a NEW ENTRY, and a halt blocks entries."""
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45, bids=((0.30, 50.0),))
        monkeypatch.setattr(pa_module, 'is_halted', lambda: True)
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        assert order.status == ORDER_CANCELLED
        assert order.terminal_reason == 'maker_cancelled_by_halt'
        assert a.positions == {}

    def test_a_halt_does_not_block_a_resting_sell(self, tmp_path, monkeypatch):
        """A stop that stops working when the kill switch is pulled is not a
        stop. An ask over an open position REDUCES risk."""
        a = make_maker_adapter(tmp_path)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.60, shares=20,
                                   book=make_book(((0.50, 500.0),)))
        order = a.simulate_maker_sell(pos.position_id, limit_price=0.60,
                                      book=make_book(((0.65, 50.0),),
                                                     bids=((0.55, 30.0),)))
        monkeypatch.setattr(pa_module, 'is_halted', lambda: True)
        a.observe_resting_orders(
            {'tok-1': make_book(((0.75, 10.0),), bids=((0.70, 40.0),))})
        assert order.status == ORDER_FILLED
        assert pos.is_open is False


class TestMakerSell:
    """The exit mirror. All or nothing, exactly as the taker sell is."""

    def _open(self, adapter, price=0.50, shares=20):
        return adapter.simulate_taker_buy(
            'strat', 'slug-1', 'tok-1', 'Up', limit_price=price + 0.10,
            shares=shares, book=make_book(((price, 500.0),)))

    def test_a_resting_ask_fills_at_our_own_price(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        pos = self._open(a)
        order = a.simulate_maker_sell(pos.position_id, limit_price=0.60,
                                      reason='profit_target',
                                      book=make_book(((0.65, 50.0),),
                                                     bids=((0.55, 30.0),)))
        a.observe_resting_orders(
            {'tok-1': make_book(((0.75, 10.0),), bids=((0.70, 40.0),))})
        assert order.status == ORDER_FILLED
        assert pos.exit_price == pytest.approx(0.60)
        assert pos.exit_kind == 'sell'
        assert pos.exit_liquidity == 'maker'
        assert pos.exit_reason == 'profit_target'
        # 20 shares bought at 0.50, sold at 0.60.
        assert pos.pnl_usdc == pytest.approx(2.0)
        assert pos.resolution == 'WIN'

    def test_a_partial_sell_is_refused_and_keeps_resting(self, tmp_path):
        """Same rule the taker sell already has, for the same reason: a
        strategy whose thesis is "we exit before resolution" has to make the
        case where it CANNOT exit loud rather than rounding it smaller."""
        a = make_maker_adapter(tmp_path)
        pos = self._open(a)
        order = a.simulate_maker_sell(pos.position_id, limit_price=0.60,
                                      book=make_book(((0.65, 50.0),),
                                                     bids=((0.55, 30.0),)))
        a.observe_resting_orders(
            {'tok-1': make_book(((0.75, 10.0),), bids=((0.70, 9.0),))})
        assert order.status == ORDER_RESTING
        assert pos.is_open is True
        assert a.maker_counts == {'observed:sell_partial_refused': 1}

    def test_a_marketable_ask_is_a_post_only_reject(self, tmp_path):
        """An ask at or below the bid is a taker sell wearing a maker label."""
        a = make_maker_adapter(tmp_path)
        pos = self._open(a)
        assert a.simulate_maker_sell(
            pos.position_id, limit_price=0.55,
            book=make_book(((0.65, 50.0),), bids=((0.55, 30.0),))) is None
        assert a.decision_counts['SKIP:maker_would_cross_book'] == 1

    def test_only_one_resting_sell_per_position(self, tmp_path):
        """Two would sell the same shares twice the moment both crossed."""
        a = make_maker_adapter(tmp_path)
        pos = self._open(a)
        book = make_book(((0.65, 50.0),), bids=((0.55, 30.0),))
        assert a.simulate_maker_sell(pos.position_id, 0.60, book=book)
        assert a.simulate_maker_sell(pos.position_id, 0.61, book=book) is None
        assert a.decision_counts['SKIP:sell_already_resting'] == 1

    def test_selling_more_than_we_hold_is_refused(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        pos = self._open(a, shares=20)
        assert a.simulate_maker_sell(
            pos.position_id, 0.60, shares=50,
            book=make_book(((0.65, 50.0),), bids=((0.55, 30.0),))) is None
        assert a.decision_counts['SKIP:invalid_sell_size'] == 1

    def test_selling_a_closed_position_is_refused(self, tmp_path, monkeypatch):
        a = make_maker_adapter(tmp_path)
        pos = self._open(a)
        monkeypatch.setattr(pa_module, 'resolution_price',
                            lambda c, s, o: WINNING_REDEMPTION)
        a.resolve_positions()
        assert a.simulate_maker_sell(
            pos.position_id, 0.60,
            book=make_book(((0.65, 50.0),), bids=((0.55, 30.0),))) is None
        assert a.decision_counts['SKIP:position_not_open'] == 1

    def test_an_unknown_position_still_leaves_a_row(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        assert a.simulate_maker_sell('nope', 0.60) is None
        assert a.decision_counts == {'SKIP:unknown_position': 1}


class TestMakerAccountingIsTheSamePlumbing:
    """A maker fill must feed the P&L the taker path already feeds."""

    def _filled(self, tmp_path, **cfg):
        a = make_maker_adapter(tmp_path, **cfg)
        order = rest_a_bid(a, limit=0.45, shares=20, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        return a, order, a.positions[order.position_id]

    def test_a_maker_position_resolves_like_any_other(self, tmp_path,
                                                      monkeypatch):
        a, _, pos = self._filled(tmp_path)
        monkeypatch.setattr(pa_module, 'resolution_price',
                            lambda c, s, o: WINNING_REDEMPTION)
        assert a.resolve_positions() == [pos]
        assert pos.pnl_usdc == pytest.approx(20 * 1.00 - 9.0)
        assert pos.exit_kind == 'resolution'

    def test_a_maker_loss_costs_exactly_the_premium(self, tmp_path,
                                                    monkeypatch):
        a, _, pos = self._filled(tmp_path)
        monkeypatch.setattr(pa_module, 'resolution_price',
                            lambda c, s, o: LOSING_REDEMPTION)
        a.resolve_positions()
        assert pos.pnl_usdc == pytest.approx(-9.0)

    def test_equity_and_capital_at_risk_see_a_maker_position(self, tmp_path):
        a, _, pos = self._filled(tmp_path)
        assert a.capital_at_risk() == pytest.approx(9.0)
        assert a.get_equity() == pytest.approx(2000.0 - 9.0)

    def test_an_unfilled_bid_is_committed_capital_and_not_risk(self, tmp_path):
        """It cannot lose anything, so calling it risk overstates exposure."""
        a = make_maker_adapter(tmp_path)
        rest_a_bid(a, limit=0.45, shares=20)
        assert a.capital_at_risk() == pytest.approx(0.0)
        assert a.capital_committed_to_resting_orders() == pytest.approx(9.0)
        assert a.get_equity() == pytest.approx(2000.0)

    def test_build_fill_works_on_a_maker_position(self, tmp_path):
        _, _, pos = self._filled(tmp_path)
        fill = pa_module.PolymarketPaperAdapter.build_fill(
            PolymarketPaperAdapter(client=StubClient()), pos)
        assert fill.avg_price == pytest.approx(0.45)
        assert fill.max_loss_usdc == pytest.approx(9.0)

    def test_a_maker_fee_is_its_own_knob(self, tmp_path):
        """Zero today. `taker_fee_rate` is a different number by construction
        the day a fee schedule exists (convention 17)."""
        a, _, pos = self._filled(tmp_path, maker_fee_rate=0.01)
        assert a.taker_fee_rate == 0.0
        assert pos.fee_usdc == pytest.approx(0.09)
        assert pos.max_loss_usdc == pytest.approx(9.09)

    def test_taker_positions_are_still_labelled_taker(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.60, shares=20,
                                   book=make_book(((0.50, 500.0),)))
        assert pos.entry_liquidity == 'taker'
        assert pos.exit_liquidity is None

    def test_convention_8_the_stop_is_strictly_below_the_entry(self, tmp_path):
        """A losing binary share redeems at exactly 0.00 and the premium IS the
        stop. Strictly below, because a stop equal to the entry is not a stop."""
        _, order, pos = self._filled(tmp_path)
        assert order.stop_price == LOSING_REDEMPTION
        assert order.stop_is_below_entry is True
        assert pos.stop_price < pos.avg_price

    def test_a_stop_not_below_entry_refuses_the_fill(self, tmp_path):
        """Re-checked at fill time, not trusted from the rest-time validation.
        Convention 23: a fix at one site is not a fix."""
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a, limit=0.45, bids=((0.30, 50.0),))
        order.stop_price = 0.45
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        assert order.status == ORDER_CANCELLED
        assert order.terminal_reason == 'stop_not_below_entry'
        assert a.positions == {}


class TestMakerLogging:
    """Convention 20 across the new path."""

    def test_decision_counts_and_row_count_still_agree(self, tmp_path):
        """The accounting identity survives the maker rows."""
        a = make_maker_adapter(tmp_path, maker_ttl_seconds=1)
        order = rest_a_bid(a, limit=0.45, shares=20, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        dead = rest_a_bid(a, limit=0.44, shares=20, bids=((0.30, 50.0),))
        a.expire_resting_orders(now_ts=dead.placed_ts + 99)
        a.log_skip('strat', 'slug-1', 'no_signal')
        assert order.status == ORDER_FILLED
        assert sum(a.decision_counts.values()) == len(log_rows(a))

    def test_observation_counts_are_not_in_the_decision_counts(self, tmp_path):
        """A resting order is looked at every cycle without anything happening
        to it. Folding those looks into decision_counts would break the CSV
        identity and bury the terminal outcomes under thousands of no-ops."""
        a = make_maker_adapter(tmp_path)
        rest_a_bid(a, limit=0.45, bids=((0.30, 50.0),))
        for _ in range(5):
            a.observe_resting_orders({'tok-1': make_book(((0.55, 50.0),))})
        assert a.maker_counts == {'observed:not_touched': 5}
        assert sum(a.decision_counts.values()) == len(log_rows(a)) == 1

    def test_every_maker_row_carries_the_fill_model_name(self, tmp_path):
        """A log row and the rule that produced it must be matchable later."""
        a = make_maker_adapter(tmp_path)
        rest_a_bid(a, limit=0.45, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        for row in log_rows(a):
            assert pa_module.MAKER_FILL_MODEL in row['features']
            assert 'order_kind=maker' in row['features']

    def test_no_new_columns_were_added_to_the_log(self, tmp_path):
        """LOG_COLUMNS is the header of a file already on disk and already
        being read. A new column would misalign every historical row."""
        a = make_maker_adapter(tmp_path)
        rest_a_bid(a, limit=0.45, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        with open(a.log_path, newline='') as f:
            assert next(csv.reader(f)) == LOG_COLUMNS

    def test_the_maker_dispositions_all_reach_the_log(self, tmp_path):
        a = make_maker_adapter(tmp_path, maker_ttl_seconds=1)
        filled = rest_a_bid(a, limit=0.45, shares=20, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        dead = rest_a_bid(a, limit=0.44, shares=20, bids=((0.30, 50.0),))
        a.expire_resting_orders(now_ts=dead.placed_ts + 99)
        pulled = rest_a_bid(a, limit=0.43, shares=20, bids=((0.30, 50.0),))
        a.cancel_resting_order(pulled.order_id)
        assert filled.status == ORDER_FILLED
        assert [r['action'] for r in log_rows(a)] == [
            'REST', 'ENTER', 'REST', 'EXPIRE', 'REST', 'CANCEL']


class TestMakerSerialisation:
    """Convention 19: `allow_nan=False`, and it is load bearing."""

    def test_a_resting_order_round_trips_through_json(self, tmp_path):
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a)
        loaded = json.loads(order.to_json())
        assert loaded['order_id'] == order.order_id
        assert loaded['fill_model'] == pa_module.MAKER_FILL_MODEL
        assert loaded['stop_is_below_entry'] is True
        assert json.loads(a.resting_orders_json())[0]['side'] == 'BUY'

    def test_a_nan_raises_instead_of_writing_a_file_only_python_can_read(
            self, tmp_path):
        """`json.dumps` happily emits a bare NaN token that is not JSON."""
        a = make_maker_adapter(tmp_path)
        order = rest_a_bid(a)
        order.max_through_shares = float('nan')
        with pytest.raises(ValueError):
            order.to_json()
        with pytest.raises(ValueError):
            a.resting_orders_json()


class TestMakerSummary:
    """The maker block is reported apart from the taker numbers."""

    def test_nothing_rested_reports_no_fill_rate_either_way(self, tmp_path):
        """Convention 11 at the summary layer: a strategy that never quoted did
        not fail to get filled."""
        a = make_maker_adapter(tmp_path)
        block = a.summary()['maker']
        assert block['orders_rested'] == 0
        assert block['fill_rate'] is None
        assert block['no_fill_reasons'] == {}

    def test_the_fill_rate_counts_orders_and_the_reasons_are_broken_out(
            self, tmp_path):
        a = make_maker_adapter(tmp_path, maker_ttl_seconds=1)
        filled = rest_a_bid(a, limit=0.45, shares=20, bids=((0.30, 50.0),))
        a.observe_resting_orders({'tok-1': crossing_book(0.40, 60.0)})
        for limit in (0.44, 0.43, 0.42):
            dead = rest_a_bid(a, limit=limit, shares=20, bids=((0.30, 50.0),))
            a.expire_resting_orders(now_ts=dead.placed_ts + 99)
        block = a.summary()['maker']
        assert filled.status == ORDER_FILLED
        assert block['orders_rested'] == 4
        assert block['orders_filled'] == 1
        assert block['fill_rate'] == pytest.approx(0.25)
        assert block['no_fill_reasons'] == {'maker_never_observed': 3}
        assert block['maker_entries'] == 1
        assert block['fill_model'] == pa_module.MAKER_FILL_MODEL

    def test_the_taker_summary_keys_are_untouched(self, tmp_path):
        """Existing readers keep working: the maker block is purely additive."""
        a = make_maker_adapter(tmp_path)
        s = a.summary()
        for key in ('mode', 'halted', 'entries', 'resolved', 'pending', 'wins',
                    'losses', 'win_rate', 'share_weighted_entry_price',
                    'breakeven_win_rate', 'realized_pnl_usdc',
                    'capital_at_risk_usdc', 'equity_usdc', 'closed_early',
                    'by_exit_kind', 'decision_counts', 'log_path', 'note'):
            assert key in s


class TestTheBlockedMakerStrategies:
    """box_builder and grid_hedge, end to end through the new path.

    This is the only evidence that matters for the reason those two files were
    written: their QUOTE legs, unmodified, rested and then filled by the real
    fill rule. Nothing here reaches into the strategies - the legs come out of
    `evaluate()` exactly as the shadow loop would receive them.
    """

    @staticmethod
    def _box_ctx():
        from strategies.polymarket.base import MarketContext
        from engine.polymarket.types import Market, Outcome
        market = Market(id='m', question='q', slug='btc-updown-5m-1000',
                        condition_id='c',
                        outcomes=(Outcome('Up', 'UP'), Outcome('Down', 'DN')))
        books = {'UP': make_book(((0.55, 50.0),), bids=((0.45, 50.0),),
                                 token_id='UP'),
                 'DN': make_book(((0.50, 50.0),), bids=((0.44, 50.0),),
                                 token_id='DN')}
        return MarketContext(window_ts=1000, market=market, books=books,
                             seconds_into_window=10.0)

    def test_box_builder_still_returns_quote_and_never_enter(self):
        """A maker does not enter at decision time. It rests, and finds out."""
        from strategies.polymarket import BoxBuilder
        decision = BoxBuilder().evaluate(self._box_ctx())
        assert decision.action == 'QUOTE'
        assert decision.is_entry is False

    def test_box_builder_legs_now_declare_a_fill_model(self):
        from strategies.polymarket import BoxBuilder
        feats = BoxBuilder().evaluate(self._box_ctx()).features
        assert feats['maker_quote_is_restable'] is True
        assert feats['maker_fill_model_available'] == pa_module.MAKER_FILL_MODEL

    @staticmethod
    def _grid_ctx():
        """The book shape that clears every one of grid_hedge's gates."""
        from strategies.polymarket.base import MarketContext
        from engine.polymarket.types import Market, Outcome
        market = Market(id='m', question='q', slug='btc-updown-5m-1699999800',
                        condition_id='c',
                        outcomes=(Outcome('Up', 'UP'), Outcome('Down', 'DN')))
        books = {'UP': make_book(((0.55, 100.0),), bids=((0.53, 100.0),),
                                 token_id='UP'),
                 'DN': make_book(((0.47, 100.0),), bids=((0.45, 100.0),),
                                 token_id='DN')}
        return MarketContext(window_ts=1699999800, market=market, books=books,
                             seconds_into_window=60.0, atr14=5.0,
                             lead_bps=20.0)

    def test_grid_hedge_still_returns_quote_and_never_enter(self):
        from strategies.polymarket import GridHedge
        decision = GridHedge().evaluate(self._grid_ctx())
        assert decision.action == 'QUOTE'
        assert decision.is_entry is False

    def test_grid_hedge_legs_now_declare_a_fill_model(self):
        from strategies.polymarket import GridHedge
        feats = GridHedge().evaluate(self._grid_ctx()).features
        assert feats['maker_quote_is_restable'] is True
        assert feats['maker_fill_model_available'] == pa_module.MAKER_FILL_MODEL
        # NOT cleared. The kill condition needs 50 grid FILLS, and a fill model
        # existing is not 50 fills (convention 11).
        assert feats['kill_condition_blocked_by'] == 'maker_fills_not_simulated'

    def test_a_grid_hedge_rung_rests_and_then_fills(self, tmp_path):
        """A ladder rung, unmodified, through the real fill rule."""
        from strategies.polymarket import GridHedge
        a = make_maker_adapter(tmp_path, notional_cap_usdc=100.0,
                               max_concurrent_positions=20)
        ctx = self._grid_ctx()
        decision = GridHedge().evaluate(ctx)
        assert decision.legs, decision.reason

        rested, refused = [], []
        for leg in decision.legs:
            token = ctx.market.token_id(leg.outcome_side)
            order = a.simulate_maker_buy(
                decision.strategy, decision.market_slug, token,
                leg.outcome_side, limit_price=leg.limit_price,
                shares=leg.shares, window_ts=decision.window_ts,
                book=ctx.books[token])
            (rested if order is not None else refused).append(leg)
            if order is not None and leg.outcome_side == 'Up':
                deep = order
        assert rested, 'no grid rung could rest at all'
        assert a.positions == {}

        # Trade the Up book strictly through the deepest rung we rested.
        a.observe_resting_orders({
            'UP': make_book(((deep.limit_price - 0.02, 500.0),),
                            bids=((deep.limit_price - 0.05, 10.0),),
                            token_id='UP')})
        assert deep.status == ORDER_FILLED
        pos = a.positions[deep.position_id]
        assert pos.strategy == 'PM_grid_hedge'
        assert pos.entry_liquidity == 'maker'
        assert pos.avg_price == pytest.approx(deep.limit_price)

    def test_a_box_builder_quote_rests_and_then_fills(self, tmp_path):
        """The end of `maker_fill_not_simulated` at the adapter layer.

        Both legs rest at the prices box_builder actually quoted, and each fills
        only once its own book trades STRICTLY through that price with more size
        than the queue that was ahead of it.
        """
        from strategies.polymarket import BoxBuilder
        a = make_maker_adapter(tmp_path)
        ctx = self._box_ctx()
        decision = BoxBuilder().evaluate(ctx)
        assert decision.reason == 'maker_fill_not_simulated'

        orders = {}
        for leg in decision.legs:
            token = ctx.market.token_id(leg.outcome_side)
            order = a.simulate_maker_buy(
                decision.strategy, decision.market_slug, token,
                leg.outcome_side, limit_price=leg.limit_price,
                shares=leg.shares, window_ts=decision.window_ts,
                book=ctx.books[token])
            assert order is not None, leg
            orders[leg.outcome_side] = order

        # Nothing has filled yet. A quote is not a fill.
        assert a.positions == {}

        # The Up book touches the quote and never goes through it. The Down book
        # trades through with more size than the queue ahead. One fill, not two.
        up, down = orders['Up'], orders['Down']
        a.observe_resting_orders({
            'UP': make_book(((up.limit_price, 200.0),),
                            bids=((up.limit_price - 0.01, 10.0),),
                            token_id='UP'),
            'DN': make_book(((down.limit_price - 0.05, 200.0),),
                            bids=((down.limit_price - 0.10, 10.0),),
                            token_id='DN'),
        })
        assert up.status == ORDER_RESTING
        assert up.touched is True
        assert down.status == ORDER_FILLED
        assert down.fill_price == pytest.approx(down.limit_price)

        pos = a.positions[down.position_id]
        assert pos.strategy == 'PM_box_builder'
        assert pos.entry_liquidity == 'maker'
        assert pos.stop_price < pos.avg_price
        assert a.summary()['maker']['orders_filled'] == 1


# -- delegation (D-343 R1 residual) -------------------------------------------

def test_paper_adapter_no_longer_defines_its_own_notional_cap(tmp_path):
    """D-343 R1 residual: the adapter's per-trade cap default used to be a bare
    `10.0` inline in `__init__` - a THIRD independent declaration of a number
    `engine.risk.constraints` and `engine.polymarket.risk_gate` had already been
    made to agree on. It is now SOURCED from that module, not redeclared.

    Mirrors `test_pm_gate_no_longer_defines_its_own_notional_caps` in
    `tests/test_polymarket_risk_gate.py`. The structural half carries the weight
    here: unlike the gate's aggregate cap, this number did NOT change value, so
    equality with `DEFAULT_LIMITS` alone would pass just as well against the old
    hardcoded literal and would prove nothing about delegation.
    """
    from engine.risk import constraints as risk_constraints

    assert pa_module.DEFAULT_NOTIONAL_CAP_USDC == pytest.approx(
        risk_constraints.DEFAULT_LIMITS.per_trade_notional_usd)

    # An adapter built with no config override gets the delegated number, not
    # some other default reached by a second path.
    assert make_adapter(tmp_path).notional_cap_usdc == pytest.approx(
        risk_constraints.DEFAULT_LIMITS.per_trade_notional_usd)

    # Structural: the module must SOURCE the number rather than redeclare a
    # literal that merely happens to match today.
    import inspect
    source = inspect.getsource(pa_module)
    assert 'risk_constraints.DEFAULT_LIMITS.per_trade_notional_usd' in source
    assert "cfg.get('notional_cap_usdc', 10.0)" not in source


def test_config_yaml_notional_cap_matches_the_delegated_default():
    """Pin the number D-343 R1 ratified on the adapter's CONFIG surface.

    The test above proves the adapter's module DEFAULT delegates. It says
    nothing about `config.yaml`, and a config override WINS over that default
    on every adapter the loop actually builds - so
    `polymarket.notional_cap_usdc` is the last place a stale literal could
    silently win. Per-trade twin of
    `test_config_yaml_max_total_exposure_matches_the_delegated_default` in
    `tests/test_polymarket_risk_gate.py`.
    """
    import yaml
    from engine.risk import constraints as risk_constraints

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(repo_root, 'config.yaml')) as fh:
        cfg = yaml.safe_load(fh)

    assert (cfg['polymarket']['notional_cap_usdc']
            == risk_constraints.DEFAULT_LIMITS.per_trade_notional_usd)

    # The risk GATE's own per-trade surface, pinned explicitly. The scalar
    # loop in `test_config_yaml_matches_the_module_defaults` already covers
    # this transitively - it compares a gate built from config.yaml against a
    # gate built with no config, and that gate default now sources
    # DEFAULT_LIMITS. Verified by reading it, not assumed. This line exists so
    # a future reader sees BOTH config surfaces pinned to one number in one
    # place, rather than having to reconstruct the transitive path.
    assert (cfg['polymarket']['risk']['notional_cap_usdc']
            == risk_constraints.DEFAULT_LIMITS.per_trade_notional_usd)


# -- D-382: confidence-based position sizing --------------------------------

class TestConfidenceCurve:
    """The pure mapping, tested without an adapter or a book anywhere near it.

    D-382 R3 left the exact curve to the implementation and required only that
    it be conservative and monotone. These pin the shape that was chosen, so
    changing it is a visible edit to a test rather than a quiet re-parameter-
    isation of every position the book takes.
    """

    def test_the_ruled_knots_map_exactly(self):
        expected = {0.50: 0.01, 0.60: 0.02, 0.70: 0.05,
                    0.80: 0.10, 0.90: 0.20, 0.95: 0.30}
        for conf, pct in expected.items():
            assert pa_module.base_position_pct(conf) == pytest.approx(pct)

    def test_the_curve_constant_and_the_knots_agree(self):
        """The table is the source; a test that restated it would prove nothing."""
        assert dict(pa_module.CONFIDENCE_SIZE_CURVE) == {
            0.50: 0.01, 0.60: 0.02, 0.70: 0.05,
            0.80: 0.10, 0.90: 0.20, 0.95: 0.30}

    def test_it_is_monotone_non_decreasing_across_the_whole_range(self):
        """More confidence never buys a smaller position. R3's one hard shape."""
        pcts = [pa_module.base_position_pct(i / 200.0) for i in range(0, 201)]
        assert all(b >= a for a, b in zip(pcts, pcts[1:]))

    def test_it_interpolates_linearly_between_knots(self):
        """0.65 sits halfway between 2% and 5%."""
        assert pa_module.base_position_pct(0.65) == pytest.approx(0.035)
        assert pa_module.base_position_pct(0.75) == pytest.approx(0.075)

    def test_below_the_first_knot_is_the_floor_not_a_refusal(self):
        """D-382 adds no gate. Sub-0.50 confidence sizes small, it does not skip."""
        for conf in (0.49, 0.10, 0.0, -3.0):
            assert pa_module.base_position_pct(conf) == pa_module.MIN_POSITION_PCT

    def test_above_the_last_knot_is_flat_at_the_top_of_the_curve(self):
        for conf in (0.95, 0.99, 1.0, 7.5):
            assert pa_module.base_position_pct(conf) == pytest.approx(0.30)

    def test_a_missing_or_unusable_confidence_is_the_floor_and_never_raises(self):
        """Conservative and non-crashing: the smallest size, not an exception."""
        for conf in (None, float('nan'), float('inf'), 'not-a-number', object()):
            assert pa_module.base_position_pct(conf) == pa_module.MIN_POSITION_PCT

    def test_the_top_of_the_curve_is_far_under_the_ninety_percent_ceiling(self):
        """The ceiling is a limit, not a target (D-382 R2/R4).

        `confidence` is a model output, not a measured win rate, so the curve
        deliberately stops at 30% rather than running up to the 90% the ruling
        permits.
        """
        assert max(p for _, p in pa_module.CONFIDENCE_SIZE_CURVE) == 0.30


class TestWinRateFactor:
    """The multiplier can only ever shrink a position, never inflate one."""

    def test_the_table_is_empty_because_no_win_rate_is_ratified(self):
        assert pa_module.DEFAULT_STRATEGY_WIN_RATES == {}

    def test_an_unknown_win_rate_does_not_penalise_the_curve(self):
        assert pa_module.win_rate_factor(None) == 1.0

    def test_it_spans_the_floor_to_one(self):
        assert pa_module.win_rate_factor(0.0) == pytest.approx(0.5)
        assert pa_module.win_rate_factor(0.5) == pytest.approx(0.75)
        assert pa_module.win_rate_factor(1.0) == pytest.approx(1.0)

    def test_it_never_leaves_the_range_even_for_a_nonsense_input(self):
        """A caller bug clamps; it does not raise inside a sizing path."""
        assert pa_module.win_rate_factor(1.4) == pytest.approx(1.0)
        assert pa_module.win_rate_factor(-2.0) == pytest.approx(0.5)
        for bad in (float('nan'), 'x', object()):
            assert pa_module.win_rate_factor(bad) == 1.0

    def test_a_measured_win_rate_shrinks_the_position(self, tmp_path):
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0)
        full = a.confidence_position_pct(0.80)
        halved = a.confidence_position_pct(0.80, win_rate=0.0)
        assert full == pytest.approx(0.10)
        assert halved == pytest.approx(0.05)

    def test_the_per_strategy_table_feeds_the_factor(self, tmp_path):
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0)
        a.strategy_win_rates['weak'] = 0.0
        assert a.confidence_position_pct(0.80, 'weak') == pytest.approx(0.05)
        assert a.confidence_position_pct(0.80, 'unknown') == pytest.approx(0.10)


class TestConfidenceSizingCeiling:
    """D-382 R2/R4: 90% is the hard ceiling and nothing gets past it."""

    def test_the_ceiling_holds_even_if_the_curve_is_raised_past_it(self, tmp_path,
                                                                  monkeypatch):
        """The clamp is applied LAST, so it survives a re-parameterised curve."""
        monkeypatch.setattr(pa_module, 'CONFIDENCE_SIZE_CURVE',
                            ((0.50, 0.10), (0.95, 5.0)))
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0)
        for conf in (0.5, 0.7, 0.95, 1.0, 99.0):
            assert a.confidence_position_pct(conf) <= a.max_position_pct

    def test_a_configured_ceiling_below_the_curve_wins(self, tmp_path):
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         max_position_pct=0.05)
        assert a.confidence_position_pct(0.95) == pytest.approx(0.05)

    def test_a_ceiling_below_the_floor_still_wins(self, tmp_path):
        """The ruling's limit outranks the not-worth-trading convenience."""
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         max_position_pct=0.001)
        assert a.confidence_position_pct(0.95) == pytest.approx(0.001)

    def test_the_budget_never_exceeds_the_d366_max_position_cost(self, tmp_path):
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0)
        for conf in (0.0, 0.5, 0.7, 0.9, 0.95, 1.0):
            assert a.sizing_budget_usdc(conf) <= a.max_position_cost() + 1e-9


class TestConfidenceSizingOnEntry:
    """The taker path. D-382 R1: the $10 flat order size is REPLACED."""

    def test_the_default_mode_is_confidence(self, tmp_path):
        assert pa_module.DEFAULT_POSITION_SIZING_MODE == 'confidence'
        assert make_adapter(tmp_path).position_sizing_mode == 'confidence'

    def test_a_confident_signal_sizes_up_past_the_ten_dollar_cap(self, tmp_path):
        """The ruling in one test: $10 was too low, 0.70 confidence buys $50."""
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         notional_cap_usdc=10.0)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=20,
                                   book=make_book([(0.50, 5000)]),
                                   confidence=0.70)
        assert pos is not None
        assert pos.shares == 100                     # 5% of $1,000 at 50c
        assert pos.cost_usdc == pytest.approx(50.0)

    def test_a_more_confident_signal_buys_more(self, tmp_path):
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         notional_cap_usdc=10.0)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=20,
                                   book=make_book([(0.50, 5000)]),
                                   confidence=0.95)
        assert pos.shares == 600                     # 30% of $1,000 at 50c
        assert pos.cost_usdc == pytest.approx(300.0)
        assert pos.cost_usdc <= a.starting_equity * a.max_position_pct

    def test_a_low_confidence_signal_sizes_at_the_one_percent_floor(self, tmp_path):
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         notional_cap_usdc=10.0)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=20,
                                   book=make_book([(0.50, 5000)]),
                                   confidence=0.50)
        assert pos.shares == 20                      # 1% of $1,000 at 50c
        assert pos.cost_usdc == pytest.approx(10.0)

    def test_the_resize_is_counted_in_sizing_counts_not_decision_counts(self, tmp_path):
        """Same accounting identity D-366 protects: one decision count per row."""
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         notional_cap_usdc=10.0)
        a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                             limit_price=0.50, shares=20,
                             book=make_book([(0.50, 5000)]), confidence=0.70)
        assert a.sizing_counts == {'taker_sized_up_at_confidence': 1}
        assert a.decision_counts == {'ENTER': 1}
        assert len(log_rows(a)) == sum(a.decision_counts.values())

    def test_a_size_down_is_counted_under_its_own_key(self, tmp_path):
        """The gate asked for more than 1% of the book; confidence trimmed it."""
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         notional_cap_usdc=1000.0)
        a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                             limit_price=0.50, shares=200,
                             book=make_book([(0.50, 5000)]), confidence=0.50)
        assert a.sizing_counts == {'taker_sized_down_at_confidence': 1}
        assert a.decision_counts == {'ENTER': 1}

    def test_the_log_row_keeps_both_the_request_and_the_fill(self, tmp_path):
        """D-366's `requested_shares`/`filled_shares` semantics, preserved."""
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         notional_cap_usdc=10.0)
        a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                             limit_price=0.50, shares=20,
                             book=make_book([(0.50, 5000)]), confidence=0.70)
        row = log_rows(a)[0]
        assert float(row['requested_shares']) == 20
        assert float(row['filled_shares']) == 100

    def test_confidence_is_read_off_features_when_not_passed_explicitly(self, tmp_path):
        """A caller that was never updated must not silently keep the flat size."""
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         notional_cap_usdc=10.0)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=20,
                                   book=make_book([(0.50, 5000)]),
                                   features={'confidence': 0.70})
        assert pos.shares == 100

    def test_an_explicit_confidence_beats_the_features_dict(self, tmp_path):
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         notional_cap_usdc=10.0)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=20,
                                   book=make_book([(0.50, 5000)]),
                                   features={'confidence': 0.50},
                                   confidence=0.70)
        assert pos.shares == 100

    def test_a_call_with_no_confidence_at_all_is_not_resized(self, tmp_path):
        """Absent is not zero. A caller outside D-382 keeps the gate's size."""
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         notional_cap_usdc=1000.0)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=100,
                                   book=make_book([(0.50, 5000)]))
        assert pos.shares == 100
        assert a.sizing_counts == {}


class TestConfidenceSizingAddsNoRefusal:
    """D-366 R1 survives D-382: the book still never skips for lack of funds."""

    def test_a_bled_book_fills_instead_of_refusing(self, tmp_path):
        """$20 left. 1% is $0.20 and buys nothing, so D-382 steps aside.

        This is the regression that matters most. Measuring the passed-through
        order against a $0.20 budget would re-create `insufficient_capital` by
        accident, under the name `over_notional_cap`.
        """
        a = make_adapter(tmp_path, starting_equity_usdc=20.0,
                         notional_cap_usdc=1000.0)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=20,
                                   book=make_book([(0.50, 5000)]),
                                   confidence=0.50)
        assert pos is not None                       # NOT skipped
        assert pos.shares == 20
        assert a.decision_counts == {'ENTER': 1}
        assert a.sizing_counts == {}                 # D-382 did not apply

    def test_the_only_physical_skip_is_still_the_d366_one(self, tmp_path):
        """90c shares, $2 left: unchanged from D-366, and no new reason."""
        a = make_adapter(tmp_path, starting_equity_usdc=2.0,
                         notional_cap_usdc=1000.0)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.90, shares=20,
                                   book=make_book([(0.90, 500)]),
                                   confidence=0.95)
        assert pos is None
        assert a.decision_counts == {'SKIP:unsizable_at_position_pct': 1}
        assert a.realized_pnl() == 0.0               # not booked as a loss

    def test_no_skip_reason_mentions_confidence_anywhere(self, tmp_path):
        """D-382 introduced no reason string, on any path, at any confidence."""
        seen = {}
        for i, conf in enumerate((0.0, 0.5, 0.7, 0.95, 1.0, None)):
            a = make_adapter(tmp_path / f'r{i}', starting_equity_usdc=1000.0,
                             notional_cap_usdc=1000.0)
            a.simulate_taker_buy('strat', f'slug-{i}', 'tok-1', 'Up',
                                 limit_price=0.50, shares=20,
                                 book=make_book([(0.50, 5000)]),
                                 confidence=conf)
            seen.update(a.decision_counts)
        assert not [k for k in seen if 'confidence' in k]

    def test_the_d366_clip_is_still_reachable_underneath(self, tmp_path):
        """The ceiling still bites a pass-through order, exactly as before."""
        a = make_adapter(tmp_path, starting_equity_usdc=100.0,
                         notional_cap_usdc=1000.0)
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=200,
                                   book=make_book([(0.50, 5000)]))
        assert pos.shares == 180                     # 90% of $100 at 50c
        assert a.sizing_counts == {'taker_capped_at_position_pct': 1}


class TestFlatModeStillReproducesTheOldBook:
    """`flat` keeps the pre-D-382 order size, so the ~2,300 measured trades
    remain reproducible without editing source."""

    def test_flat_mode_ignores_confidence_entirely(self, tmp_path):
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         notional_cap_usdc=10.0,
                         position_sizing_mode='flat')
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=20,
                                   book=make_book([(0.50, 5000)]),
                                   confidence=0.95)
        assert pos.shares == 20
        assert a.sizing_counts == {}

    def test_flat_mode_still_enforces_the_notional_cap(self, tmp_path):
        """`over_notional_cap` is alive and still guards the flat cap."""
        a = make_adapter(tmp_path, starting_equity_usdc=1000.0,
                         notional_cap_usdc=10.0,
                         position_sizing_mode='flat')
        pos = a.simulate_taker_buy('strat', 'slug-1', 'tok-1', 'Up',
                                   limit_price=0.50, shares=100,
                                   book=make_book([(0.50, 5000)]),
                                   confidence=0.95)
        assert pos is None
        assert a.decision_counts == {'SKIP:over_notional_cap': 1}

    def test_an_unknown_mode_refuses_to_construct(self, tmp_path):
        """Neither silent degradation direction is acceptable."""
        with pytest.raises(ValueError):
            make_adapter(tmp_path, position_sizing_mode='kelly')


class TestConfidenceSizingOnTheMakerPath:
    """Both entry paths, same rule (D-382 task 1)."""

    def test_a_resting_bid_sizes_on_confidence(self, tmp_path):
        a = make_maker_adapter(tmp_path, starting_equity_usdc=1000.0,
                               notional_cap_usdc=10.0)
        order = rest_a_bid(a, limit=0.50, shares=20, confidence=0.70)
        assert order is not None
        assert order.shares == 100                   # 5% of $1,000 at 50c
        assert a.sizing_counts == {'maker_sized_up_at_confidence': 1}

    def test_a_resting_bid_reads_confidence_off_features_too(self, tmp_path):
        a = make_maker_adapter(tmp_path, starting_equity_usdc=1000.0,
                               notional_cap_usdc=10.0)
        order = rest_a_bid(a, limit=0.50, shares=20,
                           features={'confidence': 0.70})
        assert order.shares == 100

    def test_a_resting_bid_without_confidence_is_not_resized(self, tmp_path):
        a = make_maker_adapter(tmp_path, starting_equity_usdc=1000.0,
                               notional_cap_usdc=1000.0)
        order = rest_a_bid(a, limit=0.50, shares=20)
        assert order.shares == 20
        assert a.sizing_counts == {}

    def test_the_maker_path_adds_no_refusal_on_a_bled_book(self, tmp_path):
        a = make_maker_adapter(tmp_path, starting_equity_usdc=20.0,
                               notional_cap_usdc=1000.0)
        order = rest_a_bid(a, limit=0.50, shares=20, confidence=0.50)
        assert order is not None
        assert order.shares == 20
        assert a.sizing_counts == {}


def test_d382_leaves_the_real_money_limits_alone():
    """The ruling is a SHADOW sizing change. Real money is untouched.

    Pinned here as well as in the D-366 tests because D-382 is the second
    consecutive ruling to move a number that looks like this one.
    """
    from engine.risk import constraints as risk_constraints
    limits = risk_constraints.DEFAULT_LIMITS
    assert limits.per_trade_notional_usd == 10.0
    assert limits.per_event_notional_usd == 30.0
    assert limits.aggregate_notional_usd == 60.0
    assert limits.max_drawdown_frac == 0.25
