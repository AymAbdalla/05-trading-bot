"""Offline tests for engine/feeds/hyperliquid_client.py.

NO NETWORK. Every HTTP interaction goes through a hand-written fake session
injected via the `session=` seam, matching tests/test_strike_proxy.py. The
clearinghouseState fixture in tests/fixtures/ is a REAL response captured live
from api.hyperliquid.xyz on 2026-08-18, trimmed to four positions; the numbers
in it were not invented.

What this file guards:
  * the parser against the real response shape
  * the >$100k notional filter
  * liq_price null preservation (null != 0.0)
  * side derivation from the sign of szi
  * the skip-accounting identity (convention 20)
  * retry/backoff behaviour per status class
  * the sqlite writer, including that NULL survives a round trip
  * wallet discovery, its cache, and its documented failure modes
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.feeds.hyperliquid_client import (  # noqa: E402
    DEFAULT_MIN_NOTIONAL,
    DEFAULT_TOP_N,
    LEADERBOARD_CACHE_WALLETS,
    SKIP_REASONS,
    HyperliquidClient,
    HyperliquidPoller,
    HyperliquidStore,
    PositionRow,
    assert_accounting_identity,
    derive_side,
    load_wallets_file,
    parse_clearinghouse_state,
    resolve_wallets,
)

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'fixtures', 'hyperliquid_clearinghouse_state.json')

WALLET = '0xf5d81a135f756ca16544e53c20fc20643ec3ad53'
TS = 1787000000


@pytest.fixture
def live_state():
    with open(FIXTURE_PATH) as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeResp(object):
    def __init__(self, status_code=200, payload=None, headers=None, raise_json=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self._raise_json = raise_json

    def json(self, **kwargs):
        if self._raise_json is not None:
            raise self._raise_json
        parse_constant = kwargs.get('parse_constant')
        if parse_constant is not None and self._payload == '__nonfinite__':
            return parse_constant('NaN')
        return self._payload


class _FakeSession(object):
    """Returns queued responses in order; the last one repeats."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.posts = []
        self.gets = []

    def _next(self):
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]

    def post(self, url, json=None, timeout=None):
        self.posts.append({'url': url, 'json': json, 'timeout': timeout})
        resp = self._next()
        if isinstance(resp, Exception):
            raise resp
        return resp

    def get(self, url, timeout=None):
        self.gets.append({'url': url, 'timeout': timeout})
        resp = self._next()
        if isinstance(resp, Exception):
            raise resp
        return resp


class _RecordingSleep(object):
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def _client(responses, retries=3):
    """Client with no real sleeping and deterministic jitter."""
    import random
    return HyperliquidClient(session=_FakeSession(responses), retries=retries,
                             sleep_fn=_RecordingSleep(), rng=random.Random(0))


# ---------------------------------------------------------------------------
class TestParsingRealResponse(object):
    """The fixture is a real captured response, not a mock-up."""

    def test_the_fixture_is_shaped_like_the_live_api(self, live_state):
        assert 'assetPositions' in live_state
        assert len(live_state['assetPositions']) == 4
        coins = [ap['position']['coin'] for ap in live_state['assetPositions']]
        assert coins == ['BTC', 'ETH', 'SOL', 'BNB']

    def test_two_of_the_four_positions_clear_the_whale_bar(self, live_state):
        rows, counts = parse_clearinghouse_state(live_state, WALLET, TS)
        assert counts['seen'] == 4
        assert counts['written'] == 2
        assert [r.symbol for r in rows] == ['BTC', 'ETH']

    def test_each_dropped_position_is_categorised(self, live_state):
        _, counts = parse_clearinghouse_state(live_state, WALLET, TS)
        # SOL is in scope but only ~$15k; BNB is ~$12 AND out of scope, and the
        # symbol gate runs first, so these are one of each and never both.
        assert counts['skipped_below_min_notional'] == 1
        assert counts['skipped_symbol_out_of_scope'] == 1
        assert counts['skipped_missing_field'] == 0
        assert counts['skipped_unparseable'] == 0

    def test_every_written_field_matches_the_source_payload(self, live_state):
        rows, _ = parse_clearinghouse_state(live_state, WALLET, TS)
        by_symbol = dict((r.symbol, r) for r in rows)
        for ap in live_state['assetPositions']:
            p = ap['position']
            if p['coin'] not in by_symbol:
                continue
            row = by_symbol[p['coin']]
            assert row.size_usd == abs(float(p['positionValue']))
            assert row.entry_price == float(p['entryPx'])
            assert row.leverage == float(p['leverage']['value'])
            assert row.wallet == WALLET
            assert row.ts == TS


