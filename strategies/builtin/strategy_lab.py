"""Strategy Lab: 7 backtest candidates from references/strategy-lab-v1.md.

Every one of these is an untested hypothesis (see the doc's section 0.1 -
expect most to fail). IDs match the lab: S6, S1, C2, C5, S2, D2, D1.

The Strategy.scan() interface only exposes a single pair's own candles, so
where the spec calls for cross-asset data the scanner doesn't have access to
(BTC dominance for C5, RSP breadth for S2, multi-anchor earnings dates for
S1), each strategy below falls back to the single-symbol proxy noted inline
-- that's a deliberate simplification per the build instructions, not an
oversight.
"""
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

from strategies.base import Strategy, Signal
from indicators.atr import latest_atr
from indicators.ema import latest_ema
from indicators.volume import volume_ratio
from indicators.macd_stoch import bollinger_bands


# ============ SHARED HELPERS ============

def _dt(ts: float) -> datetime:
    """Convert timestamp to datetime, auto-detecting microseconds vs milliseconds vs seconds."""
    if ts > 1e14:       # microseconds (16 digits, e.g. Binance kline CSV)
        return datetime.fromtimestamp(ts / 1_000_000, tz=timezone.utc)
    elif ts > 1e11:     # milliseconds (13 digits, e.g. yfinance)
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    else:               # already seconds
        return datetime.fromtimestamp(ts, tz=timezone.utc)


def _dt_et(ts: float) -> datetime:
    """Timestamp -> US/Eastern wall time (DST-aware). Session-window
    strategies (market open, midday, close) MUST use this, not _dt: comparing
    an ET window like 11:15-14:15 against UTC hours selects 6:15-9:15 ET in
    winter - a completely different (and usually opposite) session regime."""
    from zoneinfo import ZoneInfo
    return _dt(ts).astimezone(ZoneInfo('America/New_York'))


_XNYS_CAL = None  # module-level cache: build the calendar exactly once


def _in_xnys_session(ts: float) -> bool:
    """True if this bar's timestamp falls inside an NYSE trading session.

    Uses exchange_calendars (library policy 2026-08-12): unlike a weekday
    check, this also excludes holidays and the afternoon of half-days -
    session-window strategies were previously 'trading' Thanksgiving Friday
    afternoons that don't exist."""
    global _XNYS_CAL
    try:
        import pandas as pd
        if _XNYS_CAL is None:
            import exchange_calendars as xcals
            _XNYS_CAL = xcals.get_calendar('XNYS')
        return bool(_XNYS_CAL.is_trading_minute(pd.Timestamp(_dt(ts))))
    except Exception:
        # Calendar unavailable (missing dep, out-of-range date): fall back to
        # the weekday approximation rather than silently trading nothing.
        return _dt_et(ts).weekday() <= 4


