"""Fair Value Mirror Fade: buy the side the model rejects.

D-326 (`docs/DECISIONS.md`), ratified under Aym's overnight authority from
Opus's edge analysis (`docs/handoffs/2026-08-19-opus-edge-analysis.md`,
ratified as the standing edge assessment at D-328). Read that handoff before
touching this file - the module docstring restates its numbers, not its
method.

## CORRECTION (D-326 amended, D-329, 2026-08-19 ~01:20 EDT): PAUSED - the
## fade thesis is UNPROVEN, not confirmed

Opus's planning-session follow-up
(`docs/handoffs/2026-08-19-opus-planning-session.md`) split the 345-trade
mirror measurement below by HOW each fill happened. **80% of the "+$281.74"
edge is MAKER fills, and a maker fill cannot be mirrored** -
`paper_adapter.py:1088 _through_and_touch` fills a resting BUY only after the
market has already moved through the limit, booking it AT the limit, so a
maker fill exists only in a state that already moved against us: adverse
selection by the simulator's own fill rule, not a market measurement.

| subset | n | mirror net | t |
|---|---|---|---|
| ALL settled (pooled, the original D-326 evidence) | 355 | +$281.74 | 3.46 |
| TAKER (executable) | 169 | +$51.15 | 1.52 |
| TAKER excl. ask <= 0.10 | 116 | +$40.24 | **1.19** |

**The executable, taker-only portion is t=1.19 on n=116 - below the t>=2.0
kill bar this file was always going to be judged against.** This strategy is
therefore PAUSED (`supported_market_types` on the class below, the D-322
sentinel mechanism) until ITS OWN taker-only signals clear that bar - not the
retrospective mirror of trades the PARENT strategies' gates selected, which
is a different population under different gates (see "THE FINDING THIS
STRATEGY TESTS" below, item 4, and the caveat already in "KILL CONDITION").
**Nothing below this correction is retracted**: execution = ~9% of the
fair_value family's loss, model = ~91%, is unchanged. Only "fading it is the
proven fix" is now "fading it is unproven" - convention 31, a claim is
verified against evidence, not repeated because it was written down once.

## THE FINDING THIS STRATEGY TESTS

The critic's "execution cost" diagnosis for the fair_value family is wrong.
Four independent measurements in the Opus analysis say the loss is
DIRECTIONAL, not the round trip:

  1. Execution: fees are 0.00 on all 1,716 closed positions, entries land at
     the ask the strategy saw (mean slippage +0.00157), and the book spread
     is tiny (median 0.0010). Realistic round trip is ~0.26c/share against a
     2.97c/share observed loss - execution explains at most **9%**.
  2. The model's OWN claimed edge is anti-predictive: `PM_fair_value_arb_hft`
     win rate falls from 45.7% (lowest-confidence quartile) to 20.2%
     (highest-confidence quartile). Pointing harder makes it worse.
  3. The mechanism: `side_fair_value` regressed on `best_ask` over 17,336
     signals has slope **0.30** against a well-calibrated forecaster's 1.0
     (0.196 on the signals actually acted on), and 87% of forecasts land in
     [0.4, 0.6] while market asks span [0.01, 0.99]. The model is pinned near
     0.5 and calling "cheap things are cheap" an edge.
  4. **The exact test.** 345 positions were held to settlement (exit at 0 or
     1 - no round trip, no stop, no target, zero fees in the arithmetic).
     Mirroring is exact negation for this subset: buying the complement at
     `1-e` and settling at `1-x` gives per-share PnL `e-x`, the precise
     negative of the observed `x-e`. Observed: n=345, net **-$294.35**
     (-6.07c/share). Mirrored: same 345 trades, net **+$281.74**
     (+5.81c/share). Edge -0.087 (observed) flips to +0.087 (mirrored),
     -3.73 SE - real, not noise.

**Execution = ~9% of the loss. Model = ~91%.** The complement of the model's
selection is the one real signal measured in this book so far. This strategy
trades that complement, live (shadow), on new signals - the 345-trade result
above is a RETROSPECTIVE mirror of trades already in the log, not a backtest
of this file, and this file has never run (see STATUS).

## THE MECHANISM

Reuses `FairValueArb.evaluate()` wholesale via `super()` - the SAME shape
`FairValueArbInverse` and `FairValueSettlementExit` use, for the identical
reason (convention 23: the model computation exists at exactly one site).
Every parent SKIP passes through with the parent's own reason untouched.
Only the ENTER branch is rewritten: instead of buying the side the model
selected (`intended`), this buys `opposite(intended)` (`flipped`), re-priced
and re-gated against ITS OWN book - never computed arithmetically from the
parent's price, because `ask(Up) + ask(Down) != 1.00` (the overround) any
more than it was for `FairValueArbInverse`.

**This is NOT `FairValueArbInverse` renamed.** Three deliberate differences,
each because the finding driving this file is different from the finding
driving that one:

  - **The edge gate is on the FLIPPED side's OWN mispricing, not book-derived
    slippage room.** Inverse's cap is `ceil_to_tick(best_ask + 1 tick)` with
    NO edge requirement of its own (its docstring calls this out as a real
    loosening it does not compensate for). This file instead requires
    `mirror_edge = fair(flipped) - ask(flipped) >= MIRROR_EDGE_THRESHOLD`
    before it will size a trade - the model's own compressed-but-nonzero
    signal on the flipped side, not a blank check. Because the fair value
    model is binary-consistent (`for_side(down) == 1 - for_side(up)`
    exactly, `engine/polymarket/fair_value.py:667-668`), `fair(flipped)` is
    read straight off the parent's own `fair_value_up`/`fair_value_down`
    feats rather than recomputed, so `mirror_edge` is exactly
    `(1 - side_fair_value) - no_ask` where the parent's own selection saw
    `side_fair_value - yes_ask` - the handoff's formula, not an
    approximation of it.
  - **Fixed size (5 shares), not notional-scaled.** This is a probe, sized
    to answer "does the sign flip", not to size positions to the model's
    confidence - the model's confidence is exactly the thing this file does
    not trust.
  - **No active exit at all.** See below.

## ENTRY GATES (all required, checked in this order)

  1. `flipped` must resolve (`opposite()` on an unrecognised label returns
     its input unchanged; refuse rather than emit an un-inverted trade under
     this class name - `mirror_side_unresolvable`).
  2. The flipped side must have a book and a best ask
     (`mirror_side_no_orderbook`, `mirror_side_no_ask`).
  3. `best_ask <= ENTRY_ASK_CAP` (0.60, matching 034's cap). The mirror buys
     the side the model considers relatively cheap, so this is usually
     satisfied by construction, not the binding gate
     (`mirror_entry_ask_above_cap`).
  4. `mirror_edge >= MIRROR_EDGE_THRESHOLD` (0.05) - see above
     (`mirror_edge_below_threshold`).
  5. `ask_depth_within_band(best_ask + depth_band) >= mirror_shares *
     DEPTH_MULTIPLE` (5 * 2 = 10 shares). Same depth-band shape the parent
     and every variant use, applied to the flipped book
     (`mirror_insufficient_book_depth`).
  6. `len(self._open) < MAX_CONCURRENT_POSITIONS` (2 - a probe, not a hog,
     same number 034 uses) - `strategy_concurrency_cap_reached`.
  7. The book must fill `mirror_shares` under a one-tick marketable limit
     (`mirror_unfillable_at_size`).

The parent's own `max_trades_per_window` (3, inherited unchanged) and
`min_entry_seconds_remaining` / clock gates still apply upstream, because
they run inside the `super().evaluate()` call this file never bypasses.

## EXIT: NONE. STOP 0.00, TARGET 1.00, BY DESIGN

`manage_exit` never returns EXIT. This is not an oversight and not the same
shape as 034's salvage floor - it is the literal arithmetic the handoff and
the Opus finding are built on: the 345-trade mirror measurement above has no
round trip, no stop and no target inside it, only entry-then-settlement, and
the whole point of this probe is to test THAT exact shape on new signals, not
a variant of it with a floor bolted on. Adding a salvage exit would answer a
different, easier question. `manage_exit` exists ONLY so `manages_exits =
True` can do concurrency bookkeeping - `_note_open`, on first sight of a
filled position in the adapter's position stream, exactly the shape
`FairValueSettlementExit` (034, commit `9d9a234`) and
`LongshotFadeHoldToResolution` (032, D-324) both use and for the identical
reason: `evaluate()` only PROPOSES an entry, the adapter's own
`max_concurrent_positions` and `PolymarketRiskGate` both run downstream and
can still refuse it, so recording open state before either gate runs is the
self-starvation bug both of those strategies were fixed for. `self._open` is
keyed on `(market_slug, attempt_number)`, the same pair key 034 uses, for the
same reason: the parent allows up to 3 entry ATTEMPTS per window against the
same slug and a slug-only key would undercount how many are genuinely open.

## KILL CONDITION (superseded by the D-326 correction above)

**Current bar (D-326 amended, Opus's planning session): the fade thesis is
dead unless taker-only settled mirror PnL reaches t >= 2.0 on n >= 250,
excluding entries below ask 0.10. Today: t = 1.19 on n = 116.** This must be
measured on THIS FILE's own trades once unpaused, not on the retrospective
mirror of the parent strategies' trades the number above was computed from -
see the correction. Scored by `agents/forge_shadow_eval.py`, CLOSED and
RESOLVED trades kept as two populations and never pooled, and every reported
number split by `fill_was_maker` (convention 32) so a maker-fill artifact
cannot repeat this file's own origin story.

The ORIGINAL kill condition this file shipped with (before the correction)
is kept below for the record, not as the live bar: kill if, over the first
60 resolved positions, realised settlement frequency (win rate) is below the
mean entry ask paid over those same 60. That condition assumed the pooled
+0.087-edge-per-share prior was a fair measurement of this file's own
gates; the correction found it was not (80% maker fills, uncounterfactual).

## STATUS: PAUSED (D-326 amended, D-329, 2026-08-19)

Never unpaused, never traded - `PM_fair_value_mirror_fade` has zero rows in
`db/trading.db` under any environment. Paused BEFORE its first shadow cycle,
per the correction above: `supported_market_types = ('smart_money',)`
(the D-322/D-323 sentinel - no cycle ever routes this type generically) on
the class below. Reverting is one line: restore
`supported_market_types = (MARKET_TYPE_CRYPTO_UPDOWN,)` (re-add the import)
once this file's own taker-only signals clear t >= 2.0 on n >= 250.
"""
from typing import Dict, Optional

