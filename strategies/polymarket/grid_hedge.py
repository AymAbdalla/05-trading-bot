"""Grid Hedge: a two-sided ladder of resting bids. BLOCKED BY A REFUSAL.

THE IDEA. Rest a ladder of buy orders at fixed intervals below the current
price on BOTH the Up and the Down side of a binary. Every rung that fills and
later trades back up to fair value is sold for the interval. The two sides
self-hedge: a move down fills Up-side rungs cheaply, and the reversal fills the
Down side. On a market whose implied volatility exceeds what actually gets
realised, the ladder collects the difference.

## WHY THIS FILE RETURNS 'QUOTE' AND NEVER 'ENTER'

A grid of resting buy orders below the current price is a MAKER strategy, and
`PolymarketPaperAdapter` simulates TAKER fills only. Read
`strategies/polymarket/box_builder.py`: same structural problem, same answer.
It reaches its full decision logic, then returns action `QUOTE` with reason
`maker_fill_not_simulated`, and the shadow loop counts that as a
maker-quote-not-simulable row rather than as a trade.

The temptation here is specific and worth naming, because the wrong version of
this file is easy to write and looks right. Simulating a resting 0.42 rung as
"filled if the ask ever printed 0.42" would manufacture EXACTLY the fills this
strategy's claimed edge depends on. Worse, it would manufacture them with a
systematic bias: in a real book the rungs that fill cheaply are
disproportionately the ones filled by flow that knows something, so a
touch-means-fill model books all of the good fills and none of the adverse
selection. It would not produce a slightly optimistic backtest. It would
produce the strategy's entire P&L out of nothing.

So this strategy is BLOCKED BY A REFUSAL, NOT BY A BUG. Nothing in it is
broken, no threshold is too tight, and lowering `GRID_SPACING` or widening
`MAX_SPREAD_FOR_GRID` would not unblock a single fill. What unblocks it is a
maker fill model - queue position, order flow, and a calibration target like
box_builder's own evidence that one maker filled 57% at T-240 while another
armed 35 times at 0.89 and got ZERO fills. A fill model that cannot reproduce
that gap is not a model.

`assert_not_enter` is the wiring test for that claim rather than a sentence in
this docstring (convention 22): every decision this class emits passes through
it, and an ENTER stops the run.

## THE FILL MODEL LANDED. THE LOOP WIRING DID NOT.

`engine/polymarket/paper_adapter.py` now has `simulate_maker_buy` /
`simulate_maker_sell`. A rung rests, and it fills only when a later book
snapshot trades STRICTLY THROUGH the rung price with more size than the queue
that was ahead of us at rest time. A touch is not a fill; it is counted under
`maker_touched_not_crossed`.

`assert_not_enter` stays, and this file still returns QUOTE. That is not a
leftover: a maker does not enter at decision time, it rests and finds out one or
more cycles later, so QUOTE remains the truthful decision and the adapter
returns a `RestingOrder` rather than a position.

What is still blocked is production. `engine/polymarket/shadow_loop.py`
short-circuits `action == 'QUOTE'` into a counted skip and never hands these
rungs to the adapter, so no rung has rested in a live cycle and the kill
condition still has zero of its 50 fills. `kill_condition_blocked_by` below is
deliberately NOT cleared. Convention 11: a shadow log full of
`maker_quote_not_simulable` is still ZERO EVIDENCE in either direction.

## BUT EVERYTHING A FILL MODEL WOULD NEED IS COMPUTED AND EXPOSED

The ladder is built in full on every QUOTE row: both sides, every rung, price
and share count, plus the budget accounting. `grid_pnl` is here and is exact.
When the fill model lands, the work in this file is wiring, not design.

## THE BUDGET ACCOUNTING IDENTITY (convention 20)

Shares per rung are rounded DOWN, which always leaves change. A rung priced
above the floor but too small to afford `MIN_SHARES` is dropped, which leaves
its whole slice. Neither is allowed to vanish:

    sum(rung.allocated_usdc) + unallocated_usdc == side_budget_usdc

That identity is ASSERTED in `build_grid_side`, and both drop causes are
counted separately (`rungs_below_floor`, `rungs_unaffordable`). Per-rung budget
is computed from the CONFIGURED level count, never from the surviving count:
dividing by survivors would silently inflate the remaining rungs every time one
dropped, which is a position size that grows when the book gets worse.

## THE VOL CLAIM IS THE WHOLE STRATEGY, AND IT IS ALSO NOT_TESTED

The claim is that the market's implied volatility exceeds realised. Both sides
of that comparison come from the context and neither is measured by us:

    realised  from `ctx.atr14`, which base.MarketContext documents as BASIS
              POINTS. A USD ATR fed here is ~10,000x too small and the
              comparison silently never passes - the same unit trap
              corridor_collector's docstring flags.
    implied   from `ctx.lead_bps` and the Up-side mid, inverted through a
              normal CDF: P(up) = Phi(lead / sigma), so sigma = lead / Phi^-1(p).

Those two are not on the same horizon, so realised is converted from a mean
absolute move to a sigma (for a normal, E|X| = sigma * sqrt(2/pi)) and then
scaled to the remaining fraction of the window. The normality assumption is
explicit and is doing real work; a fat-tailed truth makes realised look smaller
than it is, which biases this comparison IN FAVOUR of quoting. That is the
wrong direction for a strategy to be biased, and it is stated rather than
buried.

`ctx.lead_bps` itself depends on the measured proxy strike, so a caller must
respect `engine/polymarket/strike.py`'s noise floor before handing one in. This
class does not re-derive it and does not second-guess it.

## NAMED REASONS, NONE OF THEM POOLED

    no_market                       no market on the context
    both_books_unavailable          neither side has a book
    one_book_unavailable            exactly one side does. A one-sided grid is
                                    a directional ladder, not a hedge, and is
                                    a DIFFERENT fact from having no book.
    no_asks                         a book exists with nothing offered
    spread_undefined_no_bid         cannot measure a spread with no bid
    spread_too_wide_for_grid        the spread swallows the rung interval
    book_too_thin_for_grid          not enough resting size to call this a
                                    market
    grid_budget_exhausted           no rung on either side survived sizing
    vol_inputs_unavailable          ctx.atr14 missing: the REALISED leg
    implied_vol_inputs_unavailable  ctx.lead_bps missing: the IMPLIED leg
    no_window_clock                 cannot scale realised without a clock
    implied_vol_undefined_at_the_money   Phi^-1(p) is ~0, sigma is undefined
    implied_vol_sign_inconsistent   the book and the strike proxy disagree on
                                    which side is ahead. A data-quality fact,
                                    not a vol reading.
    implied_vol_below_realized      computed both, the claim is false right now
    maker_fill_not_simulated        the QUOTE path

Only `implied_vol_below_realized` is a RESULT. `implied_vol_sign_inconsistent`
is a data-quality fact about two inputs. Everything else is a convention 11
cannot-run or the refusal. `vol_inputs_unavailable` and
`implied_vol_inputs_unavailable` are deliberately two strings for what a
careless version would call one gate: a missing ATR and a missing lead have
different owners and different fixes.

KILL CONDITION: grid PnL below -$5.00 over 50 grid fills, measured by
`grid_pnl` fed from `backtest/polymarket_harness.py`.

AND IT IS CURRENTLY UNMEASURABLE. Say it out loud rather than pretending the
kill condition is armed: the harness cannot feed `grid_pnl` because maker fills
are not modelled, so there will never be 50 grid fills, so the condition can
never fire. It is a specification of what would kill this strategy, not a live
guard on it. `grid_pnl` itself is exact and tested; what is missing is the
input. Anyone reading a shadow log full of `maker_fill_not_simulated` rows
should read it as ZERO EVIDENCE in either direction, not as a strategy that was
tested and found flat (convention 11).
"""
import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Dict, Iterable, List, Optional, Tuple

