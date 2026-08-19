# External Signals - 2026-08-19 Forge Cycle

**Generated:** 2026-08-19 06:25 UTC (cron Forge eval loop)
**Source:** Web search (X, Reddit, trading blogs)
**Status:** RAW, awaiting Opus reasoning distillation

---

## Signal 1: Overreaction Fade (90-120 min post-news)

**Source:** polymarkets.co.il/en/guide/trading-strategies/ (verified: "Polymarket Strategies: Arbitrage & Mispriced Markets 2026")

**Claim:** Markets overreact to news in the first 2 hours. 60% of eventual mean-reversion happens within 90-120 minutes. Example: Iran Ceasefire spiked 35% to 68% in 8 minutes; settled at 58% before reverting to 48% over 36 hours.

**Mechanism:** Wait 90-120 min after a news event, then fade the initial spike. Entry at what empirical probability stabilizes to. Requires news monitoring and a probability calibration model.

**Relevance to shadow:** Maps directly to our existing "fade" thesis (D-326). Offers a time-bound refinement the existing proposal lacks. The 90-120 min window is measurable and testable.

---

## Signal 2: Rules Edge / Resolution Mechanics Arbitrage

**Source:** polymarkets.co.il/en/guide/trading-strategies/

**Claim:** Most traders don't read resolution criteria. Market titles mislead. Reading actual rules word-for-word gives a systematic edge (structural, not forecasting). Example: Trump speech market resolved NO because the word appeared in Q&A not the specified segment. Rules readers captured 70+ point moves.

**Mechanism:** Download contract resolution text, parse for discrepancy between title/rules, compare to market price. Fully structural - exploits human laziness about documentation.

**Relevance to shadow:** Matches the "STRUCTURAL" strategy family identified in the CLAUDE.md. Unlike forecasting strategies that depend on predicting the future, resolution mechanics is a deterministic edge that can be automated via text analysis.

---

## Signal 3: Cross-Platform Residuals (Polymarket/Kalshi)

**Source:** tradetheoutcome.com/polymarket-strategy-2026/, newyorkcityservers.com/blog/prediction-market-arbitrage-guide

**Claim:** Combined fee drag of 5%+ (Polymarket 2% on winners, Kalshi 3% taker), plus settlement risk from differing rule wording. Only spreads >6% survive. The median arb window shrank from 12.3s (2024) to 2.7s (2026). 73% of arb profits go to sub-100ms bots.

**Mechanism:** Engineering / speed competition. Not an edge we can capture without infrastructure upgrades.

**Relevance to shadow:** LOW. Speed-arb is a different game. Noted only to confirm our existing stance: structural arb on a single venue (Polymarket) is more viable than cross-venue for a budget setup.

---

## Signal 4: Bregman Projection Arb (Convex Optimization)

**Source:** layerx.xyz/blog/polymarketbots (verified active at time of writing)

**Claim:** Frank-Wolfe optimization to identify when multi-outcome market prices violate probability constraints (not summing to $1.00), then calculate optimal arb allocation via KL-divergence minimization. Market-neutral, risk-free by definition. O(n^2) complexity.

**Mechanism:** Convex optimization solver applied to orderbook data. Uses VWAP analysis for execution. Fully structural - finds mathematical violations in pricing constraints.

**Relevance to shadow:** This is genuinely novel and structural. Our existing "pair completion" (proposal 026) and "complement no-arb" ideas are cousins but not the same. Bregman projection handles multi-outcome markets (not just binary pairs) and is proven in paper trading by the original author.

**Caveat:** The original author notes the arb signals are rare and compress rapidly. Still, a mathematical arb that survives fees is distinct from every strategy we currently run.

---

## Signal 5: Mention-Market "No" Bias (Transcript Frequency Analysis)

**Source:** polymarkets.co.il/en/guide/trading-strategies/

**Claim:** Retail systematically overprices Yes on word-mention markets. Base rates typically 15% while markets price 70%+. Expected edge 30-40% on small capital ($50-$500 per trade).

**Mechanism:** Pull speaker transcripts from prior 10-20 speeches, count target word frequency, compare base rate to market price. Requires transcript DB (YouTube captions, Rev.com).

**Relevance to shadow:** LOW for crypto Up/Down (our current universe). High if we ever expand to political/event markets. Filing for future reference rather than current action.

---

## Signal 6: Liquidity Provision / Maker Rebate Capture

**Source:** polymarkets.co.il/en/guide/trading-strategies/

**Claim:** Polymarket distributes ~$5M/month in maker rebates. Makers near mid-price maximize reward share. $200-$800/day on $10K-$50K capital (15-35% net after adverse selection).

**Mechanism:** Place two-sided limit orders near mid-price, collect rebates, manage adverse selection risk.

**Relevance to shadow:** Our proposal 024 (pm-maker-rebate-corridor-quote-ladder) already covers this. The $5M/month figure confirms the opportunity size is real. The 15-35% net estimate (after adverse selection) is more specific than our current thesis. Not new.

---

## Key Patterns for Opus Reasoning

1. **Structural > Forecasting.** Multiple independent sources converge: forecasting edge is unproven at the sub-1-cent level; structural edges (resolution mechanics, Bregman projection, complement no-arb) are the unproven-but-promising path.

2. **Time-bound fades are testable.** The 90-120 min overreaction fade window is measurable and actionable in our system. We have the news monitoring infrastructure (we already poll Polymarket markets).

3. **Bregman projection is genuinely new.** No existing proposal covers convex optimization arb. Worth a dedicated look.