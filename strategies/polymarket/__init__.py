"""Polymarket prediction-market strategies (D-267, D-268).

Four strategies ported from moondevonyt's public Polymarket repo. His
thresholds are preserved; the MoonDev API dependency, the wallet client, his
account config, and the live order path are all removed. Data comes from
Polymarket's public read-only APIs and from public exchange feeds.

    from strategies.polymarket import build_strategies
    for s in build_strategies():
        decision = s.evaluate(ctx)   # always returns a Decision, never None

Every module here sets `PAPER_MODE = True` and every class carries
`paper_mode = True`. His originals ship `PAPER_MODE = False`.

## Status: every one of these is NOT_TESTED

None of the four has been through our graveyard. moondevonyt's win rates are
his numbers from his logs on his setup - hypotheses, not evidence (convention
3). The resolution-PnL harness extension does not exist yet, and running these
through the existing price-path harness would fabricate numbers (D-268).

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

All four are tightenings, all are logged in the module docstrings, and none
changes a threshold.

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
from strategies.polymarket.mid_price_continuation import MidPriceContinuation
from strategies.polymarket.streak_snapper import StreakSnapper


def build_strategies():
    """Fresh instances of all four. Order is stable for reproducible logs."""
    return [
        StreakSnapper(),
        MidPriceContinuation(),
        BoxBuilder(),
        CorridorCollector(),
    ]


__all__ = [
    'PolymarketStrategy', 'MarketContext', 'Decision', 'Leg', 'Window',
    'BINARY_STOP', 'BINARY_TARGET', 'PAPER_MODE',
    'streak', 'window_atr', 'cumulative_move', 'opposite', 'source_counts',
    'effective_ask_for', 'cap_bids', 'p_corridor_lookup',
    'StreakSnapper', 'MidPriceContinuation', 'BoxBuilder', 'CorridorCollector',
    'build_strategies',
]
