"""Venue-accurate cost model. Four regimes, not four fee levels.

Source: references/broker-fee-reference-2026.md (verified 2026-08-13).

WHY THIS EXISTS
The harness charged ONE cost model - Binance.US crypto taker fees, 0.10% per
side - to all 1,390,451 backtest trades, including 905,124 EQUITY trades on a
venue that charges $0 commission. Equity costs were overstated roughly 4-10x,
and futures were modeled as a percentage when they are per-contract on a
$34k-minimum instrument.

The gross-edge-is-zero verdict survives (gross was ~0 in every class), but the
BAR for future work differs by class by an order of magnitude. A model that
cannot tell a stock from a coin cannot tell you what is worth testing.

THE FOUR REGIMES

  PERCENTAGE (crypto): cost scales with notional. Size-invariant in bps.
  SPREAD (US equities): commission is zero; you pay half-spread per leg plus
      statutory sell-side fees. Cost depends on the INSTRUMENT's liquidity,
      not the broker.
  FIXED PER CONTRACT (options): cost scales with CONTRACT COUNT, not notional.
      Inverts the size logic - bigger premium means fewer bps.
  STACKED FIXED (futures): broker + exchange + regulatory per side. Sub-basis-
      point per notional, but the minimum position is one contract.

Every cost model carries a VERSION. Results computed under different versions
must never be pooled (silent assertion).
"""
from dataclasses import dataclass, field
from typing import Optional

# Bump when any rate below changes. Stamped on every graveyard entry.
COST_MODEL_VERSION = '2026-08-13'


@dataclass
class CostBreakdown:
    """Every component named, so a surprising total can be traced."""
    commission: float = 0.0
    exchange_fee: float = 0.0
    regulatory: float = 0.0
    spread_cost: float = 0.0
    slippage: float = 0.0

    @property
    def total(self) -> float:
        return (self.commission + self.exchange_fee + self.regulatory
                + self.spread_cost + self.slippage)


