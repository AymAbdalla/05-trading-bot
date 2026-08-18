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
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket import paper_adapter as pa_module
from engine.polymarket.paper_adapter import (LOG_COLUMNS, PaperPosition,
                                             PolymarketPaperAdapter)
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
