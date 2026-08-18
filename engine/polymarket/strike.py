"""A MEASURED proxy for the window strike that Gamma does not publish.

Why this file exists
--------------------
`btc-updown-5m` markets resolve against the Chainlink BTC/USD 60-second TWAP
stream (`resolutionSource` on every live market, confirmed 2026-08-18). The
strike is that TWAP sampled at window open. Gamma publishes no `openPrice`,
`strikePrice`, or equivalent - `context.STRIKE_KEYS` probes for one and always
comes back None.

Two strategies need it and neither could run without it:
  * `mid_price_continuation` skipped `no_spot_or_strike` on 310/311 windows.
  * `corridor_collector`     skipped `no_lead_or_atr`    on 310/311 windows.
Both are the SAME root cause. `corridor_collector` gets its `atr14` fine; it is
`lead_bps` that is None, and `lead_bps` needs a strike.

The previous session refused to substitute exchange spot for the strike, and it
was right to refuse. Substituting spot puts a number in the field that is wrong
precisely when the strategy is deciding. This module does not overturn that
ruling - it replaces "substitute a number and hope" with "substitute a number
whose error has been measured, and refuse to trade inside that error".

The measurement (this is the whole justification)
-------------------------------------------------
`backtest/measure_strike_proxy.py` replays completed windows, predicts each
outcome from this proxy alone, and scores it against the Gamma oracle's
resolved outcome. Run 2026-08-18T02:21Z, 199 resolved windows scored of 200
requested (1 unresolved, dropped not guessed), Binance.US 1m klines:

    |move| bucket    n    disagreements    rate
        0 -  1 bps  45         19         42.2%   <- coin flip. unusable.
        1 -  2 bps  17          4         23.5%
        2 -  5 bps  59          4          6.8%
        5 - 10 bps  46          3          6.5%
       10 +   bps   32          0          0.0%

    cumulative:  |move| >= 3 bps -> 4.2% (n=119)
                 |move| >= 5 bps -> 3.8% (n=78)
                 |move| >= 10 bps -> 0.0% (n=32)

    headline (all windows pooled): 15.1% - do NOT quote this alone. It averages
    a coin flip and a 96% together into a number that describes neither.

So the proxy is not "good" or "bad". It is a coin flip below 1 bp and roughly
96% accurate above 5. That shape is the entire design: the proxy is usable, but
only outside a noise floor, and the floor has to be enforced rather than
documented.

STANDING CAVEAT (convention 7): the >= 5 bps cell is 3 disagreements out of 78.
n=78 is enough to act on and not enough to settle anything. Re-measure as
windows accumulate; this is a working number, not a verdict:

    env -u PYTHONPATH python3 backtest/measure_strike_proxy.py --windows 500

What this module guarantees
---------------------------
1. It NEVER returns spot as the strike. It returns a 60-second TWAP built from
   1-minute klines over the same lookback window Chainlink uses, or None.
2. Every strike carries its `source` and the `noise_floor_bps` it was measured
   against, so a decision logged with a proxy strike stays identifiable as one
   forever (convention 11: a proxy result is not an oracle result).
3. Every failure is counted AND categorised, never silently skipped
   (convention 20). Two drop causes never share one counter.
4. `is_inside_noise_floor()` exists so callers skip with a DISTINCT reason
   rather than folding proxy noise into a real market condition. A strategy
   that declines because the signal is inside the measurement error has NOT
   been tested and must not be recorded as having found nothing.
"""
import logging
import math
import threading
import time
from collections import Counter
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Chainlink's configured lookback, read off `cryptoMarketConfig` on a live
# market: {'id': 'btc-5m-twap-60', ..., 'twapEnabled': True,
# 'twapLookbackSeconds': 60}. Not a guess and not a tunable.
TWAP_LOOKBACK_SEC = 60

# The measured noise floor, in basis points. Below this the proxy disagrees
# with the oracle 39% of the time and carries no information at all.
#
# Convention 17 applies with force: this is a hardcoded threshold and therefore
# an assumption with an expiry date. It was DERIVED from the table above, not
# picked. If it is ever loosened, re-run the harness FIRST and compare against
# these numbers deliberately - a strategy's win rate improving after this value
# is lowered is the exact shape of a false positive.
STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0

