"""Strategy Lab v4 Stage 1: the three DEEP RENT ignitions traded in SHARES.

Source: references/strategy-lab-v4.md. Assessment:
docs/STRATEGY-LAB-V3-V4-ASSESSMENT.md, whose recommendation this file
implements verbatim:

    "implement the three v4 IGNITIONS as share strategies first. They are
    testable today, they are the falsifiable core, and the LEAPS wrapper is
    a sizing/leverage decision layered on top of a signal that must work
    regardless."

So NOTHING here is an options strategy. No IV rank gate, no vol gate, no
Greeks - those protect the STRUCTURE choice (which option to rent), and there
is no structure yet. If these ignitions have no edge in shares, no LEAPS
wrapper rescues them (v4 doc, Part 4, monitor 4). If they do, Stage 1 of the
options overlay gets a signal worth wrapping.

WHAT EACH IGNITION BETS ON (and the citation that pays for the test):

  I1  PEAD gap-and-hold      Bernard & Thomas 1989; disputed by Martineau
                             2022, defended by 2025 papers. EXPLICITLY
                             CONTESTED - our universe is the arbiter.
  I2  52-week-high breakout  George & Hwang, JF 2004: proximity to the 52w
                             high predicts continuation (anchoring).
  I3  Trend reclaim          12-1 momentum + long MA reclaim; the most
                             durable multi-week tailwind in the time-series
                             literature.

DEVIATIONS FROM THE DOC (each one deliberate and visible):

1. I1 IS A PRICE-ONLY PROXY. The doc's I1 keys on a top-decile STANDARDIZED
   EARNINGS SURPRISE with a gap-and-hold day-one close. There is no earnings
   calendar in this project yet (queued, assessment item 3), so this
   implementation detects the PRICE SIGNATURE only: a large overnight gap up
   that holds into the close on heavy volume. Some of those gaps are
   earnings; many are not. A PASS or FAIL here is a verdict on gap-and-hold
   momentum, NOT on PEAD proper. The strategy name says _proxy so no report
   can quietly drop the word.
2. WEEKLY BARS FOR I2 AND I3. The graveyard tests the LAST 20% of each
   series; daily test slices run ~250 bars, and a faithful 52-week lookback
   needs ~253 daily bars of warmup - the strategy would be structurally
   untestable (0-2 evaluable bars per ticker). On weekly bars the same
   lookback is 52 bars inside a ~157-bar test slice, so the FAITHFUL horizon
   fits the harness. This is a translation, not a reinterpretation: the
   doc's own trigger for I2 is written in weeks ("first weekly close at a
   new 52-week high after >=8 weeks below it").
3. NO VOL GATE, NO SUBLET, NO EXIT ENGINE. Share-lane Stage 1 tests the
   ignition only. Exits are the graveyard's standard exit configs; the
   doc's 6-10 week ride maps naturally onto time_8c on weekly bars.
4. Ignitions are SEPARATE strategies (separate graveyard cohorts), matching
   the doc's cohort-mortality monitor: any ignition retires independently.

Interface contract (same as v2/v3): scan() reads index -1 and earlier only,
never raises, returns None or a bullish Signal whose stop is strictly below
entry. Conditions were fixed by the doc BEFORE testing (standing rule 4);
thresholds below cite their source line.
"""
import logging
import math
from typing import Dict, List, Optional

from strategies.base import Strategy, Signal
from strategies.builtin.strategy_lab_v2 import _bar_seconds, _median

logger = logging.getLogger(__name__)

_DAY_S = 86_400.0
_WEEK_S = 7 * _DAY_S


def _is_daily(candles: Dict[str, List[float]]) -> bool:
    """True when bars are day-spaced (weekend gaps keep the median at ~1-3
    days; anything under half a week reads as daily here, intraday is
    caught separately)."""
    s = _bar_seconds(candles.get('timestamps') or [])
    return s is not None and 0.75 * _DAY_S <= s < 0.5 * _WEEK_S


def _is_weekly(candles: Dict[str, List[float]]) -> bool:
    s = _bar_seconds(candles.get('timestamps') or [])
    return s is not None and 0.75 * _WEEK_S <= s <= 1.6 * _WEEK_S


