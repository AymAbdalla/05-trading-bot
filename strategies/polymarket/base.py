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


# -- market types, and why routing is a DECLARATION rather than a guess -------
#
# Until D-312 there was exactly one market universe a strategy could be handed:
# the crypto Up/Down 5m window. `MarketContext` therefore described a crypto
# window and nothing else, and every strategy could assume `spot`, `strike`,
# `windows` and a 300-second clock were meaningful. Weather markets broke that
# assumption first - `build_weather_context` hands over a context with no spot,
# no strike, no candles and a `window_ts` that is a POLL SECOND rather than a
# window open - and the loop coped by keeping a separate `weather_strategies`
# list so a crypto strategy never saw a weather context.
#
# That worked for one extra universe. It does not scale to five. Event, sports
# and political markets are each a fourth, fifth and sixth list, and the failure
# it invites is silent: hand `fair_value_arb` a sports market and it does not
# crash, it computes a BTC-move probability against a `spot` of None and skips
# forever under a fair-value reason, which reads in the skip table as "the model
# declined" rather than "the model was never applicable". Convention 11 - a
# strategy that could not run is NOT the same fact as one that ran and refused,
# and one counter must never hold both.
#
# So the market type is carried ON THE CONTEXT and the support set is DECLARED
# ON THE STRATEGY, and the loop routes on the pair. A strategy that is handed a
# type it did not declare is a WIRING BUG, not a skip: `assert_supports` raises
# rather than returning a reason, because a reason string would put the bug in
# the data instead of in the stack trace (convention 22 - a claim in a docstring
# is not a wiring test).

#: Crypto Up/Down binaries, 5m and 15m, on the SHADOW_ASSETS. The original and
#: the only universe with a spot price, a strike and a fixed 300-second clock.
MARKET_TYPE_CRYPTO_UPDOWN = 'crypto_updown'

#: Temperature markets. No spot, no strike, no candles; the strategy fetches its
#: own METAR and forecast inputs. `window_ts` is the poll second.
MARKET_TYPE_WEATHER = 'weather'

#: High-volume general event markets discovered by dollar volume off Gamma.
MARKET_TYPE_EVENT = 'event'

#: Sports and esports markets. Structurally an event market; kept as its own
#: type because the discovery query, the volume distribution and the resolution
#: clock all differ, and pooling their results under one label would average two
#: populations into one that describes neither.
MARKET_TYPE_SPORTS = 'sports'

#: Elections, Fed decisions, policy. Same argument as sports for keeping it
#: separate from `event`.
MARKET_TYPE_POLITICAL = 'political'

#: Markets reached by following a tracked wallet's fills rather than by polling
#: a universe. The market could be ANY of the above; what makes it its own type
#: is the DISCOVERY path, and therefore the sample. A win rate measured on
#: markets a smart-money wallet chose is not a win rate on markets we chose.
MARKET_TYPE_SMART_MONEY = 'smart_money'

#: Every type the router knows. A context carrying anything else is rejected at
#: construction rather than routed to nobody, because a typo in a type string
#: would otherwise present as "no strategy supports this market".
MARKET_TYPES = (
    MARKET_TYPE_CRYPTO_UPDOWN,
    MARKET_TYPE_WEATHER,
    MARKET_TYPE_EVENT,
    MARKET_TYPE_SPORTS,
    MARKET_TYPE_POLITICAL,
    MARKET_TYPE_SMART_MONEY,
)

