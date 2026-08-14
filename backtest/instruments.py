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
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class InstrumentSpec:
    """How one unit of this instrument is sized, margined, and priced."""
    symbol: str
    asset_class: str          # CRYPTO | EQUITY | ETF | FUTURES | OPTIONS
    multiplier: float = 1.0   # exposure per point of price
    min_size: float = 0.0     # smallest tradable unit (0 = fractional ok)
    integer_only: bool = False
    initial_margin: Optional[float] = None   # futures: capital actually posted
    tick_size: float = 0.01

    @property
    def is_contract(self) -> bool:
        return self.integer_only

    def exposure(self, price: float, qty: float) -> float:
        """Notional exposure controlled."""
        return price * self.multiplier * qty

    def capital_at_risk(self, price: float, qty: float) -> float:
        """What the account actually commits. This is the denominator that
        makes futures and options returns comparable to anything."""
        if self.asset_class == 'FUTURES' and self.initial_margin:
            return self.initial_margin * qty
        if self.asset_class == 'OPTIONS':
            return price * self.multiplier * qty      # premium paid = max loss
        return self.exposure(price, qty)

    def size_for(self, capital: float, price: float) -> float:
        """How many units can `capital` actually buy? Returns 0 when the
        instrument is unaffordable - which is a real and common answer."""
        if price <= 0:
            return 0.0
        per_unit = (self.initial_margin if (self.asset_class == 'FUTURES'
                                            and self.initial_margin)
                    else price * self.multiplier)
        if per_unit <= 0:
            return 0.0
        n = capital / per_unit
        if self.integer_only:
            n = int(n)
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
}


def resolve_asset_class(ticker: str, sector: Optional[str] = None) -> str:
    """Which cost regime does this row belong to?

    Sector tag first (it is curated), then the ticker's own shape as a
    fallback. Returning the wrong class here silently charges the wrong cost
    model, so the fallbacks are deliberately conservative: anything that is
    not obviously a coin or a contract is treated as an equity.
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


def spec_for(ticker: str, asset_class: str) -> InstrumentSpec:
    """Resolve a graveyard ticker to how it is really traded."""
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
