# Comm design: cron polling (A) vs webhook doorbell (B)

**Written:** 2026-08-13, Cody
**For:** Raven + Aym
**Question:** how does Raven find out a Cody batch finished, without Aym relaying?

## The reframe that dissolves the token worry

Aym's concern (polling burns tokens, so use Haiku for the poll) is solving the
wrong layer. **Detecting a new file needs no model at all.** "Is there a file in
docs/handoffs/ newer than the last one I saw" is a filesystem stat - a shell
one-liner cron can run for zero tokens:

```bash
find docs/handoffs -name '*.md' -newer .last-reviewed-marker | head -1
```

Haiku-polling is the worst point in the design space: it pays LLM tokens (and
adds LLM nondeterminism) to do what `find` does for free and deterministically.
If polling is wanted, poll with a script; invoke Raven's normal model ONLY when
the script finds something. Then option A's token cost is ~zero too, and the
A-vs-B choice is purely about latency and reliability.

## Comparison (A = cron+script poll, B = webhook doorbell)

| | A: cron poll (script, not Haiku) | B: webhook POST at batch end |
|---|---|---|
| Token cost | ~0 (script detects; model only on hit) | ~0 (model only on POST) |
| Latency | up to the poll interval (5 min fine) | seconds |
| Catches a crashed Cody session | **YES** - if the handoff file was written, the sweep finds it even though no doorbell rang | NO - a session that dies (or forgets) after writing the file but before POSTing stalls silently |
| Catches a down Hermes | Yes, next poll after it's back | Only if the POST is retried/fallback fires |
| Moving parts | cron entry + marker file | webhook route, HMAC secret, Cody must remember |
| Failure mode visibility | Silent-if-cron-dies (needs a heartbeat) | Silent-if-POST-lost (needs a fallback) |
| Security surface | none new | localhost-only + HMAC (fine as specced) |

## Recommendation: B primary, A as the safety net - and A is a script, never Haiku

Neither alone. The failure modes are complementary:

1. **Webhook (B) as the doorbell.** Instant latency, zero waste, and the
   workspace protocol already specs it correctly (localhost bind, HMAC, fixed
   body, `messages_send` fallback on non-2xx). This is the happy path and will
   handle ~95% of handoffs.
2. **A slow script-based sweep as the backstop** - every 30-60 min, not 5 (the
   webhook already gives fast-path latency; the sweep only exists to catch the
   rare dropped doorbell, so it can be lazy). Zero tokens: cron runs the `find`
   against a marker file, and only if a file turns up does it trigger Raven's
   normal model, exactly as Aym intended - minus Haiku.
3. **Never let the two double-fire:** the sweep must update the same
   `.last-reviewed-marker` the webhook path updates, so a handoff reviewed via
   doorbell isn't re-reviewed by the sweep.

Why not B alone: the one case that most needs automation is a Cody session that
crashed mid-batch - which is precisely the case where the POST never happens.
The handoff file on disk is the durable artifact; the webhook is just a
doorbell. Trust the artifact, not the doorbell.

Why not A alone (even at 5 min): it works, but you'd be accepting 5-minute
latency and a cron dependency to avoid a webhook that's already specced,
localhost-only, and HMAC-signed. The webhook's marginal risk is near zero and
its latency win is real when Aym is actively waiting on a review loop.

Cost summary for Aym: with the script doing detection, BOTH options cost
approximately nothing in tokens. Raven's normal model runs once per actual
handoff in every design - that cost is identical everywhere and is the price of
the review itself, not of the plumbing. The Haiku-poll variant is the only
design on the table that pays tokens for plumbing, and it should be dropped.

## One caution on the Raven->Cody direction

The protocol's reverse path (Raven spawns Cody headlessly on a fixed-template
prompt) is fine, but keep the guardrails as written: fixed templates only,
instruction content in a reviewable file on disk, no
`--dangerously-skip-permissions` on spawned sessions. An automated
file->trigger->act loop between two agents with no human in the fast path is
exactly where a malformed or malicious file in `docs/handoffs/from-raven/`
would get acted on without review - the fixed-template + file-on-disk rule is
what keeps Aym able to audit that path after the fact, so it's load-bearing,
not bureaucracy.
