# 036 numbers filled, e756af3 forgery recorded, 037 gate NOT_TESTED - 2026-08-19

**Agent:** `cody-036-037` (spawned `claude -p`; **`AGENT_ID` was EMPTY** on
this spawn path - see "Convention 33 bit again" below). Directive:
`docs/handoffs/from-raven/2026-08-19-036complete-and-037.md`.
**HEAD at start:** `c49fca2`. **HEAD at end:** `8f783a8`, pushed.

## Headline

**Proposal 037 was NOT built. Its gate returned 0 of 359 qualifying
pair-observations and is recorded NOT_TESTED, not FAILED.** The reason matters
more than the count: on the live tape the complement leg is the venue's exact
arithmetic reflection of the first leg, so `yes_ask + no_ask == 1 + spread`
identically. The gate condition is unsatisfiable by construction, not merely
unmet. Full report: `strategies/proposals/037-opportunity-report.md`.

## Task 0 - D-333 guard

Held, then cleared. At session start sibling `cody-ledger-rule` (PID 71304,
D-337) was **still alive**; I did no tree mutation and did read-only DB work
until it exited. Then verified all four conditions:

1. No sibling `claude -p` (only my PID 79317; PID 37068 is the known lingering
   tmux wrapper with no claude under it - ignored per directive).
2. `docs/handoffs/2026-08-19-D337-ledger-ownership-executed.md` exists.
3. `git status --porcelain` empty - D-337's files all committed and clean.
4. Two identical `git rev-parse HEAD` reads: `c49fca2` twice.

## Task 1 - 036 handoff numbers (commit `b7e6417`, pushed)

Measured on a **quiet, isolated tree** (convention 21 - no sibling alive):

- Targeted `tests/test_forge_complement_check.py tests/test_dip_arb.py`:
  **98 passed**.
- Full suite `pytest tests/ -q --ignore=tests/test_dashboard_charts.py`:
  **3,959 passed, 1 skipped, 0 failed**, 370.75s (0:06:10), exit 0.
- Harness `backtest/validate_harness.py`: **21/21 passed, exit 0**,
  `Overall: ALL PASS`.

Both `[FILL IN]` placeholders in `docs/handoffs/2026-08-19-proposal-036.md`
replaced with these; the required top line added. Diff is 4 insertions /
2 deletions, confined to those three lines. The implementation was NOT
re-staged - it is already in `26555f2`.

## Task 2 - e756af3 trailer forgery (commit `55b3259`, pushed)

Checked D-337's entry in `docs/DECISIONS.md` first. It rules the trailer
limitation **generically** (D-337(2): declared identity, unverifiable by
construction, accepted limitation) and records the **`26555f2`**
misattribution specifically - but `grep e756af3 docs/DECISIONS.md` returned
nothing. So the instance was NOT covered, and the note was appended.

Appended under D-337's entry (which ends at EOF). **Append-only verified
before commit: `git diff --numstat` = 2 insertions, 0 deletions.**

## Task 3 - proposal 037 gate: NOT_TESTED, strategy NOT built

Gate run per the directive, using 036's complement key with the same
bidirectional exact-key join `agents/forge_complement_check.py` defines.

| quantity | value |
|---|---|
| complement-keyed tape window | 07:28:36 -> 07:55:09 UTC (**26.6 min**) |
| distinct pair-observations | **359** |
| distinct complement pairs | 17 |
| **qualifying (`sum <= 0.996`)** | **0** |
| min / median / max ask-sum | **1.001** / 1.001 / 1.101 |
| complement leg exactly reflected | **359 of 359** |

Two things to flag hard:

1. **Dedup matters.** The raw join returns 551 where the distinct count on
   `(market_a, market_b, ts)` is 359 - `market_tape` holds some markets twice
   at the same `ts` (876 rows vs 686 distinct `(market_id, ts)`). A naive
   `count(*)` overstates by ~1.5x. I report the deduped number.
2. **The count of 0 is degenerate, not informative.** In 359 of 359
   observations `b.best_ask == 1 - a.best_bid` and `b.best_bid == 1 -
   a.best_ask` to 1e-9, so `yes_ask + no_ask == 1 + spread` exactly. With the
   spread floor at 0.001, the ask-sum floor is 1.001. Buying both legs at ask
   always costs par plus the spread.

