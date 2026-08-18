"""Instrument specifications: how a thing is actually sized and paid for.

THE PROBLEM THIS SOLVES
The harness sizes every position as `qty = notional_cap / price` with a $100
notional. That is correct for spot crypto and fractional-share equities. It is
NONSENSE for contract instruments:

  - One MES contract is 5 x the S&P index. At index 6,800 that is $34,000 of
    EXPOSURE. You cannot buy $100 of it. You buy ONE contract or none.
  - One option contract is 100 shares of underlying. A $5.00 premium contract
    costs $500. You cannot buy $100 of it.

So all 79,642 futures rows in the graveyard describe trades that could never
be placed, and the options overlay had to skip most signals because a $100
budget could not afford a single contract.

THE THREE THINGS THAT CHANGE FOR CONTRACT INSTRUMENTS

1. QUANTITY IS AN INTEGER, MINIMUM ONE. No fractional contracts. Position size
   moves in large discrete jumps, and "too small to trade" is a real outcome.
2. CAPITAL AT RISK IS NOT NOTIONAL.
   - Futures: you post MARGIN (MES ~$1,200-2,400), not the $34,000 exposure.
     Leverage is roughly 15-25x, so the risk model is completely different.
   - Options: you pay PREMIUM, and that premium is your maximum loss.
3. RETURNS MUST BE REPORTED AGAINST CAPITAL AT RISK, not against exposure.
   A $170 gain on one MES is 0.5% of exposure but ~10% of margin. Reporting it
   against exposure understates both the return and the risk by ~20x.

WHAT THIS MEANS FOR BACKTESTING
Futures and options results are only meaningful as: PnL per contract, and
return on capital-at-risk. Percentage-of-notional comparisons against spot
strategies are apples to oranges and must not be pooled.

THE FIFTH CLASS: PREDICTION_MARKET (added 2026-08-17, D-267)
A Polymarket share is not a price instrument at all. It costs `price` USDC,
where the price IS the market's probability estimate on [0.00, 1.00], and it
redeems for exactly $1.00 or exactly $0.00. So:

  - Capital at risk is the PREMIUM PAID, like an option, and that premium is
    the entire maximum loss. There is no margin. Leverage is exactly 1.0.
  - Quantity is WHOLE SHARES with a venue minimum lot (Polymarket: 5). You
    cannot buy 3.7 shares, and "the cap cannot reach the minimum lot" is a
    real and common outcome, exactly as it is for a futures contract.
  - Upside is BOUNDED at (1.00 - price) per share. That has no analogue
    anywhere else in this file, and it is why entry price and win rate are
    meaningless read apart: 60% right at 55c makes money, 60% right at 65c
    loses it.

Nothing about the futures margin path or the options 100x multiplier applies.
PREDICTION_MARKET gets its own explicit branches rather than falling through
to the spot default - falling through is exactly how one asset class ends up
quietly scored against another one's payoff.
"""
from dataclasses import dataclass
from typing import Optional


# --- PREDICTION_MARKET (Polymarket) protocol constants ---------------------
# MEASURED, not assumed: these come back on every live /book payload and are
# already mirrored in engine/polymarket/types.py. Kept here so the backtest
# layer never has to import the engine.
BINARY_WIN_PAYOFF = 1.00       # a winning share redeems for exactly $1.00
BINARY_LOSS_PAYOFF = 0.00      # a losing share redeems for exactly $0.00
POLYMARKET_MIN_SHARES = 5.0    # venue minimum order size, in shares
POLYMARKET_PRICE_TICK = 0.01


def is_valid_binary_price(price) -> bool:
    """Is `price` a legal per-share premium for a tradable binary share?

    Strictly inside (0.00, 1.00). Both endpoints are excluded deliberately: a
    share quoted at exactly 0.00 or exactly 1.00 is a RESOLVED market, not a
    tradable quote. Admitting 1.00 lets a backtest "buy" a certainty, and
    admitting 0.00 makes the sizing divide-by-zero look like infinite size.
    """
    try:
        p = float(price)
    except (TypeError, ValueError):
        return False
    if p != p:                      # NaN
        return False
    return 0.0 < p < 1.0


