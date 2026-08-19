"""Longshot Fade, Hold to Resolution: pay the spread once, at settlement.

Proposal 032 (`strategies/proposals/032-pm-longshot-fade-hold-to-resolution.md`).
Read it in full before touching this file - the module docstring restates the
mechanism, not the whole thesis.

## THE MECHANISM, IN ONE SENTENCE

With 1 to 5 minutes left in a 15-minute crypto Up/Down window and spot already
well outside the strike, the trailing token still quotes 3 to 7 cents when a
diffusion model says it should be cheaper - because the counterparty who should
sweep it has to lock 93-97 cents of capital for a few minutes to earn 3-7,
which is capital-inefficient under a 5-slot concurrency cap. This buys the
FAVORITE side in that band and holds to resolution, so the taker spread is
paid exactly ONCE (going in) instead of twice (in and back out), which is the
structural escape every diagnosed loser in this book was missing - see the
proposal's thesis field for the vault citations.

## WHY THIS IS A 15-MINUTE STRATEGY BUILT ON A 5-MINUTE ENGINE, AND HOW

`MarketContext` (`strategies/polymarket/base.py`) is built around the 5-minute
crypto window as primary: `ctx.windows` is a deque of 5-minute bars,
`ctx.window_ts` / `ctx.seconds_into_window` / `ctx.seconds_remaining` are all
5m-window-relative. The 15m companion market lives on the SAME context as
`ctx.market_15m` / `ctx.books_15m` / `ctx.book_15m(side)`, used before this file
only by `corridor_pair_live.py` and `corridor_collector.py`.

This file reuses `corridor_pair_live.CorridorPairLive`'s two static helpers
rather than re-deriving them (convention 23 - one definition, not two that
drift):

    parent_15m_ts(window_ts) = (window_ts // 900) * 900   floors a 5m window_ts
                                                            into its 15m parent
    open_at(ctx, ts)                                       matches a window's
                                                            open by TIMESTAMP,
                                                            never by index

A 15m window [T, T+900] contains exactly one 5m sub-window whose CLOSE
coincides with the 15m window's close: the FINAL THIRD, [T+600, T+900]. This
file only evaluates when `ctx.window_ts - parent_15m_ts(ctx.window_ts) == 600`
(`FINAL_THIRD_OFFSET_SEC`, imported from `corridor_pair_live`), which is also
exactly why the proposal's `t_rem` (60-300s remaining in the 900s window) needs
no new engine plumbing to compute: during the final third,

    t_rem = 900 - (600 + ctx.seconds_into_window) = 300 - ctx.seconds_into_window
          = WINDOW_SECONDS - ctx.seconds_into_window = ctx.seconds_remaining

so `ctx.seconds_remaining` (the 5m clock the engine already carries) IS `t_rem`
for as long as we are inside the final third, with no separate 900s clock to
build or maintain. Verified by direct substitution above, not asserted.

## THE SIGMA TAPE: IN-MEMORY, PER-ASSET, NO PERSISTENCE, AND A COLD RESTART
## RESETS IT TO EMPTY. READ THIS BEFORE TRUSTING AN EARLY `insufficient_window_
## history` COUNT.

`sigma_window_bps` needs the sample stdev of `(close-open)/open` in bps over
the last 20 COMPLETED 15-minute windows of the same asset. `ctx.windows` cannot
supply this: it carries only `WINDOW_LOOKBACK = 16` five-minute bars (about 80
minutes, `engine/polymarket/shadow_loop.py`), and 20 completed 15m windows need
roughly 5 hours of history. So this strategy builds its OWN tape, fed on EVERY
`evaluate()` call regardless of any gate below - the same "observe first, on
every cycle, including the ones that skip" discipline `fair_value_arb.py` uses
for its own price tape, and for the identical reason: a tape that only fills on
tradeable cycles has holes exactly where the signal would matter.

The tape is built by watching `ctx.spot` roll from one 900-second bucket
(`parent_15m_ts(ctx.window_ts)`) into the next: the first spot observed in a
bucket is that window's OPEN, the most recent spot observed before the bucket
changes is its CLOSE. This is an approximation from our own poll cadence, not
the venue's TWAP settlement price - the same caveat class as
`CorridorPairLive.open_at`'s "a BTC exchange bar open, not the Chainlink
settlement TWAP" - and it is named that way in `_observe_window_tape`.

**`self._tape_by_asset` is a plain in-memory `dict[str, deque]`. It is per
STRATEGY INSTANCE, and instances are per-ASSET**: `strategies/polymarket/
__init__.py`'s `build_strategies()` is called fresh once per asset by
`PolymarketShadowLoop.__init__`'s `_registry()` closure (verified by reading
`engine/polymarket/shadow_loop.py` directly - `_registry()` calls
`build_strategies()` again on every `for key in self.assets:` iteration, not
once). So a BTC instance of this strategy never sees an ETH or SOL context, and
keying the tape by asset here is defensive documentation of that fact rather
than a requirement it currently exercises - a future refactor that pools one
instance across assets would still be correct against this tape because of the
key, and would silently corrupt it without one.

**No `tape_db_path` / SQLite persistence, unlike `DipArb`'s proposal-031
`market_tape` table.** A shadow-loop restart resets this tape to completely
empty, and reaching 20 completed windows again from cold takes at least 5 hours
of continuous uptime. THIS IS WHY the proposal's own kill condition explicitly
allows "fewer than 60 positions resolve within 14 days -> NOT_TESTED, requeue"
rather than treating a quiet strategy as a failed one (convention 11). Adding
persistence was judged out of scope for this build (Raven's handoff Task 2 asks
only for registration and market-type declaration, not new engine plumbing);
if a future session judges the restart-reset cost worth paying, `DipArb`'s
`PriceTapeByToken` / `market_tape` table is the precedent to copy, not
reinvent.

## THE MODEL

    sigma_window_bps  sample stdev of the tape's (close-open)/open, in bps
    sigma_rem         sigma_window_bps * sqrt(t_rem / T),  T = 900
    d_bps             10000 * (spot - window_open) / window_open
    d_adj             max(0, abs(d_bps) - STRIKE_PROXY_NOISE_FLOOR_BPS)
    z                 d_adj / sigma_rem
    p_tail            1 - Phi(z)                          (NormalDist(0,1).cdf)
    favorite          the side spot is currently on ('Up' if spot >= open)
    fair_fav          1 - p_tail

`STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0` is subtracted from `abs(d_bps)` and it is
applied AGAINST us, exactly as the proposal specifies - the same shape as
`weather_arb.py`'s `strike_inside_proxy_noise_floor` / `MIN_ATTAINABLE_P_YES`
gates: a small, uncertain distance-from-strike measurement is discounted toward
"no edge" before the model is allowed an opinion, rather than trusted at face
value. `window_open` here is `open_15m`, the 15m window's own open matched by
timestamp via `CorridorPairLive.open_at` - a BTC exchange bar open, not a
settlement TWAP, restated from that file's own caveat.

## ENTRY GATES (proposal entry_exit_rules 5, ALL required)

    60 <= t_rem <= 300
    MIN_FAVORITE_ASK <= a_fav <= MAX_FAVORITE_ASK     (a_fav = favorite best ask)
    fair_fav - a_fav >= MIN_EDGE_VS_FAIR
    tail token's best bid >= TAIL_BID_MULTIPLE * p_tail
    ask depth at a_fav >= size

The tail-bid gate is not a liquidity check by itself, it is a DIVERGENCE
requirement: if the tail's own best bid has already fallen to (or below) about
`TAIL_BID_MULTIPLE * p_tail`, the market has already priced the tail close to
where this model would, which the proposal states plainly - "a market that
already agrees with the model is not a trade." Skipped under
`tail_bid_already_converged_with_model`, distinct from `no_tail_bid` (missing
book/bid entirely, convention 20: a missing input and a converged one are
different facts).

## SIZING AND THE CONCURRENCY SELF-LIMIT, AND ITS HONEST LIMIT

Fixed `NOTIONAL_USDC = 10.0` per entry, `shares = floor(NOTIONAL_USDC / a_fav)`,
refused below the exchange minimum (`MIN_SHARES = 5`, matching every other
strategy in this package).

**"Max 2 concurrent positions from this strategy" (proposal rule 6) is enforced
PER STRATEGY INSTANCE, and instances are per-asset** - see the tape section
above for why. There is no cross-asset channel a strategy instance can read (no
strategy in this package is handed the account's open-position list or another
asset's instance state), so the closest honest implementation is a per-instance
cap: `self._open`, a `dict[market_15m_slug -> resolve_at_ts]`, pruned on every
`evaluate()` call by comparing against a wall-clock estimate derived from
`ts15 + offset_into_15m_window + seconds_into_window`. A market already present
in `self._open` is refused under `already_entered_this_window` (this file never
ladders into one market twice); a market that would push the COUNT of unpruned
entries to `MAX_CONCURRENT_POSITIONS = 2` is refused under
`strategy_concurrency_cap_reached`.

**Recorded from the position stream, not the ENTER decision (2026-08-19
fix, same shape as `FairValueSettlementExit`'s own fix the same day).**
`evaluate()` only PROPOSES an entry; the shadow loop's adapter
(`max_concurrent_positions`) and `PolymarketRiskGate` both run downstream of
it and can still refuse the trade. The first cut of this file called
`_note_open` inside `evaluate()`, at ENTER-decision time, with no rollback on
a downstream refusal - so a burst of refused ENTERs alone could fill
`self._open` and trip `strategy_concurrency_cap_reached` against positions
that were never opened (self-starvation, the identical failure mode
`FairValueSettlementExit` measured live: 25 self-inflicted skips against 0
opened positions). `_note_open` is now called from `manage_exit`, the first
time this instance sees a given position's `market_slug` - i.e. only once
the adapter has actually filled it. `evaluate()` still PRUNES `self._open` on
every call (unchanged - pruning is time-based, not fill-based), it just no
longer ADDS to it.

**What this does NOT guarantee**: with three asset instances (BTC, ETH, SOL)
each independently capped at 2, the strategy FAMILY could hold up to 6
positions at once if all three fire simultaneously - not the flat 2 a naive
reading of "max 2 concurrent from this strategy" might suggest. The account-
level backstop is `PolymarketRiskGate` / `PolymarketPaperAdapter`'s
`max_concurrent_positions` (5, shared across the WHOLE book, every strategy and
asset), which still binds regardless of what this file tracks internally. This
per-instance cap exists to stop ONE asset's instance from quietly consuming the
account cap on its own during a long-lived book of favorites all reaching
resolution around the same time within one window; it is not, and cannot be
from where a strategy instance sits, a promise of exactly 2 system-wide.

## EXIT A, PRIMARY: HOLD TO RESOLUTION

Payoff 1.00 or 0.00. No converged-mid sale, no time-based sale, no
discretionary exit except Exit B below. Stop is `BINARY_STOP` (0.00) - a
losing binary share is worth exactly zero, satisfying convention 8's floor -
exactly like every other resolution-holding strategy in this package (see
`base.py`'s module docstring, "The Signal mapping").

## EXIT B, THE THESIS-INVALIDATION STOP, AND WHY THIS STRATEGY DECLARES
## `manages_exits = True` EVEN THOUGH ITS PRIMARY EXIT IS RESOLUTION

Proposal rule 8 (entry_exit_rules, not restated in Raven's compressed handoff -
read the proposal file directly): if spot crosses back through the strike
(`d_adj` reaches 0, i.e. `abs(d_bps) <= STRIKE_PROXY_NOISE_FLOOR_BPS` again)
AND the favorite's best bid is at or below `THESIS_INVALIDATION_BID_CEILING =
0.80` - strictly below the 0.93 entry floor, satisfying convention 8 - sell at
whatever the bid pays (`URGENT_SELL_LIMIT`, shared with `fair_value_arb.py` /
`dip_arb.py`, meaning "accept any bid; a stop that refuses a bad price is not a
stop"). This is a THESIS stop, not a volatility stop: it fires only when the
distance-from-strike signal the entry was bought against has itself gone to
zero, which the proposal says should happen on "a small minority of entries."

This is implemented via `manages_exits = True` plus `estimate()` /
`manage_exit()`, the same capability-dispatch protocol `DipArb` and the
`FairValueArb` family use (`engine/polymarket/shadow_loop.py`'s
`manage_exits()` - read its own extensive comment on why "manages its own
exits" and "publishes a fair value" are two different capabilities). `estimate
()` here does not publish a fair value at all; it republishes the live `ctx.
spot` through the SAME channel (`for_side` -> a float, named `fair_value` for
interface compatibility with the loop, exactly as `DipArb.TapeMeanEstimate`
documents doing for its own rolling mean) because `manage_exit` needs a LIVE
spot to recompute `d_adj` and the loop's exit path has no other way to hand one
over. `SpotSnapshotEstimate.usable` is True only when the context is, once
again, the SAME final-third 5m sub-window this strategy trades in - a position
opened there lives at most 300 seconds, entirely inside that one 5m window, so
the estimate stays usable for its whole life.

`position.features['open_15m']` (stamped by this file at entry, read back by
`manage_exit` off `position.features` - `PaperPosition.features` is exactly the
dict this file's `Decision.features` becomes, per `PaperAdapter.simulate_taker_
buy`) supplies the strike reference; a position whose features do not carry it
(should not happen, since every ENTER path here stamps it, but convention 11
says never assume) holds under `no_strike_reference_for_stop` rather than
guessing.

## THE >15% SELF-PAUSE CONDITION IS MEASURED, NOT ENFORCED. THIS IS A DELIBERATE
## CHOICE AND IT IS AN OPEN QUESTION FOR RAVEN.

The proposal: "if [Exit B] fires on more than 15% of entries, the sigma
estimator is wrong and the strategy is paused before the kill condition is
reached." This file counts `self._entries_count` and `self._stop_fires_count`
per instance and stamps `stop_fire_rate_this_instance` on every `manage_exit`
row, so the number is always computable from the log without a special query.
It does NOT auto-refuse further entries once the rate crosses 15%: that would
be a second, undocumented kill switch layered on top of `agents/
forge_shadow_eval.py`'s own kill-condition machinery, sharing no code and no
review with it, and a per-INSTANCE rate (see the concurrency section above)
would pause one asset's exposure while leaving the other two asset instances
fully live on the same broken sigma estimator - which is arguably worse than no
pause at all, since it would look like a safety mechanism had engaged. Whether
to build a real cross-instance pause, or to treat `stop_fire_rate_this_instance`
as a manual watch condition Raven checks against the shadow log, is left open
and stated as such in the handoff.

## KILL CONDITION (verbatim from the proposal, restated per convention 6)

Retire if net P&L per resolved position is below 0.00 USD over 60+ resolved
positions, or if realized win rate is below 90% on 60+ positions entered in the
0.93-0.97 band, both measured by `agents/forge_shadow_eval.py`. Fewer than 60
positions resolved within 14 days of wiring -> NOT_TESTED, requeue (convention
11 - a strategy that could not accumulate a sample is not a strategy that ran
and failed).

## STATUS: NOT_TESTED (D-268)

Like every strategy in this package, this has never been through the
resolution-PnL harness because that harness does not exist yet - see `base.py`'s
module docstring, "What these strategies are NOT". No backtest was run against
this file, per Aym's explicit instruction for this build.
"""
import math
from collections import deque
from dataclasses import dataclass, field
from statistics import NormalDist, stdev
from typing import Deque, Dict, List, Optional, Tuple