from engine.polymarket.paper_adapter import MAKER_FILL_MODEL
from strategies.polymarket.base import (WINDOW_SECONDS, Decision, Leg,
                                        MarketContext, PolymarketStrategy)

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

# ---------------------------------------------------------------------------
# Grid geometry. House numbers, every one an assumption with an expiry date.
# ---------------------------------------------------------------------------

#: Rungs per side. Five below the ask at 3c spacing reaches 15c down, which on
#: a 5-minute binary is a large move and is meant to be.
GRID_LEVELS = 5

#: Interval between rungs, in dollars per share. This is ALSO the per-rung
#: profit target: a rung filled at 0.42 is worth the interval if it trades back
#: to 0.45. Tightening it does not create fills, it only shrinks the target -
#: see the module docstring on what is actually blocking this strategy.
GRID_SPACING = 0.03

#: No rung below this. Under 2c the tick grid has almost no resolution left,
#: minimum-size rules bite, and a fill there is a lottery ticket rather than a
#: mean-reversion trade.
MIN_RUNG_PRICE = 0.02

#: Total budget across BOTH sides. Split evenly, so each side gets half.
GRID_BUDGET_USDC = 50.0

#: Exchange minimum order size, in shares. A rung that cannot afford this is
#: dropped and COUNTED; it is never rounded up.
MIN_SHARES = 5

