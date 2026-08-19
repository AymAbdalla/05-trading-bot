# `signals` consumer audit (D-339 clause (3) prerequisite)

**Session:** `cody-keying-prep`, 2026-08-19 ~05:30 EDT. **HEAD at audit:** `a8c75bc`.
**Method:** grep of `engine/`, `strategies/`, `agents/`, `scripts/`, `tests/`,
`dashboard/`, `tools/`, `research/`, `backtest/` plus read-only queries against
`db/trading.db`. Convention 31: every number below was measured this session, not
quoted. No code was changed.

## 1. Measurements taken this session (read-only)

| quantity | value |
|---|---|
| `signals` rows | 699,660 |
| `pair LIKE '%-updown-%'` | 575,144 |
| `pair LIKE '%-5m-%'` | 575,144 |
| `pair LIKE '%-15m-%'` | **0** |
| distinct `tf` values in the whole table | **`5m` only, on all 699,660 rows** |
| rows whose `features_json` carries `market_slug_15m` | **57,505** |
| `positions` rows | 2,201 |
| `positions.pair LIKE '%-15m-%'` | **24** |
| `positions.pair LIKE '%-5m-%'` | 2,174 |

Two of these are new and they change the shape of the problem.

**(i) `tf` is the literal string `'5m'` on every row in the table**, written
hard-coded at `engine/polymarket/shadow_loop.py:850`. It is not a measurement of
anything. It cannot be repurposed as the duration key without changing what an
existing row means.

**(ii) The 15m identity is already on 57,505 signal rows, inside
`features_json` as `market_slug_15m`**, put there by
`corridor_collector.py:172`, `corridor_pair_live.py:233`,
`longshot_fade_hold_to_resolution.py:621`. Split by strategy:
`PM_corridor_pair` 28,449, `PM_corridor_collector` 18,095,
`PM_longshot_fade_hold_to_resolution` 6,087, `PM_corridor_pair_live` 4,874.

So the tape is **not blind** to the 15m market. It is **unqueryable by key**.
That is a strictly smaller gap than "the 15m universe is unrecorded", and it
gives the post-restart verification a ground truth to check against (section 5).

## 2. Where the 5m slug is stamped onto a signal row

The gap is one line.

- `engine/polymarket/shadow_loop.py:2393` -> `slug = getattr(ctx.market, 'slug', None)`.
  `ctx.market` is **always** the 5m market (`context.py:344`, `shadow_loop.py:1956`).
- `engine/polymarket/shadow_loop.py:2432` -> `leg_slug = leg.market_slug or slug`.
  A leg **can** carry the 15m slug, and `base.py:256` documents
  `Leg.market_slug` as "None = this window's 5m market". `record_entry` writes
  the leg's own slug, which is why 24 `positions` rows are `-15m-`.
- `engine/polymarket/shadow_loop.py:2524` -> `record_signal(..., market_slug=slug, ...)`.
  **The signal row gets `slug`, never `leg_slug`.** One signal, N legs, and the
  slug written is the 5m one even when the evaluated leg was the 15m market.
- `engine/polymarket/shadow_loop.py:850` -> `market_slug or 'POLYMARKET'` into
  `pair`, and the literal `'5m'` into `tf`.

Three writers call `record_signal`: `:2154` (`_log_and_count`, every skip),
`:2524` (taker entry), `:2886` (`_record_maker_entry`). All three pass a slug
that originates at `:2393` or from `order.market_slug`.

`engine/db.py:63` `insert_signal` is the non-Polymarket writer and takes
`pair`/`tf` from its caller's dict.

## 3. Every consumer of `signals`, and whether an additive key moves it

`READS pair` is the only column that matters here; a consumer that never reads
`pair` cannot be moved by a key that sits beside it.

