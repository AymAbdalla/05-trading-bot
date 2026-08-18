"""Streak Snapper: fade a stretched same-direction run at the next window open.

Ported from moondevonyt's `streak_snapper.py`. Thresholds preserved; the
MoonDev API tick feed, the wallet client, and the live order path are gone.

THESIS (his, not ours): after 4+ consecutive same-direction 5-minute BTC
windows whose cumulative move exceeds 3x the 1-hour mean absolute move, the
next window reverses about 54.3% of the time. Buy the reversal side at 52c or
less at window open and hold to resolution.

HIS NUMBERS, NOT EVIDENCE. 54.3% on 8,802 windows is from his logs on his
setup. Convention 3: unverified until our own harness says otherwise (D-268).

WHY THE PRICE CAP IS THE WHOLE STRATEGY. A binary bought at p needs a win rate
above p to make money. At 52c the claimed 54.3% leaves 2.3 points of margin; at
55c it leaves nothing; at 57c it is a losing strategy that wins more often than
it loses. So LIMIT_CAP is not a preference, it is the edge, and the effective
average entry after walking the book - not the best ask - is what has to clear
it.

TWO DELIBERATE DEVIATIONS FROM THE ORIGINAL, both tightening, both logged:

  1. He places a limit BUY at min(0.52, ask) even when the ask is ABOVE the cap
     (and even when the book is empty), then cancels after 60s if unfilled.
     That is a resting maker order dressed as an entry, and our paper adapter
     cannot simulate whether it fills. We SKIP instead, reason `ask_above_cap`.
     Reporting a maker order we cannot model as an entry would manufacture the
     exact fills the strategy lives or dies on.
  2. He prices the fill at his limit. We walk the book for the full intended
     size and gate on the EFFECTIVE average, which is what the docstring above
     always claimed and what D-268 means by "entry is the per-share premium".
     A 0.51 top-of-book quote for 6 shares is not a 0.51 entry for 19.

KILL CONDITION: trailing-50 resolved win rate below 50%, once 20 trades exist.
Two sigma below the claimed 54.3%. Also dies if our resolution-PnL harness
scores it under 30bps net edge on our own data (convention 5, D-268).
"""
import math

from strategies.polymarket.base import (Decision, Leg, MarketContext,
                                        PolymarketStrategy, cumulative_move,
                                        effective_ask_for, opposite,
                                        source_counts, streak, window_atr)

# Never False in this repo. moondevonyt ships this True->False for "LIVE FIRE";
# nothing here has live-trading authority.
PAPER_MODE = True

# moondevonyt's constants, unchanged. Each one is load-bearing per his README:
# without the stretch filter the edge falls to 50.7%, i.e. nothing.
STREAK_MIN = 4          # consecutive same-direction windows required
STRETCH_MULT = 3.0      # |cumulative move| must exceed this x the 1h ATR
ATR_WINDOWS = 12        # 12 x 5min = 1 hour
CUM_WINDOWS = 4         # cumulative move measured over the streak's last 4
LIMIT_CAP = 0.52        # hard price cap. Paying 55c+ burns the edge.
ENTRY_WINDOW_SEC = 20   # only enter in the first 20s of the new window
SIZE_USD = 10.0         # his flat validation-phase stake
MIN_SHARES = 5          # Polymarket minimum order size
MIN_WINDOWS = 16        # streak room + ATR room


def shares_for(size_usd: float, price: float, min_shares: int = MIN_SHARES) -> float:
    """His sizing: floor(stake / price), never below the exchange minimum."""
    if price <= 0:
        return float(min_shares)
    return float(max(min_shares, math.floor(size_usd / price)))