def _bar_seconds(timestamps: List[float], default: float = 3600.0) -> float:
    """Median spacing between bars, in seconds, inferred from the window itself.

    Strategy.scan() is handed candles with no timeframe label, so any strategy
    whose logic is expressed in TIME ("4 days back", "one week ago") has to
    derive the bar size or it silently means something different on every
    timeframe. Median, not mean: equity series have weekend and holiday gaps
    that would drag a mean upward, and crypto series occasionally have a
    missing bar.

    Returns `default` (1h) only when there are fewer than two usable
    timestamps, which cannot happen for any window a harness would build.
    """
    if not timestamps or len(timestamps) < 2:
        return default
    diffs = []
    prev = _dt(timestamps[0]).timestamp()
    for ts in timestamps[1:]:
        cur = _dt(ts).timestamp()
        d = cur - prev
        prev = cur
        if d > 0:
            diffs.append(d)
    if not diffs:
        return default
    diffs.sort()
    return diffs[len(diffs) // 2]


def _bars_for(seconds: float, bar_seconds: float) -> int:
    """How many bars of size `bar_seconds` span `seconds` of wall time."""
    if bar_seconds <= 0:
        return 1
    return max(1, int(round(seconds / bar_seconds)))


def _percentile_rank(current: float, series: List[float]) -> float:
    """Fraction of `series` <= current. 0.15 means current is in the bottom 15%."""
    if not series:
        return 0.5
    return sum(1 for v in series if v <= current) / len(series)


def _clamp(value: float, lo: float = 0.3, hi: float = 0.7) -> float:
    return max(lo, min(hi, value))


def _compute_target(entry: float, stop: float, r: float = 2.0) -> float:
    risk = entry - stop
    if risk <= 0:
        return entry
    return entry + risk * r


def _anchored_vwap(highs: List[float], lows: List[float], closes: List[float],
                    volumes: List[float], anchor_idx: int) -> Optional[float]:
    cum_pv = 0.0
    cum_vol = 0.0
    for i in range(anchor_idx, len(closes)):
        typical = (highs[i] + lows[i] + closes[i]) / 3.0
        cum_pv += typical * volumes[i]
        cum_vol += volumes[i]
    if cum_vol == 0:
        return None
    return cum_pv / cum_vol


def _bollinger_bandwidth_at(closes: List[float], i: int, period: int = 20,
                             std_mult: float = 2.0) -> Optional[float]:
    """Bandwidth of a 20,2 Bollinger as of index i, using closes[i-period:i]."""
    if i < period:
        return None
    window = closes[i - period:i]
    sma = sum(window) / period
    if sma == 0:
        return None
    variance = sum((x - sma) ** 2 for x in window) / period
    std = math.sqrt(variance)
    return (2 * std_mult * std) / sma


# ============ S6 · Volatility-Squeeze Direction Deferral ============

class VolatilitySqueezeDeferral(Strategy):
    """Bollinger(20,2) squeeze inside Keltner(20, 2*ATR10); enter on upside break."""
    name = "S6"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        n = len(closes)
        if n < 45:
            return None

        # Squeeze state is evaluated as of the prior candle so the breakout
        # candle's own range doesn't contaminate the bands it's breaking.
        prior_closes = closes[:-1]
        prior_highs = highs[:-1]
        prior_lows = lows[:-1]
        if len(prior_closes) < 40:
            return None

        bb_lower, bb_mid, bb_upper = bollinger_bands(prior_closes, 20, 2.0)
        if bb_mid == 0:
            return None
        keltner_mid = latest_ema(prior_closes, 20)
        atr10 = latest_atr(prior_highs, prior_lows, prior_closes, 10)
        if atr10 == 0:
            return None
        kelt_upper = keltner_mid + 2 * atr10
        kelt_lower = keltner_mid - 2 * atr10
        squeeze_active = bb_upper < kelt_upper and bb_lower > kelt_lower
        if not squeeze_active:
            return None

        bandwidth = (bb_upper - bb_lower) / bb_mid
        lookback = min(len(prior_closes), 504)  # trailing ~2 years of daily bars
        start_i = max(20, len(prior_closes) - lookback)
        bandwidth_series = []
        for i in range(start_i, len(prior_closes) + 1):
            bw = _bollinger_bandwidth_at(prior_closes, i)
            if bw is not None:
                bandwidth_series.append(bw)
        if len(bandwidth_series) < 50:
            return None
        pct_rank = _percentile_rank(bandwidth, bandwidth_series)
        if pct_rank > 0.15:
            return None

        atr14 = latest_atr(highs, lows, closes, 14)
        if atr14 == 0:
            return None
        breakout_level = bb_upper + 0.25 * atr14
        if closes[-1] <= breakout_level:
            return None

        entry = closes[-1]
        stop = bb_lower
        squeeze_range = bb_upper - bb_lower
        if squeeze_range <= 0 or stop >= entry:
            return None
        target = entry + 3.5 * squeeze_range

        vol_r = volume_ratio(volumes, 20)
        conf = 0.35 + (0.15 - pct_rank) + (0.05 if vol_r >= 1.5 else 0.0)
        conf = _clamp(conf)

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'bb_upper': round(bb_upper, 4), 'bb_lower': round(bb_lower, 4),
                'bandwidth': round(bandwidth, 6), 'bandwidth_percentile': round(pct_rank, 4),
                'kelt_upper': round(kelt_upper, 4), 'kelt_lower': round(kelt_lower, 4),
                'atr14': round(atr14, 4), 'volume_ratio': round(vol_r, 4),
            },
            entry=entry, stop=stop, target=target, valid_for=10,
        )


# ============ S1 · AVWAP Confluence Node ============

