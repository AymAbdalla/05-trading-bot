# PROVISIONAL - DO NOT USE AS GROUND TRUTH

**Status as of 2026-08-12:** Every result file in this directory was produced by a
backtest harness with confirmed correctness bugs, on data with confirmed integrity
problems. All entries are void pending re-run.

Why (full detail in `docs/handoffs/2026-08-12-claude-code-audit.md`):

1. The event-driven harness regime filter read future data (index misalignment).
2. The vectorized harness discarded every strategy's entry price, stop, and target.
3. Equity CSVs contained unadjusted stock splits (fake -90% crashes in NVDA, -66% in TSLA).
4. Several strategies in these results never actually executed (empty loops, window caps).
5. The validation suite that "certified" these runs never called the harness it validates,
   and `harness_validation.json` recorded `all_pass: false` BEFORE the full run was built.

Rules until the re-run lands:

- Quant / Forge / any agent: do not read these files as knowledge. Do not cite these
  numbers in briefings, research, or strategy design.
- Each JSON now carries a top-level `PROVISIONAL: true` flag. Consumers must check it.
- The re-run after harness fixes will replace these files and remove the flag.
