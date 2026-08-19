# Overnight profitability push - executed

**Cody, 2026-08-19 ~00:20 EDT.** Worked `docs/handoffs/from-raven/2026-08-18-overnight-profitability-push.md`
end to end. Full suite green (3,818 passed, 1 skipped, 0 failed), harness 21/21
PASS. **Not committed** - see "Commit status" at the bottom; the diff is
reported here for review instead.

## Headline correction before anything else: Task 1 was already done

The directive's Task 1 asked me to wire the maker fill simulation into
`shadow_loop.py` because "`shadow_loop.py` short-circuits every QUOTE into
`SKIP_MAKER`" and CLAUDE.md's "Standing corrections" section makes the same
claim ("the maker fill model EXISTS but is NOT WIRED... Convention 23").

**That claim is stale and false, verified against both the code and the live
database, not guessed:**

- `git blame` shows the wiring landed in commit `ea30111` (2026-08-18 16:22
  EDT) - the SAME commit D-320 already covers for an unrelated reason
  (`stop_px`). Its own commit message ("D-312 to D-315: wire the general
  binary market spaces, register proposal 024") never mentions maker fills,
  which is exactly why nobody re-checked the "NOT WIRED" claim afterward
  (convention 31 again: a commit message is a claim, not a fact).
- `engine/polymarket/shadow_loop.py:3101` calls `self.observe_maker_orders`
  as **Phase 2 of every cycle**, before exits and before entries, exactly as
  its own docstring says it must. The catch-all bucket the old claim describes
  (`maker_quote_not_simulable`) is gone from the file entirely - grep confirms
  zero occurrences outside comments explaining that it used to exist.
- `db/trading.db` has **71 real `PM_box_builder` positions and 102 real
  `PM_grid_hedge` positions**, opened between 2026-08-18 and 2026-08-19, all
  from `simulate_maker_buy`/`observe_resting_orders`. This is not "wired but
  unreachable" - it has been running and producing fills.
- Of those, **65 box_builder and 100 grid_hedge positions are closed**:
  box_builder is -$54.30 net (24.6% WR), grid_hedge is -$178.16 net (26.0%
  WR) - the exact grid_hedge number the directive's own diagnosis cited.

**No code change was made for Task 1.** `tests/test_maker_fill_wiring.py`
(30 tests, already committed, already green) is the wiring test that proves
this. I ran it in isolation to confirm before touching anything else.

**Consequence for the "profitability push" framing:** the maker path isn't a
missing feature holding back profitability - it's a second live bleed
alongside the fair-value family. See "Not acted on" below.

## Task 2 (D-321): raised the shadow cap 5 -> 10 - DONE

`config.yaml`: `polymarket.max_concurrent_positions` (line ~138) and
`polymarket.risk.max_concurrent_positions` (line ~217) raised 5 -> 10.

Doing this alone surfaced a wiring requirement the directive didn't mention:
`tests/test_polymarket_risk_gate.py::TestConfigWiring::
test_config_yaml_matches_the_module_defaults` asserts config.yaml's risk
numbers agree with the module's own `DEFAULT_*` constants (this is
convention 17's own enforcement, documented in the config block's header).
Raising only the config value without the module default would have shipped
config.yaml and the code disagreeing about the built-in fallback. Fixed both:

- `engine/polymarket/risk_gate.py`: `DEFAULT_MAX_CONCURRENT_POSITIONS = 5` -> `10`
- `engine/polymarket/paper_adapter.py`: adapter's own inline fallback `5` -> `10`

That surfaced two more failures - both tests that hardcoded "5 open positions
breaches the cap" or "9 open positions breaches the cap", written when the
cap was 5. Fixed both to derive the count from
`rg.DEFAULT_MAX_CONCURRENT_POSITIONS` instead of a literal, matching this
file's own "derived, never hardcoded" convention used elsewhere in the same
file (`tests/test_polymarket_risk_gate.py`,
`TestPerAssetDailyLossBreaker::test_disabling_does_not_disable_the_other_risk_caps`
and `TestVerdictShape::test_every_rejection_carries_a_reason`).

