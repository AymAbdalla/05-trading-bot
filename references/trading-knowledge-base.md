# Trading Knowledge Base — Equities, Derivatives, and Market Structure

**Audience:** Raven (Hermes agent) and the Quant strategy layer
**Author:** Compiled for Aym Abdalla
**Version:** 1.1
**Compiled:** 12 August 2026
**Scope:** US equities, ETFs, listed options, OTC/microcap. Crypto is covered only where the mechanics differ meaningfully from the current Quant v1 engine.

---

## 0. How to read this document

### 0.1 Why this exists

Quant v1 trades spot crypto. The version roadmap runs v2 (equities via Alpaca), v3 (futures), v4 (options). This document is the domain knowledge for v2 and v4 — the rules, mechanics, and evidence base that do not exist in a crypto spot engine and that will silently break assumptions carried over from it.

The single most important cross-domain warning: **crypto spot markets have no settlement cycle, no market-wide halts, no short-sale restrictions, no pattern-based margin regime, no expiry, no assignment, and no exercise mechanics. US equities and options have all of them.** A strategy genome ported directly from the crypto engine will produce backtests that are arithmetically correct and operationally impossible.

### 0.2 Confidence tiers

Every claim in this document is tagged. Raven must preserve these tags when quoting from it and must not upgrade a tag.

| Tag | Meaning | How to treat it |
|---|---|---|
| **[RULE]** | Codified regulation, exchange rule, or contract specification. Verifiable against a primary source. | Treat as fact. Re-verify the date before acting — rules change. |
| **[MATH]** | Deterministic arithmetic or definitional identity. | Treat as fact. Does not expire. |
| **[EVIDENCE]** | Peer-reviewed or large-sample empirical finding, with the study named. | Treat as conditional on the sample, market, and period. Cite the limits. |
| **[CONTESTED]** | Findings conflict across credible studies. | Never state as settled. Present both sides. |
| **[PRACTICE]** | How practitioners actually do it. Widely used, not independently validated. | Describe as convention, not as edge. |
| **[UNVERIFIED]** | A specific numeric claim sourced only to a vendor, broker blog, or educator with no published methodology. | **Never cite as a statistic.** Flag as marketing until independently tested. |

### 0.3 The honesty rules that apply to this domain

These extend Quant's existing SOUL.md rules:

1. **No win-rate claim without a sample size, a date range, and a cost assumption.** A 65% win rate on 40 trades in a bull quarter is noise.
2. **Never report a backtest result without the friction model used.** In intraday equities, friction is often larger than the raw edge.
3. **A pattern having a name is not evidence.** Most named candlestick and chart patterns have failed formal testing in at least one major market (§5.3).
4. **Distinguish "documented anomaly" from "tradeable edge."** Many anomalies vanish after transaction costs, and the literature says so explicitly.
5. **Regulatory facts have expiry dates.** The largest rule change affecting retail day trading in 25 years happened in June 2026 (§2.2). Anything trained on pre-2026 text is wrong about it.

### 0.4 Freshness warning

Market-structure facts in this document carry an as-of date. Several are mid-transition as of August 2026:

- The PDT rule was **eliminated** effective 4 June 2026, with broker phase-in running to 20 October 2027. Different brokers are on different regimes right now.
- US equities are moving to near-24-hour trading, with the SIP extension to 23x5 expected 6 December 2026.
- Tick sizes went sub-penny for tick-constrained stocks in November 2025.

Re-verify anything in §1 and §2 before it drives an execution decision.

---

## 1. Market structure — the physical layer

You cannot model slippage, fills, or halt risk without this. Skipping it is how backtests become fiction.

### 1.1 Where trades actually happen

US equity trading is fragmented across three venue types. **[RULE]**

- **National securities exchanges** (~16 of them: NYSE, Nasdaq, Cboe BZX/BYX/EDGA/EDGX, IEX, MEMX, MIAX, LTSE, and others). Displayed, lit order books. Publish quotes to the consolidated tape.
- **Alternative Trading Systems (ATSs) / dark pools.** Registered but non-displayed. Execute without publishing a pre-trade quote. Report trades to a FINRA Trade Reporting Facility.
- **Wholesalers / internalizers.** Broker-dealers (Citadel Securities, Virtu, Jane Street, Susquehanna, others) that execute retail order flow off-exchange against their own capital.

**Why this matters for a bot:** a large fraction of retail marketable order flow never touches a lit exchange. The price you see on a consolidated feed is not necessarily the price your order interacts with. Cboe reported US off-exchange equities ADV of 208 million matched shares in July 2026, up 47.4% year over year, while its on-exchange ADV fell 12.4% — the off-exchange share is growing. **[EVIDENCE — Cboe monthly volume report, July 2026]**

### 1.2 Reg NMS, the NBBO, and order protection

**[RULE]** Regulation NMS establishes the National Best Bid and Offer (NBBO) — the highest displayed bid and lowest displayed offer across all exchanges. The **Order Protection Rule (Rule 611)** prohibits trading through a protected quotation: a venue cannot execute at a worse price than another venue's displayed, immediately accessible quote.

Key consequences:

- Your effective reference price is the NBBO, not any single exchange's book.
- Price improvement (executing inside the NBBO) is legal and common; wholesalers compete on it.
- Odd lots historically did not set the NBBO. That is changing (§1.4).

### 1.3 Tick sizes and access fees — the November 2025 reform

**[RULE — SEC adopting release Sept 18 2024; compliance date 3 November 2025; upheld by the D.C. Circuit 14 October 2025]**

Before the reform: minimum quoting increment was $0.01 for NMS stocks priced at or above $1.00, and $0.0001 below $1.00. Access fee cap was 30 mils ($0.003/share).

After the reform:

| Change | Detail |
|---|---|
| **Second tick size** | $0.005 (half-penny) minimum quoting increment for NMS stocks priced ≥ $1.00 with a time-weighted average quoted spread of **1.5 cents or less** ("tick-constrained" stocks). Everything else stays at $0.01. |
| **Access fee cap** | Cut from 30 mils to **10 mils ($0.001/share)** for protected quotations priced ≥ $1.00. |
| **Fee transparency** | Exchange fees and rebates must be determinable at the time of execution (new Rule 612(d)). |
| **Round lot redefinition** | Tiered by price: 100 / 40 / 10 / 1 share. |
| **What was NOT adopted** | The SEC declined to harmonize *trading* increments with *quoting* increments. Sub-tick price improvement remains legal. |

**Modeling implication for Quant v2:** For a tick-constrained, high-liquidity name (mega-cap tech, SPY, QQQ), the minimum spread is now $0.005, not $0.01. A backtest that assumes a one-cent spread on SPY overstates friction. A backtest that assumes a half-penny spread on an illiquid small cap understates it badly. Spread must be modeled per-symbol from actual quote data, never as a global constant.

The TWAQS evaluation is run by the primary listing exchange over a defined evaluation period, so a stock's tick assignment can change. Do not hardcode it.

### 1.4 Round lots and odd lots

**[RULE]** The round lot definition is now price-tiered (100 / 40 / 10 / 1 share), effective 3 November 2025. Dissemination of odd-lot information, including a new "best odd-lot order" (BOLO) data element, was set for six months after that date to allow system reprogramming.

**Why it matters:** for very high-priced stocks, a 100-share round lot was an enormous notional, which meant most real orders were odd lots and therefore invisible in the protected quote. Shrinking the round lot pulls more of the real book into the NBBO. Historic tick-level data from before this change has different odd-lot visibility than data after it. **This is a structural break in the dataset.** Any backtest spanning November 2025 must treat pre- and post- as different regimes.

### 1.5 Trading sessions — and the move toward 23x5

**Current standard session structure (as of August 2026):** **[RULE]**

| Session | Hours (ET) | Characteristics |
|---|---|---|
| Pre-market | 4:00 a.m. – 9:30 a.m. | Thin. Wide spreads. Gap formation. Most retail brokers restrict order types to limit-only. |
| Regular trading hours (RTH) | 9:30 a.m. – 4:00 p.m. | Full liquidity. Opening and closing auctions. |
| After-hours | 4:00 p.m. – 8:00 p.m. | Thin. Earnings reactions. Wide spreads. |

**The transition underway:** **[RULE — in progress, verify before relying on]**

- **24X National Exchange** received SEC approval and is building toward 23 hours a day, five days a week, with a one-hour maintenance pause.
- **Nasdaq "Global Trading Hours"** — announced launch **6 December 2026**, moving to 23 hours/day, five days a week, with a single one-hour closure between 8:00 p.m. and 9:00 p.m. ET. The 9:30 open and 4:00 close bells remain as auction anchors.
- **NYSE Arca** has announced plans for 22 hours a day (1:30 a.m. – 11:30 p.m. ET weekdays).
- **Cboe EDGX** has announced 24x5 plans.
- **DTCC/NSCC** is extending clearing to 24x5 (Sunday 8 p.m. ET to Friday 8 p.m. ET) to support this. The **SIP extension to 23x5 is expected 6 December 2026.**

**Implications Raven must carry forward:**
1. The concepts "overnight gap" and "session close" are being redefined. Strategies built on gap statistics have a structural expiry date.
2. Overnight-session trades settle as next-day transactions — the calendar date and the trade date diverge.
3. Liquidity in extended sessions is not comparable to RTH liquidity. A signal that fires at 2 a.m. faces a completely different cost structure than the same signal at 10 a.m.

### 1.6 Settlement — T+1

**[RULE]** Standard settlement for US equities, ETFs, corporate bonds, municipal securities, and options is **T+1** (trade date plus one business day), effective 28 May 2024. Previously T+2; before 2017, T+3.

This matters mostly in cash accounts (§2.3). In a margin account, T+1 is largely invisible to the trader because the broker extends credit.

### 1.7 Halts, pauses, and restrictions

This is the category most likely to break a naive backtest, because historical OHLCV data does not tell you the market was closed.

#### 1.7.1 Limit Up-Limit Down (LULD) — single-stock **[RULE]**

A price-band mechanism, SEC-approved in 2012, that prevents trades outside a band computed from the rolling five-minute average price.

- If the price moves to a band and does not return within **15 seconds**, trading pauses for **5 minutes**.
- **Tier 1** securities: S&P 500, Russell 1000, and select ETPs. **Tier 2**: everything else NMS.
- Band widths depend on tier and price: **5%**, **10%**, **20%**, or (for stocks under $0.75) the lesser of $0.15 or 75%.
- Stocks priced $0.75–$3.00 use 20% bands.
- **Bands double** during the first 15 minutes and the last 25 minutes of the session.
- A pause in the last 10 minutes of the session means the security does not trade again that day.
- LULD applies to **both directions** — it stops manic buying as well as panic selling.

#### 1.7.2 Market-wide circuit breakers (MWCB) **[RULE]**

Triggered by S&P 500 declines from the prior close:

| Level | Decline | Effect |
|---|---|---|
| Level 1 | 7% | 15-minute halt if before 3:25 p.m. ET; no halt at or after 3:25 p.m. |
| Level 2 | 13% | Same as Level 1 |
| Level 3 | 20% | Market closes for the remainder of the day, at any time |

Level 1 was triggered four times in March 2020 (the 9th, 12th, 16th, and 18th).

#### 1.7.3 Short Sale Restriction — Reg SHO Rule 201 **[RULE]**

The "alternative uptick rule." Triggered when a covered security drops **10% or more** from the prior day's closing price. Once triggered, short sales are only permitted at a price **above** the current national best bid. The restriction remains in effect for the **remainder of that day and the following trading day**.

Critical distinctions Raven must not confuse:
- SSR **does not prohibit** short selling. It prohibits shorting into the bid.
- A stock can be on SSR without being halted, and halted without being on SSR.
- SSR materially changes short-side fill probability. **A short strategy backtested without SSR logic will show fills it could never have gotten.**

#### 1.7.4 Regulatory and news halts **[RULE]**

- **T1** — news pending / news dissemination halt.
- **T12** — halt pending additional information requested by the exchange.
- **SEC trading suspensions** — up to 10 business days, typically for questions about the accuracy of public information. Common in microcap fraud cases. A suspended stock often reopens on the Expert Market with catastrophic price discovery (§3.4).

**Modeling rule:** any equities backtest must contain explicit halt handling. The naive assumption — that you can always exit at the next bar — is exactly wrong in the scenarios where exiting matters most.

### 1.8 Order types that actually matter

| Type | Behavior | Use / hazard |
|---|---|---|
| Market | Executes immediately at best available price | Never use in thin names, pre/post-market, or immediately after a halt reopen. Slippage is unbounded. |
| Limit | Executes at limit or better | Default for systematic execution. Risk is non-fill, not bad price. |
| Stop (stop-market) | Becomes a market order when the stop price trades | Converts price risk into slippage risk. On a gap, fills far from the stop. |
| Stop-limit | Becomes a limit order when the stop trades | Caps slippage but can leave you in a losing position if price runs past the limit. |
| Marketable limit | Limit priced through the opposite side | The practical compromise: immediate execution with a slippage cap. |
| IOC / FOK | Immediate-or-cancel / fill-or-kill | For liquidity probing. |
| MOO / MOC / LOO / LOC | Auction orders | The closing auction is the single largest liquidity event of the day for most names. |
| Pegged / midpoint | Tracks NBBO or midpoint | Reduces spread cost; adds adverse-selection risk. |

**[PRACTICE]** For an automated system, the defensible defaults are: marketable limit for entries requiring immediacy, resting limit for patient entries, and a hard "cancel and re-evaluate" timer rather than a chase loop.

### 1.9 The full cost stack

**[MATH]** Round-trip cost is not commission. It is:

```
Total cost = spread cost
           + commission
           + exchange/regulatory fees
           + market impact
           + slippage vs. decision price
           + (short side) borrow fee
           + (margin) financing interest
           + opportunity cost of non-fills
```

Regulatory fees on US equity sales (as of 2026, rates change — verify): SEC Section 31 fee and FINRA Trading Activity Fee (TAF) both apply to **sales only**. They are small per share but nonzero, and a high-frequency strategy pays them thousands of times.

**The arithmetic that kills intraday strategies [MATH]:** a strategy with a gross edge of 0.10% per trade, trading 3 times a day, 250 days a year, generates 750 round trips. At a realistic all-in round-trip cost of 0.08% in a liquid name, 80% of the gross edge is consumed. In a small cap with a 0.5% spread, the strategy is deeply negative before it starts. **This is the single best explanation for the day-trading failure rates in §9.**

---

## 2. Account mechanics and the rules that constrain strategy

### 2.1 Cash vs. margin accounts

**[RULE]**

| | Cash account | Margin account |
|---|---|---|
| Leverage | None | Reg T: 50% initial for equities (2:1) |
| Settlement constraint | Must trade with settled funds | Broker extends credit; no settlement wait |
| Shorting | Not permitted | Permitted (with locate) |
| Options | Long options and covered strategies only | Spreads, naked positions (with approval level) |
| Violation regime | Good faith / free riding / cash liquidation | Margin calls, forced liquidation |
| SIPC | Same protection | Same protection, but hypothecation applies |

### 2.2 The Pattern Day Trader rule is gone — read this section carefully

This is the largest change to retail day-trading regulation since 2001, and it happened **after most models' training cutoffs.** Any assistant, tool, or document asserting a $25,000 minimum for day trading is out of date.

**[RULE — SEC Release No. 34-105226, 14 April 2026; FINRA Regulatory Notice 26-10, 20 April 2026; effective 4 June 2026]**

#### What was eliminated

FINRA amended Rule 4210 to **replace the day trading margin requirements in their entirety.** Specifically removed:

- The definition of "day trading" for margin purposes
- The "pattern day trader" designation itself
- The four-day-trades-in-five-business-days trigger count
- The **$25,000 minimum equity requirement**
- The computation and use of "day-trading buying power" based on prior-day end-of-day FINRA excess

FINRA's stated rationale: the 2001-era rules were restrictive and did not reflect modern real-time risk systems; the $25,000 threshold was a blunt instrument that restricted participation without meaningfully reducing intraday risk; and the old framework was never designed for products like 0DTE options that carry large intraday exposure.

#### What replaced it — the intraday margin standard

New Rule 4210(d)(2) requires each member to determine an **"intraday margin deficit"** for each customer margin account (other than good faith or portfolio margin accounts) on each day containing an **"IML-reducing transaction."**

Definitions that matter:

- **Intraday margin level (IML)** — broadly, the amount the customer could withdraw while still meeting maintenance margin.
- **IML-reducing transaction** — broadly, any transaction that reduces that amount. Executing a short sale, or buying a security other than to cover a short, generally qualifies.
- **Intraday margin deficit** — broadly, the highest deficiency, following an IML-reducing transaction, between margin to be maintained and account equity.

Key operational parameters written into the rule:

- **Real-time monitoring is permitted but NOT required.** A member may make a single end-of-day calculation, as it already does for maintenance margin.
- **Sweep programs**: FDIC-insured bank deposits under a member-operated Sweep Program may be treated as a credit balance for IML purposes — regardless of whether the customer day trades.
- **Deposits and withdrawals** during the day may be treated as occurring simultaneously and immediately after the start of the day, as may any transaction closing a position that was open at the start of the day. This lets net deposits and margin released by closing positions offset deficits that arose earlier.
- **Multi-leg strategies**: substantially contemporaneous legs of a spread (or another reduced-margin strategy) may be treated as simultaneous. So may an option assignment/exercise and same-day liquidation of the resulting position.
- **Ordering ambiguity**: if the member cannot demonstrate which of two same-day activities occurred first, IML must be computed on the assumption producing the **highest** deficit.

