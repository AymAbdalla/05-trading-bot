"""Base types for Polymarket prediction-market strategies.

These implement the same `Strategy` interface as every crypto strategy, so the
scanner can call them, but read the warning below before running one through
the backtest harness.

## The Signal mapping, and why it is shaped this way

A Polymarket share pays exactly $1.00 or exactly $0.00. So:

    entry  = the per-share premium we are willing to pay (0.00 - 1.00)
    stop   = 0.00   -- a losing share is worth zero. That IS the floor, and it
                       satisfies convention 8 (stop strictly below entry).
    target = 1.00   -- resolution.

That is not a convenient fiction. It is the exact payoff. The `direction` field
carries 'bullish' for the Up/Yes side and 'bearish' for Down/No, because that is
the vocabulary the scanner speaks; the actual outcome name is in
`features['outcome_side']`.

`entry` is the PREMIUM, not the order's limit price - see `Leg.premium`. A
strategy that quotes a marketable limit at its cap and expects to be filled
several cents inside it must report the fill, or every entry in the graveyard
comes back stamped with the cap and the cost model is a constant.

## What these strategies are NOT (D-268)

Interface-compatible is not harness-runnable. The existing vectorized harness
scores a path: it walks bars, applies stops, computes profit factor and
R-multiple. A Polymarket position has no path - it has a premium and a coin
flip weighted by the market's own probability estimate. Running these through
the price-path harness would score a Polymarket contract's payoff against BTC's
price series, which is a different instrument, and the numbers would be
fabricated in exactly the way the pre-purge FUTURES rows were.

Until the resolution-PnL harness extension exists, every strategy here is
NOT_TESTED. Not tested-and-found-nothing. Convention 11.

## Signal source: oracle vs price

Window direction can come from two places. The Gamma oracle (`outcomePrices`
exactly 1/0) is ground truth and is what the market actually settled on. BTC
price closes are an approximation - they usually agree, but they disagree at
the boundary, which is precisely where a fade strategy lives. Every `Window`
records which source it came from, and `Decision.features` reports how many of
each went into the signal, so a result computed off approximated directions can
never be mistaken for one computed off the oracle.

## PAPER_MODE

Every strategy module in this package sets `PAPER_MODE = True` and every class
carries `paper_mode = True`. moondevonyt's originals ship with `PAPER_MODE =
False` ("LIVE FIRE"). Ours must not: nothing in this repo has live-trading
authority, and a strategy is not the place to discover that.
"""
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from strategies.base import Signal, Strategy

# A binary's payoff endpoints. Repeated from engine.polymarket.types so a
# strategy file never needs to import the engine to state its own contract.
BINARY_STOP = 0.00
BINARY_TARGET = 1.00

WINDOW_SECONDS = 300

#: Package-wide default. Individual modules restate it so a reader grepping for
#: PAPER_MODE finds it in the file they are looking at, the way the originals do.
PAPER_MODE = True


@dataclass(frozen=True)
class Window:
    """One completed 5-minute BTC window."""

    ts: int
    open: float
    close: float
    direction: str          # 'UP' | 'DOWN'
    source: str = 'price'   # 'oracle' | 'price'

    @property
    def move_usd(self) -> float:
        return self.close - self.open


@dataclass
class MarketContext:
    """Everything a Polymarket strategy needs to decide one window.

    Deliberately a plain data bag rather than a live client handle: a strategy
    that can fetch its own data can also fetch data mid-decision, and then the
    decision is no longer reproducible from a logged context.

    UNITS, because two of these are easy to get wrong and neither fails loudly:
      `lead_bps` - signed, basis points. Positive means the Up side leads.
      `atr14`    - MUST also be in basis points, because `corridor_collector`
                   divides one by the other. moondevonyt's original does that
                   division in USD/USD; we do it in bps/bps. Feeding a USD ATR
                   here produces a ratio roughly 10,000x too small and the
                   lead/ATR gate silently never passes.
    """

    window_ts: int
    windows: List[Window] = field(default_factory=list)  # completed, oldest first
    market: object = None                    # engine.polymarket.types.Market
    books: Dict[str, object] = field(default_factory=dict)  # token_id -> Orderbook
    spot: Optional[float] = None             # live BTC price
    strike: Optional[float] = None           # this window's price-to-beat
    seconds_into_window: Optional[float] = None
    # Second market, for cross-market strategies (corridor_collector).
    market_15m: object = None
    books_15m: Dict[str, object] = field(default_factory=dict)
    lead_bps: Optional[float] = None
    atr14: Optional[float] = None

    @property
    def seconds_remaining(self) -> Optional[float]:
        if self.seconds_into_window is None:
            return None
        return WINDOW_SECONDS - self.seconds_into_window

    def book(self, side: str):
        """Orderbook for an outcome name on the 5m market, or None."""
        if self.market is None:
            return None
        token = self.market.token_id(side)
        return self.books.get(token) if token else None

    def book_15m(self, side: str):
        if self.market_15m is None:
            return None
        token = self.market_15m.token_id(side)
        return self.books_15m.get(token) if token else None


