"""Strategy Lab v2: the OHLCV-only subset of references/strategy-lab-v2.md.

The doc defines 18 hypotheses. Only the ones that need nothing beyond
OHLCV + timestamps are implemented here. Skipped and why:

  1.1 Funding Shadow        needs perp funding series aligned to the scanned
                            pair; scan() gets no pair identity, so the funding
                            CSVs in backtest/data cannot be joined safely
  1.4 TradFi Handoff        sign is left to the backtest per pair; needs a
                            per-pair sign table that does not exist yet
  2.2 Gap Context Engine    needs a per-ticker premarket volume percentile table
  2.3 Sector Orphan         needs the sector ETF series alongside the stock
  2.4 Ghost Levels          needs a split history table per ticker
  2.7 Halt Resumption       needs the surprise-scanner lane, not a static file
  3.1 / 3.2 / 3.3           futures sessions, settlements, macro calendar
  5.1 / 5.2                 cross asset by construction

Every strategy here is an UNTESTED hypothesis. Expect most to die in the
graveyard; that is the point of the pipeline.

Interface contract: scan() reads index -1 and earlier only. No future data,
ever. Every bullish signal carries a stop strictly below entry.
"""
import logging
import math
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from strategies.base import Strategy, Signal
from indicators.atr import latest_atr
from strategies.builtin.strategy_lab import (
    _dt, _dt_et, _in_xnys_session, _clamp,
)

logger = logging.getLogger(__name__)


# ============ SHARED HELPERS ============

# ET conversion and the exchange calendar are the two expensive calls in this
# module and both are pure functions of the timestamp. A sliding 260 bar scan
# window asks the same question about the same bar 260 times, so caching turns
# a full series sweep from minutes into seconds.

@lru_cache(maxsize=400_000)
def _et_parts(ts: float) -> Tuple[object, int, int]:
    """(ET calendar date, ET minute of day, ET weekday) for one bar.

    ET, never UTC. A session window expressed in UTC hours selects a
    completely different part of the day in winter vs summer, which has
    already inverted one strategy's hypothesis in this project.
    """
    d = _dt_et(ts)
    return (d.date(), d.hour * 60 + d.minute, d.weekday())


@lru_cache(maxsize=400_000)
def _in_session(ts: float) -> bool:
    """Cached NYSE trading minute check (holidays and half days included)."""
    return _in_xnys_session(ts)


def _ts_seconds(ts: float) -> float:
    """Raw timestamp normalized to epoch seconds regardless of source units."""
    return _dt(ts).timestamp()


# RTH bounds as ET minutes of day: 9:30 = 570, 16:00 = 960.
RTH_OPEN_MIN = 570
RTH_CLOSE_MIN = 960


def _atr(candles: Dict[str, List[float]], period: int = 14) -> float:
    """ATR at the last bar. Prefers the harness precomputed value when the
    caller supplied one, otherwise recomputes from the window."""
    if period == 14:
        pre = candles.get('atr_14')
        if pre:
            try:
                val = float(pre)
                if val > 0:
                    return val
            except (TypeError, ValueError):
                pass
    try:
        return latest_atr(candles['highs'], candles['lows'], candles['closes'], period)
    except Exception:
        return 0.0


RTH_SECONDS = 6.5 * 3600  # one regular equity session


def _session_atr(candles: Dict[str, List[float]]) -> Optional[float]:
    """ATR rescaled from bar scale to session scale (square root of time).

    Needed because the spec quotes several thresholds against a SESSION sized
    ATR ("opening range height between 0.5 and 2.0 ATR"), while the harness
    hands strategies the ATR of whatever bar it is scanning. On 5m bars the
    measured opening range is 2.4x to 6.4x the 5m ATR, so the spec band read
    literally rejects 99% of days and the strategy dies of a units mismatch
    rather than of a bad hypothesis. Scaling by sqrt(bars per session) puts
    the two quantities back on the same footing; on daily-or-slower bars the
    ATR is already session scale and is returned unchanged.
    """
    atr = _atr(candles, 14)
    if atr <= 0:
        return None
    bar_s = _bar_seconds(candles.get('timestamps') or [])
    if not bar_s or bar_s <= 0:
        return None
    bars_per_session = RTH_SECONDS / bar_s
    if bars_per_session <= 1.0:
        return atr
    return atr * math.sqrt(bars_per_session)


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _mean_std(values: List[float]) -> Tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return mean, math.sqrt(var)


