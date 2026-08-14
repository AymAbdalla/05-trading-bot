# Strategy Lab v2 — Unorthodox Cross-Asset Day-Trading Hypotheses
**For: Quant trading bot — graveyard backtesting pipeline**
**Date: 2026-08-13 | Author: Claude (strategy lab) | Status: UNTESTED HYPOTHESES**

---

## How to read this document

Every strategy is expressed in the SPEC genome format: **Filter layer → Entry layer → Exit layer**, plus data requirements, expected signal frequency, and a falsifiable thesis (the behavioral or structural reason the edge *could* exist — if the thesis is wrong, the backtest should kill it).

Design constraints honored throughout:
- Viable at ≥1 signal/day across the portfolio (per-strategy frequency listed; time-anchored strategies fire daily by construction)
- No exotic paid data. Everything runs on OHLCV + volume + free public feeds (funding rates, economic calendar, expiration calendar, sector ETF maps)
- Crypto strategies are **long-only spot compatible** (v1). Equity/futures strategies note long/short; long-only variants given where the edge survives it
- Every strategy must clear the fee-to-edge gate on $100 notional — strategies with structurally tight targets are flagged

**Harness warning that applies to the whole doc:** several strategies are *time-of-day anchored*. The random-entry twin MUST be time-matched (random entries drawn at the same clock time) or the twin comparison will credit the clock, not the signal. This should probably become silent assertion #15.

---

## SECTION 1 — CRYPTO (deployable against v1 stack today)

### 1.1 Funding Shadow
**Thesis:** Perp funding is a real-time poll of leveraged crowd positioning. When shorts are paying heavily to stay short while spot *refuses to break down*, the fuel for a short squeeze is loaded — and spot longs capture the squeeze with zero liquidation risk. The unorthodox part: using a derivative market's tax rate as a spot entry signal, on a venue (Binance.US) that doesn't even list the derivative.
- **Filter:** 1h regime filter neutral-or-better (reuse existing EMA filter)
- **Entry:** predicted funding rate for the pair's perp ≤ 10th percentile of its trailing 90-day distribution AND 15m close holds above the rolling 4h low for ≥3 consecutive candles (spot absorption)
- **Exit:** funding recovers above trailing median, OR standard stop below the absorption low, OR 8h time cap
- **Data:** ccxt `fetchFundingRate` from Binance global or Bybit (public, no keys) as the *signal* venue; execution stays Binance.US spot. Poll on the 15m loop
- **Frequency:** ~0.5–1.5 signals/day across BTC/ETH/SOL
- **Kill condition:** if squeezes resolve via spot dumping to perp price instead of perp squeezing up, expectancy goes negative fast — the graveyard will show it

### 1.2 Wick Autopsy (Absorption Fingerprint)
**Thesis:** OHLC contains a free order-flow proxy nobody uses systematically: the *path asymmetry* of candles. A market where sellers repeatedly probe down (long lower wicks) but candles keep closing in the top third of their range, while net price goes nowhere, is a market where passive buyers are absorbing aggression. That fingerprint precedes markup.
- **Filter:** 20-candle net price change within ±0.5 ATR (flat tape — required, this is an accumulation signal, not a trend signal)
- **Entry:** absorption score = Σ(lower wick) / Σ(upper wick) over 20 candles > threshold (start 1.6) AND ≥60% of those candles close in top third of their range → enter on the first range-expansion candle (range > 1.5× 20-candle median)
- **Exit:** close below the absorption zone low, or 2R target, or 12h time cap
- **Data:** pure OHLCV. Zero new infrastructure
- **Frequency:** ~1–2/day across 3 pairs on 15m

