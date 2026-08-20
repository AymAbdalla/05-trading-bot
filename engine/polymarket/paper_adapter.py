"""Paper trading adapter for Polymarket binary markets.

Simulated TAKER and MAKER fills against the LIVE CLOB orderbook. No orders are
ever sent; no wallet is ever touched. Live execution needs EIP-712 signing and
is explicitly out of scope (D-267).

`PAPER_MODE` below is an unconditional `True`. There is no config key, no
environment variable and no constructor argument that can flip it, the
constructor refuses to build an adapter if it has been tampered with, and this
module imports no wallet, no signer and no order SDK. The only client it
touches (`PolymarketClient`) exposes no verb but GET.

Two things make this different from `engine/adapters/paper.py`:

  1. **Fills walk the book.** The crypto paper adapter fills at
     `ask * (1 + slippage)` because a Binance BTC book is deep enough that
     top-of-book plus a slippage constant is a fair model. A Polymarket 5-minute
     book is not: the top level is routinely 5-20 shares. So we consume real
     levels and report the real average, per the Dan1ro0 article's point that
     tradable edge is `fair_value - expected_average_entry - costs - margin`,
     not `fair_value - best_ask`.

  2. **PnL is resolution-based, unless a strategy sells out first.** There is
     no stop and no path. A position held to the end is worth its premium until
     the oracle speaks, then exactly $1.00 or exactly $0.00 per share, and
     `max loss = what you paid` IS the stop. A position CLOSED early
     (`simulate_taker_sell`) realises `proceeds - cost` instead, where the
     proceeds come from walking the BID side for the full size.

## Two exit kinds, and why they must never be pooled

`resolve_positions` and `simulate_taker_sell` both close a position, and they
produce statistically different animals:

    exit_kind='resolution'   binary payoff, 1.00 or 0.00. Win rate has to beat
                             the entry premium for the strategy to make money.
    exit_kind='sell'         a few cents either way. Win rate has to beat the
                             profit/loss ratio of the exit rules instead.

Averaging a 99%-win-rate 1c scalp with a 52%-win-rate 50c binary produces a
number that describes neither. So `summary()` reports `by_exit_kind` alongside
the pooled totals, `share_weighted_entry_price` and `breakeven_win_rate` are
computed on RESOLUTION exits only (they are meaningless for a position that
never redeemed), and every close writes its exit kind into the CSV.

## The maker path, and the exact fill rule it uses

`box_builder` and `grid_hedge` are MAKER strategies. Both were blocked on the
same fact: this adapter only ever simulated marketable orders, so a resting bid
had no fill model at all and both files returned QUOTE with the reason
`maker_fill_not_simulated`. `simulate_maker_buy` and `simulate_maker_sell` are
that missing model.

A resting order is NOT a fill and does not become a position when it is rested.
It becomes a `RestingOrder`, and it only becomes a `PaperPosition` when a later
BOOK SNAPSHOT proves it would have been filled. That is why the maker verbs
return a `RestingOrder` and not a `PaperPosition`: the shapes differ because the
facts differ, and returning a position at rest time would be the fabrication
this whole path exists to avoid.

THE FILL RULE IS A STRICT CROSS, NOT A TOUCH, AND IT IS QUEUE AWARE.

For a resting BUY at limit L holding S shares:

  1. At rest time we measure `queue_ahead_shares` = every share bid at L OR
     BETTER in the book we rested into. Price priority puts the better bids
     ahead of us; time priority puts everyone already sitting at L ahead of us
     too, because we just joined the back of that queue.
  2. A later snapshot fills us only if offers appear at prices STRICTLY BELOW L.
     `through_shares` = the size offered under our own bid. An offer resting
     strictly under our bid is unambiguous evidence that sell flow came down
     through our price level: a real book cannot stay crossed, so anything
     resting below our bid means the bids at and above L were consumed.
  3. `fillable = through_shares - queue_ahead_shares`. We are filled for
     `min(S, fillable)` and NOT ONE SHARE MORE.
  4. Every filled share is priced at EXACTLY L. That is the entire economic
     point of resting: a maker pays no spread, the taker crosses to our price.

AND THE FILL IS ADVERSE, WHICH THE LOG SAYS OUT LOUD. Two different spread
numbers exist here and they must never be quoted as one. `spread_declined_usdc`
is the flattering one, measured at REST time: the offer we chose not to lift
minus our own limit. `slippage_vs_top` on the ENTER row is measured at FILL
time, and it is usually POSITIVE, because by the time a resting bid is crossed
the offer has come down THROUGH it and we own the shares above the current
market. That is adverse selection, it is the actual cost of being a maker, and a
model that reports only the first number has described the decision rather than
the trade.

A TOUCH IS NOT A FILL. `best_ask == L` is a locked market, not a trade through
our level, and we may be arbitrarily deep in that queue. It is recorded
(`touched`) and it is a terminal reason of its own (`maker_touched_not_crossed`)
so the optimistic model's fills are visible as the number they are, but it
never opens a position.

Why strict-cross and not "the ask touched our price": box_builder's own module
docstring already names the failure. moondevonyt's fable maker filled 57% at
T-240 while a v5 bot armed 35 times at 0.89 and got ZERO fills. A model that
cannot reproduce that gap is not a model. Touch-means-fill books all of the good
fills and none of the adverse selection, and it would not produce a slightly
optimistic backtest, it would produce the strategy's entire P&L out of nothing.
Strict-cross-minus-queue is the most conservative rule that is still reachable
from snapshot data alone: we have no trade prints, so an offer resting below our
own bid is the only evidence of aggression we can actually see.

`max_through_shares` is a MAXIMUM across snapshots, never a sum. Two snapshots
that both show 40 shares offered under our bid are far more likely to be the
same 40 shares seen twice than 80 shares of flow, and summing them would invent
depth exactly the way top-of-book fills invent price.

A resting order that never fills is a first-class outcome, not a silent drop.
Every one of them terminates in an EXPIRE or CANCEL row with its own reason, and
no two causes share a counter (convention 20):

    maker_never_observed          rested, then no book was ever handed back. A
                                  cannot-run, not a no-fill (convention 11).
    maker_never_touched           the book never even reached our price.
    maker_touched_not_crossed     it printed AT our price and never through it.
                                  This is the bucket a naive model steals from.
    maker_cross_below_min_shares  it crossed, nobody was ahead of us, and the
                                  size was still under the exchange minimum.
    maker_queue_ahead_not_cleared it crossed, but the queue in front of us ate
                                  all of it. A DIFFERENT fact from the line
                                  above and it has a different fix.
    maker_sell_partial_only       a resting SELL could have been part filled.
                                  See below for why that is refused.
    maker_cancelled_by_halt       it would have filled, but the kill switch was
                                  pulled while it rested. A resting BUY that
                                  fills is a NEW ENTRY, and the documented
                                  Polymarket halt contract blocks entries. A
                                  resting SELL is NOT cancelled by a halt, for
                                  the same reason `simulate_taker_sell` is not
                                  halt gated: a stop that stops working when
                                  the kill switch is pulled is not a stop.
    stop_not_below_entry          convention 8 refused the fill. Unreachable
                                  while `limit_price > 0` is enforced at rest
                                  time, and re-checked at fill time anyway
                                  (convention 23).
    position_not_open             a resting SELL outlived the position it was
                                  closing. Not an error and not a market fact.
    cancelled_by_strategy         the caller pulled it.

The buy and sell sides deliberately disagree about partial fills, and they
disagree the same way the taker sides already do. A partial maker BUY fills (as
long as it clears `min_shares`) and cancels its own remainder, because a
position whose cost basis moves across snapshots makes every per-share number
downstream ambiguous. A partial maker SELL is REFUSED and the order keeps
resting, for the reason `simulate_taker_sell` already gives: a strategy whose
thesis is "we exit before resolution" has to make the case where it CANNOT exit
loud and expensive rather than rounding it into a smaller position.

Convention 8 holds on the maker path exactly as it does on the taker path. A
losing binary share redeems at exactly 0.00, so `stop_price` is 0.00 and the
premium paid IS the stop. `limit_price` is checked to be strictly greater than
zero before anything rests, so the stop is strictly below the entry, and
`_stop_is_below_entry` re-checks it at fill time rather than trusting the
argument check to still hold (convention 23: a fix at one site is not a fix).

The kill switch is enforced HERE, in the adapter, and not in `risk_gate.py`.
The gate's contract is that it is a pure function of the portfolio state it is
handed; making it stat() a file would break that, and a gate that reads the
filesystem cannot be reasoned about from its arguments. The adapter is the only
place a Polymarket position can be opened, so it is the only place the switch
has to hold.

Note the asymmetry with the crypto executor: HALT there also FLATTENS. Here it
still blocks new ENTRIES ONLY, and `simulate_taker_sell` is deliberately NOT
halt-gated - closing risk during a halt is the point of a halt, and a stop loss
that stops working when the kill switch is pulled is not a stop loss.

BUT read what changed. Before this method existed the Polymarket path had no
sell of any kind, so "a halt cannot close a binary" was a STRUCTURAL FACT. It is
now a CHOICE: flattening would mean the halt reaching into open positions and
selling them, which is a policy decision about operator intent and Raven's call,
not the adapter's. Until that ruling `botctl status` and the shadow loop's halt
note both continue to say a halt blocks entries only, and both are still
accurate. Convention 22 - a docstring is not the wiring, so the wiring is
unchanged and this paragraph says so.

Every decision window is logged, entries AND skips. That is moondevonyt's
convention ("the logging IS the product") and it is also convention 20 here: a
silent skip is a missing number. A window with no row in the log is a window we
cannot audit, and a strategy whose skips are invisible cannot be distinguished
from one that never fired. Every exit path in `simulate_taker_buy` writes a row
before returning, including the ones reached by an exception - and including
the halt, so that a halted session is visibly halted in the log rather than
looking like a session where no strategy ever signalled.
"""
import csv
import json
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from engine.halt import is_halted
from engine.polymarket.client import PolymarketClient
from engine.polymarket.markets import get_market_by_slug
from engine.polymarket.orderbook import fetch_orderbook, walk_book
from engine.polymarket.prices import resolution_price
from engine.polymarket.types import (LOSING_REDEMPTION, MIN_SHARES, PRICE_TICK,
                                     WINNING_REDEMPTION, Fill, Orderbook)
from engine.risk import constraints as risk_constraints

logger = logging.getLogger(__name__)