D-321 recorded in `docs/DECISIONS.md`. Full rationale, the starved-034
evidence (39 ENTER decisions, 0 opens, the exact skip-reason breakdown), and
why `max_total_exposure_usdc: 100.0` doesn't need to move with it are all in
the entry - not repeated here.

## Task 3 (D-322): paused fair_value_arb_hft and fair_value_arb_inverse - DONE

**Mechanism note, because the obvious approach doesn't work here.** Removing
either strategy from `build_strategies()`'s returned list would have broken
five different `len(names) == 25` / indexed pins across the test suite
(`test_fair_value_settlement_exit.py`, `test_longshot_fade_hold_to_
resolution.py`, `test_maker_fill_wiring.py`, `test_weather_bracket_width_
matched.py`, `test_weather_shadow_wiring.py`) - the registry is append-only
and multiple tests pin both the length and specific indices. Instead, both
classes now override `supported_market_types = ('smart_money',)`:

- `'smart_money'` is a real value in `MARKET_TYPES`, so `MarketContext`
  construction and the generic house-interface test
  (`test_no_strategy_raises_on_garbage`, which builds a context from
  `strategy.supported_market_types[0]`) both stay valid - an empty tuple
  breaks that test with an `IndexError`, which is what I shipped first and
  then caught by running the suite.
- No cycle in `shadow_loop.py` (`run_cycle`, `run_weather_cycle`,
  `run_space_cycle`) ever calls `_supporting(pool, 'smart_money')` - it is
  used only as `PM_smart_money_copy`'s own internal discovery-path tag, never
  as a routed polling universe. Declaring it is therefore equivalent to
  declaring membership in no universe any cycle polls.
- `build_strategies()` itself is untouched: both classes still construct at
  their pinned indices (10, 11), `len(names) == 25` still holds.
- Reverting is one line per file: delete the `supported_market_types`
  override.

D-322 has the critic's evidence (hft -$221/22.7% WR vs 66.7% break-even;
inverse -$65/48.1% WR vs 75% break-even) and states explicitly what was left
alone: the parent (034 needs its tape), `_wide`/`_patient` (not named by the
critic's KILL list), `dip_arb` (031's subject).

## Task 4: caller_feed SSL fix - BLOCKED, verified not guessed

Reproduced the exact error live, then spent the verification budget the task
asked for ("choose the cleanest... verify with one live fetch") on all three
named options plus the direct source. Every path failed for a different,
now-documented reason:

| target | result |
|---|---|
| `redlib.catsarch.com` (current) | TLS handshake itself fails: `SSL: TLSV1_ALERT_PROTOCOL_VERSION`. Forcing `ssl.TLSVersion.TLSv1_2` explicitly does not fix it; `TLSv1_3` is unsupported by this venv's SSL library at all (`Unsupported protocol version 0x304`). |
| `redlib.privacyredirect.com` (alt mirror) | Same TLS handshake failure as above. |
| `redlib.perennialte.ch` (alt mirror) | TLS succeeds, but the instance itself 500s on its own root page - the mirror is down. |
| `safereddit.com` (alt mirror) | TLS succeeds, root loads, but it has no `/user/<handle>.json` route at all (404) - a different redlib fork, HTML-only. |
| `redlib.tux.pizza`, `reddit.invak.id` (alt mirrors) | Timed out. |
| `www.reddit.com` direct | 403, bot-blocked - the reason the proposal routed through a mirror in the first place. |
| `r.jina.ai` wrapper (the documented TODO) | Reaches through (200), but renders redlib's "you are about to leave Redlib" JS interstitial as markdown text instead of returning the raw JSON listing - not a usable response, and confirms the module docstring's existing caution that this host's contract was unverified. |

