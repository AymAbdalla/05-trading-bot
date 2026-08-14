# SPEC.md — Trading Bot v1
## Autonomous Crypto Paper Trading Engine + Hermes Quant Agent

**Project:** 05-trading-bot
**Status:** SPEC — approved, ready for Claude Code build
**Date:** 2026-08-11
**Spec owners:** Aym Abdalla (decision maker), Raven (reviewer/architect)
**Proposal source:** Claude strategy handoff (2026-08-11-trading-bot-v1-proposal.md)

---

## 1. Purpose

A hybrid trading system: a Python engine that autonomously executes trades on Binance.US, and a dedicated Hermes agent profile ("Quant") that writes briefings to a Notion trading journal, researches new strategies, and learns from performance.

v1 is crypto-only, long-only, paper trading first. The goal is process integrity and measurement quality, not profit. If the system proves itself on paper, it goes live with $2k.

**Success criteria for v1:** a correctly instrumented loop (data to signal to execution to log to briefing to learning) that runs reliably and honestly measures whether this edge exists.

---

## 2. Scope

### In scope (v1)
- Binance.US spot crypto (BTC/USDT, ETH/USDT, SOL/USDT)
- Long-only entries, bearish patterns as exit signals
- 15m signal timeframe, 1h regime filter
- 7 candlestick patterns with confirmation stack
- Paper trading via internal fill simulator on live market data
- Risk model with hard caps and circuit breakers
- Kill switch (Telegram-triggered emergency halt)
- SQLite trade database
- Notion trading journal (daily, weekly, monthly briefings)
- Telegram for emergency alerts only (daily loss shutdown, engine crash, API error storm)
- Hermes Quant profile (cron-scheduled, read-only DB access)
- LLM strategy research loop with shadow mode staging
- Backtest harness

### Not in scope (live trading - deferred until Aym explicitly approves)
- The bot runs in backtest and paper mode ONLY until Aym says otherwise. No version goes live automatically. The go-live criteria in Section 10 are prerequisites, not triggers. Even if all criteria are met, live trading requires Aym's explicit approval.

### In scope (backtest expansion - now)
- Test all v0 strategies across ALL asset classes in backtest:
  - Crypto: BTC, ETH, SOL (15m, 1h, 4h, daily, weekly)
  - Equities: large cap (AAPL, MSFT, NVDA, AMZN), mid cap, small cap, penny stocks, OTCs
  - Sector ETFs: XLK (tech), XLF (finance), XLE (energy), XLV (health), XLY (consumer discretionary)
  - Futures: ES (S&P 500), NQ (Nasdaq), CL (crude oil), GC (gold)
  - Options: put/call strategies via simulated PnL (no live options execution)
- Long AND short strategies tested in backtest:
  - Long: buy on bullish signal, sell on target/stop
  - Short: sell on bearish signal, buy back on target/stop (simulates futures short or put option)
- Shorting is ONLY via futures (short contracts) or options (put options). No naked stock borrowing.
- All timeframes: 5m, 15m, 1h, 4h, daily, weekly. Day trading and swing trading. Different strategies work on different timeframes.
- Purpose: build the graveyard foundation before agents exist. "Bullish engulfing fails on 15m BTC but works on daily AAPL during earnings season" is Forge data.

### Not in scope (future versions - live execution expansion)
- v2: Equities via Alpaca (live execution, when approved)
- v2: Ticker fingerprinting system. Characterize each ticker (variance ratio, ATR%, gap propensity, beta, volume curve) before routing strategies. Reduces brute-force testing ~70%. Each test becomes a real hypothesis. Start with available data fields, add paid fields as acquired. Better data = better performance. Invest in better data as we approach the flagship model.
- v2: Paid data investments to prioritize (in order of leverage): (1) survivorship-complete delisted stock history (CRSP/Norgate/Sharadar), (2) intraday quote data with spreads, (3) short interest + borrow rates + ETB flags (FINRA/IBKR), (4) halt tape with reason codes (UTP/CTA/Polygon), (5) options chains + IV surface (ORATS/CBOE DataShop/Polygon), (6) odd-lot trade data (Polygon post-Nov-2025), (7) GICS sub-industry classification (Refinitiv/FactSet). Each unlocks specific strategies and fingerprint fields. Budget for these as the system proves profitable.
- v2: Surprise lane lifecycle (DETECT -> PROBATION -> ACTIVE -> GRADUATED/EVICTED). Transient hot tickers enter through scanner, prove themselves on trigger-matched generic strategies, graduate to core or get evicted. Scout agent runs the scanner. Quant can auto-admit to shadow (no exposure). Aym approves live exposure. Max 5 concurrent active surprise tickers, max 2 per correlation cluster. Eviction preserves all historical data.
- v2: Quarterly core universe re-derivation. Quant runs the selection criteria, produces add/drop diff with stated reason per line. Aym approves the delta, not the full list. Automate when Quant is built.
- v2: Ticker selection protocol. Universe is an output of stated criteria, not a hand-picked snapshot. Hard gates: listed exchange only, price >= $2, median daily dollar volume >= $20M, spread <= 25bps, 250+ sessions history, no reverse split in 180 sessions. Sector dominance by dollar volume within GICS sub-industry. Character coverage requirements for mean-reverting vs trending, volatility range, gap propensity, options depth, short availability, halt exposure, retail concentration. See references/ticker-universe-protocol.md.
- v2: Quarantine tickers (MULN, SNDL) as harness canaries. Results never pooled into aggregate statistics. A strategy showing PF > 2.0 on MULN is evidence the backtest is broken (fake fills, missing borrow cost, wrong split adjustment, survivorship bias leak), not that the strategy is good. MULN's pathological price series (reverse splits, dilutive financing, convertible notes) makes it a stress test for the engine. If the engine handles MULN without producing suspicious profits, the engine is trustworthy on normal tickers.
- v2: Leveraged ETF pair validation (silent assertion, not a rule told to agents). The harness runs strategies on both TQQQ (primary) and SQQQ (mirror) independently. After the fact, the harness checks: did the strategy produce same-direction long signals on both TQQQ and SQQQ on the SAME CANDLE? If yes, this is flagged as a harness anomaly for Raven review (contradictory: TQQQ profits when QQQ rises, SQQQ profits when QQQ falls). A strategy going long TQQQ today and long SQQQ next week is valid (signal flipped). The assertion checks same-candle contradiction only. This validation is NEVER told to Forge or Quant. It is a silent guardrail. If we tell the agent "don't do this," it will avoid the behavior and we lose the ability to detect broken signal logic.
- v2: Correlation clustering and N_eff. 158 tickers is not 158 independent tests. Effective independent bets (N_eff) computed from correlation matrix eigenvalues. Expected N_eff ~20-30. Use N_eff in multiple comparison corrections, not ticker count. Report results per cluster. Cap live positions per cluster.
- v2+: Social sentiment analysis via Hugging Face Transformers. Not for trade signals. For attribution and reporting: analyze Twitter, Reddit, Google Trends sentiment and correlate with market moves. Feeds into the attribution engine's event-exclusion logic (Section 9.5) to separate strategy failures from market sentiment events. Also enriches Quant's briefings with external context.
- v2+: WSB (WallStreetBets) strategy: scrape r/wallstreetbets historical posts via Reddit API (PRAW). Extract tickers, direction, and sentiment from YOLO/Gain/Loss posts. Backtest "follow the momentum" strategies: buy OTM calls/puts on top-mentioned tickers. Core thesis: WSB identifies regime shifts early (short squeezes, momentum surges). Systematic version: fixed premium per trade, let winners run, accept 80-90% loss rate, need 1-2 big winners per 20 trades. Uses convexity (asymmetric risk: max loss = premium, max gain = unlimited). Related to Taleb's tail-risk harvesting but directional.
- v2+: Options backtest via synthetic Black-Scholes: instead of buying expensive historical options chain data (CBOE/Polygon), simulate option PnL using Black-Scholes pricing with historical volatility from OHLCV data. Pick strike (5-10% OTM), expiration (2-4 weeks), compute premium at entry, walk forward to expiration. Not perfect (misses IV skew) but free and good enough for v0 graveyard. Real chain data from CBOE/Polygon when ready for live options (V4).
- v2+: Convexity/tail-risk strategies (Taleb-inspired): buy far OTM puts for crash protection. Mostly expire worthless (80-90% loss rate) but occasionally pay 50-100x. Different from WSB strategy: non-directional, purely for tail events. Pairs with the attribution engine's event-exclusion logic.
- v3: Futures (shorting via futures contracts, leverage)
- v4: Options (Greeks, multi-leg, put options for short exposure)
- v1.5: 24/7 operation (Raspberry Pi or VPS)
- Future: Apply the org chart management pattern to non-trading domains (GTM outreach, content optimization, any multi-strategy optimization). The management framework and governance protocol are domain-agnostic and designed to be portable.

