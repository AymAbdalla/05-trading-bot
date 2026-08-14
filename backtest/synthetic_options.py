"""Synthetic Black-Scholes options pricing for backtesting.

Used to approximate option PnL without buying expensive historical option chain data.
Computes option prices from stock OHLCV + historical volatility.

Supports:
- Call and put pricing
- Delta, gamma, theta, vega
- Implied move calculation (straddle price / spot)
- Multiple strikes and expirations

This is an approximation. It misses:
- IV skew (real options have different IV at different strikes)
- IV term structure (real options have different IV at different expirations)
- Bid-ask spread (we model half-spread as slippage)
- Real market microstructure

But it's free and good enough for v0 graveyard testing.
"""
import math
from typing import Optional, Tuple
from datetime import datetime, timedelta


def norm_cdf(x: float) -> float:
    """Cumulative standard normal distribution."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


# Pricing core delegated to vollib (the maintained py_vollib rename) per the
# 2026-08-12 library policy: the hand-rolled version had a formula bug in its
# zero-volatility branch (it discounted the SPOT). Signatures kept stable so
# callers never see the swap. Degenerate branches stay explicit because
# vollib raises on t<=0 / sigma<=0.
from py_vollib.black_scholes import black_scholes as _bs_price


def black_scholes_call(spot: float, strike: float, time_to_expiry: float,
                       risk_free: float, volatility: float) -> float:
    """Black-Scholes call option price.

    Args:
        spot: current underlying price
        strike: option strike price
        time_to_expiry: time to expiration in years (e.g. 30/365 = 30 days)
        risk_free: risk-free rate (e.g. 0.05 for 5%)
        volatility: annualized volatility (e.g. 0.30 for 30%)

    Returns: call option price
    """
    if time_to_expiry <= 0:
        return max(0, spot - strike)
    if volatility <= 0:
        # Deterministic forward: value = discounted intrinsic on the forward
        return max(0, spot - strike * math.exp(-risk_free * time_to_expiry))
    return max(0, float(_bs_price('c', spot, strike, time_to_expiry, risk_free, volatility)))


def black_scholes_put(spot: float, strike: float, time_to_expiry: float,
                      risk_free: float, volatility: float) -> float:
    """Black-Scholes put option price."""
    if time_to_expiry <= 0:
        return max(0, strike - spot)
    if volatility <= 0:
        return max(0, strike * math.exp(-risk_free * time_to_expiry) - spot)
    return max(0, float(_bs_price('p', spot, strike, time_to_expiry, risk_free, volatility)))


def option_delta(spot: float, strike: float, time_to_expiry: float,
                 risk_free: float, volatility: float, is_call: bool = True) -> float:
    """Option delta (directional sensitivity)."""
    if time_to_expiry <= 0:
        return 1.0 if (is_call and spot > strike) else (0.0 if is_call else (-1.0 if spot < strike else 0.0))

    d1 = (math.log(spot / strike) + (risk_free + 0.5 * volatility ** 2) * time_to_expiry) / (volatility * math.sqrt(time_to_expiry))

    if is_call:
        return norm_cdf(d1)
    else:
        return norm_cdf(d1) - 1


def option_gamma(spot: float, strike: float, time_to_expiry: float,
                 risk_free: float, volatility: float) -> float:
    """Option gamma (rate of delta change)."""
    if time_to_expiry <= 0 or volatility <= 0:
        return 0.0

    d1 = (math.log(spot / strike) + (risk_free + 0.5 * volatility ** 2) * time_to_expiry) / (volatility * math.sqrt(time_to_expiry))
    return norm_pdf(d1) / (spot * volatility * math.sqrt(time_to_expiry))


def option_theta(spot: float, strike: float, time_to_expiry: float,
                 risk_free: float, volatility: float, is_call: bool = True) -> float:
    """Option theta (time decay per day)."""
    if time_to_expiry <= 0:
        return 0.0

    d1 = (math.log(spot / strike) + (risk_free + 0.5 * volatility ** 2) * time_to_expiry) / (volatility * math.sqrt(time_to_expiry))
    d2 = d1 - volatility * math.sqrt(time_to_expiry)

    theta = -(spot * norm_pdf(d1) * volatility) / (2 * math.sqrt(time_to_expiry))

    if is_call:
        theta -= risk_free * strike * math.exp(-risk_free * time_to_expiry) * norm_cdf(d2)
    else:
        theta += risk_free * strike * math.exp(-risk_free * time_to_expiry) * norm_cdf(-d2)

    return theta / 365  # per day


def option_vega(spot: float, strike: float, time_to_expiry: float,
                risk_free: float, volatility: float) -> float:
    """Option vega (IV sensitivity per 1% change in vol)."""
    if time_to_expiry <= 0:
        return 0.0

    d1 = (math.log(spot / strike) + (risk_free + 0.5 * volatility ** 2) * time_to_expiry) / (volatility * math.sqrt(time_to_expiry))
    return spot * norm_pdf(d1) * math.sqrt(time_to_expiry) / 100


def implied_move_straddle(spot: float, strike: float, time_to_expiry: float,
                          risk_free: float, volatility: float) -> float:
    """Approximate implied move as ATM straddle price / spot.

    The implied move is the market's expectation of how much the stock will move.
    Computed as: (ATM call + ATM put) / spot
    """
    call = black_scholes_call(spot, strike, time_to_expiry, risk_free, volatility)
    put = black_scholes_put(spot, strike, time_to_expiry, risk_free, volatility)
    return (call + put) / spot


def compute_historical_volatility(closes: list, period: int = 30) -> float:
    """Compute annualized historical volatility from close prices.

    Uses log returns and standard deviation.
    """
    if len(closes) < period + 1:
        return 0.0

    log_returns = []
    for i in range(len(closes) - period, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            log_returns.append(math.log(closes[i] / closes[i - 1]))

    if len(log_returns) < 2:
        return 0.0

    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_vol = math.sqrt(variance)

    # Annualize (252 trading days for equities, 365 for crypto)
    annual_vol = daily_vol * math.sqrt(252)

    return annual_vol


def simulate_option_trade(spot_at_entry: float, strike: float, days_to_expiry: int,
                          is_call: bool, is_long: bool, closes: list,
                          entry_idx: int, risk_free: float = 0.05,
                          spread_cost: float = 0.04) -> dict:
    """Simulate an option trade from entry to expiry or exit.

    Args:
        spot_at_entry: stock price at entry
        strike: option strike
        days_to_expiry: DTE at entry
        is_call: True for call, False for put
        is_long: True for long (buy), False for short (sell)
        closes: list of close prices (historical)
        entry_idx: index in closes where trade is entered
        risk_free: risk-free rate
        spread_cost: estimated round-trip spread as fraction of premium (default 4%)

    Returns dict with: entry_price, exit_price, pnl, pnl_pct, exit_reason, exit_idx, greeks_at_entry
    """
    # Compute historical volatility at entry (30-day)
    hv = compute_historical_volatility(closes[:entry_idx + 1], 30)
    if hv <= 0:
        hv = 0.30  # fallback 30%

    # Entry option price
    tte_entry = days_to_expiry / 365
    if is_call:
        entry_price = black_scholes_call(spot_at_entry, strike, tte_entry, risk_free, hv)
    else:
        entry_price = black_scholes_put(spot_at_entry, strike, tte_entry, risk_free, hv)

    # Add spread cost (half-spread on entry and exit)
    entry_price_with_slippage = entry_price * (1 + spread_cost / 2)

    # Greeks at entry
    delta = option_delta(spot_at_entry, strike, tte_entry, risk_free, hv, is_call)
    gamma = option_gamma(spot_at_entry, strike, tte_entry, risk_free, hv)
    theta = option_theta(spot_at_entry, strike, tte_entry, risk_free, hv, is_call)
    vega = option_vega(spot_at_entry, strike, tte_entry, risk_free, hv)

    # Walk forward to expiry or exit
    exit_price = 0.0
    exit_reason = 'expiry'
    exit_idx = min(entry_idx + days_to_expiry, len(closes) - 1)

    for i in range(entry_idx + 1, min(entry_idx + days_to_expiry + 1, len(closes))):
        spot = closes[i]
        days_remaining = days_to_expiry - (i - entry_idx)
        tte = max(days_remaining / 365, 0.0001)

        # Recompute HV at each step (rolling)
        hv_step = compute_historical_volatility(closes[:i + 1], 30)
        if hv_step <= 0:
            hv_step = hv  # use entry HV if can't compute

        if is_call:
            opt_price = black_scholes_call(spot, strike, tte, risk_free, hv_step)
        else:
            opt_price = black_scholes_put(spot, strike, tte, risk_free, hv_step)

        # Check intrinsic value at expiry
        if days_remaining <= 0:
            if is_call:
                opt_price = max(0, spot - strike)
            else:
                opt_price = max(0, strike - spot)
            exit_price = opt_price
            exit_reason = 'expiry'
            exit_idx = i
            break

    # If we exited at expiry, compute final value
    if exit_reason == 'expiry':
        spot_at_exit = closes[exit_idx]
        if is_call:
            intrinsic = max(0, spot_at_exit - strike)
        else:
            intrinsic = max(0, strike - spot_at_exit)
        exit_price = intrinsic

    # Apply spread on exit
    exit_price_with_slippage = exit_price * (1 - spread_cost / 2)

    # PnL
    if is_long:
        pnl = exit_price_with_slippage - entry_price_with_slippage
        pnl_pct = pnl / entry_price_with_slippage if entry_price_with_slippage > 0 else 0
    else:
        # Short option: collect premium at entry, pay at exit
        pnl = entry_price_with_slippage - exit_price_with_slippage
        pnl_pct = pnl / entry_price_with_slippage if entry_price_with_slippage > 0 else 0

    return {
        'entry_price': round(entry_price, 6),
        'entry_price_with_slippage': round(entry_price_with_slippage, 6),
        'exit_price': round(exit_price, 6),
        'exit_price_with_slippage': round(exit_price_with_slippage, 6),
        'pnl': round(pnl, 6),
        'pnl_pct': round(pnl_pct * 100, 2),  # as percentage
        'exit_reason': exit_reason,
        'exit_idx': exit_idx,
        'hv_at_entry': round(hv, 4),
        'delta': round(delta, 4),
        'gamma': round(gamma, 6),
        'theta': round(theta, 6),
        'vega': round(vega, 4),
        'implied_move': round(implied_move_straddle(spot_at_entry, strike, days_to_expiry / 365, risk_free, hv) * 100, 2),
    }
