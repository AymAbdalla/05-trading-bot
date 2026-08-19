"""Fair Value Arb, HFT: fire on small gaps, take them off fast.

A parameter variant of `FairValueArb`. Same code path, same fair value model,
same exit rule ORDER. Five constants move; no logic is added.

    edge_threshold           0.04 -> 0.02   fire on half the mispricing
    max_loss                 0.03 -> 0.02   a tighter stop
    min_profit               0.01 -> 0.01   UNCHANGED
    max_trades_per_window       3 -> 5      more attempts per window
    min_book_depth_shares      50 -> 100    a TIGHTER depth gate
    time_stop_sec            60.0 -> 30.0   cut it at the bottom of the band

## THE ARITHMETIC

    EV per trade = w * 0.01 - (1 - w) * 0.02 = 0.03w - 0.02
    break-even   = w = 66.7%

against the parent's 75%, on a 1:2 reward:risk rather than 1:3. Note WHERE that
improvement comes from: the target did not move at all, only the stop tightened.
This variant does not ask for a bigger win, it accepts a smaller loss. Whether
that is an improvement depends entirely on how often a 2c stop is hit by noise
that a 3c stop would have ridden out, and nothing here measures that. A tighter
stop on a book with a 1-2c spread is one tick of noise away from being a
coin-flip generator.

## READ THIS BEFORE RUNNING IT: two of these changes point opposite ways

  LOOSENING (fires more, worse selection)
    edge_threshold 0.04 -> 0.02, max_trades_per_window 3 -> 5

  TIGHTENING (fires less, better selection)
    min_book_depth_shares 50 -> 100

So "this variant fires more often" is not a summary of it, and neither is "the
numbers improved". If its results move relative to the parent, the loosened edge
threshold and the tightened depth gate are two candidate causes and the rows
carry both (`edge_threshold` and `min_book_depth_shares` are on every decision),
so they can be separated after the fact rather than argued about.

**Convention 17 applies to this variant with full force.** It is the exact shape
the convention warns about: a threshold that only REMOVED a constraint, followed
by more activity. More trades is not more edge. If this variant's
`distinct_findings` improve over the parent's, suspect the 2c threshold before
believing the result.

## THE PARENT SAYS 4c IS CONSERVATIVE *BECAUSE WE ARE NOT FASTER*

Quoting `fair_value_arb.EDGE_THRESHOLD` directly: "Deliberately conservative:
the brief's own note is that a faster bot fires at 1-2c, and we are not faster."

This variant fires at 2c, which is inside the band the parent explicitly says
belongs to somebody quicker. That is not a reason to refuse to build it - it is
Aym's call and it is a legitimate thing to measure - but the speed objection is
real and measurable, and it is this: the shadow loop's default poll interval is
5.0 seconds (`shadow_loop.DEFAULT_POLL_SEC`). A 2c gap that a faster participant
closes in under 5 seconds is one this strategy will never see, and the ones it
DOES see at 2c are therefore biased toward the gaps nobody else wanted. A 30s
time stop is six polls. Nothing about this is high frequency; the name describes
the intent, and the loop cadence is the binding constraint on it.

The round-trip spread objection from the parent's docstring is unchanged and
bites hardest here: we lift the ask and hit the bid, so a 1c profit target on a
1-2c spread is still a 2-3c favourable repricing request - now sought from a
2c entry gap. The entry edge and the required repricing are the same order of
magnitude, which is the arithmetic reason this is the least likely of the three
variants to clear its own costs.

## STATUS

NOT_TESTED (D-268). Never scored. Inherits the parent's provenance - a Reddit
screenshot - and stamps `claimed_win_rate_is_unverified_vendor_number=True` on
every row accordingly.

KILL CONDITION: trailing-50 win rate below 66.7% once 50 trades exist (its own
break-even, so this is "the variant does not clear its own arithmetic" and not a
tuned number), OR - the clause that tests the 2c threshold specifically - a
realised mean gross profit per trade below the round-trip spread measured on the
same windows, over 50 trades, which is the condition under which the entry gap
never covered the cost of taking it. Either clause fires alone. Scored by
`backtest/polymarket_harness.py`, CLOSED and RESOLVED populations kept separate,
and kept separate from `PM_fair_value_arb`'s rows - shared code path, different
population.
"""
from strategies.polymarket.fair_value_arb import (MAX_LOSS,
                                                  MAX_TRADES_PER_WINDOW,
                                                  MIN_BOOK_DEPTH_SHARES,
                                                  MIN_PROFIT, TIME_STOP_SEC,
                                                  FairValueArb)

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

#: 2c, half the parent's. The parent's own comment says this band belongs to a
#: faster bot. EXPIRY: this is the constant convention 17 points at; if results
#: improve, suspect it first.
EDGE_THRESHOLD = 0.02

