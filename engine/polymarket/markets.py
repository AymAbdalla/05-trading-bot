"""Market discovery, search, and metadata for Polymarket.

Turns Gamma's loosely-typed JSON into `Market` objects. The two things worth
knowing:

  1. Gamma double-encodes `outcomes`, `outcomePrices` and `clobTokenIds` as JSON
     strings inside the JSON document. `parse_embedded_list` handles it, and
     unlike a bare `json.loads` it also refuses a field that decodes to
     something other than a list.
  2. `clobTokenIds[i]` lines up with `outcomes[i]`. For a BTC Up/Down market
     that is [UP, DOWN]. We pair them positionally and never assume index 0 is
     "Yes" - Polymarket's own API reference documents index 0 as Yes, but that
     only holds for markets literally labelled Yes/No, and a strategy that buys
     token[0] believing it is always the bullish side will be silently
     backwards on the Up/Down markets this project actually trades.

Every function that filters markets out reports WHY, by reason, rather than
returning a shorter list (convention 20). `*_checked` variants expose the
counts; the plain variants log them.
"""
import logging
import time
from collections import Counter
from typing import Dict, List, Optional, Tuple

from engine.polymarket.client import (PolymarketClient, parse_embedded_json,
                                      parse_embedded_list)
from engine.polymarket.types import Market, Outcome, safe_float

logger = logging.getLogger(__name__)

# Crypto Up/Down 5-minute markets are slugged by their unix window-open second,
# prefixed by the lowercase ticker: `btc-updown-5m-{ts}`, `eth-updown-5m-{ts}`,
# `sol-updown-5m-{ts}`. Verified live on all three, 2026-08-18.
UPDOWN_5M_DURATION = 300
UPDOWN_5M_SLUG = '{asset}-updown-5m-{ts}'

# Up/Down markets also exist at 15 minutes on every registered asset, same
# `{asset}-updown-15m-{ts}` shape. Verified live on btc, eth and sol.
# There is NO 1-hour market: `btc-updown-1h-{ts}` returns empty.
UPDOWN_15M_DURATION = 900
UPDOWN_15M_SLUG = '{asset}-updown-15m-{ts}'

# The BTC-specific names, kept because they are imported by name across the
# engine, the strategies and the tests. Aliases, not a second definition: one
# duration constant and one slug template (convention 23).
BTC_UPDOWN_5M_DURATION = UPDOWN_5M_DURATION
BTC_UPDOWN_5M_SLUG = UPDOWN_5M_SLUG.format(asset='btc', ts='{ts}')

# Gamma caps `limit`; 100 is comfortably inside it and is what the reference
# examples use.
GAMMA_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 20

#: The field name to sort `/markets` by when you want the BIGGEST markets.
#:
#: NOT `volume`. Measured live 2026-08-18, and this is the whole reason this
#: constant exists rather than the obvious string:
#:
#:   order=volume&ascending=false&limit=20&active=true&closed=false
#:       -> volumes $10 to $9,997, not monotonic in EITHER direction. Every
#:          value on the page begins with the digit 9, which is the signature
#:          of a LEXICOGRAPHIC sort on a text column: "9997.5" and "9.99" and
#:          "99.99" all sort together and $43,000,000.00 sorts near the bottom
#:          because it starts with a '4'.
#:   order=volumeNum&ascending=false&limit=20&active=true&closed=false
#:       -> $42,242,857 down to $83,444,578, STRICTLY monotonic descending.
#:   order=volumeNum&ascending=true&limit=8
#:       -> all zeros, so `ascending` is genuinely honoured.
#:
#: Gamma does NOT silently ignore an unknown order field - `order=notarealfield`
#: returns HTTP 422. So `order=volume` is a RECOGNISED field that sorts the
#: wrong way, which is worse than an ignored one: the request succeeds, the page
#: looks like a result, and it is the exact inverse of what was asked for.
#: `order=liquidity` fails the same way. Re-measure before changing this.
#:
#: D-302: this is the default for the whole `list_markets` family too, not just
#: event-market discovery. It used to be defined only down in the event-market
#: section while the listing functions defaulted to the broken `volume`.
VOLUME_ORDER_FIELD = 'volumeNum'

# Reasons a raw Gamma market object cannot become a Market.
MARKET_DROP_REASONS = (
    'not_a_dict',
    'no_outcomes',
    'no_token_ids',
    'outcomes_not_a_list',
    'token_ids_not_a_list',
    'length_mismatch',
)


def _to_float(value, default=None) -> Optional[float]:
    """Kept for callers; delegates to the non-finite-rejecting parser."""
    return safe_float(value, default)


def market_from_gamma_checked(raw: dict) -> Tuple[Optional[Market], Optional[str]]:
    """Build a Market from one Gamma market object, with a refusal reason.

    Returns `(market, None)` on success or `(None, reason)` on refusal, where
    reason is one of MARKET_DROP_REASONS. Refusing rather than half-building is
    deliberate: a Market you cannot fetch a book for is not a market you can
    trade, and handing one back invites a NoneType failure three layers deeper.
    """
    if not isinstance(raw, dict):
        return None, 'not_a_dict'

    names, names_status = parse_embedded_list(raw.get('outcomes'))
    token_ids, tokens_status = parse_embedded_list(raw.get('clobTokenIds'))
    # outcomePrices is optional - a market with no quotes yet still exists.
    prices, _ = parse_embedded_list(raw.get('outcomePrices'))

    if names_status == 'not_a_list':
        return None, 'outcomes_not_a_list'
    if tokens_status == 'not_a_list':
        return None, 'token_ids_not_a_list'
    if not names:
        return None, 'no_outcomes'
    if not token_ids:
        return None, 'no_token_ids'
    if len(names) != len(token_ids):
        # Positional pairing is the only link between a name and its token. If
        # the lengths disagree we cannot know which is which, and guessing
        # would put trades on the wrong side of the market.
        return None, 'length_mismatch'

    outcomes = tuple(
        Outcome(name=str(names[i]),
                token_id=str(token_ids[i]),
                price=safe_float(prices[i]) if i < len(prices) else None)
        for i in range(len(names))
    )

    return Market(
        id=str(raw.get('id', '')),
        question=str(raw.get('question', '')),
        slug=str(raw.get('slug', '')),
        condition_id=str(raw.get('conditionId', '')),
        outcomes=outcomes,
        active=bool(raw.get('active', True)),
        closed=bool(raw.get('closed', False)),
        end_date=raw.get('endDate'),
        volume=safe_float(raw.get('volume')),
        liquidity=safe_float(raw.get('liquidity')),
        raw=raw,
    ), None


