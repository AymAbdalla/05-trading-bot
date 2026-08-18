"""Resolution-PnL harness for Polymarket binary outcome markets (D-267 / 3D).

WHY THIS FILE EXISTS AND WHAT IT REFUSES TO DO
----------------------------------------------
The vectorized harness scores a PATH. It walks bars, applies a stop, and
computes profit factor from the distance between entry and stop. A Polymarket
position has no path worth scoring: you buy a share for `p` dollars, you hold
it to settlement, and it redeems for exactly $1.00 or exactly $0.00.

Running a Polymarket strategy through the price-path harness would score a
binary's payoff against BTC's price series - a different instrument. That is
the exact shape of the pre-purge FUTURES rows, which had to be deleted. So
until this file existed, all four Polymarket strategies were NOT_TESTED
(D-268), and they still are until somebody actually runs them through it with
real resolved markets.

THE PAYOFF, IN FULL
-------------------
    win:   pnl = (1.00 - entry) * shares - costs
    loss:  pnl = (0.00 - entry) * shares - costs = -entry * shares - costs

Both ends are bounded. That single fact is what breaks every path metric:

  - PROFIT FACTOR built on stop distance is meaningless: the "stop" is 0.00
    for every trade, so the ratio degenerates to a function of entry price
    rather than of the strategy. A binary profit factor CAN be defined (see
    `profit_factor_binary` below) and this harness reports it under that name,
    with the definition attached, so nobody reads it as the same number the
    graveyard reports.
  - R-MULTIPLE is not computed. R = risk unit = entry - stop = entry. So
    "R-multiple" would just be pnl / (entry * shares), which is the return on
    capital this harness already reports under its correct name. Reporting it
    twice under a path-metric name would invite pooling with graveyard rows
    that mean something else.
  - MAE / MFE are not computed. They require an intra-trade price path. The
    strategies here hold to resolution and never act on the interim mark, so
    an excursion statistic would describe drawdown nobody could have traded
    and nobody would have reacted to.

Those three exclusions are enumerated in every report under
`metrics_not_computed`, with the reason, so their absence is a recorded fact
rather than an oversight (convention 20: a silent skip is a missing number).

WHY 30bps IS A STRANGE THRESHOLD ON THIS INSTRUMENT (read before citing one)
---------------------------------------------------------------------------
D-268's kill condition is 30bps net edge. Convention 5 sets that floor. On a
binary the per-trade return on capital is roughly +100% or -100% at a 50c
entry, so the standard deviation of per-trade return is close to 1.0. A 30bps
mean against a 1.0 standard deviation needs about (2 / 0.003)^2 ~ 444,000
trades for a 2-sigma read. At 288 five-minute windows a day that is over a
thousand years.

The threshold is not wrong, it is just very easy to clear and impossible to
confirm at that magnitude. A REAL Polymarket edge is a calibration
disagreement measured in percentage points: being right 54% of the time on
50c shares is a 400bps edge, and that needs about 2,500 trades. So this
harness gates on THREE things, not one, and reports the sample size each
observed effect would actually need:

    1. net_edge_bps >= 30           (D-268 / convention 5, the kill floor)
    2. n_trades    >= MIN_RESOLVED_TRADES  (convention 7, the shrug floor)
    3. t_stat      >= 2.0           (the edge is distinguishable from noise)

`trades_needed_for_2sigma` is reported on every run so an underpowered PASS
can never look like a confirmed one.

VERDICT VOCABULARY (convention 11, unchanged)
---------------------------------------------
NOT_TESTED means the harness COULD NOT RUN. Here that is: no entries, no
market data, every market unresolved, or the capital cap cannot buy the venue
minimum lot. It never means "ran and found nothing".

FAIL means it ran and did not clear the gates. A FAIL on 40 trades carries
`underpowered: true` and is a shrug, exactly as convention 7 describes.

PASS means it cleared all three gates. It is still a backtest.
"""
import bisect
import json
import logging
import math
import os
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from backtest.cost_model import CostModel, PM_COST_MODEL_VERSION
from backtest.instruments import (BINARY_LOSS_PAYOFF, BINARY_WIN_PAYOFF,
                                  POLYMARKET_MIN_SHARES, binary_pnl,
                                  is_valid_binary_price, spec_for)

