# Ticker Universe & Selection Protocol

**Companion to:** `trading-knowledge-base.md` v1.1, `strategy-lab-v1.md` v1.0
**Author:** Compiled for Aym Abdalla · reviews and extends Raven's draft universe
**Version:** 1.0
**Compiled:** 12 August 2026
**Purpose:** Define what Quant trades, how a ticker earns a slot, how it loses one, and how transient names get temporarily looped in.

---

## 0. The one structural change I would make

Raven's draft is a good list. It is not yet a **selection protocol**, and that difference matters more than any individual ticker on it.

The draft is a hand-curated snapshot of what is prominent in August 2026. That has three problems that compound over time:

1. **It cannot be audited.** There is no stated rule that puts AMAT in and leaves out MRVL. Without a rule, disagreements about the list are matters of taste, and future-Raven cannot reproduce or defend the choice.
2. **It is survivorship-biased by construction.** A list of "what's hot now" is a list of the last five years' winners. NVDA, PLTR, and MSTR are on it because they won. The 2021 version of this list would have had PTON, ZM, and BBBY in the speculative bucket. **Backtesting a strategy on today's winners and concluding it works is the single most common way to fool yourself** (KB §11.1).
3. **It decays silently.** In eighteen months a chunk of this list will be stale, and nothing in the current design will notice.

**The fix:** make the list an **output** of stated criteria, re-derived on a schedule, with the hand-picks confined to an explicit, small, justified override list. Then the universe maintains itself, the survivorship bias becomes measurable, and every membership decision has a reason attached.

Everything in §3 exists to make that possible. The ticker list in §4 is what those criteria produce today, not a permanent artifact.

---

## 1. Scope and framing

**Primary purpose:** research substrate. This universe exists to backtest strategies, build a database of what works where, and establish a foundation. Capital constraints and notional sizing are deliberately out of scope — those become relevant at promotion, not at research.

**Consequence of that framing:** the universe should be **wider than a trading universe would be**, because breadth improves inference, and narrower filters can always be applied at promotion time. Price ceilings, fractional-share limits, and notional caps are **promotion-stage filters**, not research-stage filters. Do not let them shape this list.

**Two lanes, one system:**

| | Core lane | Surprise lane |
|---|---|---|
| Membership | Persistent, criteria-derived | Transient, event-triggered |
| Strategy fitting | Per-ticker curated and re-fit | Generic, trigger-matched |
| Data depth | Full fingerprint (§5) | Partial; often no long history |
| Admission | Quarterly re-derivation | Continuous scanner |
| Typical tenure | Years | 5–40 sessions |
| Failure mode | Silent staleness | Chasing noise |

They are not two lists. They are one pipeline with a **graduation path** (surprise → core) and a **demotion path** (core → bench → out). §7.5 defines both.

---

## 2. Audit of the draft universe

Run against the list as given. These are factual corrections, not preferences.

### 2.1 Counting

- **157 slots, 156 unique tickers.** TSLA appears twice — Consumer Discretionary and EV/Automotive. Deduplicate, and pick one home (EV/Automotive is the more informative classification for strategy routing).

### 2.2 Misclassifications

| Ticker | Listed under | Correct GICS |
|---|---|---|
| **TMO** (Thermo Fisher) | Telecom/Media | **Health Care** — Life Sciences Tools & Services. Clear error. |
| **WMT** | Consumer Discretionary | **Consumer Staples** — reclassified in the March 2023 GICS restructure |
| **COST** | Consumer Discretionary | **Consumer Staples** — same reclassification |

These matter because sector classification drives the "dominant in its sector" metric, the correlation clustering in §6, and sector exposure caps. Getting them wrong corrupts all three.

### 2.3 Redundancy — the list is smaller than it looks

**Same-index duplicates:** SPY, VOO, and VTI are all essentially US large-cap beta. SPY and VOO track the *identical* index. Add UPRO, SPXU, and ES=F and you have **six instruments expressing one factor.** That is fine for testing instrument-specific microstructure (SPY vs VOO spread behavior is a real, interesting difference) but it is **not six independent tests of a strategy.**

**Inverse pairs:** TQQQ/SQQQ, SOXL/SOXS, TNA/TZA, UPRO/SPXU, LABU/LABD — all five pairs are present. Each pair is **one factor and its mirror**, not two tickers. A long-only strategy tested on both TQQQ and SQQQ that "works on both" is almost certainly picking up something pathological.

**Sector ETF / constituent overlap:** XLK is roughly 40%+ AAPL, MSFT, NVDA. Testing a strategy on XLK, then on AAPL, MSFT, and NVDA, and counting four independent confirmations is a mistake. §6 handles this properly.

### 2.4 Gaps

**Missing sector ETF:** the draft has 10 Select Sector SPDRs. There are 11. **XLC (Communication Services)** is missing — and it holds GOOGL, META, NFLX, DIS, and CMCSA, five names that *are* on the list. That is a real hole.

**Missing names that meet the draft's own stated criteria** ("top volume per sector," "hot/speculative," "where WSB-style momentum happens"):

| Missing | Why it belongs |
|---|---|
| **HOOD** | Retail brokerage. Enormous volume, and it is structurally *the* retail-flow proxy — relevant to D5 and S4 in the strategy lab |
| **RDDT** | Reddit itself. The venue where the WSB dynamic happens, with high volatility and heavy options |
| **SMCI** | AI infrastructure, extreme realized volatility, top-tier volume, and a documented accounting/halt history that makes it a genuinely useful test case |
| **ARM, MRVL** | AI semis with deep options; the semi bucket is NVDA/AMD/INTC/MU-heavy and misses these |
| **UBER, ABNB, DASH** | Top-decile volume consumer tech, deep options, none represented |
| **PYPL, XYZ (Block)** | Payments beyond V/MA — different beta, different retail profile |
| **PDD, JD** | China e-commerce; BABA alone under-represents the ADR cluster |
| **AAL / DAL / UAL** or **JETS** | Airlines are a persistent high-retail-volume, high-beta cluster with zero representation |
| **XLC** | The missing sector ETF |
| **SMH** | Semiconductor ETF — arguably a better semis signal than SOXL, since it is unlevered |
| **IBIT** | Spot bitcoin ETF. The crypto-equity bridge that now matters more than MARA/RIOT |
| **GDX** | Gold miners — the equity-commodity bridge; GLD alone misses the levered miner response |
| **KRE** | Regional banks. A dormant cluster that becomes the entire market during a banking event |
| **IBB** or **XBI** | Biotech ETF to complement the five individual biotechs; XBI is equal-weight and the better volatility proxy |
| **VIX** (index, not ETP) | You have UVXY and VXX, which are decaying futures products. You need the **index itself** as a signal input for O1, O4, and the regime gates |
| **SPX / XSP** | Cash-settled, European, Section 1256 index options. The strategy lab's O1, O3, and O5 all specify SPX for good reason (KB §12) |

