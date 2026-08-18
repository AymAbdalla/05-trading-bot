# Handoff: the Opus reasoning layer (Raven's 8-task instruction file)

**By:** Cody, 2026-08-18
**Instruction:** `docs/handoffs/from-raven/2026-08-18-execute-all-pending-tasks.md`
**Mode:** paper only. Nothing committed, nothing staged, no live order path touched.

---

## The shape of it

Raven asked for Opus reasoning in five places (Forge proposals, vault lessons,
blowup root cause, critic post-mortems, cycle takeaways). Rather than five
subprocess call sites, there is one:

```
agents/llm_client.py     the ONLY place that spawns `claude -p`
  |                      task -> model routing, timeouts, Convention 19 JSON
  +-- agents/vault_reader.py   reads ~/aym/vault/Trading/** into a prompt block
  +-- agents/vault_writer.py   the ONLY writer into the vault, model-routed
        +-- scripts/vault_refresh.py   rebuilds notes from db/trading.db
        +-- agents/critic.py           post-mortems
        +-- scripts/shadow_runner.py   blowup root cause (detached)
  +-- agents/forge_reasoner.py  the brief, the prompt, the JSON candidates
        +-- agents/forge.py           validates and writes
```

### Two design calls that deviate from the instruction file

**1. The model does not hold the pen.** Raven's file says "Opus writes the
proposals to `strategies/proposals/`" and "spawn Opus to write the report to
the vault". In both cases Opus *composes* and Python *writes*. Reasons: we can
reject an empty or refusing turn before it overwrites a good note; the write is
atomic; and every artifact carries a provenance header naming the model. For
Forge specifically, the whole contract of `forge.py` is that the deterministic
half enforces the schema and refuses anything unfalsifiable, and that only
holds if Python writes the file. Same outputs, same locations, three fewer
failure modes.

**2. Failure to get a turn is NOT_TESTED, never "found nothing".** Convention
11. `LLMResult.ok is False` means the turn could not run (no binary, timeout,
non-zero exit, empty stdout). A turn that ran and declined is a different fact
and is recorded differently. Vault notes written without a model say so *in the
file*, in a `model: NOT_TESTED` header plus a visible warning block, because
they get read back as evidence.

---

## Task by task

### Task 1 + 4: Forge Opus reasoner, and Forge reads Obsidian - DONE

`agents/forge_reasoner.py` (new), `agents/forge.py` (modified), 35 tests.

- Opt-in via `--reasoner` / `--opus`. **The no-flag default is byte-identical
  to the old behaviour** and is pinned by a test.
- The prompt's schema section is *generated from* `forge.REQUIRED_FIELDS`,
  `KINDS`, `min_edge_bps_for()` etc, so it cannot drift from `validate()`.
- Brief = graveyard evidence + gaps + `hypothesis_graph` TESTED_FAILED rows
  (41 of 135) + live shadow results + the whole vault.
- Four outcomes, separately counted: `ok`, `no_candidates`, `unusable_reply`,
  `NOT_TESTED`. The last three fall back to the deterministic list and say why.

**Verified with a real Opus turn** (the subagent left this NOT_TESTED; I ran
it): `screened 7, wrote 7, refused 0, warned 3`. Opus can satisfy the schema.

The three model-authored proposals are 017, 018, 019. They are good: 017 cites
the vault lesson by filename, adopts its exact 5-of-10 kill test, sets
`expected_edge_bps: null` rather than inventing a number, cites
`hypothesis_graph` ids 112/113/114 by number, and explains why it is *not*
re-treading them. That is the compounding loop working on its first cycle.

### Task 2: vault writer with model routing, and the shadow_runner fix - DONE

`agents/vault_writer.py` (new), `scripts/shadow_runner.py` (modified via
`engine.concurrency.safe_edit`).

- Routing: lessons / blowups / cards / cycle takeaways / critic post-mortems ->
  Opus. Daily and weekly summaries -> Sonnet. Callers name a TASK, never a
  model, so re-routing is one edit in `MODEL_FOR_TASK`.
- The hardcoded root cause is gone. It used to assert "the primary cause was
  the fair_value_arb family" and "the spread is the enemy" regardless of what
  the account did, and it would have kept asserting that after those
  strategies were killed.
