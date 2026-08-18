# Handoff: liquidation/Hyperliquid feeds + fair_value_arb variants

**Session:** Cody, 2026-08-18 ~05:13-05:50
**Author:** parent session PID 36157, four subagents
**Status:** built, tested, NOTHING COMMITTED. Tree left for review.
**Mode:** paper / research only. No keys, no wallet, no orders.

---

## Read this first: the tree moved under us

There were **4** concurrent `claude -p` sessions when this work started and **8**
by the time it ended. Convention 21 was not theoretical today.

- `strategies/polymarket/__init__.py` `build_strategies()` went **8 -> 11** (ours)
  **-> 15** (another session, mid-verification) while this session was checking
  its own work. The extra four are `PM_fair_value_arb_inverse`,
  `PM_liq_cascade_chaser`, `PM_small_liq_continuation`, `PM_near_liq_trigger`.
  None of those are ours; we did not touch them.
- Another session was given the **same** liquidation-recorder brief and rewrote
  `engine/feeds/__init__.py` and `tests/test_liquidation_recorder.py` under us.
- Another session **started both feeds as long-running processes** (PIDs 37491,
  37578). We did not start them and did not kill them.

**Do not read any count in this document as stable.** Re-derive it.

---

## Task 1: data feeds

### `engine/feeds/liquidation_recorder.py` (+ `run_liquidation_recorder.sh`)

Binance `!forceOrder@arr` + Bybit v5. Writes table `liquidations` in
`db/trading.db`, exact schema as specified, plus `idx_liquidations_ts` and
`idx_liquidations_symbol_ts`.

**Bybit topic was measured, not assumed.** Probed live on one connection:

| topic | response |
|---|---|
| `liquidation.BTCUSDT` | `{"success":false,"ret_msg":"error:handler not found..."}` |
| `allLiquidation.BTCUSDT` | `{"success":true}` |

So it subscribes `allLiquidation.<SYMBOL>`. Both message shapes are parsed
(legacy object, current list); `--bybit-topic-prefix` flips back.

**Side semantics.** The venue puts the FORCED ORDER side on the wire; the table
stores the side that was LIQUIDATED, which is the inverse. Binance `o.S=SELL`
-> a long was liquidated. Covered by tests in both directions on both venues.

**Tests: 72 passed, 1.03s, fully offline.**

#### What is NOT proven, and it matters

1. **Zero liquidation events have ever been parsed from live data on either
   venue.** Both parse paths are proven only against synthetic frames.
2. **Binance's data plane is dead on this host.** Handshake and control plane
   work (`LIST_SUBSCRIPTIONS` confirms the subscription), but `!forceOrder@arr`
   delivered **0 frames in 6 minutes** and a `btcusdt@aggTrade` control stream
   delivered **0 messages in 45s** — a stream that should fire many times a
   second. That is geo-blocking, not a code bug. **Binance will contribute
   nothing from this machine until it is routed differently.**
3. Bybit's socket is healthy — subscribe ACK plus a working 20s heartbeat
   (18 frames = 1 ack + 17 pongs) — but the market was quiet and produced no
   liquidations in the observation window. A `tickers.BTCUSDT` control stream
   delivered 310 messages in 45s, so the socket is fine.
4. **Bybit's side field is UNVERIFIED against documentation** (`WebFetch` was
   denied). Empirical cross-check to run once tape exists: during a dump both
   venues must show the same dominant liquidated side. If they disagree, flip
   `_BYBIT_ORDER_SIDE_TO_LIQUIDATED` first.
5. Known `id` collision, stated not hidden: `!forceOrder` carries no order id,
   so two distinct liquidations sharing venue/symbol/side/price/qty/millisecond
   collapse into one row. `duplicates` in the heartbeat has a floor above zero
   and is not pure reconnect replay.

**Live table state: `liquidations` = 0 rows.** Convention 11: that is
NOT_TESTED, not "no liquidations happened."

### `engine/feeds/hyperliquid_client.py` (+ `run_hyperliquid_feed.sh`)

Writes `hyperliquid_positions`. Working and accumulating: **234 rows** across
BTC/ETH/SOL at time of writing, from **5 distinct wallets**.

**There is no global whale endpoint on `/info`** — verified by probe, not by
docs (WebFetch/WebSearch both denied):

- Work: `meta`, `allMids`, `metaAndAssetCtxs`, `openOrders`, `userFills`,
  `clearinghouseState` (requires `user`)
- 422 / do not exist: `userState`, `allPositions`, `leaderboard`,
  `clearinghouseState` without `user`
