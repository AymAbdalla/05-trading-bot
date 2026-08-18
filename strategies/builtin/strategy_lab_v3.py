"""Strategy Lab v3: the three OHLCV-only strategies from
references/strategy-lab-v3.md.

The doc defines five strategies. Only three are implementable against the
current harness, which hands scan() ONE instrument's candles and nothing
else. Skipped and why:

  Strategy 3  Same-Clock Echo        cross-sectional by construction: it ranks
                                     ticker x half-hour-slot cells against each
                                     other. There is no cross-sectional harness
                                     and scan() has no ticker identity.
  Strategy 5  Paid Liquidity Reversal cross-sectional (bottom-quintile residual
                                     losers within the core list) AND needs the
                                     sector ETF series plus an earnings calendar.
  Bonus       Attention Gap Fade      requires shorting plus a premarket volume
                                     table per ticker.

Everything here is an UNTESTED hypothesis with a peer-reviewed anchor. The
anchor buys the hypothesis a fair test, not a pass.

Interface contract: scan() reads index -1 and earlier only. No future data,
ever. Every bullish signal carries a stop strictly below entry and a target
above it.

TIMEFRAME NOTE (measured 2026-08-13, so nobody re-opens the investigation):
all five strategies emit ZERO signals on 1d bars, by construction and not by
defect. Both intraday momentum variants and both macro drift variants are
anchored to a clock inside the day (15:30 ET, 23:30 UTC, 14:00 ET) and a
daily bar carries no such stamp. Vacuum Refill does run on 1d bars, but on
BTC_USD_1d its volume z > 3 gate passes only 3 times in 731 bars and none of
those 3 clear the realized-vol percentile. Measured counts on 5m / 15m data
are in the build handoff.
"""
import json
import logging
import math
import os
from datetime import date as _date, timedelta
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from strategies.base import Strategy, Signal
from strategies.builtin.strategy_lab import (
    _dt, _clamp, _percentile_rank,
)
from strategies.builtin.strategy_lab_v2 import (
    _et_parts, _in_session, _atr, _bar_seconds, _mean_std,
    _rth_day_indices, _session_atr, RTH_OPEN_MIN, RTH_CLOSE_MIN,
)

logger = logging.getLogger(__name__)


# ============ MACRO CALENDAR (loaded ONCE at import) ============

_AUX_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'backtest', 'data', 'aux',
)
_MACRO_CALENDAR_PATH = os.path.join(_AUX_DIR, 'macro_calendar.json')


def _load_macro_calendar(path: str = _MACRO_CALENDAR_PATH):
    """Read the static macro calendar into date sets, one time only.

    Reading this file inside scan() would mean ~95,000 disk reads per CSV
    sweep. It is a static published schedule, so import time is the right
    time. A missing or malformed file degrades to empty sets: the macro
    strategies then emit nothing rather than crashing the whole sweep.
    """
    fomc, nfp, cpi = set(), set(), set()
    try:
        with open(path, 'r') as fh:
            payload = json.load(fh)
        for event in payload.get('events', []):
            try:
                y, m, d = (int(part) for part in str(event['date']).split('-'))
                day = _date(y, m, d)
            except (KeyError, ValueError, TypeError):
                continue
            kind = str(event.get('event', '')).upper()
            if kind == 'FOMC':
                fomc.add(day)
            elif kind == 'NFP':
                nfp.add(day)
            elif kind == 'CPI':
                cpi.add(day)
    except Exception as exc:  # missing file, bad JSON, permissions
        logger.warning("strategy_lab_v3: macro calendar unavailable (%s); "
                       "macro strategies will emit no signals", exc)
    return frozenset(fomc), frozenset(nfp), frozenset(cpi)


FOMC_DATES, NFP_DATES, CPI_DATES = _load_macro_calendar()
# DELIBERATELY excludes CPI: IntradayMomentum's macro-day amplifier was swept
# against FOMC|NFP only, and the 2026-08-13 graveyard run started under that
# definition. Widening it mid-run would silently change an already-tested
# strategy's semantics (the gate-version sin, in miniature). Fold CPI in at
# the next FULL graveyard rebuild, not before.
MACRO_DATES = FOMC_DATES | NFP_DATES

