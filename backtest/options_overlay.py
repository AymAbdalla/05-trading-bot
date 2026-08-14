"""Options overlay: run any equity strategy's signals as long CALL trades.

Closes the biggest coverage hole. `synthetic_options.py` has existed since
early in the project with ZERO callers - options were listed as in-scope for
the graveyard but had never been tested at all.

WHAT THIS DOES
Take a strategy's bullish signals on the underlying. Instead of buying 100
shares, buy N call contracts (OTM by `otm_pct`, `dte` days to expiry), priced
by Black-Scholes on trailing realized volatility. Exit when the UNDERLYING
hits the strategy's stop or target, or at expiry, whichever comes first, and
reprice the option at that moment.

WHY THE FEE MODEL IS THE POINT
Equity/crypto fees are a PERCENTAGE of notional, so position size cancels out
(see research/2026-08-13-position-size-and-costs.md). Options commissions at
US brokers are a FIXED DOLLAR AMOUNT PER CONTRACT (typically $0.50-$0.65,
sometimes with a per-order minimum, occasionally $0 plus regulatory fees).
Fixed cost + variable premium means the fee as a PERCENTAGE of the trade
swings enormously:

    $0.65 fee on a $0.50 premium  ($50 per contract)  = 1.30% each way
    $0.65 fee on a $10.00 premium ($1000 per contract) = 0.065% each way

That is a 20x difference in cost drag from strike/expiry selection alone.
Cheap far-OTM lottery tickets - exactly what the SPEC's v2+ WSB and
convexity ideas propose buying - are the WORST case for fee drag.

MODEL LIMITS (read before trusting any number)
- No IV surface: one trailing realized-vol number stands in for the smile.
  Real OTM options trade at a premium to this, so entry costs here are
  OPTIMISTIC.
- No bid/ask spread on the option itself. Real options spreads are wide,
  often 2-10% of premium on retail names. Modelled via `spread_pct`, default
  deliberately non-zero.
- No early assignment, no dividends, no pin risk.
- American vs European: priced European; for long calls without dividends
  early exercise is not optimal anyway, so this is minor.
This is a FEE-STRUCTURE and DIRECTIONAL sanity model, not an options pricing
engine. Real chain data (CBOE/ORATS/Polygon) is the SPEC's V4 answer.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backtest.synthetic_options import black_scholes_call
from backtest.vectorized_harness import Indicators, SCAN_WINDOW

logger = logging.getLogger(__name__)

TRADING_DAYS = 252


@dataclass
class OptionTrade:
    entry_idx: int
    exit_idx: int
    underlying_entry: float
    underlying_exit: float
    strike: float
    dte_days: int
    contracts: int
    premium_in: float       # per share
    premium_out: float      # per share
    commission: float       # total, both legs
    pnl_net: float
    exit_reason: str


@dataclass
class OptionResult:
    strategy_id: str
    ticker: str
    timeframe: str
    trades: List[OptionTrade] = field(default_factory=list)
    underlying_pnl: float = 0.0   # same signals traded as shares, for contrast

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl_net > 0)

    @property
    def win_rate(self) -> float:
        return self.wins / len(self.trades) if self.trades else 0.0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl_net for t in self.trades)

    @property
    def total_commission(self) -> float:
        return sum(t.commission for t in self.trades)

    @property
    def profit_factor(self) -> float:
        gp = sum(t.pnl_net for t in self.trades if t.pnl_net > 0)
        gl = abs(sum(t.pnl_net for t in self.trades if t.pnl_net <= 0))
        if gl == 0:
            return float('inf') if gp > 0 else 0.0
        return gp / gl

    @property
    def commission_pct_of_premium(self) -> float:
        """Commission as a share of total premium paid: the number that makes
        or breaks a cheap-option strategy."""
        paid = sum(t.premium_in * 100 * t.contracts for t in self.trades)
        return (self.total_commission / paid * 100) if paid else 0.0

    def to_report(self) -> dict:
        pf = self.profit_factor
        return {
            'strategy': self.strategy_id, 'ticker': self.ticker,
            'timeframe': self.timeframe, 'instrument': 'long_call',
            'trades': self.trade_count,
            'pf': None if pf == float('inf') else round(pf, 4),
            'win_rate': round(self.win_rate, 4),
            'total_pnl_usd': round(self.total_pnl, 2),
            'commission_usd': round(self.total_commission, 2),
            'commission_pct_of_premium': round(self.commission_pct_of_premium, 2),
            'underlying_pnl_usd': round(self.underlying_pnl, 2),
        }


def realized_vol(closes, idx: int, lookback: int, bars_per_year: float) -> float:
    """Annualized realized vol from log returns of the last `lookback` bars.

    bars_per_year must match the SERIES timeframe: hardcoding sqrt(252) (as
    synthetic_options.compute_historical_volatility does) is only correct for
    daily bars and badly wrong for 15m or weekly.
    """
    lo = max(1, idx - lookback + 1)
    rets = []
    for j in range(lo, idx + 1):
        if closes[j - 1] > 0 and closes[j] > 0:
            rets.append(math.log(closes[j] / closes[j - 1]))
    if len(rets) < 5:
        return 0.30
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return max(0.05, math.sqrt(var) * math.sqrt(bars_per_year))


BARS_PER_YEAR = {'5m': TRADING_DAYS * 78, '15m': TRADING_DAYS * 26,
                 '1h': TRADING_DAYS * 6.5, '4h': TRADING_DAYS * 1.625,
                 '1d': TRADING_DAYS, '1wk': 52}
BARS_PER_DAY = {'5m': 78, '15m': 26, '1h': 6.5, '4h': 1.625, '1d': 1, '1wk': 0.2}


def run_option_overlay(strategy, ind: Indicators, ticker: str, timeframe: str,
                       signals: Optional[List] = None,
                       otm_pct: float = 0.05, dte: int = 30,
                       commission_per_contract: float = 0.65,
                       order_minimum: float = 0.0,
                       spread_pct: float = 0.03,
                       risk_free: float = 0.04,
                       budget_usd: float = 100.0,
                       vol_lookback: int = 60) -> OptionResult:
    """Replay a strategy's bullish signals as long calls.

    budget_usd caps premium spend per trade (the SPEC's fixed-notional idea
    applied to options). Contracts = floor(budget / (premium * 100)); if that
    is zero the signal is skipped (cannot afford one contract).
    """
    bars_year = BARS_PER_YEAR.get(timeframe, TRADING_DAYS)
    bars_day = BARS_PER_DAY.get(timeframe, 1)
    closes = ind.closes
    n = ind.n
    min_idx = min(SCAN_WINDOW, 100)

    trades: List[OptionTrade] = []
    underlying_pnl = 0.0
    i = min_idx
    while i < n:
        sig = (signals[i] if signals is not None and i < len(signals)
               else strategy.scan_window(ind, i) if hasattr(strategy, 'scan_window') else None)
        if sig is None or sig.direction != 'bullish' or sig.entry is None or sig.stop is None:
            i += 1
            continue

        spot = float(closes[i])
        strike = round(spot * (1 + otm_pct), 2)
        vol = realized_vol(closes, i, vol_lookback, bars_year)
        t_years = dte / 365.0
        premium = black_scholes_call(spot, strike, t_years, risk_free, vol)
        if premium <= 0.01:
            i += 1
            continue
        premium_in = premium * (1 + spread_pct / 2)   # pay the ask side

        contracts = int(budget_usd // (premium_in * 100))
        if contracts < 1:
            i += 1          # cannot afford a single contract at this premium
            continue

        # Walk the underlying to the strategy's stop/target or option expiry.
        expiry_idx = min(n - 1, i + int(dte * bars_day))
        exit_idx, reason = expiry_idx, 'expiry'
        target = sig.target if sig.target else spot + (spot - float(sig.stop)) * 2
        for j in range(i + 1, expiry_idx + 1):
            if ind.lows[j] <= float(sig.stop):
                exit_idx, reason = j, 'underlying_stop'
                break
            if ind.highs[j] >= target:
                exit_idx, reason = j, 'underlying_target'
                break

        spot_out = float(closes[exit_idx])
        bars_held = exit_idx - i
        t_left = max(0.0, (dte - bars_held / bars_day) / 365.0)
        vol_out = realized_vol(closes, exit_idx, vol_lookback, bars_year)
        premium_raw = black_scholes_call(spot_out, strike, t_left, risk_free, vol_out)
        premium_out = premium_raw * (1 - spread_pct / 2)   # sell the bid side

        # FIXED per-contract commission, both legs, plus any order minimum.
        commission = 2 * max(order_minimum, commission_per_contract * contracts)
        pnl = (premium_out - premium_in) * 100 * contracts - commission

        trades.append(OptionTrade(
            entry_idx=i, exit_idx=exit_idx, underlying_entry=spot,
            underlying_exit=spot_out, strike=strike, dte_days=dte,
            contracts=contracts, premium_in=premium_in, premium_out=premium_out,
            commission=commission, pnl_net=pnl, exit_reason=reason))

        # Same signal traded as shares on the same budget, for contrast.
        shares = budget_usd / spot
        underlying_pnl += (spot_out - spot) * shares

        i = exit_idx + 1

    return OptionResult(strategy_id=strategy.name, ticker=ticker,
                        timeframe=timeframe, trades=trades,
                        underlying_pnl=underlying_pnl)
