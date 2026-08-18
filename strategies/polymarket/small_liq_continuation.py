"""Small-Liq Continuation: the same cascade, bought in the cheap seats.

Ported from moondevonyt's `small_liq_continuation.py`. His thresholds are
preserved. The MoonDev API liq feed, the wallet client and the live order path
are gone; the liquidation tape comes from OUR OWN recorder
(`engine/feeds/liquidation_recorder.py`) via `liquidation_feed.py`.

THESIS (his, not ours): $25K-$500K of one-sided BTC liquidations inside two
minutes is still forced flow and still tends to continue through the end of the
5m window, but it is not dramatic enough for the book to have priced it, so the
continuation side is still 30-45c. Buy it as a taker and hold to resolution.

    longs liquidated   -> forced SELLING  -> buy Down
    shorts liquidated  -> forced BUYING   -> buy Up

SIDE SEMANTIC: `liquidations.side` is WHICH SIDE GOT LIQUIDATED - already
inverted by the recorder. This module never inverts anything; it calls
`continuation_outcome()`, the single mapping point in the package. See
`liq_cascade_chaser`'s docstring and the both-directions test.

## HIS NUMBERS, NOT EVIDENCE. UNVERIFIED VENDOR NUMBERS, and the good part is
## the CONTROL GROUP, not the headline.

  Liq-signal fills at 0.30-0.40: 48.8% win at 34.1c average, +43% EV, n=41.
  Control, same fill mechanic on MACD/CVD signals: 18-31% win, -8% to -48% EV.
  Sub-0.30 liq fills: 16.7% win. That is where the 0.30 FLOOR comes from.

The control group is why this is worth shadowing at all: same entry style, same
market, different signal, opposite outcome. That isolates the liquidation as the
thing carrying the information. It does not make n=41 a large sample - convention
7, a PASS on 87 trades is a shrug, and 41 is half of that.

HIS OWN CAVEAT, which we keep: those +43% fills were resting BIDS. This bot is a
TAKER and will pay 1-3c more, so the realistic edge is lower and possibly much
lower. And the 58.8% liq-direction figure he quotes is POOLED across all
liquidation sizes; this tier's own directional accuracy was never measured
separately.

## GROSS EDGE IN BPS, BEFORE THE LOGIC (convention 5)

On a binary the edge is per dollar of PREMIUM: EV = win_rate/entry - 1, and the
entry price IS the break-even win rate. At his 48.8%:

    entry 0.30 (band floor)  ->  0.488/0.30 - 1 = +62.7%  = ~6,270 bps
    entry 0.37 (taker-adjusted mid-band) ->        +31.9%  = ~3,190 bps
    entry 0.45 (band cap)    ->  0.488/0.45 - 1 =  +8.4%   =   ~840 bps
    entry 0.488              ->  exactly zero

So unlike `liq_cascade_chaser` - whose band is negative at its own top under its
own vendor number - this whole band is positive under his. That is not evidence
that it is better. It is evidence that HIS BAND WAS CHOSEN AROUND HIS MEASURED
CELL, which is precisely what makes an n=41 number unsafe to trust: the band and
the result come from the same 41 fills. The number to watch is the realised
average entry against the realised win rate, and nothing else.

The 30bps dead-on-arrival bar (convention 5) was written for spot round trips
and is not the binding constraint here.

## THE TWO LIQ BOTS ARE SEPARATED BY PRICE, NOT BY SIZE. Read this before
## pooling them.

His `LIQ_MEGA_USD = 500_000` skip exists so this bot never takes "the big bot's
trade". But our `liq_cascade_chaser` has NO upper size bound, so a $200K cascade
qualifies for both. What actually keeps them apart is that their price bands are
disjoint: 0.30-0.45 here, 0.50-0.85 there. At any one instant, on any one book,
at most one of them can enter.

Across a window they CAN both enter, on the SAME side, if the book walks from
0.44 up through 0.50 while the cascade is still inside the lookback. That is one
directional bet held twice, and the place to cap it is the portfolio risk gate
(`engine/risk.py`), never a strategy quietly checking on its sibling. Two
strategies coordinating through shared state is how a correlated position
becomes invisible to the thing whose job is counting it.

Their results must be scored as SEPARATE populations regardless. Same signal,
different price, is the whole difference between them - as his own forensics
show, on a binary that is a different strategy, not a different setting.

## DEVIATIONS, all tightening except where marked, all logged

  1. The 0.30-0.45 band is judged on the BOOK-WALKED effective average for the
     full intended size, not on the best ask. House rule.
  2. One ENTER per window, from a ring of per-window state - his rule, ours
     expressed without an order client. It counts ATTEMPTS: `evaluate()` never
     learns whether the halt check, the risk gate or the paper adapter accepted
     it, so `entry_attempts_are_not_fills` is True on every row.
  3. His daily -$30 stop is NOT here. Portfolio-level control, and this repo
     already has one. A strategy that halts itself is a second, invisible kill
     switch.
  4. NOT a deviation but a contradiction in HIS OWN sources, resolved in favour
     of his code: the README says ">= $100K -> 1.5x size (that tier measured
     +29% EV)" while the code comment on the same constant says the "$25K-$100K
     cell was +29% EV". Those name opposite tiers. We implement the CODE (>=
     $100K gets the kicker) and stamp `size_kicker_applied` on every row so the
     scorer can separate kicked from un-kicked trades - a size multiplier makes
     per-trade PnL non-identically-distributed, and a pooled average across both
     describes neither.

## CAN THIS FIRE TODAY?

Only once the recorder has run. As of 2026-08-18 the `liquidations` table exists
with ZERO rows, so this returns `liquidation_feed_empty` every cycle - a
NOT_TESTED-shaped reason, never `no_cascade`. It needs 120s of recorded tape
before its first real evaluation. Unlike `liq_cascade_chaser` it needs NO price
tape, NO spot and NO strike: liquidations plus a book are the whole input, which
makes it the earlier of the two to produce a real result.

KILL CONDITION: dies if `backtest/polymarket_harness.py` scores its resolved win
rate below its own realised average entry price over 50 or more resolved trades.
On a binary that comparison is the entire test; there is no other break-even.
Fewer than 50 resolved trades is NOT_TESTED, not FAIL (conventions 7 and 11).
It also dies if the realised average entry drifts above 0.488 - his own
directional number - because at that point the band has stopped being the cheap
seats and the only claimed edge is gone before the win rate gets a say.
"""
import math
from typing import Dict

