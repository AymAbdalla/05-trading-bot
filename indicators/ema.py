"""EMA (Exponential Moving Average) indicator.

FACADE over the `ta` library (Aym ruling 2026-08-12); signatures and padding
conventions unchanged. Original hand-rolled math (SMA-seeded) lives in
tests/reference_indicators.py as the cross-check reference.

Convention notes:
- ta's EMA seeds with pandas ewm (first-value seed) instead of an SMA seed;
  the two converge within a fraction of a percent well before any consumer's
  warmup guard, and every consumer (scanner, harnesses) requires
  period+lookback closed candles before trusting a value.
- Padding: positions before the window report values[0], like the original.
"""
from typing import List


def ema(values: List[float], period: int = 50) -> List[float]:
    """Compute EMA over a series. Returns list same length as input."""
    n = len(values)
    if n == 0:
        return []
    if n < period:
        return [values[0]] * n

    import pandas as pd
    from ta.trend import EMAIndicator

    series = EMAIndicator(pd.Series(values, dtype=float), window=period).ema_indicator()
    return series.fillna(values[0]).tolist()


def latest_ema(values: List[float], period: int = 50) -> float:
    """Get the most recent EMA value."""
    result = ema(values, period)
    return result[-1] if result else 0.0


def ema_slope(values: List[float], period: int = 50, lookback: int = 10) -> float:
    """Slope of the EMA over the last `lookback` candles.

    Positive = uptrend, negative = downtrend. Returns 0 if not enough data.
    Guard requires period + lookback values: comparing against seed/padding
    values produced garbage slopes during warmup (2026-08-12 audit finding).
    """
    if len(values) < period + lookback:
        return 0.0
    emas = ema(values, period)
    current = emas[-1]
    past = emas[-(lookback + 1)]
    if past == 0:
        return 0.0
    return (current - past) / past
