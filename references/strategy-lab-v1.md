# Strategy Lab — 24 Untested Hypotheses

**Companion to:** `trading-knowledge-base.md` v1.1
**Author:** Compiled for Aym Abdalla
**Version:** 1.0
**Compiled:** 12 August 2026
**Purpose:** Backtest candidates only. Nothing here has been tested by anyone.

---

## 0. Read this before anything else

### 0.1 What these are and are not

These are **24 structured hypotheses**, written to be backtestable. Not one has been tested. I have no evidence any of them work. Several are deliberately built on ideas the published literature says *fail* — inverted, gated, or recombined to see whether the failure was in the idea or in the framing.

The design goal was **creativity constrained by executability**: every strategy has a precise trigger, a precise exit, a sizing rule, an estimated trade frequency, a named failure mode, and a falsifier. If you cannot code it from the spec, the spec is wrong and that is my error.

**Expected outcome, stated honestly up front:** if these behave like any other batch of 24 fresh trading ideas, roughly 18–20 will fail outright, 3–5 will look promising in-sample and die out-of-sample, and 0–2 might survive. That is the normal yield. Plan the testing budget around it.

### 0.2 The multiple-comparisons problem I just handed you

Handing someone 24 strategies to backtest is manufacturing a data-snooping problem. Say you test all 24 at p < 0.05. Under the null that **none** of them work, the expected number that clear the bar by chance alone is 24 × 0.05 ≈ **1.2**. Add parameter variants — say each gets 8 configurations tested — and you are running ~192 hypotheses, with ~9.6 false positives expected.

**Corrections, in increasing order of rigor:**

| Method | Adjusted bar for 24 tests | Notes |
|---|---|---|
| None | p < 0.05 | Guarantees false positives |
| Bonferroni | p < 0.0021 | Very conservative; will reject real weak edges |
| Benjamini-Hochberg (FDR 10%) | Rank-dependent | The practical default for a batch like this |
| Deflated Sharpe / White's Reality Check | Bootstrap-based | Sullivan, Timmermann & White (1999); the correct tool, most work |

**Minimum protocol I would insist on for this batch:**

1. **Declare the parameter grid before testing.** Every configuration you try counts as a hypothesis. Log the count.
2. **Split the data first.** Train on the earliest 50%, validate on the next 25%, hold out the final 25% and do not look at it until you have committed to a shortlist.
3. **Test all 24 with identical friction assumptions** so the comparison is fair.
4. **Apply Benjamini-Hochberg** across the full set of tested configurations, not just the ones you liked.
5. **Everything surviving still goes through the existing Quant lifecycle** — backtest bar PF ≥ 1.3, DD ≤ 15%, ≥150 trades over 9 months; then the shadow gate. This document does not create an exception to that.
6. **Log every rejection in the graveyard with its hypothesis count.** The count is what makes the correction possible later.

### 0.3 Frequency was a design constraint

You asked for rules loose enough to actually fire. Every spec carries an **estimated signal frequency**. Where I had to choose between a tighter filter and a testable sample, I chose the sample and put the tightening in an optional "hardening" note.

Rule of thumb used throughout: a strategy that generates fewer than ~50 signals over a 3-year backtest cannot be evaluated. If a filter takes you below that, the filter is wrong for research even if it would be right for trading. **Test loose, then tighten and watch what happens to the metrics** — that curve is itself informative. If tightening the filter improves the edge monotonically, you have something. If it improves and then collapses, you overfit.

### 0.4 Spec format

```
ID · Name
Class · Risk tier
Thesis          — one sentence
Novelty         — what makes this different from the standard version
Universe        — what it trades
Signal          — the precise trigger
Entry           — how you get in
Exit            — how you get out (all paths)
Sizing          — position size rule
Frequency       — estimated signals per period
Failure mode    — how it breaks
Falsifier       — the specific result that kills it
Data            — what you need to test it
```

### 0.5 Risk tier definitions

| Tier | Risk/trade | Concurrent | Return profile | Acceptable max DD |
|---|---|---|---|---|
| **Conservative** | 0.25%–0.5% | Up to 8 | High win rate, low payoff, slow | 8% |
| **Moderate** | 0.5%–1.0% | Up to 5 | Balanced | 15% |
| **Aggressive** | 1.0%–2.0% | Up to 3 | Low win rate, high payoff, lumpy | 30% |

**Aggressive here means aggressive expected variance, not absent risk control.** Every strategy has a stop, a size cap, and a kill condition. There is no un-stopped naked short premium in this document — not because it cannot make money, but because an untested strategy with unbounded loss is not a research candidate, it is a solvency event waiting for a bad Tuesday.

### 0.6 Friction assumptions to use throughout

Do not use a global constant. Minimum modeling standard for these tests:

- **Equities spread:** per-symbol, from actual quote data. Half-penny minimum for tick-constrained names post-Nov 2025 (see KB §1.3).
- **Equities commission + fees:** per-share commission plus SEC Section 31 and FINRA TAF on sales.
- **Options:** per-contract commission plus **half the quoted bid-ask spread** as slippage, minimum. Retail option round-trip spreads have been estimated around 8% of option value — if your model is materially cheaper than that, justify it.
- **Crypto:** taker fee unless you can prove maker fill; plus spread; plus a slippage term scaling with order size relative to order-book depth.
- **Shorts:** borrow cost, locate availability, and SSR logic. No exceptions.
- **Halts:** no fills during a halt. Reopen at the auction price, not the pre-halt price.

---
## 1. Day trading strategies (equities and ETFs)

### D1 · The Midday Liquidity Tax Dodge
**Class:** Day · **Risk tier:** Conservative

**Thesis.** Almost every retail intraday strategy trades the open and the close, where spread and volatility are structurally highest. Signal quality may be worse midday, but cost is *much* worse at the edges. The net could favor the boring window.

**Novelty.** This is not a signal idea, it is a **cost-arbitrage** idea. It takes any mediocre mean-reversion signal and asks whether the U-shaped cost curve (KB §6.1) is large enough that the same signal flips from unprofitable at 9:45 to profitable at 12:15. Nobody tests this because midday is considered dead time. Dead time is cheap time.

**Universe.** S&P 500 constituents with 20-day median dollar volume > $50M, price > $10, tick-constrained (half-penny quoting) preferred.

**Signal.** Between **11:15 and 14:15 ET only**:
- Price is more than **1.2× intraday σ** below session VWAP (compute σ from the distribution of (price − VWAP) over the prior 20 sessions at matched time-of-day)
- 5-minute RVOL between **0.6 and 1.8** (not dead, not an event)
- Stock has **not** triggered SSR today
- ATR(14) daily is between the 20th and 80th percentile of its own 1-year range (avoid both dead and berserk names)

**Entry.** Resting limit at the midpoint, good for 90 seconds, then reprice once to the near touch, then cancel.

**Exit.** Whichever comes first: (a) touch of session VWAP, (b) 1.0× intraday σ adverse move from entry, (c) **15:45 ET hard flat**.

**Sizing.** 0.4% risk. Stop distance = 1.0σ. Max 8 concurrent, max 2 per GICS sector.

**Frequency.** Estimated 6–15 signals/day across a 500-name universe; roughly 1,800–3,800/year. Ample.

**Failure mode.** Midday mean reversion may simply be weaker by exactly as much as it is cheaper, leaving a wash. Also: the mid-session lull is where a genuine trend day quietly grinds against you all afternoon.

**Falsifier.** Run the identical signal in three time buckets (9:35–10:35, 11:15–14:15, 15:00–15:55). If net expectancy is not **highest** in the middle bucket, the entire premise is dead. That comparison is the actual experiment.

**Data.** 1-min bars with quotes, per-symbol spread history, SSR flags, session VWAP.

---

### D2 · Failed-Breakout Harvest (inverted ORB)
**Class:** Day · **Risk tier:** Moderate

**Thesis.** The published falsification study found opening-range breakout fails after friction, with the *best* variant at 55.5% win rate still not significant (KB §6.2). If ORB is a coin flip that costs money, the interesting trade is not the breakout — it is the **failure** of the breakout.

**Novelty.** Most "failed breakout" trades are discretionary pattern-reading. This makes the failure a *timed, mechanical* event and adds the condition that almost nobody screens for: **the breakout must be unsupported by a news catalyst.** A breakout on real news should be respected. A breakout on nothing is a liquidity event.

**Universe.** Russell 1000, price > $5, 20-day median dollar volume > $20M.

**Signal.** Define OR = 9:30–9:45 ET range.
- Price breaks OR high (or low) between 9:45 and 11:00
- Break extends at least **0.25 × OR width** beyond the boundary
- **No news catalyst** within the prior 24h (no earnings, no 8-K, no analyst action, no index event) — this is the key gate
- Break happens on RVOL **below** the day's opening RVOL (i.e., the breakout is on *fading* participation)
- Price then closes a 5-minute bar back **inside** the opening range

**Entry.** Short (or long, for a failed downside break) on the close of the first 5-minute bar that reenters the OR.

**Exit.** Target = **opposite side of the opening range**. Stop = the extreme of the failed breakout excursion + 0.1 × OR. Time stop 15:30 ET.

**Sizing.** 0.8% risk. Max 3 concurrent. Shorts require confirmed borrow and no SSR.

**Frequency.** Estimated 3–8 signals/day across Russell 1000; ~900–2,000/year. Very testable.

**Failure mode.** Trend days. On a strong trend day the "failed" breakout reasserts and runs, and the stop at the excursion extreme is exactly where the resumption starts. Expect clustered losses on trend days — check whether a market-level trend filter (SPY above/below its own OR) fixes it or destroys the sample.

