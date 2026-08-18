# Polymarket wallet resolution for `smart_money_copy`

**Fetched:** 2026-08-18, between 14:05 and 14:25 UTC.
**By:** Cody, task 8.
**Target:** resolve the 7 handles in `TRACKED_WALLETS`
(`strategies/polymarket/smart_money_copy.py`) to Polymarket PROXY WALLET
addresses, or prove they cannot be resolved.

**Result: 7 of 7 resolved.** No address in this document was guessed, derived,
pattern-matched, or inferred from a prefix. Every one came back from a public
HTTP 200 and was then round-tripped back to the same display name through a
SECOND, independent endpoint.

---

## 1. The endpoints the task named do not exist

The task's suggested Data API calls both 404. Recorded so nobody spends another
hour on them.

| request | status | body |
|---|---|---|
| `GET https://data-api.polymarket.com/profiles?limit=50&orderBy=profit&ascending=false` | **404** | `404 page not found` |
| `GET https://data-api.polymarket.com/profiles?limit=5` | **404** | `404 page not found` |
| `GET https://data-api.polymarket.com/profiles?name=bonereaper` | **404** | (same route, same 404) |
| `GET https://data-api.polymarket.com/leaderboard?window=all&limit=5&orderBy=profit` | **404** | `404 page not found` |
| `GET https://lb-api.polymarket.com/leaderboard?window=all&limit=5&orderBy=profit` | **404** | `404 page not found` |
| `GET https://data-api.polymarket.com/public-search?q=bonereaper&search_profiles=true` | **404** | `404 page not found` (public-search is on GAMMA, not Data) |
| `GET https://gamma-api.polymarket.com/profiles?address=0xeebde...` | **401** | `{"type":"authorization error","error":"invalid token/cookies"}` |
| `GET https://data-api.polymarket.com/pnl?user=0x751a...` | **404** | `404 page not found` |

There is no public `profiles` collection endpoint and no public leaderboard
route on either host. Anything claiming otherwise is describing an endpoint that
was removed or never existed.

## 2. The endpoints that DO work

Four public, unauthenticated GETs carried this whole task:

| endpoint | what it gives |
|---|---|
| `GET gamma-api.polymarket.com/public-search?q=<handle>&limit_per_type=<n>&search_profiles=true` | handle -> `{name, pseudonym, proxyWallet}`. **`search_profiles=true` is mandatory**; without it the `profiles` key is absent from the response entirely. |
| `GET polymarket.com/api/profile/userData?address=<addr>` | address -> `{name, pseudonym, createdAt, takerTier}`. The REVERSE direction, and a different host from Gamma. This is the verification leg. |
| `GET data-api.polymarket.com/traded?user=<addr>` and `/value?user=<addr>` and `/activity?user=<addr>&limit=1` | traded total, current position value, last activity. Liveness check. |
| `GET user-pnl-api.polymarket.com/user-pnl?user_address=<addr>&interval=all&fidelity=1d` | all-time cumulative PnL series, `[{t, p}, ...]`. Profitability check. **This host is not in `engine/polymarket/client.py`'s host table.** |

## 3. Did the filter actually filter? (the `order=volume` lesson)

CLAUDE.md warns that Gamma returns HTTP 200 with a wrong-order page for
`order=volume`. So `search_profiles=true` was tested for the same failure mode
before any result was trusted.

**Control query, a handle that cannot exist:**

```
GET https://gamma-api.polymarket.com/public-search?q=zzqqxyvvnotarealhandle99&limit_per_type=10&search_profiles=true
-> HTTP 200
{"pagination":{"hasMore":false,"totalResults":0}}
```

Zero results, not a default page. The filter is real. A second control: `q=trump`
returns events and no profile named after the query, while `q=bonereaper`
returns four profiles all containing the substring "Bonereaper". The match is
case-insensitive SUBSTRING, not exact, which matters for section 6.

