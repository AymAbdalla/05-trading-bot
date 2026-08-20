# THE ONE RESTART: EXECUTED

**Session:** `cody-restart-now`, 2026-08-20. Restart at **11:41 EDT /
15:41 UTC**. Brief: `docs/handoffs/from-raven/2026-08-20-restart-now.md`.
HEAD `5d93e91` throughout (matched the brief - sixth correct HEAD claim in a
row, still re-derived per convention 25).

**The restart happened. All seven verifications that could run, passed.**
One (V6) is vacuous so far and must be re-run. One task (R7 backfill) was
REFUSED on a documented ruling conflict and is escalated to Aym below.

---

## 1. Restart log

| time (EDT) | event |
|---|---|
| 11:37:07 | session start, HEAD `5d93e91` re-derived, tree read |
| 11:39 | fresh snapshots written to `backups/2026-08-20-restart/` |
| 11:40:21 | `backtest/validate_harness.py` **21/21 rc 0** (convention 1 gate) |
| 11:40:5x | SIGTERM to main wrapper 52716 and env B python 73117 |
| 11:41:0x | both loops confirmed gone; their tmux sessions exited with them |
| 11:41:1x | `botctl.py resume --ack 4e7449c1` - **HALT cleared** |
| **11:41:23.712** | **T = changeover timestamp** (`1787240483712` ms) |
| 11:41:26 | main relaunched, tmux `shadow-main`, **PID 11872** |
| 11:41:30 | env B relaunched, tmux `shadow-survivors`, **PID 11895** |

**Snapshots (D-358 R3, taken before anything else):**
`backups/2026-08-20-restart/trading.db` 1,932,570,624 B (2.3s) and
`trading-survivors.db` 624,623,616 B (1.0s), via the sqlite backup API on a
read-only connection, not `cp` - a live WAL file copied with `cp` is not a
consistent snapshot. 728 GiB free after.

**The halt that was cleared:** `4e7449c1`, "auto: portfolio drawdown 0.4066
exceeds 0.4000". At shutdown the old epoch read equity **622.10**, peak
1,027.96, 4,134 closed trades, realized **-1,696.74**, and **53 open
positions**.

**Shutdown was clean.** The old main loop wrote its final stats line and
equity snapshot before exiting; env B likewise. Nothing was `kill -9`ed.

**New banner, read back from the log (not claimed):**
`commit: 5d93e91`, `mode: paper`, `launched-by: cody-restart-now`,
`launcher-pid: 11856  parent-pid: 37068`. Log files:
`logs/polymarket_shadow_20260820T154126Z.log` (main),
`logs/polymarket_shadow_survivors_20260820T1541Z.log` (env B).

**What this restart activated** (all were INERT until now, convention 13):
15m signal keying, the calibration tape, the `dip_arb` kill (D-356 R4), the
env B whitelist correction (R4), and **D-359's auto-halt disable**
(`SHADOW_RISK_LIMITS max_drawdown_frac=1.0`, verified in
`shadow_loop.py:411-412`). The real-money default in
`engine/risk/constraints.py:242` is still `0.25` and was not touched.

**53 open positions became orphans** at this restart (D-353). The sweep was
NOT run - the brief forbids folding it into this session, and it gets its own
brief. The orphan cohort has grown again.

---

## 2. V1-V7 verifications (design doc section 6), T = 1787240483712

Run against `db/trading.db` unless noted. Every figure point-in-time,
measured ~3 minutes after T on a live book.

