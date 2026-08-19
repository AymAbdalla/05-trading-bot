"""Status Quo Collector: sell tail risk on stable political questions.

Proposal 028 (`strategies/proposals/028-pm-status-quo-collector.md`), modeled
on Polymarket wallet Llalalala (rank #129 politics, $412,688 over 21 months):
buy NO at 80-90c on dated "the world stays the same" questions, collect the
10-25% yield when nothing changes, accept that a tail event eventually costs
one position. Read `strategies/polymarket/status_quo_classifier.py` first -
it is where a question TEXT becomes a STATUS_QUO / CHANGE_EVENT / UNKNOWN
shape, and this file trades ONLY the first of those three, never the other
two.

## The inverse shape (see the proposal's thesis)

Every other strategy in this registry buys cheap upside, paying a small
premium at a high loss rate, hoping for a rare big winner. This one buys
expensive safety: high win rate, small yield, and a tail loss that is
accepted by design rather than avoided. `manages_exits = False` and the stop
is BINARY_STOP (0.00) exactly like every other Polymarket strategy here - see
`base.py`'s module docstring - but the INTENT is inverted even though the
mechanics are identical.

## The classifier is the whole edge and the whole risk (handoff Task 1)

`evaluate` refuses anything the classifier does not call STATUS_QUO with high
confidence, including its own honest UNKNOWN. This file adds NO judgement of
its own about whether a question is stable - it either trusts the classifier
or it does not trade. Getting that gate right (or wrong) belongs to
`status_quo_classifier.py`, not here.

## Side, band, and never chasing (handoff Task 2, proposal's entry_exit_rules)

Side is NO, always. Never YES - this file has no code path that ever builds a
'Yes' leg. Entry requires the NO book's best ask in [MIN_NO_PRICE,
MAX_NO_PRICE] = [0.80, 0.90]. Below 0.80 the market disagrees with the
status-quo read (proposal: "never take a NO position below 0.80 on a question
whose tail is regime change"). Above 0.90 the remaining yield does not
compensate the tail (proposal: "never chase above 0.90").

## The ladder, and the judgment call it required

The proposal describes the reference wallet's actual behaviour - adding size
as the market drifted, at roughly 82c and 89c (its real Iran position:
$1.5k@50c, $94k@82c, $106k@89c). That is a CONTINUOUS strategy re-sizing an
open position as the tape moves. This file evaluates one market ONCE PER
POLL CYCLE with no memory of a live "position" object to add to - the same
constraint every other strategy in this package works under (see `DipArb`'s
and `SmartMoneyCallers`' own per-cycle dedup state). The translation used
here, stated because it is a real assumption (convention 17) and not the only
one that could have been chosen:

    up to THREE entries per market, each its own ENTER Decision / Leg:
      'initial'   - the first cycle the NO ask is anywhere in [0.80, 0.90]
      'scale_82'  - a later cycle, ask >= 0.82, ONLY after 'initial' filled
      'scale_89'  - a later cycle, ask >= 0.89, ONLY after 'scale_82' filled

Each rung is entered AT MOST ONCE per market (`_entered_rungs`, bounded and
per-instance - see `RUNG_MEMORY_SIZE` below). This reproduces the reference
wallet's shape (three additions, each at a higher price than the last,
capped at 0.90) inside a stateless-per-cycle evaluation loop, rather than
literally replaying its two fixed price points. A future build that gives
Polymarket strategies a live position handle to add to directly would be the
more faithful implementation; this is not that, and this docstring says so
rather than letting a reader assume the ladder is more literal than it is.

## Sizing: the cap IS the fixed size (handoff Task 2, rule 4)

`MAX_NOTIONAL_USDC` (default $50, 5% of the $1,000 paper bankroll) is the
per-RUNG notional ceiling, not a per-market one - three filled rungs on one
market can commit up to 3x this. That is deliberate: the proposal's 5% rule
protects a SINGLE position going to zero, and each rung is its own share lot
bought at its own price, exactly as the reference wallet's three separate
buys were three separate exposures. Shares are sized against `MAX_NO_PRICE`
(the worst price this file will ever pay), not against the current ask, so
the worst-case notional for any single rung never reaches the cap even on a
fill exactly at 0.90: `floor(50 / 0.90) * 0.90 = 49.50`, strictly under 5% of
$1,000.

## Ask depth (handoff Task 2, rule 2e): 2x size, walked under the cap

`ASK_DEPTH_MULTIPLE = 2.0`. The book must show at least twice the shares this
rung would buy within `DEPTH_BAND` of the best ask before this file will
enter - thin liquidity on an 80-90c book is itself information the classifier
cannot see.

## Exits

Holds to resolution. `manages_exits = False`. Stop is BINARY_STOP (0.00,
convention 8's floor), target is BINARY_TARGET (1.00). The paper adapter has
no sell path (convention 8's note, restated from `smart_money_copy`).

KILL CONDITION (from the proposal, restated per convention 6): after the
political space is live and this strategy is polled there, NOT_TESTED unless
it enters on at least 1% of evaluations over 500+ shadow cycles AND resolves
100+ positions (`agents/forge_shadow_eval.py` against `db/trading.db`). Once
100+ positions resolve: retired if net PnL < 0, or if the largest single loss
exceeds 40% of starting bankroll AND the trailing loss rate is higher than
the 80-90c entry band implies (the tail itself is expected and is not by
itself the kill trigger - see the proposal's `kill_condition` field verbatim).

This strategy is NOT_TESTED (D-268) like every other strategy in this
package until the resolution-PnL harness exists.
"""
import logging
from typing import Dict, List, Optional