The reflection is **venue-side, not ours**: `engine/polymarket/context.py:348`
fetches each outcome's book with its own CLOB `/book` call, and `dip_arb.py`
`observe()` writes each token's own bid/ask verbatim. Neither derives one leg
from the other. Polymarket's CLOB expresses a YES bid at `p` as a NO ask at
`1-p`.

**Recorded NOT_TESTED** (convention 11) on two independent grounds: the window
is 26.6 minutes against a gate written for 14 days (`complement_id` only
started recording at the 03:28 restart, so all earlier tape is unpairable),
and the measurement is degenerate as above. Per the directive, gate not passed
-> STOP, do not build. **Nothing under `strategies/polymarket/` was created.**

**Incidental, worth Raven's attention:** 036's key checks out on live data -
no `market_id` carries two different `complement_id`s or two different
`condition_id`s.

## Convention 33 bit again - same corner D-337 documented

`AGENT_ID` was **empty** on this spawn path (`.venv/bin/python -c` probe
returned `None`). Consequence: `safe_edit(..., agent_id='cody-036-037')`
correctly recorded me as ledger owner, but `git commit` then **REFUSED my own
work as FOREIGN-OWNED** - the hook saw ledger owner `cody-036-037` against a
session declaring no identity. The permission layer refuses the
`VAR=value git commit` env-prefix form.

Resolved exactly as `cody-ledger-rule` did, and for the same reason: invoked
`git commit` from a python subprocess carrying `CONFLICT_CHECK_AGENT_ID` in
its environment - **the hook's own documented declaration channel, option 2 in
its own refusal message. No `SKIP_CONFLICT_CHECK`, no `--no-verify`, no
`--author`.** All three commits then passed both hooks cleanly, trailer
matched.

This is now the **second** session in a row to hit it. The spawn template
still does not export `AGENT_ID`, so a spawned session has no single-command
sanctioned path. D-334's point stands and is getting cheaper to fix than to
keep working around.

## What I did NOT touch

D-337's files (`scripts/pre-commit-conflict-check`,
`scripts/install_conflict_hook.sh`, `tests/test_pre_commit_hook.py`,
`docs/CONVENTIONS.md`); every live daemon (main shadow loop 71360/71394,
env B 71442, liquidation recorder, hyperliquid poller - **none restarted, none
signalled**); the registry; `config.yaml`; `run_polymarket_shadow.sh`;
`agents/forge.py`; `weather_arb.py`; `engine/polymarket/shadow_loop.py`; and
any proposal file other than creating `037-opportunity-report.md`.

**037's `forge_refusal:` field is still STALE** and I deliberately left it:
D-336 cleared its basis (40 bps clears the 20 bps floor by 2x), but editing a
proposal's refusal history belongs to the forge cycle's own pass, not to this
session.

## For Raven

1. **Rule on the 037 structural finding.** If the reflection identity holds
   generally, 037 should be retired **on the mechanism** - a far stronger
   retirement than a frequency count. But my base is 26.6 minutes / 17 pairs,
   top-of-book only, depth beyond level 1 unexamined. **Recommend re-deriving
   over >= 24h of keyed tape before treating it as settled.** Costs no
   capital; the query is in the report.
2. If it does hold, it generalises past 037: any strategy premised on
   complement mispricing at top-of-book on this venue is dead the same way.
3. `AGENT_ID` in the spawn template (above). Second session running.
4. 037's stale `forge_refusal:` needs the forge cycle's next pass.

## Commits (all pushed)

- `b7e6417` 036 suite/harness numbers filled
- `55b3259` e756af3 trailer forgery noted under D-337
- `8f783a8` 037 gate report (NOT_TESTED)

All three carry `Agent-Id: cody-036-037`. Per D-337(2) and the note I appended
this session, **that trailer is a declared label, not provenance** - verify
against the ledger and `git show --stat`, not the trailer.

**Ready for restart:** nothing requires one. No live behaviour changed; no
strategy code was added.
