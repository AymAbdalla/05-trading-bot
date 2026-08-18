"""Smart Money Copy: mirror a BUY made by a wallet with a MEASURED record.

Concept borrowed from MrFadiAi's public Polymarket bot ("copy the profitable
wallets"). The concept is all that is borrowed. His thresholds, his wallet list
and his claimed returns are not evidence and none of them is wired into a gate
here (convention 3).

    a tracked wallet BUYS an outcome in the market we are looking at
        -> the wallet's OWN measured record clears the gate
        -> the trade is fresh enough to still be the whale and not the price
        -> we mirror the SIDE, at OUR size, and hold to resolution

## THE HANDLE PROBLEM, which is the reason most of this file exists

The wallet list comes from a Dan1ro0 article and a Reddit post, and it is a list
of DISPLAY HANDLES: bonereaper, boneohio, coinfilippe, doggystyie, Sharky6999,
0x50f7, 0xaaaaa.

The Polymarket Data API takes `?user=<proxy wallet address>`. It does not take a
handle. So TRACKED_WALLETS maps handle -> address, and the mapping has to be
LOOKED UP, never guessed: a 4-hex-character prefix is 16 bits of a 160-bit
address, a fingerprint for confirming a candidate and never a key for finding
one, and a wrong address does not fail loudly, it copies a stranger.

**As of 2026-08-18 all 7 are resolved.** Full log, per-row confidence, and the
requests that produced each address: `research/polymarket_wallets.md`. The
route that worked was Gamma's `public-search?...&search_profiles=true`, and
every hit was round-tripped back to the same display name through a second
independent host before being written in. Two of the endpoints this repo
previously assumed existed - Data API `/profiles` and any public `/leaderboard`
- return 404 on every host tried; that is recorded so nobody re-derives it.

Two corrections that came out of that work:

  - **`0x50f7` and `0xaaaaa` were never address prefixes.** They are chosen
    usernames that look like hex. `TRACKED_WALLET_PREFIXES` is consequently
    empty and `unresolved_prefix_only` is now a bucket with nothing in it.
  - **This strategy can now reach its next gate**, which it never could before.

The census machinery below is UNCHANGED and still load-bearing, because a
resolved address is not a permanent fact (convention 17, and see the expiry note
above the table). A wallet we cannot resolve is skipped with its own reason
`wallet_address_unresolved`, it is COUNTED, and it is categorised into
`unresolved_prefix_only` (we have some hex and not enough of it) versus
`unresolved_no_address` (we have a display name and nothing else). Two different
problems needing two different fixes; pooling them into one number would hide
that (convention 20), and the accounting identity

    resolved + unresolved_prefix_only + unresolved_no_address == len(TRACKED_WALLETS)

is stamped on every decision row this strategy emits and asserted in
`tests/test_smart_money_copy.py`.

Resolving the addresses did not make the strategy tradeable, it made the NEXT
blocker visible: the record gate. That blocker is addressed in the next section.

## EVERY MARKET TYPE, because the WALLET picks the market and we do not

`supported_market_types = MARKET_TYPES` - all six. This strategy is the one in
the package with no universe of its own. It does not poll a board and pick a
market; it reads a wallet's tape and the wallet picks. Declaring anything
narrower would mean silently discarding the trades a tracked whale makes outside
whatever we had guessed their universe was.

That is not hypothetical. Measured on the live tape 2026-08-18, last 50 fills
per wallet, classified by slug: **166 of 350 rows (47%) were NOT crypto Up/Down
markets.** `coinfilippe` was 50/50 non-crypto and `Sharky6999` 49/50. A
crypto-only reading of this strategy was throwing away roughly half of what the
wallets we chose to follow actually do.

What made the file crypto-only was never the matching or the sizing, both of
which are market-agnostic. It was ONE line: the observation clock was derived
from `ctx.window_ts + ctx.seconds_into_window`, which only exists on a
`crypto_updown` context. On any other type the strategy returned
`no_trade_clock` forever - a refusal that looks in a log exactly like a wiring
problem we had already fixed. `observation_clock` now derives the crypto case
exactly as before and falls back to the injected wall clock on every other type,
stamping `trade_clock_source` on the row so the two can never be pooled.

**`MARKET_TYPE_SMART_MONEY` is about the DISCOVERY PATH, not the venue.** A
market we reached by following a whale is not the same SAMPLE as the same market
reached by polling a board we chose, so every row carries `discovery_path` and
`sample_is_wallet_discovered` and the two populations can be split apart later
rather than averaged into one that describes neither.

## THE WIN RATE GATE, and the one thing that would make this file dishonest

The gate is "win rate above 60% over more than 50 trades". Those two numbers
are ours and they are assumptions with an expiry date (convention 17). The
number that would NOT be ours is the article's claimed win rate for these
wallets, and copying that into the gate would be fabricating evidence: it is a
blog post about somebody else's wallet, we have never seen their fills, and a
strategy that reads its own gate off a screenshot has no gate at all.

So `WalletRecord` is computed from trades we have SCORED OURSELVES. If we cannot
score any, the record is UNMEASURED and the wallet is skipped. There is no
fallback, no default win rate, and no "assume the article".

### How a fill becomes a win or a loss (2026-08-18, this is the new part)

Polymarket's public `/trades` returns fills, not outcomes: no `won`, no
`realized_pnl`, no redemption flag. It DOES return `conditionId` and `asset`,
and `engine/polymarket/market_resolution.py` turns a `conditionId` into "which
token paid $1.00" off `clob.polymarket.com/markets/<conditionId>`. Read that
module's docstring; the endpoint choice and the `closed`/`winner` trap are
documented there against real payloads.

The arithmetic, on a binary where the winning share redeems at exactly $1.00
and the losing share at exactly $0.00:

    BUY  of outcome X at price p, n shares:
        X won   ->  pnl = (1.00 - p) * n        (paid p, redeems 1.00)
        X lost  ->  pnl = -p * n                (paid p, redeems 0.00)

    SELL of outcome X at price p, n shares:
        X won   ->  pnl = -(1.00 - p) * n       (received p, gave up 1.00)
        X lost  ->  pnl = +p * n                (received p, gave up 0.00)

`trade_pnl_usdc` is the single implementation and `wallet_trade_won` is the
single sign read. Getting this inverted is the obvious silent failure - it would
report a 40% wallet as a 60% wallet with no error anywhere - so the direction is
pinned three ways: the fill's `asset` is matched against the resolution's WINNING
TOKEN ID rather than against the outcome display string, `MarketResolution`
refuses any payload with more than one winner or with overlapping winner/loser
sets, and `tests/test_smart_money_wallet_record.py` asserts every one of the
four rows above plus a mirrored-resolution test in which every verdict must flip.

### What is measured is a HOLD-TO-RESOLUTION record, not their realized PnL

Only BUY rows are scored. Two reasons, both load-bearing:

  1. **A SELL is usually an exit, not a bet.** A wallet that BUYs Up at 0.56 and
     SELLs it at 0.70 realised +0.14 and never held to resolution. Counting both
     rows as independent bets scores one round trip as one win and one loss
     regardless of what the market did, which is noise dressed as a sample.
  2. **We only ever mirror a BUY.** `evaluate` refuses a SELL (see
     `no_tracked_wallet_buy`). So "what would have happened had they bought this
     and held it" is EXACTLY the trade this strategy takes, and it is the right
     thing to gate on.

Say what that means plainly: this is not the wallet's realized PnL and it is not
what they banked. Bonereaper's public all-time PnL is $1.3M; nothing in that
number is used here (convention 3). SELL rows are counted into a named drop
bucket, never silently dropped (convention 20).

### The minimum sample, and why 50 is a weak screen and is kept anyway

`MIN_TRADE_COUNT = 50`, strictly exceeded. The arithmetic, stated so nobody has
to trust it: under a null of a coin flip, the standard error on 50 binary trades
is sqrt(0.25/50) = 7.07 points, so an observed 60% sits 1.41 SE above 50% - a
one-sided p of about 0.079. **That is a screen, not a finding** (convention 7).
100 trades would put the same 60% at 2.0 SE (p ~ 0.023).

50 is kept because it is the number already shipped and already documented, and
moving a live gate threshold is a decision with a D-number, not a side effect of
the task that finally made the gate reachable. It is written down here as a known
weakness rather than left for a reader to discover.

The consequence is that "we scored 12 of their trades and 9 won" is NOT a wallet
that passes and it is NOT a wallet that failed. It is NOT_TESTED, and it gets its
own skip reason `wallet_record_insufficient_sample`, which never shares a counter
with `wallet_record_below_threshold` (a real measured rejection) or with
`wallet_record_unmeasured` (we scored nothing at all). Three different facts,
three different reasons, three different counters (conventions 11 and 20).

## WHAT THIS STRATEGY CANNOT SEE (convention 22)

  - **Whether the whale is still in.** We see BUY fills. We do not see their
    position, their hedge on another venue, or a SELL they placed one second
    after the fill we copied. Mirroring a leg of a spread as a naked directional
    binary is a real failure mode and nothing here can detect it.
  - **Whether the fill we are reading is the whale's own idea.** Copy trading is
    a crowded strategy. If ten bots mirror the same wallet, the price we lift is
    partly their impact, which is a cost that does not appear anywhere in this
    file.
  - **Whether our Decision became a fill.** The halt check, the risk gate and
    the paper adapter all sit downstream and any of them can refuse. Counters
    here are ATTEMPTS.

## Deduplication, and why a poll loop needs it

The shadow loop polls every few seconds and the Data API returns the same
recent trade on every poll. Without dedupe one whale BUY becomes twenty copies
of itself, which would look in a graveyard like twenty independent signals
agreeing. Copied trade ids are remembered (bounded) and a repeat is refused with
`already_copied_this_trade`.

## Exits

Holds to resolution. `manages_exits = False`. On a binary the stop is exactly
0.00, which is strictly below any entry premium and satisfies convention 8.
Positions from this strategy belong in the RESOLUTION population, never pooled
with the fair-value family's SELL population.

KILL CONDITION: trailing-30 copied-trade win rate below 50%, once 30 copied
trades exist, scored by `backtest/polymarket_harness.py` over the
`PM_smart_money_copy` population alone. 50% is not an arbitrary line: we buy at
the ask, so a copied binary bought at premium p needs a win rate above p to
break even, and the entry band below caps p at 0.95. A sub-50% result over 30
trades says the wallets are not predictive at OUR latency even if they are
predictive at theirs, which is the specific thing this strategy is betting on
and the specific thing it cannot verify in advance.
"""
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from engine.polymarket.market_resolution import MarketResolutionCache
from engine.polymarket.risk_gate import (DEFAULT_MARKET_TYPE_PATTERNS,
                                         classify_market_type)
