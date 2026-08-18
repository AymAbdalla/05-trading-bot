"""Maker Rebate Corridor Quote Ladder: rest ONE bid, take the paid side.

Implements `strategies/proposals/024-pm-maker-rebate-corridor-quote-ladder.md`.
`kind: experiment`, `expected_edge_bps: null`. Read the proposal before reading
the numbers here; nothing in this file is an edge estimate.

## What the proposal actually specifies, and what the TITLE says

The title says "corridor quote ladder". The rules do not describe a ladder and
they do not describe a corridor. Rule 3 places **ONE** resting BUY, rule 4 caps
it at **one resting order per market per window** and forbids quoting both sides
of the same market in the same window. `grid_hedge` is the ladder in this
package; this is not a second one.

The rules win over the title. The name is kept because it is the proposal's
`name` field and renaming it here would make the strategy key un-greppable
against the document that specifies it. Flagged rather than silently resolved.

## THE THESIS, restated so it cannot be read as an edge claim

616 `fair_value_arb` family trades paid the round trip and lost $338.60 with
$0.00 in fees, and the inverse variant - which took the opposite side of every
signal - still lost $32.50 at a 48.1% win rate. A near-coin-flip that bleeds in
BOTH directions is a cost charged to whoever crosses. This strategy takes the
other side of that transfer by resting instead of crossing.

That is an argument about WHO PAYS. It is not an argument that we will be paid,
because the fill rate is unknown: `maker_fill_not_simulated` appears 3,130 times
in the skip table under `SIM_LIMIT`. Convention 11 - a condition that has never
been evaluated has no knowable edge - so `expected_edge_bps` is null and this is
an experiment.

## THE GEOMETRY IS DEMANDING AND IT IS STATED UP FRONT

Target is entry + 2c. Stop is entry - 4c. Break-even win rate is
`4 / (2 + 4) = 66.7%`, exposed as `breakeven_win_rate` and computed from the
instance rather than written down, so a re-sized variant cannot be compared
against the parent's number by accident. The `fair_value_arb` family showed win
rates of 22% to 48% ON THIS EXACT INSTRUMENT. If the maker fill inherits that
hit rate, the 2c capture does not save it.

The proposal itself says: if the first 200 filled quotes come in below 60%, the
2/4 geometry is wrong and should be RE-SIZED before the idea is judged.

## ADVERSE SELECTION IS THE STRONGEST ARGUMENT AGAINST THIS

A resting bid on a 5-minute binary fills precisely when the market is moving
against it. The taker who lifts our bid is selling because spot just moved
through the strike, so the fill rate is highest exactly when the fill is worst.
Nothing in our evidence says this book is friendly to a maker. A simulated fill
is not evidence a real resting order would have filled; promoting this on paper
results alone would be promoting the adapter's assumptions.

## WHY THIS RETURNS 'QUOTE' AND NEVER 'ENTER'

Same answer as `box_builder` and `grid_hedge`, and it is enforced rather than
described: `assert_not_enter` RAISES on an ENTER decision and every decision
this class emits passes through it (convention 22). A maker does not enter at
decision time. It rests, and finds out one or more cycles later, so `QUOTE` is
the truthful action and `simulate_maker_buy` is what turns one into a position -
strict cross, minus the queue that was ahead of us at rest time. A touch is not
a fill.

## CONVENTION 5, and why the 0.20-0.80 mid band IS the gross-edge floor

On a binary the floor is 200bps, not 30. The capture is a flat 2c, so the
capture in bps is `0.02 / entry * 10_000` and it SHRINKS as the contract gets
dearer:

    entry 0.20  ->  1000 bps
    entry 0.35  ->   571 bps
    entry 0.80  ->   250 bps
    entry 0.98  ->   204 bps

`QUOTE_MID_MAX = 0.80` is therefore not a taste. It is the point past which a 2c
capture stops clearing the PREDICTION_MARKET floor with any margin, and the
quote price is the best BID, which is strictly below the mid, so the realised
capture in bps is always better than the number the band guarantees.
`capture_bps` rides on every row so this is measurable rather than argued about.
`QUOTE_MID_MIN = 0.20` is the proposal's: a 0.05 contract cannot pay a 1c
capture.

## CONVENTION 8: the stop, and the degenerate case

Rule 7 is a stop at `entry - 0.04`, strictly below entry by construction. When
entry is at or below 0.04 the subtraction lands at or below 0.00, and a binary
share cannot be quoted below 0.00, so the stop COLLAPSES ONTO THE STRUCTURAL
FLOOR at 0.00 and the whole premium is at risk. The proposal requires that the
two regimes be LOGGED SEPARATELY rather than pooled, and `stop_regime` does
exactly that: `'discretionary_4c'` or `'structural_floor_held_to_resolution'`.
Convention 20 - two causes, two counters.

Note this strategy does NOT use `base.tiered_stop_price`. The tier table is a
different rule with different distances; the proposal specifies a flat 4c and
the proposal is the spec. `stop_price_for` implements the flat rule and the
shadow loop reads it off the strategy rather than re-deriving one (convention
23).

## HALT

`engine/halt.py` is the single definition and the maker path already honours it:
a resting BUY is refused while halted and orders already on the book are
cancelled. This file does NOT re-implement that check. A second implementation
of a kill switch is a second thing to get wrong, and the proposal's rule 9 asks
only that the existing behaviour not be weakened. `halt_is_enforced_by` names
the site on every row.

## NAMED REASONS, NONE OF THEM POOLED (convention 20)

    no_market                   no market on the context
    no_window_clock             no seconds_remaining; the arm band is a clock
                                gate and cannot be evaluated without one
    quote_outside_arm_band      there IS a clock and we are outside the band
    already_quoted_this_window  rule 4's hard cap, one order per market per
                                window. Deliberately NOT shared with
                                `already_entered_this_window`: that one means a
                                POSITION exists, this one means an ORDER does
    no_orderbook                neither outcome token has a book
    no_asks                     the best a token got was "a book with no offer"
    no_bids                     ... "a book with no bid". There is nothing to
                                join, and an absent bid is not a bid of zero
    book_too_tight_to_arm       spread under 2c: nothing to capture
    mid_outside_quote_band      mid outside 0.20-0.80 on every candidate
    maker_fill_not_simulated    the QUOTE path

`no_asks`, `no_bids`, `book_too_tight_to_arm` and `mid_outside_quote_band` are
four counters and never one, exactly as the proposal's convention-20 note
requires.

WHICH ONE IS REPORTED when the two outcome tokens fail at different gates: the
gate the FURTHEST-ADVANCED token reached. If Up has no asks and Down merely
quotes too tight, the binding constraint on the market is tightness, and
reporting `no_asks` there would file a market-quality fact as a data blocker.

## KILL CONDITION (convention 6: a number and a named harness)

Run `agents/forge_shadow_eval.py` over `db/trading.db` after the maker path is
wired. Kill this experiment if EITHER:

  (a) fewer than **100** resting quotes are simulated as filled after **20,000**
      evaluations - the fill model is not producing a testable sample; OR
  (b) net P&L per filled quote is below **0.0 cents** over **200 or more**
      filled quotes.

`backtest/validate_harness.py` must exit 0 before either number counts
(convention 1).

AND IT IS NOT ARMED YET. Say it out loud: `KILL_CONDITION_BLOCKED_BY` is
`no_filled_quote_sample`. Neither branch can be evaluated until resting quotes
actually fill in a live cycle, and a shadow log full of QUOTE rows is ZERO
EVIDENCE in either direction, not a flat result (convention 11).

## WHAT WOULD CHANGE MY MIND

The proposal's own two: if the logged spread on acted signals comes back with a
median under 1 cent then the spread was never the cost, the `fair_value_arb`
diagnosis is a misattribution and this proposal is built on it. And if the first
200 simulated fills show a win rate materially below the fill-weighted rate of
the TAKER side over the same windows, that is adverse selection in the data and
the maker side is not free money on this book.
"""
import math
from typing import Dict, List, Optional, Tuple

