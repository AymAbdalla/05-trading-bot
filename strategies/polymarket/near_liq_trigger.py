"""Near-Liq Trigger: buy the direction a cluster of liquidation prices points.

Ported from moondevonyt's `near_liq_trigger.py`. His thresholds are preserved
where they survive; his data path does not. His key-bearing hosted API, the
wallet client and the live order path are all gone (D-267), and the whale feed
is OUR OWN `hyperliquid_positions` table, written by
`engine/feeds/hyperliquid_client.py` off Hyperliquid's public endpoints,
instead of his hosted positions snapshot.

THESIS (his, restated by Aym): a large leveraged position has a PUBLIC
liquidation price. When spot walks into a cluster of them, the exchange's
liquidation engine becomes a forced flow in a known direction, and a 5-minute
BTC Up/Down binary should move with it.

## THE DIRECTION CHAIN, written out, because getting it backwards is silent

A LONG is liquidated when price falls to its `liq_price`, so a long's
liquidation price sits BELOW spot. Liquidating a long means the engine SELLS
the long's coin into the book. Forced selling pushes price DOWN. So:

    cluster of LONG liq prices, BELOW spot   ->  forced SELLING   ->  buy Down
    cluster of SHORT liq prices, ABOVE spot  ->  forced BUYING    ->  buy Up

That mapping lives in exactly one place, `CLUSTER_SIDE_TO_OUTCOME`, and it is
pinned by a PAIR of tests (`..._long_cluster_below_spot_buys_down` and
`..._short_cluster_above_spot_buys_up`) rather than by this paragraph. An
inverted map does not raise, does not drop a row and does not change a single
counter - it just loses money on every trade it ever takes, which is why the
test is a pair and not a single case (convention 22).

## WHY "NEAR" IS IN BASIS POINTS AND NOT IN DOLLARS

moondevonyt's `NEAR_LIQ_PCT = 0.5` is a PERCENT, which is already scale-free;
his `MIN_POSITION_USD` is not, and neither would a "$500 from liquidation"
rule be. A dollar distance silently changes meaning as BTC moves: $300 away is
50 bps at $60k and 25 bps at $120k, so a threshold set in dollars quietly
doubles in strictness across a bull market and nobody edits a line of code.
Every distance in this module is therefore in BASIS POINTS off live spot
(`NEAR_BPS = 50.0`, which IS his 0.5%), and `MarketContext.lead_bps` already
establishes bps as the house unit for "distance from a reference price".

Size stays in USD because notional is what the liquidation engine actually has
to sell. There is no scale-free version of "how much forced flow is a lot".

## THE DISTANCE IS RECOMPUTED, NEVER READ

`hyperliquid_positions` stores `liq_price`, not a distance. That is deliberate
and it is the fix for the bug his README documents: his feed's own
`distance_pct` was frozen at snapshot time and reported a whale "0.01% away"
after price had already blown through him and he was gone. `liq_price` is the
only field that does not go stale between polls, so the gap is recomputed here
against the CURRENT `ctx.spot` every evaluation, and any position whose liq
price spot has already passed is dropped as `dropped_liq_price_passed` rather
than counted as maximally close.

## GROSS EDGE IN BPS (convention 5): I CANNOT ESTIMATE IT HONESTLY

Stated plainly rather than guessed at, because a fabricated number here would
be the load-bearing one. Nobody archives "who was near liquidation" - his
README says so, it is why his bot has no backtest, and it is why THIS table had
to be built before this strategy could exist. The function we would need is

    P(BTC up over the next 5 minutes | $X of forced flow within Y bps)

and it has never been measured on any data this repo holds. Zero rows exist.

What CAN be pre-registered is the arithmetic the estimate has to clear, and it
is unforgiving. A binary bought at p needs a win rate above p. The
unconditional base rate for a 5-minute BTC window is ~50%. So:

    entry 0.60  ->  needs 60%  ->  a 10-point directional shift
    entry 0.95  ->  needs 95%  ->  a 45-point shift

He caps at 0.95. We cap at 0.60 (see MAX_ENTRY_PRICE) because a 45-point shift
is not an extraordinary claim, it is an absurd one, and convention 5's 30bps
floor is not the binding constraint here - the win rate is. At a 0.50 premium,
1 point of win rate is ~200bps gross, so 30bps is 0.15 points and the floor is
cleared by any real effect at all. The question is whether the effect exists.

THE MEASUREMENT THAT WOULD REPLACE THIS PARAGRAPH: join `hyperliquid_positions`
against BTC 5m bars once ~2 weeks of the table exists, bucket by cluster USD
and distance in bps, and read the conditional up-rate off it. Until then every
number below is an assumption with an expiry date (convention 17).

## THE HONEST WEAKNESS: THIS IS A SELF-AWARE SIGNAL

A `liq_price` is not a fact about the future. It is an ESTIMATE the exchange
publishes about a position as it is margined right now, and it MOVES: add
margin and it walks away from spot, remove margin and it walks toward it,
change the position size and it jumps. Every number this strategy reads is a
snapshot of something the position's owner can change at will.

Worse, it is PUBLIC. The whale can see the same cluster we can, knows that
everyone else can see it too, and has a direct financial incentive to move his
liquidation price - or to defend it - precisely when the crowd has crowded in
behind it. "Everyone can see where the stops are" is not a hidden edge; it is a
well-known feature of a public book, and the flow it attracts can go either
way. Positions have been deliberately built to bait exactly this trade.

So: this strategy reads a signal its counterparty can move, in a game its
counterparty knows we are playing. That is not a reason to refuse to test it.
It IS a reason to refuse to explain a losing result as bad luck.

## THE SECOND LOCK IS WIRED (D-288)

His bot ARMS on the near-liq whale and then REFUSES to trade until a real
liquidation print of >= $5,000 lands on the same side within 120 seconds. His
README is emphatic about why: "close to liquidation is a CONDITION, not an
EVENT" - a whale can hover a quarter-percent from death for hours. The arm alone
is a strictly weaker and strictly more trigger-happy version of his idea.

That lock is now wired. `strategies/polymarket/liquidation_feed.py` reads the
`liquidations` table the recorder writes, and this module queries it AFTER the
cluster arms: same asset, trailing `SECOND_LOCK_WINDOW_SEC`, and at least
`SECOND_LOCK_MIN_USD` of flow on THE SAME SIDE as the cluster. A whale hovering
with no print behind him is `no_recent_liquidation`, which is a RESULT.

`features['second_lock_wired']` is now True on every row this module emits. It
is a VERSION STAMP, not a per-row verdict: it says which code produced the row,
so the arm-alone era (False) is never pooled with this one. Whether the lock
actually passed is `second_lock_ok` plus the reason.

THE SIDE CHAIN, and why it is a separate map. The cluster side is the side that
is ABOUT to be liquidated; `liquidations.side` is the side that WAS liquidated
(the recorder already inverted the exchange's order side - see that module's
docstring, and do not invert it again). Those are the same vocabulary, so:

    cluster LONG  near liq  ->  liquidations.side == 'long'   ->  outcome Down
    cluster SHORT near liq  ->  liquidations.side == 'short'  ->  outcome Up

`CLUSTER_SIDE_TO_LIQUIDATED_SIDE` holds that in one place, and the test
`test_the_two_side_maps_agree` asserts it composes with
`liquidation_feed.continuation_outcome` to give exactly
`CLUSTER_SIDE_TO_OUTCOME`. A flip in EITHER module breaks that test; a flip in
neither is unprovable by reading, which is convention 22.

DEGRADATION. The liquidation feed can be missing, empty, short or stale, and
those are the feed module's OWN four named reasons, reused verbatim rather than
renamed here (convention 20: one cause, one name, across modules). All four are
NOT_TESTED. `no_recent_liquidation` and `liquidation_below_second_lock_min` are
RESULTS, and they are two reasons rather than one because "the tape was silent"
and "the tape printed $900 and we wanted $5,000" demand different responses:
the first is a market state, the second is a threshold we chose.

Two things that used to partly stand in for the missing lock are kept, because
they gate a different thing (the cluster, not the event):
`CLUSTER_MIN_POSITIONS` (he arms on ONE position, we require a cluster) and
`CLUSTER_DOMINANCE_RATIO` (fuel on both sides is not a direction).

## THE FEED IS A WATCHLIST, NOT A CENSUS - AND IT MAY NEVER FIRE

`hyperliquid_client.py` polls the top ~25 addresses off an undocumented
leaderboard whose ranking field it has MEASURED to be stale and misleading (its
caveats 3 and 4). It observes the positions of the addresses it asked about. It
does not observe Hyperliquid, it does not observe Binance or Bybit at all, and
his `positions.json` covered exchanges this table never will.

Two consequences that must be read together:

  1. Every count below is a LOWER BOUND. `cluster_positions = 2` means "at
     least 2 of the ~25 wallets we watch", never "2 in the market".
  2. `CLUSTER_MIN_POSITIONS = 2` may therefore be unreachable in practice. Two
     of twenty-five watched wallets holding BTC longs within 50 bps of
     liquidation at the same instant is a demanding coincidence. If this
     strategy logs `no_liq_cluster_near_spot` for a month straight, the first
     hypothesis is COVERAGE, not absence of the phenomenon, and the fix is
     `--top-n`, not a lower threshold. Lowering a threshold to make a strategy
     fire is the `COST_FLOOR = -0.30` shape (convention 17).

## IT RUNS THE MOMENT DATA EXISTS, AND SKIPS HONESTLY UNTIL THEN

Aym's directive: shadow it now, do not wait for a backtest. So this module
never raises on a missing feed and never invents a signal to fill the gap. It
degrades through SEVEN separately-named feed states, because "the client has
never run" and "the poller died an hour ago" are different facts about the
system and pooling them into one counter destroys the only information a skip
carries (convention 20):

    hyperliquid_db_missing         db/trading.db is not there at all
    hyperliquid_db_unreadable      it is there and sqlite will not open it
    hyperliquid_table_missing      db opens, no hyperliquid_positions table:
                                   the client has never run
    hyperliquid_feed_empty         table exists, zero rows
    hyperliquid_feed_stale         newest row older than FEED_MAX_AGE_SEC;
                                   the age in seconds is in the reason string
    hyperliquid_single_snapshot_only  exactly one distinct snapshot timestamp;
                                   one poll is not proof the poller is cycling
    no_liq_cluster_near_spot       RAN. Found nothing. A real evaluation
                                   (convention 11).

The first six are NOT_TESTED - "could not run". The seventh is a result.

The second lock adds four more NOT_TESTED states and two more results, and they
are the LIQUIDATION feed's states, not this one's. A dead whale poller and a
dead liquidation recorder are two different outages on two different processes
and never share a counter:

    liquidation_table_missing          } the four NO_DATA_REASONS, imported
    liquidation_feed_empty             } from liquidation_feed rather than
    liquidation_history_too_short      } re-spelled here. NOT_TESTED.
    liquidation_feed_stale             }
    no_recent_liquidation              RAN. Silent tape on the armed side.
    liquidation_below_second_lock_min  RAN. Printed, under OUR floor.

## CONVENTION 8

Single taker leg on a binary. `entry` is the book-walked effective premium,
`stop` is BINARY_STOP = 0.00 (a losing share is worth exactly zero, which is
strictly below any entry we will accept) and `target` is BINARY_TARGET = 1.00.
There is no exit path: the position is held to resolution, like every strategy
in this package except the fair-value family.

KILL CONDITION: trailing-50 resolved win rate below the AVERAGE ENTRY PRICE
paid over those same 50 trades, once 50 resolved trades exist. Not a fixed
percentage, because the break-even here is whatever we paid and a 0.35 book and
a 0.58 book are different bets. Concretely, at the cap this reads as: 50
trades, trailing win rate below 60%, dead. Second clause: if 30 consecutive
days of a live poller produce fewer than 10 ENTER decisions, the strategy is
untestable on this feed and dies for lack of a sample rather than for lack of
edge - a different verdict, recorded as a different one. D-296: that second
clause's clock DOES NOT START until the `liquidations` table has at least one
row. With an empty tape it would fire in 30 days with certainty and the record
would blame the idea for a dead feed. See `kill_clock_status()` at the bottom
of this file. NAMED HARNESS for both clauses:
`backtest/polymarket_harness.py`, scoring `PM_near_liq_trigger` in its
OWN population (it holds to resolution; never pool it with the fair-value
family's sold trades).
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from strategies.polymarket.base import (BINARY_STOP, BINARY_TARGET, Decision,
                                        Leg, MarketContext, PolymarketStrategy,
                                        effective_ask_for)
from strategies.polymarket.liquidation_feed import (NO_DATA_REASONS,
                                                    REASON_TABLE_MISSING,
                                                    SIDE_LONG, SIDE_SHORT,
                                                    read_liquidation_window)

# Never False in this repo. moondevonyt ships this True->False for "LIVE FIRE";
# nothing here has live-trading authority.
PAPER_MODE = True

# ---------------------------------------------------------------------------
# THRESHOLDS. Every one of them is named, and every one says where it came
# from. Convention 17: a hardcoded threshold is an assumption with an expiry
# date, so an anonymous number is a lie about its own provenance.
#
# Provenance tags used below:
#   [VENDOR]  moondevonyt's number, preserved. UNVERIFIED - his logs, his
#             setup, and his README says no backtest of it exists.
#   [HOUSE]   ours. No measurement behind it either; the difference is that we
#             know that and say why we picked it.
#   [DERIVED] arithmetic from other constants in this block, not a new
#             assumption. Changing an input changes this one on purpose.
#   [MIRROR]  copied from another module's default. Drift risk is named.
# ---------------------------------------------------------------------------

# [VENDOR] NEAR_LIQ_PCT = 0.5 (percent), expressed in bps so the unit is the
# same one MarketContext.lead_bps uses. 0.5% == 50 bps exactly.
NEAR_BPS = 50.0

# [VENDOR] MIN_POSITION_USD = 100_000. Note this ALSO happens to be
# hyperliquid_client's DEFAULT_MIN_NOTIONAL, so at default settings the feed
# has already applied this filter and it re-applies as a no-op. It is kept
# because the feed's floor is a CLI flag on somebody else's process and this
# strategy must not inherit a silent change to it.
WHALE_MIN_USD = 100_000.0

# [HOUSE] He arms on ONE position. Aym's hypothesis is explicitly about a
# CLUSTER, and a cluster is also the only stand-in we have for his missing
# second lock: one whale hovering is a condition, several stacked in the same
# 50 bps is at least a bigger pile. 2 is the smallest number that is a cluster
# at all. See the watchlist section above for why even 2 may be unreachable.
CLUSTER_MIN_POSITIONS = 2

# [DERIVED] CLUSTER_MIN_POSITIONS * WHALE_MIN_USD. This gate CANNOT BIND at the
# current settings - any 2 positions that each cleared $100k already sum to
# $200k - and that is intentional rather than an oversight. It exists so that
# lowering WHALE_MIN_USD (or the feed's --min-notional) does not silently lower
# the cluster's notional floor along with it. Inventing an independent number
# here would be inventing a measurement.
CLUSTER_MIN_USD = CLUSTER_MIN_POSITIONS * WHALE_MIN_USD

# [HOUSE] Fuel on both sides is not a direction. The dominant side must carry
# this multiple of the other side's notional or we sit out with
# `liq_clusters_balanced`. 2.0 is arbitrary and pre-registered as arbitrary;
# it is a threshold on a quantity nobody has measured. Expiry: the first
# harness run that has enough ENTER rows to bucket by dominance ratio.
CLUSTER_DOMINANCE_RATIO = 2.0

# [HOUSE, tightened from VENDOR 0.95] The cap IS the break-even, so it is the
# strategy. See the gross-edge section: 0.95 asks a liquidation cluster to turn
# a 50/50 into a 95/5. 0.60 asks for a 10-point shift, which is merely a very
# strong claim. This is the single deviation from his numbers that changes what
# gets traded, and it is a tightening.
MAX_ENTRY_PRICE = 0.60
VENDOR_MAX_ENTRY_PRICE = 0.95   # [VENDOR] recorded so the deviation is visible

# [VENDOR] USD_SIZE = 5 flat stake, MIN_SHARES = 5 (Polymarket's order minimum).
SIZE_USD = 5.0
MIN_SHARES = 5

# [VENDOR] MIN_TIME_LEFT = 30. Do not buy a binary with 30 seconds left; there
# is no time for the forced flow the whole thesis depends on to happen.
MIN_TIME_LEFT_SEC = 30.0

# [MIRROR] engine.feeds.hyperliquid_client.DEFAULT_INTERVAL_SEC is 30.0. It is
# a CLI default on a different process, so this is a COPY and can drift; it is
# used only to express FEED_MAX_AGE_SEC as a multiple of a poll, and the age in
# seconds is reported raw on every row so a reader never has to trust it.
HYPERLIQUID_POLL_INTERVAL_SEC = 30.0

# [HOUSE] Four poll intervals. One or two missed polls is a network blip and a
# 60-second-old liq price is still a fine liq price; four consecutive misses is
# a dead poller, and a dead poller's last snapshot describes a market that has
# moved on. His equivalent (SNAPSHOT_MAX_AGE_SEC = 1800) is far looser, because
# his feed refreshed irregularly - measured 0s to 18 minutes - and ours polls
# on a fixed 30s timer, so his tolerance would let ours die unnoticed for half
# an hour.
FEED_MAX_AGE_SEC = 4 * HYPERLIQUID_POLL_INTERVAL_SEC   # 120.0

# [HOUSE] Two distinct snapshot timestamps required. One row written once and
# then a crash is INDISTINGUISHABLE from a healthy feed for as long as
# FEED_MAX_AGE_SEC, so freshness alone cannot prove the poller is cycling; two
# distinct timestamps prove at least one complete cycle happened.
#
# CAVEAT, and it is a real one: the client writes a row per QUALIFYING
# position, so a poll that found no whales writes nothing and leaves no
# timestamp. `distinct_snapshot_ts` therefore counts snapshots THAT CONTAINED A
# WHALE, not polls. It is named that way in the features for exactly this
# reason, and it under-counts a healthy but quiet poller.
MIN_SNAPSHOTS = 2

# [HOUSE] How far back to look when counting distinct snapshots. 10 minutes is
# 20 polls at the default interval, so a feed that has cycled even once in the
# last ten minutes with a whale on the books satisfies MIN_SNAPSHOTS.
SNAPSHOT_HISTORY_SEC = 600.0

# [VENDOR] COIN = "BTC". This bot never touches another coin. The table holds
# ETH and SOL too (the client's DEFAULT_SYMBOLS); they are filtered out here
# and counted, never silently dropped.
SYMBOL = 'BTC'

# --- THE SECOND LOCK (D-288) ------------------------------------------------

# [VENDOR] 120 seconds. His arm expires if no real liquidation print lands
# behind it inside this window. It is the SAME number as FEED_MAX_AGE_SEC by
# coincidence, not by derivation - they measure different things (one is how
# old a whale snapshot may be, the other is how long an arm stays valid) and
# they must be free to move independently, so they are two constants.
SECOND_LOCK_WINDOW_SEC = 120.0

# [VENDOR] MIN_LIQ_USD = 5_000. The floor that separates "a real liquidation
# happened" from "a $40 retail account got flushed". Unverified like every other
# vendor number; a print under it is `liquidation_below_second_lock_min`, which
# is countable, so the shadow log carries the distribution needed to re-set it.
SECOND_LOCK_MIN_USD = 5_000.0

# `hyperliquid_positions.symbol` holds a bare coin ('BTC'); `liquidations.symbol`
# holds a venue contract name ('BTCUSDT' on both venues today). The join is
# therefore a prefix match, the same one liquidation_feed's DEFAULT_SYMBOL_LIKE
# uses, built here from `self.symbol` so an ETH or SOL instance asks about its
# own asset rather than inheriting a hardcoded BTC.
SYMBOL_LIKE_SUFFIX = '%'

TABLE = 'hyperliquid_positions'
DEFAULT_DB_PATH = 'db/trading.db'

# ---------------------------------------------------------------------------
# THE DIRECTION MAP. One place, one time. See the module docstring.
#
#   LONG  liq price is BELOW spot -> liquidating it SELLS  -> price DOWN -> Down
#   SHORT liq price is ABOVE spot -> liquidating it BUYS   -> price UP   -> Up
# ---------------------------------------------------------------------------
CLUSTER_SIDE_TO_OUTCOME: Dict[str, str] = {'LONG': 'Down', 'SHORT': 'Up'}

# ---------------------------------------------------------------------------
# THE SECOND-LOCK SIDE MAP. Cluster side -> the `liquidations.side` value that
# CONFIRMS it. A cluster of LONGs near liquidation is confirmed by longs having
# actually been liquidated, and `liquidations.side` already means "which side
# got liquidated" (the recorder inverted the exchange's order side once, and
# liquidation_feed's docstring forbids inverting it again).
#
# The values are the feed module's own constants rather than the literals
# 'long'/'short', so a rename there is an ImportError here instead of a lookup
# that silently matches nothing and reads as a quiet tape.
# ---------------------------------------------------------------------------
CLUSTER_SIDE_TO_LIQUIDATED_SIDE: Dict[str, str] = {'LONG': SIDE_LONG,
                                                   'SHORT': SIDE_SHORT}

VALID_SIDES: Tuple[str, ...] = ('LONG', 'SHORT')

# ---------------------------------------------------------------------------
# Every skip this strategy can emit, as ONE list, so the test that asserts they
# are distinct has something to read and so a new reason cannot be added
# without appearing here. Convention 20: two drop causes never share a number.
# ---------------------------------------------------------------------------
FEED_SKIP_REASONS: Tuple[str, ...] = (
    'hyperliquid_db_missing',
    'hyperliquid_db_unreadable',
    'hyperliquid_table_missing',
    'hyperliquid_feed_empty',
    'hyperliquid_feed_stale',
    'hyperliquid_single_snapshot_only',
)

# The liquidation feed's four NOT_TESTED states, reused VERBATIM rather than
# renamed into this module's vocabulary. Two strategies already emit these exact
# strings; a third spelling of the same fact is how `no_lead_or_atr` happened.
SECOND_LOCK_FEED_SKIP_REASONS: Tuple[str, ...] = tuple(NO_DATA_REASONS)

# The two second-lock RESULTS. Both mean the query ran (convention 11).
SECOND_LOCK_RESULT_REASONS: Tuple[str, ...] = (
    'no_recent_liquidation',                # zero matching-side flow in window
    'liquidation_below_second_lock_min',    # flow existed, under the floor
)

SKIP_REASONS: Tuple[str, ...] = (FEED_SKIP_REASONS
                                 + SECOND_LOCK_FEED_SKIP_REASONS
                                 + SECOND_LOCK_RESULT_REASONS + (
    'no_spot',
    'no_liq_cluster_near_spot',
    'liq_clusters_balanced',
    'late_in_window',
    'no_market',
    'no_orderbook',
    'no_asks',
    'ask_above_cap',
    'insufficient_ask_depth',
    'unfillable_at_cap',
    'effective_ask_above_cap',
))

# Per-position drop causes inside one snapshot. Same rule: one row increments
# exactly ONE of these, and the accounting identity below is asserted.
POSITION_DROP_REASONS: Tuple[str, ...] = (
    'dropped_unusable_side',      # side is not LONG/SHORT
    'dropped_unparseable',        # a number that would not coerce
    'dropped_null_liq_price',     # liq_price IS NULL: cannot be liquidated
    'dropped_below_whale_min',    # real position, under WHALE_MIN_USD
    'dropped_liq_price_passed',   # spot is already through it; he is gone
    'dropped_not_near_spot',      # further than NEAR_BPS away
)


# ---------------------------------------------------------------------------
# Feed read. READ-ONLY, by URI, so a strategy can never write to a table it
# does not own. `file:...?mode=ro` is not a convention here, it is the only
# thing standing between an evaluate() call and somebody else's data.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FeedRead:
    """One attempt to read the whale feed. `status` is never a guess."""

    status: str                       # 'ok' or a FEED_SKIP_REASONS member
    rows: Tuple[dict, ...] = ()
    latest_ts: Optional[int] = None
    prev_ts: Optional[int] = None
    age_sec: Optional[float] = None
    distinct_snapshot_ts: int = 0
    total_rows: int = 0
    detail: str = ''


def _ro_uri(db_path: str) -> str:
    """`file:<abspath>?mode=ro`, percent-escaped.

    Escaped rather than concatenated because a path containing `?` or `#`
    would otherwise be parsed as URI query/fragment and sqlite would open
    something other than the file that was asked for.
    """
    from urllib.request import pathname2url
    return 'file:{}?mode=ro'.format(pathname2url(os.path.abspath(db_path)))


def read_feed(db_path: str, symbol: str = SYMBOL, now: Optional[float] = None,
              max_age_sec: float = FEED_MAX_AGE_SEC,
              min_snapshots: int = MIN_SNAPSHOTS,
              history_sec: float = SNAPSHOT_HISTORY_SEC) -> FeedRead:
    """Read the newest whale snapshot, or say precisely why it could not.

    Never raises on a missing file, a missing table or a database another
    process is mid-write on. Never returns an empty row tuple to mean
    "failed" - the status says which (convention 11).

    Two deliberate choices in the SQL below:

      * Freshness and snapshot-count are measured over ALL SYMBOLS, not just
        `symbol`. Staleness is a property of the POLLER. A live poller that
        currently sees no BTC whale but three ETH ones is healthy, and
        filtering by symbol first would report it as a dead feed - a false
        alarm about infrastructure dressed up as a market condition.
      * Only the row set at `latest_ts` is returned. Pooling two snapshots
        would double-count the same wallet's same position.
    """
    now = time.time() if now is None else float(now)

    if not os.path.exists(db_path):
        return FeedRead(status='hyperliquid_db_missing',
                        detail='no file at {}'.format(db_path))

    conn = None
    try:
        conn = sqlite3.connect(_ro_uri(db_path), uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row

        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE,)).fetchone()
        if exists is None:
            return FeedRead(status='hyperliquid_table_missing',
                            detail='{} has no {} table; the feed client has '
                                   'never run'.format(db_path, TABLE))

        total = conn.execute(
            'SELECT COUNT(*) FROM {}'.format(TABLE)).fetchone()[0]
        if not total:
            return FeedRead(status='hyperliquid_feed_empty', total_rows=0,
                            detail='{} exists with zero rows'.format(TABLE))

        latest_ts = conn.execute(
            'SELECT MAX(ts) FROM {}'.format(TABLE)).fetchone()[0]
        if latest_ts is None:                       # pragma: no cover
            return FeedRead(status='hyperliquid_feed_empty', total_rows=total,
                            detail='COUNT>0 but MAX(ts) is NULL')
        latest_ts = int(latest_ts)
        age = now - latest_ts

        if age > max_age_sec:
            return FeedRead(status='hyperliquid_feed_stale',
                            latest_ts=latest_ts, age_sec=age, total_rows=total,
                            detail='newest row is {:.0f}s old (max {:.0f}s); '
                                   'the poller is probably dead'
                                   .format(age, max_age_sec))

        stamps = [int(r[0]) for r in conn.execute(
            'SELECT DISTINCT ts FROM {} WHERE ts >= ? ORDER BY ts DESC'
            .format(TABLE), (int(now - history_sec),)).fetchall()]
        prev_ts = stamps[1] if len(stamps) > 1 else None

        if len(stamps) < min_snapshots:
            return FeedRead(status='hyperliquid_single_snapshot_only',
                            latest_ts=latest_ts, age_sec=age,
                            distinct_snapshot_ts=len(stamps),
                            total_rows=total,
                            detail='{} distinct snapshot ts in the last {:.0f}s '
                                   '(need {}); one poll does not prove the '
                                   'poller is cycling'
                                   .format(len(stamps), history_sec,
                                           min_snapshots))

        rows = [dict(r) for r in conn.execute(
            'SELECT ts, wallet, symbol, side, size_usd, entry_price, '
            'liq_price, leverage FROM {} WHERE ts = ? AND symbol = ?'
            .format(TABLE), (latest_ts, symbol)).fetchall()]

        return FeedRead(status='ok', rows=tuple(rows), latest_ts=latest_ts,
                        prev_ts=prev_ts, age_sec=age,
                        distinct_snapshot_ts=len(stamps), total_rows=total)

    except sqlite3.OperationalError as exc:
        # Includes "unable to open database file" (a path we cannot read even
        # though os.path.exists said otherwise) and "database is locked" past
        # the busy timeout. Both are "could not run", never "no whales".
        return FeedRead(status='hyperliquid_db_unreadable',
                        detail='{}: {}'.format(type(exc).__name__, exc))
    except sqlite3.DatabaseError as exc:
        # e.g. the file exists but is not a sqlite database.
        return FeedRead(status='hyperliquid_db_unreadable',
                        detail='{}: {}'.format(type(exc).__name__, exc))
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:       # pragma: no cover
                pass


# ---------------------------------------------------------------------------
# Cluster maths
# ---------------------------------------------------------------------------
def liq_distance_bps(side: str, spot: float,
                     liq_price: Optional[float]) -> Optional[float]:
    """Signed distance from spot to a liquidation price, in basis points.

    POSITIVE means spot has not reached it yet: a LONG whose liq sits below,
    or a SHORT whose liq sits above. ZERO OR NEGATIVE means price has already
    traded through it and the position is gone - the caller drops those rather
    than treating "0 bps away" as maximally imminent, which is the exact bug
    his README documents.
    """
    if spot is None or spot <= 0 or liq_price is None or liq_price <= 0:
        return None
    if side == 'LONG':
        return (spot - liq_price) / spot * 10_000.0
    if side == 'SHORT':
        return (liq_price - spot) / spot * 10_000.0
    return None


@dataclass(frozen=True)
class LiqCluster:
    """The near-liquidation positions on ONE side of one snapshot."""

    side: str                 # 'LONG' | 'SHORT'
    positions: int
    usd: float
    nearest_bps: float
    mean_bps: float

    @property
    def outcome_side(self) -> str:
        return CLUSTER_SIDE_TO_OUTCOME[self.side]


def _as_float(value) -> Optional[float]:
    """Finite float or None. Bools are not numbers here."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float('inf'), float('-inf')):
        return None
    return out


