"""Hand-rolled reference indicator implementations - TEST USE ONLY.

These are the original audited implementations (verified against textbook
values on 2026-08-12) that used to live in indicators/. Per Aym's ruling the
production indicators now delegate to the `ta` library; these copies remain
as the INDEPENDENT reference the cross-check tests compare production
against. Do not import from production code.
"""
import math
from typing import List, Tuple


def ref_rsi(closes: List[float], period: int = 14) -> List[float]:
    n = len(closes)
    if n < period + 1:
        return [50.0] * n
    changes = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsis = [50.0] * period
    rsis.append(100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss)))
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        rsis.append(100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss)))
    return rsis


def ref_atr(highs: List[float], lows: List[float], closes: List[float],
            period: int = 14) -> List[float]:
    n = len(highs)
    if n < period + 1:
        return [0.0] * n
    trs = []
    for i in range(n):
        if i == 0:
            trs.append(highs[0] - lows[0])
        else:
            pc = closes[i - 1]
            trs.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    atrs = [0.0] * period
    atrs.append(sum(trs[1:period + 1]) / period)
    for i in range(period + 1, n):
        atrs.append((atrs[-1] * (period - 1) + trs[i]) / period)
    return atrs


def ref_ema(values: List[float], period: int = 50) -> List[float]:
    n = len(values)
    if n == 0:
        return []
    if n < period:
        return [values[0]] * n
    mult = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    emas = [values[0]] * (period - 1)
    emas.append(seed)
    for i in range(period, n):
        emas.append((values[i] - emas[-1]) * mult + emas[-1])
    return emas


def ref_bollinger(closes: List[float], period: int = 20,
                  std_mult: float = 2.0) -> Tuple[float, float, float]:
    if len(closes) < period:
        sma = sum(closes) / len(closes) if closes else 0
        return sma, sma, sma
    sma = sum(closes[-period:]) / period
    var = sum((closes[-period + i] - sma) ** 2 for i in range(period)) / period
    std = math.sqrt(var)
    return sma - std_mult * std, sma, sma + std_mult * std