**And every address was verified in the opposite direction**, on a different
host, before being written into the source. Gamma saying "Bonereaper lives at
0xeebde..." is one claim; `polymarket.com/api/profile/userData?address=0xeebde...`
independently answering `"name":"Bonereaper"` is the round trip. All 7 round-trip.

## 4. The resolved table

Every row: found by `public-search`, confirmed by `userData` reverse lookup,
liveness and profitability from the Data and PnL APIs. All fetched 2026-08-18.

| handle in source | display name returned | proxy wallet | acct created | all-time PnL (USDC) | traded | last activity (UTC) |
|---|---|---|---|---|---|---|
| `bonereaper` | `Bonereaper` | `0xeebde7a0e019a63e6b476eb425505b7b3e6eba30` | 2026-03-25 | **1,307,131.60** | 118,395 | 2026-08-18T14:21:03Z TRADE, ETH Up/Down 5m |
| `0x50f7` | `0x50f7` | `0xee65685de42f8de9a03b4c53ee77d56a20d2cfc9` | 2026-04-03 | **501,051.10** | 108,125 | 2026-08-18T14:21:37Z REDEEM, DOGE Up/Down 5m |
| `boneohio` | `BoneOhio` | `0x48ac40fc545cf327edd5365435c3a9f385614a7e` | 2026-03-24 | **259,543.90** | 128,583 | 2026-08-18T14:18:45Z TRADE, XRP Up/Down 5m |
| `coinfilippe` | `coinfilippe` | `0x997cda7b31612e3c394bfb55440619f3f689251e` | 2025-10-17 | **82,360.05** | 4,580 | 2026-08-18T14:21:30Z TRADE, BTC above $64,000 |
| `0xaaaaa` | `0xAAAAA` | `0x251c1a283703beed41590b0875a8dcb8ddd1541f` | 2026-04-27 | **310,660.70** | 32,521 | 2026-08-18T14:21:40Z REDEEM, BTC Up/Down 5m |
| `doggystyie` | `DoggyStyIe` | `0x0484e64092ba4108c2786b61e6fc052d3bf41b1a` | 2026-04-22 | **249,641.08** | 28,396 | 2026-08-18T14:21:24Z TRADE, BTC Up/Down 5m |
| `Sharky6999` | `Sharky6999` | `0x751a2b86cab503496efd325c8344e10159349ea1` | 2025-01-05 | **965,550.75** | 32,644 | 2026-08-18T14:16:40Z REDEEM, BTC Up/Down 15m |

`traded` is the raw integer from `data-api /traded`. It is NOT labelled by the
API and it is NOT safe to call it either "USDC volume" or "trade count": for
Bonereaper it rose 118,388 -> 118,395 across two calls ~2 minutes apart, which
is consistent with both readings. Do not put it in a gate until somebody pins
down the unit. **It is recorded here as an identity signal, not as evidence.**

Raw JSON for one row, as returned (the pattern is identical for all seven):

```
GET https://gamma-api.polymarket.com/public-search?q=bonereaper&limit_per_type=10&search_profiles=true
-> HTTP 200
{"profiles":[{"name":"Bonereaper","pseudonym":"Popular-Insurrection","displayUsernamePublic":true,"proxyWallet":"0xeebde7a0e019a63e6b476eb425505b7b3e6eba30"}, ...]}

GET https://polymarket.com/api/profile/userData?address=0xeebde7a0e019a63e6b476eb425505b7b3e6eba30
-> HTTP 200
{"id":"7024422","createdAt":"2026-03-25T05:55:18.904078Z","proxyWallet":"0xeebde7a0e019a63e6b476eb425505b7b3e6eba30","displayUsernamePublic":true,"pseudonym":"Popular-Insurrection","name":"Bonereaper","users":[{"id":"6943965","creator":false,"mod":false}],"verifiedBadge":false,"takerTier":6,"takerTierName":"Obsidian"}

GET https://data-api.polymarket.com/traded?user=0xeebde7a0e019a63e6b476eb425505b7b3e6eba30
-> HTTP 200
{"user":"0xeebde7a0e019a63e6b476eb425505b7b3e6eba30","traded":118395}

GET https://data-api.polymarket.com/value?user=0xeebde7a0e019a63e6b476eb425505b7b3e6eba30
-> HTTP 200
[{"user":"0xeebde7a0e019a63e6b476eb425505b7b3e6eba30","value":12100.9134}]

GET https://user-pnl-api.polymarket.com/user-pnl?user_address=0xeebde7a0e019a63e6b476eb425505b7b3e6eba30&interval=all&fidelity=1d
-> HTTP 200
[{"t":...,"p":19804.9}, ... ,{"t":1787...,"p":1307131.6}]   (147 daily points)
```

