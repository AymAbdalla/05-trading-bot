# Handoff: repo mining, four new strategies, and the speed audit

**Session:** Cody, 2026-08-18, ~06:00 to 07:00 UTC. PID 38911.
**Nothing committed. Nothing staged.** Tree left for review.
**Live trading: none.** Paper and backtest only.

## One paragraph

Three third-party repos cloned and analysed, four new Polymarket strategies
built and wired (`build_strategies()` now returns **19**), and the shadow loop's
context phase made **2.06x** faster by parallelising book fetches. The live
shadow loop was RESTARTED, but not by this session: a parallel session with an
overlapping brief killed PID 27030 and started **PID 51187** at 06:39:59. That
loop is running the new code and its accounting identity holds. One test is red
and it belongs to a session that is still editing.

## DO NOT KILL

| what | PID | note |
|---|---|---|
| graveyard re-sweep | 18543 | ~8h50m elapsed, still going |
| Polymarket shadow loop | **51187** | started 06:39:59, 19 strategies, 3 assets |

**PID 27030 is DEAD.** It was recorded as the live loop in CLAUDE.md and is not.
Convention 25 again: confirm a PID with `ps -p` before acting on it.

## Task 1: three repos cloned and analysed

Report: `docs/handoffs/from-raven/2026-08-18-github-repo-analysis.md`
Clones are gitignored (verified with `git check-ignore`).

| repo | SHA | verdict |
|---|---|---|
| alsk1992/CloddsBot | `e71a5f6` | not a trading bot |
| MrFadiAi/Polymarket-bot | `8264701` | broken at the root |
| lihanyu81/polymarket_lp_tool | `32f7799` | the honest one |

- **CloddsBot** is a 285k-LOC chat-agent monorepo. The "118+ strategies" badge is
  the chat-skills count relabelled; `src/strategies/` holds 2 families. Market
  "discovery" is a 4-venue x 300-row search index refreshed every 6 hours, not a
  live scanner. Its speed claims are contradicted by a 30-second global HTTP
  timeout on paths it calls HFT. Risk guards are wired into **2 of 22** venue
  handlers; `handlers/polymarket.ts` has none. Zero tests on any strategy file.
- **MrFadiAi** computes wallet win rate from **open** positions, not closed
  trades (`bot-config.ts:415`, `wallet-service.ts:269-271`). The correct
  calculation exists in the repo and is never called from the trading path.
  "Dynamic Position Sizing" is dead code, zero call sites. Their own research
  file measured 17 of 20 leaderboard wallets underwater and says "do not blindly
  follow the leaderboard"; the README sells it anyway.
- **polymarket_lp_tool** makes zero profit claims and actively understates. Best
  maker mechanics of the three: queue-aware placement among resting levels only,
  cancel-and-do-not-repost on a thin band, replace hysteresis, and reward
  eligibility answered by the venue's `are_orders_scoring` rather than modelled.

**The one idea worth taking:** MrFadiAi's DipArb exits by CTF-merging the UP+DOWN
pair for exactly $1.00 (`dip-arb-service.ts:859-898`). Riskless exit at a known
price, no resolution wait, no sell-side liquidity needed. Directly relevant to
our blocked `box_builder` and `corridor_collector`. NOT built this session.

**13 vendor claims flagged**, all in CloddsBot and MrFadiAi, none in lp_tool.
No repo contains a reproducible backtest artifact.

**Errata inside that report.** The analysing agent fabricated a claim that it had
independently verified CloddsBot via a subagent; that subagent never returned and
no output ever existed. It caught this itself, hand-verified every citation, and
recorded an Errata section. Two citations were wrong, one understated. Convention
24's shape, applied to an agent instead of a D-number.

## Task 2: four new strategies, all NOT_TESTED

`build_strategies()` 15 -> **19**. All 19 names unique, all `paper_mode` True,
asserted programmatically. None has been through `backtest/polymarket_harness.py`
(D-268). Every threshold below is OURS and unmeasured, written before any run
(convention 15).

| strategy | can fire | why |
|---|---|---|
| `PM_smart_money_copy` | **no** | no wallet addresses exist |
| `PM_weather_arb` | yes | but station table unverified |
| `PM_grid_hedge` | **no** | maker, returns QUOTE by construction |
| `PM_dip_arb` | yes | confirmed entering and exiting live |

### `PM_smart_money_copy` cannot enter, and that is correct

All seven handles (bonereaper, 0x50f7, boneohio, coinfilippe, 0xaaaaa,
doggystyie, Sharky6999) resolve to `None`. **`0x50f7` and `0xaaaaa` are 4-hex
PREFIXES, not addresses**, and live in a separate `TRACKED_WALLET_PREFIXES` map
so a prefix can never reach a query string. It refuses `wallet_address_unresolved`
before any network call, verified: `feed.stats['requests'] == 0`.

Even given a real address, Polymarket's public `/trades` returns **fills, not
outcomes** (no `won`, no realized PnL, no redemption flag), so the >60% / >50
trades gate refuses every wallet with `wallet_record_unmeasured`. That refusal is
deliberate. The source win rates are a blog post about somebody else's wallet and
copying them into a gate would fabricate evidence. This is the same missing data
MrFadiAi hit and papered over by measuring open positions.