@dataclass(frozen=True)
class Leg:
    """One side of a (possibly multi-leg) Polymarket position."""

    outcome_side: str        # 'Up' | 'Down' | 'Yes' | 'No'
    limit_price: float
    order_type: str = 'taker'  # 'taker' (marketable) | 'maker' (resting bid)
    market_slug: Optional[str] = None   # None = this window's 5m market
    shares: Optional[float] = None
    expected_price: Optional[float] = None

    @property
    def premium(self) -> float:
        """Per-share cost we expect to pay. This is D-268's `entry`.

        `limit_price` is the ORDER's limit - the worst price we will accept.
        `expected_price` is what walking the book says we actually pay. They
        are different numbers, and reporting the limit as the entry is how a
        binary backtest books a 47c fill as a 55c one and then wonders why
        every row has an identical cost basis.
        """
        return (self.limit_price if self.expected_price is None
                else self.expected_price)

    def to_dict(self) -> dict:
        return {
            'outcome_side': self.outcome_side,
            'limit_price': self.limit_price,
            'expected_price': self.expected_price,
            'premium': self.premium,
            'order_type': self.order_type,
            'market_slug': self.market_slug,
            'shares': self.shares,
        }


@dataclass
class Decision:
    """The result of evaluating one window. ALWAYS produced, never skipped.

    A window that produces no Decision is a window nobody can audit. Every exit
    path in every strategy here returns one, with a `reason` naming the gate
    that stopped it (convention 20: a silent skip is a missing number).
    """

    action: str                      # 'ENTER' | 'QUOTE' | 'SKIP'
    reason: str = ''
    strategy: str = ''
    window_ts: Optional[int] = None
    market_slug: Optional[str] = None
    legs: List[Leg] = field(default_factory=list)
    features: dict = field(default_factory=dict)

    @property
    def is_entry(self) -> bool:
        return self.action == 'ENTER'

    @property
    def primary_leg(self) -> Optional[Leg]:
        return self.legs[0] if self.legs else None

    def to_dict(self) -> dict:
        return {
            'action': self.action,
            'reason': self.reason,
            'strategy': self.strategy,
            'window_ts': self.window_ts,
            'market_slug': self.market_slug,
            'legs': [lg.to_dict() for lg in self.legs],
            'features': self.features,
        }


class PolymarketStrategy(Strategy):
    """Base class for prediction-market strategies.

    Subclasses implement `evaluate(ctx) -> Decision`. `scan(candles)` is a thin
    adapter that builds a context from BTC candles alone and forwards to it, so
    the scanner's existing contract keeps working.
    """

    #: Set by subclasses. Used for logging and graveyard keys.
    strategy_name = 'polymarket_base'

    #: True when the strategy rests maker orders. The paper adapter simulates
    #: TAKER fills only, so a maker strategy's fills cannot be simulated
    #: honestly yet and its decisions come back as QUOTE, never ENTER.
    uses_maker_orders = False

    #: Never False in this repo. See the module docstring.
    paper_mode = PAPER_MODE

    @property
    def name(self) -> str:
        return self.strategy_name

    @property
    def is_entry(self) -> bool:
        return True

    @abstractmethod
    def evaluate(self, ctx: MarketContext) -> Decision:
        """Decide one window. Must return a Decision on every path."""

    # -- Strategy interface -------------------------------------------------

    def scan(self, candles: Dict[str, List[float]]) -> Optional[Signal]:
        """Adapter onto the standard scanner contract.

        `candles` must be BTC 5-MINUTE bars. Directions are derived from the
        closes and tagged `source='price'`, not `'oracle'` - see the module
        docstring. With no orderbook in the context, every strategy here falls
        through to its book gate and returns SKIP, which is correct: you cannot
        know what a fill would cost without a book. To get entries, build a
        MarketContext with live books and call `evaluate` directly.
        """
        ctx = self.context_from_candles(candles)
        if ctx is None:
            return None
        decision = self.evaluate(ctx)
        return self.decision_to_signal(decision)

    def context_from_candles(self, candles: Dict[str, List[float]],
                             min_windows: int = 16) -> Optional[MarketContext]:
        """Build a book-less context from BTC 5m OHLCV."""
        closes = candles.get('closes') or []
        opens = candles.get('opens') or closes
        timestamps = candles.get('timestamps') or []
        if len(closes) < min_windows or len(timestamps) < min_windows:
            return None

        windows = []
        for i in range(len(closes)):
            o = float(opens[i])
            c = float(closes[i])
            windows.append(Window(
                ts=int(timestamps[i]) // 1000 if timestamps[i] > 1e11
                   else int(timestamps[i]),
                open=o, close=c,
                direction='UP' if c >= o else 'DOWN',
                source='price',
            ))
        return MarketContext(
            window_ts=windows[-1].ts + WINDOW_SECONDS,
            windows=windows,
            spot=float(closes[-1]),
            strike=float(closes[-1]),
        )

    def decision_to_signal(self, decision: Decision) -> Optional[Signal]:
        """Map an ENTER Decision onto a Signal. Anything else returns None.

        For multi-leg strategies the Signal describes the PRIMARY leg only and
        the full structure rides in `features['legs']`. A single Signal cannot
        express "buy both sides and hold the pair to resolution"; anything
        consuming these must read `legs` or it will size half a position.
        """
        if not decision.is_entry or not decision.legs:
            return None
        leg = decision.legs[0]
        premium = leg.premium
        if not (BINARY_STOP < premium <= BINARY_TARGET):
            # A zero-premium entry would violate convention 8 (stop strictly
            # below entry) and a premium above 1.00 cannot be a binary at all.
            # Neither is a signal; both are a bug upstream, and swallowing them
            # here would put an unpriceable row in the graveyard.
            return None
        features = dict(decision.features)
        features.update({
            'outcome_side': leg.outcome_side,
            'order_type': leg.order_type,
            'market_slug': decision.market_slug,
            'window_ts': decision.window_ts,
            'legs': [lg.to_dict() for lg in decision.legs],
            'multi_leg': len(decision.legs) > 1,
            'payoff': 'binary_resolution',
            'leg_limit_price': leg.limit_price,
            'paper_mode': self.paper_mode,
        })
        return Signal(
            pair=decision.market_slug or 'POLYMARKET',
            pattern=self.strategy_name,
            direction=('bullish' if leg.outcome_side.strip().lower()
                       in ('up', 'yes') else 'bearish'),
            confidence=float(decision.features.get('confidence', 0.5)),
            features=features,
            entry=premium,
            stop=BINARY_STOP,
            target=BINARY_TARGET,
            valid_for=1,
        )


