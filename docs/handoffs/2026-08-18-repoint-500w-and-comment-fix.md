# Handoff: repoint the proxy error to 500w, and name both instruction files

**By:** Cody, 2026-08-18 ~07:25
**Acting on:** `docs/handoffs/from-raven/2026-08-18-repoint-to-500w-and-comment-fix.md`
**Scope:** mechanical only. No strategy decisions, no new strategies, nothing
committed, nothing restarted, nothing killed.

---

## Task 1: `NOISE_FLOOR_ERROR_BY_ASSET` repointed at the 500w measurement

Done. **20 edits across 5 files.** Raven's file named 2 sites; there were 8
carrying the 220w numbers, and 12 more comment/docstring/test lines quoting
them. Convention 23.

I verified the numbers against the JSON rather than copying them from the
instruction file. They match exactly:

| asset | 220w rate | **500w rate** | 220w n | **500w n** | disagreements |
|---|---|---|---|---|---|
| btc | 2.7% | **5.1%** | 75 | **175** | 9 |
| eth | 6.6% | **9.3%** | 106 | **248** | 23 |
| sol | 14.3% | **15.8%** | 84 | **196** | 31 |

```python
NOISE_FLOOR_ERROR_BY_ASSET = {'btc': 0.051, 'eth': 0.093, 'sol': 0.158}
```

### I also moved `NOISE_FLOOR_ERROR_N_BY_ASSET`, which Raven did not ask for

**This is the one thing in this session that needs a look rather than a nod.**

Raven's task listed the rate constant, its provenance comment, and the rate
drift test. It did not mention `NOISE_FLOOR_ERROR_N_BY_ASSET`, which sat at the
220w sample sizes `{btc: 75, eth: 106, sol: 84}`. Leaving it would have stamped
a **500-window rate beside a 220-window n on every gated row** - a number that
looks qualified and is not, which is the exact defect D-297 exists to prevent.
Its own drift test says so out loud: "must make this red rather than silently
leaving a 220-window `n` stamped on rows measured over 500."

So I moved it to `{btc: 175, eth: 248, sol: 196}`. **That has a behaviour
consequence, not just a documentation one:**

- `strike_proxy_error_low_sample` is DERIVED from `n` against convention 7's
  threshold of 100. At 220w it was `True` for BTC (75) and SOL (84).
- At 500w all three clear 100, so **the flag is now `False` for all three
  assets on every gated row.**

Nothing in the flag's logic changed - re-measuring cleared it by itself, which
is the shape D-297 asked for. But rows logged before and after this edit carry
different values for that field, so **any query that groups on
`strike_proxy_error_low_sample` now spans two regimes.** Flagging it rather
than burying it.

If Raven wants the `n` left at 220w, that is a one-line revert, but the rate
and the `n` must then be described as coming from different samples.

### Every site touched

**`engine/polymarket/strike.py`** (6)
1. provenance comment: source file `strike_proxy_by_asset.json` -> `_500w.json`,
   sample sizes, and the "5x" span -> ~3x. Records the old numbers and the fact
   that every rate moved UP.
2. `NOISE_FLOOR_ERROR_BY_ASSET` itself.
3. `NOISE_FLOOR_ERROR_N_BY_ASSET` + its comment (see above).
4. the SOL discretization argument, which quoted "SOL's 14.3% at 5 bps".
5. `error_at_floor_pct_for` docstring: unit example `(2.7, not 0.027)`.
6. `error_sample_at_floor_for` docstring: "two of these three are not" was
   about to become false.

**`engine/polymarket/shadow_loop.py`** (2) - the gate comment and the D-297
sample comment, both of which spelled out the 220w numbers inline.

**`tests/test_strike_proxy.py`** (9) - the rate drift test now reads
`_500w.json`; the sample drift test now reads `_500w.json`; the hardcoded
`2.7 / 6.6 / 14.3` assertions; the low-sample expectations; three docstrings.

**`tests/test_strike_proxy_per_asset.py`** (1) - **the site my first grep
missed.** It writes the rate as a bare `2.7`, no `%` and no `0.0` prefix, so it
matched none of the patterns I swept with. The drift test caught it on the
first run. Worth remembering: grep found 5 of 6 files, the harness found the
6th.

**`tests/test_polymarket_shadow_loop.py`** (2) - the stamped-dict assertion and
its docstring.

All edits went through `engine.concurrency.safe_edit` (convention 26), each
anchor asserted to match exactly once so a stale anchor raises instead of
silently no-opping.

### Test result

```
env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q -k "strike or noise_floor" --tb=short -p no:cacheprovider
75 passed, 2274 deselected, 2 warnings in 3.00s
```

**75 passed, 0 failed.** (First run was 74 passed / 1 failed - that was the
`test_strike_proxy_per_asset.py` site above, then fixed.)

The four directly affected test files together: **165 passed**.

Full suite, which finished after the first draft of this note:

```
env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q --ignore=tests/test_dashboard_charts.py -p no:cacheprovider
1 failed, 2291 passed, 1 skipped, 2 warnings in 690.24s (0:11:30)
```

**2,291 passed. The 1 failure is the known permanently-red one**, not
something this session caused:
`test_polymarket_risk_gate.py::TestConfigWiring::test_config_yaml_matches_the_module_defaults`,
failing on `config.yaml daily_loss_limit_usdc 0.0 != module default 30.0`. That
is the daily-loss-breaker drift test CLAUDE.md describes as red by
construction and awaiting a ruling. It has nothing to do with the strike proxy
and I left it alone. Re-derive the count yourself rather than quoting this
line - three sessions are appending tests.

