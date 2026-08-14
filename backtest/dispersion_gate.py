"""Lab v5 P3 "DISPERSION GATE" - the derived entry gate, never scanned.

Source: references/strategy-lab-v5.md SS2 (The Toll Law) and SS3 P3.
Sequencing: docs/ROADMAP.md P2 ("P3 first" - cheapest powered test, reuses
everything already built).

THE LAW (v5 SS2, verbatim):
    "Cost is charged per round trip. Edge accrues per unit of time held.
     Expected favorable move scales with ATR_hold ~ ATR_bar x sqrt(bars held);
     a genuine signal captures some fraction kappa of it (realistic
     kappa ~ 0.05-0.20). Net per trade:  net = kappa . ATR_hold - c
     Minimum viable volatility-per-hold: ATR_hold >= c / kappa.
     This law is falsifiable, so it is Proposal 1 rather than an assumption."

THE GATE (v5 P3): entry allowed only when per-hold ATR >= c / kappa, with
kappa = 0.10 PRE-REGISTERED in the v5 doc. Both sides in the same units:
ATR_hold as a fraction of price (ATR14[bar] x sqrt(bars_in_hold) / close),
c in fractional terms (round_trip_bps / 10_000). The threshold is DERIVED
from the fee schedule before any result is read - standing rule 4
(conditions predicted, never discovered by scanning) satisfied by
construction. Nothing in this file fits anything to P&L.

REFINEMENT OVER THE v5 DOC (prominent, per the work order):
    The v5 doc derived the gate from a single flat c = 14bps ("with c = 14bps
    and kappa = 0.10, the gate is per-hold ATR >= 1.4%"). Since D-235 the
    toll c is PER ASSET CLASS from backtest/cost_model.py (venue-accurate,
    version-stamped): crypto ~12bps core / ~14bps other pairs, equity/ETF
    ~4.2bps (spread + statutory sell-side fees on a $100 clip), futures
    ~1.3bps of exposure on one micro contract. The derived gate is therefore
    SHARPER than the doc assumed: an equity entry needs only ~0.42% per-hold
    ATR, not 1.4%, while non-core crypto still needs 1.4%. Each series gets
    the c of ITS OWN instrument (same coster the harness charges it with),
    so gate and P&L can never disagree about the toll.

PRE-REGISTERED PREDICTIONS (v5 P3, verbatim):
    1. "edge vs. entry-time vol-decile, pooled across 180 tickers, is ~
       flat-negative through the middle deciles and turns positive only in
       the top decile-and-a-half" (the trend-state interaction the doc also
       mentions is NOT tested here - single-variable test, documented skip).
    2. (P3 thesis) "The conservative-gate +$0.094 on 116 trades is this law
       peeking through an underpowered sample" - i.e. gated entries clear
       the toll where ungated entries do not, and the effect survives the
       time-based holdout.

KILL CONDITIONS - stated BEFORE any result, standing rule 6:
    KILL 1 (v5 P3, verbatim): "monotone-flat edge across vol deciles pooled
        => dispersion conditioning is dead and SS4's 116 trades were the
        hammer's $1.48 in a costume."
    KILL 2 (v5 SS2's falsifiability clause applied to this gate): the law
        says net = kappa . ATR_hold - c, so trades passing ATR_hold >= c/kappa
        must show better net per-trade P&L than the ungated pool. If gated
        per-trade net on the SECOND-HALF holdout is not better than ungated
        (for the non-control entries), the derived gate selects nothing and
        the Toll-Law gate at kappa = 0.10 is dead on this universe.

POWER (standing rule 7): the bar for +/-$0.09 edges is 4,000-8,700 pooled
trades (graveyard package SS4). A result under the bar is a SHRUG, not a
verdict; the report prints which one it is.

DESIGN CALLS (documented per the work order):
    - Entries: the three P3 names resolved to registered strategies:
        grid_2.0atr        -> strategies/builtin/expanded.py GridStrategy(2.0)   (exact name)
        stoch_rsi_oversold -> strategies/builtin/expanded.py StochRsiOversold()  (exact name)
        dca (control)      -> strategies/builtin/expanded.py DCAStrategy(7) = "dca_7"
      P3 says "dca"; the registry has dca_7 and dca_14. dca_7 is chosen: P3
      asks for the highest-frequency entries and dca_7 fires twice as often.
      dca is is_benchmark - its arm is a CONTROL, never a discovery.
    - Exits: TIME-BASED ONLY (time_4c/time_8c/time_16c), so bars_in_hold is
      exact by construction (4/8/16) and no median-realized-hold estimation
      is needed. R-based exits are excluded - the simpler, cleaner option
      the work order allows.
    - The gate is evaluated at the SIGNAL bar using only trailing data
      (ATR14[i] is computed from bars <= i; the threshold is a constant from
      the fee schedule). All three entries are market orders (entry ~ close
      of the signal bar), so signal bar == fill bar for every trade here.
    - Confirmation stack OFF for BOTH arms (matches constraint_sweep's
      AGGRESSIVE level): the dispersion gate is the ONLY variable between
      gated and ungated. Also maximizes pooled N (power).
    - TIME-BASED HOLDOUT (v5 work order 3): each series splits at its
      calendar midpoint timestamp. The threshold is derived, not fitted, so
      BOTH halves are honest; the doc's judgment is on the second half.
      Trades are assigned to a half by ENTRY timestamp.
    - Vol decile: ATR14/close percentile within the series' OWN history up
      to that bar (expanding window, >= 50 observations before a decile is
      assigned; earlier trades are excluded from the decile table and
      counted). Pre-entry data only.
    - Leave-one-asset-out on the pooled result, same underlying-grouping
      pattern as backtest/asset_class_analysis.py.
    - CROSS-CLASS DOLLAR POOLING IS SPOT-ONLY (CRYPTO/EQUITY/ETF). Futures
      PnL is contract dollars on ~$34k of exposure while spot trades are
      $100 clips - different denominators, and ROADMAP P0.4 forbids pooling
      them. Futures series still run (gated + ungated, per-class tables and
      fires-check) but are excluded from the pooled tables, the decile
      table, the leave-one-out and the power count. The first smoke run
      demonstrated why: with futures pooled, leave-one-out flagged ES as a
      one-asset costume on every strategy - a denominator artifact.
    - Standing rule 8: single cost_model_version across the whole run,
      asserted; version + kappa + per-class c stamped in the output JSON.

Usage:
    python3 backtest/dispersion_gate.py                    # full sweep (all 1d + 1h series)
    python3 backtest/dispersion_gate.py --smoke            # 8-ticker end-to-end proof
    python3 backtest/dispersion_gate.py --limit N          # first N discovered tickers
    python3 backtest/dispersion_gate.py --out PATH.json    # override output path
"""
import bisect
import collections
import json
import logging
import math
import os
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import yaml

