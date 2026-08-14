"""Strategy Lab v5, P5 "FORCED-FLOW HARVEST" (references/strategy-lab-v5.md).

P5's thesis, verbatim from the doc: "forced sellers must liquidate regardless
of price, and the pressure ends when the margin call clears; the post-cascade
snapback is payment for absorbing mechanically-motivated flow. Price patterns
fire on geometry; this fires only when the SELLER'S CONSTRAINT is
identifiable."

TWO strategies, deliberately SEPARATE graveyard cohorts, because P5's third
kill condition is mechanism coherence: "the funding-stress leg and the
volume-climax leg disagree on sign (mechanism incoherence)". Pooling the two
legs into one cohort would make that kill condition unmeasurable.

  V5_forced_flow_crypto      funding-rate stress percentile + a >=3-bar
                             expanding down-candle cascade on climax volume,
                             cascade low holding above the prior swing low
  V5_capitulation_equity     capitulation days: volume z > 4, close in the
                             bottom decile of the day's range, gap-down open

==================== FUNDING-REGIME CAVEAT (read this first) ================
The perp funding data in this project is ONE YEAR (2025-08-13 to 2026-08-13)
of an UNUSUAL regime - sustained negative BTC perp funding in 2026 (HANDOVER
UNVERIFIED ASSUMPTIONS item 6). Any funding-conditioned result out of
V5_forced_flow_crypto is a claim about ONE STRANGE REGIME, not about crypto
funding markets in general. Do not generalize a PASS or a FAIL beyond that
year without new data.
=============================================================================

P5 PRE-REGISTERED PREDICTION (verbatim, applies to both cohorts):
    "event-cohort net >= +30bps per trade at the corrected cost model, with
    edge NOT concentrated in a single underlying (leave-one-out flag from
    S3.4 applies automatically)."

P5 KILL CONDITIONS (verbatim, all three):
    "pooled net < +30bps, OR the top underlying's removal moves the result
    > $0.15/trade (one asset in a costume), OR the funding-stress leg and
    the volume-climax leg disagree on sign (mechanism incoherence)."

P5 gross estimate: 100-300bps per event over the multi-day snapback. This
lane only exists if the edge is LARGE (standing rule 5 satisfied by the doc
itself).

DEVIATIONS FROM P5 (each deliberate and visible):

1. NO EARNINGS-WINDOW EXCLUSION on V5_capitulation_equity, PROMINENTLY: P5
   specifies "no earnings within the window (informational-flow exclusion)".
   There is NO earnings calendar in this project (same gap that made v4's I1
   a proxy). Informational-flow contamination is therefore UNCONTROLLED:
   some capitulation days ARE earnings collapses, i.e. informed selling, the
   documented way reversal traders die. Every result from this cohort is
   PROVISIONAL until an earnings calendar exists and the exclusion is
   applied. The feature dict carries 'earnings_exclusion': 'OMITTED_NO_
   EARNINGS_CALENDAR' so no report can quietly drop the caveat.
2. CRYPTO GATING IS STRUCTURAL, NOT BY TICKER: scan() has no ticker
   identity. The tasking assumed equity series would simply never match
   funding dates; CHECKED 2026-08-13 and FALSE - equity daily files run
   through 2026-08 and the graveyard's last-20% test slices sit ENTIRELY
   inside the funding year. The funding-date gate alone would not exclude
   equities. The crypto gate is therefore: (a) bar spacing 1h..1d, (b) the
   tape trades on weekends (24/7 market - equity series have zero
   Saturday/Sunday bars, crypto always has them), (c) the prior UTC date is
   in the funding table with enough trailing history. (b) is the load-
   bearing discriminator; (c) alone would pass recent equities.
3. FUNDING READ FROM THE PRIOR UTC DATE: the day-of-cascade funding prints
   are not all known until the day closes, so on intraday bars reading
   "today's" funding would peek at future prints. The stress percentile is
   computed for the PRIOR calendar date, which is fully printed by the time
   any bar of the current date closes. Funding stress is persistent
   day-to-day, so this is the mechanism with one day of lag, not a
   different mechanism.
4. STRESS THRESHOLD: P5 says "funding-rate stress percentile" without a
   number. Fixed here BEFORE any P&L was read: the prior date's mean
   funding (pooled across the BTC/ETH/SOL files - no ticker identity, see
   2) must sit at or below the 25th percentile of all EARLIER dates in the
   table (trailing-only, no lookahead), with >= 30 trailing dates. Measured
   on the table itself: 97 of 366 dates qualify. The pooled-symbol mean is
   a compromise forced by the interface, documented, not hidden.
5. EXIT: P5 holds 2-5 days with a stop strictly below the event low. The
   harness has no in-strategy time exit; on daily bars the exit config
   'time_8c' (8 bars) is the NEAREST available analog to the 2-5 day hold -
   read verdicts from that config first, and treat trailing/fixed-R
   verdicts as measuring a different trade. The stop IS the event low
   (strictly below it), per P5. Target is the harness-standard 2R.

Interface contract (same as v2/v3/v4): scan() reads index -1 and earlier
only, never raises, returns None or a bullish Signal whose stop is strictly
below entry. min_bars <= 260 (SCAN_WINDOW) for both strategies.
"""
import csv
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from strategies.base import Strategy, Signal
from strategies.builtin.strategy_lab import _clamp, _dt
from strategies.builtin.strategy_lab_v2 import _bar_seconds, _mean_std

