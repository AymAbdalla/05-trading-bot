# Strategy Lab v5 — The Graveyard Response
**For: Quant trading bot — direct reply to STRATEGYGRAVEYARDPACKAGE.md (2026-08-13)**
**Date: 2026-08-13 | Author: Claude (strategy lab) | Status: five proposals in the graveyard's own §7 format. No trickery: every proposal pre-registers its predictions and names its own kill condition.**

---

## 0. What the graveyard actually proved

218,295 rows, 35 strategies, all landing at negative-transaction-cost is not a failed search — it is a successful measurement: **price-pattern signals on liquid instruments at short horizons under taker execution carry zero exploitable information after costs.** That replicates the academic baseline for exactly that search space. The apparatus is trustworthy (the package's own conclusion); the search space was the corpse.

Every one of the 35 strategies shared three structural choices, and the choices — not the signals — set the outcome:
1. **HORIZON:** short holds, where the cost-to-move ratio is mathematically fatal (§2)
2. **EXECUTION SIDE:** all 35 paid taker fee + slippage on every leg
3. **EVENT SELECTION:** all fired on price geometry; none fired on identified forced flow

Lab v5 changes one knob per proposal instead of proposing signal #36.

---

## 1. THE FEE AUDIT (not a strategy — a stale parameter)

**Finding:** Binance.US changed its spot fee schedule on 2026-04-22 to **0% maker / 0.02% taker on all pairs, every user, no tiers**; core pairs (BTC/USD, ETH/USD) run **0% maker / 0.01% taker**. The harness charges 0.10% taker per side — **5–10× the current venue fee**.

**Corrected round-trip cost on $100:**
| Execution | Fee RT | Slippage RT | Total | vs. modeled $0.30 |
|---|---|---|---|---|
| Taker/taker (harness today) | $0.20 | $0.10 | $0.30 | baseline |
| Taker/taker (true, all pairs) | $0.04 | $0.10 | **$0.14** | −53% |
| Taker/taker (true, core pairs) | $0.02 | $0.10 | **$0.12** | −60% |
| Maker/maker (true) | $0.00 | ~$0.00* | **~$0.00–0.02** | −~95% |

*Maker legs don't pay slippage — you set the price. They pay **adverse selection** instead, which is a real cost that must be measured, not assumed away (see P2).

**What this does NOT change:** the library stays dead. Gross edge ≈ 0 means a coin flip at $0.14 loses $0.14. Re-running the 35 at true costs is still worth doing as a **control with a pre-registered prediction: everything lands at ≈ −$0.14, confirming gross ≈ 0 at the new floor.** If anything lands materially above −$0.14, the original gross-zero conclusion was cost-model-dependent and needs re-examination.

**What it DOES change:** the §7 rule-3 bar drops from 30bps to **~14bps taker / ~2–5bps maker (adverse-selection-adjusted TBD).** Half the hypotheses previously dead-on-arrival are now testable.

**Work orders:** (1) per-venue, per-pair fee table in the cost model, with a maker/taker flag on every leg; (2) stamp cost-model version on every run — runs under different cost models must never pool (new silent assertion); (3) verify live fee schedule against the actual account before any live decision — fee schedules change, and OCBS-style trades are excluded from these rates. Equities lane: Alpaca is commission-free but spread-paying; its cost model is spread-based, not fee-based.

---

## 2. THE TOLL LAW (the arithmetic that killed the library)

Cost is charged **per round trip**. Edge accrues **per unit of time held**. Expected favorable move scales with ATR_hold ≈ ATR_bar × √(bars held); a genuine signal captures some fraction κ of it (realistic κ ≈ 0.05–0.20). Net per trade:

  **net = κ · ATR_hold − c**

Minimum viable volatility-per-hold: **ATR_hold ≥ c / κ.**

| Hold | Typical ATR_hold (liquid equity/crypto) | κ=0.10 capture | vs c=$0.30 | vs c=$0.14 |
|---|---|---|---|---|
| 15m | 0.2–0.4% | 2–4 bps | dead | dead |
| 1 day | 1.5–3% | 15–30 bps | marginal | viable |
| 5 days | 3–6% | 30–60 bps | viable | comfortable |
| 10 days | 4–9% | 40–90 bps | comfortable | comfortable |

The entire 218k-row library was tested in rows one and two, mostly row one. **No signal quality can rescue a horizon whose move budget is smaller than the toll.** This law is falsifiable, so it is Proposal 1 rather than an assumption.

**Pre-kill demonstration (rule 3 in action):** the famous overnight-drift anomaly (equity returns concentrate close-to-open) pays ~2–5bps per night and requires a daily round trip. Dead at ANY retail cost model, old or new, by arithmetic alone — zero compute spent. The framework kills famous things too; that is what makes its survivals meaningful.

---

## 3. FIVE PROPOSALS (graveyard §7 format)

### P1 — HORIZON LADDER *(knob: horizon; a law test, signal-agnostic)*
- **Thesis:** per the Toll Law, for any signal carrying true information, net edge per trade rises with holding period until signal decay dominates. The library's universal failure is predicted by horizon alone.
- **Design:** two deliberately generic, pre-committed signals — REV (5-day return in bottom cross-sectional decile → long) and MOM (60-day return in top decile, above 100d MA → long) — each run across the hold ladder {1, 3, 5, 10, 20 days}, all 180 tickers pooled, both cost models.
- **Pre-registered predictions:** (a) net-vs-hold slope is positive for both families at short end; (b) REV peaks at 3–10 day holds (Lehmann/Jegadeesh weekly-reversal horizon), MOM needs ≥20 days; (c) nothing is viable at 1-day under the old cost model.
- **Kill condition:** flat-or-declining net-vs-hold across BOTH families ⇒ the Toll Law fails on this universe and short-horizon research is un-condemned.
- **Gross estimate:** REV at 5-day holds, documented weekly-reversal literature range ≈ 30–80bps gross per trade pre-decay; MOM at 20 days similar order.
- **Frequency/power:** deciles × 180 tickers × ~100 non-overlapping formations ⇒ tens of thousands pooled per cell. Powered even for small edges.
- **Fires-check:** trivial (cross-sectional ranks always exist). Time-based holdout native: calendar-half split, exactly §6.3.

### P2 — TOLL COLLECTOR *(knob: execution side; maker-only passive reversion)*
- **Thesis:** the library's −$0.30 is somebody's +$0.30. At 0% maker, the bot can be the somebody. Liquidity provision earns most when volatility is high and providers withdraw — the documented mechanism behind reversal profits — so passive fills during stress are compensated, not accidental.
- **Design:** resting limit buys at k·ATR below last close (k ∈ {1.5, 2.0, 2.5}), armed ONLY when 1h realized vol > 70th percentile of 30 days (calm-market passivity is uncompensated). Exit via resting limit at fill + m·ATR (maker both ways). Taker stop strictly below entry (rule 7), expected to fire on a minority of trades.
- **Critical honesty — the fill model:** maker fills must be simulated conservatively: filled only if the bar trades THROUGH the limit (low < limit − 1 tick), never on a touch. This is a harness extension and it is the load-bearing wall; an optimistic fill model here would be self-deception of exactly the §3.6 class.
- **Do not confuse this with re-running grid at zero fees:** grid_2.0atr's gross was ≈ +$0.03 ≈ noise; costless noise is still noise. The vol-arming condition and placement discipline are the hypothesis; the fee structure just stops taxing it.
- **Pre-registered prediction:** maker-filled trades show positive net edge concentrated in the top vol quintile; calm-quintile fills ≈ 0.
- **Kill condition:** if maker fills underperform the equivalent taker-at-touch trades by MORE than the fee+slippage savings (~14–30bps), adverse selection eats the discount and passive execution is dead as an edge source.
- **Gross estimate:** post-dislocation reversion after ≥2·ATR intraday flushes in crypto: 20–60bps over hours-to-days, plus ~0 execution cost.
- **Frequency/power:** hundreds of armed windows per month across 3 pairs × 3 k-levels ⇒ thousands of fills per year of data. Fires-check: log armed-time %, order-placement count, and fill rate BEFORE reading P&L.

### P3 — DISPERSION GATE *(knob: event selection via predicted condition; formalizes §4 and §6.4)*
- **Thesis:** from the Toll Law, any entry's viability condition is ATR_hold ≥ c/κ. With c = 14bps and κ = 0.10, the gate is per-hold ATR ≥ 1.4% — **derived before testing, which is exactly the §6.1 demand.** The conservative-gate +$0.094 on 116 trades is this law peeking through an underpowered sample.
- **Also explains the BASE < AGGRESSIVE anomaly:** vol filters are non-monotonic for reversion-tilted entries. Moderate vol (>1.2×) selects trending continuation — the worst regime for reversion. Extreme vol (>2.0×) selects forced-flow dislocation — the best. Pre-registered prediction: edge vs. entry-time vol-decile, pooled across 180 tickers, is ≈ flat-negative through the middle deciles and turns positive only in the top decile-and-a-half; interacting with a trend-state flag sharpens the middle-decile trough.
- **Design:** take the 3 highest-frequency existing entries (grid_2.0atr, stoch_rsi_oversold, dca as control), apply the DERIVED gate (not a scanned one), pool all 180 tickers, judge on a time-based holdout.
- **Kill condition:** monotone-flat edge across vol deciles pooled ⇒ dispersion conditioning is dead and §4's 116 trades were the hammer's $1.48 in a costume.
- **Power:** the conservative gate on 3 tickers made 116 trades; on 180 tickers ⇒ est. 5–8k trades, meeting the doc's own 4,000–8,700 bar for ±$0.09 edges. This is the cheapest powered test in the lab — it reuses everything already built.

### P4 — FINGERPRINT ROUTER *(knob: event selection via instrument character; answers §6.2 with a pre-registered fingerprint)*
- **Thesis:** return autocorrelation is an instrument-level trait driven by microstructure and clientele (Lo–MacKinlay variance ratios), stable enough to route on. Asset class failed (§5.4) because it is orthogonal to the trait that matters: VR varies more WITHIN classes than between them.
- **Design:** compute VR(5,1) and VR(20,5) per instrument on the TRAINING half only. Pre-commit the routing before any strategy P&L is seen: reversion entries → VR < 0.9 instruments; momentum entries → VR > 1.1; dead zone (0.9–1.1) untraded. Judge routed vs. anti-routed cells on the time holdout.
- **Why this is not §3.5 winner-filtering:** the router is a theory-derived statistic measured on data that never touches strategy P&L, committed in writing before the first routed backtest. Discovery and judgment never share a sample or a variable.
- **Two kill conditions:** (a) trait instability — cross-half Spearman correlation of instrument VRs < 0.3 ⇒ the fingerprint is weather, not character, and routing dies before P&L is even read; (b) routed cells fail to beat anti-routed cells by the power-adjusted margin ⇒ the trait exists but doesn't pay.
- **Frequency/power:** pooled routed cells across 180 instruments × existing strategy library ⇒ tens of thousands of trades recycled from infrastructure already built.

### P5 — FORCED-FLOW HARVEST *(knob: event selection; the large-edge/low-frequency lane)*
- **Thesis (the doc's own rule-1 exemplar, now cost-justified):** forced sellers must liquidate regardless of price, and the pressure ends when the margin call clears; the post-cascade snapback is payment for absorbing mechanically-motivated flow. Price patterns fire on geometry; this fires only when the SELLER'S CONSTRAINT is identifiable.
- **Selection (uses only data already in the environment):** crypto — funding-rate stress percentile (the 1yr perp funding files) + ≥3 expanding down candles on volume z > 3 with the cascade low holding above the prior swing low; equities — capitulation days: volume z > 4, close in bottom decile of range, gap-down open, no earnings within the window (informational-flow exclusion). Hold 2–5 days (Toll Law compliant); stop strictly below the event low.
- **Pre-registered prediction:** event-cohort net ≥ +30bps per trade at the corrected cost model, with edge NOT concentrated in a single underlying (leave-one-out flag from §3.4 applies automatically).
- **Kill conditions:** pooled net < +30bps, OR the top underlying's removal moves the result > $0.15/trade (one asset in a costume), OR the funding-stress leg and the volume-climax leg disagree on sign (mechanism incoherence).
- **Gross estimate:** 100–300bps per event over the multi-day snapback — this lane only exists if the edge is LARGE; that is the design, per the power math.
- **Frequency/power:** 1–3 events/month/instrument pooled across 180 ⇒ est. 2–6k events in the dataset. At a 100bps true edge, hundreds suffice; at 30bps, the pool still clears the 400–800 bar.

---

## 4. Cross-cutting harness work orders
1. Venue-accurate cost model with per-leg maker/taker flags; cost-model version stamped on every run (never pool across versions)
2. Conservative maker-fill simulator (trade-through rule) — prerequisite for P2 only
3. Time-based holdout as a first-class split alongside instrument holdout (P1, P3, P4 all judge on it)
4. Fires-check reports (armed-time, signal count, fill rate) emitted BEFORE P&L for every proposal — §3.2's lesson, made mandatory
5. Fixed-cost regimes (options commissions) invert the size logic per §5.6's own note — that is DEEP RENT's (Lab v4) native habitat; the two labs share no cost model and must never share a bar

## 5. Power ledger
| Proposal | Expected pooled N | Edge size targeted | Bar (from §4 power calc) | Powered? |
|---|---|---|---|---|
| P1 Horizon Ladder | 20k+ | 30–80bps | 400–800 | yes, amply |
| P2 Toll Collector | 2–5k fills | 20–60bps at ~0 cost | 400–800 | yes |
| P3 Dispersion Gate | 5–8k | +9–15bps | 4,000–8,700 | yes, barely — pool everything |
| P4 Fingerprint Router | 20k+ recycled | 15–40bps routed spread | 4,000–8,700 | yes |
| P5 Forced-Flow | 2–6k events | 100–300bps | 400–800 | yes |

## 6. The claim audit (no-trickery clause)
Nothing here games the harness: P2 requires a HARDER fill model than exists today; P3 and P4 pre-register their thresholds and shapes so the tests are confirmatory, not exploratory; P1 and the fee-audit control both include predictions whose failure would damage this document's own thesis. Five proposals, five named kill conditions, one stale parameter found. If all five die honestly, the graveyard gains five well-built graves and the Toll Law's boundaries get measured either way — which is more than 35 patterns ever paid for.