from strategies.polymarket.base import (MARKET_TYPE_CRYPTO_UPDOWN,
                                        MARKET_TYPE_EVENT,
                                        MARKET_TYPE_SMART_MONEY, MARKET_TYPES,
                                        Decision, Leg, MarketContext,
                                        PolymarketStrategy, effective_ask_for)

logger = logging.getLogger(__name__)

# Never False in this repo. Nothing here has live-trading authority, and this
# module imports no wallet, no signer and no order path.
PAPER_MODE = True

# ---------------------------------------------------------------------------
# The wallet list. Read the HANDLE PROBLEM section above before editing it.
# ---------------------------------------------------------------------------

#: handle -> Polymarket PROXY WALLET ADDRESS, or None when we do not have one.
#:
#: PROVENANCE: every address below was resolved on 2026-08-18 and the full
#: research log - every request, every status code, every dead end, and the
#: per-row confidence - is `research/polymarket_wallets.md`. Read it before
#: editing this table. Nothing here was guessed, derived from a prefix, or
#: pattern-matched from a plausible-looking hex string. A None here would be a
#: skip reason, never a slot to fill in to make the table look complete.
#:
#: Each address was found by `gamma-api /public-search?...&search_profiles=true`
#: and then round-tripped back to the SAME display name through a second,
#: independent host, `polymarket.com/api/profile/userData?address=`. The search
#: filter was itself controlled against the `order=volume` failure mode: a
#: nonsense handle returns `totalResults: 0`, not a default page.
#:
#: CONVENTION 17 - these are assumptions with an expiry date. A Polymarket
#: display name is not a permanent binding to an address: a handle that is
#: renamed or deleted frees up for somebody else, and a trader can rotate to a
#: fresh proxy wallet, at which point this table points at a dormant address
#: that looks exactly like "the whale is quiet today". Re-run the round trip in
#: section 9 of the research log monthly, and before treating any output of this
#: strategy as a result rather than a smoke test.
#:
#: NOT VERIFIED, equally for all seven: that the profile Polymarket calls
#: `Bonereaper` today is the `bonereaper` the Dan1ro0 article meant. Web access
#: was denied in the resolving session so neither the article nor the Reddit
#: post was ever read. See research log section 7 for why that gap is small
#: (all seven are six-figure-profitable, all seven trade crypto Up/Down, all
#: seven were live within the same four minutes) and for what it still does not
#: establish. Their PnL is THEIR record, it is not evidence for us, and
#: convention 3 means none of it is wired into a gate here.
TRACKED_WALLETS: Dict[str, Optional[str]] = {
    'bonereaper': '0xeebde7a0e019a63e6b476eb425505b7b3e6eba30',
    # Display NAME, not an address prefix - see the note on
    # TRACKED_WALLET_PREFIXES below and research log section 6.
    '0x50f7': '0xee65685de42f8de9a03b4c53ee77d56a20d2cfc9',
    'boneohio': '0x48ac40fc545cf327edd5365435c3a9f385614a7e',
    'coinfilippe': '0x997cda7b31612e3c394bfb55440619f3f689251e',
    # Display NAME, not an address prefix.
    '0xaaaaa': '0x251c1a283703beed41590b0875a8dcb8ddd1541f',
    'doggystyie': '0x0484e64092ba4108c2786b61e6fc052d3bf41b1a',
    'Sharky6999': '0x751a2b86cab503496efd325c8344e10159349ea1',
}

#: handle -> the address PREFIX the source gave us, for entries that are
#: prefixes rather than names. Kept separate from TRACKED_WALLETS on purpose: a
#: prefix is a fingerprint for confirming a candidate address, and putting it in
#: the address slot is how it would end up in a query string.
#:
#: 2026-08-18: THIS MAP IS NOW EMPTY, and the reason is a corrected assumption
#: rather than a solved problem. `0x50f7` and `0xaaaaa` were never address
#: prefixes. They are chosen USERNAMES that happen to look like hex, and both
#: resolve above. The evidence (research log section 6): each has exactly one
#: chosen-name profile, and the only accounts that genuinely START with those
#: hex characters carry Polymarket's default `<address>-<epoch-ms>` username and
#: have traded ZERO. A list of profitable traders does not contain three wallets
#: that have never placed a trade. Also, a source truncating addresses would not
#: truncate to 4 characters once and 5 the next time.
#:
#: The map and the `unresolved_prefix_only` bucket stay. The census is generic,
#: the identity still balances with the bucket at zero, and a future handle that
#: really is a prefix belongs here. Deleting the machinery is a separate change
#: and needs a D-number.
TRACKED_WALLET_PREFIXES: Dict[str, str] = {}

#: An EVM address is '0x' plus 40 hex characters. Anything shorter is a prefix.
ADDRESS_LENGTH = 42

RESOLVED = 'resolved'
UNRESOLVED_PREFIX_ONLY = 'unresolved_prefix_only'
UNRESOLVED_NO_ADDRESS = 'unresolved_no_address'

#: Every resolution status. A new one must be added here or the identity check
#: in `resolve_tracked_wallets` will refuse to balance.
RESOLUTION_STATUSES = (RESOLVED, UNRESOLVED_PREFIX_ONLY, UNRESOLVED_NO_ADDRESS)

# ---------------------------------------------------------------------------
# Feed constants
# ---------------------------------------------------------------------------

DATA_API_HOST = 'https://data-api.polymarket.com'
TRADES_PATH = '/trades'

#: The query parameter that actually filters `/trades` by wallet. **It is
#: `user`, and it is NOT `takerAddress`.** Named as a constant rather than
#: inlined because getting it wrong does not fail, it returns somebody else's
#: fills with a 200 on top.
#:
#: Controlled on the live endpoint, 2026-08-18, exactly the way CLAUDE.md's
#: `order=volume` lesson says to control a filter - by asking whether it filters
#: at all, not by checking the status code:
#:
#:     GET /trades?takerAddress=0xeebde7a0...&limit=5   -> 200, 5 rows
#:     GET /trades?takerAddress=0x751a2b86...&limit=5   -> 200, THE SAME 5 rows
#:                                                        (identical txhashes)
#:     GET /trades?takerAddress=0x0000...00&limit=5     -> 200, 5 rows belonging
#:                                                        to 4 OTHER wallets
#:     GET /trades?user=0xeebde7a0...&limit=5           -> 200, all 5 rows carry
#:                                                        proxyWallet == that
#:     GET /trades?user=0x0000...00&limit=5             -> 200, ZERO rows
#:
#: `takerAddress` is accepted and ignored. A strategy built on it would copy the
#: global tape while its logs said it was following seven chosen whales, and
#: nothing in the response would ever say otherwise. The zero-row control on
#: `user` is what proves that one IS a filter and not a default page.
TRADES_USER_PARAM = 'user'

#: Rejected. Kept named so the next reader who is handed this parameter finds
#: the control above instead of re-running it.
TRADES_UNFILTERED_PARAM = 'takerAddress'

#: Short on purpose. This runs inside a 5-minute window loop that polls every
#: few seconds; a feed that blocks for 10 seconds has already made the trade
#: stale by the time it answers.
DEFAULT_FEED_TIMEOUT_SEC = 2.0

#: Bounded. Two attempts, one backoff. Retrying harder inside a latency-gated
#: strategy buys a stale answer at a higher price.
DEFAULT_FEED_RETRIES = 2
FEED_BACKOFF_SEC = 0.25

#: Trades pulled per wallet per poll.
DEFAULT_TRADE_LIMIT = 25

#: Trades pulled when building a record. Larger because MIN_TRADE_COUNT is 50
#: and a record built from fewer rows than the gate needs cannot pass it. It has
#: to be a good deal larger than 50: SELL rows and still-open markets are both
#: dropped from the sample, so 500 fills is nowhere near 500 scorable ones.
DEFAULT_RECORD_LIMIT = 500

