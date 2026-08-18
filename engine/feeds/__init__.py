"""Market-data feeds that are not trading venues.

A "feed" here is a read-only public data source the bot observes but never
sends orders to. Contrast with `engine/adapters/` (execution venues) and
`engine/polymarket/` (a venue the bot paper-trades on).

Modules:
    hyperliquid_client - whale (>$100k) open-position poller for Hyperliquid.
    liquidation_recorder - venue liquidation tape recorder (Bybit only; see the
        module for why Binance and Hyperliquid are not usable sources).
    noaa_weather - airport METAR temperature from aviationweather.gov. THE
        resolution-relevant reading for Polymarket city temperature markets.
    open_meteo - downtown grid-cell temperature. The consumer-app anchor,
        DIAGNOSTIC ONLY: nothing resolves on it.

`noaa_weather` and `open_meteo` are a PAIR. The airport-versus-downtown gap is
the entire thesis of `strategies/polymarket/weather_arb.py`, it comes from a
social media post claiming 3 to 8F, and this repo has never measured it. The
two feeds are the instrument for measuring it; the recorder that would write
paired readings to a table does not exist yet.
"""
