"""Vectorized backtest harness: numpy-precomputed indicators for ~100x speedup.

WHY THIS EXISTS
----------------
backtest/harness.py recomputes ATR/RSI/EMA/volume-ratio from scratch on every
candle by handing strategy.scan() a growing window `candles[:i+1]`. Each of
those indicator functions (indicators/atr.py, rsi.py, ema.py, volume.py) is
itself O(window) per call, so total cost across a test run is O(n^2) per
strategy. For n=7000 candles x 30 strategies that's 20+ minutes per ticker.

Fix: compute every indicator ONCE as a full numpy array (O(n) total), then
look values up by index. Strategy.scan() itself still does its own internal
recompute (we don't modify strategies/), but it's only ever handed a bounded
lookback window (SCAN_WINDOW candles) so that internal cost is O(1) per
candle instead of O(i). The harness's own regime/RSI/volume/support checks
and all exit-path simulation use the precomputed arrays directly (O(1)
lookups), which is where most of the old harness's per-candle cost lived.

Python 3.9 compatible: Optional[X], not X | None.
"""
import json
import logging
import random
import time as time_mod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from strategies.base import Signal
# HARNESS CONTRACT NOTE: this harness simulates ENTRY strategies against exit
# CONFIGS only. Exit-signal strategies (bearish patterns, doji filter, trail
# signals) and bearish/short entry signals are NOT simulated here - signals
# with direction != 'bullish' are dropped. Graveyard results from this
# pipeline say nothing about those. The event harness (backtest/harness.py)
# does simulate bearish exit signals.
from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
from indicators.atr import true_range
from indicators.support_resistance import find_support_levels

logger = logging.getLogger(__name__)


# ============ TIMEFRAME / EXIT CONFIG DEFINITIONS ============

TIMEFRAME_MINUTES = {
    '5m': 5,
    '15m': 15,
    '1h': 60,
    '4h': 240,
    '1d': 1440,
    '1wk': 1440 * 7,
}

# Standardized ATR-based stop used by every exit config that doesn't derive
# its stop from the strategy's own Signal.stop. Keeping this fixed lets us
# compare all 30 strategies under the same risk framework.
STOP_ATR_MULT = 0.25
TRAIL_ATR_MULT = 0.5

# type: 'fixed' | 'trailing' | 'time' | 'trailing_time' | 'hold'
EXIT_CONFIGS = {
    'fixed_1r':        {'type': 'fixed',         'r_multiple': 1.0},
    'fixed_2r':        {'type': 'fixed',         'r_multiple': 2.0},
    'fixed_3r':        {'type': 'fixed',         'r_multiple': 3.0},
    'trailing_atr':     {'type': 'trailing',      'target_r': None},
    'trailing_atr_2r':  {'type': 'trailing',      'target_r': 2.0},
    'time_4c':         {'type': 'time',          'candles': 4},
    'time_8c':         {'type': 'time',          'candles': 8},
    'time_16c':        {'type': 'time',          'candles': 16},
    'trail_time_combo': {'type': 'trailing_time', 'candles': 8},
    # SPEC 5.1 #6: bearish patterns (shooting star, bearish engulfing, ...)
    # as EXIT triggers. These 14 exit strategies existed from the start and
    # were never once simulated - the harness dropped every non-bullish
    # signal, so the graveyard had no exit-signal evidence at all.
    'signal_exit': {'type': 'signal', 'target_r': None},
    'signal_exit_2r': {'type': 'signal', 'target_r': 2.0},
    # 'no_exit' removed: hold-to-end is buy-and-hold, already the benchmark
}

# Control-only exit configs, used by validate_harness.py. Not part of the
# graveyard sweep grid (run_sweep callers pass explicit exit_configs lists
# or default to EXIT_CONFIGS - keep these OUT of that dict).
CONTROL_EXIT_CONFIGS = {
    'time_1c': {'type': 'time', 'candles': 1},   # oracle: exit at next close
    'hold':    {'type': 'hold'},                  # buy-hold accounting control
}

# How many trailing candles strategy.scan() is handed. Must comfortably
# cover every builtin strategy's internal lookback (EMA-50, support
# lookback=100, etc.) while staying constant so scan() cost doesn't grow
# with the position in the series.
SCAN_WINDOW = 260

# A strategy may declare min_bars > SCAN_WINDOW (e.g. C2 WeekendVacuumReversion
# needs 840) and still be tested: scan_all_bars widens ITS OWN window to
# max(SCAN_WINDOW, min_bars), leaving every other strategy's O(1)-per-bar cost
# untouched. This cap is only a defensive ceiling against a hypothetical
# strategy asking for an unreasonable amount of history (which would still be
# reported NOT_TESTED, honestly, rather than silently eating minutes per run).
MAX_STRATEGY_WINDOW = 2000

# How often (in candles) support/resistance levels are recomputed. Support
# detection clusters swing lows over a 100-candle lookback -- doing that on
# every single candle is wasteful since the levels barely move candle to
# candle. Recomputing every N candles and caching between is a >10x
# reduction in that specific cost with negligible accuracy loss.
SUPPORT_RECOMPUTE_EVERY = 20

# Bump whenever a change alters PASS/FAIL semantics or fill/cost behavior.
# Stamped on every graveyard entry so results from different gate eras can
# never be silently pooled (the incremental resume key has no code
# fingerprint, so this stamp is the detector).
# 1: original;  2: percentile twin gate + honored entry/stop + gap fills
GATE_VERSION = 2


def _median(values: List[float]) -> float:
    """TRUE median including inf entries (re-audit NEW-3): a median over only
    the finite subset understates a baseline that is honestly infinite."""
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    lo, hi = ordered[mid - 1], ordered[mid]
    if hi == float('inf'):
        return float('inf') if lo == float('inf') else lo
    return (lo + hi) / 2.0


# ============ RESULT DATACLASSES ============

@dataclass
class VTrade:
    entry_idx: int
    exit_idx: int
    entry_ts: int
    exit_ts: int
    entry_px: float
    exit_px: float
    stop_px: float
    target_px: Optional[float]
    qty: float
    pnl_gross: float
    fee_cost: float
    pnl_net: float
    r_multiple: float
    exit_reason: str  # 'stop', 'target', 'trail_stop', 'time', 'signal_exit', 'end_of_data'
    features: dict = field(default_factory=dict)
    # What the account actually committed: notional for spot, MARGIN for
    # futures, premium for options. The denominator of every return% below.
    # For spot it equals entry_px * qty, so flat-mode numbers are unchanged.
    capital_at_risk: float = 0.0