### 2.5 Execution-reality mismatches (flag, do not necessarily fix)

- **Futures (ES=F, NQ=F, etc.) are not tradeable on Alpaca.** These are v3 per the roadmap. Keep them as *data and signal* sources — ES weekend behavior is a required control for strategy C3 — but tag them clearly as non-executable in v2 so nothing accidentally routes an order there.
- **Crypto tickers are in Yahoo format** (BTC-USD). v1 executes Binance.US USDT pairs. Maintain an explicit symbol-mapping table; a silent mismatch between backtest symbol and execution symbol is exactly the kind of bug that survives review.
- **ADRs** (TSM, BABA, SE, NIO, XPEV, and PDD/JD if added) trade on foreign-market news overnight and gap on schedules US names do not. Tag them; several strategies that assume US-session information flow will behave differently on ADRs, and that is a finding, not a bug.

### 2.6 The dilution/fraud cluster

**MULN, SNDL, NVAX** sit in a category the knowledge base flags hard (KB §3.5, §6.4). MULN specifically has a reverse-split and dilutive-financing history that makes historical price series nearly meaningless unless perfectly adjusted, and it is the archetype of the name whose backtest looks spectacular and whose live execution is impossible.

**Recommendation:** keep them, but in a **quarantined research tier** with a mandatory flag: any strategy result on these names must be reported separately and never pooled into aggregate statistics. They are useful precisely as a stress test — if a strategy "works" on MULN, that is evidence the backtest harness is broken, not that the strategy is good. **Treat them as a canary, not as an opportunity.**

---
## 3. Selection criteria — the admission function

This is the section that turns a list into a protocol. A ticker is in the core universe if and only if it passes the hard gates and scores into a tier.

### 3.1 Hard gates (all must pass)

Evaluated on trailing 60 sessions. A ticker failing any gate is ineligible regardless of anything else.

| Gate | Threshold | Rationale |
|---|---|---|
| Listing venue | NYSE, Nasdaq, NYSE American, Cboe. **No OTC, no Expert Market** | KB §3.4 — tier is a hard fill-risk gate, and Alpaca does not support OTC anyway |
| Price | ≥ $2.00 | Below this, spread as a share of price dominates everything |
| Median daily dollar volume | ≥ $20M | The single most important gate. Everything downstream assumes fills exist |
| Median quoted spread | ≤ 25 bps | Above this, most intraday strategies are dead before they start (KB §1.9) |
| Trading history | ≥ 250 sessions | Below this it goes to the surprise lane, not core |
| Corporate status | No pending delisting notice, no announced fixed-cash acquisition | Dead money and terminal price series |
| Split hygiene | No reverse split in trailing 180 sessions | Reverse splits mark the dilution-machine cluster |

**Deliberately absent:** market cap floor, price ceiling, options requirement. Market cap is a poor liquidity proxy (float matters more). A price ceiling is a promotion-stage concern, not research. Options depth is a *tier* criterion, not a gate — plenty of useful equity strategies need no options.

### 3.2 The sector-dominance metric

The draft asserts "top volume per sector." That needs a definition, because share volume, dollar volume, and options volume produce three different rankings, and dollar volume is the only one that means anything across price levels.

**Definition.** For each ticker `i` in GICS sub-industry `s`:

```
DollarVol(i)      = median(close × volume) over 60 sessions
SectorMedian(s)   = median DollarVol across all eligible tickers in s
RelativeVolume(i) = DollarVol(i) / SectorMedian(s)
SectorRank(i)     = rank of DollarVol(i) within s, descending
```

**A ticker is "dominant in its sector" if `SectorRank ≤ 5` OR `RelativeVolume ≥ 4.0`.**

Two conditions rather than one, because sub-industries differ wildly in size. In a 60-name sub-industry, rank-5 is genuinely dominant. In a 6-name sub-industry, rank-5 means almost nothing, and the relative-volume test is what catches actual dominance.

**Use GICS sub-industry (level 4), not sector (level 1).** "Technology" is not a sector for this purpose — it contains semiconductors, software, and hardware, which have completely different volatility, options depth, and news cadence. Raven's draft groups 17 names under "Technology," which flattens exactly the distinctions strategy routing depends on.

### 3.3 Character coverage requirements

Liquidity alone produces a universe of 100 mega-caps that all behave the same way. The universe must **deliberately span behavioral character**, because that is what makes cross-sectional strategy inference possible.

Minimum coverage targets (measured on the fingerprint in §5):

| Dimension | Requirement |
|---|---|
| Mean-reverting vs. trending | ≥ 20 tickers with variance ratio < 0.9, ≥ 20 with VR > 1.1 |
| Volatility | ≥ 15 tickers with daily ATR% > 4%, ≥ 15 with ATR% < 1.5% |
| Gap propensity | ≥ 15 tickers gapping >1% on more than 25% of sessions |
| Options depth | ≥ 40 tickers with weekly expirations and 25Δ spreads under 5% of mid |
| Short availability | ≥ 30 easy-to-borrow, ≥ 10 persistently hard-to-borrow (for the short-constraint strategies) |
| Halt exposure | ≥ 10 tickers with 3+ LULD halts in the trailing year (D3 needs a population) |
| Retail concentration | ≥ 15 tickers in the top decile of odd-lot trade share |
| Sector | ≥ 4 tickers in every GICS level-1 sector, all 11 |

