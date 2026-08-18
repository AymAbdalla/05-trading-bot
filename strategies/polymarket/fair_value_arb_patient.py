"""Fair Value Arb, PATIENT: hold through the first 15 seconds, on purpose.

A parameter variant of `FairValueArb` plus ONE piece of new logic - a minimum
holding time, which the parent does not have. Same fair value model, same entry
path, same exit rule ORDER.

    edge_threshold        0.04 -> 0.06     a slightly higher bar
    min_profit            0.01 -> 0.02     ask for a 2c repricing
    max_loss              0.03 -> 0.03     UNCHANGED
    max_trades_per_window    3 -> 2        fewer attempts
    time_stop_sec         60.0 -> 120.0    the top of the 30-120s band
    min_hold_sec           n/a -> 15.0     NEW. See the warning below.

## THE ARITHMETIC

    EV per trade = w * 0.02 - (1 - w) * 0.03 = 0.05w - 0.03
    break-even   = w = 60%

against the parent's 75%, on a 2:3 reward:risk rather than 1:3. But read the
next section before quoting that 60%, because the min-hold makes the 0.03 in
that formula a number the strategy does not actually enforce for the first 15
seconds of a position's life.

## ############ THE MIN-HOLD WIDENS REAL RISK. DO NOT HIDE THIS. ############

Convention 8: every entry needs a stop strictly below entry. On a binary that is
satisfied structurally - a losing share is worth exactly 0.00. The 3c
`price_stop` is a TIGHTER discretionary stop layered on top of that structural
floor, and `min_hold_sec` DEFERS IT BY UP TO 15 SECONDS.

For those 15 seconds the only stop this position has is the structural one, at
0.00. So:

    declared max loss per position  = max_loss * shares = 0.03 * shares
    ACTUAL worst case in the first 15s
                                    = entry_price * shares
                                    = the full premium
                                    <= max_notional_usdc = $10.00

Worked, at the two ends of the inherited tradeable band:

    fair 0.60 -> cap 0.54, 18 shares. declared max loss $0.54.
                 worst case $9.72.   18x
    fair 0.90 -> cap 0.84, 11 shares. declared max loss $0.33.
                 worst case $9.24.   28x

So the honest statement is: **the min-hold can turn a declared $0.33-$0.60 loss
into a loss of up to $10.00 on a single position, up to roughly 28x the declared
max_loss.** That is the arithmetic worst case, requiring the bid to collapse to
zero inside 15 seconds; the realistic case is a stop that fills several cents
worse than 3c below entry, which is smaller and far more likely and is exactly
the outcome that quietly invalidates the 60% break-even above.

The break-even calculation is therefore an UPPER BOUND on this variant's odds,
not a description of them. Every row carries `min_hold_sec` and every suppressed
exit carries `min_hold_suppressed_reason`, so the realised cost of the deferral
is measurable after the fact rather than argued about: sum the realised loss on
closes whose `min_hold_suppressed_reason == 'price_stop'` and compare it to
`0.03 * shares`. If that gap is material the min-hold is refuted by its own log.

## WHAT IS SUPPRESSED, AND WHAT IS NOT

Suppressed inside the first 15 seconds (both DISCRETIONARY - they are opinions
about price, and the whole point of the variant is to stop acting on the first
15 seconds of noise):

    price_stop      the bid fell 3c below entry
    profit_target   the bid pays entry + 2c

NOT suppressed, ever, at any age:

    window_close        HARD DEADLINE. Under 30s left the position stops being a
                        mispricing trade and becomes a directional bet on the
                        resolution. Suppressing this could strand a position
                        past its market close, where it resolves 1.00/0.00 with
                        no sell path at all - see CLAUDE.md's kill-switch note
                        that a Polymarket binary held to resolution cannot be
                        flattened in paper mode. This is the one exit whose
                        suppression could lose the entire premium as a matter of
                        routine rather than as a tail.
    no_bid_liquidity    already a HOLD, and an UNSELLABLE one. Untouched so its
                        count stays comparable with the parent's.
    no_orderbook        ditto.
    unreadable_position ditto - a bookkeeping fault, not a trade to manage.
    converged           reachable inside 15s, and left alone: the spec names the
                        profit target and the price stop, not this.
    model_stop          ditto.
    time_stop           unreachable inside 15s anyway at 120.0s.

A suppressed exit returns HOLD with reason `min_hold_not_met` - its OWN reason,
never pooled with `waiting_for_convergence` or any other market condition
(conventions 11 and 20). `waiting_for_convergence` means "the rules ran and none
fired"; `min_hold_not_met` means "a rule fired and we refused it". Two causes,
two numbers.

**Known short-circuit, stated rather than discovered later.** Suppression is
applied to the parent's ANSWER, not woven into its rule chain, so a cycle in
which `profit_target` fires is held without going on to test `converged` and
`model_stop` below it. Those are not lost - the loop re-polls every position
every cycle, so they are re-tested moments later and unconditionally once the
15s elapses. The cost is at most a few seconds of deferral on a model exit, and
the benefit is that this variant cannot drift away from the parent's rule
ordering, which is what would make the two non-comparable.

An UNKNOWN age (a position with no `opened_ts`) is NOT suppressed. We cannot
prove the position is inside its min-hold, and the safe direction to fail is
toward taking the stop.

## THE 120s TIME STOP IS MOSTLY UNREACHABLE, AND THAT IS NOT A BUG

`min_entry_seconds_remaining` is inherited at 60.0 and `window_close_exit_sec`
at 30.0, so a position can be opened with as little as 60s of window left and
will be cut by `window_close` after 30s. A 120s time stop only ever binds on
positions opened early in a window. The parent reports the truncation as
`holding_seconds_available` on every entry row; read that before concluding the
time stop did anything. Raising `min_entry_seconds_remaining` would fix it and
is NOT done here, because it would change the entry population and this variant
would stop being comparable to the parent - which is the only reason it exists.

Combined with the 15s min-hold, a position opened at 60s remaining has a
15s floor and a 30s ceiling: half its life is unstoppable by the price rules.
That is the sharpest form of the risk stated above.

## STATUS

NOT_TESTED (D-268). Never scored. Inherits the parent's provenance, which is a
Reddit screenshot, and stamps
`claimed_win_rate_is_unverified_vendor_number=True` on every row accordingly.

KILL CONDITION: trailing-50 win rate below 60% once 50 trades exist (its own
break-even), OR - and this clause is the one that tests the min-hold rather than
the strategy - mean realised loss on closes carrying
`min_hold_suppressed_reason == 'price_stop'` worse than 1.5x `max_loss * shares`
over 20 such closes, which is the deferral costing more than half the stop again
and is the falsification of the whole idea. Either clause fires alone. Scored by
`backtest/polymarket_harness.py`, CLOSED and RESOLVED populations kept separate,
and kept separate from `PM_fair_value_arb`'s rows - shared code path, different
population.
"""
from typing import Optional