# Which asset the floor above was DERIVED from, and what the other two
# actually do AT it. The floor is one constant applied to three underlyings on
# the argument that the instrument is identical (all three are
# `*-5m-twap-60`). These numbers are what that argument costs, measured.
#
# Source: `research/strike_proxy_by_asset_500w.json`, the `rate_pct` at
# threshold_bps == 5.0 in each asset's `cumulative` table, 500 windows
# requested per asset (n AT the floor: btc 175, eth 248, sol 196), measured
# 2026-08-18 (D-285). Expressed as a FRACTION, not a percent.
#
# These REPLACE the 220-window numbers (btc 2.7%, eth 6.6%, sol 14.3%). Every
# rate moved UP at the larger sample and BTC nearly doubled, so the 220w read
# was the optimistic end of sampling noise, not a stable measurement. Worth
# keeping: convention 7's n=100 line is about a rate being STABLE, not merely
# reportable, and ETH cleared that line at 220w (n=106) yet still moved 6.6%
# -> 9.3%. Clearing the bar is necessary, not sufficient.
# SOL's 15.8% is ~3x BTC's 5.1%, so "same instrument" is an argument the data
# only partly supports - do not quote the pooled headline, it averages a coin
# flip and a 96%.
#
# Convention 17: these are hardcoded and therefore have an expiry date.
# `tests/test_strike_proxy.py::test_the_per_asset_error_matches_the_measurement`
# re-reads the JSON and goes red if the two ever drift.
NOISE_FLOOR_SOURCE_ASSET = 'btc'
NOISE_FLOOR_ERROR_BY_ASSET = {'btc': 0.051, 'eth': 0.093, 'sol': 0.158}

#: The threshold `NOISE_FLOOR_ERROR_BY_ASSET` was measured AT. It is a separate
#: constant from the ACTIVE floor on purpose: once the active floor can differ
#: per asset, "the measured error" and "the error at the floor now in force"
#: stop being the same claim, and silently reusing one for the other is how a
#: stale number ends up labelled as a current measurement.
NOISE_FLOOR_ERROR_MEASURED_AT_BPS = 5.0

#: How many windows each of those rates is built on: the `n` at
#: threshold_bps == 5.0 in the same `cumulative` table. D-297 requires this
#: alongside the percentage, because a rate without its sample size is a
#: verdict pretending to be one.
#:
#: At 500 windows ALL THREE clear convention 7's line of 100: btc n=175
#: (9 disagreements), eth n=248 (23), sol n=196 (31). At 220 windows two did
#: not (btc n=75, sol n=84), and `strike_proxy_error_low_sample` rode True on
#: every row those two gated. It is False for all three now.
#:
#: This dict MUST move whenever `NOISE_FLOOR_ERROR_BY_ASSET` moves. A rate
#: from one sample beside an `n` from another is worse than no `n` at all,
#: because it looks qualified. The flag is computed from `n` rather than
#: hardcoded per asset, so re-measuring cleared it with no second edit - the
#: shape D-297 asked for, and here it actually paid.
NOISE_FLOOR_ERROR_N_BY_ASSET = {'btc': 175, 'eth': 248, 'sol': 196}

#: Convention 7's threshold, named once. "A PASS on 87 trades is a shrug"
#: (D-256) is the same claim about the same number.
LOW_SAMPLE_N = 100

