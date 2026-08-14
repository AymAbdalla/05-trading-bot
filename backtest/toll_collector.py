"""Lab v5 P2 "TOLL COLLECTOR" — maker-only passive reversion experiment.

Source of requirements: references/strategy-lab-v5.md, proposal P2.
Fill-model mandate: SPEC.md 5.9 ("Maker fills simulated conservatively:
filled only when the bar trades THROUGH the resting limit, never on a touch").
Cost model: backtest/cost_model.py (crypto maker 0%, taker 0.02%/0.01% core).

SELF-CONTAINED by design: this module shares NO simulation code with
backtest/harness.py or backtest/vectorized_harness.py. The maker-fill rule is
a harness EXTENSION the shared harnesses do not have, and P2 calls it "the
load-bearing wall; an optimistic fill model here would be self-deception of
exactly the §3.6 class." Keeping it here avoids destabilizing the referee'd
harness pair mid-graveyard-run. The Binance CSV loader is copied (not
imported) from run_incremental_graveyard.py for the same decoupling reason.

PRE-REGISTERED PREDICTION (strategy-lab-v5.md P2, verbatim):
    "Pre-registered prediction: maker-filled trades show positive net edge
    concentrated in the top vol quintile; calm-quintile fills ≈ 0."
    (Note: the arming gate only trades above the 70th vol percentile, so
    "calm-quintile fills" are structurally absent here; the testable half is
    the concentration claim, read as: fills arming above the 90th percentile
    outperform fills arming in the 70th-90th band.)

KILL CONDITION (strategy-lab-v5.md P2, verbatim):
    "Kill condition: if maker fills underperform the equivalent taker-at-touch
    trades by MORE than the fee+slippage savings (~14-30bps), adverse
    selection eats the discount and passive execution is dead as an edge
    source."
    Operationalized: kill fires if pooled maker NET bps/trade < pooled taker
    NET bps/trade (taker net already carries fee+slippage, maker carries ~0,
    so "gross underperformance > savings" and "net < net" are the same test).

DESIGN (every knob pre-committed here, none tuned on results):
  Entry   : resting limit BUY at L = last close - k*ATR14, k in {1.5, 2.0, 2.5}.
  Arming  : 1h realized vol (std of last 24 hourly log returns) > 70th
            percentile of the trailing 30 days (720 bars) of that vol series,
            percentile computed from data STRICTLY BEFORE the current bar
            (window [i-720, i-1]; including bar i would leak the decision
            bar into its own threshold).
  Lifetime: order rests while armed, RE-PRICED at every bar close to the new
            close - k*ATR (P2 anchors on "last close", so the anchor moves),
            CANCELLED the bar the vol condition lapses. Calm-market passivity
            is uncompensated per P2, so no order rests in calm regimes.
  Exit    : resting limit SELL at T = L + m*ATR with m = 1.0 (maker both
            ways). m=1.0 chosen because P2's gross estimate is "20-60bps over
            hours-to-days" after >=2*ATR flushes; one ATR is the smallest
            round target inside that band and keeps hold times in the
            hours-to-days lane the Toll Law demands. Not swept, not tuned.
  Stop    : taker stop at S = L - 1.5*ATR, strictly below entry (rule 7 /
            standing rule 6). 1.5 > m so the stop is the minority outcome by
            construction, matching P2's "expected to fire on a minority of
            trades". Pays taker fee + slippage per the cost model.
  Backstop: time exit after 72 bars (3 days) at close, taker. Without it a
            position could ride to end-of-data, which is neither maker
            reversion nor a stop - it is an unmodeled hold.
  Sizing  : $100 fixed notional (SPEC 6.1), one position at a time per
            (pair, k). ATR/stop/target are anchored to the ATR of the bar
            whose close priced the filled order, so entry, target and stop
            share one volatility unit.

FILL CONVENTIONS (all deliberately biased AGAINST the maker hypothesis - a
conservative fill model can only make a surviving result stronger):
  maker BUY  : fills ONLY if bar low < L - one_tick. A touch (low == L) or a
               stop at exactly L - tick does NOT fill: at the venue, a touch
               means the far queue traded, not necessarily our order.
  maker SELL : fills ONLY if bar high > T + one_tick. Same reasoning.
  maker gaps : NO price improvement. If the bar opens through the limit the
               fill is booked at the limit price, not the better open. (The
               shared harness gives limit exits max(target, open); we forgo
               that improvement on maker legs to stay conservative.)
  maker cost : zero fee, zero slippage (you set the price). The real maker
               cost is adverse selection, which this experiment MEASURES via
               the taker comparison arm - it is never assumed away.
  entry-bar stop: if the fill bar's low also trades <= S, the stop fires the
               SAME bar. This is deterministic, not path-guessing: any print
               below S is below L, so the market passed L (filling us) on the
               way down. The shared harness defers exits to the next bar; on
               a flush-through bar that would hide a same-bar disaster.
  entry-bar target: NOT checked on the fill bar (the high may predate the
               fill; granting it would be optimistic). First checked next bar.
  taker stop : gap-aware like vectorized_harness._simulate_exit: fill base =
               min(stop, open), then slippage moves the price (TradeCoster
               convention: slippage is a PRICE adjustment, fees are dollars).

COMPARISON ARM (the kill-condition instrument): the SAME limit-price series
executed taker-at-touch - a marketable order fired when the bar touches L
(low <= L), filled at min(L, open) * (1 + slip) with taker fee, exits at the
same T/S levels but executed taker (target on touch high >= T, fill
T * (1 - slip) + fee). Identical levels, opposite execution side. The taker
arm gets touch fills and gap price improvement that the maker arm is denied;
every asymmetry favors taker, so a maker win is not an artifact of the sim.
Note the arms fill DIFFERENT trade sets by construction (touch-only bars are
taker-only) - that selection difference IS adverse selection, the thing P2
exists to measure.

FIRES-CHECK FIRST (v5 work order 4, standing rule from §3.2): armed-time %,
orders placed, fill rate and taker-stop rate are computed, printed and
serialized BEFORE any P&L field.

Output: research/graveyard/toll_collector.json, stamped cost_model_version.
Run:    python3 backtest/toll_collector.py
"""
import csv
import glob
import json
import math
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.cost_model import CostModel, COST_MODEL_VERSION  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'research', 'graveyard', 'toll_collector.json')

