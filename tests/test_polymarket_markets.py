"""Tests for category market discovery (sports, politics). Offline, no network.

Companion to `tests/test_event_market_search.py`, which owns the `/markets`
route. This file owns the `/events?tag_slug=` route and pins five things:

  1. **The route.** `/markets?tag=` is a TRAP. Measured live 2026-08-18,
     `tag=sports`, `tag=politics`, `tag=nfl`, `tag=nba` and no tag at all all
     returned the identical three rows (an Ethiopian PM market, a Second Coming
     market and a US-invades-Iran market). Gamma accepts `tag` on `/markets`
     with HTTP 200 and ignores it. `/events?tag_slug=` genuinely filters. The
     guard is `TestTheRoute`, and it asserts on the PARAMS SENT, because a test
     that only looked at the returned list would pass against the ignored-tag
     query.
  2. **No order parameter is sent.** `/events?order=volumeNum` answers HTTP 422
     and `/events?order=volume` is the lexicographic text sort of D-302. The
     sort is local and `order_field` says so.
  3. **The volume floor is strict and shared.** `> 10000`, not `>=`, and it is
     the same `DEFAULT_MIN_EVENT_VOLUME_USDC` constant the event scanner uses.
  4. **The accounting identity.** `returned + sum(drops) == raw_count` with a
     separate counter per cause and no cause counted twice (convention 20).
  5. **Four outcomes, not two.** total outage, partial outage, answered-and-
     empty, and answered-with-markets are four different facts (convention 11).

`TestVolumeNumIsNotVolume` additionally re-pins D-302 from this side: the
constant is `volumeNum`, the `/markets` family still sends it, and the new
`order_field` label is DERIVED from it rather than being a second order string.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.polymarket.markets import (  # noqa: E402
    CATEGORY_MARKET_DROP_REASONS, DEFAULT_CATEGORY_MARKET_LIMIT,
    DEFAULT_MAX_PAGES_PER_TAG, DEFAULT_MIN_EVENT_VOLUME_USDC,
    EVENT_MARKET_ORDER_FIELD, GAMMA_EVENTS_OFFSET_CAP, GAMMA_PAGE_SIZE,
    LOCAL_VOLUME_ORDER, MARKET_DROP_REASONS, POLITICAL_TAG_SLUGS,
    SPORTS_TAG_SLUGS, VOLUME_ORDER_FIELD, list_markets, list_markets_checked,
    search_event_markets_checked, search_political_markets,
    search_political_markets_checked, search_sports_markets,
    search_sports_markets_checked)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def market_row(slug, volume=1_000_000.0, active=True, closed=False,
               outcomes=('Yes', 'No'), prices=('0.40', '0.60'),
               token_ids=None, question='Will it?', accepting=True,
               condition_id=None):
    """One Gamma market row, double-encoded exactly as Gamma sends it."""
    if token_ids is None:
        token_ids = [slug + ':' + str(i) for i in range(len(outcomes))]
    row = {
        'id': 'id-' + slug,
        'question': question,
        'slug': slug,
        'conditionId': condition_id if condition_id is not None
        else '0xcond-' + slug,
        'outcomes': json.dumps(list(outcomes)),
        'clobTokenIds': json.dumps(list(token_ids)),
        'outcomePrices': json.dumps(list(prices)),
        'active': active,
        'closed': closed,
    }
    if accepting is not None:
        row['acceptingOrders'] = accepting
    if volume is not None:
        row['volume'] = str(volume)
    return row


def event_row(title, markets):
    return {'id': 'ev-' + title, 'title': title, 'slug': title,
            'markets': list(markets)}


class FakeEventsClient:
    """Records every Gamma call and serves canned `/events` pages.

    `gamma_calls` is the point of the class. The central claim of these
    functions is that they ask the RIGHT ROUTE with the RIGHT PARAMS, and the
    only way to show that is to record what was sent. `pages_for` maps a tag
    slug to a list of pages; anything not listed answers with an empty page.
    """

    def __init__(self, pages_for=None, fail_tags=(), fail_all=False,
                 payload_override=None):
        self.pages_for = dict(pages_for or {})
        self.fail_tags = set(fail_tags)
        self.fail_all = fail_all
        self.payload_override = payload_override
        self.gamma_calls = []

    def gamma(self, path, params=None):
        params = dict(params or {})
        self.gamma_calls.append((path, params))
        if self.fail_all:
            return None
        if self.payload_override is not None:
            return self.payload_override
        tag = params.get('tag_slug')
        if tag in self.fail_tags:
            return None
        pages = self.pages_for.get(tag, [])
        idx = int(params.get('offset', 0)) // GAMMA_PAGE_SIZE
        return pages[idx] if idx < len(pages) else []

    def clob(self, path, params=None):
        return None

    def data(self, path, params=None):
        return None


def one_tag(tag, events):
    """Client that serves `events` as a single short page for `tag`."""
    return FakeEventsClient({tag: [events]})


BOTH = pytest.mark.parametrize('search, tags, category', [
    (search_sports_markets_checked, SPORTS_TAG_SLUGS, 'sports'),
    (search_political_markets_checked, POLITICAL_TAG_SLUGS, 'political'),
])


# ---------------------------------------------------------------------------
# 1. The route and the params
# ---------------------------------------------------------------------------

class TestTheRoute:

    @BOTH
    def test_it_uses_the_events_tag_route_not_markets_tag(self, search, tags,
                                                          category):
        """`/markets?tag=` is accepted and IGNORED by Gamma (HTTP 200, unfiltered
        page). Measured 2026-08-18 - see the module docstring. Only
        `/events?tag_slug=` actually filters.
        """
        client = FakeEventsClient()
        search(client)
        assert client.gamma_calls, 'no request was made at all'
        for path, params in client.gamma_calls:
            assert path == '/events', path
            assert 'tag_slug' in params
            assert 'tag' not in params, \
                '/markets-style tag param sent to /events: %r' % (params,)

    @BOTH
    def test_no_order_parameter_is_sent(self, search, tags, category):
        """`/events?order=volumeNum` is HTTP 422 and `order=volume` is the
        D-302 text sort. Sending either would be worse than sending none.
        """
        client = FakeEventsClient()
        search(client)
        for _path, params in client.gamma_calls:
            assert 'order' not in params, params
            assert 'ascending' not in params, params

    @BOTH
    def test_it_asks_for_active_open_unarchived(self, search, tags, category):
        client = FakeEventsClient()
        search(client)
        for _path, params in client.gamma_calls:
            assert params['active'] == 'true'
            assert params['closed'] == 'false'
            assert params['archived'] == 'false'
            assert params['limit'] == GAMMA_PAGE_SIZE

    def test_sports_covers_every_league_the_brief_named(self):
        """NFL, NBA, MLB, NHL, soccer, tennis and the three esports titles.

        A claim in a docstring is not coverage (convention 22), so this asserts
        on the tuple the code actually iterates.
        """
        for tag in ('nfl', 'nba', 'mlb', 'nhl', 'soccer', 'tennis',
                    'esports', 'dota-2', 'league-of-legends'):
            assert tag in SPORTS_TAG_SLUGS, tag
        # CS:GO is CS2 now; the `csgo` slug is live and returns 0 events.
        assert 'csgo' not in SPORTS_TAG_SLUGS
        assert 'cs2' in SPORTS_TAG_SLUGS
        assert 'counter-strike' in SPORTS_TAG_SLUGS

    def test_politics_covers_elections_fed_and_policy(self):
        for tag in ('politics', 'elections', 'world-elections', 'geopolitics',
                    'fed', 'fed-rates', 'monetary-policy'):
            assert tag in POLITICAL_TAG_SLUGS, tag
        # `policy` is a live slug that returns 0 events. Listing it would cost
        # a request per scan to learn nothing.
        assert 'policy' not in POLITICAL_TAG_SLUGS

    def test_every_tag_is_queried_once_per_scan(self):
        client = FakeEventsClient()
        search_sports_markets_checked(client)
        asked = [p['tag_slug'] for _path, p in client.gamma_calls]
        assert asked == list(SPORTS_TAG_SLUGS)

    def test_the_two_tag_lists_do_not_overlap(self):
        """Sports and politics must not double-count the same market between
        two scanners that a caller may well add together."""
        assert not (set(SPORTS_TAG_SLUGS) & set(POLITICAL_TAG_SLUGS))

    def test_no_tag_slug_is_listed_twice(self):
        for tags in (SPORTS_TAG_SLUGS, POLITICAL_TAG_SLUGS):
            assert len(tags) == len(set(tags)), tags

    @BOTH
    def test_a_custom_tag_tuple_is_honoured(self, search, tags, category):
        client = FakeEventsClient()
        search(client, tags=('only-this',))
        assert [p['tag_slug'] for _p, p in client.gamma_calls] == ['only-this']


# ---------------------------------------------------------------------------
# 2. Ordering
# ---------------------------------------------------------------------------

class TestOrdering:

    @BOTH
    def test_markets_come_back_highest_volume_first(self, search, tags,
                                                    category):
        rows = [market_row('small', volume=20_000.0),
                market_row('huge', volume=9_000_000.0),
                market_row('mid', volume=500_000.0)]
        client = FakeEventsClient({tags[0]: [[event_row('e', rows)]]})
        result = search(client)
        assert [m.slug for m in result['markets']] == ['huge', 'mid', 'small']

    @BOTH
    def test_the_sort_is_reported_as_local(self, search, tags, category):
        """A reader must not think Gamma sorted this page."""
        result = search(FakeEventsClient())
        assert result['order_field'] == LOCAL_VOLUME_ORDER
        assert result['order_field'].startswith('local:')
        assert result['order_field'] != VOLUME_ORDER_FIELD

    @BOTH
    def test_the_sort_is_numeric_not_lexicographic(self, search, tags,
                                                   category):
        """The exact D-302 failure, reproduced locally. Under a text sort
        '9997.5' outranks '4200000.0' because it starts with a 9.
        """
        rows = [market_row('nine-k', volume=9997.5 + 100),
                market_row('four-m', volume=4_200_000.0),
                market_row('nine-nine', volume=99_999.0)]
        client = FakeEventsClient({tags[0]: [[event_row('e', rows)]]})
        volumes = [m.volume for m in search(client)['markets']]
        assert volumes == sorted(volumes, reverse=True)
        assert volumes[0] == 4_200_000.0

    @BOTH
    def test_the_limit_keeps_the_biggest_not_the_first_seen(self, search, tags,
                                                           category):
        """Cap AFTER the sort. Capping first would return whatever Gamma's
        unordered page happened to list, which is the thing this module exists
        to stop.
        """
        rows = [market_row('tiny-but-first', volume=11_000.0),
                market_row('biggest', volume=8_000_000.0)]
        client = FakeEventsClient({tags[0]: [[event_row('e', rows)]]})
        result = search(client, limit=1)
        assert [m.slug for m in result['markets']] == ['biggest']
        assert result['drops']['over_limit'] == 1
        assert result['truncated'] is True

    @BOTH
    def test_untruncated_results_say_so(self, search, tags, category):
        client = FakeEventsClient(
            {tags[0]: [[event_row('e', [market_row('a', volume=5e6)])]]})
        assert search(client, limit=10)['truncated'] is False


# ---------------------------------------------------------------------------
# 3. The volume floor
# ---------------------------------------------------------------------------

class TestVolumeFloor:

    @BOTH
    def test_the_floor_is_strict_from_both_sides(self, search, tags, category):
        floor = DEFAULT_MIN_EVENT_VOLUME_USDC
        rows = [market_row('exactly', volume=floor),
                market_row('a-cent-over', volume=floor + 0.01),
                market_row('a-cent-under', volume=floor - 0.01)]
        client = FakeEventsClient({tags[0]: [[event_row('e', rows)]]})
        result = search(client)
        assert [m.slug for m in result['markets']] == ['a-cent-over']
        assert result['drops']['volume_below_floor'] == 2

    @BOTH
    def test_the_default_floor_is_ten_thousand(self, search, tags, category):
        assert search(FakeEventsClient())['min_volume_usdc'] == 10000.0

    @BOTH
    def test_the_floor_is_the_shared_event_constant(self, search, tags,
                                                    category):
        """One floor for all three scanners. A per-category floor would make
        "big enough" mean different things depending on which function found
        the market.
        """
        import inspect
        default = inspect.signature(search).parameters['min_volume_usdc'].default
        assert default == DEFAULT_MIN_EVENT_VOLUME_USDC
        assert search_event_markets_checked(
            FakeEventsClient(payload_override=[]))['min_volume_usdc'] == default

    @BOTH
    def test_a_caller_can_move_the_floor(self, search, tags, category):
        rows = [market_row('small', volume=500.0)]
        client = FakeEventsClient({tags[0]: [[event_row('e', rows)]]})
        assert search(client, min_volume_usdc=100.0)['returned'] == 1

    @BOTH
    def test_an_unreadable_volume_is_not_a_small_volume(self, search, tags,
                                                        category):
        """Convention 11 at the field level: cannot-measure is not
        measured-and-too-small, so they get different counters.
        """
        rows = [market_row('novol', volume=None),
                market_row('junkvol', volume=None),
                market_row('small', volume=5.0)]
        rows[1]['volume'] = 'not-a-number'
        client = FakeEventsClient({tags[0]: [[event_row('e', rows)]]})
        drops = search(client)['drops']
        assert drops['volume_unreadable'] == 2
        assert drops['volume_below_floor'] == 1


# ---------------------------------------------------------------------------
# 4. Drop accounting (convention 20)
# ---------------------------------------------------------------------------

class TestDropAccounting:

    @BOTH
    def test_returned_plus_dropped_equals_raw_count(self, search, tags,
                                                    category):
        rows = [market_row('keep', volume=5e6),
                market_row('dead', volume=5e6, active=False),
                market_row('shut', volume=5e6, closed=True),
                market_row('nobook', volume=5e6, accepting=False),
                market_row('poor', volume=1.0),
                market_row('novol', volume=None),
                {'not': 'a market'}]
        client = FakeEventsClient({tags[0]: [[event_row('e', rows)]]})
        result = search(client)
        assert result['raw_count'] == 7
        assert result['returned'] + result['dropped'] == result['raw_count']
        assert result['dropped'] == sum(result['drops'].values())

    @BOTH
    def test_every_cause_gets_its_own_counter(self, search, tags, category):
        rows = [market_row('keep', volume=5e6),
                market_row('dead', volume=5e6, active=False),
                market_row('shut', volume=5e6, closed=True),
                market_row('nobook', volume=5e6, accepting=False),
                market_row('poor', volume=1.0),
                market_row('novol', volume=None)]
        client = FakeEventsClient({tags[0]: [[event_row('e', rows)]]})
        drops = search(client)['drops']
        assert drops == {'inactive': 1, 'closed': 1, 'not_accepting_orders': 1,
                         'volume_below_floor': 1, 'volume_unreadable': 1}

    @BOTH
    def test_a_non_dict_event_is_counted_not_skipped(self, search, tags,
                                                     category):
        client = FakeEventsClient({tags[0]: [['not an event', 42]]})
        result = search(client)
        assert result['drops']['event_not_a_dict'] == 2
        assert result['raw_count'] == 2

    @BOTH
    def test_parse_refusals_keep_their_own_names(self, search, tags, category):
        """Inherited from `market_from_gamma_checked`, not restated, so a new
        parse refusal cannot appear in one vocabulary and not the other.
        """
        bad = market_row('mismatch', volume=5e6)
        bad['clobTokenIds'] = json.dumps(['only-one'])
        nodict = 'plainly not a dict'
        client = FakeEventsClient({tags[0]: [[event_row('e', [bad, nodict])]]})
        drops = search(client)['drops']
        assert drops['length_mismatch'] == 1
        assert drops['not_a_dict'] == 1

    @BOTH
    def test_a_non_dict_market_is_not_double_counted(self, search, tags,
                                                     category):
        """One cause, one counter. An `isinstance` pre-check next to the
        parser's own `not_a_dict` would give this row two names.
        """
        client = FakeEventsClient({tags[0]: [[event_row('e', ['nope'])]]})
        drops = search(client)['drops']
        assert sum(drops.values()) == 1
        assert drops['not_a_dict'] == 1

    @BOTH
    def test_every_reason_emitted_is_in_the_declared_vocabulary(self, search,
                                                               tags, category):
        rows = [market_row('dead', volume=5e6, active=False),
                market_row('shut', volume=5e6, closed=True),
                market_row('nobook', volume=5e6, accepting=False),
                market_row('poor', volume=1.0),
                market_row('novol', volume=None),
                market_row('big1', volume=5e6),
                market_row('big2', volume=4e6),
                'not a dict']
        client = FakeEventsClient({tags[0]: [[event_row('e', rows), 'bad']]})
        drops = search(client, limit=1)['drops']
        for reason in drops:
            assert reason in CATEGORY_MARKET_DROP_REASONS, reason
        assert 'over_limit' in drops
        assert 'event_not_a_dict' in drops

    def test_the_vocabulary_inherits_the_parse_reasons(self):
        for reason in MARKET_DROP_REASONS:
            assert reason in CATEGORY_MARKET_DROP_REASONS, reason

    def test_the_vocabulary_has_no_duplicate_strings(self):
        assert len(CATEGORY_MARKET_DROP_REASONS) == \
            len(set(CATEGORY_MARKET_DROP_REASONS))


# ---------------------------------------------------------------------------
# 5. Dedupe across overlapping tags
# ---------------------------------------------------------------------------

class TestDedupe:

    @BOTH
    def test_the_same_market_under_two_tags_is_returned_once(self, search,
                                                             tags, category):
        row = market_row('shared', volume=5e6)
        client = FakeEventsClient({tags[0]: [[event_row('a', [row])]],
                                   tags[1]: [[event_row('b', [row])]]})
        result = search(client)
        assert [m.slug for m in result['markets']] == ['shared']
        assert result['drops']['duplicate_across_tags'] == 1
        assert result['raw_count'] == 2

    @BOTH
    def test_dedupe_is_on_condition_id_not_row_id(self, search, tags,
                                                  category):
        """Two Gamma rows with the same `conditionId` are one tradeable
        market whatever their row ids say."""
        a = market_row('slug-a', volume=5e6, condition_id='0xsame')
        b = market_row('slug-b', volume=5e6, condition_id='0xsame')
        client = FakeEventsClient({tags[0]: [[event_row('e', [a, b])]]})
        result = search(client)
        assert result['returned'] == 1
        assert result['drops']['duplicate_across_tags'] == 1

    @BOTH
    def test_a_duplicate_is_counted_as_a_duplicate_not_as_below_floor(
            self, search, tags, category):
        """Attribution: the first thing wrong with the second copy of a market
        is that it is the second copy. Deduping before the volume gates keeps
        `volume_below_floor` a count of DISTINCT markets.
        """
        row = market_row('tiny', volume=1.0)
        client = FakeEventsClient({tags[0]: [[event_row('a', [row])]],
                                   tags[1]: [[event_row('b', [row])]]})
        drops = search(client)['drops']
        assert drops['volume_below_floor'] == 1
        assert drops['duplicate_across_tags'] == 1


# ---------------------------------------------------------------------------
# 6. Pagination
# ---------------------------------------------------------------------------

class TestPagination:

    @BOTH
    def test_it_pages_until_a_short_page(self, search, tags, category):
        full = [event_row('e%d' % i, [market_row('m%d' % i, volume=5e6)])
                for i in range(GAMMA_PAGE_SIZE)]
        # The short second page must carry DISTINCT markets. Reusing `full[:3]`
        # made page two three duplicates of page one, so dedupe correctly
        # returned 100 and the test read that as a pagination failure.
        tail = [event_row('t%d' % i, [market_row('t%d' % i, volume=5e6)])
                for i in range(3)]
        client = FakeEventsClient({tags[0]: [full, tail]})
        result = search(client, limit=1000, tags=(tags[0],))
        assert result['pages'] == 2
        assert result['returned'] == GAMMA_PAGE_SIZE + 3
        offsets = [p['offset'] for _path, p in client.gamma_calls]
        assert offsets == [0, GAMMA_PAGE_SIZE]

    @BOTH
    def test_a_short_first_page_stops_immediately(self, search, tags,
                                                  category):
        client = FakeEventsClient(
            {tags[0]: [[event_row('e', [market_row('a', volume=5e6)])]]})
        search(client, tags=(tags[0],))
        assert len(client.gamma_calls) == 1

    @BOTH
    def test_hitting_the_page_budget_is_reported(self, search, tags, category):
        full = [event_row('e%d' % i, [market_row('m%d' % i, volume=5e6)])
                for i in range(GAMMA_PAGE_SIZE)]
        client = FakeEventsClient({tags[0]: [full, full, full]})
        result = search(client, limit=1000, tags=(tags[0],),
                        max_pages_per_tag=2)
        assert result['pagination_capped'] is True
        assert result['pages'] == 2

    @BOTH
    def test_exhausting_a_tag_is_not_reported_as_capped(self, search, tags,
                                                        category):
        client = FakeEventsClient(
            {tags[0]: [[event_row('e', [market_row('a', volume=5e6)])]]})
        assert search(client, tags=(tags[0],))['pagination_capped'] is False

    @BOTH
    def test_it_never_asks_past_the_gamma_offset_cap(self, search, tags,
                                                     category):
        """Gamma answers HTTP 422 past offset 2100 and the client turns that
        into a bare `None`, which is indistinguishable from an outage. So we
        never ask: the ceiling becomes a counted `pagination_capped`, not a
        misreported `read_failed` (convention 11).
        """
        full = [event_row('e%d' % i, [market_row('m%d-%d' % (i, n),
                                                 volume=5e6)])
                for i, n in enumerate(range(GAMMA_PAGE_SIZE))]
        client = FakeEventsClient({tags[0]: [full] * 100})
        search(client, limit=100000, tags=(tags[0],), max_pages_per_tag=10_000)
        offsets = [p['offset'] for _path, p in client.gamma_calls]
        assert max(offsets) < GAMMA_EVENTS_OFFSET_CAP
        assert len(offsets) == GAMMA_EVENTS_OFFSET_CAP // GAMMA_PAGE_SIZE

    def test_the_default_page_budget_equals_the_cap(self):
        assert DEFAULT_MAX_PAGES_PER_TAG == \
            GAMMA_EVENTS_OFFSET_CAP // GAMMA_PAGE_SIZE


# ---------------------------------------------------------------------------
# 7. Failure contract (convention 11) - four outcomes, not two
# ---------------------------------------------------------------------------

class TestFailureContract:

    @BOTH
    def test_a_total_outage_is_not_an_empty_result(self, search, tags,
                                                   category):
        failed = search(FakeEventsClient(fail_all=True))
        empty = search(FakeEventsClient())
        assert failed['markets'] == empty['markets'] == []
        assert failed['ok'] is False
        assert empty['ok'] is True
        assert failed['reason'] == 'read_failed'
        assert empty['reason'] == 'no_{}_market'.format(category)

    @BOTH
    def test_a_total_outage_returns_no_exception(self, search, tags, category):
        """Degrade, do not raise. Same contract as `search_event_markets`."""
        result = search(FakeEventsClient(fail_all=True))
        assert result['read_failures']['read_failed'] == len(tags)
        assert result['raw_count'] == 0
        assert result['drops'] == {}

    @BOTH
    def test_a_partial_outage_is_ok_true_with_counted_failures(self, search,
                                                               tags, category):
        """The fourth outcome. Some tags answered, some did not: the list is
        real but short, and pooling that into either `ok` value loses a fact.
        """
        client = FakeEventsClient(
            {tags[0]: [[event_row('e', [market_row('a', volume=5e6)])]]},
            fail_tags={tags[1]})
        result = search(client, tags=(tags[0], tags[1]))
        assert result['ok'] is True
        assert result['returned'] == 1
        assert result['read_failures'] == {'read_failed': 1}

    @BOTH
    def test_a_clean_run_reports_no_read_failures(self, search, tags,
                                                  category):
        assert search(FakeEventsClient())['read_failures'] == {}

    @BOTH
    def test_an_unexpected_envelope_is_its_own_status(self, search, tags,
                                                      category):
        """A changed response shape is not an outage and the fix is different."""
        client = FakeEventsClient(payload_override={'nope': 1})
        result = search(client)
        assert result['ok'] is False
        assert result['read_failures'] == {'unexpected_shape': len(tags)}

    @BOTH
    def test_a_data_wrapped_envelope_is_unwrapped(self, search, tags,
                                                  category):
        client = FakeEventsClient(payload_override={
            'data': [event_row('e', [market_row('wrapped', volume=5e6)])]})
        result = search(client)
        assert result['ok'] is True
        assert [m.slug for m in result['markets']] == ['wrapped']

    @BOTH
    def test_an_events_wrapped_envelope_is_unwrapped(self, search, tags,
                                                     category):
        client = FakeEventsClient(payload_override={
            'events': [event_row('e', [market_row('wrapped', volume=5e6)])]})
        assert [m.slug for m in search(client)['markets']] == ['wrapped']


# ---------------------------------------------------------------------------
# 8. Result shape
# ---------------------------------------------------------------------------

class TestResultShape:

    @BOTH
    def test_the_checked_result_carries_every_documented_key(self, search,
                                                             tags, category):
        result = search(FakeEventsClient())
        for key in ('ok', 'markets', 'summaries', 'raw_count', 'returned',
                    'dropped', 'drops', 'order_field', 'min_volume_usdc',
                    'category', 'tags_searched', 'pages', 'truncated',
                    'pagination_capped', 'read_failures', 'reason'):
            assert key in result, key

    @BOTH
    def test_the_failed_result_carries_the_same_keys(self, search, tags,
                                                     category):
        """A caller must not have to branch on `ok` before reading a key."""
        assert set(search(FakeEventsClient(fail_all=True))) == \
            set(search(FakeEventsClient()))

    @BOTH
    def test_the_category_and_tags_are_reported_back(self, search, tags,
                                                     category):
        result = search(FakeEventsClient())
        assert result['category'] == category
        assert result['tags_searched'] == list(tags)

    @BOTH
    def test_summaries_line_up_one_to_one_with_markets(self, search, tags,
                                                       category):
        rows = [market_row('a', volume=5e6), market_row('b', volume=4e6)]
        client = FakeEventsClient({tags[0]: [[event_row('e', rows)]]})
        result = search(client)
        assert [s['slug'] for s in result['summaries']] == \
            [m.slug for m in result['markets']]
        assert result['summaries'][0]['volume'] == 5e6

    @BOTH
    def test_outcome_prices_stay_positional(self, search, tags, category):
        """Index 0 is not reliably the bullish side. See the module docstring
        of engine/polymarket/markets.py.
        """
        rows = [market_row('updown', volume=5e6, outcomes=('Up', 'Down'))]
        client = FakeEventsClient({tags[0]: [[event_row('e', rows)]]})
        s = search(client)['summaries'][0]
        assert [o['name'] for o in s['outcome_prices']] == ['Up', 'Down']
        assert 'yes' not in s

    @BOTH
    def test_the_result_is_json_serialisable_with_allow_nan_false(
            self, search, tags, category):
        """Convention 19. `json.loads` is not strict, so a NaN volume that
        slipped through would round-trip here and break every other parser.
        """
        rows = [market_row('a', volume=5e6)]
        client = FakeEventsClient({tags[0]: [[event_row('e', rows)]]})
        result = search(client)
        payload = {k: v for k, v in result.items() if k != 'markets'}
        json.dumps(payload, allow_nan=False)


# ---------------------------------------------------------------------------
# 9. The plain wrappers
# ---------------------------------------------------------------------------

class TestPlainWrappers:

    @pytest.mark.parametrize('plain, checked, tags', [
        (search_sports_markets, search_sports_markets_checked,
         SPORTS_TAG_SLUGS),
        (search_political_markets, search_political_markets_checked,
         POLITICAL_TAG_SLUGS),
    ])
    def test_it_returns_the_same_list_as_the_checked_variant(self, plain,
                                                             checked, tags):
        rows = [market_row('a', volume=5e6), market_row('tiny', volume=1.0)]
        pages = {tags[0]: [[event_row('e', rows)]]}
        assert [m.slug for m in plain(FakeEventsClient(pages))] == \
            [m.slug for m in checked(FakeEventsClient(pages))['markets']]

    @pytest.mark.parametrize('plain', [search_sports_markets,
                                       search_political_markets])
    def test_a_failed_read_returns_empty_and_warns(self, plain, caplog):
        with caplog.at_level('WARNING'):
            assert plain(FakeEventsClient(fail_all=True)) == []
        assert 'read FAILED' in caplog.text

    @pytest.mark.parametrize('plain, tags', [
        (search_sports_markets, SPORTS_TAG_SLUGS),
        (search_political_markets, POLITICAL_TAG_SLUGS),
    ])
    def test_a_partial_outage_warns_that_the_list_is_short(self, plain, tags,
                                                           caplog):
        """A short list that looks complete is the failure mode this module is
        written against, so the plain variant must not stay quiet about it.
        """
        client = FakeEventsClient(
            {tags[0]: [[event_row('e', [market_row('a', volume=5e6)])]]},
            fail_tags={tags[1]})
        with caplog.at_level('WARNING'):
            assert len(plain(client, tags=(tags[0], tags[1]))) == 1
        assert 'INCOMPLETE' in caplog.text

    @pytest.mark.parametrize('plain', [search_sports_markets,
                                       search_political_markets])
    def test_a_total_outage_does_not_raise(self, plain):
        assert plain(FakeEventsClient(fail_all=True)) == []

    @pytest.mark.parametrize('fn', [
        search_sports_markets, search_sports_markets_checked,
        search_political_markets, search_political_markets_checked])
    def test_the_default_limit_is_shared(self, fn):
        import inspect
        assert inspect.signature(fn).parameters['limit'].default == \
            DEFAULT_CATEGORY_MARKET_LIMIT


# ---------------------------------------------------------------------------
# 10. D-302 re-pinned from this side
# ---------------------------------------------------------------------------

class TestVolumeNumIsNotVolume:
    """`order=volume` sorts Gamma's volume column as TEXT and returns the
    SMALLEST markets while answering HTTP 200. `order=volumeNum` sorts
    numerically. Measured live 2026-08-18 (D-302). `order=notarealfield` is
    HTTP 422, so `volume` is a RECOGNISED field that returns the inverse of
    what was asked for - worse than one that is ignored.

    `tests/test_event_market_search.py` owns this too. Duplicated here on
    purpose: this file adds a THIRD order string to the module
    (`LOCAL_VOLUME_ORDER`) and the risk it introduces is exactly that someone
    later "unifies" the two by writing `'volume'` somewhere.
    """

    def test_the_constant_is_volumeNum(self):
        assert VOLUME_ORDER_FIELD == 'volumeNum'
        assert VOLUME_ORDER_FIELD != 'volume'

    def test_the_event_alias_is_the_same_object(self):
        assert EVENT_MARKET_ORDER_FIELD is VOLUME_ORDER_FIELD

    def test_the_markets_route_still_sends_volumeNum(self):
        client = FakeEventsClient(payload_override=[])
        list_markets_checked(client)
        assert client.gamma_calls[0][1]['order'] == 'volumeNum'
        client = FakeEventsClient(payload_override=[])
        list_markets(client)
        assert client.gamma_calls[0][1]['order'] == 'volumeNum'
        client = FakeEventsClient(payload_override=[])
        search_event_markets_checked(client)
        assert client.gamma_calls[0][1]['order'] == 'volumeNum'

    def test_the_local_label_is_derived_from_the_constant(self):
        """Not a second order string. One definition of which column "by
        volume" means (convention 23).
        """
        assert LOCAL_VOLUME_ORDER == 'local:volumeNum_desc'
        assert VOLUME_ORDER_FIELD in LOCAL_VOLUME_ORDER
        assert 'local:volume_desc' != LOCAL_VOLUME_ORDER

    def test_no_order_string_in_the_module_is_bare_volume(self):
        """Sweep over the module's own constants, so a fourth order string
        added later cannot quietly be the broken one.
        """
        import engine.polymarket.markets as mod

        for name in dir(mod):
            if not name.isupper():
                continue
            value = getattr(mod, name)
            if isinstance(value, str) and 'ORDER' in name:
                assert value != 'volume', '%s == %r' % (name, value)
