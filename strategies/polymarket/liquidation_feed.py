"""Read-only reader for the `liquidations` table, shared by the two liq bots.

One query, one set of skip reasons, two strategies. The alternative - each
strategy carrying its own copy of the SQL - is how `no_lead_or_atr` happened:
two symptom names for one cause, discovered a week later.

## THE SIDE SEMANTIC. Read this before using `long_usd` / `short_usd`.

`engine/feeds/liquidation_recorder.py` already inverted the exchange's order
side. The `side` column stores WHICH SIDE GOT LIQUIDATED:

    side = 'long'    longs were force-CLOSED (the engine SOLD)   -> price down
    side = 'short'   shorts were force-CLOSED (the engine BOUGHT) -> price up

So `long_usd` is money that got flushed OUT of longs, and it is BEARISH flow.
DO NOT INVERT IT AGAIN. Double-inverting raises nothing, drops nothing and
changes no count - it silently flips the sign of every signal built on top of
this module. That is why the mapping is stated once here, asserted in
`tests/test_liquidation_strategies.py` in BOTH directions, and never repeated
inside a strategy.

`dominant_side` is the liquidated side, and `continuation_outcome()` is the ONLY
place in this package that turns it into an Up/Down outcome name.

## UNITS: `ts` is MILLISECONDS

Both venues stream millisecond epochs (Binance `!forceOrder` `o.T`, Bybit
`allLiquidation` `T`), and `build_event` stores what the venue sent without
rescaling. So `ts` is ms and this module converts in exactly one place
(`MS_PER_SECOND`).

Deliberately NOT auto-detected by magnitude. Convention 14's `min_bars_for()`
lesson: unit detection by magnitude reads a synthetic series near epoch 0 as
seconds and derives a bar size 1000x wrong. If the column ever did contain
seconds, `newest_ts` would land in 1970 and the age check below would report it
as `liquidation_feed_stale` - a loud, named, wrong-looking number rather than a
silently 1000x-off window sum.

## WHY READ-ONLY IS A URI AND NOT A PROMISE

`db/trading.db` is written continuously by the shadow loop and the recorder. A
strategy has no business holding a writable handle to it, and "we only run
SELECTs" is a convention 22 claim, not a wiring test. So the connection is
opened `file:...?mode=ro`, which makes a write raise `readonly database` at the
sqlite layer instead of relying on nobody adding an INSERT later.

## THE FOUR NO-DATA REASONS ARE NOT ONE REASON (conventions 11 and 20)

    liquidation_table_missing       the recorder has never run
    liquidation_feed_empty          table exists, zero rows for our symbols
    liquidation_history_too_short   fewer than N seconds of tape recorded
    liquidation_feed_stale          newest row far older than the lookback

None of them means "we looked and there was no cascade". That is `no_cascade`,
which each strategy raises itself, and it is the only one of the five that is a
RESULT. Pooling them would put "the recorder is dead" and "the market was quiet"
in the same bucket, and those demand opposite responses from an operator.

PRECEDENCE, because a dead-after-30-seconds recorder trips two of them at once:
staleness is checked BEFORE history length. A dead recorder is the fact an
operator has to act on; the short history is a consequence of it, not a second
independent cause.
"""
import os
import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Repo root, derived from this file, so a strategy reads the same database
#: whatever directory the process was launched from. The shadow loop is started
#: by a shell script from the repo root and the recorder defaults to the
#: relative `db/trading.db`; a strategy imported from a test's tmp cwd is not,
#: and a silently-missing database would read as `liquidation_table_missing`
#: forever without anybody noticing the path was wrong.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
DEFAULT_DB_PATH = os.path.join(_REPO_ROOT, 'db', 'trading.db')

#: See the UNITS section. Conversion happens here and nowhere else.
MS_PER_SECOND = 1000

#: `symbol` holds the venue's contract name (BTCUSDT on both venues today), so
#: the asset filter is a prefix match. 'BTC%' also catches a future BTCUSD or
#: BTCUSDC listing, which is what we want: same asset, same cascade.
DEFAULT_SYMBOL_LIKE = 'BTC%'