from strategies.polymarket.base import (MARKET_TYPE_CRYPTO_UPDOWN,
                                        WINDOW_SECONDS, Decision, Leg,
                                        MarketContext, PolymarketStrategy,
                                        effective_ask_for, opposite)
from strategies.polymarket.corridor_pair_live import (FINAL_THIRD_OFFSET_SEC,
                                                      WINDOW_15M_SEC,
                                                      CorridorPairLive)
from strategies.polymarket.fair_value_arb import ExitDecision, URGENT_SELL_LIMIT

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

# ---------------------------------------------------------------------------
# The model's own constants. Every one is OURS and every one is an assumption
# with an expiry date (convention 17) - none of them has been fitted or
# backtested. They are the proposal's own numbers, restated here verbatim.
# ---------------------------------------------------------------------------

#: T in the proposal's own notation: the 15-minute window length, seconds.
#: Reused from `corridor_pair_live` rather than redefined (convention 23).
T_WINDOW_SEC = float(WINDOW_15M_SEC)

#: Completed 15m windows required before `sigma_window_bps` is trusted at all.
#: Below this the answer is `insufficient_window_history` - CANNOT MEASURE,
#: never "no dip found" (convention 11).
MIN_WINDOWS_FOR_SIGMA = 20

#: Applied against `abs(d_bps)` before it becomes `d_adj`. Same shape as
#: `weather_arb.py`'s `strike_inside_proxy_noise_floor` treatment: a small
#: distance-from-strike is discounted toward "no signal" rather than trusted.
STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0

