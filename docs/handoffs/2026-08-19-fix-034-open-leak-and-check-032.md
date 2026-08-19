# Fix 034's `_open` leak, check 032 — executed 2026-08-19 00:00 EDT

Executed `docs/handoffs/from-raven/2026-08-19-fix-034-open-leak-and-check-032.md`
in full. All five tasks done, tree clean on my files, pushed. One finding the
instruction did not anticipate, reported at the bottom: **the shadow loop is
dead** and **a sibling overnight session left an uncommitted change in the
tree**. Neither is mine to fix; both need Raven's eyes.

## What I did

**Task 0** — committed the two untracked handoff docs
(`2026-08-18-034-tests-d319-convention31-EXECUTED.md`,
`2026-08-18-proposal-034-commit-and-d319-BLOCKED.md`) as instructed. `0dacc2f`.

**Task 1** — fixed the leak in `strategies/polymarket/fair_value_settlement_exit.py`.

The bug, confirmed live before touching anything: since the 22:50:58 EDT
shadow-loop restart, 034 logged **25 self-inflicted
`strategy_concurrency_cap_reached` skips against 0 rows in `positions`**
(queried `signals`/`positions` directly, convention 30) — `evaluate()` was
calling `_note_open()` on every ENTER decision, before the adapter's own
`max_concurrent_positions` (26 rejections) and `PolymarketRiskGate` (17 +
2+2+2+... rejections) had a chance to refuse the fill downstream. 034 was
starving itself on top of being starved by the account-wide cap.

Fix, per the ruling: `_note_open` no longer runs from `evaluate()`. It now
runs from `manage_exit()`, the first time this strategy instance sees a given
position in `self.adapter.open_positions()` — i.e. only once the position has
actually filled. Keyed the same `(market_slug, attempt_number)` way as
before, read back off `position.features['attempt_number']` (stamped by
`FairValueArb.evaluate()` on every real ENTER and carried through unchanged
into `PaperPosition.features` by `PaperAdapter.simulate_taker_buy`), with a
`position_id` fallback for the case that should not happen. `_prune_open` is
unchanged — pruning was already time-based, not fill-based, so it needed no
change. Entry gates (edge 0.05, ask cap 0.60, max 2 concurrent), the salvage
floor, and hold-to-resolution semantics are all untouched.