#### Satisfaction and the 90-day freeze

- An intraday margin deficit must be satisfied **as promptly as possible**.
- It is "satisfied" when net deposits, or increases in the account's IML, equal the deficit. One deposit can satisfy multiple outstanding deficits.
- A deficit remains outstanding until satisfied, or until immediately after the close of business on the **15th business day** after it arose.
- **90-day freeze:** if a customer makes a practice of failing to satisfy deficits promptly and fails to satisfy one by the close of the **5th business day**, the member must enforce policies preventing the customer from creating or increasing a short position or debit balance for **90 calendar days** (other than by closing a short position), or until satisfied.
- **De minimis carve-out:** deficits not exceeding the **lesser of 5% of account equity or $1,000** do not count toward "making a practice," nor do deficits the member reasonably determines arose under extraordinary circumstances.

#### Portfolio margin

New paragraphs (g)(1)(J) and (g)(1)(K) require members to include intraday risk in their written risk analysis methodology, and require each portfolio margin account with **less than $5 million** in equity to maintain intraday margin substantially similar to end-of-day margin. The existing $5 million threshold is preserved.

#### Implementation reality — this is the trap

**Effective date: 4 June 2026. Phase-in for members needing more time: until 20 October 2027.**

**This means brokers are on different regimes simultaneously.** E*TRADE, for example, announced implementation on 9 June 2026. Others will be later. Raven must **never** assume a given broker's current rule set. Before any v2 equities logic depends on margin availability, verify the specific broker's implementation status.

#### Scope limits

The PDT framework and its replacement apply only to **US equities and equity options at FINRA member broker-dealers.** Futures, forex, and cryptocurrency are unaffected — the current Quant v1 crypto engine was never subject to either regime.

#### Observed market effect

Cboe attributed part of the 2026 surge in 0DTE volume to the PDT repeal removing a friction point for smaller retail accounts. 0DTE reached its highest-ever share of total SPX volume in the period following the change. **[EVIDENCE — Cboe Q2 2026 investor presentation]** Treat causal attribution here as the issuer's interpretation, not an established finding.

### 2.3 Cash account violations — still fully in force

The PDT repeal changed nothing here. **[RULE]**

| Violation | Trigger | Penalty |
|---|---|---|
| **Good faith violation (GFV)** | Buy with unsettled funds, then sell the position before the funds used settle | 3 GFVs in a rolling 12 months → 90-day settled-cash-only restriction |
| **Free riding** | Buy securities and pay for them by selling those same securities, without ever depositing the funds | **One** violation in 12 months → 90-day settled-cash-only restriction. Prohibited under Reg T. |
| **Cash liquidation** | Buy securities and cover the cost by selling *other* fully paid securities after the purchase date | Same consequence path as GFV |

Note the asymmetry: buying with unsettled funds and **holding past settlement** is fine. It is the sale before settlement that triggers the GFV.

T+1 helped cash-account traders — the wait is one business day instead of two — but did not remove the constraint.

### 2.4 Margin mechanics

**[RULE]**
- **Reg T initial margin:** 50% for marginable equities. $2,000 minimum to open a margin account.
- **FINRA maintenance margin:** 25% minimum for long equity positions; 30% for most shorts. Brokers routinely impose higher house requirements, especially on volatile, low-priced, or concentrated positions.
- **Margin call:** equity falls below maintenance. Broker may liquidate without notice and may choose which positions to liquidate.
- **Special memorandum account (SMA)** tracks excess equity.

**[PRACTICE]** House requirements on small caps, recent IPOs, and heavily shorted names are frequently 50–100%, meaning no leverage at all — sometimes changed intraday with no warning. A backtest assuming 2:1 on a low-float runner is fiction.

### 2.5 Short selling mechanics

**[RULE]**
- **Locate requirement (Reg SHO Rule 203(b)):** before accepting a short sale order, a broker must have reasonable grounds to believe the security can be borrowed and delivered. "Easy to borrow" lists cover liquid names; everything else requires a hard locate.
- **Borrow fee:** an annualized rate on the borrowed value, accrued daily, quoted as a stock loan rate. Hard-to-borrow names can carry rates of 20%, 100%, or several hundred percent annualized.
- **Buy-in risk:** the lender can recall shares at any time, forcing the short to cover at market — usually at the worst possible moment.
- **Dividends:** the short pays the dividend to the lender.
- **Unlimited theoretical loss.** Not a cliché — a mathematical property. Long risk is bounded at 100%; short risk is not bounded.

**Modeling rule:** a short-side equities backtest that omits borrow cost, locate availability, buy-in risk, and SSR is not a backtest. It is a fantasy. Small-cap short strategies in particular look spectacular on paper and are frequently unexecutable in practice, because the names with the largest apparent edge are exactly the ones that are hard or impossible to borrow.

---

## 3. The instrument universe

### 3.1 Market capitalization tiers

**[MATH]** Market cap = shares outstanding × price. Note: **free float** (shares actually available to trade) is a different number and often far smaller. Float, not market cap, drives intraday volatility in small names.

**[PRACTICE]** Conventional tiers. These are conventions, not rules, and sources draw the lines differently:

| Tier | Typical range | Character |
|---|---|---|
| Mega cap | > $200B | Deepest liquidity, tightest spreads, heaviest options market |
| Large cap | $10B – $200B | Institutional coverage, efficient pricing |
| Mid cap | $2B – $10B | Moderate coverage |
| Small cap | $300M – $2B | Thinner, higher volatility, wider spreads |
| Micro cap | $50M – $300M | Sparse coverage, manipulation-prone |
| Nano cap | < $50M | Mostly OTC, highest fraud exposure |

**Index-anchored reference points (more precise than the conventions above) [RULE — FTSE Russell June 2026 reconstitution, effective after close 26 June 2026]:**

- Russell 2000 (small cap) maximum cutoff: **$5.7 billion**
- Russell Midcap maximum: **$61.7 billion**
- Russell 3000 total market cap grew substantially into the 2026 reconstitution; the "Magnificent Seven" combined market cap rose 49% to **$22.4 trillion** from $15.0 trillion the prior year (FTSE Russell, as of 30 April 2026)

Two facts Raven should hold together: **[EVIDENCE — Royce Investment Partners, as of 30 June 2026]** the Russell 2000 has historically averaged roughly **7.6%** of the Russell 3000's total market cap, but as of 30 June 2026 small caps were roughly **4.5%** — well below the long-run average. Index concentration in mega caps is at an unusual level. This is context, not a signal.

**Why the tiers matter operationally, not descriptively:**
- Spread as a percentage of price scales inversely with size.
- Options liquidity effectively disappears below mid cap.
- Borrow availability disappears below small cap.
- LULD Tier 1 vs. Tier 2 designation tracks size.
- House margin requirements tighten as size falls.

### 3.2 Listed vs. OTC

**[RULE]** Exchange-listed securities (NYSE, Nasdaq, NYSE American, Cboe) must meet initial and continued listing standards: minimum bid price, public float, shareholder count, market value, corporate governance requirements, and audited financials.

Over-the-counter securities trade through interdealer quotation systems, principally **OTC Link ATS** operated by OTC Markets Group. There is no listing standard in the exchange sense — only tier placement based on disclosure.

### 3.3 Penny stocks — the legal definition

**[RULE]** The definition lives in **Exchange Act Rule 3a51-1**, not in common usage. Generally, a penny stock is an equity security that is **not** listed on a national securities exchange, priced under **$5.00** per share, and whose issuer fails specified financial tests (net tangible assets and average revenue thresholds).

**Exchange-listed securities are excluded from the penny stock definition** because listing standards do the work — so a $2 Nasdaq-listed stock is legally not a penny stock, while a $4 OTC stock generally is.

The penny stock rules (**Exchange Act Section 15(h)** and **Rules 15g-1 through 15g-100**, from the Securities Enforcement Remedies and Penny Stock Reform Act of 1990) regulate **broker-dealer conduct**, not investor conduct. Before effecting a penny stock transaction, the broker must:

- Approve the customer for the specific transaction and obtain a written agreement
- Deliver a standardized risk disclosure document (Schedule 15G)
- Disclose the current quotation and the broker's compensation
- Provide monthly account statements showing the market value of each penny stock held

**Practical consequence:** these requirements are why many brokers restrict, surcharge, or simply refuse OTC penny stock orders. Availability is a broker-by-broker question, not a market question.

### 3.4 OTC Markets tiers

**[RULE / PRACTICE — verify current standards at otcmarkets.com; tier rules change]**

| Tier | Description | Disclosure requirement |
|---|---|---|
| **OTCQX** | Top tier, established companies | Current in SEC or equivalent reporting (or OTC Alternative Reporting with PCAOB-audited GAAP/IFRS financials), financial standards, governance standards, minimum bid price, sponsor required. OTCQX Premier has higher standards. |
| **OTCQB** | Venture market, early-stage | Annual audit, current in reporting to SEC or a banking regulator, minimum bid price **$0.01**, minimum public float requirement, annual verification |
| **Pink — Current Information** | Broker-driven open market | Annual and quarterly reports to GAAP or IFRS standards supplied to OTC Markets |
| **Pink — Limited Information** | Minimum needed to satisfy Rule 15c2-11 | Sparse |
| **Pink — No Information** | No current disclosure | Effectively dark |
| **Expert Market** | Restricted quoting | Unsolicited quotes only; generally inaccessible to retail |

**Rule 15c2-11 [RULE]:** amended rule became effective **28 September 2021.** It requires that current issuer information be publicly available for a broker-dealer to publish quotations. The practical effect was to push non-disclosing issuers into the Expert Market, where retail investors typically cannot buy — only sell unsolicited. To go public via the OTC market, a company files a **Form 211** with FINRA through a sponsoring market maker.

**Why this matters for any automated strategy:** the tier is a hard liquidity and fill-risk gate. An Expert Market security can be effectively untradeable in one direction. **Any equities scanner must filter on tier before anything else.**

### 3.5 Microcap manipulation — the actual mechanics

Raven needs this as a **screening filter**, not as a strategy.

**[RULE / EVIDENCE — SEC and FINRA investor alerts]** Microcap and penny stocks are structurally susceptible to manipulation because of thin float, low absolute price, limited public information, and no analyst coverage.

Recognized scheme types:

- **Pump and dump** — promoters accumulate, then boost price with false or misleading statements, then sell into the buying frenzy they created.
- **Scalping** — recommending a stock to drive the price up, then selling into it.
- **Touting** — promoting a stock without disclosing compensation received for the promotion.
- **Short-and-distort** — the inverse: seed negative rumors, buy back at the artificially depressed price.
- **Ramp and dump** — organized version, often via messaging-app groups.

**The 2026 evolution [EVIDENCE — Bloomberg analysis, January 2026]:** a Bloomberg review of public offerings plus subsequent chatroom promotion and price movement found that **roughly a quarter of the more than 250 companies that went public on Nasdaq's smallest listing tier since 2023 were promoted in WhatsApp group chats and then crashed or were suspended by the SEC** over potentially manipulative trading. Five microcaps on the NYSE's small-cap tier showed similar patterns. The schemes typically involved small Asia-based companies taken public by US underwriters specializing in microcap deals.

**The critical update to conventional wisdom:** an **exchange listing is no longer a reliable manipulation filter.** The SEC's own February 2026 alert states explicitly that while microcaps are more susceptible, pump-and-dump schemes are not limited to microcaps, and warns investors not to lower their guard because a company is listed on a major US exchange.

**Red flags to encode as filters [RULE — SEC/FINRA guidance]:**
- Promotion of the stock exceeding promotion of the company's actual products or services
- Unsolicited "inside information" via any channel
- Sudden volume surge with no material public news
- Extreme return predictions
- Being added to an investment group chat after clicking an advertisement
- Recent small-tier IPO combined with concentrated social promotion
- History of reverse splits, frequent name/ticker changes, or shell status
- Toxic financing structures (convertible notes with floating discount-to-market conversion, "death spiral" financing) — visible in filings and structurally guaranteed to dilute

**Hard rule for Quant:** any strategy proposal targeting sub-$5 OTC securities must be treated as **highest-risk** and requires explicit Aym approval regardless of backtest quality. Backtests on this universe are unusually prone to survivorship bias, because the delisted/suspended failures often vanish from historical datasets entirely.

### 3.6 ETFs — structure and the SPY/QQQ specifics

#### 3.6.1 How ETFs actually work

**[RULE]** An ETF trades like a stock but its share count is elastic. **Authorized Participants (APs)** — large broker-dealers — can create new shares by delivering a basket of the underlying securities to the fund, or redeem shares by receiving the basket back. This **creation/redemption mechanism** is what keeps the ETF price tethered to net asset value: when the ETF trades at a premium, APs create and sell; at a discount, they buy and redeem.

Two consequences:
1. **An ETF's liquidity is not its average daily volume.** It is the liquidity of the underlying basket. A low-volume ETF holding mega caps can absorb size that its volume statistics would never suggest.
2. **In-kind creation/redemption is why ETFs rarely distribute capital gains** — tax events defer until the holder sells.

#### 3.6.2 SPY vs. QQQ vs. the alternatives

**[RULE / EVIDENCE — figures as of mid-2026; AUM and index levels change constantly, re-verify before use]**

| | SPY | QQQ | VOO | IVV |
|---|---|---|---|---|
| Index | S&P 500 | Nasdaq-100 | S&P 500 | S&P 500 |
| Expense ratio | **0.0945%** | **0.20%** | **0.03%** | **0.03%** |
| Legal structure | **Unit Investment Trust (1993)** | Unit Investment Trust | Open-end fund | Open-end fund |
| Launched | 1993 (first US ETF) | 1999 | 2010 | 2000 |
| Holdings | ~500 | ~101 | ~500 | ~500 |
| Primary use | Trading, options, hedging | Tech/growth trading and exposure | Buy-and-hold | Buy-and-hold |

**The UIT structure matters and is widely misunderstood [RULE]:** SPY's Unit Investment Trust wrapper means it **cannot reinvest dividends internally** — they sit in cash until the quarterly distribution, creating a small cash drag — and it limits securities lending, which is part of why its expense ratio stayed higher than open-end competitors. QQQ shares the UIT structure.

**Why SPY nonetheless dominates trading:** it is the most actively traded ETF in the world, and the options ecosystem around it is unmatched — depth, strike granularity, expiration coverage. For an active or options-based strategy, that liquidity is worth far more than 6 basis points of expense ratio. For buy-and-hold, it is not.

**Approximate AUM (mid-2026, directional only — sources disagree and these move):** SPY on the order of $640B+; VOO crossed $1 trillion and is variously reported between $1.5T and $1.7T; IVV around $580B. **Treat any specific AUM number in this document as stale.**

Concentration note: as of mid-2026 the top 10 S&P 500 holdings were reported at roughly 39% of the index. QQQ is materially more concentrated — around 101 holdings with a top-10 weight near 50%. **A "diversified index" trade in QQQ is substantially a mega-cap tech factor trade.** Raven should not describe QQQ exposure as broad market exposure.

**The tax fork that most retail traders miss:** SPY and QQQ options are **equity options** (ordinary short-term treatment). SPX, NDX, RUT, XSP, and VIX options are **Section 1256 contracts** with 60/40 treatment. Same economic exposure, materially different after-tax outcome. See §12.

#### 3.6.3 Other ETFs worth knowing

| Ticker | Exposure | Trading relevance |
|---|---|---|
| IWM | Russell 2000 small cap | The small-cap risk proxy; higher beta |
| DIA | Dow 30 | Price-weighted, mostly legacy |
| RSP | S&P 500 equal weight | Useful for breadth diagnosis vs. SPY |
| GLD / SLV | Gold / silver | Macro hedge; now list frequent short-dated options |
| TLT | 20+ year Treasuries | Rate/duration expression |
| Sector SPDRs (XLK, XLF, XLE, etc.) | GICS sectors | Rotation and relative-strength work |
| VXX / UVXY | VIX futures exposure | **Structurally decaying** — these hold futures, not spot VIX, and roll cost is persistent |

### 3.7 Leveraged and inverse ETFs — the decay math

This is a case where the mathematics is unambiguous and the marketing is not. **[MATH]**

Leveraged ETFs target a multiple of **daily** returns, and rebalance exposure at the end of every session to maintain that ratio. The daily reset produces **path dependence.**

**The mechanic:** after a gain, the fund must *buy* more exposure to maintain the ratio on a larger base. After a loss, it must *sell.* Structurally, it buys high and sells low, every single day.

**Worked example [MATH]:** an index starts at 100, falls 10% to 90, then rises 11.11% back to 100. The index is exactly flat. A 2x fund falls 20% to 80, then rises 22.22% to 97.78. **The 2x fund lost 2.22% while the index went nowhere.**

Key properties Raven must state accurately:

1. **This is not a fee, not tracking error, and not a flaw.** A zero-fee, perfectly tracking leveraged ETF would still exhibit it. It is arithmetic.
2. **Decay is not universal — it is regime dependent.** In a sustained, low-volatility uptrend, daily compounding can make a 2x fund *outperform* 2x the cumulative index return. In choppy, mean-reverting markets it underperforms substantially. The driver is the combination of high volatility and lack of trend.
3. **Three distinct costs must not be conflated:** volatility decay (path-dependent compounding), tracking error (imperfect daily hedging), and expense ratio drag (typically 0.75%–1.00% annually, plus embedded swap financing costs).
4. **Issuer guidance ranges** from "designed for one day or less" to "monitor positions regularly." The SEC's investor bulletin on leveraged and inverse ETFs states these products are meant to be held for a single day or less. Every 2x prospectus discloses the multi-day divergence explicitly.