@dataclass
class VResult:
    strategy_id: str
    ticker: str
    timeframe: str
    exit_config: str
    trades: List[VTrade] = field(default_factory=list)
    buy_hold_return: float = 0.0       # full-window price return %, for reporting
    buy_hold_pnl_usd: float = 0.0      # $ PnL of buy-and-hold on ONE notional_cap, fees included
    random_twin_pf: float = 0.0          # median of the twin distribution
    twin_pfs: List[float] = field(default_factory=list)  # full distribution
    # Which cost regime produced these numbers. 'flat:...' = legacy single
    # rate; a date = backtest/cost_model.py version. Results carrying
    # different stamps must never be pooled (assertions.py enforces it).
    cost_model_version: str = 'flat:unstamped'
    asset_class: str = 'FLAT'
    instrument: str = ''
    # Signals that reached sizing and were rejected for lack of capital
    # (`coster.size()` returned 0). Never silently dropped: when this is the
    # whole reason a series produced no trades, the row is NOT_TESTED rather
    # than FAIL (R-002, convention 20).
    zero_size_rejects: int = 0

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl_net > 0)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return self.wins / len(self.trades)

    # NOTE on naming: profit_factor (and the two sums below) are computed on
    # pnl_net, i.e. AFTER fees - the "PF after fees" the SPEC gates want.
    # gross_pf below is the pre-fee PF (F2: needed for fee-application
    # assertions and any future inversion analysis).
    @property
    def gross_profit(self) -> float:
        return sum(t.pnl_net for t in self.trades if t.pnl_net > 0)

    @property
    def gross_loss(self) -> float:
        return abs(sum(t.pnl_net for t in self.trades if t.pnl_net <= 0))

    @property
    def profit_factor(self) -> float:
        if self.gross_loss == 0:
            return float('inf') if self.gross_profit > 0 else 0.0
        return self.gross_profit / self.gross_loss

    @property
    def gross_pf(self) -> float:
        """Profit factor BEFORE fees (from pnl_gross). Must be >= net PF."""
        gp = sum(t.pnl_gross for t in self.trades if t.pnl_gross > 0)
        gl = abs(sum(t.pnl_gross for t in self.trades if t.pnl_gross <= 0))
        if gl == 0:
            return float('inf') if gp > 0 else 0.0
        return gp / gl

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl_net for t in self.trades)

    @property
    def strategy_return_pct(self) -> float:
        """Average per-trade return on CAPITAL AT RISK. For spot that is the
        old return-on-notional exactly; for futures it is return on margin,
        the only denominator that isn't off by ~20x. NOT comparable to a
        full-period price return - use beats_buy_hold() for that."""
        total_car = sum(t.capital_at_risk or t.entry_px * t.qty
                        for t in self.trades)
        if total_car == 0:
            return 0.0
        return (self.total_pnl / total_car) * 100

    @property
    def avg_capital_at_risk(self) -> float:
        if not self.trades:
            return 0.0
        return (sum(t.capital_at_risk or t.entry_px * t.qty
                    for t in self.trades) / len(self.trades))

    def beats_buy_hold(self) -> bool:
        """Dollar comparison on the same fixed notional: total strategy PnL vs
        PnL of parking one notional_cap in the asset for the whole test window
        (fees included on both). Same capital, same window, same costs."""
        return self.total_pnl > self.buy_hold_pnl_usd

    @property
    def twin_percentile(self) -> Optional[float]:
        """Where this strategy's PF sits in the distribution of matched
        random twins: the fraction of twins it beats, 0.0 to 1.0.

        This replaces the old "PF beat one random draw by 0.15" test, which
        was a single noise sample compared against an arbitrary, scale-
        dependent threshold. '97th percentile of 100 matched random twins'
        is a real statement; 'beat one draw by 0.16' is not.
        """
        if not self.twin_pfs or not self.trades:
            return None
        pf = self.profit_factor
        if pf == float('inf'):
            # Beats every finite twin; ties with any infinite ones.
            beaten = sum(1 for t in self.twin_pfs if t != float('inf'))
        else:
            beaten = sum(1 for t in self.twin_pfs if pf > t)
        return beaten / len(self.twin_pfs)

    def beats_random_twin(self, min_percentile: float = 0.90) -> bool:
        """Strategy must sit in the top decile of matched random twins."""
        pct = self.twin_percentile
        if pct is None:
            return False
        return pct >= min_percentile

    def beats_random_twin_legacy(self, min_diff: float = 0.15) -> bool:
        pf = self.profit_factor
        twin = self.random_twin_pf
        if pf == float('inf'):
            return True
        if twin == float('inf'):
            return False
        return pf >= twin + min_diff

    def to_report(self) -> dict:
        pf = self.profit_factor
        twin = self.random_twin_pf
        return {
            'strategy': self.strategy_id,
            'ticker': self.ticker,
            'timeframe': self.timeframe,
            'exit_config': self.exit_config,
            'trades': self.trade_count,
            'pf': None if pf == float('inf') else round(pf, 4),
            'gross_pf': None if self.gross_pf == float('inf') else round(self.gross_pf, 4),
            'win_rate': round(self.win_rate, 4),
            'return_pct': round(self.strategy_return_pct, 2),
            'total_pnl_usd': round(self.total_pnl, 2),
            'buy_hold_pct': round(self.buy_hold_return, 2),
            'buy_hold_pnl_usd': round(self.buy_hold_pnl_usd, 2),
            'random_twin_pf': None if twin == float('inf') else round(twin, 4),
            'twin_percentile': (None if self.twin_percentile is None
                                else round(self.twin_percentile, 3)),
            'twin_sample_size': len(self.twin_pfs),
            'gate_version': GATE_VERSION,
            'cost_model_version': self.cost_model_version,
            'asset_class': self.asset_class,
            'instrument': self.instrument,
            'avg_capital_at_risk_usd': round(self.avg_capital_at_risk, 2),
            'beats_buy_hold': self.beats_buy_hold(),
            'beats_twin': self.beats_random_twin(),
            'zero_size_rejects': self.zero_size_rejects,
        }


# ============ NUMPY INDICATOR PRECOMPUTATION ============

