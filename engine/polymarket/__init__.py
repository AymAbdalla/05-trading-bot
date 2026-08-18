"""Polymarket integration: read-only data layer + paper execution.

Added under D-267 (multi-asset scope expansion). Prediction markets are a new
asset class alongside crypto, equities, futures and options - additive, never a
replacement for the Binance.US spot path.

Nothing in this package can place a real order. Live execution would need the
CLOB SDK V2 with wallet-based EIP-712 signing and is out of scope until paper
mode proves a strategy through the graveyard AND Aym approves.

    from engine.polymarket import PolymarketClient, PolymarketPaperAdapter
    from engine.polymarket import get_btc_updown_5m, fetch_orderbook, walk_book

    client = PolymarketClient()
    market = get_btc_updown_5m(client, current_window_ts())
    book = fetch_orderbook(client, market.token_id('Up'))
    walk = walk_book(book, shares=100, limit_price=0.52)   # real average entry

## The `_checked` variants

Most read functions come in two shapes. The plain one returns the data and logs
what it dropped; the `_checked` one returns the data AND the drop counts, by
reason. Use `_checked` anywhere a short result could be mistaken for a real
observation - an empty list from a failed read is not an empty market
(convention 11), and a filter loop that skips must say why it skipped
(convention 20).
"""
from engine.polymarket.client import (CLOB_HOST, DATA_HOST, GAMMA_HOST,
                                      NonFiniteJSONError, PolymarketClient,
                                      parse_embedded_json,
                                      parse_embedded_json_checked,
                                      parse_embedded_list)
from engine.polymarket.context import (build_context, context_health,
                                       fetch_btc_spot, fetch_btc_spot_checked,
                                       price_windows, price_windows_checked,
                                       resolved_windows,
                                       resolved_windows_checked)
from engine.polymarket.markets import (btc_updown_slug, current_window_ts,
                                       get_btc_updown_5m,
                                       get_btc_updown_5m_checked,
                                       get_market_by_slug,
                                       get_market_by_slug_checked,
                                       list_all_markets, list_markets,
                                       list_markets_checked, market_from_gamma,
                                       market_from_gamma_checked,
                                       resolved_direction,
                                       resolved_direction_checked,
                                       search_event_markets,
                                       search_event_markets_checked,
                                       search_markets, search_markets_checked,
                                       window_directions,
                                       window_directions_checked)
from engine.polymarket.orderbook import (effective_ask, fetch_midpoint,
                                         fetch_orderbook, fetch_orderbooks,
                                         fetch_orderbooks_checked,
                                         fetch_spread, orderbook_from_api,
                                         walk_book)
from engine.polymarket.paper_adapter import (PaperPosition,
                                             PolymarketPaperAdapter)
from engine.polymarket.prices import (current_price, open_interest,
                                      price_history, price_history_checked,
                                      recent_trades, recent_trades_checked,
                                      resolution_price,
                                      resolution_price_checked)
from engine.polymarket.types import (LOSING_REDEMPTION, MAX_PRICE, MIN_PRICE,
                                     MIN_SHARES, PRICE_TICK,
                                     WINNING_REDEMPTION, Fill, Market,
                                     Orderbook, Outcome, PriceLevel, Trade,
                                     WalkResult, parse_levels,
                                     parse_levels_counted, safe_float)

__all__ = [
    # client
    'PolymarketClient', 'parse_embedded_json', 'parse_embedded_json_checked',
    'parse_embedded_list', 'NonFiniteJSONError',
    'GAMMA_HOST', 'CLOB_HOST', 'DATA_HOST',
    # markets
    'Market', 'Outcome', 'market_from_gamma', 'market_from_gamma_checked',
    'get_market_by_slug', 'get_market_by_slug_checked',
    'list_markets', 'list_markets_checked', 'list_all_markets',
    'search_markets', 'search_markets_checked',
    'search_event_markets', 'search_event_markets_checked',
    'get_btc_updown_5m', 'get_btc_updown_5m_checked', 'btc_updown_slug',
    'current_window_ts', 'resolved_direction', 'resolved_direction_checked',
    'window_directions', 'window_directions_checked',
    # orderbook
    'Orderbook', 'PriceLevel', 'WalkResult', 'orderbook_from_api',
    'fetch_orderbook', 'fetch_orderbooks', 'fetch_orderbooks_checked',
    'fetch_midpoint', 'fetch_spread', 'walk_book', 'effective_ask',
    'parse_levels', 'parse_levels_counted',
    # prices
    'price_history', 'price_history_checked', 'current_price',
    'recent_trades', 'recent_trades_checked', 'open_interest',
    'resolution_price', 'resolution_price_checked', 'Trade',
    # live context assembly
    'build_context', 'context_health', 'resolved_windows',
    'resolved_windows_checked', 'price_windows', 'price_windows_checked',
    'fetch_btc_spot', 'fetch_btc_spot_checked',
    # paper execution
    'PolymarketPaperAdapter', 'PaperPosition', 'Fill',
    # constants and helpers
    'PRICE_TICK', 'MIN_SHARES', 'WINNING_REDEMPTION', 'LOSING_REDEMPTION',
    'MIN_PRICE', 'MAX_PRICE', 'safe_float',
]