from strategies.polymarket.fair_value_arb import (MAX_LOSS,
                                                  MAX_TRADES_PER_WINDOW,
                                                  MIN_PROFIT, TIME_STOP_SEC,
                                                  ExitDecision, FairValueArb)

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

#: 6c, between the parent's 4c and the wide variant's 8c.
EDGE_THRESHOLD = 0.06

#: 2c. Also the number the parent's KILL CONDITION asks for as an average, so
#: this variant's profit target and its parent's kill line finally agree - the
#: "spec tension" the parent's docstring names is not present here.
MIN_PROFIT_PATIENT = 0.02

#: NOMINAL geometry ONLY. **This is no longer the stop** (2026-08-18). The stop
#: is `strategies.polymarket.base.tiered_stop_price`, shared by all six
#: exit-managing Polymarket strategies and keyed to the entry price. Only
#: `breakeven_win_rate` still reads this.
#:
#: The min-hold warning above is UNCHANGED and if anything sharper: for the
#: first `min_hold_sec` the only stop a position has is the structural 0.00
#: floor, and that is now true of the tiered stop exactly as it was of the 3c
#: one. `manage_exit` here defers the parent's ANSWER, so it defers whichever
#: stop the parent computed, tiered or not.
MAX_LOSS_PATIENT = MAX_LOSS

#: Two attempts per window. Attempts, not fills.
MAX_TRADES_PER_WINDOW_PATIENT = 2

#: 120s, the TOP of the brief's own 30-120s correction band, against the
#: parent's 60s which sits in the middle of it. Mostly unreachable - see the
#: module docstring.
TIME_STOP_SEC_PATIENT = 120.0

#: Seconds after entry during which the DISCRETIONARY exits are refused. This
#: is the only piece of logic in this file that the parent does not have, and it
#: is the only one that widens real risk. EXPIRY: it is an assumption with no
#: measurement behind it. The measurement that would move it is the realised
#: loss on `min_hold_suppressed_reason == 'price_stop'` closes.
MIN_HOLD_SEC = 15.0

#: Exactly the two exits deferred by the min-hold. A tuple, not a substring
#: match: `price_stop` and `model_stop` both end in `_stop` and only one of them
#: belongs here.
SUPPRESSED_DURING_MIN_HOLD = ('price_stop', 'profit_target')

#: The reason string a deferred exit is reported under. Its own name, never
#: pooled with a market condition (conventions 11 and 20).
MIN_HOLD_REASON = 'min_hold_not_met'