from strategies.polymarket.base import (BINARY_STOP, Decision, Leg,
                                        MarketContext, WINDOW_SECONDS,
                                        effective_ask_for, opposite)
from strategies.polymarket.fair_value_arb import (PRICE_TICK, ExitDecision,
                                                  FairValueArb)
from strategies.polymarket.fair_value_arb_inverse import ceil_to_tick

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

#: The strategy whose signal this reads backwards. Stamped on every row so
#: the relationship is in the data, not only in the class name.
PARENT_STRATEGY_NAME = 'PM_fair_value_arb'

#: `(1 - side_fair_value) - no_ask`, the handoff's formula, gated at the same
#: 0.05 the parent's own tightened variant (034) uses. EXPIRY: no measurement
#: stands behind this exact number yet - it is the handoff's stated gate, not
#: a backtest result. The number that would move it is this file's own
#: trailing win-rate-vs-ask-paid curve once 60+ positions resolve.
MIRROR_EDGE_THRESHOLD = 0.05

#: Same cap 034 uses. The mirror buys the side the model considers cheap, so
#: this is usually satisfied - see the module docstring.
ENTRY_ASK_CAP = 0.60

#: Fixed size. This is a probe sized to answer "does the sign flip", not
#: sized to the model's own confidence - see the module docstring.
MIRROR_SHARES = 5

