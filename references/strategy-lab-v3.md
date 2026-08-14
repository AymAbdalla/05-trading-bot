# Strategy Lab v3 — Five Literature-Anchored Strategies Across Four Horizons
**For: Quant trading bot — graveyard backtesting pipeline**
**Date: 2026-08-13 | Author: Claude (strategy lab) | Status: UNTESTED HYPOTHESES, PEER-REVIEWED FOUNDATIONS**

---

## The doctrine (read this before the strategies)

**What creates an edge — the four durable sources per the literature:**

1. **Risk premia** — payment for holding risk when others step aside. Lucca & Moench (Journal of Finance, 2015) found that since 1994, the 24 hours before scheduled FOMC announcements produced excess returns accounting for the majority of the entire equity premium (80%+ in their 1994–2011 sample). Savor & Wilson (JFQA, 2013) generalized it: expected returns are significantly higher on scheduled macro announcement days (CPI, employment, FOMC).
2. **Liquidity provision** — payment for absorbing forced or impatient flow. Nagel (Review of Financial Studies, 2012): short-term reversal profits ≈ market-making returns, strongly predictable with VIX, spiking during turmoil when real liquidity providers pull back.
3. **Structural / mechanical flows** — institutions execute on schedules, and schedules leak. Heston, Korajczyk & Sadka (Journal of Finance, 2010): return continuation at half-hour intervals that are exact multiples of a trading day, lasting 40+ trading days, strongest in the first and last half-hours — consistent with recurring VWAP/TWAP order slicing.
4. **Behavioral persistence** — biases that survive being published. Berkman, Koch, Tuttle & Zhang (JFQA, 2012): retail-attention stocks systematically open rich and fade intraday; effect concentrated where valuation is hard and arbitrage is costly.

**The meta-edge (McLean & Pontiff, Journal of Finance, 2016):** across 97 published predictors, returns fell ~26% out-of-sample and ~58% post-publication — but decay was smallest where limits-to-arbitrage bite (high idiosyncratic risk, low liquidity, high costs). **Edges don't die from discovery; they die from institutional-scale arbitrage. Capacity-constrained, cost-heavy edges are the retail bot's protected habitat.** Corollary: every strategy below ships with a decay monitor, because published edges are wasting assets and the honest response is measurement, not faith.

**Edge geometry — answering "does it have to be sector/asset specific":** No. Three geometries exist: **time-series** (asset vs. its own history), **cross-sectional** (ranking within a universe), **calendar/structural** (market-wide). This lab spans all three on purpose — geometric diversity is what makes five strategies a portfolio instead of five bets on the same factor.

**The cost law:** most published gross edges are smaller than retail round-trip costs. The fee-to-edge gate is not a compliance step — it is the strategy. Every spec below states its cost hurdle explicitly.

---

## STRATEGY 1 — SCALP (minutes): "Vacuum Refill"
**Asset:** Crypto spot (BTC/ETH/SOL) — runs on v1 stack today. Long-only compatible.
**Anchor:** Nagel (RFS 2012) — liquidity provision earns most when providers withdraw; reversal profits are the receipt. Crypto microstructure work on funding/liquidation cascades supplies the crypto-native trigger.

**The documented effect:** Reversal after sharp moves is compensation for absorbing flow — largest exactly when volatility is high and balance sheets retreat. In crypto, the cleanest "forced flow" is a liquidation cascade: mechanical selling with zero information content.

**The nuance layer (what makes this non-textbook):**
- **Mechanical-vs-informational discriminator:** only refill a vacuum created by *idiosyncratic* forced flow. Test: the flush pair moves >3× the median move of the other two pairs in the same window. If BTC, ETH, SOL flush *together*, that's market-wide information — stand down. One pair alone in freefall on climax volume is mechanics, not news. (This is the crypto implementation of the finding that reversal works on liquidity-driven moves, not news-driven ones.)
- **Vol-conditioned sizing:** per Nagel, expected liquidity-provision returns scale with stress. Signal quality score rises with the pair's realized-vol percentile; below the 40th percentile the strategy is off (calm tape = no one is being forced).

**Genome:**
- Filter: pair realized vol (1h) > 60th percentile of 30-day distribution; idiosyncrasy test passes (flush pair z-move > 3× cross-pair median)
- Entry: 1m–5m flush of ≥ 2.5× round-trip cost with volume z > 3 vs 24h baseline; enter on first 1m candle closing above its open
- Exit: 50% retrace of the flush = target; new flush low = stop; 30-minute hard time box
- Data: OHLCV 1m (have it)
- Frequency: bursty — 0 on dead days, 2–5 on stressed days; portfolio ≥1/day in normal crypto vol
- **Fee reality (the whole ballgame):** at ~0.3–0.6% round trip on $100 spot clips, only violent dislocations clear the bar. The 2.5× cost multiple in the entry rule IS the strategy — remove it and this becomes a fee donation machine.
- Decay monitor: rolling 90-day expectancy after fees; auto-retire below zero for 2 consecutive windows

