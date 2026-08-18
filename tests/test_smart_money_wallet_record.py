"""Tests for the wallet-record measurement that unblocked `smart_money_copy`.

Offline only. Every resolution lookup here goes through `StubResolver` or a
`MarketResolutionCache` wrapping a fake client. There is no path from this file
to `clob.polymarket.com` and `test_no_network_client_is_ever_constructed` pins
that structurally.

Kept out of `tests/test_smart_money_copy.py` on purpose: that file was being
edited by another session while this was written (convention 21).

## What this file is defending against, in order

1. **A SILENTLY INVERTED SCORER.** This is the failure that would do real
   damage, because it has no symptom. If `won` were flipped, a 40% wallet would
   be reported as a 60% wallet, the gate would open on the worst wallets in the
   list, and every log line would look healthy. `TestPnLMath` pins all four
   (side, outcome) cells by value, `TestInversionGuard` mirrors a resolution and
   asserts EVERY verdict flips, and `test_the_scorer_prefers_the_token_id_to_the
   _outcome_string` pins that a fill is matched on `asset` rather than on a
   display string that a venue can rename.

2. **AN OPEN MARKET SCORED AS A LOSS.** The live CLOB payload ships
   `winner: false` on every token of an unresolved market. A truthiness read of
   that field marks every open position a loss and manufactures a win rate out
   of nothing but market duration. `TestResolutionParsing` asserts `closed` and
   `winner` are two separate requirements.

3. **A SMALL SAMPLE READ AS A RESULT.** Convention 7. `has_sample` and `beats`
   are separate booleans so "9 wins out of 12" cannot be reported as a wallet
   that failed. `TestSampleGate` pins the three skip reasons apart.

4. **A CACHE THAT EXPIRES A RESOLVED MARKET.** A settled binary is immutable and
   re-asking for it every five seconds is how this strategy would eat its own
   latency budget. `TestCache` pins the two-tier ttl.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.market_resolution import (  # noqa: E402
    DEFAULT_MAX_ENTRIES, MarketResolution, MarketResolutionCache,
    RESOLUTION_STATUSES, STATUS_BAD_PAYLOAD, STATUS_FETCH_FAILED,
    STATUS_INCONSISTENT_TOKENS, STATUS_MULTIPLE_WINNERS, STATUS_NO_TOKENS,
    STATUS_NO_WINNER, STATUS_NOT_CLOSED, STATUS_RESOLVED, resolution_from_clob)
from engine.polymarket.types import (Market, Orderbook, Outcome,  # noqa: E402
                                     PriceLevel)
from strategies.polymarket.base import MarketContext, Window  # noqa: E402
import strategies.polymarket.smart_money_copy as smc  # noqa: E402
from strategies.polymarket.smart_money_copy import (  # noqa: E402
    ROW_DROP_REASONS, SmartMoneyCopy, WalletRecord, WalletTrade,
    WalletTradeFeed, record_from_trade_rows, score_trade_row, trade_pnl_usdc,
    wallet_trade_won)

UP_TOK = 'tok-up'
DOWN_TOK = 'tok-down'
COND = 'cond-1'
WINDOW_TS = 1_700_000_000
SLUG = 'btc-updown-5m-1700000000'
ADDR_A = '0x' + 'a1' * 20


# ============ fixtures ============

def _clob_payload(closed=True, winner_outcome='Up', tokens=None, slug='m-slug'):
    """A payload shaped exactly like the live `clob /markets/<cond>` body.

    The real one, captured 2026-08-18, is in the module docstring of
    `engine/polymarket/market_resolution.py`. This mirrors its key set.
    """
    if tokens is None:
        tokens = [
            {'token_id': UP_TOK, 'outcome': 'Up',
             'price': 1 if winner_outcome == 'Up' else 0,
             'winner': closed and winner_outcome == 'Up'},
            {'token_id': DOWN_TOK, 'outcome': 'Down',
             'price': 1 if winner_outcome == 'Down' else 0,
             'winner': closed and winner_outcome == 'Down'},
        ]
    return {'condition_id': COND, 'market_slug': slug, 'closed': closed,
            'active': True, 'archived': False, 'tokens': tokens}


def _resolution(winner_token=UP_TOK, loser_token=DOWN_TOK):
    return resolution_from_clob(
        _clob_payload(winner_outcome='Up' if winner_token == UP_TOK else 'Down'),
        COND)


class StubResolver:
    """condition_id -> MarketResolution. Counts every call. Touches nothing."""

    def __init__(self, table=None, default=None):
        self.table = dict(table or {})
        self.default = default or MarketResolution(
            condition_id='', closed=False, resolved=False,
            status=STATUS_NOT_CLOSED)
        self.calls = []

    def get(self, condition_id):
        self.calls.append(condition_id)
        return self.table.get(str(condition_id), self.default)


class FakeClient:
    """Something with a `.clob` and a `.data`. No session, no sockets."""

    def __init__(self, clob_payloads=None, data_rows=None):
        #: path -> payload. A missing path returns None, which is what
        #: PolymarketClient does on a failed read.
        self.clob_payloads = dict(clob_payloads or {})
        self.data_rows = data_rows
        self.clob_calls = []
        self.data_calls = []
        self.raise_on_clob = False

    def clob(self, path, params=None):
        self.clob_calls.append(path)
        if self.raise_on_clob:
            raise RuntimeError('fake transport blew up')
        return self.clob_payloads.get(path)

    def data(self, path, params=None):
        self.data_calls.append((path, params))
        return self.data_rows


def _row(side='BUY', price=0.60, size=10.0, asset=UP_TOK, cond=COND,
         outcome='Up', **over):
    """A row shaped like a live Data API `/trades` row."""
    row = {'proxyWallet': ADDR_A, 'side': side, 'asset': asset,
           'conditionId': cond, 'outcome': outcome, 'outcomeIndex': 0,
           'price': price, 'size': size, 'timestamp': 1_700_000_000,
           'slug': SLUG, 'transactionHash': '0xabc'}
    row.update(over)
    return row


def _market():
    return Market(id='m1', question='BTC up or down?', slug=SLUG,
                  condition_id=COND,
                  outcomes=(Outcome('Up', UP_TOK), Outcome('Down', DOWN_TOK)))


def _ctx(seconds_into_window=100.0):
    books = {
        UP_TOK: Orderbook(token_id=UP_TOK,
                          bids=(PriceLevel(0.58, 200.0),),
                          asks=(PriceLevel(0.60, 200.0),), timestamp=WINDOW_TS),
        DOWN_TOK: Orderbook(token_id=DOWN_TOK,
                            bids=(PriceLevel(0.40, 200.0),),
                            asks=(PriceLevel(0.42, 200.0),),
                            timestamp=WINDOW_TS),
    }
    return MarketContext(
        window_ts=WINDOW_TS,
        windows=[Window(ts=WINDOW_TS, open=100_000.0, close=100_010.0,
                        direction='UP', source='price')],
        market=_market(), books=books, spot=100_010.0,
        seconds_into_window=seconds_into_window)


def _trade(address=ADDR_A, handle='whale'):
    return WalletTrade(trade_id='t-1', handle=handle, address=address,
                       side='BUY', outcome_side='Up', token_id=UP_TOK,
                       market_slug=SLUG, condition_id=COND, price=0.59,
                       size=5000.0, ts=float(WINDOW_TS) + 70.0)


class RecordFeed:
    """Serves one trade per wallet and one record. The only feed in this file.

    `fetch_trades` filters by ADDRESS. Serving every wallet's trades to every
    wallet would double-count the record-gate buckets and make a two-wallet mix
    test assert the wrong numbers.
    """

    def __init__(self, record=None, trades=None, records=None):
        self.record = record
        self.trades = trades if trades is not None else [_trade()]
        #: address -> WalletRecord or None. Overrides `record` when present.
        self.records = records
        self.record_calls = 0

    def fetch_trades(self, handle, address):
        return [t for t in self.trades if t.address == address], {}

    def fetch_record(self, address):
        self.record_calls += 1
        if self.records is not None:
            return self.records.get(address)
        return self.record


def _strategy(feed, **kw):
    return SmartMoneyCopy(trade_feed=feed, wallets={'whale': ADDR_A}, **kw)


# ============ 1. the PnL arithmetic, all four cells ============

class TestPnLMath:
    """A binary pays exactly 1.00 to the winner and exactly 0.00 to the loser.

    Every cell is asserted by VALUE, not by sign. A sign-only assertion passes
    on a scorer that has the right direction and the wrong magnitude, and the
    magnitude is what the pnl report is made of.
    """

    def test_buy_a_winner_pays_one_minus_premium(self):
        # Paid 0.60 x 100 = 60.00, redeems 1.00 x 100 = 100.00.
        assert trade_pnl_usdc('BUY', 0.60, 100, True) == pytest.approx(40.0)

    def test_buy_a_loser_loses_the_whole_premium(self):
        # Paid 60.00, redeems 0.00. The loss is the premium, never more.
        assert trade_pnl_usdc('BUY', 0.60, 100, False) == pytest.approx(-60.0)

    def test_sell_a_winner_loses_one_minus_premium(self):
        # Received 60.00, owes 100.00.
        assert trade_pnl_usdc('SELL', 0.60, 100, True) == pytest.approx(-40.0)

    def test_sell_a_loser_keeps_the_premium(self):
        # Received 60.00, owes 0.00.
        assert trade_pnl_usdc('SELL', 0.60, 100, False) == pytest.approx(60.0)

    @pytest.mark.parametrize('price', [0.01, 0.25, 0.5, 0.77, 0.99])
    @pytest.mark.parametrize('won', [True, False])
    def test_a_sell_is_exactly_the_negation_of_the_buy(self, price, won):
        """The identity that makes an inversion detectable.

        Implemented as two explicit branches rather than a sign flip, so this
        asserts the two branches agree rather than asserting `-x == -x`.
        """
        buy = trade_pnl_usdc('BUY', price, 33.0, won)
        sell = trade_pnl_usdc('SELL', price, 33.0, won)
        assert sell == pytest.approx(-buy)

    def test_the_two_outcomes_of_one_fill_sum_to_the_full_notional(self):
        # (1-p)*n - (-p*n) = n. If either cell drifts this breaks.
        win = trade_pnl_usdc('BUY', 0.31, 70.0, True)
        lose = trade_pnl_usdc('BUY', 0.31, 70.0, False)
        assert win - lose == pytest.approx(70.0)

    def test_an_unreadable_side_raises_rather_than_defaulting(self):
        # Defaulting an unknown side to BUY would score a coin flip as a
        # measurement. The caller's job is to DROP the row.
        with pytest.raises(ValueError):
            trade_pnl_usdc('LONG', 0.5, 10, True)

    def test_a_scratch_is_not_a_win(self):
        # A fill at exactly 1.00 that wins nets zero. Counting a zero as a win
        # inflates the gate by exactly the number of scratches.
        assert trade_pnl_usdc('BUY', 1.0, 10, True) == pytest.approx(0.0)
        assert wallet_trade_won('BUY', 1.0, 10, True) is False
        assert wallet_trade_won('BUY', 0.99, 10, True) is True


# ============ 2. the inversion guard ============

class TestInversionGuard:
    """Mirror the resolution; every verdict must flip. Nothing may survive."""

    def test_mirroring_the_winner_flips_every_verdict_and_every_sign(self):
        up_won = _resolution(winner_token=UP_TOK)
        down_won = _resolution(winner_token=DOWN_TOK)
        assert up_won.resolved and down_won.resolved

        for tok in (UP_TOK, DOWN_TOK):
            a = up_won.verdict_for_token(tok)
            b = down_won.verdict_for_token(tok)
            assert a is not None and b is not None
            assert a is not b, 'verdict for {} did not flip'.format(tok)

        row = _row(side='BUY', price=0.60, size=100.0, asset=UP_TOK)
        won_a, pnl_a, _ = score_trade_row(row, StubResolver({COND: up_won}))
        won_b, pnl_b, _ = score_trade_row(row, StubResolver({COND: down_won}))
        assert (won_a, won_b) == (True, False)
        assert pnl_a == pytest.approx(40.0)
        assert pnl_b == pytest.approx(-60.0)

    def test_the_scorer_prefers_the_token_id_to_the_outcome_string(self):
        """A renamed display string must not be able to change the verdict.

        The row claims outcome 'Down' while its `asset` is the token that WON.
        Token id is the strong key, so this scores as a win. If the scorer ever
        starts reading the display string first, this flips and fails.
        """
        res = _resolution(winner_token=UP_TOK)
        row = _row(asset=UP_TOK, outcome='Down', price=0.60, size=100.0)
        won, pnl, reason = score_trade_row(row, StubResolver({COND: res}))
        assert reason == 'scored'
        assert won is True and pnl == pytest.approx(40.0)

    def test_the_outcome_string_is_used_only_when_there_is_no_token(self):
        res = _resolution(winner_token=UP_TOK)
        row = _row(asset=None, outcome='Down', price=0.30, size=10.0)
        row.pop('asset')
        won, pnl, reason = score_trade_row(row, StubResolver({COND: res}))
        assert reason == 'scored'
        assert won is False and pnl == pytest.approx(-3.0)

    def test_a_win_rate_is_never_the_complement_of_itself(self):
        """The live-tape sanity check, in miniature.

        Nine of ten fills on the winning token is 90%, not 10%. This is the
        shape of the real 2026-08-18 measurement: Sharky6999 scored 0.985, and
        an inverted scorer would have reported 0.015 for a wallet with a public
        six-figure profit.
        """
        res = _resolution(winner_token=UP_TOK)
        rows = [_row(asset=UP_TOK, price=0.9) for _ in range(9)]
        rows.append(_row(asset=DOWN_TOK, price=0.1))
        rec, _drops = record_from_trade_rows(rows, ADDR_A,
                                             StubResolver({COND: res}))
        assert rec.trades == 10 and rec.wins == 9
        assert rec.win_rate == pytest.approx(0.9)


# ============ 3. parsing a resolution ============

class TestResolutionParsing:

    def test_a_closed_market_with_one_winner_resolves(self):
        res = resolution_from_clob(_clob_payload(winner_outcome='Down'), COND)
        assert res.resolved is True
        assert res.status == STATUS_RESOLVED
        assert res.winning_token_ids == frozenset({DOWN_TOK})
        assert res.losing_token_ids == frozenset({UP_TOK})
        assert res.winning_outcomes == frozenset({'down'})
        assert res.market_slug == 'm-slug'

    def test_an_open_market_is_not_resolved_and_is_not_a_loss(self):
        """The live failure mode. `winner: false` on every token of an OPEN
        market must never be readable as "both outcomes lost"."""
        res = resolution_from_clob(_clob_payload(closed=False), COND)
        assert res.resolved is False
        assert res.status == STATUS_NOT_CLOSED
        # The critical assertion: no verdict, not a False verdict.
        assert res.verdict_for_token(UP_TOK) is None
        assert res.verdict_for_token(DOWN_TOK) is None
        assert res.verdict_for_outcome('Up') is None

    def test_closed_with_no_winner_is_not_resolved(self):
        payload = _clob_payload()
        for tok in payload['tokens']:
            tok['winner'] = False
        res = resolution_from_clob(payload, COND)
        assert res.resolved is False and res.status == STATUS_NO_WINNER

    def test_two_winners_is_refused_rather_than_picked_from(self):
        payload = _clob_payload()
        for tok in payload['tokens']:
            tok['winner'] = True
        res = resolution_from_clob(payload, COND)
        assert res.resolved is False
        assert res.status == STATUS_MULTIPLE_WINNERS

    def test_a_sole_winning_token_with_nothing_to_lose_is_degenerate(self):
        payload = _clob_payload(tokens=[{'token_id': UP_TOK, 'outcome': 'Up',
                                         'price': 1, 'winner': True}])
        res = resolution_from_clob(payload, COND)
        assert res.resolved is False
        assert res.status == STATUS_INCONSISTENT_TOKENS

    def test_no_tokens_is_its_own_status(self):
        payload = _clob_payload()
        payload['tokens'] = []
        assert resolution_from_clob(payload, COND).status == STATUS_NO_TOKENS

    @pytest.mark.parametrize('payload', [None, [], 'nope', 42])
    def test_a_payload_that_is_not_a_dict_is_named_not_raised(self, payload):
        res = resolution_from_clob(payload, COND)
        assert res.resolved is False and res.status == STATUS_BAD_PAYLOAD

    def test_a_truthy_but_not_true_winner_is_not_a_winner(self):
        # `winner: 1` or `winner: 'false'` are schema drift. `is True` refuses
        # both rather than letting a string flip a verdict.
        payload = _clob_payload()
        payload['tokens'][0]['winner'] = 1
        payload['tokens'][1]['winner'] = False
        assert resolution_from_clob(payload, COND).status == STATUS_NO_WINNER

    def test_closed_must_be_exactly_true(self):
        payload = _clob_payload()
        payload['closed'] = 'true'
        assert resolution_from_clob(payload, COND).status == STATUS_NOT_CLOSED

    def test_a_token_not_in_the_market_gets_no_verdict(self):
        res = _resolution()
        assert res.verdict_for_token('some-other-token') is None

    def test_outcome_matching_is_case_and_whitespace_insensitive(self):
        res = _resolution(winner_token=UP_TOK)
        assert res.verdict_for_outcome('  UP  ') is True
        assert res.verdict_for_outcome('down') is False


# ============ 4. the cache ============

class TestCache:

    def _cache(self, payloads=None, **kw):
        client = FakeClient(clob_payloads=payloads if payloads is not None
                            else {'/markets/' + COND: _clob_payload()})
        return MarketResolutionCache(client=client, **kw), client

    def test_a_miss_fetches_and_a_hit_does_not(self):
        cache, client = self._cache()
        first = cache.get(COND)
        assert first.resolved is True
        assert len(client.clob_calls) == 1
        assert cache.health['cache_miss'] == 1

        second = cache.get(COND)
        assert second is first
        # THE point of the cache: no second request.
        assert len(client.clob_calls) == 1
        assert cache.health['cache_hit_resolved'] == 1

    def test_a_resolved_entry_never_expires(self):
        now = [1000.0]
        cache, client = self._cache(clock=lambda: now[0], pending_ttl_sec=1.0,
                                    failure_ttl_sec=1.0)
        cache.get(COND)
        now[0] += 10 ** 9      # ~30 years later
        cache.get(COND)
        assert len(client.clob_calls) == 1, 'a settled binary is immutable'
        assert cache.health['cache_expired'] == 0

    def test_an_unresolved_entry_expires_on_the_pending_ttl(self):
        now = [1000.0]
        cache, client = self._cache(
            payloads={'/markets/' + COND: _clob_payload(closed=False)},
            clock=lambda: now[0], pending_ttl_sec=120.0)
        assert cache.get(COND).resolved is False
        now[0] += 119.0
        cache.get(COND)
        assert len(client.clob_calls) == 1
        now[0] += 2.0
        cache.get(COND)
        assert len(client.clob_calls) == 2
        assert cache.health['cache_expired'] == 1

    def test_an_open_market_that_settles_is_picked_up_after_the_ttl(self):
        now = [1000.0]
        client = FakeClient(clob_payloads={
            '/markets/' + COND: _clob_payload(closed=False)})
        cache = MarketResolutionCache(client=client, clock=lambda: now[0],
                                      pending_ttl_sec=10.0)
        assert cache.get(COND).resolved is False
        client.clob_payloads['/markets/' + COND] = _clob_payload(closed=True)
        now[0] += 11.0
        assert cache.get(COND).resolved is True

    def test_a_failed_fetch_uses_the_failure_ttl_not_the_pending_one(self):
        now = [1000.0]
        cache, client = self._cache(payloads={}, clock=lambda: now[0],
                                    pending_ttl_sec=10_000.0,
                                    failure_ttl_sec=30.0)
        res = cache.get(COND)
        assert res.status == STATUS_FETCH_FAILED
        now[0] += 31.0
        cache.get(COND)
        # Retried on the SHORT ttl. Pooling the two would make one transport
        # blip look like a market that is open for three hours.
        assert len(client.clob_calls) == 2

    def test_a_client_that_raises_is_a_transport_failure_not_a_resolution(self):
        cache, client = self._cache()
        client.raise_on_clob = True
        res = cache.get(COND)
        assert res.resolved is False and res.status == STATUS_FETCH_FAILED
        assert cache.health['fetch_raised'] == 1

    def test_no_client_means_no_lookup_and_no_network(self):
        cache = MarketResolutionCache(client=None)
        res = cache.get(COND)
        assert res.status == STATUS_FETCH_FAILED
        assert cache.health['no_client'] == 1

    def test_a_client_without_a_clob_method_is_refused_not_crashed_on(self):
        cache = MarketResolutionCache(client=object())
        assert cache.get(COND).status == STATUS_FETCH_FAILED
        assert cache.health['client_has_no_clob'] == 1

    def test_get_many_dedupes_so_seven_whales_are_one_fetch(self):
        cache, client = self._cache()
        out = cache.get_many([COND, COND, COND, None, '', COND])
        assert set(out) == {COND}
        assert len(client.clob_calls) == 1

    def test_the_cache_is_bounded_and_eviction_is_counted(self):
        payloads = {'/markets/c%d' % i: _clob_payload() for i in range(10)}
        cache, _client = self._cache(payloads=payloads, max_entries=4)
        for i in range(10):
            cache.get('c%d' % i)
        assert len(cache) == 4
        assert cache.health['cache_evicted'] == 6

    def test_the_default_bound_is_finite(self):
        assert 0 < DEFAULT_MAX_ENTRIES < 10 ** 7

    def test_the_census_balances_and_every_status_is_registered(self):
        cache, _client = self._cache(payloads={
            '/markets/a': _clob_payload(),
            '/markets/b': _clob_payload(closed=False),
        })
        for cid in ('a', 'b', 'c'):
            cache.get(cid)
        census = cache.census()
        assert set(census) == set(RESOLUTION_STATUSES)
        assert census[STATUS_RESOLVED] == 1
        assert census[STATUS_NOT_CLOSED] == 1
        assert census[STATUS_FETCH_FAILED] == 1
        assert sum(census.values()) == len(cache)

    def test_invalidate_drops_one_or_all(self):
        cache, client = self._cache()
        cache.get(COND)
        cache.invalidate(COND)
        cache.get(COND)
        assert len(client.clob_calls) == 2
        cache.invalidate()
        assert len(cache) == 0


# ============ 5. scoring a row ============

class TestScoreTradeRow:

    def _resolver(self):
        return StubResolver({COND: _resolution(winner_token=UP_TOK)})

    def test_a_clean_buy_scores(self):
        won, pnl, reason = score_trade_row(_row(), self._resolver())
        assert (won, reason) == (True, 'scored')
        assert pnl == pytest.approx(4.0)

    def test_a_sell_is_dropped_not_inverted(self):
        """A SELL is an EXIT, not a bet against the outcome.

        Counting the SELL leg of a round trip as an independent bet scores one
        trade as one win and one loss regardless of what the market did.
        """
        won, pnl, reason = score_trade_row(_row(side='SELL'), self._resolver())
        assert won is None and pnl is None
        assert reason == 'sell_row_not_an_independent_bet'

    def test_an_open_market_is_unscorable_not_a_loss(self):
        won, _pnl, reason = score_trade_row(_row(), StubResolver())
        assert won is None
        assert reason == 'market_not_resolved'

    @pytest.mark.parametrize('over,expected', [
        ({'conditionId': None}, 'no_condition_id'),
        ({'price': None}, 'unreadable_price'),
        ({'price': 'abc'}, 'unreadable_price'),
        ({'price': float('nan')}, 'unreadable_price'),
        ({'price': 1.4}, 'price_outside_zero_one'),
        ({'price': -0.1}, 'price_outside_zero_one'),
        ({'size': 0}, 'unreadable_size'),
        ({'size': -5}, 'unreadable_size'),
        ({'size': None}, 'unreadable_size'),
        ({'side': 'LONG'}, 'unreadable_side'),
        ({'asset': 'not-in-this-market', 'outcome': 'Sideways'},
         'token_not_in_resolved_market'),
    ])
    def test_every_refusal_has_its_own_named_reason(self, over, expected):
        row = _row(**over)
        if over.get('conditionId', 'keep') is None:
            row.pop('conditionId')
        won, pnl, reason = score_trade_row(row, self._resolver())
        assert won is None and pnl is None
        assert reason == expected
        assert reason in ROW_DROP_REASONS, 'reason not registered'

    def test_a_row_that_is_not_a_dict_is_named(self):
        assert score_trade_row('nope', self._resolver())[2] == 'row_not_a_dict'

    def test_every_registered_reason_is_unique(self):
        assert len(set(ROW_DROP_REASONS)) == len(ROW_DROP_REASONS)


# ============ 6. building the record ============

class TestRecordFromTradeRows:

    def _resolver(self):
        return StubResolver({COND: _resolution(winner_token=UP_TOK)})

    def test_counts_wins_pnl_and_the_mean_entry(self):
        rows = [_row(asset=UP_TOK, price=0.60, size=100.0),   # +40
                _row(asset=UP_TOK, price=0.80, size=100.0),   # +20
                _row(asset=DOWN_TOK, price=0.40, size=100.0)]  # -40
        rec, drops = record_from_trade_rows(rows, ADDR_A, self._resolver())
        assert (rec.trades, rec.wins) == (3, 2)
        assert rec.win_rate == pytest.approx(2 / 3)
        assert rec.pnl_usdc == pytest.approx(20.0)
        assert rec.mean_entry_price == pytest.approx(0.60)
        assert rec.measured is True
        assert rec.source == 'hold_to_resolution_buys'
        assert drops == {}

    def test_edge_over_breakeven_is_the_win_rate_minus_the_premium(self):
        # The 2026-08-18 finding in miniature: a 90% win rate bought at 0.88 is
        # two points of edge, not forty.
        rec = WalletRecord(address=ADDR_A, trades=100, wins=90, source='t',
                           mean_entry_price=0.88)
        assert rec.edge_over_breakeven == pytest.approx(0.02)

    def test_edge_is_none_when_the_premium_is_unknown(self):
        rec = WalletRecord(address=ADDR_A, trades=100, wins=90, source='t')
        assert rec.edge_over_breakeven is None

    def test_unscorable_rows_are_counted_and_not_treated_as_losses(self):
        rows = [_row(asset=UP_TOK), _row(side='SELL'),
                _row(cond='other-cond')]
        rec, drops = record_from_trade_rows(rows, ADDR_A, self._resolver())
        assert (rec.trades, rec.wins) == (1, 1)
        assert drops == {'sell_row_not_an_independent_bet': 1,
                         'market_not_resolved': 1}
        assert rec.drops == drops

    def test_nothing_scorable_is_none_with_the_drops_still_reported(self):
        """None must not swallow the reason. "500 rows, every market open" and
        "the feed returned nothing" need different responses."""
        rows = [_row(cond='open-%d' % i) for i in range(5)]
        rec, drops = record_from_trade_rows(rows, ADDR_A, StubResolver())
        assert rec is None
        assert drops == {'market_not_resolved': 5}

    def test_no_resolver_means_no_record(self):
        rec, drops = record_from_trade_rows([_row()], ADDR_A, None)
        assert rec is None and drops == {}

    def test_a_small_sample_still_produces_a_record(self):
        """Deciding 3 is too few is the GATE's job, not the builder's.

        Returning None here would turn a small sample into an unmeasurable one,
        which is a different fact (convention 11).
        """
        rec, _ = record_from_trade_rows([_row()] * 3, ADDR_A, self._resolver())
        assert rec is not None and rec.trades == 3
        assert rec.has_sample(50) is False


# ============ 7. the sample gate and the 60% boundary ============

class TestSampleGate:

    def _rec(self, trades, wins):
        return WalletRecord(address=ADDR_A, trades=trades, wins=wins,
                            source='hold_to_resolution_buys')

    @pytest.mark.parametrize('trades,expected', [
        (0, False), (49, False), (50, False), (51, True), (500, True)])
    def test_has_sample_is_strictly_greater_than_the_minimum(self, trades,
                                                             expected):
        assert self._rec(trades, trades).has_sample(50) is expected

    @pytest.mark.parametrize('trades,wins,expected', [
        (1000, 600, False),    # exactly 60.0% is not ABOVE 60%
        (1000, 601, True),     # one more win and it clears
        (1000, 599, False),
        (1000, 1000, True),
        (1000, 0, False),
    ])
    def test_beats_is_strictly_greater_than_the_threshold(self, trades, wins,
                                                          expected):
        assert self._rec(trades, wins).beats(0.60) is expected

    def test_beats_says_nothing_about_sample_size(self):
        """The whole point of the split. 3 wins out of 3 BEATS the bar and has
        no sample; folding them into one boolean loses which one failed."""
        tiny = self._rec(3, 3)
        assert tiny.beats(0.60) is True
        assert tiny.has_sample(50) is False
        assert tiny.passes(0.60, 50) is False

    def test_an_unmeasured_record_fails_both_legs(self):
        rec = WalletRecord(address=ADDR_A, trades=1000, wins=1000,
                           source='claimed', measured=False)
        assert rec.has_sample(50) is False
        assert rec.beats(0.60) is False
        assert rec.passes() is False

    def test_the_shipped_minimum_is_the_documented_one(self):
        # Convention 17: this is an assumption with an expiry date, and the
        # power arithmetic behind it is in the module docstring. If somebody
        # moves it, they should have to move this line too.
        assert smc.MIN_TRADE_COUNT == 50
        assert smc.MIN_WIN_RATE == pytest.approx(0.60)


# ============ 8. the three skip reasons, kept apart ============

class TestRecordSkipReasons:
    """Three causes, three reasons, three counters. Never pooled."""

    def _skip(self, record):
        return _strategy(RecordFeed(record=record)).evaluate(_ctx())

    def test_unmeasured_when_nothing_could_be_scored(self):
        d = self._skip(None)
        assert d.reason == 'wallet_record_unmeasured'
        assert d.features['wallets_record_unmeasured'] == 1
        assert d.features['wallets_record_insufficient_sample'] == 0
        assert d.features['wallets_record_below_threshold'] == 0

    def test_insufficient_sample_is_its_own_reason_not_a_rejection(self):
        """9 wins out of 12 is a 75% win rate. It is NOT a wallet that passed
        and it is NOT a wallet that failed. It is NOT_TESTED (convention 7)."""
        d = self._skip(WalletRecord(address=ADDR_A, trades=12, wins=9,
                                    source='hold_to_resolution_buys'))
        assert d.reason == 'wallet_record_insufficient_sample'
        assert d.features['wallets_record_insufficient_sample'] == 1
        assert d.features['wallets_record_below_threshold'] == 0
        assert d.features['wallets_record_unmeasured'] == 0
        assert d.features['scored_trades_per_wallet'] == [12]

    def test_below_threshold_is_a_measured_result(self):
        d = self._skip(WalletRecord(address=ADDR_A, trades=400, wins=200,
                                    source='hold_to_resolution_buys'))
        assert d.reason == 'wallet_record_below_threshold'
        assert d.features['wallets_record_below_threshold'] == 1
        assert d.features['wallets_record_insufficient_sample'] == 0
        assert d.features['wallets_record_unmeasured'] == 0

    def test_a_big_enough_sample_above_the_bar_enters(self):
        rec = WalletRecord(address=ADDR_A, trades=400, wins=300,
                           source='hold_to_resolution_buys', pnl_usdc=-732.94,
                           mean_entry_price=0.70)
        d = _strategy(RecordFeed(record=rec)).evaluate(_ctx())
        assert d.action == 'ENTER', d.reason
        assert d.features['wallet_win_rate_measured'] == pytest.approx(0.75)
        assert d.features['wallet_record_pnl_usdc'] == pytest.approx(-732.94)
        assert d.features['wallet_edge_over_breakeven'] == pytest.approx(0.05)
        # The live tape has wallets that clear 60% while LOSING money. The row
        # has to carry that, or a reader of the log cannot see it.
        assert d.features[
            'wallet_record_is_hold_to_resolution_not_their_realized_pnl'] \
            is True

    def test_a_mix_of_causes_gets_its_own_reason(self):
        """Two wallets failing for two different reasons must not be reported
        under either one of them."""
        addr_b = '0x' + 'b2' * 20
        feed = RecordFeed(
            trades=[_trade(address=ADDR_A, handle='a'),
                    _trade(address=addr_b, handle='b')],
            records={ADDR_A: None,
                     addr_b: WalletRecord(address=addr_b, trades=12, wins=9,
                                          source='hold_to_resolution_buys')})
        strat = SmartMoneyCopy(trade_feed=feed,
                               wallets={'a': ADDR_A, 'b': addr_b})
        d = strat.evaluate(_ctx())
        assert d.reason == 'wallet_record_mixed_causes'
        assert d.features['wallets_record_unmeasured'] == 1
        assert d.features['wallets_record_insufficient_sample'] == 1
        assert d.features['wallets_record_below_threshold'] == 0

    def test_the_unmeasured_plus_below_mix_keeps_its_historical_name(self):
        addr_b = '0x' + 'b2' * 20
        feed = RecordFeed(
            trades=[_trade(address=ADDR_A, handle='a'),
                    _trade(address=addr_b, handle='b')],
            records={ADDR_A: None,
                     addr_b: WalletRecord(address=addr_b, trades=400, wins=100,
                                          source='hold_to_resolution_buys')})
        strat = SmartMoneyCopy(trade_feed=feed,
                               wallets={'a': ADDR_A, 'b': addr_b})
        d = strat.evaluate(_ctx())
        assert d.reason == 'wallet_record_mixed_unmeasured_and_below'
        assert d.features['wallets_record_unmeasured'] == 1
        assert d.features['wallets_record_below_threshold'] == 1
        assert d.features['wallets_record_insufficient_sample'] == 0

    def test_every_new_reason_is_classified_by_forge(self):
        """A reason missing from SKIP_CLASSIFICATION makes the classifier go
        red, and an unclassified skip is an uncounted one (convention 20)."""
        from agents.forge_shadow_eval import SKIP_CLASSIFICATION
        for reason in ('wallet_record_insufficient_sample',
                       'wallet_record_mixed_causes',
                       'wallet_record_unmeasured',
                       'wallet_record_below_threshold'):
            assert reason in SKIP_CLASSIFICATION, reason
        # The two NOT_TESTED-shaped ones must not be credited as results.
        assert SKIP_CLASSIFICATION[
            'wallet_record_insufficient_sample'][0] == 'DATA_BLOCKER'
        assert SKIP_CLASSIFICATION[
            'wallet_record_mixed_causes'][0] == 'DATA_BLOCKER'
        # And the measured rejection must not be credited as a blocker.
        assert SKIP_CLASSIFICATION[
            'wallet_record_below_threshold'][0] == 'GENUINE'


# ============ 9. the record cache runs on a slow cadence ============

class TestRecordCadence:

    #: A record that reaches the gate and FAILS it, so every evaluation walks
    #: the record path. A passing record would ENTER once and then short-circuit
    #: at `already_copied_this_trade`, which would make this test pass for the
    #: wrong reason.
    def _failing(self):
        return WalletRecord(address=ADDR_A, trades=400, wins=200, source='t')

    def test_the_record_is_not_rebuilt_every_evaluation(self):
        """The 5-second poll must not trigger a 500-row read plus a CLOB
        lookup per market. That is the whole reason the cache exists."""
        feed = RecordFeed(record=self._failing())
        strat = _strategy(feed)
        for _ in range(20):
            d = strat.evaluate(_ctx())
            assert d.reason == 'wallet_record_below_threshold'
        assert feed.record_calls == 1
        assert strat.record_stats['record_cache_hit'] == 19
        assert strat.record_stats['record_cache_miss'] == 1

    def test_an_unmeasurable_wallet_is_cached_too(self):
        feed = RecordFeed(record=None)
        strat = _strategy(feed)
        for _ in range(5):
            strat.evaluate(_ctx())
        assert feed.record_calls == 1

    def test_the_record_expires_so_a_wallet_can_fall_back_out(self):
        """Not infinite. A wallet that degrades has to be able to drop out of
        the gate without a process restart (convention 17)."""
        now = [1000.0]
        feed = RecordFeed(record=self._failing())
        strat = _strategy(feed, record_ttl_sec=3600.0, clock=lambda: now[0])
        strat.evaluate(_ctx())
        now[0] += 3599.0
        strat.evaluate(_ctx())
        assert feed.record_calls == 1
        now[0] += 2.0
        strat.evaluate(_ctx())
        assert feed.record_calls == 2
        assert strat.record_stats['record_cache_expired'] == 1

    def test_the_default_ttl_is_slow_but_finite(self):
        assert smc.DEFAULT_RECORD_TTL_SEC == pytest.approx(3600.0)
        assert _strategy(RecordFeed()).record_ttl_sec > 60.0

    def test_a_feed_that_raises_is_a_failure_not_a_bad_record(self):
        class Boom(RecordFeed):
            def fetch_record(self, address):
                raise RuntimeError('nope')

        strat = _strategy(Boom())
        d = strat.evaluate(_ctx())
        assert d.reason == 'wallet_record_unmeasured'
        assert strat.record_stats['record_fetch_raised'] == 1


# ============ 10. the feed's two record paths, offline ============

class TestFeedRecordPaths:

    def test_explicit_settlement_keys_win_and_need_no_resolver(self):
        client = FakeClient(data_rows=[{'won': True}, {'won': False}])
        feed = WalletTradeFeed(client=client)
        rec = feed.fetch_record(ADDR_A)
        assert (rec.trades, rec.wins) == (2, 1)
        assert rec.source == 'settled_trade_rows'
        # No resolution lookups were needed.
        assert client.clob_calls == []

    def test_the_live_schema_falls_through_to_the_resolution_scorer(self):
        client = FakeClient(
            data_rows=[_row(asset=UP_TOK, price=0.6, size=100.0)],
            clob_payloads={'/markets/' + COND: _clob_payload()})
        feed = WalletTradeFeed(client=client)
        rec = feed.fetch_record(ADDR_A)
        assert rec is not None
        assert rec.source == 'hold_to_resolution_buys'
        assert (rec.trades, rec.wins) == (1, 1)
        assert client.clob_calls == ['/markets/' + COND]

    def test_a_client_with_no_clob_gets_no_resolver_and_no_crash(self):
        client = type('DataOnly', (), {
            'data': lambda self, path, params=None: [_row()]})()
        feed = WalletTradeFeed(client=client)
        assert feed.resolver is None
        assert feed.fetch_record(ADDR_A) is None
        assert feed.stats['record_no_resolver'] == 1

    def test_a_failed_trades_read_is_not_an_empty_record(self):
        client = FakeClient(data_rows=None)
        feed = WalletTradeFeed(client=client)
        assert feed.fetch_record(ADDR_A) is None
        assert feed.stats['record_fetch_failed'] == 1

    def test_row_drops_are_counted_on_the_feed_too(self):
        client = FakeClient(
            data_rows=[_row(side='SELL'), _row(asset=UP_TOK)],
            clob_payloads={'/markets/' + COND: _clob_payload()})
        feed = WalletTradeFeed(client=client)
        feed.fetch_record(ADDR_A)
        assert feed.stats['row_drop_sell_row_not_an_independent_bet'] == 1


# ============ 11. house rules ============

def test_no_network_client_is_ever_reachable_from_this_file():
    """Structural, not a promise in a comment.

    Asserted over this module's NAMESPACE rather than over its source text.
    Convention 29: `inspect.getsource` re-reads from disk and a source scan for
    the string 'urllib' would also match this docstring, so the check would be
    both spuriously fragile and self-referential.

    Every resolver here is a StubResolver or a MarketResolutionCache over a
    FakeClient, and FakeClient has no session and no socket.
    """
    ns = vars(sys.modules[__name__])
    for name, value in ns.items():
        assert getattr(value, '__name__', None) != 'PolymarketClient', name
        assert getattr(value, '__name__', None) != 'requests', name
    # FakeClient is the only thing handed to a MarketResolutionCache here, and
    # it cannot make a request: no session, no transport, just a dict lookup.
    assert not hasattr(FakeClient(), 'session')


def test_the_payout_constants_are_the_binary_ones():
    assert smc.WINNING_SHARE_PAYOUT_USDC == 1.00
    assert smc.LOSING_SHARE_PAYOUT_USDC == 0.00


def test_the_resolution_module_has_no_write_path():
    """No signer, no POST, no order path - the same structural refusal
    `engine/polymarket/client.py` makes.

    Asserted over the module's attributes, not its prose (convention 29): the
    docstring legitimately contains the word 'signer' while describing the
    absence of one.
    """
    import engine.polymarket.market_resolution as mr
    names = set(dir(mr))
    for forbidden in ('requests', 'post', 'sign', 'signer', 'private_key',
                      'PRIVATE_KEY', 'Account', 'web3', 'eth_account'):
        assert forbidden not in names, forbidden
    # The one outbound call it makes is routed through a caller-supplied
    # client's `.clob`, and the path it builds is a GET path with no verb.
    assert mr.CLOB_MARKET_PATH == '/markets/'
