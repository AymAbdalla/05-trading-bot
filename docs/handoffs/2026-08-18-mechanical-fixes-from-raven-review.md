# Mechanical fixes from Raven's 2026-08-18 review

**By:** Cody, 2026-08-18 ~06:30-07:00
**Instruction file:** `docs/handoffs/from-raven/2026-08-18-mechanical-fixes-from-review.md`
**Nothing committed.** Tree left for review, per instruction.

## Headline: 4 of 6 tasks were already done by a concurrent session

PIDs **38911** and **45175** were live `claude -p` sessions editing this tree
the whole time I worked. Tasks 2, 3, 4 and 5 were already complete when I got to
them. I verified each rather than assuming, and the verification is the
deliverable for those four. Tasks 1 and 6 were real work.

This is convention 21 in its most expensive form: **I raced a concurrent session
twice on the same file** and had to reconcile both times (details below).

---

## Task 1: skip classification — DONE, but I did NOT do what was asked

**Raven asked:** classify `no_recent_liquidation` and
`liquidation_below_second_lock_min` as **DATA_BLOCKER**, on the rationale "the
strategy cannot evaluate because it depends on liquidation feed data that does
not exist yet (the feed table has 0 rows)".

**I kept both GENUINE.** The rationale describes a different reason string.

The measurement, not an opinion. Both returns sit BELOW this line at
`strategies/polymarket/near_liq_trigger.py:960`:

```python
if not window.ok:
    return decide('SKIP', window.reason, **feats)   # the four NOT_TESTED names
```

So by the time either of the two reasons can be reached, the liquidation feed
has already been proved present, non-empty for this symbol, long enough and
fresh. With `liquidations` at **0 rows** — which I confirmed,
`select count(*)` is 0 — the strategy emits `liquidation_feed_empty`, which is
**already classified DATA_BLOCKER** at line 391. Neither of Raven's two keys is
reachable in the state her rationale describes.

Classifying them DATA_BLOCKER would report "could not run" for a lock that ran
and returned false — the convention 11 inversion, pointing the other way.

Two things that make this cheap to overrule:

1. **Measured consequence today is ZERO rows either way.** The `liquidations`
   table is empty (Bybit-only tape, quiet), so no row can currently carry
   either key. This costs nothing to reverse.
2. **A concurrent session reached the same conclusion independently**, with the
   same code-path argument, and had already added both keys as GENUINE before I
   finished. That is two independent readings agreeing against the instruction.

I removed my duplicate block and left theirs, adding only the fact theirs
lacked: the 0-row measurement and an explicit note that this contradicts
Raven's instruction. **Needs a D-number either way.**

### What I actually added to `SKIP_CLASSIFICATION`

Four keys, all from `weather_arb.py`, which a concurrent session was writing
while I worked. These were the real failures:

| key | class | why |
|---|---|---|
| `global_temperature_market_excluded` | GENUINE | universe filter. The question WAS read; the product was declined for being a global-temperature market rather than a city one. Exists so it never pools into `resolution_station_unknown`. |
| `source_reporting_precision_unknown` | DATA_BLOCKER | rules text does not state the source's reporting precision, so the ladder rung edges are a guess |
| `source_precision_finer_than_ladder_step` | DATA_BLOCKER | precision IS known (0.1 native, the 66 Hong Kong markets) and that is what makes it unusable: `[26.5, 27.5)` and `[27.0, 28.0)` are both "27C" and Polymarket has not published which. The missing input is the rung EDGE. |
| `daily_extreme_not_priced_by_point_in_time_model` | DATA_BLOCKER | the module's own comment invokes convention 11 for it: not "no edge here", but "this model cannot price a high/low question and will not pretend to" |

`tests/test_forge_shadow_eval.py`: **44 passed**.

### Caveat you should not skip

`weather_arb.py` is under active edit by another session. The last of those four
reasons appeared *after* I had already made the test green once. **This test
will go red again the moment that session adds another `decide('SKIP', ...)`
literal.** That is the AST guard working as designed, not a regression.

---

## Task 2: `test_fair_value_arb_variants.py` — ALREADY DONE, verified