| # | file:line | what it reads | reads `pair`? | additive key changes it? |
|---|---|---|---|---|
| 1 | `engine/db.py:68` | writer, explicit column list | writes | NO |
| 2 | `engine/db.py:84,90` | `UPDATE ... SET acted / skip_reason WHERE id` | no | NO |
| 3 | `engine/executor.py:96` | `UPDATE ... skip_reason='engine_restart'` | no | NO |
| 4 | `engine/executor.py:174,272,299` | `SELECT ts / features_json / strategy_id WHERE id` | no | NO |
| 5 | `engine/polymarket/shadow_loop.py:846` | writer, explicit column list | writes | NO |
| 6 | `agents/critic.py:374-376` | `SELECT id, ts, pair, strategy_id, ... WHERE id IN` | **YES** | NO (fetch by id) |
| 7 | `agents/critic.py:384-394` | `SELECT pair, ts, features_json`, builds `post_exit[(pair, side)]` | **YES** | **SEE 4.1** |
| 8 | `agents/critic.py:402-406` | `GROUP BY strategy_id, acted, skip_reason` | no | NO |
| 9 | `agents/forge_shadow_eval.py:999` | `select ts, pair, tf, strategy_id, pattern, direction, confidence, acted, skip_reason, mode` | **YES** | **SEE 4.2** |
| 10 | `scripts/shadow_summary_lib.py:414` | `SELECT strategy_id, acted, skip_reason, ts` | no | NO |
| 11 | `scripts/weekly_shadow_summary.py:65` | `count(*) ... GROUP BY strategy_id` | no | NO |
| 12 | `scripts/vault_refresh.py:197,205,233,244,267,273` | strategy_id / skip_reason / acted aggregates | no | NO |
| 13 | `dashboard/db_reader.py:384` | `... acted, skip_reason, mode FROM signals` + explicit list | no | NO |
| 14 | `backtest/analyze_shadow_fair_value_arb.py:760` | parses the window ts off the `pair` tail | **YES** | **SEE 4.3** |
| 15 | `tests/test_polymarket_multi_asset.py:473,500` | `SELECT DISTINCT pair` / `strategy_id, pair` | **YES** | **SEE 4.4** |
| 16 | `tests/test_scanner.py` (5 sites), `test_maker_fill_wiring.py:419`, `test_polymarket_shadow_loop.py:391,439,691` | `SELECT * FROM signals` | implicit | **SEE 4.5** |
| 17 | `tests/test_vault_refresh.py:67,72,75` | `INSERT INTO signals VALUES (?x12)`, positional | positional | **SEE 4.5** |
| 18 | `tests/test_critic.py:53`, `tests/test_forge_shadow_eval.py:30`, `tests/test_vault_refresh.py:31` | own local `CREATE TABLE signals` | n/a | **SEE 4.6** |

**No production consumer anywhere in the tree does a `LIKE '%-5m-%'` universe
count.** Grep found that pattern only in `docs/DECISIONS.md`, three handoffs, and
Raven's own brief. The "5m universe count" the brief asks about is an
ad-hoc analyst query, not code. It is therefore not a code-mitigation item, but
it IS a reporting item: see 4.7.

**No consumer joins `market_tape` to `signals`.** `market_tape.market_id` is a
CLOB **token id**, not a slug (verified: 56 distinct values, all 77-digit
integers), so no such join exists or could exist on the slug.

## 4. The consumers that move, and the required mitigation

### 4.1 `agents/critic.py:393` + `:703` - a cross-table join on `pair`. BLOCKING for option B, safe under option A.

`post_exit` is keyed `(signals.pair, outcome_side)` at `:393` and looked up at
`:703` with `row.get('pair')` where `row` is a **`positions`** row. This is an
undeclared join between `positions.pair` and `signals.pair`.

Today: 24 positions carry a `-15m-` `pair` and **no** signal row does, so those
24 always fall through to `no_post_exit_price_observation`. The premature-exit
check is silently unavailable for every 15m corridor leg. Convention 20: that is
a missing number, and it is a second, independent casualty of the same keying
gap.

- Under **option A** (new column, `pair` untouched): unchanged. Still 24 rows
  with no observation. No regression, no silent repair.
