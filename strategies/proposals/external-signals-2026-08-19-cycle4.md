# External Signals - 2026-08-19 Forge Cycle 4 (~23:55 UTC)

**Generated:** 2026-08-19 23:55 UTC (cron Forge eval loop, 4h tick)
**Source:** X API (xurl search, last 24h) + web search (Firecrawl/WebSearch bridge)
**Status:** RAW, awaiting Opus reasoning distillation
**Note:** Reddit (r/Polymarket, r/algotrading) blocked again this cycle
(crawler-blocked on reddit.com, same as cycles 2-3). X + web only.

---

## Signal 1: The arbitrage window has collapsed to ~2.7 seconds (STRUCTURAL - the taker latency-arb family is now measurably dead at any human timescale)

**Source:** Web search aggregation (coin360.com/news/polymarket-traders-losses-profit-concentration-2026-predictions + Medium/Illumination "Beyond Simple Arbitrage: 4 Polymarket Strategies Bots Actually Profit From in 2026", both retrieved 2026-08-19 23:5x UTC).

**Claim:** Average arbitrage opportunity duration on Polymarket is now ~2.7 seconds (down from ~12.3s in 2024). Median spread ~0.3%. 73% of arbitrage profits are captured by sub-100ms execution bots. The same articles report ~70% of Polymarket traders lose money and <0.04% of accounts capture >70% of total profits.

**Relevance to shadow (HIGH):** This corroborates the fair-value-family verdict from the vault (spread_eats_edge, TESTED_FAILED at 616 trades) with venue-wide numbers. Our taker strategies enter on ~8s holds against a 2.7s window; the round-trip tax the vault measured is the same phenomenon the venue numbers describe. Do NOT re-propose market-order taker latency-arbitrage in any form. The surviving taker families are the ones whose edge does not depend on being faster than the sub-100ms bots: complement/structural (037), resolution mechanics (038), and the mid-price-continuation/time-stop family whose PnL concentrates at target exits rather than speed.

---

## Signal 2: Polymarket is hiring an internal market-making team that may trade against users (STRUCTURAL WARNING for the maker side)

**Source:** Web search (web3.career listing surfaced 2026-08-19; reported by coin360 + others).

**Claim:** Polymarket is recruiting staff for an internal market-making team, including approaching sports bettors, to provide liquidity — i.e. the venue itself is becoming a counterparty on the maker side.

**Relevance to shadow (HIGH):** Directly changes the maker-side calculus for 024 (maker_rebate_corridor_quote_ladder) and 042 (maker_fill_markout_probe). If the venue's own MM desk competes in the same books, maker rebates may shrink, adverse selection against passive rebate farmers (Signal 3, cycle 3) may deepen, and the "maker side is now structurally PAID" read from cycle 3's fee change is less clean than it looked. The maker-fill markout measurement (042) is now the right instrument to re-run once the internal desk's effect is observable. Do not kill 024 on this alone; file as a risk factor for the maker family.

---

## Signal 3: Post-shakeout 15-minute continuation: "odds lag the actual move by ~60-90s" (forecasting-flavored, but the edge source is timing/price-discovery lag, not calibrated prediction)

**Source:** crypticorn.com/polymarket-5-minute-strategy/ (retrieved 2026-08-19, full text).

**Claim:** On 15-minute BTC UP/DOWN markets, after the first 5 minutes: enter only when (a) direction held >= 60s post-shakeout, (b) odds still mispriced relative to actual price movement ("BTC trending up 90s but UP odds still 40c when real prob ~65c"), (c) no major scheduled news within 10 minutes. Exits: sell at 85-90c; exit if odds stall 60-70c for 3+ minutes; hold through slight reversals; accept loss at resolution if reversed to 20c against; sell immediately on news. Reported WR 55-65%, wins 2-3x losses, quarter-Kelly sizing.

**Relevance to shadow (MEDIUM):** This is the "trade against late emotional money" play. It is forecasting in the sense that it bets direction, but the claimed edge source is *price-discovery lag* after a shakeout, which is structure-adjacent (the same information-lag mechanic as Signal 4 and the cycle-3 weather trader). Our 15m exposure is tiny (1.11% of trading.db book is 15m); this would need the 15m universe to be worth testing. Candidate for an experiment proposal with a strict kill, given the standing correction that calibrated forecasting has no proven edge. The reported numbers are self-reported blog data - hypotheses, not evidence.

