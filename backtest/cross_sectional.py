"""Cross-sectional backtest harness: rank the universe, trade the extremes.

SPEC 5.8 (V2 lane). Every prior harness is TIME-SERIES: it walks one
instrument's bars and asks "does this instrument's own history trigger an
entry." This harness holds the WHOLE universe at one moment, computes a
per-instrument ranking metric, and opens positions in the top/bottom slice.
Cross-sectional ranking neutralizes market drift by construction (long the
top decile vs flat the rest removes the beta term instead of subtracting a
buy-and-hold benchmark afterward - the comparison that caused repeated
methodological trouble in the time-series lane).

DESIGN (SPEC 5.8 required design, point by point)
1. ALIGNMENT: N instruments on a common timestamp grid = the UNION of their
   bar keys. Forward-fill NOTHING. A name with no bar at a grid step is simply
   not tradable at that step and contributes no history bar for it either.
2. STEPPING / LOOKAHEAD: at each grid step the ranker receives a PanelView
   exposing, per instrument, ONLY bars whose key is STRICTLY BEFORE the step
   key. The bar being traded at the step is structurally unreachable by the
   ranker - it is not "please don't peek", the slice simply ends before it.
   This is deliberately one bar more conservative than the time-series
   harness (where scan() sees the signal bar it fills on): the lookahead
   class of bug cost this project its first graveyard, so the structural
   guarantee is worth one bar of signal freshness. tests/test_cross_sectional
   holds an oracle test proving a "cheating" ranker is denied the trade bar.
3. SELECTION: top/bottom K, decile, or quintile of the scored names.
4. FILLS/COSTS: same semantics as backtest/vectorized_harness.py -
   entry at close (or open, for slot strategies) of the decision bar plus
   slippage; gap-aware stop fills (min(stop, open)); market exits pay
   slippage, and both legs pay fees through backtest/cost_model.TradeCoster
   (venue-accurate, D-235) resolved per instrument via resolve_asset_class.
5. REBALANCE: on the strategy's own schedule (rebalance_every grid steps, or
   the ranker gates itself by returning {} off-schedule). Names selected but
   missing their bar at the step are NOT opened and NOT phantom-carried -
   they are re-evaluated fresh at the next rebalance (counted in
   fires_check.skipped_missing_bar). A name already held is never doubled.
6. REPORTING: pooled result + per-cell (per-instrument) rows + the same
   leave-one-asset-out guard backtest/asset_class_analysis.py uses (a pooled
   number carried by one underlying is that underlying wearing a costume).

NON-NEGOTIABLES INHERITED FROM THE AUDITS (SPEC 5.8)
- Time-matched random twins: twins replay the STRATEGY'S OWN formation
  steps - same clock slots, same breadth (same number of names opened per
  step), same exit rules, same costs - with only the NAME CHOICE randomized.
  This is the cross-sectional analog of vectorized_harness._time_bucket_key /
  _run_random_twin: a strategy anchored to 15:30 is compared against twins
  that also trade at 15:30, so the twin comparison isolates the ranking
  instead of crediting the clock.
- Survivorship: the universe is today's listing, not the listing at time T.
  Known bias. Every result is stamped 'survivorship': 'survivors-only
  universe' so no downstream reader can miss it.
- Cost-model version stamped on every result; results carrying different
  stamps must never be pooled (cost_model_version_uniform is computed here;
  backtest/assertions.py enforces it downstream).

KILL CONDITION (for this module - it is measurement apparatus, so its kill
condition is about ITS OWN validity, not an edge): if the lookahead oracle
test in tests/test_cross_sectional.py ever shows the cheating ranker
capturing the trade bar's return, the harness is broken and every result it
has produced is void. No result from this harness is durable until that
suite passes, per standing rule 1 (validate before trusting).

STRATEGY RANKERS INCLUDED (the run scripts wire them up):
- make_rev_ranker / make_mom_ranker: Lab v5 P1 Horizon Ladder generic
  signals (see backtest/run_horizon_ladder.py for the pre-registration).
- make_same_clock_echo_ranker: Lab v3 #3 (Heston/Korajczyk/Sadka, JF 2010).
- make_paid_liquidity_reversal_ranker: Lab v3 #5 (Nagel, RFS 2012) -
  DEVIATION: the news-exclusion filter is SKIPPED (no earnings calendar
  exists in this environment). See the ranker docstring; the deviation is
  material and is stamped into the run params.

Python 3.9 compatible: Optional[X], not X | None. Rank code never raises -
a ranker exception degrades to no-signal for that step and is counted.
"""
import argparse
import json
import logging
import random
import sys
import time as time_mod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

# Runnable both as a module (python3 -m backtest.cross_sectional / imports)
# and as a script (python3 backtest/cross_sectional.py), same bootstrap the
# other backtest run scripts use.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.cost_model import CostModel, FlatCoster
from backtest.instruments import resolve_asset_class

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'backtest' / 'data'
DAY_MS = 86_400_000

# Bump whenever a change alters fill/cost/selection semantics, so results
# from different eras of this harness can never be silently pooled (same
# convention as vectorized_harness.GATE_VERSION).
CS_GATE_VERSION = 1

# SPEC 5.8: absent delisted-name data the universe is survivors-only. This
# string goes on EVERY result so the bias is visible in every downstream file.
SURVIVORSHIP_STAMP = 'survivors-only universe'


# ============ SMALL SHARED HELPERS ============

def _wilder_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                period: int = 14) -> np.ndarray:
    """Same ta-backed ATR as vectorized_harness._wilder_atr (Aym ruling
    2026-08-12: indicators are ta facades). Copied rather than imported so
    this module does not drag in the strategy zoo vectorized_harness imports
    at module load."""
    n = len(highs)
    if n < period + 1:
        return np.zeros(n)
    import pandas as pd
    from ta.volatility import AverageTrueRange
    series = AverageTrueRange(pd.Series(highs), pd.Series(lows),
                              pd.Series(closes), window=period).average_true_range()
    return series.fillna(0.0).to_numpy()


def aggregate_15m_to_30m(candles: List[dict]) -> List[dict]:
    """Build 30-minute bars from 15-minute bars, grouped on half-hour
    boundaries (ts // 30min). Needed because Same-Clock Echo (Lab v3 #3) is
    defined on half-hour slots and the data store has *_15m but no *_30m.

    UTC half-hours coincide with New-York half-hours (the offset is a whole
    number of hours), so grouping in UTC is slot-faithful. A half hour with
    only one 15m bar present becomes a 30m bar of that single bar - no
    forward fill, the gap stays a gap."""
    out: List[dict] = []
    cur_key = None
    cur: Optional[dict] = None
    for c in candles:
        key = c['ts'] // (30 * 60 * 1000)
        if key != cur_key:
            if cur is not None:
                out.append(cur)
            cur_key = key
            cur = {'ts': key * 30 * 60 * 1000, 'open': c['open'],
                   'high': c['high'], 'low': c['low'], 'close': c['close'],
                   'volume': c['volume']}
        else:
            cur['high'] = max(cur['high'], c['high'])
            cur['low'] = min(cur['low'], c['low'])
            cur['close'] = c['close']
            cur['volume'] += c['volume']
    if cur is not None:
        out.append(cur)
    return out


