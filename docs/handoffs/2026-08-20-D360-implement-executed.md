# D-360 IMPLEMENTED: position count cap removed on the Polymarket shadow path

**Session:** `cody-D360-cap` (declared, not inherited - see identity note).
**When:** 2026-08-20, 11:54 -> 12:06 EDT (measured with `date`).
**Brief:** `docs/handoffs/from-raven/2026-08-20-D360-implement.md`.
**Commit:** `bbc8185`, pushed. 4 files, 32 insertions, 11 deletions.
**Result:** code + tests done and green. **No restart.** One item of the brief
REFUSED with reasons, escalated below - read section 3 before the second restart.

---

## 1. Gate evidence

| Gate | Required | Found |
|---|---|---|
| D-360 in DECISIONS.md | present, R1-R4 read | present, `docs/DECISIONS.md:3997-4004` |
| HEAD re-derived | convention 25 | `8ad03db` at start, **moved to `1b49ca6` mid-session**, `bbc8185` after my commit |
| No sibling writing my files | `ps aux \| grep claude` | PID **438** `cody-tick7-rulings` ALIVE. Its files untouched by me. |
| Both loops live and untouched | main 11872, env B 11895 | both alive, `ELAPSED 24:55` / `24:51`, started 11:41:26 / 11:41:30. **Not restarted, not signalled, not touched.** |

**HEAD moved underneath me again.** `1b49ca6` (**D-361**, shadow split approved +
038 backfill approved by Aym) landed from the tick7 sibling while I was running
the suite. Docs-only, `docs/DECISIONS.md` only, no overlap with my files. This is
the second consecutive session where the brief's HEAD claim went stale mid-session;
convention 25 held both times. **Re-read `git rev-parse HEAD` before you commit.**

**Identity:** `AGENT_ID` probed **EMPTY** this session (`os.environ.get('AGENT_ID')`
-> `None`). Running tally is now **9 SET / 13 EMPTY**. The commit therefore
declared identity explicitly via `CONFLICT_CHECK_AGENT_ID=cody-D360-cap`.