**Rule for Quant:** any strategy holding a leveraged ETF for more than one session must model the daily reset explicitly. Applying a 2x multiplier to a daily return series and compounding is **wrong** — it produces the naive expectation, not the actual product behavior. Simulate the daily rebalance.

### 3.8 Options — contract mechanics

**[RULE]**

- A standard US equity option contract covers **100 shares** of the underlying (adjustable for splits, special dividends, and corporate actions — "adjusted" options are a common trap).
- **Call:** right (not obligation) to buy at the strike by expiration. **Put:** right to sell at the strike.
- **Long** = holder, paid premium, has the right. **Short/writer** = received premium, has the obligation.
- **American style:** exercisable any time before expiration. Most single-stock and ETF options (SPY, QQQ) are American.
- **European style:** exercisable only at expiration. Most cash-settled index options (SPX, NDX, RUT, XSP) are European.
- **Physical settlement** (shares change hands) for equity/ETF options vs. **cash settlement** (net cash) for index options. This is a large practical difference: cash-settled index options carry **no assignment-into-shares risk.**
- Expirations: monthly (third Friday), weeklies, and daily expirations on the most active products.

#### 3.8.1 Moneyness and intrinsic/extrinsic value

**[MATH]**
```
Call intrinsic value = max(0, Spot − Strike)
Put  intrinsic value = max(0, Strike − Spot)
Extrinsic (time) value = Option price − Intrinsic value
```
At expiration, extrinsic value is zero by definition. Every long option position is a race between directional move and time decay.

#### 3.8.2 The Greeks

**[MATH]** Partial derivatives of option value.

| Greek | Measures | Practical meaning |
|---|---|---|
| **Delta (Δ)** | ∂Price/∂Spot | Directional exposure. Roughly share-equivalents per contract ÷ 100. Loosely (and imperfectly) read as a rough probability proxy for finishing ITM. |
| **Gamma (Γ)** | ∂Delta/∂Spot | Rate of delta change. Highest at-the-money and near expiration. **Gamma is what makes 0DTE violent.** |
| **Theta (Θ)** | ∂Price/∂Time | Time decay. Negative for long options, positive for short. Accelerates into expiration for ATM options. |
| **Vega (ν)** | ∂Price/∂IV | Sensitivity to implied volatility. Long options are long vega. Highest for longer-dated and ATM. |
| **Rho (ρ)** | ∂Price/∂Rates | Usually minor for short-dated; matters for LEAPS. |

**The identity that matters most for anyone selling premium [MATH]:**
```
Hedged long option P&L  ≈  ½ × Γ × S² × (σ²realized − σ²implied) × dt
```
Every delta-hedged option position is, to first order, a bet on **realized volatility versus implied volatility.** This converts every option trade into a statement about which of those two numbers is larger. See §7.4.

#### 3.8.3 Exercise, assignment, and pin risk — where retail gets hurt

**[RULE]**

- **Exercise by exception:** the OCC automatically exercises expiring options that are in the money by **$0.01 or more**, unless the clearing member submits contrary instructions. The threshold applies to equity options in customer, firm, and market-maker accounts, and to index options in all account types. It used to be $0.25.
- **This is a procedure between the OCC and its clearing members, not an automatic customer-level rule.** OCC Rule 805 Interpretation .02 states explicitly that the thresholds are administrative and are not intended to dictate which customer positions must be exercised. **Your broker may use a different threshold. Verify it.**
- **Contrary exercise advice (CEA):** a holder can instruct their broker *not* to exercise an ITM option, or *to* exercise an OTM one. Deadlines are typically around 4:30 p.m. CT / 5:30 p.m. ET on expiration day, broker-dependent.
- **Assignment is random.** The OCC allocates to clearing members, who allocate to customers by their own method (random or FIFO). You do not transact with your original counterparty.
- **Early assignment risk** is real on American-style short options, and concentrates around ex-dividend dates for short calls (a deep ITM call with less extrinsic value than the dividend is an early-exercise candidate).

**Pin risk [RULE / EVIDENCE]:** when the underlying settles at or near your short strike at expiration, you do not know how many contracts will be assigned. Post-close news can flip the outcome after the regular session ends, and SPY/QQQ keep trading after 4:00 p.m. while option delta flips from 0 to 100 across the strike.

The pinning effect is documented, not folklore: **Ni, Pearson & Poteshman (Journal of Financial Economics, 2005)** found that closing prices of optionable stocks cluster at strike prices on expiration dates far more often than chance predicts. **[EVIDENCE]**

**Hard rule:** any automated options strategy must close short positions that are within a defined distance of the strike before the final bell on expiration, or accept and explicitly size for the resulting share position. There is no third option.

---

## 4. Trading styles — a comparison that drives everything downstream

The style determines the cost structure, the data requirements, the risk model, and the failure mode. Choosing it is an architecture decision, not a preference.

| | Scalping | Day trading | Swing trading | Position trading |
|---|---|---|---|---|
| Hold time | Seconds – minutes | Minutes – hours, flat at close | Days – weeks | Weeks – months |
| Trades/year | Thousands | Hundreds – low thousands | Dozens – low hundreds | A few – dozens |
| Primary edge source | Microstructure, order flow, spread capture | Intraday momentum/reversion, catalysts | Multi-day momentum, mean reversion, factor exposure | Trend, fundamentals, macro |
| Cost sensitivity | **Extreme** — cost usually exceeds gross edge for retail | **Very high** | Moderate | Low |
| Overnight gap risk | None | None | **Full** | **Full** |
| Data requirement | Tick / L2 / full depth | 1-min or finer, real-time | Daily / hourly | Daily / weekly |
| Infrastructure | Colocation-class; retail is structurally disadvantaged | Reliable low-latency execution | Standard API | Standard API |
| Capital efficiency | High turnover, low per-trade size | Moderate | Lower turnover | Lowest turnover |
| Dominant failure mode | Adverse selection, latency arbitrage against you | Cost drag + overtrading | Gap risk, regime change | Drawdown tolerance, opportunity cost |
| Regulatory friction | Was PDT (now intraday margin), SSR, halts | Same | Settlement in cash accounts | Minimal |

**The honest ranking for a retail-scale automated system:** cost sensitivity and infrastructure requirements make scalping structurally unattractive and day trading marginal. Swing trading offers the best ratio of realistic edge to friction at retail scale. This is not a preference — it falls directly out of the arithmetic in §1.9 and the evidence in §9.

**Relevant to the Quant roadmap:** v1 runs 15-minute signals with a 1-hour regime filter — which sits between day and swing trading. Ported to equities, that cadence lands squarely in the highest-friction zone unless per-trade edge is large. The fee-to-edge gate already in the v1 risk model becomes **more** important in equities, not less.

---
## 5. Technical analysis

### 5.1 Epistemic status — read before using anything in this section

Technical analysis is a large body of practice with a **mixed and market-dependent** empirical record. The honest summary:

- Some documented intraday and cross-sectional regularities survive formal testing in some markets and periods.
- Most named patterns, when tested rigorously with bootstrap methods and transaction costs, **do not** produce statistically significant excess returns in developed, liquid markets.
- Results that succeed frequently do so in less efficient markets (Taiwan, China, Malaysia in the studies cited below) and frequently fail in the US and Japan.
- Data-snooping bias is severe. Testing 26 patterns × 4 holding periods × 3 trend definitions produces ~300 hypotheses; several will look significant by chance alone.

**Raven's operating stance:** treat every TA construct as a **hypothesis generator**, never as an established edge. The pattern is the hypothesis; the backtest with proper cost modeling and multiple-comparison correction is the test. Quant's existing lifecycle thresholds (PF ≥ 1.3, DD ≤ 15%, ≥150 trades over 9 months for the backtest bar) are precisely the right instrument for this. Do not lower them for a pattern because it is famous.

### 5.2 Price action and market structure

The vocabulary, stated as vocabulary rather than as edge. **[PRACTICE]**

- **Trend structure:** an uptrend is a sequence of higher highs and higher lows; a downtrend, lower highs and lower lows. A break in the sequence is a "structure break" / "change of character."
- **Support and resistance:** price levels with a history of absorbing supply or demand. Mechanistically justified by resting limit orders and stop clusters, not by magic.
- **Range vs. trend regime:** the single most important classification, because mean-reversion logic and momentum logic have opposite signs. Quant v1's 1h EMA regime filter is doing this job; the equivalent must exist in v2.
- **Liquidity sweeps / stop runs:** price briefly exceeds an obvious level, triggers resting stops, and reverses. Real phenomenon (stops are visible in aggregate to anyone who can infer them), but easy to over-fit to in hindsight.
- **Gaps:** overnight price discontinuity. Sub-types: common, breakaway, runaway/measuring, exhaustion. "Gap fill" is a strong intuition with weak universal evidence — fill rates vary enormously by gap size, cause, and market cap.

### 5.3 Candlestick patterns — anatomy and honest evidence

#### 5.3.1 Anatomy

**[MATH]** A candle encodes four values: open, high, low, close. Body = |close − open|. Upper wick = high − max(open, close). Lower wick = min(open, close) − low.

The interpretive claim is that the relationship between body and wicks reveals intra-period supply/demand imbalance. That interpretive claim is plausible and not, by itself, evidence.

#### 5.3.2 Single-candle patterns

| Pattern | Structure | Conventional reading |
|---|---|---|
| **Doji** | Open ≈ close, both wicks present | Indecision |
| **Dragonfly doji** | Open ≈ close ≈ high, long lower wick | Rejection of lower prices |
| **Gravestone doji** | Open ≈ close ≈ low, long upper wick | Rejection of higher prices |
| **Hammer** | Small body at top, long lower wick (≥2× body), after a decline | Bullish reversal |
| **Hanging man** | Same shape as hammer, after an advance | Bearish reversal |
| **Inverted hammer** | Small body at bottom, long upper wick, after a decline | Bullish reversal |
| **Shooting star** | Same shape as inverted hammer, after an advance | Bearish reversal |
| **Marubozu** | Full body, minimal wicks | Conviction in the body's direction |
| **Spinning top** | Small body, wicks both sides | Indecision |

Note that hammer/hanging man and inverted hammer/shooting star are **the same shapes** with different names depending on prior trend. This means the pattern is not doing the work — **the trend definition is.** Studies get different results largely because they define prior trend differently (3-day MA, 10-day EMA, etc.).

#### 5.3.3 Two- and three-candle patterns

| Pattern | Structure | Conventional reading |
|---|---|---|
| **Bullish engulfing** | Down candle, then up candle whose body engulfs it | Bullish reversal |
| **Bearish engulfing** | Up candle, then down candle whose body engulfs it | Bearish reversal |
| **Piercing pattern** | Down candle, then up candle opening below its low and closing above its midpoint | Bullish reversal |
| **Dark cloud cover** | Up candle, then down candle opening above its high and closing below its midpoint | Bearish reversal |
| **Bullish/bearish harami** | Large candle, then small candle contained within its body | Loss of momentum |
| **Tweezer top / bottom** | Two candles with matching highs or lows | Level rejection |
| **Morning star** | Down candle, small-bodied candle, strong up candle | Bullish reversal |
| **Evening star** | Up candle, small-bodied candle, strong down candle | Bearish reversal |
| **Three white soldiers** | Three consecutive strong up candles | Bullish continuation |
| **Three black crows** | Three consecutive strong down candles | Bearish continuation |
| **Three inside up/down** | Harami followed by confirmation | Reversal confirmation |
| **Rising/falling three methods** | Strong candle, three small counter-trend candles, resumption | Continuation |

#### 5.3.4 What the research actually found

This is the section Raven should cite whenever anyone claims candlesticks "work." **[CONTESTED]**

**Negative findings (developed markets):**

- **Marshall, Young & Rose (2006),** *Journal of Banking & Finance* 30(8):2303–2323. Tested candlestick strategies on the 35 individual DJIA component stocks, 1 January 1992 – 31 December 2002, using an extension of the bootstrap methodology that generates random open, high, low, **and** close prices (a genuine methodological advance — earlier work bootstrapped close prices only). **Finding: candlestick trading strategies do not have value for DJIA stocks.** They tested 14 patterns and found no predictive power or financial value. The authors interpret this as evidence of informational efficiency. They also note that, other than Three Inside Down, mean profits following bearish single lines and reversal patterns were all negative — prices fell after bearish candles more than half the time, but when they rose, they rose by more.
- **Marshall, Young & Cahan (2008)** applied the same approach to the 100 largest Tokyo Stock Exchange stocks, 1975–2004. **Finding: no positive abnormal returns.**
- **Tharavanij, Siraprapasiri & Rajchamaha (2017),** Stock Exchange of Thailand, holding periods of 1, 3, 5, and 10 days, two exit strategies. **Finding: most candlestick reversal patterns do not generate statistically significant mean returns.** Patterns that did show significant mean returns carried very high standard deviations, and binomial tests confirmed that most patterns — including those with significant mean returns — **cannot reliably predict direction.**
- **Fock et al. (2005)** and **Horton (2009)** also report negative evidence.

**Positive findings (mostly less-efficient markets):**

- **Caginalp & Laurent (1998)** — early supportive result.
- **Goo, Chen & Chang (2007)**, **Shiu & Lu (2011)**, **Lu & Shiu (2012)**, **Lu, Shiu & Liu (2012)** — all supportive, and **all using Taiwan market data.**
- **Lu, Shiu & Liu (2012)**, *Journal of Applied Finance / Review of Financial Economics*: two-day patterns on Taiwan Top 50 Tracker Fund components, 29 October 2002 – 31 December 2008, buying on bullish patterns and holding until a bearish pattern appears. **Finding: three bullish reversal patterns were significantly profitable after commissions and taxes, especially the Piercing pattern. Bearish patterns were not.** Robustness included market-condition subsamples, out-of-sample testing, and bootstrap.
- **Lu (2014)** — all possible one-day patterns, Taiwan, 4 January 1992 – 31 December 2009. **Finding: some one-day candlesticks, combined with the correct trend definition, are useful after transaction costs.** Also found the approach performed better on **smaller firms and lower-priced stocks** — consistent with an inefficiency explanation rather than a universal-pattern explanation.
- Work on DJIA components, January 1974 – December 2009, reported a noticeable **increase** in the predictive power of one-day patterns from 1992 onward, with bootstrap confirmation.
- Three-day reversal patterns on DJIA data with a Caginalp-Laurent holding strategy were reported profitable at 0.5% and 0.1% transaction costs after accounting for data-snooping bias — but the **same patterns with a Marshall-Young-Rose holding strategy were not profitable.**

**What Raven should conclude and state:**

1. **There is no general consensus in the literature.** Say so explicitly.
2. **The exit rule changes the answer more than the entry pattern does.** The same patterns profit or fail depending on the holding strategy. This is a strong hint that any edge lives in exit management, not pattern recognition.
3. **The trend-definition choice changes the answer.** Patterns "with the correct trend" work; the same patterns without a trend filter often do not.
4. **Positive results cluster in Taiwan, China, and Malaysia; negative results cluster in the US and Japan.** The most parsimonious reading is that candlesticks capture inefficiency where inefficiency exists, not a universal structural regularity.
5. **Confirmation improves results.** Both Fock et al. (2005) and Goo et al. (2007) reported that adding other technical indicators or stop-loss rules improved candlestick performance.

**Direct relevance to Quant:** v1 uses 7 candlestick starting patterns plus a confirmation stack, on crypto. Points 2, 3, and 5 above are the load-bearing ones — the confirmation stack and the regime filter are likely doing more work than the patterns. When porting to equities (a more efficient market with more negative evidence), **expect pattern-only performance to degrade, and design the v2 evaluation to attribute performance between pattern, confirmation, and exit separately.** The existing attribution engine is the right place to do it.

### 5.4 Chart patterns

**[PRACTICE / CONTESTED]** Larger-scale formations. The same epistemic caveats apply — arguably more so, because chart patterns are identified with more discretion and are therefore harder to test.

**Continuation:** flags, pennants, ascending/descending triangles, symmetrical triangles, rectangles, cup-and-handle, wedges (falling wedge bullish, rising wedge bearish).

**Reversal:** head and shoulders (and inverse), double top/bottom, triple top/bottom, rounding top/bottom, broadening formations, diamond.

**The standard measured-move convention [PRACTICE]:** project the pattern's height from the breakout point. Widely used, not independently validated as a profit target methodology.

**The honest problem:** chart pattern identification is not deterministic. Two analysts label the same chart differently. Any automated system must define patterns **algorithmically and unambiguously** — at which point the definition, not the pattern name, is what is being tested. Pattern win-rate statistics published without a machine-readable definition are unfalsifiable.

### 5.5 Moving averages

**[MATH]**
```
SMA(n)  = (P₁ + P₂ + ... + Pₙ) / n
EMA(n)  = α·Pₜ + (1−α)·EMAₜ₋₁        where α = 2/(n+1)
WMA(n)  = Σ(wᵢ·Pᵢ) / Σwᵢ              linearly increasing weights
```

Common periods: 9, 20, 21, 50, 100, 200. **[PRACTICE]** These are conventions, and their prominence is partly self-fulfilling — enough participants watch the 200-day SMA that it acquires some reflexive significance.

Applications: trend filter (price above/below), dynamic support/resistance, crossovers ("golden cross" 50/200 up; "death cross" 50/200 down), and slope as a trend-strength proxy.