def select_names(scores: Dict[str, float], selection: dict) -> List[str]:
    """Pick the traded slice from a {name: score} dict. Pure function so the
    selection arithmetic is unit-testable in isolation.

    selection keys:
      direction: 'top' | 'bottom'   (which extreme is LONGED)
      mode: 'decile' | 'quintile' | 'k'
      k: int (mode 'k' only)
      min_scored: minimum number of scored names for a valid cross-section
                  (a "decile" of 3 names is not a decile; default 10)
      min_names / max_names: clamp on the selected count (genome "min 3 /
                  max 5" style constraints). min_names unmet => no selection.
    """
    clean = {k: v for k, v in scores.items()
             if v is not None and np.isfinite(v)}
    min_scored = selection.get('min_scored', 10)
    if len(clean) < min_scored:
        return []
    mode = selection.get('mode', 'decile')
    if mode == 'decile':
        count = max(1, int(len(clean) * 0.10))
    elif mode == 'quintile':
        count = max(1, int(len(clean) * 0.20))
    elif mode == 'k':
        count = int(selection.get('k', 1))
    else:
        raise ValueError(f'unknown selection mode: {mode}')
    max_names = selection.get('max_names')
    if max_names is not None:
        count = min(count, int(max_names))
    min_names = selection.get('min_names')
    reverse = selection.get('direction', 'top') == 'top'
    # Tie-break on name so selection is deterministic run to run.
    ranked = sorted(clean.items(), key=lambda kv: (kv[1], kv[0]), reverse=reverse)
    picked = [name for name, _ in ranked[:count]]
    if min_names is not None and len(picked) < int(min_names):
        return []
    return picked


# ============ PANEL: THE ALIGNED UNIVERSE ============

@dataclass
class _Series:
    """One instrument's raw bars as numpy arrays, plus its alignment maps."""
    name: str
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray
    ts: np.ndarray            # true bar timestamps, ms
    keys: np.ndarray          # alignment keys (ts, or day-floored ts)
    pos_at: np.ndarray = field(default=None)      # grid step -> bar idx or -1
    n_before: np.ndarray = field(default=None)    # grid step -> bars strictly before
    atr14: np.ndarray = field(default=None)       # lazy, for stop configs

    @property
    def n(self) -> int:
        return len(self.ts)


def _to_series(name: str, candles: List[dict], date_align: bool) -> Optional[_Series]:
    if not candles:
        return None
    # The shared loader sorts and dedupes, but this class also takes raw
    # candle lists (tests, aggregation output) - sort defensively, because
    # every alignment map below assumes monotone keys.
    candles = sorted(candles, key=lambda c: c['ts'])
    ts = np.array([c['ts'] for c in candles], dtype=np.int64)
    keys = (ts // DAY_MS) * DAY_MS if date_align else ts
    # Duplicate keys would make "the bar at step g" ambiguous (two bars, one
    # date). Keep the FIRST and warn - the loader already dedupes exact
    # timestamps, so this only fires on odd data (e.g. two sessions per date).
    uniq, first_idx = np.unique(keys, return_index=True)
    if len(uniq) != len(keys):
        logger.warning('%s: %d duplicate alignment keys dropped (kept first)',
                       name, len(keys) - len(uniq))
    sel = np.sort(first_idx)
    return _Series(
        name=name,
        opens=np.array([c['open'] for c in candles], dtype=float)[sel],
        highs=np.array([c['high'] for c in candles], dtype=float)[sel],
        lows=np.array([c['low'] for c in candles], dtype=float)[sel],
        closes=np.array([c['close'] for c in candles], dtype=float)[sel],
        volumes=np.array([c['volume'] for c in candles], dtype=float)[sel],
        ts=ts[sel], keys=keys[sel],
    )


class Panel:
    """N tradable instruments + named CONTEXT instruments on one grid.

    Grid = sorted UNION of the TRADABLE instruments' alignment keys. Nothing
    is forward-filled: a name missing a grid key has pos_at[g] == -1 there
    and its history at later steps contains no bar for that key.

    CONTEXT SERIES (SPEC 5.8 second gap): instruments a ranker may READ but
    can never trade - VIX, sector ETFs, peer names. They are stored apart
    from the tradables, do not extend the grid, and the harness only ever
    opens positions in panel.tickers, so trading a context name is
    structurally impossible rather than merely discouraged.

    date_align=True floors keys to the UTC day: needed to put equity daily
    bars (04:00/05:00 UTC stamps) and crypto daily bars (00:00 UTC) on one
    daily cross-section. The bars keep their TRUE timestamps for fee/fill
    bookkeeping; only the alignment key is floored. This is alignment, not
    fill - no bar is invented.
    """

    def __init__(self, series: Dict[str, List[dict]],
                 context: Optional[Dict[str, List[dict]]] = None,
                 date_align: bool = False):
        self.date_align = date_align
        self.series: Dict[str, _Series] = {}
        self.context: Dict[str, _Series] = {}
        for name, candles in series.items():
            s = _to_series(name, candles, date_align)
            if s is not None and s.n > 0:
                self.series[name] = s
        for name, candles in (context or {}).items():
            if name in self.series:
                raise ValueError(f'context name collides with tradable: {name}')
            s = _to_series(name, candles, date_align)
            if s is not None and s.n > 0:
                self.context[name] = s
        if not self.series:
            raise ValueError('panel has no tradable series')

        # Union grid over TRADABLES only.
        self.grid = np.unique(np.concatenate(
            [s.keys for s in self.series.values()]))
        # Alignment maps, O(n log n) once instead of O(log n) per lookup in
        # the hot loop.
        for s in list(self.series.values()) + list(self.context.values()):
            left = np.searchsorted(s.keys, self.grid, side='left')
            s.n_before = left.astype(np.int64)
            hit = (left < s.n) & (s.keys[np.minimum(left, s.n - 1)] == self.grid)
            pos = np.where(hit, left, -1)
            s.pos_at = pos.astype(np.int64)

    @property
    def tickers(self) -> List[str]:
        return sorted(self.series.keys())

    @property
    def n_steps(self) -> int:
        return len(self.grid)

    def lookup(self, name: str) -> Optional[_Series]:
        return self.series.get(name) or self.context.get(name)

    @classmethod
    def from_csv_dir(cls, tickers: List[str], timeframe: str = '1d',
                     context_tickers: Optional[List[str]] = None,
                     data_dir: Optional[Path] = None,
                     date_align: bool = False,
                     to_30m: bool = False) -> 'Panel':
        """Load {ticker}_{timeframe}.csv files through the shared loader
        (backtest/data_loader.py - the pandas rewrite; 706 files were
        silently unparseable under the old parser). Missing files are skipped
        with a warning, never invented. to_30m aggregates 15m bars into 30m
        slots for the Same-Clock Echo lane."""
        from backtest.data_loader import load_csv
        data_dir = Path(data_dir) if data_dir else DATA_DIR

        def _load(name: str) -> List[dict]:
            path = data_dir / f'{name}_{timeframe}.csv'
            if not path.exists():
                logger.warning('no data file for %s (%s)', name, path.name)
                return []
            candles = load_csv(str(path), name, timeframe)
            if to_30m:
                candles = aggregate_15m_to_30m(candles)
            return candles

        series = {t: _load(t) for t in tickers}
        series = {t: c for t, c in series.items() if c}
        context = {}
        for t in (context_tickers or []):
            candles = _load(t)
            if candles:
                context[t] = candles
        return cls(series, context=context, date_align=date_align)


class PanelView:
    """What a ranker is allowed to see at one grid step: every instrument's
    bars with key STRICTLY BEFORE the step key. The decision/trade bar is not
    in here - that is the structural no-lookahead guarantee, and the oracle
    test in tests/test_cross_sectional.py depends on it staying structural.

    history() serves tradables AND context names through one call; the
    harness itself only ever trades panel.tickers.
    """

    __slots__ = ('_panel', '_g', 'key', 'ts')

    def __init__(self, panel: Panel, g: int):
        self._panel = panel
        self._g = g
        self.key = int(panel.grid[g])   # alignment key of the decision step
        self.ts = self.key              # alias; true decision-bar ts is per-name

    def history(self, name: str) -> Optional[Dict[str, np.ndarray]]:
        """Arrays of all bars strictly before this step for `name`, or None
        if it has none yet. numpy slices (views, no copy) - cheap to call in
        the hot loop."""
        s = self._panel.lookup(name)
        if s is None:
            return None
        k = int(s.n_before[self._g])
        if k <= 0:
            return None
        return {'opens': s.opens[:k], 'highs': s.highs[:k],
                'lows': s.lows[:k], 'closes': s.closes[:k],
                'volumes': s.volumes[:k], 'ts': s.ts[:k]}

    def bars_before(self, name: str) -> int:
        s = self._panel.lookup(name)
        return int(s.n_before[self._g]) if s is not None else 0


# ============ TRADE RECORD ============

@dataclass
class CSTrade:
    ticker: str
    entry_idx: int
    exit_idx: int
    entry_ts: int
    exit_ts: int
    entry_px: float
    exit_px: float
    stop_px: Optional[float]
    qty: float
    pnl_gross: float
    fee_cost: float
    pnl_net: float
    exit_reason: str        # 'stop' | 'time' | 'slot_close' | 'end_of_data'
    asset_class: str
    capital_at_risk: float


def _pf(pnls: List[float]) -> float:
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p <= 0))
    if gl == 0:
        return float('inf') if gp > 0 else 0.0
    return gp / gl