#: t_rem entry band, seconds remaining in the 900s window.
T_REM_MIN = 60.0
T_REM_MAX = 300.0

#: Favorite best-ask entry band.
MIN_FAVORITE_ASK = 0.93
MAX_FAVORITE_ASK = 0.97

#: Minimum modelled edge (fair_fav - a_fav) required to enter.
MIN_EDGE_VS_FAIR = 0.025

#: The tail token's best bid must be at least this multiple of our own p_tail,
#: or the market has already converged with the model and there is no trade
#: left (see the module docstring).
TAIL_BID_MULTIPLE = 3.0

#: Fixed per-entry notional, per the proposal ("fixed 10 USD notional").
NOTIONAL_USDC = 10.0

#: Exchange minimum order size, in shares. Matches every other strategy here.
MIN_SHARES = 5

#: Per-INSTANCE (per-asset) concurrency self-limit. See the module docstring's
#: "SIZING AND THE CONCURRENCY SELF-LIMIT" section for what this does and does
#: not guarantee across the three asset instances.
MAX_CONCURRENT_POSITIONS = 2

#: How many (slug -> resolve_at) entries `self._open` keeps as a defensive
#: bound, belt-and-braces on top of the prune-by-resolve-time logic above -
#: mirrors `StatusQuoCollector.RUNG_MEMORY_SIZE`'s reasoning. In practice
#: `self._open` should never hold more than `MAX_CONCURRENT_POSITIONS` entries
#: because pruning runs on every call; this bound only guards against a prune
#: bug silently leaking memory over a multi-day process lifetime.
OPEN_TRACKING_MAX = 50

