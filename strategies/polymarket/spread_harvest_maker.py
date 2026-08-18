"""Spread Harvest: buy the underdog inside a wide coin-flip book.

Adapted from moondevonyt's `spread_harvest_maker.py`. His gates are preserved;
the MoonDev feed, the wallet client and the live order path are gone. One thing
is NOT preserved and it is the important one, so it is first.

## THIS IS A TAKER STRATEGY. HIS IS A MAKER. THEY ARE NOT THE SAME BET.

His bot rests a post-only BUY at `dog_best_bid + 0.01`, hard-banded 0.40-0.48,
strictly below the dog ask, and never crosses. Getting PAID the spread is his
entire thesis: "a maker inside the spread gets paid to wait." Our paper adapter
simulates TAKER fills only, and `box_builder`'s docstring sets out at length why
simulating a resting bid as a taker lift would fabricate exactly the fills a
maker strategy lives on.

So this does not simulate his order. It places a DIFFERENT one: a marketable buy
of the underdog at the ASK, gated on his wide-book rule and his 0.40-0.48 price
band. That order really would fill against a real book, so it can be simulated
honestly - but it is a different trade with a different edge:

    his      rest a bid at 0.44, get paid the spread, eat adverse selection
    ours     pay the ask at 0.44, no spread capture, no adverse selection

The class name and file name keep his, because that is where the gates come
from. `strategy_name` is `PM_spread_harvest_TAKER` so that no graveyard row,
dashboard line or handoff can read as a measurement of his maker strategy. It is
not one, in either direction: a pass here is not evidence his bot works, and a
fail here is not evidence it does not.

## Where the edge would be, stated so it can be killed

Paying 0.44 for a share that wins 50% of the time is +6c, about 1360bps. That
number is entirely load-bearing on the window really being a coin flip. It is
not "the spread" - we forgo the spread by crossing - it is only the gap between
the price and a true 0.50, so a systematic tilt of a few points against us
erases it. `book_implied_p_dog` rides on every row precisely so that tilt is
measurable rather than assumed away.

## THE COIN-FLIP GATE IS THE HONEST WEAK POINT

His gate is `coa = |spot - strike| / ATR4 <= 0.40`: a vol-normalised statement
that BTC is sitting on the strike. It needs the settlement strike, which is a
Chainlink 60s TWAP Gamma does not publish, so on a live context it is
unavailable - the same wall `mid_price_continuation` hits.

Two paths, and every decision says which one it took:

  `coin_flip_source='cushion_atr'`   strike, spot and atr14 all present. His
        gate, applied exactly, in bps/bps. The dog is the side BTC is currently
        below, as he defines it.

  `coin_flip_source='book_implied'`  no strike. The near-tie test is then the
        PRICE BAND itself: a side the market's own ask puts at 0.40-0.48 is
        within ten cents of a coin flip by the only estimate available. The dog
        is the lower-MIDPOINT side.

## WHAT IT SHIPS WITH TODAY: NOTHING. `allow_book_implied_coin_flip=False` (D-282)

Gamma publishes no settlement strike, so on every live shadow-loop context the
`cushion_atr` path is unreachable. An earlier revision of this file concluded
that the strategy should therefore ship running on `book_implied` and only on
`book_implied`, and described that as the accepted configuration. **Raven ruled
the other way in D-282.** The default is now
`allow_book_implied_coin_flip=False`, so the strategy SKIPS `no_cushion_data`
on every live window and will fire on nothing until a real strike feed exists.

The reasoning is the one the next section already makes: `book_implied` is a
DIFFERENT gate, not a degraded `cushion_atr`. Shipping it as the default would
fill the graveyard with rows that look like a measurement of the ported
strategy and are not one, and a strategy that quietly runs on its fallback is
the harder error to notice later than a strategy that visibly fires on nothing
(convention 11: NOT_TESTED means "could not run", and that is the honest state
here). The gate stays IMPLEMENTED and fully tested so a sensitivity run can
turn it on explicitly with `allow_book_implied_coin_flip=True`; its output is a
separate population and must never be pooled with `cushion_atr` rows.

Because the gate split outlives the default, every decision row carries a
`gate` feature - `gate='book_implied'` or `gate='cushion_atr'` - as a
first-class field beside `coin_flip_source`, and `gate_must_not_be_pooled=True`
beside it. `coin_flip_source` was already there and says the same thing; `gate`
exists so a scorer, a dashboard filter or a handoff has one obvious key to group
on and cannot pool the two populations by simply not having noticed the
distinction. If a strike ever becomes available, rows will start arriving with
`gate='cushion_atr'` and THOSE MUST BE SCORED AS A SEPARATE POPULATION. A win
rate computed across both describes neither gate.

The second is a DIFFERENT GATE, not a looser version of the first, and it is
weaker in a specific way: it asks what the book thinks, and a stale or absent
market maker can make a book think something the price does not support. His
gate would catch a window that has quietly run away from the strike while the
quotes lag; this one would not. Any result produced under `book_implied` must be
reported separately from any produced under `cushion_atr` and the two must never
be pooled (convention 11 - the second is not a measurement of the first).

The dog is taken from the MIDPOINT, not the ask, deliberately. On a wide book
both asks are marked up and the underdog's ask can sit ABOVE the favourite's -
his own log has dog asks at 0.60-0.68 in near-ties - so "cheaper ask" is not
"less likely side". The midpoint is the book's actual probability estimate.

## HIS NUMBERS, NOT EVIDENCE, AND HE SAYS SO LOUDER THAN WE WOULD

  89c maker:      0 fills in 275 windows. Dead.
  cheap bids <=0.35: filled 32-35% win. Textbook adverse selection, 184 fills.
  0.40-0.50 band: 12/21 = 57% at a 44c average. NOT toxic.

12 of 21 is the number the 0.40-0.48 band rests on. That is a 57% point estimate
with a standard error near 11 points, so the true rate plausibly sits anywhere
from 35% to 79%, and his own README says it: "this exact quote style has never
been run... Treat it as a data-collecting instrument with a plausible edge
attached." Convention 7 cuts both ways and 21 trades is a shrug.

## DEVIATIONS FROM HIS BOT, all logged

  1. TAKER, not maker. Discussed above; it is the whole adaptation.
  2. The 0.40-0.48 band is applied to the effective BOOK-WALKED ask for the full
     block, not to a quote derived from top-of-book.
  3. No mid-window cancel ladder. `coa > 0.60` and `ask_sum < 1.05` cancel a
     RESTING order; a taker fill is already done and there is nothing to pull.
     Both are still computed and logged so the cancel-rule dataset survives.
  4. One entry per window, as his one-quote-per-window.
  5. His README says the quote is live "T-120 -> T-30" but his constant is
     `TIME_BAND = (30, 180)`. The constant wins: 30-180 seconds left.

KILL CONDITION: trailing-50 resolved win rate below the trailing-50 average
effective entry price, once 20 trades exist - i.e. it stops clearing its own
breakeven, which on a binary is the only win rate that means anything. Also dies
if the mean `book_implied_p_dog` at entry differs from 0.50 by more than 5
points over 50 entries (the coin-flip premise is false and the fallback gate is
not detecting it), or if backtest/polymarket_harness.py scores it under 30bps
net edge on our own data (convention 5, D-268).
"""
import math
from typing import Dict, Optional

