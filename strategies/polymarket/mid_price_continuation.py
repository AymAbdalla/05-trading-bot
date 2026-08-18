"""Mid-Price Continuation: buy the leading side at 40-55c when BTC is through
the strike.

Ported from moondevonyt's `mid_price_continuation.py`. Thresholds preserved;
the MoonDev API, the wallet client, and the live order path are gone.

THESIS (his, not ours): when BTC has punched through the window's price-to-beat
by at least 0.05%, the leading side keeps leading more often than its 40-55c
price implies. Buy the leading side as a taker, hold to resolution.

HIS NUMBERS, NOT EVIDENCE. ~60.5% on 168 trades at 0.475 average entry, mined
from his lag-arb logs. 168 is a small sample - convention 7 cuts both ways, and
a 60% win rate on 168 trades has roughly a 4-point standard error, so the true
rate could sit anywhere from 52% to 68%. At a 47.5c average entry the strategy
is profitable across most of that interval and marginal at the bottom of it.
Unverified until our harness says otherwise (D-268).

THE BAND IS THE STRATEGY, IN BOTH DIRECTIONS. His forensics found the SAME
family lost money at 60-70c (-12% EV) and 80-90c (-7% EV). Same signal, worse
price, negative expectancy - which is the clearest possible demonstration that
on a binary the entry price and the win rate are one number, not two. Hence a
hard 0.55 cap and, less obviously, a 0.40 FLOOR: below 40c the market is
telling you the "leading" side is not actually leading, and taking that trade
means the signal disagrees with the book about which side is even ahead.

UNITS. His gate is `(spot - strike) / strike * 100 >= 0.05` - PERCENT. Ours
keeps the ratio unscaled and compares against 0.0005. Same gate, and the `%`
is the single easiest thing to lose in a port: 0.05 as a ratio would be a 5%
move, which BTC does not make inside five minutes, and the strategy would look
alive while never firing.

ONE DELIBERATE DEVIATION, tightening and logged: he gates on the best ask and
then sizes against depth. We walk the book for the full intended size first and
gate the 0.40-0.55 band on the EFFECTIVE average entry. On a binary the entry
price IS half the edge, so the price that has to clear the band is the one we
would actually pay, never top-of-book.

THE STRIKE IS NOT FREE. `engine/polymarket/context.py` leaves `strike` None
unless a caller supplies a real Chainlink TWAP reading, because Gamma does not
publish one for these markets. This strategy therefore SKIPs with
`no_spot_or_strike` on a stock live context, and that is correct behaviour, not
a dead strategy - substituting spot for the strike would make the 5bps gate
compare a number to itself.

KILL CONDITION: dies if our resolution-PnL harness scores it under 30bps net
edge (convention 5, D-268), or if the realized average entry drifts above 0.52
(the margin is gone before the win rate ever gets a say).
"""
import math

from strategies.polymarket.base import (Decision, Leg, MarketContext,
                                        PolymarketStrategy,
                                        effective_ask_for, source_counts)

# Never False in this repo. moondevonyt ships this False ("LIVE on AUG14").
PAPER_MODE = True

# moondevonyt's constants, unchanged (his MIN_ITM_PCT of 0.05 is in percent;
# see the UNITS note above for why this one is written as a ratio).
ITM_PCT_MIN = 0.0005        # 0.05% through the strike
ENTRY_BAND_LOW = 0.40       # below this the book disagrees about who leads
ENTRY_BAND_HIGH = 0.55      # hard cap. 60-70c was -12% EV on the same signal.
SECONDS_LEFT_MIN = 120      # too close to resolution: no room to be right
SECONDS_LEFT_MAX = 300      # the full window
USD_SIZE = 5.0              # his flat test stake
MIN_SHARES = 5              # Polymarket minimum order size


def shares_for(usd: float, price: float, min_shares: int = MIN_SHARES) -> float:
    """His `calc_shares`: stake/price floored to 2dp, never below the minimum."""
    if price <= 0:
        return float(min_shares)
    return max(float(min_shares), math.floor((usd / price) * 100) / 100)