| # | check | result |
|---|---|---|
| **V1a** | NULL `market_duration` | 1,380,954 (pre-T baseline 1,380,307 + growth to T) |
| **V1b** | `ts < T AND market_duration IS NOT NULL` **MUST be 0** | **0 PASS** - no DEFAULT leaked |
| **V2** | post-T by duration | `5m` 234, `15m` 18, `mixed` 36, NULL 132 - **PASS**, writer is wired |
| **V3** | `ts >= T AND pair LIKE '%-15m-%'` **MUST be 0** | **0 PASS** - option A held, `pair` untouched |
| **V4** | longshot_fade must be 100% `15m` | **18/18 `15m`, PASS** - the strongest available test |
| **V5** | corridor family must read `mixed` | `PM_corridor_collector` 18 `mixed`, `PM_corridor_pair` 18 `mixed` - **PASS** |
| **V6** | positions on `-15m-` markets vs signal key | **VACUOUS - re-run required.** Only 11 post-T positions exist, all `5m`; zero on a `-15m-` market yet |
| **V7** | complement window intact | min still **1787124516.649457** (the 03:28 2026-08-19 restart), count 77,581 -> 77,633 - **PASS, window NOT reset** |

**V2's 132 NULL rows are expected, not a failure.** They are skip-path rows
from strategies that cannot declare a scope; design section 3.3 specifies
NULL there rather than a defaulted `'5m'` (convention 20).

**V6 is the one open verification.** It is not a failure - there is nothing
to test yet. Re-run it once the book has opened positions on 15m markets:

```sql
SELECT s.market_duration, COUNT(*) FROM positions p JOIN signals s ON p.signal_id = s.id
 WHERE p.opened_ts >= 1787240483712 AND p.pair LIKE '%-15m-%' GROUP BY 1;
```
Any `5m` row in that result is the original bug still present.

**Env B verified independently and also passes:** V1b **0**, V3 **0**,
post-T `5m` 135 / `15m` 27 / `mixed` 27 / NULL 78.

**Schema landed on both DBs:** `signals.market_duration` present,
`calibration_tape` and `calibration_resolution` created.

**The calibration tape is writing:** 84 rows (main) and 108 rows (env B)
within ~3 minutes, columns as specified including `market_duration`,
`selected`, and full book fields (`best_bid`/`best_ask`/`mid`/
`book_depth_levels`). `calibration_resolution` is 0, which is correct - the
stamp is write-once after a window resolves.

---

## 3. R4 - env B whitelist corrections: APPLIED

D-328's correction was: drop `PM_dip_arb`, drop `PM_fair_value_arb_wide`, add
`corridor_collector`.

**The two drops were already in effect** - neither name was in the running
env B roster at PID 73115, so they were applied at some earlier restart. The
outstanding half was the addition. Env B now runs **9** strategies:

```
PM_temporal_arbitrage, PM_fair_value_arb_patient,
PM_longshot_fade_hold_to_resolution, PM_weather_bracket_width_matched,
PM_fair_value_settlement_exit, PM_weather_arb, PM_streak_snapper,
PM_small_liq_continuation, PM_corridor_collector
```

Verified live, not claimed: the `--strategies` unmatched-name warning
(added by `cody-whitelist-warn`) did **not** fire, so all 9 names resolved,
and `PM_corridor_collector` has already written 27 post-T signals in env B,
all keyed `mixed`.

**Durability gap worth a ruling.** The env B roster exists only inside a tmux
invocation. It is not in version control, not in `config.yaml`, and not in
any script. A reboot loses R4 silently and nobody would notice. Main has
`run_polymarket_shadow.sh`; env B has nothing equivalent. I did not build one
(out of scope for this brief) but recommend it.

---

## 4. R7 - 038 backfill: NOT RUN. Ruling conflict, escalated to Aym.

**The read-only half ran. The write half did not.** This is a deliberate
refusal, not an oversight.

The brief (task 6) and `2026-08-20-keying-restart.md` R7 both instruct
`settlement_coverage.py --backfill`. Three standing records say the opposite:

- **D-354 R4:** "The 038 backfill stays DEFERRED on BOTH databases... Running
  it now would add a biased population to a clean forward ledger and cannot
  change any verdict before 400. **Do not run it. Revisit only on Aym's
  explicit call.**"
- **D-355 R5(e):** reaffirms - "only Aym's explicit call revisits it."
- **The tick6 brief, line 33:** "do NOT run `--backfill` (D-354 R4)".