PAIRS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
K_LEVELS = [1.5, 2.0, 2.5]

# Binance spot PRICE_FILTER tick sizes for these pairs (all 0.01 as of the
# 2024-2025 data window). The tick only matters as the strict-inequality
# buffer in the trade-through rule; at these prices it is sub-basis-point.
TICKS = {'BTCUSDT': 0.01, 'ETHUSDT': 0.01, 'SOLUSDT': 0.01}

# Pre-committed knobs (see docstring DESIGN - none of these were swept).
M_EXIT = 1.0
STOP_ATR_MULT = 1.5
MAX_HOLD_BARS = 72
ATR_PERIOD = 14
VOL_WINDOW = 24          # 1-day realized vol measured on 1h returns
PCTL_WINDOW = 720        # 30 days of hourly bars
ARM_PCT = 70.0
NOTIONAL_USD = 100.0     # SPEC 6.1 fixed notional cap

PREDICTION_VERBATIM = (
    "Pre-registered prediction: maker-filled trades show positive net edge "
    "concentrated in the top vol quintile; calm-quintile fills ≈ 0.")
KILL_CONDITION_VERBATIM = (
    "Kill condition: if maker fills underperform the equivalent taker-at-touch "
    "trades by MORE than the fee+slippage savings (~14–30bps), adverse "
    "selection eats the discount and passive execution is dead as an edge source.")


# ============================================================================
# DATA LOADING - copied verbatim from run_incremental_graveyard.py (see
# module docstring for why it is copied, not imported).
# ============================================================================

def load_binance_merged(pair: str, tf: str) -> List[dict]:
    pattern = os.path.join(DATA_DIR, "{}-{}-*.csv".format(pair, tf))
    files = sorted(glob.glob(pattern))
    candles = []
    for f in files:
        with open(f, 'r') as fh:
            reader = csv.reader(fh)
            # Binance kline files are HEADERLESS: peek at row 1 and keep it
            # if numeric (skipping it unconditionally dropped the first
            # candle of every monthly file - re-audit NEW-2).
            first = next(reader, None)
            rows = ([first] if first and first[0].replace('.', '').isdigit() else [])
            for row in rows + list(reader):
                if len(row) < 6:
                    continue
                try:
                    ts = int(float(row[0]))
                    # Binance switched to MICROSECOND stamps at 2025-01-01;
                    # mixed units corrupt interval inference and re-open the
                    # regime-lookahead hole (re-audit NEW-1). Normalize to ms.
                    if ts >= 1e14:
                        ts //= 1000
                    candles.append({
                        'ts': ts,
                        'open': float(row[1]), 'high': float(row[2]),
                        'low': float(row[3]), 'close': float(row[4]),
                        'volume': float(row[5]),
                    })
                except (ValueError, IndexError):
                    continue
    candles.sort(key=lambda c: c['ts'])
    seen = set()
    deduped = []
    for c in candles:
        if c['ts'] not in seen:
            seen.add(c['ts'])
            deduped.append(c)
    return deduped


