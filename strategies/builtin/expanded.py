"""Expanded strategy library: ALL candlestick patterns + non-candlestick strategies.

35+ candlestick patterns (from indicators/patterns_all.py) wrapped as Strategy objects.
7 non-candlestick strategies from research:
  - BreakoutStrategy (MAX strategy, Quantpedia)
  - EmaPullback
  - BollingerReversion
  - RsiExtreme
  - FairValueGap (from user-provided PDF)
  - VolumeSurge
  - MomentumContinuation
"""
import math
from typing import Dict, List, Optional

from strategies.base import Strategy, Signal
from indicators.patterns_all import (
    BULLISH_PATTERNS, BEARISH_PATTERNS, NEUTRAL_PATTERNS,
)
from indicators.atr import latest_atr
from indicators.rsi import latest_rsi
from indicators.ema import latest_ema, ema_slope, ema
from indicators.volume import volume_ratio, volume_sma


def _compute_target(entry: float, stop: float, r: float = 2.0) -> float:
    risk = entry - stop
    if risk <= 0:
        return entry
    return entry + risk * r


# ============ CANDLESTICK STRATEGY WRAPPERS ============

class _PatternStrategy(Strategy):
    """Generic wrapper for candlestick patterns."""
    _pattern_func = None
    _is_entry = True
    _confidence = 0.5

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_entry(self) -> bool:
        return self._is_entry

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        result = self._pattern_func(
            candles['opens'], candles['highs'], candles['lows'], candles['closes']
        )
        if not result['found']:
            return None

        direction = result.get('direction', 'neutral')
        action = result.get('action')

        # Exit/filter patterns
        if direction == 'bearish' or action == 'close_long':
            return Signal(
                pair="", pattern=self._name, direction='bearish',
                confidence=0.7, features={'atr': result.get('atr', 0)},
                entry=None, stop=None, target=None, action='close_long',
            )
        if action == 'block_entries':
            return Signal(
                pair="", pattern=self._name, direction='neutral',
                confidence=0.3, features={},
                action='block_entries',
            )
        if direction == 'trail':
            return Signal(
                pair="", pattern=self._name, direction='trail',
                confidence=0.5, features={'atr': result.get('atr', 0)},
                stop=result.get('stop'), action='tighten_stop',
            )

        # Bullish entry
        entry = result.get('entry')
        stop = result.get('stop')
        if entry is None or stop is None:
            return None
        target = _compute_target(entry, stop)
        rsi_val = latest_rsi(candles['closes'], 14)
        vol_r = volume_ratio(candles['volumes'], 20)
        conf = self._confidence
        if rsi_val < 45:
            conf = min(0.7, conf + 0.1)
        return Signal(
            pair="", pattern=self._name, direction='bullish',
            confidence=conf,
            features={'rsi': rsi_val, 'volume_ratio': vol_r, 'atr': result.get('atr', 0)},
            entry=entry, stop=stop, target=target,
            valid_for=result.get('valid_for', 1),
        )


def _make_strategy_class(name, func, is_entry=True, confidence=0.5):
    """Dynamically create a strategy class for a pattern."""
    return type(name, (_PatternStrategy,), {
        '_name': name,
        '_pattern_func': staticmethod(func),
        '_is_entry': is_entry,
        '_confidence': confidence,
    })


# Build all candlestick strategy classes
_CANDLESTICK_STRATEGIES = []
for pname, pfunc in BULLISH_PATTERNS.items():
    cls = _make_strategy_class(pname, pfunc, is_entry=True, confidence=0.55)
    _CANDLESTICK_STRATEGIES.append(cls())

_EXIT_STRATEGIES = []
for pname, pfunc in BEARISH_PATTERNS.items():
    cls = _make_strategy_class(pname, pfunc, is_entry=False, confidence=0.7)
    _EXIT_STRATEGIES.append(cls())

_FILTER_STRATEGIES = []
for pname, pfunc in NEUTRAL_PATTERNS.items():
    cls = _make_strategy_class(pname, pfunc, is_entry=False, confidence=0.3)
    _FILTER_STRATEGIES.append(cls())