class TestNotionalFilter(object):
    def _state(self, position_value):
        return {'assetPositions': [{'position': {
            'coin': 'BTC', 'szi': '1.0', 'entryPx': '64000',
            'positionValue': str(position_value), 'liquidationPx': '1.0',
            'leverage': {'type': 'cross', 'value': 3}}}]}

    def test_exactly_at_the_threshold_is_kept(self):
        rows, counts = parse_clearinghouse_state(self._state(100000.0), WALLET, TS)
        assert counts['written'] == 1
        assert rows[0].size_usd == 100000.0

    def test_a_cent_under_the_threshold_is_dropped(self):
        rows, counts = parse_clearinghouse_state(self._state(99999.99), WALLET, TS)
        assert rows == []
        assert counts['skipped_below_min_notional'] == 1

    def test_a_short_is_measured_on_absolute_notional(self):
        # positionValue is already unsigned on the live API, but a defensive
        # abs() means a signed feed could never sneak a -$5M position past the
        # floor by comparing a negative number.
        rows, counts = parse_clearinghouse_state(self._state(-250000.0), WALLET, TS)
        assert counts['written'] == 1
        assert rows[0].size_usd == 250000.0

    def test_the_threshold_is_configurable(self):
        rows, _ = parse_clearinghouse_state(self._state(5000.0), WALLET, TS,
                                            min_notional=1000.0)
        assert len(rows) == 1

    def test_default_threshold_is_one_hundred_thousand(self):
        assert DEFAULT_MIN_NOTIONAL == 100_000.0

    def test_symbols_are_configurable(self):
        state = {'assetPositions': [{'position': {
            'coin': 'DOGE', 'szi': '1.0', 'entryPx': '1',
            'positionValue': '500000', 'liquidationPx': None}}]}
        rows, _ = parse_clearinghouse_state(state, WALLET, TS, symbols=('DOGE',))
        assert len(rows) == 1
        rows, counts = parse_clearinghouse_state(state, WALLET, TS)
        assert rows == [] and counts['skipped_symbol_out_of_scope'] == 1


class TestNullLiquidationPrice(object):
    """null means 'cannot be liquidated'. 0.0 would mean 'liquidates at zero'."""

    def test_the_live_eth_position_really_does_have_a_null_liq_price(self, live_state):
        eth = [ap['position'] for ap in live_state['assetPositions']
               if ap['position']['coin'] == 'ETH'][0]
        assert eth['liquidationPx'] is None

    def test_null_is_preserved_as_none_not_zero(self, live_state):
        rows, _ = parse_clearinghouse_state(live_state, WALLET, TS)
        eth = [r for r in rows if r.symbol == 'ETH'][0]
        assert eth.liq_price is None
        assert eth.liq_price != 0.0

    def test_a_present_liq_price_is_still_parsed(self, live_state):
        rows, _ = parse_clearinghouse_state(live_state, WALLET, TS)
        btc = [r for r in rows if r.symbol == 'BTC'][0]
        assert isinstance(btc.liq_price, float)
        assert btc.liq_price > 0

    def test_a_null_liq_price_does_not_drop_the_position(self, live_state):
        # The position is still a whale position; only the liq column is absent.
        _, counts = parse_clearinghouse_state(live_state, WALLET, TS)
        assert counts['skipped_missing_field'] == 0

    def test_a_missing_leverage_block_is_none_not_zero(self):
        state = {'assetPositions': [{'position': {
            'coin': 'BTC', 'szi': '1.0', 'entryPx': '64000',
            'positionValue': '250000', 'liquidationPx': None}}]}
        rows, counts = parse_clearinghouse_state(state, WALLET, TS)
        assert counts['written'] == 1
        assert rows[0].leverage is None


