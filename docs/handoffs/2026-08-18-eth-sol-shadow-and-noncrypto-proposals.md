# Handoff: ETH + SOL on the shadow loop, and 9 non-crypto proposals

**Session:** Cody, 2026-08-18 05:36 to 06:15
**Branch:** main, nothing committed, tree left for review
**Two tasks:** (1) add ETH and SOL Up/Down 5m markets to the shadow loop,
(2) Forge proposals for event, sports and cross-market strategies.

**The shadow loop was NOT restarted.** PID 27030 is still alive and still
running the BTC-only code (convention 13: Python snapshots source at import).
Aym said he would handle the restart. See "How to restart" below.

---

## Task 1: ETH and SOL are live on the shadow loop

### Verified before building, not assumed

Convention 3 applied to the market rather than the strategy. Every claim below
was read off a live endpoint on 2026-08-18, and my first probe was a FALSE
NEGATIVE that said all five assets were missing including BTC. It was a
transient failure with a missing User-Agent. Had I trusted it I would have
reported "ETH and SOL do not exist" and stopped.

| asset | 5m market | 15m market | settlement config | Binance.US | Coinbase |
|---|---|---|---|---|---|
| btc | yes | yes | `btc-5m-twap-60` | BTCUSDT | BTC-USD |
| eth | yes | yes | `eth-5m-twap-60` | ETHUSDT | ETH-USD |
| sol | yes | yes | `sol-5m-twap-60` | SOLUSDT | SOL-USD |

All three carry `twapEnabled: True, twapLookbackSeconds: 60`. That identical
mechanic is what makes reusing the BTC strategies legitimate rather than
hopeful: same instrument, different underlying.

**Also found, deliberately NOT wired:** `xrp-updown-5m` and `doge-updown-5m`
exist and are also `*-5m-twap-60`. Their Gamma markets are verified; their
exchange symbols are NOT. Registering them on a guessed symbol would put an
unverified string in a price path, and a wrong symbol fails as a missing spot
that reads like an outage. Verify two endpoints and they are a one-line add.

**`btc-updown-1h-{ts}` DOES NOT EXIST.** Checked directly and by enumerating
every active crypto market: every `duration` returned is `5m` or `15m`. An
hourly BTC market exists under a different slug family
(`bitcoin-up-or-down-<date>-<hour>-et`) with `cryptoMarketConfig: None`,
settling on a Binance candle open-vs-close and NOT a TWAP, with about $27 of
volume. This kills the 1h leg of the multi-timeframe idea.

### The design decision that matters: per-asset strategy instances

This was not "call the same code three times". Strategy objects hold mutable
per-window state, and sharing one instance across three assets would be
silently wrong in three ways that all produce plausible numbers rather than an
exception:

1. **`FairValueArb` owns a `PriceTape`** of (timestamp, spot) observations. One
   shared instance would push BTC at 64,000 and SOL at 76 into the SAME series.
   The model would read a 99.9% move every time the loop stepped between
   assets.
2. **`_window_trades`** is a per-window trade budget. Shared, BTC's trades would
   spend ETH's budget.
3. **`StrikeProxy` caches klines for ONE symbol.** Shared, SOL's strike would be
   rebuilt from BTCUSDT bars.

So each asset gets its own `AssetRuntime` holding its own strategy instances,
strike proxy and candle source. What stays shared is what genuinely is shared:
one paper adapter, one risk gate, one $1,000 bankroll.

**The subtlest bug this fixed before it shipped:** `manage_exits` keyed managers
by `strategy_name`. Every asset now runs an instance called
`PM_fair_value_arb`, so that dict collapsed to whichever instance was written
last, and a BTC position could be handed to SOL's instance and given a model
stop computed off SOL's displacement. A wrong exit that looks normal in every
log. Exits now route by `asset_for_slug(pos.market_slug)` and are keyed on
`(asset, strategy_name)`. A position on an unregistered slug is counted as
`unroutable_position` and left alone rather than guessed at.

