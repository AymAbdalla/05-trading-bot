# Strategy Lab v4 — DEEP RENT: The LEAPS Swing System
**For: Quant trading bot — options expansion lane (backtest-first)**
**Date: 2026-08-13 | Author: Claude (strategy lab) | Status: UNTESTED SYSTEM, LITERATURE-ANCHORED DOCTRINE**

---

## The concept in one sentence

Rent high-delta exposure on the flattest stretch of the option decay curve — deep ITM, 15–24 months out — timed by documented 4–10 week anomalies, and return the keys (sell) before the rent spikes. Never hold to expiry. Never hold past the decay knee. The exit discipline IS the strategy.

---

## PART 1 — DOCTRINE: The four structural truths of LEAPS swings

### Truth 1 — The theta curve is your landlord, and rent is nonlinear
Time decay for near-the-money options accelerates as expiration approaches; the decay curve of a 24-month option across an 8-week hold is nearly flat, while an 8-week hold on a 3-month option burns a devastating share of premium. A LEAPS swing pays weeks of rent at the long-dated rate for exposure that short-dated buyers pay the panic rate for. **Design consequence: hard decay floor — the position is always sold long before the curve's knee (rule: exit no later than 9–12 months DTE remaining, enforced structurally by the 10-week max hold on 15–24 month entries).**

### Truth 2 — The VRP tax, and how the surface lets you dodge it
Options are on average overpriced: implied volatility exceeds subsequently realized volatility, so unconditional long-premium strategies bleed (the volatility risk premium — Bakshi & Kapadia 2003; Carr & Wu 2009). This is the honest headwind for the entire v4 concept, and the design answers it three ways:

1. **Buy the cheap end of the embedded-leverage spectrum.** Frazzini & Pedersen (Review of Asset Pricing Studies, 2022; NBER 2012): higher embedded leverage → lower risk-adjusted returns, because leverage-constrained investors overpay for return magnification. Long low-embedded-leverage / short high-embedded-leverage earns large abnormal returns (t = 8.6 in equity options). Deep ITM LEAPS (delta 0.70–0.80) are among the LOWEST embedded-leverage options that still lever capital ~2–4× vs. shares. Lottery-ticket OTM weeklies are the tax; deep ITM LEAPS are closer to the rebate.
2. **Minimize the taxed fraction.** Only extrinsic value pays theta and vol-crush. At 0.75 delta and 18 months, extrinsic is typically ~10–20% of premium; the rest is intrinsic — stock-like, untaxed. You are buying mostly stock with a defined-risk wrapper and 2–4× capital efficiency.
3. **Goyal–Saretto entry gate.** Goyal & Saretto (JFE, 2009): sorting on realized-minus-implied vol predicts option returns — long cheap-IV options and short rich-IV options earns significant monthly returns, robust to conditions, industries, liquidity. So DEEP RENT only opens when the underlying's IV is NOT rich: IV rank ≤ 40 AND 1-month realized vol ≥ ~0.85 × entry-tenor implied vol. Buying calls after a euphoric rally at IV rank 80 — the retail default — is structurally banned.

### Truth 3 — Vega is a second engine, not a passenger
LEAPS carry large vega. Entering at low IV rank means an IV regime shift upward pays you even when direction stalls — the dual-engine property. It also defines an exit: if IV rank travels from ≤40 at entry to ≥65 mid-hold, the vega engine has paid out; harvesting it is a legitimate exit even with the directional thesis incomplete.

### Truth 4 — The spread is the fee gate's final boss
LEAPS quoted spreads on single names routinely run 2–10%+ of premium — the options analog of the crypto fee wall, paid twice. Practitioner backtesting guidance: model fills 25–50% of the bid-ask width worse than mid, plus commissions. **Design consequence: universe restricted to penny-increment / high-OI chains (SPY, QQQ, IWM, mega-caps, and liquid $30–90 optionable names), and the backtest charges the pessimistic fill on every leg. A strategy that only survives at mid-fills does not survive.**