#: The types that are plain binary markets with a book, a resolution and no
#: crypto window behind them. Named so a strategy can declare "any liquid
#: binary" in one place instead of listing three constants that a fourth type
#: would then silently not join (convention 23).
GENERAL_BINARY_MARKET_TYPES = (
    MARKET_TYPE_EVENT,
    MARKET_TYPE_SPORTS,
    MARKET_TYPE_POLITICAL,
)


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
    #: Which universe this market came from. Defaults to the crypto Up/Down
    #: window so every context built before D-312 keeps its exact meaning - a
    #: default of None or '' would make "nobody set it" and "it is a crypto
    #: window" the same value, which is the ambiguity this field exists to end.
    #: Validated in `__post_init__`: an unrecognised type is a wiring bug and
    #: raises, rather than routing to no strategy and reading as a quiet board.
    market_type: str = MARKET_TYPE_CRYPTO_UPDOWN
    # Second market, for cross-market strategies (corridor_collector).
    market_15m: object = None
    books_15m: Dict[str, object] = field(default_factory=dict)
    lead_bps: Optional[float] = None
    atr14: Optional[float] = None

    def __post_init__(self) -> None:
        if self.market_type not in MARKET_TYPES:
            raise ValueError(
                'unknown market_type {!r}; known types are {}'
                .format(self.market_type, ', '.join(MARKET_TYPES)))

    @property
    def is_crypto_window(self) -> bool:
        """True when `windows`, `spot`, `strike` and the 300s clock are real.

        Every other market type carries a `window_ts` that is a POLL SECOND and
        a `seconds_remaining` that is arithmetic on a 300-second constant which
        describes nothing. Read this before trusting either.
        """
        return self.market_type == MARKET_TYPE_CRYPTO_UPDOWN

    @property
    def seconds_remaining(self) -> Optional[float]:
        """Seconds left in a 5-minute crypto window.

        **None on every non-crypto market type, deliberately.** Before D-312
        this computed `300 - seconds_into_window` unconditionally, and
        `build_weather_context` passes `seconds_into_window` as a sub-second
        fraction, so a weather market reported roughly 300 seconds remaining -
        a number with no referent that nonetheless passed every "am I early
        enough in the window" gate in the package. A clock that does not exist
        must read as absent, not as comfortable (convention 11).
        """
        if self.seconds_into_window is None or not self.is_crypto_window:
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

    #: True when the strategy rests maker orders. Note this no longer means
    #: "cannot be filled": `PolymarketPaperAdapter.simulate_maker_buy` and the
    #: loop's `observe_maker_orders` phase have simulated resting fills since
    #: 2026-08-18. A maker strategy still returns QUOTE rather than ENTER,
    #: because resting an order is not a fill.
    uses_maker_orders = False

    #: The market universes this strategy is willing to be handed. The loop
    #: routes on it; `assert_supports` enforces it.
    #:
    #: The default is crypto-only and that is load-bearing. Every strategy
    #: written before D-312 assumed a spot, a strike and a 300-second clock,
    #: and inheriting "supports everything" would hand those assumptions a
    #: sports market and get a permanent, plausible-looking refusal instead of
    #: a loud one. Widening this is an opt-in decision per strategy, made by
    #: someone who has read what that strategy reads off the context.
    supported_market_types = (MARKET_TYPE_CRYPTO_UPDOWN,)

    #: Never False in this repo. See the module docstring.
    paper_mode = PAPER_MODE

    @classmethod
    def supports_market_type(cls, market_type: str) -> bool:
        """Does this strategy accept a context of `market_type`?"""
        return market_type in cls.supported_market_types

    def assert_supports(self, ctx: 'MarketContext') -> None:
        """RAISE if handed a market type this strategy did not declare.

        Deliberately an exception and not a `Decision(SKIP, ...)`. A strategy
        evaluating a universe it never opted into is a ROUTING bug in the loop,
        and the two ways to report it are not equivalent:

          * a skip reason puts the bug in `db/trading.db` as a row that looks
            like a decision, gets counted in the identity, and shows up in the
            skip table next to genuine gates - where it will eventually be read
            as evidence about the market rather than about our wiring;
          * an exception puts it in a stack trace, where the loop's per-strategy
            handler counts it under `strategy_exceptions` and `cycle_exception`,
            which is the bucket that already means "our code broke".

        Convention 22: the declaration above is a claim, and this is the test
        that makes the wiring honour it.
        """
        market_type = getattr(ctx, 'market_type', MARKET_TYPE_CRYPTO_UPDOWN)
        if not self.supports_market_type(market_type):
            raise ValueError(
                '{} was handed a {!r} market but declares support for {}; '
                'this is a loop routing bug, not a strategy decision'
                .format(self.strategy_name, market_type,
                        ', '.join(self.supported_market_types)))

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