class TestSideDerivation(object):
    def test_negative_signed_size_is_short(self):
        assert derive_side(-4.16563) == 'SHORT'

    def test_positive_signed_size_is_long(self):
        assert derive_side(81.4769) == 'LONG'

    def test_the_live_btc_short_and_eth_long_are_read_correctly(self, live_state):
        rows, _ = parse_clearinghouse_state(live_state, WALLET, TS)
        sides = dict((r.symbol, r.side) for r in rows)
        assert sides == {'BTC': 'SHORT', 'ETH': 'LONG'}

    def test_side_comes_from_szi_not_from_notional(self):
        # positionValue is unsigned on the live API, so a parser that derived
        # side from it would call every position LONG.
        state = {'assetPositions': [{'position': {
            'coin': 'BTC', 'szi': '-2.0', 'entryPx': '64000',
            'positionValue': '128000', 'liquidationPx': '1.0',
            'leverage': {'type': 'cross', 'value': 3}}}]}
        rows, _ = parse_clearinghouse_state(state, WALLET, TS)
        assert rows[0].side == 'SHORT'


class TestSkipAccounting(object):
    """Convention 20: no silent continue, identity asserted."""

    MALFORMED = {'assetPositions': [
        {'position': {'coin': 'BTC', 'szi': '1.0', 'entryPx': '64000',
                      'positionValue': '250000', 'liquidationPx': None,
                      'leverage': {'value': 3}}},          # kept
        {'position': {'coin': 'ETH', 'szi': '1.0', 'entryPx': '2000',
                      'positionValue': '10'}},             # below min
        {'position': {'coin': 'DOGE', 'szi': '1.0', 'entryPx': '1',
                      'positionValue': '999999'}},         # out of scope
        {'position': {'coin': 'SOL', 'szi': None, 'entryPx': '76',
                      'positionValue': '250000'}},         # missing field
        {'position': {'coin': 'SOL', 'szi': 'not-a-number', 'entryPx': '76',
                      'positionValue': '250000'}},         # unparseable
        {'position': {'szi': '1.0', 'entryPx': '1', 'positionValue': '250000'}},
                                                            # missing coin
        {'nonsense': True},                                 # unparseable
        'not-a-dict',                                       # unparseable
    ]}

    def test_the_identity_holds_on_a_messy_payload(self):
        _, counts = parse_clearinghouse_state(self.MALFORMED, WALLET, TS)
        assert_accounting_identity(counts)
        assert counts['seen'] == 8

    def test_each_cause_lands_in_exactly_one_bucket(self):
        _, counts = parse_clearinghouse_state(self.MALFORMED, WALLET, TS)
        assert counts['written'] == 1
        assert counts['skipped_below_min_notional'] == 1
        assert counts['skipped_symbol_out_of_scope'] == 1
        assert counts['skipped_missing_field'] == 2   # null szi, absent coin
        assert counts['skipped_unparseable'] == 3     # bad float, no position, str

    def test_the_counts_sum_to_the_number_received(self):
        _, counts = parse_clearinghouse_state(self.MALFORMED, WALLET, TS)
        assert counts['written'] + sum(counts[r] for r in SKIP_REASONS) == counts['seen']

    def test_a_broken_identity_raises_rather_than_being_repaired(self):
        bogus = {'seen': 10, 'written': 1, 'skipped_below_min_notional': 2,
                 'skipped_symbol_out_of_scope': 0, 'skipped_missing_field': 0,
                 'skipped_unparseable': 0}
        with pytest.raises(AssertionError):
            assert_accounting_identity(bogus)

    def test_every_skip_reason_key_is_always_present(self, live_state):
        _, counts = parse_clearinghouse_state(live_state, WALLET, TS)
        for reason in SKIP_REASONS:
            assert reason in counts

    def test_an_unusable_payload_yields_zero_seen_not_a_crash(self):
        for payload in (None, [], 'x', {}, {'assetPositions': 'nope'}):
            rows, counts = parse_clearinghouse_state(payload, WALLET, TS)
            assert rows == []
            assert counts['seen'] == 0
            assert_accounting_identity(counts)


