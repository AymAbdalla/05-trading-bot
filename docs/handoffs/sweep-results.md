# Sweep results, 2026-08-13 late evening

**Written by:** Cody (session 2)
**Status of the batch:** 1 of 5 finished. The graveyard sweep is still running.

This is the summary requested for the five-output read. Only one of the five
is actually readable yet, so this covers that one honestly and records why the
other four are not available - plus a problem with the sweep in flight that
changes how its output should be treated.

---

## Where the batch actually stands

| # | Output | State |
|---|---|---|
| 1 | P0.3 graveyard control run | **RUNNING** - PID 63767, started 16:01, ~60% done (114 of 191 tickers, at PLTR). ETA ~4h. |
| 2 | Constraint sweep | **DONE** - `research/graveyard/constraint_sweep.json`. Read below. |
| 3 | Dispersion gate (full) | Not started. Queued behind #1. |
| 4 | Horizon ladder | Not started. Queued behind #1. |
| 5 | PLR | Not started. Queued behind #1. |

`run_queued_chain.sh` (PID 69639) is alive and polling every 300s for the
sweep to exit, then runs 3/4/5 in order. Nothing about the chain is stuck.

A note for whoever checks next: `ps aux | grep python` **misses the sweep**.
The interpreter is `/Library/.../MacOS/Python` with a capital P. Use
`pgrep -f run_incremental_graveyard` or `grep -i python`. The first check this
session wrongly concluded the sweep had died.

---

## The sweep in flight is running stale code (read this before using its output)

Python snapshots source at import. The sweep started at 16:01; these landed
after and are therefore **not** in the running process:

| File | Modified | Consequence |
|---|---|---|
| `strategy_lab_v4.py` | 16:19 | v4's 3 strategies absent |
| `strategy_lab_v5.py` | 16:45 | v5's 2 strategies absent |
| `run_incremental_graveyard.py` | 16:47 | runner's own edits absent |
| `vectorized_harness.py` | 17:44 | absent |
| `cost_model.py` | **17:45** | **D-249 contract-sizing fix absent** |

Confirmed rather than assumed: the log says "49 new strategies" per series and
the graveyard header says `strategies_tested: 49` = 28 + 7 + 9 + 5, with v4/v5
missing. Today's total is 54.

**What that costs.** Only FUTURES/OPTIONS rows are affected by D-249 -
`InstrumentSpec.is_contract` is true for those two alone, so EQUITY, ETF and
CRYPTO never touch the contract-sizing path. Current split:

```
EQUITY 242,011 | ETF 24,255 | FUTURES 12,936 | CRYPTO 8,624   (287,826 total)
```

So ~96% of the run is good work and 12,936 rows need re-running. The sweep was
**left running** on that basis - killing it would discard the good 96% to fix a
bucket that has to be rebuilt either way (D-253).

**The silent part.** The D-249 fix shipped without a `COST_MODEL_VERSION` bump.
Every row, pre-fix and post-fix, carries `'2026-08-13'`. The project's "never
pool across cost_model_version" rule cannot see this contamination, and neither
can anything else in the row metadata.

Remedy is built and armed but **not run**: `backtest/purge_stale_futures.py`
drops every contract row so the incremental runner rebuilds them under current
code. It refuses to run while the sweep is alive (the runner rewrites the whole
graveyard after each ticker and would clobber a purge on its next save). Dry
run today reports 12,936 rows to purge, 274,890 to keep - including a flag that
**51 of the purged rows are PASS/PASS_BENCHMARK**, which is expected: inflated
sizing is exactly what would manufacture a passing futures row.

---

## Result 2: constraint sweep (the one finished output)

44 strategies x 14 series x 3 exits x 3 constraint levels. Levels differ on
confirmation stack, regime-uptrend requirement, `rsi_max_entry` (100/70/45) and
`volume_min_ratio` (0.0/1.2/2.0).

```
level             trades         pnl   pnl/trade   strategies firing
AGGRESSIVE       836,072 -149,941.31     -0.1793                  43
BASE             194,595  -88,410.72     -0.4543                  42
CONSERVATIVE       5,045    7,759.32     +1.5380                  24
```

The script's own diagnostic reads: *"Tightening the gate IMPROVES per-trade PnL
by +1.7174. The confirmation stack is selecting for something real."*

**That claim does not hold up.** Two reasons, both from the same JSON.

### 1. It is not monotonic

AGGRESSIVE (-0.1793) is *better* than BASE (-0.4543). Tightening the first
notch makes things worse; only the second notch turns positive. A real
selectivity effect reads AGGR < BASE < CONS. This is U-shaped, and the
headline +1.7174 is just the two endpoints subtracted with the middle ignored.

### 2. It is two strategies, on 282 trades

| strategy | trades | pnl | $/trade | % of CONSERVATIVE profit |
|---|---|---|---|---|
| dca_7 | 195 | 3,232 | 16.57 | 41.6% |
| dca_14 | 87 | 2,860 | 32.87 | 36.9% |
| grid_2.0atr | 1,107 | 2,506 | 2.26 | 32.3% |
| V2_expiry_pin | 15 | 893 | 59.51 | 11.5% |
| bollinger_reversion | 393 | 477 | 1.21 | 6.1% |

dca_7 + dca_14 are **5.6% of CONSERVATIVE's trades and 78.5% of its profit**.
Remove them and per-trade PnL collapses from +1.5380 to **+0.3502**. The tail
below them is noise by convention 7 - V2_expiry_pin is 15 trades, rsi_extreme
is 3.

CONSERVATIVE fires 165.7x less than AGGRESSIVE, and the surviving bucket is
thin enough that two DCA variants set its sign.

### Verdict

Recorded as **NOT SUPPORTED, not disproven** (D-256). The sweep is underpowered
at the conservative end - that is a statement about the experiment, not about
selectivity. `2026-08-13-constraint-sensitivity-PRELIMINARY.md` stays
PRELIMINARY.

If this is worth resolving, the fix is more conservative-level trades (more
series, longer windows), not more strategies - and DCA should be looked at on
its own, since a dollar-cost-averaging rule surviving a tight entry gate on 87
trades is a structurally different claim from a signal having edge.

---

## judge.py

Run against the live graveyard. `validate_harness` is green (21/21, status
DURABLE), but the run surfaced a bug in judge.py itself rather than a result:
it reported **`status: DURABLE, entries: 0`** on a 287k-entry graveyard.

Cause: `load_graveyard` swallowed `json.JSONDecodeError` and returned `[]`, so
a read landing mid-write became "no evidence" with a DURABLE stamp on it. Fixed
(D-255) - it now retries, then raises `GraveyardUnreadable`, and the pack comes
back `status: UNREADABLE`, which no green harness can upgrade. 543 passed,
1 skipped, 544 collected.

`research/judge_evidence_pack.json` on disk is the **pre-fix empty pack** -
ignore it. Re-run judge after the purge and rebuild; that is when its output is
worth reading.

---

## What to do next, in order

1. Wait for PID 63767 to exit (~4h), then let the chain run 3/4/5.
2. `python3 backtest/purge_stale_futures.py` (dry run), then `--apply`.
3. `python3 backtest/run_incremental_graveyard.py` - rebuilds the purged
   futures rows *and* backfills v4/v5 under current code, in one pass.
4. Re-run `agents/judge.py` for a real evidence pack.
5. Then read all five outputs together, as designed.

Use `env -u PYTHONPATH python3` if running from an agent-spawned session
(D-257) - otherwise numpy fails to import for reasons that have nothing to do
with this project.