class AVWAPConfluence(Strategy):
    """Long when >=3 of 4 anchored VWAPs cluster within 0.5*ATR(20) of each other."""
    name = "S1"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        n = len(closes)
        if n < 210:
            return None

        atr20 = latest_atr(highs, lows, closes, 20)
        if atr20 == 0:
            return None

        lookback = min(n, 252)
        window_start = n - lookback
        window_highs = highs[window_start:]
        window_lows = lows[window_start:]
        window_volumes = volumes[window_start:]
        idx_52w_high = window_start + window_highs.index(max(window_highs))
        idx_52w_low = window_start + window_lows.index(min(window_lows))
        idx_high_volume = window_start + window_volumes.index(max(window_volumes))
        # No earnings calendar available to the scanner; approximate "last
        # earnings" with a quarterly (~63 session) lookback anchor.
        idx_earnings_proxy = n - 63 if n - 63 >= window_start else None

        anchors = {}
        for label, idx in (('52w_high', idx_52w_high), ('52w_low', idx_52w_low),
                            ('high_volume', idx_high_volume),
                            ('earnings_proxy', idx_earnings_proxy)):
            if idx is None or idx >= n:
                continue
            val = _anchored_vwap(highs, lows, closes, volumes, idx)
            if val is not None:
                anchors[label] = val
        if len(anchors) < 3:
            return None

        band = 0.5 * atr20
        values = list(anchors.values())
        best_cluster = []
        for v in values:
            cluster = [x for x in values if abs(x - v) <= band]
            if len(cluster) > len(best_cluster):
                best_cluster = cluster
        if len(best_cluster) < 3:
            return None
        node = sum(best_cluster) / len(best_cluster)

        current_close = closes[-1]
        prior_close = closes[-2] if n >= 2 else current_close
        dist = abs(current_close - node)
        prior_dist = abs(prior_close - node)
        if dist > 0.75 * atr20 or dist >= prior_dist:
            return None

        sma50_now = sum(closes[-50:]) / 50
        sma50_prior = sum(closes[-60:-10]) / 50
        if sma50_now <= sma50_prior:
            return None
        sma200 = sum(closes[-200:]) / 200
        if current_close <= sma200:
            return None

        entry = node
        stop = node - 1.5 * atr20
        target = node + 3.0 * atr20
        if stop >= entry or target <= entry:
            return None

        conf = _clamp(0.4 + 0.05 * (len(best_cluster) - 3))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'node': round(node, 4), 'cluster_size': len(best_cluster),
                'atr20': round(atr20, 4), 'sma50': round(sma50_now, 4),
                'sma200': round(sma200, 4),
                'anchors': {k: round(v, 4) for k, v in anchors.items()},
            },
            entry=entry, stop=stop, target=target, valid_for=5,
        )


# ============ C2 · Weekend Liquidity Vacuum Reversion ============