#: How long a computed wallet record stays cached before it is rebuilt. One hour.
#:
#: This is the "slow cadence" the record gate runs on and it is deliberately
#: thousands of times the 5-second poll interval. Rebuilding a record costs one
#: `/trades` read plus one CLOB read per DISTINCT unresolved market in the tape;
#: doing that per evaluation would spend the entire latency budget of a strategy
#: whose edge is a 120-second freshness window, to re-derive a number that moves
#: by at most a fraction of a point per hour on a 50+ trade base.
#:
#: Not infinite, which is what the previous per-process cache effectively was.
#: The shadow loop runs for days and a wallet that degrades has to be able to
#: fall back out of the gate without a restart (convention 17).
DEFAULT_RECORD_TTL_SEC = 3600.0

# ---------------------------------------------------------------------------
# Strategy constants. OURS, not the article's. Convention 17 applies to all.
# ---------------------------------------------------------------------------

#: Measured win rate a wallet must EXCEED. Strictly greater.
MIN_WIN_RATE = 0.60

#: Settled trades a wallet's record must EXCEED. Strictly greater. Convention 7
#: cuts both ways: a 70% win rate on 12 trades is a shrug, not a green light.
MIN_TRADE_COUNT = 50

#: A copied trade older than this is refused. Following a whale 10 minutes late
#: is following the price, not the whale, and the price has already moved to
#: where the whale put it.
#:
#: It is a LATENCY budget, not a market-duration one, so it does not become
#: wrong when the market type widens: the loop polls every few seconds, so a
#: whale's fill on a six-month election market is seen just as fresh as one on a
#: 5-minute BTC window. What DOES plausibly differ per type is how long our edge
#: survives after them, and that is a number nobody here has measured. So the
#: per-type override below exists and ships EMPTY rather than being filled with
#: guesses (convention 17 - a hardcoded threshold is an assumption with an
#: expiry date, and an invented one has already expired).
MAX_TRADE_AGE_SEC = 120.0

#: market type -> its own freshness horizon, overriding MAX_TRADE_AGE_SEC.
#: EMPTY ON PURPOSE. See above. Filling it is a decision with a D-number.
MAX_TRADE_AGE_SEC_BY_MARKET_TYPE: Dict[str, float] = {}

#: Our own size cap, in shares. Explicitly NOT the whale's size: they are
#: sizing against their bankroll and their conviction, neither of which we can
#: see, and copying a size is how a copy-trader inherits somebody else's risk
#: limits.
MAX_SHARES = 20

#: Per-trade notional. Matches PolymarketPaperAdapter.notional_cap_usdc and
#: PolymarketRiskGate.DEFAULT_NOTIONAL_CAP_USDC; restated so a size computed
#: here cannot silently exceed a cap enforced somewhere else.
MAX_NOTIONAL_USDC = 10.0

#: Exchange minimum order size, in shares.
MIN_SHARES = 5

#: Highest premium we will pay. Above this the trade is Dan1ro0 concept 4E
#: (near-resolution capture), which needs its own position limits and a
#: data-quality kill switch, neither of which is built. Also the break-even
#: arithmetic: at 0.96 the copied wallet has to be right 96% of the time and
#: the kill condition's 50% line stops meaning anything.
MAX_ENTRY_PRICE = 0.95

#: Shares that must rest within DEPTH_BAND of the best ask. A whale's fill
#: against a 6-share top level tells us nothing about what WE can get.
MIN_BOOK_DEPTH_SHARES = 50
DEPTH_BAND = 0.03

#: Copied trade ids remembered. Bounded so a long session does not grow without
#: limit; large enough that a trade cannot age out of the set while it is still
#: inside MAX_TRADE_AGE_SEC at any plausible whale trade rate.
COPIED_IDS_KEPT = 2000

#: Boolean settlement keys `record_from_rows` will accept, in order.
SETTLEMENT_BOOL_KEYS = ('won', 'is_win', 'is_winner')

#: Numeric settlement keys. A row is a win when the value is strictly positive.
SETTLEMENT_NUMERIC_KEYS = ('realized_pnl', 'realizedPnl', 'pnl', 'profit')


# ---------------------------------------------------------------------------
# Slug -> market type, in the BASE vocabulary.
# ---------------------------------------------------------------------------

#: The `risk_gate` slug labels that mean "a crypto Up/Down window". Derived from
#: that module's own pattern table rather than restated, so a seventh crypto
#: pattern added there joins this set automatically instead of quietly
#: classifying as a general binary (convention 23 - a fix at one site is not a
#: fix).
CRYPTO_SLUG_TYPES = frozenset(DEFAULT_MARKET_TYPE_PATTERNS)


def underlying_market_type(slug: Optional[str]) -> str:
    """A `/trades` row's slug -> one of `strategies.polymarket.base.MARKET_TYPES`.

    Used to BREAK DOWN a measured record, never to gate one. It exists because
    a wallet's 98% record on 5-minute BTC windows is not evidence about their
    election trades, and one pooled win rate cannot say so.

    **What this cannot do, stated rather than left to be discovered.** It reads
    a slug, so it can separate a crypto Up/Down window from everything else and
    it CANNOT separate `sports` from `political` from `event`: all three land in
    `MARKET_TYPE_EVENT`. That is a known ceiling on the classifier, not a claim
    that the market is a general event market, and every record built through
    here carries `market_type_cannot_split_sports_or_political: True` so the
    label is never read as more precise than it is (convention 28 - half a
    resolution is not a resolution).
    """
    label = classify_market_type(slug or '')
    if label in CRYPTO_SLUG_TYPES:
        return MARKET_TYPE_CRYPTO_UPDOWN
    return MARKET_TYPE_EVENT


def _reject_non_finite(token: str) -> float:
    """`json.loads` accepts bare Infinity and NaN. This refuses them.

    Convention 19. A record computed over a NaN pnl is a record that silently
    counts a corrupt row as a loss, and the value round-trips out of Python as
    a token no other JSON parser accepts.
    """
    raise ValueError('feed payload contained the non-finite JSON constant '
                     '{!r}; this is not portable JSON (convention 19)'.format(
                         token))


