"""Liq Cascade Chaser: buy the continuation side of a BTC liquidation cascade.

Ported from moondevonyt's `liq_cascade_chaser.py`. His thresholds are preserved.
The MoonDev API liq feed, his tick feed, the wallet client and the live order
path are gone; the liquidation tape now comes from OUR OWN recorder
(`engine/feeds/liquidation_recorder.py`) via `liquidation_feed.py`.

THESIS (his, not ours): forced liquidations are price-insensitive flow. The
engine must close the position at whatever the book offers, so price gets shoved
further in the same direction and tends to keep going through the end of the 5m
window. Early in the window Polymarket still prices that side at 50-85c. Buy it
as a taker and hold to resolution.

    longs liquidated   -> forced SELLING  -> buy Down
    shorts liquidated  -> forced BUYING   -> buy Up

THE SIDE SEMANTIC IS THE ONE THING THAT CAN SILENTLY INVERT THIS WHOLE
STRATEGY. The `liquidations.side` column already stores WHICH SIDE GOT
LIQUIDATED - the recorder inverted the exchange's order side on the way in. This
module never inverts anything; it calls `continuation_outcome()`, which is the
single mapping point in the package, and `tests/test_liquidation_strategies.py`
asserts both directions produce opposite outcome sides. A second inversion here
raises nothing, drops no rows and moves no counter.

## HIS NUMBERS, NOT EVIDENCE. All of these are UNVERIFIED VENDOR NUMBERS.

  58.8% directional on 187 liq signals (his March fleet, re-graded against
  candles). Beat his CVD (51.4%) and MACD (52.4%) control groups, which is the
  only reason to look at liquidations at all.
  71.7%-87.0% win rates in the measured pocket, from n=76 over ~4 days.
  95% window continuation on 52 weeks of 1m candles, for the tape-confirmed
  subset only.

Every row this strategy emits carries
`claimed_win_rate_is_unverified_vendor_number=True`. Convention 3: unverified
until our own harness says otherwise.

## GROSS EDGE IN BPS, BEFORE THE LOGIC (convention 5), AND WHY THE BAND IS
## NEGATIVE AT ITS OWN TOP

On a binary, edge is per dollar of PREMIUM, not per dollar of notional - a share
bought at p returns 1.00 or 0.00, so EV = win_rate/p - 1. The 30bps
dead-on-arrival bar was written for spot round-trips and is not the binding
constraint here; the binding constraint is win rate versus entry price, and they
are one number, not two.

    entry 0.50, at his 58.8%   ->  0.588/0.50 - 1 = +17.6%  = ~1,760 bps
    entry 0.588                ->  exactly zero
    entry 0.85, at his 58.8%   ->  0.588/0.85 - 1 = -30.8%  = ~-3,080 bps

So HIS OWN DIRECTIONAL NUMBER makes the top of his own 0.50-0.85 band
substantially negative. The 71.7-87.0% pocket is what would rescue it, and that
is n=76 over four days, which convention 7 calls a shrug in either direction.

We did NOT move his band to 0.588. Picking a threshold off a vendor number we do
not believe is convention 17's exact mistake in the other direction. Instead
every row stamps `entry_above_vendor_breakeven` and
`vendor_breakeven_entry=0.588`, so the scorer can split the population at that
line WITHOUT a re-run and tell us whether the expensive half is the loser his
arithmetic says it is. If it is, the cap becomes 0.588 and that needs a
D-number.

## THE ONE LOOSENING IN THIS PACKAGE, NAMED (it is not a tightening)

His entry needs BOTH halves of the "95% continuation signature": the window has
already moved >= 0.15% in the liq direction, AND the in-window tick rate is >=
2x the trailing-hour rate. We have no tick feed at all - not a thin one, none -
so the SECOND half is DROPPED, not approximated.

Every other deviation in this package tightens. This one removes a gate, which
means this strategy fires on a strictly LARGER population than his did, and its
results are NOT comparable to his 95%/58.8% figures. `TICK_RATE_GATE_AVAILABLE
= False` and `tick_rate_confirmation_applied: False` on every row exist so that
nobody can compare them by accident. Restoring the gate needs a trade tape we do
not record; that is a feed, not a threshold.

The FIRST half is kept and is required. It is measured from the current 5m bar's
OPEN against live spot - not from the strike. The strike is a measured Chainlink
proxy with a 5bps noise floor; the window open is an exchange bar we already
pull, a few dollars of disagreement only nudges the trigger instant, and it
cannot mis-settle anything (the same reasoning `temporal_arbitrage.window_open`
is built on). This strategy therefore does NOT set `needs_strike`.

## DEVIATIONS THAT TIGHTEN, all logged

  1. He places the taker buy, waits 5s and cancels. We gate on the BOOK-WALKED
     effective average for the full intended size, and the 0.50-0.85 band is
     judged on that, never on the best ask. House rule; the other five follow it.
  2. One ENTER per window, tracked in a ring of per-window state. His 5s
     cancel-and-skip-window is the same rule expressed through an order client
     we do not have. Note this counts ATTEMPTS: `evaluate()` never learns
     whether the halt check, the risk gate or the paper adapter accepted it, so
     `entry_attempts_are_not_fills` is stamped True on every row.
  3. His daily -$60 stop and trailing-30 win-rate kill switch are NOT in this
     class. They are portfolio-level controls and this repo already has them
     (`engine/risk.py`, `engine/halt.py`). A strategy that halts itself is a
     second, invisible kill switch.

## CAN THIS FIRE TODAY?

Only once the recorder has run. As of 2026-08-18 the `liquidations` table exists
with ZERO rows, so this strategy returns `liquidation_feed_empty` on every
cycle - a NOT_TESTED-shaped reason, never `no_cascade`. It needs
`min_history_sec` (= the 120s lookback) of recorded tape before it can produce
its first real evaluation, and nothing about it needs a code change at that
point.

KILL CONDITION: dies if `backtest/polymarket_harness.py` scores its resolved win
rate below its own realised average entry price over 50 or more resolved trades
- that is the break-even, and on a binary there is no other one. Fewer than 50
resolved trades is NOT_TESTED, not FAIL (conventions 7 and 11). It also dies if
the harness reports that the sub-0.588 and above-0.588 halves BOTH score below
their own entry, since the band split is the only structural repair available
before the thesis itself is the problem.
"""
import math
from typing import Dict, Optional

