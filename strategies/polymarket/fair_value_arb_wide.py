"""Fair Value Arb, WIDE: fewer trades, bigger target, bigger stop.

A parameter variant of `FairValueArb`. Same code path, same fair value model,
same exit rule ORDER - only four constants move. It exists so the payoff
geometry can be varied against the SAME entry model, which is the only way to
tell "the model has edge and the targets were wrong" apart from "the model has
no edge". Nothing here is a new hypothesis about the market.

    edge_threshold        0.04 -> 0.08     demand twice the mispricing
    min_profit            0.01 -> 0.03     ask for a real repricing
    max_loss              0.03 -> 0.05     give it room to get there
    max_trades_per_window    3 -> 2        fewer, bigger attempts

Everything else is inherited unchanged, deliberately. A variant that moved six
constants would tell you nothing about which one mattered.

## THE ARITHMETIC (read this before reading a win rate)

    EV per trade = w * 0.03 - (1 - w) * 0.05 = 0.08w - 0.05
    break-even   = w = 62.5%

against the parent's 75%. That is a genuinely better reward:risk - 3:5 rather
than 1:3 - and it is the honest reason to run this variant.

**It is NOT a free improvement, and the trap here is convention 17 in reverse.**
The break-even calculation holds w constant while the target moves. It will not
be constant. The parent's module docstring shows why: we enter by lifting the
ask and exit by hitting the bid, so on a book with a 1-2c spread a 3c profit
target is a 4-5c favourable repricing request. Asking for 4-5c inside 60 seconds
is a materially harder question than asking for 2-3c, and if the win rate falls
by more than 12.5 points this variant is WORSE than the parent despite the
better-looking break-even. The comparison that decides it is realised w against
realised w, never break-even against break-even.

The 8c edge threshold cuts the other way and is the part most likely to help: it
is a selection filter, not a payoff change. Fewer, better-selected entries is
the one lever here that can raise w rather than merely repricing it.

## WHERE THE 5c STOP STOPS EXISTING

`min_fair_value` is inherited at 0.10, and the entry cap is `fair - 0.08`. So a
fair value between 0.10 and 0.13 produces an entry between 0.02 and 0.05, and
`entry - 0.05` is at or below 0.00 - a stop the bid can never reach. In that
band the effective max loss is the FULL PREMIUM, not 5c, and the break-even
above does not describe those trades. The parent had the same shape with 3c and
a 0.10 floor; widening the stop widens the band in which it is inoperative.
Rows still carry `stop_price`, so the band is measurable rather than assumed.
Not fixed here: raising `min_fair_value` would change the entry population and
make this variant non-comparable to the parent, which is the whole point of it.

## STATUS

NOT_TESTED (D-268), exactly like the parent. It has never been scored. Every row
it emits still carries `claimed_win_rate_is_unverified_vendor_number=True`,
because it inherits the parent's provenance and the parent's provenance is a
Reddit screenshot.

KILL CONDITION: trailing-50 win rate below 62.5% once 50 trades exist - the
break-even above, so this is "the variant does not clear its own arithmetic" and
not a tuned number - OR a realised win rate more than 12.5 points below the
parent's over the same 50-trade window, which is the condition under which the
wider target has cost more than it paid. Either clause fires alone. Scored by
`backtest/polymarket_harness.py`, which must keep CLOSED and RESOLVED trades in
separate populations, and must keep this strategy's rows separate from
`PM_fair_value_arb`'s - they share a code path, not a population.
"""
from strategies.polymarket.fair_value_arb import (MAX_LOSS,
                                                  MAX_TRADES_PER_WINDOW,
                                                  MIN_PROFIT, FairValueArb)

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

#: Twice the parent's 4c. A selection filter, not a payoff change - see the
#: docstring. EXPIRY: this is an assumption with no measurement behind it, and
#: the only evidence that could move it is a scored win rate at 8c.
EDGE_THRESHOLD = 0.08

#: 3c, against the parent's 1c. On a 1-2c spread this is a 4-5c repricing
#: request. Stated in the docstring because it is the reason this variant can
#: fail while looking better on paper.
MIN_PROFIT_WIDE = 0.03

#: 5c, against the parent's 3c. Inoperative below a 0.13 fair value - see the
#: docstring.
MAX_LOSS_WIDE = 0.05

#: Two attempts per window, not three. Attempts, not fills; the parent's
#: docstring explains why that distinction is stamped on every row.
MAX_TRADES_PER_WINDOW_WIDE = 2


class FairValueArbWide(FairValueArb):
    """`FairValueArb` with a wider target, a wider stop and a higher bar.

    A thin parameter variant: no logic is overridden, only defaults passed
    through `super().__init__()`. If this class ever grows a rule the parent
    does not have, it stops being a variant and its results stop being
    comparable to the parent's.

    Kill condition: trailing-50 win rate below 62.5% (its own break-even), OR a
    win rate more than 12.5 points below the parent's over the same 50 trades.
    See the module docstring for the arithmetic behind both.
    """

    strategy_name = 'PM_fair_value_arb_wide'
    paper_mode = PAPER_MODE

    def __init__(self, edge_threshold: float = EDGE_THRESHOLD,
                 min_profit: float = MIN_PROFIT_WIDE,
                 max_loss: float = MAX_LOSS_WIDE,
                 max_trades_per_window: int = MAX_TRADES_PER_WINDOW_WIDE,
                 **kwargs):
        # Named explicitly rather than folded into **kwargs so that a reader of
        # this signature sees exactly which four constants differ from the
        # parent, and so a caller can still override any of them.
        super().__init__(edge_threshold=edge_threshold,
                         min_profit=min_profit,
                         max_loss=max_loss,
                         max_trades_per_window=max_trades_per_window,
                         **kwargs)

    @property
    def breakeven_win_rate(self) -> float:
        """`max_loss / (min_profit + max_loss)`, computed from the INSTANCE.

        Computed, never written down: a constant restating 0.625 would go stale
        the first time somebody constructs this class with a different
        `max_loss` and would then be quoted as if it had been measured
        (convention 22 - a docstring is not a wiring test).
        """
        denom = self.min_profit + self.max_loss
        return float('nan') if denom <= 0 else self.max_loss / denom


# Re-exported so a reader can see at a glance what the parent's numbers were.
PARENT_MIN_PROFIT = MIN_PROFIT
PARENT_MAX_LOSS = MAX_LOSS
PARENT_MAX_TRADES_PER_WINDOW = MAX_TRADES_PER_WINDOW