logger = logging.getLogger(__name__)

ASSET_CLASS = 'PREDICTION_MARKET'

# Bump when a gate threshold or a metric definition below changes. Stamped on
# every report so two runs under different rules can never be pooled.
PM_GATE_VERSION = 'pm-gate-2026-08-17'

# --- the three PASS gates --------------------------------------------------
# Convention 5 / D-268. Under this, the idea is dead on arrival.
MIN_NET_EDGE_BPS = 30.0
# Convention 7. A PASS on a tiny sample is a shrug, so it is not a PASS. 200
# is a floor on anecdote, NOT a power calculation - the power calculation is
# per-effect-size and is reported as `trades_needed_for_2sigma`. At a 400bps
# edge (54% win rate on 50c shares) the real requirement is ~2,500 trades.
MIN_RESOLVED_TRADES = 200
# The observed edge must be distinguishable from noise on its own sample.
MIN_T_STAT = 2.0

INF_NOTE = ('null here means a non-finite value (infinity or undefined), not '
            'a missing measurement. A cost RATE is legitimately infinite when '
            'an instrument cannot be afforded at the configured capital '
            '(convention 12), and a t-statistic is undefined when every trade '
            'returned exactly the same amount. Bare Infinity/NaN tokens are '
            'not valid JSON, so they are written as null and named in '
            'non_finite_fields.')


# ===========================================================================
# DATA SHAPES
# ===========================================================================

@dataclass(frozen=True)
class ResolvedMarket:
    """One historical Polymarket market, resolved or not.

    `winning_outcome` is None for a market that has NOT settled. That is a
    deliberate three-state read (won / lost / unknown) rather than a boolean:
    a market quoting 0.99 has not resolved, and booking it as a win is how a
    backtest quietly collects settlements that never happened.

    `prices` is an optional (unix_seconds, price) series for `price_outcome`,
    as returned by engine.polymarket.prices.price_history. It is used only to
    price an entry, never to score one - the score comes from resolution.
    """

    market_id: str
    winning_outcome: Optional[str] = None
    outcomes: Tuple[str, ...] = ()
    condition_id: Optional[str] = None
    resolved_ts: Optional[int] = None
    prices: Tuple[Tuple[int, float], ...] = ()
    price_outcome: Optional[str] = None

    @property
    def is_resolved(self) -> bool:
        return self.winning_outcome is not None

    def knows_side(self, side: str) -> bool:
        """Is `side` an outcome this market actually has?

        With no declared outcome list we cannot tell, so we say yes and let
        `won()` compare against the winner. With a list, an unrecognised side
        is a data problem and must not be silently scored as a loss.
        """
        if not self.outcomes:
            return True
        target = (side or '').strip().lower()
        return any(o.strip().lower() == target for o in self.outcomes)

    def won(self, side: str) -> Optional[bool]:
        """True/False for a resolved market, None when it cannot be decided."""
        if not self.is_resolved:
            return None
        if not self.knows_side(side):
            return None
        return (side or '').strip().lower() == self.winning_outcome.strip().lower()

    def price_at(self, ts: Optional[int],
                 max_staleness_s: Optional[int] = None) -> Optional[float]:
        """Last quoted price at or BEFORE `ts`. Never looks ahead.

        Returns None when there is no prior point, or when the most recent one
        is older than `max_staleness_s`. A stale quote on a five-minute market
        is not a price you could have traded, and pretending otherwise is the
        cheapest way to manufacture edge.
        """
        if ts is None or not self.prices:
            return None
        times = [t for t, _ in self.prices]
        idx = bisect.bisect_right(times, int(ts)) - 1
        if idx < 0:
            return None
        t, p = self.prices[idx]
        if max_staleness_s is not None and (int(ts) - t) > max_staleness_s:
            return None
        return float(p)


