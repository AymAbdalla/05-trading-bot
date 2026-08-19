# Calibration tape: spec (forge brief v2 priority 1, 029 condition (b))

**Session:** `cody-keying-prep`, 2026-08-19 ~05:40 EDT. **Design only. No code
was written this session.** Owner is the scheduled keying-restart session
(~03:45 EDT 2026-08-20), per D-340 clause (3).

## 1. What forge brief v2 asks for

Sample EVERY market in the universe on every poll, not just the ones a strategy
selected. Write `(market_id, ts, mid, best_bid, best_ask, seconds_remaining)`.
Stamp `(market_id, resolved_outcome)` once when the window closes. Bucket by
price, plot realised frequency. The instrument answers: **when the book says
0.20, does it settle 20% of the time?** That is a forecast-free calibration
curve, and it is 029's condition (b).

## 2. The brief's premise is half right. Measured this session.

Brief v2 says "`market_tape` already holds the shape; what is missing is the
resolution stamp and the unselected sampling." The shape claim is right. The
"what is missing" list is **incomplete**, and the gap is larger than one column.

Live `market_tape`, re-derived read-only from `db/trading.db`:

| quantity | value |
|---|---|
| columns | `id, market_id, ts, mid, best_bid, best_ask, source, condition_id, complement_id` |
| rows | 15,390 |
| distinct `market_id` | **56** |
| span | 6.55 hours (unix 1787107860.97 to 1787131441.13) |
| write rate | ~2,350 rows/hour |
| `source` split | `mid` 13,308, `ask` 2,087 |
| rows with `complement_id` | 4,922 |
| `market_id LIKE '%-5m-%'` | **0** |
| `market_id LIKE '%-15m-%'` | **0** |

**Finding 1: `market_tape.market_id` is a CLOB token id, not a market slug.**
All 56 values are 77-digit integers. No slug-shaped value exists in the column,
so the two zeros above are not evidence about durations. Any spec that says
"market_id" must say which identifier it means. This one means token id.

**Finding 2, and this is the blocking one: `market_tape` deliberately EXCLUDES
the crypto up/down universe.** `strategies/polymarket/dip_arb.py:889` reads
`persist = not ctx.is_crypto_window`, and `is_crypto_window` is True for
`market_type == 'crypto_updown'` (`base.py:217`). The docstring at `:869-877`
states the reason plainly: "a crypto token id is new every window ... writing it
anyway would multiply `market_tape` volume for no benefit."

So the calibration tape's entire target universe is the one universe
`market_tape` is wired to skip. This is not a bug in `dip_arb`; it is a correct
decision for `dip_arb`'s own purpose that the calibration tape reverses. **The
tape cannot be produced by flipping that flag and adding a column.**

**Finding 3: `observe()` reads `ctx.market` only** (`dip_arb.py:887`), never
`ctx.market_15m`. Even with the flag flipped, the 15m market would never be
sampled. The very market 029 needs is the one this writer cannot see.

**Finding 4: the writer is a STRATEGY.** `market_tape` is filled by
`DipArb.tape` (`PriceTapeByToken._persist_row`, `dip_arb.py:425-448`), wired at
`shadow_loop.py:1321`. A universe-wide calibration instrument owned by one
strategy is fragile by construction, and `dip_arb` is already on the env B drop
list (open item 6). An instrument that dies when a strategy is de-registered is
not an instrument.

**Finding 5: `seconds_remaining` does not exist** in the table, and `best_bid` is
NULL on all 2,087 `source='ask'` rows.

## 3. Consequence: this is a NEW writer, not a schema delta

The honest version of the brief's "schema delta" is: **a new table and a new
loop-level sampler.** Extending `market_tape` in place would require reversing
the persist rule for the crypto path, which multiplies `dip_arb`'s tape,
changes the meaning of a table 037 and 026 are mid-measurement on, and couples
029's gate to a strategy's lifecycle.

**`market_tape` must not be modified before the 24h complement window closes.**
`complement_id` records only from the 03:28 EDT 2026-08-19 restart; 4,922 rows
carry it today. 026 and 037 depend on that window being uninterrupted until
~03:28 EDT 2026-08-20. Touching this table's schema or write rate before then
contaminates the exact measurement it was created for.

## 4. Proposed schema

Two tables, both new, both additive. Nothing existing is altered.

```sql
-- One row per (token, poll). Written for EVERY market in the universe,
-- selected or not.
CREATE TABLE IF NOT EXISTS calibration_tape (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id           TEXT NOT NULL,     -- CLOB token id, the join key
    market_slug        TEXT NOT NULL,     -- e.g. btc-updown-15m-1787064300
    market_duration    TEXT NOT NULL,     -- '5m' | '15m'; same vocabulary as signals.market_duration
    outcome_side       TEXT,              -- 'Up' | 'Down'; NULL if unreadable
    condition_id       TEXT,              -- Gamma join key
    ts                 REAL NOT NULL,     -- unix seconds, poll time
    window_ts          INTEGER NOT NULL,  -- the window this market settles
    seconds_remaining  REAL,              -- window_ts + duration - ts; NULL if not derivable
    mid                REAL,
    best_bid           REAL,
    best_ask           REAL,
    book_depth_levels  INTEGER,           -- levels seen; 0 is a real reading, NULL is "not read"
    selected           INTEGER NOT NULL   -- 1 = some strategy evaluated it this cycle, 0 = unselected
);
CREATE INDEX IF NOT EXISTS idx_cal_tape_token_ts ON calibration_tape(token_id, ts);
CREATE INDEX IF NOT EXISTS idx_cal_tape_window   ON calibration_tape(window_ts, market_duration);

-- Write-once. One row per token, ever.
CREATE TABLE IF NOT EXISTS calibration_resolution (
    token_id         TEXT PRIMARY KEY,
    market_slug      TEXT NOT NULL,
    market_duration  TEXT NOT NULL,
    window_ts        INTEGER NOT NULL,
    resolved_outcome TEXT NOT NULL,       -- 'UP' | 'DOWN', verbatim from the oracle
    won              INTEGER NOT NULL,    -- 1 if THIS token is the winner, else 0
    resolved_ts      REAL NOT NULL,       -- when we observed it, not when it settled
    source           TEXT NOT NULL        -- 'oracle'
);
```

