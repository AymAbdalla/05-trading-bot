# DESIGN: the discovery / knowledge-capture pipeline

**Author:** Cody (Opus), 2026-08-19 ~09:5x EDT.
**Brief:** `docs/handoffs/from-raven/2026-08-19-discovery-design.md`.
**Status:** DESIGN ONLY. Nothing built, nothing created, read-only session.
**Decision owner:** Aym. Raven and Cody both have open calls listed at the end.

---

## 0. Verdict up front

I agree with the pipeline SHAPE (gather, extract, classify, route, document,
follow through). I am rejecting three parts of the proposed structure, each on
measured evidence from this workspace, not on taste:

| Raven proposed | My call | Why (measured) |
|---|---|---|
| New repo `aym-discovery` | Use `vault/Discovery/` | The vault is already a separate private repo, already pushed today, already in Cody's session-start routine, already in an app Aym opens. A 4th knowledge repo starts empty and unread. |
| `pending/` folder | One line in `vault/System/open-loops.md` | Every staging folder in this workspace is dead or backlogged. `open-loops.md` is the only tracking surface with real throughput. |
| Priority `critical/valuable/interesting/noise` | `ACT / RECORD / DROP` | Priority-by-vibe does not force a decision. Priority-by-action does. |

And one addition Raven's design is missing, which I think is the single most
important part:

> **The terminal state `REFUTED` is mandatory, and dedupe must consult it.**
> The best find of this week (the Kalman tutorial) was REJECTED, and the
> rejection produced D-342 R5, a general rule now applied to every future
> proposal. A pipeline that only records what we USED would have recorded
> nothing for it.

---

## 1. Corrections to the brief's premises

The brief is right about the problem and wrong about two facts. Both change the design.

**1.1 The X monitor DOES persist. It does not classify.**

The brief says the streams "evaporate after delivery". They do not. The Hermes
cron runner writes every job response to disk:

```
~/.hermes/cron/output/e02ca6070099/2026-08-14_12-31-46.md
                                   2026-08-15_08-00-36.md
                                   2026-08-16_08-00-26.md
                                   2026-08-17_08-00-39.md
                                   2026-08-18_08-01-09.md
                                   2026-08-19_08-11-24.md
```

Six X-monitor digests are on disk right now, oldest 2026-08-14. Forge's output
is in `strategies/proposals/external-signals-2026-08-19.md` and
`-cycle2.md`, both git-tracked.

What is missing is not persistence. It is **classification, dedupe, routing and
a search surface**. That is a smaller and cheaper problem than the brief frames,
and it means **we can backfill from day one instead of waiting for tomorrow's
run**. Six digests plus two Forge files are the seed corpus.

**1.2 A private GitHub repo is NOT a portfolio artifact.**

Q1's stated rationale is "visible in Aym's profile as a portfolio artifact
showing research discipline". Private repos do not appear on a GitHub profile
to anyone but Aym. `gh repo list` confirms the shape: `aym-knowledge-vault`
and `aym-canon` are private and invisible to visitors; `05-trading-bot`,
`06-career-agent`, `account-scorer-v2`, `gtm-ai-engine` are the public
portfolio.

So the portfolio argument for a new repo collapses. Only the backup argument
survives, and the vault already provides that.

---

## 2. The evidence against `pending/`

This is the part I want Raven to actually argue with, because it is the failure
mode that kills the whole thing.

Measured this morning:

| Staging folder | Created | Contents now | Throughput |
|---|---|---|---|
| `~/aym/references/_incoming/` | 2026-07-17 | `.gitkeep` only | **0 items in 33 days** |
| `~/aym/vault/Inbox/` | 2026-08-14 | empty | **0 items in 5 days** |
| `~/aym/strategy-handoffs/inbox/` | 2026-07-20 | 3 files, oldest 2026-08-17 14:55 | **backlogged 2 days; `processed/` last written 2026-08-11, 8 days ago** |

Three staging folders. Two never used once. One accumulating faster than it
drains. The `strategy-handoffs` README explains exactly why: step 5 is "Aym
tells Raven: import the latest handoff." **A queue that needs a human to say
"go" is a queue that stops when the human is busy.**

Now the control case. `vault/System/open-loops.md` is a flat, single-writer,
one-line-per-item ledger with the resolution written inline on the same line.
Its git log shows commits multiple times a day, every day:

```
48fe9a6 open-loops: keying-prep review DONE, queue held (keying-restart cron-owned)
c9220c1 open-loops: reconcile done (737a461), D-328 back-filled ...
ccc746c open-loops: review 4 new 08-19 handoffs ...
268c43b update open-loops: 034 leak fix done, overnight push running ...
```

It works because there is nothing to move. An item is closed by editing its own
line, not by relocating a file. **Do not add a fourth inbox. Add a line to the
one ledger that has never gone stale.**

Same argument retires `archive/`. Moving processed entries after 30 days is a
second custodial chore with no reader. `status:` in the frontmatter is the
state, exactly as `strategies/proposals/README.md` already rules for proposals:

> "A REJECTED proposal stays in the directory. Deleting it loses the record that
> the idea was considered, and the next Forge run proposes it again."

---

## 3. The real risk is not losing finds. It is ingesting hype.

Raven's closing line is "a discovery repo nobody reads is a graveyard." Agreed,
and here is the mechanism by which it becomes one.

I counted today's digest (`2026-08-19_08-11-24.md`), the freshest real input we
have. Roughly 20 items. The dollar claims in it: +$81,323, +$143,379, +$243,619,
+$351,356, +$78,083, $218K, $70K/month, $200K/month, $20K, +$847. The digest's
own footer says it plainly:

> "The $243K/$351K Polymarket figures are unverified claims circulating in these
> threads, treat as hype signal, not fact."

Items with an actual stated MECHANISM, on the same day: three.
1. spot-vs-TWAP lag (spot > TWAP > strike plus BTC momentum implies UP)
2. `glassboxbots`, real-money bots published with method AND production bugs
3. the "50+ Polymarket bots" analysis article

**Keep rate is roughly 15%.** A pipeline that catalogs "what we found" will fill
with engagement bait at 6:1, and then Aym stops opening it. So the entry bar has
to be structural, and I want it stated as a hard rule:

> **THE MECHANISM RULE.** An entry is written only if its `claim:` can be stated
> in one sentence that names a MECHANISM and contains no dollar figure and no
> P&L claim. If the only thing a source offers is how much someone made, it is
> DROP. The number is not evidence; the mechanism is the thing we can test.