**The unifying law — horizon matching:** the ignition signal's documented payoff horizon (4–10 weeks) must sit entirely inside the structure's cheap-rent window. Signals that resolve in days (Lab v3 #1–3) don't justify the spread crossing; theses that need years belong to investors, not this bot. DEEP RENT exists precisely at the horizon where LEAPS geometry is maximally favorable and almost nobody trades systematically.

---

## PART 2 — THE SYSTEM: DEEP RENT

### Module A — CHASSIS (instrument selection)
- Right: calls (long-only native; puts as a later variant)
- Tenor at entry: 15–24 months DTE
- Delta at entry: 0.70–0.80 (deep ITM; the low-embedded-leverage zone)
- Chain quality: penny increment or spread ≤ 2% of premium; OI ≥ 500 at the strike; underlying ADV ≥ $50M
- Skip any entry within 10 calendar days before the underlying's earnings (unless the ignition IS the earnings signal — I1)

### Module B — IGNITION (entry signals; any one fires the trade, all share the vol gate)

**I1 — PEAD Ride (single names).** After a top-decile standardized earnings surprise (SUE) with a gap-and-hold day-one close in the upper half of the day's range, buy the LEAPS on day 2–3 and ride the drift 6–9 weeks, exiting before the next earnings report.
*Evidence status — contested, and the doc says so:* classic estimates put the drift near 2% over 60 trading days for extreme-surprise deciles (Bernard & Thomas 1989), and a large share of the drift concentrates around the subsequent quarterly announcement. But Martineau (2022) argues PEAD vanished from non-microcaps by ~2006, while two 2025 papers counter that it remains alive. DEEP RENT treats I1 as an explicitly disputed hypothesis: the bot's own universe and window becomes the arbiter, and I1's cohort is tracked separately so a dead ignition can be retired without killing the system.

**I2 — 52-Week-High Breakout (single names + ETFs).** George & Hwang (Journal of Finance, 2004): proximity to the 52-week high predicts continuation at multi-week/month horizons — an anchoring effect (traders under-react near a salient reference price). Trigger: first weekly close at a new 52-week high after ≥8 weeks below it, with 20-day volume above its 6-month median. Ride 6–10 weeks.

**I3 — Trend Reclaim (ETF core: SPY/QQQ/IWM).** Price crosses and holds above a rising 100-day MA after ≥4 weeks below it, with 12-1 month momentum non-negative. The boring signal — included because trend is the most durable multi-week tailwind in the time-series literature, and index chains are the cheapest spreads in the universe (the chassis loves what the signal likes).

### Module C — VOL GATE (applies to every ignition; the Goyal–Saretto/VRP shield)
- IV rank (1-year lookback) ≤ 40 at entry
- 1-month realized vol ≥ 0.85 × implied vol at the entry tenor (never pay rich vol)
- VIX not in backwardation-panic (VIX > 1.15 × VIX3M blocks entries — knife-catching guard)
- If the gate blocks an ignition, the signal may still be traded in shares (routes to the v2 equity lane); the vol gate protects the *structure choice*, not the thesis

### Module D — EXIT ENGINE (the "sell it, don't marry it" mandate — six doors out, first one hit wins)
1. **Profit harvest:** premium up +60% → sell half; +100% → flat. (Delta 0.75 with gamma tailwind makes these reachable on a 10–20% underlying move.)
2. **Delta cap:** position delta ≥ 0.90 → convexity spent, option now behaves like stock with worse liquidity → sell; re-enter a fresh 0.75-delta strike only if the ignition re-fires.
3. **Vega payday:** IV rank ≥ 65 after a ≤ 40 entry → the second engine paid; take it.
4. **Thesis stop:** underlying closes below the signal invalidation level (I1: below the earnings gap fill; I2: weekly close back below the prior 52w high; I3: close below the 100-day MA) → sell and salvage. *The structural mercy of LEAPS:* a stopped 8-week hold on an 18-month option returns most of its premium because almost no theta burned — the loss is delta P&L, disciplined by this stop. The same wrong thesis in 2-month calls approaches total loss. This asymmetry between being wrong in LEAPS vs. wrong in short-dated options is the quiet core of the whole design.
5. **Time stop:** 10 weeks, unconditional.
6. **Decay floor / calendar guards:** never hold below 9 months DTE (unreachable if rule 5 works — belt-and-suspenders for bot-logic failure); and exit any deep ITM call before ex-dividend whenever remaining extrinsic < the dividend (American-exercise economics — ignoring this donates the dividend).

### Module E — SUBLET (optional overlay, v4.1, backtest as a separate genome flag)
Sell 30–45 DTE calls at ~0.20–0.25 delta against the LEAPS (the "poor man's covered call" structure) — but framed correctly: **you are long the cheap end and short the expensive end of the Frazzini–Pedersen embedded-leverage spectrum simultaneously**, harvesting VRP on the short leg while the long leg rides the swing.
- Strike: above the LEAPS breakeven; never below the ignition's measured-move target
- Manage: close/roll the short call at 50% profit or 21 DTE
- **Suspension rule (the nuance that saves it):** no sublet during the first 3 weeks after ignition or while 5-day momentum is top-quartile — never cap a drift you just paid to ride. Sublet activates when the move stalls, monetizing the plateau.
- Cost: caps upside per cycle; adds assignment management. The backtest decides whether SUBLET adds or subtracts — both configurations run.

### Module F — SIZING & RISK (capital honesty)
- Max loss per position = premium paid, structurally defined — no margin, no liquidation risk. This is philosophically aligned with the bot's hard-cap doctrine: the worst case is known at entry to the dollar.
- Expected loss per stopped trade ≈ 20–35% of premium (thesis stop + minimal theta), which is the number to size against.
- **Minimum-clip reality:** one contract is the atom. Deep ITM 18-month calls: ~$800–2,500 on liquid $30–90 names; $8,000+ on SPY. A $2k account cannot run this live within a 5% per-trade cap — v4 is a backtest/paper lane until the account can hold 3–5 positions at sane concentration (ballpark $15–25k+, or run the ETF book via IWM/mid-priced ETFs at the low end). Backtesting costs $0 and the research compounds regardless.
- Portfolio: max 5 concurrent rentals; max 2 per sector; ignition cohorts (I1/I2/I3) tracked as separate books.

---

## PART 3 — BACKTEST ENGINEERING (how to test options without options money)

### Stage 1 — Synthetic bridge (free, can start tonight)
Price synthetic LEAPS with Black–Scholes on the existing OHLCV pipeline: IV proxy = VIX-scaled for index ETFs, realized-vol × a calibrated premium multiplier for single names; compute delta/theta/vega from the model; charge the pessimistic spread from Truth 4 as a synthetic cost.
- **What Stage 1 CAN validate:** ignition timing, horizon matching, exit-engine logic, the shares-vs-LEAPS structural comparison, sizing math
- **What it CANNOT validate:** skew/smile, term-structure dynamics, real spreads, early-exercise events — every Stage-1 result is stamped PROVISIONAL, same convention as the graveyard's provisional flag
- **Silent assertion #16 (new):** every DEEP RENT backtest trade runs against two twins — (a) the identical signal traded in shares, (b) a random-entry LEAPS with matched tenor/delta/holding period. Twin (a) isolates whether the *structure* adds value after costs; twin (b) isolates whether the *signal* does. A strategy must beat both.

### Stage 2 — Real chains (funded when Stage 1 survives)
Options data landscape (2026): ORATS — ~25 years of EOD data with a hosted backtester and precomputed analytics, tiers from ~$99/mo; ThetaData — cheaper raw EOD if we build the harness ourselves (we already have one); CBOE DataShop — exchange-direct, institutional pricing (~$500/mo historical); Polygon — raw chains, Greeks are DIY; marketdata.app — 15+ years of historical options prices on 5,000+ tickers with a free access tier, the budget on-ramp for spot-checking Stage-1 assumptions before paying anyone.
Recommended path: free marketdata.app spot-checks → ThetaData or ORATS EOD subscription for the real Stage 2 → never CBOE-tier until live P&L justifies it.

### Modeling rules
- Fills: mid ± 25–50% of quoted spread (worse side), both legs, plus per-contract commission
- American exercise: dividend-capture exercise logic on deep ITM calls; assignment simulation on SUBLET short legs
- Sample weighting: post-2010 heavily weighted (options market structure changed with penny pilots and electronic MM); PEAD cohort additionally split pre/post-2006 to test the Martineau boundary on our own universe

---

## PART 4 — DECAY MONITORS & KILL CONDITIONS
1. **Entry-quality audit:** rolling distribution of entry IV ranks; if fills cluster above 40, the vol gate is broken in code — halt
2. **Theta ledger:** realized time-value bleed per position vs. modeled; divergence > 2× flags the pricing model or the chain quality
3. **Cohort mortality:** I1 (PEAD) judged on its own 24-month rolling expectancy given its contested status; I2/I3 likewise; any ignition retires independently
4. **Structure test:** if the shares-twin (assertion #16a) beats the LEAPS book after costs across a full cohort, the honest conclusion is that the account should trade shares — the doc's job is to make that verdict impossible to hide
5. **Book-level:** two consecutive quarters of negative expectancy after costs → v4 to graveyard with full autopsy

---

## PART 5 — What's actually new here (claim audit)
- LEAPS as buy-and-hold stock replacement: known. PMCC as income: known. PEAD, 52w-high, trend: published.
- **The synthesis that is genuinely not in the retail playbook:** a systematic, rules-only 4–10 week LEAPS *rotation* whose instrument selection is dictated by the embedded-leverage literature (buy the cheap end of the surface), whose entries are gated by the Goyal–Saretto vol-mispricing result, whose holding window is derived from decay-curve geometry rather than conviction, whose exits include a vega-payday door nobody codes, and whose harness forces every trade to beat both a shares-twin and a random-LEAPS-twin before it counts. Each brick is documented; the building is not. That's where "never attempted" honestly lives — and the backtest, not the claim, gets the last word.

## Primary sources
- Frazzini & Pedersen (2022), "Embedded Leverage," *Review of Asset Pricing Studies* 12(1) (NBER WP 18558, 2012)
- Goyal & Saretto (2009), "Cross-Section of Option Returns and Volatility," *Journal of Financial Economics* 94
- Bakshi & Kapadia (2003), "Delta-Hedged Gains and the Negative Market Volatility Risk Premium," *RFS*; Carr & Wu (2009), "Variance Risk Premiums," *RFS*
- Bernard & Thomas (1989), "Post-Earnings-Announcement Drift," *Journal of Accounting Research*; Martineau (2022), "Rest in Peace Post-Earnings Announcement Drift"; 2025 rebuttal literature (UCLA Anderson Review coverage, 2026)
- George & Hwang (2004), "The 52-Week High and Momentum Investing," *Journal of Finance* 59
- McLean & Pontiff (2016) decay framework carried over from Lab v3 — every anchor above is assumed smaller live than published
