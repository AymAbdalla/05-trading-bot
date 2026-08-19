"""Deterministic, model-free entry constraints (D-342 R2).

The one thing this module does NOT take is a probability. That is the whole
point of it. Every other gate in the system - `RiskGate.check_fee_to_edge`, the
Polymarket gate's Kelly sizing, every strategy's edge threshold - multiplies a
number the fair_value model produced, and the measured slope of that model is
0.30 (execution is ~9% of the loss; the model is 91%). A control whose binding
depends on a miscalibrated forecast inherits the miscalibration. These three
constraints bind on notional alone, so they are exactly as correct when the
model is wrong as when it is right.

## The three constraints

- **per-trade notional cap** - the premium one entry may commit.
- **per-event notional cap** - keyed on `(asset_family, window_ts)`. Same-epoch
  BTC/ETH/SOL Up/Down windows are ONE correlated bet and must not stack: btc/eth
  co-resolution measured at phi = +0.529. This is the genuinely new constraint;
  see "Relationship to the Polymarket gate" below.
- **aggregate notional cap** - total open premium at risk.

Plus a **portfolio drawdown halt**, which lives in `engine/risk/events.py`
because engaging it is a side effect. It routes into `engine.halt` - the single
definition of the kill switch. There is no second halt path.

## Why a per-EVENT cap is not the correlation cap we already have

`engine/polymarket/risk_gate.py:correlation_key` aggregates on
`(declared_group, direction)`, and its declared groups are per-asset
(`btc`, `eth`, `sol`). Two facts follow, and both are why this module exists:

1. It groups btc with btc across ALL windows, so it cannot see that a btc, an
   eth and a sol position in the SAME five-minute epoch are one bet on one
   move. Measured on the book: **247 of 298 events (82.9%) span more than one
   asset, and 173 span all three.**
2. At its peak the book's concurrent exposure in a SINGLE event was $76.20
   against a total concurrent peak of $76.97 - i.e. at the worst moment,
   essentially the entire book was one correlated epoch. That is the exposure
   no existing cap measures.

## Relationship to the Polymarket gate (READ BEFORE WIRING)

The per-trade and aggregate caps here DUPLICATE `notional_cap_usdc` and
`max_total_exposure_usdc` in the Polymarket gate. That duplication is
deliberate for now - D-342 R2 specifies all three constraints in one
model-free evaluator - but it must NOT survive activation. `engine/halt.py`
already records what two copies of one control cost: "three copies of a kill
switch is three chances for one of them to point somewhere else, and the
failure mode is silent."

**At wiring time exactly one of the two must be authoritative.** The
established pattern is delegation, not a second gate: the Polymarket gate
already defers its equity tail backstops to `engine.risk.RiskGate` rather than
reimplementing them. Follow that. Nothing here is wired into any live path.

## Defaults are measured, not guessed

A cap set above the book's natural range is decorative, and a decorative cap is
this module's own kill condition (dead if no constraint binds >5 times in 30
days). Every default below was read off `db/trading.db` on 2026-08-19 - see
each constant.

## Failure direction

Unreadable state fails CLOSED (convention 11: an unreadable state is not an
empty one). An exposure we cannot parse is not an exposure of nothing; counting
it as zero would silently relax every cap, which is the one direction a safety
control must never fail. Nothing is skipped silently (convention 20) - every
denial names a constraint and carries its numbers.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Tuple

# ---------------------------------------------------------------------------
# Constraint names. These are the values that land in `risk_events`, and the
# kill condition is "group `risk_events` by constraint name" - so they are an
# interface, not log text. Do not reword them casually.
# ---------------------------------------------------------------------------

CONSTRAINT_PER_TRADE = 'per_trade_notional'
CONSTRAINT_PER_EVENT = 'per_event_notional'
CONSTRAINT_AGGREGATE = 'aggregate_notional'
CONSTRAINT_DRAWDOWN = 'portfolio_drawdown'
CONSTRAINT_UNREADABLE = 'unreadable_exposure'
CONSTRAINT_INVALID_CANDIDATE = 'invalid_candidate'

ALL_CONSTRAINTS = (
    CONSTRAINT_PER_TRADE,
    CONSTRAINT_PER_EVENT,
    CONSTRAINT_AGGREGATE,
    CONSTRAINT_DRAWDOWN,
    CONSTRAINT_UNREADABLE,
    CONSTRAINT_INVALID_CANDIDATE,
)


# ---------------------------------------------------------------------------
# Asset families. DECLARED, never inferred - the same discipline
# `DEFAULT_CORRELATION_GROUPS` states: "a group that silently regroups itself is
# a limit that silently stops binding."
# ---------------------------------------------------------------------------

#: The correlated crypto Up/Down family. All of these settle on the same
#: mechanic (`*-5m-twap-60`, verified live 2026-08-18 in `assets.py`) against
#: majors that co-move; btc/eth co-resolution phi = +0.529.
CRYPTO_UPDOWN_FAMILY = 'crypto_updown'

#: Anything we cannot classify. Named rather than None so it still gets a cap:
#: an unrecognised slug is the case most likely to be a surprise.
UNKNOWN_FAMILY = 'unclassified'

#: xrp and doge are NOT in the `assets.py` registry (their exchange symbols are
#: unverified) but their Up/Down markets exist and are the same instrument. If
#: one ever reaches this evaluator, folding it into the correlated family is the
#: conservative reading; leaving it out would let it stack uncapped.
_CRYPTO_UPDOWN_TICKERS = frozenset({'btc', 'eth', 'sol', 'xrp', 'doge'})

#: Polymarket slugs these markets two ways; both spellings are one market
#: (`DEFAULT_MARKET_TYPE_PATTERNS` carries the same pairs).
_TICKER_ALIASES = {
    'bitcoin': 'btc',
    'ethereum': 'eth',
    'solana': 'sol',
    'ripple': 'xrp',
    'dogecoin': 'doge',
}

_UPDOWN_MARKERS = ('-updown-', '-up-or-down-')


def asset_family_for_slug(slug):
    """The correlated family a market slug belongs to.

    `'btc-updown-5m-1787022000'`, `'eth-updown-15m-...'` and
    `'solana-up-or-down-5m-...'` all return `CRYPTO_UPDOWN_FAMILY`. That
    collapsing is the point: they are one bet on one move, and the per-event cap
    exists to stop them stacking inside a single epoch.

    Everything else returns `UNKNOWN_FAMILY` rather than a guessed string. A
    plausible wrong family is not a checkable answer (convention 11).
    """
    text = str(slug or '').strip().lower()
    if not text:
        return UNKNOWN_FAMILY
    head = text.split('-', 1)[0]
    ticker = _TICKER_ALIASES.get(head, head)
    if ticker in _CRYPTO_UPDOWN_TICKERS and any(m in text for m in _UPDOWN_MARKERS):
        return CRYPTO_UPDOWN_FAMILY
    return UNKNOWN_FAMILY


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Exposure:
    """One unit of premium at risk - an open position, or a candidate entry.

    `notional_usd` is premium paid (`shares * price`). On a binary that is also
    the maximum loss exactly, so one number carries both.

    `window_ts` is the epoch the market resolves on. `None` means "no epoch",
    which is NOT the same as epoch 0 - it gets its own per-event bucket rather
    than merging with every other undated position.
    """

    asset_family: str
    window_ts: Optional[int]
    notional_usd: float
    market_slug: Optional[str] = None

    @classmethod
    def from_slug(cls, market_slug, window_ts, notional_usd):
        """Build one, deriving the family from the slug."""
        return cls(asset_family=asset_family_for_slug(market_slug),
                   window_ts=window_ts, notional_usd=notional_usd,
                   market_slug=market_slug)


@dataclass(frozen=True)
class EquityState:
    """Current and running-peak account equity, both in USD.

    `peak_usd` is the caller's job to carry (it comes from `equity_snapshots`).
    Passing it in rather than reading it here is what keeps this module pure and
    therefore testable without a database.
    """

    current_usd: float
    peak_usd: float

    def drawdown_frac(self):
        """Fractional drawdown from the running peak, or None if unmeasurable.

        None is returned for a non-finite or non-positive peak. Convention 11:
        that is "we could not measure the drawdown", never "the drawdown is
        zero" - the caller must treat it as unreadable, not as safe.
        """
        peak, cur = self.peak_usd, self.current_usd
        if not (math.isfinite(peak) and math.isfinite(cur)) or peak <= 0:
            return None
        return max(0.0, (peak - cur) / peak)


@dataclass(frozen=True)
class Limits:
    """The caps. Absolute USD, not fractions of equity - a cap that scales with
    a shrinking account loosens exactly as things get worse."""

    #: Matches `DEFAULT_NOTIONAL_CAP_USDC` in the Polymarket gate on purpose.
    #: Measured: per-trade notional p50 $6.20, p90 $9.50, max $10.00 (n=2,333),
    #: i.e. the existing $10 cap already binds hard. A second, different number
    #: here would be two caps disagreeing about one thing.
    per_trade_notional_usd: float = 10.0

    #: Measured peak CONCURRENT per-event notional: p50 $18.76, p75 $30.22,
    #: p90 $43.12, max $76.20 across 298 events. $30 binds on 75 of 298 (25.2%)
    #: - comfortably above the median so ordinary operation is untouched, and
    #: far from decorative. This is the constraint the kill condition watches.
    per_event_notional_usd: float = 30.0

    #: Measured concurrent aggregate exposure: p90 $39.02, p99 $57.34, peak
    #: $76.97. The Polymarket gate's existing $100 cap NEVER bound on this book
    #: - it is decorative by the section-5 definition. $60 binds in the top ~1%.
    #: See "Relationship to the Polymarket gate": only one may be authoritative.
    aggregate_notional_usd: float = 60.0

    #: Portfolio drawdown that engages the halt. MEASURED WARNING: this book's
    #: historical max drawdown is 35.99%, and a halt at 25% would have fired 3
    #: times (at 10%, 8 times; only >=40% never fires). Any non-decorative
    #: drawdown halt WOULD stop the current shadow book. That is a live decision
    #: for whoever activates this, not a default to accept silently.
    max_drawdown_frac: float = 0.25


DEFAULT_LIMITS = Limits()


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    """Allow, or Deny naming exactly one binding constraint.

    `constraint` is None only when `allowed` is True. `detail` carries the
    numbers that produced the verdict so a `risk_events` row is self-contained -
    a denial you cannot reconstruct is a missing number (convention 20).
    """

    allowed: bool
    constraint: Optional[str] = None
    reason: str = 'allow'
    detail: Dict[str, object] = field(default_factory=dict)
    #: True only for a drawdown breach: the caller must engage `engine.halt`.
    halt_required: bool = False


def allow(detail=None):
    """An approving Decision."""
    return Decision(True, None, 'allow', dict(detail or {}), False)


def deny(constraint, reason, detail, halt_required=False):
    """A denying Decision naming `constraint`."""
    return Decision(False, constraint, reason, dict(detail), halt_required)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _readable_notional(item):
    """The finite, non-negative notional on `item`, or None if unreadable.

    None means "we failed to measure this exposure". It is never coerced to
    0.0: a zero would quietly shrink every sum this feeds, and a cap computed
    from an under-counted book stops binding without saying so.
    """
    try:
        value = float(getattr(item, 'notional_usd'))
    except (AttributeError, TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def event_key(item):
    """The `(asset_family, window_ts)` bucket an exposure aggregates into."""
    family = str(getattr(item, 'asset_family', '') or UNKNOWN_FAMILY)
    window = getattr(item, 'window_ts', None)
    if window is not None:
        try:
            window = int(window)
        except (TypeError, ValueError):
            # An unparseable epoch is its own bucket, not "no epoch". Merging it
            # into the None bucket would pool unrelated markets under one cap.
            window = str(window)
    return family, window


def check(open_positions, candidate, equity, limits=DEFAULT_LIMITS):
    """Allow or deny `candidate` given the open book and equity.

    Deterministic and side-effect free: same inputs, same verdict, no clock, no
    database, no filesystem, and deliberately no probability. Recording the
    denial and engaging the halt belong to `engine/risk/events.py`.

    Constraints are evaluated most-severe first, and the FIRST one that binds is
    the one reported, so a denial always names a single cause.
    """
    cand_notional = _readable_notional(candidate)
    if cand_notional is None:
        return deny(
            CONSTRAINT_INVALID_CANDIDATE,
            'candidate notional is unreadable or negative',
            {'candidate_notional_usd': repr(getattr(candidate, 'notional_usd', None)),
             'market_slug': getattr(candidate, 'market_slug', None)})

    # 1. Portfolio drawdown. Checked before the caps because a book in breach
    #    should stop entirely, not merely stop adding to one event.
    drawdown = equity.drawdown_frac()
    if drawdown is None:
        return deny(
            CONSTRAINT_UNREADABLE,
            'equity is unreadable, so drawdown could not be measured',
            {'current_usd': repr(equity.current_usd),
             'peak_usd': repr(equity.peak_usd)})
    if drawdown > limits.max_drawdown_frac:
        return deny(
            CONSTRAINT_DRAWDOWN,
            'portfolio drawdown {:.4f} exceeds {:.4f}'.format(
                drawdown, limits.max_drawdown_frac),
            {'drawdown_frac': drawdown,
             'limit_frac': limits.max_drawdown_frac,
             'current_usd': equity.current_usd,
             'peak_usd': equity.peak_usd},
            halt_required=True)

    # 2. Per-trade cap. Independent of the book, so it is checked before any
    #    aggregation - an oversized single entry is wrong on an empty book too.
    if cand_notional > limits.per_trade_notional_usd:
        return deny(
            CONSTRAINT_PER_TRADE,
            'entry notional {:.4f} exceeds per-trade cap {:.4f}'.format(
                cand_notional, limits.per_trade_notional_usd),
            {'candidate_notional_usd': cand_notional,
             'limit_usd': limits.per_trade_notional_usd,
             'market_slug': candidate.market_slug})

    # Aggregate the open book ONCE, and account for every row seen
    # (convention 20: the identity seen == counted + unreadable must hold).
    total_open = 0.0
    per_event = {}
    seen = 0
    unreadable = 0
    for pos in open_positions or ():
        seen += 1
        notional = _readable_notional(pos)
        if notional is None:
            unreadable += 1
            continue
        total_open += notional
        key = event_key(pos)
        per_event[key] = per_event.get(key, 0.0) + notional

    if unreadable:
        # Fail CLOSED. We know the book is bigger than we can measure, so every
        # sum below is a lower bound, and a cap checked against a lower bound is
        # a cap that passes things it should have stopped.
        return deny(
            CONSTRAINT_UNREADABLE,
            '{} of {} open exposures are unreadable; caps cannot be '
            'evaluated'.format(unreadable, seen),
            {'seen': seen, 'unreadable': unreadable,
             'counted': seen - unreadable,
             'measured_total_usd': total_open})

    # 3. Per-event cap. The correlated-epoch constraint.
    key = event_key(candidate)
    event_open = per_event.get(key, 0.0)
    event_after = event_open + cand_notional
    if event_after > limits.per_event_notional_usd:
        return deny(
            CONSTRAINT_PER_EVENT,
            'event ({}, {}) would reach {:.4f}, exceeding per-event cap '
            '{:.4f}'.format(key[0], key[1], event_after,
                            limits.per_event_notional_usd),
            {'asset_family': key[0], 'window_ts': key[1],
             'event_open_usd': event_open,
             'candidate_notional_usd': cand_notional,
             'event_after_usd': event_after,
             'limit_usd': limits.per_event_notional_usd,
             'market_slug': candidate.market_slug})

    # 4. Aggregate cap.
    total_after = total_open + cand_notional
    if total_after > limits.aggregate_notional_usd:
        return deny(
            CONSTRAINT_AGGREGATE,
            'aggregate exposure would reach {:.4f}, exceeding cap '
            '{:.4f}'.format(total_after, limits.aggregate_notional_usd),
            {'open_total_usd': total_open,
             'candidate_notional_usd': cand_notional,
             'total_after_usd': total_after,
             'limit_usd': limits.aggregate_notional_usd,
             'open_positions': seen})

    return allow({'open_total_usd': total_open,
                  'total_after_usd': total_after,
                  'asset_family': key[0], 'window_ts': key[1],
                  'event_after_usd': event_after,
                  'candidate_notional_usd': cand_notional,
                  'open_positions': seen})