This is the same instinct as convention 20 ("a silent continue is a missing
number") pointed the other way: a number with no mechanism is a missing idea.

Note that the current `daily-x-monitor` prompt actively selects AGAINST this.
Its six standing queries are ranked by `min_faves` (5, 10, 20), i.e. by
engagement, which is precisely what the hype accounts optimise. The prompt also
already records that `min_faves` is not available on the current X API tier, so
the filter is being applied by hand anyway. See section 7 for the rewrite.

---

## 4. Architecture (recommended)

```
~/aym/vault/                       (existing private repo: aym-knowledge-vault)
  Discovery/
    README.md                      what this is, the mechanism rule, how to read it
    INDEX.md                       one line per entry, the search surface
    sources.yaml                   standing queries per domain
    entries/
      2026-08-19-kalman-pairs-binaries.md
      2026-08-19-belief-to-trade-layer.md
      ...
    domains/
      trading-bot.md               rolling digest, what is open per domain
      career-agent.md
      hermes-setup.md
      claude-setup.md
      gtm-stack.md
      future.md
```

Deleted from Raven's skeleton: `pending/` (section 2), `archive/` (section 2).

Everything else keeps Raven's shape. `domains/*.md` earns its place because it is
the thing a human skims; `INDEX.md` is what an agent greps.

### Entry frontmatter

```yaml
---
id: 2026-08-19-kalman-pairs-binaries
found: 2026-08-19
source: aym            # aym | x | reddit | arxiv | github | web | forge
url: none (8 screenshots sent by Aym)
url_sha1: none
domain: trading-bot
claim: >
  Kalman-filtered hedge ratio on a cointegrated pair gives a z-score entry
  that is forecast-free.
priority: ACT          # ACT | RECORD   (DROP never gets a file)
status: REFUTED        # CATALOGUED | SPECD | DEVELOPED | REFUTED | INFORMED
routed_to: D-342 R1, R5; engine/risk/ (e32bdd7, 161b12f)
---
```

`claim` is the load-bearing field. It is what the mechanism rule gates on, what
a human reads in `INDEX.md`, and what catches a re-arrival (section 5).

### Status vocabulary (borrowed wholesale from `strategies/proposals/README.md`)

- `CATALOGUED` filed, nothing done yet
- `SPECD` a proposal or spec exists, names it
- `DEVELOPED` built, `routed_to` carries the commit hash
- `REFUTED` tried and rejected, `routed_to` carries the D-number
- `INFORMED` changed a decision without becoming a build

`REFUTED` and `INFORMED` are the two Raven's list omits, and between them they
cover the two most valuable outcomes we have actually had.

### Kill condition (convention 6: a number and a named measurement)

> If by **2026-09-18** (30 days) fewer than **3** entries have reached
> `DEVELOPED`, `REFUTED` or `INFORMED`, the pipeline is producing catalog with no
> follow-through and gets DELETED, not maintained.
> Measurement: `grep -c 'status: \(DEVELOPED\|REFUTED\|INFORMED\)' vault/Discovery/entries/*.md`.

A discovery system with no kill condition is exactly the thing this project
refuses to accept from a strategy proposal. It should not get an exemption for
being infrastructure.

---

## 5. Answers to the five questions

**Q1. Repo name and placement.**
`vault/Discovery/`, not a new repo. The portfolio rationale is void (section
1.2). What is left is backup and separation from project repos, and the vault
delivers both today: it is a distinct private repo, git-tracked, pushed
2026-08-19 09:51. It is also the only one of these surfaces Aym has an app for.

Honest counterpoint, because it is Aym's directive and I am not going to bury
it: Aym said "document everything in its own separate repo." The strictest
reading of that is a brand new repo. My reading is that the intent is
"separate from the project repos so discovery does not clutter them", which
`vault/Discovery/` satisfies. **Aym's call.** If he wants the literal separate
repo, the entire design below is unchanged, only the path moves. Nothing here
depends on it.

One real caveat either way: the vault is currently 21 markdown files with a
single entry in `Decisions/`. It is underused, not overloaded. That is an
argument for putting discovery there (give it traffic) and simultaneously a
warning (the vault has not proven it gets read either). The kill condition in
section 4 is what settles that argument with a measurement instead of an
opinion.

**Q2. Dedupe.**
Keep the URL hash. It is right and it is cheap. But it is not sufficient, and
the failure is already in our history: the Kalman tutorial (screenshots, no URL)
and arXiv 2607.03015 arrived as two different sources and were ONE decision
(D-342). Bregman projection will re-arrive next month from a different blog with
a different URL.

So: `url_sha1` for exact dedupe, plus a **claim-line check against REFUTED
entries** before writing anything. If a new find's mechanism matches a REFUTED
entry's `claim`, it is DROPped with a pointer to the D-number, not filed again.
That is the direct fix for the problem `strategies/proposals/README.md` names
("the next Forge run proposes it again").

No embeddings, no similarity search. A grep and a human eye. Agreed with Raven:
keep it dumb, keep it honest.

**Q3. Routing depth.**
Yes, memory gets ONE line, and I would go stricter than Raven: memory gets a line
only if the fact changes behaviour **without the repo being read**. "Bregman
projection exists" fails that test. "gamma's bestBid/bestAsk read 0.63/0.64 while
the live CLOB book was 0.06/0.08, read the book" passes it. Most finds get zero
memory lines.

On "should some finds skip the repo entirely": no. If it was worth a skill it was
worth a one-line index entry pointing at the skill. The index entry is what stops
the same source being re-evaluated from scratch in six weeks. The cost is one line.

**Q4. Follow-through.**
No new cron. Raven's worry is correct, and `cody-handoff-sweep` is already
carrying 1,818 characters of prompt including two hardcoded "do not re-review
this" exceptions. Loading it further will break it.

Better, and it needs zero new machinery: **the entry id travels with the work.**
The from-raven brief that spawns Cody carries `discovery: <entry-id>` in its
header, Cody's handoff reports it back, and the sweep closes the line in
`open-loops.md` the same way it closes everything else. This is exactly how
proposal numbers already flow (038 to `1c5a761`, 039 gated on it). We are not
inventing a tracking convention, we are reusing the one that works.

**Q5. Forge.**
Forge keeps its own file. It must NOT write into the discovery repo.

Two reasons. First, `strategies/proposals/external-signals-<date>.md` is an input
to Forge's own step 3 (the Opus reasoner reads it), so it is not merely an
output to be relocated. Second, and this is the serious one: this workspace has
a documented history of cross-writer damage, logged in `open-loops.md` for
2026-08-19 alone (a phantom duplicate agent committing `b1d44bb`, a peer running
`git add -A` and sweeping another session's three files into `4d03681`, a forged
trailer on `e756af3`). Adding a second writer to a shared knowledge repo, on
this machine, with these actors, is asking for it.

So: **one writer per file.** The morning pipeline READS Forge's latest
external-signals file and promotes only items clearing the mechanism rule. Forge
does not know the discovery repo exists.

---

## 6. Routing rules (the operative table)

Applied per find, in order. First match wins.

| Test | Route |
|---|---|
| `claim` cannot be stated without a dollar figure | **DROP.** No file. |
| `url_sha1` already in INDEX.md | **DROP.** Already ours. |
| mechanism matches a `REFUTED` entry's claim | **DROP**, log the D-number in the digest so Aym sees it was caught |
| changes behaviour without reading the repo | entry + **ONE line to memory** |
| a reusable procedure with commands | entry + **skill** |
| actionable in the current universe, evidence is real | entry `priority: ACT` + **from-raven brief to Cody** carrying `discovery: <id>` |
| real but out of current universe or blocked | entry `priority: RECORD`, `status: CATALOGUED` |
| everything else | **DROP** |

"Actionable in the current universe" is doing real work in that table. Signal 5
in Forge's cycle-1 file (mention-market "No" bias) is a good idea for political
markets and we trade crypto Up/Down, so it is `RECORD`, not `ACT`. Forge already
graded it that way by hand. The table just makes it a rule.

---

## 7. The cron prompt, ready to install

Replaces `daily-x-monitor` (`e02ca6070099`, `0 8 * * *`). Same slot, same
schedule. The queries drop `min_faves` (unavailable on our API tier, and it
selects for exactly the hype we are trying to exclude) in favour of mechanism
words.

```text
You are Raven. Run the daily discovery pipeline. Read-only on project repos.
Write only to ~/aym/vault/Discovery/.

STEP 1 - GATHER. Search each standing query in
~/aym/vault/Discovery/sources.yaml (X via xurl, arXiv, GitHub, web). Also READ,
do not modify, the newest strategies/proposals/external-signals-*.md in
~/aym/projects/05-trading-bot/. Cover the last 24h.

STEP 2 - THE MECHANISM RULE. For each candidate write a one-sentence claim
naming the MECHANISM. If the claim cannot be written without a dollar figure or
a P&L boast, DROP it. This is the whole filter. Expect to drop most of what you
find; a keep rate near 15% is correct, not a failure.

STEP 3 - DEDUPE. Normalise each surviving URL, sha1 it, and check INDEX.md.
Drop exact matches. Then grep the claim against entries with status REFUTED and
drop anything whose mechanism we already rejected, noting the D-number.

STEP 4 - WRITE. For each survivor create
~/aym/vault/Discovery/entries/YYYY-MM-DD-<slug>.md with the frontmatter schema
in Discovery/README.md, append one line to INDEX.md, and update the matching
domains/<domain>.md.

STEP 5 - ROUTE. Apply the routing table in Discovery/README.md.
- priority ACT: write a from-raven brief into the target project's
  docs/handoffs/from-raven/ with `discovery: <entry-id>` in the header, and add
  a line to ~/aym/vault/System/open-loops.md. Do NOT spawn Cody from here; the
  handoff-sweep queue owns spawning.
- one-line durable fact: add to Hermes memory.
- reusable procedure: draft the skill.
- everything else: leave at status CATALOGUED.

STEP 6 - COMMIT. Commit the vault with a descriptive message. Never commit
secrets.

STEP 7 - DELIVER. Telegram digest to Aym, under 1500 chars:
what was kept (claim + link), what was ROUTED and where, what was dropped as
already-refuted (with the D-number), and the running count of entries at
DEVELOPED/REFUTED/INFORMED against the 3-by-2026-09-18 kill condition.
If nothing cleared the mechanism rule, say "nothing cleared the bar today" and
stop. A quiet day is a correct outcome, not a failed run.
```

**Backfill, one time, before the first scheduled run:** the six existing digests
in `~/.hermes/cron/output/e02ca6070099/` and the two Forge external-signals files
run through steps 2 to 5. That seeds the index with real history instead of an
empty directory.

### `sources.yaml` starting shape

```yaml
trading-bot:
  x: ["Polymarket arbitrage mechanism", "prediction market structural edge",
      "complete set arbitrage", "Polymarket maker rebate"]
  arxiv: ["q-fin.TR", "cs.GT"]          # filter: prediction markets, market making
  github: ["polymarket bot", "prediction market arbitrage"]
  reddit: ["r/algotrading", "r/Polymarket"]   # note: blocked from the cron env,
                                              # see Forge cycle-2 header
hermes-setup: {x: ["Hermes agent skills", "Hermes agent memory"]}
claude-setup: {x: ["Claude Code subagents", "Claude Code hooks"],
               web: ["Anthropic engineering blog"]}
career-agent: {x: ["GTM engineer", "AI SDR agent"]}
gtm-stack:    {x: ["Clay alternative", "GTM automation"]}
future:       {}
```

Reddit is listed but is currently **unreachable from the cron environment**, per
the cycle-2 file's own header ("blocked fetches, JSON API refused"). Left in the
config with the note so nobody re-discovers it as a bug. Convention 11: could not
run is not the same as ran and found nothing.

---

## 8. Seed entries

Written out in full so the shape is concrete and Raven can attack the schema
against real cases rather than a template.

### `entries/2026-08-19-kalman-pairs-binaries.md`

```yaml
---
id: 2026-08-19-kalman-pairs-binaries
found: 2026-08-19
source: aym
url: none (8 screenshots sent by Aym, 2026-08-19 morning)
url_sha1: none
domain: trading-bot
claim: >
  A Kalman filter estimates a time-varying hedge ratio between two cointegrated
  series, and a z-score on the residual gives forecast-free entries.
priority: ACT
status: REFUTED
routed_to: D-342 R1 (rejected), D-342 R5 (general rule)
---
```

Body: rejected on ALGEBRA, not on backtest. A Polymarket short is a purchase of
the complement, so the pairs trade is `buy a1-UP + buy a2-DOWN` and the payoff
is linear: `edge = (q1-p1) - (q2-p2)`. The joint distribution drops out of
expected value, so correlation, cointegration and beta (everything a Kalman
filter estimates) affect VARIANCE ONLY, never return. It is a forecaster in
disguise. Supporting numbers: best t=1.63 on n=54; leave-one-asset-out swings t
from -0.21 to +3.08; spread dispersion expands 0.104 to 0.547 at settlement, so
the rolling z-score is structurally invalid.

**Why this is the flagship entry:** it is the highest-value find of the week AND
it was rejected. It produced D-342 R5, now applied to every future proposal:
*a forecast-free strategy is one whose payoff is guaranteed by an IDENTITY, not
one whose signal is computed without a forecast.* Under Raven's original status
vocabulary this entry has no valid terminal state.

### `entries/2026-08-19-belief-to-trade-layer.md`

```yaml
---
id: 2026-08-19-belief-to-trade-layer
found: 2026-08-19
source: arxiv
url: https://arxiv.org/abs/2607.03015
url_sha1: <compute at write time>
domain: trading-bot
claim: >
  The gap between a calibrated forecast and a profitable book is the trading
  layer (sizing, exposure caps, stops, drawdown halt), not the forecaster.
priority: ACT
status: DEVELOPED
routed_to: engine/risk/constraints.py, engine/risk/events.py (e32bdd7, 161b12f);
           D-342 R2 adopted-inactive; D-342 R3 quarter-Kelly REFUSED
---
```

Body: "Beyond Forecasting: The Belief-to-Trade Layer in Prediction-Market
Agents." Same delivery as the Kalman tutorial, opposite outcome, which is why
they must be two entries and not one. Adopted: the deterministic risk module
(per-trade $10, per-event $30, aggregate $60, drawdown halt 25%, all measured
off `db/trading.db`). Refused: quarter-Kelly, measured **1.09x worse** per share
on our book (n=1,299, -0.0231 to -0.0253). Right formula, wrong input, since
Kelly multiplies whatever calibration you have and ours is negative. Currently
in the tree and INACTIVE: `evaluate_and_record` has no caller, and two blockers
(cap duplication with the Polymarket risk gate, and a non-decorative drawdown
halt would stop the shadow book at 35.99% observed max drawdown) are open for
Raven.

### `entries/2026-08-19-bregman-projection-arb.md`

```yaml
---
id: 2026-08-19-bregman-projection-arb
found: 2026-08-19
source: forge
url: https://layerx.xyz/blog/polymarketbots
url_sha1: <compute at write time>
domain: trading-bot
claim: >
  Frank-Wolfe / KL-divergence projection finds multi-outcome markets whose
  prices violate the probability simplex and returns the optimal arb allocation.
priority: RECORD
status: CATALOGUED
routed_to: none yet; adjacent to proposals 026, 036, 037
---
```

Body: the multi-outcome generalisation of complement no-arb. Genuinely
structural (it is a violated identity, so it passes D-342 R5, unlike the Kalman
z-score). **RECORD not ACT**, for a specific reason: proposal 037 rule 1
explicitly refuses to extend to multi-outcome until the keyed tape exists, and
037 is currently NOT_TESTED with 0 of 359 pairs qualifying because the
complement leg is the venue's exact arithmetic reflection to 1e-9 (floor 1.001
against a 0.996 gate). Its re-derivation is gated on 24h of keyed tape from
~03:28 2026-08-20. Promoting this to ACT before then would burn a build slot on
a premise we cannot yet measure. The source's own author notes the signals are
rare and compress rapidly.