#: Exit B: the thesis-invalidation stop. Strictly below `MIN_FAVORITE_ASK`
#: (convention 8 - the discretionary stop must sit below the entry band, not
#: merely below the fill).
THESIS_INVALIDATION_BID_CEILING = 0.80

#: Every SKIP reason `evaluate()` can produce. Listed so a reader can see at a
#: glance that no two causes share a string (convention 20), and so a test can
#: assert every one is reachable (convention 22).
SKIP_REASONS = (
    'missing_market_leg',
    'unknown_asset_for_tape',
    'not_final_third_of_15m',
    'no_spot',
    'no_15m_window_open',
    'invalid_15m_window_open',
    'no_clock',
    't_rem_outside_entry_window',
    'already_entered_this_window',
    'strategy_concurrency_cap_reached',
    'insufficient_window_history',
    'non_positive_sigma_rem',
    'no_orderbook',
    'no_favorite_ask',
    'favorite_ask_outside_entry_band',
    'edge_below_min',
    'no_tail_bid',
    'tail_bid_already_converged_with_model',
    'unsizable_at_notional',
    'insufficient_ask_depth',
    'unfillable_at_favorite_ask_cap',
)

_NORMAL = NormalDist(0.0, 1.0)


# ---------------------------------------------------------------------------
# The per-asset window tape. In-memory only - see the module docstring's tape
# section for the restart caveat and the per-instance-per-asset argument.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Observed15mWindow:
    """One approximated-complete 15m window: our own poll-cadence open/close.

    NOT the venue's TWAP settlement price. `ts` is the window's `parent_15m_ts`
    bucket start.
    """

    ts: int
    open: float
    close: float

    @property
    def move_bps(self) -> float:
        return 10000.0 * (self.close - self.open) / self.open


@dataclass
class _InProgressWindow:
    """The bucket currently being observed, before it rolls over and becomes
    an `Observed15mWindow`."""

    ts: int
    open: float
    last: float


