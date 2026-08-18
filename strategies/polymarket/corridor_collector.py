"""Corridor Collector: pair a 15m leader with the final 5m opposite.

Ported from moondevonyt's `corridor_collector.py`. Thresholds preserved; the
wallet client and live order path are gone.

THE STRUCTURE. A 15-minute window [T, T+900] contains the 5-minute window
[T+600, T+900] as its final third, and BOTH settle off the same close, P15.
Write P0 for the 15m open and P10 for the 5m open. Buy the 15m LEADER and the
5m OPPOSITE and there is no outcome where both legs lose:

    P15 beyond P10   (the lead keeps running)  -> $1   (the 15m leg pays)
    P0 < P15 < P10   (THE CORRIDOR)            -> $2   (BOTH pay, the payday)
    P15 beyond P0    (a full reversal)         -> $1   (the 5m leg saves you)

Fair value of the pair is therefore 1 + P(corridor). Pay less than that and the
structure is positive-expectancy. The $1.00 floor is the stop - there is no
other stop and none is needed, which is the one place a prediction market is
genuinely easier than a price market.

P(CORRIDOR) IS A TABLE, NOT A CONSTANT. His 41.3% is the blended in-zone
average he quotes in the README. The bot itself looks P(corridor) up by lead
size, and inside the 5-30 bps zone the table reads 0.326 / 0.405 / 0.440 /
0.464 - it never reads 0.413 anywhere. Since the price gate is
`ask15 + ask5 <= 1 + p_corridor - 0.08`, a flat 0.413 (which an earlier version
of this file used) is 8.7c too generous at a 6 bps lead and 5.1c too strict at
a 25 bps lead. Same signal, wrong price gate, and the price is what kills you.

HIS NUMBERS, NOT EVIDENCE. The floor held in 34,918 of 34,918 windows.

READ THAT LAST CLAIM CAREFULLY. "Zero failures in 34,918" is a structural
identity, not an empirical finding: given the two markets settle off the same
close, both legs losing is arithmetically impossible. The backtest confirming
it 34,918 times is a wiring check, not evidence of edge. The EDGE is entirely
in whether P(corridor) is really what the table says and whether you can buy
the pair below 1 + that. The floor never fails; the PRICE is what kills you,
which is why the 8c edge requirement and the two ask caps exist.

UNITS, AND A REAL PORT RISK. His zone gate divides USD by USD: lead in dollars
over ATR14 in dollars. Ours divides `ctx.lead_bps` by `ctx.atr14`, so ATR14
must ALSO be in basis points. Nothing in `engine/polymarket/context.py`
computes or checks that - `atr14` is passed straight through from the caller -
so a USD ATR here yields a ratio ~10,000x too small and `lead_inside_noise`
swallows every window while the strategy looks alive. Second risk in the same
place: context.py's `lead_bps` is (spot - strike)/strike, the 5m ITM distance,
NOT the 15m P10-vs-P0 lead this strategy means. Both are engine-side and are
flagged rather than patched here.

REAL EXECUTION RISK: a naked leg. Both legs go in as marketable GTCs back to
back, and if the second fails you are directional, not hedged. Retry once,
never at a worse price, then flatten and flag UNPAIRED.

KILL CONDITIONS: dies if realized P(corridor) in the zone comes in under 25%
(the pair price stops clearing), if the unpaired rate exceeds 5% of attempts,
or if the resolution-PnL harness scores it under 30bps net edge on our own data
(convention 5, D-268).
"""
from typing import Optional

from strategies.polymarket.base import (Decision, Leg, MarketContext,
                                        PolymarketStrategy, opposite)

# Never False in this repo. moondevonyt ships this False ("LIVE FIRE").
PAPER_MODE = True