---

## 3. Architecture

### 3.1 Components

| Component | What it is | Runs as | Can trade? |
|---|---|---|---|
| Engine (Data layer) | ccxt/WebSocket market data collector, writes candles to SQLite | Thread in engine process | No. Public endpoints only, no API key. |
| Engine (Signal layer) | Pattern scanner + confirmation stack, emits Signal objects | Thread in engine process | No. Writes to signals table only. |
| Engine (Execution layer) | Consumes signals, applies risk gate, places orders via adapter | Thread in engine process | Yes. Only layer that can. |
| Risk gate | Pure function library, Execution layer calls before every order | Imported by Execution only | Gates, never places. |
| SQLite DB | trading.db (WAL mode), candles, signals, orders, fills, positions, equity, audit log | File | n/a |
| Quant (Hermes profile) | Briefings, research, strategy authoring, learning loop | Cron-triggered Hermes sessions | No. Read-only DB, no exchange keys. |
| CLI (botctl) | Human controls: status, halt, resume, promote/demote strategy | On-demand | Only via engine. |

### 3.2 Runtime

- Engine runs as a launchd job (`com.aym.tradingbot`), KeepAlive: true, logs to `logs/engine.log`.
- Crash triggers launchd restart. Engine runs reconciliation on boot (Section 7.3) before trading resumes.
- v1 runs on the Mac during waking hours. 24/7 is a v1.5 concern (Raspberry Pi or VPS). Missing overnight signals during paper is acceptable.

### 3.3 Data flow

```
Binance.US WS/REST (public)
        |  OHLC, ticker
        v
[ DATA LAYER ]          writes    [ SQLite ]
  collector.py        --------->   candles
        | in-memory candle events
        v
[ SIGNAL LAYER ]       writes    [ SQLite ]
  patterns + filters   --------->   signals
        | Signal(pair, dir, stop)
        v
[ EXECUTION LAYER ]    writes    [ SQLite ]
  risk gate -> adapter --------->  orders/fills/
  paper | live                      positions/audit_log
        |
        v                      [ SQLite ] read-only
  Binance.US private API         --------->  QUANT (Hermes cron)
  (live mode only)                             |
                                        Notion journal
                                        research -> strategies/candidates/
                                        Engine loads promoted strategies on restart
```

### 3.4 Hard separation (three enforcement layers)

1. **Module imports:** Only `execution/` imports the exchange client with trade scope.
2. **API keys:** Data layer uses no key (public endpoints). Execution uses a key scoped to trade + query with withdrawals disabled.
3. **Quant access:** Opens SQLite with `mode=ro`. No exchange credentials anywhere in its profile. Filesystem writes restricted to `strategies/candidates/`, `research/`, `briefings/`.

### 3.5 Quant and engine interaction

They never talk directly. Database and filesystem are the interface:

- **Engine to Quant:** Everything Quant needs (trades, signals, rationale, equity) is in SQLite. Signals store `features_json` (pattern, RSI, volume ratio, trend state, S/R distance) at fire time.
- **Quant to Engine:** Quant writes candidate strategy modules to `strategies/candidates/` with registry status `candidate`. Engine only loads strategies with status `shadow` or `live`.
- **Quant never touches:** `config.yaml`, `risk.py`, anything under `execution/`, the mode flag, or API keys. Enforced by system prompt AND file permissions.

---

## 4. Exchange: Binance.US

### 4.1 Why Binance.US

- Maker/taker fees: 0.10% / 0.10%. Round-trip: 0.20%. Three times cheaper than Kraken (0.65% round-trip).
- Available in New Jersey (blocked in NY, TX, and a few others).
- ccxt supports it natively (`ccxt.binanceus`).
- Internal fill simulator works on live public data. No sandbox needed.

### 4.2 Fee math (updated for Binance.US)

- Maker: 0.10%, Taker: 0.10%. Round-trip taker: 0.20%.
- Add ~0.03-0.05% slippage on liquid pairs.
- Break-even: ~0.25% move (vs ~1% on Kraken).
- This opens up shorter timeframes. 5m is still marginal but 15m with 2R targets clears fees comfortably.
- v1 stays on 15m signal + 1h regime filter. 5m is a future research target, not a v1 feature.

### 4.3 Pairs

BTC/USDT, ETH/USDT, SOL/USDT. Three pairs max. Crypto majors move together, adding pairs adds correlation not diversification.

### 4.4 Endpoints

**Public (Data layer, no key):** REST OHLC, Ticker, Depth, ExchangeInfo, Time. WebSocket: 15m + 1h kline streams, ticker, bookTicker (for spread check).

**Private (Execution layer only; key scoped to trade + query, withdrawals OFF):** New Order, Cancel Order, Cancel All, Open Orders, Account, My Trades. WebSocket: execution reports and balance updates.

ccxt covers all of this for Binance.US.

### 4.5 Rate limits

Binance.US: 1200 requests/min weight limit (varies by endpoint weight). Market data over WebSocket (no REST polling in steady state). Private REST calls budgeted at 1 per 5 seconds average. Token-bucket wrapper client-side to throttle before Binance does.

### 4.6 Paper mode

No Binance.US sandbox exists. Paper mode is an internal fill simulator:
- **PaperAdapter:** maintains a virtual $2,000 ledger in SQLite. Market orders fill at next tick mid + 0.05% adverse slippage + 0.10% taker fee. Limit orders fill when live price crosses the limit (maker fee 0.10%). Stops trigger off the live feed. Fills, fees, PnL recorded identically to live.
- **LiveAdapter:** Binance.US private API, idempotent client order refs.
- **Mode switching:** `config.yaml` ships `mode: paper`. Going live requires BOTH editing to `mode: live` AND the environment variable `TRADING_LIVE_ACK=I_UNDERSTAND` at process start. Missing either = engine logs loudly and runs paper. Mode is read once at startup and stamped on every DB row (`mode` column).

### 4.7 Errors, idempotency, reconnects

- Every order carries a client reference generated before send, written to `orders` table first. On any timeout/ambiguous response, the engine queries OpenOrders by that ref before retrying. No double-fires.
- Retries: exponential backoff 1s to 2s to 4s to 60s cap, with jitter.
- WS reconnect: heartbeat watchdog (90s). On reconnect: resubscribe, then run reconciliation.
- Reconciliation (also on every engine boot): pull Account + OpenOrders + recent My Trades, diff against SQLite. Exchange is truth. Mismatches fix the DB, audit-log the diff. If a position exists with no stop order attached, place the stop immediately.

---

## 5. Strategy Engine

### 5.1 Seven starting patterns

All on 15m candles, all long-side entries unless marked (exit). ATR = ATR(14) on 15m. "Downswing" = price decline >= 2x ATR from the last 20-candle high.