_ONE_DAY = timedelta(days=1)


# ============ SHARED HELPERS ============

UTC_DAY_SECONDS = 24 * 3600


@lru_cache(maxsize=400_000)
def _utc_parts(ts: float) -> Tuple[object, int]:
    """(UTC calendar date, UTC minute of day) for one bar.

    Only the deliberate crypto variant of Strategy 2 uses this. Every other
    time anchored rule in this module goes through _et_parts, because a
    session window expressed in UTC hours selects a different part of the
    equity day in winter than in summer.
    """
    d = _dt(ts)
    return (d.date(), d.hour * 60 + d.minute)


def _horizon_atr(candles: Dict[str, List[float]], horizon_seconds: float) -> Optional[float]:
    """Bar ATR rescaled to an arbitrary holding horizon (square root of time).

    The v2 helper _session_atr hard codes the 6.5 hour equity session. The
    crypto variant of Strategy 2 holds across a 24 hour "day", so it needs
    the same square-root-of-time rescaling against a different horizon.
    Without it, a 0.5 ATR stop quoted at day scale would be read as 0.5 of a
    15 minute bar's ATR and every trade would be stopped out on noise.
    """
    atr = _atr(candles, 14)
    if atr <= 0 or not math.isfinite(atr):
        return None
    bar_s = _bar_seconds(candles.get('timestamps') or [])
    if not bar_s or bar_s <= 0:
        return None
    bars = horizon_seconds / bar_s
    if bars <= 1.0:
        return atr
    return atr * math.sqrt(bars)


def _log_return(prev: float, cur: float) -> Optional[float]:
    if prev is None or cur is None:
        return None
    if prev <= 0 or cur <= 0:
        return None
    if not (math.isfinite(prev) and math.isfinite(cur)):
        return None
    return math.log(cur / prev)


