"""What every market the loop FETCHED actually settled at.

Forge proposal 038 (`pm_settlement_resolution_ledger`), a REPAIR. It adds no
strategy, no entry, no exit and no position, and it changes no trading
behaviour. It is a write-only instrument plus one read helper.

## The defect it repairs

Nothing in this system records resolution. It is only *inferable*, from a
sibling position on the same `(pair, outcome_side)` that happened to be held to
`exit_px` 0.00 or 1.00 - because settlement is written as `exit_reason` 'stop'
at 0.00 and 'target' at 1.00 rather than as a resolution record. Measured in
`db/trading.db` at 2026-08-19 ~11:30 UTC: 2,216 closed positions touch 864
distinct market-sides and resolution is recoverable for 325 of them, 37.6%.

The inference method is SOUND - of the 17 pairs where both sides were
recovered independently, 17 of 17 show exactly one side at 1.00, which is the
arithmetic a binary must satisfy - but it is BIASED, one way and knowably. A
winning side gets sold early by `profit_target` and leaves no settlement row;
a losing side rots to 0.00 and records one. So recovery preferentially captures
losses: 28.5% of singly-recovered sides settled 1.00 against a ~50% unbiased
benchmark. Every forecast-free exit comparison in this repo is therefore
currently computed on a sample selected on the outcome it is testing.

Recording resolution for markets we did NOT trade is the whole repair. That is
why `observe()` is called from the fetch path and not from the entry path.

## Where the resolution comes from: the CLOB, not Gamma (rule 3, settled)

Proposal 038's one MISSING data requirement was whether the venue exposes a
resolution field at all, or whether resolution has to be read off a terminal
book price. It exposes one, and this repo already had a verified reader for it:
`engine/polymarket/market_resolution.py` wraps
`GET clob.polymarket.com/markets/<conditionId>`, whose body carries `closed`
plus a per-token `winner` flag, verified against live responses on 2026-08-18
(CLOB answered 8 of 8 condition ids; Gamma answered 1 of 8).

So every row this module writes live carries `source = 'venue'`.
`inferred_terminal_price` is defined here, and the schema accommodates it, but
NOTHING writes it - there is no terminal-price reader in this module, because
the venue field made one unnecessary. If one is ever added it must read the
BOOK and never Gamma's `bestBid`/`bestAsk` summary fields, which read 0.63/0.64
while the live CLOB book for the same token was 0.06/0.08 three minutes from
expiry.

`MarketResolutionCache` is also a DIFFERENT source from the one that produced
the settlement rows this ledger is checked against: the paper adapter settles
via `prices.resolution_price`, which reads Gamma's `outcomePrices` by slug. The
independence is deliberate and load-bearing. If the ledger read the same
endpoint the adapter does, the kill condition's disagreement test would agree
by construction and would be measuring nothing.

## Verdicts come from the TOKEN ID, never the outcome string

`resolution_from_clob` lowercases outcome display names into its
`winning_outcomes`/`losing_outcomes` sets, while `signals.features_json`
carries 'Up'/'Down' capitalised. Matching those two would be a case bug wearing
a join's clothes, and matching on display strings at all is how a scorer
silently inverts itself. So this module keys the ledger's `outcome_side` off
the fetched `Market`'s own `Outcome.name` string - the exact casing the rest
of the loop writes - and decides the PRICE with
`MarketResolution.verdict_for_token(token_id)`, the strong key.
`resolution_for()` then matches `outcome_side` case-insensitively on read, so a
strategy that wrote 'up' still finds a row written as 'Up'.

## Nothing is written until the answer is known

`verdict_for_token` returns None for an unresolved market AND for a token that
is not in the market at all. Both mean "cannot score", and neither is written.
A market that never resolves is ABANDONED with a named reason and counted
per window (rule 7), never recorded at 0.00. A silent gap in this table is a
missing number, and this table exists to stop exactly that.

## What must not read it

Not strategies. A resolution record exists at window CLOSE, which is after
every entry and exit decision for that window, so a strategy reading it is
look-ahead or a bug. Its only consumers are `backtest/` and
`agents/forge_shadow_eval.py`.
"""
import logging
import time
from collections import Counter, OrderedDict