class _V4Strategy(Strategy):
    """Same never-raise contract as _V2Strategy/_V3Strategy: a strategy that
    throws inside a sweep reads as a dead strategy, which is how a bug gets
    mistaken for a verdict."""
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
            n = len(candles['closes'])
            for key in ('opens', 'highs', 'lows', 'volumes', 'timestamps'):
                if len(candles[key]) != n:
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

    def _long(self, entry: float, stop: float, confidence: float,
              features: dict) -> Optional[Signal]:
        """Bullish signal with the doc's thesis stop; 2R target per harness
        convention. Refuses degenerate geometry instead of emitting it."""
        if not (math.isfinite(entry) and math.isfinite(stop)):
            return None
        if stop >= entry or entry <= 0:
            return None
        return Signal(pair='', pattern=self.name, direction='bullish',
                      confidence=confidence, features=features, entry=entry,
                      stop=stop, target=entry + 2 * (entry - stop),
                      valid_for=1)


# ============ I1 · PEAD gap-and-hold (PRICE-ONLY PROXY, daily bars) ============

class GapAndHoldProxy(_V4Strategy):
    """v4 Module B, I1 - as a price-only proxy (DEVIATION 1 above).

    Doc trigger: "a top-decile standardized earnings surprise (SUE) with a
    gap-and-hold day-one close in the upper half of the day's range, buy on
    day 2-3 and ride the drift 6-9 weeks."

    Proxy trigger, on daily bars only:
      - today's OPEN gaps >= GAP_MIN above yesterday's close (the
        announcement signature; no earnings data, so no SUE)
      - today's CLOSE holds in the upper half of today's range
        (the doc's own gap-AND-HOLD condition, verbatim)
      - today's volume >= VOL_MULT x trailing 20-bar mean (announcements
        trade heavy; filters drift-up opens)
    Entry at the close of the signal day. The doc buys day 2-3; entry at
    day-one close is the nearest expressible moment - one bar earlier, if
    anything a HARSHER test of drift (more room to mean-revert).

    Thesis stop (doc, exit door 4): "below the earnings gap fill" - the
    pre-gap close IS the gap-fill level.
    """
    name = "V4_gap_hold_proxy"
    min_bars = 25

    GAP_MIN = 0.03      # >=3% overnight gap: large-surprise territory on the
                        # liquid names the doc's chassis restricts to
    VOL_MULT = 1.5      # same surge multiple the confirmation stack uses
    UPPER_HALF = 0.5    # doc: close in the upper half of the day's range

    def _scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        if not _is_daily(candles):
            return None
        closes, opens = candles['closes'], candles['opens']
        highs, lows, vols = candles['highs'], candles['lows'], candles['volumes']
        n = len(closes)
        if n < self.min_bars:
            return None

        prev_close = closes[-2]
        o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
        if prev_close <= 0 or h <= l:
            return None

        gap = o / prev_close - 1.0
        if gap < self.GAP_MIN:
            return None
        range_pos = (c - l) / (h - l)
        if range_pos < self.UPPER_HALF:
            return None
        base_vol = sum(vols[-21:-1]) / 20.0
        if base_vol <= 0 or vols[-1] < self.VOL_MULT * base_vol:
            return None

        stop = prev_close * 0.999          # a hair below the gap-fill level
        return self._long(c, stop, confidence=0.5,
                          features={'gap_pct': round(gap * 100, 2),
                                    'range_pos': round(range_pos, 2),
                                    'vol_mult': round(vols[-1] / base_vol, 2),
                                    'proxy_for': 'PEAD (no earnings data)'})


# ============ I2 · 52-week-high breakout (weekly bars, faithful) ============