def is_full_address(value: Optional[str]) -> bool:
    """True only for '0x' plus 40 hex characters. A prefix is not an address."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if len(v) != ADDRESS_LENGTH or not v.lower().startswith('0x'):
        return False
    try:
        int(v[2:], 16)
    except ValueError:
        return False
    return True


def resolve_tracked_wallets(wallets: Optional[Dict[str, Optional[str]]] = None
                            ) -> Tuple[Dict[str, str], Dict[str, str],
                                       Dict[str, int]]:
    """Split the wallet list into resolved, unresolved, and a counted census.

    Returns `(resolved, statuses, counts)`:

      resolved  handle -> address, for handles carrying a FULL address
      statuses  handle -> one of RESOLUTION_STATUSES, for EVERY handle
      counts    status -> how many handles landed there, every status present
                even at zero

    Nothing is dropped. The accounting identity `sum(counts.values()) ==
    len(wallets)` holds by construction and is asserted here rather than being
    left for a reader to trust (convention 20).
    """
    wallets = TRACKED_WALLETS if wallets is None else wallets
    resolved: Dict[str, str] = {}
    statuses: Dict[str, str] = {}
    counts: Dict[str, int] = {s: 0 for s in RESOLUTION_STATUSES}

    for handle, address in wallets.items():
        if is_full_address(address):
            resolved[handle] = str(address).strip()
            status = RESOLVED
        elif handle in TRACKED_WALLET_PREFIXES or (
                isinstance(address, str) and address.strip()):
            # We have SOME hex, just not enough of it. A different problem from
            # "we have a display name and nothing else" and it needs a
            # different fix, so it gets its own bucket.
            status = UNRESOLVED_PREFIX_ONLY
        else:
            status = UNRESOLVED_NO_ADDRESS
        statuses[handle] = status
        counts[status] += 1

    total = sum(counts.values())
    if total != len(wallets):
        raise AssertionError(
            'wallet resolution census does not balance: {} counted vs {} '
            'tracked'.format(total, len(wallets)))
    return resolved, statuses, counts


# ---------------------------------------------------------------------------
# Feed types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WalletTrade:
    """One fill by a tracked wallet, as far as the public feed can tell us.

    `side` is the wallet's side, normalised to 'BUY' or 'SELL'. A row whose
    side we could not read is NOT defaulted to BUY - it is dropped and counted,
    because mirroring an unreadable side is a coin flip wearing a signal's name.
    """

    trade_id: str
    handle: str
    address: str
    side: str
    outcome_side: str
    token_id: Optional[str] = None
    market_slug: Optional[str] = None
    condition_id: Optional[str] = None
    price: Optional[float] = None
    size: Optional[float] = None
    ts: Optional[float] = None

    @property
    def is_buy(self) -> bool:
        return self.side == 'BUY'

    def age_sec(self, now: float) -> Optional[float]:
        """Seconds since the fill, or None when the row carried no timestamp.

        None means CANNOT MEASURE. A trade with no timestamp is not a fresh
        trade, and the staleness gate treats it as unusable rather than as
        zero seconds old.
        """
        if self.ts is None:
            return None
        return float(now) - float(self.ts)

    def to_dict(self) -> dict:
        return {
            'trade_id': self.trade_id, 'handle': self.handle,
            'address': self.address, 'side': self.side,
            'outcome_side': self.outcome_side, 'token_id': self.token_id,
            'market_slug': self.market_slug, 'price': self.price,
            'size': self.size, 'ts': self.ts,
        }


@dataclass(frozen=True)
class WalletRecord:
    """A wallet's MEASURED settled record. Never a claimed one.

    `measured` is True only when this was computed from rows that carried an
    explicit settlement field. There is no constructor path that produces a
    measured record from an assumption, which is the point of the class.
    """

    address: str
    trades: int
    wins: int
    source: str
    measured: bool = True
    #: Summed hold-to-resolution PnL over the scored rows, in USDC, at THEIR
    #: sizes. Reported, never gated on: a record dominated by one enormous
    #: winning fill is not the same evidence as a consistent 60%, and mixing a
    #: dollar figure into a win-rate gate would hide that.
    pnl_usdc: Optional[float] = None
    #: Rows the feed returned that could NOT be scored, keyed by cause. Never
    #: pooled into a single "skipped" number (convention 20).
    drops: Optional[Dict[str, int]] = None
    #: Mean premium paid across the scored BUY rows. On a binary this IS the
    #: break-even win rate, so it is the number a win rate has to be compared
    #: AGAINST. Measured 2026-08-18 on all seven tracked wallets: every one of
    #: them has a win rate within 4 points of this. See `edge_over_breakeven`.
    mean_entry_price: Optional[float] = None
    #: market type -> {'trades', 'wins', 'win_rate', 'mean_entry_price'} over
    #: the same scored rows the pooled numbers above are built from. REPORTED,
    #: never gated on.
    #:
    #: This is the number the market-type widening actually produced. Live tape,
    #: 2026-08-18, `bonereaper`, last 500 fills: 0.5804 pooled, which is 0.6622
    #: on btc_5m and 0.2857 on the non-crypto rows. One pooled figure describes
    #: neither, and gating on it would admit or refuse both together. Splitting
    #: the GATE per type is a change of thesis and needs a D-number; splitting
    #: the MEASUREMENT does not, and without it nobody can see the split at all.
    by_market_type: Optional[Dict[str, Dict[str, float]]] = None

    @property
    def edge_over_breakeven(self) -> Optional[float]:
        """`win_rate - mean_entry_price`, or None if either is unknown.

        THIS is the number that says whether a wallet is any good, and the raw
        win rate is very nearly not. On a binary bought at premium p the
        break-even win rate is exactly p, so a 98.5% win rate at a mean entry of
        0.949 is 3.7 points of edge, not 48.5 points of skill.

        Measured on the live tape, 2026-08-18, last ~500 BUY fills per wallet:

            handle       win rate   mean entry   edge
            Sharky6999      0.985        0.949   +0.037
            0x50f7          0.907        0.872   +0.035
            boneohio        0.913        0.901   +0.012
            bonereaper      0.499        0.503   -0.005

        The win rate is a restatement of the price paid. It is REPORTED and it
        is NOT gated on, because switching the gate from win rate to edge is a
        change of thesis and needs a D-number, not a side effect of the task
        that made the gate reachable. It is on the record so the next reader
        does not have to rediscover it.
        """
        wr = self.win_rate
        if wr is None or self.mean_entry_price is None:
            return None
        return wr - float(self.mean_entry_price)

    @property
    def win_rate(self) -> Optional[float]:
        if self.trades <= 0:
            return None
        return self.wins / float(self.trades)

    def has_sample(self, min_trades: int = MIN_TRADE_COUNT) -> bool:
        """Is the sample big enough for the win rate to mean anything?

        Split out from `passes` so the caller can tell "not enough trades yet"
        (NOT_TESTED, convention 11) apart from "measured and below the bar"
        (a result). Collapsing the two into one boolean is exactly the pooling
        convention 20 forbids.
        """
        return self.measured and self.trades > int(min_trades)

    def beats(self, min_win_rate: float = MIN_WIN_RATE) -> bool:
        """Is the win rate above the bar? Says NOTHING about sample size."""
        wr = self.win_rate
        if not self.measured or wr is None:
            return False
        return wr > float(min_win_rate)

    def passes(self, min_win_rate: float = MIN_WIN_RATE,
               min_trades: int = MIN_TRADE_COUNT) -> bool:
        return self.has_sample(min_trades) and self.beats(min_win_rate)

    def win_rate_for_market_type(self, market_type: str) -> Optional[float]:
        """Measured win rate on ONE market type, or None if none was scored.

        None is NOT_TESTED for that type and never a 0% (convention 11). A
        wallet with 300 scored crypto rows and zero scored election rows has no
        election record at all, which is a different fact from a bad one.
        """
        bucket = (self.by_market_type or {}).get(market_type)
        if not bucket or not bucket.get('trades'):
            return None
        return float(bucket['wins']) / float(bucket['trades'])

    def to_dict(self) -> dict:
        return {'address': self.address, 'trades': self.trades,
                'wins': self.wins, 'win_rate': self.win_rate,
                'measured': self.measured, 'source': self.source,
                'pnl_usdc': self.pnl_usdc, 'drops': dict(self.drops or {}),
                'mean_entry_price': self.mean_entry_price,
                'edge_over_breakeven': self.edge_over_breakeven,
                'by_market_type': {k: dict(v) for k, v in
                                   (self.by_market_type or {}).items()},
                'market_type_cannot_split_sports_or_political': True}


def _safe_float(value) -> Optional[float]:
    """`float()` that refuses non-finite values and unparseable ones."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _normalise_side(raw) -> Optional[str]:
    """'buy'/'BUY'/'Buy' -> 'BUY'. Anything unrecognised -> None."""
    if not isinstance(raw, str):
        return None
    v = raw.strip().upper()
    if v in ('BUY', 'B', 'BID'):
        return 'BUY'
    if v in ('SELL', 'S', 'ASK'):
        return 'SELL'
    return None


def _normalise_ts(raw) -> Optional[float]:
    """Epoch seconds, or None.

    Milliseconds are converted ONLY when the field name says so (handled by the
    caller). Magnitude sniffing is deliberately absent: convention 14's
    `min_bars_for()` lesson is that unit detection by magnitude reads a
    synthetic timestamp near epoch 0 as the wrong unit and derives a value
    1000x off, silently.
    """
    return _safe_float(raw)


def trade_from_row(row: dict, handle: str, address: str
                   ) -> Tuple[Optional[WalletTrade], Optional[str]]:
    """Parse one Data API row. Returns `(trade, drop_reason)`.

    Exactly one of the two is not None. Every drop is named so a feed returning
    a changed schema shows up as a categorised count rather than as an empty
    result that reads identically to a quiet wallet (convention 20).
    """
    if not isinstance(row, dict):
        return None, 'row_not_a_dict'

    side = _normalise_side(row.get('side'))
    if side is None:
        return None, 'unreadable_side'

    outcome = row.get('outcome')
    if not isinstance(outcome, str) or not outcome.strip():
        return None, 'unreadable_outcome'

    ts = _normalise_ts(row.get('timestamp'))
    if ts is None:
        ts_ms = _normalise_ts(row.get('timestamp_ms'))
        ts = None if ts_ms is None else ts_ms / 1000.0

    trade_id = row.get('transactionHash') or row.get('id') or row.get('trade_id')
    if not trade_id:
        # No stable id means no dedupe, and no dedupe means one whale BUY
        # becomes twenty copies of itself. Refuse rather than synthesise one.
        return None, 'no_trade_id'

    return WalletTrade(
        trade_id=str(trade_id),
        handle=handle,
        address=address,
        side=side,
        outcome_side=outcome.strip(),
        token_id=(str(row['asset']) if row.get('asset') else
                  (str(row['token_id']) if row.get('token_id') else None)),
        market_slug=(str(row['slug']) if row.get('slug') else None),
        condition_id=(str(row['conditionId']) if row.get('conditionId')
                      else None),
        price=_safe_float(row.get('price')),
        size=_safe_float(row.get('size')),
        ts=ts,
    ), None


def record_from_rows(rows, address: str) -> Optional[WalletRecord]:
    """Build a MEASURED record, or return None.

    None means "this wallet's record could not be measured", never "this wallet
    has a bad record" (convention 11). A row counts only when it carries an
    explicit settlement field; rows without one are not assumed to be losses,
    they are not counted at all, and if NO row carries one the answer is None.

    Polymarket's public `/trades` carries none of these keys today, so against
    the live API this returns None and the strategy skips
    `wallet_record_unmeasured`. That is the honest current state, not a bug.
    """
    if not rows:
        return None
    settled = 0
    wins = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        decided = None
        for key in SETTLEMENT_BOOL_KEYS:
            if key in row and isinstance(row[key], bool):
                decided = bool(row[key])
                break
        if decided is None:
            for key in SETTLEMENT_NUMERIC_KEYS:
                if key in row:
                    val = _safe_float(row[key])
                    if val is not None:
                        decided = val > 0
                    break
        if decided is None:
            continue
        settled += 1
        if decided:
            wins += 1
    if settled == 0:
        return None
    return WalletRecord(address=address, trades=settled, wins=wins,
                        source='settled_trade_rows', measured=True)


# ---------------------------------------------------------------------------
# Scoring a fill against how the market actually resolved.
#
# This is the block that unblocked `wallet_record_unmeasured`. Read the "How a
# fill becomes a win or a loss" section of the module docstring first.
# ---------------------------------------------------------------------------

#: What a share redeems for. These are the two numbers the whole file rests on
#: and they are not estimates: a Polymarket binary settles at exactly $1.00 for
#: the winning outcome and exactly $0.00 for the loser. Named rather than
#: inlined so `1 - p` can never be read as a rounding convenience.
WINNING_SHARE_PAYOUT_USDC = 1.00
LOSING_SHARE_PAYOUT_USDC = 0.00

