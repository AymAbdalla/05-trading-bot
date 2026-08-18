# Trading Bot Dashboard

Real-time web dashboard for the multi-asset trading bot. Dark theme, trading-terminal aesthetic.

## Run it

```bash
cd ~/aym/projects/05-trading-bot
source .venv/bin/activate
streamlit run dashboard/app.py --server.port 8501
```

Then open http://localhost:8501 in your browser.

## What it shows

**Overview tab:** Bot status (ALIVE/HALTED/PAPER), current balance, today's PnL, equity curve, win rate, Sharpe, profit factor, max drawdown, kill switch status.

**Live Trades tab:** Real-time trade log with auto-refresh. Color-coded (green wins, red losses, yellow open). Filterable by asset class, strategy, date.

**Strategies tab:** Performance table per strategy (trades, win rate, PnL, R-multiple, Sharpe). Bar chart of PnL by strategy.

**Graveyard tab:** 535,425 entries, verdict counts, top PASS concentrations, strategy health, judge assertion status.

**Agent Activity tab:** Audit log (orders, fills, halts, strategy changes), risk events.

## Tech

- Streamlit (web framework)
- Plotly (charts)
- SQLite read-only (safe alongside running engine, WAL mode)
- Auto-refresh every 5 seconds on live tabs

## Notes

- The dashboard reads from `db/trading.db` in read-only mode. It never writes.
- Works even when the bot is not running (shows empty states).
- Graveyard tab reads from `research/graveyard/summary.json` and `research/judge_evidence_pack.json`.