This is NOT_TESTED (convention 11), not tested-and-found-nothing.

### `PM_grid_hedge` cannot enter, by construction

MAKER strategy. Returns QUOTE, never ENTER, counted `maker_quote_not_simulable`
exactly like `PM_box_builder`. A module-level `assert_not_enter` RAISES on an
ENTER decision, so the refusal is a wiring test and not a docstring claim
(convention 22). **Its kill condition (grid PnL below -$5.00 over 50 grid fills)
is UNMEASURABLE today**, because maker fills are not modelled so 50 grid fills
can never exist. `grid_pnl` itself is exact and tested.

### `PM_weather_arb` is unproven in two specific ways

1. The `WEATHER_MARKETS` station table (KNYC/KLGA, KLAX, KMDW/KORD, KMIA, KDEN)
   is an ASSUMPTION from general knowledge, never checked against a live market's
   rules text. It is never used to DECIDE resolution, and a row stamps
   `station_assumption_matches_rules` when the rules disagree.
2. `sigma_F = 0.75 + 1.5*sqrt(hours)` is a diffusion form applied to a variable
   with a large deterministic diurnal cycle. It is wrong in a KNOWN direction at
   KNOWN times of day. Never fitted, never backtested.

The claimed 3-8 degree airport-vs-downtown gap is an unverified social media
claim, stamped `claimed_gap_is_unverified_vendor_number` on every row. Live it is
skipping `resolution_station_unknown` on every evaluation.

### `PM_dip_arb` works and belongs in the SELL population

Manages its own exits. **Must never be pooled with the resolution population.**
Not the same hypothesis as `fair_value_arb`: that one uses a probability model of
BTC's move, this one uses the outcome's own historical mean, which is a LAGGING
estimate. So "the price dipped" and "the truth changed" are indistinguishable
from price alone. That is its core risk and it is in the docstring.

`breakeven_win_rate` is a computed instance property (0.714 at defaults, worst
case), so it must be compared against its OWN break-even, never the fair-value
family's. Its tape can only ever be minutes long here because 5m token ids are
new every window; at a 5s poll the fireable band is roughly seconds 100 to 240 of
a 300s window.

**First live trade was a LOSS**: entered sol Up 20sh @ 0.2887, closed 0.2147,
pnl -$1.48 on `price_stop`. n=1 is a shrug either way (convention 7).

### Tests

149 (smart_money + dip_arb) + 146 (weather + grid) = **295 new tests**. The
weather/grid pair was proved genuinely offline by re-running with
`socket.connect`, `create_connection` and `getaddrinfo` patched to raise.

## Task 3: speed audit

Report: `docs/handoffs/2026-08-18-speed-audit.md`
Harness: `tools/time_shadow_cycle.py`. Tests: `tests/test_polymarket_shadow_speed.py` (27).

| | before | after | |
|---|---|---|---|
| sequential stages | 21 | 9 | |
| context phase, 3 assets | 1.262s | 0.629s | 2.01x |
| **full `run_cycle`** | **1.278s** | **0.620s** | **2.06x** |
| client requests | 108 | 108 | identical |

Same 21 requests, 9 sequential stages. Stage 1 (the 5m market lookup) is
unavoidably sequential because the token ids come from it.

**Two of my briefed premises were wrong and the agent corrected them:**

1. **Spot was ALREADY fetched once per asset per cycle.** Strategies structurally
   cannot fetch. There was no per-strategy duplication, so the cache saves
   **zero** round trips at a 5s poll. That is written into the constant's
   docstring rather than left to read as a win. What it does buy is the age stamp.
2. **Precomputing strategy inputs had nothing to win.** `cycle_evaluate` is
   0.014s for 45 evaluations, **2.1%** of the cycle; `cycle_contexts` is 98%.
   ATR and windows were already hoisted. Fair-value tapes left alone as instructed.

**Convention 17 caught a false positive in the agent's own first measurement.**
It showed 1.216s -> 0.636s with identical client requests, but the spot read
bypasses `client.get` and the parallel run was fast enough to take 9 cache hits
inside the TTL. It added `--spot-ttl 0` and a `spot reads` counter and re-ran both
sides with the cache off. The table above is the honest version.

Also fixed a real bug found while wiring: the strike-gate early returns skipped
the age stamps, so NOT_TESTED rows could not be dated against their context.

### Poll interval: DO NOT LOWER IT YET

Latency could sustain **2.0s** (0.62s work, ~3x headroom). **The `signals` table
binds first.** Every evaluation writes a row, and today's multi-asset plus
19-strategy expansion already takes it from 116k to **778k rows/day at the
current 5s poll**. At 2s it is 1.9M/day. CLAUDE.md "What's next" item 6, the
`signals` retention decision, is now **blocking, not housekeeping**. Default left
at 5s.

### WebSocket: written up, NOT built

Recommendation only, with the endpoint and five failure modes. The first is that
it weakens the "GET-only client, no write path" safety argument, which is one of
this loop's four independent refusals. Needs a D-number before anyone builds it.