def _bar_seconds(timestamps: List[float]) -> Optional[float]:
    """Median spacing between bars, in seconds. None if undeterminable."""
    n = len(timestamps)
    if n < 3:
        return None
    sample = timestamps[-min(n, 40):]
    deltas = []
    for i in range(1, len(sample)):
        d = _ts_seconds(sample[i]) - _ts_seconds(sample[i - 1])
        if d > 0:
            deltas.append(d)
    if not deltas:
        return None
    med = _median(deltas)
    return med if med > 0 else None


def _rth_day_indices(timestamps: List[float]) -> List[int]:
    """Indices of bars sharing the last bar's ET date and inside 9:30-16:00 ET.

    The equity CSVs carry extended hours (04:00-19:55 ET), so a naive
    same date grouping would fold premarket into the opening range.
    """
    n = len(timestamps)
    if n == 0:
        return []
    today, _, _ = _et_parts(timestamps[-1])
    out = []
    i = n - 1
    while i >= 0:
        date, minute, _ = _et_parts(timestamps[i])
        if date != today:
            break
        if RTH_OPEN_MIN <= minute < RTH_CLOSE_MIN:
            out.append(i)
        i -= 1
    out.reverse()
    return out


def _level_increment(price: float) -> Optional[float]:
    """Round level spacing derived from price magnitude.

    DEVIATION: the spec names per instrument spacings (BTC $1000, ETH $100,
    SOL $10) and a separate equity ladder, but scan() is handed candles with
    no pair identity, so spacing has to come from price magnitude alone.
    The ladder below reproduces BTC ($1000), ETH ($100) and the equity rules
    exactly. SOL near $150 lands on $5 instead of $10, which is a superset:
    every $10 level is also a $5 level, so no spec level is lost, only extra
    levels are added.
    """
    if price <= 0 or not math.isfinite(price):
        return None
    if price < 50:
        return 1.0
    if price < 500:
        return 5.0
    if price < 1000:
        return 10.0
    if price < 10000:
        return 100.0
    return 1000.0


class _V2Strategy(Strategy):
    """Base for the v2 lab: scan() never raises, it returns None.

    A strategy that throws inside the sweep would be silently skipped bar by
    bar and look like a dead strategy. The wrapper logs the first failure per
    instance at warning level so a real bug is visible once, not 90,000 times.
    """
    is_entry = True
    _warned = False

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        try:
            if not candles:
                return None
            for key in ('opens', 'highs', 'lows', 'closes', 'volumes', 'timestamps'):
                series = candles.get(key)
                if not series:
                    return None
            return self._scan(candles)
        except Exception as exc:
            if not self._warned:
                self._warned = True
                logger.warning("%s.scan raised (suppressed, returning None): %s",
                               self.name, exc)
            return None

    def _scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        raise NotImplementedError


# ============ 1.2 · Wick Autopsy (Absorption Fingerprint) ============

