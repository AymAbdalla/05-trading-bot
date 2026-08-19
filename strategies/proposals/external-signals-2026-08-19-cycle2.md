# External Signals - 2026-08-19 Forge Cycle 2 (~11:05 UTC)

**Generated:** 2026-08-19 11:05 UTC (cron Forge eval loop, 4h tick)
**Source:** X API (xurl search, last 24h) + web search
**Status:** RAW, awaiting Opus reasoning distillation
**Note:** Reddit (r/Polymarket, r/algotrading) was unreachable from this
environment (blocked fetches, JSON API refused). X + web only this cycle.

---

## Signal 1: HFT complete-set arb with directional residual (STRUCTURAL)

**Source:** X post 2089934088157159495 (2026-08-19 04:34 UTC)

**Claim:** "Polymarket HFT bot using hybrid strategy: builds complete sets for
arbitrage (avg cost $0.9387, edge 6.13c/set) plus directional residual. $50 avg
trade, 284 trades/hour, 50% win rate, +$220,792 profit."

**Mechanism:** Assemble the full outcome set of a market/condition (all
outcomes, not just a binary pair) at a combined cost below $1.00, capturing the
structural overround. Residual leg is directional. Live on Polymarket per the
post; figures unverifiable from the post alone.

**Relevance to shadow:** This is the multi-outcome set-building cousin of
proposal 037. 037 rule 1 explicitly refuses to extend to multi-outcome until
the keyed tape exists. This claim is the strongest external evidence that set
arbitrage is real and live on Polymarket. The claimed edge (6.13c/set = ~613
bps on ~94c cost) clears our 200 bps floor by 3x. Caveat: HFT claim, 284
trades/hour is a speed game; but the OPPORTUNITY CHECK (does an assembled set
cost < $1.00 at all, on our slower tape?) is cheap and structural.

---

## Signal 2: Repeated weather-forecast strategy ($1k/day claim) (FORECASTING-adjacent)

**Source:** X post 2090028212927545775 (2026-08-19 10:48 UTC)

**Claim:** "This trader is quietly making around $1,000 a day from weather
markets... total profit already above $44,000. The process is simple: checks
the forecast through the w..." (truncated). Second post (2089817322888423524,
2026-08-18 20:50): "He bought a weather contract for $37. It turned into $15K...
mostly hunts weather markets, looking for contracts that are priced ridiculously
low."

**Mechanism:** Systematic weather-contract valuation off public forecasts;
buy underpriced contracts (deep longshots and mispriced brackets).

**Relevance to shadow:** We already run PM_weather_arb and 033
(pm_weather_bracket_width_matched) in env B. This validates the family exists
with real money behind it. The "priced ridiculously low" phrasing suggests
extreme longshot weather legs, which 033's bracket-width-matching may not
cover. Not new, but sharpens the weather thesis: the edge may live at the
extreme-longshot end.

---

## Signal 3: Top-wallet filtering boosts win rate 53.8% -> 67.2% (STRUCTURAL-adjacent)

**Source:** X post 2090016079418401071 (2026-08-19 10:00 UTC)

**Claim:** "filtering by the top 50,000 wallets on Polymarket can boost your
winning rate from 53.8% to 67.2%. This edge is especially evident in
OtherSports, where 5 calls were recently fired."

**Mechanism:** Track resolved-PnL-ranked wallets; follow/fade their positions
(smart-money copy).

**Relevance to shadow:** We have 022 (shadow_unblock_smart_money_copy) already
filed and unblocked but not yet in env B. This adds a concrete number (13.4 pt
win-rate lift) and a universe note (OtherSports, i.e. sports markets we don't
currently trade). Low novelty, but it re-prioritizes 022 over forecasting
strats.

---

## Signal 4: Cross-venue Polymarket x Kalshi arb spreads 7-14% on slow markets (STRUCTURAL)

**Source:** Predictbook Telegram alerts mirrored on X: posts 2090029694917935255
(07:00-07:05 spread Susie Wiles 7%), 2089988085598671103 (Drake 7%),
2089967025380827451 (Taylor Swift 14%), 2089954612543729946 (BTC 10%),
2089944212787822931 (hurricane name 11%), 2089914795780870628 (Chuck Gray 8%).

**Claim:** Persistent, frequently-alerted spreads of 7-14% between the same
event on Polymarket vs Kalshi, in politics/music/crypto-name/weather-name
markets.

**Mechanism:** Buy the cheap side on one venue, sell/hedge the expensive side
on the other; net when both resolve $1 on the same outcome.

**Relevance to shadow:** Previously rated LOW for a budget setup (speed
competition, sub-100ms bots). BUT these are slow political/novelty markets
with fat, minutes-to-hours-stable spreads, not crypto up/down. The 2026-08-19
signals file already noted only spreads >6% survive fees; these are 7-14%.
Not our current universe (crypto up/down only), so it is a universe-expansion
question, not a current-cycle proposal. Worth one line to Opus: if we ever
open the discovery pass to political markets, cross-venue residual capture is
the most credible structural edge available.

---

## Signal 5: CLOB cancel-only incident Aug 19 11:00-12:00 UTC (OPS EVENT, not a strategy)

**Source:** POLYMARKET DIGEST post 2089957522475999369 (2026-08-19 06:07 UTC)

**Claim:** "Scheduled maintenance (Aug 19, 11:00-12:00 UTC) ran long, causing a
503 'trading disabled' error and brief cancel-only mode on the CLOB."

**Relevance to shadow:** Our shadow loops were live through this window. Any
tape/fills recorded 11:00-12:00 UTC today may reflect a cancel-only, degraded
venue. Structural angle (not actionable now): maintenance windows may widen
spreads systematically, but that is a venue-level pattern needing its own
study, and it is forecasting-adjacent. Primary use: flag for data QA, not for
a proposal.

---

## Signal 6: Polymarket hiring internal market-making team (STRUCTURAL WARNING)

**Source:** Web search result (web3.career listing surfaced 2026-08-19)

**Claim:** "Polymarket is hiring an internal market-making team that may trade
against users, having recently approached traders including sports bettors."

**Relevance to shadow:** If the venue itself makes markets against takers,
maker-rebate capture (024) faces a well-capitalized adverse selector, and
passive quote strategies degrade. Do NOT kill 024 on this alone; note it as a
monitorable regime risk.

---

## Key Patterns for Opus Reasoning

1. **Set-building arb is externally confirmed live.** The strongest fresh
   signal. Our 037 is binary-pair only and structurally dead until the keyed
   tape lands (03:28 2026-08-20 per D-339). The multi-outcome set question can
   be measured for free once 036 lands: does any assembled set cost < $1.00?
2. **Weather family validated by real money.** Edge may skew to extreme
   longshots ("priced ridiculously low"). Consider a weather-longshot probe
   amendment to 033 rather than a new proposal.
3. **Smart-money copy gets a number.** 13.4 pt win-rate lift; 022 should
   graduate into env B before new forecasting strats.
4. **Cross-venue residual is real but out of universe.** File for the day the
   discovery pass opens past crypto up/down.
5. **Ops caveat:** tape from 11:00-12:00 UTC today may be degraded
   (cancel-only mode). Do not build kill conditions on that window.
