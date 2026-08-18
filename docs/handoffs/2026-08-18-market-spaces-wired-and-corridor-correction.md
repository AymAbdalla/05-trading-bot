# Market spaces wired, proposal 024 registered, corridor thesis corrected

**Session:** Cody, 2026-08-18 19:50 to 22:35 UTC.
**Task file:** `docs/handoffs/from-raven/2026-08-18-full-polymarket-and-career.md`
**Commit:** `ea30111`
**Tests:** 3,498 passed, 2 failed, 1 skipped (5m40s).
**Harness:** `backtest/validate_harness.py` 21/21, exit 0.

---

## Read this first: three sessions were pointed at one task file

Raven spawned TWO headless Cody sessions at 15:52:39, both on this same file,
one per project folder. I am a third, interactive session started at 19:50.

- PIDs 91555 and 91576 were alive four hours after spawn having written
  **nothing**. `find . -newermt "2026-08-18 15:00"` returned zero files.
- 91576 reached me over the session bridge and we agreed a split: I take
  tasks 1-4 (trading bot), it takes 5-6 (career agent). It confirmed it would
  not touch this repo.
- **I had already spawned a subagent onto the career agent before that message
  arrived.** I killed it immediately. It wrote nothing: verified against
  `git status` and mtimes in 06-career-agent, which showed only the
  pre-existing set. No damage, but it was a real near-miss and it is recorded
  here rather than quietly dropped.

**Tasks 5 and 6 are NOT in this handoff.** They belong to the other session and
it is writing its own.

## The task file was written against a stale tree, on BOTH sides

This matters more than any single item below. Raven's file describes work that
a 14:00-ish session had already done:

| Raven's ask | Actual state when I started |
|---|---|
| 1b "fix the Gamma bug: `order=volumeNum` not `order=volume`" | Already fixed. `markets.py:80`, D-302. |
| 1c "add `search_sports_markets()`" | Already built, tagged sweep across NFL/NBA/MLB/NHL/soccer/tennis/esports. |
| 1d "add `search_political_markets()`" | Already built. |
| 3 "implement proposal 024" | Already written, 656 lines, untracked and unregistered. |
| 5a (other session) "broaden job titles" | Already shipped, all 11 titles, and already through a bug cycle. |

Nothing here was built fresh, and I have not reported it as such. The other
session independently found the same pattern on its side.

## What was ACTUALLY missing: the cycle, not the scaffolding

The afternoon session left the market-space work half-landed. `MarketSpace`,
`space_status`, the seven `SPACE_*` constants, the four `DEFAULT_SPACE_*`
constants and the D-312/D-313 citations all existed. What did not exist:

- `run_space_cycle` - referenced in a comment, never written.
- Any instantiation of `MarketSpace`. No `self.spaces`, no timer, no stats.
- Any import of the sports or political scanners into the loop.

So three universes' worth of discovery code was built, tested, and called by
nothing. Convention 22 in its purest form.

### Built this session

- `run_space_cycle`, `discover_space_markets`, `build_space_context`,
  `check_space_identity`, `space_stats` in `engine/polymarket/shadow_loop.py`.
  ONE implementation driven by a `MarketSpace` record, not three copies.
- Constructor builds the three spaces, selecting strategies by DECLARATION.
- The run() cadence block drives each space on its own 60s timer, after the
  crypto cycle so a slow Gamma sweep cannot delay a 5-minute window.
- `tests/test_space_shadow_wiring.py`, 28 tests, fully offline.

### Two defects found while wiring, both fixed

1. **`build_weather_context` never stamped `market_type`.** Every weather
   context was a weather market carrying the default `crypto_updown` label.
   It went unnoticed because `WeatherArb` does not call `assert_supports`, and
   the one strategy in that space that does (`SmartMoneyCopy`) declares every
   type and so accepted the wrong label silently. A routing declaration is only
   enforceable if the context carries the type the router selected on.
2. **The tagged search deduped AFTER the quality gates.** A market dropped for
   low volume under tag A was re-evaluated under tag B and dropped again, so
   `volume_below_floor` counted copies rather than markets and
   `duplicate_across_tags` never fired for it.

## The corridor correction, and it changes a verdict

Proposal 026 asks for a code read BEFORE any logging, and says it would rather
be made redundant by it. It was made redundant.

`CorridorPairLive.evaluate` builds `Leg(lead_side, market_slug=slug_15)` and
`Leg(opp_side, market_slug=slug)`. The 15m leader and the final-5m opposite:
two different MARKETS on two different CLOCKS settling off one close. Not two
complementary outcome tokens. Verified in the wiring, not the docstring.

The same file computes `best_case_pnl_per_pair = 2.00 - pair_cost`.