class WeekendVacuumReversion(Strategy):
    """Fade abnormally large, abnormally low-volume weekend moves in crypto."""
    name = "C2"
    is_entry = True

    # Every horizon below is TIME, not a bar count (D-274 / R-008). The
    # pre-fix code hardcoded bar counts that happened to equal these
    # durations on 1h bars only: `24 * 4` bars meant "4 days" and was 24
    # hours on 15m and 8 hours on 5m, so the Friday anchor was unreachable
    # and the anchor search failed on 100% of sub-hourly trigger bars.
    HISTORY_SECONDS = 5 * 7 * 24 * 3600      # 5 weeks of baseline weekends
    ANCHOR_LOOKBACK_SECONDS = 4 * 24 * 3600  # far enough back to reach Friday
    WEEK_SECONDS = 7 * 24 * 3600             # step between baseline weekends

    # Bar-count floor the harness reads. It is the 1h value, because
    # `min_bars` is a class constant and the harness reads it before it knows
    # the series timeframe. Use `min_bars_for()` when the bar size IS known:
    # on 15m the real requirement is 3,360 bars and on 5m it is 10,080, both
    # above vectorized_harness.MAX_STRATEGY_WINDOW (2,000), so those series
    # are structurally NOT_TESTED rather than tested-and-failed (convention
    # 11). Until the harness calls min_bars_for, sub-hourly series clear the
    # 840-bar gate, get a 840-bar window, fail the in-scan history guard and
    # are mislabelled FAIL. See D-274.
    min_bars = 24 * 7 * 5

    @classmethod
    def min_bars_for(cls, bar_seconds: float) -> int:
        """Bars of history this strategy needs on a series of this bar size."""
        return _bars_for(cls.HISTORY_SECONDS, bar_seconds)

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        timestamps = candles['timestamps']
        n = len(closes)
        bar_seconds = _bar_seconds(timestamps)
        if n < _bars_for(self.HISTORY_SECONDS, bar_seconds):
            return None

        current_dt = _dt(timestamps[-1])
        if current_dt.weekday() != 6 or current_dt.hour < 22:
            return None

        friday_idx = None
        sunday20_idx = None
        i = n - 1
        anchor_lookback = _bars_for(self.ANCHOR_LOOKBACK_SECONDS, bar_seconds)
        while i >= 0 and i >= n - anchor_lookback:
            dt = _dt(timestamps[i])
            if sunday20_idx is None and dt.weekday() == 6 and dt.hour <= 20:
                sunday20_idx = i
            if dt.weekday() == 4 and dt.hour <= 20:
                friday_idx = i
                break
            i -= 1
        if friday_idx is None or sunday20_idx is None or sunday20_idx <= friday_idx:
            return None

        friday_price = closes[friday_idx]
        sunday_price = closes[sunday20_idx]
        if friday_price == 0:
            return None
        weekend_move = (sunday_price - friday_price) / friday_price
        weekend_span = sunday20_idx - friday_idx
        weekend_volume = sum(volumes[friday_idx:sunday20_idx + 1])

        weekly_moves = []
        weekly_volumes = []
        week_bars = _bars_for(self.WEEK_SECONDS, bar_seconds)
        cursor = friday_idx
        for _ in range(12):
            f_idx = cursor - week_bars
            s_idx = f_idx + weekend_span
            if f_idx < 0 or s_idx >= n or s_idx <= f_idx or closes[f_idx] == 0:
                cursor -= week_bars
                continue
            move = abs((closes[s_idx] - closes[f_idx]) / closes[f_idx])
            weekly_moves.append(move)
            weekly_volumes.append(sum(volumes[f_idx:s_idx + 1]))
            cursor -= week_bars
        if len(weekly_moves) < 4:
            return None

        median_move = sorted(weekly_moves)[len(weekly_moves) // 2]
        if median_move == 0:
            return None
        abs_move = abs(weekend_move)
        if abs_move <= 1.5 * median_move:
            return None

        vol_pct = _percentile_rank(weekend_volume, weekly_volumes)
        if vol_pct >= 0.40:
            return None

        # Quant v1 is spot/long-only, so only the downside vacuum (bought
        # into a reversion higher) is expressible here.
        if weekend_move >= 0:
            return None

        entry = closes[-1]
        stop = entry * (1 - 1.3 * abs_move)
        target = entry * (1 + 0.5 * abs_move)
        if stop >= entry or target <= entry:
            return None

        conf = _clamp(0.4 + min(0.15, (abs_move / median_move - 1.5) * 0.05))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'weekend_move': round(weekend_move, 4),
                'median_weekend_move': round(median_move, 4),
                'weekend_volume': round(weekend_volume, 2),
                'weekend_volume_percentile': round(vol_pct, 4),
            },
            entry=entry, stop=stop, target=target, valid_for=48,
        )


# ============ C5 · Dominance Rotation Ladder ============

class DominanceRotation(Strategy):
    """Rotate into ETH when momentum accelerates and volume migrates in.

    No BTC/SOL market-cap series is available to a single-pair scan, so BTC
    dominance ROC and altcoin/BTC volume migration are proxied from ETH's own
    momentum (14d ROC accelerating vs the prior 14d) and its own volume trend.
    """
    name = "C5"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        n = len(closes)
        if n < 34:
            return None

        atr14 = latest_atr(highs, lows, closes, 14)
        if atr14 == 0:
            return None
        if closes[-15] == 0 or closes[-29] == 0:
            return None
        roc14 = (closes[-1] - closes[-15]) / closes[-15]
        roc14_prior = (closes[-15] - closes[-29]) / closes[-29]
        accelerating = roc14 > roc14_prior

        vol_ratio_now = volume_ratio(volumes, 20)
        vol_ratio_prior = volume_ratio(volumes[:-14], 20)
        migration_confirmed = (vol_ratio_prior > 0
                                and vol_ratio_now >= vol_ratio_prior * 1.20)

        if roc14 <= 0.05 or not accelerating or not migration_confirmed:
            return None

        entry = closes[-1]
        stop = entry - 2 * atr14
        target = _compute_target(entry, stop, r=2.0)
        if stop >= entry or target <= entry:
            return None

        conf = _clamp(0.4 + min(0.15, (roc14 - 0.05) * 2))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'roc14': round(roc14, 4), 'roc14_prior': round(roc14_prior, 4),
                'volume_ratio_now': round(vol_ratio_now, 4),
                'volume_ratio_14ago': round(vol_ratio_prior, 4),
                'atr14': round(atr14, 4),
            },
            entry=entry, stop=stop, target=target, valid_for=5,
        )