### Files

**New**
- `engine/polymarket/assets.py` - the asset registry. One place where "btc"
  maps to a slug prefix, a Binance symbol, a Coinbase pair and a
  DataCollector pair. Before this, those four strings lived in four files.
- `tests/test_polymarket_multi_asset.py` - 23 tests, all offline.
- `tools/patch_apply.py` - see "One thing to decide" below.

**Modified**
- `engine/polymarket/markets.py` - `updown_5m_slug(asset, ts)`,
  `updown_15m_slug`, `get_updown_5m[_checked]`. Every BTC-named function kept
  as a thin wrapper, so existing callers and the running graveyard sweep are
  untouched.
- `engine/polymarket/context.py` - `fetch_spot_checked(client, asset)`,
  `spot_sources_for(asset)`; `resolved_windows*` and `build_context` take
  `asset='btc'`. BTC wrappers kept.
- `engine/polymarket/shadow_loop.py` - `AssetRuntime`, per-asset
  `build_context(window_ts, now, asset)`, `run_cycle` in three phases, exit
  routing, `--assets`, identity.
- `tests/test_polymarket_shadow_loop.py` - two fixture changes only. The
  stubbed spot function was renamed, and `build_loop` now pins
  `assets=('btc',)` so every existing exact-count assertion keeps testing what
  it was written to test.

### The accounting identity gained a factor and kept its meaning

    evaluations == entries + skips == cycles * strategies_per_asset * assets

`evaluations_per_cycle` SUMS `len(rt.strategies)` over the runtimes rather than
multiplying, so if assets ever run different strategy sets the identity still
describes what the loop does. A per-asset failure does NOT shrink the
denominator: an unlisted ETH market is attributed as `no_market` to each of
ETH's 15 strategies individually. There is a test for exactly that.

### Live proof, on a separate database

Convention 3. Two cycles against the real API, `--db /tmp/pmsmoke/smoke.db` so
the live session's data was never touched:

```
assets          : btc, eth, sol
strategies      : 15 per asset, 45 evaluations per cycle

PM SHADOW ENTER PM_fair_value_arb btc-updown-5m-1787047200 Up   20 sh @ 0.3400
PM SHADOW ENTER PM_fair_value_arb eth-updown-5m-1787047200 Down 20 sh @ 0.4300
PM SHADOW ENTER PM_fair_value_arb sol-updown-5m-1787047200 Down 20 sh @ 0.4575
PM SHADOW CLOSE PM_fair_value_arb eth-updown-5m-1787047200 0.4300 -> 0.3781 (price_stop)

cycles=2 assets=btc,eth,sol evals=90 entries=8 skips=82 identity_ok=True
```

90 == 2 x 15 x 3. Database check: 30 signal rows per asset, 15 distinct
strategy ids each, `features_json.asset` correctly stamped, and positions
opened on all three assets under the SAME `strategy_id` - which is the point,
it makes `PM_fair_value_arb` on BTC scoreable against itself on SOL with a
GROUP BY rather than a parsed string.

The risk gate blocked per market, separately, exactly as intended:
`max_positions_per_market_side: 1 open on eth-updown-5m-... down` alongside the
BTC and SOL versions. Each asset has its own slot.

### Tests

- `tests/test_polymarket_multi_asset.py` 23 passed
- `tests/test_polymarket_shadow_loop.py` 37 passed
- Full suite (minus dashboard, which needs `.venv` for plotly): **1,641 passed,
  1 skipped**

One caution for whoever runs the suite next. My first full run showed 7
failures in `test_fair_value_arb*.py` and `test_polymarket_new_strategies.py`,
all "build_strategies returns 15, expected 11". Re-running the same files
immediately gave 312 passed. **They were another session mid-edit, not
regressions.** This is the documented hazard at the top of CLAUDE.md and it is
real. Check `ps aux` before believing a red suite.

---

## Three things Raven and Aym should actually decide