class MidPriceContinuation(PolymarketStrategy):
    """Buy the leading side at 40-55c once BTC is decisively through strike."""

    # Read by the shadow loop BEFORE evaluate() is called. The strike this
    # strategy compares against is a measured proxy, so the loop refuses any
    # lead inside that proxy's measured error rather than letting this class
    # decide on a number it cannot know is noise.
    needs_strike = True

    strategy_name = 'PM_mid_price_continuation'
    paper_mode = PAPER_MODE

    def __init__(self, itm_pct_min: float = ITM_PCT_MIN,
                 band_low: float = ENTRY_BAND_LOW,
                 band_high: float = ENTRY_BAND_HIGH,
                 seconds_left_min: int = SECONDS_LEFT_MIN,
                 seconds_left_max: int = SECONDS_LEFT_MAX,
                 usd_size: float = USD_SIZE,
                 min_shares: int = MIN_SHARES,
                 shares=None):
        self.itm_pct_min = itm_pct_min
        self.band_low = band_low
        self.band_high = band_high
        self.seconds_left_min = seconds_left_min
        self.seconds_left_max = seconds_left_max
        self.usd_size = usd_size
        self.min_shares = min_shares
        #: Override the USD-derived size. Only for tests and sweeps.
        self.shares = shares

    def intended_shares(self, price: float) -> float:
        if self.shares is not None:
            return float(self.shares)
        return shares_for(self.usd_size, price, self.min_shares)

    def evaluate(self, ctx: MarketContext) -> Decision:
        slug = getattr(ctx.market, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.spot is None or ctx.strike is None:
            return decide('SKIP', 'no_spot_or_strike')
        if ctx.strike <= 0:
            return decide('SKIP', 'invalid_strike', strike=ctx.strike)

        itm_pct = (ctx.spot - ctx.strike) / ctx.strike
        leading_side = 'Up' if itm_pct >= 0 else 'Down'

        feats = {
            'spot': ctx.spot,
            'strike': ctx.strike,
            'itm_pct': round(itm_pct, 6),
            'itm_bps': round(itm_pct * 10_000, 2),
            'leading_side': leading_side,
            'direction_sources': source_counts(ctx.windows),
            'confidence': 0.605,
            'confidence_is_unverified_vendor_number': True,
        }

        if abs(itm_pct) < self.itm_pct_min:
            # Sitting on the strike. The window is a coin flip and the leading
            # side is whichever way the last tick happened to fall.
            return decide('SKIP', 'not_through_strike', **feats)

        remaining = ctx.seconds_remaining
        feats['seconds_remaining'] = remaining
        if remaining is not None:
            if remaining < self.seconds_left_min:
                return decide('SKIP', 'too_close_to_resolution', **feats)
            if remaining > self.seconds_left_max:
                return decide('SKIP', 'window_not_open', **feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market', **feats)

        book = ctx.book(leading_side)
        if book is None:
            return decide('SKIP', 'no_orderbook', **feats)

        best_ask = book.best_ask
        feats['best_ask'] = best_ask
        if best_ask is None:
            return decide('SKIP', 'no_asks', **feats)

        # Depth gate BEFORE the band gate: a 0.48 top-of-book quote for 3
        # shares is not a 0.48 entry for 10, and the band has to be judged on
        # the price we would actually pay. This is the Dan1ro0 point - tradable
        # edge uses expected average entry, never the best ask.
        shares = self.intended_shares(best_ask)
        depth = book.ask_depth(self.band_high)
        feats['ask_depth_at_cap'] = depth
        feats['intended_shares'] = shares
        if depth < shares:
            return decide('SKIP', 'insufficient_ask_depth', **feats)

        effective = effective_ask_for(book, shares, self.band_high)
        feats['effective_ask'] = None if effective is None else round(effective, 4)
        if effective is None:
            return decide('SKIP', 'unfillable_at_cap', **feats)
        feats['slippage_vs_top'] = round(effective - best_ask, 4)

        if effective > self.band_high:
            return decide('SKIP', 'effective_ask_above_band', **feats)
        if effective < self.band_low:
            # Cheaper than the floor means the book does not agree this side is
            # leading. Our signal and the market disagree, and the market is
            # the one holding the inventory.
            return decide('SKIP', 'effective_ask_below_band', **feats)

        feats['limit_price'] = self.band_high
        feats['breakeven_win_rate'] = round(effective, 4)

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=leading_side,
                                limit_price=self.band_high,
                                order_type='taker',
                                shares=shares,
                                expected_price=effective)],
                      **feats)
