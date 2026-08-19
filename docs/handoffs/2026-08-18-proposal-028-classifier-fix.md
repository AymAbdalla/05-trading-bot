# Proposal 028 classifier fix - 8 status_quo_collector skip reasons classified

**Cody, 2026-08-18 ~20:45 EDT.** Executed
`docs/handoffs/from-raven/2026-08-18-fix-028-classifier.md`. Mechanical fix:
`test_every_skip_reason_the_strategies_emit_is_classified` was failing because
`status_quo_collector.py` (proposal 028) emits 8 skip reasons not present in
`SKIP_CLASSIFICATION` (agents/forge_shadow_eval.py).

## What changed

`agents/forge_shadow_eval.py`, one new section (+35 lines), added after the
`no_underdog` entry, before the dict's closing brace. No other file touched.

## Classification choices and why

Read `strategies/polymarket/status_quo_collector.py` and
`status_quo_classifier.py` directly rather than taking Raven's bucket labels
literally - the brief's English descriptions ("model/shape rejection",
"market structure rejection", "execution ladder") are semantic hints, not
the actual bucket names (the file only has DATA_BLOCKER / GENUINE /
SIM_LIMIT / UNKNOWN). Applied the file's own test: does the reason name a
MISSING INPUT, or a FALSE CONDITION computed from present data?

- **`classifier_change_event_shape`, `classifier_unknown_shape` -> DATA_BLOCKER.**
  The classifier is deterministic and rule-based over real inputs (question
  text, resolution_date), so at first glance these look like evaluated
  conditions. But `status_quo_collector` only trades STATUS_QUO-shaped
  markets - a CHANGE_EVENT or UNKNOWN classification means the market is
  categorically the wrong product, the same shape as the existing
  `not_a_temperature_market` entry (weather_arb handed a crypto window) and
  `resolution_station_unknown` (station could not be determined at all).
  Both of those are DATA_BLOCKER precedents in the file already. GENUINE
  would read as "looked at a STATUS_QUO condition and declined," which is
  false - it never had a STATUS_QUO-shaped market to evaluate.
- **`not_binary` -> DATA_BLOCKER.** Same wrong-product logic: the strategy
  prices binary continuity contracts only; a non-binary market was never a
  candidate.
- **`no_resolution_date`, `no_market_slug` -> DATA_BLOCKER.** Literal missing
  inputs (`market.end_date`, `market.slug` both None) - same shape as
  `no_market`, `no_trade_clock`.
- **`price_outside_entry_band` -> GENUINE.** `best_ask` was real and present;
  the condition `min_no_price <= best_ask <= max_no_price` was computed and
  came out false. Same shape as `mid_outside_quote_band`.
- **`ladder_rung_not_yet_reached`, `ladder_fully_filled` -> GENUINE.** Both
  come out of `_next_rung()`, computed from a real `best_ask` against real,
  strategy-tracked rung state (`self._rungs_for(slug)`). Same shape as
  `already_entered_this_window` / `pair_complete` - a condition on live state,
  evaluated and false, not a missing input.

## One bug caught and fixed before verification

First write introduced an unescaped apostrophe inside a single-quoted string
literal (`'...the classifier's '`), which broke the module's syntax entirely
(`import agents.forge_shadow_eval` raised `SyntaxError`). Caught immediately
by importing the module before running tests, fixed with a second
`safe_edit` swapping the string to double-quotes. Confirmed clean import
before proceeding. Worth flagging: a syntax error in this file would have
failed every test that imports it, not just the classifier test - importing
after any edit to this file is cheap insurance.

## Verification

- Target test: `test_every_skip_reason_the_strategies_emit_is_classified` - PASS.
- Full suite: `.venv/bin/python -m pytest tests/ -q --ignore=tests/test_dashboard_charts.py`
  -> **3,707 passed, 1 skipped, 0 failed**, 350.81s.
- Harness: `.venv/bin/python backtest/validate_harness.py` -> **21/21 checks,
  exit 0**.

## Not committed

The working tree was not clean going in - proposal 028's own files
(`strategies/polymarket/status_quo_classifier.py`,
`strategies/polymarket/status_quo_collector.py`, two test files, plus
modified `docs/DECISIONS.md`, `strategies/polymarket/__init__.py`, three
other test files, and the three standing unstaged research/log exclusions)
were already uncommitted from an earlier session, none of it touched by this
fix. Per the brief's rule ("do NOT commit unless the tree is clean"), nothing
was committed. `git diff --stat agents/forge_shadow_eval.py` shows exactly
one file, 35 insertions, 0 deletions - that is the full scope of this
session's change.

Concurrency: 0 active checkouts before and after, no peer `claude -p`
sessions observed. Both edits went through `engine.concurrency.safe_edit`
with `agent_id='cody-028-fix'`.

## Open question for Raven

None from this fix - it's mechanical and verified. Proposal 028's broader
status (wired but no owner per CLAUDE.md's "Genuinely open" section) is
unchanged; this session only made the classifier table complete.
