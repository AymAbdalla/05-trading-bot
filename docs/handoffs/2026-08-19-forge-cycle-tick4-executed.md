# Forge cycle 2026-08-19 tick 4 — executed

**Agent:** `cody-forge-tick4` (`AGENT_ID` probed **EMPTY** on this gateway
spawn; tally now 5 SET / 6 EMPTY — still not settled)
**Brief:** `docs/handoffs/from-raven/2026-08-19-forge-reasoner-cycle-tick4.md`
**HEAD at run:** `0f21067`
**Evidence:** re-derived read-only from `db/trading.db` and
`db/trading-survivors.db` this session. **Nothing was carried from the brief
un-rechecked.**

## Headline: two of the brief's central claims did not survive re-derivation

**1. The settlement_exit kill is NOT breached.** The brief called it "KILL
CONDITION BREACHED" on a win rate of 0.119 / 0.185 against D-327's 0.30. I
reproduced 0.119 and 0.185 **exactly** — but they are win rates over *all*
closes, and D-327 names a **settlement frequency** over *resolved* closes.
53.0% (132/249) and 61.9% (313/506) of this strategy's closes are
`sell:salvage_floor` early exits that never reached settlement. Measured as the
condition is written:

| | `trading.db` | `survivors.db` |
|---|---|---|
| settlement freq (resolved subset) | 45/117 = **0.3846** | 109/193 = **0.5648** |
| lower bound (all censored = 0.00) | 45/249 = 0.1807 | 109/506 = 0.2154 |
| 034 kill: P&L per resolved position | **+3.0790** (n=117) | **+5.2413** (n=193) |

Both frequencies are **above** 0.30, not far below it. 034's kill needs 200+
resolved; neither DB reaches it (117, 193) and pooling them is forbidden. The
honest bracket straddles 0.30 in both DBs → **NOT_TESTED**, not a kill and not
an acquittal. D-327 also cites "mean entry ask of 0.33"; measured means are
**0.2402** and **0.2883**.

The family *is* pooled-negative (−0.0213 and −0.0268 per share, −102.71 and
−263.81 USD). Retiring it on **that** is defensible and proposal 041 says so
explicitly. What I refused was recording that a *named threshold* fired when it
did not.

**2. The fee change is a scenario question, not a re-grade of today's P&L.**
The fee is reported on **15m** markets. Our book is **99% 5m**: 2,757 of 2,788
closed positions in `trading.db` (15m = **1.11%**) and 1,205 of 1,208 in
`survivors.db` (15m = **0.25%**).

## Written (3 proposals, all schema-valid, all `expected_edge_bps: null`)

**040 `pm_dynamic_taker_fee_regrade`** (repair) — three *measured* defects in
the fee model, not one:
- **Shape.** `paper_adapter.py:220` `DEFAULT_TAKER_FEE_RATE = 0.0`, charged
  **flat on notional** at `:833` and `:994`. A fee peaking at 50/50 cannot be
  expressed that way. Flat-3.15% vs peaked-3.15% disagree by **67.09 USD** on
  the same 521 taker closes — **19% of the window's entire loss**. Shape
  matters more than rate.
- **Duration.** No duration argument; see the 99%-5m split above.
- **Incidence (nobody had written this down).** Settlement redemption charges
  **no exit fee**; a round-trip sale is charged again at `:994`. So the tax is
  **2x** on round-trippers and **1x** on hold-to-resolution. Lifetime
  settlement share: `fair_value_arb` 7/976 (1%), `_hft` 5/376, `_inverse`
  0/266, `_wide` 0/79 — against `temporal_arbitrage` 148/148,
  `mid_price_continuation` 70/70, `streak_snapper` 44/44, the maker ladder
  43/43, all **100%**. The venue's change **double-taxes the 1,703 positions
  and −849.41 USD that are already the largest loss centre**.
- **Result:** peaked schedule, settlement legs charged once → `trading.db`
  −354.54 → **−437.95**; `survivors.db` −445.37 → **−586.37**. **No strategy
  changes sign in either DB.** The fee deepens the bleed; it does not cause it.
  Do not let this be read as blaming the venue for a 91%-model problem.

**041 `pm_settlement_exit_kill_test_undecidable`** (governance) — the finding
above, with the bracket as the reporting format and two named paths to
decidability (035's uncensored arm; 038's ledger). Carries an explicit
**contradicts_cycle_brief** warning and a banner.

**042 `pm_maker_fill_markout_probe`** (experiment) — the maker side, measured
across the `fill_was_maker` backfill boundary for the first time:
- PRE `2026-08-19T07:28:34Z`: n=16, all flag 0 = **backfill, not observations**,
  −4.85 USD, −0.0606/share.
- POST: n=27, all flag 1 = **observed**, +2.60 USD, **+0.0193/share**.
- Pooling gives the lifetime −2.25 and is forbidden by convention 32. **A
  naive lifetime query flips the sign.**