# -- the discretionary stop, in ONE place ------------------------------------
#
# Every strategy in this package that closes a position BEFORE resolution used
# to carry its own `max_loss` constant, in absolute cents of a $1.00 contract
# and identical for every entry price:
#
#     fair_value_arb          0.03      fair_value_arb_wide      0.05
#     fair_value_arb_hft      0.02      fair_value_arb_patient   0.03
#     fair_value_arb_inverse  0.03      dip_arb                  0.05
#
# A fixed cent distance is a wildly different RISK depending on where the
# contract is priced, because the denominator is the premium and not the $1.00
# payout. Measured on `db/trading.db` over the 2026-08-18 shadow window, the
# three cheapest fills each family actually took:
#
#     PM_fair_value_arb       min entry 0.0500 -> a 3c stop is 60.0% of premium
#     PM_fair_value_arb_hft   min entry 0.0483 -> a 2c stop is 41.4% of premium
#     PM_dip_arb              min entry 0.0200 -> a 5c stop is below 0.00 and
#                                                 therefore does not exist
#
# and at the same time the AVERAGE fill (0.35 on the parent) only risked 8.5%.
# One constant was doing two incompatible jobs.
#
# The rule below states the stop as a distance keyed to the entry price tier.
# It lives here and nowhere else (convention 23): six sites with the same
# arithmetic is six places for it to drift.

#: `(entry price strictly below this, stop DISTANCE)`, scanned in order.
#: Distances are absolute contract price, i.e. a fraction of the $1.00 payout,
#: NOT a fraction of the entry premium.
#:
#:     entry < 0.10           ->  0.05
#:     0.10 <= entry < 0.50   ->  0.08
#:     entry >= 0.50          ->  0.10
#:
#: EXPIRY (convention 17): these three numbers are a specification handed down,
#: not a measurement. Nothing has been scored at them. The measurement that
#: would move them is the realised loss distribution on `sell:price_stop` closes
#: at each tier, which `stop_px` on the positions row now makes computable.
STOP_TIERS = ((0.10, 0.05), (0.50, 0.08), (float('inf'), 0.10))

#: Outcome labels a stop may be quoted for. Every position in this package is a
#: LONG of exactly one outcome token - there is no short leg anywhere - so the
#: stop is below the entry on every side and the label does not change the
#: arithmetic. It is validated rather than ignored because a caller passing a
#: label nobody recognises is a caller who may believe sides are handled
#: asymmetrically, and silently agreeing with them is how a stop ends up on the
#: wrong book.
STOP_SIDE_LABELS = ('up', 'down', 'yes', 'no')


def tiered_stop_distance(entry_px: float) -> float:
    """The NOMINAL tier distance for `entry_px`. Not clamped, not a price.

    This is the number the tier table says, before the 0.00 floor is applied.
    `tiered_stop_price` is what anything trading should read; this exists so a
    log row can say what the rule asked for next to what it could deliver.
    """
    entry = float(entry_px)
    if not (entry == entry) or entry in (float('inf'), float('-inf')):
        raise ValueError('entry_px must be finite, got {!r}'.format(entry_px))
    for upper, distance in STOP_TIERS:
        if entry < upper:
            return distance
    return STOP_TIERS[-1][1]


