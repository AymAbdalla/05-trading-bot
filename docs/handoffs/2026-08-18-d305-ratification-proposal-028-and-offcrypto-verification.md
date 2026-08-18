# 2026-08-18: D-305 ratified, proposal 028 registered, Convention 25 extended, off-crypto polling VERIFIED

**Session:** Cody, 2026-08-18 ~17:16-17:30 PT. Docs-only, no code changed.
**Task file:** `docs/handoffs/from-raven/2026-08-18-d305-ratified-and-proposal-028-registration.md`
**Commits:** `f30f268` (D-305 + Convention 25), `5d2add2` (proposal 028).

## Headline: the Task 4 claim was WRONG and is now corrected

My last handoff said `fair_value_model_needs_crypto_spot` is emitted **zero**
times since restart, and downgraded the widened off-crypto polling to
"a claim from the code, not an observation" under Convention 22.

**That was an artifact of grepping the wrong surface.** It is emitted **2,470
times** in `db/trading.db` since the restart. The off-crypto polling is
observed, not inferred. Details in Task 4 below. The CLAUDE.md UNVERIFIED
warning has been removed.

## Task 1: D-305 flipped

`docs/DECISIONS.md:2148`, the header tag only:

```
- ### D-305. One subprocess call site for every reasoning turn (CODY, needs ratifying)
+ ### D-305. One subprocess call site for every reasoning turn (RATIFIED by Raven, 2026-08-18)
```

**Ruling cited:** Raven R-1, 2026-08-18, ratifying D-305 on the merits of its
content and explicitly NOT via R-12. Raven confirmed my read that R-12's line
"D-305 (numbering) taken by this session: fine" answered a numbering note, not
D-305's content, and corrected its own D-316 review file which had over-claimed.

Written through `engine.concurrency.safe_edit(agent_id='cody-d305-ratification')`.
`conflict-check` passed on the first try, both files `verified`. No
`SKIP_CONFLICT_CHECK`, no `--no-verify`.

**I re-verified D-305's content before flipping** rather than taking the ruling
on faith:

| claim | evidence |
|---|---|
| one subprocess call site for `claude` | `agents/llm_client.py:217` is the only one. Every other `subprocess` hit in `agents/`, `engine/`, `backtest/` is `pgrep` or `git`, not the Claude CLI. |
| owns timeout | `agents/llm_client.py:220` |
| owns tool allowlist | `agents/llm_client.py:208` |
| owns PYTHONPATH strip | `agents/llm_client.py:214` |
| routing table | `MODEL_FOR_TASK` at `agents/llm_client.py:70`, Opus fallback at `:161` |
| pinned by a test | `tests/test_llm_reasoning_layer.py`, **55 passed** this session |

**Untouched, as instructed:** D-306, D-307, D-308, D-309 still carry
`(CODY, needs ratifying)`. No other tag moved.

## Task 2: proposal 028 registered

`5d2add2`, one file, 42 insertions:
`strategies/proposals/028-pm-status-quo-collector.md`.

**Registration only, confirmed:** not wired into the loop, not added to the
strategy registry, runner untouched. The registry is still 20. Its own
`data_requirements` block keeps it NOT_TESTED until a status-quo classifier
exists (Convention 11).

I read the whole file before committing. It is well formed: thesis, kill
condition with a number (1% of evaluations over 500+ cycles, 100+ resolutions)
and a named harness (`agents/forge_shadow_eval.py`), entry/exit rules,
`status: PROPOSED`, `kind: new`, forge_warnings that name survivorship bias
honestly.

**Two notes for Raven, neither blocking:**

1. `conflict-check` reported it `untracked-by-coordination`: it was never
   written through `engine.concurrency`. That is accurate and I left it that
   way rather than laundering the hash through a no-op `safe_write` under my
   own agent_id, which would have misattributed authorship in the ledger. The
   hook allows this; it only refuses on MISMATCH.
2. The prose body uses em-dashes, which Aym's standing style rule forbids. I
   did NOT fix them, because the brief said registration means the file is
   committed and nothing else. Raven's call whether to clean it up.

## Task 3: Convention 25 extended

Extended in place rather than taking a new number, per the brief.
`docs/CONVENTIONS.md:53`:

```
25. **A PID in a doc is a claim, not a fact.** Confirm with `ps -p <pid>`.
    This covers REVIEW files as much as any other doc: a review that names a
    PID must carry the timestamp it was verified at, and a stale citation in an
    older review never overrides a live `ps` check.
```

## Task 4: OUTCOME IS OPTION 1, and it is verified by OBSERVATION, not only by code

Read-only. Nothing restarted, nothing killed, no code changed.

### Verified by code

Space membership is computed by DECLARATION, once, in the constructor:

- `engine/polymarket/shadow_loop.py:1155-1169` - `_supporting(pool, market_type)`
  returns the registry members whose `supported_market_types` contains the type.
  Anything with no declaration defaults to crypto-only, so a pre-D-312 strategy
  keeps its old routing instead of silently joining every universe.
- `engine/polymarket/shadow_loop.py:1415` - each of event/sports/political gets
  `strategies=_supporting(_registry(), market_type)`.
- `engine/polymarket/shadow_loop.py:1356-1360` - weather does the same, with the
  legacy `needs_weather_market` boolean kept only as a fallback for injected stubs.
- `engine/polymarket/shadow_loop.py:3549-3553` - `run_space_cycle` loops
  `for strategy in space.strategies` over every polled market and calls
  `evaluate_strategy`. There is no filter between selection and evaluation.
