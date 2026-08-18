"""Independent Bayesian fair value for a Polymarket binary (Dan1ro0 concept 1).

The point of this module is to produce a probability estimate for "Up wins this
5-minute window" that does NOT read the market's own price. The Polymarket ask
IS the market's probability estimate; deriving our fair value from it and then
comparing the two would be a tautology that always reports zero edge, or worse,
a circular one that reports edge whenever our arithmetic and theirs round
differently.

    fair value  ->  compare to the live ask  ->  the gap is the edge

## The Bayesian frame, and why the prior is exactly 0.5

`revise_probability(prior, multiplier)` is the article's revision rule verbatim:
prior odds times a signal multiplier gives posterior odds. Everything else here
exists to produce ONE honest multiplier to feed it.

The prior is 0.5 because at window open a BTC Up/Down contract is a symmetric
bet on a driftless-over-five-minutes random walk. That is a structural fact
about the instrument, not a tuned parameter. It is also the only prior available
that is not the market price.

## THE CORRELATION PROBLEM IS THE WHOLE DESIGN

Dan1ro0's warning, and the reason this file is shaped the way it is: a BTC move
causes displacement, speed, volume, book imbalance, and ETH/SOL moves ALL AT
ONCE. Multiplying five multipliers derived from one underlying event
manufactures confidence out of nothing. Five 1.3x confirmations of one fact is
3.7x of fabricated certainty, the fair value comes out 12c away from the market
instead of 2c, and the strategy fires into noise it invented.

So `combine_multipliers` groups signals into declared CLUSTERS, takes the
STRONGEST signal within each cluster (largest |log multiplier|), and multiplies
only ACROSS clusters. Today there is exactly ONE cluster, `btc_move`, and every
directional signal is in it - so the combination is a max, never a product.
That is not a placeholder. It is the claim that we currently have one piece of
information about this window, observed several ways.

The cluster machinery exists so that a genuinely independent signal (a
liquidation feed, a scheduled macro print) can be added later and be allowed to
multiply. Adding a signal to `btc_move` costs nothing and can only ever replace
a weaker view of the same fact. That asymmetry is deliberate: the cheap mistake
is under-counting evidence, the expensive one is double-counting it.

## Two of the five listed inputs are NOT directional multipliers

The task brief lists five inputs. Two of them are not evidence about direction
and are not modelled as multipliers, because doing so would be wrong:

  seconds to expiry   Time does not say which way BTC goes. It says how much
                      the displacement we already observe can still be undone.
                      It enters as the diffusion horizon (tau), which is what
                      makes fair value converge to 0.00/1.00 at expiry - the
                      behaviour the brief asks for, obtained from the physics
                      rather than bolted on.
  short-term vol      Volatility is the SCALE the displacement is measured in,
                      not an independent vote. It enters as sigma. Higher recent
                      vol widens sigma, shrinks z, and pulls fair value back
                      toward 0.5 - correctly, because a big move in a violent
                      tape is weaker evidence than the same move in a calm one.

Folding those two into the diffusion signal instead of counting them separately
IS the correlation fix, applied at the point where it is cheapest.

## The diffusion signal

Displacement d from the window open, remaining time tau, per-window sigma:

    sigma_remaining = sigma_window * sqrt(tau / WINDOW_SECONDS)
    z               = d / sigma_remaining
    P(Up)           = Phi(z)

`window_atr` gives a MEAN ABSOLUTE move, not a standard deviation. For a normal
they differ by sqrt(pi/2) ~ 1.2533, and skipping that conversion understates
sigma by 20%, which overstates z by 25%, which is a systematically overconfident
fair value on every single window. `MEAN_ABS_TO_SIGMA` is that constant.

## What this module deliberately refuses to do

  - It never reads a market price, a midpoint, or a last-trade. Book IMBALANCE
    is read (depth, not price) and is capped hard and kept inside `btc_move`, so
    at its strongest it can only replace the diffusion view, never add to it.
    That cap is what stops "our fair value" from quietly becoming "their price
    plus a rounding error".
  - It never returns a probability it cannot justify. Missing spot, missing
    window open, missing ATR, a non-finite input, or a zero/negative sigma all
    produce `usable=False` with a NAMED reason. `usable=False` means CANNOT
    ESTIMATE, never "estimated 0.5" (convention 11), and the caller must skip
    rather than trade a default.
  - It never emits a non-finite number. Probabilities are clamped into
    [PROB_FLOOR, PROB_CEIL] so odds stay finite and the whole estimate stays
    JSON-serialisable under `allow_nan=False` (convention 19).

## Model uncertainty is subtracted, not assumed away

`model_uncertainty` shrinks the combined log-odds toward the prior before the
probability is reported. It is NOT a safety margin on the edge (the strategy
owns that separately). It is the admission that this model has never been
scored against a resolution: a 15% shrink at a 3-sigma displacement moves fair
value by roughly 2c, which is half the strategy's entry threshold. Convention
17 - it is a DEFAULT_* with a stated expiry, and the expiry is the day
`backtest/polymarket_harness.py` scores calibration on real resolutions.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

#: A 5-minute BTC Up/Down window, in seconds. Restated rather than imported from
#: strategies.polymarket.base so the engine never imports the strategy package.
WINDOW_SECONDS = 300

# --- numeric hygiene --------------------------------------------------------

#: Probabilities are clamped into this band before odds are taken. At 0.995 the
#: odds are 199; at 1.0 they are infinite and every downstream log, ratio and
#: json.dump breaks (convention 19). The band is also an honest statement: this
#: model has no business claiming 99.9% on a 5-minute crypto window.
PROB_FLOOR = 0.005
PROB_CEIL = 0.995

#: Floor on the remaining-time horizon, in seconds. At tau=0 the diffusion sigma
#: is zero and z is a division by zero; at tau=1s the model is already saying
#: "whatever the displacement is, it settles", which is the correct limit. The
#: floor picks the limit instead of the exception.
MIN_TAU_SECONDS = 5.0

#: E|X| = sigma * sqrt(2/pi) for a zero-mean normal, so sigma = E|X| * sqrt(pi/2).
#: `window_atr` returns a mean absolute move. Without this the model is
#: overconfident on every window by a constant factor. See the module docstring.
MEAN_ABS_TO_SIGMA = math.sqrt(math.pi / 2.0)

# --- signal caps (each one an assumption with an expiry, convention 17) -----

#: Largest |log multiplier| the speed signal may contribute. 0.35 is a 1.42x
#: odds ratio, which moves a 0.50 fair value to about 0.586. Momentum over 30
#: seconds is a weak, noisy read on a 5-minute outcome and is not allowed to
#: outvote a 2-sigma displacement.
SPEED_LOG_CAP = 0.35

#: Largest |log multiplier| the book-imbalance signal may contribute. Smaller
#: than the speed cap on purpose: resting depth is the closest thing here to
#: reading the market's own opinion, and the tighter the cap the less our
#: "independent" fair value can drift into being the market price restated.
IMBALANCE_LOG_CAP = 0.25

#: Realized-vol ratio is clamped here before it scales sigma. Outside this band
#: the tape is almost always short, stale, or a single spike, and an unclamped
#: ratio of 12 would flatten fair value to 0.5 on exactly the windows that move.
VOL_RATIO_MIN = 0.4
VOL_RATIO_MAX = 3.0

#: Fraction of the combined log-odds given back to the prior. See the docstring.
#: EXPIRY: the day polymarket_harness.py measures calibration on real
#: resolutions, this becomes a fitted number instead of a haircut.
DEFAULT_MODEL_UNCERTAINTY = 0.15

#: The one declared correlation cluster. Everything BTC-move-driven lives here
#: and therefore combines by max, not by product.
CLUSTER_BTC_MOVE = 'btc_move'


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def clamp_probability(p: float) -> float:
    """Squeeze into [PROB_FLOOR, PROB_CEIL] so odds stay finite."""
    return clamp(float(p), PROB_FLOOR, PROB_CEIL)


def is_finite(*values) -> bool:
    for v in values:
        try:
            if not math.isfinite(float(v)):
                return False
        except (TypeError, ValueError):
            return False
    return True


def normal_cdf(z: float) -> float:
    """Phi(z). `math.erf` is exact enough and needs no scipy dependency."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# The revision rule (Dan1ro0's, verbatim)