#: A spread wider than this eats the rung interval before the grid does any
#: work. At 5c against a 3c interval the round trip is already negative.
MAX_SPREAD_FOR_GRID = 0.05

#: Resting size that must exist within the grid's price range for this to count
#: as a market at all. Measured on the ASK side, and that is a PROXY, not the
#: thing we care about: whether OUR resting bid would fill is not observable
#: from a book snapshot at all. It is here to reject a dead book, not to predict
#: a fill.
MIN_GRID_DEPTH_SHARES = 50.0

#: Polymarket charges no explicit taker fee on the CLOB today. A knob rather
#: than a hardcoded 0.0 because "the fee is zero" is an assumption with an
#: expiry date (convention 17), and a strategy whose edge is 3c per share dies
#: the day that changes. Matches `paper_adapter.DEFAULT_TAKER_FEE_RATE`.
DEFAULT_FEE_RATE = 0.0

PRICE_TICK = 0.01
PRICE_DECIMALS = 2

#: For a normal, E|X| = sigma * sqrt(2/pi). This converts a mean-absolute-move
#: ATR into a sigma. The normality assumption is doing real work here; see the
#: module docstring.
_MEAN_ABS_TO_SIGMA = 1.0 / math.sqrt(2.0 / math.pi)

#: Below this |Phi^-1(p)| the implied sigma is a division by nothing.
_PROBIT_EPS = 1e-3

#: Every reason `evaluate` can produce, QUOTE included. Listed so a reader can
#: see no two causes share a string, and so a test can assert each is reachable
#: rather than trusting the docstring (convention 22).
DECISION_REASONS = (
    'no_market',
    'both_books_unavailable',
    'one_book_unavailable',
    'no_asks',
    'spread_undefined_no_bid',
    'spread_too_wide_for_grid',
    'book_too_thin_for_grid',
    'grid_budget_exhausted',
    'vol_inputs_unavailable',
    'implied_vol_inputs_unavailable',
    'no_window_clock',
    'implied_vol_undefined_at_the_money',
    'implied_vol_sign_inconsistent',
    'implied_vol_below_realized',
    'maker_fill_not_simulated',
)

_NORMAL = NormalDist(0.0, 1.0)


def round_price(price: float) -> float:
    """Snap onto the 1c tick grid. Polymarket quotes nothing finer."""
    return round(float(price), PRICE_DECIMALS)


def assert_not_enter(action: str) -> str:
    """Refuse an ENTER, loudly. Every GridHedge decision passes through here.

    This is the module docstring's central claim made into a WIRING TEST rather
    than a sentence (convention 22). It is not defensive coding: this strategy
    has no simulable taker path at all, so an ENTER out of it would be a fill
    nobody modelled and a P&L nobody earned. If a future edit reaches this, that
    edit is the bug and it should stop the run rather than quietly book a
    manufactured trade.
    """
    if action == 'ENTER':
        raise AssertionError(
            'PM_grid_hedge must never return ENTER: every rung is a resting '
            'maker order and maker fills are not simulated by the paper '
            'adapter. Unblocking this needs a maker fill model, not a looser '
            'threshold. See the module docstring.')
    return action


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GridRung:
    """One resting bid in the ladder.

    The order type is always maker. It is spelled out on the Leg rather than
    implied, so a reader of a logged leg cannot mistake a rung for a marketable
    order.
    """

    level: int                 # 1 = nearest the ask
    price: float
    shares: int
    budget_usdc: float         # the slice this rung was given
    allocated_usdc: float      # what it actually spent

    @property
    def leftover_usdc(self) -> float:
        """Change from rounding shares DOWN. Never silently dropped."""
        return self.budget_usdc - self.allocated_usdc

    def to_dict(self) -> dict:
        return {'level': self.level, 'price': self.price,
                'shares': self.shares,
                'budget_usdc': round(self.budget_usdc, 6),
                'allocated_usdc': round(self.allocated_usdc, 6),
                'leftover_usdc': round(self.leftover_usdc, 6)}


