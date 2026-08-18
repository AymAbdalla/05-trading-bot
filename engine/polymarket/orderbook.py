"""Live CLOB orderbook fetching and taker-fill simulation.

The important function here is `walk_book`. Every naive prediction-market
backtest assumes you get filled at the best ask for whatever size you want.
On a 5-minute BTC market the top level is often 5-20 shares, so a 100-share
order eats three or four levels and the real average entry is materially worse
than the quote. On a binary contract that difference is the entire edge: at a
54% win rate, 2c of extra entry cost is roughly half the theoretical margin.

So: never price a fill off `best_ask`. Walk the book.
"""
import logging
import time
from collections import Counter
from typing import Dict, List, Optional, Sequence

from engine.polymarket.client import PolymarketClient
from engine.polymarket.types import (MAX_PRICE, MIN_PRICE, Orderbook,
                                     WalkResult, parse_levels_counted,
                                     safe_float)

logger = logging.getLogger(__name__)


def orderbook_from_api(token_id: str, payload: dict) -> Optional[Orderbook]:
    """Build an Orderbook from a /book response, sorting both sides ourselves.

    Returns None for an empty book. An empty book is not a book with a zero
    price - there is simply nobody quoting, and every caller must skip.

    Rows the venue sent that we refused are counted by reason and carried on
    `Orderbook.drops`, not discarded silently (convention 20). A book that
    looks thin because the venue is thin and one that looks thin because we
    threw away half its levels are different problems.
    """
    if not isinstance(payload, dict):
        return None
    bids, bid_drops = parse_levels_counted(payload.get('bids'), descending=True)
    asks, ask_drops = parse_levels_counted(payload.get('asks'), descending=False)
    if not bids and not asks:
        return None

    drops: Counter = Counter()
    for reason, n in bid_drops.items():
        drops['bid_' + reason] += n
    for reason, n in ask_drops.items():
        drops['ask_' + reason] += n
    if drops:
        logger.info('book %s: dropped %d raw levels %s',
                    token_id, sum(drops.values()), dict(drops))

    def _f(key, default):
        return safe_float(payload.get(key), default)

    book = Orderbook(
        token_id=str(token_id),
        bids=bids,
        asks=asks,
        timestamp=int(time.time()),
        tick_size=_f('tick_size', 0.01),
        min_order_size=_f('min_order_size', 5),
        drops=dict(drops),
    )
    if book.is_crossed():
        # bid > ask is not free money, it is a stale or corrupt snapshot.
        # Refusing to trade it is the only safe read.
        logger.warning('crossed book on token %s (bid %.4f > ask %.4f), '
                       'treating as unusable', token_id,
                       book.best_bid, book.best_ask)
        return None
    if book.is_one_sided:
        logger.debug('book %s is one-sided (%d bids, %d asks)',
                     token_id, len(book.bids), len(book.asks))
    return book


def fetch_orderbook(client: PolymarketClient,
                    token_id: str) -> Optional[Orderbook]:
    """Live book for one token. None on any failure or empty book."""
    payload = client.clob('/book', {'token_id': str(token_id)})
    if payload is None:
        return None
    return orderbook_from_api(token_id, payload)


def fetch_orderbooks(client: PolymarketClient,
                     token_ids: Sequence[str]) -> Dict[str, Optional[Orderbook]]:
    """Books for several tokens. Keys are always present, values may be None.

    Every requested token gets a key so a caller can tell "we asked and there
    was no book" apart from "we never asked".
    """
    return {str(t): fetch_orderbook(client, t) for t in token_ids}


def fetch_orderbooks_checked(client: PolymarketClient,
                             token_ids: Sequence[str]) -> Dict[str, object]:
    """`fetch_orderbooks` plus a count of how many came back unusable."""
    books = fetch_orderbooks(client, token_ids)
    missing = [t for t, b in books.items() if b is None]
    return {'books': books, 'requested': len(books),
            'usable': len(books) - len(missing), 'missing': missing}


