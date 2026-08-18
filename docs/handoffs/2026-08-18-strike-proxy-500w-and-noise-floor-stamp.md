# Strike proxy at 500 windows, noise-floor stamp rename, session census

**By:** Cody, 2026-08-18 ~07:00 UTC
**Acting on:** `docs/handoffs/from-raven/2026-08-18-mechanical-followups.md`
**Committed:** nothing. Tree left for review.
**Shadow loop:** PID 51187 still alive (17m). Not touched, not restarted.

All three tasks done. One thing needs a ruling and I did NOT decide it myself:
the 500-window measurement disagrees with the 220-window numbers Raven asked me
to hardcode. Details in Task 1.

---

## Task 1: 500 windows per asset. Available, and the answer changed.

500 windows WAS available for all three assets. Output in
`research/strike_proxy_by_asset_500w.json` (the 220w file is untouched).

| asset | requested | scored | dropped | headline disagreement |
|---|---|---|---|---|
| btc | 500 | 499 | 1 unresolved | 18.4% |
| eth | 500 | 499 | 1 unresolved | 22.4% |
| sol | 500 | 500 | 0 | 31.0% |

Headline is the useless number (it averages a coin flip and a 96%). The
cumulative rate at and above the configured 5.0 bps floor is the result:

| asset | 220w rate @5bps | 220w n | **500w rate @5bps** | **500w n** |
|---|---|---|---|---|
| btc | 2.7% | 75 | **5.1%** | **175** |
| eth | 6.6% | 106 | **9.3%** | **248** |
| sol | 14.3% | 84 | **15.8%** | **196** |

Every asset is now above the convention 7 n=100 threshold at the floor. SOL's
n=84 problem is gone.

**Raven's actual question was whether SOL's flat error profile holds.** It
holds. SOL, cumulative, 500 windows:

```
  >= 4 bps  15.6%     >= 8 bps  15.0%
  >= 5 bps  15.8%     >= 10 bps 10.5%
```

Widening the floor from 5 to 8 bps buys SOL 0.8 percentage points and costs 83
of its 196 windows. BTC over the same move goes 5.1% -> 3.2% and ETH 9.3% ->
2.7%. That is the measurement; what to do about it is not my call.

**Every rate moved UP at the larger sample.** BTC nearly doubled. So the 220w
numbers were the optimistic end of the sampling noise, not a stable estimate.

### The thing that needs a ruling

Task 2 told me to hardcode `{'btc': 0.027, 'eth': 0.066, 'sol': 0.143}` — the
**220-window** numbers. Task 1 then produced different numbers. I used the 220w
values exactly as instructed, because Task 1 said "do NOT interpret the results,
just measure and report", and promoting a fresh measurement into a production
constant is interpreting it.

So right now the constant, its comment, and the drift test all point at
`research/strike_proxy_by_asset.json` (220w) and agree with each other. Nothing
is inconsistent. But the stamp on every gated row currently claims BTC costs
2.7% when the larger sample says 5.1%.

**Someone should decide whether to repoint the constant at the 500w file.** It
is a two-line change plus the test's source path. I did not make it.

`STRIKE_PROXY_NOISE_FLOOR_BPS` is UNCHANGED at 5.0, as instructed.

---

## Task 2: `noise_floor_measured_on` -> `noise_floor_source` + error dict

Done. The old name read as "this row was measured on BTC" — it was not; the
FLOOR was. That is the whole reason for the rename.

**`engine/polymarket/strike.py`** — two new constants next to the floor:

```python
NOISE_FLOOR_SOURCE_ASSET = 'btc'
NOISE_FLOOR_ERROR_BY_ASSET = {'btc': 0.027, 'eth': 0.066, 'sol': 0.143}
```

Fractions, not percents. Provenance comment names the source file, the field
(`rate_pct` at `threshold_bps == 5.0`), the sample size and the date.

**`engine/polymarket/shadow_loop.py:~1539`** — the stamp on every strike-gated
row is now:

```python
noise_floor_source=NOISE_FLOOR_SOURCE_ASSET,
noise_floor_measured_error_by_asset=dict(NOISE_FLOOR_ERROR_BY_ASSET),
```

`dict(...)` is a copy on purpose. Handing the module-level dict by reference
into a features row that gets mutated downstream is how a constant stops being
constant.

I also rewrote the comment block above that gate. It still said "the harness
has not been re-run per asset. Until it is, an ETH or SOL lead just above 5 bps
is trusted on BTC's evidence." That is now false — it HAS been re-run — and a
stale comment asserting a gap that has been closed is worse than no comment.

**`backtest/measure_strike_proxy.py`** — same stale claim in the docstring,
same fix.

### Three new tests

- `test_the_per_asset_error_matches_the_measurement` — re-reads
  `research/strike_proxy_by_asset.json` and fails if the hardcoded dict drifts
  from it. Convention 17: the constant now has a named harness that makes its
  expiry loud instead of silent. **Re-running the harness onto that path is
  what turns this red, deliberately.**
- `test_the_floor_is_sourced_from_an_asset_that_was_actually_measured` — the
  source asset must be the one with the LOWEST measured error. "Inherited from
  BTC" only means anything if BTC is the favourable case; if that inverts, the
  inheritance argument has inverted with it and this says so.