from strategies.polymarket.base import (Decision, Leg, MarketContext,
                                        PolymarketStrategy, effective_ask_for)
from strategies.polymarket.liquidation_feed import (DEFAULT_DB_PATH,
                                                    DEFAULT_STALE_AFTER_SEC,
                                                    DEFAULT_SYMBOL_LIKE,
                                                    continuation_outcome,
                                                    now_from_context,
                                                    read_liquidation_window)

# Never False in this repo. moondevonyt ships this False ("LIVE on AUG14").
PAPER_MODE = True

# -- the liquidation trigger -------------------------------------------------

#: VENDOR NUMBER, UNVERIFIED. His `LIQ_MIN_USD`. Floor of the tier he mined.
LIQ_MIN_USD = 25_000.0

#: VENDOR NUMBER, UNVERIFIED. His `LIQ_MEGA_USD`. At or above this he hands the
#: trade to the big-cascade bot. See "SEPARATED BY PRICE, NOT BY SIZE" above:
#: in our port this skip does NOT make the two populations disjoint, the price
#: bands do.
LIQ_MEGA_USD = 500_000.0

#: VENDOR NUMBER, UNVERIFIED. His `KICKER_USD` / `KICKER_MULT`. See deviation 4:
#: his README and his code disagree about which tier earned the +29% EV this
#: number is justified by.
KICKER_USD = 100_000.0
KICKER_MULT = 1.5