class FiftyTwoWeekHighBreakout(_V4Strategy):
    """v4 Module B, I2. George & Hwang (JF 2004): nearness to the 52-week
    high predicts continuation at multi-week horizons; traders anchor on the
    salient reference price and under-react beside it.

    Doc trigger, implemented on the doc's own clock (weekly bars):
      - first weekly close ABOVE the prior 52-week high...
      - ...after >= 8 consecutive weeks in which no such close happened
      - recent volume (4-week mean) above its 6-month (26-week) median

    Thesis stop (doc, exit door 4): "weekly close back below the prior 52w
    high" - the breakout level itself.
    """
    name = "V4_52w_high_breakout"
    min_bars = 62         # 52w lookback + 8w quiet spell + margins

    LOOKBACK = 52
    QUIET_WEEKS = 8
    VOL_WINDOW = 4
    VOL_MEDIAN_WINDOW = 26

    def _new_high(self, closes: List[float], highs: List[float], i: int) -> bool:
        """Did bar i close above the max high of the LOOKBACK bars before it?
        (i is a negative index; python slicing keeps this lookahead-safe:
        the window ends strictly before bar i.)"""
        window = highs[i - self.LOOKBACK:i]
        return bool(window) and closes[i] > max(window)

    def _scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        if not _is_weekly(candles):
            return None
        closes, highs, vols = candles['closes'], candles['highs'], candles['volumes']
        n = len(closes)
        if n < self.min_bars:
            return None

        if not self._new_high(closes, highs, -1):
            return None
        # ">= 8 weeks below it": none of the prior QUIET_WEEKS bars was
        # itself a new-52w-high close (first breakout, not the third).
        for back in range(2, self.QUIET_WEEKS + 2):
            if self._new_high(closes, highs, -back):
                return None
        recent_vol = sum(vols[-self.VOL_WINDOW:]) / self.VOL_WINDOW
        vol_median = _median(list(vols[-self.VOL_MEDIAN_WINDOW:]))
        if vol_median is None or vol_median <= 0 or recent_vol <= vol_median:
            return None

        breakout_level = max(highs[-1 - self.LOOKBACK:-1])
        return self._long(closes[-1], breakout_level, confidence=0.55,
                          features={'breakout_level': round(breakout_level, 4),
                                    'vol_vs_median': round(recent_vol / vol_median, 2)})


# ============ I3 · Trend reclaim (weekly translation) ============

class TrendReclaim(_V4Strategy):
    """v4 Module B, I3. The doc's "boring signal": price crosses and holds
    above a rising 100-day MA after >= 4 weeks below it, with 12-1 month
    momentum non-negative.

    Weekly translation (DEVIATION 2 above): 100 trading days ~= 20 weeks, so
    the reclaim MA is a 20-week SMA; ">= 4 weeks below" is 4 weekly closes
    below it; 12-1 momentum is close[-5]/close[-53] (skip the most recent
    month, standard momentum construction).

    Thesis stop (doc, exit door 4): "close below the 100-day MA" - the MA
    value at entry.
    """
    name = "V4_trend_reclaim"
    min_bars = 58          # 53 for 12-1 momentum + reclaim window

    MA_WEEKS = 20
    BELOW_WEEKS = 4
    MOM_SKIP = 4           # skip most recent ~1 month of weeks
    MOM_TOTAL = 52         # 12 months of weeks

    @staticmethod
    def _sma(closes: List[float], end: int, n: int) -> Optional[float]:
        """SMA of the n bars ending AT (and including) negative index end."""
        seg = closes[end - n + 1:] if end == -1 else closes[end - n + 1:end + 1]
        if len(seg) < n:
            return None
        return sum(seg) / n

    def _scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        if not _is_weekly(candles):
            return None
        closes = candles['closes']
        n = len(closes)
        if n < self.min_bars:
            return None

        ma_now = self._sma(closes, -1, self.MA_WEEKS)
        ma_prev = self._sma(closes, -1 - self.BELOW_WEEKS, self.MA_WEEKS)
        if ma_now is None or ma_prev is None:
            return None
        if closes[-1] <= ma_now:
            return None
        if ma_now <= ma_prev:                      # MA must be rising
            return None
        # ">= 4 weeks below": the four closes before this one all sat below
        # their own concurrent MA.
        for back in range(2, self.BELOW_WEEKS + 2):
            ma_b = self._sma(closes, -back, self.MA_WEEKS)
            if ma_b is None or closes[-back] >= ma_b:
                return None
        # 12-1 momentum non-negative
        past = closes[-(self.MOM_TOTAL + 1)]
        recent = closes[-(self.MOM_SKIP + 1)]
        if past <= 0 or recent / past - 1.0 < 0.0:
            return None

        return self._long(closes[-1], ma_now, confidence=0.5,
                          features={'ma20w': round(ma_now, 4),
                                    'mom_12_1': round(recent / past - 1.0, 4)})


# ============ EXPORTS ============

STRATEGY_LAB_V4_STRATEGIES = [
    GapAndHoldProxy(),
    FiftyTwoWeekHighBreakout(),
    TrendReclaim(),
]