@dataclass
class GridSide:
    """The ladder for ONE outcome side, with full drop accounting.

    The accounting identity `allocated_usdc + unallocated_usdc == budget_usdc`
    holds by construction and is asserted in `build_grid_side`. Two drop causes
    (`rungs_below_floor`, `rungs_unaffordable`) never share one number.
    """

    outcome_side: str
    best_ask: float
    budget_usdc: float
    per_rung_budget_usdc: float
    rungs: List[GridRung] = field(default_factory=list)
    rungs_below_floor: int = 0
    rungs_unaffordable: int = 0

    @property
    def allocated_usdc(self) -> float:
        return sum(r.allocated_usdc for r in self.rungs)

    @property
    def unallocated_usdc(self) -> float:
        return self.budget_usdc - self.allocated_usdc

    @property
    def total_shares(self) -> int:
        return sum(r.shares for r in self.rungs)

    @property
    def max_loss_usdc(self) -> float:
        """If every rung filled and every one resolved worthless.

        On a binary that is the exact floor: a losing share is worth 0.00. This
        IS the stop convention 8 requires, and it is why no separate stop price
        appears anywhere in this file.
        """
        return self.allocated_usdc

    def to_dict(self) -> dict:
        return {
            'outcome_side': self.outcome_side,
            'best_ask': self.best_ask,
            'budget_usdc': round(self.budget_usdc, 6),
            'per_rung_budget_usdc': round(self.per_rung_budget_usdc, 6),
            'rungs': [r.to_dict() for r in self.rungs],
            'rung_count': len(self.rungs),
            'rungs_below_floor': self.rungs_below_floor,
            'rungs_unaffordable': self.rungs_unaffordable,
            'allocated_usdc': round(self.allocated_usdc, 6),
            'unallocated_usdc': round(self.unallocated_usdc, 6),
            'total_shares': self.total_shares,
            'max_loss_usdc': round(self.max_loss_usdc, 6),
        }


def grid_prices(best_ask: float, levels: int = GRID_LEVELS,
                spacing: float = GRID_SPACING) -> List[float]:
    """Rung prices below `best_ask`, nearest first. Snapped to the tick grid.

    The `MIN_RUNG_PRICE` floor is NOT applied here; `build_grid_side` applies it
    and counts what it dropped. This function is pure geometry so a caller can
    inspect the raw ladder including the rungs that will later be refused.
    """
    return [round_price(float(best_ask) - spacing * (i + 1))
            for i in range(max(0, int(levels)))]


def build_grid_side(outcome_side: str, best_ask: float,
                    budget_usdc: float,
                    levels: int = GRID_LEVELS,
                    spacing: float = GRID_SPACING,
                    min_rung_price: float = MIN_RUNG_PRICE,
                    min_shares: int = MIN_SHARES) -> GridSide:
    """Build one side's ladder and account for every cent of the budget.

    THE ROUNDING RULE, spelled out because it is the part that goes wrong
    silently:

      - per-rung budget is `budget_usdc / levels`, using the CONFIGURED level
        count. Never the surviving count. Dividing by survivors makes each
        remaining rung bigger every time one drops, which is a position that
        grows precisely when the book got worse.
      - shares are `floor(per_rung_budget / price)`. Always DOWN. Rounding up
        would overspend the budget by up to one share per rung, and across ten
        rungs that is a real number.
      - a rung below `min_rung_price` is dropped, counted in
        `rungs_below_floor`, and its whole slice goes to unallocated.
      - a rung that can afford fewer than `min_shares` is dropped, counted in
        `rungs_unaffordable`, and its whole slice goes to unallocated.

    Nothing is redistributed. Change is change, and it is reported.
    """
    levels = max(0, int(levels))
    budget = float(budget_usdc)
    per_rung = (budget / levels) if levels else 0.0
    side = GridSide(outcome_side=outcome_side, best_ask=float(best_ask),
                    budget_usdc=budget, per_rung_budget_usdc=per_rung)

    for i, price in enumerate(grid_prices(best_ask, levels, spacing)):
        if price < min_rung_price - 1e-12:
            side.rungs_below_floor += 1
            continue
        shares = int(math.floor(per_rung / price + 1e-9)) if price > 0 else 0
        if shares < min_shares:
            side.rungs_unaffordable += 1
            continue
        side.rungs.append(GridRung(level=i + 1, price=price, shares=shares,
                                   budget_usdc=per_rung,
                                   allocated_usdc=shares * price))

    # Convention 20: assert the identity rather than trusting it. Every dropped
    # rung's slice has to be findable in `unallocated_usdc`. With zero levels
    # there are no slices at all and the whole budget is unallocated, which this
    # identity cannot express, so it is skipped rather than faked.
    if levels:
        accounted = (side.allocated_usdc
                     + sum(r.leftover_usdc for r in side.rungs)
                     + per_rung * (side.rungs_below_floor
                                   + side.rungs_unaffordable))
        assert abs(accounted - budget) < 1e-6, (
            'grid budget accounting broken on {}: allocated+leftover+dropped '
            '= {} vs budget {}'.format(outcome_side, accounted, budget))
    return side


