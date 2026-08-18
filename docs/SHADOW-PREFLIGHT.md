# Shadow session preflight

Run this checklist BEFORE starting a shadow (paper) session with
`./run_shadow.sh`. Every command below was executed against this repo on
2026-08-17 unless it is explicitly tagged **UNVERIFIED**.

Paper and backtest only. This build has no live trading authority.

Run python as `env -u PYTHONPATH python3` everywhere (convention 14). Hermes
leaks its 3.11 venv onto PYTHONPATH and numpy then fails to import in a way
that reads as a broken install.

---

## 0. Is anything already running?

This working directory can be shared by two sessions (convention 21). Check
before you start anything.

```bash
ps aux | grep "[p]ython3 -m engine.main"
ps aux | grep "[p]ython3 backtest"
git status
```

Expect no engine process. A running backtest is fine but do not edit anything
it imports while it runs (convention 13).

## 1. Harness validity

No result is durable unless this exits 0 (convention 1). The script lives in
`backtest/`, not at the repo root.

```bash
env -u PYTHONPATH python3 backtest/validate_harness.py
echo "exit=$?"
```

Expect `Harness-validity checks: 21/21 passed` and `exit=0`.

Note: this REWRITES `research/graveyard/harness_validation.json`, so it dirties
the working tree by at least the `generated` timestamp. Confirmed on
2026-08-17: a re-run changed only that one line.

## 2. Test suite

```bash
env -u PYTHONPATH python3 -m pytest tests/ -q
```

Expect green. The suite takes roughly 4-5 minutes.

The 22 known-red tests in `tests/test_polymarket_risk_gate.py` were fixed on
2026-08-17: 17 were implementation defects in `engine/polymarket/risk_gate.py`
(NaN leaking into a verdict, unreadable positions buying headroom, case-
sensitive market and side keys, the taker fee sitting outside every cap, and
four more), 2 were defects in `engine/risk.py`, and 3 were wrong constants in
the test file itself. If any of them are red again, do not start a Polymarket
session.

## 3. Config is paper mode

```bash
env -u PYTHONPATH python3 -c "import yaml; print(yaml.safe_load(open('config.yaml'))['mode'])"
```

Expect exactly `paper`.

There are two independent refusals behind this, and `run_shadow.sh` is a third:

- `engine/main.py:46-51` exits 1 on any mode other than `paper`.
- `engine/polymarket/paper_adapter.py` sets `PAPER_MODE = True`
  unconditionally and `__init__` raises if it has been tampered with.
- `run_shadow.sh` gate 1 refuses before launching. Verified against a
  `mode: live` config (refused) and against a config with no `mode:` key at all
  (also refused, rather than defaulting to paper).

## 4. Environment

**No API key is required for a paper shadow run.** The only environment
variable the engine reads is `TRADING_DB_PATH` (`engine/db.py:12`, defaults to
`db/trading.db`). Verified: `getenv` / `environ[` appears nowhere else under
`engine/`.

`TRADING_LIVE_ACK` must be unset or empty. It is half of the two-key live
interlock described in `.env.example`; `run_shadow.sh` gate 2 refuses to start
if it is set (verified).

```bash
echo "TRADING_LIVE_ACK=[${TRADING_LIVE_ACK:-}]"   # expect []
echo "TRADING_DB_PATH=[${TRADING_DB_PATH:-db/trading.db}]"
```

List which keys in `.env` are populated, without printing any value:

```bash
env -u PYTHONPATH python3 -c "
for line in open('.env'):
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line: continue
    k, v = line.split('=', 1)
    print(f'{k}: {\"SET\" if v.strip() else \"empty\"}')
"
```

Keys from `.env.example` and who actually needs them:

| Key | Needed for a shadow run? |
|---|---|
| `BINANCE_API_KEY` / `BINANCE_API_SECRET` | No. Paper mode uses public market data. |
| `NOTION_TOKEN` | No. Journal writes only. |
| `ALPACA_API_KEY` / `ALPACA_API_SECRET` / `ALPACA_ENDPOINT` | No. Backtest data downloads only. |
| `FRED_API_KEY` | No. Macro series downloads only. |
| `TRADING_DB_PATH` | Optional. Defaults to `db/trading.db`. |
| `TRADING_LIVE_ACK` | Must be EMPTY. |

**Gotcha:** nothing in `engine/` loads `.env`. Only the three
`backtest/download_*.py` scripts call `load_dotenv`. Editing `.env` does not
change the engine's environment; export the variable in your shell if you need
it.