**87 passed.** No absolute count remains. `TestRegistry` already asserts
relative position: `len(strategies) >= 11`, names unique, the three variants
present and contiguous immediately after their parent. The comment in the file
records that it already failed once at 11 -> 15 and was fixed then. No change
needed from me.

---

## Task 3: `db/schema.sql` — ALREADY DONE, verified

Both `liquidations` and `hyperliquid_positions` DDL are present (lines 160 and
174) with all four indexes (lines 216-221) and comment headers matching the
existing style.

Better than asked: a concurrent session also added
`tests/test_schema_matches_feed_modules.py`, which asserts the two copies of
each DDL (schema.sql and the module's `SCHEMA_SQL`) actually agree. **11
passed.** That closes the drift risk the two-copy arrangement creates, which
the instruction did not ask for.

---

## Task 4: `best_bid` / `spread` in `features_json` — ALREADY DONE for the
## parent; I FIXED A SIDE MISMATCH in the inverse

The parent was done: `strategies/polymarket/fair_value_arb.py:524-525` adds
`best_bid` and `spread` (`best_ask - best_bid`, rounded to 6dp) to the entry
`feats` dict, marked `LOGGING ONLY: no threshold, gate or exit rule reads
these`. The three parameter variants inherit it (none override `evaluate`).

**`fair_value_arb_inverse.py` DOES override `evaluate`, and it was wrong.** It
calls `super().evaluate(ctx)`, then flips the side and overwrites `best_ask`
(line 464) with the flipped side's ask — but left `best_bid` and `spread` as
the PARENT's side. Every inverse row would have logged an inverse-side ask
next to a parent-side bid, which subtract to a spread **that was never quoted
on either book**. Since the whole inverse hypothesis is "the spread does not
invert", that is the one field it could least afford to get wrong.

Fix, logging-only, no logic touched:
- preserve the parent's pair as `parent_best_bid` / `parent_spread`, matching
  the existing `parent_*` convention at lines 415-419
- re-read `best_bid` from the flipped book and recompute `spread` against the
  inverse ask; also stamped as `inverse_best_bid` / `inverse_spread`

`test_fair_value_arb.py` + `_inverse` + `_variants`: **280 passed.**

---

## Task 5: strike proxy on ETH and SOL — ALREADY GENERALIZED AND RUN

`backtest/measure_strike_proxy.py` already takes `--asset` (and `--asset all`),
driven off the `engine/polymarket/assets.py` registry. It had already been run:
`research/strike_proxy_by_asset.json`, 220 windows requested per asset,
measured 2026-08-18 10:24-10:26Z. **Reporting, not changing** — per instruction,
`STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0` is untouched.

Disagreement rate between the Binance.US-derived proxy strike and the oracle:

| asset | scored | drops | headline | **at the 5.0 bps floor** | at 8 bps | at 10 bps |
|---|---|---|---|---|---|---|
| BTC | 220/220 | — | 15.0% | **2.7%** (n=75) | 5.0% | 4.5% |
| ETH | 219/220 | 1 unresolved | 20.5% | **6.6%** (n=106) | 3.4% | 4.7% |
| SOL | 218/220 | 2 unresolved | 33.0% | **14.3%** (n=84) | 16.7% | 10.5% |

Sample is adequate on all three — no NOT_TESTED, nothing to report under
convention 11.

**What the numbers say.** The single 5.0 bps constant was measured on BTC and
is inherited by the other two. It does not transfer:

- **ETH is ~2.4x BTC's error rate at the same floor.**
- **SOL is ~5.3x BTC**, and — the part that matters — **widening the floor does
  not fix SOL.** It gets *worse* at 8 bps (16.7%) before improving at 10
  (10.5%). BTC and ETH both fall toward ~3-5%; SOL does not converge. That is
  not a floor that is set too low, it is a proxy that tracks SOL badly.

Never quote the headline column. It averages a coin flip and a 96%.

**This is a parameter decision for Aym, and it is a real one:** SOL strategies
are currently gated on an instrument-error floor that has never been measured on
SOL, and now has been. I did not change it.

---

## Task 6: `CLAUDE.md` rewritten — DONE

Rewritten, not appended. Under 3 pages. Untracked, never reaches GitHub.
Corrections applied:

- registry **19**, all listed with indices (0-7 pinned by test, 8-10 must stay
  contiguous after the parent, 11+ free append)
- shadow loop is multi-asset; `assets.py` is the registry;
  `SHADOW_ASSETS = ('btc','eth','sol')` and the `--assets` CLI default is
  `btc,eth,sol`. A cycle is **19 x 3 = 57** evaluations.
- Binance geoblocked (HTTP 451, 0 frames in 25s, TLS handshake succeeds so it
  logs CONNECTED forever); tape is Bybit-only at **0 rows**; Hyperliquid has
  **no** public liquidation feed and is not the replacement; HL whale poller is
  live at **1,534 rows**
- the daily-loss-limit consequence, including that `0.0` once meant the exact
  opposite of its comment, that `> 0` guards now make it genuinely disable, and
  that `test_config_yaml_matches_the_module_defaults` is permanently red by
  construction
- test count marked as **re-derive, do not quote** — with the command
- the concurrency warning (convention 21) promoted to the top of the file
- conventions renumbered/kept 1-26; convention 24 updated: DECISIONS.md now runs
  to **D-288**, so the old "there is no D-284" warning is obsolete

### One correction to the instruction file itself

It said "Do NOT restart the shadow loop (PID 27030)". **PID 27030 was already
dead before I started.** A concurrent session (45175, explicitly instructed to
restart it) had killed and restarted it twice. The live loop is now **PID
51187** (plus runner 51148), started 06:39 with `--poll 5 --equity 1000` and the
assets default, so it does see all 19 x 3. I did not restart anything. Feeds
18543, 37578 and 48637 all confirmed alive and untouched.

---

## Full suite: 2,167 passed, 1 skipped, 2 failed — and only 1 failure is real

```
env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q --ignore=tests/test_dashboard_charts.py
2 failed, 2167 passed, 1 skipped in 514.74s (8m34s)
```

Note the count: **2,167**, not the "1,314" CLAUDE.md claimed and not the
"~1,959+" the instruction file estimated. Four pytest processes were competing
for the machine, which is why it took 8.5 minutes.

**Failure 1: `test_every_skip_reason_the_strategies_emit_is_classified` — NOT
REAL, already green.** This is convention 13 in miniature: the run started at
06:43 and Python snapshotted source at import, *before* I added the fourth
weather key. Re-run against the current tree: **224 passed** across
`test_forge_shadow_eval.py` + `test_polymarket_risk_gate.py`, with this test
green. Do not "fix" it.

**Failure 2: `TestConfigWiring::test_config_yaml_matches_the_module_defaults` —
REAL, not mine, and pending a ruling.** `config.yaml` has
`daily_loss_limit_usdc: 0.0` while the module default is `30.0`. The test exists
precisely to make that drift fail loudly, and the drift is deliberate (breakers
disabled in shadow). It is permanently red until someone decides whether
"breakers off in shadow" is the intended posture. **I did not touch it and did
not silence it.** See item 3 below.

## For Raven to rule on

1. **`no_recent_liquidation` / `liquidation_below_second_lock_min`: GENUINE or
   DATA_BLOCKER?** I kept GENUINE against the instruction, for the code-path
   reason above. Zero measured consequence today. Needs a D-number either way.
2. **Should the strike proxy noise floor be per-asset?** SOL is 14.3% at the
   floor vs BTC's 2.7%, and does not converge when widened. Aym's call.
3. **Is "breakers off in shadow" the intended posture**, and what happens to the
   permanently-red `test_config_yaml_matches_the_module_defaults`?
4. **`weather_arb.py` is still being written.** The AST skip-classification
   guard will go red again on the next `decide('SKIP', ...)` literal added. That
   is the guard working, not a break.

## Not done / out of scope

- Nothing committed. Tree left for review.
- `STRIKE_PROXY_NOISE_FLOOR_BPS` unchanged (explicitly instructed).
- No strategy logic, threshold or exit rule touched anywhere. Task 4's inverse
  change is logging fields only.