# ---------------------------------------------------------------------------
# PnL. This is what the kill condition measures, once something can feed it.
# ---------------------------------------------------------------------------

def grid_pnl(fills: Iterable,
             fee_rate: float = DEFAULT_FEE_RATE) -> Dict[str, object]:
    """Realised PnL for a set of completed grid round trips, fees included.

    `fills` is an iterable of `(rung_price, exit_price, shares)` triples, or of
    mappings carrying those three keys. Each entry is ONE round trip: bought at
    the rung, sold at the exit.

    Returns a dict with `net_usdc`, `gross_usdc`, `fees_usdc`, `fills`,
    `winners`, `losers` and a per-fill breakdown. `net_usdc` is the number the
    kill condition reads.

    Fees are charged on BOTH legs, on notional, at `fee_rate`. Polymarket's CLOB
    taker fee is zero today; see `DEFAULT_FEE_RATE` for why that is a knob and
    not a constant.

    Every row is stamped `fills_are_hypothetical_maker_fills=True`, because
    nothing in this repo can produce a real one.

    RAISES on garbage input: a non-finite value, a price outside [0, 1] (a
    binary share never settles outside it, so a quote outside it is a corrupt
    field), or a non-positive share count. Same reasoning as
    `orderbook.walk_book`: returning 0.0 for a malformed input reads identically
    to "the grid made nothing", which files a code defect under the strategy's
    performance (convention 11).
    """
    rate = float(fee_rate)
    if not math.isfinite(rate) or rate < 0:
        raise ValueError('fee_rate must be a finite non-negative number, '
                         'got {!r}'.format(fee_rate))

    rows: List[dict] = []
    gross = 0.0
    fees = 0.0
    winners = 0
    losers = 0

    for i, fill in enumerate(fills or ()):
        if isinstance(fill, dict):
            entry, exit_px, shares = (fill.get('rung_price'),
                                      fill.get('exit_price'),
                                      fill.get('shares'))
        else:
            try:
                entry, exit_px, shares = fill
            except (TypeError, ValueError):
                raise ValueError(
                    'fill {} must be (rung_price, exit_price, shares) or a '
                    'mapping with those keys, got {!r}'.format(i, fill))

        try:
            entry = float(entry)
            exit_px = float(exit_px)
            shares = float(shares)
        except (TypeError, ValueError):
            raise ValueError(
                'fill {} has a non-numeric field: {!r}'.format(i, fill))
        for name, value in (('rung_price', entry), ('exit_price', exit_px),
                            ('shares', shares)):
            if not math.isfinite(value):
                raise ValueError('fill {} has a non-finite {}: {!r}'.format(
                    i, name, value))
        if not (0.0 <= entry <= 1.0) or not (0.0 <= exit_px <= 1.0):
            raise ValueError(
                'fill {} prices must lie on [0, 1]; a binary share never '
                'settles outside it. got entry={!r} exit={!r}'.format(
                    i, entry, exit_px))
        if shares <= 0:
            raise ValueError(
                'fill {} must have positive shares, got {!r}'.format(i, shares))

        cost = entry * shares
        proceeds = exit_px * shares
        fee = (cost + proceeds) * rate
        net = proceeds - cost - fee
        gross += proceeds - cost
        fees += fee
        if net > 0:
            winners += 1
        elif net < 0:
            losers += 1
        rows.append({'rung_price': entry, 'exit_price': exit_px,
                     'shares': shares, 'cost_usdc': round(cost, 6),
                     'proceeds_usdc': round(proceeds, 6),
                     'fee_usdc': round(fee, 6), 'net_usdc': round(net, 6)})

    return {
        'fills': len(rows),
        'gross_usdc': round(gross, 6),
        'fees_usdc': round(fees, 6),
        'net_usdc': round(gross - fees, 6),
        'fee_rate': rate,
        'winners': winners,
        'losers': losers,
        'scratches': len(rows) - winners - losers,
        'per_fill': rows,
        'fills_are_hypothetical_maker_fills': True,
    }


