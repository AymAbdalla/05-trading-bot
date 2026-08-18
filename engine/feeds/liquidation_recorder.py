"""24/7 recorder of perpetual-futures liquidation events. BYBIT ONLY.

READ-ONLY MARKET DATA. This module imports no key material and no order API.
Every endpoint is public and unauthenticated. The only thing it can do is
append rows to the `liquidations` table.

Run it with `run_liquidation_recorder.sh`, not by hand, so the PID file and the
log naming stay consistent with the other long-running jobs in this repo.


## WHICH VENUES ARE HERE, AND WHY THE OTHER TWO ARE NOT

The runnable set is `SUPPORTED_EXCHANGES` and it contains exactly one venue.
`RETIRED_EXCHANGES` holds the other two together with the MEASUREMENT that
retired each, so that "add Binance back" is a decision someone takes against
evidence rather than a plausible-looking one-line default change.

  bybit        ACTIVE. Measured 2026-08-18: 118 real trades in 20s on the
               control socket. Venue-wide per symbol, 3 symbols, no wildcard.

  binance      REMOVED, geoblocked from this machine. `fapi.binance.com/
               fapi/v1/ping` returns HTTP 451, and `!forceOrder@arr` delivered
               0 frames in 25s - one of the highest-volume streams in crypto.
               The nasty part, and the reason this is a hard refusal rather
               than a warning: the websocket TLS HANDSHAKE SUCCEEDS. So the old
               code logged `CONNECTED, reconnects=0` forever while recording
               nothing. A permanent silent zero that reads as uptime. Binance.US
               has no futures and is not a substitute.

  hyperliquid  NOT AVAILABLE. There is no public venue-wide liquidation feed to
               add. Measured 2026-08-18 (full log in
               research/hyperliquid/liquidation_source_probe.md):
                 * POST /info with type liquidations / recentLiquidations /
                   allLiquidations -> HTTP 422, and the error body is
                   BYTE-IDENTICAL to the one returned for the invented type
                   `zzzNotARealType`. It is not deprecated or gated; the server
                   has simply never heard of it.
                 * All five liquidation WS subscriptions -> {"channel":"error"},
                   while `trades` ACKed on the same socket in the same run.
                 * The public `trades` payload's complete key set over 197
                   trades is [coin, hash, px, side, sz, tid, time, users].
                   There is NO liquidation flag on it.
               So `trades` is NOT a liquidation feed and must never be recorded
               into this table as one. Doing that would manufacture a tape and
               reproduce the exact Binance failure shape - a wrong number that
               reads as data. `userFills` does carry a real `liquidation`
               object, but it is address-scoped with no `allFills`, so it is a
               biased SAMPLE, not a tape; it would need its own table, its own
               dedup key and its own side rule (which is inverted TWICE
               relative to Bybit's) and it is out of scope here.

The Hyperliquid feed that IS running is `engine/feeds/hyperliquid_client.py`,
and it polls large open POSITIONS with their `liq_price`. That is
forward-looking forced-flow information, not a liquidation print. It writes
`hyperliquid_positions`, never `liquidations`, and the two must not be pooled.


## SILENCE IS NOT HEALTH

Because the Binance failure was a silent zero behind a healthy-looking socket,
`_heartbeat` now warns when a feed has been CONNECTED for longer than
`SILENCE_ALERT_SEC` and has parsed zero events. A quiet venue and a dead venue
look identical for the first few minutes; after that, saying nothing would be
the bug. The warning is not proof of a fault - Bybit genuinely goes quiet - so
it is worded as an instruction to check, not as a diagnosis.


## THE SIDE SEMANTIC (read this before touching the mapping tables)

Both venues report the side of the FORCED ORDER, not the side of the position
that died. A liquidation engine closes a long by SELLING it and closes a short
by BUYING it back. So:

    order side SELL  ->  a LONG  was liquidated
    order side BUY   ->  a SHORT was liquidated

Our `side` column stores WHICH SIDE GOT LIQUIDATED ('long' / 'short'), because
that is the thing a downstream strategy actually reasons about ("longs are being
flushed"). Therefore the exchange field is INVERTED on the way in. Getting this
backwards does not raise, does not drop a row and does not move any counter -
it silently flips the sign of every downstream strategy. That is why the mapping
lives in one named table per venue and is covered by an explicit unit test in
both directions for both exchanges (`tests/test_liquidation_recorder.py`).

Binance's `!forceOrder@arr` is unambiguous: `o.S` is the side of the liquidation
order, `o.X` its order status, `o.ap` its average fill price. A forced SELL is a
long being closed.

Bybit's `allLiquidation` field `S` is treated the SAME way (inverted).

UNVERIFIED, AND STATED AS SUCH: Bybit's own documentation has historically
worded this field loosely, and the doc page could not be read from this session.
The honest cross-check is empirical - during a market-wide dump both venues must
report the SAME dominant liquidated side. If a recorded window ever shows
Binance and Bybit disagreeing on the dominant side, suspect this mapping FIRST
and flip `_BYBIT_ORDER_SIDE_TO_LIQUIDATED`, nothing else. Until enough tape
exists to run that check, the Bybit half of the inversion is an assumption with
an expiry date (convention 17), not a measurement.


## THE BYBIT TOPIC (measured, not assumed)

Bybit v5 has no wildcard liquidation topic; subscriptions are per symbol. There
are two historical forms and this module handles BOTH message shapes:

    liquidation.<SYMBOL>      legacy. `data` is a single OBJECT.
                              Measured 2026-08-18 against
                              wss://stream.bybit.com/v5/public/linear:
                              {"success":false,
                               "ret_msg":"error:handler not found,
                                          topic:liquidation.BTCUSDT"}
                              i.e. the handler is GONE on the live venue.
    allLiquidation.<SYMBOL>   current. `data` is a LIST of objects.
                              Same probe: {"success":true,"ret_msg":""}.

So `allLiquidation.<SYMBOL>` is what this recorder SUBSCRIBES to
(`BYBIT_TOPIC_PREFIX`). The legacy parse path is retained because it costs four
lines and because an archived fixture from the old shape must not become
unreadable. `--bybit-topic-prefix` exists so an operator can flip back without
an edit if Bybit reverses the deprecation.


## CONCURRENCY: another process owns this database

`db/trading.db` is written continuously by the Polymarket shadow loop. The rules
this module follows, in order of how much damage breaking them does:

  1. NEVER switch journal modes on a db another process has open. `PRAGMA
     journal_mode=WAL` on an already-WAL db is a harmless no-op, but the switch
     is a global exclusive operation and the reverse would break every
     concurrent reader. So the mode is READ (`PRAGMA journal_mode`, no `=`) and
     only SET when this process is the one that just created the file. A
     non-WAL pre-existing db is a loud warning, never a silent conversion.
  2. `PRAGMA busy_timeout=5000` so a collision waits instead of raising
     `database is locked`.
  3. Batch. One transaction per flush, not one per event.
  4. Keep the write transaction SHORT and never hold it across an `await`. This
     is structural, not a promise: every sqlite call happens on a single
     dedicated worker thread (`_writer` executor, max_workers=1) and the async
     side only ever awaits the executor future. There is no code path on which
     an open transaction and a suspended coroutine coexist.
  5. This module never writes to any table but `liquidations`, and never issues
     DELETE or UPDATE. Append-only.


## CONVENTION 20: every dropped message is counted AND categorised

There is no silent `continue` in this file. Two accounting identities hold per
exchange and are asserted on every heartbeat:

    frames_received == sum(frame_buckets.values())
    items_seen      == events_parsed + sum(event_drops.values())

and, when not in --dry-run, a third across the writer:

    events_parsed   == events_inserted + events_duplicate + events_buffered

`frame_buckets` and `event_drops` are deliberately separate namespaces: a frame
that was a subscribe ack and a frame whose three data items all had a bad price
are different failures and must never share a number. A violated identity is
logged at ERROR with both sides printed; it is never repaired by nudging a
counter to match.


## IDEMPOTENCY

`id` is a deterministic 128-bit hash of the event's own fields, so a reconnect
that replays a burst re-derives the SAME id and `INSERT OR IGNORE` drops it. A
UUID here would double-count every reconnect, and reconnects are routine (see
Binance's 24h server-side disconnect below).

The known cost, stated rather than hidden: two GENUINELY distinct liquidations
on the same venue, same symbol, same side, same price, same quantity and the
same MILLISECOND collapse into one row. On Binance that is reachable in a
cascade of identical small orders. The duplicate counter therefore has a real
floor above zero and `duplicates` in the heartbeat must not be read as "pure
reconnect replay". A venue-supplied order id would fix it; `!forceOrder@arr`
does not carry one.


## RECONNECT / KEEPALIVE

Each exchange runs in its OWN supervised task. A dead socket on one must not
take the other down, so a feed coroutine that raises is caught by its own
supervisor and only its own backoff advances. Cancellation propagates; errors
do not.

  * Binance disconnects every 24h server-side, by design. It also sends
    protocol-level ping frames; the `websockets` library answers those pongs
    itself, and `ping_interval=20` makes us ping too so a half-open socket is
    detected in seconds rather than hours.
  * Bybit drops a connection that has not sent an APPLICATION-level
    `{"op":"ping"}` within ~30s. Protocol pings do not count for it. So there is
    a dedicated 20s heartbeat task per Bybit connection.

Backoff is exponential with full jitter and a cap, and the attempt counter
resets only after a connection has stayed up longer than
`RECONNECT_RESET_SEC` - otherwise a socket that dies immediately after each
reconnect would reset the counter forever and hammer the venue at base delay.
"""
import argparse
import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import random
import signal
import sqlite3
import sys
import time

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger('liquidation_recorder')