#: Newest row older than this and the recorder is presumed dead rather than the
#: market presumed quiet. HOUSE NUMBER, not a vendor one, and it is a judgement
#: call in one direction only: too tight mislabels a genuinely quiet tape as a
#: broken feed. 900s is ~7x the 120s lookback both strategies use. BTC perp
#: liquidations print many times a minute in any normal regime, so 15 minutes of
#: total silence across two venues is a feed problem with far higher prior than
#: a market one. Convention 17: this is an assumption with an expiry date, and
#: `newest_age_sec` is stamped on EVERY row so the real distribution can be read
#: off the shadow log instead of re-guessed.
DEFAULT_STALE_AFTER_SEC = 900.0

#: Waiting beats raising when the recorder holds the write lock.
_BUSY_TIMEOUT_MS = 5000

REASON_TABLE_MISSING = 'liquidation_table_missing'
REASON_FEED_EMPTY = 'liquidation_feed_empty'
REASON_HISTORY_TOO_SHORT = 'liquidation_history_too_short'
REASON_FEED_STALE = 'liquidation_feed_stale'

#: Every no-data reason this module can produce. A strategy that adds its own
#: must not reuse one of these strings.
NO_DATA_REASONS = (REASON_TABLE_MISSING, REASON_FEED_EMPTY,
                   REASON_HISTORY_TOO_SHORT, REASON_FEED_STALE)

SIDE_LONG = 'long'
SIDE_SHORT = 'short'


# ---------------------------------------------------------------------------
# The one place a liquidated side becomes an outcome name
# ---------------------------------------------------------------------------

def continuation_outcome(liquidated_side: Optional[str]) -> Optional[str]:
    """Liquidated side -> the Polymarket outcome the cascade CONTINUES into.

    Longs liquidated means forced SELLING, so price is being pushed DOWN and the
    continuation side is 'Down'. Shorts liquidated means forced BUYING, so 'Up'.

    This is the single translation point between the recorder's vocabulary and
    Polymarket's. Both strategies call it; neither writes the mapping out.
    """
    if liquidated_side == SIDE_LONG:
        return 'Down'
    if liquidated_side == SIDE_SHORT:
        return 'Up'
    return None