## 5. Kill switch drill

Do this every time, before the session, with nothing running. The full drill
below was executed on 2026-08-17 and every output shown is real.

```bash
# 1. Engage
env -u PYTHONPATH python3 botctl.py halt "preflight drill"
#    -> HALT written (id=f308fa56): preflight drill
#    -> Resume with: python3 botctl.py resume --ack f308fa56

# 2. Confirm the state is visible
env -u PYTHONPATH python3 botctl.py status
#    -> halted: True
#    -> halt info: {'halt_id': 'f308fa56', 'ts': ..., 'reason': 'preflight drill'}

# 3. Confirm the launcher refuses while halted
./run_shadow.sh
#    -> run_shadow: REFUSING TO START: clear it first: ... resume --ack <halt_id>

# 4. Confirm a wrong ack does NOT clear it
env -u PYTHONPATH python3 botctl.py resume --ack deadbeef
#    -> ack mismatch: expected halt id 'f308fa56'. Not resuming.   (exit 1)

# 5. Clear it
env -u PYTHONPATH python3 botctl.py resume --ack f308fa56
#    -> HALT removed. Engine resumes entries on its next cycle.

# 6. Confirm clear
env -u PYTHONPATH python3 botctl.py status   # -> halted: False
ls HALT                                       # -> No such file or directory
```

Substitute the halt id printed in step 1; it is a fresh random hex each time.

What a halt does on the crypto path, from the code:

- `engine/executor.py:185` blocks new entries and records `skip_reason='halted'`.
- `engine/executor.py:407-408` calls `_handle_halt()` on every step.
- `_handle_halt` (`engine/executor.py:112-144`) closes every open position with
  `exit_reason='halt'`, cancels resting paper orders, and drains the signal
  queue so nothing fires hours-stale on resume.
- The state survives a restart: the HALT file is on disk, not in memory.

What a halt does on the Polymarket path, from the code:

- `engine/polymarket/paper_adapter.py` imports `is_halted` from `engine.halt`
  and checks it as the FIRST guard in `simulate_taker_buy`, ahead of position
  limits, price bands and sizing. A halted window logs `SKIP / halted` and
  costs zero orderbook reads.
- `summary()` reports a `halted` flag, so an operator reading a session summary
  sees why the entry count is zero.
- Wiring verified live on 2026-08-17: the same order filled with the switch
  clear and was blocked with it engaged. Regression coverage is
  `tests/test_polymarket_halt_wiring.py` (12 tests), which also asserts that no
  module under `engine/polymarket/` hardcodes its own HALT path.

**The asymmetry matters.** On the crypto path HALT also FLATTENS. On the
Polymarket path it can only block new entries, because a binary held to
resolution has no sell path in paper mode. A halt does NOT close Polymarket
exposure; those positions stay open until the oracle resolves them. `botctl
status` says so explicitly rather than letting you infer otherwise.

`engine/halt.py` has no environment or config override by design. If you need
a different path for a test, monkeypatch `engine.halt.HALT_FILE` and understand
that you are patching the safety net.

## 6. Notional caps

Read them, do not assume them.

```bash
env -u PYTHONPATH python3 -c "
import yaml
c = yaml.safe_load(open('config.yaml'))
for k, v in c['risk'].items(): print('risk.' + k, '=', v)
print()
for k, v in c['polymarket']['risk'].items():
    if not isinstance(v, dict): print('polymarket.risk.' + k, '=', v)
"
```

Current values and what binds:

- `risk.notional_cap_usd: 100` - FIXED per-trade notional. Does not scale with
  balance. Read by `engine/risk.py:45`.
- `risk.max_trades_per_day: 1` - one entry per day. This is the cap most likely
  to make a shadow session look dead. It is intentional.
- `risk.max_concurrent_positions: 2`, `risk.max_positions_per_pair: 1`.
- `risk.consecutive_loss_pause: 4` - four losses in a row pauses entries 24h.
- `risk.daily_ops_stop_multiplier: 3` / `weekly_ops_stop_multiplier: 15` -
  equity drop past these AUTO-HALTS the engine (`engine/executor.py:209-210`
  escalates to `_trigger_auto_halt`, which writes the HALT file). Resume is
  human-only.
- `polymarket.risk.*` - a full set of caps, currently unreachable because no
  runner exists (section 7).

Raising any of these needs a D-number in `docs/DECISIONS.md`.

