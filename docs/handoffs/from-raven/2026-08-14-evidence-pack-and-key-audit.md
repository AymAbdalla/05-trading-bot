# From Raven: Commit judge evidence pack + full key audit

**Date:** 2026-08-14
**Status:** Aym approved both. Execute now.

## Task 1: Commit the real judge evidence pack

The current judge evidence pack on the public repo is the old empty one (0 entries). Replace it with the real one.

1. Copy `research/judge_evidence_pack.json` (the DURABLE one with 535,425 entries) into the committed research directory
2. Also regenerate `research/graveyard/summary.json` by running `python3 backtest/summarize_graveyard.py`
3. Rewrite the `research/graveyard/README.md` to reflect the current state: DURABLE, 535,425 entries, 55 strategies, 381 PASS, 155 distinct findings, 4 of 8 silent assertions flagging
4. Commit with message: "Replace stale evidence pack with DURABLE judge pack (535,425 entries, 55 strategies)"
5. Push to main

## Task 2: Full key audit

Run a thorough key audit on the public repo. This is not a scan, this is an audit.

1. Check every tracked file for:
   - API keys (Alpaca, Binance, FRED, Notion, Telegram, any other service)
   - OAuth tokens or secrets
   - Passwords or credentials
   - Private URLs with embedded auth
2. Check git history (all commits) for any of the above using `git log -p --all -S "pattern"` for each key pattern
3. Check .env.example to make sure it has placeholder values only, no real keys
4. Check that .gitignore covers .env, .venv, and any other secret-bearing files
5. Write the audit results to `docs/handoffs/2026-08-14-key-audit.md` with a pass/fail for each check
6. If anything is found, flag it immediately and do NOT push until it is resolved

Use `env -u PYTHONPATH python3` for any python commands (D-257).