# -- per-asset active noise floor -------------------------------------------
#
# MEASURED 2026-08-18 from the 10,276 rows this gate has actually rejected in
# `research/polymarket_paper/polymarket_paper_log.csv`, not assumed.
#
# READ THE DIRECTION OF THE GATE BEFORE CHANGING THESE. It is
# `abs(lead_bps) < floor -> skip`. RAISING this number blocks MORE windows;
# LOWERING it admits more. Every window this gate has ever rejected had
# |lead_bps| < 5.0 (max observed 4.989), which is true by construction, so any
# floor >= 5.0 admits exactly 0.0% more than 5.0 does. A floor of 15 or 25 bps
# does not loosen this gate - it tightens it toward never firing at all.
#
# What each floor would ADMIT, as a share of the windows currently blocked:
#
#     floor      btc      eth      sol    measured proxy disagreement in band
#     0.5 bps   65.9%    62.6%     4.7%     42.2%  <- coin flip, no information
#     1.0 bps   50.8%    56.6%     4.7%     23.5%
#     2.0 bps   20.2%    31.5%     4.7%      6.8%
#     3.0 bps    0.0%    25.1%     4.7%      6.8%
#     5.0 bps    0.0%     0.0%     0.0%      3.8%  <- the previous setting
#
# 1.0 bps is chosen for shadow mode as the LOWEST floor that still sits outside
# the measured coin-flip band. Below 1 bp the proxy disagrees with the oracle
# 42.2% of the time, which is ~50%: a strategy firing there is not learning
# that its edge is weak, it is sampling a random number generator, and
# convention 11 calls that NOT_TESTED rather than a result. At 1 bp and above
# the disagreement is 23.5% and falling - noisy, but it carries information, so
# a loss there is real evidence about the strategy. That is the trade this
# setting makes deliberately: more firing, at a known and logged error rate.
#
# SOL IS NOT UNBLOCKED BY ANY FLOOR, AND THIS IS THE IMPORTANT ONE.
# 95.3% of SOL's blocked windows carry lead_bps EXACTLY 0.0, and across the
# whole log SOL's only two observed nonzero leads are 3.953 and 3.955. That is
# TICK QUANTIZATION, not measurement noise: SOL trades near $75.89 against a
# $0.01 Binance.US tick, so ONE TICK IS 1.318 bps, and a quiet 1-minute bar is
# perfectly flat (O==H==L==C), making spot and the TWAP proxy bit-identical.
# BTC near $64,210 has a 0.002 bps tick and is effectively continuous. So SOL's
# 15.8% disagreement at 5 bps is very likely a DISCRETIZATION artifact rather
# than a worse proxy, and widening SOL specifically would move it from 4.7%
# admitted to 0.0%. SOL therefore gets the SAME floor as the others; its real
# blocker is sub-tick resolution and is open work, not a floor to be tuned.
#
# Convention 17: hardcoded, therefore an assumption with an expiry date.
# Overridable per asset from `config.yaml` via `set_noise_floor_bps_by_asset`.
NOISE_FLOOR_BPS_BY_ASSET: Dict[str, float] = {
    'btc': 1.0,
    'eth': 1.0,
    'sol': 1.0,
}

#: The MEASURED proxy-vs-oracle disagreement rate by |lead_bps| band, as a
#: percent, from the 199-window run in this module's docstring. Bands are
#: [low, high) in bps; the final band is open-ended.
#:
#: This exists so the disagreement rate can ride on EVERY evaluation that uses
#: a proxy strike, not only on the ones the gate rejects. A strategy that fires
#: at 1.2 bps and loses should be readable as "fired inside a 23.5%-error band"
#: without anyone re-deriving that from a comment.
PROXY_DISAGREEMENT_PCT_BY_BAND = (
    (0.0, 1.0, 42.2),
    (1.0, 2.0, 23.5),
    (2.0, 5.0, 6.8),
    (5.0, 10.0, 6.5),
    (10.0, float('inf'), 0.0),
)

# The kline source. Binance.com is geo-blocked from this machine AND answers
# HTTP 200 with an error body, so a status check passes it and the failure only
# surfaces as a missing key. Binance.US is what the rest of this project uses.
KLINES_URL = 'https://api.binance.us/api/v3/klines'
DEFAULT_SYMBOL = 'BTCUSDT'

# Binance caps `limit` at 1000 bars per request.
MAX_KLINES_LIMIT = 1000


