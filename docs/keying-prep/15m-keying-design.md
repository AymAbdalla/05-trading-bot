# 15m signal keying: design (D-339 clause (3))

**Session:** `cody-keying-prep`, 2026-08-19 ~05:35 EDT. **Design only. No code
was written this session.** Implementation lands in the ONE restart Raven
schedules for ~03:45 EDT 2026-08-20.

Read `signals-consumer-audit.md` first. Every claim below rests on its
measurements.

## 1. Constraints, carried verbatim from D-339 clause (3)

- Additive recording change only (a market-duration key on the signal row, or
  `pair` keyed to the actual evaluated market slug).
- Must NOT change the semantics of existing rows.
- Must NOT contaminate the 24h complement window. The 037/026 re-derivation
  warms ~03:28 **2026-08-20**. The keying change lands **AFTER** that window,
  together with the calibration-tape work, in **ONE restart Raven schedules**,
  never mid-window.
- Verify against every consumer of `signals` first (5m universe counts,
  corridor constructs, existing gates).
- It unblocks 029's gate DATA PIPELINE. It does not by itself satisfy (b) or (c).

## 2. Decision: option A, a new nullable column. Option B is rejected.

D-339 offers two shapes. They are not equivalent and the audit settles it.

**Option B, repointing `pair` to the evaluated slug, is rejected on three
independent grounds:**

1. **It is ill-defined for the strategies that need it most.** A corridor
   evaluation is ONE signal row over TWO markets: `corridor_collector.py:292-295`
   builds a 15m leader leg and a 5m opposite leg in a single decision. There is
   no single "the evaluated market slug" to repoint `pair` to.
2. **It silently moves `agents/critic.py:393`/`:703`**, which is an undeclared
   join between `positions.pair` and `signals.pair`. Repointing changes the
   premature-exit series for reasons that have nothing to do with the market
   (audit 4.1).
3. **It silently contaminates `backtest/analyze_shadow_fair_value_arb.py:760`**,
   which parses a window ts off the `pair` tail. A 15m slug has the same tail
   shape, so the parse succeeds and the answer is wrong (audit 4.3).

**`tf` is also rejected as the carrier.** It reads `'5m'` on all 699,660 rows
because `shadow_loop.py:850` writes that literal unconditionally. Making it
truthful going forward would leave the column meaning "hard-coded constant"
before a timestamp and "measured duration" after it, inside one column with no
way to tell which. That is exactly the semantics change D-339 forbids.

## 3. The change

### 3.1 One column

```sql
market_duration TEXT      -- NULLABLE, NO DEFAULT
```

Values written by the loop: `'5m'`, `'15m'`, `'mixed'`. Never `''`.

- `'5m'`  - every leg (or the evaluation, on a skip) is on the 5m window market.
- `'15m'` - every leg is on the native 15m market. `PM_longshot_fade_hold_to_resolution`
  is exactly this: a single-leg strategy whose only Leg carries
  `market_slug=slug_15` (`longshot_fade_hold_to_resolution.py:791-796`). It will
  populate this value from the first cycle after the restart.
- `'mixed'` - the decision spans both, which is the corridor family
  (`corridor_collector.py:292-295`, `corridor_pair_live.py:393`).

### 3.2 NULL means "not recorded". It must not be defaulted.

**`NULL` with no `DEFAULT` is load-bearing and non-negotiable.** The precedent is
in this repo and it went the other way: `fill_was_maker INTEGER NOT NULL DEFAULT 0`
(`shadow_loop.py:720`) backfilled every pre-existing row to a value that reads as
a measurement, and CLAUDE.md now carries a standing correction warning that
"2,140 of 2,140 non-null" cannot be trusted because an unknown share is
migration backfill. A `DEFAULT '5m'` here would repeat that mistake exactly: it
would fabricate 699,660 measurements that nobody made, and it would be
indistinguishable from real ones forever.

`NULL` = "this row predates the key". That is the honest value and it satisfies
"must not change the semantics of existing rows" literally: existing rows gain a
column that says nothing about them.