**Known properties [MATH]:** all moving averages lag by construction. EMA lags less than SMA of the same period but is noisier. Crossover systems whipsaw badly in ranges. **Optimizing the MA period on historical data is one of the most reliable ways to produce an overfit strategy** — the parameter surface is smooth and continuous, so there is always a "best" period, and it is almost never stable out of sample.

### 5.6 VWAP — the deep dive

VWAP deserves more space than any other indicator because, uniquely, it is not primarily a retail charting tool. It is an **institutional execution benchmark**, which gives it a mechanistic basis most indicators lack.

#### 5.6.1 The math

**[MATH]**
```
VWAP = Σ(Typical Priceᵢ × Volumeᵢ) / Σ(Volumeᵢ)

where Typical Priceᵢ = (Highᵢ + Lowᵢ + Closeᵢ) / 3
```

Cumulative from a defined anchor. Standard session VWAP resets at the RTH open each day.

**Properties [MATH]:**
- Volume-weighted, so high-volume prices dominate. This is the substantive difference from an SMA, which weights all prices equally regardless of how much actually traded there.
- It is cumulative and resetting, so it becomes progressively **less sensitive** as the session accumulates volume. VWAP at 9:35 a.m. moves fast; at 3:30 p.m. it barely moves. **A strategy using VWAP distance must normalize for time of day** or it will fire constantly in the morning and never in the afternoon.
- Standard deviation bands (1σ, 2σ, 3σ) can be constructed around VWAP, analogous to Bollinger Bands but volume-weighted.

#### 5.6.2 Why institutions use it

**[RULE / PRACTICE]** VWAP is the primary benchmark for evaluating execution quality on large equity orders. A buy filled below the session VWAP is a good fill; above it is a poor one. This drives **VWAP execution algorithms** at every major brokerage: the algorithm slices a large parent order across the session in proportion to forecast volume, aiming to match or beat the day's VWAP.

**This is the mechanistic argument for VWAP mattering:** if a meaningful share of institutional volume is being executed by algorithms explicitly targeting VWAP, then VWAP is a level around which real, size-driven order flow concentrates. That is a genuinely different claim from "this indicator has historically predicted price."

**The important caveat, from the execution literature:** VWAP measures the execution *process*, not the economic cost of the decision. The true cost measure is **Implementation Shortfall**, benchmarked against the **arrival price** (the price when the decision was made). An algorithm can beat VWAP perfectly while the trader loses money because the market moved away during execution. Do not confuse a good VWAP fill with a good trade.

#### 5.6.3 Anchored VWAP (AVWAP)

**[PRACTICE]** Instead of anchoring to the session open, anchor the cumulative calculation to a chosen event: an earnings release, a gap, a major swing high or low, an IPO date, a breakout bar.

The interpretation is clean and worth stating precisely: **AVWAP from event X is the average price paid by everyone who has transacted since event X.** When price returns to that level, the aggregate position of participants since the event is roughly break-even. That is a defensible reason to expect decision-making — profit-taking, stop placement, adding — to cluster there.

Common anchors: earnings date, 52-week high/low, first day of a major move, index reconstitution date.

#### 5.6.4 Practical conventions

**[PRACTICE]**
- Price above VWAP → intraday bullish bias; below → bearish bias.
- Trending session: VWAP behaves as dynamic support/resistance; pullbacks to it are entries in the trend direction.
- Ranging session: VWAP behaves as a magnet; extensions to 2σ are fade candidates.
- Volume confirms. A reaction at VWAP on low volume is noise.
- **Treat VWAP as a zone, not a line.** Price routinely pierces it before reacting.
- Multiple session anchors matter in a 24-hour context: midnight VWAP, London-open VWAP, US RTH VWAP. As US equities move toward 23x5 (§1.5), **"the" VWAP becomes ambiguous** and the anchor must be specified explicitly.

#### 5.6.5 A warning about VWAP statistics

**[UNVERIFIED]** Vendor and educator sources circulate specific figures — for example, a claimed 65% success rate for first pullbacks to VWAP in the first 90 minutes on stocks gapping up more than 3% (attributed to TraderLion, 2022), a 63% reversion rate from 2σ extensions, and a 58% win rate at 2.1:1 for a combined opening-range + VWAP breakout on Nasdaq-100 stocks (attributed to SpeedTrader, 2020).

**None of these have published methodology, sample size, cost assumptions, or out-of-sample validation.** Raven must not cite them as statistics. They are marketing claims. If a VWAP strategy is worth trading, Quant can measure its actual hit rate on its own data — which is the only number that should ever enter a briefing.

### 5.7 Oscillators

**[MATH]**

**RSI (Relative Strength Index)** — J. Welles Wilder, 1978. Default period 14.
```
RS  = Average Gain over n periods / Average Loss over n periods
RSI = 100 − (100 / (1 + RS))
```
Bounded 0–100. Conventional thresholds: >70 overbought, <30 oversold. **[PRACTICE]** Those thresholds are conventions from 1978, not derived constants.

**Critical correction to the standard retail reading:** in a strong trend, RSI stays "overbought" for extended periods, and mechanically shorting overbought readings in an uptrend is a well-known way to lose money consistently. RSI is a **momentum** measure that is often misused as a reversal signal. Shorter lookbacks (RSI(2), RSI(3)) are the basis of a genuine short-term mean-reversion literature — but that is a different tool from RSI(14) used as an overbought/oversold gauge.

**MACD (Moving Average Convergence Divergence)** — Gerald Appel. Defaults 12, 26, 9.
```
MACD line   = EMA(12) − EMA(26)
Signal line = EMA(9) of MACD line
Histogram   = MACD line − Signal line
```
Uses: signal-line crossover, zero-line crossover, histogram divergence. **[MATH]** MACD is a difference of two lagging averages, so it lags twice. It is structurally a trend/momentum tool and performs poorly in ranges.

**Stochastic Oscillator** — George Lane. Defaults 14, 3, 3.
```
%K = 100 × (Close − Lowest Low(n)) / (Highest High(n) − Lowest Low(n))
%D = SMA(3) of %K
```
Measures close position within the recent range.

**Divergence [PRACTICE / CONTESTED]:** price makes a higher high while the oscillator makes a lower high (bearish divergence), or the inverse (bullish). Extremely popular; extremely prone to hindsight bias, because divergences that "worked" are visually memorable and divergences that failed are invisible. **Divergence must be defined algorithmically and tested before use, and the base rate of failed divergences must be measured, not assumed.**

### 5.8 Volatility measures

**ATR (Average True Range)** — Wilder, 1978. Default 14. **[MATH]**
```
True Range = max(High − Low, |High − Prev Close|, |Low − Prev Close|)
ATR = smoothed average of True Range over n periods
```
ATR is a **volatility** measure with no directional content. Its highest-value use is not signaling — it is **normalization**: position sizing, stop distance, and target distance should be expressed in ATR units so that the same strategy risks the same amount across instruments of different volatility. This is one of the genuinely robust ideas in technical analysis. See §8.3.

**Bollinger Bands** — John Bollinger. Defaults 20, 2. **[MATH]**
```
Middle = SMA(20)
Upper  = SMA(20) + 2 × standard deviation(20)
Lower  = SMA(20) − 2 × standard deviation(20)
```
Bandwidth = (Upper − Lower) / Middle, a direct volatility-regime measure. The "squeeze" (historically narrow bandwidth preceding expansion) is a **volatility** forecast, not a direction forecast — volatility clustering is one of the most robust findings in all of empirical finance, so squeeze logic has a real basis, but it tells you nothing about which way.

**Keltner Channels** — ATR-based rather than standard-deviation-based; typically EMA(20) ± 2×ATR(10). Comparing Bollinger width to Keltner width is the basis of the common "squeeze" indicator.

**Standard deviation vs. ATR:** standard deviation uses closes only; ATR incorporates gaps and intraperiod range. **For equities with overnight gaps, ATR is the more honest volatility measure.** For 24-hour crypto, the difference is smaller — another place where a v1→v2 port needs review.

### 5.9 Trend strength

**ADX (Average Directional Index)** — Wilder, 1978. Default 14. **[MATH]** Derived from +DI and −DI (directional indicators). ADX measures trend **strength** without direction. **[PRACTICE]** Conventional reading: below 20–25 indicates no trend; above 25 indicates trend; above 40–50 indicates strong trend.

Practical use: as a **regime gate**. Route to momentum logic when ADX is high, mean-reversion logic when ADX is low. This is a defensible architectural pattern and maps directly onto the regime-filter layer already in Quant's strategy genome.

### 5.10 Volume tools

- **Relative Volume (RVOL) [MATH]:** current cumulative volume ÷ average cumulative volume at the same time of day over a lookback. Time-of-day normalization is essential given the U-shaped intraday volume pattern (§6.1). RVOL is the single most useful screening variable for intraday equity strategies, because it identifies where participation is abnormal.
- **Volume Profile / Market Profile [PRACTICE]:** volume distributed by price level rather than by time. Key constructs: Point of Control (highest-volume price), Value Area (typically the 70% of volume around the POC), High Volume Nodes (acceptance) and Low Volume Nodes (rejection, where price tends to move quickly).
- **OBV (On-Balance Volume) [MATH]:** cumulative sum of volume, signed by the direction of the close. A crude flow proxy.
- **Accumulation/Distribution, Chaikin Money Flow, Money Flow Index:** variations weighting volume by where the close sits within the bar's range.
- **VPOC, delta, footprint charts:** order-flow-level tools requiring tick and bid/ask-classified data, not OHLCV.

**Warning [MATH]:** OBV and related indicators derived only from OHLCV cannot distinguish buying pressure from selling pressure. They infer it from close position. Genuine order-flow analysis requires trade-level data with aggressor classification. Do not represent an OHLCV-derived indicator as order flow.

### 5.11 Multi-timeframe analysis

**[PRACTICE]** The standard architecture: higher timeframe sets bias, intermediate timeframe sets setup, lower timeframe sets trigger. Common ratios are roughly 4:1 to 6:1 between levels (daily → hourly → 15-min, or hourly → 15-min → 3-min).

**The look-ahead trap [MATH]:** when combining timeframes in a backtest, a higher-timeframe bar is **not complete** until its close. Using a completed daily bar to filter intraday signals from within that same day is look-ahead bias, and it is the single most common silent error in multi-timeframe backtesting. Use only the **prior** completed higher-timeframe bar. This is worth a dedicated assertion in the backtest harness.

### 5.12 Indicator pathologies to encode as guardrails

1. **Redundancy.** RSI, Stochastic, CCI, and Williams %R are all normalized momentum measures. Stacking them is not confirmation — it is the same signal counted four times, which inflates apparent confluence while adding no information.
2. **Lag.** Every moving-average-derived indicator lags by construction. There is no setting that removes it.
3. **Repainting.** Some indicators (certain pivot, zigzag, and "non-lag" constructions) revise historical values as new data arrives. **A repainting indicator produces a perfect backtest and a worthless live system.** Any indicator in the Quant library must be verified non-repainting, and that verification should be an automated test, not a code review note.
4. **Parameter overfitting.** Every indicator has parameters, every parameter can be optimized, and the optimum is rarely stable. Prefer defaults or coarse, theoretically motivated values. If a strategy only works at RSI(11) and fails at RSI(10) and RSI(12), it does not work.
5. **Multiple comparisons.** Testing many indicator/parameter combinations guarantees false positives. This is exactly what the **rejection graveyard** in the current SPEC is for — but it only helps if the count of tested hypotheses is actually used to adjust the significance bar, not merely logged.

---
## 6. Strategy playbooks

Each entry states the structure, the mechanistic rationale, the evidence status, and the failure mode. **None of these are recommendations.** They are hypotheses with known properties.

### 6.1 Intraday time-of-day structure (the substrate for everything below)

**[EVIDENCE]** The U-shaped intraday pattern is one of the oldest and most replicated findings in market microstructure.

- **Wood, McInish & Ord (1985)** documented high variability in returns during the opening and closing minutes on the NYSE — the original U-shape finding.
- **Harris (1986)** confirmed the positive correlation between volume and price change across 479 NYSE stocks.
- **Jain & Joh (1988)** reported a statistically significant U-shaped pattern in NYSE trading volume, plus day-of-week effects.
- **McInish & Wood (1990, 1992)**, **Foster & Viswanathan (1993)**, and many others replicated across markets.
- **Admati & Pfleiderer (1988)** provided the theoretical account: informed-trader concentration drives liquidity traders to cluster, producing simultaneous high volume and high volatility.
- More recent work on Nasdaq in the electronic era confirms strong U-shapes in **both trading volume and bid-ask spread**, and links closing-spread spikes to the observed pattern of higher overnight than intraday returns for some stocks.

**Nuance [CONTESTED]:** **Eaves & Williams (2010)** found intraday **volume** is U-shaped but intraday **volatility** is closer to L-shaped, and concluded that informed-trader timing cannot be the source of the patterns. Some markets show W-shapes rather than U-shapes (Bildik, Istanbul).

**What this means operationally:**
- Spread and volatility are highest at the open and elevated into the close.
- Costs are worst exactly when opportunity looks best.
- Any volume-based indicator must be **time-of-day normalized** or it will systematically misfire.
- The closing auction is the largest single liquidity event of the day.

**Intraday momentum [EVIDENCE]:** research on the Chinese market documented that the first and/or second-to-last half-hour returns significantly predict the last half-hour return, in and out of sample, with economically meaningful gains. The authors tie it to infrequent-rebalancing and late-informed investors, consistent with the U-shaped volume pattern. **This is a documented anomaly in one market. It is not a validated US equity strategy.** Test it; do not assume it transfers.

**The overnight/intraday split [CONTESTED, and unusually interesting]:** **Knuteson** documented that across 21 major world stock indices over several decades, **overnight returns (close to next open) have been strongly positive while intraday returns (open to close) have been negative or flat.** The pattern is remarkably consistent and there is **no consensus explanation.** Knuteson argues the popular innocuous explanations are wrong and advances a more troubling interpretation.

Raven's stance: this is a genuine, large, well-documented empirical regularity with a **disputed cause.** It is worth studying and worth testing. It is not something to bet the book on, because a pattern without an understood mechanism can disappear without warning — and the transition to 23x5 trading (§1.5) may literally destroy the "overnight" concept that defines it.

### 6.2 Opening Range Breakout (ORB) and Gap-and-Go

**Structure:** define an opening range (first 5, 15, or 30 minutes). Enter long on a break above the range high, short on a break below the range low. Stop at the opposite side of the range or at a fraction of the range. "Gap-and-Go" adds a pre-market gap and elevated RVOL as a precondition.

**Mechanistic rationale:** the opening period resolves overnight information; a decisive break signals which side won the auction.

**Evidence status [CONTESTED — and the negative evidence is strong]:**

A systematic falsification study on MNQ futures tested the 09:30–09:55 ET opening range (first six five-minute bars) across immediate, pullback, and delayed entry variants, after 2-point friction:

| Variant | N | Mean net | T-stat | Win rate | Verdict |
|---|---|---|---|---|---|
| ORB Long — bar+1 | 447 | −0.82 pts | 1.17 | 51.9% | FAIL |
| ORB Long — bar+15 | 447 | +2.82 pts | 1.50 | 55.5% | FAIL |
| ORB Short — bar+1 | 428 | −3.45 pts | −1.33 | 47.2% | FAIL |
| ORB Short — bar+15 | 428 | −2.16 pts | −0.04 | 47.7% | FAIL |
| ORB Pullback entry | 83 | −4.44 pts | −1.27 | 19.3% | FAIL |

**Note the critical detail: the best variant had a 55.5% win rate and still failed statistical significance.** This is the clearest available illustration of why win rate is a nearly useless statistic in isolation.

**Failure modes:** false breakouts in range-bound sessions; the opening range period is a free parameter that invites overfitting; slippage at the open is at its daily maximum; a stop at the opposite side of the range means risk scales with opening volatility, which is exactly when it is largest.

**Verdict for Quant:** ORB is the most-taught intraday strategy in retail education and has poor published evidence. If tested, it must clear the standard lifecycle bar with realistic friction, and the opening-range length must be fixed *a priori*, not selected.

### 6.3 VWAP strategies

**Variant A — VWAP trend continuation.** In a trending session, enter pullbacks to VWAP in the trend direction. Rationale: institutional VWAP algorithms create real buying support below VWAP in accumulation and selling resistance above it in distribution. Stop below the VWAP zone; exit at a prior extreme or an ATR multiple.

**Variant B — VWAP mean reversion.** In a range-bound session, fade extensions to the 2σ or 3σ VWAP band back toward VWAP. Requires an explicit regime classifier, because running this in a trend is how accounts die.

**Variant C — AVWAP confluence.** Anchor VWAP to a significant event and treat the level as decision-relevant when price returns to it.

**Evidence status [PRACTICE, mechanistically motivated].** VWAP has a stronger *a priori* case than most indicators because the institutional benchmark use is documented and creates real order flow. But **the specific hit rates circulating in retail education are unverified vendor claims (§5.6.5).**

**Failure modes:** regime misclassification (fading a trend); VWAP's declining sensitivity late in the session; band width being volatility-dependent so σ-based triggers fire unevenly across the day; and the fact that a strategy premised on institutional flow requires the instrument to actually have institutional flow — which excludes most small caps.

### 6.4 Small-cap / low-float momentum ("runners")

**Structure:** screen for low float (commonly under 20M shares), a news catalyst, extreme RVOL (often 5–20×), and a large pre-market gap. Trade the resulting intraday momentum long, or short the exhaustion.