def _ohlc4(bar) -> Optional[float]:
    """Average price of a 1m bar, as a discrete stand-in for a continuous TWAP.

    (O+H+L+C)/4 is used rather than the close because a TWAP integrates price
    across the whole minute and a close samples one instant of it. On a quiet
    bar the two agree to the cent; on a fast bar the close is exactly the
    reading that lags the average most, which is the regime the strike matters
    in.

    Returns None on any non-finite or non-positive component rather than
    propagating a NaN into a strike comparison (convention 19: a non-finite
    must fail loudly, not ride along).
    """
    try:
        o, h, l, c = float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4])
    except (TypeError, ValueError, IndexError):
        return None
    for v in (o, h, l, c):
        if not math.isfinite(v) or v <= 0:
            return None
    return (o + h + l + c) / 4.0


class StrikeProxy:
    """Serves 60-second TWAP readings from a cached window of 1m klines.

    Thread-safe: the shadow loop polls every 5 seconds and the cache must not
    be rebuilt concurrently under it.

    The cache is deliberately time-boxed rather than unbounded. A strike is
    only correct for the instant it was sampled at, and an unbounded cache
    would happily serve a reading from an hour ago as though it were current.
    """

    def __init__(self, session: Optional[requests.Session] = None,
                 symbol: str = DEFAULT_SYMBOL, timeout: float = 10.0,
                 refresh_sec: float = 30.0, history_bars: int = 400):
        self.session = session or requests.Session()
        self.symbol = symbol
        self.timeout = timeout
        self.refresh_sec = refresh_sec
        self.history_bars = min(history_bars, MAX_KLINES_LIMIT)

        self._bars: Dict[int, list] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

        # Convention 20: every drop cause gets its own counter. "the API was
        # down" and "the bar exists but is malformed" are different facts and
        # must never share a number.
        self.health: Counter = Counter()

    # -- cache --------------------------------------------------------------

    def _refresh(self, now: float, force: bool = False) -> None:
        """Re-pull the kline window. A failure KEEPS the previous bars.

        Blanking the cache on a transient HTTP error would turn one failed
        request into a strike outage across every strategy that needs it. The
        staleness is bounded by `bar_age_sec`, which rides in the result, so a
        caller can still tell a fresh reading from an old one.
        """
        if not force and self._bars and (now - self._fetched_at) < self.refresh_sec:
            return
        try:
            resp = self.session.get(
                KLINES_URL,
                params={'symbol': self.symbol, 'interval': '1m',
                        'limit': self.history_bars},
                timeout=self.timeout)
        except requests.RequestException as exc:
            self.health[f'klines_request_failed:{type(exc).__name__}'] += 1
            return
        if resp.status_code != 200:
            self.health[f'klines_http_{resp.status_code}'] += 1
            return
        try:
            payload = resp.json()
        except ValueError:
            self.health['klines_bad_json'] += 1
            return
        if not isinstance(payload, list) or not payload:
            # This is the binance.com failure mode: 200 carrying an error body.
            self.health['klines_not_a_list'] += 1
            return

        bars = {}
        for bar in payload:
            try:
                open_ts = int(bar[0]) // 1000
            except (TypeError, ValueError, IndexError):
                self.health['bar_bad_timestamp'] += 1
                continue
            bars[open_ts] = bar
        if not bars:
            self.health['klines_all_bars_dropped'] += 1
            return

        self._bars = bars
        self._fetched_at = now
        self.health['klines_refreshed'] += 1

    # -- readings -----------------------------------------------------------

    def twap60(self, at_ts: int, now: Optional[float] = None) -> Optional[float]:
        """The 60-second TWAP ending at `at_ts`, or None.

        Chainlink's 60s TWAP at time t averages price over [t-60, t), which is
        exactly the 1-minute bar whose open timestamp is t-60. This alignment
        is the reason a 1m kline is the right granularity and a 5m one is not.
        """
        now = time.time() if now is None else now
        with self._lock:
            self._refresh(now)
            bar = self._bars.get(at_ts - TWAP_LOOKBACK_SEC)
        if bar is None:
            self.health['bar_not_in_window'] += 1
            return None
        value = _ohlc4(bar)
        if value is None:
            self.health['bar_malformed_ohlc'] += 1
            return None
        return value

    def strike_for(self, window_ts: int,
                   now: Optional[float] = None) -> Dict[str, object]:
        """The proxy strike for a 5m window, with everything needed to judge it.

        Returns a dict, never a bare float, because a strike without its source
        and its noise floor is exactly the number that gets mistaken for an
        oracle reading three files downstream.

        `strike=None` means "we do not know", and every caller must skip on it.
        It never means zero and never falls back to spot.
        """
        now = time.time() if now is None else now
        strike = self.twap60(window_ts, now=now)
        with self._lock:
            fetched_at = self._fetched_at
        return {
            'strike': strike,
            # Deliberately verbose. Anyone grepping the logs should trip over
            # the word "proxy" rather than have to know that they should.
            'source': None if strike is None else f'proxy_twap{TWAP_LOOKBACK_SEC}_binance_us_{self.symbol}',
            'is_proxy': True,
            'noise_floor_bps': STRIKE_PROXY_NOISE_FLOOR_BPS,
            'bar_age_sec': (None if not fetched_at else round(now - fetched_at, 1)),
            'window_ts': window_ts,
        }