# ---------------------------------------------------------------------------
# Endpoints and defaults
# ---------------------------------------------------------------------------

BYBIT_WS_URL = 'wss://stream.bybit.com/v5/public/linear'

#: Kept for the record and for archived-fixture parsing ONLY. Nothing connects
#: to it: 'binance' is not in SUPPORTED_EXCHANGES and the CLI refuses it.
BINANCE_WS_URL = 'wss://fstream.binance.com/ws/!forceOrder@arr'

#: The venues this recorder will actually run. One entry, on purpose.
SUPPORTED_EXCHANGES: Tuple[str, ...] = ('bybit',)

#: Venue -> why it is not in SUPPORTED_EXCHANGES. The CLI prints the reason
#: verbatim when somebody asks for one of these, so the measurement travels with
#: the refusal instead of living only in a handoff nobody rereads.
RETIRED_EXCHANGES: Dict[str, str] = {
    'binance': (
        'GEOBLOCKED from this machine (measured 2026-08-18: fapi ping -> HTTP '
        '451; !forceOrder@arr -> 0 frames in 25s). The TLS handshake still '
        'SUCCEEDS, so it logged CONNECTED and recorded nothing - a silent zero '
        'that reads as uptime. Binance.US has no futures and is not a '
        'substitute.'),
    'hyperliquid': (
        'NO PUBLIC LIQUIDATION FEED EXISTS (measured 2026-08-18, see '
        'research/hyperliquid/liquidation_source_probe.md). /info rejects every '
        'liquidation type with the SAME error as an invented type; all WS '
        'liquidation subscriptions error while trades ACKs; and the public '
        'trades payload has no liquidation flag. Recording trades here would '
        'fabricate a tape. hyperliquid_client.py polls POSITIONS into '
        'hyperliquid_positions and is a different thing.'),
}

#: Measured live on 2026-08-18: `liquidation.<SYMBOL>` returns
#: "error:handler not found"; `allLiquidation.<SYMBOL>` acks success.
BYBIT_TOPIC_PREFIX = 'allLiquidation'
BYBIT_LEGACY_TOPIC_PREFIX = 'liquidation'

#: v5 has no wildcard. These are the floor, not the ceiling - `--symbols` adds.
DEFAULT_BYBIT_SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')

DEFAULT_DB_PATH = 'db/trading.db'
DEFAULT_LOG_DIR = 'logs'
DEFAULT_STATS_INTERVAL_SEC = 60.0

#: Bybit closes a connection with no application-level ping inside ~30s.
BYBIT_PING_INTERVAL_SEC = 20.0
WS_PING_TIMEOUT_SEC = 20.0
WS_OPEN_TIMEOUT_SEC = 20.0

RECONNECT_BASE_SEC = 1.0
RECONNECT_CAP_SEC = 60.0
#: A connection must survive this long before its failure counter resets.
RECONNECT_RESET_SEC = 60.0

#: Flush when either trips. Small enough that a crash loses ~2s of tape.
DEFAULT_BATCH_SIZE = 200
DEFAULT_FLUSH_INTERVAL_SEC = 2.0