def market_from_gamma(raw: dict) -> Optional[Market]:
    """Build a Market from one Gamma market object, or None if unusable."""
    market, reason = market_from_gamma_checked(raw)
    if market is None:
        slug = raw.get('slug') or raw.get('id') if isinstance(raw, dict) else None
        level = logger.warning if reason == 'length_mismatch' else logger.debug
        level('gamma market %s unusable: %s', slug, reason)
    return market


def _markets_from_payload(payload) -> Tuple[List[Market], Dict[str, int]]:
    """Convert a Gamma list payload to Markets, counting every refusal."""
    drops: Counter = Counter()
    out: List[Market] = []
    for raw in payload or ():
        market, reason = market_from_gamma_checked(raw)
        if market is None:
            drops[reason] += 1
            continue
        out.append(market)
    return out, dict(drops)


def _unwrap_list(payload):
    """Gamma returns a bare list on most endpoints and {'data': [...]} on some."""
    if isinstance(payload, dict):
        return payload.get('data') or []
    if isinstance(payload, list):
        return payload
    return []


# -- single market -----------------------------------------------------------

def get_market_by_slug_checked(client: PolymarketClient, slug: str,
                               active_only: bool = False
                               ) -> Tuple[Optional[Market], str]:
    """Fetch one market by slug, saying whether a miss was a miss or an outage.

    Returns `(market, status)` with status one of `ok`, `not_found`,
    `read_failed`, or a MARKET_DROP_REASONS value.

    `active_only=False` by default so this also works for RESOLVED markets,
    which is how the strategies read historical window outcomes.

    Gamma's `/markets?slug=` excludes closed markets unless you ask for them,
    and it returns an empty list rather than an error when it does. Verified
    live 2026-08-17: a 20-minute-old btc-updown-5m window came back `[]` on the
    plain query and resolved 1/0 on `closed=true`. Without the second query
    every settled window reads as "market not found" - a cannot-run that is
    indistinguishable from an unresolved one (convention 11). So: ask for open
    first, then explicitly ask for closed.

    The read_failed short-circuit matters for the same reason. A transport
    failure on the first query used to fall through to the closed query and,
    if that also failed, surface as a plain None - reporting an outage as
    "this window does not exist".
    """
    def _fetch(params: Dict[str, object]) -> Tuple[Optional[Market], str]:
        payload = client.gamma('/markets', params)
        if payload is None:
            return None, 'read_failed'
        rows = _unwrap_list(payload)
        if not rows:
            return None, 'not_found'
        market, reason = market_from_gamma_checked(rows[0])
        if market is None:
            return None, reason
        return market, 'ok'

    if active_only:
        return _fetch({'slug': slug, 'closed': 'false', 'active': 'true'})

    market, status = _fetch({'slug': slug})
    if status != 'not_found':
        return market, status
    return _fetch({'slug': slug, 'closed': 'true'})


def get_market_by_slug(client: PolymarketClient, slug: str,
                       active_only: bool = False) -> Optional[Market]:
    """Fetch one market by its slug. None if not indexed yet or unusable."""
    market, status = get_market_by_slug_checked(client, slug, active_only)
    if market is None and status == 'read_failed':
        logger.warning('market %s: read FAILED (not the same as not listed)', slug)
    return market


# -- listing and search ------------------------------------------------------

def list_markets_checked(client: PolymarketClient, limit: int = 100,
                         active: bool = True, closed: bool = False,
                         order: str = VOLUME_ORDER_FIELD,
                         ascending: bool = False,
                         tag: Optional[str] = None,
                         offset: int = 0) -> Dict[str, object]:
    """One page of markets plus full drop accounting.

    Returns `{'ok', 'markets', 'raw_count', 'drops', 'reason'}`. `ok=False`
    means the read failed; an empty `markets` with `ok=True` means Gamma
    genuinely had nothing. Those are different facts and the plain
    `list_markets` cannot express the difference (convention 11).

    The accounting identity `raw_count - sum(drops) == len(markets)` holds.

    `order` defaults to `VOLUME_ORDER_FIELD` ('volumeNum'). Passing the
    plausible-looking `'volume'` sorts that column as TEXT and returns the
    SMALLEST markets first (D-302).
    """
    params: Dict[str, object] = {
        'limit': limit, 'offset': offset,
        'active': str(bool(active)).lower(),
        'closed': str(bool(closed)).lower(),
        'order': order, 'ascending': str(bool(ascending)).lower(),
    }
    if tag:
        params['tag'] = tag

    payload = client.gamma('/markets', params)
    if payload is None:
        return {'ok': False, 'markets': [], 'raw_count': 0, 'drops': {},
                'reason': 'read_failed'}

    rows = _unwrap_list(payload)
    markets, drops = _markets_from_payload(rows)
    return {'ok': True, 'markets': markets, 'raw_count': len(rows),
            'drops': drops, 'reason': None}


