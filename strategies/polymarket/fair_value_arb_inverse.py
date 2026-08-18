"""Fair Value Arb, INVERSE: buy whatever the parent refused to buy.

Not a parameter variant. The three existing variants (`_wide`, `_patient`,
`_hft`) move constants against the same hypothesis. This one keeps every
constant and inverts the hypothesis: it takes the parent's entry signal, its
entry timing, its edge threshold, its fair value model, its sizing rule and its
depth gate, and then buys THE OTHER SIDE.

    parent says buy Down   ->  this buys Up
    parent says buy Up     ->  this buys Down
    parent says SKIP       ->  this skips, with the parent's own reason

## WHY, AND THE MEASUREMENT BEHIND IT

Source: `research/polymarket_paper/polymarket_paper_log.csv`, rows where
`strategy == 'PM_fair_value_arb'` and `action == 'CLOSE'`. Measured, not
estimated, and re-derived from the log rather than copied from a summary:

    closes            33
    wins               7          win rate 21.2%
    avg win       +$0.9614
    avg loss      -$1.3371
    total P&L    -$28.0340
    exit reasons  26 price_stop, 7 profit_target
    sides taken   17 Down, 16 Up

The side split is the load-bearing part. 17/16 is not a directional bias, so
the parent is not simply "long Up in a down tape". It is losing symmetrically,
which is the shape a systematically ANTI-CALIBRATED model makes: the model
picks a side, and the side it picks is the wrong one at a rate well below a
coin flip. If that is what is happening, the same signal read backwards is a
signal.

**33 trades is a SHRUG, not a verdict (convention 7).** A FAIL on 200k trades
is a verdict; 33 closes is a number you would get from a fair coin with
uncomfortable frequency, and it is one strategy over a few hours of one paper
loop. Nothing below should be read as "the parent is proven anti-calibrated".
The honest statement is "the parent's log is bad enough to be worth reading
backwards, cheaply, in shadow, where being wrong costs nothing".

## THE ARITHMETIC, AND IT IS NOT GOOD

Naive inverse EV per share, taking the parent's 21.2% at face value and
assuming it flips cleanly to 79%:

    EV = 0.79 * min_profit - 0.21 * max_loss
       = 0.79 * 0.01 - 0.21 * 0.03
       = 0.0079 - 0.0063
       = +0.0016 per share

That is 16 bps against the $1.00 binary payoff, or ~37 bps against a 43c
premium (the denominator this repo's `realized_edge_bps` actually uses). Either
way it is on top of convention 5's 30bps dead-on-arrival floor, not clear of
it - and that floor is a floor for GROSS edge. We enter by lifting the ask and
exit by hitting the bid, and on a 5-minute Polymarket book that round trip is
1-2c, which is 5-10x the entire 0.16c edge. Net of the spread the number is
negative.

So: **this strategy is at or below the house DOA floor on its own naive
arithmetic, before costs.** It is being built anyway because shadow is free and
the hypothesis is falsifiable, not because the arithmetic supports it. Do not
dress it up.

## THE WIN RATE WILL NOT BE 79%, AND NOT FOR A SUBTLE REASON

The parent's 7 wins were `profit_target` exits: the bid paid entry + 1c. Flip
the side and those become roughly -1c moves. **-1c is INSIDE the 3c stop.** It
does not stop out. It sits there and runs to the time stop, and resolves
somewhere between -3c and +1c depending on what the book does over the next 60
seconds.

So the inverse's LOSS population is not the parent's WIN population reflected.
The parent's 26 stop-outs (-3c-ish) become the inverse's target hits (+1c);
the parent's 7 target hits (+1c) become a bucket of near-scratch positions with
an unknown tail. The honest expectation is:

    more wins, smaller edge per win, and a large time-stop bucket
    whose distribution nobody has measured

The time-stop bucket is where this strategy will actually be decided, and no
number in this docstring describes it. It is stamped on every exit row as
`time_stop` so it can be counted later.

## THE OPPOSITE SIDE IS NOT `1 - parent_entry`. THE BOOK IS.

On a Polymarket binary `ask(Up) + ask(Down)` is about 1.03, not 1.00. The 3c is
the overround and it is paid by whoever lifts. So the flipped side is priced
against ITS OWN book, walked for OUR size under OUR limit, with the same depth
gate the parent applies - never computed arithmetically from the parent's
price. Every entry row carries `ask_sum`, `overround`,
`naive_inverse_price_one_minus_parent` and `overround_cost_vs_naive` so the gap
between the naive number and the real one is on the record rather than assumed.

The direct consequence, and the main reason a 79% loss rate does not become a
79% win rate: **the spread does not invert.** Both strategies lift the ask and
both hit the bid. The parent pays the round trip and so does this. Inverting
the side inverts the direction of the bet; it does nothing whatsoever to the
cost of taking it. Whatever fraction of the parent's -$28.03 was spread rather
than direction, this strategy pays again.

If the opposite side's book fails a gate the parent's side passed, that is a
legitimate SKIP with its OWN reason (`inverse_side_*`), never pooled with the
parent's (convention 20).

## THE ENTRY CAP: REPLACED, AND HERE IS WHY IT HAD TO BE

The parent's cap is `floor_to_tick(fair - edge_threshold)`: the worst price at
which the trade still carries the model's edge. **That formula is not merely
wrong on the flipped side, it is unsatisfiable by construction.**

Worked on the standard fixture. Model says P(Up) = 0.71, ask(Up) = 0.60,
ask(Down) = 0.42, so the parent buys Up with an 11c edge. Flipped:

    fair(Down)  = 0.29
    ask(Down)   = 0.42
    parent-style cap = 0.29 - 0.04 = 0.25, which is 17c BELOW the ask

The gap is not an accident of this fixture. The parent selects the side with
the LARGEST `fair - ask`, so the side it rejects is rich by (that edge) plus
(the overround), and both of those are strictly positive whenever the parent
fires. A model-derived cap on the flipped side is therefore below the flipped
ask ALWAYS, and inheriting it would produce a strategy that never fires once -
which in a graveyard is indistinguishable from a strategy honestly measured and
found to have no edge (convention 3).

**Decision: the cap is book-derived, not model-derived.**

    cap = ceil_to_tick(best_ask(flipped) + inverse_slippage_allowance)

with the allowance at one tick. A marketable limit with one tick of room, and
the fill is still reported as the walked `effective_ask`, not as the cap.

**Consequence, stated plainly: the inverse has NO price-based entry filter of
its own.** The parent's cap was doing real work - it refused entries that had
drifted inside the edge between the estimate and the order. This has nothing
equivalent. Its entry selection is inherited wholesale from the parent's signal
plus its own depth gate, and if the parent's signal is noise then this buys
noise at the market. That is a genuine loosening relative to the parent and it
is not compensated anywhere.

## THE FAIR VALUE BAND: INHERITED UNCHANGED, AND THAT IS CORRECT

`min_fair_value = 0.10` / `max_fair_value = 0.90` gates `P(Up)`. The interval
is symmetric about 0.5, so `p in [0.10, 0.90]` implies `1 - p in [0.10, 0.90]`
exactly. The gate means the same thing for both sides and needs no adjustment.
It is inherited untouched.

What DOES change is where the entries land. The parent's cap is `fair - 4c`,
so its entries cluster LOW; the flipped side is roughly `1 - fair` plus the
overround, so this strategy's entries cluster HIGH. Two consequences:

  1. **The 3c stop is real here, and it was not always real for the parent.**
     `fair_value_arb_wide.py`'s "WHERE THE 5c STOP STOPS EXISTING" section
     applies to the parent below a 0.13 fair value, where `entry - max_loss` is
     at or below 0.00 and the effective max loss is the full premium. The
     inverse's entries sit at the other end of the book, so that band is
     largely unreachable for it. A small, genuine improvement.

  2. **The profit target can stop existing instead.** At an entry of 0.99 the
     1c target is 1.00 and no bid on a binary sits there, so the position can
     only ever exit at a scratch or a loss. Handled with ONE derived gate:
     entries above `1.00 - PRICE_TICK - min_profit` (= 0.98 at the inherited
     constants) are refused as `inverse_entry_above_profit_target_ceiling`.
     Derived from the tick grid and the instance's own `min_profit`, not tuned.

  3. Sizing: same RULE (`floor(max_notional / cap)`, capped at
     `target_shares`), different share COUNT, because the flipped side costs
     more. A 20-share parent entry at 0.42 becomes ~10 shares at 0.95. Per-trade
     dollar risk is therefore not comparable trade-for-trade with the parent,
     only rule-for-rule. `shares_capped_by_notional` says which happened.

## THE MODEL EXITS ARE DISABLED, AND THAT IS THE WHOLE POINT

The parent has six exit rules. Two of them - `model_stop` and `converged` -
are the FAIR VALUE MODEL talking. `model_stop` fires when
`fair_value <= entry + model_stop_margin`.

On an inverse position that condition is true on the first poll, always. We
bought the side the model prices BELOW our entry; that is the definition of
this strategy. Inheriting `model_stop` would close every single position within
one cycle of opening it, at the spread, and the hypothesis would never be
tested even once. `converged` has the same defect in a milder form: it fires
when the ask returns to the model's fair value, which for us is a level the ask
never left.

**So `manage_exit` withholds `fair_value` from the parent's rule chain.** Not a
reimplementation - the parent already skips both rules when `fair_value is
None`, so the rule ORDER is untouched and this variant cannot drift away from
it. Four rules remain live:

    window_close    hard deadline, unchanged
    price_stop      the TIERED stop on the ACTUAL entry - `base.STOP_TIERS`,
                    which for this strategy's high entries is 10c, not 3c
    profit_target   1c above the ACTUAL entry, unchanged
    time_stop       60s, unchanged

The observed fair value is still recorded, under
`model_fair_value_observed_not_acted_on`, so "we saw it and refused it" is
distinguishable from "we never had it" (convention 20).

Two consequences, both stated rather than discovered later:

  - **Exit populations are NOT comparable with the parent's.** The parent's
    rows include `model_stop` and `converged`; ours cannot. Any comparison of
    exit-reason distributions between the two is comparing a 6-rule strategy to
    a 4-rule one.
  - **The time-stop bucket will be much larger than the parent's**, because the
    two exits that used to catch a stalled position early are gone. That is the
    same bucket the win-rate section above already flagged as unmeasured. It is
    now the dominant unknown in this strategy, not a detail.

## STOPS AND TARGETS COME FROM THE FILL, NEVER FROM ARITHMETIC

`manage_exit` reads `position.avg_price` - the price the paper adapter actually
filled at, on the token actually bought. So the stop and the target are already
computed from the real entry of the real side with no code change at all, and
there is nowhere in this file that a stop could be derived as
`1 - parent_stop`. That is why `manage_exit` is inherited rather than rewritten:
the correct behaviour here is the parent's behaviour applied to a different
position, and rewriting it would be the only way to get it wrong.

## AUDITING THE FLIP (convention 22: a docstring is not a wiring test)

Every entry row this strategy emits carries, in features:

    inverted_from                 'PM_fair_value_arb'
    parent_intended_side          the side the MODEL chose
    outcome_side                  the side actually bought
    inverse_side_taken            the same value again, explicitly
    flip_applied                  True

so an analyst can assert `parent_intended_side != outcome_side` on the log
rather than trusting the class name. A row with `flip_applied` True and the two
sides equal is a bug, and it is greppable.

Attempt accounting: the parent's `evaluate` burns one of its
`max_trades_per_window` attempts at the moment it decides ENTER. If this
strategy then skips on the flipped book, that attempt is already spent. Correct
and deliberate - the parent counts ATTEMPTS, not fills, for exactly this class
of downstream refusal - but it means a window can be exhausted by three
inverse-side skips. Those rows carry
`inverse_attempt_consumed_on_skip=True` so they can be counted.

## STATUS

**NOT_TESTED (D-268).** Never scored, not once. Zero rows exist under
`PM_fair_value_arb_inverse`. NOT_TESTED means "could not run", never "ran and
found nothing" (convention 11).

Its provenance is the parent's provenance: a Reddit screenshot claiming a 99.3%
win rate that is not our data and never was. Every row still carries
`claimed_win_rate_is_unverified_vendor_number=True` - inverting a strategy does
not launder where it came from. The 21.2% above is ours and IS measured; the
99.3% is not.

KILL CONDITION: trailing-50 win rate below 75% once 50 trades exist. 75% is
this strategy's OWN break-even at a 1c target and a 3c stop
(`max_loss / (min_profit + max_loss)`, and see `breakeven_win_rate`, which
computes it from the instance) - it is not a tuned number and it is not the
parent's number by coincidence, it is the same payoff geometry. Scored by
`backtest/polymarket_harness.py`, CLOSED and RESOLVED trades kept as two
populations and never pooled, and kept separate from every other
`PM_fair_value_arb*` row - shared code path, different population.

**"It beats the parent" is explicitly NOT a kill condition and must never be
substituted for one.** The parent lost $28.03 over 33 closes. Beating it is a
bar on the floor: both of these can be, and on the arithmetic above probably
are, losers. This strategy is judged against 75% and against nothing else.
"""
import math
from typing import Optional