**1. The daily loss limit will now bind about three times faster, and it is
ALREADY binding.** The live log at 05:33 shows the breaker firing 232 times:
`daily_loss_breaker: realized loss today =$30.08 > limit=$30.00`. That is with
BTC alone. Three assets on one $1,000 bankroll and one shared $30/day limit
means the loop will spend even more of its day halted. The breaker being
account-wide is CORRECT and is what the task asked for, and I verified it (one
adapter, one gate, one bankroll). But the limit was sized for one market. This
is a parameter decision, not a bug, and it is Aym's call, not mine. Nothing was
changed.

**2. The 5 bp strike noise floor was measured on BTC and is being applied to
ETH and SOL on an argument, not a measurement.** `STRIKE_PROXY_NOISE_FLOOR_BPS
= 5.0` comes from `backtest/measure_strike_proxy.py` over 199 BTC windows. The
argument for reusing it is strong (identical instrument, identical 60s TWAP,
identical kline reconstruction) but it is an argument. Worse,
`measure_strike_proxy.py` CANNOT currently measure the others: its lookup is
hardcoded to `get_btc_updown_5m_checked`. I did not change the harness, because
changing a measurement tool in the same session that adds the thing it would
measure is how a number becomes self-confirming. Every gated row is stamped
`noise_floor_measured_on: 'btc'` so no future reader can mistake the
inheritance for a measurement. Generalising that harness is the honest next
step.

**3. This needs a D-number and I did not take one.** DECISIONS.md now ends at
D-284, so D-285 is free, but other sessions are writing right now and two
sessions claiming one number is worse than no number (convention 24). Proposed
text:

> **D-285: the Polymarket shadow loop polls BTC, ETH and SOL 5m Up/Down
> markets.** All three carry an identical `*-5m-twap-60` Chainlink settlement,
> verified live 2026-08-18, which is what makes the BTC strategies applicable
> unchanged. Each asset runs its OWN strategy instances, strike proxy and
> candle source, because strategy state is per-window and per-asset; the
> bankroll, adapter and risk gate stay shared because the money is shared. The
> accounting identity becomes `cycles * strategies_per_asset * assets`. The 5bp
> strike noise floor is INHERITED from the BTC measurement and stamped as such,
> not re-measured. xrp and doge markets exist and are deliberately not wired.

---

## How to restart the shadow loop

The running process (27030) predates all of this. It picks up all three assets
with no argument change, because the CLI default is now `btc,eth,sol`:

```bash
kill 27030
./run_polymarket_shadow.sh          # or the existing --poll 5 --equity 1000
```

To keep it BTC-only, or to check one asset in isolation:

```bash
env -u PYTHONPATH python3 -m engine.polymarket.shadow_loop --assets btc
```

API load roughly triples (one market read plus two book reads per asset per
cycle, plus the 15m leg). At a 5-second poll that is comfortably inside the
documented 4,000 req/10s budget, but it has not been observed over a long run.

---

## Task 2: nine proposals, 008 to 016

Written by three Forge subagents in parallel. All nine validate against
`agents/forge.py` with zero refusals and zero warnings, all names unique. None
appended to `forge_runs.jsonl` (another session owns that file).

Note the floor that actually binds is **200bps**, not the 30 in convention 5:
`MIN_GROSS_EDGE_BPS_BY_ASSET_CLASS['PREDICTION_MARKET'] = 200`, one tick on a
mid-priced contract.

| # | name | kind | edge |
|---|---|---|---|
| 008 | pm_event_fair_value_arb | edge_hypothesis | 400 |
| 009 | pm_event_momentum | edge_hypothesis | 400 |
| 010 | pm_event_contrarian | edge_hypothesis | 900 |
| 011 | pm_sports_live_momentum | **experiment** | **null** |
| 012 | pm_sports_pregame_value | edge_hypothesis | 650 |
| 013 | pm_sports_late_game_longshot | **experiment** | **null** |
| 014 | pm_btc_eth_beta_residual | edge_hypothesis | 330 |
| 015 | pm_btc_sol_beta_residual | edge_hypothesis | 330 |
| 016 | pm_btc_multi_timeframe_coherence | **experiment** | **null** |