from engine.polymarket.paper_adapter import MAKER_FILL_MODEL
from strategies.polymarket.base import (BINARY_STOP, BINARY_TARGET,
                                        MARKET_TYPE_CRYPTO_UPDOWN, Decision,
                                        Leg, MarketContext, PolymarketStrategy)
from strategies.polymarket.grid_hedge import grid_pnl

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

# ---------------------------------------------------------------------------
# The proposal's constants. Every one is an assumption with an expiry date
# (convention 17); none has been measured, because nothing has filled.
# ---------------------------------------------------------------------------

#: Rule 1. Arm only while the REMAINING time sits inside the band, in seconds.
#: `(min_remaining, max_remaining)` - both inclusive.
ARM_BAND_5M = (60.0, 240.0)
ARM_BAND_15M = (120.0, 600.0)

#: Rule 2. Under this the ask-to-bid gap is not worth resting into.
MIN_ARM_SPREAD = 0.02

#: Rule 3. Never quote a contract whose mid sits outside this band. The upper
#: bound is also the convention 5 gross-edge floor - see the module docstring.
QUOTE_MID_MIN = 0.20
QUOTE_MID_MAX = 0.80

#: Rules 3 and 4. One order, five shares, per market per window.
SHARES_PER_QUOTE = 5
MAX_QUOTES_PER_MARKET_PER_WINDOW = 1