@dataclass
class Indicators:
    """Full-length numpy arrays, computed once per (ticker, timeframe)."""
    n: int
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray
    timestamps: np.ndarray
    atr14: np.ndarray
    rsi14: np.ndarray
    ema50: np.ndarray
    vol_sma20: np.ndarray
    vol_ratio20: np.ndarray
    dollar_volume: np.ndarray
    regime_uptrend: np.ndarray  # bool: close > ema50 and ema50 rising
    support_by_bucket: List[List[float]] = field(default_factory=list)  # cached every SUPPORT_RECOMPUTE_EVERY candles
    # Per-series caches live ON the series object. Keying a harness-level dict
    # by id(ind) is a REAL bug: CPython reuses memory addresses after garbage
    # collection, so a freed 101-bar series and a new 312-bar series can share
    # an id and the cache returns an array of the wrong length.
    exit_bars_cache: Optional[np.ndarray] = None
    twin_cache: dict = field(default_factory=dict)

    def support_levels_at(self, i: int) -> List[float]:
        """Levels available AT bar i with no lookahead. Bucket k is computed
        from data through the END of bucket k (index 20k+19), so a bar inside
        bucket k may only see bucket k-1 (data through 20k-1 <= i). Serving
        bucket k itself would leak up to 19 future bars."""
        bucket = i // SUPPORT_RECOMPUTE_EVERY - 1
        if bucket >= len(self.support_by_bucket):
            bucket = len(self.support_by_bucket) - 1
        if bucket < 0:
            return []
        return self.support_by_bucket[bucket]


# Precomputed indicator arrays are ta-backed (Aym ruling 2026-08-12), with
# the same padding conventions the original numpy versions used (ATR 0.0,
# RSI 50.0, EMA first-value) so downstream warmup guards behave identically.

def _wilder_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(highs)
    if n < period + 1:
        return np.zeros(n)
    import pandas as pd
    from ta.volatility import AverageTrueRange
    series = AverageTrueRange(pd.Series(highs), pd.Series(lows),
                              pd.Series(closes), window=period).average_true_range()
    return series.fillna(0.0).to_numpy()


def _wilder_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    if n < period + 1:
        return np.full(n, 50.0)
    import pandas as pd
    from ta.momentum import RSIIndicator
    series = RSIIndicator(pd.Series(closes), window=period).rsi()
    return series.fillna(50.0).to_numpy()


def _ema(values: np.ndarray, period: int = 50) -> np.ndarray:
    n = len(values)
    if n == 0:
        return np.array([])
    if n < period:
        return np.full(n, values[0])
    import pandas as pd
    from ta.trend import EMAIndicator
    series = EMAIndicator(pd.Series(values), window=period).ema_indicator()
    return series.fillna(float(values[0])).to_numpy()


def _rolling_mean(values: np.ndarray, period: int) -> np.ndarray:
    """O(n) rolling mean via cumulative sum. Matches indicators/volume.py semantics
    (positions before `period` fall back to the running average of what's available)."""
    n = len(values)
    if n == 0:
        return np.array([])
    if n < period:
        avg = values.mean()
        return np.full(n, avg)

    csum = np.cumsum(values)
    out = np.empty(n)
    out[:period - 1] = 0.0  # unused - matches original's zero-padding region
    windowed = (csum[period - 1:] - np.concatenate(([0.0], csum[:n - period]))) / period
    out[period - 1:] = windowed
    return out


def precompute_indicators(candles: List[dict], support_lookback: int = 100,
                           support_min_touches: int = 2,
                           regime_ema_period: int = 50,
                           regime_lookback: int = 10) -> Indicators:
    """Compute every indicator array ONCE, in O(n) total, using numpy."""
    n = len(candles)
    opens = np.array([c['open'] for c in candles], dtype=float)
    highs = np.array([c['high'] for c in candles], dtype=float)
    lows = np.array([c['low'] for c in candles], dtype=float)
    closes = np.array([c['close'] for c in candles], dtype=float)
    volumes = np.array([c['volume'] for c in candles], dtype=float)
    timestamps = np.array([c['ts'] for c in candles], dtype=np.int64)

    atr14 = _wilder_atr(highs, lows, closes, 14)
    rsi14 = _wilder_rsi(closes, 14)
    ema50 = _ema(closes, regime_ema_period)
    vol_sma20 = _rolling_mean(volumes, 20)
    with np.errstate(divide='ignore', invalid='ignore'):
        vol_ratio20 = np.where(vol_sma20 > 0, volumes / np.where(vol_sma20 == 0, 1, vol_sma20), 1.0)
    dollar_volume = closes * volumes

    # Regime: EMA rising over `regime_lookback` candles AND price above it.
    regime_uptrend = np.zeros(n, dtype=bool)
    if n > regime_lookback:
        shifted = np.empty(n)
        shifted[:regime_lookback] = ema50[:regime_lookback]
        shifted[regime_lookback:] = ema50[:-regime_lookback]
        with np.errstate(divide='ignore', invalid='ignore'):
            slope = np.where(shifted != 0, (ema50 - shifted) / shifted, 0.0)
        regime_uptrend = (slope > 0) & (closes > ema50)

    # Support levels: recompute periodically, not every candle.
    support_by_bucket: List[List[float]] = []
    lows_list = candles and [c['low'] for c in candles]
    highs_list = [c['high'] for c in candles]
    closes_list = [c['close'] for c in candles]
    for start in range(0, n, SUPPORT_RECOMPUTE_EVERY):
        end = min(start + SUPPORT_RECOMPUTE_EVERY, n)
        idx = end - 1  # compute using data available at end of this bucket
        window_lo = max(0, idx - support_lookback)
        levels = find_support_levels(
            lows_list[window_lo:idx + 1],
            highs_list[window_lo:idx + 1],
            closes_list[window_lo:idx + 1],
            lookback=support_lookback,
            min_touches=support_min_touches,
        )
        support_by_bucket.append(levels)

    return Indicators(
        n=n, opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes,
        timestamps=timestamps, atr14=atr14, rsi14=rsi14, ema50=ema50,
        vol_sma20=vol_sma20, vol_ratio20=vol_ratio20, dollar_volume=dollar_volume,
        regime_uptrend=regime_uptrend, support_by_bucket=support_by_bucket,
    )


# ============ EXIT SIMULATION (per-trade, numpy-assisted first-touch) ============

