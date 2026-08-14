# Harness Validation Review — v0 Graveyard Run

**Reviewing:** Raven's v0 strategy breakdown, 5-agent architecture, and 14 proposed silent assertions
**Reviewer:** Claude
**Date:** 12 August 2026
**Verdict:** Architecture is sound. Three findings are blocking. Ten assertions missing, one of them critical.

---

## 1. Direct answer to the sequencing question

**Question asked:** implement the silent assertions now, or wait for the current graveyard run to finish?

**Answer: split them. Implement the harness-validity subset before trusting a single result from the current run. Defer the result-quality subset.**

The distinction:

| Class | Examples | When |
|---|---|---|
| **Harness validity** — does the engine work at all? | Positive control, fee-application check, look-ahead shift test, survivorship check, fill-price sanity | **Now. Blocking.** These cannot be applied retroactively because they require re-running with modified inputs |
| **Result quality** — is this particular result trustworthy? | Single-trade dominance, win-rate ceiling, stop/target always triggers, trade-count sanity | **Later. Retroactive.** These read stored result rows and can run any time |

The reason this matters more than it sounds: **a corrupted graveyard is worse than no graveyard.** The entire value proposition is that Forge reads 300,000 results as ground truth for months. If the harness has a fee bug or a look-ahead leak, you are not generating 300,000 useless rows — you are generating 300,000 confidently wrong rows that will steer every downstream agent for the life of the system. The graveyard is described as permanent knowledge. Permanent wrong knowledge is a liability that compounds.

Cost of doing this now: probably a day, and a re-run. Cost of doing it later: discovering in month three that the foundation is wrong and every diagnosis Forge made was built on it.

---

## 2. Finding F1 — there is no positive control (blocking)

This is the most important thing in this review.

The quarantine canary (MULN/SNDL showing profit → harness broken) is a **negative control**. It catches false positives. There is nothing in the design that catches **false negatives**, and the run is currently structured so that a false negative is invisible.

Here is the trap, stated precisely. The framing is "v0 strategies are supposed to be bad — failure is the expected starting condition," supported by academic citations. That framing is correct on the merits. But it creates a dangerous prior: **if everything fails, the design has pre-committed to interpreting that as confirmation rather than as a symptom.**

A harness with a sign error, a fee-units bug (bps vs. percent), a broken fill model, or an off-by-one on signal-to-entry wiring produces exactly the same output as "candlestick patterns don't work": uniformly bad results. You cannot tell those apart from the results alone. And you have written down, in advance, that bad results are expected.

**Required fix — the cheat strategy.** Add a deliberately look-ahead-biased strategy to the grid:

```
Strategy: ORACLE_CONTROL
Entry:  long at bar t close if close[t+1] > close[t]
Exit:   at bar t+1 close
Expected: PF > 5.0, win rate > 90%, on essentially every ticker and timeframe
```

If ORACLE_CONTROL does not produce absurd results, **the harness cannot wire a signal to a fill correctly, and every other result in the run is meaningless.** This is a five-line strategy that validates the entire pipeline.

Add two more:

```
Strategy: BUYHOLD_CONTROL
  Enter bar 1, exit last bar. Net return must equal the buy-and-hold benchmark
  to within fee cost. If it does not, P&L accounting is broken.

Strategy: COIN_FLIP_CONTROL
  Random entry, random exit, matched frequency. Net PF should land slightly
  BELOW 1.0 by exactly the fee drag. If it lands at 1.0, fees are not applied.
  If it lands well above or below, something else is wrong.
```

Those three controls together validate signal wiring, P&L accounting, and cost application. **None of the 14 proposed assertions cover any of that.**

---

## 3. Finding F2 — the inversion premise is arithmetically wrong as stated

From the breakdown:

> "A strategy with PF 0.07 on 55 trades isn't just bad. It's reliably wrong. That's a signal pointing the wrong direction. Invert it and you might have PF 1.5+."

**Inverting a strategy inverts its gross edge. It does not invert its costs.**

```
Original:  net = (−X gross) − C costs
Inverted:  net = (+X gross) − C costs
```

Costs appear with the same sign in both. So inversion only produces a profitable strategy if the gross edge X genuinely exceeds costs C. For a 15-minute crypto strategy with 55 trades, **most of a PF-0.07 result is almost certainly fee and spread drag, not an anti-signal.** Invert a fee-driven loss and you get a differently-shaped fee-driven loss.