from strategies.polymarket.base import (MARKET_TYPE_POLITICAL, Decision, Leg,
                                        MarketContext, PolymarketStrategy,
                                        effective_ask_for)
from strategies.polymarket.status_quo_classifier import (CHANGE_EVENT,
                                                          STATUS_QUO, UNKNOWN,
                                                          classify)

logger = logging.getLogger(__name__)

# Never False in this repo. See base.py's module docstring.
PAPER_MODE = True

#: The only side this file ever trades.
OUTCOME_SIDE = 'No'

#: Entry band. Strictly the proposal's numbers, not looser or tighter.
MIN_NO_PRICE = 0.80
MAX_NO_PRICE = 0.90

#: Scale-up rungs. Never above MAX_NO_PRICE - see the module docstring's
#: ladder section for why these are discrete thresholds rather than a
#: continuous add.
SCALE_RUNG_1 = 0.82
SCALE_RUNG_2 = 0.89

INITIAL = 'initial'
SCALE_82 = 'scale_82'
SCALE_89 = 'scale_89'
#: Ladder order. A rung can only be entered once every rung before it in this
#: tuple has already been entered for that market.
RUNG_ORDER = (INITIAL, SCALE_82, SCALE_89)

#: Paper bankroll, per the proposal ("paper bankroll is $1000").
BANKROLL_USDC = 1000.0

#: Per-rung notional ceiling. See the module docstring's sizing section for
#: why this is per-RUNG and why sizing against MAX_NO_PRICE keeps the
#: worst-case fill strictly under 5% of bankroll.
MAX_POSITION_FRACTION = 0.05
MAX_NOTIONAL_USDC = BANKROLL_USDC * MAX_POSITION_FRACTION

#: Ask depth required, as a multiple of this rung's share size.
ASK_DEPTH_MULTIPLE = 2.0

#: Tolerance band for the depth check, matching `DipArb`'s `depth_band` in
#: spirit: how far past the best ask we will look when asking "is there real
#: size behind this quote".
DEPTH_BAND = 0.02

#: How many distinct market slugs' rung progress this instance remembers.
#: Bounded for the same reason `SmartMoneyCallers._entered_play_ids` is
#: bounded - an unbounded per-process dict would leak for the life of a
#: shadow-loop process that runs for days. THE CAVEAT (convention 17): a
#: market that falls out of this window and is polled again later looks like
#: a market we have never touched, and a rung already filled could fill
#: again. Every dedup structure in this package shares this restart/eviction
#: caveat; it is not unique to this file.
RUNG_MEMORY_SIZE = 500