def list_markets(client: PolymarketClient, limit: int = 100,
                 active: bool = True, closed: bool = False,
                 order: str = VOLUME_ORDER_FIELD, ascending: bool = False,
                 tag: Optional[str] = None,
                 offset: int = 0) -> List[Market]:
    """List markets, genuinely highest volume first by default (D-302).

    The default sorts on `volumeNum`. Before D-302 it sorted on `volume`,
    which Gamma orders lexicographically, so "highest volume first" was
    exactly backwards and the docstring said so anyway.
    """
    result = list_markets_checked(client, limit, active, closed, order,
                                  ascending, tag, offset)
    if not result['ok']:
        logger.warning('list_markets read FAILED (empty list is NOT the answer)')
    elif result['drops']:
        logger.info('list_markets dropped %d of %d rows: %s',
                    sum(result['drops'].values()), result['raw_count'],
                    result['drops'])
    return result['markets']


def list_all_markets(client: PolymarketClient, active: bool = True,
                     closed: bool = False, order: str = VOLUME_ORDER_FIELD,
                     ascending: bool = False, tag: Optional[str] = None,
                     page_size: int = GAMMA_PAGE_SIZE,
                     max_pages: int = DEFAULT_MAX_PAGES) -> Dict[str, object]:
    """Page through `/markets` until exhausted, `max_pages`, or a failed read.

    Gamma caps a single response, so a one-shot `limit=5000` silently returns a
    truncated list that looks complete. This walks `offset` instead and says
    how it stopped: `exhausted`, `max_pages`, or `read_failed`. A caller that
    treats a `max_pages` stop as a complete universe is drawing a conclusion
    from a truncated sample.
    """
    markets: List[Market] = []
    drops: Counter = Counter()
    raw_count = 0
    pages = 0
    stop = 'exhausted'

    for page in range(max(1, int(max_pages))):
        result = list_markets_checked(client, limit=page_size, active=active,
                                      closed=closed, order=order,
                                      ascending=ascending, tag=tag,
                                      offset=page * page_size)
        pages = page + 1
        if not result['ok']:
            stop = 'read_failed'
            break
        raw_count += result['raw_count']
        drops.update(result['drops'])
        markets.extend(result['markets'])
        if result['raw_count'] < page_size:
            stop = 'exhausted'
            break
    else:
        stop = 'max_pages'

    return {'markets': markets, 'pages': pages, 'raw_count': raw_count,
            'drops': dict(drops), 'stop_reason': stop,
            'complete': stop == 'exhausted'}


def search_markets_checked(client: PolymarketClient, query: str,
                           limit: Optional[int] = None) -> Dict[str, object]:
    """Full-text search with drop accounting.

    Gamma nests markets inside events for this endpoint. `truncated` says
    whether `limit` cut the result short, so a caller cannot mistake "we
    stopped early" for "that is everything".
    """
    payload = client.gamma('/public-search', {'q': query})
    if payload is None:
        return {'ok': False, 'markets': [], 'raw_count': 0, 'drops': {},
                'events': 0, 'truncated': False, 'reason': 'read_failed'}
    if not isinstance(payload, dict):
        return {'ok': False, 'markets': [], 'raw_count': 0, 'drops': {},
                'events': 0, 'truncated': False, 'reason': 'unexpected_shape'}

    events = payload.get('events') or []
    out: List[Market] = []
    drops: Counter = Counter()
    raw_count = 0
    truncated = False

    for event in events:
        if not isinstance(event, dict):
            drops['event_not_a_dict'] += 1
            continue
        for raw in event.get('markets') or ():
            raw_count += 1
            market, reason = market_from_gamma_checked(raw)
            if market is None:
                drops[reason] += 1
                continue
            if limit and len(out) >= limit:
                truncated = True
                continue
            out.append(market)

    return {'ok': True, 'markets': out, 'raw_count': raw_count,
            'drops': dict(drops), 'events': len(events),
            'truncated': truncated, 'reason': None}


def search_markets(client: PolymarketClient, query: str,
                   limit: Optional[int] = None) -> List[Market]:
    """Full-text search. Gamma nests markets inside events for this endpoint."""
    result = search_markets_checked(client, query, limit)
    if not result['ok']:
        logger.warning('search_markets(%r) read FAILED: %s', query,
                       result['reason'])
    elif result['drops']:
        logger.info('search_markets(%r) dropped %d of %d: %s', query,
                    sum(result['drops'].values()), result['raw_count'],
                    result['drops'])
    return result['markets']


# -- high-volume event market discovery --------------------------------------

#: Alias, not a second definition (convention 23). The measurement that picked
#: `volumeNum` over `volume` is documented on `VOLUME_ORDER_FIELD` at the top of
#: the module, which is also what the `list_markets` family now defaults to.
EVENT_MARKET_ORDER_FIELD = VOLUME_ORDER_FIELD

#: Dollar volume a market must EXCEED to count as an event market worth
#: scanning. Strictly greater. Convention 17: an assumption with an expiry date,
#: not a measurement.
DEFAULT_MIN_EVENT_VOLUME_USDC = 10000.0

DEFAULT_EVENT_MARKET_LIMIT = 20

#: Reasons a Gamma row does not become a returned event market. The parse
#: reasons are inherited from `market_from_gamma_checked` rather than restated,
#: so a new parse refusal cannot appear in one tuple and not the other
#: (convention 23).
EVENT_MARKET_DROP_REASONS = MARKET_DROP_REASONS + (
    'inactive',
    'closed',
    'volume_unreadable',
    'volume_below_floor',
)


def event_market_summary(market: Market) -> Dict[str, object]:
    """Slug, question, volume and outcome prices for one market.

    `outcome_prices` is a list of `{'name', 'token_id', 'price'}` in the
    market's own positional order, NOT a Yes/No dict. See the module docstring:
    index 0 is not reliably the bullish side, and flattening these into
    `{'yes': ..., 'no': ...}` is how a caller ends up on the wrong side of an
    Up/Down market. A price of None means Gamma quoted nothing, which is not
    the same as a price of zero.
    """
    return {
        'slug': market.slug,
        'question': market.question,
        'volume': market.volume,
        'outcome_prices': [
            {'name': o.name, 'token_id': o.token_id, 'price': o.price}
            for o in (market.outcomes or ())
        ],
    }