There is a second problem layered on top. If you scan 300,000 results for the worst profit factors and invert those, you are **selecting on the extreme tail of a noise distribution.** The worst results in a 300k grid are the unluckiest, not the most reliably wrong. Their inverses will regress hard toward the mean.

**Fix — three conditions, all required, before any inversion is considered:**

1. **Gross PF, computed separately from net PF, must be significantly below 1.0.** This is the whole test. Store `gross_pf` as a first-class column alongside `net_pf`; you need it anyway for the fee-to-edge gate.
2. **Sample adequacy** — 55 trades is not enough to establish reliable wrongness. Use the same bar you use for a positive result.
3. **Out-of-sample confirmation** — the inverted version must be validated on held-out data, and must carry the *original's* hypothesis count in its multiple-comparison correction. Inverting is not a new hypothesis; it is the same hypothesis with a sign flip.

Point 6 in the breakdown says "no other system does this." That is roughly true, and there is a reason: it is mostly a way to mine noise. The idea is salvageable, but only with the gross/net split. **Add a `gross_pf` column before anything else in this thread proceeds.**

---

## 4. Finding F3 — the time-exit finding is confounded

From the breakdown:

> "We discovered that time-based exits beat fixed targets across the board. That finding alone is worth more than the entire v0 strategy library."

Possibly. But there is a mechanical explanation that is not an edge, and it needs ruling out first.

**Fixed target + fixed stop on a drifting asset will disproportionately hit stops.** A time-based exit has no stop to hit, so it captures drift. On a long-biased asset over a period of positive drift, "time exits beat fixed targets" reduces to **"holding beats getting stopped out,"** which is a restatement of buy-and-hold with extra steps — not a discovery about exit design.

**Three checks before this finding goes into the graveyard as a durable conclusion:**

1. **Do time-exit strategies beat buy-and-hold, or only beat fixed-target strategies?** If they beat fixed-target but lose to buy-and-hold, the real finding is "your stops are too tight for these instruments" — useful, much narrower, and it does *not* support "don't optimize entry patterns."
2. **Were targets and stops volatility-normalized?** A fixed 1% target means something completely different on BTC 15m and on KO daily. If exit configs use fixed percentages rather than ATR multiples, the comparison is confounded by instrument volatility and the "finding" is partly a restatement of which tickers are volatile.
3. **Does it hold on the short side and in down periods?** If time exits only win in up-drift periods on long-only strategies, it is a directional-drift artifact. Split results by market regime.

This finding is currently being used to steer Forge's entire generation priority ("don't optimize entry patterns, optimize exit timing"). **That is a large bet on a result that has not been separated from the buy-and-hold baseline.** It might survive all three checks — several of them would make it a stronger finding, not a weaker one. But steer Forge after the checks, not before.

---

## 5. The multiple-comparisons math is worse than stated

Grid size: 37 × 9 × 180 × 5 = **299,700**.

| Quantity | Value |
|---|---|
| Expected "significant" results at naive p < 0.05, under the null that **nothing works** | **~14,985** |
| BH at FDR 10% — smallest p-value threshold | 3.3 × 10⁻⁷ |
| Benjamini-Yekutieli penalty for arbitrary dependence, H(m) | **13.2× more conservative than BH** |
| **Expected maximum z-score under pure null**, √(2·ln n) | **≈ 5.0** |

That last row is the one to internalize. **In a grid this size you should expect to see a roughly 5-sigma result even if not a single strategy has any edge whatsoever.** When the run finishes and something spectacular appears at the top of the leaderboard, that is the base-rate expectation, not evidence.

**Two corrections needed:**

**(a) BH is the wrong variant here.** Benjamini-Hochberg controls FDR under independence or positive regression dependency. These tests are massively dependent — same tickers across strategies, same strategies across correlated tickers, overlapping timeframes on identical underlying data. Under arbitrary dependence you need **Benjamini-Yekutieli**, which is 13.2× stricter at this m. Alternatively, use BH but compute it on the **effective** number of tests, not the raw count.