@dataclass(frozen=True)
class Entry:
    """One intended purchase of one outcome in one market.

    `entry_price` is the per-share premium ACTUALLY PAID. If it came from
    walking a real orderbook (engine.polymarket.orderbook.walk_book) then the
    spread and the depth walk are already inside it, and `fill_is_walked` must
    be True so the cost model does not charge them a second time.
    """

    market_id: str
    outcome_side: str
    entry_price: float
    shares: float
    strategy: str = ''
    entry_ts: Optional[int] = None
    order_type: str = 'taker'
    fill_is_walked: bool = False
    features: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredTrade:
    """An Entry joined to a resolution, with the full binary payoff applied."""

    entry: Entry
    won: bool
    gross_pnl_usdc: float
    cost_usdc: float
    net_pnl_usdc: float
    capital_usdc: float          # premium + costs = cash actually committed

    @property
    def return_on_capital(self) -> float:
        return self.net_pnl_usdc / self.capital_usdc if self.capital_usdc > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            'market_id': self.entry.market_id,
            'strategy': self.entry.strategy,
            'outcome_side': self.entry.outcome_side,
            'entry_price': round(self.entry.entry_price, 6),
            'shares': self.entry.shares,
            'entry_ts': self.entry.entry_ts,
            'fill_is_walked': self.entry.fill_is_walked,
            'won': self.won,
            'gross_pnl_usdc': round(self.gross_pnl_usdc, 6),
            'cost_usdc': round(self.cost_usdc, 6),
            'net_pnl_usdc': round(self.net_pnl_usdc, 6),
            'capital_usdc': round(self.capital_usdc, 6),
            'return_on_capital': round(self.return_on_capital, 6),
        }


# Every reason an Entry can fail to become a ScoredTrade. Each one is counted
# separately: two different silent drops reported as one number is exactly the
# bug convention 20 was written for.
DROP_REASONS = (
    'unknown_market',        # no market record with this id
    'market_unresolved',     # market exists but has not settled
    'unknown_outcome_side',  # side is not one of the market's outcomes
    'invalid_entry_price',   # not strictly inside (0.00, 1.00)
    'below_min_lot',         # fewer shares than the venue will accept
    'non_positive_shares',
)


# ===========================================================================
# HARNESS
# ===========================================================================