# Unconditional. Not read from config, not overridable per instance, and
# checked in __init__ so that flipping it is a hard failure rather than a
# quietly live adapter.
PAPER_MODE = True

DEFAULT_LOG_DIR = os.path.join('research', 'polymarket_paper')

# Polymarket charges no explicit taker fee on the CLOB today. It is a config
# knob rather than a hardcoded 0.0 because "the fee is zero" is an assumption
# with an expiry date (convention 17), and a strategy whose edge is 2c per
# share dies the day that changes.
DEFAULT_TAKER_FEE_RATE = 0.0

# Same reasoning, other side of the book. Polymarket rebates nothing and charges
# nothing to a maker today, and "the maker fee is zero" is an assumption with an
# expiry date exactly as much as the taker one is (convention 17). Kept as a
# SEPARATE knob rather than reusing `taker_fee_rate`, because the day the venue
# introduces a fee schedule the two numbers will not be equal, and a maker
# strategy whose whole thesis is "we do not pay the spread" is precisely the one
# a maker fee kills.
DEFAULT_MAKER_FEE_RATE = 0.0

# How long a resting order lives before it expires unfilled, in seconds. One
# 5-minute window by default: a quote from the previous window is not a quote,
# and an order with no expiry at all would make "this never filled" unreachable,
# which would quietly delete the most important outcome the maker path has.
DEFAULT_MAKER_TTL_SECONDS = 300

# Per-trade premium at risk, as this adapter's own fill-size sanity check reads
# it. DELEGATED to engine.risk.constraints (D-343 R1 residual): the adapter used
# to carry a bare 10.0 inline in __init__ - a THIRD independent copy of a number
# engine.risk.constraints and engine.polymarket.risk_gate had already been made
# to agree on, and exactly the drift the D-343 R1 ruling exists to end ("three
# copies of a kill switch is three chances for one of them to point somewhere
# else"). The VALUE does not change ($10); what changes is that there is now one
# place it is defined. See risk_gate.DEFAULT_NOTIONAL_CAP_USDC, which sources
# the same field. Note this cap is a fill-size sanity check on an order the gate
# has already passed, not a second gate - it binds `shares * limit_price`, and
# `affordable_shares` sizes down to it.
DEFAULT_NOTIONAL_CAP_USDC = risk_constraints.DEFAULT_LIMITS.per_trade_notional_usd

# Names the exact fill rule below, so a log row, a strategy feature and a
# summary block all say the same thing and a later change to the rule is a
# visible string change rather than a silent one. Read the module docstring for
# what each word means: STRICT CROSS (a touch is not a fill), QUEUE AWARE (we
# join the back of the queue at our own price and have to wait it out).
MAKER_FILL_MODEL = 'strict_cross_queue_aware_v1'

# Resting order lifecycle. A resting order is in exactly one of these.
ORDER_RESTING = 'RESTING'
ORDER_FILLED = 'FILLED'
ORDER_EXPIRED = 'EXPIRED'
ORDER_CANCELLED = 'CANCELLED'

# Terminal reasons for a resting order that produced no position. Listed here
# rather than as inline string literals so a test can assert each one is
# REACHABLE instead of trusting a docstring (convention 22), and so it is
# visible at a glance that no two drop causes share a string (convention 20).
MAKER_NO_FILL_REASONS = (
    'maker_never_observed',
    'maker_never_touched',
    'maker_touched_not_crossed',
    'maker_cross_below_min_shares',
    'maker_queue_ahead_not_cleared',
    'maker_sell_partial_only',
    'maker_cancelled_by_halt',
    'stop_not_below_entry',
    'position_not_open',
    'cancelled_by_strategy',
)

# Floating point slack. Book prices arrive as parsed decimals, so 0.47 is not
# exactly 0.47 and a bare `<` on two nominally equal prices is a coin flip.
# Everything that has to distinguish a TOUCH from a CROSS goes through this.
PRICE_EPS = 1e-12
SIZE_EPS = 1e-9

# A share pays exactly $1.00 or exactly $0.00, so every price lives on [0, 1].
# A quote outside that interval is a corrupt or misparsed book, never an
# opportunity: paying more than $1.00 for a $1.00-max payoff is a guaranteed
# loss, and a negative price would book negative cost (free money).
MIN_PRICE = 0.0
MAX_PRICE = 1.0

LOG_COLUMNS = [
    'ts', 'iso', 'strategy', 'market_slug', 'window_ts', 'action', 'reason',
    'outcome_side', 'token_id', 'limit_price', 'requested_shares',
    'filled_shares', 'avg_price', 'best_ask', 'slippage_vs_top',
    'levels_consumed', 'exhausted_book', 'cost_usdc', 'fee_usdc',
    'max_loss_usdc', 'max_gain_usdc', 'position_id', 'resolution',
    'won', 'pnl_usdc', 'features',
]


@dataclass
class PaperPosition:
    """An open (or resolved) simulated Polymarket position."""

    position_id: str
    strategy: str
    market_slug: str
    token_id: str
    outcome_side: str
    shares: float
    avg_price: float
    cost_usdc: float
    fee_usdc: float
    opened_ts: int
    window_ts: Optional[int] = None
    resolution: Optional[str] = None      # 'WIN' | 'LOSS' | None (pending)
    pnl_usdc: Optional[float] = None
    features: dict = field(default_factory=dict)

    # -- how the position ended. `exit_kind` is None while it is open, then
    # 'resolution' (the oracle paid 1.00 or 0.00) or 'sell' (we hit the bid
    # before expiry). `resolution` stays a WIN/LOSS on both paths so existing
    # readers keep working, and WIN on a sell means realised PnL > 0 - a
    # break-even scratch is not a win.
    exit_kind: Optional[str] = None       # 'resolution' | 'sell' | None
    exit_price: Optional[float] = None    # walked average sell price
    exit_reason: Optional[str] = None     # the strategy's own exit rule name
    exit_ts: Optional[int] = None
    exit_fee_usdc: float = 0.0
    proceeds_usdc: Optional[float] = None

    # -- how the position was FILLED, as opposed to how it ended. 'taker' means
    # we crossed the spread and paid it; 'maker' means a resting order of ours
    # was crossed INTO and we were paid it. A maker strategy's entire claimed
    # edge is the difference between those two numbers, so pooling them would
    # erase the thing under test. Defaulted to 'taker' so every position built
    # by the pre-existing taker path is described exactly as it was before.
    entry_liquidity: str = 'taker'
    exit_liquidity: Optional[str] = None   # 'maker' | 'taker' | None (open)

    # Convention 8: every entry needs a stop STRICTLY below entry. On a binary
    # there is no path and no stop order - a losing share redeems at exactly
    # 0.00 and the premium paid IS the maximum loss. Recording it as a field
    # rather than leaving it implicit means the invariant can be asserted at
    # fill time instead of asserted in prose.
    stop_price: float = LOSING_REDEMPTION

    @property
    def is_open(self) -> bool:
        return self.resolution is None

    @property
    def closed_early(self) -> bool:
        """Sold before the oracle spoke. Never true for a resolved position."""
        return self.exit_kind == 'sell'

    @property
    def total_fee_usdc(self) -> float:
        """Entry fee plus exit fee. Both sides of a round trip pay."""
        return self.fee_usdc + self.exit_fee_usdc

    @property
    def max_loss_usdc(self) -> float:
        return self.cost_usdc + self.fee_usdc

    @property
    def max_gain_usdc(self) -> float:
        return self.shares * WINNING_REDEMPTION - self.cost_usdc - self.fee_usdc

    @property
    def breakeven_win_rate(self) -> float:
        """Win rate this entry needs just to break even.

        For a binary bought at p, that is p PLUS fees. Printing it next to a
        strategy's claimed win rate is the fastest way to see whether an entry
        band is viable: 60% at 55c clears, 60% at 65c does not.
        """
        return (self.cost_usdc + self.fee_usdc) / (self.shares * WINNING_REDEMPTION)