class WindowTapeByAsset:
    """Per-asset rolling tape of completed 15m (open, close) pairs.

    Fed by `observe(asset, ts15, spot)` on EVERY `evaluate()` call, regardless
    of any gate - see the module docstring. Detects a completed window by
    watching `ts15` (the CURRENT 15m bucket) change between calls: the bucket
    that just ended is pushed onto that asset's deque as an
    `Observed15mWindow`, and a fresh in-progress bucket starts.
    """

    def __init__(self, maxlen: int = MIN_WINDOWS_FOR_SIGMA):
        self.maxlen = int(maxlen)
        self._tapes: Dict[str, Deque[Observed15mWindow]] = {}
        self._current: Dict[str, _InProgressWindow] = {}

    def observe(self, asset: str, ts15: int, spot: float) -> None:
        if not asset or spot is None:
            return
        try:
            spot_f = float(spot)
        except (TypeError, ValueError):
            return
        if not math.isfinite(spot_f) or spot_f <= 0.0:
            return

        cur = self._current.get(asset)
        if cur is None:
            self._current[asset] = _InProgressWindow(ts=int(ts15), open=spot_f,
                                                      last=spot_f)
            return
        if cur.ts == int(ts15):
            cur.last = spot_f
            return

        # The bucket rolled over. `cur` describes a window that just closed.
        if cur.open > 0.0:
            tape = self._tapes.setdefault(asset,
                                          deque(maxlen=self.maxlen))
            tape.append(Observed15mWindow(ts=cur.ts, open=cur.open,
                                          close=cur.last))
        self._current[asset] = _InProgressWindow(ts=int(ts15), open=spot_f,
                                                  last=spot_f)

    def count(self, asset: str) -> int:
        return len(self._tapes.get(asset, ()))

    def sigma_window_bps(self, asset: str,
                         min_windows: int = MIN_WINDOWS_FOR_SIGMA
                         ) -> Optional[float]:
        """Sample stdev (ddof=1) of the tape's move_bps, or None below
        `min_windows`. None means CANNOT MEASURE (convention 11)."""
        tape = self._tapes.get(asset)
        if tape is None or len(tape) < min_windows:
            return None
        moves = [w.move_bps for w in tape]
        try:
            return stdev(moves)
        except Exception:                                    # noqa: BLE001
            return None


# ---------------------------------------------------------------------------
# Exit B's estimate() contract - a live spot passthrough, not a fair value.
# See the module docstring for the full reasoning.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SpotSnapshotEstimate:
    """What `LongshotFadeHoldToResolution.estimate(ctx)` hands the shadow loop.

    Carries a live spot, republished through the `fair_value` channel `manage_
    exit` expects (see `DipArb.TapeMeanEstimate`'s docstring for the same
    pattern applied to a rolling mean instead of a spot).
    """

    usable: bool
    reason: str
    window_ts: Optional[int] = None
    spot: Optional[float] = None

    def for_side(self, outcome_side: str) -> Optional[float]:
        """The live spot, regardless of side - it is not a per-side quantity.

        Still validates the side is one this strategy trades ('up'/'down'),
        raising on anything else, matching `TapeMeanEstimate.for_side` /
        `FairValueEstimate.for_side`'s refuse-to-guess contract.
        """
        key = (outcome_side or '').strip().lower()
        if key not in ('up', 'down'):
            raise ValueError(
                'unknown outcome side {!r}; this strategy only ever buys '
                'Up or Down'.format(outcome_side))
        return self.spot