class PolymarketHarness:
    """Scores prediction-market strategies on RESOLUTION PnL.

    Deliberately has no data-fetching code inside `score`. Markets and entries
    are handed in. That makes an offline run reproducible from a logged
    fixture, and it makes the missing-data case impossible to confuse with a
    zero-edge case: no markets means NOT_TESTED, always, and there is no code
    path that can invent one.
    """

    def __init__(self, cost_model: Optional[CostModel] = None,
                 notional_cap_usdc: float = 10.0,
                 min_trades: int = MIN_RESOLVED_TRADES,
                 min_net_edge_bps: float = MIN_NET_EDGE_BPS,
                 min_t_stat: float = MIN_T_STAT,
                 min_shares: float = POLYMARKET_MIN_SHARES):
        self.cost_model = cost_model or CostModel()
        self.notional_cap_usdc = float(notional_cap_usdc)
        self.min_trades = int(min_trades)
        self.min_net_edge_bps = float(min_net_edge_bps)
        self.min_t_stat = float(min_t_stat)
        self.min_shares = float(min_shares)
        self.spec = spec_for('POLYMARKET', ASSET_CLASS)

    # -- sizing ---------------------------------------------------------

    def shares_for(self, price: float,
                   notional_cap: Optional[float] = None) -> float:
        """Whole shares the cap affords at this premium. 0 is a real answer."""
        cap = self.notional_cap_usdc if notional_cap is None else float(notional_cap)
        return self.spec.size_for(cap, price)

    def can_size_at(self, price: float) -> bool:
        return self.shares_for(price) > 0

    # -- per-trade cost --------------------------------------------------

    def entry_cost(self, shares: float, price: float,
                   fill_is_walked: bool = False) -> float:
        """Dollar cost of the single taker leg. There is no exit leg.

        A held-to-resolution binary redeems on-chain; the venue charges
        nothing for that, and the gas that IS charged is live-only and is not
        modelled here (CostModel.PM_CHARGE_GAS is False by design).
        """
        return self.cost_model.prediction_market_leg(
            shares, price, fill_is_walked=fill_is_walked).total

    # -- scoring ---------------------------------------------------------

    def score(self, entries: Sequence[Entry],
              markets: Sequence[ResolvedMarket],
              strategy: str = '',
              label: str = '',
              data_available: bool = True,
              not_tested_reason: Optional[str] = None) -> dict:
        """Score one strategy's entries against resolved markets.

        `data_available=False` (or a non-empty `not_tested_reason`) short-
        circuits straight to NOT_TESTED. That is the hook for the offline
        case: a caller that could not fetch history says so, and no number is
        produced at all.
        """
        base = {
            'strategy': strategy or (entries[0].strategy if entries else ''),
            'label': label,
            'asset_class': ASSET_CLASS,
            'gate_version': PM_GATE_VERSION,
            'cost_model_version': PM_COST_MODEL_VERSION,
            'notional_cap_usdc': self.notional_cap_usdc,
            'scoring': 'resolution_pnl',
            'gates': {
                'min_net_edge_bps': self.min_net_edge_bps,
                'min_trades': self.min_trades,
                'min_t_stat': self.min_t_stat,
            },
        }

        if not_tested_reason:
            return self._not_tested(base, not_tested_reason)
        if not data_available:
            return self._not_tested(base, 'market_data_unavailable')
        if not markets:
            return self._not_tested(base, 'no_market_data')
        if not entries:
            # No entries is genuinely ambiguous, so it is reported as its own
            # reason rather than folded into "no data": a strategy that
            # evaluated every window and chose to enter none DID run, but it
            # produced nothing this harness can score either way.
            return self._not_tested(base, 'no_entries')

        by_id: Dict[str, ResolvedMarket] = {m.market_id: m for m in markets}
        drops = {r: 0 for r in DROP_REASONS}
        scored: List[ScoredTrade] = []

        for e in entries:
            m = by_id.get(e.market_id)
            if m is None:
                drops['unknown_market'] += 1
                continue
            if not m.is_resolved:
                drops['market_unresolved'] += 1
                continue
            if not is_valid_binary_price(e.entry_price):
                drops['invalid_entry_price'] += 1
                continue
            if e.shares <= 0:
                drops['non_positive_shares'] += 1
                continue
            if e.shares < self.min_shares:
                drops['below_min_lot'] += 1
                continue
            won = m.won(e.outcome_side)
            if won is None:
                drops['unknown_outcome_side'] += 1
                continue

            gross = binary_pnl(e.entry_price, e.shares, won)
            cost = self.entry_cost(e.entry_price, e.shares) if False else \
                self.entry_cost(e.shares, e.entry_price, e.fill_is_walked)
            premium = e.entry_price * e.shares
            scored.append(ScoredTrade(
                entry=e, won=won, gross_pnl_usdc=gross, cost_usdc=cost,
                net_pnl_usdc=gross - cost, capital_usdc=premium + cost))

        # Accounting identity, asserted rather than assumed (convention 20).
        dropped = sum(drops.values())
        if len(entries) - dropped != len(scored):
            raise AssertionError(
                f'entry accounting broken: in={len(entries)} '
                f'dropped={dropped} scored={len(scored)}')

        base['entries_in'] = len(entries)
        base['dropped'] = drops
        base['dropped_total'] = dropped
        base['markets_supplied'] = len(markets)
        base['markets_resolved'] = sum(1 for m in markets if m.is_resolved)

        if not scored:
            # Nothing survived. Name the dominant reason so a reader can tell
            # "no market ever settled" (could not run) apart from "the sizing
            # rejected every entry" (also could not run) apart from "the data
            # was mislabelled".
            reason = max(drops, key=lambda k: drops[k]) if dropped else 'no_entries'
            return self._not_tested(base, f'no_scorable_trades:{reason}')

        return self._report(base, scored)

    # -- report ----------------------------------------------------------

    def _not_tested(self, base: dict, reason: str) -> dict:
        out = dict(base)
        out.update({
            'verdict': 'NOT_TESTED',
            'not_tested_reason': reason,
            'trades': 0,
            'note': ('NOT_TESTED means the harness could not run this, never '
                     '"it ran and found nothing" (convention 11).'),
        })
        return out

    def _report(self, base: dict, scored: List[ScoredTrade]) -> dict:
        n = len(scored)
        wins = sum(1 for t in scored if t.won)
        losses = n - wins
        win_rate = wins / n

        shares_total = sum(t.entry.shares for t in scored)
        premium_total = sum(t.entry.entry_price * t.entry.shares for t in scored)
        gross = sum(t.gross_pnl_usdc for t in scored)
        cost = sum(t.cost_usdc for t in scored)
        net = gross - cost
        capital = sum(t.capital_usdc for t in scored)

        # Share-weighted average premium. The simple mean is also reported
        # because a strategy that buys 5 shares at 90c and 500 at 10c has two
        # very different "average entries" and reading the wrong one inverts
        # the breakeven conclusion.
        avg_entry_weighted = premium_total / shares_total if shares_total else 0.0
        avg_entry_simple = statistics.fmean(t.entry.entry_price for t in scored)
        cost_per_share = cost / shares_total if shares_total else 0.0
        breakeven = avg_entry_weighted + cost_per_share

        rets = [t.return_on_capital for t in scored]
        mean_ret = statistics.fmean(rets)
        std_ret = statistics.stdev(rets) if n >= 2 else 0.0
        if n >= 2 and std_ret > 0:
            t_stat = mean_ret / (std_ret / math.sqrt(n))
        elif mean_ret == 0:
            t_stat = 0.0
        else:
            # Zero dispersion with a non-zero mean. Mathematically infinite,
            # and a real answer for a degenerate sample - not a bug to zero.
            t_stat = math.inf if mean_ret > 0 else -math.inf

        # Sample this observed effect would need for a 2-sigma read. Reported
        # on every run so an underpowered PASS is visibly underpowered.
        if mean_ret > 0 and std_ret > 0:
            needed = math.ceil((self.min_t_stat * std_ret / mean_ret) ** 2)
        elif mean_ret > 0:
            needed = 1
        else:
            needed = math.inf     # a non-positive edge never becomes significant

        win_pnl = sum(t.net_pnl_usdc for t in scored if t.net_pnl_usdc > 0)
        loss_pnl = sum(-t.net_pnl_usdc for t in scored if t.net_pnl_usdc < 0)
        pf_binary = (win_pnl / loss_pnl) if loss_pnl > 0 else math.inf

        net_edge_bps = (net / capital * 10_000) if capital > 0 else -math.inf
        gross_edge_bps = (gross / capital * 10_000) if capital > 0 else -math.inf
        cost_bps = (cost / capital * 10_000) if capital > 0 else math.inf

        underpowered = n < self.min_trades or t_stat < self.min_t_stat
        passed = (n >= self.min_trades
                  and net_edge_bps >= self.min_net_edge_bps
                  and t_stat >= self.min_t_stat)

        failed_gates = []
        if n < self.min_trades:
            failed_gates.append(f'trades {n} < {self.min_trades}')
        if not (net_edge_bps >= self.min_net_edge_bps):
            failed_gates.append(
                f'net_edge_bps {net_edge_bps:.1f} < {self.min_net_edge_bps}')
        if not (t_stat >= self.min_t_stat):
            failed_gates.append(f't_stat {t_stat:.2f} < {self.min_t_stat}')

        out = dict(base)
        out.update({
            'verdict': 'PASS' if passed else 'FAIL',
            'verdict_reason': 'all gates cleared' if passed
                              else '; '.join(failed_gates),
            'underpowered': bool(underpowered),
            'trades': n,
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,

            'avg_entry_price_share_weighted': avg_entry_weighted,
            'avg_entry_price_simple_mean': avg_entry_simple,
            'cost_per_share_usdc': cost_per_share,
            'breakeven_win_rate': breakeven,
            # THE number for this asset class. Win rate minus what the entry
            # price (plus cost) already implies. Positive means the market's
            # probability estimate was wrong in our favour. Entry price and
            # win rate are meaningless read apart (D-267); this is them read
            # together.
            'calibration_gap_pp': (win_rate - breakeven) * 100.0,

            'shares_total': shares_total,
            'premium_paid_usdc': premium_total,
            'capital_deployed_usdc': capital,
            'gross_pnl_usdc': gross,
            'cost_usdc': cost,
            'net_pnl_usdc': net,

            'gross_edge_bps': gross_edge_bps,
            'cost_bps': cost_bps,
            'net_edge_bps': net_edge_bps,
            'ev_per_trade_usdc': net / n,
            'ev_per_share_usdc': (net / shares_total) if shares_total else 0.0,

            'mean_return_on_capital': mean_ret,
            'stdev_return_on_capital': std_ret,
            't_stat': t_stat,
            'trades_needed_for_2sigma': needed,

            'profit_factor_binary': pf_binary,
            'profit_factor_binary_definition': (
                'sum(net pnl of profitable trades) / sum(|net pnl of losing '
                'trades|), computed from RESOLUTION payoffs. This is NOT the '
                'graveyard profit_factor, which is built on stop distance on '
                'a price path. Do not pool the two.'),

            'metrics_not_computed': {
                'profit_factor_path': (
                    'built on stop distance; every binary stop is 0.00, so it '
                    'would measure entry price, not strategy'),
                'r_multiple': (
                    'R = entry - stop = entry, so R-multiple collapses to '
                    'return on capital, which is reported under that name'),
                'mae': 'requires an intra-trade price path; positions are held to resolution',
                'mfe': 'requires an intra-trade price path; positions are held to resolution',
            },
        })
        return out