from strategies.polymarket.base import (Decision, Leg, MarketContext,
                                        PolymarketStrategy, effective_ask_for)
from strategies.polymarket.liquidation_feed import (DEFAULT_DB_PATH,
                                                    DEFAULT_STALE_AFTER_SEC,
                                                    DEFAULT_SYMBOL_LIKE,
                                                    continuation_outcome,
                                                    now_from_context,
                                                    read_liquidation_window)

# Never False in this repo. moondevonyt ships this False ("LIVE FIRE!").
PAPER_MODE = True

# -- the liquidation trigger -------------------------------------------------

#: VENDOR NUMBER, UNVERIFIED. His `MIN_LIQ_USD`. Dominant-side liquidation USD
#: in the trailing window that counts as a cascade. Proposal 003 argues this is
#: far too small to move BTC SPOT meaningfully (it raised the bar to $50M for a
#: spot port against a 22bps round-trip cost floor). That argument does not
#: transfer: here we are not paying a spot round trip and not collecting a price
#: move, we are buying a binary whose price the cascade may not have moved yet.
#: A cascade too small to move spot is exactly the one the book has not priced.
MIN_LIQ_USD = 10_000.0

#: VENDOR NUMBER, UNVERIFIED. His `LIQ_LOOKBACK_SEC`. Trailing 2 minutes.
LIQ_LOOKBACK_SEC = 120.0

#: HOUSE, and deliberately NON-BINDING at 1.0. His implicit rule is
#: `dominant > other`, i.e. a ratio strictly above 1.0, so 10,001 against
#: 10,000 counts as a cascade for him. That is a balanced tape, not a cascade.
#: We did not invent a replacement: `liq_dominance_ratio` is stamped on every
#: row so the real threshold can be read off the shadow log rather than guessed
#: now (convention 17). Raising this is a sweep parameter, not an edit.
DOMINANCE_RATIO_MIN = 1.0

#: HOUSE, NON-BINDING at 1. One $10k print is not a cascade in the plain-English
#: sense, but 3 or 5 would be a number nobody measured. Stamped, not enforced.
MIN_LIQ_COUNT = 1

# -- tape confirmation (first half kept, second half dropped) ----------------