**This entry is the argument for the `RECORD` tier existing.** It is a good idea
that is correctly not being worked on today, and without a file it gets
re-proposed by the next Forge cycle.

---

## 9. Two things I found that are off-brief but shouldn't wait

Both are open items in `CLAUDE.md` that I can close or sharpen from
`~/.hermes/cron/jobs.json`. Convention 25 applies (a doc is a claim), so these
are re-derived, not quoted.

**9.1 The 2026-08-20 restart cron IS installed.** Open item 1 says "nobody has
confirmed the cron for the ONE restart is INSTALLED." It is:

```
id: b4b677c33385   name: keying-restart-spawn
schedule: {kind: once, run_at: 2026-08-20T03:45:00-04:00}   enabled: True
```

It is a Hermes cron job, not a system crontab, which is why looking for a
crontab found nothing. Open item 1's first half can be closed.

**9.2 The critic cron is still NOT running.** Same file, second half of open
item 1 and R-10:

```
id: f2bfd4085884   name: critic-eval-loop
schedule: {kind: interval, minutes: 240}   enabled: False
```

It exists but is **disabled**, so this is a one-flag change and does not need
Terminal.app or a crontab at all. Worth Raven confirming whether it was disabled
deliberately before anyone flips it.

---

## 10. Open calls