# ===========================================================================
# BUILDING INPUTS
# ===========================================================================

def markets_from_records(records: Sequence[dict]) -> List[ResolvedMarket]:
    """Build ResolvedMarkets from plain dicts (fixtures, cached JSON, tests).

    A record needs `market_id`. `winning_outcome` absent or None means the
    market has NOT resolved, and the harness will refuse to score it.
    """
    out: List[ResolvedMarket] = []
    for r in records or ():
        prices = tuple(
            (int(p['t']), float(p['p'])) for p in (r.get('prices') or ())
            if 't' in p and 'p' in p)
        out.append(ResolvedMarket(
            market_id=str(r['market_id']),
            winning_outcome=r.get('winning_outcome'),
            outcomes=tuple(r.get('outcomes') or ()),
            condition_id=r.get('condition_id'),
            resolved_ts=r.get('resolved_ts'),
            prices=tuple(sorted(prices)),
            price_outcome=r.get('price_outcome'),
        ))
    return out


def resolved_markets_from_client(client, markets: Sequence,
                                 interval: str = '1m',
                                 fidelity: Optional[int] = None
                                 ) -> Tuple[List[ResolvedMarket], dict]:
    """Fetch price history for already-discovered Gamma markets.

    `markets` are engine.polymarket.types.Market objects (they carry
    `condition_id`, `slug`, `outcomes`, and `resolved_outcome`). The engine
    import is LOCAL to this function on purpose: the backtest layer must stay
    runnable, and testable, with no network and no engine package present.

    Returns (markets, diagnostics). Diagnostics counts every market that could
    not be turned into a scorable record, by reason - an unreadable history is
    not an empty one (convention 11), and the two must not be added together.
    """
    diag = {'requested': len(markets or ()), 'resolved': 0, 'unresolved': 0,
            'history_read_failed': 0, 'history_empty': 0, 'errors': []}
    out: List[ResolvedMarket] = []
    if not markets:
        return out, diag

    try:
        from engine.polymarket.prices import price_history_checked
    except Exception as exc:            # pragma: no cover - import environment
        diag['errors'].append(f'engine unavailable: {exc}')
        return out, diag

    for m in markets:
        winner = getattr(m, 'resolved_outcome', None)
        cid = getattr(m, 'condition_id', None)
        prices: Tuple[Tuple[int, float], ...] = ()
        if cid:
            try:
                res = price_history_checked(client, cid, interval=interval,
                                            fidelity=fidelity)
            except Exception as exc:
                diag['history_read_failed'] += 1
                diag['errors'].append(f'{cid}: {exc}')
                res = {'ok': False, 'points': []}
            if not res.get('ok'):
                diag['history_read_failed'] += 1
            elif not res.get('points'):
                diag['history_empty'] += 1
            else:
                prices = tuple(sorted((int(p['t']), float(p['p']))
                                      for p in res['points']))
        if winner:
            diag['resolved'] += 1
        else:
            diag['unresolved'] += 1
        out.append(ResolvedMarket(
            market_id=str(getattr(m, 'slug', None) or cid or ''),
            winning_outcome=winner,
            outcomes=tuple(o.name for o in getattr(m, 'outcomes', ()) or ()),
            condition_id=cid,
            prices=prices,
            price_outcome=(getattr(m, 'outcomes', ()) or (None,))[0].name
                          if getattr(m, 'outcomes', ()) else None,
        ))
    return out, diag