class StatusQuoCollector(PolymarketStrategy):
    """Buy NO at 80-90c on classifier-confirmed status-quo political
    questions. See the module docstring for the full ruling."""

    strategy_name = 'PM_status_quo_collector'
    paper_mode = PAPER_MODE

    #: Political space only. The classifier is built and tested against
    #: political-question phrasing (elections, governments, heads of state);
    #: widening this to other GENERAL_BINARY_MARKET_TYPES is a future opt-in,
    #: not a default, matching `smart_money_callers`' narrowing ruling.
    supported_market_types = (MARKET_TYPE_POLITICAL,)

    #: Holds to resolution. The shadow loop reads this to decide whether to
    #: poll an exit.
    manages_exits = False

    def __init__(self,
                 min_no_price: float = MIN_NO_PRICE,
                 max_no_price: float = MAX_NO_PRICE,
                 scale_rung_1: float = SCALE_RUNG_1,
                 scale_rung_2: float = SCALE_RUNG_2,
                 max_notional_usdc: float = MAX_NOTIONAL_USDC,
                 ask_depth_multiple: float = ASK_DEPTH_MULTIPLE,
                 depth_band: float = DEPTH_BAND):
        self.min_no_price = float(min_no_price)
        self.max_no_price = float(max_no_price)
        self.scale_rung_1 = float(scale_rung_1)
        self.scale_rung_2 = float(scale_rung_2)
        self.max_notional_usdc = float(max_notional_usdc)
        self.ask_depth_multiple = float(ask_depth_multiple)
        self.depth_band = float(depth_band)
        #: slug -> set of rungs already entered for that market. Bounded -
        #: see RUNG_MEMORY_SIZE above.
        self._entered_rungs: Dict[str, set] = {}
        self._slug_order: List[str] = []

    # -- rung bookkeeping ---------------------------------------------------

    def _rungs_for(self, slug: str) -> set:
        rungs = self._entered_rungs.get(slug)
        if rungs is None:
            rungs = set()
            self._entered_rungs[slug] = rungs
            self._slug_order.append(slug)
            while len(self._slug_order) > RUNG_MEMORY_SIZE:
                oldest = self._slug_order.pop(0)
                self._entered_rungs.pop(oldest, None)
        return rungs

    def _next_rung(self, slug: str, best_ask: float) -> Optional[str]:
        """Which rung (if any) this cycle's price qualifies this market for.

        None means "no unfilled rung is reachable at this price this cycle" -
        either the ladder is already fully filled, or the next rung's price
        floor has not been reached yet.
        """
        entered = self._rungs_for(slug)
        if INITIAL not in entered:
            return INITIAL
        if SCALE_82 not in entered:
            return SCALE_82 if best_ask >= self.scale_rung_1 else None
        if SCALE_89 not in entered:
            return SCALE_89 if best_ask >= self.scale_rung_2 else None
        return None

    def _note_entered(self, slug: str, rung: str) -> None:
        self._rungs_for(slug).add(rung)

    # -- entry ----------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        # RAISES on a type we did not declare (convention 22).
        self.assert_supports(ctx)

        market = ctx.market
        slug = getattr(market, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            feats.setdefault('outcome_side', OUTCOME_SIDE)
            feats.setdefault('min_no_price', self.min_no_price)
            feats.setdefault('max_no_price', self.max_no_price)
            feats.setdefault('exits_before_resolution', False)
            feats.setdefault('sizing_model', 'fixed_notional_per_rung')
            feats.setdefault('max_notional_usdc', self.max_notional_usdc)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if market is None:
            return decide('SKIP', 'no_market')

        question = getattr(market, 'question', None) or ''
        resolution_date = getattr(market, 'end_date', None)
        classification = classify(question, resolution_date)
        feats = {
            'classifier_label': classification.label,
            'classifier_rule': classification.rule,
        }

        if classification.label == CHANGE_EVENT:
            return decide('SKIP', 'classifier_change_event_shape', **feats)
        if classification.label == UNKNOWN:
            return decide('SKIP', 'classifier_unknown_shape', **feats)
        assert classification.label == STATUS_QUO

        if not getattr(market, 'is_binary', False):
            return decide('SKIP', 'not_binary', **feats)
        if not resolution_date:
            # The classifier may have used a date found in the question TEXT
            # instead - entry additionally requires the market's own
            # resolution date field, per the proposal's entry rule (c).
            return decide('SKIP', 'no_resolution_date', **feats)

        book = ctx.book(OUTCOME_SIDE)
        if book is None:
            return decide('SKIP', 'no_orderbook', **feats)
        best_ask = book.best_ask
        feats['best_ask'] = best_ask
        if best_ask is None:
            return decide('SKIP', 'no_asks', **feats)

        if not (self.min_no_price <= best_ask <= self.max_no_price):
            return decide('SKIP', 'price_outside_entry_band', **feats)

        if slug is None:
            # Cannot track rung state for a market with no identity. Refuse
            # rather than risk re-entering the same rung indefinitely.
            return decide('SKIP', 'no_market_slug', **feats)

        rung = self._next_rung(slug, best_ask)
        feats['rungs_already_entered'] = sorted(self._rungs_for(slug))
        if rung is None:
            reason = ('ladder_fully_filled'
                      if SCALE_89 in self._rungs_for(slug)
                      else 'ladder_rung_not_yet_reached')
            return decide('SKIP', reason, **feats)
        feats['rung'] = rung

        shares = int(self.max_notional_usdc // self.max_no_price)
        feats['shares'] = shares
        if shares <= 0:
            return decide('SKIP', 'unsizable_at_notional_cap', **feats)

        depth_limit = round(best_ask + self.depth_band, 6)
        depth = book.ask_depth(depth_limit)
        required_depth = self.ask_depth_multiple * shares
        feats['depth_band'] = self.depth_band
        feats['ask_depth_within_band'] = depth
        feats['required_depth'] = required_depth
        if depth < required_depth:
            return decide('SKIP', 'insufficient_ask_depth', **feats)

        effective = effective_ask_for(book, shares, self.max_no_price)
        feats['effective_ask'] = (None if effective is None
                                  else round(effective, 4))
        if effective is None:
            return decide('SKIP', 'unfillable_at_cap', **feats)
        if effective > self.max_no_price:
            return decide('SKIP', 'effective_ask_above_cap', **feats)

        feats['notional_usdc'] = round(shares * effective, 4)
        feats['breakeven_win_rate'] = round(effective, 4)

        self._note_entered(slug, rung)

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=OUTCOME_SIDE,
                                limit_price=self.max_no_price,
                                order_type='taker',
                                shares=shares,
                                expected_price=effective)],
                      **feats)
