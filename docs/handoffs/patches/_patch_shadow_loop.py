"""One-shot surgical patcher for the shadow loop + strategy registry.

Anchors are asserted to match exactly once before anything is written, so a
concurrent edit by another session (convention 21 - this working directory is
shared) fails loudly instead of being clobbered.
"""
import io
import sys

EDITS = []


def edit(path, old, new):
    EDITS.append((path, old, new))


INIT = 'strategies/polymarket/__init__.py'
LOOP = 'engine/polymarket/shadow_loop.py'

# ===================================================================== registry
edit(INIT,
     '''"""Polymarket prediction-market strategies (D-267, D-268).

Seven strategies. Five are ports from moondevonyt's public Polymarket repo and
two implement Forge proposals.''',
     '''"""Polymarket prediction-market strategies (D-267, D-268).

Eight strategies. Five are ports from moondevonyt's public Polymarket repo, two
implement Forge proposals, and one (`fair_value_arb`) implements Dan1ro0
concepts 1-2 and is the only strategy in this package that does NOT hold to
resolution.''')

edit(INIT,
     """## Status: every one of these is NOT_TESTED

None of the seven has been through our graveyard. moondevonyt's win rates are
his numbers from his logs on his setup - hypotheses, not evidence (convention
3). The two Forge-proposal strategies have no vendor numbers at all, only an
estimated edge written before any code existed (convention 15). The
resolution-PnL harness extension does not exist yet, and running these through
the existing price-path harness would fabricate numbers (D-268).""",
     """## Status: every one of these is NOT_TESTED

None of the eight has been through our graveyard. moondevonyt's win rates are
his numbers from his logs on his setup - hypotheses, not evidence (convention
3). The two Forge-proposal strategies have no vendor numbers at all, only an
estimated edge written before any code existed (convention 15).
`fair_value_arb` carries a 99.3%/32,614-trade claim from a Reddit post about
somebody else's wallet, which is the weakest provenance in the package and is
stamped `claimed_win_rate_is_unverified_vendor_number=True` on every row it
emits. The resolution-PnL harness extension does not exist yet, and running
these through the existing price-path harness would fabricate numbers (D-268).

## One of them exits early, and that splits the scoring

`PM_fair_value_arb` sells before resolution. Its positions close with
`exit_kind='sell'` and a few cents of PnL; every other strategy here closes with
`exit_kind='resolution'` and a 1.00-or-0.00 payoff. Those two populations must
be scored SEPARATELY and never pooled - a pooled win rate across them describes
neither. `PolymarketPaperAdapter.summary()` reports `by_exit_kind` for exactly
this reason.""")

edit(INIT,
     """  PM_spread_harvest_taker     taker, single leg. Runs today on a book-implied
                              near-tie gate. Results under
                              `coin_flip_source='book_implied'` must be scored
                              SEPARATELY from any produced under
                              `cushion_atr`; they are different gates.
\"\"\"""",
     """  PM_spread_harvest_taker     taker, single leg. Runs today on a book-implied
                              near-tie gate. Results under
                              `coin_flip_source='book_implied'` must be scored
                              SEPARATELY from any produced under
                              `cushion_atr`; they are different gates.
  PM_fair_value_arb           taker in AND taker out. Needs a live BTC spot at
                              poll frequency (for the price tape that feeds the
                              speed and realized-vol inputs), both books for the
                              imbalance signal, and the 5m bar whose timestamp
                              equals this window's - all supplied by the shadow
                              loop. Needs the scorer to charge positions that
                              could NOT be sold at their realised resolution
                              PnL, exactly as temporal_arbitrage's unpaired legs
                              are charged, and to keep sold and redeemed trades
                              in separate populations.
\"\"\"""")