def search_event_markets_checked(
        client: PolymarketClient,
        limit: int = DEFAULT_EVENT_MARKET_LIMIT,
        min_volume_usdc: float = DEFAULT_MIN_EVENT_VOLUME_USDC,
        tag: Optional[str] = None,
        offset: int = 0) -> Dict[str, object]:
    """High-volume active markets, with every exclusion counted by cause.

    Returns `{'ok', 'markets', 'summaries', 'raw_count', 'returned', 'dropped',
    'drops', 'order_field', 'min_volume_usdc', 'reason'}`.

    `ok=False` means the READ failed and `markets` being empty says nothing
    about the world. `ok=True` with an empty `markets` means Gamma answered and
    nothing cleared the floor. Those are different facts (convention 11).

    Every drop cause gets its own counter and no two causes share one
    (convention 20). The accounting identity

        returned + dropped == raw_count

    is asserted here rather than left for a reader to trust. In particular
    `inactive` and `closed` are counted separately from each other and from the
    volume filters, even though the query already asks Gamma to exclude both:
    a row that comes back contradicting its own query filter is a fact about
    Gamma worth seeing, and silently re-filtering it would hide that.
    """
    params: Dict[str, object] = {
        'limit': int(limit), 'offset': int(offset),
        'active': 'true', 'closed': 'false',
        'order': EVENT_MARKET_ORDER_FIELD, 'ascending': 'false',
    }
    if tag:
        params['tag'] = tag

    floor = float(min_volume_usdc)
    base = {'order_field': EVENT_MARKET_ORDER_FIELD, 'min_volume_usdc': floor}

    payload = client.gamma('/markets', params)
    if payload is None:
        return dict(base, ok=False, markets=[], summaries=[], raw_count=0,
                    returned=0, dropped=0, drops={}, reason='read_failed')

    rows = _unwrap_list(payload)
    drops: Counter = Counter()
    out: List[Market] = []

    for raw in rows:
        market, reason = market_from_gamma_checked(raw)
        if market is None:
            drops[reason] += 1
            continue
        if not market.active:
            drops['inactive'] += 1
            continue
        if market.closed:
            drops['closed'] += 1
            continue
        if market.volume is None:
            # Cannot measure, which is not the same as measured-and-too-small.
            drops['volume_unreadable'] += 1
            continue
        if market.volume <= floor:
            drops['volume_below_floor'] += 1
            continue
        out.append(market)

    dropped = sum(drops.values())
    if len(out) + dropped != len(rows):
        raise AssertionError(
            'event market accounting does not balance: {} returned + {} '
            'dropped != {} fetched'.format(len(out), dropped, len(rows)))

    return dict(base, ok=True, markets=out,
                summaries=[event_market_summary(m) for m in out],
                raw_count=len(rows), returned=len(out), dropped=dropped,
                drops=dict(drops), reason=None)


def search_event_markets(client: PolymarketClient,
                         limit: int = DEFAULT_EVENT_MARKET_LIMIT,
                         min_volume_usdc: float = DEFAULT_MIN_EVENT_VOLUME_USDC,
                         tag: Optional[str] = None,
                         offset: int = 0) -> List[Market]:
    """High-volume active markets, highest dollar volume first.

    The plain variant. A failed read logs and returns `[]`; a caller that needs
    to tell an outage from a genuinely empty result must use the `_checked`
    variant, which is the same split as `list_markets` / `list_markets_checked`.
    """
    result = search_event_markets_checked(client, limit, min_volume_usdc,
                                          tag, offset)
    if not result['ok']:
        logger.warning('search_event_markets read FAILED (empty list is NOT '
                       'the answer)')
    elif result['drops']:
        logger.info('search_event_markets dropped %d of %d rows: %s',
                    result['dropped'], result['raw_count'], result['drops'])
    return result['markets']


# -- category discovery: sports and politics ---------------------------------
#
# WHY THIS DOES NOT REUSE `search_event_markets(tag=...)`.
#
# Measured live 2026-08-18 against `https://gamma-api.polymarket.com`:
#
#   /markets?tag=sports  &limit=3&order=volumeNum&ascending=false&active=true
#   /markets?tag=politics&limit=3&...
#   /markets?tag=nfl     &limit=3&...
#   /markets?tag=nba     &limit=3&...
#   /markets            (no tag) &limit=3&...
#
# returned the SAME three rows every time: "Will Adanech Abiebie be the next
# Prime Minister of Ethiopia?", "Will Jesus Christ return before 2027?", "Will
# the U.S. invade Iran before 2027?". Gamma accepts `tag` on `/markets` with
# HTTP 200 and IGNORES it. That is the `order=volume` failure mode again in a
# different parameter: the request succeeds, the page looks like a filtered
# result, and it is the unfiltered global top-of-book. A sports scanner built on
# `/markets?tag=nfl` would return an Ethiopian election market and nobody would
# see anything wrong.
#
# `/events?tag_slug=` DOES filter. Same day, `tag_slug=nba` -> "Will LeBron
# James retire before next NBA season?", `tag_slug=nfl` -> "Tush Push banned for
# 2026 NFL Season?", `tag_slug=fed` -> "How many Fed rate cuts in 2026?". This
# is the same route `strategies/polymarket/weather_arb.find_weather_markets`
# already uses, and for the same reason. It is not imported from there because
# `engine` must not depend on `strategies`.
#
# THE `/events` ROUTE TAKES NO USABLE ORDER PARAMETER. `order=volumeNum` on
# `/events` returns HTTP 422 (measured; `volumeNum` is a `/markets` column,
# not an `/events` one) and `order=volume` returns 200 with the lexicographic
# text sort documented on `VOLUME_ORDER_FIELD`. So these functions send NO
# order at all and sort locally on a list already in hand, which is the same
# call `rank_weather_markets` makes.
#
# SERVER-SIDE VOLUME FILTERING DOES NOT WORK EITHER. `volume_num_min=10000` and
# `volume_min=10000` on `/events?tag_slug=soccer` both returned the identical
# 2,100 events as the unfiltered query. Ignored, like `tag`. The floor is
# applied locally.

