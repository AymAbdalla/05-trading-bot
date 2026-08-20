"""Fair Value Settlement Exit: the same entry, one spread crossing instead of two.

Proposal 034 (`strategies/proposals/034-pm-fair-value-settlement-exit-experiment.md`).
Read it in full before touching this file. This is an EXPERIMENT, not a fix: it
exists to decide between two contradictory claims already in the record, not to
repair either one in advance.

## THE BLOCKING PRECONDITION (Task 0) - RESOLVED, NOT BY THIS SESSION

The proposal will not be wired until it is known which field the exit path
reads for its stop: `positions.stop_px` (the DB column, all 0.00 on the 67
`stop_too_tight` fair-value rows the critic flagged) or something else.

Finding, with line numbers: the live exit path
(`FairValueArb.manage_exit`, `strategies/polymarket/fair_value_arb.py:811-813`)
computes the discretionary stop FRESH on every check -
`self.stop_price_for(entry, outcome_side)`, which calls
`strategies.polymarket.base.tiered_stop_price` - and compares it against the
book's live best bid. It never reads `positions.stop_px` at all; that column
is populated separately, for RECORD-KEEPING only, by
`ShadowStore.record_entry` (`engine/polymarket/shadow_loop.py:844-868`) via
`_entry_stop_px` (`engine/polymarket/shadow_loop.py:2270-2300`), which calls
the exact same `stop_price_for` method at entry time and writes the result.

So the two facts are not in tension; they describe two different eras of the
SAME column. `record_entry`'s own docstring (`shadow_loop.py:809-824`) states
plainly that the column "used to be hardcoded to 0.00 for every Polymarket
row" - and commit `ea30111` (2026-08-18 16:22 EDT, "D-312 to D-315: wire the
general binary market spaces, register proposal 024") is where that changed:
`_entry_stop_px` and the tiered stop in `fair_value_arb.py` both landed in that
commit, already merged to `main` before this proposal was written. The 67
flagged trades predate it - their rows were written under the OLD hardcoded-
0.00 path, while their LIVE exits (at the time) used a flat `entry - 0.03`
stop (see `fair_value_arb.py:144-157`), not the tiered rule that exists now
and not the value the DB column claims. `stop_px = 0.00` on those specific 67
rows is a bookkeeping gap that has already been closed for every row written
since; it is not evidence that the exit path ran with no stop at all.

**No new fix was required here** - commit `ea30111` already did it, before
this proposal existed, as an uncredited byproduct of unrelated wiring work.
That commit's own message does not call the stop_px fix out by name (D-312
through D-315 are about market-space routing), so nothing in `docs/DECISIONS.md`
formally closes this specific contradiction. This session adds D-320 to do
that - see `docs/DECISIONS.md` - so a future reader does not have to re-derive
the same git-log trail to answer the question this precondition asks.

Consequence for this file: `stop_too_tight` on the 67 pre-`ea30111` rows is
UNRESOLVED as a verdict on the tiered stop itself - those rows never ran
against it. The critic's `stop_too_tight` claim and the deterministic
classifier's `model_miscalibrated` claim remain both live, which is exactly
the state this experiment (below) is built to break.

## THE MECHANISM

Unchanged parent `PM_fair_value_arb` entry signal, tightened selection, and a
different exit: hold to resolution instead of selling on convergence, so the
spread is paid once (entering) instead of twice (entering and exiting). See
the parent's own module docstring for the round-trip arithmetic this is
built to test.

## WHAT IS INHERITED FROM `FairValueArb`, UNCHANGED

The fair value model (`estimate()`), the price tape (`observe()`), the window
clock and attempt-per-window bookkeeping, the depth gate, the book-walked
sizing rule (`floor(MAX_NOTIONAL_USDC / cap)`, capped at `TARGET_SHARES`), and
`evaluate()`'s entry-side selection (best of Up/Down by raw edge). This file
calls `FairValueArb.evaluate()` via `super()` and only POST-FILTERS its ENTER
decisions - the same shape `FairValueArbInverse.evaluate()` uses, and for the
same reason: reusing the model wholesale is the only way an experiment about
the EXIT can avoid also becoming a silent experiment about the entry
(convention 23 in reverse - a fix duplicated at a second site is not a fix,
and neither is a MODEL duplicated at a second site).

Explicitly NOT run: the `hft`, `wide`, `patient` or `inverse` variants.
Inverting a loser does not flip its sign, and five variants of the same model
already produced 615 trades of one mistake (see `fair_value_arb_inverse.py`'s
own measured numbers). One variant, one exit change, one question.

## WHAT IS NEW: THE TIGHTENED ENTRY GATE

Two gates layered on top of the parent's own (which stay live and unchanged -
`edge_threshold`, `min_fair_value`/`max_fair_value`, the depth gate):

  1. `edge_threshold` raised from the parent's 0.04 to `EDGE_THRESHOLD = 0.05`
     - the SAME gate, a tighter number. Passed through `super().__init__()`,
       not reimplemented.
  2. `entry_ask_cap` - NEW. `ENTRY_ASK_CAP = 0.60`. The parent has no cap on
     the entry ask itself (only on fair value's tradeable band, 0.10-0.90).
     Checked AFTER the parent selects its best-edge side, against that side's
     `best_ask` (already stamped in `feats` by the parent) - not against the
     other side, which this file never re-evaluates. `settlement_entry_ask_
     above_cap` when it fails.

This is a TIGHTENING, not a lowering (matches `fair_value_arb.py`'s own
`EDGE_THRESHOLD` expiry note) and a CONFOUND, stated rather than hidden: this
file changes the exit AND tightens the entry in the same experiment, so a
positive result cannot be attributed to one alone. See the proposal's "Why
this might fail" section for the reasoning; it is not repeated here.

## WHAT IS NEW: THE PER-INSTANCE CONCURRENCY SELF-CAP

`MAX_CONCURRENT_POSITIONS = 2`, tracked in `self._open`
(`(market_slug, attempt_number) -> resolve_at estimate`), pruned every
`evaluate()` call by comparing against `ctx.window_ts +
ctx.seconds_into_window` - the SAME shape `LongshotFadeHoldToResolution.
_open` uses, for the identical reason: this strategy did not carry
cross-cycle open-position state before (it used to sell within ~60-120s,
`evaluate()` never needed to know what was still open), and now that it
holds to a 5-minute resolution, it does.

**Recorded from the position stream, not the ENTER decision (2026-08-19 fix).**
`evaluate()` only PROPOSES an entry; the shadow loop's adapter
(`max_concurrent_positions`) and `PolymarketRiskGate`
(`max_concurrent_positions`, `max_positions_per_market_side`) both run
downstream of it and can still refuse the trade. The first cut of this file
called `_note_open` inside `evaluate()`, at ENTER-decision time, with no
rollback on a downstream refusal - so a burst of refused ENTERs alone could
fill `self._open` and trip `strategy_concurrency_cap_reached` against
positions that were never opened (self-starvation, confirmed live: 25
self-inflicted `strategy_concurrency_cap_reached` skips against 0 opened
positions in the first 45 minutes after the 2026-08-18 22:50 shadow-loop
restart). `_note_open` is now called from `manage_exit`, the first time this
instance sees a given position in `self.adapter.open_positions()` - i.e.
only once the adapter has actually filled it. `evaluate()` still PRUNES
`self._open` on every call (unchanged - pruning is time-based, not
fill-based, and does not need to see the position stream), it just no
longer ADDS to it.

**Keyed on the (slug, attempt) PAIR, not the slug alone.** The parent's own
`max_trades_per_window` allows up to 3 entry ATTEMPTS inside one 5-minute
window, all against the same market slug - a slug-only key would let a
second attempt inside the same window silently overwrite the first's
tracking entry and undercount how many are genuinely open.

**The same honest limit applies here as there.** `build_strategies()` is
called fresh per asset (`engine/polymarket/shadow_loop.py`'s `_registry()`
closure, called once per key in `self.assets`), so this cap binds PER ASSET
INSTANCE, not system-wide - three assets each independently capped at 2 could
hold up to 6 positions at once from this strategy family alone. The account-
level backstop is still `PolymarketRiskGate`'s `max_concurrent_positions`
(5, shared across the whole book), which binds regardless of what this file
tracks. Reuses the existing `strategy_concurrency_cap_reached` classification
(`agents/forge_shadow_eval.py`) rather than inventing a new name for the same
fact.

## EXIT A, PRIMARY: HOLD TO RESOLUTION

No `converged`, no `model_stop`, no `time_stop`, no `window_close`. All four
are the parent's exit-before-resolution machinery and this strategy's entire
point is not running it. `manage_exit` is a full rewrite (does not call
`super().manage_exit()`), exactly as `LongshotFadeHoldToResolution.manage_exit`
is a full rewrite rather than a filtered pass-through - the parent's SIX-rule
chain has nothing in it this strategy wants to keep except the salvage floor's
shape.

`estimate()` is still inherited (this strategy still publishes a real fair
value, unlike `FairValueArbInverse`, which had to withhold it). Nothing below
reads it as a gate; it is passed through into `manage_exit`'s `fair_value`
parameter and stamped on every exit row as `fair_value_observed_not_acted_on`
so "we saw it and chose not to act" is on the record rather than absent from
it (convention 20 - a value that arrives and is deliberately unused is a
different fact from a value that never arrived).

## EXIT B: THE SALVAGE FLOOR, AND ITS DEGENERATE CASE

Proposal rule 4: if the held token's best bid is at or below
`SALVAGE_FLOOR = 0.10`, sell at whatever the bid pays
(`URGENT_SELL_LIMIT = 0.0` - "accept any price"; the codebase-wide meaning of
that constant, matching `fair_value_arb.py` / `dip_arb.py` /
`longshot_fade_hold_to_resolution.py`'s own use of it). This is a salvage
floor, not a volatility stop: a losing binary share settles at 0.00, so
taking 0.10 whenever the market still offers it is strictly better than
waiting for resolution to zero it out - it directly answers the
`stop_too_tight` failure mode instead of repeating it with a different
number.

**The degenerate case, handled the same way `tiered_stop_price` handles its
own (`strategies/polymarket/base.py:611-619`), because it is the identical
shape.** `min_fair_value = 0.10` is inherited unchanged from the parent and
`edge_threshold` is 0.05, so an entry can legally land as low as roughly 0.05
- BELOW the 0.10 salvage floor. A flat 0.10 floor on an entry already priced
under it would fire on the very first bid observed, which is not a stop
(convention 8: a stop must sit strictly below entry). `salvage_stop_price()`
below collapses onto the structural 0.00 floor in that band and flags it
(`salvage_floor_is_structural_floor=True`) rather than silently letting a
0.10 "stop" sit above a 0.06 entry. This is a reading of the proposal's flat
number, not a deviation from it: the proposal never priced its own floor
against `min_fair_value`, and refusing to invent an unstated exception was
judged safer than reproducing the exact defect this exit exists to fix.

## KILL CONDITION - SUPERSEDED BY D-327, 2026-08-19

The proposal's original condition (below, kept for the record) never got a
chance to fire: 1,131 signals, ZERO acted, because `max_trades_this_window`
alone ate 643 of them (57%) - the throttle, not the edge, was starving this
strategy before its exit model could be measured at all. Opus's edge
analysis (`docs/handoffs/2026-08-19-opus-edge-analysis.md`, Task 1.5) found
the original premise backwards in the same session: 034 exists to "halve the
round trip" by holding to settlement, but the round trip it would halve is
~0.26c/share, while the book's own hold-to-settlement population already
measures **3.4x worse per share than stopping out** inside 034's own entry
band (-8.80c/share settled vs -2.59c/share intraday-exited, n=203 vs n=953).
The stop this file removes is not the disease; it is the thing currently
limiting the damage from a model measured elsewhere in the same analysis to
be anti-predictive (slope 0.30 against a calibrated forecaster's 1.0).

**D-327 (RATIFIED, 2026-08-19): 034 is re-gated as a MEASUREMENT INSTRUMENT,
not a profit strategy.** It is the only strategy in the registry that holds
the fair_value selector to settlement with the round trip fully instrumented
(`model_edge_at_entry` and `entry_ask` stamped on every signal), and that
calibration data - realised settlement frequency against price paid, for
this specific selector - does not exist anywhere else in the system. Its
value is the measurement, whatever the sign turns out to be. It is not
deleted, because deleting the only instrumented settlement path would throw
the measurement away along with the (already refuted) profit premise.

**New kill condition, with a number and a named harness (convention 6): 034
is dead if realised settlement frequency over its first 60 entries is below
0.30, against a mean entry ask of 0.33** - the break-even shape a
hold-to-settlement strategy always has (a share pays 0 or 1, so break-even
win rate equals the price paid), evaluated against Opus's own measured prior
for this exact gate (`edge >= 0.05`, `ask <= 0.60`): over the 10,630
fair_value-family signals that would pass it, mean entry ask 0.3300, median
0.3400. Scored by `agents/forge_shadow_eval.py`, CLOSED and RESOLVED
positions kept as two populations and never pooled. Fewer than 60 entries
resolved within 14 days -> NOT_TESTED, not failed (convention 11), requeue.

Opus's own expectation, stated so a later reader can check whether it held:
"roughly 25% realised against a 33% requirement, about -9 cents per share...
If it comes in near 33% I am wrong and the settlement thesis survives."

### The proposal's original condition (SUPERSEDED, kept for the record)

Retire if net P&L per resolved position is below 0.00 USD over 200 or more
resolved positions (`agents/forge_shadow_eval.py`), and in that case execute
the 9 standing kill recommendations for the fair-value family on the strength
of it. Fewer than 200 positions resolved within 14 days of wiring ->
NOT_TESTED, not failed (convention 11), requeue. This never bound - see
above - and D-327's condition is the one that applies.

## STATUS: NOT_TESTED (D-268 shape), THROTTLE RELAXED UNDER D-327

No backtest was run against this file, per Aym's explicit instruction for
this build (shadow-only, never live). Zero rows exist under
`PM_fair_value_settlement_exit` before this session wires it in.
`max_trades_per_window` raised from the inherited 3 to `MAX_TRADES_PER_WINDOW
= 12` (D-327) so it can actually accumulate the 60 entries its own kill
condition needs - see that constant's docstring for why 12 and why the real
safety bound stays `MAX_CONCURRENT_POSITIONS` (2), unchanged.
"""
from typing import Dict, Optional