def _underlying(t: str) -> str:
    """BTC/USDT, BTC_USD and BTC_F are one asset for leave-one-out purposes
    (same normalization as backtest/asset_class_analysis.py)."""
    return (t or '').split('/')[0].replace('_USD', '').replace('_F', '')


def leave_one_out(trades: List[CSTrade]) -> dict:
    """LEAVE-ONE-ASSET-OUT robustness, per asset_class_analysis.py. Three
    separate concentration incidents were caught this way (HANDOVER key
    finding 6): a pooled number carried by one underlying is that underlying
    wearing a costume. Returns the worst drop and whether the pooled result
    is carried by a single asset (>$0.15/trade swing, the analysis module's
    own threshold)."""
    n = len(trades)
    if n == 0:
        return {'n_assets': 0, 'worst_drop_asset': None,
                'pnl_per_trade_worst_drop': None, 'carried_by_one_asset': False}
    total = sum(t.pnl_net for t in trades)
    by_asset: Dict[str, List[float]] = {}
    for t in trades:
        by_asset.setdefault(_underlying(t.ticker), []).append(t.pnl_net)
    worst = None
    for asset, pnls in by_asset.items():
        rem_n = n - len(pnls)
        if rem_n <= 0:
            continue
        rem_ppt = (total - sum(pnls)) / rem_n
        if worst is None or rem_ppt < worst[1]:
            worst = (asset, rem_ppt)
    pooled_ppt = total / n
    return {
        'n_assets': len(by_asset),
        'worst_drop_asset': worst[0] if worst else None,
        'pnl_per_trade_worst_drop': round(worst[1], 4) if worst else None,
        'carried_by_one_asset': bool(worst and pooled_ppt - worst[1] > 0.15),
    }


# ============ THE HARNESS ============