def tiered_stop_price(entry_px: float, side: Optional[str] = None) -> float:
    """The stop PRICE for a long of one outcome token bought at `entry_px`.

    `max(0.00, entry_px - tiered_stop_distance(entry_px))`.

    Convention 8 is enforced, not assumed:

      * the result is clamped at 0.00, because a losing binary share is worth
        exactly 0.00 and a negative stop is a price no book can print;
      * the result is asserted strictly below `entry_px`, which is what makes
        it a stop at all.

    **The degenerate case, and the choice made about it.** When `entry_px` is at
    or below its own tier distance - a 0.06 fill in the `<0.10` tier, or a 0.03
    fill anywhere - `entry - distance` lands at or below 0.00. There is no price
    strictly between 0.00 and the entry that the rule asks for, so the
    discretionary stop COLLAPSES ONTO THE STRUCTURAL ONE at 0.00 and the whole
    premium is at risk. That is reported (`stop_is_structural_floor`), never
    silently rewritten to some other distance: inventing a tighter stop here
    would be a number with no rule behind it, and inventing a wider one is
    impossible. Convention 20 - the two cases get two flags, not one counter.

    **Read this before quoting the tiers as a risk reduction.** The distances
    are a fraction of the $1.00 payout, so as a fraction of the PREMIUM they are
    largest exactly where the premium is smallest. At a 0.06 entry the `<0.10`
    tier asks for 0.05, which is 83% of the premium - worse than the 3c-on-6c
    case (50%) that motivated the change. The tiers cut risk on the mid and high
    buckets, where the overwhelming majority of this package's fills sit, and
    they do not fix the sub-10c bucket. `stop_loss_fraction_of_entry` is stamped
    on every row so this is measurable rather than argued about.

    `side` is accepted for interface symmetry and validated. It cannot change
    the answer: see `STOP_SIDE_LABELS`.
    """
    entry = float(entry_px)
    if side is not None and str(side).strip().lower() not in STOP_SIDE_LABELS:
        raise ValueError(
            'unknown outcome side {!r}; refusing to quote a stop for a side '
            'this package does not trade'.format(side))
    if not (entry > BINARY_STOP):
        # An entry at or below 0.00 has no price strictly below it. That is a
        # bookkeeping fault upstream, not a trade with a bad stop.
        raise ValueError(
            'entry_px must be strictly above {:.2f}, got {!r}'
            .format(BINARY_STOP, entry_px))
    if entry > BINARY_TARGET:
        raise ValueError(
            'entry_px must be at or below {:.2f}, got {!r}'
            .format(BINARY_TARGET, entry_px))
    stop = round(entry - tiered_stop_distance(entry), 10)
    if stop < BINARY_STOP:
        stop = BINARY_STOP
    assert stop < entry, (
        'stop {!r} is not strictly below entry {!r}'.format(stop, entry))
    return stop


def effective_stop_distance(entry_px: float,
                            side: Optional[str] = None) -> float:
    """`entry_px - tiered_stop_price(...)`: the loss the stop actually admits.

    Equal to `tiered_stop_distance` everywhere except the degenerate band, where
    it is the whole premium. This is the number an exit rule compares a bid
    against; the nominal one is for the log.
    """
    return round(float(entry_px) - tiered_stop_price(entry_px, side), 10)


def tiered_stop_features(entry_px: float,
                         side: Optional[str] = None) -> Dict[str, object]:
    """The stop, and everything needed to audit it, as decision features.

    One dict built in one place so the six strategies cannot stamp six
    different shapes of the same fact onto their rows.
    """
    entry = float(entry_px)
    stop = tiered_stop_price(entry, side)
    nominal = tiered_stop_distance(entry)
    effective = round(entry - stop, 10)
    return {
        'stop_price': round(stop, 6),
        'stop_distance': round(effective, 6),
        'stop_distance_nominal': nominal,
        'stop_is_tiered': True,
        # None for the unbounded top tier, never `inf`: these features are
        # serialised with `allow_nan=False` (convention 19) and an infinity
        # here would get the whole key stripped out of the row.
        'stop_tier_upper_bound': next(
            (u for u, _d in STOP_TIERS if entry < u and u != float('inf')),
            None),
        # The number the whole change is about: a stop is only "50% per tick"
        # or "8% per tick" relative to what was paid, and that ratio never
        # appeared on a row before.
        'stop_loss_fraction_of_entry': round(effective / entry, 6),
        # True means the discretionary stop does not exist for this fill and
        # the only stop is resolution at 0.00. Counted separately from a stop
        # that merely sits wide (convention 20).
        'stop_is_structural_floor': stop <= BINARY_STOP,
    }


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