from strategies.polymarket.base import (BINARY_STOP, MARKET_TYPE_CRYPTO_UPDOWN,
                                        WINDOW_SECONDS, Decision, MarketContext)
from strategies.polymarket.fair_value_arb import (ExitDecision, FairValueArb,
                                                  URGENT_SELL_LIMIT)

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

#: Tightened from the parent's 0.04 (proposal rule 2: "a tighter gate, not a
#: lower one"). Passed straight through `super().__init__()`.
EDGE_THRESHOLD = 0.05

#: NEW. The parent has no cap on the entry ask itself. Checked post-hoc
#: against the side the parent already selected.
ENTRY_ASK_CAP = 0.60

#: Exit B's flat salvage floor. See the module docstring's degenerate-case
#: section for what happens when an entry is priced below this.
SALVAGE_FLOOR = 0.10

#: Per-instance (per-asset) concurrency self-limit. See the module docstring's
#: honest-limit section for what this does and does not guarantee
#: system-wide.
#:
#: D-362 R2, 2026-08-20: LIFTED to the 100_000 SENTINEL. Aym: "lift all
#: position caps on the strategies, only keep global position caps." A
#: per-strategy count cap of 2 sat UNDERNEATH the global cap D-360 had already
#: removed, so it - not the global gate - was the binding constraint, and it
#: was binding without anyone having decided it should. The global gate
#: (`PolymarketRiskGate.max_concurrent_positions`) and the capital caps in
#: `engine/risk/constraints.py` are the only position brakes now. Restore a
#: small integer here only under a new D-number.
MAX_CONCURRENT_POSITIONS = 100_000