#: VENDOR NUMBER, UNVERIFIED. His `MIN_MOVE_PCT = 0.15`, which is in PERCENT.
#: Ours is the unscaled RATIO, compared against `(spot - open) / open`. Losing
#: the `%` is the single easiest thing to break in this port: 0.15 as a ratio is
#: a 15% five-minute move, which BTC does not make, and the strategy would look
#: alive while never firing. Same trap `mid_price_continuation` documents.
MIN_MOVE_PCT = 0.0015

#: His `VOLUME_MULT = 2.0` is NOT implemented. We record no trade tape, so there
#: is no tick rate to compare. See "THE ONE LOOSENING" above. This flag exists
#: to be stamped on rows, not to be flipped: flipping it needs a feed.
TICK_RATE_GATE_AVAILABLE = False
VENDOR_TICK_RATE_MULT = 2.0     # what the dropped gate would have been

# -- price, timing, book -----------------------------------------------------

#: VENDOR NUMBERS, UNVERIFIED. His `PRICE_ZONE`. Below 0.50 the book disagrees
#: with the liquidation about direction; above 0.85 the fee eats what is left.
ENTRY_BAND_LOW = 0.50
ENTRY_BAND_HIGH = 0.85

#: Derived from his 58.8% directional claim, NOT a threshold. Stamped so the
#: scorer can split the population. See the edge arithmetic above.
VENDOR_DIRECTIONAL_RATE = 0.588
VENDOR_BREAKEVEN_ENTRY = 0.588

#: VENDOR NUMBERS, UNVERIFIED. His `MIN_ELAPSED` / `MAX_ELAPSED`: minutes 0-3
#: only, after a few seconds have passed so the window open is established.
MIN_ELAPSED_SEC = 10.0
MAX_ELAPSED_SEC = 180.0

#: VENDOR NUMBER, UNVERIFIED. His `MAX_SPREAD`. Wider than 5c and the book is
#: too thin to lift without the spread being most of the trade.
MAX_SPREAD = 0.05

#: VENDOR NUMBER, UNVERIFIED. His `BASE_SIZE_USD`, flat validation stake.
BASE_SIZE_USD = 15.0
MIN_SHARES = 5                  # Polymarket minimum order size

#: Ring size for per-window entry state. Same as `temporal_arbitrage`.
STATE_WINDOWS_KEPT = 8


def shares_for(usd: float, price: float, min_shares: int = MIN_SHARES) -> float:
    """His sizing: floor(stake / price), never below the exchange minimum."""
    if price <= 0:
        return float(min_shares)
    return float(max(min_shares, math.floor(usd / price)))