class CrossSectionalHarness:
    """Steps the grid, ranks with a PanelView, opens the selected slice with
    vectorized-harness fill/cost semantics, reports pooled + per-cell +
    leave-one-out + time-matched twins.

    use_cost_model defaults TRUE here (unlike vectorized_harness, whose
    default must stay bit-identical to the flat legacy model for the
    cross-harness referee). This harness is new - it has no flat-era referee
    to agree with - so it starts life on the venue-accurate model. The flat
    model remains available (config {'use_cost_model': False}) for zero-cost
    probes, and either way the version stamp makes the regimes unpoolable.
    """

    TWIN_SEEDS = 100   # 1% percentile granularity, matching vectorized

    def __init__(self, config: Optional[dict] = None):
        config = config or {}
        self.notional_cap = config.get('risk', {}).get('notional_cap_usd', 100)
        self.taker_fee = config.get('exchange', {}).get('fees', {}).get('taker', 0.001)
        self.slippage = config.get('exchange', {}).get('slippage', {}).get('market', 0.0005)
        self.use_cost_model = bool(config.get('use_cost_model', True))
        self.cost_model = CostModel()

    # -- costs ----------------------------------------------------------

    def _coster(self, s: _Series, sector: Optional[str]):
        """One coster per instrument, exactly as vectorized_harness._coster:
        venue-accurate when opted in, legacy flat otherwise. Reference price
        is the series median close (stable against gaps/drift; it only
        scales the futures tick->rate conversion)."""
        if not self.use_cost_model:
            return FlatCoster(self.taker_fee, self.slippage,
                              notional_cap=self.notional_cap)
        asset_class = resolve_asset_class(s.name, sector)
        ref_px = float(np.median(s.closes)) if s.n else 1.0
        return self.cost_model.coster(s.name, asset_class, ref_px,
                                      notional_cap=self.notional_cap)

    # -- exits ----------------------------------------------------------

    @staticmethod
    def _walk_exit(s: _Series, i: int, stop_px: Optional[float],
                   exit_cfg: dict) -> Optional[Tuple[int, float, str]]:
        """First-touch exit walk over the instrument's OWN bars, from i+1.
        Gap-aware stop fills: a bar that opens through the stop fills at the
        OPEN, not the stop (filling at the stop on a gap-through
        systematically understates losses - same rule as
        vectorized_harness._simulate_exit). Returns (exit_idx, raw_px,
        reason) or None when the trade cannot be held at all (entry on the
        final bar of a time exit)."""
        cfg_type = exit_cfg['type']
        n = s.n
        if cfg_type == 'same_bar_close':
            # Slot strategies: enter at the bar's open, exit at its close.
            return i, float(s.closes[i]), 'slot_close'
        if cfg_type == 'time':
            if i >= n - 1:
                return None       # no future bar exists to exit on
            bars = int(exit_cfg['bars'])
            last = min(i + bars, n - 1)
            if stop_px is not None:
                lows = s.lows[i + 1:last + 1]
                hit = lows <= stop_px
                if hit.any():
                    j = i + 1 + int(np.argmax(hit))
                    return j, min(stop_px, float(s.opens[j])), 'stop'
            reason = 'time' if i + bars <= n - 1 else 'end_of_data'
            return last, float(s.closes[last]), reason
        raise ValueError(f'unknown exit config type: {cfg_type}')

    def _open_trade(self, s: _Series, g: int, entry_mode: str,
                    exit_cfg: dict, coster) -> Optional[CSTrade]:
        """Fill + exit + fees for one selected name at one step. Mirrors the
        vectorized harness trade block: slippage moves the fill price (it
        also moves the stop distance), fees come off the PnL in dollars."""
        i = int(s.pos_at[g])
        if i < 0:
            return None
        slip = coster.slip_rate
        mult = coster.multiplier
        if entry_mode == 'close':
            entry_px = float(s.closes[i]) * (1 + slip)
        elif entry_mode == 'open':
            entry_px = float(s.opens[i]) * (1 + slip)
        else:
            raise ValueError(f'unknown entry mode: {entry_mode}')
        qty = coster.size(entry_px)
        if qty <= 0:
            return None

        # Stop from ATR at the last VISIBLE bar (i-1) - never the trade bar,
        # for the same reason the ranker never sees it. Fallback 2% when ATR
        # has no warmup (matches the twin fallback in vectorized_harness).
        stop_px = None
        stop_mult = exit_cfg.get('stop_atr_mult')
        if stop_mult is not None:
            if s.atr14 is None:
                s.atr14 = _wilder_atr(s.highs, s.lows, s.closes, 14)
            atr = float(s.atr14[i - 1]) if i >= 1 else 0.0
            stop_px = (entry_px - stop_mult * atr) if atr > 0 else entry_px * 0.98
            if stop_px >= entry_px:
                return None       # degenerate stop: skip, never "fix" it

        walked = self._walk_exit(s, i, stop_px, exit_cfg)
        if walked is None:
            return None
        exit_idx, raw_px, reason = walked
        # Every exit here is a market order (stop/time/slot close), so every
        # exit pays slippage; there are no resting-limit targets in v1 of
        # this harness.
        actual_exit = raw_px * (1 - slip)
        entry_fee = coster.leg_fee(entry_px, qty, False, ts_ms=int(s.ts[i]))
        exit_fee = coster.leg_fee(actual_exit, qty, True, ts_ms=int(s.ts[exit_idx]))
        pnl_gross = (actual_exit - entry_px) * qty * mult
        return CSTrade(
            ticker=s.name, entry_idx=i, exit_idx=exit_idx,
            entry_ts=int(s.ts[i]), exit_ts=int(s.ts[exit_idx]),
            entry_px=entry_px, exit_px=actual_exit, stop_px=stop_px, qty=qty,
            pnl_gross=pnl_gross, fee_cost=entry_fee + exit_fee,
            pnl_net=pnl_gross - (entry_fee + exit_fee),
            exit_reason=reason,
            asset_class=coster.asset_class,
            capital_at_risk=coster.capital_at_risk(entry_px, qty),
        )

    # -- main loop ------------------------------------------------------

    def run(self, panel: Panel, ranker: Callable[[PanelView], Dict[str, float]],
            strategy_id: str, selection: dict, exit_cfg: dict,
            entry_mode: str = 'close', rebalance_every: int = 1,
            min_history: int = 30,
            sector_of: Optional[Dict[str, str]] = None,
            params: Optional[dict] = None,
            twin_seeds: Optional[int] = None,
            include_trades: bool = False,
            debug_twins: bool = False) -> dict:
        """One full cross-sectional run. Returns the report dict (the shape
        written to result JSONs).

        ranker(view) -> {ticker: score}. It may score any subset; the
        harness intersects with eligibility (bar present at the step AND
        >= min_history prior bars). A raising ranker degrades to {} for that
        step and is counted - rank code must never kill a sweep (standing
        rule: scan/rank code never raises).

        rebalance_every is measured in GRID STEPS. Formation happens at the
        first step at least rebalance_every steps after the previous
        formation that clears min_scored - so a weekend step with only three
        crypto names present does not consume the schedule slot for a
        174-name universe.
        """
        sector_of = sector_of or {}
        twin_n = self.TWIN_SEEDS if twin_seeds is None else int(twin_seeds)
        costers = {name: self._coster(s, sector_of.get(name))
                   for name, s in panel.series.items()}

        trades: List[CSTrade] = []
        held_until: Dict[str, int] = {}   # ticker -> last grid key it is held through
        # Twin replay script: (grid step, n opened, eligible names then).
        formation_log: List[Tuple[int, int, List[str]]] = []
        fires = {'grid_steps': panel.n_steps, 'formations': 0,
                 'steps_skipped_min_scored': 0, 'ranker_errors': 0,
                 'names_selected_total': 0, 'names_opened': 0,
                 'names_skipped_already_held': 0,
                 'names_skipped_missing_bar': 0,
                 'names_skipped_unfillable': 0}

        last_form_g = -10**9
        for g in range(panel.n_steps):
            if g - last_form_g < rebalance_every:
                continue
            key = int(panel.grid[g])
            view = PanelView(panel, g)
            try:
                scores = ranker(view) or {}
            except Exception:
                # Degrade to no-signal, never raise: one bad step must not
                # kill a sweep. Counted so a dead ranker is visible in the
                # fires-check, not silent.
                fires['ranker_errors'] += 1
                scores = {}
            # Eligibility: tradable, has a bar AT this step, enough history.
            # Context names can never pass (they are not in panel.series).
            eligible = {}
            for name, score in scores.items():
                s = panel.series.get(name)
                if s is None:
                    continue
                if s.pos_at[g] < 0:
                    fires['names_skipped_missing_bar'] += 1
                    continue
                if s.n_before[g] < min_history:
                    continue
                eligible[name] = score
            picked = select_names(eligible, selection)
            if not picked:
                if scores:
                    fires['steps_skipped_min_scored'] += 1
                continue
            last_form_g = g
            fires['formations'] += 1
            fires['names_selected_total'] += len(picked)
            opened_here = 0
            for name in picked:
                if held_until.get(name, -1) >= key:
                    fires['names_skipped_already_held'] += 1
                    continue
                trade = self._open_trade(panel.series[name], g, entry_mode,
                                         exit_cfg, costers[name])
                if trade is None:
                    fires['names_skipped_unfillable'] += 1
                    continue
                trades.append(trade)
                held_until[name] = int(panel.series[name].keys[trade.exit_idx])
                opened_here += 1
            fires['names_opened'] += opened_here
            # Twins replay every formation the strategy attempted, with the
            # SAME breadth, even if some strategy fills failed - breadth is
            # part of the strategy's footprint, not of the name choice.
            all_eligible = [t for t in panel.tickers
                            if panel.series[t].pos_at[g] >= 0
                            and panel.series[t].n_before[g] >= min_history]
            formation_log.append((g, len(picked), all_eligible))

        # FIRES-CHECK before any P&L is computed or logged (strategy-lab-v5
        # work order 4: emit armed-time/signal counts BEFORE reading P&L).
        logger.info('fires-check %s: %s', strategy_id, fires)

        twin_pfs, twin_debug = self._run_twins(
            panel, formation_log, exit_cfg, entry_mode, costers, twin_n,
            debug=debug_twins)

        return self._to_report(panel, strategy_id, selection, exit_cfg,
                               entry_mode, rebalance_every, params or {},
                               trades, fires, twin_pfs, costers,
                               include_trades=include_trades,
                               twin_debug=twin_debug)

    # -- twins ----------------------------------------------------------

    def _run_twins(self, panel: Panel, formation_log, exit_cfg: dict,
                   entry_mode: str, costers: dict, twin_seeds: int,
                   debug: bool = False) -> Tuple[List[float], Optional[dict]]:
        """TIME-MATCHED random twins (SPEC 5.8 non-negotiable). Each twin
        replays the strategy's own formation steps - identical clock slots
        and breadth - choosing names uniformly from that step's eligible set
        instead of by rank. Own RNG instance per seed; never touches global
        random state (same discipline as vectorized_harness._run_random_twin)."""
        if not formation_log:
            return [], ({'entry_ts': []} if debug else None)
        pfs: List[float] = []
        debug_ts: List[int] = []
        for seed in range(twin_seeds):
            rng = random.Random(seed)
            held: Dict[str, int] = {}
            pnls: List[float] = []
            for g, n_names, eligible in formation_log:
                key = int(panel.grid[g])
                free = [t for t in eligible if held.get(t, -1) < key]
                if not free:
                    continue
                for name in rng.sample(free, min(n_names, len(free))):
                    trade = self._open_trade(panel.series[name], g, entry_mode,
                                             exit_cfg, costers[name])
                    if trade is None:
                        continue
                    pnls.append(trade.pnl_net)
                    held[name] = int(panel.series[name].keys[trade.exit_idx])
                    if debug and seed == 0:
                        debug_ts.append(trade.entry_ts)
            if pnls:
                pfs.append(_pf(pnls))
        return pfs, ({'entry_ts': debug_ts} if debug else None)

    # -- reporting ------------------------------------------------------

    def _to_report(self, panel: Panel, strategy_id: str, selection: dict,
                   exit_cfg: dict, entry_mode: str, rebalance_every: int,
                   params: dict, trades: List[CSTrade], fires: dict,
                   twin_pfs: List[float], costers: dict,
                   include_trades: bool = False,
                   twin_debug: Optional[dict] = None) -> dict:
        pnls = [t.pnl_net for t in trades]
        pf = _pf(pnls)
        gross = _pf([t.pnl_gross for t in trades])
        wins = sum(1 for p in pnls if p > 0)
        total_car = sum(t.capital_at_risk for t in trades)

        # Twin percentile, exactly VResult.twin_percentile's convention.
        twin_pct = None
        if twin_pfs and trades:
            if pf == float('inf'):
                beaten = sum(1 for t in twin_pfs if t != float('inf'))
            else:
                beaten = sum(1 for t in twin_pfs if pf > t)
            twin_pct = beaten / len(twin_pfs)
        finite = sorted([t for t in twin_pfs if t != float('inf')])
        twin_median = None
        if twin_pfs:
            ordered = sorted(twin_pfs)
            twin_median = ordered[len(ordered) // 2] if ordered else None
            if twin_median == float('inf'):
                twin_median = finite[-1] if finite else None

        # Per-cell = per instrument. SPEC 5.8 point 6.
        by_ticker: Dict[str, List[CSTrade]] = {}
        for t in trades:
            by_ticker.setdefault(t.ticker, []).append(t)
        per_cell = []
        for name in sorted(by_ticker):
            ts_ = by_ticker[name]
            cell_pnls = [t.pnl_net for t in ts_]
            per_cell.append({
                'ticker': name,
                'asset_class': ts_[0].asset_class,
                'trades': len(ts_),
                'total_pnl_usd': round(sum(cell_pnls), 4),
                'pnl_per_trade': round(sum(cell_pnls) / len(ts_), 4),
                'win_rate': round(sum(1 for p in cell_pnls if p > 0) / len(ts_), 4),
            })

        # Time-based holdout (strategy-lab-v5 work order 3): calendar-half
        # split of the grid, trades bucketed by entry timestamp. Reported
        # for every run so a signal that lives only in one half is visible.
        halves = {'first_half': [], 'second_half': []}
        if panel.n_steps:
            # date_align keys are day-floored ms, plain keys are ms - both
            # are ms epochs, so the midpoint is comparable to entry_ts.
            mid = (int(panel.grid[0]) + int(panel.grid[-1])) // 2
            for t in trades:
                (halves['first_half'] if t.entry_ts <= mid
                 else halves['second_half']).append(t.pnl_net)
        time_split = {}
        for half, hp in halves.items():
            time_split[half] = {
                'trades': len(hp),
                'pnl_per_trade': round(sum(hp) / len(hp), 4) if hp else None,
            }

        versions = sorted({c.version for c in costers.values()})
        report = {
            'harness': 'cross_sectional',
            'cs_gate_version': CS_GATE_VERSION,
            'strategy': strategy_id,
            'params': dict(params, selection=selection, exit=exit_cfg,
                           entry_mode=entry_mode,
                           rebalance_every=rebalance_every),
            # NEVER pool across cost-model versions (standing rule 8 /
            # SPEC 5.9). 'MIXED' is a poison value that the uniformity flag
            # and assertions.py both catch.
            'cost_model_version': versions[0] if len(versions) == 1 else 'MIXED:' + '|'.join(versions),
            'cost_model_version_uniform': len(versions) == 1,
            'survivorship': SURVIVORSHIP_STAMP,
            'asset_classes': sorted({c.asset_class for c in costers.values()}),
            'universe_size': len(panel.tickers),
            'grid_steps': panel.n_steps,
            'fires_check': fires,
            'trades': len(trades),
            'wins': wins,
            'win_rate': round(wins / len(trades), 4) if trades else 0.0,
            'pf': None if pf == float('inf') else round(pf, 4),
            'gross_pf': None if gross == float('inf') else round(gross, 4),
            'total_pnl_usd': round(sum(pnls), 4),
            'pnl_per_trade': round(sum(pnls) / len(pnls), 4) if pnls else None,
            'return_pct_on_capital': (round(sum(pnls) / total_car * 100, 4)
                                      if total_car else None),
            'twin_median_pf': (None if twin_median in (None, float('inf'))
                               else round(twin_median, 4)),
            'twin_percentile': None if twin_pct is None else round(twin_pct, 3),
            'twin_sample_size': len(twin_pfs),
            'time_split': time_split,
            'per_cell': per_cell,
            'leave_one_out': leave_one_out(trades),
        }
        if include_trades:
            report['trades_detail'] = [vars(t) for t in trades]
        if twin_debug is not None:
            report['twin_debug'] = twin_debug
        return report


# ===========================================================================
# STRATEGY RANKERS
# ===========================================================================

def make_rev_ranker(days: int = 5) -> Callable[[PanelView], Dict[str, float]]:
    """Lab v5 P1 generic REV signal: score = trailing `days`-bar return over
    VISIBLE bars (the trade bar is structurally absent from the view). The
    run script selects the BOTTOM decile -> long. Pre-committed, deliberately
    generic; the pre-registration lives in run_horizon_ladder.py."""
    def ranker(view: PanelView) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for name in view._panel.tickers:
            h = view.history(name)
            if h is None or len(h['closes']) < days + 1:
                continue
            c = h['closes']
            if c[-days - 1] <= 0:
                continue
            out[name] = float(c[-1] / c[-days - 1] - 1.0)
        return out
    return ranker


def make_mom_ranker(days: int = 60, ma_period: int = 100
                    ) -> Callable[[PanelView], Dict[str, float]]:
    """Lab v5 P1 generic MOM signal: score = trailing 60-bar return; a name
    is only scored while its last visible close sits ABOVE its 100-bar MA
    (the pre-committed trend filter). Top decile -> long."""
    def ranker(view: PanelView) -> Dict[str, float]:
        out: Dict[str, float] = {}
        need = max(days + 1, ma_period)
        for name in view._panel.tickers:
            h = view.history(name)
            if h is None or len(h['closes']) < need:
                continue
            c = h['closes']
            if c[-days - 1] <= 0:
                continue
            if float(c[-1]) <= float(np.mean(c[-ma_period:])):
                continue
            out[name] = float(c[-1] / c[-days - 1] - 1.0)
        return out
    return ranker


def make_paid_liquidity_reversal_ranker(
        etf_of: Dict[str, str],
        vix_name: str = 'VIX',
        vix_lookback: int = 252,
        vix_pctile: float = 0.60,
        form_days: int = 5,
        market_fallback: str = 'SPY') -> Callable[[PanelView], Dict[str, float]]:
    """Lab v3 #5 "Paid Liquidity Reversal" (Nagel, RFS 2012; de Groot/Huij/
    Zhou on costs; references/strategy-lab-v3.md STRATEGY 5).

    Rank = 5-day RESIDUAL return: the stock's 5-day return minus its mapped
    sector ETF's 5-day return (a stock down WITH its sector is not
    dislocated; a stock down ALONE is a liquidity candidate). Select the
    BOTTOM QUINTILE -> long. The whole strategy is OFF unless VIX's last
    visible close is above its `vix_pctile` percentile of the trailing year
    (Nagel: you are selling liquidity - only sell it when it is expensive;
    unconditional weekly reversal is the mediocre version).

    VIX and the sector ETFs are CONTEXT SERIES - readable, never tradable -
    which is exactly the SPEC 5.8 second-gap capability this harness exists
    to provide (the time-series implementation in strategy_lab_v3.py had to
    ship with 'vix_conditioning: OMITTED_NO_SERIES').

    ==== PROMINENT DEVIATION FROM THE SOURCE DOC ====
    The doc's third filter - NEWS EXCLUSION (drop names with earnings inside
    the formation/holding window; the doc calls this decomposition "the
    single most important nuance in the strategy") - is NOT IMPLEMENTED.
    No earnings calendar exists in this environment. The volume-z>3 proxy
    alone was judged insufficient to claim the filter. Consequence: this
    implementation buys informed selling alongside liquidity selling, which
    the source doc predicts DEPRESSES the measured edge. A failure here does
    not refute Nagel's conditional claim; a success here is stronger than
    the doc requires. Stamped in run params as
    news_exclusion='OMITTED_NO_EARNINGS_CALENDAR'.
    =================================================

    Degrades to {} (strategy off) when VIX context is missing - rank code
    never raises.
    """
    def ranker(view: PanelView) -> Dict[str, float]:
        vh = view.history(vix_name)
        if vh is None or len(vh['closes']) < 60:
            return {}
        vc = vh['closes']
        cur = float(vc[-1])
        window = vc[-vix_lookback:]
        # Fraction of the trailing year strictly below today's VIX.
        pct = float(np.mean(window < cur))
        if pct < vix_pctile:
            return {}
        out: Dict[str, float] = {}
        for name in view._panel.tickers:
            h = view.history(name)
            if h is None or len(h['closes']) < form_days + 1:
                continue
            c = h['closes']
            if c[-form_days - 1] <= 0:
                continue
            r_stock = float(c[-1] / c[-form_days - 1] - 1.0)
            etf = etf_of.get(name, market_fallback)
            eh = view.history(etf)
            if eh is None or len(eh['closes']) < form_days + 1:
                # No benchmark visible -> cannot compute a residual; skip the
                # name rather than fake a raw-return rank.
                continue
            ec = eh['closes']
            if ec[-form_days - 1] <= 0:
                continue
            r_etf = float(ec[-1] / ec[-form_days - 1] - 1.0)
            out[name] = r_stock - r_etf
        return out
    return ranker


def make_same_clock_echo_ranker(panel: Panel,
                                slots_utc_minutes: Optional[List[int]] = None,
                                slots_ny: Tuple[str, ...] = ('09:30', '15:30'),
                                trailing_days: int = 20,
                                min_obs: int = 10
                                ) -> Callable[[PanelView], Dict[str, float]]:
    """Lab v3 #3 "Same-Clock Echo" (Heston, Korajczyk & Sadka, JF 2010;
    references/strategy-lab-v3.md STRATEGY 3).

    A stock's return in a half-hour slot predicts its return in the SAME
    slot on later days (institutions slicing the same parent orders through
    the same execution schedules). Rank each ticker at slot open by its mean
    same-slot open->close return over the trailing `trailing_days`
    observations; long the top decile during the slot only (entry slot open,
    exit slot close, never overnight). Per the genome, only the open and
    close half-hours (09:30, 15:30 ET) are traded initially - the paper
    finds the effect strongest there.

    IMPLEMENTATION NOTES
    - 30m bars are built from the *_15m files via aggregate_15m_to_30m (no
      *_30m data exists; 15m makes the strategy feasible, not infeasible).
    - Slot times are NEW YORK times. Bar timestamps are UTC and the UTC
      offset moves with DST, so slot membership is resolved through
      zoneinfo('America/New_York') - a fixed UTC minute would silently
      shift every trade by an hour half the year.
    - PRECOMPUTATION: this factory reads the panel's TIMESTAMP arrays up
      front to label each bar's NY minute-of-day. Timestamps are schedule
      information, not price information - knowing a future bar's clock time
      is knowing the exchange calendar, not the future price - so this does
      not breach the no-lookahead wall. All PRICE access goes through the
      view and is bounded by it: scores use only bars strictly before the
      decision bar (bar count k from the view slice).
    """
    from zoneinfo import ZoneInfo
    from datetime import datetime
    ny = ZoneInfo('America/New_York')
    slot_minutes = set()
    for hhmm in slots_ny:
        hh, mm = hhmm.split(':')
        slot_minutes.add(int(hh) * 60 + int(mm))
    if slots_utc_minutes:
        slot_minutes.update(slots_utc_minutes)

    # Per-ticker: NY minute-of-day per bar, and open->close return per bar.
    minute_of: Dict[str, np.ndarray] = {}
    bar_ret: Dict[str, np.ndarray] = {}
    for name, s in panel.series.items():
        minute_of[name] = np.array(
            [datetime.fromtimestamp(int(t) / 1000, ny).hour * 60
             + datetime.fromtimestamp(int(t) / 1000, ny).minute
             for t in s.ts], dtype=np.int32)
        with np.errstate(divide='ignore', invalid='ignore'):
            bar_ret[name] = np.where(s.opens > 0,
                                     s.closes / s.opens - 1.0, 0.0)
    # The grid's own NY minutes, so the ranker can gate by slot in O(1).
    grid_minute = {}
    for g, key in enumerate(panel.grid):
        dt = datetime.fromtimestamp(int(key) / 1000, ny)
        grid_minute[int(key)] = dt.hour * 60 + dt.minute

    def ranker(view: PanelView) -> Dict[str, float]:
        m = grid_minute.get(view.key)
        if m is None or m not in slot_minutes:
            return {}       # off-slot: strategy is flat by construction
        out: Dict[str, float] = {}
        for name in view._panel.tickers:
            k = view.bars_before(name)
            if k <= 0:
                continue
            # Same-slot bars among the VISIBLE k bars only.
            same = np.nonzero(minute_of[name][:k] == m)[0]
            if len(same) < min_obs:
                continue
            rets = bar_ret[name][same[-trailing_days:]]
            out[name] = float(np.mean(rets))
        return out
    return ranker


# ===========================================================================
# UNIVERSE / SECTOR HELPERS (shared by the run scripts)
# ===========================================================================

# Sector-name -> sector-ETF map for the residual ranking in Lab v3 #5.
# XLC (communication services) is not in the data store, so Telecom/Media
# falls back to the market proxy, as do the sector-less speculative names.
SECTOR_NAME_TO_ETF = {
    'Technology Mega Cap': 'XLK', 'Technology Mid/Large': 'XLK',
    'Semiconductors': 'XLK', 'Internet/E-commerce': 'XLY',
    'Financial Services': 'XLF', 'Energy': 'XLE', 'Healthcare': 'XLV',
    'Biotech (high vol)': 'XLV', 'Consumer Discretionary': 'XLY',
    'Consumer Staples': 'XLP', 'Industrials': 'XLI', 'Utilities': 'XLU',
    'Real Estate': 'XLRE', 'Materials': 'XLB', 'EV/Automotive': 'XLY',
}

# Universe-JSON sectors that are NOT single stocks (ETFs, indices, futures,
# crypto) - excluded from the PLR stock universe.
_NON_STOCK_SECTORS = {
    'Index ETFs', 'Leveraged ETFs', 'Sector ETFs', 'Commodity ETFs',
    'Bond ETFs', 'Volatility', 'Crypto (Yahoo)', 'Futures',
}


def load_universe() -> Dict[str, List[str]]:
    with open(ROOT / 'backtest' / 'ticker_universe.json') as f:
        return json.load(f)


def sector_maps() -> Tuple[Dict[str, str], Dict[str, str]]:
    """(ticker -> sector name, ticker -> sector ETF or 'SPY')."""
    universe = load_universe()
    sector_of, etf_of = {}, {}
    for sector, tickers in universe.items():
        for t in tickers:
            sector_of[t] = sector
            etf_of[t] = SECTOR_NAME_TO_ETF.get(sector, 'SPY')
    return sector_of, etf_of


def daily_tradable_tickers(data_dir: Optional[Path] = None) -> List[str]:
    """Every *_1d.csv ticker, minus:
    - VIX: an untradable index level (volume column is zero); it is a
      CONTEXT series here, never a position.
    - *_F futures: TradeCoster sizes them in whole contracts on margin, so
      their per-trade dollars are ~50x a $100 spot clip. Pooling those rows
      into a pooled pnl-per-trade would let one MES trade outvote fifty
      equity trades - the exact apples-to-oranges pooling instruments.py
      warns against. They stay out of the cross-sectional pool until a
      normalized (return-on-capital) pooling mode exists.
    """
    data_dir = Path(data_dir) if data_dir else DATA_DIR
    out = []
    for p in sorted(data_dir.glob('*_1d.csv')):
        t = p.name[:-len('_1d.csv')]
        if t == 'VIX' or t.endswith('_F'):
            continue
        out.append(t)
    return out


# ===========================================================================
# RUN HELPERS for Lab v3 #3 and #5 (smoke-scale entry points; the Horizon
# Ladder has its own dedicated script, backtest/run_horizon_ladder.py)
# ===========================================================================

PLR_SMOKE_TICKERS = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'GOOGL',
                     'JPM', 'XOM', 'UNH', 'TSLA']