# ============ NON-CANDLESTICK STRATEGIES ============

class BreakoutStrategy(Strategy):
    """Buy when price breaks above N-period high. MAX strategy from Quantpedia."""
    name = "breakout_20"
    is_entry = True

    def __init__(self, lookback: int = 20):
        self.lookback = lookback
        self.name = f"breakout_{lookback}"

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        if len(closes) < self.lookback + 2:
            return None
        atr_val = latest_atr(highs, lows, closes, 14)
        if atr_val == 0:
            return None
        recent_high = max(highs[-self.lookback - 1:-1])
        current_close = closes[-1]
        if current_close > recent_high:
            entry = current_close
            stop = min(lows[-3:]) - 0.25 * atr_val
            target = _compute_target(entry, stop)
            vol_r = volume_ratio(volumes, 20)
            rsi_val = latest_rsi(closes, 14)
            conf = 0.6 if vol_r >= 1.5 else 0.5
            return Signal(
                pair="", pattern=self.name, direction='bullish',
                confidence=conf,
                features={'rsi': rsi_val, 'volume_ratio': vol_r, 'atr': atr_val,
                          'breakout_level': recent_high},
                entry=entry, stop=stop, target=target, valid_for=2,
            )
        return None


class EmaPullback(Strategy):
    """Buy when price pulls back to EMA(20) in an uptrend."""
    name = "ema_pullback"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        if len(closes) < 55:
            return None
        atr_val = latest_atr(highs, lows, closes, 14)
        if atr_val == 0:
            return None
        ema20 = latest_ema(closes, 20)
        ema50 = latest_ema(closes, 50)
        current_close = closes[-1]
        # Uptrend: close above EMA50
        if current_close <= ema50:
            return None
        # Pullback: close within 0.5 ATR of EMA20
        if abs(current_close - ema20) > 0.5 * atr_val:
            return None
        # Must have bounced (current close > previous close)
        if current_close <= closes[-2]:
            return None
        entry = current_close
        stop = ema20 - 0.5 * atr_val
        target = _compute_target(entry, stop)
        rsi_val = latest_rsi(closes, 14)
        vol_r = volume_ratio(volumes, 20)
        return Signal(
            pair="", pattern=self.name, direction='bullish',
            confidence=0.55,
            features={'rsi': rsi_val, 'volume_ratio': vol_r, 'atr': atr_val,
                      'ema20': round(ema20, 4), 'ema50': round(ema50, 4)},
            entry=entry, stop=stop, target=target, valid_for=2,
        )


class BollingerReversion(Strategy):
    """Buy when price touches lower Bollinger Band in uptrend."""
    name = "bollinger_reversion"
    is_entry = True

    def _bollinger(self, closes, period=20, std_mult=2.0):
        if len(closes) < period:
            return None, None, None
        sma = sum(closes[-period:]) / period
        variance = sum((closes[-period+i] - sma) ** 2 for i in range(period)) / period
        std = math.sqrt(variance)
        upper = sma + std_mult * std
        lower = sma - std_mult * std
        return lower, sma, upper

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        if len(closes) < 55:
            return None
        atr_val = latest_atr(highs, lows, closes, 14)
        if atr_val == 0:
            return None
        lower, mid, upper = self._bollinger(closes)
        if lower is None:
            return None
        ema50 = latest_ema(closes, 50)
        current_close = closes[-1]
        # Must be in uptrend (above EMA50)
        if current_close <= ema50:
            return None
        # Price must touch or dip below lower band
        if lows[-1] > lower:
            return None
        # Must close back above lower band
        if current_close < lower:
            return None
        entry = current_close
        stop = lower - 0.25 * atr_val
        # Target: middle band (mean reversion target)
        target = mid
        if target <= entry:
            target = _compute_target(entry, stop)
        rsi_val = latest_rsi(closes, 14)
        vol_r = volume_ratio(volumes, 20)
        return Signal(
            pair="", pattern=self.name, direction='bullish',
            confidence=0.55,
            features={'rsi': rsi_val, 'volume_ratio': vol_r, 'atr': atr_val,
                      'bb_lower': round(lower, 4), 'bb_mid': round(mid, 4)},
            entry=entry, stop=stop, target=target, valid_for=2,
        )