class TestRetryAndBackoff(object):
    def test_a_200_is_returned_without_retrying(self, live_state):
        c = _client([_FakeResp(200, live_state)])
        assert c.clearinghouse_state(WALLET) is not None
        assert len(c.session.posts) == 1
        assert c._sleep.calls == []

    def test_a_500_is_retried_then_succeeds(self, live_state):
        c = _client([_FakeResp(500), _FakeResp(200, live_state)])
        assert c.clearinghouse_state(WALLET) is not None
        assert len(c.session.posts) == 2
        assert len(c._sleep.calls) == 1
        assert c.stats['retries'] == 1

    def test_backoff_grows_exponentially(self):
        c = _client([_FakeResp(503)], retries=4)
        assert c.clearinghouse_state(WALLET) is None
        waits = c._sleep.calls
        assert len(waits) == 3          # never sleeps after the last attempt
        assert waits[0] < waits[1] < waits[2]

    def test_a_persistent_500_gives_up_and_returns_none(self):
        c = _client([_FakeResp(500)], retries=3)
        assert c.clearinghouse_state(WALLET) is None
        assert len(c.session.posts) == 3
        assert c.stats['fail_http_5xx'] == 1

    def test_a_429_is_retried(self, live_state):
        c = _client([_FakeResp(429), _FakeResp(200, live_state)])
        assert c.clearinghouse_state(WALLET) is not None
        assert c.stats['http_429'] == 1

    def test_a_numeric_retry_after_header_is_honoured(self, live_state):
        c = _client([_FakeResp(429, headers={'Retry-After': '7'}),
                     _FakeResp(200, live_state)])
        c.clearinghouse_state(WALLET)
        assert c._sleep.calls == [7.0]

    def test_a_junk_retry_after_header_falls_back_to_backoff(self, live_state):
        c = _client([_FakeResp(429, headers={'Retry-After': 'soon'}),
                     _FakeResp(200, live_state)])
        c.clearinghouse_state(WALLET)
        assert c._sleep.calls[0] < 5.0

    def test_an_absurd_retry_after_is_clamped(self, live_state):
        c = _client([_FakeResp(429, headers={'Retry-After': '99999'}),
                     _FakeResp(200, live_state)])
        c.clearinghouse_state(WALLET)
        assert c._sleep.calls == [30.0]

    def test_a_422_is_definitive_and_not_retried(self):
        # 422 is what /info returns for a bad request type. Retrying it is load
        # with no chance of a different answer.
        c = _client([_FakeResp(422)])
        assert c.clearinghouse_state(WALLET) is None
        assert len(c.session.posts) == 1
        assert c.stats['fail_http_4xx'] == 1

    def test_a_network_exception_is_retried_then_returns_none(self):
        c = _client([IOError('connection reset')], retries=2)
        assert c.clearinghouse_state(WALLET) is None
        assert c.stats['fail_network'] == 1

    def test_unparseable_json_returns_none_not_an_empty_dict(self):
        c = _client([_FakeResp(200, raise_json=ValueError('bad json'))])
        assert c.clearinghouse_state(WALLET) is None
        assert c.stats['fail_bad_json'] == 1

    def test_non_finite_json_is_rejected(self):
        # Convention 19: json.loads accepts NaN by default; a NaN in a REAL
        # column is a silent poison value.
        c = _client([_FakeResp(200, payload='__nonfinite__')])
        assert c.clearinghouse_state(WALLET) is None
        assert c.stats['fail_non_finite_json'] == 1

    def test_the_request_body_is_address_scoped(self, live_state):
        c = _client([_FakeResp(200, live_state)])
        c.clearinghouse_state(WALLET)
        assert c.session.posts[0]['json'] == {'type': 'clearinghouseState',
                                              'user': WALLET}
        assert c.session.posts[0]['timeout'] is not None