@dataclass
class InstrumentSpec:
    """How one unit of this instrument is sized, margined, and priced."""
    symbol: str
    # CRYPTO | EQUITY | ETF | FUTURES | OPTIONS | PREDICTION_MARKET
    asset_class: str
    multiplier: float = 1.0   # exposure per point of price
    min_size: float = 0.0     # smallest tradable unit (0 = fractional ok)
    integer_only: bool = False
    initial_margin: Optional[float] = None   # futures: capital actually posted
    tick_size: float = 0.01

    @property
    def is_contract(self) -> bool:
        return self.integer_only

    @property
    def is_binary(self) -> bool:
        """Does one unit redeem at exactly $1.00 or exactly $0.00?"""
        return self.asset_class == 'PREDICTION_MARKET'

    def exposure(self, price: float, qty: float) -> float:
        """Notional exposure controlled."""
        return price * self.multiplier * qty

    def max_payout(self, qty: float) -> float:
        """Gross dollars a winning position redeems for.

        Only meaningful for a binary, where it is qty x $1.00 regardless of
        what was paid. Returns 0.0 for everything else rather than inventing a
        number: a spot position has no bounded payout.
        """
        if not self.is_binary:
            return 0.0
        return float(qty) * BINARY_WIN_PAYOFF

    def capital_at_risk(self, price: float, qty: float) -> float:
        """What the account actually commits. This is the denominator that
        makes futures and options returns comparable to anything."""
        if self.asset_class == 'FUTURES' and self.initial_margin:
            return self.initial_margin * qty
        if self.asset_class == 'OPTIONS':
            return price * self.multiplier * qty      # premium paid = max loss
        if self.asset_class == 'PREDICTION_MARKET':
            # Premium paid, and it is the WHOLE maximum loss - a losing share
            # redeems at exactly $0.00. Stated as its own branch rather than
            # left to fall through to `exposure()` (which returns the same
            # number today) so that a future change to spot exposure cannot
            # silently redefine a binary's risk.
            return price * self.multiplier * qty
        return self.exposure(price, qty)

    def size_for(self, capital: float, price: float) -> float:
        """How many units can `capital` actually buy? Returns 0 when the
        instrument is unaffordable - which is a real and common answer."""
        if price <= 0:
            return 0.0
        if self.asset_class == 'PREDICTION_MARKET' and not is_valid_binary_price(price):
            # A binary quoted at or above 1.00 is not a tradable share. Sizing
            # it would produce a position that cannot lose, which is the exact
            # shape of a fabricated result.
            return 0.0
        per_unit = (self.initial_margin if (self.asset_class == 'FUTURES'
                                            and self.initial_margin)
                    else price * self.multiplier)
        if per_unit <= 0:
            return 0.0
        n = capital / per_unit
        if self.integer_only:
            n = int(n)
            # min_size is the venue's minimum LOT. Futures and options leave it
            # at 0.0 (falsy), so this line is inert for them and the answer is
            # unchanged; Polymarket sets 5 shares and a cap that reaches only 4
            # buys nothing at all.
            if self.min_size and n < self.min_size:
                return 0.0
            return float(n) if n >= 1 else 0.0
        if self.min_size and n < self.min_size:
            return 0.0
        return n


# CME micro and standard equity-index futures. Margins are approximate and
# broker-dependent; they move with volatility and must be re-checked before
# any live use.
FUTURES_SPECS = {
    'MES': InstrumentSpec('MES', 'FUTURES', multiplier=5.0, integer_only=True,
                          initial_margin=1_800.0, tick_size=0.25),
    'MNQ': InstrumentSpec('MNQ', 'FUTURES', multiplier=2.0, integer_only=True,
                          initial_margin=2_600.0, tick_size=0.25),
    'M2K': InstrumentSpec('M2K', 'FUTURES', multiplier=5.0, integer_only=True,
                          initial_margin=900.0, tick_size=0.10),
    'MCL': InstrumentSpec('MCL', 'FUTURES', multiplier=100.0, integer_only=True,
                          initial_margin=1_400.0, tick_size=0.01),
    'MGC': InstrumentSpec('MGC', 'FUTURES', multiplier=10.0, integer_only=True,
                          initial_margin=1_900.0, tick_size=0.10),
    'ES': InstrumentSpec('ES', 'FUTURES', multiplier=50.0, integer_only=True,
                         initial_margin=18_000.0, tick_size=0.25),
    'NQ': InstrumentSpec('NQ', 'FUTURES', multiplier=20.0, integer_only=True,
                         initial_margin=26_000.0, tick_size=0.25),
    'CL': InstrumentSpec('CL', 'FUTURES', multiplier=1_000.0, integer_only=True,
                         initial_margin=14_000.0, tick_size=0.01),
    'GC': InstrumentSpec('GC', 'FUTURES', multiplier=100.0, integer_only=True,
                         initial_margin=19_000.0, tick_size=0.10),
}

# The graveyard's _F tickers are continuous CONTINUOUS-PRICE series for the
# standard contracts. Micro equivalents trade the same index at 1/10 size and
# are what a small account can actually reach.
STANDARD_TO_MICRO = {'ES': 'MES', 'NQ': 'MNQ', 'RTY': 'M2K',
                     'CL': 'MCL', 'GC': 'MGC', 'YM': 'MYM'}


# Graveyard `sector` tags map onto the four classes that are actually PAID FOR
# differently. This is the single definition; asset_class_analysis.py and both
# harnesses import it so a ticker can never be an ETF in the analysis and an
# equity in the cost model.
SECTOR_TO_CLASS = {
    'Crypto': 'CRYPTO', 'Crypto (Yahoo)': 'CRYPTO', 'Crypto-related': 'EQUITY',
    'Futures': 'FUTURES',
    'Sector ETFs': 'ETF', 'Index ETFs': 'ETF', 'Leveraged ETFs': 'ETF',
    'Bond ETFs': 'ETF', 'Commodity ETFs': 'ETF', 'Volatility': 'ETF',
    # Fifth regime (D-267). No existing graveyard row carries either tag, so
    # adding them cannot reclassify anything already recorded.
    'Prediction Markets': 'PREDICTION_MARKET',
    'Polymarket': 'PREDICTION_MARKET',
}