# moondevonyt's constants, unchanged.
LEAD_BPS_MIN = 5.0        # below this there is no lead to speak of
LEAD_BPS_MAX = 30.0       # above this the corridor stops being reachable
LEAD_ATR_MIN = 1.0        # the lead must be large relative to recent range
EDGE_REQUIRED = 0.08      # pay at least 8c below fair value for the pair
ASK_5M_CAP = 0.55         # sanity cap on the 5m opposite leg
ASK_15M_CAP = 0.93        # sanity cap on the 15m leader leg
ENTRY_WINDOW_SEC = 90     # first 90s of the final 5m market
SHARES_PER_LEG = 5        # equal shares, Polymarket minimum

# His P(corridor) table, keyed by absolute lead in bps. 52 weeks of 1-min
# candles, his measurement. (lead_lo, lead_hi, p_corridor), lo inclusive.
P_CORRIDOR_BINS = (
    (0.0, 2.0, 0.072),
    (2.0, 5.0, 0.219),
    (5.0, 10.0, 0.326),
    (10.0, 15.0, 0.405),
    (15.0, 20.0, 0.440),
    (20.0, 30.0, 0.464),
    (30.0, 50.0, 0.497),
)

#: The blended in-zone figure he quotes in prose. Reported for comparison only;
#: the price gate uses the table.
P_CORRIDOR_BLENDED = 0.413


def p_corridor_lookup(lead_bps: float,
                      bins=P_CORRIDOR_BINS) -> float:
    """His table lookup. Leads at or above the last bin use the last bin."""
    lead = abs(float(lead_bps))
    for lo, hi, p in bins:
        if lo <= lead < hi:
            return p
    return bins[-1][2]