#: Ask depth within `self.depth_band` of the flipped best ask must be at
#: least this many multiples of `MIRROR_SHARES` (10 shares at the defaults).
DEPTH_MULTIPLE = 2.0

#: How far above the flipped side's best ask the entry limit is placed. One
#: tick - a slippage allowance, not an edge requirement, matching
#: `FairValueArbInverse.INVERSE_SLIPPAGE_ALLOWANCE`.
MIRROR_SLIPPAGE_ALLOWANCE = PRICE_TICK

#: Per-instance (per-asset) concurrency self-limit.
#:
#: D-362 R2, 2026-08-20: LIFTED to the 100_000 SENTINEL, same ruling and same
#: reasoning as `FairValueSettlementExit.MAX_CONCURRENT_POSITIONS` - a
#: per-strategy count cap underneath the global cap D-360 removed was the real
#: binding constraint and nobody had decided it should be. Global gate plus
#: capital caps are the only position brakes now.
MAX_CONCURRENT_POSITIONS = 100_000

#: Belt-and-braces bound on internal open-position tracking, matching
#: `FairValueSettlementExit.OPEN_TRACKING_MAX`'s reasoning.
OPEN_TRACKING_MAX = 50

#: New SKIP reasons this file can emit on top of everything it inherits from
#: `FairValueArb.evaluate()`. Listed so a reader sees at a glance that none
#: collide with a parent reason (convention 20).
NEW_SKIP_REASONS = ('mirror_side_unresolvable', 'mirror_side_no_orderbook',
                    'mirror_side_no_ask', 'mirror_entry_ask_above_cap',
                    'mirror_edge_below_threshold',
                    'mirror_insufficient_book_depth',
                    'strategy_concurrency_cap_reached',
                    'mirror_unfillable_at_size')