#: Stamped alongside a `None` error when the asset has no measurement. A row
#: that says "we do not know this asset's proxy error" and a row that says
#: "this asset's proxy error is 0%" are opposite claims and never share a
#: field value (convention 20).
ERROR_UNAVAILABLE_FLAG = 'strike_proxy_error_unavailable'


def error_at_floor_pct_for(asset: Optional[str]) -> Optional[float]:
    """This asset's MEASURED proxy-vs-oracle disagreement rate AT the floor.

    D-297. `NOISE_FLOOR_ERROR_BY_ASSET` carries all three as fractions; the
    gated row wants the ONE number for the asset the row is about, as a
    percent, because that is the figure a reader compares against the row's
    own `noise_floor_bps`. Returns a percent (5.1, not 0.051).

    `None` for an unknown or absent asset, never 0.0. A fourth asset added to
    `SHADOW_ASSETS` without being measured must read as UNMEASURED, not as a
    perfect proxy - that is the convention 11 shape at the level of a field.
    Callers pair the `None` with `ERROR_UNAVAILABLE_FLAG` and carry on: a
    missing measurement is not a reason to refuse to log the skip.
    """
    if not asset:
        return None
    fraction = NOISE_FLOOR_ERROR_BY_ASSET.get(str(asset).lower())
    if fraction is None:
        return None
    return round(fraction * 100.0, 1)


def error_sample_at_floor_for(asset: Optional[str]) -> Tuple[Optional[int],
                                                             Optional[bool]]:
    """(n, low_sample) behind this asset's error rate. D-297.

    A percentage with no `n` beside it reads as settled. At the 500-window
    measurement all three clear convention 7's threshold of 100 (btc n=175,
    eth n=248, sol n=196); at 220 windows BTC (75) and SOL (84) did not. The
    row carries the sample so a reader can tell a strong hint from a verdict
    without going back to the JSON.

    `low_sample` is DERIVED from `n`, never asserted per asset, so re-measuring
    on more windows clears the flag by itself - which is exactly what the 500w
    re-measurement did, with no edit to this function. `(None, None)` for an
    unmeasured asset - unknown is not "well sampled", and it is not "poorly
    sampled" either.
    """
    if not asset:
        return None, None
    n = NOISE_FLOOR_ERROR_N_BY_ASSET.get(str(asset).lower())
    if n is None:
        return None, None
    return int(n), bool(int(n) < LOW_SAMPLE_N)


def is_inside_noise_floor(lead_bps: Optional[float],
                          noise_floor_bps: float = STRIKE_PROXY_NOISE_FLOOR_BPS
                          ) -> bool:
    """True when a lead is too small for a proxy strike to resolve it.

    A None lead is INSIDE the floor. "We could not compute it" and "it is too
    small to trust" both mean the strategy must not act, and the caller
    distinguishes them by which reason it logs, not by this returning False.
    """
    if lead_bps is None:
        return True
    if not math.isfinite(lead_bps):
        return True
    return abs(lead_bps) < noise_floor_bps


