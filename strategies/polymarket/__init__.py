"""Polymarket prediction-market strategies (D-267, D-268).

Eight strategies. Five are ports from moondevonyt's public Polymarket repo, two
implement Forge proposals, and one (`fair_value_arb`) implements Dan1ro0
concepts 1-2 and is the only strategy in this package that does NOT hold to
resolution. His thresholds are preserved; the MoonDev API
dependency, the wallet client, his account config, and the live order path are
all removed. Data comes from Polymarket's public read-only APIs and from public
exchange feeds.

    from strategies.polymarket import build_strategies
    for s in build_strategies():
        decision = s.evaluate(ctx)   # always returns a Decision, never None

Every module here sets `PAPER_MODE = True` and every class carries
`paper_mode = True`. His originals ship `PAPER_MODE = False`.

## Status: every one of these is NOT_TESTED

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
this reason.

## Where our port deliberately differs from his bots

  streak_snapper      SKIPs when the ask is above the 52c cap. He rests a 52c
                      limit anyway and cancels after 60s - a maker fill we
                      cannot simulate. Also gates on the book-walked effective
                      entry, not top-of-book.
  mid_price_cont.     Gates the 0.40-0.55 band on the effective entry for the
                      full intended size, not the best ask.
  box_builder         Returns QUOTE, never ENTER. See its module docstring.
  corridor_collector  Adds a depth check on both legs before committing to
                      either; he discovers thin books as an UNPAIRED leg.
  spread_harvest      TAKER, not maker. This is not a tightening, it is a
                      DIFFERENT ORDER, and the strategy key says so
                      (`PM_spread_harvest_taker`). Read its module docstring
                      before quoting any result from it against his bot.

The first four are tightenings, all logged in the module docstrings, and none
changes a threshold. The fifth is not a tightening and is named accordingly.

## The Forge-proposal strategy, and the one that only LOOKED like one

  temporal_arbitrage           Proposal 002. Buys the two sides of ONE 5m window
                               at different instants, each when it is cheap, for
                               a pair that redeems 1.00. Between the legs it is
                               NAKED, and leg-completion risk - not direction -
                               is the whole trade.
  corridor_pair_live           RENAMED from `cross_window_relative_value`
                               (D-281). The module and the class carry the
                               `_live` suffix; the `strategy_name` key is
                               `PM_corridor_pair`, which is what D-281 ruled.
                               The old name claimed a lineage this file
                               does not have. It implements the FLOORED PAIR
                               structure, which is proposal 005's own "nearest
                               neighbour" and explicitly NOT its relative-value
                               hypothesis. Proposal 005 stays PROPOSED and
                               UNBUILT: it is blocked on 30 days of paired
                               history we do not have, and nothing here invents
                               the missing distribution. The name now says what
                               the code is - corridor_collector's structure, run
                               live off a lead we can actually measure. Its
                               module docstring is the authority.

## What each one needs before it can be scored

  PM_streak_snapper           taker, single leg. Ready for the harness
                              extension as-is.
  PM_mid_price_continuation   taker, single leg. Needs a live BTC spot feed
                              and the window's strike (a Chainlink TWAP read;
                              Gamma does not publish one).
  PM_box_builder              MAKER. Returns QUOTE, never ENTER. Needs a maker
                              fill model before it can be scored at all - see
                              its module docstring for why simulating resting
                              fills as taker fills would overstate it.
  PM_corridor_collector       taker, two legs across two markets. Needs the 15m
                              market context alongside the 5m, a 15m-vs-5m-open
                              lead, and an ATR14 quoted in BASIS POINTS.
  PM_temporal_arbitrage       taker, one leg per decision, two decisions per
                              pair. Needs the scorer to charge UNPAIRED legs to
                              the strategy at their realised resolution PnL, and
                              to compute completion rate by joining positions on
                              window_ts - NOT by counting ENTER decisions, which
                              this strategy cannot confirm became fills.
  PM_corridor_pair            taker, two legs across two markets. Needs the 15m
                              context and BTC 5m bars covering the 15m open. It
                              only ever fires on the FINAL third of a 15m
                              window; the $1.00 floor does not exist otherwise.
                              Needs 8c of edge below binned fair (D-281).
  PM_spread_harvest_taker     taker, single leg. Fires on NOTHING today: D-282
                              ships it with `allow_book_implied_coin_flip=False`
                              and Gamma publishes no strike, so its only live
                              gate is unreachable. Results from a sensitivity
                              run under `coin_flip_source='book_implied'` must
                              be scored SEPARATELY from any produced under
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
"""
from strategies.polymarket.base import (BINARY_STOP, BINARY_TARGET, PAPER_MODE,
                                        Decision, Leg, MarketContext,
                                        PolymarketStrategy, Window,
                                        cumulative_move, effective_ask_for,
                                        opposite, source_counts, streak,
                                        window_atr)
from strategies.polymarket.box_builder import BoxBuilder, cap_bids
from strategies.polymarket.corridor_collector import (CorridorCollector,
                                                      p_corridor_lookup)
from strategies.polymarket.corridor_pair_live import \
    CorridorPairLive
from strategies.polymarket.fair_value_arb import ExitDecision, FairValueArb
from strategies.polymarket.mid_price_continuation import MidPriceContinuation
from strategies.polymarket.spread_harvest_maker import SpreadHarvestMaker
from strategies.polymarket.streak_snapper import StreakSnapper
from strategies.polymarket.temporal_arbitrage import TemporalArbitrage


def build_strategies():
    """Fresh instances of all eight. Order is stable for reproducible logs.

    New strategies are APPENDED, never inserted. The shadow loop's accounting
    identity is `evaluations == cycles * len(strategies)`, so a reordering
    would not break it - but every historical log line is keyed by position in
    somebody's head, and appending keeps a diff of the counters readable.

    Fresh instances matter more than it looks: `TemporalArbitrage`,
    `SpreadHarvestMaker` and `FairValueArb` carry per-window state, so two
    callers sharing one instance would share a block ledger. `FairValueArb`
    additionally carries a BTC price TAPE, and two loops feeding one tape would
    interleave their observations into a series neither of them saw.
    """
    return [
        StreakSnapper(),
        MidPriceContinuation(),
        BoxBuilder(),
        CorridorCollector(),
        TemporalArbitrage(),
        CorridorPairLive(),
        SpreadHarvestMaker(),
        FairValueArb(),
    ]


__all__ = [
    'PolymarketStrategy', 'MarketContext', 'Decision', 'Leg', 'Window',
    'BINARY_STOP', 'BINARY_TARGET', 'PAPER_MODE',
    'streak', 'window_atr', 'cumulative_move', 'opposite', 'source_counts',
    'effective_ask_for', 'cap_bids', 'p_corridor_lookup',
    'StreakSnapper', 'MidPriceContinuation', 'BoxBuilder', 'CorridorCollector',
    'TemporalArbitrage', 'CorridorPairLive', 'SpreadHarvestMaker',
    'FairValueArb', 'ExitDecision',
    'build_strategies',
]