def build_clusters(rows: Sequence[dict], spot: float,
                   near_bps: float = NEAR_BPS,
                   whale_min_usd: float = WHALE_MIN_USD
                   ) -> Tuple[Dict[str, LiqCluster], Dict[str, int]]:
    """Group one snapshot's near-liq positions by side.

    Returns (clusters_by_side, counts). `counts` carries 'positions_considered',
    'positions_near' and every POSITION_DROP_REASONS key, and satisfies

        positions_considered - sum(dropped_*) == positions_near

    by construction: every branch below either appends the position to a side
    or increments exactly one drop counter. There is no bare `continue`
    (convention 20). The caller asserts the identity anyway.
    """
    counts: Dict[str, int] = {'positions_considered': 0, 'positions_near': 0}
    for reason in POSITION_DROP_REASONS:
        counts[reason] = 0

    near: Dict[str, List[Tuple[float, float]]] = {'LONG': [], 'SHORT': []}

    for row in rows:
        counts['positions_considered'] += 1

        side = str(row.get('side') or '').strip().upper()
        if side not in VALID_SIDES:
            counts['dropped_unusable_side'] += 1
            continue

        size_usd = _as_float(row.get('size_usd'))
        if size_usd is None:
            counts['dropped_unparseable'] += 1
            continue

        raw_liq = row.get('liq_price')
        if raw_liq is None:
            # NULL is DATA, not a defect: the position cannot be liquidated.
            # It is the one position that will never be forced flow.
            counts['dropped_null_liq_price'] += 1
            continue
        liq_price = _as_float(raw_liq)
        if liq_price is None:
            counts['dropped_unparseable'] += 1
            continue

        if size_usd < whale_min_usd:
            counts['dropped_below_whale_min'] += 1
            continue

        dist = liq_distance_bps(side, spot, liq_price)
        if dist is None or dist <= 0.0:
            counts['dropped_liq_price_passed'] += 1
            continue
        if dist > near_bps:
            counts['dropped_not_near_spot'] += 1
            continue

        near[side].append((dist, size_usd))
        counts['positions_near'] += 1

    clusters: Dict[str, LiqCluster] = {}
    for side, members in near.items():
        if not members:
            continue
        total = sum(usd for _, usd in members)
        clusters[side] = LiqCluster(
            side=side,
            positions=len(members),
            usd=total,
            nearest_bps=min(d for d, _ in members),
            mean_bps=sum(d for d, _ in members) / len(members),
        )
    return clusters, counts