class WickAutopsy(_V2Strategy):
    """THESIS: repeated lower wick probes that keep closing in the top third of
    range while net price goes nowhere is passive absorption, and absorption
    precedes markup. Enter on the first range expansion out of that pocket.

    DEVIATION 1: the spec does not state a direction filter on the expansion
    candle. Long only here, so the expansion candle must close above its own
    open, otherwise the "markup" read is contradicted by the trigger bar.
    DEVIATION 2: the 60% top-third count is taken over the NON DEGENERATE
    candles in the window. Extended hours equity bars are sometimes flat
    (high == low) and a flat bar has no "top third", so counting it as a
    failure penalised the fingerprint for a data artifact.

    FREQUENCY WARNING (measured 2026-08-13): at the spec's literal 60%
    threshold this fires roughly once per 4,000 bars. It is alive but rare,
    and per-file trade counts will sit under the harness's 20 trade gate;
    judge it pooled across the universe, not per ticker.
    """
    name = "V2_wick_autopsy"

    WINDOW = 20
    MIN_ABSORPTION = 1.6
    MIN_TOP_THIRD_FRAC = 0.60
    FLAT_TAPE_ATR = 0.5
    EXPANSION_MULT = 1.5

    def _scan(self, candles):
        opens = candles['opens']
        highs = candles['highs']
        lows = candles['lows']
        closes = candles['closes']
        n = len(closes)
        w = self.WINDOW
        if n < w + 16:
            return None

        # The absorption window is the w candles BEFORE the trigger bar, so
        # the expansion candle's own body cannot inflate the fingerprint.
        lo = n - 1 - w
        hi = n - 1
        if lo < 0:
            return None

        sum_upper = 0.0
        sum_lower = 0.0
        top_third = 0
        scorable = 0
        ranges = []
        for i in range(lo, hi):
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            rng = h - l
            ranges.append(rng)
            body_hi = max(o, c)
            body_lo = min(o, c)
            sum_upper += max(0.0, h - body_hi)
            sum_lower += max(0.0, body_lo - l)
            if rng > 0:
                scorable += 1
                if c >= l + (2.0 / 3.0) * rng:
                    top_third += 1

        if sum_upper <= 0 or sum_lower <= 0:
            return None
        absorption = sum_lower / sum_upper
        if absorption <= self.MIN_ABSORPTION:
            return None
        if scorable < 15:
            return None  # too many flat bars for the fingerprint to mean anything
        if top_third < math.ceil(self.MIN_TOP_THIRD_FRAC * scorable):
            return None

        # ATR excludes the trigger bar: the expansion candle would otherwise
        # widen ATR and make the flat tape test trivially easy to pass.
        atr = latest_atr(highs[:-1], lows[:-1], closes[:-1], 14)
        if atr <= 0:
            return None
        net_change = abs(closes[hi - 1] - closes[lo])
        if net_change > self.FLAT_TAPE_ATR * atr:
            return None

        median_range = _median(ranges)
        if median_range <= 0:
            return None
        threshold = self.EXPANSION_MULT * median_range

        trigger_range = highs[-1] - lows[-1]
        if trigger_range <= threshold:
            return None
        # "first" expansion candle: the prior bar must not already qualify.
        if ranges and ranges[-1] > threshold:
            return None
        if closes[-1] <= opens[-1]:
            return None

        entry = closes[-1]
        stop = min(lows[lo:hi])
        if stop >= entry:
            return None
        target = entry + 2.0 * (entry - stop)

        conf = _clamp(0.40 + min(0.15, (absorption - self.MIN_ABSORPTION) * 0.10))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'absorption_score': round(absorption, 4),
                'top_third_count': top_third, 'scorable_candles': scorable,
                'net_change_atr': round(net_change / atr, 4),
                'expansion_ratio': round(trigger_range / median_range, 4),
                'zone_low': round(stop, 6), 'atr14': round(atr, 6),
            },
            entry=entry, stop=stop, target=target, valid_for=4,
        )


# ============ 1.3 · Round-Number Defense Decay ============

class RoundNumberDefenseDecay(_V2Strategy):
    """THESIS: round numbers hold because human limit orders rest there, and
    each successive test with a SMALLER rejection wick means that resting
    liquidity is being eaten. Trade the break of a decayed level, or the
    bounce off a level whose defense is getting stronger.

    DEVIATION: level spacing comes from price magnitude (see
    _level_increment) because scan() receives no pair identity. Long only,
    per the spec's long only compatible entry list.
    """
    name = "V2_round_number_decay"

    LOOKBACK = 192
    MIN_TESTS = 2
    DECAY_RATIO = 0.5

    def _tests_at(self, level, highs, lows, closes, lo, hi):
        """Rejection wick sizes at `level` over bars [lo, hi), oldest first.

        A resistance test is a bar that pokes above the level and closes back
        below it; the wick measured is the rejected penetration. A support
        test is the mirror image.
        """
        resistance = []
        support = []
        for i in range(lo, hi):
            if highs[i] > level and closes[i] < level:
                resistance.append(highs[i] - level)
            elif lows[i] < level and closes[i] > level:
                support.append(level - lows[i])
        return resistance, support

    def _scan(self, candles):
        highs = candles['highs']
        lows = candles['lows']
        closes = candles['closes']
        n = len(closes)
        if n < 40:
            return None

        price = closes[-1]
        inc = _level_increment(price)
        if inc is None:
            return None

        atr = _atr(candles, 14)
        if atr <= 0:
            return None

        lo = max(0, n - 1 - self.LOOKBACK)
        hi = n - 1  # history stops before the trigger bar
        if hi - lo < 20:
            return None

        level = math.floor(price / inc) * inc
        if level <= 0:
            return None
        prev_close = closes[-2]

        resistance, support = self._tests_at(level, highs, lows, closes, lo, hi)

        # (a) break above a resistance level whose defense decayed
        if prev_close <= level < price and len(resistance) >= self.MIN_TESTS:
            first_wick, last_wick = resistance[0], resistance[-1]
            if first_wick > 0 and last_wick < self.DECAY_RATIO * first_wick:
                entry = price
                stop = level - 0.25 * atr
                target = level + inc
                if stop < entry and target > entry:
                    conf = _clamp(0.40 + min(0.15, 0.03 * (len(resistance) - 2)))
                    return Signal(
                        pair="", pattern=self.name, direction='bullish',
                        confidence=conf,
                        features={
                            'mode': 'resistance_break', 'level': round(level, 6),
                            'increment': inc, 'tests': len(resistance),
                            'first_wick': round(first_wick, 6),
                            'last_wick': round(last_wick, 6),
                            'atr14': round(atr, 6),
                        },
                        entry=entry, stop=stop, target=target, valid_for=4,
                    )
            return None

        # (b) bounce off a support level whose defense wicks are growing
        if prev_close > level and lows[-1] < level <= price and len(support) >= self.MIN_TESTS:
            first_wick, last_wick = support[0], support[-1]
            if first_wick > 0 and last_wick > first_wick:
                entry = price
                stop = min(lows[-1], level) - 0.25 * atr
                target = level + inc
                if stop < entry and target > entry:
                    conf = _clamp(0.40 + min(0.15, 0.03 * (len(support) - 2)))
                    return Signal(
                        pair="", pattern=self.name, direction='bullish',
                        confidence=conf,
                        features={
                            'mode': 'support_bounce', 'level': round(level, 6),
                            'increment': inc, 'tests': len(support),
                            'first_wick': round(first_wick, 6),
                            'last_wick': round(last_wick, 6),
                            'atr14': round(atr, 6),
                        },
                        entry=entry, stop=stop, target=target, valid_for=4,
                    )
        return None