def _simulate_exit(ind: Indicators, entry_idx: int, entry_px: float,
                    initial_stop: float, exit_cfg: dict,
                    max_idx: int,
                    exit_bars: Optional[np.ndarray] = None) -> Tuple[int, float, str]:
    """Walk forward from entry_idx+1 to find the exit. Returns (exit_idx, exit_px, reason).

    Uses numpy boolean-mask + argmax for the pure fixed stop/target case
    (no python loop needed), falls back to a bounded python loop only for
    trailing-stop configs where the stop level itself changes candle to
    candle (inherently path-dependent, can't be vectorized away).
    """
    cfg_type = exit_cfg['type']
    start = entry_idx + 1
    if start > max_idx:
        return max_idx, ind.closes[max_idx], 'end_of_data'

    # GAP HANDLING (applies to every stop/target touch below):
    # - Stop (long): if the bar OPENS at/below the stop, the real fill is the
    #   open, not the stop price. Fill = min(stop, open). Filling at the stop
    #   on a gap-through systematically understates losses.
    # - Target: if the bar opens above the target, a resting limit sell fills
    #   at the open (better). Fill = max(target, open).

    if cfg_type == 'hold':
        return max_idx, ind.closes[max_idx], 'end_of_data'

    if cfg_type == 'signal':
        # Stop always live; optional R target; exit when a bearish PATTERN
        # fires (SPEC 5.1 #6 "close any open long at next candle open").
        # Signal exits fill at the NEXT bar's open, not this bar's close:
        # the pattern is only known once its candle has closed.
        target_r = exit_cfg.get('target_r')
        risk = entry_px - initial_stop
        target_px = entry_px + risk * target_r if (target_r is not None and risk > 0) else None
        for j in range(start, max_idx + 1):
            if ind.lows[j] <= initial_stop:
                return j, min(initial_stop, float(ind.opens[j])), 'stop'
            if target_px is not None and ind.highs[j] >= target_px:
                return j, max(target_px, float(ind.opens[j])), 'target'
            if exit_bars is not None and exit_bars[j]:
                k = min(j + 1, max_idx)
                return k, float(ind.opens[k]), 'signal_exit'
        return max_idx, ind.closes[max_idx], 'end_of_data'

    if cfg_type == 'time':
        n_candles = exit_cfg['candles']
        exit_idx = min(entry_idx + n_candles, max_idx)
        # Time exit MUST still respect the stop loss.
        lows = ind.lows[start:exit_idx + 1]
        stop_hit = lows <= initial_stop
        if stop_hit.any():
            rel_idx = int(np.argmax(stop_hit))
            j = start + rel_idx
            return j, min(initial_stop, float(ind.opens[j])), 'stop'
        return exit_idx, ind.closes[exit_idx], 'time'

    if cfg_type == 'fixed':
        r = exit_cfg['r_multiple']
        risk = entry_px - initial_stop
        target_px = entry_px + risk * r if risk > 0 else None
        highs = ind.highs[start:max_idx + 1]
        lows = ind.lows[start:max_idx + 1]
        stop_hit = lows <= initial_stop
        target_hit = highs >= target_px if target_px is not None else np.zeros_like(stop_hit)
        both_hit = stop_hit | target_hit
        if not both_hit.any():
            return max_idx, ind.closes[max_idx], 'end_of_data'
        rel_idx = int(np.argmax(both_hit))
        exit_idx = start + rel_idx
        # Conservative: if both stop and target trigger on the same candle, assume stop first.
        if stop_hit[rel_idx]:
            return exit_idx, min(initial_stop, float(ind.opens[exit_idx])), 'stop'
        return exit_idx, max(target_px, float(ind.opens[exit_idx])), 'target'

    if cfg_type == 'trailing':
        target_r = exit_cfg.get('target_r')
        risk = entry_px - initial_stop
        target_px = entry_px + risk * target_r if (target_r is not None and risk > 0) else None
        stop = initial_stop
        highest_close = entry_px
        for j in range(start, max_idx + 1):
            if ind.lows[j] <= stop:
                return j, min(stop, float(ind.opens[j])), 'trail_stop'
            if target_px is not None and ind.highs[j] >= target_px:
                return j, max(target_px, float(ind.opens[j])), 'target'
            if ind.closes[j] > highest_close:
                highest_close = ind.closes[j]
                new_stop = ind.closes[j] - TRAIL_ATR_MULT * ind.atr14[j]
                if new_stop > stop:
                    stop = new_stop
        return max_idx, ind.closes[max_idx], 'end_of_data'

    if cfg_type == 'trailing_time':
        n_candles = exit_cfg['candles']
        time_limit_idx = min(entry_idx + n_candles, max_idx)
        stop = initial_stop
        highest_close = entry_px
        for j in range(start, time_limit_idx + 1):
            if ind.lows[j] <= stop:
                return j, min(stop, float(ind.opens[j])), 'trail_stop'
            if ind.closes[j] > highest_close:
                highest_close = ind.closes[j]
                new_stop = ind.closes[j] - TRAIL_ATR_MULT * ind.atr14[j]
                if new_stop > stop:
                    stop = new_stop
            if j == time_limit_idx:
                return j, ind.closes[j], 'time'
        return time_limit_idx, ind.closes[time_limit_idx], 'time'

    raise ValueError(f"Unknown exit config type: {cfg_type}")


# ============ MAIN HARNESS ============