logger = logging.getLogger(__name__)

_HOUR_S = 3600.0
_DAY_S = 86_400.0
_WEEK_S = 7 * _DAY_S


# ============ PERP FUNDING TABLE (loaded ONCE at import) ============
#
# Same pattern as strategy_lab_v3's _load_macro_calendar: reading these files
# inside scan() would mean ~95,000 disk reads per sweep; they are static
# history, so import time is the right time. Missing/malformed files degrade
# to an EMPTY table, and an empty table means V5_forced_flow_crypto emits
# nothing rather than crashing the sweep or guessing.

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'backtest', 'data',
)
_FUNDING_FILES = [
    # the 1-year hourly history (the actual "1yr perp funding files")
    os.path.join(_DATA_DIR, 'aux', 'funding_krakenfutures_BTC.csv'),
    os.path.join(_DATA_DIR, 'aux', 'funding_krakenfutures_ETH.csv'),
    os.path.join(_DATA_DIR, 'aux', 'funding_krakenfutures_SOL.csv'),
    # short recent snapshots from two more venues (~90-100 rows each); they
    # only densify the most recent dates of the same pooled daily mean
    os.path.join(_DATA_DIR, 'funding_gate_BTC_USDT.csv'),
    os.path.join(_DATA_DIR, 'funding_gate_ETH_USDT.csv'),
    os.path.join(_DATA_DIR, 'funding_gate_SOL_USDT.csv'),
    os.path.join(_DATA_DIR, 'funding_okx_BTC_USDT_SWAP.csv'),
    os.path.join(_DATA_DIR, 'funding_okx_ETH_USDT_SWAP.csv'),
    os.path.join(_DATA_DIR, 'funding_okx_SOL_USDT_SWAP.csv'),
]

# Minimum trailing dates before a stress percentile is trusted. With a
# 366-date table this burns the first month, which is the price of a
# trailing-only (lookahead-free) percentile.
_MIN_TRAILING_DATES = 30