from backtest.cost_model import COST_MODEL_VERSION
from backtest.data_loader import load_csv
from backtest.vectorized_harness import (GATE_VERSION, VectorizedBacktestHarness,
                                         precompute_indicators)
# Reuse the graveyard's discovery machinery verbatim so the universe is the
# same one every other experiment sees (SKIP_FILES, Binance merging, sector
# tags from backtest/ticker_universe.json).
from backtest.run_incremental_graveyard import (BINANCE_PAIRS,
                                                discover_yf_tickers,
                                                load_binance_merged)
from strategies.builtin.expanded import (DCAStrategy, GridStrategy,
                                         StochRsiOversold)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'backtest', 'data')
DEFAULT_OUT = os.path.join(ROOT, 'research', 'graveyard', 'dispersion_gate.json')
SMOKE_OUT = os.path.join(ROOT, 'research', 'graveyard', 'dispersion_gate_smoke.json')

# --- Pre-registered constants. Do not tune. -------------------------------
KAPPA = 0.10                       # v5 SS2/P3, pre-registered capture fraction
EXIT_HOLDS = {'time_4c': 4, 'time_8c': 8, 'time_16c': 16}   # exact bars_in_hold
TIMEFRAMES = ('1d', '1h')          # the work order's universe
MIN_CANDLES = 400                  # same floor as constraint_sweep
MIN_DECILE_HISTORY = 50            # observations before a decile is assigned
POWER_BAR = (4000, 8700)           # graveyard SS4 bar for +/-$0.09 edges