class VectorizedBacktestHarness:
    """Runs strategies against precomputed indicator arrays. ~100x faster than
    backtest/harness.py for the same strategy/data because indicators are
    computed once (O(n)) instead of on every candle (O(n^2))."""

    def __init__(self, config: Optional[dict] = None):
        config = config or {}
        self.notional_cap = config.get('risk', {}).get('notional_cap_usd', 100)
        self.taker_fee = config.get('exchange', {}).get('fees', {}).get('taker', 0.001)
        self.slippage = config.get('exchange', {}).get('slippage', {}).get('market', 0.0005)

        # OPT-IN venue-accurate costs (config: use_cost_model: true). Off, the
        # harness is bit-identical to the flat legacy model, which the
        # cross-harness referee and the zero-cost validation probes require.
        # Either way every report is stamped with the cost model version, so
        # the two eras can never be pooled by accident.
        self.use_cost_model = bool(config.get('use_cost_model', False))
        from backtest.cost_model import CostModel
        self.cost_model = CostModel()

        conf = config.get('strategy', {}).get('confirmation', {})
        self.rsi_max_entry = conf.get('rsi_max_entry', 60)
        self.volume_min_ratio = conf.get('volume_min_ratio', 1.5)
        self.require_regime_uptrend = conf.get('require_regime_uptrend', True)
        self.apply_confirmation_stack = conf.get('apply_confirmation_stack', True)

    def _coster(self, ticker: str, ind: Indicators,
                sector: Optional[str] = None,
                fee_override: Optional[float] = None,
                slippage_override: Optional[float] = None):
        """Resolve the cost regime for this run.

        Overrides (validation probes, stress tests) always mean the FLAT
        model - a probe that says "fees = 0" must get exactly that in every
        regime. Otherwise: modeled costs when opted in, legacy flat when not.
        """
        from backtest.cost_model import FlatCoster
        from backtest.instruments import resolve_asset_class
        if (fee_override is not None or slippage_override is not None
                or not self.use_cost_model):
            return FlatCoster(
                self.taker_fee if fee_override is None else fee_override,
                self.slippage if slippage_override is None else slippage_override,
                notional_cap=self.notional_cap)
        asset_class = resolve_asset_class(ticker, sector)
        # Reference price fixes the futures tick->rate conversion; the median
        # close is stable against opening gaps and end-of-series drift.
        ref_px = float(np.median(ind.closes)) if ind.n else 1.0
        return self.cost_model.coster(ticker, asset_class, ref_px,
                                      notional_cap=self.notional_cap)

    def _make_window(self, ind: Indicators, i: int, future_bars: int = 0,
                      window_size: int = SCAN_WINDOW) -> Dict:
        lo = max(0, i - window_size + 1)
        window = {
            'opens': ind.opens[lo:i + 1].tolist(),
            'highs': ind.highs[lo:i + 1].tolist(),
            'lows': ind.lows[lo:i + 1].tolist(),
            'closes': ind.closes[lo:i + 1].tolist(),
            'volumes': ind.volumes[lo:i + 1].tolist(),
            'timestamps': ind.timestamps[lo:i + 1].tolist(),
            # Precomputed values for strategies that choose to use them
            # instead of recomputing from the raw arrays above.
            'atr_14': float(ind.atr14[i]),
            'rsi_14': float(ind.rsi14[i]),
            'ema_50': float(ind.ema50[i]),
            'volume_ratio_20': float(ind.vol_ratio20[i]),
            'support_levels': ind.support_levels_at(i),
        }
        # CONTROL-ONLY channel: future closes for harness-validation oracles.
        # Only populated when run_strategy sees strategy.is_control == True.
        # Real strategies never receive this key.
        if future_bars > 0:
            window['future_closes'] = ind.closes[i + 1:i + 1 + future_bars].tolist()
        return window

    def _passes_liquidity_filter(self, ind: Indicators, i: int,
                                  liquidity_filter: Optional[dict]) -> bool:
        if not liquidity_filter:
            return True
        min_vol = liquidity_filter.get('min_candle_volume')
        if min_vol is not None and ind.volumes[i] < min_vol:
            return False
        min_dollar = liquidity_filter.get('min_dollar_volume')
        if min_dollar is not None and ind.dollar_volume[i] < min_dollar:
            return False
        return True

    @staticmethod
    def _resolve_entry(ind: Indicators, i: int, signal, slip: float
                       ) -> Optional[Tuple[int, float]]:
        """Turn a Signal at bar i into an actual fill, per SPEC 5.1 order types.

        - Market (signal.entry ~= close[i]): fill at bar i close + slippage.
          "Buy at close of signal candle" - patterns 1/3/4 and most strategies.
        - Buy-stop (signal.entry > close[i], e.g. hammer high): order rests for
          signal.valid_for candles. First bar whose high touches the level
          fills at max(level, that bar's open) + slippage (gap-aware: opening
          above the level fills at the open, not the level). Never touched ->
          no trade (the signal expires; that is the whole point of a stop
          order - unconfirmed setups don't become trades).
        - Buy-limit (signal.entry < close[i]): order rests for valid_for
          candles. First bar whose low touches the level fills at
          min(level, open) with NO slippage (resting limit orders don't pay
          taker slippage). Never touched -> no trade.

        Returns (fill_idx, fill_px) or None if the order never fills.
        """
        level = float(signal.entry)
        close_i = float(ind.closes[i])
        n = ind.n
        # "close enough to close" = market order at the close
        if abs(level - close_i) <= close_i * 0.001:
            return i, close_i * (1 + slip)

        valid_for = max(1, int(getattr(signal, 'valid_for', 1) or 1))
        last_j = min(i + valid_for, n - 1)
        if level > close_i:  # buy-stop above market
            for j in range(i + 1, last_j + 1):
                if ind.highs[j] >= level:
                    fill = max(level, float(ind.opens[j]))
                    return j, fill * (1 + slip)
            return None
        else:  # buy-limit below market
            for j in range(i + 1, last_j + 1):
                if ind.lows[j] <= level:
                    fill = min(level, float(ind.opens[j]))
                    return j, fill
            return None

    def run_strategy(self, strategy, ind: Indicators, ticker: str, timeframe: str,
                      exit_config: str, liquidity_filter: Optional[dict] = None,
                      fee_override: Optional[float] = None,
                      slippage_override: Optional[float] = None,
                      execution_delay: int = 0,
                      precomputed_signals: Optional[List] = None,
                      sector: Optional[str] = None) -> VResult:
        """Scan the whole series once, take every valid signal, simulate the exit
        using the given exit_config. One position open at a time (no pyramiding).

        execution_delay: fills market entries at the close of bar i+delay
        instead of bar i (stress probe / look-ahead shift test). Pending
        (stop/limit) orders start resting delay bars later.

        precomputed_signals: per-bar scan results from scan_all_bars() so a
        sweep can scan each strategy ONCE and replay across all exit
        configs. Valid ONLY for stateless strategies (all swept builtins);
        signals are consulted exactly where a live scan would have run, so
        results are identical to scanning inline.
        """
        exit_cfg = EXIT_CONFIGS.get(exit_config) or CONTROL_EXIT_CONFIGS[exit_config]
        coster = self._coster(ticker, ind, sector=sector,
                              fee_override=fee_override,
                              slippage_override=slippage_override)
        slip = coster.slip_rate
        mult = coster.multiplier

        # Control-only future-data channel (see controls.py contract).
        future_bars = 0
        if getattr(strategy, 'is_control', False):
            future_bars = int(getattr(strategy, 'wants_future_bars', 0) or 0)

        n = ind.n
        min_idx = min(SCAN_WINDOW, 100)  # warmup: covers EMA-50/RSI-14/ATR-14/support-100
        trades: List[VTrade] = []
        zero_size_rejects = 0   # signals killed by affordability, not by edge
        # Bearish-pattern exit bars, only computed for signal exit configs.
        exit_bars = (self.exit_signal_bars(ind)
                     if exit_cfg['type'] == 'signal' else None)

        if n <= min_idx + 1:
            return VResult(strategy.name, ticker, timeframe, exit_config,
                           cost_model_version=coster.version,
                           asset_class=coster.asset_class,
                           instrument=coster.instrument)

        # Buy-and-hold benchmark over the SAME tradable window, both as a price
        # return % (reporting) and as $ PnL on the SAME position size the
        # strategy trades (one notional_cap for spot, one contract for
        # futures), with round-trip fees + slippage on both sides.
        bh_entry_px = float(ind.closes[min_idx]) * (1 + slip)
        bh_exit_px = float(ind.closes[-1]) * (1 - slip)
        bh_return = ((ind.closes[-1] - ind.closes[min_idx]) / ind.closes[min_idx]) * 100
        bh_qty = coster.size(bh_entry_px)
        bh_fees = coster.round_trip_fee(bh_entry_px, bh_exit_px, bh_qty,
                                        exit_ts_ms=int(ind.timestamps[-1]))
        bh_pnl_usd = (bh_exit_px - bh_entry_px) * bh_qty * mult - bh_fees

        i = min_idx
        while i < n:
            if not self._passes_liquidity_filter(ind, i, liquidity_filter):
                i += 1
                continue

            if precomputed_signals is not None:
                signal = precomputed_signals[i]
            else:
                window = self._make_window(ind, i, future_bars=future_bars)
                signal = strategy.scan(window)
            if signal is None or signal.direction != 'bullish' or signal.entry is None or signal.stop is None:
                i += 1
                continue

            if self.apply_confirmation_stack:
                if self.require_regime_uptrend and not ind.regime_uptrend[i]:
                    i += 1
                    continue
                if ind.rsi14[i] > self.rsi_max_entry:
                    i += 1
                    continue
                if ind.vol_ratio20[i] < self.volume_min_ratio:
                    i += 1
                    continue

            signal_idx = i + execution_delay
            if signal_idx >= n:
                break
            fill = self._resolve_entry(ind, signal_idx, signal, slip)
            if fill is None:
                i += 1  # pending order expired unfilled - keep scanning
                continue
            entry_idx, entry_px = fill

            # The strategy's stop is a price level and is honored as-is. A
            # stop at/above the fill price is an invalid long: SKIP the trade
            # (matching the event harness). Substituting an ultra-tight ATR
            # stop here traded a different risk plan than the strategy
            # declared - the exact sin the original audit condemned
            # (re-audit NEW-6).
            initial_stop = float(signal.stop)
            if initial_stop >= entry_px:
                i += 1
                continue

            qty = coster.size(entry_px)
            if qty <= 0:
                # Rejected for lack of capital, not lack of edge. Counted so
                # the difference is visible downstream instead of arriving as
                # an indistinguishable zero-trade FAIL (convention 20).
                zero_size_rejects += 1
                i += 1
                continue
            exit_idx, exit_px, reason = _simulate_exit(
                ind, entry_idx, entry_px, initial_stop, exit_cfg, n - 1,
                exit_bars=exit_bars,
            )

            # Slippage: stops, time exits, and end-of-data closes are market
            # orders -> pay slippage. Targets are resting limit orders -> none.
            actual_exit = exit_px if reason == 'target' else exit_px * (1 - slip)
            entry_fee = coster.leg_fee(entry_px, qty, False,
                                       ts_ms=int(ind.timestamps[entry_idx]))
            exit_fee = coster.leg_fee(actual_exit, qty, True,
                                      ts_ms=int(ind.timestamps[exit_idx]))
            total_fees = entry_fee + exit_fee
            pnl_gross = (actual_exit - entry_px) * qty * mult
            pnl_net = pnl_gross - total_fees

            risk = (entry_px - initial_stop) * qty * mult
            r_multiple = pnl_net / risk if risk > 0 else 0.0

            trades.append(VTrade(
                entry_idx=entry_idx, exit_idx=exit_idx,
                entry_ts=int(ind.timestamps[entry_idx]), exit_ts=int(ind.timestamps[exit_idx]),
                entry_px=entry_px, exit_px=actual_exit, stop_px=initial_stop,
                target_px=None, qty=qty, pnl_gross=pnl_gross, fee_cost=total_fees,
                pnl_net=pnl_net, r_multiple=r_multiple, exit_reason=reason,
                features={'rsi': round(float(ind.rsi14[signal_idx]), 2),
                          'volume_ratio': round(float(ind.vol_ratio20[signal_idx]), 2),
                          'confidence': signal.confidence},
                capital_at_risk=coster.capital_at_risk(entry_px, qty),
            ))

            i = exit_idx + 1  # no overlapping positions

        twin_pfs = self._twin_distribution(
            ind, exit_config, exit_cfg, coster, min_idx,
            time_buckets=self._time_bucket_key(ind, [t.entry_idx for t in trades]))

        return VResult(
            strategy_id=strategy.name, ticker=ticker, timeframe=timeframe,
            exit_config=exit_config, trades=trades,
            buy_hold_return=bh_return, buy_hold_pnl_usd=bh_pnl_usd,
            random_twin_pf=_median(twin_pfs), twin_pfs=twin_pfs,
            cost_model_version=coster.version,
            asset_class=coster.asset_class, instrument=coster.instrument,
            zero_size_rejects=zero_size_rejects,
        )

    @staticmethod
    def _time_bucket_key(ind: Indicators, signal_idx: List[int]) -> Optional[frozenset]:
        """Which minutes-of-day does this strategy actually fire at?

        Returns None when signals are spread across the session (no clock
        anchoring), else the frozenset of minute-of-day buckets to match.
        A strategy firing only at 15:30 must be compared against a twin that
        also enters at 15:30 - otherwise the twin comparison credits the
        CLOCK rather than the signal (strategy-lab-v2 harness warning, which
        is correct: this is silent assertion #15)."""
        if not signal_idx:
            return None
        mins = {int((int(ind.timestamps[i]) // 60000) % 1440) for i in signal_idx}
        # Heuristic: if signals hit more than a third of the day's distinct
        # bar-times present in the series, treat as unanchored.
        all_mins = {int((int(t) // 60000) % 1440) for t in ind.timestamps}
        if len(all_mins) <= 1:
            return None                      # daily/weekly bars: one time only
        if len(mins) > max(3, len(all_mins) // 3):
            return None                      # spread across the session
        return frozenset(mins)

    def _twin_distribution(self, ind: Indicators, exit_config: str, exit_cfg: dict,
                           coster, min_idx: int,
                           time_buckets: Optional[frozenset] = None) -> List[float]:
        """Cached per (ticker-series, exit_config, costs, time-buckets). The
        twin baseline depends only on the price series, the exit rules, and
        WHICH TIMES OF DAY it may enter - never on the strategy itself - so a
        sweep computes it once per distinct combination instead of once per
        strategy. The coster's cache_key carries every cost input, so a twin
        computed under one cost regime can never answer for another."""
        key = (exit_config, coster.cache_key(), min_idx, time_buckets)
        if key not in ind.twin_cache:
            ind.twin_cache[key] = self._run_random_twin(
                ind, exit_cfg, coster, min_idx, time_buckets=time_buckets)
        return ind.twin_cache[key]

    # 100 matched twins (the validation review's recommendation) gives 1%
    # percentile granularity. Affordable because the twin distribution does
    # NOT depend on the strategy - only on (indicators, exit config, costs) -
    # so it is computed once per (ticker, exit_config) and shared across all
    # strategies in the sweep instead of being recomputed 35 times.
    TWIN_SEEDS = 100

    def _run_random_twin(self, ind: Indicators, exit_cfg: dict, coster,
                          min_idx: int,
                          num_entries: Optional[int] = None,
                          time_buckets: Optional[frozenset] = None) -> List[float]:
        """Random-entry baseline run through the SAME exit config, so the
        comparison against the strategy under test is apples to apples.
        Returns the FULL distribution of TWIN_SEEDS independent seeded draws
        (own RNG instance - never touches the global random state). Callers
        take the median for reporting and the percentile for the gate."""
        n = ind.n
        if n - min_idx < 50:
            return []
        slip = coster.slip_rate
        mult = coster.multiplier
        if num_entries is None:
            num_entries = max(10, (n - min_idx) // 50)

        candidates = list(range(min_idx, n - 10))
        if time_buckets:
            # Time-matched twin: draw entries ONLY from the same minutes of
            # day the strategy fires at, so the comparison isolates the
            # signal instead of rewarding the clock.
            matched = [i for i in candidates
                       if int((int(ind.timestamps[i]) // 60000) % 1440) in time_buckets]
            if len(matched) >= 20:
                candidates = matched
        seed_pfs = []
        for seed in range(self.TWIN_SEEDS):
            rng = random.Random(seed)
            entry_indices = sorted(rng.sample(candidates, min(num_entries, len(candidates))))

            pnls = []
            for idx in entry_indices:
                entry_px = ind.closes[idx] * (1 + slip)
                atr_at_entry = ind.atr14[idx]
                initial_stop = entry_px - STOP_ATR_MULT * atr_at_entry if atr_at_entry > 0 else entry_px * 0.98
                qty = coster.size(entry_px)
                if qty <= 0:
                    continue
                exit_idx, exit_px, reason = _simulate_exit(ind, idx, entry_px, initial_stop, exit_cfg, n - 1)
                # Same slippage convention as the strategy path: limit targets
                # pay none, every market exit (stop/time/end) pays slippage.
                actual_exit = exit_px if reason == 'target' else exit_px * (1 - slip)
                entry_fee = coster.leg_fee(entry_px, qty, False,
                                           ts_ms=int(ind.timestamps[idx]))
                exit_fee = coster.leg_fee(actual_exit, qty, True,
                                          ts_ms=int(ind.timestamps[exit_idx]))
                pnl_net = ((actual_exit - entry_px) * qty * mult
                           - entry_fee - exit_fee)
                pnls.append(pnl_net)

            if not pnls:
                continue
            gross_profit = sum(p for p in pnls if p > 0)
            gross_loss = abs(sum(p for p in pnls if p <= 0))
            if gross_loss == 0:
                seed_pfs.append(float('inf') if gross_profit > 0 else 0.0)
            else:
                seed_pfs.append(gross_profit / gross_loss)

        return seed_pfs

    def exit_signal_bars(self, ind: Indicators) -> np.ndarray:
        """Boolean array: does ANY bearish exit pattern fire on this bar?

        Computed once per series and cached, since it depends only on price
        (not on the entry strategy or exit config). This is what finally puts
        the 14 EXIT_STRATEGIES_EXPANDED into the graveyard."""
        if ind.exit_bars_cache is not None:
            return ind.exit_bars_cache

        from strategies.builtin.expanded import EXIT_STRATEGIES_EXPANDED
        n = ind.n
        out = np.zeros(n, dtype=bool)
        min_idx = min(SCAN_WINDOW, 100)
        for i in range(min_idx, n):
            window = self._make_window(ind, i)
            for strat in EXIT_STRATEGIES_EXPANDED:
                try:
                    sig = strat.scan(window)
                except Exception:
                    continue
                if sig is not None and sig.action == 'close_long':
                    out[i] = True
                    break
        ind.exit_bars_cache = out
        return out

    def scan_all_bars(self, strategy, ind: Indicators,
                      liquidity_filter: Optional[dict] = None) -> List:
        """One scan pass over the whole series for a stateless strategy.
        Returns per-bar signals for run_strategy(precomputed_signals=...).
        This is the 9x saving that makes sweeps tractable with ta-backed
        indicators: signals don't depend on the exit config, so scanning
        once per strategy instead of once per (strategy, exit_config) is
        semantically identical and skips 8 redundant full-series scans."""
        n = ind.n
        min_idx = min(SCAN_WINDOW, 100)
        # A strategy that declared min_bars > SCAN_WINDOW (e.g. C2's 840) gets
        # ITS OWN wider window here; scan() still self-guards on `n < min_bars`
        # for the early bars where even the widened window is short, so this
        # never needs a different min_idx.
        window_size = max(SCAN_WINDOW, getattr(strategy, 'min_bars', 0) or 0)
        signals: List = [None] * n
        for i in range(min_idx, n):
            if not self._passes_liquidity_filter(ind, i, liquidity_filter):
                continue
            signals[i] = strategy.scan(self._make_window(ind, i, window_size=window_size))
        return signals

    # ---- sweep across strategies x exit configs, writing a graveyard JSON ----

    def run_sweep(self, candles: List[dict], ticker: str, timeframe: str,
                  strategies: Optional[List] = None,
                  exit_configs: Optional[List[str]] = None,
                  liquidity_filter: Optional[dict] = None,
                  min_pf: float = 1.15, min_trades: int = 20,
                  sector: Optional[str] = None) -> List[dict]:
        """Precompute indicators once, then run every (strategy, exit_config) pair
        against those same arrays. Returns a list of report dicts (also the
        shape written to the graveyard JSON)."""
        strategies = strategies if strategies is not None else ENTRY_STRATEGIES_EXPANDED
        exit_configs = exit_configs if exit_configs is not None else list(EXIT_CONFIGS.keys())

        t0 = time_mod.time()
        ind = precompute_indicators(candles)
        precompute_s = time_mod.time() - t0
        logger.info(f"Precomputed indicators for {ticker} {timeframe} "
                    f"({ind.n} candles) in {precompute_s:.2f}s")

        # Stamp NOT_TESTED rows with the same cost identity as tested ones,
        # so the version-uniformity assertion sees one dataset either way.
        sweep_coster = self._coster(ticker, ind, sector=sector)

        # STRUCTURAL AFFORDABILITY GATE (Raven ruling R-002).
        # `coster.size()` returns a fixed contract count for contract
        # instruments, so when the account cannot afford one contract the
        # answer is 0 at every price on every bar. No signal from any strategy
        # could ever become a position on this series. That is "the harness
        # could not run this", which is NOT_TESTED - not a FAIL, which would
        # claim we tested an idea and it lost money (convention 11).
        # Read once per series because it is a property of the instrument and
        # the notional cap, never of the strategy.
        unsizable = not sweep_coster.can_size
        if unsizable:
            per_unit = (getattr(sweep_coster.spec, 'initial_margin', None)
                        or sweep_coster.reference_price * sweep_coster.multiplier)
            unsizable_detail = (
                f'one {sweep_coster.instrument} needs ${per_unit:,.0f}, '
                f'notional cap is ${sweep_coster.notional_cap:,.0f}')
            logger.info(f'{ticker} {timeframe}: UNSIZABLE at cap '
                        f'({unsizable_detail}) - all rows NOT_TESTED')

        reports = []
        # Loop order: strategy OUTER so each strategy is scanned exactly once
        # (scan_all_bars) and replayed across every exit config. Signals are
        # exit-config-independent; this is a ~9x reduction in scan cost.
        for strategy in strategies:
            # NOT_TESTED means "the harness structurally could not have run
            # this", never "it ran and produced nothing" (D-109). Two ways
            # that happens: the strategy demands more history than any
            # reasonable window would supply (min_bars > MAX_STRATEGY_WINDOW,
            # a defensive ceiling), or THIS series is simply too short for it
            # (ind.n < min_bars) even though scan_all_bars will widen the
            # window up to MAX_STRATEGY_WINDOW for strategies that ask for
            # more than SCAN_WINDOW (e.g. C2's 840). A strategy that clears
            # both checks but still fires zero signals was genuinely tested
            # and reports FAIL/0-trades like anything else - that is not a
            # NOT_TESTED case.
            min_bars = getattr(strategy, 'min_bars', 0)
            if min_bars > MAX_STRATEGY_WINDOW or ind.n < min_bars:
                reason = (f'needs {min_bars} bars, max strategy window is {MAX_STRATEGY_WINDOW}'
                          if min_bars > MAX_STRATEGY_WINDOW
                          else f'needs {min_bars} bars, series has {ind.n}')
                for exit_config in exit_configs:
                    reports.append({
                        'strategy': strategy.name, 'ticker': ticker,
                        'timeframe': timeframe, 'exit_config': exit_config,
                        'trades': 0, 'verdict': 'NOT_TESTED',
                        'not_tested_reason': reason,
                        'gate_version': GATE_VERSION,
                        'cost_model_version': sweep_coster.version,
                        'asset_class': sweep_coster.asset_class,
                        'instrument': sweep_coster.instrument,
                        'inversion_flagged': False,
                    })
                continue

            # Bar-count gate first, so its rows keep exactly the labels and
            # prose they had before this gate existed. A series that is BOTH
            # too short and unaffordable is reported as too short; either
            # reason is sufficient and neither is more true than the other.
            if unsizable:
                for exit_config in exit_configs:
                    reports.append({
                        'strategy': strategy.name, 'ticker': ticker,
                        'timeframe': timeframe, 'exit_config': exit_config,
                        'trades': 0, 'verdict': 'NOT_TESTED',
                        'not_tested_reason': 'unsizable_at_cap',
                        'not_tested_detail': unsizable_detail,
                        'gate_version': GATE_VERSION,
                        'cost_model_version': sweep_coster.version,
                        'asset_class': sweep_coster.asset_class,
                        'instrument': sweep_coster.instrument,
                        'inversion_flagged': False,
                    })
                continue

            sig_cache = self.scan_all_bars(strategy, ind, liquidity_filter)
            for exit_config in exit_configs:
                result = self.run_strategy(
                    strategy, ind, ticker, timeframe, exit_config,
                    liquidity_filter=liquidity_filter,
                    precomputed_signals=sig_cache,
                    sector=sector,
                )
                report = result.to_report()

                pf = result.profit_factor
                passed = (
                    pf != float('inf') and pf >= min_pf and
                    result.beats_buy_hold() and
                    result.beats_random_twin() and
                    result.trade_count >= min_trades
                )
                # A benchmark strategy (DCA etc.) has no signal: passing the
                # gate means the market cooperated, not that edge exists.
                # Label it so no downstream agent mistakes it for a discovery.
                if getattr(strategy, 'is_benchmark', False):
                    report['is_benchmark'] = True
                    report['verdict'] = 'PASS_BENCHMARK' if passed else 'FAIL'
                else:
                    report['verdict'] = 'PASS' if passed else 'FAIL'
                report['inversion_flagged'] = bool(
                    pf < 0.5 and pf != float('inf') and result.trade_count >= 10
                )
                reports.append(report)

        elapsed = time_mod.time() - t0
        logger.info(f"Sweep complete: {len(reports)} (strategy x exit_config) runs "
                    f"in {elapsed:.2f}s ({precompute_s:.2f}s precompute)")
        return reports

    @staticmethod
    def write_graveyard(reports: List[dict], filepath: str):
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        graveyard = {
            'generated': time_mod.strftime('%Y-%m-%d %H:%M:%S'),
            'total_tests': len(reports),
            'passed': sum(1 for r in reports if r['verdict'] == 'PASS'),
            'failed': sum(1 for r in reports if r['verdict'] == 'FAIL'),
            'inversions_flagged': sum(1 for r in reports if r.get('inversion_flagged')),
            'entries': reports,
        }
        with open(path, 'w') as f:
            json.dump(graveyard, f, indent=2)
        logger.info(f"Wrote graveyard: {path} ({len(reports)} entries)")
        return graveyard