## 5. Disambiguating `bonereaper`

The only handle whose search returned more than one CHOSEN name. All four were
reverse-looked-up:

| name | address | created | traded | value |
|---|---|---|---|---|
| **`Bonereaper`** | `0xeebde7a0...` | 2026-03-25 | 118,395 | 12,100.91 |
| `BonereapersCrazyUncle` | `0x11760788...` | 2026-07-24 | **0** | 0 |
| `Bonereaper5` | `0xd455484f...` | 2026-05-15 | 244 | 0 |
| `Bonereaper1` | `0x725fd079...` | 2026-05-04 | 105 | 0 |

Exactly one exact-name match, and it is the only one of the four that trades.
The other three are near-empty accounts whose names look like what you pick when
`Bonereaper` is already taken, which is itself weak evidence that Polymarket
enforces display-name uniqueness.

`boneohio`, `coinfilippe`, `doggystyie` and `sharky6999` each returned
**exactly one** profile at `limit_per_type=25`. No collision to resolve.

Note `doggystyie` -> `DoggyStyIe`: capital i, not lowercase L. Lowercased both
sides it is a character-for-character match.

## 6. `0x50f7` and `0xaaaaa` are DISPLAY NAMES, not address prefixes

The module docstring in `smart_money_copy.py` assumed these two entries were
truncated addresses, and built `TRACKED_WALLET_PREFIXES` plus a whole
`unresolved_prefix_only` census bucket around that reading. **The evidence says
the reading was wrong.** They are chosen usernames that happen to look like hex.

`public-search?q=0x50f7&limit_per_type=50` returns 4 profiles:

| name | address | starts with 0x50f7? | traded | all-time PnL |
|---|---|---|---|---|
| **`0x50f7`** (chosen name) | `0xee65685de42f8de9a03b4c53ee77d56a20d2cfc9` | no | 108,125 | **501,051.10** |
| `0x50f7bF7344...-1777991176896` (auto) | `0x50f7bf7344a0dd344cb2f8254674c66f46e0c5b2` | yes | **0** | not fetched |
| `0x50f74dbdb6...-1770780227053` (auto) | `0x50f74dbdb6236c9890220069ccf1c7b080a9aff1` | yes | **0** | not fetched |
| `0x50F726258727...-1739421806830` (auto) | `0x50f72625872704b5d2ca08a33dd4addd5bace18e` | yes | **0** | not fetched |

`0xaaaaa` is the same shape: one chosen name `0xAAAAA` at
`0x251c1a283703beed41590b0875a8dcb8ddd1541f` (PnL 310,660.70, traded 32,521),
and three auto-named accounts that genuinely start with `0xaaaaa` and have
**traded = 0** each.

The `<checksummed-address>-<epoch-ms>` name form is Polymarket's DEFAULT for an
account that never set a username. So the three prefix-matching candidates in
each set are unnamed, never-traded, empty wallets. A list of profitable traders
does not contain three wallets that have never placed a trade.

Two further points that settle it:

1. **A source listing truncated addresses would not truncate to different
   lengths.** `0x50f7` is 4 hex characters, `0xaaaaa` is 5. Usernames vary in
   length; a display convention does not.