#: Reasons a `/trades` row cannot be scored. Every row the record builder
#: refuses lands in exactly one of these and is COUNTED (convention 20). None of
#: them is a loss; they are all "cannot measure this row".
ROW_DROP_REASONS = (
    'sell_row_not_an_independent_bet',
    'no_condition_id',
    'unreadable_price',
    'unreadable_size',
    'price_outside_zero_one',
    'market_not_resolved',
    'token_not_in_resolved_market',
    'row_not_a_dict',
    'unreadable_side',
)


def trade_pnl_usdc(side: str, entry_price: float, size: float,
                   outcome_won: bool) -> float:
    """PnL in USDC of holding one fill to resolution.

    `outcome_won` is about the OUTCOME TOKEN that was traded, not about the
    trader. A wallet that SELLS a token which then wins has LOST money on that
    row, and this function returns a negative number for it.

    On a binary the winning share redeems at $1.00 and the loser at $0.00, so:

        BUY  of X at p, n shares:  X won -> +(1.00 - p) * n
                                   X lost -> -p * n
        SELL of X at p, n shares:  X won -> -(1.00 - p) * n
                                   X lost -> +p * n

    The SELL row is the BUY row negated, which is the identity a test asserts
    directly. It is implemented as an explicit branch anyway rather than as a
    sign flip, because a sign flip is the single edit that would silently invert
    this whole file.
    """
    p = float(entry_price)
    n = float(size)
    payout = (WINNING_SHARE_PAYOUT_USDC if outcome_won
              else LOSING_SHARE_PAYOUT_USDC)
    norm = _normalise_side(side)
    if norm == 'BUY':
        # Paid p per share, receive `payout` per share.
        return (payout - p) * n
    if norm == 'SELL':
        # Received p per share, owe `payout` per share.
        return (p - payout) * n
    raise ValueError('trade_pnl_usdc needs a BUY or a SELL, got {!r}; an '
                     'unreadable side must be DROPPED, never defaulted'.format(
                         side))


def wallet_trade_won(side: str, entry_price: float, size: float,
                     outcome_won: bool) -> bool:
    """Did the wallet make money on this fill, held to resolution?

    Strictly positive. A scratch is not a win: a zero counted as a win inflates
    the gate by exactly the number of scratches, which is the same trap
    `record_from_rows` already avoids for the numeric-pnl path.
    """
    return trade_pnl_usdc(side, entry_price, size, outcome_won) > 0.0


def score_trade_row(row: dict, resolver) -> Tuple[Optional[bool],
                                                  Optional[float], str]:
    """Score one live `/trades` row. Returns `(won, pnl_usdc, reason)`.

    `won` and `pnl_usdc` are both None exactly when the row could not be scored,
    and `reason` then names why and is one of `ROW_DROP_REASONS`. On success
    `reason` is `'scored'`.

    BUY rows only. See the module docstring for why a SELL is not an independent
    bet and is not simply inverted into one.
    """
    if not isinstance(row, dict):
        return None, None, 'row_not_a_dict'

    side = _normalise_side(row.get('side'))
    if side is None:
        return None, None, 'unreadable_side'
    if side != 'BUY':
        return None, None, 'sell_row_not_an_independent_bet'

    cond = row.get('conditionId') or row.get('condition_id')
    if not cond:
        return None, None, 'no_condition_id'

    price = _safe_float(row.get('price'))
    if price is None:
        return None, None, 'unreadable_price'
    if not (0.0 <= price <= 1.0):
        # A binary premium outside [0, 1] is a corrupt row, not an expensive
        # one, and (1 - p) on it would produce a plausible-looking pnl.
        return None, None, 'price_outside_zero_one'

    size = _safe_float(row.get('size'))
    if size is None or size <= 0.0:
        return None, None, 'unreadable_size'

    resolution = resolver.get(str(cond))
    if resolution is None or not getattr(resolution, 'resolved', False):
        # Includes every open market. NOT a loss (convention 11).
        return None, None, 'market_not_resolved'

    # Token id FIRST. `asset` on the trade row is character-for-character the
    # `token_id` in the CLOB response, so this comparison cannot be confused by
    # a display string. The outcome-name fallback exists only because the Data
    # API is not guaranteed to populate `asset`.
    token = row.get('asset') or row.get('token_id')
    verdict = resolution.verdict_for_token(str(token)) if token else None
    if verdict is None:
        verdict = resolution.verdict_for_outcome(row.get('outcome'))
    if verdict is None:
        return None, None, 'token_not_in_resolved_market'

    pnl = trade_pnl_usdc(side, price, size, verdict)
    return pnl > 0.0, pnl, 'scored'


def record_from_trade_rows(rows, address: str, resolver
                           ) -> Tuple[Optional[WalletRecord], Dict[str, int]]:
    """Build a MEASURED record by scoring `/trades` rows against resolutions.

    Returns `(record_or_None, drops)`. None means "we could score nothing",
    never "this wallet has a bad record" (convention 11). `drops` is keyed by
    `ROW_DROP_REASONS` and is returned even when the record is None, because
    "500 rows and every market still open" and "the feed returned nothing" are
    different problems and one None cannot tell them apart.

    A record with 3 scored trades is still returned. Deciding that 3 is too few
    is the GATE's job (`has_sample`), and folding it in here would turn a small
    sample into an unmeasurable one, which is a different fact.
    """
    drops: Dict[str, int] = {}
    if not rows or resolver is None:
        return None, drops
    scored = 0
    wins = 0
    pnl_total = 0.0
    price_total = 0.0
    #: market type -> [scored, wins, summed entry price]. Accumulated on the
    #: SAME rows as the pooled figures, so the buckets add back up to them
    #: exactly and that identity is asserted in the tests (convention 20).
    per_type: Dict[str, List[float]] = {}
    for row in rows:
        won, pnl, reason = score_trade_row(row, resolver)
        if won is None:
            drops[reason] = drops.get(reason, 0) + 1
            continue
        scored += 1
        if won:
            wins += 1
        pnl_total += float(pnl or 0.0)
        # Unweighted mean, matching the win rate's own unweighted count. A
        # size-weighted mean would be a different quantity and would not be the
        # break-even for the win rate it is compared against.
        price = float(_safe_float(row.get('price')) or 0.0)
        price_total += price
        mtype = underlying_market_type(
            row.get('slug') if isinstance(row, dict) else None)
        bucket = per_type.setdefault(mtype, [0.0, 0.0, 0.0])
        bucket[0] += 1
        bucket[1] += 1 if won else 0
        bucket[2] += price
    if scored == 0:
        return None, drops
    by_market_type = {
        mtype: {'trades': int(n), 'wins': int(w),
                'win_rate': round(w / n, 6),
                'mean_entry_price': round(p / n, 6)}
        for mtype, (n, w, p) in per_type.items() if n}
    return WalletRecord(address=address, trades=scored, wins=wins,
                        source='hold_to_resolution_buys',
                        measured=True, pnl_usdc=round(pnl_total, 6),
                        drops=dict(drops),
                        mean_entry_price=round(price_total / scored, 6),
                        by_market_type=by_market_type), drops