**If a coverage target is unmet, that is a reason to admit a ticker that would not otherwise rank** — and that admission gets logged with the coverage gap as its stated reason. That is the legitimate override mechanism, and it replaces hand-picking with documented purpose.

### 3.4 Structural instrument rules

Leveraged, inverse, and volatility products are not stocks and must not be admitted under equity criteria.

| Rule | Detail |
|---|---|
| **Pair collapse** | For each leveraged/inverse pair, designate **one primary** for testing; the inverse is a *validation mirror only*. A strategy that works on TQQQ should show the mirrored result on SQQQ. If it works on both in the same direction, the harness is broken. Primaries: **TQQQ, SOXL, TNA, UPRO, LABU.** |
| **Daily-reset modeling required** | Any holding period > 1 session must simulate the daily rebalance explicitly. Applying a 3× multiplier to a return series and compounding is wrong (KB §3.7) |
| **Vol products flagged decaying** | UVXY and VXX hold futures, not spot VIX; roll cost is persistent and structural. Never test a long-hold strategy on them without modeling roll |
| **VIX index is signal-only** | Not tradeable. Used as input to O1, O4, and regime gates |
| **Max universe share** | Structural instruments capped at 15% of the core universe, so aggregate results are not dominated by products with engineered return distributions |

### 3.5 Eviction criteria

A core ticker is demoted to bench when **any** hold for 60 consecutive sessions:

- Median dollar volume falls below $15M (25% below the admission gate — a deliberate hysteresis band so names do not flap in and out)
- Median spread exceeds 35 bps
- It fails its sector-dominance test and no coverage requirement depends on it
- A hard gate is newly violated (delisting notice, reverse split, acquisition)

**Demotion is not deletion.** Bench tickers keep their fingerprint and their historical strategy results. The database of what worked where is the actual asset here — losing history to keep the list tidy would be the worst possible trade.

---

## 4. The core universe

What §3 produces today, with the §2 corrections applied. Tiers determine research priority and strategy-fitting depth, not importance.

### 4.1 Tier definitions

| Tier | Name | Treatment | Count |
|---|---|---|---|
| **T0** | Benchmarks & index instruments | Deepest fitting; every strategy tested here first | 8 |
| **T1** | Anchors | Mega-cap, deepest options, tightest spreads. Per-ticker curated strategies | 22 |
| **T2** | Sector representatives | Per-ticker fitting at lower cadence | 58 |
| **T3** | Character names | High-beta, retail-heavy, event-prone. Where momentum/squeeze strategies get tested | 30 |
| **T4** | Structural instruments | Special modeling rules per §3.4 | 24 |
| **T5** | Non-equity signal & bench | Crypto, futures, quarantine | 16 |

### 4.2 T0 — Benchmarks and index instruments

`SPY` `QQQ` `IWM` `DIA` `VTI` `SPX` `XSP` `VIX`

- **SPY, QQQ, IWM** — the three primaries. Every strategy in the lab gets tested here before anywhere else.
- **VOO dropped.** Identical index to SPY with thinner options; it adds no research information. If you want the SPY-vs-VOO microstructure comparison later, re-add it as a targeted study rather than a universe member.
- **VTI** retained for the small-cap tail relative to SPY.
- **SPX / XSP** — cash-settled, European, Section 1256. Required for O1, O3, O5.
- **VIX** — signal only, not tradeable.

### 4.3 T1 — Anchors (22)

`AAPL` `MSFT` `NVDA` `AMZN` `GOOGL` `META` `TSLA` `AVGO` `AMD` `ORCL` `NFLX` `JPM` `V` `MA` `LLY` `UNH` `XOM` `WMT` `COST` `HD` `TSM` `CRM`

Criteria: top-decile dollar volume, weekly and daily options, sub-10 bps spreads, easy borrow, continuous coverage.

### 4.4 T2 — Sector representatives (58)

Organized by **GICS level-1 sector**, with sub-industry noted where it drives routing. Corrections from §2.2 applied.

| Sector | Tickers |
|---|---|
| Information Technology | `INTC` `MU` `QCOM` `ADBE` `CSCO` `AMAT` `LRCX` `MRVL` `ARM` `SMH` |
| Communication Services | `DIS` `CMCSA` `T` `VZ` `XLC` |
| Consumer Discretionary | `LOW` `NKE` `SBUX` `F` `GM` `UBER` `ABNB` `DASH` `EBAY` |
| Consumer Staples | `PG` `KO` `PEP` `MO` `PM` `MDLZ` |
| Financials | `BAC` `WFC` `GS` `MS` `C` `PYPL` `XYZ` `KRE` |
| Health Care | `JNJ` `PFE` `ABBV` `MRK` `BMY` `TMO` `AMGN` `GILD` `REGN` `XBI` |
| Energy | `CVX` `COP` `SLB` `EOG` `PSX` |
| Industrials | `BA` `CAT` `GE` `MMM` `HON` `UPS` `RTX` `LMT` `JETS` |
| Utilities | `NEE` `DUK` `SO` `D` `AEP` |
| Real Estate | `AMT` `PLD` `EQIX` `SPG` |
| Materials | `LIN` `APD` `SHW` `FCX` `NEM` |

**Changes from draft:** TMO moved to Health Care; WMT and COST moved to Consumer Staples and promoted to T1; XLC added; MRVL, ARM, SMH, UBER, ABNB, DASH, PYPL, XYZ, KRE, XBI, JETS added to fill coverage gaps.

### 4.5 T3 — Character names (30)

The high-beta, retail-concentrated, event-prone bucket. **This is where the momentum, squeeze, attention-decay, and second-day strategies get their sample** — T1 and T2 will not produce enough qualifying signals for them.

