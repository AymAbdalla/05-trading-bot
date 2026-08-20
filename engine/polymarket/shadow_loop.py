"""Polymarket paper/shadow trading loop (D-267, D-268; Raven's SHADOW GO).

Polls the live BTC Up/Down 5-minute market, evaluates the four Polymarket
strategies against a real CLOB book every few seconds, simulates taker fills
through `PolymarketPaperAdapter`, and persists every decision to
`db/trading.db` and to the adapter's CSV.

## PAPER MODE, structurally

`PAPER_MODE` below is an unconditional `True` and the loop refuses to build if
it is not. This module imports no wallet, no signer, and no order SDK. The only
client it touches is `PolymarketClient`, which exposes no verb but GET, and the
only execution path is `PolymarketPaperAdapter`, which has none. The risk gate
is called with `mode='paper'` and `PolymarketRiskGate.check_order` blocks
anything else outright. That is four independent refusals, not one flag.

## The kill switch, and what a halt CANNOT do here

`engine.halt.is_halted()` is consulted before every entry, by this loop AND
again inside the adapter. Neither check is redundant: the loop's check exists so
a halted window is counted and categorised as `halted` rather than disappearing
into an anonymous adapter refusal, and the adapter's exists because the adapter
is the only place a position can be opened and a safety check belongs at the
boundary it protects.

A halt here blocks NEW ENTRIES ONLY. It does not and cannot flatten. A binary
held to resolution has no sell path in paper mode - the position is worth its
premium until the oracle speaks and then exactly $1.00 or exactly $0.00 - so
there is nothing to close. This is the documented asymmetry with the crypto
executor, where HALT also flattens. Do not read a halted Polymarket session as a
session with no exposure.

Resolution bookkeeping deliberately continues during a halt. Skipping it would
leave positions PENDING that the oracle has already decided, and an operator
would read a halted session's PnL with the losses missing.

## Convention 20: every window is a counted, categorised row

Every (cycle, strategy) pair is one EVALUATION and every evaluation lands in
exactly one bucket, entry or a named skip. Cycle-level failures (no market, no
book, an API outage) are attributed to each strategy individually rather than
short-circuiting the cycle, so the accounting identity

    evaluations == entries + sum(skips)          (and == cycles * n_strategies)

holds on every flush and is asserted there. A violated identity is logged at
ERROR and written to `audit_log` as `accounting_violation`; it is never
silently repaired by adjusting a counter to match.

The skip taxonomy keeps drop causes separate on purpose:

    no_market       Gamma has no market for this window (a 200 with an empty
                    list). Normal in the first seconds of a window.
    api_error       A read FAILED. Carries the consecutive-failure count. This
                    is never merged with no_market: "the venue said no such
                    market" and "we could not reach the venue" need opposite
                    responses (convention 11).
    no_liquidity    The market exists and the book came back empty, one-sided
                    or crossed. Distinct from api_error above it and from the
                    adapter's `book_above_limit` below it.
    halted          The kill switch is engaged. Entry blocked, loop continues.
    risk_gate:...   The gate's own reason string, verbatim, never re-worded.
    adapter:...     The paper adapter's own reason, verbatim, taken from its
                    decision_counts delta.
    strategy:...    The strategy's own gate. This is the strategy WORKING.
    cycle_exception The loop caught something unexpected. Counted, categorised,
                    never fatal.

## What can actually fire, and what cannot (read before reading the log)

THE LIST IS THE AUTHORITY, not a number written here.
`strategies.polymarket.build_strategies()` is what this loop runs, one full set
per asset, and `len(self.strategies)` is the per-asset count the accounting
identity multiplies. That count has drifted repeatedly (it was EIGHT when this
section was first written, then eleven, then fifteen; it is NINETEEN as of
2026-08-18) and it will drift again, so read it off the list -

    env -u PYTHONPATH python3 -c "import strategies.polymarket as p; print(
        len(p.build_strategies()))"

- rather than trusting a figure in prose, here or anywhere else. For the same
reason no "N of them can fire" total is stated below: the per-strategy notes are
the claim, and a summed headline would rot independently of them.

Each note says whether an ENTRY is reachable in THIS loop. CANNOT always means
blocked by missing DATA or by a refusal we chose, never "we ran it and it
declined" (convention 11):

  PM_streak_snapper          CAN fire. Needs 16 price windows with USD
                             magnitudes; supplied from Binance.US 5m candles.
  PM_temporal_arbitrage      CAN fire. Needs spot, atr14, and the 5m price bar
                             whose timestamp equals this window's - all three
                             are supplied. Buys ONE leg per decision and holds
                             a NAKED position between them.
  PM_corridor_pair           CAN fire, on one 5m window in three. It refuses
                             any window that is not the FINAL THIRD of its 15m
                             parent (`not_final_third_of_15m`), because the
                             pair's $1.00 floor only exists when both markets
                             settle off the same close. It computes its own 15m
                             lead from the bar that opened the 15m window, so
                             unlike corridor_collector it needs no strike.
  PM_spread_harvest_taker    CAN fire, on its BOOK-IMPLIED near-tie gate. With
                             no strike there is no cushion and no `coa`, so the
                             0.40-0.48 price band does the near-tie work. That
                             is a DIFFERENT gate from his, not a looser one -
                             every row carries `coin_flip_source` and the two
                             populations must never be pooled.
  PM_fair_value_arb          CAN fire. Needs spot, both books, and the 5m bar
                             whose timestamp equals this window's. It is the
                             only strategy here that EXITS BEFORE RESOLUTION -
                             see the exit-management section below.
  PM_mid_price_continuation  CANNOT. Needs the window's strike, which is a
                             Chainlink 60s TWAP that Gamma does not publish.
                             We refuse to substitute spot (see
                             `context.CRYPTO_CONFIG_KEY`) because spot is wrong
                             precisely mid-move, which is when this strategy
                             decides. Skips `no_spot_or_strike` forever.
  PM_corridor_collector      CANNOT, same missing strike: no strike means no
                             `lead_bps`. Skips `no_lead_or_atr` forever. The
                             ATR half of that gate IS supplied.
  PM_box_builder             CAN fill, since 2026-08-18, and it still never
                             ENTERS. It is a MAKER strategy and returns QUOTE;
                             this loop now RESTS those legs
                             (`_attempt_maker_quotes`) and a later snapshot that
                             crosses STRICTLY through the quoted price fills
                             them (`observe_maker_orders`). The evaluation is
                             counted `maker_quote_rested`, which is a SKIP - a
                             rest is not a fill. The fill lands in
                             `maker_counts`, outside the identity. The old
                             single bucket `maker_quote_not_simulable` is gone;
                             it pooled eight distinct causes.

  PM_fair_value_arb_wide     CAN fire, all four. ONE hypothesis tested four
  PM_fair_value_arb_patient  ways, not four hypotheses - see the package
  PM_fair_value_arb_hft      docstring. They share fair_value_arb's data
  PM_fair_value_arb_inverse  requirements and its shape, and like it they EXIT
                             BEFORE RESOLUTION, so they belong in the SELL
                             population and must never be pooled with the
                             resolution population.
  PM_liq_cascade_chaser      NOT CHARACTERISED HERE. These three read our own
  PM_small_liq_continuation  recorder tables (`liquidations`,
  PM_near_liq_trigger        `hyperliquid_positions`) rather than anything this
                             loop fetches, so whether an entry is reachable is a
                             question about whether those recorders are running
                             and populated, not about this loop's context. No
                             claim either way is made here rather than guessing
                             one; see their own module docstrings.
  PM_smart_money_copy        CANNOT, twice over. All seven tracked wallet
                             handles resolve to no address - two of them,
                             `0x50f7` and `0xaaaaa`, are 4-hex PREFIXES and not
                             addresses at all - so it refuses
                             `wallet_address_unresolved` before any network call
                             is made. And even given a real address, Polymarket's
                             public `/trades` returns FILLS, not outcomes, so the
                             win-rate gate refuses `wallet_record_unmeasured`.
                             Both are NOT_TESTED (convention 11), not
                             tested-and-found-nothing.
  PM_grid_hedge              CAN fill, exactly like box_builder and by the same
                             route. It is a MAKER strategy and returns QUOTE,
                             never ENTER, and a module-level `assert_not_enter`
                             RAISES on an ENTER, so that half is a WIRING TEST
                             and not a docstring claim (convention 22). Its kill
                             condition needs 50 grid FILLS and is still
                             unmeasured: a fill MODEL existing is not 50 fills
                             (convention 11). Its ladder is up to 10 rungs and
                             the maker budget is 2, so most rungs are refused
                             `maker_rest_budget_exhausted` - a counted, named
                             refusal, not a silent drop. It also declares
                             `needs_strike = True`, so with no strike it is
                             stopped by the strike gate FIRST and reports
                             `no_spot_or_strike` rather than anything maker-
                             shaped - two independent blocks, and which one you
                             see is the ordering, not a change of cause.
  PM_weather_arb             CAN fire in principle, and that is the strongest
                             thing that can honestly be said. Its station table
                             is an unverified ASSUMPTION and its sigma model was
                             never fitted. It also needs live weather markets to
                             exist on Gamma; when none match it skips
                             `no_weather_market`, which is NOT_TESTED.
  PM_dip_arb                 CAN fire. It EXITS BEFORE RESOLUTION like the
                             fair-value family, so it belongs in the SELL
                             population and must never be pooled with the
                             resolution population. It needs NO fair value - it
                             exits against its own rolling tape mean. It now
                             carries a deliberately NEVER-USABLE `estimate()`
                             so that `manage_exit` keeps reading its own tape;
                             see the `exit_no_fair_value_protocol` block in
                             `__init__` for the open conflict between that and
                             this loop's capability dispatch, which needs a
                             D-number.

The CANNOTs above are NOT_TESTED, not tested-and-found-nothing (convention 11).
The loop names them in its own counters so a zero-entry session cannot be
mistaken for a session where the strategies looked and declined.

STALE-CLAIM WARNING on the two strike entries above: they were written before
`engine/polymarket/strike.py` existed. A MEASURED proxy strike is supplied now
and enforced behind `STRIKE_PROXY_NOISE_FLOOR_BPS`, so "forever" no longer
holds - see `build_context`'s docstring for what the proxy is and what its error
was measured to be. Inside the noise floor they skip `strike_inside_proxy_noise_floor`,
which is its own reason and is never pooled with a market condition.

## Exit management, and why it sits OUTSIDE the accounting identity

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

## The maker path, and the one thing that makes it possible

A QUOTE decision is rested, not filled. `_attempt_maker_quotes` calls
`PolymarketPaperAdapter.simulate_maker_buy`, which returns a `RestingOrder` and
opens nothing. `observe_maker_orders` runs at the top of every cycle and hands
that cycle's books to every order still on the book; only a snapshot showing
size resting STRICTLY BELOW our bid fills us, and only for whatever got past the
queue that was ahead of us at rest time.

**The order survives the cycle because the ADAPTER does.** `self.adapter` is
built once in `__init__` and `adapter.resting_orders` is a dict on it, so an
order placed in cycle N is still there in cycle N+1. That is the whole reason a
maker fill is representable here at all, and it is why `observe_maker_orders`
must stay in `run_cycle`: without it, orders would rest forever, never fill,
never expire, and the two maker strategies would report activity and no results.

Three counter spaces, three different facts, and none of them pooled:

    counts          the EVALUATION's disposition. `maker_quote_rested` is a
                    SKIP: at decision time nothing has been bought.
    maker_counts    what happened to RESTING ORDERS. `fill:*`, `expire:*`,
                    `cancel:*`, plus `observed` and `still_resting`. Outside
                    the identity - a resting order is not a window.
    health          our own wiring faults (`maker_leg_no_book`,
                    `maker_adapter_refusals`, `maker_partial_quotes`).

**The honest limitation, stated so nobody has to rediscover it.** We have no
trade prints, only book snapshots ~5 seconds apart. A fill that happened and
reversed between two polls is invisible and is scored as a no-fill. The model is
therefore PESSIMISTIC, which is the right direction to be wrong in for a
strategy whose entire claimed edge is "our resting order got hit".

## `timings` holds SECONDS and is outside the identity, permanently

`self.timings` is a third counter space alongside `health` and `exit_counts`,
and it is the one most likely to be folded into something by accident, because
it is a Counter and Counters get summed. It holds WALL-CLOCK DURATIONS keyed
`<step>` with a companion `<step>_calls`. A duration is not an evaluation, so
nothing in it may ever reach

    evaluations == entries + sum(skips) == cycles * n_strategies * n_assets

Read it through `timing_report()`, which splits total from call count from
per-call average - a step that is slow and a step that merely ran a lot produce
the same total and need different fixes.

## Fetches inside one context build run CONCURRENTLY

`build_context` issues its reads in three stages rather than seven sequential
round trips. Stage 1 is the 5m market lookup and cannot be parallelised: the
token ids the books are keyed by come out of its response. Stage 2 fans out to
both 5m books, the spot read and the 15m market lookup. Stage 3 fans out to the
15m books. Threads, not asyncio - see `DEFAULT_FETCH_WORKERS` for why, and
`_run_parallel` for the written-down reasoning about sharing one
`requests.Session` across them.

Parallelism is ON by default and switchable OFF with `parallel_fetches=False`,
so a suspected concurrency bug can be bisected by flipping a constructor flag
instead of reverting a patch during an incident. The per-token status taxonomy
is unchanged by it (`ok` / `api_error` / `no_liquidity`) except for one ADDED
value, `fetch_exception`, which exists precisely so that a thread that raised
can never be recorded as a venue outage.

## Two strategies now carry state, and this loop cannot confirm their fills

`PM_temporal_arbitrage` and `PM_spread_harvest_taker` remember what they
attempted in a window. They see DECISIONS, never fills: the halt check, the
risk gate and the adapter all sit downstream of `evaluate()` and any of them
can refuse. So a leg this loop blocked is still recorded by the strategy as
attempted, and temporal_arbitrage will go looking for its second leg.

The consequence that matters: temporal_arbitrage's completion rate CANNOT be
computed by counting ENTER decisions or `entry` counters here. It has to come
from a join of the `positions` table on window_ts. Every one of its decision
rows carries `completion_rate_measurable_from_this_log = False` so a reader
cannot reach the wrong number by accident. Wiring a fill callback back into the
strategies is the fix and it is not built - convention 22, a docstring saying
otherwise would not be a wiring test.
"""
import argparse
import concurrent.futures
import dataclasses
import functools
import json
import logging
import math
import os
import signal
import sqlite3
import threading
import time
import traceback
import uuid
from collections import Counter
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from engine.db import get_db_path
from engine.halt import is_halted
from engine.risk import constraints as risk_constraints
from engine.risk import events as risk_events
from engine.polymarket.assets import (SHADOW_ASSETS, asset_for_slug,
                                      get_asset, market_duration_for_slug)
from engine.polymarket.client import PolymarketClient
from engine.polymarket.strike import (ERROR_UNAVAILABLE_FLAG,
                                     NOISE_FLOOR_ERROR_BY_ASSET,
                                     NOISE_FLOOR_SOURCE_ASSET,
                                     STRIKE_PROXY_NOISE_FLOOR_BPS,
                                     StrikeProxy, error_at_floor_pct_for,
                                     NOISE_FLOOR_ERROR_MEASURED_AT_BPS,
                                     active_floor_error_pct_for,
                                     disagreement_pct_for_lead,
                                     noise_floor_bps_for,
                                     set_noise_floor_bps_by_asset,
                                     error_sample_at_floor_for,
                                     is_inside_noise_floor)
from engine.polymarket.context import (fetch_btc_spot_checked,
                                       fetch_spot_checked,
                                       price_windows_checked)
from engine.polymarket.markets import (UPDOWN_5M_DURATION,
                                       UPDOWN_15M_DURATION,
                                       current_window_ts,
                                       get_btc_updown_5m_checked,
                                       get_market_by_slug_checked,
                                       get_updown_5m_checked,
                                       search_event_markets_checked,
                                       search_political_markets_checked,
                                       search_sports_markets_checked,
                                       updown_15m_slug)
from engine.polymarket.orderbook import orderbook_from_api
from engine.polymarket.resolution_ledger import ResolutionLedger
from engine.polymarket.paper_adapter import (MAKER_FILL_MODEL, ORDER_EXPIRED,
                                             PolymarketPaperAdapter)
from engine.polymarket.risk_gate import PolymarketRiskGate
from engine.polymarket.types import WINNING_REDEMPTION
from strategies.polymarket import build_strategies
from strategies.polymarket.base import (MARKET_TYPE_CRYPTO_UPDOWN,
                                        MARKET_TYPE_EVENT,
                                        MARKET_TYPE_POLITICAL,
                                        MARKET_TYPE_SPORTS,
                                        MARKET_TYPE_WEATHER, MarketContext,
                                        window_atr)
from strategies.polymarket.dip_arb import (PriceTapeByToken,
                                           observe_market_into_tape)
from strategies.polymarket.weather_arb import set_weather_config

logger = logging.getLogger(__name__)

# Unconditional, checked in the constructor. There is no config key, no
# environment variable and no argument that flips it.
PAPER_MODE = True
MODE = 'paper'

#: Raven's directive: a $1,000.00 simulated bankroll. This deliberately
#: OVERRIDES `config.yaml`'s `polymarket.starting_equity_usdc` (2000.0), and it
#: overrides it for the risk gate's bankroll too so the two can never disagree
#: about how much money this session has.
DEFAULT_STARTING_EQUITY_USDC = 1000.0

DEFAULT_POLL_SEC = 5.0
DEFAULT_EQUITY_SNAPSHOT_SEC = 300.0     # every 5 minutes
DEFAULT_RESOLVE_SEC = 60.0

#: D-343 R2 (Raven, under Aym's full-authority directive). The REAL-money
#: drawdown default lives in `engine.risk.constraints.DEFAULT_LIMITS`
#: (max_drawdown_frac=0.25) and stays there UNEDITED - this override exists
#: only while the book is the shadow measurement 026/037/038 depend on.
#: Measured on this book's own `equity_snapshots`: max drawdown from running
#: peak is 35.99%, and a 25% halt would have fired 3 times (8 times at 10%);
#: only >=40% never fires on the current tape. Remove this override (fall
#: back to `risk_constraints.DEFAULT_LIMITS` directly) the day a strategy
#: demonstrates calibrated edge and the book moves toward live - that is a
#: decision for Raven and Aym, not a default to flip silently.
#:
#: A-17 (Aym ruling 2026-08-20): auto-halt DISABLED in shadow mode. The
#: drawdown guard fights the measurement goal in shadow (D-358: resume,
#: keep measuring, fund-if-zero). max_drawdown_frac=1.0 means the halt
#: NEVER fires for drawdown in shadow — the book runs until it zeroes
#: and gets re-funded. The real-money default (0.25) is UNCHANGED.
SHADOW_RISK_LIMITS = dataclasses.replace(
    risk_constraints.DEFAULT_LIMITS, max_drawdown_frac=1.0)

#: How often the settlement resolution ledger sweeps its pending markets
#: (Forge proposal 038). Matched to the resolve cadence rather than the poll
#: cadence: a 5-minute window cannot close more than once a minute, and the
#: ledger's own cache holds an unresolved market for 120s anyway, so sweeping
#: every poll would spend cycles to re-read the same cache entry. The sweep
#: runs AFTER every trading phase and never raises.
DEFAULT_RESOLUTION_SWEEP_SEC = 60.0
DEFAULT_STATS_FLUSH_SEC = 60.0
DEFAULT_CANDLE_REFRESH_SEC = 60.0

#: How often the WEATHER cycle runs: discovery, then one decision per selected
#: temperature market. 60 seconds, and it is deliberately 12x the crypto poll.
#:
#: The binding constraint is upstream, not us. A METAR station issues an
#: observation about every 30 minutes and open-meteo refreshes its blended model
#: roughly hourly, so polling a temperature market every 5 seconds would re-ask
#: the same two questions 720 times per new answer. The books do move faster
#: than that, but this strategy holds to resolution on a market that settles at
#: the end of a calendar day: a fill one minute later is not a different trade.
DEFAULT_WEATHER_CYCLE_SEC = 60.0

#: How many temperature markets get an orderbook read per weather cycle.
#:
#: MEASURED, not guessed. Live 2026-08-18, discovery returned 1,090 markets of
#: which 1,034 carried a readable station, a parseable threshold and a daily
#: extreme. Two book reads each would be 2,068 CLOB requests per cycle; at a
#: 60-second cadence that is 34 req/s against a 7,200-per-10s budget, so it
#: would not breach the limiter - but it would spend the entire poll on rungs of
#: the same few ladders, since one city's ladder is eleven markets standing on
#: one station and one forecast.
#:
#: 8 is 16 book reads per minute. `rank_weather_markets` orders by volume AFTER
#: filtering, so the budget lands on the deepest books rather than on whichever
#: page Gamma returned first. Convention 17: an assumption with an expiry date.
#: Raise it against a measured request count, never because it felt small.
DEFAULT_WEATHER_MARKET_LIMIT = 8

#: Cap on how many discovered markets are even considered for ranking. Purely a
#: runaway guard on a pure-string filter that costs no network; the live board
#: is about 1,090 and this is not expected to bind.
DEFAULT_WEATHER_DISCOVERY_LIMIT = 1500

# -- the GENERAL BINARY market spaces: event, sports, political (D-313) -------
#
# Same shape as the weather cycle and for the same reason: a universe whose size
# is a property of the BOARD rather than of our configuration cannot live inside
# the crypto cycle's fixed rectangle without destroying the identity that
# catches a dropped decision. So each gets its own counters, its own evaluation
# total, its own identity check and its own cadence.
#
# What is NOT duplicated is the code. `run_space_cycle` is one implementation
# driven by a `MarketSpace` record; event, sports and political differ only in
# their discovery query. Convention 23 - three copies of a cycle is three places
# for the accounting to drift, and the weather cycle already showed how much
# accounting a cycle carries.

#: How often each general-binary space runs. 60s, matching weather rather than
#: the 5s crypto poll.
#:
#: The justification is NOT the same as weather's, so it is not shared with it.
#: Weather is slow because its INPUTS are slow (a METAR every 30 minutes). These
#: books move continuously. The reason here is cost and honesty about what we
#: can act on: an event or sports market resolves in hours or days, so a fill one
#: minute later is the same trade, and polling three universes at 5s would
#: triple our Gamma request rate to chase a difference no strategy here can use.
#: Convention 17: an assumption with an expiry date. The measurement that would
#: move it is the realised slippage between the price at decision time and the
#: price one cycle later.
DEFAULT_SPACE_CYCLE_SEC = 60.0

#: How many markets per space get an orderbook read per cycle. Two book reads
#: each, so 6 spaces x 6 markets x 2 = at most 72 CLOB reads a minute, which is
#: 1.2/s against a 7,200-per-10s budget.
DEFAULT_SPACE_MARKET_LIMIT = 6

#: How many rows Gamma is asked for per space before filtering. Gamma caps a
#: page at 100.
DEFAULT_SPACE_DISCOVERY_LIMIT = 100

#: Dollar volume a market must EXCEED to be polled. Raven's task file specifies
#: ">$10K volume" for sports; applied to all three spaces for consistency, and
#: it is the same floor `search_event_markets` already defaults to.
DEFAULT_SPACE_MIN_VOLUME_USDC = 10000.0

#: Disposition strings for a space cycle. One per cause, never pooled
#: (convention 20). Deliberately parameterised by space name rather than shared,
#: so "sports discovery failed" and "political discovery failed" are two facts.
SPACE_DISCOVERY_FAILED = 'discovery_read_failed'
SPACE_NO_MARKET_LISTED = 'no_market_listed'
SPACE_NONE_POLLABLE = 'no_pollable_market'
SPACE_NO_BOOK = 'no_orderbook'
SPACE_CYCLE_EXCEPTION = 'cycle_exception'
SPACE_DISABLED = 'disabled'
SPACE_NO_STRATEGY = 'no_strategy_supports_this_market_type'

#: The reasons above, as they appear in the counters: `{space}_{reason}`.
SPACE_STATUSES = (SPACE_DISCOVERY_FAILED, SPACE_NO_MARKET_LISTED,
                  SPACE_NONE_POLLABLE, SPACE_NO_BOOK, SPACE_CYCLE_EXCEPTION,
                  SPACE_DISABLED, SPACE_NO_STRATEGY)


def space_status(space_name: str, status: str) -> str:
    """`('sports', 'no_orderbook')` -> `'sports_no_orderbook'`.

    Namespaced so two spaces failing for the same reason are two counters. A
    shared `no_orderbook` bucket across sports and political would answer "which
    universe has no books" with a single number that describes neither.
    """
    return '{}_{}'.format(space_name, status)

#: Width of the per-cycle fetch executor. THREADS, not asyncio: `client.py` is
#: synchronous `requests`, and converting it to aiohttp would be a rewrite of
#: the one module whose GET-only shape is the structural safety argument for
#: this entire package. Four is the widest independent fan-out any single stage
#: of `build_context` has (two 5m books + spot + the 15m market lookup), so a
#: larger pool would only create threads with nothing to do.
#:
#: RATE LIMITER: `client.RateLimiter.acquire` takes a lock and is thread-safe,
#: and the post-headroom budgets are 3,200 req/10s (Gamma) and 7,200 req/10s
#: (CLOB). Three assets at a 5-second poll issue roughly 21 reads per cycle,
#: about 4.2/s. A width of 4 cannot burst past a limiter whose budget is two
#: orders of magnitude above our steady state, so the executor needs no
#: throttle of its own beyond what the limiter already enforces.
DEFAULT_FETCH_WORKERS = 4

#: TTL for the per-asset spot cache, on a MONOTONIC clock, because a wall clock
#: can step backwards under NTP and a cache keyed on one would then hold an
#: entry it believes is from the future - forever.
#:
#: HONEST NOTE, do not oversell this: at the current call pattern spot is
#: ALREADY fetched exactly once per asset per cycle. Strategies hold no client
#: and structurally cannot fetch (see `context.py`'s module docstring), so there
#: is no per-strategy duplication to remove and this TTL saves ZERO round trips
#: at a 5-second poll. It exists for two other reasons: it bounds the damage if
#: the poll interval is ever lowered below the TTL, and it forces every spot
#: reading to carry an AGE. A cache without an age stamp is a lie.
DEFAULT_SPOT_CACHE_TTL_SEC = 2.0

#: Bounded exponential backoff on consecutive API failures. Bounded because an
#: unbounded backoff on a 5-minute market is indistinguishable from a dead loop.
MAX_BACKOFF_SEC = 60.0

WINDOW_LOOKBACK = 16
CANDLE_PAIR = 'BTC/USDT'          # BTC's pair; per-asset pairs live in assets.py
CANDLE_TF = '5m'
ATR_WINDOWS_FOR_CORRIDOR = 14   # corridor_collector reads ctx.atr14