**Falsifier.** If the news-catalyst gate does not materially improve results versus the ungated version, the central claim is wrong and this reduces to generic fading.

**Data.** 1-min bars, corporate action and news timestamps, RVOL, borrow, SSR.

---

### D3 · Halt-Reopen Auction Fade
**Class:** Day · **Risk tier:** Aggressive

**Thesis.** A LULD halt forces a 5-minute pause and a reopening auction. The reopening print is set by a compressed, panicked order book with no continuous price discovery for five minutes. That print is likely to overshoot.

**Novelty.** Almost nobody backtests halt reopens, because standard OHLCV data does not mark halts and most retail platforms do not surface them cleanly. That data gap *is* the opportunity — an under-researched event with a mechanically-forced structure. Also genuinely novel: **classify the halt type first.** A T1 news halt reopens on new information (respect it). A LULD volatility halt reopens on no new information (fade it). Treating those the same is why this looks like noise to most people.

**Universe.** Any NMS security, price > $1, that experiences a **LULD** halt (halt code, not news code) between 10:00 and 15:00 ET.

**Signal.**
- LULD halt occurs (not T1, not T12, not regulatory)
- It is the **1st or 2nd** halt of the day in that name (3rd+ halts indicate a genuine repricing regime — excluded)
- Reopening auction price is **more than 8%** away from the pre-halt 5-minute VWAP
- Reopen print size is at least 2× the name's median 5-minute volume

**Entry.** Fade the reopen direction, entering **60 seconds after** the reopen (never at the auction — you will not get that print). Marketable limit capped at 1.5% through the touch.

**Exit.** Target = **50% retracement** of the halt gap (reopen price back toward pre-halt VWAP). Stop = 1.5× the entry-to-reopen distance. Hard time stop **20 minutes**. Hard flat 15:45.

**Sizing.** 1.2% risk, capped at 1.5% of the name's median 5-min dollar volume. Max 2 concurrent. **Assume the stop does not work** — a second halt can trigger against you mid-position, so size to survive a 3× stop excursion.

**Frequency.** LULD halts are common — dozens per day market-wide, though most are in tiny names. With the price and volume filters, estimate 2–6 qualifying events/day; ~500–1,500/year. Plenty.

**Failure mode.** Cascading halts. If the name halts again while you are positioned, you cannot exit at any price and the reopen can be far worse. This is the single most likely way this strategy produces a loss much larger than its stop implies. It is also why this is tiered aggressive.

**Falsifier.** Split results by halt type. If LULD-halt fades and news-halt fades perform the same, the classification premise is wrong and there is no edge here beyond generic gap fading.

**Data.** **Halt tape with halt reason codes** (this is the hard part — UTP/CTA halt feeds or a vendor that carries them), auction prints, 1-min bars.

**Hardening note.** If the sample is large enough, add: only fade when the reopen direction is *against* the day's prevailing SPY direction. Expect this to roughly halve the sample.

---

### D4 · SSR Asymmetry Long
**Class:** Day · **Risk tier:** Moderate

**Thesis.** When Rule 201 SSR triggers (a 10% decline from prior close), short sellers can no longer hit the bid — they must post above it. That is a **mechanical, temporary, one-sided reduction in selling pressure** that lasts the rest of the day and all of the next. If any buying interest shows up during that window, it faces an artificially thinned opposition.

**Novelty.** SSR is universally discussed as a *warning* ("this thing is dumping") and almost never as a *structural asymmetry to trade with*. The nuance that makes it testable: SSR's effect should be strongest in names where short flow is normally a large share of volume. So the signal is not "SSR fired" — it is **SSR fired on a name with high normal short participation**.

**Universe.** Price > $2, 20-day median dollar volume > $10M, short interest > 8% of float, and off-exchange short volume ratio (FINRA daily short volume data) in the top tercile over the past 20 days.