- `liquidatable` is valid but returns `[]`

Discovery is therefore two-stage, via
`GET https://stats-data.hyperliquid.xyz/Mainnet/leaderboard` (200, **41,975
addresses**), then `clearinghouseState` per address. No addresses were invented.

**Three caveats that outrank "it works":**

1. That leaderboard is **undocumented** — the frontend's S3 bucket, not the API.
   It can vanish. Module degrades cache -> `--wallets` and logs the failure.
2. **The top of the leaderboard is garbage.** `accountValue` is stale; the top
   10 addresses returned **zero** positions. For the top one the leaderboard
   claimed **$14,113,335,843** while `clearinghouseState` said **$10,141.13** in
   the same minute. Hence `DEFAULT_TOP_N = 25`.
3. **This is a watchlist, not a census.** Sampling bias is real and
   unquantified. OPEN QUESTION for Raven/Aym, not a solved problem.

Real `--once` output, identity holds (139 - 126 = 13):

```
HL POLL ts=1787045403 wallets=25 ok=25 empty=19 failed=0 | positions seen=139
kept=13 skipped=126 (scope=124 below_min=2 missing=0 unparseable=0) | rows_written=13
```

Largest real position captured: a **$73.7M BTC short**, entry 63236.6, liq
102339.61. `liq_price` NULLs are preserved, never coerced to 0 (24 of 60 BTC
rows). **Tests: 70 passed, offline.** Rate limits unverified (docs unreachable);
40 sequential calls drew zero 429s, backoff implemented and tested.

### The premise correction

The three "blocked strategies" **did not exist as code** when this session
started. `liq_cascade_chaser`, `small_liq_continuation` and `near_liq_trigger`
appeared only in `strategies/proposals/003-liq-cascade-spot-long.md`
(status `PROPOSED`) and as a source citation in `agents/forge_candidates.py`.

That proposal names this exact blocker itself: *"Historical liquidation data
does not exist on any public REST endpoint... requires standing up a
liquidation recorder and WAITING for the data to accumulate."* So the feeds are
the right unblock. Its kill condition needs **50M USD trailing-2-min clusters
and >=200 signals** — that is the bar the recorder has to feed.

**A concurrent session has since written all three strategy files and
registered them.** They are wired to a table with **0 rows**. Whatever they
produce today is NOT_TESTED by construction.

---

## Task 2: fair_value_arb post-mortem + variants

### Subagent C: the post-mortem is the most important output of this session

`agents/forge.py --shadow-results` **does exist** (line 662) and ran clean, but
its shadow view is aggregate across all PM strategies with no per-strategy P&L,
so it could not answer the question. New read-only script:
`backtest/analyze_shadow_fair_value_arb.py` (opens `mode=ro`, never writes).

Snapshot 2026-08-18T09:24:51Z, 43 positions, max(opened_ts) 04:21:00Z.

| metric | PM_fair_value_arb | temporal_arb | corridor_pair_live |
|---|---|---|---|
| closed / open | **33 / 0** | 8 / 0 | 2 / 0 |
| win rate | **21.2%** (7/33) | 0.0% | 100% |
| avg win / avg loss | +0.961 / -1.337 | - | - |
| profit factor | **0.194** | 0 | - |
| gross / net | -28.034 / **-28.034** | -6.00 | +3.95 |
| **fees** | **0.000** | 0.000 | 0.000 |

**Fees do not explain the loss — the paper adapter applies no fee model at all.
-28.03 is a pre-fee number. Reality can only be equal or worse.**

**The headline number:** designed geometry (+1c / -3c) needs a **75.0%**
break-even win rate. Observed **21.2%**. One-sided p = 1.4e-10.

**The diagnosis — the stop sits inside the strategy's own spread.** Entry is at
the ASK; both target and stop are measured against the BID. Under a symmetric
null, gambler's ruin predicts 8.2 stops in 33; observed **26**
(P(X>=26 | p=0.25) = 1.4e-10). 22 of 33 closed within one 5s poll cycle.
Median hold **6.2s** — so `TIME_STOP_SEC=60` never binds, and the 30-120s
convergence thesis was **never given a chance to be tested**.

**The declared stop is fiction.** Exits sell at `URGENT_SELL_LIMIT=0.0` and walk
the bid down, so `max_loss` controls *when the stop triggers, not what it costs*.
Realised: **+5.00c per win (5.0x design), -6.98c per loss (2.33x design), worst
-16c**. 20 of 26 losers exceeded the declared 3c. Realised break-even is
therefore **~58.3%**, and the strategy is at 21.2% — it fails that too.

