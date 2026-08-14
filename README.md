# Trading Bot v1

Multi-asset backtesting engine. 55 strategies across equities, ETFs, crypto, futures, and options, tested for real edge after transaction costs.

---

## The result

I tested 55 strategies across 535,425 backtest runs and 1.9 million trade records, and found no edge that survives transaction costs.

Pooled across tickers, every measurable strategy lands within a few cents of the round-trip cost floor. On a $100 position that floor is $0.30, and the strategies come in between -$0.25 and -$0.35 per trade. That is not a spread of outcomes, it is one number. They are not predicting direction badly. They are not predicting anything, and the loss is the toll.

The measurement apparatus is the asset. Strategies are fungible.

Full writeup: [`research/2026-08-13-v0-verdict.md`](research/2026-08-13-v0-verdict.md).

**Status:** Paper and backtest only. Live trading is not enabled.

---

## How this was built

I design the system, write the spec, and direct AI coding agents to implement it. I set the verification bar, and nothing counts as a result until it clears it: `validate_harness.py` has to exit 0 or every downstream number is marked provisional. Every decision is logged in [`docs/DECISIONS.md`](docs/DECISIONS.md) with a number, an owner, and a reason, including the ones where the project was wrong. SPEC approved 2026-08-11, T1 through T9 complete 2026-08-13. Four days for 559 tests, three harnesses, a 21 check validation suite, and a 535,000 row graveyard.

---

## Architecture

```
engine/          Collector, scanner, executor, risk, adapters (paper+live), main
backtest/        Vectorized + event + cross-sectional harnesses, cost model, graveyard builder
strategies/      55 strategies in 6 families (expanded, lab v1-v5)
indicators/      ATR, RSI, EMA, MACD/Stochastic, patterns, volume, support/resistance
sandbox/         AST allowlist validator, subprocess runner, hash pinning
agents/          Quant SOUL.md (active), judge.py (active), Scout/Forge/Coach/Echo (designed, not active)
tests/           559 tests (1 skipped)
docs/            SPEC, DECISIONS, ROADMAP, handoffs, strategy graveyard package, audits
research/        Graveyard outputs, cross-sectional analysis, judge evidence packs
```

### Strategy families

| Family | Count | Description |
|--------|-------|-------------|
| expanded.py | 28 | Core candlestick + momentum patterns |
| strategy_lab.py | 7 | Lab v1 experiments |
| strategy_lab_v2.py | 9 | RSI divergence + confirmation overlays |
| strategy_lab_v3.py | 6 | Microstructure + volume profiles |
| strategy_lab_v4.py | 3 | Ignition patterns |
| strategy_lab_v5.py | 2 | Forced flow divergence |
| **Total** | **55** | |

### Backtest harness

Two independent harnesses cross-validate, plus a third external engine as referee:

1. **Vectorized harness** for batch processing across all tickers and timeframes
2. **Event harness** for bar-by-bar simulation, used for verification
3. **backtesting.py** (external) as an independent check, wired in as assertion A5

`validate_harness.py` runs 21 oracle-through-harness checks: delayed-oracle lookahead detection, fee application, cross-engine agreement, buy-hold accounting reproduced to the cent, and result-quality silent assertions. Every control runs *through* the harness rather than computing its own answer alongside it. Current status: 21/21, DURABLE.

### Cost model

Four venue regimes sourced from `references/broker-fee-reference-2026.md`: crypto percentage, equity spread, options per contract, futures stacked fixed. Contract instruments are sized against the configured notional cap rather than assumed to be one contract (D-249). Results are stamped with `cost_model_version` and never pooled across versions.

### Agent org chart

Per SPEC 5.7, the Quant splits into five agents once the library reaches 5+ live strategies. There are currently zero live strategies, so only Quant and Judge are active.