class FairValueArbPatient(FairValueArb):
    """`FairValueArb` that refuses to be shaken out in the first 15 seconds.

    Four constants move and one rule is added: `manage_exit` defers the price
    stop and the profit target until the position is `min_hold_sec` old. Hard
    safety exits - `window_close` above all - are never deferred. See the module
    docstring for exactly what is and is not suppressed, and for the worst-case
    loss the deferral creates.

    Kill condition: trailing-50 win rate below 60% (its own break-even), OR mean
    realised loss on min-hold-deferred stops worse than 1.5x the declared stop
    over 20 such closes.
    """

    strategy_name = 'PM_fair_value_arb_patient'
    paper_mode = PAPER_MODE

    def __init__(self, edge_threshold: float = EDGE_THRESHOLD,
                 min_profit: float = MIN_PROFIT_PATIENT,
                 max_loss: float = MAX_LOSS_PATIENT,
                 max_trades_per_window: int = MAX_TRADES_PER_WINDOW_PATIENT,
                 time_stop_sec: float = TIME_STOP_SEC_PATIENT,
                 min_hold_sec: float = MIN_HOLD_SEC,
                 **kwargs):
        super().__init__(edge_threshold=edge_threshold,
                         min_profit=min_profit,
                         max_loss=max_loss,
                         max_trades_per_window=max_trades_per_window,
                         time_stop_sec=time_stop_sec,
                         **kwargs)
        #: Not a parent kwarg. The parent has no concept of a holding floor.
        self.min_hold_sec = float(min_hold_sec)

    @property
    def breakeven_win_rate(self) -> float:
        """`max_loss / (min_profit + max_loss)`, computed from the INSTANCE.

        An UPPER BOUND on this variant's odds, not a description of them: it
        uses the DECLARED `max_loss`, which the min-hold does not enforce for
        the first `min_hold_sec` of a position's life. See the module docstring.
        """
        denom = self.min_profit + self.max_loss
        return float('nan') if denom <= 0 else self.max_loss / denom

    @staticmethod
    def _age(position, now: float) -> Optional[float]:
        """Seconds since the fill, or None when the position will not say.

        None is NOT treated as young. A position that cannot prove it is inside
        its min-hold gets its stop, because the safe direction to fail on a stop
        is toward taking it.
        """
        opened_ts = getattr(position, 'opened_ts', None)
        if opened_ts is None:
            return None
        try:
            return float(now) - float(opened_ts)
        except (TypeError, ValueError):
            return None

    def manage_exit(self, position, book, now: float,
                    fair_value: Optional[float] = None) -> ExitDecision:
        """The parent's decision, with the two discretionary exits deferred.

        Applied to the parent's ANSWER rather than woven into its rule chain, so
        this variant cannot drift away from the parent's documented rule
        ordering. The known consequence is a few seconds of extra deferral on
        `converged` / `model_stop` in the cycles where a suppressed exit fired
        first; the loop re-polls every position every cycle, so nothing is lost.

        Never raises for a reason the parent would not also raise for.
        """
        decision = super().manage_exit(position, book, now,
                                       fair_value=fair_value)

        age = self._age(position, now)
        # Stamped on EVERY row, HOLDs and non-suppressed EXITs included. A
        # counter that only appears when it bites cannot be used to work out how
        # often it did not (convention 20).
        decision.features['min_hold_sec'] = self.min_hold_sec
        decision.features['min_hold_met'] = (
            None if age is None else bool(age >= self.min_hold_sec))
        decision.features['min_hold_age_unknown'] = age is None

        if age is None or age >= self.min_hold_sec:
            return decision
        if not decision.is_exit:
            return decision
        if decision.reason not in SUPPRESSED_DURING_MIN_HOLD:
            # window_close and every other hard safety exit lands here and is
            # returned untouched. This branch is the safety guarantee.
            decision.features['min_hold_did_not_suppress'] = decision.reason
            return decision

        return ExitDecision(
            'HOLD', MIN_HOLD_REASON,
            position_id=decision.position_id,
            features=dict(
                decision.features,
                # The exit we refused, named. Two causes never share one number.
                min_hold_suppressed_reason=decision.reason,
                min_hold_suppressed_limit_price=decision.limit_price,
                min_hold_seconds_to_go=round(self.min_hold_sec - age, 3),
                # Stated on the row, not only in the docstring: for these
                # seconds the only stop this position has is the structural
                # 0.00 floor, so the loss is bounded by the premium and not by
                # max_loss.
                stop_deferred_worst_case_is_full_premium=(
                    decision.reason == 'price_stop'),
            ))


# Re-exported so a reader can see at a glance what the parent's numbers were.
PARENT_MIN_PROFIT = MIN_PROFIT
PARENT_MAX_LOSS = MAX_LOSS
PARENT_MAX_TRADES_PER_WINDOW = MAX_TRADES_PER_WINDOW
PARENT_TIME_STOP_SEC = TIME_STOP_SEC
