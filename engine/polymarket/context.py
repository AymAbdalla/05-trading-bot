"""Build a live MarketContext from the Polymarket public APIs.

This is the seam between the data layer and the strategies. Without it the two
halves do not touch: strategies take a `MarketContext` and the client returns
raw JSON, and nothing turns one into the other.

Kept separate from `markets.py` on purpose. A strategy must never hold a client
handle - if it can fetch, it can fetch mid-decision, and then the decision is no
longer reproducible from its logged context. Everything network-facing happens
here, once, before `evaluate` is called.

Window directions come from the Gamma oracle (`outcomePrices` exactly 1/0),
tagged `source='oracle'`. Windows the oracle has not resolved are DROPPED, not
inferred from price. Inferring them would put fabricated data into the exact
place - the boundary windows - where a fade strategy makes its decisions. Every
drop is counted by reason (convention 20): "the oracle is behind" and "Gamma is
down" produce the same short list and are completely different facts.
"""
import logging
import time
from collections import Counter
from typing import Dict, List, Optional, Tuple

import requests

from engine.polymarket.client import PolymarketClient
from engine.polymarket.markets import (BTC_UPDOWN_5M_DURATION, btc_updown_slug,
                                       current_window_ts, get_btc_updown_5m,
                                       get_btc_updown_5m_checked,
                                       get_market_by_slug)
from engine.polymarket.orderbook import fetch_orderbook
from engine.polymarket.types import safe_float
from strategies.polymarket.base import MarketContext, Window

logger = logging.getLogger(__name__)

BTC_UPDOWN_15M_SLUG = 'btc-updown-15m-{ts}'
BTC_UPDOWN_15M_DURATION = 900

# Public spot sources, in preference order. No API key on any of them; the
# MoonDev tick feed is deliberately not used (D-267).
#
# binance.com is GEO-BLOCKED from this machine and, worse, answers HTTP 200
# with an error body rather than a 4xx - so a status-code check passes it and
# the failure only surfaces when the price key is missing. Binance.US is the
# endpoint this project already uses for crypto and is the one that works here.
SPOT_SOURCES = (
    ('binance_us', 'https://api.binance.us/api/v3/ticker/price?symbol=BTCUSDT', 'price'),
    ('coinbase', 'https://api.coinbase.com/v2/prices/BTC-USD/spot', None),
)

# What these markets actually settle against. Read off `cryptoMarketConfig` on
# a live btc-updown-5m market, 2026-08-17: {'id': 'btc-5m-twap-60', 'asset':
# 'btc', 'duration': '5m', 'twapEnabled': True, 'twapLookbackSeconds': 60}, and
# confirmed by the market description, which names the Chainlink BTC/USD
# 60-second TWAP stream as the resolution source.
#
# THIS MATTERS MORE THAN IT LOOKS. It means:
#   1. The strike is the Chainlink TWAP at window open. Gamma does NOT publish
#      it - there is no openPrice, strikePrice, or equivalent field anywhere on
#      the market object. moondevonyt's mid_price_continuation reads a
#      "Polymarket crypto-price openPrice" that does not exist on this market
#      shape.
#   2. Exchange spot is a PROXY for the settlement price, not the settlement
#      price. A 60-second TWAP lags spot during exactly the fast moves that
#      generate signals, and mid_price_continuation's entry gate is 5 bps -
#      about $32 at $64k BTC, which is well inside the range a TWAP and a spot
#      print can disagree by mid-move.
#
# So `strike` is left None unless a caller supplies a real one. Substituting
# spot would put a number in the field that is wrong precisely when the
# strategy is deciding, and the resulting entries would look measured.
CRYPTO_CONFIG_KEY = 'cryptoMarketConfig'

# Keys probed in case Polymarket ever starts publishing the strike. As of
# 2026-08-17 none of them exist on a btc-updown-5m market.
STRIKE_KEYS = ('openPrice', 'open_price', 'strikePrice')