def _load_funding_stress(paths: List[str] = _FUNDING_FILES) -> Dict[object, float]:
    """{UTC date: trailing percentile rank of that date's pooled mean funding}.

    Pooled = all rates from all files stamped on that UTC date, averaged
    (BTC/ETH/SOL and venues together - scan() has no ticker identity to key
    a per-symbol table on; DEVIATION 4 in the module docstring). The
    percentile for date D is computed against dates STRICTLY BEFORE D only,
    so nothing here can see the future. Dates with < _MIN_TRAILING_DATES of
    history are omitted entirely (the gate then refuses to fire, which is
    the conservative direction). Timestamp units auto-detected per row
    (seconds / ms / us - the three venues disagree).
    """
    by_date: Dict[object, List[float]] = {}
    for path in paths:
        try:
            with open(path, 'r') as fh:
                for row in csv.DictReader(fh):
                    try:
                        ts = float(row['ts'])
                        rate = float(row['funding_rate'])
                    except (KeyError, ValueError, TypeError):
                        continue
                    if not math.isfinite(ts) or not math.isfinite(rate):
                        continue
                    if ts >= 1e14:      # microseconds
                        ts /= 1e6
                    elif ts >= 1e11:    # milliseconds
                        ts /= 1e3
                    day = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                    by_date.setdefault(day, []).append(rate)
        except Exception as exc:  # missing file, permissions, bad encoding
            logger.warning("strategy_lab_v5: funding file unavailable (%s: %s); "
                           "continuing with the rest", path, exc)
    if not by_date:
        logger.warning("strategy_lab_v5: NO funding data loaded; "
                       "V5_forced_flow_crypto will emit no signals")
        return {}
    dates = sorted(by_date)
    means = [sum(by_date[d]) / len(by_date[d]) for d in dates]
    stress: Dict[object, float] = {}
    for i, day in enumerate(dates):
        if i < _MIN_TRAILING_DATES:
            continue
        trail = means[:i]
        stress[day] = sum(1 for v in trail if v <= means[i]) / len(trail)
    return stress


# date -> trailing percentile rank of that date's pooled mean funding.
# LOW rank = funding deeply negative vs its own past = the stress state.
FUNDING_STRESS_PCTL = _load_funding_stress()


# ============ SHARED HELPERS ============

def _weekend_bar_count(timestamps: List[float], lookback: int) -> int:
    """How many of the last `lookback` bars land on a UTC Saturday/Sunday.

    The 24/7-tape discriminator (DEVIATION 2): equity series have ZERO
    weekend bars on any timeframe; crypto series always have them once the
    window spans a weekend.
    """
    count = 0
    for ts in timestamps[-lookback:]:
        try:
            if _dt(ts).weekday() >= 5:
                count += 1
        except (OverflowError, OSError, ValueError):
            continue
    return count


class _V5Strategy(Strategy):
    """Same never-raise contract as _V2/_V3/_V4Strategy: a strategy that
    throws inside a sweep is skipped bar by bar and reads as a dead strategy
    in the graveyard, which is how a bug gets mistaken for a verdict. First
    failure per instance is logged so a real bug is visible once, not
    90,000 times."""
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
        """Bullish signal with P5's event-low stop; 2R target per harness
        convention (DEVIATION 5). Refuses degenerate geometry instead of
        emitting it."""
        if not (math.isfinite(entry) and math.isfinite(stop)):
            return None
        if stop >= entry or entry <= 0:
            return None
        return Signal(pair='', pattern=self.name, direction='bullish',
                      confidence=confidence, features=features, entry=entry,
                      stop=stop, target=entry + 2 * (entry - stop),
                      valid_for=1)


# ============ P5 crypto leg · funding-stressed liquidation cascade ============