@dataclass
class RestingOrder:
    """A simulated limit order sitting on the book, waiting to be crossed into.

    This is deliberately NOT a position. It is a claim on a fill that has not
    happened, and most of them never will. `status` moves RESTING -> FILLED or
    RESTING -> EXPIRED / CANCELLED exactly once, and a terminated order is never
    re-observed.

    ## The three numbers the fill rule turns on

    `queue_ahead_shares`   measured ONCE, at rest time, from the book we rested
                           into. Every share already bid at our price or better
                           is in front of us. This is why a touch cannot fill
                           us: the flow that reaches our price level has to
                           clear those shares before it reaches ours.
    `max_through_shares`   the best evidence of aggression we ever saw, as a
                           MAXIMUM over snapshots and never a running sum. See
                           the module docstring: summing snapshots counts the
                           same resting size twice and invents depth.
    `touched`              did the book ever print exactly AT our price without
                           going through it. Never fills anything. It exists so
                           the fills an optimistic model would have booked are
                           countable rather than merely denied.
    """

    order_id: str
    strategy: str
    market_slug: str
    token_id: str
    outcome_side: str
    side: str                 # 'BUY' (resting bid) | 'SELL' (resting ask)
    limit_price: float
    shares: float
    placed_ts: int
    queue_ahead_shares: float
    expires_ts: Optional[int] = None
    window_ts: Optional[int] = None
    features: dict = field(default_factory=dict)

    # Top of book AT REST TIME. This is the only honest reference for "we did
    # not pay the spread": the ask we declined to lift when we chose to rest.
    # The ask at FILL time is a different and much less flattering number -
    # by the time a resting bid is crossed the offer has usually come down
    # THROUGH it, so the fill is above the current market. That is adverse
    # selection, it is real, and both numbers are kept so neither can be quoted
    # as the other.
    ask_at_rest: Optional[float] = None
    bid_at_rest: Optional[float] = None

    # For a SELL this is the position being closed and is set at rest time. For
    # a BUY it is the position the fill OPENED and stays None until then.
    position_id: Optional[str] = None
    # The strategy's own name for why it is resting this (an exit rule name on
    # a SELL, a rung label on a BUY). Carried through to the terminal row.
    intent: str = ''

    status: str = ORDER_RESTING
    observations: int = 0
    max_through_shares: float = 0.0
    touched: bool = False
    filled_shares: float = 0.0
    fill_price: Optional[float] = None
    fill_ts: Optional[int] = None
    fee_usdc: float = 0.0
    terminal_reason: Optional[str] = None
    terminal_ts: Optional[int] = None

    # Convention 8, carried on the ORDER and not only on the position it may
    # become. A resting bid that would open an entry with no stop below it is a
    # bad order before it is a bad position, and this is the field the fill path
    # checks before it opens anything.
    stop_price: float = LOSING_REDEMPTION

    @property
    def is_resting(self) -> bool:
        return self.status == ORDER_RESTING

    @property
    def unfilled_shares(self) -> float:
        """Shares that rested and never traded. The honest half of a partial."""
        return max(0.0, self.shares - self.filled_shares)

    @property
    def notional_usdc(self) -> float:
        """USDC this order commits if it fills in full. Not yet at risk."""
        return self.shares * self.limit_price

    @property
    def fillable_shares(self) -> float:
        """Shares the best snapshot so far would have given us. Never negative.

        `through - queue_ahead`, floored at zero. A negative value means the
        queue in front of us was larger than the flow that arrived, which is a
        no-fill and not a short.
        """
        return max(0.0, self.max_through_shares - self.queue_ahead_shares)

    @property
    def stop_is_below_entry(self) -> bool:
        """Convention 8, as a boolean rather than as a sentence in a docstring.

        STRICTLY below. A stop equal to the entry is not a stop, it is a
        rounding of the entry, and on a binary bought at 0.00 there would be no
        loss to stop out of in the first place.
        """
        return self.stop_price < self.limit_price - PRICE_EPS

    @property
    def spread_declined_usdc(self) -> Optional[float]:
        """Per share not paid by resting instead of lifting, AT REST TIME.

        This is the maker's claimed edge, and it is the only version of that
        number worth quoting: it compares our limit against the offer we chose
        not to take. None when the book had no offer to decline.

        It is NOT a realised profit. It is what the decision was worth if the
        fill is not adverse, and a maker fill usually IS adverse - see
        `ask_at_rest`. Quoting this as PnL is the same error as quoting a
        best-ask fill as an entry price.
        """
        if self.ask_at_rest is None:
            return None
        if self.side == 'BUY':
            return self.ask_at_rest - self.limit_price
        if self.bid_at_rest is None:
            return None
        return self.limit_price - self.bid_at_rest

    def to_dict(self) -> dict:
        return {
            'order_id': self.order_id,
            'strategy': self.strategy,
            'market_slug': self.market_slug,
            'token_id': self.token_id,
            'outcome_side': self.outcome_side,
            'side': self.side,
            'limit_price': self.limit_price,
            'shares': self.shares,
            'placed_ts': self.placed_ts,
            'expires_ts': self.expires_ts,
            'ask_at_rest': self.ask_at_rest,
            'bid_at_rest': self.bid_at_rest,
            'spread_declined_usdc': self.spread_declined_usdc,
            'window_ts': self.window_ts,
            'queue_ahead_shares': self.queue_ahead_shares,
            'max_through_shares': self.max_through_shares,
            'fillable_shares': self.fillable_shares,
            'touched': self.touched,
            'observations': self.observations,
            'status': self.status,
            'filled_shares': self.filled_shares,
            'unfilled_shares': self.unfilled_shares,
            'fill_price': self.fill_price,
            'fill_ts': self.fill_ts,
            'fee_usdc': self.fee_usdc,
            'position_id': self.position_id,
            'intent': self.intent,
            'terminal_reason': self.terminal_reason,
            'terminal_ts': self.terminal_ts,
            'stop_price': self.stop_price,
            'stop_is_below_entry': self.stop_is_below_entry,
            'fill_model': MAKER_FILL_MODEL,
        }

    def to_json(self) -> str:
        """Serialise. `allow_nan=False` is convention 19, and it is load bearing.

        Python's `json` happily emits bare `NaN` and `Infinity` tokens that are
        not JSON and that most non-Python parsers reject, so a single corrupt
        size would produce a file that only Python can read back. Raising here
        is the correct failure: a resting order carrying a NaN size never had a
        fill model applied to it in the first place.
        """
        return json.dumps(self.to_dict(), allow_nan=False, sort_keys=True)