CLAUDE.md is explicit that DECISIONS.md wins over a brief, and D-354 R4
names Aym specifically as the only authority that can unblock it. Aym's
directive this session ("get all the data have it stored and ready for
Forge") is not that explicit call. **So the write did not happen.**

In fairness to the other reading: D-359 lists "038 backfill" in the restart
payload, and the backfilled rows would be written under
`source='sibling_inference_backfill'` and are excluded from the coverage
number by construction (038 rule 4), which does blunt D-354 R4's stated
contamination concern. It is a one-word decision either way, and a fresh
snapshot exists, so it is fully reversible.

**Aym: say the word and it runs in one command.**

### Coverage baseline (read-only, ran, NOT graded per the brief)

**Main:**
```
COVERAGE  596/1726 = 34.5%  [FAILED]
          closed positions 4143, no outcome_side 272,
          sources ['venue', 'inferred_terminal_price']
AGREEMENT overlap 422, disagreements 14, rate 0.0332  [FAILED]
```

**Env B:**
```
COVERAGE  377/1286 = 29.3%  [FAILED]
          closed positions 2370, no outcome_side 0,
          sources ['venue', 'inferred_terminal_price']
AGREEMENT overlap 201, disagreements 0, rate 0.0000  [PASS]
```

Both FAILED marks are expected and are **not** verdicts: the ledger only
starts recording at T, and the 99% kill condition needs >= 200 closed
positions booked after T, which is a 7-day measurement. Do not grade these.

**The two market-sides the brief asked about are correctly handled.**
`sol-updown-5m-1787056800`/Up and `btc-updown-5m-1787134200`/Down are both
reported as `CONTRADICTORY INFERENCE [0.0, 1.0]`, never resolved, never
backfilled. Eight further contradictory sides were found (10 total).

**New finding for Raven: main has 14 venue-vs-inference disagreements
(3.32%); env B has zero on 201 overlapping rows.** The venue ledger and the
terminal-price inference disagree on outright direction in main, which means
one of the two is wrong about who won a settled market. That is a
data-integrity question, not a coverage question, and it is worth its own
look.

---

## 5. Task 2 - shadow-environment split: the answer is YES, and the evidence names the family

Raven's default position was "keep 2, add a 3rd ONLY if a specific strategy
family's signal is being masked (name the family and the masking evidence)."
**That condition is met.** The family is **fair_value**, and the masking is
measured.

### 5.1 The masking evidence

Both books over the **same overlapping window** (from env B's first position,
2026-08-19T04:40:54Z), closed positions only:

| strategy | main n | main pnl | env B n | env B pnl |
|---|---|---|---|---|
| **PM_fair_value_arb_patient** | **3** | **+3.14** | **887** | **-388.57** |
| PM_fair_value_settlement_exit | 642 | -216.45 | 1070 | -525.63 |
| PM_temporal_arbitrage | 149 | +7.61 | 199 | -17.81 |
| PM_streak_snapper | 42 | +1.49 | 51 | -66.45 |
| PM_small_liq_continuation | 5 | +9.50 | 7 | -23.74 |
| PM_fair_value_arb | **1061** | **-509.13** | 0 | - |

**`PM_fair_value_arb_patient` reads +3.14 on 3 closes in main and -388.57 on
887 closes in env B, in the same window.** That is a 296x sample difference
and an opposite verdict. Main's read of that strategy is a cap-starvation
artifact. On main's evidence alone it looks like a winner and could have been
promoted on a false positive; env B shows it losing -0.44/trade with real
sample.

**The masking agent is named:** `PM_fair_value_arb` took **1061** closes in
that window in main, against a **10-slot** concurrent cap
(`config.yaml:138`, D-321). One strategy consumes the book. Main's own
reason counts for the last session show
`adapter:SKIP:max_concurrent_positions: 310` - the cap is not theoretical,
it bound 310 times.

The pattern is consistent and it points one way: **every strategy that looks
like a small winner in main reads as a loser in env B once it gets sample.**
That is what you would expect if the standing correction is right that the
model is 91% of the loss. Note the 046 caveat - these are clustered samples
and I am making no significance claim. Sample *coverage* (3 vs 887) is a
counting fact, not a p-value.

Second, independent point in favour: **env B's cleaner cohort does produce
cleaner measurements.** 0/201 venue-vs-inference disagreements against
main's 14/422. That was Raven's question 2(b) and the answer is yes.

### 5.2 The cost, measured

Per book, measured live on the two running loops: **~275 MB RSS (1.1% of
24 GB) and 0-5.6% of one core** on a 10-core machine. A third book is
negligible on this hardware. The real costs are the ones Raven named: more
venue polling, and **more orphans per restart** (this restart alone orphaned
53 positions with the sweep still unimplemented). Epoch dilution is real too
- splitting divides the same event stream across books.

### 5.3 Recommendation: correct the membership of the 2 books before adding a 3rd

**Do not add a third environment yet.** The measurement that matters can be
had for free, because **env B is already the fair_value book and nobody
labelled it that way**: 2,049 of its 2,364 closes (**87%**) are
fair_value_settlement_exit + fair_value_arb_patient + fair_value_arb_wide.
It is not a "survivors" book in practice.

Concrete proposal, for Aym and Raven to rule on (I did **not** execute any of
this - the brief scoped Task 2 as analysis):

1. **Make env B explicitly the fair_value isolation book.** Add
   `PM_fair_value_arb`, `_hft`, `_inverse` to it. It already holds the other
   three. Risk profile: this is the bleed book, expect it to lose; its job is
   to measure the family honestly with a full 10-slot cap of its own.
2. **Remove the fair_value family from main.** Use the reversible D-322
   `supported_market_types` mechanism, the same one that killed `dip_arb` -
   not deletion. This frees main's 10 slots for the families that are
   currently starved (mid_price_continuation, corridor_pair, box_builder,
   grid_hedge, maker_rebate_quote_ladder). Main becomes the diversified book.
3. **Only then consider a third book,** if a *second* family shows masking
   after main's cap stops being consumed by one strategy.

Why this beats adding env C: it costs zero extra processes, zero extra
polling, zero extra orphan surface, and it fixes the actual defect - one
strategy eating a shared cap - rather than routing around it. Adding a third
book while leaving `PM_fair_value_arb` in main would leave the masking in
place.

**Caveat Aym should weigh:** step 2 changes main's roster mid-flight and will
end the current sampling arm for those six strategies. Anything mid-measurement
against main's fair_value rows (043, 046 work) should be checked before it is
applied. That is a Raven call, not mine.

---

## 6. Suite and gates

- `backtest/validate_harness.py`: **21/21, rc 0**, run before the restart.
- Full suite: **4,161 passed / 1 skipped / 0 failed**, 388.25s, exit code 0.
  Identical to last session's count - this session added no tests because it
  added no code (the restart is operational; the code shipped in `3952e28`).
- Post-restart smoke, measured not claimed: **0** `ERROR`/`Traceback` lines in
  the new main log, 21 post-T positions opened, and **`PM_dip_arb` post-T
  entries = 0**, which is the D-356 R4 kill confirmed live for the first time.
- Tree: **only the `cody-tick7-rulings` session's 10 files** are dirty (2
  modified proposals, 5 scripts, 3 new proposal docs). **I did not touch or
  commit any of them.** That session (PID 438) is still alive. `HALT` is gone
  from the untracked set because it was properly resumed, not deleted.
