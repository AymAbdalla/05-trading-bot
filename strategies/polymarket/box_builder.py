"""Box Builder: two-sided maker. Bid both Up and Down for a combined 94c.

Ported from moondevonyt's `box_builder.py`. Thresholds preserved; the wallet
client and live order path are gone.

THESIS (his, not ours): rest post-only bids on BOTH sides in the first half of
a 5-minute window at a combined bid_Up + bid_Down of 0.94 or less. Up and Down
are mutually exclusive and exhaustive, so a filled pair redeems for exactly
$1.00 whatever BTC does. Paying 94c for a guaranteed dollar is a 6c spread
harvest that does not care about direction. Adverse selection - the thing that
punishes a one-sided maker - is what fills the second leg.

THE QUOTES ARE LOWBALLS, NOT A BUDGET SPLIT. This is the part a port gets
wrong. His `cap_bids` JOINS both best bids and then backs off symmetrically in
1c ticks, higher leg giving first, until the sum is at or under 0.94. So 0.94
is a CEILING that usually is not reached: on a book bidding 0.30/0.30 he quotes
0.30/0.30 and targets a 40c box, not 0.47/0.47.

Splitting the 0.94 proportionally instead - which an earlier version of this
file did - quotes ABOVE the best bid on both sides of a cheap book. That is not
a lowball, it is a cross: post-only rejects it, and if it is not post-only it
lifts the offer and pays 94c for a box the book was giving away at 60c. His
logs already recorded that failure once, as 249 post-only rejects from
chasing.

THE HONEST LIMITATION, AND WHY THIS RETURNS 'QUOTE' NOT 'ENTER'.

This is a MAKER strategy. Its economics live entirely in whether a resting bid
gets hit, and our paper adapter simulates TAKER fills against live book depth.
It cannot tell you whether a 0.47 bid would have been filled - that depends on
queue position, on order flow we do not observe, and on the fact that the flow
which fills you is disproportionately the flow that knows something. Simulating
it as "filled if the ask ever touched 0.47" would fabricate exactly the fills
that matter, and would systematically overstate the strategy, because in a real
book the pairs that complete cheaply are the ones you most wanted not to get.

His own logs say the same thing from the other side: the fable maker filled 57%
at T-240, while a v5 bot armed 35 times at 0.89 and got ZERO fills. A fill model
that cannot reproduce that gap is not a model.

So this strategy evaluates its full decision logic - arm/skip, both quote
prices, the completion ladder, the stranded-leg rule - and returns a QUOTE
decision carrying the legs it would have rested. It never returns ENTER. That
makes it a fully specified, fully logged, zero-fill strategy until a maker fill
model exists. Convention 11: not tested, not tested-and-failed.

The one part that IS taker and therefore IS simulatable is the completion lift:
once leg one fills at p1, if the other side's ask is at or below 0.99 - p1 you
cross for it. `completion_lift` implements that and returns an ENTER decision,
because that leg really is a marketable order against a real book.

KILL CONDITIONS: dies if the both-legs-filled rate is under 20% once a maker
fill model exists (a box that only ever fills one leg is a directional bet
wearing a hedge costume), or if the resolution-PnL harness scores it under
30bps net edge on our own data (convention 5, D-268).
"""
from strategies.polymarket.base import (Decision, Leg, MarketContext,
                                        PolymarketStrategy)

# Never False in this repo. moondevonyt ships this False ("LIVE FIRE").
PAPER_MODE = True

# moondevonyt's constants, unchanged.
ARM_ASK_SUM_MIN = 1.03    # only arm when the book is wide enough to be worth it
MAX_PAIR_COST = 0.94      # combined bid CEILING. The pair redeems for exactly 1.00.
QUOTE_START_SEC = 0       # T-300: window open
QUOTE_END_SEC = 150       # T-150: stop quoting at the halfway mark
CANCEL_ALL_SEC = 290      # his CANCEL_ALL_SEC=10 counted from the close (T-10)
COMPLETION_BID_CAP = 0.97  # raise the stranded leg's bid to at most 0.97 - p1
COMPLETION_LIFT_CAP = 0.99  # cross for the second leg if ask <= 0.99 - p1
STRANDED_CHECK_SEC = 210  # his BAILOUT_SEC=90 counted from the close (T-90)
PRICE_TICK = 0.01         # Polymarket 1c ticks
SHARES_PER_LEG = 5        # Polymarket minimum, until the CSV proves the rate


def cap_bids(bid_up, bid_down, cap: float = MAX_PAIR_COST,
             tick: float = PRICE_TICK):
    """His `cap_bids`, unchanged: join both best bids, then back off.

    Steps down in 1c ticks, higher leg first, until bid_up + bid_down is at or
    under `cap`. Neither leg ever goes below one tick, and neither leg is ever
    quoted ABOVE the best bid it joined - which is what keeps these lowballs
    instead of crosses.

    Returns None when either side has no bid to join. An absent bid is not a
    bid of zero (convention 11); quoting into a one-sided book is how you end
    up with the naked leg this whole structure exists to avoid.
    """
    if bid_up is None or bid_down is None:
        return None
    bu, bd = round(float(bid_up), 2), round(float(bid_down), 2)
    while round(bu + bd, 2) > cap and (bu > tick or bd > tick):
        if bu >= bd:
            bu = round(bu - tick, 2)
        else:
            bd = round(bd - tick, 2)
    return max(bu, tick), max(bd, tick)