class LiqCascadeChaser(PolymarketStrategy):
    """Buy the continuation side of a confirmed BTC liquidation cascade."""

    strategy_name = 'PM_liq_cascade_chaser'
    paper_mode = PAPER_MODE

    def __init__(self, min_liq_usd: float = MIN_LIQ_USD,
                 lookback_sec: float = LIQ_LOOKBACK_SEC,
                 dominance_ratio_min: float = DOMINANCE_RATIO_MIN,
                 min_liq_count: int = MIN_LIQ_COUNT,
                 min_move_pct: float = MIN_MOVE_PCT,
                 band_low: float = ENTRY_BAND_LOW,
                 band_high: float = ENTRY_BAND_HIGH,
                 min_elapsed_sec: float = MIN_ELAPSED_SEC,
                 max_elapsed_sec: float = MAX_ELAPSED_SEC,
                 max_spread: float = MAX_SPREAD,
                 usd_size: float = BASE_SIZE_USD,
                 min_shares: int = MIN_SHARES,
                 db_path: str = DEFAULT_DB_PATH,
                 symbol_like: str = DEFAULT_SYMBOL_LIKE,
                 stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
                 shares=None):
        self.min_liq_usd = min_liq_usd
        self.lookback_sec = lookback_sec
        self.dominance_ratio_min = dominance_ratio_min
        self.min_liq_count = min_liq_count
        self.min_move_pct = min_move_pct
        self.band_low = band_low
        self.band_high = band_high
        self.min_elapsed_sec = min_elapsed_sec
        self.max_elapsed_sec = max_elapsed_sec
        self.max_spread = max_spread
        self.usd_size = usd_size
        self.min_shares = min_shares
        self.db_path = db_path
        self.symbol_like = symbol_like
        self.stale_after_sec = stale_after_sec
        #: Override the USD-derived size. Tests and sweeps only.
        self.shares = shares
        #: window_ts -> True once an ENTER has been ATTEMPTED for that window.
        self._entered: Dict[int, bool] = {}

    # -- per-window state ---------------------------------------------------

    def _mark_entered(self, window_ts: int) -> None:
        self._entered[window_ts] = True
        if len(self._entered) > STATE_WINDOWS_KEPT:
            for ts in sorted(self._entered)[:-STATE_WINDOWS_KEPT]:
                self._entered.pop(ts, None)

    def entered_this_window(self, window_ts: int) -> bool:
        return bool(self._entered.get(window_ts))

    def intended_shares(self, price: float) -> float:
        if self.shares is not None:
            return float(self.shares)
        return shares_for(self.usd_size, price, self.min_shares)

    @staticmethod
    def window_open(ctx: MarketContext) -> Optional[float]:
        """Open of THIS window, matched on timestamp, never assumed.

        Copied in spirit from `temporal_arbitrage.window_open`, and matched on
        `w.ts == ctx.window_ts` for the same reason: a stale candle pull would
        otherwise hand back the PREVIOUS window's open and the move would be
        measured from the wrong place, in the wrong direction about half the
        time. No match is a skip, never a substitution.

        This is a BTC exchange bar open, NOT the settlement strike.
        """
        for w in ctx.windows:
            if w.ts == ctx.window_ts:
                return w.open
        return None

    # -- decision -----------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        slug = getattr(ctx.market, 'slug', None)
        now_s, clock_source = now_from_context(ctx)

        liq = read_liquidation_window(
            now_s=now_s, lookback_sec=self.lookback_sec,
            db_path=self.db_path, symbol_like=self.symbol_like,
            min_history_sec=self.lookback_sec,
            stale_after_sec=self.stale_after_sec)

        base = liq.features()
        base.update({
            'liq_clock_source': clock_source,
            'min_liq_usd': self.min_liq_usd,
            # Stated on EVERY row, skips included, so no later reader can pick
            # a vendor number up as a measurement or compare our population to
            # his (convention 3, and the dropped tick-rate gate above).
            'claimed_win_rate_is_unverified_vendor_number': True,
            'tick_rate_confirmation_applied': TICK_RATE_GATE_AVAILABLE,
            'vendor_tick_rate_mult_not_applied': VENDOR_TICK_RATE_MULT,
            'vendor_directional_rate': VENDOR_DIRECTIONAL_RATE,
            'vendor_breakeven_entry': VENDOR_BREAKEVEN_ENTRY,
            'entry_attempts_are_not_fills': True,
            'confidence': VENDOR_DIRECTIONAL_RATE,
        })

        def decide(action, reason, legs=None, **feats):
            merged = dict(base)
            merged.update(feats)
            merged.setdefault('paper_mode', self.paper_mode)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=merged)

        # 1. Could we read the tape at all? Four distinct NOT_TESTED-shaped
        #    reasons, none of which is "no cascade" (conventions 11 and 20).
        if not liq.ok:
            return decide('SKIP', liq.reason)

        # 2. The signal itself.
        side = liq.dominant_side
        if side is None and liq.total_usd > 0.0:
            # Both sides being flushed equally is a two-way squeeze, not a
            # cascade, and it has no continuation direction to buy. Separated
            # from `no_cascade` because the USD is large and the diagnosis
            # ("nothing happened" vs "both things happened") is opposite.
            return decide('SKIP', 'balanced_liq_tape')
        if liq.dominant_usd < self.min_liq_usd:
            # RAN and found nothing. The only result-shaped skip in this block.
            return decide('SKIP', 'no_cascade')
        ratio = liq.dominance_ratio
        if ratio is not None and ratio < self.dominance_ratio_min:
            return decide('SKIP', 'liq_not_dominant_enough')
        if liq.dominant_count < self.min_liq_count:
            return decide('SKIP', 'insufficient_liq_count')

        outcome_side = continuation_outcome(side)
        if outcome_side is None:
            # Unreachable given `dominant_side` returns 'long'/'short'/None and
            # None was handled above. Kept because a silently-None outcome side
            # downstream would become a bearish signal by default in
            # `decision_to_signal`, which is the failure this whole module is
            # written to prevent.
            return decide('SKIP', 'unmappable_liquidated_side')

        feats = {'outcome_side_planned': outcome_side}

        # 3. Tape confirmation, first half only. See the module docstring.
        open_px = self.window_open(ctx)
        feats['window_open'] = open_px
        if open_px is None or open_px <= 0:
            return decide('SKIP', 'no_window_open_bar', **feats)
        if ctx.spot is None:
            return decide('SKIP', 'no_spot', **feats)

        move_pct = (ctx.spot - open_px) / open_px
        # Signed INTO the liquidation's direction: a long flush should have
        # pushed price down, so a Down cascade wants a negative move.
        signed = move_pct if outcome_side == 'Up' else -move_pct
        feats.update({
            'spot': ctx.spot,
            'window_move_pct': round(move_pct, 6),
            'window_move_bps': round(move_pct * 10_000, 2),
            'move_in_liq_direction_bps': round(signed * 10_000, 2),
            'min_move_bps': round(self.min_move_pct * 10_000, 2),
        })
        if signed < self.min_move_pct:
            # Either the tape has not moved enough yet, or - if `signed` is
            # negative - it moved AGAINST the liquidations, which is the market
            # absorbing the flow. Both are "the continuation signature is not
            # there", which is what this one reason says.
            return decide('SKIP', 'move_not_confirming_liq_direction', **feats)

        # 4. Timing. Minutes 0-3 of the window only.
        elapsed = ctx.seconds_into_window
        feats['seconds_into_window'] = elapsed
        if elapsed is not None:
            if elapsed < self.min_elapsed_sec:
                return decide('SKIP', 'too_early_in_window', **feats)
            if elapsed > self.max_elapsed_sec:
                return decide('SKIP', 'late_in_window', **feats)

        if self.entered_this_window(ctx.window_ts):
            return decide('SKIP', 'already_entered_this_window', **feats)

        # 5. The book.
        if ctx.market is None:
            return decide('SKIP', 'no_market', **feats)
        book = ctx.book(outcome_side)
        if book is None:
            return decide('SKIP', 'no_orderbook', **feats)

        best_ask = book.best_ask
        feats['best_ask'] = best_ask
        if best_ask is None:
            return decide('SKIP', 'no_asks', **feats)

        spread = book.spread
        feats['spread'] = None if spread is None else round(spread, 4)
        if spread is None:
            # One-sided book. His MAX_SPREAD gate cannot be evaluated, and
            # treating "no bids at all" as a tight spread would invert the gate.
            return decide('SKIP', 'no_bids_for_spread', **feats)
        if spread > self.max_spread:
            return decide('SKIP', 'spread_too_wide', **feats)

        # Size off the best ask, then judge the band on the walked average -
        # a 0.52 top-of-book quote for 4 shares is not a 0.52 entry for 28.
        shares = self.intended_shares(best_ask)
        depth = book.ask_depth(self.band_high)
        feats['intended_shares'] = shares
        feats['ask_depth_at_cap'] = depth
        if depth < shares:
            return decide('SKIP', 'insufficient_ask_depth', **feats)

        effective = effective_ask_for(book, shares, self.band_high)
        feats['effective_ask'] = (None if effective is None
                                  else round(effective, 4))
        if effective is None:
            return decide('SKIP', 'unfillable_at_cap', **feats)
        feats['slippage_vs_top'] = round(effective - best_ask, 4)

        if effective > self.band_high:
            return decide('SKIP', 'effective_ask_above_band', **feats)
        if effective < self.band_low:
            # Cheaper than the floor means the book flatly disagrees that this
            # side is winning, while we are looking at the flow that should
            # have made it win. His logs say that pocket loses.
            return decide('SKIP', 'effective_ask_below_band', **feats)

        feats['limit_price'] = self.band_high
        # On a binary the entry price IS the break-even win rate. Convention 8
        # is satisfied structurally: the stop is BINARY_STOP = 0.00, strictly
        # below any entry `decision_to_signal` will accept.
        feats['breakeven_win_rate'] = round(effective, 4)
        feats['entry_above_vendor_breakeven'] = bool(
            effective > VENDOR_BREAKEVEN_ENTRY)

        self._mark_entered(ctx.window_ts)
        return decide('ENTER', '',
                      legs=[Leg(outcome_side=outcome_side,
                                limit_price=self.band_high,
                                order_type='taker',
                                shares=shares,
                                expected_price=effective)],
                      **feats)
