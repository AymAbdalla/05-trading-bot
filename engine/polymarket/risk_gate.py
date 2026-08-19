"""Risk gate for Polymarket binary outcome markets.

`engine/risk.py` guards the crypto path and every one of its checks is built on
entry/stop distance: size is risk-per-trade divided by stop distance, and the
fee-to-edge ratio is measured against that same distance. None of that
transfers. A Polymarket share pays exactly $1.00 or exactly $0.00, so:

    loss per share  = the premium paid                      (bounded, certain)
    gain per share  = 1.00 - premium                        (bounded, certain)
    "risk"          = the full premium, always              (there is no stop)

Which means sizing is derived from the PREMIUM, not from a stop distance.

## The defect this module exists to avoid

`strategies/polymarket/base.py` maps a Polymarket entry onto the Signal
interface as entry=premium, stop=0.00, target=1.00. That is the exact payoff,
not a convenient fiction, and it satisfies convention 8 (stop strictly below
entry) because 0.00 really is below every valid entry in [0.01, 0.99] - a
losing share is worth zero, which is the true floor, so there is no such thing
as an inverted stop on a long binary.

But feeding that Signal to the crypto gate goes wrong in both directions:

  - `compute_position_size(entry)` returns `notional_cap / premium`. At a 3c
    premium that is 3,333 shares of a lottery ticket for the same $100 the gate
    thinks it is risking on BTC. The number is arithmetically right and
    economically meaningless, because on a binary the whole notional is the
    risk.
  - `check_fee_to_edge(entry, stop)` computes `edge = (entry - stop) * qty`.
    With stop=0.00 the "edge" is the entire notional, so the fee ratio collapses
    to ~0.2% and the gate waves through every entry, including a 99c share whose
    real upside is 1c. Treat stop=0.00 as an enormous stop distance and every
    binary looks like the safest trade on the book. Treat it as zero distance
    instead and you divide by zero.

So the gate here never asks how far the stop is. It asks how much premium is at
risk, in USDC, and caps that four ways: per trade, per market type, per
correlated group, and per portfolio.

## What this module does NOT do

No side effects. No writes. No orders. It reads a portfolio state it is handed
and returns a verdict. Live execution needs EIP-712 signing and is out of scope
(D-267); `check_order` refuses outright on any mode other than 'paper'.

It also does not decide whether a trade has edge. That is the fair-value model's
job (Dan1ro0 section 1-2, not built yet). This gate only bounds the damage.

## Composition with the crypto ops backstops

The daily/weekly equity backstops in `engine/risk.py` are tail-event catches for
data bugs and marking errors, not trading-risk controls, and they are asset
agnostic. Rather than reimplement them, `check_order` delegates to
`RiskGate.check_ops_backstops(conn)` when it is given a DB connection. That is
why `ops_gate` is a `RiskGate` built from a config whose `notional_cap_usd` is
the POLYMARKET per-trade cap: the backstop thresholds are multiples of a single
worst-case loss, and on a binary that worst case is the premium.

The Polymarket daily loss breaker is separate and additive, because the paper
adapter keeps positions in memory and writes no `equity_snapshots` rows, so
there is no equity series for the crypto backstops to read on this path. It
measures realized resolution PnL since UTC midnight instead. Open positions are
not folded into it - they are bounded independently by
`max_total_exposure_usdc`, which caps the worst case a still-pending book can
add to the day.

## Reading a book we cannot parse (convention 11)

Every USDC cap here is measured against the open book the caller hands in. A
position the gate cannot read is therefore the one input that makes every cap
loosen instead of tighten: drop it and measured exposure comes in LOWER than
real exposure, so the limit does not bind exactly when the portfolio state is
least trustworthy. `aggregate_exposure` counts and categorises every skip
(convention 20) and `check_order` refuses outright when any position was
skipped as unreadable. Unreadable is not empty.

## Every threshold here is an assumption with an expiry date (convention 17)

Every constant below is a module-level DEFAULT_* with a stated rationale and is
overridable from `config['polymarket']['risk']`. None of them is derived from
our own data, because we have none yet: D-268 says these strategies are
NOT_TESTED until the resolution-PnL harness exists. They are deliberately small.
"""
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Payoff constants. Repeated from engine.polymarket.types so this module can be
# imported by a caller that does not want the HTTP client stack pulled in.
# ---------------------------------------------------------------------------
WINNING_REDEMPTION = 1.00
LOSING_REDEMPTION = 0.00

# ---------------------------------------------------------------------------
# THRESHOLDS. Each one: what it is, why this number, and what would change it.
# ---------------------------------------------------------------------------

# Per-trade premium at risk. Matches the paper adapter's own notional_cap_usdc
# default so the two do not disagree. EXPIRY: raise only after the
# resolution-PnL harness scores a strategy above 30bps net (D-268 kill line).
DEFAULT_NOTIONAL_CAP_USDC = 10.0

# Total premium at risk across every open Polymarket position. 10x the per-trade
# cap, i.e. ten full-size losers is the worst a stalled book can do. This is
# what makes it safe to leave open positions out of the daily loss breaker.
DEFAULT_MAX_TOTAL_EXPOSURE_USDC = 100.0

# Concurrent open positions. Matches the paper adapter's default of 10.
DEFAULT_MAX_CONCURRENT_POSITIONS = 10  # D-321: raised 5->10, shadow only

# Positions per (market, outcome side). 1 stops a strategy averaging into the
# same side of the same window and quietly exceeding the per-trade cap.
DEFAULT_MAX_POSITIONS_PER_MARKET_SIDE = 1

# Positions per market across both sides. 2 so a deliberate Up+Down hedge
# (Dan1ro0 4B/4C, box_builder) is still expressible; 1 would ban hedging.
DEFAULT_MAX_POSITIONS_PER_MARKET = 2

# Realized resolution loss since UTC midnight that halts new entries, PER ASSET.
# 3x the per-trade cap, the same shape as the crypto daily_ops_stop_multiplier
# of 3.
#
# PER ASSET as of D-285/D-288, and the number did not change - its SCOPE did.
# $30 was sized when the loop traded one market (BTC). The loop now runs 3
# assets x 15 strategies, and one portfolio-wide $30 budget means the first
# combination of strategies to lose $30 anywhere halts entries EVERYWHERE,
# including on two assets that have not lost a cent. That is not a risk control,
# it is a coupling: SOL's bad hour stops BTC trading. Bucketing by asset keeps
# each asset's risk exactly what it was before the loop went multi-asset.
DEFAULT_DAILY_LOSS_LIMIT_USDC = 30.0

