"""One-shot surgical patcher for engine/polymarket/paper_adapter.py.

Every replacement asserts its anchor is present EXACTLY once before it fires, so
a silently-missed patch is impossible: the script raises instead of writing a
half-patched file. Deleted after it runs.
"""
import io
import sys

PATH = 'engine/polymarket/paper_adapter.py'

EDITS = []


def edit(old, new):
    EDITS.append((old, new))


# ---------------------------------------------------------------- docstring
edit(
    """  2. **PnL is resolution-based.** There is no stop and no path. A position is
     worth its premium until the oracle speaks, then it is worth exactly $1.00
     or exactly $0.00 per share. `max loss = what you paid` IS the stop.

The kill switch is enforced HERE, in the adapter, and not in `risk_gate.py`.
The gate's contract is that it is a pure function of the portfolio state it is
handed; making it stat() a file would break that, and a gate that reads the
filesystem cannot be reasoned about from its arguments. The adapter is the only
place a Polymarket position can be opened, so it is the only place the switch
has to hold. Note the asymmetry with the crypto executor: HALT there also
FLATTENS. Here it can only block new entries, because a binary held to
resolution has no sell path in paper mode. Blocking entries is the whole of
what a halt can mean on this asset class, and `botctl status` says so rather
than letting an operator infer that a halt closed the exposure.
""",
    """  2. **PnL is resolution-based, unless a strategy sells out first.** There is
     no stop and no path. A position held to the end is worth its premium until
     the oracle speaks, then exactly $1.00 or exactly $0.00 per share, and
     `max loss = what you paid` IS the stop. A position CLOSED early
     (`simulate_taker_sell`) realises `proceeds - cost` instead, where the
     proceeds come from walking the BID side for the full size.

## Two exit kinds, and why they must never be pooled

`resolve_positions` and `simulate_taker_sell` both close a position, and they
produce statistically different animals:

    exit_kind='resolution'   binary payoff, 1.00 or 0.00. Win rate has to beat
                             the entry premium for the strategy to make money.
    exit_kind='sell'         a few cents either way. Win rate has to beat the
                             profit/loss ratio of the exit rules instead.

Averaging a 99%-win-rate 1c scalp with a 52%-win-rate 50c binary produces a
number that describes neither. So `summary()` reports `by_exit_kind` alongside
the pooled totals, `share_weighted_entry_price` and `breakeven_win_rate` are
computed on RESOLUTION exits only (they are meaningless for a position that
never redeemed), and every close writes its exit kind into the CSV.

The kill switch is enforced HERE, in the adapter, and not in `risk_gate.py`.
The gate's contract is that it is a pure function of the portfolio state it is
handed; making it stat() a file would break that, and a gate that reads the
filesystem cannot be reasoned about from its arguments. The adapter is the only
place a Polymarket position can be opened, so it is the only place the switch
has to hold.

Note the asymmetry with the crypto executor: HALT there also FLATTENS. Here it
still blocks new ENTRIES ONLY, and `simulate_taker_sell` is deliberately NOT
halt-gated - closing risk during a halt is the point of a halt, and a stop loss
that stops working when the kill switch is pulled is not a stop loss.

BUT read what changed. Before this method existed the Polymarket path had no
sell of any kind, so "a halt cannot close a binary" was a STRUCTURAL FACT. It is
now a CHOICE: flattening would mean the halt reaching into open positions and
selling them, which is a policy decision about operator intent and Raven's call,
not the adapter's. Until that ruling `botctl status` and the shadow loop's halt
note both continue to say a halt blocks entries only, and both are still
accurate. Convention 22 - a docstring is not the wiring, so the wiring is
unchanged and this paragraph says so.
""")

# ------------------------------------------------------- PaperPosition fields
edit(
    """    opened_ts: int
    window_ts: Optional[int] = None
    resolution: Optional[str] = None      # 'WIN' | 'LOSS' | None (pending)
    pnl_usdc: Optional[float] = None
    features: dict = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.resolution is None
""",
    """    opened_ts: int
    window_ts: Optional[int] = None
    resolution: Optional[str] = None      # 'WIN' | 'LOSS' | None (pending)
    pnl_usdc: Optional[float] = None
    features: dict = field(default_factory=dict)

    # -- how the position ended. `exit_kind` is None while it is open, then
    # 'resolution' (the oracle paid 1.00 or 0.00) or 'sell' (we hit the bid
    # before expiry). `resolution` stays a WIN/LOSS on both paths so existing
    # readers keep working, and WIN on a sell means realised PnL > 0 - a
    # break-even scratch is not a win.
    exit_kind: Optional[str] = None       # 'resolution' | 'sell' | None
    exit_price: Optional[float] = None    # walked average sell price
    exit_reason: Optional[str] = None     # the strategy's own exit rule name
    exit_ts: Optional[int] = None
    exit_fee_usdc: float = 0.0
    proceeds_usdc: Optional[float] = None

    @property
    def is_open(self) -> bool:
        return self.resolution is None

    @property
    def closed_early(self) -> bool:
        \"\"\"Sold before the oracle spoke. Never true for a resolved position.\"\"\"
        return self.exit_kind == 'sell'

    @property
    def total_fee_usdc(self) -> float:
        \"\"\"Entry fee plus exit fee. Both sides of a round trip pay.\"\"\"
        return self.fee_usdc + self.exit_fee_usdc
""")