#: Rule 6. The resting SELL goes at entry plus this.
CAPTURE = 0.02

#: Rule 6. Cancel and re-place ONCE if our sell ends up more than this far
#: inside the best ask.
REPLACE_IF_INSIDE_ASK_BY = 0.03
MAX_SELL_REPLACEMENTS = 1

#: Rule 7. Stop distance, absolute contract price. Strictly below entry by
#: construction; collapses onto 0.00 for an entry at or below 0.04.
STOP_DISTANCE = 0.04

#: Rule 8. Cancel an unfilled rest, or cross out of a filled one, this many
#: seconds before the window closes. Nothing is carried into resolution.
TIME_STOP_SEC = 30.0

#: Convention 5. On a binary the gross-edge floor is 200bps, not 30.
MIN_GROSS_EDGE_BPS = 200.0

#: Convention 6. The numbers, next to the harness that reads them.
KILL_CONDITION = ('agents/forge_shadow_eval.py over db/trading.db: kill if '
                  'fewer than 100 resting quotes fill in 20000 evaluations, '
                  'OR if net P&L per filled quote is below 0.0 cents over 200 '
                  'or more filled quotes')
KILL_CONDITION_HARNESS = 'agents/forge_shadow_eval.py'
KILL_CONDITION_MIN_FILLS_FOR_SAMPLE = 100
KILL_CONDITION_EVALUATIONS = 20_000
KILL_CONDITION_MIN_FILLS_FOR_VERDICT = 200
KILL_CONDITION_MIN_NET_PNL_PER_FILL = 0.0
#: Convention 11, said out loud: neither branch can run yet.
KILL_CONDITION_BLOCKED_BY = 'no_filled_quote_sample'

PRICE_DECIMALS = 2

#: Every reason `evaluate` can produce, QUOTE included. Listed so a reader can
#: see no two causes share a string and so a test can assert each is REACHABLE
#: rather than trusting this docstring (convention 22).
DECISION_REASONS = (
    'no_market',
    'no_window_clock',
    'quote_outside_arm_band',
    'already_quoted_this_window',
    'no_orderbook',
    'no_asks',
    'no_bids',
    'book_too_tight_to_arm',
    'mid_outside_quote_band',
    'degenerate_quote',
    'maker_fill_not_simulated',
)

#: The two stop regimes rule 7 asks to be logged separately.
STOP_REGIME_DISCRETIONARY = 'discretionary_4c'
STOP_REGIME_STRUCTURAL = 'structural_floor_held_to_resolution'

#: Outcome sides evaluated, in the order ties are broken.
CANDIDATE_SIDES = ('Up', 'Down')


def round_price(price: float) -> float:
    """Snap onto the 1c tick grid. Polymarket quotes nothing finer."""
    return round(float(price), PRICE_DECIMALS)


def assert_not_enter(action: str) -> str:
    """Refuse an ENTER, loudly. Every decision this module emits passes here.

    The module docstring's central claim made into a WIRING TEST rather than a
    sentence (convention 22). This strategy has no simulable taker ENTRY path
    at all: its only order at decision time is a resting bid, and an ENTER out
    of it would be a fill nobody modelled and a P&L nobody earned. If a future
    edit reaches this, that edit is the bug and it should stop the run rather
    than quietly book a manufactured trade.
    """
    if action == 'ENTER':
        raise AssertionError(
            'PM_maker_rebate_corridor_quote_ladder must never return ENTER: '
            'its only decision-time order is a RESTING BID, and resting is not '
            'filling. A fill arrives one or more cycles later through '
            'PolymarketPaperAdapter.simulate_maker_buy. See the module '
            'docstring.')
    return action


