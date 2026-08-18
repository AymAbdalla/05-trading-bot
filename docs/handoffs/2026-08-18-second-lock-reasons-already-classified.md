# Handoff: second-lock skip reasons were already classified (no code change)

**From:** Cody, 2026-08-18
**Instruction:** `docs/handoffs/from-raven/2026-08-18-classify-new-second-lock-reasons.md`
**Outcome:** Nothing to build. The requested change was already in the tree,
made by a concurrent session. I verified it rather than duplicating it.

---

## Headline

**Raven's task was already done, and done correctly.** Both reasons are in
`SKIP_CLASSIFICATION` as `GENUINE`, which is exactly what the instruction
asked for. I wrote no code. Do not read this as "Cody fixed the test" - the
test was already green when I arrived.

| what Raven asked | state when I checked |
|---|---|
| `'no_recent_liquidation': (GENUINE, '')` | present, `agents/forge_shadow_eval.py:401` |
| `'liquidation_below_second_lock_min': (GENUINE, '')` | present, line 404 |
| AST test passes | passes, 5/5 consecutive isolated runs |

## What I verified (MEASURED, not assumed)

1. **Both reasons exist in the strategy**, `strategies/polymarket/near_liq_trigger.py`
   lines 976 and 981, with the module comments Raven quoted ("RAN. The tape was
   silent on this side." / "the tape printed, but under a floor WE chose").
2. **Both are classified GENUINE** and sit in the `# === added 2026-08-18
   (second pass) ===` block under a 22-line comment explaining the call.
3. **No duplicate keys.** Walked the dict's AST: 167 keys, zero duplicates.
   This mattered - the same table already silently lost a `no_market` entry to
   a duplicate key once (a duplicate dict key is not a Python error, the later
   one just wins).
4. **`liquidations` table: 0 rows.** So the GENUINE-vs-DATA_BLOCKER choice has
   **zero retroactive effect**: `select count(*) from signals where skip_reason
   in (<the two>)` returns **0** of 49,575 skips. Cheap to reverse.

## The thing Raven should know: two instruction files disagreed

Two Raven instruction files landed on this, **three minutes apart, asking for
opposite classifications**:

- **06:40** `2026-08-18-mechanical-fixes-from-review.md` (Task 1) asked for
  **DATA_BLOCKER** on both, rationale "the feed table has 0 rows".
- **06:43** `2026-08-18-classify-new-second-lock-reasons.md` (what I was given)
  asked for **GENUINE**.

The concurrent session that implemented this saw only the 06:40 file, declined
DATA_BLOCKER on its own reasoning, and shipped GENUINE - which the 06:43 file
then independently asked for. Both arrived at the same place.

**The 06:40 rationale was wrong on a point worth keeping**, and its own
in-file rebuttal is the useful artifact: with 0 rows `window.ok` is False, so
the strategy emits `liquidation_feed_empty` and **neither of these two reasons
is even reachable**. "The table is empty" describes a *different key*. Both of
these sit strictly *after* `window.ok` is True, i.e. the recorder was alive,
fresh and long enough - the feed WAS observed. GENUINE is right.

**If Raven wants this changed to DATA_BLOCKER anyway, it needs a D-number**
(convention 10), not a third instruction file.

## What I could not do

I tried one comment-only edit to `agents/forge_shadow_eval.py` and **the
permission layer blocked it** (non-interactive session). I did not retry it.

The in-file comment at line ~394 currently reads *"Raven's 2026-08-18
instruction file asked for DATA_BLOCKER on both"*. With two instruction files
now on disk saying opposite things, that sentence reads as **wrong** to anyone
who opens the 06:43 file first. My intended edit named both files and dates.
Cosmetic, but it is a stale-fact trap in a file whose whole purpose is
classification hygiene. **Someone with write permission should name the file
in that comment.**

## Test state (MEASURED)

Full suite: **2,223 passed, 1 skipped, 2 failed** in 8m44s.

**Both failures are accounted for. Neither is mine.**

1. `test_config_yaml_matches_the_module_defaults` - **known-red, doing its
   job.** `config.yaml daily_loss_limit_usdc = 0.0` vs module default `30.0`.
   This is the deliberate deviation flagged in CLAUDE.md and still awaiting
   Aym's ruling on whether breakers-off is the intended shadow posture. Do not
   weaken it.

2. `test_every_skip_reason_the_strategies_emit_is_classified` - **a transient,
   and worth understanding.** It FAILED inside the full-suite run but passes
   5/5 in isolation, before and after. Cause: the test globs
   `strategies/polymarket/*.py` and parses them **from disk at execution
   time**, so a concurrent session writing a new SKIP literal during the 8m44s
   run makes it fail. I ruled out the alternative explanation (test pollution):
   no `pytest-randomly`/`xdist` is installed so ordering is deterministic, and
   the only test that mutates the dict adds a key and deletes the same key.

   **I did not capture which reason was missing** - my output tail caught the
   summary line, not the assertion body. So I can say it flapped, not what it
   flapped on. Convention 21: this test is inherently order-of-the-clock
   sensitive in a shared tree. **It will keep flapping while multiple sessions
   edit strategies.** That is arguably a property Raven wants to keep (it is
   what catches a new unclassified reason), but it means a red run of THIS test
   in a shared tree is not evidence until re-run in isolation.

`backtest/validate_harness.py`: **21/21 passed, exit 0**, ALL PASS (harness
wiring, accounting and fee application verified), finished 06:54:25. Convention
1 satisfied - though I changed no code, so there was nothing new to invalidate.

## Not done, per instruction

- Nothing committed. Tree left for review.
- No shadow loop restart, no feeds killed, no graveyard sweep touched.
  (Note: the instruction cited shadow loop PID 27030; MEASURED, the live PIDs
  are different again - convention 25. I did not touch any of them.)

## Next steps for Raven

1. **Confirm GENUINE is final** and, if you still want DATA_BLOCKER, open a
   D-number rather than a third instruction file.
2. **Fix the comment at `forge_shadow_eval.py:~394`** to name which instruction
   file it is rebutting. Blocked for me by permissions.
3. **Decide whether the AST test's disk-read flap is acceptable.** It is a real
   trade-off, not a bug: reading disk at runtime is what makes it catch new
   reasons, and also what makes it unreliable in a shared tree.
4. The open ruling on the 0.0 loss breakers is unchanged and still blocking a
   green suite.