class StreakSnapper(PolymarketStrategy):
    """Fade 4+ stretched same-direction 5m windows at the next open."""

    strategy_name = 'PM_streak_snapper'
    paper_mode = PAPER_MODE

    def __init__(self, streak_min: int = STREAK_MIN,
                 stretch_mult: float = STRETCH_MULT,
                 atr_windows: int = ATR_WINDOWS,
                 cum_windows: int = CUM_WINDOWS,
                 limit_cap: float = LIMIT_CAP,
                 entry_window_sec: int = ENTRY_WINDOW_SEC,
                 size_usd: float = SIZE_USD,
                 min_shares: int = MIN_SHARES):
        self.streak_min = streak_min
        self.stretch_mult = stretch_mult
        self.atr_windows = atr_windows
        self.cum_windows = cum_windows
        self.limit_cap = limit_cap
        self.entry_window_sec = entry_window_sec
        self.size_usd = size_usd
        self.min_shares = min_shares

    def evaluate(self, ctx: MarketContext) -> Decision:
        slug = getattr(ctx.market, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if len(ctx.windows) < MIN_WINDOWS:
            return decide('SKIP', 'insufficient_window_history',
                          windows_available=len(ctx.windows),
                          windows_required=MIN_WINDOWS)

        # A window whose direction we could not read is not a window that went
        # the other way. Refusing to guess is the whole reason the source tag
        # exists (convention 11).
        if any(w.direction not in ('UP', 'DOWN') for w in ctx.windows):
            return decide('SKIP', 'unreadable_window_direction')

        # The stretch filter needs real USD magnitudes. Oracle windows carry a
        # DIRECTION and nothing else - `resolved_windows` encodes them as unit
        # moves so the streak count works, which means an ATR computed off them
        # is 1.0 and every stretch ratio comes out near the streak length.
        # That number would look like a signal and mean nothing. Without the
        # stretch filter the edge is 50.7%, so this is not a degraded mode, it
        # is a different strategy. Refuse rather than run on it.
        if not any(w.source == 'price' for w in ctx.windows):
            return decide('SKIP', 'no_magnitude_data',
                          window_sources=sorted({w.source
                                                 for w in ctx.windows}),
                          note=('stretch filter requires USD window moves; '
                                'oracle windows carry direction only'))

        streak_len, streak_dir = streak(ctx.windows)
        atr_usd = window_atr(ctx.windows, self.atr_windows)
        cum_move = cumulative_move(ctx.windows, self.cum_windows)
        stretch = abs(cum_move) / atr_usd if atr_usd > 0 else 0.0

        feats = {
            'streak_len': streak_len,
            'streak_dir': streak_dir,
            'cum_move_usd': round(cum_move, 2),
            'atr_usd': round(atr_usd, 2),
            'stretch_ratio': round(stretch, 3),
            'direction_sources': source_counts(ctx.windows),
        }

        if atr_usd <= 0:
            # Every window flat. Stretch is undefined, not infinite.
            return decide('SKIP', 'zero_atr_undefined_stretch', **feats)

        if streak_len < self.streak_min:
            return decide('SKIP', 'no_streak', **feats)

        if abs(cum_move) <= self.stretch_mult * atr_usd:
            # Plain streak counting without this filter is a 50.7% coin flip.
            return decide('SKIP', 'not_stretched', **feats)

        fade_side = opposite('Up' if streak_dir == 'UP' else 'Down')
        feats['fade_side'] = fade_side
        # Confidence is the claimed edge over the cap, scaled. Reported so the
        # scanner has a number; it is NOT a measured probability.
        feats['confidence'] = 0.543
        feats['confidence_is_unverified_vendor_number'] = True

        if (ctx.seconds_into_window is not None
                and ctx.seconds_into_window > self.entry_window_sec):
            # Late-window stink bids got cancelled 95% of the time in his logs.
            return decide('SKIP', 'late_in_window', **feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market', **feats)

        book = ctx.book(fade_side)
        if book is None:
            return decide('SKIP', 'no_orderbook', **feats)

        best_ask = book.best_ask
        feats['best_ask'] = best_ask
        if best_ask is None:
            return decide('SKIP', 'no_asks', **feats)

        if best_ask > self.limit_cap:
            # No chasing. The cap IS the edge. Deviation 1 in the docstring:
            # he would rest a 0.52 bid here, we cannot model that fill.
            return decide('SKIP', 'ask_above_cap', **feats)

        shares = shares_for(self.size_usd, self.limit_cap, self.min_shares)
        depth = book.ask_depth(self.limit_cap)
        feats['intended_shares'] = shares
        feats['ask_depth_at_cap'] = depth
        if depth < shares:
            return decide('SKIP', 'insufficient_ask_depth', **feats)

        effective = effective_ask_for(book, shares, self.limit_cap)
        feats['effective_ask'] = (None if effective is None
                                  else round(effective, 4))
        if effective is None:
            return decide('SKIP', 'unfillable_at_cap', **feats)
        if effective > self.limit_cap:
            # walk_book cannot return this given the same limit, but the gate
            # is the whole strategy and a silent regression here is invisible.
            return decide('SKIP', 'effective_ask_above_cap', **feats)

        feats['limit_price'] = self.limit_cap
        feats['breakeven_win_rate'] = round(effective, 4)

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=fade_side,
                                limit_price=self.limit_cap,
                                order_type='taker',
                                shares=shares,
                                expected_price=effective)],
                      **feats)
