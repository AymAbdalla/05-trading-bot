"""Harness control strategies and validation assertions.

These validate that the backtest engine itself works correctly.
They are NOT told to any agent. They are silent guardrails.

Three control strategies:
- ORACLE_CONTROL: sees tomorrow's close via a control-only channel. Must produce
  extreme PF with zero fees, and must COLLAPSE when executed one bar late.
- BUYHOLD_CONTROL: buy first scanned bar, hold to last bar. Must equal the
  buy-and-hold benchmark to within fee + slippage cost.
- COIN_FLIP_CONTROL: seeded random entries. Same seed run with and without fees
  must show strictly lower PF with fees (proves fees are applied).

DESIGN CONTRACT with the harness:
- scan() only ever receives past data. A real oracle is impossible through the
  normal interface (by design). Controls that declare `is_control = True` and
  `wants_future_bars = k` receive an extra window key 'future_closes' (the next
  k closes) from VectorizedBacktestHarness. The harness NEVER provides that key
  to strategies without is_control, so there is no leak channel for real
  strategies. Everything else (fills, fees, slippage, exits) goes through the
  identical code path as real strategies. That is the whole point: a control
  that does not exercise the real pipeline validates nothing.
"""
import random
from typing import Dict, List, Optional
from strategies.base import Strategy, Signal


class OracleControl(Strategy):
    """POSITIVE CONTROL: looks ahead one bar, through the harness pipeline.

    Entry: bullish signal at bar t iff close[t+1] > close[t] (read from the
    control-only 'future_closes' channel). Run with exit config 'time_1c'
    (exit at close of t+1) and zero fees/slippage: PF must be extreme and
    win rate ~100%. Run again with execution_delay=1: the foresight is stale
    and PF must collapse toward random. If either fails, signal-to-fill
    wiring is broken and every other result in the run is meaningless.
    """
    name = "ORACLE_CONTROL"
    is_entry = True
    is_control = True
    wants_future_bars = 1

    def scan(self, candles: Dict[str, List]) -> Optional[Signal]:
        closes = candles['closes']
        future_closes = candles.get('future_closes')
        if not closes or not future_closes:
            return None

        current_close = closes[-1]
        next_close = future_closes[0]
        if next_close > current_close:
            return Signal(
                pair='CONTROL',
                pattern='ORACLE_CONTROL',
                direction='bullish',
                confidence=1.0,
                features={'lookahead': True},
                entry=current_close,
                # Stop far below so it (almost) never triggers; the oracle's
                # exit is the time_1c config, not the stop. Must be > 0 so
                # falsy-stop checks in harness code paths don't drop it.
                stop=current_close * 0.5,
                target=None,
                action='enter',
                valid_for=1,
            )
        return None


class BuyHoldControl(Strategy):
    """P&L ACCOUNTING CONTROL: buy on the first scanned bar, hold to the end.

    Run with exit config 'hold'. The resulting single trade's return must equal
    the harness's own buy-and-hold benchmark to within round-trip fee +
    slippage. If it doesn't, P&L accounting is broken.

    Instance is single-use: construct a fresh one per run (state resets in
    __init__, never shared across runs/tickers).
    """
    name = "BUYHOLD_CONTROL"
    is_entry = True
    is_control = True

    def __init__(self):
        self._entered = False

    def scan(self, candles: Dict[str, List]) -> Optional[Signal]:
        if self._entered:
            return None
        closes = candles['closes']
        if not closes:
            return None
        self._entered = True
        entry = closes[-1]  # actionable price NOW, not a stale historical bar
        return Signal(
            pair='CONTROL',
            pattern='BUYHOLD_CONTROL',
            direction='bullish',
            confidence=1.0,
            features={'control': True},
            entry=entry,
            stop=entry * 0.001,  # positive (non-falsy) but can never trigger
            target=None,
            action='enter',
            valid_for=1,
        )


class CoinFlipControl(Strategy):
    """FEE APPLICATION CONTROL: seeded random entries.

    Uses its own random.Random instance so runs are reproducible and never
    touch (or get clobbered by) the global RNG. Run the SAME seed twice, once
    with fee_override=0 and once with real fees: every per-seed PF must be
    strictly lower with fees. That is the fee-application assertion (A2).
    """
    name = "COIN_FLIP_CONTROL"
    is_entry = True
    is_control = True

    def __init__(self, seed: int = 0, entry_prob: float = 0.10):
        self._rng = random.Random(seed)
        self.seed = seed
        self.entry_prob = entry_prob

    def scan(self, candles: Dict[str, List]) -> Optional[Signal]:
        closes = candles['closes']
        if not closes:
            return None
        if self._rng.random() < self.entry_prob:
            entry = closes[-1]
            return Signal(
                pair='CONTROL',
                pattern='COIN_FLIP_CONTROL',
                direction='bullish',
                confidence=0.5,
                features={'random': True, 'seed': self.seed},
                entry=entry,
                stop=entry * 0.98,  # 2% stop
                target=None,
                action='enter',
                valid_for=1,
            )
        return None


# NOTE: OracleControl and CoinFlipControl are safe to share; BuyHoldControl is
# single-use. validate_harness.py constructs fresh instances per run anyway.
CONTROL_STRATEGIES = [OracleControl(), BuyHoldControl(), CoinFlipControl()]