def resolve_asset_class(ticker: str, sector: Optional[str] = None) -> str:
    """Which cost regime does this row belong to?

    Sector tag first (it is curated), then the ticker's own shape as a
    fallback. Returning the wrong class here silently charges the wrong cost
    model, so the fallbacks are deliberately conservative: anything that is
    not obviously a coin or a contract is treated as an equity.

    NOTE on PREDICTION_MARKET: there is deliberately NO ticker-shape fallback.
    A Polymarket identifier is a hyphenated slug, and any rule that reads
    hyphens would also catch BRK-B. Prediction markets must be tagged
    explicitly by sector (or resolved by the caller), because a wrong guess
    here charges a binary's payoff to an equity's cost model.
    """
    if sector and sector in SECTOR_TO_CLASS:
        return SECTOR_TO_CLASS[sector]
    t = (ticker or '').upper()
    if '/' in t or t.endswith('_USD') or t.endswith('USDT'):
        return 'CRYPTO'
    if t.endswith('_F'):
        return 'FUTURES'
    return 'EQUITY'


def option_spec(symbol: str = 'OPT') -> InstrumentSpec:
    """One contract = 100 shares. Premium is quoted per share."""
    return InstrumentSpec(symbol, 'OPTIONS', multiplier=100.0,
                          integer_only=True, tick_size=0.01)


def prediction_market_spec(symbol: str = 'PM',
                           min_shares: float = POLYMARKET_MIN_SHARES
                           ) -> InstrumentSpec:
    """One share = one binary claim paying $1.00 or $0.00.

    multiplier is 1.0 on purpose: unlike an option there is no contract
    size, the quoted price IS the per-unit cost in dollars. integer_only with
    min_size=5 encodes Polymarket's whole-share minimum lot.
    """
    return InstrumentSpec(symbol, 'PREDICTION_MARKET', multiplier=1.0,
                          min_size=float(min_shares), integer_only=True,
                          tick_size=POLYMARKET_PRICE_TICK)


def spec_for(ticker: str, asset_class: str) -> InstrumentSpec:
    """Resolve a graveyard ticker to how it is really traded."""
    if asset_class == 'PREDICTION_MARKET':
        # Handled before the `_F` strip and the uppercase: a Polymarket
        # identifier is a case-sensitive slug or a hex condition id, and
        # mangling it would make the spec's symbol un-joinable back to the
        # market it came from.
        return prediction_market_spec(ticker or 'PM')
    base = ticker.replace('_F', '').upper()
    if asset_class == 'FUTURES':
        micro = STANDARD_TO_MICRO.get(base)
        if micro and micro in FUTURES_SPECS:
            return FUTURES_SPECS[micro]      # prefer the reachable size
        if base in FUTURES_SPECS:
            return FUTURES_SPECS[base]
        return InstrumentSpec(base, 'FUTURES', multiplier=5.0,
                              integer_only=True, initial_margin=2_000.0)
    if asset_class == 'OPTIONS':
        return option_spec(base)
    # Spot: fractional sizing, notional == exposure == capital at risk.
    return InstrumentSpec(base, asset_class, multiplier=1.0, integer_only=False)


def binary_pnl(entry_price: float, shares: float, won: bool) -> float:
    """Gross resolution PnL for a long binary position, before costs.

    win:  (1.00 - entry) x shares
    loss: -entry x shares

    This is the whole payoff. There is no path, no stop, and no exit price -
    a Polymarket position is held to settlement and redeems at exactly one of
    two values. Anything that computes a PnL from an intermediate mark is
    describing a different trade than the one this function scores.
    """
    e = float(entry_price)
    q = float(shares)
    if won:
        return (BINARY_WIN_PAYOFF - e) * q
    return (BINARY_LOSS_PAYOFF - e) * q


def breakeven_win_rate(entry_price: float, cost_per_share: float = 0.0) -> float:
    """Win rate this entry needs just to break even, on [0, 1].

    For a binary bought at p with c of cost per share, that is p + c. Printed
    next to a claimed win rate it is the fastest read on whether an entry band
    is viable at all: 60% at 55c clears, 60% at 65c does not.
    """
    return float(entry_price) + float(cost_per_share)


def affordability_report(capital: float, price: float, ticker: str,
                         asset_class: str) -> dict:
    """Can this account trade this instrument at all, and at what leverage?"""
    spec = spec_for(ticker, asset_class)
    qty = spec.size_for(capital, price)
    return {
        'ticker': ticker, 'asset_class': asset_class,
        'instrument': spec.symbol,
        'tradable': qty > 0,
        'units': qty,
        'exposure': spec.exposure(price, qty) if qty else 0.0,
        'capital_at_risk': spec.capital_at_risk(price, qty) if qty else 0.0,
        'leverage': (spec.exposure(price, qty) / capital) if qty else 0.0,
        'reason': None if qty > 0 else (
            f'one {spec.symbol} needs '
            f'${spec.initial_margin or price * spec.multiplier:,.0f}, '
            f'account has ${capital:,.0f}'),
    }