class CorridorCollector(PolymarketStrategy):
    """Buy the 15m leader plus the final-5m opposite. Floor is $1.00."""

    strategy_name = 'PM_corridor_collector'
    paper_mode = PAPER_MODE

    def __init__(self, lead_bps_min: float = LEAD_BPS_MIN,
                 lead_bps_max: float = LEAD_BPS_MAX,
                 lead_atr_min: float = LEAD_ATR_MIN,
                 p_corridor: Optional[float] = None,
                 edge_required: float = EDGE_REQUIRED,
                 ask_5m_cap: float = ASK_5M_CAP,
                 ask_15m_cap: float = ASK_15M_CAP,
                 entry_window_sec: int = ENTRY_WINDOW_SEC,
                 shares_per_leg: float = SHARES_PER_LEG):
        self.lead_bps_min = lead_bps_min
        self.lead_bps_max = lead_bps_max
        self.lead_atr_min = lead_atr_min
        #: None = use his lead-size table. A float pins every window to one
        #: probability, which is only ever right for a sensitivity sweep.
        self.p_corridor = p_corridor
        self.edge_required = edge_required
        self.ask_5m_cap = ask_5m_cap
        self.ask_15m_cap = ask_15m_cap
        self.entry_window_sec = entry_window_sec
        self.shares_per_leg = shares_per_leg

    def corridor_probability(self, lead_bps: float) -> float:
        if self.p_corridor is not None:
            return self.p_corridor
        return p_corridor_lookup(lead_bps)

    def evaluate(self, ctx: MarketContext) -> Decision:
        slug = getattr(ctx.market, 'slug', None)
        slug_15 = getattr(ctx.market_15m, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('market_slug_15m', slug_15)
            feats.setdefault('paper_mode', self.paper_mode)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None or ctx.market_15m is None:
            return decide('SKIP', 'missing_market_leg',
                          has_5m=ctx.market is not None,
                          has_15m=ctx.market_15m is not None)

        if ctx.lead_bps is None or ctx.atr14 is None:
            return decide('SKIP', 'no_lead_or_atr')

        lead_bps = ctx.lead_bps
        lead_side = 'Up' if lead_bps >= 0 else 'Down'
        abs_lead = abs(lead_bps)
        lead_atr_ratio = (abs_lead / ctx.atr14) if ctx.atr14 > 0 else 0.0
        p_corridor = self.corridor_probability(abs_lead)

        feats = {
            'lead_bps': round(lead_bps, 3),
            'abs_lead_bps': round(abs_lead, 3),
            'atr14_bps': round(ctx.atr14, 3),
            'lead_atr_ratio': round(lead_atr_ratio, 3),
            'lead_side_15m': lead_side,
            'opposite_side_5m': opposite(lead_side),
            'p_corridor': p_corridor,
            'p_corridor_source': ('table' if self.p_corridor is None
                                  else 'pinned'),
            'p_corridor_blended_reference': P_CORRIDOR_BLENDED,
            'p_corridor_is_unverified_vendor_number': True,
            'fair_pair_value': round(1.0 + p_corridor, 4),
            'payoff_floor': 1.00,
            'floor_is_structural_not_empirical': True,
            'confidence': p_corridor,
        }

        if ctx.seconds_into_window is not None \
                and ctx.seconds_into_window > self.entry_window_sec:
            return decide('SKIP', 'late_in_window',
                          seconds_into_window=ctx.seconds_into_window, **feats)

        if ctx.atr14 <= 0:
            return decide('SKIP', 'zero_atr_undefined_ratio', **feats)
        if abs_lead < self.lead_bps_min:
            return decide('SKIP', 'lead_below_zone', **feats)
        if abs_lead > self.lead_bps_max:
            # Too far ahead: the corridor requires the close to land BETWEEN P0
            # and P10, and a big lead makes that band unreachable.
            return decide('SKIP', 'lead_above_zone', **feats)
        if lead_atr_ratio < self.lead_atr_min:
            # The lead is inside the noise. Not a lead.
            return decide('SKIP', 'lead_inside_noise', **feats)

        opp_side = opposite(lead_side)
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
            return decide('SKIP', 'ask_5m_above_cap', **feats)
        if ask_15 > self.ask_15m_cap:
            return decide('SKIP', 'ask_15m_above_cap', **feats)

        # Depth on BOTH legs before committing to either. A pair you can only
        # half-fill is a directional position, which is the one thing this
        # structure exists to avoid. His bot has no depth check and discovers
        # this as an UNPAIRED leg after the fact; a backtest cannot afford to.
        depth_15 = book_15.ask_depth(self.ask_15m_cap)
        depth_5 = book_5.ask_depth(self.ask_5m_cap)
        feats.update({'depth_15m': depth_15, 'depth_5m': depth_5,
                      'shares_per_leg': self.shares_per_leg})
        if depth_15 < self.shares_per_leg or depth_5 < self.shares_per_leg:
            return decide('SKIP', 'insufficient_depth_for_pair', **feats)

        pair_cost = ask_15 + ask_5
        max_pair_cost = 1.0 + p_corridor - self.edge_required
        feats.update({
            'pair_cost': round(pair_cost, 4),
            'max_pair_cost': round(max_pair_cost, 4),
            'edge_vs_fair': round(1.0 + p_corridor - pair_cost, 4),
            'worst_case_pnl_per_pair': round(1.00 - pair_cost, 4),
        })

        if pair_cost > max_pair_cost:
            return decide('SKIP', 'pair_cost_above_edge_threshold', **feats)

        # Legs are ordered leader-first. Fill that one first: if the second
        # fails you are left holding the side that is currently winning, which
        # is the less bad naked leg to be stuck with.
        #
        # limit_price is the cap (the marketable GTC's limit); expected_price
        # is the ask we would actually pay, which is what D-268 calls the
        # per-share premium and what `Signal.entry` reports.
        legs = [
            Leg(lead_side, self.ask_15m_cap, order_type='taker',
                market_slug=slug_15, shares=self.shares_per_leg,
                expected_price=ask_15),
            Leg(opp_side, self.ask_5m_cap, order_type='taker',
                market_slug=slug, shares=self.shares_per_leg,
                expected_price=ask_5),
        ]
        return decide('ENTER', '', legs=legs, **feats)
