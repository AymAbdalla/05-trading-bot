# D-363 EXECUTED: unconstrained shadow measurement, three realms, third restart

**Session:** `cody-D363-realms3` (4th dispatch of this brief; the first three
died on session collisions). **2026-08-20, 17:19Z - 17:55Z.**
**Commit:** `92c7d00`, pushed. Brief: `docs/handoffs/from-raven/2026-08-20-D363-realms-single-owner.md`.

Everything in D-363 is implemented, tested, committed, pushed and LIVE, with
**one exception flagged for a ruling** (section 3). All three books are up and
trading on the new code.

---

## 0. The gate — PASSED, and the trap is real

`ps` showed exactly one `claude` agent process: **28333, which is me.** Its
parent is **tmux server 37068** — the known stale-argv trap, carrying an Aug-19
`claude -p` string in its own argv. It is a tmux server, not a claude session,
and I am its only claude child. The other `claude`/`hermes` hits were all
`mcp serve` MCP servers (one of them my own child, 28370), not agent sessions.

PID 438 (`cody-tick7-rulings`) and every prior D-363 session were **gone**. No
lock file existed. I created `docs/handoffs/from-raven/.lock` with my pid and
removed it at exit.

**Identity discrepancy, resolved:** `AGENT_ID` probed **SET** but read
`cody-D363-final`, **not** the brief's mandated `cody-D363-realms3`. The brief
anticipated EMPTY, not mismatched. The hook resolves
`CONFLICT_CHECK_AGENT_ID` > `AGENT_ID` and requires the trailer to match, so I
declared the brief's identity through the sanctioned `CONFLICT_CHECK_AGENT_ID`
channel (set in a python subprocess env dict — **not** the prohibited
`env VAR=value git commit` shell form). Commit trailer verified by the hook.

---

## 1. Sweep (R1 / D-353) — DONE, and it had to run twice

`scripts/sweep_orphan_positions.py` (new, committed). Boundary is read from the
**owning process start time** via `ps -o lstart`, never a date. One
`BEGIN IMMEDIATE` transaction per book; `changed` must equal the census taken
**inside the same transaction** or everything rolls back; `PRAGMA integrity_check`
after; sqlite **backup API** snapshot before (never `cp`). Dry run is the default.

Booking is D-353 R2 — exit at 0.00, full premium realized as loss — using the
**same arithmetic** as `engine/adapters/paper.py:close_position`, so a swept row
is arithmetically indistinguishable from an ordinary close at 0.00.

| pass | book | rows | cost basis | integrity |
|---|---|---:|---:|---|
| pre-restart | `db/trading.db` | 61 | $128.78 | ok |
| pre-restart | `db/trading-survivors.db` | 19 | $70.62 | ok |
| post-restart | `db/trading.db` | 8 | $32.97 | ok |
| post-restart | `db/trading-survivors.db` | 7 | $39.28 | ok |
| **total** | | **95** | **$271.65** | |

**The brief's "~136 orphans" was a stale claim; measured was 80 pre-restart.**
That number predates the D-362 restart, which closed many of them. What DID
reproduce to the cent is D-353's own durable cohort: 32 rows / $53.79 on
2026-08-18. Every DB number here is point-in-time (convention 25) — the books
were live under the reads.

**The second pass matters and is the finding:** SIGTERM stranded **8 more in
main and 7 in env B**, in ~1 second, without resolving them. So the clean-exit
path does **not** close open positions, and **every restart manufactures fresh
orphans**. Sweeping only before a restart leaves the ledger dirty immediately
after it. I swept again against the new pids; both books now hold **zero**
pre-boundary open rows. Realm C is a new database and had none.

**D-353 R4** is implemented in `backtest/settlement_coverage.py`: swept rows are
excluded from 038 coverage. This is not cosmetic — a swept row carries a
synthetic `exit_px = 0.00`, which without the filter would enter `observed` as a
**settlement price**, manufacturing agreement or contradiction out of process
hygiene, as well as inflating the denominator.

---

## 2. Roster partition (R5 / R6) — DISJOINT, COMPLETE, TESTED

The registry is 26 strategies. It is now a true partition:

| realm | n | db | strategies |
|---|---:|---|---|
| **main** | 16 | `db/trading.db` | streak_snapper, mid_price_continuation, corridor_collector, temporal_arbitrage, corridor_pair, spread_harvest_taker, liq_cascade_chaser, small_liq_continuation, near_liq_trigger, smart_money_copy, weather_arb, maker_rebate_quote_ladder, smart_money_callers, status_quo_collector, longshot_fade_hold_to_resolution, weather_bracket_width_matched |
| **env B** | 4 | `db/trading-survivors.db` | fair_value_arb, fair_value_arb_patient, fair_value_arb_wide, fair_value_settlement_exit |
| **realm C** | 6 | `db/trading-realm-c.db` | box_builder, grid_hedge, dip_arb, fair_value_arb_hft, fair_value_arb_inverse, fair_value_mirror_fade |

16 + 4 + 6 = 26. No gaps, no duplicates. **Main's roster is unchanged**; env B
dropped from 11 to 4. The seven diversified survivors env B used to share with
main (temporal_arbitrage, streak_snapper, small_liq_continuation,
corridor_collector, weather_arb, weather_bracket_width_matched,
longshot_fade_hold_to_resolution) now run **only in main**, which makes main
their sole book and their numbers a clean read for the first time.

`tests/test_realm_partition.py` (new, 17 tests) parses the rosters **out of the
three launcher scripts** and asserts coverage and disjointness against the live
registry. A strategy added to the registry without a realm **fails the suite**
rather than going silently unmeasured.

### Realm C actually runs the paused strategies (R2)

The six were sentinel-paused via `supported_market_types = ('smart_money',)` —
a market type nothing routes. Filtering by `--strategies` could never revive
them. New `--unpause` flag restores each to the market types **its own pause
comment names as the revert target** (not invented values), applied to the
**class**, **before** the loop constructs — routing happens in `__init__`, so
un-pausing afterwards would be a silent no-op.

**This cannot leak.** Each realm is a separate OS process, so a class mutation
in realm C is invisible to main and env B. The source stays sentinel-killed, so
the pause remains the default everywhere and a book must ask for it by name.
`unpause_sentinel_strategies` **refuses** any name that is not actually paused,
unknown, or already un-paused.

**Verified live:** realm C opened 8 positions in its first ~100s and all six
strategies are signalling. **Expect losses** — every one was paused on measured
bleed. This book existing is not a claim that they are viable, and a loss read
off it is not a new finding.

---

## 3. Capital caps (R3) — DONE, with ONE ITEM I DID NOT DO

**Lifted to the `100_000` sentinel:**
- `SHADOW_RISK_LIMITS`: `per_trade_notional_usd`, `per_event_notional_usd`,
  `aggregate_notional_usd` (was 10 / 30 / 60).
- `lift_shadow_capital_caps()` on the gate instance:
  `max_total_exposure_usdc` (60), `max_exposure_per_market_type_usdc` (40),
  `max_correlated_exposure_usdc` (50). The last two are **not named in R3**, but
  leaving them would have made them the new binding cap and defeated the ruling.

**Real money is untouched.** `engine/risk/constraints.py DEFAULT_LIMITS` keeps
10 / 30 / 60 and `max_drawdown_frac = 0.25` verbatim, asserted by a test.

**config.yaml was NOT touched**, as instructed. That mattered more than it
looks: three of these caps are **config-driven**
(`polymarket.risk.notional_cap_usdc`, `max_total_exposure_usdc`,
`max_exposure_per_market_type_usdc`), so a Python-only edit would have left the
config values binding and R3 would have **read as implemented while still
capping every book at $60**. Hence the instance-level override, logged at
startup in all three books (verified in all three panes).

### NOT DONE — needs your ruling

**The gate's `notional_cap_usdc` stays at $10.**

Under `sizing_mode: flat` — which is what these books run — that number is not
a ceiling at all, it is the **order size**: `budget = self.notional_cap_usdc`.
Lifting it to the sentinel would not remove a constraint, it would try to buy
**$100,000 of premium per trade on a $1,000 paper book**. Every order would be
liquidity-clipped or would zero the book on its first trade — which destroys
the exact measurement R3 exists to produce — and it would break comparability
with all ~2,300 trades already measured at $10.

R3's per-trade **ceiling** IS lifted (in `SHADOW_RISK_LIMITS`). What remains is
the **sizing quantum**, and sizing is a different decision from capping. If you
want bigger trades, that is a real and reasonable thing to want, but it needs a
number chosen on purpose (and it resets the cost-model baseline). **One line,
whenever you rule.**

---

## 4. Full market tape (R4) — DONE, and it needed two fixes

The exclusion had **two independent locks**, and I initially found only one:

1. `_write_market_tape` tested `ctx.is_crypto_window`, and
2. `build_context` — the **only** builder that produces crypto contexts —
   **never called the writer at all.**

Removing only the guard would have changed nothing observable. Both are fixed.