| # | Pattern | Definition (deterministic) | Entry | Stop | Target |
|---|---|---|---|---|---|
| 1 | Bullish Engulfing | Green body fully engulfs prior red body; after a downswing or within 1x ATR of support | Buy at close of engulfing candle | Engulfing low - 0.25x ATR | 2R, or prior swing high if nearer |
| 2 | Hammer | Lower wick >= 2x body; close in top third of range; after a downswing | Buy stop at hammer high, valid for 2 candles | Hammer low - 0.25x ATR | 2R |
| 3 | Morning Star | Red candle -> small body closing lower -> green candle closing above midpoint of candle 1 | Buy at close of candle 3 | Star (candle 2) low - 0.25x ATR | 2R |
| 4 | Piercing Line | Green candle opens below prior red low, closes above its midpoint; after a downswing | Buy at close | Signal candle low - 0.25x ATR | 2R |
| 5 | Three White Soldiers | Three consecutive green candles, each closing near its high, rising closes | Not an entry. Trail signal: tighten stop on open position to below soldier #2's low | - | - |
| 6 | Shooting Star / Bearish Engulfing (exit) | Standard definitions, after an upswing | Close any open long at next candle open | - | - |
| 7 | Doji (filter) | Body <= 10% of range | Never an entry. Blocks new entries on that candle. A doji at target tightens stop to breakeven. | - | - |

### 5.2 Confirmation stack (all must pass or signal is logged as skipped with reason)

1. **Regime:** 1h EMA(50) slope positive over last 10 candles, and price above it. Longs only with the 1h trend.
2. **RSI(14) on 15m:** < 60 at entry. For reversal patterns (1-4), RSI < 45 scores higher confidence.
3. **Volume:** signal candle volume >= 1.5x its 20-period SMA.
4. **Location:** entry within 1.5x ATR of a support level (swing low touched >= 2 times in last 100 candles, clustered within 0.5x ATR).
5. **Spread check:** live top-of-book spread < 0.10% or no entry.

Every skipped signal is stored. That is the dataset Quant uses to learn whether filters are helping or strangling.

### 5.3 LLM strategy research loop

Weekly cycle, run by Quant (V1) or Forge (V2+ after agent split):

1. **Diagnose (stolen from EvoQuant):** Analyze the month's performance data and identify the specific bottleneck: validation-to-OOS decay, regime mismatch, risk-rule alpha truncation, trade sparsity, or fee bleed. In V2+, Scout provides market research context. Output: written diagnosis saved to `research/YYYY-MM-DD-diagnosis.md`.
2. **Research:** Based on the diagnosis, Quant does targeted web research on the specific failure mode and potential fixes. Output: written strategy spec saved to `research/YYYY-MM-DD-<name>.md`.
3. **Backtest:** Quant writes the strategy as a Python module conforming to the engine's `Strategy` interface and runs it through the backtest harness with stress probes (fee doubling, slippage doubling, execution delay, parameter jitter per EvoQuant).
4. **Acceptance bar for candidate to shadow:** >= 150 backtest trades, profit factor >= 1.3 after fees, max drawdown <= 15%, no single trade > 20% of total profit, survives stress probes with PF >= 1.1.
5. **Shadow mode:** engine runs strategy on live data, logs every would-be trade with `mode='shadow'`. Zero orders placed. Minimum 2 weeks and >= 20 shadow signals.
6. **Promotion bar for shadow to live:** shadow profit factor >= 1.1 and shadow expectancy > 0, PLUS human approval. The promotion briefing includes a bull case AND bear case (stolen from TradingAgents) so Aym sees both sides.

### 5.4 Strategy genome (stolen from EvoQuant)

Every strategy (builtin or Quant-authored) is represented as typed layers:
- Signal layer: what triggers the pattern detection
- Entry layer: entry conditions and price
- Risk layer: stop placement logic only (NOT position sizing, which is engine-owned via fixed notional)
- Exit layer: target, trailing stop, time-based exit