class _V3Strategy(Strategy):
    """Base for the v3 lab: scan() never raises, it returns None.

    Same contract as _V2Strategy. A strategy that throws inside the sweep is
    silently skipped bar by bar and reads as a dead strategy in the
    graveyard, which is exactly how a bug gets mistaken for a verdict. The
    wrapper logs the first failure per instance so a real bug is visible
    once, not 90,000 times.
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


# ============ STRATEGY 2 · "The 3:30 Verdict" (market intraday momentum) ============

class IntradayMomentum(_V3Strategy):
    """THESIS: the first half hour's return, measured from the prior close,
    predicts the last half hour's return. The effect concentrates on high
    volatility, high volume and macro-release days, and strengthens when the
    twelfth half hour (15:00-15:30) agrees in sign with the first. Buy the
    close on agreement days that are in an amplifier state.

    CITATION: Gao, Han, Li and Zhou, "Market Intraday Momentum", Journal of
    Financial Economics 129(2), 2018.

    DOCUMENTED EFFECT SIZE: predictive R-squared of 1.6% for the first half
    hour alone on SPY 1993-2013, rising to 2.6% once the twelfth half hour is
    added, which matches or beats typical MONTHLY return predictors. Present
    across ten other heavily traded ETFs. The doc notes post-2013 weakening,
    so the decay monitor (rolling 250 day directional hit rate must hold
    above 52.5%) is part of the genome, not an afterthought.

    DEVIATION 1 (EXIT, the big one): the paper and the doc both exit FLAT at
    15:58 the same day. This harness has no forced intraday time exit, so the
    trade is expressed as the doc's "0.5x ATR disaster stop" plus a mirrored
    0.5x ATR target. The position therefore survives into subsequent bars and
    days instead of being closed at the bell, which changes the risk profile
    materially. When sweeping this strategy the closest available analogs are
    the harness exit configs 'time_4c' / 'time_8c' (on 5m bars, 4 and 8 bars
    are 20 and 40 minutes, bracketing the paper's 28 minute hold); read any
    verdict from a non-time exit config with that caveat attached.

    DEVIATION 2 (ATR SCALE): the 0.5x ATR is read against a SESSION scale
    ATR (v2's _session_atr, bar ATR rescaled by the square root of bars per
    session), not the raw 5m or 15m bar ATR. The doc's "disaster stop" is a
    day-trade-scale quantity; 0.5 of a 5 minute bar's ATR is a few cents on
    SPY and would measure slippage rather than the hypothesis. This is the
    same units mismatch that killed strategies in the v2 lab.

    DEVIATION 3 (AMPLIFIER LOOKBACK): the doc compares the DAY's realized
    volatility and volume against the 70th percentile of the TRAILING 60
    DAYS. Sixty days of 5m bars is roughly 11,500 bars; the harness scan
    window is 260. The amplifiers are therefore computed against the regular
    trading hours bars of the PRIOR sessions inside the scan window (about
    one prior session on 5m data, three on 15m, sixteen on 1h): today's mean
    absolute RTH bar return must exceed the 70th percentile of the prior
    sessions' absolute RTH bar returns, and today's mean RTH bar volume must
    exceed the 70th percentile of the prior sessions' RTH bar volumes. The
    direction and rough strictness of the amplifier survive ("today is a hot
    day relative to recent tape"); its 60 day depth does not. A faithful
    version needs a per-ticker daily history table the harness does not pass.

    DEVIATION 4 (ENTRY TIMING): entry is the CLOSE of the bar stamped 15:30,
    so on 5m data the fill is at 15:35 rather than the paper's 15:30 print.
    Entering at the 15:30 print itself would require acting on a bar before
    it closed, which is lookahead.

    NO DEVIATION: r1 is measured from the PRIOR CLOSE (not from the 9:30
    open), which is the paper's definition and the reason the signal contains
    overnight information.

    READING THE VARIANTS: scan() has no ticker identity, so the ET variant
    also fires on crypto CSVs (BTC bars stamped inside the NYSE session pass
    the calendar check) and the UTC variant also fires on the extended-hours
    equity CSVs (23:30 UTC is 19:30 ET, deep in the after-hours tape). Those
    crossed readings are noise, not hypotheses. Judge V3_intraday_momentum on
    equity files and V3_intraday_momentum_crypto on crypto files.
    """
    name = "V3_intraday_momentum"

    # 15:30 ET. The trigger is the first bar of the session stamped at or
    # after this minute, so the rule survives 5m, 15m and 30m bar sizes.
    TRIGGER_MIN = 930
    FIRST_HALF_HOUR_END = 600   # 10:00 ET
    TWELFTH_START = 900         # 15:00 ET
    AMPLIFIER_PCT = 0.70
    MIN_AMPLIFIERS = 2
    STOP_ATR = 0.5
    TARGET_ATR = 0.5
    MIN_BASELINE_BARS = 30
    MIN_DAY_BARS = 8

    # Roughly one prior session plus today on 5m extended-hours data. Kept
    # under the 260 bar scan window on purpose: see DEVIATION 3 for what that
    # costs. Raising it above 260 would make the harness report NOT_TESTED.
    min_bars = 200

    def __init__(self, crypto: bool = False):
        """crypto=True runs the doc's own proposed novel test: define the
        "day" as UTC 00:00-24:00, take r1 from the first UTC half hour and
        r12 from the PENULTIMATE UTC half hour (23:00-23:30), and trigger at
        23:30 UTC into the final half hour. That is the exact structural
        analog of 9:30-10:00 / 15:00-15:30 / 15:30-16:00 on a 24 hour clock.
        """
        self.crypto = crypto
        if crypto:
            self.name = "V3_intraday_momentum_crypto"
            # D-276 (Raven, 2026-08-17): NO `mean_reversion = True` here.
            # R-006 originally named the crypto variant in the confirmation-
            # stack exemption cohort; the objection raised against that was
            # upheld. V3's thesis is momentum - the first half hour's return
            # predicts the last half hour's return - so it is not a strategy
            # the stack suppresses by construction, and exempting it was
            # exempting the wrong thing. Both V3 variants now run WITH the
            # confirmation stack, like every other non-cohort strategy.
            #
            # The flag was set here, per-instance, rather than on the class,
            # because only the crypto variant was named. Deleting the
            # assignment is what actually changes harness behaviour:
            # `_stack_applies` reads `strategy.mean_reversion` BEFORE it
            # consults the name list in strategies/cohorts.py, so removing
            # the name from R006_COHORT alone would have been inert.

    # -- day partition -------------------------------------------------

    def _day_bounds(self):
        """(trigger_minute, first_half_hour_end, twelfth_start) for the clock."""
        if self.crypto:
            # 48 half hours in a UTC day. Trigger at 23:30 into the 48th;
            # r12's analog is the 47th half hour, 23:00-23:30.
            return 1410, 30, 1380
        return self.TRIGGER_MIN, self.FIRST_HALF_HOUR_END, self.TWELFTH_START

    def _parts(self, ts):
        if self.crypto:
            return _utc_parts(ts)
        date, minute, _ = _et_parts(ts)
        return (date, minute)

    def _today_indices(self, timestamps: List[float]) -> List[int]:
        """Indices of the current day's tradeable bars, oldest first."""
        if not self.crypto:
            return _rth_day_indices(timestamps)
        n = len(timestamps)
        today, _ = _utc_parts(timestamps[-1])
        out = []
        i = n - 1
        while i >= 0:
            date, _ = _utc_parts(timestamps[i])
            if date != today:
                break
            out.append(i)
            i -= 1
        out.reverse()
        return out

    def _is_baseline_bar(self, ts, today) -> bool:
        """True for a prior-day bar that belongs in the amplifier baseline.

        Equities: prior sessions' RTH bars only. Folding the thin extended
        hours tape into the baseline would drag both percentiles down and
        make the amplifiers pass almost every day, which is how an
        unsatisfiable-or-trivial filter gets shipped by accident.
        """
        if self.crypto:
            date, _ = _utc_parts(ts)
            return date != today
        date, minute, _ = _et_parts(ts)
        return date != today and RTH_OPEN_MIN <= minute < RTH_CLOSE_MIN

    # -- signal --------------------------------------------------------

    def _scan(self, candles):
        closes = candles['closes']
        volumes = candles['volumes']
        timestamps = candles['timestamps']
        n = len(closes)
        if n < 60:
            return None

        trigger_min, first_end, twelfth_start = self._day_bounds()

        today, minute = self._parts(timestamps[-1])
        if minute < trigger_min:
            return None
        if not self.crypto:
            if minute >= RTH_CLOSE_MIN:
                return None
            if not _in_session(timestamps[-1]):
                return None

        day = self._today_indices(timestamps)
        if len(day) < self.MIN_DAY_BARS or day[-1] != n - 1:
            return None
        # First bar of the day at or after the trigger minute, and only that
        # one: without this the rule would fire on every remaining bar of the
        # session.
        for i in day[:-1]:
            if self._parts(timestamps[i])[1] >= trigger_min:
                return None

        # --- prior close: the last tradeable bar of the previous day ---
        first_today = day[0]
        prior_idx = None
        for i in range(first_today - 1, -1, -1):
            if self._is_baseline_bar(timestamps[i], today):
                prior_idx = i
                break
        if prior_idx is None:
            return None
        prior_close = closes[prior_idx]
        if prior_close <= 0:
            return None

        # --- r1: prior close -> end of the first half hour ---
        first_hh = [i for i in day if self._parts(timestamps[i])[1] < first_end]
        if not first_hh:
            return None
        r1 = (closes[first_hh[-1]] / prior_close) - 1.0

        # --- r12: the half hour immediately before the trigger ---
        pre_trigger = [i for i in day if self._parts(timestamps[i])[1] < trigger_min]
        pre_twelfth = [i for i in day if self._parts(timestamps[i])[1] < twelfth_start]
        if not pre_trigger or not pre_twelfth:
            return None
        base_12 = closes[pre_twelfth[-1]]
        if base_12 <= 0:
            return None
        r12 = (closes[pre_trigger[-1]] / base_12) - 1.0

        # Long only: the paper trades the sign of r1, we take positive-sign
        # days only. Sign agreement is the doc's dual-signal filter.
        if r1 <= 0 or r12 <= 0:
            return None

        # --- amplifiers ---
        baseline_abs = []
        baseline_vol = []
        prev_close = None
        prev_idx = None
        for i in range(n):
            if not self._is_baseline_bar(timestamps[i], today):
                prev_close = None
                prev_idx = None
                continue
            if prev_close is not None and prev_idx == i - 1:
                lr = _log_return(prev_close, closes[i])
                if lr is not None:
                    baseline_abs.append(abs(lr))
            if volumes[i] > 0:
                baseline_vol.append(volumes[i])
            prev_close = closes[i]
            prev_idx = i
        if len(baseline_abs) < self.MIN_BASELINE_BARS:
            return None
        if len(baseline_vol) < self.MIN_BASELINE_BARS:
            return None

        today_abs = []
        for pos in range(1, len(day)):
            lr = _log_return(closes[day[pos - 1]], closes[day[pos]])
            if lr is not None:
                today_abs.append(abs(lr))
        today_vols = [volumes[i] for i in day if volumes[i] > 0]
        if len(today_abs) < 4 or len(today_vols) < 4:
            return None

        day_vol = sum(today_abs) / len(today_abs)
        day_volume = sum(today_vols) / len(today_vols)

        vol_amp = _percentile_rank(day_vol, baseline_abs) > self.AMPLIFIER_PCT
        volume_amp = _percentile_rank(day_volume, baseline_vol) > self.AMPLIFIER_PCT
        macro_amp = today in MACRO_DATES

        amplifiers = int(vol_amp) + int(volume_amp) + int(macro_amp)
        if amplifiers < self.MIN_AMPLIFIERS:
            return None

        atr = (_horizon_atr(candles, UTC_DAY_SECONDS) if self.crypto
               else _session_atr(candles))
        if not atr or atr <= 0:
            return None

        entry = closes[-1]
        stop = entry - self.STOP_ATR * atr
        target = entry + self.TARGET_ATR * atr
        if stop <= 0 or stop >= entry or target <= entry:
            return None

        conf = _clamp(0.42 + 0.04 * (amplifiers - self.MIN_AMPLIFIERS))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'r1': round(r1, 6), 'r12': round(r12, 6),
                'amplifiers': amplifiers,
                'vol_amplifier': vol_amp, 'volume_amplifier': volume_amp,
                'macro_amplifier': macro_amp,
                'day_mean_abs_return': round(day_vol, 8),
                'baseline_bars': len(baseline_abs),
                'day_bars': len(day),
                'horizon_atr': round(atr, 6),
                'clock': 'UTC' if self.crypto else 'ET',
            },
            entry=entry, stop=stop, target=target, valid_for=2,
        )