class ForcedFlowCrypto(_V5Strategy):
    """P5 crypto selection, verbatim: "funding-rate stress percentile (the
    1yr perp funding files) + >=3 expanding down candles on volume z > 3
    with the cascade low holding above the prior swing low." Long at the
    close of the final cascade bar; stop strictly below the event (cascade)
    low. Hold 2-5 days intended -> 'time_8c' on daily bars is the nearest
    exit config (DEVIATION 5).

    FUNDING-REGIME CAVEAT (module docstring, repeated because it gates this
    strategy specifically): the funding table is ONE YEAR of an unusual
    regime (sustained negative BTC perp funding in 2026). Every signal here
    is conditioned on that one strange regime.

    PRE-REGISTERED PREDICTION (P5, verbatim): "event-cohort net >= +30bps
    per trade at the corrected cost model, with edge NOT concentrated in a
    single underlying (leave-one-out flag from S3.4 applies automatically)."

    KILL CONDITIONS (P5, verbatim): "pooled net < +30bps, OR the top
    underlying's removal moves the result > $0.15/trade (one asset in a
    costume), OR the funding-stress leg and the volume-climax leg disagree
    on sign (mechanism incoherence)."

    CASCADE DEFINITION (fixed by fire-frequency diagnostics on candle
    counts ONLY - no P&L was read while choosing any of this; see the
    2026-08-13 handoff for the measured base rates): the last CASCADE_MIN..
    CASCADE_MAX bars are all down candles (close < open) making
    monotonically lower closes (each close below the previous bar's close,
    including the bar before the cascade), and the candle ranges EXPAND
    NET across the cascade (the final bar's high-low strictly wider than
    the first's - a cascade accelerates as stops trigger stops; measured:
    requiring strictly monotone per-bar expansion killed ~60% of otherwise
    qualifying cascades because discrete bars smooth the acceleration).
    The largest qualifying length wins. Climax volume: the cascade's max
    volume must be > MIN_VOL_Z standard deviations above the VOL_BASELINE
    bars ending before the cascade. Structure: the cascade low must HOLD
    ABOVE the min low of the SWING_LOOKBACK bars before the cascade - a
    cascade that breaks major support is a breakdown, not an absorbable
    flush.

    GATES (DEVIATION 2 and 3): bar spacing 1h..1d; 24/7 tape = at least
    MIN_WEEKEND_BARS weekend bars within a trailing window spanning
    WEEKEND_SPAN_S seconds (span-based so daily and hourly crypto both
    pass while equities, with zero weekend bars ever, cannot); prior UTC
    date present in FUNDING_STRESS_PCTL at or below STRESS_PCTL.
    """
    name = "V5_forced_flow_crypto"

    STRESS_PCTL = 0.25       # DEVIATION 4: fixed before any P&L was read
    CASCADE_MIN = 3          # P5: ">=3 expanding down candles"
    CASCADE_MAX = 5
    MIN_VOL_Z = 3.0          # P5: "volume z > 3"
    VOL_BASELINE = 45        # trailing bars for the volume mean/std
    SWING_LOOKBACK = 45      # prior swing low window
    WEEKEND_SPAN_S = 9.5 * _DAY_S   # any 9.5-day span contains a weekend
    MIN_WEEKEND_BARS = 2

    # VOL_BASELINE + SWING_LOOKBACK + cascade + margin. Kept low enough
    # that the ~147-bar last-20% test slices of the *_USD_1d files are
    # evaluable at all (200 made daily crypto structurally untestable);
    # far under 260 (SCAN_WINDOW) so nothing reads NOT_TESTED. On 1h bars
    # the weekend gate needs a ~228-bar window, which the 260-bar scan
    # window supplies mid-slice.
    min_bars = 110

    def __init__(self, funding_stress: Optional[Dict[object, float]] = None):
        """funding_stress overrides the module-level table - tests inject a
        synthetic table so fixtures do not depend on which dates happened to
        be stressed in the real files. Production instances use the real
        table loaded at import."""
        self._stress = (FUNDING_STRESS_PCTL if funding_stress is None
                        else funding_stress)

    def _funding_stressed(self, last_ts: float) -> Optional[Tuple[str, float]]:
        """(prior UTC date iso, stress pctl) if the funding gate passes."""
        try:
            prior_day = _dt(last_ts).date() - timedelta(days=1)
        except (OverflowError, OSError, ValueError):
            return None
        pctl = self._stress.get(prior_day)
        if pctl is None or pctl > self.STRESS_PCTL:
            return None
        return (prior_day.isoformat(), pctl)

    def _find_cascade(self, opens, highs, lows, closes) -> Optional[int]:
        """Length of the largest qualifying cascade ending at bar -1, else
        None. Qualifying = every cascade bar closes below its open AND below
        the prior bar's close, and the final bar's high-low range is
        strictly wider than the first's (net range expansion - see the
        class docstring for why per-bar monotone expansion was rejected)."""
        n = len(closes)
        best = None
        for k in range(self.CASCADE_MIN, self.CASCADE_MAX + 1):
            start = n - k
            if start < 1:
                break
            ok = True
            for i in range(start, n):
                if closes[i] >= opens[i]:          # not a down candle
                    ok = False; break
                if closes[i] >= closes[i - 1]:     # not a lower close
                    ok = False; break
            if ok and (highs[-1] - lows[-1]) <= (highs[start] - lows[start]):
                ok = False                          # ranges did not expand
            if ok:
                best = k
        return best

    def _scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        timestamps = candles['timestamps']
        n = len(timestamps)
        if n < self.min_bars:
            return None

        # Gate 1: timeframe. 1h through daily; weekly and sub-hourly are out.
        bar_s = _bar_seconds(timestamps)
        if bar_s is None or not (0.75 * _HOUR_S <= bar_s < 0.5 * _WEEK_S):
            return None

        # Gate 2 (the crypto discriminator, DEVIATION 2): 24/7 tape. The
        # lookback is span-based (enough bars to cover WEEKEND_SPAN_S) so a
        # midweek 1h window cannot dodge the weekend check by being short.
        lookback = int(math.ceil(self.WEEKEND_SPAN_S / bar_s))
        if n < lookback:
            return None
        if _weekend_bar_count(timestamps, lookback) < self.MIN_WEEKEND_BARS:
            return None

        # Gate 3 (the seller's constraint, DEVIATION 3+4): funding stress on
        # the prior UTC date.
        stressed = self._funding_stressed(timestamps[-1])
        if stressed is None:
            return None
        stress_date, stress_pctl = stressed

        opens, highs = candles['opens'], candles['highs']
        lows, closes = candles['lows'], candles['closes']
        volumes = candles['volumes']

        # Gate 4: the cascade itself.
        k = self._find_cascade(opens, highs, lows, closes)
        if k is None:
            return None
        start = n - k

        # Gate 5: climax volume. Baseline ends BEFORE the cascade so the
        # cascade cannot inflate its own reference (v3 VacuumRefill lesson).
        base_lo = start - self.VOL_BASELINE
        if base_lo < 0:
            return None
        mean_v, std_v = _mean_std(list(volumes[base_lo:start]))
        if std_v <= 0 or mean_v <= 0:
            return None
        vol_z = (max(volumes[start:]) - mean_v) / std_v
        if vol_z <= self.MIN_VOL_Z:
            return None

        # Gate 6: structure. Cascade low holds ABOVE the prior swing low.
        swing_lo = start - self.SWING_LOOKBACK
        if swing_lo < 0:
            return None
        cascade_low = min(lows[start:])
        prior_swing_low = min(lows[swing_lo:start])
        if cascade_low <= prior_swing_low or cascade_low <= 0:
            return None

        entry = closes[-1]
        stop = cascade_low * 0.999   # STRICTLY below the event low (P5)
        conf = _clamp(0.45 + min(0.10, (vol_z - self.MIN_VOL_Z) * 0.02)
                      + 0.05 * (self.STRESS_PCTL - stress_pctl))
        return self._long(entry, stop, confidence=conf, features={
            'funding_stress_pctl': round(stress_pctl, 4),
            'funding_stress_date': stress_date,
            'funding_regime_caveat': 'ONE_YEAR_UNUSUAL_REGIME_2026',
            'cascade_bars': k,
            'cascade_low': round(cascade_low, 6),
            'prior_swing_low': round(prior_swing_low, 6),
            'volume_z': round(vol_z, 4),
        })