# ---------------------------------------------------------------------------

def revise_probability(previous_probability: float,
                       signal_multiplier: float) -> float:
    """Bayesian revision: prior odds * signal = posterior odds.

        prior_odds    = p / (1 - p)
        adjusted_odds = prior_odds * multiplier
        posterior     = adjusted_odds / (1 + adjusted_odds)

    The prior is clamped into [PROB_FLOOR, PROB_CEIL] first. At p=0 the prior
    odds are 0 and no multiplier can ever move the estimate; at p=1 they are
    infinite and the posterior is 1.0 whatever the evidence says. Both are
    absorbing states that a 5-minute model must not be able to enter, and both
    arrive silently as `0.0` and `1.0` rather than as an error.

    A non-positive or non-finite multiplier is a caller bug, not a signal of
    certainty, and raises. Returning 0.0 for it would file a code defect under
    "the market is certain the answer is Down" (convention 11).
    """
    p = clamp_probability(previous_probability)
    m = float(signal_multiplier)
    if not math.isfinite(m) or m <= 0:
        raise ValueError(
            'signal_multiplier must be a positive finite number, got {!r}; a '
            'multiplier of 0 or inf is not evidence of certainty'
            .format(signal_multiplier))
    prior_odds = p / (1.0 - p)
    adjusted_odds = prior_odds * m
    return adjusted_odds / (1.0 + adjusted_odds)


