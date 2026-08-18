# Handoff: SKIP_CLASSIFICATION gap closed, with one ruling needed

**From:** Cody, 2026-08-18 06:15
**Task:** `docs/handoffs/from-raven/2026-08-18-fix-skip-classification.md`
**Scope:** `agents/forge_shadow_eval.py` + `tests/test_forge_shadow_eval.py` only.
**Nothing committed. Nothing restarted. No process killed.**

---

## Headline

UNKNOWN skips are **0.00% of 40,554**, down from the 18.1% Subagent C found.

| class | count | share |
|---|---|---|
| GENUINE | 29,513 | 72.77% |
| DATA_BLOCKER | 10,713 | 26.42% |
| SIM_LIMIT | 328 | 0.81% |
| **UNKNOWN** | **0** | **0.00%** |

But **I did not write most of that fix, and I disagree with one line of the
instruction.** Read both sections below before accepting this.

## What I found before changing anything

Most of the repair was **already in the working tree, uncommitted**, applied by
a concurrent session (`agents/forge_shadow_eval.py`, +198 lines, a block marked
`=== added 2026-08-18 ===`). All 9 reasons Raven listed were already classified,
and the `risk_gate:` prefix rule Raven asked for was already implemented as
`SKIP_PREFIX_CLASSIFICATION` -> SIM_LIMIT, which is the classification Raven's
own note said to pick if it fit better. It does: the strategy had already
decided to enter and our side refused.

`tests/test_forge_shadow_eval.py` was **untouched**, so none of it was pinned.

So steps 1-2 of the instruction were done by someone else; the real remaining
work was step 3 (tests), plus one defect and one disagreement.

## The ruling I need: `strike_inside_proxy_noise_floor`

**Raven's instruction says GENUINE. The code says DATA_BLOCKER. I kept
DATA_BLOCKER.** This is 7,450 skips, 18.77% of all skips - the single largest
reason in the data, and the one that made up nearly the whole original gap.

The strike is a **measured proxy**: `engine/polymarket/strike.py` rebuilds the
Chainlink 60s TWAP from Binance.US klines, and `measure_strike_proxy.py` put a
number on its error. `STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0` is the floor below
which the signal is inside **our own instrument error**. The strategy did not
compute an edge and decline. It was refused an input it could trust.

The module's own tie-break rule agrees: "When a reason could be read either
way, it goes to DATA_BLOCKER, because over-reporting NOT_TESTED costs a re-test
and under-reporting it puts a fabricated verdict in the record." CLAUDE.md also
says this reason is "never pooled with a market condition."

I measured the consequence rather than argue it:

```
DATA_BLOCKER (as coded):   NOT_TESTED=2  RAN_NO_ENTRY=3  FIRED=3
GENUINE (per instruction): NOT_TESTED=0  RAN_NO_ENTRY=5  FIRED=3
```

Under Raven's reading, **`PM_corridor_collector` and `PM_mid_price_continuation`
flip from NOT_TESTED to RAN_NO_ENTRY** - "could not run" becomes "ran and found
nothing" for ~19% of the evidence. That is the exact inversion convention 11
exists to prevent, so I did not apply it.

The other 8 reasons Raven listed ARE genuine and are classified GENUINE. Only
this one is in dispute. **It needs a D-number either way.**

## The defect I did fix

`'no_market'` was **defined twice** in `SKIP_CLASSIFICATION` (149 literal keys,
148 unique). A duplicate dict key is not a Python error - the later one silently
wins and the earlier `missing_input` string is lost with no warning. Both
happened to be DATA_BLOCKER so no class was wrong, but the two definitions
described different missing inputs ("resolved market" vs "a Gamma market for
this window") and one was being silently discarded.

It has two real emitters: ~16 strategy modules raise it when the context has no
market, and `shadow_loop.py` raises it when Gamma serves none. Merged into one
entry naming both. Pinned by `test_no_skip_reason_is_defined_twice`, which walks
the AST because the parsed dict cannot show the duplicate.

## Tests added (44 pass, up from 32)

The one that matters is **`test_every_skip_reason_the_strategies_emit_is_classified`**.
Grepping for `decide('SKIP', ...)` is what went stale and caused this gap in the
first place, so it walks the AST of all 22 strategy modules instead, extracts
every SKIP literal (**127 distinct**), and asserts the table covers all of them.
A new strategy that adds an unclassified reason now fails at 0 skips instead of
silently at several thousand. Currently 0 unclassified.

Also added: the 8 genuine reasons parametrized; the noise-floor blocker and its
NOT_TESTED consequence; `risk_gate:` / `adapter:` prefix matching with variable
numeric tails; exact-entry-beats-prefix ordering; risk-gate-blocked strategy is
NOT_TESTED; and a guard that a novel reason is **still UNKNOWN** (the repair must
not have turned the table into a catch-all).

## Test runs

- `tests/test_forge_shadow_eval.py` -> **44 passed**
- Full suite (ex-dashboard) -> **1,959 passed, 1 skipped, 8 failed**

**The 8 failures are not mine and are not real.** All were in
`test_weather_arb.py`, `test_grid_hedge.py`, `test_smart_money_copy.py`,
`test_fair_value_arb_variants.py` - files a concurrent session (PID 38911, still
alive) was writing *during* the 7:44 run. `tests/test_grid_hedge.py` was modified
at 06:10:59, mid-run. Re-running those 4 files immediately after: **317 passed,
0 failed.** Convention 21, exactly as CLAUDE.md warns.

Nothing outside `agents/forge_shadow_eval.py` and `tests/test_forge_shadow_eval.py`
was touched by me.

## What I did not do

- Did not commit. Tree left for review, per instruction.
- Did not restart the shadow loop (PID 27030 confirmed alive) or touch the
  sweep (18543), the hyperliquid feed, or the liquidation recorder.
- Did not run `validate_harness.py`. This change produces no backtest result,
  so convention 1 has nothing to certify here. Say if you want it anyway.
- Did not reclassify anything the concurrent session had already decided,
  beyond the duplicate-key merge.

## Next

1. **Rule on `strike_inside_proxy_noise_floor`.** DATA_BLOCKER (kept) vs
   GENUINE (instructed). It moves 2 strategies between NOT_TESTED and
   RAN_NO_ENTRY and needs a D-number.
2. The concurrent session's +198-line block is still uncommitted and now has
   test coverage. Worth deciding whether it lands as one commit or separately
   from my two changes.
