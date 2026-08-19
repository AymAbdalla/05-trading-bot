# DESIGN: multi-asset modular trading architecture

**Author:** Cody (Opus), 2026-08-19 11:36 EDT (measured with `date`).
**Brief:** `docs/handoffs/from-raven/2026-08-19-multi-asset-modules.md`.
**Repo state read:** HEAD `a4832f2`, working tree carries only untracked
`_scratch_*.py` and one proposal draft.
**Status:** DESIGN ONLY. No code written, no repo created, no package created.
**Decision owner:** Aym. Raven and Cody both have open calls at the end.

---

## 0. Verdict up front

**Option A (monorepo, shared core) now. Option B (extracted `trading-core`
package) later, on a stated trigger, not on a feeling.** I am rejecting Raven's
lean toward B-immediately, and I am rejecting the five-module carve as drawn.

| Raven proposed | My call | Why (measured in this repo) |
|---|---|---|
| Option B: `trading-core` package + 5 repos, starting now | **Option A now, B on a trigger** | A shared package with one real consumer is a directory with extra ceremony. This repo already shipped an abstraction designed against one consumer - `strategies/base.py` - and the second consumer had to shim it into uselessness. Section 2. |
| Five modules by **venue** (Polymarket / crypto / equities / futures / options) | **Three engines by PAYOFF SHAPE + five venue adapters** | Every component that forked when Polymarket landed forked on payoff shape, not on venue. Crypto, equities and futures are one engine. Section 1. |
| "The adapter boundary" is the seam | **Necessary, not sufficient. Three seams.** | The adapter seam is real, but it does not explain the strategy-interface fork or the harness fork. Both sit above the adapter. Section 1.3. |
| Backtest harness is shareable ("the 21 checks, generalized") | **False today, and this is a live hole** | `backtest/validate_harness.py` contains **zero** references to Polymarket. It validates one payoff family on 8 equity/crypto CSVs. The Polymarket harness has no validation gate at all. Section 1.2. |
| Prop-firm futures as the proof-of-concept second module | **No. Crypto second** - and for a different reason than Aym's | Crypto is the only candidate that inherits an **already-validated** harness. Prop is the module where being wrong costs cash. Section 5. |
| Module = department | **No. Five modules, one desk, one DECISIONS.md** | D-numbers cannot fork. Convention 24 becomes unenforceable across repos. Section 7. |
| Options in the roadmap | **Cut it. Reopen only on a named identity.** | D-342 R5 kills it on its own terms: options edge is a vol-surface forecast, and our measured fair_value slope is 0.30. Section 6.4. |

One thing Raven's list is missing entirely, and it is the most important
sentence in this document:

> **This repo has already run the experiment.** Polymarket was bolted onto a
> candle-based crypto/equities engine. We do not have to guess which seams
> hold - we can read which ones held. Sections 1.1 and 1.2 are that autopsy.

---

## 1. What is genuinely shareable - decided by autopsy, not by taste

### 1.1 The components that survived the second asset class

| Component | Lines | Held? | Why it held |
|---|---|---|---|
| `engine/halt.py` | 99 | **YES, cleanly** | Takes no market input at all. One file path, one existence check, no config key, no env override. |
| `engine/risk/constraints.py` | 425 | **YES, with one defect** | Takes `(notional_usd, asset_family, window_ts)` and equity. Deliberately takes **no probability** (D-342). Absolute USD, not fractions of equity. Defect in 1.4. |
| `engine/risk/events.py` | 174 | **YES** | Writes `risk_events`, routes drawdown into the one halt. Nothing venue-shaped. |
| `engine/concurrency.py` | 992 | **YES** | It is repo infrastructure, not trading code. Counted honestly below. |
| `agents/forge.py` | 1,029 | **YES - already multi-asset** | Already declares 12 asset classes including `FUTURES` and `OPTIONS`, already carries a **per-asset-class** edge floor (30bps spot / 20bps binary, D-336), already has `find_asset_class_gaps`. Forge did not need porting. It was built for this. |

### 1.2 The components that FORKED