---

## STRATEGY 2 — DAY TRADE (hours): "The 3:30 Verdict"
**Asset:** SPY/QQQ (v2); BTC UTC-day analog testable on v1 data now.
**Anchor:** Gao, Han, Li & Zhou, "Market Intraday Momentum" (Journal of Financial Economics, 2018).

**The documented effect:** The first half-hour return (measured from prior close) predicts the last half-hour return on SPY, 1993–2013; predictive R² of 1.6% — rising to 2.6% when the twelfth half-hour (3:00–3:30) is added — which matches or beats typical *monthly* predictors. The effect strengthens on high-volatility days, high-volume days, and macro-news days, and appears across ten other heavily traded ETFs. Mechanisms: infrequent portfolio rebalancing and late-informed trading pressing into the close.

**The nuance layer:**
- **Trade only the paper's amplifier states.** Naive version: always trade the last half-hour in the sign of the first. Nuanced version: require ≥2 of {day's realized vol > 70th pct, volume > 70th pct, macro-release day} — you're harvesting the conditional effect, which is where the economic significance lives.
- **Dual-signal agreement:** require sign(r1) == sign(r12). Agreement days carry the higher R²; conflict days are noise.
- **Decay honesty:** recent literature notes intraday momentum has weakened in post-2013 data. This strategy ships with a mandatory health monitor: rolling 250-day directional hit rate must hold above 52.5%; below it for 60 days → auto-retire to graveyard with cause "post-publication decay." Let the data, not nostalgia, decide if it still lives.
- **Crypto analog:** define the BTC "day" as UTC 00:00; test whether the first UTC half-hour predicts the last. Free test on data already in the pipeline.

**Genome:**
- Filter: amplifier count ≥ 2; sign agreement r1/r12
- Entry: 15:30 ET in the sign of r1 (long-only v1: positive-sign days only)
- Exit: 15:58 flat, always; 0.5× ATR disaster stop
- Data: OHLCV + macro calendar (static)
- Frequency: signal evaluated daily; fires ~2–3×/week after filters
- Fee reality: one round trip/day on liquid ETFs — spread cost trivial, fine on Alpaca

---

## STRATEGY 3 — HOURLY SWING (30–60 min holds): "Same-Clock Echo"
**Asset:** Equities, cross-sectional across the v2 core list. The most "nobody retail trades this" pick in the lab.
**Anchor:** Heston, Korajczyk & Sadka, "Intraday Patterns in the Cross-Section of Stock Returns" (Journal of Finance, 2010).

**The documented effect:** A stock's return in a given half-hour interval positively predicts its return in the *same half-hour interval* on subsequent days, at lags that are exact multiples of a trading day, persisting for at least 40 trading days. Robust to weekday, month, turn-of-month, size, S&P membership, and systematic risk controls. Strongest in the first and last half-hours of the day. Volume and order-imbalance show the same periodicity — the fingerprint of institutions slicing the same parent orders through the same execution schedules day after day.

**The nuance layer:**
- **Trade the mechanism, not just the pattern:** the driver is recurring institutional execution, so concentrate where institutions execute — large, institutionally-held core-list names — and in the open/close half-hours where the paper finds the effect strongest.
- **Cross-sectional, not time-series:** rank each ticker×slot cell by its average same-slot return over the trailing 20 days. Long only the top-decile cells during their slot. Cross-sectional ranking neutralizes market drift — you're isolating the periodicity, not riding beta.
- **Execution-cost jujitsu:** the paper itself notes timing trades to the periodicity can save roughly the effective spread — meaning the *entry timing* partially self-funds the cost hurdle.

**Genome:**
- Filter: slot ∈ {9:30–10:00, 15:30–16:00} initially (extend to all 13 slots only if the concentrated version survives); ticker in core list
- Entry: at slot open, long the top-decile ticker×slot cells by trailing-20-day same-slot mean return (min 3 tickers, max 5, standard $100 clips)
- Exit: slot close, always. No overnight, no carryover
- Data: 30m OHLCV per core ticker, 40-day rolling store — a new but small data structure
- Frequency: 1–2 slots/day → daily by construction
- Fee reality: 30-minute holds on liquid names; spread is the cost, so restrict to top-liquidity tickers (also where the mechanism lives — the constraints agree)
- Decay monitor: quarterly re-estimate of the slot-lag autocorrelation; if the day-multiple lag structure flattens, the mechanism (scheduled execution) has changed → retire

