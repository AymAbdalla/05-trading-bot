"""Volume indicators."""
from typing import List


def volume_sma(volumes: List[float], period: int = 20) -> List[float]:
    """Simple moving average of volume."""
    n = len(volumes)
    if n < period:
        return [sum(volumes) / n if n > 0 else 0.0] * n

    smas = [0.0] * (period - 1)
    for i in range(period - 1, n):
        smas.append(sum(volumes[i - period + 1:i + 1]) / period)
    return smas


def volume_ratio(volumes: List[float], period: int = 20) -> float:
    """Ratio of current volume to its SMA. > 1.5 = high volume."""
    smas = volume_sma(volumes, period)
    if not smas or smas[-1] == 0:
        return 1.0
    return volumes[-1] / smas[-1]