# ============================================================================
# INDICATORS - minimal, self-contained, documented conventions.
# ============================================================================

def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                period: int = ATR_PERIOD) -> np.ndarray:
    """Simple-mean ATR: rolling mean of True Range over `period` bars.

    WHY simple mean, not Wilder smoothing: the ATR here is a placement
    yardstick (k*ATR below close), not a signal being compared against other
    modules. A rolling mean is transparent, has a hard warmup boundary
    (nan before `period` bars), and cannot smuggle in an infinite lookback.
    """
    n = len(closes)
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    prev_close = closes[:-1]
    tr[1:] = np.maximum(highs[1:], prev_close) - np.minimum(lows[1:], prev_close)
    atr = np.full(n, np.nan)
    if n >= period:
        kernel = np.ones(period) / period
        atr[period - 1:] = np.convolve(tr, kernel, mode='valid')
    return atr


def compute_realized_vol(closes: np.ndarray, window: int = VOL_WINDOW) -> np.ndarray:
    """Realized vol at bar i = std of the last `window` hourly log returns
    (returns up to and including bar i's close - all known at bar i close).
    nan until a full window of returns exists."""
    n = len(closes)
    vol = np.full(n, np.nan)
    if n < window + 1:
        return vol
    logret = np.diff(np.log(closes))
    for i in range(window, n):
        vol[i] = float(np.std(logret[i - window:i]))
    return vol