# ============ 1.5 · Liquidation Echo ============

class LiquidationEcho(_V2Strategy):
    """THESIS: a forced selling cascade that fails to reach the next obvious
    liquidity pool (the prior swing low) is mechanical selling with nothing
    behind it. Buy the first candle that closes above its own open.

    DEVIATION: the spec's volume baseline is 7 days. The harness scan window
    is 260 bars, so on intraday timeframes the baseline is capped at the bars
    available (still ~250 bars) rather than a true 7 days.
    """
    name = "V2_liquidation_echo"

    MIN_CASCADE = 3
    MIN_VOL_Z = 3.0
    SWING_BUFFER_ATR = 0.25

    def _prior_swing_low(self, lows, lo, cascade_start, ref_price):
        """Most recent pivot low before the cascade that sits below the
        cascade's starting price.

        The pivot must be a STRICT minimum of its 3 bar neighbourhood on both
        sides. A non-strict test treats a run of identical lows (flat tape,
        or a thin extended-hours stretch) as a pivot, which silently moves
        the liquidity pool up to the current price and lets the cascade
        "hold above" a level that was never a level.
        """
        for i in range(cascade_start - 4, lo + 2, -1):
            if i - 3 < 0 or i + 4 > cascade_start:
                continue
            left = min(lows[i - 3:i])
            right = min(lows[i + 1:i + 4])
            if lows[i] < left and lows[i] < right and lows[i] < ref_price:
                return lows[i]
        return None

    def _scan(self, candles):
        opens = candles['opens']
        highs = candles['highs']
        lows = candles['lows']
        closes = candles['closes']
        volumes = candles['volumes']
        timestamps = candles['timestamps']
        n = len(closes)
        if n < 45:
            return None

        # Trigger bar: the first candle closing above its own open.
        if closes[-1] <= opens[-1]:
            return None

        # The cascade is the run of down candles ending at the bar before.
        end = n - 2
        i = end
        while i >= 0 and closes[i] < opens[i]:
            i -= 1
        cascade_start = i + 1
        cascade_len = end - cascade_start + 1
        if cascade_len < self.MIN_CASCADE:
            return None

        ranges = [highs[j] - lows[j] for j in range(cascade_start, end + 1)]
        if any(r <= 0 for r in ranges):
            return None
        if ranges[-1] <= ranges[0]:
            return None  # not expanding

        atr = _atr(candles, 14)
        if atr <= 0:
            return None

        # Volume z-score of the heaviest cascade bar vs a trailing baseline.
        bar_s = _bar_seconds(timestamps)
        want = int(7 * 86400 / bar_s) if bar_s else 0
        avail = cascade_start
        baseline_n = min(want, avail) if want else avail
        if baseline_n < 20:
            baseline_n = avail
        if baseline_n < 10:
            return None
        baseline = volumes[cascade_start - baseline_n:cascade_start]
        mean_v, std_v = _mean_std(baseline)
        if std_v <= 0:
            return None
        cascade_vol = max(volumes[cascade_start:end + 1])
        vol_z = (cascade_vol - mean_v) / std_v
        if vol_z <= self.MIN_VOL_Z:
            return None

        cascade_low = min(lows[cascade_start:end + 1])
        cascade_top = max(highs[cascade_start:end + 1])
        if cascade_top <= cascade_low:
            return None

        lo = max(0, cascade_start - 200)
        swing_low = self._prior_swing_low(lows, lo, cascade_start, opens[cascade_start])
        if swing_low is None:
            return None
        if cascade_low < swing_low + self.SWING_BUFFER_ATR * atr:
            return None  # cascade reached the pool; not an exhausted echo

        entry = closes[-1]
        stop = cascade_low
        target = cascade_low + 0.5 * (cascade_top - cascade_low)
        if stop >= entry or target <= entry:
            return None

        conf = _clamp(0.40 + min(0.15, (vol_z - self.MIN_VOL_Z) * 0.02))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'cascade_len': cascade_len, 'volume_z': round(vol_z, 4),
                'cascade_low': round(cascade_low, 6),
                'cascade_top': round(cascade_top, 6),
                'prior_swing_low': round(swing_low, 6),
                'baseline_bars': baseline_n, 'atr14': round(atr, 6),
            },
            entry=entry, stop=stop, target=target, valid_for=4,
        )


