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

# BTC Up/Down 5-minute markets are slugged by their unix window-open second.
BTC_UPDOWN_5M_DURATION = 300
BTC_UPDOWN_5M_SLUG = 'btc-updown-5m-{ts}'

# Gamma caps `limit`; 100 is comfortably inside it and is what the reference
# examples use.
GAMMA_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 20

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
                         order: str = 'volume', ascending: bool = False,
                         tag: Optional[str] = None,
                         offset: int = 0) -> Dict[str, object]:
    """One page of markets plus full drop accounting.

    Returns `{'ok', 'markets', 'raw_count', 'drops', 'reason'}`. `ok=False`
    means the read failed; an empty `markets` with `ok=True` means Gamma
    genuinely had nothing. Those are different facts and the plain
    `list_markets` cannot express the difference (convention 11).

    The accounting identity `raw_count - sum(drops) == len(markets)` holds.
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
                 order: str = 'volume', ascending: bool = False,
                 tag: Optional[str] = None,
                 offset: int = 0) -> List[Market]:
    """List markets, highest volume first by default."""
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
                     closed: bool = False, order: str = 'volume',
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


# -- BTC Up/Down 5-minute helpers -------------------------------------------

def current_window_ts(now: Optional[float] = None,
                      duration: int = BTC_UPDOWN_5M_DURATION) -> int:
    """Unix second the current 5-minute window opened at."""
    now = time.time() if now is None else now
    return (int(now) // duration) * duration


def btc_updown_slug(window_ts: int) -> str:
    return BTC_UPDOWN_5M_SLUG.format(ts=int(window_ts))


def get_btc_updown_5m(client: PolymarketClient,
                      window_ts: int) -> Optional[Market]:
    """The BTC Up/Down 5m market for a given window open. None if not indexed.

    Not-yet-indexed is common in the first seconds of a window and is a normal
    skip, not an error - callers log SKIP_NO_MARKET and move on.
    """
    return get_market_by_slug(client, btc_updown_slug(window_ts))


def get_btc_updown_5m_checked(client: PolymarketClient, window_ts: int
                              ) -> Tuple[Optional[Market], str]:
    """As above, but distinguishes "not indexed" from "could not read"."""
    return get_market_by_slug_checked(client, btc_updown_slug(window_ts))


def resolved_direction(client: PolymarketClient,
                       window_ts: int) -> Optional[str]:
    """Oracle-resolved direction of a past 5m window: 'UP', 'DOWN', or None.

    None means "not resolved yet", NOT "no move". Convention 11 applies at this
    level too: a window we cannot read the outcome of has not been observed,
    and inferring it from a tick feed would be inventing data. Callers that
    need a fallback must do so explicitly and label it.
    """
    direction, _reason = resolved_direction_checked(client, window_ts)
    return direction


def resolved_direction_checked(client: PolymarketClient, window_ts: int
                               ) -> Tuple[Optional[str], str]:
    """Direction plus WHY it is missing: `ok`, `read_failed`, `not_listed`,
    `not_binary`, `unresolved`, or a MARKET_DROP_REASONS value.

    A run that skipped 40 windows because Gamma was down and a run that skipped
    40 windows because the oracle had not settled them are the same length and
    completely different facts.
    """
    market, status = get_btc_updown_5m_checked(client, window_ts)
    if market is None:
        return None, 'not_listed' if status == 'not_found' else status
    if not market.is_binary:
        return None, 'not_binary'
    winner = market.resolved_outcome
    if winner is None:
        return None, 'unresolved'
    return winner.strip().upper(), 'ok'


def window_directions(client: PolymarketClient, window_starts: List[int]
                      ) -> Dict[int, Optional[str]]:
    """Resolved direction for a list of window opens, oldest key to newest.

    One Gamma call per window. At 5 minutes a strategy only ever needs ~16 of
    these per cycle, which is nothing against a 4,000 req/10s budget.
    """
    return {ts: resolved_direction(client, ts) for ts in window_starts}


def window_directions_checked(client: PolymarketClient,
                              window_starts: List[int]) -> Dict[str, object]:
    """`window_directions` plus a tally of why each None is None."""
    directions: Dict[int, Optional[str]] = {}
    reasons: Counter = Counter()
    for ts in window_starts:
        direction, reason = resolved_direction_checked(client, ts)
        directions[ts] = direction
        reasons[reason] += 1
    return {'directions': directions, 'reasons': dict(reasons),
            'resolved': sum(1 for v in directions.values() if v is not None),
            'requested': len(window_starts)}