def arm_band_for(window_seconds: float) -> Tuple[float, float]:
    """The proposal's arm band for a 5m or a 15m window, in seconds remaining.

    Rule 1 names exactly two bands and no rule for anything else. Anything that
    is not a 900-second window is treated as the 5m case, which is the
    CONSERVATIVE reading: the 5m band is the narrower of the two, so an
    unrecognised clock arms less often rather than more.
    """
    return ARM_BAND_15M if float(window_seconds) >= 900.0 else ARM_BAND_5M


def capture_bps(entry_px: float, capture: float = CAPTURE) -> Optional[float]:
    """The 2c capture as basis points of the premium actually paid.

    `None` for a non-positive entry rather than an infinity: convention 19
    serialises these features with `allow_nan=False`, and an `inf` here would
    get the whole key stripped out of the row instead of failing loudly.
    """
    entry = float(entry_px)
    if not math.isfinite(entry) or entry <= 0.0:
        return None
    return (float(capture) / entry) * 10_000.0


def stop_regime_for(entry_px: float,
                    stop_distance: float = STOP_DISTANCE) -> str:
    """Which of rule 7's two regimes applies at `entry_px`. Never pooled."""
    return (STOP_REGIME_STRUCTURAL
            if float(entry_px) - float(stop_distance) <= BINARY_STOP
            else STOP_REGIME_DISCRETIONARY)


#: `grid_hedge.grid_pnl` already computes exactly what this kill condition
#: reads - net P&L over a set of (entry, exit, shares) round trips, fees on
#: both legs, every row stamped as a hypothetical maker fill. Reusing it rather
#: than writing a second one: convention 23, and two implementations of one
#: arithmetic is two places for it to drift.
quote_pnl = grid_pnl


def breakeven_win_rate(capture: float = CAPTURE,
                       stop_distance: float = STOP_DISTANCE) -> float:
    """`stop / (capture + stop)`. 66.7% at the proposal's 2c/4c geometry.

    Stated as a function of the INSTANCE's numbers rather than written down, so
    a re-sized variant cannot be compared against the parent's break-even by
    accident - the same trap the four `fair_value_arb` variants document.
    """
    return float(stop_distance) / (float(capture) + float(stop_distance))


