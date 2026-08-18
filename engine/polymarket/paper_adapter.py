"""Paper trading adapter for Polymarket binary markets.

Simulated taker fills against the LIVE CLOB orderbook. No orders are ever
placed; no wallet is ever touched. Live execution needs EIP-712 signing and is
explicitly out of scope (D-267).

`PAPER_MODE` below is an unconditional `True`. There is no config key, no
environment variable and no constructor argument that can flip it, the
constructor refuses to build an adapter if it has been tampered with, and this
module imports no wallet, no signer and no order SDK. The only client it
touches (`PolymarketClient`) exposes no verb but GET.

Two things make this different from `engine/adapters/paper.py`:

  1. **Fills walk the book.** The crypto paper adapter fills at
     `ask * (1 + slippage)` because a Binance BTC book is deep enough that
     top-of-book plus a slippage constant is a fair model. A Polymarket 5-minute
     book is not: the top level is routinely 5-20 shares. So we consume real
     levels and report the real average, per the Dan1ro0 article's point that
     tradable edge is `fair_value - expected_average_entry - costs - margin`,
     not `fair_value - best_ask`.

  2. **PnL is resolution-based.** There is no stop and no path. A position is
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

Every decision window is logged, entries AND skips. That is moondevonyt's
convention ("the logging IS the product") and it is also convention 20 here: a
silent skip is a missing number. A window with no row in the log is a window we
cannot audit, and a strategy whose skips are invisible cannot be distinguished
from one that never fired. Every exit path in `simulate_taker_buy` writes a row
before returning, including the ones reached by an exception - and including
the halt, so that a halted session is visibly halted in the log rather than
looking like a session where no strategy ever signalled.
"""
import csv
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from engine.halt import is_halted
from engine.polymarket.client import PolymarketClient
from engine.polymarket.markets import get_market_by_slug
from engine.polymarket.orderbook import fetch_orderbook, walk_book
from engine.polymarket.prices import resolution_price
from engine.polymarket.types import (LOSING_REDEMPTION, MIN_SHARES, PRICE_TICK,
                                     WINNING_REDEMPTION, Fill, Orderbook)

logger = logging.getLogger(__name__)

# Unconditional. Not read from config, not overridable per instance, and
# checked in __init__ so that flipping it is a hard failure rather than a
# quietly live adapter.
PAPER_MODE = True

DEFAULT_LOG_DIR = os.path.join('research', 'polymarket_paper')

# Polymarket charges no explicit taker fee on the CLOB today. It is a config
# knob rather than a hardcoded 0.0 because "the fee is zero" is an assumption
# with an expiry date (convention 17), and a strategy whose edge is 2c per
# share dies the day that changes.
DEFAULT_TAKER_FEE_RATE = 0.0

# A share pays exactly $1.00 or exactly $0.00, so every price lives on [0, 1].
# A quote outside that interval is a corrupt or misparsed book, never an
# opportunity: paying more than $1.00 for a $1.00-max payoff is a guaranteed
# loss, and a negative price would book negative cost (free money).
MIN_PRICE = 0.0
MAX_PRICE = 1.0

LOG_COLUMNS = [
    'ts', 'iso', 'strategy', 'market_slug', 'window_ts', 'action', 'reason',
    'outcome_side', 'token_id', 'limit_price', 'requested_shares',
    'filled_shares', 'avg_price', 'best_ask', 'slippage_vs_top',
    'levels_consumed', 'exhausted_book', 'cost_usdc', 'fee_usdc',
    'max_loss_usdc', 'max_gain_usdc', 'position_id', 'resolution',
    'won', 'pnl_usdc', 'features',
]


@dataclass
class PaperPosition:
    """An open (or resolved) simulated Polymarket position."""

    position_id: str
    strategy: str
    market_slug: str
    token_id: str
    outcome_side: str
    shares: float
    avg_price: float
    cost_usdc: float
    fee_usdc: float
    opened_ts: int
    window_ts: Optional[int] = None
    resolution: Optional[str] = None      # 'WIN' | 'LOSS' | None (pending)
    pnl_usdc: Optional[float] = None
    features: dict = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.resolution is None

    @property
    def max_loss_usdc(self) -> float:
        return self.cost_usdc + self.fee_usdc

    @property
    def max_gain_usdc(self) -> float:
        return self.shares * WINNING_REDEMPTION - self.cost_usdc - self.fee_usdc

    @property
    def breakeven_win_rate(self) -> float:
        """Win rate this entry needs just to break even.

        For a binary bought at p, that is p PLUS fees. Printing it next to a
        strategy's claimed win rate is the fastest way to see whether an entry
        band is viable: 60% at 55c clears, 60% at 65c does not.
        """
        return (self.cost_usdc + self.fee_usdc) / (self.shares * WINNING_REDEMPTION)