# ============ 2.1 · Second-Break Verdict ============

class SecondBreakVerdict(_V2Strategy):
    """THESIS: the first opening range break is where breakout stops and chase
    orders concentrate, which is why it fails. Long only variant: treat a
    break below the opening range low that closes back inside within two
    candles as the bait, and buy the reclaim toward the OR high.

    DEVIATION: the "OR height between 0.5 and 2.0 ATR" band is measured
    against a SESSION scale ATR (see _session_atr), not the raw 5m/15m bar
    ATR. Measured on real data the opening range is 2.4x to 6.4x the 5m bar
    ATR, so the literal band passed 0% of AAPL days and 1% of SPY days. That
    is a units mismatch, not a verdict on the hypothesis.
    """
    name = "V2_second_break"

    OR_END_MIN = 600  # 10:00 ET
    MIN_OR_ATR = 0.5
    MAX_OR_ATR = 2.0
    MAX_BREAK_BARS = 2

    def _scan(self, candles):
        highs = candles['highs']
        lows = candles['lows']
        closes = candles['closes']
        timestamps = candles['timestamps']
        n = len(closes)
        if n < 20:
            return None
        if not _in_session(timestamps[-1]):
            return None

        _, minute, _ = _et_parts(timestamps[-1])
        if minute < self.OR_END_MIN:
            return None

        day = _rth_day_indices(timestamps)
        if len(day) < 8 or day[-1] != n - 1:
            return None

        or_idx = [i for i in day if _et_parts(timestamps[i])[1] < self.OR_END_MIN]
        post = [i for i in day if _et_parts(timestamps[i])[1] >= self.OR_END_MIN]
        if not or_idx or len(post) < 2:
            return None

        or_high = max(highs[i] for i in or_idx)
        or_low = min(lows[i] for i in or_idx)
        or_width = or_high - or_low
        if or_width <= 0:
            return None

        atr = _session_atr(candles)
        if not atr or atr <= 0:
            return None
        or_atr = or_width / atr
        if not (self.MIN_OR_ATR <= or_atr <= self.MAX_OR_ATR):
            return None

        cur = post[-1]
        close_now = closes[cur]
        if not (or_low <= close_now <= or_high):
            return None  # not back inside the range

        # Count the run of bars immediately before the trigger that closed
        # below the OR low. 1 or 2 means the failed break is fresh.
        pos = len(post) - 1
        break_bars = []
        k = pos - 1
        while k >= 0 and closes[post[k]] < or_low:
            break_bars.append(post[k])
            k -= 1
        if not (1 <= len(break_bars) <= self.MAX_BREAK_BARS):
            return None

        failed_low = min(lows[i] for i in break_bars)
        entry = close_now
        stop = failed_low - 0.1 * or_width
        target = or_high
        if stop >= entry or target <= entry:
            return None

        conf = _clamp(0.42 + (0.05 if len(break_bars) == 1 else 0.0))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'or_high': round(or_high, 6), 'or_low': round(or_low, 6),
                'or_width_atr': round(or_atr, 4),
                'break_bars': len(break_bars),
                'failed_low': round(failed_low, 6), 'session_atr': round(atr, 6),
            },
            entry=entry, stop=stop, target=target, valid_for=3,
        )