# ---------------------------------------------------------------------------
# The read result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LiquidationWindow:
    """Aggregated liquidation flow over one trailing window, or why not.

    `ok=False` always carries a `reason` from `NO_DATA_REASONS`. `ok=True` means
    the read succeeded; it says NOTHING about whether a cascade was found, which
    is the strategy's own threshold question.
    """

    ok: bool
    reason: Optional[str]
    lookback_sec: float
    now_s: float

    long_usd: float = 0.0
    short_usd: float = 0.0
    long_count: int = 0
    short_count: int = 0

    #: Over the SYMBOL-FILTERED table, not just the window.
    rows_total: int = 0
    newest_ts_ms: Optional[int] = None
    oldest_ts_ms: Optional[int] = None
    history_span_sec: Optional[float] = None
    newest_age_sec: Optional[float] = None

    symbol_like: str = DEFAULT_SYMBOL_LIKE
    db_path: str = DEFAULT_DB_PATH
    #: Set when `reason == REASON_TABLE_MISSING`. Two causes, one reason, kept
    #: separable: 'no_such_file' (recorder never started) vs 'no_such_table'
    #: (database exists for other reasons, recorder never wrote). Convention 20
    #: wants them countable; the task wants one operator-facing name.
    missing_cause: Optional[str] = None

    @property
    def total_usd(self) -> float:
        return self.long_usd + self.short_usd

    @property
    def total_count(self) -> int:
        return self.long_count + self.short_count

    @property
    def dominant_side(self) -> Optional[str]:
        """Liquidated side with more USD, or None when exactly tied/empty."""
        if self.long_usd > self.short_usd:
            return SIDE_LONG
        if self.short_usd > self.long_usd:
            return SIDE_SHORT
        return None

    @property
    def dominant_usd(self) -> float:
        side = self.dominant_side
        if side == SIDE_LONG:
            return self.long_usd
        if side == SIDE_SHORT:
            return self.short_usd
        return 0.0

    @property
    def other_usd(self) -> float:
        side = self.dominant_side
        if side == SIDE_LONG:
            return self.short_usd
        if side == SIDE_SHORT:
            return self.long_usd
        return 0.0

    @property
    def dominant_count(self) -> int:
        side = self.dominant_side
        if side == SIDE_LONG:
            return self.long_count
        if side == SIDE_SHORT:
            return self.short_count
        return 0

    @property
    def dominance_ratio(self) -> Optional[float]:
        """dominant / other. None when there is no dominant side.

        `inf` is a legitimate answer (all flow one-sided, which is the shape a
        cascade actually has) and is convention 12's case, not a bug. It is
        rendered as the string 'inf' by `features()` so the row stays valid
        JSON under a strict parser (convention 19).
        """
        if self.dominant_side is None:
            return None
        if self.other_usd <= 0.0:
            return float('inf')
        return self.dominant_usd / self.other_usd

    @property
    def continuation_side(self) -> Optional[str]:
        """'Up' | 'Down' | None. See `continuation_outcome`."""
        return continuation_outcome(self.dominant_side)

    def features(self) -> dict:
        """Everything an analyst needs to re-derive the decision from the log.

        Stamped on every Decision both strategies emit, entries AND skips. A
        skip whose liquidation numbers are not recorded is a skip nobody can
        distinguish from a broken query.
        """
        ratio = self.dominance_ratio
        if ratio == float('inf'):
            # `json.dumps(allow_nan=False)` refuses Infinity, and `json.loads`
            # would happily accept it back - convention 19. A string keeps the
            # row portable and keeps the meaning.
            ratio_out = 'inf'
        elif ratio is None:
            ratio_out = None
        else:
            ratio_out = round(ratio, 3)
        return {
            'liq_feed_ok': self.ok,
            'liq_feed_live': self.ok,
            'liq_lookback_sec': self.lookback_sec,
            'liq_long_usd': round(self.long_usd, 2),
            'liq_short_usd': round(self.short_usd, 2),
            'liq_total_usd': round(self.total_usd, 2),
            'liq_long_count': self.long_count,
            'liq_short_count': self.short_count,
            'liq_count': self.total_count,
            'liq_dominant_side': self.dominant_side,
            'liq_dominant_usd': round(self.dominant_usd, 2),
            'liq_dominance_ratio': ratio_out,
            'liq_continuation_side': self.continuation_side,
            'liq_newest_age_sec': (None if self.newest_age_sec is None
                                   else round(self.newest_age_sec, 1)),
            'liq_history_span_sec': (None if self.history_span_sec is None
                                     else round(self.history_span_sec, 1)),
            'liq_rows_total': self.rows_total,
            'liq_symbol_like': self.symbol_like,
            'liq_side_already_inverted_by_recorder': True,
        }


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------

def now_from_context(ctx) -> Tuple[float, str]:
    """(epoch seconds, source) for 'now', preferring the CONTEXT's own clock.

    A decision that reads `time.time()` cannot be reproduced from a logged
    MarketContext, which is the whole reason `MarketContext` is a data bag and
    not a client handle. `window_ts + seconds_into_window` is the instant the
    context describes, so a replay of that context re-reads the same trailing
    window of tape.

    Falls back to the wall clock only when the context has no
    `seconds_into_window`, and says so in the returned source, which is stamped
    on the Decision. A row tagged `wall_clock` is not replayable and must not be
    treated as if it were.
    """
    window_ts = getattr(ctx, 'window_ts', None)
    into = getattr(ctx, 'seconds_into_window', None)
    if window_ts is not None and into is not None:
        return float(window_ts) + float(into), 'context'
    import time
    return time.time(), 'wall_clock'


# ---------------------------------------------------------------------------
# The read
# ---------------------------------------------------------------------------

def _connect_ro(db_path: str) -> sqlite3.Connection:
    """Read-only handle. Raises sqlite3.OperationalError if the file is absent.

    `mode=ro` does NOT create the file, which is the point: a typo'd path must
    surface as a missing feed, never as a fresh empty database that then reads
    as `liquidation_feed_empty` forever.
    """
    conn = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True)
    conn.execute('PRAGMA busy_timeout=%d' % _BUSY_TIMEOUT_MS)
    return conn


