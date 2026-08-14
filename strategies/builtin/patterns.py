"""Builtin candlestick pattern strategies.

Each strategy wraps a pattern detection function from indicators/patterns.py
and converts it into a Signal object with entry/stop/target.
"""
import math
from typing import Dict, List, Optional

from strategies.base import Strategy, Signal
from indicators.patterns import (
    bullish_engulfing, hammer, morning_star, piercing_line,
    three_white_soldiers, bearish_exit, doji
)
from indicators.rsi import latest_rsi
from indicators.volume import volume_ratio
from indicators.ema import ema_slope, latest_ema


def _compute_target(entry: float, stop: float, r: float = 2.0) -> float:
    """Compute 2R target from entry and stop."""
    risk = entry - stop
    if risk <= 0:
        return entry  # invalid, no target
    return entry + risk * r


class BullishEngulfing(Strategy):
    name = "bullish_engulfing"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        result = bullish_engulfing(
            candles['opens'], candles['highs'], candles['lows'], candles['closes']
        )
        if result['found']:
            entry = result['entry']
            stop = result['stop']
            rsi_val = latest_rsi(candles['closes'], 14)
            vol_ratio = volume_ratio(candles['volumes'], 20)
            target = _compute_target(entry, stop)
            confidence = 0.6 if rsi_val < 45 else 0.5
            return Signal(
                pair="", pattern=self.name, direction="bullish", confidence=confidence,
                features={'rsi': rsi_val, 'volume_ratio': vol_ratio, 'atr': result['atr']},
                entry=entry, stop=stop, target=target,
            )
        return None


class Hammer(Strategy):
    name = "hammer"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        result = hammer(
            candles['opens'], candles['highs'], candles['lows'], candles['closes']
        )
        if result['found']:
            entry = result['entry']
            stop = result['stop']
            rsi_val = latest_rsi(candles['closes'], 14)
            vol_ratio = volume_ratio(candles['volumes'], 20)
            target = _compute_target(entry, stop)
            confidence = 0.6 if rsi_val < 45 else 0.5
            return Signal(
                pair="", pattern=self.name, direction="bullish", confidence=confidence,
                features={'rsi': rsi_val, 'volume_ratio': vol_ratio, 'atr': result['atr']},
                entry=entry, stop=stop, target=target,
                valid_for=result.get('valid_for', 2),
            )
        return None


class MorningStar(Strategy):
    name = "morning_star"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        result = morning_star(
            candles['opens'], candles['highs'], candles['lows'], candles['closes']
        )
        if result['found']:
            entry = result['entry']
            stop = result['stop']
            rsi_val = latest_rsi(closes) if (closes := candles['closes']) else 50.0
            vol_ratio = volume_ratio(candles['volumes'], 20)
            target = _compute_target(entry, stop)
            return Signal(
                pair="", pattern=self.name, direction="bullish", confidence=0.55,
                features={'rsi': rsi_val, 'volume_ratio': vol_ratio, 'atr': result['atr']},
                entry=entry, stop=stop, target=target,
            )
        return None


class PiercingLine(Strategy):
    name = "piercing_line"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        result = piercing_line(
            candles['opens'], candles['highs'], candles['lows'], candles['closes']
        )
        if result['found']:
            entry = result['entry']
            stop = result['stop']
            rsi_val = latest_rsi(candles['closes'], 14)
            vol_ratio = volume_ratio(candles['volumes'], 20)
            target = _compute_target(entry, stop)
            return Signal(
                pair="", pattern=self.name, direction="bullish", confidence=0.5,
                features={'rsi': rsi_val, 'volume_ratio': vol_ratio, 'atr': result['atr']},
                entry=entry, stop=stop, target=target,
            )
        return None


class ThreeWhiteSoldiers(Strategy):
    name = "three_white_soldiers"
    is_entry = False

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        result = three_white_soldiers(
            candles['opens'], candles['highs'], candles['lows'], candles['closes']
        )
        if result['found']:
            return Signal(
                pair="", pattern=self.name, direction="trail", confidence=0.5,
                features={'atr': result['atr']},
                stop=result['stop'],
                action="tighten_stop",
            )
        return None


class BearishExit(Strategy):
    name = "bearish_exit"
    is_entry = False

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        result = bearish_exit(
            candles['opens'], candles['highs'], candles['lows'], candles['closes']
        )
        if result['found']:
            return Signal(
                pair="", pattern=self.name, direction="bearish", confidence=0.7,
                features={'atr': result['atr']},
                action="close_long",
            )
        return None


class DojiFilter(Strategy):
    name = "doji_filter"
    is_entry = False

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        result = doji(
            candles['opens'], candles['highs'], candles['lows'], candles['closes']
        )
        if result['found']:
            return Signal(
                pair="", pattern=self.name, direction="neutral", confidence=0.3,
                features={},
                action="block_entries",
            )
        return None


ALL_BUILTIN = [
    BullishEngulfing(),
    Hammer(),
    MorningStar(),
    PiercingLine(),
    ThreeWhiteSoldiers(),
    BearishExit(),
    DojiFilter(),
]

ENTRY_STRATEGIES = [s for s in ALL_BUILTIN if s.is_entry]
EXIT_STRATEGIES = [s for s in ALL_BUILTIN if not s.is_entry]