### 1.3 Round-Number Defense Decay
**Thesis:** Round numbers ($1,000 increments on BTC, $100 on ETH, $10 on SOL) are where human limit orders cluster. The novel piece isn't "round numbers matter" — it's *measuring the decay of the defense*: each successive test of a level that produces a smaller rejection wick means the resting liquidity is being eaten. Enter on the break of a level whose defense measurably decayed, skip breaks of fresh levels.
- **Filter:** level must have ≥2 prior tests in the last 48h
- **Entry (long-only compatible):** (a) break above resistance whose last rejection wick was <50% the size of the first rejection wick, or (b) bounce off support whose defense wicks are *growing* (buyers stepping up)
- **Exit:** next round level up = target; failed-break close back below level = stop
- **Data:** pure OHLCV; level set is mechanical
- **Frequency:** ~1/day across 3 pairs

### 1.4 TradFi Handoff
**Thesis:** Crypto trades 24/7 but its *liquidity* doesn't — it breathes with US market hours and the CME session. Price discovered in the thin "CME-dark" window (Friday 5pm ET → Sunday 6pm ET, plus daily off-hours) is discovered on low conviction. When the deep-pocket session reopens, that thin-air move gets audited. Trade the audit. Sign (reversion vs continuation) is deliberately left to the backtest — let each pair vote.
- **Filter:** absolute drift accumulated during the illiquid window > 1.0 ATR (no signal on quiet handoffs)
- **Entry:** first 2h of the liquid session, direction = per-pair backtested sign (long-only v1: take only the long-side signals)
- **Exit:** time-boxed — flat by end of the liquid session's first 6h, plus standard stop
- **Data:** OHLCV + a session calendar (static)
- **Frequency:** fires every weekday by construction when the drift filter passes (~3–4/week per pair)
- **Harness:** time-matched random twin is mandatory here

### 1.5 Liquidation Echo
**Thesis:** A liquidation cascade that *fails to reach* the next obvious liquidity pool (prior swing low) is exhausted forced selling with no follow-through — the most reliable snapback setup in crypto, because the selling was mechanical, not informed.
- **Filter:** none beyond max-position rules
- **Entry:** ≥3 consecutive down candles with expanding ranges AND volume z-score > 3 vs 7-day baseline, where the cascade low holds ABOVE the prior swing low by ≥0.25 ATR → long the close of the first candle that closes above its own open
- **Exit:** 50% retrace of the cascade = target; break of cascade low = stop; 6h time cap
- **Data:** pure OHLCV
- **Frequency:** bursty — 0 on quiet days, 2–3 on volatile days; portfolio-level ≥1/day is realistic in crypto vol regimes

---

## SECTION 2 — EQUITIES (v2 core lane, Alpaca)

### 2.1 Second-Break Verdict
**Thesis:** Everyone trades the opening range breakout, which is exactly why the *first* break fails so often — it's where breakout stops and chase orders concentrate. The unorthodox move: treat the first break as bait. Trade the failed break (reversal back inside), and only trade a with-trend break if it's the second attempt.
- **Filter:** opening range = 9:30–10:00; OR height between 0.5 and 2.0 ATR (skip dead and broken tapes)
- **Entry:** price breaks OR extreme, then closes back inside within 2 candles → enter toward the opposite OR extreme. Long-only variant: only take failed break-DOWNs (long the reclaim)
- **Exit:** opposite OR extreme = target; back beyond the failed extreme = stop
- **Data:** OHLCV
- **Frequency:** daily by construction per ticker; across a core list, many signals/day

### 2.2 Gap Context Engine
**Thesis:** Gaps aren't one phenomenon. A gap that lands *inside* yesterday's range is an overnight repricing within accepted value → fades. A gap *beyond* yesterday's high/low on heavy premarket volume is new information → continues. Retail treats all gaps the same; conditioning on landing zone + per-ticker premarket volume percentile is the edge. This fits the curated core list perfectly: every ticker gets its own volume percentile table.
- **Filter:** |gap| > 0.3 ATR
- **Entry:** inside-range gap → fade toward prior close at 9:35; beyond-range gap AND premarket volume > ticker's 80th percentile → go with the gap on first 5m pullback
- **Exit:** fade: prior close = target. Go-with: 1.5R trail. Both: time-boxed flat by 11:00
- **Data:** OHLCV + premarket bars (Alpaca provides)
- **Frequency:** daily, multiple tickers