BTC_UPDOWN_15M_SLUG = 'btc-updown-15m-{ts}'
BTC_UPDOWN_15M_DURATION = 900

#: Dispositions that are not a passthrough of somebody else's reason string.
#: Named here so a typo is a NameError rather than a new bucket that silently
#: splits one count into two.
SKIP_NO_MARKET = 'no_market'
SKIP_API_ERROR = 'api_error'
SKIP_NO_LIQUIDITY = 'no_liquidity'
SKIP_HALTED = 'halted'
SKIP_CYCLE_EXCEPTION = 'cycle_exception'
SKIP_NO_LEGS = 'enter_without_legs'
SKIP_UNKNOWN_TOKEN = 'unknown_outcome_token'
# The strike is a proxy with a MEASURED error, and this is what a strategy gets
# when its signal is inside that error. It must never pool with a real market
# condition (convention 20): "the move was too small for our instrument to see"
# and "the move was too small to trade" are different facts, and only the second
# one is a result. A strategy skipped for this reason has NOT been tested on
# that window (convention 11).
SKIP_PROXY_NOISE = 'strike_inside_proxy_noise_floor'

#: -- the MAKER path's dispositions ------------------------------------------
#:
#: `maker_quote_not_simulable` is GONE. It was the short-circuit: every QUOTE
#: decision was counted under it and the legs were thrown away, so both maker
#: strategies produced one number forever and that number described this loop,
#: not them. `simulate_maker_buy` has existed in the paper adapter since
#: 2026-08-18 and nothing called it. Now this loop does.
#:
#: A QUOTE is still NEVER an entry. Resting is not filling: `_attempt_maker_quotes`
#: rests the legs and the evaluation is counted as one of the reasons below,
#: while the FILL (if any) happens one or more cycles later in
#: `observe_maker_orders` and lands in `maker_counts`, outside the identity.
#:
#: Every one of these is a DISTINCT drop cause and none of them shares a
#: counter with another (convention 20). In particular `maker_halted` is not
#: `halted`: the kill switch refuses a taker ENTRY, and on this path it both
#: refuses the rest AND cancels the buys already resting, which is a different
#: observable consequence and needs its own number.
SKIP_MAKER_RESTED = 'maker_quote_rested'
SKIP_MAKER_HALTED = 'maker_halted'
SKIP_MAKER_NO_LEGS = 'maker_quote_without_legs'
SKIP_MAKER_ALREADY_RESTING = 'maker_quote_already_resting'
SKIP_MAKER_BUDGET = 'maker_rest_budget_exhausted'
#: The adapter's own refusal taxonomy, carried verbatim behind this prefix so
#: our counters report the adapter's reason rather than our guess at it. It is
#: a SEPARATE prefix from `adapter:` (the taker path) on purpose: a maker refused
#: for `maker_would_cross_book` and a taker refused for `over_notional_cap` are
#: not the same finding and pooling them under one prefix would hide which path
#: is being refused.
MAKER_ADAPTER_PREFIX = 'maker_adapter:'

#: How many of `max_concurrent_positions` the maker strategies may hold at once.
#:
#: This is not decoration. The adapter's `committed_slots()` counts resting BUYS
#: against the SAME cap as open positions, by design (a resting bid is a position
#: the moment it is crossed into). `PM_box_builder` quotes two legs per asset and
#: `PM_grid_hedge` up to ten, on three assets, every 5-7 seconds - so without a
#: budget the first cycle that quotes fills all 5 slots and every one of the 17
#: taker strategies is refused `max_concurrent_positions` for the rest of the
#: session. Wiring the maker path on must not silently turn the taker path off.
#:
#: D-362 R3, 2026-08-20: REMOVED - set to the 100_000 SENTINEL. Aym: "remove
#: market order budget." The whole justification above is downstream of a
#: FIVE-SLOT `max_concurrent_positions`, and D-360 removed that cap. With no
#: count cap there are no slots for maker quotes to starve takers of, so this
#: budget was refusing `maker_rest_budget_exhausted` for a reason that no
#: longer exists. `SKIP_MAKER_BUDGET` stays wired: it is now reachable only by
#: someone configuring `max_resting_maker_orders` down again, which is the
#: point of leaving the mechanism in place. Restore a small integer here the
#: day a real count cap comes back.
DEFAULT_MAX_RESTING_MAKER_ORDERS = 100_000

#: The WEATHER cycle's own cycle-level dispositions. Named here for the same
#: reason the ones above are: a typo becomes a NameError rather than a new
#: bucket that silently splits one count into two.
#:
#: These live in `weather_counts`, never in `counts`. Convention 20 is the whole
#: point of the split: "Gamma was unreachable", "Gamma answered and listed no
#: temperature markets", "we found markets but none was worth a book read" and
#: "we read the book and nobody is quoting" are four different facts, and the
#: first three used to be indistinguishable because the weather cycle did not
#: exist and `PM_weather_arb` was simply handed a BTC window instead.
WX_DISCOVERY_FAILED = 'weather_discovery_read_failed'
WX_NO_MARKET_LISTED = 'weather_no_market_listed'
WX_NONE_POLLABLE = 'weather_no_pollable_market'
WX_NO_BOOK = 'weather_no_orderbook'
WX_CYCLE_EXCEPTION = 'weather_cycle_exception'
WX_DISABLED = 'weather_disabled'

#: A fetch thread RAISED. This is a fault in our own code, not a fact about the
#: venue, and it must never merge with `api_error` ("we reached the venue and
#: the read failed") or with `no_liquidity` ("nobody is quoting"). Convention
#: 20: two drop causes never share one number. This ADDS a fourth value to the
#: per-token status taxonomy; it does not collapse the three already there, and
#: it cannot occur at all on the sequential path.
STATUS_FETCH_EXCEPTION = 'fetch_exception'


def _ms(seconds: Optional[float] = None) -> int:
    """Unix milliseconds. Every ts column in db/schema.sql is in ms."""
    return int((time.time() if seconds is None else seconds) * 1000)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def window_ts_from_slug(slug: Optional[str]) -> Optional[int]:
    """Trailing epoch off a window slug, or None.

    btc-updown-15m-1787064300 -> 1787064300. The trailing integer IS the
    window START (verified: entries land 1-241s into a 300s window), so the
    close is this plus the duration, never this alone.

    Returns None rather than guessing on a slug with no numeric tail. A
    weather slug has no window and must not come back as one.
    """
    if not slug:
        return None
    tail = str(slug).rsplit('-', 1)[-1]
    return int(tail) if tail.isdigit() else None

class ShadowStore:
    """SQLite writer for the shadow loop.

    WAL mode and one short transaction per write, because `dashboard/` opens the
    same file read-only (R-010) and a long-running write transaction would make
    the dashboard read stale or block. `busy_timeout` is set so a concurrent
    reader's checkpoint never turns into an exception that kills the loop.

    Every column written below already exists in `db/schema.sql`. `ensure_schema`
    replays that file, whose statements are all `CREATE ... IF NOT EXISTS` and
    are therefore idempotent; nothing here renames or migrates an existing
    column.
    """

    SCHEMA_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'db', 'schema.sql')

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_db_path()
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=15.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL;')
        self.conn.execute('PRAGMA synchronous=NORMAL;')
        self.conn.execute('PRAGMA busy_timeout=10000;')
        self.ensure_schema()

    #: Forge proposal 030 stage 1 (pm_one_legged_pair_unwind_guard). Added to
    #: `positions` here rather than only in db/schema.sql, because
    #: `CREATE TABLE IF NOT EXISTS` is a no-op against a `positions` table
    #: that already exists on disk - every live and every test database
    #: predates these columns, and schema.sql's copy of the DDL only helps a
    #: database that does not exist yet. `(name, sql_type)`; every one is
    #: nullable, so adding it changes no existing row and no existing reader.
    _POSITIONS_PAIR_LINKAGE_COLUMNS = (
        ('pair_id', 'TEXT'),
        ('leg_index', 'INTEGER'),
        ('leg_target_px', 'REAL'),
        ('leg_fill_px', 'REAL'),
        ('leg_fill_ts', 'INTEGER'),
        ('leg2_latency_ms', 'REAL'),
        ('pair_cost_expected', 'REAL'),
        ('pair_cost_actual', 'REAL'),
        ('leg_bid_at_signal', 'REAL'),
        ('leg_ask_at_signal', 'REAL'),
        ('leg_bid_at_fill', 'REAL'),
        ('leg_ask_at_fill', 'REAL'),
    )

    def _migrate_positions_pair_linkage_columns(self) -> None:
        """`ALTER TABLE ADD COLUMN` for any of the above missing on disk.

        Must run BEFORE `executescript(schema.sql)` below, not after:
        schema.sql also declares `idx_positions_pair_id`, and
        `CREATE INDEX ... ON positions(pair_id)` against a `positions` table
        that predates this column raises `OperationalError: no such column`
        immediately - `CREATE TABLE IF NOT EXISTS` silently no-ops on an
        existing table, but the index statement right after it does not.
        Running the ALTER first means the column already exists by the time
        that statement runs, on an old db and a fresh one alike.

        A `positions` table that does not exist yet is left alone: schema.sql
        creates it a moment later WITH these columns already in the
        `CREATE TABLE`, so there is nothing to migrate.

        SQLite has no `ADD COLUMN IF NOT EXISTS`, so the guard is
        `PRAGMA table_info` read first. Idempotent and cheap: `positions` is
        low thousands of rows, not millions, and this runs once per process
        start.
        """
        table_exists = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='positions'").fetchone()
        if not table_exists:
            return
        existing = {row[1] for row in
                    self.conn.execute('PRAGMA table_info(positions)')}
        for name, sql_type in self._POSITIONS_PAIR_LINKAGE_COLUMNS:
            if name in existing:
                continue
            self.conn.execute(
                'ALTER TABLE positions ADD COLUMN {} {}'.format(
                    name, sql_type))
        self.conn.commit()

    #: D-329 Task 2 (Opus Q3 measurement): fill provenance, so a fade/mirror
    #: claim can never again pool maker fills (adverse-selected by the fill
    #: rule itself, see `paper_adapter._through_and_touch`) with taker fills.
    #: Same reasoning as `_POSITIONS_PAIR_LINKAGE_COLUMNS` above: `CREATE
    #: TABLE IF NOT EXISTS` is a no-op against a `positions` table that
    #: already exists on disk, so a live db needs the ALTER too.
    _POSITIONS_FILL_PROVENANCE_COLUMNS = (
        ('fill_was_maker', 'INTEGER NOT NULL DEFAULT 0'),
    )

    def _migrate_positions_fill_provenance_column(self) -> None:
        """`ALTER TABLE ADD COLUMN` for `fill_was_maker` if missing.

        `DEFAULT 0` backfills every existing row to false (unmeasured rows
        predate fill-provenance tracking and were never maker fills under
        this column's own definition - they simply have no opinion recorded).
        Same table-existence guard as
        `_migrate_positions_pair_linkage_columns`, and must run before
        `executescript` for the same reason: a fresh db has not created
        `positions` yet, and an existing one needs the ALTER first.
        """
        table_exists = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='positions'").fetchone()
        if not table_exists:
            return
        existing = {row[1] for row in
                    self.conn.execute('PRAGMA table_info(positions)')}
        for name, sql_type in self._POSITIONS_FILL_PROVENANCE_COLUMNS:
            if name in existing:
                continue
            self.conn.execute(
                'ALTER TABLE positions ADD COLUMN {} {}'.format(
                    name, sql_type))
        self.conn.commit()

    #: Forge proposal 036 (pm_complement_pair_keying). Same reasoning as
    #: `_POSITIONS_FILL_PROVENANCE_COLUMNS`: `CREATE TABLE IF NOT EXISTS` is a
    #: no-op against a `market_tape` table that already exists on disk, and
    #: `db/schema.sql` also declares `idx_market_tape_condition_ts` on
    #: `condition_id` - `CREATE INDEX` against a column that does not exist
    #: yet raises `OperationalError` immediately, unlike the no-op table
    #: statement before it. Every live and every test database that has ever
    #: run the off-crypto tape predates these columns.
    _MARKET_TAPE_COMPLEMENT_COLUMNS = (
        ('condition_id', 'TEXT'),
        ('complement_id', 'TEXT'),
    )

    def _migrate_market_tape_complement_columns(self) -> None:
        """`ALTER TABLE ADD COLUMN` for the two above if missing.

        Same table-existence guard and same "must run before executescript"
        ordering as `_migrate_positions_pair_linkage_columns` - see that
        method's docstring for why. A `market_tape` table that does not exist
        yet is left alone: schema.sql creates it a moment later with these
        columns already in the `CREATE TABLE`.
        """
        table_exists = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='market_tape'").fetchone()
        if not table_exists:
            return
        existing = {row[1] for row in
                    self.conn.execute('PRAGMA table_info(market_tape)')}
        for name, sql_type in self._MARKET_TAPE_COMPLEMENT_COLUMNS:
            if name in existing:
                continue
            self.conn.execute(
                'ALTER TABLE market_tape ADD COLUMN {} {}'.format(
                    name, sql_type))
        self.conn.commit()

    #: D-339 clause (3): the 15m signal keying. Same reasoning as
    #: _POSITIONS_FILL_PROVENANCE_COLUMNS above - CREATE TABLE IF NOT
    #: EXISTS is a no-op against a signals table that already exists on
    #: disk, and every live database predates this column.
    #:
    #: NOTE the absence of NOT NULL DEFAULT, which is deliberate and is the
    #: single most important line in this migration. fill_was_maker above
    #: carries DEFAULT 0 and that backfilled every pre-existing row into a
    #: value indistinguishable from a real measurement. NULL here means
    #: "this row predates the key" and nothing else. ALTER TABLE ADD COLUMN
    #: with no default does not rewrite the existing rows; it is a
    #: header-only change and is fast even on ~700k rows.
    _SIGNALS_DURATION_COLUMNS = (
        ('market_duration', 'TEXT'),
    )

    def _migrate_signals_duration_column(self) -> None:
        """ALTER TABLE ADD COLUMN for market_duration if missing.

        Same table-existence guard and the same "must run before
        executescript" ordering as the migrations above - see
        _migrate_positions_pair_linkage_columns for why. A signals table
        that does not exist yet is left alone: schema.sql creates it a
        moment later with the column already in the CREATE TABLE.
        """
        table_exists = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='signals'").fetchone()
        if not table_exists:
            return
        existing = {row[1] for row in
                    self.conn.execute('PRAGMA table_info(signals)')}
        for name, sql_type in self._SIGNALS_DURATION_COLUMNS:
            if name in existing:
                continue
            self.conn.execute(
                'ALTER TABLE signals ADD COLUMN {} {}'.format(
                    name, sql_type))
        self.conn.commit()

    def ensure_schema(self) -> None:
        """Idempotent migration. Replays db/schema.sql verbatim.

        If the file is missing we do NOT invent a schema - a loop writing into
        tables nobody declared is worse than a loop that refuses to start.
        """
        if not os.path.exists(self.SCHEMA_PATH):
            raise RuntimeError(
                'db/schema.sql not found at {}; refusing to invent a schema'
                .format(self.SCHEMA_PATH))
        self._migrate_positions_pair_linkage_columns()
        self._migrate_positions_fill_provenance_column()
        self._migrate_market_tape_complement_columns()
        self._migrate_signals_duration_column()
        with open(self.SCHEMA_PATH) as f:
            self.conn.executescript(f.read())
        self.conn.commit()

    # -- serialisation ------------------------------------------------------

    @staticmethod
    def _json(payload: dict) -> str:
        """Serialise, refusing non-finite values (convention 19).

        `json.dumps` writes bare `NaN`/`Infinity` by default and `json.loads`
        accepts them, so a payload that round-trips in Python is unreadable to
        every other JSON parser. `allow_nan=False` raises instead - and that
        raise must not kill the loop, so the fallback NAMES the offending keys
        rather than dropping them silently (convention 20).
        """
        try:
            return json.dumps(payload, default=str, allow_nan=False)
        except ValueError:
            bad = sorted(str(k) for k, v in payload.items()
                         if isinstance(v, float) and not math.isfinite(v))
            safe = {k: v for k, v in payload.items() if str(k) not in bad}
            safe['_non_finite_keys'] = bad
            try:
                return json.dumps(safe, default=str, allow_nan=False)
            except ValueError:
                return json.dumps({'_unserialisable': True,
                                   '_keys': sorted(str(k) for k in payload)})

    # -- writes -------------------------------------------------------------

    def record_signal(self, *, strategy_id: str, market_slug: Optional[str],
                      pattern: str, direction: str, confidence: float,
                      features: dict, acted: bool,
                      skip_reason: Optional[str],
                      ts_ms: Optional[int] = None,
                      market_duration: Optional[str] = None) -> str:
        """One row per EVALUATION, entry or skip. Returns the signal id.

        `direction` is 'long' on every row: the only Polymarket action this loop
        can take is a BUY of one outcome token. WHICH outcome lives in
        `features_json['outcome_side']`, because the direction column is a
        'long' | 'exit' contract shared with the crypto path and overloading it
        with 'Up'/'Down' would break every existing reader.

        market_duration is D-339 clause (3): which window this evaluation
        was actually against, 5m | 15m | mixed. It defaults to None and
        None is WRITTEN AS NULL, never coerced to a value - the crypto
        path in engine/db.py and every test caller pass nothing and must
        keep meaning "not recorded" rather than "5m". pair and tf are
        untouched by this column: that is the additive contract, and
        tests/test_market_duration_keying.py enforces it.
        """
        signal_id = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                'INSERT INTO signals (id, ts, pair, tf, strategy_id, pattern, '
                'direction, confidence, features_json, acted, skip_reason, '
                'mode, market_duration) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (signal_id, ts_ms if ts_ms is not None else _ms(),
                 market_slug or 'POLYMARKET', '5m', strategy_id, pattern,
                 direction, float(confidence), self._json(features),
                 1 if acted else 0,
                 None if acted else skip_reason, MODE, market_duration))
        return signal_id

    #: Window length per duration key, seconds. Used to derive
    #: seconds_remaining and to decide whether a window has CLOSED yet.
    CALIBRATION_SPANS = {'5m': UPDOWN_5M_DURATION,
                         '15m': UPDOWN_15M_DURATION}

    def record_calibration_rows(self, rows) -> int:
        """Bulk-insert one poll of the calibration tape. Returns rows written.

        One transaction for the whole cycle rather than one per token: this
        runs every poll over 12 tokens, and a per-statement commit would
        land squarely in the loop hot path.
        """
        if not rows:
            return 0
        with self.conn:
            self.conn.executemany(
                'INSERT INTO calibration_tape (token_id, market_slug, '
                'market_duration, outcome_side, condition_id, ts, '
                'window_ts, seconds_remaining, mid, best_bid, best_ask, '
                'book_depth_levels, selected) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
        return len(rows)

    def stamp_calibration_resolution(self, *, token_id: str,
                                     market_slug: str,
                                     market_duration: str,
                                     window_ts: int,
                                     resolved_outcome: str, won: int,
                                     resolved_ts: float,
                                     source: str = 'oracle') -> bool:
        """Write the resolution stamp. True if THIS call wrote it.

        INSERT OR IGNORE against a PRIMARY KEY on token_id, so a SECOND
        stamp for a token is dropped by the key. That is the design, not a
        limitation: a resolution that changes is a data error, and INSERT
        OR REPLACE would overwrite the first reading and hide it. False
        here means "already stamped", and the caller counts it into health
        so a changed resolution is COUNTED rather than lost (convention 20).
        """
        with self.conn:
            cur = self.conn.execute(
                'INSERT OR IGNORE INTO calibration_resolution (token_id, '
                'market_slug, market_duration, window_ts, '
                'resolved_outcome, won, resolved_ts, source) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (token_id, market_slug, market_duration, int(window_ts),
                 resolved_outcome, int(won), float(resolved_ts), source))
        return cur.rowcount > 0

    def pending_calibration_tokens(self, now: float, limit: int = 48):
        """Taped tokens whose window has CLOSED and that carry no stamp yet.

        The closed test is done in SQL off the row own duration, so a 15m
        window is never declared closed on the 5m clock. Oldest first, and
        capped: the stamp costs one Gamma read per token and must not turn
        into an unbounded backlog sweep inside a poll.
        """
        return self.conn.execute(
            'SELECT DISTINCT t.token_id, t.market_slug, t.market_duration, '
            't.window_ts, t.outcome_side '
            'FROM calibration_tape t '
            'LEFT JOIN calibration_resolution r ON r.token_id = t.token_id '
            'WHERE r.token_id IS NULL '
            'AND t.window_ts + (CASE t.market_duration WHEN ? '
            'THEN ? ELSE ? END) <= ? '
            'ORDER BY t.window_ts ASC LIMIT ?',
            ('15m', UPDOWN_15M_DURATION, UPDOWN_5M_DURATION,
             float(now), int(limit))).fetchall()
    def record_entry(self, position, *, signal_id: str, limit_price: float,
                     strategy_id: str, stop_px: Optional[float] = None,
                     pair_id: Optional[str] = None,
                     leg_index: Optional[int] = None,
                     leg_target_px: Optional[float] = None,
                     leg2_latency_ms: Optional[float] = None,
                     pair_cost_expected: Optional[float] = None,
                     pair_cost_actual: Optional[float] = None,
                     leg_bid_at_signal: Optional[float] = None,
                     leg_ask_at_signal: Optional[float] = None,
                     leg_bid_at_fill: Optional[float] = None,
                     leg_ask_at_fill: Optional[float] = None) -> None:
        """orders + fills + positions for one simulated fill, in ONE transaction.

        `pair_id` onward is Forge proposal 030 stage 1
        (pm_one_legged_pair_unwind_guard), log-only: every argument here is
        None for every single-leg strategy, which writes NULL into the
        matching column and changes nothing else about this row.
        `leg_fill_px` and `leg_fill_ts` are not parameters - they are this
        position's own `avg_price` and fill timestamp, so they are derived
        below rather than passed in, and are only written when `pair_id` is
        given (single-leg strategies leave them NULL rather than duplicate
        `entry_px`/`opened_ts` for no reason).

        A fill row whose order row is missing is a reconciliation problem
        invented by our own bookkeeping, and the dashboard joins fills to orders
        to derive fees.

        `stop_px` is the DISCRETIONARY stop the strategy will manage this
        position against, or None for a strategy that holds to resolution.

        This column used to be hardcoded to 0.00 for every Polymarket row, on
        the reasoning that a losing binary share is worth exactly 0.00 and that
        IS the structural stop. True, and it made the column useless: the
        2026-08-18 fair-value post-mortem could not answer "what stop did this
        family run" from the trade rows at all, because 616 closed trades all
        read `stop_px` 0.00 / `target_px` 1.00 while their exit reasons read
        `sell:price_stop`. Two different stops - a resolution floor and a
        discretionary exit level - shared one column and the discretionary one
        was the one that was never written (convention 20).

        None still writes 0.00, which is correct and now means what it says:
        this position has no discretionary stop and only resolution can close
        it at a loss.
        """
        order_id = str(uuid.uuid4())
        fill_id = str(uuid.uuid4())
        ts_ms = int(position.opened_ts) * 1000
        leg_fill_px = position.avg_price if pair_id is not None else None
        leg_fill_ts = ts_ms if pair_id is not None else None
        with self.conn:
            self.conn.execute(
                'INSERT INTO orders (id, cl_ord_id, ts, pair, side, type, qty, '
                'limit_price, stop_price, status, exchange_order_id, signal_id, '
                'mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (order_id, 'PM-{}'.format(position.position_id), ts_ms,
                 position.market_slug, 'buy', 'limit', position.shares,
                 float(limit_price), None, 'filled', None, signal_id, MODE))
            self.conn.execute(
                'INSERT INTO fills (id, order_id, ts, price, qty, fee) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (fill_id, order_id, ts_ms, position.avg_price, position.shares,
                 position.fee_usdc))
            self.conn.execute(
                'INSERT INTO positions (id, pair, strategy_id, signal_id, '
                'opened_ts, closed_ts, entry_px, exit_px, qty, stop_px, '
                'target_px, pnl_gross, pnl_net, fees, r_multiple, exit_reason, '
                'mode, fill_was_maker, pair_id, leg_index, leg_target_px, '
                'leg_fill_px, leg_fill_ts, leg2_latency_ms, '
                'pair_cost_expected, pair_cost_actual, leg_bid_at_signal, '
                'leg_ask_at_signal, leg_bid_at_fill, leg_ask_at_fill) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '
                '?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (position.position_id, position.market_slug, strategy_id,
                 signal_id, ts_ms, None, position.avg_price, None,
                 position.shares,
                 # The discretionary stop when the strategy has one, else 0.00
                 # - a losing binary share is worth exactly 0.00 and that IS
                 # the structural stop (convention 8: strictly below any valid
                 # entry). The target stays resolution at 1.00: no strategy
                 # here quotes a fixed target price, they exit on a rule.
                 (0.0 if stop_px is None else float(stop_px)),
                 WINNING_REDEMPTION,
                 None, None, position.fee_usdc, None, None, MODE,
                 # D-329 Task 2: fill provenance, read off the SAME field the
                 # maker/taker fill-rate stats already read
                 # (`PaperPosition.entry_liquidity`), not re-derived.
                 1 if getattr(position, 'entry_liquidity', 'taker') == 'maker'
                 else 0,
                 pair_id, leg_index, leg_target_px, leg_fill_px, leg_fill_ts,
                 leg2_latency_ms, pair_cost_expected, pair_cost_actual,
                 leg_bid_at_signal, leg_ask_at_signal, leg_bid_at_fill,
                 leg_ask_at_fill))

    def record_resolution(self, position) -> None:
        """Settle a position row to $1.00 or $0.00.

        `r_multiple` divides realized PnL by the premium plus fee, which on a
        binary is the exact maximum loss - not an estimated stop distance.
        """
        won = position.resolution == 'WIN'
        exit_px = WINNING_REDEMPTION if won else 0.0
        risk = position.cost_usdc + position.fee_usdc
        pnl_net = position.pnl_usdc
        pnl_gross = (position.shares * exit_px) - position.cost_usdc
        r_multiple = ((pnl_net / risk) if risk > 0 and pnl_net is not None
                      else None)
        with self.conn:
            self.conn.execute(
                'UPDATE positions SET closed_ts = ?, exit_px = ?, '
                'pnl_gross = ?, pnl_net = ?, r_multiple = ?, exit_reason = ? '
                'WHERE id = ?',
                (_ms(), exit_px, pnl_gross, pnl_net, r_multiple,
                 'target' if won else 'stop', position.position_id))

    def record_close(self, position) -> None:
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

    def record_equity(self, equity: float, cash: float, open_risk: float,
                      ts_ms: Optional[int] = None) -> None:
        """Equity snapshot. PK is (ts, mode), so a replay overwrites rather
        than duplicating a timestamp."""
        with self.conn:
            self.conn.execute(
                'INSERT OR REPLACE INTO equity_snapshots (ts, equity, cash, '
                'open_risk, mode) VALUES (?, ?, ?, ?, ?)',
                (ts_ms if ts_ms is not None else _ms(), float(equity),
                 float(cash), float(open_risk), MODE))

    def audit(self, event_type: str, payload: dict,
              actor: str = 'engine') -> None:
        with self.conn:
            self.conn.execute(
                'INSERT INTO audit_log (ts, actor, event_type, payload_json) '
                'VALUES (?, ?, ?, ?)',
                (_ms(), actor, event_type, self._json(payload)))

    def risk_event(self, event_type: str, details: dict) -> None:
        with self.conn:
            self.conn.execute(
                'INSERT INTO risk_events (id, ts, type, details_json) '
                'VALUES (?, ?, ?, ?)',
                (str(uuid.uuid4()), _ms(), event_type, self._json(details)))

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass


# ---------------------------------------------------------------------------
# Candles (magnitude data for the streak filter)
# ---------------------------------------------------------------------------

def default_candle_source(config: dict, asset: str = 'btc'):
    """Binance.US 5m candles for one asset, via the existing DataCollector.

    Why this exists at all: `resolved_windows` gives DIRECTION and nothing else
    (the oracle publishes no open/close), so an ATR computed off oracle windows
    is 1.0 by construction and every stretch ratio comes out near the streak
    length. streak_snapper refuses to run on that - correctly - with
    `no_magnitude_data`. Real USD magnitudes have to come from a price feed, and
    Binance.US is the one this project already uses.

    ONE ASSET PER SOURCE. The ATR these candles produce is divided into that
    asset's own lead_bps, and SOL's ATR against BTC's lead is not a wrong number
    that fails a gate loudly, it is a plausible number that passes the wrong
    ones. The pair comes from the assets registry rather than a literal here.

    Read-only: `fetch_ohlcv` is called, `store_candles` is not. The shadow loop
    does not own the candles table.
    """
    from engine.collector import DataCollector

    collector = DataCollector(config)
    pair = get_asset(asset).candle_pair

    def _fetch() -> Optional[dict]:
        rows = collector.fetch_ohlcv(pair, CANDLE_TF,
                                     limit=WINDOW_LOOKBACK + 8)
        if not rows:
            return None
        return {
            'opens': [r['open'] for r in rows],
            'highs': [r['high'] for r in rows],
            'lows': [r['low'] for r in rows],
            'closes': [r['close'] for r in rows],
            'volumes': [r['volume'] for r in rows],
            'timestamps': [r['ts'] for r in rows],
        }

    return _fetch


def default_candle_source_factory(config: dict):
    """A per-asset candle source builder for the loop to call.

    The loop needs ONE source per asset and cannot build them itself without
    importing DataCollector, which would drag the collector into every unit
    test. A factory keeps that import where it already is.

    A source that fails to build is None rather than fatal: a missing candle
    feed costs magnitude data on that asset and nothing else, and the strategies
    that need it already skip `no_magnitude_data`. Losing ETH's candles is not a
    reason to stop trading BTC.
    """
    def _build(asset: str):
        try:
            return default_candle_source(config, asset)
        except Exception as exc:
            logger.warning('no candle source for %s (%s: %s); magnitude-based '
                           'strategies will skip no_magnitude_data on it',
                           asset, type(exc).__name__, exc)
            return None
    return _build


# ---------------------------------------------------------------------------
# Per-asset runtime state
# ---------------------------------------------------------------------------

class AssetRuntime:
    """Everything the loop holds PER ASSET, and the reason it is per asset.

    Three fields here are mutable state that a strategy or a proxy accumulates
    across cycles. Sharing any one of them between assets is silently wrong:

      strategies    Strategy instances carry per-window state. `FairValueArb`
                    owns a `PriceTape` of (timestamp, spot) observations and a
                    `_window_trades` counter. One shared instance would feed BTC
                    spot and ETH spot into the SAME tape - the model would then
                    measure a 60,000-dollar "move" every time the loop stepped
                    from BTC to ETH - and would spend BTC's per-window trade
                    budget on ETH. So every asset gets its OWN instances.

      strike_proxy  Caches 1m klines for ONE Binance symbol. A shared proxy
                    would serve BTCUSDT bars as SOL's strike.

      candles       5m OHLCV for one pair, the source of ATR and window
                    magnitudes.

    Nothing that is genuinely account-wide lives here. The paper adapter, the
    risk gate and the equity are single instances on the loop, because the
    bankroll IS shared: $1,000 covers all three assets together and the daily
    loss breaker is supposed to see the combined number.
    """

    __slots__ = ('asset', 'strategies', 'strike_proxy', 'candle_source',
                 'candles', 'candles_at')

    def __init__(self, asset: str, strategies, strike_proxy, candle_source):
        self.asset = asset
        self.strategies = list(strategies)
        self.strike_proxy = strike_proxy
        self.candle_source = candle_source
        self.candles: Optional[dict] = None
        self.candles_at = 0.0


# ---------------------------------------------------------------------------
# Per-space runtime state (event, sports, political)
# ---------------------------------------------------------------------------

class MarketSpace:
    """One non-crypto market universe polled on its own clock.

    The crypto side is a fixed rectangle: three assets, one market each, known
    before the poll. A space is not - its size is whatever Gamma returned this
    minute. So a space owns the same five things the weather cycle had to grow
    (poll list, counters, evaluation total, health, discovery record) and gets
    the same treatment: its own identity check, and NOTHING of it inside the
    crypto identity.

    `strategies` is the routing result, not a hand-maintained list. It is the
    subset of the registry that DECLARED this space's `market_type`, computed
    once in the constructor. The weather cycle's predecessor selected on a
    boolean `needs_weather_market` flag, which worked for one universe and does
    not generalise: a flag per universe is a flag somebody has to remember to
    add, and the failure mode is a strategy that is silently never polled.

    These are DEDICATED instances, never the ones on `AssetRuntime`. The
    argument is `AssetRuntime`'s own, one level up: strategy instances carry
    per-market tape and per-window counters, and feeding one instance a BTC
    window and an NFL market would interleave two observation streams into a
    series neither of them saw.
    """

    __slots__ = ('name', 'market_type', 'strategies', 'query', 'cycle_sec',
                 'market_limit', 'discovery_limit', 'min_volume_usdc',
                 'enabled', 'counts', 'evaluations', 'cycles', 'health',
                 'discovery', 'markets', 'identity_violations',
                 'last_cycle', 'last_discovery')

    def __init__(self, name: str, market_type: str, strategies, query: dict,
                 cycle_sec: float = DEFAULT_SPACE_CYCLE_SEC,
                 market_limit: int = DEFAULT_SPACE_MARKET_LIMIT,
                 discovery_limit: int = DEFAULT_SPACE_DISCOVERY_LIMIT,
                 min_volume_usdc: float = DEFAULT_SPACE_MIN_VOLUME_USDC,
                 enabled: bool = True):
        self.name = str(name)
        self.market_type = str(market_type)
        self.strategies = list(strategies)
        #: `{'tag': ..., 'keywords': (...)}`. `tag` is handed to Gamma;
        #: `keywords` filter the returned questions locally. Both are recorded
        #: on every discovery result so a poll list can be explained rather
        #: than inferred.
        self.query = dict(query)
        self.cycle_sec = float(cycle_sec)
        self.market_limit = max(0, int(market_limit))
        self.discovery_limit = max(1, int(discovery_limit))
        self.min_volume_usdc = float(min_volume_usdc)
        self.enabled = bool(enabled)
        self.counts: Counter = Counter()
        self.evaluations = 0
        self.cycles = 0
        self.identity_violations = 0
        self.health: Counter = Counter()
        self.discovery: Dict[str, object] = {}
        self.markets: List = []
        self.last_cycle = 0.0
        self.last_discovery = 0.0

    def status(self, reason: str) -> str:
        """This space's namespaced form of a SPACE_* disposition."""
        return space_status(self.name, reason)

    @property
    def strategy_names(self) -> List[str]:
        return [getattr(s, 'strategy_name', str(s)) for s in self.strategies]


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