# ============ 2.5 · Volume Desert Breakout ============

class VolumeDesertBreakout(_V2Strategy):
    """THESIS: 12:00-13:30 ET is the volume desert. A directional move on 2x
    desert baseline volume means someone is deliberately working an order
    while nobody is watching, and informed flow chooses quiet tape.

    DEVIATION 1: the spec's SPY flat filter (index 30m move < 0.15%) is
    OMITTED. It needs a second ticker's series and the harness passes one
    instrument's candles only. Idiosyncratic flow is therefore not isolated
    from index level moves; expect more signals than the spec implies.
    DEVIATION 2: the lunch baseline is built from the prior sessions visible
    inside the 260 bar scan window, not a long per ticker table.
    DEVIATION 3: the 1 ATR stop is a SESSION scale ATR (see _session_atr).
    One 5m bar's ATR is noise-sized, so a literal reading would stop every
    trade out within minutes and measure slippage rather than the thesis.
    """
    name = "V2_volume_desert"

    LUNCH_START = 720  # 12:00 ET
    LUNCH_END = 810    # 13:30 ET
    MIN_BASELINE_BARS = 6
    VOL_MULT = 2.0
    RANGE_MULT = 1.5

    def _scan(self, candles):
        opens = candles['opens']
        highs = candles['highs']
        lows = candles['lows']
        closes = candles['closes']
        volumes = candles['volumes']
        timestamps = candles['timestamps']
        n = len(closes)
        if n < 40:
            return None
        if not _in_session(timestamps[-1]):
            return None

        today, minute, _ = _et_parts(timestamps[-1])
        if not (self.LUNCH_START <= minute <= self.LUNCH_END):
            return None

        base_vol = []
        base_rng = []
        for i in range(n - 1):
            date, m, _ = _et_parts(timestamps[i])
            if date == today:
                continue
            if self.LUNCH_START <= m <= self.LUNCH_END:
                rng = highs[i] - lows[i]
                if rng > 0 and volumes[i] > 0:
                    base_vol.append(volumes[i])
                    base_rng.append(rng)
        if len(base_vol) < self.MIN_BASELINE_BARS:
            return None

        med_vol = _median(base_vol)
        med_rng = _median(base_rng)
        if med_vol <= 0 or med_rng <= 0:
            return None

        cur_vol = volumes[-1]
        cur_rng = highs[-1] - lows[-1]
        if cur_vol <= self.VOL_MULT * med_vol:
            return None
        if cur_rng <= self.RANGE_MULT * med_rng:
            return None
        if closes[-1] <= opens[-1]:
            return None  # long only: move direction must be up

        atr = _session_atr(candles)
        if not atr or atr <= 0:
            return None

        entry = closes[-1]
        stop = entry - 1.0 * atr
        if stop >= entry:
            return None

        conf = _clamp(0.40 + min(0.15, (cur_vol / med_vol - self.VOL_MULT) * 0.03))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'volume_mult': round(cur_vol / med_vol, 4),
                'range_mult': round(cur_rng / med_rng, 4),
                'baseline_bars': len(base_vol),
                'lunch_median_volume': round(med_vol, 4),
                'session_atr': round(atr, 6),
            },
            # Target None on purpose: the exit is a 1 ATR trail supplied by
            # the harness exit config, not a fixed price.
            entry=entry, stop=stop, target=None, valid_for=3,
        )


# ============ 2.6 · VWAP Magnet Close ============