- The DB row write stays inline and immediate. The reasoning is spawned
  **detached** (`python3 -m agents.vault_writer blowup --id N`) so a 10-minute
  turn cannot delay the 5-second loop restart, the note survives the runner
  being killed, and any past blowup can be re-analysed by id later.

### Task 5: rewrite the five vault notes with Opus - DONE

`scripts/vault_refresh.py` (new), 15 tests. **5/5 composed by Opus**, 1.5k-2.7k
chars each grew to 7.7k-14.9k.

The notes are no longer the artifact; the script is. Raven's numbers were
already stale when I started: the lesson said `fair_value_arb` had 503 trades
at 21% win rate, the database said 255 at 32.5%. Re-run the script and every
number is re-derived.

### Task 3: the critic - DONE

`agents/critic.py` (new), `tests/test_critic.py` (85 tests).

Dry run against the live DB, 801 closed trades, **nothing written** (I verified
after: graph still 135 rows, 0 with `source='critic'`, no kill file, no vault
note):

| mode | count |
|---|---|
| model_miscalibrated | 341 |
| entry_signal_wrong | 108 |
| stop_too_tight | 62 |
| unclassified | 20 |
| spread_eats_edge | 4 |
| regime_mismatch | 0 |

264 winners + 535 losers + 2 flat = 801. The identity is asserted, not hoped
for. 9 kills recommended, 1 **withheld**: `PM_temporal_arbitrage` is a correct
`entry_signal_wrong` at 19.5% win rate but is net **+$0.59** (asymmetric
two-leg payoffs), so killing it would have been wrong. Six never-fired
strategies are reported but excluded from the graph (Convention 11).

**Two classifiers are honestly not decidable and say so rather than guessing:**

- `regime_mismatch` returns None every time. No regime label exists anywhere,
  and all 135 graph rows carry `market_regime='any'`, so there is not even a
  stored claim to mismatch against.
- `stop_too_tight` is only partly decidable. There is no quote tape, and
  `candles` covers 1785754800000-1786473900000 which does not overlap the
  trading window at all. Undecidable cases go to
  `unclassified/no_post_exit_price_observation`. **This matters: all 535
  losers exited on a stop**, so a classifier keyed on `exit_reason` would have
  labelled every one of them and looked authoritative.

The subagent also found a real data trap worth recording: on
`PM_fair_value_arb_inverse` the recorded `best_bid` is the **complement
token's** bid (`best_ask 0.67, best_bid 0.33` summing to 1.00), so a naive
`(bid+ask)/2` mid computes 0.50, the price of nothing, and falsely labelled 16
inverse trades. Parent/hft/wide satisfy `ask - bid == spread`; the inverse
violates it on 30 of 154 rows.

### Task 8: wallet addresses for smart_money_copy - DONE, 7 of 7

`research/polymarket_wallets.md` (new),
`strategies/polymarket/smart_money_copy.py`, `tests/test_smart_money_copy.py`
(86 tests, up from 84).

All seven resolved, none invented. **I re-ran the lookup myself rather than
taking it on trust**: `Bonereaper` -> `0xeebde7a0e019a63e6b476eb425505b7b3e6eba30`
matches, and a nonsense handle returns no `profiles` key at all, so the filter
is real and not a default page. Each address was round-tripped to the same
display name on two further independent hosts.

Three corrections to the premises in the instruction file:

- **`data-api.polymarket.com/profiles` does not exist.** 404 on every form. The
  route that works is Gamma's `public-search?q=<handle>&search_profiles=true`,
  and the `search_profiles=true` flag is mandatory.
- **`0x50f7` and `0xaaaaa` were never address prefixes.** They are chosen
  usernames that happen to look like hex. The accounts that genuinely start
  with those characters carry Polymarket's default `<address>-<epoch>` username
  and have traded zero. `TRACKED_WALLET_PREFIXES` is now `{}`.