class PolymarketShadowLoop:
    """Paper-mode decision loop over the live BTC Up/Down 5-minute market."""

    def __init__(self, client=None, adapter=None, store=None,
                 config: Optional[dict] = None,
                 strategies: Optional[Sequence] = None,
                 risk_gate=None, candle_source=None,
                 poll_sec: float = DEFAULT_POLL_SEC,
                 starting_equity: float = DEFAULT_STARTING_EQUITY_USDC,
                 db_path: Optional[str] = None,
                 log_dir: Optional[str] = None,
                 equity_snapshot_sec: float = DEFAULT_EQUITY_SNAPSHOT_SEC,
                 resolve_sec: float = DEFAULT_RESOLVE_SEC,
                 resolution_sweep_sec: float = DEFAULT_RESOLUTION_SWEEP_SEC,
                 enable_resolution_ledger: bool = True,
                 stats_flush_sec: float = DEFAULT_STATS_FLUSH_SEC,
                 candle_refresh_sec: float = DEFAULT_CANDLE_REFRESH_SEC,
                 include_15m: bool = True,
                 strike_proxy: Optional[StrikeProxy] = None,
                 assets: Optional[Sequence[str]] = None,
                 candle_source_factory=None,
                 parallel_fetches: bool = True,
                 fetch_workers: int = DEFAULT_FETCH_WORKERS,
                 spot_cache_ttl_sec: float = DEFAULT_SPOT_CACHE_TTL_SEC,
                 weather_cycle_sec: float = DEFAULT_WEATHER_CYCLE_SEC,
                 weather_market_limit: int = DEFAULT_WEATHER_MARKET_LIMIT,
                 weather_discovery_limit: int =
                 DEFAULT_WEATHER_DISCOVERY_LIMIT,
                 enable_weather: bool = True,
                 weather_strategies: Optional[Sequence] = None,
                 space_cycle_sec: float = DEFAULT_SPACE_CYCLE_SEC,
                 space_market_limit: int = DEFAULT_SPACE_MARKET_LIMIT,
                 space_discovery_limit: int = DEFAULT_SPACE_DISCOVERY_LIMIT,
                 space_min_volume_usdc: float = DEFAULT_SPACE_MIN_VOLUME_USDC,
                 enable_spaces: bool = True,
                 spaces: Optional[Sequence] = None):
        if PAPER_MODE is not True:
            raise RuntimeError(
                'PAPER_MODE is not True. This loop has no live execution path; '
                'a falsy PAPER_MODE means the module was tampered with.')

        config = dict(config or {})
        pm = dict(config.get('polymarket') or {})
        # The bankroll is stated ONCE and pushed into both the adapter and the
        # gate. Two components disagreeing about how much money exists is how a
        # cap stops binding.
        pm['starting_equity_usdc'] = float(starting_equity)
        risk = dict(pm.get('risk') or {})
        risk['bankroll_usdc'] = float(starting_equity)
        pm['risk'] = risk
        config['polymarket'] = pm
        self.config = config
        self.starting_equity = float(starting_equity)

        self.client = client if client is not None else PolymarketClient()
        if adapter is not None:
            self.adapter = adapter
        else:
            kw = {'log_dir': log_dir} if log_dir else {}
            self.adapter = PolymarketPaperAdapter(client=self.client,
                                                  config=config, **kw)
        self.store = store if store is not None else ShadowStore(db_path)
        self.gate = (risk_gate if risk_gate is not None
                     else PolymarketRiskGate(config))
        # -- Forge proposal 038, the settlement resolution ledger. Records what
        # every market the loop FETCHED settled at, whether or not a position
        # was ever held in it. It is WRITE-ONLY from the loop's point of view:
        # nothing here is read back into a decision, and no strategy is wired
        # to it, because a resolution record exists at window CLOSE - after
        # every entry and exit decision for that window - so a strategy reading
        # it would be look-ahead. Its consumers are `backtest/` and
        # `agents/forge_shadow_eval.py`.
        #
        # It shares the store's connection rather than opening a second one:
        # sqlite3 connections are single-thread by default and every ledger
        # write happens on the main loop thread, in `sweep_resolutions`, after
        # the trading phases.
        self.enable_resolution_ledger = bool(enable_resolution_ledger)
        self.resolution_sweep_sec = float(resolution_sweep_sec)
        self.ledger = (
            ResolutionLedger(conn=self.store.conn, client=self.client)
            if self.enable_resolution_ledger else None)
        # -- assets. Every asset polled runs the full strategy set against its
        # own market, and the per-asset state that makes that safe lives in an
        # AssetRuntime (see that class for why sharing it is wrong).
        self.assets: Tuple[str, ...] = tuple(
            SHADOW_ASSETS if assets is None else assets)
        if not self.assets:
            raise ValueError('no assets to poll; the loop would run zero '
                             'evaluations per cycle and look healthy doing it')
        for key in self.assets:
            get_asset(key)          # raises now, at wiring time, not mid-run

        self.candle_source = candle_source
        self.candle_source_factory = candle_source_factory

        # -- ROUTING (D-312). The crypto cycle gets the subset of the registry
        # that DECLARED `crypto_updown`, not the whole registry.
        #
        # This changes the identity's denominator and that is the point. Before
        # D-312 `PM_weather_arb` ran inside the crypto cycle on every asset,
        # every 5-second poll, and returned `not_a_temperature_market` every
        # time. The old comment below called that "what keeps the crypto
        # identity's denominator at 19 x 3", which is true and is an argument
        # for a WRONG denominator: it was writing three rows a poll - roughly
        # 52,000 a day - into a `signals` table that already has an open
        # retention question, to record 52,000 times that a Bitcoin market is
        # not a temperature market. A denominator held constant by evaluating
        # strategies against universes they cannot trade is not an accounting
        # win, it is noise with an identity check on top.
        #
        # `evaluations_per_cycle` is computed from the LISTS, never written
        # down, so the identity follows the routing automatically.
        def _registry():
            # Proposal 031 phase 1: the live loop's own resolved db path
            # wires DipArb's tape to `market_tape` so it survives a restart.
            # An injected `strategies` list (every test that builds its own
            # pool) bypasses `build_strategies()` entirely and is unaffected.
            return list(strategies) if strategies is not None \
                else build_strategies(dip_arb_tape_db_path=self.store.db_path)

        #: Everything the registry offers, before routing. Kept so `stats()`
        #: can report what was routed OUT of each space and why - a strategy
        #: that is silently in no space at all is the failure this records.
        self._registry_names = [getattr(s, 'strategy_name', str(s))
                                for s in _registry()]

        def _supporting(pool, market_type: str) -> list:
            """The members of `pool` that declared `market_type`.

            Defaults to crypto-only for anything with no declaration at all, so
            a strategy from outside this package - or one written before D-312 -
            keeps its exact previous routing rather than silently joining every
            universe.
            """
            out = []
            for s in pool:
                declared = getattr(s, 'supported_market_types',
                                   (MARKET_TYPE_CRYPTO_UPDOWN,))
                if market_type in declared:
                    out.append(s)
            return out

        self.runtimes: Dict[str, AssetRuntime] = {}
        for key in self.assets:
            self.runtimes[key] = AssetRuntime(
                asset=key,
                strategies=_supporting(_registry(), MARKET_TYPE_CRYPTO_UPDOWN),
                # An INJECTED proxy or candle source applies to every asset:
                # that is what a test wants when it hands over one offline stub.
                # The DEFAULTS are built per asset, because a default that
                # served BTC klines as SOL's strike would be wrong in
                # production and invisible in the tests.
                strike_proxy=(strike_proxy if strike_proxy is not None else
                              StrikeProxy(
                                  session=getattr(self.client, 'session', None),
                                  symbol=get_asset(key).binance_symbol)),
                candle_source=(candle_source if candle_source is not None
                               else (candle_source_factory(key)
                                     if candle_source_factory else None)),
            )

        # The BTC (or first-asset) strategy list, kept under its old name
        # because `len(self.strategies)` is the per-asset strategy count that
        # the accounting identity multiplies, and because main() prints it.
        # It is one asset's instances, NOT the whole population.
        self.strategies = self.runtimes[self.assets[0]].strategies

        # -- fetch concurrency. ON by default and switchable OFF from the
        # constructor, so a suspected concurrency bug can be bisected by
        # flipping a flag rather than by reverting a patch mid-incident.
        self.parallel_fetches = bool(parallel_fetches)
        self.fetch_workers = max(1, int(fetch_workers))
        self.spot_cache_ttl_sec = max(0.0, float(spot_cache_ttl_sec))
        #: asset -> (monotonic_at, fetch_spot_checked result). Monotonic and
        #: not wall clock; see DEFAULT_SPOT_CACHE_TTL_SEC.
        self._spot_cache: Dict[str, Tuple[float, dict]] = {}

        self.poll_sec = float(poll_sec)
        self.equity_snapshot_sec = float(equity_snapshot_sec)
        self.resolve_sec = float(resolve_sec)
        self.stats_flush_sec = float(stats_flush_sec)
        self.candle_refresh_sec = float(candle_refresh_sec)
        self.include_15m = bool(include_15m)
        # See DEFAULT_MAX_RESTING_MAKER_ORDERS. Read off the SAME `pm` block the
        # adapter reads its own caps from, so the two cannot be configured
        # against each other from two different places.
        self.max_resting_maker_orders = max(0, int(
            pm.get('max_resting_maker_orders',
                   DEFAULT_MAX_RESTING_MAKER_ORDERS)))
        # The strike Gamma does not publish, served as a MEASURED proxy.
        # Injectable for the same reason `candle_source` is: a default that
        # reaches the network turns every unit test into a live API call, and a
        # test whose result depends on what BTC did this minute is not a test.
        # Reuses the client's connection pool when there is one; these are not
        # Polymarket hosts, so no Polymarket rate limiter applies either way.
        # getattr, not attribute access: a client stand-in without a `session`
        # is a legitimate caller, and StrikeProxy opens its own on None.
        # Per-asset proxies live on the runtimes; this is the first asset's, kept
        # for callers that reach for `loop.strike_proxy` by name.
        self.strike_proxy = self.runtimes[self.assets[0]].strike_proxy

        # -- `market_tape` (D-362 R4). THE LOOP OWNS THIS WRITE.
        #
        # It used to live inside `DipArb.observe`, which meant the table only
        # filled on cycles that evaluated `PM_dip_arb`. When that strategy was
        # sentinel-killed the tape froze in EVERY book - including
        # `db/trading-survivors.db`, which had never run dip_arb at all - and
        # it could not self-heal, because no other strategy writes it. Every
        # `market_tape` consumer (`agents/forge_complement_check.py`, Forge
        # proposal 031) was silently reading a dead table.
        #
        # Written from `_write_market_tape`, called on every non-crypto context
        # this loop builds, so no roster edit can turn it off again.
        self.market_tape = PriceTapeByToken(db_path=self.store.db_path)
        #: Counted, never inferred: rows accepted, and contexts that produced
        #: no row at all. `market_tape` volume alone cannot distinguish "the
        #: writer is off" from "the books were empty" (convention 20).
        self.tape_rows_written = 0
        self.tape_contexts = 0

        # -- accounting. Convention 20: every evaluation lands in exactly one
        # bucket and the identity below is asserted on every flush.
        self.evaluations = 0
        self.cycles = 0
        self.counts: Counter = Counter()
        # Counters OUTSIDE the identity space: they describe the loop's health,
        # not a window's disposition, and folding them in would break the
        # identity for reasons that have nothing to do with decisions.
        self.health: Counter = Counter()
        # Ditto, and for the same reason: an open-position exit check is not an
        # evaluation of a window. Its own categorised taxonomy - see the module
        # docstring's exit-management section.
        # Token ids some strategy actually ENTERED on this cycle. Reset
        # every poll and read by the calibration sampler, which runs after
        # the evaluation phase so that selected is a fact about this cycle
        # rather than a guess from the registry. An unselected market is
        # the arm 029 condition (b) exists to measure, so selected = 0 is
        # WRITTEN, never omitted (convention 20).
        self._cycle_selected_tokens: set = set()
        self.exit_counts: Counter = Counter()
        # Ditto, fourth space: what happened to RESTING ORDERS this cycle. A
        # resting order is looked at on every cycle and usually nothing happens
        # to it, so these are not evaluations and folding them into `counts`
        # would break the identity with thousands of no-ops. Keys are
        # `observed`, `still_resting`, `fill:*`, `expire:*` and `cancel:*`, and
        # the terminal reasons are the ADAPTER's own strings, never re-worded.
        self.maker_counts: Counter = Counter()
        # Ditto again, and the most important of the three: `timings` holds
        # SECONDS, not events. Separate from `health` so that nothing summing
        # counter values can pull a duration into a count, and outside the
        # identity for the same reason `exit_counts` is - a stopwatch reading
        # is not a decision. Keys are `<step>` (cumulative seconds) and
        # `<step>_calls` (a count). Read it via `timing_report()`.
        self.timings: Counter = Counter()
        # The increments happen from inside fetch threads, and `Counter[k] += x`
        # is a read-modify-write that loses updates under concurrency. A lost
        # timing sample is a number that is quietly too small, which is exactly
        # the kind of silently-wrong figure this codebase refuses elsewhere.
        self._timing_lock = threading.Lock()
        self.identity_violations = 0

        # -- exit-management CAPABILITY DISPATCH (convention 20).
        #
        # `manages_exits = True` says "this strategy decides its own exits". It
        # does NOT say "this strategy publishes a fair value". Those are two
        # different capabilities, and this loop used to assume the second
        # followed from the first: `manage_exits` called `estimate()` on every
        # manager inside a try/except and counted the AttributeError.
        #
        # The fair-value family (`PM_fair_value_arb` and its variants) exits
        # against a model estimate and implements `estimate()`. `PM_dip_arb`
        # exits against its own rolling tape mean and needs no fair value at
        # all. It had no `estimate()`, and this loop called one anyway: every
        # cycle raised a caught AttributeError and incremented
        # `exit_fair_value_exceptions`. Its exits were never affected -
        # `manage_exit()` is called afterwards with `fair_value=None` either
        # way - but the INSTRUMENT was, at roughly 51,000 spurious increments
        # per day across three assets on a 5-second poll, enough to bury a
        # genuine fair-value exception completely.
        #
        # RESOLVED by D-300 (Raven ruling, 2026-08-18). A concurrent session
        # fixed the same bug from the OTHER end and gave `DipArb` a documented,
        # never-usable `estimate()`. THAT is the one that stands: a strategy
        # declaring `manages_exits = True` is obliged to ship an `estimate()`
        # this loop can call, and DipArb now ships one.
        #
        # So the paragraph above is HISTORY, not current state. "PM_dip_arb has
        # no estimate()" was true when this dispatch was written and is no
        # longer true of any registered strategy. It is kept because it is the
        # measurement that explains the ~51,000 spurious increments per day, and
        # deleting it would leave the counter's history unreadable.
        #
        # The dispatch stays, and it is now a SAFETY GUARD rather than a fix:
        # redundant with `DipArb.estimate()` today, kept for the next strategy
        # that declares the flag without shipping the method. With every current
        # manager implementing the protocol this gauge reads 0, which makes any
        # NONZERO reading a wiring bug - including a fair-value strategy that
        # loses its `estimate()` in a refactor, a breakage the pre-dispatch
        # shape absorbed silently into a caught AttributeError.
        #
        # So dispatch on the CAPABILITY, and keep the two populations in
        # SEPARATE numbers. "has no estimate()" is a WIRING FACT, recorded once
        # here as a set and reported as a gauge; "estimate() raised" stays a
        # per-occurrence health counter with its try/except intact. Giving
        # DipArb a stub `estimate()` that returns an unusable object was
        # rejected for the same reason: it would make a genuine future breakage
        # look identical to a strategy that never had one.
        self.exit_no_fair_value_protocol = set()
        for _key in self.assets:
            for _s in self.runtimes[_key].strategies:
                if (getattr(_s, 'manages_exits', False)
                        and not hasattr(_s, 'estimate')):
                    self.exit_no_fair_value_protocol.add(
                        (_key, getattr(_s, 'strategy_name', None)))
        # ASSIGNED, not incremented: a gauge over a set, so it stays constant
        # across cycles instead of re-counting the same wiring fact every poll.
        self.health['exit_no_fair_value_protocol'] = len(
            self.exit_no_fair_value_protocol)

        # -- the WEATHER cycle. A second, slower cycle over a completely
        # different market universe, with its own counters, its own identity and
        # its own cadence.
        #
        # WHY IT IS NOT PART OF `run_cycle`. The crypto cycle's identity is
        # `evaluations == cycles * strategies * assets`: a fixed rectangle, one
        # market per (cycle, asset), known before the poll starts. The weather
        # universe is 1,090 markets that change through the day, of which a
        # volume-ranked handful are polled, and the count per cycle is whatever
        # discovery returned. Folding that into the rectangle would make the
        # rectangle stop being a rectangle, and the identity is the only thing
        # that catches a silently dropped decision (convention 20). So the two
        # spaces are separate and BOTH are checked.
        #
        # `PM_weather_arb` NO LONGER runs inside the crypto cycle (D-312). It
        # used to, and the reason given was that it kept the crypto identity's
        # denominator at 19 x 3 - see the routing block above for why that was
        # an argument for the wrong denominator. It is now selected into the
        # weather space by DECLARATION (`supported_market_types`) rather than by
        # the `needs_weather_market` boolean, which does not generalise past one
        # universe. The flag is still read as a fallback so an injected stub
        # that predates D-312 keeps working.
        self.enable_weather = bool(enable_weather)
        self.weather_cycle_sec = float(weather_cycle_sec)
        self.weather_market_limit = max(0, int(weather_market_limit))
        self.weather_discovery_limit = max(1, int(weather_discovery_limit))
        # DEDICATED instances, not the ones on `runtimes`. Those are being fed
        # crypto windows; these are being fed temperature markets, and a shared
        # instance would merge two feed-cache streams and two health counters
        # into one that describes neither (the same argument `AssetRuntime`
        # makes for per-asset strategy instances).
        if weather_strategies is not None:
            self.weather_strategies = list(weather_strategies)
        else:
            self.weather_strategies = [
                s for s in _registry()
                if MARKET_TYPE_WEATHER in getattr(s, 'supported_market_types',
                                                  ())
                or getattr(s, 'needs_weather_market', False)]
        #: OUTSIDE the crypto identity, and with an identity of its own:
        #: `weather_evaluations == entry + every skip`. See `_count`.
        self.weather_counts: Counter = Counter()
        self.weather_evaluations = 0
        self.weather_cycles = 0
        self.weather_identity_violations = 0
        #: Health, not dispositions. Discovery outcomes and book-read outcomes
        #: live here for the same reason `health` exists on the crypto side: a
        #: failed discovery is a fact about the venue, not a decision about a
        #: market.
        self.weather_health: Counter = Counter()
        #: The last discovery result, verbatim, so an operator can see WHY the
        #: poll list is what it is rather than inferring it from an empty list.
        self.weather_discovery: Dict[str, object] = {}
        self.weather_markets: List = []
        self._last_weather_cycle = 0.0
        self._last_weather_discovery = 0.0

        # -- the general binary spaces (D-313) -----------------------------
        #
        # Selected by DECLARATION, exactly as the weather space is. A strategy
        # joins `sports` by putting MARKET_TYPE_SPORTS in its
        # `supported_market_types`, not by anyone editing a list here. The
        # failure mode this avoids is a strategy that is silently never polled
        # because somebody added a universe and forgot a flag.
        #
        # DEDICATED instances per space, never the ones on `runtimes` and never
        # shared BETWEEN spaces. Same argument `AssetRuntime` makes: strategy
        # instances carry per-market tape and per-window counters, so feeding
        # one instance an NFL market and a Fed-decision market would interleave
        # two observation streams into a series neither of them saw.
        self._space_finders = {
            'event': search_event_markets_checked,
            'sports': search_sports_markets_checked,
            'political': search_political_markets_checked,
        }
        self.enable_spaces = bool(enable_spaces)
        self.space_cycle_sec = float(space_cycle_sec)
        space_defs = (
            ('event', MARKET_TYPE_EVENT, {'tag': None, 'keywords': ()}),
            ('sports', MARKET_TYPE_SPORTS, {'tag': 'sports_tag_slugs',
                                            'keywords': ()}),
            ('political', MARKET_TYPE_POLITICAL, {'tag': 'political_tag_slugs',
                                                  'keywords': ()}),
        )
        if spaces is not None:
            self.spaces: List[MarketSpace] = list(spaces)
        else:
            self.spaces = [
                MarketSpace(name=name, market_type=market_type,
                            # `_registry()` builds a FRESH population per call
                            # when nothing was injected, which is what makes
                            # these dedicated instances rather than three
                            # references to one list.
                            strategies=_supporting(_registry(), market_type),
                            query=query, cycle_sec=self.space_cycle_sec,
                            market_limit=space_market_limit,
                            discovery_limit=space_discovery_limit,
                            min_volume_usdc=space_min_volume_usdc,
                            enabled=self.enable_spaces)
                for name, market_type, query in space_defs
            ]

        self._stop = False
        self._halt_state = False
        self._consecutive_api_errors = 0
        self._last_resolve = 0.0
        self._last_resolution_sweep = 0.0
        self._last_equity_snapshot = 0.0
        self._last_stats_flush = 0.0
        self._started_at: Optional[float] = None

    # -- lifecycle ----------------------------------------------------------

    def request_stop(self, *_args) -> None:
        """Signal-handler safe. Sets a flag; the loop shuts down at the top of
        the next iteration and still flushes a final equity snapshot."""
        self._stop = True

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self.request_stop)
            except (ValueError, OSError):
                # Not the main thread. The caller keeps its own stop control.
                logger.debug('could not install handler for %s', sig)

    # -- helpers ------------------------------------------------------------

    def _count(self, disposition: str,
               counts: Optional[Counter] = None) -> str:
        """Record one evaluation's disposition. The only way a count moves.

        `counts=None` is the IDENTITY SPACE: the crypto Up/Down cycle, where
        `evaluations == cycles * strategies * assets` must hold exactly.

        Passing a Counter records the disposition OUTSIDE that space and leaves
        `self.evaluations` untouched. That is what the weather cycle uses, and
        it is not a loophole: a weather market is not a (cycle, asset, strategy)
        triple. There is no fixed number of temperature markets per poll, the
        weather cycle runs on its own 60-second cadence rather than the 5-second
        one, and folding either fact into the identity would make the identity
        stop describing anything - which is the same argument `exit_counts` and
        `timings` already sit outside it on. The weather space has its OWN
        identity, checked in `check_weather_identity`.
        """
        if counts is None:
            self.evaluations += 1
            self.counts[disposition] += 1
        else:
            counts[disposition] += 1
        return disposition

    def _skips(self) -> int:
        return sum(v for k, v in self.counts.items() if k != 'entry')

    def _entries(self) -> int:
        return self.counts['entry']

    def _fetch_book_checked(self, token_id: str) -> Tuple[Optional[object], str]:
        """Book for one token, saying whether a miss was an outage or an empty
        book.

        `fetch_orderbook` collapses both into None, and those two facts need
        opposite responses: an outage means back off and retry, an empty book
        means nobody is quoting this market and the window simply is not
        tradable. Convention 20 forbids sharing one number between them, so this
        goes through `client.clob` and `orderbook_from_api` - both existing
        public APIs - rather than the collapsing wrapper.

        Returns (book, status), status in {'ok', 'api_error', 'no_liquidity'}.
        `orderbook_from_api` also returns None for a CROSSED book, which is a
        stale or corrupt snapshot; it folds into no_liquidity because in both
        cases there is nothing we can lift.
        """
        payload = self.client.clob('/book', {'token_id': str(token_id)})
        if payload is None:
            return None, SKIP_API_ERROR
        book = orderbook_from_api(token_id, payload)
        if book is None:
            return None, SKIP_NO_LIQUIDITY
        return book, 'ok'

    # -- instrumentation and concurrency ------------------------------------

    def _record_timing(self, key: str, seconds: float) -> None:
        """Add one wall-clock sample to `timings`. Thread-safe.

        WALL time, not CPU time: every step measured here is network-bound and
        CPU time on a blocked socket read is approximately zero, which would
        make the whole instrumentation read as free.
        """
        with self._timing_lock:
            self.timings[key] += seconds
            self.timings[key + '_calls'] += 1

    def _timed(self, key: str, fn: Callable, *args, **kwargs):
        """Run `fn`, recording its wall time under `key`. Re-raises unchanged.

        The `finally` matters: a step that raised still took time, and dropping
        its sample would make an exploding step look like a fast one.
        """
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            self._record_timing(key, time.perf_counter() - t0)

    def timing_report(self) -> dict:
        """`{step: {'total_sec', 'calls', 'avg_sec'}}`.

        Split three ways on purpose. A step that is slow and a step that merely
        ran a lot produce the same total and need opposite fixes, and a bare
        total cannot tell them apart.
        """
        with self._timing_lock:
            raw = dict(self.timings)
        out: Dict[str, dict] = {}
        for key, total in raw.items():
            if key.endswith('_calls'):
                continue
            calls = int(raw.get(key + '_calls', 0))
            out[key] = {
                'total_sec': round(float(total), 4),
                'calls': calls,
                'avg_sec': round(float(total) / calls, 4) if calls else None,
            }
        return out

    def _run_parallel(self, tasks: Sequence[Tuple[str, Callable]]
                      ) -> Dict[str, Tuple[object, Optional[BaseException]]]:
        """Run independent network reads concurrently. NEVER raises.

        `tasks` is an ORDERED sequence of `(key, zero-arg callable)`. The result
        maps key -> `(value, exception)`, exactly one of which is not None, and
        it is built by walking `tasks` in the CALLER's order rather than in
        thread-completion order - so two runs of the same cycle produce the same
        iteration order and a diff of two logs stays readable.

        A task that raises is caught and reported. A fetch thread is allowed to
        fail; it is not allowed to take the cycle with it.

        ## Sharing one `requests.Session` across these threads: the reasoning

        `PolymarketClient` holds ONE `requests.Session` and every task here goes
        through it. `requests` does not document `Session` as thread-safe, so
        this was checked rather than assumed:

          * Every call is a GET. Nothing here mounts an adapter, sets a header
            or changes `session.params` after construction - `client.py` sets
            the User-Agent once in `__init__` and never touches it again. The
            documented hazard is concurrent MUTATION of session state, and there
            is none.
          * The connection pool underneath is urllib3's, which IS thread-safe by
            design and is the part actually being shared. At a width of 4 we
            stay inside the default `pool_maxsize=10`, so no request is forced
            to open and discard a connection outside the pool.
          * The one piece of session state a GET can still mutate is the cookie
            jar via `Set-Cookie`. `http.cookiejar.CookieJar` guards itself with
            an internal RLock and `requests` uses it directly, so a concurrent
            extract is locked. These are public unauthenticated JSON APIs and
            nothing here depends on a cookie either way.
          * `client.stats` is a plain dict of ints incremented WITHOUT a lock,
            so a concurrent `+= 1` can lose an update. That is accepted and
            stated here rather than hidden, and it is the reason
            `client.stats['requests']` is a FLOOR under parallel fetches, not an
            exact count. `RateLimiter` is separately locked, so the number that
            has to be exact - the budget - is exact.

        The alternative was a pool of Sessions, one per worker. Not taken: each
        would carry its own connection pool and therefore its own TLS
        handshakes, which costs more than the contention it avoids at width 4.
        """
        results: Dict[str, Tuple[object, Optional[BaseException]]] = {}
        if not tasks:
            return results

        # One task, or parallelism switched off: run inline. Spinning up an
        # executor to serialise a single call is pure overhead, and the
        # sequential path has to stay exercised anyway - it is the control that
        # makes the parallel measurement believable (convention 17).
        if not self.parallel_fetches or len(tasks) == 1:
            for key, fn in tasks:
                try:
                    results[key] = (fn(), None)
                except Exception as exc:      # categorised by the caller
                    results[key] = (None, exc)
            return results

        # A per-call executor rather than one held on the loop. Creating four
        # threads costs well under a millisecond against a network phase
        # measured in hundreds of them, and a pool with a lifecycle is a pool
        # that can outlive a test, leak into the next one, or be left running by
        # a caller that never reaches `shutdown()`. Cheap and disposable beats
        # fast and stateful here.
        width = min(self.fetch_workers, len(tasks))
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=width, thread_name_prefix='pm-fetch') as pool:
            submitted = [(key, pool.submit(fn)) for key, fn in tasks]
            for key, future in submitted:     # caller order, not completion
                try:
                    results[key] = (future.result(), None)
                except Exception as exc:      # categorised by the caller
                    results[key] = (None, exc)
        return results

    def _books_from_results(self, outcomes, results: dict, prefix: str
                            ) -> Tuple[Dict[str, object], Dict[str, str]]:
        """Turn `_run_parallel` output into `(books, status_by_outcome_name)`.

        The status values are `_fetch_book_checked`'s, unchanged: `ok`,
        `api_error`, `no_liquidity`. The one addition is `fetch_exception` for
        the case that cannot happen sequentially - a task that raised - and it
        gets its own `health` counter. It is NEVER folded into `api_error`:
        "the venue read failed" and "our own fetch code threw" need opposite
        responses, and one shared number cannot ask for both.

        Iteration is over `outcomes`, so ordering is the market's own and does
        not depend on which thread finished first.
        """
        books: Dict[str, object] = {}
        status: Dict[str, str] = {}
        for outcome in outcomes:
            value, exc = results.get(prefix + outcome.token_id, (None, None))
            if exc is not None:
                status[outcome.name] = STATUS_FETCH_EXCEPTION
                self.health['book_fetch_exception'] += 1
                logger.error('PM SHADOW book fetch raised for token %s: %s: %s',
                             outcome.token_id, type(exc).__name__, exc)
                continue
            if value is None:
                # No result recorded for a token we asked about. Not a venue
                # fact either, so it takes the same non-venue bucket rather
                # than quietly becoming an outage.
                status[outcome.name] = STATUS_FETCH_EXCEPTION
                self.health['book_result_missing'] += 1
                continue
            book, bstatus = value
            status[outcome.name] = bstatus
            if book is not None:
                books[outcome.token_id] = book
        return books, status

    def spot_checked(self, asset: str, now_mono: Optional[float] = None) -> dict:
        """`fetch_spot_checked` behind a short-TTL, per-asset cache.

        Returns `fetch_spot_checked`'s dict plus TWO keys that are the whole
        reason the cache is allowed to exist:

            age_sec   how old this reading is, in seconds. 0.0 on a fresh read.
            cached    True when it came from the cache.

        `age_sec` rides into every decision's features as `spot_age_sec`,
        exactly as `candles_age_sec` already does, so a decision taken on a
        stale spot stays identifiable after the fact. A cached value with no age
        stamp is indistinguishable from a fresh one in the log, and a number
        nobody can date is a number nobody can audit.

        The cache is keyed PER ASSET. One shared entry would hand BTC's spot to
        ETH's strike comparison, which does not fail loudly - it produces a lead
        of about -97% that every gate rejects, so the wiring error would present
        as a quiet market.

        A FAILED read is NOT cached. Caching `spot=None` would turn one flaky
        request into a guaranteed outage for every poll inside the TTL, and the
        log would then describe a longer outage than actually happened
        (convention 11).
        """
        now_mono = time.monotonic() if now_mono is None else now_mono
        hit = self._spot_cache.get(asset)
        if hit is not None:
            cached_at, cached_result = hit
            age = now_mono - cached_at
            # `age >= 0` is not redundant even on a monotonic clock: an injected
            # `now_mono` from a test or a caller can precede the stamp, and a
            # negative age would pass a `< ttl` check forever.
            if 0.0 <= age < self.spot_cache_ttl_sec:
                self.health['spot_cache_hit:' + asset] += 1
                return dict(cached_result, age_sec=round(age, 3), cached=True)

        self.health['spot_cache_miss:' + asset] += 1
        result = self._timed('spot', fetch_spot_checked, self.client, asset)
        if result.get('spot') is not None:
            self._spot_cache[asset] = (now_mono, result)
        return dict(result, age_sec=0.0, cached=False)

    def _refresh_candles(self, runtime: 'AssetRuntime', now: float) -> None:
        """Re-pull one asset's 5m candles at most every `candle_refresh_sec`.

        A failed refresh keeps the PREVIOUS candles rather than blanking them:
        an outage is not a market with no history (convention 11). Staleness is
        bounded by the 5m bar itself, and `candles_age_sec` rides in every
        decision's features so a stale-context decision stays identifiable
        after the fact.

        The health counters are keyed BY ASSET. A pooled `candle_fetch_empty`
        across three assets would read as intermittent flakiness when the actual
        fact is that one pair is dead and two are fine (convention 20).
        """
        if runtime.candle_source is None:
            return
        if (runtime.candles is not None
                and now - runtime.candles_at < self.candle_refresh_sec):
            return
        try:
            candles = runtime.candle_source()
        except Exception as exc:
            self.health['candle_fetch_exception:' + runtime.asset] += 1
            logger.warning('candle source for %s raised: %s: %s',
                           runtime.asset, type(exc).__name__, exc)
            return
        if not candles or not candles.get('closes'):
            self.health['candle_fetch_empty:' + runtime.asset] += 1
            return
        runtime.candles = candles
        runtime.candles_at = now
        self.health['candle_refresh_ok:' + runtime.asset] += 1

    # -- context ------------------------------------------------------------

    def build_context(self, window_ts: int, now: Optional[float] = None,
                      asset: str = 'btc'
                      ) -> Tuple[Optional[MarketContext], str, dict]:
        """Assemble ONE ASSET's MarketContext for this window, from live reads.

        `asset` is the third parameter and defaults to 'btc' so that every
        existing positional call keeps its exact previous meaning.

        One context, one underlying. The spot, strike, lead_bps, ATR, windows
        and books in the returned object are all statements about `asset`, and
        the loop builds one of these per asset per cycle rather than one object
        holding three of everything - there would be no type error to catch a
        strategy reading BTC's spot against SOL's strike.

        Returns `(ctx, status, detail)`. `status` is 'ok' or a cycle-level skip
        reason that will be attributed to every strategy.

        `strike` is a MEASURED PROXY, never spot. These markets settle on a
        Chainlink 60-second BTC/USD TWAP that Gamma does not publish anywhere on
        the market object (see `context.CRYPTO_CONFIG_KEY`). Substituting spot
        would put a number in the field that is wrong precisely during the fast
        moves that generate signals, so this does not do that either: it
        reconstructs the same 60-second average from 1m klines and refuses to
        act inside the error that reconstruction was measured to have.

        The error is not assumed, it is scored against the oracle by
        `backtest/measure_strike_proxy.py` (199 windows, 2026-08-18): 42% wrong
        below 1 bp, 3.8% wrong at or above 5 bps. `evaluate_strategy` enforces
        the 5 bp floor, so mid_price_continuation and corridor_collector can now
        run - but only on leads the proxy has been shown to resolve.
        """
        now = time.time() if now is None else now
        # A SECOND clock, and deliberately not derived from `now`. `now` is wall
        # time and is injected by tests and by the loop; the spot cache's TTL
        # must be measured on a clock that cannot step backwards under NTP.
        now_mono = time.monotonic()
        runtime = self.runtimes[asset]
        detail: dict = {'window_ts': window_ts, 'asset': asset}

        # -- Stage 1: the 5m market. Sequential and unavoidably so - the token
        # ids every book read is keyed by come out of THIS response, so nothing
        # below can be issued until it lands.
        market, status = self._timed('market_5m', get_updown_5m_checked,
                                     self.client, asset, window_ts)
        if market is None:
            detail['market_status'] = status
            if status == 'read_failed':
                return None, SKIP_API_ERROR, detail
            # 'not_found' plus every MARKET_DROP_REASONS value: Gamma answered,
            # and the answer was that there is no usable market here.
            return None, SKIP_NO_MARKET, detail

        detail['market_slug'] = market.slug
        # Forge proposal 038 rule 2: the ledger observes every market the loop
        # FETCHED, and this is the fetch. It is deliberately HERE and not at
        # the entry path, and not after the `not books` return below either -
        # a market whose books were unreadable was still fetched, and dropping
        # it would rebuild the exact selection the ledger exists to remove.
        # Cheap and idempotent per slug: no network, no database, and the same
        # market re-fetched every 5 seconds for five minutes registers once.
        if self.ledger is not None:
            self.ledger.observe(market, window_ts, UPDOWN_5M_DURATION)

        # -- Stage 2, PARALLEL: everything that depends only on stage 1 or on
        # nothing at all. Both 5m books, the spot read and the 15m market
        # lookup - four reads that used to be four sequential ones.
        #
        # LOAD DELTA, stated rather than buried: on the `not books` early return
        # below, this has already issued the spot read and the 15m market read
        # that the old sequential order returned before reaching. That is two
        # extra GETs on a path the live session hits rarely - the running loop's
        # counters are dominated by `strategy:*`, i.e. status `ok` - against
        # budgets two orders of magnitude above our steady state. It buys the
        # parallelism on the almost-always path, and that is the trade.
        tasks: List[Tuple[str, Callable]] = [
            ('book5:' + o.token_id,
             functools.partial(self._fetch_book_checked, o.token_id))
            for o in market.outcomes
        ]
        tasks.append(('spot',
                      functools.partial(self.spot_checked, asset, now_mono)))
        if self.include_15m:
            # Every registered asset has a 15m market, verified live on btc, eth
            # and sol (2026-08-18). `updown_15m_slug` floors the 5m window_ts
            # into its containing 15m window rather than trusting the caller.
            tasks.append(('market15',
                          functools.partial(get_market_by_slug_checked,
                                            self.client,
                                            updown_15m_slug(asset, window_ts))))
        stage2 = self._timed('stage2_parallel', self._run_parallel, tasks)

        books, book_status = self._books_from_results(market.outcomes, stage2,
                                                      'book5:')
        detail['book_status'] = book_status

        if not books:
            # Every side unreadable. WHICH KIND of unreadable decides the
            # response, in a fixed precedence: our own code throwing outranks a
            # venue outage, which outranks an empty book. Backing off is right
            # when the venue is unreachable, wrong when it is merely quiet, and
            # neither is right when the fault is ours.
            values = set(book_status.values())
            if STATUS_FETCH_EXCEPTION in values:
                return None, SKIP_CYCLE_EXCEPTION, detail
            if SKIP_API_ERROR in values:
                return None, SKIP_API_ERROR, detail
            return None, SKIP_NO_LIQUIDITY, detail

        self._timed('candles', self._refresh_candles, runtime, now)
        windows: List = []
        if runtime.candles is not None:
            built = price_windows_checked(runtime.candles,
                                          lookback=WINDOW_LOOKBACK)
            windows = built['windows']
            if built['drops']:
                detail['candle_drops'] = built['drops']
        detail['windows'] = len(windows)
        detail['candles_age_sec'] = (round(now - runtime.candles_at, 1)
                                     if runtime.candles is not None else None)

        spot_result, spot_exc = stage2.get('spot', (None, None))
        if spot_exc is not None or spot_result is None:
            # `fetch_spot_checked` returns a dict on every failure it
            # anticipates, so reaching here means OUR code raised. Synthesise
            # the same shape rather than letting a None flow onward into the
            # lead computation, and count it under its own name.
            self.health['spot_fetch_exception:' + asset] += 1
            if spot_exc is not None:
                logger.error('PM SHADOW spot fetch raised for %s: %s: %s',
                             asset, type(spot_exc).__name__, spot_exc)
            spot_result = {
                'spot': None, 'source': None, 'asset': asset,
                'failures': {'fetch_exception': '{}: {}'.format(
                    type(spot_exc).__name__, spot_exc) if spot_exc else
                    'no result recorded'},
                'age_sec': None, 'cached': False,
            }
        spot = spot_result['spot']
        detail['spot_source'] = spot_result['source']
        # The cache's age stamp, carried exactly like `candles_age_sec` so a
        # decision taken on a stale spot stays identifiable after the fact.
        detail['spot_age_sec'] = spot_result.get('age_sec')
        detail['spot_cached'] = spot_result.get('cached')
        if spot is None:
            detail['spot_failures'] = spot_result['failures']
            self.health['spot_unavailable:' + asset] += 1

        # ATR in BASIS POINTS. corridor_collector divides lead_bps by atr14, so
        # a USD ATR here would be ~10,000x too large and the ratio gate would
        # silently never pass. Units are the bug this comment exists to prevent.
        atr14 = None
        if windows and spot:
            atr_usd = window_atr(windows, ATR_WINDOWS_FOR_CORRIDOR)
            atr14 = (atr_usd / spot) * 10_000.0

        # The strike is a Chainlink 60s TWAP that Gamma does not publish. It is
        # NOT spot and is never spot: `StrikeProxy` rebuilds the same 60-second
        # average from 1m klines, and `backtest/measure_strike_proxy.py` scores
        # that reconstruction against the oracle. The proxy is a coin flip below
        # 1 bp and ~96% right above 5, so it is supplied here and REFUSED below
        # the measured floor in `evaluate_strategy` - supplying it is safe only
        # because the floor is enforced.
        strike_result = self._timed('strike', runtime.strike_proxy.strike_for,
                                    window_ts, now=now)
        strike = strike_result['strike']
        detail['strike_source'] = strike_result['source']
        detail['strike_is_proxy'] = strike_result['is_proxy']
        detail['strike_bar_age_sec'] = strike_result['bar_age_sec']
        if strike is None:
            self.health['strike_unavailable:' + asset] += 1

        lead_bps = None
        if spot is not None and strike:      # truthy also guards strike == 0
            lead_bps = (spot - strike) / strike * 10_000.0
        detail['lead_bps'] = (None if lead_bps is None else round(lead_bps, 3))

        market_15m = None
        books_15m: Dict[str, object] = {}
        if self.include_15m:
            # The 15m market lookup was ISSUED in stage 2 above, concurrently
            # with the 5m books; this only reads its result.
            m15_value, m15_exc = stage2.get('market15', (None, None))
            if m15_exc is not None:
                self.health['market_15m_exception:' + asset] += 1
                logger.error('PM SHADOW 15m market read raised for %s: %s: %s',
                             asset, type(m15_exc).__name__, m15_exc)
                m15, s15 = None, STATUS_FETCH_EXCEPTION
            elif m15_value is None:
                m15, s15 = None, STATUS_FETCH_EXCEPTION
            else:
                m15, s15 = m15_value
            detail['market_15m_status'] = s15
            if m15 is not None:
                market_15m = m15
                # The 15m market is a SEPARATE market with its own slug, its
                # own condition id and its own settlement, and `positions.pair`
                # carries 15m slugs (25 of 730 distinct pairs at 2026-08-19).
                # Observing only the 5m market would leave every 15m position
                # unresolvable and the coverage number short by construction.
                # Its window is the containing 15m one, floored the same way
                # `updown_15m_slug` floors it - passing the 5m `window_ts`
                # would put a close time up to 10 minutes early on the record.
                if self.ledger is not None:
                    self.ledger.observe(
                        m15,
                        (window_ts // UPDOWN_15M_DURATION) * UPDOWN_15M_DURATION,
                        UPDOWN_15M_DURATION)
                # -- Stage 3, PARALLEL: the 15m books. These could not join
                # stage 2 because the token ids they are keyed by come out of
                # the 15m market response stage 2 was still waiting on.
                tasks15: List[Tuple[str, Callable]] = [
                    ('book15:' + o.token_id,
                     functools.partial(self._fetch_book_checked, o.token_id))
                    for o in m15.outcomes
                ]
                stage3 = self._timed('stage3_parallel', self._run_parallel,
                                     tasks15)
                # The 15m book statuses used to be discarded (`book, _ = ...`),
                # so a 15m outage and a quiet 15m book produced the same silent
                # nothing. They are counted and categorised now (convention 20).
                books_15m, status_15m = self._books_from_results(
                    m15.outcomes, stage3, 'book15:')
                detail['book_status_15m'] = status_15m

        ctx = MarketContext(
            window_ts=window_ts,
            windows=windows,
            market=market,
            books=books,
            spot=spot,
            strike=strike,
            seconds_into_window=now - window_ts,
            market_15m=market_15m,
            books_15m=books_15m,
            lead_bps=lead_bps,
            atr14=atr14,
        )
        return ctx, 'ok', detail

    # -- market_tape --------------------------------------------------------

    def _write_market_tape(self, ctx) -> int:
        """Persist this context's quotes to `market_tape`. Returns rows accepted.

        D-362 R4. Called on every NON-CRYPTO context the loop builds, before
        any strategy is consulted, so the tape is a function of the cycle and
        not of the roster - the defect this replaces.

        NEVER RAISES. This is instrumentation: a tape failure must not take a
        trading cycle down with it. `PriceTapeByToken` already swallows and
        counts sqlite errors per row; the belt here catches anything the
        stamping itself could throw on a malformed market object.

        CRYPTO WINDOWS ARE EXCLUDED, deliberately and unchanged from proposal
        031 phase 1: a crypto token id is new every 5-minute window, so a
        persisted crypto tape is never read back after a restart, and writing
        it would multiply the table's volume for no consumer. Off-crypto token
        ids live for days and poll twelve times slower - that is the tape a
        restart used to reset to empty, and the tape the complement check
        reads.
        """
        if ctx is None or getattr(ctx, 'is_crypto_window', False):
            return 0
        self.tape_contexts += 1
        try:
            # The SAME clock `DipArb.clock` used, so rows written before and
            # after this move carry timestamps on one scale: the window start
            # plus the offset into it, derived from the context rather than
            # read off the wall clock, so a row is reproducible from a logged
            # context.
            now = float(ctx.window_ts) + float(ctx.seconds_into_window or 0.0)
            accepted = observe_market_into_tape(self.market_tape, ctx, now,
                                                persist=True)
        except Exception:
            self.health['market_tape_write_exception'] += 1
            logger.warning('market_tape: write pass failed for %s',
                           getattr(ctx.market, 'slug', None), exc_info=True)
            return 0
        rows = sum(1 for ok in accepted.values() if ok)
        self.tape_rows_written += rows
        return rows

    # -- evaluation ---------------------------------------------------------

    def _log_and_count(self, strategy_name: str, market_slug: Optional[str],
                       disposition: str, reason: str, features: dict,
                       confidence: float = 0.0, acted: bool = False,
                       window_ts: Optional[int] = None,
                       log_csv: bool = True,
                       counts: Optional[Counter] = None) -> str:
        """Count one evaluation, write its signals row, and (usually) its CSV row.

        `log_csv=False` is used on exactly one path: when the paper adapter has
        ALREADY written its own row for this window. Writing a second one would
        double-count the window in `adapter.decision_counts` and put two rows in
        the CSV for one decision, which is the mirror image of the silent-skip
        problem - a window that looks like two.
        """
        self._count(disposition, counts)
        if log_csv:
            self.adapter.log_skip(
                strategy_name, market_slug or 'unknown', reason,
                window_ts='' if window_ts is None else window_ts,
                features=';'.join('{}={}'.format(k, v)
                                  for k, v in sorted(features.items())))
        self.store.record_signal(
            strategy_id=strategy_name, market_slug=market_slug,
            pattern=strategy_name, direction='long', confidence=confidence,
            features=features, acted=acted, skip_reason=reason,
            market_duration=self._market_duration_for(strategy_name,
                                                      market_slug))
        return disposition

    @staticmethod
    def _context_features(detail: dict) -> dict:
        """The context AGE STAMPS that belong on every decision row.

        Every cached input carries its age here, and every skip row gets them
        too - including the strike-gate skips, which return BEFORE the strategy
        evaluates. Those rows used to carry no ages at all, so a NOT_TESTED row
        could not be dated against the context that produced it, and a session
        that skipped an hour on stale candles looked exactly like one that
        skipped an hour on fresh ones.

        `candles_age_sec` was already carried on the post-evaluate rows. The
        spot pair is new and rides beside it for the identical reason: a cached
        value with no age stamp is indistinguishable from a fresh one after the
        fact, and a cache without an age stamp is a lie.

        `asset` is here rather than duplicated at three call sites - with three
        underlyings in flight, an untagged decision row is not self-describing.
        """
        lead_bps = detail.get('lead_bps')
        return {
            'asset': detail.get('asset'),
            'candles_age_sec': detail.get('candles_age_sec'),
            'spot_age_sec': detail.get('spot_age_sec'),
            'spot_cached': detail.get('spot_cached'),
            # The proxy's MEASURED error rides on every row, not just the
            # rejected ones. A strategy that FIRES at 1.2 bps did so inside a
            # band where the proxy disagrees with the oracle 23.5% of the time,
            # and that fact has to be on the row to be recoverable later: a
            # loss there is weak evidence about the strategy and strong
            # evidence about the strike, and nothing downstream can tell those
            # apart from a price alone. `None` is UNKNOWN, never 0.0 - rows for
            # strategies that need no strike carry no lead and get None here.
            'strike_proxy_noise_floor_bps': noise_floor_bps_for(
                detail.get('asset')),
            'strike_proxy_disagreement_pct': disagreement_pct_for_lead(
                lead_bps),
        }

    def evaluate_strategy(self, strategy, ctx: MarketContext,
                          detail: dict,
                          counts: Optional[Counter] = None) -> str:
        """Evaluate one strategy against one context. Returns the disposition.

        Always returns, and every exit path has counted and logged first.
        """
        name = getattr(strategy, 'strategy_name', str(strategy))
        slug = getattr(ctx.market, 'slug', None)
        # Bound ONCE so no exit path below can forget the counter space it
        # belongs to. `counts=None` is the crypto identity space; the weather
        # cycle passes its own Counter. See `_count`.
        _log = functools.partial(self._log_and_count, counts=counts)

        # Strike-dependent strategies are gated BEFORE they evaluate, not after.
        # Letting one read a sub-noise-floor lead and decline on its own terms
        # would record a measurement error as a strategy decision, and the two
        # are indistinguishable once they share a skip reason.
        if getattr(strategy, 'needs_strike', False):
            if ctx.strike is None:
                return _log(name, slug,
                                           'strategy:no_spot_or_strike',
                                           'no_spot_or_strike',
                                           dict(self._context_features(detail),
                                                strike_available=False),
                                           window_ts=ctx.window_ts)
            # The 5 bp floor was DERIVED from the BTC proxy against the BTC
            # oracle (`backtest/measure_strike_proxy.py`, 199 windows). It is
            # applied unchanged to ETH and SOL because the instrument is
            # identical - same Chainlink 60s TWAP, same 1m kline
            # reconstruction. That was an ARGUMENT; it has since been MEASURED
            # per asset (`research/strike_proxy_by_asset_500w.json`, 500
            # windows each). At this floor the proxy disagrees with the oracle
            # 5.1% of the time on BTC, 9.3% on ETH and 15.8% on SOL. One
            # constant, three error rates spanning ~3x - so the rate rides on
            # every gated row rather than living only in a comment
            # (convention 22).
            # The floor is PER ASSET now, so it is read from the row's own
            # asset rather than from one module constant. The direction of
            # this comparison is the thing to keep straight: the gate is
            # `abs(lead) < floor`, so a BIGGER floor rejects MORE windows.
            row_asset = detail.get('asset')
            active_floor_bps = noise_floor_bps_for(row_asset)
            if is_inside_noise_floor(ctx.lead_bps, active_floor_bps):
                # D-297: the dict below says what all three assets do at the
                # 5.0 bps threshold the measurement was TAKEN at;
                # `strike_proxy_error_at_floor_pct` says what THIS row's asset
                # does there. `None` means unmeasured, never zero, and is
                # flagged rather than left to look like a good reading.
                #
                # `strike_proxy_error_at_active_floor_pct` is the separate and
                # more honest field: it is populated ONLY when the floor in
                # force equals the threshold the error was measured at, and is
                # None otherwise. Without it, moving the floor off 5.0 would
                # keep publishing a 5.0-bps error rate under a name that reads
                # as "the error at the floor", which is exactly the stale
                # number this codebase keeps having to hunt down.
                #
                # The `_n` / `_low_sample` pair is the rest of D-297. A
                # rate with no sample size beside it reads as settled. At the
                # 500-window measurement all three clear convention 7's
                # threshold of 100 (btc n=175, eth n=248, sol n=196), so
                # `strike_proxy_error_low_sample` is False on every gated row
                # now; at 220 windows it was True for BTC (75) and SOL (84).
                # The flag is DERIVED from `n` inside `strike.py`, never
                # asserted per asset, so re-measuring cleared it by itself.
                error_pct = error_at_floor_pct_for(row_asset)
                active_error_pct = active_floor_error_pct_for(row_asset)
                error_n, error_low_sample = error_sample_at_floor_for(row_asset)
                gate_feats = dict(
                    self._context_features(detail),
                    lead_bps=(None if ctx.lead_bps is None
                              else round(ctx.lead_bps, 3)),
                    noise_floor_bps=active_floor_bps,
                    noise_floor_default_bps=STRIKE_PROXY_NOISE_FLOOR_BPS,
                    noise_floor_source=NOISE_FLOOR_SOURCE_ASSET,
                    noise_floor_measured_error_by_asset=dict(
                        NOISE_FLOOR_ERROR_BY_ASSET),
                    noise_floor_error_measured_at_bps=(
                        NOISE_FLOOR_ERROR_MEASURED_AT_BPS),
                    strike_proxy_error_at_floor_pct=error_pct,
                    strike_proxy_error_at_active_floor_pct=active_error_pct,
                    strike_proxy_error_n=error_n,
                    strike_proxy_error_low_sample=error_low_sample,
                    strike_is_proxy=True)
                if error_pct is None:
                    gate_feats[ERROR_UNAVAILABLE_FLAG] = True
                return _log(
                    name, slug, 'strategy:' + SKIP_PROXY_NOISE,
                    SKIP_PROXY_NOISE, gate_feats, window_ts=ctx.window_ts)

        decision = strategy.evaluate(ctx)
        feats = dict(decision.features or {})
        feats['cycle'] = detail.get('cycle')
        # The asset is stamped on every decision row. The market slug already
        # encodes it, but a slug is a string a reader has to parse and this is
        # the column a per-asset comparison groups by: same strategy_id, three
        # assets, so `PM_fair_value_arb` on BTC can be scored against itself on
        # SOL without anyone re-deriving which is which.
        feats['asset'] = detail.get('asset')
        feats['window_ts'] = ctx.window_ts
        feats['seconds_into_window'] = (None if ctx.seconds_into_window is None
                                        else round(ctx.seconds_into_window, 1))
        feats.update(self._context_features(detail))
        try:
            confidence = float(feats.get('confidence') or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        if decision.action == 'QUOTE':
            # A maker quote is STILL not an entry and is still never filled as
            # one. What changed is that the legs are now RESTED on the book
            # instead of being thrown away: `simulate_maker_buy` returns a
            # RestingOrder, and only a later snapshot that crosses STRICTLY
            # through our price can turn one into a position. See
            # `_attempt_maker_quotes` and `observe_maker_orders`.
            return self._attempt_maker_quotes(strategy, decision, ctx, feats,
                                              confidence, counts)

        if not decision.is_entry:
            reason = decision.reason or 'unspecified'
            return _log(name, slug, 'strategy:' + reason, reason,
                                       feats, confidence,
                                       window_ts=ctx.window_ts)

        if not decision.legs:
            # An ENTER with no legs is a strategy bug, not a market condition.
            return _log(name, slug, SKIP_NO_LEGS, SKIP_NO_LEGS,
                                       feats, confidence,
                                       window_ts=ctx.window_ts)

        return self._attempt_entry(strategy, decision, ctx, feats, confidence,
                                   counts)

    # -- the stop that gets written to the positions row ----------------------

    @staticmethod
    def _collapse_durations(durations) -> Optional[str]:
        """Collapse the per-leg durations of ONE decision to one value.

        One distinct duration means the decision sat entirely on that
        window. Two means it spanned both, which is the corridor family and
        is exactly what mixed exists to say. Nothing readable means None:
        an unknown is a missing number, not a 5m (convention 20).
        """
        seen = {d for d in durations if d}
        if not seen:
            return None
        if len(seen) == 1:
            return next(iter(seen))
        return 'mixed'

    def _market_duration_for(self, strategy_name: Optional[str],
                             market_slug: Optional[str]) -> Optional[str]:
        """The market-duration key for a row with no legs to read.

        The strategy DECLARATION wins over the slug, and that ordering is
        the whole reason the declaration exists. On a skip the loop records
        ctx.market.slug, which is always the 5m market - even for
        PM_longshot_fade_hold_to_resolution, which evaluates nothing but
        the 15m book. Trusting the slug there would write 5m onto a 15m
        evaluation, which is the original keying bug wearing a new column.

        Falling back to the slug is not a default: it reads the duration
        off the market actually named on the row. When neither can say -
        an undeclared strategy on a weather slug, say - the answer is None
        and NULL is written.
        """
        declared = getattr(self._strategy_named(strategy_name),
                           'market_duration_scope', None)
        if declared:
            return declared
        return market_duration_for_slug(market_slug)
    def _strategy_named(self, name: Optional[str]):
        """The strategy instance called `name`, or None.

        Only used by the maker path, which knows the strategy by name because
        the fill arrives cycles after the decision. Linear over a 19-element
        list rather than a cached dict: this runs once per maker fill, and a
        cache is one more thing that can disagree with `self.strategies`.
        """
        if not name:
            return None
        for s in self.strategies:
            if getattr(s, 'strategy_name', None) == name:
                return s
        return None

    def _entry_stop_px(self, strategy, position) -> Optional[float]:
        """The discretionary stop for a new fill, or None.

        None means "this strategy has no discretionary stop", which is the
        truth for every hold-to-resolution strategy in the registry and is
        written as 0.00 - the structural floor - by `record_entry`.

        Capability dispatch on `stop_price_for`, the same shape `manage_exits`
        uses for `estimate`: a strategy opts in by shipping the method, and the
        six that manage their own exits do. Nothing here re-derives a stop, so
        a strategy whose stop rule changes cannot be left behind by this file
        (convention 23).

        A `ValueError` from the helper means the fill price is unpriceable
        (at or below 0.00, above 1.00) - a bookkeeping fault upstream, not a
        stop of zero. It is counted under its own health key rather than
        rounded into the same 0.00 a hold-to-resolution strategy writes,
        because those are two different facts (convention 20).
        """
        fn = getattr(strategy, 'stop_price_for', None)
        if not callable(fn):
            return None
        try:
            return float(fn(position.avg_price,
                            getattr(position, 'outcome_side', None)))
        except (ValueError, TypeError, AssertionError) as exc:
            self.health['entry_stop_unpriceable'] += 1
            logger.warning('could not price a stop for %s @ %r: %s',
                           getattr(strategy, 'strategy_name', strategy),
                           getattr(position, 'avg_price', None), exc)
            return None

    def _risk_open_exposures(self) -> Tuple[risk_constraints.Exposure, ...]:
        """The open book, as the model-free risk evaluator sees it (D-343).

        `max_loss_usdc` is the exact premium at risk on a binary - the same
        number `exposures_from_adapter` in risk_gate.py uses for the PM gate's
        own exposure snapshot, so the two consult one definition of "how much
        is this position worth if it loses" even though they aggregate it
        differently. Resting (unfilled) maker quotes are deliberately excluded,
        matching `exposures_from_adapter`: nothing is at risk until a quote
        actually fills.
        """
        return tuple(
            risk_constraints.Exposure.from_slug(
                pos.market_slug, pos.window_ts, pos.max_loss_usdc)
            for pos in self.adapter.open_positions())

    def _risk_equity_state(self) -> risk_constraints.EquityState:
        """Current equity plus the running peak read off `equity_snapshots`.

        Peak is read from history AND compared against the live current value:
        a live tick that exceeds every recorded snapshot IS the new peak, not
        a drawdown from a stale one. No history at all reads as "no drawdown
        has ever been observed", which is genuinely 0.0 - convention 11 is
        about treating an UNREADABLE state as empty, and an account that has
        never been snapshotted before has a real, measured peak of exactly its
        current equity, not an unmeasurable one.
        """
        current = self.adapter.get_equity()
        row = self.store.conn.execute(
            'SELECT MAX(equity) AS peak FROM equity_snapshots WHERE mode = ?',
            (MODE,)).fetchone()
        historical_peak = (row['peak'] if row and row['peak'] is not None
                           else current)
        return risk_constraints.EquityState(
            current_usd=current, peak_usd=max(current, historical_peak))

    def _check_risk_constraints(self, leg_slug: Optional[str],
                                window_ts: Optional[int],
                                notional_usd: float) -> 'risk_constraints.Decision':
        """The model-free entry constraints (D-343 Task 1): per-trade,
        per-event, aggregate and portfolio drawdown - independent of the risk
        gate above and of any forecast. Every denial writes a `risk_events`
        row and a drawdown breach engages `engine.halt`, both handled inside
        `evaluate_and_record`; this method only builds the inputs.
        """
        candidate = risk_constraints.Exposure.from_slug(
            leg_slug, window_ts, notional_usd)
        return risk_events.evaluate_and_record(
            self.store.conn, self._risk_open_exposures(), candidate,
            self._risk_equity_state(), limits=SHADOW_RISK_LIMITS)

    def _attempt_entry(self, strategy, decision, ctx: MarketContext,
                       feats: dict, confidence: float,
                       counts: Optional[Counter] = None) -> str:
        """Halt check, risk gate, then the paper adapter, for every leg.

        Multi-leg (corridor_collector) fills legs leader-first, which is the
        strategy's own stated ordering: if the second leg fails you are left
        holding the side that is currently winning. A partial pair is recorded
        as a partial pair in `health`, never reported as a clean entry.
        """
        name = getattr(strategy, 'strategy_name', str(strategy))
        slug = getattr(ctx.market, 'slug', None)
        _log = functools.partial(self._log_and_count, counts=counts)

        # 1. The kill switch, before anything else. Checked HERE so a halted
        # window is counted as `halted` rather than reaching the adapter and
        # returning an anonymous refusal. The adapter checks again; that is the
        # backstop, not a duplicate.
        if is_halted():
            return _log(
                name, slug, SKIP_HALTED, SKIP_HALTED,
                dict(feats, halt_note=('polymarket halt blocks ENTRIES only; a '
                                       'binary held to resolution has no sell '
                                       'path in paper mode, so a halt cannot '
                                       'flatten open exposure')),
                confidence, window_ts=ctx.window_ts)

        filled = []
        first_block: Optional[str] = None
        adapter_logged = False

        # Forge proposal 030 stage 1 (pm_one_legged_pair_unwind_guard),
        # log-only: a `pair_id` links every leg of a multi-leg decision so a
        # one-legged fill can finally be COUNTED, not just suspected from a
        # single unexplained -$4.20 row. None (and every field below) for a
        # single-leg decision - the vast majority of strategies here never
        # touch this branch. `pair_cost_expected` is priced from EVERY leg
        # the strategy asked for, before leg 1 is attempted, exactly as
        # proposal 030 build order item 1 specifies ("record
        # pair_cost_expected ... before submitting leg 1"); it is None only
        # if a leg carries no `expected_price` at all.
        pair_id = str(uuid.uuid4()) if len(decision.legs) > 1 else None
        pair_cost_expected = None
        if pair_id is not None:
            prices = [leg.expected_price for leg in decision.legs]
            if all(p is not None for p in prices):
                pair_cost_expected = round(sum(prices), 4)
        prev_leg_fill_monotonic: Optional[float] = None
        # D-339 clause (3): one entry per LEG that actually filled, read
        # off the market object the leg was routed to rather than off the
        # decision. A corridor fills a 15m leg and a 5m leg from this same
        # loop, and collapsing those two readings is what produces mixed.
        filled_durations = []

        for leg_index, leg in enumerate(decision.legs, start=1):
            leg_slug = leg.market_slug or slug
            slug_15 = getattr(ctx.market_15m, 'slug', None)
            market = (ctx.market_15m
                      if (leg.market_slug and slug_15
                          and leg.market_slug == slug_15)
                      else ctx.market)
            token_id = (market.token_id(leg.outcome_side)
                        if market is not None else None)
            if token_id is None:
                first_block = first_block or SKIP_UNKNOWN_TOKEN
                self.health['leg_unknown_token'] += 1
                continue
            book = ctx.books.get(token_id) or ctx.books_15m.get(token_id)
            if book is None:
                first_block = first_block or SKIP_NO_LIQUIDITY
                self.health['leg_no_book'] += 1
                continue

            # Proposal 030: snapshot this leg's own book before the fill
            # attempt. Under the CURRENT synchronous single-tick execution
            # model every leg fills (or fails to) inside this same for-loop,
            # off the SAME `ctx` book snapshot the strategy's `evaluate()`
            # already read - so "at signal" and "at fill" are numerically
            # identical by construction today, not independently measured.
            # Recorded as two separate pairs of columns anyway, honestly
            # duplicated rather than invented, so the schema does not need to
            # change the day leg submission actually becomes asynchronous
            # (stage 2/3, not built here).
            leg_bid = book.best_bid
            leg_ask = book.best_ask

            # 2. The risk gate. Its reason string is carried VERBATIM:
            # re-wording it here would make this log disagree with the gate's
            # own tests about what blocked the order.
            verdict = self.gate.check_adapter_order(
                self.adapter, leg_slug, leg.outcome_side,
                premium=leg.premium, requested_shares=leg.shares, mode=MODE)
            if not verdict.approved:
                first_block = first_block or ('risk_gate:' + verdict.reason)
                self.health['risk_gate_blocks'] += 1
                continue

            # 2b. The model-free entry constraints (D-343). Checked on the
            # gate's OWN sized notional, after the gate above but strictly
            # before the adapter fills - the per-trade and aggregate caps are
            # no longer independently defined here (D-343 R1 delegated them),
            # and the per-event cap has no equivalent in the gate at all.
            risk_decision = self._check_risk_constraints(
                leg_slug, ctx.window_ts, verdict.notional_usdc)
            if not risk_decision.allowed:
                first_block = first_block or (
                    'risk_constraint:' + risk_decision.reason)
                self.health['risk_constraint_blocks'] += 1
                continue

            # 3. The adapter. It owns the book walk, the fill, and its own log
            # row; its refusal reason is recovered from the decision_counts
            # delta so the taxonomy in our counters is the adapter's own rather
            # than our guess at it.
            before = dict(self.adapter.decision_counts)
            position = self.adapter.simulate_taker_buy(
                strategy=name, market_slug=leg_slug, token_id=token_id,
                outcome_side=leg.outcome_side, limit_price=leg.limit_price,
                shares=verdict.shares, window_ts=ctx.window_ts,
                features={k: v for k, v in feats.items() if v is not None},
                book=book)
            if position is None:
                adapter_logged = True
                moved = [k for k, v in self.adapter.decision_counts.items()
                         if v > before.get(k, 0)]
                first_block = first_block or (
                    'adapter:' + (moved[0] if moved else 'unreported'))
                self.health['adapter_refusals'] += 1
                continue

            leg2_latency_ms = None
            if pair_id is not None and prev_leg_fill_monotonic is not None:
                leg2_latency_ms = round(
                    (time.monotonic() - prev_leg_fill_monotonic) * 1000.0, 3)
            prev_leg_fill_monotonic = time.monotonic()

            filled.append((position, leg, leg_index, leg_bid, leg_ask,
                           leg2_latency_ms))
            filled_durations.append(market_duration_for_slug(
                getattr(market, 'slug', None) or leg_slug))
            self._cycle_selected_tokens.add(token_id)

        if not filled:
            reason = first_block or 'adapter:unreported'
            return _log(name, slug, reason, reason, feats,
                                       confidence, window_ts=ctx.window_ts,
                                       log_csv=not adapter_logged)

        if len(filled) < len(decision.legs):
            self.health['partial_pairs'] += 1

        # Proposal 030: "at completion" (build order item 1) means every leg
        # in the pair actually filled - a partial pair has no pair cost, it
        # has an open question, and pair_cost_actual stays None rather than
        # silently describing a pair that never completed.
        pair_cost_actual = None
        if pair_id is not None and len(filled) == len(decision.legs):
            pair_cost_actual = round(
                sum(p.avg_price for p, *_ in filled), 4)

        # The entry is counted ONCE per evaluation (the identity counts
        # evaluations, not legs) and written once per leg into the DB.
        self._count('entry', counts)
        signal_id = self.store.record_signal(
            strategy_id=name, market_slug=slug, pattern=name, direction='long',
            confidence=confidence,
            features=dict(feats, legs_filled=len(filled),
                          legs_requested=len(decision.legs),
                          outcome_side=filled[0][1].outcome_side,
                          pair_id=pair_id,
                          pair_cost_expected=pair_cost_expected),
            acted=True, skip_reason=None,
            market_duration=(self._collapse_durations(filled_durations)
                             or self._market_duration_for(name, slug)))
        for position, leg, leg_index, leg_bid, leg_ask, leg2_latency_ms \
                in filled:
            self.store.record_entry(
                position, signal_id=signal_id, limit_price=leg.limit_price,
                strategy_id=name,
                stop_px=self._entry_stop_px(strategy, position),
                pair_id=pair_id,
                leg_index=(leg_index if pair_id is not None else None),
                leg_target_px=(leg.expected_price if pair_id is not None
                              else None),
                leg2_latency_ms=leg2_latency_ms,
                pair_cost_expected=pair_cost_expected,
                pair_cost_actual=pair_cost_actual,
                leg_bid_at_signal=(leg_bid if pair_id is not None else None),
                leg_ask_at_signal=(leg_ask if pair_id is not None else None),
                leg_bid_at_fill=(leg_bid if pair_id is not None else None),
                leg_ask_at_fill=(leg_ask if pair_id is not None else None))
            self.store.audit('position_opened', {
                'position_id': position.position_id,
                'strategy': name,
                'market_slug': position.market_slug,
                'outcome_side': position.outcome_side,
                'shares': position.shares,
                'avg_price': position.avg_price,
                'cost_usdc': position.cost_usdc,
                'max_loss_usdc': position.max_loss_usdc,
                'breakeven_win_rate': position.breakeven_win_rate,
                'mode': MODE,
            })
            logger.info('PM SHADOW ENTER %s %s %s %.0f sh @ %.4f (cost $%.2f)',
                        name, position.market_slug, position.outcome_side,
                        position.shares, position.avg_price,
                        position.cost_usdc)
        return 'entry'

    # -- maker quotes ---------------------------------------------------------

    def _resting_buys_for(self, strategy_name: str) -> list:
        """This strategy's open resting BUYS, across every market and asset."""
        return [o for o in self.adapter.open_resting_orders()
                if o.side == 'BUY' and o.strategy == strategy_name]

    def _attempt_maker_quotes(self, strategy, decision, ctx: MarketContext,
                              feats: dict, confidence: float,
                              counts: Optional[Counter] = None) -> str:
        """Rest a QUOTE decision's legs on the book. NEVER opens a position.

        This is the other half of `_attempt_entry`, and the difference between
        the two is the whole maker/taker distinction stated in code: a taker
        knows its fill at call time, so `_attempt_entry` can return `'entry'`; a
        maker does not, so the best this can return is "the order is on the
        book". The fill, if it ever comes, is booked one or more cycles later by
        `observe_maker_orders` against a snapshot that crosses our price.

        ## The honest limitation, stated here because it is load-bearing

        We have NO TRADE PRINTS. A resting order is judged against successive
        BOOK SNAPSHOTS, and the only snapshot-visible evidence that sell flow
        came down through our bid is size resting strictly BELOW it (a real book
        cannot stay crossed). So a fill that happened and reversed entirely
        between two polls is invisible to us and is scored as a no-fill. That
        biases this model PESSIMISTIC, which is the direction to be wrong in for
        a strategy whose entire claimed edge is "our resting order got hit".
        The alternative - counting a touch as a fill - books all the good fills
        and none of the adverse selection and would produce box_builder's P&L
        out of nothing. See the paper adapter's module docstring.

        ## Every no-rest path is counted and categorised (convention 20)

        `maker_halted`, `maker_quote_without_legs`, `maker_quote_already_resting`,
        `maker_rest_budget_exhausted`, `unknown_outcome_token`, `no_liquidity`,
        `risk_gate:*` and `maker_adapter:*` are eight distinct causes and none
        of them shares a counter with another. The old single bucket
        (`maker_quote_not_simulable`) pooled all eight and several thousand rows
        of it said nothing about either strategy.
        """
        name = getattr(strategy, 'strategy_name', str(strategy))
        slug = getattr(ctx.market, 'slug', None)
        _log = functools.partial(self._log_and_count, counts=counts)

        # 1. The kill switch, FIRST, exactly as on the entry path. A resting bid
        # that fills is a new entry, and the Polymarket halt contract blocks
        # entries - so a halt must refuse the rest rather than defer it. The
        # buys ALREADY resting are cancelled by `observe_maker_orders`, which
        # runs every cycle whether or not anybody quotes; this is the refusal
        # half and that is the cancellation half (convention 23: a fix at one
        # site is not a fix).
        if is_halted():
            return _log(
                name, slug, SKIP_MAKER_HALTED, SKIP_MAKER_HALTED,
                dict(feats, halt_note=('halt refuses NEW resting buys and '
                                       'cancels the ones already on the book; '
                                       'resting SELLS are left alone because '
                                       'an ask over an open position reduces '
                                       'risk')),
                confidence, window_ts=ctx.window_ts)

        if not decision.legs:
            # A QUOTE with nothing to quote is a strategy bug, not a market
            # condition - the mirror of `enter_without_legs`.
            return _log(name, slug, SKIP_MAKER_NO_LEGS, SKIP_MAKER_NO_LEGS,
                        feats, confidence, window_ts=ctx.window_ts)

        rested = []
        first_block: Optional[str] = None
        adapter_logged = False

        for leg in decision.legs:
            leg_slug = leg.market_slug or slug
            slug_15 = getattr(ctx.market_15m, 'slug', None)
            market = (ctx.market_15m
                      if (leg.market_slug and slug_15
                          and leg.market_slug == slug_15)
                      else ctx.market)
            token_id = (market.token_id(leg.outcome_side)
                        if market is not None else None)
            if token_id is None:
                first_block = first_block or SKIP_UNKNOWN_TOKEN
                self.health['maker_leg_unknown_token'] += 1
                continue
            book = ctx.books.get(token_id) or ctx.books_15m.get(token_id)
            if book is None:
                first_block = first_block or SKIP_NO_LIQUIDITY
                self.health['maker_leg_no_book'] += 1
                continue

            # 2. ONE resting buy per (strategy, token) at a time. Not an
            # optimisation - a correctness rule and a fidelity rule at once.
            #
            # This loop polls every ~5 seconds and `box_builder` quotes for 150
            # seconds of every window, so without this it would rest ~30 orders
            # per side per window at whatever the bid had drifted to. It would
            # also be CHASING, which is the specific behaviour box_builder's own
            # module docstring refuses: his logs recorded 249 post-only rejects
            # from exactly that. The first quote stands until it fills, expires
            # on its TTL, or a halt cancels it. We do NOT amend or replace: an
            # amend goes to the back of the queue anyway, so re-quoting every
            # poll would reset our queue position 30 times a window and no
            # order would ever reach the front.
            if any(o.token_id == str(token_id)
                   for o in self._resting_buys_for(name)):
                first_block = first_block or SKIP_MAKER_ALREADY_RESTING
                self.health['maker_leg_already_resting'] += 1
                continue

            # 3. The maker budget. See DEFAULT_MAX_RESTING_MAKER_ORDERS: the
            # adapter counts resting buys against the same slot cap as open
            # positions, so an unbudgeted maker path starves every taker
            # strategy on the first cycle it quotes.
            if len(self.adapter.resting_buy_orders()) >= \
                    self.max_resting_maker_orders:
                first_block = first_block or SKIP_MAKER_BUDGET
                self.health['maker_budget_blocks'] += 1
                continue

            # 4. The risk gate, with the leg's own slug, exactly as the entry
            # path calls it and with its reason carried verbatim. A resting bid
            # is money that can be spent without asking us again, so it is gated
            # at rest time rather than at fill time - there is no fill time we
            # get to veto.
            verdict = self.gate.check_adapter_order(
                self.adapter, leg_slug, leg.outcome_side,
                premium=leg.premium, requested_shares=leg.shares, mode=MODE)
            if not verdict.approved:
                first_block = first_block or ('risk_gate:' + verdict.reason)
                self.health['maker_risk_gate_blocks'] += 1
                continue

            # 4b. The model-free entry constraints (D-343), gated at REST time
            # for the same reason comment 4 above gates the risk gate there:
            # a resting bid is money that can be spent without asking us
            # again, so this is the only veto point that exists.
            risk_decision = self._check_risk_constraints(
                leg_slug, ctx.window_ts, verdict.notional_usdc)
            if not risk_decision.allowed:
                first_block = first_block or (
                    'risk_constraint:' + risk_decision.reason)
                self.health['maker_risk_constraint_blocks'] += 1
                continue

            # 5. The adapter. It owns the post-only check, the queue
            # measurement, the TTL and its own REST/SKIP log row; its refusal
            # reason is recovered from the decision_counts delta so our taxonomy
            # is its taxonomy rather than our guess at it.
            before = dict(self.adapter.decision_counts)
            order = self.adapter.simulate_maker_buy(
                strategy=name, market_slug=leg_slug, token_id=token_id,
                outcome_side=leg.outcome_side, limit_price=leg.limit_price,
                shares=verdict.shares, window_ts=ctx.window_ts,
                features={k: v for k, v in feats.items() if v is not None},
                book=book, intent=decision.reason or 'maker_quote')
            if order is None:
                adapter_logged = True
                moved = [k for k, v in self.adapter.decision_counts.items()
                         if v > before.get(k, 0)]
                first_block = first_block or (
                    MAKER_ADAPTER_PREFIX + (moved[0] if moved else 'unreported'))
                self.health['maker_adapter_refusals'] += 1
                continue

            rested.append((order, leg))

        if not rested:
            reason = first_block or (MAKER_ADAPTER_PREFIX + 'unreported')
            return _log(name, slug, reason, reason, feats, confidence,
                        window_ts=ctx.window_ts, log_csv=not adapter_logged)

        if len(rested) < len(decision.legs):
            # A one-sided box or a partial grid. Recorded because it is the
            # shape both maker strategies are most likely to be wrong about:
            # box_builder's edge needs BOTH legs and a lone leg is a naked
            # directional binary.
            self.health['maker_partial_quotes'] += 1
        self.health['maker_legs_rested'] += len(rested)

        for order, leg in rested:
            logger.info('PM SHADOW REST %s %s %s %.0f sh @ %.4f '
                        '(queue_ahead %.1f, ttl %s)',
                        name, order.market_slug, order.outcome_side,
                        order.shares, order.limit_price,
                        order.queue_ahead_shares, order.expires_ts)
            try:
                self.store.audit('maker_order_rested', {
                    'order_id': order.order_id,
                    'strategy': name,
                    'market_slug': order.market_slug,
                    'outcome_side': order.outcome_side,
                    'limit_price': order.limit_price,
                    'shares': order.shares,
                    'queue_ahead_shares': order.queue_ahead_shares,
                    'expires_ts': order.expires_ts,
                    'mode': MODE,
                })
            except sqlite3.Error:
                pass

        # ONE disposition per evaluation, and it is a SKIP, not an entry. The
        # `log_csv=False` is the same rule the entry path uses: the adapter has
        # already written a REST row per leg and a second row here would put two
        # rows in the CSV for one decision.
        return _log(name, slug, SKIP_MAKER_RESTED, SKIP_MAKER_RESTED,
                    dict(feats, legs_rested=len(rested),
                         legs_quoted=len(decision.legs),
                         maker_fill_decided_on_a_later_cycle=True),
                    confidence, window_ts=ctx.window_ts, log_csv=False)

    def observe_maker_orders(self, contexts=None,
                             now: Optional[float] = None) -> dict:
        """Hand this cycle's books to every resting order. Never raises.

        THIS IS THE HALF THAT MAKES A MAKER FILL POSSIBLE AT ALL, and it is why
        resting orders had to live somewhere that survives a poll. They do:
        `self.adapter` is built once in `__init__` and reused for every cycle,
        and `adapter.resting_orders` is a dict on it. A resting order placed in
        cycle N is still there in cycle N+1, and `observe_resting_orders` is
        what tells it about the book that has arrived since.

        Runs BEFORE `manage_exits` and therefore before entries, for the same
        reason exits do: an order that fills or expires here changes
        `committed_slots` and a slot freed this cycle should be usable this
        cycle.

        A token with no book this cycle is NOT observed and NOT a no-fill
        (convention 11) - the adapter advances `observations` only when it is
        handed a book, and expiry is still checked for orders whose feed went
        dark. Everything lands in `maker_counts`, which is OUTSIDE the
        `evaluations == cycles * strategies * assets` identity: a resting order
        is not a window and looking at one is not an evaluation.
        """
        now_ts = int(time.time() if now is None else now)
        if contexts is None:
            contexts = {}
        elif isinstance(contexts, MarketContext):
            contexts = {self.assets[0]: contexts}

        result = {'resting_before': len(self.adapter.open_resting_orders()),
                  'observed': 0, 'filled': 0, 'terminated': 0,
                  'cancelled_by_halt': 0}

        # THE KILL SWITCH, and it runs before anything else here. A resting BUY
        # is a pending entry, so a halt has to take it OFF the book rather than
        # leave it armed - the adapter cancels a crossed one at observation
        # time, but an uncrossed one would otherwise sit there and fill the
        # moment the halt lifted, on a book that has since moved. Resting SELLS
        # are deliberately left: an ask over an open position reduces risk, and
        # this is the same asymmetry `simulate_taker_sell` already has.
        if is_halted():
            for order in self._all_resting_buys():
                self.adapter.cancel_resting_order(order.order_id,
                                                  'maker_cancelled_by_halt')
                self.maker_counts['cancel:maker_cancelled_by_halt'] += 1
                result['cancelled_by_halt'] += 1
            result['resting_after'] = len(self.adapter.open_resting_orders())
            return result

        if not self.adapter.open_resting_orders():
            result['resting_after'] = 0
            return result

        books: Dict[str, object] = {}
        for ctx in contexts.values():
            books.update(ctx.books or {})
            books.update(ctx.books_15m or {})

        # Counted BEFORE the call, over the orders that were still OPEN. The
        # adapter keeps terminated orders in `resting_orders` forever (that is
        # how a session can report how many it rested against how few filled),
        # so counting afterwards would re-count every order that ever existed
        # on every cycle and `observed` would grow quadratically.
        observed = sum(1 for o in self.adapter.open_resting_orders()
                       if o.token_id in books)

        try:
            terminated = self.adapter.observe_resting_orders(books, now_ts)
        except Exception as exc:                              # noqa: BLE001
            # A resting-order observation must never take the cycle with it,
            # and its failure must be a NAMED counter rather than an empty list
            # that reads like a quiet book.
            logger.error('PM SHADOW observe_resting_orders raised: %s: %s',
                         type(exc).__name__, exc)
            self.health['maker_observe_exceptions'] += 1
            self.maker_counts['observe_exception'] += 1
            result['resting_after'] = len(self.adapter.open_resting_orders())
            return result

        self.maker_counts['observed'] += observed
        result['observed'] = observed

        for order in terminated:
            reason = order.terminal_reason or 'unreported'
            if order.filled_shares > 0:
                # A maker FILL. On a BUY this opened a position, and the store
                # has to learn about it here because no entry path ran.
                self.maker_counts['fill:' + reason] += 1
                result['filled'] += 1
                if order.side == 'BUY':
                    self._record_maker_entry(order)
            elif order.status == ORDER_EXPIRED:
                self.maker_counts['expire:' + reason] += 1
            else:
                self.maker_counts['cancel:' + reason] += 1
            result['terminated'] += 1

        still = len(self.adapter.open_resting_orders())
        self.maker_counts['still_resting'] += still
        result['resting_after'] = still
        return result

    def _all_resting_buys(self) -> list:
        return list(self.adapter.resting_buy_orders())

    def _record_maker_entry(self, order) -> None:
        """Write a filled resting BUY into the store as the entry it is.

        The taker path records its entry inside `_attempt_entry`. A maker fill
        has no such moment - the decision that caused it was several cycles
        ago - so without this the position would exist in the adapter and in
        the CSV and NOWHERE in `db/trading.db`, which is the table every
        downstream reader (Forge, the critic, the dashboard) actually reads.
        """
        if getattr(order, 'token_id', None):
            self._cycle_selected_tokens.add(order.token_id)
        position = self.adapter.positions.get(order.position_id or '')
        if position is None:
            self.health['maker_fill_without_position'] += 1
            return
        try:
            signal_id = self.store.record_signal(
                strategy_id=order.strategy, market_slug=order.market_slug,
                pattern=order.strategy, direction='long', confidence=0.0,
                features=dict(order.features,
                              entry_liquidity='maker',
                              order_id=order.order_id,
                              queue_ahead_shares=order.queue_ahead_shares,
                              max_through_shares=order.max_through_shares,
                              observations=order.observations,
                              maker_fill_model=MAKER_FILL_MODEL),
                acted=True, skip_reason=None,
                market_duration=(
                    market_duration_for_slug(order.market_slug)
                    or self._market_duration_for(order.strategy,
                                                 order.market_slug)))
            self.store.record_entry(
                position, signal_id=signal_id,
                limit_price=order.limit_price, strategy_id=order.strategy,
                # Resolved by NAME here: a maker fill arrives cycles after the
                # decision that placed it, so there is no strategy object in
                # scope the way there is on the taker path.
                stop_px=self._entry_stop_px(
                    self._strategy_named(order.strategy), position))
            self.store.audit('maker_order_filled', {
                'order_id': order.order_id,
                'position_id': position.position_id,
                'strategy': order.strategy,
                'market_slug': order.market_slug,
                'outcome_side': order.outcome_side,
                'filled_shares': order.filled_shares,
                'limit_price': order.limit_price,
                'observations': order.observations,
                'queue_ahead_shares': order.queue_ahead_shares,
                'max_through_shares': order.max_through_shares,
                'mode': MODE,
            })
        except sqlite3.Error as exc:
            logger.warning('could not record maker fill %s: %s',
                           order.order_id, exc)
            self.health['maker_fill_record_errors'] += 1

    # -- exits ----------------------------------------------------------------

    def manage_exits(self, contexts=None, now: Optional[float] = None) -> dict:
        """Poll every open position whose strategy manages its own exits.

        Only strategies carrying `manages_exits = True` are consulted, and only
        about positions they opened. Everything else in this package holds to
        resolution and is left alone.

        `contexts` is a `{asset: MarketContext}` mapping. A bare MarketContext
        is also accepted and is read as the first asset's, so older single-asset
        callers keep working. `None` or `{}` is the OUTAGE path and is a
        supported call, not a degraded one: books are fetched per position
        instead of being read off a context, and fair value is unavailable so
        the model-driven exits cannot fire. The price-driven ones (window close,
        price stop, profit target, time stop) still do, which is the point -
        those are the ones that bound the loss.

        ## Positions are routed back to the instance that opened them

        Every asset runs its own instances of the SAME strategies, so
        `strategy_name` alone no longer identifies one object: there are three
        `PM_fair_value_arb` instances and they hold different price tapes. A
        position is routed by the asset in its own `market_slug`, so a BTC
        position is judged by BTC's instance against BTC's fair value. Keying
        only on the name would hand a BTC position to whichever instance the
        dict comprehension happened to write last, and the resulting model stop
        would be computed off a different coin's displacement - a wrong exit
        that looks like a normal one in every log.

        A position whose slug belongs to no registered asset is counted as
        `unroutable_position` and left alone rather than guessed at.

        Never raises. A strategy that throws while deciding an exit must not
        take the loop, or the other positions, with it.

        Returns a small summary. Every disposition also lands in `exit_counts`,
        which is OUTSIDE the `evaluations == cycles * n_strategies * n_assets`
        identity.
        """
        now = time.time() if now is None else now
        if contexts is None:
            contexts = {}
        elif isinstance(contexts, MarketContext):
            contexts = {self.assets[0]: contexts}

        result = {'checked': 0, 'exits': 0, 'refused': 0}

        # (asset, strategy_name) -> instance. Two keys, because the name alone
        # is ambiguous across assets now.
        managers: Dict[Tuple[str, str], object] = {}
        for asset in self.assets:
            for s in self.runtimes[asset].strategies:
                if getattr(s, 'manages_exits', False):
                    managers[(asset, getattr(s, 'strategy_name', None))] = s
        if not managers:
            return result

        # One fair-value estimate per (asset, strategy) per cycle, not one per
        # position: the estimate is a property of the window, and recomputing
        # it per position would let two positions on the same window be judged
        # against two different fair values.
        estimates: Dict[Tuple[str, str], object] = {}
        for (asset, name), strategy in managers.items():
            ctx_a = contexts.get(asset)
            if ctx_a is None:
                continue
            # CAPABILITY DISPATCH, not protocol assumption. Managing your own
            # exits and publishing a fair value are two different capabilities;
            # only a strategy that HAS `estimate()` is consulted for one. A
            # manager without it (PM_dip_arb exits against its own rolling tape
            # mean) is recorded as a wiring fact in a SET - once, at setup, and
            # idempotently here - and never as an exception. See the
            # `exit_no_fair_value_protocol` block in `__init__` for why the two
            # populations must not share a number (convention 20).
            if not hasattr(strategy, 'estimate'):
                self.exit_no_fair_value_protocol.add((asset, name))
                self.health['exit_no_fair_value_protocol'] = len(
                    self.exit_no_fair_value_protocol)
                continue
            # The try/except stays for the strategies that DO have it. A real
            # failure inside `estimate()` must still be caught and counted, and
            # `exit_fair_value_exceptions` is now a usable signal because only
            # those strategies can move it.
            try:
                estimates[(asset, name)] = strategy.estimate(ctx_a)
            except Exception as exc:
                self.health['exit_fair_value_exceptions'] += 1
                logger.warning('PM SHADOW fair value raised for %s on %s: %s: %s',
                               name, asset, type(exc).__name__, exc)

        for pos in list(self.adapter.open_positions()):
            asset = asset_for_slug(pos.market_slug)
            if asset is None or asset not in self.runtimes:
                # Not one of ours, or an asset we no longer poll. Either way we
                # have no instance that can speak for it, and inventing one
                # would manage a position with the wrong model.
                self.exit_counts['unroutable_position'] += 1
                continue
            strategy = managers.get((asset, pos.strategy))
            if strategy is None:
                continue
            ctx = contexts.get(asset)
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
            est = estimates.get((asset, pos.strategy))
            # Fair value is a statement about ONE window ON ONE ASSET. Applying
            # this window's estimate to a position from the previous window
            # would be a model stop computed off the wrong displacement; the
            # asset half of the key stops the same error across underlyings.
            if (ctx is not None and est is not None
                    and getattr(est, 'usable', False)
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

    #: Stamps attempted per cycle. Each one costs a Gamma read, so the
    #: backlog is drained at a bounded rate rather than in one sweep.
    CALIBRATION_STAMP_BATCH = 8

    def sample_calibration_tape(self, contexts, now: float) -> int:
        """One calibration row per token in the universe. Returns rows written.

        029 condition (b): the curve needs EVERY market, not the ones a
        strategy picked. The universe here is 3 assets x {5m, 15m} x 2
        outcomes = 12 tokens per cycle, which is the narrow scope Raven R1
        shipped; widening it to adjacent windows or the 16-window lookback
        scales linearly and needs its own number first.

        Costs NO extra network. build_context has already fetched both
        books for both markets through fetch_orderbook, so every price
        here comes off the CLOB book. That is not a convenience, it is the
        D-339 trap: gamma bestBid/bestAsk read 0.63/0.64 on a token whose
        live book was 0.06/0.08 three minutes from expiry, and a curve
        built on the summary fields would measure Gamma staleness and call
        it market miscalibration - worst exactly where the curve matters
        most. market.raw is never read here.

        A token with no book still gets a row, with NULL prices and NULL
        depth. Dropping it would make an unreadable book indistinguishable
        from a market that was never in the universe (convention 11).
        """
        rows = []
        for ctx in contexts.values():
            for market, books in ((ctx.market, ctx.books),
                                  (ctx.market_15m, ctx.books_15m)):
                if market is None:
                    continue
                slug = getattr(market, 'slug', None)
                duration = market_duration_for_slug(slug)
                window_ts = window_ts_from_slug(slug)
                if duration is None or window_ts is None:
                    self.health['calibration_unkeyed_market'] += 1
                    continue
                span = self.store.CALIBRATION_SPANS.get(duration)
                remaining = (window_ts + span - now) if span else None
                for outcome in (market.outcomes or ()):
                    book = (books or {}).get(outcome.token_id)
                    depth = (len(book.bids) + len(book.asks)) if book else None
                    rows.append((
                        outcome.token_id, slug, duration, outcome.name,
                        market.condition_id or None, float(now),
                        int(window_ts), remaining,
                        book.midpoint if book else None,
                        book.best_bid if book else None,
                        book.best_ask if book else None,
                        depth,
                        1 if outcome.token_id in
                        self._cycle_selected_tokens else 0))
        written = self.store.record_calibration_rows(rows)
        self.health['calibration_rows'] += written
        return written

    def stamp_calibration_resolutions(self, now: float) -> int:
        """Stamp closed windows with their oracle outcome. Returns stamps written.

        Runs on windows that have already CLOSED, so it lags the tape by one
        window. resolved_ts records when we OBSERVED the resolution, not
        when the window settled; conflating those two is how a calibration
        curve quietly acquires a lookahead.

        Reads each market by its own slug rather than through
        resolved_windows_checked, which the spec named: that helper is
        get_updown_5m_checked underneath and cannot see a 15m market at
        all, and the 15m arm is half of what this tape exists to measure.
        Its failure taxonomy is kept - read_failed / not_listed /
        unresolved / not_binary each land in health under their own name,
        so an oracle running behind is never confused with a read that
        failed (convention 20).
        """
        stamped = 0
        for row in self.store.pending_calibration_tokens(
                now, limit=self.CALIBRATION_STAMP_BATCH):
            token_id, slug, duration, window_ts, side = row
            market, status = get_market_by_slug_checked(self.client, slug)
            if market is None:
                self.health['calibration_resolve:' + str(status)] += 1
                continue
            if not market.is_binary:
                self.health['calibration_resolve:not_binary'] += 1
                continue
            winner = market.resolved_outcome
            if winner is None:
                self.health['calibration_resolve:unresolved'] += 1
                continue
            won = 1 if ((side or '').strip().lower()
                        == winner.strip().lower()) else 0
            wrote = self.store.stamp_calibration_resolution(
                token_id=token_id, market_slug=slug,
                market_duration=duration, window_ts=window_ts,
                resolved_outcome=winner.strip().upper(), won=won,
                resolved_ts=float(now))
            if wrote:
                stamped += 1
                self.health['calibration_resolutions'] += 1
            else:
                self.health['calibration_resolution_duplicate'] += 1
        return stamped
    def run_cycle(self, now: Optional[float] = None) -> dict:
        """One poll. Never raises: an unexpected failure is a counted category.

        Returns a small summary dict, mostly for tests and the stats line.
        """
        now = time.time() if now is None else now
        cycle_t0 = time.perf_counter()
        self.cycles += 1
        window_ts = current_window_ts(now)
        detail: dict = {'cycle': self.cycles}
        # Per-cycle, so selected describes THIS poll. Carrying it across
        # cycles would mark a market selected forever after one entry.
        self._cycle_selected_tokens = set()

        # -- Phase 1: build every asset's context before deciding anything.
        # Contexts first, entries later, because exits run BETWEEN the two and
        # an exit on ETH can free a slot a BTC entry uses in the same cycle.
        contexts: Dict[str, MarketContext] = {}
        per_asset: Dict[str, dict] = {}
        any_ok = False
        any_api_error = False

        contexts_t0 = time.perf_counter()
        for asset in self.assets:
            try:
                ctx, status, built = self.build_context(window_ts, now, asset)
            except Exception as exc:
                logger.error('PM SHADOW cycle %d raised building %s context: %s',
                             self.cycles, asset,
                             traceback.format_exc().strip().splitlines()[-1])
                self.health['context_exceptions:' + asset] += 1
                ctx, status = None, SKIP_CYCLE_EXCEPTION
                built = {'asset': asset,
                         'exception': '{}: {}'.format(type(exc).__name__, exc)}
            info = dict(built)
            info['status'] = status
            per_asset[asset] = info
            if ctx is not None:
                contexts[asset] = ctx
            if status == SKIP_API_ERROR:
                any_api_error = True
            elif status == 'ok':
                any_ok = True
        # The whole network-bound phase, across every asset. This is the number
        # that bounds the poll interval, and it is recorded per CYCLE rather
        # than per asset so it can be read straight against `poll_sec`.
        self._record_timing('cycle_contexts', time.perf_counter() - contexts_t0)

        # Backoff is a property of the CYCLE, not of an asset. Incrementing per
        # asset would triple the backoff rate for one outage, and backing off
        # because ETH is unlisted while BTC is answering fine would stop polling
        # a market that is working. Any success resets it.
        if any_ok:
            self._consecutive_api_errors = 0
        elif any_api_error:
            self._consecutive_api_errors += 1

        # -- Phase 2: resting maker orders, against the books just fetched.
        # BEFORE exits and therefore before entries: a resting order that fills
        # or expires here changes `adapter.committed_slots()`, and a slot freed
        # this cycle should be usable this cycle. This is also the ONLY place a
        # maker order can ever fill - `_attempt_maker_quotes` only puts it on
        # the book - so if this call is ever removed both maker strategies go
        # back to booking zero fills while still reporting rested orders, which
        # is the exact ambiguity convention 20 exists to remove.
        detail['maker'] = self._timed('cycle_maker_observe',
                                      self.observe_maker_orders, contexts, now)

        # -- Phase 3: exits, across every asset at once.
        # BEFORE entries: closing a position frees a concurrency slot that an
        # entry this same cycle can use, and a stop that waits for the entry
        # loop is a stop one poll late. Positions on assets whose context failed
        # this cycle are still managed - `manage_exits` fetches their books
        # itself - because a stop that stops working during an outage is not a
        # stop loss.
        detail['exits'] = self._timed('cycle_exits', self.manage_exits,
                                      contexts, now)

        # -- Phase 4: evaluate each asset's own strategy instances.
        evaluate_t0 = time.perf_counter()
        for asset in self.assets:
            runtime = self.runtimes[asset]
            info = per_asset[asset]
            status = info['status']
            ctx = contexts.get(asset)
            asset_detail = dict(detail)
            asset_detail.update(info)

            if ctx is None:
                # Attribute the failure to EVERY strategy on THIS asset, so
                # `evaluations == cycles * n_strategies * n_assets` holds
                # unconditionally and an asset that never reached a strategy is
                # visibly that, rather than an asset with no signals.
                reason = status
                if status == SKIP_API_ERROR:
                    reason = '{}:attempt_{}'.format(SKIP_API_ERROR,
                                                    self._consecutive_api_errors)
                    info['api_error_attempt'] = self._consecutive_api_errors
                    asset_detail['api_error_attempt'] = \
                        self._consecutive_api_errors
                for strategy in runtime.strategies:
                    name = getattr(strategy, 'strategy_name', str(strategy))
                    self._log_and_count(name, info.get('market_slug'), status,
                                        reason, dict(asset_detail),
                                        window_ts=window_ts)
                continue

            for strategy in runtime.strategies:
                name = getattr(strategy, 'strategy_name', str(strategy))
                try:
                    self.evaluate_strategy(strategy, ctx, asset_detail)
                except Exception as exc:
                    # A strategy that raises must not take the other strategies,
                    # the other ASSETS, or the loop with it. It is still one
                    # evaluation and it is still counted.
                    logger.error('PM SHADOW strategy %s on %s raised: %s: %s',
                                 name, asset, type(exc).__name__, exc)
                    self.health['strategy_exceptions'] += 1
                    self._log_and_count(
                        name, getattr(ctx.market, 'slug', None),
                        SKIP_CYCLE_EXCEPTION,
                        '{}:{}'.format(type(exc).__name__, exc)[:200],
                        dict(asset_detail), window_ts=window_ts)

        # CPU plus the sqlite writes, with no network in it: every strategy
        # reads the context it was handed and none of them can fetch. Recorded
        # separately from `cycle_contexts` so "the venue is slow" and "we are
        # slow" can never be read off one number.
        self._record_timing('cycle_evaluate', time.perf_counter() - evaluate_t0)

        # -- Phase 5: the calibration tape (029 condition (b)).
        # AFTER the evaluation phase, deliberately: selected is read off
        # what strategies actually entered on this cycle, which is not
        # known until they have run. The books were fetched in phase 1 and
        # are reused, so this phase adds sqlite writes and no network.
        # Never raises: a tape that dies must not take the loop with it,
        # and the failure is a counted category rather than a silent gap.
        try:
            detail['calibration_rows'] = self._timed(
                'cycle_calibration', self.sample_calibration_tape,
                contexts, now)
            detail['calibration_stamps'] = self._timed(
                'cycle_calibration_stamp', self.stamp_calibration_resolutions,
                now)
        except Exception:
            logger.error('PM SHADOW cycle %d calibration tape raised: %s',
                         self.cycles,
                         traceback.format_exc().strip().splitlines()[-1])
            self.health['calibration_exceptions'] += 1

        self._record_timing('cycle_total', time.perf_counter() - cycle_t0)

        detail['assets'] = per_asset
        # The first asset's fields are ALSO mirrored at the top level. This is a
        # compatibility surface, not a summary: with one asset configured it
        # reproduces the pre-multi-asset shape exactly, and with three it
        # describes the FIRST one only. Read `detail['assets']` for the truth.
        first = per_asset[self.assets[0]]
        for key, value in first.items():
            detail.setdefault(key, value)
        detail['status'] = first['status']
        return detail

    # -- the weather cycle ---------------------------------------------------

    def discover_weather_markets(self, now: Optional[float] = None) -> dict:
        """Ask Gamma what temperature markets exist, and rank them for polling.

        Returns the discovery result with a `ranking` block attached. Never
        raises: a discovery that blows up is a counted health event and an empty
        poll list, not a dead loop.

        THREE OUTCOMES THAT MUST NEVER BE POOLED (convention 11, and this is the
        exact split the brief asked for):

            ok=False, 'read_failed'       Gamma was unreachable. We know
                                          NOTHING about the board.
            ok=True, 'no_weather_market'  Gamma answered and listed none. A
                                          fact about the day.
            ok=True, ranking selects 0    Markets exist and not one of them is
                                          worth a book read - because it has no
                                          readable station, or no threshold, or
                                          it is the annual-ranking family.

        The third is `weather_no_pollable_market` and it is counted separately
        from the second, because "Polymarket listed nothing" and "Polymarket
        listed 1,090 and our parser can use none of them" point at completely
        different things: one is a quiet day and the other is our bug.

        DISCOVERY IS A TAG SWEEP, NOT A VOLUME QUERY, and it is worth saying why
        no `order` parameter appears anywhere in this path. Gamma's `/markets`
        route sorts `order=volume` as TEXT and returns the SMALLEST markets
        while still answering HTTP 200 (it returns 422 only for a genuinely
        unknown field, so a 200 does not mean the sort was understood). This
        goes through `/events?tag_slug=weather` instead, which takes no ordering
        at all, and `rank_weather_markets` sorts the result LOCALLY on a list we
        already hold. A local sort cannot be silently backwards.
        """
        from strategies.polymarket.weather_arb import (find_weather_markets,
                                                       rank_weather_markets)

        now = time.time() if now is None else float(now)
        t0 = time.perf_counter()
        try:
            result = find_weather_markets(self.client,
                                          limit=self.weather_discovery_limit,
                                          now=now)
        except Exception as exc:                          # noqa: BLE001
            # Discovery reaches the network. It must never take the loop with
            # it, and its failure must be a NAMED counter rather than an empty
            # list that reads like a quiet day.
            self.weather_health['discovery_exceptions'] += 1
            logger.error('PM SHADOW weather discovery raised: %s: %s',
                         type(exc).__name__, exc)
            result = {'ok': False, 'markets': [], 'reason': 'read_failed',
                      'exception': '{}: {}'.format(type(exc).__name__, exc)}
        self._record_timing('weather_discovery', time.perf_counter() - t0)

        if not result.get('ok'):
            self.weather_health['discovery_read_failed'] += 1
            self.weather_markets = []
            result['ranking'] = {'selected': [], 'declined': {},
                                 'considered': 0, 'volume_ordered': []}
        else:
            self.weather_health['discovery_ok'] += 1
            ranking = rank_weather_markets(result.get('markets') or (),
                                           limit=self.weather_market_limit)
            self.weather_markets = list(ranking['selected'])
            for reason, count in ranking['declined'].items():
                # Prefixed so a poll-budget decline can never be read as a
                # decision skip: no market counted here ever reached `evaluate`.
                self.weather_health['declined:' + reason] += count
            result['ranking'] = ranking

        self._last_weather_discovery = now
        self.weather_discovery = result
        logger.info('PM SHADOW weather discovery ok=%s reason=%s raw=%d '
                    'found=%d selected=%d', result.get('ok'),
                    result.get('reason'), result.get('raw_count', 0),
                    len(result.get('markets') or ()), len(self.weather_markets))
        return result

    def build_weather_context(self, market, now: float
                              ) -> Tuple[Optional[MarketContext], str, dict]:
        """One temperature market's MarketContext. `(ctx, status, detail)`.

        Far smaller than `build_context` because a temperature market needs none
        of it: no spot, no strike, no candles, no 15m companion, no ATR. The
        strategy fetches its own weather inputs (see `weather_arb`'s docstring on
        why that compromise exists) and everything else it reads is on the market
        object and its books.

        `window_ts` is the POLL SECOND. There is no 5-minute window here to floor
        to, and `WeatherArb.clock` is written for exactly that: it reads
        `window_ts + seconds_into_window` as the absolute observation time, which
        is what the METAR freshness gate and the local-day arithmetic need.
        """
        detail: dict = {'market_slug': getattr(market, 'slug', None),
                        'asset': 'weather',
                        'weather_market': True}
        tasks: List[Tuple[str, Callable]] = [
            ('wxbook:' + o.token_id,
             functools.partial(self._fetch_book_checked, o.token_id))
            for o in (market.outcomes or ())
        ]
        if not tasks:
            return None, WX_NO_BOOK, detail
        results = self._timed('weather_books', self._run_parallel, tasks)
        books, book_status = self._books_from_results(market.outcomes, results,
                                                      'wxbook:')
        detail['book_status'] = book_status
        if not books:
            # Same precedence as the crypto path: our own code throwing outranks
            # a venue outage, which outranks a book nobody is quoting.
            values = set(book_status.values())
            if STATUS_FETCH_EXCEPTION in values:
                return None, WX_CYCLE_EXCEPTION, detail
            if SKIP_API_ERROR in values:
                return None, SKIP_API_ERROR, detail
            return None, WX_NO_BOOK, detail

        # STAMPED, not defaulted. `MarketContext.market_type` defaults to
        # crypto_updown, so a weather context that does not set it is a weather
        # market wearing a crypto label. Nothing raised on that before only
        # because `WeatherArb` does not call `assert_supports`; the one strategy
        # in this space that DOES call it (`smart_money_copy`) declares every
        # type and so accepted the wrong label silently. Convention 22: the
        # routing declaration is a claim, and it is only enforceable if the
        # context carries the type the router used to select the strategy.
        ctx = MarketContext(window_ts=int(now), market=market, books=books,
                            seconds_into_window=float(now) - int(now),
                            market_type=MARKET_TYPE_WEATHER)
        # D-362 R4: tape BEFORE any strategy is consulted, so the row exists
        # whether or not this market reaches one.
        detail['tape_rows'] = self._write_market_tape(ctx)
        return ctx, 'ok', detail

    def run_weather_cycle(self, now: Optional[float] = None) -> dict:
        """One weather poll: discover if due, then decide every selected market.

        Never raises. Returns a small summary for the stats line and the tests.

        EVERY EVALUATION LANDS IN `weather_counts` AND NOWHERE ELSE. The crypto
        identity is untouched by this method, which is asserted rather than
        trusted: `tests/test_weather_shadow_wiring.py` runs a weather cycle and
        checks `loop.evaluations` and `loop.counts` are exactly what they were.
        """
        now = time.time() if now is None else float(now)
        self.weather_cycles += 1
        t0 = time.perf_counter()
        summary: dict = {'cycle': self.weather_cycles, 'markets': 0,
                         'entries': 0}

        if not self.enable_weather or not self.weather_strategies:
            # A DISPOSITION, not a silent return. A session running with weather
            # off and one running with weather on and finding nothing produce
            # the same empty log otherwise, and only one of them is a fact about
            # the board (convention 20).
            self._count(WX_DISABLED, self.weather_counts)
            self.weather_evaluations += 1
            summary['status'] = WX_DISABLED
            self._record_timing('weather_cycle', time.perf_counter() - t0)
            return summary

        if (now - self._last_weather_discovery >= self.weather_cycle_sec
                or not self.weather_discovery):
            self.discover_weather_markets(now)

        discovery = self.weather_discovery or {}
        summary['discovery_ok'] = discovery.get('ok')
        summary['discovery_reason'] = discovery.get('reason')

        if not self.weather_markets:
            # Three distinct causes, three distinct counters. This is the
            # accounting that did not exist before: previously a cycle that
            # found nothing was simply a cycle in which `PM_weather_arb` skipped
            # `resolution_station_unknown` on a BTC window.
            if not discovery.get('ok'):
                status = WX_DISCOVERY_FAILED
            elif not (discovery.get('markets') or ()):
                status = WX_NO_MARKET_LISTED
            else:
                status = WX_NONE_POLLABLE
            for strategy in self.weather_strategies:
                name = getattr(strategy, 'strategy_name', str(strategy))
                self._log_and_count(name, None, status, status,
                                    {'weather_cycle': self.weather_cycles,
                                     'discovery_reason': discovery.get('reason'),
                                     'discovery_ok': discovery.get('ok'),
                                     'discovery_raw_count': discovery.get(
                                         'raw_count'),
                                     'discovery_found': len(
                                         discovery.get('markets') or ()),
                                     'asset': 'weather'},
                                    window_ts=int(now),
                                    counts=self.weather_counts)
                self.weather_evaluations += 1
            summary['status'] = status
            self._record_timing('weather_cycle', time.perf_counter() - t0)
            return summary

        summary['status'] = 'ok'
        for market in self.weather_markets:
            try:
                ctx, status, detail = self.build_weather_context(market, now)
            except Exception as exc:                      # noqa: BLE001
                logger.error('PM SHADOW weather context raised for %s: %s: %s',
                             getattr(market, 'slug', None),
                             type(exc).__name__, exc)
                self.weather_health['context_exceptions'] += 1
                ctx, status = None, WX_CYCLE_EXCEPTION
                detail = {'market_slug': getattr(market, 'slug', None),
                          'asset': 'weather',
                          'exception': '{}: {}'.format(type(exc).__name__, exc)}
            detail['weather_cycle'] = self.weather_cycles
            summary['markets'] += 1

            if ctx is None:
                # Attributed to every weather strategy, exactly as the crypto
                # path attributes a failed context to every strategy on that
                # asset. A market that never reached a strategy is visibly that.
                for strategy in self.weather_strategies:
                    name = getattr(strategy, 'strategy_name', str(strategy))
                    self._log_and_count(name, detail.get('market_slug'), status,
                                        status, dict(detail),
                                        window_ts=int(now),
                                        counts=self.weather_counts)
                    self.weather_evaluations += 1
                continue

            for strategy in self.weather_strategies:
                name = getattr(strategy, 'strategy_name', str(strategy))
                try:
                    disposition = self.evaluate_strategy(
                        strategy, ctx, detail, counts=self.weather_counts)
                except Exception as exc:                  # noqa: BLE001
                    logger.error('PM SHADOW weather strategy %s raised on %s: '
                                 '%s: %s', name,
                                 getattr(market, 'slug', None),
                                 type(exc).__name__, exc)
                    self.weather_health['strategy_exceptions'] += 1
                    disposition = self._log_and_count(
                        name, getattr(market, 'slug', None),
                        WX_CYCLE_EXCEPTION,
                        '{}:{}'.format(type(exc).__name__, exc)[:200],
                        dict(detail), window_ts=int(now),
                        counts=self.weather_counts)
                self.weather_evaluations += 1
                if disposition == 'entry':
                    summary['entries'] += 1

        self._last_weather_cycle = now
        self._record_timing('weather_cycle', time.perf_counter() - t0)
        self.check_weather_identity()
        return summary

    def check_weather_identity(self) -> bool:
        """`weather_evaluations == sum(weather_counts.values())`.

        The weather space's own identity. It cannot take the crypto form
        (`cycles * strategies * assets`) because the number of markets polled is
        whatever discovery returned, which is a property of the board and not of
        the configuration. What it CAN assert is the thing convention 20 is
        actually about: every evaluation landed in exactly one named bucket and
        none was silently dropped.

        Logged at ERROR and audited; never repaired by adjusting a counter.
        """
        total = sum(self.weather_counts.values())
        ok = total == self.weather_evaluations
        if not ok:
            self.weather_identity_violations += 1
            logger.error('PM SHADOW WEATHER IDENTITY VIOLATED: '
                         'weather_evaluations=%d counted=%d counts=%s',
                         self.weather_evaluations, total,
                         dict(self.weather_counts))
            try:
                self.store.audit('weather_accounting_violation', {
                    'weather_evaluations': self.weather_evaluations,
                    'counted': total, 'counts': dict(self.weather_counts)})
            except sqlite3.Error:
                pass
        return ok

    def weather_stats(self) -> dict:
        """The weather space's numbers, kept apart from the crypto ones."""
        discovery = self.weather_discovery or {}
        return {
            'enabled': self.enable_weather,
            'strategies': [getattr(s, 'strategy_name', str(s))
                           for s in self.weather_strategies],
            'cycles': self.weather_cycles,
            'cycle_sec': self.weather_cycle_sec,
            'market_limit': self.weather_market_limit,
            'evaluations': self.weather_evaluations,
            'counts': dict(self.weather_counts),
            'health': dict(self.weather_health),
            'identity_ok': (sum(self.weather_counts.values())
                            == self.weather_evaluations),
            'identity_violations': self.weather_identity_violations,
            'discovery_ok': discovery.get('ok'),
            'discovery_reason': discovery.get('reason'),
            'discovery_raw_count': discovery.get('raw_count'),
            'discovery_found': len(discovery.get('markets') or ()),
            'discovery_drops': discovery.get('drops'),
            'polled_markets': [getattr(m, 'slug', None)
                               for m in self.weather_markets],
        }

    # -- the general binary spaces: event, sports, political (D-313) --------
    #
    # One implementation, three records. Everything below takes a `MarketSpace`
    # and touches nothing outside it, which is why there are three universes
    # here and no third copy of the weather cycle. Convention 23: a fix at one
    # site is not a fix, and three hand-copied cycles are three places for the
    # accounting to drift apart.

    def discover_space_markets(self, space: 'MarketSpace',
                               now: Optional[float] = None) -> dict:
        """Ask Gamma what this space contains. Never raises.

        Same three-way split the weather discovery makes, for the same reason
        (convention 11): a read that FAILED and a board that is genuinely EMPTY
        are different facts and must not share a counter.

        The ordering is LOCAL. `_search_tagged_markets_checked` sorts on
        `market.volume` in Python on a list we already hold, because Gamma's
        `/events` route has no working sort and `/markets?order=volume` sorts as
        TEXT while still answering HTTP 200 - it returns the SMALLEST markets
        and looks successful. A local sort cannot be silently backwards.
        """
        now = time.time() if now is None else float(now)
        t0 = time.perf_counter()
        finder = self._space_finders.get(space.name)
        if finder is None:
            # A space with no discovery function is a construction bug, not a
            # quiet day. Counted, never silent (convention 20).
            space.health['discovery_no_finder'] += 1
            result = {'ok': False, 'markets': [], 'reason': 'read_failed',
                      'exception': 'no discovery function for space {!r}'
                                   .format(space.name)}
        else:
            try:
                result = finder(self.client, limit=space.discovery_limit,
                                min_volume_usdc=space.min_volume_usdc)
            except Exception as exc:                      # noqa: BLE001
                space.health['discovery_exceptions'] += 1
                logger.error('PM SHADOW %s discovery raised: %s: %s',
                             space.name, type(exc).__name__, exc)
                result = {'ok': False, 'markets': [], 'reason': 'read_failed',
                          'exception': '{}: {}'.format(type(exc).__name__, exc)}
        self._record_timing(space.name + '_discovery', time.perf_counter() - t0)

        if not result.get('ok'):
            space.health['discovery_read_failed'] += 1
            space.markets = []
        else:
            space.health['discovery_ok'] += 1
            # Already volume-ordered by the finder. The poll budget is applied
            # here rather than in the query so that `discovery_found` records
            # what the BOARD held and `polled_markets` records what we chose,
            # and the gap between them is visible instead of inferred.
            found = list(result.get('markets') or ())
            space.markets = found[:space.market_limit]
            over = len(found) - len(space.markets)
            if over > 0:
                # Prefixed so a poll-budget decline can never be read as a
                # decision skip: no market counted here ever reached `evaluate`.
                space.health['declined:over_poll_budget'] += over
            for reason, count in (result.get('read_failures') or {}).items():
                space.health['read_failure:' + str(reason)] += count

        space.last_discovery = now
        space.discovery = result
        logger.info('PM SHADOW %s discovery ok=%s reason=%s raw=%d found=%d '
                    'selected=%d', space.name, result.get('ok'),
                    result.get('reason'), result.get('raw_count', 0),
                    len(result.get('markets') or ()), len(space.markets))
        return result

    def build_space_context(self, space: 'MarketSpace', market, now: float
                            ) -> Tuple[Optional[MarketContext], str, dict]:
        """One general-binary market's MarketContext. `(ctx, status, detail)`.

        Shaped like `build_weather_context` and for the same reason: an NFL
        market has no spot, no strike, no 5-minute clock, no ATR and no 15m
        companion. What it has is a question, outcomes and books.

        `market_type` is STAMPED from the space that selected the market. That
        is what makes `assert_supports` meaningful: the router picked this
        strategy because it declared this type, and the context now carries the
        same type the router used, so a mismatch raises instead of being
        evaluated under a wrong label.

        `window_ts` is the POLL SECOND. These markets resolve in hours or days,
        so there is no window to floor to, and a strategy that needs a deadline
        reads `market.end_date` rather than inventing one from the clock.
        """
        detail: dict = {'market_slug': getattr(market, 'slug', None),
                        'asset': space.name,
                        'market_type': space.market_type,
                        'space': space.name}
        tasks: List[Tuple[str, Callable]] = [
            ('spacebook:' + o.token_id,
             functools.partial(self._fetch_book_checked, o.token_id))
            for o in (market.outcomes or ())
        ]
        if not tasks:
            return None, space.status(SPACE_NO_BOOK), detail
        results = self._timed(space.name + '_books', self._run_parallel, tasks)
        books, book_status = self._books_from_results(market.outcomes, results,
                                                      'spacebook:')
        detail['book_status'] = book_status
        if not books:
            # Same precedence as the crypto and weather paths: our own code
            # throwing outranks a venue outage, which outranks an unquoted book.
            values = set(book_status.values())
            if STATUS_FETCH_EXCEPTION in values:
                return None, space.status(SPACE_CYCLE_EXCEPTION), detail
            if SKIP_API_ERROR in values:
                return None, SKIP_API_ERROR, detail
            return None, space.status(SPACE_NO_BOOK), detail

        ctx = MarketContext(window_ts=int(now), market=market, books=books,
                            seconds_into_window=float(now) - int(now),
                            market_type=space.market_type)
        # D-362 R4: see `_build_weather_context`. This is the path that carries
        # the two-outcome event/political/sports markets the complement check
        # keys on, so it is the one that matters most.
        detail['tape_rows'] = self._write_market_tape(ctx)
        return ctx, 'ok', detail

    def run_space_cycle(self, space: 'MarketSpace',
                        now: Optional[float] = None) -> dict:
        """One space poll: discover if due, then decide every selected market.

        Never raises. Returns a small summary for the stats line and the tests.

        EVERY EVALUATION LANDS IN `space.counts` AND NOWHERE ELSE. The crypto
        identity and the weather identity are both untouched by this method.
        """
        now = time.time() if now is None else float(now)
        space.cycles += 1
        t0 = time.perf_counter()
        summary: dict = {'space': space.name, 'cycle': space.cycles,
                         'markets': 0, 'entries': 0}

        def _attribute(status: str, extra: dict) -> None:
            """Charge one disposition to every strategy in this space.

            A market that never reached a strategy is visibly that, on every
            strategy, rather than being one unattributed decrement.
            """
            for strategy in space.strategies:
                name = getattr(strategy, 'strategy_name', str(strategy))
                self._log_and_count(name, extra.get('market_slug'), status,
                                    status, dict(extra), window_ts=int(now),
                                    counts=space.counts)
                space.evaluations += 1

        if not space.enabled or not space.strategies:
            # Two different facts, two different counters, and neither is a
            # silent return (convention 20). A session running with sports off
            # and one running with sports on but no strategy declaring
            # `sports` produce the same empty log otherwise, and only one of
            # them is a fact about our configuration.
            status = space.status(SPACE_DISABLED if not space.enabled
                                  else SPACE_NO_STRATEGY)
            self._count(status, space.counts)
            space.evaluations += 1
            summary['status'] = status
            self._record_timing(space.name + '_cycle',
                                time.perf_counter() - t0)
            return summary

        if (now - space.last_discovery >= space.cycle_sec
                or not space.discovery):
            self.discover_space_markets(space, now)

        discovery = space.discovery or {}
        summary['discovery_ok'] = discovery.get('ok')
        summary['discovery_reason'] = discovery.get('reason')

        if not space.markets:
            # Three distinct causes, three distinct counters. "Gamma was
            # unreachable", "Gamma listed nothing" and "Gamma listed markets
            # and none cleared the volume floor" point at three different
            # things: a venue outage, a quiet board, and our own filter.
            if not discovery.get('ok'):
                reason = SPACE_DISCOVERY_FAILED
            elif not (discovery.get('markets') or ()):
                reason = SPACE_NO_MARKET_LISTED
            else:
                reason = SPACE_NONE_POLLABLE
            status = space.status(reason)
            _attribute(status, {
                'market_slug': None,
                'asset': space.name,
                'space': space.name,
                'market_type': space.market_type,
                'space_cycle': space.cycles,
                'discovery_ok': discovery.get('ok'),
                'discovery_reason': discovery.get('reason'),
                'discovery_raw_count': discovery.get('raw_count'),
                'discovery_found': len(discovery.get('markets') or ()),
            })
            summary['status'] = status
            self._record_timing(space.name + '_cycle',
                                time.perf_counter() - t0)
            space.last_cycle = now
            self.check_space_identity(space)
            return summary

        summary['status'] = 'ok'
        for market in space.markets:
            try:
                ctx, status, detail = self.build_space_context(space, market,
                                                               now)
            except Exception as exc:                      # noqa: BLE001
                logger.error('PM SHADOW %s context raised for %s: %s: %s',
                             space.name, getattr(market, 'slug', None),
                             type(exc).__name__, exc)
                space.health['context_exceptions'] += 1
                ctx = None
                status = space.status(SPACE_CYCLE_EXCEPTION)
                detail = {'market_slug': getattr(market, 'slug', None),
                          'asset': space.name, 'space': space.name,
                          'market_type': space.market_type,
                          'exception': '{}: {}'.format(type(exc).__name__, exc)}
            detail['space_cycle'] = space.cycles
            summary['markets'] += 1

            if ctx is None:
                _attribute(status, detail)
                continue

            for strategy in space.strategies:
                name = getattr(strategy, 'strategy_name', str(strategy))
                try:
                    disposition = self.evaluate_strategy(
                        strategy, ctx, detail, counts=space.counts)
                except Exception as exc:                  # noqa: BLE001
                    # `assert_supports` lands here by design. A routing bug is
                    # a stack trace in the bucket that already means "our code
                    # broke", never a skip row that reads like a decision.
                    logger.error('PM SHADOW %s strategy %s raised on %s: '
                                 '%s: %s', space.name, name,
                                 getattr(market, 'slug', None),
                                 type(exc).__name__, exc)
                    space.health['strategy_exceptions'] += 1
                    disposition = self._log_and_count(
                        name, getattr(market, 'slug', None),
                        space.status(SPACE_CYCLE_EXCEPTION),
                        '{}:{}'.format(type(exc).__name__, exc)[:200],
                        dict(detail), window_ts=int(now),
                        counts=space.counts)
                space.evaluations += 1
                if disposition == 'entry':
                    summary['entries'] += 1

        space.last_cycle = now
        self._record_timing(space.name + '_cycle', time.perf_counter() - t0)
        self.check_space_identity(space)
        return summary

    def check_space_identity(self, space: 'MarketSpace') -> bool:
        """`space.evaluations == sum(space.counts.values())`.

        One space's own identity, for the same reason the weather space has
        one: the crypto form (`cycles * strategies * assets`) cannot apply when
        the number of markets polled is a property of the board rather than of
        the configuration. What it CAN assert is convention 20's actual claim -
        every evaluation landed in exactly one named bucket.

        Logged at ERROR and audited; never repaired by adjusting a counter.
        """
        total = sum(space.counts.values())
        ok = total == space.evaluations
        if not ok:
            space.identity_violations += 1
            logger.error('PM SHADOW %s IDENTITY VIOLATED: evaluations=%d '
                         'counted=%d counts=%s', space.name.upper(),
                         space.evaluations, total, dict(space.counts))
            try:
                self.store.audit('space_accounting_violation', {
                    'space': space.name, 'evaluations': space.evaluations,
                    'counted': total, 'counts': dict(space.counts)})
            except sqlite3.Error:
                pass
        return ok

    def space_stats(self) -> dict:
        """Every space's numbers, each kept apart from the others."""
        out: dict = {}
        for space in self.spaces:
            discovery = space.discovery or {}
            out[space.name] = {
                'enabled': space.enabled,
                'market_type': space.market_type,
                'strategies': space.strategy_names,
                'cycles': space.cycles,
                'cycle_sec': space.cycle_sec,
                'market_limit': space.market_limit,
                'min_volume_usdc': space.min_volume_usdc,
                'evaluations': space.evaluations,
                'counts': dict(space.counts),
                'health': dict(space.health),
                'identity_ok': (sum(space.counts.values())
                                == space.evaluations),
                'identity_violations': space.identity_violations,
                'discovery_ok': discovery.get('ok'),
                'discovery_reason': discovery.get('reason'),
                'discovery_raw_count': discovery.get('raw_count'),
                'discovery_found': len(discovery.get('markets') or ()),
                'discovery_drops': discovery.get('drops'),
                'polled_markets': [getattr(m, 'slug', None)
                                   for m in space.markets],
            }
        return out

    # -- periodic work ------------------------------------------------------

    def resolve(self) -> List:
        """Settle anything the oracle has decided. Runs during a HALT too.

        A halt blocks entries. It does not un-decide a window that already
        resolved, and skipping this would leave an operator reading a halted
        session's PnL with the losses missing.
        """
        try:
            settled = self.adapter.resolve_positions()
        except Exception as exc:
            self.health['resolve_exceptions'] += 1
            logger.warning('PM SHADOW resolve raised: %s: %s',
                           type(exc).__name__, exc)
            return []
        for position in settled:
            self.store.record_resolution(position)
            self.store.audit('position_closed', {
                'position_id': position.position_id,
                'strategy': position.strategy,
                'market_slug': position.market_slug,
                'resolution': position.resolution,
                'pnl_usdc': position.pnl_usdc,
                'mode': MODE,
            })
            logger.info('PM SHADOW RESOLVE %s %s %s pnl=%.4f',
                        position.strategy, position.market_slug,
                        position.resolution, position.pnl_usdc or 0.0)
        return settled

    def sweep_resolutions(self, now: Optional[float] = None) -> dict:
        """Write what every FETCHED market settled at. Forge proposal 038.

        Runs during a HALT, exactly like `resolve()` and for the same reason: a
        halt blocks entries, it does not un-decide a window that already
        settled, and a ledger with a hole in it wherever the operator halted
        would be a ledger selected on the operator.

        Never raises - `ResolutionLedger.sweep` is total and counts its own
        failures - but the call is guarded anyway, because this is a RECORDER
        and a recorder that can stop the trading loop is worse than no recorder
        at all.
        """
        if self.ledger is None:
            return {}
        try:
            return self.ledger.sweep(now)
        except Exception as exc:                            # noqa: BLE001
            self.health['resolution_sweep_exceptions'] += 1
            logger.warning('PM SHADOW resolution sweep raised: %s: %s',
                           type(exc).__name__, exc)
            return {}

    def ledger_stats(self) -> dict:
        """The ledger's own coverage report, or a disabled marker.

        Outside the cycle identity, like `weather` and `timings`: a market
        resolution is not a (cycle, asset, strategy) triple. `unresolved` here
        is proposal 038 rule 7 - markets fetched but NOT resolved, per window,
        with a reason, counted, because a silent gap in that table is the exact
        failure the table exists to stop.
        """
        if self.ledger is None:
            return {'enabled': False}
        stats = self.ledger.stats()
        stats['enabled'] = True
        return stats

    def snapshot_equity(self, ts_ms: Optional[int] = None) -> dict:
        """Write one equity row.

        `equity` and `cash` are the same number by construction and that is not
        a bug: the adapter holds OPEN positions at ZERO rather than marking them
        to a 5-minute book that is mostly noise, so uninvested cash IS the whole
        of measured account value and equity can only ever surprise upward.
        `open_risk` carries the premium currently at risk, which on a binary is
        the exact - not estimated - maximum loss.
        """
        equity = self.adapter.get_equity()
        open_risk = self.adapter.capital_at_risk()
        self.store.record_equity(equity=equity, cash=equity,
                                 open_risk=open_risk, ts_ms=ts_ms)
        return {'equity': equity, 'cash': equity, 'open_risk': open_risk}

    @property
    def evaluations_per_cycle(self) -> int:
        """Strategies times assets: one evaluation per pair, every cycle.

        Summed over the runtimes rather than multiplied out, so that if the
        assets ever run different strategy sets the identity still describes
        what the loop actually does instead of what it was assumed to do.
        """
        return sum(len(rt.strategies) for rt in self.runtimes.values())

    def check_identity(self) -> bool:
        """`evaluations == entries + skips` and `== cycles * strategies * assets`.

        A violated identity means a decision window went unrecorded somewhere,
        which is exactly the silent-drop failure convention 20 exists to catch.
        Logged at ERROR and written to audit_log; never repaired by adjusting a
        counter to match.

        The multi-asset form is the same identity with one more factor. Every
        (cycle, asset, strategy) triple lands in exactly one bucket, including
        the triples where that asset's context failed to build - those are
        attributed to each of that asset's strategies individually, which is why
        an unlisted ETH market cannot quietly shrink the denominator.
        """
        entries = self._entries()
        skips = self._skips()
        expected = self.cycles * self.evaluations_per_cycle
        ok = (self.evaluations == entries + skips
              and self.evaluations == expected)
        if not ok:
            self.identity_violations += 1
            logger.error(
                'PM SHADOW ACCOUNTING IDENTITY VIOLATED: evaluations=%d '
                'entries=%d skips=%d cycles*strategies*assets=%d assets=%s '
                'counts=%s',
                self.evaluations, entries, skips, expected,
                ','.join(self.assets), dict(self.counts))
            try:
                self.store.audit('accounting_violation', {
                    'evaluations': self.evaluations, 'entries': entries,
                    'skips': skips, 'expected': expected,
                    'counts': dict(self.counts)})
            except sqlite3.Error:
                pass
        return ok

    def stats(self) -> dict:
        summary = self.adapter.summary()
        entries = self._entries()
        skips = self._skips()
        return {
            'mode': MODE,
            'cycles': self.cycles,
            'assets': list(self.assets),
            'strategies_per_asset': len(self.strategies),
            'evaluations': self.evaluations,
            'entries': entries,
            'skips': skips,
            'identity_ok': (self.evaluations == entries + skips
                            and self.evaluations
                            == self.cycles * self.evaluations_per_cycle),
            'identity_violations': self.identity_violations,
            'counts': dict(self.counts),
            'health': dict(self.health),
            # Outside the identity on purpose - see manage_exits.
            'exit_counts': dict(self.exit_counts),
            # Outside it too, and for the same reason: a resting order is not a
            # window and looking at one is not an evaluation. For a MAKER
            # strategy these are the result - `rested` against `fill:*` IS the
            # fill rate, and a session with rests and no fills is a finding,
            # not an absence.
            'maker_counts': dict(self.maker_counts),
            'maker_orders': {
                'resting_now': len(self.adapter.open_resting_orders()),
                'resting_buys_now': len(self.adapter.resting_buy_orders()),
                'rested_total': len(self.adapter.resting_orders),
                'capital_committed_usdc': (
                    self.adapter.capital_committed_to_resting_orders()),
                'max_resting_maker_orders': self.max_resting_maker_orders,
                'fill_model': MAKER_FILL_MODEL,
                # The adapter's non-terminal observation outcomes, kept
                # separate from ours: it counts what the BOOK did to an order,
                # we count what happened to the ORDER.
                'adapter_observations': dict(self.adapter.maker_counts),
            },
            # D-362 R4. Outside the identity - a tape row is an observation of
            # a book, not a decision about a window. `contexts` is the
            # denominator: rows==0 with contexts==0 means nothing off-crypto
            # was pollable, rows==0 with contexts>0 means the writer ran and
            # every book was empty or refused. The old defect - nobody writing
            # at all - now shows as contexts==0 on a loop that is polling,
            # rather than as a table that simply stopped growing.
            'market_tape': {
                'rows_written': self.tape_rows_written,
                'contexts': self.tape_contexts,
                'drops': dict(self.market_tape.drops),
                'db_path': self.market_tape.db_path,
            },
            # SECONDS, not events, and outside the identity for the same
            # reason. Reported as total / calls / per-call average because a
            # step that is slow and a step that merely ran a lot produce the
            # same total and need opposite fixes.
            'timings': self.timing_report(),
            # Outside the identity too, and for the same reason as `weather`
            # below: a market resolution is not a (cycle, asset, strategy)
            # triple. Forge proposal 038. `unresolved_by_window` inside is
            # rule 7 - the markets we FETCHED and could not resolve, with a
            # reason, counted, so a gap in that table is never silent.
            'resolution_ledger': self.ledger_stats(),
            # Outside the identity, like `exit_counts` and `timings`, and for a
            # reason stated in the constructor: a weather market is not a
            # (cycle, asset, strategy) triple. It carries its own identity flag.
            'weather': self.weather_stats(),
            # Same argument as `weather`, three more times. Each space carries
            # its OWN identity flag; there is deliberately no pooled total
            # across spaces, because "how many evaluations did we make" summed
            # over four universes on three different cadences is a number that
            # describes none of them (convention 20).
            'spaces': self.space_stats(),
            'halted': is_halted(),
            'equity_usdc': summary['equity_usdc'],
            'open_positions': summary['pending'],
            'resolved': summary['resolved'],
            'closed_early': summary.get('closed_early', 0),
            'by_exit_kind': summary.get('by_exit_kind', {}),
            'realized_pnl_usdc': summary['realized_pnl_usdc'],
            'client_stats': dict(getattr(self.client, 'stats', {}) or {}),
        }

    def space_reason_lines(self, stats: Optional[dict] = None) -> List[str]:
        """One preformatted stats line per off-crypto space (D-317).

        `flush_stats` logs `stats['counts']`, which is the CRYPTO identity's
        counter and nothing else. Every weather, event, sports and political
        disposition lands in that space's OWN counter and in the `signals`
        table, and none of it reached stdout: grepping the log for a space skip
        reason returns 0 BY CONSTRUCTION, which reads exactly like 'that space
        evaluated nothing' and on 2026-08-18 was read that way. Convention 30.

        One line per space and deliberately no pooled total: summing four
        universes running on three different cadences produces a number that
        describes none of them (convention 20). Weather is included because it
        is an off-crypto space with the same blind spot, even though it keeps
        its counters on the loop rather than in a `MarketSpace`.

        Returns the lines rather than logging them so a test can assert the
        content without capturing log output.
        """
        stats = self.stats() if stats is None else stats
        records = [('weather', stats.get('weather') or dict())]
        records.extend(sorted((stats.get('spaces') or dict()).items()))
        lines = []
        for name, record in records:
            lines.append(
                'PM SHADOW space %s enabled=%s cycles=%s evals=%s '
                'identity_ok=%s strategies=%d reasons %s' % (
                    name, record.get('enabled'), record.get('cycles'),
                    record.get('evaluations'),
                    record.get('identity_ok'),
                    len(record.get('strategies') or ()),
                    json.dumps(record.get('counts') or dict(),
                               sort_keys=True)))
        return lines

    def flush_stats(self) -> dict:
        stats = self.stats()
        self.check_identity()
        logger.info('PM SHADOW stats cycles=%d assets=%s evals=%d entries=%d '
                    'skips=%d equity=$%.2f open=%d resolved=%d identity_ok=%s',
                    stats['cycles'], ','.join(self.assets),
                    stats['evaluations'], stats['entries'],
                    stats['skips'], stats['equity_usdc'],
                    stats['open_positions'], stats['resolved'],
                    stats['identity_ok'])
        logger.info('PM SHADOW reasons %s',
                    json.dumps(stats['counts'], sort_keys=True))
        # D-317. Both lines above are the crypto identity and nothing else.
        # Without these an operator cannot see weather, event, sports or
        # political health without opening the database, which is how 2,470
        # rows of `fair_value_model_needs_crypto_spot` got reported as zero.
        # Guarded: instrumentation may never take the run loop down.
        try:
            for line in self.space_reason_lines(stats):
                logger.info('%s', line)
        except Exception as exc:                          # noqa: BLE001
            logger.warning('could not log per-space counters: %s', exc)
        try:
            self.store.audit('shadow_stats', stats)
        except sqlite3.Error as exc:
            logger.warning('could not write stats audit row: %s', exc)
        return stats

    def _note_halt_transition(self) -> None:
        """Record the EDGE, not the level.

        One audit row per transition beats one per cycle, and a halt that is
        never recorded at all is a halt an operator cannot find in the history.
        """
        halted = is_halted()
        if halted == self._halt_state:
            return
        self._halt_state = halted
        event = 'halt' if halted else 'resume'
        payload = {
            'source': 'polymarket_shadow_loop',
            'note': ('HALT blocks Polymarket ENTRIES only. It cannot flatten: '
                     'a binary held to resolution has no sell path in paper '
                     'mode, so open exposure survives the halt.'),
            'open_positions': len(self.adapter.open_positions()),
            'capital_at_risk_usdc': self.adapter.capital_at_risk(),
        }
        try:
            self.store.audit(event, payload)
            self.store.risk_event('kill_switch' if halted else 'resume', payload)
        except sqlite3.Error as exc:
            logger.warning('could not write halt audit row: %s', exc)
        logger.warning('PM SHADOW %s: %s', event.upper(), payload['note'])

    def backoff_sec(self) -> float:
        """Bounded exponential backoff on CONSECUTIVE api errors.

        Zero errors means the normal poll interval. The cap exists because an
        unbounded backoff on a 5-minute market is indistinguishable from a dead
        loop, and a loop that has stopped polling without saying so is the
        silent failure this whole module is written against.
        """
        if self._consecutive_api_errors <= 0:
            return self.poll_sec
        return min(self.poll_sec * (2 ** self._consecutive_api_errors),
                   MAX_BACKOFF_SEC)

    # -- main loop ----------------------------------------------------------

    def run(self, max_cycles: Optional[int] = None,
            duration_sec: Optional[float] = None,
            sleeper=time.sleep) -> dict:
        """Poll until stopped, `max_cycles`, or `duration_sec`.

        Shutdown always flushes: resolve, a final equity snapshot, a stats line
        and a `shadow_stop` audit row. A session whose last equity point is five
        minutes before it died is a session nobody can reconcile.
        """
        self._started_at = time.time()
        self._halt_state = is_halted()
        self.store.audit('shadow_start', {
            'source': 'polymarket_shadow_loop',
            'mode': MODE,
            'paper_mode': PAPER_MODE,
            'starting_equity_usdc': self.starting_equity,
            'poll_sec': self.poll_sec,
            'strategies': [getattr(s, 'strategy_name', str(s))
                           for s in self.strategies],
            'halted_at_start': self._halt_state,
            'pid': os.getpid(),
        })
        # The starting point of the equity curve is a fact worth writing down
        # before anything can change it.
        self.snapshot_equity()
        self._last_equity_snapshot = time.time()
        self._last_resolve = time.time()
        self._last_stats_flush = time.time()

        logger.info('PM SHADOW start: mode=%s equity=$%.2f poll=%.1fs '
                    'strategies=%d halted=%s pid=%d', MODE,
                    self.starting_equity, self.poll_sec, len(self.strategies),
                    self._halt_state, os.getpid())

        try:
            while not self._stop:
                if max_cycles is not None and self.cycles >= max_cycles:
                    break
                if (duration_sec is not None and self._started_at is not None
                        and time.time() - self._started_at >= duration_sec):
                    break

                self._note_halt_transition()
                self.run_cycle()

                now = time.time()
                # The weather cycle, on its own cadence. AFTER `run_cycle` so a
                # slow weather sweep can never delay the 5-minute crypto window,
                # and guarded by its own timer so it cannot run every poll.
                # `run_weather_cycle` never raises; it counts instead.
                if (self.enable_weather
                        and now - self._last_weather_cycle
                        >= self.weather_cycle_sec):
                    self._last_weather_cycle = now
                    self.run_weather_cycle(now)
                # The general binary spaces, each on its own timer. AFTER the
                # crypto cycle for the same reason weather is: a slow Gamma
                # sweep must never delay a 5-minute window. Each space is
                # timed independently so one slow universe cannot starve the
                # next - `run_space_cycle` never raises, it counts.
                if self.enable_spaces:
                    for space in self.spaces:
                        if (space.enabled
                                and now - space.last_cycle >= space.cycle_sec):
                            self.run_space_cycle(space, now)
                if now - self._last_resolve >= self.resolve_sec:
                    self.resolve()
                    self._last_resolve = now
                # AFTER `resolve()` and after every trading phase: the ledger
                # is a recorder, it holds no position and frees no slot, so
                # nothing in a cycle should ever wait on it (proposal 038).
                if (now - self._last_resolution_sweep
                        >= self.resolution_sweep_sec):
                    self.sweep_resolutions(now)
                    self._last_resolution_sweep = now
                if now - self._last_equity_snapshot >= self.equity_snapshot_sec:
                    self.snapshot_equity()
                    self._last_equity_snapshot = now
                if now - self._last_stats_flush >= self.stats_flush_sec:
                    self.flush_stats()
                    self._last_stats_flush = now

                if not self._stop:
                    sleeper(self.backoff_sec())
        except KeyboardInterrupt:
            logger.warning('PM SHADOW interrupted; flushing before exit')
        finally:
            stats = self.shutdown()
        return stats

    def shutdown(self) -> dict:
        """Final resolve, final ledger sweep, final equity snapshot, stats."""
        self.resolve()
        # One last sweep, so a window that closed during the final cycle is
        # recorded rather than lost to the restart. Anything still pending is
        # simply not written - it is an ABSENT row, counted by the ledger's
        # rule-7 report, never a row at 0.00.
        self.sweep_resolutions()
        try:
            self.snapshot_equity()
        except sqlite3.Error as exc:
            logger.error('could not write final equity snapshot: %s', exc)
        stats = self.flush_stats()
        try:
            self.store.audit('shadow_stop', stats)
        except sqlite3.Error as exc:
            logger.warning('could not write shadow_stop audit row: %s', exc)
        logger.info('PM SHADOW stopped. equity=$%.2f entries=%d evaluations=%d',
                    stats['equity_usdc'], stats['entries'],
                    stats['evaluations'])
        return stats


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def load_config(path: str = 'config.yaml') -> dict:
    try:
        import yaml
    except ImportError:
        logger.warning('pyyaml not available; running on module defaults')
        return {}
    if not os.path.exists(path):
        logger.warning('no %s; running on module defaults', path)
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Polymarket paper/shadow trading loop (PAPER ONLY).')
    p.add_argument('--poll', type=float, default=DEFAULT_POLL_SEC,
                   help='seconds between polls (default: %(default)s)')
    p.add_argument('--equity', type=float, default=DEFAULT_STARTING_EQUITY_USDC,
                   help='starting paper balance in USDC (default: %(default)s)')
    p.add_argument('--db', default=None, help='path to trading.db')
    p.add_argument('--log-dir', default=None,
                   help='directory for the paper adapter CSV')
    p.add_argument('--config', default='config.yaml')
    p.add_argument('--max-cycles', type=int, default=None)
    p.add_argument('--duration-sec', type=float, default=None)
    p.add_argument('--assets', default=','.join(SHADOW_ASSETS),
                   help='comma-separated crypto Up/Down 5m assets to poll '
                        '(default: %(default)s). Each one runs the full '
                        'strategy set against its own market with its own '
                        'per-window state.')
    p.add_argument('--no-candles', action='store_true',
                   help='do not fetch candles (streak_snapper will then '
                        'skip no_magnitude_data on every window)')
    p.add_argument('--no-15m', action='store_true',
                   help='skip the 15m market read used by corridor_collector')
    p.add_argument('--verbose', action='store_true')
    p.add_argument('--strategies', default=None,
                   help='comma-separated strategy_id whitelist restricting '
                        'the ROUTED sets (crypto, weather, spaces) to those '
                        'names; default = all. The registry itself '
                        '(build_strategies() and its pinned indices) is '
                        'untouched.')
    return p


def filter_strategies_by_name(loop: 'PolymarketShadowLoop',
                              names_csv: str) -> None:
    """Restrict every routed strategy set on `loop` to `names_csv`.

    Applied AFTER construction, to the routed sets (`runtimes[*].strategies`,
    `weather_strategies`, `spaces[*].strategies`) rather than by rebuilding the
    loop from a pre-filtered pool: rebuilding would lose real per-strategy
    wiring that only happens inside the loop's own registry call (e.g.
    DipArb's `dip_arb_tape_db_path`, proposal 031). `evaluations_per_cycle` is
    a property summed over the same lists, so it and the accounting identity
    follow the filter automatically.

    This is for controlled A/B shadow runs (survivors-only environment on a
    separate DB) where one process needs a strict subset of the full book.
    The registry (`build_strategies()`, its pinned indices, `_registry_names`)
    is never touched.
    """
    whitelist = {name.strip() for name in names_csv.split(',') if name.strip()}
    matched = set()

    def _kept(strategies):
        """Filter to the whitelist, recording which names actually matched."""
        out = []
        for s in strategies:
            name = getattr(s, 'strategy_name', None)
            if name in whitelist:
                matched.add(name)
                out.append(s)
        return out

    for rt in loop.runtimes.values():
        rt.strategies[:] = _kept(rt.strategies)
    loop.weather_strategies[:] = _kept(loop.weather_strategies)
    for space in loop.spaces:
        space.strategies[:] = _kept(space.strategies)

    # A whitelist name that matches nothing stays a NO-OP - a typo must not
    # take down a shadow run mid-flight - but it must not stay SILENT. An
    # unnoticed typo in `--strategies` makes an A/B environment quietly
    # thinner than intended, and the run then reads as a clean result for a
    # book it never actually had. Convention 20: a silent skip is a missing
    # number. One line, at WARNING, naming every unmatched name.
    unmatched = whitelist - matched
    if unmatched:
        logger.warning('--strategies names matched nothing: %s',
                       ', '.join(sorted(unmatched)))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    config = load_config(args.config)

    # The strike-proxy noise floor is per asset and overridable from the
    # config, so changing how much the gate admits does not need a code edit
    # (convention 17). A bad value RAISES here rather than being coerced: a
    # NaN floor would make `abs(lead) < floor` False for every lead and
    # silently disable the gate while looking configured.
    try:
        floors = set_noise_floor_bps_by_asset(
            (config.get('polymarket') or {})
            .get('strike_proxy', {})
            .get('noise_floor_bps_by_asset'))
    except ValueError as exc:
        logger.error('REFUSING TO START: bad strike proxy noise floor: %s', exc)
        return 1
    logger.info('strike proxy noise floor by asset (bps): %s', floors)

    # The weather settings, same shape and same reason: changing whether a
    # daily-extreme market is priced must not need a code edit (convention 17),
    # and a bad value RAISES here rather than being coerced. `'false'` is a
    # truthy string, and coercing it would turn a refusal into permission while
    # the row stamp kept saying the flag was off.
    try:
        weather = set_weather_config(
            (config.get('polymarket') or {}).get('weather'))
    except ValueError as exc:
        logger.error('REFUSING TO START: bad polymarket.weather config: %s', exc)
        return 1
    logger.info('weather settings: %s', weather)

    # Outer mode gate. `run_polymarket_shadow.sh` checks this too, and the risk
    # gate refuses a non-paper mode outright. Three readers, one answer.
    mode = config.get('mode')
    if mode is not None and mode != 'paper':
        logger.error("REFUSING TO START: config mode is %r, not 'paper'", mode)
        return 1
    if os.environ.get('TRADING_LIVE_ACK'):
        logger.error('REFUSING TO START: TRADING_LIVE_ACK is set. A shadow '
                     'session is not the place to be half-way through arming '
                     'live.')
        return 1

    assets = [a.strip().lower() for a in (args.assets or '').split(',')
              if a.strip()]
    if not assets:
        logger.error('REFUSING TO START: --assets is empty. A loop with no '
                     'assets runs zero evaluations and looks healthy doing it.')
        return 1
    try:
        for key in assets:
            get_asset(key)
    except KeyError as exc:
        logger.error('REFUSING TO START: %s', exc)
        return 1

    # A FACTORY, not one source: each asset needs candles for its own pair, and
    # one shared BTC source would compute SOL's ATR from BTC's bars.
    candle_source_factory = None
    if not args.no_candles:
        candle_source_factory = default_candle_source_factory(config)

    loop = PolymarketShadowLoop(
        config=config, poll_sec=args.poll, starting_equity=args.equity,
        db_path=args.db, log_dir=args.log_dir, assets=assets,
        candle_source_factory=candle_source_factory,
        include_15m=not args.no_15m)
    loop.install_signal_handlers()

    if args.strategies:
        filter_strategies_by_name(loop, args.strategies)
        logger.info('strategies=%d (filtered from %d)',
                    len(loop.strategies), len(loop._registry_names))

    print('=' * 72, flush=True)
    print('POLYMARKET SHADOW LOOP - PAPER MODE ONLY', flush=True)
    print('  starting equity : ${:,.2f}'.format(loop.starting_equity))
    print('  poll interval   : {:.1f}s'.format(loop.poll_sec))
    print('  database        : {}'.format(loop.store.db_path))
    print('  decision csv    : {}'.format(loop.adapter.log_path))
    print('  assets          : {}'.format(', '.join(loop.assets)))
    print('  strategies      : {} per asset, {} evaluations per cycle'.format(
        len(loop.strategies), loop.evaluations_per_cycle))
    print('    {}'.format(', '.join(
        getattr(s, 'strategy_name', str(s)) for s in loop.strategies)))
    print('  weather cycle   : {} every {:.0f}s, top {} markets by volume'
          .format('ON' if loop.enable_weather else 'OFF',
                  loop.weather_cycle_sec, loop.weather_market_limit))
    print('    {}'.format(', '.join(
        getattr(s, 'strategy_name', str(s))
        for s in loop.weather_strategies) or 'none'))
    print('    daily-extreme markets priced: {}'.format(
        weather['allow_daily_extreme_markets']))
    print('  halted          : {}'.format(is_halted()))
    print('  NOTE: a HALT blocks Polymarket ENTRIES only. It cannot flatten.',
          flush=True)
    print('=' * 72, flush=True)

    loop.run(max_cycles=args.max_cycles, duration_sec=args.duration_sec)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