# Classes whose dollar PnL shares a denominator (a $100 notional clip) and
# may therefore be pooled. FUTURES trade one whole micro contract (~$34k
# exposure, PnL in contract dollars) - pooling them with spot is the
# denominator mismatch ROADMAP P0.4 forbids. They run and report per-class.
SPOT_CLASSES = ('CRYPTO', 'EQUITY', 'ETF')

# P3's three entries. dca_7 is the control (is_benchmark on the class).
STRATEGY_MAPPING = {
    'grid_2.0atr': 'GridStrategy(2.0)  [exact name match]',
    'stoch_rsi_oversold': 'StochRsiOversold()  [exact name match]',
    'dca_7': 'DCAStrategy(7)  [P3 says "dca"; dca_7 chosen over dca_14: '
             'higher frequency. CONTROL, is_benchmark]',
}
CONTROL_STRATEGIES = {'dca_7'}

# 8 tickers spanning EQUITY / ETF / CRYPTO / FUTURES for the smoke run.
SMOKE_TICKERS = ['AAPL', 'MSFT', 'SPY', 'QQQ', 'GLD', 'BTC_USD', 'ETH_USD', 'ES_F']


def build_strategies():
    """Fresh instances (DCA keeps no state, but cheap insurance)."""
    return [GridStrategy(2.0), StochRsiOversold(), DCAStrategy(7)]


# ==========================================================================
# GATE ARITHMETIC (pure functions - unit-tested in tests/test_dispersion_gate.py)
# ==========================================================================

def series_cost_fraction(coster) -> float:
    """The toll c for THIS instrument, as a fraction of exposure.

    Mirrors TradeCoster.describe()['round_trip_bps_of_exposure'] without the
    display rounding: round-trip dollar fees at the reference price plus
    slippage/spread on both legs, over exposure. Using the SAME coster the
    harness charges guarantees the gate and the P&L agree about the toll.
    """
    px = coster.reference_price
    qty = coster.size(px)
    exposure = coster.exposure(px, qty)
    if exposure <= 0:
        return float('inf')
    fee = coster.round_trip_fee(px, px, qty)
    return (fee + 2.0 * coster.slip_rate * exposure) / exposure


def gate_threshold_frac(cost_frac: float, kappa: float = KAPPA) -> float:
    """ATR_hold >= c / kappa (v5 SS2). Both sides fractional-of-price."""
    return cost_frac / kappa


def atr_hold_frac(atr14: np.ndarray, closes: np.ndarray,
                  hold_bars: int) -> np.ndarray:
    """Per-hold ATR as a fraction of price: ATR14[i] x sqrt(H) / close[i].

    ATR14[i] is Wilder ATR over bars <= i - trailing data only. sqrt scaling
    per v5 SS2 ("ATR_hold ~ ATR_bar x sqrt(bars held)").
    """
    with np.errstate(divide='ignore', invalid='ignore'):
        frac = np.where(closes > 0, atr14 * math.sqrt(hold_bars) / closes, 0.0)
    return np.nan_to_num(frac, nan=0.0, posinf=0.0, neginf=0.0)


def gate_mask(atr14: np.ndarray, closes: np.ndarray, hold_bars: int,
              threshold_frac: float) -> np.ndarray:
    """Boolean per bar: may an entry fire here? Undefined ATR gates OUT
    (conservative: a bar whose volatility we cannot measure is not proven
    to clear the toll)."""
    return atr_hold_frac(atr14, closes, hold_bars) >= threshold_frac


def mask_signals(signals: List, mask: np.ndarray) -> List:
    """Gated signal list for run_strategy(precomputed_signals=...): a signal
    at a gated-out bar becomes None, exactly as if the strategy never fired."""
    return [s if mask[i] else None for i, s in enumerate(signals)]