def entries_from_decisions(decisions: Sequence, markets: Sequence[ResolvedMarket],
                           notional_cap_usdc: float = 10.0,
                           min_shares: float = POLYMARKET_MIN_SHARES,
                           price_source: str = 'limit',
                           max_staleness_s: Optional[int] = 600
                           ) -> Tuple[List[Entry], dict]:
    """Turn strategy Decisions into Entries, sizing each from the cap.

    Duck-typed against strategies.polymarket.base.Decision (`action`, `legs`,
    `market_slug`, `window_ts`, `strategy`) so the backtest layer takes no
    import dependency on the strategy package.

    `price_source`:
      'limit'  - pay the leg's limit price. This is an ASSUMPTION that the
                 order filled at its limit, and it is optimistic: a marketable
                 limit that fills at all often fills worse. Costs are charged
                 with fill_is_walked=False so the spread and depth terms apply.
      'series' - pay the market's last quoted price at or before the decision
                 timestamp, from the historical series. No lookahead. Entries
                 with no usable quote are dropped and counted.

    Neither is a book walk. A book walk needs a historical ORDERBOOK, which
    the CLOB does not serve retrospectively, so the honest ceiling on any
    offline Polymarket backtest is a quote-based fill with modelled spread.
    That limitation rides in the returned diagnostics as `fill_model`.
    """
    if price_source not in ('limit', 'series'):
        raise ValueError(f"price_source must be 'limit' or 'series', got {price_source!r}")

    by_id = {m.market_id: m for m in markets}
    spec = spec_for('POLYMARKET', ASSET_CLASS)
    diag = {'decisions_in': len(decisions or ()), 'not_entry': 0,
            'no_legs': 0, 'unknown_market': 0, 'no_quote': 0,
            'invalid_price': 0, 'unaffordable': 0, 'built': 0,
            'fill_model': price_source,
            'fill_model_note': ('no historical orderbook exists, so this is a '
                                'quote-based fill with modelled spread, not a '
                                'book walk')}
    out: List[Entry] = []

    for d in decisions or ():
        if getattr(d, 'action', None) != 'ENTER':
            diag['not_entry'] += 1
            continue
        legs = getattr(d, 'legs', None) or []
        if not legs:
            diag['no_legs'] += 1
            continue
        leg = legs[0]
        slug = getattr(d, 'market_slug', None) or getattr(leg, 'market_slug', None)
        market = by_id.get(slug)
        if market is None:
            diag['unknown_market'] += 1
            continue

        ts = getattr(d, 'window_ts', None)
        if price_source == 'limit':
            price = getattr(leg, 'limit_price', None)
        else:
            price = market.price_at(ts, max_staleness_s=max_staleness_s)
            if price is None:
                diag['no_quote'] += 1
                continue
        if not is_valid_binary_price(price):
            diag['invalid_price'] += 1
            continue

        shares = getattr(leg, 'shares', None)
        if not shares:
            shares = spec.size_for(notional_cap_usdc, float(price))
        if not shares or shares < min_shares:
            diag['unaffordable'] += 1
            continue

        out.append(Entry(
            market_id=slug,
            outcome_side=getattr(leg, 'outcome_side', ''),
            entry_price=float(price),
            shares=float(shares),
            strategy=getattr(d, 'strategy', '') or '',
            entry_ts=ts,
            order_type=getattr(leg, 'order_type', 'taker'),
            fill_is_walked=False,
            features=dict(getattr(d, 'features', {}) or {}),
        ))
        diag['built'] += 1

    # in - dropped == out, asserted (convention 20).
    dropped = (diag['not_entry'] + diag['no_legs'] + diag['unknown_market']
               + diag['no_quote'] + diag['invalid_price'] + diag['unaffordable'])
    if diag['decisions_in'] - dropped != len(out):
        raise AssertionError(
            f"decision accounting broken: in={diag['decisions_in']} "
            f'dropped={dropped} built={len(out)}')
    return out, diag


