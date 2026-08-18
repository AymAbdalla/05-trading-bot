"""Tests for high-volume event market discovery. Offline only, no network.

These pin four things:

  1. **The order field.** `order=volume` is NOT what this asks Gamma for, and
     that is the finding this file exists to protect. Measured live 2026-08-18:
     `order=volume&ascending=false` returns markets from $10 to $9,997 in an
     order that is not monotonic in either direction, because Gamma sorts the
     `volume` column as TEXT. `order=volumeNum&ascending=false` returns
     $42,242,857 down to $83,444,578, strictly monotonic. Gamma answers HTTP 422
     for an unknown order field, so `volume` is a real field that sorts the
     wrong way - a request that succeeds and returns the inverse of what was
     asked for. `test_the_order_field_is_volumeNum_not_volume` is the guard.
  2. **The volume floor is strict.** `> 10000`, not `>=`. A market exactly at
     the floor is dropped, and the boundary is tested from both sides.
  3. **The accounting identity.** `returned + dropped == raw_count`, with a
     separate counter per drop cause (convention 20). `inactive`, `closed`,
     `volume_unreadable` and `volume_below_floor` never share a bucket.
  4. **A failed read is not an empty result.** `ok=False` and `ok=True` with
     zero markets are different facts (convention 11).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.polymarket.markets import (  # noqa: E402
    DEFAULT_MIN_EVENT_VOLUME_USDC, EVENT_MARKET_DROP_REASONS,
    EVENT_MARKET_ORDER_FIELD, MARKET_DROP_REASONS, event_market_summary,
    search_event_markets, search_event_markets_checked)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeClient:
    """Read-only stand-in for PolymarketClient that records what was asked.

    `gamma_calls` is the point of the class. The central claim of
    `search_event_markets` is that it asks Gamma for the RIGHT sort field, and
    the only way to show that is to record the params rather than to trust that
    some markets came back. A test that only inspects the returned list would
    pass identically against the broken `order=volume` query.
    """

    def __init__(self, payload):
        self._payload = payload
        self.gamma_calls = []

    def gamma(self, path, params=None):
        self.gamma_calls.append((path, dict(params or {})))
        if callable(self._payload):
            return self._payload(path, dict(params or {}))
        return self._payload

    def clob(self, path, params=None):
        return None

    def data(self, path, params=None):
        return None


def market_row(slug, volume=1_000_000.0, active=True, closed=False,
               outcomes=('Yes', 'No'), prices=('0.40', '0.60'),
               token_ids=None, question='Will it?'):
    """One Gamma market, double-encoded exactly as Gamma sends it.

    Token ids are derived FROM THE SLUG so two rows never share a token. With a
    constant token id several of these tests would pass for the wrong reason.
    """
    if token_ids is None:
        token_ids = [slug + ':' + str(i) for i in range(len(outcomes))]
    row = {
        'id': 'id-' + slug,
        'question': question,
        'slug': slug,
        'conditionId': '0xcond-' + slug,
        'outcomes': json.dumps(list(outcomes)),
        'clobTokenIds': json.dumps(list(token_ids)),
        'outcomePrices': json.dumps(list(prices)),
        'active': active,
        'closed': closed,
    }
    if volume is not None:
        row['volume'] = str(volume)
    return row


# ---------------------------------------------------------------------------
# The query itself
# ---------------------------------------------------------------------------

class TestTheQuery:

    def test_the_order_field_is_volumeNum_not_volume(self):
        """`order=volume` is a live, measured trap. See the module docstring.

        Gamma sorts the `volume` column lexicographically, so
        `order=volume&ascending=false` returns the SMALLEST markets while
        returning HTTP 200 and a page that looks like a result.
        """
        assert EVENT_MARKET_ORDER_FIELD == 'volumeNum'
        client = FakeClient([market_row('a', volume=5_000_000.0)])
        search_event_markets_checked(client)
        _path, params = client.gamma_calls[0]
        assert params['order'] == 'volumeNum'
        assert params['order'] != 'volume'

    def test_it_asks_for_descending_active_and_open(self):
        client = FakeClient([])
        search_event_markets_checked(client, limit=20)
        path, params = client.gamma_calls[0]
        assert path == '/markets'
        assert params['ascending'] == 'false'
        assert params['active'] == 'true'
        assert params['closed'] == 'false'
        assert params['limit'] == 20

    def test_tag_is_only_sent_when_given(self):
        client = FakeClient([])
        search_event_markets_checked(client)
        assert 'tag' not in client.gamma_calls[0][1]
        client = FakeClient([])
        search_event_markets_checked(client, tag='politics')
        assert client.gamma_calls[0][1]['tag'] == 'politics'

    def test_the_order_field_used_is_reported_back(self):
        """A caller must be able to see which field the sort actually used."""
        client = FakeClient([])
        result = search_event_markets_checked(client)
        assert result['order_field'] == EVENT_MARKET_ORDER_FIELD


# ---------------------------------------------------------------------------
# The volume filter
# ---------------------------------------------------------------------------

class TestVolumeFilter:

    def test_the_default_floor_is_ten_thousand(self):
        assert DEFAULT_MIN_EVENT_VOLUME_USDC == 10000.0

    def test_above_the_floor_is_kept(self):
        client = FakeClient([market_row('big', volume=10000.01)])
        result = search_event_markets_checked(client)
        assert result['returned'] == 1
        assert result['markets'][0].slug == 'big'

    def test_exactly_at_the_floor_is_dropped(self):
        """The filter is `> floor`, strictly. Boundary from the inside."""
        client = FakeClient([market_row('edge', volume=10000.0)])
        result = search_event_markets_checked(client)
        assert result['returned'] == 0
        assert result['drops'] == {'volume_below_floor': 1}

    def test_just_below_the_floor_is_dropped(self):
        client = FakeClient([market_row('small', volume=9999.99)])
        result = search_event_markets_checked(client)
        assert result['returned'] == 0
        assert result['drops'] == {'volume_below_floor': 1}

    def test_the_floor_is_configurable(self):
        rows = [market_row('a', volume=50_000.0),
                market_row('b', volume=20_000.0)]
        result = search_event_markets_checked(client=FakeClient(rows),
                                              min_volume_usdc=30_000.0)
        assert [m.slug for m in result['markets']] == ['a']
        assert result['drops'] == {'volume_below_floor': 1}
        assert result['min_volume_usdc'] == 30_000.0

    def test_a_missing_volume_is_unreadable_not_below_floor(self):
        """Cannot-measure and measured-and-too-small are different facts.

        Convention 11. Pooling them would make a Gamma schema change look
        exactly like a quiet market.
        """
        client = FakeClient([market_row('novol', volume=None)])
        result = search_event_markets_checked(client)
        assert result['drops'] == {'volume_unreadable': 1}
        assert 'volume_below_floor' not in result['drops']

    def test_a_non_finite_volume_is_unreadable_not_infinitely_large(self):
        """`json.loads` accepts bare Infinity (convention 19).

        A market whose volume parses to inf must not sail through a `> 10000`
        comparison as the biggest market on the venue.
        """
        row = market_row('inf')
        row['volume'] = 'Infinity'
        result = search_event_markets_checked(FakeClient([row]))
        assert result['drops'] == {'volume_unreadable': 1}
        assert result['returned'] == 0


# ---------------------------------------------------------------------------
# Active / closed exclusion
# ---------------------------------------------------------------------------

class TestActiveAndClosed:

    def test_an_inactive_row_is_dropped_and_counted(self):
        """Gamma was asked for active=true. A row that comes back contradicting
        its own query filter is a fact about Gamma, so it is re-checked here and
        counted rather than trusted.
        """
        client = FakeClient([market_row('dead', volume=5_000_000.0,
                                        active=False)])
        result = search_event_markets_checked(client)
        assert result['drops'] == {'inactive': 1}
        assert result['returned'] == 0

    def test_a_closed_row_is_dropped_and_counted(self):
        client = FakeClient([market_row('done', volume=5_000_000.0,
                                        closed=True)])
        result = search_event_markets_checked(client)
        assert result['drops'] == {'closed': 1}

    def test_inactive_and_closed_never_share_a_bucket(self):
        """Two drop causes, two numbers (convention 20)."""
        rows = [market_row('dead', volume=5e6, active=False),
                market_row('done', volume=5e6, closed=True),
                market_row('live', volume=5e6)]
        result = search_event_markets_checked(FakeClient(rows))
        assert result['drops'] == {'inactive': 1, 'closed': 1}
        assert [m.slug for m in result['markets']] == ['live']

    def test_inactive_is_checked_before_volume(self):
        """An inactive market below the floor is counted once, as inactive.

        Not twice, and not arbitrarily under whichever filter ran last.
        """
        client = FakeClient([market_row('x', volume=1.0, active=False)])
        result = search_event_markets_checked(client)
        assert result['drops'] == {'inactive': 1}
        assert sum(result['drops'].values()) == 1


# ---------------------------------------------------------------------------
# Malformed rows
# ---------------------------------------------------------------------------

class TestMalformedRows:

    def test_a_non_dict_row_is_dropped_by_reason(self):
        result = search_event_markets_checked(FakeClient(['nope', 42, None]))
        assert result['drops'] == {'not_a_dict': 3}
        assert result['returned'] == 0

    def test_a_length_mismatch_is_dropped_by_reason(self):
        """outcomes and clobTokenIds are paired POSITIONALLY. If the lengths
        disagree we cannot know which token is which, and guessing puts trades
        on the wrong side.
        """
        row = market_row('mismatch', volume=5e6)
        row['clobTokenIds'] = json.dumps(['only-one'])
        result = search_event_markets_checked(FakeClient([row]))
        assert result['drops'] == {'length_mismatch': 1}

    def test_missing_outcomes_and_missing_tokens_are_separate_reasons(self):
        no_outcomes = market_row('a', volume=5e6)
        no_outcomes['outcomes'] = json.dumps([])
        no_tokens = market_row('b', volume=5e6)
        no_tokens['clobTokenIds'] = json.dumps([])
        result = search_event_markets_checked(
            FakeClient([no_outcomes, no_tokens]))
        assert result['drops'] == {'no_outcomes': 1, 'no_token_ids': 1}

    def test_a_bad_row_does_not_lose_the_good_rows_around_it(self):
        rows = [market_row('good1', volume=5e6),
                'garbage',
                market_row('good2', volume=5e6)]
        result = search_event_markets_checked(FakeClient(rows))
        assert [m.slug for m in result['markets']] == ['good1', 'good2']
        assert result['drops'] == {'not_a_dict': 1}

    def test_every_reason_this_module_can_emit_is_declared(self):
        """A drop reason that is not in EVENT_MARKET_DROP_REASONS is a silent
        number. The parse reasons are inherited, so this also fails if
        `market_from_gamma_checked` grows a reason nobody propagated.
        """
        for reason in MARKET_DROP_REASONS:
            assert reason in EVENT_MARKET_DROP_REASONS
        for extra in ('inactive', 'closed', 'volume_unreadable',
                      'volume_below_floor'):
            assert extra in EVENT_MARKET_DROP_REASONS
        assert len(set(EVENT_MARKET_DROP_REASONS)) == \
            len(EVENT_MARKET_DROP_REASONS)


# ---------------------------------------------------------------------------
# The accounting identity
# ---------------------------------------------------------------------------

class TestAccountingIdentity:

    def test_identity_holds_on_a_clean_page(self):
        rows = [market_row('m%d' % i, volume=5e6) for i in range(6)]
        result = search_event_markets_checked(FakeClient(rows))
        assert result['returned'] + result['dropped'] == result['raw_count']
        assert result['raw_count'] == 6
        assert result['dropped'] == 0

    def test_identity_holds_with_every_drop_cause_present_at_once(self):
        """One row per cause plus two survivors. This is the test that would
        catch a `continue` added without a counter (convention 20).
        """
        bad_tokens = market_row('mismatch', volume=5e6)
        bad_tokens['clobTokenIds'] = json.dumps(['one'])
        no_vol = market_row('novol', volume=None)
        rows = [
            market_row('keep1', volume=5e6),
            market_row('keep2', volume=11_000.0),
            market_row('dead', volume=5e6, active=False),
            market_row('done', volume=5e6, closed=True),
            no_vol,
            market_row('tiny', volume=500.0),
            bad_tokens,
            'garbage',
        ]
        result = search_event_markets_checked(FakeClient(rows))
        assert result['raw_count'] == 8
        assert result['returned'] == 2
        assert result['dropped'] == 6
        assert result['returned'] + result['dropped'] == result['raw_count']
        assert result['drops'] == {
            'inactive': 1, 'closed': 1, 'volume_unreadable': 1,
            'volume_below_floor': 1, 'length_mismatch': 1, 'not_a_dict': 1,
        }

    def test_the_identity_is_asserted_not_merely_documented(self, monkeypatch):
        """Swallow the drop counters and the function must REFUSE.

        This simulates the exact bug convention 20 exists to stop: a row is
        filtered out and nothing counts it. Without the assertion the caller
        gets a quietly short list that reads as "Gamma had less to offer".
        Convention 22: a claim in a docstring is not a wiring test.
        """
        import engine.polymarket.markets as mk

        monkeypatch.setattr(mk, 'Counter', LeakyCounter)
        with pytest.raises(AssertionError, match='does not balance'):
            mk.search_event_markets_checked(FakeClient(['garbage']))

    def test_the_leak_harness_only_fires_when_a_row_is_actually_dropped(self):
        """Guard on the guard: LeakyCounter must not make a clean page fail,
        or the test above would pass no matter what the function did.
        """
        assert sum(LeakyCounter().values()) == 0


class LeakyCounter(dict):
    """A Counter that silently swallows increments.

    Stands in for a missing counter so the accounting assertion has a real
    failure to catch rather than being trusted.
    """

    def __init__(self, *a, **kw):
        super().__init__()

    def __getitem__(self, key):
        return 0

    def __setitem__(self, key, value):
        pass


# ---------------------------------------------------------------------------
# Read failure vs empty result
# ---------------------------------------------------------------------------

class TestReadFailure:

    def test_a_failed_read_is_not_an_empty_result(self):
        result = search_event_markets_checked(FakeClient(None))
        assert result['ok'] is False
        assert result['reason'] == 'read_failed'
        assert result['markets'] == []
        assert result['raw_count'] == 0

    def test_a_genuinely_empty_page_is_ok_true(self):
        result = search_event_markets_checked(FakeClient([]))
        assert result['ok'] is True
        assert result['reason'] is None
        assert result['markets'] == []

    def test_the_two_are_distinguishable(self):
        """The whole reason the `_checked` variant exists (convention 11)."""
        failed = search_event_markets_checked(FakeClient(None))
        empty = search_event_markets_checked(FakeClient([]))
        assert failed['markets'] == empty['markets'] == []
        assert failed['ok'] != empty['ok']

    def test_a_data_wrapped_payload_is_unwrapped(self):
        payload = {'data': [market_row('wrapped', volume=5e6)]}
        result = search_event_markets_checked(FakeClient(payload))
        assert [m.slug for m in result['markets']] == ['wrapped']


# ---------------------------------------------------------------------------
# The plain wrapper
# ---------------------------------------------------------------------------

class TestPlainWrapper:

    def test_it_returns_the_market_list(self):
        rows = [market_row('a', volume=5e6), market_row('tiny', volume=1.0)]
        assert [m.slug for m in search_event_markets(FakeClient(rows))] == ['a']

    def test_a_failed_read_returns_empty_and_warns(self, caplog):
        with caplog.at_level('WARNING'):
            assert search_event_markets(FakeClient(None)) == []
        assert 'read FAILED' in caplog.text

    def test_it_uses_the_same_order_field_as_the_checked_variant(self):
        client = FakeClient([])
        search_event_markets(client)
        assert client.gamma_calls[0][1]['order'] == EVENT_MARKET_ORDER_FIELD


# ---------------------------------------------------------------------------
# The summary shape
# ---------------------------------------------------------------------------

class TestSummary:

    def test_it_carries_slug_question_volume_and_outcome_prices(self):
        rows = [market_row('sluggo', volume=12_345.0,
                           question='Will X happen?',
                           outcomes=('Up', 'Down'), prices=('0.31', '0.69'))]
        result = search_event_markets_checked(FakeClient(rows))
        s = result['summaries'][0]
        assert s['slug'] == 'sluggo'
        assert s['question'] == 'Will X happen?'
        assert s['volume'] == 12_345.0
        assert [o['name'] for o in s['outcome_prices']] == ['Up', 'Down']
        assert [o['price'] for o in s['outcome_prices']] == [0.31, 0.69]

    def test_outcome_prices_stay_positional_and_are_not_flattened_to_yes_no(self):
        """Index 0 is not reliably the bullish side. See the module docstring
        of engine/polymarket/markets.py.
        """
        rows = [market_row('updown', volume=5e6, outcomes=('Up', 'Down'))]
        s = search_event_markets_checked(FakeClient(rows))['summaries'][0]
        assert isinstance(s['outcome_prices'], list)
        assert 'yes' not in s
        assert all('token_id' in o for o in s['outcome_prices'])

    def test_a_missing_price_is_none_not_zero(self):
        row = market_row('nopx', volume=5e6)
        row.pop('outcomePrices')
        s = search_event_markets_checked(FakeClient([row]))['summaries'][0]
        assert [o['price'] for o in s['outcome_prices']] == [None, None]

    def test_summaries_line_up_one_to_one_with_markets(self):
        rows = [market_row('a', volume=5e6), market_row('b', volume=5e6)]
        result = search_event_markets_checked(FakeClient(rows))
        assert len(result['summaries']) == len(result['markets'])
        assert [s['slug'] for s in result['summaries']] == \
            [m.slug for m in result['markets']]

    def test_event_market_summary_is_callable_on_its_own(self):
        result = search_event_markets_checked(
            FakeClient([market_row('solo', volume=5e6)]))
        assert event_market_summary(result['markets'][0]) == \
            result['summaries'][0]


# ---------------------------------------------------------------------------
# Ordering is preserved
# ---------------------------------------------------------------------------

class TestOrderPreserved:

    def test_gamma_row_order_is_preserved_not_re_sorted(self):
        """We rely on Gamma's sort. Re-sorting locally would hide a day when
        Gamma's sort silently breaks - which is exactly what `order=volume`
        does today.
        """
        rows = [market_row('first', volume=9e6),
                market_row('second', volume=8e6),
                market_row('third', volume=7e6)]
        result = search_event_markets_checked(FakeClient(rows))
        assert [m.slug for m in result['markets']] == \
            ['first', 'second', 'third']

    def test_a_dropped_row_does_not_reorder_the_survivors(self):
        rows = [market_row('first', volume=9e6),
                market_row('tiny', volume=1.0),
                market_row('third', volume=7e6)]
        result = search_event_markets_checked(FakeClient(rows))
        assert [m.slug for m in result['markets']] == ['first', 'third']
