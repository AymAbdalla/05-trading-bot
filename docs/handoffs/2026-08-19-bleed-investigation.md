# Main-loop equity bleed: investigated. Not a regime change.

**Session:** `cody-bleed-investigation`, 2026-08-19, 16:41-16:50 EDT (measured
with `date`). **Brief:** `docs/handoffs/from-raven/2026-08-19-bleed-investigation-and-doc-fixes.md`.
**HEAD:** `76f2269`, unchanged - no tracked file touched this session.
**Analysis only. No loop was killed, signalled or restarted. `config.yaml`
untouched. No strategy code or params changed.**

Every number below is point-in-time and the DB is LIVE under the reads
(convention 25). The realized-loss figure moved **-57.31 -> -46.26 -> -49.90**
across three reads six minutes apart, purely from settlements landing. Quote a
figure with its clock time or not at all.

---

## Headline: the bleed is REAL but it is NOT NEW, and the alarm number is inflated

The brief reported 1000.00 -> 933.57 in 26 minutes (-6.6%) and called it "a
faster bleed than the previous full day". Both halves of that comparison need
correcting.

### 1. `equity` writes every open position to ZERO. It is a lower bound, not a mark.

`shadow_loop.py:4082-4096` `snapshot_equity` is explicit, and its docstring
says so in terms:

> `equity` and `cash` are the same number by construction and that is not a
> bug: the adapter holds OPEN positions at ZERO rather than marking them to a
> 5-minute book that is mostly noise, so uninvested cash IS the whole of
> measured account value and **equity can only ever surprise upward**.

So `equity == cash` on every row is by construction, not evidence. The premium
in open positions is already fully written off; `open_risk` carries it. At the
16:32 reading the honest bracket was **[933.57, 961.40]** (equity, equity +
open_risk = 27.83), i.e. **[-6.6%, -3.9%]**, not a point estimate of -6.6%.
At 20:42:53 UTC: equity **911.39**, open_risk **31.30**, bracket
**[911.39, 942.69]**.

**This is not a defect and I am not proposing a change to it.** But an alarm
raised off the low edge of that bracket is reading the metric as if it marked
the book, and it does not.

### 2. Against the loop's OWN prior day, this window is an ordinary bad bucket.

I bucketed the pre-restart day (03:28-16:17 UTC) into 35-minute buckets keyed
on `closed_ts` - the same cut and the same width as the post-restart window -
and asked where the post-restart window falls.

| metric | post-restart window | pre-restart median | pre-restart buckets WORSE |
|---|---|---|---|
| pnl_net | **-46.26** | -17.15 | **4 / 22** |
| per notional | **-0.1441** | -0.0808 | **6 / 22** |
| per share | **-0.0410** | -0.0232 | **6 / 22** |

Pre-restart bucket mean **-19.88**, sd **35.29**, so the post-restart window is
**z = -0.75**. One-sided p is **0.18 to 0.27** depending on the metric. The
worst pre-restart bucket was **-104.46**; four buckets were worse than this one
on raw P&L.

**Conclusion: NOT_TESTED as a regime change - and on the evidence available,
not even suggestive of one.** This is the same bleed the book has been running
all day, sampled over an unlucky 35 minutes and then annualised. Extrapolating
a single 35-minute bucket to a daily rate is what produced the "faster than the
previous full day" reading; the day itself contains buckets twice as bad.

---

## Where the loss actually is

Closes in window (n=73 at the last read, -49.90). Concentrated in two
strategies, which between them account for **more than 100%** of the loss -
everything else nets positive:

| strategy | n | pnl_net |
|---|---|---|
| `PM_fair_value_settlement_exit` | 16 | **-35.99** |
| `PM_fair_value_arb` | 33 | **-13.69** |
| `PM_corridor_collector` | 2 | -4.15 |
| `PM_maker_rebate_quote_ladder` | 5 | -3.65 |
| `PM_corridor_pair` | 3 | -0.70 |
| `PM_temporal_arbitrage` | 5 | +0.20 |
| `PM_dip_arb` | 5 | **+11.73** |

### The engine is `sell:salvage_floor`, and it is identical in BOTH books

| book | n | entry (mean) | exit (mean) | pnl | shares |
|---|---|---|---|---|---|
| MAIN `settlement_exit` salvage_floor | 10 | 0.2987 | **0.0616** | -42.19 | 189 |
| SURVIVORS `settlement_exit` salvage_floor | 17 | 0.3156 | **0.0631** | -78.92 | 324 |

Entry ~0.30, exit ~0.06: the floor realizes about **-79% of premium**. The two
books are separate processes on separate DBs, one of them (survivors) never
restarted and still on its pre-wiring snapshot - and the shape matches to two
decimal places. **This is a strategy-design property, not anything the restart
or the new code did.**

`PM_fair_value_arb` is the same asymmetry in milder form: 14 `sell:price_stop`
at 0.3587 -> 0.1991 for **-44.03**, against 17 `sell:profit_target` at 0.3017
-> 0.4021 for **+33.46**. Losses run further than wins. Net -13.69 on 33.