**Fixed 2026-08-17:** the daily and weekly ops stops used to be silently absent
on any period whose first equity snapshot landed after the boundary, including
every fresh database. `get_day_open_equity` looked backward only, returned
None, and `check_ops_backstops` guards on `is not None`, so the stop was skipped
without logging. It now falls forward to the earliest snapshot after the
boundary (`engine/risk.py::_period_open_equity`). Convention 11: no carry-in row
is not no drawdown.

## 7. Scope: what a shadow session actually covers

`run_shadow.sh` starts `engine.main`, which wires collector -> scanner ->
executor for the crypto pairs in `config.yaml` (`BTC/USDT`, `ETH/USDT`,
`SOL/USDT`). That is all it starts.

**The Polymarket path is not running.** Verified 2026-08-17: outside `tests/`,
nothing constructs `PolymarketPaperAdapter` or `PolymarketRiskGate`. There is
no Polymarket runner module. The package is library code with tests.

The halt IS wired into that path now (section 5), so when a runner is written
it inherits the kill switch rather than needing it retrofitted. But nothing on
that path has been scored: per D-268, every Polymarket strategy is NOT_TESTED
until `backtest/polymarket_harness.py` scores it on resolution PnL. Code that
exists is not a strategy that was tested.

## 8. What to watch in the logs

`run_shadow.sh` tees stdout to `logs/shadow_<UTC timestamp>.log`.

**`config.yaml logging.file: logs/engine.log` is not wired.** `engine/main.py`
calls `logging.basicConfig(level, format)` with no file handler, so the engine
logs to stdout only. Verified: no `FileHandler` or `RotatingFileHandler`
anywhere in the repo, and `logs/engine.log` does not exist. The tee in
`run_shadow.sh` is the only thing producing a log file.

```bash
tail -f logs/shadow_*.log
```

Healthy start:

```
engine running (paper mode): collector + scanner + executor
reconcile_on_boot: {'positions_checked': 0, 'positions_closed': 0, ...}
```

Lines that mean something is wrong or blocked (strings taken from
`engine/executor.py`):

| Log line | Meaning |
|---|---|
| `HALT file present: closing all positions, no new entries` | Kill switch engaged. |
| `AUTO-HALT (...); resume requires: botctl.py resume --ack <id>` | An ops backstop fired. Investigate before resuming. |
| `stale data, entry blocked` | No fresh signal-timeframe candle for 2x the interval. Feed problem. |
| `risk gate blocked entry: <reason>` | Normal when the reason is `max_trades_per_day` or `consecutive_loss_pause`. Not normal for `daily_ops_stop` / `weekly_ops_stop`, which auto-halt. |
| `signal expired before execution` | Queue latency. A few is fine; a stream of them is not. |
| `reconcile: closing unprotectable position` | A position had a missing or non-positive stop and was closed on boot. |

Cross-check from a second terminal at any time:

```bash
env -u PYTHONPATH python3 botctl.py status
```

Zero trades is the expected result of a short session at
`max_trades_per_day: 1`. Zero trades is not a failure, and it is not evidence
of anything either (convention 11).

## 9. How to abort

In order of preference:

1. **Graceful, from another terminal.** Closes positions, cancels resting
   orders, drains the queue, and persists across restarts:
   ```bash
   env -u PYTHONPATH python3 botctl.py halt "why you are stopping"
   ```
   Then stop the process with Ctrl-C once you have seen
   `HALT file present: closing all positions` in the log.

2. **Ctrl-C in the session terminal.** `engine/main.py:62-67` traps SIGINT and
   SIGTERM and shuts down in order (scanner, then executor, then collector) so
   no signal is left half-processed. This does NOT close open positions and
   does NOT persist. Use option 1 if anything is open.

3. **`kill <pid>`** sends SIGTERM and takes the same path as Ctrl-C.
   **UNVERIFIED:** the signal handler is registered in code that I read but I
   did not run a live engine and send it a signal.

After any abort, leave the HALT in place until you know why you stopped, then:

```bash
env -u PYTHONPATH python3 botctl.py status                 # get the halt_id
env -u PYTHONPATH python3 botctl.py resume --ack <halt_id>
```

---

## UNVERIFIED items in this document

- The healthy-start log lines in section 8. They are read from the format
  strings in `engine/main.py` and `engine/executor.py`; no engine was started
  during this session, so the exact rendered output was not observed.
- `kill <pid>` behaviour in section 9 (reasoning above).

Everything else in this file was executed. The kill-switch drill in section 5
is real output from a real run, on both the crypto and the Polymarket path.