def assert_position_accounting(counts: Dict[str, int]) -> None:
    """considered - sum(dropped_*) == near, or raise (convention 20).

    Never 'repaired' by adjusting a counter to match.
    """
    dropped = sum(v for k, v in counts.items() if k.startswith('dropped_'))
    assert counts['positions_considered'] - dropped == counts['positions_near'], (
        'near_liq_trigger position accounting identity broken: {}'.format(counts))


def shares_for(size_usd: float, price: float,
               min_shares: int = MIN_SHARES) -> float:
    """His sizing: floor(stake / price), never below the exchange minimum."""
    if price <= 0:
        return float(min_shares)
    return float(max(min_shares, math.floor(size_usd / price)))


# ---------------------------------------------------------------------------
# The strategy
# ---------------------------------------------------------------------------
class NearLiqTrigger(PolymarketStrategy):
    """Buy the side a dominant cluster of nearby liquidation prices points at."""

    strategy_name = 'PM_near_liq_trigger'
    paper_mode = PAPER_MODE

    def __init__(self, db_path: Optional[str] = None,
                 symbol: str = SYMBOL,
                 near_bps: float = NEAR_BPS,
                 whale_min_usd: float = WHALE_MIN_USD,
                 cluster_min_positions: int = CLUSTER_MIN_POSITIONS,
                 cluster_min_usd: float = CLUSTER_MIN_USD,
                 dominance_ratio: float = CLUSTER_DOMINANCE_RATIO,
                 max_entry_price: float = MAX_ENTRY_PRICE,
                 size_usd: float = SIZE_USD,
                 min_shares: int = MIN_SHARES,
                 min_time_left_sec: float = MIN_TIME_LEFT_SEC,
                 feed_max_age_sec: float = FEED_MAX_AGE_SEC,
                 min_snapshots: int = MIN_SNAPSHOTS,
                 snapshot_history_sec: float = SNAPSHOT_HISTORY_SEC,
                 second_lock_window_sec: float = SECOND_LOCK_WINDOW_SEC,
                 second_lock_min_usd: float = SECOND_LOCK_MIN_USD,
                 liq_db_path: Optional[str] = None,
                 now_fn=None):
        self.db_path = db_path
        self.symbol = symbol
        self.near_bps = near_bps
        self.whale_min_usd = whale_min_usd
        self.cluster_min_positions = cluster_min_positions
        self.cluster_min_usd = cluster_min_usd
        self.dominance_ratio = dominance_ratio
        self.max_entry_price = max_entry_price
        self.size_usd = size_usd
        self.min_shares = min_shares
        self.min_time_left_sec = min_time_left_sec
        self.feed_max_age_sec = feed_max_age_sec
        self.min_snapshots = min_snapshots
        self.snapshot_history_sec = snapshot_history_sec
        self.second_lock_window_sec = second_lock_window_sec
        self.second_lock_min_usd = second_lock_min_usd
        # Both tables live in `db/trading.db` today, so this defaults to the
        # SAME resolved path as the whale feed. It is a separate knob only so a
        # test (or a future split database) can point them apart; leaving it
        # None keeps the two in lockstep, which is what we want in production.
        self.liq_db_path = liq_db_path
        # Injectable so tests are deterministic and fully offline. NOT derived
        # from ctx.window_ts: a context built by `context_from_candles` carries
        # a window_ts from whenever the last bar was, and using that as a clock
        # would make a month-old backtest context read as "the feed is fresh".
        self._now_fn = now_fn or time.time

    # -- db path ------------------------------------------------------------
    def resolve_db_path(self) -> str:
        """Explicit path wins; otherwise the engine's, otherwise the default.

        `engine.db` is imported lazily so this module stays importable without
        the engine package, the same reason `effective_ask_for` defers its
        import of the orderbook walker.
        """
        if self.db_path:
            return self.db_path
        try:
            from engine.db import get_db_path
            return get_db_path()
        except Exception:               # pragma: no cover - defensive
            return DEFAULT_DB_PATH

    def resolve_liq_db_path(self) -> str:
        """Where the `liquidations` table lives. Same file as the whale feed."""
        return self.liq_db_path or self.resolve_db_path()

    def symbol_like(self) -> str:
        """`'BTC'` -> `'BTC%'`. See SYMBOL_LIKE_SUFFIX."""
        return str(self.symbol).upper() + SYMBOL_LIKE_SUFFIX

    # -- evaluation ---------------------------------------------------------
    def evaluate(self, ctx: MarketContext) -> Decision:
        slug = getattr(ctx.market, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            # Stamped on EVERY row, skips included, so nothing downstream can
            # read a threshold off a log and mistake it for a measurement, and
            # so the pre-second-lock era is never pooled with a later one.
            feats.setdefault('thresholds_are_unverified_vendor_numbers', True)
            feats.setdefault('vendor_claims_no_backtest_exists', True)
            # A VERSION STAMP, not a verdict. True on every row this code
            # emits, including rows that skip long before the lock is reachable,
            # because it answers "which strategy produced this?" - and the
            # arm-alone era (False) must never be pooled with this one (D-288).
            # Whether the lock PASSED is `second_lock_ok`.
            feats.setdefault('second_lock_wired', True)
            feats.setdefault('feed_is_watchlist_not_census', True)
            feats.setdefault('counts_are_lower_bounds', True)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        # -- spot. Free, and nothing downstream means anything without it: a
        # liq price is only a distance relative to where price is now.
        spot = _as_float(ctx.spot)
        if spot is None or spot <= 0:
            return decide('SKIP', 'no_spot', spot=ctx.spot)

        # -- the feed -------------------------------------------------------
        db_path = self.resolve_db_path()
        feed = read_feed(db_path, symbol=self.symbol, now=self._now_fn(),
                         max_age_sec=self.feed_max_age_sec,
                         min_snapshots=self.min_snapshots,
                         history_sec=self.snapshot_history_sec)

        feed_feats = {
            'feed_status': feed.status,
            'feed_live': feed.status == 'ok',
            'feed_age_sec': (None if feed.age_sec is None
                             else round(feed.age_sec, 1)),
            'feed_latest_ts': feed.latest_ts,
            'feed_prev_snapshot_ts': feed.prev_ts,
            'feed_snapshot_gap_sec': (None if (feed.latest_ts is None
                                               or feed.prev_ts is None)
                                      else feed.latest_ts - feed.prev_ts),
            'feed_distinct_snapshot_ts': feed.distinct_snapshot_ts,
            'feed_total_rows': feed.total_rows,
            'feed_db_path': db_path,
            'feed_detail': feed.detail,
            'spot': spot,
        }

        if feed.status != 'ok':
            # Six distinct NOT_TESTED states. The age is inside `feed_detail`
            # AND in `feed_age_sec`, so a stale feed is never a bare word.
            return decide('SKIP', feed.status, **feed_feats)

        # -- the snapshot ---------------------------------------------------
        clusters, counts = build_clusters(
            feed.rows, spot, near_bps=self.near_bps,
            whale_min_usd=self.whale_min_usd)
        assert_position_accounting(counts)

        feats = dict(feed_feats)
        feats.update(counts)
        feats['positions_symbol_match'] = len(feed.rows)
        feats['near_bps_threshold'] = self.near_bps
        for side in VALID_SIDES:
            c = clusters.get(side)
            feats['{}_cluster_positions'.format(side.lower())] = (
                0 if c is None else c.positions)
            feats['{}_cluster_usd'.format(side.lower())] = (
                0.0 if c is None else round(c.usd, 2))
            feats['{}_cluster_nearest_bps'.format(side.lower())] = (
                None if c is None else round(c.nearest_bps, 2))

        qualified = [c for c in clusters.values()
                     if c.positions >= self.cluster_min_positions
                     and c.usd >= self.cluster_min_usd]
        feats['qualified_cluster_sides'] = sorted(c.side for c in qualified)

        if not qualified:
            # RAN AND FOUND NOTHING: a result, not a "could not run"
            # (convention 11). The arm did not form, so the second lock is not
            # reached and no `second_lock_ok` is stamped - absent, not False,
            # because we never asked the question.
            return decide('SKIP', 'no_liq_cluster_near_spot', **feats)

        qualified.sort(key=lambda c: c.usd, reverse=True)
        winner = qualified[0]
        rival = clusters.get('SHORT' if winner.side == 'LONG' else 'LONG')
        rival_usd = 0.0 if rival is None else rival.usd
        # Guard the division: a zero-notional rival is not "infinitely
        # dominated", it is simply absent, and inf in a feature dict is a
        # convention 19 hazard the moment somebody json-dumps this row.
        dominance = (None if rival_usd <= 0.0 else winner.usd / rival_usd)
        feats['rival_cluster_usd'] = round(rival_usd, 2)
        feats['cluster_dominance'] = (None if dominance is None
                                      else round(dominance, 3))

        if dominance is not None and dominance < self.dominance_ratio:
            # Fuel on both sides. The forced flow cancels and the direction is
            # a coin flip wearing a signal's clothes.
            return decide('SKIP', 'liq_clusters_balanced', **feats)

        outcome_side = winner.outcome_side   # the ONE direction mapping
        feats.update({
            'cluster_side': winner.side,
            'cluster_positions': winner.positions,
            'cluster_usd': round(winner.usd, 2),
            'cluster_nearest_bps': round(winner.nearest_bps, 2),
            'cluster_mean_bps': round(winner.mean_bps, 2),
            'outcome_side': outcome_side,
            'direction_rationale': (
                'LONG liqs sit below spot; liquidating them SELLS, price down'
                if winner.side == 'LONG' else
                'SHORT liqs sit above spot; liquidating them BUYS, price up'),
            # The base rate for a 5m BTC window, NOT a measured probability.
            # Written down so the scanner has a number and so nobody later
            # mistakes it for an edge estimate.
            'confidence': 0.5,
            'confidence_is_base_rate_not_a_measurement': True,
            'binary_stop': BINARY_STOP,
            'binary_target': BINARY_TARGET,
        })

        # -- THE SECOND LOCK (D-288) ----------------------------------------
        #
        # Placed HERE, after the arm and before the timing and book gates,
        # because it completes the SIGNAL and those two gate EXECUTION. Order
        # is not cosmetic: put it last and a quiet tape in a late window would
        # log `late_in_window`, which reads as "we had a signal and missed it".
        # We did not have a signal. The kill condition counts ENTER decisions,
        # so overstating how often one existed is the expensive mistake.
        #
        # The clock is `self._now_fn()`, NOT `liquidation_feed.now_from_context`.
        # The whale feed's freshness is already measured against this clock, and
        # two clocks would let a snapshot be "fresh" and a liquidation window be
        # "stale" at the same instant for no reason but the source of `now`.
        # See the __init__ note on why ctx.window_ts is not a clock here.
        now_s = float(self._now_fn())
        liq_db = self.resolve_liq_db_path()
        wanted_side = CLUSTER_SIDE_TO_LIQUIDATED_SIDE[winner.side]
        window = read_liquidation_window(
            now_s=now_s, lookback_sec=self.second_lock_window_sec,
            db_path=liq_db, symbol_like=self.symbol_like())

        feats.update(window.features())
        # D-296. Derived from the window we ALREADY read, so the guard costs no
        # extra query. It rides on every row from here down, entries included,
        # because "the clock was not running when this row was written" is a
        # property of the row and not of whoever scores it later.
        feats.update(kill_clock_row_features(window))
        feats.update({
            'second_lock_window_sec': self.second_lock_window_sec,
            'second_lock_min_usd': self.second_lock_min_usd,
            'second_lock_wanted_side': wanted_side,
            'second_lock_db_path': liq_db,
            'second_lock_now_source': 'strategy_now_fn',
        })

        if not window.ok:
            # The feed module's own four NOT_TESTED names, verbatim. "The
            # recorder is dead" is not "the market was quiet" (convention 11).
            feats['second_lock_ok'] = False
            return decide('SKIP', window.reason, **feats)

        matched_usd = (window.long_usd if wanted_side == SIDE_LONG
                       else window.short_usd)
        matched_count = (window.long_count if wanted_side == SIDE_LONG
                         else window.short_count)
        feats['second_lock_matched_usd'] = round(matched_usd, 2)
        feats['second_lock_matched_count'] = matched_count

        if matched_count == 0:
            # RAN. The tape was silent on this side. A market state.
            feats['second_lock_ok'] = False
            return decide('SKIP', 'no_recent_liquidation', **feats)
        if matched_usd < self.second_lock_min_usd:
            # RAN. The tape printed, but under a floor WE chose. Separate from
            # the above because only this one moves when we change our mind.
            feats['second_lock_ok'] = False
            return decide('SKIP', 'liquidation_below_second_lock_min', **feats)

        # The condition became an event.
        feats['second_lock_ok'] = True

        # -- timing ---------------------------------------------------------
        remaining = ctx.seconds_remaining
        feats['seconds_remaining'] = remaining
        if remaining is not None and remaining < self.min_time_left_sec:
            # No time left for the cascade the whole thesis depends on.
            return decide('SKIP', 'late_in_window', **feats)

        # -- book -----------------------------------------------------------
        if ctx.market is None:
            return decide('SKIP', 'no_market', **feats)

        book = ctx.book(outcome_side)
        if book is None:
            return decide('SKIP', 'no_orderbook', **feats)

        best_ask = book.best_ask
        feats['best_ask'] = best_ask
        if best_ask is None:
            return decide('SKIP', 'no_asks', **feats)

        if best_ask > self.max_entry_price:
            # The cap is the break-even. Above it the trade needs a win rate
            # no liquidation signal has ever been shown to produce.
            feats['vendor_max_entry_price'] = VENDOR_MAX_ENTRY_PRICE
            return decide('SKIP', 'ask_above_cap', **feats)

        shares = shares_for(self.size_usd, self.max_entry_price,
                            self.min_shares)
        depth = book.ask_depth(self.max_entry_price)
        feats['intended_shares'] = shares
        feats['ask_depth_at_cap'] = depth
        if depth < shares:
            return decide('SKIP', 'insufficient_ask_depth', **feats)

        effective = effective_ask_for(book, shares, self.max_entry_price)
        feats['effective_ask'] = (None if effective is None
                                  else round(effective, 4))
        if effective is None:
            return decide('SKIP', 'unfillable_at_cap', **feats)
        if effective > self.max_entry_price:
            # walk_book cannot return this given the same limit, but the cap is
            # the whole break-even and a silent regression here is invisible.
            return decide('SKIP', 'effective_ask_above_cap', **feats)

        feats['limit_price'] = self.max_entry_price
        # On a binary held to resolution the premium IS the break-even win
        # rate. Stated per row so the kill condition can be evaluated against
        # what was actually paid rather than against the cap.
        feats['breakeven_win_rate'] = round(effective, 4)

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=outcome_side,
                                limit_price=self.max_entry_price,
                                order_type='taker',
                                shares=shares,
                                expected_price=effective)],
                      **feats)


