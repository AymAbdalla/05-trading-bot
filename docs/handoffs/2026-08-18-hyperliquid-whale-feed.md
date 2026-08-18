# Handoff: Hyperliquid whale-position feed

**By:** Cody, 2026-08-18 ~05:35 local (09:35 UTC)
**Scope:** build `engine/feeds/hyperliquid_client.py` to feed a future `near_liq_trigger`.
**Status:** built, tested (70 offline tests), proven on live data. Nothing committed.

---

## Files created (all NEW, nothing existing was modified)

| file | what |
|---|---|
| `engine/feeds/hyperliquid_client.py` | the poller (918 lines) |
| `engine/feeds/__init__.py` | package init |
| `tests/test_hyperliquid_client.py` | 70 offline tests |
| `tests/fixtures/hyperliquid_clearinghouse_state.json` | REAL captured response |
| `run_hyperliquid_feed.sh` | runner, matches `run_polymarket_shadow.sh` style |
| `research/hyperliquid/leaderboard_wallets.json` | wallet cache (generated) |

**`db/schema.sql` was NOT modified — the permission layer refused the edit.**
The DDL lives in `SCHEMA_SQL` in the module and is applied by
`ensure_schema()`. **Someone must paste it into `db/schema.sql`**, or a db
rebuilt from schema.sql alone will lack the table until the module runs.

---

## The hard question: is global whale discovery possible?

**Yes, but not through `/info`.** Verified by probe, not by reading docs
(WebFetch/WebSearch were both denied this session, so every claim below is
empirical):

| request | result |
|---|---|
| `{"type":"meta"}` | 200, works |
| `{"type":"allMids"}` | 200, works |
| `{"type":"metaAndAssetCtxs"}` | 200, works |
| `{"type":"openOrders","user":...}` | 200, works |
| `{"type":"userFills","user":...}` | 200, works |
| `{"type":"clearinghouseState","user":...}` | 200, works |
| `{"type":"clearinghouseState"}` (no user) | **422** |
| `{"type":"userState","user":...}` | **422 — no such type** |
| `{"type":"allPositions"}` | **422 — no such type** |
| `{"type":"leaderboard"}` | **422 — no such type** |
| `{"type":"liquidatable"}` | 200 but returns `[]` — valid type, no content |

So there is no `/info` call for "all whale positions". Discovery works via a
**different host**: `GET https://stats-data.hyperliquid.xyz/Mainnet/leaderboard`
returns 200 and **41,975 rows** of `{ethAddress, accountValue}`. Two-stage:
leaderboard -> per-address `clearinghouseState`. That is what shipped.

### Three findings that matter more than "it works"

1. **The leaderboard is undocumented.** It is the S3 bucket the frontend reads,
   not part of the API. It can vanish. On failure the module falls back to the
   on-disk cache, then to `--wallets`, and logs a FAILURE (convention 11).

2. **`accountValue` is a stale field and the top of the list is garbage.** The
   ten highest-`accountValue` addresses returned **zero** positions between
   them. For the top address the leaderboard claimed **$14,113,335,843** while
   `clearinghouseState` reported **$10,141.13** in the same minute. My first
   `--once` run used `--top-n 10` and correctly reported `seen=0`; that was not
   a bug, those wallets are genuinely empty ghosts. Real whales start below
   rank 10, so `DEFAULT_TOP_N = 25`.

3. **Therefore this is a WATCHLIST, not a census.** Ranking by a stale
   account-value field is not a random sample of large positions. The module
   never claims to see all whales; the log says `wallets=N`. **Sampling bias is
   real and unquantified — this is the open question for Raven/Aym below.**

---

## Live proof (`--once`, real API, real DB)

```
HL POLL ts=1787045403 wallets=25 ok=25 empty=19 failed=0 | positions seen=139
kept=13 skipped=126 (scope=124 below_min=2 missing=0 unparseable=0)
| rows_written=13
```

Identity holds: 139 − 126 = 13. Largest captured: a **$73.7M BTC short**
(entry 63236.6, liq 102339.61, 5x). **Two rows carried a real NULL
`liq_price`** (both BTC longs) — preserved as NULL, never coerced to 0.0.

19 of 25 wallets were fetched fine and genuinely held nothing. That is counted
as `empty`, separately from `failed`, so "no positions" can never be confused
with "request died" (convention 11).

## Tests

`env -u PYTHONPATH python3 -m pytest tests/test_hyperliquid_client.py -q`
-> **70 passed** in 0.17s. Fully offline, hand-written fake session, no network.

Full suite: **1,484 passed, 2 failed, 1 skipped**. Both failures
(`test_liquidation_recorder.py`, `test_polymarket_strategies.py`) are **other
sessions' files and both PASS on re-run** — the documented mid-edit
phenomenon. `test_dashboard_charts.py` still needs `.venv` for plotly
(pre-existing).

---

## Concurrency

`db/trading.db` journal_mode is **read and logged, never set**.
`PRAGMA busy_timeout=5000`, one short `executemany` transaction per poll.
**Shadow loop 27030 and graveyard sweep 18543 were both alive at start and are
both still alive.** Neither was touched.

**A continuous poller is running that I did NOT start: PID 37578** (`-m
engine.feeds.hyperliquid_client`, no args, so the 30s loop), launched by
another Cody session via my runner. Per convention 21 I left it alone. It is
writing 13 rows per 30s = **~37k rows/day**. That belongs with the existing
`signals` retention question.

Another session has already written `strategies/polymarket/near_liq_trigger.py`
reading this table. I did not touch it.

---

## Open questions for Raven / Aym

1. **Paste `SCHEMA_SQL` into `db/schema.sql`.** Blocked by permissions here.
2. **Sampling bias.** `--top-n 25` is a coverage guess. Nobody has measured
   what fraction of Hyperliquid's large positions it actually sees. Until
   someone does, no breadth claim from this table is defensible.
3. **Retention.** ~37k rows/day, unbounded, from a poller already running.
4. **Rate limits are UNVERIFIED.** Docs were unreachable (web access denied).
   40 sequential calls drew zero 429s and defaults are conservative, but the
   real published limit is not known. Backoff is implemented and tested.
5. **Should the feed obey HALT?** Currently no, by design — halting trading is
   not a reason to stop recording. Needs a ruling, and a D-number either way.
6. **`liquidatable` returns `[]`.** Valid type, empty content. Might be a
   better whale source than the leaderboard if it ever populates. Unexplored.

**No D-numbers were invented.** DECISIONS.md stops at D-283 (convention 24).