# ---------------------------------------------------------------------------
# Volatility inputs
# ---------------------------------------------------------------------------

def implied_sigma_bps(lead_bps: float,
                      p_up: float) -> Tuple[Optional[float], str]:
    """Invert P(up) = Phi(lead / sigma) for sigma, in basis points.

    Returns `(sigma_bps, 'ok')`, or `(None, reason)` for the two degenerate
    cases, which are different facts and get different strings:

      implied_vol_undefined_at_the_money   Phi^-1(p) is within _PROBIT_EPS of
                                           zero, or p is not strictly inside
                                           (0, 1). At the money the equation
                                           carries no information about sigma at
                                           all: every sigma prices a coin flip.
      implied_vol_sign_inconsistent        the book and the strike proxy
                                           disagree about which side is ahead,
                                           so the algebra yields a negative
                                           sigma. That is a data-quality fact
                                           about the two inputs, not a
                                           volatility reading, and it must not
                                           be pooled with a real one.
    """
    p = float(p_up)
    if not (0.0 < p < 1.0):
        return None, 'implied_vol_undefined_at_the_money'
    z = _NORMAL.inv_cdf(p)
    if abs(z) < _PROBIT_EPS:
        return None, 'implied_vol_undefined_at_the_money'
    sigma = float(lead_bps) / z
    if not math.isfinite(sigma):             # pragma: no cover - z guards it
        return None, 'implied_vol_undefined_at_the_money'
    if sigma <= 0:
        return None, 'implied_vol_sign_inconsistent'
    return sigma, 'ok'


def realized_sigma_bps(atr14_bps: float, seconds_remaining: float,
                       window_seconds: int = WINDOW_SECONDS) -> float:
    """`ctx.atr14` (a MEAN ABSOLUTE move, bps) as a sigma over the time left.

    Two conversions, both stated because both are assumptions:
      1. mean absolute move -> sigma, via E|X| = sigma * sqrt(2/pi). Normality.
      2. one full window -> the remaining fraction, via sqrt of time. Diffusion.

    A fat-tailed truth makes this number too SMALL, which biases the
    implied-over-realised comparison in favour of quoting. Wrong direction, and
    named rather than buried.
    """
    per_window = float(atr14_bps) * _MEAN_ABS_TO_SIGMA
    frac = max(0.0, float(seconds_remaining)) / float(window_seconds)
    return per_window * math.sqrt(frac)


# ---------------------------------------------------------------------------
# The strategy
# ---------------------------------------------------------------------------