#: D-327, 2026-08-19. Raised from the parent's inherited default (3) so 034
#: can actually accumulate the 60 entries its own kill condition needs -
#: 643 of 1,131 signals (57%) were eaten by this throttle alone before any
#: exit model got measured. 12 is chosen, not derived: it is well above what
#: `MAX_CONCURRENT_POSITIONS` (2) will ever let bind in practice, because a
#: hold-to-resolution strategy never frees a slot early the way the
#: exit-before-resolution parent does - so the concurrency self-cap, not the
#: per-window attempt count, is meant to be the real safety bound here.
#: EXPIRY: no measurement stands behind 12 specifically; the number that
#: would move it is how many of the first 60 entries land inside one window
#: once this runs live.
MAX_TRADES_PER_WINDOW = 12

#: Belt-and-braces bound on internal open-position tracking, matching
#: `LongshotFadeHoldToResolution.OPEN_TRACKING_MAX`'s reasoning - `self._open`
#: should never exceed `MAX_CONCURRENT_POSITIONS` entries because pruning runs
#: every call; this only guards a prune bug from leaking memory over a
#: multi-day process lifetime.
OPEN_TRACKING_MAX = 50

#: New SKIP reason this file can emit on top of everything it inherits from
#: `FairValueArb.evaluate()`. Listed so a reader sees at a glance that it does
#: not collide with any parent reason (convention 20).
NEW_SKIP_REASONS = ('settlement_entry_ask_above_cap',)