| Cluster | Tickers |
|---|---|
| Retail-flow proxies | `HOOD` `RDDT` `PLTR` `SOFI` `RBLX` `DUOL` |
| Crypto-equity bridge | `COIN` `MSTR` `MARA` `RIOT` `CLSK` `IBIT` |
| AI infrastructure / high-beta semis | `SMCI` `VRT` |
| EV & mobility | `RIVN` `LCID` `NIO` `XPEV` |
| China ADRs | `BABA` `PDD` `JD` `SE` `KWEB` |
| Biotech binary-event | `MRNA` `BNTX` `NVAX` |
| Growth / high short interest | `SHOP` `CVNA` `AFRM` |
| Precious-metal equity beta | `GDX` |

### 4.6 T4 — Structural instruments (24)

| Group | Primaries (test here) | Mirrors (validate only) |
|---|---|---|
| Leveraged / inverse | `TQQQ` `SOXL` `TNA` `UPRO` `LABU` | `SQQQ` `SOXS` `TZA` `SPXU` `LABD` |
| Sector SPDRs | `XLK` `XLF` `XLE` `XLV` `XLY` `XLP` `XLB` `XLRE` `XLU` `XLI` (XLC in T2) | — |
| Commodity | `GLD` `SLV` `USO` `UNG` | `CORN` `WEAT` (thin — bench, see note) |
| Bond | `TLT` `IEF` `HYG` `LQD` | — |
| Volatility ETPs | `UVXY` `VXX` | — |

**Note on CORN and WEAT:** both are thin enough that they will fail the $20M dollar-volume gate on most days. Move to bench unless you specifically want an agricultural test case. They will otherwise pollute aggregate liquidity statistics.

### 4.7 T5 — Non-equity signal and quarantine (16)

| Group | Symbols | Status |
|---|---|---|
| Crypto (v1 live) | `BTC-USD` `ETH-USD` `SOL-USD` | Executable on Binance.US as USDT pairs — maintain symbol map |
| Equity index futures | `ES=F` `NQ=F` `YM=F` `RTY=F` | **Signal and data only.** Not executable in v2. Required for C3's futures-residual control |
| Commodity / rate futures | `CL=F` `GC=F` `NG=F` `ZB=F` | Signal and data only |
| **Quarantine** | `MULN` `SNDL` | **Flagged. Results never pooled.** Canary for harness bugs per §2.6 |

### 4.8 Roll-up

| Tier | Count |
|---|---|
| T0 | 8 |
| T1 | 22 |
| T2 | 58 |
| T3 | 30 |
| T4 | 24 |
| T5 | 16 |
| **Total** | **158** |

Roughly the same size as the draft, with the errors fixed, the coverage gaps filled, and — importantly — **every membership now traceable to a criterion in §3.**

---
## 5. Ticker fingerprinting

This is the bridge between the ticker list and the strategy library, and it is the part that makes "curated per-ticker strategies" principled rather than brute-force.

**The problem with brute force:** 24 strategies × 158 tickers = 3,792 backtests. At p<0.05, roughly **190 will look significant by chance alone.** You cannot correct your way out of that with statistics alone — you need a *prior* that narrows which strategies get tested on which tickers.

**The fingerprint is that prior.** Characterize the ticker first, then route only the strategies whose assumptions the ticker actually satisfies. This cuts the test count by roughly 70% and, more importantly, makes each remaining test a real hypothesis rather than a lottery ticket.

### 5.1 The fingerprint schema

Computed per ticker on a rolling basis, refreshed monthly. Every field is mechanical.

**Liquidity block**
| Field | Definition |
|---|---|
| `dollar_vol_60d` | Median close × volume, 60 sessions |
| `spread_bps_median` | Time-weighted average quoted spread, in bps |
| `tick_constrained` | TWAQS ≤ 1.5¢ → half-penny quoting (KB §1.3) |
| `depth_at_touch` | Median displayed size at NBBO |
| `close_auction_share` | Closing auction volume ÷ total daily volume |

**Volatility block**
| Field | Definition |
|---|---|
| `atr_pct_14` | ATR(14) ÷ price |
| `rvol_20 / rvol_60 / rvol_252` | Realized volatility at three horizons |
| `vol_of_vol` | Std dev of 20-day realized vol over 252 sessions |
| `downside_ratio` | Downside deviation ÷ total deviation (asymmetry) |

**Regime character block — the most important block**
| Field | Definition | Routing meaning |
|---|---|---|
| `variance_ratio_2 / _5 / _10` | Lo-MacKinlay variance ratio at lags 2, 5, 10 | **< 0.9 = mean-reverting; > 1.1 = trending.** The single best strategy-routing variable available |
| `hurst` | Hurst exponent, daily closes | Corroborates VR; < 0.5 anti-persistent, > 0.5 persistent |
| `adx_median` | Median ADX(14) over 252 sessions | Trend-strength baseline |
| `pct_days_above_20sma` | Trend persistence proxy | |
| `autocorr_1 / _5` | Return autocorrelation at 1 and 5 days | Direct reversal measure |

**Intraday shape block**
| Field | Definition |
|---|---|
| `volume_curve` | 13 values — share of daily volume in each 30-min bucket. Quantifies U-shape strength (KB §6.1) |
| `range_formed_by_1030` | Share of the daily high-low range established by 10:30 ET |
| `vwap_touch_rate` | Share of sessions where price touches VWAP after 11:00 |
| `or_break_hold_rate` | Share of opening-range breaks that hold to the close — directly gates D2 |

**Gap block**
| Field | Definition |
|---|---|
| `gap_freq_1pct` | Share of sessions gapping >1% |
| `gap_mean_abs` | Mean absolute overnight gap |
| `gap_fill_rate_same_day` | Share of gaps fully retraced intraday |
| `overnight_return_share` | Overnight return ÷ total return over 252 sessions — tests the Knuteson split (KB §6.1) on this specific name |

**Event block**
| Field | Definition |
|---|---|
| `earnings_move_mean_abs` | Mean absolute earnings-day move, last 12 quarters |
| `earnings_move_vs_implied` | Realized ÷ implied move ratio — feeds O3 directly |
| `halt_count_252d` | LULD halts in trailing year — gates D3 eligibility |
| `ssr_trigger_count_252d` | Rule 201 triggers — gates D4 |
| `news_density` | 8-K filings per quarter |