# The SECOND tier, measured across every asset at once. Defense in depth, not a
# duplicate: the per-asset limit catches one asset going wrong, this catches a
# systemic problem (a marking bug, a bad strike proxy, a venue outage) that
# loses money on all of them at once and would otherwise need 3 separate
# breakers to trip before anything stopped.
#
# 5x the per-asset limit rather than 3x (= the sum, at 3 assets), because a
# portfolio limit set AT the sum of its parts can only ever trip simultaneously
# with the last per-asset limit and would never bind on its own. It has to sit
# strictly above the sum to be a distinct control. EXPIRY: this is a house
# number over a number of assets that will change; re-derive it when
# SHADOW_ASSETS grows, or it silently becomes tighter than the sum again.
PORTFOLIO_DAILY_LOSS_LIMIT_MULTIPLE = 5.0
DEFAULT_PORTFOLIO_DAILY_LOSS_LIMIT_USDC = (DEFAULT_DAILY_LOSS_LIMIT_USDC
                                           * PORTFOLIO_DAILY_LOSS_LIMIT_MULTIPLE)

# The bucket for a position whose slug is not in the asset registry - an event
# market, a sports market, anything not btc/eth/sol. NAMED rather than dropped:
# an unrouted loss that fell out of every bucket would be a loss no breaker ever
# measures, which is the exact shape of convention 20's silent `continue`. They
# pool into one bucket that gets the same per-asset limit as a real asset.
UNKNOWN_ASSET = 'unknown'

# Premium at risk in any single market type (btc_5m, btc_15m, event, ...).
DEFAULT_MAX_EXPOSURE_PER_MARKET_TYPE_USDC = 40.0

# Premium at risk across one correlated group in one direction (Dan1ro0 s6:
# BTC 5m Up and BTC 15m Up are the same bet). Deliberately BELOW the sum of the
# per-type caps it spans, or it would never bind.
DEFAULT_MAX_CORRELATED_EXPOSURE_USDC = 50.0

# Tradeable premium band. Outside it the trade is not sized, it is refused.
# 0.99 has 1c of upside and 99c of downside; Dan1ro0 4E says that trade is real
# but one loss erases 75 wins, so it is out until it has its own limits.
DEFAULT_MIN_PREMIUM = 0.01
DEFAULT_MAX_PREMIUM = 0.99

# Exchange minimum order size, in shares.
DEFAULT_MIN_SHARES = 5

# Fraction of full Kelly when sizing_mode='kelly' (Dan1ro0 s6).
DEFAULT_KELLY_FRACTION = 0.20

# 'flat' = always the per-trade cap. 'kelly' = fractional Kelly, hard caps still
# binding on top. Flat is the default because Kelly needs a calibrated
# win-probability estimate and our fair-value model does not exist yet.
DEFAULT_SIZING_MODE = 'flat'

# Bankroll used by Kelly when the caller does not pass one.
DEFAULT_BANKROLL_USDC = 2000.0

# Polymarket charges no explicit CLOB taker fee today. Config knob, not a
# constant, for the same reason the paper adapter has one: "the fee is zero" is
# an assumption with an expiry date.
DEFAULT_TAKER_FEE_RATE = 0.0

# Slug substring -> market type. Substring matching, longest pattern first, so
# adding a longer, more specific pattern cannot be shadowed by a shorter one.
DEFAULT_MARKET_TYPE_PATTERNS: Dict[str, Tuple[str, ...]] = {
    'btc_5m': ('btc-updown-5m', 'bitcoin-up-or-down-5m'),
    'btc_15m': ('btc-updown-15m', 'bitcoin-up-or-down-15m'),
    'btc_1h': ('btc-updown-1h', 'bitcoin-up-or-down-1h'),
    'eth_5m': ('eth-updown-5m', 'ethereum-up-or-down-5m'),
    'eth_15m': ('eth-updown-15m', 'ethereum-up-or-down-15m'),
    'sol_5m': ('sol-updown-5m', 'solana-up-or-down-5m'),
}

# Anything unmatched. Named rather than None so it gets a cap like everything
# else: an unrecognised slug is the case most likely to be a surprise.
DEFAULT_MARKET_TYPE = 'event'

# DECLARED correlation groups, never inferred. Group -> market types that are
# the same underlying bet. Inference from realized correlation would need
# history we do not have, and a group that silently regroups itself is a limit
# that silently stops binding.
DEFAULT_CORRELATION_GROUPS: Dict[str, Tuple[str, ...]] = {
    'btc': ('btc_5m', 'btc_15m', 'btc_1h'),
    'eth': ('eth_5m', 'eth_15m'),
    'sol': ('sol_5m',),
}

# Outcome-name vocabulary. Everything else maps to 'other:<name>', which gets
# its own correlation key rather than being folded into 'up' - guessing the
# direction of an unknown outcome label is how a limit stops meaning anything.
UP_ALIASES = frozenset({'up', 'yes', 'over', 'above', 'higher', 'long'})
DOWN_ALIASES = frozenset({'down', 'no', 'under', 'below', 'lower', 'short'})

SECONDS_PER_DAY = 86400


# ---------------------------------------------------------------------------
# Numeric hygiene (convention 19)
# ---------------------------------------------------------------------------

def finite_or(value, default: float = 0.0) -> float:
    """`float(value)` when that is a finite number, `default` otherwise.

    Applied to everything that lands in a REPORTED field. A NaN premium echoed
    back into the verdict is a rejection nobody can serialise: `json.dump(
    allow_nan=False)` raises on it and every non-Python parser rejects the
    payload, so a correct block becomes an unloggable one.

    This is not a blanket clamp of every non-finite number (convention 12). It
    never stands in for a decision: a corrupt input is detected and refused by
    the check that owns it, with its own reason, before it gets this far.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def json_num(value, digits: int = 6):
    """Round for serialisation, or None when the value is not finite.

    Belt and braces behind `finite_or`: nothing should reach here non-finite,
    and if something ever does, the verdict stays readable by a non-Python
    parser instead of shipping a bare NaN or Infinity token.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v, digits) if math.isfinite(v) else None