**Task 2** — checked 032 for the same shape. It has it
(`longshot_fade_hold_to_resolution.py:740`, `_note_open` called before
`return decide('ENTER')`, identical to 034's original bug), but the empirical
check says it has not fired: since the 22:50:58 restart, 032 produced **zero**
ENTER decisions and zero rows anywhere that would indicate reaching its own
concurrency gate (no `already_entered_this_window`, no
`strategy_concurrency_cap_reached`, `acted=1` count is 0). Every one of its
729 signal rows in that window is `not_final_third_of_15m` (501),
`insufficient_window_history` (177), or `t_rem_outside_entry_window` (51) —
it is still filling its 20-completed-window sigma tape (needs ~5 hours from a
cold restart) and never got far enough to attempt an entry. Per the ruling's
explicit conditional ("if it does not leak, report the evidence... and change
nothing in 032"), **032 is untouched**. The identical bug shape is latent in
its code and will start leaking the moment its tape warms up and it starts
producing ENTER decisions — flagging this for whoever picks it up next; it is
not a hard blocker tonight since it has not cost a single data point yet.

**Task 3** — tests. `tests/test_fair_value_settlement_exit.py`'s
`TestConcurrencyCap` (previously 2 tests, both premised on `evaluate()`
filling the cap on its own) rewritten for fill-based semantics: 3 new tests
plus the 2 old ones re-derived against `manage_exit`. Net: 5 tests, +3 vs
before.

- `test_downstream_rejection_no_longer_leaks_the_cap` — the regression test
  for the actual bug: three ENTER decisions in a row with **no** `manage_exit`
  call (simulating every fill being refused downstream) now leave `_open` at
  0 and never trip the cap. Before the fix this would have SKIPped the third
  one under `strategy_concurrency_cap_reached`.
- `test_manage_exit_notes_the_open_on_first_sight_of_a_filled_position` — a
  filled position is counted exactly once, idempotently, across repeated
  `manage_exit` calls on the same still-open position.
- `test_open_key_falls_back_to_position_id_when_attempt_number_missing` — the
  defensive fallback path (convention 11), not the expected one.
- `test_max_two_concurrent_then_third_is_capped` — re-derived: two positions
  noted via `manage_exit`, third `evaluate()` still correctly SKIPs at the
  cap.
- `test_open_positions_prune_once_their_window_has_resolved` — re-derived:
  prune-by-time still clears a resolved window's slots; `evaluate()`'s own
  ENTER no longer self-adds, so the post-prune count is 0, not 1 (the old
  assertion assumed the now-removed self-add).

No test changes made to 032 or its test file — the ruling's "(and 032 if
fixed)" clause is conditional on actually fixing it, and I didn't.

**Task 4** — verify and commit.
- Full suite: **3,818 passed, 1 skipped, 0 failed** in 350s (`.venv/bin/python
  -m pytest tests/ -q --ignore=tests/test_dashboard_charts.py`). Baseline was
  3,815 (CLAUDE.md); +3 net new tests in `TestConcurrencyCap` accounts for the
  delta exactly.
- Harness: `.venv/bin/python backtest/validate_harness.py` — **21/21 PASS**,
  exit 0.
- Registry re-derived: **25 strategies, index 24 = FairValueSettlementExit**,
  `supported_market_types == ('crypto_updown',)`, unchanged.
- Commit `9d9a234` ("fix: 034's _open leak..."). First commit attempt was
  **REFUSED by the conflict-check pre-commit hook** — I had edited both files
  with the plain Edit tool, bypassing `engine.concurrency`'s ledger
  (convention 26 violation, my own mistake, not a peer conflict: `who`
  reported 0 active checkouts throughout). Reconciled by re-writing the
  already-correct on-disk content through `engine.concurrency.safe_write`
  with `agent_id='cody-034-openleak'` for both files, re-staged, re-committed
  clean. Pushed. `origin/main == HEAD == 9d9a234` confirmed.

**Task 5** — this file, wake-up file rewrite, webhook post.

## Files changed (mine, all committed and pushed)

- `strategies/polymarket/fair_value_settlement_exit.py` — the fix, plus a
  docstring section explaining it (module docstring + `_open_key_for`'s own
  docstring).
- `tests/test_fair_value_settlement_exit.py` — `TestConcurrencyCap` rewritten,
  `_position()` helper extended with `position_id`/`features` params
  (backward compatible defaults).
- `docs/handoffs/2026-08-18-034-tests-d319-convention31-EXECUTED.md`,
  `docs/handoffs/2026-08-18-proposal-034-commit-and-d319-BLOCKED.md` — Task 0
  bookkeeping.

## Two findings the instruction did not anticipate — Raven needs to see these

### 1. The Polymarket shadow loop is dead. Neither PID in CLAUDE.md is running.

Checked at session start (23:36 EDT): PID 27490 (loop) and PID 90158
(wrapper) both alive, matching CLAUDE.md. Checked again after my pytest run
(23:46 EDT): **both gone**. `ps -p 27490` and `ps -p 90158` return nothing;
`ps aux | grep shadow_loop` returns nothing. I did not touch either process.

Its log (`logs/polymarket_shadow_20260819T025057Z.log`) stops cleanly at
23:38:57 EDT with no traceback, no CRITICAL, no SIGTERM/SIGKILL message — it
just stops. The `signals` table's last row is timestamped 23:39:17 EDT,
consistent. Liquidation recorder (48637) and the hyperliquid poller (37578)
are both still alive and unaffected, so this was not a machine-wide event.

**Likely benign explanation, not confirmed**: `docs/handoffs/from-raven/
2026-08-18-overnight-profitability-push.md` (Task 5, the sibling session's
instructions, see below) says explicitly "Do NOT restart the loop yourself...
Raven restarts after review" — consistent with the loop having been stopped
deliberately, by Raven or by Aym, before or during that overnight push, so
Task 1's maker-wiring change (the highest-risk change in the codebase per
that file) could be developed without a live process reading stale source
mid-edit (convention 13). I have **not** restarted it, per my own
instruction's explicit "do not restart... Convention 13 applies" and this
file's — my fix reaches the loop whenever it next restarts.

### 2. A sibling overnight session left uncommitted work in the shared tree.

`git status` after my own push shows `config.yaml` and `docs/DECISIONS.md`
modified, not staged, not committed. Diff: `polymarket.max_concurrent_
positions` and `polymarket.risk.max_concurrent_positions` both raised 5→10,
and a new **D-321** entry ("RATIFIED by execution under Aym's overnight
directive, 2026-08-18") documenting the raise. This matches `docs/handoffs/
from-raven/2026-08-18-overnight-profitability-push.md` Task 2 exactly — a
parallel session (Aym's explicit overnight authority, full authority granted,
shadow only) was dispatched to do four things: wire the maker-fill
simulation, raise the cap 5→10, pause `fair_value_arb_hft`/`fair_value_arb_
inverse`, and fix the 027 caller-feed SSL error. That file's own Task 5 says
"Commit ONLY if the tree is otherwise clean; otherwise report the diff" — the
tree has this diff sitting in it, uncommitted, and D-322 (the pause ruling)
is referenced by the D-321 text but I could not find its own entry in
`docs/DECISIONS.md`, so Tasks 3 and 4 of that push look incomplete too. No
`claude -p` process is running for it now (checked via `ps aux`) — it may
have stopped naturally after Task 2 to let a later step run, or it may have
died the same way the shadow loop did. I have **not touched `config.yaml` or
`docs/DECISIONS.md`** — that overnight push explicitly claimed those files as
its own disjoint scope ("DO NOT touch [fair_value_settlement_exit.py] — work
on DISJOINT files: shadow_loop.py, paper_adapter.py, config.yaml,
strategies/polymarket/__init__.py, caller_feed.py") and reconciling or
completing its work is not something I have visibility into (I don't know
what Task 1's maker-wiring state is, if anything was started). Flagging for
Raven rather than guessing.

Both of these are reported, not acted on. My own scope (034's leak, 032's
check) is done, tested, and shipped independently of either.

## Test count: RE-DERIVE, do not quote

```
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_dashboard_charts.py
```
3,818 passed, 1 skipped, 0 failed, 350s, as of `9d9a234`, my own commit.
`docs/DECISIONS.md`/`config.yaml`'s uncommitted diff does not touch test
files, so this number is unaffected by the open item above.
