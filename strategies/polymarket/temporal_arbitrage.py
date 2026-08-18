"""Temporal Arbitrage: build a $1.00 pair one cheap leg at a time.

Implements Forge proposal 002 (`strategies/proposals/002-pm-temporal-arbitrage.md`,
source: Dan1ro0 concept 4B). This is NOT a moondevonyt port.

THE STRUCTURE. Up and Down on the same 5m window are mutually exclusive and
exhaustive, so one share of each redeems for exactly $1.00 whatever BTC does.
Buying both AT ONCE is `box_builder`. Buying each one WHEN IT IS CHEAP, at
different instants inside the same window, is this. A pair assembled for 0.84
is 16c of profit that does not care about direction.

    leg 1   BTC runs away from the window open   -> the losing side gets cheap
    leg 2   BTC comes back toward the open       -> the other side gets cheap
    pair    redeems 1.00                          -> profit = 1.00 - pair cost

## THE RISK IS LEG COMPLETION, NOT DIRECTION, AND IT IS THE WHOLE TRADE

Between leg 1 and leg 2 this holds a NAKED directional position. If BTC keeps
running, leg 2 never gets cheap, the window expires, and the unpaired leg
resolves to 0.00. Proposal 002 does the arithmetic: at a 47c leg-1 entry the
break-even completion rate is about 89%, so a 60% completion rate makes this
"a losing directional strategy wearing an arbitrage label".

Our leg-1 cap is 0.35, not the proposal's 0.47, which moves break-even to
roughly 0.35 / (0.35 + 0.16) = 69%. Cheaper leg 1, more room. That is the
entire reason for the tighter cap and it is the first number to check when
this is scored.

An UNPAIRED leg is HELD TO RESOLUTION and its PnL belongs to this strategy.
A version of this that reported only completed pairs would show a clean 6-16c
per pair and be entirely fictional.

## WHAT THIS STRATEGY CANNOT SEE (read before quoting any completion rate)

`evaluate()` returns a Decision. It never learns whether that Decision became a
fill: the halt check, the risk gate and the paper adapter all sit downstream and
any of them can refuse. So the state machine below tracks leg 1 as ATTEMPTED,
not as HELD, and every feature it emits says so (`leg1_fill_confirmed` is always
False, `completion_rate_measurable_from_this_log` is always False).

The bias runs both ways and neither is small: a refused leg 1 makes us chase a
leg 2 for a position we do not own, and it also stops us retrying leg 1 in that
window. Completion rate has to be computed by joining `positions` on window_ts
in the database, never by counting ENTER decisions in this log. Convention 22 -
a docstring claiming otherwise would not be a wiring test.

## DEVIATIONS FROM PROPOSAL 002, all tightening, all logged

  1. Leg 1 is capped at 0.35, not 0.47, and requires a BTC move that justifies
     the cheapness. The proposal buys "either side at or below 0.47" with no
     directional trigger at all, which on a quiet book fires constantly at a
     break-even completion rate we have no reason to think we clear.
  2. Leg 2 is capped at min(0.49, 0.94 - leg1). The proposal caps it only at
     0.94 - leg1. Both bind; the tighter one is reported as `leg2_cap`.
  3. Blocks are 5 shares, not 50. Polymarket's minimum, and this has never been
     scored. 50 shares of naked leg is not a size to discover a completion rate
     at.
  4. Both legs must clear their cap on the BOOK-WALKED effective average for the
     full block, not on the best ask (the house rule the other four already
     follow).

The move trigger is expressed in ATR units, not in a hardcoded bps number, so
it recalibrates with volatility instead of expiring silently (convention 17).
`ctx.atr14` is in BASIS POINTS - see MarketContext's units note.

## SYMMETRY

Proposal 002 says "buy EITHER side when its ask is at or below" the cap, and the
task brief states it in the BTC-up direction (Down first, Up second). They are
the same trade read from opposite ends and this implements both, defaulting to
symmetric. `symmetric=False` restricts leg 1 to Down, which halves the
opportunity set and is only there for a sensitivity run.

KILL CONDITION: trailing-30 win rate below 40% once 15 trades exist. NOTE what
that measures: a COMPLETE pair always wins, so a win rate below 40% can only be
driven by unpaired legs, which makes this a completion-rate kill condition
written in win-rate units. Proposal 002's own clauses also bind and are
stricter: median completed-pair cost above 0.97 over 200+ attempted pairs;
fewer than 60% of first legs completing before expiry; or net PnL negative once
UNPAIRED legs are charged here at their realised resolution PnL. Scored by
backtest/polymarket_harness.py. Convention 5's 30bps floor applies on top.
"""
from typing import Dict, Optional