edit(INIT,
     """from strategies.polymarket.cross_window_relative_value import \\
    CrossWindowRelativeValue
from strategies.polymarket.mid_price_continuation import MidPriceContinuation""",
     """from strategies.polymarket.cross_window_relative_value import \\
    CrossWindowRelativeValue
from strategies.polymarket.fair_value_arb import ExitDecision, FairValueArb
from strategies.polymarket.mid_price_continuation import MidPriceContinuation""")

edit(INIT,
     '''def build_strategies():
    """Fresh instances of all seven. Order is stable for reproducible logs.''',
     '''def build_strategies():
    """Fresh instances of all eight. Order is stable for reproducible logs.''')

edit(INIT,
     """    Fresh instances matter more than it looks: `TemporalArbitrage` and
    `SpreadHarvestMaker` carry per-window state, so two callers sharing one
    instance would share a block ledger.
    \"\"\"
    return [
        StreakSnapper(),
        MidPriceContinuation(),
        BoxBuilder(),
        CorridorCollector(),
        TemporalArbitrage(),
        CrossWindowRelativeValue(),
        SpreadHarvestMaker(),
    ]""",
     """    Fresh instances matter more than it looks: `TemporalArbitrage`,
    `SpreadHarvestMaker` and `FairValueArb` carry per-window state, so two
    callers sharing one instance would share a block ledger. `FairValueArb`
    additionally carries a BTC price TAPE, and two loops feeding one tape would
    interleave their observations into a series neither of them saw.
    \"\"\"
    return [
        StreakSnapper(),
        MidPriceContinuation(),
        BoxBuilder(),
        CorridorCollector(),
        TemporalArbitrage(),
        CrossWindowRelativeValue(),
        SpreadHarvestMaker(),
        FairValueArb(),
    ]""")

edit(INIT,
     """    'StreakSnapper', 'MidPriceContinuation', 'BoxBuilder', 'CorridorCollector',
    'TemporalArbitrage', 'CrossWindowRelativeValue', 'SpreadHarvestMaker',
    'build_strategies',
]""",
     """    'StreakSnapper', 'MidPriceContinuation', 'BoxBuilder', 'CorridorCollector',
    'TemporalArbitrage', 'CrossWindowRelativeValue', 'SpreadHarvestMaker',
    'FairValueArb', 'ExitDecision',
    'build_strategies',
]""")

# ================================================================ loop docstring
edit(LOOP,
     """The strategy list is `strategies.polymarket.build_strategies()`, which is now
SEVEN strategies. FOUR of them can produce an entry in this loop; the other
three are blocked by missing DATA, not by their own logic:""",
     """The strategy list is `strategies.polymarket.build_strategies()`, which is now
EIGHT strategies. FIVE of them can produce an entry in this loop; the other
three are blocked by missing DATA, not by their own logic:""")

edit(LOOP,
     """  PM_mid_price_continuation  CANNOT. Needs the window's strike, which is a""",
     """  PM_fair_value_arb          CAN fire. Needs spot, both books, and the 5m bar
                             whose timestamp equals this window's. It is the
                             only strategy here that EXITS BEFORE RESOLUTION -
                             see the exit-management section below.
  PM_mid_price_continuation  CANNOT. Needs the window's strike, which is a""")