---

## STRATEGY 4 — WEEKLY SWING (1–5 day holds): "Macro Calendar Harvest"
**Asset:** SPY/QQQ core; BTC leg testable on v1 (long-only native — this strategy is only ever long or flat).
**Anchors:** Lucca & Moench (JF 2015); Savor & Wilson (JFQA 2013); Cieslak, Morse & Vissing-Jorgensen, "Stock Returns over the FOMC Cycle" (JF 2019).

**The documented effect:** Since 1994, U.S. equity excess returns have been vastly larger on FOMC announcement days than other days, with the drift accruing in the 24 hours *before* the announcement — over 80% of the equity premium in the 1994–2011 sample — and appearing in major international indices as well. Savor & Wilson: scheduled macro announcement days (CPI, employment, FOMC) carry a premium generally. Lucca & Moench also find the drift is larger when implied vol (VIX) is elevated going in. Cieslak et al. add a biweekly rhythm across the FOMC cycle.

**The nuance layer:**
- **Exposure timing as the entire strategy:** hold equity index exposure *only* inside announcement-risk windows; sit in cash otherwise. You harvest a large share of the premium while holding risk a fraction of the time — the risk-adjusted play, not the raw-return play.
- **VIX conditioning:** size up (within the 5% cap) when VIX > its 1-year median at window entry, per the paper's own conditioning.
- **Regime honesty:** post-2015 evidence is mixed — some studies find the drift shortened after press conferences became standard; practitioner work through 2025 argues it persists. Perfect candidate for the health monitor: track pre-FOMC window returns as their own series; the strategy's aliveness is an empirical output, not an assumption.
- **The BTC leg is the genuinely novel test:** macro-sensitivity of BTC is largely a post-2020 institutionalization phenomenon, so the pre-FOMC/CPI window behavior of crypto is young, under-studied, and testable tonight on data already in the pipeline. If BTC now carries an announcement premium, almost nobody is systematically harvesting it at retail scale.

**Genome:**
- Filter: static macro calendar (FOMC schedule, CPI, NFP — all published a year ahead)
- Entry: 14:00 ET the day before a scheduled FOMC decision (24h window); optional CPI/NFP day-of windows as separate genome variants
- Exit: 5 minutes before announcement release (pure drift harvest, zero event risk) — variant B holds through release to test the Savor-Wilson day premium
- Data: OHLCV + free calendar; VIX daily close (free)
- Frequency: 8 FOMC/year + ~24 CPI/NFP → 2–4 windows/month; weekly-swing cadence
- Fee reality: 2–4 round trips/month on index products — negligible
- Decay monitor: rolling 24-window mean pre-announcement return; retire on 2 consecutive negative years

---

## STRATEGY 5 — WEEKLY SWING (3–5 day holds, cross-sectional): "Paid Liquidity Reversal"
**Asset:** Equities, core-list cross-section (v2). Long-only variant native.
**Anchor:** Nagel (RFS 2012), building on Lehmann (1990) weekly reversal; cost findings from de Groot, Huij & Zhou.

**The documented effect:** Weekly loser-buying/winner-selling profits behave like market-maker income: strongly predictable with VIX, spiking in turmoil — because that's when real liquidity providers are constrained and the price of immediacy rises. The classic failure mode is costs: naive small-cap reversal dies on transaction costs; restricting to larger, liquid stocks preserves the effect at tradeable cost (de Groot et al.).

**The nuance layer — three filters that separate this from the naive version:**
1. **VIX throttle (Nagel):** the strategy is OFF below the VIX 60th percentile. You are selling liquidity; only sell when it's expensive. Unconditional weekly reversal is a mediocre edge — conditional reversal is the documented one.
2. **Residual ranking, not raw:** rank by 5-day return residual vs. the mapped sector ETF (reuses Strategy 2.3 infrastructure from Lab v2). A stock down with its whole sector isn't dislocated; a stock down *alone* is a liquidity candidate.
3. **News exclusion:** drop names with earnings inside the formation or holding window (free calendar) or a formation-week volume z > 3 (informational-move proxy). Reversal is compensation for absorbing *non-informational* flow; buying informed selling is how reversal traders die. This decomposition — reversal works on liquidity-driven moves, fails on news-driven moves — is the single most important nuance in the strategy.
- **Positive skew note:** Nagel documents the strategy's returns were positively skewed with strong crisis performance — the opposite personality of most mean-reversion books. In portfolio terms it's the natural hedge leg to Strategies 2–4.