def odds(p: float) -> float:
    """p / (1 - p), on the clamped probability."""
    q = clamp_probability(p)
    return q / (1.0 - q)


def probability_from_odds(o: float) -> float:
    return o / (1.0 + o)


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FairValueSignal:
    """One piece of evidence, expressed as an odds multiplier on a 0.5 prior.

    `multiplier > 1` favours Up. `cluster` declares what underlying fact this
    signal is a view of; signals sharing a cluster are treated as correlated and
    only the strongest of them is ever used. Named `FairValueSignal` and not
    `Signal` because `strategies.base.Signal` is a different thing entirely.
    """

    name: str
    cluster: str
    multiplier: float
    detail: Dict[str, float] = field(default_factory=dict)

    @property
    def log_multiplier(self) -> float:
        return math.log(self.multiplier)

    @property
    def strength(self) -> float:
        """|log multiplier|. Direction-blind, which is what a max needs."""
        return abs(self.log_multiplier)

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'cluster': self.cluster,
            'multiplier': round(self.multiplier, 6),
            'log_multiplier': round(self.log_multiplier, 6),
            'detail': {k: (None if not is_finite(v) else round(float(v), 6))
                       for k, v in self.detail.items()},
        }


def diffusion_signal(displacement_usd: float, sigma_window_usd: float,
                     seconds_remaining: float,
                     window_seconds: float = WINDOW_SECONDS,
                     vol_ratio: float = 1.0) -> FairValueSignal:
    """Distance from the window open, scaled by remaining-time volatility.

    This is signals 1, 3 and 4 from the brief in ONE number, because they are
    one number: a displacement is only meaningful relative to how far price can
    still travel, and how far it can still travel is volatility times the square
    root of the time left.

        sigma_remaining = sigma_window * vol_ratio * sqrt(tau / window)
        z               = displacement / sigma_remaining
        P(Up)           = Phi(z)

    `sigma_window_usd` is a STANDARD DEVIATION, not a mean absolute move. Pass
    `window_atr(...) * MEAN_ABS_TO_SIGMA`, or use `estimate_fair_value`, which
    does the conversion for you.

    Raises on a non-positive sigma. A zero sigma means "price cannot move",
    which would make every displacement infinitely significant - the single
    most dangerous silent answer this file could return.
    """
    if not is_finite(displacement_usd, sigma_window_usd, seconds_remaining,
                     window_seconds, vol_ratio):
        raise ValueError('diffusion_signal received a non-finite input')
    if sigma_window_usd <= 0:
        raise ValueError(
            'sigma_window_usd must be positive, got {!r}; a zero sigma makes '
            'every displacement infinitely significant'.format(sigma_window_usd))
    if window_seconds <= 0:
        raise ValueError('window_seconds must be positive')

    ratio = clamp(float(vol_ratio), VOL_RATIO_MIN, VOL_RATIO_MAX)
    tau = max(float(seconds_remaining), MIN_TAU_SECONDS)
    sigma_remaining = sigma_window_usd * ratio * math.sqrt(tau / window_seconds)
    z = displacement_usd / sigma_remaining
    p_up = clamp_probability(normal_cdf(z))
    # Prior odds at 0.5 are exactly 1.0, so the odds implied by this signal ARE
    # its multiplier. Written as a ratio anyway so the identity is visible and
    # a different prior would not silently break it.
    multiplier = odds(p_up) / odds(0.5)
    return FairValueSignal(
        name='diffusion',
        cluster=CLUSTER_BTC_MOVE,
        multiplier=multiplier,
        detail={
            'displacement_usd': displacement_usd,
            'sigma_window_usd': sigma_window_usd,
            'vol_ratio_used': ratio,
            'tau_seconds': tau,
            'sigma_remaining_usd': sigma_remaining,
            'z': z,
            'p_up_implied': p_up,
        },
    )