**Options block**
| Field | Definition |
|---|---|
| `opt_volume_20d`, `opt_oi_total` | Depth |
| `opt_spread_pct_25d` | 25-delta spread as % of mid — the real tradeability test |
| `has_weeklies`, `has_dailies` | Expiration granularity |
| `iv_rank_median`, `skew_25d_median` | Surface character |
| `section_1256` | Boolean — tax treatment (KB §12) |

**Short-side block**
| Field | Definition |
|---|---|
| `short_interest_pct_float` | |
| `days_to_cover` | |
| `borrow_rate_median`, `borrow_rate_p95` | |
| `etb_flag` | Easy-to-borrow availability rate |
| **Any short strategy is ineligible on a ticker where `etb_flag` < 0.8** | Hard rule |

**Correlation block**
| Field | Definition |
|---|---|
| `beta_spy`, `beta_sector_etf` | |
| `idio_vol_share` | Residual vol ÷ total vol — how much is stock-specific |
| `cluster_id` | Assigned by §6 |

**Retail-flow block**
| Field | Definition |
|---|---|
| `odd_lot_share` | Odd-lot trade count ÷ total trade count, time-of-day normalized. Feeds D5 |
| `social_mention_baseline` | 30-day median, for surprise-lane thresholding |

**Hygiene block**
| Field | Definition |
|---|---|
| `split_history`, `ticker_change_history` | |
| `data_gaps` | Sessions with missing or suspect data |
| `first_trade_date` | |
| `adr_flag`, `foreign_hours_flag` | Overnight information regime differs |

### 5.2 Strategy routing rules

The fingerprint decides which strategies are even *tested* on a ticker. Explicit rules:

| Condition | Routing |
|---|---|
| `variance_ratio_5 < 0.9` | Eligible: D1, mean-reversion swing, VWAP-fade. **Blocked:** breakout/trend |
| `variance_ratio_5 > 1.1` | Eligible: S6, trend, breakout, D2-continuation. **Blocked:** naive fade |
| `0.9 ≤ VR ≤ 1.1` | Test both; this is the ambiguous band and results here are the most informative about the routing rule itself |
| `atr_pct_14 < 1.5%` | Blocked from all intraday strategies — the move does not clear the spread |
| `or_break_hold_rate < 0.35` | **Eligible for D2 (failed-breakout fade).** This field alone should identify D2's best universe |
| `halt_count_252d ≥ 3` | Eligible for D3. Also **flagged for mandatory halt simulation** in every other strategy |
| `ssr_trigger_count_252d ≥ 2` | Eligible for D4 |
| `etb_flag < 0.8` | All short strategies blocked; substitute defined-risk put spreads if options qualify |
| `opt_spread_pct_25d > 8%` | All options strategies blocked |
| `earnings_move_mean_abs > 6%` | Eligible for O3, S3 |
| `odd_lot_share` top decile | Eligible for D5, S4 |
| `gap_freq_1pct > 0.25` | Blocked from strategies using overnight stops; eligible for gap strategies |
| `overnight_return_share > 0.8` | Flagged for the overnight/intraday split study |
| `adr_flag = true` | Flagged — overnight information regime differs; results reported separately |

**Build the fingerprint before fitting a single strategy.** It is the highest-leverage engineering task in the v2 build, and it converts an unmanageable 3,792-test brute force into a few hundred motivated hypotheses.

---

## 6. Correlation and redundancy management

### 6.1 158 tickers is not 158 independent tests

This is the statistical issue that will most distort results if ignored. AAPL, MSFT, NVDA, QQQ, XLK, TQQQ, and SOXL are, in a stressed market, close to **one position and one test**. A strategy showing positive results across all seven has produced roughly **one** piece of evidence, not seven.

**Required computation:** cluster the universe on the correlation matrix of daily returns (252-session rolling, Ward linkage or similar), and derive an **effective number of independent bets**:

```
N_eff = (Σλᵢ)² / Σλᵢ²        (participation ratio of correlation-matrix eigenvalues)
```

My expectation before you run it: **N_eff will land somewhere around 20–30**, not 158. That number, not the ticker count, is what belongs in the denominator of every multiple-comparison correction.

### 6.2 Expected clusters

Provisional — replace with the computed version:

| Cluster | Members |
|---|---|
| Mega-cap tech / AI | AAPL MSFT NVDA GOOGL META AVGO AMD ORCL CRM QQQ XLK TQQQ |
| Semis | NVDA AMD INTC MU QCOM AMAT LRCX MRVL ARM TSM SMH SOXL |
| Broad beta | SPY VTI SPX ES=F UPRO |
| Small-cap beta | IWM TNA RTY=F |
| Financials | JPM BAC WFC GS MS C KRE XLF |
| Payments | V MA PYPL XYZ |
| Energy | XOM CVX COP SLB EOG PSX XLE USO CL=F |
| Healthcare defensive | JNJ PFE MRK ABBV BMY LLY UNH XLV |
| Biotech beta | MRNA BNTX NVAX XBI REGN AMGN GILD LABU |
| Staples defensive | PG KO PEP MO PM MDLZ WMT COST XLP |
| Rate-sensitive | TLT IEF LQD HYG XLU XLRE NEE DUK SO D AEP AMT PLD EQIX SPG ZB=F |
| Crypto complex | BTC ETH SOL COIN MSTR MARA RIOT CLSK IBIT |
| Retail-flow / high-beta speculative | HOOD RDDT PLTR SOFI RBLX RIVN LCID CVNA AFRM SMCI |
| China ADR | BABA PDD JD SE NIO XPEV KWEB |
| Precious metals | GLD SLV NEM GDX GC=F |
| Volatility | VIX UVXY VXX (inverse-correlated to broad beta) |

**Note the crypto cluster.** COIN, MSTR, MARA, RIOT, CLSK, and IBIT are largely a leveraged expression of BTC. Since v1 already trades BTC directly, **the crypto-equity bucket adds far less diversification than its ticker count implies** — and a v1+v2 combined book could be far more concentrated in one factor than the position count suggests (KB §8.5).