**Noise verdict.** t = **-4.10**; bootstrap 95% CI of the mean
**[-1.244, -0.446]** (20k iters, seed 20260818); P(bootstrap mean >= 0) = 0.0000.
So -28.03 is not plausibly luck around zero. But **convention 7 still applies:
n=33 is a shrug on the P&L question** and nobody may call this strategy
"broken" or "working" from it. What IS resolved at n=33 is the binomial
win-rate test, which is decisive.

**The sample cannot grow.** The daily loss breaker latched at 04:10Z
(`realized loss today = $30.08 > limit = $30.00`) and has fired 224 times since.
Last entry 04:21Z. The strategy's own kill condition needs 50 trades and will
not reach it today.

**Do not quote the `max_trades_this_window` 83.5% headline.** It is an artifact:
the cap counts ATTEMPTS at 5s cadence, so once 3 are burnt every later poll
writes another row. Real number: **233 attempts -> 33 fills (14.2%)**.

Other findings: `raw_edge` behaves as a broken-model detector, not an arb signal
(median claimed edge 0.156, max 0.480, `realized_edge_bps` up to 80,046; nothing
rejects an implausibly large edge, and losers claimed *higher* edge than
winners). This one strategy caused **93.2%** of the -30.08 drawdown that froze
the whole Polymarket paper book. Loss is spread across 13 of 14 windows — it is
systematic, not one bad market.

**Cannot be answered at n=33:** `best_bid` and spread are **not in
`features_json`**, so the spread hypothesis is strong but indirect.
**Fix before the next run: log `best_bid` and `spread` at entry.** Time-of-day
is not computable — all 33 trades sit in one contiguous 79-minute session.