# ============ STRATEGY 4 · "Macro Calendar Harvest" (pre-announcement drift) ============

class MacroDrift(_V3Strategy):
    """THESIS: the equity premium is not earned evenly through the calendar.
    It accrues disproportionately in the hours BEFORE scheduled macro
    announcements, when investors demand payment for holding announcement
    risk. Hold index exposure only inside those windows and sit flat
    otherwise.

    CITATIONS: Lucca and Moench, "The Pre-FOMC Announcement Drift", Journal
    of Finance 70(1), 2015. Savor and Wilson, "How Much Do Investors Care
    About Macroeconomic Risk?", JFQA 48(2), 2013. Cieslak, Morse and
    Vissing-Jorgensen, "Stock Returns over the FOMC Cycle", Journal of
    Finance 74(5), 2019.

    DOCUMENTED EFFECT SIZE: Lucca and Moench find the 24 hours before
    scheduled FOMC announcements account for over 80% of the entire equity
    premium in the 1994-2011 sample, and the pattern appears in major
    international indices. Savor and Wilson find scheduled macro announcement
    days (CPI, employment, FOMC) carry a premium generally. The doc flags
    that post-2015 evidence is mixed, which is exactly why the decay monitor
    (rolling 24 window mean pre-announcement return, retire on two
    consecutive negative years) is part of the genome.

    DEVIATION 1 (VIX CONDITIONING OMITTED, prominent): the doc, following
    Lucca and Moench, sizes up when VIX is above its one year median at
    window entry. backtest/data/VIX_1d.csv exists, but the Strategy interface
    hands scan() exactly one instrument's candles and no second series, so
    VIX is simply not reachable from inside scan(). This implementation has
    NO VIX condition at all. That removes the paper's own conditioning
    variable, so expect a weaker and noisier signal than the citation
    implies, and do not read a failure here as a refutation of the
    conditional claim. Restoring it needs either a VIX-aware harness channel
    or a precomputed VIX percentile table keyed by date.

    DEVIATION 2 (EXIT): the doc exits 5 minutes before the release, harvesting
    drift with zero event risk. The harness has no scheduled-time exit, so the
    trade is expressed as a symmetric 1.0x session-scale ATR stop and target.
    When sweeping this strategy, 'time_8c' and 'time_16c' are the closest
    analogs available: on 5m bars they cap the hold at 40 and 80 minutes, and
    on 15m bars at 2 and 4 hours, all of which land inside the pre-release
    window rather than through the announcement. A verdict taken from
    'trailing_atr' or a fixed R config is measuring a different trade than
    the paper's.

    DEVIATION 3 (ATR SCALE): 1.0x ATR is read against v2's session-scale ATR
    for the same units reason documented on IntradayMomentum. A 1.0x 5m bar
    ATR stop on an intended 24 hour hold is noise.

    DEVIATION 4 (CALENDAR-DAY OFFSET): "the day before" is the previous
    CALENDAR day, not the previous trading day. FOMC decisions land midweek
    and NFP on Fridays, so the two coincide in practice; the rule would miss
    an event whose preceding calendar day is a holiday rather than sliding
    back to the prior session.
    """
    name = "V3_macro_drift"

    WINDOW_START_MIN = 840  # 14:00 ET, the doc's 24 hour window open
    WINDOW_END_MIN = RTH_CLOSE_MIN
    STOP_ATR = 1.0
    TARGET_ATR = 1.0
    MIN_DAY_BARS = 6

    min_bars = 60

    def __init__(self, event: str = 'FOMC'):
        self.event = event.upper()
        if self.event == 'NFP':
            self.name = "V3_macro_drift_nfp"
            self._dates = NFP_DATES
        elif self.event == 'CPI':
            # Unblocked 2026-08-13 by the FRED key (948 release dates,
            # 1949-2026). Savor & Wilson's announcement-day premium covers
            # CPI explicitly; drift window semantics identical to FOMC/NFP.
            self.name = "V3_macro_drift_cpi"
            self._dates = CPI_DATES
        else:
            self.event = 'FOMC'
            self._dates = FOMC_DATES

    def _scan(self, candles):
        closes = candles['closes']
        timestamps = candles['timestamps']
        n = len(closes)
        if n < 30:
            return None
        if not self._dates:
            return None  # calendar failed to load: emit nothing, never guess

        today, minute, _ = _et_parts(timestamps[-1])
        if not (self.WINDOW_START_MIN <= minute < self.WINDOW_END_MIN):
            return None
        if not _in_session(timestamps[-1]):
            return None

        # The event must be TOMORROW. Reading the calendar for today's date
        # would be trading the announcement, not the drift into it.
        try:
            event_day = today + _ONE_DAY
        except (TypeError, OverflowError):
            return None
        if event_day not in self._dates:
            return None

        day = _rth_day_indices(timestamps)
        if len(day) < self.MIN_DAY_BARS or day[-1] != n - 1:
            return None
        # Enter once per window, on its first bar.
        for i in day[:-1]:
            if _et_parts(timestamps[i])[1] >= self.WINDOW_START_MIN:
                return None

        atr = _session_atr(candles)
        if not atr or atr <= 0:
            return None

        entry = closes[-1]
        if entry <= 0:
            return None
        stop = entry - self.STOP_ATR * atr
        target = entry + self.TARGET_ATR * atr
        if stop <= 0 or stop >= entry or target <= entry:
            return None

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=0.45,
            features={
                'event': self.event,
                'event_date': str(event_day),
                'window_open_et_minute': minute,
                'session_atr': round(atr, 6),
                'session_bars': len(day),
                'vix_conditioning': 'OMITTED_NO_SERIES',
            },
            entry=entry, stop=stop, target=target, valid_for=2,
        )