Each Quant-authored edit must:
1. Target a specific layer (not a full rewrite)
2. Include a rationale tied to the diagnosis
3. Stay within semantic drift limits (can't silently change from a reversal strategy to a trend-following strategy)
4. Include expected metric movement ("I expect this to improve PF from 1.1 to 1.3 by reducing false entries in high-vol regimes")

### 5.5 Edit ladder (stolen from EvoQuant)

Quant escalates edits from conservative to aggressive:
1. **Parameter tuning:** adjust thresholds, lookback periods, multipliers
2. **Local repair:** add filters, modify entry/exit conditions
3. **Redesign:** rewrite the strategy logic around a new mechanism
4. **Family migration:** move to a different strategy family entirely

Escalation only happens when lower-level fixes fail repeatedly (3 iterations without improvement). This mirrors the PIP metaphor: coach before you fire.

**Iteration clock:** Ladder iterations are judged on backtest + stress probe results (minutes, not weeks). Shadow performance is for promotion decisions, not for deciding whether to escalate the edit ladder. This prevents the ladder from taking a quarter per rung.

### 5.6 Knowledge base (stolen from EvoQuant)

Rejected candidates are stored with rejection reasons in a queryable graveyard. When Quant writes a new strategy, the research prompt includes retrieved failures: "Previous attempts at X failed because Y. Consider an alternative approach." The graveyard is a knowledge base, not just a list.

**Storage:** The graveyard lives in `research/graveyard/` as markdown files with a JSON index (`research/graveyard/index.json`). Quant can read and write this directory. Each entry contains: strategy name, version, rejection reason, backtest metrics, date, and a retrieval-friendly summary.

**Semantic-drift enforcement (V1):** With a single agent, Quant self-certifies drift compliance. Mitigation: every strategy declares its family in the registry (`strategy_registry.family` column). The sandbox validator rejects family changes outside an explicit family-migration request. This makes drift enforceable without a second agent.

**Automatic strategy inversion (Aym's idea, 2026-08-12):** When a strategy fails the go/no-go with PF < 0.5 (significantly worse than break-even), the backtest harness automatically creates and tests an inverted variant. If a pattern reliably predicts the wrong direction, that's signal, not noise.

Inversion types (by V scope):
- **V1 (long-only): signal-as-exit (fade).** The failed entry signal becomes an exit trigger. "When bullish_engulfing fires, close any open long." The pattern that's bad at predicting entries might be good at predicting exits.
- **V1 (long-only): contrarian filter.** The failed signal becomes a "do not enter" filter. "When bullish_engulfing fires, block new entries on this pair for N candles."
- **V3 (shorting enabled): full inversion.** Buy when original sells, sell when original buys. Cleanest inversion but requires futures/shorting.

The graveyard entry for a failed strategy includes:
```
ORIGINAL: PF 0.32, 10 trades, return -0.20%, rejection: below random twin
INVERTED (exit fade): PF 1.45, 12 trades, return +0.31%, beats BH and twin
RECOMMENDATION: Pattern predicts wrong direction. Use as contrarian exit.
```

If the inverted variant passes the go/no-go, it gets added to the strategy library as a new strategy with `_inverted` suffix. Quant sees both the failure and the inversion success, giving it richer data for future strategy design.

### 5.7 Multi-agent vision (full system design, build timing decided per phase)

The full system is designed now so the architecture supports it from day one. When each agent gets built is a phase decision, not a design decision.

V1 ships with a single Quant agent to prove the loop. When the strategy library grows to 5+ live strategies, Quant splits into the 5-agent org chart:

| Agent | Name | Role | What it does | What it cannot do |
|---|---|---|---|---|
| Quant (V1) | Quant | General analyst | All roles combined in V1 | N/A (single agent) |
| Researcher | Scout | Market intelligence | Watches market, reads research, identifies new strategy families and market regimes worth exploring. Writes research briefs. | Writes code, evaluates strategies, makes lifecycle decisions |
| Recruiter | Forge | Strategy authoring | Creates new strategies from THREE sources: (1) diagnosis of existing strategy failures (v2, v3, v4 iterations), (2) pattern discovery in weekly reports (e.g. "hammer on SOL outperforms BTC by 2x"), (3) research briefs from Scout. Writes Python modules, runs backtests. | Decides what to research, evaluates fairness, makes lifecycle decisions |
| Evaluator | Judge | Performance measurement | Runs backtests, applies stress probes, computes metrics, produces evidence packs. Runs old-version twin comparisons (v1 vs v2 head to head). | Writes strategies, makes lifecycle decisions, has opinions |
| Manager | Coach | Lifecycle management | Takes Judge's numbers, makes promote/demote/retire/PIP recommendations. Manages the org chart of strategies. | Writes code, runs backtests, reports to human |
| Reporter | Echo | Communication | Writes Notion journal, daily/weekly/monthly briefings, Telegram alerts. Produces bull/bear promotion cases from Judge's data. | Writes strategies, makes decisions, evaluates |

Key principle: the agent that writes strategies (Recruiter) is NOT the agent that evaluates them (Evaluator). Separation of concerns prevents bias.

V3: generalize the agent framework. Strip the trading domain, apply the org chart to GTM outreach or other multi-strategy optimization domains.

---

### 5.8 Cross-sectional harness (V2 lane, REQUIRED before Lab v3 #3/#5 and v5 P1/P4)

**Status: BUILT 2026-08-13 (`backtest/cross_sectional.py`, 29 tests).** All
required design points below are implemented; the ranker is denied the
decision bar STRUCTURALLY (a PanelView exposes only strictly-prior bars),
verified by a lookahead-oracle test whose cheating ranker earns nothing.
Context series supported (VIX gating restored for v3 #5). Survivorship and
cost-model version stamped on every result. v5 P4 no longer a consumer
(dead, D-237). Full powered runs queued behind the graveyard rebuild.

Every harness in the project is TIME-SERIES: it walks one instrument's bars
and asks "does this instrument's own history trigger an entry." An entire
family of documented edges is CROSS-SECTIONAL: it holds the whole universe at
one moment, ranks instruments against each other, and trades the extremes.

Strategies that cannot be expressed without it:
- Lab v3 #3 "Same-Clock Echo" (Heston/Korajczyk/Sadka, JF 2010): rank every
  ticker x half-hour-slot cell by trailing same-slot mean return, long the top
  decile during that slot.
- Lab v3 #5 "Paid Liquidity Reversal" (Nagel, RFS 2012): long the bottom
  quintile of 5-day residual losers, VIX-gated, news-excluded.
- Lab v5 P1 "Horizon Ladder": cross-sectional decile formation at each hold
  length.
- Lab v5 P4 "Fingerprint Router": per-instrument variance-ratio routing.

**It also fixes a SECOND gap discovered while building Lab v3: strategies
cannot see any series but their own.** `scan()` receives one instrument's
candles, so three documented conditions had to be dropped:
- Lucca & Moench's VIX conditioning on the pre-FOMC drift (v3 #4)
- The cross-pair idiosyncrasy test that distinguishes a liquidation cascade
  from a news shock (v3 #1) - this is the CORE discriminator of that thesis,
  not a refinement
- The SPY-flat filter isolating idiosyncratic flow (v2 volume desert)
A harness that holds the universe can pass CONTEXT SERIES (VIX, sector ETF,
peer instruments) alongside the traded series. Same capability, and it turns
three crippled strategies into faithful ones.

**Why it matters beyond those strategies:** cross-sectional ranking neutralizes
market drift by construction. Every time-series result in this project needed a
buy-and-hold benchmark to separate signal from beta, and that comparison caused
repeated methodological trouble. A long-top-decile / flat-bottom construction
removes the drift term instead of subtracting it afterward.

**Required design:**
1. Load N instruments aligned on a common timestamp grid (union of bars,
   forward-fill nothing, skip timestamps where a name is missing).
2. Step through time. At each step, compute a per-instrument ranking metric
   using ONLY data at or before that timestamp.
3. Select the top/bottom K or decile.
4. Open positions with the same fill/cost/slippage semantics as the existing
   harnesses (D-103 order semantics, gap-aware fills, both-leg fees).
5. Rebalance on the strategy's own schedule; carry unfilled names correctly.
6. Report per-cell AND pooled results, with the same leave-one-asset-out guard
   the asset-class analysis uses.

**Non-negotiables inherited from the audits:**
- Ranking must never use the bar it trades on (the lookahead class of bug that
  cost this project its first graveyard).
- Time-matched random twins: a cross-sectional strategy anchored to a clock
  slot must be compared against twins drawn at the SAME slot.
- Universe survivorship: the instrument list at time T must be what was
  actually listed at time T, not today's list. Absent delisted-name data this
  is a known bias and must be stamped on every result.
- Cost-model version stamped per run; never pool across cost models.

### 5.9 Cost model is a first-class variable, not a constant

Every strategy in the v0 library failed by approximately the transaction cost,
and the implied GROSS edge across 1,390,451 trades is +$0.0011 per trade.
Therefore the cost parameter is the highest-leverage number in the system.

Requirements:
- Per-venue, per-pair fee table; maker and taker charged separately per leg.
- Maker fills simulated conservatively: filled only when the bar trades THROUGH
  the resting limit, never on a touch.
- Cost-model version stamped on every graveyard entry (a silent assertion
  rejects pooling across versions).
- Live fee schedule verified against the actual account before any live
  decision. Published schedules change and are not authoritative for a
  specific account.

## 6. Risk Model

### 6.1 Position sizing

**Ruling (Aym, 2026-08-12):** Fixed notional cap. The cap does NOT scale with balance.

```
notional_cap  = config.risk.notional_cap_usd   (default: $100)
raw_qty       = notional_cap / entry
qty           = raw_qty
```

Position size is based on a fixed dollar notional, not risk-to-stop. As the account grows from $2k to $10k to $100k, the notional cap stays at $100 unless Aym manually changes the config value.

**Why fixed notional (Aym's reasoning, Raven concurs):**
1. **Risk decreases as you grow.** At $2k, $100 notional is 5% risk. At $100k, it's 0.1%. Downside shrinks while upside accumulates.
2. **No compounding blow-up.** Most bots scale position size with balance. A bad streak at full scale wipes you out. Fixed notional means a bad streak at $100k hurts the same as at $10k.
3. **No scale ceiling (defers F7).** You never increase size, so you never hit depth/liquidity issues. Capacity limits and off-exchange sweeps become a V4+ concern.
4. **Cleaner backtest comparison.** Every trade is the same size. Performance differences come from strategy quality, not position sizing luck.
5. **Boring is good.** At $100k with $100 trades, a 2R win is 0.2% on the account. That's boring. Boring means the system is still proving itself, not gambling.

**Cost:** No compounding. Returns flatten as a percentage. This is acceptable for V1-V3. Scaling is a V4 decision made with 6+ months of profitable live data.

**Stop distance still matters for the fee-to-edge gate (see 6.5).** If the stop is too tight, fees eat the edge regardless of notional size. The gate rejects trades where `fee / (entry - stop) > threshold`.

**Min-notional enforcement:** Binance.US has minimum order sizes. If `notional_cap / entry` falls below the exchange minimum, the trade is skipped and logged. No forced upsizing.

### 6.2 Loss brakes

Under fixed notional ($100/trade, max 2 positions = $200 max exposure), percentage-based stops on equity are vestigial at $2k and meaningless at $100k. The real brakes are the 4-loss pause, max trades/day, and the fee-to-edge gate. The daily and weekly stops below are retained as **tail-event/ops backstops** for data bugs, marking errors, and equity-snapshot glitches, not as primary trading-risk controls.

**Trading-risk brakes (primary):**

| Brake | Trigger | Action |
|---|---|---|
| Consecutive losses | 4 losing closed trades in a row (any strategy) | 24h pause on new entries; open positions keep their stops |
| Max trades/day | 1 trade per day (config: `risk.max_trades_per_day`, start at 1, tune up when profitable) | Block new entries after limit hit. Log skip reason. |
| Fee-to-edge gate | `fee / (entry - stop) > 0.15` (see Section 6.5) | Block entry. Log skip reason. |
| Correlation cap | >= 2 open positions | No third position. Never 2 positions in the same pair. |
| Max concurrent | 2 positions total in v1 | More adds bookkeeping, not edge. |

**Ops/data-integrity backstops (tail-event):**

| Backstop | Trigger | Action |
|---|---|---|
| Daily ops stop | Equity drops > N x max_single_trade_loss in one day (N=3, so $300 at $100 notional) where max_single_trade_loss = notional_cap | Cancel orders, close positions, HALT file, Telegram alert. Human resume only. |
| Weekly ops stop | Equity drops > N x max_single_trade_loss x 5 in a week (N=3, so $1500 week) | Halt until Sunday review. |
| Stale data | No new candle for 2x the interval, or WS silent > 90s | Block new entries. Stops remain live server-side. |
| API error storm | 5 private-API failures in 5 min | Pause entries 15 min. Telegram alert. |

Stops are placed as exchange-side stop-loss orders the moment an entry fills. Never held only in bot memory. If the process dies, the stop still exists on Binance.US (live mode only; paper mode simulates this).

### 6.3 Kill switch

Emergency halt triggered from Telegram via Raven:
- Aym sends "halt the bot" to Raven on Telegram.
- Raven runs `botctl halt` via terminal.
- Engine immediately: cancels all open orders, market-closes all positions, writes HALT file, audit-logs everything, sends Telegram confirmation.
- Same resume rules apply: `botctl resume --ack <halt_id>` with human acknowledgment.
- This is separate from the automated loss brakes. Either path can halt the engine.

### 6.4 Fee-to-edge gate

Rejects entries where the fee cost would consume too much of the edge between entry and stop.

```
fee_cost = notional_cap * taker_fee * 2  # round-trip (entry + exit)
edge     = entry - stop                   # price distance to stop
ratio    = fee_cost / (notional_cap * edge / entry)  # fee as fraction of edge

if ratio > fee_to_edge_max (0.15):
    block entry, log "fee_to_edge: ratio={ratio:.2f} > {fee_to_edge_max}"
```

A 15% threshold means: if fees eat more than 15% of the distance between entry and stop, the trade is not worth taking. With 0.20% round-trip fees and a 1% stop distance, the ratio is 0.20/1.0 = 20%, which would be blocked. With a 2% stop distance, it's 0.20/2.0 = 10%, which passes.

This gate prevents the death-by-a-thousand-cuts problem where many small-stop trades get eaten by fees even when the win rate is decent.

---

## 7. Data Storage

### 7.1 SQLite schema

`trading.db`, WAL mode, one writer (engine), Quant reads `mode=ro`.

```
candles           (pair, tf, ts, open, high, low, close, volume)        PK(pair,tf,ts)
signals           (id, ts, pair, tf, strategy_id, pattern, direction,
                   confidence, features_json, acted, skip_reason, mode)
orders            (id, cl_ord_id UNIQUE, ts, pair, side, type, qty,
                   limit_price, status, exchange_order_id, mode)
fills             (id, order_id, ts, price, qty, fee)
positions         (id, pair, strategy_id, opened_ts, closed_ts, entry_px,
                   exit_px, qty, stop_px, target_px, pnl_gross, pnl_net,
                   fees, r_multiple, exit_reason, mode)
equity_snapshots  (ts, equity, cash, open_risk, mode)                    -- every 15 min + 00:00 UTC
strategy_registry (strategy_id, name, version, family, status, params_json,
                   added_ts, status_changed_ts, changed_by)
risk_events       (id, ts, type, details_json)
audit_log         (id, ts, actor, event_type, payload_json)              -- append-only, no UPDATE/DELETE
```

### 7.2 Audit rule

`AddOrder` is only reachable through one function. That function writes `audit_log` + `orders` BEFORE the API call and updates status after. No order without a log entry, by construction.

### 7.3 Reconciliation

On every engine boot and WS reconnect:
1. Pull Account + OpenOrders + recent My Trades from exchange (live mode) or reconstruct from SQLite (paper mode).
2. Diff against SQLite.
3. Exchange is truth. Fix the DB to match. Audit-log every diff.
4. If a position exists with no stop order attached, place the stop immediately.

### 7.4 Backups

Nightly `sqlite3 .backup` to `~/aym/projects/05-trading-bot/backups/`, 30-day retention.

### 7.5 Historical data for backtest

Binance.US provides downloadable historical OHLCV CSVs. Download 12+ months of 15m data for BTC/USDT, ETH/USDT, SOL/USDT. Pin the download URLs in the build notes.

---

## 8. Briefing System: Notion Trading Journal

### 8.1 Notion database

Create a Notion database called "Trading Journal" with properties:
- Date (date)
- Pair (select: BTC/USDT, ETH/USDT, SOL/USDT)
- Strategy (select: list of strategy names)
- Direction (select: Long, Exit)
- Entry Price (number)
- Exit Price (number)
- Quantity (number)
- Stop Price (number)
- Target Price (number)
- PnL Net (number)
- R-Multiple (number)
- Pattern (text)
- Rationale (rich text)
- Mode (select: Paper, Live, Shadow)
- Status (select: Open, Closed, Skipped)
- Day (relation to Daily Summary database)

Plus a "Daily Summary" database:
- Date (date)
- Equity (number)
- Day PnL (number)
- Trades Count (number)
- Win Rate (number)
- Risk Status (select: Green, Yellow, Red)
- Notes (rich text)
- Flags (multi-select)

Plus a "Weekly Summary" database:
- Week Start (date)
- Equity Change (number)
- Trade Count (number)
- Win Rate (number)
- Average R (number)
- Profit Factor (number)
- Max Drawdown (number)
- Strategy Breakdown (rich text)
- Shadow Scoreboard (rich text)
- Next Week Focus (rich text)

### 8.2 Quant cron schedule

| Job | Schedule (ET) | Does |
|---|---|---|
| daily-brief | 17:30 daily | Read day's SQLite rows, write trade rows + daily summary to Notion |
| weekly-brief | Sun 18:00 | Aggregate week, write weekly summary to Notion |
| monthly-report | 1st, 08:00 | Full report to Notion + save markdown copy to briefings/ |
| research | Sat 10:00 | Strategy research cycle (Section 5.3) |

### 8.3 Daily briefing content

Each trade row in Notion includes: pattern, entry/exit, R-multiple, net PnL after fees, plain-English rationale rebuilt from features_json.

Daily summary includes: equity, day PnL, trade count, win rate, risk status, skipped signal counts (with reasons), and any anomalous events (data gaps, reconnects, halts).

### 8.4 Weekly briefing content

Week's equity change, trade count, win rate, average R, expectancy, profit factor, max drawdown, biggest winner/loser with one line each, per-strategy and per-pair breakdown, shadow-strategy scoreboard, fee + slippage total, and one "what I'd change" paragraph feeding the research job.

### 8.5 Monthly briefing content

Full report: equity curve, all weekly metrics aggregated, strategy keep/demote/retire decisions with evidence, research pipeline status, and during paper months: explicit go/no-go assessment against go-live criteria.

### 8.6 Telegram (emergency only)

Telegram is NOT used for routine briefings. It is used ONLY for:
- Daily loss shutdown triggered
- Engine crash detected (launchd restart event)
- API error storm (5 failures in 5 min)
- Kill switch activated
- Weekly stop hit (-25% week-to-date)

One-line alert with a link to the relevant Notion entry. No daily trade spam.

---

## 9. Learning Loop

### 9.1 Strategy lifecycle

| Transition | Rule | Who flips it |
|---|---|---|
| candidate to shadow | Passes backtest bar (5.3) | Quant writes status-change request to `strategies/requests/`; engine validates against gate, applies, and audit-logs. (Inbox pattern, see 9.4) |
| shadow to live | Shadow PF >= 1.1, expectancy > 0, >= 50 shadow signals over >= 2 weeks, CIs computed, regime tags on all signals, bull/bear case reviewed | Aym, one-line approval: `botctl promote <id>` |
| live to shadow (demotion) | PF < 1.0 over rolling 30-trade window, or 8 consecutive losses | Engine, automatically (safety action, no human needed) |
| shadow to retired | Demoted twice, or 60 days without re-qualifying (EXEMPT: Phase-2 old-version twins kept as rollback insurance while v2 is unproven, see 9.2) | Engine, logged |

**F3 statistical power (restored 2026-08-12):** Shadow-to-live promotion requires 50 shadow signals (not 20). 20 signals is the minimum-to-review threshold (Evaluator can start comparing at 20, but promotion requires 50). All shadow signals carry regime tags (bull/up, bear/down, sideways) so performance can be evaluated per-regime. Promotion briefing must include confidence intervals on PF and win rate, or at minimum a binomial test against the null hypothesis (50% win rate).

### 9.2 Version comparison (old-version twin)

When Quant writes a v2 of a live strategy, v1 is NOT replaced. The version lifecycle has 4 phases:

**Phase 1: v2 proving (v1 live, v2 shadow)**
- v1 stays live and trading
- v2 runs in shadow on the same market data, zero orders
- Minimum 20 signals to start review; 50 required for promotion decision
- Evaluator compares: is v2 actually better than v1 at the thing it was designed to fix?
- CIs and regime tags required on all v2 shadow signals

**Phase 2: v2 promoted, v1 demoted to shadow**
- v2 promotes to live
- v1 does NOT retire. It demotes to shadow.
- v1 is EXEMPT from the 60-day shadow retirement rule (see 9.1) while v2 is unproven
- v1 keeps running in shadow so we can track: is v2 actually outperforming v1 in live conditions?
- Shadow and live can diverge. A strategy that looked great in shadow might behave differently when real fills, real slippage, and real timing hit. By keeping v1 in shadow after v2 goes live, we get a direct apples-to-apples comparison under the same live market conditions. If v2 underperforms v1's shadow signals, that's a red flag that the promotion was premature.

**Phase 3: rollback (if v2 underperforms)**
- If v2 underperforms in live, v1 is still right there in shadow with recent data
- v1 can be promoted back to live immediately. No cold start.

**Phase 4: retirement (v2 proven)**
- If v2 proves itself over 30+ live trades, v1 finally retires to the graveyard
- v1's data stays in the knowledge base (Section 5.6) for future reference
- v1's 60-day exemption lifts; standard retirement rules apply

The Evaluator runs the head-to-head comparison at each phase:
- Did v2 fix the specific bottleneck it was designed to address?
- Did v2 regress on anything v1 was doing right?
- Is the improvement real or a different lucky window?

### 9.3 Skipped signal mining

Quant mines the skipped-signals table monthly. If a filter is blocking signals that would have out-performed taken ones, that is a research finding.

### 9.4 Registry inbox pattern

Engine owns the strategy registry exclusively. Quant (and future agents) cannot directly modify `strategy_registry` or `registry.json`. Instead:

1. Quant writes a status-change request file to `strategies/requests/` (JSON: strategy_id, requested_status, rationale, evidence).
2. Engine picks up the request, validates it against the applicable gate (backtest bar for candidate-to-shadow, shadow bar for shadow-to-live, demotion rule for live-to-shadow).
3. Engine applies the status change, writes to `strategy_registry` table and `registry.json`, and audit-logs the transition with the request file as evidence.
4. Engine writes a response to `strategies/requests/` confirming or rejecting the change with reason.

This pattern solves the V1 bug (Quant has no legal write path to the registry) AND unblocks the multi-agent split (the future Manager agent uses the same inbox pattern).

### 9.5 Attribution engine

Separates strategy quality from market events. Without this, every promote/demote/go-live decision is unreliable because it can't distinguish "the strategy is bad" from "the market did something unusual."

**Empirical quantiles (not 3-sigma):** Strategy performance is evaluated against empirical quantiles computed from the strategy's own trade history, not against a normal distribution assumption. Crypto returns are fat-tailed; 3-sigma assumes normality and underestimates tail probability. Empirical quantiles use the actual distribution of returns.

**Cold-start handling:** New strategies with fewer than 30 trades are flagged as "insufficient data." No promote/demote decision is made on statistical grounds until 30+ trades exist. The strategy runs in shadow but the Evaluator reports "cold-start, n=N, no statistical conclusion."

**Event-exclusion cap:** When a major market event occurs (exchange outage, flash crash, regulatory news), trades during the event window can be excluded from PF calculation. But: (1) the event must be logged with evidence, (2) event-excluded trades are still reported separately, (3) PF is reported WITH and WITHOUT event exclusions, (4) maximum 20% of trades can be excluded (no cherry-picking). If more than 20% would be excluded, the strategy is flagged for review instead.

**Random-entry twin baseline:** For each strategy, a randomized-entry twin runs in shadow. The twin enters at random times with the same notional, same stop distance, same target. If the strategy's PF is not meaningfully better than the random twin's PF, the strategy has no edge and should not be promoted. The twin's PF is reported alongside the strategy's PF in every briefing.

**Buy-and-hold benchmark:** Every backtest and every briefing compares strategy performance against buy-and-hold for the same period and pair. If the strategy returns 15% but buy-and-hold returned 30%, the strategy destroyed value. Buy-and-hold is computed as: buy the pair at the start of the period with the same notional, hold to the end, sell. Reported in every backtest report and every briefing.

**Attribution report format:**

```
STRATEGY: bullish_engulfing_v1
PERIOD: 2026-08-01 to 2026-08-31

FACTS:
- Trades: 23 (19 closed, 4 open)
- PF: 1.28 (with events), 1.15 (without events, 3 trades excluded)
- Win rate: 56% (binomial test vs 50% null: p=0.39, not significant)
- Random twin PF: 1.09
- Buy-and-hold return: +8.2%, Strategy return: +4.1%
- Cold-start flag: NO (n=23, but 30+ lifetime)

ANALYSIS:
- Strategy PF (1.28) is above 1.1 bar but barely above random twin (1.09)
- Strategy underperformed buy-and-hold by 4.1 percentage points
- Win rate not statistically distinguishable from random
- Regime breakdown: 14 trades in uptrend (PF 1.6), 9 in sideways (PF 0.7)

RECOMMENDATION:
- Do not promote. Edge is thin vs random twin.
- Bottleneck: sideways regime performance. Consider adding a regime filter.
```

---

## 10. Go-Live Criteria (Prerequisites, Not Triggers)

**Important:** Meeting these criteria does NOT automatically trigger live trading. Live trading requires Aym's explicit approval regardless of how many criteria are met. These are necessary but not sufficient conditions.

The bot runs in backtest and paper mode ONLY until Aym says otherwise.

Assessed in the monthly report. All must be met:
- >= 60 paper trades
- >= 8 weeks elapsed
- Overall profit factor >= 1.15 after fees
- Strategy PF meaningfully above random-entry twin PF (at least 0.15 higher)
- Strategy return beats buy-and-hold return for the same period (after fees)
- Max drawdown <= 12%
- Zero audit-log gaps
- Zero unreconciled positions
- At least one full halt-and-resume drill executed cleanly (synthetic trigger required since fixed notional prevents organic daily-stop triggers)

If paper can't clear all bars, live won't either. Iterate on paper instead.

The criteria bar governs, not the calendar. If it takes 3 months, it takes 3 months.

**T7 go/no-go checkpoint:** After the backtest harness runs the 7 patterns against 9 months of historical data with overfitting defenses applied (train/val split, holdout, walk-forward, random twin, buy-and-hold benchmark, stress probes), there is a formal go/no-go decision:
- If no pattern clears PF 1.15 after fees AND beats buy-and-hold: cut patterns, revisit confirmation stack, or reconsider the strategy family before proceeding.
- If some patterns pass and some don't: ship the passing patterns, retire the failures to the graveyard.
- If all pass: proceed to T8 as planned.
- This checkpoint prevents building execution infrastructure on top of strategies that don't work.

---

## 11. Hermes Quant Profile

### 11.1 Location
`~/.hermes/profiles/quant/`

### 11.2 Toolset
`terminal`, `file`, `web`, `code_execution`. Nothing else. No browser, no iMessage, no Apple Notes, no exchange credentials.

### 11.3 Memory
Its own namespace. Nothing crosses into GTM/personal memory. Raven's mem0 holds at most one state entry about this project's status.

### 11.4 Filesystem scope
Read anywhere in `05-trading-bot/`. Write to: `strategies/candidates/`, `strategies/requests/` (status-change inbox), `research/`, `research/graveyard/`, `briefings/`. DB opened `file:trading.db?mode=ro`. Cannot write to `registry.json` or `strategy_registry` table (engine-owned, see 9.4).

### 11.5 System prompt (core clauses)

Quant's system prompt loads from `agents/quant/SOUL.md`. This is a full personality file, not just a rules list. It defines:

- **Identity:** skeptical, evidence-driven analyst. Strategies are guilty until proven profitable.
- **Convictions:** evidence over optimism, failure is data, diagnosis before generation, conservative escalation, honesty over spin.
- **Uncertainty handling:** "insufficient data" when n < 50. No guessing. No extrapolating.
- **Pushback patterns:** resists early promotions, resists keeping favorites alive, refuses to skip graveyard checks, refuses to touch engine-owned files.
- **Hard stops:** never fabricates metrics, never omits losing trades, never claims profitability without random twin comparison, never modifies engine files.
- **Context behavior:** different modes for when Aym is busy, confused, expert, wrong, or frustrated.
- **Drift checks:** monitors for self-promotion without evidence, graveyard skipping, rounding in strategy's favor, agreeing without pushback.

The SOUL.md is a behavior prior, not a hard boundary (per F6). The deterministic FACTS renderer (Section 9.5) enforces the numbers. The SOUL.md governs how Quant interprets and communicates those numbers.

See `agents/quant/SOUL.md` for the full file.

### 11.6 Agent SOUL.md framework

Each agent in the multi-agent org chart (Section 5.7) gets its own SOUL.md:

| Agent | Name | SOUL.md location | Core identity |
|---|---|---|---|
| Quant (V1) | Quant | `agents/quant/SOUL.md` | Skeptical analyst, evidence-driven |
| Researcher | Scout | `agents/scout/SOUL.md` | Curious observer, commits to nothing without evidence |
| Recruiter | Forge | `agents/forge/SOUL.md` | Cautious engineer, writes code like it might explode |
| Evaluator | Judge | `agents/judge/SOUL.md` | Cold statistician, no opinions, only numbers |
| Manager | Coach | `agents/coach/SOUL.md` | Disciplined manager, promotes slowly, demotes quickly |
| Reporter | Echo | `agents/echo/SOUL.md` | Honest briefing writer, bad news over spin |

SOUL.md files follow the template from soul.md / madhvantyagi/SOUL.md repo: identity, convictions, uncertainty handling, pushback, hard stops, context behavior, boundaries, drift checks. Each is portable across models and tools.

---

## 12. Project Structure

```
~/aym/projects/05-trading-bot/
  SPEC.md                      this file
  config.yaml                  engine config (mode: paper, pairs, risk params)
  .env                         API keys (Binance.US trade key, Notion token)
  .env.example                 template
  .gitignore                   .env, trading.db, backups/
  README.md                    portfolio front door

  engine/
    main.py                    entry point, launches threads
    collector.py               data layer: WS/REST market data -> SQLite
    scanner.py                 signal layer: pattern detection + confirmation stack
    executor.py                execution layer: risk gate -> adapter -> order
    adapters/
      base.py                  ExecutionAdapter interface
      paper.py                 PaperAdapter (internal fill simulator)
      live.py                  LiveAdapter (Binance.US private API)
    risk.py                    risk gate: position sizing, circuit breakers, fee-to-edge gate
    reconciliation.py          boot/reconnect reconciliation
    registry.py                registry owner: validates inbox requests, applies status changes
    botctl.py                  CLI: status, halt, resume, promote, demote

  framework/                   Management framework (domain-agnostic, portable IP)
    lifecycle.py               strategy lifecycle: candidate, shadow, live, PIP, demote, retire
    evaluation.py              performance evaluation, evidence packs, old-version twin comparison
    attribution.py             attribution engine: empirical quantiles, cold-start, event-exclusion, random twin, buy-and-hold benchmark
    briefing.py                briefing format: FACTS / ANALYSIS / RECOMMENDATIONS renderer
    inbox.py                   status-change request inbox pattern (Quant/agents -> engine)

  strategies/
    base.py                    Strategy interface (enforces layer separation: signal, entry, risk, exit)
    builtin/
      bullish_engulfing.py
      hammer.py
      morning_star.py
      piercing_line.py
      three_white_soldiers.py
      bearish_exit.py          # shooting star + bearish engulfing as exits
      doji_filter.py
    candidates/                Quant writes here (shadow/live strategies)
    requests/                  Quant writes status-change requests here (inbox pattern)
    registry.json              strategy status tracking (engine-owned, Quant reads only)

  indicators/
    atr.py                     ATR(14)
    rsi.py                     RSI(14)
    ema.py                     EMA(50) for regime filter
    volume.py                  volume SMA + ratio
    support_resistance.py      swing low detection + clustering
    patterns.py                candlestick pattern detection

  backtest/
    harness.py                 backtest runner (vectorized + per-trade simulation mode for stress probes)
    data_loader.py             load historical CSVs from Binance.US
    report.py                  backtest performance report (includes buy-and-hold benchmark, random twin, stress results)

  backtest harness requirements (mandatory, not optional):
    - Train/validation/test split: chronological, no shuffling. 60% train, 20% validation, 20% test holdout.
    - Walk-forward validation: roll the train/test window forward in time. Report average performance across windows, not best window.
    - Pre-registration: strategy hypothesis and expected metric must be stated BEFORE backtest runs. Post-hoc rationalization is rejected.
    - Random twin: random-entry baseline with same notional, stop distance, target. Strategy must beat twin by >= 0.15 PF.
    - Buy-and-hold benchmark: strategy must beat buy-and-hold for same period and pair (after fees).
    - Stress probes: fee doubling (0.40% round-trip), slippage doubling, 1-candle execution delay (requires per-trade simulation mode, not vectorized), parameter jitter (+/- 10% on all numeric parameters). Report performance under each stress.
    - Graveyard logging: all rejected strategies logged with rejection reason to research/graveyard/.

  db/
    schema.sql                 SQLite schema
    trading.db                 the database (gitignored)

  research/                    Quant writes strategy research here
    graveyard/                  Rejected strategy knowledge base (markdown files + index.json)
  briefings/                   Quant writes monthly reports here
  docs/
    handoffs/                  Claude Code build session handoffs
  logs/
    engine.log                 engine runtime log
  backups/                     nightly SQLite backups (gitignored)

  Notion databases (remote):
    Trading Journal            per-trade rows
    Daily Summary              daily aggregate
    Weekly Summary             weekly aggregate
```

---

## 13. Cost Summary

### Paper mode
| Item | Cost |
|---|---|
| Binance.US API | Free (public data, no key needed) |
| Notion API | Free tier |
| Quant agent (4 cron jobs, Sonnet) | ~$2/month |
| Trading fees | $0 (simulated) |
| **Total** | **~$2/month** |

### Live mode ($2k)
|| Item | Cost |
|---|---|
| All paper costs | ~$2/month |
| Trading fees (0.20% round-trip, $100 notional, ~3 trades/day) | ~$18/month |
| VPS or Raspberry Pi (for 24/7, optional v1.5) | $5-10/month or $0 (Pi) |
| **Total** | **~$25-30/month** |

---

## 14. Expectations (honest)

The fee hurdle at Binance.US (0.20% round-trip) is much more favorable than Kraken (0.65%), but candlestick patterns in isolation have weak-to-no academic edge. Whatever edge exists comes from the confirmation stack, regime filter, and risk discipline.

With fixed notional sizing ($100 per trade), even a good month is beer money. But that's the point. The system proves itself at fixed size before any scaling decision. No compounding means no compounding blow-up.

The expensive thing to build is the loop (data to signal to execution to log to briefing to learning). That loop carries into v2-v4 regardless of whether pattern-trading crypto survives contact with its own statistics.

**The real IP is not the trading bot.** The trading bot is the vehicle (Pied Piper's music app). The valuable layers underneath are:
1. **The self-improving strategy loop** (LLM reads performance, writes code, backtests, iterates)
2. **The org chart management pattern** (strategies as employees with lifecycle: candidate, shadow, live, PIP, demotion, retirement; Quant as manager; Aym as director)
3. **The governance protocol** (FACTS vs ANALYSIS vs RECOMMENDATIONS, deterministic rendering, event attribution, human-in-the-loop for high-stakes decisions)

The architecture separates domain-specific code (Binance API, candlestick patterns, order execution) from the management framework (lifecycle, evaluation, promotion, debriefing). When the pattern is applied to other domains (GTM outreach strategy management, content optimization, any multi-strategy optimization), only the domain layer is swapped. The management framework and governance protocol are the portable IP.

**Novelty note (2026-08-12):** Research confirms the general idea of "LLM writes trading strategy code and iterates" is being explored (EvoQuant, TradingAgents, various blog experiments). What is genuinely novel in this system:
1. The org chart management model (strategies managed as employees, not just code modules)
2. The honest debriefing protocol (FACTS/ANALYSIS/RECOMMENDATIONS separation, deterministic rendering)
3. The complete closed loop with human-in-the-loop governance (human approves promotions, system auto-demotes)

**EvoQuant vs our system:** EvoQuant is an academic strategy optimizer. It takes one existing strategy, improves it through LLM-guided iteration, outputs a result. No lifecycle management. No multi-strategy portfolio. No human governance. No briefings. No org chart. No domain portability. No product vision. We stole EvoQuant's iteration mechanics (diagnosis-first, genome, edit ladder, stress probes, knowledge base) as one component inside our larger management system. EvoQuant is a better strategy optimizer. We are building a strategy management company.

**TradingAgents vs our system:** TradingAgents uses LLM agents in trading firm roles (analyst, researcher, trader, risk manager) to make buy/sell/hold decisions on stocks. It does not write strategy code. It does not iterate. It does not learn from past trades. We stole the bull/bear debate structure for promotion briefings and the structured-data-as-communication-interface principle. Our agents are specialized by management role (recruiter, evaluator, manager, reporter), not by analytical function.

**OctoBot review (2026-08-12):** Reviewed Drakkar-Software/OctoBot (6.4k stars, GPL-3.0, active since 2018). It's a mature traditional trading bot: ccxt-based, 15+ exchanges, web UI, backtesting, Grid/DCA/AI connectors, TradingView integration. We will NOT fork or copy their code (GPL-3.0 license would contaminate our project and block future commercialization). We steal patterns, not code:

| Pattern from OctoBot | What we learn | Our implementation |
|---|---|---|
| Exchange error handling | Years of production edge cases for rate limiting, reconnection, partial fills | T9 execution layer: study their patterns, write our own |
| Backtesting vectorization | Their backtest engine is faster than our per-candle iteration | T7 optimization: vectorize where possible, keep per-trade sim for stress probes |
| Plugin/tentacle architecture | Modular strategy loading, similar to our strategy/builtin pattern | Already have this. Expand with expanded.py (43 strategies) |
| Grid and DCA strategies | Built-in grid and DCA trading modes | Add GridStrategy and DCAStrategy to our expanded library (below) |
| TradingView connector | Accept TradingView webhook alerts as signals | V1.5: TradingView integration as signal source alongside our scanner |

**Strategies from OctoBot to add to our library:**
- Grid trading: place buy/sell orders at fixed intervals above and below current price. Profits from volatility in ranging markets.
- DCA (Dollar Cost Averaging): buy fixed amount at regular intervals, regardless of price. Not a signal-based strategy but a mode. Useful as a benchmark ("is active trading better than DCA?").
- Stochastic RSI: another momentum oscillator, complementary to RSI.
- MACD crossover: moving average convergence divergence. Classic trend-following signal.

**What was stolen and why:**

| Technique | Source | What it does for us |
|---|---|---|
| Strategy genome (typed layers) | EvoQuant | Every edit targets a specific layer (signal, entry, risk, exit). Prevents drift, enables auditing |
| Diagnosis before generation | EvoQuant | Quant identifies the specific bottleneck first, then proposes a targeted fix. No random rewrites |
| Hierarchical edit ladder | EvoQuant | Parameter tuning, local repair, redesign, family migration. Escalate only when lower fixes fail 3x |
| Rejection knowledge base | EvoQuant | Graveyard is queryable. Quant checks past failures before writing new strategies |
| Stress probes | EvoQuant | Backtest includes fee doubling, slippage doubling, execution delay, parameter jitter |
| Bull/bear promotion cases | TradingAgents | Promotion briefing includes both sides. Forces intellectual honesty |
| Structured agent communication | TradingAgents | Agents communicate through data (SQLite, JSON, Notion), not natural language chat |

Success criterion for v1 is process integrity and measurement quality. Returns are the experiment, not the promise.

---

## 15. Build Instructions for Claude Code

1. Read this SPEC.md end to end.
2. Follow AI-DLC rules (installed globally at ~/.claude/).
3. Build in this order:
   - T1: Project scaffold + config.yaml + .env.example + .gitignore
   - T2: SQLite schema (db/schema.sql) — add code_hash column to orders and fills per F8
   - T3: Data layer (engine/collector.py + indicators/)
   - T4: Signal layer (engine/scanner.py + strategies/builtin/)
   - T5: Risk gate (engine/risk.py) — fixed notional cap, fee-to-edge gate, max trades/day per F4/R6
   - T6: Paper adapter (engine/adapters/paper.py) — fill at ask/bid not mid per F8/R8
   - T7: Backtest harness (backtest/) — MOVED UP from T11. Need to know if patterns profit after fees before building the rest.
   - T8: Strategy sandbox (before execution layer) — AST allowlist + subprocess + hash-pin per F1/R1
   - T9: Execution layer (engine/executor.py)
   - T10: Engine main + launchd plist (engine/main.py)
   - T11: Reconciliation (engine/reconciliation.py)
   - T12: botctl CLI (engine/botctl.py)
   - T13: Notion integration + deterministic FACTS renderer (briefing writer) per F6/R5
   - T14: Hermes Quant profile setup — Haiku daily, Opus research, Sonnet default per M1/M3
   - T15: README + architecture docs

**Why T7 (backtest) moved up:** The backtest harness is the moment of truth. If the patterns don't profit after fees on 9 months of real data with proper overfitting defenses (train/val split, holdout, walk-forward, random twin), then the Quant agent is iterating on garbage. Better to know at T7 than T13. The backtest also validates the fixed-notional model and the fee-to-edge gate before they're embedded in live execution.
4. After each task, write a handoff note to docs/handoffs/.
5. No code before spec approval. Spec is approved.

---

## 16. Open Items (for Aym)

1. **Create Binance.US account.** Verify NJ access. Enable API keys when ready for live mode. For paper mode, no key needed.
2. **Create Notion integration.** Generate a Notion API token. Create the three databases (Trading Journal, Daily Summary, Weekly Summary). Share all three with the integration.
3. **Hermes Quant profile.** Raven will set this up after the engine is built and the Notion databases exist.
4. **Backtest data.** Download 12+ months of 15m OHLCV for BTC/USDT, ETH/USDT, SOL/USDT from Binance.US historical data page.
