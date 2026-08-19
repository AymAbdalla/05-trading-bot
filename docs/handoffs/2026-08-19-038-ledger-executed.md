# 038 settlement resolution ledger - EXECUTED

**Session:** `cody-038-ledger`, 2026-08-19 ~07:26-08:05 EDT.
**Brief:** `docs/handoffs/from-raven/2026-08-19-038-ledger-implement.md`.
**Commit:** `1c5a761`, pushed. **HEAD before:** `ccc7991`, then `b73610a` (a
SIBLING's docs-only commit landed mid-session), then mine on top.
**AGENT_ID:** read **`cody-038-ledger`** with python at session start - **SET**,
on a tmux spawn, consistent with D-341 R4.

## Verdict

**R1 is DONE, not partial.** 038's `entry_exit_rules` 1-7 are implemented,
tested and green, and the ledger is in HEAD ready for the ~03:45 EDT 2026-08-20
restart to activate. Nothing was shipped half-done, so the brief's "leave the
tree clean and report what remains" escape hatch was not used.

**Suite 4,025 passed / 1 skipped / 0 failed** (was 3,962; this session added
63 tests). **`backtest/validate_harness.py` 21/21, returncode 0.** Both
re-derived at 07:44-07:51, neither quoted.

## The venue question - ANSWERED, and it changed the design

038's one MISSING data requirement was whether the venue exposes a resolution
field or whether resolution must come from a terminal book price. **It exposes
one, and this repo already had a verified reader for it that nothing was
using.** `engine/polymarket/market_resolution.py` (440 lines, written for the
blocked `smart_money_copy`) wraps
`GET clob.polymarket.com/markets/<conditionId>`, whose body carries `closed`
plus a per-token `winner`, verified against live responses on 2026-08-18 at
**8 of 8** condition ids where gamma answered **1 of 8**.

So **`source` is `venue` on every live row**, and **no terminal-price reader was
written**. `inferred_terminal_price` is defined and accepted by the writer, but
nothing produces it. The GAMMA TRAP is therefore not on any live path.

**This is the design decision worth Raven's attention.** The paper adapter
settles positions via `prices.resolution_price`, which reads gamma
`outcomePrices` **by slug** - a different endpoint and a different field. So the
ledger and the sibling inference it is checked against are **independent
reads**, and the kill condition's disagreement clause actually tests something.
Had the ledger reused the adapter's endpoint it would have agreed by
construction and measured nothing. Please do not let a later session
"simplify" it onto `resolution_price`.

## What was built

| file | what |
|---|---|
| `engine/polymarket/resolution_ledger.py` | NEW. Table DDL, source vocabulary, `ResolutionLedger` (observe/sweep), `write_resolutions`, `resolution_for`, `resolution_row_for`, `table_exists`. |
| `db/schema.sql` | `market_resolutions` appended, with the reasoning block. |
| `engine/polymarket/shadow_loop.py` | `observe()` on the FETCH path (5m + 15m), `sweep_resolutions()` on a 60s timer after every trading phase and in `shutdown()`, `ledger_stats()` in `stats()`, `enable_resolution_ledger` flag. |
| `backtest/settlement_coverage.py` | NEW. Coverage, agreement, sibling-inference map, marked backfill, CLI. |
| `tests/test_resolution_ledger.py` | NEW, 54 tests. |
| `tests/test_polymarket_shadow_loop.py` | +6 wiring tests. |
| `tests/test_schema_matches_feed_modules.py` | `market_resolutions` registered in the two-copy drift guard. |

Rule-by-rule: **(1)** new table, additive, `market_tape` untouched, UNIQUE on
`(market_slug, outcome_side)`, **no DEFAULT on any column and no NOT NULL on
`resolved_px`** - pinned by a test that reads `PRAGMA table_info` column by
column. **(2)** `observe()` is called from `build_context`, immediately after
the market is fetched and **before** the `not books` early return, so a market
whose books were unreadable is still recorded - a test pins exactly that.
**(3)** `source` is a closed vocabulary; an unknown value RAISES.
**(4)** backfill implemented and marked, excluded from coverage by
construction. **(5)** `resolution_for` returns None for absent market, absent
side, and NULL `resolved_px`, and never 0.00. **(6)** no strategy is wired to
the table. **(7)** unresolved markets are counted per window with a reason and
surfaced in `stats()['resolution_ledger']['unresolved_by_window']`.

## A real bug the tests caught

The first draft read **`Outcome.outcome`**. The field is **`Outcome.name`** -
`outcome()` is the accessor on `Market`. Reading the wrong one **does not
raise**: it yields an empty outcomes tuple, so every market is refused as
`no_outcomes` and the recorder reports itself healthy while writing nothing.
It was caught only because the test factory builds the real `Market` dataclass
instead of a stub. Worth remembering as an argument against stub-shaped tests
for anything that touches a real type.

## Kill-condition status: NOT_TESTED, correctly

`backtest/settlement_coverage.py --db db/trading.db` run read-only reports
**NOT_TESTED** with the reason "market_resolutions table absent from this
database; the ledger activates at the next loop restart". That is the honest
answer and not a failure - convention 11. Reporting it as 0/889 would read like
a broken recorder rather than an absent one.

**Baseline re-derived 07:35 EDT (drifted from the brief - the loop kept
trading):** 2,268 closed positions (was 2,216) touching **889** distinct
market-sides (was 864), recoverable for **345** = **38.8%** (was 325 / 37.6%),
195 with no recoverable `outcome_side` (was 193), **29.9%** of singly-recovered
sides settled 1.00 (was 28.5%) against a ~50% unbiased benchmark. The method
reproduces exactly; only the tape grew.

**The 2 contradictory market-sides are NAMED**, as the kill condition asked:
**`sol-updown-5m-1787056800` / Up** and **`btc-updown-5m-1787134200` / Down**.
Both carry 0.00 AND 1.00, impossible for one side of one binary. They are
reported as contradictory, never resolved to a value, never backfilled, and a
test pins that a contradictory side cannot manufacture a disagreement failure.

## What was NOT done, deliberately

1. **No loop restarted, signalled or touched.** PIDs 71393, 71444, 48637, 37578
   alive at start and end with original start times.
2. **Nothing written to the live database.** `market_resolutions` is absent from
   `db/trading.db` by design; `ensure_schema` creates it at restart.
3. **The backfill was NOT executed.** It is built and unit-tested but writing it
   means writing into a file the loop holds open in WAL while 026 and 037 are
   mid-measurement. `--backfill` is the flag. **Raven's call when to run it;
   my read is after the restart, alongside the first coverage measurement.**
4. **The 039 fork arm was not built** (R2 says so explicitly).
5. **The restart brief, `docs/keying-prep/`, the cron, `CONVENTIONS.md`,
   `config.yaml`, the strategy registry, `market_tape` and the `signals` schema
   were not touched.**
6. **Space and weather markets are not observed by the ledger.** 038's own
   `markets:` field scopes it to crypto Up/Down, and those are 1 of 730 distinct
   traded pairs. Flagging it rather than leaving it silent - if Raven wants the
   coverage denominator to include them, that is a follow-up.

## Convention 21: a live sibling, and it is still mid-flight

At 07:40 EDT **PID 6865** appeared - `claude -p read
docs/handoffs/from-raven/2026-08-19-kalman-rulings-risk-module.md` - and
committed `b73610a` (docs only, 520 lines) mid-session. At my session end it had
left **`engine/risk/` uncommitted**: a STAGED rename
`engine/risk.py -> engine/risk/__init__.py` plus untracked `constraints.py`,
`events.py` and `tests/test_risk_constraints.py`.

I committed by **PATHSPEC** (convention 34) and touched nothing under
`engine/risk/`. The hook confirmed **8 files, 8 own-work, 0 FOREIGN-OWNED**, and
its staged rename survived intact. **Raven should check whether that risk work
ever landed** - if those files are still uncommitted, that session died
mid-write.

## D-341 recorded

Appended to `docs/DECISIONS.md` as **D-341** with R1-R4 transcribed and the
recording note OUTSIDE the ruling text. Hash-guard **H0
`afc0aafa...b9d11d58`** taken at read and re-checked immediately before the
write via `engine.concurrency.safe_edit`. **`git diff --numstat` = 24
insertions, 0 deletions** - append-only verified, no prior entry modified.

The D-number was computed **dynamically** from the file rather than hard-coded,
because the live sibling was also processing a rulings brief and could have
claimed 341 first. It did not; the entry is D-341 as the brief specified.

## For Raven

- Grade nothing about 038's coverage yet - it is NOT_TESTED until the restart.
- Decide **when the backfill runs** (open item 2 in CLAUDE.md).
- The restart session must **record `T` precisely**: it is the ledger go-live
  time and the start of 039's 14-day clock.
- Check the sibling's `engine/risk/` work.
