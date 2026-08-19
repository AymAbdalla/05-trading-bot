# Opus planning session: the agreed plan, and a correction that changes D-326

**Cody (Opus), 2026-08-19 ~01:05 EDT.** Worked
`docs/handoffs/from-raven/2026-08-19-opus-planning-session.md` (Raven, written
00:38). Read-only on `db/trading.db`. **No code, config, or `DECISIONS.md`
changes.** Deliverable is `docs/PLAN-2026-08-19.md` (new, untracked).

Main loop PID 35848 untouched and live. Four sibling Claude sessions were
running concurrently; I wrote only two new files, both uncontended.

---

## Read this first: D-326 needs a hold, and the build is in flight

`cody-mirror-fade` (PID 40151) is building `fair_value_mirror_fade.py` right
now — the file and a modified `strategies/polymarket/__init__.py` are already
in the tree. **I did not touch either.** The hold below is Raven's and Aym's
call, not mine to execute.

**The finding:** the +$281.74 mirror that D-326 was ruled on does not survive
being split by *how the fill happened*.

| subset | n | paid | realised | mirror net of real overround | t |
|---|---|---|---|---|---|
| ALL settled | 355 | 0.215 | 0.158 | +269.45 | **3.46** |
| MAKER-fill | 186 | 0.213 | 0.147 | +218.30 | **3.13** |
| TAKER (**executable**) | 169 | 0.222 | 0.183 | **+51.15** | **1.52** |
| TAKER excl. ask <= 0.10 | 116 | 0.382 | 0.327 | +40.24 | **1.19** |

**80% of the fade evidence is maker fills, and a maker fill cannot be
mirrored** — for two reasons, one of them mechanical:

1. The counterfactual doesn't exist. Those fills happened because someone
   crossed our resting bid. Taking the other side means lifting our own offer
   at a price the market has already left.
2. **The loss is the fill rule, not the market.**
   `paper_adapter.py:1088 _through_and_touch` fills a resting BUY at `L` only
   when the best ask has fallen **strictly below `L`**, and
   `_fill_resting_buy` (line 1461) books it **at `L`**. So the fill exists only
   in states where price already moved against us, priced pre-move. Adverse
   selection by construction. `grid_hedge` paying 0.213 and realising 0.147 is
   the simulator restating itself. (To be fair to it: this is the deliberately
   conservative choice and its docstring says so. Good modelling — just not a
   market measurement.)

**And the approved probe mirrors the fair_value model, the one family with
almost no settlement evidence:** of 355 settled positions, `fair_value_arb`
has 5, `hft` has 5, `inverse` and `wide` have **zero**. The headline contains
ten fair-value trades.

Of the +$51.15 that is executable, roughly half is 29 deep longshots
(ask 0.025-0.045) that all settled at zero — selling 4-cent lottery tickets at
24:1 downside on a sample where every one won.

**What this does NOT overturn:** model slope 0.30, the anti-predictive edge
quartiles (hft 45.7% -> 20.2% WR as claimed edge rises), every strategy below
its break-even WR, no time-of-day edge. The fair_value model is still bad.
Only this changed: **fading it is not the proven fix.**

**Kill condition for the fade thesis (convention 6):** dead unless taker-only
settled mirror PnL reaches **t >= 2.0 on n >= 250**, excluding entries below
ask 0.10. Today: **t = 1.19 on n = 116.**

Note: the 034 re-gate (mirror-fade directive Task 2) is **good and should
proceed** — unaffected by any of this.

---

## The fact that should reorganise the program

`PM_temporal_arbitrage`, 83 positions, **zero censoring** (no stop, 100% held
to settlement, each a distinct market):

```
415 shares   paid 0.1813   realised 0.1807   edge -0.0006
```

**The market price is calibrated to within 0.06 percentage points.** Cleanest
measurement in the DB: no fill model, no stop, no outcome selection, no pooling.

Every strategy in the registry and every existing proposal is a *forecasting*
strategy — a bet we beat that price. On the only unbiased sample we own, it
cannot be beaten measurably. Caveat: n=83, one strategy, two days, BTC 15m
only. It bounds the edge loosely. But it is the only number not contaminated by
a stop, a fill model, or a selection rule.

---

## Answers to Raven's five questions (detail in the plan, §2)

**Q1 — env B.** Agreed: drop `dip_arb` (t=-2.25) and `fair_value_arb_wide`.
The strongest counter-argument to `streak_snapper` is **not** its sample size —
it is that `streak_snapper` is a MAKER strategy, so its +$4.52 comes from the
same fill rule D-323 paused elsewhere. Keep it, but **tag it: maker-fill
results must never be pooled with taker results.** Pooling is what produced
D-326. Corridor family: add `corridor_collector` (n=5, model-independent),
leave `corridor_pair` out (68.1% break-even WR, 19 positions already enough).
Recommended env B is 9 strategies, listed in the plan.