class TestStore(object):
    def _store(self, tmp_path):
        return HyperliquidStore(str(tmp_path / 'test.db'))

    def test_schema_is_created_on_a_fresh_db(self, tmp_path):
        store = self._store(tmp_path)
        names = [r[0] for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert 'hyperliquid_positions' in names
        store.close()

    def test_indexes_exist(self, tmp_path):
        store = self._store(tmp_path)
        idx = [r[0] for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
        assert 'idx_hyperliquid_positions_ts' in idx
        assert 'idx_hyperliquid_positions_symbol_ts' in idx
        store.close()

    def test_busy_timeout_is_set(self, tmp_path):
        store = self._store(tmp_path)
        assert store.conn.execute('PRAGMA busy_timeout').fetchone()[0] == 5000
        store.close()

    def test_rows_round_trip_with_null_liq_price_intact(self, tmp_path, live_state):
        store = self._store(tmp_path)
        rows, _ = parse_clearinghouse_state(live_state, WALLET, TS)
        assert store.write_positions(rows) == 2
        got = store.conn.execute(
            'SELECT symbol, side, size_usd, entry_price, liq_price, leverage '
            'FROM hyperliquid_positions ORDER BY symbol').fetchall()
        by_symbol = dict((r['symbol'], r) for r in got)
        assert by_symbol['ETH']['liq_price'] is None      # the whole point
        assert by_symbol['BTC']['liq_price'] is not None
        assert by_symbol['BTC']['side'] == 'SHORT'
        assert by_symbol['ETH']['side'] == 'LONG'
        store.close()

    def test_repeated_polls_append_rather_than_upsert(self, tmp_path):
        store = self._store(tmp_path)
        store.write_positions([PositionRow(TS, WALLET, 'BTC', 'SHORT',
                                           250000.0, 64000.0, 70000.0, 3.0)])
        store.write_positions([PositionRow(TS + 30, WALLET, 'BTC', 'SHORT',
                                           260000.0, 64000.0, 70000.0, 3.0)])
        n = store.conn.execute(
            'SELECT COUNT(*) FROM hyperliquid_positions').fetchone()[0]
        assert n == 2   # time-series, not current-state

    def test_writing_no_rows_is_a_noop(self, tmp_path):
        store = self._store(tmp_path)
        assert store.write_positions([]) == 0
        store.close()

    def test_ensure_schema_is_idempotent(self, tmp_path):
        store = self._store(tmp_path)
        store.ensure_schema()
        store.ensure_schema()
        store.close()

    def test_journal_mode_is_observed_not_forced(self, tmp_path):
        # The module must not switch journal_mode on a db another process holds
        # open. A fresh file defaults to 'delete'; the store must REPORT that
        # rather than silently converting it to WAL.
        store = self._store(tmp_path)
        assert store.journal_mode.lower() == 'delete'
        assert store.conn.execute(
            'PRAGMA journal_mode').fetchone()[0].lower() == 'delete'
        store.close()


class TestPoller(object):
    def test_a_poll_writes_the_parsed_rows(self, tmp_path, live_state):
        client = _client([_FakeResp(200, live_state)])
        store = HyperliquidStore(str(tmp_path / 'p.db'))
        poller = HyperliquidPoller(client, store=store, sleep_fn=_RecordingSleep())
        result = poller.poll_once([WALLET], ts=TS)
        assert result['written'] == 2
        assert result['wallets_ok'] == 1
        assert result['wallets_failed'] == 0
        store.close()

    def test_a_failed_wallet_is_counted_as_failed_not_as_empty(self, tmp_path):
        # Convention 11: "could not run" is never "ran and found nothing".
        client = _client([_FakeResp(500)], retries=1)
        poller = HyperliquidPoller(client, store=None, dry_run=True,
                                   sleep_fn=_RecordingSleep())
        result = poller.poll_once([WALLET], ts=TS)
        assert result['wallets_failed'] == 1
        assert result['wallets_ok'] == 0
        assert result['wallets_empty'] == 0     # NOT conflated with a failure
        assert result['counts']['seen'] == 0
        assert poller.health['wallet_fetch_failed'] == 1

    def test_an_empty_wallet_is_distinct_from_a_failed_one(self):
        # A wallet that answers with zero positions genuinely holds nothing.
        # That is a RESULT. A wallet whose request failed is NOT a result.
        client = _client([_FakeResp(200, {'assetPositions': []})])
        poller = HyperliquidPoller(client, store=None, dry_run=True,
                                   sleep_fn=_RecordingSleep())
        result = poller.poll_once([WALLET], ts=TS)
        assert result['wallets_ok'] == 1
        assert result['wallets_empty'] == 1
        assert result['wallets_failed'] == 0

    def test_dry_run_writes_nothing(self, tmp_path, live_state):
        client = _client([_FakeResp(200, live_state)])
        store = HyperliquidStore(str(tmp_path / 'd.db'))
        poller = HyperliquidPoller(client, store=store, dry_run=True,
                                   sleep_fn=_RecordingSleep())
        result = poller.poll_once([WALLET], ts=TS)
        assert result['written'] == 0
        assert len(result['rows']) == 2      # parsed, just not persisted
        n = store.conn.execute(
            'SELECT COUNT(*) FROM hyperliquid_positions').fetchone()[0]
        assert n == 0
        store.close()

    def test_the_identity_holds_across_multiple_wallets(self, live_state):
        client = _client([_FakeResp(200, live_state)])
        poller = HyperliquidPoller(client, store=None, dry_run=True,
                                   sleep_fn=_RecordingSleep())
        result = poller.poll_once([WALLET, WALLET, WALLET], ts=TS)
        assert_accounting_identity(result['counts'])
        assert result['counts']['seen'] == 12
        assert result['counts']['written'] == 6

    def test_all_rows_in_one_poll_share_a_timestamp(self, live_state):
        client = _client([_FakeResp(200, live_state)])
        poller = HyperliquidPoller(client, store=None, dry_run=True,
                                   sleep_fn=_RecordingSleep())
        result = poller.poll_once([WALLET, WALLET], ts=TS)
        assert set(r.ts for r in result['rows']) == {TS}


class TestWalletResolution(object):
    LEADERBOARD = {'leaderboardRows': [
        {'ethAddress': '0xaaa', 'accountValue': '100'},
        {'ethAddress': '0xbbb', 'accountValue': '900'},
        {'ethAddress': '0xccc', 'accountValue': '500'},
        {'ethAddress': '0xbbb', 'accountValue': '400'},   # duplicate address
        {'ethAddress': None, 'accountValue': '999'},      # unusable
        'not-a-dict',                                     # unusable
    ]}

    def test_leaderboard_is_ranked_by_account_value_and_deduped(self):
        c = _client([_FakeResp(200, self.LEADERBOARD)])
        assert c.fetch_leaderboard_wallets(top_n=3) == ['0xbbb', '0xccc', '0xaaa']

    def test_top_n_limits_the_result(self):
        c = _client([_FakeResp(200, self.LEADERBOARD)])
        assert c.fetch_leaderboard_wallets(top_n=1) == ['0xbbb']

    def test_a_failed_leaderboard_returns_none_not_an_empty_list(self):
        c = _client([_FakeResp(503)])
        assert c.fetch_leaderboard_wallets() is None

    def test_default_top_n_is_deeper_than_ten(self):
        # The top 10 by leaderboard accountValue measured as empty ghost
        # accounts on 2026-08-18 (module docstring caveat 3). A default of 10
        # or less would poll nothing but ghosts.
        assert DEFAULT_TOP_N > 10

    def test_a_wallets_file_overrides_discovery(self, tmp_path):
        path = tmp_path / 'w.txt'
        path.write_text('# a comment\n0xdead\n\n0xbeef  # trailing\n')
        c = _client([_FakeResp(200, self.LEADERBOARD)])
        wallets, source = resolve_wallets(c, wallets_file=str(path))
        assert wallets == ['0xdead', '0xbeef']
        assert 'file:' in source
        assert c.session.gets == []      # discovery never ran

    def test_load_wallets_file_strips_comments_and_blanks(self, tmp_path):
        path = tmp_path / 'w.txt'
        path.write_text('\n# header\n0xabc\n   \n0xdef # note\n')
        assert load_wallets_file(str(path)) == ['0xabc', '0xdef']

    def test_discovery_failure_with_no_cache_yields_empty_and_a_reason(self, tmp_path):
        c = _client([_FakeResp(500)])
        wallets, source = resolve_wallets(c, cache_path=str(tmp_path / 'nope.json'))
        assert wallets == []
        assert 'FAILED' in source

    def test_a_fresh_and_large_enough_cache_avoids_refetching_34mb(self, tmp_path):
        cache = tmp_path / 'cache.json'
        import time as _t
        cache.write_text(json.dumps({'fetched_ts': _t.time(),
                                     'wallets': ['0x111', '0x222']}))
        c = _client([_FakeResp(200, self.LEADERBOARD)])
        wallets, source = resolve_wallets(c, cache_path=str(cache), top_n=2)
        assert wallets == ['0x111', '0x222']
        assert c.session.gets == []
        assert 'cache' in source

    def test_a_cache_smaller_than_top_n_triggers_a_refetch(self, tmp_path):
        # Otherwise a `--top-n 2` run silently caps a later `--top-n 3` run at
        # 2 wallets, with no error to explain the small number.
        cache = tmp_path / 'cache.json'
        import time as _t
        cache.write_text(json.dumps({'fetched_ts': _t.time(),
                                     'wallets': ['0x111', '0x222']}))
        c = _client([_FakeResp(200, self.LEADERBOARD)])
        wallets, _ = resolve_wallets(c, cache_path=str(cache), top_n=3)
        assert len(c.session.gets) == 1
        assert wallets == ['0xbbb', '0xccc', '0xaaa']

    def test_the_cache_stores_more_than_it_returns(self, tmp_path):
        cache = tmp_path / 'cache.json'
        c = _client([_FakeResp(200, self.LEADERBOARD)])
        wallets, _ = resolve_wallets(c, cache_path=str(cache), top_n=1)
        assert wallets == ['0xbbb']
        blob = json.loads(cache.read_text())
        # All 3 usable addresses are cached even though only 1 was returned.
        assert blob['wallets'] == ['0xbbb', '0xccc', '0xaaa']
        assert LEADERBOARD_CACHE_WALLETS > DEFAULT_TOP_N

    def test_a_stale_cache_triggers_a_refetch(self, tmp_path):
        cache = tmp_path / 'cache.json'
        cache.write_text(json.dumps({'fetched_ts': 0, 'wallets': ['0x111']}))
        c = _client([_FakeResp(200, self.LEADERBOARD)])
        wallets, _ = resolve_wallets(c, cache_path=str(cache), top_n=2)
        assert wallets == ['0xbbb', '0xccc']
        assert len(c.session.gets) == 1

    def test_a_stale_cache_is_used_when_the_refetch_fails(self, tmp_path):
        cache = tmp_path / 'cache.json'
        cache.write_text(json.dumps({'fetched_ts': 0, 'wallets': ['0x111']}))
        c = _client([_FakeResp(500)])
        wallets, source = resolve_wallets(c, cache_path=str(cache))
        assert wallets == ['0x111']
        assert 'STALE' in source

    def test_discovery_can_be_disabled(self):
        c = _client([_FakeResp(200, self.LEADERBOARD)])
        wallets, source = resolve_wallets(c, discover=False)
        assert wallets == []
        assert 'disabled' in source


class TestCliAndReadOnlyGuarantees(object):
    def test_the_module_uses_the_documented_endpoints(self):
        from engine.feeds import hyperliquid_client as hc
        assert hc.INFO_URL == 'https://api.hyperliquid.xyz/info'
        assert hc.LEADERBOARD_URL == \
            'https://stats-data.hyperliquid.xyz/Mainnet/leaderboard'

    def test_there_is_no_order_or_signing_path(self):
        # Read-only guarantee, checked against the source rather than against a
        # claim in a docstring (convention 22).
        from engine.feeds import hyperliquid_client as hc
        src = open(hc.__file__.replace('.pyc', '.py')).read()
        for forbidden in ('privateKey', 'private_key', 'eth_account',
                          '/exchange', "'type': 'order'"):
            assert forbidden not in src, forbidden

    def test_a_non_positive_min_notional_is_refused(self):
        from engine.feeds.hyperliquid_client import main
        assert main(['--once', '--dry-run', '--min-notional', '0']) == 1

    def test_no_discover_without_a_wallets_file_is_refused(self):
        from engine.feeds.hyperliquid_client import main
        assert main(['--once', '--dry-run', '--no-discover']) == 1

    def test_the_cli_exposes_every_required_flag(self):
        from engine.feeds.hyperliquid_client import build_parser
        args = build_parser().parse_args([])
        for flag in ('symbols', 'min_notional', 'interval', 'wallets', 'db',
                     'once', 'dry_run'):
            assert hasattr(args, flag), flag
        assert args.interval == 30.0        # the required default cadence