**So both legs winning is the DESIGNED payoff, not an anomaly.** The 1.21 pair
paid 1.21 and received 2.00: a profit of $0.79. The vault note's premise
("both legs exited at 1.00") is true and its conclusion ("which a genuine
complementary pair cannot do") is false, because it is not a complementary pair
and never claimed to be. Fair value is `1.00 + P(corridor)`, and the binned
table reads 0.326 to 0.464 in the 5-30bps zone, so paying 1.21 is correct
whenever P(corridor) exceeds 0.21.

**The standing correction in CLAUDE.md is withdrawn for `corridor_pair_live`.**

What survives is narrower and real: the $1.00 floor holds only if BOTH legs
fill. The legs are sequential takers, so a one-legged fill has no floor and is
a naked directional position. That is exactly the $4.20 unhedged loss on the
record, and proposal 026 phase two rules 7 and 8 are the part worth keeping.

## Proposals 024, 025, 026 are three different kinds of thing

Raven asked for all three as registry strategies. Two of them are not.

- **024: registered.** It IS a strategy - it evaluates a market and returns
  `QUOTE`. Appended at index 19, so every historical log position is unchanged.
  Registry is now **20**; the crypto-routed population is **19**. Its four skip
  reasons are now classified. `already_quoted_this_window` is SIM_LIMIT, not
  GENUINE: our own per-window cap refused, the book never got a look in.
- **025: not built, and not as a strategy.** It is a counterfactual logger that
  must hook the loop's `max_trades_this_window` branch. A registry strategy
  cannot see that branch, because the cap fires in the loop AFTER the strategy
  has already decided. Building it as a strategy file would produce something
  that cannot do the one thing it exists to do. It also writes into a `signals`
  table already producing ~78k rows/day with an OPEN retention question.
- **026: not built.** See the corridor section.

## Repairs to what D-312 left red

The baseline was **22 failures**, not the 2 that CLAUDE.md claimed. Thirteen of
them were one cause: D-312 moved `PM_weather_arb` out of the crypto cycle, so
the crypto denominator became 19 of 20, and thirteen tests still derived it
from `len(build_strategies())`. `N_STRATEGIES` is now the crypto-routed subset,
still derived and never hardcoded.

Also fixed: the two hard registry-count pins (19 -> 20), the weather capability
test which asserted pre-D-312 behaviour, and the four sports/political market
tests (one real dedup-ordering bug, one fixture that accidentally built
duplicates).

**Net: 20 inherited failures fixed, 28 new tests added, back to the documented
2-failure baseline.** Both remaining failures reproduce at pure HEAD and are
the two already known to need a ruling.

## The pre-commit hook refused, correctly

`conflict-check` REFUSED the first commit: 5 staged files had changed since the
last write that went through `engine.concurrency`. All 5 were my own edits -
Claude Code's Edit tool bypasses the ledger, which CLAUDE.md already documents.

I did NOT use `SKIP_CONFLICT_CHECK=1`. I verified each file still contained the
specific edit this session made, then re-registered all 5 through
`safe_write` under agent id `cody-market-spaces`. Second attempt: MISMATCH=0.

## NOT done, deliberately

- **No strategy was re-declared into the new spaces to make them fire.** The
  only strategy polled there is `PM_smart_money_copy`, which already declared
  every type. Widening any other strategy's `supported_market_types` is a
  TRADING decision under D-312 clause 2, and it is Aym's, not a side effect of
  wiring transport. This is the single biggest judgement call in the session.
- **The live loop was not restarted.** PID 90192 is running pre-edit source
  (Convention 13), so the spaces are on disk and NOT live. Restarting is
  already on Aym's list.
- **`research/polymarket_paper/polymarket_paper_log.csv` not staged.** The live
  loop is appending to it right now; committing a file mid-write is a torn
  snapshot. 233,469 uncommitted rows are sitting there.
- Task 2's broader ask - "run grid_hedge and dip_arb against any market with
  sufficient liquidity", "run fair_value_arb against any binary market" - is
  the same re-declaration decision. Not taken.

## For Raven and Aym

**Needs a ruling:**

1. **Ratify D-312 to D-315.** D-312 and D-313 were cited in 8 places with no
   bodies; I wrote them from the code. The design is not mine, the write-up is.
2. **D-314 withdraws a standing correction.** Worth a second pair of eyes: I am
   claiming the vault note reached a wrong conclusion from a true observation.
3. **Which strategies, if any, should be re-declared into event/sports/political?**
   The transport is live; nothing uses it but `smart_money_copy`.
4. **Restart the loop** so the spaces actually run?
5. **025** is gated behind the `signals` retention decision.
6. The two permanently-red tests, still unruled.

**Carried over, unchanged:** the maker-fill wiring gap (`SKIP_MAKER`), the dead
graveyard re-sweep, the weather sigma fit, and Aym's owed items.