2. **The chosen-name accounts fit the pattern of the other five perfectly**:
   both are six-figure-profitable and both were active in crypto Up/Down markets
   within the same minute as the other five (section 4).

**Consequence for the source:** `TRACKED_WALLET_PREFIXES` and the
`unresolved_prefix_only` bucket are now describing a problem that does not
exist. They are left in place, because the census machinery is generic and the
identity `resolved + prefix_only + no_address == len(TRACKED_WALLETS)` still
balances with `prefix_only == 0`. Removing them is a separate change needing a
D-number. **The docstring claim that these two are prefixes is now corrected in
the source.**

## 7. What could NOT be verified, and it applies to all seven equally

**The Dan1ro0 article and the Reddit post were not read.** `WebSearch` and
`WebFetch` are both permission-denied in this session:

```
WebSearch  -> "Permission to use WebSearch has been denied."
WebFetch   -> "Permission to use WebFetch has been denied."
```

So there is exactly one unverified link in the chain, and it is the same link
for every row: **whether the profile Polymarket calls `Bonereaper` today is the
`bonereaper` the article meant.** Nothing found here can close that, because the
evidence for it lives on a web page this session cannot reach.

What makes that gap small rather than large: the article is described as a list
of profitable Polymarket traders, and all seven independently-resolved profiles
are (a) six-figure-profitable all-time, (b) trading crypto Up/Down markets,
which is the family this repo's shadow loop trades, and (c) active within the
same four-minute window on 2026-08-18. Seven unrelated name collisions all
landing on profitable crypto Up/Down traders is not a plausible coincidence.

What it does NOT establish: that any of them is profitable at OUR latency, that
their edge survives being copied, or that any single one of them is the specific
person the article named. Convention 3 still holds. Their PnL is not our
evidence and none of these numbers is wired into a gate.

## 8. Confidence, per row

| handle | address | confidence | basis |
|---|---|---|---|
| `bonereaper` | `0xeebde7a0...` | **HIGH** | unique exact-name match, round-tripped, 1.31M PnL, live |
| `boneohio` | `0x48ac40fc...` | **HIGH** | sole search result, round-tripped, 259k PnL, live |
| `coinfilippe` | `0x997cda7b...` | **HIGH** | sole search result, round-tripped, 82k PnL, live |
| `doggystyie` | `0x0484e640...` | **HIGH** | sole search result, round-tripped, 249k PnL, live |
| `Sharky6999` | `0x751a2b86...` | **HIGH** | sole search result, round-tripped, 965k PnL, live, oldest account (2025-01-05) |
| `0x50f7` | `0xee65685d...` | **MEDIUM-HIGH** | sole chosen-name match, round-tripped, 501k PnL, live. Deduction in section 6 needed to rule out the truncated-address reading. |
| `0xaaaaa` | `0x251c1a28...` | **MEDIUM-HIGH** | as above, 310k PnL, live |

No row is LOW and no row was guessed. Nothing was written in to make the table
look full.

## 9. Expiry (Convention 17)

**Every address in this file is an assumption with an expiry date.** Two ways it
goes stale, neither of which produces an error:

- **A username is not a permanent binding.** If `Bonereaper` renames or deletes,
  the name frees up and a different person can take it. The address stays in our
  source pointing at the ORIGINAL account, which is the safer failure. But the
  reverse also happens: re-run the round trip and get a different address for
  the same handle, meaning the source is now stale, not wrong.
- **Wallets rotate.** A trader moving to a fresh proxy wallet leaves this file
  pointing at a dormant address that quietly stops emitting trades. That looks
  identical to "the whale is not trading today".

**Re-verification, one command per handle:**

```bash
curl -s "https://gamma-api.polymarket.com/public-search?q=<handle>&limit_per_type=25&search_profiles=true"
curl -s "https://polymarket.com/api/profile/userData?address=<address>"   # must answer with <handle>
```

Suggested cadence: monthly, and before any run that treats this strategy's
output as a result rather than a smoke test.

## 10. A THIRD independent confirmation of every mapping