# ---------------------------------------------------------- simulate_taker_sell
edit(
    """    def build_fill(self, position: PaperPosition) -> Fill:
""",
    '''    def simulate_taker_sell(self, position_id: str,
                            limit_price: float = MIN_PRICE,
                            shares: Optional[float] = None,
                            book: Optional[Orderbook] = None,
                            reason: str = '',
                            features: Optional[dict] = None
                            ) -> Optional[PaperPosition]:
        """Close an open position by walking the BID side. Returns it, or None.

        This is the mirror of `simulate_taker_buy` and it exists for exactly one
        reason: `PM_fair_value_arb` claims its edge by selling a corrected
        mispricing before resolution. Without a real sell simulation that claim
        could not be tested at all, and simulating it at `best_bid` would
        overstate it in precisely the way walking the ask stops
        `simulate_taker_buy` from overstating entries. A 5-minute book's top bid
        is routinely 5-20 shares; a 20-share exit eats levels.

        `limit_price` is the LOWEST price we will accept per share. It defaults
        to 0.00, which accepts every bid - correct for a stop loss, because a
        stop that refuses a bad price is not a stop. A profit-taking caller
        should pass its target. `shares` defaults to the whole position.

        ## All-or-nothing, deliberately

        A partial fill is REFUSED and the position stays open. That is not
        conservatism, it is the honest failure mode: a strategy whose entire
        thesis is "we exit before resolution" has to make the case where it
        CANNOT exit loud and expensive rather than rounding it into a smaller
        position. An unsold position rides to resolution and its full binary
        PnL is charged to the strategy, the same treatment temporal_arbitrage
        gives an unpaired leg.

        ## NOT gated on the halt

        `simulate_taker_buy` refuses during a HALT; this does not. A halt says
        "stop taking risk", and closing a position reduces risk. Blocking exits
        during a halt would strand exactly the exposure the operator pulled the
        switch about. See the module docstring for what this does and does not
        change about the halt's documented contract.

        Every exit path logs a row before returning, so a None return always has
        a recorded reason (convention 20).
        """
        features = features or {}
        position = self.positions.get(position_id)

        if position is None:
            self._log('unknown', 'unknown', 'SKIP', 'unknown_position',
                      position_id=position_id)
            return None

        strategy = position.strategy
        slug = position.market_slug
        base = dict(window_ts='' if position.window_ts is None else position.window_ts,
                    outcome_side=position.outcome_side,
                    token_id=position.token_id,
                    limit_price=limit_price,
                    position_id=position.position_id)

        if not position.is_open:
            # Already settled. Selling it again would book the proceeds twice
            # and leave equity permanently wrong.
            self._log(strategy, slug, 'SKIP', 'position_not_open',
                      resolution=position.resolution, **base)
            return None

        requested = position.shares if shares is None else float(shares)
        base['requested_shares'] = requested

        if requested <= 0 or requested > position.shares + 1e-9:
            # Selling more than we hold is a short, which this venue path does
            # not have, and selling zero is a caller bug. Neither is a market
            # observation.
            self._log(strategy, slug, 'SKIP', 'invalid_sell_size', **base)
            return None

        if not (MIN_PRICE <= limit_price <= MAX_PRICE):
            self._log(strategy, slug, 'SKIP', 'limit_price_out_of_range', **base)
            return None

        if book is None:
            try:
                book = fetch_orderbook(self.client, position.token_id)
            except Exception as exc:
                logger.warning('PM PAPER sell orderbook read raised for %s: '
                               '%s: %s', position.token_id, type(exc).__name__,
                               exc)
                self._log(strategy, slug, 'SKIP', 'orderbook_read_error', **base)
                return None
        if book is None:
            self._log(strategy, slug, 'SKIP', 'no_orderbook', **base)
            return None

        if not book.bids:
            # Nobody is bidding. This position CANNOT be closed right now, which
            # is a different fact from "our limit was too high" below, and the
            # two need opposite responses: this one means the exit model has
            # failed and the position is heading for resolution.
            self._log(strategy, slug, 'SKIP', 'no_bid_liquidity', **base)
            return None

        walk = walk_book(book, requested, limit_price, side='SELL')

        common = dict(
            base, filled_shares=walk.filled_shares,
            slippage_vs_top=('' if walk.slippage_vs_top is None
                             else round(walk.slippage_vs_top, 4)),
            levels_consumed='|'.join('{}@{}'.format(p, s)
                                     for p, s in walk.levels_consumed),
            exhausted_book=walk.exhausted_book,
        )

        if walk.unfilled:
            self._log(strategy, slug, 'NO_FILL', 'bid_below_limit', **common)
            return None

        bad_levels = [p for p, _ in walk.levels_consumed
                      if not (MIN_PRICE <= p <= MAX_PRICE)]
        if bad_levels:
            self._log(strategy, slug, 'SKIP', 'book_price_out_of_range',
                      avg_price=walk.avg_price, **common)
            return None

        if not walk.fully_filled:
            # See the docstring. The position stays OPEN and is still exposed.
            self._log(strategy, slug, 'NO_FILL', 'partial_sell_refused',
                      avg_price=walk.avg_price, **common)
            return None

        # For a SELL walk, `cost_usdc` is the sum of price*size taken off the
        # bids - i.e. the PROCEEDS. Named `cost` on WalkResult because it is
        # side-agnostic there; renamed here so nothing downstream subtracts it.
        proceeds = walk.cost_usdc
        exit_fee = proceeds * self.taker_fee_rate
        pnl = proceeds - exit_fee - position.cost_usdc - position.fee_usdc

        position.proceeds_usdc = proceeds
        position.exit_fee_usdc = exit_fee
        position.exit_price = walk.avg_price
        position.exit_kind = 'sell'
        position.exit_reason = reason or 'unspecified'
        position.exit_ts = int(time.time())
        position.pnl_usdc = pnl
        # A scratch is not a win. `> 0` and not `>= 0`, so a zero-PnL round trip
        # lands in the same bucket as a small loss rather than inflating a win
        # rate that this strategy is judged on to two decimal places.
        position.resolution = 'WIN' if pnl > 0 else 'LOSS'

        feat_str = ';'.join('{}={}'.format(k, v)
                            for k, v in sorted(features.items()))
        hold_sec = (None if position.exit_ts is None
                    else position.exit_ts - position.opened_ts)
        detail = ('exit_kind=sell;entry_price={:.4f};exit_price={:.4f};'
                  'hold_seconds={};proceeds_usdc={:.4f}').format(
                      position.avg_price, walk.avg_price,
                      '' if hold_sec is None else hold_sec, proceeds)

        self._log(strategy, slug, 'CLOSE', position.exit_reason,
                  avg_price=round(walk.avg_price, 4),
                  cost_usdc=round(position.cost_usdc, 4),
                  fee_usdc=round(position.total_fee_usdc, 6),
                  max_loss_usdc=round(position.max_loss_usdc, 4),
                  resolution=position.resolution,
                  won=pnl > 0,
                  pnl_usdc=round(pnl, 4),
                  features=(detail + ';' + feat_str) if feat_str else detail,
                  **{k: v for k, v in common.items()
                     if k not in ('features', 'position_id')},
                  position_id=position.position_id)

        logger.info('PM PAPER CLOSE %s %s %s %.0f sh %.4f -> %.4f pnl=%.4f (%s)',
                    strategy, slug, position.outcome_side, walk.filled_shares,
                    position.avg_price, walk.avg_price, pnl,
                    position.exit_reason)
        return position

    def build_fill(self, position: PaperPosition) -> Fill:
''')