# ---------------------------------------------------------------------------
# THE KILL CLOCK (D-296)
#
# The module docstring's SECOND kill clause reads: "if 30 consecutive days of a
# live poller produce fewer than 10 ENTER decisions, the strategy is untestable
# on this feed and dies for lack of a sample rather than for lack of edge."
#
# On 2026-08-18 `select count(*) from liquidations` is ZERO. Binance is
# geoblocked (HTTP 451) and Bybit's tape has been quiet, so the second lock has
# never had an input. Every evaluation stops at `liquidation_feed_empty` long
# before an ENTER is reachable. Start the clock against that and in 30 days it
# fires with certainty, and the record would read "PM_near_liq_trigger: killed,
# under 10 entries in 30 days" - a verdict about the IDEA, produced entirely by
# a dead feed. That is convention 11 at the level of a kill condition.
#
# So the clock does not start on the day the strategy was deployed. It starts
# on the day the tape first printed ANYTHING. Zero rows is not "day 0 of 30",
# it is "the clock has not been started", and those are different states with
# different names.
#
# The FIRST clause (trailing-50 resolved win rate vs average entry price) is
# untouched by this. It is gated on 50 RESOLVED TRADES existing, which is
# already self-deferring: no feed means no entries means no resolutions means
# that clause cannot fire either. Only the calendar clause needed a guard,
# because a calendar runs whether or not anything happens.
# ---------------------------------------------------------------------------