class GridHedge(PolymarketStrategy):
    """Two-sided resting ladder. Returns QUOTE, never ENTER.

    Every rung is a maker order, and `PolymarketPaperAdapter` simulates taker
    fills only, so this strategy has no simulable entry path at all. It runs its
    full decision logic, builds the complete ladder, computes the vol comparison
    its whole claim rests on, and returns `QUOTE` with reason
    `maker_fill_not_simulated`.

    Read the module docstring before "fixing" that. It is a refusal, not a bug.
    """

    strategy_name = 'PM_grid_hedge'
    uses_maker_orders = True
    paper_mode = PAPER_MODE

    #: Each filled rung would be managed to its own profit target rather than
    #: held to resolution - but since no rung can fill, no exit ever runs. False,
    #: honestly, rather than True describing code nothing reaches.
    manages_exits = False

    #: Read by the shadow loop BEFORE evaluate() is called. `lead_bps` comes from
    #: the measured proxy strike, so the loop must apply
    #: `STRIKE_PROXY_NOISE_FLOOR_BPS` before this class sees it. This class does
    #: not re-derive the strike and does not second-guess that floor.
    needs_strike = True

    def __init__(self, grid_levels: int = GRID_LEVELS,
                 grid_spacing: float = GRID_SPACING,
                 min_rung_price: float = MIN_RUNG_PRICE,
                 grid_budget_usdc: float = GRID_BUDGET_USDC,
                 min_shares: int = MIN_SHARES,
                 max_spread: float = MAX_SPREAD_FOR_GRID,
                 min_grid_depth_shares: float = MIN_GRID_DEPTH_SHARES,
                 fee_rate: float = DEFAULT_FEE_RATE):
        self.grid_levels = grid_levels
        self.grid_spacing = grid_spacing
        self.min_rung_price = min_rung_price
        self.grid_budget_usdc = grid_budget_usdc
        self.min_shares = min_shares
        self.max_spread = max_spread
        self.min_grid_depth_shares = min_grid_depth_shares
        self.fee_rate = fee_rate

    # -- helpers ------------------------------------------------------------

    def build_grid(self, ask_up: float,
                   ask_down: float) -> Tuple[GridSide, GridSide]:
        """Both ladders. The budget is split EVENLY, never by price.

        Splitting by price would put more capital on the cheaper side, which is
        the side the market thinks is losing. That is a directional bet dressed
        as a hedge, and the whole point of the structure is that it is not one.
        """
        half = float(self.grid_budget_usdc) / 2.0
        return (
            build_grid_side('Up', ask_up, half, self.grid_levels,
                            self.grid_spacing, self.min_rung_price,
                            self.min_shares),
            build_grid_side('Down', ask_down, half, self.grid_levels,
                            self.grid_spacing, self.min_rung_price,
                            self.min_shares),
        )

    @staticmethod
    def legs_for(sides: Iterable[GridSide]) -> List[Leg]:
        """Every rung of every side, as maker Legs, nearest-the-ask first."""
        legs: List[Leg] = []
        for side in sides:
            for rung in side.rungs:
                legs.append(Leg(outcome_side=side.outcome_side,
                                limit_price=rung.price,
                                order_type='maker',
                                shares=rung.shares))
        return legs

    # -- decision -----------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        """Decide one window. ALWAYS returns a Decision, and NEVER an ENTER."""
        slug = getattr(ctx.market, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            assert_not_enter(action)
            feats.setdefault('paper_mode', self.paper_mode)
            feats.setdefault('uses_maker_orders', True)
            feats.setdefault('fill_model', 'maker_fills_not_simulated')
            feats.setdefault('blocked_by_refusal_not_by_bug', True)
            feats.setdefault('kill_condition_is_currently_unmeasurable', True)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market')

        # 1. BOOKS. Missing one side is NOT the same fact as missing both.
        book_up = ctx.book('Up')
        book_down = ctx.book('Down')
        if book_up is None and book_down is None:
            return decide('SKIP', 'both_books_unavailable')
        if book_up is None or book_down is None:
            # A one-sided grid is a directional ladder. The self-hedge that
            # makes this structure interesting is the OTHER side filling on the
            # reversal, and half a grid does not have one.
            return decide('SKIP', 'one_book_unavailable',
                          has_book_up=book_up is not None,
                          has_book_down=book_down is not None)

        ask_up, ask_down = book_up.best_ask, book_down.best_ask
        bid_up, bid_down = book_up.best_bid, book_down.best_bid
        feats: Dict[str, object] = {
            'ask_up': ask_up, 'ask_down': ask_down,
            'bid_up': bid_up, 'bid_down': bid_down,
        }
        if ask_up is None or ask_down is None:
            return decide('SKIP', 'no_asks', **feats)

        # 2. SPREAD. A spread we cannot measure is not a narrow spread.
        if bid_up is None or bid_down is None:
            return decide('SKIP', 'spread_undefined_no_bid', **feats)
        spread_up = ask_up - bid_up
        spread_down = ask_down - bid_down
        feats.update({'spread_up': round(spread_up, 4),
                      'spread_down': round(spread_down, 4),
                      'max_spread': self.max_spread,
                      'grid_spacing': self.grid_spacing})
        if max(spread_up, spread_down) > self.max_spread + 1e-12:
            # The spread eats the rung interval before the grid does any work.
            return decide('SKIP', 'spread_too_wide_for_grid', **feats)

        # 3. DEPTH. A proxy for "this book is alive", not for "our bid fills".
        band = self.grid_spacing * self.grid_levels
        depth_up = book_up.ask_depth(ask_up + band)
        depth_down = book_down.ask_depth(ask_down + band)
        feats.update({'grid_band': round(band, 4),
                      'ask_depth_up': depth_up, 'ask_depth_down': depth_down,
                      'min_grid_depth_shares': self.min_grid_depth_shares,
                      'depth_is_a_liveness_proxy_not_a_fill_model': True})
        if min(depth_up, depth_down) < self.min_grid_depth_shares:
            return decide('SKIP', 'book_too_thin_for_grid', **feats)

        # 4. THE LADDER. Built BEFORE the vol gates so every skip below still
        # carries the full grid a fill model would need.
        side_up, side_down = self.build_grid(ask_up, ask_down)
        feats.update({
            'grid_levels': self.grid_levels,
            'min_rung_price': self.min_rung_price,
            'grid_budget_usdc': self.grid_budget_usdc,
            'grid_up': side_up.to_dict(),
            'grid_down': side_down.to_dict(),
            'total_rungs': len(side_up.rungs) + len(side_down.rungs),
            'total_allocated_usdc': round(
                side_up.allocated_usdc + side_down.allocated_usdc, 6),
            'total_unallocated_usdc': round(
                side_up.unallocated_usdc + side_down.unallocated_usdc, 6),
            'max_loss_usdc': round(
                side_up.max_loss_usdc + side_down.max_loss_usdc, 6),
            'stop_is_zero_because_a_losing_binary_is_worth_zero': True,
        })
        if not side_up.rungs and not side_down.rungs:
            # Nothing survived the floor and the minimum size. The budget could
            # not buy a grid, which is a cannot-run, not a market view.
            return decide('SKIP', 'grid_budget_exhausted', **feats)

        # 5. THE VOL CLAIM. Two legs, two separate missing-input reasons.
        feats['atr14_bps'] = ctx.atr14
        feats['lead_bps'] = ctx.lead_bps
        feats['atr14_must_be_in_bps'] = True
        if ctx.atr14 is None:
            # The REALISED leg. NOT_TESTED, not "realised was low".
            return decide('SKIP', 'vol_inputs_unavailable',
                          missing_input='atr14', **feats)
        if ctx.lead_bps is None:
            # The IMPLIED leg. Different owner, different fix.
            return decide('SKIP', 'implied_vol_inputs_unavailable',
                          missing_input='lead_bps', **feats)

        remaining = ctx.seconds_remaining
        feats['seconds_remaining'] = remaining
        if remaining is None:
            # Realised has to be scaled to the time left. Without a clock the
            # comparison is between two different horizons, which is not a
            # comparison.
            return decide('SKIP', 'no_window_clock', **feats)

        p_up = book_up.midpoint if book_up.midpoint is not None else ask_up
        feats['p_up_used'] = round(float(p_up), 6)
        feats['p_up_source'] = ('midpoint' if book_up.midpoint is not None
                                else 'best_ask')
        implied, implied_status = implied_sigma_bps(ctx.lead_bps, p_up)
        feats['implied_sigma_bps'] = (None if implied is None
                                      else round(implied, 3))
        feats['implied_vol_status'] = implied_status
        if implied is None:
            return decide('SKIP', implied_status, **feats)

        realized = realized_sigma_bps(ctx.atr14, remaining)
        feats.update({
            'realized_sigma_bps': round(realized, 3),
            'realized_sigma_assumes_normal_and_sqrt_time': True,
            'implied_vs_realized_bps': round(implied - realized, 3),
            'implied_vol_exceeds_realized': implied > realized,
            'confidence': 0.5,
            'confidence_is_a_placeholder_no_fill_model_exists': True,
        })
        if implied <= realized:
            # Computed both, and the claim is false right now. A RESULT, and the
            # only reason in this file that is neither a cannot-run nor the
            # refusal.
            return decide('SKIP', 'implied_vol_below_realized', **feats)

        # 6. QUOTE. Never ENTER. See the module docstring: simulating these
        # rungs as taker lifts would manufacture the strategy's entire P&L.
        legs = self.legs_for((side_up, side_down))
        feats['leg_count'] = len(legs)
        feats['grid_pnl_helper'] = 'strategies.polymarket.grid_hedge.grid_pnl'
        feats['kill_condition'] = 'grid_pnl net_usdc < -5.00 over 50 fills'
        feats['kill_condition_blocked_by'] = 'maker_fills_not_simulated'
        # A maker fill model now EXISTS in the paper adapter
        # (`simulate_maker_buy`, strict cross minus the queue that was ahead of
        # us). These three keys are the whole wiring change: they tell a
        # consumer these rungs are restable and name the verb and the rule.
        #
        # `kill_condition_blocked_by` above is deliberately NOT cleared. The
        # kill condition needs 50 grid FILLS, and a fill model existing is not
        # 50 fills. It stays blocked until the shadow loop rests these rungs and
        # the adapter reports them filled - convention 11, and convention 22: a
        # capability in a docstring is not a wired capability.
        feats['maker_fill_model_available'] = MAKER_FILL_MODEL
        feats['maker_quote_is_restable'] = True
        feats['maker_rest_verb'] = ('engine.polymarket.paper_adapter'
                                    '.PolymarketPaperAdapter.simulate_maker_buy')
        return decide('QUOTE', 'maker_fill_not_simulated', legs=legs, **feats)