### The settled subset is nearly flat - and I am NOT drawing the obvious conclusion

| subset | n | pnl | per share |
|---|---|---|---|
| SETTLED (`exit_px` in 0.00/1.00) | 22 | **-2.70** | -0.0126 |
| EARLY (stops, salvage) | 47 | **-43.55** | -0.0477 |

It is tempting to read this as "the early-exit machinery manufactures the loss
and the positions would have recovered". **That read is the exact
selection-bias trap `CLAUDE.md` already warns about under proposal 041**: a
position reaches settlement only by not having hit its stop, so the settled
subset is what remains *after* a rule that removes losers has removed them. The
settled subset cannot acquit the stops. I am recording the split as a
measurement and explicitly declining the inference. The defensible claim stays
the bucket test in section 2.

---

## Brief task 3: the risk wiring is ACTIVE and has FIRED. `CLAUDE.md` was stale.

`CLAUDE.md` said "`risk_events` holds **0** `risk_constraint` rows so far".
**That is now false: 5 rows**, all written this window, all
`constraint: per_event_notional`, all on `sol-updown-5m-1787172000`, blocking
candidate notional of 6.40 to 8.64 USD against `limit_usd: 30.0` with
`event_open_usd: 27.57`. Matches the log counter `risk_constraint:event` = 5
exactly. **The D-343 delegation is not merely loaded, it is binding.** I have
corrected that line in `CLAUDE.md`.

Effect on the book: **marginal.** 5 blocks against 64 entries, all on one
event, all small. It did not cause and did not prevent the window's loss.

### The loop IS at the concurrent-position ceiling. Confirmed twice, independently.

Final cumulative disposition counters (last cycle line, 16:42:46):

- `strategy:max_trades_this_window` **1355** (by far the dominant binder)
- `strategy:not_final_third_of_15m` 1051
- `risk_gate:max_positions_per_market_side` **240**
- `risk_gate:max_concurrent_positions` **142**
- `entry` 64, `risk_constraint:event` 5

The brief's **142 reproduces exactly.** Note these lines are **cumulative
counters reprinted every cycle**, not per-cycle events - `grep -c` on the log
returns 44 (the number of cycle lines) and that is a different quantity. Anyone
diffing the two will think they disagree; they do not.

Independent confirmation of the ceiling: the gate reads `10 open (limit: 10)`,
and the DB holds **exactly 10** positions opened since the restart with
`closed_ts IS NULL`. The two agree.

**Does the ceiling change book composition?** Yes, mechanically: at a saturated
ceiling, entry becomes first-come-first-served among surviving candidates
rather than best-edge-first. But it is **not the dominant binder** -
`max_trades_this_window` blocks about 10x more often, and `CLAUDE.md` already
names that as the binding gate (D-327).

---

## GENUINE DEFECT FOUND: positions are orphaned at every process death

**Not implemented, not fixed. Reporting with evidence, per the brief.**

`risk_gate` counts open positions from `self.adapter.open_positions()`
(`shadow_loop.py:2466,3160,4293`) - **in-memory adapter state**, which starts
empty on a fresh process. That is correct for the gate: a new process should
not be capped by positions it cannot manage.

The consequence is elsewhere. `db/trading.db` holds **62** rows with
`closed_ts IS NULL`. Only **10** are genuinely live. The other **52 are
orphans** from earlier process deaths, cost basis **109.36 USD**:

| day opened | orphans | cost basis |
|---|---|---|
| 2026-08-18 | 32 | 53.79 |
| 2026-08-19 | 20 | 55.57 |

These rows will **never** be closed. Nothing sweeps them. Two consequences:

1. **Any analysis keying on `closed_ts IS NULL` to mean "currently open"
   over-counts by 52** (62 vs 10, a 6.2x error). I nearly made this mistake
   myself before cross-checking against the gate.
2. **Every lifetime `sum(pnl_net)` silently excludes them.** Their premium is a
   real sunk cost that never appears in any P&L total. The true lifetime loss
   is understated by **up to 109.36 USD** relative to what `pnl_net` sums show,
   and this accrues at **every** restart. 52 of 2,921 positions (1.8%) are
   already affected, over just two days.

**Proposed fix, NOT implemented, for Raven and Aym to rule on:** on startup,
mark pre-existing `closed_ts IS NULL` rows from prior processes with an
explicit terminal `exit_reason` (e.g. `orphaned:process_death`) and a
`closed_ts`, rather than leaving them indistinguishable from live positions.
Whether their P&L should be booked at 0.00, at entry price, or resolved via the
038 ledger is a **separate ruling I am not making** - the 038 ledger is the
obvious instrument, and this is the strongest new argument for running that
backfill eventually, though not now (see below). **This touches the entry path
and must NOT be bolted onto the ~03:45 2026-08-20 restart, which is already
fully loaded.**

---

## Brief task 4: survivors comparison - and a correction to my own first read

Over the identical wall-clock window, keyed on `closed_ts`:

| book | n | pnl | per notional | per share |
|---|---|---|---|---|
| MAIN | 69 | **-46.26** | -0.1441 | -0.0410 |
| SURVIVORS | 54 | **-1.89** | -0.0064 | -0.0020 |

**Correction, recorded because it changes the conclusion:** my first pass cut
both books on `opened_ts >= T0` and got MAIN -46.26 vs SURVIVORS -37.78, which
looked like near-identical bleed and would have supported "it is the market
window, not the code". **That cut is wrong for survivors.** The main loop reset
to 1000.00 at restart, so `opened_ts` and `closed_ts` select nearly the same
rows there; survivors never restarted, so an `opened_ts` filter drops its
pre-window positions that closed inside the window. On the correct cut the two
books **diverge sharply** and the "identical bleed" reading does not hold.

The divergence is **composition, not decay**. Survivors runs a 3-strategy
whitelist and its variants differ:

| strategy | MAIN | SURVIVORS |
|---|---|---|
| `settlement_exit` | -35.99 (n=16) | -11.97 (n=30) |
| `fair_value_arb` / `_patient` | -13.69 (n=33) | **+4.93** (n=18) |
| `temporal_arbitrage` | +0.20 (n=5) | +5.15 (n=6) |

Within `settlement_exit` the salvage_floor mechanics are identical (table
above); survivors simply caught **8 `target` wins (+84.35)** to main's **1
(+13.00)**. That is **1/16 vs 8/30 on the winner side - small-n outcome
variance, not a mechanism difference**, and I am not treating it as one.
Survivors equity over the window: 443.998 -> **449.736**, i.e. **+5.74**.

**Convention 13 still governs survivors** - it is on its pre-wiring snapshot
and its results must never be crossed with the main loop's. The table above is
a side-by-side, not a pooled figure.

---

## Brief task 3 (Raven ruling recorded): 038 backfill STAYS DEFERRED

Recording as instructed so the ~03:45 cron session does not pick it up early.

**Open item 14 is live but MUST NOT be run yet.** `market_resolutions` exists
and is writing (**44 rows** at 20:42 UTC, up from the 24 the brief cites).
Reasons it stays deferred: 026 and 037 are mid-measurement, and the backfill
would write into a WAL file the shadow loop holds open. **Earliest safe window
is after 037's 24h+ re-derivation (~03:28 EDT 2026-08-20) AND the ~03:45
restart.** Not this session, and **not by the cron session either.**

---

## What I did NOT do

- Did not kill, restart, signal or touch either loop. Both alive at session
  end: **52733** (main, since 16:06:30) and **71442/71444** (survivors, since
  03:28:40), re-verified by `ps`.
- Did not edit `config.yaml`, strategy code, or any param.
- Did not run the 038 backfill.
- Did not touch `DECISIONS.md` or `CONVENTIONS.md`.
- Did not implement the orphan fix - described only, per the brief.
- **No tracked file was modified. HEAD is still `76f2269` and the tree is
  clean.** Suite and harness NOT re-run and did not need to be: zero importable
  files touched. The `2e1184a` baseline (4,085 passed / 1 skipped / 0 failed;
  harness 21/21) is inherited unchanged.

## CLAUDE.md fixes applied (brief task 2, both via `safe_edit`)

1. **Write-tool claim corrected.** Was "WORKED in two sessions running". Now
   **2 sessions WORKED, 2 REFUSED** - `cody-record-rulings` and this session
   were both refused. **This session probed it twice: refused on a repo-root
   `_scratch_*.py`, then refused again on this handoff's `docs/` path** before
   I fell back. So the refusal is **not path-specific**, which is a stronger
   statement than the brief had. Every write this session went through
   `.venv/bin/python -c`.
2. **AGENT_ID tallies reconciled to one count.** The section now carries the
   sole authoritative figure, **5 SET against 8 EMPTY** (the prior 5/6 plus
   `cody-record-rulings` EMPTY plus this session EMPTY). Open item 10 no longer
   carries a second number and now points at the section, with a note that a
   duplicate tally is how the two drifted apart.
3. Also corrected, as a factual error found during the work: the
   "`risk_events` holds **0** `risk_constraint` rows" claim, now 5.

**This session read `AGENT_ID` EMPTY.** No commit was needed - nothing tracked
changed, and `CLAUDE.md` is gitignored, so there is nothing to commit for it
either.

## Residual for Raven

- **`CLAUDE.md` lines 82-83 are still self-contradictory** - "This session read
  **SET**... This session read **EMPTY**" is two different sessions layered
  into one paragraph. Out of scope for this brief (which named the tallies
  only), so left alone deliberately. Worth a one-line fix by whoever next
  rewrites that section.
- **The orphan-sweep defect needs a ruling** - both whether to sweep, and what
  P&L to book. It is the strongest new argument for running the 038 backfill
  once the window opens.
- Nothing here changes the ~03:45 2026-08-20 restart's payload. **Do not add
  the orphan fix to it.**