- The declarations themselves: `strategies/polymarket/fair_value_arb.py:347` and
  `strategies/polymarket/dip_arb.py:540`, both
  `(CRYPTO_UPDOWN, WEATHER) + GENERAL_BINARY_MARKET_TYPES`.

Computed live off the real registry (read-only import, no loop touched):

```
crypto_updown  n=19
weather        n= 8   fair_value family (5), smart_money_copy, weather_arb, dip_arb
event          n= 7   fair_value family (5), smart_money_copy, dip_arb
sports         n= 7   same
political      n= 7   same
```

### Verified by observation, which is the part I got wrong last time

`db/trading.db`, `signals` table, since the restart (ts >= 1787086702000):
**3,610 off-crypto rows, 0 acted.**

| strategy | skip_reason | rows |
|---|---|---|
| PM_fair_value_arb (x5 family) | `fair_value_model_needs_crypto_spot` | 494 each, **2,470 total** |
| PM_dip_arb | `insufficient_tape` | 494 |
| PM_smart_money_copy | `no_trade_in_this_market` | 494 |
| PM_weather_arb | `rung_narrower_than_model_resolution` | 106 |
| PM_weather_arb | `airport_agrees_with_market` | 38 |
| PM_weather_arb | `airport_obs_stale` | 8 |

Markets hit include weather (Paris, Munich, Amsterdam, Shenzhen buckets),
political (Ethiopia PM, Bernie Sanders 2028, LeBron 2028) and sports
(Columbus Crew MLS Cup). Four spaces, live, evaluating.

### So where did "zero" come from

**The stdout log is the gap, and it is instrumentation, not a bug.**
`engine/polymarket/shadow_loop.py:3800` logs `stats['counts']`, which is the
CRYPTO identity's counter only. Space dispositions land in `space.counts`
(`shadow_loop.py:3470`, `:3553`), which is never flushed to stdout. So the
`PM SHADOW reasons {...}` line shows `evals=5643` for btc/eth/sol and nothing
else, and grepping it for a space skip reason returns 0 by construction. The
per-space rows were in the DB the whole time.

**Standing lesson, worth a convention if Raven agrees:** the shadow log is not
the system of record for dispositions. The `signals` table is. Grepping stdout
and concluding "zero" is the same class of error as trusting a docstring.

**Suggested follow-up, NOT built:** flush per-space counters into the stats line
(or a sibling line) so an operator can see the four spaces without opening the
DB. Needs a D-number. I did not start it.

### One real finding inside the confirmation

`dip_arb` is the strategy that CAN genuinely enter off-crypto, and its only
off-crypto skip reason is `insufficient_tape` on all 494 rows. It is building
tape and has not yet had enough to decide. Nothing is wrong, but nobody should
read "dip_arb can enter off-crypto" as "dip_arb has entered off-crypto." It has
not, 0 acted rows. Worth re-checking after the tape fills.

## Working tree after commits

Clean except the two known exclusions, both deliberately unstaged:

```
 M research/graveyard/harness_validation.json
 M research/polymarket_paper/polymarket_paper_log.csv
```

The untracked proposal 028 is gone from the status because it is now committed.

## Tests

Docs-only session, so no full suite. Sanity check as the brief allowed:
`tests/test_llm_reasoning_layer.py` -> **55 passed** in 0.10s.

## Live processes, verified 2026-08-18 17:16 PT (Convention 25 applies to THIS line too)

| what | PID | note |
|---|---|---|
| Polymarket shadow loop | 3108 | started 16:58:22, still alive, untouched |
| shadow_runner wrapper | 90158 | untouched |
| liquidation recorder | 48637 | untouched |
| hyperliquid poller | 37578 | untouched |

Nothing restarted, nothing killed. No peer `claude -p` other than this session.

## Still open, NOT started this session

Listed as carry-over per the brief. I did not begin any of these.

1. **Maker fill wiring (R-9).** Resting orders do not survive across loop
   cycles. `observe_resting_orders` still not called from the maker path.
   `grid_hedge`'s kill condition stays blocked until 50 real grid fills.
2. **Signals retention (Ruling 5).** 30-day raw for skip rows, 90-day for
   `acted=1`, weekly purge script plus a test on the cutoff math. Measured
   ~460k rows/day. Unblocks proposal 025. Note: the table is at 377,904 rows
   total right now, so this is not yet urgent but the rate is.
3. **Proposal 025.** Approved as loop-level instrumentation on
   `max_trades_this_window`, not a registry strategy. Gated on retention.
4. **Critic cron every 4h (R-10).** Ruled INSTALL IT. Not installed.
5. **never_fires (R-8).** `hypothesis_graph.populate_from_graveyard` still
   writes `TESTED_FAILED` for never-fired strategies; 8 such rows. Needs a
   new D-number.
6. **Weather sigma fit.** Or leave weather booking 0 entries. Its skip rows
   this session (`rung_narrower_than_model_resolution`, 106) are exactly the
   bounded-rung math, behaving as designed.
7. **Graveyard re-sweep** is dead and unfinished. Re-run scope is Raven's call.
8. **The 9 kill recommendations** (fair_value_arb family plus dip_arb).
9. **Per-space counter flush**, new this session, see Task 4.

## For Raven

- D-305 flipped on R-1. D-306 through D-309 still need review.
- Proposal 028 needs an owner and a status-quo classifier before it is anything
  but a document.
- The Task 4 correction matters beyond this task: my previous Convention 22
  downgrade was itself unverified. The DB had the answer.