def market_key(market_slug: str) -> str:
    """Canonical key for a market slug.

    Case and surrounding whitespace are not a second market. A per-market
    counter that keys on the raw string lets 'BTC-...' and 'btc-...' each hold
    their own position on what is one window, which doubles the per-trade cap
    with no limit firing.
    """
    return (market_slug or '').strip().lower()


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PolymarketVerdict:
    """Result of a Polymarket risk gate check.

    `reason` is 'approved' or a non-empty block reason. There is no code path
    that returns approved=False with an empty reason (convention 6) and
    `test_every_rejection_carries_a_reason` asserts it across every gate.
    """

    approved: bool
    reason: str
    shares: int = 0
    premium: float = 0.0            # per-share price used for sizing
    notional_usdc: float = 0.0      # shares * premium, the premium at risk
    fee_usdc: float = 0.0
    max_loss_usdc: float = 0.0      # notional + fee. On a binary this is exact.
    max_gain_usdc: float = 0.0      # shares * 1.00 - cost - fee
    breakeven_win_rate: float = 0.0  # for a binary, the entry price (plus fee)
    market_type: str = ''
    correlation_key: str = ''
    sizing_mode: str = ''
    binding_constraint: str = ''    # which budget actually set the size
    detail: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'approved': self.approved,
            'reason': self.reason,
            'shares': self.shares,
            'premium': json_num(self.premium),
            'notional_usdc': json_num(self.notional_usdc),
            'fee_usdc': json_num(self.fee_usdc),
            'max_loss_usdc': json_num(self.max_loss_usdc),
            'max_gain_usdc': json_num(self.max_gain_usdc),
            'breakeven_win_rate': json_num(self.breakeven_win_rate),
            'market_type': self.market_type,
            'correlation_key': self.correlation_key,
            'sizing_mode': self.sizing_mode,
            'binding_constraint': self.binding_constraint,
            'detail': {k: json_num(v) for k, v in self.detail.items()},
        }


@dataclass(frozen=True)
class OpenExposure:
    """One open Polymarket position, as the gate sees it.

    `cost_usdc` is the premium paid INCLUDING fee - the exact amount that
    evaporates if the outcome resolves against us. Not a mark, not an estimate.
    """

    market_slug: str
    outcome_side: str
    cost_usdc: float
    shares: float = 0.0
    market_type: Optional[str] = None   # derived from the slug when None


@dataclass(frozen=True)
class ExposureSnapshot:
    """Aggregated open exposure. Every number is USDC premium at risk."""

    total_usdc: float
    by_market_type: Dict[str, float]
    by_correlation_key: Dict[str, float]
    by_market_slug: Dict[str, float]
    count: int
    count_by_market_slug: Dict[str, int]
    count_by_market_side: Dict[Tuple[str, str], int]
    # Convention 20: a loop that skips must count and categorize why.
    counts: Dict[str, int] = field(default_factory=dict)

    @property
    def unreadable(self) -> int:
        """Positions that exist but whose exposure could not be measured.

        Both drop causes, added up for the one caller that has to fail closed
        on either. They stay SEPARATE in `counts` (convention 20) because they
        are different faults: a missing or non-numeric attribute is a schema or
        parse problem, a NaN cost is a marking problem.
        """
        return (self.counts.get('skipped_unreadable', 0)
                + self.counts.get('skipped_non_finite_cost', 0))


# ---------------------------------------------------------------------------
# Classification (declared, never inferred)
# ---------------------------------------------------------------------------

def classify_market_type(slug: str,
                         patterns: Optional[Dict[str, Tuple[str, ...]]] = None,
                         default: str = DEFAULT_MARKET_TYPE) -> str:
    """Map a market slug to a declared market type.

    Longest pattern first so a more specific pattern added later cannot be
    shadowed by a shorter one already in the table.
    """
    patterns = DEFAULT_MARKET_TYPE_PATTERNS if patterns is None else patterns
    s = market_key(slug)
    if not s:
        return default
    flat: List[Tuple[str, str]] = []
    for mtype, pats in patterns.items():
        for p in pats:
            flat.append((str(p).lower(), mtype))
    flat.sort(key=lambda pair: len(pair[0]), reverse=True)
    for pat, mtype in flat:
        if pat and pat in s:
            return mtype
    return default


def normalize_direction(outcome_side: str) -> str:
    """'Up'/'Yes' -> 'up', 'Down'/'No' -> 'down', anything else -> 'other:<x>'.

    An unrecognised outcome label never merges into 'up'. Two markets whose
    sides we cannot read are not thereby the same bet, and pretending they are
    would let a correlation limit pass by accident.
    """
    s = (outcome_side or '').strip().lower()
    if s in UP_ALIASES:
        return 'up'
    if s in DOWN_ALIASES:
        return 'down'
    return 'other:{}'.format(s or 'unknown')


def correlation_key(market_type: str, outcome_side: str,
                    groups: Optional[Dict[str, Tuple[str, ...]]] = None) -> str:
    """Key under which correlated exposure is aggregated.

    Shape is '<group>:<direction>'. A market type in no declared group gets
    'ungrouped:<market_type>:<direction>', so the correlation cap still binds
    per type rather than silently not applying.

    Opposite directions are NOT netted. Buying Up at 55c and Down at 49c is not
    a risk reduction - the pair costs $1.04 and redeems $1.00, so the "hedged"
    portion is a guaranteed 4c loss (Dan1ro0 4C). Netting would report that
    position as flat. Gross aggregation is the conservative and correct
    reading; a real pair-cost check belongs in the strategy, not here.
    """
    groups = DEFAULT_CORRELATION_GROUPS if groups is None else groups
    direction = normalize_direction(outcome_side)
    for group, types in groups.items():
        if market_type in tuple(types):
            return '{}:{}'.format(group, direction)
    return 'ungrouped:{}:{}'.format(market_type, direction)


# ---------------------------------------------------------------------------
# Sizing math
# ---------------------------------------------------------------------------

def fractional_kelly(win_probability: float, entry_price: float,
                     fraction: float = DEFAULT_KELLY_FRACTION) -> float:
    """Fraction of bankroll to stake (Dan1ro0 section 6).

        net_odds   = (1 - entry) / entry
        full_kelly = (net_odds * p_win - p_loss) / net_odds
        stake      = max(full_kelly * fraction, 0)

    Returns 0.0 for a non-positive edge and for any degenerate input
    (entry outside (0, 1), probability outside [0, 1]). Zero means "do not
    size this", which the caller turns into an explicit rejection - it never
    becomes a silent minimum-size trade.
    """
    if not (0.0 < entry_price < 1.0):
        return 0.0
    if not (0.0 <= win_probability <= 1.0):
        return 0.0
    loss_probability = 1.0 - win_probability
    net_odds = (1.0 - entry_price) / entry_price
    if net_odds <= 0:
        return 0.0
    full_kelly = (net_odds * win_probability - loss_probability) / net_odds
    return max(full_kelly * fraction, 0.0)


