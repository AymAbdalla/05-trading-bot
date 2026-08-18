"""Price history and public trade prints for Polymarket.

Price history is keyed on `conditionId` (a hex string), NOT on the CLOB token
id. That trips everyone once. Trades and open interest use the same key.

A note on what this data is. A Polymarket price series is a probability series:
it lives on [0, 1], it is bounded, and it terminates in a jump to exactly 1 or
exactly 0. Feeding it to an indicator that assumes a lognormal price process -
ATR, RSI, anything expressed in percent-of-price - produces numbers, and those
numbers mean nothing. The strategies here read BTC's price series for signal
and the Polymarket series only for entry cost and resolution.
"""
import logging
from collections import Counter
from typing import Dict, List, Optional

from engine.polymarket.client import PolymarketClient
from engine.polymarket.markets import get_market_by_slug
from engine.polymarket.types import MAX_PRICE, MIN_PRICE, Trade, safe_float

logger = logging.getLogger(__name__)

VALID_INTERVALS = ('all', '1d', '1w', '1m', '3m', '6m', '1y')

# `conditionId` is a 0x-prefixed hex string. It is NOT the CLOB token id, and
# passing a token id here returns an empty history rather than an error - which
# reads as "this market has no history" and is the single easiest way to
# conclude a market is untradable when the query was simply wrong.
CONDITION_ID_PREFIX = '0x'


def _looks_like_condition_id(value: str) -> bool:
    v = str(value or '')
    if not v.startswith(CONDITION_ID_PREFIX):
        return False
    body = v[len(CONDITION_ID_PREFIX):]
    if not body:
        return False
    try:
        int(body, 16)
    except ValueError:
        return False
    return True


def price_history_checked(client: PolymarketClient, condition_id: str,
                          interval: str = '1d',
                          fidelity: Optional[int] = None) -> Dict[str, object]:
    """Historical price points, saying whether empty means failed or empty.

    Returns `{'ok', 'points', 'reason', 'raw_count', 'drops'}`. Use this
    anywhere the difference matters - an unreadable series is not an empty one
    (convention 11).

    `ok` is decided by whether the transport returned a payload at all, NOT by
    a delta on `client.stats['failures']`. The old delta approach was wrong on
    a shared client: any other thread failing a request between the two reads
    flipped this call's verdict to "failed", and a retried-then-succeeded
    request left the counter unchanged.

    Points are dropped by reason rather than silently skipped (convention 20).
    """
    if interval not in VALID_INTERVALS:
        raise ValueError(f'interval must be one of {VALID_INTERVALS}, '
                         f'got {interval!r}')
    if not _looks_like_condition_id(condition_id):
        # Not fatal - Polymarket could change the format - but it is almost
        # always a CLOB token id passed by mistake, and that returns [].
        logger.warning('price history: %r does not look like a conditionId '
                       '(expected 0x-prefixed hex); an empty result here may '
                       'mean the wrong key, not an empty market', condition_id)

    params: Dict[str, object] = {'market': condition_id, 'interval': interval}
    if fidelity is not None:
        params['fidelity'] = int(fidelity)

    payload = client.clob('/prices-history', params)
    if payload is None:
        logger.debug('price history read FAILED for %s', condition_id)
        return {'ok': False, 'points': [], 'reason': 'read_failed',
                'raw_count': 0, 'drops': {}}

    if not isinstance(payload, dict):
        return {'ok': False, 'points': [], 'reason': 'unexpected_shape',
                'raw_count': 0, 'drops': {}}

    history = payload.get('history')
    if not history:
        return {'ok': True, 'points': [], 'reason': 'no_history_yet',
                'raw_count': 0, 'drops': {}}

    drops: Counter = Counter()
    out: List[dict] = []
    for pt in history:
        if not isinstance(pt, dict):
            drops['malformed'] += 1
            continue
        ts = pt.get('t')
        price = safe_float(pt.get('p'))
        if price is None:
            # `float('NaN')` parses; a NaN probability does not exist.
            drops['bad_price'] += 1
            continue
        if not (MIN_PRICE <= price <= MAX_PRICE):
            drops['price_out_of_range'] += 1
            continue
        try:
            t = int(ts)
        except (TypeError, ValueError):
            drops['bad_timestamp'] += 1
            continue
        out.append({'t': t, 'p': price})

    out.sort(key=lambda p: p['t'])
    if drops:
        logger.info('price history %s: dropped %d of %d points %s',
                    condition_id, sum(drops.values()), len(history), dict(drops))
    return {'ok': True, 'points': out,
            'reason': None if out else 'all_points_dropped',
            'raw_count': len(history), 'drops': dict(drops)}


def price_history(client: PolymarketClient, condition_id: str,
                  interval: str = '1d',
                  fidelity: Optional[int] = None) -> List[dict]:
    """Historical price points for a market: [{'t': unix_sec, 'p': float}, ...].

    Returns [] on failure OR on a genuinely empty history. Callers that need to
    tell those apart must use `price_history_checked` - this signature cannot
    express the difference.
    """
    return price_history_checked(client, condition_id, interval,
                                 fidelity)['points']