**For Aym:**
1. `vault/Discovery/` (my recommendation) or a literal new `aym-discovery` repo?
   Everything else in this design is identical either way, only the path moves.
2. Does the kill condition stand (3 entries at DEVELOPED/REFUTED/INFORMED by
   2026-09-18, or the pipeline is deleted)? I think infrastructure should have to
   pass the same bar we make strategies pass, but that is a call, not a fact.

**For Raven:**
3. Attack section 2 if you can. If `pending/` survives that evidence, I want to
   see the argument, because the same evidence says `strategy-handoffs/inbox/`
   has 3 files waiting on you right now from 2026-08-17.
4. Does replacing `daily-x-monitor` outright (section 7) lose anything you value?
   I think the engagement-ranked digest is negative-value given the 6:1 hype
   ratio, but you own that job.
5. 9.2: was `critic-eval-loop` disabled deliberately?

**Not decided here, deliberately:** whether the six backfill digests get entries
retroactively dated to their own capture date or all dated to the backfill day.
I lean their own date (the index is a research record, not a changelog), but it
affects nothing until the repo exists.

---

## 11. What this design does NOT do

- It does not spawn Cody. Routing writes a brief; `cody-handoff-sweep` owns
  spawning, unchanged.
- It does not touch `strategies/proposals/`, `market_tape`, `config.yaml`,
  `DECISIONS.md`, or any live loop. Nothing here interacts with the 2026-08-20
  restart.
- It does not add a cron. It replaces one and reuses two.
- It does not create a second writer to any file.
