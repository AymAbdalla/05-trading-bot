# Cody session plan - 2026-08-13, autonomous batch

**Written:** 2026-08-13 evening
**For:** Raven, check-in log for this session
**Trigger:** Raven's go-ahead, working autonomously, checking in at decision
points and batch completions via Hermes (telegram:8017725309).

Background: two heavy sweeps (`run_incremental_graveyard.py`,
`constraint_sweep.py`, PIDs 63767/63797) are still running from before this
session started. Nothing below touches those processes or races them for
correctness - editing source files on disk does not affect code already
loaded into a running process's memory. Test runs in this session will
contend with them for CPU, which just means slower, not wrong.

## Priority items (as given)

1. Write governance carve-out + D-217 + Option 2 decisions to DECISIONS.md
2. Build `agents/judge.py` (Judge-as-code)
3. Wire `instruments.py` into the sweep loop
4. Fix C2 WeekendVacuumReversion
5. Build v4 ignition strategies as share strategies
6. Write runbook in `agents/README.md`

## Recon findings before touching anything

- **Item 1** has a fully-scoped answer already sitting in
  `docs/agent-proposal-reconciliation.md` (Q1, Q2, Q4, bottom line). Three
  exact lines to add, not open decisions:
  - Governance carve-out: "trading-bot agents run standalone; Raven audits
    monthly, not per-decision."
  - D-217: run under SPEC-5.7 rules now (interim safety) AND Aym ratifies
    the 11 rules directly (permanent resolution) - combines the proposal's
    options (a) and (b), per the joint debrief.
  - Option 2 status: "Hermes-cron-triggers-subagent is the near-term shape
    (Option 1 refined); Option 2 (Agent SDK runner) stays the long-term
    target if/when volume or reliability demands it."
  - Also folding in the reconciliation doc's item 3 (runbook dropped off
    the build list) - item 6 below closes that gap too.

- **Item 5 is ALREADY DONE.** `strategies/builtin/strategy_lab_v4.py` (317
  lines) implements all three IGNITIONS as share strategies
  (`V4_gap_hold_proxy`, `V4_52w_high_breakout`, `V4_trend_reclaim`), fully
  tested (16 tests in `tests/test_strategy_lab_v4.py`), and already wired
  into `run_incremental_graveyard.py`'s `ALL_STRATEGIES`. It was built
  (file mtime 16:19 today) but never logged in DECISIONS.md and ROADMAP.md
  still describes it as future work. I1 (PEAD) is honestly labeled a
  price-only proxy since there's no earnings calendar (matches the P5
  precedent for the same gap). Action: verify it still passes, then write
  the decision entry and fix ROADMAP.md's stale wording. No new code.

- **Item 4 (C2)**: root cause is `backtest/vectorized_harness.py`'s
  `run_sweep`, which hard-rejects any strategy with `min_bars > SCAN_WINDOW`
  (260) as NOT_TESTED, and `_make_window`/`scan_all_bars` always slice a
  fixed 260-bar window regardless of what the strategy asked for. C2 needs
  840. Fix: give `_make_window` a per-call `window_size`, have
  `scan_all_bars` pass `max(SCAN_WINDOW, strategy.min_bars)`, and change the
  `run_sweep` gate to only NOT_TEST strategies past a sane upper bound
  (not just "bigger than the global default"). This only widens the window
  for the ONE strategy that asks for it - the other 34 strategies keep
  their existing O(1)-per-bar cost, so this doesn't slow down the sweeps
  already running. Touches the same file as item 3, doing both together.