- `engine.concurrency who`: **0 active checkouts**.

## 7. Open items and flags

1. **R7 backfill needs one word from Aym** (section 4). Everything else in
   the brief is done.
2. **V6 must be re-run** once 15m positions exist (query in section 2).
3. **Env B's `market_tape` is still frozen** at `1787124461.656716`
   (2026-08-19T07:27:41Z) **after the restart**. Tick7 R4(a) recorded this as
   an open item for the keying-restart window; the restart did **not** clear
   it, so it is a real bug, not a stuck process. Env B contributes nothing to
   any keyed-tape re-derivation until it is fixed. I did not investigate
   (restart lane).
4. **14 venue-vs-inference direction disagreements in main, 0 in env B**
   (section 4). Data integrity, unowned.
5. **Env B's roster is not in version control** (section 3). A reboot silently
   loses R4.
6. **53 new orphans** from this restart. D-353 ruled, still unimplemented.
7. Both books restarted at **1000.00 equity** (D-358 fund-if-zero). The
   drawdown denominator epoch resets here - directly relevant to proposal 048.
8. The auto-halt can no longer fire in shadow. **Nothing now stops a bleed
   except a human reading the equity curve.** That was the intent of D-359,
   but it is worth saying out loud.

---

## 8. ADDENDUM - D-360 landed one minute after the restart