### 3.3 Where the value is computed

`engine/polymarket/shadow_loop.py`. The information is already in hand at every
call site; nothing new is fetched.

- **Entry path, `:2431-2432`.** The loop already computes
  `leg_slug = leg.market_slug or slug` per leg and already compares
  `leg.market_slug == slug_15` at `:2434-2437`. The duration is the set of those
  comparisons across `decision.legs`, collapsed to one of the three values.
  It is passed into the `record_signal` call at `:2524`.
- **Skip path, `:2154` (`_log_and_count`).** A skip has no legs. The duration is
  taken from the strategy's own declared scope. Where the strategy cannot say,
  the value is **NULL, not `'5m'`** (convention 20: an unknown is a missing
  number, not a default).
- **Maker path, `:2886` (`_record_maker_entry`).** `order.market_slug` is the
  real market of the resting order. Derive from it directly.
- **Sink, `:830-854` (`record_signal`).** New keyword-only parameter
  `market_duration: Optional[str] = None`, added to the INSERT's explicit column
  list. Default `None` so `engine/db.py:63`'s crypto path and every test caller
  is unaffected.

### 3.4 Migration

`db/schema.sql:21` declares `CREATE TABLE IF NOT EXISTS signals`, which is a
**no-op** against the live table. An `ALTER TABLE` is required, following the
pattern already in the file:

- Add `_SIGNALS_DURATION_COLUMNS = (('market_duration', 'TEXT'),)` and
  `_migrate_signals_duration_column()` on `ShadowStore`, shaped on
  `_migrate_positions_fill_provenance_column` (`shadow_loop.py:723-750`) **minus
  its `NOT NULL DEFAULT`**.
- Call it in `_ensure_schema` at `:796-798`, **before** `executescript`, for the
  reason documented at `:686-696`: a fresh db has not created the table yet, an
  existing one needs the ALTER first.
- Add the column to `db/schema.sql`'s `CREATE TABLE` so a fresh db is born with it.
- Guard with `PRAGMA table_info`; SQLite has no `ADD COLUMN IF NOT EXISTS`.
- `ALTER TABLE ADD COLUMN` with no default does not rewrite the 699,660 existing
  rows. It is a header-only change and is fast.

### 3.5 The one consumer that must change in the same commit

`agents/forge_shadow_eval.py:999` selects an explicit column list. Add
`market_duration` to it. Without this, 029's gate is unblocked in the database
and still unrunnable through the tool that evaluates 029 (audit 4.2).

### 3.6 Explicitly out of scope

No existing row is updated. No backfill from `features_json.market_slug_15m`,
even though 57,505 rows carry it. Backfilling would put derived values in the
same column as observed ones and recreate the `fill_was_maker` problem. Those
rows stay NULL and are used as a **control** instead (section 6).

## 4. Sequencing. This is the part that can go wrong.

1. **Do not restart before ~03:28 EDT 2026-08-20.** `complement_id` only records
   from the 03:28 restart, so the 24h complement window for 037/026 is not warm
   until then. A restart before that resets the window and destroys the only
   instrument that can settle 037's NOT_TESTED.
2. The keying change, the calibration tape (`calibration-tape-spec.md`) and the
   env B whitelist corrections ride the **same single restart**, ~03:45 EDT
   2026-08-20, scheduled by Raven. Not one restart each.
3. Convention 13: edits do not reach a running loop. Python snapshots source at
   import, so the running loop (71360/71394) will not pick this up and must not
   be signalled to try.
4. Convention 1: `backtest/validate_harness.py` must exit 0, and the suite must
   be re-run, before the change is durable.

## 5. Trap to carry into the implementation

**Read the book, never the gamma summary fields.** Gamma's `bestBid`/`bestAsk`
read 0.63/0.64 for a token whose live CLOB book was 0.06/0.08 three minutes from
expiry. Any code added here that wants a price for a 15m market must go through
`fetch_orderbook` (`context.py:363-366`, `shadow_loop.py:2112`), never
`market.raw`'s summary fields. This applies with full force to the calibration
tape, which is a price instrument by definition.