class BoxBuilder(PolymarketStrategy):
    """Two-sided maker harvesting the Up+Down spread on a 5m window."""

    strategy_name = 'PM_box_builder'
    uses_maker_orders = True
    paper_mode = PAPER_MODE

    def __init__(self, arm_ask_sum_min: float = ARM_ASK_SUM_MIN,
                 max_pair_cost: float = MAX_PAIR_COST,
                 quote_start_sec: int = QUOTE_START_SEC,
                 quote_end_sec: int = QUOTE_END_SEC,
                 shares_per_leg: float = SHARES_PER_LEG):
        self.arm_ask_sum_min = arm_ask_sum_min
        self.max_pair_cost = max_pair_cost
        self.quote_start_sec = quote_start_sec
        self.quote_end_sec = quote_end_sec
        self.shares_per_leg = shares_per_leg

    def evaluate(self, ctx: MarketContext) -> Decision:
        slug = getattr(ctx.market, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market')

        elapsed = ctx.seconds_into_window
        feats = {
            'seconds_into_window': elapsed,
            'shares_per_leg': self.shares_per_leg,
            'fill_model': 'maker_fills_not_simulated',
        }

        if elapsed is not None:
            if elapsed < self.quote_start_sec:
                return decide('SKIP', 'window_not_open', **feats)
            if elapsed > self.quote_end_sec:
                # Late deep bids never fill: 35 arms at 0.89, zero fills.
                return decide('SKIP', 'past_quote_window', **feats)

        book_up = ctx.book('Up')
        book_down = ctx.book('Down')
        if book_up is None or book_down is None:
            return decide('SKIP', 'no_orderbook', **feats)

        ask_up, ask_down = book_up.best_ask, book_down.best_ask
        bid_up, bid_down = book_up.best_bid, book_down.best_bid
        feats.update({'ask_up': ask_up, 'ask_down': ask_down,
                      'bid_up': bid_up, 'bid_down': bid_down})

        if ask_up is None or ask_down is None:
            return decide('SKIP', 'no_asks', **feats)

        # Rounded to 2dp before the comparison, as his `try_arm` does. On a
        # 1c-tick book an unrounded 1.0299999 is the same quote as 1.03 and
        # should arm; float noise is not a signal.
        ask_sum = round(ask_up + ask_down, 2)
        feats['ask_sum'] = ask_sum
        if ask_sum < self.arm_ask_sum_min:
            # A tight book has no spread to harvest. Quoting into it just
            # accumulates one-sided inventory.
            return decide('SKIP', 'book_too_tight_to_arm', **feats)

        # Quotes are STATIC LOWBALLS. Join both best bids, back off in ticks
        # until the pair is under the ceiling. Never quote above the bid we
        # joined - chasing earned 249 post-only rejects in his logs.
        quotes = cap_bids(bid_up, bid_down, self.max_pair_cost)
        if quotes is None:
            return decide('SKIP', 'no_bids_to_join', **feats)
        quote_up, quote_down = quotes

        feats.update({
            'quote_up': quote_up,
            'quote_down': quote_down,
            'pair_cost': round(quote_up + quote_down, 4),
            'guaranteed_redemption': 1.00,
            'gross_edge_per_pair': round(1.00 - (quote_up + quote_down), 4),
        })

        if round(quote_up + quote_down, 2) > self.max_pair_cost + 1e-9:
            # Only reachable if both legs bottomed out at one tick and the cap
            # was set below 0.02. Assert it rather than ship a losing box.
            return decide('SKIP', 'pair_cost_above_cap', **feats)
        if quote_up <= 0 or quote_down <= 0:
            return decide('SKIP', 'degenerate_quote', **feats)

        legs = [
            Leg('Up', quote_up, order_type='maker', shares=self.shares_per_leg),
            Leg('Down', quote_down, order_type='maker', shares=self.shares_per_leg),
        ]
        # QUOTE, never ENTER. See the module docstring: we cannot honestly
        # simulate whether these rest orders fill, and pretending otherwise
        # would manufacture the strategy's entire P&L.
        return decide('QUOTE', 'maker_fill_not_simulated', legs=legs, **feats)

    # -- taker sub-paths, which CAN be simulated ---------------------------

    def completion_lift(self, ctx: MarketContext, filled_side: str,
                        filled_price: float) -> Decision:
        """One leg filled at `filled_price`. Cross for the other if it is cheap.

        This IS a marketable order against a real book, so unlike the resting
        quotes it can be simulated honestly and returns ENTER.
        """
        slug = getattr(ctx.market, 'slug', None)
        other = 'Down' if filled_side.strip().lower() == 'up' else 'Up'
        lift_cap = round(COMPLETION_LIFT_CAP - filled_price, 2)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        feats = {'filled_side': filled_side, 'filled_price': filled_price,
                 'completion_side': other, 'lift_cap': lift_cap,
                 'stranded_check_sec': STRANDED_CHECK_SEC}

        if lift_cap <= 0:
            # First leg cost so much that any pair completes above $1.00. The
            # "hedge" would lock in a loss.
            return decide('SKIP', 'no_profitable_completion', **feats)

        book = ctx.book(other)
        if book is None:
            return decide('SKIP', 'no_orderbook', **feats)
        ask = book.best_ask
        feats['completion_ask'] = ask
        if ask is None:
            return decide('SKIP', 'no_asks', **feats)
        if ask > lift_cap:
            # Cannot complete profitably. The stranded-leg rule applies: at
            # T-90, hold only if our side is favoured, else cut at the bid.
            feats['bid_ladder_target'] = round(
                min(book.best_bid or 0.0, COMPLETION_BID_CAP - filled_price), 2)
            return decide('SKIP', 'completion_ask_above_cap', **feats)

        feats['pair_cost'] = round(filled_price + ask, 4)
        feats['gross_edge_per_pair'] = round(1.00 - (filled_price + ask), 4)
        return decide('ENTER', 'completion_lift',
                      legs=[Leg(other, lift_cap, order_type='taker',
                                shares=self.shares_per_leg,
                                expected_price=ask)],
                      **feats)