- Under **option B** (repoint `pair` to the evaluated slug): those lookups would
  begin matching, AND the 5m side would break, because a corridor signal that is
  repointed to the 15m slug stops keying the 5m series it currently feeds. The
  critic's premature-exit rate would move for reasons unrelated to the market.

**Mitigation:** choose option A. If option B is ever chosen instead, the critic
must be dual-keyed and the change must be reported as a break in the premature
exit series, not absorbed silently.

### 4.2 `agents/forge_shadow_eval.py:999` - forge stays blind unless the column is added to this SELECT

Explicit column list, so an additive column does not break it, and does not
reach it either. Forge's own decision reader would keep seeing a universe with
no 15m rows even after the keying lands.

**Mitigation:** the restart session must add the new column to this SELECT in the
same change. Otherwise 029's gate is unblocked in the database and still
unrunnable through the tool that evaluates it. This is a required part of the
keying change, not a follow-up.

### 4.3 `backtest/analyze_shadow_fair_value_arb.py:760` - parses the window ts off the `pair` tail

Docstring: "`pair` looks like btc-updown-5m-1787022000; the tail is the window
ts". A 15m slug has the identical tail shape, so the parse would not crash, it
would silently mix 15m windows into a 5m analysis.

- Under **option A**: unchanged, `pair` still only ever holds 5m slugs on new rows.
- Under **option B**: silent contamination. No exception, wrong answer.

**Mitigation:** another reason for option A. If option B is ever chosen, this
parser needs an explicit duration filter first.

### 4.4 `tests/test_polymarket_multi_asset.py:473-482, 499-504`

`:477` asserts every asset produced a `-updown-5m-` slug; `:482` asserts
`asset_for_slug(s) in ASSETS` for every distinct `pair`. Option A leaves both
green. Option B keeps them green only incidentally (`asset_for_slug` already
handles a 15m slug, proven at `:296`), which is worse: the tests would pass
while the keying meaning changed underneath them.

**Mitigation:** under option A, add one assertion that the new column is
populated and that `pair` still carries the 5m slug, so the additive contract is
enforced by a test rather than by intent.

### 4.5 `SELECT * FROM signals` and the one positional INSERT

Ten test sites use `SELECT *`. All ten index the result by column name, so an
extra column is additive for them. `tests/test_vault_refresh.py:67,72,75` uses a
**positional** `INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)` with
exactly 12 placeholders, which would raise on a 13-column table - but that test
builds its own `CREATE TABLE signals` at `:31`, so it is insulated.

**Mitigation:** none required, but do not "tidy" that local schema into the real
one. Its isolation is what keeps the positional insert valid.

### 4.6 Three tests carry their own hand-written `signals` schema

`tests/test_critic.py:53`, `tests/test_forge_shadow_eval.py:30`,
`tests/test_vault_refresh.py:31`. They will NOT gain the new column.

**Mitigation:** any production reader that requires the new column by name will
fail against these fixtures. Readers must treat the column as optional (NULL
tolerant), or these three fixtures must be updated in the same change. Decide
explicitly; do not discover it at test time.

### 4.7 Every prior `-15m-` = 0 measurement stays true and stays quotable

D-338, D-339 and three handoffs record `pair LIKE '%-15m-%'` = 0. After the
change that query starts returning non-zero **for rows written after the
restart only**. The zero was never wrong; it measured a keying gap.

**Mitigation:** the restart handoff must state the changeover timestamp so no
later session reads the discontinuity as a venue event. Re-derived here
unchanged at 699,660 / 575,144 / **0**.

## 5. Ground truth available for post-restart verification

57,505 existing rows carry `market_slug_15m` in `features_json`, and 24
`positions` rows carry a `-15m-` `pair`. Both predate the change and neither is
touched by it, so both are usable as controls. The verification queries built on
them are in `15m-keying-design.md` section 6.

## 6. Not resolved by this audit

- Whether `agents/forge_shadow_eval.py:999` should expose the new column as a
  column or as a derived field. Named as required work; not designed here.
- The three local test schemas (4.6) need a decision, taken by the restart
  session, not by this audit.
