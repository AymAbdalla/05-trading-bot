# D-382 executed: confidence-based position sizing, three realms restarted

**Session:** `cody-D382-sizing`, 2026-08-20, ~15:10-15:35 EDT (19:10-19:35 UTC),
measured with `date`. Brief:
`docs/handoffs/from-raven/2026-08-20-D382-sizing-implementation.md`.

## Gate evidence

- **41541 DEAD.** `ps -p 41541` returned nothing at 15:09:53 EDT. Its handoff
  `docs/handoffs/2026-08-20-tick7-completion-executed.md` exists.
- **Lock was FREE** - line 1 empty, body read "cody-tick7-completion RELEASED
  2026-08-20 15:02 EDT". Taken by me: pid `45099`, `cody-D382-sizing`.
- **`ps` clean.** Only my own `claude -p` (45099). The known traps were present
  and correctly ignored: tmux server **37068** carrying the stale Aug-19
  `claude -p` argv, four `claude mcp serve` daemons, and the Claude desktop
  helpers. No sibling session.
- **Tree** carried only the expected dirt (`db/snapshots/`, five `scripts/*.py`,
  the cycle8 proposal). **HEAD at gate: `77379e1`.**
- **Three loops LIVE** at gate: 40841 main (14:30:42), 40884 env B (14:31:09),
  40927 realm C (14:31:36).

**AGENT_ID was SET and CORRECT** (`cody-D382-sizing`), probed with python.
`CONFLICT_CHECK_AGENT_ID` was None and was not needed.
**Running tally: 14 SET / 13 EMPTY.** Keep probing; it is still spawn-dependent.

## HEAD MOVED UNDER ME MID-SESSION. AGAIN.

`git rev-parse HEAD` immediately before committing read **`3b495b4`**, not the
`77379e1` I cleared the gate on. Raven committed **D-383** (Aym ruling, shadow
drawdown measurement limit 1.0 -> 0.25) into this tree at **15:31:12 EDT**,
while I was live and holding the lock.

**This one was harmless** - docs-only, `docs/DECISIONS.md` alone, no overlap
with any file I touched, no provenance theft. But it is the second consecutive
session in which the dispatcher committed into a live tree, and it is exactly
the collision convention 36 (below) was written for, landing while I was writing
the convention. Re-deriving HEAD at commit time caught it; a HEAD read once at
the gate would not have.

**The three live loops do NOT carry D-383.** They were restarted at 15:28, three
minutes BEFORE that commit existed. D-383 is recorded and NOT implemented -
`SHADOW_RISK_LIMITS.max_drawdown_frac` is still `1.0` in source. My brief
explicitly forbade touching it, so I did not. **It is the next brief, not a
leftover.** 049's enrichment stays dormant until someone implements D-383 and
restarts.

## What D-382 is, as built

`engine/polymarket/paper_adapter.py`. The $10 flat order size is **replaced**:
every entry is sized to a percentage of available capital chosen from the
signal's own confidence, scaled by the strategy's win rate, clamped into
`[1%, max_position_pct]`.

- `CONFIDENCE_SIZE_CURVE` - the knots, interpolated linearly, flat outside:
  **0.50 -> 1%, 0.60 -> 2%, 0.70 -> 5%, 0.80 -> 10%, 0.90 -> 20%, 0.95+ -> 30%.**
- `win_rate_factor(win_rate)` -> `[0.5, 1.0]`, multiplies the curve. It can only
  ever SHRINK. **Unknown win rate = 1.0**, i.e. the curve, not a penalty: the
  curve is already the conservative default and docking it again for a missing
  number would do the curve's job twice.
- `DEFAULT_STRATEGY_WIN_RATES` is **EMPTY, on purpose.** No strategy here has a
  ratified measured win rate. `breakeven_win_rate` is everywhere in this repo
  and is **not one** - it is the threshold a strategy would have to clear.
  `summary()['win_rate']` is a live moving session statistic and reading it
  per-order would put a DB-shaped read inside a deliberately pure adapter.
  So **every live strategy is "unknown" today and sizes at the curve.** Filling
  this table is a decision (whose number, over what window, clustered how per
  046), not a default to quietly populate.
- `DEFAULT_POSITION_SIZING_MODE = 'confidence'`. A `'flat'` mode is kept so the
  ~2,300 trades already measured at $10 stay reproducible without editing
  source. An unknown mode **raises at construction** rather than degrading.
- **`config.yaml` untouched.** Both new keys are module constants with defaults.