GAMMA_EVENTS_PATH = '/events'

#: Gamma refuses `/events` past this offset. Measured 2026-08-18:
#: `tag_slug=soccer&limit=100&offset=2000` -> 100 rows, `offset=2100` -> HTTP
#: 422. So 2,100 rows is the whole reachable universe for ONE tag, whatever the
#: tag actually contains, and soccer is genuinely at that ceiling today.
#:
#: This is a hard API cap, not our budget. It matters because the 422 arrives
#: through `client.gamma` as a plain `None`, which is indistinguishable from an
#: outage. We therefore never ASK past the cap: paging stops with
#: `pagination_capped=True`, a counted fact, instead of a `read_failed` that
#: would misreport a known ceiling as a network problem (convention 11).
GAMMA_EVENTS_OFFSET_CAP = 2100

#: 21 pages of 100 is exactly `GAMMA_EVENTS_OFFSET_CAP`. Raising this alone
#: buys nothing; the cap is upstream.
DEFAULT_MAX_PAGES_PER_TAG = GAMMA_EVENTS_OFFSET_CAP // GAMMA_PAGE_SIZE

#: The sort these functions actually perform, as a label for `order_field`.
#:
#: DERIVED from `VOLUME_ORDER_FIELD`, not written out, so there is one
#: definition of which column "by volume" means (convention 23) and no second
#: order string in this module that a later edit could drift.
#:
#: The `local:` prefix is load-bearing. These functions sort LOCALLY and cannot
#: do otherwise: `/events?order=volumeNum` is HTTP 422 (`volumeNum` is a
#: `/markets` column) and `/events?order=volume` is the lexicographic text
#: sort. Reporting a bare `'volumeNum'` would tell a reader the server sorted
#: this page, and if Gamma's sort ever broke again the reader would be looking
#: at the wrong component. The value sorted on is `Market.volume`, which
#: `market_from_gamma_checked` has already parsed to a float, so the comparison
#: is numeric - it is `volumeNum` semantics reached by a different route, not
#: the text sort under another name.
LOCAL_VOLUME_ORDER = 'local:{}_desc'.format(VOLUME_ORDER_FIELD)

#: Tag slugs for `search_sports_markets`. Every one was confirmed to return a
#: non-empty first page on 2026-08-18, with the live event count at
#: `limit=100`: nfl 100, nba 21, mlb 100, nhl 4, soccer 100, tennis 100,
#: esports 100, cs2 40, counter-strike 1, dota-2 37, league-of-legends 100.
#:
#: `csgo` is NOT here. It is a real slug that Gamma accepts and it returns 0
#: events - the game is CS2 now and the old tag is dead. Listing a dead tag
#: would spend a request per scan to learn nothing, and would let "CS:GO is
#: covered" be true of the code and false of the data. CS:GO coverage is
#: `cs2` plus `counter-strike` (convention 22).
#:
#: The tags OVERLAP heavily - `esports` is close to a superset of `cs2`,
#: `dota-2` and `league-of-legends`. That is deliberate: the narrow tags catch
#: anything the broad one has not been tagged with, and every repeat is counted
#: under `duplicate_across_tags` rather than silently collapsed.
SPORTS_TAG_SLUGS = (
    'nfl', 'nba', 'mlb', 'nhl', 'soccer', 'tennis',
    'esports', 'cs2', 'counter-strike', 'dota-2', 'league-of-legends',
)

#: Tag slugs for `search_political_markets`. Live event counts at `limit=100`
#: on 2026-08-18: politics 100, us-politics 3, elections 100, world-elections
#: 33, geopolitics 100, trump 100, congress 39, supreme-court 7, courts 18,
#: fed 24, fed-rates 23, monetary-policy 3, inflation 28, gdp 18, economy 100.
#:
#: `policy`, `uk-politics` and `national-security` are accepted by Gamma and
#: return 0 events. Excluded for the same reason as `csgo`. "Policy" markets on
#: Polymarket live under `politics`, `congress` and `economy`, not under a
#: `policy` tag; the tag exists and is empty.
POLITICAL_TAG_SLUGS = (
    'politics', 'us-politics', 'elections', 'world-elections', 'geopolitics',
    'trump', 'congress', 'supreme-court', 'courts',
    'fed', 'fed-rates', 'monetary-policy', 'inflation', 'gdp', 'economy',
)

DEFAULT_CATEGORY_MARKET_LIMIT = DEFAULT_EVENT_MARKET_LIMIT

#: Every reason a raw row from the tag route does not become a returned market.
#: Parse reasons are inherited from `market_from_gamma_checked` rather than
#: restated (convention 23). No two causes share a counter and no cause has two
#: counters (convention 20): a non-dict market row is counted as `not_a_dict`
#: by the parser, not re-counted under a second name here.
CATEGORY_MARKET_DROP_REASONS = MARKET_DROP_REASONS + (
    'event_not_a_dict',
    'duplicate_across_tags',
    'inactive',
    'closed',
    'not_accepting_orders',
    'volume_unreadable',
    'volume_below_floor',
    'over_limit',
)

#: Statuses `_gamma_events_page` can report instead of a page.
EVENTS_PAGE_STATUSES = ('ok', 'read_failed', 'unexpected_shape')


def _gamma_events_page(client: PolymarketClient, tag_slug: str, offset: int,
                       limit: int) -> Tuple[Optional[List], str]:
    """One page of `/events` for a tag. `(events, status)`.

    Status is one of EVENTS_PAGE_STATUSES. `read_failed` and
    `unexpected_shape` are kept apart because one is an outage and the other is
    Gamma changing its response envelope, and the fix for each is different.

    No `order` is sent. See the section header: `/events` answers 422 for
    `volumeNum` and text-sorts `volume`.
    """
    payload = client.gamma(GAMMA_EVENTS_PATH, {
        'tag_slug': tag_slug,
        'limit': int(limit),
        'offset': int(offset),
        'active': 'true',
        'closed': 'false',
        'archived': 'false',
    })
    if payload is None:
        return None, 'read_failed'
    if isinstance(payload, list):
        return payload, 'ok'
    if isinstance(payload, dict):
        events = payload.get('events')
        if events is None:
            events = payload.get('data')
        if isinstance(events, list):
            return events, 'ok'
    return None, 'unexpected_shape'