ECHO_SMOKE_TICKERS = PLR_SMOKE_TICKERS


def run_paid_liquidity_reversal(tickers: Optional[List[str]] = None,
                                twin_seeds: Optional[int] = None,
                                use_cost_model: bool = True) -> dict:
    """Lab v3 #5 on daily bars. VIX-gated via context series; residual vs
    sector ETF context; bottom-quintile long; 5-day hold with a -2 ATR stop.

    DEVIATIONS (also stamped into params):
    - news exclusion OMITTED (no earnings calendar) - see ranker docstring.
    - the genome's third exit ("residual mean-reverts to > -0.25 sigma") is
      NOT implemented; exits are time (5 bars) or -2 ATR stop only. Keeping
      the exit set small keeps the exit machinery shared with every other
      strategy in this harness; the residual exit is a refinement, not the
      thesis.
    """
    sector_of, etf_of = sector_maps()
    if tickers is None:
        universe = load_universe()
        tickers = sorted({t for sector, ts in universe.items()
                          if sector not in _NON_STOCK_SECTORS for t in ts})
    context = sorted(set(etf_of.values()) | {'VIX', 'SPY'})
    panel = Panel.from_csv_dir(tickers, '1d',
                               context_tickers=context, date_align=True)
    harness = CrossSectionalHarness({'use_cost_model': use_cost_model})
    ranker = make_paid_liquidity_reversal_ranker(etf_of)
    # A real run needs a real cross-section behind its "quintile"; a smoke
    # universe (~10 names) only needs the machinery exercised.
    min_scored = 20 if len(panel.tickers) > 25 else 8
    return harness.run(
        panel, ranker, 'cs_paid_liquidity_reversal_v3_5',
        selection={'direction': 'bottom', 'mode': 'quintile',
                   'max_names': 5, 'min_scored': min_scored},
        exit_cfg={'type': 'time', 'bars': 5, 'stop_atr_mult': 2.0},
        entry_mode='close',
        rebalance_every=5,     # weekly-ish formation cadence per the genome
        min_history=60,
        sector_of=sector_of,
        twin_seeds=twin_seeds,
        params={'anchor': 'Nagel RFS 2012',
                'vix_gate': 'pctile>=0.60 of trailing 252d (context series)',
                'residual': '5d return minus mapped sector ETF 5d return',
                'news_exclusion': 'OMITTED_NO_EARNINGS_CALENDAR',
                'residual_reversion_exit': 'OMITTED_TIME_AND_STOP_ONLY'},
    )