# ===========================================================================
# OUTPUT
# ===========================================================================

def _sanitize(obj, path: str, non_finite: List[str]):
    """Recursively replace non-finite floats with None, recording where.

    `json.loads` is not a strict JSON parser - it accepts bare `Infinity` and
    `NaN`, so a Python round trip proves nothing about portability
    (convention 19). Anything written here must survive JSON.parse, and an
    infinite value is often the CORRECT answer (convention 12), so it becomes
    a documented null rather than a zero or a crash.
    """
    if isinstance(obj, dict):
        return {k: _sanitize(v, f'{path}.{k}' if path else str(k), non_finite)
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v, f'{path}[{i}]', non_finite)
                for i, v in enumerate(obj)]
    if isinstance(obj, float) and not math.isfinite(obj):
        non_finite.append(path)
        return None
    return obj


def write_report(report: dict, filepath: str) -> dict:
    """Write a report as strictly-valid JSON. Never emits Infinity or NaN."""
    non_finite: List[str] = []
    payload = _sanitize(dict(report), '', non_finite)
    if non_finite:
        payload['non_finite_fields'] = sorted(non_finite)
        payload['_note'] = INF_NOTE
    path = os.fspath(filepath)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w') as f:
        # allow_nan=False makes a regression raise at WRITE time rather than
        # ship an unparseable file (convention 19).
        json.dump(payload, f, indent=2, allow_nan=False)
    logger.info('wrote polymarket report: %s', path)
    return payload