def speed_signal(recent_speed_usd_per_sec: float,
                 baseline_speed_usd_per_sec: float,
                 log_cap: float = SPEED_LOG_CAP) -> FairValueSignal:
    """Rate of change over the last ~30s against its trailing average.

    SIGNED: a fast move UP favours Up. The magnitude is `tanh` of the excess
    speed ratio and is then scaled by `log_cap`, so the multiplier is bounded by
    construction and a 40x speed spike cannot produce a 40x multiplier.

    This lives in the `btc_move` cluster with the diffusion signal, so it can
    only ever REPLACE that view, never add to it - the displacement it is
    accelerating is the same displacement the diffusion signal already measured.
    In practice it wins only when price is fast but still near the open, which
    is exactly the case where displacement alone says nothing.

    A non-positive or non-finite baseline means the tape cannot support the
    comparison; the signal returns a neutral 1.0 and says so in `detail`, rather
    than dividing by zero into a fabricated certainty.
    """
    if not is_finite(recent_speed_usd_per_sec, baseline_speed_usd_per_sec):
        return FairValueSignal('speed', CLUSTER_BTC_MOVE, 1.0,
                               {'usable': 0.0, 'reason_non_finite': 1.0})
    if baseline_speed_usd_per_sec <= 0:
        return FairValueSignal('speed', CLUSTER_BTC_MOVE, 1.0,
                               {'usable': 0.0, 'reason_no_baseline': 1.0})

    sign = 1.0 if recent_speed_usd_per_sec >= 0 else -1.0
    ratio = abs(recent_speed_usd_per_sec) / baseline_speed_usd_per_sec
    # excess is 0 at the baseline speed, positive when faster. tanh bounds it
    # into (-1, 1) without a hard clip, so the signal degrades smoothly instead
    # of pinning at the cap the moment the tape twitches.
    excess = math.tanh(ratio - 1.0)
    multiplier = math.exp(log_cap * excess * sign)
    return FairValueSignal(
        name='speed',
        cluster=CLUSTER_BTC_MOVE,
        multiplier=multiplier,
        detail={
            'usable': 1.0,
            'recent_speed_usd_per_sec': recent_speed_usd_per_sec,
            'baseline_speed_usd_per_sec': baseline_speed_usd_per_sec,
            'speed_ratio': ratio,
            'excess_tanh': excess,
        },
    )


def book_imbalance(up_book, down_book,
                   depth_band: float = 0.05) -> Tuple[Optional[float], dict]:
    """Net buying pressure for Up, as a number in [-1, 1], or None.

    Both tokens are read, because they are two views of one belief: bidding Up
    and offering Down are the same trade expressed twice, and counting only the
    Up book would miss half the information and be biased by which side happens
    to be quoted.

        pressure_up   = bid depth on Up   + ask depth on Down
        pressure_down = ask depth on Up   + bid depth on Down
        imbalance     = (up - down) / (up + down)

    Depth is counted within `depth_band` of the relevant best price, not over
    the whole book. A 500-share offer parked at 0.02 is not an opinion about
    this window, and letting it into the sum makes the imbalance a measurement
    of where somebody left a stale order.

    Returns `(None, detail)` when neither side has any depth to compare. That is
    a cannot-measure, not a balanced book (convention 11).
    """
    detail: Dict[str, float] = {}

    def _depth(book, side: str) -> float:
        if book is None:
            return 0.0
        if side == 'bid':
            best = book.best_bid
            return 0.0 if best is None else book.bid_depth(best - depth_band)
        best = book.best_ask
        return 0.0 if best is None else book.ask_depth(best + depth_band)

    up_bid = _depth(up_book, 'bid')
    up_ask = _depth(up_book, 'ask')
    down_bid = _depth(down_book, 'bid')
    down_ask = _depth(down_book, 'ask')

    detail.update({'up_bid_depth': up_bid, 'up_ask_depth': up_ask,
                   'down_bid_depth': down_bid, 'down_ask_depth': down_ask})

    pressure_up = up_bid + down_ask
    pressure_down = up_ask + down_bid
    total = pressure_up + pressure_down
    detail['pressure_up'] = pressure_up
    detail['pressure_down'] = pressure_down
    if total <= 0 or not is_finite(total):
        return None, detail
    return (pressure_up - pressure_down) / total, detail