# ============ STRATEGY 1 · "Vacuum Refill" (liquidity provision) ============

class VacuumRefill(_V3Strategy):
    """THESIS: a violent flush on climax volume during an already-stressed
    tape is mechanical, forced flow with no information in it. Whoever
    absorbs it is providing liquidity when providers have withdrawn, and the
    snapback is the fee for that service. Buy the first candle that closes
    above its own open after such a flush and sell the halfway retrace.

    CITATION: Nagel, "Evaporating Liquidity", Review of Financial Studies
    25(7), 2012.

    DOCUMENTED EFFECT SIZE: Nagel shows short-term reversal profits behave
    like market-making income, are strongly predictable with VIX, and SPIKE
    during turmoil precisely when real liquidity providers pull back. The
    edge is conditional on stress, which is why the volatility percentile
    filter below is a gate and not a nicety.

    DEVIATION 1 (TIMEFRAME, the doc's own precondition): the doc specifies 1m
    to 5m crypto bars. The lowest resolution data in this project is 15m for
    crypto and 5m for equities, so the flush is measured over whatever bars
    scan() receives. Consequence: far fewer and structurally larger flushes
    than the doc envisages, since a 1m liquidation cascade is smoothed inside
    a 15m candle. Any frequency number produced here is a floor, not the
    doc's "2-5 on stressed days".

    DEVIATION 2 (CROSS-PAIR IDIOSYNCRASY TEST OMITTED, the largest omission):
    the doc's central discriminator is that the flushing pair must move more
    than 3x the median move of the other two pairs in the same window, which
    is what separates mechanical forced flow from market-wide information.
    scan() receives one instrument's candles and no cross-ticker channel, so
    that test is simply absent. This implementation therefore CANNOT tell a
    liquidation cascade from a news shock, and buying informed selling is the
    documented way reversal traders die. Treat every result here as an upper
    bound on noise and a lower bound on nothing.

    DEVIATION 3 (VOLUME BASELINE): the doc uses a 24 hour baseline. 24 hours
    of 15m bars is 96 bars, so the 96 bar baseline below is exact for 15m
    crypto and an approximation on every other timeframe.

    DEVIATION 4 (NO TIME BOX): the doc adds a 30 minute hard time box on top
    of the target and stop. The harness has no time box inside a strategy;
    the 'time_4c' exit config is the nearest analog when sweeping.

    NO DEVIATION on the cost gate: the flush must be at least 2.5x the
    round-trip cost (2.5 x 0.3% = 0.75%). The doc is explicit that removing
    this multiple turns the strategy into a fee donation machine, so it stays
    literal.
    """
    name = "V3_vacuum_refill"

    ROUND_TRIP_COST = 0.003
    COST_MULTIPLE = 2.5
    MIN_FLUSH = ROUND_TRIP_COST * COST_MULTIPLE  # 0.75%
    MAX_FLUSH_BARS = 3
    MIN_VOL_Z = 3.0
    VOL_BASELINE = 96          # 24h of 15m bars
    RV_WINDOW = 20             # rolling realized vol length
    RV_LOOKBACK = 200          # trailing distribution the percentile is taken over
    RV_PERCENTILE = 0.60

    # RV_LOOKBACK + RV_WINDOW + the flush itself. Deliberately under the 260
    # bar scan window so the harness tests this rather than reporting
    # NOT_TESTED.
    min_bars = 230

    def _realized_vol_series(self, closes: List[float], end_idx: int,
                             count: int) -> Optional[List[float]]:
        """Rolling RV_WINDOW realized vol at bars [end_idx-count+1 .. end_idx].

        Built from a prefix sum of squared log returns so the whole series
        costs O(bars) instead of O(bars x window). Returns None if the window
        would reach before index 0.
        """
        first = end_idx - count + 1
        lo = first - self.RV_WINDOW
        if lo < 1 or end_idx >= len(closes):
            return None
        # sq[k] is the squared log return of bar (lo - 1 + k) against its
        # predecessor, for k >= 1.
        prefix = [0.0]
        total = 0.0
        for i in range(lo, end_idx + 1):
            lr = _log_return(closes[i - 1], closes[i])
            if lr is None:
                return None
            total += lr * lr
            prefix.append(total)
        out = []
        for j in range(first, end_idx + 1):
            hi_k = j - lo + 1
            lo_k = hi_k - self.RV_WINDOW
            if lo_k < 0:
                return None
            out.append(math.sqrt(max(0.0, (prefix[hi_k] - prefix[lo_k]) / self.RV_WINDOW)))
        return out

    def _scan(self, candles):
        opens = candles['opens']
        highs = candles['highs']
        lows = candles['lows']
        closes = candles['closes']
        volumes = candles['volumes']
        n = len(closes)
        if n < self.min_bars:
            return None

        # Gate 1 (cheapest): the trigger bar must close above its own open.
        if closes[-1] <= opens[-1]:
            return None

        # Gate 2: a flush of at least 2.5x round-trip cost over 1-3 bars
        # ending immediately before the trigger bar. The largest qualifying
        # flush wins so a 3 bar cascade is not shadowed by its own last bar.
        end = n - 2
        best = None
        for k in range(1, self.MAX_FLUSH_BARS + 1):
            start = end - k + 1
            if start - 1 < 0:
                break
            ref = closes[start - 1]
            if ref <= 0 or not math.isfinite(ref):
                break
            if closes[end] >= ref:
                continue  # the block is not net down
            flush_low = min(lows[start:end + 1])
            if flush_low <= 0:
                continue
            decline = (ref - flush_low) / ref
            if decline < self.MIN_FLUSH:
                continue
            if best is None or decline > best[0]:
                best = (decline, start, ref, flush_low)
        if best is None:
            return None
        decline, start, ref, flush_low = best

        # Gate 3: climax volume. Baseline stops at the bar before the flush so
        # the flush cannot inflate its own reference.
        base_lo = start - self.VOL_BASELINE
        if base_lo < 0:
            return None
        baseline = volumes[base_lo:start]
        mean_v, std_v = _mean_std(baseline)
        if std_v <= 0 or mean_v <= 0:
            return None
        flush_vol = max(volumes[start:end + 1])
        vol_z = (flush_vol - mean_v) / std_v
        if vol_z <= self.MIN_VOL_Z:
            return None

        # Gate 4: stressed regime. Measured at the bar BEFORE the flush, so
        # this is a filter on the tape the flush arrived into rather than a
        # restatement of the flush itself.
        rv = self._realized_vol_series(closes, start - 1, self.RV_LOOKBACK + 1)
        if not rv or len(rv) < self.RV_LOOKBACK + 1:
            return None
        current_rv = rv[-1]
        trailing = rv[:-1]
        if current_rv <= 0:
            return None
        if _percentile_rank(current_rv, trailing) <= self.RV_PERCENTILE:
            return None

        flush_top = max(ref, max(highs[start:end + 1]))
        if flush_top <= flush_low:
            return None

        entry = closes[-1]
        stop = flush_low
        target = flush_low + 0.5 * (flush_top - flush_low)
        if stop >= entry or target <= entry:
            return None

        conf = _clamp(0.40 + min(0.15, (vol_z - self.MIN_VOL_Z) * 0.02
                                 + (decline - self.MIN_FLUSH) * 5.0))

        return Signal(
            pair="", pattern=self.name, direction='bullish', confidence=conf,
            features={
                'flush_pct': round(decline, 6),
                'flush_bars': end - start + 1,
                'flush_low': round(flush_low, 6),
                'flush_top': round(flush_top, 6),
                'volume_z': round(vol_z, 4),
                'realized_vol': round(current_rv, 8),
                'rv_percentile': round(_percentile_rank(current_rv, trailing), 4),
                'cross_pair_test': 'OMITTED_NO_CROSS_TICKER_DATA',
            },
            entry=entry, stop=stop, target=target, valid_for=3,
        )


# ============ EXPORTS ============

STRATEGY_LAB_V3_STRATEGIES = [
    IntradayMomentum(),                # ET session clock (SPY/QQQ/equities)
    IntradayMomentum(crypto=True),     # UTC day clock (the doc's free BTC test)
    MacroDrift(event='FOMC'),
    MacroDrift(event='NFP'),
    MacroDrift(event='CPI'),           # unblocked by FRED key 2026-08-13
    VacuumRefill(),
]