from engine.polymarket.market_resolution import (
    MarketResolutionCache, STATUS_RESOLVED)

logger = logging.getLogger(__name__)

# -- `source` vocabulary. Exactly one of these is on every row. ---------------

#: Read from the venue's own resolution field (CLOB `tokens[].winner`). The
#: only value the live writer in this module ever produces.
SOURCE_VENUE = 'venue'

#: Taken from a terminal BOOK price at or after window close. Defined by rule 3
#: and accepted by the writer; nothing produces it today. See the module
#: docstring - the venue field made a price reader unnecessary.
SOURCE_INFERRED_TERMINAL_PRICE = 'inferred_terminal_price'

#: Historical rows recovered by the sibling-position inference described in the
#: module docstring. Used for NOTHING else. A backfill that cannot be
#: distinguished from an observation is not a backfill, it is contamination.
SOURCE_SIBLING_INFERENCE_BACKFILL = 'sibling_inference_backfill'

#: Sources that count toward the rule-2 coverage number in 038's kill
#: condition. Backfill is deliberately absent: coverage is measured on markets
#: FETCHED AFTER the ledger lands, and counting recovered history toward it
#: would let the repair pass on data that predates it.
LIVE_SOURCES = (SOURCE_VENUE, SOURCE_INFERRED_TERMINAL_PRICE)

BACKFILL_SOURCES = (SOURCE_SIBLING_INFERENCE_BACKFILL,)

#: Every legal value. The writer rejects anything else rather than letting a
#: typo quietly open a fourth category that no report knows to split on.
RESOLUTION_SOURCES = LIVE_SOURCES + BACKFILL_SOURCES

# -- schema ------------------------------------------------------------------

#: Also declared in `db/schema.sql`; `tests/test_schema_matches_feed_modules.py`
#: asserts the two copies agree. Two copies exist for the same reason
#: `market_tape` has two: `schema.sql` builds a FRESH database, and this one
#: lets the table bootstrap against a database that predates it.
#:
#: NO DEFAULT on any column and NO NOT NULL on `resolved_px`, deliberately and
#: by rule 1. `fill_was_maker` was added as `INTEGER NOT NULL DEFAULT 0` and
#: 2,253 pre-existing rows backfilled to 0 became indistinguishable from
#: observations; convention 32 is now only mechanically checkable after a
#: timestamp. A NULL here means NOT RECORDED and must never be read as 0.00.
#:
#: `window_ts` and `resolved_ts` are both UNIX SECONDS - `window_ts` because
#: that is what `markets.current_window_ts()` returns, and `resolved_ts` to
#: match it inside one table. They are NOT milliseconds. `signals.ts` and
#: `positions.opened_ts` in this same database ARE milliseconds; do not join
#: across them without converting.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS market_resolutions (
    market_slug  TEXT,
    outcome_side TEXT,
    resolved_px  REAL,
    resolved_ts  REAL,
    window_ts    INTEGER,
    source       TEXT,
    UNIQUE (market_slug, outcome_side)
);
CREATE INDEX IF NOT EXISTS idx_market_resolutions_window
    ON market_resolutions(window_ts);
CREATE INDEX IF NOT EXISTS idx_market_resolutions_source
    ON market_resolutions(source);