# ===========================================================================
# SMOKE FIXTURE (synthetic - NOT a result)
# ===========================================================================

def synthetic_smoke(n: int = 400, win_rate: float = 0.54,
                    entry_price: float = 0.50, shares: float = 20.0,
                    seed: int = 7) -> Tuple[List[Entry], List[ResolvedMarket]]:
    """A deterministic synthetic fixture that proves the harness runs.

    THIS IS NOT A RESULT AND MUST NEVER BE CITED AS ONE. The win rate is an
    input, not a measurement: the fixture hands the harness a known edge and
    checks the harness recovers it. Any number it produces is a statement
    about the arithmetic in this file, nothing else.
    """
    import random
    rng = random.Random(seed)
    entries: List[Entry] = []
    markets: List[ResolvedMarket] = []
    for i in range(n):
        mid = f'synthetic-{i:05d}'
        won = rng.random() < win_rate
        markets.append(ResolvedMarket(
            market_id=mid,
            winning_outcome='Up' if won else 'Down',
            outcomes=('Up', 'Down'),
        ))
        entries.append(Entry(
            market_id=mid, outcome_side='Up', entry_price=entry_price,
            shares=shares, strategy='synthetic_smoke', entry_ts=1_700_000_000 + i * 300,
        ))
    return entries, markets


if __name__ == '__main__':      # pragma: no cover - manual smoke only
    logging.basicConfig(level=logging.INFO)
    ents, mkts = synthetic_smoke()
    rep = PolymarketHarness(notional_cap_usdc=10.0).score(
        ents, mkts, strategy='synthetic_smoke', label='SMOKE TEST - synthetic')
    print(json.dumps(_sanitize(rep, '', []), indent=2))