class CostModel:
    """Per-leg cost for a trade. Call once for entry, once for exit."""

    VERSION = COST_MODEL_VERSION

    # --- CRYPTO: percentage regime (Binance.US, since 2026-04-22) ---
    # UNVERIFIED against a live account. Reference doc's checklist item 1.
    CRYPTO_TAKER = 0.0002        # 0.02% all pairs
    CRYPTO_TAKER_CORE = 0.0001   # 0.01% BTC/USD, ETH/USD
    CRYPTO_MAKER = 0.0           # 0.00%
    CRYPTO_CORE_PAIRS = {'BTC/USD', 'ETH/USD', 'BTC/USDT', 'ETH/USDT'}

    # --- EQUITIES: spread regime (Alpaca, commission-free) ---
    EQUITY_COMMISSION = 0.0
    SEC_FEE_PER_DOLLAR = 20.60 / 1_000_000   # sell side only, since 2026-04-04
    SEC_FEE_ZERO_BEFORE_MS = 1775260800000   # 2026-04-04; was $0 for 11 months
    TAF_PER_SHARE = 0.000195                 # sell side, min $0.01, cap $9.79
    TAF_MIN = 0.01
    TAF_CAP = 9.79
    # Half-spread per leg. Liquid large caps ~1-5bps round trip; this is the
    # per-leg default and SHOULD be replaced per instrument once the
    # fingerprinting work produces a spread table.
    EQUITY_HALF_SPREAD = 0.00015             # ~3bps round trip

    # --- OPTIONS: fixed per contract ---
    OPTION_COMMISSION_PER_CONTRACT = 0.65    # Schwab-class; 0.0 at Robinhood/Webull
    OPTION_PASSTHROUGH_PER_CONTRACT = 0.05   # ORF + OCC + exchange, approx
    OPTION_TAF_PER_CONTRACT = 0.00329        # sells only

    # --- FUTURES: stacked fixed, per side ---
    FUTURES_BROKER_MICRO = 0.39
    FUTURES_BROKER_STANDARD = 1.29
    FUTURES_EXCHANGE = {'MES': 0.62, 'MNQ': 0.62, 'ES': 1.38, 'NQ': 1.38}
    FUTURES_EXCHANGE_DEFAULT = 1.38
    FUTURES_REGULATORY = 0.02

    # Slippage is a MODELING assumption, not a venue fee. It applies to taker
    # legs in every regime and is replaced by adverse-selection measurement on
    # maker legs (which this model does not estimate - it must be measured).
    SLIPPAGE_TAKER = 0.0005

    # Per-regime slippage. These are ASSUMPTIONS, all UNMEASURED, and they are
    # separated because 5bps means something different in each market:
    #   crypto   5bps  - the SPEC's original figure, never measured live
    #   equity   the half-spread IS the slippage; there is no separate charge
    #   futures  one tick per side (MES tick 0.25 on ~6800 = 0.37bps). Charging
    #            5bps here would be 13 ticks of slip on the most liquid
    #            contract in the world - the flat model's worst single error.
    #   options  1% of premium per side, wide-spread placeholder. Options are
    #            NOT swept (no IV smile), so this number decides nothing yet.
    FUTURES_SLIP_TICKS = 1.0
    OPTION_SLIP_PCT = 0.01

    def __init__(self, slippage_taker: Optional[float] = None,
                 option_commission: Optional[float] = None,
                 futures_micro: bool = True):
        self.slippage_taker = (self.SLIPPAGE_TAKER if slippage_taker is None
                               else slippage_taker)
        self.option_commission = (self.OPTION_COMMISSION_PER_CONTRACT
                                  if option_commission is None else option_commission)
        self.futures_micro = futures_micro

    # ------------------------------------------------------------------

    def crypto_leg(self, notional: float, symbol: str = '',
                   maker: bool = False) -> CostBreakdown:
        if maker:
            return CostBreakdown(commission=notional * self.CRYPTO_MAKER)
        rate = (self.CRYPTO_TAKER_CORE if symbol in self.CRYPTO_CORE_PAIRS
                else self.CRYPTO_TAKER)
        return CostBreakdown(commission=notional * rate,
                             slippage=notional * self.slippage_taker)

    def equity_leg(self, notional: float, shares: float, is_sell: bool,
                   ts_ms: Optional[int] = None,
                   half_spread: Optional[float] = None) -> CostBreakdown:
        """Commission-free. Cost is spread plus statutory sell-side fees.

        The SEC fee was $0 from May 2025 until 2026-04-04, so a backtest
        spanning that window has a TIME-VARYING regulatory fee. Pass ts_ms to
        model it correctly.
        """
        hs = self.EQUITY_HALF_SPREAD if half_spread is None else half_spread
        cb = CostBreakdown(commission=self.EQUITY_COMMISSION,
                           spread_cost=notional * hs)
        if is_sell:
            if ts_ms is None or ts_ms >= self.SEC_FEE_ZERO_BEFORE_MS:
                cb.regulatory += notional * self.SEC_FEE_PER_DOLLAR
            taf = min(max(shares * self.TAF_PER_SHARE, self.TAF_MIN), self.TAF_CAP)
            cb.regulatory += taf
        return cb

    def option_leg(self, contracts: int, premium_per_contract: float,
                   is_sell: bool) -> CostBreakdown:
        """Cost scales with CONTRACTS, not notional. This is what inverts the
        size logic: the same $1.30 round trip is 130bps on a $100 contract and
        1.6bps on an $8,000 one."""
        cb = CostBreakdown(
            commission=contracts * self.option_commission,
            exchange_fee=contracts * self.OPTION_PASSTHROUGH_PER_CONTRACT,
        )
        if is_sell:
            cb.regulatory += contracts * self.OPTION_TAF_PER_CONTRACT
            cb.regulatory += (contracts * premium_per_contract
                              * self.SEC_FEE_PER_DOLLAR)
        return cb

    def futures_leg(self, contracts: int, symbol: str = 'MES') -> CostBreakdown:
        broker = (self.FUTURES_BROKER_MICRO if self.futures_micro
                  else self.FUTURES_BROKER_STANDARD)
        exch = self.FUTURES_EXCHANGE.get(symbol, self.FUTURES_EXCHANGE_DEFAULT)
        return CostBreakdown(
            commission=contracts * broker,
            exchange_fee=contracts * exch,
            regulatory=contracts * self.FUTURES_REGULATORY,
        )

    # ------------------------------------------------------------------

    def round_trip_bps(self, asset_class: str, notional: float,
                       symbol: str = '', **kw) -> float:
        """Round-trip cost in basis points of notional. The single number that
        decides whether a hypothesis is worth writing (rule 3)."""
        if notional <= 0:
            return float('inf')
        ac = asset_class.upper()
        if ac == 'CRYPTO':
            total = (self.crypto_leg(notional, symbol).total
                     + self.crypto_leg(notional, symbol).total)
        elif ac in ('EQUITY', 'ETF'):
            shares = kw.get('shares', notional / max(kw.get('price', 100.0), 1e-9))
            total = (self.equity_leg(notional, shares, False).total
                     + self.equity_leg(notional, shares, True,
                                       ts_ms=kw.get('ts_ms')).total)
            total += notional * self.slippage_taker * 2 if kw.get('taker', False) else 0.0
        elif ac == 'OPTIONS':
            contracts = kw.get('contracts', 1)
            prem = notional / max(contracts, 1)
            total = (self.option_leg(contracts, prem, False).total
                     + self.option_leg(contracts, prem, True).total)
        elif ac == 'FUTURES':
            contracts = kw.get('contracts', 1)
            total = (self.futures_leg(contracts, symbol).total
                     + self.futures_leg(contracts, symbol).total)
        else:
            raise ValueError(f'unknown asset class: {asset_class}')
        return total / notional * 10_000

    # ------------------------------------------------------------------

    def coster(self, ticker: str, asset_class: str, reference_price: float,
               notional_cap: float = 100.0, contracts: Optional[int] = None,
               fee_mult: float = 1.0, slip_mult: float = 1.0) -> 'TradeCoster':
        """Bind this model to one instrument, ready for the harness loop.

        contracts=None (the default) sizes contract instruments honestly from
        notional_cap via InstrumentSpec.size_for - see TradeCoster.__init__.
        """
        from backtest.instruments import spec_for
        return TradeCoster(self, spec_for(ticker, asset_class), asset_class,
                           ticker, reference_price, notional_cap=notional_cap,
                           contracts=contracts, fee_mult=fee_mult,
                           slip_mult=slip_mult)

    def describe(self) -> dict:
        return {
            'version': self.VERSION,
            'source': 'references/broker-fee-reference-2026.md',
            'crypto': {'venue': 'binanceus', 'maker': self.CRYPTO_MAKER,
                       'taker': self.CRYPTO_TAKER,
                       'taker_core': self.CRYPTO_TAKER_CORE,
                       'verified': False},
            'equity': {'venue': 'alpaca', 'commission': self.EQUITY_COMMISSION,
                       'half_spread_default': self.EQUITY_HALF_SPREAD,
                       'sec_fee_time_varying': True},
            'options': {'commission_per_contract': self.option_commission,
                        'regime': 'fixed_per_contract'},
            'futures': {'micro': self.futures_micro, 'regime': 'stacked_fixed',
                        'note': 'minimum position is ONE contract; MES ~$34k notional'},
            'slippage_taker': self.slippage_taker,
        }