def current_price(client: PolymarketClient, token_id: str,
                  side: str = 'buy') -> Optional[float]:
    """Best price for one side of a token. 'buy' = what you pay to buy.

    This is top-of-book only. For anything you intend to trade, use
    `orderbook.effective_ask` instead - see the note in orderbook.py.
    """
    if side not in ('buy', 'sell'):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    payload = client.clob('/price', {'token_id': str(token_id), 'side': side})
    if not isinstance(payload, dict):
        return None
    return safe_float(payload.get('price'))


def recent_trades_checked(client: PolymarketClient,
                          condition_id: Optional[str] = None,
                          limit: int = 100,
                          offset: int = 0) -> Dict[str, object]:
    """Public trade prints with drop accounting.

    Returns `{'ok', 'trades', 'raw_count', 'drops', 'reason'}`. A read failure
    and a market with no prints both used to surface as `[]`.
    """
    params: Dict[str, object] = {'limit': int(limit)}
    if offset:
        params['offset'] = int(offset)
    if condition_id:
        params['market'] = condition_id

    payload = client.data('/trades', params)
    if payload is None:
        return {'ok': False, 'trades': [], 'raw_count': 0, 'drops': {},
                'reason': 'read_failed'}
    if not isinstance(payload, list):
        return {'ok': False, 'trades': [], 'raw_count': 0, 'drops': {},
                'reason': 'unexpected_shape'}

    drops: Counter = Counter()
    out: List[Trade] = []
    for raw in payload:
        if not isinstance(raw, dict):
            drops['malformed'] += 1
            continue
        size = safe_float(raw.get('size'))
        price = safe_float(raw.get('price'))
        if size is None:
            drops['bad_size'] += 1
            continue
        if price is None:
            drops['bad_price'] += 1
            continue
        if not (MIN_PRICE <= price <= MAX_PRICE):
            drops['price_out_of_range'] += 1
            continue
        try:
            ts = int(raw['timestamp'])
        except (KeyError, TypeError, ValueError):
            drops['bad_timestamp'] += 1
            continue
        out.append(Trade(
            condition_id=str(raw.get('conditionId', condition_id or '')),
            slug=raw.get('slug'),
            outcome=raw.get('outcome'),
            side=str(raw.get('side', '')).upper(),
            size=size,
            price=price,
            timestamp=ts,
            transaction_hash=raw.get('transactionHash'),
        ))

    if drops:
        logger.info('recent_trades: dropped %d of %d prints %s',
                    sum(drops.values()), len(payload), dict(drops))
    return {'ok': True, 'trades': out, 'raw_count': len(payload),
            'drops': dict(drops), 'reason': None}


def recent_trades(client: PolymarketClient,
                  condition_id: Optional[str] = None,
                  limit: int = 100) -> List[Trade]:
    """Public trade prints, newest first. Market-scoped if condition_id given."""
    return recent_trades_checked(client, condition_id, limit)['trades']


def open_interest(client: PolymarketClient,
                  condition_id: str) -> Optional[float]:
    """Open interest for a market, or None if unreadable."""
    payload = client.data('/oi', {'market': condition_id})
    if payload is None:
        return None
    if isinstance(payload, dict):
        for key in ('oi', 'openInterest', 'value'):
            if key in payload:
                return safe_float(payload[key])
        return None
    return safe_float(payload)


def resolution_price(client: PolymarketClient, slug: str,
                     outcome: str) -> Optional[float]:
    """Settlement value of one outcome: 1.0, 0.0, or None if unresolved.

    Strict by design. A market quoting 0.99 has not resolved. Booking that as a
    win is how a paper log drifts optimistic right where it matters most, since
    the near-certain trades are exactly the ones whose rare loss is expensive.
    """
    value, _reason = resolution_price_checked(client, slug, outcome)
    return value


def resolution_price_checked(client: PolymarketClient, slug: str, outcome: str):
    """`resolution_price` plus WHY it is None.

    Reasons: `ok`, `no_market`, `not_binary`, `no_such_outcome`, `unresolved`,
    `counterparty_not_settled`. The last one is the subtle case - a brand-new
    market can print 0 on a side that has simply never traded, which is not the
    same as that side having lost.
    """
    market = get_market_by_slug(client, slug)
    if market is None:
        return None, 'no_market'
    if not market.is_binary:
        return None, 'not_binary'
    o = market.outcome(outcome)
    if o is None:
        return None, 'no_such_outcome'
    if o.price == 1.0:
        return 1.0, 'ok'
    if o.price == 0.0:
        # Only meaningful once the OTHER side is at 1.0.
        other = [x for x in market.outcomes if x.token_id != o.token_id]
        if other and other[0].price == 1.0:
            return 0.0, 'ok'
        return None, 'counterparty_not_settled'
    return None, 'unresolved'
