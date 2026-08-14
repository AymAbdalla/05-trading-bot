"""Base Strategy interface for the trading engine.

Every strategy (builtin or Quant-authored) must implement this interface.
The scanner calls scan() on each registered strategy for each new candle.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class Signal:
    """A trading signal emitted by a strategy."""

    def __init__(self, pair: str, pattern: str, direction: str, confidence: float,
                 features: dict, entry: Optional[float] = None, stop: Optional[float] = None,
                 target: Optional[float] = None, action: Optional[str] = None,
                 valid_for: int = 1):
        self.pair = pair
        self.pattern = pattern  # strategy name
        self.direction = direction  # 'bullish', 'bearish', 'trail', 'neutral'
        self.confidence = confidence
        self.features = features  # dict of indicator values at signal time
        self.entry = entry
        self.stop = stop
        self.target = target
        self.action = action  # 'close_long', 'block_entries', 'tighten_stop', None
        self.valid_for = valid_for  # how many candles the signal stays valid

    @property
    def strategy_id(self) -> str:
        return self.pattern

    def to_dict(self) -> dict:
        return {
            'pair': self.pair,
            'pattern': self.pattern,
            'direction': self.direction,
            'confidence': self.confidence,
            'features': self.features,
            'entry': self.entry,
            'stop': self.stop,
            'target': self.target,
            'action': self.action,
            'valid_for': self.valid_for,
        }


class Strategy(ABC):
    """Abstract base class for trading strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy name."""
        pass

    @property
    @abstractmethod
    def is_entry(self) -> bool:
        """True if this strategy produces entry signals, False for exit/filter."""
        pass

    @abstractmethod
    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        """Scan the latest candles and return a signal if found.

        Args:
            candles: dict with keys 'opens', 'highs', 'lows', 'closes', 'volumes', 'timestamps'

        Returns:
            Signal if a pattern is found, None otherwise.
        """
        pass
