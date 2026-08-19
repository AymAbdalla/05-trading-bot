# Handoff: discovery pipeline design (cody-discovery-design)

**Session:** `cody-discovery-design`, 2026-08-19 ~09:5x EDT, opus, gateway spawn.
**Brief:** `docs/handoffs/from-raven/2026-08-19-discovery-design.md`.
**Deliverable:** `docs/DESIGN-2026-08-19-discovery.md`.
**Tree impact:** two new doc files. **No code, no repo created, no cron touched,
no live process signalled.** Read-only on everything else, per the brief.

---

## What was produced

`docs/DESIGN-2026-08-19-discovery.md`. It answers all five of Raven's questions,
attacks three parts of the proposed design on measured evidence, supplies the
repo skeleton, the ready-to-install cron prompt, the routing table, and the three
seed entries written out in full.

## The three challenges to Raven's design (all evidence-backed)

1. **No new repo. Use `vault/Discovery/`.** Q1's stated rationale ("visible in
   Aym's profile as a portfolio artifact") is factually wrong: private repos do
   not appear on a GitHub profile. `gh repo list` confirms `aym-knowledge-vault`
   and `aym-canon` are private/invisible while the four portfolio repos are
   public. Only the backup argument survives, and the vault already provides it.
   **Flagged as Aym's call**, because Aym's words were "its own separate repo"
   and I do not get to overrule that quietly. Nothing else in the design depends
   on the path.

2. **Kill `pending/`.** Measured this morning: `references/_incoming/` has been
   empty for 33 days (`.gitkeep` only); `vault/Inbox/` empty since creation;
   `strategy-handoffs/inbox/` holds 3 files stuck since 2026-08-17 while
   `processed/` was last written 2026-08-11, 8 days ago. Three staging folders,
   two never used, one draining slower than it fills. The control case is
   `vault/System/open-loops.md`, a flat single-writer ledger that gets committed
   multiple times a day and has never gone stale. Replace `pending/` with a line
   there. Same evidence retires `archive/`: `status:` in frontmatter is the
   state, exactly as `strategies/proposals/README.md` already rules.

3. **The risk is hype ingestion, not lost finds.** I counted today's digest
   (`~/.hermes/cron/output/e02ca6070099/2026-08-19_08-11-24.md`): ~20 items,
   ~15 unverified P&L claims, 3 with a stated mechanism. Keep rate ~15%. The
   digest's own footer flags the figures as hype. Proposed hard gate, THE
   MECHANISM RULE: an entry is written only if its claim names a mechanism in one
   sentence with no dollar figure. Note the current `daily-x-monitor` prompt
   ranks by `min_faves`, i.e. selects *for* the hype (and the prompt already
   records that `min_faves` is unavailable on our API tier anyway).

## The addition Raven's design was missing

`REFUTED` and `INFORMED` as terminal states, and **dedupe must consult REFUTED**.
The best find of the week (Kalman) was REJECTED, and the rejection produced
D-342 R5, now applied to every future proposal. Under Raven's original status
vocabulary (`catalogued|spec'd|developed|informed`) that entry has no valid
terminal state. Also: URL hashing alone would not have caught it, since the
Kalman tutorial (screenshots, no URL) and arXiv 2607.03015 arrived as two
different sources and were one decision. Fix is a claim-line grep against
REFUTED entries, not embeddings.

## Correction to the brief's premise

The brief says the streams "evaporate after delivery". **They persist.** The
Hermes cron runner writes every response to `~/.hermes/cron/output/<job_id>/`;
six X-monitor digests are on disk, oldest 2026-08-14. What is missing is
classification, dedupe, routing and a search surface. This makes the problem
cheaper AND means the index can be backfilled from six digests plus two Forge
files on day one instead of starting empty.

## Off-brief findings worth acting on

Both from `~/.hermes/cron/jobs.json`, re-derived not quoted (convention 25).

- **CLAUDE.md open item 1, first half, CLOSED.** The ONE restart cron IS
  installed: `b4b677c33385` / `keying-restart-spawn`, `kind: once`,
  `run_at: 2026-08-20T03:45:00-04:00`, `enabled: True`. It is a *Hermes* cron,
  not a system crontab, which is why looking for a crontab found nothing.
- **R-10 sharpened.** `f2bfd4085884` / `critic-eval-loop` exists at
  `interval: 240m` but is **`enabled: False`**. It is a one-flag change, not a
  Terminal.app/crontab job. Raven should confirm it was disabled deliberately
  before anyone flips it.

## Open item 12: AGENT_ID now reads SET on the gateway path

`os.environ.get('AGENT_ID')` returned `'cody-discovery-design'` on this gateway
spawn. Tally is now **2 EMPTY** (`cody-forge-reasoner-c2`, `cody-risk-module`)
against **2 SET** (`cody-kalman-discuss`, this session) on the same path. Still
not settled in either direction. Keep probing, never assume.

## Live siblings observed (convention 25, filtered on comm not argv)

- **PID 12477** (ppid 37068), `claude --model sonnet -p ... 2026-08-19-risk-module-wiring.md`.
  **LIVE at the time of writing.** This accounts for the dirty tree on
  `engine/risk/*`, `engine/polymarket/risk_gate.py`, `config.yaml` and the two
  risk-gate test files. **I did not touch any of them.**
- PID 15106 is this session.
- `engine.concurrency who` shows 2 checkouts, both `cody-038-ledger`
  (`docs/DECISIONS.md`, `engine/polymarket/resolution_ledger.py`), both stale:
  038 has committed and exited. Neither overlaps my paths.

The `dashboard/*.py` and `tests/test_dashboard_theme.py` modifications in the
tree belong to **neither** of the two live claude processes' briefs and I could
not attribute them. Flagging, not touching.

## What I did NOT do

- Did not create any repo, folder, cron job, or `sources.yaml`. Design only, per
  the brief's rules.
- Did not modify `daily-x-monitor`. The replacement prompt is written out in the
  design doc, ready to install, but installing it is Raven's call after Aym rules
  on section 10.
- Did not write to the vault.
- Did not spawn anything.
- Did not run the suite or the harness. **Nothing in this session touches Python**,
  so neither number moved. Do not treat this handoff as corroborating any suite
  count. Last known good, from the previous session and NOT re-derived here:
  4,072 / 1 skipped, harness 21/21.

## Questionable / incomplete

- **Section 10 has two questions blocking implementation**, both listed for Aym:
  the repo path, and whether the kill condition (3 entries at
  DEVELOPED/REFUTED/INFORMED by 2026-09-18 or the pipeline is deleted) stands.
  I recommend it stands. Infrastructure should pass the bar we make strategies
  pass.
- **Reddit is in `sources.yaml` but is unreachable from the cron environment**,
  per Forge cycle-2's own header. Left in with the note so nobody re-discovers
  it as a bug. Convention 11.
- I did not verify that Obsidian sync is actually set up on Aym's phone. Part of
  my argument for the vault is "it is the surface Aym already opens." If he does
  not actually open it, that argument weakens and the separate repo gets better.

## Next steps for Raven

1. Rule on the three challenges, especially the `pending/` evidence in section 2.
2. Answer 9.2: was `critic-eval-loop` disabled deliberately?
3. Take Aym's call on the repo path, then the build is mechanical: create the
   skeleton, install the section 7 prompt over `e02ca6070099`, run the one-time
   backfill over the six digests plus two Forge files.
4. Close open item 1's first half in `CLAUDE.md`: the restart cron is installed.