def salvage_stop_price(entry_px: float, floor: float = SALVAGE_FLOOR) -> float:
    """The salvage-floor stop PRICE for a long bought at `entry_px`.

    `floor` when the entry is above it; the structural floor (`BINARY_STOP`,
    0.00) when it is not, because there is no price strictly between 0.00 and
    an entry already at or below `floor` that the flat rule could mean. See
    the module docstring's "degenerate case" section - this mirrors
    `strategies.polymarket.base.tiered_stop_price`'s own handling of the
    identical shape.

    Convention 8 is enforced, not assumed: the result is asserted strictly
    below `entry_px`.
    """
    entry = float(entry_px)
    if not entry > BINARY_STOP:
        raise ValueError(
            'entry_px must be strictly above {:.2f}, got {!r}'
            .format(BINARY_STOP, entry_px))
    stop = float(floor) if float(floor) < entry else BINARY_STOP
    assert stop < entry, (
        'stop {!r} is not strictly below entry {!r}'.format(stop, entry))
    return stop


class FairValueSettlementExit(FairValueArb):
    """`FairValueArb`'s entry model, tightened, held to resolution instead of
    sold on convergence, with a flat salvage-floor stop replacing the
    parent's six-rule exit chain.

    Re-gated as a MEASUREMENT INSTRUMENT under D-327 (2026-08-19), not a
    profit strategy - see the module docstring's superseded-kill-condition
    section. Kill condition: realised settlement frequency over the first 60
    entries below 0.30, against a mean entry ask of 0.33, scored by
    `agents/forge_shadow_eval.py`. Fewer than 60 entries resolved within 14
    days -> NOT_TESTED, requeue (convention 11). See the module docstring for
    the full ruling, including the already-resolved blocking precondition
    this proposal was gated on.
    """

    strategy_name = 'PM_fair_value_settlement_exit'
    paper_mode = PAPER_MODE

    #: Still True: Exit B (the salvage floor) needs it. Exit A (hold to
    #: resolution) needs nothing from the shadow loop at all - a position this
    #: file never sells simply resolves like every other strategy's.
    manages_exits = True

    #: Crypto Up/Down only (Task 2 of the handoff). The parent is declared
    #: crypto plus weather plus every general binary market (D-316); this
    #: experiment is explicitly scoped to "unchanged parent model on crypto
    #: Up/Down" and nothing else (proposal rule 1), so the declaration is
    #: narrowed rather than inherited.
    supported_market_types = (MARKET_TYPE_CRYPTO_UPDOWN,)

    def __init__(self, edge_threshold: float = EDGE_THRESHOLD,
                 entry_ask_cap: float = ENTRY_ASK_CAP,
                 salvage_floor: float = SALVAGE_FLOOR,
                 max_concurrent_positions: int = MAX_CONCURRENT_POSITIONS,
                 max_trades_per_window: int = MAX_TRADES_PER_WINDOW,
                 **kwargs):
        # Every other parent constant passes straight through unchanged
        # (target_shares, max_notional_usdc, min_book_depth_shares,
        # depth_band, min_fair_value, max_fair_value, model_uncertainty,
        # atr_windows). `max_trades_per_window` is D-327's relaxation (3 ->
        # 12) - see that constant's docstring. `min_profit`, `max_loss`,
        # `model_stop_margin`, `convergence_eps`, `time_stop_sec` and
        # `window_close_exit_sec` are still accepted (via `super().__init__`)
        # for interface compatibility but read by nothing here - `manage_exit`
        # is a full rewrite that never consults them.
        super().__init__(edge_threshold=edge_threshold,
                         max_trades_per_window=max_trades_per_window, **kwargs)
        self.entry_ask_cap = float(entry_ask_cap)
        self.salvage_floor = float(salvage_floor)
        self.max_concurrent_positions = int(max_concurrent_positions)

        #: (market_slug, attempt_number) -> believed resolve-at timestamp.
        #: Keyed on the PAIR, not the slug alone: the parent's own
        #: `max_trades_per_window` allows up to `MAX_TRADES_PER_WINDOW` (12,
        #: D-327) entry ATTEMPTS inside one 5-minute window, all against the
        #: same market slug, and each is a separate simulated fill this file
        #: must count separately - keying on slug alone would let a second
        #: attempt silently overwrite the first's tracking entry and
        #: undercount how many are actually open. Pruned on every
        #: `evaluate()` call. See the module docstring's concurrency section.
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
        # See `LongshotFadeHoldToResolution._note_open`: this bound never
        # pops from `_open` itself, only from the order list, because `_open`
        # under-reporting its true count would silently widen the cap this
        # tracks against.
        while len(self._open_order) > OPEN_TRACKING_MAX:
            self._open_order.pop(0)

    @staticmethod
    def _open_key_for(position) -> tuple:
        """The same `(market_slug, attempt_number)` identity `evaluate()`
        keys `self._open` on, read back off a FILLED `PaperPosition` instead
        of the ENTER `Decision` - see the module docstring's concurrency
        section for why this moved to the position stream. `attempt_number`
        is stamped into `Decision.features` on every ENTER path
        (`FairValueArb.evaluate()`, inherited unchanged here) and
        `PaperAdapter.simulate_taker_buy` carries that dict through unchanged
        into `PaperPosition.features`, so it is present on every real fill.
        `position_id` (unique per fill) is the fallback for the case that
        should not happen (convention 11: never assume) where it is not.
        """
        features = getattr(position, 'features', None) or {}
        attempt = features.get('attempt_number')
        if attempt is None:
            return (position.market_slug, getattr(position, 'position_id', None))
        return (position.market_slug, attempt)

    # -- entry ------------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        """The parent's decision, tightened. See the module docstring.

        Structure: `FairValueArb.evaluate()` runs UNCHANGED - the tape
        observes, the model computes, the entry side is selected, the
        parent's own edge/band/depth gates apply. Only the ENTER branch is
        post-filtered here, against the two NEW gates this file adds. Every
        parent SKIP passes through with the parent's own reason untouched,
        the same shape `FairValueArbInverse.evaluate()` uses.
        """
        if ctx.window_ts is not None and ctx.seconds_into_window is not None:
            self._prune_open(float(ctx.window_ts)
                             + float(ctx.seconds_into_window))

        decision = super().evaluate(ctx)
        feats = decision.features
        feats['primary_exit'] = 'hold_to_resolution'
        feats['exits_before_resolution'] = False
        feats['has_salvage_floor_stop'] = True
        feats['entry_ask_cap'] = self.entry_ask_cap
        feats['open_positions_this_instance'] = len(self._open)
        feats['max_concurrent_positions'] = self.max_concurrent_positions

        if not decision.is_entry or not decision.legs:
            return decision

        # Task 1 rule 2 / 6: the raw model edge, explicit and named, on every
        # entry - not just present under the parent's own `raw_edge` key.
        feats['model_edge_at_entry'] = feats.get('raw_edge')
        # Task 1 rule 6: the half-spread actually paid is measurable from
        # this alongside the fill. The parent already stamps `best_bid` for
        # the chosen side (LOGGING ONLY there); re-stamped under an explicit
        # name here because this proposal's own data_requirements field names
        # it as a prerequisite deliverable.
        feats['best_bid_at_entry'] = feats.get('best_bid')
        feats['entry_ask'] = feats.get('best_ask')

        def skip(reason, **extra):
            feats.update(extra)
            decision.action = 'SKIP'
            decision.reason = reason
            decision.legs = []
            return decision

        best_ask = feats.get('best_ask')
        if best_ask is None or best_ask > self.entry_ask_cap:
            return skip('settlement_entry_ask_above_cap')

        if len(self._open) >= self.max_concurrent_positions:
            return skip('strategy_concurrency_cap_reached',
                        open_count=len(self._open))

        # No `_note_open` here. This is a PROPOSED entry - the adapter's own
        # `max_concurrent_positions` and `PolymarketRiskGate` both still run
        # downstream and can refuse it. Recording open state at this point,
        # before either gate runs, is the bug this file was fixed for
        # (2026-08-19) - see the module docstring's concurrency section.
        # `self._open` is only ADDED TO from `manage_exit`, once the position
        # has actually filled.
        return decision

    # -- exit B: salvage floor ---------------------------------------------

    def manage_exit(self, position, book, now: float,
                    fair_value: Optional[float] = None) -> ExitDecision:
        """Exit B only: the flat salvage floor. See the module docstring.

        Does NOT call `super().manage_exit()` - the parent's six-rule chain
        (`window_close`, `price_stop`, `profit_target`, `converged`,
        `model_stop`, `time_stop`) is entirely the exit-before-resolution
        machinery this experiment exists to stop running. This is a full
        rewrite, the same shape `LongshotFadeHoldToResolution.manage_exit` is.
        """
        pid = getattr(position, 'position_id', '')
        entry = float(getattr(position, 'avg_price', 0.0) or 0.0)
        shares = float(getattr(position, 'shares', 0.0) or 0.0)
        best_bid = None if book is None else book.best_bid

        # The position stream is the source of truth for "this instance's
        # concurrency cap has one more slot filled" - see `_open_key_for`'s
        # docstring and the module docstring's concurrency section. Runs
        # before every other branch below (including the unreadable/no-book
        # early returns) because a position that filled still occupies a
        # slot regardless of what its exit decision turns out to be this
        # cycle. Idempotent: a position already in `self._open` is a no-op
        # dict overwrite with the same `resolve_at`.
        window_ts = getattr(position, 'window_ts', None)
        if window_ts not in (None, ''):
            key = self._open_key_for(position)
            if key not in self._open:
                self._note_open(key, float(window_ts) + WINDOW_SECONDS)

        feats = {
            'entry_price': round(entry, 4),
            'shares': shares,
            'best_bid': best_bid,
            'fair_value_observed_not_acted_on': (
                None if fair_value is None else round(float(fair_value), 6)),
            'primary_exit': 'hold_to_resolution',
            'exits_before_resolution': False,
            'is_salvage_floor_stop': True,
            'salvage_floor': self.salvage_floor,
        }

        def hold(reason, **extra):
            return ExitDecision('HOLD', reason, position_id=pid,
                                features=dict(feats, **extra))

        def exit_now(reason, limit, **extra):
            return ExitDecision('EXIT', reason, position_id=pid,
                                limit_price=limit, shares=shares,
                                features=dict(feats, exit_limit_price=limit,
                                              **extra))

        if shares <= 0 or entry <= 0:
            return hold('unreadable_position')
        if book is None:
            return hold('no_orderbook')
        if best_bid is None:
            return hold('no_bid_liquidity',
                        unsellable=True,
                        note=('nobody is bidding; this position cannot be '
                              'closed and will resolve if that persists'))

        stop_px = salvage_stop_price(entry, self.salvage_floor)
        feats['salvage_stop_price'] = stop_px
        feats['salvage_floor_is_structural_floor'] = stop_px <= BINARY_STOP

        if best_bid <= stop_px + 1e-12:
            return exit_now('salvage_floor', URGENT_SELL_LIMIT,
                            salvage_stop_price=stop_px)

        return hold('holding_to_resolution')