# ============ P5 equity leg · capitulation day ============

class CapitulationEquity(_V5Strategy):
    """P5 equity selection, verbatim: "capitulation days: volume z > 4,
    close in bottom decile of range, gap-down open, no earnings within the
    window (informational-flow exclusion)." Long at the close of the
    capitulation day; stop strictly below the day's (event) low. Hold 2-5
    days intended -> 'time_8c' on daily bars is the nearest exit config
    (DEVIATION 5). Daily bars only.

    ======================= PROMINENT DEVIATION =======================
    THE EARNINGS-WINDOW EXCLUSION IS NOT IMPLEMENTED. No earnings calendar
    exists in this project. Informational-flow contamination is therefore
    UNCONTROLLED: an unknown fraction of these capitulation days are
    earnings collapses (informed selling, not forced flow). All results
    from this cohort are PROVISIONAL until an earnings calendar exists and
    the exclusion is applied. (Module docstring DEVIATION 1.)
    ===================================================================

    PRE-REGISTERED PREDICTION (P5, verbatim): "event-cohort net >= +30bps
    per trade at the corrected cost model, with edge NOT concentrated in a
    single underlying (leave-one-out flag from S3.4 applies automatically)."

    KILL CONDITIONS (P5, verbatim): "pooled net < +30bps, OR the top
    underlying's removal moves the result > $0.15/trade (one asset in a
    costume), OR the funding-stress leg and the volume-climax leg disagree
    on sign (mechanism incoherence)."

    GATING: daily bars only (P5's "days" language; the z>4 volume climax
    and the day's-range decile are daily-bar quantities). The mirror-image
    24/7 gate EXCLUDES crypto series (any weekend bar in the last 30
    disqualifies): crypto daily opens equal the prior close, so 'gap-down
    open' would silently never fire there anyway, but the explicit gate
    keeps the cohorts structurally disjoint for the mechanism-coherence
    kill condition.
    """
    name = "V5_capitulation_equity"

    MIN_VOL_Z = 4.0          # P5: "volume z > 4"
    VOL_BASELINE = 60        # trailing bars for the volume mean/std
    BOTTOM_DECILE = 0.10     # P5: "close in bottom decile of range"
    GAP_MIN = 0.005          # gap-down open: >= 0.5% below the prior close.
                             # P5 gives no size; 0.5% separates a true
                             # overnight gap from open-auction noise. Fixed
                             # before any P&L was read.
    WEEKEND_LOOKBACK = 30    # 30 daily bars always span >= 4 weekends
    min_bars = 70            # VOL_BASELINE + gap reference + margin

    def _scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        timestamps = candles['timestamps']
        n = len(timestamps)
        if n < self.min_bars:
            return None

        # Gate 1: daily bars only.
        bar_s = _bar_seconds(timestamps)
        if bar_s is None or not (0.75 * _DAY_S <= bar_s < 0.5 * _WEEK_S):
            return None

        # Gate 2: NOT a 24/7 tape (equity cohort must exclude crypto; see
        # class docstring).
        if _weekend_bar_count(timestamps, self.WEEKEND_LOOKBACK) > 0:
            return None

        opens, highs = candles['opens'], candles['highs']
        lows, closes = candles['lows'], candles['closes']
        volumes = candles['volumes']

        prev_close = closes[-2]
        o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
        if prev_close <= 0 or h <= l or l <= 0:
            return None

        # Gate 3: gap-down open (the overnight forced-liquidation signature).
        if o > prev_close * (1.0 - self.GAP_MIN):
            return None

        # Gate 4: close in the bottom decile of the day's range (sellers
        # were still hitting bids at the bell - capitulation, not dip-buy).
        if (c - l) / (h - l) > self.BOTTOM_DECILE:
            return None

        # Gate 5: climax volume, z > 4 against the trailing baseline (which
        # ends at the bar before the event, so the event cannot inflate its
        # own reference).
        base = list(volumes[-1 - self.VOL_BASELINE:-1])
        if len(base) < self.VOL_BASELINE:
            return None
        mean_v, std_v = _mean_std(base)
        if std_v <= 0 or mean_v <= 0:
            return None
        vol_z = (volumes[-1] - mean_v) / std_v
        if vol_z <= self.MIN_VOL_Z:
            return None

        stop = l * 0.999   # STRICTLY below the event low (P5)
        gap = o / prev_close - 1.0
        conf = _clamp(0.45 + min(0.10, (vol_z - self.MIN_VOL_Z) * 0.02))
        return self._long(c, stop, confidence=conf, features={
            'volume_z': round(vol_z, 4),
            'gap_pct': round(gap * 100, 2),
            'range_pos': round((c - l) / (h - l), 4),
            'earnings_exclusion': 'OMITTED_NO_EARNINGS_CALENDAR',
        })


# ============ EXPORTS ============

STRATEGY_LAB_V5_STRATEGIES = [
    ForcedFlowCrypto(),
    CapitulationEquity(),
]