class PolymarketPaperAdapter:
    """Simulated taker execution against live Polymarket CLOB books."""

    def __init__(self, client: Optional[PolymarketClient] = None,
                 config: Optional[dict] = None,
                 log_dir: str = DEFAULT_LOG_DIR):
        if PAPER_MODE is not True:
            raise RuntimeError(
                'PAPER_MODE is not True. This adapter has no live execution '
                'path; a falsy PAPER_MODE means the module was tampered with.')

        self.client = client or PolymarketClient()
        cfg = (config or {}).get('polymarket', {})

        self.starting_equity = float(cfg.get('starting_equity_usdc', 2000.0))
        self.taker_fee_rate = float(cfg.get('taker_fee_rate',
                                            DEFAULT_TAKER_FEE_RATE))
        self.notional_cap_usdc = float(cfg.get('notional_cap_usdc', 10.0))
        self.max_concurrent_positions = int(cfg.get('max_concurrent_positions', 5))
        self.min_shares = int(cfg.get('min_shares', MIN_SHARES))
        self.price_tick = float(cfg.get('price_tick', PRICE_TICK))

        self.mode = 'paper'
        self.positions: Dict[str, PaperPosition] = {}
        self.log_dir = log_dir
        self.log_path = os.path.join(log_dir, 'polymarket_paper_log.csv')

        # Every window that reached the adapter, by disposition. A skip that is
        # not counted is a skip that did not happen, as far as any later
        # analysis can tell.
        self.decision_counts: Dict[str, int] = {}

    # -- logging ------------------------------------------------------------

    def _log(self, strategy: str, market_slug: str, action: str,
             reason: str = '', **kw) -> None:
        """Append one decision row. Called for entries AND every skip."""
        key = f'{action}:{reason}' if reason else action
        self.decision_counts[key] = self.decision_counts.get(key, 0) + 1

        os.makedirs(self.log_dir, exist_ok=True)
        now = time.time()
        row = {c: '' for c in LOG_COLUMNS}
        row.update({
            'ts': int(now),
            'iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
            'strategy': strategy,
            'market_slug': market_slug,
            'action': action,
            'reason': reason,
        })
        for k, v in kw.items():
            if k in row:
                row[k] = v

        # An existing but EMPTY file still needs a header. Testing existence
        # alone leaves a zero-byte log (touched by a setup script, or left by a
        # run that died between open() and writeheader()) headerless forever,
        # and then every reader silently promotes the first decision row to the
        # column names - deleting one window from every downstream count.
        header_needed = (not os.path.exists(self.log_path)
                         or os.path.getsize(self.log_path) == 0)
        with open(self.log_path, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
            if header_needed:
                writer.writeheader()
            writer.writerow(row)

        level = logging.INFO if action == 'ENTER' else logging.DEBUG
        logger.log(level, 'PM PAPER %s %s %s %s', action, strategy,
                   market_slug, reason)

    def log_skip(self, strategy: str, market_slug: str, reason: str,
                 **kw) -> None:
        """Public hook so a strategy can record a skip it decided on its own.

        Without this the adapter only ever sees windows that got as far as
        wanting a fill, and the skip distribution in the log would be a
        survivorship-biased view of the strategy's actual behaviour.
        """
        self._log(strategy, market_slug, 'SKIP', reason, **kw)

    # -- sizing -------------------------------------------------------------

    def shares_for(self, limit_price: float,
                   notional_usdc: Optional[float] = None) -> int:
        """Whole shares affordable at `limit_price` under the notional cap.

        Returns 0 when the cap cannot buy the exchange minimum. That is the
        Polymarket analogue of D-249's unsizable-futures case, and it must
        surface as NOT_TESTED / cannot-run, never as a loss (convention 11).

        `notional_usdc` sizes a single signal DOWN from the cap. Sizing above
        the cap is not honoured at fill time - see `simulate_taker_buy`, which
        enforces `notional_cap_usdc` regardless of how the size was derived.
        """
        notional = self.notional_cap_usdc if notional_usdc is None else notional_usdc
        if limit_price <= 0:
            return 0
        # +1e-9 before the floor: notional/price is not exact in binary
        # floating point, and a value that should be exactly 100.0 can arrive
        # as 99.999999999999986, costing a whole share.
        n = math.floor(notional / limit_price + 1e-9)
        return n if n >= self.min_shares else 0

    def round_to_tick(self, price: float, direction: str = 'down') -> float:
        """Snap a price to the tick grid. 'down' for a buy cap, 'up' for a sell.

        The epsilon is not cosmetic. `0.29 / 0.01` evaluates to
        28.999999999999996, so a plain floor moved a price that was ALREADY on
        the 1c grid down a full tick to 0.28 (and `0.07 / 0.01` ceil'd up to
        0.08). On a binary whose whole edge is 2-3c, one tick is a third to a
        half of it, and this lands on the entry cap - the number that decides
        whether a window trades at all.

        Output precision is derived from the tick rather than hardcoded to 2
        decimals, so a venue change to a 0.001 tick does not silently collapse
        every price back onto the 1c grid.
        """
        eps = 1e-9
        steps = price / self.price_tick
        steps = (math.floor(steps + eps) if direction == 'down'
                 else math.ceil(steps - eps))
        decimals = max(0, -math.floor(math.log10(self.price_tick)) )
        return round(steps * self.price_tick, decimals)

    # -- execution ----------------------------------------------------------

    def simulate_taker_buy(self, strategy: str, market_slug: str,
                           token_id: str, outcome_side: str,
                           limit_price: float, shares: float,
                           window_ts: Optional[int] = None,
                           features: Optional[dict] = None,
                           book: Optional[Orderbook] = None
                           ) -> Optional[PaperPosition]:
        """Simulate a marketable buy by walking the live book.

        Returns the opened PaperPosition, or None if nothing filled. Every exit
        path writes a log row first, so a None return always has a recorded
        reason.
        """
        features = features or {}
        feat_str = ';'.join(f'{k}={v}' for k, v in sorted(features.items()))
        # `or ''` would turn a legitimate window_ts of 0 into "no window".
        ts_cell = '' if window_ts is None else window_ts

        base = dict(window_ts=ts_cell, outcome_side=outcome_side,
                    token_id=token_id, limit_price=limit_price,
                    requested_shares=shares, features=feat_str)

        # First guard, ahead of every other check. A halt outranks position
        # limits, price bands and sizing: those all ask "should this trade
        # happen", and the halt has already answered no. Checking it first also
        # means a halted session costs zero orderbook reads.
        #
        # `is_halted()` is fail-safe by construction - an unreadable HALT file
        # still counts as halted - so an IO problem here blocks entries rather
        # than quietly permitting them.
        if is_halted():
            self._log(strategy, market_slug, 'SKIP', 'halted', **base)
            return None

        if len(self.open_positions()) >= self.max_concurrent_positions:
            self._log(strategy, market_slug, 'SKIP', 'max_concurrent_positions',
                      **base)
            return None

        if not (MIN_PRICE < limit_price <= MAX_PRICE):
            # Cannot-run, not a loss. A limit outside [0, 1] on a binary is a
            # caller bug or a corrupt feed, and filling it would book a
            # position whose max_gain_usdc is negative by construction.
            self._log(strategy, market_slug, 'SKIP', 'limit_price_out_of_range',
                      **base)
            return None

        if shares < self.min_shares:
            # Cannot run, did not lose. Same shape as unsizable_at_cap.
            self._log(strategy, market_slug, 'SKIP', 'unsizable_at_cap', **base)
            return None

        # A declared risk cap that is not enforced is an unbounded fabricated
        # -PnL surface: whatever edge per share the strategy claims gets
        # multiplied by a position the account could never have funded.
        if shares * limit_price > self.notional_cap_usdc + 1e-9:
            self._log(strategy, market_slug, 'SKIP', 'over_notional_cap', **base)
            return None

        if book is None:
            try:
                book = fetch_orderbook(self.client, token_id)
            except Exception as exc:
                # PolymarketClient already swallows requests errors and returns
                # None. Anything that still escapes would otherwise take the
                # whole decision window out of the log with it, which is the
                # exact silent-drop convention 20 forbids.
                logger.warning('PM PAPER orderbook read raised for %s: %s: %s',
                               token_id, type(exc).__name__, exc)
                self._log(strategy, market_slug, 'SKIP', 'orderbook_read_error',
                          **base)
                return None
        if book is None:
            self._log(strategy, market_slug, 'SKIP', 'no_orderbook', **base)
            return None

        if not book.asks:
            # Nobody is quoting. An empty book and a book that has bids but no
            # asks are the same fact for a BUY: there is nothing to lift at any
            # price. `book_above_limit` below is the OPPOSITE diagnosis - there
            # IS depth, our limit was simply too tight. Merging the two would
            # make the skip taxonomy useless, which is what convention 20
            # forbids: a skip that is counted but not categorised cannot tell
            # you whether to loosen the limit or drop the market entirely.
            self._log(strategy, market_slug, 'SKIP', 'no_liquidity', **base)
            return None

        walk = walk_book(book, shares, limit_price, side='BUY')

        common = dict(
            base, filled_shares=walk.filled_shares,
            best_ask=book.best_ask,
            slippage_vs_top=('' if walk.slippage_vs_top is None
                             else round(walk.slippage_vs_top, 4)),
            levels_consumed='|'.join(f'{p}@{s}' for p, s in walk.levels_consumed),
            exhausted_book=walk.exhausted_book,
        )

        if walk.unfilled:
            self._log(strategy, market_slug, 'NO_FILL', 'book_above_limit',
                      avg_price='', **common)
            return None

        bad_levels = [p for p, _ in walk.levels_consumed
                      if not (MIN_PRICE <= p <= MAX_PRICE)]
        if bad_levels:
            self._log(strategy, market_slug, 'SKIP', 'book_price_out_of_range',
                      avg_price=walk.avg_price, **common)
            return None

        if walk.partial and walk.filled_shares < self.min_shares:
            # Below the exchange minimum, so this order could not have existed.
            self._log(strategy, market_slug, 'NO_FILL',
                      'partial_below_min_shares', avg_price=walk.avg_price,
                      **common)
            return None

        fee = walk.cost_usdc * self.taker_fee_rate
        position = PaperPosition(
            position_id=str(uuid.uuid4()),
            strategy=strategy,
            market_slug=market_slug,
            token_id=str(token_id),
            outcome_side=outcome_side,
            shares=walk.filled_shares,
            avg_price=walk.avg_price,
            cost_usdc=walk.cost_usdc,
            fee_usdc=fee,
            opened_ts=int(time.time()),
            window_ts=window_ts,
            features=features,
        )
        self.positions[position.position_id] = position

        self._log(strategy, market_slug, 'ENTER',
                  'partial_fill' if walk.partial else '',
                  avg_price=round(walk.avg_price, 4),
                  cost_usdc=round(walk.cost_usdc, 4),
                  fee_usdc=round(fee, 6),
                  max_loss_usdc=round(position.max_loss_usdc, 4),
                  max_gain_usdc=round(position.max_gain_usdc, 4),
                  position_id=position.position_id,
                  resolution='PENDING', **common)
        return position

    def build_fill(self, position: PaperPosition) -> Fill:
        """Fill record for a position, for callers that want the flat shape."""
        return Fill(
            market_slug=position.market_slug,
            token_id=position.token_id,
            outcome=position.outcome_side,
            side='BUY',
            shares=position.shares,
            avg_price=position.avg_price,
            cost_usdc=position.cost_usdc,
            fee_usdc=position.fee_usdc,
            timestamp=position.opened_ts,
        )

    # -- resolution ---------------------------------------------------------

    def resolve_positions(self) -> List[PaperPosition]:
        """Settle any open position whose market the oracle has resolved.

        Only exact 1.0/0.0 counts (see `prices.resolution_price`). A position
        whose market has not resolved stays PENDING forever rather than being
        marked to a 0.99 book - a fabricated win is worse than a missing one.
        Returns the positions settled by this call.

        Deliberately NOT gated on the halt. Resolution is bookkeeping, not a
        trade: it records what an already-open position settled at. Skipping it
        during a halt would leave positions PENDING that the oracle has already
        decided, and the operator would be reading a halted session's PnL with
        the losses missing.
        """
        settled = []
        # One read per (slug, outcome) per call. Five positions on the same
        # market used to mean five identical Gamma round trips.
        seen: Dict[tuple, Optional[float]] = {}
        for pos in list(self.positions.values()):
            if not pos.is_open:
                continue
            key = (pos.market_slug, pos.outcome_side)
            if key in seen:
                value = seen[key]
            else:
                try:
                    value = resolution_price(self.client, pos.market_slug,
                                             pos.outcome_side)
                except Exception as exc:
                    # An unreadable oracle is not an unresolved market and is
                    # certainly not a loss (convention 11). Leave the position
                    # PENDING and record that the read failed, so a run with a
                    # broken feed does not look like a run with no resolutions.
                    logger.warning('PM PAPER resolution read raised for %s: '
                                   '%s: %s', pos.market_slug,
                                   type(exc).__name__, exc)
                    self._log(pos.strategy, pos.market_slug, 'SKIP',
                              'resolution_read_error',
                              window_ts='' if pos.window_ts is None else pos.window_ts,
                              outcome_side=pos.outcome_side,
                              token_id=pos.token_id,
                              position_id=pos.position_id,
                              resolution='PENDING')
                    seen[key] = None
                    continue
                seen[key] = value
            if value is None:
                continue

            won = value == WINNING_REDEMPTION
            redemption = pos.shares * (WINNING_REDEMPTION if won
                                       else LOSING_REDEMPTION)
            pos.pnl_usdc = redemption - pos.cost_usdc - pos.fee_usdc
            pos.resolution = 'WIN' if won else 'LOSS'
            settled.append(pos)

            self._log(pos.strategy, pos.market_slug, 'RESOLVE',
                      pos.resolution.lower(),
                      window_ts='' if pos.window_ts is None else pos.window_ts,
                      outcome_side=pos.outcome_side, token_id=pos.token_id,
                      filled_shares=pos.shares,
                      avg_price=round(pos.avg_price, 4),
                      cost_usdc=round(pos.cost_usdc, 4),
                      fee_usdc=round(pos.fee_usdc, 6),
                      position_id=pos.position_id,
                      resolution=pos.resolution, won=won,
                      pnl_usdc=round(pos.pnl_usdc, 4))
        return settled

    # -- accounting ---------------------------------------------------------

    def open_positions(self) -> List[PaperPosition]:
        return [p for p in self.positions.values() if p.is_open]

    def resolved_positions(self) -> List[PaperPosition]:
        return [p for p in self.positions.values() if not p.is_open]

    def realized_pnl(self) -> float:
        return sum(p.pnl_usdc or 0.0 for p in self.resolved_positions())

    def capital_at_risk(self) -> float:
        """USDC that is currently unrecoverable if every open position loses.

        On a binary this is exact, not an estimate: max loss is the premium.
        """
        return sum(p.max_loss_usdc for p in self.open_positions())

    def get_equity(self) -> float:
        """Starting capital + realized PnL - premium tied up in open positions.

        Open positions are held at ZERO, not marked to the book. On a 5-minute
        market the book is thin enough that marking is mostly noise, and
        holding at zero means equity can only ever surprise upward.
        """
        return (self.starting_equity + self.realized_pnl()
                - self.capital_at_risk())

    def summary(self) -> dict:
        """Session summary. Pending is reported separately from won and lost.

        Collapsing PENDING into either bucket is the single easiest way to
        make a paper log lie, so the three counts never merge here.
        """
        resolved = self.resolved_positions()
        wins = [p for p in resolved if p.resolution == 'WIN']
        pending = self.open_positions()
        entries = len(self.positions)

        resolved_shares = sum(p.shares for p in resolved)
        weighted_entry = ((sum(p.cost_usdc for p in resolved) / resolved_shares)
                          if resolved_shares else None)
        # Entry price and breakeven coincide only at a ZERO fee. taker_fee_rate
        # is a config knob precisely because that is an assumption with an
        # expiry date (convention 17), so the hurdle is computed from money
        # actually spent, fees included. Reporting the bare entry price here
        # would UNDERSTATE the bar in the one field whose whole job is to be
        # compared against win_rate.
        breakeven = (((sum(p.cost_usdc for p in resolved)
                       + sum(p.fee_usdc for p in resolved))
                      / (resolved_shares * WINNING_REDEMPTION))
                     if resolved_shares else None)
        return {
            'mode': self.mode,
            'halted': is_halted(),
            'entries': entries,
            'resolved': len(resolved),
            'pending': len(pending),
            'wins': len(wins),
            'losses': len(resolved) - len(wins),
            'win_rate': (len(wins) / len(resolved)) if resolved else None,
            'share_weighted_entry_price': weighted_entry,
            'breakeven_win_rate': breakeven,
            'realized_pnl_usdc': round(self.realized_pnl(), 4),
            'capital_at_risk_usdc': round(self.capital_at_risk(), 4),
            'equity_usdc': round(self.get_equity(), 4),
            'decision_counts': dict(self.decision_counts),
            'log_path': self.log_path,
            'note': ('win_rate, share_weighted_entry_price and '
                     'breakeven_win_rate are computed on RESOLVED positions '
                     'only. pending is never folded into wins or losses. '
                     'Compare win_rate against breakeven_win_rate - on a '
                     'binary, entry price plus fees IS the hurdle.'),
        }

    def market_for_slug(self, slug: str):
        """Convenience passthrough so strategies need only the adapter."""
        return get_market_by_slug(self.client, slug)