def noise_floor_bps_for(asset: Optional[str]) -> float:
    """The ACTIVE noise floor for `asset`, in bps.

    Falls back to `STRIKE_PROXY_NOISE_FLOOR_BPS` for an asset with no entry.
    The fallback is deliberately the CONSERVATIVE 5.0 rather than the looser
    shadow-mode value: an unregistered asset is one nobody has measured, and
    the failure mode of guessing loose there is a strategy firing on a strike
    whose error is unknown, which is unreadable rather than merely noisy.
    """
    if not asset:
        return STRIKE_PROXY_NOISE_FLOOR_BPS
    return NOISE_FLOOR_BPS_BY_ASSET.get(
        str(asset).lower(), STRIKE_PROXY_NOISE_FLOOR_BPS)


def set_noise_floor_bps_by_asset(overrides: Optional[Dict[str, object]]) -> Dict[str, float]:
    """Apply `config.yaml` overrides onto `NOISE_FLOOR_BPS_BY_ASSET` in place.

    Called once at shadow-loop startup so the floor is configurable per asset
    without editing this module (convention 17: a threshold nobody can see in
    the config is one nobody reviews).

    Rejects anything non-finite or negative rather than letting it through: a
    NaN floor makes `abs(lead) < floor` False for every lead, which would
    silently DISABLE the gate entirely while looking like a configured value
    (convention 19 - a non-finite must fail loudly, not ride along). A floor of
    exactly 0.0 is legal and means "admit every finite lead, including 0.0".

    Returns the resulting mapping. Raises ValueError on a bad value.
    """
    if not overrides:
        return dict(NOISE_FLOOR_BPS_BY_ASSET)
    for raw_asset, raw_value in dict(overrides).items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            raise ValueError(
                'noise floor for %r must be a number, got %r'
                % (raw_asset, raw_value)) from None
        if not math.isfinite(value) or value < 0:
            raise ValueError(
                'noise floor for %r must be finite and >= 0, got %r'
                % (raw_asset, raw_value))
        NOISE_FLOOR_BPS_BY_ASSET[str(raw_asset).lower()] = value
    return dict(NOISE_FLOOR_BPS_BY_ASSET)


def active_floor_error_pct_for(asset: Optional[str]) -> Optional[float]:
    """The measured disagreement rate at the floor ACTUALLY IN FORCE, or None.

    `error_at_floor_pct_for` returns the rate measured at 5.0 bps, which stops
    describing reality the moment the active floor moves off 5.0. Rather than
    keep reporting a 5.0-bps number under a field that reads as "at the floor",
    this returns None whenever the active floor is not the threshold the
    measurement was taken at. None means UNMEASURED, never 0.0 - the same
    convention 11 shape `error_at_floor_pct_for` already follows for an
    unregistered asset.
    """
    if noise_floor_bps_for(asset) != NOISE_FLOOR_ERROR_MEASURED_AT_BPS:
        return None
    return error_at_floor_pct_for(asset)


def disagreement_pct_for_lead(lead_bps: Optional[float]) -> Optional[float]:
    """The measured proxy-vs-oracle disagreement rate for this lead's band.

    The point of this function is that it applies to every evaluation, not just
    the rejected ones. A window that PASSES the gate at 1.2 bps still carries a
    23.5% measured chance the proxy strike disagrees with the oracle, and that
    number belongs on the row rather than in a comment.

    Returns a percent (23.5, not 0.235). None for a missing or non-finite lead,
    which is UNKNOWN and never 0.0.

    STANDING CAVEAT (convention 7): these bands come from 199 BTC windows. The
    2-5 bps cell is 4 disagreements out of 59. It is enough to act on and not
    enough to settle anything, and it is a BTC measurement being applied to
    three assets - see `NOISE_FLOOR_ERROR_BY_ASSET` for what that costs.
    """
    if lead_bps is None:
        return None
    try:
        magnitude = abs(float(lead_bps))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(magnitude):
        return None
    for low, high, pct in PROXY_DISAGREEMENT_PCT_BY_BAND:
        if low <= magnitude < high:
            return pct
    return None