def read_liquidation_window(now_s: float,
                            lookback_sec: float,
                            db_path: str = DEFAULT_DB_PATH,
                            symbol_like: str = DEFAULT_SYMBOL_LIKE,
                            min_history_sec: Optional[float] = None,
                            stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
                            conn: Optional[sqlite3.Connection] = None
                            ) -> LiquidationWindow:
    """Sum liquidation USD by liquidated side over `[now - lookback, now]`.

    Never raises on a missing file, a missing table or an empty table: each is a
    NAMED reason on the returned object. A strategy that had to try/except
    around this would end up with one `except Exception` covering four causes.

    `min_history_sec` defaults to `lookback_sec`: a 30-second-old recorder
    cannot answer a question about the last 120 seconds, and summing the 30
    seconds it does have would produce a number that looks like a quiet tape and
    is actually a short one.

    `conn` is for tests and for a caller that wants to amortise the connect
    across strategies. When supplied it is NOT closed here.
    """
    if min_history_sec is None:
        min_history_sec = lookback_sec

    def fail(reason: str, **kw) -> LiquidationWindow:
        return LiquidationWindow(ok=False, reason=reason,
                                 lookback_sec=lookback_sec, now_s=now_s,
                                 symbol_like=symbol_like, db_path=db_path,
                                 **kw)

    owned = conn is None
    if owned:
        if not os.path.exists(db_path):
            # Checked explicitly rather than inferred from the sqlite error
            # text, which is not a stable API.
            return fail(REASON_TABLE_MISSING, missing_cause='no_such_file')
        try:
            conn = _connect_ro(db_path)
        except sqlite3.OperationalError:
            return fail(REASON_TABLE_MISSING, missing_cause='no_such_file')

    try:
        try:
            row = conn.execute(
                'SELECT COUNT(*), MIN(ts), MAX(ts) FROM liquidations '
                'WHERE symbol LIKE ?', (symbol_like,)).fetchone()
        except sqlite3.OperationalError:
            # "no such table: liquidations". The recorder has never run against
            # this database even though the database itself exists (the shadow
            # loop created it).
            return fail(REASON_TABLE_MISSING, missing_cause='no_such_table')

        rows_total = int(row[0] or 0)
        if rows_total == 0:
            # Scoped to `symbol_like`. A table full of ETH rows and no BTC rows
            # is, for a BTC strategy, exactly as unusable as an empty one - and
            # `liq_symbol_like` on the row says which question was asked.
            return fail(REASON_FEED_EMPTY, rows_total=0)

        oldest_ms = int(row[1])
        newest_ms = int(row[2])
        newest_age = now_s - newest_ms / MS_PER_SECOND
        span = (newest_ms - oldest_ms) / MS_PER_SECOND
        common = dict(rows_total=rows_total, newest_ts_ms=newest_ms,
                      oldest_ts_ms=oldest_ms, history_span_sec=span,
                      newest_age_sec=newest_age)

        # Staleness BEFORE history length - see the module docstring on
        # precedence. Also catches a seconds-vs-milliseconds column, which
        # lands `newest_age_sec` in the tens of millions.
        if newest_age > stale_after_sec:
            return fail(REASON_FEED_STALE, **common)

        if span < min_history_sec:
            return fail(REASON_HISTORY_TOO_SHORT, **common)

        lo_ms = int((now_s - lookback_sec) * MS_PER_SECOND)
        hi_ms = int(now_s * MS_PER_SECOND)
        agg = {SIDE_LONG: (0, 0.0), SIDE_SHORT: (0, 0.0)}
        for side, cnt, usd in conn.execute(
                'SELECT side, COUNT(*), COALESCE(SUM(value_usd), 0.0) '
                'FROM liquidations '
                'WHERE ts >= ? AND ts <= ? AND symbol LIKE ? '
                'GROUP BY side', (lo_ms, hi_ms, symbol_like)):
            if side in agg:
                agg[side] = (int(cnt), float(usd))
            # An unrecognised side is not silently pooled into either bucket.
            # `liquidated_side()` in the recorder cannot emit one, so a row here
            # would mean the table was written by something else - and guessing
            # would be the 50% silent error that module's docstring warns about.

        return LiquidationWindow(
            ok=True, reason=None, lookback_sec=lookback_sec, now_s=now_s,
            long_count=agg[SIDE_LONG][0], long_usd=agg[SIDE_LONG][1],
            short_count=agg[SIDE_SHORT][0], short_usd=agg[SIDE_SHORT][1],
            symbol_like=symbol_like, db_path=db_path, **common)
    finally:
        if owned and conn is not None:
            conn.close()
