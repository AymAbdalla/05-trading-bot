# Trading Bot v1

Autonomous crypto paper trading engine with multi-strategy backtest harness, strategy graveyard, and quant agent org chart.

**Status:** Paper/backtest only. No live trading until Aym explicitly approves.
**Repo:** Private (github.com/AymAbdalla/05-trading-bot)

---

## What This Is

A multi-strategy trading bot that tests 54 strategies across 180+ tickers, 4 timeframes, and 9 exit configurations, then evaluates which (if any) have real edge after transaction costs.

The core finding from v0: **33 of 35 original strategies have zero gross edge.** The measurement apparatus (backtest harness, validation suite, graveyard analysis) is the asset. Strategies are fungible.

Built by Cody (Claude Code), spec'd by Raven (Hermes) and Aym Abdalla, using the AI-DLC workflow.

---

## Architecture

```
engine/          Core engine: collector, scanner, executor, risk, adapters (paper+live), main
backtest/        Vectorized + event + cross-sectional harnesses, cost model, graveyard builder
strategies/      54 strategies in 6 families (expanded, lab v1-v5)
indicators/      ATR, RSI, EMA, MACD/Stochastic, patterns, volume, support/resistance
sandbox/         AST allowlist validator, subprocess runner, hash pinning
agents/          Quant SOUL.md (active), judge.py (active), Scout/Forge/Coach/Echo (designed, not active)
tests/           559 tests (1 skipped)
docs/            SPEC, DECISIONS, ROADMAP, handoffs, strategy graveyard package, audits
research/        Graveyard outputs, cross-sectional analysis, judge evidence packs
```

### Strategy Families

| Family | Count | Description |
|--------|-------|-------------|
| expanded.py | 28 | Core candlestick + momentum patterns |
| strategy_lab.py | 7 | Lab v1 experiments |
| strategy_lab_v2.py | 9 | RSI divergence + confirmation overlays |
| strategy_lab_v3.py | 5 | Microstructure + volume profiles |
| strategy_lab_v4.py | 3 | Ignition patterns |
| strategy_lab_v5.py | 2 | Forced flow divergence |
| **Total** | **54** | |

### Agent Org Chart

Per SPEC 5.7, the Quant splits into a 5-agent org chart when the strategy library reaches 5+ live strategies. Currently zero live strategies, so only Quant is active.

| Agent | Role | Status |
|-------|------|--------|
| Quant | Skeptical analyst (diagnosis, research, authoring, backtesting) | Active (LLM) |
| Judge | Evidence pack evaluator (pure Python, no LLM) | Active (judge.py) |
| Scout | Market watcher, research briefs | Designed (not loaded) |
| Forge | Strategy author from diagnosis/research | Designed (not loaded) |
| Coach | Promote/demote/retire recommendations | Designed (not loaded) |
| Echo | Notion journal, briefings, Telegram alerts | Designed (not loaded) |

The separation principle: the agent that writes strategies is not the agent that evaluates them, and the agent that evaluates is not the agent that decides.

---

## Backtest Harness

Two independent harnesses that cross-validate:

1. **Vectorized harness** - fast, batch processing across all tickers/timeframes
2. **Event harness** - bar-by-bar simulation, used for verification

### Validation (21 checks, all DURABLE)

`validate_harness.py` runs 21 oracle-through-harness checks including:
- Delayed-oracle lookahead detection
- Fee application verification
- Cross-engine agreement
- Result-quality silent assertions

No result is durable unless `validate_harness.py` exits 0.

### v0 Verdict (2026-08-13)

- **1,390,451 pooled trades** across 218,295 result rows
- **Implied gross edge: +$0.0011/trade** (effectively zero)
- **12 PASS rows** out of 14,688 tests completed
- **3 distinct findings** (strategy x ticker x timeframe) after multiple comparisons correction
- Expected false positive rate at this test count: best result ~4.4 sigma by chance alone

The verdict: the strategies are dead. The harness is the asset.

---

## Key Decisions

Full decision log in `docs/DECISIONS.md` (v8, D-247 through D-260). Highlights:

- **D-247**: Judge is pure Python, no LLM. Composed from validate_harness + assertions + pooled_analysis + asset_class_analysis + summarize_graveyard
- **D-249**: Contract sizing fix (futures sizing was wrong, affecting 12,936 of 287,826 rows)
- **D-253**: Let stale-code graveyard sweep finish rather than killing it (96% of rows unaffected)
- **D-254**: Purge all contract rows (BLOCKED on Aym confirmation - drops 51 PASS rows)
- **D-256**: Constraint sweep "selecting for something real" claim is NOT supported (non-monotonic, underpowered)
- **D-257**: Run python as `env -u PYTHONPATH python3` from agent-spawned sessions (Hermes venv leak fix)
- **D-259**: Purge tool tested (15 tests) before pointing at real data
- **D-260**: Post-sweep repair script exists but is NOT armed (requires Aym's --confirm)

---

## Conventions (Learned the Hard Way)

1. No result is durable unless `validate_harness.py` exits 0
2. Cite `distinct_findings` from summary.json, never raw pass counts
3. Verify a strategy FIRES on real data before interpreting results
4. Conditions must be predicted before testing, never discovered by scanning
5. Estimate gross edge in bps before writing code; under 30bps = dead on arrival
6. Every proposal states a kill condition
7. A FAIL on a 200k-trade strategy is a verdict; a FAIL on 1,700 trades is a shrug
8. Every entry needs stop strictly below entry
9. Write a handoff note after every build session (not optional)
10. Write decisions to DECISIONS.md with D-number, who decided, why, where
11. NOT_TESTED means "could not run," never "ran and found nothing"
12. Edits during a long run do not reach it (Python snapshots source at import)
13. Run python as `env -u PYTHONPATH python3` from agent-spawned sessions

---

## Setup

```bash
# Clone
git clone https://github.com/AymAbdalla/05-trading-bot.git
cd 05-trading-bot

# Virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # or see .env.example for dependencies

# Configure
cp .env.example .env
# Edit .env with your API keys (Binance.US, Alpaca)

# Run tests
python -m pytest -q

# Validate harness
python backtest/validate_harness.py
```

---

## Roadmap

Full roadmap in `docs/ROADMAP.md`. Current priorities:

- **P0.1**: Verify actual Binance.US fee schedule (may be 0% maker / 0.02% taker, not 0.10%)
- **Post-sweep repair**: Purge stale futures rows, rebuild under fixed sizing (BLOCKED on Aym D-254)
- **Read all 5 outputs together**: graveyard, constraint sweep, dispersion, horizon, PLR
- **First supervised paper run + kill-switch drill** (requires Aym)

---

## Documentation

| File | Purpose |
|------|---------|
| `SPEC.md` | Full specification (957 lines) |
| `docs/DECISIONS.md` | Decision log (v8, D-247 through D-260) |
| `docs/ROADMAP.md` | Roadmap with P0-P6 priorities |
| `docs/STRATEGY-GRAVEYARD-PACKAGE.md` | Handoff for strategy hypothesis generation |
| `CLAUDE.md` | Cody wake-up file (session briefing) |
| `HANDOVER.md` | Inter-agent handover protocol |
| `agents/README.md` | Agent org chart and SOUL.md index |
| `docs/handoffs/` | Build session handoff notes |

---

## Owners

- **Aym Abdalla** - decision maker, final say on everything
- **Raven (Hermes)** - spec, review, context, communication
- **Cody (Claude Code)** - all code writing, testing, debugging

---

## Changelog

### 2026-08-14
- Repo created, initial commit, README written
- All T1-T9 code, tests, docs committed as baseline

### 2026-08-13
- T1-T9 complete: 559 tests passing, 1 skipped
- validate_harness 21/21 DURABLE
- v0 verdict: 33/35 strategies zero gross edge
- Graveyard sweep + 5-output chain complete
- Post-sweep repair script built (not armed, BLOCKED on D-254)

### 2026-08-12
- T7-T9 built: backtest harness, sandbox, execution layer
- Claude Code audit completed, fixes applied
- Test suite rewritten

### 2026-08-11
- Project created, SPEC approved
- T1-T6 built: scaffold, schema, data layer, signal layer, risk, paper adapter