class FairValueMirrorFade(FairValueArb):
    """`FairValueArb`'s entry signal, side flipped, no active exit.

    Buys `opposite(intended)` wherever the parent would enter, gated on the
    flipped side's own mispricing (`mirror_edge >= 0.05`), its own ask cap
    (<= 0.60) and its own depth (>= 2x the fixed 5-share size). Holds every
    fill to settlement - no stop, no target, no salvage floor - because that
    is the exact arithmetic the D-326 ruling was built to test. See the
    module docstring for the Opus edge analysis this strategy exists to
    answer, AND the correction at the top of that docstring: the taker-only
    result (t=1.19, n=116) is below the t>=2.0 kill bar, so this strategy is
    PAUSED.

    Kill condition (superseded, see module docstring): dead unless
    taker-only settled mirror PnL reaches t >= 2.0 on n >= 250, excluding
    entries below ask 0.10.
    """

    strategy_name = 'PM_fair_value_mirror_fade'
    paper_mode = PAPER_MODE

    #: True for bookkeeping ONLY - see the module docstring's "EXIT: NONE"
    #: section. `manage_exit` never returns EXIT; it exists so the
    #: concurrency self-cap can see real fills.
    manages_exits = True

    #: PAUSED (D-326 amended, D-329, 2026-08-19, Raven ruling on Opus's
    #: planning-session correction, shadow only). The retrospective mirror
    #: measurement this file was built on (+$281.74, t=3.46, n=345) does not
    #: survive being split by fill provenance: 80% of it is MAKER fills,
    #: which cannot be mirrored (see the module docstring's correction). The
    #: executable taker-only portion is t=1.19 on n=116, below the t>=2.0
    #: kill bar. This is NOT a deletion - `build_strategies()` still returns
    #: this instance at its pinned index 25, `len(names) == 26` still holds,
    #: and reverting is one line: restore
    #: `supported_market_types = (MARKET_TYPE_CRYPTO_UPDOWN,)` (re-add the
    #: import) once this file's own taker-only signals clear the bar.
    supported_market_types = ('smart_money',)  # sentinel: no cycle ever routes this type generically (see comment above)

    def __init__(self, mirror_edge_threshold: float = MIRROR_EDGE_THRESHOLD,
                 entry_ask_cap: float = ENTRY_ASK_CAP,
                 mirror_shares: int = MIRROR_SHARES,
                 depth_multiple: float = DEPTH_MULTIPLE,
                 mirror_slippage_allowance: float = MIRROR_SLIPPAGE_ALLOWANCE,
                 max_concurrent_positions: int = MAX_CONCURRENT_POSITIONS,
                 **kwargs):
        # Every parent constant passes straight through unchanged
        # (edge_threshold, min_fair_value/max_fair_value, the depth band,
        # max_trades_per_window, the clock gates, model_uncertainty,
        # atr_windows). `min_profit`, `max_loss`, `model_stop_margin`,
        # `convergence_eps`, `time_stop_sec` and `window_close_exit_sec` are
        # still accepted (interface compatibility) but read by nothing here
        # - there is no active exit chain, see the module docstring.
        super().__init__(**kwargs)
        self.mirror_edge_threshold = float(mirror_edge_threshold)
        self.entry_ask_cap = float(entry_ask_cap)
        self.mirror_shares = int(mirror_shares)
        self.depth_multiple = float(depth_multiple)
        self.mirror_slippage_allowance = float(mirror_slippage_allowance)
        self.max_concurrent_positions = int(max_concurrent_positions)

        #: (market_slug, attempt_number) -> believed resolve-at timestamp.
        #: Same key shape as `FairValueSettlementExit._open` and for the
        #: same reason - see the module docstring's concurrency section.
        self._open: Dict[tuple, float] = {}
        self._open_order = []

    # -- bookkeeping ----------------------------------------------------

    def _prune_open(self, now_estimate: float) -> None:
        stale = [key for key, resolve_at in self._open.items()
                if resolve_at <= now_estimate]
        for key in stale:
            self._open.pop(key, None)

    def _note_open(self, key: tuple, resolve_at: float) -> None:
        self._open[key] = resolve_at
        self._open_order.append(key)
        while len(self._open_order) > OPEN_TRACKING_MAX:
            self._open_order.pop(0)

    @staticmethod
    def _open_key_for(position) -> tuple:
        """Same identity `evaluate()` keys `self._open` on, read back off a
        FILLED `PaperPosition`. See `FairValueSettlementExit._open_key_for`,
        which this mirrors exactly."""
        features = getattr(position, 'features', None) or {}
        attempt = features.get('attempt_number')
        if attempt is None:
            return (position.market_slug, getattr(position, 'position_id', None))
        return (position.market_slug, attempt)

    # -- entry ----------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        """The parent's decision, side flipped and re-priced. See the module
        docstring for why this cannot be `1 - parent_entry` and is not."""
        if ctx.window_ts is not None and ctx.seconds_into_window is not None:
            self._prune_open(float(ctx.window_ts)
                             + float(ctx.seconds_into_window))

        decision = super().evaluate(ctx)
        feats = decision.features
        feats['mirrored_from'] = PARENT_STRATEGY_NAME
        feats['primary_exit'] = 'hold_to_resolution'
        feats['exits_before_resolution'] = False
        feats['open_positions_this_instance'] = len(self._open)
        feats['max_concurrent_positions'] = self.max_concurrent_positions
        feats['flip_applied'] = False

        if not decision.is_entry or not decision.legs:
            # A parent SKIP is a mirror SKIP, with the parent's reason
            # intact - there is nothing to invert about "no orderbook" or
            # "too late in the window".
            return decision

        parent_leg = decision.legs[0]
        intended = parent_leg.outcome_side
        flipped = opposite(intended)

        feats['parent_intended_side'] = intended
        feats['parent_side_fair_value'] = feats.get('side_fair_value')
        feats['parent_best_ask'] = feats.get('best_ask')
        feats['parent_best_bid'] = feats.get('best_bid')
        feats['parent_raw_edge'] = feats.get('raw_edge')
        feats['parent_shares'] = feats.get('shares')

        def skip(reason, **extra):
            feats.update(extra)
            # The parent already burned an attempt deciding ENTER
            # (`FairValueArb._note_attempt`, inside `super().evaluate()`).
            feats['mirror_attempt_consumed_on_skip'] = True
            decision.action = 'SKIP'
            decision.reason = reason
            decision.legs = []
            return decision

        if not flipped or flipped == intended:
            return skip('mirror_side_unresolvable',
                        mirror_unresolved_label=intended)

        book = ctx.book(flipped)
        if book is None:
            return skip('mirror_side_no_orderbook')
        best_ask = book.best_ask
        if best_ask is None:
            return skip('mirror_side_no_ask')
        feats['best_ask'] = best_ask
        feats['mirror_best_ask'] = best_ask
        feats['best_bid'] = book.best_bid
        feats['mirror_best_bid'] = book.best_bid

        feats['entry_ask_cap'] = self.entry_ask_cap
        if best_ask > self.entry_ask_cap:
            return skip('mirror_entry_ask_above_cap')

        # `for_side()` is binary-consistent (`probability_down == 1 -
        # probability`), so this is exactly `(1 - side_fair_value)` read off
        # the parent's own feats - the handoff's formula, not an
        # approximation of it. See the module docstring.
        mirror_fair = (feats.get('fair_value_up') if flipped in ('Up', 'Yes')
                       else feats.get('fair_value_down'))
        feats['side_fair_value'] = mirror_fair
        feats['mirror_side_fair_value'] = mirror_fair
        mirror_edge = (None if mirror_fair is None
                       else round(mirror_fair - best_ask, 4))
        feats['mirror_edge'] = mirror_edge
        feats['mirror_edge_threshold'] = self.mirror_edge_threshold
        if mirror_edge is None or mirror_edge < self.mirror_edge_threshold:
            return skip('mirror_edge_below_threshold')

        min_depth = self.mirror_shares * self.depth_multiple
        depth_limit = round(best_ask + self.depth_band, 6)
        depth = book.ask_depth(depth_limit)
        feats['mirror_ask_depth_within_band'] = depth
        feats['mirror_min_depth_shares'] = min_depth
        if depth < min_depth:
            return skip('mirror_insufficient_book_depth')

        if len(self._open) >= self.max_concurrent_positions:
            return skip('strategy_concurrency_cap_reached',
                        open_count=len(self._open))

        cap = min(ceil_to_tick(best_ask + self.mirror_slippage_allowance), 1.0)
        feats['entry_cap'] = cap
        feats['mirror_entry_cap'] = cap

        effective = effective_ask_for(book, self.mirror_shares, cap)
        feats['effective_ask'] = (None if effective is None
                                  else round(effective, 4))
        feats['mirror_effective_ask'] = feats['effective_ask']
        if effective is None:
            # A partial fill is not an entry (convention 12).
            return skip('mirror_unfillable_at_size')
        if effective > cap:
            return skip('mirror_effective_ask_above_cap')

        feats['outcome_side'] = flipped
        feats['mirror_side_taken'] = flipped
        feats['flip_applied'] = True
        feats['shares'] = self.mirror_shares
        feats['target_shares'] = self.mirror_shares
        feats['shares_capped_by_notional'] = False
        feats['notional_usdc'] = round(self.mirror_shares * effective, 4)
        feats['realized_edge'] = (None if mirror_fair is None
                                  else round(mirror_fair - effective, 4))
        feats['realized_edge_bps'] = (
            round(feats['realized_edge'] / effective * 10_000, 1)
            if feats['realized_edge'] is not None and effective > 0 else None)
        # No `_note_open` here - see the module docstring's concurrency
        # section. `self._open` is only ADDED TO from `manage_exit`, once a
        # position has actually filled.

        decision.legs = [Leg(outcome_side=flipped,
                             limit_price=cap,
                             order_type='taker',
                             shares=self.mirror_shares,
                             expected_price=effective)]
        return decision

    # -- exit: bookkeeping only, never an exit ---------------------------

    def manage_exit(self, position, book, now: float,
                    fair_value: Optional[float] = None) -> ExitDecision:
        """Records the fill for the concurrency cap and holds, always. See
        the module docstring's "EXIT: NONE" section - this is not a
        truncated exit chain, it is the deliberate absence of one."""
        pid = getattr(position, 'position_id', '')
        window_ts = getattr(position, 'window_ts', None)
        if window_ts not in (None, ''):
            key = self._open_key_for(position)
            if key not in self._open:
                self._note_open(key, float(window_ts) + WINDOW_SECONDS)

        return ExitDecision(
            'HOLD', 'holding_to_resolution', position_id=pid,
            features={
                'primary_exit': 'hold_to_resolution',
                'exits_before_resolution': False,
                'stop_price': BINARY_STOP,
                'no_active_exit_by_design': True,
                'fair_value_observed_not_acted_on': (
                    None if fair_value is None else round(float(fair_value), 6)),
                'open_positions_this_instance': len(self._open),
            })