**Signal.**
- SSR triggers today
- Price stabilizes: two consecutive 5-minute bars with higher lows, occurring **after 11:00 ET** (skip the morning capitulation)
- Second bar closes above the first bar's midpoint
- RVOL still > 1.5 (interest hasn't died)

**Entry.** Long on the close of the confirming bar.

**Exit.** Three-part: (a) one-third at +1.0 ATR(5-min, 14), (b) one-third at prior day's close, (c) final third trailed on a 10-period low of 5-min bars. Hard flat at 15:50 **or** carry the final third overnight into day 2 of SSR (test both — day 2 still has the restriction active, which is the whole thesis).

**Sizing.** 0.7% risk. Max 4 concurrent.

**Frequency.** SSR triggers on hundreds of names per year; with these filters estimate 200–600 qualifying setups/year. Solid.

**Failure mode.** SSR fires because something is genuinely wrong. You are systematically buying names on their worst day. The strategy's whole survival depends on the stabilization confirmation being a real filter rather than a delay before more selling.

**Falsifier.** Compare against a control group of names that fell 8–9.9% (just missing the SSR trigger) with identical stabilization confirmation. **If the SSR group does not outperform the near-miss group, the mechanism claim is false** and you are just buying dips. This control comparison is the actual test and it is clean — the 10% threshold is a sharp regression discontinuity.

**Data.** SSR trigger list, FINRA daily short volume, short interest, 5-min bars.

---

### D5 · Retail Session Fingerprint (post-PDT-repeal)
**Class:** Day · **Risk tier:** Moderate

**Thesis.** The PDT rule was eliminated 4 June 2026 (KB §2.2). Sub-$25k accounts can now day trade freely for the first time since 2001. That is a **composition change in intraday order flow** — more small, unsophisticated, attention-driven, odd-lot-sized flow. If retail herding predicts negative forward returns (KB §10.4), then a proxy for *concentrated small-account participation* should be a fade signal.

**Novelty.** This is a strategy that **could not have existed before June 2026**. It trades a regulatory regime change directly. The proxy is the clever part: since the Nov 2025 round-lot redefinition, odd-lot data is richer, and small-account flow skews odd-lot. So use **odd-lot trade count as a share of total trade count**, time-of-day normalized, as the retail-intensity proxy.

**Universe.** Price $5–$150 (the retail sweet spot; above ~$150 fractional/odd-lot behavior changes), 20-day median dollar volume > $15M, has listed options.

**Signal.**
- Odd-lot trade count share is in the **top 5%** of that name's own trailing 60-day distribution, measured over a rolling 30-minute window, time-of-day matched
- Price is up more than **3%** on the day at signal time
- Signal fires between 10:30 and 14:30
- Name appeared in the top 50 by social mention volume in the prior 24h (optional second gate — test with and without)

**Entry.** Short on signal, or buy a defined-risk put debit spread if borrow is unavailable. Marketable limit.

**Exit.** Target = VWAP. Stop = day's high + 0.5 ATR. Hard flat 15:45. Also test a 3-day swing variant given the −4.7% 20-day abnormal return finding.

**Sizing.** 0.8% risk. Max 3 concurrent. SSR-aware.

**Frequency.** Estimate 2–6/day; ~500–1,500/year. But note: **the usable sample starts June 2026**, so you have ~2 months of post-regime data as of this writing. Backtest the pre-June period as a control and expect the effect, if real, to *strengthen* after June 4.

**Failure mode.** The odd-lot proxy may not track small-account flow at all — institutions also use odd lots for algo slicing, and that has grown. If odd-lot share is mostly institutional algos, the signal is measuring the opposite of what you think.

**Falsifier.** Regression discontinuity at 4 June 2026. If signal efficacy shows **no structural break** at the PDT repeal date, the causal story is wrong. (Caveat: two months is a thin post-period. This is a hypothesis to revisit in 2027, not to settle now.)

**Data.** Odd-lot trade data (post-Nov 2025 dissemination), tick data with trade sizes, social mention counts, borrow.

---

### D6 · The Second-Day Decay Curve
**Class:** Day · **Risk tier:** Aggressive

**Thesis.** Retail-tracked scanners are full of "second day play" setups — trade the day after a big mover. But the standard version treats day 2 as a single binary. The real structure is a **decay curve**: the fade probability should depend on how much of the day-1 move was retail-driven and how much float rotated.

**Novelty.** Formalize **float rotation** — cumulative day-1 volume ÷ free float — as a continuous state variable rather than a screening filter, and interact it with day-1 gain. The hypothesis is that fade probability is a function of *rotation × gain*, not either alone. A stock that gained 80% on 0.3× rotation has a different day 2 than one that gained 80% on 4× rotation: the second has exhausted its available supply and its holders are all underwater buyers from yesterday.

**Universe.** Price $1–$30, free float < 50M shares, day-1 gain > 30%, day-1 dollar volume > $25M.

**Signal.** Compute on day 2 pre-market:
- `RotationScore = day1_volume / free_float`
- `ExhaustionScore = day1_gain_pct × log(1 + RotationScore)`
- Signal fires when **ExhaustionScore > threshold** (start with the 70th percentile of the historical distribution, then sweep the threshold — the shape of the response curve is the finding)
- Day 2 opens **green or flat** (a red open means the fade already happened without you)
- No new dilutive filing overnight (S-1, 424B, ATM prospectus) — **if there IS one, that is a separate and probably stronger short signal; tag it and test it as variant D6b**

**Entry.** Short on the first 5-minute bar that closes below the pre-market low, entered after 9:45.

**Exit.** Target = day-1 close (the full round trip). Stop = day-2 high + 0.3 ATR. Time stop 14:00.

**Sizing.** 1.5% risk. **Max 1 concurrent — this is the highest-risk strategy in the document.** Hard requirements: confirmed borrow, borrow rate under 100% annualized, and explicit halt simulation. If any of those three cannot be modeled, do not test this; the result will be meaningless.

**Frequency.** Estimate 1–4 setups/day in active markets, far fewer in quiet ones; ~250–800/year, heavily clustered in speculative regimes.

**Failure mode.** Everything in KB §6.4. Squeeze continuation on day 2 is common, borrow disappears exactly when you want it, SSR is likely active, LULD halts fire repeatedly, and the historical dataset is missing the delisted names. **This is the strategy most likely to produce a beautiful backtest and an untradeable reality.**

**Falsifier.** Plot realized edge against ExhaustionScore deciles. **If the relationship is not monotonic**, the interaction hypothesis is dead and you are just shorting big movers, which is a well-known way to get run over.

**Data.** Free float (survivorship-complete, including delisted names), intraday volume, SEC filing timestamps, borrow rates, halt tape.

---
## 2. Swing trading strategies (equities and ETFs)

### S1 · AVWAP Confluence Node
**Class:** Swing · **Risk tier:** Conservative

**Thesis.** Anchored VWAP from a significant event tells you the average price paid by everyone who transacted since that event (KB §5.6.3). When **multiple independent anchors converge** at the same price, that price is simultaneously break-even for several distinct cohorts of holders. Convergence should concentrate decision-making.

**Novelty.** AVWAP is usually used one anchor at a time and read discretionarily. This makes convergence a **measurable, continuous variable** — count how many independent AVWAPs sit within a tight band — and trades the node rather than any single line. To my knowledge nobody publishes a systematic multi-anchor confluence test.

**Universe.** Russell 1000, price > $10, options-listed.

**Signal.** Maintain four AVWAPs per name, anchored at: (1) last earnings date, (2) 52-week high, (3) 52-week low, (4) the highest-volume single day of the trailing 6 months.
- **Node condition:** at least **3 of 4** AVWAPs sit within a band of width **0.5 × ATR(20)**
- Current price is within **0.75 × ATR(20)** of the node and approaching it
- The 50-day SMA is rising (long-only version; run a short mirror separately)
- Price is above its 200-day SMA

**Entry.** Limit at the node price, good-til-cancelled for 5 sessions, then cancel.

**Exit.** Stop = node − 1.5 × ATR(20). Target = node + 3.0 × ATR(20). Trail after +1.5 ATR using a 10-day low. Time stop 30 sessions.

**Sizing.** 0.5% risk. Max 8 concurrent, max 2 per sector.

**Frequency.** Nodes are not rare — estimate 20–60 qualifying setups/month across the Russell 1000; ~250–700/year. Loosen to 2-of-4 anchors if the sample disappoints (and report both).

**Failure mode.** Four anchors on the same instrument are not independent — in a quiet, trending stock they can all cluster mechanically without meaning anything. **Test whether convergence is more common in low-volatility names**; if so, you have built a volatility filter wearing a costume.

**Falsifier.** Compare 3-of-4 nodes to a matched control of single-AVWAP touches. If confluence adds nothing over a single anchor, the whole premise fails.

**Data.** Daily OHLCV, earnings dates, intraday volume for AVWAP construction.

---

### S2 · Breadth Divergence Rotation (RSP/SPY)
**Class:** Swing · **Risk tier:** Conservative

**Thesis.** The S&P 500 top-10 weight was around 39% in mid-2026 (KB §3.6). When cap-weighted SPY rises while equal-weighted RSP does not, the advance is narrow and concentrated. Narrow advances have historically been fragile — but rather than trying to time the top, use the divergence as a **regime router** that decides which of your other strategies gets capital.

**Novelty.** This is a **meta-strategy**, not a trade. Most breadth work tries to predict market direction, which is hard. This instead predicts *which strategy family should work* — narrow markets favor momentum in leaders and punish mean reversion in laggards; broad markets do the opposite. It is a capital allocator, and it is the one idea here I would be most curious to see tested, because if it works it makes everything else in this document better.

**Universe.** SPY, RSP, and the routing targets (your other strategies).

**Signal.** Compute `BreadthRatio = SPY_total_return / RSP_total_return` over trailing 21 sessions.
- **Narrow regime:** BreadthRatio in the top 25% of its own trailing 3-year distribution
- **Broad regime:** bottom 25%
- **Neutral:** middle 50%

**Entry / routing.**
- Narrow regime → allocate to momentum/trend strategies; halve allocation to mean-reversion strategies
- Broad regime → reverse the above
- Neutral → equal weight
- Optional direct expression: long RSP / short SPY in the top decile of narrowness (beta-matched, dollar-neutral)

**Exit.** Regime persists until the ratio crosses back into the neutral band. Rebalance weekly, not daily (this reduces whipsaw and transaction costs).

**Sizing.** As a router: no direct position, it scales other strategies between 0.5× and 1.5×. As a direct trade: 0.4% risk, single position.

**Frequency.** Regime changes maybe 8–20 times a year. As a router this is continuous, so the effective sample is every trade every routed strategy makes.

**Failure mode.** Narrowness can persist for years — it did through much of 2023–2026. A strategy that fades narrowness gets destroyed in a secular concentration trend. **The router version is much safer than the direct version** precisely because it never fights the trend, it only reweights.

**Falsifier.** Take any two of your other strategies with opposite character (say D1 mean reversion and S3 trend). If their relative performance is **uncorrelated with the breadth regime**, the router has no information and should be discarded.

**Data.** Daily SPY and RSP total returns. Trivially available. **This is the cheapest test in the document — do it first.**

---

### S3 · Post-Earnings Drift with Options Confirmation
**Class:** Swing · **Risk tier:** Moderate

**Thesis.** Post-earnings-announcement drift is among the most robustly documented anomalies in finance (KB §6.8). It has also been widely known for four decades, so the naive version is likely arbitraged thin. Adding a **second, independent confirmation from the options market** may isolate the subset of surprises where informed money agrees with the drift direction.

**Novelty.** The confirmation signal is not options volume (noisy, retail-dominated) but the **change in implied skew across the event**. If the surprise is positive and post-event skew *flattens* (put demand falls relative to calls), the options market is ratifying the surprise. If the surprise is positive but skew *steepens*, someone is buying protection into good news — that is a disagreement, and it should predict weaker or reversed drift.

**Universe.** Russell 1000, options-listed, 30-day option volume > 1,000 contracts/day.

**Signal.** Within 2 sessions after an earnings release:
- Earnings surprise (SUE or analyst-estimate-based) in the **top or bottom quintile**
- Day-1 price reaction directionally agrees with the surprise sign
- **Skew confirmation:** 25-delta put IV minus 25-delta call IV, 30-day tenor, measured T+1 versus T−1. For a positive surprise, require skew to **narrow**; for negative, require it to **widen**
- Realized IV crush occurred (30-day IV fell at least 20% from pre-event level) — confirms it was a real event, not a leak

**Entry.** Enter on the close of T+2 in the direction of the surprise. Shares, not options — the drift is slow and theta would eat it.

**Exit.** Hold **45 calendar days** or until the next earnings, whichever is first. Stop = 2.0 × ATR(20). No profit target — drift strategies die from early exits.

**Sizing.** 0.7% risk. Max 5 concurrent, max 2 per sector, max 3 entering in any single earnings week (avoid a season-concentrated book).

**Frequency.** Roughly 1,000 Russell 1000 earnings events per quarter; top/bottom quintile is ~400; skew confirmation might cut that by half. Estimate **150–250 signals per quarter**, ~600–1,000/year. Very healthy sample.

**Failure mode.** Drift has weakened in recent decades in some studies, and 45-day holds accumulate market beta — a broad drawdown will hit the whole book at once regardless of surprise quality. Consider a beta hedge variant.

**Falsifier.** Run three arms: skew-confirms, skew-disagrees, no skew filter. **If the confirm arm does not beat the no-filter arm, the skew idea contributes nothing.** If the disagree arm underperforms the no-filter arm, that is bonus evidence the mechanism is real.

**Data.** Earnings dates and estimates, option surface (25-delta IVs by tenor), daily bars.

---

### S4 · Attention Decay Fade
**Class:** Swing · **Risk tier:** Moderate

**Thesis.** Barber, Huang, Odean & Schwarz found average 20-day abnormal returns of **−4.7%** for the top stocks purchased daily by Robinhood users, around −3% at 5 days and about −6% for extreme herding (KB §10.4). If that reversal is real and persistent, it is directly tradeable with a schedule.

**Novelty.** Two things. First, the paper measures *when the crowd arrives*; this strategy is explicitly about entering **after** peak attention rather than during it — trading the documented decay rather than the spike. Second, the entry timing is derived from the published decay curve rather than guessed: the 5-day figure is smaller than the 20-day figure, implying the drift builds, so a **delayed entry** may capture the steeper part with less exposure to the initial squeeze.

**Universe.** Price > $3, market cap > $300M, options-listed (for a defined-risk expression when borrow is unavailable), 20-day median dollar volume > $10M.

**Signal.** Build a daily attention rank from whatever proxy you have — social mention counts, Google Trends, unusual options volume, or odd-lot trade count share (see D5).
- Name enters the **top 20 by attention** on day 0
- Attention on day 0 is at least **4× its own 30-day median**
- Price rose at least **15%** over the 5 sessions ending day 0
- **Enter on day +3, not day 0** (let the initial momentum exhaust)
- Attention on day +3 has **fallen** at least 40% from the day-0 peak — this is the decay confirmation and it is the core of the idea

**Entry.** Short shares, or a put debit spread 30–45 DTE at roughly 40-delta/20-delta if borrow is unavailable or expensive.

**Exit.** Hold **17 sessions** (targeting the documented 20-day window from day 0). Stop = day-0 high. Take profit at −8% or the 50-day SMA, whichever comes first.

**Sizing.** 0.8% risk. Max 4 concurrent. Never more than 1 position in names that appear correlated by narrative (all AI names are one position, not four — KB §8.5).

**Frequency.** Top-20 attention names refresh constantly; with the 4× and 15% gates, estimate 3–10 qualifying setups/week, ~200–500/year.

**Failure mode.** The catastrophic one: shorting into a squeeze that has not finished. The day-0-high stop can be very far away. Also, the Robinhood finding is from 2018–2020 data; Huang & Nolan report **no reversal** in their WSB sample (KB §10.2) — the effect is genuinely contested and may be period-specific.

**Falsifier.** Test entry days 0, 1, 3, 5, and 10 as separate arms. **If day 0 is best, the "decay" framing is wrong** and this is just momentum-fading. If the curve peaks around day 3–5, the framing holds.

**Data.** An attention proxy time series (this is the binding constraint — Robintrack is dead; you would need social mention data, options volume, or the odd-lot proxy), daily bars, borrow.

---

### S5 · Index Reconstitution Shadow
**Class:** Swing · **Risk tier:** Conservative

**Thesis.** FTSE Russell publishes **preliminary** add/delete lists weeks before the June reconstitution takes effect (KB §3.1). Index funds must trade at the reconstitution close. That is a known, dated, size-certain flow — one of the few genuinely predictable liquidity events in equities.

**Novelty.** The straight front-run is crowded and well documented. The nuance here is to trade the **band-edge uncertainty** instead: FTSE Russell applies percentile *banding* around breakpoints, meaning some names near the boundary are ambiguous until final lists publish. Names that the market prices as *certain* adds have the flow already in them. Names that are **ambiguous and then resolve as adds** should have the largest unpriced flow. Trade the resolution, not the announcement.

**Universe.** Names within ±10% of the Russell 1000/2000 breakpoint or the Russell 2000/Microcap breakpoint at rank day (30 April).

**Signal.**
- Name is inside the banding-ambiguous zone at rank day
- Between preliminary list publication and final list, the name is **confirmed** as an add to the larger index
- Estimated index demand (index AUM × weight) exceeds **3× the name's 20-day median daily dollar volume** — this ratio is the real signal, not the add itself

**Entry.** Enter long on the session after final confirmation, scaling in over 3 days to reduce your own impact.

**Exit.** **Exit into the reconstitution close auction** (the effective date). Test two variants: (a) full exit at the auction, (b) hold 5 sessions past for the documented post-reconstitution reversal.

**Sizing.** 0.4% risk. Max 6 concurrent. Cap position at 3% of the name's median daily dollar volume.

**Frequency.** Low — this is once a year in June, plus quarterly IPO additions. Perhaps **20–60 qualifying names per year.** Over a 10-year backtest that is 200–600 observations, which is adequate, but a 3-year backtest will not be. **Flagging this explicitly: this is the one strategy in the document where you need a long history rather than a deep universe.**

**Failure mode.** The trade is well known and increasingly pre-positioned; the edge has likely compressed over time. Test for **monotonic decay across years** — if the 2015 cohort works and the 2024 cohort does not, that is the answer.

**Falsifier.** Compare ambiguous-then-confirmed adds against unambiguous adds. If they perform the same, the banding-uncertainty premise is wrong.

**Data.** FTSE Russell preliminary and final lists by year (obtainable from LSEG), index AUM estimates, daily bars. Needs 8+ years.

---

### S6 · Volatility-Squeeze Direction Deferral
**Class:** Swing · **Risk tier:** Aggressive

**Thesis.** Volatility clustering is among the most robust findings in empirical finance. A Bollinger/Keltner squeeze reliably forecasts **volatility expansion** and tells you nothing about **direction** (KB §5.8). Most traders guess the direction and lose. The correct response to a forecast you only half-have is to take the half you have.

**Novelty.** Instead of guessing direction, **defer it**: enter a straddle-like structure at the squeeze and let the market pick the side, then convert into a directional position once resolved. In shares-only form, that means a **bracket order pair** where the first fill defines the trade and the opposite order becomes the stop. The genuinely novel part is the **squeeze-age condition** — squeeze quality should depend on how long compression has persisted relative to that name's own history, not on an absolute bandwidth threshold. An old squeeze in a normally-volatile name is a coiled spring; a young squeeze in a sleepy name is nothing.

**Universe.** Russell 1000 plus liquid sector ETFs. Price > $10, options-listed.

**Signal.**
- Bollinger(20,2) bandwidth is in the **bottom 15%** of that name's own trailing 2-year distribution
- Bollinger bands are **inside** Keltner(20, 2×ATR10) channels (the classic squeeze condition)
- **SqueezeAge** = consecutive sessions in that state, and SqueezeAge is in the **top 30%** of the name's own historical squeeze durations — this is the new variable
- Name's 1-year realized volatility is **above** the universe median (a squeeze matters more in something that normally moves)

**Entry.** Place a stop-buy at (upper Bollinger + 0.25 ATR) and a stop-sell at (lower Bollinger − 0.25 ATR), both good for **10 sessions**. First fill wins; cancel the other and convert it into the stop.

**Exit.** Stop = the opposite bracket level. Target = 3.5 × the squeeze range (upper minus lower band at signal). Trail after 2× using a 5-day low/high. Time stop 25 sessions.

**Sizing.** 1.2% risk based on bracket-to-bracket distance. Max 3 concurrent. **Expect a low win rate (30–40%) by construction** — this is a convexity strategy and it must be evaluated on expectancy, never on hit rate (KB §8.2).

**Frequency.** Squeezes are common — estimate 30–80 qualifying setups/month before the SqueezeAge filter, 10–25 after. ~150–350/year. Good.

**Failure mode.** Whipsaw. Squeeze breaks that immediately reverse fill you at the extreme and stop you at the opposite extreme, for a full bracket-width loss. Volatility expansion is a real forecast, but expansion in both directions in sequence is the specific way this bleeds.

**Falsifier.** Sort results by SqueezeAge decile. **If there is no relationship between squeeze duration and subsequent edge, the novel variable is worthless** and this reduces to a standard squeeze play — which is widely traded and probably thin.

**Data.** Daily OHLCV, 2 years of history per name for the percentile distributions.

---
## 3. Crypto strategies

Notes that apply to all six: crypto has no settlement cycle, no halts, no SSR, and no market-wide circuit breakers, so the equity guardrails do not transfer. What it does have that equities lack: **24/7 trading, a public funding-rate sentiment signal, and on-chain data.** Strategies below lean on exactly those three, because that is where the non-transferable edge would live.

Perpetual futures represent roughly **93% of crypto futures volume**, and the standard funding interval is **8 hours**. For scale: 0.01% per 8h ≈ **10.95% APR**; 0.03% per 8h ≈ **32.85% APR**. Funding is the largest variable cost after fees for multi-day holders — and it is also the most live sentiment indicator available in any market.

**Quant v1 is spot-only and long-only.** Where a strategy below needs perps, I have marked it and given a spot-expressible variant where one exists.

---

### C1 · Funding-Rate Sentiment Overlay (spot-expressible)
**Class:** Crypto · **Risk tier:** Conservative

**Thesis.** The perpetual funding rate is a real-money, continuously-updated measure of directional crowding. You do not need to trade perps to use it. **Extreme positive funding means longs are crowded and paying to stay in** — a poor moment to add spot exposure. Extreme negative funding means the opposite.

**Novelty.** Funding is almost always discussed as either a carry-trade input or a contrarian timing signal in isolation. Using it as a **gate on an unrelated spot strategy** — a sentiment overlay rather than a signal — is a cleaner test of its information content, and it is directly compatible with a long-only spot engine that cannot touch perps.

**Universe.** BTC, ETH, SOL versus USDT (matching current Quant v1 pairs). Funding sampled from the deepest CEX venue per asset.

**Signal.** Compute the trailing 30-day percentile of the 8-hour funding rate per asset.
- **Green gate:** funding below the 60th percentile → normal position sizing
- **Amber gate:** funding between the 60th and 85th → **half size**
- **Red gate:** funding above the 85th percentile → **no new longs** (existing positions managed normally)
- Optional bonus arm: funding **below the 15th percentile** (shorts crowded/paying) → **1.5× size** on long entries

**Entry / Exit.** No independent entries. This modulates whatever the base strategy does.

**Sizing.** Multiplier of 0×, 0.5×, 1.0×, or 1.5× on the base strategy's size.

**Frequency.** Continuous. The gate changes state maybe 30–80 times a year per asset — every base-strategy trade is affected, so the effective sample equals the base strategy's trade count.

**Failure mode.** Funding can stay extreme for weeks in a strong trend. A red gate during a sustained bull run means sitting out the best period. Track **opportunity cost explicitly** — log the return of every trade the gate blocked.

**Falsifier.** Compare base strategy alone versus base strategy gated. **If gating does not improve risk-adjusted return, funding adds nothing.** Also check the blocked-trade log: if blocked trades had *better* average returns than taken ones, the gate is inverted.

**Data.** Historical 8h funding rates per venue per asset. Widely available from exchange APIs and aggregators. **Cheap to test — do this one early.**

---

### C2 · Weekend Liquidity Vacuum Reversion
**Class:** Crypto · **Risk tier:** Moderate

**Thesis.** Crypto trades continuously, but participation does not. Weekends have thinner books, fewer market makers running full size, and no traditional-finance flow. Moves made into a thin weekend book are more likely to be liquidity artifacts than information, and should partially revert when Monday liquidity returns.

**Novelty.** The "weekend effect" is folklore in crypto, usually stated as a vague directional claim. This makes it a **liquidity-conditional reversion**: the signal is not that it is Saturday, it is that the move happened on **abnormally low volume relative to the same weekend hour historically**. That distinction is testable and separates a real liquidity vacuum from a genuine weekend news event.

**Universe.** BTC, ETH, SOL vs USDT. Extendable to the top 20 by market cap for a wider sample.

**Signal.** Measure from Friday 20:00 UTC to Sunday 20:00 UTC:
- Absolute move over the window exceeds **1.5 × the trailing 12-week median weekend absolute move**
- Cumulative weekend volume is **below the 40th percentile** of the trailing 12-week weekend volume distribution (**this is the vacuum condition and the whole point**)
- Funding rate did not move to an extreme (rules out a genuine positioning shift)

**Entry.** Enter against the weekend move at **Sunday 22:00 UTC**, scaling in across three tranches over 6 hours.

**Exit.** Target = **50% retracement** of the weekend move. Stop = 1.3 × the weekend move extended. Time stop **Tuesday 20:00 UTC** regardless.

**Sizing.** 0.8% risk. Max 2 concurrent (BTC and ETH are highly correlated — treat any two majors as ~1.5 positions, not 2; see KB §8.5).

**Frequency.** ~52 weekends/year × 3 assets = 156 opportunities, with maybe 25–40% qualifying. Estimate **40–60 signals/year per the 3-asset universe.** Thin — this is why I suggest extending to the top 20 for the backtest, giving ~250–400/year.

**Failure mode.** Weekend moves are frequently the *start* of a real trend that continues into the week — the January 2024 and several 2025 episodes worked this way. The low-volume gate is the only thing standing between this and systematically fading real breakouts. If that gate is weak, so is the strategy.

**Falsifier.** Split by the volume condition. **If high-volume weekend moves revert as often as low-volume ones, the liquidity-vacuum mechanism is fictional** and this is just weekend mean reversion, which is likely already arbitraged.

**Data.** Hourly OHLCV with volume, 8h funding, at least 3 years for the weekend distributions.

---

### C3 · Cross-Asset Weekend Bridge (crypto → equities)
**Class:** Crypto/Equities · **Risk tier:** Moderate

**Thesis.** Crypto trades when equity markets are closed. If crypto partially reflects global risk appetite, then **crypto's move across the equity weekend contains information about Monday's equity open** that the equity market has not yet priced.

**Novelty.** This is the strategy I find most interesting in the whole document, because it exploits a **structural information asymmetry created by differing market hours** rather than a behavioral pattern. It is a genuine cross-asset play available to a retail-scale operator, and it has a natural expiry date: as US equities move to 23x5 trading (KB §1.5), the closed window shrinks and this edge should mechanically decay. **That built-in decay is itself a testable prediction** — the effect should be measurably weaker after December 2026.

**Universe.** Signal from BTC and ETH. Expression in SPY, QQQ, and optionally IWM.

**Signal.** Measured from Friday 16:00 ET (equity close) to Sunday 23:00 ET:
- Compute crypto composite return: 0.6 × BTC + 0.4 × ETH
- Signal fires when |composite return| > **2.0%**
- **Confirmation:** the move is not idiosyncratic to crypto — require that at least one of gold, the dollar index, or equity index futures moved in a directionally consistent way over the same window (this separates "risk appetite" from "crypto-specific news")
- Exclude weekends containing scheduled major macro events on Monday before the open

**Entry.** Long or short SPY/QQQ in the composite's direction at the **Monday open**, or in the pre-market from 08:00 ET if you want to front-run the open auction (test both).

**Exit.** Hard flat at **Monday close**. This is a one-day trade by construction — the information should be priced in within hours.

**Sizing.** 0.7% risk. One position at a time (SPY or QQQ, not both).

**Frequency.** ~52 weekends/year, with a 2% composite move probably qualifying 30–45% of the time. Estimate **15–25 trades/year.** That is thin — **you will need 8+ years of history** for a usable sample of ~150–200. This is a long-history test, not a wide-universe test.

**Failure mode.** Equity index futures also trade Sunday evening and already price much of this, which may leave nothing. **Test the residual explicitly:** regress Monday's SPY return on crypto's weekend move *controlling for* the ES futures move over the same window. If crypto's coefficient is not significant after that control, the strategy is dead and you have learned something clean.

**Falsifier.** The futures-residual regression above. Also: run the effect year by year and look for **decay after December 2026** when extended equity hours begin. If the effect does not decay as the closed window shrinks, the causal story is wrong even if the correlation is real.

**Data.** Crypto hourly bars, ES/NQ futures data across the weekend session, SPY/QQQ open and close, macro calendar. 8+ years.

---

### C4 · Realized-Implied Volatility Spread (crypto VRP)
**Class:** Crypto · **Risk tier:** Conservative

**Thesis.** The variance risk premium is well documented in equities (KB §7.2). Crypto options markets are younger, thinner, and dominated by different participants. **The VRP may be larger, more variable, or differently signed** — and a systematic measurement is itself worth having, independent of whether you trade it.

**Novelty.** Rather than jumping to selling crypto options (thin books, wide spreads, real counterparty risk), this uses the **RV/IV spread as a directional and sizing signal for the spot book**. High IV relative to RV means the options market expects more turbulence than has been delivered — historically that has often coincided with fear rather than realized danger, which for a long-only spot strategy is a size-up condition, not a size-down one.

**Universe.** BTC and ETH (the only crypto assets with a usable options surface). Signal applies to the spot book.

**Signal.**
- `RV` = 30-day realized volatility (close-to-close, annualized)
- `IV` = 30-day at-the-money implied volatility from the deepest options venue
- `VRPSpread = IV − RV`, and take its trailing 1-year percentile
- **Fear-premium regime:** VRPSpread above the 80th percentile → increase spot long sizing 1.3×
- **Complacency regime:** VRPSpread below the 20th percentile → reduce spot long sizing to 0.6×
- Additional gate: require the **IV term structure** to be in contango (30d IV < 90d IV) for the fear-premium size-up; backwardation means real stress and should override to 0.7×

**Entry / Exit.** Overlay only, like C1. Can be combined with C1 multiplicatively (cap the combined multiplier at 1.5×, floor at 0×).

**Sizing.** Multiplier 0.6× to 1.3× on base strategy size.

**Frequency.** Continuous overlay. Regime states change perhaps 20–40 times/year.

**Failure mode.** Crypto options data quality is poor before roughly 2021 and thin outside BTC/ETH. Also, "high IV = fear = opportunity" fails catastrophically in a genuine regime break, which is exactly when IV is highest. **The term-structure override is the only protection here and it should be tested separately** to see whether it earns its place.

**Falsifier.** If sizing multipliers produce no improvement in risk-adjusted return over flat sizing, discard. Also check: does the fear-premium size-up produce its worst trades in the top 5% of VRPSpread? If yes, the relationship is non-monotonic and the top tail needs its own rule.

**Data.** Crypto options IV surface (Deribit is the standard source), spot OHLCV. 4+ years.

---

### C5 · Dominance Rotation Ladder
**Class:** Crypto · **Risk tier:** Aggressive

**Thesis.** Capital in crypto rotates in a fairly consistent sequence: BTC first, then large-cap alts, then the long tail. Bitcoin dominance (BTC market cap ÷ total crypto market cap) falling while total market cap rises is the classic "alt season" signature.

**Novelty.** The folk version is a binary ("alt season is here"). This makes it a **ladder with defined rungs and a rotation schedule**, and — the actually novel part — it uses the **rate of change of dominance rather than its level**, plus a requirement that the rotation be **confirmed by volume migration**, not just price. Dominance can fall because BTC dropped, which is not a rotation, it is a crash. Volume migration distinguishes them.

**Universe.** BTC, ETH, SOL as the tradeable set (spot, matching v1). Signal computed from full market-cap aggregates.

**Signal.**
- `DomROC` = 14-day rate of change of BTC dominance
- `TotalMcapROC` = 14-day rate of change of total crypto market cap
- **Rotation condition:** DomROC < −2% **and** TotalMcapROC > +5%
- **Volume migration confirmation:** the 7-day average of (altcoin aggregate volume ÷ BTC volume) has risen at least 20% over 14 days — **this is the gate that separates rotation from crash**
- Rung assignment: 0 rungs (all BTC) if no rotation; 1 rung (shift 30% to ETH) on rotation; 2 rungs (shift a further 20% to SOL) if rotation persists 10+ sessions and DomROC is still negative

**Entry.** Rebalance toward the target allocation over 3 days, in thirds.

**Exit.** Unwind rungs in reverse when DomROC turns positive for 5 consecutive sessions, **or immediately in full** if TotalMcapROC drops below −8% (a crash, not a rotation).

**Sizing.** Total crypto book unchanged; this only changes composition. **Max drawdown control comes from the −8% total-market kill switch, which must be tested as the primary risk control, not an afterthought.**

**Frequency.** Rotation conditions might fire 3–8 times/year. Over 6 years of usable data that is **20–50 regime episodes.** Thin. Consider extending the tradeable set to the top 10 by market cap for a richer test, then reducing to BTC/ETH/SOL for live.

**Failure mode.** In a genuine bear market, dominance rises and this correctly stays in BTC — but BTC still falls, and this strategy has **no cash rung.** Consider adding a stablecoin rung as a variant (C5b) and test whether it helps or just adds whipsaw. My guess is it helps materially and this is the most important variant to test.

**Falsifier.** Compare rung-following to a static BTC/ETH/SOL equal weight, and to 100% BTC. If neither is beaten on risk-adjusted return, rotation timing adds nothing. Then separately test whether the **volume migration gate** improves on the price-only version — if not, drop it.

**Data.** BTC dominance and total market cap history, per-asset volume, 6+ years.

---

### C6 · Stablecoin Supply Impulse
**Class:** Crypto · **Risk tier:** Moderate

**Thesis.** Stablecoin aggregate supply is a proxy for dry powder entering or leaving the crypto system. **Net issuance is capital arriving; net redemption is capital leaving.** Because issuance is on-chain and public, this is a rare case of observable flow data that has no equity-market equivalent for retail.

**Novelty.** Stablecoin supply is widely watched as a slow macro backdrop. The novelty is treating **the impulse (second derivative) rather than the level**, and combining it with **exchange-held stablecoin balances** — total supply says capital exists, exchange balances say capital is positioned to buy. Supply growing while exchange balances fall means capital is going to DeFi or custody, not to spot bids. That distinction should matter and is rarely made.

**Universe.** Signal from USDT + USDC + DAI aggregate supply and exchange balances. Expression in BTC/ETH/SOL spot.

**Signal.**
- `SupplyImpulse` = 7-day change in the 30-day rate of change of aggregate stablecoin supply
- `ExchangeRatio` = stablecoin balance held on CEXs ÷ total stablecoin supply
- **Bullish impulse:** SupplyImpulse in the top 25% of its trailing 1-year distribution **and** ExchangeRatio rising over 14 days
- **Bearish impulse:** SupplyImpulse in the bottom 25% **and** ExchangeRatio falling
- Add a 3-day confirmation delay before acting (on-chain data is noisy and gets revised)

**Entry.** Bullish impulse → base strategy sizing × 1.4. Bearish impulse → × 0.5 and no new entries. Standalone variant: enter BTC long on bullish impulse, hold 20 sessions.

**Exit.** Overlay: state persists until the condition inverts. Standalone: 20-session hold or a 2 × ATR(14) stop.

**Sizing.** 0.9% risk for the standalone variant. Overlay multiplier 0.5× to 1.4×.

**Frequency.** Estimate 12–25 impulse events/year. Over 5 years, **60–125 observations** for the standalone version. Adequate but not generous — run the overlay version too, since it inherits the base strategy's sample.

**Failure mode.** Stablecoin supply reacts to regulatory events, chain migrations, and single large-issuer decisions that have nothing to do with market appetite. A single Tether mint of size can dominate the impulse. **Winsorize and check whether results depend on a handful of large events** — if 3 mints drive the whole result, there is no strategy.

**Falsifier.** If the ExchangeRatio condition adds nothing over SupplyImpulse alone, the novel component fails and you are left with a well-known slow signal.

**Data.** On-chain stablecoin supply by issuer, CEX stablecoin balances (Glassnode, Nansen, CryptoQuant, or DefiLlama), 5+ years.

---
## 4. Options strategies

All six are **defined-risk**. There is no naked short premium in this document (see §0.5). Every one assumes half-the-quoted-spread slippage minimum and per-contract commission both ways.

Tax note for the backtest: SPX/XSP/NDX/RUT are Section 1256 (60/40, no wash sales); SPY/QQQ options are not (KB §12). If you model after-tax returns, this materially changes the ranking between otherwise-identical strategies.

---

### O1 · VRP Harvest with a Triple Gate
**Class:** Options · **Risk tier:** Conservative

**Thesis.** The variance risk premium is real and well documented — implied volatility has historically exceeded subsequently realized volatility, with S&P 500 one-month IV averaging roughly 3–4 points above realized (KB §7.2). The problem has never been the premium's existence; it is that short-volatility losses cluster in crashes. **The entire engineering problem is regime selection.**

**Novelty.** Most premium-selling systems use one gate (usually IV rank). This uses **three independent gates that must all agree**, chosen specifically because they fail at different times: a level gate, a term-structure gate, and a trend gate. The claim being tested is that requiring agreement across three uncorrelated conditions removes most of the crash exposure while retaining most of the premium.

**Universe.** SPX or XSP (cash-settled, European, Section 1256, no assignment risk — this matters enormously for an automated system).

**Signal.** All three must hold:
1. **Level gate:** VIX between **13 and 26** (below 13, premium does not pay for the risk; above 26, you are selling into a real event)
2. **Term-structure gate:** VIX futures in **contango** — front month below second month by at least 3%
3. **Trend gate:** SPX above its **50-day SMA**, and the 50-day SMA is not declining

**Entry.** Sell a **16-delta put credit spread**, 30–45 DTE, spread width 25 points (SPX) or 2.5 (XSP). Target credit of at least 15% of spread width; skip the trade if the market will not pay it.

**Exit.** Close at **50% of maximum profit**, or at **21 DTE**, whichever comes first (the standard tastytrade-style management, included here because it is widely used and therefore a fair benchmark — test 25%, 50%, and 75% profit targets as separate arms). Hard stop if the short strike is breached: close immediately, accept the loss.

**Sizing.** 0.5% of account at max loss per position. Max 4 concurrent, staggered across expirations. **Total portfolio max loss across all open positions capped at 4%.**

**Frequency.** New position eligibility roughly weekly when gates are open. Gates are probably open **55–70% of the time.** Estimate **35–50 positions/year.** Over 10 years, 350–500 — a good sample that includes 2018, 2020, 2022, and 2024 stress events.

**Failure mode.** The gates will be open going into a crash, because crashes start from calm. February 2018 and February 2020 both began with VIX in range, contango intact, and price above the 50-day. **Expect the gates to fail at least once in any 10-year sample.** The defined-risk spread is what makes that survivable rather than terminal. Do not remove the long wing to improve the credit.

**Falsifier.** Test each gate's marginal contribution by ablation — run all-three, each pair, each single, and none. **If three gates do not beat two, the extra complexity is unjustified.** Also report the worst single trade and worst month for every configuration; that number, not the Sharpe, is what should drive the decision.

**Data.** SPX/XSP option chains with historical IV, VIX and VIX futures term structure, 10+ years including 2018 and 2020.

---

### O2 · Skew-Inversion Reversal
**Class:** Options · **Risk tier:** Moderate

**Thesis.** S&P 500 options exhibit the most pronounced put skew of any major asset — OTM puts persistently trade at higher IV than OTM calls (KB §7.1). Not all assets do; agricultural commodities, JPY, and CHF show *inverse* smirks. When a normally put-skewed equity name **inverts** to call-skewed, something unusual is happening in positioning.

**Novelty.** Skew is usually monitored at the index level as a fear gauge. Applying it at the **single-name level as a regime-change detector**, and specifically trading the *inversion event* rather than the skew level, is much less common. The additional nuance: **separate inversions that come with rising call IV (speculative demand, likely bullish and likely to overshoot) from those that come with falling put IV (hedge unwinding, a quieter and more durable signal).** These should have different forward returns and lumping them together is probably why this looks like noise.

**Universe.** Optionable Russell 1000 names with 30-day option volume > 2,000 contracts/day and a continuous IV surface.

**Signal.**
- `Skew` = 25-delta put IV − 25-delta call IV, 30-day tenor
- Skew crosses from positive to **negative** (inversion) for the first time in 60 sessions
- Classify the driver: over the prior 5 sessions, did call IV rise more than put IV fell (**Type A: speculative**) or did put IV fall more than call IV rose (**Type B: unwind**)?
- **Test both types as separate arms — the classification is the hypothesis**
- Exclude names with earnings within 10 sessions (a different mechanism entirely)

**Entry.**
- **Type A (speculative inversion):** fade it. Buy a put debit spread, 45 DTE, 40Δ/20Δ.
- **Type B (unwind inversion):** follow it. Buy a call debit spread, 45 DTE, 40Δ/20Δ.

**Exit.** Close at 100% of debit paid (double), or at 21 DTE, or when skew normalizes back above zero, whichever is first. Stop at 50% of debit lost.

**Sizing.** 0.7% of account at max loss. Max 5 concurrent, max 2 per sector.

**Frequency.** Skew inversions in single names are not rare — estimate **10–30 per month** across the optionable Russell 1000, ~150–350/year. Healthy sample.

**Failure mode.** Single-name IV surfaces are noisy and interpolated. A "skew inversion" may be a data artifact from a thin strike. Require a minimum open interest at both the 25Δ put and call strikes, and drop any name where the surface is fit from fewer than 8 strikes.

**Falsifier.** **If Type A and Type B have statistically indistinguishable forward returns, the classification premise — the entire novel content — is dead** and this collapses into generic skew trading.

**Data.** Single-name option surfaces with delta-interpolated IVs, 5+ years. This is the most expensive dataset in the document.

---

### O3 · Implied-Move Underpricing Screen (earnings)
**Class:** Options · **Risk tier:** Moderate

**Thesis.** The options market prices an implied move for each earnings event. Retail systematically buys long premium into earnings and loses (KB §10.7.1). But "retail loses on average" does not mean the implied move is *always* too high — it means it is too high **on average**. The distribution has a left tail where the market underprices, and that tail is findable.

**Novelty.** Instead of a blanket long or short premium bias, screen for the ratio of **implied move to that name's own historical realized earnings move distribution**, and trade only the extreme tails. The novel refinement: **weight recent earnings moves more heavily and condition on whether the company has changed size class**, since a name that has doubled in market cap has a structurally different move distribution than its own 3-year history implies. Most implied-vs-realized screens use a naive equal-weighted history and get fooled by exactly this.

**Universe.** Optionable names with at least 12 quarters of earnings history and 30-day option volume > 1,000 contracts/day.

**Signal.** Two sessions before earnings:
- `ImpliedMove` = ATM straddle price ÷ spot, for the expiration immediately following earnings
- `RealizedDist` = the name's absolute earnings-day moves over the last 12 quarters, **exponentially weighted with a 6-quarter half-life**
- `Ratio` = ImpliedMove ÷ median(RealizedDist)
- **Long premium arm:** Ratio in the **bottom 15%** of the cross-sectional distribution that week (market is underpricing this name's typical move)
- **Short premium arm:** Ratio in the **top 15%** (market is overpricing)
- **Size-class adjustment:** exclude names whose market cap has changed more than 60% over the 12-quarter window unless you recompute the distribution on percentage moves only

**Entry.**
- Long arm: buy a **strangle** (30Δ call + 30Δ put), nearest expiration after earnings, entered at the close 2 days prior.
- Short arm: sell an **iron condor** with short strikes at 20Δ and wings 3 strikes out, same expiration.

**Exit.** Close both arms at the **open the session after earnings**. No holding for drift — this is a pure volatility-event trade and IV crush makes holding expensive.

**Sizing.** 0.6% of account at max loss. Max 6 concurrent (earnings cluster, so a cap matters), max 2 per sector, max 3 in any single day.

**Frequency.** ~1,000 optionable earnings events per quarter; 15% tails on each side gives roughly **150 long-arm and 150 short-arm signals per quarter**, ~1,200/year combined. Excellent sample.

**Failure mode.** Overnight gap risk is the entire trade for the short arm; a single outsized move can exceed several months of collected credit. The iron condor caps it, which is why the wings are non-negotiable. For the long arm, the failure is subtler: paying the spread twice on a strangle in a name with 8%-wide option spreads consumes most of the expected edge before the move even happens.

**Falsifier.** If the **short arm and long arm both lose**, the ratio has no information and the screen is worthless. If only the short arm works, you have rediscovered the VRP and should just trade O1, which is cheaper and cleaner.

**Data.** Historical option chains around earnings dates, earnings calendar, 4+ years.

---

### O4 · Calendar on Term-Structure Inversion
**Class:** Options · **Risk tier:** Conservative

**Thesis.** IV term structure is normally upward-sloping. When the front month prices **above** the back month, the market is pricing near-term stress that it does not expect to persist. If that near-term stress resolves faster than priced, selling the expensive front and owning the cheap back should profit.

**Novelty.** Calendar spreads are conventionally used as a neutral theta play at a chosen strike. This uses them as a **pure term-structure convergence trade with a specific trigger**, and adds a filter that separates *scheduled* inversions (earnings, FOMC, known binary events — where the inversion is rational and there is no edge) from **unscheduled** ones (fear without a calendar reason, where convergence is more likely). That scheduled/unscheduled split is the substance of the idea.

**Universe.** SPY, QQQ, IWM, and the 50 most option-liquid single names.

**Signal.**
- 30-day ATM IV **exceeds** 90-day ATM IV by at least **8%** relative
- **No scheduled event** in the front-month window: no earnings, no FDA date, no scheduled FOMC, no index event, no known litigation date
- Underlying is within 3% of its 20-day SMA (avoid inversions caused by a violent directional move already underway)
- Front-month IV is above its own 1-year 60th percentile (the inversion should come from front-month richness, not back-month collapse)

**Entry.** Sell the front-month ATM straddle, buy the 90-day ATM straddle, **delta-neutral at entry** (adjust strikes or add a small share hedge). Or the simpler single-strike double calendar if you want fewer legs to model.

**Exit.** Close when term structure returns to contango (30d IV < 90d IV), or at **front-month 10 DTE**, or at a 40% loss of net debit, whichever is first.

**Sizing.** 0.5% of account at estimated max loss. Max 4 concurrent. **Vega exposure across all open calendars capped** at a defined portfolio limit — this is a vega strategy and position-count limits alone will not control it.

**Frequency.** Index inversions happen maybe 10–20 times/year; single names much more often. Across a 53-name universe estimate **60–120 signals/year.** Reasonable.

**Failure mode.** Term structure inverts and then **stays inverted or deepens** because the near-term fear was correct. March 2020 and August 2024 both did this. The stop is the only defense, and calendars are hard to exit cleanly in a stressed market — model exit slippage generously, at more than half the spread.

**Falsifier.** Split scheduled versus unscheduled inversions. **If they perform the same, the filter that makes this novel does nothing.**

**Data.** IV term structure by tenor, event calendars (earnings, FDA, FOMC), option chains. 5+ years including 2020 and 2022.

---

### O5 · Gamma Cliff Positioning (0DTE)
**Class:** Options · **Risk tier:** Aggressive

**Thesis.** 0DTE options reached roughly **66% of total SPX volume** by July 2026 (KB §7.3). That concentration means dealer gamma positioning at specific strikes is enormous and mechanical in the final hours. Price should be attracted to high-open-interest strikes and repelled from low-OI zones as dealers hedge.

**Novelty.** "Max pain" and gamma-wall trading exist in retail folklore but are almost always applied as a static level at the open. This makes it a **time-weighted, dynamically-recomputed** measure — gamma concentration matters far more at 15:00 than at 10:00, because time-to-expiry scales the hedging intensity — and it explicitly trades the **transition between gamma regimes** rather than the level. Also, the evidence base is real, not folklore: Ni, Pearson & Poteshman (JFE 2005) documented that optionable stocks cluster at strikes on expiration dates more than chance predicts (KB §3.8.3).

**Universe.** SPX only (deepest 0DTE market, cash-settled, Section 1256, no assignment risk). XSP for smaller size.

**Signal.** Recompute every 15 minutes from 13:00 ET:
- Build the dealer gamma profile by strike from open interest and estimated dealer positioning sign
- `GammaWall` = strike with the largest net dealer gamma within ±1.5% of spot
- `Distance` = (spot − GammaWall) ÷ spot
- **Pin arm:** if |Distance| is between 0.15% and 0.6% and total 0DTE OI within ±1% of spot is in the **top 30%** of its 60-day distribution → trade toward the wall
- **Breakaway arm:** if spot has moved **through** the wall with 15-minute volume in the top 10% of its intraday distribution → trade away from the wall (the wall broke; dealers now hedge in the same direction as the move, which accelerates it)
- No new positions after 15:30 ET

**Entry.** Pin arm: **iron butterfly** centered at the GammaWall, wings 0.5% out. Breakaway arm: **debit vertical** in the breakout direction, 25Δ/10Δ.

**Exit.** Both arms: close at **15:55 ET**, no exceptions, never hold to cash settlement. Pin arm stop at 2× credit received. Breakaway arm stop at 50% of debit.

**Sizing.** **1.0% of account at max loss, hard cap.** Max 1 position at a time. This is the highest-variance options strategy here and the position count reflects it.

**Frequency.** Daily eligibility; expect one arm or the other to qualify on perhaps 50–65% of sessions. Estimate **130–165 trades/year.** Good sample builds quickly.

**Failure mode.** Dealer positioning sign is **estimated, not observed.** You do not know whether dealers are long or short gamma at a strike — you are inferring it from OI and a customer-flow assumption. **If the sign assumption is wrong, the strategy is exactly inverted**, and it will look like a coherent losing strategy rather than random noise, which is the most dangerous kind of wrong.

**Falsifier.** Run the strategy with the dealer sign assumption **flipped** as a control arm. If the flipped version performs as well or better, your positioning model is wrong and everything built on it is invalid. **Run this control first, before optimizing anything.**

**Data.** Intraday SPX option chains with open interest and volume by strike (OI updates daily, so intraday OI must be estimated from prior close plus same-day volume — model this carefully, it is the weakest link), 3+ years.

---

### O6 · Wheel with a Fundamental Floor
**Class:** Options · **Risk tier:** Conservative

**Thesis.** The wheel — sell cash-secured puts, take assignment, sell covered calls, repeat — is the most popular income strategy in retail communities. Its known failure mode is being assigned a declining business and then selling calls against a permanently impaired position, converting a premium strategy into a slow bagholding operation.

**Novelty.** Not the wheel itself, which is thoroughly documented. The novelty is a **quantitative floor condition** that must hold for a name to enter the wheel, plus an **exit-the-wheel rule** — the thing the standard version conspicuously lacks. Most wheel discussions have an entry philosophy and no exit philosophy at all, which is why they end in a portfolio of assigned losers.

**Universe.** Names meeting **all** of:
- Market cap > $10B
- Positive free cash flow in each of the last 8 quarters
- Net debt / EBITDA < 3.0
- Not within 2 quarters of a known patent cliff, major litigation date, or announced merger
- 30-day option volume > 2,000 contracts/day
- IV rank > 30 (there must be premium worth collecting)

**Signal.**
- Sell a **20-delta cash-secured put**, 30–45 DTE, when IV rank > 30
- Roll or close at 50% max profit
- If assigned, sell a **25-delta covered call**, 30–45 DTE
- **Exit-the-wheel rule (the novel part):** stop selling calls and liquidate the position outright if **any** of these trigger — (a) two consecutive quarters of negative FCF, (b) net debt/EBITDA rises above 4.0, (c) the position is down more than 25% from the assignment price **and** the 200-day SMA has been declining for 60+ sessions. Take the loss, redeploy.

**Entry / Exit.** As above. Ladder across 4–6 names and staggered expirations.

**Sizing.** Each put fully cash-secured. Max **6 concurrent** names. Max 20% of the book in any one name. Max 40% in any one sector.

**Frequency.** With 30–45 DTE cycles across 6 slots, roughly **50–80 option trades/year**, plus assignments. Over 6 years, 300–500 trades. Fine.

**Failure mode.** The wheel's return profile is short-volatility and long-equity — it will underperform badly in a strong bull market (you cap every winner) and lose money in a bear market (you own the shares). **It is a market-neutral-sounding strategy that is not remotely market-neutral.** Benchmark it against buy-and-hold of the same names, not against cash; against cash it will always look good and that comparison is meaningless.

**Falsifier.** **If the wheel does not beat simply owning the same basket on a risk-adjusted basis, it is not a strategy, it is a tax on your own upside.** Then separately: does the exit rule help? Run with and without. If the exit rule does not improve results, the standard wheel critique is wrong, which would itself be worth knowing.

**Data.** Option chains, quarterly fundamentals (FCF, debt, EBITDA), daily bars. 6+ years including 2022.

---
## 5. Cross-cutting notes

### 5.1 Full index

| ID | Name | Class | Tier | Est. signals/yr | Data difficulty |
|---|---|---|---|---|---|
| D1 | Midday Liquidity Tax Dodge | Day | Conservative | 1,800–3,800 | Low |
| D2 | Failed-Breakout Harvest | Day | Moderate | 900–2,000 | Medium |
| D3 | Halt-Reopen Auction Fade | Day | Aggressive | 500–1,500 | **High** (halt codes) |
| D4 | SSR Asymmetry Long | Day | Moderate | 200–600 | Medium |
| D5 | Retail Session Fingerprint | Day | Moderate | 500–1,500 | **High** (odd-lot) |
| D6 | Second-Day Decay Curve | Day | Aggressive | 250–800 | **High** (float, borrow) |
| S1 | AVWAP Confluence Node | Swing | Conservative | 250–700 | Low |
| S2 | Breadth Divergence Rotation | Swing | Conservative | Continuous | **Very low** |
| S3 | PEAD with Options Confirmation | Swing | Moderate | 600–1,000 | Medium |
| S4 | Attention Decay Fade | Swing | Moderate | 200–500 | **High** (attention proxy) |
| S5 | Index Reconstitution Shadow | Swing | Conservative | 20–60 | Medium, needs 8+ yrs |
| S6 | Volatility-Squeeze Deferral | Swing | Aggressive | 150–350 | Low |
| C1 | Funding-Rate Sentiment Overlay | Crypto | Conservative | Continuous | **Very low** |
| C2 | Weekend Liquidity Vacuum | Crypto | Moderate | 40–400 | Low |
| C3 | Cross-Asset Weekend Bridge | Crypto/Eq | Moderate | 15–25 | Medium, needs 8+ yrs |
| C4 | Realized-Implied Vol Spread | Crypto | Conservative | Continuous | Medium |
| C5 | Dominance Rotation Ladder | Crypto | Aggressive | 3–8 regimes | Low |
| C6 | Stablecoin Supply Impulse | Crypto | Moderate | 12–25 | Medium (on-chain) |
| O1 | VRP Harvest, Triple Gate | Options | Conservative | 35–50 | Medium |
| O2 | Skew-Inversion Reversal | Options | Moderate | 150–350 | **Very high** (surfaces) |
| O3 | Implied-Move Underpricing | Options | Moderate | ~1,200 | High |
| O4 | Calendar on Inversion | Options | Conservative | 60–120 | High |
| O5 | Gamma Cliff (0DTE) | Options | Aggressive | 130–165 | **Very high** (intraday OI) |
| O6 | Wheel with Fundamental Floor | Options | Conservative | 50–80 | Medium |

### 5.2 Suggested testing order

Ordered by **information gained per hour of work**, not by how interesting the strategy is:

**Tier 1 — test this week (cheap data, fast answers):**
- **S2** (Breadth Rotation) — needs only two daily price series. If it works it improves every other strategy's allocation. Highest leverage in the document.
- **C1** (Funding Overlay) — free exchange API data, and it plugs straight into the existing v1 engine as a sizing multiplier.
- **D1** (Midday) — the three-time-bucket comparison is a clean, self-contained experiment even if the strategy itself fails.
- **S6** (Squeeze) — daily OHLCV only.
- **S1** (AVWAP Node) — daily bars plus earnings dates.

**Tier 2 — test this month:**
- **S3** (PEAD + skew), **C2** (Weekend Vacuum), **C5** (Dominance), **O1** (VRP Triple Gate), **O6** (Wheel), **D2** (Failed Breakout).

**Tier 3 — only if Tier 1–2 produced something and you want more:**
- **D4**, **C4**, **C6**, **O3**, **O4**, **C3**, **S5**.

**Tier 4 — expensive data, high risk, do last or never:**
- **D3** (halt codes), **D5** (odd-lot), **D6** (float + borrow + halts), **S4** (attention proxy), **O2** (single-name surfaces), **O5** (intraday OI estimation).

### 5.3 Controls worth running across the whole batch

Several of these have a **built-in control experiment** that is more informative than the strategy result itself. Run these regardless of outcome:

| Strategy | The control | What it tells you |
|---|---|---|
| D1 | Same signal in 3 time buckets | Whether the U-shaped cost curve is economically large |
| D4 | Names that fell 8–9.9% (just missed SSR) | A clean regression discontinuity at the 10% threshold |
| D6 | Edge vs. ExhaustionScore decile | Whether float rotation interacts with gain at all |
| S3 | Skew-confirms vs. disagrees vs. no filter | Whether option skew carries information about drift |
| S4 | Entry on day 0/1/3/5/10 | The actual shape of the attention decay curve |
| C3 | Regress on crypto move controlling for ES futures | Whether crypto carries residual weekend information |
| O1 | Gate ablation (all 8 combinations) | Which gates earn their complexity |
| O2 | Type A vs. Type B inversions | Whether the driver classification means anything |
| O5 | **Dealer sign assumption flipped** | Whether your positioning model is inverted |
| O6 | Wheel vs. buy-and-hold same basket | Whether the wheel is a strategy or a self-tax |

**Several of these controls produce a publishable-quality finding whether the strategy works or not.** D4's discontinuity, C3's futures residual, and O1's ablation are all genuinely interesting questions independent of whether they make money.

### 5.4 Common ways this batch will fool you

1. **The 2020–2021 problem.** Any backtest spanning that period will show inflated results for long-biased and volatility-selling strategies. Report results **excluding** 2020–2021 as a separate column.
2. **Structural breaks (KB §11.1).** T+1 (May 2024), tick sizes and round lots (Nov 2025), PDT repeal (June 2026), and 23x5 hours (Dec 2026) all change the data-generating process. Do not splice silently.
3. **The best-looking one is probably the most overfit.** In a batch of 24 with parameter sweeps, the top performer is the most likely to be a chance maximum. **Rank by out-of-sample stability, not by in-sample return.**
4. **Correlated failures.** D2, D4, D6, S4, and O5 all lose money in the same environment: a violent one-directional trend. Passing individually does not mean the book is diversified. **Compute the correlation matrix of strategy daily returns before combining anything.**
5. **Frequency estimates are mine and unverified.** Every "estimated signals/year" figure above is my judgment, not a measurement. The first thing any backtest should output is the **actual** signal count. If it is wildly off my estimate in either direction, the filter is not doing what I described and the spec needs rereading before you interpret any P&L.
6. **Survivorship.** D6 and S5 especially require datasets that include delisted names. Without that, both will look far better than they are.

### 5.5 What I would actually expect

If I had to guess before any testing — and this is a guess, worth nothing on its own:

- **Most likely to show something:** S2 (breadth routing), C1 (funding overlay), O1 (VRP with gates). All three lean on documented, mechanistically-explicable effects rather than novel patterns. The novelty in them is in the *conditioning*, which is the safer kind of novelty.
- **Most likely to be interesting even in failure:** D1 (the cost-curve measurement), D4 (the SSR discontinuity), C3 (the futures residual).
- **Most likely to produce a beautiful, untradeable backtest:** D6, then O5. Both depend on data or execution assumptions that break in live trading.
- **Most likely to be a data artifact:** O2 and O5 — both are built on estimated or interpolated quantities rather than observed ones.
- **Most likely to already be arbitraged:** S5 (reconstitution) and the naive core of S3 (PEAD). Both are decades-old and well known; only the novel conditioning could survive.

### 5.6 Standing constraints

Nothing in this document overrides the existing Quant governance. Specifically:

- Every survivor still faces the full lifecycle: backtest bar → sandbox → shadow gate (PF ≥ 1.1 over ≥20 signals in 2+ weeks) → paper → explicit approval for live.
- The **fee-to-edge gate applies to all of these** and will likely kill several outright, particularly the higher-frequency day strategies. That is the gate working.
- Anything touching shorts, options, or a new asset class is a **class change requiring Aym's explicit approval**, not an autonomous Quant decision.
- The risk model — fixed notional cap, daily and weekly loss limits, consecutive-loss pause, concurrent position cap, exchange-side stops, kill switch — is unchanged by anything here.
- **A backtest result is not permission.** These are hypotheses. Treat every one as false until it has survived out-of-sample testing, multiple-comparison correction, and shadow trading — in that order.

---

## 6. Change log

| Version | Date | Note |
|---|---|---|
| 1.0 | 12 Aug 2026 | 24 untested hypotheses across day, swing, crypto, and options. None tested by anyone. Companion to `trading-knowledge-base.md` v1.1. |
