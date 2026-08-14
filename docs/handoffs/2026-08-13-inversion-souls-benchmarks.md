# Handoff: Inversion Engine, Agent SOULs, Benchmark Honesty

**Date:** 2026-08-13
**Built by:** Claude Code

## 1. SPEC 5.6 inversion: implemented, tested, refuted

`backtest/inversion.py`, `backtest/run_inversions.py`, 10 tests.

Inversion had been a flag with no implementation since the project started
(1,733 flags, zero runs). It is now a real, F2-gated test - and the first run
settles the question:

**48 eligible failures tested out-of-sample. Zero beat buy-and-hold. Edge is
-$0.31 per exit against a $0.30 round-trip cost.** The supposed anti-signal is
trading friction, to the cent. Writeup: `research/2026-08-13-inversion-finding.md`.

The machinery stays (cheap, gated, settles the question every run). The
expectation is retired. Forge/Quant must read the finding before proposing
inversion-based work.

Two tests prove the fade test can detect real edge when it exists (a signal
firing right before declines shows POSITIVE edge) and correctly rejects noise
(random signals in an uptrend show pure fee drag). Without both directions
the test would be unfalsifiable.

## 2. Benchmark honesty

The fresh graveyard's only PASS was `dca_14` beating buy-and-hold by $0.31 on
28 trades in a rising market. DCA has no signal. Benchmarks now report
`PASS_BENCHMARK`, never `PASS`, carry `is_benchmark` in the graveyard, and are
excluded from inversion.

## 3. Agent SOUL.md files (NEEDS YOUR RULING, RAVEN)

`agents/{scout,forge,judge,coach,echo}/SOUL.md` + `agents/README.md`, matching
Quant's approved 8-section structure and voice, no em-dashes.

**11 encoded rules are NOT literally in SPEC 5.7** - they come from the audit
and the validation review. The load-bearing ones needing a ruling are listed
in DECISIONS.md D-217. The single most important conflict:

> Judge's SOUL uses the random twin as a PERCENTILE against a distribution of
> matched twins. Quant's existing SOUL still cites the single-draw + 0.15 PF
> threshold. These contradict. Rule on which survives, and update the loser.

None of the five are load-bearing in code yet: only Quant's SOUL is active,
and the org split does not trigger until 5+ live strategies (SPEC 5.7).

## 4. State

- Tests: **160 passing**. validate_harness: 21/21. cross-harness: AGREE.
- Graveyard re-run: in progress, ~2,520 entries, currently on AAPL 5m
  (18.5k candles per ticker at that timeframe, so intraday equities are the
  slow stretch). Runs unattended; saves per ticker.
- Inversions can be re-run any time against the completed graveyard:
  `python3 backtest/run_inversions.py`

## 5. Queue for next session

1. Registry loader for shadow-mode strategies (uses `sandbox.verify_hash`).
2. NEW-4 from the backtest re-audit: the delay stress probe morphs order types
   and therefore UNDERSTATES delay damage. Highest-value remaining harness fix.
3. Telegram alerts (SPEC 6.2/6.3 notification paths).
4. SPEC 5.3 acceptance bar as a real gate.
5. launchd plist (T11) once a supervised paper session has run clean.

## 6. Aym's owed items (unchanged)

Rotate the Alpaca key. First supervised paper run + kill-switch drill.
Review the CC DECISION entries in DECISIONS.md, especially D-217's rulings.

---

## 7. Addendum: graveyard summary honesty (added after the run reached 15k)

The re-run was killed externally at 15,120 entries (restarted; it resumes
incrementally). Its output shows 12 PASS rows. That is **2 distinct findings**:

- 11 rows = grid strategies on ADBE 1h across 11 exit configs (one idea, one
  ticker, one timeframe, two parameter settings)
- 1 row  = dca_14 on ETH/USDT (a BENCHMARK; not a discovery, see section 2)

`backtest/summarize_graveyard.py` now produces `research/graveyard/summary.json`
with raw rows vs distinct findings, pass concentration, and the
expected-best-by-chance z-score (~4.4 sigma at this grid size).

**Rule: cite `distinct_findings` from summary.json. Never cite a raw pass
count.** A cluster of passes on one ticker is one observation reported many
times, and at ~15k tests chance alone produces impressive-looking rows.

The ADBE grid cluster is still worth a look when the run completes - but as
ONE hypothesis needing out-of-sample confirmation on other tickers, not as
eleven confirmations of anything.