### 2.3 Sector Orphan Convergence
**Thesis:** Index arb keeps stocks glued to their sector ETF at low frequency; intraday, the glue is loose. When a sector ETF moves >1σ and a member stock's residual hasn't followed — and the stock shows *no* idiosyncratic volume anomaly (no news of its own) — the laggard converges. It's stat-arb logic scaled down to a single-name bot with free data.
- **Filter:** stock's own volume within normal band (volume z < 2 — a volume spike means it has its own story; stand down)
- **Entry:** sector ETF 30m move > 1σ, stock residual (move − β×ETF move) lagging > 1σ in the opposite direction → trade the stock toward convergence
- **Exit:** residual closes to < 0.25σ, or 90-minute time cap, or stop at 1 ATR
- **Data:** OHLCV for stock + its mapped sector ETF (XLK/XLF/XLE/etc. — static map per core ticker); rolling 20-day β
- **Frequency:** ~1–3/day across a 30-ticker core list

### 2.4 Ghost Levels
**Thesis:** After a stock split, the crowd's muscle memory doesn't split with it. Round numbers in *pre-split coordinates* keep acting as levels (post-10:1 NVDA: $150 = old $1,500). Almost nobody codes level sets in two coordinate systems. You already fought split-adjustment bugs in the data layer — this turns that scar tissue into a strategy.
- **Filter:** ticker has split within trailing 3 years (static table per core ticker)
- **Entry/Exit:** identical mechanics to Round-Number Defense Decay (1.3), run over the union of {current round levels} ∪ {pre-split round levels ÷ split ratio}, with a genome flag weighting ghost levels separately so the backtest can tell you if ghosts are real
- **Data:** OHLCV + split history (already in your pipeline, painfully)
- **Frequency:** ~2–4/week per split ticker

### 2.5 Volume Desert Breakout
**Thesis:** 12:00–13:30 ET is the volume desert — algos idle, desks at lunch. A directional move on 2×+ desert-baseline volume while the index ETF sits flat means someone is deliberately working an order when nobody's watching. Informed flow chooses quiet tape. Follow it into the afternoon.
- **Filter:** SPY 30m move < 0.15% (index flat — isolates idiosyncratic flow)
- **Entry:** 12:00–13:30 candle with volume > 2× that ticker's lunch-hour baseline AND range > 1.5× lunch baseline → enter in move direction
- **Exit:** hold to 15:30 with 1 ATR trail
- **Data:** OHLCV + per-ticker lunch-volume baseline table
- **Frequency:** ~1–2/day across core list

### 2.6 VWAP Magnet Close
**Thesis:** Institutional execution desks are benchmarked to VWAP. Late in the session, a price stretched far from VWAP creates pressure from unfilled benchmark orders to pull it back; a price hugging VWAP tends to pin. The edge is trading the *magnet*, conditioned on distance.
- **Filter:** time = 15:30
- **Entry:** |price − session VWAP| > 0.75 ATR → trade toward VWAP
- **Exit:** VWAP touch, or 15:58 hard flat (never carry into the auction)
- **Data:** OHLCV (VWAP computed from bars)
- **Frequency:** daily scan; fires on ~30–40% of ticker-days
- **Fee flag:** targets can be tight — enforce fee-to-edge gate strictly; may only survive on higher-ATR names

