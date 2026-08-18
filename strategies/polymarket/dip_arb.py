"""Dip Arb: buy an outcome trading well below its OWN recent average price.

Concept from MrFadiAi's public Polymarket bot. The concept is all that is
borrowed; every constant below is ours and every one of them is an assumption
with an expiry date (convention 17).

    rolling mean of the outcome's own traded price   (a HISTORICAL number)
    current effective ask more than DIP_THRESHOLD below it
        -> buy the dip
    bid returns to the rolling mean
        -> SELL, before resolution

## THIS IS NOT fair_value_arb, AND THE DIFFERENCE IS THE WHOLE POINT

`PM_fair_value_arb` builds a probability for the outcome from a model of BTC's
move: spot versus the window open, time remaining, realized volatility,
diffusion. It compares the book to something computed OUTSIDE the book.

This strategy does no such thing. Its reference is the arithmetic mean of the
outcome's own recent quoted price. It compares the book to the book's own past.
That makes it a completely different bet and it must never be pooled with the
fair-value family for scoring, even though it shares their exit machinery.

## WHY IT CAN BE WRONG, stated rather than hidden

On a binary whose true probability is genuinely moving, a historical mean is a
LAGGING estimate. Consider an outcome that traded at 0.60 for four minutes and
is now offered at 0.50. Two stories fit that tape exactly:

    1. a thin book, one impatient seller, and 0.60 is still the right price
    2. BTC moved, the outcome really is worth 0.50 now, and the mean is stale

**From price alone these are indistinguishable.** Nothing in this file can tell
them apart, because the only input is the price series that both stories
produce. Story 1 is the trade. Story 2 is buying a falling knife and holding it
into a resolution that pays 0.00. That is the core risk of this strategy, it is
structural rather than a tuning problem, and no threshold in this file fixes it.

Two partial mitigations, both named so nobody mistakes them for a solution:

  - `mean_collapsed_to_entry` exits when the rolling mean itself falls to our
    entry. That is story 2 revealing itself: the reference we bought against
    has come to us instead of us going to it. It cuts the loss; it does not
    prevent it, and it fires LATE by construction because a mean lags.
  - The tradeable band refuses means below 0.10 and above 0.90, where a 10%
    relative dip is inside the tick grid and the exit model stops working.

## THE TAPE IS SHORT, AND THAT IS A HARD LIMIT ON THIS VENUE

Token ids on the BTC Up/Down 5-minute markets are new every window. So a token's
tape starts EMPTY at every window open and there is no cross-window history to
inherit. With the shadow loop polling every few seconds:

    MIN_OBSERVATIONS = 20  at a ~5s poll  ->  roughly 100 seconds of tape
    MIN_ENTRY_SECONDS_REMAINING = 60      ->  no new entry after ~240 seconds

so on a 5-minute market this strategy can only fire in roughly the middle third
of a window, and its "historical average" spans a couple of minutes rather than
days. That is not a bug and it is not tuned away by lowering MIN_OBSERVATIONS:
a mean over 5 observations is a number, not an average, and it would fire this
strategy on poll jitter. The honest description is that the reference here is a
SHORT-RUN mean, and any result must be read as a statement about short-run mean
reversion inside one window.

## SCORING: this strategy is in the SELL population, never the RESOLUTION one

It exits before resolution. Its positions close with a few cents of PnL, not
with a 1.00-or-0.00 payoff. Pooling it with the resolution strategies produces a
win rate that describes neither population, which is exactly the split
`PolymarketPaperAdapter.summary()` reports `by_exit_kind` for. It shares that
property with the fair-value family and with nothing else in this package, and
sharing an exit SHAPE is still not a reason to pool it with them: their
reference price is a model and this one's is a moving average.

## WHAT THIS STRATEGY CANNOT SEE (convention 22)

  - **Who moved the price.** One 500-share seller and fifty 10-share sellers
    produce the same dip and mean opposite things.
  - **Whether its own Decision became a fill.** The halt check, the risk gate
    and the paper adapter all sit downstream and any of them can refuse.
  - **Trade prints.** The tape here is built from QUOTES (midpoint, or the ask
    when only one side is quoting), not from executions, because the CLOB book
    is what the loop already has on every cycle. A mean of quotes is not a mean
    of trades; every observation records which source it came from and every
    decision reports the mix, so a result computed off a one-sided book can
    never be mistaken for one computed off midpoints.

## EXITS ARE ALL-OR-NOTHING, and an unsellable position resolves

`PolymarketPaperAdapter.simulate_taker_sell` refuses a partial fill. If the bid
side cannot absorb the full position under our limit, the sell does not happen
and THE POSITION STAYS OPEN. If it is still open when the oracle speaks, it
resolves like any other binary and its full PnL is charged here. `manage_exit`
names that case `no_bid_liquidity` and stamps `unsellable=True` rather than
letting it look like a patient hold.

KILL CONDITION: trailing-30 win rate below 45%, once 30 closed trades exist,
scored by `backtest/polymarket_harness.py` on the `PM_dip_arb` population alone
and on CLOSED trades only, never pooled with resolution trades. 45% is below
the instance's own `breakeven_win_rate` on purpose: the property is the
worst-case break-even computed from the floor target, while the realised target
is the rolling mean, which sits further above entry than the floor whenever the
entry gate did its job. A strategy killed at its own break-even would be killed
by its most conservative number rather than by its results. Convention 7 cuts
both ways here: 30 trades is a thin sample, so a FAIL at 30 is a flag to stop
allocating, not a verdict on mean reversion.
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from strategies.polymarket.base import (GENERAL_BINARY_MARKET_TYPES,
                                        MARKET_TYPE_CRYPTO_UPDOWN,
                                        WINDOW_SECONDS, Decision, Leg,
                                        MarketContext, PolymarketStrategy,
                                        effective_ask_for,
                                        effective_stop_distance,
                                        tiered_stop_features,
                                        tiered_stop_price)
from strategies.polymarket.fair_value_arb import (URGENT_SELL_LIMIT,
                                                  ExitDecision, floor_to_tick)

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

# --- the tape ---------------------------------------------------------------

#: Observations kept per token. 60 at a ~5s poll is one full 5-minute window,
#: which is all the history a 5m token can ever have (see the docstring).
TAPE_LEN = 60

#: Observations older than this are pruned regardless of TAPE_LEN. Belt and
#: braces: TAPE_LEN alone would let a stalled loop keep 60 samples from ten
#: minutes ago and call their mean a recent average.
TAPE_MAX_AGE_SEC = 900.0

#: Fewest observations before a mean is quoted at all. Below it the answer is
#: `insufficient_tape`, which means CANNOT MEASURE, not "no dip found"
#: (convention 11).
MIN_OBSERVATIONS = 20

# --- entry ------------------------------------------------------------------

#: RELATIVE, not absolute: the ask must be below `mean * (1 - DIP_THRESHOLD)`.
#: 10% of a 0.50 mean is 5c; 10% of a 0.15 mean is 1.5c, which is why the
#: tradeable band exists. Stated explicitly because "10% below" reads as 10
#: cents to about half the people who see it, and on a binary those are
#: different strategies.
DIP_THRESHOLD = 0.10

#: Floor on the sell limit at the profit target. NOT a cap on the fill, and NOT
#: the target: the target is the rolling mean, which is normally further away.
MIN_PROFIT = 0.02

#: NOMINAL payoff geometry ONLY. **This is no longer the stop.**
#:
#: The stop is `strategies.polymarket.base.tiered_stop_price`, shared with the
#: fair-value family and defined in exactly one place. This constant survives
#: only as the input to `breakeven_win_rate`, the WORST-CASE property the
#: docstring's kill condition is stated against. Its old role was worse here
#: than anywhere else in the package: `PM_dip_arb`'s cheapest recorded fill is
#: 0.0200, and `0.0200 - 0.05` is negative, so this strategy has been booking
#: entries whose declared 5c stop was unreachable by construction.
MAX_LOSS = 0.05

#: The rolling mean falling to within this of entry is `mean_collapsed_to_entry`
#: - story 2 from the docstring revealing itself.
MEAN_STOP_MARGIN = 0.01

#: The bid coming within this of the mean counts as reversion complete. One
#: tick: waiting for an exact touch on a 1c grid is waiting for a coincidence.
MEAN_REVERSION_EPS = 0.01

#: Seconds after entry at which an unresolved thesis is closed out.
TIME_STOP_SEC = 120.0

#: Under this much window left, close regardless. Past here the position is a
#: directional bet on the resolution, which is a different strategy.
WINDOW_CLOSE_EXIT_SEC = 30.0

#: No new entry with less than this left. Derived, not tuned: an entry needs
#: room for a reversion to happen before the close-out fires.
MIN_ENTRY_SECONDS_REMAINING = 60.0

#: Entry attempts per 5-minute window. Attempts, not fills.
MAX_TRADES_PER_WINDOW = 2

#: Target size, in shares.
TARGET_SHARES = 20

#: Per-trade notional. Matches PolymarketPaperAdapter.notional_cap_usdc and
#: PolymarketRiskGate.DEFAULT_NOTIONAL_CAP_USDC; restated so a size computed
#: here cannot silently exceed a cap enforced somewhere else.
MAX_NOTIONAL_USDC = 10.0

#: Exchange minimum order size, in shares.
MIN_SHARES = 5

#: Shares that must rest within DEPTH_BAND of the best ask. A 10% dip against a
#: 6-share top level is one stale quote, not an opportunity.
MIN_BOOK_DEPTH_SHARES = 50
DEPTH_BAND = 0.03

#: Tradeable band for the rolling MEAN. Below 0.10 a 10% relative dip is 1c,
#: which is one tick and therefore noise, and there is no room beneath entry for
#: MAX_LOSS to mean anything. Above 0.90 this becomes near-resolution capture,
#: which needs its own position limits and data-quality kill switch, neither of
#: which is built.
MIN_TRADEABLE_MEAN = 0.10
MAX_TRADEABLE_MEAN = 0.90

#: Windows of per-window attempt state kept.
STATE_WINDOWS_KEPT = 8

#: Outcome names looked up on every cycle. Both vocabularies, because the BTC
#: 5m markets label their outcomes Up/Down and other Polymarket markets use
#: Yes/No. A name the market does not carry resolves to no token and is dropped
#: without a candidate, which is not the same as a token with no book.
CANDIDATE_SIDES = ('Up', 'Down', 'Yes', 'No')

#: Where an observation's price came from. Kept separate on purpose: a midpoint
#: and a lone ask are different statistics and pooling them into one mean would
#: make a one-sided book look like a two-sided one.
SOURCE_MID = 'mid'
SOURCE_ASK = 'ask'


def token_id_for(market, side: str) -> Optional[str]:
    """`market.token_id(side)` that refuses to raise on a malformed market.

    `evaluate` must return a Decision on EVERY path, including the one where
    the context carries something that is not a Market at all. Raising here
    would take out a whole shadow-loop cycle over one bad field, and a cycle
    that raised is a cycle nobody counted (convention 20).
    """
    lookup = getattr(market, 'token_id', None)
    if not callable(lookup):
        return None
    try:
        token = lookup(side)
    except Exception:                                   # noqa: BLE001
        return None
    return token if token else None


@dataclass(frozen=True)
class Observation:
    """One price observation for one token."""

    ts: float
    price: float
    source: str


class PriceTapeByToken:
    """Per-token rolling record of quoted prices.

    One tape per token id, because a Polymarket outcome's price series is its
    own; averaging Up and Down together would produce a mean of about 0.50
    forever and a dip signal that fires on whichever side happens to be cheap.

    Refuses non-finite prices, prices outside (0, 1], and out-of-order
    timestamps, and COUNTS each refusal. A tape that silently reorders itself
    makes a stale read look like a fresh one; a tape that silently drops rows
    makes a broken feed look like a quiet market (convention 20).
    """

    def __init__(self, max_len: int = TAPE_LEN,
                 max_age_sec: float = TAPE_MAX_AGE_SEC):
        self.max_len = int(max_len)
        self.max_age_sec = float(max_age_sec)
        self.tapes: Dict[str, List[Observation]] = {}
        self.drops: Dict[str, int] = {}

    def _drop(self, reason: str) -> bool:
        self.drops[reason] = self.drops.get(reason, 0) + 1
        return False

    def observe(self, token_id: str, ts, price, source: str) -> bool:
        """Record one observation. Returns False if it was refused."""
        if not token_id:
            return self._drop('no_token_id')
        try:
            ts_f = float(ts)
            price_f = float(price)
        except (TypeError, ValueError):
            return self._drop('unparseable')
        if not (math.isfinite(ts_f) and math.isfinite(price_f)):
            return self._drop('non_finite')
        if not (0.0 < price_f <= 1.0):
            # A binary price outside (0, 1] is a corrupt field, not an extreme
            # quote. 0.0 exactly is excluded: it would drag a mean toward zero
            # and it is never a real ask.
            return self._drop('price_out_of_range')

        tape = self.tapes.setdefault(str(token_id), [])
        if tape and ts_f < tape[-1].ts:
            return self._drop('out_of_order')

        tape.append(Observation(ts_f, price_f, source))
        cutoff = ts_f - self.max_age_sec
        if tape[0].ts < cutoff:
            tape = [o for o in tape if o.ts >= cutoff]
        if len(tape) > self.max_len:
            tape = tape[-self.max_len:]
        self.tapes[str(token_id)] = tape
        return True

    def observations(self, token_id: Optional[str]) -> List[Observation]:
        if not token_id:
            return []
        return list(self.tapes.get(str(token_id), ()))

    def count(self, token_id: Optional[str]) -> int:
        return len(self.observations(token_id))

    def mean(self, token_id: Optional[str],
             min_observations: int = MIN_OBSERVATIONS) -> Optional[float]:
        """Arithmetic mean of the token's tape, or None.

        None means CANNOT MEASURE. A mean over fewer than `min_observations`
        samples is not a short-run average, it is a couple of quotes, and
        returning it as one would fire this strategy on poll jitter.
        """
        obs = self.observations(token_id)
        if len(obs) < min_observations:
            return None
        return sum(o.price for o in obs) / len(obs)

    def source_mix(self, token_id: Optional[str]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for o in self.observations(token_id):
            out[o.source] = out.get(o.source, 0) + 1
        return out


def reference_price(book) -> Tuple[Optional[float], Optional[str]]:
    """Midpoint when both sides quote, else the best ask. `(price, source)`.

    A one-sided book has no midpoint, and skipping it entirely would put holes
    in the tape exactly where the market was thin - which is where the dips are.
    So the ask is used and the source is recorded, never merged with the
    midpoint observations.
    """
    if book is None:
        return None, None
    mid = book.midpoint
    if mid is not None:
        return mid, SOURCE_MID
    ask = book.best_ask
    if ask is not None:
        return ask, SOURCE_ASK
    return None, None


# --- the exit-side estimate --------------------------------------------------

#: Reasons `estimate()` can return. Every distinct CAUSE gets its own string and
#: two causes never share one (convention 20). `no_market` and
#: `no_outcome_tokens` are different faults: the first is "there was no market
#: on the context at all", the second is "there was one and it carried none of
#: the outcome names we know". `insufficient_tape` is CANNOT MEASURE
#: (convention 11), never "no dip found"; it deliberately reuses `evaluate`'s
#: spelling because it is the same underlying fact, and the two live in
#: different places (a Decision reason vs an estimate reason) that nothing
#: pools into one counter.
EST_NO_CONTEXT = 'no_context'
EST_NO_MARKET = 'no_market'
EST_NO_OUTCOME_TOKENS = 'no_outcome_tokens'
EST_INSUFFICIENT_TAPE = 'insufficient_tape'
EST_PER_TOKEN_MEAN = 'reference_is_per_token_not_per_window'

#: Outcome labels `TapeMeanEstimate.for_side` will answer for, lowercased. The
#: same four this strategy builds candidates from. Anything else RAISES rather
#: than resolving to a direction, matching `FairValueEstimate.for_side`.
KNOWN_SIDE_LABELS = {s.lower(): s for s in CANDIDATE_SIDES}


@dataclass(frozen=True)
class TapeMeanEstimate:
    """What `DipArb.estimate(ctx)` hands the shadow loop. Never usable, on
    purpose, and the reason is the interesting part.

    ## The contract this satisfies

    `PolymarketShadowLoop.manage_exits` calls `strategy.estimate(ctx)` once per
    (asset, strategy) per cycle for every strategy with `manages_exits = True`,
    and then, per position:

        if est is not None and est.usable and pos.window_ts == ctx.window_ts:
            fair = est.for_side(pos.outcome_side)     # ValueError is counted
        decision = strategy.manage_exit(pos, book, now, fair_value=fair)

    So the object needs exactly two things: a `usable` flag and a `for_side`
    that returns one float for one outcome name and raises on a name it does
    not recognise. `FairValueEstimate` is the other implementation of that
    contract; this is not a subclass of it because
    `FairValueEstimate.for_side` derives the Down value as `1 - p`, and two
    tape means are NOT mirror images of each other - they are two independent
    quote series that happen to be about the same event.

    ## Why `usable` is always False

    DipArb's reference is the arithmetic mean of ONE TOKEN's own quote tape,
    and that tape lives on this same object. So there is nothing here for a
    caller to carry that `manage_exit` cannot already read for itself, and
    supplying it anyway can only be neutral or wrong:

      - **Neutral**, in every case where the loop would actually use it. The
        loop only reads the estimate when `pos.window_ts == ctx.window_ts`, and
        it routes positions to the instance for the asset in their own slug, so
        the position's token IS the context market's token for that side. The
        number handed over would be identical to the one `manage_exit` computes
        from `self.mean_for(pos.token_id)` on the fallback path, because it
        comes from the same tape one line earlier.
      - **Wrong**, in any case where those two tokens disagree. Then we would
        be handing `manage_exit` some OTHER token's mean, and the position
        would be stopped out against a reference it was never bought against.
        That failure is invisible in the logs: the row would read
        `mean_collapsed_to_entry` exactly like a real one.

    Zero upside and a silent wrong exit as the downside, so this under-claims:
    `usable=False`, `manage_exit` falls back to its own tape and stamps
    `rolling_mean_source='own_tape'`, and the exception that used to be thrown
    at that call site (there was no `estimate` at all) stops.

    The per-side means are still carried, as DATA. They are what an operator or
    a test wants to see; they are just not something this strategy asks the
    loop to trade on.
    """

    usable: bool
    reason: str
    window_ts: Optional[int] = None
    min_observations: int = MIN_OBSERVATIONS
    #: side -> rolling mean, or None where the tape is not deep enough. Only
    #: sides the market actually carries a token for appear here; a side the
    #: market does not have is absent rather than present-and-None, because
    #: "this market has no Yes outcome" and "the Yes tape is cold" are
    #: different facts.
    means_by_side: Dict[str, Optional[float]] = None
    observations_by_side: Dict[str, int] = None
    tokens_by_side: Dict[str, str] = None

    def __post_init__(self):
        # frozen=True, so the defaults have to be installed this way. Written
        # out rather than using field(default_factory=dict) so the three stay
        # visibly parallel.
        for name in ('means_by_side', 'observations_by_side', 'tokens_by_side'):
            if getattr(self, name) is None:
                object.__setattr__(self, name, {})

    def for_side(self, outcome_side: str) -> Optional[float]:
        """Rolling mean for one outcome name, or None. Unknown names RAISE.

        None means "no mean for that side": either the market carries no such
        outcome or its tape is below `min_observations`. `manage_exit` treats a
        None `fair_value` as "read your own tape", which lands on the same
        answer, so None is safe to return and safe to ignore.

        A name outside the four this strategy knows raises `ValueError`, the
        same way `FairValueEstimate.for_side` does, and the loop counts it as
        `exit_unknown_outcome_side`. Guessing that an unrecognised label means
        Up is how a position gets managed against the other side's number.
        """
        key = (outcome_side or '').strip().lower()
        if key not in KNOWN_SIDE_LABELS:
            raise ValueError(
                'unknown outcome side {!r}; refusing to guess a direction'
                .format(outcome_side))
        return self.means_by_side.get(KNOWN_SIDE_LABELS[key])

    def to_dict(self) -> dict:
        return {
            'usable': self.usable,
            'reason': self.reason,
            'window_ts': self.window_ts,
            'min_observations': self.min_observations,
            'means_by_side': {k: (None if v is None else round(v, 6))
                              for k, v in self.means_by_side.items()},
            'observations_by_side': dict(self.observations_by_side),
            'reference_is_per_token_not_per_window': True,
        }


class DipArb(PolymarketStrategy):
    """Buy an outcome offered well below its own short-run mean; sell on the
    return to that mean.

    Kill condition: trailing-30 win rate below 45% once 30 closed trades exist,
    scored by `backtest/polymarket_harness.py` on the `PM_dip_arb` population
    alone, CLOSED trades only. See the module docstring.
    """

    strategy_name = 'PM_dip_arb'
    paper_mode = PAPER_MODE

    #: This strategy manages its own exits. The shadow loop reads this flag to
    #: decide whether to poll `manage_exit` for a position.
    manages_exits = True

    #: CRYPTO PLUS EVERY GENERAL BINARY, and the reason is structural rather
    #: than optimistic.
    #:
    #: This strategy reads THE OUTCOME'S OWN PRICE TAPE and nothing else. Grep
    #: `evaluate`: it touches `ctx.market`, `ctx.books` and the clock, and it
    #: touches `ctx.spot`, `ctx.strike`, `ctx.windows` and `ctx.atr14` exactly
    #: never. `reference_price` reads a midpoint off an orderbook. That is the
    #: entire input set, and every binary market on Polymarket has a book. So
    #: unlike `fair_value_arb` - which is a probability model OF A CRYPTO PRICE
    #: MOVE and is meaningless without a spot - this one is genuinely
    #: market-agnostic. `CANDIDATE_SIDES` already carries Yes/No alongside
    #: Up/Down for exactly this reason.
    #:
    #: AND THE MODULE DOCSTRING'S HARDEST LIMIT IS A CRYPTO LIMIT, NOT A
    #: STRATEGY LIMIT. "THE TAPE IS SHORT, AND THAT IS A HARD LIMIT ON THIS
    #: VENUE" is a statement about the BTC Up/Down 5-minute markets: their token
    #: ids are new every window, so a tape starts EMPTY at every window open,
    #: `MIN_OBSERVATIONS = 20` at a ~5s poll eats ~100 seconds of it, and what
    #: is left is a "historical average" spanning a couple of minutes. On an
    #: event, sports or political market the token id lives for DAYS. The tape
    #: is continuous across polls, `TAPE_MAX_AGE_SEC` (900s) rather than the
    #: window becomes the binding horizon, and the mean is finally an average of
    #: a market rather than of one window's noise. That is a genuine improvement
    #: in the quality of the reference price, and it is the strongest argument
    #: for widening this declaration at all.
    #:
    #: WHAT IT IS NOT. It is not evidence. Nothing has scored a single dip_arb
    #: trade on a non-crypto market, the mean-reversion hypothesis on a
    #: multi-day event book is a DIFFERENT hypothesis from the one on a
    #: five-minute window, and the two populations must be scored apart rather
    #: than pooled (convention 7, and the same no-pooling rule the module
    #: docstring already applies against the fair-value family).
    supported_market_types = ((MARKET_TYPE_CRYPTO_UPDOWN,)
                              + GENERAL_BINARY_MARKET_TYPES)

    def __init__(self, dip_threshold: float = DIP_THRESHOLD,
                 min_observations: int = MIN_OBSERVATIONS,
                 tape_len: int = TAPE_LEN,
                 tape_max_age_sec: float = TAPE_MAX_AGE_SEC,
                 min_profit: float = MIN_PROFIT,
                 max_loss: float = MAX_LOSS,
                 mean_stop_margin: float = MEAN_STOP_MARGIN,
                 mean_reversion_eps: float = MEAN_REVERSION_EPS,
                 time_stop_sec: float = TIME_STOP_SEC,
                 window_close_exit_sec: float = WINDOW_CLOSE_EXIT_SEC,
                 min_entry_seconds_remaining: float = MIN_ENTRY_SECONDS_REMAINING,
                 max_trades_per_window: int = MAX_TRADES_PER_WINDOW,
                 target_shares: int = TARGET_SHARES,
                 max_notional_usdc: float = MAX_NOTIONAL_USDC,
                 min_shares: int = MIN_SHARES,
                 min_book_depth_shares: float = MIN_BOOK_DEPTH_SHARES,
                 depth_band: float = DEPTH_BAND,
                 min_mean: float = MIN_TRADEABLE_MEAN,
                 max_mean: float = MAX_TRADEABLE_MEAN):
        self.dip_threshold = dip_threshold
        self.min_observations = min_observations
        self.min_profit = min_profit
        self.max_loss = max_loss
        self.mean_stop_margin = mean_stop_margin
        self.mean_reversion_eps = mean_reversion_eps
        self.time_stop_sec = time_stop_sec
        self.window_close_exit_sec = window_close_exit_sec
        self.min_entry_seconds_remaining = min_entry_seconds_remaining
        self.max_trades_per_window = max_trades_per_window
        self.target_shares = target_shares
        self.max_notional_usdc = max_notional_usdc
        self.min_shares = min_shares
        self.min_book_depth_shares = min_book_depth_shares
        self.depth_band = depth_band
        self.min_mean = min_mean
        self.max_mean = max_mean

        self.tape = PriceTapeByToken(max_len=tape_len,
                                     max_age_sec=tape_max_age_sec)
        #: window_ts -> entry ATTEMPTS. Not fills: the halt check, the risk gate
        #: and the paper adapter all sit downstream and any of them can refuse.
        self._window_trades: Dict[int, int] = {}

    # -- the number the family compares on ----------------------------------

    @property
    def breakeven_win_rate(self) -> float:
        """`max_loss / (min_profit + max_loss)`, computed from the INSTANCE.

        Computed, never written down: a constant restating 0.714 would go stale
        the first time somebody constructs this class with a different
        `max_loss` and would then be quoted as if it had been measured
        (convention 22 - a docstring is not a wiring test).

        This is the WORST CASE for this strategy, not the expected case. It
        assumes every winner exits at the MIN_PROFIT floor, while the actual
        target is the rolling mean, which the entry gate puts further above
        entry than the floor. The realised per-trade figure is stamped on each
        decision row as `breakeven_win_rate_this_trade`. Comparing this number
        against a fair-value variant's is comparing two different questions.
        """
        denom = self.min_profit + self.max_loss
        return float('nan') if denom <= 0 else self.max_loss / denom

    # -- the stop -----------------------------------------------------------
    #
    # Delegated to `strategies.polymarket.base`, the same helper the five
    # fair-value strategies use. Two thin methods, no arithmetic, so a wiring
    # test can prove this strategy reaches the shared rule and not a copy of it
    # (convention 22). Sharing the STOP rule is not pooling the POPULATIONS:
    # this strategy's reference is a tape mean and the family's is a model, and
    # the module docstring's no-pooling rule is untouched.

    @staticmethod
    def stop_price_for(entry_px: float,
                       side: Optional[str] = None) -> float:
        """The tiered stop price for a fill at `entry_px`. See base."""
        return tiered_stop_price(entry_px, side)

    @staticmethod
    def stop_distance_for(entry_px: float,
                          side: Optional[str] = None) -> float:
        """`entry_px - stop_price_for(...)`: the loss the stop admits."""
        return effective_stop_distance(entry_px, side)

    @staticmethod
    def breakeven_win_rate_for(entry: float, target: float,
                               stop: float) -> Optional[float]:
        """Break-even for one trade's actual target and stop, or None.

        `loss / (gain + loss)`. None when either leg is non-positive, because a
        target at or below entry is not a target and a break-even computed from
        it would be a number with no meaning attached.
        """
        gain = target - entry
        loss = entry - stop
        if gain <= 0 or loss <= 0:
            return None
        return loss / (gain + loss)

    # -- per-window state ---------------------------------------------------

    def trades_this_window(self, window_ts: int) -> int:
        return self._window_trades.get(window_ts, 0)

    def _note_attempt(self, window_ts: int) -> int:
        n = self._window_trades.get(window_ts, 0) + 1
        self._window_trades[window_ts] = n
        if len(self._window_trades) > STATE_WINDOWS_KEPT:
            for ts in sorted(self._window_trades)[:-STATE_WINDOWS_KEPT]:
                self._window_trades.pop(ts, None)
        return n

    # -- context helpers ----------------------------------------------------

    @staticmethod
    def clock(ctx: MarketContext) -> Optional[float]:
        """Absolute seconds for this observation, or None.

        Derived from the window's own timestamp rather than the wall clock, so
        a decision is reproducible from a logged context and a test does not
        have to mock `time`.
        """
        if ctx.seconds_into_window is None:
            return None
        return float(ctx.window_ts) + float(ctx.seconds_into_window)

    def observe(self, ctx: MarketContext) -> Dict[str, bool]:
        """Record this cycle's quote for every token with a book.

        Returns token_id -> accepted. Called on EVERY cycle including the ones
        that skip: a tape that only fills on tradeable cycles has holes exactly
        where the market was quiet, and quiet is where the mean comes from.
        """
        out: Dict[str, bool] = {}
        now = self.clock(ctx)
        if now is None or ctx.market is None:
            return out
        for outcome in getattr(ctx.market, 'outcomes', ()) or ():
            token = getattr(outcome, 'token_id', None)
            if not token:
                continue
            price, source = reference_price(ctx.books.get(token))
            if price is None:
                continue
            out[token] = self.tape.observe(token, now, price, source)
        return out

    def mean_for(self, token_id: Optional[str]) -> Optional[float]:
        return self.tape.mean(token_id, self.min_observations)

    def estimate(self, ctx: MarketContext) -> TapeMeanEstimate:
        """The shadow loop's per-cycle reference call. ALWAYS returns; never
        usable; never touches the tape.

        `manages_exits = True` obliges this method to exist. Before it did, the
        loop's `strategy.estimate(ctx_a)` raised `AttributeError` on EVERY
        cycle, which its `try/except` swallowed into
        `health['exit_fair_value_exceptions']` plus a warning line - a health
        counter that exists to catch real model failures, permanently pinned by
        a missing method.

        Three properties, each load-bearing:

          1. **READ-ONLY.** `manage_exits` is phase 2 of `run_cycle` and
             `evaluate` is phase 3, so this runs BEFORE this cycle's quote has
             been observed. `PriceTapeByToken.observe` only rejects timestamps
             that go BACKWARDS, so recording here would happily add a second
             copy of the same quote at the same timestamp and quietly weight it
             double in the mean. Nothing in here writes.
          2. **Never usable.** See `TapeMeanEstimate` - supplying a per-window
             scalar for a per-token mean is neutral at best and a silently
             wrong exit at worst, so it under-claims and lets `manage_exit`
             read its own tape.
          3. **Never raises.** Same reason `token_id_for` does not: a strategy
             that throws here is caught and counted, but a counted exception is
             a cycle nobody can read.

        The reason strings distinguish the causes that look alike from the
        outside. A fresh instance is cold for its first `min_observations`
        cycles (~100s at a 5s poll) and says `insufficient_tape`; a warm one
        says `reference_is_per_token_not_per_window`. Those are a startup
        condition and a permanent design fact, and one number for both would
        make the first invisible.

        ## RESOLVED by D-300, 2026-08-18: this method is the surviving fix

        The loop was fixed from the other end too. `PolymarketShadowLoop` does
        CAPABILITY DISPATCH: it calls `estimate()` only on managers where
        `hasattr(strategy, 'estimate')`, and records the rest in an
        `exit_no_fair_value_protocol` SET reported as a gauge. That fix was
        written on the explicit premise that DipArb has no `estimate()`.

        Raven ruled (D-300): THIS method stands, and that premise is retired.
        Declaring `manages_exits = True` obliges a strategy to ship an
        `estimate()` the loop can call, so the obligation is met here rather
        than worked around there.

        The dispatch in `shadow_loop.__init__` is deliberately NOT removed. It
        is redundant with this method today and is kept as a safety guard for a
        future exit-managing strategy that declares the flag without shipping
        the method. Its comment has been updated to say so.

        The consequence worth knowing: with every current manager implementing
        the protocol, `health['exit_no_fair_value_protocol']` reads 0 rather
        than one entry per asset. That makes any NONZERO reading a wiring bug,
        which also catches a fair-value strategy that loses its `estimate()` in
        a refactor - a breakage the pre-dispatch shape absorbed silently.
        """
        if ctx is None:
            return TapeMeanEstimate(False, EST_NO_CONTEXT,
                                    min_observations=self.min_observations)

        window_ts = getattr(ctx, 'window_ts', None)
        market = getattr(ctx, 'market', None)
        if market is None:
            return TapeMeanEstimate(False, EST_NO_MARKET, window_ts=window_ts,
                                    min_observations=self.min_observations)

        means: Dict[str, Optional[float]] = {}
        counts: Dict[str, int] = {}
        tokens: Dict[str, str] = {}
        for side in CANDIDATE_SIDES:
            token = token_id_for(market, side)
            if not token:
                # The market does not carry this outcome. Absent, not None:
                # see the note on `means_by_side`.
                continue
            tokens[side] = token
            counts[side] = self.tape.count(token)
            means[side] = self.mean_for(token)

        if not tokens:
            return TapeMeanEstimate(False, EST_NO_OUTCOME_TOKENS,
                                    window_ts=window_ts,
                                    min_observations=self.min_observations,
                                    means_by_side=means,
                                    observations_by_side=counts,
                                    tokens_by_side=tokens)

        reason = (EST_PER_TOKEN_MEAN
                  if any(m is not None for m in means.values())
                  else EST_INSUFFICIENT_TAPE)
        return TapeMeanEstimate(False, reason, window_ts=window_ts,
                                min_observations=self.min_observations,
                                means_by_side=means,
                                observations_by_side=counts,
                                tokens_by_side=tokens)

    # -- entry --------------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        slug = getattr(ctx.market, 'slug', None)
        observed = self.observe(ctx)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            feats.setdefault('tokens_observed_this_cycle',
                             sum(1 for ok in observed.values() if ok))
            feats.setdefault('tape_drops', dict(self.tape.drops))
            feats.setdefault('trades_this_window',
                             self.trades_this_window(ctx.window_ts))
            feats.setdefault('trade_count_is_attempts_not_fills', True)
            feats.setdefault('exits_before_resolution', True)
            # The reference is the outcome's OWN price history, not a model of
            # BTC. Stamped on every row so a scorer can never pool this with the
            # fair-value family on the strength of a shared exit shape.
            feats.setdefault('reference_is_historical_mean_not_model', True)
            feats.setdefault('mean_is_a_lagging_estimate', True)
            feats.setdefault('breakeven_win_rate_floor',
                             round(self.breakeven_win_rate, 6))
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market')

        if self.clock(ctx) is None:
            # Every gate below is a clock gate, and the tape cannot be
            # timestamped without one. Refuse rather than guess.
            return decide('SKIP', 'no_window_clock')

        remaining = ctx.seconds_remaining
        if self.trades_this_window(ctx.window_ts) >= self.max_trades_per_window:
            return decide('SKIP', 'max_trades_this_window',
                          max_trades_per_window=self.max_trades_per_window,
                          seconds_remaining=(None if remaining is None
                                             else round(remaining, 1)))

        if remaining is not None and remaining < self.min_entry_seconds_remaining:
            return decide('SKIP', 'too_late_in_window',
                          seconds_remaining=round(remaining, 1),
                          min_entry_seconds_remaining=self.min_entry_seconds_remaining)

        # Both sides are candidates and they are NOT mirror images: each has its
        # own book, its own tape and its own mean.
        candidates = []
        for side in CANDIDATE_SIDES:
            token = token_id_for(ctx.market, side)
            if not token:
                continue
            book = ctx.books.get(token)
            mean = self.mean_for(token)
            candidates.append({
                'side': side, 'token': token, 'book': book, 'mean': mean,
                'observations': self.tape.count(token),
                'best_ask': None if book is None else book.best_ask,
            })

        feats = {
            'seconds_remaining': (None if remaining is None
                                  else round(remaining, 1)),
            'min_observations': self.min_observations,
            'dip_threshold': self.dip_threshold,
            'tape_observations': {c['side']: c['observations']
                                  for c in candidates},
            'tape_means': {c['side']: (None if c['mean'] is None
                                       else round(c['mean'], 6))
                           for c in candidates},
            'confidence': 0.0,
        }

        if not candidates:
            # No readable outcome on the market object at all. A different fact
            # from "the outcome has no book" and from "the book has no asks".
            return decide('SKIP', 'no_outcomes', **feats)

        with_book = [c for c in candidates if c['book'] is not None]
        if not with_book:
            return decide('SKIP', 'no_orderbook', **feats)

        priced = [c for c in with_book if c['best_ask'] is not None]
        if not priced:
            # An empty book and a bids-only book are the same fact for a BUY:
            # nothing to lift at any price.
            return decide('SKIP', 'no_asks', **feats)

        with_mean = [c for c in priced if c['mean'] is not None]
        if not with_mean:
            # CANNOT MEASURE, not "no dip found" (convention 11).
            return decide('SKIP', 'insufficient_tape', **feats)

        in_band = [c for c in with_mean
                   if self.min_mean <= c['mean'] <= self.max_mean]
        feats['min_tradeable_mean'] = self.min_mean
        feats['max_tradeable_mean'] = self.max_mean
        if not in_band:
            return decide('SKIP', 'mean_outside_tradeable_band', **feats)

        # Rank by RELATIVE dip, which is what the threshold is stated in.
        for c in in_band:
            c['raw_dip_fraction'] = (c['mean'] - c['best_ask']) / c['mean']
        feats['candidate_dips'] = {c['side']: round(c['raw_dip_fraction'], 6)
                                   for c in in_band}

        best = max(in_band, key=lambda c: c['raw_dip_fraction'])
        side = best['side']
        token = best['token']
        book = best['book']
        mean = best['mean']
        feats.update({
            'outcome_side': side,
            'token_id': token,
            'rolling_mean': round(mean, 6),
            'observations': best['observations'],
            'tape_source_mix': self.tape.source_mix(token),
            'best_ask': best['best_ask'],
            'raw_dip_fraction': round(best['raw_dip_fraction'], 6),
        })

        # The cap IS the edge. Quote the worst price at which the trade still
        # clears the dip threshold and gate on the BOOK-WALKED average under it,
        # so a fill several cents inside the cap is reported at what it actually
        # cost rather than at the cap (the house rule in base.Leg.premium).
        cap = floor_to_tick(mean * (1.0 - self.dip_threshold))
        feats['entry_cap'] = cap
        if cap < 0.01:
            return decide('SKIP', 'dip_threshold_exceeds_mean', **feats)

        if best['raw_dip_fraction'] <= self.dip_threshold:
            # No dip worth taking. This is the strategy WORKING, and it is
            # expected to be the overwhelming majority of cycles.
            return decide('SKIP', 'dip_below_threshold', **feats)

        depth_limit = round(best['best_ask'] + self.depth_band, 6)
        depth = book.ask_depth(depth_limit)
        feats['depth_band'] = self.depth_band
        feats['ask_depth_within_band'] = depth
        feats['min_book_depth_shares'] = self.min_book_depth_shares
        if depth < self.min_book_depth_shares:
            return decide('SKIP', 'insufficient_book_depth', **feats)

        affordable = int(math.floor(self.max_notional_usdc / cap + 1e-9))
        shares = min(self.target_shares, affordable)
        feats['target_shares'] = self.target_shares
        feats['affordable_shares_at_cap'] = affordable
        feats['shares'] = shares
        feats['shares_capped_by_notional'] = shares < self.target_shares
        if shares < self.min_shares:
            # Could not run, did not lose (convention 11).
            return decide('SKIP', 'unsizable_at_notional_cap', **feats)

        effective = effective_ask_for(book, shares, cap)
        feats['effective_ask'] = (None if effective is None
                                  else round(effective, 4))
        if effective is None:
            return decide('SKIP', 'unfillable_at_cap', **feats)
        if effective > cap:
            # walk_book cannot return this under the same limit, but the cap is
            # the whole edge and a silent regression here would be invisible.
            return decide('SKIP', 'effective_ask_above_cap', **feats)

        realized_dip = (mean - effective) / mean
        feats['realized_dip_fraction'] = round(realized_dip, 6)
        feats['realized_dip_bps'] = (round((mean - effective) / effective
                                           * 10_000, 1) if effective > 0
                                     else None)
        feats['profit_target_price'] = round(mean, 4)
        feats['profit_target_floor_price'] = round(effective + self.min_profit, 4)
        # Tiered, and computed from the WALKED fill rather than the cap. The
        # target here is the rolling mean, not a fixed distance, so this is the
        # only leg of the geometry that the change touches.
        feats.update(tiered_stop_features(effective, side))
        feats['breakeven_win_rate_this_trade'] = self.breakeven_win_rate_for(
            effective, mean, self.stop_price_for(effective, side))
        feats['breakeven_win_rate_if_held_to_resolution'] = round(effective, 4)
        feats['notional_usdc'] = round(shares * effective, 4)
        # Confidence is the size of the dip we are taking, clamped into [0, 1].
        # It is a model-free observation about the book, NOT a probability that
        # the trade wins, and nothing may read it as a win rate.
        feats['confidence'] = round(min(1.0, max(0.0, realized_dip)), 6)
        feats['confidence_is_dip_size_not_win_probability'] = True
        feats['attempt_number'] = self._note_attempt(ctx.window_ts)
        feats['trades_this_window'] = self.trades_this_window(ctx.window_ts)

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=side,
                                limit_price=cap,
                                order_type='taker',
                                shares=shares,
                                expected_price=effective)],
                      **feats)

    # -- exit ---------------------------------------------------------------

    def manage_exit(self, position, book, now: float,
                    fair_value: Optional[float] = None) -> ExitDecision:
        """Decide whether one OPEN position should be sold right now.

        Same shape as `FairValueArb.manage_exit`, and it returns the SAME
        `ExitDecision` class so the shadow loop's exit path needs no special
        case. `fair_value` keeps that method's parameter name for interface
        compatibility, but here it means THE REFERENCE ROLLING MEAN. When the
        caller passes None the strategy reads its own tape for the position's
        token, so an exit still works when nobody upstream tracked the mean.

        Rule order, and why:

          1. `unreadable_position`  No size or no cost basis is a bookkeeping
                                    fault, not a trade to manage.
          2. `no_orderbook` / `no_bid_liquidity`  Cannot sell. `no_bid_liquidity`
                                    is stamped `unsellable=True`: it is NOT a
                                    patient hold, and if it persists to expiry
                                    the position resolves.
          3. `window_close`         Under 30s left the position stops being a
                                    mean-reversion trade and becomes a
                                    directional bet on the resolution.
          4. `price_stop`           The bid has reached the TIERED stop for
                                    this fill (`base.tiered_stop_price`), not a
                                    fixed MAX_LOSS below entry.
                                    Take the loss at whatever the book pays; a
                                    stop that refuses a bad price is not a stop.
                                    Ranked ABOVE the mean rules on purpose: it
                                    is the only rule that still works when the
                                    mean cannot be computed, and an operator
                                    reading `price_stop` learns more than one
                                    reading `mean_collapsed_to_entry` about the
                                    same fill.
          5. `mean_reverted`        The bid has come back to the rolling mean
                                    AND pays at least entry + MIN_PROFIT. Both
                                    legs are required: without the profit leg, a
                                    mean that fell to meet us would book a
                                    scratch and log it as a successful
                                    reversion.
          6. `mean_collapsed_to_entry`  The rolling mean itself has fallen to at
                                    or below entry + MEAN_STOP_MARGIN. The
                                    reference we bought against has come to us.
                                    That is story 2 in the module docstring and
                                    it is the exit that distinguishes this from
                                    a plain trailing stop. It fires LATE by
                                    construction, because a mean lags.
          7. `time_stop`            TIME_STOP_SEC with none of the above.

        A position whose mean cannot be computed still gets the clock and price
        rules; it holds `no_reference_mean` rather than `waiting_for_reversion`,
        because "we have no reference" and "we are waiting for the reference to
        be reached" are different states.
        """
        pid = getattr(position, 'position_id', '')
        entry = float(getattr(position, 'avg_price', 0.0) or 0.0)
        shares = float(getattr(position, 'shares', 0.0) or 0.0)
        opened_ts = getattr(position, 'opened_ts', None)
        window_ts = getattr(position, 'window_ts', None)
        token_id = getattr(position, 'token_id', None)
        outcome_side = getattr(position, 'outcome_side', None)

        mean = fair_value if fair_value is not None else self.mean_for(token_id)

        age = (None if opened_ts is None else float(now) - float(opened_ts))
        seconds_remaining = (None if window_ts is None
                             else float(window_ts) + WINDOW_SECONDS - float(now))
        best_bid = None if book is None else book.best_bid
        best_ask = None if book is None else book.best_ask

        feats = {
            'entry_price': round(entry, 4),
            'shares': shares,
            'age_sec': (None if age is None else round(age, 1)),
            'seconds_remaining': (None if seconds_remaining is None
                                  else round(seconds_remaining, 1)),
            'best_bid': best_bid,
            'best_ask': best_ask,
            'rolling_mean': (None if mean is None else round(mean, 6)),
            'rolling_mean_source': ('caller' if fair_value is not None
                                    else 'own_tape'),
            'observations': self.tape.count(token_id),
            'unrealized_at_bid': (None if best_bid is None
                                  else round((best_bid - entry) * shares, 4)),
            'profit_target_price': (None if mean is None else round(mean, 4)),
            'profit_target_floor_price': round(entry + self.min_profit, 4),
            'exits_before_resolution': True,
            'reference_is_historical_mean_not_model': True,
        }

        # The tiered stop, from the REAL fill on the REAL side. Computed before
        # the unreadable-position guard below so every row carries either a
        # stop or a named reason it has none (conventions 11 and 20).
        stop_px: Optional[float] = None
        if entry > 0.0:
            stop_px = self.stop_price_for(entry, outcome_side)
            feats.update(tiered_stop_features(entry, outcome_side))
        else:
            feats['stop_price'] = None
            feats['stop_is_tiered'] = True
            feats['stop_uncomputable_reason'] = 'entry_price_not_positive'

        def hold(reason, **extra):
            return ExitDecision('HOLD', reason, position_id=pid,
                                features=dict(feats, **extra))

        def exit_now(reason, limit, **extra):
            return ExitDecision('EXIT', reason, position_id=pid,
                                limit_price=limit, shares=shares,
                                features=dict(feats, exit_limit_price=limit,
                                              **extra))

        if shares <= 0 or entry <= 0:
            # A position with no size or no cost basis is a bookkeeping fault,
            # not a trade to manage. Refuse rather than compute stops off zero.
            return hold('unreadable_position')

        if book is None:
            return hold('no_orderbook')
        if best_bid is None:
            return hold('no_bid_liquidity',
                        unsellable=True,
                        note=('nobody is bidding; this position cannot be '
                              'closed and will resolve if that persists'))

        if seconds_remaining is not None \
                and seconds_remaining < self.window_close_exit_sec:
            return exit_now('window_close', URGENT_SELL_LIMIT,
                            window_close_exit_sec=self.window_close_exit_sec)

        if stop_px is not None and best_bid <= stop_px + 1e-12:
            # `max_loss` keeps its old key so an existing reader of
            # `sell:price_stop` rows does not lose the column, but it is the
            # TIERED distance for this fill now, not the instance constant.
            return exit_now('price_stop', URGENT_SELL_LIMIT,
                            max_loss=round(entry - stop_px, 6),
                            max_loss_specified_not_used=self.max_loss)

        if mean is not None:
            if (best_bid >= mean - self.mean_reversion_eps - 1e-12
                    and best_bid >= entry + self.min_profit - 1e-12):
                # Limit at the profit floor, not at the bid: if walking depth
                # for our full size would average below the floor, we would
                # rather not sell than book a fill that fails the rule we
                # exited on.
                return exit_now('mean_reverted',
                                floor_to_tick(entry + self.min_profit),
                                mean_reversion_eps=self.mean_reversion_eps,
                                rolling_mean_at_exit=round(mean, 6))

            if mean <= entry + self.mean_stop_margin + 1e-12:
                return exit_now('mean_collapsed_to_entry', URGENT_SELL_LIMIT,
                                mean_stop_margin=self.mean_stop_margin,
                                note=('the reference mean fell to our entry; '
                                      'the dip was the truth changing, not a '
                                      'mispricing'))

        if age is not None and age >= self.time_stop_sec:
            return exit_now('time_stop', URGENT_SELL_LIMIT,
                            time_stop_sec=self.time_stop_sec)

        if mean is None:
            return hold('no_reference_mean')
        return hold('waiting_for_mean_reversion')

    def exit_decisions(self, positions, books: Dict[str, object], now: float,
                       fair_value_by_side: Optional[Dict[str, float]] = None
                       ) -> List[ExitDecision]:
        """`manage_exit` over a batch. Same signature as the fair-value family.

        A position whose token has no book still gets a decision - it just gets
        a HOLD naming the missing book, because a position we could not look at
        is not a position we decided to keep.
        """
        fair_value_by_side = fair_value_by_side or {}
        out: List[ExitDecision] = []
        for pos in positions or ():
            if getattr(pos, 'strategy', None) != self.strategy_name:
                continue
            book = books.get(getattr(pos, 'token_id', None))
            mean = fair_value_by_side.get(getattr(pos, 'outcome_side', ''))
            out.append(self.manage_exit(pos, book, now, fair_value=mean))
        return out