def aggregate_exposure(
    open_positions: Iterable[OpenExposure],
    patterns: Optional[Dict[str, Tuple[str, ...]]] = None,
    groups: Optional[Dict[str, Tuple[str, ...]]] = None,
) -> ExposureSnapshot:
    """Aggregate open premium at risk by market, market type and correlation.

    Convention 20: nothing is skipped silently. `counts` carries `seen`,
    `counted`, `skipped_unreadable`, `skipped_non_finite_cost`,
    `skipped_non_positive_cost` and `unclassified_slug`, and the accounting
    identity `seen - skipped_* == counted` is asserted before returning.

    The two cost skips are deliberately NOT one counter. A cost of $0.00 is a
    real and benign observation; a NaN cost is an exposure we failed to
    measure. Merging them reports the second as the first and hides the only
    one of the two that has to fail closed (conventions 11 and 20).

    Market slugs and outcome sides are canonicalised before they are used as
    keys. Two spellings of one market are one market.
    """
    total = 0.0
    by_type: Dict[str, float] = {}
    by_corr: Dict[str, float] = {}
    by_slug: Dict[str, float] = {}
    count_by_slug: Dict[str, int] = {}
    count_by_side: Dict[Tuple[str, str], int] = {}
    counts = {
        'seen': 0,
        'counted': 0,
        'skipped_non_positive_cost': 0,
        'skipped_non_finite_cost': 0,
        'skipped_unreadable': 0,
        'unclassified_slug': 0,
    }

    for pos in open_positions or ():
        counts['seen'] += 1
        try:
            slug = str(getattr(pos, 'market_slug'))
            side = str(getattr(pos, 'outcome_side'))
            cost = float(getattr(pos, 'cost_usdc'))
        except (AttributeError, TypeError, ValueError):
            # Unreadable, not zero. An exposure we cannot parse is not an
            # exposure of nothing (convention 11).
            counts['skipped_unreadable'] += 1
            logger.warning('pm risk: unreadable open exposure %r', pos)
            continue
        if not math.isfinite(cost):
            # A NaN or infinite premium paid is a marking fault, not an
            # affordability answer, so convention 12 does not apply: this is a
            # cost already incurred, and a cost we cannot read is unreadable.
            counts['skipped_non_finite_cost'] += 1
            logger.warning('pm risk: open exposure with non-finite cost: %r',
                           getattr(pos, 'market_slug', pos))
            continue
        if cost <= 0:
            counts['skipped_non_positive_cost'] += 1
            continue

        slug_key = market_key(slug)
        side_key = normalize_direction(side)
        mtype = getattr(pos, 'market_type', None) or classify_market_type(
            slug, patterns)
        if mtype == DEFAULT_MARKET_TYPE:
            counts['unclassified_slug'] += 1
        ckey = correlation_key(mtype, side, groups)

        total += cost
        by_type[mtype] = by_type.get(mtype, 0.0) + cost
        by_corr[ckey] = by_corr.get(ckey, 0.0) + cost
        by_slug[slug_key] = by_slug.get(slug_key, 0.0) + cost
        count_by_slug[slug_key] = count_by_slug.get(slug_key, 0) + 1
        key = (slug_key, side_key)
        count_by_side[key] = count_by_side.get(key, 0) + 1
        counts['counted'] += 1

    skipped = sum(v for k, v in counts.items() if k.startswith('skipped_'))
    assert counts['seen'] - skipped == counts['counted'], (
        'exposure accounting identity broken: {}'.format(counts))

    return ExposureSnapshot(
        total_usdc=total,
        by_market_type=by_type,
        by_correlation_key=by_corr,
        by_market_slug=by_slug,
        count=counts['counted'],
        count_by_market_slug=count_by_slug,
        count_by_market_side=count_by_side,
        counts=counts,
    )


def utc_midnight_seconds(now: Optional[float] = None) -> int:
    """Start of the current UTC day, in unix SECONDS.

    Seconds, not milliseconds: `PaperPosition.opened_ts` is `int(time.time())`.
    The crypto gate works in ms because the `orders` table does. Mixing the two
    is a 1000x error that reads as "no trades today, ever".
    """
    now = time.time() if now is None else now
    n = int(now)
    return n - (n % SECONDS_PER_DAY)


def realized_pnl_today(positions: Sequence[object], now: Optional[float] = None,
                       ts_attr: str = 'opened_ts') -> Tuple[float, Dict[str, int]]:
    """Realized resolution PnL since UTC midnight, plus a skip census.

    Returns (pnl_usdc, counts). Negative pnl is a loss.

    CAVEAT, stated because it will bite someone: `PaperPosition` records
    `opened_ts` but no resolution timestamp, so a position is attributed to the
    day it was OPENED. For a 5-minute market that is the same day except across
    the midnight boundary, where a window opened at 23:58 and resolved at 00:03
    is booked to the previous day. That understates today's loss by at most one
    position's premium, which the per-trade cap bounds. Pass an explicit
    `realized_pnl_today_usdc` to `check_order` if the caller has better
    timestamps.

    Convention 20: every skip is counted and categorized.
    """
    cutoff = utc_midnight_seconds(now)
    counts = {
        'seen': 0,
        'counted': 0,
        'skipped_still_open': 0,
        'skipped_before_today': 0,
        'skipped_missing_ts': 0,
        'skipped_missing_pnl': 0,
    }
    pnl = 0.0
    for pos in positions or ():
        counts['seen'] += 1
        resolution = getattr(pos, 'resolution', None)
        pnl_value = getattr(pos, 'pnl_usdc', None)
        if resolution is None:
            counts['skipped_still_open'] += 1
            continue
        if pnl_value is None:
            # Resolved but unpriced. NOT zero PnL (convention 11).
            counts['skipped_missing_pnl'] += 1
            logger.warning('pm risk: resolved position with no pnl_usdc: %r',
                           getattr(pos, 'position_id', pos))
            continue
        ts = getattr(pos, ts_attr, None)
        if ts is None:
            counts['skipped_missing_ts'] += 1
            continue
        if int(ts) < cutoff:
            counts['skipped_before_today'] += 1
            continue
        pnl += float(pnl_value)
        counts['counted'] += 1

    skipped = sum(v for k, v in counts.items() if k.startswith('skipped_'))
    assert counts['seen'] - skipped == counts['counted'], (
        'realized-pnl accounting identity broken: {}'.format(counts))
    return pnl, counts


def asset_bucket(market_slug: Optional[str]) -> str:
    """The daily-loss bucket a market slug belongs to. Never None.

    Delegates to the ONE asset registry (`engine.polymarket.assets`) rather than
    splitting the slug here, so 'btc' means the same thing to the breaker as it
    does to the shadow loop's per-asset strategy routing. An unregistered slug
    is `UNKNOWN_ASSET`, not a guess: `asset_for_slug` deliberately refuses to
    return 'trump' for an election market, and inventing a bucket per unknown
    prefix would give every one-off event market its own private $30 budget.

    Imported lazily so `risk_gate` stays importable by a caller that has not
    pulled in the rest of the polymarket package.
    """
    try:
        from engine.polymarket.assets import asset_for_slug
    except Exception:                   # pragma: no cover - defensive
        return UNKNOWN_ASSET
    return asset_for_slug(market_slug) or UNKNOWN_ASSET


