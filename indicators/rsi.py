"""RSI (Relative Strength Index) indicator.

FACADE over the `ta` library (Aym ruling 2026-08-12: production indicator
math comes from a maintained library so future edits can't silently break
it). Signatures and padding conventions are unchanged from the original
hand-rolled version, which now lives in tests/reference_indicators.py as the
independent cross-check reference.

Padding convention: positions with insufficient history report 50.0
(neutral), exactly like the original.
"""
from typing import List


def rsi(closes: List[float], period: int = 14) -> List[float]:
    """Compute RSI over a series. Returns list same length as input.

    Wilder's smoothing (ta.momentum.RSIIndicator).
    Values < 30 = oversold, > 70 = overbought.
    """
    n = len(closes)
    if n < period + 1:
        return [50.0] * n

    import pandas as pd
    from ta.momentum import RSIIndicator

    series = RSIIndicator(pd.Series(closes, dtype=float), window=period).rsi()
    return series.fillna(50.0).tolist()


def latest_rsi(closes: List[float], period: int = 14) -> float:
    """Get the most recent RSI value."""
    result = rsi(closes, period)
    return result[-1] if result else 50.0