"""

# -- writer defaults ---------------------------------------------------------

#: How long after a window closes before the first resolution lookup. The chain
#: does not settle the instant the window ends, and asking immediately would
#: spend a fetch to be told `not_closed` every time.
DEFAULT_GRACE_SEC = 60.0

#: How long a market stays pending before it is ABANDONED with its last named
#: status. Not infinite: an unbounded pending set in a process that runs for
#: days is a leak, and a market still unresolved an hour after close is a
#: finding (rule 7) rather than something to keep polling forever.
DEFAULT_MAX_PENDING_SEC = 3600.0

#: Ceiling on network lookups issued by ONE sweep. A backlog after an outage
#: must not turn one cycle into fifty sequential GETs while a 5-minute window
#: is running. Deferred markets stay pending and are counted, never dropped.
DEFAULT_MAX_LOOKUPS_PER_SWEEP = 8

#: Ceiling on remembered slugs, so a process running for days does not grow a
#: set forever. ~1,150 crypto Up/Down markets a day across 3 assets and 2
#: durations, so this is roughly two weeks of history.
DEFAULT_MAX_REMEMBERED = 20000

#: Ceiling on windows kept in the unresolved-by-window report.
DEFAULT_MAX_UNRESOLVED_WINDOWS = 500


def table_exists(conn):
    """Is `market_resolutions` on disk yet?

    Asked explicitly rather than caught as an OperationalError inside the read
    helper, because "the ledger has not landed in this database" and "the
    ledger recorded nothing" are different facts. Swallowing the missing-table
    error would report the first as the second - a coverage of 0/889 that reads
    like a broken recorder instead of an absent one (convention 11: NOT_TESTED
    means "could not run", never "ran and found nothing").
    """
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='market_resolutions'").fetchone() is not None


def ensure_schema(conn):
    """Create the table if absent. Idempotent; safe on a live database.

    Purely additive - it touches no existing table, which is why it is safe to
    run while proposals 026 and 037 are mid-measurement on `market_tape`.
    """
    conn.executescript(SCHEMA_SQL)
    conn.commit()


# -- write path --------------------------------------------------------------

def write_resolutions(conn, rows, source, resolved_ts=None):
    """Insert `(market_slug, outcome_side, resolved_px, window_ts)` rows.

    Returns the number of rows actually INSERTED. `INSERT OR IGNORE`, not
    `OR REPLACE`: the UNIQUE key is `(market_slug, outcome_side)` and a market
    resolves ONCE. Re-observing it must be a no-op, and a later backfill must
    never be able to overwrite a live venue observation.

    `source` is validated against `RESOLUTION_SOURCES` and a bad one RAISES.
    An unrecognised source silently accepted would create a category no report
    knows to split on, which is the pooling failure rule 3 exists to prevent.

    `resolved_px` may be None - the column is nullable on purpose - but this
    module's own writer never passes one. NULL means NOT RECORDED, never 0.00.
    """
    if source not in RESOLUTION_SOURCES:
        raise ValueError(
            'unknown resolution source {0!r}; must be one of {1} - a new '
            'source must be registered there rather than pooled into an '
            'existing one'.format(source, list(RESOLUTION_SOURCES)))
    ts = time.time() if resolved_ts is None else float(resolved_ts)
    payload = [
        (str(slug), str(side),
         None if px is None else float(px),
         ts,
         None if window_ts is None else int(window_ts),
         source)
        for slug, side, px, window_ts in rows
    ]
    if not payload:
        return 0
    with conn:
        cur = conn.executemany(
            'INSERT OR IGNORE INTO market_resolutions '
            '(market_slug, outcome_side, resolved_px, resolved_ts, window_ts, '
            'source) VALUES (?, ?, ?, ?, ?, ?)', payload)
        return cur.rowcount if cur.rowcount is not None else 0


# -- read path (rule 5) ------------------------------------------------------

def resolution_for(conn, market_slug, outcome_side, sources=None):
    """What that market-side settled at: 1.0, 0.0, or None if UNKNOWN.

    None is the answer for an absent market, an absent side, and a row whose
    `resolved_px` is NULL. It is NEVER 0.00 for unknown: returning 0.00 would
    convert every unrecorded market into a recorded loss and would push every
    downstream number in the same direction the current sibling inference
    already leans. Every consumer must tolerate None.

    `outcome_side` matches case-insensitively - `signals.features_json` carries
    'Up'/'Down', the ledger stores whatever casing the venue used, and a join
    that fails on case looks exactly like a market that was never recorded.

    `sources` restricts the answer to those `source` values. Pass
    `LIVE_SOURCES` to exclude backfilled history (rule 4); the default is
    unrestricted, because a consumer asking "what did this settle at" wants the
    answer whatever recorded it. Use `resolution_row_for` when the source
    matters to the report, since rule 3 forbids pooling sources in one.
    """
    row = resolution_row_for(conn, market_slug, outcome_side, sources)
    return None if row is None else row['resolved_px']


def resolution_row_for(conn, market_slug, outcome_side, sources=None):
    """`resolution_for` plus the row's `source`, `resolved_ts` and `window_ts`.

    Exists so a report can SPLIT by source rather than pooling venue readings
    with backfilled inferences (rule 3, and convention 32's discipline: an
    observation and a reading have different error modes and a mixed column
    silently becomes the weaker of the two).
    """
    if not market_slug or not outcome_side:
        return None
    sql = ('SELECT market_slug, outcome_side, resolved_px, resolved_ts, '
           'window_ts, source FROM market_resolutions '
           'WHERE market_slug = ? AND LOWER(outcome_side) = LOWER(?)')
    params = [str(market_slug), str(outcome_side)]
    if sources is not None:
        listed = [str(s) for s in sources]
        if not listed:
            return None
        sql += ' AND source IN ({0})'.format(','.join('?' * len(listed)))
        params.extend(listed)
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    keys = ('market_slug', 'outcome_side', 'resolved_px', 'resolved_ts',
            'window_ts', 'source')
    # Indexed, not keyed, so this works whether or not the caller set
    # `conn.row_factory = sqlite3.Row`.
    return dict((k, row[i]) for i, k in enumerate(keys))


# -- the ledger --------------------------------------------------------------

class _Pending(object):
    """One market awaiting resolution. Fetched, not necessarily traded."""

    __slots__ = ('slug', 'condition_id', 'outcomes', 'window_ts', 'closes_at',
                 'first_seen', 'attempts', 'last_status')

    def __init__(self, slug, condition_id, outcomes, window_ts, closes_at,
                 first_seen):
        self.slug = slug
        self.condition_id = condition_id
        self.outcomes = outcomes          # ((outcome_name, token_id), ...)
        self.window_ts = window_ts
        self.closes_at = closes_at
        self.first_seen = first_seen
        self.attempts = 0
        self.last_status = None


class ResolutionLedger(object):
    """Records what every FETCHED market settled at, traded or not.

    Two calls make it work:

      - `observe(market, window_ts, duration)` from the FETCH path, once per
        market per window. Cheap, no network, idempotent per slug.
      - `sweep(now)` on a timer, well after the trading phases of a cycle. This
        is the only method that touches the network or the database.

    Both are total: neither raises out of the loop. A failure is a counted,
    NAMED category (convention 20), because a resolution recorder that can kill
    the trading loop is a worse instrument than no recorder at all.

    Bounded on purpose in three places - lookups per sweep, pending age, and
    remembered slugs - because this object lives for the life of a process that
    runs for days.
    """

    def __init__(self, conn=None, cache=None, client=None,
                 grace_sec=DEFAULT_GRACE_SEC,
                 max_pending_sec=DEFAULT_MAX_PENDING_SEC,
                 max_lookups_per_sweep=DEFAULT_MAX_LOOKUPS_PER_SWEEP,
                 max_remembered=DEFAULT_MAX_REMEMBERED,
                 max_unresolved_windows=DEFAULT_MAX_UNRESOLVED_WINDOWS,
                 clock=None):
        self.conn = conn
        self.cache = (cache if cache is not None
                      else MarketResolutionCache(client=client))
        self.grace_sec = float(grace_sec)
        self.max_pending_sec = float(max_pending_sec)
        self.max_lookups_per_sweep = max(1, int(max_lookups_per_sweep))
        self.max_remembered = max(1, int(max_remembered))
        self.max_unresolved_windows = max(1, int(max_unresolved_windows))
        self._clock = clock or time.time
        #: One counter per distinct cause; nothing shares a bucket.
        self.health = Counter()
        #: slug -> _Pending, insertion ordered so the oldest is swept first.
        self._pending = OrderedDict()
        #: Slugs already settled or abandoned, so re-fetching a market every 5
        #: seconds for five minutes is one row and not sixty INSERT attempts.
        self._recorded = OrderedDict()
        #: window_ts -> {reason: count}. Rule 7: markets fetched but NOT
        #: resolved, counted, with a reason, per window.
        self.unresolved_by_window = OrderedDict()

    # -- fetch path ---------------------------------------------------------

    def observe(self, market, window_ts, duration):
        """Register one FETCHED market. True if it is newly pending.

        Called from `build_context` rather than from the entry path, and that
        placement IS the repair - conditioning the record on holding a position
        reproduces the exact bias the record exists to remove (rule 2).

        Every refusal is named and counted. A market with no `condition_id`
        cannot be looked up on the CLOB, and saying so in a counter is the
        difference between a known gap and a silent one.
        """
        try:
            slug = getattr(market, 'slug', None)
            if not slug:
                self.health['observe_no_slug'] += 1
                return False
            slug = str(slug)
            if slug in self._recorded:
                self.health['observe_already_recorded'] += 1
                return False
            if slug in self._pending:
                self.health['observe_already_pending'] += 1
                return False
            condition_id = getattr(market, 'condition_id', None)
            if not condition_id:
                # Rule 7: a gap with a name. Nothing on the CLOB is addressable
                # without this id, so it will never resolve, and calling it
                # pending would inflate the pending count forever.
                self.health['observe_no_condition_id'] += 1
                self._count_unresolved(window_ts, 'no_condition_id')
                return False
            # `Outcome.name`, NOT `.outcome` - the field is `name` and the
            # accessor on `Market` is `outcome()`. Reading the wrong one does
            # not raise, it yields an empty tuple, and the ledger would refuse
            # every market with `no_outcomes` while reporting itself healthy.
            outcomes = tuple(
                (str(o.name), str(o.token_id))
                for o in (getattr(market, 'outcomes', None) or ())
                if getattr(o, 'name', None) and getattr(o, 'token_id', None))
            if not outcomes:
                self.health['observe_no_outcomes'] += 1
                self._count_unresolved(window_ts, 'no_outcomes')
                return False
            now = self._clock()
            self._pending[slug] = _Pending(
                slug=slug, condition_id=str(condition_id), outcomes=outcomes,
                window_ts=int(window_ts),
                closes_at=float(window_ts) + float(duration),
                first_seen=now)
            self.health['observed'] += 1
            return True
        except Exception as exc:                            # noqa: BLE001
            # A recorder must never take the trading loop with it.
            self.health['observe_raised'] += 1
            logger.warning('resolution ledger observe raised: %s: %s',
                           type(exc).__name__, exc)
            return False

    # -- sweep --------------------------------------------------------------

    def sweep(self, now=None):
        """Resolve and write every pending market whose window has closed.

        Never raises. Returns a small summary for the stats line and tests.

        Markets are taken OLDEST FIRST and the lookup budget is a hard stop:
        past it the rest stay pending and are counted as deferred, so a backlog
        after an outage cannot turn one cycle into fifty sequential GETs.
        """
        now = self._clock() if now is None else float(now)
        summary = {'due': 0, 'lookups': 0, 'written': 0, 'resolved': 0,
                   'still_pending': len(self._pending), 'abandoned': 0,
                   'deferred': 0}
        try:
            due = [p for p in self._pending.values()
                   if now >= p.closes_at + self.grace_sec]
            summary['due'] = len(due)
            for pending in due:
                if summary['lookups'] >= self.max_lookups_per_sweep:
                    summary['deferred'] += 1
                    self.health['sweep_deferred_lookup_budget'] += 1
                    continue
                summary['lookups'] += 1
                summary['written'] += self._resolve_one(pending, now, summary)
            summary['still_pending'] = len(self._pending)
        except Exception as exc:                            # noqa: BLE001
            self.health['sweep_raised'] += 1
            logger.warning('resolution ledger sweep raised: %s: %s',
                           type(exc).__name__, exc)
        return summary

    def _resolve_one(self, pending, now, summary):
        res = self.cache.get(pending.condition_id)
        pending.attempts += 1
        pending.last_status = res.status

        if not res.resolved:
            self.health['sweep_unresolved:' + res.status] += 1
            if now - pending.closes_at > self.max_pending_sec:
                self._abandon(pending, res.status, summary)
            return 0

        rows = []
        for name, token_id in pending.outcomes:
            verdict = res.verdict_for_token(token_id)
            if verdict is None:
                # A resolved market whose token we cannot place in it. Never
                # written as a price: this is a KEYING fault and retrying will
                # not fix it, so the market is abandoned under its own reason
                # rather than left to age out wearing a misleading status.
                self.health['sweep_token_not_in_resolved_market'] += 1
                self._abandon(pending, 'token_not_in_resolved_market', summary)
                return 0
            rows.append((pending.slug, name, 1.0 if verdict else 0.0,
                         pending.window_ts))

        written = 0
        if self.conn is not None:
            written = write_resolutions(self.conn, rows, SOURCE_VENUE,
                                        resolved_ts=now)
        else:
            self.health['sweep_no_conn'] += 1
        self._remember(pending.slug)
        self._pending.pop(pending.slug, None)
        summary['resolved'] += 1
        self.health['sweep_resolved'] += 1
        self.health['sweep_rows_written'] += written
        logger.info('PM RESOLUTION %s window=%s sides=%d rows=%d source=%s',
                    pending.slug, pending.window_ts, len(rows), written,
                    SOURCE_VENUE)
        return written

    def _abandon(self, pending, reason, summary):
        """Stop chasing a market, and SAY SO with a number (rule 7).

        Nothing is written for it. An abandoned market is an ABSENT row, not a
        row at 0.00 - which is the whole difference between this table and the
        `fill_was_maker` column it is written not to repeat.
        """
        self._pending.pop(pending.slug, None)
        self._remember(pending.slug)
        self._count_unresolved(pending.window_ts, reason)
        summary['abandoned'] += 1
        self.health['sweep_abandoned:' + reason] += 1
        logger.warning('PM RESOLUTION ABANDONED %s window=%s attempts=%d '
                       'reason=%s', pending.slug, pending.window_ts,
                       pending.attempts, reason)

    # -- bookkeeping --------------------------------------------------------

    def _remember(self, slug):
        self._recorded[slug] = True
        while len(self._recorded) > self.max_remembered:
            self._recorded.popitem(last=False)
            self.health['recorded_evicted'] += 1

    def _count_unresolved(self, window_ts, reason):
        key = int(window_ts) if window_ts is not None else -1
        bucket = self.unresolved_by_window.get(key)
        if bucket is None:
            bucket = Counter()
            self.unresolved_by_window[key] = bucket
        bucket[reason] += 1
        while len(self.unresolved_by_window) > self.max_unresolved_windows:
            self.unresolved_by_window.popitem(last=False)
            self.health['unresolved_windows_evicted'] += 1

    # -- reporting (rule 7) --------------------------------------------------

    def stats(self):
        """Everything this instrument knows about its own coverage.

        `unresolved_by_window` is the rule-7 number: markets FETCHED but not
        resolved, per window, split by reason. `pending_by_status` is the same
        question for markets still being chased, and the two are deliberately
        separate - "given up on" and "not answered yet" are different facts and
        pooling them would hide a venue outage inside a backlog.
        """
        pending_by_status = Counter()
        for pending in self._pending.values():
            pending_by_status[pending.last_status or 'never_attempted'] += 1
        return {
            'pending': len(self._pending),
            'pending_by_status': dict(pending_by_status),
            'recorded_slugs': len(self._recorded),
            'unresolved_by_window': dict(
                (str(window), dict(reasons))
                for window, reasons in self.unresolved_by_window.items()),
            'unresolved_total': sum(
                sum(reasons.values())
                for reasons in self.unresolved_by_window.values()),
            'cache_entries': len(self.cache),
            'health': dict(self.health),
        }


__all__ = [
    'SOURCE_VENUE', 'SOURCE_INFERRED_TERMINAL_PRICE',
    'SOURCE_SIBLING_INFERENCE_BACKFILL', 'LIVE_SOURCES', 'BACKFILL_SOURCES',
    'RESOLUTION_SOURCES', 'SCHEMA_SQL', 'table_exists', 'ensure_schema',
    'write_resolutions',
    'resolution_for', 'resolution_row_for', 'ResolutionLedger',
    'STATUS_RESOLVED',
]