- `test_the_gated_row_carries_where_the_floor_came_from_and_what_it_costs`
  (in `test_polymarket_shadow_loop.py`) — reads `features_json` off the actual
  logged signal row and asserts both new fields are present AND that
  `noise_floor_measured_on` is **absent**. Convention 22: the docstring saying
  the rename happened is not a wiring test. The absence assertion is there
  because a rename that only adds is how a downstream query silently returns
  zero rows.

### Search results for the old name

Live code: **zero** remaining, other than the deliberate absence-assertion in
my new test.

Docs still containing it, and why I left them:
- `docs/DECISIONS.md:571` (D-285) — a decision record describing what was
  decided at the time. Convention 10; I do not rewrite decision bodies. **Its
  text now describes a superseded field name.** If that matters it wants a
  follow-up D-number, which is not mechanical work.
- Four `docs/handoffs/**` files — historical records of past sessions. Same
  reasoning.

### Test run

```
tests/ -k "strike or noise_floor"                     33 passed
+ the wiring test explicitly (name matches neither)    5 passed
test_strike_proxy + test_polymarket_shadow_loop
  + test_polymarket_multi_asset + test_polymarket_shadow_speed
                                                     112 passed
```

I did not run the whole suite. With four sessions writing to this tree a full
run's failures cannot be attributed (convention 21), so a clean 112 on the
files I touched is the honest signal and a noisy 2,100 is not.

All edits went through `engine.concurrency.safe_edit` (convention 26), so every
one is hash-checked and registered.

**Convention 13:** none of this reaches PID 51187. It snapshotted its source at
06:39. The running loop still stamps the old field name until someone restarts
it, which I did not do.

---

## Task 3: the tmux census was already out of date

Raven asked about two sessions with 0-byte logs. There are **four** other Cody
sessions, one of the two named is finished, and the 0-byte logs were a red
herring.

| session | PID | state | doing what |
|---|---|---|---|
| `cody-mech-fixes` | 51304 | **DEAD, finished cleanly** | `2026-08-18-mechanical-fixes-from-review.md` |
| `cody-trading-bot` | 52363 | alive, 10m+ | `2026-08-18-classify-new-second-lock-reasons.md` |
| `cody-mech-fixes-cleanup` | 54689 | alive, started 06:52 | `2026-08-18-mechanical-fixes-and-cleanup.md` |
| (unnamed) | 55490 | alive, started 06:53 | `2026-08-18-implement-raven-rulings-d289-d297.md` |
| (unnamed) | 45175 | alive since 06:12 | wire 4 repo strategies + weather markets |
| `cody-mech-followups` | 54519 | me | this file |

**The 0-byte logs meant nothing.** `claude -p | tee` writes the whole response
at exit, so the log is 0 bytes for the entire run and full the instant it ends.
It is not a stuck-vs-running signal. `cody-mech-fixes` proved it: 0 bytes at
06:51, 3,070 bytes at 06:53. Use `ps -p <pid>` (convention 25). I killed
nothing.

### What `cody-mech-fixes` (51304) did

Its output is at `/tmp/cody-mech-fixes-output.log`. It posted its own handoff to
the webhook and got `accepted`. Summary of its claims:

- **4 of its 6 tasks were already done** by concurrent sessions. It verified
  rather than assumed, which it correctly calls the deliverable for those four.
- **Refused** to reclassify `no_recent_liquidation` /
  `liquidation_below_second_lock_min` as DATA_BLOCKER. Same argument that is
  already in CLAUDE.md: both sit below `if not window.ok`, so with 0 feed rows
  the strategy emits `liquidation_feed_empty` and neither key is reachable.
  Kept GENUINE. Notes a concurrent session reached the same conclusion
  independently. **Still needs a D-number either way.**
- **Found a real bug** in `fair_value_arb_inverse.py`: it flips the side and
  overwrites `best_ask` but left `best_bid`/`spread` as the parent's side, so
  every inverse row logged a spread quoted on neither book. Logging-only, fixed.
  Worth noting the inverse hypothesis IS "the spread does not invert", so that
  was the worst available field to get wrong.
- Suite at the time: 2,167 passed, 1 skipped, 2 failed. It attributes one
  failure to convention 13 (run started before its fix landed) and the other to
  the known-red `test_config_yaml_matches_the_module_defaults`.

I have not independently verified any of those claims. They are its report, not
my measurement.

---

## Open items for Raven

1. **Repoint `NOISE_FLOOR_ERROR_BY_ASSET` at the 500w file, or leave it at
   220w?** Every rate moved up; BTC nearly doubled. Needs a decision, not a
   default.
2. **D-285's body names the old field.** Follow-up D-number, or leave the record
   as written?
3. SOL at 500 windows is 15.8% at the floor and 15.0% at 8 bps. Still flat, now
   at n=196. Feeds directly into open item 5 in CLAUDE.md (per-asset floor vs
   one constant) — Aym's call, untouched.
4. The two liquidation skip reasons are still GENUINE and still undecided. Two
   independent sessions have now made the same argument for keeping them.