#: The second clause's two numbers, named rather than inline so the guard and
#: the docstring cannot drift apart silently (convention 17: both have an
#: expiry date, and it is "when the tape has run for a month").
KILL_CLOCK_DAYS = 30
KILL_CLOCK_MIN_ENTRIES = 10

#: Why the clock is not running. Deliberately NOT a `decide('SKIP', ...)`
#: reason: this is a property of the kill-condition evaluation, not of a
#: market cycle, and putting it in the strategy's skip vocabulary would file
#: an infrastructure fact under "this window declined" 57 times a cycle.
KILL_CLOCK_DEFERRED_EMPTY = 'kill_clock_deferred_empty_liquidation_tape'
KILL_CLOCK_RUNNING = 'kill_clock_running'

#: Distinct from the above ON PURPOSE. "No liquidations table at all" and "a
#: table that exists and is empty" have the same consequence today and
#: different owners: the first is a schema/db problem, the second is a quiet
#: tape. Convention 20 - two causes never share one name.
KILL_CLOCK_DEFERRED_NO_TABLE = 'kill_clock_deferred_no_liquidation_table'


def liquidation_row_count(db_path: str) -> Optional[int]:
    """Rows in `liquidations`, or `None` if the table cannot be read at all.

    `None` and `0` are different answers and the caller treats them as such.
    Read-only by URI, like every other read in this module, and it never
    raises: a database the recorder holds mid-write must not take the kill
    evaluation down with it.
    """
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(_ro_uri(db_path), uri=True, timeout=5.0)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "select name from sqlite_master where type='table' "
            "and name='liquidations'").fetchone()
        if row is None:
            return None
        return int(conn.execute('select count(*) from liquidations')
                   .fetchone()[0])
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def kill_clock_status(db_path: str, entries_to_date: Optional[int] = None,
                      now: Optional[float] = None,
                      days: int = KILL_CLOCK_DAYS,
                      min_entries: int = KILL_CLOCK_MIN_ENTRIES) -> Dict:
    """Is the 30-day/10-entry kill clause running, and has it fired?

    D-296. The guard is the first thing checked and it is unconditional: with
    no liquidation row to date the clause is not evaluated AT ALL, and
    `fired` is False because nothing was measured - never because a
    measurement came out favourable.

    `clock_started_ms` is the timestamp of the OLDEST liquidation row, not the
    newest and not "now". The tape's first print is the first moment the
    second lock could have had an input, so it is the first moment a
    zero-entry day is evidence about the strategy rather than about the feed.

    `entries_to_date` is passed in rather than queried here: the ENTER count
    lives in `signals`, which this module does not own and must not couple to.
    `None` means the caller has not counted them, so the clause is reported as
    running-but-unevaluated rather than as a pass.
    """
    now = time.time() if now is None else float(now)
    rows = liquidation_row_count(db_path)

    if rows is None:
        return {'clock_running': False, 'fired': False,
                'status': KILL_CLOCK_DEFERRED_NO_TABLE,
                'liquidation_rows': 0, 'clock_started_ms': None,
                'days_elapsed': None, 'days_required': days,
                'entries_to_date': entries_to_date,
                'min_entries': min_entries, 'evaluated': False}
    if rows == 0:
        return {'clock_running': False, 'fired': False,
                'status': KILL_CLOCK_DEFERRED_EMPTY,
                'liquidation_rows': 0, 'clock_started_ms': None,
                'days_elapsed': None, 'days_required': days,
                'entries_to_date': entries_to_date,
                'min_entries': min_entries, 'evaluated': False}

    started_ms = None
    try:
        conn = sqlite3.connect(_ro_uri(db_path), uri=True, timeout=5.0)
        try:
            started_ms = conn.execute(
                'select min(ts) from liquidations').fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:                # pragma: no cover - defensive
        started_ms = None

    days_elapsed = (None if started_ms is None
                    else round((now - float(started_ms) / 1000.0) / 86400.0, 3))

    # Unevaluated is a THIRD state, not a pass. Without an entry count there is
    # no basis to fire and no basis to clear, and reporting `fired=False` with
    # `evaluated=False` keeps a caller from reading silence as survival.
    evaluated = (entries_to_date is not None and days_elapsed is not None
                 and days_elapsed >= days)
    fired = bool(evaluated and int(entries_to_date) < min_entries)

    return {'clock_running': True, 'fired': fired,
            'status': KILL_CLOCK_RUNNING,
            'liquidation_rows': int(rows), 'clock_started_ms': started_ms,
            'days_elapsed': days_elapsed, 'days_required': days,
            'entries_to_date': entries_to_date,
            'min_entries': min_entries, 'evaluated': evaluated}