def imbalance_signal(imbalance: Optional[float],
                     log_cap: float = IMBALANCE_LOG_CAP) -> FairValueSignal:
    """Book imbalance as a hard-capped odds multiplier.

    `None` (no depth on either side) returns a neutral 1.0 flagged unusable,
    never a directional guess.

    The cap is the point of this function. Resting depth is downstream of the
    same BTC move as everything else AND it is the closest thing here to
    reading the market's own price, so it is capped tighter than any other
    signal and kept inside `btc_move`. If it ever becomes the strongest signal
    the fair value it produces sits within 6c of a coin flip, which is small
    enough that the strategy's entry threshold does most of the refusing.
    """
    if imbalance is None or not is_finite(imbalance):
        return FairValueSignal('imbalance', CLUSTER_BTC_MOVE, 1.0,
                               {'usable': 0.0})
    value = clamp(float(imbalance), -1.0, 1.0)
    return FairValueSignal(
        name='imbalance',
        cluster=CLUSTER_BTC_MOVE,
        multiplier=math.exp(log_cap * value),
        detail={'usable': 1.0, 'imbalance': value},
    )


# ---------------------------------------------------------------------------
# Combination: max within a cluster, product across clusters
# ---------------------------------------------------------------------------

def combine_multipliers(signals: Sequence[FairValueSignal]
                        ) -> Tuple[float, List[FairValueSignal], dict]:
    """Combine signals WITHOUT double-counting correlated evidence.

    Within each declared cluster the single strongest signal wins (largest
    |log multiplier|); the rest are discarded, counted and named. Across
    clusters the winners multiply, which is the only place independence is
    assumed - and it is assumed because the clusters were DECLARED independent,
    not because the numbers looked uncorrelated.

    Returns `(combined_multiplier, winners, census)`.

    `census` carries `seen`, `used`, `suppressed_correlated` and a per-cluster
    breakdown of which signal won and how many it displaced. Convention 20: the
    signals we threw away are the entire mechanism of this function, so a
    caller must be able to see them. A run where `imbalance` wins every cluster
    is a run whose fair value is close to the market's own, and nobody can spot
    that from the probability alone.

    Ties break on the signal's position in the input sequence, so the ordering
    of `estimate_fair_value`'s signal list is a deliberate precedence, not an
    accident: diffusion first.
    """
    census = {
        'seen': len(signals),
        'used': 0,
        'suppressed_correlated': 0,
        'winner_by_cluster': {},
        'suppressed_by_cluster': {},
    }
    if not signals:
        return 1.0, [], census

    by_cluster: Dict[str, List[FairValueSignal]] = {}
    for sig in signals:
        by_cluster.setdefault(sig.cluster, []).append(sig)

    winners: List[FairValueSignal] = []
    log_total = 0.0
    for cluster in sorted(by_cluster):
        members = by_cluster[cluster]
        # max() keeps the FIRST maximum on ties, which makes input order the
        # precedence rule. See the docstring.
        best = max(members, key=lambda s: s.strength)
        winners.append(best)
        log_total += best.log_multiplier
        census['used'] += 1
        census['suppressed_correlated'] += len(members) - 1
        census['winner_by_cluster'][cluster] = best.name
        census['suppressed_by_cluster'][cluster] = [
            s.name for s in members if s is not best]

    assert census['used'] + census['suppressed_correlated'] == census['seen'], (
        'signal accounting identity broken: {}'.format(census))
    return math.exp(log_total), winners, census