# ============ S2 · Breadth Divergence Rotation ============

class BreadthDivergenceRotation(Strategy):
    """Long SPY when the "broad" regime is proxied to be active.

    The real signal needs RSP alongside SPY; a single-pair scan can't see
    both. Proxy: realized 21-session volatility in the bottom quartile of its
    own trailing ~3yr distribution stands in for a broad (not narrow,
    concentration-driven) advance.
    """
    name = "S2"
    is_entry = True

    def _realized_vol(self, closes: List[float], period: int = 21) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(-period, 0)]
        mean = sum(rets) / len(rets)
        variance = sum((r - mean) ** 2 for r in rets) / len(rets)
        return math.sqrt(variance)

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        n = len(closes)
        lookback = min(n, 756)  # ~3 years of daily bars
        if lookback < 100:
            return None
        window_start = n - lookback

        vol_series = []
        for i in range(window_start + 22, n + 1):
            v = self._realized_vol(closes[window_start:i])
            if v is not None:
                vol_series.append(v)
        if len(vol_series) < 50:
            return None

        current_vol = vol_series[-1]
        pct = _percentile_rank(current_vol, vol_series)
        if pct > 0.25:
            return None  # not a broad regime

        atr14 = latest_atr(highs, lows, closes, 14)
        if atr14 == 0:
            return None
        ema50 = latest_ema(closes, 50)
        if closes[-1] <= ema50:
            return None

        entry = closes[-1]
        stop = entry - 1.5 * atr14
        target = entry + 2.5 * atr14
        if stop >= entry or target <= entry:
            return None

        conf = _clamp(0.4 + (0.25 - pct))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'realized_vol_21d': round(current_vol, 6),
                'vol_percentile': round(pct, 4),
                'atr14': round(atr14, 4), 'ema50': round(ema50, 4),
            },
            entry=entry, stop=stop, target=target, valid_for=5,
        )


# ============ D2 · Failed-Breakout Harvest (inverted ORB) ============

class FailedBreakoutHarvest(Strategy):
    """Fade the first 5-min bar that re-enters the opening range after a break.

    NOTE (harness contract): the upside-break half of this strategy emits
    direction='bearish' (a short). The vectorized harness simulates LONGS
    ONLY and drops bearish signals, so graveyard results for D2 cover only
    the downside-break/long-fade half. Do not read them as a verdict on the
    full hypothesis. Short simulation is a V3 (futures) concern.
    """
    name = "D2"
    is_entry = True

    def _day_indices(self, timestamps: List[float], today) -> List[int]:
        n = len(timestamps)
        idxs = []
        i = n - 1
        while i >= 0 and _dt_et(timestamps[i]).date() == today:
            idxs.append(i)
            i -= 1
        idxs.reverse()
        return idxs

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        timestamps = candles['timestamps']
        n = len(closes)
        if n < 20:
            return None

        current_dt = _dt_et(timestamps[-1])  # ET: session-day grouping
        if not _in_xnys_session(timestamps[-1]):
            return None

        day_indices = self._day_indices(timestamps, current_dt.date())
        if len(day_indices) < 5:
            return None

        or_indices = day_indices[:3]  # first 15 minutes (9:30-9:45) as 5m bars
        or_high = max(highs[i] for i in or_indices)
        or_low = min(lows[i] for i in or_indices)
        or_width = or_high - or_low
        if or_width <= 0:
            return None

        current_idx = day_indices[-1]
        if current_idx <= or_indices[-1]:
            return None

        prev_idx = day_indices[-2]
        prev_close = closes[prev_idx]
        if or_low <= prev_close <= or_high:
            return None  # previous bar was already back inside; not the reentry bar

        post_or_indices = [i for i in day_indices if or_indices[-1] < i < current_idx]
        if not post_or_indices:
            return None
        broke_upside = any(highs[i] >= or_high + 0.25 * or_width for i in post_or_indices)
        broke_downside = any(lows[i] <= or_low - 0.25 * or_width for i in post_or_indices)
        if not (broke_upside or broke_downside):
            return None

        current_close = closes[current_idx]
        if not (or_low <= current_close <= or_high):
            return None

        atr14 = latest_atr(highs, lows, closes, 14)
        vol_r = volume_ratio(volumes, 20)
        entry = current_close

        if broke_upside:
            direction = 'bearish'
            stop = max(highs[i] for i in post_or_indices) + 0.1 * or_width
            target = or_low
            if stop <= entry or target >= entry:
                return None
        else:
            direction = 'bullish'
            stop = min(lows[i] for i in post_or_indices) - 0.1 * or_width
            target = or_high
            if stop >= entry or target <= entry:
                return None

        conf = _clamp(0.45 + (0.05 if 0.6 <= vol_r <= 1.0 else 0.0))

        return Signal(
            pair="", pattern=self.name, direction=direction, confidence=conf,
            features={
                'or_high': round(or_high, 4), 'or_low': round(or_low, 4),
                'or_width': round(or_width, 4), 'atr14': round(atr14, 4),
                'volume_ratio': round(vol_r, 4),
            },
            entry=entry, stop=stop, target=target, valid_for=1,
        )