class MakerRebateCorridorQuoteLadder(PolymarketStrategy):
    """One resting bid per market per window. Returns QUOTE, never ENTER."""

    strategy_name = 'PM_maker_rebate_quote_ladder'
    uses_maker_orders = True
    paper_mode = PAPER_MODE

    #: The proposal's `markets` field names btc/eth/sol up-down 5m and 15m and
    #: nothing else. Every gate in this file is a crypto-window gate: the arm
    #: band is measured against a 300 or 900 second clock, and the two outcome
    #: tokens are looked up as 'Up' and 'Down'. Declaring anything wider would
    #: be claiming support for a universe this has never been evaluated against
    #: (convention 3).
    supported_market_types = (MARKET_TYPE_CRYPTO_UPDOWN,)

    #: Rules 6 to 8 describe what happens AFTER a fill, and none of it runs
    #: today: nothing has filled. False, honestly, rather than True describing
    #: code nothing reaches. `exit_plan` is the specification of those rules and
    #: is exercised directly by the tests.
    manages_exits = False

    def __init__(self, arm_band_5m: Tuple[float, float] = ARM_BAND_5M,
                 arm_band_15m: Tuple[float, float] = ARM_BAND_15M,
                 min_arm_spread: float = MIN_ARM_SPREAD,
                 quote_mid_min: float = QUOTE_MID_MIN,
                 quote_mid_max: float = QUOTE_MID_MAX,
                 shares_per_quote: float = SHARES_PER_QUOTE,
                 capture: float = CAPTURE,
                 stop_distance: float = STOP_DISTANCE,
                 time_stop_sec: float = TIME_STOP_SEC,
                 max_quotes_per_window: int = MAX_QUOTES_PER_MARKET_PER_WINDOW):
        self.arm_band_5m = tuple(arm_band_5m)
        self.arm_band_15m = tuple(arm_band_15m)
        self.min_arm_spread = min_arm_spread
        self.quote_mid_min = quote_mid_min
        self.quote_mid_max = quote_mid_max
        self.shares_per_quote = shares_per_quote
        self.capture = capture
        self.stop_distance = stop_distance
        self.time_stop_sec = time_stop_sec
        self.max_quotes_per_window = max_quotes_per_window
        #: Rule 4's hard cap. Per INSTANCE, which is why `build_strategies()`
        #: hands out fresh objects: two loops sharing one ledger would let each
        #: consume the other's quota.
        self._quotes_this_window: Dict[Tuple[Optional[str], int], int] = {}

    # -- the geometry, exposed so nothing has to re-derive it ----------------

    @property
    def breakeven_win_rate(self) -> float:
        """66.7% at defaults. Computed from THIS instance, never a constant."""
        return breakeven_win_rate(self.capture, self.stop_distance)

    def stop_price_for(self, entry_px: float,
                       side: Optional[str] = None) -> float:
        """Rule 7's stop PRICE for a long filled at `entry_px`.

        `max(0.00, entry_px - 0.04)`, and convention 8 is enforced rather than
        assumed: the result is clamped at the 0.00 a losing binary share is
        actually worth, and then ASSERTED strictly below the entry. Read by
        `shadow_loop._stop_for`; `side` is accepted for interface symmetry and
        cannot change the answer, because every position in this package is a
        long of exactly one outcome token.
        """
        entry = float(entry_px)
        if not math.isfinite(entry):
            raise ValueError('entry_px must be finite, got {!r}'.format(entry_px))
        if not (entry > BINARY_STOP):
            raise ValueError(
                'entry_px must be strictly above {:.2f}, got {!r}'
                .format(BINARY_STOP, entry_px))
        if entry > BINARY_TARGET:
            raise ValueError(
                'entry_px must be at or below {:.2f}, got {!r}'
                .format(BINARY_TARGET, entry_px))
        stop = round(entry - float(self.stop_distance), 10)
        if stop < BINARY_STOP:
            stop = BINARY_STOP
        assert stop < entry, (
            'stop {!r} is not strictly below entry {!r}'.format(stop, entry))
        return stop

    def exit_plan(self, entry_px: float,
                  best_ask: Optional[float] = None,
                  replacements_so_far: int = 0) -> Dict[str, object]:
        """Rules 6, 7 and 8 as data. Books nothing; describes what would run.

        Returned as a dict rather than executed because NOTHING HAS FILLED. A
        method that "manages the exit" of a position that cannot exist is code
        no test can reach and no log row can confirm (convention 22).

        `sell_price`      rule 6: a resting SELL at entry + 2c.
        `replace_sell`    rule 6: True when our sell sits more than 3c inside
                          the best ask AND we have not already re-placed once.
        `stop_price`      rule 7, and `stop_regime` says which of the two
                          regimes produced it. Never pooled.
        `time_stop_sec`   rule 8: cancel unfilled / cross out of filled, 30s
                          before close. Nothing is carried into resolution.
        """
        entry = float(entry_px)
        sell_price = round_price(min(BINARY_TARGET, entry + float(self.capture)))
        stop_price = self.stop_price_for(entry)
        regime = stop_regime_for(entry, self.stop_distance)
        inside_ask = (None if best_ask is None
                      else round(float(best_ask) - sell_price, 6))
        replace = bool(
            inside_ask is not None
            and inside_ask > REPLACE_IF_INSIDE_ASK_BY + 1e-12
            and int(replacements_so_far) < MAX_SELL_REPLACEMENTS)
        return {
            'entry_price': round(entry, 6),
            'sell_price': sell_price,
            'capture': self.capture,
            'sell_is_inside_best_ask_by': inside_ask,
            'replace_sell': replace,
            'replace_threshold': REPLACE_IF_INSIDE_ASK_BY,
            'replacements_so_far': int(replacements_so_far),
            'max_sell_replacements': MAX_SELL_REPLACEMENTS,
            'stop_price': round(stop_price, 6),
            'stop_distance_nominal': self.stop_distance,
            'stop_distance_effective': round(entry - stop_price, 10),
            'stop_regime': regime,
            'stop_is_structural_floor': regime == STOP_REGIME_STRUCTURAL,
            'stop_loss_fraction_of_entry': round((entry - stop_price) / entry, 6),
            'time_stop_sec': self.time_stop_sec,
            'carries_into_resolution': regime == STOP_REGIME_STRUCTURAL,
            'breakeven_win_rate': round(self.breakeven_win_rate, 6),
        }

    # -- window bookkeeping -------------------------------------------------

    @staticmethod
    def window_key(ctx: MarketContext) -> Tuple[Optional[str], int]:
        return (getattr(ctx.market, 'slug', None), int(ctx.window_ts))

    def quotes_this_window(self, ctx: MarketContext) -> int:
        return self._quotes_this_window.get(self.window_key(ctx), 0)

    def _note_quote(self, ctx: MarketContext) -> None:
        key = self.window_key(ctx)
        self._quotes_this_window[key] = self._quotes_this_window.get(key, 0) + 1

    # -- decision -----------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        """Decide one window. ALWAYS returns a Decision, and NEVER an ENTER."""
        self.assert_supports(ctx)
        slug = getattr(ctx.market, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            assert_not_enter(action)
            feats.setdefault('paper_mode', self.paper_mode)
            feats.setdefault('uses_maker_orders', True)
            feats.setdefault('fill_model', 'maker_fills_not_simulated')
            feats.setdefault('expected_edge_bps', None)
            feats.setdefault('kind', 'experiment')
            feats.setdefault('kill_condition', KILL_CONDITION)
            feats.setdefault('kill_condition_harness', KILL_CONDITION_HARNESS)
            feats.setdefault('kill_condition_blocked_by',
                             KILL_CONDITION_BLOCKED_BY)
            feats.setdefault('breakeven_win_rate',
                             round(self.breakeven_win_rate, 6))
            feats.setdefault(
                'halt_is_enforced_by',
                'engine.halt.is_halted via the paper adapter maker path')
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market')

        # 1. THE ARM BAND. A missing clock is NOT "outside the band" - one is a
        # cannot-run and the other is a measured refusal (convention 11).
        remaining = ctx.seconds_remaining
        window_seconds = self.window_seconds_for(ctx)
        low, high = (self.arm_band_15m if window_seconds >= 900.0
                     else self.arm_band_5m)
        feats: Dict[str, object] = {
            'window_seconds': window_seconds,
            'arm_band_low_sec': low,
            'arm_band_high_sec': high,
            'seconds_remaining': (None if remaining is None
                                  else round(remaining, 1)),
        }
        if remaining is None:
            return decide('SKIP', 'no_window_clock', **feats)
        if not (low - 1e-9 <= remaining <= high + 1e-9):
            return decide('SKIP', 'quote_outside_arm_band', **feats)

        # 4. ONE ORDER PER MARKET PER WINDOW, hard cap. Checked before the book
        # is read: whatever the book says, we are not quoting again.
        already = self.quotes_this_window(ctx)
        feats.update({'quotes_this_window': already,
                      'max_quotes_per_window': self.max_quotes_per_window})
        if already >= self.max_quotes_per_window:
            return decide('SKIP', 'already_quoted_this_window', **feats)

        # 2 and 3. THE BOOK, per outcome token, then the mid band.
        candidates, worst_gate, gate_features = self._survey(ctx)
        feats.update(gate_features)
        if worst_gate < 0:
            return decide('SKIP', 'no_orderbook', **feats)
        if not candidates:
            # The gate the FURTHEST-ADVANCED token reached. Four literals, four
            # counters; the binding constraint on the market, not the worst
            # single token. See the module docstring.
            gate_reason = 'no_asks'
            if worst_gate == 1:
                gate_reason = 'no_bids'
            elif worst_gate == 2:
                gate_reason = 'book_too_tight_to_arm'
            elif worst_gate == 3:
                gate_reason = 'mid_outside_quote_band'
            return decide('SKIP', gate_reason, **feats)

        # Widest spread wins - the spread IS the capture rule 2 gates on - and
        # ties break to the first of CANDIDATE_SIDES so the choice is
        # reproducible from a logged context.
        chosen = max(candidates, key=lambda c: (c['spread'],
                                                -CANDIDATE_SIDES.index(c['side'])))
        quote_price = round_price(chosen['best_bid'])
        if quote_price <= BINARY_STOP:
            # A best bid of 0.00 survives the mid band whenever the spread is
            # wide enough (mid 0.20 on a 0.00/0.40 book). There is no price
            # strictly below it for rule 7's stop to sit at, so there is no
            # entry to quote. A bookkeeping fault about the book, not a market
            # view - and its own counter, never pooled with the tightness gate.
            feats['quote_price'] = quote_price
            feats['quote_side'] = chosen['side']
            return decide('SKIP', 'degenerate_quote', **feats)
        plan = self.exit_plan(quote_price, best_ask=chosen['best_ask'])
        capture = capture_bps(quote_price, self.capture)
        feats.update({
            'quote_side': chosen['side'],
            'quote_price': quote_price,
            'quote_joins_best_bid': True,
            'chosen_best_ask': chosen['best_ask'],
            'chosen_best_bid': chosen['best_bid'],
            'chosen_mid': round(chosen['mid'], 6),
            'chosen_spread': round(chosen['spread'], 6),
            'shares_per_quote': self.shares_per_quote,
            'capture_bps': None if capture is None else round(capture, 1),
            'min_gross_edge_bps': MIN_GROSS_EDGE_BPS,
            'capture_bps_clears_binary_floor': (
                capture is not None and capture >= MIN_GROSS_EDGE_BPS),
            'exit_plan': plan,
            'stop_price': plan['stop_price'],
            'stop_regime': plan['stop_regime'],
            'confidence': 0.5,
            'confidence_is_a_placeholder_no_fill_rate_is_known': True,
            'maker_fill_model_available': MAKER_FILL_MODEL,
            'maker_quote_is_restable': True,
            'maker_rest_verb': ('engine.polymarket.paper_adapter'
                                '.PolymarketPaperAdapter.simulate_maker_buy'),
            'quote_pnl_helper': ('strategies.polymarket'
                                 '.maker_rebate_corridor_quote_ladder'
                                 '.quote_pnl'),
            'is_a_ladder': False,
            'title_says_ladder_rules_say_one_order': True,
        })
        legs = [Leg(chosen['side'], quote_price, order_type='maker',
                    shares=self.shares_per_quote)]
        self._note_quote(ctx)
        return decide('QUOTE', 'maker_fill_not_simulated', legs=legs, **feats)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def window_seconds_for(ctx: MarketContext) -> float:
        """300 or 900, read off the market slug, defaulting to the 5m case.

        The context carries a 300-second clock (`base.WINDOW_SECONDS`) and no
        window-length field, so the 15m band can only be selected by looking at
        the market this context is about. An unrecognised slug takes the 5m
        band, which is the narrower one - an unknown clock arms LESS often.
        """
        slug = str(getattr(ctx.market, 'slug', '') or '')
        return 900.0 if '15m' in slug else 300.0

    def _survey(self, ctx: MarketContext):
        """Both outcome tokens against rules 2 and 3.

        Returns `(candidates, furthest_gate, features)`. `furthest_gate` is -1
        when no token had a book at all, else the index of the last gate any
        token reached: 0 asks, 1 bids, 2 spread, 3 mid band, 4 armed.
        """
        candidates: List[Dict[str, object]] = []
        furthest = -1
        features: Dict[str, object] = {
            'min_arm_spread': self.min_arm_spread,
            'quote_mid_min': self.quote_mid_min,
            'quote_mid_max': self.quote_mid_max,
        }
        for side in CANDIDATE_SIDES:
            book = ctx.book(side)
            prefix = side.lower()
            features[prefix + '_has_book'] = book is not None
            if book is None:
                continue
            best_ask, best_bid = book.best_ask, book.best_bid
            features[prefix + '_best_ask'] = best_ask
            features[prefix + '_best_bid'] = best_bid
            furthest = max(furthest, 0)
            if best_ask is None:
                continue
            furthest = max(furthest, 1)
            if best_bid is None:
                continue
            spread = best_ask - best_bid
            mid = (best_ask + best_bid) / 2.0
            features[prefix + '_spread'] = round(spread, 6)
            features[prefix + '_mid'] = round(mid, 6)
            furthest = max(furthest, 2)
            if spread < self.min_arm_spread - 1e-12:
                continue
            furthest = max(furthest, 3)
            if not (self.quote_mid_min - 1e-12 <= mid
                    <= self.quote_mid_max + 1e-12):
                continue
            furthest = max(furthest, 4)
            candidates.append({'side': side, 'best_ask': best_ask,
                               'best_bid': best_bid, 'spread': spread,
                               'mid': mid})
        return candidates, furthest, features