# ------------------------------------------------- resolve_positions tagging
edit(
    """            won = value == WINNING_REDEMPTION
            redemption = pos.shares * (WINNING_REDEMPTION if won
                                       else LOSING_REDEMPTION)
            pos.pnl_usdc = redemption - pos.cost_usdc - pos.fee_usdc
            pos.resolution = 'WIN' if won else 'LOSS'
            settled.append(pos)
""",
    """            won = value == WINNING_REDEMPTION
            redemption = pos.shares * (WINNING_REDEMPTION if won
                                       else LOSING_REDEMPTION)
            pos.pnl_usdc = redemption - pos.cost_usdc - pos.fee_usdc
            pos.resolution = 'WIN' if won else 'LOSS'
            # Tagged so `summary()` can keep the two payoff shapes apart. A
            # position that reaches here was never sold; `simulate_taker_sell`
            # sets `exit_kind='sell'` and closes it out of this loop entirely.
            pos.exit_kind = 'resolution'
            pos.exit_reason = 'oracle_' + pos.resolution.lower()
            pos.exit_price = WINNING_REDEMPTION if won else LOSING_REDEMPTION
            pos.proceeds_usdc = redemption
            settled.append(pos)
""")

# --------------------------------------------------------------- summary()
edit(
    """        resolved = self.resolved_positions()
        wins = [p for p in resolved if p.resolution == 'WIN']
        pending = self.open_positions()
        entries = len(self.positions)

        resolved_shares = sum(p.shares for p in resolved)
        weighted_entry = ((sum(p.cost_usdc for p in resolved) / resolved_shares)
                          if resolved_shares else None)""",
    """        resolved = self.resolved_positions()
        wins = [p for p in resolved if p.resolution == 'WIN']
        pending = self.open_positions()
        entries = len(self.positions)

        # Entry price and breakeven only mean something for a position that
        # REDEEMED. A trade sold at 0.53 never had a 1.00-or-0.00 payoff, so
        # folding it in would move a number whose whole job is to be compared
        # against a resolution win rate. Legacy behaviour is preserved exactly:
        # before `simulate_taker_sell` existed every resolved position was a
        # resolution exit, so this filter is a no-op on any pre-existing run.
        redeemed = [p for p in resolved if p.exit_kind != 'sell']
        resolved_shares = sum(p.shares for p in redeemed)
        weighted_entry = ((sum(p.cost_usdc for p in redeemed) / resolved_shares)
                          if resolved_shares else None)""")