#: VENDOR NUMBER, UNVERIFIED. His `LIQ_LOOKBACK_SEC`. Trailing 2 minutes.
LIQ_LOOKBACK_SEC = 120.0

#: HOUSE, NON-BINDING at 1.0. His implicit rule is `dominant > other`. Same
#: reasoning as `liq_cascade_chaser.DOMINANCE_RATIO_MIN`: the ratio is stamped
#: on every row so the threshold can be measured instead of guessed
#: (convention 17).
DOMINANCE_RATIO_MIN = 1.0

#: HOUSE, NON-BINDING at 1. Stamped, not enforced.
MIN_LIQ_COUNT = 1

# -- price and timing --------------------------------------------------------

#: VENDOR NUMBERS, UNVERIFIED. His `PRICE_ZONE`. The floor is load-bearing: his
#: sub-0.30 liq fills won 16.7%. The cap hands 0.45+ to `liq_cascade_chaser`.
ENTRY_BAND_LOW = 0.30
ENTRY_BAND_HIGH = 0.45

#: His pooled directional claim, used ONLY as a stamped reference point for the
#: scorer. Not a threshold, never compared against inside this class.
VENDOR_WIN_RATE = 0.488
VENDOR_BREAKEVEN_ENTRY = 0.488

#: VENDOR NUMBERS, UNVERIFIED. His `TIME_BAND`, in seconds REMAINING (not
#: elapsed - the two are the same constant read backwards and swapping them
#: would invert the gate silently).
SECONDS_LEFT_MIN = 60.0
SECONDS_LEFT_MAX = 240.0

#: VENDOR NUMBER, UNVERIFIED. His `USD_SIZE`, flat base stake.
USD_SIZE = 5.0
MIN_SHARES = 5                  # Polymarket minimum order size

#: Ring size for per-window entry state. Same as `temporal_arbitrage`.
STATE_WINDOWS_KEPT = 8


def shares_for(usd: float, price: float, min_shares: int = MIN_SHARES) -> float:
    """stake/price floored to 2dp, never below the exchange minimum."""
    if price <= 0:
        return float(min_shares)
    return max(float(min_shares), math.floor((usd / price) * 100) / 100)