from strategies.polymarket.base import (Decision, Leg, MarketContext,
                                        PolymarketStrategy, effective_ask_for)

# Never False in this repo. moondevonyt ships this False ("LIVE FIRE").
PAPER_MODE = True

# moondevonyt's constants, unchanged.
COA_MAX = 0.40             # verified coin flip only (|cushion| / ATR)
COA_CANCEL = 0.60          # his cancel level. Logged; a taker fill cannot be pulled.
ASK_SUM_MIN = 1.10         # the book is WIDE, the market makers are absent
ASK_SUM_COLLAPSE = 1.05    # his cancel level. Logged, same reason.
BAND_LOW = 0.40            # below this his fills were TOXIC (32-35% win)
BAND_HIGH = 0.48           # the mid-price shelf ceiling
TIME_LEFT_MIN = 30         # his TIME_BAND[0]
TIME_LEFT_MAX = 180        # his TIME_BAND[1]
USD_SIZE = 5.0             # his flat stake
MIN_SHARES = 5             # Polymarket minimum order size
STATE_WINDOWS_KEPT = 8


def shares_for(usd: float, price: float, min_shares: int = MIN_SHARES) -> float:
    """His `calc_shares`: stake/price floored to 2dp, never below the minimum."""
    if price <= 0:
        return float(min_shares)
    return max(float(min_shares), math.floor((usd / price) * 100) / 100)