class RsiExtreme(Strategy):
    """Buy when RSI drops below 30 (oversold) in uptrend."""
    name = "rsi_extreme"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        if len(closes) < 55:
            return None
        atr_val = latest_atr(highs, lows, closes, 14)
        if atr_val == 0:
            return None
        rsi_val = latest_rsi(closes, 14)
        ema50 = latest_ema(closes, 50)
        # Must be in uptrend
        if closes[-1] <= ema50:
            return None
        # RSI must be oversold
        if rsi_val >= 35:
            return None
        entry = closes[-1]
        stop = min(lows[-5:]) - 0.25 * atr_val
        target = _compute_target(entry, stop)
        vol_r = volume_ratio(volumes, 20)
        return Signal(
            pair="", pattern=self.name, direction='bullish',
            confidence=0.6,
            features={'rsi': rsi_val, 'volume_ratio': vol_r, 'atr': atr_val},
            entry=entry, stop=stop, target=target, valid_for=3,
        )


class FairValueGap(Strategy):
    """Detect 3-candle FVG pattern. Entry on retest of gap."""
    name = "fair_value_gap"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        if len(closes) < 55:
            return None
        atr_val = latest_atr(highs, lows, closes, 14)
        if atr_val == 0:
            return None
        ema50 = latest_ema(closes, 50)
        if closes[-1] <= ema50:
            return None
        # Look for FVG in last ~10 candles: candle[i-2] high < candle[i] low
        # (3-candle gap; candle i-1 is the impulse bar). The old loop was
        # range(-3, -12) with no -1 step = empty range: this strategy could
        # never fire and every graveyard row for it was fiction.
        for i in range(-1, -min(12, len(closes) - 2), -1):
            gap_low = highs[i - 2]   # candle 1 high
            gap_high = lows[i]       # candle 3 low
            if gap_high > gap_low:
                gap_size = gap_high - gap_low
                if gap_size >= 0.1 * atr_val:
                    # Check if current price is retesting the gap
                    current_close = closes[-1]
                    if abs(current_close - (gap_low + gap_high) / 2) <= 0.5 * atr_val:
                        entry = current_close
                        stop = gap_low - 0.25 * atr_val
                        target = _compute_target(entry, stop)
                        rsi_val = latest_rsi(closes, 14)
                        vol_r = volume_ratio(volumes, 20)
                        return Signal(
                            pair="", pattern=self.name, direction='bullish',
                            confidence=0.55,
                            features={'rsi': rsi_val, 'volume_ratio': vol_r,
                                      'atr': atr_val, 'gap_low': gap_low,
                                      'gap_high': gap_high},
                            entry=entry, stop=stop, target=target, valid_for=3,
                        )
        return None


class VolumeSurge(Strategy):
    """Buy when volume > 3x average AND price closes in top 25% of range."""
    name = "volume_surge"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        if len(closes) < 55:
            return None
        atr_val = latest_atr(highs, lows, closes, 14)
        if atr_val == 0:
            return None
        vol_r = volume_ratio(volumes, 20)
        if vol_r < 3.0:
            return None
        o, h, l, c = candles['opens'][-1], highs[-1], lows[-1], closes[-1]
        rng = h - l
        if rng == 0:
            return None
        # Close in top 25%
        if c < l + rng * 0.75:
            return None
        # Green candle
        if c <= o:
            return None
        ema50 = latest_ema(closes, 50)
        if c <= ema50:
            return None
        entry = c
        stop = l - 0.25 * atr_val
        target = _compute_target(entry, stop)
        rsi_val = latest_rsi(closes, 14)
        return Signal(
            pair="", pattern=self.name, direction='bullish',
            confidence=0.6,
            features={'rsi': rsi_val, 'volume_ratio': vol_r, 'atr': atr_val},
            entry=entry, stop=stop, target=target, valid_for=1,
        )