class VWAPMagnetClose(_V2Strategy):
    """THESIS: execution desks are benchmarked to VWAP, so late in the session
    an unfilled benchmark order pulls a stretched price back. Long only
    variant: price more than 0.75 ATR BELOW session VWAP at 15:30 gets bought
    toward VWAP.

    DEVIATION 1: the trigger is the first bar of the day inside 15:30-15:45
    ET rather than an exact 15:30 stamp, so the rule still fires on bar sizes
    that have no bar stamped exactly 15:30.
    NO DEVIATION on scale: the 0.75 ATR stretch and 0.5 ATR stop are read
    against the BAR ATR, deliberately. Measured on real data, price sits more
    than 0.75 session-scale ATR below VWAP at 15:30 on ~1.5% of ticker-days,
    while the bar-ATR reading fires on ~35%, which is what the spec's own
    "30-40% of ticker-days" estimate describes. Bar scale is the intended
    reading.

    FEE FLAG (spec section 2.6 and integration note 3): the resulting target
    distance is a median 0.14% (SPY) to 0.32% (AAPL) of price, against a
    round-trip cost near 0.3%. The fee-to-edge gate is expected to veto most
    days. Judge this one on gate-passing days only.
    """
    name = "V2_vwap_magnet"

    TRIGGER_START = 930  # 15:30 ET
    TRIGGER_END = 945    # 15:45 ET
    MIN_STRETCH_ATR = 0.75
    STOP_ATR = 0.5

    def __init__(self, use_session_atr: bool = False):
        """ATR scale is a genuine judgement call, so BOTH readings are
        registered and the graveyard settles it empirically rather than
        either of us guessing. Bar scale fires ~35% of ticker-days (matching
        the spec's own 30-40% estimate) with targets near the fee floor;
        session scale fires ~1.5% with targets that clear costs easily. One
        is frequent-and-marginal, the other rare-and-meaningful."""
        self.use_session_atr = use_session_atr
        if use_session_atr:
            self.name = "V2_vwap_magnet_sessionatr"

    def _scan(self, candles):
        highs = candles['highs']
        lows = candles['lows']
        closes = candles['closes']
        volumes = candles['volumes']
        timestamps = candles['timestamps']
        n = len(closes)
        if n < 30:
            return None
        if not _in_session(timestamps[-1]):
            return None

        _, minute, _ = _et_parts(timestamps[-1])
        if not (self.TRIGGER_START <= minute < self.TRIGGER_END):
            return None

        day = _rth_day_indices(timestamps)
        if len(day) < 10 or day[-1] != n - 1:
            return None
        # Only the first bar of the day inside the trigger window counts.
        for i in day[:-1]:
            if self.TRIGGER_START <= _et_parts(timestamps[i])[1] < self.TRIGGER_END:
                return None

        cum_pv = 0.0
        cum_vol = 0.0
        for i in day:
            typical = (highs[i] + lows[i] + closes[i]) / 3.0
            cum_pv += typical * volumes[i]
            cum_vol += volumes[i]
        if cum_vol <= 0:
            return None
        vwap = cum_pv / cum_vol

        atr = _session_atr(candles) if self.use_session_atr else _atr(candles, 14)
        if not atr or atr <= 0:
            return None

        entry = closes[-1]
        stretch = vwap - entry
        if stretch <= self.MIN_STRETCH_ATR * atr:
            return None

        stop = entry - self.STOP_ATR * atr
        target = vwap
        if stop >= entry or target <= entry:
            return None

        conf = _clamp(0.40 + min(0.15, (stretch / atr - self.MIN_STRETCH_ATR) * 0.10))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'vwap': round(vwap, 6),
                'stretch_atr': round(stretch / atr, 4),
                'session_bars': len(day), 'atr14': round(atr, 6),
            },
            entry=entry, stop=stop, target=target, valid_for=3,
        )


# ============ 4.1 · Expiry Pin Drift ============

