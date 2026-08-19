# Ratify 029 RE-GATE (D-338), venue universe check, AGENT_ID re-measurement - EXECUTED

**Session:** `cody-ratify-029`, 2026-08-19 ~04:56-05:00 EDT
**Directive:** `docs/handoffs/from-raven/2026-08-19-ratify-029-universe-check.md`
**Commit:** `2f2e19c`, pushed to `main`. Tree clean.
**Harness:** re-derived this session, 21/21 PASS, exit 0 (convention 1).

## Headline

**Re-gate condition (a) is ANSWERED, and it goes the other way: native 15m crypto
up/down markets EXIST on the venue.** The ruling was braced for "they do not
exist -> re-scope onto 5m or retire". That branch does NOT trigger. 029's
"15m only, never 5m" premise is sound on the venue.

**The zero `-15m-` rows in `signals` is a signal-KEYING artifact, not a universe
absence and not a fetch-layer discovery gap.** Our loop already fetches 15m
markets and their books every cycle, and is doing so right now.

## Task 0: D-333 WAIT guard - CLEARED

- `ps aux | grep "claude -p"` showed only my own PID `94010` plus the tmux
  wrapper `37068` (a `tmux new-session` process, not a live sibling session).
- `git status --porcelain` empty.
- Two `git rev-parse HEAD` reads, seconds apart, both `6997940...`.
No wait was needed.

## Task 3: AGENT_ID - SET. Open item 11 closes as FIXED-BY-SPAWN-EXPORT

`os.environ.get('AGENT_ID')` read **`cody-ratify-029`** at session start.
`CONFLICT_CHECK_AGENT_ID` was `None` and was never set by me.

That is the **fourth** data point, and the first on a bare `claude -p` spawn to
read SET. Readings now: `cody-d337-ratify` SET (03:52), `cody-037-rename` EMPTY
(04:12), `cody-open-items` EMPTY (04:30), **`cody-ratify-029` SET (04:56)**.

End-to-end confirmation, not just the read: the commit went through as a plain
`git commit -- docs/DECISIONS.md` with **no** python subprocess, **no**
`CONFLICT_CHECK_AGENT_ID`, and **no** bypass flag. Both hooks resolved the
identity from the exported var and the D-335 trailer check passed
(`Agent-Id: cody-ratify-029` matches the resolved identity). The convention-33
corner that D-334 warned about is gone on this spawn path.
**Recommend Raven confirm on review before declaring it closed.**

## Task 1: D-338 recorded in DECISIONS.md

- Transcribed **verbatim**. Verified programmatically against directive lines
  42-48: 2,555 chars both sides, **EXACT MATCH**, no re-wrap, no added backticks.
- **Append-only:** `git diff --numstat` = `16 0`, zero deletion lines.
- **Hash-guard:** H0 `21166fc8f8ba...8c37` recorded at read, re-hashed
  immediately before write, UNCHANGED. Written via
  `engine.concurrency.safe_edit(agent_id='cody-ratify-029')`.
- My recording-session note sits **outside** the ruling text (convention 31), and
  is where the venue finding lives. The ruling itself is not amended.

## Task 2: Venue universe check - verdict **EXIST**

Read-only public endpoints only. No auth, no wallet, no orders, no config or key
touched. Measured 08:56-08:58 UTC 2026-08-19.

**Coverage: 48 of 48 probes FOUND.** `gamma-api.polymarket.com/markets?slug=
{asset}-updown-15m-{ts}` across btc, eth and sol, 16 consecutive 15-minute
boundaries each (4 hours). Zero misses. 5m probes ran as a positive control and
also returned 6/6 on btc.

**Native, not corridor constructs.** Live btc window
`btc-updown-15m-1787129100`, id `3692579`, question "Bitcoin Up or Down -
August 19, 4:45AM-5:00AM ET", `endDate 2026-08-19T09:00:00Z`:

| field | 15m market | co-expiring 5m market |
|---|---|---|
| id | `3692579` | `3692757` |
| conditionId | `0xbabc2cff...345cc` | `0xaca44525...d40649` |
| acceptingOrders | true | true |
| enableOrderBook | true | true |
| liquidityNum | 6,138.15 | 10,206.10 |
| volumeNum | 4,029.33 | 32.96 |

Distinct `conditionId`, distinct `clobTokenIds`, distinct id. The 900s span is
real: window ts `08:45:00Z` -> `endDate 09:00:00Z`.

**Real book, real volume.** `clob.polymarket.com/book` on the Up token returned
**6 bid levels against 92 ask levels**, ~1,667 shares of bid depth against
~7,129 of ask depth (Down token mirrors it). Settled btc 15m windows carried
$17,789 / $35,523 / $29,138 volume on the three preceding boundaries - **more**
volume than the 5m windows, not less.

**Why our tape shows zero.** The loop already reads them:
`engine/polymarket/shadow_loop.py:1986-2124` (`include_15m` defaults True,
15m market read in stage 2, 15m books fanned out in stage 3),
`engine/polymarket/context.py:357-384`, `strategies/polymarket/base.py:243-246`
(`ctx.book_15m(side)`). Three strategies consume it: `corridor_collector`,
`corridor_pair_live`, `longshot_fade_hold_to_resolution`. And it is live **now** -
`logs/polymarket_shadow_20260819T072834Z.log` records `strategy:ask_15m_above_cap`
and `strategy:not_final_third_of_15m` from 03:41 onward.
The gap is only that no strategy KEYS a signal row to the 15m market; `pair` is
written as the 5m slug, so the 15m tape is invisible to any `pair` filter.
**Convention 20: that is a missing number, and it is what made the universe look
empty.**

**Independently re-derived** (not quoted): `signals` = 687,861 rows, 567,188
up/down, `pair LIKE '%-15m-%'` = **0**, `pair LIKE '%-5m-%'` = 567,188, 884
distinct 5m markets. Reproduces the ruling's zero; the 5m count drifts up from
Raven's 881 and `cody-open-items`' 869 exactly as a growing tape should.

**Trap for whoever runs the gate:** gamma's `bestBid`/`bestAsk` on the 15m market
read 0.63/0.64 while the live CLOB book for the same token was 0.06/0.08 three
minutes from expiry. **Read the book, never the gamma summary fields.**

## What is NOT resolved

- **Conditions (b) and (c) still block 029.** (b) the unselected-market
  calibration tape does not exist. (c) the forecast-free direction check is
  untouched. The universe answer removes one blocker of three.
- **The consequence is offered, NOT self-applied.** I did not change 029's status
  or file, and did not amend D-338. Raven ratifies or corrects.
- I did **not** measure whether a 15m-keyed strategy would find tradeable
  imbalance. Existence of a book is not evidence of edge.
- Coverage was 4 hours on one read. Not a 24h claim.

## What I did NOT touch

The daemons (no restart, signal or edit - 71360/71394, 71442, 48637, 37578 left
alone), every proposal file including 029's, `tests/`, `engine/`, `agents/`,
`strategies/`, `scripts/`, `config.yaml`, the registry,
`run_polymarket_shadow.sh`, `docs/CONVENTIONS.md`, the forge briefs, and every
DECISIONS.md entry other than the new D-338. The 24h complement re-derivation was
NOT run (window not warm until ~03:28 2026-08-20). db read-only via
`mode=ro` URI throughout.

## For Raven

1. **Ratify or correct** the consequence: condition (a) = EXIST, so the
   re-scope-or-retire branch does not fire.
2. **Confirm open item 11** closes as FIXED-BY-SPAWN-EXPORT (4th reading, SET,
   plus a clean plain-commit through both hooks).
3. **New item:** should a strategy key signals to the 15m market so the tape is
   queryable by `pair`? That is the concrete unblock for 029's gate, and it is a
   recording change, not a new strategy.