edit(LOOP,
     '''## Two strategies now carry state, and this loop cannot confirm their fills''',
     '''## Exit management, and why it sits OUTSIDE the accounting identity

`PM_fair_value_arb` sells before resolution, so this loop polls every open
position it owns on every cycle (`manage_exits`) and hands any EXIT decision to
`PolymarketPaperAdapter.simulate_taker_sell`. Three things about that:

  1. **Exits run BEFORE entries.** Closing a position frees a concurrency slot
     that a new entry in the same cycle can use, and a stop that waits a cycle
     for the entry loop to finish is a stop that is one poll late.
  2. **Exits run even when the context could not be built.** A stop loss that
     stops working because Gamma returned a 500 is not a stop loss, so
     `manage_exits(None)` fetches its own books on the api_error / no_market
     path.
  3. **Exit dispositions land in `exit_counts`, NOT in `counts`.** The identity
     `evaluations == cycles * n_strategies` counts one evaluation per strategy
     per cycle; a position check is not an evaluation and folding it in would
     break the identity for a reason that has nothing to do with decisions.
     `exit_counts` is its own categorised taxonomy (`hold:*`, `exit:*`,
     `sell_refused:*`, `book_*`) and is reported in `stats()`.

The refusal case is the one to watch. `simulate_taker_sell` is all-or-nothing:
if the bid side cannot absorb the full position under the limit, the sell does
not happen, the position STAYS OPEN, and it resolves like any other binary if
that persists. Those are counted as `sell_refused:<rule>`, and a session whose
`sell_refused` count rivals its `exit:` count is a session where the exit model
does not work, however good the win rate on the trades that did close looks.

A HALT does not block exits. See the paper adapter's module docstring for what
that does and does not change about the halt's contract - the short version is
that a halt still blocks entries only, and flattening is now a policy choice
rather than a structural impossibility.

## Two strategies now carry state, and this loop cannot confirm their fills''')

# ================================================================= record_close
edit(LOOP,
     """    def record_equity(self, equity: float, cash: float, open_risk: float,""",
     '''    def record_close(self, position) -> None:
        """Settle a positions row that was SOLD before the oracle spoke.

        Deliberately NOT `record_resolution`. That method stamps `exit_px` with
        the redemption value (1.00 or 0.00) and an exit_reason of
        'target'/'stop', neither of which is true of a position sold at 0.53
        because a mispricing corrected. Reusing it would put a redemption price
        on a trade that never redeemed, and every downstream PnL attribution
        would silently disagree with the fills table.

        `fees` is rewritten to the ROUND-TRIP total: the entry fee was already
        written at `record_entry` time and the exit fee only exists now.
        `r_multiple` divides realised PnL by premium plus fees, which on a
        binary is the exact maximum loss the position ever had - not an
        estimated stop distance.
        """
        risk = position.cost_usdc + position.fee_usdc
        pnl_net = position.pnl_usdc
        proceeds = position.proceeds_usdc
        pnl_gross = (None if proceeds is None
                     else proceeds - position.cost_usdc)
        r_multiple = ((pnl_net / risk) if risk > 0 and pnl_net is not None
                      else None)
        with self.conn:
            self.conn.execute(
                'UPDATE positions SET closed_ts = ?, exit_px = ?, '
                'pnl_gross = ?, pnl_net = ?, fees = ?, r_multiple = ?, '
                'exit_reason = ? WHERE id = ?',
                (_ms(), position.exit_price, pnl_gross, pnl_net,
                 position.total_fee_usdc, r_multiple,
                 'sell:' + (position.exit_reason or 'unspecified'),
                 position.position_id))

    def record_equity(self, equity: float, cash: float, open_risk: float,''')

# ================================================================== counters
edit(LOOP,
     """        self.counts: Counter = Counter()
        # Counters OUTSIDE the identity space: they describe the loop's health,
        # not a window's disposition, and folding them in would break the
        # identity for reasons that have nothing to do with decisions.
        self.health: Counter = Counter()""",
     """        self.counts: Counter = Counter()
        # Counters OUTSIDE the identity space: they describe the loop's health,
        # not a window's disposition, and folding them in would break the
        # identity for reasons that have nothing to do with decisions.
        self.health: Counter = Counter()
        # Ditto, and for the same reason: an open-position exit check is not an
        # evaluation of a window. Its own categorised taxonomy - see the module
        # docstring's exit-management section.
        self.exit_counts: Counter = Counter()""")