# ---------------------------------------------------------------------------
# The price tape (feeds speed and realized vol)
# ---------------------------------------------------------------------------

class PriceTape:
    """A bounded rolling record of (timestamp, spot) observations.

    The shadow loop polls every ~5 seconds and has a BTC spot on every cycle.
    That is the only sub-window price history available on this path, so speed
    and realized volatility are computed from it rather than from a tick feed
    we do not have.

    Everything here reports how much data it actually had. A speed computed
    from two samples 240 seconds apart is not a 30-second speed, and returning
    it as one would put a fabricated number into the fair value. Each method
    returns None when its window is not covered, and None means CANNOT MEASURE.
    """

    #: Oldest observation kept, in seconds. One full window plus slack, which
    #: is everything either consumer asks for.
    MAX_AGE_SECONDS = 420.0

    #: Fewest samples any statistic will be computed from.
    MIN_SAMPLES = 3

    #: A lookback is only honoured if the tape actually spans this fraction of
    #: it. Below that, the samples exist but describe a different interval.
    MIN_SPAN_FRACTION = 0.6

    def __init__(self, max_age_seconds: float = MAX_AGE_SECONDS):
        self.max_age_seconds = float(max_age_seconds)
        self.samples: List[Tuple[float, float]] = []

    def observe(self, ts: float, price: float) -> bool:
        """Record one spot observation. Returns False if it was refused.

        Refuses non-finite and non-positive prices outright: a NaN spot poisons
        every statistic downstream and serialises to a token no non-Python JSON
        parser accepts (convention 19). Out-of-order timestamps are also
        refused rather than sorted in - a tape that silently reorders itself
        makes a stale read look like a fresh one.
        """
        if not is_finite(ts, price) or price <= 0:
            return False
        if self.samples and ts < self.samples[-1][0]:
            return False
        self.samples.append((float(ts), float(price)))
        cutoff = float(ts) - self.max_age_seconds
        if self.samples[0][0] < cutoff:
            self.samples = [s for s in self.samples if s[0] >= cutoff]
        return True

    def _window(self, lookback_sec: float,
                now: Optional[float] = None) -> List[Tuple[float, float]]:
        if not self.samples:
            return []
        end = self.samples[-1][0] if now is None else float(now)
        return [s for s in self.samples if s[0] >= end - lookback_sec]

    def speed(self, lookback_sec: float = 30.0,
              now: Optional[float] = None) -> Optional[float]:
        """Signed USD/second over `lookback_sec`, or None.

        None when the tape does not span enough of the interval. A "30-second
        speed" measured over 4 seconds is a different statistic wearing the same
        name, and it is the one that would fire the strategy on a poll jitter.
        """
        window = self._window(lookback_sec, now)
        if len(window) < 2:
            return None
        span = window[-1][0] - window[0][0]
        if span < lookback_sec * self.MIN_SPAN_FRACTION or span <= 0:
            return None
        return (window[-1][1] - window[0][1]) / span

    def baseline_speed(self, lookback_sec: float = 300.0,
                       now: Optional[float] = None) -> Optional[float]:
        """Mean ABSOLUTE USD/second over the longer window, or None.

        Absolute, because it is the denominator of a speed ratio: it answers
        "how fast does this tape normally move", which has no direction.
        """
        window = self._window(lookback_sec, now)
        if len(window) < self.MIN_SAMPLES:
            return None
        span = window[-1][0] - window[0][0]
        if span <= 0:
            return None
        moved = sum(abs(window[i][1] - window[i - 1][1])
                    for i in range(1, len(window)))
        return moved / span

    def realized_sigma(self, lookback_sec: float = 300.0,
                       horizon_sec: float = WINDOW_SECONDS,
                       now: Optional[float] = None) -> Optional[float]:
        """Realized sigma in USD over `horizon_sec`, from returns, or None.

        Per-sample log returns are annualised to the horizon by sqrt(time), the
        standard diffusion scaling. Returns None on a short or degenerate tape;
        a zero sigma is also returned as None, because "price did not move in
        the last five minutes" is not a claim that it cannot move in the next
        one, and feeding a 0 into `diffusion_signal` would raise.
        """
        window = self._window(lookback_sec, now)
        if len(window) < self.MIN_SAMPLES:
            return None
        span = window[-1][0] - window[0][0]
        if span < lookback_sec * self.MIN_SPAN_FRACTION or span <= 0:
            return None

        returns: List[float] = []
        for i in range(1, len(window)):
            dt = window[i][0] - window[i - 1][0]
            p0, p1 = window[i - 1][1], window[i][1]
            if dt <= 0 or p0 <= 0 or p1 <= 0:
                continue
            returns.append(math.log(p1 / p0) / math.sqrt(dt))
        if len(returns) < 2:
            return None
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        if var <= 0 or not is_finite(var):
            return None
        sigma_per_root_sec = math.sqrt(var)
        last_price = window[-1][1]
        sigma = sigma_per_root_sec * math.sqrt(horizon_sec) * last_price
        return sigma if is_finite(sigma) and sigma > 0 else None