class PolymarketPaperAdapter:
    """Simulated taker execution against live Polymarket CLOB books."""

    def __init__(self, client: Optional[PolymarketClient] = None,
                 config: Optional[dict] = None,
                 log_dir: str = DEFAULT_LOG_DIR):
        if PAPER_MODE is not True:
            raise RuntimeError(
                'PAPER_MODE is not True. This adapter has no live execution '
                'path; a falsy PAPER_MODE means the module was tampered with.')

        self.client = client or PolymarketClient()
        cfg = (config or {}).get('polymarket', {})

        self.starting_equity = float(cfg.get('starting_equity_usdc', 2000.0))
        self.taker_fee_rate = float(cfg.get('taker_fee_rate',
                                            DEFAULT_TAKER_FEE_RATE))
        self.maker_fee_rate = float(cfg.get('maker_fee_rate',
                                            DEFAULT_MAKER_FEE_RATE))
        self.maker_ttl_seconds = float(cfg.get('maker_ttl_seconds',
                                               DEFAULT_MAKER_TTL_SECONDS))
        self.notional_cap_usdc = float(cfg.get('notional_cap_usdc',
                                               DEFAULT_NOTIONAL_CAP_USDC))
        # D-360: count cap removed in shadow, capital is the only cap.
        self.max_concurrent_positions = int(cfg.get('max_concurrent_positions',
                                                    100_000))
        self.min_shares = int(cfg.get('min_shares', MIN_SHARES))
        self.price_tick = float(cfg.get('price_tick', PRICE_TICK))

        self.mode = 'paper'
        self.positions: Dict[str, PaperPosition] = {}
        self.log_dir = log_dir
        self.log_path = os.path.join(log_dir, 'polymarket_paper_log.csv')

        # Every window that reached the adapter, by disposition. A skip that is
        # not counted is a skip that did not happen, as far as any later
        # analysis can tell.
        self.decision_counts: Dict[str, int] = {}

        # Resting limit orders, by order_id. Terminated ones stay in here so a
        # session can report how many orders it rested and how few of them
        # filled, which for a maker strategy IS the result.
        self.resting_orders: Dict[str, RestingOrder] = {}

        # Non-terminal observation outcomes, kept in their OWN counter and
        # deliberately not in `decision_counts`. `decision_counts` holds an
        # accounting identity with the CSV - one row per count - and a resting
        # order is looked at on every cycle without anything happening to it.
        # Folding those looks into `decision_counts` would break that identity
        # and bury the terminal outcomes under thousands of no-ops.
        self.maker_counts: Dict[str, int] = {}

    # -- logging ------------------------------------------------------------

    def _log(self, strategy: str, market_slug: str, action: str,
             reason: str = '', **kw) -> None:
        """Append one decision row. Called for entries AND every skip."""
        key = f'{action}:{reason}' if reason else action
        self.decision_counts[key] = self.decision_counts.get(key, 0) + 1

        os.makedirs(self.log_dir, exist_ok=True)
        now = time.time()
        row = {c: '' for c in LOG_COLUMNS}
        row.update({
            'ts': int(now),
            'iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
            'strategy': strategy,
            'market_slug': market_slug,
            'action': action,
            'reason': reason,
        })
        for k, v in kw.items():
            if k in row:
                row[k] = v

        # An existing but EMPTY file still needs a header. Testing existence
        # alone leaves a zero-byte log (touched by a setup script, or left by a
        # run that died between open() and writeheader()) headerless forever,
        # and then every reader silently promotes the first decision row to the
        # column names - deleting one window from every downstream count.
        header_needed = (not os.path.exists(self.log_path)
                         or os.path.getsize(self.log_path) == 0)
        with open(self.log_path, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
            if header_needed:
                writer.writeheader()
            writer.writerow(row)

        level = logging.INFO if action == 'ENTER' else logging.DEBUG
        logger.log(level, 'PM PAPER %s %s %s %s', action, strategy,
                   market_slug, reason)

    def log_skip(self, strategy: str, market_slug: str, reason: str,
                 **kw) -> None:
        """Public hook so a strategy can record a skip it decided on its own.

        Without this the adapter only ever sees windows that got as far as
        wanting a fill, and the skip distribution in the log would be a
        survivorship-biased view of the strategy's actual behaviour.
        """
        self._log(strategy, market_slug, 'SKIP', reason, **kw)

    def _maker_count(self, key: str) -> None:
        """Count one non-terminal maker observation outcome.

        Separate from `decision_counts` on purpose - see `__init__`. Every
        observation of a resting order lands in exactly one of these keys, so
        "we looked at it 400 times and it never crossed" is a number rather
        than an absence (convention 20).
        """
        self.maker_counts[key] = self.maker_counts.get(key, 0) + 1

    # -- sizing -------------------------------------------------------------

    def shares_for(self, limit_price: float,
                   notional_usdc: Optional[float] = None) -> int:
        """Whole shares affordable at `limit_price` under the notional cap.

        Returns 0 when the cap cannot buy the exchange minimum. That is the
        Polymarket analogue of D-249's unsizable-futures case, and it must
        surface as NOT_TESTED / cannot-run, never as a loss (convention 11).

        `notional_usdc` sizes a single signal DOWN from the cap. Sizing above
        the cap is not honoured at fill time - see `simulate_taker_buy`, which
        enforces `notional_cap_usdc` regardless of how the size was derived.
        """
        notional = self.notional_cap_usdc if notional_usdc is None else notional_usdc
        if limit_price <= 0:
            return 0
        # +1e-9 before the floor: notional/price is not exact in binary
        # floating point, and a value that should be exactly 100.0 can arrive
        # as 99.999999999999986, costing a whole share.
        n = math.floor(notional / limit_price + 1e-9)
        return n if n >= self.min_shares else 0

    def round_to_tick(self, price: float, direction: str = 'down') -> float:
        """Snap a price to the tick grid. 'down' for a buy cap, 'up' for a sell.

        The epsilon is not cosmetic. `0.29 / 0.01` evaluates to
        28.999999999999996, so a plain floor moved a price that was ALREADY on
        the 1c grid down a full tick to 0.28 (and `0.07 / 0.01` ceil'd up to
        0.08). On a binary whose whole edge is 2-3c, one tick is a third to a
        half of it, and this lands on the entry cap - the number that decides
        whether a window trades at all.

        Output precision is derived from the tick rather than hardcoded to 2
        decimals, so a venue change to a 0.001 tick does not silently collapse
        every price back onto the 1c grid.
        """
        eps = 1e-9
        steps = price / self.price_tick
        steps = (math.floor(steps + eps) if direction == 'down'
                 else math.ceil(steps - eps))
        decimals = max(0, -math.floor(math.log10(self.price_tick)) )
        return round(steps * self.price_tick, decimals)

    # -- execution ----------------------------------------------------------

    def simulate_taker_buy(self, strategy: str, market_slug: str,
                           token_id: str, outcome_side: str,
                           limit_price: float, shares: float,
                           window_ts: Optional[int] = None,
                           features: Optional[dict] = None,
                           book: Optional[Orderbook] = None
                           ) -> Optional[PaperPosition]:
        """Simulate a marketable buy by walking the live book.

        Returns the opened PaperPosition, or None if nothing filled. Every exit
        path writes a log row first, so a None return always has a recorded
        reason.
        """
        features = features or {}
        feat_str = ';'.join(f'{k}={v}' for k, v in sorted(features.items()))
        # `or ''` would turn a legitimate window_ts of 0 into "no window".
        ts_cell = '' if window_ts is None else window_ts

        base = dict(window_ts=ts_cell, outcome_side=outcome_side,
                    token_id=token_id, limit_price=limit_price,
                    requested_shares=shares, features=feat_str)

        # First guard, ahead of every other check. A halt outranks position
        # limits, price bands and sizing: those all ask "should this trade
        # happen", and the halt has already answered no. Checking it first also
        # means a halted session costs zero orderbook reads.
        #
        # `is_halted()` is fail-safe by construction - an unreadable HALT file
        # still counts as halted - so an IO problem here blocks entries rather
        # than quietly permitting them.
        if is_halted():
            self._log(strategy, market_slug, 'SKIP', 'halted', **base)
            return None

        if self.committed_slots() >= self.max_concurrent_positions:
            self._log(strategy, market_slug, 'SKIP', 'max_concurrent_positions',
                      **base)
            return None

        if not (MIN_PRICE < limit_price <= MAX_PRICE):
            # Cannot-run, not a loss. A limit outside [0, 1] on a binary is a
            # caller bug or a corrupt feed, and filling it would book a
            # position whose max_gain_usdc is negative by construction.
            self._log(strategy, market_slug, 'SKIP', 'limit_price_out_of_range',
                      **base)
            return None

        if shares < self.min_shares:
            # Cannot run, did not lose. Same shape as unsizable_at_cap.
            self._log(strategy, market_slug, 'SKIP', 'unsizable_at_cap', **base)
            return None

        # A declared risk cap that is not enforced is an unbounded fabricated
        # -PnL surface: whatever edge per share the strategy claims gets
        # multiplied by a position the account could never have funded.
        if shares * limit_price > self.notional_cap_usdc + 1e-9:
            self._log(strategy, market_slug, 'SKIP', 'over_notional_cap', **base)
            return None

        if book is None:
            try:
                book = fetch_orderbook(self.client, token_id)
            except Exception as exc:
                # PolymarketClient already swallows requests errors and returns
                # None. Anything that still escapes would otherwise take the
                # whole decision window out of the log with it, which is the
                # exact silent-drop convention 20 forbids.
                logger.warning('PM PAPER orderbook read raised for %s: %s: %s',
                               token_id, type(exc).__name__, exc)
                self._log(strategy, market_slug, 'SKIP', 'orderbook_read_error',
                          **base)
                return None
        if book is None:
            self._log(strategy, market_slug, 'SKIP', 'no_orderbook', **base)
            return None

        if not book.asks:
            # Nobody is quoting. An empty book and a book that has bids but no
            # asks are the same fact for a BUY: there is nothing to lift at any
            # price. `book_above_limit` below is the OPPOSITE diagnosis - there
            # IS depth, our limit was simply too tight. Merging the two would
            # make the skip taxonomy useless, which is what convention 20
            # forbids: a skip that is counted but not categorised cannot tell
            # you whether to loosen the limit or drop the market entirely.
            self._log(strategy, market_slug, 'SKIP', 'no_liquidity', **base)
            return None

        walk = walk_book(book, shares, limit_price, side='BUY')

        common = dict(
            base, filled_shares=walk.filled_shares,
            best_ask=book.best_ask,
            slippage_vs_top=('' if walk.slippage_vs_top is None
                             else round(walk.slippage_vs_top, 4)),
            levels_consumed='|'.join(f'{p}@{s}' for p, s in walk.levels_consumed),
            exhausted_book=walk.exhausted_book,
        )

        if walk.unfilled:
            self._log(strategy, market_slug, 'NO_FILL', 'book_above_limit',
                      avg_price='', **common)
            return None

        bad_levels = [p for p, _ in walk.levels_consumed
                      if not (MIN_PRICE <= p <= MAX_PRICE)]
        if bad_levels:
            self._log(strategy, market_slug, 'SKIP', 'book_price_out_of_range',
                      avg_price=walk.avg_price, **common)
            return None

        if walk.partial and walk.filled_shares < self.min_shares:
            # Below the exchange minimum, so this order could not have existed.
            self._log(strategy, market_slug, 'NO_FILL',
                      'partial_below_min_shares', avg_price=walk.avg_price,
                      **common)
            return None

        fee = walk.cost_usdc * self.taker_fee_rate
        position = PaperPosition(
            position_id=str(uuid.uuid4()),
            strategy=strategy,
            market_slug=market_slug,
            token_id=str(token_id),
            outcome_side=outcome_side,
            shares=walk.filled_shares,
            avg_price=walk.avg_price,
            cost_usdc=walk.cost_usdc,
            fee_usdc=fee,
            opened_ts=int(time.time()),
            window_ts=window_ts,
            features=features,
        )
        self.positions[position.position_id] = position

        self._log(strategy, market_slug, 'ENTER',
                  'partial_fill' if walk.partial else '',
                  avg_price=round(walk.avg_price, 4),
                  cost_usdc=round(walk.cost_usdc, 4),
                  fee_usdc=round(fee, 6),
                  max_loss_usdc=round(position.max_loss_usdc, 4),
                  max_gain_usdc=round(position.max_gain_usdc, 4),
                  position_id=position.position_id,
                  resolution='PENDING', **common)
        return position

    def simulate_taker_sell(self, position_id: str,
                            limit_price: float = MIN_PRICE,
                            shares: Optional[float] = None,
                            book: Optional[Orderbook] = None,
                            reason: str = '',
                            features: Optional[dict] = None
                            ) -> Optional[PaperPosition]:
        """Close an open position by walking the BID side. Returns it, or None.

        This is the mirror of `simulate_taker_buy` and it exists for exactly one
        reason: `PM_fair_value_arb` claims its edge by selling a corrected
        mispricing before resolution. Without a real sell simulation that claim
        could not be tested at all, and simulating it at `best_bid` would
        overstate it in precisely the way walking the ask stops
        `simulate_taker_buy` from overstating entries. A 5-minute book's top bid
        is routinely 5-20 shares; a 20-share exit eats levels.

        `limit_price` is the LOWEST price we will accept per share. It defaults
        to 0.00, which accepts every bid - correct for a stop loss, because a
        stop that refuses a bad price is not a stop. A profit-taking caller
        should pass its target. `shares` defaults to the whole position.

        ## All-or-nothing, deliberately

        A partial fill is REFUSED and the position stays open. That is not
        conservatism, it is the honest failure mode: a strategy whose entire
        thesis is "we exit before resolution" has to make the case where it
        CANNOT exit loud and expensive rather than rounding it into a smaller
        position. An unsold position rides to resolution and its full binary
        PnL is charged to the strategy, the same treatment temporal_arbitrage
        gives an unpaired leg.

        ## NOT gated on the halt

        `simulate_taker_buy` refuses during a HALT; this does not. A halt says
        "stop taking risk", and closing a position reduces risk. Blocking exits
        during a halt would strand exactly the exposure the operator pulled the
        switch about. See the module docstring for what this does and does not
        change about the halt's documented contract.

        Every exit path logs a row before returning, so a None return always has
        a recorded reason (convention 20).
        """
        features = features or {}
        position = self.positions.get(position_id)

        if position is None:
            self._log('unknown', 'unknown', 'SKIP', 'unknown_position',
                      position_id=position_id)
            return None

        strategy = position.strategy
        slug = position.market_slug
        base = dict(window_ts='' if position.window_ts is None else position.window_ts,
                    outcome_side=position.outcome_side,
                    token_id=position.token_id,
                    limit_price=limit_price,
                    position_id=position.position_id)

        if not position.is_open:
            # Already settled. Selling it again would book the proceeds twice
            # and leave equity permanently wrong.
            self._log(strategy, slug, 'SKIP', 'position_not_open',
                      resolution=position.resolution, **base)
            return None

        requested = position.shares if shares is None else float(shares)
        base['requested_shares'] = requested

        if requested <= 0 or requested > position.shares + 1e-9:
            # Selling more than we hold is a short, which this venue path does
            # not have, and selling zero is a caller bug. Neither is a market
            # observation.
            self._log(strategy, slug, 'SKIP', 'invalid_sell_size', **base)
            return None

        if not (MIN_PRICE <= limit_price <= MAX_PRICE):
            self._log(strategy, slug, 'SKIP', 'limit_price_out_of_range', **base)
            return None

        if book is None:
            try:
                book = fetch_orderbook(self.client, position.token_id)
            except Exception as exc:
                logger.warning('PM PAPER sell orderbook read raised for %s: '
                               '%s: %s', position.token_id, type(exc).__name__,
                               exc)
                self._log(strategy, slug, 'SKIP', 'orderbook_read_error', **base)
                return None
        if book is None:
            self._log(strategy, slug, 'SKIP', 'no_orderbook', **base)
            return None

        if not book.bids:
            # Nobody is bidding. This position CANNOT be closed right now, which
            # is a different fact from "our limit was too high" below, and the
            # two need opposite responses: this one means the exit model has
            # failed and the position is heading for resolution.
            self._log(strategy, slug, 'SKIP', 'no_bid_liquidity', **base)
            return None

        walk = walk_book(book, requested, limit_price, side='SELL')

        common = dict(
            base, filled_shares=walk.filled_shares,
            slippage_vs_top=('' if walk.slippage_vs_top is None
                             else round(walk.slippage_vs_top, 4)),
            levels_consumed='|'.join('{}@{}'.format(p, s)
                                     for p, s in walk.levels_consumed),
            exhausted_book=walk.exhausted_book,
        )

        if walk.unfilled:
            self._log(strategy, slug, 'NO_FILL', 'bid_below_limit', **common)
            return None

        bad_levels = [p for p, _ in walk.levels_consumed
                      if not (MIN_PRICE <= p <= MAX_PRICE)]
        if bad_levels:
            self._log(strategy, slug, 'SKIP', 'book_price_out_of_range',
                      avg_price=walk.avg_price, **common)
            return None

        if not walk.fully_filled:
            # See the docstring. The position stays OPEN and is still exposed.
            self._log(strategy, slug, 'NO_FILL', 'partial_sell_refused',
                      avg_price=walk.avg_price, **common)
            return None

        # For a SELL walk, `cost_usdc` is the sum of price*size taken off the
        # bids - i.e. the PROCEEDS. Named `cost` on WalkResult because it is
        # side-agnostic there; renamed here so nothing downstream subtracts it.
        proceeds = walk.cost_usdc
        exit_fee = proceeds * self.taker_fee_rate
        pnl = proceeds - exit_fee - position.cost_usdc - position.fee_usdc

        position.proceeds_usdc = proceeds
        position.exit_fee_usdc = exit_fee
        position.exit_price = walk.avg_price
        position.exit_kind = 'sell'
        position.exit_reason = reason or 'unspecified'
        position.exit_ts = int(time.time())
        position.pnl_usdc = pnl
        # A scratch is not a win. `> 0` and not `>= 0`, so a zero-PnL round trip
        # lands in the same bucket as a small loss rather than inflating a win
        # rate that this strategy is judged on to two decimal places.
        position.resolution = 'WIN' if pnl > 0 else 'LOSS'

        feat_str = ';'.join('{}={}'.format(k, v)
                            for k, v in sorted(features.items()))
        hold_sec = (None if position.exit_ts is None
                    else position.exit_ts - position.opened_ts)
        detail = ('exit_kind=sell;entry_price={:.4f};exit_price={:.4f};'
                  'hold_seconds={};proceeds_usdc={:.4f}').format(
                      position.avg_price, walk.avg_price,
                      '' if hold_sec is None else hold_sec, proceeds)

        self._log(strategy, slug, 'CLOSE', position.exit_reason,
                  avg_price=round(walk.avg_price, 4),
                  cost_usdc=round(position.cost_usdc, 4),
                  fee_usdc=round(position.total_fee_usdc, 6),
                  max_loss_usdc=round(position.max_loss_usdc, 4),
                  resolution=position.resolution,
                  won=pnl > 0,
                  pnl_usdc=round(pnl, 4),
                  features=(detail + ';' + feat_str) if feat_str else detail,
                  **{k: v for k, v in common.items()
                     if k not in ('features', 'position_id')},
                  position_id=position.position_id)

        logger.info('PM PAPER CLOSE %s %s %s %.0f sh %.4f -> %.4f pnl=%.4f (%s)',
                    strategy, slug, position.outcome_side, walk.filled_shares,
                    position.avg_price, walk.avg_price, pnl,
                    position.exit_reason)
        return position

    # -- maker execution ----------------------------------------------------
    #
    # Read the module docstring before touching anything below it. The fill rule
    # is a STRICT CROSS minus the QUEUE AHEAD, and it is conservative on purpose:
    # every loosening of it manufactures fills for the two strategies whose
    # entire claimed edge is "our resting order got hit".

    def committed_slots(self) -> int:
        """Open positions PLUS resting buys, against `max_concurrent_positions`.

        A resting bid is a position the moment it is crossed into, and nothing
        asks our permission first. Counting only filled positions would let a
        strategy rest twenty orders under a cap of five and then discover the
        cap was decorative the one time the book moved.

        With no resting orders this is exactly `len(self.open_positions())`, so
        the taker path's behaviour is unchanged by construction.
        """
        return len(self.open_positions()) + len(self.resting_buy_orders())

    def open_resting_orders(self) -> List[RestingOrder]:
        """Orders still waiting. Terminated ones are kept but not returned."""
        return [o for o in self.resting_orders.values() if o.is_resting]

    def resting_buy_orders(self) -> List[RestingOrder]:
        return [o for o in self.open_resting_orders() if o.side == 'BUY']

    def capital_committed_to_resting_orders(self) -> float:
        """USDC a resting bid would spend if it filled in full.

        NOT included in `capital_at_risk`, and that is the honest split: an
        unfilled bid can lose nothing, so calling it risk would overstate the
        session's exposure. It is still money that cannot be spent twice, which
        is why it is reported at all.
        """
        return sum(o.notional_usdc for o in self.resting_buy_orders())

    def resting_orders_json(self) -> str:
        """Every resting order, serialised. Convention 19: `allow_nan=False`.

        A NaN or an infinity in here would serialise to a token that is not
        JSON and that most non-Python readers reject, so this raises rather than
        writing a file only Python can load back.
        """
        return json.dumps([o.to_dict() for o in self.resting_orders.values()],
                          allow_nan=False, sort_keys=True)

    @staticmethod
    def _queue_ahead_for(book: Orderbook, side: str,
                         limit_price: float) -> float:
        """Shares that sit in front of us the moment we join the book.

        BUY: everything bid at our price or BETTER, because price priority puts
        the better bids first and time priority puts everyone already sitting at
        our price ahead of us too - we just joined the back of that queue.
        SELL: the mirror, everything offered at our price or lower.

        This is measured ONCE, at rest time, and never refreshed. Refreshing it
        downwards on a later snapshot would quietly promote us up the queue
        every time somebody else cancelled, which is the optimistic direction.
        """
        if side == 'BUY':
            return book.bid_depth(limit_price)
        return book.ask_depth(limit_price)

    @staticmethod
    def _through_and_touch(book: Orderbook, side: str,
                           limit_price: float) -> tuple:
        """(shares that traded THROUGH our level, did the book merely TOUCH it).

        For a resting BUY at L, `through` is the size offered STRICTLY BELOW L.
        A real book cannot stay crossed, so size resting under our own bid is
        the only snapshot-visible evidence that sell flow came down through our
        price. `touched` is an offer sitting exactly AT L: a locked market, and
        not a fill, because we do not know how deep in that queue we are.

        The strictness is the whole model. Change `<` to `<=` here and both
        maker strategies become profitable on paper for no reason at all.
        """
        if side == 'BUY':
            levels = book.asks
            through = sum(lvl.size for lvl in levels
                          if lvl.price < limit_price - PRICE_EPS)
            at_level = any(abs(lvl.price - limit_price) <= PRICE_EPS
                           for lvl in levels)
        else:
            levels = book.bids
            through = sum(lvl.size for lvl in levels
                          if lvl.price > limit_price + PRICE_EPS)
            at_level = any(abs(lvl.price - limit_price) <= PRICE_EPS
                           for lvl in levels)
        # A cross implies a touch. Recording it that way keeps
        # `maker_never_touched` meaning what it says: the book never came near
        # us at all, which is a sizing/pricing problem rather than a queue one.
        return through, bool(at_level or through > SIZE_EPS)

    def simulate_maker_buy(self, strategy: str, market_slug: str,
                           token_id: str, outcome_side: str,
                           limit_price: float, shares: float,
                           window_ts: Optional[int] = None,
                           features: Optional[dict] = None,
                           book: Optional[Orderbook] = None,
                           ttl_seconds: Optional[float] = None,
                           intent: str = '') -> Optional[RestingOrder]:
        """Rest a limit BUY on the book. Returns the order, or None if refused.

        THIS DOES NOT FILL AND DOES NOT OPEN A POSITION. It returns a
        `RestingOrder`, and the caller has to hand later book snapshots to
        `observe_resting_orders` before anything can happen. That is the
        difference between a maker and a taker stated in the return type: a
        taker knows its fill at call time and a maker does not.

        Every gate here mirrors `simulate_taker_buy` in the same order and with
        the same reason strings, so a skip taxonomy comparison across the two
        paths compares like with like. The one extra gate is post-only.

        ## Post-only, and why a crossing bid is refused rather than filled

        A bid at or above the best ask is marketable. It is a taker order with a
        maker label on it, and Polymarket's post-only flag rejects exactly that.
        Filling it here would be the single most attractive bug available: the
        strategy would book a "maker" fill while actually paying the spread, and
        `box_builder` would report the harvest it exists to claim. His own logs
        recorded that failure once already, as 249 post-only rejects from
        chasing. So it is `maker_would_cross_book`, and it is a SKIP.

        Every exit path writes a log row before returning (convention 20).
        """
        features = features or {}
        feat_str = ';'.join(f'{k}={v}' for k, v in sorted(features.items()))
        ts_cell = '' if window_ts is None else window_ts

        base = dict(window_ts=ts_cell, outcome_side=outcome_side,
                    token_id=token_id, limit_price=limit_price,
                    requested_shares=shares, features=feat_str)

        # Same first guard as the taker path, for the same reason: a halt has
        # already answered "should this trade happen", and resting an order the
        # halt would refuse to fill is just a slower way of ignoring it.
        if is_halted():
            self._log(strategy, market_slug, 'SKIP', 'halted', **base)
            return None

        if self.committed_slots() >= self.max_concurrent_positions:
            self._log(strategy, market_slug, 'SKIP', 'max_concurrent_positions',
                      **base)
            return None

        if not (MIN_PRICE < limit_price <= MAX_PRICE):
            self._log(strategy, market_slug, 'SKIP', 'limit_price_out_of_range',
                      **base)
            return None

        if shares < self.min_shares:
            self._log(strategy, market_slug, 'SKIP', 'unsizable_at_cap', **base)
            return None

        if shares * limit_price > self.notional_cap_usdc + 1e-9:
            self._log(strategy, market_slug, 'SKIP', 'over_notional_cap', **base)
            return None

        if book is None:
            try:
                book = fetch_orderbook(self.client, token_id)
            except Exception as exc:
                logger.warning('PM PAPER maker orderbook read raised for %s: '
                               '%s: %s', token_id, type(exc).__name__, exc)
                self._log(strategy, market_slug, 'SKIP', 'orderbook_read_error',
                          **base)
                return None
        if book is None:
            # We need a book to measure the queue we are joining. Resting
            # without one would mean assuming `queue_ahead_shares = 0`, which is
            # the front of the queue, which is the optimistic assumption.
            self._log(strategy, market_slug, 'SKIP', 'no_orderbook', **base)
            return None

        best_ask = book.best_ask
        if best_ask is not None and limit_price >= best_ask - PRICE_EPS:
            self._log(strategy, market_slug, 'SKIP', 'maker_would_cross_book',
                      best_ask=best_ask, **base)
            return None

        queue_ahead = self._queue_ahead_for(book, 'BUY', limit_price)
        now = int(time.time())
        ttl = self.maker_ttl_seconds if ttl_seconds is None else float(ttl_seconds)
        order = RestingOrder(
            order_id=str(uuid.uuid4()),
            strategy=strategy,
            market_slug=market_slug,
            token_id=str(token_id),
            outcome_side=outcome_side,
            side='BUY',
            limit_price=float(limit_price),
            shares=float(shares),
            placed_ts=now,
            queue_ahead_shares=float(queue_ahead),
            expires_ts=(None if ttl is None or ttl <= 0 else now + int(ttl)),
            window_ts=window_ts,
            features=dict(features),
            ask_at_rest=best_ask,
            bid_at_rest=book.best_bid,
            intent=intent,
            # Convention 8. A losing binary share redeems at exactly 0.00 and
            # `limit_price > 0` was enforced above, so this is strictly below
            # the entry. `_fill_resting_buy` re-checks it rather than trusting
            # that (convention 23).
            stop_price=LOSING_REDEMPTION,
        )
        self.resting_orders[order.order_id] = order

        self._log(strategy, market_slug, 'REST', 'maker_buy_resting',
                  best_ask=best_ask, position_id=order.order_id,
                  resolution='RESTING',
                  **dict(base, features=self._maker_features(order, feat_str)))
        return order

    def simulate_maker_sell(self, position_id: str, limit_price: float,
                            shares: Optional[float] = None,
                            book: Optional[Orderbook] = None,
                            reason: str = '',
                            features: Optional[dict] = None,
                            ttl_seconds: Optional[float] = None
                            ) -> Optional[RestingOrder]:
        """Rest a limit SELL against an open position. Returns it, or None.

        The exit mirror of `simulate_maker_buy`, and the maker mirror of
        `simulate_taker_sell`. A profit-taking exit is the natural maker: the
        whole reason to rest an ask instead of hitting the bid is to be paid the
        spread rather than to pay it.

        NOT halt gated, exactly as `simulate_taker_sell` is not. A halt says
        stop taking risk, and an ask resting over an open position reduces it.
        The asymmetry with `simulate_maker_buy` is deliberate and is the same
        asymmetry the taker pair already has.

        `limit_price` here is the LOWEST price we will accept, and it is
        required rather than defaulted. `simulate_taker_sell` can default to
        0.00 because a stop that refuses a bad price is not a stop; a resting
        ask at 0.00 is not a stop at all, it is a gift, so there is no sensible
        default and the caller has to say.
        """
        features = features or {}
        position = self.positions.get(position_id)

        if position is None:
            self._log('unknown', 'unknown', 'SKIP', 'unknown_position',
                      position_id=position_id)
            return None

        strategy = position.strategy
        slug = position.market_slug
        feat_str = ';'.join(f'{k}={v}' for k, v in sorted(features.items()))
        base = dict(window_ts=('' if position.window_ts is None
                               else position.window_ts),
                    outcome_side=position.outcome_side,
                    token_id=position.token_id,
                    limit_price=limit_price,
                    position_id=position.position_id)

        if not position.is_open:
            self._log(strategy, slug, 'SKIP', 'position_not_open',
                      resolution=position.resolution, **base)
            return None

        # One resting exit per position. Two would let the same shares be sold
        # twice the moment both crossed, and the second sale would be a naked
        # short this venue path does not have.
        for existing in self.open_resting_orders():
            if existing.side == 'SELL' and existing.position_id == position_id:
                self._log(strategy, slug, 'SKIP', 'sell_already_resting',
                          **dict(base, requested_shares=(shares if shares
                                                         is not None
                                                         else position.shares)))
                return None

        requested = position.shares if shares is None else float(shares)
        base['requested_shares'] = requested

        if requested <= 0 or requested > position.shares + 1e-9:
            self._log(strategy, slug, 'SKIP', 'invalid_sell_size', **base)
            return None

        if not (MIN_PRICE <= limit_price <= MAX_PRICE):
            self._log(strategy, slug, 'SKIP', 'limit_price_out_of_range', **base)
            return None

        if book is None:
            try:
                book = fetch_orderbook(self.client, position.token_id)
            except Exception as exc:
                logger.warning('PM PAPER maker sell orderbook read raised for '
                               '%s: %s: %s', position.token_id,
                               type(exc).__name__, exc)
                self._log(strategy, slug, 'SKIP', 'orderbook_read_error', **base)
                return None
        if book is None:
            self._log(strategy, slug, 'SKIP', 'no_orderbook', **base)
            return None

        best_bid = book.best_bid
        if best_bid is not None and limit_price <= best_bid + PRICE_EPS:
            # Marketable. This is a taker sell wearing a maker label; route it
            # to `simulate_taker_sell` instead of quietly booking the spread we
            # would in fact have paid.
            self._log(strategy, slug, 'SKIP', 'maker_would_cross_book', **base)
            return None

        queue_ahead = self._queue_ahead_for(book, 'SELL', limit_price)
        now = int(time.time())
        ttl = self.maker_ttl_seconds if ttl_seconds is None else float(ttl_seconds)
        order = RestingOrder(
            order_id=str(uuid.uuid4()),
            strategy=strategy,
            market_slug=slug,
            token_id=position.token_id,
            outcome_side=position.outcome_side,
            side='SELL',
            limit_price=float(limit_price),
            shares=float(requested),
            placed_ts=now,
            queue_ahead_shares=float(queue_ahead),
            expires_ts=(None if ttl is None or ttl <= 0 else now + int(ttl)),
            window_ts=position.window_ts,
            features=dict(features),
            ask_at_rest=book.best_ask,
            bid_at_rest=best_bid,
            position_id=position.position_id,
            intent=reason or 'unspecified',
            # An exit does not have an entry stop of its own; the position it is
            # closing already carries one. Recorded as the position's so a row
            # never shows a blank where convention 8 expects a number.
            stop_price=position.stop_price,
        )
        self.resting_orders[order.order_id] = order

        self._log(strategy, slug, 'REST', 'maker_sell_resting',
                  resolution='RESTING',
                  **dict(base, features=self._maker_features(order, feat_str)))
        return order

    @staticmethod
    def _maker_features(order: RestingOrder, extra: str = '') -> str:
        """The maker-specific numbers, folded into the existing features cell.

        Deliberately NOT new CSV columns. `LOG_COLUMNS` is the header of a log
        file that is already on disk and already being read by
        `scripts/shadow_summary_lib.py`; adding a column would leave every
        historical row misaligned against the new header. The features cell is
        the extension point that already exists.
        """
        declined = order.spread_declined_usdc
        detail = ('order_kind=maker;maker_fill_model={};side={};'
                  'queue_ahead_shares={};max_through_shares={};touched={};'
                  'observations={};order_id={};stop_price={};'
                  'ask_at_rest={};spread_declined_usdc={}').format(
                      MAKER_FILL_MODEL, order.side,
                      round(order.queue_ahead_shares, 4),
                      round(order.max_through_shares, 4), order.touched,
                      order.observations, order.order_id, order.stop_price,
                      order.ask_at_rest,
                      '' if declined is None else round(declined, 4))
        return (detail + ';' + extra) if extra else detail

    def observe_resting_orders(self, books: Dict[str, Orderbook],
                               now_ts: Optional[int] = None
                               ) -> List[RestingOrder]:
        """Hand a fresh set of book snapshots to every resting order.

        `books` is keyed by token id, the same shape `fetch_orderbooks` returns
        and the same shape `MarketContext.books` carries, so the shadow loop can
        pass what it already has. A token with no book here is NOT an error and
        NOT a no-fill: the order simply was not observed this cycle, and
        `observations` does not advance. Convention 11 - could-not-look is not
        looked-and-found-nothing.

        Returns the orders that TERMINATED on this call (filled, expired or
        cancelled by the halt), so a caller can act on them without diffing
        state itself. An order that is still resting is not in the list.
        """
        now = int(time.time()) if now_ts is None else int(now_ts)
        terminated: List[RestingOrder] = []
        halted = is_halted()

        for order in list(self.open_resting_orders()):
            book = books.get(order.token_id)
            if book is not None:
                outcome = self._observe_one(order, book, now, halted)
                if outcome is not None:
                    terminated.append(outcome)
                    continue
            # Expiry is checked even when no book arrived. An order whose window
            # closed while the feed was down is expired, not immortal.
            if self._expire_if_due(order, now):
                terminated.append(order)
        return terminated

    def _observe_one(self, order: RestingOrder, book: Orderbook, now: int,
                     halted: bool) -> Optional[RestingOrder]:
        """One snapshot against one order. Returns it if it terminated."""
        order.observations += 1
        through, touched = self._through_and_touch(book, order.side,
                                                   order.limit_price)
        # MAXIMUM, never a running sum. See the module docstring: two snapshots
        # showing the same resting 40 shares are 40 shares, not 80.
        order.max_through_shares = max(order.max_through_shares, float(through))
        order.touched = order.touched or touched

        fillable = order.fillable_shares
        if fillable <= SIZE_EPS:
            self._maker_count('observed:' + ('touched_not_crossed' if touched
                                             else 'not_touched'))
            return None

        if order.side == 'BUY':
            if halted:
                # A resting bid that fills is a NEW ENTRY, and the Polymarket
                # halt contract blocks entries. Cancelling rather than deferring
                # so the order cannot silently fill the moment the halt lifts on
                # a book that has since moved.
                self._terminate(order, ORDER_CANCELLED, 'maker_cancelled_by_halt',
                                now)
                return order
            fill_n = min(order.shares, fillable)
            if fill_n < self.min_shares - SIZE_EPS:
                # Below the exchange minimum, so this fill could not have
                # existed. Keep resting: more flow may arrive.
                self._maker_count('observed:crossed_below_min_shares')
                return None
            self._fill_resting_buy(order, book, fill_n, now)
            return order

        # SELL. All or nothing, exactly as `simulate_taker_sell` is.
        if fillable < order.shares - SIZE_EPS:
            self._maker_count('observed:sell_partial_refused')
            return None
        self._fill_resting_sell(order, book, order.shares, now)
        return order

    def _fill_resting_buy(self, order: RestingOrder, book: Orderbook,
                          fill_n: float, now: int) -> None:
        """Open a position from a crossed resting bid. Fills AT OUR OWN PRICE.

        Every share is priced at `order.limit_price`, not at the ask. That is
        the entire economic claim of a maker strategy and it is the one number
        in this file that must not be fudged in either direction: filling at the
        ask would delete the edge, and filling better than our own limit would
        invent one.
        """
        if not order.stop_is_below_entry:
            # Convention 8, re-checked at the fill rather than trusted from the
            # rest-time argument validation (convention 23: a fix at one site is
            # not a fix). Unreachable while `limit_price > 0` is enforced, and
            # it stays a refusal rather than an assert so a bad order is a
            # counted skip instead of a crashed cycle.
            self._terminate(order, ORDER_CANCELLED, 'stop_not_below_entry', now)
            return

        price = order.limit_price
        cost = fill_n * price
        fee = cost * self.maker_fee_rate
        position = PaperPosition(
            position_id=str(uuid.uuid4()),
            strategy=order.strategy,
            market_slug=order.market_slug,
            token_id=order.token_id,
            outcome_side=order.outcome_side,
            shares=fill_n,
            avg_price=price,
            cost_usdc=cost,
            fee_usdc=fee,
            opened_ts=now,
            window_ts=order.window_ts,
            features=dict(order.features),
            entry_liquidity='maker',
            stop_price=order.stop_price,
        )
        self.positions[position.position_id] = position

        order.filled_shares = fill_n
        order.fill_price = price
        order.fill_ts = now
        order.fee_usdc = fee
        order.position_id = position.position_id
        partial = fill_n < order.shares - SIZE_EPS
        # A partial maker buy CANCELS its remainder rather than resting on. A
        # position whose cost basis moves across snapshots makes every per-share
        # number downstream ambiguous, and per-share is the unit both maker
        # strategies are judged in.
        order.status = ORDER_FILLED
        order.terminal_reason = 'maker_partial_fill' if partial else 'maker_fill'
        order.terminal_ts = now

        best_ask = book.best_ask
        self._log(order.strategy, order.market_slug, 'ENTER',
                  order.terminal_reason,
                  window_ts='' if order.window_ts is None else order.window_ts,
                  outcome_side=order.outcome_side, token_id=order.token_id,
                  limit_price=price, requested_shares=order.shares,
                  filled_shares=fill_n, avg_price=round(price, 4),
                  best_ask=best_ask,
                  # AGAINST THE ASK AT FILL TIME, and usually POSITIVE. Do not
                  # read this as slippage in the taker sense. By the time a
                  # resting bid is crossed the offer has come down through it,
                  # so we own the shares above the current market. That is
                  # adverse selection and it is the honest cost of being a
                  # maker. The flattering number - the spread we declined to
                  # pay at rest time - is `spread_declined_usdc` in the
                  # features cell, and the two must never be quoted as one.
                  slippage_vs_top=('' if best_ask is None
                                   else round(price - best_ask, 4)),
                  levels_consumed='{}@{}'.format(price, fill_n),
                  exhausted_book=False,
                  cost_usdc=round(cost, 4), fee_usdc=round(fee, 6),
                  max_loss_usdc=round(position.max_loss_usdc, 4),
                  max_gain_usdc=round(position.max_gain_usdc, 4),
                  position_id=position.position_id, resolution='PENDING',
                  features=self._maker_features(order))
        logger.info('PM PAPER MAKER FILL %s %s %s %.0f sh @ %.4f (ask %s, '
                    'queue_ahead %.1f, through %.1f)', order.strategy,
                    order.market_slug, order.outcome_side, fill_n, price,
                    best_ask, order.queue_ahead_shares, order.max_through_shares)

    def _fill_resting_sell(self, order: RestingOrder, book: Orderbook,
                           fill_n: float, now: int) -> None:
        """Close a position from a crossed resting ask. Proceeds AT OUR PRICE.

        Mirrors `simulate_taker_sell`'s accounting exactly - same fields, same
        WIN/LOSS rule, same `exit_kind='sell'` - so the two feed one P&L. The
        only difference recorded is `exit_liquidity`, because a scalp that was
        PAID the spread and one that PAID it are the same payoff shape but not
        the same edge.
        """
        position = self.positions.get(order.position_id or '')
        if position is None or not position.is_open:
            # The position resolved or was sold by another path while this
            # rested. Not a fill and not an error: the order simply has nothing
            # left to close.
            self._terminate(order, ORDER_CANCELLED, 'position_not_open', now)
            return

        price = order.limit_price
        proceeds = fill_n * price
        exit_fee = proceeds * self.maker_fee_rate
        pnl = proceeds - exit_fee - position.cost_usdc - position.fee_usdc

        position.proceeds_usdc = proceeds
        position.exit_fee_usdc = exit_fee
        position.exit_price = price
        position.exit_kind = 'sell'
        position.exit_liquidity = 'maker'
        position.exit_reason = order.intent or 'unspecified'
        position.exit_ts = now
        position.pnl_usdc = pnl
        # A scratch is not a win, same rule as the taker sell.
        position.resolution = 'WIN' if pnl > 0 else 'LOSS'

        order.filled_shares = fill_n
        order.fill_price = price
        order.fill_ts = now
        order.fee_usdc = exit_fee
        order.status = ORDER_FILLED
        order.terminal_reason = 'maker_fill'
        order.terminal_ts = now

        hold_sec = now - position.opened_ts
        detail = ('exit_kind=sell;exit_liquidity=maker;entry_price={:.4f};'
                  'exit_price={:.4f};hold_seconds={};proceeds_usdc={:.4f}'
                  ).format(position.avg_price, price, hold_sec, proceeds)
        self._log(order.strategy, order.market_slug, 'CLOSE',
                  position.exit_reason,
                  window_ts='' if order.window_ts is None else order.window_ts,
                  outcome_side=order.outcome_side, token_id=order.token_id,
                  limit_price=price, requested_shares=order.shares,
                  filled_shares=fill_n, avg_price=round(price, 4),
                  levels_consumed='{}@{}'.format(price, fill_n),
                  exhausted_book=False,
                  cost_usdc=round(position.cost_usdc, 4),
                  fee_usdc=round(position.total_fee_usdc, 6),
                  max_loss_usdc=round(position.max_loss_usdc, 4),
                  position_id=position.position_id,
                  resolution=position.resolution, won=pnl > 0,
                  pnl_usdc=round(pnl, 4),
                  features=self._maker_features(order, detail))
        logger.info('PM PAPER MAKER CLOSE %s %s %s %.0f sh %.4f -> %.4f '
                    'pnl=%.4f (%s)', order.strategy, order.market_slug,
                    order.outcome_side, fill_n, position.avg_price, price, pnl,
                    position.exit_reason)

    def _no_fill_reason(self, order: RestingOrder) -> str:
        """Why this order never filled. One cause, one string, never pooled.

        The ordering is causal, not cosmetic. "The cross was smaller than the
        exchange minimum" is tested before "the queue ate it", because an order
        that could not have been legal even alone at the front of the queue was
        not beaten by the queue.
        """
        if order.observations == 0:
            # Never handed a book. Convention 11: could-not-run, and it must not
            # be read as a market that refused to fill us.
            return 'maker_never_observed'
        if order.max_through_shares <= SIZE_EPS:
            return ('maker_touched_not_crossed' if order.touched
                    else 'maker_never_touched')
        if order.side == 'SELL':
            # All-or-nothing, so any shortfall at all is the partial refusal -
            # unless nothing at all got past the queue, which is a queue fact.
            if order.fillable_shares > SIZE_EPS:
                return 'maker_sell_partial_only'
            return 'maker_queue_ahead_not_cleared'
        if order.max_through_shares < self.min_shares - SIZE_EPS:
            return 'maker_cross_below_min_shares'
        return 'maker_queue_ahead_not_cleared'

    def _expire_if_due(self, order: RestingOrder,
                       now: Optional[int] = None) -> bool:
        """Expire a resting order whose TTL has run out. True if it expired."""
        now = int(time.time()) if now is None else int(now)
        if order.expires_ts is None or now < order.expires_ts:
            return False
        self._terminate(order, ORDER_EXPIRED, self._no_fill_reason(order), now)
        return True

    def expire_resting_orders(self,
                              now_ts: Optional[int] = None) -> List[RestingOrder]:
        """Expire everything past its TTL. Returns what expired."""
        now = int(time.time()) if now_ts is None else int(now_ts)
        return [o for o in list(self.open_resting_orders())
                if self._expire_if_due(o, now)]

    def cancel_resting_order(self, order_id: str,
                             reason: str = 'cancelled_by_strategy'
                             ) -> Optional[RestingOrder]:
        """Pull a resting order. Returns it, or None if there was nothing to pull.

        A cancel is its own terminal reason and never shares a counter with an
        expiry: "we changed our mind" and "the market never came to us" are
        different facts about a strategy and they have different fixes.
        """
        order = self.resting_orders.get(order_id)
        if order is None:
            self._log('unknown', 'unknown', 'SKIP', 'unknown_resting_order',
                      position_id=order_id)
            return None
        if not order.is_resting:
            self._log(order.strategy, order.market_slug, 'SKIP',
                      'resting_order_not_open', position_id=order.order_id,
                      resolution=order.status)
            return None
        self._terminate(order, ORDER_CANCELLED, reason or 'cancelled_by_strategy',
                        int(time.time()))
        return order

    def _terminate(self, order: RestingOrder, status: str, reason: str,
                   now: int) -> None:
        """End a resting order with no fill, and WRITE THE ROW.

        This is the method that makes "it never filled" a number instead of an
        absence. Without it a maker session would report zero entries and zero
        skips, which reads identically to a session where the strategy never
        signalled at all - the exact ambiguity convention 20 exists to remove.
        """
        order.status = status
        order.terminal_reason = reason
        order.terminal_ts = now
        action = 'CANCEL' if status == ORDER_CANCELLED else 'EXPIRE'
        self._log(order.strategy, order.market_slug, action, reason,
                  window_ts='' if order.window_ts is None else order.window_ts,
                  outcome_side=order.outcome_side, token_id=order.token_id,
                  limit_price=order.limit_price,
                  requested_shares=order.shares,
                  filled_shares=order.filled_shares,
                  position_id=order.order_id, resolution=status,
                  features=self._maker_features(order))

    def build_fill(self, position: PaperPosition) -> Fill:
        """Fill record for a position, for callers that want the flat shape."""
        return Fill(
            market_slug=position.market_slug,
            token_id=position.token_id,
            outcome=position.outcome_side,
            side='BUY',
            shares=position.shares,
            avg_price=position.avg_price,
            cost_usdc=position.cost_usdc,
            fee_usdc=position.fee_usdc,
            timestamp=position.opened_ts,
        )

    # -- resolution ---------------------------------------------------------

    def resolve_positions(self) -> List[PaperPosition]:
        """Settle any open position whose market the oracle has resolved.

        Only exact 1.0/0.0 counts (see `prices.resolution_price`). A position
        whose market has not resolved stays PENDING forever rather than being
        marked to a 0.99 book - a fabricated win is worse than a missing one.
        Returns the positions settled by this call.

        Deliberately NOT gated on the halt. Resolution is bookkeeping, not a
        trade: it records what an already-open position settled at. Skipping it
        during a halt would leave positions PENDING that the oracle has already
        decided, and the operator would be reading a halted session's PnL with
        the losses missing.
        """
        settled = []
        # One read per (slug, outcome) per call. Five positions on the same
        # market used to mean five identical Gamma round trips.
        seen: Dict[tuple, Optional[float]] = {}
        for pos in list(self.positions.values()):
            if not pos.is_open:
                continue
            key = (pos.market_slug, pos.outcome_side)
            if key in seen:
                value = seen[key]
            else:
                try:
                    value = resolution_price(self.client, pos.market_slug,
                                             pos.outcome_side)
                except Exception as exc:
                    # An unreadable oracle is not an unresolved market and is
                    # certainly not a loss (convention 11). Leave the position
                    # PENDING and record that the read failed, so a run with a
                    # broken feed does not look like a run with no resolutions.
                    logger.warning('PM PAPER resolution read raised for %s: '
                                   '%s: %s', pos.market_slug,
                                   type(exc).__name__, exc)
                    self._log(pos.strategy, pos.market_slug, 'SKIP',
                              'resolution_read_error',
                              window_ts='' if pos.window_ts is None else pos.window_ts,
                              outcome_side=pos.outcome_side,
                              token_id=pos.token_id,
                              position_id=pos.position_id,
                              resolution='PENDING')
                    seen[key] = None
                    continue
                seen[key] = value
            if value is None:
                continue

            won = value == WINNING_REDEMPTION
            redemption = pos.shares * (WINNING_REDEMPTION if won
                                       else LOSING_REDEMPTION)
            pos.pnl_usdc = redemption - pos.cost_usdc - pos.fee_usdc
            pos.resolution = 'WIN' if won else 'LOSS'
            # Tagged so `summary()` can keep the two payoff shapes apart. A
            # position that reaches here was never sold; `simulate_taker_sell`
            # sets `exit_kind='sell'` and closes it out of this loop entirely.
            pos.exit_kind = 'resolution'
            pos.exit_reason = 'oracle_' + pos.resolution.lower()
            pos.exit_price = WINNING_REDEMPTION if won else LOSING_REDEMPTION
            pos.proceeds_usdc = redemption
            settled.append(pos)

            self._log(pos.strategy, pos.market_slug, 'RESOLVE',
                      pos.resolution.lower(),
                      window_ts='' if pos.window_ts is None else pos.window_ts,
                      outcome_side=pos.outcome_side, token_id=pos.token_id,
                      filled_shares=pos.shares,
                      avg_price=round(pos.avg_price, 4),
                      cost_usdc=round(pos.cost_usdc, 4),
                      fee_usdc=round(pos.fee_usdc, 6),
                      position_id=pos.position_id,
                      resolution=pos.resolution, won=won,
                      pnl_usdc=round(pos.pnl_usdc, 4))
        return settled

    # -- accounting ---------------------------------------------------------

    def open_positions(self) -> List[PaperPosition]:
        return [p for p in self.positions.values() if p.is_open]

    def resolved_positions(self) -> List[PaperPosition]:
        return [p for p in self.positions.values() if not p.is_open]

    def realized_pnl(self) -> float:
        return sum(p.pnl_usdc or 0.0 for p in self.resolved_positions())

    def capital_at_risk(self) -> float:
        """USDC that is currently unrecoverable if every open position loses.

        On a binary this is exact, not an estimate: max loss is the premium.
        """
        return sum(p.max_loss_usdc for p in self.open_positions())

    def get_equity(self) -> float:
        """Starting capital + realized PnL - premium tied up in open positions.

        Open positions are held at ZERO, not marked to the book. On a 5-minute
        market the book is thin enough that marking is mostly noise, and
        holding at zero means equity can only ever surprise upward.
        """
        return (self.starting_equity + self.realized_pnl()
                - self.capital_at_risk())

    def summary(self) -> dict:
        """Session summary. Pending is reported separately from won and lost.

        Collapsing PENDING into either bucket is the single easiest way to
        make a paper log lie, so the three counts never merge here.
        """
        resolved = self.resolved_positions()
        wins = [p for p in resolved if p.resolution == 'WIN']
        pending = self.open_positions()
        entries = len(self.positions)

        # Entry price and breakeven only mean something for a position that
        # REDEEMED. A trade sold at 0.53 never had a 1.00-or-0.00 payoff, so
        # folding it in would move a number whose whole job is to be compared
        # against a resolution win rate. Legacy behaviour is preserved exactly:
        # before `simulate_taker_sell` existed every resolved position was a
        # resolution exit, so this filter is a no-op on any pre-existing run.
        redeemed = [p for p in resolved if p.exit_kind != 'sell']
        resolved_shares = sum(p.shares for p in redeemed)
        weighted_entry = ((sum(p.cost_usdc for p in redeemed) / resolved_shares)
                          if resolved_shares else None)
        # Entry price and breakeven coincide only at a ZERO fee. taker_fee_rate
        # is a config knob precisely because that is an assumption with an
        # expiry date (convention 17), so the hurdle is computed from money
        # actually spent, fees included. Reporting the bare entry price here
        # would UNDERSTATE the bar in the one field whose whole job is to be
        # compared against win_rate.
        breakeven = (((sum(p.cost_usdc for p in redeemed)
                       + sum(p.total_fee_usdc for p in redeemed))
                      / (resolved_shares * WINNING_REDEMPTION))
                     if resolved_shares else None)

        # The two exit kinds, never pooled. See the module docstring.
        by_exit_kind: Dict[str, dict] = {}
        for kind in ('resolution', 'sell'):
            group = [p for p in resolved if (p.exit_kind or 'resolution') == kind]
            if not group:
                continue
            group_wins = [p for p in group if p.resolution == 'WIN']
            pnl = sum(p.pnl_usdc or 0.0 for p in group)
            shares = sum(p.shares for p in group)
            by_exit_kind[kind] = {
                'closed': len(group),
                'wins': len(group_wins),
                'losses': len(group) - len(group_wins),
                'win_rate': len(group_wins) / len(group),
                'realized_pnl_usdc': round(pnl, 4),
                'avg_pnl_per_trade_usdc': round(pnl / len(group), 4),
                # The kill-condition unit for PM_fair_value_arb: cents per
                # share, not dollars per trade, so it is comparable across
                # sizes.
                'avg_pnl_per_share_usdc': (round(pnl / shares, 6)
                                           if shares else None),
            }

        # The maker block. Reported SEPARATELY and never folded into the taker
        # numbers, for the same reason `by_exit_kind` exists: a maker fill was
        # PAID the spread and a taker fill PAID it, and the difference between
        # those two is the entire thing `box_builder` and `grid_hedge` claim.
        # `fill_rate` is the number that decides whether either strategy is
        # real, and it is deliberately orders-based: a maker's denominator is
        # orders rested, not signals generated.
        rested = list(self.resting_orders.values())
        filled_orders = [o for o in rested if o.status == ORDER_FILLED]
        no_fill_reasons: Dict[str, int] = {}
        for o in rested:
            if o.status in (ORDER_EXPIRED, ORDER_CANCELLED) and o.terminal_reason:
                no_fill_reasons[o.terminal_reason] = (
                    no_fill_reasons.get(o.terminal_reason, 0) + 1)
        maker_block = {
            'fill_model': MAKER_FILL_MODEL,
            'orders_rested': len(rested),
            'orders_filled': len(filled_orders),
            'orders_resting': len(self.open_resting_orders()),
            # None, not 0.0, when nothing was ever rested. Convention 11: a
            # strategy that never quoted did not fail to get filled.
            'fill_rate': ((len(filled_orders) / len(rested)) if rested
                          else None),
            'no_fill_reasons': no_fill_reasons,
            'observation_counts': dict(self.maker_counts),
            'capital_committed_usdc': round(
                self.capital_committed_to_resting_orders(), 4),
            'maker_entries': sum(1 for p in self.positions.values()
                                 if p.entry_liquidity == 'maker'),
            'maker_exits': sum(1 for p in self.positions.values()
                               if p.exit_liquidity == 'maker'),
            'note': ('fill_rate counts ORDERS, not windows. The fill rule is a '
                     'strict cross minus the queue that was ahead of us at rest '
                     'time; a book that merely TOUCHED our price is counted '
                     'under maker_touched_not_crossed and never filled. '
                     'capital_committed_usdc is NOT in capital_at_risk_usdc: an '
                     'unfilled bid cannot lose anything.'),
        }

        return {
            'mode': self.mode,
            'halted': is_halted(),
            'entries': entries,
            'maker': maker_block,
            'resolved': len(resolved),
            'pending': len(pending),
            'wins': len(wins),
            'losses': len(resolved) - len(wins),
            'win_rate': (len(wins) / len(resolved)) if resolved else None,
            'share_weighted_entry_price': weighted_entry,
            'breakeven_win_rate': breakeven,
            'realized_pnl_usdc': round(self.realized_pnl(), 4),
            'capital_at_risk_usdc': round(self.capital_at_risk(), 4),
            'equity_usdc': round(self.get_equity(), 4),
            'closed_early': sum(1 for p in resolved if p.closed_early),
            'by_exit_kind': by_exit_kind,
            'decision_counts': dict(self.decision_counts),
            'log_path': self.log_path,
            'note': ('win_rate is computed on RESOLVED positions only and '
                     'pending is never folded into wins or losses. It POOLS '
                     'both exit kinds - use by_exit_kind for anything that '
                     'matters, because a 1c scalp sold at the bid and a 50c '
                     'binary held to the oracle have different payoff shapes '
                     'and a pooled win rate describes neither. '
                     'share_weighted_entry_price and breakeven_win_rate cover '
                     'RESOLUTION exits only; they are meaningless for a '
                     'position that never redeemed. On a binary held to '
                     'resolution, entry price plus fees IS the hurdle.'),
        }

    def market_for_slug(self, slug: str):
        """Convenience passthrough so strategies need only the adapter."""
        return get_market_by_slug(self.client, slug)