def expanding_vol_deciles(atr14: np.ndarray, closes: np.ndarray,
                          start: int = 20,
                          min_history: int = MIN_DECILE_HISTORY) -> np.ndarray:
    """Entry-time vol decile: percentile of ATR14/close within THIS series'
    own history up to and including bar i (expanding window - no future
    data). -1 until `min_history` observations exist; those bars' trades are
    excluded from the decile table and counted as excluded.

    P3 pre-registers the SHAPE across these deciles: flat-negative middles,
    positive only in the top decile-and-a-half.
    """
    n = len(closes)
    out = np.full(n, -1, dtype=int)
    hist: List[float] = []
    for i in range(start, n):
        c = closes[i]
        a = atr14[i]
        if not (np.isfinite(a) and np.isfinite(c)) or c <= 0 or a <= 0:
            continue
        x = a / c
        bisect.insort(hist, x)
        if len(hist) >= min_history:
            pct = bisect.bisect_right(hist, x) / len(hist)   # (0, 1]
            out[i] = min(9, int(pct * 10))
    return out


def calendar_midpoint_ts(timestamps: np.ndarray) -> int:
    """v5 work order 3: split each series at its CALENDAR midpoint (mean of
    first and last timestamp), not the bar-count midpoint - gaps in trading
    hours would otherwise skew the halves. Deterministic by construction."""
    return int((int(timestamps[0]) + int(timestamps[-1])) // 2)


def trade_half(entry_ts: int, mid_ts: int) -> str:
    """H1 = first calendar half, H2 = the holdout the doc judges on."""
    return 'H1' if entry_ts < mid_ts else 'H2'


def underlying(ticker: str) -> str:
    """Same grouping as asset_class_analysis.py: BTC/USDT and BTC_USD are
    one asset for leave-one-out purposes."""
    return (ticker or '').split('/')[0].replace('_USD', '').replace('_F', '')


# ==========================================================================
# SERIES DISCOVERY (graveyard-identical universe, restricted to 1d + 1h)
# ==========================================================================

def discover_series(limit: Optional[int] = None,
                    smoke: bool = False) -> List[Tuple[str, str, str, list]]:
    """(ticker, timeframe, sector, candles) for every usable 1d/1h series.

    Mirrors run_incremental_graveyard.main(): Binance merged pairs (1h only
    here), then discover_yf_tickers() with SKIP_FILES already applied inside
    it, sector tags from backtest/ticker_universe.json.
    """
    universe_path = os.path.join(ROOT, 'backtest', 'ticker_universe.json')
    with open(universe_path) as f:
        universe = json.load(f)
    ticker_sector = {}
    for sector, tickers in universe.items():
        for t in tickers:
            safe = t.replace('^', '').replace('=', '_').replace('-', '_')
            ticker_sector[safe] = sector

    out = []
    if not smoke:
        # Binance merged pairs, 1h (the 15m files are out of this universe).
        for pair in BINANCE_PAIRS:
            display = f'{pair[:3]}/USDT'
            candles = load_binance_merged(pair, '1h')
            if len(candles) >= MIN_CANDLES:
                out.append((display, '1h', 'Crypto', candles))

    yf = SMOKE_TICKERS if smoke else discover_yf_tickers()
    if limit:
        yf = yf[:limit]
    for ticker in yf:
        sector = ticker_sector.get(ticker, 'Unknown')
        for tf in TIMEFRAMES:
            path = os.path.join(DATA_DIR, f'{ticker}_{tf}.csv')
            if not os.path.exists(path):
                continue
            candles = load_csv(path)
            if len(candles) >= MIN_CANDLES:
                out.append((ticker, tf, sector, candles))
    return out


# ==========================================================================
# MAIN SWEEP
# ==========================================================================

def run(series_limit: Optional[int] = None, smoke: bool = False,
        out_path: Optional[str] = None) -> dict:
    with open(os.path.join(ROOT, 'config.yaml')) as f:
        config = yaml.safe_load(f)
    # Venue-accurate per-class costs (D-235) - the whole point of the
    # per-class refinement. The gate threshold comes from the SAME coster.
    config['use_cost_model'] = True
    # Confirmation stack OFF for both arms: the dispersion gate is the only
    # variable (constraint_sweep's AGGRESSIVE level).
    conf = config.setdefault('strategy', {}).setdefault('confirmation', {})
    conf['apply_confirmation_stack'] = False
    harness = VectorizedBacktestHarness(config)

    strategies = build_strategies()
    series = discover_series(limit=series_limit, smoke=smoke)
    if not series:
        raise SystemExit('no usable series found')

    print(f'DISPERSION GATE (Lab v5 P3): {len(strategies)} entries x '
          f'{len(series)} series x {len(EXIT_HOLDS)} time exits x '
          f'{{ungated, gated}}   kappa={KAPPA}  cost_model={COST_MODEL_VERSION}')

    # Aggregators. No per-trade list is kept - full-run memory stays flat.
    #   pnl_agg[(strategy, exit, arm, cls, half)]      -> [trades, pnl]
    #   decile_agg[(strategy, exit, decile)]           -> [trades, pnl]   (ungated arm)
    #   loo_agg[(strategy, arm, underlying)]           -> [trades, pnl]
    #   fires[(strategy, exit, cls)] -> {candidates, gated_out, surviving}
    pnl_agg = collections.defaultdict(lambda: [0, 0.0])
    decile_agg = collections.defaultdict(lambda: [0, 0.0])
    loo_agg = collections.defaultdict(lambda: [0, 0.0])
    fires = collections.defaultdict(lambda: {'candidate_entries': 0,
                                             'gated_out': 0,
                                             'surviving_trades': 0})
    decile_excluded = 0          # trades at bars with < MIN_DECILE_HISTORY obs
    per_series_thresholds = []
    class_c_bps = collections.defaultdict(list)
    versions = set()

    for s_idx, (ticker, tf, sector, candles) in enumerate(series):
        ind = precompute_indicators(candles)
        # Same private resolver run_strategy itself uses, so the c in the
        # gate is byte-identical to the c in the P&L.
        coster = harness._coster(ticker, ind, sector=sector)
        assert not str(coster.version).startswith('flat'), \
            'dispersion gate requires the venue-accurate cost model'
        cls = coster.asset_class
        c_frac = series_cost_fraction(coster)
        thr = gate_threshold_frac(c_frac)
        class_c_bps[cls].append(c_frac * 10_000)
        per_series_thresholds.append({
            'ticker': ticker, 'timeframe': tf, 'asset_class': cls,
            'c_bps': round(c_frac * 10_000, 3),
            'atr_hold_threshold_pct': round(thr * 100, 4),
        })

        deciles = expanding_vol_deciles(ind.atr14, ind.closes)
        mid_ts = calendar_midpoint_ts(ind.timestamps)
        masks = {ex: gate_mask(ind.atr14, ind.closes, hold, thr)
                 for ex, hold in EXIT_HOLDS.items()}

        for strat in strategies:
            sigs = harness.scan_all_bars(strat, ind)
            candidate_idx = [i for i, s in enumerate(sigs)
                             if s is not None and s.direction == 'bullish'
                             and s.entry is not None and s.stop is not None]
            for ex in EXIT_HOLDS:
                mask = masks[ex]
                fc = fires[(strat.name, ex, cls)]
                fc['candidate_entries'] += len(candidate_idx)
                fc['gated_out'] += sum(1 for i in candidate_idx if not mask[i])
                for arm, arm_sigs in (('ungated', sigs),
                                      ('gated', mask_signals(sigs, mask))):
                    r = harness.run_strategy(strat, ind, ticker, tf, ex,
                                             precomputed_signals=arm_sigs,
                                             sector=sector)
                    versions.add(r.cost_model_version)
                    if arm == 'gated':
                        fc['surviving_trades'] += r.trade_count
                    is_spot = cls in SPOT_CLASSES
                    for t in r.trades:
                        half = trade_half(int(t.entry_ts), mid_ts)
                        cell = pnl_agg[(strat.name, ex, arm, cls, half)]
                        cell[0] += 1
                        cell[1] += t.pnl_net
                        if not is_spot:
                            continue   # contract dollars never pool with spot
                        lcell = loo_agg[(strat.name, arm, underlying(ticker))]
                        lcell[0] += 1
                        lcell[1] += t.pnl_net
                        if arm == 'ungated':
                            d = int(deciles[t.entry_idx])
                            if d < 0:
                                decile_excluded += 1
                            else:
                                dc = decile_agg[(strat.name, ex, d)]
                                dc[0] += 1
                                dc[1] += t.pnl_net
        print(f'  [{s_idx + 1}/{len(series)}] {ticker} {tf} [{cls}] '
              f'c={c_frac * 10_000:.2f}bps thr={thr * 100:.3f}%', flush=True)

    # Standing rule 8: one cost-model version across everything pooled here.
    assert len(versions) == 1, f'mixed cost model versions: {versions}'

    # ---- DERIVED THRESHOLDS + FIRES-CHECK, printed BEFORE any P&L --------
    print('\nDERIVED PER-CLASS THRESHOLDS (c from cost_model.py, kappa='
          f'{KAPPA}; the v5 doc assumed a flat 14bps -> 1.4%)')
    print(f"{'class':<10s}{'c bps min':>10s}{'c bps max':>10s}"
          f"{'thr% min':>10s}{'thr% max':>10s}{'series':>8s}")
    class_c_summary = {}
    for cls in sorted(class_c_bps):
        vals = class_c_bps[cls]
        class_c_summary[cls] = {
            'series': len(vals),
            'c_bps_min': round(min(vals), 3), 'c_bps_max': round(max(vals), 3),
            'c_bps_mean': round(sum(vals) / len(vals), 3),
            'atr_hold_threshold_pct_min': round(min(vals) / 10_000 / KAPPA * 100, 4),
            'atr_hold_threshold_pct_max': round(max(vals) / 10_000 / KAPPA * 100, 4),
        }
        cc = class_c_summary[cls]
        print(f"{cls:<10s}{cc['c_bps_min']:>10.2f}{cc['c_bps_max']:>10.2f}"
              f"{cc['atr_hold_threshold_pct_min']:>10.3f}"
              f"{cc['atr_hold_threshold_pct_max']:>10.3f}{cc['series']:>8d}")

    print('\nFIRES-CHECK (before P&L, v5 work order 4 / graveyard SS3.2)')
    print(f"{'strategy':<20s}{'exit':<10s}{'class':<9s}{'candidates':>11s}"
          f"{'gated out':>10s}{'%':>7s}{'surviving':>10s}")
    fires_check = []
    for (name, ex, cls), fc in sorted(fires.items()):
        n_cand = fc['candidate_entries']
        pct = fc['gated_out'] / n_cand * 100 if n_cand else 0.0
        fires_check.append({'strategy': name, 'exit': ex, 'asset_class': cls,
                            'candidate_entries': n_cand,
                            'gated_out': fc['gated_out'],
                            'gated_out_pct': round(pct, 2),
                            'surviving_gated_trades': fc['surviving_trades']})
        print(f"{name:<20s}{ex:<10s}{cls:<9s}{n_cand:>11,}"
              f"{fc['gated_out']:>10,}{pct:>6.1f}%{fc['surviving_trades']:>10,}")

    # ---- P&L TABLES (only after the fires-check is on the record) --------
    def cell(name, ex, arm, half=None):
        """Pooled [n, pnl] across SPOT classes only (and halves when half is
        None). Futures are per-class-table only - ROADMAP P0.4."""
        n, p = 0, 0.0
        for (s, e, a, c, h), (cn, cp) in pnl_agg.items():
            if (s == name and e == ex and a == arm and c in SPOT_CLASSES
                    and (half is None or h == half)):
                n += cn
                p += cp
        return n, p

    strategy_names = [s.name for s in strategies]
    results_pooled = {}
    print('\nGATED vs UNGATED, net $/trade (pooled across SPOT classes '
          'only - futures stay per-class, ROADMAP P0.4; judgment is on H2 - '
          'v5 work order 3)')
    print(f"{'strategy':<20s}{'exit':<10s}{'arm':<9s}{'n(H1)':>8s}{'$/t H1':>9s}"
          f"{'n(H2)':>8s}{'$/t H2':>9s}{'n(pool)':>9s}{'$/t pool':>10s}")
    for name in strategy_names:
        for ex in EXIT_HOLDS:
            for arm in ('ungated', 'gated'):
                n1, p1 = cell(name, ex, arm, 'H1')
                n2, p2 = cell(name, ex, arm, 'H2')
                n, p = n1 + n2, p1 + p2
                results_pooled[f'{name}|{ex}|{arm}'] = {
                    'H1': {'trades': n1, 'pnl_usd': round(p1, 4),
                           'pnl_per_trade': round(p1 / n1, 6) if n1 else None},
                    'H2': {'trades': n2, 'pnl_usd': round(p2, 4),
                           'pnl_per_trade': round(p2 / n2, 6) if n2 else None},
                    'pooled': {'trades': n, 'pnl_usd': round(p, 4),
                               'pnl_per_trade': round(p / n, 6) if n else None},
                }
                print(f"{name:<20s}{ex:<10s}{arm:<9s}"
                      f"{n1:>8,}{(p1 / n1 if n1 else 0):>9.4f}"
                      f"{n2:>8,}{(p2 / n2 if n2 else 0):>9.4f}"
                      f"{n:>9,}{(p / n if n else 0):>10.4f}")

    # Per-class gated/ungated split (the per-class c makes this the honest view).
    results_by_class = {}
    for (name, ex, arm, cls, half), (n, p) in pnl_agg.items():
        key = f'{name}|{arm}|{cls}'
        d = results_by_class.setdefault(key, {'trades': 0, 'pnl_usd': 0.0})
        d['trades'] += n
        d['pnl_usd'] = round(d['pnl_usd'] + p, 4)
    for d in results_by_class.values():
        d['pnl_per_trade'] = (round(d['pnl_usd'] / d['trades'], 6)
                              if d['trades'] else None)

    # ---- VOL-DECILE TABLE (P3's pre-registered shape) --------------------
    print('\nUNGATED trades by entry-time vol decile (SPOT classes, pooled '
          'across tickers and exits; P3 predicts flat-negative middles, '
          'positive only in the top decile-and-a-half)')
    print(f"{'strategy':<20s}" + ''.join(f'D{d:>1d}{"":>6s}' for d in range(10)))
    vol_deciles = {}
    for name in strategy_names:
        row = {}
        line = f'{name:<20s}'
        for d in range(10):
            n = sum(v[0] for (s, e, dd), v in decile_agg.items()
                    if s == name and dd == d)
            p = sum(v[1] for (s, e, dd), v in decile_agg.items()
                    if s == name and dd == d)
            row[f'decile_{d}'] = {'trades': n, 'pnl_usd': round(p, 4),
                                  'pnl_per_trade': round(p / n, 6) if n else None}
            line += f'{(p / n if n else 0):>8.3f}'
        vol_deciles[name] = row
        print(line)
    print(f'(decile-excluded trades - fewer than {MIN_DECILE_HISTORY} history '
          f'observations at entry: {decile_excluded:,})')

    # ---- LEAVE-ONE-ASSET-OUT on the pooled gated result ------------------
    # Same worst-without pattern as asset_class_analysis.py: a pooled number
    # carried by one underlying is that underlying wearing a costume.
    loo = {}
    for name in strategy_names:
        for arm in ('ungated', 'gated'):
            tot_n = sum(v[0] for (s, a, u), v in loo_agg.items()
                        if s == name and a == arm)
            tot_p = sum(v[1] for (s, a, u), v in loo_agg.items()
                        if s == name and a == arm)
            worst = None
            for (s, a, u), (an, ap) in loo_agg.items():
                if s != name or a != arm:
                    continue
                rem_n = tot_n - an
                if rem_n <= 0:
                    continue
                rem_ppt = (tot_p - ap) / rem_n
                if worst is None or rem_ppt < worst[1]:
                    worst = (u, rem_ppt)
            ppt = tot_p / tot_n if tot_n else None
            loo[f'{name}|{arm}'] = {
                'trades': tot_n,
                'pnl_per_trade': round(ppt, 6) if ppt is not None else None,
                'worst_drop_asset': worst[0] if worst else None,
                'pnl_per_trade_without_it': round(worst[1], 6) if worst else None,
                'carried_by_one_asset': bool(
                    worst and ppt is not None and ppt - worst[1] > 0.15),
            }
    print('\nLEAVE-ONE-ASSET-OUT (gated arm, SPOT classes)')
    for name in strategy_names:
        r = loo[f'{name}|gated']
        print(f"  {name:<20s} {r['trades']:>8,} trades  "
              f"{(r['pnl_per_trade'] if r['pnl_per_trade'] is not None else 0):>+9.4f}/t  "
              f"worst without {r['worst_drop_asset']}: "
              f"{(r['pnl_per_trade_without_it'] if r['pnl_per_trade_without_it'] is not None else 0):>+9.4f}"
              f"{'  << ONE-ASSET COSTUME' if r['carried_by_one_asset'] else ''}")

    # ---- POWER (standing rule 7: verdict or shrug, say which) ------------
    signal_gated_n = sum(
        results_pooled[f'{name}|{ex}|gated']['pooled']['trades']
        for name in strategy_names if name not in CONTROL_STRATEGIES
        for ex in EXIT_HOLDS)
    power_status = ('VERDICT-CAPABLE' if signal_gated_n >= POWER_BAR[0]
                    else 'SHRUG (under-powered)')
    print(f'\nPOWER: {signal_gated_n:,} pooled gated SPOT trades on the '
          f'non-control entries vs the {POWER_BAR[0]:,}-{POWER_BAR[1]:,} bar '
          f'for +/-$0.09 edges -> {power_status}')

    # ---- OUTPUT (fires_check precedes every P&L field, by construction) --
    out = {
        'experiment': 'dispersion_gate (Lab v5 P3)',
        'source_doc': 'references/strategy-lab-v5.md SS2 (Toll Law), SS3 P3',
        'cost_model_version': COST_MODEL_VERSION,
        'gate_version': GATE_VERSION,
        'kappa': KAPPA,
        'per_class_c_refinement': (
            'v5 doc assumed flat c=14bps -> 1.4%% gate; this run derives c PER '
            'ASSET CLASS from backtest/cost_model.py, so the gate is sharper: '
            'see per_class_c_bps.'),
        'per_class_c_bps': class_c_summary,
        'strategy_mapping': STRATEGY_MAPPING,
        'control_strategies': sorted(CONTROL_STRATEGIES),
        'exit_holds': EXIT_HOLDS,
        'confirmation_stack': 'OFF for both arms (gate is the only variable)',
        'pooling': ('cross-class dollar pooling is SPOT-only '
                    '(CRYPTO/EQUITY/ETF); FUTURES PnL is contract dollars on '
                    '~$34k exposure vs $100 spot clips (ROADMAP P0.4) and '
                    'appears only in fires_check and results.by_class'),
        'holdout': 'calendar-midpoint split per series; judgment on H2',
        'kill_conditions': [
            'KILL 1 (P3 verbatim): monotone-flat edge across vol deciles '
            "pooled => dispersion conditioning is dead and SS4's 116 trades "
            "were the hammer's $1.48 in a costume.",
            'KILL 2 (SS2 falsifiability): gated per-trade net on the H2 '
            'holdout not better than ungated for the non-control entries => '
            'the derived gate selects nothing; Toll-Law gate at kappa=0.10 '
            'dead on this universe.',
        ],
        'pre_registered_predictions': [
            'Edge vs entry-time vol-decile pooled: flat-negative through the '
            'middle deciles, positive only in the top decile-and-a-half.',
            'Gated entries clear the toll where ungated do not; effect '
            'survives the time-based holdout (judged on H2).',
        ],
        'universe': {'series': len(series), 'timeframes': list(TIMEFRAMES),
                     'smoke': smoke, 'min_candles': MIN_CANDLES},
        'per_series_thresholds': per_series_thresholds,
        'fires_check': fires_check,
        'decile_excluded_trades': decile_excluded,
        'results': {
            'pooled_gated_vs_ungated': results_pooled,
            'by_class': results_by_class,
            'vol_deciles_ungated': vol_deciles,
            'leave_one_asset_out': loo,
            'power': {'bar_for_009_edges': list(POWER_BAR),
                      'pooled_gated_trades_noncontrol': signal_gated_n,
                      'status': power_status},
        },
    }
    path = out_path or (SMOKE_OUT if smoke else DEFAULT_OUT)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(out, f, indent=1)
    print(f'\nsaved: {path}')
    return out


def main():
    smoke = '--smoke' in sys.argv
    limit = None
    if '--limit' in sys.argv:
        limit = int(sys.argv[sys.argv.index('--limit') + 1])
    out_path = None
    if '--out' in sys.argv:
        out_path = sys.argv[sys.argv.index('--out') + 1]
    run(series_limit=limit, smoke=smoke, out_path=out_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