# =============================================================== manage_exits
edit(LOOP,
     """    # -- cycle --------------------------------------------------------------

    def run_cycle(self, now: Optional[float] = None) -> dict:""",
     '''    # -- exits ----------------------------------------------------------------

    def manage_exits(self, ctx: Optional[MarketContext] = None,
                     now: Optional[float] = None) -> dict:
        """Poll every open position whose strategy manages its own exits.

        Only strategies carrying `manages_exits = True` are consulted, and only
        about positions they opened. Everything else in this package holds to
        resolution and is left alone.

        `ctx=None` is the OUTAGE path and is a supported call, not a degraded
        one: books are fetched per position instead of being read off the
        context, and fair value is unavailable so the model-driven exits cannot
        fire. The price-driven ones (window close, price stop, profit target,
        time stop) still do, which is the point - those are the ones that bound
        the loss.

        Never raises. A strategy that throws while deciding an exit must not
        take the loop, or the other positions, with it.

        Returns a small summary. Every disposition also lands in `exit_counts`,
        which is OUTSIDE the `evaluations == cycles * n_strategies` identity.
        """
        now = time.time() if now is None else now
        managers = {getattr(s, 'strategy_name', None): s
                    for s in self.strategies
                    if getattr(s, 'manages_exits', False)}
        result = {'checked': 0, 'exits': 0, 'refused': 0}
        if not managers:
            return result

        # One fair-value estimate per managing strategy per cycle, not one per
        # position: the estimate is a property of the window, and recomputing
        # it per position would let two positions on the same window be judged
        # against two different fair values.
        estimates: Dict[str, object] = {}
        if ctx is not None:
            for name, strategy in managers.items():
                try:
                    estimates[name] = strategy.estimate(ctx)
                except Exception as exc:
                    self.health['exit_fair_value_exceptions'] += 1
                    logger.warning('PM SHADOW fair value raised for %s: %s: %s',
                                   name, type(exc).__name__, exc)

        for pos in list(self.adapter.open_positions()):
            strategy = managers.get(pos.strategy)
            if strategy is None:
                continue
            result['checked'] += 1

            book = None
            if ctx is not None:
                book = (ctx.books.get(pos.token_id)
                        or ctx.books_15m.get(pos.token_id))
            if book is None:
                # A position on a PREVIOUS window is not in this cycle's
                # context. Fetch its own book rather than treating a missing
                # one as "no exit today" - that position is the one closest to
                # expiry and therefore the one most in need of a decision.
                book, bstatus = self._fetch_book_checked(pos.token_id)
                if book is None:
                    self.exit_counts['book_' + bstatus] += 1
                    continue

            fair = None
            est = estimates.get(pos.strategy)
            # Fair value is a statement about ONE window. Applying this
            # window's estimate to a position from the previous window would be
            # a model stop computed off the wrong displacement.
            if (est is not None and getattr(est, 'usable', False)
                    and pos.window_ts == ctx.window_ts):
                try:
                    fair = est.for_side(pos.outcome_side)
                except ValueError:
                    self.health['exit_unknown_outcome_side'] += 1

            try:
                decision = strategy.manage_exit(pos, book, now=now,
                                                fair_value=fair)
            except Exception as exc:
                self.health['exit_decision_exceptions'] += 1
                self.exit_counts['decision_exception'] += 1
                logger.error('PM SHADOW exit decision raised for %s %s: %s: %s',
                             pos.strategy, pos.position_id,
                             type(exc).__name__, exc)
                continue

            self.exit_counts[('exit:' if decision.is_exit else 'hold:')
                             + (decision.reason or 'unspecified')] += 1
            if not decision.is_exit:
                continue

            closed = self.adapter.simulate_taker_sell(
                position_id=pos.position_id,
                limit_price=(0.0 if decision.limit_price is None
                             else decision.limit_price),
                shares=decision.shares,
                book=book,
                reason=decision.reason,
                features={k: v for k, v in (decision.features or {}).items()
                          if v is not None})

            if closed is None:
                # The adapter refused: thin bids, a limit the book will not
                # meet, or a partial. The position is STILL OPEN and STILL
                # EXPOSED, and if that persists to expiry it resolves. This
                # counter is the one that falsifies the whole strategy.
                result['refused'] += 1
                self.health['exit_sell_refused'] += 1
                self.exit_counts['sell_refused:' + (decision.reason
                                                    or 'unspecified')] += 1
                continue

            result['exits'] += 1
            self.store.record_close(closed)
            self.store.audit('position_closed_early', {
                'position_id': closed.position_id,
                'strategy': closed.strategy,
                'market_slug': closed.market_slug,
                'outcome_side': closed.outcome_side,
                'exit_kind': closed.exit_kind,
                'exit_reason': closed.exit_reason,
                'entry_price': closed.avg_price,
                'exit_price': closed.exit_price,
                'shares': closed.shares,
                'pnl_usdc': closed.pnl_usdc,
                'resolution': closed.resolution,
                'mode': MODE,
            })
            logger.info('PM SHADOW CLOSE %s %s %s %.0f sh %.4f -> %.4f '
                        'pnl=%.4f (%s)', closed.strategy, closed.market_slug,
                        closed.outcome_side, closed.shares, closed.avg_price,
                        closed.exit_price or 0.0, closed.pnl_usdc or 0.0,
                        closed.exit_reason)
        return result

    # -- cycle --------------------------------------------------------------

    def run_cycle(self, now: Optional[float] = None) -> dict:''')

