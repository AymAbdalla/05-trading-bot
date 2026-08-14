"""ATR (Average True Range) indicator.

FACADE over the `ta` library (Aym ruling 2026-08-12); signatures and padding
conventions unchanged. Original hand-rolled math lives in
tests/reference_indicators.py as the cross-check reference.

Padding convention: positions with insufficient history report 0.0, exactly
like the original.
"""
from typing import List


def true_range(highs: List[float], lows: List[float], closes: List[float], i: int) -> float:
    """True range for candle i. (Kept hand-rolled: one max() of three terms;
    the vectorized harness imports this directly.)"""
    if i == 0:
        return highs[i] - lows[i]
    prev_close = closes[i - 1]
    return max(
        highs[i] - lows[i],
        abs(highs[i] - prev_close),
        abs(lows[i] - prev_close),
    )


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
    """Compute ATR over a series. Returns list same length as input
    (0.0 padding at start). Wilder smoothing (ta.volatility.AverageTrueRange)."""
    n = len(highs)
    if n < period + 1:
        return [0.0] * n

    import pandas as pd
    from ta.volatility import AverageTrueRange

    series = AverageTrueRange(
        pd.Series(highs, dtype=float), pd.Series(lows, dtype=float),
        pd.Series(closes, dtype=float), window=period,
    ).average_true_range()
    # ta emits 0.0 during its warmup already; normalize NaN to 0.0 anyway.
    return series.fillna(0.0).tolist()


def latest_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    """Get the most recent ATR value, or 0 if not enough data."""
    result = atr(highs, lows, closes, period)
    return result[-1] if result else 0.0