- The observed sample settles **15 of 27** against mean paid 0.5363 →
  one-sided binomial **p = 0.4988**. A coin flip. **No result.**
- Kill is written in **post-fill markout** with a **−0.0315/share ceiling
  derived from the taker fee itself**, so it stays decidable without knowing
  the rebate size (which is unknown).

## Amended

**039** — appended, via `engine.concurrency.safe_edit`, idempotent on a marker.
Sharper than the brief's version: `survivors.db` holds **zero**
`PM_fair_value_arb` rows (env B's whitelist excludes it) and its 15 `time_stop`
rows are `_wide`/`_patient`, different strategies. All 976 `PM_fair_value_arb`
positions and 33 of the system's 49 time-stops are in `trading.db`, whose loop
died at **16:17:57 UTC**. So 039's matched observations accrue at **zero per
hour** and env B **cannot** substitute. Its 14-day `NOT_TESTED` clock must
start on *038-live AND loop-running*, not 038 alone.

## Refused (5, all recorded in `forge_runs.jsonl`)

1. `pm_settlement_exit_family_kill` — **not supported by measurement**; replaced
   by 041's opposite finding.
2. `pm_maker_rebate_sizing_amendment` — rebate size/mechanism unknown, n=27 at
   p=0.4988. Sizing up on a press announcement is fabrication. Folded into 042.
3. `pm_cross_venue_kalshi_spread` — out of the crypto Up/Down universe.
4. `pm_latency_arb_family` — the venue declared war on it; we have no latency
   edge; Signal 6 debunks one of the cycle's profit claims as a fabricated
   dashboard.
5. `main_shadow_loop_restart` — Raven's lane, explicitly out of scope.

## Questionable / needs Raven

1. **041 contradicts the brief's instruction.** Flagged in the front matter, in
   a banner, and in `forge_runs.jsonl` rather than silently resolved. If you
   want the kill recorded anyway, that is a decision — but it should be
   recorded as a decision, not as a threshold firing.
2. **NEW validator conflict.** `kind: governance` is what the brief names and
   is **absent** from `agents/forge.py:208`
   `KINDS = ('edge_hypothesis','combination','repair','experiment')`. Through
   `forge.py:491` 041 would be refused `unknown_kind`, and again at `:209` for
   a null `expected_edge_bps` since `governance` is not in `NULL_EDGE_KINDS`.
   Latent, not live — hand-written `.md` files never pass through `forge.py`.
   **I deliberately did not relabel it `repair` to make it pass**; it repairs
   nothing. One-line call either way.
3. **`peaked_315` is a guess** — one reported point does not determine a
   function. 040 rule 7 forces all three scenarios into one report so the guess
   cannot quietly become the number, and rule 1 keeps the default at **0.0**
   until a venue-sourced schedule exists.
4. The **200 bps vs ~20 bps** floor dispute is unchanged and does not gate any
   of these three (all null-edge kinds).
5. `strategies/proposals/external-signals-2026-08-19-cycle3.md` is **untracked**
   and is an input my proposals cite. Another session wrote it; I left it alone
   rather than commit a foreign-owned file. Your call whether it gets tracked.

## Verified this session (convention 25 — claims, re-measured)

- **Main loop DEAD, confirmed by `ps`**: only PID **71442 / 71444** (env B,
  `AGENT_ID=cody-env-b`, started 03:28:40) is alive. `trading.db` last close
  `2026-08-19T16:17:57.735Z`, last equity **619.046**.
- **`survivors.db` is LIVE and moved under my read** — two passes minutes apart
  returned n=765 then n=768 taker closes since cutoff. Its figures are
  point-in-time by nature and I say so in the run record.
- `positions.fees` reads **0.0000** on every closed position in both DBs — the
  current fee model behaving exactly as documented, not a bug.
- `market_tape` already carries `condition_id` and `complement_id` columns in
  `db/trading.db` (34,700 rows, 03:31→16:17 UTC). **Not acted on** — flagging
  it because 036/037's blockage was described as a missing key. Worth a look;
  I did not touch it (026/037 mid-measurement).

## Not done / out of scope

- No loop restarted, no process signalled, no `config.yaml` or `DECISIONS.md`
  touched, no backtest run, nothing wired live. 037/039 left blocked.
- No code written. 040 and 042 both propose code; neither implements it.
- Full suite / harness **not** re-run — this session wrote only proposal
  markdown and a JSONL append, no importable code. Baseline stands at
  `2e1184a`'s reading (4,085 passed / 1 skipped, harness 21/21).

## Ledger discipline

All three new files registered via `concurrency.checkout(allow_missing=True)` +
`checkin`; 039 and `forge_runs.jsonl` edited via `safe_edit`, both idempotent
on marker strings. `engine.concurrency who` read **0 active checkouts** at
session start — the long-running `cody-discovery-design` stale `CLAUDE.md`
checkout did **not** reappear. Zero hook friction.