- **Item 3 (instruments.py wiring)**: `backtest/instruments.py` (contract
  specs, 9 tests) exists but nothing calls it from the sweep. Plan: wire
  contract sizing into `vectorized_harness.py`'s cost/sizing path for
  futures/options tickers, stamp `contract_qty`/`capital_at_risk` on
  entries, and confirm the existing pool-guard keeps futures/options out of
  spot-pooled stats (ROADMAP P0.4's explicit requirement). Doing this in
  the same pass as the C2 fix since both touch `vectorized_harness.py`.

- **Item 2 (judge.py)**: SOUL.md requires PF+n+window, buy-hold delta, twin
  PERCENTILE (not single draw - already computed per-entry as
  `twin_percentile` in `vectorized_harness.py`), NOT_TESTED handling,
  PROVISIONAL stamping when `validate_harness.py` is red, and expected-
  best-by-chance reporting. Building this as one module that composes the
  existing pieces (`validate_harness.main()`, `assertions.run_all()`,
  `pooled_analysis.pool()`/`per_strategy_summary()`,
  `asset_class_analysis.analyze()`, `summarize_graveyard.summarize()`) into
  one evidence-pack JSON emitter, per the runtime proposal's "Judge is
  ~80% deterministic already" framing. New file, no conflict with the
  vectorized_harness.py work above - running this as a parallel background
  build.

## Execution order

1. DECISIONS.md + ROADMAP.md writes (item 1 + item 5 documentation) - fast,
   no code risk, done first.
2. Launch `agents/judge.py` + tests as a background build (item 2) -
   independent file, runs in parallel with step 3.
3. C2 fix + instruments.py wiring together in `vectorized_harness.py`
   (items 3 + 4) - sequential, same file, done by me directly given the
   precision needed not to regress the 517+ passing tests or the two live
   sweeps' eventual re-run correctness.
4. Full test suite + `validate_harness.py` after steps 2+3 land.
5. Runbook in `agents/README.md` (item 6) - last, since it documents the
   judge.py loop built in step 2.

Check-in to Raven after step 1 (governance decisions, in case of pushback),
after steps 2-3 land together (build batch), and at session end.

---

## Results (session end, 2026-08-13 ~18:05)

All six items closed. Full test suite: **547 passed, 1 skipped** (the skip
predates this session, unrelated). `validate_harness.py`: **21/21**. Both
background sweeps (`run_incremental_graveyard.py` PID 63767,
`constraint_sweep.py` PID 63797) ran undisturbed the entire session -
nothing here touched their state or competed with them beyond ordinary CPU
sharing.

1. **DECISIONS.md**: D-243 (governance carve-out), D-244 (D-217 ratified:
   SPEC-5.7 interim + Aym ratifies directly), D-245 (Option 2 stays the
   long-term target). ROADMAP.md gained a P6 cross-link section.

2. **`agents/judge.py`**: built by a background subagent, reviewed and
   independently test-run by me. 317-ish lines wrapping
   validate_harness/assertions/pooled_analysis/asset_class_analysis/
   summarize_graveyard into one evidence-pack JSON, matching SOUL.md's
   requirements (twin percentile not single-draw, NOT_TESTED never
   converted to FAIL, PROVISIONAL stamping, cold_start/reviewable/evaluable
   confidence labels). `tests/test_judge.py`, 17 tests, all green. D-247.

3. **`instruments.py` wiring (D-249)**: the actual bug was one layer
   deeper than the ROADMAP note implied. `TradeCoster` already imported the
   contract-sizing logic (D-235) but its constructor floored `contracts` to
   a minimum of 1 regardless of affordability - `instruments.py`'s own
   `size_for` (which correctly returns 0 when unaffordable) was imported
   but never reachable. Every futures trade silently sized 1 contract even
   when the account couldn't afford the margin - the exact "79,642
   fictional rows" problem P0.4 describes, just hiding one function call
   deeper than expected. Fixed at the shared `TradeCoster`/
   `CostModel.coster()` layer so all three harnesses (vectorized, event,
   cross-sectional) inherit it at once. `pooled_analysis.pool()` also now
   excludes FUTURES/OPTIONS from dollar-pooled cells by default, matching
   the precedent already set in `cross_sectional.py`. One existing test
   (`test_cost_model.py`) had PINNED the bug as expected behavior - split
   into an unaffordable-at-$100 case (now correctly produces zero trades)
   and an affordable-at-$2,000 case (still one contract, $1,800 margin).
   One more existing test (`test_dispersion_gate.py`, the futures toll-rate
   pin) needed the same fix for the same reason. **Not done**: re-running
   the graveyard's existing 79,642 futures rows, which were produced under
   the old bug - flagged in ROADMAP P0.4 as a deliberate follow-up (needs
   those entries removed from the graveyard JSON first, which is a call
   for whoever runs it, not a silent side effect of a code fix).

4. **C2 WeekendVacuumReversion (D-248)**: the old gate rejected any
   strategy with `min_bars > SCAN_WINDOW` (260) outright, even when the
   actual series had 5+ years of data. Now each strategy gets its own
   window (`max(SCAN_WINDOW, strategy.min_bars)`) and the NOT_TESTED check
   asks whether THIS series is long enough, not whether it fits the shared
   260-bar default. Other 34 strategies' per-bar cost is unchanged. Proved
   the window actually widens with a spy-scan test; did not attempt to
   prove C2 fires on synthetic data (a separate, larger fixture problem -
   no test file for strategy_lab.py's own firing logic exists yet, a
   pre-existing gap out of scope here).

5. **v4 ignitions**: confirmed already built (`strategy_lab_v4.py`, 17
   tests, wired into the sweep) - a documentation-debt fix only (D-246),
   no new code. ROADMAP.md's stale "being built" wording corrected.

6. **Runbook**: added to `agents/README.md` - start/stop/audit for
   `judge.py`, plus a short governance summary section linking D-243/244/245.

Nothing here touches execution/, risk.py, config.yaml, or requires Aym's
sign-off beyond what D-244 already authorizes (he ratifies D-217's 11 rules
directly whenever he has bandwidth; nothing was blocked waiting for that).
