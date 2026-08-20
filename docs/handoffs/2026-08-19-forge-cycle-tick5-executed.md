# Forge reasoner cycle tick 5 - executed

**Session:** `cody-forge-reasoner` (AGENT_ID probed EMPTY on this gateway spawn)
**Brief:** `docs/handoffs/from-raven/2026-08-19-forge-reasoner-cycle-tick5.md`
**HEAD at start:** `4121245` (matched the brief, verified twice with
`git rev-parse HEAD`)
**Evidence re-derived:** 2026-08-19T23:57:33Z, single-transaction read-only
snapshot of `db/trading.db` + `db/trading-survivors.db`

---

## What was written

| # | file | kind | verdict |
|---|---|---|---|
| 043 | `strategies/proposals/043-pm-early-exit-counterfactual-ledger.md` | experiment | **Inverts the brief's priority 1** |
| 044 | `strategies/proposals/044-pm-weather-information-lag-refused.md` | governance | **Refuses the brief's priority 2** |

Plus two in-place amendments via `engine.concurrency.safe_edit` (idempotent on
a marker string), and one appended `forge_runs.jsonl` record.

**Next free number is 045** (I used 043 and 044; the third slot went to an
amendment instead of a new file).

---

## The headline: the exit-reason asymmetry is circular, and its suggested fix is backwards

The brief called the exit-reason asymmetry "the headline measurement" and "the
strongest Opus reasoning target this cycle", and suggested testing whether
holding losers to resolution beats the `salvage_floor` exit.

**The asymmetry carries no information.** `sell:profit_target` is the name of
the exit that fires *because* a position is winning; `sell:price_stop` fires
*because* it is losing. Sorting P&L by exit reason recovers the definitions of
the exits. The same shape appears in a winning book, a losing book, and a book
of coin flips.

**The non-circular version is now computable for the first time, and it says
the opposite.** The 038 ledger is LIVE in `db/trading.db` (350 rows, 175
slugs, `source=venue`), and `positions.pair` is the market slug, so an exit
joins directly to its own resolution. Measured:

- `sell:salvage_floor`: **1136 shares across 59 matched positions sold for
  72.52 USD; worth 40.00 USD at resolution.** Holding would have cost
  **32.52 USD more** - salvage is **44.8% better** than the hold-through.
- Break-even is an **identity**, not an estimate: holding wins iff the realised
  settle rate exceeds the mean salvage price. Measured 0.0352 against 0.0638.
- **Every** early exit sells above realised value: profit_target 0.4177 into
  0.3657, price_stop 0.1822 into 0.1498, salvage_floor 0.0638 into 0.0352,
  model_stop 0.2954 into 0.1935. That is **not exit skill** - it is the
  91%-model diagnosis showing up in the exit column. Salvaged positions were
  bought at 0.2608 and were worth 0.0352; the exit policy is arguing over the
  last six cents of a twenty-six cent mistake.

**The ledger is checked against itself, not trusted.** On 148 positions
carrying *both* an independent settlement (`exit_px` exactly 0.00/1.00) and a
ledger row, it disagrees on 25 of 2016 shares (**1.24%**), near-symmetric
(15 vs 10), net directional bias **0.25%** = 2.82 shares against a 32.52-share
shortfall. **11.5x too small to flip the sign.**

`salvage_floor` is also **100% `PM_fair_value_settlement_exit`** in both DBs
(196/196 and 433/433). So the brief's "worst line in env B" and its
kill-status item 1 are the **same population**, not two findings.

---

## Kill-condition status (re-derived, not carried)