def fetch_btc_spot_checked(client: PolymarketClient) -> Dict[str, object]:
    """Live BTC spot from a public exchange, with per-source failure reasons.

    Returns `{'spot', 'source', 'failures'}`. `spot=None` means every source
    failed and `failures` names why each one did, rather than leaving a bare
    "all sources failed" line that cannot distinguish a geo-block from a
    timeout from a schema change.

    The returned price is validated finite and strictly positive. It is NOT
    bounded above: any plausible-range constant would be a hardcoded threshold
    with an expiry date (convention 17), and BTC has repeatedly outrun the
    ranges people picked for it.

    Note this deliberately bypasses `client.get` - these are not Polymarket
    hosts, so the per-host rate limiters and the Polymarket retry policy do not
    apply. It reuses only the session's connection pool.
    """
    failures: Dict[str, str] = {}
    for name, url, key in SPOT_SOURCES:
        try:
            resp = client.session.get(url, timeout=client.timeout)
        except requests.RequestException as exc:
            failures[name] = f'{type(exc).__name__}: {exc}'
            continue

        if resp.status_code != 200:
            failures[name] = f'HTTP {resp.status_code}'
            continue

        try:
            payload = resp.json()
        except ValueError as exc:
            failures[name] = f'bad JSON: {exc}'
            continue

        try:
            raw = payload[key] if key else payload['data']['amount']
        except (KeyError, TypeError, IndexError):
            # This is the binance.com failure mode: HTTP 200 carrying an error
            # body. The status code says fine, the schema says otherwise.
            failures[name] = 'price key missing (200 with an error body?)'
            continue

        spot = safe_float(raw)
        if spot is None:
            failures[name] = f'non-finite price {raw!r}'
            continue
        if spot <= 0:
            failures[name] = f'non-positive price {spot!r}'
            continue

        if failures:
            logger.info('BTC spot from %s after %d failed source(s): %s',
                        name, len(failures), failures)
        return {'spot': spot, 'source': name, 'failures': failures}

    logger.warning('all %d BTC spot sources failed; spot is unknown: %s',
                   len(SPOT_SOURCES), failures)
    return {'spot': None, 'source': None, 'failures': failures}


def fetch_btc_spot(client: PolymarketClient) -> Optional[float]:
    """Live BTC spot from a public exchange. None if every source fails.

    None means "we do not know the price", and every strategy that needs spot
    skips on it. A stale or invented price here would flow straight into the
    strike comparison that decides which side is leading.
    """
    return fetch_btc_spot_checked(client)['spot']


def resolved_windows_checked(client: PolymarketClient, window_ts: int,
                             lookback: int = 16) -> Dict[str, object]:
    """The last `lookback` COMPLETED 5m windows the oracle has resolved.

    Returns `{'windows', 'requested', 'skips'}` with windows oldest-first and
    `skips` counting why each missing one is missing: `read_failed`,
    `not_listed`, `unresolved`, `not_binary`.

    That split is the whole point. A caller seeing 3 windows out of 16 needs to
    know whether the oracle is 13 windows behind (wait) or Gamma refused 13
    reads (skip the cycle entirely). Both used to produce an identical short
    list, and a streak computed off it would be measuring an outage.
    """
    out: List[Window] = []
    skips: Counter = Counter()

    for i in range(lookback, 0, -1):
        ts = window_ts - i * BTC_UPDOWN_5M_DURATION
        market, status = get_btc_updown_5m_checked(client, ts)
        if market is None:
            skips['not_listed' if status == 'not_found' else status] += 1
            continue
        if not market.is_binary:
            skips['not_binary'] += 1
            continue
        winner = market.resolved_outcome
        if winner is None:
            skips['unresolved'] += 1
            continue
        direction = winner.strip().upper()
        # The oracle gives us direction but not the open/close prices. Encode
        # the direction as a unit move so streak logic works; anything that
        # needs true USD magnitude (the stretch filter) must use price windows
        # instead, and `magnitude_available` in the context says which it got.
        out.append(Window(ts=ts, open=0.0,
                          close=(1.0 if direction == 'UP' else -1.0),
                          direction=direction, source='oracle'))

    if skips:
        logger.info('resolved_windows: %d/%d resolved, skipped %s',
                    len(out), lookback, dict(skips))
    return {'windows': out, 'requested': lookback, 'skips': dict(skips)}


def resolved_windows(client: PolymarketClient, window_ts: int,
                     lookback: int = 16) -> List[Window]:
    """Oracle-resolved completed 5m windows, oldest first.

    Unresolved windows are omitted rather than guessed, so a short list means
    "the oracle is behind" or "the read failed", never "the market was quiet".
    Use `resolved_windows_checked` when you need to tell those apart.
    """
    return resolved_windows_checked(client, window_ts, lookback)['windows']


def price_windows_checked(candles: dict, lookback: int = 16) -> Dict[str, object]:
    """Completed 5m windows built from BTC OHLCV, tagged `source='price'`.

    These carry real USD magnitudes, which the oracle windows do not, so a
    stretch-filter strategy needs these. They can disagree with the oracle at
    the boundary - that is the cost of having magnitude.

    Malformed candles are counted, not raised on and not silently skipped. The
    previous version had no error handling at all: one `None` close in an OHLCV
    dict raised a TypeError out of context assembly and took down the cycle.
    """
    closes = candles.get('closes') or []
    opens = candles.get('opens') or []
    timestamps = candles.get('timestamps') or []
    n = min(len(closes), len(opens), len(timestamps))

    drops: Counter = Counter()
    if not (len(closes) == len(opens) == len(timestamps)):
        # Truncating to the shortest array is the only safe read, but a caller
        # must know its arrays were ragged - silently using the first n of each
        # can misalign a close with another bar's timestamp.
        drops['ragged_arrays'] += max(len(closes), len(opens),
                                      len(timestamps)) - n

    out: List[Window] = []
    for i in range(max(0, n - lookback), n):
        o = safe_float(opens[i])
        c = safe_float(closes[i])
        if o is None or c is None:
            drops['bad_ohlc'] += 1
            continue
        try:
            ts = int(timestamps[i])
        except (TypeError, ValueError):
            drops['bad_timestamp'] += 1
            continue
        # Millisecond epochs are ~1e12, seconds ~1e9.
        out.append(Window(ts=ts // 1000 if ts > 1e11 else ts, open=o, close=c,
                          direction='UP' if c >= o else 'DOWN', source='price'))

    if drops:
        logger.info('price_windows: built %d, dropped %s', len(out), dict(drops))
    return {'windows': out, 'drops': dict(drops)}