class SmallLiqContinuation(PolymarketStrategy):
    """Buy the continuation side of a $25K-$500K BTC liquidation burst at 30-45c."""

    strategy_name = 'PM_small_liq_continuation'
    paper_mode = PAPER_MODE

    def __init__(self, liq_min_usd: float = LIQ_MIN_USD,
                 liq_mega_usd: float = LIQ_MEGA_USD,
                 kicker_usd: float = KICKER_USD,
                 kicker_mult: float = KICKER_MULT,
                 lookback_sec: float = LIQ_LOOKBACK_SEC,
                 dominance_ratio_min: float = DOMINANCE_RATIO_MIN,
                 min_liq_count: int = MIN_LIQ_COUNT,
                 band_low: float = ENTRY_BAND_LOW,
                 band_high: float = ENTRY_BAND_HIGH,
                 seconds_left_min: float = SECONDS_LEFT_MIN,
                 seconds_left_max: float = SECONDS_LEFT_MAX,
                 usd_size: float = USD_SIZE,
                 min_shares: int = MIN_SHARES,
                 db_path: str = DEFAULT_DB_PATH,
                 symbol_like: str = DEFAULT_SYMBOL_LIKE,
                 stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
                 shares=None):
        self.liq_min_usd = liq_min_usd
        self.liq_mega_usd = liq_mega_usd
        self.kicker_usd = kicker_usd
        self.kicker_mult = kicker_mult
        self.lookback_sec = lookback_sec
        self.dominance_ratio_min = dominance_ratio_min
        self.min_liq_count = min_liq_count
        self.band_low = band_low
        self.band_high = band_high
        self.seconds_left_min = seconds_left_min
        self.seconds_left_max = seconds_left_max
        self.usd_size = usd_size
        self.min_shares = min_shares
        self.db_path = db_path
        self.symbol_like = symbol_like
        self.stale_after_sec = stale_after_sec
        #: Override the USD-derived size. Tests and sweeps only. Bypasses the
        #: kicker, which is why `size_kicker_applied` is stamped separately.
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

    def stake_for(self, dominant_usd: float) -> float:
        """Base stake, with his 1.5x kicker on the larger tier."""
        if dominant_usd >= self.kicker_usd:
            return self.usd_size * self.kicker_mult
        return self.usd_size

    def intended_shares(self, price: float, dominant_usd: float) -> float:
        if self.shares is not None:
            return float(self.shares)
        return shares_for(self.stake_for(dominant_usd), price, self.min_shares)

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
            'liq_min_usd': self.liq_min_usd,
            'liq_mega_usd': self.liq_mega_usd,
            # Stated on EVERY row, skips included (convention 3).
            'claimed_win_rate_is_unverified_vendor_number': True,
            'vendor_win_rate': VENDOR_WIN_RATE,
            'vendor_breakeven_entry': VENDOR_BREAKEVEN_ENTRY,
            'vendor_win_rate_measured_on_resting_bids_not_taker': True,
            'entry_attempts_are_not_fills': True,
            'confidence': VENDOR_WIN_RATE,
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
            return decide('SKIP', 'balanced_liq_tape')
        if liq.dominant_usd < self.liq_min_usd:
            # RAN and found nothing. The only result-shaped skip in this block.
            return decide('SKIP', 'no_cascade')
        if liq.dominant_usd >= self.liq_mega_usd:
            # His rule. Kept for population hygiene against his own logs, even
            # though in our port the price bands are what actually separate the
            # two bots - see the module docstring.
            return decide('SKIP', 'mega_liq_belongs_to_cascade_chaser')
        ratio = liq.dominance_ratio
        if ratio is not None and ratio < self.dominance_ratio_min:
            return decide('SKIP', 'liq_not_dominant_enough')
        if liq.dominant_count < self.min_liq_count:
            return decide('SKIP', 'insufficient_liq_count')

        outcome_side = continuation_outcome(side)
        if outcome_side is None:
            # Unreachable; kept because a None outcome side would default to
            # 'bearish' in `decision_to_signal` rather than fail.
            return decide('SKIP', 'unmappable_liquidated_side')

        stake = self.stake_for(liq.dominant_usd)
        feats = {
            'outcome_side_planned': outcome_side,
            'stake_usd': round(stake, 4),
            'size_kicker_applied': bool(liq.dominant_usd >= self.kicker_usd),
            'size_kicker_mult': self.kicker_mult,
        }

        # 3. Timing, in seconds REMAINING.
        remaining = ctx.seconds_remaining
        feats['seconds_remaining'] = remaining
        if remaining is not None:
            if remaining < self.seconds_left_min:
                return decide('SKIP', 'too_close_to_resolution', **feats)
            if remaining > self.seconds_left_max:
                return decide('SKIP', 'window_not_open', **feats)

        if self.entered_this_window(ctx.window_ts):
            return decide('SKIP', 'already_entered_this_window', **feats)

        # 4. The book.
        if ctx.market is None:
            return decide('SKIP', 'no_market', **feats)
        book = ctx.book(outcome_side)
        if book is None:
            return decide('SKIP', 'no_orderbook', **feats)

        best_ask = book.best_ask
        feats['best_ask'] = best_ask
        if best_ask is None:
            return decide('SKIP', 'no_asks', **feats)

        shares = self.intended_shares(best_ask, liq.dominant_usd)
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
            # His sub-0.30 cell won 16.7%. Cheap is not cheerful.
            return decide('SKIP', 'effective_ask_below_band', **feats)

        feats['limit_price'] = self.band_high
        # On a binary the entry price IS the break-even win rate. Convention 8
        # is satisfied structurally: BINARY_STOP = 0.00 is strictly below any
        # entry `decision_to_signal` will accept.
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