**1. `PM_fair_value_settlement_exit` (034): still undecidable, and 041's own
prediction is confirmed.** trading.db 179 resolved of 374, freq 0.3575, honest
bracket **[0.1711, 0.3575]**; survivors 269 resolved of 698, freq 0.5725,
bracket **[0.2206, 0.5725]**. 0.30 sits inside both. 041 argued the question
stays undecidable at any n because the censoring rate does not fall with n -
adding ~811 closes moved censoring 53.0%/61.9% -> 52.1%/61.5%, under a point.
**Note for Raven: survivors has now PASSED 034's 200-resolved bar at n=269 and
the kill still does not fire, because the point estimate 0.5725 is ABOVE 0.30.**
I did not re-litigate 041; 043 is the instrument that dissolves the censoring
instead of bracketing it.

**2. `PM_fair_value_arb`:** not re-proposed. Still bleeding (189 closes,
-61.03 USD since cutoff). Convention 13.

**3. 032 longshot_fade:** unchanged, NOT_TESTED.

**4. 039:** **both blockers CLEARED** - see amendments below.

---

## Amendments (in place, via safe_edit)

**039** - both blockers cleared. The 038 ledger is live (rule 1 satisfied), and
the observation source is back: `PM_fair_value_arb` booked 189 closes since the
cutoff, system-wide `sell:time_stop` is now 52 positions / 920 shares (was 45).
**The 14-day NOT_TESTED clock can start, dated from the 20:06:30Z restart** as
the later of the two preconditions. Also recorded: **the first ledger-sourced
`time_stop` observation runs AGAINST the thesis** (20 shares sold at 0.1590
against a realised 0.00 - the opposite sign to the thesis's +0.184). n=1, not
evidence. But it **removes the robustness argument**: the thesis leaned on
038's ~20-point loser bias working *against* the finding, and 043 measures that
bias at 0.25% of shares on venue-sourced rows.

**042** - the clock has started and the sign flipped. Rule 9's "accumulates
nothing at zero rows per hour" is false: observed maker fills grew **27 -> 45**
(+4 open), 22.5% of the way to the 200-fill bar. **The sign flipped**: the
original 27 carried +2.60 USD / +0.0193 per share; the 45 carry **-2.40 USD
over 225 shares / -0.0107 per share**. Recorded as a coin flip resampling, not
a result (the filing already declared p=0.4988). **Explicitly warned that
-0.0107 must NOT be read against the kill thresholds** - those are denominated
in 60-second post-fill *markout*, which nothing records yet. Signal 2 (venue
internal MM desk) filed as a **risk factor changing no threshold**: a desk
taking a share of the rebate pool can only move the real rebate *down*, which
makes the fee-derived -0.0315 ceiling more conservative, never less. What it
changes is **urgency** - the clean pre-desk markout baseline exists now and
will not indefinitely.

---

## Refused, with reasons

- **Hold-to-resolution instead of salvage** (priority 1a) - contradicted by
  measurement, see above. Also: -1814.63 is the wrong decision quantity. By the
  time the bid reaches 0.10 the position is already worth <= 0.10, so the exit
  choice controls ~0.07 of a ~0.29 entry - roughly a quarter of the loss. The
  other three quarters is entry.
- **Weather information-lag probe** (priority 2) - the feed is **HAVE**
  (`engine/feeds/noaa_weather.py`, METAR, `observed_ts` + `obs_age_sec` as
  first-class fields), the mechanic is **wired and live**, and it measures the
  lag as **absent**: `airport_agrees_with_market` fires 2,614 of the 3,423
  temperature signals that *reach* the comparison = **76.4%**, live to
  2026-08-20T00:00:40Z. The real blocker is instrument resolution -
  `rung_narrower_than_model_resolution` 8,251 (65.2%), a deliberate
  `MIN_ATTAINABLE_P_YES=0.5` refusal. **033 exists to fix exactly that and has
  never fired** (7,608 signals, 0 acted). The brief's +7.35 is one closed trade
  with **seven open siblings**. Two numbered reversal conditions written.
- **Standalone 045 maker proposal** (priority 3) - would restate 042. The new
  facts went into the amendment instead.
- **Signal 5 holding reward** - unverified, nothing leans on it. Recorded the
  number to check it against: it would need to be worth **> 0.0286 per share**
  on salvaged shares to close 043's gap.
- **Signal 3 (15m continuation)** and **Signal 6 (cross-venue)** - out of
  current universe.

---

## Two self-corrections, both recorded inside the proposals

1. **044's first draft asserted a denominator I had not measured** and read the
   agreement rate as 60.7% when it is **76.4%**. Dividing agreements by *all*
   temperature signals gives 20.6%; dividing by those that *reached* the
   comparison gives 76.4%. Two defensible-looking queries differ by 56 points.
   The kill condition now enumerates the denominator term by term.
2. An earlier pass read 9,423 `resolution_station_unknown` rows as a live
   station-mapping crisis. **100% of them are CRYPTO markets**
   (`sol-updown-5m-...`), under a defect **already fixed 2026-08-18 ~15:25Z**
   by the `not_a_temperature_market` gate. Convention 25 applies to your own
   aggregate.

---

## Questionable / for Raven

1. **`kind: governance` is unregistered, second offence.** 044 joins 041.
   `agents/forge.py:208` `KINDS` omits it and `:209` `NULL_EDGE_KINDS` omits it
   too. Latent (hand-written `.md` never passes through `forge.py`), but there
   are now two such files on disk. One-line call either way; I did not relabel
   to `repair` to make it pass, because it repairs nothing.
2. **043's counterfactual arm is a floor, not the arm.** "Decline to salvage"
   is not "hold to resolution" - the position still faces `profit_target`. So
   the true hold value is weakly *above* 40.00 USD and the 44.8% should be read
   as an **upper bound** on salvage's advantage. Stated in the proposal's "Why
   this might fail"; rule 4 does not currently compute the recovery rate that
   would bound it.
3. **Env B cannot be measured at all.** `db/trading-survivors.db` has **no
   `market_resolutions` table**, so its 433 salvage closes / -1814.63 USD have
   no counterfactual by any method. The *larger* salvage population is the one
   we cannot measure. Creating that table is not my lane and I did not.
4. **Fee incidence runs against 043's result, not for it.** A salvage sale pays
   a taker fee; a redemption does not. Under 040's peaked schedule salvage
   would look *worse* than measured. The 44.8% is stated before that correction.
5. **The matched sample is post-restart only** (the ledger's first window is
   after the 16:17-20:06 gap), so it is four hours of book being read as a
   strategy property. The kill's n=400 is a partial answer, not a complete one.

---

## Discipline notes

- Every figure re-derived read-only in one transaction. **Both loops are LIVE
  and moved under the read** - the salvage matched sample grew 1096 -> 1136
  shares between passes minutes apart. All figures are point-in-time and say so.
- No loop restarted, signalled or touched. No `config.yaml`, no `DECISIONS.md`.
  No importable code modified. No DB writes. 037/039 blockages not unblocked
  beyond noting them.
- Both new files registered through `engine.concurrency` (checkout/checkin
  round trip); both amendments through `safe_edit`. `who` showed 0 active
  checkouts at start.
- All four touched proposals validated: YAML frontmatter parses and carries all
  12 required schema fields.
- Write tool **WORKED** this session (repo-root `_scratch_*.py`, `docs/`, and
  `strategies/proposals/` paths all succeeded).
- `AGENT_ID` probed **EMPTY**. Running tally is **6 SET / 9 EMPTY**.

## Stale lines in CLAUDE.md this session corrected

- "`market_resolutions` does NOT exist in the live `db/trading.db` yet" -
  **FALSE**, it exists with 350 rows.
- "weather books 0 entries and that is CORRECT" - the *substance* stands
  (the book still refuses almost everything, for a stated arithmetic reason),
  but the literal claim is stale: `PM_weather_arb` has 4 entries in
  `trading.db` and 4 in env B, first at 2026-08-19T02:08:04Z.