**Root cause, confirmed by control test:** this venv's Python (the system
`Python3.framework` under `/Library/Developer/CommandLineTools/`) links
LibreSSL 2.8.3 (2018). `https://www.google.com` works fine through it, so the
library isn't universally broken - but any Cloudflare-fronted host in this
list either TLS-handshake-rejects it outright or WAF-403s it regardless of
User-Agent. `curl` (LibreSSL 3.3.6 via SecureTransport) completes the same
handshake against `redlib.catsarch.com` without issue and gets the same 403,
proving the TLS failure and the WAF block are two independent problems, not
one.

**No code shipped for this task.** Both remaining options are real but out
of scope for a code-only fix tonight: (a) upgrade this venv's Python/OpenSSL,
or (b) shell out to `curl` as `CallerFeed`'s transport (it's already
injectable via `transport=`) to work around the TLS problem specifically -
which would still need a working, un-blocked mirror behind it, and none of
the ones tried live tonight qualify. Flagged for Raven rather than guessed.

## Task 5: verify - DONE

- `pytest tests/ -q --ignore=tests/test_dashboard_charts.py`: **3,818 passed,
  1 skipped, 0 failed** (was 3,815 at session start; +3 net from the
  risk-gate default-sync fixes above, no new strategy tests added tonight).
- `backtest/validate_harness.py`: **21/21 PASS, exit 0.**
- Did NOT restart the shadow loop - see the next section, it isn't running
  regardless.

## Also found, unprompted: the shadow loop is dead

`ps -p 27490 -p 90158` returns nothing for either PID. CLAUDE.md's "DO NOT
KILL" table (re-verified by the prior session at 23:14 EDT) is now stale -
both the loop and its respawn wrapper are gone. I did not start a
replacement (the directive says report and wait for Raven's restart, and
that instruction holds regardless of whether the old process happened to
still be up). This means the ~30-minute starvation snapshot CLAUDE.md cites
is also now historical, not current - there has been no live polling since
some point before this session started.

## Not acted on - flagged for Raven, not unilateral

`PM_box_builder` (-$54.30) and `PM_grid_hedge` (-$178.16) are real, live,
closed-position losses from the maker path (see "Task 1" above) - the same
shape as the fair-value bleed D-322 addresses. Task 3 named only the two
fair-value variants; pausing the maker strategies was not authorized by this
directive and I did not do it. If Raven wants the same treatment applied
there, the same `supported_market_types` sentinel mechanism applies, but
note `PM_box_builder` and `PM_grid_hedge` are the ONLY two strategies
currently exercising the maker path at all - pausing both would make
`observe_maker_orders`'s wiring literally unreachable again, which is worth
deciding deliberately rather than as a side effect.

## Commit status: NOT committed

Working tree at session end:

```
 M config.yaml
 M docs/DECISIONS.md
 M engine/polymarket/paper_adapter.py
 M engine/polymarket/risk_gate.py
 M strategies/polymarket/fair_value_arb_hft.py
 M strategies/polymarket/fair_value_arb_inverse.py
 M tests/test_polymarket_risk_gate.py
?? docs/handoffs/2026-08-19-fix-034-open-leak-and-check-032.md
```

The untracked handoff is `cody-034-openleak`'s output, not mine - that
session's own diff to `fair_value_settlement_exit.py` and its test file is
already committed (`9d9a234`, visible in `git log`), so the tree isn't "mine
alone and otherwise clean." Per this directive's own rule ("Commit ONLY if
the tree is otherwise clean; otherwise report the diff"), I'm reporting
rather than committing. All seven of my changes are staged-ready; nothing
here conflicts with `9d9a234`.

## Next steps for Raven

1. Decide whether/how to restart the shadow loop - it is not running.
2. Rule on `PM_box_builder`/`PM_grid_hedge` (pause, tune, or leave as
   measured NOT_TESTED-but-losing).
3. Rule on Task 4 (`caller_feed` SSL): venv upgrade vs. curl-transport
   workaround vs. leave blocked.
4. Review and commit (or request changes to) the seven-file diff above.