def price_windows(candles: dict, lookback: int = 16) -> List[Window]:
    """Completed 5m windows built from BTC OHLCV, tagged `source='price'`."""
    return price_windows_checked(candles, lookback)['windows']


def _probe_strike(raw: dict) -> Optional[float]:
    """Look for a published strike. Returns None because there isn't one.

    Deliberately NOT derived from spot. See CRYPTO_CONFIG_KEY above: the strike
    is a Chainlink TWAP that Gamma does not publish. These keys are probed
    anyway in case Polymarket starts exposing it.
    """
    for key in STRIKE_KEYS:
        if raw.get(key) is not None:
            value = safe_float(raw[key])
            if value is not None:
                return value
    return None


def build_context(client: PolymarketClient,
                  window_ts: Optional[int] = None,
                  windows: Optional[List[Window]] = None,
                  lookback: int = 16,
                  include_15m: bool = False,
                  spot: Optional[float] = None,
                  strike: Optional[float] = None,
                  atr14: Optional[float] = None) -> MarketContext:
    """Assemble a full live context for one 5-minute window.

    Pass `windows` (from `price_windows`) when a strategy needs USD magnitudes;
    otherwise oracle-resolved directions are fetched. Pass `strike` when you
    have a real Chainlink TWAP reading - it is the only way the strike-based
    strategies can run, and there is no correct default (see CRYPTO_CONFIG_KEY).

    Every field that could not be read stays None so the strategies' own gates
    catch it. This function never substitutes a default for missing market data.
    """
    window_ts = current_window_ts() if window_ts is None else window_ts
    now = time.time()

    market = get_btc_updown_5m(client, window_ts)
    books = {}
    if market is not None:
        for outcome in market.outcomes:
            book = fetch_orderbook(client, outcome.token_id)
            if book is not None:
                books[outcome.token_id] = book
        if strike is None:
            strike = _probe_strike(market.raw)

    if windows is None:
        windows = resolved_windows(client, window_ts, lookback)

    market_15m = None
    books_15m = {}
    if include_15m:
        ts15 = (window_ts // BTC_UPDOWN_15M_DURATION) * BTC_UPDOWN_15M_DURATION
        market_15m = get_market_by_slug(
            client, BTC_UPDOWN_15M_SLUG.format(ts=ts15))
        if market_15m is not None:
            for outcome in market_15m.outcomes:
                book = fetch_orderbook(client, outcome.token_id)
                if book is not None:
                    books_15m[outcome.token_id] = book

    if spot is None:
        spot = fetch_btc_spot(client)

    lead_bps = None
    if spot is not None and strike:  # `strike` truthy also guards strike == 0
        lead_bps = (spot - strike) / strike * 10_000

    return MarketContext(
        window_ts=window_ts,
        windows=windows,
        market=market,
        books=books,
        spot=spot,
        strike=strike,
        seconds_into_window=now - window_ts,
        market_15m=market_15m,
        books_15m=books_15m,
        lead_bps=lead_bps,
        atr14=atr14,
    )


def context_health(ctx: MarketContext) -> dict:
    """What the context is missing. Log this next to every decision.

    A SKIP for `no_orderbook` and a SKIP for `not_stretched` mean completely
    different things - one is a data outage, the other is the strategy working.
    Without this they look identical in the aggregate counts.
    """
    config = {}
    book_drops: Counter = Counter()
    if ctx.market is not None:
        config = getattr(ctx.market, 'raw', {}).get(CRYPTO_CONFIG_KEY) or {}
    for book in ctx.books.values():
        for reason, n in (getattr(book, 'drops', None) or {}).items():
            book_drops[reason] += n
    return {
        'has_market': ctx.market is not None,
        'books': len(ctx.books),
        'book_level_drops': dict(book_drops),
        'has_spot': ctx.spot is not None,
        'spot_is_proxy_for_settlement': ctx.spot is not None,
        'has_strike': ctx.strike is not None,
        'settlement_source': config.get('id'),
        'twap_lookback_seconds': config.get('twapLookbackSeconds'),
        'strike_published_by_gamma': False,
        'windows': len(ctx.windows),
        'window_sources': sorted({w.source for w in ctx.windows}),
        'magnitude_available': any(w.source == 'price' for w in ctx.windows),
        'has_15m': ctx.market_15m is not None,
        'seconds_into_window': (None if ctx.seconds_into_window is None
                                else round(ctx.seconds_into_window, 1)),
    }