# ===========================================================================
# HARNESS ADAPTER
# ===========================================================================

class TradeCoster:
    """One instrument's costs, in the two shapes a backtest loop needs.

    The harnesses model cost in two places and it matters which is which:

      SLIPPAGE / SPREAD moves the FILL PRICE. It must be a price adjustment,
      not a dollar subtraction, because it also moves the distance to the
      stop - a worse fill on a long is a tighter stop, and a model that
      subtracts it at the end silently reports a risk plan nobody traded.

      FEES are a dollar amount taken off the PnL. They do not move the fill.

    A flat `taker_fee` scalar could not express either honestly across four
    regimes: it charged futures 13 ticks of slip, charged commission-free
    equities a crypto commission, and made cost proportional to notional on
    instruments where it is per contract. This class replaces the scalar with
    something the loop can still use as two cheap values.

    Bound to a REFERENCE PRICE at construction so `slip_rate` stays a plain
    float across the run (futures slip is a tick count, so it is only
    price-independent once the price is fixed). Index futures move maybe 15%
    across a test slice; the resulting slip error is a fraction of a tick.
    """

    def __init__(self, model: CostModel, spec, asset_class: str, ticker: str,
                 reference_price: float, notional_cap: float = 100.0,
                 contracts: Optional[int] = None, fee_mult: float = 1.0,
                 slip_mult: float = 1.0):
        self.model = model
        self.spec = spec
        self.asset_class = (asset_class or 'EQUITY').upper()
        self.ticker = ticker
        self.reference_price = max(float(reference_price), 1e-9)
        self.notional_cap = float(notional_cap)
        # contracts=None (the default) means "size it honestly": how many
        # whole contracts notional_cap actually affords at this price, via
        # instruments.InstrumentSpec.size_for - which returns 0 when it does
        # not afford even one. Forcing a floor of 1 here (the pre-2026-08-13
        # behavior) is what made all 79,642 futures graveyard rows fictional:
        # a $100 account "traded" one MES contract needing $1,800 margin. An
        # explicit `contracts=N` (stress probes, diagnostics) still overrides
        # this and is never floored to 1 either - 0 is a real, honest answer.
        if contracts is None:
            contracts = (spec.size_for(self.notional_cap, self.reference_price)
                        if spec.integer_only else 1)
        self.contracts = max(int(contracts), 0)
        self.fee_mult = float(fee_mult)
        self.slip_mult = float(slip_mult)
        self.slip_rate = self._slip_rate()

    # -- identity -------------------------------------------------------

    @property
    def version(self) -> str:
        return self.model.VERSION

    @property
    def is_contract(self) -> bool:
        return self.asset_class in ('FUTURES', 'OPTIONS')

    @property
    def can_size(self) -> bool:
        """Can this instrument produce a non-zero position AT ALL?

        For contracts, `size()` returns `self.contracts`, fixed at
        construction from `notional_cap`. When that is 0 - a $100 cap against
        an $1,800 initial margin - EVERY signal on this series is rejected for
        lack of capital, at every price, on every bar. Nothing the strategy
        does can change it.

        That makes it a STRUCTURAL fact about the run rather than an outcome
        of it, which is why the harness reads it before the loop instead of
        inferring it from zero trades afterwards. A strategy whose every
        signal was rejected for lack of capital did not run and fail; it did
        not run (convention 11, Raven ruling R-002).
        """
        return (not self.is_contract) or self.contracts > 0

    @property
    def multiplier(self) -> float:
        """Dollars of PnL per unit of qty per point of price move."""
        return self.spec.multiplier

    @property
    def instrument(self) -> str:
        return self.spec.symbol

    def cache_key(self) -> tuple:
        """Everything that changes a number. Twin caches key on this."""
        return (self.version, self.asset_class, self.ticker,
                round(self.slip_rate, 12), self.notional_cap, self.contracts,
                self.fee_mult, round(self.reference_price, 6))

    # -- the two values the loop uses -----------------------------------

    def _slip_rate(self) -> float:
        ac = self.asset_class
        if ac == 'CRYPTO':
            return self.model.slippage_taker * self.slip_mult
        if ac in ('EQUITY', 'ETF'):
            # The half-spread IS the slippage for a commission-free equity.
            return self.model.EQUITY_HALF_SPREAD * self.slip_mult
        if ac == 'FUTURES':
            ticks = self.model.FUTURES_SLIP_TICKS * self.spec.tick_size
            return (ticks / self.reference_price) * self.slip_mult
        if ac == 'OPTIONS':
            return self.model.OPTION_SLIP_PCT * self.slip_mult
        raise ValueError(f'unknown asset class: {ac}')

    def leg_fee(self, price: float, qty: float, is_sell: bool,
                ts_ms: Optional[int] = None) -> float:
        """Dollar fee for ONE leg. Excludes anything already priced into the
        fill via `slip_rate` - double-charging the spread is the easiest way
        to rebuild the 30bps model by accident."""
        if qty <= 0:
            return 0.0
        ac = self.asset_class
        notional = price * qty * self.spec.multiplier
        if ac == 'CRYPTO':
            fee = self.model.crypto_leg(notional, self.ticker).commission
        elif ac in ('EQUITY', 'ETF'):
            cb = self.model.equity_leg(notional, qty, is_sell, ts_ms=ts_ms)
            fee = cb.commission + cb.regulatory     # spread_cost is in slip
        elif ac == 'FUTURES':
            fee = self.model.futures_leg(int(qty), self.spec.symbol).total
        elif ac == 'OPTIONS':
            fee = self.model.option_leg(int(qty), price, is_sell).total
        else:
            raise ValueError(f'unknown asset class: {ac}')
        return fee * self.fee_mult

    def round_trip_fee(self, entry_px: float, exit_px: float, qty: float,
                       exit_ts_ms: Optional[int] = None) -> float:
        return (self.leg_fee(entry_px, qty, False)
                + self.leg_fee(exit_px, qty, True, ts_ms=exit_ts_ms))

    # -- sizing ---------------------------------------------------------

    def size(self, price: float) -> float:
        """Position size at this price.

        Spot: the fixed notional cap, fractional. Contracts: a whole number,
        minimum one, because that is the only thing that exists. The $100 cap
        does NOT apply to contracts - one MES is ~$34k of exposure on ~$1.8k
        of margin, so pretending a $100 clip bought some fraction of it is
        what made all 79,642 futures rows fictional.
        """
        if price <= 0:
            return 0.0
        if self.is_contract:
            return float(self.contracts)
        return self.notional_cap / price

    def capital_at_risk(self, price: float, qty: float) -> float:
        """The denominator that makes a futures return comparable to a spot
        one: margin posted, or premium paid, or cash spent."""
        return self.spec.capital_at_risk(price, qty)

    def exposure(self, price: float, qty: float) -> float:
        return self.spec.exposure(price, qty)

    def describe(self) -> dict:
        px, qty = self.reference_price, self.size(self.reference_price)
        return {
            'cost_model_version': self.version,
            'asset_class': self.asset_class,
            'instrument': self.spec.symbol,
            'slip_rate_per_leg': round(self.slip_rate, 8),
            'qty': qty,
            'exposure_usd': round(self.exposure(px, qty), 2),
            'capital_at_risk_usd': round(self.capital_at_risk(px, qty), 2),
            'round_trip_fee_usd': round(self.round_trip_fee(px, px, qty), 4),
            'round_trip_bps_of_exposure': (
                round((self.round_trip_fee(px, px, qty)
                       + 2 * self.slip_rate * self.exposure(px, qty))
                      / max(self.exposure(px, qty), 1e-9) * 10_000, 3)),
        }