class ExpiryPinDrift(_V2Strategy):
    """THESIS: on expiry days dealer gamma hedging pins the underlying toward
    high open interest strikes, which cluster at round numbers. No options
    data needed to harvest the average effect. Long only: buy when price sits
    just BELOW the nearest strike after 14:00.

    DEVIATION: strike spacing uses the shared magnitude ladder (see
    _level_increment) since scan() has no ticker identity and therefore no
    real strike chain.
    """
    name = "V2_expiry_pin"

    EXPIRY_WEEKDAYS = (0, 2, 4)  # Monday, Wednesday, Friday
    START_MIN = 840   # 14:00 ET
    END_MIN = 955     # 15:55 ET, never carry into the auction
    BAND = 0.003      # within 0.3% of the strike
    STOP_PCT = 0.005  # 0.5% adverse stop

    def _scan(self, candles):
        closes = candles['closes']
        timestamps = candles['timestamps']
        n = len(closes)
        if n < 20:
            return None
        if not _in_session(timestamps[-1]):
            return None

        _, minute, weekday = _et_parts(timestamps[-1])
        if weekday not in self.EXPIRY_WEEKDAYS:
            return None
        if not (self.START_MIN <= minute < self.END_MIN):
            return None

        price = closes[-1]
        inc = _level_increment(price)
        if inc is None:
            return None

        strike = math.ceil(price / inc) * inc
        distance = strike - price
        if distance <= 0:
            return None  # exactly on the strike: no drift left to harvest
        if distance > self.BAND * price:
            return None

        entry = price
        target = strike
        stop = entry * (1.0 - self.STOP_PCT)
        if stop >= entry or target <= entry:
            return None

        conf = _clamp(0.40 + min(0.12, (self.BAND - distance / price) * 30))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'strike': round(strike, 6), 'increment': inc,
                'distance_pct': round(distance / price, 6),
                'et_minute': minute, 'weekday': weekday,
            },
            entry=entry, stop=stop, target=target, valid_for=3,
        )


# ============ 4.2 · 0DTE Afternoon Amplifier ============

class ZeroDTEAmplifier(_V2Strategy):
    """THESIS: on 0DTE days dealers can flip to negative gamma in the
    afternoon and hedge WITH the move, so a morning range break after 14:00
    accelerates instead of reverting. The natural adversary of 4.1.

    DEVIATION: long side only, so only breaks above the morning range high
    are taken. The spec's 1.5R trail is expressed as a fixed 1.5R target
    because the trail lives in the harness exit config.
    """
    name = "V2_0dte_amplifier"

    EXPIRY_WEEKDAYS = (0, 2, 4)
    MORNING_END = 720  # 12:00 ET
    START_MIN = 840    # 14:00 ET
    END_MIN = 955      # 15:55 ET

    def _scan(self, candles):
        highs = candles['highs']
        lows = candles['lows']
        closes = candles['closes']
        timestamps = candles['timestamps']
        n = len(closes)
        if n < 25:
            return None
        if not _in_session(timestamps[-1]):
            return None

        _, minute, weekday = _et_parts(timestamps[-1])
        if weekday not in self.EXPIRY_WEEKDAYS:
            return None
        if not (self.START_MIN <= minute < self.END_MIN):
            return None

        day = _rth_day_indices(timestamps)
        if len(day) < 10 or day[-1] != n - 1:
            return None

        morning = [i for i in day if _et_parts(timestamps[i])[1] < self.MORNING_END]
        midday = [i for i in day
                  if self.MORNING_END <= _et_parts(timestamps[i])[1] < self.START_MIN]
        if len(morning) < 3 or len(midday) < 1:
            return None

        mr_high = max(highs[i] for i in morning)
        mr_low = min(lows[i] for i in morning)
        mr_range = mr_high - mr_low
        if mr_range <= 0:
            return None

        # The morning range must have HELD through midday.
        for i in midday:
            if highs[i] > mr_high or lows[i] < mr_low:
                return None

        after = [i for i in day if _et_parts(timestamps[i])[1] >= self.START_MIN]
        if len(after) < 2:
            return None
        if closes[-1] <= mr_high:
            return None
        # First break only: every earlier afternoon bar closed inside.
        for i in after[:-1]:
            if closes[i] > mr_high:
                return None

        entry = closes[-1]
        stop = mr_high - 0.25 * mr_range
        if stop >= entry:
            return None
        target = entry + 1.5 * (entry - stop)

        conf = _clamp(0.40 + min(0.12, (entry - mr_high) / mr_range * 0.3))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'morning_high': round(mr_high, 6), 'morning_low': round(mr_low, 6),
                'morning_range': round(mr_range, 6),
                'break_size_range': round((entry - mr_high) / mr_range, 4),
                'et_minute': minute, 'weekday': weekday,
            },
            entry=entry, stop=stop, target=target, valid_for=3,
        )


# ============ EXPORTS ============

STRATEGY_LAB_V2_STRATEGIES = [
    WickAutopsy(),
    RoundNumberDefenseDecay(),
    LiquidationEcho(),
    SecondBreakVerdict(),
    VolumeDesertBreakout(),
    VWAPMagnetClose(),                    # bar-scale ATR (frequent, marginal targets)
    VWAPMagnetClose(use_session_atr=True),  # session-scale ATR (rare, meaningful targets)
    ExpiryPinDrift(),
    ZeroDTEAmplifier(),
]