Discovered at push time, not assumed: **HEAD moved underneath this session.**
`dc2be8e` (D-360, `raven-D360-cap`) was committed at **11:42:27 EDT**, 61
seconds after the loops relaunched at 11:41:26. My commit `74b4ffa` sits on
top of it. This is the second time `git` state changing underneath a session
has been the thing that surfaced an external actor (convention 25 earns its
keep again).

**D-360 removes the position COUNT cap in shadow** - Aym's ruling, capital
becomes the only cap. Two facts about its current status, both measured:

- **It is docs-only so far.** `dc2be8e` changed `docs/DECISIONS.md` and
  nothing else (9 insertions, 1 file). The code named in D-360 R3
  (`risk_gate.py`, `paper_adapter.py`, `engine/risk/__init__.py`) is
  **unchanged**. The cap is still 10.
- **It is therefore inert in the loops I started**, which is what D-360 R4
  already says: the restart "executed BEFORE this ruling arrived, so D-360
  requires a SECOND restart to activate."

**I did not implement D-360 this session.** My brief carries a FREEZE banner
scoping me to the restart and forbidding config changes beyond it, and D-360
R3 edits risk-gate code. That is a different brief. D-360 R4 sequences it
anyway: changes made + tested first, then the second restart.

### D-360 sharpens section 5's recommendation - it does not retire it

This matters and it cuts against the obvious reading. One could conclude
"the cap is going away, so the masking goes away, so no split is needed."
**That is wrong, and section 5's proposal gets *more* important under D-360,
not less.**

The 10-slot cap was never the disease. The disease is that **one strategy
with 1061 fills shares a finite resource with a strategy that got 3.**
D-360 does not remove contention; it changes the currency of contention from
*slots* to *capital*. With no count cap and a 1,000 USD book,
`PM_fair_value_arb` will consume the capital exactly as it consumed the
slots, and `PM_fair_value_arb_patient` will still be starved of sample -
only now the starvation will be invisible, because there will be no
`adapter:SKIP:max_concurrent_positions` counter incrementing to make it
legible. **D-360 removes the instrument that made the masking measurable.**

So the recommendation stands and gains urgency: separate the fair_value
family from the other families into its own book, so each family contends
for its own capital. Under D-360 that is the *only* remaining mechanism that
keeps one high-volume loser from crowding out everything else's sample.

**Recommended sequence for Raven:** land D-360's code + tests, take the
pre-approved second restart, then **re-measure the section 5.1 table before
acting on the split** - the counts will move once the cap lifts, and the
proposal deserves fresh numbers rather than these. The masking conclusion is
robust to that (3 vs 887 in the same window is not a marginal reading), but
the magnitudes will change.

**One safety note, offered once and not repeated.** After this restart the
drawdown auto-halt cannot fire (D-359). If D-360 also removes the position
count cap, then a 1,000 USD paper book will have no count limit and no
automatic stop - per-trade notional, per-event (30 USD) and aggregate
capital limits are all that remain, which is precisely what Aym specified.
Both rulings are his and both are recorded; I am flagging the *interaction*
of the two, which no single ruling states, not re-litigating either one.
It is paper money and the blast radius is a number in a database.