class LongshotFadeHoldToResolution(PolymarketStrategy):
    """Buy the crypto Up/Down favorite at 93-97c in the last 1-5 minutes of a
    15m window when a diffusion model says it is underpriced; hold to
    resolution. See the module docstring for the full ruling."""

    strategy_name = 'PM_longshot_fade_hold_to_resolution'
    paper_mode = PAPER_MODE

    #: Crypto-only. Needs spot, the 15m window's own open and 20 windows of
    #: this-asset history - none of which exist off the crypto path.
    supported_market_types = (MARKET_TYPE_CRYPTO_UPDOWN,)

    #: True for Exit B, the thesis-invalidation stop. See the module
    #: docstring's "Exit B" section for why the PRIMARY exit is still
    #: resolution despite this flag.
    manages_exits = True

    def __init__(self,
                 min_windows_for_sigma: int = MIN_WINDOWS_FOR_SIGMA,
                 strike_proxy_noise_floor_bps: float = STRIKE_PROXY_NOISE_FLOOR_BPS,
                 t_rem_min: float = T_REM_MIN,
                 t_rem_max: float = T_REM_MAX,
                 min_favorite_ask: float = MIN_FAVORITE_ASK,
                 max_favorite_ask: float = MAX_FAVORITE_ASK,
                 min_edge_vs_fair: float = MIN_EDGE_VS_FAIR,
                 tail_bid_multiple: float = TAIL_BID_MULTIPLE,
                 notional_usdc: float = NOTIONAL_USDC,
                 min_shares: int = MIN_SHARES,
                 max_concurrent_positions: int = MAX_CONCURRENT_POSITIONS,
                 thesis_invalidation_bid_ceiling: float = THESIS_INVALIDATION_BID_CEILING):
        self.min_windows_for_sigma = int(min_windows_for_sigma)
        self.strike_proxy_noise_floor_bps = float(strike_proxy_noise_floor_bps)
        self.t_rem_min = float(t_rem_min)
        self.t_rem_max = float(t_rem_max)
        self.min_favorite_ask = float(min_favorite_ask)
        self.max_favorite_ask = float(max_favorite_ask)
        self.min_edge_vs_fair = float(min_edge_vs_fair)
        self.tail_bid_multiple = float(tail_bid_multiple)
        self.notional_usdc = float(notional_usdc)
        self.min_shares = int(min_shares)
        self.max_concurrent_positions = int(max_concurrent_positions)
        self.thesis_invalidation_bid_ceiling = float(thesis_invalidation_bid_ceiling)

        #: Per-instance (per-asset) rolling window tape. See the module
        #: docstring's tape section.
        self.tape = WindowTapeByAsset(maxlen=max(min_windows_for_sigma, 1))

        #: market_15m slug -> believed resolve-at timestamp. Pruned on every
        #: `evaluate()` call. See "SIZING AND THE CONCURRENCY SELF-LIMIT".
        self._open: Dict[str, float] = {}
        self._open_order: List[str] = []

        #: Observational only (see the module docstring's self-pause section).
        #: Never auto-enforced.
        self._entries_count = 0
        self._stop_fires_count = 0

    # -- bookkeeping ----------------------------------------------------

    def _prune_open(self, now_estimate: float) -> None:
        stale = [slug for slug, resolve_at in self._open.items()
                if resolve_at <= now_estimate]
        for slug in stale:
            self._open.pop(slug, None)

    def _note_open(self, slug: str, resolve_at: float) -> None:
        self._open[slug] = resolve_at
        self._open_order.append(slug)
        # Belt-and-braces bound on `_open_order` alone, matching
        # `StatusQuoCollector._rungs_for`'s eviction shape. `_open` itself is
        # already bounded in practice by `_prune_open` running on every call
        # (it should never exceed `MAX_CONCURRENT_POSITIONS` entries); this
        # guards only against a prune bug silently leaking memory over a
        # multi-day process lifetime, so eviction here never touches `_open`
        # directly - popping an oldest slug that is still genuinely open would
        # make `_open` under-report the count this cap depends on.
        while len(self._open_order) > OPEN_TRACKING_MAX:
            self._open_order.pop(0)

    @staticmethod
    def _resolve_at_for(position) -> Optional[float]:
        """The believed resolve-at timestamp for a FILLED position, read back
        off it rather than recomputed from a fresh clock - see `manage_exit`'s
        note-open call. `position.features['parent_15m_ts']` is stamped by
        every `evaluate()` decision path (including ENTER, via `decide`'s own
        `setdefault`) and carried through unchanged into `PaperPosition.
        features` by `PaperAdapter.simulate_taker_buy`, so it is present on
        every real fill. Falls back to re-deriving it from `position.
        window_ts` (the 5m window_ts, always present) for the case that
        should not happen (convention 11: never assume) where the feature is
        missing.
        """
        features = getattr(position, 'features', None) or {}
        ts15 = features.get('parent_15m_ts')
        if ts15 is None:
            window_ts = getattr(position, 'window_ts', None)
            if window_ts is None:
                return None
            ts15 = CorridorPairLive.parent_15m_ts(window_ts)
        return float(ts15) + T_WINDOW_SEC

    @property
    def stop_fire_rate_this_instance(self) -> Optional[float]:
        """Exit B fires / total entries, this instance only. None until at
        least one entry exists. See the module docstring's self-pause
        section - this is OBSERVATIONAL, never auto-enforced."""
        if self._entries_count <= 0:
            return None
        return self._stop_fires_count / self._entries_count

    # -- entry ----------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        self.assert_supports(ctx)

        slug_5m = getattr(ctx.market, 'slug', None)
        slug_15 = getattr(ctx.market_15m, 'slug', None)
        ts15 = CorridorPairLive.parent_15m_ts(ctx.window_ts)
        offset = ctx.window_ts - ts15
        now_estimate = ts15 + offset + float(ctx.seconds_into_window or 0.0)
        self._prune_open(now_estimate)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            feats.setdefault('market_slug_15m', slug_15)
            feats.setdefault('parent_15m_ts', ts15)
            feats.setdefault('strike_proxy_noise_floor_bps',
                             self.strike_proxy_noise_floor_bps)
            feats.setdefault('exits_before_resolution', False)
            feats.setdefault('primary_exit', 'hold_to_resolution')
            feats.setdefault('has_thesis_invalidation_stop', True)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug_5m,
                            legs=legs or [], features=feats)

        if ctx.market is None or ctx.market_15m is None:
            return decide('SKIP', 'missing_market_leg',
                          has_5m=ctx.market is not None,
                          has_15m=ctx.market_15m is not None)

        # Imported lazily: `engine.polymarket`'s package `__init__` pulls in
        # `requests` via the client module, and a strategy file should stay
        # importable without a network stack - same discipline `base.
        # effective_ask_for` uses for its own engine import.
        from engine.polymarket.assets import asset_for_slug
        asset = asset_for_slug(slug_15) or asset_for_slug(slug_5m)
        if asset is None:
            return decide('SKIP', 'unknown_asset_for_tape')

        # Feed the tape FIRST, on every cycle including the ones that skip
        # below - see the module docstring's tape section.
        if ctx.spot is not None:
            self.tape.observe(asset, ts15, ctx.spot)

        if offset != FINAL_THIRD_OFFSET_SEC:
            return decide('SKIP', 'not_final_third_of_15m',
                          offset_sec=offset,
                          required_offset_sec=FINAL_THIRD_OFFSET_SEC,
                          asset=asset)
        if ctx.spot is None:
            return decide('SKIP', 'no_spot', asset=asset)

        open_15m = CorridorPairLive.open_at(ctx, ts15)
        if open_15m is None:
            return decide('SKIP', 'no_15m_window_open', asset=asset,
                          windows_available=len(ctx.windows))
        if open_15m <= 0:
            return decide('SKIP', 'invalid_15m_window_open', asset=asset,
                          open_15m=open_15m)

        # See the module docstring's derivation: t_rem == ctx.seconds_remaining
        # for exactly as long as offset == FINAL_THIRD_OFFSET_SEC, which is
        # already established above.
        t_rem = ctx.seconds_remaining
        if t_rem is None:
            return decide('SKIP', 'no_clock', asset=asset)
        if not (self.t_rem_min <= t_rem <= self.t_rem_max):
            return decide('SKIP', 't_rem_outside_entry_window', asset=asset,
                          t_rem=round(t_rem, 1), t_rem_min=self.t_rem_min,
                          t_rem_max=self.t_rem_max)

        if slug_15 in self._open:
            return decide('SKIP', 'already_entered_this_window', asset=asset,
                          t_rem=round(t_rem, 1))
        if len(self._open) >= self.max_concurrent_positions:
            return decide('SKIP', 'strategy_concurrency_cap_reached',
                          asset=asset, t_rem=round(t_rem, 1),
                          open_count=len(self._open),
                          max_concurrent_positions=self.max_concurrent_positions)

        sigma_window_bps = self.tape.sigma_window_bps(
            asset, self.min_windows_for_sigma)
        windows_stored = self.tape.count(asset)
        if sigma_window_bps is None:
            return decide('SKIP', 'insufficient_window_history', asset=asset,
                          windows_stored=windows_stored,
                          windows_required=self.min_windows_for_sigma,
                          t_rem=round(t_rem, 1))

        sigma_rem = sigma_window_bps * math.sqrt(t_rem / T_WINDOW_SEC)
        if not (sigma_rem > 0.0) or not math.isfinite(sigma_rem):
            return decide('SKIP', 'non_positive_sigma_rem', asset=asset,
                          sigma_window_bps=round(sigma_window_bps, 4),
                          sigma_rem=sigma_rem)

        d_bps = 10000.0 * (ctx.spot - open_15m) / open_15m
        d_adj = max(0.0, abs(d_bps) - self.strike_proxy_noise_floor_bps)
        z = d_adj / sigma_rem
        p_tail = 1.0 - _NORMAL.cdf(z)
        favorite = 'Up' if d_bps >= 0.0 else 'Down'
        tail_side = opposite(favorite)
        fair_fav = 1.0 - p_tail

        feats = {
            'asset': asset,
            'open_15m': round(open_15m, 2),
            'spot': round(ctx.spot, 4),
            'window_15m_open_source': 'btc_price_bar_open_not_settlement_twap',
            't_rem': round(t_rem, 1),
            'windows_stored': windows_stored,
            'sigma_window_bps': round(sigma_window_bps, 4),
            'sigma_rem_bps': round(sigma_rem, 4),
            'd_bps': round(d_bps, 4),
            'd_adj_bps': round(d_adj, 4),
            'z': round(z, 4),
            'p_tail': round(p_tail, 6),
            'favorite_side': favorite,
            'tail_side': tail_side,
            'fair_value_favorite': round(fair_fav, 6),
        }

        book_fav = ctx.book_15m(favorite)
        book_tail = ctx.book_15m(tail_side)
        if book_fav is None or book_tail is None:
            return decide('SKIP', 'no_orderbook',
                          has_favorite_book=book_fav is not None,
                          has_tail_book=book_tail is not None, **feats)

        a_fav = book_fav.best_ask
        feats['favorite_best_ask'] = a_fav
        if a_fav is None:
            return decide('SKIP', 'no_favorite_ask', **feats)
        if not (self.min_favorite_ask <= a_fav <= self.max_favorite_ask):
            return decide('SKIP', 'favorite_ask_outside_entry_band',
                          min_favorite_ask=self.min_favorite_ask,
                          max_favorite_ask=self.max_favorite_ask, **feats)

        edge = round(fair_fav - a_fav, 6)
        feats['edge_vs_fair'] = edge
        feats['min_edge_vs_fair'] = self.min_edge_vs_fair
        if edge < self.min_edge_vs_fair:
            return decide('SKIP', 'edge_below_min', **feats)

        tail_bid = book_tail.best_bid
        feats['tail_best_bid'] = tail_bid
        feats['tail_bid_floor'] = round(self.tail_bid_multiple * p_tail, 6)
        if tail_bid is None:
            return decide('SKIP', 'no_tail_bid', **feats)
        if tail_bid < self.tail_bid_multiple * p_tail:
            return decide('SKIP', 'tail_bid_already_converged_with_model',
                          **feats)

        shares = int(self.notional_usdc // a_fav)
        feats['shares'] = shares
        feats['notional_usdc_target'] = self.notional_usdc
        if shares < self.min_shares:
            return decide('SKIP', 'unsizable_at_notional', **feats)

        depth = book_fav.ask_depth(a_fav)
        feats['favorite_ask_depth'] = depth
        if depth < shares:
            return decide('SKIP', 'insufficient_ask_depth', **feats)

        effective = effective_ask_for(book_fav, shares, self.max_favorite_ask)
        feats['effective_favorite_ask'] = (None if effective is None
                                           else round(effective, 4))
        if effective is None:
            return decide('SKIP', 'unfillable_at_favorite_ask_cap', **feats)

        # No `_note_open` here. This is a PROPOSED entry - the adapter's own
        # `max_concurrent_positions` and `PolymarketRiskGate` both still run
        # downstream and can refuse it. Recording open state at this point,
        # before either gate runs, is the bug this file was fixed for
        # (2026-08-19, same shape as `FairValueSettlementExit`'s own fix) -
        # see `manage_exit` below. `self._open` is only ADDED TO once the
        # position has actually filled.
        resolve_at = float(ts15 + T_WINDOW_SEC)
        self._entries_count += 1
        feats['notional_usdc_actual'] = round(shares * effective, 4)
        feats['resolve_at_estimate'] = resolve_at
        feats['confidence'] = round(edge, 4)

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=favorite,
                                limit_price=self.max_favorite_ask,
                                order_type='taker',
                                market_slug=slug_15,
                                shares=shares,
                                expected_price=effective)],
                      **feats)

    # -- exit B: thesis-invalidation stop ---------------------------------

    def estimate(self, ctx: MarketContext) -> SpotSnapshotEstimate:
        """A live-spot passthrough for `manage_exit`. See the module
        docstring's "Exit B" section - this is NOT a fair value."""
        if ctx is None or ctx.market_15m is None or ctx.spot is None:
            return SpotSnapshotEstimate(
                usable=False, reason='no_spot_or_15m_market',
                window_ts=None if ctx is None else ctx.window_ts, spot=None)
        ts15 = CorridorPairLive.parent_15m_ts(ctx.window_ts)
        if ctx.window_ts - ts15 != FINAL_THIRD_OFFSET_SEC:
            return SpotSnapshotEstimate(
                usable=False, reason='not_final_third_of_15m',
                window_ts=ctx.window_ts, spot=ctx.spot)
        return SpotSnapshotEstimate(usable=True, reason='',
                                    window_ts=ctx.window_ts, spot=ctx.spot)

    def manage_exit(self, position, book, now: float,
                    fair_value: Optional[float] = None) -> ExitDecision:
        """Exit B only: thesis-invalidation stop. See the module docstring.

        `fair_value` here is repurposed as the LIVE SPOT (see `estimate()` and
        `SpotSnapshotEstimate`), never a probability - the parameter name is
        kept for interface compatibility with `PolymarketShadowLoop.
        manage_exits`, matching `DipArb.manage_exit`'s own precedent for
        repurposing this channel.
        """
        pid = getattr(position, 'position_id', '')
        entry = float(getattr(position, 'avg_price', 0.0) or 0.0)
        shares = float(getattr(position, 'shares', 0.0) or 0.0)
        pos_features = getattr(position, 'features', None) or {}
        open_15m = pos_features.get('open_15m')
        spot_now = fair_value
        best_bid = None if book is None else book.best_bid

        # The position stream is the source of truth for "this instance's
        # concurrency cap has one more slot filled" - see `_resolve_at_for`'s
        # docstring and the module docstring's concurrency section. Runs
        # before every other branch below (including the unreadable/no-book
        # early returns) because a position that filled still occupies a slot
        # regardless of what its exit decision turns out to be this cycle.
        # Idempotent: a position already in `self._open` is a no-op.
        slug = getattr(position, 'market_slug', None)
        if slug and slug not in self._open:
            resolve_at = self._resolve_at_for(position)
            if resolve_at is not None:
                self._note_open(slug, resolve_at)

        feats = {
            'entry_price': round(entry, 4),
            'shares': shares,
            'best_bid': best_bid,
            'window_open_15m': open_15m,
            'spot_now': spot_now,
            'spot_source': ('estimate_channel' if fair_value is not None
                            else 'unavailable'),
            'thesis_invalidation_bid_ceiling': self.thesis_invalidation_bid_ceiling,
            'strike_proxy_noise_floor_bps': self.strike_proxy_noise_floor_bps,
            'exits_before_resolution': False,
            'is_thesis_invalidation_stop': True,
            'stop_fire_rate_this_instance': self.stop_fire_rate_this_instance,
            'stop_fire_rate_enforced': False,
        }

        def hold(reason, **extra):
            return ExitDecision('HOLD', reason, position_id=pid,
                                features=dict(feats, **extra))

        def exit_now(reason, limit, **extra):
            self._stop_fires_count += 1
            return ExitDecision('EXIT', reason, position_id=pid,
                                limit_price=limit, shares=shares,
                                features=dict(feats, exit_limit_price=limit,
                                              stop_fire_rate_this_instance=
                                              self.stop_fire_rate_this_instance,
                                              **extra))

        if shares <= 0 or entry <= 0:
            return hold('unreadable_position')
        if book is None:
            return hold('no_orderbook')
        if best_bid is None:
            return hold('no_bid_liquidity', unsellable=True)
        try:
            open_15m_f = None if open_15m is None else float(open_15m)
        except (TypeError, ValueError):
            open_15m_f = None
        if open_15m_f is None or open_15m_f <= 0.0:
            return hold('no_strike_reference_for_stop')
        if spot_now is None:
            return hold('no_live_spot_for_stop')

        d_bps_now = 10000.0 * (float(spot_now) - open_15m_f) / open_15m_f
        d_adj_now = max(0.0, abs(d_bps_now) - self.strike_proxy_noise_floor_bps)
        feats['d_bps_now'] = round(d_bps_now, 4)
        feats['d_adj_now'] = round(d_adj_now, 4)

        if d_adj_now <= 0.0 and best_bid <= self.thesis_invalidation_bid_ceiling:
            return exit_now('thesis_invalidated_spot_crossed_strike',
                            URGENT_SELL_LIMIT)
        return hold('thesis_intact')