def run_same_clock_echo(tickers: Optional[List[str]] = None,
                        twin_seeds: Optional[int] = None,
                        use_cost_model: bool = True) -> dict:
    """Lab v3 #3 on 30m bars aggregated from *_15m files. Rank at slot open
    by trailing-20-observation same-slot mean return; long the top decile
    (min 3 / max 5 names per the genome) for the slot only: entry at slot
    open, exit at slot close, never overnight."""
    sector_of, _ = sector_maps()
    if tickers is None:
        tickers = ECHO_SMOKE_TICKERS
    panel = Panel.from_csv_dir(tickers, '15m', to_30m=True, date_align=False)
    harness = CrossSectionalHarness({'use_cost_model': use_cost_model})
    ranker = make_same_clock_echo_ranker(panel)
    # Genome floor is 3-5 names per slot, which needs a >=30-name
    # cross-section for a decile to reach it. A smoke panel (~10 names) gets
    # min_names=1 purely to exercise the machinery - not a result.
    small = len(panel.tickers) <= 15
    selection = {'direction': 'top', 'mode': 'decile',
                 'min_names': 1 if small else 3, 'max_names': 5,
                 'min_scored': 5 if small else 8}
    return harness.run(
        panel, ranker, 'cs_same_clock_echo_v3_3',
        selection=selection,
        exit_cfg={'type': 'same_bar_close'},
        entry_mode='open',
        rebalance_every=1,     # ranker gates itself to the two slots
        min_history=200,       # ~ >20 sessions of 30m bars
        sector_of=sector_of,
        twin_seeds=twin_seeds,
        params={'anchor': 'Heston/Korajczyk/Sadka JF 2010',
                'slots_ny': ['09:30', '15:30'],
                'bars': '30m aggregated from 15m files',
                'trailing_days': 20},
    )