def realized_pnl_today_by_asset(positions: Sequence[object],
                                now: Optional[float] = None,
                                ts_attr: str = 'opened_ts'
                                ) -> Tuple[Dict[str, float], float,
                                           Dict[str, int]]:
    """`realized_pnl_today`, split by asset. Returns (by_asset, total, counts).

    `by_asset` carries a key for every asset that had a counted position today
    and no keys for the rest - an asset with no resolved trades is ABSENT, which
    the caller reads as "no loss to measure", not as a measured 0.0.

    `total` is the exact sum of `by_asset.values()`, asserted below. It is not
    recomputed independently: two code paths producing "the portfolio PnL" is
    how the two tiers of the breaker would eventually disagree about the same
    day, and the assertion is cheaper than the investigation.

    Every skip category is `realized_pnl_today`'s, unchanged, because this
    counts the same positions the same way (convention 20: one cause, one name).
    """
    counts = {
        'seen': 0,
        'counted': 0,
        'skipped_still_open': 0,
        'skipped_before_today': 0,
        'skipped_missing_ts': 0,
        'skipped_missing_pnl': 0,
    }
    cutoff = utc_midnight_seconds(now)
    by_asset: Dict[str, float] = {}
    total = 0.0
    for pos in positions or ():
        counts['seen'] += 1
        resolution = getattr(pos, 'resolution', None)
        pnl_value = getattr(pos, 'pnl_usdc', None)
        if resolution is None:
            counts['skipped_still_open'] += 1
            continue
        if pnl_value is None:
            counts['skipped_missing_pnl'] += 1
            logger.warning('pm risk: resolved position with no pnl_usdc: %r',
                           getattr(pos, 'position_id', pos))
            continue
        ts = getattr(pos, ts_attr, None)
        if ts is None:
            counts['skipped_missing_ts'] += 1
            continue
        if int(ts) < cutoff:
            counts['skipped_before_today'] += 1
            continue
        bucket = asset_bucket(getattr(pos, 'market_slug', None))
        value = float(pnl_value)
        by_asset[bucket] = by_asset.get(bucket, 0.0) + value
        total += value
        counts['counted'] += 1

    skipped = sum(v for k, v in counts.items() if k.startswith('skipped_'))
    assert counts['seen'] - skipped == counts['counted'], (
        'realized-pnl-by-asset accounting identity broken: {}'.format(counts))
    assert math.isclose(sum(by_asset.values()), total, rel_tol=1e-9,
                        abs_tol=1e-9), (
        'per-asset pnl does not sum to the portfolio pnl: {} vs {}'
        .format(by_asset, total))
    return by_asset, total, counts


def exposures_from_adapter(adapter) -> Tuple[OpenExposure, ...]:
    """Build the gate's view of open exposure from a PolymarketPaperAdapter.

    Read-only. Lives here rather than on the adapter so the gate stays a pure
    function of a state it is handed, and so nothing in the adapter has to
    change to be gated.
    """
    out: List[OpenExposure] = []
    for pos in adapter.open_positions():
        out.append(OpenExposure(
            market_slug=pos.market_slug,
            outcome_side=pos.outcome_side,
            cost_usdc=pos.max_loss_usdc,   # premium + fee, the exact loss
            shares=pos.shares,
        ))
    return tuple(out)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