### 2.7 Halt Resumption Drift (surprise-scanner lane)
**Thesis:** Stocks resuming from LULD volatility halts show short-horizon continuation in the halt direction — the halt paused the order flow, it didn't cancel it. Built for the surprise-ticker lane: these names arrive with the exact volume-anomaly fingerprint the scanner hunts.
- **Filter:** surprise-lane ticker only; detect halt via mid-session 5m bar with zero volume followed by a reopening gap
- **Entry:** first 5m bar post-resumption, direction = halt direction, only if the resumption bar doesn't fully reverse the halt move
- **Exit:** 15-minute time box, stop at resumption bar's opposite extreme
- **Data:** OHLCV (halt inferred); Alpaca trade-condition data later if this survives the graveyard
- **Frequency:** bursty; scanner-dependent
- **Risk flag:** highest-variance idea in this doc — cap it at the standard $100 and expect a violent equity curve either way

---

## SECTION 3 — FUTURES (v3 lane)

### 3.1 Overnight Inventory Flush
**Thesis:** Market-profile desks track "overnight inventory." When the entire Globex session trades one-sided (100% above or below prior settlement), the overnight crowd is all-in one direction with RTH liquidity about to arrive — the first RTH hour statistically flushes that inventory back toward settle. Mechanical, daily, and almost never coded by retail because it comes from profile theory, not indicators.
- **Filter:** 100% of Globex session bars above (below) prior settlement
- **Entry:** at RTH open, fade toward prior settlement
- **Exit:** settlement touch = target; 1.25 ATR stop; flat by 10:30 ET
- **Data:** ES/NQ/CL OHLCV with session tags + settlement prices
- **Frequency:** condition true ~30–40% of days per contract → ~1/day across 3 contracts

### 3.2 Release Overshoot Fade
**Thesis:** Scheduled macro releases (CPI, NFP, FOMC) trigger an algorithmic first impulse that routinely overshoots the human repricing. If the impulse exceeds 2× pre-release ATR and gives back 30% within 10 minutes, the overshoot is confirmed — fade the rest, time-boxed.
- **Filter:** static economic calendar (free), releases at 8:30/10:00/14:00 ET
- **Entry:** 5m impulse > 2× trailing ATR, then 30% retrace within 10 min → enter in retrace direction
- **Exit:** 50% retrace of impulse = target; new impulse extreme = stop; 45-minute hard time box
- **Data:** OHLCV + release calendar
- **Frequency:** several/week (calendar-driven, not daily — pair with 3.1 for daily coverage)

### 3.3 Globex VWAP Reversion at RTH Open
**Thesis:** The RTH open price is an auction between overnight positioning and fresh day-session flow. Distance between the open and the *overnight VWAP* measures how stretched the handoff is; stretched opens revert toward overnight VWAP before trending.
- **Filter:** |RTH open − Globex VWAP| > 0.6 ATR
- **Entry:** 9:35 ET toward Globex VWAP
- **Exit:** VWAP touch or 60-minute time box
- **Data:** OHLCV with session tags
- **Frequency:** ~2–3/week per contract

---

## SECTION 4 — OPTIONS-STRUCTURE STRATEGIES, UNDERLYING-ONLY (v4 bridge — tradeable in v2)

The trick in this section: exploit *options-market mechanics* while trading only the underlying, so no options data feed and no options execution is needed. These bridge v2 → v4.

### 4.1 Expiry Pin Drift
**Thesis:** On expiration days, dealer gamma hedging pins underlyings toward high-open-interest strikes, which cluster at round numbers. You don't need OI data to exploit the average effect: after 14:00 on expiry days, price within 0.3% of a round strike gets pulled in.
- **Filter:** expiration-day calendar (SPY/QQQ: Mon/Wed/Fri + daily; single names: monthly Fri) AND time ≥ 14:00
- **Entry:** price within 0.3% of nearest round strike but not on it → trade toward the strike
- **Exit:** strike touch, or 15:55 flat, or 0.5% adverse stop
- **Data:** OHLCV + static expiration calendar. Nothing else
- **Frequency:** daily on SPY/QQQ
- **Fee flag:** tight targets — fee-to-edge gate will veto low-ATR days; let it