**Genome:**
- Filter: VIX > 60th percentile (1-year lookback); universe = core list only
- Entry: Friday close (or rolling daily variant): long bottom-quintile residual losers passing the news exclusion, equal $100 clips, max 5 names
- Exit: 5 trading days, or residual mean-reverts to > −0.25σ, or single-name −2 ATR stop
- Data: daily OHLCV + sector ETF map (exists) + earnings calendar (free)
- Frequency: weekly formation; typically 0 positions in calm regimes, fully loaded in stressed ones — by design
- Fee reality: 5-day holds on liquid names; cost drag minor. The real cost risk is adverse selection, which is exactly what filter 3 exists to remove
- Decay monitor: track conditional (VIX-on) expectancy separately from unconditional; the strategy's claim is only about the conditional state

---

## BONUS — "Attention Gap Fade" (scalp/day hybrid, v2 surprise-scanner lane)
Berkman, Koch, Tuttle & Zhang (JFQA 2012): stocks that grabbed retail attention show strong overnight returns and open at prices high relative to the rest of the day, then reverse — concentrated in hard-to-value, costly-to-arbitrage names, i.e., exactly the profile the surprise scanner captures. Spec: for scanner-lane tickers gapping up > 3% on attention signatures (premarket volume z > 4), short the 9:31 print, cover by 11:00, hard stop above the opening range high. Requires shorting (v2/Alpaca) and locate availability — flagged advanced, listed because the paper maps one-to-one onto an architecture lane that already exists.

---

## Portfolio geometry (why these five, together)

| # | Horizon | Geometry | Edge source | Regime that pays it |
|---|---------|----------|-------------|---------------------|
| 1 | Scalp | Time-series | Liquidity provision | Stressed crypto vol |
| 2 | Day | Time-series | Structural (rebalancing/late info) | High-vol, news days |
| 3 | Hourly swing | Cross-sectional | Structural (execution schedules) | Normal institutional flow |
| 4 | Weekly swing | Calendar | Risk premium | Scheduled uncertainty |
| 5 | Weekly swing | Cross-sectional | Liquidity provision | Equity stress (VIX high) |

Five strategies, three geometries, four distinct edge sources, payoff profiles that peak in *different* regimes — including two (1 and 5) that earn most when everything else is bleeding. That's the actual answer to "what creates an edge": not one signal, but a book of small, cost-disciplined, mechanically-motivated exposures whose failure modes don't correlate.

## Non-negotiables for the harness
1. Time-matched random twins for Strategies 2, 3, 4 (clock-anchored)
2. Strategies 4 and 5 both load on equity stress — treat as one family in the Judge's multiple-testing correction
3. Every strategy's decay monitor is part of its genome, not an afterthought: per McLean & Pontiff, post-publication decay averaged ~58% across published predictors. Assume every edge here is smaller live than in the papers; the monitors exist to measure exactly how much smaller
4. Papers used SPY-scale liquidity and pre-2014 samples in several cases; the backtest window should heavily weight post-2015 data for equities and post-2020 for any BTC macro leg

## Primary sources
- Gao, Han, Li & Zhou (2018), "Market Intraday Momentum," *Journal of Financial Economics* 129(2)
- Heston, Korajczyk & Sadka (2010), "Intraday Patterns in the Cross-Section of Stock Returns," *Journal of Finance* 65(4)
- Lucca & Moench (2015), "The Pre-FOMC Announcement Drift," *Journal of Finance* 70(1)
- Savor & Wilson (2013), "How Much Do Investors Care About Macroeconomic Risk?" *JFQA* 48(2)
- Cieslak, Morse & Vissing-Jorgensen (2019), "Stock Returns over the FOMC Cycle," *Journal of Finance* 74(5)
- Nagel (2012), "Evaporating Liquidity," *Review of Financial Studies* 25(7)
- Berkman, Koch, Tuttle & Zhang (2012), "Paying Attention: Overnight Returns and the Hidden Cost of Buying at the Open," *JFQA* 47(4)
- McLean & Pontiff (2016), "Does Academic Research Destroy Stock Return Predictability?" *Journal of Finance* 71(1)
- Further reading: Bogousslavsky (2021, JFE) on intraday/overnight cross-sections; Boyarchenko, Larsen & Whelan (NY Fed) on the overnight drift in ES futures; Da, Liu & Schaumburg on decomposing short-term reversal