class MomentumContinuation(Strategy):
    """Buy when price makes new 20-period high AND RSI > 55."""
    name = "momentum_continuation"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        if len(closes) < 55:
            return None
        atr_val = latest_atr(highs, lows, closes, 14)
        if atr_val == 0:
            return None
        recent_high = max(highs[-21:-1])
        if closes[-1] <= recent_high:
            return None
        rsi_val = latest_rsi(closes, 14)
        if rsi_val <= 55:
            return None
        entry = closes[-1]
        stop = min(lows[-3:]) - 0.25 * atr_val
        target = _compute_target(entry, stop)
        vol_r = volume_ratio(volumes, 20)
        return Signal(
            pair="", pattern=self.name, direction='bullish',
            confidence=0.6,
            features={'rsi': rsi_val, 'volume_ratio': vol_r, 'atr': atr_val,
                      'new_high': recent_high},
            entry=entry, stop=stop, target=target, valid_for=2,
        )


class MacdCrossover(Strategy):
    """Buy on MACD bullish crossover (MACD crosses above signal line)."""
    name = "macd_crossover"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        if len(closes) < 40:
            return None
        atr_val = latest_atr(highs, lows, closes, 14)
        if atr_val == 0:
            return None
        from indicators.macd_stoch import macd_crossover as detect_macd
        result = detect_macd(closes)
        if not result['found'] or result['direction'] != 'bullish':
            return None
        entry = closes[-1]
        stop = min(lows[-3:]) - 0.25 * atr_val
        target = _compute_target(entry, stop)
        rsi_val = latest_rsi(closes, 14)
        vol_r = volume_ratio(volumes, 20)
        return Signal(
            pair="", pattern=self.name, direction='bullish',
            confidence=0.55,
            features={'rsi': rsi_val, 'volume_ratio': vol_r, 'atr': atr_val,
                      'macd': round(result['macd'], 6),
                      'macd_signal': round(result['signal'], 6),
                      'histogram': round(result['histogram'], 6)},
            entry=entry, stop=stop, target=target, valid_for=2,
        )


class StochRsiOversold(Strategy):
    """Buy when Stochastic RSI drops below 0.2 (oversold) in uptrend."""
    name = "stoch_rsi_oversold"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        if len(closes) < 55:
            return None
        atr_val = latest_atr(highs, lows, closes, 14)
        if atr_val == 0:
            return None
        from indicators.macd_stoch import stochastic_rsi
        stoch_rsi = stochastic_rsi(closes)
        if stoch_rsi >= 0.2:
            return None
        ema50 = latest_ema(closes, 50)
        if closes[-1] <= ema50:
            return None
        entry = closes[-1]
        stop = min(lows[-5:]) - 0.25 * atr_val
        target = _compute_target(entry, stop)
        rsi_val = latest_rsi(closes, 14)
        vol_r = volume_ratio(volumes, 20)
        return Signal(
            pair="", pattern=self.name, direction='bullish',
            confidence=0.6,
            features={'rsi': rsi_val, 'volume_ratio': vol_r, 'atr': atr_val,
                      'stoch_rsi': round(stoch_rsi, 4)},
            entry=entry, stop=stop, target=target, valid_for=3,
        )


