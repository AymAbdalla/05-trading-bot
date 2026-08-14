# Handoff: evidence pack committed, key audit PASS

**Date:** 2026-08-14
**Author:** Cody
**Instruction:** `docs/handoffs/from-raven/2026-08-14-evidence-pack-and-key-audit.md`
**Commits:** `5643bb0` (evidence pack), `7a994e3` (key audit). Both pushed to main.

## Result

Both tasks done. Audit came back clean, so the push went ahead as instructed.
Remote verified: `origin/main` now serves the DURABLE pack, 535,425 entries,
55 strategies, 155 distinct findings, `degraded` null.

## Task 1: evidence pack

- `research/judge_evidence_pack.json` committed. It was already the real
  post-purge file on disk, just uncommitted. The public repo had been serving
  the old empty pack.
- `research/graveyard/summary.json` regenerated with
  `env -u PYTHONPATH python3 backtest/summarize_graveyard.py`. Went from 15,120
  entries / 12 PASS / 3 distinct findings to 535,425 / 381 / 155. Every number
  matches the judge pack exactly, which is the check that mattered.
- `research/graveyard/harness_validation.json` committed. `all_pass: true`,
  generated 2026-08-14 13:45.
- `research/graveyard/README.md` rewritten. See below, I did not write what
  Raven asked me to write, and the reason matters.

## The one place I deviated

Raven's step 3 said the README should say "4 of 8 silent assertions flagging",
which is right, and CLAUDE.md describes `duplicate_strategies` as "C2 is
identical to C5/D1/D2/S1/S2 across all 264 compared rows". I pulled the actual
violations out of the pack before writing that down. It does not hold up.

C2 pairs with **all 54** other strategies at `identical_fraction` 1.0, not five.
And `trade_count_sanity` shows why: C2 produces zero trades in all 264 rows it
is compared on. Every one of those comparisons is empty against empty. The next
highest members of the duplicate list (`V2_vwap_magnet_sessionatr`,
`V5_capitulation_equity`, `V4_gap_hold_proxy`, `V4_trend_reclaim`,
`rising_three_methods`, `rsi_extreme`, `V3_intraday_momentum_crypto`,
`V5_forced_flow_crypto`) are the same eight strategies that top the zero-trade
list at 99%+.

So `duplicate_strategies` and `trade_count_sanity` are not two findings, they
are one: **8 strategies do not fire.** That is convention 3 failing out loud.

This changes the priority. CLAUDE.md's "what's next" item 4 says the duplicate
finding is the one that most affects how many distinct findings the pack really
has. It probably does not. Non-firing strategies contribute no PASS rows, so
they are not inflating the 155. The real cost is that 8 of 55 strategies were
never actually tested and are sitting in the graveyard looking like verdicts.
Fixing the firing should clear most of both assertions at once.

**For Raven:** CLAUDE.md and, if it says the same thing, D-226 both need this
correction. I did not edit DECISIONS.md, that is a reasoning change, not a
factual one, so per convention 15 it needs a decision or a version bump rather
than an in-place fix. Flagging it rather than doing it.

I also marked the pre-purge files in the README. Only `summary.json`,
`harness_validation.json`, `v0_graveyard_full.json` and the judge pack are
post-purge. The other ten outputs in that directory were built 2026-08-13 or
earlier, against the graveyard that still had the 23,595 bad futures rows.
`assertions.json` is the worst of them: 539 entries, one failing assertion, and
it is superseded by the `silent_assertions` block in the judge pack. Anyone
reading the directory cold would have taken it as current.

## Task 2: key audit

**PASS. Nothing exposed.** Full writeup in `docs/handoffs/2026-08-14-key-audit.md`.

Scope was 1,327 tracked files (451MB, including 1,038 CSVs and all 72 zips
decompressed) plus all 1,320 blobs across all 5 commits on every ref.

The check that actually settles it was not the regexes. I read the three live
secret values out of the real `.env` and searched for those exact byte strings
in every tracked file and every blob in the object database. Zero hits. Same
method on the Hermes webhook HMAC secret. Zero hits. `.env` was verified never
committed by listing each of the 5 commit trees for a path named exactly `.env`,
not by grepping, because the glob `.env.*` catches `.env.example` and produces
a misleading hit.

No secret value was printed or written anywhere. Probe files under `/tmp` were
deleted.

Four non-blocking findings, all hygiene:

- **F1** `.gitignore` covers `.env` exactly, not `.env.local` or
  `.env.production`. Nothing of that name exists, so nothing leaks. Foot-gun on
  a public repo. One-line fix: `.env*` plus `!.env.example`.
- **F2** `research/graveyard/archive/v0_graveyard_flatcost_partial_2026-08-13.json`
  is 128MB, untracked, and **not** ignored. The pattern only covers
  `v0_graveyard_full*.json`. It is over GitHub's 100MB hard limit, so a careless
  `git add -A` makes a commit that cannot be pushed and needs a rewrite to undo.
  Fix: `research/graveyard/archive/*.json`. This is why I staged by explicit path
  today and never used `-A`.
- **F3** Telegram chat ID `TELEGRAM_CHAT_ID` is public in `CLAUDE.md` (lines 31, 174)
  and `docs/handoffs/2026-08-13-cody-session-1.md` line 6. Not a credential, it
  is useless without the bot token and the bot token is not in the repo. Stable
  personal identifier on a public repo. Aym's call, I am not recommending action.
- **F4** `.env.example` is missing `FRED_API_KEY` and `ALPACA_ENDPOINT`. A
  completeness gap, the opposite of a leak.

I did not fix F1 or F2. They are `.gitignore` changes that were not in scope and
Raven had a separate stale-analysis-and-gitignore handoff open. Say the word and
they are two lines.

## What was not checked

Stated so this is not over-read: this repo only, local refs only, no GitHub-side
check of forks or Actions logs. It does not prove the Alpaca keys were never
exposed by some other route. D-262 rotation is still worth doing on principle,
not because of anything found here.

## Still open

- The five graveyard outputs still need reading together (D-264, the actual
  backtesting work). Not started this session.
- The 8 non-firing strategies. This is the concrete version of "decide what to
  do about the failing assertions".
- The ten pre-purge outputs in `research/graveyard/` need rebuilding before
  anyone cites them.
- Untracked handoff docs from earlier today are still uncommitted. I only
  committed the audit and Raven's instruction file, since the rest were not in
  scope for this session.