# ============================================================= run_cycle calls
edit(LOOP,
     """            for strategy in self.strategies:
                name = getattr(strategy, 'strategy_name', str(strategy))
                self._log_and_count(name, detail.get('market_slug'), status,
                                    reason, dict(detail), window_ts=window_ts)
            detail['status'] = status
            return detail

        for strategy in self.strategies:""",
     """            for strategy in self.strategies:
                name = getattr(strategy, 'strategy_name', str(strategy))
                self._log_and_count(name, detail.get('market_slug'), status,
                                    reason, dict(detail), window_ts=window_ts)
            # Open positions still need managing during an outage. A stop loss
            # that stops working because Gamma returned a 500 is not a stop
            # loss, so this runs on the failure path too, fetching its own
            # books.
            detail['exits'] = self.manage_exits(None, now)
            detail['status'] = status
            return detail

        # BEFORE entries: closing a position frees a concurrency slot that an
        # entry this same cycle can use, and a stop that waits for the entry
        # loop is a stop one poll late.
        detail['exits'] = self.manage_exits(ctx, now)

        for strategy in self.strategies:""")

# ==================================================================== stats()
edit(LOOP,
     """            'counts': dict(self.counts),
            'health': dict(self.health),
            'halted': is_halted(),
            'equity_usdc': summary['equity_usdc'],
            'open_positions': summary['pending'],
            'resolved': summary['resolved'],
            'realized_pnl_usdc': summary['realized_pnl_usdc'],""",
     """            'counts': dict(self.counts),
            'health': dict(self.health),
            # Outside the identity on purpose - see manage_exits.
            'exit_counts': dict(self.exit_counts),
            'halted': is_halted(),
            'equity_usdc': summary['equity_usdc'],
            'open_positions': summary['pending'],
            'resolved': summary['resolved'],
            'closed_early': summary.get('closed_early', 0),
            'by_exit_kind': summary.get('by_exit_kind', {}),
            'realized_pnl_usdc': summary['realized_pnl_usdc'],""")


def main() -> int:
    by_path = {}
    for path, old, new in EDITS:
        if path not in by_path:
            with io.open(path, encoding='utf-8') as f:
                by_path[path] = f.read()

    for i, (path, old, new) in enumerate(EDITS, 1):
        text = by_path[path]
        n = text.count(old)
        if n != 1:
            sys.stderr.write(
                'PATCH {} on {} matched {} times, expected exactly 1. '
                'Nothing written.\n'.format(i, path, n))
            return 1
        by_path[path] = text.replace(old, new, 1)

    for path, text in by_path.items():
        with io.open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        print('patched {}'.format(path))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