from strategies.polymarket.base import (Decision, Leg, MarketContext, opposite,
                                        tiered_stop_features)
from strategies.polymarket.fair_value_arb import (PRICE_TICK, ExitDecision,
                                                  FairValueArb)

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

#: The strategy whose signal this reads backwards. Stamped on every row so the
#: relationship is in the data and not only in the class name.
PARENT_STRATEGY_NAME = 'PM_fair_value_arb'

#: How far above the flipped side's best ask the entry limit is placed. One
#: tick. This REPLACES the parent's model-derived cap, which is unsatisfiable on
#: the flipped side - see the module docstring. It is a slippage allowance, not
#: an edge requirement, and it does not filter anything.
#: EXPIRY: no measurement stands behind it. The number that would move it is a
#: measured distribution of `effective_ask - best_ask` on filled inverse rows.
INVERSE_SLIPPAGE_ALLOWANCE = PRICE_TICK

# -- the measured parent numbers, so a reader never has to trust the prose -----
# Source: research/polymarket_paper/polymarket_paper_log.csv, PM_fair_value_arb,
# action == CLOSE. Re-derived from the log, not copied from a summary. 33 trades
# is a shrug, not a verdict (convention 7).
PARENT_MEASURED = {
    'source': 'research/polymarket_paper/polymarket_paper_log.csv',
    'closes': 33,
    'wins': 7,
    'win_rate': 7 / 33,
    'avg_win_usdc': 0.9614,
    'avg_loss_usdc': -1.3371,
    'total_pnl_usdc': -28.0340,
    'exits': {'price_stop': 26, 'profit_target': 7},
    'sides': {'Down': 17, 'Up': 16},
    'sample_is_a_shrug_not_a_verdict': True,
}