`selected` is the column that makes this an unselected-market tape rather than
another selection-biased one. Without it the table cannot answer the question it
exists for. Convention 20: `selected = 0` must be written, not omitted.

`book_depth_levels` is there because the D-339 trap (section 6) is a
depth problem wearing a price disguise.

## 5. Where the resolution stamp lands, and why it is write-once

`PRIMARY KEY (token_id)` plus `INSERT OR IGNORE`. A second stamp for a token is
silently dropped by the key, which is the point: a resolution that changes is a
data error, and an `INSERT OR REPLACE` would hide it. Count the ignored inserts
into `health` so a changed resolution is COUNTED rather than lost
(convention 20).

Source: `resolved_windows_checked` (`context.py:193-235`) already reads the
oracle's `resolved_outcome` per window and already splits its failures into
`read_failed` / `not_listed` / `unresolved` / `not_binary`. It is the right
source and it must be used through the `_checked` variant, so an unresolved
window is recorded as unresolved rather than dropped.

The stamp runs on a window that has **closed**, so it lags the tape by one
window. `resolved_ts` records observation time, not settlement time; conflating
them is how a calibration curve acquires a lookahead.

## 6. The trap, carried verbatim

**Read the book, never the gamma summary fields.** Gamma's `bestBid`/`bestAsk`
read 0.63/0.64 while the live CLOB book for the same token was 0.06/0.08 three
minutes from expiry. A calibration curve built on the gamma summary would be
measuring Gamma's staleness and calling it market miscalibration, and it would
be most wrong exactly where the curve matters most, near expiry at extreme
prices.

`best_bid` / `best_ask` / `mid` MUST come from `fetch_orderbook`
(`context.py:348, 363-366`; `shadow_loop.py:2112`). Never from `market.raw`.
This is a hard requirement, not a preference.

## 7. Volume

Measured this session: the loop writes ~26,707 `signals` rows/hour at 17
strategies x 3 assets, which is roughly 520 cycles/hour, about one every 7
seconds.

Minimum universe: 3 assets x {5m, 15m} x 2 outcomes = **12 tokens per cycle**.

- ~6,240 rows/hour, ~150,000 rows/day.
- For comparison, `market_tape` today writes ~2,350 rows/hour, so this is
  roughly 2.7x the existing tape rate.

That is affordable but it is not free, and it must be stated in the restart
brief rather than discovered as disk growth. If the universe is widened beyond
the current window (adjacent unselected windows, the 16-window lookback), the
rate scales linearly and needs its own number before it ships.

## 8. Sharing the ONE restart

D-339 clause (3) and D-340 clause (3) put this and the 15m keying in the same
single restart, ~03:45 EDT 2026-08-20, after the 24h complement window warms at
~03:28. Order within that restart:

1. Verify the complement window closed intact (`15m-keying-design.md` V7).
2. Apply the `signals.market_duration` migration and writer (keying design 3.3-3.5).
3. Create the two calibration tables and the loop-level sampler.
4. Apply the env B whitelist corrections (open item 6), since this is the natural
   restart they were waiting for.
5. Convention 1: harness exits 0 and the suite is re-run BEFORE the result is
   durable.
6. Restart once. Record the changeover timestamp `T` in the handoff; every
   verification query in both docs depends on it.

`calibration_tape.market_duration` and `signals.market_duration` share one
vocabulary on purpose. That shared key is what lets 029's gate join a signal to
the unselected universe it was chosen out of, and it is the reason these two
changes belong in the same restart rather than in two.

## 9. Kill condition for the instrument itself

Convention 6. If, after 48 hours of tape, fewer than 500 tokens have BOTH a
`calibration_tape` row and a `calibration_resolution` row, the sampler is not
working and the instrument is declared NOT_TESTED rather than reported as a flat
calibration curve. A curve built on an unmeasured tape is worse than no curve.

## 10. What is NOT resolved here

- **Nobody has decided whether `dip_arb`'s `market_tape` and this tape should
  eventually merge.** Two price tapes in one database is a duplication cost.
  Deliberately not decided in this session: `market_tape` is mid-measurement for
  037 and 026 and must not be touched before ~03:28 2026-08-20. Flagged for Raven.
- The exact universe width (current window only, or the lookback windows too)
  needs a decision before implementation. Section 7 prices the narrow version only.
- `tests/test_schema_matches_feed_modules.py:62` asserts `market_tape`'s
  declaration in `db/schema.sql` matches `dip_arb.SCHEMA_SQL`. If the new tables
  are ever declared in two places, they must be added to that test's table list
  in the same commit, or the drift it exists to catch will be invisible for them.