#: NOMINAL geometry ONLY. **This is no longer the stop** (2026-08-18). The stop
#: is `strategies.polymarket.base.tiered_stop_price`, shared by all six
#: exit-managing Polymarket strategies, and it is keyed to the entry price
#: rather than to the strategy. Read this constant only alongside
#: `breakeven_win_rate`, which is the only thing left that reads it.
#:
#: **Consequence, stated rather than left to be discovered.** The stop was this
#: variant's ONLY differentiator from the parent on the payoff axis - the module
#: docstring above says so in as many words: "This variant does not ask for a
#: bigger win, it accepts a smaller loss." Under the shared rule `_hft` and the
#: parent now have the SAME stop at the same entry price, so what remains
#: different is `edge_threshold`, `max_trades_per_window`,
#: `min_book_depth_shares` and `time_stop_sec`. The 66.7% break-even below no
#: longer describes a live position; `breakeven_win_rate_at(entry)` does.
MAX_LOSS_HFT = 0.02

#: UNCHANGED at 1c. Restated rather than inherited silently so a reader
#: comparing the three variants sees that it did not move.
MIN_PROFIT_HFT = MIN_PROFIT

#: Five attempts per window, not three. Attempts, not fills.
MAX_TRADES_PER_WINDOW_HFT = 5

#: 100 shares within DEPTH_BAND, double the parent's 50. The one TIGHTENING in
#: this variant, and the one thing here that can raise the win rate rather than
#: merely reprice it: a 2c gap against a thin top level is even more likely to
#: be one stale quote than a 4c gap is.
MIN_BOOK_DEPTH_SHARES_HFT = 100

#: 30s, the BOTTOM of the brief's 30-120s correction band, against the parent's
#: 60s in the middle of it. Six polls at the loop's 5.0s default cadence.
TIME_STOP_SEC_HFT = 30.0


class FairValueArbHFT(FairValueArb):
    """`FairValueArb` on a 2c gap, a 2c stop and a 30s clock.

    A thin parameter variant: no logic is overridden, only defaults passed
    through `super().__init__()`. If this class ever grows a rule the parent
    does not have, it stops being a variant and its results stop being
    comparable to the parent's.

    Kill condition: trailing-50 win rate below 66.7% (its own break-even), OR
    mean gross profit per trade below the round-trip spread on the same windows
    over 50 trades. See the module docstring.
    """

    strategy_name = 'PM_fair_value_arb_hft'
    paper_mode = PAPER_MODE

    #: PAUSED (D-322, 2026-08-19, Aym's overnight profitability directive,
    #: shadow only). The critic's post-mortem measured this variant at -$221
    #: over its live shadow trades, 22.7% win rate against a 66.7% break-even -
    #: pure bleed, not a variant worth the fair-value family's shared slots.
    #: Declaring a market type nothing in the loop ever asks for is the D-312
    #: mechanism: "a strategy joins a universe by declaring it", so leaving
    #: every universe is the same declaration pointed the other way. This is
    #: NOT a deletion - `build_strategies()` still returns this instance at
    #: its pinned index 10, `len(names) == 25` still holds, and reverting is
    #: one line: restore `supported_market_types = FairValueArb.
    #: supported_market_types` (or delete this override) to rejoin every
    #: universe the parent has.
    supported_market_types = ('smart_money',)  # sentinel: no cycle ever routes this type generically (see comment above)

    def __init__(self, edge_threshold: float = EDGE_THRESHOLD,
                 min_profit: float = MIN_PROFIT_HFT,
                 max_loss: float = MAX_LOSS_HFT,
                 max_trades_per_window: int = MAX_TRADES_PER_WINDOW_HFT,
                 min_book_depth_shares: float = MIN_BOOK_DEPTH_SHARES_HFT,
                 time_stop_sec: float = TIME_STOP_SEC_HFT,
                 **kwargs):
        super().__init__(edge_threshold=edge_threshold,
                         min_profit=min_profit,
                         max_loss=max_loss,
                         max_trades_per_window=max_trades_per_window,
                         min_book_depth_shares=min_book_depth_shares,
                         time_stop_sec=time_stop_sec,
                         **kwargs)

    @property
    def breakeven_win_rate(self) -> float:
        """`max_loss / (min_profit + max_loss)`, computed from the INSTANCE.

        Computed, never written down: a constant restating 0.667 would go stale
        the first time somebody constructs this class with a different
        `max_loss` and would then be quoted as if it had been measured
        (convention 22).
        """
        denom = self.min_profit + self.max_loss
        return float('nan') if denom <= 0 else self.max_loss / denom


# Re-exported so a reader can see at a glance what the parent's numbers were.
PARENT_MAX_LOSS = MAX_LOSS
PARENT_MAX_TRADES_PER_WINDOW = MAX_TRADES_PER_WINDOW
PARENT_MIN_BOOK_DEPTH_SHARES = MIN_BOOK_DEPTH_SHARES
PARENT_TIME_STOP_SEC = TIME_STOP_SEC