def ceil_to_tick(price: float, tick: float = PRICE_TICK) -> float:
    """Snap a limit UP onto the tick grid.

    The mirror of the parent's `floor_to_tick`, and it needs its own epsilon for
    the same floating-point reason in the other direction: `0.43 / 0.01` is
    42.99999999999999, so a bare ceil on a price already on the grid would push
    it up a full tick. On a strategy whose entire claimed edge is 0.16c, one
    tick is six times the edge.

    UP, not down, because this cap is a marketable limit and flooring it could
    place the limit below the ask it is meant to lift.
    """
    if tick <= 0:
        return price
    steps = math.ceil(price / tick - 1e-9)
    decimals = max(0, -math.floor(math.log10(tick)))
    return round(steps * tick, decimals)


class FairValueArbInverse(FairValueArb):
    """`FairValueArb`'s signal, read backwards.

    Same entry timing, same edge threshold, same fair value model, same sizing
    rule, same depth gate. The side is flipped and re-priced against its own
    book; the entry cap is book-derived rather than model-derived (the parent's
    is unsatisfiable on the flipped side); and the two model-driven exits are
    withheld, because they would close every position on the first poll.

    Kill condition: trailing-50 win rate below 75% once 50 trades exist - its
    own break-even at a 1c target and a 3c stop. Beating the parent is NOT a
    kill condition; both can be losers. See the module docstring.
    """

    strategy_name = 'PM_fair_value_arb_inverse'
    paper_mode = PAPER_MODE

    def __init__(self,
                 inverse_slippage_allowance: float = INVERSE_SLIPPAGE_ALLOWANCE,
                 **kwargs):
        # Every parent constant is passed straight through. This variant moves
        # NONE of them: the hypothesis is inverted, the parameters are not, and
        # a variant that changed both would tell you nothing about either.
        super().__init__(**kwargs)
        self.inverse_slippage_allowance = float(inverse_slippage_allowance)

    # -- derived numbers ----------------------------------------------------

    @property
    def breakeven_win_rate(self) -> float:
        """`max_loss / (min_profit + max_loss)`, computed from the INSTANCE.

        0.75 at the inherited constants, which is also the kill line. Computed
        and never written down: a constant restating 0.75 would go stale the
        first time somebody constructed this class with a different `max_loss`
        and would then be quoted as if it had been measured (convention 22).
        """
        denom = self.min_profit + self.max_loss
        return float('nan') if denom <= 0 else self.max_loss / denom

    @property
    def max_entry_price(self) -> float:
        """Highest entry at which the profit target is still reachable.

        `1.00 - PRICE_TICK - min_profit`. Above it, `entry + min_profit` lands
        at or above the top of the price grid, no bid can ever sit there, and
        the position is structurally incapable of hitting its own target - it
        can only scratch, stop, or time out. Derived from the tick grid and the
        instance's own `min_profit`, not tuned.

        The parent does not need this gate: its entries are capped at
        `fair - edge_threshold` with fair at most 0.90, so it cannot reach here.
        This strategy's entries cluster at the top of the book, so it can.
        """
        return round(1.0 - PRICE_TICK - self.min_profit, 6)

    # -- entry --------------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        """The parent's decision, with the side flipped and re-priced.

        Structure, and why it is this shape: the parent's `evaluate` is called
        UNCHANGED and its answer is then transformed. Every SKIP it produces
        passes through with the parent's own reason, so the two strategies see
        identical entry timing, identical thresholds and identical model output
        by construction rather than by a claim. Only the ENTER branch is
        rewritten, and it is rewritten against the flipped side's own book.
        """
        decision = super().evaluate(ctx)
        feats = decision.features
        feats['inverted_from'] = PARENT_STRATEGY_NAME
        feats['inverse_model_exits_disabled'] = True
        feats['flip_applied'] = False

        if not decision.is_entry or not decision.legs:
            # A parent SKIP is an inverse SKIP, with the parent's reason intact.
            # There is nothing to invert about "no orderbook" or "too late in
            # the window", and giving those our own reason strings would split
            # one cause across two counters for no gain.
            return decision

        parent_leg = decision.legs[0]
        intended = parent_leg.outcome_side
        flipped = opposite(intended)

        feats['parent_intended_side'] = intended
        feats['parent_entry_cap'] = feats.get('entry_cap')
        feats['parent_best_ask'] = feats.get('best_ask')
        # The parent stamps `best_bid` and `spread` for the side IT chose. This
        # class takes the other side, so those two are re-read from the flipped
        # book below and would otherwise be a silent side mismatch: an
        # inverse-side ask sitting next to a parent-side bid, which subtract to
        # a spread that was never quoted on either book.
        feats['parent_best_bid'] = feats.get('best_bid')
        feats['parent_spread'] = feats.get('spread')
        feats['parent_effective_ask'] = feats.get('effective_ask')
        feats['parent_side_fair_value'] = feats.get('side_fair_value')
        feats['parent_raw_edge'] = feats.get('raw_edge')
        feats['parent_shares'] = feats.get('shares')

        def skip(reason, **extra):
            """A refusal on the FLIPPED side. Never the parent's reason.

            The parent's side passed every gate; this one did not. Pooling the
            two would make "the market had no opportunity" and "the opportunity
            existed on the side we refuse to take" one number (convention 20).
            """
            feats.update(extra)
            # The parent already burned an attempt deciding ENTER. Say so on the
            # row: three of these exhaust a window without a single fill.
            feats['inverse_attempt_consumed_on_skip'] = True
            decision.action = 'SKIP'
            decision.reason = reason
            decision.legs = []
            return decision

        if not flipped or flipped == intended:
            # `opposite()` returns its input for a label it does not recognise.
            # Emitting the parent's own side under this class name would be the
            # single worst failure available here: an un-inverted trade logged
            # as an inverted one. Refuse instead of guessing.
            return skip('inverse_side_unresolvable',
                        inverse_unresolved_label=intended)

        feats['outcome_side'] = flipped
        feats['inverse_side_taken'] = flipped
        feats['flip_applied'] = True

        # The model's own view of the side we are taking. NOT used as a gate and
        # NOT used as a cap - it is the number we are betting against - but
        # recorded, because the whole hypothesis is about its calibration.
        inverse_fair = (feats.get('fair_value_up') if flipped in ('Up', 'Yes')
                        else feats.get('fair_value_down'))
        feats['side_fair_value'] = inverse_fair
        feats['inverse_side_fair_value'] = inverse_fair

        book = ctx.book(flipped)
        if book is None:
            return skip('inverse_side_no_orderbook')
        best_ask = book.best_ask
        if best_ask is None:
            # Bids-only is the same fact as empty for a BUY: nothing to lift.
            return skip('inverse_side_no_ask')
        feats['best_ask'] = best_ask
        feats['inverse_best_ask'] = best_ask

        # Same LOGGING-ONLY pair the parent added, re-read on the side actually
        # taken. No threshold, gate or exit rule reads either one. The parent's
        # values are preserved above under `parent_*` rather than overwritten,
        # because the two books' spreads are separate facts and the inverse
        # hypothesis is precisely that the spread does NOT invert.
        inverse_best_bid = book.best_bid
        feats['best_bid'] = inverse_best_bid
        feats['inverse_best_bid'] = inverse_best_bid
        feats['spread'] = (None if inverse_best_bid is None
                           else round(best_ask - inverse_best_bid, 6))
        feats['inverse_spread'] = feats['spread']

        # The overround, MEASURED on this book rather than assumed at 3c. This
        # is the number that proves `1 - parent_entry` would have been a lie.
        parent_ask = feats.get('parent_best_ask')
        parent_eff = feats.get('parent_effective_ask')
        if parent_ask is not None:
            feats['ask_sum'] = round(best_ask + parent_ask, 6)
            feats['overround'] = round(best_ask + parent_ask - 1.0, 6)

        cap = min(ceil_to_tick(best_ask + self.inverse_slippage_allowance), 1.0)
        feats['entry_cap'] = cap
        feats['inverse_entry_cap'] = cap
        feats['inverse_cap_is_book_derived_not_model_derived'] = True
        feats['inverse_slippage_allowance'] = self.inverse_slippage_allowance
        if cap < PRICE_TICK:
            return skip('inverse_side_unpriceable_cap')

        # Same depth gate, same band, applied to the flipped book. A 4c gap
        # against a 6-share top level is one stale quote on either side.
        depth_limit = round(best_ask + self.depth_band, 6)
        depth = book.ask_depth(depth_limit)
        feats['ask_depth_within_band'] = depth
        feats['inverse_ask_depth_within_band'] = depth
        if depth < self.min_book_depth_shares:
            return skip('inverse_side_insufficient_book_depth')

        # Same sizing RULE at the flipped cap. Different share COUNT, because
        # the flipped side costs more - see the docstring.
        affordable = int(math.floor(self.max_notional_usdc / cap + 1e-9))
        shares = min(self.target_shares, affordable)
        feats['affordable_shares_at_cap'] = affordable
        feats['shares'] = shares
        feats['shares_capped_by_notional'] = shares < self.target_shares
        if shares < self.min_shares:
            # Could not run, did not lose (convention 11).
            return skip('inverse_side_unsizable_at_notional_cap')

        effective = self._effective_ask(book, shares, cap)
        feats['effective_ask'] = (None if effective is None
                                  else round(effective, 4))
        feats['inverse_effective_ask'] = feats['effective_ask']
        if effective is None:
            # A partial fill is not an entry (convention 12).
            return skip('inverse_side_unfillable_at_cap')
        if effective > cap:
            return skip('inverse_side_effective_ask_above_cap')
        if not (0.0 < effective <= 1.0):
            # An entry at 0.00 has no stop strictly below it (convention 8) and
            # an entry above 1.00 is not a binary at all. Both are bugs upstream.
            return skip('inverse_side_unpriceable_fill')

        feats['max_entry_price'] = self.max_entry_price
        if effective > self.max_entry_price:
            return skip('inverse_entry_above_profit_target_ceiling',
                        profit_target_would_be=round(
                            effective + self.min_profit, 4))

        # What the naive arithmetic would have claimed, next to what the book
        # actually charged. On the record, on the row, every time.
        if parent_eff is not None:
            naive = round(1.0 - float(parent_eff), 4)
            feats['naive_inverse_price_one_minus_parent'] = naive
            feats['overround_cost_vs_naive'] = round(effective - naive, 4)

        # The parent's `realized_edge` is `fair - effective`. Ours is NEGATIVE
        # by construction: we are paying above the model's fair value on
        # purpose. Reported with its sign and flagged, so nobody reads a
        # negative edge here as a malfunction.
        if inverse_fair is not None:
            realized = float(inverse_fair) - effective
            feats['realized_edge'] = round(realized, 4)
            feats['realized_edge_bps'] = (round(realized / effective * 10_000, 1)
                                          if effective > 0 else None)
        feats['realized_edge_is_negative_by_construction'] = True

        # Confidence IS the model's fair value for the side taken - which is
        # LOW, because the model dislikes this side. That is not a bug and it is
        # not a win rate: a high-conviction inverse trade is one where the model
        # is most sure we are wrong, which is precisely the trade this
        # hypothesis wants.
        feats['confidence'] = inverse_fair if inverse_fair is not None else 0.0
        feats['confidence_is_model_output_not_measured_win_rate'] = True
        feats['confidence_is_the_model_we_are_betting_against'] = True

        feats['profit_target_price'] = round(effective + self.min_profit, 4)
        # The TIERED stop, from the flipped side's own walked fill. This
        # strategy's entries cluster at the top of the book (measured mean
        # 0.6907 over 176 fills), so it sits in the `>= 0.50` tier almost
        # always and its stop widened from 0.03 to 0.10 - the largest change
        # the tiering makes anywhere in the family, and the opposite direction
        # from the one the lesson that motivated it was about. Stated here
        # rather than discovered from a P&L column later.
        feats.update(tiered_stop_features(effective, flipped))
        feats['stop_and_target_from_actual_fill_not_mirrored'] = True
        feats['breakeven_win_rate'] = round(self.breakeven_win_rate, 6)
        feats['breakeven_win_rate_at_tiered_stop'] = round(
            self.breakeven_win_rate_at(effective, flipped), 6)
        feats['breakeven_win_rate_if_held'] = round(effective, 4)
        feats['notional_usdc'] = round(shares * effective, 4)
        feats['parent_measured_win_rate'] = round(PARENT_MEASURED['win_rate'], 4)
        feats['parent_measured_closes'] = PARENT_MEASURED['closes']
        feats['parent_sample_is_a_shrug_not_a_verdict'] = True

        decision.legs = [Leg(outcome_side=flipped,
                             limit_price=cap,
                             order_type='taker',
                             shares=shares,
                             expected_price=effective)]
        return decision

    @staticmethod
    def _effective_ask(book, shares: float, cap: float) -> Optional[float]:
        """The parent's fill simulation, on the flipped book.

        Indirected through one method so there is exactly ONE walk-the-book call
        in this file and a test can point it at a refusing book without patching
        the shared helper for every strategy in the package.
        """
        from strategies.polymarket.base import effective_ask_for
        return effective_ask_for(book, shares, cap)

    # -- exit ---------------------------------------------------------------

    def manage_exit(self, position, book, now: float,
                    fair_value: Optional[float] = None) -> ExitDecision:
        """The parent's exit rules with the two MODEL rules withheld.

        `fair_value` is accepted and then NOT passed down. The parent already
        skips `model_stop` and `converged` when it is None, so the rule chain
        and the rule ORDER are the parent's, untouched - this variant cannot
        drift away from them.

        Why: `model_stop` fires when `fair_value <= entry + model_stop_margin`,
        and an inverse position is BY DEFINITION opened on the side the model
        prices below our entry. It would fire on the first poll of every
        position ever opened, close it at the spread, and the hypothesis would
        never be tested once.

        Stops and targets need no adjustment here at all: the parent computes
        them from `position.avg_price`, which is the real fill on the real token
        we really bought. There is no mirrored arithmetic anywhere on this path.
        """
        decision = super().manage_exit(position, book, now, fair_value=None)
        feats = decision.features
        feats['inverted_from'] = PARENT_STRATEGY_NAME
        feats['inverse_model_exits_disabled'] = True
        # Seen and refused, not missing. `fair_value` above stays None because
        # nothing acted on it; the observation lands under its own key so the
        # two cases are countable apart (convention 20).
        feats['model_fair_value_observed_not_acted_on'] = (
            None if fair_value is None else round(float(fair_value), 6))
        feats['inverse_active_exit_rules'] = ['window_close', 'price_stop',
                                              'profit_target', 'time_stop']
        return decision


# Re-exported so a reader can see the parent's numbers without opening the log.
PARENT_WIN_RATE_MEASURED = PARENT_MEASURED['win_rate']
PARENT_CLOSES_MEASURED = PARENT_MEASURED['closes']
PARENT_TOTAL_PNL_MEASURED = PARENT_MEASURED['total_pnl_usdc']
