"""Additional indicators: MACD, Stochastic RSI, Bollinger Bands.

MACD and Bollinger are FACADES over the `ta` library (Aym ruling 2026-08-12);
signatures unchanged. The swap also removed the original MACD's O(n^2)
signal-line reconstruction. StochRSI stays hand-rolled (nonstandard,
documented variant built on our rsi module, which is itself ta-backed now).
"""
import math
from typing import List, Tuple


def _macd_series(closes: List[float], fast: int, slow: int, signal: int):
    import pandas as pd
    from ta.trend import MACD

    m = MACD(pd.Series(closes, dtype=float), window_slow=slow,
             window_fast=fast, window_sign=signal)
    return m.macd(), m.macd_signal(), m.macd_diff()


def macd(closes: List[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> Tuple[float, float, float]:
    """Compute MACD line, signal line, and histogram.

    Returns (macd_line, signal_line, histogram).
    """
    if len(closes) < slow + signal:
        return 0.0, 0.0, 0.0
    line, sig, hist = _macd_series(closes, fast, slow, signal)
    return float(line.iloc[-1]), float(sig.iloc[-1]), float(hist.iloc[-1])


def macd_crossover(closes: List[float], fast: int = 12, slow: int = 26,
                   signal: int = 9) -> dict:
    """Detect MACD bullish crossover (MACD crosses above signal).

    Returns dict with 'found', 'direction', 'macd', 'signal', 'histogram'.
    Compares the last two points of ONE series computation (the original
    recomputed the entire series twice per call).
    """
    if len(closes) < slow + signal + 2:
        return {'found': False, 'direction': 'neutral', 'macd': 0, 'signal': 0, 'histogram': 0}

    line, sig, hist = _macd_series(closes, fast, slow, signal)
    macd_now, signal_now = float(line.iloc[-1]), float(sig.iloc[-1])
    macd_prev, signal_prev = float(line.iloc[-2]), float(sig.iloc[-2])

    result = {'found': False, 'direction': 'neutral',
              'macd': macd_now, 'signal': signal_now, 'histogram': float(hist.iloc[-1])}

    if macd_prev <= signal_prev and macd_now > signal_now:
        result['found'] = True
        result['direction'] = 'bullish'
    elif macd_prev >= signal_prev and macd_now < signal_now:
        result['found'] = True
        result['direction'] = 'bearish'

    return result


def stochastic_rsi(closes: List[float], rsi_period: int = 14,
                   stoch_period: int = 14) -> float:
    """Compute Stochastic RSI.

    StochRSI = (RSI - min(RSI)) / (max(RSI) - min(RSI))
    Range: 0 to 1. < 0.2 = oversold, > 0.8 = overbought.
    """
    if len(closes) < rsi_period + stoch_period:
        return 0.5

    # Compute RSI history
    from indicators.rsi import rsi as compute_rsi
    rsi_values = compute_rsi(closes, rsi_period)

    if len(rsi_values) < stoch_period:
        return 0.5

    recent_rsi = rsi_values[-stoch_period:]
    rsi_min = min(recent_rsi)
    rsi_max = max(recent_rsi)

    if rsi_max == rsi_min:
        return 0.5

    current_rsi = rsi_values[-1]
    return (current_rsi - rsi_min) / (rsi_max - rsi_min)


def bollinger_bands(closes: List[float], period: int = 20,
                    std_mult: float = 2.0) -> Tuple[float, float, float]:
    """Compute Bollinger Bands via ta.volatility.BollingerBands.

    Returns (lower, middle, upper).
    """
    if len(closes) < period:
        sma = sum(closes) / len(closes) if closes else 0
        return sma, sma, sma

    import pandas as pd
    from ta.volatility import BollingerBands

    bb = BollingerBands(pd.Series(closes, dtype=float), window=period,
                        window_dev=std_mult)
    return (float(bb.bollinger_lband().iloc[-1]),
            float(bb.bollinger_mavg().iloc[-1]),
            float(bb.bollinger_hband().iloc[-1]))