class PolymarketRiskGate:
    """Pure risk gate for Polymarket binaries. No side effects, no orders.

    Read `config['polymarket']['risk']`. Every knob has a module-level default.
    """

    def __init__(self, config: Optional[dict] = None, ops_gate=None):
        cfg = (config or {}).get('polymarket', {})
        risk = cfg.get('risk', {}) or {}

        self.notional_cap_usdc = float(
            risk.get('notional_cap_usdc', DEFAULT_NOTIONAL_CAP_USDC))
        self.max_total_exposure_usdc = float(
            risk.get('max_total_exposure_usdc', DEFAULT_MAX_TOTAL_EXPOSURE_USDC))
        self.max_concurrent_positions = int(
            risk.get('max_concurrent_positions', DEFAULT_MAX_CONCURRENT_POSITIONS))
        self.max_positions_per_market_side = int(
            risk.get('max_positions_per_market_side',
                     DEFAULT_MAX_POSITIONS_PER_MARKET_SIDE))
        self.max_positions_per_market = int(
            risk.get('max_positions_per_market', DEFAULT_MAX_POSITIONS_PER_MARKET))
        self.daily_loss_limit_usdc = float(
            risk.get('daily_loss_limit_usdc', DEFAULT_DAILY_LOSS_LIMIT_USDC))
        self.portfolio_daily_loss_limit_usdc = float(
            risk.get('portfolio_daily_loss_limit_usdc',
                     DEFAULT_PORTFOLIO_DAILY_LOSS_LIMIT_USDC))
        self.max_exposure_per_market_type_usdc = float(
            risk.get('max_exposure_per_market_type_usdc',
                     DEFAULT_MAX_EXPOSURE_PER_MARKET_TYPE_USDC))
        # Per-type overrides, e.g. {'event': 20.0}. Falls back to the flat cap.
        self.market_type_exposure_overrides = dict(
            risk.get('market_type_exposure_overrides', {}) or {})
        self.max_correlated_exposure_usdc = float(
            risk.get('max_correlated_exposure_usdc',
                     DEFAULT_MAX_CORRELATED_EXPOSURE_USDC))
        self.min_premium = float(risk.get('min_premium', DEFAULT_MIN_PREMIUM))
        self.max_premium = float(risk.get('max_premium', DEFAULT_MAX_PREMIUM))
        self.min_shares = int(risk.get('min_shares', DEFAULT_MIN_SHARES))
        self.kelly_fraction = float(
            risk.get('kelly_fraction', DEFAULT_KELLY_FRACTION))
        self.sizing_mode = str(risk.get('sizing_mode', DEFAULT_SIZING_MODE))
        self.bankroll_usdc = float(
            risk.get('bankroll_usdc',
                     cfg.get('starting_equity_usdc', DEFAULT_BANKROLL_USDC)))
        self.taker_fee_rate = float(
            risk.get('taker_fee_rate',
                     cfg.get('taker_fee_rate', DEFAULT_TAKER_FEE_RATE)))
        self.market_type_patterns = dict(
            risk.get('market_type_patterns', DEFAULT_MARKET_TYPE_PATTERNS))
        self.correlation_groups = dict(
            risk.get('correlation_groups', DEFAULT_CORRELATION_GROUPS))

        # Optional engine.risk.RiskGate for the shared equity tail backstops.
        # Injected, never constructed here, so this module never imports the
        # crypto execution path (and a missing DB is never this gate's problem).
        self.ops_gate = ops_gate

        # Consultation log for those backstops. A backstop that is silently
        # never called is worse than no backstop, because the config says it is
        # on, so every consultation is recorded on the ops gate itself and can
        # be inspected as `ops_gate.calls`. A gate that already keeps its own
        # log is left alone, so one consultation is never recorded twice.
        self._own_ops_call_log = (ops_gate is not None
                                  and not hasattr(ops_gate, 'calls'))
        if self._own_ops_call_log:
            ops_gate.calls = []

    # -- classification passthroughs ----------------------------------------

    def market_type(self, market_slug: str) -> str:
        return classify_market_type(market_slug, self.market_type_patterns)

    def correlation_key(self, market_slug: str, outcome_side: str) -> str:
        return correlation_key(self.market_type(market_slug), outcome_side,
                               self.correlation_groups)

    def market_type_cap(self, market_type: str) -> float:
        return float(self.market_type_exposure_overrides.get(
            market_type, self.max_exposure_per_market_type_usdc))

    # -- sizing --------------------------------------------------------------

    def unit_cost_usdc(self, premium: float,
                       fee_rate: Optional[float] = None) -> float:
        """All-in cost of ONE share: premium plus the taker fee on it.

        Sizing on premium alone and adding the fee afterwards puts real risk
        above every cap by exactly the fee. At today's 0% that is invisible,
        which is why it is wired now rather than discovered the day Polymarket
        turns fees on.
        """
        rate = self.taker_fee_rate if fee_rate is None else fee_rate
        return float(premium) * (1.0 + float(rate))

    def shares_for_premium(self, premium: float,
                           budget_usdc: Optional[float] = None,
                           fee_rate: Optional[float] = None) -> int:
        """Whole shares affordable at `premium` for a USDC budget.

        THIS is the sizing primitive, and it takes a premium, not a stop
        distance. `shares * premium` is both the notional and the maximum loss;
        on a binary those are the same number, which is the whole reason this
        function exists separately from `RiskGate.compute_position_size`.

        The budget bounds the ALL-IN cost, premium plus fee, so the number of
        shares returned can never breach the cap it was sized against.

        Returns 0 when the budget cannot buy the exchange minimum - "could not
        run", never "ran and lost" (convention 11, the D-249 shape).
        """
        budget = self.notional_cap_usdc if budget_usdc is None else budget_usdc
        try:
            premium = float(premium)
            budget = float(budget)
        except (TypeError, ValueError):
            return 0
        if premium <= 0 or not math.isfinite(premium):
            return 0
        if budget <= 0 or not math.isfinite(budget):
            return 0
        unit = self.unit_cost_usdc(premium, fee_rate)
        if unit <= 0 or not math.isfinite(unit):
            return 0
        n = int(math.floor(budget / unit))
        return n if n >= self.min_shares else 0

    def kelly_budget_usdc(self, fair_value: float, premium: float,
                          bankroll_usdc: Optional[float] = None) -> float:
        """USDC that fractional Kelly wants to stake. Hard caps bind on top.

        Returns NaN for an unusable bankroll rather than 0.0, because 0.0 reads
        as "no edge" and an unreadable bankroll is not a measurement of no edge
        (convention 11). `check_order` refuses on it explicitly.
        """
        bankroll = self.bankroll_usdc if bankroll_usdc is None else bankroll_usdc
        try:
            bankroll = float(bankroll)
        except (TypeError, ValueError):
            return float('nan')
        if not math.isfinite(bankroll) or bankroll <= 0:
            return float('nan')
        frac = fractional_kelly(fair_value, premium, self.kelly_fraction)
        return max(frac * bankroll, 0.0)

    # -- circuit breaker -----------------------------------------------------

    def _loss_or_reason(self, pnl_value, label: str) -> Tuple[bool, object]:
        """(True, loss) or (False, reason). Unreadable state is never a number.

        Shared by both tiers so an unparseable PnL is refused identically
        whichever one saw it. A tier that quietly coerced garbage to 0.0 would
        stop existing exactly when the portfolio state is least trustworthy.
        """
        try:
            pnl = float(pnl_value or 0.0)
        except (TypeError, ValueError):
            return False, ('daily_loss_breaker: {} realized pnl is not a number '
                           '({!r}) - refusing to trade on unreadable state'
                           .format(label, pnl_value))
        if not math.isfinite(pnl):
            return False, ('daily_loss_breaker: {} realized pnl is not finite '
                           '({!r}) - refusing to trade on unreadable state'
                           .format(label, pnl_value))
        return True, -pnl               # positive when we are down

    def check_daily_loss_breaker(self, realized_pnl_today_usdc: float,
                                 asset: Optional[str] = None,
                                 portfolio_pnl_today_usdc: Optional[float] = None
                                 ) -> Tuple[bool, str]:
        """Halt new entries once today's realized loss exceeds the limit.

        Same shape as `RiskGate.check_ops_backstops`: a signed drop compared
        against a threshold that is a multiple of one worst-case loss, returning
        (ok, reason). It does not duplicate that check - it measures a different
        series (realized resolution PnL, not equity snapshots) because the
        Polymarket paper adapter writes no equity_snapshots rows.

        TWO TIERS, and which arguments you pass decides how many run:

        `realized_pnl_today_usdc` is the PnL the PER-ASSET limit is measured
        against, and `asset` names the bucket it was filtered to. `asset=None`
        means the caller did not split the book, so the per-asset limit is
        applied to whatever it handed in. That fallback is deliberately the
        TIGHT direction - one $30 budget for the whole book is the pre-D-285
        behaviour and is stricter than three $30 budgets, so a caller that has
        not been updated is over-protected rather than under-protected.

        `portfolio_pnl_today_usdc` runs the SECOND, higher limit across every
        asset. `None` means the caller did not supply it, so that tier does NOT
        run - it is not treated as 0.0, which would read as a measured
        break-even and silently pass a check that was never performed
        (convention 11).

        The per-asset tier is checked FIRST. When both would trip, the reason a
        human reads should name the asset that actually did the damage, not the
        aggregate it rolls up into.
        """
        # 'unsplit-book' rather than 'portfolio', because those are two
        # different facts: this tier applies the PER-ASSET limit, and when the
        # caller did not split the book it is applying it to everything. A
        # reason string saying 'portfolio' next to the per-asset limit would
        # read as the second tier having tripped.
        label = 'asset={}'.format(asset) if asset else 'unsplit-book'
        ok, loss = self._loss_or_reason(realized_pnl_today_usdc, label)
        if not ok:
            return False, loss
        if self.daily_loss_limit_usdc > 0 and loss > self.daily_loss_limit_usdc:
            return False, ('daily_loss_breaker: {} realized loss today '
                           '=${:.2f} > limit=${:.2f}'
                           .format(label, loss, self.daily_loss_limit_usdc))

        if portfolio_pnl_today_usdc is not None:
            ok, total_loss = self._loss_or_reason(portfolio_pnl_today_usdc,
                                                  'portfolio')
            if not ok:
                return False, total_loss
            if self.portfolio_daily_loss_limit_usdc > 0 and total_loss > self.portfolio_daily_loss_limit_usdc:
                return False, (
                    'daily_loss_breaker: portfolio realized loss today '
                    '=${:.2f} > portfolio limit=${:.2f} (per-asset limit '
                    '${:.2f} was not breached on {}) - this is the systemic '
                    'guard, not one bad asset'
                    .format(total_loss, self.portfolio_daily_loss_limit_usdc,
                            self.daily_loss_limit_usdc, label))
        return True, 'ok'

    # -- the gate ------------------------------------------------------------

    def check_order(self,
                    market_slug: str,
                    outcome_side: str,
                    premium: float,
                    open_positions: Sequence[OpenExposure] = (),
                    realized_pnl_today_usdc: float = 0.0,
                    asset: Optional[str] = None,
                    portfolio_pnl_today_usdc: Optional[float] = None,
                    requested_shares: Optional[float] = None,
                    fair_value: Optional[float] = None,
                    sizing_mode: Optional[str] = None,
                    bankroll_usdc: Optional[float] = None,
                    mode: str = 'paper',
                    conn=None) -> PolymarketVerdict:
        """Full risk check for one Polymarket buy. Returns a PolymarketVerdict.

        `premium` is the per-share price we would pay (the limit, or the walked
        average if the caller already has one). Loss is bounded at
        `shares * premium`; upside at `shares * (1.00 - premium)`.

        Check order mirrors the crypto gate: backstops, then breakers, then
        counts, then sizing. Every rejection carries a non-empty reason
        (convention 6), and every rejection is serialisable: a corrupt input is
        named in the reason string, never echoed into a numeric field
        (convention 19).

        It always returns a verdict. A risk gate that raises instead of
        answering is a risk gate the caller can step past with a bare `except`.
        """
        mtype = self.market_type(market_slug)
        ckey = self.correlation_key(market_slug, outcome_side)

        def block(reason: str, **detail) -> PolymarketVerdict:
            assert reason, 'a rejection with no reason is a silent rejection'
            return PolymarketVerdict(False, reason, premium=finite_or(premium),
                                     market_type=mtype, correlation_key=ckey,
                                     sizing_mode=sizing_mode or self.sizing_mode,
                                     detail={k: finite_or(v)
                                             for k, v in detail.items()})

        # 0. Paper only. Live needs EIP-712 signing and Aym's approval (D-267).
        if mode != 'paper':
            return block("live_mode_not_authorized: mode={!r}, Polymarket is "
                         "paper-only until a strategy clears the graveyard AND "
                         "Aym approves (D-267)".format(mode))

        # 1. Shared equity tail backstops, delegated not duplicated.
        if self.ops_gate is not None and conn is not None:
            if self._own_ops_call_log:
                self.ops_gate.calls.append(conn)
            ops_ok, ops_reason = self.ops_gate.check_ops_backstops(conn)
            if not ops_ok:
                return block(ops_reason)

        # 2. Polymarket daily loss circuit breaker, per asset then portfolio.
        # `asset` is passed through UNCHANGED and is never inferred from
        # `market_slug` here. It labels which slice of the book
        # `realized_pnl_today_usdc` was measured over, and only the caller knows
        # that. Deriving it from the slug would stamp `asset=btc` on a number
        # that might be the whole portfolio - a reason string that reads as a
        # measurement and is not one.
        breaker_ok, breaker_reason = self.check_daily_loss_breaker(
            realized_pnl_today_usdc, asset=asset,
            portfolio_pnl_today_usdc=portfolio_pnl_today_usdc)
        if not breaker_ok:
            return block(breaker_reason)

        # 3. Premium validity.
        #
        # Convention 8 note: the harness rejects an inverted stop because a
        # stop at or above entry on a long is a bug. On a binary the stop is
        # 0.00 and it is genuinely below every valid entry, so it can never
        # invert - which means convention 8's check has nothing to catch here
        # and cannot be reused as a sanity check. THIS band replaces it. It is
        # the only thing standing between the gate and a "0.00 premium" or a
        # "1.40 premium" arriving from a mis-parsed book, both of which the
        # stop-based check would have waved through.
        try:
            premium = float(premium)
        except (TypeError, ValueError):
            return block('invalid_premium: {!r} is not a number'.format(premium))
        if not math.isfinite(premium):
            return block('invalid_premium: {!r} is not finite'.format(premium))
        if premium < self.min_premium or premium > self.max_premium:
            return block('invalid_premium: {:.4f} outside tradeable band '
                         '[{:.2f}, {:.2f}]'
                         .format(premium, self.min_premium, self.max_premium))

        snap = aggregate_exposure(open_positions, self.market_type_patterns,
                                  self.correlation_groups)

        # 3b. An open book we could not fully read (convention 11). Every cap
        # below is measured against this snapshot, so a position that fell out
        # of it does not make the limits slightly loose, it makes them not
        # bind: measured exposure comes in under real exposure and the caller
        # gets headroom it has not earned. Nine positions we cannot parse are
        # nine positions, not zero. Refuse.
        if snap.unreadable:
            return block(
                'unreadable_open_positions: {} of {} open positions could not '
                'be measured ({} unparseable, {} non-finite cost) - refusing '
                'rather than trading against a book we cannot read'
                .format(snap.unreadable, snap.counts['seen'],
                        snap.counts['skipped_unreadable'],
                        snap.counts['skipped_non_finite_cost']),
                open_positions_seen=snap.counts['seen'],
                open_positions_unreadable=snap.unreadable)

        # 4. Max concurrent positions.
        if snap.count >= self.max_concurrent_positions:
            return block('max_concurrent_positions: {} open (limit: {})'
                         .format(snap.count, self.max_concurrent_positions))

        # 5. Positions in this market, per side and overall. Keyed on the
        # canonical slug and the normalized direction, so 'up'/'Up'/'Yes' on
        # one market is one side of one market rather than three free bets.
        slug_key = market_key(market_slug)
        side_key = normalize_direction(outcome_side)
        side_count = snap.count_by_market_side.get((slug_key, side_key), 0)
        if side_count >= self.max_positions_per_market_side:
            return block('max_positions_per_market_side: {} open on {} {} '
                         '(limit: {})'.format(side_count, slug_key, side_key,
                                              self.max_positions_per_market_side))
        slug_count = snap.count_by_market_slug.get(slug_key, 0)
        if slug_count >= self.max_positions_per_market:
            return block('max_positions_per_market: {} open on {} (limit: {})'
                         .format(slug_count, slug_key,
                                 self.max_positions_per_market))

        # 6. Sizing. Every cap is expressed as a USDC BUDGET, and the smallest
        # budget wins. Ordered exposure-caps-first so a genuine risk limit is
        # reported in preference to the per-trade cap when budgets tie.
        type_cap = self.market_type_cap(mtype)
        budgets: List[Tuple[str, float]] = [
            ('max_total_exposure',
             self.max_total_exposure_usdc - snap.total_usdc),
            ('max_exposure_per_market_type',
             type_cap - snap.by_market_type.get(mtype, 0.0)),
            ('max_correlated_exposure',
             self.max_correlated_exposure_usdc
             - snap.by_correlation_key.get(ckey, 0.0)),
            ('notional_cap', self.notional_cap_usdc),
        ]

        mode_used = (sizing_mode or self.sizing_mode).lower()
        if mode_used not in ('flat', 'kelly'):
            return block('invalid_sizing_mode: {!r} (expected flat or kelly)'
                         .format(mode_used))
        if mode_used == 'kelly':
            if fair_value is None:
                # Kelly without a probability estimate is not a smaller bet, it
                # is an unmeasurable one. Refuse rather than fall back to flat.
                return block('kelly_requires_fair_value: sizing_mode=kelly but '
                             'no fair_value was supplied')
            kelly_usdc = self.kelly_budget_usdc(fair_value, premium,
                                                bankroll_usdc)
            if not math.isfinite(kelly_usdc):
                # A NaN budget compares False against everything, so it drops
                # silently out of min() and the trade sizes at the next cap up:
                # the Kelly constraint stops existing without saying so.
                return block('invalid_bankroll: {!r} is not a usable bankroll, '
                             'so the kelly budget cannot be computed'
                             .format(self.bankroll_usdc
                                     if bankroll_usdc is None
                                     else bankroll_usdc))
            if kelly_usdc <= 0:
                return block('kelly_no_edge: fair_value={:.4f} vs premium '
                             '{:.4f} gives non-positive Kelly stake'
                             .format(finite_or(fair_value), premium))
            budgets.append(('kelly_stake', kelly_usdc))

        unit_cost = self.unit_cost_usdc(premium)
        if unit_cost <= 0 or not math.isfinite(unit_cost):
            return block('invalid_fee_rate: taker_fee_rate={!r} gives a '
                         'per-share cost of {!r}'
                         .format(self.taker_fee_rate, unit_cost))

        if requested_shares is not None:
            # Silently ignoring a degenerate request sizes at the FULL cap,
            # which is the opposite of what a caller asking for 0, -5 or NaN
            # shares meant. The skip has to be visible (convention 20).
            try:
                requested = float(requested_shares)
            except (TypeError, ValueError):
                requested = float('nan')
            if not math.isfinite(requested) or requested <= 0:
                return block('invalid_requested_shares: {!r} is not a positive '
                             'number of shares'.format(requested_shares))
            budgets.append(('requested_shares', requested * unit_cost))

        binding, budget = min(budgets, key=lambda kv: kv[1])
        budget = max(budget, 0.0)
        shares = self.shares_for_premium(premium, budget)
        affordable = int(math.floor(budget / unit_cost))

        detail = {
            'budget_usdc': budget,
            'open_total_usdc': snap.total_usdc,
            'open_market_type_usdc': snap.by_market_type.get(mtype, 0.0),
            'open_correlated_usdc': snap.by_correlation_key.get(ckey, 0.0),
            'market_type_cap_usdc': type_cap,
            'unit_cost_usdc': unit_cost,
        }
        for name, value in budgets:
            detail['budget_' + name] = value

        if shares < self.min_shares:
            if binding in ('notional_cap', 'kelly_stake', 'requested_shares'):
                # Capital was available, it just does not buy the exchange
                # minimum at this premium. Could not run; did not lose.
                return block(
                    'unsizable_at_cap: budget ${:.2f} at {:.4f}/share all-in '
                    'buys {} shares, below min {} (binding: {})'
                    .format(budget, unit_cost, affordable, self.min_shares,
                            binding), **detail)
            return block(
                '{}: ${:.2f} of headroom at {:.4f}/share all-in buys {} '
                'shares, below min {} (open ${:.2f} of cap ${:.2f})'
                .format(binding, budget, unit_cost, affordable, self.min_shares,
                        detail['open_total_usdc'] if binding == 'max_total_exposure'
                        else (detail['open_market_type_usdc']
                              if binding == 'max_exposure_per_market_type'
                              else detail['open_correlated_usdc']),
                        self.max_total_exposure_usdc
                        if binding == 'max_total_exposure'
                        else (type_cap
                              if binding == 'max_exposure_per_market_type'
                              else self.max_correlated_exposure_usdc)),
                **detail)

        notional = shares * premium
        fee = notional * self.taker_fee_rate
        cost = notional + fee
        return PolymarketVerdict(
            approved=True,
            reason='approved',
            shares=shares,
            premium=premium,
            notional_usdc=notional,
            fee_usdc=fee,
            max_loss_usdc=cost,
            max_gain_usdc=shares * WINNING_REDEMPTION - cost,
            breakeven_win_rate=cost / (shares * WINNING_REDEMPTION),
            market_type=mtype,
            correlation_key=ckey,
            sizing_mode=mode_used,
            binding_constraint=binding,
            detail=detail,
        )

    def check_adapter_order(self, adapter, market_slug: str, outcome_side: str,
                            premium: float, **kw) -> PolymarketVerdict:
        """`check_order` with the portfolio state read off a paper adapter.

        Convenience only. The adapter is read, never written.

        THIS is where the daily loss breaker becomes per-asset (D-285/D-288),
        and it is the only place the split happens. The shadow loop calls this
        once per leg with that leg's slug, so routing here means the loop needs
        no asset plumbing of its own - and, more importantly, there is exactly
        ONE site that decides which bucket a slug belongs to. A second derivation
        in the loop is convention 23's failure mode in reverse: two places that
        agree today and drift silently.

        Both tiers are supplied: `realized_pnl_today_usdc` is THIS asset's
        slice, `portfolio_pnl_today_usdc` is the whole book. An asset with no
        resolved trades today is absent from the split, which reads as 0.0 for
        the per-asset tier - correct, because no resolved trade is genuinely no
        realized loss, unlike the unreadable cases the tier refuses on.
        """
        positions = list(adapter.positions.values())
        by_asset, total, _counts = realized_pnl_today_by_asset(positions)
        bucket = asset_bucket(market_slug)
        kw.setdefault('realized_pnl_today_usdc', by_asset.get(bucket, 0.0))
        kw.setdefault('asset', bucket)
        kw.setdefault('portfolio_pnl_today_usdc', total)
        return self.check_order(market_slug, outcome_side, premium,
                                open_positions=exposures_from_adapter(adapter),
                                **kw)