**(b) The effective test count is far below 300,000.** Rough decomposition: ~28 independent ticker clusters (per the universe protocol) × ~15 genuinely distinct strategy families (the 16 candlestick patterns are not 16 independent ideas) × ~3 timeframe regimes × ~3 exit families ≈ **3,800 effective tests.** Still large, but two orders of magnitude smaller, and that changes the threshold enormously.

**Recommendation:** have Judge compute and store `n_eff` per correction family rather than using the raw grid count. And **report the leaderboard against the null-max expectation** — a column showing "expected best-by-chance" next to "observed best" would prevent the single most likely misreading of this entire run.

---

## 6. The hypothesis-counting hole in the agent architecture

The separation of Forge (writes) / Judge (evaluates, never alters results, no opinions) / Coach (promotes) is genuinely good design. There is one gap that the org chart makes *harder* to see rather than easier.

**Judge applies Benjamini-Hochberg to what Forge submits. Forge decides what to submit.**

If Forge internally drafts and screens 50 variants and submits the best one, Judge corrects for 1 hypothesis when 50 were generated. That is p-hacking, laundered through an organizational boundary, and it will look rigorous in every report.

**Fix — make the search log a submission requirement:**

- Forge must submit `hypotheses_generated` (count), `hypotheses_screened` (count), and the full variant log, not just the winner.
- Judge corrects on **generated**, not **submitted**.
- Add a silent assertion: **if `hypotheses_generated` equals `hypotheses_submitted` on more than 80% of Forge's submissions, Forge is either not iterating or not reporting iteration.** Both are worth knowing and neither is visible from the output.

This is the failure mode that survives review precisely because the org chart looks like it has already solved it.

---

## 7. Review of the 14 proposed assertions

All 14 are worth building. Notes on five of them.

| Assertion | Note |
|---|---|
| **Random twin proximity** (PF within 0.15) | **Upgrade this.** A fixed 0.15 PF delta is arbitrary and scale-dependent. Run **100 random twins**, not one, matched on trade count *and* holding-period distribution *and* long/short mix. Then report the strategy's **percentile within the twin distribution**. "97th percentile of 100 matched random twins" is a real test; "PF beat one random draw by 0.16" is not. This single change makes the random-twin control substantially stronger, and it is the best idea in Raven's list — worth building properly. |
| **Price discontinuity** (>25% jump, no volume spike) | Crypto legitimately does this. Set a per-asset-class threshold. Also, the more common cause is an **unadjusted split**, so check the split calendar before flagging — otherwise this will fire constantly and get ignored. |
| **Quarantine canary** (PF > 2.0 on MULN/SNDL) | Correct, but PF > 2.0 is generous. On names with reverse-split-corrupted price series, PF > 1.3 already suggests something is wrong. Tighten it. |
| **Trade count sanity** (0 trades on 1000+ candles) | Also flag the **inverse across the grid**: if a strategy produces 0 trades on more than 60% of tickers, the signal condition is broken, not selective. |
| **Fee-to-edge violation** | Good — and this one requires `gross_pf` to exist as a column, which ties directly to F2. Build them together. |

---

## 8. Ten assertions to add

Ordered by value. The first four are the harness-validity set from §1 and should block the run.

| # | Assertion | Check | Why |
|---|---|---|---|
| **A1** | **Oracle control** | `ORACLE_CONTROL` (look-ahead) must produce PF > 5.0 and win rate > 90% on ≥95% of tickers | If a strategy that literally sees the future does not win, signal-to-fill wiring is broken. **Nothing else in the run means anything until this passes.** |
| **A2** | **Fee application** | For every result: `net_pf < gross_pf`, strictly. Zero exceptions | Catches an entire class of cost-model bug in one line. Requires storing both |
| **A3** | **Look-ahead shift test** | Re-run a sample with all signals shifted forward one bar. Results must degrade materially toward random | If shifting the signal barely changes results, the strategy is reading information it would not have had. This is the highest-value leak detector available |
| **A4** | **Survivorship check** | % of tickers in the dataset still listed today. If ~100%, flag the entire run | A universe with no delisted names inflates every long-biased result. This is the bias most likely to be silently present and it invalidates cross-sectional conclusions |
| **A5** | **Fill-price realism** | % of fills at the exact high or low of a bar. Should be near zero | Fills at bar extremes mean the fill model is optimistic; results are unachievable |
| **A6** | **Duplicate-strategy detector** | Two "different" strategies with >95% identical trade timestamps on the same ticker/timeframe | The 16 candlestick patterns are not 16 independent ideas. Duplicates inflate apparent breadth of evidence and corrupt the effective-test count |
| **A7** | **Regime concentration** | Single best calendar month contributes >50% of total P&L | Distinguishes a strategy from a regime bet. Complements "single trade dominates" at the period level |
| **A8** | **Timeframe coherence** | Same strategy on 5m and 15m of the same ticker should show correlated results | Uncorrelated results across adjacent timeframes means a resampling bug on one of them |
| **A9** | **Event-data presence** | For equities: if `halt_count` is 0 across all tickers over multi-year history, or `ssr_trigger_count` is 0, the feed is not wired in | Any halt- or SSR-dependent strategy is producing fiction, and it will look like clean data rather than missing data |
| **A10** | **Forge search-log completeness** | `hypotheses_generated` present and > `hypotheses_submitted` on ≥20% of submissions | Per §6 |