### 4.2 0DTE Afternoon Amplifier
**Thesis:** On heavy 0DTE days, dealers can flip to negative gamma in the afternoon — hedging *with* the move instead of against it. Observable signature in the underlying alone: a morning-range break after 14:00 on a 0DTE day accelerates rather than mean-reverts. Same calendar, opposite regime to 4.1 — the two strategies are natural adversaries, and which one owns the afternoon is itself a regime signal.
- **Filter:** 0DTE day AND time ≥ 14:00 AND morning range (9:30–12:00) held until 14:00
- **Entry:** 5m close beyond the morning range extreme → go with it
- **Exit:** 1.5R trail into 15:55 flat
- **Data:** OHLCV + calendar
- **Frequency:** ~1–2/week on SPY/QQQ

---

## SECTION 5 — CROSS-ASSET

### 5.1 BTC Overnight Oracle
**Thesis:** BTC is the only liquid risk asset that trades while US equities sleep — it's a free overnight risk-sentiment poll. BTC's move during equity-closed hours carries information about the equity open. And the mirror leg feeds your live v1 bot today: an S&P futures gap is an input signal for crypto's first liquid-session hours.
- **Leg A (v2 equities):** at 9:30 ET, if BTC's equity-closed-hours move z-score > 1.5, trade QQQ in that direction, 90-minute time box
- **Leg B (v1 crypto, live-relevant now):** ES overnight gap > 0.5% acts as a *directional filter layer* added to existing crypto strategies (suppress longs on big risk-off gaps) — a filter genome edit, not a new strategy, so it slots into the hierarchical edit ladder
- **Data:** BTC OHLCV (have it) + ES or SPY premarket bars
- **Frequency:** Leg A ~2/week; Leg B modifies daily flow

### 5.2 Handoff Chain
**Thesis:** Risk sentiment travels the sun: Asia close → Europe open → US open. Measure agreement across the chain (Nikkei-hours BTC drift, DAX-hours drift, US premarket) — when all three legs agree, the US session inherits momentum; when they conflict, the US session opens choppy and mean-reverting. Use the *chain agreement score* as a regime dial that any strategy in the library can consume as a filter layer.
- **Implementation:** not a standalone strategy — a shared regime feature computed once daily and exposed to every genome as an optional filter gene. Cheap to compute from data already flowing
- **Data:** BTC OHLCV sliced by global session windows (crypto proxies the regional sessions — no international feeds needed)

---

## Backtest integration notes (for Raven / Quant)

1. **Silent assertion candidate #15:** time-matched random twins for every time-anchored strategy (1.4, 2.1, 2.2, 2.5, 2.6, 3.1, 3.3, 4.1, 4.2, 5.1). An untimed random twin makes clock-driven strategies look falsely alpha-positive.
2. **Funding data (1.1):** Binance.US lists no perps. Signal source = Binance global or Bybit public funding endpoints via ccxt, no API keys required. Log the signal venue in the trade record for attribution.
3. **Fee-to-edge:** 2.6 and 4.1 have structurally tight targets. Expect the gate to veto them on low-ATR days — that's the gate working, not the strategy failing. Judge them on gate-passing days only.
4. **Long-only v1 mapping:** crypto section strategies are all long-only compatible as written. Equity/futures strategies assume long/short (Alpaca supports both in v2) — long-only variants are noted where the edge plausibly survives the amputation, but backtest both and let the numbers decide.
5. **Sign-agnostic testing:** 1.4 and 2.2 deliberately leave direction to the data. Test both signs per instrument; the graveyard entry should record which sign won and by how much — a sign that flips across instruments is itself a finding.
6. **Correlation trap:** 2.3, 5.1, and 5.2 share a risk-sentiment factor. If all three pass, they are NOT three independent edges — the Judge agent's Benjamini-Hochberg pass should treat them as one family.
7. **Expected mortality:** if more than 3–4 of these 18 survive the full lifecycle gates, be suspicious of the harness before being proud of the strategies.