**Mechanistic rationale:** with a small float and a demand shock, price must move a long way to clear the imbalance. This is genuinely real — the mechanism is sound.

**Why this is nonetheless the most dangerous category in this document:**

1. **It overlaps heavily with the manipulation universe (§3.5).** The catalyst may be a promotion. As of 2026, an exchange listing does not exclude this.
2. **Dilution is structural.** Companies with these profiles frequently have at-the-market offerings or toxic convertible financing that fire directly into the spike. The move is often the financing event.
3. **SSR will be active** on most of these on the down leg (§1.7.3), changing short-side fills.
4. **Borrow is expensive or unavailable** exactly when the short setup looks best (§2.5).
5. **LULD halts fire repeatedly.** Tier 2 bands, doubled at the open, with 5-minute pauses and reopening auctions. Your stop does not execute during a halt.
6. **House margin requirements** are frequently 100% on these names, sometimes changed intraday.
7. **Backtest survivorship bias is extreme.** The names that got suspended or delisted are often missing from historical datasets, which biases results upward in the most misleading possible direction.

**Verdict for Quant:** if this category is ever pursued, it requires a purpose-built harness with halt simulation, SSR logic, borrow-cost modeling, and a delisting-inclusive dataset. Nothing less produces a meaningful number. This is a v3+ conversation at the earliest.

### 6.5 Short-term mean reversion (swing)

**Structure:** in an instrument above a long-term trend filter (e.g., above the 200-day SMA), buy short-term oversold conditions (RSI(2) below a threshold, N consecutive down closes, or a close below a lower band), exit on reversion (RSI recovery, close above a short MA, or a fixed holding period).

**Mechanistic rationale:** short-horizon reversal is one of the better-documented cross-sectional equity effects, plausibly a liquidity-provision premium — you are being paid to supply liquidity to forced or impatient sellers.

**Evidence status [EVIDENCE, with a large caveat].** Short-horizon reversal is well documented in the academic cross-sectional literature. **The caveat is that documented reversal effects are strongest in exactly the small, illiquid stocks where transaction costs are highest**, and a substantial part of the literature finds the effect shrinks or disappears after realistic costs. Test with per-symbol costs, never a flat assumption.

**Failure modes:** catching a genuine repricing rather than noise (the position keeps going); the strategy has a high win rate and a poor payoff ratio, so a single failure erases many wins; and it performs worst precisely during market stress, when correlations converge and everything is oversold simultaneously.

### 6.6 Trend following and breakout (swing/position)

**Structure:** enter on a break of an N-period high (Donchian-style), or on a moving-average alignment; trail a stop at an ATR multiple or an N-period low; hold until the trail is hit.

**Mechanistic rationale:** time-series momentum has one of the longest and broadest bodies of supporting evidence in finance, documented across equities, bonds, commodities, and currencies over long histories. Behavioral explanations (underreaction, disposition effect) and risk-based explanations both exist.

**Evidence status [EVIDENCE — among the strongest in this document, with caveats].** Cross-sectional and time-series momentum are widely replicated. Caveats that matter: momentum suffers rare, severe crashes (post-crisis rebounds are brutal for momentum); it has experienced long flat periods; and much of the published evidence uses long/short portfolios of many names, which is a different animal from a single-instrument trend system.

**Failure modes:** low win rate by construction (often 30–40%), so it demands psychological and structural tolerance for long losing streaks; whipsaw in ranges; and the trailing stop gives back a meaningful fraction of every winner by design.

**Note for the Quant lifecycle:** a trend strategy with a 35% win rate and PF 1.4 is healthy, but an **auto-demote at 8 straight losses** may fire on a normal trend-following losing streak. At a 35% win rate, the probability of 8 consecutive losses at some point in 150 trades is substantial. **This is a real interaction between §8 statistics and the existing risk model — worth checking against the auto-demote rule before v2 goes live.**

### 6.7 Pairs trading / statistical arbitrage

**Structure:** identify two historically cointegrated instruments, trade the spread when it deviates beyond a threshold, exit on convergence.

**Mechanistic rationale:** if two assets share economic drivers, relative mispricing should be temporary.

**Evidence status [CONTESTED].** Documented historically, with substantial evidence that returns declined markedly as the strategy became widely known and as execution costs for the pure form compressed.

**Failure modes:** cointegration is a statistical property that **breaks**, usually for a fundamental reason (merger, regulation, business-model divergence), and precisely when your position is largest. The strategy requires shorting, therefore borrow costs, locate, and SSR. Spread-based backtests are exceptionally prone to look-ahead bias in the cointegration estimation window.

### 6.8 Earnings-related strategies

**[RULE / PRACTICE]** Earnings create a scheduled, known-date volatility event. Implied volatility rises into the event and collapses immediately after ("IV crush").

- **Long premium into earnings** requires the realized move to exceed the implied move — a bet against the variance risk premium (§7.4).
- **Short premium into earnings** collects the elevated IV but carries the full gap risk.
- **Post-earnings-announcement drift (PEAD)** is one of the most robustly documented anomalies in the academic literature: prices continue drifting in the direction of the earnings surprise for weeks afterward. **[EVIDENCE]** It has been documented for decades and has persisted, though evidence on decay in recent decades is mixed.

**Failure mode across all earnings strategies:** the event is binary and gaps through stops. Position sizing must assume the stop does not work.

### 6.9 SPY and QQQ index trading specifics

**Why these two dominate [RULE / PRACTICE]:**
- Tightest spreads of any equity instrument (and now potentially half-penny quoted, §1.3).
- Deepest options chains, with daily expirations.
- No single-company idiosyncratic risk — no accounting fraud, no FDA rejection, no CEO departure.
- No borrow problem on the short side (though SSR can still apply).
- Effectively no manipulation risk at the instrument level.

**Structural differences to hold:**
- **SPY** = S&P 500, ~500 names, top-10 weight around 39% (mid-2026), broad but increasingly concentrated.
- **QQQ** = Nasdaq-100, ~101 names, top-10 weight near 50%. **A concentrated mega-cap tech expression, not broad market exposure.**
- QQQ typically has higher realized volatility and higher beta than SPY. A strategy calibrated on SPY will size incorrectly on QQQ unless volatility-normalized (§8.3).
- Both are UITs and cannot internally reinvest dividends.

**The SPX/SPY choice [RULE]:** for options, SPX (and its 1/10-size sibling XSP) offers Section 1256 60/40 tax treatment, cash settlement, no early assignment (European), and no share-position risk. SPY offers finer strike granularity, smaller contract size, and American-style flexibility. **For a systematic options strategy at any meaningful scale, the SPX/XSP tax and settlement advantages are substantial (§12).**

---

## 7. Options — deeper mechanics

### 7.1 Implied volatility, term structure, and skew

**[MATH]** Implied volatility is the volatility input that makes a pricing model output the observed market price. It is **not** a forecast in the ordinary sense — it is the market's price of volatility, which embeds both a forecast and a risk premium.

- **Term structure:** IV plotted across expirations. Normally upward-sloping (contango) in calm markets; inverts (backwardation) in stress. An inverted term structure is a stress signal.
- **Skew / smirk:** IV plotted across strikes for one expiration. **S&P 500 options exhibit the most pronounced smirk of any major asset** — OTM puts carry substantially higher IV than OTM calls, reflecting persistent demand for downside protection. Not all assets share this: agricultural commodities, the Japanese yen, and the Swiss franc show *inverse* smirks with OTM calls priced higher. The sign of the smirk is associated with the sign of the correlation between spot and volatility. **[EVIDENCE]**
- **IV Rank / IV Percentile [PRACTICE]:** where current IV sits relative to its own trailing range. Used to decide whether to be a net buyer or seller of premium.

### 7.2 The variance risk premium — the closest thing to a documented options edge

**[EVIDENCE — this is well documented, and the caveat is as important as the finding]**