- **Resolving the wallets does not unblock the strategy, and the next blocker
  is not the one we thought.** It is not market scanning: the loop hands the
  strategy the market directly, and at 14:25 UTC three of the seven had a fresh
  BUY inside the strategy's own 120s staleness cap. The hard stop is
  `wallet_record_unmeasured` - a live `/trades` row carries no `won`, no
  `realized_pnl`, no redemption flag, so none of `SETTLEMENT_BOOL_KEYS` or
  `SETTLEMENT_NUMERIC_KEYS` can be filled. That was a docstring claim; it is
  now confirmed against a live payload.

Unverified, equally for all seven: the Dan1ro0 article and the Reddit post.
`WebSearch` and `WebFetch` were permission-denied in that session, so the link
from "the handle the article meant" to "the profile Polymarket calls that
today" is asserted by the venue, not by the source.

Stale as a result: `strategies/polymarket/__init__.py` around line 138 still
says all seven are `None` and that two are 4-hex prefixes.

### Task 6: weather - DONE, and it found a defect worth more than the task

Full report: `docs/handoffs/2026-08-18-weather-arb-daily-extreme-and-discovery.md`.
D-311 (the agent tried for D-306, found this session had taken it, and moved
up - Convention 24 working as intended).

All four pieces landed, each with live evidence rather than an assertion:

- **Model:** `M = max(O, X)` for a daily high. `O` is the extreme the station
  has ALREADY reported today, read from the METAR history endpoint, so it is a
  hard bound and not modelled. `X ~ Normal(mu, sigma)` off open-meteo's
  forecast at the station's own coordinates. All three assumptions (normality
  of an extreme, bias persistence, the sigma constants) are written on the
  estimate object as Convention 15 estimates, never fitted.
- **Discovery:** live, 2,035 raw markets -> 1,090 kept -> 1,034 usable, 31
  cities, 0.2s. No `order` parameter is sent at all (the tag route takes
  none); ranking is local. That dodges the `order=volume` text-sort trap AND a
  second one: a plain volume sort would have spent the whole budget on "Will
  2026 be the hottest year on record?" at $820,702 against $9,330 for the
  biggest genuine city ladder.
- **Feeds:** METAR `LEMD` returned `temp: 39C` at `40.466, -3.555`; open-meteo
  at those coordinates returned `temperature_2m_max` of 100.4F / 93.8F.
- **`resolution_station_unknown` is gone.** The crypto path now returns
  `not_a_temperature_market`, which was Convention 20's point: those were two
  different facts sharing one counter.

**The defect it found on the way.** The first live run booked 2 entries at 0.43
and 0.34 "edge". Those were arithmetic, not edge. For a bounded rung of width
`w`, the maximum attainable Yes probability is `2*Phi(w/(2*sigma)) - 1`, which
depends on nothing but width and sigma. A Celsius bucket is 1.8F wide; at
31.5h out, sigma is 2.96F, giving a ceiling of **0.239**. The Madrid 36C row
returned **0.238**. The model could never have said Yes about that rung, so it
would have taken No on nine of every eleven ladder rungs, forever, and called
it edge.

Same shape as `strike_inside_proxy_noise_floor` and treated the same way:
refuse where the instrument cannot resolve. Re-measured with the gate: **0
entries**, 17 `rung_narrower_than_model_resolution`, 2
`airport_agrees_with_market`, 1 `observation_window_too_far_out`. The fix is
to fit sigma, not to lower the floor.

**The claim changed and the kill condition should follow.** The model's centre
is now open-meteo's forecast, so a disagreement is mostly "our forecast
provider vs the crowd's", not "the crowd reads the wrong thermometer". The
airport-vs-downtown gap is still unmeasured and `DowntownWeatherFeed` still
gates nothing.

The named calibration harness `backtest/measure_daily_extreme_calibration.py`
**does not exist**, and every row is stamped
`daily_extreme_calibration_harness_exists: false` rather than implying it does.

### Task 7: maker fill simulation - DONE at the adapter, NOT wired into the loop

`engine/polymarket/paper_adapter.py` (845 -> 1944 lines), 128 tests in
`tests/test_polymarket_paper_adapter.py` (60 pre-existing unchanged, 68 new).

Fill rule is **strict cross, queue aware**, and the choice matters:

- Queue position is measured once at rest time and never refreshed downward,
  so we are not silently promoted up the queue every time someone else cancels.
