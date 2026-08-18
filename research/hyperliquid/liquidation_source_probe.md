# Hyperliquid liquidation source probe

**Date:** 2026-08-18
**Author:** Cody (research session)
**Question:** does Hyperliquid expose a PUBLIC, UNAUTHENTICATED liquidation feed
we can record into the `liquidations` table
(`id, ts, exchange, symbol, side['long'|'short'], price, qty, value_usd`)?

**VERDICT: NO.** There is no public, venue-wide Hyperliquid liquidation stream.
Every probe below is a live measurement taken this session, not documentation.

Read-only probes only. No key material, no wallet, no order endpoint was touched.
Nothing in this document was written to the database.

---

## 1. Summary table of everything probed

| # | Transport | Request | Measured result |
|---|---|---|---|
| 1 | POST `api.hyperliquid.xyz/info` | `{"type":"liquidations"}` | **HTTP 422** `Failed to deserialize the JSON body into the target type` |
| 2 | POST `/info` | `{"type":"recentLiquidations"}` | **HTTP 422**, same body |
| 3 | POST `/info` | `{"type":"allLiquidations"}` | **HTTP 422**, same body |
| 4 | POST `/info` | `{"type":"liquidations","coin":"BTC"}` | **HTTP 422**, same body |
| 5 | POST `/info` | `{"type":"userNonFundingLedgerUpdates"}` (no `user`) | **HTTP 422**, same body |
| 6 | POST `/info` | `{"type":"meta"}` — CONTROL | **HTTP 200**, 17,527 bytes |
| 7 | WS `wss://api.hyperliquid.xyz/ws` | `subscription {"type":"liquidations"}` | **error frame**, type not in enum |
| 8 | WS same | `{"type":"allLiquidations"}` | **error frame** |
| 9 | WS same | `{"type":"liquidations","coin":"BTC"}` | **error frame** |
| 10 | WS same | `{"type":"allLiquidations","coin":"BTC"}` | **error frame** |
| 11 | WS same | `{"type":"forceOrder"}` | **error frame** |
| 12 | WS same | `{"type":"trades","coin":"BTC"}` — CONTROL | **ACK**, 35 frames / 27s |
| 13 | WS same | `{"type":"userFills","user":<any addr>}` | **ACK + snapshot**, works unauthenticated |
| 14 | WS same | `{"type":"userEvents","user":<any addr>}` | **ACK** |
| 15 | WS `wss://rpc.hyperliquid.xyz/ws` | `{"type":"explorerTxs"}` | **ACK**, 205 payload frames / 15s |
| 16 | GET `stats-data.hyperliquid.xyz/Mainnet/leaderboard` — CONTROL | — | **HTTP 200**, 34,652,963 bytes |
| 17 | GET `stats-data.../Mainnet/liquidations` | — | **HTTP 403** |
| 18 | GET `stats-data.../Mainnet/recentLiquidations` | — | **HTTP 403** |
| 19 | GET `stats-data.../Mainnet/liquidationSummary` | — | **HTTP 403** |

Controls (rows 6, 12, 16) all returned 200/ACK in the same runs, so the 422s and
error frames are the venue rejecting the *request type*, not a broken probe, a
geoblock, or a dead host. This is the opposite failure mode from Binance's HTTP
451 + silent-zero websocket.

---

## 2. The `/info` liquidation types do not exist

```
POST https://api.hyperliquid.xyz/info
{"type":"liquidations"}        -> 422  Failed to deserialize the JSON body into the target type
{"type":"recentLiquidations"}  -> 422  (identical body)
{"type":"allLiquidations"}     -> 422  (identical body)
{"type":"liquidations","coin":"BTC"} -> 422  (identical body)
{"type":"meta"}                -> 200  {"universe":[{"szDecimals":5,"name":"BTC",...
```

422 with "failed to deserialize" is the Rust server refusing to parse the body
into its request enum. The type is absent from the API surface. This matches the
existing `hyperliquid_client.py` docstring, which recorded the same 422 for
`userState`, `allPositions` and `leaderboard`.

---

## 3. The WebSocket has no liquidation subscription — and the control proves it

Sent to `wss://api.hyperliquid.xyz/ws`, all five liquidation-shaped subscriptions
came back as errors while the three `trades` subscriptions were ACKed in the same
socket, same second:

```
{"channel":"error","data":"Error parsing JSON into valid websocket request: {\"method\": \"subscribe\", \"subscription\": {\"type\": \"liquidations\"}}"}
{"channel":"error","data":"Error parsing JSON into valid websocket request: {\"method\": \"subscribe\", \"subscription\": {\"type\": \"allLiquidations\"}}"}
{"channel":"error","data":"Error parsing JSON into valid websocket request: {\"method\": \"subscribe\", \"subscription\": {\"type\": \"forceOrder\"}}"}
{"channel":"subscriptionResponse","data":{"method":"subscribe","subscription":{"type":"trades","coin":"BTC"}}}
```

Run totals: **43 frames in 27.3s**, `{"error":5,"subscriptionResponse":3,"trades":35}`.

**The decisive control.** A deliberately nonsense type produces a *byte-identical
error shape*:

```
{"channel":"error","data":"Error parsing JSON into valid websocket request: {\"method\": \"subscribe\", \"subscription\": {\"type\": \"zzzNotARealType\"}}"}
{"channel":"error","data":"Error parsing JSON into valid websocket request: {\"method\": \"subscribe\", \"subscription\": {\"type\": \"liquidations\"}}"}
```

So `liquidations` is not a gated, permissioned or deprecated channel. It is
indistinguishable from a string the server has never heard of.

---

## 4. Public `trades` carries NO liquidation marker

197 trades captured over 25s across BTC/ETH/SOL/HYPE/DOGE. Raw objects:

```json
{"coin":"BTC","side":"A","px":"64159.0","sz":"0.13722","time":1787047504010,"hash":"0x292f4dcc51901beb2aa904426dff020202cf00b1ec933abdccf7f91f1093f5d5","tid":1016897150094344,"users":["0x07fd7e702ba749ffa49c3a6c17fcd9e6c7b7082a","0x4a8a68b2ec7c67cbb79060d98157b68c47eba0e6"]}
{"coin":"BTC","side":"B","px":"64160.0","sz":"0.00587","time":1787047505157,"hash":"0x77daa017a76cb4ca795404426dff1302011f00fd426fd39c1ba34b6a66608eb5","tid":284971718326268,"users":["0xd0bd58f100b3a25aecc1b9ac900c6a6868acf729","0x654086857e1fad6dcf05cf6695cce51ea3984268"]}
```

**Complete key set, measured over the whole sample:**
`['coin','hash','px','side','sz','tid','time','users']`

No flag, no `liquidation` object, no `dir`, no special counterparty. `users` is
just the two addresses on the fill. A liquidation *is* present in this stream
(it produces a real fill) but is **not distinguishable** from any other trade.

### 4a. The `hash == 0x000...0` false lead, and how it was killed

27.9% of trades (55 of 197) carry an all-zero `hash`. That looked like a
protocol-internal marker. It is not.

Test: take the `tid`s of captured zero-hash trades and look them up in
`userFills` for the addresses involved. **14 tids matched.** Every one:

```json
{"coin":"BTC","px":"64142.0","sz":"0.00052","side":"A","time":1787047599030,
 "startPosition":"-5.11888","dir":"Open Short","closedPnl":"0.0",
 "hash":"0x0000...0000","oid":518865150801,"crossed":false,"fee":"-0.001",
 "tid":294513737315391,"cloid":"0x877ee321679e174c3a13de39f74d150a",
 "feeToken":"USDC","twapId":null}
```

`crossed: false` (a resting maker fill), a **negative** fee (a maker rebate), a
client order id, and **no `liquidation` key**. These are ordinary maker fills.
A zero hash means the fill was not attributed to an L1 transaction, not that it
was forced. At 27.9% of all tape it could not plausibly be liquidations anyway.

**Do not use zero-hash as a liquidation marker.** Recording it as one would
manufacture a fabricated liquidation tape at roughly 28% of total trade volume.

---

## 5. Where liquidation data ACTUALLY lives: `userFills`, address-scoped

`POST /info {"type":"userFills","user":"0x..."}` returns up to 2000 fills for one
address, unauthenticated. Across 4 arbitrary active addresses (8000 fills), the
key union was:

```
coin 8000, px 8000, sz 8000, side 8000, time 8000, startPosition 8000,
dir 8000, closedPnl 8000, hash 8000, oid 8000, crossed 8000, fee 8000,
tid 8000, feeToken 8000, twapId 8000, liquidation 328, cloid 2986, builderFee 600
```

**328 of 8000 fills (4.1%) carry a `liquidation` object.** Shape:

```json
{"coin":"xyz:CRWV","px":"116.86","sz":"2.47","side":"A","time":1786628572099,
 "startPosition":"-143.54","dir":"Open Short","closedPnl":"0.0",
 "hash":"0xe6838c8a3dba3578e7fd044214d3e2020481006fd8bd544a8a4c37dcfcbe0f63",
 "oid":515445455983,"crossed":false,"fee":"0.008659","tid":711626751774561,
 "liquidation":{"liquidatedUser":"0xbb8b801eeddaa7061ae171041ec77516313f2921",
                "markPx":"116.84","method":"market"},
 "feeToken":"USDC","twapId":null}
```

This is the real thing: `liquidatedUser`, `markPx`, `method`. But it is annotated
onto **the counterparty's own fill**, and `userFills` requires a `user`. There is
no `allFills`. So this data is reachable only by choosing addresses to ask about,
exactly like the whale poller's problem — and worse, because a liquidation is
absorbed by whoever happened to be resting on the book.

### 5a. `method` has two values with DIFFERENT side semantics

Measured examples of both:

- `"method":"market"` — the common path. The liquidation engine crosses the book.
  The `liquidation` object lands on the resting maker's fill.
- `"method":"backstop"` — the position is transferred to an HLP sub-vault. Here
  the fill's own `dir` names the liquidated side literally:
  `"dir":"Liquidated Cross Long"`, `"dir":"Liquidated Isolated Short"`.

These are not the same mapping and must never be pooled. See section 8.

### 5b. The HLP backstop vault is public but far too sparse to be a feed

`vaultDetails` for HLP (`0xdfc24b077bc1425ad1dea75bcb6f8158e10df303`) returns 7
`childAddresses`. HLP itself has **0 fills** (they sit on the children). Probing
all 7 children's most recent 2000 fills each:

| child address | fills | with `liquidation` | newest fill age |
|---|---|---|---|
| `0x010461c1...` | 2000 | 0 | 0.0 h |
| `0x2e3d94f0...` | 2000 | 0 | 2177.7 h |
| `0x2ed5c448...` | 2000 | **7** | 578.4 h |
| `0x31ca8395...` | 2000 | 0 | 0.0 h |
| `0x469f6902...` | 6 | 0 | 5353.5 h |
| `0x5e177e5e...` | 2000 | 0 | 1525.1 h |
| `0xb0a55f13...` | 2000 | **2** | 4641.8 h |

Nine backstop liquidations total, the newest **24 days old**. The backstop path
fires only when the book cannot absorb the position. It is a tail-event log, not
a tape. Sample row:

```json
{"coin":"SOL","px":"67.5499","sz":"28.33","side":"B","time":1770337193454,
 "dir":"Liquidated Cross Long","crossed":true,"fee":"0.0",
 "liquidation":{"liquidatedUser":"0x7ba283114573bde6fd304ad7b188a763e5402a52",
                "markPx":"68.2","method":"backstop"}}
```

---

## 6. The explorer WS is user actions only — no liquidation action exists

`wss://rpc.hyperliquid.xyz/ws` + `{"type":"explorerTxs"}` ACKs and streams
(205 payload frames in 15s). Tx keys: `['action','block','error','hash','time','user']`.

Action types observed in 15s:

```
order 128, cancelByCloid 53, cancel 39, evmRawTx 3,
SetGlobalAction 2, noop 2, batchModify 1, VoteEthDepositAction 1
```

Types containing "liq": **none**. Liquidations are protocol-internal state
transitions triggered by the clearinghouse, not signed user transactions, so they
never appear as an L1 tx. The explorer cannot be a liquidation feed by design.

---

## 7. `stats-data` bucket: only the leaderboard is reachable

```
GET https://stats-data.hyperliquid.xyz/Mainnet/leaderboard          -> 200, 34,652,963 bytes
GET https://stats-data.hyperliquid.xyz/Mainnet/liquidations         -> 403
GET https://stats-data.hyperliquid.xyz/Mainnet/recentLiquidations   -> 403
GET https://stats-data.hyperliquid.xyz/Mainnet/liquidationSummary   -> 403
```

Honesty note: these three paths were **guessed by me**, not documented anywhere.
A 403 from an S3-backed bucket is the standard response for a key that is not
public, and it does not distinguish "does not exist" from "exists but private".
Either way it is unreachable from here. This is a negative result on names I
invented, and must not be written up as "Hyperliquid has a private liquidations
bucket". The only thing measured is that `leaderboard` is public and these are not.

---

## 8. If anyone later builds on the `userFills` path: the side mapping

Do not treat this as settled. It is derived, not empirically cross-checked, and
convention 17 applies.

Our `liquidations.side` column stores **which side got liquidated**, so it needs
an inversion at every venue. The two Hyperliquid paths invert differently:

**`method: "market"`** — the row comes from the *maker who absorbed* the
liquidation, so the sign flips **twice**:

| maker `side` in userFills | liquidation order was | our `side` |
|---|---|---|
| `A` (ask / maker sold) | a BUY (closing a short) | `short` |
| `B` (bid / maker bought) | a SELL (closing a long) | `long` |

Note this is the **opposite** of the raw Binance/Bybit rule as applied to the
field you read, because on those venues the field is the *forced order's* side
and here it is the *counterparty's* side. Reusing
`_BYBIT_ORDER_SIDE_TO_LIQUIDATED` unchanged would silently invert every row.

**`method: "backstop"`** — do not derive anything. The fill's own `dir` states it:
`"Liquidated Cross Long"` / `"Liquidated Isolated Short"` -> parse `long`/`short`
directly. Deriving from `side` here would also work but adds a failure mode for
no benefit.

Column mapping if it were ever recorded:

| our column | source |
|---|---|
| `ts` | `time` (ms epoch) |
| `exchange` | `'hyperliquid'` |
| `symbol` | `coin` (note: spot/HIP-3 markets appear as `xyz:CRWV`, not a perp) |
| `side` | per the table above — **never** copied straight from `side` |
| `price` | `px` (the fill price; `liquidation.markPx` is a different number) |
| `qty` | `sz` |
| `value_usd` | `float(px) * float(sz)` |

**Deduplication is mandatory and non-obvious.** One liquidation is absorbed by
many makers and appears once in each of their `userFills`. In the CRWV sample,
three fills shared one `hash` and one `oid` while naming **three different**
`liquidatedUser`s. Any watchlist of more than one address will double-count.
Dedupe key: `(hash, liquidation.liquidatedUser, coin)`.

---

## 9. What the honest options are

1. **Record nothing from Hyperliquid into `liquidations`.** This is the default
   and it is defensible. The table has an `exchange` column; Hyperliquid simply
   has no row-producing source. Convention 11: the absence is "could not run",
   not "no liquidations happened".

2. **Do NOT record public `trades` as liquidations.** Measured: the trades
   channel has an eight-key schema with no liquidation marker of any kind, and
   the one candidate marker (zero `hash`) was tested and disproved at 27.9% of
   volume. Recording it would be a fabricated tape, and the failure would be
   silent — exactly the Binance HTTP-451 shape, where a dead source read as
   uptime.

3. **A `userFills` watchlist is possible but is a SAMPLE, not a feed.**
   `userFills` works unauthenticated over both HTTP and WS push (the WS
   `{"type":"userFills","user":...}` subscription ACKed and delivered a snapshot
   for an address we do not control). 4.1% of fills on active addresses carry a
   `liquidation` object with `liquidatedUser`, `markPx` and `method`.
   If this is ever built, it must be named honestly — a separate table or an
   explicit `source='userfills_watchlist'`, never pooled into `liquidations`
   alongside Bybit's venue-wide tape, because the coverage denominators are not
   comparable and the sampling bias is unquantified (same unresolved problem as
   caveat 4 of the whale poller).

4. **The nearest REAL substitute we already have is
   `hyperliquid_positions.liq_price`.** The running whale poller records where
   large positions *will* liquidate. That is a forward-looking clustering signal
   and is what `near_liq_trigger` was designed around. It is not a liquidation
   print and must not be described as one, but it is genuine Hyperliquid
   forced-flow information and it is already flowing.

5. **The venue-wide liquidation tape stays Bybit-only, 3 symbols.** Nothing
   measured here changes that. Binance remains HTTP 451 from this machine.

---

## 10. Reproduction

Probe scripts were written to `/tmp` and are not part of the repo. To reproduce
the two load-bearing negatives:

```bash
env -u PYTHONPATH python3 - <<'EOF'
import requests
for t in ('liquidations','recentLiquidations','allLiquidations','meta'):
    r = requests.post('https://api.hyperliquid.xyz/info', json={'type': t}, timeout=15)
    print(t, r.status_code, r.text[:80])
EOF
```

```bash
env -u PYTHONPATH python3 - <<'EOF'
import asyncio, json
from websockets.asyncio.client import connect
async def m():
    async with connect('wss://api.hyperliquid.xyz/ws') as ws:
        for s in ({'type':'liquidations'}, {'type':'zzzNotARealType'}, {'type':'trades','coin':'BTC'}):
            await ws.send(json.dumps({'method':'subscribe','subscription':s}))
        for _ in range(5):
            print((await asyncio.wait_for(ws.recv(), 10))[:180])
asyncio.run(m())
EOF
```

Expect: 422/422/422/200, then two identical error frames and a trades ACK.