**The finding:** option-implied volatility systematically **exceeds** subsequently realized volatility of the same underlying. The gap is the **variance risk premium (VRP)**. It has been documented extensively — Bakshi & Kapadia (2003), Carr & Wu (2009), Todorov (2010), Drechsler (2013), and many others — and is found to be significant and systematically negative (from the buyer's perspective) across a range of indices, individual stocks, and ETFs.

Reported magnitudes vary by source and period. One commonly cited figure is that S&P 500 one-month implied volatility has averaged roughly **3–4 percentage points above subsequent realized volatility**, with variance sellers collecting positive carry in something on the order of 85% of months. **[EVIDENCE — treat the specific numbers as period-dependent and re-verify against current data.]**

**Why it exists — and why it is not free money.** The premium is compensation for a genuinely unpleasant risk profile:
- Markets gap, and gap risk is expensive to hedge.
- Buyers pay for protection around scheduled events (earnings, FOMC, elections) — a jump premium that continuous realized-volatility measurement does not capture.
- Volatility spikes disproportionately when markets decline, so short-volatility losses arrive precisely in bad times, correlated with everything else in a portfolio.

**Because losses cluster in bad times, this premium should not be arbitraged away.** That is the strongest argument for its persistence — and simultaneously the reason it is dangerous.

**The failure mode, stated plainly.** Short-volatility strategies are described in the literature as "generally profitable, but vulnerable to abrupt volatility spikes." The return distribution is high win rate with severe negative skew: many small gains, occasional catastrophic losses. Quantpedia notes research showing selling puts has produced substantial average returns; the same body of work documents the tail. **A short-volatility strategy evaluated on win rate or on profit factor over a calm sample will look outstanding and will be lethal.** Evaluate on maximum drawdown, tail loss, and performance conditional on volatility regime — never on win rate.

**Requirement for Quant v4:** any short-premium strategy must be evaluated with an explicit stress probe covering at minimum a February 2018-style volatility spike, a March 2020-style crash, and an August 2024-style unwind. The SPEC already includes backtest stress probes; **this is the category where they are non-negotiable.**

### 7.3 0DTE — the structural shift in the options market

**[EVIDENCE — Cboe data]** Zero-days-to-expiration options have gone from a niche to the dominant form of SPX trading. The trajectory:

| Period | 0DTE share of total SPX volume |
|---|---|
| 2016 | ~5% |
| End of 2023 | ~45% |
| 2024 | ~47% |
| February 2025 | 56% (record at the time) |
| August 2025 | 62.4% |
| May 2026 | **65%** |
| July 2026 | **66.2%** |

Supporting figures: Cboe reported Q2 2026 SPX options ADV of **5.1 million contracts** (up 40% YoY) with 0DTE ADV of **3.1 million** (up 48%). Mini-SPX (XSP) set a July 2026 monthly ADV record of **238,000 contracts** with **138,000** 0DTE. Participation is broad: individual participation in 0DTE reached 65% of customer ADV in May 2026, institutional 60%.

Drivers cited by Cboe: the introduction of Tuesday and Thursday expirations in 2022, expanded retail access (Robinhood rolled out index options to all customers in late January 2025), macro uncertainty, and the **repeal of the PDT rule in June 2026**.

**What 0DTE actually is, mechanically [MATH]:** an option with hours to live has near-zero vega, enormous gamma near the money, and theta decaying at its maximum rate. Delta flips between ~0 and ~1 across the strike within a very small price move. **It is closer to a leveraged directional bet with a hard time limit than to a conventional option position.**

**Risks specific to 0DTE:**
- Gamma risk is extreme. A short 0DTE position can go from comfortably OTM to deeply ITM in minutes.
- There is no time to recover. Conventional management techniques — rolling, adjusting, waiting — do not exist.
- Assignment/settlement risk is immediate for physically settled products. **Cash-settled index options (SPX, XSP) eliminate this specific risk**, which is a substantial part of why institutional 0DTE flow concentrates in SPX rather than SPY.
- Liquidity is excellent in SPX/SPY/QQQ and poor almost everywhere else.

**Note the reflexive loop worth flagging:** 0DTE volume growth was partly driven by the PDT repeal, and the PDT repeal was justified in part by FINRA's observation that the 2001 framework "did not address recently popular products like 0DTE options." The regulation and the product co-evolved.

### 7.4 Core option structures

| Strategy | Construction | Direction | Vol view | Max loss | Notes |
|---|---|---|---|---|---|
| Long call | Buy call | Bullish | Long vol | Premium | Needs move + speed; theta works against you |
| Long put | Buy put | Bearish | Long vol | Premium | Same |
| Covered call | Long 100 shares + short call | Mildly bullish | Short vol | Stock to zero, less premium | Caps upside; watch qualified-covered-call rules (§12) |
| Cash-secured put | Short put + cash | Mildly bullish | Short vol | Strike − premium | Synthetically equivalent to a covered call |
| Bull call spread | Buy lower call, sell higher call | Bullish | Mixed | Net debit | Defined risk, defined reward |
| Bear put spread | Buy higher put, sell lower put | Bearish | Mixed | Net debit | Defined both ways |
| Bull put spread (credit) | Sell higher put, buy lower put | Bullish/neutral | Short vol | Width − credit | High win rate, poor payoff ratio |
| Bear call spread (credit) | Sell lower call, buy higher call | Bearish/neutral | Short vol | Width − credit | Same profile |
| Iron condor | Bull put spread + bear call spread | Neutral | Short vol | Width − credit | Profits from range + time; loses on any large move |
| Iron butterfly | ATM short straddle + protective wings | Neutral | Short vol | Width − credit | Tighter, higher credit, narrower profit zone |
| Long straddle | Buy ATM call + put | Direction-agnostic | Long vol | Both premiums | Needs a move larger than implied |
| Long strangle | Buy OTM call + put | Direction-agnostic | Long vol | Both premiums | Cheaper, needs a bigger move |
| Short straddle/strangle | Sell the above | Neutral | Short vol | **Undefined** | Maximum VRP capture, maximum tail risk |
| Calendar spread | Sell near-dated, buy longer-dated, same strike | Neutral | Long vol (term) | Net debit | Trades term structure |
| Diagonal | Calendar with different strikes | Directional-neutral | Mixed | Net debit | More parameters, more ways to be wrong |
| Collar | Long stock + long put + short call | Protective | Mixed | Defined | Financed hedge |
| Ratio spread | Unequal long/short counts | Directional | Varies | **Can be undefined** | Check the naked leg carefully |

**The structural asymmetry Raven must state whenever credit spreads come up [MATH]:** a credit spread that collects $0.30 on a $1.00-wide spread risks $0.70 to make $0.30. Breakeven win rate is 70%. A 75% win rate looks impressive and delivers a thin edge that one bad month erases. **High win rate and positive expectancy are different properties, and short-premium structures are engineered to look good on the first while being fragile on the second.**

### 7.5 Options liquidity screening

**[PRACTICE]** Before any options strategy is considered on an underlying, check:
1. Bid-ask spread as a percentage of the mid — under ~5% for liquid, over ~10% is prohibitive for anything active.
2. Open interest at the specific strikes, not just on the underlying.
3. Volume at the specific strikes.
4. Number of listed expirations and strike granularity.
5. Whether the options are **adjusted** (from splits or special dividends) — adjusted contracts have non-standard deliverables and are a common source of expensive surprises.

Practical reality: genuinely liquid US options exist on roughly a few hundred underlyings. Everything below mega/large cap has options that exist on paper and are untradeable in practice for a systematic strategy.

---
## 8. Risk management and position sizing

This section is arithmetic, not opinion. It is the most transferable material in the document.

### 8.1 The R-multiple framework

**[MATH]** Define **R** = the dollar amount risked on a trade (entry price minus stop price, times position size). Express every outcome as a multiple of R. A trade that made twice what it risked is +2R; a full stop-out is −1R.

Why this matters: it makes results comparable across instruments, position sizes, and account sizes, and it separates **trade selection** from **position sizing** — two things retail traders routinely conflate.

### 8.2 Expectancy

**[MATH]**
```
Expectancy (in R) = (Win rate × Average win in R) − (Loss rate × Average loss in R)
```

Worked examples:
- 60% win rate, average win 1R, average loss 1R → (0.6 × 1) − (0.4 × 1) = **+0.20R per trade**
- 40% win rate, average win 3R, average loss 1R → (0.4 × 3) − (0.6 × 1) = **+0.60R per trade**
- 75% win rate, average win 0.4R, average loss 1R → (0.75 × 0.4) − (0.25 × 1) = **−0.05R per trade** — *negative despite a 75% win rate*

**The third example is the entire reason win rate must never be reported without a payoff ratio.** It is also the exact profile of an under-priced credit spread (§7.4).

**Profit factor [MATH]:** `Gross profit / Gross loss`. Related but not identical to expectancy — profit factor ignores trade count, expectancy does not. Quant's existing PF thresholds (backtest ≥1.3, shadow ≥1.1, auto-demote below 1.0, auto-retire below 0.8) are a sound frame; pairing each with an expectancy-in-R figure would make briefings more interpretable.

### 8.3 Position sizing

**Fixed fractional [MATH]** — the standard:
```
Position size = (Account equity × Risk fraction) / (Entry price − Stop price)
```
Risk fraction is typically 0.5%–2% per trade for discretionary retail; systematic strategies with many concurrent positions use less per position.

**ATR-normalized sizing [MATH]** — the better default for a multi-instrument system:
```
Position size = (Account equity × Risk fraction) / (k × ATR)
```
where k is a multiple (commonly 1.5–3). This equalizes risk across instruments of different volatility, so the same strategy risks the same amount on a 1%-daily-range mega cap and a 6%-daily-range small cap. **Without volatility normalization, a multi-instrument strategy's realized risk is whatever the most volatile instrument happens to be doing.**

**Kelly criterion [MATH]:**
```
f* = (p × b − q) / b
```
where p = win probability, q = 1 − p, b = win/loss ratio. Kelly maximizes long-run geometric growth.

**Why nobody sane trades full Kelly:** it assumes p and b are **known exactly**. They are estimated, with error, from a finite sample. Overestimating the edge causes overbetting, and the penalty is severe and asymmetric. Standard practice is **half-Kelly or quarter-Kelly**, which sacrifices modest growth for a large reduction in drawdown and in sensitivity to estimation error.

**Directly relevant to Quant's current risk model:** v1 uses a fixed $100 notional cap that deliberately does not scale with balance, with compounding parked as a V4+ decision. That is a defensible and conservative choice — it eliminates estimation-error risk entirely at the cost of growth. **When scaling is revisited, the framework above (fractional Kelly on measured live expectancy, not backtested expectancy) is the right approach, and the parameters must come from live data with an adequate sample.**

### 8.4 Drawdown mathematics

**[MATH]** Recovery is asymmetric and the asymmetry is brutal:

| Drawdown | Gain required to recover |
|---|---|
| 10% | 11.1% |
| 20% | 25.0% |
| 30% | 42.9% |
| 40% | 66.7% |
| 50% | **100%** |
| 60% | 150% |
| 75% | **300%** |
| 90% | **900%** |

This is why maximum drawdown is a **hard constraint**, not a performance metric. Quant's DD ≤ 15% backtest bar and ≤ 12% go-live bar are well-calibrated against this table.

**Streak probabilities [MATH]:** the probability of at least one run of k consecutive losses in n trades at loss rate q is substantial for realistic parameters. A 40%-win-rate trend strategy has a ~60% per-trade loss rate; over 150 trades, an 8-loss streak is not unusual — it is expected behavior. **Any auto-demote rule keyed to a consecutive-loss count must be calibrated against the strategy's own win rate, not set as a global constant** (see the note in §6.6).

### 8.5 Correlation and portfolio-level risk

**[MATH]** Running five positions each risking 1% is not 5% of risk if the positions are correlated. In a stressed market, correlations across equities converge toward 1 — meaning a "diversified" book of five equity longs becomes, functionally, one position at 5% risk exactly when that matters most.

Controls: cap sector exposure, cap total gross and net exposure, cap the number of positions sharing a common factor (beta, sector, size, or a single macro driver), and measure realized portfolio correlation rather than assuming diversification from position count.

**Note for v2:** Quant v1's max-2-concurrent-positions cap (one per pair) across BTC/ETH/SOL is already conservative — but those three are highly correlated, so the effective diversification is near zero. The same trap scales badly in equities, where a "diversified" list of five names can all be the same AI-infrastructure trade.

### 8.6 Stops — what they do and do not do

**[MATH]** A stop converts unbounded price risk into bounded price risk **plus unbounded slippage risk.** It does not guarantee an exit price. It fails specifically when:
- The market gaps through it overnight or over a weekend
- The instrument is halted (§1.7) — no orders execute during a halt
- Liquidity evaporates in a fast move
- It is a stop-market in a thin book

**Stop placement principles [PRACTICE]:**
- Place the stop where the **thesis is invalidated**, then size the position to that distance. Never pick a size first and place the stop where the dollar loss feels tolerable.
- Volatility-based stops (ATR multiples) adapt to conditions; fixed-percentage stops do not.
- Obvious levels (round numbers, prior day's low, session extremes) are where stops cluster and therefore where they get run.

### 8.7 System-level circuit breakers

**[PRACTICE]** Exactly the model already in Quant v1, which generalizes well to equities:
- Daily loss limit → halt for the day
- Weekly loss limit → halt for the week
- Consecutive-loss pause
- Maximum concurrent positions
- Exchange-side (not just application-side) stops
- A manual kill switch

Additions specific to equities and options:
- **Halt-aware position management** — pre-defined behavior when a held name halts
- **Expiration-day position policy** for options (§3.8.3)
- **Corporate action screening** — splits, dividends, mergers, ticker changes, and index reconstitution all break naive position tracking
- **Earnings-date awareness** — either exclude names inside an earnings window or size explicitly for gap risk

---

## 9. What the evidence actually says about trading outcomes

Raven must be able to state this accurately and without either doom-mongering or salesmanship.

### 9.1 Day trader profitability — the primary studies

**[EVIDENCE]**

**Chague, De-Losso & Giovannetti (2020), "Day Trading for a Living?"** — Brazilian equity index futures market (third largest by volume globally). Observed **all 19,646 individuals** who began day trading between 2013 and 2015. Of the roughly 1,600 who persisted **beyond 300 trading days**: **97% lost money.** Only **1.1%** earned more than the Brazilian minimum wage. In the version reporting dollar figures, only **0.4%** earned more than a bank teller (about US$54/day), and the single best individual earned about US$310/day with a standard deviation of US$2,560 — enormous risk for the return.

**Barber, Lee, Liu & Odean (2014), "The Cross-Section of Speculator Skill: Evidence from Day Trading"** — complete Taiwan Stock Exchange records, **1992–2006**. Found **fewer than 1% of day traders were predictably profitable net of fees** — roughly 4,000 out of ~450,000 in a typical year. A related finding in the same body of work is that roughly **15–20% of day traders profited net of fees in a given period**, which is a different question from being *predictably* profitable.

**Important: those two numbers are not contradictory, and the distinction is the whole point.** "Profitable in a given year" (~15–20%) includes luck. "Predictably profitable — skill detectable out of sample" (<1%) is the number that matters for anyone deciding whether to do this.

### 9.2 Other markets — the numbers vary, the conclusion does not

| Study | Market | Period | % profitable net of fees |
|---|---|---|---|
| Jordan & Diltz (2003) | US, 324 day traders | Feb 1998 – Oct 1999 | 36% |
| Choe & Eom (2009) | Korea, index futures | Jan 2003 – Mar 2005 | 25% |
| Kuo & Lin (2013) | Taiwan, futures | Oct 2007 – Sep 2008 | 19% |
| Barber et al. (2014) | Taiwan, stocks | 1992–2006 | ~20% profitable; **<1% predictably** |
| Chague et al. (2020) | Brazil, index futures | 2013–2015, 300+ days | **3%** |

**NASAA's "Report of the Day Trading Project Group"** examined US day-trading firms during the late-1990s boom and concluded that promotional claims that 80–85% of customers were profitable were **not supported by the firms' own internal data**, and that the overwhelming majority of customers lost money, often quickly and in large amounts.

### 9.3 How to state this honestly — the range and why sources disagree

**[CONTESTED, but only about the number, not the direction]** Reported figures range from 1% to over 30%. The dispersion is almost entirely explained by definitional differences:

- **Sample definition.** "Everyone who ever placed a day trade" vs. "everyone who persisted 300+ days" vs. "active users of a journaling app" are three different populations. Requiring persistence *selects for* people who did not quit after losing — which cuts both ways.
- **Profitability definition.** Gross vs. net of commissions vs. net of all costs and taxes vs. exceeding a benchmark vs. exceeding minimum wage.
- **Time horizon.** One-year profitability rates are consistently far higher than three-year rates. Most apparently profitable traders regress toward the mean.
- **Self-selection.** One trading-journal platform reports 31% net profitable over 90+ days among its ~8,400 active journalers, and states plainly that journaling users self-select for traders roughly 2–3× more likely to be in the profitable tail, estimating the base rate for non-journalers at 5–10%. That is unusually candid vendor disclosure and worth respecting as such — but it is still a vendor sample.

**The defensible summary Raven should use:** *Across academic studies of complete trader populations in multiple countries, the share of day traders who are profitable net of costs over a full year is typically in the single digits to low tens of percent, and the share who are predictably, persistently profitable over multiple years is on the order of 1–3%. Estimates vary widely because "day trader" and "profitable" are defined differently across studies. The direction of the finding is consistent across every market studied.*

Do not use the bare "90% of traders lose money" line. It is directionally right and methodologically empty.

### 9.4 The mechanism — it is arithmetic, not psychology

The most useful framing in the literature is not that traders lack discipline. It is that **retail day trading requires extracting a signal that barely exists at intraday horizons, while paying full transaction costs hundreds of times a year, against counterparties who are faster by orders of magnitude.**

Related documented behavioral effects, each independently replicated:
- **Overtrading** — high trading frequency by individuals is strongly associated with underperformance versus buy-and-hold (Barber & Odean's broader work).
- **Disposition effect** — investors systematically realize gains too early and hold losses too long.
- **Leverage amplification** — day traders using margin have been reported with average returns around −4.53% in the compiled statistics literature. **[CONTESTED — treat as directional, not precise.]**

**What Raven should take from §9:** this is *not* an argument that systematic trading is futile. It is an argument that the failure mechanism is **cost arithmetic plus overconfident position sizing**, both of which a disciplined automated system with an explicit fee-to-edge gate is well positioned to avoid — which is precisely what Quant's existing design does. The honest framing is: the base rate is bad, the reasons are identifiable, and the architecture should be built to attack those specific reasons.

---

## 10. Social trading, r/wallstreetbets, and the gambling layer

### 10.1 Why this section exists and how to read it

WSB is not a curiosity to be sneered at or a folk hero to be celebrated. It is the largest visible retail trading population in the world, it demonstrably moves prices, and it is the single richest natural experiment in retail behavior that finance research has ever had access to. There is now a substantial peer-reviewed literature on it in top journals.

It is also a place where the reported outcomes are systematically unrepresentative of the actual outcomes, for structural reasons that are measurable rather than moralistic.

**Raven's stance:** treat WSB as (a) a genuine data source with documented, conditional predictive properties, (b) a documented case study in selection bias, and (c) a documented risk factor for the individual. All three are supported by evidence. State all three. Do not collapse into either "Reddit degenerates" dismissal or "retail army" romanticism — both are lazy, and both are contradicted by parts of the record.

**Scale [RULE]:** founded 31 January 2012 by Jaime Rogozinski. Reported subscriber counts vary by source and date — roughly 16 million in one May 2026 account and roughly 19.9 million in another 2026 figure. At the peak of the GameStop episode, 28 January 2021, the forum generated more than 271 million pageviews in a single day. It grew roughly 10x year-over-year into early 2021.

### 10.2 What actually works — the finding most people get wrong

The most important result in this literature is not "WSB is worthless." It is that **WSB's informativeness was real, then died, and the death is dateable.**

#### The Bradley, Hanousek, Jame & Xiao result

**[EVIDENCE — *Review of Financial Studies*, Vol. 37, Issue 5, May 2024, pp. 1409–1459]**

The authors examined "due diligence" (DD) recommendation posts on WSB. Findings:

- **Before the GameStop squeeze, WSB DD recommendations were significant predictors of future returns and of cash-flow news.** Despite anonymity, no editorial review, and no reputational stake, the forum was producing genuinely informative research.
- **After GME, that predictability was completely eliminated.**
- The compositional shift is measurable: post-GME, the fraction of reports emphasizing **price-pressure strategies rose by 165%**, and the fraction of reports on **attention-grabbing stocks rose by 75%**.
- The decline in informativeness was **concentrated in exactly those reports** — the price-pressure and attention-grabbing ones.
- Retail trade informativeness followed the same arc: strong following DD reports pre-GME, absent post-GME.

The authors' interpretation: the GameStop event **altered the culture of WSB**, and the resulting deterioration in research quality adversely affected smaller investors.

**Why this matters more than any other single finding here.** It is not a story about retail investors being stupid. It is a story about a forum that had a real information-production function, becoming famous, attracting a different population, and converting from a research venue into a coordination venue — at which point the information disappeared. **The mechanism was popularity, not incompetence.** Any social-sentiment signal Quant ever builds is subject to the same decay process.

#### The Semenova et al. result — granularity matters

**[EVIDENCE — Semenova, Gorduza, Wildi, Dong & Zohren, *Journal of Portfolio Management*, 50(4), 2024, pp. 88–106]**

This paper decomposed WSB with topic modeling and network analysis. Two findings worth carrying:

1. **Forum activity has a Granger-causal relationship with the returns of several assets** — some now classified as meme stocks, others that went unnoticed.
2. On portfolio construction: investing on **all posts mentioning a ticker** produced next-day log returns **not statistically different from zero.** But **moderator-flaired DD posts and hand-labeled posts showed statistically significant positive returns.**

**The lesson is a filtering lesson, not a sentiment lesson.** Raw mention volume is noise. Curated, effortful posts carried signal. Any WSB-derived feature must discriminate by post type and quality, not aggregate everything into a "sentiment score."

#### The Huang & Nolan result — and a genuine disagreement

**[EVIDENCE / CONTESTED]** Huang & Shum Nolan trained a machine-learning classifier on daily forum-wide and individual-stock sentiment. They found forum-wide sentiment is **inversely related to the VIX**, and that a monthly Attention Herding Portfolio generated sizable alphas. Notably, they report that **unlike Barber et al., they find no reversal in stock returns** following bullish attention herding.

That is a direct empirical conflict with the Robinhood herding literature (§10.4). Raven must present it as a conflict, not pick a side.

### 10.3 What does not work — the copy-the-forum strategies

**[EVIDENCE]**

- A daily-rebalanced long-short portfolio going long WSB "buy" suggestions and short "sell" suggestions, tested across holding periods from one day to one year over 2012 through Q1 2021, did not produce the alpha its premise implies.
- A separate academic study concluded that **neither an increase in post volume nor a more positive tone led to higher stock returns**, and argued that as WSB became a mass forum it acquired more noise and less informativeness.
- **The real-money test:** the VanEck Social Sentiment ETF (BUZZ), which tracked the 75 large-cap US stocks with the most bullish social-media perception, returned **−3.58% from April 2021 to March 2024**, versus **+11.58%** for Vanguard's S&P 500 ETF (VOO) — an underperformance of **15.16 percentage points**. This is the closest thing available to an out-of-sample, live-capital, fee-paying test of "trade the social sentiment," and it failed.

**The synthesis:** curated DD posts pre-2021 carried signal. Aggregate social sentiment, packaged and sold as a product, did not. The gap between those two statements is the entire practical content of this section.

### 10.4 The herding evidence — what happens when the crowd arrives

**[EVIDENCE — Barber, Huang, Odean & Schwarz, "Attention Induced Trading and Returns: Evidence from Robinhood Users"]**

Not WSB itself, but the same population and the closest thing to a clean measurement of what crowd buying does to subsequent returns. Using Robintrack data from 2 May 2018 to 13 August 2020 (user-stock positions grew from ~5 million to over 42 million):

- **Average 20-day abnormal returns of −4.7% for the top stocks purchased each day.**
- Five-day abnormal returns around **−3%**, and about **−6%** for the most extreme herding episodes.
- Herding episodes were linked to the **app's simplified display of information** — i.e., partly a UI artifact, not purely investor psychology.
- The negative returns were **not** simple inventory-based reversals, and **not** driven by the bid-ask spread — they persisted using quote midpoints.
- The average Robinhood user held about **three stocks**.
- The authors concluded that in aggregate, users establishing new positions during these episodes **incur losses**, while some individuals profit — and that other market participants appear to have watched the ownership data and traded against the flow.

**The structural point:** the crowd's arrival *is* the price spike. If a signal fires when the crowd is already visible, the move being predicted has already happened, and what remains is the reversal. This is not a WSB-specific finding; it is a general property of attention-driven flow.

### 10.5 GameStop, January 2021 — what the record actually shows

This is the founding myth, and the official finding contradicts the popular version — while itself being contested by academics.

**[RULE — SEC Staff Report on Equity and Options Market Structure Conditions in Early 2021, 18 October 2021]**

- The SEC staff concluded that **neither a short squeeze nor a gamma squeeze caused the price increase.**
- On the short squeeze: the run-up coincided with buying by short position holders, but that buying was **a small fraction of overall buy volume.** Staff concluded it was **the positive sentiment, not the buying-to-cover, that sustained the weeks-long appreciation.**
- On the gamma squeeze: options volume from individual customers rose enormously, but the increase was **mostly driven by buying of puts rather than calls**, and data showed **market makers were buying, rather than writing, call options.** Staff did not find evidence of a gamma squeeze in GME during January 2021.
- Staff identified a confluence of factors: large price moves, large volume changes, large short interest, frequent Reddit mentions, and heavy mainstream media coverage.
- The report also explained why reported short interest can mechanically exceed 100% of shares outstanding through re-lending of the same share, without requiring naked shorting.

**[CONTESTED — Ad Hoc Academic Committee on Equity and Options Market Structure Conditions in Early 2021 (Mitts, Battalio, Brogaard, Cain, Glosten, Kochuba)]**

A group of academics challenged both conclusions:
- Extending the sample and incorporating securities lending data, they found evidence that **a nontrivial fraction of GME trading volume consisted of short sellers covering.** They noted the SEC's method identified large short positions as of 15 January 2021, while anecdotal reports suggested large shorts were opened earlier, in December.
- Extending the gamma analysis to include delta-hedging by **both put and call** market makers, they found that a large volume of trading appeared to consist of options hedging — calling the "no gamma squeeze" conclusion into question.
- They were explicit about their limitation: **without the SEC's nonpublic, deanonymized data they could not pin down the magnitude** of either effect.

**How Raven should state this:** the SEC's own analysis attributes the GME move primarily to sentiment and attention rather than to forced short covering or dealer hedging; credentialed academics have published a substantive methodological challenge to both conclusions using public data; and neither side claims to have fully resolved it. **The one thing the record does not support is the confident retail narrative that a coordinated retail army mechanically forced a short squeeze.** It also does not support confidently asserting the opposite.

### 10.6 The exaggerated wins — why the visible record is unrepresentative

This is the part with the least peer-reviewed data and the clearest logic. Tag accordingly.

#### 10.6.1 The posting selection function

**[MATH — this part is arithmetic, not opinion]** WSB culture requires "positions or ban" — screenshots of the position. What gets posted is a **non-random sample of outcomes**, filtered by at least five layers:

1. **Voluntary disclosure.** Nobody is required to post an outcome. Posting is chosen.
2. **Extremity filter.** Ordinary outcomes generate no engagement. A +8% week is invisible; a +4,000% week goes to the front page. **The distribution of posted results is the tails of the true distribution with the middle deleted.**
3. **Upvote amplification.** Ranking algorithms sort by engagement, so the visible sample is the extreme tail of an already-extreme sample.
4. **Attrition.** Accounts that blow up stop posting. This is the same **survivorship bias** described in §11.1 for backtests, operating on people instead of securities. A trader's fifteenth account is indistinguishable from a new one.
5. **Fabrication.** Screenshots are trivially editable and unverifiable, and any promoter with a position has an incentive to manufacture credibility.

**The consequence, stated precisely:** the observable win/loss ratio on WSB is not an estimate of the population win/loss ratio. It carries **no information** about it. This is not a claim that the posted wins are fake — many are real. It is a claim that a sample selected on the outcome variable cannot be used to estimate the outcome variable's distribution.

#### 10.6.2 Loss porn is a selection effect too, and it cuts the other way

WSB does display losses prominently — this is genuinely unusual among trading communities and is a real point in its favor versus a typical guru selling a course. But loss porn is subject to the **same extremity filter**. A catastrophic, funny loss is content. A grinding 14% annual underperformance is not.

**The net effect is not "WSB hides losses." It is that WSB displays the two tails and deletes the middle** — which is where almost all real outcomes live. The result is a community whose visible evidence base makes trading look like a high-variance coin flip between life-changing wealth and total ruin, when the modal real outcome is quiet, unremarkable, cost-driven underperformance.

#### 10.6.3 The single-name case study problem

Individual famous outcomes — a large position that became enormous, or an inheritance destroyed and then vindicated by a later recovery — circulate as arguments. They are not arguments. **N=1 with outcome-based selection has no inferential content**, in either direction. Raven should decline to reason from them and should say why.

### 10.7 The losers — what the aggregate data shows

Now the population-level numbers, which are not selected on outcome.

#### 10.7.1 Retail options losses

**[EVIDENCE — and there is a real academic dispute here that must be reported]**

**The large-loss camp:**
- **Bryzgalova, Pavlova & Sikorskaya** estimated that the aggregate portfolio of retail options trades lost an average of **−$5.03 million per day**, totaling roughly **$2.1 billion** from November 2019 through June 2021, assuming all trades open new positions at a 10-day horizon. They found retail options traders experienced losses **at every trade horizon considered.** Because their OPRA data captures trades on all exchanges, this analysis is not limited to one venue or a self-selected group.
- Reported average gross monthly losses for retail options traders of around **1.81%**, characterized as economically large and statistically significant.
- **de Silva et al.** documented that retail investors take sizable net long option positions ahead of high expected-announcement-volatility earnings events, demand liquidity, appear to lack private information, and **suffer large losses as a result.** They link the preference for longer-dated options to realization utility — buying more time before having to realize a loss.
- **Naranjo, Nimalendran & Wu (2024)** concluded retail traders generally lose on complex (multi-leg) options trades.
- **Beckmeyer, Branger & Gayda (2023)** found retail traders lose money trading S&P 500 index options.
- A University of Florida study reported retail losses across every measured period on complex multi-leg options, averaging **−16.4% over three days.**

**The dissenting camp:**
- **Bogousslavsky & Muravyev** analyzed data from a social investing platform (~3,000 traders sharing trades) and found only **small** average losses on single-leg options trades. Their profitability estimates range from **−5% to +1% across subsamples**, and they found **naked option sales earn about 20% on average.** They argue that **concerns about large retail options losses may be overstated**, and attribute the divergence from the other studies to proxy limitations, differences in the unit of analysis, endogenous holding periods, and investor sophistication — noting that the "single-leg auction" proxy captures only spread-crossing market orders, and the "open-close" proxy lacks trade prices.

**What Raven should conclude:** the direction is consistent (retail loses on net, especially on long premium), but **the magnitude is genuinely disputed and depends heavily on the retail-identification proxy used.** Anyone quoting a single dramatic figure without naming the proxy is overstating the certainty of the literature. Note also the recurring sub-finding, present in both camps: **long option positions lose on average while short option positions do better** — consistent with the variance risk premium in §7.2.

#### 10.7.2 The 0DTE-specific numbers

**[EVIDENCE]** Research on 0DTE found that since the introduction of daily expirations, retail investors incurred aggregate losses of roughly **$350,000 per day**, totaling more than **$125 million** over the sample. The decomposition matters more than the headline: retail **debit** 0DTE orders lost about **−$8.05 per contract**, while retail **credit** orders were profitable even after fees. Retail in aggregate favors debit orders, so aggregate retail profits are negative.

**Caveat:** the source presenting this decomposition was also selling an e-book on profiting from 0DTE. Treat the decomposition as plausible and consistent with the VRP literature, but not as independently verified.

#### 10.7.3 The largest regulatory dataset available

**[EVIDENCE — SEBI (India) study, September 2024]** India's securities regulator analyzed individual trader P&L in the equity futures and options segment from April 2021 through March 2024 — a complete regulatory dataset, not a sample:

- **Aggregate individual trader losses exceeded ₹1.8 lakh crore** over the period.
- **Only 7.2% of individual traders were profitable over three years.**
- **Only about 1% earned profits exceeding ₹1 lakh after adjusting for transaction costs.**
- Proprietary traders and foreign portfolio investors booked profits over the same period.

This is a different market with different microstructure, and it should not be read directly onto US retail. But it is the largest, cleanest regulatory measurement of retail derivatives outcomes that exists, and its numbers sit squarely inside the range from the day-trading studies in §9.

#### 10.7.4 The benchmark gap

**[EVIDENCE / CONTESTED — DALBAR's methodology has been criticized in the academic literature; treat as directional]** DALBAR's 2025 Quantitative Analysis of Investor Behavior reported the average equity investor earned **16.54%** in 2024 against **25.02%** for the S&P 500 — an **848 basis point** shortfall, the second largest of the decade, in a strong bull market. DALBAR's "Guess Right Ratio" (how often investors correctly time entries and exits) fell to **25%**, tying a record low.

### 10.8 The gambling layer — the research nobody quotes

This is the part of the literature that is most consistently omitted from trading education, and it is well established.

#### 10.8.1 Trading and gambling overlap at the population level

**[EVIDENCE — systematic review, F1000Research]** The main finding of a systematic review of studies that fielded validated problem-gambling instruments (SOGS, CPGI, PGSI, or DSM-5) alongside financial trading data: **the proportion of problem gamblers is substantially higher among people who trade in financial markets than in the general population.**

**[EVIDENCE — latent class analysis, 1,429 Spanish adults aged 18–64, population-based panel with post-stratification weights]** Three distinct retail-investor subgroups emerged:

| Class | Share | Profile | PGSI ≥ 8 (problem gambling) |
|---|---|---|---|
| Crypto-traders | 52.4% | Crypto-focused, minimal gambling beyond lotteries | **0.5%** |
| Stock-traders | 32.0% | Stocks/ETFs plus lotteries; highest mean trade size | **3.5%** |
| Gambling-traders | 15.6% | High-risk trading plus broad gambling; highest trading frequency, shortest holds, most intensive monitoring | **24.9%** |

The authors' framing is precise and worth adopting: **not all traders gamble, but some gamblers trade.** The gambling-traders class was not the largest by volume, but scored highest on impulsivity, gambling-related cognitive biases, disordered trading, and illicit substance use.

#### 10.8.2 The substitution evidence — trading literally competes with gambling

**[EVIDENCE]** Several independent studies show trading and gambling are behavioral substitutes:
- **Dorn, Dorn & Sengmueller (2014)**: US and German individual investors trade **less actively** during weeks with large lottery jackpots (Powerball, Mega Millions).
- **Gao & Lin (2014)**: in Taiwan, trading volume in retail-preferred stocks **drops 5% to 10%** on days with large lottery jackpots.
- **Dorn & Sengmueller (2009)**: German investors who self-report enjoying gambling have significantly higher portfolio turnover.
- **Mosenhauer et al. (2021)**: in a survey of 795 US participants who both gambled and held stocks, self-reported relative portfolio turnover was **positively associated with problem gambling scores**, robust to controls for financial literacy, overconfidence, and demographics, and present **equally across all self-reported portfolio sizes.**
- **Recent work (2026)** documents a substitution effect between **options trading and sports betting**, and finds option attention significantly higher in gambling-prone US states, especially around salient events like earnings.

That last one has a direct implication: the growth of prediction markets and legal sports betting is competing for the same behavior and the same wallet. Prediction market platforms saw roughly **$60 billion** in volume in the first part of 2026, already surpassing the **$51 billion** for all of 2025, with one bank projecting $240 billion for 2026.

#### 10.8.3 The lottery-stock preference

**[EVIDENCE — Kumar (2009); Bali, Cakici & Whitelaw (2011); Han & Kumar (2013); Boyer & Vorkink (2014); and a large subsequent literature]** Individual investors display a documented preference for **lottery-like securities**: low price, high idiosyncratic volatility, high positive skew, small chance of a very large return. Retail option traders specifically prefer **short-dated, out-of-the-money contracts with wide spreads** — the most lottery-like structures available.

**These same characteristics predict poor average returns.** That is the core finding: the preference is measurable, and the assets it selects underperform. **Penny stocks, low-float runners, OTM 0DTE calls, and meme names are the same behavioral object wearing different tickers** — which ties this section directly to §3.5 and §6.4.

#### 10.8.4 The meme-asset specific finding

**[EVIDENCE — Philander (2023), *Addictive Behaviors*, n = 643]** Meme-asset owners, relative to non-owners:
- **Perceive less risk** from financial uncertainty
- Have **higher overconfidence** in their own investment ability
- Have **higher risk of gambling problems**

Perceived-risk scores declined monotonically with the number of meme assets held (no meme assets M = 27.5; one M = 25.5; two M = 24.9; three or more M = 24.4). The author's conclusion is that these products **may be treated like gambling by some individuals.**

Related experimental work found participants traded **more often in high-volatility markets**, robust to controls for financial literacy, overconfidence, age, and gender — volatility itself increases trading frequency.

#### 10.8.5 How to handle this without moralizing

This research is included because "unbiased" requires it, not to lecture. The operationally useful version, stripped of judgment:

- **The behavioral profile that produces gambling harm is the same profile that produces bad trading returns:** high frequency, short holds, intensive monitoring, preference for skewed payoffs, overconfidence, and loss chasing.
- Therefore **the risk controls that protect capital and the ones that protect the person are the same controls.** Position caps, loss limits, mandatory pauses after losing streaks, and prohibitions on discretionary override are simultaneously risk management and harm reduction. They are already in Quant's design.
- The distinction that matters is not "investing vs. gambling" as a moral category. It is **positive expectancy with bounded sizing vs. negative expectancy with escalating sizing.** A systematic strategy with a measured edge and a hard notional cap is on one side of that line. A discretionary trader increasing size after losses is on the other, regardless of instrument.
- **If Aym or anyone else ever asks Raven to relax a loss limit, remove a pause, or increase size following a losing streak, the correct response is to name that this is the documented escalation pattern and decline pending explicit out-of-session review.** That is not paternalism; it is the governance protocol working as designed.

### 10.9 What WSB looks like in 2026

**[EVIDENCE / PRACTICE — current-state reporting, verify before relying on]**

The community did not disappear, and its character has shifted:

- **Composition change.** The 2026 WSB "index" — ten stocks voted on by members — is reported as a mix of mega caps (Alphabet, Amazon, Tesla), retail-cult names (Palantir, Micron), space names (AST SpaceMobile, Rocket Lab), an AI-host bitcoin miner (IREN), a speculative growth name (Nebius), and Reddit itself. Reporting frames this as a **shift from short-term moon shots toward thematic bets** — though the underlying behavior is still concentrated, high-beta, and narrative-driven.
- **Squeeze mechanics persist.** In 2025, Opendoor (OPEN) rose **312% over six days** with options volume reaching **34 million contracts** on that ticker alone — reported as exceeding the volume seen during the original GME rally. In mid-2026, Wendy's surged more than 25% in a day on a WSB rally.
- **A WSB moderator's own characterization**, reported in June 2026, is that traders are more informed, respond to news and filings faster, size bets more reasonably, and show "fewer (but still some)" all-in positions. **[UNVERIFIED — this is a self-report from a community moderator, not a measurement.]**
- **A structural difference from 2021:** no stimulus flow. The 2021 episode was partly funded by pandemic transfer payments.
- The professional read is that participants now treat these as **short-duration momentum vehicles to be sold into strength**, rather than long-term holds — which, if accurate, means the behavior has become more explicitly speculative rather than less, just with faster exits.

**The important structural point for Quant:** the meme dynamic has **not** been confined to microcaps or OTC. It has appeared in exchange-listed large caps and in freshly IPO'd small-tier listings (§3.5). **Ticker-level screening on market cap or listing venue will not exclude it.**

### 10.10 If Quant ever builds a social-sentiment feature

Concrete requirements, derived from everything above:

1. **Discriminate by post type.** Aggregate mention counts were not significantly different from zero; curated DD posts were. Build the filter first.
2. **Assume decay, and test for it.** The Bradley et al. result is a documented, dated destruction of a real signal by popularity. Any social signal must be tested for structural break, not just fit over the full sample.
3. **Model the reversal.** −4.7% average 20-day abnormal returns after peak crowd buying is the base case for a signal that fires late. Establish whether the feature leads or lags the crowd; if it lags, it is a fade signal, not a follow signal.
4. **Never trade a name solely on social signal.** §3.5 applies in full: promotion is indistinguishable from organic enthusiasm from the outside, and an exchange listing is not a filter.
5. **Model the halts.** Meme moves trigger LULD repeatedly (§1.7.1) and SSR on the down leg (§1.7.3). A backtest without them is fiction.
6. **Model the borrow.** Fading a meme move requires shorting exactly the names that are hardest and most expensive to borrow (§2.5).
7. **Survivorship-audit the dataset.** Names that were suspended or delisted must be present in the historical data.
8. **Cap it.** Even if a social feature passes every gate, it is a lottery-payoff feature by construction, and the sizing must reflect that, not the backtested Sharpe.
9. **The live-performance bar should be higher than for a mechanical signal**, because the signal's own popularity degrades it — the more it works, the faster it stops working.

## 11. Backtesting and validation

### 11.1 Failure modes, ranked by how often they silently destroy a result

1. **Survivorship bias.** Datasets excluding delisted, acquired, bankrupt, and suspended companies inflate returns. **In the microcap universe this is catastrophic**, because failure and delisting are the modal outcomes.
2. **Look-ahead bias.** Using information unavailable at decision time. Most common forms: incomplete higher-timeframe bars (§5.11), same-day fundamental data (financials are known after the period, not during), index constituent lists applied retroactively, and any adjusted-price series where the adjustment factor was not known at the time.
3. **Under-modeled transaction costs.** Flat commissions with no spread, no market impact, no borrow cost, no fee-per-share. §1.9 covers the full stack.
4. **Overfitting / data snooping.** Testing many strategies or parameters and reporting the best. **Sullivan, Timmermann & White (1999)** established the bootstrap reality check as the standard correction for exactly this in technical trading rule evaluation. Any rejection graveyard needs to feed a multiple-comparison adjustment, not just a log.
5. **Unrealistic fill assumptions.** Assuming fills at the close, at the midpoint, or at limit prices without queue position. Assuming fills at all during halts.
6. **Regime dependence.** A strategy tested only 2010–2021 was tested almost entirely in a bull market with declining rates.
7. **Structural breaks in the data itself.** Decimalization (2001), Reg NMS (2007), the round-lot and tick-size changes (November 2025), T+1 (May 2024), the PDT repeal (June 2026), and the coming 23x5 transition all change the data-generating process. **Splicing across them without acknowledgment is a methodological error.**
8. **Ignoring corporate actions.** Splits, dividends, spin-offs, mergers, ticker changes.
9. **Repainting indicators** (§5.12).

### 11.2 Minimum standards

**[PRACTICE — and consistent with Quant's existing SPEC]**

- Sample size adequate for the strategy's win rate. Quant's ≥150 trades over 9 months is a reasonable floor; low-win-rate strategies need more.
- Out-of-sample and walk-forward validation with the parameter search confined to the in-sample window.
- Costs modeled per-symbol from actual quote data, never as a global constant.
- Explicit halt, SSR, and borrow logic for any equity strategy that shorts.
- Stress probes on defined historical regimes (2008, 2018 volatility spike, March 2020, 2022 rate shock, August 2024 unwind).
- Report maximum drawdown, tail loss, and longest losing streak alongside PF — never PF alone.
- Paper/shadow trading before capital, with a live-vs-backtest divergence check as an explicit gate. Quant's shadow gate (PF ≥ 1.1 over ≥20 signals in 2+ weeks) does this; **adding a slippage-divergence check — realized fill price vs. backtest assumed fill — would catch cost-model errors that PF alone hides.**

### 11.3 The insufficient-sample rule

Already in Quant's SOUL.md and worth restating here because it is the single most important honesty rule in this domain: **a metric computed on an inadequate sample is not a weak signal — it is no signal.** Report the sample size with every metric or do not report the metric.

---

## 12. US tax treatment — orientation only

**Not tax advice. Rules change; verify with a qualified CPA before any of this affects a decision.**

### 12.1 The two regimes

**[RULE]**

| | Securities (equity/ETF options, stocks) | Section 1256 contracts |
|---|---|---|
| What qualifies | Stocks, single-stock options, ETF options including **SPY and QQQ** | Regulated futures, broad-based **index** options: **SPX, NDX, RUT, XSP, VIX** |
| Tax rate | Short-term = ordinary income rates (up to 37%); long-term if held > 1 year | **60% long-term / 40% short-term regardless of holding period** |
| Wash sale rule | **Applies** (IRC §1091) | **Exempt** |
| Straddle rules | Apply (§1092) | Do not apply |
| Year-end treatment | Realized only | **Mandatory mark-to-market** on 31 December |
| Reporting | Schedule D / Form 8949 | **Form 6781** |
| Loss carryback | No | Net 1256 losses may be carried back 3 years against prior 1256 gains |

**The SPY vs. SPX difference is the largest single tax lever available to an active index options trader.** Same underlying economic exposure, materially different after-tax outcome, and it applies even to a 0DTE position held for five minutes.

### 12.2 The wash sale rule

**[RULE — IRC §1091]** A loss is disallowed if a substantially identical security is purchased within **30 days before or after** the loss sale (a 61-day window). The disallowed loss is added to the basis of the replacement position.

Two traps:
- **It applies across all of a taxpayer's accounts, including IRAs.** Selling at a loss in a taxable account and repurchasing in an IRA disallows the loss permanently — the basis adjustment is lost inside the IRA.
- It applies to options as well as stock, and the "substantially identical" analysis for options is not intuitive.

### 12.3 Trader Tax Status and the §475(f) election

**[RULE]** Traders who qualify for **Trader Tax Status (TTS)** may elect **mark-to-market accounting under IRC §475(f)**, which:
- Eliminates wash sale tracking on securities
- Removes the $3,000 annual capital loss limitation (losses become ordinary)
- Gives up long-term capital gains treatment
- **Must be elected by the tax filing deadline for the *prior* year — it cannot be made retroactively**

TTS itself is a subjective IRS facts-and-circumstances test requiring frequent, regular, and continuous trading with the primary intent of profiting from short-term price movement, and substantial time devoted to it. Sporadic trading does not qualify regardless of dollar volume. Part-time active trading frequently does not qualify.

**Section 1256 contracts are exempt from wash sale rules regardless of whether a §475(f) election is in effect.**

### 12.4 Options-specific items worth knowing

- **Qualified vs. unqualified covered calls:** writing a deep-ITM or short-dated call against stock approaching long-term status can **suspend the holding period**, converting a long-term gain into a short-term one. The tax cost can exceed the premium collected.
- **Protective puts** bought within 12 months of the stock purchase can similarly disrupt long-term treatment.
- Tax treatment differs depending on whether an option **expires, is closed, is exercised, or is assigned** — four different outcomes, four different treatments.

---

## 13. Glossary

**Adjusted option** — a contract whose deliverable was modified by a corporate action; non-standard, frequently mispriced by retail.
**Arrival price** — the market price at the moment a trading decision was made; the correct benchmark for Implementation Shortfall.
**ATS** — Alternative Trading System; a non-exchange execution venue.
**Assignment** — the obligation to fulfill a short option contract, allocated randomly by the OCC.
**Bid-ask spread** — the difference between the best bid and best offer; the primary implicit trading cost.
**BOLO** — Best Odd-Lot Order; a data element added by the 2024 Reg NMS amendments.
**Contango / backwardation** — upward- / downward-sloping forward or IV term structure.
**CEA** — Contrary Exercise Advice; instruction overriding default exercise behavior.
**DD** — "due diligence"; a WSB post format containing extended written analysis, distinct from a position screenshot.
**Delisting** — removal from an exchange; a major source of survivorship bias.
**Gain porn / loss porn** — WSB terms for screenshots of extreme profits or losses; both are extremity-filtered samples (§10.6).
**Gamma squeeze** — a price spiral driven by dealers delta-hedging short call exposure; the SEC found no evidence of one in GME during January 2021, a conclusion academics have contested.
**Exercise by exception** — the OCC's automatic exercise of options ITM by $0.01 or more, absent contrary instructions.
**Float** — shares actually available for trading; distinct from and often far smaller than shares outstanding.
**GFV** — Good Faith Violation; a cash-account settlement violation.
**Implementation Shortfall** — total cost of a trade measured against the arrival price.
**IML** — Intraday Margin Level, from the June 2026 Rule 4210 amendments.
**LULD** — Limit Up-Limit Down; the single-stock volatility pause mechanism.
**MWCB** — Market-Wide Circuit Breaker; 7%/13%/20% S&P 500 decline thresholds.
**NBBO** — National Best Bid and Offer.
**OCC** — Options Clearing Corporation; central counterparty for all US listed options.
**PFOF** — Payment for Order Flow; compensation paid to route retail orders to wholesalers.
**Meme stock** — a stock whose price is driven primarily by social narrative and attention rather than fundamentals; not confined to microcaps.
**PGSI** — Problem Gambling Severity Index; a validated screening instrument, with a score of 8 or above indicating problem gambling.
**Pin risk** — assignment uncertainty when the underlying settles at or near a short strike.
**POC** — Point of Control; the highest-volume price in a volume profile.
**R-multiple** — trade outcome expressed as a multiple of the amount risked.
**RVOL** — Relative Volume; current volume versus the time-of-day-adjusted average.
**Short squeeze** — forced buying by short sellers covering; per the SEC staff report, short covering was a small fraction of GME buy volume in January 2021.
**WSB** — r/wallstreetbets; the largest retail trading forum, founded 31 January 2012.
**SSR** — Short Sale Restriction; Reg SHO Rule 201, triggered by a 10% decline.
**T+1** — one-business-day settlement, standard since 28 May 2024.
**TWAQS** — Time-Weighted Average Quoted Spread; determines tick-size assignment under amended Rule 612.
**UIT** — Unit Investment Trust; SPY's and QQQ's legal structure, which prevents internal dividend reinvestment.
**VRP** — Variance Risk Premium; the persistent gap between implied and subsequently realized volatility.
**VWAP** — Volume Weighted Average Price; the institutional execution benchmark.
**Wash sale** — a loss disallowed by repurchase within the 61-day window under IRC §1091.

---

## 14. Sources

### Primary regulatory sources
- FINRA Regulatory Notice 26-10 (20 April 2026) — intraday margin standards replacing PDT: https://www.finra.org/rules-guidance/notices/26-10
- SEC Release No. 34-105226 (14 April 2026) — approval of FINRA Rule 4210 amendments: https://www.sec.gov/files/rules/sro/finra/2026/34-105226.pdf
- FINRA Regulatory Notice 24-13 (October 2024) — the retrospective review that preceded the change
- FINRA, "Guardrails for Market Volatility" — LULD and circuit breakers: https://www.finra.org/investors/insights/guardrails-market-volatility
- SEC Investor.gov, "Stock Market Circuit Breakers": https://www.investor.gov/introduction-investing/investing-basics/glossary/stock-market-circuit-breakers
- Nasdaq Trader, Short Sale Circuit Breaker (Reg SHO Rule 201): https://www.nasdaqtrader.com/trader.aspx?id=shortsalecircuitbreaker
- SEC adopting release, Reg NMS Rules 610/612 amendments (18 September 2024); compliance 3 November 2025
- SEC Investor Alerts: Social Media and Investment Fraud; Social Media and Stock Tip Scams (February 2026); Pump and Dump Schemes
- Exchange Act Rule 3a51-1 (penny stock definition); Rules 15g-1 through 15g-100; Rule 15c2-11 (amended, effective 28 September 2021)
- Options Clearing Corporation Rule 805 and Interpretation .02 (exercise by exception): https://www.optionseducation.org/referencelibrary/faq/options-exercise
- FTSE Russell, June 2026 US Indexes Reconstitution: https://www.lseg.com/en/media-centre/press-releases/ftse-russell/2026/ftse-russell-begins-june-2026-semi-annual-russell-us-indexes-reconstitution

### Academic literature
- Barber, B., Lee, Y-T., Liu, Y-J. & Odean, T. (2014). "The Cross-Section of Speculator Skill: Evidence from Day Trading."
- Chague, F., De-Losso, R. & Giovannetti, B. (2020). "Day Trading for a Living?"
- Marshall, B. R., Young, M. R. & Rose, L. C. (2006). "Candlestick technical trading strategies: Can they create value for investors?" *Journal of Banking & Finance* 30(8), 2303–2323.
- Marshall, B. R., Young, M. R. & Cahan, R. (2008). Candlestick analysis on the Tokyo Stock Exchange, 1975–2004.
- Tharavanij, P., Siraprapasiri, V. & Rajchamaha, K. (2017). "Profitability of Candlestick Charting Patterns in the Stock Exchange of Thailand." *SAGE Open.*
- Lu, T-H., Shiu, Y-M. & Liu, T-C. (2012). "Profitable candlestick trading strategies — The evidence from a new perspective."
- Lu, T-H. (2014). "The profitability of candlestick charting in the Taiwan stock market."
- Caginalp, G. & Laurent, H. (1998); Goo, Chen & Chang (2007); Shiu & Lu (2011); Fock et al. (2005); Horton (2009).
- Sullivan, R., Timmermann, A. & White, H. (1999). "Data-Snooping, Technical Trading Rule Performance, and the Bootstrap." *Journal of Finance.*
- Wood, McInish & Ord (1985); Harris (1986); Jain & Joh (1988); McInish & Wood (1990, 1992); Admati & Pfleiderer (1988) — intraday U-shape.
- Eaves & Williams (2010) — the dissenting L-shaped volatility finding.
- Ni, S. X., Pearson, N. D. & Poteshman, A. M. (2005). "Stock price clustering on option expiration dates." *Journal of Financial Economics.*
- Bakshi & Kapadia (2003); Carr & Wu (2009); Todorov (2010); Drechsler (2013); Bekaert & Hoerova (2014); Konstantinidi & Skiadopoulos (2016) — variance risk premium.
- Knuteson, B. "Strikingly Suspicious Overnight and Intraday Returns." arXiv:2010.01727
- "Structural Limits of OHLCV-Based Intraday Signals in MNQ Futures: A Systematic Falsification Study." arXiv:2605.04004 — the ORB results in §6.2.

### Social trading, WSB, and gambling literature (§10)
- Bradley, D., Hanousek, J., Jame, R. & Xiao, Z. (2024). "Place Your Bets? The Value of Investment Research on Reddit's Wallstreetbets." *Review of Financial Studies* 37(5), 1409–1459. https://doi.org/10.1093/rfs/hhad098
- Semenova, V., Gorduza, D., Wildi, W., Dong, X. & Zohren, S. (2024). "Wisdom of the Crowds or Ignorance of the Masses? A Data-Driven Guide to WallStreetBets." *Journal of Portfolio Management* 50(4), 88–106. Preprint: arXiv:2308.09485
- Semenova, V. & Winkler, J. (2021). "Reddit's Self-Organised Bull Runs: Social Contagion and Asset Prices." INET Oxford Working Paper 2021-04.
- Huang, C. & Shum Nolan, P. "Social Network Sentiment and Markets: Evidence from the Wallstreetbets Forum." SSRN 4384743.
- Barber, B. M., Huang, X., Odean, T. & Schwarz, C. "Attention Induced Trading and Returns: Evidence from Robinhood Users." SSRN 3715077.
- Boylston, C., Palacios, B., Tassev, P. & Bruckman, A. (2021). "WallStreetBets: Positions or Ban." arXiv:2101.12110
- Bryzgalova, S., Pavlova, A. & Sikorskaya, T. "Retail Trading in Options and the Rise of the Big Three Wholesalers." *Journal of Finance.*
- de Silva, T., et al. "Losing is Optional: Retail Option Trading and Expected Announcement Volatility." *Review of Finance* 30(2).
- Bogousslavsky, V. & Muravyev, D. "An Anatomy of Retail Option Trading" — the dissenting magnitude estimate.
- Naranjo, A., Nimalendran, M. & Wu, L. (2024) — complex options trades. Beckmeyer, H., Branger, N. & Gayda, L. (2023) — S&P 500 index options.
- Philander, K. S. (2023). "Meme asset wagering: Perceptions of risk, overconfidence, and gambling problems." *Addictive Behaviors* 137, 107532.
- "Not all traders gamble, but some gamblers trade: a latent class analysis of trading and gambling behaviors among retail investors." *Public Health* (2025). PMID 40373541.
- "Association between gambling and financial trading: A systematic review." *F1000Research* 12:111.
- Mosenhauer, M., Newall, P. W. S. & Walasek, L. (2021). "The stock market as a casino: Associations between stock market trading frequency and problem gambling." PMID 34587115.
- Newall, P. W. S. & Weiss-Cohen, L. (2022). "The Gamblification of Investing." *Int J Environ Res Public Health* 19(9), 5391.
- Kumar, A. (2009); Bali, T., Cakici, N. & Whitelaw, R. (2011); Han, B. & Kumar, A. (2013); Boyer, B. & Vorkink, K. (2014) — lottery-stock preference.
- Dorn, A. J., Dorn, D. & Sengmueller, P. (2014); Gao, X. & Lin, T-C. (2014); Dorn, D. & Sengmueller, P. (2009) — gambling/trading substitution.
- "Do retail traders gamble on stock options?" (2026) — regional gambling culture and options attention.
- Statman, M. (2002) — the shared psychology of stock traders and lottery players.

### Industry and market data
- Cboe Global Markets monthly volume reports and Q2 2026 investor presentation — 0DTE share data
- Cboe Insights (Mandy Xu), Macro Volatility Digest — SPX 0DTE share by month
- Bloomberg (January 2026), analysis of Nasdaq small-tier IPOs and WhatsApp promotion
- SEC Staff Report on Equity and Options Market Structure Conditions in Early 2021 (18 October 2021): https://www.sec.gov/files/staff-report-equity-options-market-struction-conditions-early-2021.pdf
- Mitts, J., Battalio, R., Brogaard, J., Cain, M., Glosten, L. & Kochuba, B. "A Report by the Ad Hoc Academic Committee on Equity and Options Market Structure Conditions in Early 2021." SSRN 4030179 — the academic challenge to the SEC report.
- SEBI (India) study on individual trader P&L in equity F&O, April 2021 – March 2024 (published September 2024)
- DALBAR Quantitative Analysis of Investor Behavior, 2025 edition (methodology contested in the academic literature)
- VanEck Social Sentiment ETF (BUZZ) performance via Portfolio Visualizer, April 2021 – March 2024
- OTC Markets Group tier standards — otcmarkets.com
- Fidelity, Schwab, E*TRADE — cash account violation rules and expiration process documentation
- Green Trader Tax; IRS Publication 550; IRC §§ 1091, 1092, 475(f), 1256

### Sources deliberately excluded
Vendor win-rate claims without published methodology (TraderLion, SpeedTrader, and similar), broker educational content presenting conventions as statistics, and any "success rate" figure lacking a sample size and cost assumption. These are flagged **[UNVERIFIED]** where mentioned and must not be cited as evidence.

---

## 15. Maintenance

### 15.1 Items with known expiry

| Item | Section | Status as of Aug 2026 | Re-verify |
|---|---|---|---|
| Broker implementation of intraday margin rule | 2.2 | Phased; deadline 20 Oct 2027 | Per broker, before any margin-dependent logic |
| Nasdaq Global Trading Hours / SIP 23x5 | 1.5 | Targeted 6 Dec 2026 | Quarterly |
| 24X, NYSE Arca, Cboe EDGX extended hours | 1.5 | Announced, pending | Quarterly |
| Tick-size assignments (TWAQS-based) | 1.3 | Periodically re-evaluated per stock | Per symbol, per evaluation period |
| ETF AUM and expense ratios | 3.6 | Moves constantly | Before any citation |
| 0DTE volume share | 7.3 | 66.2% as of July 2026 | Monthly from Cboe |
| Russell breakpoints | 3.1 | June 2026 reconstitution | Annually, each June |
| WSB composition and current behavior | 10.9 | Reported mid-2026 | Semi-annually; the 2026 shift is press reporting, not measurement |
| Prediction-market volume figures | 10.8.2 | ~$60B YTD 2026 | Quarterly |
| Tax rates and thresholds | 12 | 2026 | Annually |

### 15.2 What this document does not cover

Futures contract specifications and margin (relevant to Quant v3); fixed income; foreign exchange; crypto derivatives; international equity market structure; corporate fundamental analysis and valuation; macroeconomic analysis; and the mechanics of specific broker APIs including Alpaca. Each is a separate document.

### 15.3 Change log

| Version | Date | Change |
|---|---|---|
| 1.0 | 12 Aug 2026 | Initial compilation. Research-backed, sourced, confidence-tagged. |
| 1.1 | 12 Aug 2026 | Added §10 (social trading, r/wallstreetbets, and the gambling layer). Former §§10–14 renumbered to 11–15. Added WSB, retail-options-loss, and gambling-research sources; expanded glossary. |