| Component | What happened | Evidence |
|---|---|---|
| **Strategy interface** | Forked into two incompatible contracts | `strategies/base.py:63` is `scan(candles) -> Signal`. `strategies/polymarket/base.py:394` is `evaluate(ctx) -> Decision`. The compatibility shim exists (`PolymarketStrategy.scan`, line 399) and its own docstring says: *"With no orderbook in the context, every strategy here falls through to its book gate and returns SKIP."* **The shared interface is decorative.** It type-checks and returns nothing. |
| **Backtest harness** | Forked into two engines | `backtest/validate_harness.py` (340 lines) loads AAPL/MSFT/NVDA/SPY/TSLA/JPM/BTC_USD CSVs and validates oracle / buy-hold / coin-flip / look-ahead / cross-harness / survivorship. `backtest/polymarket_harness.py` (817 lines) was written from scratch and **explicitly refuses** to compute profit factor on stop distance, R-multiple, and MAE/MFE, because a binary has no path. |
| **Harness validation** | Did **not** fork - it just never followed | `grep -i polymarket backtest/validate_harness.py` returns **nothing**. Convention 1 says no result is durable unless `validate_harness.py` exits 0. That gate has never covered the Polymarket engine. **This is a standing hole in module #1, and it is the single biggest argument for fixing the harness story before adding module #2.** |
| **Fill model / paper adapter** | Forked, 3.5x | `engine/adapters/paper.py` (559 lines: fills at ask/bid, $2,000 ledger, stops off the live feed) vs `engine/polymarket/paper_adapter.py` (1,959 lines: resting maker fills, binary settlement, multi-leg pairs). |
| **Schema** | Shared, but strained | `positions` grew **13** Polymarket-only columns (`pair_id`, `leg_index`, `leg_*_at_signal`, `leg2_latency_ms`, `pair_cost_*`). And `signals.tf` is written as the string literal `'5m'` at `engine/polymarket/shadow_loop.py:877` - which is precisely why the 15m keying change needs a new `market_duration` column and is on the ~03:45 2026-08-20 restart. **A shared table that cannot express the second asset's own timeframe is a shared table in name only.** |

*(Line number check, convention 25: the wake-up file says the `'5m'` literal is
at `shadow_loop.py:850`. It is at **877**. Re-grep, never trust a line number in
a doc - including this one.)*

### 1.3 The actual rule this autopsy produces

Everything that held takes **notional, time and identity**. Everything that
forked touches **the price path or the payoff shape**. That is the whole
pattern, and it gives the design rule:

> **A component belongs in core if and only if its inputs are (notional, time,
> instrument identity) and it never reads a price path or a payoff. Everything
> that reads a path or a payoff belongs to a PAYOFF FAMILY, not to core and not
> to a venue.**

So the seam is not one seam. It is three, stacked:

```
  seam 3   core            risk caps, halt, ledger, registry, Forge, decisions
                           inputs: notional, time, identity.  no price, no payoff
  ------------------------------------------------------------------------
  seam 2   payoff engine   PATH | BINARY | CONVEX
                           the harness, the fill model, the strategy interface,
                           the exit semantics.  ONE per payoff family, not per venue
  ------------------------------------------------------------------------
  seam 1   venue adapter   market data, order submission, fees, settlement feed
                           ONE per venue
```

Raven's brief collapses seam 2 into seam 1. That collapse is exactly the mistake
that produced a `scan()` that returns SKIP for every strategy.

### 1.4 The one defect in the "venue-agnostic" risk module

`engine/risk/constraints.py:139` is `asset_family_for_slug(slug)`. It string-parses
a **Polymarket slug** (`'btc-updown-5m-...'`, `'solana-up-or-down-...'`) and
checks for the markers `-updown-` / `-up-or-down-`. A Binance `BTCUSDT` perp
hits none of that and lands in `UNKNOWN_FAMILY`.

The consequence is not cosmetic. The per-event cap aggregates on
`(asset_family, window_ts)`, and its whole justification is measured: *"247 of
298 events (82.9%) span more than one asset"*, btc/eth co-resolution
phi = +0.529. A BTC perp long and a `btc-updown` UP share the same underlying
move, and today they would land in different buckets and stack uncapped.

**Fix before module #2, not after:** replace `asset_family_for_slug(slug)` with
`asset_family(instrument)`, where the venue adapter is required to declare the
family. Declared, never inferred - which is the discipline the module's own
docstring already states for correlation groups: *"a group that silently
regroups itself is a limit that silently stops binding."*