# -- shared signal maths -----------------------------------------------------

def streak(windows: Sequence[Window]) -> tuple:
    """(length, direction) of the run of same-direction windows at the end."""
    if not windows:
        return 0, None
    direction = windows[-1].direction
    n = 0
    for w in reversed(windows):
        if w.direction != direction:
            break
        n += 1
    return n, direction


def window_atr(windows: Sequence[Window], lookback: int = 12) -> float:
    """Mean absolute 5-minute move over `lookback` windows, in USD.

    Not Wilder's ATR. These are 5-minute closes with no intrabar high/low, so a
    true-range calculation would be reading data that is not there. Naming it
    ATR because moondevonyt's rules are written in those terms; the arithmetic
    is a mean absolute move and the difference matters if you ever compare it
    to `indicators/atr.py`.

    Divides by the number of windows actually sampled. moondevonyt divides by
    the constant 12; identical whenever at least 12 windows exist, which the
    callers here enforce with a 16-window minimum, and better behaved when they
    do not.
    """
    sample = list(windows)[-lookback:]
    if not sample:
        return 0.0
    return sum(abs(w.move_usd) for w in sample) / len(sample)


def cumulative_move(windows: Sequence[Window], n: int = 4) -> float:
    """Signed sum of the last n window moves, in USD."""
    return sum(w.move_usd for w in list(windows)[-n:])


def opposite(side: str) -> str:
    """'Up' <-> 'Down', 'Yes' <-> 'No', preserving the caller's casing style."""
    lookup = {'up': 'Down', 'down': 'Up', 'yes': 'No', 'no': 'Yes'}
    return lookup.get(side.strip().lower(), side)


def source_counts(windows: Sequence[Window]) -> Dict[str, int]:
    """How many windows came from the oracle vs from price. Goes in features."""
    out: Dict[str, int] = {}
    for w in windows:
        out[w.source] = out.get(w.source, 0) + 1
    return out


def effective_ask_for(book, shares: float,
                      limit_price: float) -> Optional[float]:
    """Average price to buy `shares` at or below `limit_price`, or None.

    Thin wrapper over `engine.polymarket.orderbook.walk_book` so the four
    strategies share ONE fill simulation instead of three approximations of
    one. Imported lazily: the engine package pulls in `requests`, and a
    strategy module should stay importable without a network stack.

    Returns None when the book cannot fill the full size under the limit. A
    partial fill is not an entry - sizing half a position and calling it the
    strategy is the mistake convention 12 exists to stop.
    """
    if book is None:
        return None
    from engine.polymarket.orderbook import walk_book
    walk = walk_book(book, shares, limit_price, side='BUY')
    if not walk.fully_filled:
        return None
    return walk.avg_price