| Agent | Role | Status |
|-------|------|--------|
| Quant | Skeptical analyst (diagnosis, research, authoring, backtesting) | Active (LLM) |
| Judge | Evidence pack evaluator (pure Python, no LLM) | Active (judge.py) |
| Scout | Market watcher, research briefs | Designed, not loaded |
| Forge | Strategy author from diagnosis/research | Designed, not loaded |
| Coach | Promote/demote/retire recommendations | Designed, not loaded |
| Echo | Journal, briefings, alerts | Designed, not loaded |

The separation principle: the agent that writes strategies is not the agent that evaluates them, and the agent that evaluates is not the agent that decides.

---

## Current evidence pack

`agents/judge.py` builds the evidence pack from the graveyard. Last run 2026-08-14 after the D-261 repair:

| | |
|---|---|
| Status | DURABLE (harness validated) |
| Graveyard entries | 535,425 |
| Strategies | 55 |
| Tests completed | 509,080 |
| PASS rows | 381 (plus 52 PASS_BENCHMARK) |
| Distinct findings | 155 (strategy x ticker x timeframe) |
| Expected best under the null | ~5.1 sigma |

That last row is the point. With 509,080 tests, chance alone is expected to produce a best result near 5.1 sigma, so a single impressive row is the base rate, not evidence. Raw PASS counts are never cited; `distinct_findings` is.

Four of the pack's eight silent assertions currently flag on this graveyard (quarantine canary, trade count sanity, duplicate strategies, timeframe coherence). They are surfaced in the pack rather than suppressed, and they are open work.

---

## What I learned

**Refusing free code can be the statistically correct move.** I turned down bulk import of external strategy libraries, because more tests inflate multiple-comparisons false positives faster than they add information, and allowed the library documentation in as research input instead (D-203).

**My own idea did not survive its own test.** I asked to slice results by sector and asset class and keep the subsets where the patterns win. That is textbook selection bias, so it was done the only valid way: select winning cells on half the underlyings, judge those exact cells on the other half, 20 random splits. Survival came back at 53.5% to 58.7%, a coin flip, and cells that won on the selection half averaged -$0.302 per trade on unseen instruments against a -$0.30 cost floor. Selecting winners bought exactly zero (D-233).

**Predict before you test.** Conditions are stated as falsifiable predictions before a run, never discovered by scanning results afterward. The constraint sweep pre-registered the shape of the curve as the finding, independent of which level won (D-234). One strategy was killed by its own pre-registered kill condition on the day it was built, before any P&L was read (D-237).

**An unreadable file is not an empty one.** `judge.py` reported `status: DURABLE, entries: 0` against a 287,000 entry graveyard. A read landing mid-write was being caught and laundered into a confident empty result. Caught by running it, not by reading it. The fix raises `GraveyardUnreadable` instead of returning `[]`, and no green harness can upgrade that to DURABLE (D-255).

**Deleting your own positive results.** I authorized a destructive purge of 23,595 graveyard rows, 51 of which were PASS or PASS_BENCHMARK, because they had been computed under a sizing bug. The tool got 15 tests pinning its destructive edges first, then a dry run, then explicit confirmation, then `--apply` (D-259, D-261).

**Skepticism has to run in both directions.** A FAIL on 200,000 trades is a verdict and a FAIL on 1,700 is a shrug, and a PASS on 87 trades is also a shrug. A script in this repo printed its own diagnostic claiming a result was real; the decision log records NOT SUPPORTED against it, with the arithmetic (D-256).

---

## Decisions made on this project

Full log in [`docs/DECISIONS.md`](docs/DECISIONS.md), v9, D-101 through D-265. Rulings marked AYM are mine; CC are the build agent's, made under the conventions below.

| ID | Decision | Who |
|----|----------|-----|
| D-101 | Builder is not verifier. Whoever writes it does not sign off on it | AYM |
| D-202 | Three backtest engines must agree before a result counts | AYM |
| D-203 | No bulk import of strategy libraries, on multiple-comparisons grounds | AYM |
| D-236 | Live fee verification demoted from blocker to shadow-test gate | AYM |
| D-261 | Confirmed the destructive purge of stale contract rows | AYM |
| D-264 | Deferred paper trading to finish the backtesting work first | AYM |
| D-102 | Nothing is durable until `validate_harness.py` exits 0 | CC, approved |
| D-109 | NOT_TESTED is a verdict, distinct from tested-and-failed | CC |
| D-249 | Contract sizing fix. The test had encoded the bug as correct behaviour | CC |
| D-255 | Judge must not report unreadable evidence as no evidence | CC |
| D-256 | The constraint sweep's own "selectivity is real" claim is NOT SUPPORTED | CC |
| D-259 | Destructive tools are tested before they touch real data | CC |

