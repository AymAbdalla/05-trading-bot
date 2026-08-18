"""The crypto assets whose Polymarket Up/Down windows the shadow loop trades.

One registry, so the mapping from "btc" to a Gamma slug prefix, a Binance
symbol, a Coinbase pair and a DataCollector pair string is written down exactly
once. Before this file the string `BTCUSDT` appeared in `strike.py`, `BTC/USDT`
in `shadow_loop.py`, `BTC-USD` in `context.py` and `btc-updown-5m-{ts}` in
`markets.py`, and adding a second asset meant finding all four. Convention 23
says a fix at one site is not a fix; the same logic applies to a constant.

## What is verified here, and what is not

Every field below was read off a live endpoint on 2026-08-18, not assumed:

    asset  gamma slug prefix        binance.us  coinbase  spot at check
    btc    btc-updown-5m-{ts}       BTCUSDT     BTC-USD   64162.10
    eth    eth-updown-5m-{ts}       ETHUSDT     ETHUSDT   1897.74
    sol    sol-updown-5m-{ts}       SOLUSDT     SOL-USD   75.96

All three carry the SAME settlement mechanic, confirmed from each market's
`cryptoMarketConfig`: `btc-5m-twap-60`, `eth-5m-twap-60`, `sol-5m-twap-60`, all
with `twapEnabled: True` and `twapLookbackSeconds: 60`. That is what makes
reusing the BTC strategies on ETH and SOL legitimate rather than hopeful: it is
the same instrument with a different underlying, so the strike proxy, the noise
floor and every window gate carry over unchanged.

All three also have a 15-minute market (`{asset}-updown-15m-{ts}`), so the
corridor strategies have their second leg on every registered asset.

`xrp-updown-5m-{ts}` and `doge-updown-5m-{ts}` also exist and are also
`*-5m-twap-60`. They are deliberately NOT registered here: their Gamma markets
were verified, their Binance.US and Coinbase symbols were NOT. Registering them
with a guessed exchange symbol would put an unverified string in a price path,
and a wrong symbol does not fail loudly, it fails as a missing spot that reads
like an outage. Verify the two price endpoints first, then add the row.

## Adding an asset

Add the row, add the key to `SHADOW_ASSETS`, and check the three endpoints
first. Nothing else in the loop needs to change: the shadow loop builds its
per-asset state by iterating this registry.
"""
from typing import Dict, NamedTuple, Optional, Tuple


class CryptoAsset(NamedTuple):
    """One tradable underlying and every name the codebase knows it by."""

    #: Our internal id AND the Polymarket slug prefix. These are the same
    #: string on purpose - Polymarket slugs its crypto markets by lowercase
    #: ticker, so a second mapping would be a second thing to keep in sync.
    key: str
    #: Binance.US symbol. Used for BOTH the 1m klines the strike proxy rebuilds
    #: its TWAP from and the spot ticker. They must be the same symbol or the
    #: lead_bps comparison is between two different instruments.
    binance_symbol: str
    #: Coinbase spot pair, the fallback source when Binance.US fails.
    coinbase_pair: str
    #: `DataCollector.fetch_ohlcv` pair notation, for the 5m magnitude candles
    #: that ATR and the streak filter are computed from.
    candle_pair: str
    #: Human label for logs.
    label: str


#: Every asset this codebase has verified endpoints for.
ASSETS: Dict[str, CryptoAsset] = {
    'btc': CryptoAsset('btc', 'BTCUSDT', 'BTC-USD', 'BTC/USDT', 'Bitcoin'),
    'eth': CryptoAsset('eth', 'ETHUSDT', 'ETH-USD', 'ETH/USDT', 'Ethereum'),
    'sol': CryptoAsset('sol', 'SOLUSDT', 'SOL-USD', 'SOL/USDT', 'Solana'),
}

#: The assets the shadow loop actually polls, in a FIXED order.
#:
#: Fixed because the accounting identity is `evaluations == cycles *
#: n_strategies * n_assets` and a set would make the per-cycle evaluation order
#: non-deterministic between runs, which turns a diff of two sessions' logs into
#: noise. Also: BTC stays first so a reader scanning a log sees the series that
#: has history before the two that do not.
SHADOW_ASSETS: Tuple[str, ...] = ('btc', 'eth', 'sol')


def get_asset(key: str) -> CryptoAsset:
    """The registry row for `key`. Raises KeyError on an unknown asset.

    Deliberately raises rather than returning None. An unknown asset key is a
    programming error at wiring time, and the failure mode of returning None
    here is a `None.binance_symbol` several frames away from the actual typo.
    """
    try:
        return ASSETS[str(key).lower()]
    except KeyError:
        raise KeyError(
            'unknown asset {!r}; registered: {}'.format(
                key, ', '.join(sorted(ASSETS)))) from None


def shadow_assets() -> Tuple[CryptoAsset, ...]:
    """The registry rows for `SHADOW_ASSETS`, in order."""
    return tuple(ASSETS[k] for k in SHADOW_ASSETS)


def asset_for_slug(slug: Optional[str]) -> Optional[str]:
    """The asset key a market slug belongs to, or None if we do not know it.

    `'btc-updown-5m-1787022000'` -> `'btc'`. Used to route an OPEN POSITION back
    to the per-asset strategy instance that opened it, which matters because
    every asset runs an instance carrying the same `strategy_name`: without this
    a BTC position could be handed to the SOL instance and judged against SOL's
    fair value.

    Returns None rather than guessing on anything not in the registry. A naive
    `slug.split('-')[0]` would happily return `'trump'` for an election market
    and the caller would then look up a strategy set that does not exist. None
    is a checkable answer; a plausible wrong string is not (convention 11).
    """
    if not slug:
        return None
    head = str(slug).split('-', 1)[0].lower()
    return head if head in ASSETS else None