**Tool probes:** Write **WORKED** (in-repo), Write **REFUSED** for `.git/` ("sensitive
file"). Edit **WORKED** (x6). Running tallies: Write **8 WORKED / 5 REFUSED**
(plus the new `.git/` refusal), Edit **11 WORKED / 2 REFUSED**.

---

## 2. What changed (all four are the Polymarket shadow path)

Sentinel is **`100_000`**, as briefed.

| File:line | Before | After |
|---|---|---|
| `engine/polymarket/risk_gate.py:125` | `DEFAULT_MAX_CONCURRENT_POSITIONS = 10  # D-321: raised 5->10, shadow only` | `DEFAULT_MAX_CONCURRENT_POSITIONS = 100_000` + a comment block explaining the sentinel |
| `engine/polymarket/paper_adapter.py:583` | `int(cfg.get('max_concurrent_positions', 10))` | `int(cfg.get('max_concurrent_positions', 100_000))` + D-360 comment |
| `config.yaml:138` | `max_concurrent_positions: 10` (adapter) | `max_concurrent_positions: 100000` |
| `config.yaml:217` | `max_concurrent_positions: 10` (`polymarket.risk`) | `max_concurrent_positions: 100000` |

**Why a large finite int and not `None`/`inf`:** `check_order` does
`snap.count >= self.max_concurrent_positions` and `paper_adapter` does
`committed_slots() >= self.max_concurrent_positions`. An int keeps those three
comparisons and their SKIP taxonomy intact (convention 20 - the
`SKIP:max_concurrent_positions` counter still exists and can still be read),
and a real cap can be restored by editing one number. `None` would have meant
touching every comparison site.

**Tests changed (2, both in `tests/test_polymarket_risk_gate.py`):**

- `test_disabling_does_not_disable_the_other_risk_caps` (:629) - was
  `range(rg.DEFAULT_MAX_CONCURRENT_POSITIONS)`, now an explicit
  `max_concurrent_positions=5` and `range(5)`.
- `test_every_rejection_carries_a_reason` (:1371) - was
  `range(rg.DEFAULT_MAX_CONCURRENT_POSITIONS + 1)`, now
  `gate(max_concurrent_positions=5)` and `range(6)`.

Both are skip-path / verdict-shape tests, exactly the case the brief said to give
an explicit small cap. **Note: both PASSED unmodified against the sentinel** (I
ran them before editing - 343 passed). I changed them anyway because ranging over
the sentinel silently allocates 100,000 `OpenExposure` objects to assert a
mechanic that binds at any cap, and because it re-couples a mechanics test to a
number that is now a policy choice. That is a judgment call, not a required fix.

**The default is still pinned.** `TestConfigWiring::test_defaults_apply_with_no_config_at_all`
(:1419) asserts `g_.max_concurrent_positions == rg.DEFAULT_MAX_CONCURRENT_POSITIONS`
symbolically, and the config.yaml-vs-module agreement test (:1462) ties
`config.yaml:217` and the module constant together. Both still bind, so the two
new numbers cannot drift apart.

---

## 3. REFUSED, and this needs one word from Aym

**I did NOT change `engine/risk/__init__.py:49` (`2`) or `config.yaml:102` (`2`).**
The brief listed both (its items 3 and 4) on the stated grounds that
`engine/risk/__init__.py` "is on the shadow path". **It is not.** Verified:

- `engine.risk.RiskGate` is imported by exactly one production module:
  `engine/executor.py:32`. `engine/executor.py` is imported by exactly one
  production module: `engine/main.py:27`. That is the **crypto / Alpaca
  real-money** path.
- `engine/polymarket/shadow_loop.py` imports `engine.risk.constraints` and
  `engine.risk.events` only (lines 333-334). It never imports `RiskGate`.
- `PolymarketRiskGate` can consult an injected `ops_gate` (`risk_gate.py:790`),
  and that IS an `engine.risk.RiskGate` - but `shadow_loop.py:1453` constructs
  `PolymarketRiskGate(config)` with **no `ops_gate`**. Grep confirms `ops_gate=`
  is passed nowhere in production code, only in `tests/`.
- `config.yaml:102` sits under the **top-level `risk:`** block, which is what
  `engine.risk.RiskGate` reads (`config.get('risk', {})`). The Polymarket gate
  reads `config['polymarket']['risk']`. Different blocks, different consumers.

**Why that made me stop rather than proceed:**

1. **D-360 R3 is conditional and the condition is false.** R3 says
   `engine/risk/__init__.py` (2 -> sentinel **"if in shadow path"**). It is not
   in the shadow path. CLAUDE.md is explicit that DECISIONS.md beats a brief.
2. D-360 R1 and R2 say "in shadow mode" throughout. Aym's words were about a
   1k shadow account.
3. **The brief's own "What you do NOT do" says no changes to real-money
   defaults.** Raising the crypto gate's concurrent cap from **2 to 100,000** is
   precisely that, and it is the one change here with a real blast radius: unlike
   the paper book, that path can reach Alpaca.

This is the same shape as `cody-restart-now`'s 038 refusal, which was upheld.
**If Aym wants the crypto cap lifted too, say so and it is a two-line change.**
Until then the crypto path keeps `max_concurrent_positions: 2`.

---

## 4. Tests and harness

```
env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q --ignore=tests/test_dashboard_charts.py
  -> 4161 passed, 1 skipped, 0 failed, 397.90s
env -u PYTHONPATH .venv/bin/python backtest/validate_harness.py
  -> Harness-validity checks: 21/21 passed, rc 0
```

**4,161 is identical to the previous session's count**, as it should be: I edited
two tests in place and added none. Affected files run separately (risk gate, paper
adapter, maker fill wiring, risk, executor): **383 passed**, re-run again after the
ledger recovery below: **383 passed**. The two edited tests were confirmed to still
COLLECT by name (`-k`, 7 selected, 7 passed) - the disappearing-test check from the
standing corrections.

---

## 5. The commit needed a ledger recovery. Worth knowing.

The pre-commit `conflict-check` hook **REFUSED twice**, and the second refusal is
the informative one:

1. First refusal: no identity declared (`AGENT_ID` empty).
2. Second refusal, with `CONFLICT_CHECK_AGENT_ID=cody-D360-cap`: **hash MISMATCH
   on all 4 files.** `engine.concurrency`'s ledger still held the *pre-edit* hash
   for each path, owned by `cody-risk-wire` and `cody-paper-adapter` from earlier
   sessions. **The Edit tool does not go through `engine.concurrency`**, so a
   plain edit leaves the ledger stale and the hook cannot tell your work from a
   sweep.

Recovery, the documented one (it matches the coordinated-append note in memory):
capture the edited content in memory, `git checkout HEAD -- <paths>`, then
re-apply each through `concurrency.safe_write(..., agent_id='cody-D360-cap')` so
the ledger records a genuine old -> new transition under my identity.

**The recovery verified itself:** the post-`checkout` hashes came back as
`14e00cd / ae7f953 / e83e53f / 91c2d42` - byte-identical to the four
"expected, last coordinated write" hashes the hook had printed - and the rewritten
hashes came back as `e7616b7 / a0c9a5b / 10e97e7 / b82d154`, byte-identical to the
content I had captured and to the staged blobs the hook had objected to. Nothing
was lost or altered. Tests re-run green afterwards.

The throwaway script lived at `scripts/_cody_d360_relink.py` and **was deleted**
before the commit. It is not in the tree and not in the commit.

Then: `conflict-check: OK. 4 verified, 4 own-work, 0 FOREIGN-OWNED` and
`Agent-Id: cody-D360-cap matches the resolved identity`.

**Generalisable:** any session editing tracked code with the plain editor will hit
this. Budget for it.

---

## 6. Explicitly NOT touched

- **The loops.** main **11872** and env B **11895**, both up since 11:41, not
  restarted / signalled / inspected beyond `ps`. **They still hold the count cap
  of 10** - the change is inert until the second restart. That is correct and
  expected (D-360 R4).
- `engine/risk/constraints.py` - **untouched**. Per-trade notional, per-event and
  aggregate notional stay. They are now the ONLY cap (D-360 R2, D-361 R5).
- D-359's real-money `max_drawdown_frac=0.25` at `constraints.py:242` - untouched.
- `docs/DECISIONS.md`, `docs/CONVENTIONS.md`, `docs/keying-prep/`, the restart
  handoff - untouched.
- **Every one of tick7's dirty files** - the 2 modified and 9 untracked paths under
  `strategies/proposals/` and `scripts/`, plus `run_polymarket_shadow_envb.sh`
  which tick7 created *during* my session. Not staged, not committed, not cleaned.
- **No 038 backfill** (see below), **no orphan sweep**, **no restart**.
- Strategy-internal `MAX_CONCURRENT_POSITIONS = 2` self-limits in
  `fair_value_settlement_exit.py`, `fair_value_mirror_fade.py`,
  `longshot_fade_hold_to_resolution.py` - **untouched**, see open item 1.
- `DEFAULT_MAX_RESTING_MAKER_ORDERS = 2` in `shadow_loop.py:622` - **untouched**,
  see open item 2.

---

## 7. Open for Raven and Aym

1. **Three strategies still cap THEMSELVES at 2 positions.**
   `fair_value_settlement_exit`, `fair_value_mirror_fade` and
   `longshot_fade_hold_to_resolution` each hold a module-level
   `MAX_CONCURRENT_POSITIONS = 2` enforced as `len(self._open) >= ...` inside the
   strategy. D-360 R3 named three files and none of them is a strategy, so I left
   them. **But Aym said "I don't want a position cap", and those three strategies
   will still refuse their third concurrent position after the second restart.**
   If the intent was every cap, this is a fourth change. **Needs a ruling.** Their
   docstrings also still describe the system cap as "5 slots system-wide", stale
   since D-321 and now doubly so.
2. **`DEFAULT_MAX_RESTING_MAKER_ORDERS = 2`** (`shadow_loop.py:622`) is a maker/taker
   fairness budget, not the position cap, so I left it. Its justifying comment is
   now obsolete: it exists because "the first cycle that quotes fills all 5 slots
   and every one of the 17 taker strategies is refused `max_concurrent_positions`
   for the rest of the session". With no count cap that starvation is impossible.
   The budget is now doing something nobody has decided it should do.
3. **`engine/risk/__init__.py:49` + `config.yaml:102`** - section 3. One word.
4. **D-361 landed mid-session and re-scopes the second restart.** R1 **approves the
   038 backfill** (this satisfies the D-354 R4 Aym-gate that `cody-restart-now`
   escalated and that my brief still listed as forbidden - the brief predates the
   ruling by minutes). R2 approves the fair_value isolation split. **R4 says D-360
   + D-359 + split membership + 038 backfill all ride the second restart.** I did
   not run the backfill: my brief forbids it and D-361 R4 sequences it with the
   restart, which is not this session. **The second-restart brief must carry it.**
5. **The second restart is the next queued item.** After it: D-360 R4 satisfied,
   and **D-361 R3 requires re-measuring the split** (the `PM_fair_value_arb_patient`
   +3.14-on-3 vs -388.57-on-887 table moves once the cap lifts).
6. Carried forward, still unowned: env B's frozen `market_tape`
   (`1787124461.656716`), the 14 venue-vs-inference direction disagreements in
   main, **V6 re-run** (query in CLAUDE.md), 53+ orphans (D-353),
   `asset_family_for_slug` slug-parsing, `validate_harness.py` having zero
   Polymarket references, the disabled R-10 critic cron.

---

## 8. Safety read

After the second restart the shadow book has **no auto-halt** (D-359), **no count
cap** (D-360), and **no daily/portfolio loss breaker** (both `0.0` in config, by
design). The only remaining limits are per-trade notional, per-event and aggregate
capital in `engine/risk/constraints.py`. D-361 R5 records Aym accepting exactly
this. It is paper money. But it means **nothing stops a bleed except a human
reading equity**, and after the cap lifts the book can hold far more concurrent
positions than any prior session's data describes. The first hours after the
second restart deserve a real look, not a glance.
