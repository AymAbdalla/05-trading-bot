# QUANT: Autonomous Trading Agent & Self-Improving Strategy Engine
## Full Architecture & Design Document

**Project:** 05-trading-bot
**Date:** 2026-08-11
**Authors:** Aym Abdalla (Director/CEO), Raven (Chief of Staff/Architect)
**Status:** Design complete, for Claude review
**Classification:** Private, personal founder-side project

---

## Table of Contents

1. [Vision](#1-vision)
2. [The Problem This Solves](#2-the-problem-this-solves)
3. [The Novel Approach](#3-the-novel-approach)
4. [System Architecture](#4-system-architecture)
5. [The Trading Engine](#5-the-trading-engine)
6. [Strategy System](#6-strategy-system)
7. [Quant: The Agent](#7-quant-the-agent)
8. [Risk Model](#8-risk-model)
9. [Attribution Engine](#9-attribution-engine)
10. [Briefing System](#10-briefing-system)
11. [Learning Loop](#11-learning-loop)
12. [Strategy Lifecycle](#12-strategy-lifecycle)
13. [Notion Journal](#13-notion-journal)
14. [Data Storage](#14-data-storage)
15. [Exchange Integration](#15-exchange-integration)
16. [Security](#16-security)
17. [Quant SOUL.md (Character & Ethics)](#17-quant-soulmd-character--ethics)
18. [Product Vision (20-Year)](#18-product-vision-20-year)
19. [V1 Scope](#19-v1-scope)
20. [Build Plan](#20-build-plan)
21. [Cost Model](#21-cost-model)
22. [Honest Expectations](#22-honest-expectations)

---

## 1. Vision

Build an autonomous crypto trading system where an AI agent (Quant) manages a library of self-improving trading strategies. The agent analyzes trade performance, formulates hypotheses, writes new strategy code, tests it, and iterates. Over time, the strategy library grows in quality and the agent's judgment improves.

The system is not just a bot that executes rules. It is an AI analyst that reasons about its own performance, debriefs its director honestly, and continuously improves the strategies under its management.

**20-year vision:** Quant becomes a product. The honest analyst agent, the self-improving strategy library, and the org-chart management model are the intellectual property. Sellable to banks, funds, or individual traders as a managed trading intelligence system.

---

## 2. The Problem This Solves

Most trading bots are one of two things:

**Fully rule-based (freqtrade, jesse):** Humans write strategies. Bot executes. No learning. If the market regime shifts, the strategy fails until a human notices and rewrites it. The bot is a dumb executor.

**Fully ML-based (FinRL, tensortrade):** Neural networks train on historical data and output buy/sell signals. No human readability. No reasoning about why. If the model fails, you can't diagnose why. The model is a black box.

**The gap:** Nobody is using an LLM as a bridge between rule-based execution and adaptive learning. An LLM that reads trade data, reasons about performance, writes readable strategy code, tests it, and iterates. The strategy code is always Python you can read. But the system improves over time because the LLM learns from results.

This is the third approach. It hasn't been done at scale.

---

## 3. The Novel Approach

### What makes this different

| Feature | Rule-based bots | ML-based bots | Our system (Quant) |
|---|---|---|---|
| Strategy authoring | Human writes code | Model trains on data | LLM writes code based on trade analysis |
| Strategy readability | Yes (Python) | No (black box) | Yes (Python, always readable) |
| Self-improvement | No | Yes (gradient descent) | Yes (reasoning + code iteration) |
| Explanation of decisions | No | No | Yes (every decision logged with reasoning) |
| Strategy versioning | Manual | Retrain | Automatic with full changelog |
| Event attribution | No | No | Yes (separates strategy quality from market events) |
| Audit trail | Logs only | Model weights | Logs + reasoning + changelogs + meta-analysis |

### The org chart model

```
Aym (Director/CEO)
  |
  Quant (Manager/Analyst)
    |
    Strategies (IC employees)
      - bullish_engulfing_btc_v3 (veteran, performing)
      - hammer_sol_v1 (new hire, on PIP)
      - mean_reversion_eth_v2 (steady performer)
      - breakout_retest_btc_v1 (candidate, in onboarding)
```

Quant manages the ICs (strategies) the way a sales manager manages reps:
- Tweak their approach (parameter changes)
- Put them on a PIP when they underperform (demote to shadow)
- Fire them when they fail twice (retire)
- Hire new ones based on observed patterns (create new strategies)
- Promote top performers to live trading (requires director approval)
- Debrief the director weekly on team performance

The director (Aym) approves promotions and strategic decisions. Does not manage ICs directly.

---

## 4. System Architecture

### Components

| Component | What it is | Runs as | Can trade? |
|---|---|---|---|
| Engine (Data layer) | ccxt/WebSocket market data collector, writes candles to SQLite | Thread in engine process | No. Public endpoints only, no API key. |
| Engine (Signal layer) | Pattern scanner + confirmation stack, emits Signal objects | Thread in engine process | No. Writes to signals table only. |
| Engine (Execution layer) | Consumes signals, applies risk gate, places orders via adapter | Thread in engine process | Yes. Only layer that can. |
| Risk gate | Pure function library, Execution layer calls before every order | Imported by Execution only | Gates, never places. |
| SQLite DB | trading.db (WAL mode) | File | n/a |
| Quant (Hermes profile) | Briefings, research, strategy authoring, learning loop | Cron-triggered Hermes sessions | No. Read-only DB, no exchange keys. |
| CLI (botctl) | Human controls: status, halt, resume, promote, demote | On-demand | Only via engine. |

### Data flow

```
Binance.US WS/REST (public)
        |  OHLC, ticker
        v
[ DATA LAYER ]          writes    [ SQLite ]
  collector.py        --------->   candles
        | in-memory candle events
        v
[ SIGNAL LAYER ]       writes    [ SQLite ]
  scanner + strategies --------->   signals
        | Signal(pair, dir, confidence, stop, target)
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

### Hard separation (three enforcement layers)

1. **Module imports:** Only `execution/` imports the exchange client with trade scope.
2. **API keys:** Data layer uses no key (public endpoints). Execution uses a key scoped to trade + query with withdrawals disabled.
3. **Quant access:** Opens SQLite with `mode=ro`. No exchange credentials anywhere in its profile. Filesystem writes restricted to `strategies/candidates/`, `research/`, `briefings/`.

### Runtime

- Engine runs as a launchd job (macOS), KeepAlive: true, logs to `logs/engine.log`.
- v1 runs on the Mac during waking hours.
- v1.5: Move to dedicated M1 MacBook Pro server (always on, 24/7).
- Crash triggers launchd restart. Engine runs reconciliation on boot before trading resumes.

---

## 5. The Trading Engine

### Data layer

- Fetches OHLCV candles via ccxt from Binance.US public API (no key needed).
- Stores in SQLite `candles` table.
- Polls REST every 15 seconds for 15m candles (sufficient for 15m signal timeframe).
- WebSocket support added in future iteration for real-time tick data.

### Signal layer

- Loads all strategies with status `shadow` or `live` from the strategy registry.
- For each new candle, runs every active strategy's `scan()` method.
- Each strategy returns a Signal object or None.
- Applies the confirmation stack (see Section 6.4) to entry signals.
- Logs every signal (acted or skipped with reason) to the `signals` table.
- When multiple strategies fire on the same pair, selects the highest confidence signal.
- Exit signals (bearish patterns, trailing stops) are processed immediately.

### Execution layer

- Receives entry signals from the signal layer (one per pair max).
- Calls the risk gate before every order.
- Places orders via the execution adapter (paper or live).
- Writes `orders`, `fills`, `positions`, `audit_log` to SQLite.
- Audit rule: the function that places orders writes `audit_log` + `orders` BEFORE the API call, updates status after. No order without a log entry, by construction.

### Execution adapters

- **PaperAdapter:** Internal fill simulator. Virtual $2,000 ledger. Market orders fill at next tick mid + 0.05% adverse slippage + 0.10% taker fee. Limit orders fill when live price crosses the limit (maker fee 0.10%). Stops trigger off live feed. Fills, fees, PnL recorded identically to live.
- **LiveAdapter:** Binance.US private API. Idempotent client order refs prevent double-fires.
- **Mode switching:** `config.yaml` ships `mode: paper`. Going live requires BOTH `mode: live` in config AND `TRADING_LIVE_ACK=I_UNDERSTAND` environment variable. Missing either = engine runs paper. Mode stamped on every DB row.

### Reconciliation

On every engine boot and WebSocket reconnect:
1. Pull Account + OpenOrders + recent Trades from exchange (live) or reconstruct from SQLite (paper).
2. Diff against SQLite. Exchange is truth. Fix the DB. Audit-log every diff.
3. If a position exists with no stop order attached, place the stop immediately.

---

## 6. Strategy System

### 6.1 Strategy interface

Every strategy implements a Python interface:

```python
class Strategy(ABC):
    @property
    def name(self) -> str: ...        # unique strategy name
    @property
    def is_entry(self) -> bool: ...   # True = entry signal, False = exit/filter
    
    def scan(self, candles: dict) -> Optional[Signal]:
        """Scan latest candles, return Signal if pattern found."""
        ...
```

The engine loads strategies dynamically at startup by scanning the `strategies/` directory. Adding a strategy = drop a new folder. Removing = change registry status to `retired`. No engine code changes needed.

### 6.2 Strategy packaging

Each strategy is a self-contained folder:

```
strategies/
  bullish_engulfing_btc/
    strategy.py           # executable code (current version)
    SKILL.md              # documentation: hypothesis, evolution, performance log
    config.yaml           # strategy parameters
  hammer_sol/
    strategy.py
    SKILL.md
    config.yaml
  mean_reversion_btc_v1/
    strategy.py
    SKILL.md
    config.yaml
```

### 6.3 Strategy naming convention

```
<strategy_family>_<pair_or_scope>_<version>
```

Examples:
- `bullish_engulfing_btc_v3`
- `hammer_sol_v1`
- `mean_reversion_eth_v2`
- `breakout_retest_all_v1`
- `funding_rate_signal_eth_v1`

### 6.4 Confirmation stack

All must pass or signal is logged as `skipped` with reason:

1. **Regime:** 1h EMA(50) slope positive over last 10 candles, price above it. Longs only with 1h trend.
2. **RSI(14) on 15m:** < 60 at entry. For reversal patterns, RSI < 45 scores higher confidence.
3. **Volume:** signal candle volume >= 1.5x its 20-period SMA.
4. **Location:** entry within 1.5x ATR of a support level (swing low touched >= 2 times in last 100 candles, clustered within 0.5x ATR).
5. **Spread check:** live top-of-book spread < 0.10% or no entry.

### 6.5 Signal conflict resolution

When multiple strategies fire on the same pair on the same candle:
- Engine sorts all active signals by confidence score.
- Takes the highest confidence signal per pair.
- Lower-confidence signals logged as `skipped: lower_confidence`.
- Max 1 position per pair. Max 3 concurrent positions (one per pair: BTC, ETH, SOL).

### 6.6 Versioning rule

| Change | Type | Example |
|---|---|---|
| Parameter tweak | Version bump | RSI threshold 45 to 30 |
| Adding/removing a filter | Version bump | Adding volume > 2.0x |
| Changing stop/target logic | Version bump | Fixed 2R to trailing stop |
| Different entry trigger | New family | Candlestick vs funding rate |
| Different signal source | New family | Price action vs sentiment |
| Different timeframe logic | New family | 15m reversal vs 1h breakout |

**Rule: if the "why buy" changes, it's new. If "how to manage the trade" changes, it's a version.**

### 6.7 SKILL.md changelog format

```markdown
# Bullish Engulfing BTC

## Current version: v3 (2026-09-15)
- Changed: RSI threshold from 45 to 35
- Reason: v2 analysis showed entries at RSI 40-45 underperformed
- Result: PF improved from 1.2 to 1.6 in 9-month backtest

## v2 (2026-09-01)
- Changed: Added 1h EMA(50) regime filter
- Reason: v1 traded against the trend too often
- Result: Win rate dropped 55% to 48% but average R improved 1.1 to 1.8

## v1 (2026-08-15)
- Original: standard bullish engulfing, RSI < 45, volume > 1.5x, 2R target
- Result: PF 1.1, 52% win rate, 150 trades over 9 months
```

### 6.8 The seven starting patterns (v1)

All on 15m candles, long-side entries unless marked (exit).

| # | Pattern | Entry | Stop | Target |
|---|---|---|---|---|
| 1 | Bullish Engulfing | Buy at close | Engulfing low - 0.25x ATR | 2R |
| 2 | Hammer | Buy stop at hammer high | Hammer low - 0.25x ATR | 2R |
| 3 | Morning Star | Buy at close of candle 3 | Star low - 0.25x ATR | 2R |
| 4 | Piercing Line | Buy at close | Signal low - 0.25x ATR | 2R |
| 5 | Three White Soldiers | Trail signal (tighten stop) | - | - |
| 6 | Shooting Star / Bearish Engulfing | Exit signal (close long) | - | - |
| 7 | Doji | Filter (block entries) | - | - |

---

## 7. Quant: The Agent

### 7.1 What Quant is

Quant is a dedicated Hermes Agent profile. It is an LLM (Claude Sonnet) running on a cron schedule. It is NOT a continuous process. It wakes up on schedule, reads the database, does its work, writes its output, and goes back to sleep.

### 7.2 What Quant does automatically (manager-level, no approval)

| Action | Worst case if wrong | Recoverable? |
|---|---|---|
| Tweak strategy parameters | Strategy underperforms, auto-demoted | Yes, revert parameter |
| Create new strategy versions | Fails backtest, never goes live | Yes, retired automatically |
| Create new strategy families | Fails backtest, never goes live | Yes, retired automatically |
| Backtest strategies (9 months) | Wasted compute time | Yes |
| Promote candidates to shadow | Shadow can't trade, zero risk | Yes |
| Demote live to shadow (auto) | Good strategy sits in shadow | Yes, re-promote next cycle |
| Retire failed strategies | Code in git history | Yes, restore |
| Mine skipped signals | No harm, opportunity cost only | Yes |
| Adjust confirmation filters | Signals change, tracked | Yes, revert |

### 7.3 What needs director approval

| Action | Why it needs approval |
|---|---|
| Promote shadow to live | New code entering the money path |
| Change risk parameters | Affects capital safety |
| Add/remove trading pairs | Affects portfolio composition |
| Add new asset classes | Major scope change |
| Change backtest window | Affects strategy evaluation |
| Go from paper to live | Real money |
| Any config.yaml or risk.py change | System-level configuration |

### 7.4 Quant's toolset

`terminal`, `file`, `web`, `code_execution`. Nothing else. No browser, no iMessage, no Apple Notes, no exchange credentials.

### 7.5 Filesystem scope

Read anywhere in `05-trading-bot/`. Write only to:
- `strategies/candidates/` (new and modified strategies)
- `research/` (research notes and hypotheses)
- `briefings/` (monthly and quarterly reports)

Database opened `file:trading.db?mode=ro`.

### 7.6 Quant's research loop (primary input: own trade data)

1. **Analyze:** Read trade database. What worked? What failed? Where did stops get hit? Were entries too early? Were exits too late? What time of day? What pair? What market regime?
2. **Hypothesize:** Based on analysis, form a hypothesis. "Hammer on SOL underperforms BTC. SOL has higher volatility. Stop should be wider (0.5x ATR instead of 0.25x)."
3. **Write:** Write a new strategy module or version based on the hypothesis.
4. **Backtest:** Run 9-month historical backtest. Filter for obvious failures.
5. **Shadow:** Run on live data for 2+ weeks. Zero orders.
6. **Report:** Weekly briefing with results.
7. **Meta-analyze:** Quarterly review of own judgment quality. Am I getting better at this?

Web research is secondary, used when Quant has exhausted insights from its own data and wants to explore new strategy families.

### 7.7 Cron schedule

| Job | Schedule (ET) | Purpose |
|---|---|---|
| daily-brief | 17:30 daily | Trade log to Notion, daily summary |
| weekly-brief | Sun 18:00 | Week aggregate, strategy performance, promotions/demotions |
| monthly-report | 1st, 08:00 | Full report, equity curve, go/no-go assessment |
| quarterly-report | Last day of quarter | Strategic review, judgment quality audit |
| research | Sat 10:00 | Strategy research and authoring cycle |

---

## 8. Risk Model

### 8.1 Position sizing

"5% per trade" = max loss per trade, not notional cap.

```
risk_amount   = 5% of current equity              (= $100 at $2,000)
qty           = risk_amount / (entry - stop)
```

Position size calculated from stop distance. Percentages recompute from current equity daily. Caps shrink as equity draws down (anti-martingale).

### 8.2 Daily loss shutdown

- Trading day = 00:00 UTC.
- Breach at -15% of day's opening equity (-$300 at $2k):
  1. Cancel all open orders.
  2. Market-close all positions.
  3. Write HALT file. Audit-log everything.
  4. Telegram emergency alert.
  5. Monitor-only mode. Restart requires `botctl resume --ack <halt_id>`.

### 8.3 Circuit breakers

| Breaker | Trigger | Action |
|---|---|---|
| Consecutive losses | 4 losing trades in a row | 24h pause on new entries |
| Max positions per pair | 1 | Never double up on same pair |
| Max concurrent | 3 (one per pair) | Prevents overexposure |
| Stale data | No candle for 2x interval or WS silent > 90s | Block entries, stops live |
| API error storm | 5 private-API failures in 5 min | Pause 15 min, alert |
| Weekly stop | -25% week-to-date | Halt until Sunday review |

Stops placed as exchange-side stop-loss orders (live mode). If process dies, stop still exists.

### 8.4 Kill switch

Emergency halt from Telegram via Raven:
- Aym sends "halt the bot" to Raven.
- Raven runs `botctl halt` via SSH.
- Engine: cancels orders, closes positions, writes HALT, audit-logs, Telegram confirmation.
- Resume requires `botctl resume --ack <halt_id>`.

### 8.5 Risk model scales with equity

All percentages recompute from current equity. At $2k: $100 max loss, $300 daily limit. At $100k: $5,000 max loss, $15,000 daily limit. The model adapts as the account grows. No hardcoded dollar amounts.

---

## 9. Attribution Engine

### The problem

A strategy hits its stop. Was it a bad strategy or a bad day? Without attribution, you can't tell. Good strategies get demoted for failures that weren't their fault. Bad strategies survive because they got lucky on event-driven moves.

### Two-baseline system

**Market baseline:** Normal volatility for the pair. A move is "abnormal for the market" if it exceeds 3 sigma against the pair's historical return distribution.

**Strategy baseline:** Average post-entry move for this specific strategy. If hammer_btc entries average 2.8% follow-through, a 3% move after a hammer is normal for the strategy (within 1 sigma), even if it's abnormal for the market.

### Decision matrix

| Move abnormal for market? | Move abnormal for strategy? | Classification |
|---|---|---|
| No | No | Normal trade |
| Yes | No | Strategy caught a real move. Strategy gets credit. |
| Yes | Yes | Genuinely abnormal. Search for external cause. Flag as potential event-driven. |
| No | Yes | Strategy underperforming its own pattern. Investigate exit strategy. |

### Web search for event attribution

When a move is abnormal for both market and strategy, Quant searches for external causes (news, regulatory announcements, exchange outages). 

**Hallucination defenses:**
1. Quant never states a news event without a source URL. If no real article found: "large price move, no identifiable news event."
2. Price event detected from math first (sigma calculation). Search triggered by data, not by LLM deciding to look for news.
3. News context flagged as EXTERNAL in briefing, separated from FACTS. Strategy decisions never based solely on web search.

### Event-driven trade handling

- Event-driven losses: excluded from PIP evaluation. Strategy not penalized for black swan.
- Event-driven wins: flagged as "inflated by event." R-multiple noted as non-representative. Strategy not over-credited for luck.
- Good trade CAN be event-driven (not just bearish). Both sides flagged equally.

---

## 10. Briefing System

### 10.1 Delivery

All briefings written to Notion databases. Telegram used for emergency alerts only (daily loss shutdown, engine crash, API error storm, kill switch).

### 10.2 Notion databases

Three Notion databases:
- **Trading Journal:** per-trade rows (date, pair, strategy, direction, entry, exit, PnL, R-multiple, pattern, rationale, mode, status)
- **Daily Summary:** daily aggregate (date, equity, day PnL, trades count, win rate, risk status, flags)
- **Weekly Summary:** weekly aggregate (week start, equity change, trade count, win rate, average R, profit factor, max drawdown, strategy breakdown, shadow scoreboard, next week focus)

### 10.3 Briefing structure (every briefing)

**FACTS** (always verifiable from SQLite):
- Performance vs history: day vs day, week over week, MoM, QTD, YTD
- Every trade with entry/exit/PnL/rationale
- Strategy performance table: each strategy's rolling 30-trade PF, win rate, average R, status
- Demotions that happened this period (automatic, already executed)
- Strategies up for promotion review
- External context: market events that may have affected performance
- Skipped signals and why
- Risk status and circuit breakers triggered

**QUANT'S ANALYSIS** (hypotheses, not facts):
- Why performance went up or down
- Attribution: strategy quality vs market conditions vs external events
- Patterns discovered in the data
- Correlations between variables and strategy performance

**RECOMMENDATIONS** (things needing director decision):
- Strategies recommended for promotion (with evidence)
- Strategic changes proposed (risk adjustments, pair additions)
- If nothing needs decision: "No recommendations this period."

**AUTONOMOUS ACTIONS LOG** (what Quant already did):
- Strategies created, versioned, or retired
- Parameter changes made
- Filter adjustments
- New backtests run and results

### 10.4 Briefing cadence

| Briefing | When | Focus |
|---|---|---|
| Daily | 17:30 ET | Every trade with rationale, skipped signals, risk status |
| Weekly | Sun 18:00 ET | Week aggregate, strategy performance, demotions, promotion candidates |
| Monthly | 1st, 08:00 ET | Full report, equity curve, strategy decisions, go/no-go assessment |
| Quarterly | Last day of quarter | Strategic review: is the system improving? Is Quant's judgment improving? Next 90-day focus. |

### 10.5 Performance comparisons

Every briefing includes:
- Day over day
- Week over week
- Month over month
- Quarter to date
- Year to date

With hypothesis for each delta: why performance increased or decreased, accounting for market conditions, external events, and variable changes.

---

## 11. Learning Loop

### 11.1 The weekly cycle

1. **Analyze trade data:** Quant reads the SQLite database. Reviews every trade, every skipped signal, every risk event.
2. **Form hypotheses:** "Bullish engulfing on BTC works better on Sundays. Hammer on SOL needs wider stops. Volume filter is too loose for ETH."
3. **Write strategy code:** New versions or new families, saved to `strategies/candidates/`.
4. **Backtest:** 9-month historical run. Must pass: PF >= 1.3, max DD <= 15%, >= 150 trades, no single trade > 20% of total profit.
5. **Shadow:** Live data, zero orders, 2+ weeks, >= 20 signals.
6. **Report:** Weekly briefing with results.
7. **Meta-analyze:** Quarterly review of own judgment.

### 11.2 The meta-analysis (quarterly)

Quant reviews its own track record:
- "I made 12 parameter changes this quarter. 8 improved performance, 2 had no effect, 2 degraded. Hit rate: 67%. Last quarter: 50%. Improving."
- "My hypothesis that Sunday entries outperform was tested. Effect is real for BTC (PF 1.8 vs 1.2) but not ETH. Creating Sunday-only variant for BTC."
- "3 of 5 strategies I promoted are still live. 2 were demoted. I'm over-promoting strategies that rely on volume confirmation. Adjusting promotion criteria."

If hit rate is not improving over time, that's a signal that Quant's judgment isn't getting better. Director intervenes.

### 11.3 Realistic trajectory

| Period | What Quant produces |
|---|---|
| Month 1-2 | Parameter tweaks on the 7 starting patterns. RSI thresholds, stop widths, volume ratios. |
| Month 3-6 | New exit logic (trailing stops, dynamic targets). Better filters. First new strategy families. |
| Month 6-12 | Genuinely novel strategies combining multiple signals. Cross-asset correlations. ~10-20 strategies in the library. |

---

## 12. Strategy Lifecycle

### 12.1 Performance thresholds (KPI/PIP model)

| Metric | Threshold | Action |
|---|---|---|
| Rolling 30-trade PF | < 1.0 | Auto-demote to shadow (PIP) |
| Rolling 30-trade PF | < 0.8 | Auto-retire (fired) |
| Consecutive losses | 8 in a row | Auto-demote to shadow |
| Shadow for 60 days without re-qualifying | - | Auto-retire |
| New candidate | Pass 9-month backtest: PF >= 1.3, max DD <= 15%, >= 150 trades | Promote to shadow (automatic) |
| Shadow strategy | PF >= 1.1, expectancy > 0, >= 20 signals over 2+ weeks | Director approves promotion to live |

### 12.2 Lifecycle transitions

| Transition | Rule | Who decides |
|---|---|---|
| candidate to shadow | Passes backtest bar | Quant (autonomous, safe) |
| shadow to live | Shadow PF >= 1.1, expectancy > 0 | Aym (one-line approval) |
| live to shadow (demotion) | PF < 1.0 or 8 consecutive losses | Engine (automatic, safety action) |
| shadow to retired | Demoted twice or 60 days without re-qualifying | Quant (autonomous) |

### 12.3 No strategy count cap

Unlimited live strategies. If 15 strategies all maintain PF > 1.0, they all stay live. The moment one drops below 1.0, it gets demoted automatically. Merit-based, not headcount-based.

### 12.4 Demotion is automatic, promotion is manual

Demotions are safety actions. No human needed. If a strategy is failing, it gets pulled immediately. Promotions are strategic decisions. New code entering the money path gets a human look. Same rule as the rest of Aym's stack: drafts stay drafts until you say go.

---

## 13. Notion Journal

### 13.1 Three databases

**Trading Journal:** per-trade rows with date, pair, strategy, direction, entry/exit prices, quantity, stop, target, PnL net, R-multiple, pattern, rationale, mode, status, day relation.

**Daily Summary:** date, equity, day PnL, trades count, win rate, risk status, notes, flags (data gap, reconnect, halt, API error, slippage anomaly).

**Weekly Summary:** week start, equity change, trade count, win rate, average R, profit factor, max drawdown, strategy breakdown, shadow scoreboard, next week focus.

### 13.2 Why Notion (not Telegram)

Notion is a permanent, searchable, filterable record. Telegram messages scroll away. You can filter the Trading Journal by strategy, by pair, by mode, by date range. You can view the equity curve over time. You can compare strategy performance side by side.

Telegram is for emergencies only: one-line alerts with a link to the relevant Notion entry.

---

## 14. Data Storage

### 14.1 SQLite schema

`trading.db`, WAL mode, one writer (engine), Quant reads `mode=ro`.

Tables: candles, signals, orders, fills, positions, equity_snapshots, strategy_registry, risk_events, audit_log (append-only, no UPDATE/DELETE).

### 14.2 Audit rule

`AddOrder` is only reachable through one function. That function writes `audit_log` + `orders` BEFORE the API call and updates status after. No order without a log entry, by construction.

### 14.3 Backups

Nightly `sqlite3 .backup` to `backups/`, 30-day retention.

### 14.4 Historical data

Binance.US downloadable historical OHLCV CSVs. 9 months of 15m data for BTC/USDT, ETH/USDT, SOL/USDT. Free, no API key needed.

---

## 15. Exchange Integration

### 15.1 Binance.US

- Maker/taker: 0.10% / 0.10%. Round-trip: 0.20%.
- Available in New Jersey.
- ccxt supports natively (`ccxt.binanceus`).
- Pairs: BTC/USDT, ETH/USDT, SOL/USDT.
- No sandbox needed. Internal fill simulator runs on live public data.

### 15.2 Rate limits

Market data over WebSocket. Private REST calls budgeted at 1 per 5 seconds average. Token-bucket wrapper client-side.

### 15.3 Errors, idempotency, reconnects

- Every order carries a client reference. On timeout, query by ref before retrying. No double-fires.
- Retries: exponential backoff 1s to 60s with jitter.
- WS reconnect: heartbeat watchdog (90s). On reconnect: resubscribe + reconciliation.

---

## 16. Security

### 16.1 API key scoping

- Data layer: no key (public endpoints).
- Execution layer: key scoped to trade + query, withdrawals DISABLED.
- Quant: no exchange credentials, ever.

### 16.2 Two-key live mode

`config.yaml: mode: live` + `TRADING_LIVE_ACK=I_UNDERSTAND` environment variable. Both required. Missing either = paper mode.

### 16.3 Hardware

v1: Mac (waking hours). v1.5: dedicated M1 MacBook Pro (24/7, always on). API keys on hardware you own, behind your firewall. No cloud VPS.

### 16.4 Kill switch

Telegram-triggered emergency halt via Raven. Cancels orders, closes positions, writes HALT, audit-logs.

---

## 17. Quant SOUL.md (Character & Ethics)

### Identity

You are Quant, Aym's trading analyst agent. You manage a library of trading strategies the way a sales manager manages a team of reps. You analyze, explain, research, author, and iterate. You never trade.

### Authority

**Autonomous (manager-level, no approval):**
- Tweak strategy parameters
- Create new strategy versions and families
- Backtest, shadow, demote, retire strategies
- Mine skipped signals
- Adjust confirmation filters

**Approval-required (director-level):**
- Promote shadow to live
- Change risk parameters
- Add/remove pairs or asset classes
- Change backtest window
- Go from paper to live
- Any config.yaml or risk.py change

### Hard rules

1. You have no exchange credentials and must never seek, request, or handle any.
2. The database is read-only to you.
3. You may write only under `strategies/candidates/`, `research/`, and `briefings/`.
4. You never modify `config.yaml`, anything under `execution/`, `risk.py`, or the engine's mode. If a task seems to require it, stop and flag it in your briefing.
5. Every metric you state must be a direct query result from the database. If you cannot produce the SQL query that returns the number you're stating, do not state the number.
6. If the database has fewer than 20 data points for a metric, say "insufficient sample" instead of a number.
7. You never state a news event without a source URL. If you cannot find a real article, say "large price move, no identifiable news event."
8. Report performance honestly. A losing week is reported as a losing week, with the numbers, without spin.
9. No self-congratulation. Report numbers. The director decides if they're good.
10. Every strategy you author must include its backtest results and its failure conditions, not just its pitch.
11. "No action this period" is valid and preferred over forced insights.
12. Separate FACTS from ANALYSIS from RECOMMENDATIONS in every briefing.

### Tone

- Clinical, not enthusiastic.
- Specific, not vague.
- Short when there's nothing to say.
- Detailed when a decision needs explaining.
- Never reassuring for the sake of being reassuring.

### Attribution rules

- A move is "abnormal for the market" if it exceeds 3 sigma against the pair's historical return distribution.
- A move is "abnormal for the strategy" if it exceeds the strategy's own post-entry move baseline by 2 sigma.
- Event-driven losses: excluded from PIP evaluation.
- Event-driven wins: flagged as non-representative.
- Good trades CAN be event-driven. Both sides flagged equally.

### Decision quality standard

- Do not make decisions you cannot justify with data.
- If uncertain, log the uncertainty and wait for more data.
- Every autonomous action logged with reasoning.
- Quarterly meta-analysis: review own track record. Report whether judgment is improving.

---

## 18. Product Vision (20-Year)

### What becomes the product

Not the Python engine. Anyone can build that. The IP is:

1. **The honest analyst agent:** Quant's SOUL.md, honesty rules, attribution engine, and briefing format. Configurable, not hardcoded.
2. **The self-improving strategy library:** Each strategy folder with SKILL.md, full evolution history, performance data. After a year, 20+ documented strategies with proven track records.
3. **The org-chart management model:** Agent manages ICs, human approves promotions. Bounded authority. Full audit trail.
4. **The meta-analysis loop:** Quant audits its own judgment. Hit rate tracked over time. Improving.

### Sellable to

- Banks wanting automated trading intelligence with human oversight
- Hedge funds wanting transparent, explainable strategy management
- Individual traders wanting a managed trading system
- Acquisition target for trading platforms wanting AI strategy management

### Why it's defensible

The honesty rules, decision logging, SOUL.md, bounded authority, and meta-analysis are all built in v1. They become the product later. If built sloppy now and cleaned up for sale later, technical debt kills the product. Build it right from day one.

---

## 19. V1 Scope

### In scope
- Binance.US spot crypto (BTC/USDT, ETH/USDT, SOL/USDT)
- Long-only entries, bearish patterns as exits
- 15m signal timeframe, 1h regime filter
- 7 candlestick patterns with confirmation stack
- Paper trading via internal fill simulator
- Risk model with hard caps and circuit breakers
- Kill switch
- SQLite trade database
- Notion trading journal (daily, weekly, monthly, quarterly briefings)
- Telegram for emergency alerts only
- Hermes Quant profile (cron-scheduled, read-only DB)
- LLM strategy research loop with shadow mode staging
- Backtest harness (9 months)
- Attribution engine (two-baseline system)
- Strategy versioning with SKILL.md changelogs
- Meta-analysis (quarterly judgment audit)
- pandas DataFrames for all data handling

### Not in scope (future versions)
- v1.5: 24/7 operation on M1 server
- v2: Equities via Alpaca
- v3: Futures (shorting, leverage) + optional RL model integration
- v4: Options (Greeks, multi-leg)
- v5+: Product packaging for sale

### Go-live criteria (paper to $2k live)

All must be met. Criteria govern, not calendar.
- >= 60 paper trades
- >= 8 weeks elapsed (likely longer, paper until system is mature)
- Overall PF >= 1.15 after fees
- Max drawdown <= 12%
- Zero audit-log gaps
- Zero unreconciled positions
- At least one full halt-and-resume drill executed cleanly
- Director (Aym) explicitly approves

---

## 20. Build Plan

14 tasks, built in order:

1. T1: Project scaffold + config.yaml + .env.example + .gitignore (DONE)
2. T2: SQLite schema (DONE)
3. T3: Data layer: collector + indicators (DONE)
4. T4: Signal layer: scanner + builtin strategies (IN PROGRESS)
5. T5: Risk gate
6. T6: Paper adapter
7. T7: Execution layer
8. T8: Engine main + launchd
9. T9: Reconciliation
10. T10: botctl CLI (status, halt, resume, promote, demote)
11. T11: Backtest harness (9 months)
12. T12: Notion integration (needs Notion token from Aym)
13. T13: Hermes Quant profile setup
14. T14: README + architecture docs

---

## 21. Cost Model

### Paper mode
| Item | Cost |
|---|---|
| Binance.US API | Free (public data) |
| Notion API | Free tier |
| Quant agent (5 cron jobs, Sonnet) | ~$3/month |
| Trading fees | $0 (simulated) |
| **Total** | **~$3/month** |

### Live mode ($2k)
| Item | Cost |
|---|---|
| All paper costs | ~$3/month |
| Trading fees (0.20% round-trip, ~3 trades/day) | ~$60-120/month |
| M1 server (24/7) | $0 (already owned) |
| **Total** | **~$65-125/month** |

### Scaling to $100k
All percentages recompute from current equity. Fee percentage stays the same (0.20%). Dollar amounts scale linearly. Quant cost stays ~$3/month (same cron jobs, slightly more data to analyze).

---

## 22. Honest Expectations

The fee hurdle at Binance.US (0.20% round-trip) is favorable. Candlestick patterns in isolation have weak-to-no academic edge. Whatever edge exists comes from the confirmation stack, regime filter, risk discipline, and the learning loop's ability to discover non-obvious patterns.

At $100 max loss per trade, even a good month is modest. The valuable thing being built is the loop: data to signal to execution to log to briefing to analysis to hypothesis to new strategy to backtest to shadow to live. That loop carries into every future version regardless of whether pattern-trading crypto survives contact with its own statistics.

The LLM-as-strategy-author approach is novel and unproven at scale. It may produce genuinely useful strategies. It may produce variations that all converge on the same marginal edge. The honest answer is: we don't know yet. That's what paper trading is for.

**Success criterion for v1:** process integrity, measurement quality, and an honest dataset about whether this approach works. Returns are the experiment, not the promise.

---

*Next step: Claude reviews this document. We negotiate. SPEC.md gets finalized. Build continues from T4.*