# ---------------------------------------------------------------------------
# The estimate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FairValueEstimate:
    """Fair value for the UP side, plus everything needed to audit it.

    `usable=False` means the model could not estimate, and `probability` is then
    the untouched prior carried only so callers never handle a None float. A
    caller that trades on an unusable estimate is trading a default (convention
    11), and `reason` names which input was missing.
    """

    probability: float
    usable: bool
    reason: str = ''
    prior: float = 0.5
    combined_multiplier: float = 1.0
    shrunk_multiplier: float = 1.0
    model_uncertainty: float = DEFAULT_MODEL_UNCERTAINTY
    signals: Tuple[FairValueSignal, ...] = ()
    winners: Tuple[FairValueSignal, ...] = ()
    census: Dict[str, object] = field(default_factory=dict)
    inputs: Dict[str, float] = field(default_factory=dict)

    @property
    def probability_down(self) -> float:
        return 1.0 - self.probability

    def for_side(self, outcome_side: str) -> float:
        """Fair value for a named outcome. Up/Yes get p, Down/No get 1 - p.

        An unrecognised outcome label raises. Guessing that an unknown side
        means Up is how a strategy ends up buying the wrong token at a price it
        computed for the other one.
        """
        side = (outcome_side or '').strip().lower()
        if side in ('up', 'yes'):
            return self.probability
        if side in ('down', 'no'):
            return self.probability_down
        raise ValueError(
            'unknown outcome side {!r}; refusing to guess a direction'
            .format(outcome_side))

    def to_dict(self) -> dict:
        return {
            'probability_up': round(self.probability, 6),
            'probability_down': round(self.probability_down, 6),
            'usable': self.usable,
            'reason': self.reason,
            'prior': self.prior,
            'combined_multiplier': round(self.combined_multiplier, 6),
            'shrunk_multiplier': round(self.shrunk_multiplier, 6),
            'model_uncertainty': self.model_uncertainty,
            'signals': [s.to_dict() for s in self.signals],
            'winner_by_cluster': self.census.get('winner_by_cluster', {}),
            'suppressed_correlated': self.census.get('suppressed_correlated', 0),
            'inputs': {k: (None if not is_finite(v) else round(float(v), 6))
                       for k, v in self.inputs.items()},
        }


def _unusable(reason: str, prior: float = 0.5, **inputs) -> FairValueEstimate:
    return FairValueEstimate(probability=prior, usable=False, reason=reason,
                             prior=prior,
                             inputs={k: v for k, v in inputs.items()
                                     if is_finite(v)})


