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

The strategy list is `strategies.polymarket.build_strategies()`, which is now
EIGHT strategies. FIVE of them can produce an entry in this loop; the other
three are blocked by missing DATA, not by their own logic:

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
  PM_box_builder             CANNOT. It is a MAKER strategy and returns QUOTE,
                             never ENTER. The adapter simulates taker fills
                             only, so its resting fills cannot be modelled
                             honestly. Counted as `maker_quote_not_simulable`.

Those three are NOT_TESTED, not tested-and-found-nothing (convention 11). The
loop names them in its own counters so a zero-entry session cannot be mistaken
for a session where the strategies looked and declined.

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
import json
import logging
import math
import os
import signal
import sqlite3
import time
import traceback
import uuid
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from engine.db import get_db_path
from engine.halt import is_halted
from engine.polymarket.client import PolymarketClient
from engine.polymarket.strike import (STRIKE_PROXY_NOISE_FLOOR_BPS,
                                     StrikeProxy, is_inside_noise_floor)
from engine.polymarket.context import (fetch_btc_spot_checked,
                                       price_windows_checked)
from engine.polymarket.markets import (current_window_ts,
                                       get_btc_updown_5m_checked,
                                       get_market_by_slug_checked)
from engine.polymarket.orderbook import orderbook_from_api
from engine.polymarket.paper_adapter import PolymarketPaperAdapter
from engine.polymarket.risk_gate import PolymarketRiskGate
from engine.polymarket.types import WINNING_REDEMPTION
from strategies.polymarket import build_strategies
from strategies.polymarket.base import MarketContext, window_atr

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
DEFAULT_STATS_FLUSH_SEC = 60.0
DEFAULT_CANDLE_REFRESH_SEC = 60.0

#: Bounded exponential backoff on consecutive API failures. Bounded because an
#: unbounded backoff on a 5-minute market is indistinguishable from a dead loop.
MAX_BACKOFF_SEC = 60.0

WINDOW_LOOKBACK = 16
CANDLE_PAIR = 'BTC/USDT'
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
SKIP_MAKER = 'maker_quote_not_simulable'
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


def _ms(seconds: Optional[float] = None) -> int:
    """Unix milliseconds. Every ts column in db/schema.sql is in ms."""
    return int((time.time() if seconds is None else seconds) * 1000)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

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

    def ensure_schema(self) -> None:
        """Idempotent migration. Replays db/schema.sql verbatim.

        If the file is missing we do NOT invent a schema - a loop writing into
        tables nobody declared is worse than a loop that refuses to start.
        """
        if not os.path.exists(self.SCHEMA_PATH):
            raise RuntimeError(
                'db/schema.sql not found at {}; refusing to invent a schema'
                .format(self.SCHEMA_PATH))
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
                      ts_ms: Optional[int] = None) -> str:
        """One row per EVALUATION, entry or skip. Returns the signal id.

        `direction` is 'long' on every row: the only Polymarket action this loop
        can take is a BUY of one outcome token. WHICH outcome lives in
        `features_json['outcome_side']`, because the direction column is a
        'long' | 'exit' contract shared with the crypto path and overloading it
        with 'Up'/'Down' would break every existing reader.
        """
        signal_id = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                'INSERT INTO signals (id, ts, pair, tf, strategy_id, pattern, '
                'direction, confidence, features_json, acted, skip_reason, mode) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (signal_id, ts_ms if ts_ms is not None else _ms(),
                 market_slug or 'POLYMARKET', '5m', strategy_id, pattern,
                 direction, float(confidence), self._json(features),
                 1 if acted else 0,
                 None if acted else skip_reason, MODE))
        return signal_id

    def record_entry(self, position, *, signal_id: str, limit_price: float,
                     strategy_id: str) -> None:
        """orders + fills + positions for one simulated fill, in ONE transaction.

        A fill row whose order row is missing is a reconciliation problem
        invented by our own bookkeeping, and the dashboard joins fills to orders
        to derive fees.
        """
        order_id = str(uuid.uuid4())
        fill_id = str(uuid.uuid4())
        ts_ms = int(position.opened_ts) * 1000
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
                'mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (position.position_id, position.market_slug, strategy_id,
                 signal_id, ts_ms, None, position.avg_price, None,
                 position.shares,
                 # A losing binary share is worth exactly 0.00 and that IS the
                 # stop (convention 8: strictly below any valid entry). The
                 # target is resolution at 1.00. Neither is a modelling choice.
                 0.0, WINNING_REDEMPTION,
                 None, None, position.fee_usdc, None, None, MODE))

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