class WalletTradeFeed:
    """Read-only reader for `data-api.polymarket.com/trades?user=<address>`.

    Injectable exactly the way `shadow_loop` injects `candle_source` and
    `strike_proxy`, so a unit test hands in a stub and never touches the
    network. The default path prefers the project's own `PolymarketClient`
    (rate limited, GET only, non-finite JSON rejected) and falls back to
    `urllib.request` when no client is supplied, so this module stays importable
    without a network stack.

    NO WALLET, NO SIGNER, NO POST. `_fetch` builds a query string and reads a
    response. There is no code path here that can place an order, which is the
    same structural refusal `engine/polymarket/client.py` makes.

    Failures return None, never `[]`. An unreachable feed and a wallet with no
    recent trades are different facts that demand different responses, and one
    empty list cannot tell them apart (convention 11).
    """

    def __init__(self, client=None, timeout: float = DEFAULT_FEED_TIMEOUT_SEC,
                 retries: int = DEFAULT_FEED_RETRIES,
                 host: str = DATA_API_HOST,
                 trade_limit: int = DEFAULT_TRADE_LIMIT,
                 record_limit: int = DEFAULT_RECORD_LIMIT,
                 resolver=None):
        self.client = client
        #: Turns a `conditionId` into "which token paid $1.00". Built from the
        #: client only when that client can actually reach the CLOB; a stub with
        #: a `.data` method and no `.clob` gets NO resolver and therefore an
        #: unmeasured record, rather than an AttributeError halfway through a
        #: poll loop.
        if resolver is None and client is not None and callable(
                getattr(client, 'clob', None)):
            resolver = MarketResolutionCache(client=client)
        self.resolver = resolver
        self.timeout = float(timeout)
        # retries=0 would mean "never send the request", which is never what a
        # caller means by it.
        self.retries = max(1, int(retries))
        self.host = host.rstrip('/')
        self.trade_limit = int(trade_limit)
        self.record_limit = int(record_limit)
        self.stats: Dict[str, int] = {
            'requests': 0, 'retries': 0, 'failures': 0,
            'fail_network': 0, 'fail_bad_json': 0, 'fail_non_finite_json': 0,
            'fail_not_a_list': 0,
        }

    # -- transport ----------------------------------------------------------

    def _fetch(self, address: str, limit: int) -> Optional[list]:
        # `user`, never `takerAddress`. See TRADES_USER_PARAM for the control
        # that shows the other one is accepted, ignored, and returns strangers'
        # fills with a 200 on top.
        params = {TRADES_USER_PARAM: address, 'limit': int(limit)}

        if self.client is not None and hasattr(self.client, 'data'):
            # The project client already rate limits, retries and rejects
            # non-finite JSON. Reimplementing any of that here would be a
            # second policy nobody updates.
            self.stats['requests'] += 1
            payload = self.client.data(TRADES_PATH, params)
            if payload is None:
                self.stats['failures'] += 1
                self.stats['fail_network'] += 1
                return None
            if not isinstance(payload, list):
                self.stats['failures'] += 1
                self.stats['fail_not_a_list'] += 1
                return None
            return payload

        session = getattr(self.client, 'session', None)
        url = self.host + TRADES_PATH

        last_err = None
        for attempt in range(self.retries):
            is_last = attempt == self.retries - 1
            self.stats['requests'] += 1
            try:
                body = (self._get_via_session(session, url, params)
                        if session is not None
                        else self._get_via_urllib(url, params))
            except Exception as exc:                    # noqa: BLE001
                last_err = '{}: {}'.format(type(exc).__name__, exc)
                if is_last:
                    self.stats['failures'] += 1
                    self.stats['fail_network'] += 1
                    break
                self.stats['retries'] += 1
                # Only sleep when another attempt actually follows. Sleeping
                # after the last one burns latency on a strategy whose whole
                # gate is a 120-second freshness window.
                time.sleep(FEED_BACKOFF_SEC * (2 ** attempt))
                continue

            try:
                payload = json.loads(body, parse_constant=_reject_non_finite)
            except ValueError as exc:
                self.stats['failures'] += 1
                key = ('fail_non_finite_json'
                       if 'non-finite' in str(exc) else 'fail_bad_json')
                self.stats[key] += 1
                logger.error('smart_money_copy feed %s: %s', url, exc)
                return None

            if not isinstance(payload, list):
                self.stats['failures'] += 1
                self.stats['fail_not_a_list'] += 1
                return None
            return payload

        logger.warning('smart_money_copy feed %s failed after %d attempts: %s',
                       url, self.retries, last_err)
        return None

    def _get_via_session(self, session, url: str, params: dict) -> str:
        resp = session.get(url, params=params, timeout=self.timeout)
        if resp.status_code != 200:
            raise IOError('HTTP {}'.format(resp.status_code))
        return resp.text

    def _get_via_urllib(self, url: str, params: dict) -> str:
        import urllib.parse
        import urllib.request
        full = url + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            full, headers={'User-Agent': '05-trading-bot/paper (read-only)'})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            if getattr(resp, 'status', 200) != 200:
                raise IOError('HTTP {}'.format(resp.status))
            return resp.read().decode('utf-8')

    # -- public API ---------------------------------------------------------

    def fetch_trades(self, handle: str, address: str,
                     limit: Optional[int] = None
                     ) -> Tuple[Optional[List[WalletTrade]], Dict[str, int]]:
        """Recent trades for one wallet. Returns `(trades_or_None, drops)`.

        `None` for the trades means the read FAILED. An empty list means the
        read succeeded and the wallet has no recent trades. `drops` is keyed by
        the reason `trade_from_row` refused a row and is never merged into the
        failure count.
        """
        rows = self._fetch(address, self.trade_limit if limit is None else limit)
        drops: Dict[str, int] = {}
        if rows is None:
            return None, drops
        out: List[WalletTrade] = []
        for row in rows:
            trade, drop = trade_from_row(row, handle, address)
            if trade is None:
                drops[drop or 'unknown'] = drops.get(drop or 'unknown', 0) + 1
                continue
            out.append(trade)
        return out, drops

    def fetch_record(self, address: str) -> Optional[WalletRecord]:
        """MEASURED record for one wallet, or None if it cannot be measured.

        Two paths, tried in that order:

          1. **Explicit settlement keys on the row.** If a future feed ever ships
             `won` or `realized_pnl`, `record_from_rows` reads it directly and no
             resolution lookups are needed. The live Data API does NOT ship them.
          2. **Score the fills ourselves** against `market_resolution`. This is
             the path that runs against the live API today.

        Path 1 is tried first on purpose: a feed that states the settlement
        outright is better evidence than our reconstruction of it, and would also
        capture SELL round trips that path 2 deliberately refuses to score.

        None means we could measure NOTHING, never "this wallet is bad"
        (convention 11).
        """
        rows = self._fetch(address, self.record_limit)
        if rows is None:
            self.stats['record_fetch_failed'] = (
                self.stats.get('record_fetch_failed', 0) + 1)
            return None
        direct = record_from_rows(rows, address)
        if direct is not None:
            self.stats['record_from_settlement_keys'] = (
                self.stats.get('record_from_settlement_keys', 0) + 1)
            return direct
        if self.resolver is None:
            self.stats['record_no_resolver'] = (
                self.stats.get('record_no_resolver', 0) + 1)
            return None
        record, drops = record_from_trade_rows(rows, address, self.resolver)
        for key, n in (drops or {}).items():
            self.stats['row_drop_' + key] = self.stats.get(
                'row_drop_' + key, 0) + n
        if record is None:
            self.stats['record_nothing_scorable'] = (
                self.stats.get('record_nothing_scorable', 0) + 1)
        else:
            self.stats['record_from_resolutions'] = (
                self.stats.get('record_from_resolutions', 0) + 1)
        return record


