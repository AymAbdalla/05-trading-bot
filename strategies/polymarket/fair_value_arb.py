"""Fair Value Mispricing Arbitrage: buy the gap, sell the correction.

Dan1ro0 concepts 1 and 2 as runnable code. NOT a moondevonyt port - there is no
original to deviate from, so every constant below is ours and every one of them
is an assumption with an expiry date (convention 17).

    fair value P (independent of the book)  ->  compare to the live ask
    gap > EDGE_THRESHOLD                    ->  buy the underpriced side
    gap closes                              ->  SELL, before resolution

The thing that makes this different from every other strategy in this package:
it does not hold to resolution. It holds a mispricing. The position is opened
because the book is wrong and closed when the book stops being wrong, which is
usually 30-120 seconds later as other participants reprice the same move. The
outcome of the window is not the trade.

## PROVENANCE, and what is and is not evidence

The shape comes from a Reddit post about a wallet ("Sharky6999") claiming a
99.3% win rate over 32,614 trades, $1.4K -> $965K. That number is NOT evidence
and nothing here treats it as one (convention 3). It is somebody's screenshot.
It is not our data, it is not our fills, we have never scored a single trade of
this strategy, and the wallet-verification task in Raven's Dan1ro0 analysis has
not been run. Every decision row this strategy emits carries
`claimed_win_rate_is_unverified_vendor_number=True` so no later reader can pick
the 99.3% up off a log and mistake it for a measurement.

Per D-268 this strategy is NOT_TESTED. Not tested-and-found-nothing.

## THE ARITHMETIC THAT DECIDES WHETHER THIS CAN WORK AT ALL

Read this before believing any win rate, ours or theirs.

We ENTER by lifting the ask and EXIT by hitting the bid. That means we pay the
spread on the round trip before the strategy has done anything. On a 5-minute
Polymarket book the spread is routinely 1-2c. So:

    to sell at entry + 1c on the BID, the ASK must rise by roughly 2-3c

A "1 cent profit target" is therefore a 2-3 cent favourable repricing request.
That is the single most likely way this strategy fails, and it fails quietly,
as a time-stop exit at a small loss rather than as an error.

Second, the payoff geometry. With MIN_PROFIT=1c and MAX_LOSS=3c:

    EV per trade = w * 0.01 - (1 - w) * 0.03 = 0.04w - 0.03
    break-even   = w = 75%

So a 99.3% claim is not implausible-looking because it is high, it is
implausible-looking because the whole strategy needs 75% just to scratch. An
80% trailing win rate - the kill line below - leaves 0.2c per trade. This is a
strategy with almost no margin for error by construction, and that is the
finding to check first when it is finally scored.

Third, a spec tension worth naming rather than silently resolving. The kill
condition asks for an average profit above 2c per trade while MIN_PROFIT sets
the profit-target floor at 1c. Those are only compatible because MIN_PROFIT is
a FLOOR on the sell limit and not a cap on the fill: we sell at the walked bid,
which can be well above the limit when the book gaps. Both clauses are
implemented exactly as specified. If the realised average sits between 1c and
2c the strategy dies to its own kill condition while winning most of its
trades, and that outcome should be read as the target being too tight, not as
the edge being absent.

## EXITS ARE ALL-OR-NOTHING, AND THAT IS THE HONEST FAILURE MODE

`PolymarketPaperAdapter.simulate_taker_sell` refuses a partial fill. If the bid
side cannot absorb the full position under our limit, the sell does not happen
and THE POSITION STAYS OPEN. If it is still open when the oracle speaks, it
resolves like any other binary and its PnL - very possibly minus the whole
premium - is charged to this strategy.

That is deliberate. A strategy whose entire claim is "we exit before
resolution" must make the case where it CANNOT exit loud and expensive rather
than rounding it away, because that case is precisely where the 99.3% goes. It
is the same treatment temporal_arbitrage gives its unpaired leg.

## WHAT THIS STRATEGY CANNOT SEE (convention 22)

`evaluate()` returns a Decision. It never learns whether that Decision became a
fill - the halt check, the risk gate and the paper adapter all sit downstream
and any of them can refuse. So `max_trades_per_window` counts ATTEMPTS, not
fills, and every row says so via `trade_count_is_attempts_not_fills=True`. The
practical bias: a window whose entries were all refused by the risk gate still
burns its three attempts. Real fill counts come from joining `positions` on
window_ts, never from counting ENTER decisions here.

Exit management is the mirror image and is NOT in `evaluate()`. It runs off
real open positions handed in by the caller (`manage_exit`), so unlike the
entry side it sees actual fills.

## CIRCULARITY, and why the fair value module is kept at arm's length

The one way to make this strategy report edge that does not exist is to derive
fair value from the price it is being compared against. `fair_value.py` reads no
price, no midpoint and no last trade; it reads book DEPTH, capped hard and
correlated-clustered so depth can only ever replace the diffusion view rather
than add to it. Read that module's docstring before changing a constant here.

KILL CONDITION: trailing-50 win rate below 80% once 50 trades exist (this
strategy should have a very high win rate precisely because it exits before
resolution - a 60% win rate here is not a weak edge, it is evidence the exit
model does not work), OR average profit per trade below 2c over 50 trades.
Either clause fires alone. Scored by `backtest/polymarket_harness.py`, which
must score CLOSED trades and RESOLVED trades as two populations and never pool
them - they have different payoff shapes and pooling them turns an unsellable
position into a rounding error. Convention 5's 30bps floor applies on top: at a
50c premium, 2c per trade is 400bps gross, so the floor is not the binding
constraint here, the spread is.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from engine.polymarket.fair_value import (DEFAULT_MODEL_UNCERTAINTY,
                                          FairValueEstimate, PriceTape,
                                          estimate_fair_value)
from strategies.polymarket.base import (GENERAL_BINARY_MARKET_TYPES,
                                        MARKET_TYPE_CRYPTO_UPDOWN,
                                        MARKET_TYPE_WEATHER,
                                        WINDOW_SECONDS, Decision, Leg,
                                        MarketContext, PolymarketStrategy,
                                        effective_ask_for,
                                        effective_stop_distance,
                                        tiered_stop_features,
                                        tiered_stop_price, window_atr)

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

# --- entry ------------------------------------------------------------------

#: Minimum gap between our fair value and the walked entry price. 4c on a ~50c
#: premium is 800bps gross, which leaves room for the round-trip spread and the
#: model-uncertainty haircut and still clears convention 5's 30bps floor.
#: Deliberately conservative: the brief's own note is that a faster bot fires at
#: 1-2c, and we are not faster. EXPIRY: tighten only after the harness scores a
#: win rate at 4c, never because 4c produced too few trades.
EDGE_THRESHOLD = 0.04

#: Floor on the sell limit at the profit target. NOT a cap on the fill.
MIN_PROFIT = 0.01

#: NOMINAL payoff geometry ONLY. **This is no longer the stop.**
#:
#: Until 2026-08-18 this constant WAS the stop: `manage_exit` fired `price_stop`
#: when the bid fell 3c below entry, at every entry price, so a 0.05 fill risked
#: 60% of its premium and a 0.83 fill risked 3.6%. The stop is now
#: `strategies.polymarket.base.tiered_stop_price`, which keys the distance to
#: the entry tier, and it lives in exactly one place for the whole package.
#:
#: What still reads this: `breakeven_win_rate`, and nothing else. That property
#: describes the payoff geometry the variants were SPECIFIED with and is what
#: the four variant docstrings' break-even arithmetic is stated against; it is
#: not a description of the risk a live position now carries. The per-fill
#: number is `breakeven_win_rate_at(entry)`, which reads the tiered stop.
MAX_LOSS = 0.03

#: Fair value must stay at least this far above the entry price. Below it the
#: reason we bought has gone, whatever the bid happens to be doing.
MODEL_STOP_MARGIN = 0.01

#: The ask coming within this of fair value means the mispricing is gone.
CONVERGENCE_EPS = 0.01

#: Seconds after entry at which an unresolved thesis is closed out. The brief's
#: number, and it matches its own claim that a gap corrects in 30-120 seconds:
#: at 60s we are inside that band, so this stop cuts some winners on purpose
#: rather than holding a position whose thesis has stopped paying rent.
TIME_STOP_SEC = 60.0

#: Under this much window left, close regardless. Past here the position is a
#: directional bet on the resolution, which is a different strategy.
WINDOW_CLOSE_EXIT_SEC = 30.0

#: No new entry with less than this left. Derived, not tuned: an entry needs
#: room for the 60s time stop to be reachable before the 30s close-out fires,
#: so the floor is WINDOW_CLOSE_EXIT_SEC + a usable holding period. At exactly
#: 60s remaining a position gets 30s before the close-out cuts it short, and
#: that truncation is reported as `holding_seconds_available`.
MIN_ENTRY_SECONDS_REMAINING = 60.0

#: Entry attempts per 5-minute window. Attempts, not fills - see the docstring.
MAX_TRADES_PER_WINDOW = 3

#: Target size. At ~50c this is the $10 per-trade notional cap exactly, which is
#: why size is scaled DOWN when the entry cap is above 0.50 rather than being
#: silently rejected by the adapter's cap check.
TARGET_SHARES = 20

#: Shares that must be resting within DEPTH_BAND of the best ask before we
#: trade. A 4c gap against a 6-share top level is not an opportunity, it is one
#: stale quote, and this is the gate proposal 001 named as its own open risk.
MIN_BOOK_DEPTH_SHARES = 50
DEPTH_BAND = 0.03

#: Per-trade notional. Matches PolymarketPaperAdapter.notional_cap_usdc and
#: PolymarketRiskGate.DEFAULT_NOTIONAL_CAP_USDC; restated so a size computed
#: here cannot silently exceed a cap enforced somewhere else.
MAX_NOTIONAL_USDC = 10.0

#: Exchange minimum order size, in shares.
MIN_SHARES = 5

#: Tradeable fair-value band. Outside it the EXIT model stops working, which is
#: a different objection from the risk gate's premium band and binds first:
#: below 0.10 there is not 3c of room beneath the entry for MAX_LOSS to mean
#: anything, and above 0.90 this becomes Dan1ro0 concept 4E (near-resolution
#: capture), which Raven's analysis says not to implement without its own
#: position limits and data-quality kill switch. Neither is built.
MIN_TRADEABLE_FAIR_VALUE = 0.10
MAX_TRADEABLE_FAIR_VALUE = 0.90

#: Sell limit for an urgent exit: take whatever the bid side offers. 0.00 is a
#: valid SELL limit (it accepts every level) where it would be an invalid BUY
#: limit. A stop that refuses a bad price is not a stop.
URGENT_SELL_LIMIT = 0.0

PRICE_TICK = 0.01

#: Windows of per-window attempt state kept. A 5m loop would otherwise grow
#: without bound.
STATE_WINDOWS_KEPT = 8

#: The reason a NON-CRYPTO context gets, before any model input is read.
#:
#: It exists so that "this model was never applicable to this market" and "this
#: model ran and could not compute a fair value" are two counters instead of
#: one. Without it a sports market falls through to `self.estimate(ctx)`, which
#: is handed `spot=None` and `window_open=None`, comes back unusable, and the
#: row reads `fair_value_no_spot` - a string that says the BTC spot feed was
#: down. A reader of the skip table would then be looking at a data-quality
#: incident that never happened, on a market that has no spot to be missing.
#: Convention 20: two drop causes never share one counter, and convention 11:
#: could-not-apply is not could-not-compute.
#:
#: Named for the MODEL and not for the market, because that is where the
#: constraint lives. The whole of `engine/polymarket/fair_value.py` is a
#: probability of a crypto price move: displacement from the window open in
#: units of realized sigma, diffused over the seconds left. There is no
#: displacement and no sigma on "will this team win", and there is no version of
#: that model that produces one.
NON_CRYPTO_SKIP_REASON = 'fair_value_model_needs_crypto_spot'


def floor_to_tick(price: float, tick: float = PRICE_TICK) -> float:
    """Snap a limit DOWN onto the tick grid.

    The epsilon matters and is not cosmetic: `0.29 / 0.01` is
    28.999999999999996 in binary floating point, so a bare floor moves a price
    already on the grid down a full tick. On a strategy whose entire margin is
    1-4c, one tick is a quarter of the edge. Same reasoning as the paper
    adapter's `round_to_tick`, restated here so a strategy module does not have
    to import the execution layer.
    """
    if tick <= 0:
        return price
    steps = math.floor(price / tick + 1e-9)
    decimals = max(0, -math.floor(math.log10(tick)))
    return round(steps * tick, decimals)


@dataclass
class ExitDecision:
    """The result of checking ONE open position for an exit.

    Always produced, HOLD included, for the same reason `Decision` always is: a
    position nobody logged a look at is a position nobody can audit, and a
    strategy whose holds are invisible cannot be told apart from one whose exit
    logic never ran (convention 20).
    """

    action: str                       # 'EXIT' | 'HOLD'
    reason: str
    position_id: str = ''
    limit_price: Optional[float] = None
    shares: Optional[float] = None
    features: dict = field(default_factory=dict)

    @property
    def is_exit(self) -> bool:
        return self.action == 'EXIT'

    def to_dict(self) -> dict:
        return {
            'action': self.action,
            'reason': self.reason,
            'position_id': self.position_id,
            'limit_price': self.limit_price,
            'shares': self.shares,
            'features': self.features,
        }


class FairValueArb(PolymarketStrategy):
    """Buy a contract priced below our own fair value; sell when it corrects.

    Scan for mispriced Polymarket contracts. Fire when the market price diverges
    from fair value by more than the edge threshold. Exit when the mispricing
    corrects - NOT at resolution.

    Kill condition: trailing-50 win rate below 80% (this strategy should have a
    very high win rate because it exits before resolution), OR average profit
    per trade below 2c over 50 trades. See the module docstring for the
    break-even arithmetic behind both numbers.
    """

    strategy_name = 'PM_fair_value_arb'
    paper_mode = PAPER_MODE

    #: This strategy manages its own exits. The shadow loop uses this flag to
    #: decide whether to poll `manage_exit` for a position; a strategy without
    #: it holds to resolution, which is what every other strategy here does.
    manages_exits = True

    #: CRYPTO PLUS EVERY GENERAL BINARY PLUS WEATHER (D-316) - and read the
    #: gate in `evaluate` before reading this as "the fair value model works on
    #: a sports market". It does not, and it is refused on the first line
    #: rather than allowed to fail downstream. Weather joined the other three
    #: at D-316 for the identical reason: the gate below is `not
    #: ctx.is_crypto_window`, which already caught weather along with event,
    #: sports and political - the declaration just did not say so. Widening it
    #: costs nothing and turns a silent non-poll into a named, counted skip.
    #:
    #: The two are not in tension. This declaration is about ROUTING: the loop
    #: is allowed to hand this strategy an event, sports or political market
    #: without that being a wiring bug, because the ENTRY MACHINERY here - the
    #: book walk, the depth gate, the notional sizing, the tiered stop, the
    #: whole `manage_exit` ladder - is market-agnostic and is what a future
    #: non-crypto fair value model would be bolted onto. What is crypto-only is
    #: the MODEL, and the model refuses under `NON_CRYPTO_SKIP_REASON` with a
    #: reason that says so in as many words.
    #:
    #: The alternative - declaring crypto only - would make every non-crypto
    #: context an `assert_supports` exception counted under
    #: `strategy_exceptions`, which is the bucket that means "our code broke".
    #: Being handed a sports market is not our code breaking; having no model
    #: for one is a fact about the model, and it belongs in the skip table under
    #: its own name where it can be counted.
    #:
    #: INHERITED BY ALL FOUR VARIANTS. `_wide`, `_patient` and `_hft` are thin
    #: parameter subclasses that override no class attribute, and `_inverse`
    #: calls `super().evaluate(ctx)` and passes every parent SKIP straight
    #: through, so the gate below reaches all five strategies from this one
    #: place (convention 23: a fix at one site is not a fix - unless the one
    #: site is the only site, which a test pins).
    supported_market_types = ((MARKET_TYPE_CRYPTO_UPDOWN, MARKET_TYPE_WEATHER)
                              + GENERAL_BINARY_MARKET_TYPES)

    def __init__(self, edge_threshold: float = EDGE_THRESHOLD,
                 min_profit: float = MIN_PROFIT,
                 max_loss: float = MAX_LOSS,
                 model_stop_margin: float = MODEL_STOP_MARGIN,
                 convergence_eps: float = CONVERGENCE_EPS,
                 time_stop_sec: float = TIME_STOP_SEC,
                 window_close_exit_sec: float = WINDOW_CLOSE_EXIT_SEC,
                 min_entry_seconds_remaining: float = MIN_ENTRY_SECONDS_REMAINING,
                 max_trades_per_window: int = MAX_TRADES_PER_WINDOW,
                 target_shares: int = TARGET_SHARES,
                 min_book_depth_shares: float = MIN_BOOK_DEPTH_SHARES,
                 depth_band: float = DEPTH_BAND,
                 max_notional_usdc: float = MAX_NOTIONAL_USDC,
                 min_shares: int = MIN_SHARES,
                 min_fair_value: float = MIN_TRADEABLE_FAIR_VALUE,
                 max_fair_value: float = MAX_TRADEABLE_FAIR_VALUE,
                 model_uncertainty: float = DEFAULT_MODEL_UNCERTAINTY,
                 atr_windows: int = 12):
        self.edge_threshold = edge_threshold
        self.min_profit = min_profit
        self.max_loss = max_loss
        self.model_stop_margin = model_stop_margin
        self.convergence_eps = convergence_eps
        self.time_stop_sec = time_stop_sec
        self.window_close_exit_sec = window_close_exit_sec
        self.min_entry_seconds_remaining = min_entry_seconds_remaining
        self.max_trades_per_window = max_trades_per_window
        self.target_shares = target_shares
        self.min_book_depth_shares = min_book_depth_shares
        self.depth_band = depth_band
        self.max_notional_usdc = max_notional_usdc
        self.min_shares = min_shares
        self.min_fair_value = min_fair_value
        self.max_fair_value = max_fair_value
        self.model_uncertainty = model_uncertainty
        self.atr_windows = atr_windows

        #: BTC spot observations, feeding the speed and realized-vol inputs.
        #: Sub-window price history exists nowhere else on this path.
        self.tape = PriceTape()
        #: window_ts -> entry ATTEMPTS. See the docstring: not fills.
        self._window_trades: Dict[int, int] = {}

    # -- the stop -----------------------------------------------------------
    #
    # Three thin methods, all delegating to `strategies.polymarket.base`. They
    # exist so `manage_exit` and `evaluate` never inline the rule and so a test
    # can prove a subclass reaches the shared helper rather than a copy of it
    # (convention 22). Nothing here re-derives a distance.

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

    def breakeven_win_rate_at(self, entry_px: float,
                              side: Optional[str] = None) -> float:
        """Break-even for THIS fill: `loss / (gain + loss)` at the tiered stop.

        The `breakeven_win_rate` property answers the same question against the
        SPECIFIED `max_loss`, which no longer sets the stop. Where the two
        disagree, this one describes the position and that one describes the
        spec. Both are on the entry row so a reader is never left guessing which
        number a docstring meant.
        """
        loss = self.stop_distance_for(entry_px, side)
        denom = self.min_profit + loss
        return float('nan') if denom <= 0 else loss / denom

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
    def window_open(ctx: MarketContext) -> Optional[float]:
        """Open of THIS window, from the price bar whose timestamp matches.

        Matched on timestamp rather than taken as "the last bar": a stale candle
        pull would otherwise hand us the previous window's open, and every
        displacement in the fair value would be measured from the wrong place -
        which does not fail loudly, it just biases every estimate.

        This is a BTC exchange bar open, NOT the settlement strike (a Chainlink
        60s TWAP that Gamma does not publish). A few dollars of disagreement
        moves the fair value slightly; it cannot mis-settle anything.
        """
        for w in ctx.windows:
            if w.ts == ctx.window_ts:
                return w.open
        return None

    @staticmethod
    def clock(ctx: MarketContext) -> Optional[float]:
        """Absolute seconds for this observation, or None.

        Derived from the window's own timestamp rather than read off the wall
        clock, so a decision is reproducible from a logged context and a test
        does not have to mock `time`.
        """
        if ctx.seconds_into_window is None:
            return None
        return float(ctx.window_ts) + float(ctx.seconds_into_window)

    def observe(self, ctx: MarketContext) -> bool:
        """Record this cycle's spot on the tape. Returns False if refused."""
        now = self.clock(ctx)
        if now is None or ctx.spot is None:
            return False
        return self.tape.observe(now, ctx.spot)

    def estimate(self, ctx: MarketContext) -> FairValueEstimate:
        """Fair value for the UP side of this window.

        Always returns an estimate; an unusable one carries a named reason and
        must not be traded on (convention 11). Callers that need a specific side
        should use `FairValueEstimate.for_side`.
        """
        now = self.clock(ctx)
        atr_usd = window_atr(ctx.windows, self.atr_windows) if ctx.windows else None
        return estimate_fair_value(
            spot=ctx.spot,
            window_open=self.window_open(ctx),
            atr_usd=atr_usd,
            seconds_remaining=ctx.seconds_remaining,
            up_book=ctx.book('Up'),
            down_book=ctx.book('Down'),
            recent_speed=self.tape.speed(30.0, now),
            baseline_speed=self.tape.baseline_speed(300.0, now),
            realized_sigma_usd=self.tape.realized_sigma(300.0, WINDOW_SECONDS,
                                                        now),
            model_uncertainty=self.model_uncertainty,
        )

    # -- entry --------------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        slug = getattr(ctx.market, 'slug', None)
        # Observe FIRST, on every cycle, including the ones that skip. The tape
        # is what makes the speed and vol signals possible, and a tape that
        # only fills on tradeable cycles is a tape with holes exactly where the
        # market was quiet.
        observed = self.observe(ctx)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            feats.setdefault('tape_samples', len(self.tape.samples))
            feats.setdefault('tape_observed_this_cycle', observed)
            feats.setdefault('trades_this_window',
                             self.trades_this_window(ctx.window_ts))
            # Stated on EVERY row, skips included. Nothing downstream may pick
            # the vendor number up as a measurement or compute a fill rate from
            # these counters.
            feats.setdefault('claimed_win_rate_is_unverified_vendor_number',
                             True)
            feats.setdefault('trade_count_is_attempts_not_fills', True)
            feats.setdefault('exits_before_resolution', True)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market')

        if not ctx.is_crypto_window:
            # THE MODEL WAS NEVER APPLICABLE HERE. Ranked above the clock gate
            # deliberately: on a non-crypto context `seconds_remaining` is also
            # None, so without this line every sports market would come back
            # `no_window_clock` - which reads as "the crypto window clock was
            # missing", a transient wiring fault, on a market that has no
            # 5-minute window for a clock to be missing from.
            #
            # Further down it is worse still. `self.estimate(ctx)` would be
            # handed `spot=None` and `window_open=None`, return unusable, and
            # emit `fair_value_no_spot` - which is the string a real BTC feed
            # outage produces. One counter would then hold a genuine data
            # incident and a permanent structural refusal, and the structural
            # one would dominate it by volume forever.
            #
            # Conventions 11 and 20, in one gate: could-not-apply is its own
            # fact and gets its own name.
            return decide('SKIP', NON_CRYPTO_SKIP_REASON,
                          market_type=ctx.market_type,
                          fair_value_model_is_a_crypto_price_model=True)

        remaining = ctx.seconds_remaining
        if remaining is None:
            # Every gate below is a clock gate. Without a clock this strategy
            # cannot tell entry time from close-out time and must not guess.
            return decide('SKIP', 'no_window_clock')

        if self.trades_this_window(ctx.window_ts) >= self.max_trades_per_window:
            return decide('SKIP', 'max_trades_this_window',
                          max_trades_per_window=self.max_trades_per_window,
                          seconds_remaining=round(remaining, 1))

        if remaining < self.min_entry_seconds_remaining:
            return decide('SKIP', 'too_late_in_window',
                          seconds_remaining=round(remaining, 1),
                          min_entry_seconds_remaining=self.min_entry_seconds_remaining)

        est = self.estimate(ctx)
        if not est.usable:
            # A fair value we could not compute is not a fair value of 0.5.
            return decide('SKIP', 'fair_value_' + (est.reason or 'unusable'),
                          seconds_remaining=round(remaining, 1),
                          fair_value_usable=False,
                          fair_value_reason=est.reason)

        p_up = est.probability
        feats = {
            'seconds_remaining': round(remaining, 1),
            'holding_seconds_available': round(
                max(0.0, remaining - self.window_close_exit_sec), 1),
            'fair_value_up': round(p_up, 6),
            'fair_value_down': round(1.0 - p_up, 6),
            'fair_value_usable': True,
            'edge_threshold': self.edge_threshold,
            'model_uncertainty': est.model_uncertainty,
            'signal_winner': est.census.get('winner_by_cluster', {}),
            'signals_suppressed_correlated': est.census.get(
                'suppressed_correlated', 0),
            'vol_ratio_source': est.census.get('vol_ratio_source'),
            # Confidence IS the fair value of the side we would take. Filled in
            # once the side is chosen; the entry-side value is what the scanner
            # reads, and it is a model output, not a measured win rate.
            'confidence': 0.0,
        }
        feats.update({'fv_' + k: v for k, v in est.inputs.items()})

        if not (self.min_fair_value <= p_up <= self.max_fair_value):
            # Outside the band the EXIT model stops working. See the constant.
            return decide('SKIP', 'fair_value_outside_tradeable_band',
                          min_fair_value=self.min_fair_value,
                          max_fair_value=self.max_fair_value, **feats)

        # Both sides are candidates. The mispricing can sit on either one and
        # they are not mirror images: each has its own book, its own depth and
        # its own spread, so the Down side being 5c cheap is a different
        # observation from the Up side being 5c rich.
        candidates = []
        for side in ('Up', 'Down'):
            fair = est.for_side(side)
            book = ctx.book(side)
            best_ask = None if book is None else book.best_ask
            best_bid = None if book is None else book.best_bid
            candidates.append({
                'side': side, 'fair': fair, 'book': book, 'best_ask': best_ask,
                'best_bid': best_bid,
                'raw_edge': (None if best_ask is None else fair - best_ask),
                # LOGGING ONLY. Nothing below reads this; it exists because the
                # post-mortem could only reach the spread hypothesis indirectly.
                'spread': (None if best_ask is None or best_bid is None
                           else round(best_ask - best_bid, 6)),
            })
        feats['candidate_edges'] = {
            c['side']: (None if c['raw_edge'] is None else round(c['raw_edge'], 4))
            for c in candidates}

        priced = [c for c in candidates if c['raw_edge'] is not None]
        if not priced:
            # No ask on either side. An empty book and a bids-only book are the
            # same fact for a BUY: nothing to lift at any price.
            if all(c['book'] is None for c in candidates):
                return decide('SKIP', 'no_orderbook', **feats)
            return decide('SKIP', 'no_asks', **feats)

        best = max(priced, key=lambda c: c['raw_edge'])
        side = best['side']
        fair = best['fair']
        book = best['book']
        feats.update({
            'outcome_side': side,
            'side_fair_value': round(fair, 6),
            'best_ask': best['best_ask'],
            # We enter by LIFTING THE ASK and every discretionary exit reads the
            # BID, so the strategy's own quoted spread is a cost it pays on the
            # round trip - and `max_loss` is measured against the bid, which
            # means a wide enough spread puts the stop INSIDE the spread at the
            # instant of entry. That was diagnosed from `best_ask` alone and had
            # to be inferred; logging both ends makes it directly measurable.
            # LOGGING ONLY: no threshold, gate or exit rule reads these.
            'best_bid': best['best_bid'],
            'spread': best['spread'],
            'raw_edge': round(best['raw_edge'], 4),
            'confidence': round(fair, 6),
            'confidence_is_model_output_not_measured_win_rate': True,
        })

        # The cap IS the edge. Quote the worst price at which the trade still
        # clears the threshold and gate on the BOOK-WALKED average under it, so
        # a fill several cents inside the cap is reported at what it actually
        # cost rather than at the cap (the house rule in base.Leg.premium).
        cap = floor_to_tick(fair - self.edge_threshold)
        feats['entry_cap'] = cap
        if cap < PRICE_TICK:
            # The threshold eats the whole price. Not an opportunity: there is
            # no limit at or above one tick that still carries the edge.
            return decide('SKIP', 'edge_threshold_exceeds_fair_value', **feats)

        if best['raw_edge'] <= self.edge_threshold:
            # No mispricing worth taking. This is the strategy WORKING, and it
            # is expected to be the overwhelming majority of windows.
            return decide('SKIP', 'edge_below_threshold', **feats)

        depth_limit = round(best['best_ask'] + self.depth_band, 6)
        depth = book.ask_depth(depth_limit)
        feats['depth_band'] = self.depth_band
        feats['ask_depth_within_band'] = depth
        feats['min_book_depth_shares'] = self.min_book_depth_shares
        if depth < self.min_book_depth_shares:
            # A gap against a 6-share top level is one stale quote, not an
            # opportunity, and any size that matters walks straight through it.
            return decide('SKIP', 'insufficient_book_depth', **feats)

        # Size DOWN to the notional cap rather than letting the adapter reject
        # the order. Silently over-sizing would be refused downstream as
        # `over_notional_cap`, which reads as a risk block rather than as
        # "20 shares does not fit in $10 at this price" - two different facts.
        affordable = int(math.floor(self.max_notional_usdc / cap + 1e-9))
        shares = min(self.target_shares, affordable)
        feats['target_shares'] = self.target_shares
        feats['affordable_shares_at_cap'] = affordable
        feats['shares'] = shares
        feats['shares_capped_by_notional'] = shares < self.target_shares
        if shares < self.min_shares:
            # Could not run, did not lose (convention 11, the D-249 shape).
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

        realized_edge = fair - effective
        feats['realized_edge'] = round(realized_edge, 4)
        feats['realized_edge_bps'] = (round(realized_edge / effective * 10_000, 1)
                                      if effective > 0 else None)
        feats['profit_target_price'] = round(effective + self.min_profit, 4)
        # The tiered stop, computed from the WALKED fill rather than from the
        # cap: the cap is what we were willing to pay and `effective` is what
        # the book charged, and a stop keyed to the wrong one of those is a
        # stop at the wrong price on every entry that filled inside its limit.
        feats.update(tiered_stop_features(effective, side))
        feats['breakeven_win_rate_at_tiered_stop'] = round(
            self.breakeven_win_rate_at(effective, side), 6)
        feats['breakeven_win_rate_at_specified_max_loss'] = round(
            self.max_loss / (self.min_profit + self.max_loss), 6) \
            if (self.min_profit + self.max_loss) > 0 else None
        feats['breakeven_win_rate_if_held'] = round(effective, 4)
        feats['notional_usdc'] = round(shares * effective, 4)
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

        Unlike `evaluate`, this sees real fills: `position` is a
        `PaperPosition` the adapter actually opened. Returns an ExitDecision on
        every path, HOLD included.

        Rule order, and why:

          1. `window_close`   Under 30s left the position stops being a
                              mispricing trade and becomes a directional bet on
                              the resolution. Cut regardless of PnL.
          2. `price_stop`     The bid has reached the TIERED stop for this
                              fill - `base.tiered_stop_price(avg_price)`, not a
                              fixed `max_loss`. Take the loss at whatever the
                              book pays; a stop that refuses a bad price is not
                              a stop. On a fill at or below its own tier
                              distance the stop is 0.00, this rule can never
                              fire, and the row says so via
                              `stop_is_structural_floor`.
          3. `profit_target`  The bid pays entry + MIN_PROFIT or better. Booked
                              ahead of the model stop on purpose: if both fire,
                              taking money is right.
          4. `converged`      The ask has come back to within CONVERGENCE_EPS of
                              fair value. The reason we bought is gone. Gated on
                              the bid being at or above entry so a loss never
                              gets logged as a successful convergence.
          5. `model_stop`     Fair value has fallen to at or below entry plus
                              MODEL_STOP_MARGIN. The thesis is dead even though
                              the book has not moved yet - this is the exit that
                              distinguishes a model from a trailing stop.
          6. `time_stop`      60 seconds with none of the above. The gap was
                              supposed to correct inside 30-120s; past the time
                              stop we are holding a coin flip we did not price.

        A book with no bids returns HOLD `no_bid_liquidity`. That is not a safe
        hold, it is an UNSELLABLE position, and if it is still unsellable at
        expiry it resolves. Named separately so a run full of them cannot be
        read as a run of patient holds.
        """
        pid = getattr(position, 'position_id', '')
        entry = float(getattr(position, 'avg_price', 0.0) or 0.0)
        shares = float(getattr(position, 'shares', 0.0) or 0.0)
        opened_ts = getattr(position, 'opened_ts', None)
        window_ts = getattr(position, 'window_ts', None)
        outcome_side = getattr(position, 'outcome_side', None)

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
            'fair_value': (None if fair_value is None else round(fair_value, 6)),
            'unrealized_at_bid': (None if best_bid is None
                                  else round((best_bid - entry) * shares, 4)),
            'profit_target_price': round(entry + self.min_profit, 4),
            'exits_before_resolution': True,
        }

        # The tiered stop. Computed from the REAL fill (`position.avg_price`)
        # on the REAL side, before the unreadable-position guard below can send
        # us home, so that every row carries either a stop or a named reason it
        # has none. `stop_price` used to be `entry - self.max_loss` here, which
        # is the line that made a 3c stop a 60% loss on a 0.05 fill.
        stop_px: Optional[float] = None
        if entry > 0.0:
            stop_px = self.stop_price_for(entry, outcome_side)
            feats.update(tiered_stop_features(entry, outcome_side))
        else:
            # Convention 11 and 20: a stop we could not compute is its own
            # fact, not a stop of 0.00 and not a missing key.
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
            # `max_loss` is kept on the row under its old key so an existing
            # reader of `sell:price_stop` rows does not silently lose the
            # column - but it is now the TIERED distance for this fill, not the
            # instance constant. `stop_distance_nominal` says what the tier
            # asked for, `stop_is_structural_floor` says when the two differ.
            return exit_now('price_stop', URGENT_SELL_LIMIT,
                            max_loss=round(entry - stop_px, 6),
                            max_loss_specified_not_used=self.max_loss)

        if best_bid >= entry + self.min_profit - 1e-12:
            # Limit at the target, not at the bid: if walking depth for our
            # full size would average below the target, we would rather not
            # sell than book a fill that fails the rule we exited on.
            return exit_now('profit_target',
                            floor_to_tick(entry + self.min_profit),
                            min_profit=self.min_profit)

        if (fair_value is not None and best_ask is not None
                and best_ask >= fair_value - self.convergence_eps
                and best_bid >= entry - 1e-12):
            return exit_now('converged', floor_to_tick(entry),
                            convergence_eps=self.convergence_eps)

        if fair_value is not None \
                and fair_value <= entry + self.model_stop_margin + 1e-12:
            return exit_now('model_stop', URGENT_SELL_LIMIT,
                            model_stop_margin=self.model_stop_margin)

        if age is not None and age >= self.time_stop_sec:
            return exit_now('time_stop', URGENT_SELL_LIMIT,
                            time_stop_sec=self.time_stop_sec)

        return hold('waiting_for_convergence')

    def exit_decisions(self, positions, books: Dict[str, object], now: float,
                       fair_value_by_side: Optional[Dict[str, float]] = None
                       ) -> List[ExitDecision]:
        """`manage_exit` over a batch. Convenience for callers and tests.

        `books` is keyed by token_id, matching `MarketContext.books`. A position
        whose token has no book still gets a decision - it just gets a HOLD
        naming the missing book, because a position we could not look at is not
        a position we decided to keep.
        """
        fair_value_by_side = fair_value_by_side or {}
        out: List[ExitDecision] = []
        for pos in positions or ():
            if getattr(pos, 'strategy', None) != self.strategy_name:
                continue
            book = books.get(getattr(pos, 'token_id', None))
            fair = fair_value_by_side.get(getattr(pos, 'outcome_side', ''))
            out.append(self.manage_exit(pos, book, now, fair_value=fair))
        return out