class FlatCoster:
    """The legacy flat model - one taker rate, one slippage - behind the same
    interface as TradeCoster, so the harness loop has exactly one code path.

    Still the RIGHT model in two places: the cross-harness referee (external
    engines are configured with one commission rate) and the validation
    suite's zero-cost / doubled-cost probes. Its version string encodes the
    rates, so a flat result can never silently pool with a modeled one - or
    with a flat one run at different rates.
    """

    multiplier = 1.0
    is_contract = False
    can_size = True          # spot sizing is fractional; never unaffordable
    asset_class = 'FLAT'
    instrument = 'FLAT'

    def __init__(self, fee_rate: float, slip: float, notional_cap: float = 100.0):
        self.fee_rate = float(fee_rate)
        self.slip_rate = float(slip)
        self.notional_cap = float(notional_cap)

    @property
    def version(self) -> str:
        return f'flat:taker={self.fee_rate:g},slip={self.slip_rate:g}'

    def cache_key(self) -> tuple:
        return ('flat', self.fee_rate, self.slip_rate, self.notional_cap)

    def leg_fee(self, price: float, qty: float, is_sell: bool,
                ts_ms: Optional[int] = None) -> float:
        return price * qty * self.fee_rate

    def round_trip_fee(self, entry_px: float, exit_px: float, qty: float,
                       exit_ts_ms: Optional[int] = None) -> float:
        return (entry_px + exit_px) * qty * self.fee_rate

    def size(self, price: float) -> float:
        return self.notional_cap / price if price > 0 else 0.0

    def capital_at_risk(self, price: float, qty: float) -> float:
        return price * qty

    def exposure(self, price: float, qty: float) -> float:
        return price * qty
