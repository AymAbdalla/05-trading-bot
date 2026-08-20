"""Corridor Pair Live: the 15m leader paired with the final-5m opposite.

RENAMED from `cross_window_relative_value` (D-281). The old name asserted a
lineage this file does not have - it read as an implementation of Forge proposal
005 and it is not one. Proposal 005 stays PROPOSED and UNBUILT
(`strategies/proposals/005-pm-cross-window-relative-value.md`, source: Dan1ro0
concept 3). What runs here is the FLOORED PAIR structure the task brief
specifies, which is `corridor_collector`'s structure driven by a lead we can
actually measure. The name now describes the code.

## READ THIS FIRST: what is implemented is NOT proposal 005's own strategy

Proposal 005 describes a ONE-LEG relative-value trade: model a fair value for
the 15m window, model the 15m outcome implied by the 5m chain, standardise the
gap against 30 days of PAIRED trailing history, and buy the stale side when
abs(score) >= 2. Its own `data_requirements` calls that history a BLOCKER: "the
mean and stdev in the score are not tunable constants, they are measured
quantities, and until they are measured this strategy has no entry rule at
all." We have no paired history, so that strategy has no entry rule today and
nothing here invents one - freezing a guessed mean and stdev into constants is
precisely the `COST_FLOOR = -0.30` mistake proposal 005 spends its last section
warning about (convention 17).

What IS implemented is the STRUCTURE the task brief describes: buy the 15m
leader and the final-5m opposite, hold both to resolution, floor the pair at
$1.00, cap the pair price at $1.41. Proposal 005 names that structure
explicitly - as `corridor_collector`, its "nearest neighbour", and it insists
the two must never be pooled:

    | | corridor_collector | proposal 005 |
    | Legs | 2, opposite sides | 1 |
    | Worst case | pair floored at 1.00 | lose the full premium |
    | Bet | structure | mispricing |

So this file trades the FLOOR, not the mispricing. Nothing it produces is
evidence for or against proposal 005's relative-value hypothesis, and no result
from it may be filed under that hypothesis. Convention 11.

## Then why is this not just a second copy of corridor_collector?

Because of where the lead comes from, and that difference is the reason this one
can actually run.

`corridor_collector` reads `ctx.lead_bps`, which the shadow loop leaves None
forever because it is derived from a settlement strike Gamma does not publish.
It skips `no_lead_or_atr` on every window and always will. Its own docstring
flags a second problem: `context.py`'s `lead_bps` is `(spot - strike) / strike`,
the 5m ITM distance, which is NOT the 15m-open-to-now lead the structure means.

This strategy computes the lead the structure actually calls for, from data we
have: the open of the 5m price bar that STARTED the 15m window, matched on
timestamp, against live spot. No strike, no substitution, and it is the correct
quantity rather than a stand-in for it.

It also enforces a structural precondition corridor_collector does not check:
the 5m window must be the FINAL THIRD of the 15m window. The $1.00 floor exists
only because both markets settle off the same close. Pair the 15m leader with
the FIRST 5m third and there is no floor at all - both legs can lose - and
nothing in the pricing would tell you.

## The structure, and where the money is

A 15m window [T, T+900] contains the 5m window [T+600, T+900] as its final
third, and both settle on the same close P15. Write P0 for the 15m open and P10
for the 5m open. Buy the 15m LEADER and the 5m OPPOSITE:

    P15 beyond P10  (the lead runs on)   -> $1.00   the 15m leg pays
    P0 < P15 < P10  (THE CORRIDOR)       -> $2.00   BOTH pay
    P15 beyond P0   (a full reversal)    -> $1.00   the 5m leg saves you

At least one leg always wins. Fair value is 1.00 + P(corridor). The floor is
the stop and no other is needed (convention 8).

THE FLOOR IS AN IDENTITY, NOT A FINDING. Given both legs settle off the same
close, both losing is arithmetically impossible. moondevonyt's "0 failures in
34,918 windows" is a wiring check. ALL the edge is in whether P(corridor) is
what the table says and whether the pair can be bought below 1 + that.

## The 1.41 cap is FAIR VALUE, so it is a ceiling and not an entry rule

The brief's $1.41 is 1.00 + 0.413, the blended in-zone corridor rate. Paying
fair value earns exactly zero before fees. Worse, 0.413 is a blend: the binned
table reads 0.326 at a 5-10bps lead, so at a 6bps lead the fair pair is 1.326
and a 1.41 cap is **8.4c above fair** - a reliably negative-expectancy entry
that would look like a rule being followed.

This is the same failure `corridor_collector`'s docstring already records
against an earlier version of itself. So the 1.41 cap is implemented as
specified AND a second gate requires the pair to be at or below the BINNED fair
value for the measured lead. Both are reported; `pair_cap_binding` says which
one stopped the trade. `require_binned_fair=False` disables the second gate for
a sensitivity run and is not a mode to trade in.

## NO ZERO-EDGE ENTRIES: THE 8c FLOOR (D-281, superseding D-277's 2c)

An earlier version of this file set the second gate at `pair_cost <=
binned_fair`, so a pair could clear at an edge of EXACTLY 0.00 and the strategy
would enter. Paying fair value earns zero before fees and less than zero after
them, and a run full of zero-edge entries is not a test of the structure, it is
a test of the fee schedule. The gate is now `pair_cost <= binned_fair -
MIN_EDGE_VS_BINNED_FAIR`, with the floor at 8c.

D-277 first set that floor at 2c, on the argument that this structure is
untested and an 8c floor risks reporting "no fires" when the truth is "the
threshold was set too high before anything was measured". Raven ruled against
that in D-281: the $1.41 pair cap IS fair value, so a pair bought at or near
binned fair is negative-expectancy after fees BY CONSTRUCTION, and the floor is
therefore not a tuning knob to be set low pending evidence. It is 8c, the same
number `corridor_collector` uses, so the two floored-pair strategies are gated
alike and their fire rates are comparable rather than confounded by a threshold
that differs between them.

`edge_vs_binned_fair` still rides on every row, including on the SKIP rows the
floor stops, so the counterfactual is measurable: if the realised distribution
clusters just under 8c the floor can be argued down on evidence, and if nothing
ever comes within 8c the structure never cleared fair value and the floor cost
nothing. `pair_cap_binding='edge_floor_8c'` names this gate specifically, so a
window stopped for "inside fair value but not by enough" is never confused with
one stopped for "above fair value outright".

P(corridor) IS AN UNVERIFIED VENDOR NUMBER. The table is moondevonyt's
measurement on his data, reused here because it is the only one that exists.
Every row says so.

KILL CONDITION: mean pair PnL below $0.95 per pair over 50 pairs. Note the shape
- the pair is floored at $1.00 gross, so an average below 0.95 means the pair is
being bought too dear rather than the structure failing. Also dies if realised
P(corridor) inside the 5-30bps zone comes in under 25%, if any pair ever
resolves with both legs losing (that would mean the final-third precondition is
not doing its job), or if backtest/polymarket_harness.py scores it under 30bps
net edge on our own data (convention 5, D-268).
"""
from typing import Optional