- A snapshot fills only if size is offered **strictly below** our limit.
  `best_ask == limit` is a locked market, recorded as `touched`, never filled.
- `max_through_shares` is a maximum across snapshots, never a sum.
- Post-only is enforced: a bid at or above the best ask is a taker order
  wearing a maker label and is refused as `maker_would_cross_book`.

We have no trade prints, only snapshots, and an offer resting strictly under
our own bid is the only snapshot-visible evidence of aggression. A
touch-means-fill model books every good fill and no adverse selection, which
is how a paper strategy looks profitable and is not.

The subagent caught a wrong claim of its own here and I want it on the record,
because it is the kind of thing that becomes a quoted number: it had asserted
`slippage_vs_top` would be negative "by construction" for a maker. It is
**positive**. By the time a resting bid is crossed, the offer has come down
through it, so we own shares above the current market. That is adverse
selection, it is real, and the honest sign was kept with a separate
`spread_declined_usdc` so the flattering number and the true cost cannot be
quoted as one.

Nine distinct terminal no-fill reasons, none sharing a counter, each proved
reachable by exercising the path rather than by reading the constant. Observation
outcomes live in a separate `maker_counts` dict so thousands of no-op looks
cannot bury the terminal outcomes or break the existing row-count identity.

**Does `box_builder` / `grid_hedge` get past `maker_fill_not_simulated`?** At
the adapter layer yes, demonstrated on real `BoxBuilder().evaluate(ctx)` legs:
one leg touched at 0.45 and correctly did NOT fill, the other filled 5 @ 0.44.

**In the live loop, no.** `engine/polymarket/shadow_loop.py` short-circuits
every `action == 'QUOTE'` into `SKIP_MAKER` (`= 'maker_quote_not_simulable'`,
defined at line 418, applied at the `return _log(name, slug, SKIP_MAKER, ...)`
call - grep for `SKIP_MAKER` rather than trusting a line number, the file is
being edited concurrently) and never hands the legs to the adapter. Nothing anywhere calls `simulate_maker_buy` or
`observe_resting_orders`. Convention 23: a fix at one site is not a fix. The
wiring added is additive only (three feature keys so the loop can be taught to
rest them); `grid_hedge`'s `kill_condition_blocked_by` was deliberately NOT
cleared, because its kill condition needs 50 grid fills and a fill model
existing is not 50 fills.

**Needs a decision before wiring:** whether resting orders survive across loop
cycles, and where `observe_resting_orders` gets called from.

---

## Findings that need a ruling

### 1. The corridor family's "risk-free arbitrage" claim does not survive its own trade log

This is the biggest thing I found and I did not change any corridor code.

`corridor_pair_live.py`'s docstring says the $1.00 floor is "AN IDENTITY, NOT A
FINDING" and that "both legs losing is arithmetically impossible". The logged
rows disagree in three separate ways:

- **`PM_corridor_collector` lost BOTH legs, twice.** `eth-updown-15m` at 0.10
  and `eth-updown-5m` at 0.11, both exited 0.00. Again at 0.31 and 0.22, both
  0.00. If it implements the floored pair, that is the identity failing.
- **`PM_corridor_pair` took a one-legged fill.** Signal `a2255aaf` opened the
  15m leg at 0.84 and the 5m leg never opened. It lost $4.20 completely
  unhedged, the single largest corridor loss. Leg risk is real and unaccounted.
- **The "2 trades, 100% WR, +$3.95" in Raven's lesson is one signal, not two
  results.** Both rows come from signal `5daa0615`. Combined entry cost was
  $1.21, not under $1.00. `signals_acted = 1`.

The identity holds only if `P10` sits on the leader side of `P0`, but the lead
is measured at entry time `t` (inside the final third), not at the 5m open. If
price dipped at `T+600` and recovered by `t`, both legs can lose. That is
consistent with what `corridor_collector` actually did.

Proposal 017 (Opus, unprompted) reached the same doubt independently and is
built as the measurement that would settle it.

**Needs:** a decision on whether to trust the docstring or the rows. Family net
is -$4.55 on 9 closed trades, so this is not urgent in dollars, but the vault
now says "corridor pair works" and Forge reads that.