def default_candle_source(config: dict):
    """Binance.US 5m BTC candles via the project's existing DataCollector.

    Why this exists at all: `resolved_windows` gives DIRECTION and nothing else
    (the oracle publishes no open/close), so an ATR computed off oracle windows
    is 1.0 by construction and every stretch ratio comes out near the streak
    length. streak_snapper refuses to run on that - correctly - with
    `no_magnitude_data`. Real USD magnitudes have to come from a price feed, and
    Binance.US is the one this project already uses.

    Read-only: `fetch_ohlcv` is called, `store_candles` is not. The shadow loop
    does not own the candles table.
    """
    from engine.collector import DataCollector

    collector = DataCollector(config)

    def _fetch() -> Optional[dict]:
        rows = collector.fetch_ohlcv(CANDLE_PAIR, CANDLE_TF,
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
                 stats_flush_sec: float = DEFAULT_STATS_FLUSH_SEC,
                 candle_refresh_sec: float = DEFAULT_CANDLE_REFRESH_SEC,
                 include_15m: bool = True,
                 strike_proxy: Optional[StrikeProxy] = None):
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
        self.strategies = (list(strategies) if strategies is not None
                           else build_strategies())
        self.candle_source = candle_source

        self.poll_sec = float(poll_sec)
        self.equity_snapshot_sec = float(equity_snapshot_sec)
        self.resolve_sec = float(resolve_sec)
        self.stats_flush_sec = float(stats_flush_sec)
        self.candle_refresh_sec = float(candle_refresh_sec)
        self.include_15m = bool(include_15m)
        # The strike Gamma does not publish, served as a MEASURED proxy.
        # Injectable for the same reason `candle_source` is: a default that
        # reaches the network turns every unit test into a live API call, and a
        # test whose result depends on what BTC did this minute is not a test.
        # Reuses the client's connection pool when there is one; these are not
        # Polymarket hosts, so no Polymarket rate limiter applies either way.
        # getattr, not attribute access: a client stand-in without a `session`
        # is a legitimate caller, and StrikeProxy opens its own on None.
        self.strike_proxy = strike_proxy if strike_proxy is not None else (
            StrikeProxy(session=getattr(self.client, 'session', None)))

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
        self.exit_counts: Counter = Counter()
        self.identity_violations = 0

        self._stop = False
        self._halt_state = False
        self._candles: Optional[dict] = None
        self._candles_at = 0.0
        self._consecutive_api_errors = 0
        self._last_resolve = 0.0
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

    def _count(self, disposition: str) -> str:
        """Record one evaluation's disposition. The only way a count moves."""
        self.evaluations += 1
        self.counts[disposition] += 1
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

    def _refresh_candles(self, now: float) -> None:
        """Re-pull BTC 5m candles at most every `candle_refresh_sec`.

        A failed refresh keeps the PREVIOUS candles rather than blanking them:
        an outage is not a market with no history (convention 11). Staleness is
        bounded by the 5m bar itself, and `candles_age_sec` rides in every
        decision's features so a stale-context decision stays identifiable
        after the fact.
        """
        if self.candle_source is None:
            return
        if (self._candles is not None
                and now - self._candles_at < self.candle_refresh_sec):
            return
        try:
            candles = self.candle_source()
        except Exception as exc:
            self.health['candle_fetch_exception'] += 1
            logger.warning('candle source raised: %s: %s',
                           type(exc).__name__, exc)
            return
        if not candles or not candles.get('closes'):
            self.health['candle_fetch_empty'] += 1
            return
        self._candles = candles
        self._candles_at = now
        self.health['candle_refresh_ok'] += 1

    # -- context ------------------------------------------------------------

    def build_context(self, window_ts: int, now: Optional[float] = None
                      ) -> Tuple[Optional[MarketContext], str, dict]:
        """Assemble one cycle's MarketContext from live reads.

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
        detail: dict = {'window_ts': window_ts}

        market, status = get_btc_updown_5m_checked(self.client, window_ts)
        if market is None:
            detail['market_status'] = status
            if status == 'read_failed':
                return None, SKIP_API_ERROR, detail
            # 'not_found' plus every MARKET_DROP_REASONS value: Gamma answered,
            # and the answer was that there is no usable market here.
            return None, SKIP_NO_MARKET, detail

        detail['market_slug'] = market.slug
        books: Dict[str, object] = {}
        book_status: Dict[str, str] = {}
        for outcome in market.outcomes:
            book, bstatus = self._fetch_book_checked(outcome.token_id)
            book_status[outcome.name] = bstatus
            if book is not None:
                books[outcome.token_id] = book
        detail['book_status'] = book_status

        if not books:
            # Every side unreadable. WHICH KIND of unreadable decides the
            # response, so report the API failure in preference to the empty
            # book: backing off is right when the venue is unreachable and
            # wrong when it is merely quiet.
            if SKIP_API_ERROR in book_status.values():
                return None, SKIP_API_ERROR, detail
            return None, SKIP_NO_LIQUIDITY, detail

        self._refresh_candles(now)
        windows: List = []
        if self._candles is not None:
            built = price_windows_checked(self._candles, lookback=WINDOW_LOOKBACK)
            windows = built['windows']
            if built['drops']:
                detail['candle_drops'] = built['drops']
        detail['windows'] = len(windows)
        detail['candles_age_sec'] = (round(now - self._candles_at, 1)
                                     if self._candles is not None else None)

        spot_result = fetch_btc_spot_checked(self.client)
        spot = spot_result['spot']
        detail['spot_source'] = spot_result['source']
        if spot is None:
            detail['spot_failures'] = spot_result['failures']
            self.health['spot_unavailable'] += 1

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
        strike_result = self.strike_proxy.strike_for(window_ts, now=now)
        strike = strike_result['strike']
        detail['strike_source'] = strike_result['source']
        detail['strike_is_proxy'] = strike_result['is_proxy']
        detail['strike_bar_age_sec'] = strike_result['bar_age_sec']
        if strike is None:
            self.health['strike_unavailable'] += 1

        lead_bps = None
        if spot is not None and strike:      # truthy also guards strike == 0
            lead_bps = (spot - strike) / strike * 10_000.0
        detail['lead_bps'] = (None if lead_bps is None else round(lead_bps, 3))

        market_15m = None
        books_15m: Dict[str, object] = {}
        if self.include_15m:
            ts15 = ((window_ts // BTC_UPDOWN_15M_DURATION)
                    * BTC_UPDOWN_15M_DURATION)
            m15, s15 = get_market_by_slug_checked(
                self.client, BTC_UPDOWN_15M_SLUG.format(ts=ts15))
            detail['market_15m_status'] = s15
            if m15 is not None:
                market_15m = m15
                for outcome in m15.outcomes:
                    book, _ = self._fetch_book_checked(outcome.token_id)
                    if book is not None:
                        books_15m[outcome.token_id] = book

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

    # -- evaluation ---------------------------------------------------------

    def _log_and_count(self, strategy_name: str, market_slug: Optional[str],
                       disposition: str, reason: str, features: dict,
                       confidence: float = 0.0, acted: bool = False,
                       window_ts: Optional[int] = None,
                       log_csv: bool = True) -> str:
        """Count one evaluation, write its signals row, and (usually) its CSV row.

        `log_csv=False` is used on exactly one path: when the paper adapter has
        ALREADY written its own row for this window. Writing a second one would
        double-count the window in `adapter.decision_counts` and put two rows in
        the CSV for one decision, which is the mirror image of the silent-skip
        problem - a window that looks like two.
        """
        self._count(disposition)
        if log_csv:
            self.adapter.log_skip(
                strategy_name, market_slug or 'unknown', reason,
                window_ts='' if window_ts is None else window_ts,
                features=';'.join('{}={}'.format(k, v)
                                  for k, v in sorted(features.items())))
        self.store.record_signal(
            strategy_id=strategy_name, market_slug=market_slug,
            pattern=strategy_name, direction='long', confidence=confidence,
            features=features, acted=acted, skip_reason=reason)
        return disposition

    def evaluate_strategy(self, strategy, ctx: MarketContext,
                          detail: dict) -> str:
        """Evaluate one strategy against one context. Returns the disposition.

        Always returns, and every exit path has counted and logged first.
        """
        name = getattr(strategy, 'strategy_name', str(strategy))
        slug = getattr(ctx.market, 'slug', None)

        # Strike-dependent strategies are gated BEFORE they evaluate, not after.
        # Letting one read a sub-noise-floor lead and decline on its own terms
        # would record a measurement error as a strategy decision, and the two
        # are indistinguishable once they share a skip reason.
        if getattr(strategy, 'needs_strike', False):
            if ctx.strike is None:
                return self._log_and_count(name, slug,
                                           'strategy:no_spot_or_strike',
                                           'no_spot_or_strike',
                                           {'strike_available': False},
                                           window_ts=ctx.window_ts)
            if is_inside_noise_floor(ctx.lead_bps):
                return self._log_and_count(
                    name, slug, 'strategy:' + SKIP_PROXY_NOISE,
                    SKIP_PROXY_NOISE,
                    {'lead_bps': (None if ctx.lead_bps is None
                                  else round(ctx.lead_bps, 3)),
                     'noise_floor_bps': STRIKE_PROXY_NOISE_FLOOR_BPS,
                     'strike_is_proxy': True},
                    window_ts=ctx.window_ts)

        decision = strategy.evaluate(ctx)
        feats = dict(decision.features or {})
        feats['cycle'] = detail.get('cycle')
        feats['window_ts'] = ctx.window_ts
        feats['seconds_into_window'] = (None if ctx.seconds_into_window is None
                                        else round(ctx.seconds_into_window, 1))
        feats['candles_age_sec'] = detail.get('candles_age_sec')
        try:
            confidence = float(feats.get('confidence') or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        if decision.action == 'QUOTE':
            # A maker quote is not an entry and must never be filled as one.
            # Simulating a resting bid as a taker lift would manufacture exactly
            # the fills box_builder's claimed edge depends on.
            return self._log_and_count(name, slug, SKIP_MAKER,
                                       decision.reason or SKIP_MAKER, feats,
                                       confidence, window_ts=ctx.window_ts)

        if not decision.is_entry:
            reason = decision.reason or 'unspecified'
            return self._log_and_count(name, slug, 'strategy:' + reason, reason,
                                       feats, confidence,
                                       window_ts=ctx.window_ts)

        if not decision.legs:
            # An ENTER with no legs is a strategy bug, not a market condition.
            return self._log_and_count(name, slug, SKIP_NO_LEGS, SKIP_NO_LEGS,
                                       feats, confidence,
                                       window_ts=ctx.window_ts)

        return self._attempt_entry(strategy, decision, ctx, feats, confidence)

    def _attempt_entry(self, strategy, decision, ctx: MarketContext,
                       feats: dict, confidence: float) -> str:
        """Halt check, risk gate, then the paper adapter, for every leg.

        Multi-leg (corridor_collector) fills legs leader-first, which is the
        strategy's own stated ordering: if the second leg fails you are left
        holding the side that is currently winning. A partial pair is recorded
        as a partial pair in `health`, never reported as a clean entry.
        """
        name = getattr(strategy, 'strategy_name', str(strategy))
        slug = getattr(ctx.market, 'slug', None)

        # 1. The kill switch, before anything else. Checked HERE so a halted
        # window is counted as `halted` rather than reaching the adapter and
        # returning an anonymous refusal. The adapter checks again; that is the
        # backstop, not a duplicate.
        if is_halted():
            return self._log_and_count(
                name, slug, SKIP_HALTED, SKIP_HALTED,
                dict(feats, halt_note=('polymarket halt blocks ENTRIES only; a '
                                       'binary held to resolution has no sell '
                                       'path in paper mode, so a halt cannot '
                                       'flatten open exposure')),
                confidence, window_ts=ctx.window_ts)

        filled = []
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
                self.health['leg_unknown_token'] += 1
                continue
            book = ctx.books.get(token_id) or ctx.books_15m.get(token_id)
            if book is None:
                first_block = first_block or SKIP_NO_LIQUIDITY
                self.health['leg_no_book'] += 1
                continue

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

            filled.append((position, leg))

        if not filled:
            reason = first_block or 'adapter:unreported'
            return self._log_and_count(name, slug, reason, reason, feats,
                                       confidence, window_ts=ctx.window_ts,
                                       log_csv=not adapter_logged)

        if len(filled) < len(decision.legs):
            self.health['partial_pairs'] += 1

        # The entry is counted ONCE per evaluation (the identity counts
        # evaluations, not legs) and written once per leg into the DB.
        self._count('entry')
        signal_id = self.store.record_signal(
            strategy_id=name, market_slug=slug, pattern=name, direction='long',
            confidence=confidence,
            features=dict(feats, legs_filled=len(filled),
                          legs_requested=len(decision.legs),
                          outcome_side=filled[0][1].outcome_side),
            acted=True, skip_reason=None)
        for position, leg in filled:
            self.store.record_entry(position, signal_id=signal_id,
                                    limit_price=leg.limit_price,
                                    strategy_id=name)
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

    # -- exits ----------------------------------------------------------------

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

    def run_cycle(self, now: Optional[float] = None) -> dict:
        """One poll. Never raises: an unexpected failure is a counted category.

        Returns a small summary dict, mostly for tests and the stats line.
        """
        now = time.time() if now is None else now
        self.cycles += 1
        window_ts = current_window_ts(now)
        detail: dict = {'cycle': self.cycles}

        try:
            ctx, status, built = self.build_context(window_ts, now)
            detail.update(built)
        except Exception as exc:
            logger.error('PM SHADOW cycle %d raised in context build: %s',
                         self.cycles,
                         traceback.format_exc().strip().splitlines()[-1])
            self.health['context_exceptions'] += 1
            ctx, status = None, SKIP_CYCLE_EXCEPTION
            detail['exception'] = '{}: {}'.format(type(exc).__name__, exc)

        if status == SKIP_API_ERROR:
            self._consecutive_api_errors += 1
            detail['api_error_attempt'] = self._consecutive_api_errors
        elif status == 'ok':
            self._consecutive_api_errors = 0

        if ctx is None:
            # Attribute the cycle-level failure to EVERY strategy, so
            # `evaluations == cycles * n_strategies` holds unconditionally and a
            # session that never reached a strategy is visibly a session that
            # never reached a strategy rather than a session with no signals.
            reason = status
            if status == SKIP_API_ERROR:
                reason = '{}:attempt_{}'.format(SKIP_API_ERROR,
                                                self._consecutive_api_errors)
            for strategy in self.strategies:
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

        for strategy in self.strategies:
            name = getattr(strategy, 'strategy_name', str(strategy))
            try:
                self.evaluate_strategy(strategy, ctx, detail)
            except Exception as exc:
                # A strategy that raises must not take the other three, or the
                # loop, with it. It is still one evaluation and it is still
                # counted.
                logger.error('PM SHADOW strategy %s raised: %s: %s',
                             name, type(exc).__name__, exc)
                self.health['strategy_exceptions'] += 1
                self._log_and_count(
                    name, getattr(ctx.market, 'slug', None),
                    SKIP_CYCLE_EXCEPTION,
                    '{}:{}'.format(type(exc).__name__, exc)[:200],
                    dict(detail), window_ts=window_ts)

        detail['status'] = 'ok'
        return detail

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

    def check_identity(self) -> bool:
        """`evaluations == entries + skips` and `== cycles * n_strategies`.

        A violated identity means a decision window went unrecorded somewhere,
        which is exactly the silent-drop failure convention 20 exists to catch.
        Logged at ERROR and written to audit_log; never repaired by adjusting a
        counter to match.
        """
        entries = self._entries()
        skips = self._skips()
        expected = self.cycles * len(self.strategies)
        ok = (self.evaluations == entries + skips
              and self.evaluations == expected)
        if not ok:
            self.identity_violations += 1
            logger.error(
                'PM SHADOW ACCOUNTING IDENTITY VIOLATED: evaluations=%d '
                'entries=%d skips=%d cycles*strategies=%d counts=%s',
                self.evaluations, entries, skips, expected, dict(self.counts))
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
            'evaluations': self.evaluations,
            'entries': entries,
            'skips': skips,
            'identity_ok': (self.evaluations == entries + skips
                            and self.evaluations
                            == self.cycles * len(self.strategies)),
            'identity_violations': self.identity_violations,
            'counts': dict(self.counts),
            'health': dict(self.health),
            # Outside the identity on purpose - see manage_exits.
            'exit_counts': dict(self.exit_counts),
            'halted': is_halted(),
            'equity_usdc': summary['equity_usdc'],
            'open_positions': summary['pending'],
            'resolved': summary['resolved'],
            'closed_early': summary.get('closed_early', 0),
            'by_exit_kind': summary.get('by_exit_kind', {}),
            'realized_pnl_usdc': summary['realized_pnl_usdc'],
            'client_stats': dict(getattr(self.client, 'stats', {}) or {}),
        }

    def flush_stats(self) -> dict:
        stats = self.stats()
        self.check_identity()
        logger.info('PM SHADOW stats cycles=%d evals=%d entries=%d skips=%d '
                    'equity=$%.2f open=%d resolved=%d identity_ok=%s',
                    stats['cycles'], stats['evaluations'], stats['entries'],
                    stats['skips'], stats['equity_usdc'],
                    stats['open_positions'], stats['resolved'],
                    stats['identity_ok'])
        logger.info('PM SHADOW reasons %s',
                    json.dumps(stats['counts'], sort_keys=True))
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
                if now - self._last_resolve >= self.resolve_sec:
                    self.resolve()
                    self._last_resolve = now
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
        """Final resolve, final equity snapshot, final stats."""
        self.resolve()
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
    p.add_argument('--no-candles', action='store_true',
                   help='do not fetch BTC candles (streak_snapper will then '
                        'skip no_magnitude_data on every window)')
    p.add_argument('--no-15m', action='store_true',
                   help='skip the 15m market read used by corridor_collector')
    p.add_argument('--verbose', action='store_true')
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    config = load_config(args.config)

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

    candle_source = None
    if not args.no_candles:
        try:
            candle_source = default_candle_source(config)
        except Exception as exc:
            logger.warning('no candle source (%s: %s); streak_snapper will '
                           'skip no_magnitude_data on every window',
                           type(exc).__name__, exc)

    loop = PolymarketShadowLoop(
        config=config, poll_sec=args.poll, starting_equity=args.equity,
        db_path=args.db, log_dir=args.log_dir, candle_source=candle_source,
        include_15m=not args.no_15m)
    loop.install_signal_handlers()

    print('=' * 72, flush=True)
    print('POLYMARKET SHADOW LOOP - PAPER MODE ONLY', flush=True)
    print('  starting equity : ${:,.2f}'.format(loop.starting_equity))
    print('  poll interval   : {:.1f}s'.format(loop.poll_sec))
    print('  database        : {}'.format(loop.store.db_path))
    print('  decision csv    : {}'.format(loop.adapter.log_path))
    print('  strategies      : {}'.format(', '.join(
        getattr(s, 'strategy_name', str(s)) for s in loop.strategies)))
    print('  halted          : {}'.format(is_halted()))
    print('  NOTE: a HALT blocks Polymarket ENTRIES only. It cannot flatten.',
          flush=True)
    print('=' * 72, flush=True)

    loop.run(max_cycles=args.max_cycles, duration_sec=args.duration_sec)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