from strategies.polymarket.base import (Decision, Leg, MarketContext,
                                        PolymarketStrategy, effective_ask_for,
                                        opposite)

# Never False in this repo.
PAPER_MODE = True

LEG1_ASK_CAP = 0.35        # the cheap side, after BTC has run
LEG2_ASK_CAP = 0.49        # the other side, after BTC comes back
PAIR_COST_CAP = 0.94       # proposal 002's ceiling. The pair redeems 1.00.
MOVE_TRIGGER_ATR = 1.0     # |move from open| must exceed one typical 5m move
LEG2_CUTOFF_SEC = 60       # stop attempting leg 2 with this much left
#: Latest leg-1 entry. Derived, not tuned: leg 1 must leave at least as much
#: leg-2 opportunity as the cutoff it has to clear, so LEG2_CUTOFF_SEC * 2.
LEG1_DEADLINE_SEC = LEG2_CUTOFF_SEC * 2
SHARES_PER_LEG = 5         # Polymarket minimum. Bounds the naked exposure.
STATE_WINDOWS_KEPT = 8     # ring size for per-window state


class TemporalArbitrage(PolymarketStrategy):
    """Assemble a 1.00-redeeming pair from two cheap legs bought minutes apart."""

    strategy_name = 'PM_temporal_arbitrage'
    paper_mode = PAPER_MODE

    def __init__(self, leg1_ask_cap: float = LEG1_ASK_CAP,
                 leg2_ask_cap: float = LEG2_ASK_CAP,
                 pair_cost_cap: float = PAIR_COST_CAP,
                 move_trigger_atr: float = MOVE_TRIGGER_ATR,
                 leg2_cutoff_sec: float = LEG2_CUTOFF_SEC,
                 leg1_deadline_sec: float = LEG1_DEADLINE_SEC,
                 shares_per_leg: float = SHARES_PER_LEG,
                 symmetric: bool = True):
        self.leg1_ask_cap = leg1_ask_cap
        self.leg2_ask_cap = leg2_ask_cap
        self.pair_cost_cap = pair_cost_cap
        self.move_trigger_atr = move_trigger_atr
        self.leg2_cutoff_sec = leg2_cutoff_sec
        self.leg1_deadline_sec = leg1_deadline_sec
        self.shares_per_leg = shares_per_leg
        #: False restricts leg 1 to the Down side. Sensitivity runs only.
        self.symmetric = symmetric
        #: window_ts -> block state. See the docstring: 'leg1' means ATTEMPTED.
        self._state: Dict[int, dict] = {}

    # -- per-window state ---------------------------------------------------

    def state_for(self, window_ts: int) -> dict:
        """Block state for one window, created on first sight.

        `stage` is one of:
          idle       nothing attempted yet
          leg1       leg 1 ATTEMPTED (not confirmed filled - see the docstring)
          complete   leg 2 attempted, the block is closed
          unpaired   the leg-2 deadline passed with leg 1 outstanding
        """
        state = self._state.get(window_ts)
        if state is None:
            state = {'stage': 'idle', 'leg1_side': None, 'leg1_price': None,
                     'leg1_shares': None, 'leg2_price': None}
            self._state[window_ts] = state
            self._prune(window_ts)
        return state

    def _prune(self, current_ts: int) -> None:
        """Keep the last few windows. A 5m loop would otherwise grow forever."""
        if len(self._state) <= STATE_WINDOWS_KEPT:
            return
        for ts in sorted(self._state)[:-STATE_WINDOWS_KEPT]:
            self._state.pop(ts, None)

    @staticmethod
    def window_open(ctx: MarketContext) -> Optional[float]:
        """Open of THIS window, taken from the matching price bar.

        Matched on timestamp rather than assumed to be the last bar: a stale
        candle pull would otherwise hand us the previous window's open and every
        move would be measured from the wrong place. No match means no
        reference, which is a skip, not a substitution.

        This is a BTC exchange bar open. It is NOT the settlement strike (a
        Chainlink 60s TWAP Gamma does not publish) and must never be used as
        one - it is the reference for our own directional trigger only, where a
        few dollars of disagreement moves the trigger instant slightly and
        cannot mis-settle anything.
        """
        for w in ctx.windows:
            if w.ts == ctx.window_ts:
                return w.open
        return None

    # -- decision -----------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        slug = getattr(ctx.market, 'slug', None)
        state = self.state_for(ctx.window_ts)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            feats.setdefault('stage', state['stage'])
            feats.setdefault('leg1_side', state['leg1_side'])
            feats.setdefault('leg1_price', state['leg1_price'])
            # Stated on EVERY row, including skips: nothing downstream may
            # compute a completion rate by counting these decisions.
            feats.setdefault('leg1_fill_confirmed', False)
            feats.setdefault('completion_rate_measurable_from_this_log', False)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market')
        if state['stage'] == 'complete':
            return decide('SKIP', 'pair_complete',
                          pair_cost=self._pair_cost(state))
        if state['stage'] == 'unpaired':
            # Recorded once when the deadline passed; every later poll in the
            # window repeats it so the leg stays visible in the log rather than
            # disappearing after one row.
            return decide('SKIP', 'unpaired_leg_held_to_resolution')

        remaining = ctx.seconds_remaining
        if remaining is None:
            # Both deadlines are clock deadlines. Without a clock this strategy
            # cannot tell leg-1 time from leg-2 time and must not guess.
            return decide('SKIP', 'no_window_clock')
        if ctx.spot is None:
            return decide('SKIP', 'no_spot', seconds_remaining=round(remaining, 1))

        open_ref = self.window_open(ctx)
        if open_ref is None:
            return decide('SKIP', 'no_window_open',
                          windows_available=len(ctx.windows),
                          seconds_remaining=round(remaining, 1))
        if open_ref <= 0:
            return decide('SKIP', 'invalid_window_open', window_open=open_ref)
        if ctx.atr14 is None:
            return decide('SKIP', 'no_atr')
        if ctx.atr14 <= 0:
            # Every recent window flat. The stretch ratio is undefined, not
            # infinite, and not zero.
            return decide('SKIP', 'zero_atr_undefined_stretch', atr14_bps=ctx.atr14)

        move_bps = ((ctx.spot - open_ref) / open_ref) * 10_000.0
        stretch = abs(move_bps) / ctx.atr14

        feats = {
            'window_open': round(open_ref, 2),
            'window_open_source': 'btc_price_bar_open_not_settlement_strike',
            'spot': round(ctx.spot, 2),
            'move_bps': round(move_bps, 3),
            'atr14_bps': round(ctx.atr14, 3),
            'stretch_ratio': round(stretch, 3),
            'move_trigger_atr': self.move_trigger_atr,
            'seconds_remaining': round(remaining, 1),
            'pair_cost_cap': self.pair_cost_cap,
            'shares_per_leg': self.shares_per_leg,
            # Not a probability. P(complete) is the unknown this strategy
            # exists to measure, so there is no honest number to put here.
            'confidence': 0.0,
            'confidence_unknown_by_design': True,
        }

        if state['stage'] == 'leg1':
            return self._attempt_leg2(ctx, state, decide, feats, stretch,
                                      remaining)
        return self._attempt_leg1(ctx, state, decide, feats, move_bps, stretch,
                                  remaining)

    # -- leg 1 --------------------------------------------------------------

    def _attempt_leg1(self, ctx, state, decide, feats, move_bps, stretch,
                      remaining) -> Decision:
        if remaining < self.leg1_deadline_sec:
            # Entering leg 1 here guarantees an unpaired leg: there would be
            # less leg-2 window left than the cutoff leg 2 has to clear.
            return decide('SKIP', 'too_late_for_leg1',
                          leg1_deadline_sec=self.leg1_deadline_sec, **feats)

        if stretch < self.move_trigger_atr:
            # BTC has not run far enough from the open for either side to be
            # cheap for the reason this strategy claims.
            return decide('SKIP', 'not_stretched', **feats)

        # The side BTC just ran AWAY from is the one the book marks down.
        leg1_side = 'Down' if move_bps > 0 else 'Up'
        feats['leg1_candidate_side'] = leg1_side
        if not self.symmetric and leg1_side != 'Down':
            return decide('SKIP', 'symmetric_disabled', **feats)

        book = ctx.book(leg1_side)
        if book is None:
            return decide('SKIP', 'no_orderbook', **feats)
        best_ask = book.best_ask
        feats['leg1_best_ask'] = best_ask
        feats['leg1_cap'] = self.leg1_ask_cap
        if best_ask is None:
            return decide('SKIP', 'no_asks', **feats)
        if best_ask > self.leg1_ask_cap:
            # Named BEFORE the depth gate. "Nobody is offering under the cap"
            # and "somebody is, for two shares" are different facts, and
            # `ask_depth(cap)` reports 0 for both. Convention 20.
            return decide('SKIP', 'leg1_ask_above_cap', **feats)

        depth = book.ask_depth(self.leg1_ask_cap)
        feats['leg1_depth_at_cap'] = depth
        if depth < self.shares_per_leg:
            return decide('SKIP', 'insufficient_leg1_depth', **feats)

        effective = effective_ask_for(book, self.shares_per_leg,
                                      self.leg1_ask_cap)
        feats['leg1_effective_ask'] = (None if effective is None
                                       else round(effective, 4))
        if effective is None:
            return decide('SKIP', 'leg1_unfillable_at_cap', **feats)
        if effective > self.leg1_ask_cap:
            # walk_book cannot return this given the same limit. The cap IS the
            # edge, so the guard stays rather than trusting that.
            return decide('SKIP', 'leg1_effective_ask_above_cap', **feats)

        # Leg 2 must still be affordable under the pair cap, or this leg is a
        # naked directional bet from the moment it goes on.
        leg2_budget = round(self.pair_cost_cap - effective, 4)
        feats['leg2_budget'] = leg2_budget
        if leg2_budget <= 0:
            return decide('SKIP', 'no_leg2_budget', **feats)

        state['stage'] = 'leg1'
        state['leg1_side'] = leg1_side
        state['leg1_price'] = round(effective, 4)
        state['leg1_shares'] = self.shares_per_leg
        feats['stage'] = 'leg1'
        feats['leg1_side'] = leg1_side
        feats['leg1_price'] = state['leg1_price']
        feats['leg'] = 1
        feats['naked_until_leg2'] = True
        feats['max_loss_if_unpaired_usdc'] = round(
            effective * self.shares_per_leg, 4)

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=leg1_side,
                                limit_price=self.leg1_ask_cap,
                                order_type='taker',
                                shares=self.shares_per_leg,
                                expected_price=effective)],
                      **feats)

    # -- leg 2 --------------------------------------------------------------

    def _attempt_leg2(self, ctx, state, decide, feats, stretch,
                      remaining) -> Decision:
        leg1_price = state['leg1_price']
        leg2_side = opposite(state['leg1_side'])
        feats['leg2_side'] = leg2_side

        if remaining < self.leg2_cutoff_sec:
            # Time exit. The block is UNPAIRED and the leg is held to
            # resolution; its PnL is charged to this strategy, never excluded.
            state['stage'] = 'unpaired'
            feats['stage'] = 'unpaired'
            feats['unpaired_leg_side'] = state['leg1_side']
            feats['unpaired_leg_price'] = leg1_price
            feats['unpaired_max_loss_usdc'] = round(
                (leg1_price or 0.0) * (state['leg1_shares'] or 0), 4)
            return decide('SKIP', 'leg2_deadline_passed_unpaired',
                          leg2_cutoff_sec=self.leg2_cutoff_sec, **feats)

        if stretch >= self.move_trigger_atr:
            # Still stretched: BTC has not come back toward the open, so leg 2
            # is not cheap for the reason this strategy claims. Waiting is the
            # position, and it is a naked one.
            return decide('SKIP', 'no_reversal_yet', **feats)

        cap = round(min(self.leg2_ask_cap, self.pair_cost_cap - leg1_price), 4)
        feats['leg2_cap'] = cap
        feats['leg2_cap_binding'] = ('leg2_ask_cap'
                                     if self.leg2_ask_cap <= cap + 1e-12
                                     else 'pair_cost_cap')
        if cap <= 0:
            return decide('SKIP', 'no_profitable_completion', **feats)

        book = ctx.book(leg2_side)
        if book is None:
            return decide('SKIP', 'no_orderbook', **feats)
        best_ask = book.best_ask
        feats['leg2_best_ask'] = best_ask
        if best_ask is None:
            return decide('SKIP', 'no_asks', **feats)
        if best_ask > cap:
            # See the leg-1 note: a cap miss is not a depth miss.
            return decide('SKIP', 'leg2_ask_above_cap', **feats)

        depth = book.ask_depth(cap)
        feats['leg2_depth_at_cap'] = depth
        if depth < self.shares_per_leg:
            return decide('SKIP', 'insufficient_leg2_depth', **feats)

        effective = effective_ask_for(book, self.shares_per_leg, cap)
        feats['leg2_effective_ask'] = (None if effective is None
                                       else round(effective, 4))
        if effective is None:
            return decide('SKIP', 'leg2_unfillable_at_cap', **feats)
        if effective > cap:
            # Unreachable given the same limit; kept for the same reason as the
            # leg-1 guard above.
            return decide('SKIP', 'leg2_effective_ask_above_cap', **feats)

        pair_cost = round(leg1_price + effective, 4)
        feats['pair_cost'] = pair_cost
        feats['gross_profit_per_pair'] = round(1.00 - pair_cost, 4)
        feats['gross_edge_bps'] = (round((1.00 - pair_cost) / pair_cost * 10_000, 1)
                                   if pair_cost > 0 else None)
        if pair_cost > self.pair_cost_cap:
            # Unreachable given the cap arithmetic above, but the cap IS the
            # edge and a silent regression here would be invisible.
            return decide('SKIP', 'pair_cost_above_cap', **feats)

        state['stage'] = 'complete'
        state['leg2_price'] = round(effective, 4)
        feats['stage'] = 'complete'
        feats['leg'] = 2
        feats['guaranteed_redemption'] = 1.00

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=leg2_side,
                                limit_price=cap,
                                order_type='taker',
                                shares=self.shares_per_leg,
                                expected_price=effective)],
                      **feats)

    @staticmethod
    def _pair_cost(state: dict) -> Optional[float]:
        if state['leg1_price'] is None or state['leg2_price'] is None:
            return None
        return round(state['leg1_price'] + state['leg2_price'], 4)