### 2. `corridor_collector`'s docstring is stale

It says it "skips `no_lead_or_atr` on every window and always will". It has 4
closed trades. D-299's noise-floor drop probably unblocked it. A docstring that
says a strategy cannot fire, next to rows where it fired, is Convention 22.

### 3. My `shadow_runner.py` fix is on disk but NOT live

PID 51148 started 06:39; the edit landed 10:19. Convention 13: it is running
the old snapshot, hardcoded prose and all. The wrapper auto-restarts the
*loop*; nothing auto-restarts the *wrapper*. `shadow_blowups` still has 0 rows
so nothing has been mis-written yet.

**Restarting the supervisor of a live loop is Aym's or Raven's call, not
mine.** Until then, the blowup reasoning path is code, not behaviour.

### 4. Proposal numbering collided, and I fixed the cause

The real reasoner run wrote a second 001 through 007 beside the existing ones.
Nothing was overwritten (the slug is in the filename) but "proposal 005"
stopped identifying a document, and `corridor_pair_live.py` cites proposal 005
by number.

Cause: `--start-index` defaulted to 1, so every run that forgot to pass it
restarted the numbering. I renumbered the new files to **017-023** and changed
the default to `next_free_index()`.

**That fix was wrong in the other direction and I caught it at the very end of
the session.** The deterministic path re-emits the same hand written candidate
list every run, and it used to REWRITE 001-005 in place. Appending at the next
free number instead produced `024-pm-dynamic-rotation.md` ...
`028-pm-cross-window-relative-value.md`, five files carrying the identical
slugs as 001-005, three minutes later. I only noticed because the final
`git status` had proposals in it that I had not written.

Both failures are the same mistake: **numbering by position instead of by
identity.** Numbers are now allocated by SLUG. A re-run of the same proposal
overwrites itself; a genuinely new proposal takes the next free number; a slug
that already carries two numbers resolves to the lowest, so a repair collapses
onto the original rather than onto the accident.

Verified by actually re-running Forge: 9 written, all into existing numbers,
directory count unchanged. The five duplicates were deleted. Nine tests cover
it, including two that assert the LIVE directory has neither a duplicate number
nor a duplicate slug.

A third bug fell out while fixing the first: the initial `next_free_index` took
`proposals_dir=PROPOSALS_DIR` as a default argument, which binds the module
global at import and silently ignores monkeypatching, so two tests were reading
the real repo while claiming to read a temp dir. Resolved at call time now.

Three bugs in one small function. The lesson worth keeping is that all three
were invisible to unit tests and visible the moment a real run happened.

### 5. My vault budget was sized for the old notes

`DEFAULT_BUDGET_CHARS = 20000` was fine for Raven's 1.5k notes. Against
Opus-composed 8k-15k notes it dropped 2 of 5 out of Forge's brief. It reported
the drop (Convention 20 did its job) but the brief was still short half the
evidence. Raised to 60000, with the expiry condition written down.

### 6. `known_failure_modes` was splitting one mode into two

Real output writes ``**Failure Mode:** `spread_eats_edge` (confirmed by the
inverse variant)``. Keyed raw, that was a different mode from a note writing
the bare token, so two occurrences counted as one each. Now normalised to the
leading token, with the prose kept on `failure_mode_raw`.

Both 5 and 6 were found by round-tripping REAL model output through my own
reader, not by a fixture. Worth repeating on anything else in this layer.

### 7. `vault_writer.dry_run` was a trap and is now `skip_model`

It meant "skip the model, still write the fallback". It read as "write
nothing". In the two hours it carried the wrong name, a `--dry-run` of
`agents/critic.py` deposited a note built from synthetic test numbers into the
real `~/aym/vault/Trading/Forge-Cycle-Summaries/`. That note was deleted, but
the vault is read back as evidence by Forge, so a synthetic note there is not
a cosmetic problem.

Renamed across `vault_writer`, `vault_refresh` and `critic`.
`scripts/vault_refresh.py` keeps `--dry-run` as a CLI alias with the trap
spelled out in its help. Three tests pin it, including one that fails if any
public writer ever regrows a `dry_run` parameter or loses its `out_dir`.