def estimate_fair_value(spot: Optional[float],
                        window_open: Optional[float],
                        atr_usd: Optional[float],
                        seconds_remaining: Optional[float],
                        up_book=None,
                        down_book=None,
                        recent_speed: Optional[float] = None,
                        baseline_speed: Optional[float] = None,
                        realized_sigma_usd: Optional[float] = None,
                        prior: float = 0.5,
                        window_seconds: float = WINDOW_SECONDS,
                        model_uncertainty: float = DEFAULT_MODEL_UNCERTAINTY,
                        ) -> FairValueEstimate:
    """P(this window closes above its open), independent of the market price.

    `atr_usd` is a MEAN ABSOLUTE 5-minute move (what `window_atr` returns); the
    sqrt(pi/2) conversion to a standard deviation happens here so no caller has
    to remember it.

    `window_open` is a BTC exchange bar open used as our own reference. It is
    NOT the settlement strike - these markets settle on a Chainlink 60-second
    TWAP that Gamma does not publish - and a few dollars of disagreement moves
    the fair value slightly rather than mis-settling anything. A caller that
    ever gets a real strike should pass it here instead.

    `realized_sigma_usd`, when supplied, is compared against the ATR-implied
    sigma to produce the `vol_ratio` that widens or narrows the diffusion. When
    it is None the ratio is 1.0 and `inputs['vol_ratio_source']` says so, so a
    window priced without a live vol read is identifiable after the fact.

    Every failure path returns an UNUSABLE estimate with a named reason. There
    is no path that returns a confident-looking 0.5.
    """
    if spot is None:
        return _unusable('no_spot', prior)
    if window_open is None:
        return _unusable('no_window_open', prior)
    if atr_usd is None:
        return _unusable('no_atr', prior)
    if seconds_remaining is None:
        return _unusable('no_window_clock', prior)
    if not is_finite(spot, window_open, atr_usd, seconds_remaining):
        return _unusable('non_finite_input', prior)
    if spot <= 0 or window_open <= 0:
        return _unusable('non_positive_price', prior, spot=spot,
                         window_open=window_open)
    if atr_usd <= 0:
        # Every recent window flat. Sigma is undefined, not zero: a zero sigma
        # would make any displacement infinitely significant.
        return _unusable('zero_atr_undefined_sigma', prior, atr_usd=atr_usd)
    if seconds_remaining < 0:
        return _unusable('window_already_closed', prior,
                         seconds_remaining=seconds_remaining)

    displacement = float(spot) - float(window_open)
    sigma_window = float(atr_usd) * MEAN_ABS_TO_SIGMA

    vol_ratio = 1.0
    vol_source = 'default_no_realized_vol'
    if realized_sigma_usd is not None and is_finite(realized_sigma_usd) \
            and realized_sigma_usd > 0:
        vol_ratio = float(realized_sigma_usd) / sigma_window
        vol_source = 'realized_tape'

    signals: List[FairValueSignal] = []
    # ORDER IS PRECEDENCE. combine_multipliers keeps the first maximum on a
    # tie, and the diffusion signal is the one with a physical justification,
    # so it goes first.
    signals.append(diffusion_signal(displacement, sigma_window,
                                    float(seconds_remaining), window_seconds,
                                    vol_ratio=vol_ratio))
    signals.append(speed_signal(
        0.0 if recent_speed is None else recent_speed,
        0.0 if baseline_speed is None else baseline_speed))
    imbalance, imb_detail = book_imbalance(up_book, down_book)
    signals.append(imbalance_signal(imbalance))

    combined, winners, census = combine_multipliers(signals)

    # Model uncertainty shrinks the evidence toward the prior in LOG-odds
    # space, which is the only space where a shrink is symmetric: shrinking the
    # probability directly would pull a 0.9 and a 0.1 by different amounts.
    u = clamp(float(model_uncertainty), 0.0, 1.0)
    shrunk = math.exp(math.log(combined) * (1.0 - u))
    probability = clamp_probability(revise_probability(prior, shrunk))

    inputs = {
        'spot': float(spot),
        'window_open': float(window_open),
        'displacement_usd': displacement,
        'atr_usd': float(atr_usd),
        'sigma_window_usd': sigma_window,
        'vol_ratio': vol_ratio,
        'seconds_remaining': float(seconds_remaining),
    }
    if imbalance is not None:
        inputs['book_imbalance'] = imbalance
    inputs.update({k: v for k, v in imb_detail.items()})

    est = FairValueEstimate(
        probability=probability,
        usable=True,
        reason='',
        prior=prior,
        combined_multiplier=combined,
        shrunk_multiplier=shrunk,
        model_uncertainty=u,
        signals=tuple(signals),
        winners=tuple(winners),
        census=census,
        inputs=inputs,
    )
    # `vol_ratio_source` is a string and `inputs` is a float map, so it rides on
    # the census instead of being silently dropped by the float filter.
    est.census['vol_ratio_source'] = vol_source
    return est
