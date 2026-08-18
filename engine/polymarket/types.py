"""Dataclasses for Polymarket binary outcome markets.

Polymarket is not a price market. A share is a claim that pays exactly $1.00 if
its outcome resolves true and exactly $0.00 if it does not, so the quoted price
IS the market's probability estimate. Everything here keeps that framing: sizes
are in SHARES, prices are in dollars-per-share on [0, 1], and the payoff is
resolution-based, never path-based.

Two consequences that bite if you forget them:
  - Max loss per share is the premium paid. Bounded. So is max gain (1 - premium).
    A 60% win rate at 55c makes money; the same 60% at 65c loses it.
  - There is no "current PnL" until resolution unless you mark to the book, and
    marking to a thin book on a 5-minute market is mostly noise.
"""
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Polymarket protocol constants, verified against the live API responses
# (`min_order_size` and `tick_size` come back on every /book payload).
PRICE_TICK = 0.01
MIN_SHARES = 5
WINNING_REDEMPTION = 1.00
LOSING_REDEMPTION = 0.00

# Every price on this venue is a probability. Anything outside [0, 1] is a
# corrupt field, not an extreme quote.
MIN_PRICE = 0.0
MAX_PRICE = 1.0


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """`float()` that refuses non-finite values.

    `float('Infinity')`, `float('inf')` and `float('NaN')` all SUCCEED in
    Python, so a corrupt API field like `{"price": "NaN"}` sails straight
    through a bare `float()` and into a PriceLevel. From there it poisons every
    average it touches and, on the way out, `json.dump` emits a bare
    `NaN`/`Infinity` token that `JSON.parse` and most non-Python parsers
    reject. That is convention 19's failure mode, caught at the point of entry
    rather than at the point of serialisation - which matters here because the
    modules that ultimately serialise these values (the paper adapter's trade
    log) are not in this package.

    Convention 12 still holds elsewhere: a cost RATE may legitimately be `inf`.
    A quoted price or size never can.
    """
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return out


@dataclass(frozen=True)
class Outcome:
    """One side of a binary market ("Up"/"Down", "Yes"/"No")."""

    name: str
    token_id: str
    price: Optional[float] = None  # last known Gamma outcomePrice, may be stale

    @property
    def is_resolved_winner(self) -> bool:
        """Gamma reports a resolved market's outcomePrices as exactly 1/0."""
        return self.price == 1.0

    @property
    def is_resolved_loser(self) -> bool:
        return self.price == 0.0


@dataclass(frozen=True)
class Market:
    """A Polymarket market as returned by the Gamma API.

    `condition_id` keys the CLOB price-history and Data API endpoints.
    `outcomes` carries the token ids used for orderbook and price queries.

    ## The clobTokenIds ordering contract (read this before indexing)

    Gamma ships `outcomes` and `clobTokenIds` as two parallel arrays. The ONLY
    documented guarantee is positional: `clobTokenIds[i]` is the CLOB token for
    `outcomes[i]`. Polymarket's own API reference adds "Index 0 = Yes, Index 1
    = No", but that holds only for markets whose outcomes are literally
    labelled Yes/No. The BTC Up/Down markets this project trades label them
    "Up"/"Down", and other markets use their own labels.

    So `market_from_gamma` zips the two arrays positionally and REFUSES to
    build a Market when their lengths disagree, and this class exposes lookup
    by NAME (`outcome`, `token_id`) rather than by index. Reaching for
    `outcomes[0]` and calling it "Yes" is the bug this shape exists to prevent:
    it does not raise, it just puts every trade on the wrong side.
    """

    id: str
    question: str
    slug: str
    condition_id: str
    outcomes: Tuple[Outcome, ...]
    active: bool = True
    closed: bool = False
    end_date: Optional[str] = None
    volume: Optional[float] = None
    liquidity: Optional[float] = None
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    def outcome(self, name: str) -> Optional[Outcome]:
        """Case-insensitive outcome lookup ("UP", "up", "Up" all work)."""
        target = name.strip().lower()
        for o in self.outcomes:
            if o.name.strip().lower() == target:
                return o
        return None

    def token_id(self, name: str) -> Optional[str]:
        o = self.outcome(name)
        return o.token_id if o else None

    @property
    def is_binary(self) -> bool:
        return len(self.outcomes) == 2

    @property
    def resolved_outcome(self) -> Optional[str]:
        """Name of the winning outcome, or None if not resolved yet.

        Deliberately strict: only exactly 1.0 counts as resolved. A market
        trading at 0.99 has NOT resolved, and treating it as resolved is how a
        backtest quietly books wins that never happened. Convention 11 - an
        unresolved market is not a market that resolved against us.
        """
        if not self.is_binary:
            return None
        for o in self.outcomes:
            if o.is_resolved_winner:
                return o.name
        return None