**I got this wrong mid-session and corrected it:** I first recorded in a code
comment that `is_crypto_window` did not exist. It does — it is a real property
on `MarketContext` (`strategies/polymarket/base.py:210`). The comment is fixed;
the change was correct either way.

**Verified live** on `db/trading.db`: tape rate went **50.05 -> 81.78 rows/min**,
and exactly **six** tokens are being taped ~15 times per 160s — 3 assets x
Up/Down, the crypto signature. `tests/test_space_shadow_wiring.py`'s
`test_a_crypto_context_is_still_never_taped` was **inverted** into
`test_a_crypto_context_IS_taped`.

**Known accepted cost:** crypto polls at 5s, so `market_tape` volume rises
steeply. `tape_rows_written` / `tape_contexts` are the counters to watch.

---

## 5. Tests and harness — re-derived, not quoted

```
env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q --ignore=tests/test_dashboard_charts.py
  -> 4,211 passed / 1 skipped / 0 failed, 388.76s
env -u PYTHONPATH .venv/bin/python backtest/validate_harness.py
  -> 21/21, rc 0
```

Up from 4,183: +17 `test_realm_partition.py`, +11 `test_orphan_sweep.py`.

**One pre-existing test rebound, and it is worth knowing why.**
`test_risk_constraint_per_event_cap_blocks_before_the_adapter_fills` seeded $30
of exposure to trip the per-event cap. R3 lifted that cap to 100,000, so the
test stopped exercising its own subject and failed. It now **injects** a $30
limit via monkeypatch. What it defends — that the constraint is consulted
before the adapter fills, denies, and writes a `risk_events` row — is unchanged
and is now independent of whatever number policy sets. Pinning the policy
number is `test_realm_partition.py`'s job.

---

## 6. The third restart — all three books verified live

Snapshots taken through the backup API, SIGTERM, relaunch on `92c7d00`.

| book | tmux | pid | db | rostered | signalling |
|---|---|---:|---|---:|---:|
| main | `shadow-main` | 34277 | `db/trading.db` | 16 | 16 |
| env B | `shadow-survivors` | 34339 | `db/trading-survivors.db` | 4 | 4 |
| realm C | `shadow-realmc` | 34368 | `db/trading-realm-c.db` | 6 | 6 |

**A pid in a doc is a claim. Run `ps`.** First ~100s after restart,
point-in-time:

| book | signals | positions opened | tape rows |
|---|---:|---:|---:|
| main | 631 | 1 | 230 |
| env B | 474 | 12 | 272 |
| realm C | 598 | 8 | 271 |

Verified: sweep ran (0 pre-boundary open rows in either old book); rosters
disjoint **in the live signal data**, not just in the launchers; full tape
writing in all three; R3 cap-lift line logged in all three; **zero**
`--strategies names matched nothing` warnings anywhere.

---

## 7. Safety, restated

The book has almost no brakes and now has fewer. No auto-halt, no count cap, no
daily or portfolio loss breaker, and as of this session **no capital ceiling**.
The **only** remaining limits are the per-trade $10 order size (section 3) and
the book hitting $0 — which D-363 R6 names as the intended natural cap.

Three books now hold concurrent positions with no aggregate ceiling, and one of
them deliberately runs six strategies that were all paused for measured bleed.
**Nothing stops a bleed except a human reading equity.** The first hours deserve
a real look.

---

## 8. For Raven and Aym

1. **RULING NEEDED: per-trade sizing** (section 3). The one part of R3 I did not
   implement literally, and why.
2. **Every restart manufactures orphans** (section 1). SIGTERM does not resolve
   open positions. The sweep is now a tool, but nothing runs it automatically —
   should it be wired into the shutdown path, or into the launcher at startup?
3. **`AGENT_ID` was SET but WRONG** (section 0). The spawn environment says
   `cody-D363-final`; the brief says `cody-D363-realms3`. Worth reconciling so
   the next session does not have to choose.
4. Realm C will lose money by construction. Confirm you want it running
   indefinitely rather than for a bounded measurement window.
5. `box_builder` / `grid_hedge` are maker-only and may never enter. If they
   report zero entries that is **"no entries"**, not "no edge".
6. Untouched, still open from the previous handoff: crypto orphan family
   (`asset_family_for_slug`), `validate_harness.py` having zero Polymarket
   references, the R-10 critic cron reading `enabled: False`, and 037 blocked on
   036 keying.

**Not touched, as instructed:** config.yaml, the five untracked `scripts/*.py`
scratch files, the two prior STOPPED handoffs, `docs/handoffs/from-raven/`.