class GridStrategy(Strategy):
    """Grid trading: buy when price drops to grid level below current.

    In ranging markets, places virtual buy orders at fixed ATR intervals
    below current price. Each level has a fixed target at the next level up.
    Not a traditional signal-based strategy but adapts to the Strategy interface.
    """
    name = "grid_atr"
    is_entry = True

    def __init__(self, grid_spacing_atr: float = 1.0):
        self.grid_spacing = grid_spacing_atr
        self.name = f"grid_{grid_spacing_atr}atr"

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        if len(closes) < 30:
            return None
        atr_val = latest_atr(highs, lows, closes, 14)
        if atr_val == 0:
            return None
        # Check if price dropped by at least 1 grid spacing from recent high
        recent_high = max(highs[-20:])
        current_close = closes[-1]
        drop = recent_high - current_close
        if drop < self.grid_spacing * atr_val:
            return None
        # Grid entry: buy at current price, stop one grid below, target one grid above
        entry = current_close
        stop = entry - self.grid_spacing * atr_val
        target = entry + self.grid_spacing * atr_val
        rsi_val = latest_rsi(closes, 14)
        vol_r = volume_ratio(volumes, 20)
        return Signal(
            pair="", pattern=self.name, direction='bullish',
            confidence=0.45,
            features={'rsi': rsi_val, 'volume_ratio': vol_r, 'atr': atr_val,
                      'grid_high': recent_high, 'drop_atr': round(drop / atr_val, 2)},
            entry=entry, stop=stop, target=target, valid_for=1,
        )


class DCAStrategy(Strategy):
    """Dollar Cost Averaging: buy at fixed time intervals regardless of price.

    For backtesting: enters every N candles. Not signal-based.
    Useful as a benchmark: "is active trading better than DCA?"

    is_benchmark: a PASS here is NOT a discovery. DCA has no signal; in a
    rising market it beats buy-and-hold on timing luck alone. The graveyard
    labels benchmark PASSes so nobody reads "the market went up" as edge.
    """
    name = "dca_7"
    is_entry = True
    is_benchmark = True

    def __init__(self, interval_candles: int = 7):
        self.interval = interval_candles
        self.name = f"dca_{interval_candles}"

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        if len(closes) < 30:
            return None
        atr_val = latest_atr(highs, lows, closes, 14)
        if atr_val == 0:
            return None
        # Enter every N candles, keyed on the TIMESTAMP, not the window
        # length. len(closes) is pinned once a bounded scan window fills up
        # (e.g. the vectorized harness's 260-candle cap), which silently
        # stopped all DCA entries after the ramp-up period.
        timestamps = candles.get('timestamps')
        if not timestamps or len(timestamps) < 2:
            return None
        diffs = sorted(timestamps[j + 1] - timestamps[j]
                       for j in range(max(0, len(timestamps) - 20), len(timestamps) - 1))
        interval_ms = diffs[len(diffs) // 2]
        if interval_ms <= 0:
            return None
        if (timestamps[-1] // interval_ms) % self.interval != 0:
            return None
        entry = closes[-1]
        # DCA doesn't use stops traditionally, but for backtest comparison
        # we use a wide stop (3 ATR) to avoid immediate stop-out
        stop = entry - 3 * atr_val
        target = entry + 2 * atr_val
        return Signal(
            pair="", pattern=self.name, direction='bullish',
            confidence=0.3,
            features={'atr': atr_val, 'dca_interval': self.interval},
            entry=entry, stop=stop, target=target, valid_for=1,
        )


# ============ EXPORTS ============

NON_CANDLESTICK_STRATEGIES = [
    BreakoutStrategy(20),
    BreakoutStrategy(50),
    EmaPullback(),
    BollingerReversion(),
    RsiExtreme(),
    FairValueGap(),
    VolumeSurge(),
    MomentumContinuation(),
    MacdCrossover(),
    StochRsiOversold(),
    GridStrategy(1.0),
    GridStrategy(2.0),
    DCAStrategy(7),
    DCAStrategy(14),
]

ENTRY_STRATEGIES_EXPANDED = _CANDLESTICK_STRATEGIES + NON_CANDLESTICK_STRATEGIES
EXIT_STRATEGIES_EXPANDED = _EXIT_STRATEGIES
FILTER_STRATEGIES_EXPANDED = _FILTER_STRATEGIES
ALL_EXPANDED = ENTRY_STRATEGIES_EXPANDED + EXIT_STRATEGIES_EXPANDED + FILTER_STRATEGIES_EXPANDED