---

## 9. Where the silence principle should stop

The silence principle is correct and I would keep it. Not telling agents what the tripwires are prevents Goodharting, and the reasoning given — "if we told Forge don't do this, it would avoid the behavior and the bug would stay hidden" — is exactly right.

But there is a line, and it is worth drawing explicitly:

| Type | Disclose? | Why |
|---|---|---|
| **Tripwires** — quarantine canary, mirror contradiction, random-twin proximity, oracle control, search-log completeness | **Silent** | These detect deception, gaming, or bugs. Disclosure destroys them |
| **Physics** — fees exist, spreads exist, shorts need borrow, halts block fills, options have spreads, leveraged ETFs reset daily | **Disclose** | These are constraints of the world, not tests of the agent |

Hiding the physics does not make Forge more honest. It makes Forge waste compute generating strategies that cannot pass the fee-to-edge gate, because it does not know the gate reflects a real cost rather than an arbitrary rule. **Hide the tripwires, publish the physics.**

Concretely: Forge should be given the knowledge base (`trading-knowledge-base.md`) in full. It should not be given the assertion list.

---

## 10. What is right, and worth saying plainly

Most of this review is critique because that is what is useful. The architecture underneath it is good, and a few decisions are better than good:

- **The random twin is the right control**, and most retail backtest systems have no equivalent. Upgrading it to 100 matched twins with a percentile output would make it genuinely rigorous.
- **The buy-and-hold benchmark as a mandatory column** prevents the most common form of self-deception in this domain.
- **Diagnose-before-generate** is the correct loop, and the graveyard is the right substrate for it.
- **The quarantine canary as a self-diagnostic** is a better use of MULN and SNDL than I originally proposed — I suggested them as a canary; wiring it as an automated assertion is the stronger version.
- **The silence principle** is a real insight and I have not seen it stated elsewhere in this context.
- **"No live trading until Aym says so; go-live criteria are prerequisites, not triggers"** is exactly the right framing and should be preserved verbatim wherever it appears.

The system's stated purpose — testing whether the testing is trustworthy — is the correct ambition. The gap is that the current implementation tests for false positives and not for false negatives, and A1 through A4 close it.

---

## 11. Recommended sequence

1. **Stop treating current-run output as durable.** Mark the in-flight results provisional until A1–A4 pass.
2. **Add `gross_pf` as a stored column.** Unblocks F2 and A2.
3. **Implement A1, A2, A3, A4** — oracle, fee application, shift test, survivorship. Half a day of work, blocking.
4. **Re-run** (the incremental builder should make this cheap for the affected subset).
5. **Upgrade the random twin** to 100 matched twins with percentile output.
6. **Have Judge compute `n_eff`** and switch to Benjamini-Yekutieli, or BH on effective count.
7. **Run the three F3 checks** on the time-exit finding before it steers Forge.
8. **Add A5–A10** and the retroactive result-quality assertions.
9. **Then** let Forge read the graveyard.

Steps 1–4 are the only blocking items. Everything else can run in parallel with continued testing.

---

## 12. Change log

| Version | Date | Note |
|---|---|---|
| 1.0 | 12 Aug 2026 | Review of v0 graveyard run, agent architecture, and 14 proposed assertions. F1–F3 blocking; A1–A10 recommended additions. |