edit(
    """        breakeven = (((sum(p.cost_usdc for p in resolved)
                       + sum(p.fee_usdc for p in resolved))
                      / (resolved_shares * WINNING_REDEMPTION))
                     if resolved_shares else None)
        return {""",
    """        breakeven = (((sum(p.cost_usdc for p in redeemed)
                       + sum(p.total_fee_usdc for p in redeemed))
                      / (resolved_shares * WINNING_REDEMPTION))
                     if resolved_shares else None)

        # The two exit kinds, never pooled. See the module docstring.
        by_exit_kind: Dict[str, dict] = {}
        for kind in ('resolution', 'sell'):
            group = [p for p in resolved if (p.exit_kind or 'resolution') == kind]
            if not group:
                continue
            group_wins = [p for p in group if p.resolution == 'WIN']
            pnl = sum(p.pnl_usdc or 0.0 for p in group)
            shares = sum(p.shares for p in group)
            by_exit_kind[kind] = {
                'closed': len(group),
                'wins': len(group_wins),
                'losses': len(group) - len(group_wins),
                'win_rate': len(group_wins) / len(group),
                'realized_pnl_usdc': round(pnl, 4),
                'avg_pnl_per_trade_usdc': round(pnl / len(group), 4),
                # The kill-condition unit for PM_fair_value_arb: cents per
                # share, not dollars per trade, so it is comparable across
                # sizes.
                'avg_pnl_per_share_usdc': (round(pnl / shares, 6)
                                           if shares else None),
            }

        return {""")

edit(
    """            'realized_pnl_usdc': round(self.realized_pnl(), 4),
            'capital_at_risk_usdc': round(self.capital_at_risk(), 4),
            'equity_usdc': round(self.get_equity(), 4),
            'decision_counts': dict(self.decision_counts),
            'log_path': self.log_path,
            'note': ('win_rate, share_weighted_entry_price and '
                     'breakeven_win_rate are computed on RESOLVED positions '
                     'only. pending is never folded into wins or losses. '
                     'Compare win_rate against breakeven_win_rate - on a '
                     'binary, entry price plus fees IS the hurdle.'),
        }""",
    """            'realized_pnl_usdc': round(self.realized_pnl(), 4),
            'capital_at_risk_usdc': round(self.capital_at_risk(), 4),
            'equity_usdc': round(self.get_equity(), 4),
            'closed_early': sum(1 for p in resolved if p.closed_early),
            'by_exit_kind': by_exit_kind,
            'decision_counts': dict(self.decision_counts),
            'log_path': self.log_path,
            'note': ('win_rate is computed on RESOLVED positions only and '
                     'pending is never folded into wins or losses. It POOLS '
                     'both exit kinds - use by_exit_kind for anything that '
                     'matters, because a 1c scalp sold at the bid and a 50c '
                     'binary held to the oracle have different payoff shapes '
                     'and a pooled win rate describes neither. '
                     'share_weighted_entry_price and breakeven_win_rate cover '
                     'RESOLUTION exits only; they are meaningless for a '
                     'position that never redeemed. On a binary held to '
                     'resolution, entry price plus fees IS the hurdle.'),
        }""")


def main() -> int:
    with io.open(PATH, encoding='utf-8') as f:
        text = f.read()

    for i, (old, new) in enumerate(EDITS, 1):
        n = text.count(old)
        if n != 1:
            sys.stderr.write(
                'PATCH {} anchor matched {} times, expected exactly 1. '
                'Refusing to write a half-patched file.\n'.format(i, n))
            return 1
        text = text.replace(old, new, 1)

    with io.open(PATH, 'w', encoding='utf-8') as f:
        f.write(text)
    print('patched {} edits into {}'.format(len(EDITS), PATH))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