**Three of the nine argue against themselves, which is the most useful output
here.** 011 (live sports momentum) prices out NEGATIVE: to clear 200bps we
would have to buy within the first 1 to 5 seconds of a score, and our poll
cadence alone is `DEFAULT_POLL_SEC = 5.0` before a public score API's 10 to 60
second lag. We lose the race by 3x to 70x. 013 (late-game longshot) prices out
at minus 6,000bps and the favourite-longshot literature runs the OPPOSITE way
to the thesis: longshots are systematically OVERpriced. 016 recommends being
folded into proposal 005 rather than built, and its 1h leg is dead because the
market does not exist.

**Two measured corrections to the assumptions in the task.** The
cross-market agent pulled 1,000 aligned 5m bars from Binance.US rather than
taking the premise:

- "BTC/ETH/SOL are 0.8 to 0.9 correlated" is **not what the data says**.
  corr(BTC,ETH) = 0.613 and corr(BTC,SOL) = 0.468 at 5m; 0.353 and 0.254 at 1m.
  That cuts both ways: it weakens the "leveraged one-factor bet in a hedge
  costume" objection AND weakens the strategy, because at R-squared 0.376
  "ETH has not caught up yet" is usually not a catch-up situation.
- "SOL, higher beta" is **measurably false**. beta(SOL|BTC) = 0.448 versus
  beta(ETH|BTC) = 0.787. SOL is the lower-beta, lower-vol, thinner-book asset.

**The most dangerous proposal is 012, precisely because it looks the best.** It
is the only sports one with a clean positive number (650bps). Its own author
flagged it as weakest: the number rests on one hand-worked example because no
sportsbook feed exists, its 4c gap filter selects exactly the observations where
our measurement error is largest (the `COST_FLOOR = -0.30` shape, convention
17), and a 2-sigma read needs roughly 5,970 resolved trades against a harness
minimum of 200. A PASS at 200 would be a shrug that reads like a result.

**Every proposal that needs a feed we do not have says so in capitals.** There
is no polling feed, no event-market price history, no news feed, no sportsbook
odds feed, no live score feed, no measured BTC/alt lead time, and no paired
multi-asset history. 009 and 010 also state they are arguably ONE hypothesis
with two arms and must count as one in the multiple-comparisons budget.

---

## One thing to decide, and one thing I did not do

**`tools/patch_apply.py` is scope I added.** This session had no Edit tool, only
Read/Write/Bash, and three other Cody sessions were editing the same files. A
full-file Write would have clobbered their concurrent work. So I wrote a small
exact-match patcher that refuses unless an anchor appears exactly once and
applies a patch set all-or-nothing. It is 50 lines, documented, and used for
every edit in this session. Keep it or delete it; nothing depends on it at
runtime.

**I did not rewrite CLAUDE.md, and that is a deliberate deviation from the
session epilogue rule.** The rule says rewrite it, not append. But three
concurrent sessions are finishing right now and at least two are writing
strategy files and tests; a full rewrite from my view of the tree would have
silently destroyed their entries, which convention 21 forbids. The handoff is
the durable artifact and it is complete. **Raven or the last session standing
should fold this into CLAUDE.md**, specifically: the loop is multi-asset, the
CLI default is `btc,eth,sol`, `assets.py` is the registry, and the
daily-loss-limit consequence in item 1 above.

## Not done, deliberately

- Did not restart the shadow loop (Aym's explicit instruction).
- Did not touch the running graveyard sweep (PID 18543).
- Did not generalise `measure_strike_proxy.py` to ETH/SOL. See item 2.
- Did not change the daily loss limit. See item 1.
- Did not wire xrp or doge.
- Did not append to `forge_runs.jsonl` or claim a D-number.