def _market_key(market: Market) -> str:
    """Identity used to dedupe one market seen under several tags.

    `condition_id` first: it is the on-chain identifier and two Gamma rows with
    the same `conditionId` are the same tradeable market whatever their row ids
    say. Falls back to `id` then `slug`, and finally to the object's own
    identity so a row with none of the three is never merged into another one.
    """
    return (market.condition_id or market.id or market.slug
            or 'obj:{}'.format(id(market)))


def _search_tagged_markets_checked(client: PolymarketClient,
                                   tags: Tuple[str, ...],
                                   category: str,
                                   limit: int,
                                   min_volume_usdc: float,
                                   max_pages_per_tag: int) -> Dict[str, object]:
    """Shared body of `search_sports_markets` and `search_political_markets`.

    One implementation, two tag lists (convention 23). A bug fixed in the
    sports scanner that did not reach the political one would be exactly the
    "a fix at one site is not a fix" failure.

    Filter order is chosen for ATTRIBUTION, not for the outcome. A market that
    is both a duplicate and below the floor is counted once, as a duplicate,
    because the first thing wrong with the second copy of a market is that it
    is the second copy. Deduping BEFORE the volume gates also keeps
    `volume_below_floor` a count of distinct markets rather than of sightings.
    """
    floor = float(min_volume_usdc)
    tags = tuple(tags)
    base: Dict[str, object] = {
        'category': category,
        'tags_searched': list(tags),
        'order_field': LOCAL_VOLUME_ORDER,
        'min_volume_usdc': floor,
    }

    drops: Counter = Counter()
    read_failures: Counter = Counter()
    #: The markets that cleared every gate, keyed for dedupe.
    seen: Dict[str, Market] = {}
    #: Every DISTINCT market key parsed, whether or not it cleared the gates.
    #:
    #: Separate from `seen` on purpose. Deduping against the ACCEPTED map only
    #: would let a market that was dropped for low volume under tag A be
    #: evaluated again under tag B and dropped a second time, so
    #: `volume_below_floor` would count copies rather than markets and
    #: `duplicate_across_tags` would never fire for it. Attribution rule
    #: (convention 20): the first thing wrong with the second copy of a market
    #: is that it is the second copy.
    seen_keys: set = set()
    raw_count = 0
    pages = 0
    any_ok = False
    pagination_capped = False

    page_cap = min(int(max_pages_per_tag),
                   GAMMA_EVENTS_OFFSET_CAP // GAMMA_PAGE_SIZE)

    for tag_slug in tags:
        for page in range(max(0, page_cap)):
            offset = page * GAMMA_PAGE_SIZE
            events, status = _gamma_events_page(client, tag_slug, offset,
                                                GAMMA_PAGE_SIZE)
            if events is None:
                read_failures[status] += 1
                break
            any_ok = True
            pages += 1

            for event in events:
                if not isinstance(event, dict):
                    raw_count += 1
                    drops['event_not_a_dict'] += 1
                    continue
                for raw in event.get('markets') or ():
                    raw_count += 1
                    market, reason = market_from_gamma_checked(raw)
                    if market is None:
                        drops[reason] += 1
                        continue
                    key = _market_key(market)
                    if key in seen_keys:
                        drops['duplicate_across_tags'] += 1
                        continue
                    # Recorded BEFORE the quality gates, so every distinct
                    # market is judged exactly once and each drop counter
                    # counts markets rather than copies.
                    seen_keys.add(key)
                    if not market.active:
                        drops['inactive'] += 1
                        continue
                    if market.closed:
                        drops['closed'] += 1
                        continue
                    if raw.get('acceptingOrders') is False:
                        # Listed and open but the book is switched off. Not the
                        # same fact as closed, and not tradeable either.
                        drops['not_accepting_orders'] += 1
                        continue
                    if market.volume is None:
                        # Cannot measure, not measured-and-too-small.
                        drops['volume_unreadable'] += 1
                        continue
                    if market.volume <= floor:
                        drops['volume_below_floor'] += 1
                        continue
                    seen[key] = market

            if len(events) < GAMMA_PAGE_SIZE:
                break
        else:
            # Ran the full page budget without a short page: there may be more
            # behind the cap and we stopped, which is not the same as
            # exhausting the tag.
            pagination_capped = True

    if not any_ok:
        # Every page of every tag failed. An empty list here says nothing about
        # the world (convention 11).
        return dict(base, ok=False, markets=[], summaries=[], raw_count=0,
                    returned=0, dropped=0, drops={}, pages=0, truncated=False,
                    pagination_capped=False,
                    read_failures=dict(read_failures), reason='read_failed')

    # Local sort, descending. `None` volumes cannot reach here - they were
    # dropped as `volume_unreadable` - so the key is total.
    ranked = sorted(seen.values(), key=lambda m: -float(m.volume))

    cap = max(0, int(limit))
    out = ranked[:cap]
    over = len(ranked) - len(out)
    if over > 0:
        drops['over_limit'] += over

    dropped = sum(drops.values())
    if len(out) + dropped != raw_count:
        raise AssertionError(
            '{} market accounting does not balance: {} returned + {} dropped '
            '!= {} fetched'.format(category, len(out), dropped, raw_count))

    return dict(base, ok=True, markets=out,
                summaries=[event_market_summary(m) for m in out],
                raw_count=raw_count, returned=len(out), dropped=dropped,
                drops=dict(drops), pages=pages,
                truncated=bool(over), pagination_capped=pagination_capped,
                read_failures=dict(read_failures),
                reason=None if out else 'no_{}_market'.format(category))


def search_sports_markets_checked(
        client: PolymarketClient,
        limit: int = DEFAULT_CATEGORY_MARKET_LIMIT,
        min_volume_usdc: float = DEFAULT_MIN_EVENT_VOLUME_USDC,
        tags: Tuple[str, ...] = SPORTS_TAG_SLUGS,
        max_pages_per_tag: int = DEFAULT_MAX_PAGES_PER_TAG
        ) -> Dict[str, object]:
    """Active, liquid sports markets, with every exclusion counted by cause.

    Covers NFL, NBA, MLB, NHL, soccer, tennis and esports (CS2/CS:GO, Dota 2,
    League of Legends) via `SPORTS_TAG_SLUGS`.

    Returns `{'ok', 'markets', 'summaries', 'raw_count', 'returned', 'dropped',
    'drops', 'order_field', 'min_volume_usdc', 'category', 'tags_searched',
    'pages', 'truncated', 'pagination_capped', 'read_failures', 'reason'}`.

    Three outcomes that must never be pooled (convention 11):

        ok=False, reason='read_failed'       every page of every tag failed.
                                             NOT a result and NOT an empty
                                             market list.
        ok=True,  reason='no_sports_market'  Gamma answered and nothing
                                             cleared the floor.
        ok=True,  reason=None                markets found.

    A partial outage is a FOURTH thing and it is `ok=True` with a non-empty
    `read_failures`: some tags answered and some did not, so the list is real
    but incomplete. Pooling that into `ok=False` would throw away tags that
    worked; pooling it into a clean `ok=True` would hide that it is short.

    VOLUME FLOOR: `min_volume_usdc` defaults to
    `DEFAULT_MIN_EVENT_VOLUME_USDC` ($10,000) and is STRICTLY exceeded - a
    market at exactly $10,000.00 is dropped. Same constant as
    `search_event_markets` on purpose, so a market's admission does not depend
    on which scanner found it. Convention 17: this is an assumption with an
    expiry date, not a measurement.

    ORDERING IS LOCAL, descending on `market.volume`, and `order_field` says
    so. Unlike `search_event_markets` this does NOT rely on Gamma's sort,
    because the `/events` route has none that works. See the section header.
    """
    return _search_tagged_markets_checked(
        client, tags, 'sports', limit, min_volume_usdc, max_pages_per_tag)


def search_sports_markets(
        client: PolymarketClient,
        limit: int = DEFAULT_CATEGORY_MARKET_LIMIT,
        min_volume_usdc: float = DEFAULT_MIN_EVENT_VOLUME_USDC,
        tags: Tuple[str, ...] = SPORTS_TAG_SLUGS,
        max_pages_per_tag: int = DEFAULT_MAX_PAGES_PER_TAG) -> List[Market]:
    """Active sports markets over the volume floor, highest volume first.

    The plain variant. A failed read logs and returns `[]`, exactly as
    `search_event_markets` does; a caller that needs to tell an outage from a
    genuinely empty result must use `search_sports_markets_checked`. A PARTIAL
    outage also logs, because a short list that looks complete is the failure
    mode this whole module is written against.
    """
    result = search_sports_markets_checked(client, limit, min_volume_usdc,
                                           tags, max_pages_per_tag)
    return _log_category_result('search_sports_markets', result)


def search_political_markets_checked(
        client: PolymarketClient,
        limit: int = DEFAULT_CATEGORY_MARKET_LIMIT,
        min_volume_usdc: float = DEFAULT_MIN_EVENT_VOLUME_USDC,
        tags: Tuple[str, ...] = POLITICAL_TAG_SLUGS,
        max_pages_per_tag: int = DEFAULT_MAX_PAGES_PER_TAG
        ) -> Dict[str, object]:
    """Active, liquid politics markets, with every exclusion counted by cause.

    Covers elections (US and world), geopolitics, Congress and the courts, and
    the macro-policy family - Fed rate decisions, monetary policy, inflation
    and GDP - via `POLITICAL_TAG_SLUGS`.

    Same result shape, same three-way `ok`/`reason` contract and same partial
    outage rule as `search_sports_markets_checked`; `reason` is
    `'no_political_market'` when Gamma answered and nothing cleared the floor.

    VOLUME FLOOR: `$10,000`, strictly exceeded, the SAME
    `DEFAULT_MIN_EVENT_VOLUME_USDC` the sports and event scanners use. The
    threshold is deliberately not tuned per category. A per-category floor
    would mean "this market is big enough" had a different meaning depending on
    which function returned it, and any later comparison of sports against
    politics would then be comparing two different populations while looking
    like one query. One floor, one meaning, and it moves for all three at once
    or not at all. $10,000 itself is inherited, not measured here - convention
    17 applies and it has an expiry date.

    ORDERING IS LOCAL, descending on `market.volume`. See the section header
    for why `/events` cannot be asked to sort.
    """
    return _search_tagged_markets_checked(
        client, tags, 'political', limit, min_volume_usdc, max_pages_per_tag)


def search_political_markets(
        client: PolymarketClient,
        limit: int = DEFAULT_CATEGORY_MARKET_LIMIT,
        min_volume_usdc: float = DEFAULT_MIN_EVENT_VOLUME_USDC,
        tags: Tuple[str, ...] = POLITICAL_TAG_SLUGS,
        max_pages_per_tag: int = DEFAULT_MAX_PAGES_PER_TAG) -> List[Market]:
    """Active politics markets over the volume floor, highest volume first.

    The plain variant. Same logging and same `[]`-on-failure contract as
    `search_sports_markets`.
    """
    result = search_political_markets_checked(client, limit, min_volume_usdc,
                                              tags, max_pages_per_tag)
    return _log_category_result('search_political_markets', result)


def _log_category_result(name: str, result: Dict[str, object]) -> List[Market]:
    """Log what the plain variants throw away, then return the market list."""
    if not result['ok']:
        logger.warning('%s read FAILED (empty list is NOT the answer): %s',
                       name, result['read_failures'])
        return result['markets']
    if result['read_failures']:
        logger.warning('%s is INCOMPLETE: %s tag pages failed (%s). The list '
                       'is real but short.', name,
                       sum(result['read_failures'].values()),
                       result['read_failures'])
    if result['pagination_capped']:
        logger.warning('%s hit the page cap on at least one tag; there may be '
                       'markets it never asked for', name)
    if result['drops']:
        logger.info('%s dropped %d of %d rows: %s', name, result['dropped'],
                    result['raw_count'], result['drops'])
    return result['markets']


# -- Crypto Up/Down 5-minute helpers -----------------------------------------

def current_window_ts(now: Optional[float] = None,
                      duration: int = UPDOWN_5M_DURATION) -> int:
    """Unix second the current 5-minute window opened at.

    Asset-independent by construction: every crypto Up/Down market opens its
    window on the same wall-clock boundary, so btc, eth and sol share a window
    timestamp and can be polled from one cycle clock.
    """
    now = time.time() if now is None else now
    return (int(now) // duration) * duration


def updown_5m_slug(asset: str, window_ts: int) -> str:
    """`('eth', 1787022000)` -> `'eth-updown-5m-1787022000'`."""
    return UPDOWN_5M_SLUG.format(asset=str(asset).lower(), ts=int(window_ts))


def updown_15m_slug(asset: str, window_ts: int) -> str:
    """The 15m slug for the 15m window CONTAINING `window_ts`.

    Takes any timestamp inside the window and floors it, rather than trusting
    the caller to have floored to 900 already. A 5m window_ts passed straight
    into a 15m template yields a slug that resolves to nothing two thirds of
    the time, and the miss looks exactly like a market that is not indexed yet.
    """
    ts15 = (int(window_ts) // UPDOWN_15M_DURATION) * UPDOWN_15M_DURATION
    return UPDOWN_15M_SLUG.format(asset=str(asset).lower(), ts=ts15)


def btc_updown_slug(window_ts: int) -> str:
    return updown_5m_slug('btc', window_ts)


def get_updown_5m(client: PolymarketClient, asset: str,
                  window_ts: int) -> Optional[Market]:
    """The Up/Down 5m market for one asset and window. None if not indexed.

    Not-yet-indexed is common in the first seconds of a window and is a normal
    skip, not an error - callers log SKIP_NO_MARKET and move on.
    """
    return get_market_by_slug(client, updown_5m_slug(asset, window_ts))


def get_updown_5m_checked(client: PolymarketClient, asset: str, window_ts: int
                          ) -> Tuple[Optional[Market], str]:
    """As above, but distinguishes "not indexed" from "could not read"."""
    return get_market_by_slug_checked(client, updown_5m_slug(asset, window_ts))


def get_btc_updown_5m(client: PolymarketClient,
                      window_ts: int) -> Optional[Market]:
    """The BTC Up/Down 5m market for a given window open. None if not indexed."""
    return get_updown_5m(client, 'btc', window_ts)


def get_btc_updown_5m_checked(client: PolymarketClient, window_ts: int
                              ) -> Tuple[Optional[Market], str]:
    """As above, but distinguishes "not indexed" from "could not read"."""
    return get_updown_5m_checked(client, 'btc', window_ts)


def resolved_direction(client: PolymarketClient, window_ts: int,
                       asset: str = 'btc') -> Optional[str]:
    """Oracle-resolved direction of a past 5m window: 'UP', 'DOWN', or None.

    None means "not resolved yet", NOT "no move". Convention 11 applies at this
    level too: a window we cannot read the outcome of has not been observed,
    and inferring it from a tick feed would be inventing data. Callers that
    need a fallback must do so explicitly and label it.
    """
    direction, _reason = resolved_direction_checked(client, window_ts, asset)
    return direction


def resolved_direction_checked(client: PolymarketClient, window_ts: int,
                               asset: str = 'btc'
                               ) -> Tuple[Optional[str], str]:
    """Direction plus WHY it is missing: `ok`, `read_failed`, `not_listed`,
    `not_binary`, `unresolved`, or a MARKET_DROP_REASONS value.

    A run that skipped 40 windows because Gamma was down and a run that skipped
    40 windows because the oracle had not settled them are the same length and
    completely different facts.

    `asset` defaults to 'btc' so every existing caller keeps its exact previous
    behaviour. A default that changed the meaning of an existing call would
    silently repoint the graveyard's window history at a different underlying.
    """
    market, status = get_updown_5m_checked(client, asset, window_ts)
    if market is None:
        return None, 'not_listed' if status == 'not_found' else status
    if not market.is_binary:
        return None, 'not_binary'
    winner = market.resolved_outcome
    if winner is None:
        return None, 'unresolved'
    return winner.strip().upper(), 'ok'


def window_directions(client: PolymarketClient, window_starts: List[int],
                      asset: str = 'btc') -> Dict[int, Optional[str]]:
    """Resolved direction for a list of window opens, oldest key to newest.

    One Gamma call per window. At 5 minutes a strategy only ever needs ~16 of
    these per cycle, which is nothing against a 4,000 req/10s budget. With three
    assets polled it is three times that and still nothing.

    The history is per-asset and must stay that way: a streak of 4 UP windows on
    BTC is a different fact from 4 UP windows on ETH, and pooling them would
    hand streak_snapper a signal that describes neither.
    """
    return {ts: resolved_direction(client, ts, asset) for ts in window_starts}


def window_directions_checked(client: PolymarketClient,
                              window_starts: List[int],
                              asset: str = 'btc') -> Dict[str, object]:
    """`window_directions` plus a tally of why each None is None."""
    directions: Dict[int, Optional[str]] = {}
    reasons: Counter = Counter()
    for ts in window_starts:
        direction, reason = resolved_direction_checked(client, ts, asset)
        directions[ts] = direction
        reasons[reason] += 1
    return {'directions': directions, 'reasons': dict(reasons),
            'resolved': sum(1 for v in directions.values() if v is not None),
            'requested': len(window_starts)}