### 8. `never_fires` divergence wants a D-number

The critic reports `never_fires` but does NOT write it to the graph, because
Convention 11 makes it NOT_TESTED and `record_failure_mode` can only write
`TESTED_FAILED`. But `hypothesis_graph.populate_from_graveyard` **does** write
it as `TESTED_FAILED`, and 8 such rows already exist. Both behaviours are
defensible; they should not both be live.

---

## Test counts (re-derived, not quoted)

**Full suite, run by me after every agent finished:**

```
env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q --ignore=tests/test_dashboard_charts.py
2 failed, 2974 passed, 1 skipped in 342.46s (5m42s)
```

Baseline in CLAUDE.md was 2,421 passed / 3 failed. Now **2,974 passed / 2
failed**: +553 tests, and the two "failed in the suite, passed in isolation"
concurrent-edit collisions from last session are gone.

Per-suite for the reasoning layer: `test_llm_reasoning_layer.py` 55,
`test_vault_refresh.py` 15, `test_forge_reasoner.py` 35, `test_critic.py` 85,
`test_polymarket_paper_adapter.py` 128, `test_smart_money_copy.py` 86,
`test_weather_arb.py` 170, `test_weather_daily_extreme.py` 84 (new),
`test_weather_shadow_wiring.py` 24 (new).

**There are TWO permanently-red tests, not one.** CLAUDE.md documents only the
first. Both reproduce at pure HEAD with none of this session's work applied:

1. `test_polymarket_risk_gate.py::TestConfigWiring::test_config_yaml_matches_the_module_defaults`
   — the known one (config 0.0 vs module 30.0, red by construction).
2. `test_r007_r008_fixes.py::test_stale_reason_string_is_emitted_by_no_live_code_path`
   — **newly identified as pre-existing.** Commit `7a05dcb` added
   `tests/test_hypothesis_graph.py`, which contains the stale reason string and
   is not in the test's allowlist. I verified this independently: the failure
   names that exact file, and `git log --diff-filter=A` confirms `7a05dcb`
   added it. Not caused by this session.

A third red appeared mid-session
(`test_forge_shadow_eval.py::test_every_skip_reason_the_strategies_emit_is_classified`,
10 unclassified reasons from `weather_arb.py`) and is **resolved** - the
weather agent added them to `SKIP_CLASSIFICATION`. It is green in the final
run above. This is the Convention 21 case: a red that meant "another session
is mid-edit", not "broken".

**Numbering note:** commit `7a05dcb`'s message says "D-299 to D-305", but no
D-305 body exists anywhere in the repo. Convention 24: a cited D-number is not
a decision. D-305 was free and this session took it.

---

## Not done / not verified

- **No cron installed for the critic.** Task 3 says "every 4 hours via cron".
  Installing a recurring job that writes to the hypothesis graph and the vault
  is an outward-facing, standing action; it is Aym's to authorise. The critic
  runs correctly on demand today.
- **Task 7's loop wiring.** The adapter can fill maker orders; the loop never
  asks it to. Named blocker, needs a design call, see above.
- **`write_daily_summary`'s Sonnet path** has unit coverage but has never been
  run against a real Sonnet turn. Every Opus path has been.
- **The weather calibration harness does not exist**
  (`backtest/measure_daily_extreme_calibration.py`). The kill condition names
  it, and every row admits it is absent rather than implying otherwise.
- **Nothing committed, nothing staged.** `git status` also shows unrelated
  modified files from concurrent sessions; I left all of them alone.

## Process claims, re-derived (Convention 25)

CLAUDE.md's table was stale by two rows at session end:

| what | claimed PID | actual |
|---|---|---|
| Polymarket shadow loop | 64196 | ALIVE |
| shadow_runner wrapper | 51148 | ALIVE, running pre-edit source |
| liquidation recorder | 48637 | ALIVE, still 0 rows |
| hyperliquid poller | 37578 | ALIVE |
| graveyard re-sweep | 18543 | **DEAD** - alive at 14:26, gone by 15:40. Nobody killed it deliberately. It was at ~9.5h. Its 535,425-row verification is unfinished. |

I killed nothing and restarted nothing.