**Why the curve stops at 30% and not 90%.** `confidence` is a MODEL OUTPUT, not
a measured win rate - `weather_arb.py:4138` stamps
`confidence_is_model_output_not_measured_win_rate=True` on every row it emits.
Treating an uncalibrated 0.95 as a 95% chance and sizing at the 90% the ruling
permits is the most expensive possible way to discover a model is miscalibrated.
R2/R4 asked for a ceiling; a ceiling is not a target. **Raise the knots when a
strategy demonstrates calibration, not before.** The 90% clamp is applied LAST
and is tested against a deliberately over-raised curve.

## Two judgement calls Raven should rule on

**1. It is a size-TO, not a size-up.** The risk gate hands the adapter a share
count already cut to `notional_cap_usdc`, so an adapter that only ever shrinks
could not implement R1 at all. Under `confidence` mode the confidence budget
REPLACES `notional_cap_usdc` as the order size. **This overrides a strategy that
deliberately requested FEWER shares than the budget** - the paired-leg case is
the one to think about. I judged this the literal reading of R1/R3, and in
shadow it bypasses no live risk cap: D-363 R3 already put every other budget in
the gate's `min()` at the 100,000 sentinel, so `notional_cap_usdc` was the only
one binding. Say the word and I will floor it at the strategy's own request.

**2. `over_notional_cap` now guards the confidence budget, not $10.** Kept as
the post-clip guard exactly where D-382-judgment Ruling 2 put it, but the number
it enforces moved with the order size. Holding it at $10 would have refused
every sized-up order and re-imposed the flat cap by the back door. Under `flat`
mode it is still `notional_cap_usdc`, unchanged.

## A defect I introduced and caught before it shipped

First wiring re-created `insufficient_capital` by accident, under another name.
On a book bled to $20 the 1% floor is $0.20 and buys nothing, so D-382 correctly
steps aside and passes the order through - but the guard was still measuring
that passed-through order against the $0.20 budget and skipping it
`over_notional_cap`. **That is the exact refusal D-366 R1 forbids and Aym
overruled.** Fixed by `size_entry()` returning `(shares, budget, applied)` so
the guard is always measured against the budget the order was actually sized
from. Pinned by
`TestConfidenceSizingAddsNoRefusal::test_a_bled_book_fills_instead_of_refusing`.

**D-382 adds NO refusal of any kind, on either path.** A test asserts no
decision-count key anywhere contains the string `confidence`.

## D-366 preserved exactly

- No skip on insufficient capital. The clip mechanism is untouched and still
  fires on pass-through orders (test pins 180 shares on a $100 book).
- `requested_shares` / `filled_shares` semantics preserved - the ENTER row still
  carries the original ask and the final size.
- Re-sizes land in `sizing_counts` (`taker_sized_up_at_confidence`,
  `taker_sized_down_at_confidence`, and the two maker equivalents), **never**
  `decision_counts` - the one-count-per-CSV-row identity is asserted.
- The physical `SKIP unsizable_at_position_pct` is unchanged.
- **Real-money `DEFAULT_LIMITS` untouched** (10/30/60, drawdown 0.25) and now
  asserted by a second test.

## Numbers, re-derived (not quoted)

- **Suite: 4,297 passed / 1 skipped / 0 failed**, 392.96s
  (`4,257 + 40 new = 4,297`, exactly).
- **Harness: 21/21 ALL PASS, rc 0.**
- All 40 new tests re-run BY NAME with `-k`: **40 selected, 40 passed.** Nothing
  disappeared. (I briefly mis-read a 162 baseline that was TWO files combined;
  `181 + 21 = 202 = 162 + 40` reconciles it.)

**Three pre-existing shadow-loop tests failed on the first full run** and were
fixed, not suppressed: `test_entry_writes_signal_order_fill_and_position`,
`test_resolution_settles_the_position_row`,
`test_equity_reflects_realized_pnl_after_resolution`. All three pinned the flat
$10 stake (`qty == 19`, `pnl_net == 9.5`, `equity == 1009.5`) inside **wiring**
tests. Per the standing correction - keep policy numbers in the policy tests -
they now DERIVE the size from the row they are checking rather than re-pinning
it, so the next sizing ruling will not break them again.

## Restart and sweep (D-382 R5)

Pre-restart snapshots via the sqlite **backup API**, never `cp`:
`db/snapshots/{trading,trading-survivors,trading-realm-c}.db.pre-D382-20260820T192115Z`.

**Equity was PRESERVED, not re-funded**, per the brief. There is no equity
resume in the loop - `--equity` sets `starting_equity` and a fresh process has
no positions - so preserving it means passing each book's own last measured
`get_equity()` back in.