### 1.5 Final boundary table

| Layer | Item | Verdict |
|---|---|---|
| **core/risk** | notional caps (per-trade / per-event / aggregate), drawdown | SHARED, unchanged |
| **core/risk** | asset-family classifier | SHARED, **must be re-cut** (1.4) |
| **core/halt** | the one kill path | SHARED, unchanged |
| **core/ledger** | `concurrency.safe_edit`, pre-commit hook | SHARED |
| **core/registry** | strategy registry, `strategies/proposals/`, Forge, critic | SHARED (Forge already is) |
| **core/schema** | `signals`, `positions`, `equity_snapshots`, `risk_events`, `audit_log` | SHARED **spine only**; payoff-specific columns move to a per-family side table (see 3.2) |
| **core/process** | conventions 1-34, DECISIONS.md, kill-condition rule, D-342 R5 | SHARED, and **singular** (section 7) |
| **payoff/PATH** | vectorized harness, `validate_harness.py`, path fill model, stop/target exits, PF / R / MAE / MFE, `assertions.py` canaries | ONE engine, serves crypto + equities + futures |
| **payoff/BINARY** | `polymarket_harness.py`, resolution ledger, premium-as-max-loss, `evaluate(ctx)->Decision` | ONE engine, serves Polymarket (and any other event venue) |
| **payoff/CONVEX** | Greeks, surface, chain | DOES NOT EXIST. Do not scaffold it (6.4) |
| **venue** | market data feed | PER VENUE |
| **venue** | order submission + venue fee schedule | PER VENUE |
| **venue** | settlement / resolution source | PER VENUE |
| **venue** | margin & leverage rules | PER VENUE |
| **venue** | asset-family declaration for a symbol | PER VENUE (feeds core, 1.4) |

**The five-module carve becomes three engines and five adapters.** Crypto,
equities and prop futures share a payoff engine and differ only in adapter,
margin rules and fee schedule. That is where the reuse Aym asked for actually
lives - and it is far more reuse than the venue carve would have produced.

---

## 2. Connect or stay separate: A now, B on a trigger

### 2.1 Why not Option B immediately

**The core has one real consumer.** Publishing `trading-core` now means
designing its API against a sample size of one. This repo has already paid for
that mistake once: `strategies/base.py` was designed against candles, the second
consumer arrived, and the shared interface degraded into a shim that returns
SKIP. An API extracted before the second consumer exists gets extracted around
the first consumer's assumptions and then cannot bend.

**Extraction is cheap later, un-extraction is not.** Moving `core/` out of a
monorepo into a package is a mechanical refactor with a test suite watching (97
test files today). Merging five diverged copies of a risk module back together
is not mechanical.

### 2.2 Raven's stated con for A, examined

> *"a Polymarket session touching crypto code is the cross-writer risk we've
> fought all week."*

The cross-writer risk is real - the wake-up file records six sightings of stale
checkouts and the `AGENT_ID` probe is still unsettled at 4 SET / 5 EMPTY. But
splitting into five repos makes it **worse for exactly the files that matter**:

- The concurrency ledger and the pre-commit hook are **repo-scoped**. Five repos
  = five independent ledgers and zero coordination on `core/`, which is the one
  thing two modules will both want to edit.
- The file that actually generated friction this week is `CLAUDE.md`, which is
  already per-directory and is gitignored anyway.
- Directory-scoped `CLAUDE.md` gives each module its own wake-up context inside
  a monorepo. That was B's main claimed advantage and it is available in A.
  *(Verify the nested-context behaviour before relying on it - convention 25
  applies to tooling claims too.)*
- Raven's own concern that Forge must see all modules is decisive: Forge reads
  the graveyard, the evidence pack, the pooled analysis and `db/trading.db`. In
  A that is a glob. In B it is a cross-repo data contract that has to be
  designed, versioned and kept in sync - the versioning problem Raven already
  flagged, applied to the component that most needs breadth.

### 2.3 The trigger for B (stated as a condition, per convention 6)

Extract `trading-core` when **all** of the following hold:

1. A second module is green on **its own validated harness** (not on the first
   module's), and
2. `core/` has taken **zero** commits that touch exactly one module's directory
   for **30 consecutive days**, and
3. Two modules import the same core symbol in production paths.

Condition 2 is the real test. If core keeps taking single-module commits, it is
not core - it is module-1 code in a shared folder, and packaging it would freeze
that mistake behind a version number.

### 2.4 Option C is refused

Reimplementing `engine/risk/` five times is the specific thing Aym said he does
not want, and `engine/halt.py`'s own docstring already prices it: *"three copies
of a kill switch is three chances for one of them to point somewhere else, and
the failure mode is silent."* Five copies of a drawdown cap is the same bug with
more surface. C is out.

---

## 3. Structure

### 3.1 The tree (Option A)

```
05-trading-bot/                      # one repo, one git history, one hook
  core/
    risk/          constraints.py  events.py         # moved from engine/risk/
    halt.py                                          # moved from engine/
    ledger/        concurrency.py                    # moved from engine/
    registry/      strategy registry, proposals/
    schema/        schema.sql  (spine tables only)
    process/       CONVENTIONS.md  DECISIONS.md      # SINGULAR. see section 7
  payoff/
    path/          vectorized harness, validate_harness.py, assertions.py,
                   path fill model, Strategy(scan)->Signal
    binary/        polymarket_harness.py, resolution ledger,
                   PolymarketStrategy(evaluate)->Decision
  modules/
    polymarket/    CLAUDE.md  adapter/  strategies/  shadow_loop.py
    crypto/        CLAUDE.md  adapter/  strategies/    # binance testnet
    equities/      CLAUDE.md  adapter/  strategies/    # alpaca paper
    futures/       CLAUDE.md  adapter/  strategies/  prop_rules.py
  agents/          forge, critic, judge, scout        # sees all modules
  dashboard/                                          # one reader, many books
  db/
  tests/
```

`engine/` disappears as a name. It currently means three different things at
once (core, payoff engine, and Polymarket module) and that ambiguity is what let
`engine/polymarket/` grow to 16,318 lines with the shared parts buried inside it.

**This is not a Phase-1 refactor.** The move only earns its cost when module #2
starts. Until then the mapping is a rename plan on paper. Doing it early is
churn against a shared working directory with live processes running out of it.

### 3.2 Schema: spine plus per-family side tables

`positions` today carries 13 columns that only Polymarket ever writes. Adding
crypto and futures would add more (leverage, margin, funding, liq price). Left
alone, the shared table becomes the union of every venue and no reader can tell
which columns are meaningful for a given row.

- **Spine** (`positions`): `id, pair, strategy_id, signal_id, opened_ts,
  closed_ts, entry_px, exit_px, qty, pnl_gross, pnl_net, fees, exit_reason,
  mode`. Venue-neutral, and it is what the dashboard and the risk module read.
- **Side tables**, keyed on `positions.id`: `positions_binary` (the leg/pair
  columns, `fill_was_maker`), `positions_path` (leverage, margin, funding,
  liq_px, `r_multiple`, MAE/MFE).
- `signals.tf` is the live warning. A shared column whose meaning is asserted by
  a literal in one module's loop is a column that will be wrong for module #2.
  The `market_duration` column already designed for the 2026-08-20 restart is
  the right shape: **additive, nullable, no default.** Use that pattern for
  every cross-module column, forever.

---

## 4. Connecting the modules together (Q4)

**Do not build `engine/portfolio/`. Reserve the seam instead - and the seam
already exists.**

`constraints.check()` aggregates open exposures into `(asset_family, window_ts)`
buckets. Portfolio-level correlation risk is the same computation with a
venue-neutral family key. Once 1.4's fix lands - the adapter declares the family
rather than core parsing a slug - a BTC perp, a BTC-settled future and a
`btc-updown` binary all land in one bucket and the per-event cap is already the
portfolio cap. No sixth module.

Two things must be added, and both are small:

1. **A declared pooling policy.** We already have two books that must not be
   pooled: the main loop on `db/trading.db` and environment B on
   `db/trading-survivors.db`, with a standing instruction never to cross their
   results. That rule lives only in prose today. It should be a declared field
   on the book, so a cross-book sum is refused rather than remembered.
2. **A cross-book equity view for the drawdown halt only.** Per-module drawdown
   is sufficient during shadow. It stops being sufficient the moment two modules
   draw on one pot of real capital - at go-live, not before.

The dashboard is currently single-book (`dashboard/config.py:23`, one
`TRADING_BOT_DB` path). "Multi-source reader" is aspirational, not current. That
is fine; it is also the cheapest possible piece of the connect-together work and
should not be confused with portfolio risk.

---

## 5. Q3, answered: prop futures is not the proof-of-concept

The risk-module fit Raven noticed is real but shallow, and the mismatch
underneath it is the whole point.

- Our `Decision` **denies a candidate entry**. A prop-firm breach **ends the
  account**. Those are different failure modes: one is a gate, one is terminal
  and externally enforced. `engine/halt.py` blocks new entries and, on the
  Polymarket path, *cannot flatten a binary in paper mode*. A prop module needs
  hard-flatten-and-lock, which is a new capability, not a config value.
- Prop-firm limits are **intraday and reset**; ours are absolute-USD and
  peak-relative. Trailing daily drawdown against a high-water mark that resets
  at a broker-defined session boundary is not `EquityState.drawdown_frac()`.
- **An evaluation costs money.** Shadow-first doctrine says you do not pay for
  an evaluation before edge exists. Prop is the module with the highest external
  cost of being wrong, which makes it the worst candidate for a proof-of-concept.

**Crypto is second - but not because it is "closest to Polymarket's data shape."
It is not; Polymarket is a binary and crypto is a path.** Crypto is second
because it is the only candidate that inherits a **validated** harness.
`validate_harness.py` runs `BTC_USD_1d.csv` through oracle, buy-hold, coin-flip,
look-ahead and cross-harness checks **today**. Convention 1's gate already exists
for the path family. For the binary family it does not exist at all.

That reframes the whole sequencing question:

> **Choose the next module by which harness gate it inherits, not by which
> markets look interesting.**

---

## 6. Sequencing, with gates that have numbers

The standing rule as written - *"no new module until the current one
demonstrates edge"* - is unfalsifiable, because "demonstrates edge" has no
number and names no harness. Convention 6 requires both. Proposed:

### 6.1 Gate 0 (blocks everything): close the harness hole

Module #1 has been running for weeks against a gate that does not cover it.
Before any second module:

- **G0a.** `validate_harness.py` gains binary-family controls - at minimum an
  oracle control and a fee-application control run through
  `PolymarketHarness.score`, on the same all-pass/exit-0 contract.
- **G0b.** `backtest/assertions.py`'s 8 assertions get a binary-family
  counterpart. The quarantine canary (`{'MULN','SNDL','BBBYQ'}`) is a path-family
  construct; the binary equivalent is a market known to have resolved against
  the consensus. **Every module ships its assertions before its first result,
  not after.**
- **G0c.** `asset_family_for_slug` becomes `asset_family(instrument)` with the
  family declared by the adapter (1.4).

G0 is genuinely small - it is one harness file and one assertions file - and it
is the difference between a second module inheriting a gate and inheriting a
gap.

### 6.2 Gate 1: Polymarket structural proof

Structural family (033 brackets + 036 family key cross-market monotonicity, the
primary surviving forecast-free direction, currently UNTESTED). **Number
required before crypto starts.** Reuse the shape this project already uses for
D-326: a t-statistic on a minimum n, taker-only, reported split by
`fill_was_maker` per convention 32. Aym and Raven set the threshold; I will not
invent it here, because a threshold invented to be clearable is convention 17's
exact failure.

### 6.3 Order after that

| # | Module | Gate to pass before starting it | Marginal cost |
|---|---|---|---|
| 1 | Polymarket | (running) | - |
| 2 | **Crypto** (Binance testnet) | G0 + Gate 1 | Adapter + fee model. **Payoff engine and its validation already exist.** |
| 3 | **Equities** (Alpaca paper) | Crypto green on `validate_harness.py` | Near-free. Same payoff engine, second adapter, plus market-hours and halt handling. |
| 4 | **Futures / prop** | Equities green **and** hard-flatten-and-lock built and drilled | Real money. New terminal failure mode (section 5). |
| 5 | ~~Options~~ | **Cut** (6.4) | - |

Note that 2 and 3 are nearly the same work. That is the payoff-family
decomposition paying for itself: Aym's five modules cost roughly three builds.

### 6.4 Options: cut it, and here is the argument on its own terms

D-342 R5 is the standing filter: *a forecast-free strategy is one whose payoff
is guaranteed by an IDENTITY, not one whose signal is computed without a
forecast.* Apply it to options and the family splits cleanly in two:

- **Forecast options** (IV rank, vol-surface relative value, skew trades). These
  are forecasts of a second moment. Our measured fair_value slope is 0.30 and
  execution is ~9% of the loss while the model is 91%. We are demonstrably bad
  at forecasting a first moment. There is no reason to expect a second moment to
  go better, and R5 rejects it outright.
- **Identity options** (put-call parity, box spreads, vertical monotonicity).
  These genuinely pass R5. They are also the most heavily competed arbitrage in
  listed markets and are not available at retail size after fees - which is
  structurally the same finding as 037: complement no-arb at top-of-book is
  *structurally impossible* on Polymarket because the venue's own arithmetic
  reflection puts the floor at 1.001 against a `<= 0.996` gate.

So options is either a forecast we should not take or an identity that does not
pay us. **Cut it from the roadmap.** Reopen it only when someone names a
specific identity and prices it against fees at our size - the same bar every
other proposal gets.

Worth noting: a Polymarket binary **is** a digital option, and the binary payoff
engine is already a (degenerate) options engine. If options ever return, they
return as an extension of the binary family, not as a fifth venue.

---

## 7. Governance: five modules, one desk

**Module is a unit of code. Department is a unit of decision-making. They do not
map 1:1, and forcing them to would break the one asset that is genuinely
irreplaceable here.**

What must stay **singular**, in one place, forever:

- **`DECISIONS.md`.** D-numbers are a single sequence, currently at D-343.
  Convention 24 (*"a cited D-number is not a decision"* - check it exists)
  is only checkable against one file. Five DECISIONS.md files means D-344 is
  written twice with different meanings, which is **exactly** the failure that
  already happened to convention 27 and is documented in the header of
  `CONVENTIONS.md`.
- **`CONVENTIONS.md`.** Same argument, and it is already git-tracked and
  test-pinned (`tests/test_conventions_doc.py`) precisely because a rewritable
  untracked mirror could not enforce it.
- **The kill-condition rule and D-342 R5.** These are the filter every proposal
  passes through. A per-module filter is not a filter.

What can and should be **per-module**:

- `CLAUDE.md` wake-up context (already directory-scoped, already gitignored).
- `docs/handoffs/` and `docs/handoffs/from-raven/`.
- The strategy set, the adapter, the venue-specific traps.
- The module's own harness invocation and its own kill conditions.

The evidence for keeping decisions singular is in this repo's own history: two
concurrent Cody sessions on **one** repo already produced a silently-clobbered
convention. Five repos with one Raven and one brain does not reduce that; it
removes the single file where the clobber was detectable.

**Trading Desk stays one department.** If it ever splits, it splits by capital
allocation authority, not by asset class.

---

## 8. What the old project's lessons forbid

Correction to the brief's premise: **the lessons are not "current but retired
code" - several of them are already running code in this repo.**

- `backtest/assertions.py:36` carries `QUARANTINE_TICKERS = {'MULN','SNDL','BBBYQ'}`
  and `assert_quarantine_canary` (line 53), with `QUARANTINE_PF_CEILING = 1.3`.
  `validate_harness.py:187` carries the same delisted set for the survivorship
  check. The canary is live.
- `ASSERTIONS` runs 8 checks over the graveyard: quarantine canary, mirror-pair
  contradiction, win-rate ceiling, trade-count sanity, duplicate strategies,
  timeframe coherence, gate-version uniformity, cost-model-version uniformity.

Two honest caveats:

1. **I did not find a selection-bias test by that name in this repo.** The
   brief's "winners avg -$0.30/trade on unseen instruments" result is not
   reproduced by code I located here. Treat it as a lesson to re-implement, not
   as an existing check. (Convention 11: I could not find it, which is not the
   same as it not existing.)
2. **All 8 assertions run against the crypto/equity graveyard only.** Nothing
   equivalent guards Polymarket results. Same hole as G0b.

The forbiddances, stated as rules for every future module:

| Lesson | Rule for a new module |
|---|---|
| Selection bias | A strategy selected on instrument set X is scored on instrument set Y before it counts. No exceptions, no "but it's the same asset class." |
| Delisting / survivorship | The universe must contain names that died. A universe of survivors is a long bias wearing a backtest. |
| Canary tickers | Every module declares instruments a broken harness would show as profitable, and asserts they are **not**. Path family: MULN/SNDL/BBBYQ. Binary family: markets that resolved against consensus. |
| Regime-filter lookahead | Every module's harness runs the A3 look-ahead shift check (oracle with `execution_delay=1` must collapse). Today only the path family does. |
| Quarantine | Data that cannot be trusted is quarantined by path, not by a flag in a config someone can flip. |
| Pooling | Never pool across cost-model versions, fill-provenance (convention 32), payoff families, or books. This is the rule most likely to be broken by a "unified" dashboard. |

---

## 9. Kill conditions for this design

Convention 6 applies to designs, not only to strategies. Each of these has a
number and a named check.

| # | The design is wrong if... | Measured by | Window |
|---|---|---|---|
| K1 | `core/` is not actually shared: **>20%** of commits touching `core/` in the window also touch exactly one `modules/*` directory | `git log --name-only` over `core/` | 60 days after module #2 lands |
| K2 | The payoff-family carve is wrong: a **third** strategy interface appears (a `scan`/`evaluate`-equivalent that is neither) | grep for abstract base classes under `payoff/` | any time |
| K3 | The monorepo verdict is wrong: **>3** hook-refused commits per week on `core/` paths caused by cross-session collision | pre-commit hook refusals, and the `file_coordination` table | rolling 4 weeks |
| K4 | A module is dead weight: no result signed off by **its own** validated harness | that module's `validate_harness` exit code | 90 days from module start |
| K5 | The shared risk core is decorative: no constraint binds more than **5** times in 30 days across **all** modules pooled | `risk.events.denials_by_constraint` - the module's existing kill condition, widened to every book | rolling 30 days |
| K6 | Gate 0 was skipped: module #2 writes its first graveyard row while the binary family still has no validation controls | `grep -i polymarket backtest/validate_harness.py` returns nothing | at module #2 start |

K6 is the one I would actually put money on failing, because it is the cheapest
to skip and the most expensive to discover late. Module #1 has already been
running against a gate that does not cover it.

---

## 10. Open questions for Aym

1. **Do you accept payoff-family over venue as the module carve?** It means
   "the crypto module" and "the equities module" are one engine with two
   adapters, which is a different mental model than the one you described. It is
   also where most of the reuse you asked for actually is.
2. **Gate 1's number.** What t-statistic on what minimum n does the Polymarket
   structural family have to clear before crypto starts? I deliberately did not
   invent one.
3. **Is Gate 0 (the harness hole) worth doing now, before the structural
   proof?** My view: yes, and it is small. But it is unplanned work against a
   roadmap that already has a scheduled restart, so it is your call.
4. **Options: accept the cut?** The argument in 6.4 is that D-342 R5 rejects it
   on our own stated filter. If you want it back on the roadmap, the way back is
   naming an identity, not naming a timeframe.
5. **When does the `engine/` -> `core/` + `payoff/` + `modules/` rename happen?**
   My recommendation: at module #2 start, not before. Doing it now is churn
   against a shared working directory with live processes.
6. **Prop firm: which one, and what are its actual rules?** Section 5 assumes
   trailing-daily-drawdown-with-session-reset. Different firms differ, and the
   rules are the spec for `prop_rules.py`. This is the one place a real document
   from a real vendor beats any design I can write.
7. **Does the cross-book pooling policy become a declared field?** (Section 4,
   item 1.) It is currently prose in a wake-up file, and prose is the format
   that loses to a rewrite.

## Open calls for Raven

- **A2 is yours:** DECISIONS.md and CONVENTIONS.md stay singular under any
  option. If Aym picks B later, that constraint has to survive the split, and
  the mechanism for that is not obvious. Worth deciding before B, not during.
- **Gate 0 needs a D-number** if Aym accepts it, and it needs to NOT land on the
  ~03:45 2026-08-20 restart, which is already fully loaded.
- The `asset_family_for_slug` re-cut (1.4) touches `engine/risk/constraints.py`,
  which is wired into the entry path as of D-343 and is inactive until the
  restart-after-the-one. Sequencing it against those two restarts is a
  coordination call, not a code call.
