# D-316: full market-space redeclaration ruling, and both permanently-red tests fixed

**Session:** Cody, 2026-08-18 16:35 to ~16:52 PT (headless, PID 99123/99143).
**Task file:** `docs/handoffs/from-raven/2026-08-18-d316-full-redeclaration-and-test-rulings.md`
**Tests:** 3,500 passed, 0 failed, 1 skipped (5m48s) - was 3,498 passed, 2
failed, 1 skipped before this session.
**Harness:** `backtest/validate_harness.py` 21/21, exit 0.
**Loop:** NOT restarted (per task rules). These declarations do not take
effect until it is.

---

## Peer check

No other headless session was working on this repo. `ps aux` showed two other
`claude -p` sessions (PIDs 98012/99145/99163/99123-tree) but all were on
`06-career-agent`, not this one. `env -u PYTHONPATH python3 -m engine.concurrency who`
returned zero open checkouts. Clean start.

## Task 1: full redeclaration - most of it was already done, and the rest declines honestly

The task file assumed the registry needed widening from scratch. It did not.
`fair_value_arb` (and its four thin variants) and `dip_arb` already declared
crypto + event + sports + political from an earlier pass (before this
session); `smart_money_copy` already declares all six types. What was actually
missing and actually decided this session:

**Widened: `fair_value_arb` family (5 strategies) and `dip_arb` gained
`MARKET_TYPE_WEATHER`.** Nobody had added weather to that earlier pass - not a
deliberate exclusion, just a pass that predates the weather space existing as
a poll target.

- `fair_value_arb`'s gate is `if not ctx.is_crypto_window: SKIP
  fair_value_model_needs_crypto_spot`, one uniform check that already covered
  weather along with the other three types. This strategy will ALWAYS skip
  off-crypto - the MODEL is a crypto price model - but the skip is now a
  named, counted row instead of silence, matching what it already does for
  event/sports/political.
- `dip_arb` is genuinely different: its docstring already argues (and I
  verified by reading `evaluate()`) that it touches only `ctx.market`,
  `ctx.books` and the clock - no spot, no strike, no windows. `CANDIDATE_SIDES`
  already carries `Yes`/`No` alongside `Up`/`Down` for exactly this reason. A
  weather market's token also lives for days, so the mean-reversion tape is
  exactly as continuous there as on an event/political market. **This one can
  genuinely ENTER off-crypto**, not just log a uniform refusal.

**Declined, checked and confirmed structural - no code change:**
`streak_snapper`, `mid_price_continuation`, `box_builder`,
`corridor_collector`, `temporal_arbitrage`, `corridor_pair_live`,
`spread_harvest_maker`, `liq_cascade_chaser`, `small_liq_continuation`,
`near_liq_trigger`, `grid_hedge`, `maker_rebate_corridor_quote_ladder`. I read
every `evaluate()` in this list, not just the class attribute. Every one of
them requires `ctx.spot`, `ctx.strike`, `ctx.windows`/`ctx.atr14`, a
`market_15m` companion, or a liquidation feed keyed to the SAME crypto asset -
none of which a general-binary or weather `MarketContext` carries (confirmed
against `build_space_context`/`build_weather_context` in `shadow_loop.py`,
which never set any of those fields). Several of these would not even crash if
widened - they already have a safe named skip on a missing clock or missing
spot (`temporal_arbitrage`, `spread_harvest_maker`, `liq_cascade_chaser`,
`near_liq_trigger`) - but a strategy that would ALWAYS skip on every single
poll of every market in a space is not "declaring a space its inputs can
exist in." That is what "do not fake declarations" rules out, and it is also
the exact set of examples Aym's own ruling text named (streak_snapper,
mid_price_continuation, temporal_arbitrage) as unable to evaluate a sports
market.

**One correction to the task file's premise:** it stated
`PM_maker_rebate_quote_ladder` (024) "already declares broadly." It does not -
it declares `(MARKET_TYPE_CRYPTO_UPDOWN,)` only, and its own docstring gives
the reason (every gate is a 300/900-second window-clock gate; "declaring
anything wider would be claiming support for a universe this has never been
evaluated against"). That reasoning is correct and I left it unchanged.

**No change:** `smart_money_copy` (already all six types) and `weather_arb`
(stays weather-only; a temperature-and-METAR model with no honest reading on
anything else).

Full reasoning, one strategy at a time, is in DECISIONS.md as D-316 (I wrote
the entry - it did not exist before this session, despite the task file
referring to "D-316" as if it were already ratified).

**This needs a restart to take effect.** The loop (PID 90192 per the last
CLAUDE.md, unconfirmed by me this session - I did not check `ps -p` on it) is
running pre-D-316 source. Per the task rules I did not restart it. Once
restarted, `PM_dip_arb` and the `fair_value_arb` family will start being
polled in the weather space alongside `PM_weather_arb` and
`PM_smart_money_copy`.

## Task 2: both permanently-red tests fixed, both pass now

1. `test_polymarket_risk_gate.py::TestConfigWiring::test_config_yaml_matches_the_module_defaults` -
   excluded `daily_loss_limit_usdc` and `portfolio_daily_loss_limit_usdc` from
   the blanket equality-with-module-defaults loop and asserted the override
   relationship directly: config ships both at 0.0 (no shadow-mode stop, by
   design), the module keeps 30.0 as the live-mode fallback for a caller with
   no config. No daily stop limit was introduced anywhere - the risk gate's
   `> 0` gate on both fields is unchanged.
2. `test_r007_r008_fixes.py::test_stale_reason_string_is_emitted_by_no_live_code_path` -
   added `tests/test_hypothesis_graph.py` to the allowlist. Verified it
   carries the string only as a synthetic fixture value (`not_tested_reason=`
   in two places), never in a live-emitting path. Not renamed, per the
   ruling.

Both changes are pure test-assertion fixes; no production risk-gate or graph
code moved.

## What I did NOT do

- Did not restart the loop (task rule).
- Did not touch `research/polymarket_paper/polymarket_paper_log.csv` (it's
  being appended live by the running loop; showed up modified in `git status`
  from that process, not from me).
- Did not commit. The working tree carries the untracked
  `2026-08-18-market-spaces-wired-and-corridor-correction.md` and
  `research/shadow-stats-before-spaces-2026-08-18.md` from the prior session,
  plus the live-appended CSV - not "otherwise clean," so per the task rules
  I'm reporting instead of committing.

## Exactly what changed (for whoever commits)

```
M docs/DECISIONS.md                          (new D-316 entry)
M strategies/polymarket/fair_value_arb.py     (+MARKET_TYPE_WEATHER, comment)
M strategies/polymarket/dip_arb.py            (+MARKET_TYPE_WEATHER, comment)
M tests/test_polymarket_risk_gate.py          (override-relationship fix)
M tests/test_r007_r008_fixes.py               (allowlist widened)
```
Plus the untracked `CLAUDE.md` rewrite (not git-tracked, per its own header).
`research/graveyard/harness_validation.json` also shows a 1-line diff from
running `backtest/validate_harness.py` to verify convention 1 - harmless
regeneration, not a content change I made.

All five files were re-registered through `engine.concurrency.safe_write`
after editing, per the pre-commit ledger requirement.

## Needs a ruling from Aym (unchanged from before, plus one new item)

Everything on the prior list still stands (loop restart, D-305..D-315
ratification, weather sigma, etc.) See the last handoff and CLAUDE.md. New:

13. **Ratify D-316** (this session's entry) alongside the D-305..D-315 batch.