### 6.3 Rules that follow

1. **Report strategy results per cluster, not just per ticker.** "Works on 12 tickers" means nothing if all 12 are in one cluster.
2. **Use `N_eff` in multiple-comparison corrections**, not the ticker count.
3. **Cap live concurrent positions per cluster**, not just per ticker.
4. **A strategy that works on exactly one cluster is a sector bet.** That may be fine, but it must be labeled as such rather than presented as a general strategy.
5. **Inverse-pair validation:** a strategy tested on TQQQ must produce the *mirrored* result on SQQQ. Matching results in the same direction indicate a harness bug — build this as an automated assertion.

---
## 7. The surprise lane

### 7.1 What this lane is for

Core tickers are chosen for *persistence*. The surprise lane exists for the opposite property: names that are **temporarily abnormal**. The ticker is the transient thing; the strategy is the constant.

**The design danger, stated plainly:** a scanner with loose thresholds becomes a machine for chasing attention, which is precisely the documented behavior that produces −4.7% 20-day abnormal returns (KB §10.4). **The scanner must be treated as a source of hypotheses about a name, not as a buy signal.** Detection and admission are separate events, and admission and trading are separate events again.

### 7.2 Hard exclusions — evaluated before any trigger

A ticker firing any exclusion never enters the lane, no matter how loud the signal:

| Exclusion | Threshold |
|---|---|
| Venue | OTC, Expert Market, or Pink No Information (KB §3.4) |
| Price | < $1.00 |
| Market cap | < $50M |
| Spread | > 100 bps |
| Regulatory | Active SEC trading suspension, or a T12 halt |
| Reverse split | Within trailing 90 sessions |
| Dilution | S-1, S-3, 424B, or ATM prospectus filed within 5 sessions **on the long side.** On the short side this is a *promoting* condition, not an excluding one (see D6b) |
| Corporate action | Announced fixed-cash acquisition |
| Already core | Route to core handling instead |

The reverse-split and dilution rules are doing most of the work here. They are what separate this lane from a pump-chasing scanner (KB §3.5).

### 7.3 Trigger taxonomy

Eight triggers. A ticker may fire several; record all of them, because the **combination** is more informative than any single one.

**T1 · Volume anomaly**
- Time-of-day-normalized RVOL ≥ **5.0** on a 30-min rolling window
- Today's dollar volume ≥ **$50M**
- Dollar volume ≥ **8×** the ticker's own 60-day median
- Fires: several per day in active markets

**T2 · Gap**
- Pre-market gap ≥ **±8%** measured at 09:00 ET
- Pre-market dollar volume ≥ **$5M**
- Sub-triggers: `gap_with_news` vs `gap_no_news` — this split is the whole informational content (see D2)

**T3 · New listing**
- IPO, direct listing, or de-SPAC within trailing **90 sessions**
- Day-1 dollar volume ≥ **$100M**
- Track and flag three dates: **quiet-period end (~25 days), lockup expiry (~90–180 days), first earnings report**
- **Special handling:** no price history means most fingerprint fields are undefined. New listings are eligible only for the small subset of strategies needing no history. Do not let a null fingerprint silently pass a routing gate — assert on it.
- **Elevated caution flag:** a quarter of the 250+ companies that listed on Nasdaq's smallest tier since 2023 were promoted in chat groups and then crashed or were suspended (KB §3.5). **An exchange listing is not a filter.** Any T3 admission on a small-tier listing requires the social-promotion check in T7 to come back clean.

**T4 · Squeeze conditions**
- Short interest ≥ **20% of float**
- Days to cover ≥ **3.0**
- Borrow rate ≥ **20% annualized** OR utilization ≥ **90%**
- Price up ≥ **10%** on RVOL ≥ 3.0
- Record whether SSR is active — it changes short-side fill dynamics for the rest of the day and the next (KB §1.7.3)

**T5 · News and catalyst**
- T1 (news) halt occurs
- Material 8-K filed
- Scheduled binary event: FDA PDUFA date, court date, major contract decision
- M&A announcement (stock-for-stock; cash deals are excluded above)
- Guidance revision outside earnings

**T6 · Options anomaly**
- Total options volume ≥ **3×** 20-day average
- **Volume ÷ open interest ≥ 0.5** — this ratio is the important one; it indicates *new positioning* rather than closing, and most "unusual options activity" scanners omit it
- 25Δ skew shifts more than 2σ from its 60-day mean
- IV rank jumps more than 30 points in a session

**T7 · Social velocity**
- Mention count ≥ **5×** the ticker's 30-day baseline
- Present on **at least two independent platforms** (single-forum spikes are more likely coordinated promotion)
- **Promotion check:** if mentions are concentrated in a single cluster of accounts, or accompanied by return predictions, flag as **suspected promotion** and route to exclusion rather than admission

**T8 · Index event**
- Announced index addition or deletion
- Inside the FTSE Russell reconstitution banding zone (feeds S5)
- Estimated index demand ≥ 3× median daily dollar volume

### 7.4 Trigger → strategy routing

The trigger determines which generic strategies are eligible. Nothing else runs.

| Trigger | Eligible strategies | Notes |
|---|---|---|
| T1 volume anomaly | D5, S4, D6 | Fade-biased; the crowd is already there |
| T2 gap + no news | **D2** | The no-news condition is D2's core gate |
| T2 gap + news | D6, momentum continuation | Respect news-driven breaks |
| T3 new listing | Price-action only; no history-dependent strategy | Most of the library is ineligible by construction |
| T4 squeeze | D6, D4 (post-SSR long side), S4 | Requires borrow verification before any short |
| T5 news halt | **D3** | Requires halt-reason codes to distinguish LULD from T1 |
| T6 options anomaly | O2, S3 | Only if `opt_spread_pct_25d` passes |
| T7 social velocity | S4, D5 | Only after the promotion check clears |
| T8 index event | S5 | Long lead time; low frequency |

### 7.5 Lifecycle

```
  DETECTED ──► PROBATION ──► ACTIVE ──┬──► GRADUATED (to core)
                   │            │     │
                   └────────────┴─────┴──► EVICTED
```