@dataclass(frozen=True)
class PriceLevel:
    """One level of the CLOB book. `size` is in shares."""

    price: float
    size: float

    @property
    def notional(self) -> float:
        return self.price * self.size


@dataclass(frozen=True)
class Orderbook:
    """A CLOB book snapshot for one token.

    Bids are stored best-first (descending price), asks best-first (ascending
    price). The API's own ordering is NOT relied on anywhere - `from_api` sorts
    explicitly. Polymarket has historically returned bids ascending, which is
    why moondevonyt's code reads `bids[-1]`; sorting ourselves means a change on
    their side cannot silently invert our best bid.

    `drops` records what `parse_levels_counted` threw away building this book.
    An empty dict means a clean payload; a non-empty one means the venue sent
    rows we refused, and the reason is named. Convention 20.
    """

    token_id: str
    bids: Tuple[PriceLevel, ...]
    asks: Tuple[PriceLevel, ...]
    timestamp: Optional[int] = None
    tick_size: float = PRICE_TICK
    min_order_size: float = MIN_SHARES
    drops: Dict[str, int] = field(default_factory=dict, compare=False)

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def midpoint(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def is_one_sided(self) -> bool:
        """One side quoting and the other empty. Tradable one way only.

        Not the same as an empty book, and not the same as a healthy one. A
        caller sizing a BUY against a bids-only book gets zero fill, which is
        correct but reads as "no liquidity" unless it knows why.
        """
        return bool(self.bids) != bool(self.asks)

    def ask_depth(self, limit_price: float) -> float:
        """Total shares offered at or below `limit_price`."""
        return sum(lvl.size for lvl in self.asks if lvl.price <= limit_price + 1e-12)

    def bid_depth(self, limit_price: float) -> float:
        """Total shares bid at or above `limit_price`."""
        return sum(lvl.size for lvl in self.bids if lvl.price >= limit_price - 1e-12)

    def is_crossed(self) -> bool:
        """A book whose best bid exceeds its best ask is corrupt, not an arb."""
        if self.best_bid is None or self.best_ask is None:
            return False
        return self.best_bid > self.best_ask


@dataclass(frozen=True)
class WalkResult:
    """Outcome of walking the book for a taker order.

    `filled_shares` may be less than requested (the book ran out, or every
    remaining level was above the limit). `levels_consumed` is kept so the
    decision log can show WHY the average entry was worse than the best ask,
    which is the whole point of walking rather than assuming top-of-book.
    """

    requested_shares: float
    filled_shares: float
    avg_price: Optional[float]
    cost_usdc: float
    levels_consumed: Tuple[Tuple[float, float], ...]  # (price, shares) pairs
    limit_price: float
    exhausted_book: bool
    slippage_vs_top: Optional[float]  # avg_price - best_ask, in dollars/share

    @property
    def fully_filled(self) -> bool:
        return self.filled_shares >= self.requested_shares - 1e-9

    @property
    def partial(self) -> bool:
        return 0 < self.filled_shares < self.requested_shares - 1e-9

    @property
    def unfilled(self) -> bool:
        return self.filled_shares <= 1e-9

    def to_dict(self) -> dict:
        return {
            'requested_shares': self.requested_shares,
            'filled_shares': self.filled_shares,
            'avg_price': self.avg_price,
            'cost_usdc': round(self.cost_usdc, 6),
            'limit_price': self.limit_price,
            'levels_consumed': [{'price': p, 'shares': s}
                                for p, s in self.levels_consumed],
            'exhausted_book': self.exhausted_book,
            'slippage_vs_top': (None if self.slippage_vs_top is None
                                else round(self.slippage_vs_top, 6)),
        }


@dataclass(frozen=True)
class Fill:
    """A simulated (paper) or real taker fill on one token."""

    market_slug: str
    token_id: str
    outcome: str
    side: str  # 'BUY' or 'SELL'
    shares: float
    avg_price: float
    cost_usdc: float
    fee_usdc: float
    timestamp: int
    walk: Optional[WalkResult] = None

    @property
    def max_loss_usdc(self) -> float:
        """A long binary can lose exactly what it cost. That IS the stop."""
        return self.cost_usdc + self.fee_usdc

    @property
    def max_gain_usdc(self) -> float:
        return self.shares * WINNING_REDEMPTION - self.cost_usdc - self.fee_usdc

    def to_dict(self) -> dict:
        d = {
            'market_slug': self.market_slug,
            'token_id': self.token_id,
            'outcome': self.outcome,
            'side': self.side,
            'shares': self.shares,
            'avg_price': self.avg_price,
            'cost_usdc': round(self.cost_usdc, 6),
            'fee_usdc': round(self.fee_usdc, 6),
            'timestamp': self.timestamp,
        }
        if self.walk is not None:
            d['walk'] = self.walk.to_dict()
        return d


@dataclass(frozen=True)
class Trade:
    """A public trade print from the Data API."""

    condition_id: str
    slug: Optional[str]
    outcome: Optional[str]
    side: str
    size: float
    price: float
    timestamp: int
    transaction_hash: Optional[str] = None


# Reasons a raw book row can be refused. Named so a caller can report WHICH,
# not just how many - two different silent drops reported as one number is the
# exact failure convention 20 was written for.
LEVEL_DROP_REASONS = ('malformed', 'non_finite', 'zero_size', 'price_out_of_range')


def parse_levels_counted(rows: Sequence[dict], descending: bool
                         ) -> Tuple[Tuple[PriceLevel, ...], Dict[str, int]]:
    """Build a sorted, best-first level tuple AND say what was dropped.

    Returns `(levels, drops)` where `drops` only carries non-zero reasons, so a
    clean payload yields `{}`. The accounting identity
    `len(rows) - sum(drops.values()) == len(levels)` holds by construction and
    is asserted in the tests.

    Why each drop exists:
      - `malformed`: the row had no usable price/size at all.
      - `non_finite`: `float("NaN")` and `float("Infinity")` both parse. A NaN
        price silently poisons every average downstream and serialises to an
        unparseable JSON token (convention 19).
      - `zero_size`: the API occasionally returns these; they make `best_ask` a
        price nobody will actually sell you.
      - `price_out_of_range`: a probability outside [0, 1] is a corrupt field.
        Left in, it would let `walk_book` book a fill above the $1 redemption
        value and report a guaranteed profit.
    """
    levels: List[PriceLevel] = []
    drops: Dict[str, int] = {r: 0 for r in LEVEL_DROP_REASONS}

    for r in rows or ():
        try:
            raw_price = r['price']
            raw_size = r['size']
        except (KeyError, TypeError):
            drops['malformed'] += 1
            continue

        price = safe_float(raw_price)
        size = safe_float(raw_size)
        if price is None or size is None:
            # Tell "not a number at all" apart from "a number Python accepts
            # but arithmetic must not" - they have different root causes.
            try:
                float(raw_price)
                float(raw_size)
            except (TypeError, ValueError):
                drops['malformed'] += 1
            else:
                drops['non_finite'] += 1
            continue

        if size <= 0:
            drops['zero_size'] += 1
            continue
        if not (MIN_PRICE <= price <= MAX_PRICE):
            drops['price_out_of_range'] += 1
            continue

        levels.append(PriceLevel(price=price, size=size))

    levels.sort(key=lambda lvl: lvl.price, reverse=descending)
    return tuple(levels), {k: v for k, v in drops.items() if v}


def parse_levels(rows: Sequence[dict], descending: bool) -> Tuple[PriceLevel, ...]:
    """Levels only. Prefer `parse_levels_counted` - it says what it threw away."""
    levels, _drops = parse_levels_counted(rows, descending)
    return levels