from strategies.polymarket.base import (Decision, Leg, MarketContext,
                                        PolymarketStrategy, effective_ask_for,
                                        opposite)
from strategies.polymarket.corridor_collector import (P_CORRIDOR_BLENDED,
                                                      p_corridor_lookup)

# Never False in this repo.
PAPER_MODE = True

WINDOW_5M_SEC = 300
WINDOW_15M_SEC = 900
#: Offset of the final 5m third inside its 15m parent. The whole floor depends
#: on this being exact.
FINAL_THIRD_OFFSET_SEC = WINDOW_15M_SEC - WINDOW_5M_SEC

LEAD_BPS_MIN = 5.0          # below this there is no leader to speak of
LEAD_BPS_MAX = 30.0         # above this the corridor stops being reachable
ASK_5M_CAP = 0.50           # the brief: 5m opposite side under 50c
ASK_15M_CAP = 0.93          # sanity cap on the leader leg
MAX_PAIR_COST = 1.41        # the brief: 1.00 + the 0.413 blended corridor rate
#: D-281 (was 0.02 under D-277). Minimum edge BELOW the binned fair pair value
#: before an entry is allowed. Set above zero so a fair-value entry - zero
#: gross, negative net - can never clear, and set at corridor_collector's 8c so
#: the two floored-pair strategies are gated alike. See the "NO ZERO-EDGE
#: ENTRIES" section.
MIN_EDGE_VS_BINNED_FAIR = 0.08
ENTRY_WINDOW_SEC = 90       # first 90s of the final 5m window
SHARES_PER_LEG = 5          # equal shares, Polymarket minimum