## 6. Verification queries, to run AFTER the restart

The changeover timestamp `T` is the restart time. Record it in the restart
handoff; every query below depends on it.

**V1. The column exists and did not rewrite history.**
```sql
SELECT COUNT(*) FROM signals WHERE market_duration IS NULL;   -- expect 699,660 + any pre-T growth
SELECT COUNT(*) FROM signals WHERE ts <  T AND market_duration IS NOT NULL;  -- MUST be 0
```
A non-zero second result means a DEFAULT leaked in. Stop and revert.

**V2. The key is actually populated after T.**
```sql
SELECT market_duration, COUNT(*) FROM signals WHERE ts >= T GROUP BY 1;
```
Expect `5m`, `15m`, `mixed` and a NULL bucket only from strategies that cannot
declare scope on a skip. A 100% NULL result after T means the writer is not
wired; that is a failure, not a quiet universe.

**V3. `pair` semantics are unchanged (the additive contract).**
```sql
SELECT COUNT(*) FROM signals WHERE ts >= T AND pair LIKE '%-15m-%';  -- MUST be 0
```
Option A does not touch `pair`. Any non-zero here means option B was implemented
by accident.

**V4. Cross-check against the pre-existing ground truth.**
`PM_longshot_fade_hold_to_resolution` is single-leg and pure 15m
(`longshot_fade_hold_to_resolution.py:791-796`), so:
```sql
SELECT market_duration, COUNT(*) FROM signals
 WHERE ts >= T AND strategy_id = 'PM_longshot_fade_hold_to_resolution' GROUP BY 1;
```
**MUST be 100% `15m`.** Anything else means the derivation at `:2431-2432` is
wrong. This is the single strongest test available, and it exists because that
strategy's 6,087 pre-T rows already carry `market_slug_15m` in `features_json`
while every one of them is keyed `-5m-` in `pair`.

**V5. The corridor family must read `mixed`, not `15m`.**
```sql
SELECT strategy_id, market_duration, COUNT(*) FROM signals
 WHERE ts >= T AND strategy_id LIKE 'PM_corridor%' GROUP BY 1,2;
```
Expect `mixed` on the entry rows. A `15m` here means the leg scan collapsed a
two-market decision to one market.

**V6. Consistency with `positions`.**
```sql
SELECT s.market_duration, COUNT(*) FROM positions p JOIN signals s ON p.signal_id = s.id
 WHERE p.opened_ts >= T AND p.pair LIKE '%-15m-%' GROUP BY 1;
```
Every position on a `-15m-` market must hang off a signal keyed `15m` or `mixed`.
A `5m` row here is the original bug, still present.

**V7. The 24h complement window is intact.**
```sql
SELECT MIN(ts), MAX(ts), COUNT(*) FROM market_tape WHERE complement_id IS NOT NULL;
```
The minimum must still be the 03:28 2026-08-19 restart, not the new one. If it
moved, the window was reset and 037/026 must wait another 24h. Re-derived this
session: 4,922 rows currently carry `complement_id`.

## 7. Rollback

`ALTER TABLE ... DROP COLUMN` is available in modern SQLite but is not needed: a
column nobody reads is inert, and every reader added by this change treats it as
optional. Rollback = revert the writer, leave the column. Do not drop it, because
dropping it would delete post-T measurements that nothing else records.

## 8. What this does NOT do

- It does **not** satisfy 029's condition (b), the unselected-market calibration
  tape, or condition (c), the forecast-free direction check. D-339 and D-340 are
  explicit on this. It unblocks the DATA PIPELINE only.
- It does **not** make any past `-15m-` = 0 measurement wrong. Those measured a
  keying gap and they still do.
- It does **not** create 15m evaluations. Three strategies already evaluate the
  15m market every cycle; this makes that fact queryable.