def _main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    ap = argparse.ArgumentParser(description='Cross-sectional harness runs '
                                 '(Lab v3 #3 echo / #5 plr)')
    ap.add_argument('--strategy', choices=['echo', 'plr'], required=True)
    ap.add_argument('--smoke', action='store_true',
                    help='10-ticker smoke run, reduced twin count')
    ap.add_argument('--tickers', default=None,
                    help='comma-separated override universe')
    ap.add_argument('--out', default=None, help='output JSON path')
    args = ap.parse_args()

    tickers = args.tickers.split(',') if args.tickers else None
    twin_seeds = 20 if args.smoke else None
    if args.smoke and tickers is None:
        tickers = PLR_SMOKE_TICKERS if args.strategy == 'plr' else ECHO_SMOKE_TICKERS
    if args.strategy == 'plr':
        report = run_paid_liquidity_reversal(tickers, twin_seeds=twin_seeds)
    else:
        report = run_same_clock_echo(tickers, twin_seeds=twin_seeds)

    out = args.out
    if out is None:
        mode = 'smoke' if args.smoke else 'full'
        out = str(ROOT / 'research' / 'cross_sectional'
                  / f'{args.strategy}_{mode}.json')
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w') as f:
        json.dump({'generated': time_mod.strftime('%Y-%m-%d %H:%M:%S'),
                   'report': report}, f, indent=1)
    slim = {k: v for k, v in report.items()
            if k not in ('per_cell', 'trades_detail')}
    print(json.dumps(slim, indent=1))
    print(f'saved: {out}')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(_main())