#: Warn when a feed has been CONNECTED this long having parsed zero events.
#: This is the Binance lesson: a healthy socket recording nothing looked like
#: uptime for hours. 15 minutes is long enough that an ordinarily quiet Bybit
#: window does not cry wolf, short enough that a dead feed surfaces the same
#: session it dies in. A threshold is an assumption with an expiry date
#: (convention 17) - if Bybit legitimately goes quiet for longer, raise it
#: rather than deleting the check.
SILENCE_ALERT_SEC = 900.0

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS liquidations (
    id TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    value_usd REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_liquidations_ts ON liquidations (ts);
CREATE INDEX IF NOT EXISTS idx_liquidations_symbol_ts ON liquidations (symbol, ts);
"""

# ---------------------------------------------------------------------------
# The side inversion. One table per venue, nothing else may translate a side.
# ---------------------------------------------------------------------------

SIDE_LONG = 'long'
SIDE_SHORT = 'short'

#: Binance `!forceOrder` `o.S` is the side of the LIQUIDATION ORDER.
#: A forced SELL closes (liquidates) a LONG. A forced BUY closes a SHORT.
#: INVERTED on purpose - see the module docstring.
#:
#: ARCHIVE ONLY. Binance is not in SUPPORTED_EXCHANGES and nothing connects to
#: it. This table and `parse_binance_frame` are retained for the same reason the
#: legacy Bybit topic parser is: an archived fixture must not become
#: unreadable, and deleting a pure function that costs four lines buys nothing.
_BINANCE_ORDER_SIDE_TO_LIQUIDATED = {
    'SELL': SIDE_LONG,
    'BUY': SIDE_SHORT,
}

#: Bybit v5 `allLiquidation` `S` (and legacy `liquidation` `side`) is likewise
#: the order side, so the same inversion applies. Keys are upper-cased before
#: lookup because Bybit sends 'Buy'/'Sell' and Binance sends 'BUY'/'SELL'.
_BYBIT_ORDER_SIDE_TO_LIQUIDATED = {
    'SELL': SIDE_LONG,
    'BUY': SIDE_SHORT,
}


def liquidated_side(exchange: str, order_side: Any) -> Optional[str]:
    """Translate an exchange's ORDER side into the side that was LIQUIDATED.

    Returns None for anything unrecognised so the caller can COUNT it as
    `bad_side` rather than guess. Never default to 'long': a default here is a
    silent 50% error rate on malformed input.
    """
    if not isinstance(order_side, str):
        return None
    key = order_side.strip().upper()
    if exchange == 'binance':
        return _BINANCE_ORDER_SIDE_TO_LIQUIDATED.get(key)
    if exchange == 'bybit':
        return _BYBIT_ORDER_SIDE_TO_LIQUIDATED.get(key)
    return None


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LiquidationEvent:
    """One liquidation, already normalised and already side-inverted."""
    id: str
    ts: int                 # epoch MILLISECONDS, integer
    exchange: str           # 'binance' | 'bybit'
    symbol: str             # venue-native, e.g. 'BTCUSDT'
    side: str               # 'long' | 'short' - the side that was LIQUIDATED
    price: float
    qty: float              # base units
    value_usd: float        # price * qty

    def as_row(self) -> Tuple[str, int, str, str, str, float, float, float]:
        return (self.id, self.ts, self.exchange, self.symbol, self.side,
                self.price, self.qty, self.value_usd)


def event_id(exchange: str, symbol: str, side: str, price: float,
             qty: float, ts: int) -> str:
    """Deterministic 128-bit id over the event's own fields.

    Deterministic is the whole point: a reconnect replays the same bytes, this
    re-derives the same id, and `INSERT OR IGNORE` makes the replay free. The
    canonical form uses repr() on the PARSED floats rather than the venue's raw
    strings, because '9910' and '9910.0' are the same event and must not become
    two rows.

    Truncated to 32 hex chars (128 bits). At the volumes this feed produces,
    accidental hash collision is not a real risk; the FIELD collision described
    in the module docstring is, and truncation does not affect it.
    """
    canonical = '|'.join((exchange, symbol, side, repr(float(price)),
                          repr(float(qty)), str(int(ts))))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]


def build_event(exchange: str, symbol: Any, order_side: Any, price: Any,
                qty: Any, ts: Any) -> Tuple[Optional[LiquidationEvent], Optional[str]]:
    """Normalise one raw item into a LiquidationEvent, or say why not.

    Returns `(event, None)` or `(None, drop_reason)`. Never returns
    `(None, None)` and never raises on bad input: every rejection is a NAMED
    reason so the caller can bucket it (convention 20).

    `value_usd = price * qty`. Both venues stream USDT-margined LINEAR perps on
    the endpoints used here, so quantity is in base units and price is in USDT;
    USDT is treated as USD, which is the standard approximation and is worth
    stating rather than burying. A COIN-margined (inverse) endpoint would need a
    different formula and is NOT subscribed to by this module.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        return None, 'missing_symbol'
    side = liquidated_side(exchange, order_side)
    if side is None:
        return None, 'bad_side'
    try:
        price_f = float(price)
        qty_f = float(qty)
    except (TypeError, ValueError):
        return None, 'bad_number'
    try:
        ts_i = int(float(ts))
    except (TypeError, ValueError):
        return None, 'bad_timestamp'
    # NaN/inf would serialise into the db and then poison every downstream
    # aggregate. json.loads accepts both (convention 19), so they are rejected
    # HERE. `x != x` is the NaN test that needs no import.
    if price_f != price_f or qty_f != qty_f:
        return None, 'bad_number'
    if not (price_f > 0.0) or price_f == float('inf'):
        return None, 'nonpositive_price'
    if not (qty_f > 0.0) or qty_f == float('inf'):
        return None, 'nonpositive_qty'
    if ts_i <= 0:
        return None, 'bad_timestamp'
    sym = symbol.strip().upper()
    return LiquidationEvent(
        id=event_id(exchange, sym, side, price_f, qty_f, ts_i),
        ts=ts_i, exchange=exchange, symbol=sym, side=side,
        price=price_f, qty=qty_f, value_usd=price_f * qty_f,
    ), None


# ---------------------------------------------------------------------------
# Parse results and per-feed accounting
# ---------------------------------------------------------------------------

@dataclass
class ParseResult:
    """What one websocket frame produced.

    `frame_bucket` is the SINGLE bucket this frame lands in - exactly one per
    frame, which is what makes `frames_received == sum(frame_buckets)` an
    identity rather than an approximation.

    `items_seen` counts data items the frame CLAIMED to carry, so
    `items_seen == len(events) + sum(drops)` holds inside the frame too.
    """
    frame_bucket: str
    events: List[LiquidationEvent]
    drops: Counter
    items_seen: int = 0

    @classmethod
    def bucket(cls, name: str) -> 'ParseResult':
        return cls(frame_bucket=name, events=[], drops=Counter(), items_seen=0)


class FeedStats:
    """Per-exchange counters. Two identities, asserted on every heartbeat."""

    def __init__(self, exchange: str):
        self.exchange = exchange
        self.frames_received = 0
        self.frame_buckets = Counter()      # type: Counter
        self.items_seen = 0
        self.events_parsed = 0
        self.event_drops = Counter()        # type: Counter
        self.events_inserted = 0
        self.events_duplicate = 0
        self.connects = 0
        self.reconnects = 0
        self.connected_since = None         # type: Optional[float]

    def record(self, result: ParseResult) -> None:
        self.frames_received += 1
        self.frame_buckets[result.frame_bucket] += 1
        self.items_seen += result.items_seen
        self.events_parsed += len(result.events)
        self.event_drops.update(result.drops)

    def check_identities(self, buffered: int, dry_run: bool) -> List[str]:
        """Return a list of violated identities. Empty list == healthy."""
        problems = []
        bucket_total = sum(self.frame_buckets.values())
        if bucket_total != self.frames_received:
            problems.append(
                'frames_received=%d != sum(frame_buckets)=%d'
                % (self.frames_received, bucket_total))
        item_total = self.events_parsed + sum(self.event_drops.values())
        if item_total != self.items_seen:
            problems.append(
                'items_seen=%d != events_parsed+drops=%d'
                % (self.items_seen, item_total))
        if not dry_run:
            written = self.events_inserted + self.events_duplicate + buffered
            if written != self.events_parsed:
                problems.append(
                    'events_parsed=%d != inserted+duplicate+buffered=%d'
                    % (self.events_parsed, written))
        return problems

    def summary(self) -> str:
        drops = ' '.join('%s=%d' % kv for kv in sorted(self.event_drops.items()))
        buckets = ' '.join('%s=%d' % kv for kv in sorted(self.frame_buckets.items()))
        return (
            '%s frames=%d items=%d events=%d inserted=%d dup=%d '
            'connects=%d reconnects=%d | buckets[%s] | drops[%s]'
            % (self.exchange, self.frames_received, self.items_seen,
               self.events_parsed, self.events_inserted, self.events_duplicate,
               self.connects, self.reconnects, buckets or '-', drops or '-'))


# ---------------------------------------------------------------------------
# Parsers. Pure functions over a raw frame - no sockets, no db, no clock.
# ---------------------------------------------------------------------------

def _load_frame(raw: Any) -> Tuple[Optional[Any], Optional[str]]:
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode('utf-8')
        except UnicodeDecodeError:
            return None, 'not_utf8'
    if isinstance(raw, str):
        try:
            return json.loads(raw), None
        except ValueError:
            return None, 'not_json'
    if isinstance(raw, (dict, list)):
        return raw, None
    return None, 'unexpected_shape'


def parse_binance_frame(raw: Any) -> ParseResult:
    """Parse one `!forceOrder@arr` frame.

    Accepts the bare event, the combined-stream wrapper
    (`{"stream": ..., "data": ...}`) and a list of either, because a caller may
    point this at `/stream?streams=` instead of `/ws/` and the difference must
    not surface as a parse failure.

    Field choices, stated because they are judgement calls:
      qty   `z` (accumulated FILLED quantity) when positive, else `q` (original
            order quantity). `z` is what actually got liquidated; `q` is the
            fallback for a snapshot pushed before the fill completed, and using
            `z` unconditionally would drop those as nonpositive_qty.
      price `ap` (average fill price) when positive, else `p` (order price).
            `ap` is the price the position actually died at.
    """
    payload, err = _load_frame(raw)
    if err is not None:
        return ParseResult.bucket(err)

    if isinstance(payload, dict) and 'stream' in payload and 'data' in payload:
        payload = payload['data']

    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = [payload]
    else:
        return ParseResult.bucket('unexpected_shape')

    if not entries:
        return ParseResult.bucket('empty_data')

    # A frame that is not a forceOrder event at all (a subscribe result, a
    # LIST_SUBSCRIPTIONS reply, some other stream) is its own bucket rather than
    # a parse error: "we were sent something else" and "we could not read it"
    # are different problems (convention 11).
    if all(isinstance(e, dict) and 'o' not in e and e.get('e') != 'forceOrder'
           for e in entries):
        if all(isinstance(e, dict) and ('result' in e or 'id' in e)
               for e in entries):
            return ParseResult.bucket('ack')
        return ParseResult.bucket('unknown_topic')

    result = ParseResult(frame_bucket='events', events=[], drops=Counter(),
                         items_seen=0)
    for entry in entries:
        result.items_seen += 1
        if not isinstance(entry, dict):
            result.drops['unexpected_shape'] += 1
            continue
        order = entry.get('o')
        if not isinstance(order, dict):
            result.drops['missing_order_object'] += 1
            continue

        qty_raw = order.get('z')
        try:
            use_filled = qty_raw is not None and float(qty_raw) > 0.0
        except (TypeError, ValueError):
            use_filled = False
        qty = qty_raw if use_filled else order.get('q')

        price_raw = order.get('ap')
        try:
            use_avg = price_raw is not None and float(price_raw) > 0.0
        except (TypeError, ValueError):
            use_avg = False
        price = price_raw if use_avg else order.get('p')

        ts = order.get('T', entry.get('E'))

        event, reason = build_event('binance', order.get('s'), order.get('S'),
                                    price, qty, ts)
        if event is None:
            result.drops[reason] += 1
        else:
            result.events.append(event)

    if not result.events:
        result.frame_bucket = 'no_valid_events'
    return result


def parse_bybit_frame(raw: Any) -> ParseResult:
    """Parse one Bybit v5 frame, handling BOTH liquidation topic shapes.

    `allLiquidation.<SYMBOL>` (current): `data` is a LIST of
        {"T": ms, "s": symbol, "S": order side, "v": qty, "p": price}
    `liquidation.<SYMBOL>` (legacy, handler removed from the live venue):
        `data` is a single OBJECT of
        {"updatedTime": ms, "symbol": ..., "side": ..., "size": ..., "price": ...}

    Control frames (subscribe acks, pongs) get their own buckets. A failed
    subscribe is `subscribe_error` and is NEVER pooled with a successful ack -
    "the venue refused this topic" and "the venue accepted it" demand opposite
    responses from an operator reading the log.
    """
    payload, err = _load_frame(raw)
    if err is not None:
        return ParseResult.bucket(err)
    if not isinstance(payload, dict):
        return ParseResult.bucket('unexpected_shape')

    op = payload.get('op')
    if op == 'ping' or op == 'pong' or payload.get('ret_msg') == 'pong':
        return ParseResult.bucket('pong')
    if op == 'subscribe' or 'success' in payload:
        if payload.get('success') is True:
            return ParseResult.bucket('ack')
        return ParseResult.bucket('subscribe_error')

    topic = payload.get('topic')
    if not isinstance(topic, str):
        return ParseResult.bucket('unexpected_shape')
    head = topic.split('.', 1)[0]
    if head not in (BYBIT_TOPIC_PREFIX, BYBIT_LEGACY_TOPIC_PREFIX):
        return ParseResult.bucket('unknown_topic')

    data = payload.get('data')
    if isinstance(data, dict):
        items = [data]              # legacy `liquidation` shape
    elif isinstance(data, list):
        items = data                # current `allLiquidation` shape
    else:
        return ParseResult.bucket('unexpected_shape')
    if not items:
        return ParseResult.bucket('empty_data')

    frame_ts = payload.get('ts')
    result = ParseResult(frame_bucket='events', events=[], drops=Counter(),
                         items_seen=0)
    for item in items:
        result.items_seen += 1
        if not isinstance(item, dict):
            result.drops['unexpected_shape'] += 1
            continue
        # Short keys are the allLiquidation shape, long keys the legacy one.
        symbol = item.get('s', item.get('symbol'))
        order_side = item.get('S', item.get('side'))
        qty = item.get('v', item.get('size'))
        price = item.get('p', item.get('price'))
        ts = item.get('T', item.get('updatedTime', frame_ts))
        event, reason = build_event('bybit', symbol, order_side, price, qty, ts)
        if event is None:
            result.drops[reason] += 1
        else:
            result.events.append(event)

    if not result.events:
        result.frame_bucket = 'no_valid_events'
    return result


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class LiquidationStore:
    """Append-only writer for the `liquidations` table.

    Fully synchronous and independently testable against a temp file. The
    recorder drives it from a single-worker executor so no sqlite call ever
    happens on the event loop thread and no transaction is ever open across an
    await; nothing in this class knows or cares about asyncio.
    """

    def __init__(self, db_path: str, busy_timeout_ms: int = 5000):
        self.db_path = db_path
        self.busy_timeout_ms = busy_timeout_ms
        self.conn = None                # type: Optional[sqlite3.Connection]
        self.journal_mode = None        # type: Optional[str]
        self.created_db = False

    def connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        # Whether the FILE already existed decides whether we are allowed to
        # touch the journal mode at all (see below).
        self.created_db = not os.path.exists(self.db_path)

        # check_same_thread=False: the recorder pins every call to one executor
        # thread, but the tests call this object directly from the main thread.
        # Both are single-threaded uses; the flag just stops sqlite objecting
        # that they are not the SAME single thread.
        conn = sqlite3.connect(self.db_path,
                               timeout=self.busy_timeout_ms / 1000.0,
                               check_same_thread=False)
        conn.execute('PRAGMA busy_timeout=%d;' % self.busy_timeout_ms)

        # READ the journal mode. Do NOT set it on a db that already existed:
        # another process (the shadow loop) has this file open and a journal
        # mode change is a global, exclusive operation.
        row = conn.execute('PRAGMA journal_mode;').fetchone()
        self.journal_mode = (row[0] if row else '') or ''
        if self.journal_mode.lower() != 'wal':
            if self.created_db:
                conn.execute('PRAGMA journal_mode=WAL;')
                row = conn.execute('PRAGMA journal_mode;').fetchone()
                self.journal_mode = (row[0] if row else '') or ''
                logger.info('created %s and set journal_mode=%s',
                            self.db_path, self.journal_mode)
            else:
                logger.warning(
                    'journal_mode of existing db %s is %r, not WAL. NOT '
                    'switching it - another process may have this file open '
                    'and a mode switch is exclusive. Concurrent writes will '
                    'contend; expect busy_timeout waits.',
                    self.db_path, self.journal_mode)
        self.conn = conn
        return conn

    def ensure_schema(self) -> None:
        assert self.conn is not None, 'connect() first'
        with self.conn:
            self.conn.executescript(SCHEMA_SQL)

    def insert_batch(self, events: Sequence[LiquidationEvent]) -> Tuple[int, int]:
        """Insert a batch. Returns (inserted, ignored_duplicates).

        One short transaction for the whole batch. `INSERT OR IGNORE` plus the
        deterministic id is what makes a reconnect replay a no-op instead of a
        double count, and the ignored count is RETURNED rather than discarded so
        the caller can log it (convention 20).
        """
        assert self.conn is not None, 'connect() first'
        if not events:
            return 0, 0
        rows = [e.as_row() for e in events]
        before = self.conn.total_changes
        with self.conn:
            self.conn.executemany(
                'INSERT OR IGNORE INTO liquidations '
                '(id, ts, exchange, symbol, side, price, qty, value_usd) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)', rows)
        inserted = self.conn.total_changes - before
        return inserted, len(rows) - inserted

    def count(self) -> int:
        assert self.conn is not None, 'connect() first'
        return int(self.conn.execute(
            'SELECT COUNT(*) FROM liquidations').fetchone()[0])

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None


# ---------------------------------------------------------------------------
# The recorder
# ---------------------------------------------------------------------------

def _import_connect():
    """Import the websocket client lazily.

    Kept out of module import so the parsers and the store - which is the whole
    of the test surface - stay importable on a machine without `websockets`,
    and so no test can accidentally acquire the ability to open a socket.
    """
    from websockets.asyncio.client import connect  # noqa: WPS433
    return connect


def backoff_delay(attempt: int, base: float = RECONNECT_BASE_SEC,
                  cap: float = RECONNECT_CAP_SEC,
                  rng: Optional[random.Random] = None) -> float:
    """Full-jitter exponential backoff, capped.

    Full jitter (uniform over [0, exp]) rather than exp +/- a bit: when both
    feeds die together - which is what a laptop lid closing looks like - equal
    jitter would reconnect them in lockstep forever.
    """
    rnd = rng or random
    exp = min(cap, base * (2 ** max(0, attempt - 1)))
    return rnd.uniform(0.0, exp)


class LiquidationRecorder:
    """Supervises one task per exchange and one batched writer."""

    def __init__(self, symbols: Sequence[str] = DEFAULT_BYBIT_SYMBOLS,
                 db_path: str = DEFAULT_DB_PATH,
                 stats_interval: float = DEFAULT_STATS_INTERVAL_SEC,
                 dry_run: bool = False,
                 batch_size: int = DEFAULT_BATCH_SIZE,
                 flush_interval: float = DEFAULT_FLUSH_INTERVAL_SEC,
                 bybit_topic_prefix: str = BYBIT_TOPIC_PREFIX,
                 exchanges: Sequence[str] = SUPPORTED_EXCHANGES,
                 feed_runners: Optional[Dict[str, Any]] = None):
        self.symbols = [s.strip().upper() for s in symbols if s.strip()]
        self.db_path = db_path
        self.stats_interval = stats_interval
        self.dry_run = dry_run
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.bybit_topic_prefix = bybit_topic_prefix
        self.exchanges = list(exchanges)

        # One supervised coroutine per entry. Injectable so a test can prove
        # "one feed dying does not take the other down" with two synthetic
        # feeds - that property belongs to `_supervise`, not to any venue, and
        # pinning it to a real venue is what made the old test die along with
        # Binance.
        #
        # The default value is the METHOD NAME, not the bound method, and it is
        # resolved with getattr in run(). Binding here would freeze the original
        # method at construction time and silently ignore the
        # `recorder._run_bybit_once = fake` idiom the existing tests use - the
        # test would then pass against the real socket-opening code.
        self.feed_runners = dict(feed_runners) if feed_runners else {
            'bybit': '_run_bybit_once',
        }
        unknown = [n for n in self.exchanges if n not in self.feed_runners]
        if unknown:
            raise ValueError(
                'no feed runner for %s; runnable venues are %s. %s'
                % (', '.join(unknown), ', '.join(sorted(self.feed_runners)),
                   '; '.join('%s: %s' % (n, RETIRED_EXCHANGES[n])
                             for n in unknown if n in RETIRED_EXCHANGES)))

        self.stats = {name: FeedStats(name) for name in self.exchanges}
        self._buffers = {name: [] for name in self.exchanges}

        # `_stop_flag` is the source of truth; `_stop` only WAKES a sleeper.
        #
        # The Event is deliberately NOT built here. On python 3.9 an
        # asyncio.Event binds to whatever `get_event_loop()` returns at
        # CONSTRUCTION time and raises outright when there is no current loop.
        # Building it in __init__ would make this object unconstructable outside
        # a loop and unusable from a loop created afterwards - a landmine for
        # both tests and any caller that builds the recorder before it builds
        # its loop. It is created in run(), on the loop that will wait on it.
        self._stop_flag = False
        self._stop = None               # type: Optional[asyncio.Event]
        self._loop = None               # type: Optional[asyncio.AbstractEventLoop]
        self._store = None              # type: Optional[LiquidationStore]
        self._writer = None             # type: Optional[concurrent.futures.ThreadPoolExecutor]
        self._started_at = 0.0

    # -- stop signalling ---------------------------------------------------

    @property
    def stopping(self) -> bool:
        return self._stop_flag

    def request_stop(self) -> None:
        """Ask the recorder to drain and exit. Safe from a signal handler."""
        self._stop_flag = True
        if self._stop is not None and self._loop is not None:
            # A signal handler runs on the main thread between bytecodes, but
            # call_soon_threadsafe is correct from anywhere and costs nothing.
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:
                pass        # loop already closed; the flag is enough

    async def _sleep_or_stop(self, timeout: float) -> bool:
        """Sleep up to `timeout`. Return True if a stop was requested."""
        if self._stop_flag:
            return True
        if self._stop is None:
            await asyncio.sleep(timeout)
            return self._stop_flag
        try:
            await asyncio.wait_for(self._stop.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # -- lifecycle ---------------------------------------------------------

    async def _run_in_writer(self, fn, *args):
        """Run a sqlite call on the single writer thread.

        Every db call goes through here. max_workers=1 means all of them run on
        the SAME thread, serialised, so a transaction can never be open while a
        coroutine is suspended.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._writer, fn, *args)

    async def start_store(self) -> None:
        if self.dry_run:
            logger.info('--dry-run: parsing and counting only, NOTHING is '
                        'written to %s', self.db_path)
            return
        self._writer = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='liqdb')
        self._store = LiquidationStore(self.db_path)
        await self._run_in_writer(self._store.connect)
        await self._run_in_writer(self._store.ensure_schema)
        existing = await self._run_in_writer(self._store.count)
        logger.info('db=%s journal_mode=%s existing_rows=%d',
                    self._store.db_path, self._store.journal_mode, existing)

    async def close_store(self) -> None:
        if self._store is not None:
            await self.flush_all()
            await self._run_in_writer(self._store.close)
            self._store = None
        if self._writer is not None:
            self._writer.shutdown(wait=True)
            self._writer = None

    # -- buffering / flushing ---------------------------------------------

    def ingest(self, exchange: str, result: ParseResult) -> None:
        """Account for one parsed frame and queue its events."""
        self.stats[exchange].record(result)
        if result.events:
            self._buffers[exchange].extend(result.events)

    async def flush(self, exchange: str) -> None:
        buf = self._buffers[exchange]
        if not buf:
            return
        batch = buf[:]
        del buf[:]
        st = self.stats[exchange]
        if self.dry_run or self._store is None:
            # A dry run still ACCOUNTS: `events_parsed` already counted these
            # and the identity check drops the writer leg, so the discard is
            # deliberate and visible rather than a silent loss.
            return
        inserted, ignored = await self._run_in_writer(
            self._store.insert_batch, batch)
        st.events_inserted += inserted
        st.events_duplicate += ignored
        if ignored:
            logger.info('%s: %d/%d rows already present (INSERT OR IGNORE); '
                        'reconnect replay or a same-ms field collision',
                        exchange, ignored, len(batch))

    async def flush_all(self) -> None:
        for name in self.exchanges:
            await self.flush(name)

    async def _flusher(self) -> None:
        while not self._stop_flag:
            await self._sleep_or_stop(self.flush_interval)
            await self.flush_all()

    # -- heartbeat ---------------------------------------------------------

    def heartbeat_lines(self) -> List[str]:
        uptime = time.time() - self._started_at if self._started_at else 0.0
        lines = ['heartbeat uptime=%.0fs dry_run=%s' % (uptime, self.dry_run)]
        for name in self.exchanges:
            lines.append('  ' + self.stats[name].summary())
        return lines

    def audit_identities(self, prefix: str = '') -> int:
        """Log any violated accounting identity. Returns how many there were."""
        violations = 0
        for name in self.exchanges:
            for p in self.stats[name].check_identities(
                    len(self._buffers[name]), self.dry_run):
                # Never repaired by nudging a counter. Loud, then keep going: a
                # recorder that exits on a bookkeeping bug also stops recording
                # the tape that would explain it.
                logger.error('%sACCOUNTING VIOLATION %s: %s', prefix, name, p)
                violations += 1
        return violations

    def silent_feeds(self, now: Optional[float] = None) -> List[Tuple[str, float]]:
        """Feeds CONNECTED longer than SILENCE_ALERT_SEC with zero events.

        Returns (exchange, seconds_connected). This is the Binance lesson made
        mechanical: a socket that connects and then delivers nothing is the
        failure mode that reads as uptime, and the only thing that distinguishes
        it from a quiet venue is how long it has been going on.

        It reports a SUSPICION, not a verdict - Bybit does have genuinely quiet
        stretches. The caller words it as "check", never as "dead".
        """
        clock = time.time() if now is None else now
        out = []
        for name in self.exchanges:
            st = self.stats[name]
            if st.connected_since is None or st.events_parsed:
                continue
            lived = clock - st.connected_since
            if lived >= SILENCE_ALERT_SEC:
                out.append((name, lived))
        return out

    async def _heartbeat(self) -> None:
        while not self._stop_flag:
            if await self._sleep_or_stop(self.stats_interval):
                return
            for line in self.heartbeat_lines():
                logger.info('%s', line)
            self.audit_identities()
            for name, lived in self.silent_feeds():
                logger.warning(
                    '%s: CONNECTED %.0fs and has parsed ZERO events. A healthy '
                    'socket recording nothing is exactly how the Binance '
                    'geoblock presented. Check the venue is genuinely quiet '
                    'before treating this uptime as data.', name, lived)

    # -- supervision -------------------------------------------------------

    def _resolve_runner(self, name: str):
        """Look up a feed coroutine at RUN time, not at construction time.

        A string entry is resolved with getattr, so replacing the attribute on
        the instance actually takes effect. See the note in __init__.
        """
        spec = self.feed_runners[name]
        return getattr(self, spec) if isinstance(spec, str) else spec

    async def _supervise(self, name: str, run_once) -> None:
        """Restart `run_once` forever with capped jittered backoff.

        Each exchange gets its own supervisor, so a socket that dies here
        cannot touch the other feed. `CancelledError` propagates (that is the
        operator asking to stop); everything else is caught, counted, logged
        with the attempt number and the delay, and retried.
        """
        attempt = 0
        while not self._stop_flag:
            started = time.monotonic()
            try:
                await run_once()
                reason = 'stream ended cleanly'
            except asyncio.CancelledError:
                raise
            except Exception as exc:            # noqa: BLE001 - deliberate
                reason = '%s: %s' % (type(exc).__name__, exc)
            if self._stop_flag:
                logger.info('%s: supervisor stopping (%s)', name, reason)
                return
            lived = time.monotonic() - started
            if lived >= RECONNECT_RESET_SEC:
                # Only a connection that STAYED UP resets the counter. Resetting
                # on any successful connect would let a socket that dies after
                # 200ms hammer the venue at base delay forever.
                attempt = 0
            attempt += 1
            self.stats[name].reconnects += 1
            self.stats[name].connected_since = None
            delay = backoff_delay(attempt)
            logger.warning('%s: disconnected after %.1fs (%s); reconnect '
                           'attempt %d in %.2fs', name, lived, reason,
                           attempt, delay)
            if await self._sleep_or_stop(delay):
                return

    # -- feeds -------------------------------------------------------------

    async def _bybit_heartbeat(self, ws) -> None:
        """Application-level ping. Bybit drops a silent connection in ~30s and
        does NOT count protocol-level ping frames toward that."""
        while True:
            await asyncio.sleep(BYBIT_PING_INTERVAL_SEC)
            await ws.send(json.dumps({'op': 'ping'}))

    async def _run_bybit_once(self) -> None:
        connect = _import_connect()
        st = self.stats['bybit']
        topics = ['%s.%s' % (self.bybit_topic_prefix, s) for s in self.symbols]
        logger.info('bybit: connecting %s topics=%s', BYBIT_WS_URL,
                    ','.join(topics))
        async with connect(BYBIT_WS_URL,
                           ping_interval=None,      # app-level ping instead
                           open_timeout=WS_OPEN_TIMEOUT_SEC,
                           close_timeout=5) as ws:
            st.connects += 1
            st.connected_since = time.time()
            await ws.send(json.dumps({'op': 'subscribe', 'args': topics,
                                      'req_id': 'liqrec'}))
            logger.info('bybit: CONNECTED, subscribe sent for %d topic(s)',
                        len(topics))
            hb = asyncio.ensure_future(self._bybit_heartbeat(ws))
            try:
                async for message in ws:
                    result = parse_bybit_frame(message)
                    if result.frame_bucket == 'ack':
                        logger.info('bybit: subscribe ACK %s', message)
                    elif result.frame_bucket == 'subscribe_error':
                        # Loud: this is how `liquidation.<SYM>` announced its
                        # removal ("error:handler not found"). A silently
                        # unsubscribed topic records nothing and looks healthy.
                        logger.error('bybit: SUBSCRIBE FAILED %s', message)
                    self.ingest('bybit', result)
                    await self._maybe_flush('bybit')
                    if self._stop_flag:
                        return
            finally:
                hb.cancel()

    async def _maybe_flush(self, exchange: str) -> None:
        if len(self._buffers[exchange]) >= self.batch_size:
            await self.flush(exchange)

    # -- run ---------------------------------------------------------------

    async def run(self, duration_sec: Optional[float] = None) -> int:
        self._started_at = time.time()
        self._loop = asyncio.get_event_loop()
        self._stop = asyncio.Event()
        if self._stop_flag:
            # Somebody signalled before run() got going. Honour it.
            self._stop.set()
        await self.start_store()

        tasks = []
        for name in self.exchanges:
            tasks.append(asyncio.ensure_future(
                self._supervise(name, self._resolve_runner(name))))
        tasks.append(asyncio.ensure_future(self._flusher()))
        tasks.append(asyncio.ensure_future(self._heartbeat()))

        try:
            if duration_sec is not None:
                if not await self._sleep_or_stop(duration_sec):
                    logger.info('duration %.0fs reached; stopping', duration_sec)
            else:
                await self._stop.wait()
        finally:
            self._stop_flag = True
            self._stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.close_store()

        for line in self.heartbeat_lines():
            logger.info('FINAL %s', line)
        return 1 if self.audit_identities(prefix='FINAL ') else 0

    def install_signal_handlers(self) -> None:
        def _handler(signum, _frame):
            logger.info('signal %d received; draining and stopping', signum)
            self.request_stop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                pass    # not the main thread; the caller handles shutdown


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def setup_logging(level: str, log_dir: str = DEFAULT_LOG_DIR) -> str:
    """Log to stdout AND to logs/liquidation_recorder_<date>.log."""
    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d')
    path = os.path.join(log_dir, 'liquidation_recorder_%s.log' % stamp)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)
    fh = logging.FileHandler(path)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    # websockets logs every frame at DEBUG; that is a wall of noise on a busy
    # tape and it would bury the reconnect lines this file exists to surface.
    logging.getLogger('websockets').setLevel(logging.WARNING)
    return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Record perp liquidations from Bybit (read-only public '
                    'websocket; no keys, no orders). Binance is geoblocked '
                    'and Hyperliquid has no public liquidation feed - see the '
                    'module docstring for the measurements.')
    p.add_argument('--symbols', default=','.join(DEFAULT_BYBIT_SYMBOLS),
                   help='comma-separated Bybit symbols to subscribe (v5 has '
                        'no wildcard). (default: %(default)s)')
    p.add_argument('--db', default=DEFAULT_DB_PATH,
                   help='sqlite path (default: %(default)s)')
    p.add_argument('--log-level', default='INFO',
                   choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'])
    p.add_argument('--log-dir', default=DEFAULT_LOG_DIR)
    p.add_argument('--stats-interval', type=float,
                   default=DEFAULT_STATS_INTERVAL_SEC,
                   help='seconds between heartbeat lines (default: %(default)s)')
    p.add_argument('--dry-run', action='store_true',
                   help='connect, parse and count, but write NOTHING')
    p.add_argument('--duration-sec', type=float, default=None,
                   help='stop after N seconds (smoke tests); default: run '
                        'until signalled')
    p.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE)
    p.add_argument('--flush-interval', type=float,
                   default=DEFAULT_FLUSH_INTERVAL_SEC)
    p.add_argument('--bybit-topic-prefix', default=BYBIT_TOPIC_PREFIX,
                   choices=[BYBIT_TOPIC_PREFIX, BYBIT_LEGACY_TOPIC_PREFIX],
                   help="Bybit v5 removed the legacy 'liquidation' handler "
                        "(measured 2026-08-18). Default: %(default)s")
    p.add_argument('--exchanges', default=','.join(SUPPORTED_EXCHANGES),
                   help='comma-separated subset of %s to run. Asking for a '
                        'retired venue (%s) is a hard error with the reason '
                        'printed, not a warning - a venue that records nothing '
                        'while looking connected is worse than one that '
                        'refuses to start. (default: %%(default)s)'
                        % (', '.join(SUPPORTED_EXCHANGES),
                           ', '.join(sorted(RETIRED_EXCHANGES))))
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    log_path = setup_logging(args.log_level, args.log_dir)

    symbols = [s for s in (x.strip() for x in args.symbols.split(',')) if s]
    exchanges = [s for s in (x.strip().lower()
                             for x in args.exchanges.split(',')) if s]
    for name in exchanges:
        if name in RETIRED_EXCHANGES:
            logger.error('REFUSING TO START: %r is retired. %s',
                         name, RETIRED_EXCHANGES[name])
            return 2
        if name not in SUPPORTED_EXCHANGES:
            logger.error('REFUSING TO START: unknown exchange %r. Runnable: %s',
                         name, ', '.join(SUPPORTED_EXCHANGES))
            return 2
    if not exchanges:
        logger.error('REFUSING TO START: --exchanges resolved to nothing. '
                     'A recorder with no feed would sit there logging clean '
                     'heartbeats and record zero rows forever.')
        return 2

    recorder = LiquidationRecorder(
        symbols=symbols, db_path=args.db, stats_interval=args.stats_interval,
        dry_run=args.dry_run, batch_size=args.batch_size,
        flush_interval=args.flush_interval,
        bybit_topic_prefix=args.bybit_topic_prefix, exchanges=exchanges)
    recorder.install_signal_handlers()

    logger.info('=' * 68)
    logger.info('LIQUIDATION RECORDER - read-only public market data')
    logger.info('-' * 68)
    # The feed roster, ACTIVE and DEAD together in one block. Printing only the
    # live venues is how a three-venue plan silently becomes a one-venue tape:
    # the operator reads "bybit" and has no way to tell whether the other two
    # are broken, unreachable or were never wired. So the absent ones are
    # printed WITH the measurement that retired them.
    logger.info('  FEED STATUS')
    for name in SUPPORTED_EXCHANGES:
        state = 'ACTIVE' if name in exchanges else 'available, NOT SELECTED'
        logger.info('    %-12s %s', name, state)
    for name in sorted(RETIRED_EXCHANGES):
        logger.info('    %-12s DEAD - %s', name, RETIRED_EXCHANGES[name])
    logger.info('    %-12s %s', 'NOTE',
                'a venue-wide liquidation tape exists here for Bybit ONLY. '
                'Any analysis that says "the liquidation tape" means Bybit, '
                '%d symbol(s). It is not the market.' % len(symbols))
    logger.info('-' * 68)
    logger.info('  running       : %s', ', '.join(exchanges))
    logger.info('  bybit topics  : %s.<SYMBOL> for %s',
                args.bybit_topic_prefix, ', '.join(symbols))
    logger.info('  db            : %s%s', args.db,
                ' (DRY RUN - not written)' if args.dry_run else '')
    logger.info('  log           : %s', log_path)
    logger.info('  side column   : the side that was LIQUIDATED, i.e. the '
                'exchange order side INVERTED')
    logger.info('  silence alarm : warn after %.0fs connected with 0 events',
                SILENCE_ALERT_SEC)
    logger.info('=' * 68)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(recorder.run(duration_sec=args.duration_sec))
    finally:
        loop.close()


if __name__ == '__main__':
    raise SystemExit(main())