# ============ D1 · Midday Liquidity Tax Dodge ============

class MiddayLiquidityDodge(Strategy):
    """Buy dips below session VWAP, but only in the boring 11:15-14:15 ET window."""
    name = "D1"
    is_entry = True

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']
        timestamps = candles['timestamps']
        n = len(closes)
        if n < 30:
            return None

        # ET, not UTC: the 11:15-14:15 midday window is a New York session
        # concept. Under UTC this selected 6:15-9:15 ET in winter (pre-open)
        # and the volatile open in summer - the opposite of the hypothesis.
        current_dt = _dt_et(timestamps[-1])
        if not _in_xnys_session(timestamps[-1]):
            return None
        minute_of_day = current_dt.hour * 60 + current_dt.minute
        if minute_of_day < 11 * 60 + 15 or minute_of_day > 14 * 60 + 15:
            return None

        today = current_dt.date()
        day_indices = []
        i = n - 1
        while i >= 0 and _dt_et(timestamps[i]).date() == today:
            day_indices.append(i)
            i -= 1
        day_indices.reverse()
        if len(day_indices) < 10:
            return None

        cum_pv = 0.0
        cum_vol = 0.0
        deviations = []
        vwap_now = closes[-1]
        for idx in day_indices:
            typical = (highs[idx] + lows[idx] + closes[idx]) / 3.0
            cum_pv += typical * volumes[idx]
            cum_vol += volumes[idx]
            vwap_i = cum_pv / cum_vol if cum_vol else typical
            deviations.append(closes[idx] - vwap_i)
            vwap_now = vwap_i
        if len(deviations) < 8:
            return None

        mean_dev = sum(deviations) / len(deviations)
        variance = sum((d - mean_dev) ** 2 for d in deviations) / len(deviations)
        sigma = math.sqrt(variance)
        if sigma == 0:
            return None

        current_dev = deviations[-1]
        if current_dev >= -1.2 * sigma:
            return None

        vol_r = volume_ratio(volumes, 20)
        if not (0.6 <= vol_r <= 1.8):
            return None

        atr14 = latest_atr(highs, lows, closes, 14)

        entry = (closes[-1] + vwap_now) / 2  # limit at the midpoint, per spec
        stop = entry - 1.0 * sigma
        target = vwap_now
        if stop >= entry or target <= entry:
            return None

        conf = _clamp(0.4 + min(0.15, (abs(current_dev) / sigma - 1.2) * 0.1))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'vwap': round(vwap_now, 4), 'sigma': round(sigma, 6),
                'deviation': round(current_dev, 4), 'volume_ratio': round(vol_r, 4),
                'atr14': round(atr14, 4),
            },
            entry=entry, stop=stop, target=target, valid_for=1,
        )


# ============ EXPORTS ============

STRATEGY_LAB_STRATEGIES = [
    VolatilitySqueezeDeferral(),
    AVWAPConfluence(),
    WeekendVacuumReversion(),
    DominanceRotation(),
    BreadthDivergenceRotation(),
    FailedBreakoutHarvest(),
    MiddayLiquidityDodge(),
]