---

## Task 2: the stale comment in `agents/forge_shadow_eval.py`

Done, comment-only. I confirmed both classifications are unchanged afterwards
(`no_recent_liquidation` GENUINE, `liquidation_below_second_lock_min` GENUINE,
`liquidation_feed_empty` still DATA_BLOCKER, 170 keys total).

I read both instruction files before writing the comment rather than trusting
the summary. Both are on disk and both say what Raven said they say:

- **06:40:00** `2026-08-18-mechanical-fixes-from-review.md` - asked
  **DATA_BLOCKER**, rationale "the feed table has 0 rows".
- **06:43:09** `2026-08-18-classify-new-second-lock-reasons.md` - asked
  **GENUINE**, rationale that `near_liq_trigger.py`'s own comments say "RAN".

The comment now names both files with their timestamps, records that GENUINE is
what stands and that D-298 ruled it, and keeps the code-path argument for why:
with 0 rows `window.ok` is False, so the strategy emits `liquidation_feed_empty`
and neither of these two keys is reachable. Classifying them DATA_BLOCKER too
would put one cause under two names (convention 20). It closes with a line
telling a future reader not to treat the 06:40 file as live just because it is
the one they happened to open.

---

## Task 3: the shadow loop - PID 51187 is DEAD, and something restarted it

Raven's file said "if it is dead, report that plainly and do nothing else." So:
reporting, not acting.

**51187 was alive when this session started** (23:09 elapsed at 07:02) and was
gone by 07:07. I did not kill it. The runner `scripts/shadow_runner.py` (PID
51148, alive since 06:39) respawned the loop as **PID 59357 at 07:04:50**, same
command line, `--poll 5 --equity 1000`.

The new loop is healthy:

- `cycles=23 assets=btc,eth,sol evals=1311 entries=15 skips=1296
  equity=$986.44 open=2 resolved=13 identity_ok=True`
- **1311 / 23 = 57.0 = 19 strategies x 3 assets.** Confirmed, not assumed.
- **Zero** ERROR / Traceback / Exception lines in the whole log. The only
  warning is the benign urllib3 / LibreSSL one.
- `strike_inside_proxy_noise_floor` is 111 of 1296 skips this session.

**I did not restart it.** Which means convention 13 applies and you should know
this before reading any row logged since:

> Loop 59357 imported `strike.py` at **07:04:50**. My edit landed at
> **07:05:52**. The running loop therefore still has the **220w** numbers in
> memory and is stamping `noise_floor_measured_error_by_asset` as
> `{btc: 0.027, eth: 0.066, sol: 0.143}` with `low_sample=True` for btc/sol.

Rows carry the new numbers only from the next restart onward. Gating behaviour
is unaffected either way - the ACTIVE floor (`NOISE_FLOOR_BPS_BY_ASSET`, all
three at 1.0) was not touched, so no window is gated differently. Only the
provenance stamp differs. **Restarting is Raven's or Aym's call, not mine.**

---

## FLAG: a concurrent session is briefed to invert the noise floor

Not my task, and I have not touched it, but it collides with the file I just
edited and I am not going to leave it unsaid.

**PID 56615** (`claude -p`, started 06:57, still alive) is running a prompt
whose Task 1 is:

> "LOWER the noise floor to 15bps for all assets (was 5bps). This lets more
> windows through." and "For SOL specifically (14.3% at 5bps ...) set the floor
> to 25bps." and "After fixing, restart the shadow loop."

**The premise is inverted.** The gate is `abs(lead_bps) < floor -> skip`, so
raising the floor blocks MORE windows, not fewer. `strike.py` says this in
capitals directly above the constant, measured from 10,276 actually-rejected
rows:

> "A floor of 15 or 25 bps does not loosen this gate - it tightens it toward
> never firing at all."

Every window the gate has ever rejected had `|lead_bps| < 5.0` (max observed
4.989), so any floor >= 5.0 admits exactly 0.0% more than 5.0 does. The prompt
also calls the floor 5.0 and describes SOL as 14.3%; the active floor is
already **1.0** per asset, and SOL is now **15.8%**. If that session carries
out its brief, it will set every asset to a floor that stops the gate firing
entirely, and restart the loop into it.

I have not killed it and will not. Someone with authority should decide whether
to let it land.

---

## Not done / open

- Full suite came back green apart from the pre-existing permanently-red
  config test (above). It took 11m30s, which is slow; the box is carrying 12
  `claude` sessions plus three feeds.
- Nothing committed. Tree left for review. Never used `git add -A`.
- `research/strike_proxy_by_asset.json` (220w) left on disk. Nothing points at
  it now. Deleting it is a call I did not make.
- The `STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0` constant itself is untouched, per
  the standing note that it is Aym's call.

## For Raven

1. Ratify or revert the `NOISE_FLOOR_ERROR_N_BY_ASSET` move, and note that
   `strike_proxy_error_low_sample` flipped to False for all three assets.
2. Decide whether the shadow loop should be restarted so rows carry the 500w
   stamp, or left to run out on the 220w one.
3. The PID 56615 brief above. That one is time-sensitive.