`data-api /trades?user=<addr>` embeds `name` and `pseudonym` on every trade row.
That is a third host, unrelated to Gamma search and to `polymarket.com/api`, and
it agrees on all seven, pseudonym included:

| handle | `/trades` row says `name` | `pseudonym` |
|---|---|---|
| `bonereaper` | `Bonereaper` | Popular-Insurrection |
| `0x50f7` | `0x50f7` | Haunting-Cheese |
| `boneohio` | `BoneOhio` | Warlike-Fallingout |
| `coinfilippe` | `coinfilippe` | Hungry-Cormorant |
| `0xaaaaa` | `0xAAAAA` | Suburban-Characterization |
| `doggystyie` | `DoggyStyIe` | Flaky-Wreck |
| `Sharky6999` | `Sharky6999` | Tremendous-Closet |

The pseudonym is Polymarket-assigned and not user-editable, so it matching
across three endpoints rules out the possibility that one endpoint is serving a
cached or stale name.

## 11. Where the strategy actually stops now (live gate walk, 2026-08-18 14:25 UTC)

Resolving addresses does not make this strategy trade. It moves the blocker.
Fresh `/trades?user=<addr>&limit=10` for all seven, scored against the
strategy's own `MAX_TRADE_AGE_SEC = 120`:

| handle | rows | fresh within 120s | most recent market |
|---|---|---|---|
| `bonereaper` | 10 | **6** | BUY, Bitcoin Up or Down 10:25-10:30 |
| `boneohio` | 10 | **4** | BUY, Bitcoin Up or Down 10:25-10:30 |
| `0xaaaaa` | 10 | **4** | BUY, Bitcoin Up or Down 10:25-10:30 |
| `doggystyie` | 10 | 0 | BUY, Bitcoin Up or Down 10:20-10:25 |
| `0x50f7` | 10 | 0 | BUY, Dogecoin Up or Down 10:15-10:30 |
| `coinfilippe` | 10 | 0 | BUY, Bitcoin above $68,000 |
| `Sharky6999` | 10 | 0 | BUY, Dogecoin Up or Down 4AM ET |

Three wallets had a fresh BUY in the CURRENT BTC Up/Down 5-minute market, which
is the exact market family the shadow loop evaluates. So the gates that follow
the address gate - `no_tracked_wallet_trades`, `no_trade_in_this_market`,
`no_tracked_wallet_buy`, `copied_trade_stale` - would all have passed on that
cycle. Offline, the shipped list now skips at `no_tracked_wallet_trades` purely
because the test stub serves no trades.

**The hard stop is the gate after those.** Full key set of a live `/trades` row:

```
asset, bio, conditionId, eventSlug, icon, name, outcome, outcomeIndex, price,
profileImage, profileImageOptimized, proxyWallet, pseudonym, side, size, slug,
timestamp, title, transactionHash
```

No `won`, no `is_win`, no `realized_pnl`, no `pnl`, no redemption flag. None of
`SETTLEMENT_BOOL_KEYS` and none of `SETTLEMENT_NUMERIC_KEYS`. So
`WalletTradeFeed.fetch_record` returns None for every wallet and the strategy
skips `wallet_record_unmeasured`. The module docstring's claim about this was
correct and is now confirmed against a live payload rather than asserted.

## 12. Incidental finding, not acted on

`data-api /activity?user=<addr>` returns rows with `"type":"REDEEM"` alongside
`"type":"TRADE"`. A REDEEM is a settlement. The module docstring states that the
`wallet_record_unmeasured` gate is blocked because "`/trades` returns fills, not
outcomes" and carries no `won` or `realized_pnl` flag. That is true of `/trades`
and it is not necessarily true of `/activity`, and `user-pnl-api` supplies a
realized PnL curve on top of it. Whether either can be turned into the
per-trade settled record that `record_from_rows` expects was **NOT TESTED**
(convention 11) and is out of this task's scope. It is a lead, not a result.