class SpreadHarvestMaker(PolymarketStrategy):
    """Buy the underdog at 0.40-0.48 inside a wide, near-tie 5m book."""

    #: NOT `PM_spread_harvest_maker`. See the module docstring: this crosses the
    #: spread, his rests inside it, and a shared key would let one be quoted as
    #: evidence about the other.
    strategy_name = 'PM_spread_harvest_taker'
    uses_maker_orders = False
    paper_mode = PAPER_MODE

    def __init__(self, coa_max: float = COA_MAX,
                 ask_sum_min: float = ASK_SUM_MIN,
                 band_low: float = BAND_LOW,
                 band_high: float = BAND_HIGH,
                 time_left_min: float = TIME_LEFT_MIN,
                 time_left_max: float = TIME_LEFT_MAX,
                 usd_size: float = USD_SIZE,
                 min_shares: int = MIN_SHARES,
                 shares=None,
                 allow_book_implied_coin_flip: bool = False):
        self.coa_max = coa_max
        self.ask_sum_min = ask_sum_min
        self.band_low = band_low
        self.band_high = band_high
        self.time_left_min = time_left_min
        self.time_left_max = time_left_max
        self.usd_size = usd_size
        self.min_shares = min_shares
        #: Override the USD-derived size. Tests and sweeps only.
        self.shares = shares
        #: D-282: DEFAULT False. Refuses to trade without a real strike, so the
        #: strategy runs ONLY under his own coa gate and skips `no_cushion_data`
        #: everywhere else. That is the strict reading of the port and it fires
        #: on nothing we can currently feed it - which is the point of the
        #: ruling: the book-implied gate is a DIFFERENT gate, not a looser
        #: version of his, so a run under it would not be a measurement of the
        #: strategy being ported. True enables it for a sensitivity run whose
        #: results must be reported separately (convention 11).
        self.allow_book_implied_coin_flip = allow_book_implied_coin_flip
        #: window_ts -> True once an entry has been ATTEMPTED. As with every
        #: strategy here, an ENTER decision is not a confirmed fill: the halt,
        #: the risk gate and the adapter all sit downstream.
        self._entered: Dict[int, bool] = {}

    def intended_shares(self, price: float) -> float:
        if self.shares is not None:
            return float(self.shares)
        return shares_for(self.usd_size, price, self.min_shares)

    def _prune(self, current_ts: int) -> None:
        if len(self._entered) <= STATE_WINDOWS_KEPT:
            return
        for ts in sorted(self._entered)[:-STATE_WINDOWS_KEPT]:
            self._entered.pop(ts, None)

    # -- decision -----------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        slug = getattr(ctx.market, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            feats.setdefault('fill_model', 'taker_adaptation_of_a_maker_strategy')
            feats.setdefault('is_moondev_maker_strategy', False)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market')
        if self._entered.get(ctx.window_ts):
            return decide('SKIP', 'already_entered_this_window')

        time_left = ctx.seconds_remaining
        feats: dict = {'seconds_remaining': (None if time_left is None
                                             else round(time_left, 1)),
                       'time_band': [self.time_left_min, self.time_left_max]}
        if time_left is None:
            # His whole gate is a time band. Without a clock there is no band.
            return decide('SKIP', 'no_window_clock', **feats)
        if not (self.time_left_min <= time_left <= self.time_left_max):
            return decide('SKIP', 'out_of_time_band', **feats)

        book_up = ctx.book('Up')
        book_down = ctx.book('Down')
        if book_up is None or book_down is None:
            return decide('SKIP', 'no_orderbook',
                          has_book_up=book_up is not None,
                          has_book_down=book_down is not None, **feats)

        ask_up, ask_down = book_up.best_ask, book_down.best_ask
        mid_up, mid_down = book_up.midpoint, book_down.midpoint
        feats.update({'ask_up': ask_up, 'ask_down': ask_down,
                      'bid_up': book_up.best_bid, 'bid_down': book_down.best_bid,
                      'mid_up': mid_up, 'mid_down': mid_down})
        if ask_up is None or ask_down is None:
            return decide('SKIP', 'no_asks', **feats)

        ask_sum = round(ask_up + ask_down, 4)
        feats.update({'ask_sum': ask_sum, 'ask_sum_min': self.ask_sum_min,
                      'ask_sum_collapse_level': ASK_SUM_COLLAPSE})
        if ask_sum < self.ask_sum_min:
            # A tight book has no hole to quote into. This is his primary
            # microstructure gate and the dataset his README says the repo has
            # never had, so it is recorded on every window either way.
            return decide('SKIP', 'book_not_wide_enough', **feats)

        dog, source, coa = self._underdog(ctx, mid_up, mid_down)
        # `gate` duplicates `coin_flip_source` on purpose. It is the field a
        # scorer groups on, and results under one gate must NEVER be pooled with
        # results under the other - they are different gates, not different
        # settings of one gate. Today this strategy ships on `book_implied`
        # because Gamma publishes no strike.
        feats.update({'coin_flip_source': source, 'gate': source,
                      'gate_must_not_be_pooled': True,
                      'coa': coa,
                      'coa_max': self.coa_max,
                      'coa_cancel_level': COA_CANCEL,
                      'dog_side': dog})
        if source == 'book_implied' and mid_up is not None and mid_down is not None:
            total = mid_up + mid_down
            feats['book_implied_p_dog'] = (
                round(min(mid_up, mid_down) / total, 4) if total > 0 else None)

        if dog is None:
            return decide('SKIP',
                          'no_cushion_data' if source == 'unavailable'
                          else 'no_underdog', **feats)
        if source == 'cushion_atr' and coa is not None and coa > self.coa_max:
            # Not a coin flip. His gate, unchanged.
            return decide('SKIP', 'not_a_coin_flip', **feats)

        book = ctx.book(dog)
        if book is None:
            return decide('SKIP', 'no_orderbook', **feats)
        dog_ask = book.best_ask
        feats.update({'dog_best_ask': dog_ask, 'band_low': self.band_low,
                      'band_high': self.band_high})
        if dog_ask is None:
            return decide('SKIP', 'no_asks', **feats)
        if dog_ask > self.band_high:
            # Named BEFORE the depth gate: `ask_depth(band_high)` is 0 both when
            # nobody offers under 48c and when somebody offers two shares under
            # it, and those are different facts (convention 20).
            return decide('SKIP', 'ask_above_band', **feats)

        shares = self.intended_shares(dog_ask)
        depth = book.ask_depth(self.band_high)
        feats.update({'intended_shares': shares,
                      'dog_depth_at_band_high': depth})
        if depth < shares:
            return decide('SKIP', 'insufficient_ask_depth', **feats)

        effective = effective_ask_for(book, shares, self.band_high)
        feats['effective_ask'] = (None if effective is None
                                  else round(effective, 4))
        if effective is None:
            return decide('SKIP', 'unfillable_at_band_high', **feats)
        feats['slippage_vs_top'] = round(effective - dog_ask, 4)

        if effective > self.band_high:
            # walk_book cannot return this given the same limit. Kept because
            # the band is the strategy and a silent regression is invisible.
            return decide('SKIP', 'effective_ask_above_band', **feats)
        if effective < self.band_low:
            # Below the floor his cheap-bid fills won 32-35%. The market is
            # saying this is not a coin flip, and it holds the inventory.
            return decide('SKIP', 'effective_ask_below_band', **feats)

        feats['limit_price'] = self.band_high
        feats['breakeven_win_rate'] = round(effective, 4)
        # The premise, not a measurement: a true coin flip pays 0.50.
        feats['edge_if_true_coin_flip'] = round(0.50 - effective, 4)
        feats['confidence'] = 0.50
        feats['confidence_is_the_coin_flip_premise_not_a_measurement'] = True

        self._entered[ctx.window_ts] = True
        self._prune(ctx.window_ts)

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=dog,
                                limit_price=self.band_high,
                                order_type='taker',
                                shares=shares,
                                expected_price=effective)],
                      **feats)

    # -- the coin-flip gate -------------------------------------------------

    def _underdog(self, ctx: MarketContext, mid_up: Optional[float],
                  mid_down: Optional[float]):
        """(dog_side, coin_flip_source, coa). See the docstring for the split.

        `coa` is None on the book-implied path - not 0.0, and not some stand-in
        computed off spot. There is no cushion, so there is no cushion-over-ATR,
        and writing a number there would put a fabricated gate value in the
        dataset his README says is the point of the bot (convention 11).
        """
        if (ctx.spot is not None and ctx.strike is not None and ctx.strike > 0
                and ctx.atr14 is not None and ctx.atr14 > 0):
            # His gate, in bps/bps. `atr14` is in basis points by MarketContext
            # contract, so the cushion has to be too or the ratio is ~10,000x
            # wrong and every window reads as a coin flip.
            cushion_bps = ((ctx.spot - ctx.strike) / ctx.strike) * 10_000.0
            coa = abs(cushion_bps) / ctx.atr14
            dog = 'Down' if cushion_bps > 0 else 'Up'
            return dog, 'cushion_atr', round(coa, 4)

        if not self.allow_book_implied_coin_flip:
            return None, 'unavailable', None
        if mid_up is None or mid_down is None:
            # A one-sided book has no midpoint, and an absent bid is not a bid
            # of zero. Without both mids there is no underdog to identify.
            return None, 'book_implied', None
        if mid_up == mid_down:
            return None, 'book_implied', None
        return ('Down' if mid_down < mid_up else 'Up'), 'book_implied', None