def kill_clock_row_features(window) -> Dict:
    """The D-296 guard state, stamped ON THE ROW, from a window already read.

    `kill_clock_status()` above is the evaluator's entry point and it opens the
    database. This is the SAME ruling for the shadow loop's hot path, and it
    issues no query at all: `LiquidationWindow` already carries `rows_total`,
    so the guard rides along free on a read the strategy had to do anyway.

    Two things are deliberate:

    1. The count is SYMBOL-FILTERED ('BTC%'), because that is the tape this
       strategy's second lock actually consults. `liquidation_row_count()`
       above is unfiltered - a broader question with a broader answer. The
       field name says which one this is, and `liq_symbol_like` is already on
       the same row, so neither can be mistaken for the other.
    2. `liquidation_table_missing` is the one reason whose `rows_total` is a
       dataclass DEFAULT rather than a count - the query never ran - so it maps
       to UNKNOWN (`None`), never to zero. "We could not read the tape" and
       "the tape is empty" are different facts with different owners.

    Stamped on every row from the second lock down, entries included, because
    "the clock was not running when this row was written" is a property of the
    row and not of whoever scores it a month later.
    """
    common = {'kill_clock_days_required': KILL_CLOCK_DAYS,
              'kill_clock_min_entries': KILL_CLOCK_MIN_ENTRIES}
    if window is None or (not getattr(window, 'ok', False)
                          and getattr(window, 'reason', None)
                          == REASON_TABLE_MISSING):
        return dict(common, kill_clock_running=False,
                    kill_clock_status=KILL_CLOCK_DEFERRED_NO_TABLE,
                    kill_clock_liq_rows_for_symbol=None)
    rows = int(getattr(window, 'rows_total', 0) or 0)
    if rows <= 0:
        return dict(common, kill_clock_running=False,
                    kill_clock_status=KILL_CLOCK_DEFERRED_EMPTY,
                    kill_clock_liq_rows_for_symbol=0)
    return dict(common, kill_clock_running=True,
                kill_clock_status=KILL_CLOCK_RUNNING,
                kill_clock_liq_rows_for_symbol=rows)