def fetch_midpoint(client: PolymarketClient, token_id: str) -> Optional[float]:
    payload = client.clob('/midpoint', {'token_id': str(token_id)})
    if not isinstance(payload, dict):
        return None
    return safe_float(payload.get('mid'))


def fetch_spread(client: PolymarketClient, token_id: str) -> Optional[float]:
    payload = client.clob('/spread', {'token_id': str(token_id)})
    if not isinstance(payload, dict):
        return None
    return safe_float(payload.get('spread'))


def walk_book(book: Orderbook, shares: float, limit_price: float,
              side: str = 'BUY') -> WalkResult:
    """Simulate a taker order against real book depth.

    Consumes levels best-first until the order is filled, the limit is crossed,
    or the book runs out. Returns a WalkResult carrying the average price, every
    level consumed, and the slippage against top-of-book.

    A partial fill is a real outcome and is reported as one. Rounding a partial
    up to a full fill is how a backtest invents liquidity that was never there;
    silently discarding it as "no trade" is the same lie in the other direction.

    `side='BUY'` walks asks (paying up). `side='SELL'` walks bids (hitting down).

    Raises on a NaN/infinite size or limit, and on a non-positive size. Those
    are caller bugs, and the alternative - a zero-fill WalkResult - reads
    identically to "the book was empty", which files a code defect under the
    venue's liquidity (convention 11). An out-of-range limit is only warned
    about: book levels are already constrained to [0, 1], so a limit of 1.5
    behaves exactly like 1.0 and a negative one fills nothing, both correct.
    """
    side = str(side).upper()
    if side not in ('BUY', 'SELL'):
        raise ValueError(f'side must be BUY or SELL, got {side!r}')

    shares_f = safe_float(shares)
    limit_f = safe_float(limit_price)
    if shares_f is None:
        raise ValueError(f'shares must be a finite number, got {shares!r}')
    if limit_f is None:
        raise ValueError(
            f'limit_price must be a finite number, got {limit_price!r}')
    if shares_f <= 0:
        raise ValueError(f'shares must be positive, got {shares_f!r}')
    if not (MIN_PRICE <= limit_f <= MAX_PRICE):
        logger.warning('limit_price %r is outside [%s, %s]; a binary share '
                       'never settles outside it', limit_f, MIN_PRICE, MAX_PRICE)

    levels = book.asks if side == 'BUY' else book.bids
    top = book.best_ask if side == 'BUY' else book.best_bid

    remaining = shares_f
    filled = 0.0
    cost = 0.0
    consumed: List = []
    exhausted = False

    for lvl in levels:
        if remaining <= 1e-9:
            break
        # BUY: only levels at or below our limit. SELL: at or above.
        if side == 'BUY' and lvl.price > limit_f + 1e-12:
            break
        if side == 'SELL' and lvl.price < limit_f - 1e-12:
            break
        take = min(remaining, lvl.size)
        filled += take
        cost += take * lvl.price
        consumed.append((lvl.price, take))
        remaining -= take
    else:
        # Ran off the end of the book with size still to fill.
        exhausted = remaining > 1e-9

    avg = (cost / filled) if filled > 1e-9 else None
    slippage = (avg - top) if (avg is not None and top is not None) else None
    if side == 'SELL' and slippage is not None:
        slippage = -slippage  # selling worse means a LOWER price

    return WalkResult(
        requested_shares=shares_f,
        filled_shares=filled,
        avg_price=avg,
        cost_usdc=cost,
        levels_consumed=tuple(consumed),
        limit_price=limit_f,
        exhausted_book=exhausted,
        slippage_vs_top=slippage,
    )


def effective_ask(book: Orderbook, shares: float) -> Optional[float]:
    """Average price to buy `shares` right now, ignoring any limit.

    This is the number a strategy should compare against its price cap, not
    `book.best_ask`. Returns None if the book cannot fill the size at all.
    """
    walk = walk_book(book, shares, limit_price=MAX_PRICE, side='BUY')
    return walk.avg_price if walk.fully_filled else None