---

## Signal 4: Weather markets: information-lag exploitation corroborated again (STRUCTURAL - second independent confirmation of the pricing-lag mechanic)

**Source:** X posts 2088964029213503592 ("He's exploiting the lag between new information becoming available and the market fully pricing it" - weather trader analysis, 2026-08-16) and 2089636432696516798 ("$2200 a week arbitraging weather... daily temperature markets NYC, LA, Atlanta, Denver, Chicago, Seoul, London", 2026-08-18), plus cycle-3's "$3,203/day weather trader, 22 predictions daily" claim.

**Claim:** Weather outcome markets (daily temperature) are priced on a lag between public observation (NOAA-style data) and market re-pricing; repeatable accounts exploit the lag. Daily temperature is a *deterministic-ish* outcome (measured, not forecast), so the edge is speed of pricing, not prediction skill.

**Relevance to shadow (MEDIUM-HIGH):** We already have a weather strategy running in env B: `PM_weather_arb` shows +7.35 on n=1 in survivors.db this window - one trade, meaningless alone, but the family now has an external-multiplicity of corroboration for its mechanic (information lag on measured outcomes). Worth a probe/experiment proposal on weather information-lag pricing with a proper kill, OR an amendment to the existing weather-bracket proposal (033) if it exists. Flag: needs the temperature-observation feed (HAVE? MISSING?) verified by the reasoner.

---

## Signal 5: "Variable holding reward on eligible positions" - possible new venue incentive for hold-to-resolution (STRUCTURAL - verify before relying)

**Source:** Web search aggregation (cryptonews.com/polymarket-strategies/ snippet: "maker rebates, liquidity rewards on selected markets, and a variable holding reward on eligible positions").

**Claim:** Polymarket documents a variable holding reward on eligible positions, alongside maker rebates and liquidity rewards.

**Relevance to shadow (MEDIUM, UNVERIFIED):** If real, this is a venue subsidy for holding positions - which would change the economics of the hold-to-resolution family (035 settlement exit, 038 resolution ledger, 039 time-stop hold-through) and possibly explain part of the exit-asymmetry we measured (target exits +334.51 n=53 vs stop/salvage bleeds -655 on n=257 in trading.db this window). The reasoner should treat this as a verification item, not a given: check polymarket.com docs before any proposal leans on it. Do not let an unverified incentive drive a proposal.

---

## Signal 6: Cross-venue arb (sportsbook vs prediction market on the same match) - "same match, same outcome, two different prices" (STRUCTURAL, out of universe)

**Source:** X posts 2090189253372178807 and 2090033283065168283 (2026-08-19, "$700-5,000 daily" arb-claim posts).

**Claim:** Same event priced differently on sportsbooks vs prediction markets; tooling spots the gaps.

**Relevance to shadow (LOW for now):** Out of universe (no sportsbook feed in the shadow stack). File for universe expansion, not a current proposal. Same category as cycle-3's Polymarket x Kalshi cross-venue signal.

---

## Signal 7 (context, not a strategy): Grok-bot "$272K, 100% win rate" and esports-wallet "~90% WR over 3 months" posts

**Source:** X posts 2089831142065389774, 2089827367703429157 (2026-08-18).

**Claim:** Two more high-WR account claims (Grok-driven, esports CS/LoL).

**Relevance:** Same category as cycle-3's fabricated-dashboard caveat - treat account-level claims as unverifiable. The esports one (90% WR trading CS/LoL outcomes) is at least a non-crypto universe worth noting for expansion; the Grok one is noise.

---

## What changed vs cycle 3 (for the reasoner)

1. NEW: arbitrage-window collapse quantified (2.7s, 73% sub-100ms) - hard external corroboration of the fair-value kill.
2. NEW: venue internal MM desk hiring - maker-side risk factor.
3. NEW: holding-reward incentive mentioned (unverified) - potential hold-to-resolution tailwind.
4. NEW: post-shakeout 15m continuation mechanics (blog, self-reported).
5. WEATHER: second corroboration of information-lag mechanic; PM_weather_arb has its first survivors.db trade (+7.35, n=1).
6. UNCHANGED: Reddit blocked; fee regime (cycle 3 Signal 1) still in force; maker-criticism thread (cycle 3 Signal 3) still stands.