---

## Conventions

1. No result is durable unless `validate_harness.py` exits 0
2. Cite `distinct_findings` from summary.json, never raw pass counts
3. Verify a strategy fires on real data before interpreting its results
4. Conditions must be predicted before testing, never discovered by scanning
5. Estimate gross edge in bps before writing code. Under 30bps is dead on arrival
6. Every proposal states a kill condition before it is built
7. A FAIL on 200k trades is a verdict, a FAIL on 1,700 is a shrug, and a PASS on 87 is also a shrug
8. Every entry needs a stop strictly below entry. The harness rejects inverted stops
9. Write a handoff note after every build session. Not optional
10. Every decision goes to DECISIONS.md with a number, an owner, and a reason
11. NOT_TESTED means "could not run", never "ran and found nothing". This applies to the evidence layer too
12. A cost rate can legitimately be `inf` when an instrument cannot be afforded at the configured capital
13. Edits during a long run do not reach it. Python snapshots source at import

---

## Setup

```bash
git clone https://github.com/AymAbdalla/05-trading-bot.git
cd 05-trading-bot

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Add your Alpaca paper-trading keys. See .env.example.

python -m pytest -q
python backtest/validate_harness.py
```

If you are running from an agent-spawned shell, invoke python as `env -u PYTHONPATH python3`. A leaked parent venv puts the wrong numpy first on the path and the failure looks like a broken install (D-257).

---

## Roadmap

Full roadmap in [`docs/ROADMAP.md`](docs/ROADMAP.md), P0 through P6. Current priorities:

- Read the five graveyard outputs together: graveyard, constraint sweep, dispersion, horizon, PLR
- Clear the four flagged silent assertions in the judge pack
- Point Forge at the surviving v3/v4/v5 proposals once Judge can evaluate what it writes
- First supervised paper run and kill-switch drill (deferred per D-264)
- The 5-agent split waits until something survives the graveyard with real edge

---

## Documentation

| File | Purpose |
|------|---------|
| `SPEC.md` | Full specification (957 lines) |
| `docs/DECISIONS.md` | Decision log (v9, D-101 through D-265) |
| `docs/ROADMAP.md` | Roadmap, P0 through P6 |
| `docs/STRATEGY-GRAVEYARD-PACKAGE.md` | Handoff for strategy hypothesis generation |
| `research/2026-08-13-v0-verdict.md` | The null result, in full |
| `agents/README.md` | Agent org chart and judge.py runbook |
| `docs/handoffs/` | Build session handoff notes |

---

## Changelog

### 2026-08-14
- D-261 repair complete: purged 23,595 stale contract rows, rebuilt under fixed sizing, graveyard back to 535,425 entries
- Judge evidence pack rebuilt: DURABLE, 55 strategies, 155 distinct findings
- Cost model source of truth fixed to `references/broker-fee-reference-2026.md` (D-265)
- Repo created, T1-T9 committed as baseline

### 2026-08-13
- T1-T9 complete: 559 tests passing, 1 skipped, validate_harness 21/21 DURABLE
- v0 verdict: 33 of 35 measured strategies at zero gross edge
- Graveyard sweep and 5-output analysis chain complete
- Conditional edge, inversion, asset-class and constraint sensitivity studies run

### 2026-08-12
- T7-T9 built: backtest harness, sandbox, execution layer
- Audit completed, fixes applied, test suite rewritten

### 2026-08-11
- Project created, SPEC approved
- T1-T6 built: scaffold, schema, data layer, signal layer, risk, paper adapter
