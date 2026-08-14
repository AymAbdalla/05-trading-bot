"""Support and resistance level detection."""
from typing import List, Tuple, Optional

from indicators.atr import latest_atr


def find_swing_lows(lows: List[float], lookback: int = 100, min_touches: int = 2) -> List[float]:
    """Find swing low levels in the last `lookback` candles.

    A swing low is a local minimum: lower than its neighbors.
    Returns a list of swing low prices.
    """
    n = len(lows)
    if n < 3:
        return []

    start = max(0, n - lookback)
    swing_lows = []

    for i in range(start + 1, n - 1):
        if lows[i] < lows[i - 1] and lows[i] <= lows[i + 1]:
            swing_lows.append(lows[i])

    return swing_lows


def find_support_levels(
    lows: List[float],
    highs: List[float],
    closes: List[float],
    lookback: int = 100,
    min_touches: int = 2,
    cluster_atr_mult: float = 0.5,
) -> List[float]:
    """Find support levels by clustering swing lows.

    Groups swing lows within cluster_atr_mult * ATR of each other.
    Only returns levels with >= min_touches.
    """
    swings = find_swing_lows(lows, lookback, min_touches)
    if not swings:
        return []

    atr_val = latest_atr(highs, lows, closes, 14)
    if atr_val == 0:
        return []

    cluster_threshold = atr_val * cluster_atr_mult

    # Cluster swing lows
    clusters = []
    for sw in sorted(swings):
        placed = False
        for cluster in clusters:
            if abs(sw - cluster[0]) <= cluster_threshold:
                cluster.append(sw)
                placed = True
                break
        if not placed:
            clusters.append([sw])

    # Only keep clusters with enough touches
    supports = []
    for cluster in clusters:
        if len(cluster) >= min_touches:
            supports.append(sum(cluster) / len(cluster))

    return supports


def nearest_support(price: float, supports: List[float], atr_val: float, max_distance_atr: float = 1.5) -> Optional[float]:
    """Find the nearest support level within max_distance_atr * ATR below the price.

    Returns None if no support is close enough.
    """
    if not supports or atr_val == 0:
        return None

    max_distance = atr_val * max_distance_atr
    below = [s for s in supports if s <= price and (price - s) <= max_distance]

    if not below:
        return None

    return max(below)  # closest support below price