**Q2 — 034's gate.** Raven's worry targets the wrong gate. `ask <= 0.60`
removes only 13.2% of the population and refused **5 of 1,131** live signals —
near non-binding. The binding constraint is `max_trades_this_window`
(**643 of 1,131**). Keep the cap, relax the throttle. Yes to a frequency kill:
retire if 60 settled entries are not reached within **14 days**. And do not
raise its edge threshold to make it fire — the quartile tables show tightening
the edge gate selects *for* model error.

**Q3 — the ONE measurement.** Not the half-spread; I measured that tonight
(median complement overround **0.0020/share**, mean 0.00316, from 7,312 tape
pairs) and it changes nothing. The ONE measurement is **the complement token's
own ask at entry (`counter_ask`) plus the complement token's identity.**
Because: `features_json` has `best_ask` and **no `best_bid` at all** (58 keys
checked); `market_tape.market_id` is a token id while `positions.pair` is a
market slug; **there is no complement mapping anywhere in the DB.** My attempt
to recover one by mid-sum matching over-matches — **61.7% of token-timestamps
get more than one candidate partner** — which is why I am reporting
"`yes_ask + no_ask < 1` in 7.85% of pairs" as **NOT_TESTED, not a finding**
(convention 11). Mandatory companion, one column:
**`positions.fill_was_maker`**. You currently cannot tell fill provenance from
the positions table; that absence is exactly what let 80% non-executable
evidence pool with 20% executable evidence.

**Q4 — the Forge brief.** All six existing proposals are forecasting
strategies. The missing family is **forecast-free / structural**:
(1) complement no-arbitrage — buy YES+NO when `yes_ask + no_ask < 1`, settles
at exactly $1 regardless of outcome (blocked on Q3, so the first deliverable is
instrumentation, not a strategy); (2) cross-market monotonicity in strike /
bracket sums; (3) resolution-mechanics edge — predict the *rule*, not the
price. Explicit do-not-propose list: anything needing the fair_value model,
anything whose edge is a maker fill, anything justified by a time-of-day window
(p = 0.342 over 2,000 shuffles).

**Q5 — the honest question.** Yes, and it is **forced by measurement, not
chosen as a style**: if the price is unbiased there is no high-edge trade to
find, so anything that works here works in fractions of a cent on high count,
out of structure rather than forecasting. One caution: the reference wallet's
1.8% ROI on $2M turnover is not our regime ($1,000 paper equity, 4,916 shares
in ~2 days). Copy the shape, not the ROI.

---

## Ranked plan for the rest of the window (full version in `docs/PLAN-2026-08-19.md` §3)

1. **HOLD the mirror-fade probe** or ship it PAUSED via the D-322 mechanism.
   Highest value, near-zero cost. `t = 1.52` will still be `t = 1.52` tomorrow.
2. **Ship the two Q3 measurements** — `counter_ask` + complement id on signals;
   `positions.fill_was_maker`. The only code I would write tonight. Both are
   prerequisites, not experiments. Must go through `safe_edit`; convention 13
   means the running loop won't see them until a restart (Raven's call).
3. **Standing rule:** any fade/mirror claim is reported split by
   `fill_was_maker`, never pooled.
4. Env B corrections (Q1) — apply to the filter if it takes one; don't restart
   env B just for this.
5. Forge brief (Q4).
6. **Do not:** restart the main loop; unpause `grid_hedge`/`box_builder`
   (D-323 was right for a better reason than it stated); build any new
   forecaster; act on a time-of-day window.

---

## Correction to my own prior handoff

`docs/handoffs/2026-08-19-opus-edge-analysis.md` (commit `e033078`) printed the
per-strategy mirror table — `grid_hedge +170.77`, `box_builder +53.45`, 80% of
the total — and **still called the pooled +$281.74 "the one real signal"**
without separating maker from taker fills. Raven ruled on the pooled number.
That is on the analysis, not on Raven. It also assumed the complement costs
exactly `1 - entry`; the real cost is ~0.3 c/share more. Everything else in
that handoff stands.

---

## What I did not do

- No code, config, DB, or `DECISIONS.md` writes. Read-only as directed.
- Did not touch `fair_value_mirror_fade.py` or `strategies/polymarket/__init__.py`
  — `cody-mirror-fade` (PID 40151) owns those and is mid-build (convention 21).
- Did not restart or touch the main loop (PID 35848) or env B.
- Did not run the full suite — no code changed, and PID 39524 is running it.
- Did not verify the complement-arbitrage rate. Reported NOT_TESTED with the
  reason (convention 11).

## Files written

- `docs/PLAN-2026-08-19.md` (the deliverable)
- `docs/handoffs/2026-08-19-opus-planning-session.md` (this file)

## Open for Raven

1. **Hold or ship D-326's probe?** My recommendation is hold, or ship paused.
2. **Approve the two Q3 measurements as tonight's build?**
3. **Env B whitelist corrections** — apply now or at next restart?
4. **Does the maker-fill artifact finding change how D-323 is stated?**
   The pause was right; the stated reason ("measured bleed") should become
   "the maker numbers are fill-model artifacts and measure nothing."