class CorridorPairLive(PolymarketStrategy):
    """Buy the 15m leader plus the final-5m opposite. At least one leg wins."""

    #: D-281 requires that the key stop referencing proposal 005 - nothing this
    #: strategy emits is evidence for or against proposal 005's relative-value
    #: hypothesis - and it writes `PM_corridor_pair` as the key. The module and
    #: the class keep the `_live` suffix; only this key is ruled.
    #:
    #: No rows exist under any key yet (this strategy has never been scored,
    #: D-268), so nothing is being split by settling it now.
    strategy_name = 'PM_corridor_pair'

    #: Same two-market shape as `PM_corridor_collector`.
    market_duration_scope = 'mixed'
    paper_mode = PAPER_MODE

    def __init__(self, lead_bps_min: float = LEAD_BPS_MIN,
                 lead_bps_max: float = LEAD_BPS_MAX,
                 ask_5m_cap: float = ASK_5M_CAP,
                 ask_15m_cap: float = ASK_15M_CAP,
                 max_pair_cost: float = MAX_PAIR_COST,
                 entry_window_sec: float = ENTRY_WINDOW_SEC,
                 shares_per_leg: float = SHARES_PER_LEG,
                 require_binned_fair: bool = True,
                 min_edge_vs_binned_fair: float = MIN_EDGE_VS_BINNED_FAIR):
        self.lead_bps_min = lead_bps_min
        self.lead_bps_max = lead_bps_max
        self.ask_5m_cap = ask_5m_cap
        self.ask_15m_cap = ask_15m_cap
        self.max_pair_cost = max_pair_cost
        self.entry_window_sec = entry_window_sec
        self.shares_per_leg = shares_per_leg
        #: False lets a pair clear at up to `max_pair_cost` even when that is
        #: above the binned fair value. Sensitivity runs only - see the
        #: docstring for why the flat 0.413 blend is 8.4c too generous at a
        #: 6bps lead.
        self.require_binned_fair = require_binned_fair
        #: D-281. Required cents of edge below binned fair value, 8c. 0.0
        #: restores the old zero-edge-allowed behaviour and exists only so a
        #: sensitivity run can measure what the floor costs. Not a mode to
        #: trade in.
        self.min_edge_vs_binned_fair = min_edge_vs_binned_fair

    # -- reference prices ---------------------------------------------------

    @staticmethod
    def parent_15m_ts(window_ts: int) -> int:
        """Open second of the 15m window containing this 5m window."""
        return (window_ts // WINDOW_15M_SEC) * WINDOW_15M_SEC

    @staticmethod
    def open_at(ctx: MarketContext, ts: int) -> Optional[float]:
        """Open of the price bar that started at `ts`, matched on timestamp.

        Matched, never assumed to be at a known index: a stale or short candle
        pull would otherwise silently hand back a different window's open and
        every lead would be measured from the wrong place. No match is a skip.

        This is a BTC exchange bar open, not the Chainlink settlement TWAP. It
        is used only to measure OUR lead signal; nothing downstream treats it as
        a strike.
        """
        for w in ctx.windows:
            if w.ts == ts:
                return w.open
        return None

    # -- decision -----------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        slug = getattr(ctx.market, 'slug', None)
        slug_15 = getattr(ctx.market_15m, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('market_slug_15m', slug_15)
            feats.setdefault('paper_mode', self.paper_mode)
            feats.setdefault('structure', 'floored_pair_not_relative_value')
            feats.setdefault('implements_proposal_005_hypothesis', False)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None or ctx.market_15m is None:
            return decide('SKIP', 'missing_market_leg',
                          has_5m=ctx.market is not None,
                          has_15m=ctx.market_15m is not None)

        ts15 = self.parent_15m_ts(ctx.window_ts)
        offset = ctx.window_ts - ts15
        if offset != FINAL_THIRD_OFFSET_SEC:
            # Structural, not a preference. Only the FINAL third settles off the
            # same close as its 15m parent, and only then is the pair floored at
            # $1.00. Pairing an earlier third would let both legs lose.
            return decide('SKIP', 'not_final_third_of_15m',
                          parent_15m_ts=ts15, offset_sec=offset,
                          required_offset_sec=FINAL_THIRD_OFFSET_SEC)

        if ctx.spot is None:
            return decide('SKIP', 'no_spot', parent_15m_ts=ts15)

        open_15m = self.open_at(ctx, ts15)
        if open_15m is None:
            return decide('SKIP', 'no_15m_window_open', parent_15m_ts=ts15,
                          windows_available=len(ctx.windows))
        if open_15m <= 0:
            return decide('SKIP', 'invalid_15m_window_open',
                          open_15m=open_15m)

        lead_bps = ((ctx.spot - open_15m) / open_15m) * 10_000.0
        abs_lead = abs(lead_bps)
        lead_side = 'Up' if lead_bps >= 0 else 'Down'
        opp_side = opposite(lead_side)
        p_corridor = p_corridor_lookup(abs_lead)

        feats = {
            'parent_15m_ts': ts15,
            'open_15m': round(open_15m, 2),
            'open_15m_source': 'btc_price_bar_open_not_settlement_strike',
            'spot': round(ctx.spot, 2),
            'lead_bps': round(lead_bps, 3),
            'abs_lead_bps': round(abs_lead, 3),
            'lead_side_15m': lead_side,
            'opposite_side_5m': opp_side,
            'p_corridor': p_corridor,
            'p_corridor_source': 'moondevonyt_binned_table',
            'p_corridor_blended_reference': P_CORRIDOR_BLENDED,
            'p_corridor_is_unverified_vendor_number': True,
            'binned_fair_pair_value': round(1.0 + p_corridor, 4),
            'payoff_floor': 1.00,
            'floor_is_structural_not_empirical': True,
            'confidence': p_corridor,
        }

        remaining = ctx.seconds_remaining
        feats['seconds_into_window'] = ctx.seconds_into_window
        if (ctx.seconds_into_window is not None
                and ctx.seconds_into_window > self.entry_window_sec):
            # Late in the final third there is not enough path left for the
            # corridor to be reached, and the leader leg is already priced for
            # the outcome.
            return decide('SKIP', 'late_in_window', **feats)
        feats['seconds_remaining'] = (None if remaining is None
                                      else round(remaining, 1))

        if abs_lead < self.lead_bps_min:
            return decide('SKIP', 'lead_below_zone', **feats)
        if abs_lead > self.lead_bps_max:
            # Too far ahead: the corridor needs the close to land BETWEEN P0
            # and P10, and a big lead puts that band out of reach.
            return decide('SKIP', 'lead_above_zone', **feats)

        book_15 = ctx.book_15m(lead_side)
        book_5 = ctx.book(opp_side)
        if book_15 is None or book_5 is None:
            return decide('SKIP', 'no_orderbook',
                          has_book_15m=book_15 is not None,
                          has_book_5m=book_5 is not None, **feats)

        ask_15 = book_15.best_ask
        ask_5 = book_5.best_ask
        feats.update({'ask_15m_leader': ask_15, 'ask_5m_opposite': ask_5})
        if ask_15 is None or ask_5 is None:
            return decide('SKIP', 'no_asks', **feats)
        if ask_5 > self.ask_5m_cap:
            return decide('SKIP', 'ask_5m_above_cap',
                          ask_5m_cap=self.ask_5m_cap, **feats)
        if ask_15 > self.ask_15m_cap:
            return decide('SKIP', 'ask_15m_above_cap',
                          ask_15m_cap=self.ask_15m_cap, **feats)

        # Depth on BOTH legs before committing to either. A pair that can only
        # be half-filled is a naked directional position, which is the one thing
        # this structure exists to avoid.
        depth_15 = book_15.ask_depth(self.ask_15m_cap)
        depth_5 = book_5.ask_depth(self.ask_5m_cap)
        feats.update({'depth_15m': depth_15, 'depth_5m': depth_5,
                      'shares_per_leg': self.shares_per_leg})
        if depth_15 < self.shares_per_leg or depth_5 < self.shares_per_leg:
            return decide('SKIP', 'insufficient_depth_for_pair', **feats)

        # Both caps are judged on the BOOK-WALKED average for the full block,
        # never on top-of-book. On a binary the entry price is the edge.
        eff_15 = effective_ask_for(book_15, self.shares_per_leg,
                                   self.ask_15m_cap)
        eff_5 = effective_ask_for(book_5, self.shares_per_leg, self.ask_5m_cap)
        feats['effective_ask_15m'] = None if eff_15 is None else round(eff_15, 4)
        feats['effective_ask_5m'] = None if eff_5 is None else round(eff_5, 4)
        if eff_15 is None or eff_5 is None:
            return decide('SKIP', 'pair_unfillable_at_caps', **feats)

        pair_cost = round(eff_15 + eff_5, 4)
        binned_fair = round(1.0 + p_corridor, 4)
        feats.update({
            'pair_cost': pair_cost,
            'max_pair_cost': self.max_pair_cost,
            'edge_vs_blended_fair': round(1.0 + P_CORRIDOR_BLENDED - pair_cost, 4),
            'edge_vs_binned_fair': round(binned_fair - pair_cost, 4),
            'worst_case_pnl_per_pair': round(1.00 - pair_cost, 4),
            'best_case_pnl_per_pair': round(2.00 - pair_cost, 4),
            'require_binned_fair': self.require_binned_fair,
            'min_edge_vs_binned_fair': self.min_edge_vs_binned_fair,
            'max_pair_cost_binned': round(
                binned_fair - self.min_edge_vs_binned_fair, 4),
        })

        if pair_cost > self.max_pair_cost:
            feats['pair_cap_binding'] = 'max_pair_cost'
            return decide('SKIP', 'pair_cost_above_cap', **feats)
        if self.require_binned_fair and pair_cost > binned_fair:
            # Inside the brief's 1.41 ceiling and still ABOVE what the structure
            # is worth at THIS lead. Negative expectancy dressed as a rule being
            # followed - the 1.41 cap is a blend and it is 8.4c too generous at
            # a 6bps lead.
            feats['pair_cap_binding'] = 'binned_fair_pair_value'
            return decide('SKIP', 'pair_cost_above_binned_fair', **feats)
        if (self.require_binned_fair
                and pair_cost > feats['max_pair_cost_binned']):
            # Below binned fair, but not by enough to survive fees. D-281 sets
            # the floor at 8c. Named separately from the gate above so "inside
            # fair value but too thin" is never pooled with "above fair value".
            # Compared against the ROUNDED threshold: the unrounded subtraction
            # leaves float dust that would refuse a pair at exactly 8c.
            feats['pair_cap_binding'] = 'edge_floor_8c'
            return decide('SKIP', 'edge_below_floor', **feats)
        feats['pair_cap_binding'] = None

        # Leader first. If the second leg fails, the naked leg we are left
        # holding is the side that is currently winning.
        legs = [
            Leg(lead_side, self.ask_15m_cap, order_type='taker',
                market_slug=slug_15, shares=self.shares_per_leg,
                expected_price=eff_15),
            Leg(opp_side, self.ask_5m_cap, order_type='taker',
                market_slug=slug, shares=self.shares_per_leg,
                expected_price=eff_5),
        ]
        return decide('ENTER', '', legs=legs, **feats)