**DETECTED**
- Trigger fires, hard exclusions pass
- Logged to `surprise_events` with trigger type, timestamp, and all metrics at detection
- **No trading. No shadow. Data collection only.**
- Partial fingerprint begins accumulating

**PROBATION** — minimum 5 sessions
- Shadow execution only; signals logged, no capital, not even paper
- Requires: dollar volume ≥ $20M each session, spread ≤ 50 bps, no new exclusion triggered
- Purpose: confirm the abnormality is a *state*, not a single print

**ACTIVE** — sessions 6 through 40
- Eligible for the trigger-matched generic strategy set only
- **Caps:** max 5 concurrent active surprise tickers, max 2 per correlation cluster, aggregate surprise-lane exposure ≤ 20% of the book
- Re-evaluated every 5 sessions
- Fingerprint continues building toward completeness

**GRADUATED** — the surprise-to-core path
Requires **all** of:
- 60+ sessions sustaining core-gate liquidity (dollar volume ≥ $20M, spread ≤ 25 bps)
- Fingerprint complete — no null fields
- Passes the §3.2 sector-dominance test, or fills a §3.3 coverage gap
- No hard exclusion at any point during the window
- **Aym approval** (see §8)

**EVICTED** — any of:
- RVOL < 1.5 for 10 consecutive sessions (the abnormality is over)
- Dollar volume < $20M for 5 consecutive sessions
- Any hard exclusion newly triggers
- 40 sessions elapsed without meeting graduation criteria
- Strategy performance on this ticker hits the auto-demote threshold

**Eviction preserves the record.** The event, the fingerprint, and every strategy result stay in the database permanently. **The evicted names are the most valuable rows you will have**, because they are the ones a survivorship-biased dataset would silently delete — and having them is what will let you compute how much survivorship bias inflates results elsewhere.

### 7.6 The demotion path (core → bench)

Core tickers failing §3.5 for 60 consecutive sessions move to bench: no new strategy fitting, existing strategies wound down, fingerprint frozen, all history retained. Bench tickers are re-evaluated at each quarterly re-derivation and can return to core without a fresh approval if they re-pass the gates.

---

## 8. Governance

You did not answer the admission-authority question, so here is my recommendation with the reasoning, for you to confirm or override.

**Recommended split, mapped to Quant's existing bounded authority:**

| Action | Authority | Reasoning |
|---|---|---|
| Detect and log a surprise ticker | **Quant, autonomous** | Pure data collection, no exposure |
| Promote to probation (shadow) | **Quant, autonomous** | Shadow trading is already inside Quant's existing authority; a new ticker within an approved asset class is not an asset-class change |
| Promote probation → active (live capital) | **Aym approves** | This is live exposure to a name with no track record — the same class of decision as a live strategy promotion |
| Graduate surprise → core | **Aym approves** | Permanent universe change |
| Evict from surprise lane | **Quant, autonomous** | Risk-reducing; matches existing auto-demote/auto-retire authority |
| Demote core → bench | **Quant, autonomous** | Risk-reducing |
| Quarterly core re-derivation | **Quant proposes, Aym approves the diff** | Quant runs the criteria and produces an add/drop list; Aym approves the delta, not all 158 |
| Change any threshold in §3 or §7 | **Aym approves** | These are risk-config changes |

The logic: **Quant may autonomously do anything that reduces exposure or adds no exposure. Anything that creates new live exposure to an unproven name needs you.** That is the same line already drawn in the SPEC, applied to tickers instead of strategies.

**One escalation rule worth adding:** if the scanner admits more than **8 surprise tickers in a rolling 5 sessions**, that is a market-regime signal, not 8 independent opportunities. Pause new admissions and notify. Broad simultaneous abnormality means correlated risk, and the position caps in §7.5 will not catch it on their own.

---

## 9. Review cadence

| Cadence | Task | Owner |
|---|---|---|
| Continuous | Surprise scanner | Quant, automated |
| Daily | Surprise-lane state transitions; exclusion re-check on all active | Quant |
| Weekly | Correlation cluster refresh; N_eff recomputation | Quant |
| Monthly | Fingerprint refresh, all core tickers | Quant |
| **Quarterly** | **Full core re-derivation from §3 criteria.** Output = add/drop diff with a stated reason per line | Quant proposes → Aym approves |
| Semi-annual | Coverage-target audit (§3.3); threshold review | Aym |
| Annual | Survivorship audit — compare current universe to the universe as of 12 months ago and measure how much the drift alone would have inflated backtest results | Both |

**That annual survivorship audit is the most important recurring item in this table** and the one most likely to get skipped. It is the only mechanism that will tell you how much of any historical result is real versus an artifact of the list having quietly become a list of winners.

---

## 10. Data requirements

| Need | Feeds | Difficulty | Gates |
|---|---|---|---|
| Daily OHLCV, splits, dividends | Alpaca, Polygon, Nasdaq Data Link | Low | Everything |
| Intraday 1-min + quotes | Alpaca, Polygon | Low–medium | All intraday strategies |
| **Delisted / survivorship-complete history** | CRSP, Norgate, Sharadar | **Medium — and non-negotiable** | The entire integrity of the exercise |
| GICS sub-industry classification | Refinitiv, FactSet, or a free approximation | Low | §3.2 sector dominance |
| Free float | Sharadar, Refinitiv | Medium | D6, squeeze triggers |
| Short interest, borrow rate, ETB flags | FINRA (bi-monthly SI), IBKR or Alpaca (borrow) | Medium | All short strategies, T4 |
| **Halt tape with reason codes** | UTP/CTA feeds, Polygon | **High** | D3, halt simulation |
| SSR trigger list | Nasdaq Trader daily file | Low | D4 |
| Odd-lot trade data | Polygon, post-Nov-2025 dissemination | Medium–high | D5 retail proxy |
| Options chains + IV surface | ORATS, CBOE DataShop, Polygon | High | All options strategies |
| Corporate filings with timestamps | SEC EDGAR full-text | Low–medium | Dilution exclusion, T5 |
| Social mention counts | Various; no clean cheap source | High | T7 — likely the binding constraint |
| Index reconstitution lists | LSEG/FTSE Russell | Medium | T8, S5 |
| Crypto OHLCV + funding | Exchange APIs | Low | v1 strategies |