| realm | old pid | new pid | tmux | started | equity carried |
|---|---|---|---|---|---|
| main (16 names) | 40841 | **47328** | `shadow-main` | 15:28:47 EDT | $981.44 |
| env B (4 fair_value) | 40884 | **47330** | `shadow-survivors` | 15:28:47 | $940.94 |
| realm C (6 un-paused) | 40927 | **47291** | `shadow-realmc` | 15:28:05 | $870.54 |

All three died fast on SIGTERM - **realm C 2.1s, env B 1.0s, main 1.1s.** Main
did NOT take ~30s this time; the D-366 handoff's 30s figure did not reproduce.
All three tmux sessions **exited with their process** (the pane's bash was
running the command), so all three were recreated with `tmux new-session`, not
`send-keys`.

**Orphan sweep AFTER the restart, against the NEW pids** (boundary = owning
process start time, from `ps -o lstart`; dry run first, then `--apply`):

| realm | rows | cost basis |
|---|---|---|
| main | 7 | $20.73 |
| env B | 7 | $30.43 |
| realm C | 13 | $45.84 |
| **total** | **27** | **$96.99** |

`integrity_check ok` and `still open pre-bnd: 0` on all three. **Each book's
sweep cost basis equals its final `open_risk` exactly** (20.73 / 30.43 / 45.84),
which is the check that the equity carry-over is coherent: the premium excluded
from the new starting equity is precisely the premium booked as orphan loss.

## D-382 is live and already measurable

Position cost per entry, point-in-time read 15:29:47 EDT:

| realm | pre-restart (last hr) | post-restart |
|---|---|---|
| main | n=41, avg $3.11, max $7.79 | n=0 (too early) |
| env B | n=161, avg $5.86, max $9.80 | n=3, avg $4.82, max $5.58 |
| realm C | n=188, avg $6.64, max **$10.00** | n=14, avg **$20.95**, max **$156.48** |

Realm C's $156.48 is ~18% of its $870 book - a high-confidence signal sizing up,
which is the ruling working. The old max of exactly $10.00 is the flat cap it
replaced.

**Expect the books to move much faster now, in both directions.** Two things
follow that nobody has seen yet: D-366's clip and `unsizable_at_position_pct`
should finally start firing (the D-366 handoff recorded zero of both, because
$10 orders never approached a $900 ceiling), and **sizing up consumes real book
depth** - env B's $4.82 average fill against a >= $9.40 floor request is already
partial fills, not small requests. That is honest slippage measurement, not a
bug, but it is new and it is the first thing to read.

## Docs

- **`docs/DECISIONS-INDEX.md`:** D-382 line added (Raven's `1627721` recorded
  D-382 in the log with no index line). **I also added a D-383 line** - the same
  gap had already recurred one commit later. That one was NOT in my brief;
  revert it if Raven wants to record it with its own ruling context. It is
  flagged `RECORDED IN THE LOG, NOT YET IMPLEMENTED`. Header count 161 -> 163.
- **`docs/CONVENTIONS.md`: the dispatcher freeze-gate is convention 36, NOT 35.**
  The brief said "verify 35 is the next free number; the count runs to 34" - it
  does not. 35 was already taken by the commit-trailer-block rule. Per D-292 I
  took the next free number. `tests/test_conventions_doc.py` asserts contiguous
  numbering and passes (16/16).

## Explicitly NOT touched

- `SHADOW_RISK_LIMITS` - **including `max_drawdown_frac`, still 1.0.** D-383 now
  rules otherwise; that is the next brief.
- `config.yaml`. `engine/halt.py`. No HALT file created.
- `engine/risk/events.py`, `engine/risk/constraints.py`, the 049 instrument.
- 048's measurement changes (D-380 R1 hold stands).
- `notional_cap_usdc` is still not lifted, and `lift_shadow_capital_caps`'s
  docstring is updated to say why the REASON changed: it is no longer the order
  size, so lifting it would now do nothing under `confidence` mode while
  silently removing `flat` mode's only order-size cap.
- No sweep on any book other than the three I restarted.
- Real-money `DEFAULT_LIMITS`, the Alpaca key, any wallet or API credential.

## Is the tree quiet for tick8?

**Yes, with one caveat.** My three commits are the only code changes; suite and
harness are green; the lock is released. The caveat is not mine: **Raven pushed
D-383 into this tree at 15:31 while I held the lock**, and D-383 is recorded but
unimplemented, so tick8 will read a `max_drawdown_frac` in source that
contradicts a ruling in the log. tick8 should be refreshed with the post-restart
pids (47328 / 47330 / 47291) and told plainly that D-383 is not live.