**Proposed kill condition (needs a D-number, not Cody's to assign):**
> KILL if the median round-trip bid-ask spread on `btc-updown-5m` at
> entry-eligible moments is >= `MAX_LOSS` (3c) over >=200 sampled book
> snapshots. Harness: `backtest/polymarket_harness.py` extended with a
> spread-sampling pass over newly logged `best_bid`/`best_ask`.

**Convention 17, in advance:** the tempting fix is to widen `MAX_LOSS` past the
spread. That *raises* the designed break-even above 75% and makes the payoff
worse. The defect is measuring entry at the ask and exits at the bid.

Incidental convention-20 gap found: `strike_inside_proxy_noise_floor` and three
other reasons are missing from `SKIP_CLASSIFICATION`, so **18.1% of all skips
land in class UNKNOWN**. Worth a ticket.

### Subagent D: the three variants

Built exactly as specified. Thin subclasses passing defaults through
`super().__init__()`; no body copied. Verified from **live instances**, not
module constants:

| strategy | edge | target | stop | max/win | depth | tstop | **breakeven** |
|---|---|---|---|---|---|---|---|
| `PM_fair_value_arb` | 0.04 | 0.01 | 0.03 | 3 | 50 | 60 | **75.0%** |
| `..._wide` | 0.08 | 0.03 | 0.05 | 2 | 50 | 60 | **62.5%** |
| `..._patient` | 0.06 | 0.02 | 0.03 | 2 | 50 | 120 | **60.0%** |
| `..._hft` | 0.02 | 0.01 | 0.02 | 5 | 100 | 30 | **66.7%** |

Every one matches the brief. `breakeven = max_loss / (min_profit + max_loss)`,
exposed as a computed property on each variant.

**Honest read, given C's finding that only `edge_threshold` and
`min_book_depth_shares` can actually move the win rate (because `max_loss` is a
trigger, not a fill):**

- **`wide` is the only honest improvement.** Better reward:risk, tighter entry
  filter, fewer attempts. Cost: a 3c target on a 1-2c spread is a 4-5c
  repricing request. If w drops more than 12.5 points it is worse than parent.
- **`hft` is the convention-17 shape.** Its break-even only improves because the
  *stop tightened*, not because the reward improved. It halves the entry filter
  and raises attempts 3 -> 5. A 2c stop on a 1-2c spread book is one tick from
  noise. Least likely of the three to clear its own costs. Given C's evidence
  that the loss IS the spread, this is the variant most likely to lose fastest.
- **`patient`'s 60% is the best-looking and the least real** — its stop is not
  enforced for the first 15s.

**Min-hold risk, stated not hidden.** Implemented by overriding `manage_exit`
and post-processing the parent's answer. Suppressed under 15s: `price_stop`,
`profit_target` (discretionary). **Never suppressed:** `window_close`,
`no_bid_liquidity`, `no_orderbook`, `unreadable_position`, `converged`,
`model_stop`, `time_stop` — matched on an explicit tuple, not a substring, since
`price_stop` and `model_stop` both end in `_stop`. Unknown `opened_ts` is NOT
treated as young; it gets its stop. Held with its own reason `min_hold_not_met`,
never pooled.

For those 15s the only stop is the structural 0.00 floor, so **loss is bounded
by the premium, not by `max_loss`**. At fair 0.60: $0.54 declared vs **$9.72**
worst case (18x). At fair 0.90: $0.33 vs **$9.24** (28x). Given the log already
shows stops realising -6.98c instead of -3c, this deferral adds to a leak that
is already the dominant cost. **This is the change to push back on hardest.**

**An operational consequence nobody has ruled on:** the daily loss breaker reads
`realized_pnl_today(adapter.positions.values())` — **portfolio-wide, not
per-strategy**. Four fair-value strategies burn the shared $30/day budget
roughly 4x faster, and when it trips it blocks entries for *all* registered
strategies, including the ones that hold to resolution. With the tree now at 15
strategies this is worse than at 11.

`shadow_loop.py` needed **no change**: `check_identity` computes
`expected = self.cycles * len(self.strategies)` and the existing test derives
`N_STRATEGIES` dynamically. A test was added asserting that identity is computed
from `len()` with no hardcoded count.

---

## Test state (honest)

- New, passing: `test_liquidation_recorder.py` 72, `test_hyperliquid_client.py`
  70, `test_fair_value_arb_variants.py` 87 (of which **83 pass**).
- **4 tests are RED**, all in `tests/test_fair_value_arb_variants.py::TestRegistry`,
  all one cause: they assert an exact global strategy count/position and another
  session moved it 11 -> 15.
  - `assert len(strategies) == 11` -> `assert 15 == 11`
  - `assert names[8:] == list(VARIANT_NAMES)`
  - `assert len(fam) == 4` -> `assert 5 == 4` (picks up `_inverse`)
  - `assert managers == {...}`
  **These are not defects in the variants** — the 83 behavioural tests pass.
  The fix is to assert "our three are present, unique, contiguous after the
  parent" instead of pinning an absolute count. **Blocked: the `Edit` tool is
  denied session-wide by the permission layer** (see below), so it could not be
  applied. One-line-each fix for whoever has write access.
- The `~1,314` baseline in CLAUDE.md is **stale** — other sessions added ~142
  untracked tests. Collection grew 1458 -> 1545 during this session.
- `env -u PYTHONPATH python3 -m pytest tests/ -q` **cannot run**:
  `tests/test_dashboard_charts.py` needs plotly, which is only in `.venv`.
  Use `env -u PYTHONPATH .venv/bin/python -m pytest`.

## Blocked / owed

1. **`Edit` is denied session-wide by the permission layer.** It hit all four
   subagents and the parent. Consequences: `db/schema.sql` was **not** updated
   with the two new tables (DDL lives in the modules and is applied at runtime,
   so the code works, but the schema file no longer describes the database);
   and the 4 red tests could not be patched. Same block that has stalled
   `cp agents/forge/forge.agent.md .claude/agents/forge.md` for three sessions.
   **This is the top item to fix — it is now costing real work.**
2. `strategies/proposals/forge_runs.jsonl` (tracked) is **modified by this
   session** — `agents/forge.py --shadow-results` appends a run record and
   rewrites 5 proposal `.md` files identically. Unstage if unwanted.
3. Log `best_bid` / `spread` into `features_json` at entry. Without it the
   spread diagnosis stays indirect.
4. D-numbers needed: the fair_value_arb kill condition above; the
   `SKIP_CLASSIFICATION` gap; whether 15 strategies sharing one $30 daily
   breaker is acceptable.

## Explicitly NOT done

- **Shadow loop 27030 NOT restarted** (Aym's call). Convention 13: it
  snapshotted imports at 23:02 and does not see any of the 3 variants.
- Nothing committed, nothing staged, no `git add`.
- Graveyard sweep 18543 and shadow loop 27030 both confirmed alive, untouched.
- Feeds 37491 / 37578 were started by ANOTHER session; left running.
- CLAUDE.md not rewritten — with 8 live sessions that would clobber their state.
- No strategy here has been scored. All variants are NOT_TESTED per D-268.
  Nothing in this session may be cited as evidence that any variant works.