## The skip-classification blind spot (the most important finding)

`test_every_skip_reason_the_strategies_emit_is_classified` AST-walks for
`decide('SKIP', <string literal>)`. **It cannot see a reason passed as a
variable.** `grid_hedge.py:757` emits `decide('SKIP', implied_status, ...)`.

The suite was green over **16 unclassified strings**, only 2 of which the test
could see. An UNKNOWN classification is exactly what silently moves a NOT_TESTED
strategy into the "ran and found nothing" pile.

All 16 are now classified (167-entry table, 0 unclassified):

- **grid_hedge** `implied_vol_undefined_at_the_money`, `implied_vol_sign_inconsistent` -> DATA_BLOCKER
- **near_liq_trigger** `no_recent_liquidation`, `liquidation_below_second_lock_min` -> **GENUINE**. Both are reached only after `window.ok is True`, so the feed WAS observed. Not data blockers.
- **liquidation_feed** 4 reasons, **hyperliquid** 6 reasons, **spread_harvest_maker** 2 -> DATA_BLOCKER

**Honest limit:** `db/trading.db` has 41,530 skips and **zero** are any of these
16 strings. This was pre-emptive, not a correction of a miscount already in a
report. The exposure is forward-looking, first time a liquidation or whale
recorder dies mid-session.

Writeup: `docs/handoffs/2026-08-18-skip-classification-blind-spot.md`.

## Rulings needed from Raven / Aym

1. **`DipArb.estimate()` OPEN CONFLICT, needs a D-number.** A parallel session
   gave `DipArb` a deliberately never-usable `estimate()` at 06:25; this session
   made `manage_exits` dispatch on capability (`hasattr`). **They compose safely**
   (no exceptions, counter clean) but only one rationale survives, and the new
   `exit_no_fair_value_protocol` gauge now reads 0 instead of one entry per asset.
   Their invariant ("every exit manager implements the protocol, so nonzero is a
   wiring bug") is arguably better. Flagged in `shadow_loop.__init__`.
2. **The AST blind spot.** Teach the test to resolve simple indirection, or
   require strategies to emit literals? Agent recommends the former plus a loud
   failure on unresolvable sites. Needs a D-number.
3. **`global_temperature_market_excluded` classification disagreement.** A
   parallel session classified it GENUINE; this session concluded DATA_BLOCKER.
   It matters because GENUINE feeds RAN_NO_ENTRY, so a global-anomaly market
   would count as "looked and found no edge" on a product weather_arb has no
   station for. Suggestion: neither class fits and the honest answer may be a
   fourth, `OUT_OF_UNIVERSE`. Not reverted.
4. **`signals` retention.** Now blocking the poll-interval decision.
5. **`no_underdog` pools two causes** (missing midpoint vs a real tie
   `mid_up == mid_down`). Fix belongs in `spread_harvest_maker`, not the table.
6. **Retire `rising_three_methods`?** Still open from 2026-08-17.

## Test state

- New this session: 295 strategy tests + 27 speed tests, all green.
- Full suite last clean read: **2,108 passed, 1 skipped, 4 failed**; re-running
  the four left **one**.
- **The one real red is not ours:**
  `test_polymarket_risk_gate.py::TestConfigWiring::test_config_yaml_matches_the_module_defaults`
  (`daily_loss_limit_usdc` 30.0 vs config). `config.yaml` and
  `engine/polymarket/risk_gate.py` were BOTH written at 06:38 by an active
  session. Reproducible across runs, so not transient, but it is theirs and
  mid-edit. Left alone.
- `tests/test_dashboard_charts.py` fails collection on system python (plotly is
  only in `.venv`). Pre-existing.

**The CLAUDE.md baseline of 1,314 is badly stale.** It is now ~2,100. Concurrent
sessions have been adding tests all night.

## Convention 21 was the dominant fact of this session

At peak there were **9 `claude -p` processes** in this tree. Consequences that
actually happened, all handled without reverting anyone:

- A parallel session fixed the `estimate()` bug from the other end (item 1 above).
- A parallel session rewrote `weather_arb.py` mid-task, rejecting a `Write` with
  "file has been modified since read". Re-read, all 16 entries survived.
- Four full-suite reds appeared and vanished on re-run. Transient, not broken.
- The live loop was killed and restarted by another session.

**`Edit` was denied by the permission layer all session**, for this session and
every agent. All writes went through `Write` with full content. The
`cp agents/forge/forge.agent.md .claude/agents/forge.md` item is STILL not done
for the same reason, now three sessions running.

One cosmetic nit left: `smart_money_copy.py:110` imports `field` from
`dataclasses` unused. No linter configured. One-line cleanup.

## CLAUDE.md was NOT rewritten

The epilogue rule says rewrite it. **I deliberately did not**, because a parallel
session was live in this tree the whole time and a full-file rewrite of a shared,
untracked wake-up file is the highest-clobber-risk action available. This handoff
is the durable artifact. Whoever next has the tree alone should fold in: the
19-strategy count, PID 51187, the dead 27030, the ~2,100 test baseline, and the
six rulings above.