class SmartMoneyCopy(PolymarketStrategy):
    """Mirror a fresh BUY from a wallet whose record we have MEASURED.

    Kill condition: trailing-30 copied-trade win rate below 50% once 30 copied
    trades exist, scored by `backtest/polymarket_harness.py` on the
    `PM_smart_money_copy` population alone. See the module docstring.
    """

    strategy_name = 'PM_smart_money_copy'
    paper_mode = PAPER_MODE

    #: EVERY market type the router knows. This is the one strategy in the
    #: package with no universe of its own: the tracked WALLET picks the market
    #: and we read their tape, so narrowing this would discard whatever the
    #: whale did outside a universe we guessed for them. On the live tape,
    #: 2026-08-18, that would have been 47% of their fills. See the module
    #: docstring section "EVERY MARKET TYPE".
    #:
    #: `MARKET_TYPES` rather than a list literal, on purpose: a seventh type
    #: added to `base` is a type this strategy already supports by the same
    #: argument, and a literal here would silently not join it.
    supported_market_types = MARKET_TYPES

    #: Holds to resolution, like every strategy here except the fair-value
    #: family. The shadow loop reads this to decide whether to poll an exit.
    manages_exits = False

    #: How every market this strategy ever sees was found. Stamped on every row
    #: so a smart-money sample can be separated from a self-selected one later
    #: (see MARKET_TYPE_SMART_MONEY in `base`), rather than averaged into a
    #: pooled number that describes neither population.
    discovery_path = 'followed_a_tracked_wallet'

    def __init__(self, trade_feed=None,
                 wallets: Optional[Dict[str, Optional[str]]] = None,
                 min_win_rate: float = MIN_WIN_RATE,
                 min_trade_count: int = MIN_TRADE_COUNT,
                 max_trade_age_sec: float = MAX_TRADE_AGE_SEC,
                 max_trade_age_by_market_type: Optional[
                     Dict[str, float]] = None,
                 max_shares: int = MAX_SHARES,
                 max_notional_usdc: float = MAX_NOTIONAL_USDC,
                 min_shares: int = MIN_SHARES,
                 max_entry_price: float = MAX_ENTRY_PRICE,
                 min_book_depth_shares: float = MIN_BOOK_DEPTH_SHARES,
                 depth_band: float = DEPTH_BAND,
                 client=None, resolver=None,
                 record_ttl_sec: float = DEFAULT_RECORD_TTL_SEC,
                 clock=None):
        #: Injected the way shadow_loop injects `candle_source`. A default is
        #: built only when nothing was supplied, so a test that passes a stub
        #: can never fall through to the network.
        self.trade_feed = (trade_feed if trade_feed is not None
                           else WalletTradeFeed(client=client,
                                                resolver=resolver))
        self.wallets = dict(TRACKED_WALLETS if wallets is None else wallets)
        self.min_win_rate = min_win_rate
        self.min_trade_count = min_trade_count
        self.max_trade_age_sec = max_trade_age_sec
        #: Ships empty. See MAX_TRADE_AGE_SEC_BY_MARKET_TYPE - the mechanism
        #: exists so a measured per-type horizon has somewhere to go; nothing
        #: is invented to fill it.
        self.max_trade_age_by_market_type = dict(
            MAX_TRADE_AGE_SEC_BY_MARKET_TYPE
            if max_trade_age_by_market_type is None
            else max_trade_age_by_market_type)
        self.max_shares = max_shares
        self.max_notional_usdc = max_notional_usdc
        self.min_shares = min_shares
        self.max_entry_price = max_entry_price
        self.min_book_depth_shares = min_book_depth_shares
        self.depth_band = depth_band

        #: The SLOW CADENCE the record gate runs on. Rebuilding a record costs
        #: a 500-row read plus a CLOB lookup per distinct unresolved market;
        #: doing it per 5-second poll would spend the entire latency budget of
        #: a strategy gated on a 120-second freshness window. Not infinite
        #: either - see DEFAULT_RECORD_TTL_SEC.
        self.record_ttl_sec = float(record_ttl_sec)
        #: Wall clock, injectable so a ttl test does not have to sleep. NOT the
        #: window clock: the record cadence is about how often we hit the API,
        #: which is a real-time question, while the staleness gate is about the
        #: observation and correctly uses `ctx`.
        self._clock = clock or time.time
        #: address -> (computed_at, WalletRecord or None). None is cached too,
        #: so an unmeasurable wallet is not retried every cycle - but it ages
        #: out on the same ttl, so a wallet whose markets have since resolved
        #: becomes measurable without a restart.
        self._records: Dict[str, Tuple[float, Optional[WalletRecord]]] = {}
        self.record_stats: Dict[str, int] = {
            'record_cache_hit': 0, 'record_cache_miss': 0,
            'record_cache_expired': 0, 'record_fetch_raised': 0,
        }
        #: trade ids already copied. See the dedupe section of the docstring.
        self._copied_ids: List[str] = []
        self._copied_set = set()

    # -- wallet resolution --------------------------------------------------

    def resolve(self) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, int]]:
        return resolve_tracked_wallets(self.wallets)

    # -- dedupe -------------------------------------------------------------

    def already_copied(self, trade_id: str) -> bool:
        return trade_id in self._copied_set

    def _note_copied(self, trade_id: str) -> None:
        if trade_id in self._copied_set:
            return
        self._copied_ids.append(trade_id)
        self._copied_set.add(trade_id)
        while len(self._copied_ids) > COPIED_IDS_KEPT:
            self._copied_set.discard(self._copied_ids.pop(0))

    # -- record lookup ------------------------------------------------------

    def record_for(self, address: str) -> Optional[WalletRecord]:
        """Cached MEASURED record, or None when it could not be measured.

        Expiry is checked on READ and the entry is EVICTED here, so a stale
        record cannot be served by some later code path that forgot to check
        its age - the same discipline `engine/feeds/noaa_weather.py` uses.
        """
        now = self._clock()
        entry = self._records.get(address)
        if entry is not None:
            computed_at, cached = entry
            if now - computed_at < self.record_ttl_sec:
                self.record_stats['record_cache_hit'] += 1
                return cached
            del self._records[address]
            self.record_stats['record_cache_expired'] += 1
        else:
            self.record_stats['record_cache_miss'] += 1

        record = None
        fetch = getattr(self.trade_feed, 'fetch_record', None)
        if callable(fetch):
            try:
                record = fetch(address)
            except Exception as exc:                    # noqa: BLE001
                # A feed that raises is a feed that failed. Never a wallet with
                # a bad record.
                logger.warning('record fetch raised for %s: %s', address, exc)
                self.record_stats['record_fetch_raised'] += 1
                record = None
        self._records[address] = (now, record)
        return record

    # -- market matching ----------------------------------------------------

    @staticmethod
    def market_tokens(market) -> set:
        outcomes = getattr(market, 'outcomes', ()) or ()
        return {str(o.token_id) for o in outcomes if getattr(o, 'token_id', None)}

    def matches_market(self, trade: WalletTrade, market) -> bool:
        """Is this fill in the market we are looking at?

        Matched on token id OR slug OR condition id. Token id is the strongest
        of the three and slug is the weakest, but the Data API is not
        consistent about which fields it populates, so all three are tried and
        `match_field` records which one answered. A row that matches on nothing
        is not our market.
        """
        return self.match_field(trade, market) is not None

    def match_field(self, trade: WalletTrade, market) -> Optional[str]:
        if market is None:
            return None
        if trade.token_id and trade.token_id in self.market_tokens(market):
            return 'token_id'
        slug = getattr(market, 'slug', None)
        if trade.market_slug and slug and trade.market_slug == slug:
            return 'market_slug'
        cond = getattr(market, 'condition_id', None)
        if trade.condition_id and cond and trade.condition_id == cond:
            return 'condition_id'
        return None

    @staticmethod
    def clock(ctx: MarketContext) -> Optional[float]:
        """Absolute seconds for this observation, or None.

        Derived from the window's own timestamp rather than the wall clock, so
        a decision is reproducible from a logged context and a test does not
        have to mock `time`. Without it the staleness gate cannot run, which is
        a refusal, not a pass.
        """
        if ctx.seconds_into_window is None:
            return None
        return float(ctx.window_ts) + float(ctx.seconds_into_window)

    #: The two ways an observation time can be arrived at. They are different
    #: EVIDENCE, not two spellings of one number: the window-derived one is
    #: replayable from a logged context, the wall-clock one is not. Named
    #: constants so the pair can never collapse into one counter (convention 20).
    CLOCK_FROM_WINDOW = 'window_offset'
    CLOCK_FROM_WALL = 'wall_clock'
    CLOCK_UNAVAILABLE = 'unavailable'

    def observation_clock(self, ctx: MarketContext
                          ) -> Tuple[Optional[float], str]:
        """`(absolute seconds, source)` for this observation.

        Returns `(None, CLOCK_UNAVAILABLE)` when neither source can answer, and
        the caller then refuses with `no_trade_clock`.

        **This method is the whole crypto-only coupling that used to be in this
        file, and the reason it is a method now.** `ctx.seconds_into_window`
        exists on a `crypto_updown` context and on nothing else by contract, so
        the old window-only derivation returned None on every weather, sports,
        political and event market, and this strategy answered `no_trade_clock`
        forever on all of them. That reads in a log exactly like a wiring bug we
        had already fixed.

        Precedence, and why this order:

          1. **The window offset, whenever the context carries one**, crypto or
             not. It is the reproducible one, and `build_weather_context` shows
             a non-crypto context can legitimately supply it (`window_ts` is the
             poll second and the offset its fraction). Preferring it means the
             crypto population's decisions are derived EXACTLY as they were
             before this change - not approximately, identically.
          2. **The injected wall clock, on non-crypto types only.** For a market
             with no window there is no other honest reading of "now", and the
             trade timestamps we compare against are absolute epochs anyway.

        A `crypto_updown` context missing its offset does NOT fall through to
        the wall clock. That combination is a broken crypto context, and quietly
        substituting a different clock would repair a wiring bug into a
        plausible-looking decision. It keeps refusing, which is what
        `no_trade_clock` has always meant.
        """
        windowed = self.clock(ctx)
        if windowed is not None:
            return windowed, self.CLOCK_FROM_WINDOW
        if getattr(ctx, 'is_crypto_window', True):
            return None, self.CLOCK_UNAVAILABLE
        return float(self._clock()), self.CLOCK_FROM_WALL

    def max_age_for(self, market_type: str) -> float:
        """Freshness horizon for one market type, in seconds.

        Falls back to the single `max_trade_age_sec` because the override table
        ships EMPTY. The fallback is not a placeholder for a number somebody
        forgot: the horizon is a latency budget and the poll cadence is the same
        on every type, so one number is the defensible default until a per-type
        one is measured.
        """
        try:
            return float(self.max_trade_age_by_market_type[market_type])
        except (KeyError, TypeError, ValueError):
            return float(self.max_trade_age_sec)

    # -- entry --------------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        # RAISES on a type we did not declare, and cannot fire here because we
        # declare all of them. Called anyway: if `supported_market_types` is
        # ever narrowed, the enforcement must already be wired (convention 22 -
        # a claim in a docstring is not a wiring test).
        self.assert_supports(ctx)

        slug = getattr(ctx.market, 'slug', None)
        market_type = getattr(ctx, 'market_type', MARKET_TYPE_CRYPTO_UPDOWN)
        resolved, statuses, counts = self.resolve()

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            # Stamped on EVERY row, skips included. Nothing downstream may pick
            # a vendor number up off a log and read it as a measurement, and
            # nothing may read a handle as if it were an address.
            feats.setdefault('claimed_win_rates_are_unverified_vendor_numbers',
                             True)
            feats.setdefault('wallet_handles_are_not_addresses', True)
            feats.setdefault('trade_count_is_attempts_not_fills', True)
            feats.setdefault('exits_before_resolution', False)
            # -- market-type stamps, on EVERY row including every skip --------
            #
            # `market_type` is what the ROUTER handed us. `underlying_slug_
            # market_type` is what the market's own slug says. They are allowed
            # to differ and the difference is the point: a wallet-discovered BTC
            # window is routed as `smart_money` and its slug still says
            # `crypto_updown`. Recording only one of the two would make a
            # smart-money sample indistinguishable from a self-selected one, or
            # would lose the venue. Both, never one merged field.
            feats.setdefault('market_type', market_type)
            feats.setdefault('underlying_slug_market_type',
                             underlying_market_type(slug))
            feats.setdefault('market_type_cannot_split_sports_or_political',
                             True)
            feats.setdefault('discovery_path', self.discovery_path)
            feats.setdefault('sample_is_wallet_discovered',
                             market_type == MARKET_TYPE_SMART_MONEY)
            feats.setdefault('supported_market_types',
                             list(self.supported_market_types))
            feats.setdefault('tracked_wallets', len(self.wallets))
            feats.setdefault('wallets_resolved', counts[RESOLVED])
            feats.setdefault('wallets_unresolved_prefix_only',
                             counts[UNRESOLVED_PREFIX_ONLY])
            feats.setdefault('wallets_unresolved_no_address',
                             counts[UNRESOLVED_NO_ADDRESS])
            feats.setdefault('wallet_resolution_census_balances',
                             sum(counts.values()) == len(self.wallets))
            feats.setdefault('wallet_statuses', dict(statuses))
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market')

        if not resolved:
            # The shipped state. Every handle counted, every one categorised,
            # nothing silently dropped.
            return decide('SKIP', 'wallet_address_unresolved',
                          unresolved_handles=sorted(
                              h for h, s in statuses.items() if s != RESOLVED))

        now = self.clock(ctx)
        if now is None:
            # The staleness gate is the whole difference between copying a
            # whale and copying the price they already moved. Without a clock
            # it cannot run and this strategy must not guess.
            return decide('SKIP', 'no_trade_clock')

        # -- pull, counting every wallet's outcome separately ---------------
        trades: List[WalletTrade] = []
        feed_failures: List[str] = []
        row_drops: Dict[str, int] = {}
        wallets_read = 0
        for handle, address in sorted(resolved.items()):
            try:
                fetched, drops = self.trade_feed.fetch_trades(handle, address)
            except Exception as exc:                    # noqa: BLE001
                logger.warning('trade feed raised for %s: %s', handle, exc)
                fetched, drops = None, {}
            for key, n in (drops or {}).items():
                row_drops[key] = row_drops.get(key, 0) + n
            if fetched is None:
                feed_failures.append(handle)
                continue
            wallets_read += 1
            trades.extend(fetched)

        feats = {
            'wallets_queried': len(resolved),
            'wallets_read': wallets_read,
            'wallets_feed_failed': len(feed_failures),
            'feed_failed_handles': sorted(feed_failures),
            'row_drops': dict(row_drops),
            'trades_seen': len(trades),
        }

        if wallets_read == 0:
            # Could not run. Never "we looked and the whales were quiet."
            return decide('SKIP', 'wallet_feed_unavailable', **feats)

        if not trades:
            return decide('SKIP', 'no_tracked_wallet_trades', **feats)

        in_market = [t for t in trades if self.matches_market(t, ctx.market)]
        feats['trades_in_this_market'] = len(in_market)
        if not in_market:
            return decide('SKIP', 'no_trade_in_this_market', **feats)

        buys = [t for t in in_market if t.is_buy]
        feats['buys_in_this_market'] = len(buys)
        if not buys:
            # A SELL is not a mirrorable BUY and inverting it into one would be
            # a different strategy with a different thesis.
            return decide('SKIP', 'no_tracked_wallet_buy', **feats)

        # Newest first. A trade with no timestamp sorts last and is refused by
        # the staleness gate rather than treated as brand new.
        buys.sort(key=lambda t: (t.ts is not None, t.ts or 0.0), reverse=True)

        fresh = [t for t in buys if not self.already_copied(t.trade_id)]
        feats['buys_already_copied'] = len(buys) - len(fresh)
        if not fresh:
            return decide('SKIP', 'already_copied_this_trade', **feats)

        ages = [t.age_sec(now) for t in fresh]
        feats['youngest_trade_age_sec'] = next(
            (round(a, 1) for a in ages if a is not None), None)
        feats['buys_without_timestamp'] = sum(1 for a in ages if a is None)

        timely = [t for t, a in zip(fresh, ages)
                  if a is not None and a <= self.max_trade_age_sec]
        feats['max_trade_age_sec'] = self.max_trade_age_sec
        feats['buys_fresh_enough'] = len(timely)
        if not timely:
            return decide('SKIP', 'copied_trade_stale', **feats)

        # -- the record gate ------------------------------------------------
        #
        # THREE refusal causes, never two. They are different facts and the
        # response to each is different (conventions 11 and 20):
        #
        #   unmeasured    we scored NOTHING for this wallet. NOT_TESTED. The fix
        #                 is a data one - a resolver, a longer tape, a working
        #                 feed.
        #   insufficient  we scored some, but not more than min_trade_count. Also
        #                 NOT_TESTED: a 75% on 8 trades is a shrug (convention 7).
        #                 The fix is to wait, or to pull more history.
        #   below         we scored MORE than min_trade_count and the win rate is
        #                 not above the bar. This one is a RESULT. Pooling it
        #                 with either of the two above would let a genuine
        #                 rejection be read as a plumbing problem, and vice
        #                 versa.
        candidate = None
        record = None
        unmeasured = 0
        insufficient_sample = 0
        below_threshold = 0
        scored_counts: List[int] = []
        for trade in timely:
            rec = self.record_for(trade.address)
            if rec is None or not rec.measured:
                unmeasured += 1
                continue
            scored_counts.append(rec.trades)
            if not rec.has_sample(self.min_trade_count):
                insufficient_sample += 1
                continue
            if not rec.beats(self.min_win_rate):
                below_threshold += 1
                continue
            candidate, record = trade, rec
            break

        feats['wallets_record_unmeasured'] = unmeasured
        feats['wallets_record_insufficient_sample'] = insufficient_sample
        feats['wallets_record_below_threshold'] = below_threshold
        feats['scored_trades_per_wallet'] = sorted(scored_counts, reverse=True)
        feats['min_win_rate'] = self.min_win_rate
        feats['min_trade_count'] = self.min_trade_count

        if candidate is None:
            causes = {'unmeasured': unmeasured,
                      'insufficient': insufficient_sample,
                      'below': below_threshold}
            present = sorted(k for k, v in causes.items() if v)
            if present == ['below']:
                # A measured record that fails is a RESULT. It must never share
                # a bucket with a record we could not measure at all.
                return decide('SKIP', 'wallet_record_below_threshold', **feats)
            if present == ['unmeasured']:
                return decide('SKIP', 'wallet_record_unmeasured', **feats)
            if present == ['insufficient']:
                return decide('SKIP', 'wallet_record_insufficient_sample',
                              **feats)
            if present == ['below', 'unmeasured']:
                # Kept as its own reason for continuity with every row already
                # logged under this name.
                return decide('SKIP',
                              'wallet_record_mixed_unmeasured_and_below',
                              **feats)
            # Any other combination. Named rather than picked by a tiebreak, and
            # the three counters above still carry the breakdown.
            return decide('SKIP', 'wallet_record_mixed_causes', **feats)

        side = candidate.outcome_side
        feats.update({
            'copied_handle': candidate.handle,
            'copied_address': candidate.address,
            'copied_trade_id': candidate.trade_id,
            'copied_trade_age_sec': round(candidate.age_sec(now) or 0.0, 1),
            'copied_trade_price': candidate.price,
            'copied_trade_size': candidate.size,
            'copied_size_is_theirs_not_ours': True,
            'match_field': self.match_field(candidate, ctx.market),
            'outcome_side': side,
            'wallet_win_rate_measured': record.win_rate,
            'wallet_trades_measured': record.trades,
            'wallet_record_source': record.source,
            # THEIR hold-to-resolution pnl at THEIR sizes over the scored rows.
            # Reported, never gated on - see the WalletRecord field comment.
            'wallet_record_pnl_usdc': record.pnl_usdc,
            'wallet_record_is_hold_to_resolution_not_their_realized_pnl': True,
            # The break-even the win rate above has to be read against. On the
            # live tape these two are within four points of each other for
            # every tracked wallet - see WalletRecord.edge_over_breakeven.
            'wallet_mean_entry_price': record.mean_entry_price,
            'wallet_edge_over_breakeven': record.edge_over_breakeven,
            # Confidence IS the wallet's measured win rate. A measurement of
            # THEIR record, not a probability that OUR copy wins: we enter
            # later, at a worse price, with none of their other positions.
            'confidence': round(record.win_rate or 0.0, 6),
            'confidence_is_their_measured_win_rate_not_ours': True,
        })

        book = ctx.book(side)
        if book is None:
            return decide('SKIP', 'no_orderbook', **feats)
        best_ask = book.best_ask
        feats['best_ask'] = best_ask
        if best_ask is None:
            return decide('SKIP', 'no_asks', **feats)

        feats['max_entry_price'] = self.max_entry_price
        if best_ask > self.max_entry_price:
            return decide('SKIP', 'ask_above_max_entry_price', **feats)

        cap = self.max_entry_price
        depth_limit = round(best_ask + self.depth_band, 6)
        depth = book.ask_depth(depth_limit)
        feats['depth_band'] = self.depth_band
        feats['ask_depth_within_band'] = depth
        feats['min_book_depth_shares'] = self.min_book_depth_shares
        if depth < self.min_book_depth_shares:
            return decide('SKIP', 'insufficient_book_depth', **feats)

        # OUR sizing. Size DOWN to the notional cap rather than letting the
        # adapter reject the order: "20 shares does not fit in $10 at 60c" and
        # "the risk gate blocked this" are different facts.
        affordable = int(math.floor(self.max_notional_usdc / best_ask + 1e-9))
        shares = min(self.max_shares, affordable)
        feats['max_shares'] = self.max_shares
        feats['affordable_shares_at_ask'] = affordable
        feats['shares'] = shares
        feats['shares_capped_by_notional'] = shares < self.max_shares
        if shares < self.min_shares:
            # Could not run, did not lose (convention 11).
            return decide('SKIP', 'unsizable_at_notional_cap', **feats)

        effective = effective_ask_for(book, shares, cap)
        feats['effective_ask'] = (None if effective is None
                                  else round(effective, 4))
        if effective is None:
            return decide('SKIP', 'unfillable_at_cap', **feats)
        if effective > cap:
            # walk_book cannot return this under the same limit, but a silent
            # regression on the price cap would be invisible.
            return decide('SKIP', 'effective_ask_above_cap', **feats)

        # On a binary the break-even win rate IS the premium paid. Reported so
        # nobody has to recompute it from the copied wallet's number, which
        # describes a different trade at a different price.
        feats['breakeven_win_rate'] = round(effective, 4)
        feats['notional_usdc'] = round(shares * effective, 4)
        feats['limit_price'] = cap

        self._note_copied(candidate.trade_id)

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=side,
                                limit_price=cap,
                                order_type='taker',
                                shares=shares,
                                expected_price=effective)],
                      **feats)