def arming_stats(vol: np.ndarray, pctl_window: int = PCTL_WINDOW,
                 arm_pct: float = ARM_PCT) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(armed, pctile, valid) per bar.

    THE LOOKAHEAD GUARD: the threshold at bar i is the `arm_pct` percentile
    of vol over bars [i - pctl_window, i - 1] - STRICTLY BEFORE i. Bar i's
    own vol is the value being tested, never part of the yardstick it is
    tested against. (Letting bar i into its own percentile window is the
    delayed-oracle class of bug validate_harness.py exists to catch; the
    test suite pins a fixture where inclusion would flip the decision.)

    pctile[i] = % of the strictly-past window below vol[i]; recorded on every
    armed bar so fills can later be split into vol bands for the prediction.
    """
    n = len(vol)
    armed = np.zeros(n, dtype=bool)
    pctile = np.full(n, np.nan)
    valid = np.zeros(n, dtype=bool)
    for i in range(pctl_window, n):
        w = vol[i - pctl_window:i]
        if np.isnan(w).any() or math.isnan(vol[i]):
            continue
        valid[i] = True
        thr = float(np.quantile(w, arm_pct / 100.0))
        armed[i] = bool(vol[i] > thr)
        pctile[i] = float((w < vol[i]).mean() * 100.0)
    return armed, pctile, valid


# ============================================================================
# THE LOAD-BEARING WALL - trade-through predicates (SPEC 5.9).
# Kept as tiny pure functions so the tests pin them directly.
# ============================================================================

def trades_through_buy(bar_low: float, limit: float, tick: float) -> bool:
    """Resting limit BUY at `limit` fills ONLY if the bar traded strictly
    below limit - one tick. low == limit (touch) -> no fill.
    low == limit - tick -> STILL no fill (that print could be the far side of
    the book at the next level; we require the market to pass beyond it)."""
    return bar_low < limit - tick


def trades_through_sell(bar_high: float, limit: float, tick: float) -> bool:
    """Mirror rule for the resting limit SELL exit."""
    return bar_high > limit + tick


# ============================================================================
# SIMULATION - one arm ('maker' or 'taker'), one (pair, k) cell.
# ============================================================================

def _fee(cm: CostModel, notional: float, fee_symbol: str, maker: bool) -> float:
    """Dollar fee for one leg. Commission ONLY - slippage is applied as a
    price adjustment by the caller (TradeCoster convention: a worse fill
    moves the distance to the stop; a dollar subtraction would not).
    crypto_leg(maker=True) returns 0 fee and 0 slippage per the fee table."""
    return cm.crypto_leg(notional, fee_symbol, maker=maker).commission


def _run_arm(arm: str, opens, highs, lows, closes, ts, atr, armed, pctile, valid,
             pair: str, k: float, cm: CostModel,
             m_exit: float = M_EXIT, stop_atr_mult: float = STOP_ATR_MULT,
             max_hold_bars: int = MAX_HOLD_BARS, tick: Optional[float] = None,
             notional_usd: float = NOTIONAL_USD) -> Tuple[List[dict], Dict[str, int]]:
    """State machine over the bar series for ONE execution side.

    Bar-i event order (each choice documented in the module docstring):
      1. If in a position: check stop (gap-aware, conservative stop-first),
         then the target, then the 72-bar time backstop.
      2. If an order is resting (placed at a PRIOR bar's close): check fill
         against bar i. Maker requires trade-through; taker fills on touch.
         A maker fill whose bar also trades <= stop exits the same bar.
      3. At bar close: place/re-price the order if armed, cancel if not.
    """
    assert arm in ('maker', 'taker')
    if tick is None:
        tick = TICKS.get(pair, 0.01)
    # 'BTCUSDT' -> 'BTC/USDT' so the cost model's CRYPTO_CORE_PAIRS lookup
    # (0.01% taker on BTC/ETH vs 0.02% elsewhere) actually matches.
    fee_symbol = pair[:-4] + '/' + pair[-4:]
    slip = cm.slippage_taker
    n = len(closes)

    counters = {'bars': n, 'eligible_bars': 0, 'armed_bars': 0,
                'episodes': 0, 'order_bars': 0, 'fills': 0,
                'stops': 0, 'targets': 0, 'time_exits': 0, 'eod_exits': 0}
    trades: List[dict] = []

    resting = False
    L = anchor_atr = anchor_pct = None
    pos = None  # dict while in a position

    def _close(i, fill_px, exit_fee, reason):
        # float() casts everywhere: numpy scalars leak in from the OHLC
        # arrays and json.dump refuses np.float64 - a crash at serialization
        # time would throw away a completed run.
        fill_px, exit_fee = float(fill_px), float(exit_fee)
        pnl = (fill_px - pos['entry_px']) * pos['qty'] - pos['entry_fee'] - exit_fee
        trades.append({
            'pair': pair, 'k': k, 'arm': arm,
            'entry_ts': int(ts[pos['idx']]), 'exit_ts': int(ts[i]),
            'bars_held': int(i - pos['idx']),
            'limit_price': round(float(pos['limit']), 8),
            'entry_px': round(float(pos['entry_px']), 8),
            'exit_px': round(fill_px, 8),
            'qty': float(pos['qty']),
            'entry_fee': float(pos['entry_fee']), 'exit_fee': exit_fee,
            'fees': float(pos['entry_fee']) + exit_fee,
            'pnl_usd': float(pnl), 'pnl_bps': float(pnl / notional_usd * 1e4),
            'reason': reason,
            'entry_vol_pctile': pos['vol_pctile'],
        })
        counters['stops' if reason.startswith('stop') else
                 'targets' if reason.startswith('target') else
                 'time_exits' if reason == 'time' else 'eod_exits'] += 1

    for i in range(n):
        # ---- 1. exits ---------------------------------------------------
        if pos is not None:
            exited = False
            if lows[i] <= pos['stop']:
                # Gap-aware: opening through the stop fills at the open
                # (vectorized_harness convention); slippage worsens it.
                base = min(pos['stop'], opens[i])
                notional = base * pos['qty']
                fill = base * (1.0 - slip)
                _close(i, fill, _fee(cm, notional, fee_symbol, maker=False), 'stop')
                exited = True
            elif arm == 'maker' and trades_through_sell(highs[i], pos['target'], tick):
                # Maker exit: zero fee, zero slip, NO gap improvement
                # (conservative - see docstring FILL CONVENTIONS).
                fill = pos['target']
                _close(i, fill, _fee(cm, fill * pos['qty'], fee_symbol, maker=True),
                       'target_maker')
                exited = True
            elif arm == 'taker' and highs[i] >= pos['target']:
                fill = pos['target'] * (1.0 - slip)
                _close(i, fill,
                       _fee(cm, pos['target'] * pos['qty'], fee_symbol, maker=False),
                       'target_taker')
                exited = True
            elif i - pos['idx'] >= max_hold_bars:
                fill = closes[i] * (1.0 - slip)
                _close(i, fill,
                       _fee(cm, closes[i] * pos['qty'], fee_symbol, maker=False),
                       'time')
                exited = True
            if exited:
                pos = None

        # ---- 2. resting-order fill check --------------------------------
        if pos is None and resting:
            if arm == 'maker':
                filled = trades_through_buy(lows[i], L, tick)
                entry_px = L  # no gap improvement, even if open < L - tick
            else:
                filled = lows[i] <= L  # marketable-at-touch trigger
                entry_px = min(L, opens[i]) * (1.0 + slip) if lows[i] <= L else None
            if filled:
                counters['fills'] += 1
                entry_px = float(entry_px)  # numpy scalar -> JSON-safe float
                qty = notional_usd / entry_px
                entry_fee = float(_fee(cm, entry_px * qty, fee_symbol,
                                       maker=(arm == 'maker')))
                pos = {'idx': i, 'limit': L, 'entry_px': entry_px, 'qty': qty,
                       'entry_fee': entry_fee,
                       'target': L + m_exit * anchor_atr,
                       'stop': L - stop_atr_mult * anchor_atr,
                       'vol_pctile': anchor_pct}
                resting = False
                # Same-bar stop (deterministic: any print <= stop is below L,
                # so the market filled us at L on its way down first).
                if lows[i] <= pos['stop']:
                    base = pos['stop']
                    fill = base * (1.0 - slip)
                    _close(i, fill,
                           _fee(cm, base * pos['qty'], fee_symbol, maker=False),
                           'stop_same_bar')
                    pos = None

        # ---- 3. close-of-bar order management ---------------------------
        if valid[i]:
            counters['eligible_bars'] += 1
            if armed[i]:
                counters['armed_bars'] += 1
        if pos is None:
            can_place = bool(valid[i] and armed[i] and not math.isnan(atr[i])
                             and atr[i] > 0)
            if can_place:
                if not resting:
                    counters['episodes'] += 1  # a new contiguous resting spell
                resting = True
                L = closes[i] - k * atr[i]
                anchor_atr = atr[i]
                p = pctile[i]
                anchor_pct = None if math.isnan(p) else float(p)
            else:
                resting = False  # arming lapsed (or never held) -> cancel
        if resting:
            counters['order_bars'] += 1  # order will rest during bar i+1

    # End of data with an open position: close at the final close, taker.
    # Honest bookkeeping - the alternative is silently dropping an open risk.
    if pos is not None:
        i = n - 1
        fill = closes[i] * (1.0 - slip)
        _close(i, fill, _fee(cm, closes[i] * pos['qty'], fee_symbol, maker=False),
               'end_of_data')
        pos = None

    return trades, counters


def run_cell(candles: List[dict], pair: str, k: float, cm: CostModel,
             m_exit: float = M_EXIT, stop_atr_mult: float = STOP_ATR_MULT,
             max_hold_bars: int = MAX_HOLD_BARS, atr_period: int = ATR_PERIOD,
             vol_window: int = VOL_WINDOW, pctl_window: int = PCTL_WINDOW,
             arm_pct: float = ARM_PCT, tick: Optional[float] = None,
             notional_usd: float = NOTIONAL_USD,
             armed_override: Optional[np.ndarray] = None) -> dict:
    """Run BOTH arms for one (pair, k). armed_override exists for the test
    suite (it pins fill/fee mechanics without needing 720 bars of vol
    history); production runs never pass it and the report records that."""
    opens = np.array([c['open'] for c in candles], dtype=float)
    highs = np.array([c['high'] for c in candles], dtype=float)
    lows = np.array([c['low'] for c in candles], dtype=float)
    closes = np.array([c['close'] for c in candles], dtype=float)
    ts = np.array([c['ts'] for c in candles], dtype=np.int64)

    atr = compute_atr(highs, lows, closes, atr_period)
    vol = compute_realized_vol(closes, vol_window)
    if armed_override is not None:
        armed = np.asarray(armed_override, dtype=bool)
        pctile = np.full(len(closes), np.nan)
        valid = ~np.isnan(atr)
    else:
        armed, pctile, valid = arming_stats(vol, pctl_window, arm_pct)

    maker_trades, maker_counters = _run_arm(
        'maker', opens, highs, lows, closes, ts, atr, armed, pctile, valid,
        pair, k, cm, m_exit, stop_atr_mult, max_hold_bars, tick, notional_usd)
    taker_trades, taker_counters = _run_arm(
        'taker', opens, highs, lows, closes, ts, atr, armed, pctile, valid,
        pair, k, cm, m_exit, stop_atr_mult, max_hold_bars, tick, notional_usd)

    return {'pair': pair, 'k': k, 'n_bars': len(candles),
            'span': [int(ts[0]), int(ts[-1])] if len(candles) else None,
            'maker_trades': maker_trades, 'maker_counters': maker_counters,
            'taker_trades': taker_trades, 'taker_counters': taker_counters}


# ============================================================================
# REPORTING - fires-check BEFORE P&L (v5 work order 4). The dict insertion
# order is the serialization order, and the runner prints in the same order.
# ============================================================================

def _trade_stats(trades: List[dict]) -> dict:
    if not trades:
        return {'n_trades': 0, 'net_usd_total': 0.0, 'net_usd_per_trade': None,
                'net_bps_per_trade': None, 'win_rate': None,
                'fees_usd_total': 0.0, 'mean_bars_held': None}
    pnl = [t['pnl_usd'] for t in trades]
    bps = np.array([t['pnl_bps'] for t in trades])
    # t-stat of the mean against zero: the number that says whether the mean
    # is a measurement or a shrug (standing rule 7 in numeric form).
    tstat = (float(bps.mean() / (bps.std() / len(bps) ** 0.5))
             if len(bps) > 1 and bps.std() > 0 else None)
    return {
        'n_trades': len(trades),
        'net_usd_total': round(sum(pnl), 4),
        'net_usd_per_trade': round(sum(pnl) / len(pnl), 6),
        'net_bps_per_trade': round(float(bps.mean()), 3),
        'std_bps': round(float(bps.std()), 2),
        't_stat': round(tstat, 3) if tstat is not None else None,
        'win_rate': round(sum(1 for p in pnl if p > 0) / len(pnl), 4),
        'fees_usd_total': round(sum(t['fees'] for t in trades), 4),
        'mean_bars_held': round(float(np.mean([t['bars_held'] for t in trades])), 2),
    }


def _fires_check_of(counters: dict, maker_trades: List[dict]) -> dict:
    eligible = counters['eligible_bars']
    episodes = counters['episodes']
    fills = counters['fills']
    closed = len(maker_trades)
    stops = sum(1 for t in maker_trades if t['reason'].startswith('stop'))
    return {
        'armed_time_pct': round(counters['armed_bars'] / eligible * 100.0, 2)
                          if eligible else None,
        'orders_placed': episodes,          # contiguous resting spells
        'order_bars': counters['order_bars'],
        'maker_fills': fills,
        'fill_rate_pct': round(fills / episodes * 100.0, 2) if episodes else None,
        'taker_stop_rate_pct': round(stops / closed * 100.0, 2) if closed else None,
    }


def build_report(cells: List[dict], cm: CostModel, meta: Optional[dict] = None) -> dict:
    all_maker = [t for c in cells for t in c['maker_trades']]
    all_taker = [t for c in cells for t in c['taker_trades']]

    # Pooled fires-check: sum counters across cells (same cost-model stamp
    # everywhere, so pooling is legal per standing rule 8).
    pooled_counters = {k: sum(c['maker_counters'][k] for c in cells)
                       for k in cells[0]['maker_counters']} if cells else {}
    fires_pooled = _fires_check_of(pooled_counters, all_maker) if cells else {}
    fires_pooled['taker_arm_entries'] = len(all_taker)

    # The kill-condition comparison. Savings = what the maker side did not
    # pay: mean taker fees (dollars, from actual trades) + 2x slippage as a
    # price effect, expressed in bps of the $100 notional.
    maker_stats = _trade_stats(all_maker)
    taker_stats = _trade_stats(all_taker)
    savings_bps = None
    if all_taker:
        mean_fee_bps = float(np.mean([t['fees'] for t in all_taker])) / NOTIONAL_USD * 1e4
        savings_bps = round(mean_fee_bps + 2 * cm.slippage_taker * 1e4, 3)
    kill_fired = None
    if maker_stats['net_bps_per_trade'] is not None and \
            taker_stats['net_bps_per_trade'] is not None:
        kill_fired = bool(maker_stats['net_bps_per_trade']
                          < taker_stats['net_bps_per_trade'])

    # Prediction split: fills arming above the 90th vol percentile vs the
    # 70-90 band (calm fills are structurally absent - the gate never arms
    # below the 70th; noted verbatim-prediction caveat in the docstring).
    hi = [t for t in all_maker if t['entry_vol_pctile'] is not None
          and t['entry_vol_pctile'] >= 90.0]
    mid = [t for t in all_maker if t['entry_vol_pctile'] is not None
           and t['entry_vol_pctile'] < 90.0]

    report = {
        'experiment': 'toll_collector_lab_v5_p2',
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'cost_model_version': COST_MODEL_VERSION,
        'params': {
            'pairs': [c['pair'] for c in cells] and sorted({c['pair'] for c in cells}),
            'k_levels': sorted({c['k'] for c in cells}),
            'm_exit': M_EXIT, 'stop_atr_mult': STOP_ATR_MULT,
            'max_hold_bars': MAX_HOLD_BARS, 'atr_period': ATR_PERIOD,
            'vol_window_bars': VOL_WINDOW, 'pctl_window_bars': PCTL_WINDOW,
            'arm_pct': ARM_PCT, 'notional_usd': NOTIONAL_USD,
            'ticks': TICKS,
            'fee_model': cm.describe(),
        },
        'prediction_verbatim': PREDICTION_VERBATIM,
        'kill_condition_verbatim': KILL_CONDITION_VERBATIM,
        # ---- FIRES-CHECK FIRST (v5 work order 4) ----
        'fires_check': {
            'pooled': fires_pooled,
            'per_cell': [dict({'pair': c['pair'], 'k': c['k']},
                              **_fires_check_of(c['maker_counters'],
                                                c['maker_trades']))
                         for c in cells],
        },
        # ---- P&L only after the fires-check ----
        'pnl': {
            'maker': maker_stats,
            'taker_at_touch': taker_stats,
            'comparison': {
                'maker_minus_taker_net_bps':
                    None if kill_fired is None else round(
                        maker_stats['net_bps_per_trade']
                        - taker_stats['net_bps_per_trade'], 3),
                'fee_plus_slip_savings_bps': savings_bps,
                'note': ('arms fill different trade sets by construction; '
                         'the difference in sets IS the adverse selection '
                         'being measured'),
            },
            'by_vol_band': {
                'pctile_90_100': _trade_stats(hi),
                'pctile_70_90': _trade_stats(mid),
            },
            'per_cell': [{'pair': c['pair'], 'k': c['k'],
                          'maker': _trade_stats(c['maker_trades']),
                          'taker': _trade_stats(c['taker_trades'])}
                         for c in cells],
        },
        'kill_result': {
            'fired': kill_fired,
            'rule': 'fired iff pooled maker net bps/trade < pooled taker net '
                    'bps/trade (equivalent to gross underperformance '
                    'exceeding the fee+slippage savings)',
        },
        # Prediction verdict computed mechanically from the pre-registered
        # split, so nobody can re-narrate it after seeing the numbers: the
        # prediction claimed edge CONCENTRATED in the top vol band.
        'prediction_result': {
            'claim': 'edge concentrated in top vol band (>=90th pctile fills)',
            'top_band_net_bps': _trade_stats(hi)['net_bps_per_trade'],
            'lower_band_net_bps': _trade_stats(mid)['net_bps_per_trade'],
            'confirmed': (None if not (hi and mid) else bool(
                (_trade_stats(hi)['net_bps_per_trade'] or 0)
                > (_trade_stats(mid)['net_bps_per_trade'] or 0)
                and (_trade_stats(hi)['net_bps_per_trade'] or 0) > 0)),
        },
        'honesty_notes': [],
        'maker_trades': all_maker,
        'taker_trades_summary_only': True,
    }

    # Standing rule 7: say out loud when the sample is a shrug.
    n_fills = maker_stats['n_trades']
    if n_fills < 400:
        report['honesty_notes'].append(
            '{} maker fills is BELOW the 400-800 power bar from the v5 power '
            'ledger; any P&L verdict here is a shrug, not a verdict.'.format(n_fills))
    if meta:
        report['honesty_notes'].extend(meta.get('notes', []))
        report['data'] = meta.get('data')
    return report


def print_report(report: dict) -> None:
    """Fires-check FIRST, then P&L - the print order is the point."""
    fc = report['fires_check']['pooled']
    print('=' * 72)
    print('TOLL COLLECTOR (Lab v5 P2) - FIRES-CHECK (before any P&L)')
    print('=' * 72)
    print('  armed-time %%        : %s' % fc.get('armed_time_pct'))
    print('  orders placed       : %s' % fc.get('orders_placed'))
    print('  maker fills         : %s' % fc.get('maker_fills'))
    print('  fill rate %%         : %s' % fc.get('fill_rate_pct'))
    print('  taker-stop rate %%   : %s' % fc.get('taker_stop_rate_pct'))
    print('  taker-arm entries   : %s' % fc.get('taker_arm_entries'))
    for cell in report['fires_check']['per_cell']:
        print('    %s k=%.1f armed %5s%% placed %5s fills %4s fillrate %5s%% stoprate %5s%%'
              % (cell['pair'], cell['k'], cell['armed_time_pct'],
                 cell['orders_placed'], cell['maker_fills'],
                 cell['fill_rate_pct'], cell['taker_stop_rate_pct']))
    print()
    print('-' * 72)
    print('P&L (only now)')
    print('-' * 72)
    mk, tk = report['pnl']['maker'], report['pnl']['taker_at_touch']
    cmp_ = report['pnl']['comparison']
    print('  maker : n=%s net/trade=%s bps (t=%s)  win=%s' %
          (mk['n_trades'], mk['net_bps_per_trade'], mk['t_stat'], mk['win_rate']))
    print('  taker : n=%s net/trade=%s bps (t=%s)  win=%s' %
          (tk['n_trades'], tk['net_bps_per_trade'], tk['t_stat'], tk['win_rate']))
    print('  maker - taker (net bps): %s   fee+slip savings: %s bps' %
          (cmp_['maker_minus_taker_net_bps'], cmp_['fee_plus_slip_savings_bps']))
    vb = report['pnl']['by_vol_band']
    print('  vol band 90-100: n=%s net=%s bps | 70-90: n=%s net=%s bps' %
          (vb['pctile_90_100']['n_trades'], vb['pctile_90_100']['net_bps_per_trade'],
           vb['pctile_70_90']['n_trades'], vb['pctile_70_90']['net_bps_per_trade']))
    print('  KILL CONDITION FIRED: %s' % report['kill_result']['fired'])
    print('  PREDICTION (edge concentrated in top vol band) CONFIRMED: %s'
          % report['prediction_result']['confirmed'])
    for note in report['honesty_notes']:
        print('  NOTE: %s' % note)


# ============================================================================
# RUNNER
# ============================================================================

def main() -> int:
    cm = CostModel()
    cells = []
    data_meta = {}
    for pair in PAIRS:
        candles = load_binance_merged(pair, '1h')
        if len(candles) < PCTL_WINDOW + VOL_WINDOW + 100:
            print('SKIP %s: only %d bars (< warmup)' % (pair, len(candles)))
            continue
        data_meta[pair] = {
            'bars': len(candles),
            'first': datetime.fromtimestamp(candles[0]['ts'] / 1000,
                                            tz=timezone.utc).isoformat(),
            'last': datetime.fromtimestamp(candles[-1]['ts'] / 1000,
                                           tz=timezone.utc).isoformat(),
        }
        for k in K_LEVELS:
            cells.append(run_cell(candles, pair, k, cm))
    if not cells:
        print('No data cells - nothing to report.')
        return 1

    meta = {'data': data_meta,
            'notes': ['Data span is ~12 months per pair (2024-10..2025-09), '
                      'not the ~2 years the proposal hoped for; 30 days of it '
                      'is arming warmup. All conclusions are one-regime '
                      'conclusions.']}
    report = build_report(cells, cm, meta)
    print_report(report)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w') as f:
        json.dump(report, f, indent=1)
    print('\nwrote %s' % OUT_PATH)
    return 0


if __name__ == '__main__':
    sys.exit(main())