**Suggested build order by leverage:** daily OHLCV with survivorship-complete history first (it gates correctness of everything else), then intraday + quotes, then short-side data, then the SSR file (cheap and unlocks D4), then options, then halt codes, then odd-lot, then social last.

---

## 11. Suggested schema

Extends the existing SQLite design. Read-only to Quant, consistent with current isolation rules.

```sql
-- Master universe registry
CREATE TABLE universe (
  ticker            TEXT PRIMARY KEY,
  tier              TEXT NOT NULL,      -- T0..T5, BENCH, SURPRISE, QUARANTINE
  gics_sector       TEXT,
  gics_sub_industry TEXT,
  instrument_type   TEXT,               -- EQUITY, ETF, LEV_ETF, INV_ETF, VOL_ETP, INDEX, FUTURE, CRYPTO
  executable_v2     INTEGER,            -- 0 for futures/indices: signal only
  exec_symbol       TEXT,               -- broker symbol; differs from data symbol for crypto
  pair_mirror       TEXT,               -- SQQQ for TQQQ etc; mirrors are validation-only
  admitted_on       TEXT,
  admitted_reason   TEXT NOT NULL,      -- criterion ID or coverage gap; never null
  demoted_on        TEXT,
  demoted_reason    TEXT,
  quarantine_flag   INTEGER DEFAULT 0,
  cluster_id        INTEGER
);

-- Rolling fingerprint, one row per ticker per refresh
CREATE TABLE fingerprint (
  ticker TEXT, as_of TEXT,
  dollar_vol_60d REAL, spread_bps_median REAL, tick_constrained INTEGER,
  atr_pct_14 REAL, rvol_20 REAL, rvol_60 REAL, vol_of_vol REAL,
  variance_ratio_2 REAL, variance_ratio_5 REAL, variance_ratio_10 REAL,
  hurst REAL, adx_median REAL, autocorr_1 REAL, autocorr_5 REAL,
  volume_curve TEXT,                    -- JSON array, 13 buckets
  range_formed_by_1030 REAL, or_break_hold_rate REAL, vwap_touch_rate REAL,
  gap_freq_1pct REAL, gap_fill_rate_same_day REAL, overnight_return_share REAL,
  earnings_move_mean_abs REAL, earnings_move_vs_implied REAL,
  halt_count_252d INTEGER, ssr_trigger_count_252d INTEGER,
  opt_volume_20d REAL, opt_spread_pct_25d REAL, has_weeklies INTEGER,
  iv_rank_median REAL, skew_25d_median REAL, section_1256 INTEGER,
  short_interest_pct_float REAL, days_to_cover REAL,
  borrow_rate_median REAL, etb_flag REAL,
  beta_spy REAL, idio_vol_share REAL, odd_lot_share REAL,
  adr_flag INTEGER, data_gaps INTEGER, fingerprint_complete INTEGER,
  PRIMARY KEY (ticker, as_of)
);

-- Which strategies are permitted on which tickers, and why
CREATE TABLE routing (
  ticker TEXT, strategy_id TEXT, eligible INTEGER,
  rule_fired TEXT,                      -- e.g. 'VR5<0.9 -> mean_reversion'
  evaluated_on TEXT,
  PRIMARY KEY (ticker, strategy_id, evaluated_on)
);

-- Surprise-lane event log; rows are NEVER deleted
CREATE TABLE surprise_events (
  event_id INTEGER PRIMARY KEY,
  ticker TEXT, detected_at TEXT,
  triggers TEXT,                        -- JSON array; record ALL that fired
  metrics_at_detection TEXT,            -- JSON snapshot
  state TEXT,                           -- DETECTED/PROBATION/ACTIVE/GRADUATED/EVICTED
  state_changed_at TEXT,
  exclusion_checks TEXT,                -- JSON: which passed
  promotion_check TEXT,                 -- T7 suspected-promotion result
  evicted_reason TEXT,
  graduated_to_core INTEGER DEFAULT 0
);

-- Correlation clustering output
CREATE TABLE clusters (
  as_of TEXT, cluster_id INTEGER, ticker TEXT,
  n_eff REAL,                           -- universe-wide effective independent bets
  PRIMARY KEY (as_of, ticker)
);
```

**One design note:** `admitted_reason` is `NOT NULL` deliberately. If a ticker cannot be given a reason at insert time, it does not belong in the universe. That single constraint is what enforces §0 at the database level rather than in a document nobody re-reads.

---

## 12. Open questions

1. **Live-admission authority (§8)** — my recommendation is auto-admit to shadow, Aym approves live. Confirm or override.
2. **T3 new listings** — with no history, most of the fingerprint is null and most strategies are ineligible. Is the IPO lane worth building in v2, or should it wait until there is a strategy that actually needs it? My lean is defer: it is the highest-effort, lowest-yield trigger.
3. **Futures** — kept as signal-only. If v3 arrives sooner than expected, the executable flag and broker mapping need revisiting.
4. **Social data (T7)** — no clean cheap source. Options: build a Reddit/X scraper, buy a feed, or **drop T7 and lean on T6 options anomaly as the crowd proxy instead.** T6 is cheaper, cleaner, and arguably better — options positioning is real money rather than talk.
5. **Quarantine tickers** — MULN and SNDL kept as harness canaries. Confirm that framing, or drop them.
6. **Universe size** — 158 is where the criteria landed. If quarterly re-derivation pushes past ~200, that is a signal the gates are too loose, not that the market got better.

---

## 13. Change log

| Version | Date | Note |
|---|---|---|
| 1.0 | 12 Aug 2026 | Initial protocol. Audits Raven's 157-slot draft (156 unique), corrects 3 misclassifications and 1 duplicate, adds XLC and 16 coverage names, drops VOO, adds fingerprinting, correlation clustering, and the surprise-lane lifecycle. |
