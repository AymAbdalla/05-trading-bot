# Handoff: config.yaml per-trade notional cap pinned to the delegated default

**Agent:** `cody-config-lock` (AGENT_ID probed EMPTY, sanctioned
`CONFLICT_CHECK_AGENT_ID` fallback used)
**When:** 2026-08-19, 08:45 EDT (measured with `date`, not estimated)
**Brief:** `docs/handoffs/from-raven/2026-08-19-config-drift-lock.md`
**Commit:** `e1c9754` (this handoff will follow in a second commit)

## What was built

One test, appended to `tests/test_polymarket_paper_adapter.py`:

`test_config_yaml_notional_cap_matches_the_delegated_default` loads
`config.yaml` and asserts BOTH per-trade surfaces equal
`risk_constraints.DEFAULT_LIMITS.per_trade_notional_usd`:

- `cfg['polymarket']['notional_cap_usdc']` (line 137, the paper adapter's
  config override) - this is the one that had NO lock.
- `cfg['polymarket']['risk']['notional_cap_usdc']` (line 215, the gate's) -
  explicit pin for a future reader, with a comment saying the scalar loop
  already covers it transitively.

No `config.yaml` change. No engine change. No behaviour change on any book:
all three numbers already read `10.0`. This closes open item 15.

## Task 3: the transitive-coverage claim CHECKS OUT

Raven asked me to verify rather than assume that
`test_config_yaml_matches_the_module_defaults`
(`tests/test_polymarket_risk_gate.py:1438`) really covers config line 215.
It does. Read, not assumed:

- The scalar loop includes `'notional_cap_usdc'` and compares
  `PolymarketRiskGate(cfg)` against `PolymarketRiskGate()`.
- `risk_gate.py:746` reads `risk.get('notional_cap_usdc', DEFAULT_NOTIONAL_CAP_USDC)`,
  so the from-YAML gate carries line 215 and the no-config gate carries the
  module default.
- `risk_gate.py:113` sets `DEFAULT_NOTIONAL_CAP_USDC = risk_constraints.DEFAULT_LIMITS.per_trade_notional_usd`.

So line 215 is chained to `DEFAULT_LIMITS` already. My added assertion on it is
redundant-but-explicit, exactly as the brief specified, and I did not duplicate
the loop itself.

## Measured result

Targeted run only, exactly as scoped:

```
.venv/bin/python -m pytest tests/test_polymarket_risk_gate.py \
    tests/test_polymarket_paper_adapter.py -q
```

**312 passed, 0 failed** (was 311 at `8a7e8b7`; +1 is the new test).
The new test was also run alone by node id: 1 passed.

Negative control: `11.0 != DEFAULT_LIMITS.per_trade_notional_usd` is True, so
the assertion is not vacuous - a drift in either config surface fails it.

**The full suite and `validate_harness.py` were NOT run.** The brief forbade
both. Open item 16 stays open; the 2026-08-20 restart already carries
"harness + suite" on its list and that is where it gets closed.

## NEW FINDING - a dead test in `tests/test_polymarket_risk_gate.py`. NOT fixed.

While verifying task 3 I found that `5864461` (`cody-risk-wire`) inserted the
two module-level D-343 delegation tests into the MIDDLE of the
`TestConfigWiring` class body. Everything after them that was still indented at
4 spaces fell inside the last module-level function instead of the class.

Concretely: `test_config_yaml_classification_tables_match_the_module`
(line 1525) is now a nested `def` inside
`test_config_yaml_max_total_exposure_matches_the_delegated_default`
(line 1513). It is never called. **It has not executed since `5864461`.**

Confirmed by AST, not by eye:

```python
import ast
t = ast.parse(open('tests/test_polymarket_risk_gate.py').read())
# -> MODULE FUNC test_config_yaml_max_total_exposure_matches_the_delegated_default 1513
#    nested: ['test_config_yaml_classification_tables_match_the_module']
```

It is the last thing in the file (file is 1543 lines), so nothing else was
orphaned. The lost coverage is the market-type-pattern / correlation-group
tables agreeing between `config.yaml` and the module - a real drift surface, of
the same species as the one this session just locked.

**I did not fix it.** The brief scoped my edits to
`tests/test_polymarket_paper_adapter.py` and said to report deviations rather
than force them. The fix is a 4-space de-indent plus moving the two
module-level functions below the class. Raven's call.

Worth noting for the pattern file: the suite was green the whole time. A
disappearing test does not fail anything. The count going 311 -> 312 also would
not have caught it.

## What was skipped or deferred

- Full suite, harness, `config.yaml`, `DECISIONS.md`, any engine file - all
  explicitly out of scope, all untouched.
- The dead test above.
- No restart, no signal, no process touched. All five live processes left
  alone; only this session's own `claude` process was on `ps`.

## Next steps for Raven

1. **Decide on the dead test.** Restore it, or record that the classification
   tables are deliberately unlocked.
2. Item 15 can be marked closed in your tracking - it is closed in `CLAUDE.md`.
3. Item 16 (suite + harness) is still open and belongs to the 2026-08-20
   restart.
4. Neither `8a7e8b7` nor `5864461` nor `e1c9754` is active in a running
   process. Convention 13. The risk wiring activates on the restart AFTER the
   ~03:45 EDT 2026-08-20 one.
