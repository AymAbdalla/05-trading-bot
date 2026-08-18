"""Tests for PM_smart_money_copy. Offline only.

Every feed here is a stub. A network call from this file is a test bug, and
`StubTradeFeed` is the only thing the strategy is ever given, so there is no
path from a test to `data-api.polymarket.com`.

Four jobs, in descending order of how much they matter:

  1. **The wallet census must balance and must stay categorised.** The shipped
     wallet list is seven display handles and zero addresses. If that ever
     silently became "six handles" because one was dropped in a filter loop,
     the strategy would look identical in a log. `TestWalletResolution` asserts
     the identity `resolved + prefix_only + no_address == len(TRACKED_WALLETS)`
     and asserts the two unresolved buckets never merge (convention 20).

  2. **The win rate gate must never be satisfiable by an assumption.** The one
     way to make this strategy fabricate evidence is to let the article's
     claimed win rate reach `WalletRecord`. `TestWalletRecord` asserts that rows
     without an explicit settlement field produce None (an UNMEASURED record)
     and that None is a refusal, not a default.

  3. **Every named skip must be reachable and distinct.** A pooled reason is a
     missing number. `TestSkipReasons` walks one context per gate.

  4. **`evaluate` never returns None.** Every path, including garbage input.

There is deliberately NO harness sweep here. Per D-268 this strategy is
NOT_TESTED until the resolution-PnL harness exists; running the price-path
harness on it would fabricate numbers.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.types import (Market, Orderbook, Outcome,  # noqa: E402
                                     PriceLevel)
from strategies.polymarket.base import MarketContext, Window  # noqa: E402
import strategies.polymarket.smart_money_copy as smc  # noqa: E402
from strategies.polymarket.smart_money_copy import (  # noqa: E402
    RESOLVED, TRACKED_WALLETS, TRACKED_WALLET_PREFIXES,
    UNRESOLVED_NO_ADDRESS, UNRESOLVED_PREFIX_ONLY, SmartMoneyCopy,
    WalletRecord, WalletTrade, WalletTradeFeed, is_full_address,
    record_from_rows, resolve_tracked_wallets, trade_from_row)

UP_TOK = 'tok-up'
DOWN_TOK = 'tok-down'
WINDOW_TS = 1_700_000_000
SLUG = 'btc-updown-5m-1700000000'
COND = 'cond-1'

ADDR_A = '0x' + 'a1' * 20
ADDR_B = '0x' + 'b2' * 20


# ============ fixtures ============

def _market(slug=SLUG):
    return Market(id='m1', question='BTC up or down?', slug=slug,
                  condition_id=COND,
                  outcomes=(Outcome('Up', UP_TOK), Outcome('Down', DOWN_TOK)))


def _book(token, asks=(), bids=()):
    return Orderbook(
        token_id=token,
        bids=tuple(PriceLevel(float(p), float(s)) for p, s in bids),
        asks=tuple(PriceLevel(float(p), float(s)) for p, s in asks),
        timestamp=WINDOW_TS)


def _ctx(seconds_into_window=100.0, up_asks=((0.60, 200.0),),
         down_asks=((0.42, 200.0),), books=None, market=True):
    if books is None:
        books = {UP_TOK: _book(UP_TOK, up_asks, ((0.58, 200.0),)),
                 DOWN_TOK: _book(DOWN_TOK, down_asks, ((0.40, 200.0),))}
    return MarketContext(
        window_ts=WINDOW_TS,
        windows=[Window(ts=WINDOW_TS, open=100_000.0, close=100_010.0,
                        direction='UP', source='price')],
        market=_market() if market else None,
        books=books,
        spot=100_010.0,
        seconds_into_window=seconds_into_window)


def _now(seconds_into_window=100.0):
    return float(WINDOW_TS) + seconds_into_window


#: `ts=None` has to mean "the row carried NO timestamp", which is a real case
#: the staleness gate must refuse. So the default needs its own sentinel rather
#: than reusing None.
_UNSET = object()


def _trade(handle='whale', address=ADDR_A, side='BUY', outcome='Up',
           token_id=UP_TOK, trade_id='t-1', ts=_UNSET, price=0.59, size=5000.0,
           slug=None, condition_id=None):
    return WalletTrade(trade_id=trade_id, handle=handle, address=address,
                       side=side, outcome_side=outcome, token_id=token_id,
                       market_slug=slug, condition_id=condition_id,
                       price=price, size=size,
                       ts=_now(70.0) if ts is _UNSET else ts)


class StubTradeFeed:
    """The only feed these tests ever use. Touches nothing."""

    def __init__(self, trades=None, records=None, fail_addresses=(),
                 drops=None, raise_on=()):
        #: address -> list of WalletTrade, or None to simulate a failed read
        self.trades = dict(trades or {})
        #: address -> WalletRecord or None (None = unmeasured)
        self.records = dict(records or {})
        self.fail_addresses = set(fail_addresses)
        self.drops = dict(drops or {})
        self.raise_on = set(raise_on)
        self.trade_calls = []
        self.record_calls = []

    def fetch_trades(self, handle, address):
        self.trade_calls.append((handle, address))
        if address in self.raise_on:
            raise RuntimeError('stub feed blew up')
        if address in self.fail_addresses:
            return None, dict(self.drops)
        return list(self.trades.get(address, [])), dict(self.drops)

    def fetch_record(self, address):
        self.record_calls.append(address)
        return self.records.get(address)


def _record(address=ADDR_A, trades=100, wins=70):
    return WalletRecord(address=address, trades=trades, wins=wins,
                        source='test', measured=True)


def _strategy(feed, wallets=None, **kwargs):
    return SmartMoneyCopy(trade_feed=feed,
                          wallets=wallets or {'whale': ADDR_A},
                          **kwargs)


# ============ 0. house rules ============

def test_paper_mode_is_true_in_the_module_and_on_the_class():
    assert smc.PAPER_MODE is True
    assert SmartMoneyCopy(trade_feed=StubTradeFeed()).paper_mode is True


def test_it_holds_to_resolution():
    assert SmartMoneyCopy(trade_feed=StubTradeFeed()).manages_exits is False


def test_the_strategy_name_is_the_one_the_kill_condition_names():
    assert SmartMoneyCopy(trade_feed=StubTradeFeed()).name == \
        'PM_smart_money_copy'


def test_the_module_imports_no_wallet_or_signer():
    with open(smc.__file__) as fh:
        src = fh.read()
    for forbidden in ('eth_account', 'web3', 'private_key', 'PRIVATE_KEY',
                      'sign_typed_data', 'session.post', '.post('):
        assert forbidden not in src, forbidden


# ============ 1. the wallet census ============

class TestWalletResolution:
    """The load-bearing accounting in this file. See the module docstring."""

    def test_every_shipped_wallet_is_a_full_address(self):
        # Resolved 2026-08-18. Provenance for every one of these:
        # research/polymarket_wallets.md. This asserts SHAPE only. A well-formed
        # address is not a correct address, and no test in this file can tell
        # the difference - that check is the round trip in the research log,
        # re-run by a human against the live API (convention 17).
        assert len(TRACKED_WALLETS) == 7
        for handle, address in TRACKED_WALLETS.items():
            assert is_full_address(address), handle

    def test_the_seven_handles_did_not_move(self):
        # The handle set is the input to the research log. If a handle is
        # renamed here, its row in that log stops describing this table.
        assert set(TRACKED_WALLETS) == {
            'bonereaper', '0x50f7', 'boneohio', 'coinfilippe', '0xaaaaa',
            'doggystyie', 'Sharky6999'}

    def test_no_two_handles_share_an_address(self):
        # A copy-paste in the table would double-weight one wallet's trade and
        # look in a log like two whales agreeing.
        addresses = [str(a).lower() for a in TRACKED_WALLETS.values()]
        assert len(set(addresses)) == len(addresses)

    def test_the_census_balances_on_the_shipped_list(self):
        resolved, statuses, counts = resolve_tracked_wallets()
        assert sum(counts.values()) == len(TRACKED_WALLETS)
        assert len(statuses) == len(TRACKED_WALLETS)
        assert resolved == {h: a for h, a in TRACKED_WALLETS.items()}

    def test_prefix_only_and_no_address_are_never_pooled(self):
        # The map is empty since 2026-08-18: `0x50f7` and `0xaaaaa` turned out
        # to be chosen usernames, not truncated addresses (research log
        # section 6). The bucket stays because the census is generic, so what
        # is asserted here is that an EMPTY bucket still balances rather than
        # quietly absorbing the resolved rows.
        _resolved, statuses, counts = resolve_tracked_wallets()
        for handle in TRACKED_WALLET_PREFIXES:
            assert statuses[handle] == UNRESOLVED_PREFIX_ONLY
        assert counts[UNRESOLVED_PREFIX_ONLY] == len(TRACKED_WALLET_PREFIXES)
        assert counts[UNRESOLVED_PREFIX_ONLY] == 0
        assert counts[UNRESOLVED_NO_ADDRESS] == 0
        assert counts[RESOLVED] == len(TRACKED_WALLETS)

    def test_a_full_address_resolves_and_a_prefix_does_not(self):
        # The prefix bucket is now driven by what is in the ADDRESS SLOT, not
        # by the shape of the handle: TRACKED_WALLET_PREFIXES is empty, so a
        # handle that merely LOOKS like hex and carries no address is
        # `no_address` like any other unresolved name. That is the correct
        # reading - `0x50f7` is a username (research log section 6) - and it
        # keeps the two buckets meaning "we have some hex" vs "we have a name".
        resolved, statuses, counts = resolve_tracked_wallets(
            {'good': ADDR_A, 'named': None, '0x50f7': None,
             'truncated': '0xdeadbeef'})
        assert resolved == {'good': ADDR_A}
        assert statuses['good'] == RESOLVED
        assert statuses['named'] == UNRESOLVED_NO_ADDRESS
        assert statuses['0x50f7'] == UNRESOLVED_NO_ADDRESS
        assert statuses['truncated'] == UNRESOLVED_PREFIX_ONLY
        assert sum(counts.values()) == 4

    def test_a_short_hex_string_in_the_address_slot_is_a_prefix_not_an_address(
            self):
        resolved, statuses, _counts = resolve_tracked_wallets(
            {'partial': '0x50f7'})
        assert resolved == {}
        assert statuses['partial'] == UNRESOLVED_PREFIX_ONLY

    @pytest.mark.parametrize('bad', [
        None, '', '0x50f7', 'bonereaper', '0x' + 'z' * 40, '0x' + 'a' * 39,
        '0x' + 'a' * 41, 123,
    ])
    def test_is_full_address_refuses_everything_that_is_not_one(self, bad):
        assert is_full_address(bad) is False

    def test_is_full_address_accepts_a_real_one(self):
        assert is_full_address(ADDR_A) is True
        assert is_full_address('  ' + ADDR_B + '  ') is True


# ============ 2. the record gate ============

class TestWalletRecord:
    """A record must be MEASURED or absent. There is no third state."""

    def test_rows_without_a_settlement_field_cannot_be_measured(self):
        rows = [{'side': 'BUY', 'price': 0.5, 'size': 10},
                {'side': 'SELL', 'price': 0.6, 'size': 10}]
        assert record_from_rows(rows, ADDR_A) is None

    def test_the_live_trades_schema_yields_no_record(self):
        # Shaped like a real /trades row: fills, no outcome. This is why the
        # strategy skips `wallet_record_unmeasured` against the live API.
        rows = [{'proxyWallet': ADDR_A, 'side': 'BUY', 'asset': UP_TOK,
                 'outcome': 'Up', 'price': 0.55, 'size': 120.0,
                 'timestamp': 1700000070, 'transactionHash': '0xabc'}]
        assert record_from_rows(rows, ADDR_A) is None

    def test_a_boolean_settlement_field_is_measured(self):
        rows = [{'won': True}, {'won': True}, {'won': False}]
        rec = record_from_rows(rows, ADDR_A)
        assert rec.measured is True
        assert (rec.trades, rec.wins) == (3, 2)
        assert rec.win_rate == pytest.approx(2 / 3)

    def test_a_numeric_pnl_field_is_measured(self):
        rows = [{'realized_pnl': 1.0}, {'realized_pnl': -1.0},
                {'realized_pnl': 0.0}]
        rec = record_from_rows(rows, ADDR_A)
        # Exactly zero is not a win. A scratch counted as a win inflates the
        # gate by exactly the number of scratches.
        assert (rec.trades, rec.wins) == (3, 1)

    def test_unsettled_rows_are_not_counted_as_losses(self):
        rows = [{'won': True}, {'side': 'BUY'}, {'side': 'BUY'}]
        rec = record_from_rows(rows, ADDR_A)
        assert rec.trades == 1 and rec.wins == 1

    def test_a_non_finite_pnl_row_is_not_counted(self):
        rows = [{'realized_pnl': float('nan')}, {'won': True}]
        rec = record_from_rows(rows, ADDR_A)
        assert rec.trades == 1

    def test_an_unmeasured_record_can_never_pass(self):
        rec = WalletRecord(address=ADDR_A, trades=1000, wins=1000,
                           source='claimed', measured=False)
        assert rec.passes() is False

    @pytest.mark.parametrize('trades,wins,expected', [
        (100, 70, True),     # 70% over 100 trades
        (100, 60, False),    # exactly 60% is not ABOVE 60%
        (50, 40, False),     # 80% but exactly 50 trades is not ABOVE 50
        (51, 41, True),      # one more trade and it clears
        (0, 0, False),
    ])
    def test_the_gate_is_strictly_greater_on_both_legs(self, trades, wins,
                                                       expected):
        assert _record(trades=trades, wins=wins).passes() is expected


# ============ 3. row parsing ============

class TestTradeParsing:

    def _row(self, **over):
        row = {'side': 'BUY', 'outcome': 'Up', 'asset': UP_TOK,
               'timestamp': 1700000070, 'transactionHash': '0xabc',
               'price': 0.55, 'size': 120.0, 'slug': SLUG}
        row.update(over)
        return row

    def test_a_good_row_parses(self):
        trade, drop = trade_from_row(self._row(), 'whale', ADDR_A)
        assert drop is None
        assert trade.is_buy is True
        assert trade.token_id == UP_TOK
        assert trade.ts == 1700000070

    @pytest.mark.parametrize('over,reason', [
        ({'side': 'lend'}, 'unreadable_side'),
        ({'side': None}, 'unreadable_side'),
        ({'outcome': ''}, 'unreadable_outcome'),
        ({'transactionHash': None, 'id': None, 'trade_id': None},
         'no_trade_id'),
    ])
    def test_every_drop_has_its_own_name(self, over, reason):
        trade, drop = trade_from_row(self._row(**over), 'whale', ADDR_A)
        assert trade is None and drop == reason

    def test_a_non_dict_row_is_named_not_crashed(self):
        trade, drop = trade_from_row(['nope'], 'whale', ADDR_A)
        assert trade is None and drop == 'row_not_a_dict'

    def test_a_missing_timestamp_is_none_not_zero(self):
        trade, _drop = trade_from_row(self._row(timestamp=None), 'w', ADDR_A)
        assert trade.ts is None
        # None means CANNOT MEASURE, so the age gate must refuse it rather than
        # read it as brand new.
        assert trade.age_sec(_now()) is None

    def test_a_millisecond_field_is_converted_only_when_named_so(self):
        trade, _drop = trade_from_row(
            self._row(timestamp=None, timestamp_ms=1700000070000), 'w', ADDR_A)
        assert trade.ts == pytest.approx(1700000070.0)


# ============ 4. the feed transport ============

class TestFeedTransport:
    """No network. The session and client paths are exercised with fakes."""

    class FakeResponse:
        def __init__(self, text, status=200):
            self.text = text
            self.status_code = status

    class FakeSession:
        def __init__(self, responses):
            self.responses = list(responses)
            self.calls = []

        def get(self, url, params=None, timeout=None):
            self.calls.append((url, params, timeout))
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    class FakeClient:
        def __init__(self, payload):
            self.payload = payload
            self.calls = []

        def data(self, path, params=None):
            self.calls.append((path, params))
            return self.payload

    def test_it_prefers_the_project_client_when_one_is_given(self):
        client = self.FakeClient([{'side': 'BUY', 'outcome': 'Up',
                                   'asset': UP_TOK, 'timestamp': 1,
                                   'transactionHash': '0x1'}])
        feed = WalletTradeFeed(client=client)
        trades, drops = feed.fetch_trades('whale', ADDR_A)
        assert client.calls[0][0] == '/trades'
        assert client.calls[0][1]['user'] == ADDR_A
        assert len(trades) == 1 and drops == {}

    def test_a_client_failure_returns_none_not_an_empty_list(self):
        feed = WalletTradeFeed(client=self.FakeClient(None))
        trades, _drops = feed.fetch_trades('whale', ADDR_A)
        # None and [] are different facts: unreachable feed vs quiet wallet.
        assert trades is None
        assert feed.stats['fail_network'] == 1

    def test_a_non_list_payload_is_its_own_failure(self):
        feed = WalletTradeFeed(client=self.FakeClient({'error': 'nope'}))
        trades, _drops = feed.fetch_trades('whale', ADDR_A)
        assert trades is None
        assert feed.stats['fail_not_a_list'] == 1

    def test_the_session_path_retries_a_network_error_then_succeeds(self):
        session = self.FakeSession([
            IOError('connection reset'),
            self.FakeResponse('[{"side":"BUY","outcome":"Up","asset":"tok-up",'
                              '"timestamp":1,"transactionHash":"0x1"}]'),
        ])
        client = type('C', (), {'session': session})()
        feed = WalletTradeFeed(client=client, retries=2)
        trades, _drops = feed.fetch_trades('whale', ADDR_A)
        assert len(trades) == 1
        assert feed.stats['retries'] == 1

    def test_the_retry_is_bounded(self):
        session = self.FakeSession([IOError('a'), IOError('b')])
        client = type('C', (), {'session': session})()
        feed = WalletTradeFeed(client=client, retries=2)
        trades, _drops = feed.fetch_trades('whale', ADDR_A)
        assert trades is None
        assert len(session.calls) == 2
        assert feed.stats['fail_network'] == 1

    def test_a_non_finite_json_payload_is_refused(self):
        # Convention 19: json.loads accepts bare NaN. This must not.
        session = self.FakeSession([self.FakeResponse('[{"price": NaN}]')])
        client = type('C', (), {'session': session})()
        feed = WalletTradeFeed(client=client, retries=1)
        trades, _drops = feed.fetch_trades('whale', ADDR_A)
        assert trades is None
        assert feed.stats['fail_non_finite_json'] == 1

    def test_unparseable_json_is_its_own_counter(self):
        session = self.FakeSession([self.FakeResponse('not json at all')])
        client = type('C', (), {'session': session})()
        feed = WalletTradeFeed(client=client, retries=1)
        trades, _drops = feed.fetch_trades('whale', ADDR_A)
        assert trades is None
        assert feed.stats['fail_bad_json'] == 1

    def test_the_default_timeout_is_short(self):
        assert WalletTradeFeed().timeout == pytest.approx(2.0)

    def test_fetch_record_returns_none_on_the_live_trades_schema(self):
        client = self.FakeClient([{'side': 'BUY', 'outcome': 'Up',
                                   'asset': UP_TOK, 'timestamp': 1,
                                   'transactionHash': '0x1'}])
        assert WalletTradeFeed(client=client).fetch_record(ADDR_A) is None


# ============ 5. the entry path ============

class TestEntry:

    def _live(self, **kwargs):
        feed = StubTradeFeed(trades={ADDR_A: [_trade()]},
                             records={ADDR_A: _record()})
        return _strategy(feed, **kwargs), feed

    def test_the_happy_path_fires(self):
        s, _feed = self._live()
        d = s.evaluate(_ctx())

        assert d.action == 'ENTER', d.reason
        assert d.reason == ''
        leg = d.primary_leg
        assert leg.outcome_side == 'Up'
        assert leg.order_type == 'taker'
        assert leg.expected_price == pytest.approx(0.60)
        assert d.features['copied_handle'] == 'whale'

    def test_the_size_is_ours_and_not_the_whales(self):
        s, _feed = self._live()
        d = s.evaluate(_ctx())
        # The stub whale bought 5,000 shares.
        assert d.features['copied_trade_size'] == 5000.0
        assert d.primary_leg.shares <= smc.MAX_SHARES
        assert d.features['notional_usdc'] <= smc.MAX_NOTIONAL_USDC + 1e-9
        assert d.features['copied_size_is_theirs_not_ours'] is True

    def test_the_break_even_is_the_premium_not_the_whales_win_rate(self):
        s, _feed = self._live()
        d = s.evaluate(_ctx())
        assert d.features['breakeven_win_rate'] == pytest.approx(0.60)
        assert d.features['wallet_win_rate_measured'] == pytest.approx(0.70)
        assert d.features['confidence_is_their_measured_win_rate_not_ours'] \
            is True

    def test_the_signal_carries_a_stop_strictly_below_entry(self):
        s, _feed = self._live()
        signal = s.decision_to_signal(s.evaluate(_ctx()))
        # On a binary a losing share is worth exactly 0.00 (convention 8).
        assert signal.stop == 0.0
        assert signal.entry > signal.stop
        assert signal.target == 1.0

    def test_it_matches_on_slug_when_the_row_has_no_token_id(self):
        feed = StubTradeFeed(
            trades={ADDR_A: [_trade(token_id=None, slug=SLUG)]},
            records={ADDR_A: _record()})
        d = _strategy(feed).evaluate(_ctx())
        assert d.action == 'ENTER', d.reason
        assert d.features['match_field'] == 'market_slug'

    def test_it_matches_on_condition_id_as_a_last_resort(self):
        feed = StubTradeFeed(
            trades={ADDR_A: [_trade(token_id=None, slug=None,
                                    condition_id=COND)]},
            records={ADDR_A: _record()})
        d = _strategy(feed).evaluate(_ctx())
        assert d.features['match_field'] == 'condition_id'

    def test_it_mirrors_the_down_side_too(self):
        feed = StubTradeFeed(
            trades={ADDR_A: [_trade(outcome='Down', token_id=DOWN_TOK)]},
            records={ADDR_A: _record()})
        d = _strategy(feed).evaluate(_ctx())
        assert d.action == 'ENTER', d.reason
        assert d.primary_leg.outcome_side == 'Down'

    def test_the_same_whale_trade_is_copied_exactly_once(self):
        s, _feed = self._live()
        first = s.evaluate(_ctx(seconds_into_window=100.0))
        second = s.evaluate(_ctx(seconds_into_window=105.0))
        assert first.action == 'ENTER'
        # A poll loop sees the same row every few seconds. Without dedupe one
        # whale BUY becomes twenty signals that all agree with each other.
        assert second.action == 'SKIP'
        assert second.reason == 'already_copied_this_trade'

    def test_the_record_is_fetched_once_per_address_not_once_per_cycle(self):
        s, feed = self._live()
        for t in (100.0, 105.0, 110.0):
            s.evaluate(_ctx(seconds_into_window=t))
        assert feed.record_calls == [ADDR_A]

    def test_an_unmeasurable_record_is_not_retried_every_cycle(self):
        feed = StubTradeFeed(trades={ADDR_A: [_trade()]}, records={})
        s = _strategy(feed)
        for t in (100.0, 105.0):
            s.evaluate(_ctx(seconds_into_window=t))
        assert feed.record_calls == [ADDR_A]

    def test_every_row_carries_the_provenance_and_census_stamps(self):
        s, _feed = self._live()
        for ctx in (_ctx(), _ctx(market=False), _ctx(seconds_into_window=None)):
            d = s.evaluate(ctx)
            f = d.features
            assert f['claimed_win_rates_are_unverified_vendor_numbers'] is True
            assert f['wallet_handles_are_not_addresses'] is True
            assert f['wallet_resolution_census_balances'] is True
            assert f['wallets_resolved'] + f['wallets_unresolved_prefix_only'] \
                + f['wallets_unresolved_no_address'] == f['tracked_wallets']


# ============ 6. every named skip ============

class TestSkipReasons:
    """One context per gate. A pooled reason is a missing number."""

    def test_the_shipped_wallet_list_now_clears_the_address_gate(self):
        # The honest shipped state as of 2026-08-18. All 7 addresses resolve,
        # so `wallet_address_unresolved` is no longer reachable from the
        # shipped list and the SKIP has MOVED to a later gate. This pins the
        # move: if a future edit blanks the table, this test fails rather than
        # the strategy quietly going back to skipping at gate 2.
        d = SmartMoneyCopy(trade_feed=StubTradeFeed()).evaluate(_ctx())
        assert d.action == 'SKIP'
        assert d.reason != 'wallet_address_unresolved'
        assert d.features['wallets_resolved'] == len(TRACKED_WALLETS)
        assert d.features['wallets_unresolved_prefix_only'] == 0
        assert d.features['wallets_unresolved_no_address'] == 0
        # All 7 were actually QUERIED, which is the thing that could not happen
        # before: the address gate returned before any feed call.
        assert d.features['wallets_queried'] == len(TRACKED_WALLETS)
        assert set(d.features['wallet_statuses'].values()) == {RESOLVED}

    def test_no_market(self):
        d = _strategy(StubTradeFeed()).evaluate(_ctx(market=False))
        assert d.reason == 'no_market'

    def test_no_trade_clock(self):
        feed = StubTradeFeed(trades={ADDR_A: [_trade()]},
                             records={ADDR_A: _record()})
        d = _strategy(feed).evaluate(_ctx(seconds_into_window=None))
        assert d.reason == 'no_trade_clock'

    def test_wallet_feed_unavailable(self):
        feed = StubTradeFeed(fail_addresses={ADDR_A})
        d = _strategy(feed).evaluate(_ctx())
        # Could not run. Never "we looked and the whales were quiet."
        assert d.reason == 'wallet_feed_unavailable'
        assert d.features['wallets_read'] == 0

    def test_a_feed_that_raises_is_a_feed_failure_not_a_crash(self):
        feed = StubTradeFeed(raise_on={ADDR_A})
        d = _strategy(feed).evaluate(_ctx())
        assert d.reason == 'wallet_feed_unavailable'

    def test_no_tracked_wallet_trades(self):
        d = _strategy(StubTradeFeed(trades={ADDR_A: []})).evaluate(_ctx())
        assert d.reason == 'no_tracked_wallet_trades'
        assert d.features['wallets_read'] == 1

    def test_no_trade_in_this_market(self):
        feed = StubTradeFeed(
            trades={ADDR_A: [_trade(token_id='some-other-token',
                                    slug='other-slug')]})
        d = _strategy(feed).evaluate(_ctx())
        assert d.reason == 'no_trade_in_this_market'

    def test_no_tracked_wallet_buy(self):
        feed = StubTradeFeed(trades={ADDR_A: [_trade(side='SELL')]})
        d = _strategy(feed).evaluate(_ctx())
        # A SELL is not a mirrorable BUY, and inverting it would be a different
        # strategy with a different thesis.
        assert d.reason == 'no_tracked_wallet_buy'

    def test_copied_trade_stale(self):
        feed = StubTradeFeed(trades={ADDR_A: [_trade(ts=_now(100.0) - 600.0)]},
                             records={ADDR_A: _record()})
        d = _strategy(feed).evaluate(_ctx())
        # Following a whale 10 minutes late is following the price.
        assert d.reason == 'copied_trade_stale'
        assert d.features['buys_fresh_enough'] == 0

    def test_a_trade_with_no_timestamp_is_stale_not_fresh(self):
        feed = StubTradeFeed(trades={ADDR_A: [_trade(ts=None)]},
                             records={ADDR_A: _record()})
        d = _strategy(feed).evaluate(_ctx())
        assert d.reason == 'copied_trade_stale'
        assert d.features['buys_without_timestamp'] == 1

    def test_wallet_record_unmeasured(self):
        feed = StubTradeFeed(trades={ADDR_A: [_trade()]}, records={})
        d = _strategy(feed).evaluate(_ctx())
        # The single most important refusal in this file: no fallback to the
        # article's claimed number.
        assert d.reason == 'wallet_record_unmeasured'
        assert d.features['wallets_record_unmeasured'] == 1

    def test_wallet_record_below_threshold(self):
        feed = StubTradeFeed(trades={ADDR_A: [_trade()]},
                             records={ADDR_A: _record(trades=100, wins=50)})
        d = _strategy(feed).evaluate(_ctx())
        # A measured record that FAILS is a result. It must never share a
        # bucket with a record we could not measure at all.
        assert d.reason == 'wallet_record_below_threshold'
        assert d.features['wallets_record_below_threshold'] == 1

    def test_the_mixed_case_gets_its_own_third_reason(self):
        feed = StubTradeFeed(
            trades={ADDR_A: [_trade(handle='a', address=ADDR_A,
                                    trade_id='t-a')],
                    ADDR_B: [_trade(handle='b', address=ADDR_B,
                                    trade_id='t-b')]},
            records={ADDR_A: _record(ADDR_A, trades=100, wins=50)})
        s = _strategy(feed, wallets={'a': ADDR_A, 'b': ADDR_B})
        d = s.evaluate(_ctx())
        assert d.reason == 'wallet_record_mixed_unmeasured_and_below'
        assert d.features['wallets_record_unmeasured'] == 1
        assert d.features['wallets_record_below_threshold'] == 1

    def test_no_orderbook(self):
        feed = StubTradeFeed(trades={ADDR_A: [_trade()]},
                             records={ADDR_A: _record()})
        d = _strategy(feed).evaluate(_ctx(books={}))
        assert d.reason == 'no_orderbook'

    def test_no_asks(self):
        feed = StubTradeFeed(trades={ADDR_A: [_trade()]},
                             records={ADDR_A: _record()})
        books = {UP_TOK: _book(UP_TOK, asks=(), bids=((0.58, 200.0),))}
        d = _strategy(feed).evaluate(_ctx(books=books))
        assert d.reason == 'no_asks'

    def test_ask_above_max_entry_price(self):
        feed = StubTradeFeed(trades={ADDR_A: [_trade()]},
                             records={ADDR_A: _record()})
        d = _strategy(feed).evaluate(_ctx(up_asks=((0.97, 200.0),)))
        assert d.reason == 'ask_above_max_entry_price'

    def test_insufficient_book_depth(self):
        feed = StubTradeFeed(trades={ADDR_A: [_trade()]},
                             records={ADDR_A: _record()})
        d = _strategy(feed).evaluate(_ctx(up_asks=((0.60, 6.0),)))
        # A whale's fill against a 6-share top level tells us nothing about
        # what WE can get.
        assert d.reason == 'insufficient_book_depth'

    def test_unsizable_at_notional_cap(self):
        feed = StubTradeFeed(trades={ADDR_A: [_trade()]},
                             records={ADDR_A: _record()})
        d = _strategy(feed, max_notional_usdc=2.0).evaluate(_ctx())
        # Could not run, did not lose (convention 11).
        assert d.reason == 'unsizable_at_notional_cap'
        assert d.features['affordable_shares_at_ask'] < smc.MIN_SHARES

    def test_unfillable_at_cap(self):
        # The depth gate is loosened so the WALK is the thing that refuses.
        # With the default 50-share depth floor this branch is unreachable,
        # which is worth knowing rather than faking.
        feed = StubTradeFeed(trades={ADDR_A: [_trade()]},
                             records={ADDR_A: _record()})
        s = _strategy(feed, min_book_depth_shares=1)
        d = s.evaluate(_ctx(up_asks=((0.60, 3.0),)))
        # A partial fill is not an entry (convention 12).
        assert d.reason == 'unfillable_at_cap'

    def test_every_skip_has_a_non_empty_reason(self):
        contexts = [_ctx(), _ctx(market=False), _ctx(books={}),
                    _ctx(seconds_into_window=None),
                    _ctx(up_asks=((0.97, 200.0),)),
                    _ctx(up_asks=((0.60, 6.0),))]
        for ctx in contexts:
            for feed in (StubTradeFeed(),
                         StubTradeFeed(trades={ADDR_A: [_trade()]},
                                       records={ADDR_A: _record()})):
                d = _strategy(feed).evaluate(ctx)
                if d.action == 'SKIP':
                    assert d.reason, 'a silent skip is a missing number'


# ============ 7. it never returns None, whatever it is handed ============

class TestNeverNone:

    JUNK = [
        MarketContext(window_ts=0),
        MarketContext(window_ts=WINDOW_TS, market=_market(), books={}),
        MarketContext(window_ts=WINDOW_TS, market=_market(),
                      seconds_into_window=-5.0),
        MarketContext(window_ts=WINDOW_TS, market=object(),
                      seconds_into_window=10.0),
    ]

    @pytest.mark.parametrize('ctx', JUNK)
    def test_junk_contexts_still_produce_a_decision(self, ctx):
        feed = StubTradeFeed(trades={ADDR_A: [_trade()]},
                             records={ADDR_A: _record()})
        d = _strategy(feed).evaluate(ctx)
        assert d is not None
        assert d.action in ('ENTER', 'SKIP', 'QUOTE')
        assert d.strategy == 'PM_smart_money_copy'

    def test_a_decision_round_trips_to_a_dict(self):
        feed = StubTradeFeed(trades={ADDR_A: [_trade()]},
                             records={ADDR_A: _record()})
        payload = _strategy(feed).evaluate(_ctx()).to_dict()
        assert payload['strategy'] == 'PM_smart_money_copy'
        assert payload['legs']

    def test_a_skip_never_produces_a_signal(self):
        s = SmartMoneyCopy(trade_feed=StubTradeFeed())
        assert s.decision_to_signal(s.evaluate(_ctx())) is None
